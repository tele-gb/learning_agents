import json
import os
import re
from html import unescape
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin
from urllib.error import HTTPError
from urllib.request import Request, urlopen


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_GIG_SEARCH_MODEL = "gpt-5.2"
DEFAULT_GIG_SEARCH_DAYS = 60
DEFAULT_GIG_SEARCH_MAX_RESULTS = 50
MIN_GIG_CONFIDENCE = 0.65
SOURCE_FETCH_TIMEOUT = 20
MAX_SOURCE_QUERIES = 90
MAX_SOURCE_LINKS_PER_QUERY = 8
MAX_SOURCE_EVENT_PAGES = 160
ALLOWED_GIG_CITIES = {
    "birmingham",
    "wolverhampton",
    "bilston",
    "stourbridge",
    "coventry",
    "warwick",
    "leamington spa",
    "sutton coldfield",
    "solihull",
    "cannock",
    "lichfield",
    "tamworth",
    "nuneaton",
    "worcester",
    "kidderminster",
    "redditch",
    "stratford-upon-avon",
}
SOURCE_SEARCH_TEMPLATES = [
    ("Skiddle", "https://www.skiddle.com/search/?keyword={query}"),
    ("Ticketmaster", "https://www.ticketmaster.co.uk/search?q={query}"),
    ("Ents24", "https://www.ents24.com/search?q={query}"),
    ("Gigantic", "https://www.gigantic.com/search?q={query}"),
    ("See Tickets", "https://www.seetickets.com/search?q={query}"),
    ("Songkick", "https://www.songkick.com/search?query={query}"),
]
DEFAULT_VENUE_TARGETS = [
    # Birmingham: established grassroots and touring venues
    "Hare & Hounds Kings Heath",
    "The Sunflower Lounge Birmingham",
    "Castle & Falcon Birmingham",
    "The Flapper Birmingham",
    "The Victoria Birmingham",
    "Dead Wax Digbeth",
    "Mama Roux's Birmingham",
    "The Crossing Digbeth",
    "The Night Owl Birmingham",
    "The Asylum Birmingham",
    "Actress & Bishop Birmingham",
    "The Dark Horse Moseley",
    "SUKi10C Birmingham",
    "Forum Digbeth",
    "XOYO Birmingham",
    "O2 Institute Birmingham",
    "O2 Academy Birmingham",

    # Birmingham: smaller, specialist and discovery-friendly venues
    "Centrala Birmingham",
    "MAC Birmingham",
    "Kitchen Garden Cafe Kings Heath",
    "Fletchers Bar Kings Heath",
    "Red Lion Folk Club Kings Heath",
    "1000 Trades Birmingham",
    "Pan-Pan Birmingham",
    "Joe Joe Jim's Birmingham",
    "The Ruin Digbeth",
    "The Rainbow Pub Digbeth",
    "Scruffy Murphy's Birmingham",
    "The Glee Club Birmingham",

    # Birmingham: jazz, classical and seated concerts
    "Eastside Jazz Club Birmingham",
    "CBSO Centre Birmingham",
    "Elgar Concert Hall Birmingham",
    "Jennifer Blackwell Performance Space Birmingham",
    "Royal Birmingham Conservatoire Birmingham",
    "Symphony Hall Birmingham",
    "Town Hall Birmingham",

    # Birmingham: larger and club-oriented venues
    "LAB11 Birmingham",
    "Luna Springs Digbeth",
    "Nortons Digbeth",
    "The Jam House Birmingham",
    "Utilita Arena Birmingham",
    "bp pulse LIVE Birmingham",

    # Wolverhampton, Bilston and the Black Country
    "University of Wolverhampton at The Halls",
    "KK's Steel Mill Wolverhampton",
    "The Robin Bilston",
    "Wolverhampton Arts Centre",
    "The Giffard Arms Wolverhampton",
    "Claptrap The Venue Stourbridge",
    "Katie Fitzgerald's Stourbridge",
    "River Rooms Stourbridge",

    # Coventry and Warwickshire
    "hmv Empire Coventry",
    "The Tin Music and Arts Coventry",
    "The Arches Venue Coventry",
    "Kasbah Coventry",
    "Warwick Arts Centre Coventry",
    "Coventry Building Society Arena",
    "The Assembly Leamington Spa",
    "Zephyr Lounge Leamington Spa",
    "Temperance Leamington Spa",
    "Royal Spa Centre Leamington Spa",

    # Sutton Coldfield, Solihull and north/east of Birmingham
    "The Rhodehouse Sutton Coldfield",
    "The Core Theatre Solihull",
    "The Station Cannock",
    "The Hub at St Mary's Lichfield",
    "Tamworth Assembly Rooms",
    "Queens Hall Nuneaton",

    # Worcestershire, Kidderminster, Redditch and Stratford
    "The Marr's Bar Worcester",
    "Huntingdon Hall Worcester",
    "45Live Kidderminster",
    "Kidderminster Town Hall",
    "Palace Theatre Redditch",
    "Rother Street Arts House Stratford-upon-Avon",
]


