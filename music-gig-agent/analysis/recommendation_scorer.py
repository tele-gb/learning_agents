import re
from collections import Counter
from typing import Any


def score_and_rank_gigs(
    enriched_gigs: list[dict[str, Any]],
    taste_profile: dict[str, Any],
    live_taste_profile: dict[str, Any] | None = None,
    llm_taste_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Score gigs with a transparent multi-signal breakdown."""
    scored_gigs = []
    for gig in enriched_gigs:
        breakdown = score_gig(
            gig,
            taste_profile,
            live_taste_profile or {},
            llm_taste_profile or {},
        )
        scored_gig = dict(gig)
        scored_gig["score_breakdown"] = breakdown
        scored_gig["match_score"] = breakdown["overall"]
        scored_gigs.append(scored_gig)

    return sorted(scored_gigs, key=lambda gig: gig["match_score"], reverse=True)


def score_gig(
    gig: dict[str, Any],
    taste_profile: dict[str, Any],
    live_taste_profile: dict[str, Any],
    llm_taste_profile: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    warnings: list[str] = []

    music_fit = _music_fit(gig, taste_profile, llm_taste_profile, reasons, warnings)
    live_fit = _live_fit(gig, live_taste_profile, reasons, warnings)
    venue_fit = _venue_fit(gig, live_taste_profile, reasons, warnings)
    novelty_fit = _novelty_fit(gig, taste_profile, live_taste_profile, reasons, warnings)
    music_fit, live_fit, venue_fit, novelty_fit = _apply_llm_score_hints(
        gig,
        music_fit,
        live_fit,
        venue_fit,
        novelty_fit,
        reasons,
        warnings,
    )
    evidence_quality = _evidence_quality(gig, reasons, warnings)

    overall = (
        (music_fit * 0.38)
        + (live_fit * 0.24)
        + (venue_fit * 0.14)
        + (novelty_fit * 0.12)
        + (evidence_quality * 0.12)
    )

    return {
        "music_fit": round(music_fit, 2),
        "live_fit": round(live_fit, 2),
        "venue_fit": round(venue_fit, 2),
        "novelty_fit": round(novelty_fit, 2),
        "evidence_quality": round(evidence_quality, 2),
        "overall": round(overall, 2),
        "reasons": reasons[:5],
        "warnings": warnings[:4],
    }


def _music_fit(
    gig: dict[str, Any],
    taste_profile: dict[str, Any],
    llm_taste_profile: dict[str, Any],
    reasons: list[str],
    warnings: list[str],
) -> float:
    score = 4.0
    gig_genres = _normalized_list(gig.get("genres", []))
    top_genres = {genre: count for genre, count in taste_profile.get("top_genres", [])}
    overlapping_genres = sorted(set(gig_genres).intersection(top_genres))

    for genre in overlapping_genres[:4]:
        score += min(1.2, 0.45 + (top_genres.get(genre, 0) * 0.15))

    if overlapping_genres:
        reasons.append(
            "Music fit: overlaps with recent listening genres: "
            + ", ".join(overlapping_genres)
        )

    affinity_score, affinity_hits, negative_hits = _profile_affinity_fit(
        gig, llm_taste_profile
    )
    if affinity_hits:
        score += affinity_score
        reasons.append(
            "Music fit: matches current taste profile: "
            + ", ".join(affinity_hits[:5])
        )

    if negative_hits:
        score -= min(1.5, 0.45 * len(negative_hits))
        warnings.append(
            "Music fit: possible taste-profile misfires: "
            + ", ".join(negative_hits[:4])
        )

    if not overlapping_genres and not affinity_hits:
        warnings.append("Music fit: no direct Spotify or taste-profile style overlap")

    return _clamp(score, 0, 10)


def _live_fit(
    gig: dict[str, Any],
    live_taste_profile: dict[str, Any],
    reasons: list[str],
    warnings: list[str],
) -> float:
    score = 5.0
    gig_text = _gig_text(gig)
    positive_terms = _live_preference_terms(live_taste_profile, "positive_signals")
    negative_terms = _live_preference_terms(live_taste_profile, "negative_signals")
    positive_tag_terms = [tag for tag, _count in live_taste_profile.get("positive_tags", [])]
    negative_tag_terms = [tag for tag, _count in live_taste_profile.get("negative_tags", [])]

    positive_hits = _matching_terms(gig_text, positive_terms + positive_tag_terms)
    negative_hits = _matching_terms(gig_text, negative_terms + negative_tag_terms)

    if positive_hits:
        score += min(2.0, 0.5 * len(positive_hits))
        reasons.append("Live fit: matches live-history signals: " + ", ".join(positive_hits[:4]))
    if negative_hits:
        score -= min(2.0, 0.6 * len(negative_hits))
        warnings.append("Live fit: possible lower-fit signals: " + ", ".join(negative_hits[:4]))

    artist = str(gig.get("artist", "")).lower()
    known_artists = _known_artists(live_taste_profile)
    if artist and artist in known_artists:
        score += 1.0
        reasons.append("Live fit: artist appears in known live/listener profile")

    return _clamp(score, 0, 10)


def _venue_fit(
    gig: dict[str, Any],
    live_taste_profile: dict[str, Any],
    reasons: list[str],
    warnings: list[str],
) -> float:
    score = 5.0
    venue = str(gig.get("venue", "")).lower()
    best_venues = {venue_name.lower(): rating for venue_name, rating, _count in live_taste_profile.get("best_venues", [])}
    low_venues = {venue_name.lower(): rating for venue_name, rating, _count in live_taste_profile.get("lowest_venues", [])}

    best_match = _matching_venue(venue, best_venues)
    low_match = _matching_venue(venue, low_venues)

    if best_match:
        rating = best_venues[best_match]
        score += max(0.5, (rating - 6.0) * 0.6)
        reasons.append(f"Venue fit: {best_match} has worked live before ({rating})")
    if low_match and low_venues[low_match] < 6.5:
        rating = low_venues[low_match]
        score -= max(0.5, (7.0 - rating) * 0.6)
        warnings.append(f"Venue fit: {low_match} has a lower live-history rating ({rating})")

    return _clamp(score, 0, 10)


def _novelty_fit(
    gig: dict[str, Any],
    taste_profile: dict[str, Any],
    live_taste_profile: dict[str, Any],
    reasons: list[str],
    warnings: list[str],
) -> float:
    score = 6.0
    artist = str(gig.get("artist", "")).lower()
    top_artists = {artist_name.lower() for artist_name, _count in taste_profile.get("top_artists", [])}
    repeat_artists = {artist_name.lower() for artist_name in live_taste_profile.get("repeat_artists", [])}
    avoid_artists = {artist_name.lower() for artist_name in live_taste_profile.get("avoid_artists", [])}

    if artist in top_artists or artist in repeat_artists:
        score += 2.0
        reasons.append("Novelty fit: familiar artist or proven repeat candidate")
    elif artist in avoid_artists:
        score -= 3.0
        warnings.append("Novelty fit: artist is in lower-fit live history")
    else:
        score += 0.7
        reasons.append("Novelty fit: new candidate with some discovery value")

    return _clamp(score, 0, 10)


def _evidence_quality(
    gig: dict[str, Any], reasons: list[str], warnings: list[str]
) -> float:
    source_url = gig.get("source_url") or gig.get("evidence", {}).get("source_url")
    source_name = gig.get("source_name") or gig.get("evidence", {}).get("source_name")
    confidence = gig.get("confidence") or gig.get("evidence", {}).get("confidence")

    if not source_url:
        warnings.append("Evidence: mock or manually entered listing has no source URL")
        return 4.5

    score = 6.5
    if source_name:
        score += 0.8
    if isinstance(confidence, (int, float)):
        score += (float(confidence) - 0.65) * 5
    reasons.append(f"Evidence: sourced from {source_name or 'listing URL'}")
    return _clamp(score, 0, 10)


def _apply_llm_score_hints(
    gig: dict[str, Any],
    music_fit: float,
    live_fit: float,
    venue_fit: float,
    novelty_fit: float,
    reasons: list[str],
    warnings: list[str],
) -> tuple[float, float, float, float]:
    hints = gig.get("analysis", {}).get("score_hints", {})
    if not isinstance(hints, dict):
        return music_fit, live_fit, venue_fit, novelty_fit

    music_fit += _bounded_adjustment(hints.get("music_fit_adjustment"))
    live_fit += _bounded_adjustment(hints.get("live_fit_adjustment"))
    venue_fit += _bounded_adjustment(hints.get("venue_fit_adjustment"))
    novelty_fit += _bounded_adjustment(hints.get("novelty_fit_adjustment"))

    hint_reasons = [str(reason) for reason in hints.get("reasons", []) if str(reason).strip()]
    hint_warnings = [str(warning) for warning in hints.get("warnings", []) if str(warning).strip()]
    if hint_reasons:
        reasons.append("LLM gig read: " + "; ".join(hint_reasons[:3]))
    if hint_warnings:
        warnings.append("LLM gig cautions: " + "; ".join(hint_warnings[:3]))

    return (
        _clamp(music_fit, 0, 10),
        _clamp(live_fit, 0, 10),
        _clamp(venue_fit, 0, 10),
        _clamp(novelty_fit, 0, 10),
    )


def _bounded_adjustment(value: Any) -> float:
    if not isinstance(value, (int, float)):
        return 0.0
    return _clamp(float(value), -2.0, 2.0)



AFFINITY_FIELD_WEIGHTS = {
    "dominant_styles": 1.0,
    "likely_gig_preferences": 1.0,
    "strong_opinions": 0.8,
    "listening_patterns": 0.6,
    "evidence": 0.4,
}
NEGATIVE_AFFINITY_FIELD_WEIGHTS = {
    "possible_misfires": 1.0,
}
AFFINITY_SYNONYMS = {
    "singer-songwriter": ["singer songwriter", "songwriter", "songwriting", "writer", "acoustic"],
    "singer songwriter": ["singer-songwriter", "songwriter", "songwriting", "writer", "acoustic"],
    "songwriter": ["singer-songwriter", "singer songwriter", "songwriting", "writer"],
    "songwriting": ["singer-songwriter", "singer songwriter", "songwriter", "writer"],
    "americana": ["alt-country", "alt country", "roots", "folk"],
    "alt-country": ["alt country", "americana", "roots", "folk"],
    "alt country": ["alt-country", "americana", "roots", "folk"],
    "folk": ["alt-folk", "indie folk", "acoustic", "roots", "singer-songwriter"],
    "alt-folk": ["folk", "indie folk", "acoustic", "singer-songwriter"],
    "indie folk": ["folk", "alt-folk", "acoustic", "singer-songwriter"],
    "indie-rock": ["indie rock", "guitar", "guitar music", "band"],
    "indie rock": ["indie-rock", "guitar", "guitar music", "band"],
    "guitar": ["guitar music", "guitar-based", "band", "indie rock"],
    "guitar-based": ["guitar", "guitar music", "band"],
    "roots": ["americana", "folk", "alt-country"],
    "listening-room": ["listening room", "singer-songwriter", "songwriter", "acoustic"],
    "listening room": ["listening-room", "singer-songwriter", "songwriter", "acoustic"],
}
AFFINITY_STOP_TERMS = {
    "music",
    "artist",
    "artists",
    "gig",
    "gigs",
    "show",
    "shows",
    "live",
    "current",
    "recent",
    "strong",
    "good",
    "great",
    "likely",
    "preference",
    "preferences",
}


def _profile_affinity_fit(
    gig: dict[str, Any], llm_taste_profile: dict[str, Any]
) -> tuple[float, list[str], list[str]]:
    positive_terms = _build_dynamic_affinity_terms(
        llm_taste_profile, AFFINITY_FIELD_WEIGHTS, expand_synonyms=True
    )
    negative_terms = _build_dynamic_affinity_terms(
        llm_taste_profile, NEGATIVE_AFFINITY_FIELD_WEIGHTS, expand_synonyms=False
    )
    if not positive_terms and not negative_terms:
        return 0.0, [], []

    gig_text = _gig_text(gig)
    positive_hits = _weighted_term_hits(gig_text, positive_terms)
    negative_hits = _weighted_term_hits(gig_text, negative_terms)
    score = min(2.4, sum(weight for _term, weight in positive_hits[:6]) * 0.35)
    return (
        score,
        _dedupe_affinity_labels([term for term, _weight in positive_hits]),
        _dedupe_affinity_labels([term for term, _weight in negative_hits]),
    )


def _dedupe_affinity_labels(terms: list[str]) -> list[str]:
    seen = set()
    labels = []
    for term in terms:
        key = _normalize_term(term)
        if not key or key in seen:
            continue
        seen.add(key)
        labels.append(term)
    return labels


def _build_dynamic_affinity_terms(
    llm_taste_profile: dict[str, Any],
    field_weights: dict[str, float],
    expand_synonyms: bool,
) -> dict[str, float]:
    terms: Counter[str] = Counter()
    for field, weight in field_weights.items():
        for value in llm_taste_profile.get(field, []):
            for term in _extract_music_terms(str(value)):
                terms[term] += weight
                if expand_synonyms:
                    for synonym in AFFINITY_SYNONYMS.get(term, []):
                        terms[_normalize_term(synonym)] += weight * 0.75

    return {term: float(weight) for term, weight in terms.items()}


def _extract_music_terms(value: str) -> list[str]:
    normalized = _normalize_term(value)
    terms: set[str] = set()
    for known_term in AFFINITY_SYNONYMS:
        if _term_in_text(known_term, normalized):
            terms.add(known_term)

    for match in re.findall(r"[a-z0-9]+(?:[- ][a-z0-9]+){0,2}", normalized):
        term = _normalize_term(match)
        words = term.split()
        if len(term) < 4 or term in AFFINITY_STOP_TERMS:
            continue
        if len(words) == 1 and words[0] in AFFINITY_STOP_TERMS:
            continue
        if len(words) == 1 and len(words[0]) < 5:
            continue
        terms.add(term)

    return sorted(terms)


def _weighted_term_hits(text: str, terms: dict[str, float]) -> list[tuple[str, float]]:
    hits = [
        (term, weight)
        for term, weight in terms.items()
        if _term_in_text(term, text)
    ]
    return sorted(hits, key=lambda item: (-item[1], item[0]))


def _term_in_text(term: str, text: str) -> bool:
    normalized_term = _normalize_term(term)
    normalized_text = _normalize_term(text)
    if not normalized_term:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])", normalized_text) is not None


def _normalize_term(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()

def _live_preference_terms(live_taste_profile: dict[str, Any], key: str) -> list[str]:
    listener_profile = live_taste_profile.get("listener_profile", {})
    live_preferences = listener_profile.get("live_preferences", {})
    if not isinstance(live_preferences, dict):
        return []
    return [str(value).lower() for value in live_preferences.get(key, [])]


def _known_artists(live_taste_profile: dict[str, Any]) -> set[str]:
    listener_profile = live_taste_profile.get("listener_profile", {})
    known_artists = listener_profile.get("known_artists", [])
    automatic_purchases = listener_profile.get("automatic_ticket_purchase", [])
    anchor_artists = listener_profile.get("discovery_anchors", [])

    names = {str(artist).lower() for artist in known_artists}
    names.update(
        str(item.get("artist", "")).lower()
        for item in automatic_purchases + anchor_artists
        if isinstance(item, dict)
    )
    return {name for name in names if name}


def _gig_text(gig: dict[str, Any]) -> str:
    analysis = gig.get("analysis", {}) if isinstance(gig.get("analysis"), dict) else {}
    pieces = [
        gig.get("artist", ""),
        gig.get("venue", ""),
        gig.get("listing_notes", ""),
        analysis.get("style_summary", ""),
        analysis.get("why_i_might_like_it", ""),
        analysis.get("why_i_might_not", ""),
        " ".join(str(value) for value in gig.get("genres", [])),
        " ".join(str(value) for value in gig.get("moods", [])),
        " ".join(str(value) for value in analysis.get("semantic_tags", [])),
    ]
    return " ".join(str(piece).lower() for piece in pieces if piece)


def _matching_terms(text: str, terms: list[str]) -> list[str]:
    matches = []
    for term in terms:
        term = str(term).lower().strip()
        if not term:
            continue
        words = [word for word in term.replace("-", " ").split() if len(word) > 3]
        if term in text or any(word in text for word in words):
            matches.append(term)
    return sorted(set(matches))


def _matching_venue(venue: str, venue_scores: dict[str, float]) -> str | None:
    for venue_name in venue_scores:
        if venue_name in venue or venue in venue_name:
            return venue_name
    return None


def _normalized_list(values: list[Any]) -> list[str]:
    return [str(value).lower().strip() for value in values if str(value).strip()]


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
