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
SPOTIFY_SCOPES = [
    "user-read-email",
    "user-read-recently-played",
    "user-top-read",
    "user-library-read",
    "user-follow-read",
]
SPOTIFY_AUTH_SCOPE = " ".join(SPOTIFY_SCOPES)
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"
DEFAULT_TOKEN_CACHE_PATH = Path(".spotify_token.json")
DEFAULT_PENDING_AUTH_PATH = Path(".spotify_auth_pending.json")
DEFAULT_TOP_LIMIT = 50
DEFAULT_SAVED_TRACK_LIMIT = 50
DEFAULT_FOLLOWED_ARTIST_LIMIT = 50


@dataclass(frozen=True)
class SpotifyConfig:
    client_id: str
    redirect_uri: str = DEFAULT_REDIRECT_URI
    token_cache_path: Path = DEFAULT_TOKEN_CACHE_PATH
    pending_auth_path: Path = DEFAULT_PENDING_AUTH_PATH
    limit: int = 50
    top_limit: int = DEFAULT_TOP_LIMIT
    saved_track_limit: int = DEFAULT_SAVED_TRACK_LIMIT
    followed_artist_limit: int = DEFAULT_FOLLOWED_ARTIST_LIMIT
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
    return load_spotify_taste_context(config)["recent_plays"]


def load_spotify_taste_context(config: SpotifyConfig) -> dict[str, Any]:
    """Load the broader Spotify signals used for taste analysis."""
    token = get_access_token(config)
    recent_plays = fetch_recent_plays(config, token)
    top_artists = fetch_top_artists(config, token)
    top_tracks = fetch_top_tracks(config, token)
    saved_tracks = fetch_saved_tracks(config, token)
    followed_artists = fetch_followed_artists(config, token)

    return {
        "source": "spotify",
        "recent_plays": recent_plays,
        "top_artists": top_artists,
        "top_tracks": top_tracks,
        "saved_tracks": saved_tracks,
        "followed_artists": followed_artists,
        "limits": {
            "recent_plays": config.limit,
            "top_items": config.top_limit,
            "saved_tracks": config.saved_track_limit,
            "followed_artists": config.followed_artist_limit,
        },
    }


def fetch_recent_plays(config: SpotifyConfig, access_token: str) -> list[dict[str, Any]]:
    payload = _spotify_get(
        "/me/player/recently-played",
        access_token,
        query_params={"limit": str(config.limit)},
    )

    items = payload.get("items", [])
    if not isinstance(items, list):
        raise SpotifyApiError("Spotify response did not include an 'items' list")

    recent_plays = [_normalize_play_history_item(item) for item in items]
    return enrich_plays_with_artist_metadata(recent_plays, access_token)


def fetch_top_artists(config: SpotifyConfig, access_token: str) -> dict[str, list[dict[str, Any]]]:
    """Fetch user's top artists across Spotify's supported time ranges."""
    top_artists: dict[str, list[dict[str, Any]]] = {}
    for time_range in ["short_term", "medium_term", "long_term"]:
        payload = _spotify_get(
            "/me/top/artists",
            access_token,
            query_params={"time_range": time_range, "limit": str(config.top_limit)},
        )
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise SpotifyApiError("Spotify top artists response did not include an 'items' list")
        top_artists[time_range] = [
            _normalize_artist(item, source=f"spotify_top_artist_{time_range}")
            for item in items
            if isinstance(item, dict)
        ]
    return top_artists


def fetch_top_tracks(config: SpotifyConfig, access_token: str) -> dict[str, list[dict[str, Any]]]:
    """Fetch user's top tracks across Spotify's supported time ranges."""
    top_tracks: dict[str, list[dict[str, Any]]] = {}
    artist_ids: list[str] = []
    for time_range in ["short_term", "medium_term", "long_term"]:
        payload = _spotify_get(
            "/me/top/tracks",
            access_token,
            query_params={"time_range": time_range, "limit": str(config.top_limit)},
        )
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise SpotifyApiError("Spotify top tracks response did not include an 'items' list")
        normalized_tracks = [
            _normalize_track(item, source=f"spotify_top_track_{time_range}")
            for item in items
            if isinstance(item, dict)
        ]
        top_tracks[time_range] = normalized_tracks
        artist_ids.extend(_unique_artist_ids(normalized_tracks))

    artists_by_id = fetch_artists_by_id(_dedupe_strings(artist_ids), access_token)
    return {
        time_range: _enrich_tracks_with_artist_metadata(tracks, artists_by_id)
        for time_range, tracks in top_tracks.items()
    }


def fetch_saved_tracks(config: SpotifyConfig, access_token: str) -> list[dict[str, Any]]:
    """Fetch the most recently saved tracks from the user's library."""
    payload = _spotify_get(
        "/me/tracks",
        access_token,
        query_params={"limit": str(config.saved_track_limit)},
    )
    items = payload.get("items", [])
    if not isinstance(items, list):
        raise SpotifyApiError("Spotify saved tracks response did not include an 'items' list")

    saved_tracks = []
    for item in items:
        if not isinstance(item, dict):
            continue
        track = item.get("track", {})
        if isinstance(track, dict):
            normalized = _normalize_track(track, source="spotify_saved_track")
            normalized["saved_at"] = item.get("added_at")
            saved_tracks.append(normalized)

    artist_ids = _unique_artist_ids(saved_tracks)
    artists_by_id = fetch_artists_by_id(artist_ids, access_token)
    return _enrich_tracks_with_artist_metadata(saved_tracks, artists_by_id)


