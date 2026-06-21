import json
import os
import time
import webbrowser
from dataclasses import dataclass
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from secrets import token_urlsafe
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE_URL = "https://api.spotify.com/v1"
SPOTIFY_RECENTLY_PLAYED_SCOPE = "user-read-recently-played"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"
DEFAULT_TOKEN_CACHE_PATH = Path(".spotify_token.json")
DEFAULT_PENDING_AUTH_PATH = Path(".spotify_auth_pending.json")


@dataclass(frozen=True)
class SpotifyConfig:
    client_id: str
    redirect_uri: str = DEFAULT_REDIRECT_URI
    token_cache_path: Path = DEFAULT_TOKEN_CACHE_PATH
    pending_auth_path: Path = DEFAULT_PENDING_AUTH_PATH
    limit: int = 50
    open_browser: bool = True


class SpotifyAuthError(RuntimeError):
    """Raised when Spotify OAuth cannot be completed."""


class SpotifyApiError(RuntimeError):
    """Raised when a Spotify Web API request fails."""


def load_recent_plays(path: Path) -> list[dict[str, Any]]:
    """Load mock Spotify recent plays from local JSON."""
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    plays = data.get("recent_plays", [])
    if not isinstance(plays, list):
        raise ValueError("Expected 'recent_plays' to be a list")
    return plays


def load_recent_plays_from_spotify(config: SpotifyConfig) -> list[dict[str, Any]]:
    """Load recent plays from Spotify and normalize them for the pipeline."""
    token = get_access_token(config)
    payload = _spotify_get(
        "/me/player/recently-played",
        token,
        query_params={"limit": str(config.limit)},
    )

    items = payload.get("items", [])
    if not isinstance(items, list):
        raise SpotifyApiError("Spotify response did not include an 'items' list")

    recent_plays = [_normalize_play_history_item(item) for item in items]
    return enrich_plays_with_artist_metadata(recent_plays, token)


def enrich_plays_with_artist_metadata(
    recent_plays: list[dict[str, Any]], access_token: str
) -> list[dict[str, Any]]:
    """Attach Spotify artist metadata, including genres, to normalized plays."""
    artist_ids = _unique_artist_ids(recent_plays)
    if not artist_ids:
        return recent_plays

    artists_by_id = fetch_artists_by_id(artist_ids, access_token)
    enriched_plays = []
    for play in recent_plays:
        enriched_play = dict(play)
        artist_id = play.get("spotify_artist_id")
        artist = artists_by_id.get(str(artist_id), {})
        genres = artist.get("genres", [])
        if isinstance(genres, list):
            enriched_play["genres"] = genres
        enriched_play["spotify_artist_popularity"] = artist.get("popularity")
        enriched_play["spotify_artist_followers"] = (
            artist.get("followers", {}).get("total")
            if isinstance(artist.get("followers"), dict)
            else None
        )
        enriched_play["spotify_artist_url"] = (
            artist.get("external_urls", {}).get("spotify")
            if isinstance(artist.get("external_urls"), dict)
            else None
        )
        enriched_plays.append(enriched_play)

    return enriched_plays


def fetch_artists_by_id(
    artist_ids: list[str], access_token: str
) -> dict[str, dict[str, Any]]:
    """Fetch Spotify artist metadata in batches of 50 IDs."""
    artists_by_id: dict[str, dict[str, Any]] = {}
    for batch in _chunks(artist_ids, 50):
        payload = _spotify_get("/artists", access_token, query_params={"ids": ",".join(batch)})
        artists = payload.get("artists", [])
        if not isinstance(artists, list):
            raise SpotifyApiError("Spotify artists response did not include an 'artists' list")
        for artist in artists:
            if isinstance(artist, dict) and artist.get("id"):
                artists_by_id[str(artist["id"])] = artist
    return artists_by_id


