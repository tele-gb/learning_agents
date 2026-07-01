import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_GIG_SEARCH_MODEL = "gpt-5.2"
DEFAULT_GIG_SEARCH_DAYS = 60
DEFAULT_GIG_SEARCH_MAX_RESULTS = 50
MIN_GIG_CONFIDENCE = 0.65
GIG_SEARCH_SOURCE_TARGETS = [
    "https://www.gig-guide.co.uk/",
    "venue websites",
    "promoter pages",
    "ticketing platforms",
    "artist websites",
    "local listings",
]



class GigSearchError(RuntimeError):
    """Raised when OpenAI web gig search cannot be completed."""


def build_gig_search_payload(
    city: str,
    date_from: str,
    date_to: str,
    max_results: int,
    model: str,
    exhaustive: bool = False,
    focus_query: str | None = None,
) -> dict[str, Any]:
    """Build the exact OpenAI web-search payload without sending it."""
    return {
        "model": model,
        "tools": [
            {
                "type": "web_search",
                "search_context_size": "high" if exhaustive else "low",
                "user_location": {
                    "type": "approximate",
                    "country": "GB",
                    "city": city,
                    "region": "West Midlands",
                },
            }
        ],
        "tool_choice": "required",
        "input": [
            {
                "role": "developer",
                "content": (
                    "You are a factual live music event collector. Your job is to find "
                    "upcoming live music performances and return them as structured "
                    "data. Optimise for coverage, diversity and discovery rather than "
                    "relevance. Search widely across venue websites, promoter pages, "
                    "ticketing platforms, local listings, artist websites, festivals, "
                    "arts venues, grassroots venues and independent music communities. "
                    "Include major touring artists, emerging artists, support acts, "
                    "local artists, niche genres, experimental music, folk, indie, "
                    "jazz, classical, electronic performances, punk, metal, singer-"
                    "songwriters, world music, community and DIY events. The goal is "
                    "to build the largest credible pool of upcoming live music events. "
                    "Do not attempt to decide whether the user would enjoy the event. "
                    "Relevance scoring happens later in the pipeline. Every event must "
                    "have evidence from a real source, include a source URL, include "
                    "artist name, include venue, include city, and include date. Prefer "
                    "event-specific pages where available, but venue listings and "
                    "promoter listings are acceptable if they clearly identify the "
                    "event. Do not invent events. Exclude comedy, theatre, spoken word, "
                    "conferences, workshops, and pure nightclub events with no "
                    "identifiable live artist. If uncertain whether an event qualifies "
                    "as a live music performance, include it and mark uncertainty "
                    "rather than discarding it. Return strict JSON only."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "Find upcoming music gigs for recommendation candidates.",
                        "city": city,
                        "date_from": date_from,
                        "date_to": date_to,
                        "max_results": max_results,
                        "search_mode": "exhaustive" if exhaustive else "broad",
                        "focus_query": focus_query or "general Birmingham music gig discovery",
                        "preferred_context": [
                            "Birmingham UK venues",
                            "Gig Guide UK listings",
                            "gig-guide.co.uk",
                            "high volume discovery",
                            "weird gigs",
                            "niche gigs",
                            "local promoters",
                            "experimental music",
                            "leftfield music",
                            "support-level touring artists",
                            "small rooms",
                            "independent venues",
                            "indie",
                            "folk",
                            "singer-songwriter",
                            "alternative rock",
                            "post-punk",
                            "jazz",
                            "soul",
                            "interesting live reputation",
                        ],
                        "source_targets": GIG_SEARCH_SOURCE_TARGETS,
                        "venue_targets": [
                            "Hare & Hounds Kings Heath",
                            "Castle & Falcon",
                            "The Flapper",
                            "The Victoria Birmingham",
                            "The Sunflower Lounge",
                            "Dead Wax Digbeth",
                            "Mama Roux's",
                            "The Crossing Digbeth",
                            "The Glee Club Birmingham",
                            "O2 Institute Birmingham",
                            "O2 Academy Birmingham",
                            "Symphony Hall",
                            "Town Hall Birmingham",
                            "Centrala",
                            "Nortons Digbeth",
                            "XOYO Birmingham",
                            "Kitchen Garden Cafe",
                            "The Jam House Birmingham",
                            "The Rainbow Venues",
                            "The Night Owl Birmingham",
                            "The Old Crown Digbeth",
                            "The Asylum Birmingham",
                            "Red Lion Folk Club"
                        ],
                        "collection_strategy": (
                            [
                                f"This pass focus is: {focus_query}." if focus_query else "This pass focus is broad Birmingham live music discovery.",
                                "Run an exhaustive source-by-source audit before returning results.",
                                "Search Gig Guide first, then each named venue target, then promoter and ticketing sources.",
                                "For each venue target, look for at least one official venue, promoter, or ticketing listing page covering the requested date range.",
                                "Do not stop after the first useful source; continue until the requested max_results is filled or every source target and venue target has been checked.",
                                "Use search_notes to name venues or sources that were checked but returned no valid sourced gigs.",
                            ]
                            if exhaustive
                            else []
                        ) + [
                            "Use https://www.gig-guide.co.uk/ as a priority discovery source for Birmingham listings, then corroborate with venue, promoter, ticketing, or artist pages where possible.",
                            "Prioritise returning many evidenced candidates over perfect taste matching.",
                            "Mix obvious listings with strange, niche, local, and lower-profile shows.",
                            "Search multiple sources rather than relying on one aggregator.",
                            "Do not stop after finding a few good matches; fill the requested max_results where evidence supports it.",
                            "A broad but sourced list is better than a narrow list of highly personalized gigs.",
                        ],
                        "required_fields": [
                            "artist",
                            "venue",
                            "date",
                            "city",
                            "source_url",
                            "source_name",
                        ],
                        "hard_rejections": [
                            "no source URL",
                            "source URL does not support the event",
                            "missing or vague date",
                            "unclear whether event is in the requested city",
                            "listing appears to be an old event",
                            "generic venue page without a specific artist/date listing",
                        ],
                    },
                    ensure_ascii=True,
                ),
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "birmingham_gig_search_results",
                "strict": True,
                "schema": _gig_search_schema(),
            }
        },
    }


