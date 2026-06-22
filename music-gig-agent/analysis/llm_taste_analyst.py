import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-5.2"
DEFAULT_MAX_RECENT_PLAYS = 50


class OpenAITasteProfileError(RuntimeError):
    """Raised when the LLM taste profile cannot be created."""


def build_llm_taste_profile(
    recent_plays: list[dict[str, Any]],
    deterministic_profile: dict[str, Any],
    spotify_taste_context: dict[str, Any] | None = None,
    model: str | None = None,
    max_recent_plays: int = DEFAULT_MAX_RECENT_PLAYS,
) -> dict[str, Any]:
    """Create a structured taste profile using the OpenAI Responses API."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise OpenAITasteProfileError("Set OPENAI_API_KEY before using --llm-taste-profile.")

    selected_model = model or os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    request_payload = build_llm_taste_profile_payload(
        recent_plays,
        deterministic_profile,
        spotify_taste_context,
        selected_model,
        max_recent_plays,
    )

    response_payload = _send_openai_request(request_payload, api_key)
    profile = _extract_structured_output(response_payload)
    profile["model"] = selected_model
    profile["source"] = "openai_responses_api"
    profile["max_recent_plays"] = max_recent_plays
    return profile


def build_llm_taste_profile_payload(
    recent_plays: list[dict[str, Any]],
    deterministic_profile: dict[str, Any],
    spotify_taste_context: dict[str, Any] | None,
    model: str,
    max_recent_plays: int = DEFAULT_MAX_RECENT_PLAYS,
) -> dict[str, Any]:
    """Build the exact OpenAI request payload without sending it."""
    prepared_profile = _prepare_deterministic_profile_for_llm(
        deterministic_profile,
        recent_plays,
    )
    return {
        "model": model,
        "input": [
                {
                    "role": "developer",
                    "content": (
                        "You are a sharp, evidence-led music taste analyst for a Birmingham "
                        "gig recommendation pipeline. Build a useful taste profile from "
                        "recent Spotify plays, top artists, top tracks, saved tracks, "
                        "followed artists, artist repetition, and artist genres. Treat "
                        "recent plays as a short-term signal, top artists/tracks as stronger "
                        "affinity signals, saved tracks as deliberate library intent, and "
                        "followed artists as long-term interest. Be judgemental in the sense "
                        "of making clear, practical calls about what the listener probably "
                        "wants from gigs. Do not be bland. Do not invent listening history. "
                        "Do not use placeholder fields as evidence. Spotify data here does "
                        "not provide reliable mood or audio energy, so ignore neutral energy "
                        "values and empty moods. If a signal is weak or missing, name it in "
                        "weak_signals. The goal is not genre classification; the goal is "
                        "predicting which live gigs this listener would genuinely choose to attend. "
                        "Do not use any hidden or assumed taste priors. Do not infer that the "
                        "listener shares the developer's preferences. If Spotify evidence is "
                        "ambiguous, say so rather than filling the gap with a default indie, "
                        "rock, folk, electronic, pop, metal, jazz, classical, mainstream, or "
                        "underground bias. Do not assume the listener wants or avoids chart "
                        "acts, tribute acts, legacy acts, small rooms, large rooms, seated "
                        "shows, club nights, heavy music, or acoustic songwriting unless "
                        "supported by the listening data. Prioritise artist similarity, "
                        "scene overlap, repeat listening, saved/followed intent, and likely "
                        "live appeal over broad genre tags. Ask yourself: would this specific "
                        "listener genuinely leave the house on a Tuesday night to see this "
                        "artist, based only on their provided signals?"
                    ),
                },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "recent_plays": _compact_recent_plays(recent_plays, max_recent_plays),
                        "spotify_taste_context": _compact_spotify_taste_context(
                            spotify_taste_context
                        ),
                        "deterministic_profile": prepared_profile,
                        "analysis_instructions": {
                            "treat_energy_as": "unavailable unless explicitly marked real",
                            "treat_empty_moods_as": "unavailable",
                            "prioritise": [
                                "top artists across short, medium, and long term",
                                "top tracks across short, medium, and long term",
                                "saved tracks",
                                "followed artists",
                                "repeat artists across multiple Spotify signal types",
                                "artist genres",
                                "genre clusters",
                                "artist popularity as mainstream-vs-niche context",
                                "recency within the provided plays",
                            ],
                            "avoid": [
                                "saying average energy implies taste",
                                "generic statements that could apply to anyone",
                                "overstating confidence when many artists lack genres",
                            ],
                        },
                    },
                    ensure_ascii=True,
                ),
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "music_taste_profile",
                "strict": True,
                "schema": _taste_profile_schema(),
            }
        },
    }


def write_llm_taste_profile(path: Path, llm_profile: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(llm_profile, indent=2), encoding="utf-8")


def write_llm_input_preview(path: Path, request_payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(request_payload, indent=2), encoding="utf-8")


def _compact_recent_plays(
    recent_plays: list[dict[str, Any]], max_recent_plays: int
) -> list[dict[str, Any]]:
    compacted = []
    for play in recent_plays[:max_recent_plays]:
        compacted.append(
            {
                "artist": play.get("artist"),
                "track": play.get("track"),
                "genres": play.get("genres", []),
                "played_at": play.get("played_at"),
                "artist_popularity": play.get("spotify_artist_popularity"),
                "artist_followers": play.get("spotify_artist_followers"),
            }
        )
    return compacted


def _compact_spotify_taste_context(
    spotify_taste_context: dict[str, Any] | None,
) -> dict[str, Any]:
    if not spotify_taste_context:
        return {}
    return {
        "top_artists": {
            time_range: [_compact_artist(artist) for artist in artists[:25]]
            for time_range, artists in spotify_taste_context.get("top_artists", {}).items()
        },
        "top_tracks": {
            time_range: [_compact_track(track) for track in tracks[:25]]
            for time_range, tracks in spotify_taste_context.get("top_tracks", {}).items()
        },
        "saved_tracks": [
            _compact_track(track)
            for track in spotify_taste_context.get("saved_tracks", [])[:25]
        ],
        "followed_artists": [
            _compact_artist(artist)
            for artist in spotify_taste_context.get("followed_artists", [])[:50]
        ],
        "limits": spotify_taste_context.get("limits", {}),
    }


def _compact_artist(artist: dict[str, Any]) -> dict[str, Any]:
    return {
        "artist": artist.get("artist"),
        "genres": artist.get("genres", []),
        "artist_popularity": artist.get("spotify_artist_popularity"),
        "artist_followers": artist.get("spotify_artist_followers"),
        "source": artist.get("source"),
    }


def _compact_track(track: dict[str, Any]) -> dict[str, Any]:
    return {
        "artist": track.get("artist"),
        "track": track.get("track"),
        "genres": track.get("genres", []),
        "artist_popularity": track.get("spotify_artist_popularity"),
        "artist_followers": track.get("spotify_artist_followers"),
        "track_popularity": track.get("track_popularity"),
        "source": track.get("source"),
    }


def _prepare_deterministic_profile_for_llm(
    deterministic_profile: dict[str, Any], recent_plays: list[dict[str, Any]]
) -> dict[str, Any]:
    prepared_profile = {
        key: value
        for key, value in deterministic_profile.items()
        if key not in {"average_energy", "top_moods"}
    }
    prepared_profile["unavailable_signals"] = _unavailable_signals(recent_plays)
    prepared_profile["genre_coverage"] = _genre_coverage(recent_plays)
    return prepared_profile


def _unavailable_signals(recent_plays: list[dict[str, Any]]) -> list[str]:
    unavailable = []
    spotify_plays = [
        play for play in recent_plays if str(play.get("source", "")).startswith("spotify")
    ]
    if spotify_plays and all(play.get("energy") == 0.5 for play in spotify_plays):
        unavailable.append("energy: all live Spotify plays use the local neutral placeholder 0.5")
    if spotify_plays and not any(play.get("moods") for play in spotify_plays):
        unavailable.append("moods: Spotify recent-played data does not provide mood labels")
    return unavailable


def _genre_coverage(recent_plays: list[dict[str, Any]]) -> dict[str, int]:
    plays_with_genres = sum(1 for play in recent_plays if play.get("genres"))
    return {
        "plays_with_genres": plays_with_genres,
        "play_count": len(recent_plays),
    }


def _send_openai_request(payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    request = Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        details = error.read().decode("utf-8")
        raise OpenAITasteProfileError(
            f"OpenAI request failed: {error.code} {_redact_sensitive_details(details)}"
        ) from error


def _extract_structured_output(response_payload: dict[str, Any]) -> dict[str, Any]:
    output_text = response_payload.get("output_text")
    if isinstance(output_text, str):
        return json.loads(output_text)

    for output_item in response_payload.get("output", []):
        for content_item in output_item.get("content", []):
            text = content_item.get("text")
            if isinstance(text, str):
                return json.loads(text)

    raise OpenAITasteProfileError("OpenAI response did not include structured text output.")


def _taste_profile_schema() -> dict[str, Any]:
    string_array = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "summary",
            "strong_opinions",
            "dominant_styles",
            "mood_descriptors",
            "listening_patterns",
            "novelty_profile",
            "likely_gig_preferences",
            "possible_misfires",
            "weak_signals",
            "confidence",
            "evidence",
        ],
        "properties": {
            "summary": {"type": "string"},
            "strong_opinions": string_array,
            "dominant_styles": string_array,
            "mood_descriptors": string_array,
            "listening_patterns": string_array,
            "novelty_profile": {"type": "string"},
            "likely_gig_preferences": string_array,
            "possible_misfires": string_array,
            "weak_signals": string_array,
            "confidence": {"type": "number"},
            "evidence": string_array,
        },
    }


def _redact_sensitive_details(details: str) -> str:
    return re.sub(r"sk-[A-Za-z0-9_*\\-]+", "[redacted-api-key]", details)
