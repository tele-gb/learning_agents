import argparse
import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import token_urlsafe
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from analysis.gig_analyst import enrich_gigs
from analysis.live_taste_analyst import build_live_taste_profile
from analysis.llm_gig_analyst import OpenAIGigEnrichmentError, enrich_gigs_with_llm
from analysis.llm_taste_analyst import OpenAITasteProfileError, build_llm_taste_profile
from analysis.recommendation_scorer import score_and_rank_gigs
from analysis.taste_analyst import build_taste_profile
from collectors.gig_collector import load_gigs
from collectors.spotify_collector import (
    SpotifyApiError,
    SpotifyConfig,
    build_authorization_url,
    fetch_followed_artists,
    fetch_recent_plays,
    fetch_saved_tracks,
    fetch_top_artists,
    fetch_top_tracks,
    _create_pending_auth,
    _exchange_code_for_token,
    _refresh_access_token,
    _spotify_get,
)


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "web_data" / "app.sqlite3"
COLLECTED_GIG_DATA_PATH = BASE_DIR / "data" / "collected_gigs.json"
MOCK_GIG_DATA_PATH = BASE_DIR / "data" / "mock_gigs.json"
SESSION_COOKIE = "music_gig_session"
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60


def main() -> None:
    load_local_env(BASE_DIR / ".env")
    args = parse_args()
    init_db(DB_PATH)
    server = ThreadingHTTPServer((args.host, args.port), MusicGigRequestHandler)
    print(f"Music gig web app running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


class MusicGigRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_json({"ok": True})
            return
        if parsed.path == "/auth/spotify/start":
            self.start_spotify_auth()
            return
        if parsed.path == "/auth/spotify/callback":
            self.finish_spotify_auth(parsed.query)
            return
        if parsed.path in {"/", "/dashboard"}:
            self.show_dashboard()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/refresh":
            self.refresh_user()
            return
        if parsed.path == "/logout":
            self.logout()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def start_spotify_auth(self) -> None:
        config = spotify_web_config()
        pending_auth = _create_pending_auth(config)
        session_id = self.get_or_create_session()
        with connect_db() as db:
            db.execute(
                """
                insert or replace into pending_auth
                    (state, session_id, code_verifier, created_at)
                values (?, ?, ?, ?)
                """,
                (
                    pending_auth["state"],
                    session_id,
                    pending_auth["code_verifier"],
                    int(time.time()),
                ),
            )
        auth_url = f"{build_authorization_url(config, pending_auth)}&show_dialog=true"
        self.redirect(auth_url, session_id=session_id)

    def finish_spotify_auth(self, raw_query: str) -> None:
        query = parse_qs(raw_query)
        state = query.get("state", [""])[0]
        code = query.get("code", [""])[0]
        if not state or not code:
            self.render_page("Spotify Error", "<p>Spotify did not return an authorization code.</p>")
            return

        with connect_db() as db:
            pending = db.execute(
                "select * from pending_auth where state = ?",
                (state,),
            ).fetchone()
        if not pending:
            self.render_page("Spotify Error", "<p>Spotify login state was not recognised.</p>")
            return

        config = spotify_web_config()
        try:
            token = _exchange_code_for_token(config, code, pending["code_verifier"])
            spotify_user = _spotify_get("/me", token["access_token"])
        except (SpotifyApiError, Exception) as error:
            self.render_page("Spotify Error", f"<p>{escape(str(error))}</p>")
            return

        user_id = upsert_user(spotify_user, token)
        session_id = pending["session_id"]
        with connect_db() as db:
            db.execute(
                "update sessions set user_id = ?, updated_at = ? where id = ?",
                (user_id, int(time.time()), session_id),
            )
            db.execute("delete from pending_auth where state = ?", (state,))
        self.redirect("/dashboard", session_id=session_id)

    def show_dashboard(self) -> None:
        user = self.current_user()
        if not user:
            self.render_page("Music Gig Agent", landing_html())
            return

        report = latest_report(user["id"])
        job = latest_refresh_job(user["id"])
        self.render_page(
            "Your Gig Dashboard",
            dashboard_html(user, report, job),
        )

    def refresh_user(self) -> None:
        user = self.current_user()
        if not user:
            self.redirect("/")
            return
        existing_job = latest_refresh_job(user["id"])
        if existing_job and existing_job["status"] == "running":
            self.redirect("/dashboard")
            return
        job_id = create_refresh_job(int(user["id"]))
        thread = threading.Thread(
            target=run_refresh_job,
            args=(job_id, int(user["id"])),
            daemon=True,
        )
        thread.start()
        self.redirect("/dashboard")

    def logout(self) -> None:
        session_id = self.session_id()
        if session_id:
            with connect_db() as db:
                db.execute("delete from sessions where id = ?", (session_id,))
        self.redirect("/", clear_session=True)

    def current_user(self) -> sqlite3.Row | None:
        session_id = self.session_id()
        if not session_id:
            return None
        cutoff = int(time.time()) - SESSION_TTL_SECONDS
        with connect_db() as db:
            return db.execute(
                """
                select users.*
                from sessions
                join users on users.id = sessions.user_id
                where sessions.id = ? and sessions.updated_at >= ?
                """,
                (session_id, cutoff),
            ).fetchone()

    def get_or_create_session(self) -> str:
        session_id = self.session_id() or token_urlsafe(32)
        with connect_db() as db:
            db.execute(
                """
                insert into sessions (id, user_id, created_at, updated_at)
                values (?, null, ?, ?)
                on conflict(id) do update set updated_at = excluded.updated_at
                """,
                (session_id, int(time.time()), int(time.time())),
            )
        return session_id

    def session_id(self) -> str:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else ""

    def redirect(
        self,
        location: str,
        session_id: str | None = None,
        clear_session: bool = False,
    ) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        if clear_session:
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
        elif session_id:
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}={session_id}; Path=/; Max-Age={SESSION_TTL_SECONDS}; HttpOnly; SameSite=Lax",
            )
        self.end_headers()

    def render_page(self, title: str, body: str) -> None:
        html = page_shell(title, body).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def refresh_report_for_user(user: sqlite3.Row) -> dict[str, Any]:
    token = valid_access_token(int(user["id"]))
    config = spotify_web_config()
    spotify_context = {
        "source": "spotify",
        "recent_plays": fetch_recent_plays(config, token),
        "top_artists": fetch_top_artists(config, token),
        "top_tracks": fetch_top_tracks(config, token),
        "saved_tracks": fetch_saved_tracks(config, token),
        "followed_artists": fetch_followed_artists(config, token),
    }
    recent_plays = spotify_context["recent_plays"]
    taste_profile = build_taste_profile(recent_plays, spotify_context)
    llm_taste_profile = maybe_build_llm_taste_profile(
        recent_plays,
        taste_profile,
        spotify_context,
    )
    gig_data_path = COLLECTED_GIG_DATA_PATH if COLLECTED_GIG_DATA_PATH.exists() else MOCK_GIG_DATA_PATH
    gigs = load_gigs(gig_data_path)
    enriched_gigs = enrich_gigs(gigs, taste_profile)
    live_taste_profile = build_live_taste_profile(empty_live_history())
    enriched_gigs = maybe_enrich_gigs_with_llm(
        enriched_gigs,
        taste_profile,
        live_taste_profile,
        llm_taste_profile,
    )
    ranked_gigs = score_and_rank_gigs(
        enriched_gigs,
        taste_profile,
        live_taste_profile,
        llm_taste_profile,
    )
    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "taste_profile": taste_profile,
        "llm_taste_profile": llm_taste_profile,
        "recommendations": ranked_gigs[:20],
        "gig_pool_size": len(gigs),
        "gig_pool_path": str(gig_data_path.relative_to(BASE_DIR)),
    }
    save_report(int(user["id"]), report)
    return report


