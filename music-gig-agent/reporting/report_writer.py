from pathlib import Path
from typing import Any


def write_monthly_report(
    path: Path,
    taste_profile: dict[str, Any],
    ranked_gigs: list[dict[str, Any]],
    llm_taste_profile: dict[str, Any] | None = None,
    live_taste_profile: dict[str, Any] | None = None,
) -> None:
    """Write a Markdown report with ranked recommendations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Birmingham Gig Recommendations",
        "",
        "A mock monthly report based on recent Spotify listening.",
        "",
        "## Taste Profile",
        "",
        f"- Recent plays analysed: {taste_profile.get('play_count', 0)}",
        f"- Spotify taste context: {_format_spotify_context_counts(taste_profile.get('spotify_context_counts', {}))}",
        f"- Average energy: {taste_profile.get('average_energy', 0)}",
        f"- Top artists: {_format_pairs(taste_profile.get('top_artists', []))}",
        f"- Top tracks: {_format_pairs(taste_profile.get('top_tracks', []))}",
        f"- Top genres: {_format_pairs(taste_profile.get('top_genres', []))}",
        f"- Top moods: {_format_pairs(taste_profile.get('top_moods', []))}",
        "",
    ]

    if llm_taste_profile:
        lines.extend(_format_llm_taste_profile(llm_taste_profile))

    if live_taste_profile and live_taste_profile.get("gig_count"):
        lines.extend(_format_live_taste_profile(live_taste_profile))

    lines.extend(["## Ranked Recommendations", ""])

    for index, gig in enumerate(ranked_gigs, start=1):
        analysis = gig.get("analysis", {})
        lines.extend(
            [
                f"### {index}. {gig.get('artist', 'Unknown Artist')} at {gig.get('venue', 'Unknown Venue')}",
                "",
                f"- Date: {gig.get('date', 'TBC')}",
                f"- Match score: {gig.get('match_score', 0)}",
                f"- Score breakdown: {_format_score_breakdown(gig.get('score_breakdown', {}))}",
                f"- Style: {analysis.get('style_summary', 'No style summary yet')}",
                f"- Similar artists: {', '.join(analysis.get('similar_artists', []))}",
                f"- Why I might like it: {analysis.get('why_i_might_like_it', 'TBC')}",
                f"- Why I might not: {analysis.get('why_i_might_not', 'TBC')}",
                f"- Scoring reasons: {_format_list(gig.get('score_breakdown', {}).get('reasons', []))}",
                f"- Scoring warnings: {_format_list(gig.get('score_breakdown', {}).get('warnings', []))}",
                f"- Confidence: {analysis.get('confidence_score', 0)}",
                f"- Suggested first song: {analysis.get('suggested_first_song', 'TBC')}",
                f"- Evidence: {_format_gig_evidence(gig)}",
                "",
            ]
        )
        if analysis.get("semantic_tags"):
            lines.insert(-3, f"- Semantic tags: {_format_list(analysis.get('semantic_tags', []))}")

    path.write_text("\n".join(lines), encoding="utf-8")


def _format_pairs(pairs: list[tuple[str, int]]) -> str:
    if not pairs:
        return "None yet"
    return ", ".join(f"{name} ({count})" for name, count in pairs)


def _format_spotify_context_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "recent plays only"
    labels = {
        "recent_plays": "recent plays",
        "top_artists_short_term": "short-term top artists",
        "top_artists_medium_term": "medium-term top artists",
        "top_artists_long_term": "long-term top artists",
        "top_tracks_short_term": "short-term top tracks",
        "top_tracks_medium_term": "medium-term top tracks",
        "top_tracks_long_term": "long-term top tracks",
        "saved_tracks": "saved tracks",
        "followed_artists": "followed artists",
    }
    return ", ".join(
        f"{value} {labels.get(key, key.replace('_', ' '))}"
        for key, value in counts.items()
        if value
    ) or "recent plays only"


def _format_llm_taste_profile(llm_taste_profile: dict[str, Any]) -> list[str]:
    lines = [
        "## LLM Taste Profile",
        "",
        str(llm_taste_profile.get("summary", "No summary generated.")),
        "",
        f"- Strong opinions: {_format_list(llm_taste_profile.get('strong_opinions', []))}",
        f"- Dominant styles: {_format_list(llm_taste_profile.get('dominant_styles', []))}",
        f"- Mood descriptors: {_format_list(llm_taste_profile.get('mood_descriptors', []))}",
        f"- Listening patterns: {_format_list(llm_taste_profile.get('listening_patterns', []))}",
        f"- Novelty profile: {llm_taste_profile.get('novelty_profile', 'Not assessed')}",
        f"- Likely gig preferences: {_format_list(llm_taste_profile.get('likely_gig_preferences', []))}",
        f"- Possible misfires: {_format_list(llm_taste_profile.get('possible_misfires', []))}",
        f"- Weak signals: {_format_list(llm_taste_profile.get('weak_signals', []))}",
        f"- LLM confidence: {llm_taste_profile.get('confidence', 'Not assessed')}",
        f"- Evidence: {_format_list(llm_taste_profile.get('evidence', []))}",
        "",
    ]
    return lines


def _format_live_taste_profile(live_taste_profile: dict[str, Any]) -> list[str]:
    listener_profile = live_taste_profile.get("listener_profile", {})
    live_preferences = listener_profile.get("live_preferences", {})
    recommendation_bias = listener_profile.get("recommendation_bias", {})
    summary = live_taste_profile.get("live_history_summary", {})

    lines = [
        "## Live Taste Profile",
        "",
        f"- Gigs logged: {live_taste_profile.get('gig_count', 0)}",
        f"- Average rating: {live_taste_profile.get('average_rating', 'Not rated')}",
        f"- Would go again: {live_taste_profile.get('would_go_again_count', 0)}",
        f"- Would not go again: {live_taste_profile.get('would_not_go_again_count', 0)}",
        f"- Repeat artists: {_format_list(live_taste_profile.get('repeat_artists', []))}",
        f"- Lower-fit artists: {_format_list(live_taste_profile.get('avoid_artists', []))}",
        f"- Positive live tags: {_format_pairs(live_taste_profile.get('positive_tags', []))}",
        f"- Negative live tags: {_format_pairs(live_taste_profile.get('negative_tags', []))}",
        f"- Best venues by rating: {_format_venues(live_taste_profile.get('best_venues', []))}",
        f"- Lower-rated venues: {_format_venues(live_taste_profile.get('lowest_venues', []))}",
    ]

    if isinstance(live_preferences, dict):
        lines.extend(
            [
                f"- Positive live signals: {_format_list(live_preferences.get('positive_signals', []))}",
                f"- Negative live signals: {_format_list(live_preferences.get('negative_signals', []))}",
            ]
        )

    if isinstance(recommendation_bias, dict) and recommendation_bias:
        active_biases = [
            key.replace("_", " ")
            for key, enabled in recommendation_bias.items()
            if enabled is True
        ]
        lines.append(f"- Recommendation bias: {_format_list(active_biases)}")

    if isinstance(summary, dict) and summary:
        lines.append(f"- Best ever gig: {summary.get('best_ever_gig', 'Not listed')}")

    lines.append("")
    return lines


def _format_list(values: list[str]) -> str:
    if not values:
        return "None yet"
    return "; ".join(str(value) for value in values)


def _format_venues(venues: list[tuple[str, float, int]]) -> str:
    if not venues:
        return "None yet"
    return "; ".join(
        f"{venue} ({rating}, {count} logged)"
        for venue, rating, count in venues
    )


def _format_gig_evidence(gig: dict[str, Any]) -> str:
    evidence = gig.get("evidence", {})
    source_url = gig.get("source_url") or evidence.get("source_url")
    source_name = gig.get("source_name") or evidence.get("source_name")
    confidence = gig.get("confidence") or evidence.get("confidence")
    if not source_url:
        return "Mock or manually entered listing"
    label = source_name or "Source"
    if confidence is None:
        return f"{label}: {source_url}"
    return f"{label}: {source_url} (listing confidence {confidence})"


def _format_score_breakdown(score_breakdown: dict[str, Any]) -> str:
    if not score_breakdown:
        return "Not available"
    keys = [
        ("music", "music_fit"),
        ("live", "live_fit"),
        ("venue", "venue_fit"),
        ("novelty", "novelty_fit"),
        ("evidence", "evidence_quality"),
    ]
    return ", ".join(
        f"{label} {score_breakdown.get(key, 0)}"
        for label, key in keys
    )