def fetch_followed_artists(config: SpotifyConfig, access_token: str) -> list[dict[str, Any]]:
    """Fetch followed artists from Spotify's cursor-based following endpoint."""
    payload = _spotify_get(
        "/me/following",
        access_token,
        query_params={"type": "artist", "limit": str(config.followed_artist_limit)},
    )
    artists_page = payload.get("artists", {})
    if not isinstance(artists_page, dict):
        raise SpotifyApiError("Spotify followed artists response did not include an 'artists' page")
    items = artists_page.get("items", [])
    if not isinstance(items, list):
        raise SpotifyApiError("Spotify followed artists response did not include an 'items' list")
    return [
        _normalize_artist(item, source="spotify_followed_artist")
        for item in items
        if isinstance(item, dict)
    ]


def enrich_plays_with_artist_metadata(
    recent_plays: list[dict[str, Any]], access_token: str
) -> list[dict[str, Any]]:
    """Attach Spotify artist metadata, including genres, to normalized plays."""
    artist_ids = _unique_artist_ids(recent_plays)
    if not artist_ids:
        return recent_plays

    artists_by_id = fetch_artists_by_id(artist_ids, access_token)
    return _enrich_tracks_with_artist_metadata(recent_plays, artists_by_id)


def _enrich_tracks_with_artist_metadata(
    tracks: list[dict[str, Any]], artists_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    enriched_tracks = []
    for track in tracks:
        enriched_track = dict(track)
        artist_id = track.get("spotify_artist_id")
        artist = artists_by_id.get(str(artist_id), {})
        genres = artist.get("genres", [])
        if isinstance(genres, list):
            enriched_track["genres"] = genres
        enriched_track["spotify_artist_popularity"] = artist.get("popularity")
        enriched_track["spotify_artist_followers"] = (
            artist.get("followers", {}).get("total")
            if isinstance(artist.get("followers"), dict)
            else None
        )
        enriched_track["spotify_artist_url"] = (
            artist.get("external_urls", {}).get("spotify")
            if isinstance(artist.get("external_urls"), dict)
            else None
        )
        enriched_tracks.append(enriched_track)

    return enriched_tracks


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
    if _has_valid_access_token(cached_token) and _has_required_scopes(cached_token):
        return str(cached_token["access_token"])

    if cached_token.get("access_token") and not _has_required_scopes(cached_token):
        print("Spotify token is missing newer taste-analysis scopes; starting reauthorization.")

    if cached_token.get("refresh_token") and _has_required_scopes(cached_token):
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
    top_limit = int(os.environ.get("SPOTIFY_TOP_LIMIT", str(DEFAULT_TOP_LIMIT)))
    saved_track_limit = int(
        os.environ.get("SPOTIFY_SAVED_TRACK_LIMIT", str(DEFAULT_SAVED_TRACK_LIMIT))
    )
    followed_artist_limit = int(
        os.environ.get("SPOTIFY_FOLLOWED_ARTIST_LIMIT", str(DEFAULT_FOLLOWED_ARTIST_LIMIT))
    )
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
        top_limit=min(max(top_limit, 1), 50),
        saved_track_limit=min(max(saved_track_limit, 1), 50),
        followed_artist_limit=min(max(followed_artist_limit, 1), 50),
        open_browser=open_browser,
    )


def build_authorization_url(config: SpotifyConfig, pending_auth: dict[str, Any]) -> str:
    auth_params = {
        "response_type": "code",
        "client_id": config.client_id,
        "scope": SPOTIFY_AUTH_SCOPE,
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
    normalized = _normalize_track(track, source="spotify_recent_play")
    normalized["played_at"] = item.get("played_at")
    return normalized


def _normalize_track(track: dict[str, Any], source: str) -> dict[str, Any]:
    artists = track.get("artists", [])
    primary_artist = artists[0] if artists else {}
    album = track.get("album", {})

    return {
        "artist": primary_artist.get("name", "Unknown Artist"),
        "track": track.get("name", "Unknown Track"),
        "genres": [],
        "moods": [],
        "energy": 0.5,
        "spotify_track_id": track.get("id"),
        "spotify_artist_id": primary_artist.get("id"),
        "spotify_url": track.get("external_urls", {}).get("spotify"),
        "source": source,
        "album": album.get("name") if isinstance(album, dict) else None,
        "track_popularity": track.get("popularity"),
    }


def _normalize_artist(artist: dict[str, Any], source: str) -> dict[str, Any]:
    followers = artist.get("followers", {})
    external_urls = artist.get("external_urls", {})
    return {
        "artist": artist.get("name", "Unknown Artist"),
        "spotify_artist_id": artist.get("id"),
        "genres": artist.get("genres", []) if isinstance(artist.get("genres"), list) else [],
        "spotify_artist_popularity": artist.get("popularity"),
        "spotify_artist_followers": (
            followers.get("total") if isinstance(followers, dict) else None
        ),
        "spotify_artist_url": (
            external_urls.get("spotify") if isinstance(external_urls, dict) else None
        ),
        "source": source,
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


def _dedupe_strings(values: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


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


def _has_required_scopes(token: dict[str, Any]) -> bool:
    granted_scopes = set(str(token.get("scope", "")).split())
    return set(SPOTIFY_SCOPES).issubset(granted_scopes)