def run_refresh_job(job_id: int, user_id: int) -> None:
    mark_refresh_job(job_id, "running")
    try:
        with connect_db() as db:
            user = db.execute("select * from users where id = ?", (user_id,)).fetchone()
        if not user:
            raise RuntimeError("User no longer exists.")
        refresh_report_for_user(user)
    except Exception as error:
        mark_refresh_job(job_id, "failed", error_message=str(error))
        return
    mark_refresh_job(job_id, "completed")


def create_refresh_job(user_id: int) -> int:
    now = datetime.now().astimezone().isoformat()
    with connect_db() as db:
        cursor = db.execute(
            """
            insert into refresh_jobs (user_id, status, created_at, updated_at)
            values (?, 'queued', ?, ?)
            """,
            (user_id, now, now),
        )
        return int(cursor.lastrowid)


def mark_refresh_job(
    job_id: int,
    status: str,
    error_message: str | None = None,
) -> None:
    now = datetime.now().astimezone().isoformat()
    with connect_db() as db:
        db.execute(
            """
            update refresh_jobs
            set status = ?, updated_at = ?, error_message = ?
            where id = ?
            """,
            (status, now, error_message, job_id),
        )


def latest_refresh_job(user_id: int) -> sqlite3.Row | None:
    with connect_db() as db:
        return db.execute(
            """
            select * from refresh_jobs
            where user_id = ?
            order by id desc
            limit 1
            """,
            (user_id,),
        ).fetchone()