def get_access_token(config: SpotifyConfig) -> str:
    """Return a valid access token, refreshing or running local auth as needed."""
    cached_token = _load_token_cache(config.token_cache_path)
    if _has_valid_access_token(cached_token):
        return str(cached_token["access_token"])

    if cached_token.get("refresh_token"):
        refreshed_token = _refresh_access_token(config, str(cached_token["refresh_token"]))
        _save_token_cache(config.token_cache_path, refreshed_token)
        return str(refreshed_token["access_token"])

    new_token = run_spotify_auth(config)
    _save_token_cache(config.token_cache_path, new_token)
    return str(new_token["access_token"])


def run_spotify_auth(config: SpotifyConfig) -> dict[str, Any]:
    """Complete Spotify Authorization Code with PKCE using a local callback server."""
    pending_auth = _create_pending_auth(config)
    verifier = str(pending_auth["code_verifier"])
    state = str(pending_auth["state"])
    redirect = urlparse(config.redirect_uri)

    if redirect.hostname not in {"127.0.0.1", "localhost"}:
        raise SpotifyAuthError("Only localhost redirect URIs are supported by this helper.")
    if redirect.port is None:
        raise SpotifyAuthError("Redirect URI must include a port, such as :8765.")

    auth_url = build_authorization_url(config, pending_auth)

    if config.open_browser:
        print("Opening Spotify authorization page...")
        print(auth_url)
        webbrowser.open(auth_url)
    else:
        print("Open this Spotify authorization URL in your browser:")
        print(auth_url)

    code = _wait_for_callback(redirect.hostname, redirect.port, redirect.path or "/", state)
    return _exchange_code_for_token(config, code, verifier)


def print_manual_authorization_url(config: SpotifyConfig) -> str:
    """Create a PKCE authorization URL and save verifier data for a later code exchange."""
    pending_auth = _create_pending_auth(config)
    _save_token_cache(config.pending_auth_path, pending_auth)
    return build_authorization_url(config, pending_auth)


def save_token_from_authorization_code(config: SpotifyConfig, code: str) -> None:
    """Exchange a manually copied authorization code and cache the resulting token."""
    pending_auth = _load_token_cache(config.pending_auth_path)
    verifier = pending_auth.get("code_verifier")
    if not verifier:
        raise SpotifyAuthError("No pending Spotify auth found. Run --spotify-auth-url first.")

    token = _exchange_code_for_token(config, code, str(verifier))
    _save_token_cache(config.token_cache_path, token)
    config.pending_auth_path.unlink(missing_ok=True)


def build_config_from_env(base_dir: Path) -> SpotifyConfig:
    """Build Spotify connector config from environment variables."""
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    if not client_id:
        raise SpotifyAuthError("Set SPOTIFY_CLIENT_ID before using --spotify.")

    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI)
    token_cache = Path(os.environ.get("SPOTIFY_TOKEN_CACHE", base_dir / ".spotify_token.json"))
    pending_auth = Path(
        os.environ.get("SPOTIFY_PENDING_AUTH", base_dir / ".spotify_auth_pending.json")
    )
    limit = int(os.environ.get("SPOTIFY_RECENT_LIMIT", "50"))
    open_browser = os.environ.get("SPOTIFY_OPEN_BROWSER", "true").lower() not in {
        "0",
        "false",
        "no",
    }

    return SpotifyConfig(
        client_id=client_id,
        redirect_uri=redirect_uri,
        token_cache_path=token_cache,
        pending_auth_path=pending_auth,
        limit=min(max(limit, 1), 50),
        open_browser=open_browser,
    )


def build_authorization_url(config: SpotifyConfig, pending_auth: dict[str, Any]) -> str:
    auth_params = {
        "response_type": "code",
        "client_id": config.client_id,
        "scope": SPOTIFY_RECENTLY_PLAYED_SCOPE,
        "redirect_uri": config.redirect_uri,
        "state": pending_auth["state"],
        "code_challenge_method": "S256",
        "code_challenge": pending_auth["code_challenge"],
    }
    return f"{SPOTIFY_AUTH_URL}?{urlencode(auth_params)}"