class GigSearchError(RuntimeError):
    """Raised when OpenAI web gig search cannot be completed."""


def build_gig_search_payload(
    city: str,
    date_from: str,
    date_to: str,
    max_results: int,
    model: str,
    search_mode: str = "broad_discovery",
    priority_artists: list[str] | None = None,
    venue_targets: list[str] | None = None,
) -> dict[str, Any]:
    """Build the exact OpenAI web-search payload without sending it."""
    venue_targets = venue_targets or DEFAULT_VENUE_TARGETS
    priority_artists = priority_artists or []
    return {
        "model": model,
        "tools": [
            {
                "type": "web_search",
                "search_context_size": "medium",
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
                        "search_mode": search_mode,
                        "search_mode_instructions": _search_mode_instructions(search_mode),
                        "city": city,
                        "region_scope": {
                            "name": "West Midlands",
                            "include_nearby_towns": [
                                "Wolverhampton",
                                "Warwick",
                                "Coventry",
                                "Leamington Spa",
                                "Solihull",
                                "Walsall",
                                "Dudley",
                                "Stourbridge",
                                "West Bromwich",
                                "Sutton Coldfield",
                            ],
                            "guidance": (
                                "Do not restrict the search to Birmingham city limits. "
                                "Include credible gigs across the wider West Midlands "
                                "and nearby reachable towns when the listing evidence is strong."
                            ),
                        },
                        "date_from": date_from,
                        "date_to": date_to,
                        "max_results": max_results,
                        "priority_artists": priority_artists,
                        "preferred_context": [
                            "Birmingham and West Midlands UK venues",
                            "nearby Midlands towns",
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
                        "venue_targets": venue_targets,
                        "collection_strategy": [
                            "Prioritise returning many evidenced candidates over perfect taste matching.",
                            "Mix obvious listings with strange, niche, local, and lower-profile shows.",
                            "Search multiple sources rather than relying on one aggregator.",
                            "Search surrounding West Midlands towns as well as Birmingham proper.",
                            "For venue sweeps, inspect each target venue's own listings/calendar before aggregator pages.",
                            "For taste sweeps, search each priority artist by name with Birmingham, West Midlands, and nearby venue names.",
                            "Do not treat one or two listings from a venue as coverage of that venue's full upcoming calendar.",
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
                            "unclear whether event is in Birmingham, the West Midlands, or a nearby requested town",
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
    priority_artists: list[str] | None = None,
    deep_search: bool = False,
) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise GigSearchError("Set OPENAI_API_KEY before using --collect-gigs.")

    search_passes = _search_passes(deep_search, priority_artists or [])
    all_gigs: list[dict[str, Any]] = []
    rejected_gigs: list[dict[str, Any]] = []
    all_notes: list[str] = []
    pass_summaries: list[dict[str, Any]] = []

    for search_pass in search_passes:
        payload = build_gig_search_payload(
            city,
            date_from,
            date_to,
            max_results,
            model,
            search_mode=search_pass["search_mode"],
            priority_artists=search_pass.get("priority_artists", []),
            venue_targets=search_pass.get("venue_targets", DEFAULT_VENUE_TARGETS),
        )
        response_payload = _send_openai_request(payload, api_key)
        results = _extract_structured_output(response_payload)
        pass_rejections: list[dict[str, Any]] = []
        validated_gigs = _validate_gigs(
            results.get("gigs", []), pass_rejections, date_from, date_to
        )
        all_gigs.extend(validated_gigs)
        rejected_gigs.extend(pass_rejections)
        all_notes.extend(
            f"{search_pass['search_mode']}: {note}"
            for note in results.get("search_notes", [])
        )
        pass_summaries.append(
            {
                "search_mode": search_pass["search_mode"],
                "returned_count": len(results.get("gigs", [])),
                "validated_count": len(validated_gigs),
                "rejected_count": len(pass_rejections),
                "priority_artists": search_pass.get("priority_artists", []),
                "venue_targets": search_pass.get("venue_targets", []),
            }
        )

    gigs = _dedupe_gigs(all_gigs)[:max_results]
    return {
        "source": "openai_web_search_deep" if deep_search else "openai_web_search",
        "model": model,
        "city": city,
        "date_from": date_from,
        "date_to": date_to,
        "collected_at": datetime.now().astimezone().isoformat(),
        "gig_count": len(gigs),
        "gigs": gigs,
        "rejected_count": len(rejected_gigs),
        "rejected_gigs": rejected_gigs,
        "search_notes": all_notes,
        "search_passes": pass_summaries,
    }


def collect_gigs_from_sources(
    city: str,
    date_from: str,
    date_to: str,
    max_results: int,
    priority_artists: list[str] | None = None,
    venue_targets: list[str] | None = None,
) -> dict[str, Any]:
    """Collect gigs by querying known listing sites and parsing event pages."""
    venue_targets = venue_targets or DEFAULT_VENUE_TARGETS
    priority_artists = priority_artists or []
    rejected_gigs: list[dict[str, Any]] = []
    source_notes: list[str] = []
    stats = {
        "queries": 0,
        "search_pages_fetched": 0,
        "event_links_found": 0,
        "event_pages_fetched": 0,
        "parse_failures": 0,
    }

    event_links = _discover_source_event_links(
        city,
        venue_targets,
        priority_artists,
        source_notes,
        stats,
    )
    parsed_gigs = []
    for source_name, event_url, query in event_links[:MAX_SOURCE_EVENT_PAGES]:
        page = _fetch_url(event_url)
        if not page:
            stats["parse_failures"] += 1
            continue
        stats["event_pages_fetched"] += 1
        gig = _parse_event_page(
            page,
            event_url,
            source_name,
            query,
            venue_targets,
        )
        if gig:
            parsed_gigs.append(gig)
        else:
            stats["parse_failures"] += 1

    validated_gigs = _validate_gigs(parsed_gigs, rejected_gigs, date_from, date_to)
    gigs = _dedupe_gigs(validated_gigs)[:max_results]
    return {
        "source": "deterministic_source_search",
        "model": None,
        "city": city,
        "date_from": date_from,
        "date_to": date_to,
        "collected_at": datetime.now().astimezone().isoformat(),
        "gig_count": len(gigs),
        "gigs": gigs,
        "rejected_count": len(rejected_gigs),
        "rejected_gigs": rejected_gigs,
        "search_notes": source_notes,
        "source_search_stats": stats,
    }


def merge_fresh_collections(
    collections: list[dict[str, Any]],
    city: str,
    date_from: str,
    date_to: str,
    max_results: int,
) -> dict[str, Any]:
    """Merge fresh same-window collections from deterministic and OpenAI searches."""
    all_gigs = []
    rejected_gigs = []
    search_notes = []
    search_passes = []
    source_stats = []
    source_names = []
    for collection in collections:
        source_names.append(str(collection.get("source", "unknown")))
        all_gigs.extend(collection.get("gigs", []))
        rejected_gigs.extend(collection.get("rejected_gigs", []))
        search_notes.extend(collection.get("search_notes", []))
        search_passes.extend(collection.get("search_passes", []))
        if collection.get("source_search_stats"):
            source_stats.append(collection["source_search_stats"])

    gigs = _dedupe_gigs(all_gigs)[:max_results]
    merged = {
        "source": "fresh_collection_merge",
        "sources": source_names,
        "city": city,
        "date_from": date_from,
        "date_to": date_to,
        "collected_at": datetime.now().astimezone().isoformat(),
        "gig_count": len(gigs),
        "gigs": gigs,
        "rejected_count": len(rejected_gigs),
        "rejected_gigs": rejected_gigs,
        "search_notes": search_notes,
    }
    if search_passes:
        merged["search_passes"] = search_passes
    if source_stats:
        merged["source_search_stats"] = source_stats
    return merged


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


def _search_passes(
    deep_search: bool,
    priority_artists: list[str],
) -> list[dict[str, Any]]:
    if not deep_search:
        return [{"search_mode": "broad_discovery"}]

    return [
        {"search_mode": "broad_discovery"},
        {
            "search_mode": "venue_calendar_sweep",
            "venue_targets": DEFAULT_VENUE_TARGETS,
        },
        {
            "search_mode": "taste_artist_sweep",
            "priority_artists": priority_artists[:24],
        },
    ]


def _search_mode_instructions(search_mode: str) -> list[str]:
    if search_mode == "venue_calendar_sweep":
        return [
            "Treat this as a listings coverage pass, not a discovery sample.",
            "Search venue calendars and venue-owned event pages for every venue target.",
            "For The Sunflower Lounge, explicitly search the official calendar plus Skiddle/Ticketmaster-style event pages.",
            "Return all evidenced music listings in the date window, including low-profile local supports if the listing identifies artist, venue, city and date.",
            "Do not stop after finding one or two events from a venue; continue until the target venue calendar has been checked.",
        ]
    if search_mode == "taste_artist_sweep":
        return [
            "Treat this as a priority-artist safety net.",
            "Search each priority artist by name with Birmingham, West Midlands, Sunflower Lounge, Hare & Hounds, Flapper, Castle & Falcon, Wolverhampton, Coventry, Warwick and Leamington Spa.",
            "If a priority artist has an upcoming evidenced local show in the date window, include it even if it is not obscure or surprising.",
            "Use artist websites, venue pages, Songkick, Bandsintown, Ticketmaster, Skiddle, Gigantic, See Tickets and promoter pages as evidence.",
            "Do not omit obvious matches just because the broad discovery pass already found enough unrelated gigs.",
        ]
    return [
        "Treat this as a broad discovery pass for sourced live music listings.",
        "Prefer diversity and credible evidence over taste matching.",
        "This pass is allowed to find surprises, but it does not replace venue-calendar or priority-artist coverage.",
    ]


def _discover_source_event_links(
    city: str,
    venue_targets: list[str],
    priority_artists: list[str],
    source_notes: list[str],
    stats: dict[str, int],
) -> list[tuple[str, str, str]]:
    links: list[tuple[str, str, str]] = []
    seen = set()
    queries = _source_queries(city, venue_targets, priority_artists)
    for query in queries[:MAX_SOURCE_QUERIES]:
        stats["queries"] += 1
        for source_name, template in SOURCE_SEARCH_TEMPLATES:
            search_url = template.format(query=quote_plus(query))
            page = _fetch_url(search_url)
            if not page:
                continue
            stats["search_pages_fetched"] += 1
            source_links = _extract_event_links(page, search_url, source_name)
            for event_url in source_links[:MAX_SOURCE_LINKS_PER_QUERY]:
                key = _normalize_source_url(event_url)
                if key in seen:
                    continue
                seen.add(key)
                links.append((source_name, event_url, query))
    stats["event_links_found"] = len(links)
    source_notes.append(
        "Deterministic source search queried "
        f"{min(len(queries), MAX_SOURCE_QUERIES)} venue/artist terms across "
        f"{len(SOURCE_SEARCH_TEMPLATES)} listing sources and found {len(links)} event-like links."
    )
    return links


def _source_queries(
    city: str,
    venue_targets: list[str],
    priority_artists: list[str],
) -> list[str]:
    queries: list[str] = []
    for artist in priority_artists[:24]:
        queries.append(f"{artist} {city}")
        queries.append(f"{artist} West Midlands")
        queries.append(f"{artist} Sunflower Lounge")
        queries.append(f"{artist} Birmingham gig")

    priority_venues = [
        venue
        for venue in venue_targets
        if _is_priority_source_venue(venue)
    ]
    for venue in [*priority_venues, *venue_targets]:
        queries.append(f"{venue} gigs")
        queries.append(f"{venue} events")
    return _unique_strings(queries)


def _is_priority_source_venue(venue: str) -> bool:
    venue_text = venue.lower()
    priority_terms = [
        "sunflower",
        "hare",
        "flapper",
        "castle",
        "dead wax",
        "victoria",
        "night owl",
        "tin music",
        "assembly leamington",
    ]
    return any(term in venue_text for term in priority_terms)


def _extract_event_links(page: str, base_url: str, source_name: str) -> list[str]:
    hrefs = re.findall(r"""href=["']([^"']+)["']""", page, flags=re.IGNORECASE)
    links = []
    for href in hrefs:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute_url = urljoin(base_url, unescape(href))
        if _looks_like_event_url(absolute_url, source_name):
            links.append(absolute_url)
    return _unique_strings(links)


def _looks_like_event_url(url: str, source_name: str) -> bool:
    lowered = url.lower()
    if any(skip in lowered for skip in ["/login", "/register", "/account", "/help"]):
        return False
    if source_name == "Skiddle":
        return "/whats-on/" in lowered
    if source_name == "Ticketmaster":
        return "/event/" in lowered or "-tickets/" in lowered
    if source_name == "Ents24":
        return "-events/" in lowered or "/event/" in lowered
    if source_name == "Gigantic":
        return "/tickets/" in lowered or "/venue/" in lowered
    if source_name == "See Tickets":
        return "/event/" in lowered or "/tour/" in lowered
    if source_name == "Songkick":
        return "/concerts/" in lowered
    return any(term in lowered for term in ["/event", "/gig", "/concert", "/tickets"])


def _parse_event_page(
    page: str,
    url: str,
    source_name: str,
    query: str,
    venue_targets: list[str],
) -> dict[str, Any] | None:
    jsonld_events = _extract_jsonld_events(page)
    for event in jsonld_events:
        gig = _gig_from_jsonld_event(event, url, source_name)
        if gig:
            return gig

    title = _meta_content(page, "og:title") or _title_text(page)
    description = _meta_content(page, "og:description") or _meta_content(page, "description")
    text = _clean_text(" ".join([title or "", description or "", query]))
    event_date = _extract_date(text)
    if not title or not event_date:
        return None

    venue = _find_known_venue(text, venue_targets) or _venue_from_title(title)
    city = _city_from_text(text) or _city_from_venue(venue)
    artist = _artist_from_title(title, venue)
    if not artist or not venue or not city:
        return None

    return {
        "artist": artist,
        "venue": venue,
        "date": event_date,
        "city": city,
        "source_url": url,
        "source_name": source_name,
        "genres_hint": [],
        "listing_notes": _truncate_text(description or title, 240),
        "confidence": 0.72,
    }


def _extract_jsonld_events(page: str) -> list[dict[str, Any]]:
    events = []
    pattern = (
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>'
        r"(.*?)"
        r"</script>"
    )
    for raw_json in re.findall(pattern, page, flags=re.IGNORECASE | re.DOTALL):
        text = unescape(raw_json.strip())
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        events.extend(_walk_jsonld_events(data))
    return events


def _walk_jsonld_events(value: Any) -> list[dict[str, Any]]:
    events = []
    if isinstance(value, list):
        for item in value:
            events.extend(_walk_jsonld_events(item))
    elif isinstance(value, dict):
        value_type = value.get("@type")
        if isinstance(value_type, list):
            is_event = any(str(item).lower().endswith("event") for item in value_type)
        else:
            is_event = str(value_type).lower().endswith("event")
        if is_event:
            events.append(value)
        for nested_key in ["@graph", "itemListElement"]:
            if nested_key in value:
                events.extend(_walk_jsonld_events(value[nested_key]))
    return events


def _gig_from_jsonld_event(
    event: dict[str, Any],
    source_url: str,
    source_name: str,
) -> dict[str, Any] | None:
    name = str(event.get("name", "")).strip()
    event_date = _iso_date_from_value(event.get("startDate"))
    location = event.get("location", {})
    venue = ""
    city = ""
    if isinstance(location, dict):
        venue = str(location.get("name", "")).strip()
        address = location.get("address", {})
        if isinstance(address, dict):
            city = str(
                address.get("addressLocality")
                or address.get("addressRegion")
                or ""
            ).strip()
    artist = _performer_name(event.get("performer")) or _artist_from_title(name, venue)
    if not artist or not venue or not event_date:
        return None
    return {
        "artist": artist,
        "venue": venue,
        "date": event_date,
        "city": city or _city_from_venue(venue) or "Birmingham",
        "source_url": str(event.get("url") or source_url),
        "source_name": source_name,
        "genres_hint": [],
        "listing_notes": _truncate_text(str(event.get("description") or name), 240),
        "confidence": 0.88,
    }


def _performer_name(performer: Any) -> str:
    if isinstance(performer, list) and performer:
        return _performer_name(performer[0])
    if isinstance(performer, dict):
        return str(performer.get("name", "")).strip()
    if isinstance(performer, str):
        return performer.strip()
    return ""


def _meta_content(page: str, name: str) -> str:
    escaped = re.escape(name)
    patterns = [
        rf'<meta[^>]+property=["\']{escaped}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+name=["\']{escaped}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{escaped}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, page, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return _clean_text(unescape(match.group(1)))
    return ""


def _title_text(page: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", page, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return _clean_text(unescape(match.group(1)))


def _artist_from_title(title: str, venue: str | None = None) -> str:
    cleaned = _clean_text(title)
    cleaned = re.sub(r"\s*\|\s*.*$", "", cleaned)
    cleaned = re.sub(r"\s+-\s+Tickets.*$", "", cleaned, flags=re.IGNORECASE)
    if venue:
        cleaned = re.sub(rf"\s+at\s+{re.escape(venue)}.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.split(r"\s+at\s+|\s+@| tickets? | live at ", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
    return cleaned.strip(" -:|")


def _venue_from_title(title: str) -> str:
    match = re.search(r"\bat\s+([^|,-]+)", title, flags=re.IGNORECASE)
    if match:
        return _clean_text(match.group(1))
    return ""


def _find_known_venue(text: str, venue_targets: list[str]) -> str:
    lowered = text.lower()
    for venue in venue_targets:
        if venue.lower() in lowered:
            return venue
    return ""


def _city_from_text(text: str) -> str:
    cities = [
        "Birmingham",
        "Wolverhampton",
        "Bilston",
        "Stourbridge",
        "Coventry",
        "Warwick",
        "Leamington Spa",
        "Sutton Coldfield",
        "Solihull",
        "Cannock",
        "Lichfield",
        "Tamworth",
        "Nuneaton",
        "Worcester",
        "Kidderminster",
        "Redditch",
        "Stratford-upon-Avon",
    ]
    lowered = text.lower()
    for city in cities:
        if city.lower() in lowered:
            return city
    return ""


def _city_from_venue(venue: str) -> str:
    return _city_from_text(venue)


def _extract_date(text: str) -> str:
    iso_match = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if iso_match:
        return iso_match.group(0)

    months = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    pattern = (
        r"\b(?:mon|tue|wed|thu|fri|sat|sun)?[a-z]*\s*"
        r"(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\s+(20\d{2})\b"
    )
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return ""
    day = int(match.group(1))
    month = months[match.group(2).lower()]
    year = int(match.group(3))
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _iso_date_from_value(value: Any) -> str:
    if not value:
        return ""
    match = re.match(r"^\d{4}-\d{2}-\d{2}", str(value))
    return match.group(0) if match else _extract_date(str(value))


def _fetch_url(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; music-gig-agent/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=SOURCE_FETCH_TIMEOUT) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text" not in content_type and "json" not in content_type and "html" not in content_type:
                return ""
            return response.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _normalize_source_url(url: str) -> str:
    return url.split("#", 1)[0].rstrip("/")


def _unique_strings(values: list[str]) -> list[str]:
    seen = set()
    unique = []
    for value in values:
        normalized = str(value).strip()
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
    return unique


def _clean_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", text).strip()


def _truncate_text(value: str, limit: int) -> str:
    cleaned = _clean_text(value)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


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

    city = str(gig.get("city", "")).lower().strip()
    venue = str(gig.get("venue", "")).lower().strip()
    if city and city not in ALLOWED_GIG_CITIES and not _text_mentions_allowed_city(venue):
        return f"city outside configured region: {gig.get('city')}"

    return None


def _text_mentions_allowed_city(text: str) -> bool:
    lowered = text.lower()
    return any(city in lowered for city in ALLOWED_GIG_CITIES)


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
