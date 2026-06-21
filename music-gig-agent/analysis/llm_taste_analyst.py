import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-5.2"


class OpenAITasteProfileError(RuntimeError):
    """Raised when the LLM taste profile cannot be created."""


def build_llm_taste_profile(
    recent_plays: list[dict[str, Any]],
    deterministic_profile: dict[str, Any],
    model: str | None = None,
) -> dict[str, Any]:
    """Create a structured taste profile using the OpenAI Responses API."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise OpenAITasteProfileError("Set OPENAI_API_KEY before using --llm-taste-profile.")

    selected_model = model or os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    request_payload = {
        "model": selected_model,
        "input": [
            {
                "role": "developer",
                "content": (
                    "You are a music taste analyst for a Birmingham gig recommendation "
                    "pipeline. Build a careful taste profile from the provided recent "
                    "Spotify plays and deterministic counts. Do not invent listening "
                    "history. If a signal is weak or missing, say so."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "recent_plays": _compact_recent_plays(recent_plays),
                        "deterministic_profile": deterministic_profile,
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

    response_payload = _send_openai_request(request_payload, api_key)
    profile = _extract_structured_output(response_payload)
    profile["model"] = selected_model
    profile["source"] = "openai_responses_api"
    return profile


def write_llm_taste_profile(path: Path, llm_profile: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(llm_profile, indent=2), encoding="utf-8")


def _compact_recent_plays(recent_plays: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted = []
    for play in recent_plays[:75]:
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
            f"OpenAI request failed: {error.code} {details}"
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
            "dominant_styles",
            "mood_descriptors",
            "listening_patterns",
            "novelty_profile",
            "likely_gig_preferences",
            "possible_misfires",
            "confidence",
            "evidence",
        ],
        "properties": {
            "summary": {"type": "string"},
            "dominant_styles": string_array,
            "mood_descriptors": string_array,
            "listening_patterns": string_array,
            "novelty_profile": {"type": "string"},
            "likely_gig_preferences": string_array,
            "possible_misfires": string_array,
            "confidence": {"type": "number"},
            "evidence": string_array,
        },
    }