def _create_pending_auth(config: SpotifyConfig) -> dict[str, Any]:
    verifier = token_urlsafe(64)
    return {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "state": token_urlsafe(24),
        "code_verifier": verifier,
        "code_challenge": _pkce_challenge(verifier),
        "created_at": int(time.time()),
    }


def _spotify_get(
    endpoint: str, access_token: str, query_params: dict[str, str] | None = None
) -> dict[str, Any]:
    url = f"{SPOTIFY_API_BASE_URL}{endpoint}"
    if query_params:
        url = f"{url}?{urlencode(query_params)}"

    request = Request(url, headers={"Authorization": f"Bearer {access_token}"})
    return _send_json_request(request)


def _exchange_code_for_token(
    config: SpotifyConfig, code: str, verifier: str
) -> dict[str, Any]:
    body = urlencode(
        {
            "client_id": config.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.redirect_uri,
            "code_verifier": verifier,
        }
    ).encode("utf-8")
    request = Request(
        SPOTIFY_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    return _token_response_with_expiry(_send_json_request(request))


def _refresh_access_token(config: SpotifyConfig, refresh_token: str) -> dict[str, Any]:
    body = urlencode(
        {
            "client_id": config.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    ).encode("utf-8")
    request = Request(
        SPOTIFY_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    token = _token_response_with_expiry(_send_json_request(request))
    token.setdefault("refresh_token", refresh_token)
    return token


def _send_json_request(request: Request) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        details = error.read().decode("utf-8")
        raise SpotifyApiError(f"Spotify request failed: {error.code} {details}") from error


def _normalize_play_history_item(item: dict[str, Any]) -> dict[str, Any]:
    track = item.get("track", {})
    artists = track.get("artists", [])
    primary_artist = artists[0] if artists else {}

    return {
        "artist": primary_artist.get("name", "Unknown Artist"),
        "track": track.get("name", "Unknown Track"),
        "genres": [],
        "moods": [],
        "energy": 0.5,
        "played_at": item.get("played_at"),
        "spotify_track_id": track.get("id"),
        "spotify_artist_id": primary_artist.get("id"),
        "spotify_url": track.get("external_urls", {}).get("spotify"),
        "source": "spotify",
    }


def _unique_artist_ids(recent_plays: list[dict[str, Any]]) -> list[str]:
    seen = set()
    artist_ids = []
    for play in recent_plays:
        artist_id = play.get("spotify_artist_id")
        if not artist_id or artist_id in seen:
            continue
        seen.add(artist_id)
        artist_ids.append(str(artist_id))
    return artist_ids


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _wait_for_callback(host: str, port: int, path: str, expected_state: str) -> str:
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed_url = urlparse(self.path)
            query = parse_qs(parsed_url.query)

            if parsed_url.path != path:
                self.send_response(404)
                self.end_headers()
                return

            returned_state = query.get("state", [""])[0]
            if returned_state != expected_state:
                result["error"] = "State mismatch during Spotify authorization."
            elif "error" in query:
                result["error"] = query["error"][0]
            else:
                code = query.get("code", [None])[0]
                if code:
                    result["code"] = code

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Spotify authorization complete. You can close this tab.")

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = HTTPServer((host, port), CallbackHandler)
    print(f"Waiting for Spotify callback on http://{host}:{port}{path} ...")
    server.handle_request()
    server.server_close()

    if result.get("error"):
        raise SpotifyAuthError(result["error"])
    if not result.get("code"):
        raise SpotifyAuthError("Spotify authorization did not return a code.")
    return result["code"]


def _pkce_challenge(verifier: str) -> str:
    import base64

    digest = sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def _token_response_with_expiry(token: dict[str, Any]) -> dict[str, Any]:
    expires_in = int(token.get("expires_in", 3600))
    token["expires_at"] = int(time.time()) + expires_in - 60
    return token


def _load_token_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _save_token_cache(path: Path, token: dict[str, Any]) -> None:
    path.write_text(json.dumps(token, indent=2), encoding="utf-8")


def _has_valid_access_token(token: dict[str, Any]) -> bool:
    return bool(token.get("access_token")) and int(token.get("expires_at", 0)) > int(time.time())