def maybe_build_llm_taste_profile(
    recent_plays: list[dict[str, Any]],
    taste_profile: dict[str, Any],
    spotify_context: dict[str, Any],
) -> dict[str, Any] | None:
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    if os.environ.get("WEBAPP_DISABLE_OPENAI", "").lower() in {"1", "true", "yes"}:
        return None
    try:
        return build_llm_taste_profile(
            recent_plays,
            taste_profile,
            spotify_taste_context=spotify_context,
        )
    except OpenAITasteProfileError:
        return None


def empty_live_history() -> dict[str, Any]:
    return {
        "listener_profile": {},
        "gigs_attended": [],
        "live_history_summary": {},
    }


def maybe_enrich_gigs_with_llm(
    enriched_gigs: list[dict[str, Any]],
    taste_profile: dict[str, Any],
    live_taste_profile: dict[str, Any],
    llm_taste_profile: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not os.environ.get("OPENAI_API_KEY"):
        return enriched_gigs
    if os.environ.get("WEBAPP_DISABLE_OPENAI", "").lower() in {"1", "true", "yes"}:
        return enriched_gigs
    limit = int(os.environ.get("WEBAPP_GIG_ENRICHMENT_LIMIT", "20"))
    model = os.environ.get(
        "OPENAI_GIG_ENRICHMENT_MODEL",
        os.environ.get("OPENAI_MODEL", "gpt-5.2"),
    )
    try:
        return enrich_gigs_with_llm(
            enriched_gigs,
            taste_profile,
            live_taste_profile,
            llm_taste_profile,
            model,
            limit=max(1, limit),
        )
    except OpenAIGigEnrichmentError:
        return enriched_gigs


def valid_access_token(user_id: int) -> str:
    config = spotify_web_config()
    with connect_db() as db:
        token = db.execute(
            "select * from spotify_tokens where user_id = ?",
            (user_id,),
        ).fetchone()
    if not token:
        raise RuntimeError("No Spotify token is stored for this user.")
    if int(token["expires_at"]) > int(time.time()) + 60:
        return str(token["access_token"])

    refreshed = _refresh_access_token(config, str(token["refresh_token"]))
    store_token(user_id, refreshed)
    return str(refreshed["access_token"])


def upsert_user(spotify_user: dict[str, Any], token: dict[str, Any]) -> int:
    spotify_user_id = str(spotify_user.get("id", "")).strip()
    if not spotify_user_id:
        raise RuntimeError("Spotify profile did not include a user id.")
    email = str(spotify_user.get("email") or "").strip()
    display_name = str(spotify_user.get("display_name") or email or spotify_user_id).strip()
    now = int(time.time())
    with connect_db() as db:
        db.execute(
            """
            insert into users (spotify_user_id, email, display_name, created_at, last_login_at)
            values (?, ?, ?, ?, ?)
            on conflict(spotify_user_id) do update set
                email = excluded.email,
                display_name = excluded.display_name,
                last_login_at = excluded.last_login_at
            """,
            (spotify_user_id, email, display_name, now, now),
        )
        user = db.execute(
            "select id from users where spotify_user_id = ?",
            (spotify_user_id,),
        ).fetchone()
    user_id = int(user["id"])
    store_token(user_id, token)
    return user_id


def store_token(user_id: int, token: dict[str, Any]) -> None:
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        with connect_db() as db:
            existing = db.execute(
                "select refresh_token from spotify_tokens where user_id = ?",
                (user_id,),
            ).fetchone()
        refresh_token = existing["refresh_token"] if existing else ""
    with connect_db() as db:
        db.execute(
            """
            insert into spotify_tokens
                (user_id, access_token, refresh_token, expires_at, scope, updated_at)
            values (?, ?, ?, ?, ?, ?)
            on conflict(user_id) do update set
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                expires_at = excluded.expires_at,
                scope = excluded.scope,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                token.get("access_token", ""),
                refresh_token,
                int(token.get("expires_at", 0)),
                token.get("scope", ""),
                int(time.time()),
            ),
        )


def save_report(user_id: int, report: dict[str, Any]) -> None:
    with connect_db() as db:
        db.execute(
            """
            insert into reports
                (user_id, created_at, taste_profile_json, llm_taste_profile_json, recommendations_json)
            values (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                report["created_at"],
                json.dumps(report["taste_profile"]),
                json.dumps(report["llm_taste_profile"]),
                json.dumps(report["recommendations"]),
            ),
        )


def latest_report(user_id: int) -> dict[str, Any] | None:
    with connect_db() as db:
        row = db.execute(
            """
            select * from reports
            where user_id = ?
            order by id desc
            limit 1
            """,
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "created_at": row["created_at"],
        "taste_profile": json.loads(row["taste_profile_json"]),
        "llm_taste_profile": json.loads(row["llm_taste_profile_json"])
        if row["llm_taste_profile_json"] != "null"
        else None,
        "recommendations": json.loads(row["recommendations_json"]),
    }


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect_db() as db:
        db.executescript(
            """
            create table if not exists users (
                id integer primary key autoincrement,
                spotify_user_id text not null unique,
                email text,
                display_name text,
                created_at integer not null,
                last_login_at integer not null
            );
            create table if not exists spotify_tokens (
                user_id integer primary key,
                access_token text not null,
                refresh_token text not null,
                expires_at integer not null,
                scope text,
                updated_at integer not null,
                foreign key(user_id) references users(id)
            );
            create table if not exists sessions (
                id text primary key,
                user_id integer,
                created_at integer not null,
                updated_at integer not null,
                foreign key(user_id) references users(id)
            );
            create table if not exists pending_auth (
                state text primary key,
                session_id text not null,
                code_verifier text not null,
                created_at integer not null
            );
            create table if not exists reports (
                id integer primary key autoincrement,
                user_id integer not null,
                created_at text not null,
                taste_profile_json text not null,
                llm_taste_profile_json text not null,
                recommendations_json text not null,
                foreign key(user_id) references users(id)
            );
            create table if not exists refresh_jobs (
                id integer primary key autoincrement,
                user_id integer not null,
                status text not null,
                created_at text not null,
                updated_at text not null,
                error_message text,
                foreign key(user_id) references users(id)
            );
            """
        )


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def spotify_web_config() -> SpotifyConfig:
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    if not client_id:
        raise RuntimeError("Set SPOTIFY_CLIENT_ID in .env before starting the web app.")
    base_url = os.environ.get("WEBAPP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    redirect_uri = os.environ.get(
        "SPOTIFY_WEB_REDIRECT_URI",
        f"{base_url}/auth/spotify/callback",
    )
    return SpotifyConfig(
        client_id=client_id,
        redirect_uri=redirect_uri,
        token_cache_path=BASE_DIR / "web_data" / "unused_spotify_token.json",
        pending_auth_path=BASE_DIR / "web_data" / "unused_pending_auth.json",
        open_browser=False,
    )


def landing_html() -> str:
    redirect_uri = spotify_web_config().redirect_uri
    return f"""
    <section class="hero">
      <div>
        <p class="eyebrow">Pi-hosted POC</p>
        <h1>Music Gig Agent</h1>
        <p>Connect Spotify, build a taste diagnosis, and see West Midlands gigs ranked for you.</p>
      </div>
      <form action="/auth/spotify/start" method="get">
        <button type="submit">Connect Spotify</button>
      </form>
    </section>
    <section class="panel">
      <h2>Spotify redirect URI</h2>
      <p>Add this exact URI in the Spotify Developer Dashboard before testing:</p>
      <code>{escape(redirect_uri)}</code>
    </section>
    <section class="panel warning">
      <h2>POC storage note</h2>
      <p>This local prototype stores Spotify refresh tokens in SQLite on the Pi. Use it only on a trusted machine/network.</p>
    </section>
    """


def dashboard_html(
    user: sqlite3.Row,
    report: dict[str, Any] | None,
    job: sqlite3.Row | None = None,
) -> str:
    job_html = refresh_job_html(job)
    header = f"""
    <div class="topbar">
      <div>
        <p class="eyebrow">Signed in</p>
        <h1>{escape(user["display_name"] or user["email"] or "Spotify user")}</h1>
      </div>
      <form action="/logout" method="post"><button class="secondary" type="submit">Log out</button></form>
    </div>
    <form action="/refresh" method="post" class="refresh-form">
      <button type="submit">Run Refresh Now</button>
    </form>
    {job_html}
    """
    if not report:
        return header + """
        <section class="panel">
          <h2>No report yet</h2>
          <p>Run a refresh to fetch Spotify data and rank the current gig pool.</p>
        </section>
        """

    taste = report["taste_profile"]
    llm = report.get("llm_taste_profile")
    recommendations = report.get("recommendations", [])
    parts = [
        header,
        '<section class="panel">',
        f'<p class="eyebrow">Last refresh</p><p>{escape(report["created_at"])}</p>',
        '<div class="stats">',
        stat("Recent plays", taste.get("play_count", 0)),
        stat("Top artist", first_pair_name(taste.get("top_artists", []))),
        stat("Top genre", first_pair_name(taste.get("top_genres", []))),
        stat("Gig cards", len(recommendations)),
        "</div>",
        "</section>",
    ]
    if llm:
        parts.extend(
            [
                '<section class="panel">',
                "<h2>Taste Diagnosis</h2>",
                f"<p>{escape(str(llm.get('summary', 'No summary generated.')))}</p>",
                chips(llm.get("dominant_styles", [])[:10]),
                "</section>",
            ]
        )
    else:
        parts.extend(
            [
                '<section class="panel">',
                "<h2>Taste Profile</h2>",
                f"<p>Top artists: {escape(format_pairs(taste.get('top_artists', [])[:8]))}</p>",
                f"<p>Top genres: {escape(format_pairs(taste.get('top_genres', [])[:8]))}</p>",
                "</section>",
            ]
        )
    parts.append('<section class="recommendations"><h2>Gig Recommendations</h2>')
    for index, gig in enumerate(recommendations[:12], start=1):
        analysis = gig.get("analysis", {})
        source_url = gig.get("source_url") or gig.get("evidence", {}).get("source_url")
        source_name = gig.get("source_name") or gig.get("evidence", {}).get("source_name") or "Source"
        parts.append(
            f"""
            <article class="gig-card">
              <div class="rank">{index}<span>{escape(str(gig.get("match_score", 0)))}</span></div>
              <div>
                <h3>{escape(str(gig.get("artist", "Unknown Artist")))}</h3>
                <p class="venue">{escape(str(gig.get("venue", "Unknown Venue")))} · {escape(str(gig.get("city", "Unknown City")))} · {escape(str(gig.get("date", "TBC")))}</p>
                <p class="small">{'LLM enriched' if analysis.get('llm_enriched') else 'Basic scoring'}</p>
                <p>{escape(str(analysis.get("style_summary", "No style summary yet")))}</p>
                <p><strong>Why it fits:</strong> {escape(str(analysis.get("why_i_might_like_it", "TBC")))}</p>
                <p><strong>Watch out:</strong> {escape(str(analysis.get("why_i_might_not", "TBC")))}</p>
                <p class="small"><a href="{escape_attr(str(source_url or '#'))}">{escape(str(source_name))}</a></p>
              </div>
            </article>
            """
        )
    parts.append("</section>")
    return "\n".join(parts)


def refresh_job_html(job: sqlite3.Row | None) -> str:
    if not job:
        return ""
    status = str(job["status"])
    if status in {"queued", "running"}:
        return f"""
        <section class="panel notice">
          <h2>Refresh {escape(status)}</h2>
          <p>Spotify and OpenAI work is running on the Pi. This page will check again shortly.</p>
          <p class="small">Started: {escape(str(job["created_at"]))}</p>
        </section>
        <script>setTimeout(() => window.location.reload(), 8000);</script>
        """
    if status == "failed":
        return f"""
        <section class="panel warning">
          <h2>Refresh failed</h2>
          <p>{escape(str(job["error_message"] or "Unknown error"))}</p>
          <p class="small">Updated: {escape(str(job["updated_at"]))}</p>
        </section>
        """
    return f"""
    <section class="panel notice">
      <h2>Refresh completed</h2>
      <p class="small">Updated: {escape(str(job["updated_at"]))}</p>
    </section>
    """


def page_shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{ --ink:#171614; --muted:#625d55; --line:#ddd7ce; --paper:#fbfaf7; --panel:#fff; --red:#c94634; --green:#2f7d68; --gold:#d99b2b; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:var(--paper); color:var(--ink); font:15px/1.5 Arial,sans-serif; }}
    main {{ max-width:980px; margin:0 auto; padding:24px 16px 48px; }}
    h1 {{ font-size:34px; line-height:1.05; margin:0 0 10px; }}
    h2 {{ margin:0 0 12px; }}
    h3 {{ margin:0; font-size:22px; }}
    button {{ background:var(--green); color:white; border:0; border-radius:8px; padding:11px 15px; font-weight:700; cursor:pointer; }}
    button.secondary {{ background:#514b44; }}
    code {{ display:block; padding:12px; background:#f2ede5; border:1px solid var(--line); border-radius:8px; overflow:auto; }}
    a {{ color:#326f9d; }}
    .hero {{ background:linear-gradient(135deg,#171614,#43382e); color:#fff; border-radius:8px; padding:28px; display:grid; grid-template-columns:1fr auto; gap:20px; align-items:center; }}
    .hero p {{ color:#f2eee7; margin:0; }}
    .eyebrow {{ color:#d99b2b; font-weight:700; text-transform:uppercase; font-size:12px; margin:0 0 6px; }}
    .panel, .gig-card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:18px; margin:16px 0; }}
    .warning {{ border-color:#d99b2b; }}
    .notice {{ border-color:#2f7d68; }}
    .topbar {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; }}
    .refresh-form {{ margin:16px 0; }}
    .stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }}
    .stat {{ border:1px solid var(--line); border-radius:8px; padding:12px; }}
    .stat span {{ display:block; color:var(--muted); font-size:12px; text-transform:uppercase; }}
    .stat strong {{ font-size:20px; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; }}
    .chip {{ background:#f6f2eb; border:1px solid var(--line); border-radius:999px; padding:4px 9px; font-size:12px; }}
    .gig-card {{ display:grid; grid-template-columns:64px 1fr; gap:16px; }}
    .rank {{ color:#fff; background:var(--red); width:44px; height:44px; border-radius:50%; display:grid; place-items:center; font-weight:700; }}
    .rank span {{ color:var(--green); background:transparent; display:block; grid-column:1; font-size:18px; transform:translateY(34px); }}
    .venue,.small {{ color:var(--muted); }}
    @media (max-width:720px) {{ .hero,.topbar,.stats,.gig-card {{ grid-template-columns:1fr; display:grid; }} }}
  </style>
</head>
<body><main>{body}</main></body>
</html>"""


def stat(label: str, value: Any) -> str:
    return f'<div class="stat"><span>{escape(label)}</span><strong>{escape(str(value))}</strong></div>'


def chips(values: list[Any]) -> str:
    rendered = "".join(f'<span class="chip">{escape(str(value))}</span>' for value in values)
    return f'<div class="chips">{rendered}</div>'


def first_pair_name(pairs: list[Any]) -> str:
    if not pairs:
        return "None yet"
    return str(pairs[0][0])


def format_pairs(pairs: list[Any]) -> str:
    if not pairs:
        return "None yet"
    return ", ".join(f"{name} ({count})" for name, count in pairs)


def escape(value: str) -> str:
    import html

    return html.escape(value, quote=False)


def escape_attr(value: str) -> str:
    import html

    return html.escape(value, quote=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local music gig web app.")
    parser.add_argument("--host", default=os.environ.get("WEBAPP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WEBAPP_PORT", "8000")))
    return parser.parse_args()


def load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


if __name__ == "__main__":
    main()
