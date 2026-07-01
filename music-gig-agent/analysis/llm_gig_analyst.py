import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_GIG_ENRICHMENT_LIMIT = 20


class OpenAIGigEnrichmentError(RuntimeError):
    """Raised when LLM gig enrichment cannot be completed."""


def enrich_gigs_with_llm(
    gigs: list[dict[str, Any]],
    taste_profile: dict[str, Any],
    live_taste_profile: dict[str, Any],
    llm_taste_profile: dict[str, Any] | None,
    model: str,
    limit: int = DEFAULT_GIG_ENRICHMENT_LIMIT,
) -> list[dict[str, Any]]:
    """Use OpenAI to add personalized gig analysis and scoring hints."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise OpenAIGigEnrichmentError("Set OPENAI_API_KEY before using --llm-gig-enrichment.")

    payload = build_llm_gig_enrichment_payload(
        gigs,
        taste_profile,
        live_taste_profile,
        llm_taste_profile,
        model,
        limit,
    )
    response_payload = _send_openai_request(payload, api_key)
    enrichment = _extract_structured_output(response_payload)
    return merge_gig_enrichment(gigs, enrichment)


def build_llm_gig_enrichment_payload(
    gigs: list[dict[str, Any]],
    taste_profile: dict[str, Any],
    live_taste_profile: dict[str, Any],
    llm_taste_profile: dict[str, Any] | None,
    model: str,
    limit: int = DEFAULT_GIG_ENRICHMENT_LIMIT,
) -> dict[str, Any]:
    """Build the exact OpenAI request payload without sending it."""
    return {
        "model": model,
        "input": [
            {
                "role": "developer",
                "content": (
                    "You are a gig recommendation analyst. You receive factual gig "
                    "listings that already have source evidence. Do not invent or "
                    "alter listing facts such as artist, venue, date, or source URL. "
                    "Your job is to add useful personal interpretation: what the act "
                    "probably sounds like, why this listener might care, why it might "
                    "miss, a first song to try, and small score hints. Be direct and "
                    "judgemental. Prefer live-history signals and artist fit over "
                    "generic genre matching. If evidence is thin, say so in warnings."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "candidate_gigs": _compact_gigs(gigs, limit),
                        "spotify_taste_profile": _compact_taste_profile(taste_profile),
                        "live_taste_profile": _compact_live_taste_profile(live_taste_profile),
                        "llm_taste_profile": llm_taste_profile or {},
                        "instructions": {
                            "do_not_change_listing_facts": True,
                            "use_source_url_only_as_evidence": True,
                            "score_adjustments_range": "between -2.0 and 2.0",
                            "avoid": [
                                "made-up biographical facts",
                                "claiming a personal fit without evidence",
                                "using the placeholder Spotify energy value",
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
                "name": "gig_enrichment_results",
                "strict": True,
                "schema": _gig_enrichment_schema(),
            }
        },
    }


def merge_gig_enrichment(
    gigs: list[dict[str, Any]], enrichment: dict[str, Any]
) -> list[dict[str, Any]]:
    enrichment_by_key = {
        _gig_key(item): item
        for item in enrichment.get("gigs", [])
        if isinstance(item, dict)
    }
    merged_gigs = []
    for gig in gigs:
        merged = dict(gig)
        item = enrichment_by_key.get(_gig_key(gig))
        if item:
            analysis = dict(merged.get("analysis", {}))
            analysis.update(
                {
                    "style_summary": item["style_summary"],
                    "similar_artists": item["similar_artists"],
                    "why_i_might_like_it": item["why_i_might_like_it"],
                    "why_i_might_not": item["why_i_might_not"],
                    "confidence_score": item["confidence_score"],
                    "suggested_first_song": item["suggested_first_song"],
                    "semantic_tags": item["semantic_tags"],
                    "score_hints": item["score_hints"],
                    "llm_enriched": True,
                }
            )
            merged["analysis"] = analysis
        merged_gigs.append(merged)
    return merged_gigs


def write_gig_enrichment_preview(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_gig_enrichment_output(path: Path, gigs: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"gigs": gigs}, indent=2), encoding="utf-8")


def _compact_gigs(gigs: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    compacted = []
    for gig in gigs[:limit]:
        analysis = gig.get("analysis", {})
        compacted.append(
            {
                "artist": gig.get("artist"),
                "venue": gig.get("venue"),
                "date": gig.get("date"),
                "city": gig.get("city"),
                "genres": gig.get("genres", []),
                "listing_notes": gig.get("listing_notes"),
                "source_name": gig.get("source_name"),
                "source_url": gig.get("source_url"),
                "current_style_summary": analysis.get("style_summary"),
                "current_reasons": {
                    "why_i_might_like_it": analysis.get("why_i_might_like_it"),
                    "why_i_might_not": analysis.get("why_i_might_not"),
                },
            }
        )
    return compacted


def _compact_taste_profile(taste_profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "top_artists": taste_profile.get("top_artists", []),
        "top_genres": taste_profile.get("top_genres", []),
        "play_count": taste_profile.get("play_count"),
    }


def _compact_live_taste_profile(live_taste_profile: dict[str, Any]) -> dict[str, Any]:
    listener_profile = live_taste_profile.get("listener_profile", {})
    return {
        "positive_tags": live_taste_profile.get("positive_tags", []),
        "negative_tags": live_taste_profile.get("negative_tags", []),
        "repeat_artists": live_taste_profile.get("repeat_artists", []),
        "avoid_artists": live_taste_profile.get("avoid_artists", []),
        "best_venues": live_taste_profile.get("best_venues", []),
        "lowest_venues": live_taste_profile.get("lowest_venues", []),
        "listener_profile": listener_profile,
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
        with urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        details = error.read().decode("utf-8")
        raise OpenAIGigEnrichmentError(
            f"OpenAI gig enrichment failed: {error.code} {_redact_sensitive_details(details)}"
        ) from error
    except OSError as error:
        raise OpenAIGigEnrichmentError(f"OpenAI gig enrichment failed: {error}") from error


def _extract_structured_output(response_payload: dict[str, Any]) -> dict[str, Any]:
    output_text = response_payload.get("output_text")
    if isinstance(output_text, str):
        return json.loads(output_text)

    for output_item in response_payload.get("output", []):
        for content_item in output_item.get("content", []):
            text = content_item.get("text")
            if isinstance(text, str):
                return json.loads(text)

    raise OpenAIGigEnrichmentError("OpenAI response did not include gig enrichment output.")


def _gig_enrichment_schema() -> dict[str, Any]:
    string_array = {"type": "array", "items": {"type": "string"}}
    score_hints = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "music_fit_adjustment",
            "live_fit_adjustment",
            "venue_fit_adjustment",
            "novelty_fit_adjustment",
            "reasons",
            "warnings",
        ],
        "properties": {
            "music_fit_adjustment": {"type": "number"},
            "live_fit_adjustment": {"type": "number"},
            "venue_fit_adjustment": {"type": "number"},
            "novelty_fit_adjustment": {"type": "number"},
            "reasons": string_array,
            "warnings": string_array,
        },
    }
    gig_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "artist",
            "venue",
            "date",
            "style_summary",
            "similar_artists",
            "why_i_might_like_it",
            "why_i_might_not",
            "confidence_score",
            "suggested_first_song",
            "semantic_tags",
            "score_hints",
        ],
        "properties": {
            "artist": {"type": "string"},
            "venue": {"type": "string"},
            "date": {"type": "string"},
            "style_summary": {"type": "string"},
            "similar_artists": string_array,
            "why_i_might_like_it": {"type": "string"},
            "why_i_might_not": {"type": "string"},
            "confidence_score": {"type": "number"},
            "suggested_first_song": {"type": "string"},
            "semantic_tags": string_array,
            "score_hints": score_hints,
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["gigs"],
        "properties": {
            "gigs": {
                "type": "array",
                "items": gig_schema,
            }
        },
    }


def _gig_key(gig: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(gig.get("artist", "")).strip().lower(),
        str(gig.get("venue", "")).strip().lower(),
        str(gig.get("date", "")).strip(),
    )


def _redact_sensitive_details(details: str) -> str:
    return re.sub(r"sk-[A-Za-z0-9_*\\-]+", "[redacted-api-key]", details)