def collect_gigs_with_openai(
    city: str,
    date_from: str,
    date_to: str,
    max_results: int,
    model: str,
    exhaustive: bool = False,
    focus_query: str | None = None,
) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise GigSearchError("Set OPENAI_API_KEY before using --collect-gigs.")

    payload = build_gig_search_payload(
        city,
        date_from,
        date_to,
        max_results,
        model,
        exhaustive=exhaustive,
        focus_query=focus_query,
    )
    response_payload = _send_openai_request(payload, api_key)
    results = _extract_structured_output(response_payload)
    rejected_gigs: list[dict[str, Any]] = []
    validated_gigs = _validate_gigs(
        results.get("gigs", []), rejected_gigs, date_from, date_to
    )
    gigs = _dedupe_gigs(validated_gigs)
    return {
        "source": "openai_web_search",
        "model": model,
        "city": city,
        "date_from": date_from,
        "date_to": date_to,
        "collected_at": datetime.now().astimezone().isoformat(),
        "gig_count": len(gigs),
        "gigs": gigs,
        "rejected_count": len(rejected_gigs),
        "rejected_gigs": rejected_gigs,
        "search_notes": results.get("search_notes", []),
    }


def write_gig_search_payload_preview(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_collected_gigs(path: Path, collected_gigs: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(collected_gigs, indent=2), encoding="utf-8")


def merge_with_existing_gig_pool(
    existing_path: Path,
    fresh_collection: dict[str, Any],
    date_from: str,
    date_to: str,
) -> dict[str, Any]:
    """Merge a fresh search into the rolling gig pool without keeping stale events."""
    existing_collection = _load_existing_collection(existing_path)
    rejected_gigs: list[dict[str, Any]] = []
    existing_gigs = _validate_gigs(
        existing_collection.get("gigs", []), rejected_gigs, date_from, date_to
    )
    fresh_gigs = fresh_collection.get("gigs", [])
    merged_gigs = _dedupe_gigs([*fresh_gigs, *existing_gigs])
    merged_collection = dict(fresh_collection)
    merged_collection["source"] = "openai_web_search_rolling_pool"
    merged_collection["gig_count"] = len(merged_gigs)
    merged_collection["gigs"] = merged_gigs
    merged_collection["pool_updated_at"] = datetime.now().astimezone().isoformat()
    merged_collection["fresh_gig_count"] = len(fresh_gigs)
    merged_collection["existing_gig_count"] = len(existing_collection.get("gigs", []))
    merged_collection["expired_or_invalid_existing_count"] = len(rejected_gigs)
    merged_collection["rejected_existing_gigs"] = rejected_gigs
    merged_collection["search_notes"] = [
        *fresh_collection.get("search_notes", []),
        (
            "Merged this search into the rolling gig pool, deduped by artist/date/venue, "
            f"and pruned existing events outside {date_from} to {date_to}."
        ),
    ]
    return merged_collection


def write_gig_search_snapshot(
    snapshot_dir: Path, collected_gigs: dict[str, Any]
) -> Path:
    collected_at = datetime.now().astimezone()
    filename = f"gig_search_{collected_at.strftime('%Y-%m-%d_%H%M%S')}.json"
    snapshot_path = snapshot_dir / filename
    write_collected_gigs(snapshot_path, collected_gigs)
    return snapshot_path


def default_date_from() -> str:
    return date.today().isoformat()


def default_date_to() -> str:
    return (date.today() + timedelta(days=DEFAULT_GIG_SEARCH_DAYS)).isoformat()


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
        raise GigSearchError(
            f"OpenAI gig search failed: {error.code} {_redact_sensitive_details(details)}"
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

    raise GigSearchError("OpenAI response did not include structured gig search output.")


def _load_existing_collection(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"gigs": []}
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError):
        return {"gigs": []}
    if not isinstance(data, dict) or not isinstance(data.get("gigs", []), list):
        return {"gigs": []}
    return data


def _dedupe_gigs(gigs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    deduped = []
    sorted_gigs = sorted(gigs, key=lambda gig: float(gig.get("confidence", 0)), reverse=True)
    for gig in sorted_gigs:
        if not isinstance(gig, dict):
            continue
        key = (
            _normalize_event_name(str(gig.get("artist", ""))),
            _normalize_venue(str(gig.get("venue", ""))),
            str(gig.get("date", ""))[:10],
        )
        if key in seen or not all(key):
            continue
        seen.add(key)
        normalized = dict(gig)
        normalized.setdefault("genres", normalized.get("genres_hint", []))
        normalized.setdefault("moods", [])
        normalized.setdefault("energy", 0.5)
        normalized.setdefault("intensity", "medium")
        normalized.setdefault("starter_song", "Start with a recent live favourite")
        normalized.setdefault("local_pick", True)
        normalized["evidence"] = {
            "source_name": normalized.get("source_name"),
            "source_url": normalized.get("source_url"),
            "confidence": normalized.get("confidence"),
        }
        deduped.append(normalized)
    return deduped


def _validate_gigs(
    gigs: list[dict[str, Any]],
    rejected_gigs: list[dict[str, Any]],
    date_from: str,
    date_to: str,
) -> list[dict[str, Any]]:
    validated = []
    for gig in gigs:
        if not isinstance(gig, dict):
            continue

        rejection_reason = _rejection_reason(gig, date_from, date_to)
        if rejection_reason:
            rejected_gigs.append(
                {
                    "artist": gig.get("artist", "Unknown Artist"),
                    "venue": gig.get("venue", "Unknown Venue"),
                    "date": gig.get("date", "Unknown Date"),
                    "source_url": gig.get("source_url", ""),
                    "reason": rejection_reason,
                }
            )
            continue
        validated.append(gig)
    return validated


def _rejection_reason(gig: dict[str, Any], date_from: str, date_to: str) -> str | None:
    required_text_fields = ["artist", "venue", "date", "city", "source_url", "source_name"]
    for field in required_text_fields:
        if not str(gig.get(field, "")).strip():
            return f"missing {field}"

    source_url = str(gig.get("source_url", "")).strip()
    if not source_url.startswith(("http://", "https://")):
        return "source_url is not an absolute URL"

    try:
        confidence = float(gig.get("confidence", 0))
    except (TypeError, ValueError):
        return "confidence is not numeric"
    if confidence < MIN_GIG_CONFIDENCE:
        return f"confidence below {MIN_GIG_CONFIDENCE}"

    if not re.match(r"^\d{4}-\d{2}-\d{2}", str(gig.get("date", ""))):
        return "date is not ISO-like YYYY-MM-DD"

    gig_date = _parse_iso_date(str(gig.get("date", ""))[:10])
    range_start = _parse_iso_date(date_from)
    range_end = _parse_iso_date(date_to)
    if gig_date is None:
        return "date could not be parsed"
    if range_start and gig_date < range_start:
        return f"date before requested range {date_from}"
    if range_end and gig_date > range_end:
        return f"date after requested range {date_to}"

    return None


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _normalize_event_name(value: str) -> str:
    normalized = re.sub(r"\([^)]*\)", "", value).lower()
    normalized = normalized.replace("&", "and")
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def _normalize_venue(value: str) -> str:
    normalized = value.lower().replace("&", "and")
    normalized = re.sub(r"^the\s+", "", normalized)
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def _gig_search_schema() -> dict[str, Any]:
    string_array = {"type": "array", "items": {"type": "string"}}
    gig_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "artist",
            "venue",
            "date",
            "city",
            "source_url",
            "source_name",
            "genres_hint",
            "listing_notes",
            "confidence",
        ],
        "properties": {
            "artist": {"type": "string"},
            "venue": {"type": "string"},
            "date": {"type": "string"},
            "city": {"type": "string"},
            "source_url": {"type": "string"},
            "source_name": {"type": "string"},
            "genres_hint": string_array,
            "listing_notes": {"type": "string"},
            "confidence": {"type": "number"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["gigs", "search_notes"],
        "properties": {
            "gigs": {
                "type": "array",
                "items": gig_schema,
            },
            "search_notes": string_array,
        },
    }


def _redact_sensitive_details(details: str) -> str:
    return re.sub(r"sk-[A-Za-z0-9_*\\-]+", "[redacted-api-key]", details)
