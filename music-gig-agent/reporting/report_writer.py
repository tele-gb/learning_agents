from pathlib import Path
from typing import Any


def write_monthly_report(
    path: Path,
    taste_profile: dict[str, Any],
    ranked_gigs: list[dict[str, Any]],
    llm_taste_profile: dict[str, Any] | None = None,
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
        f"- Average energy: {taste_profile.get('average_energy', 0)}",
        f"- Top artists: {_format_pairs(taste_profile.get('top_artists', []))}",
        f"- Top genres: {_format_pairs(taste_profile.get('top_genres', []))}",
        f"- Top moods: {_format_pairs(taste_profile.get('top_moods', []))}",
        "",
    ]

    if llm_taste_profile:
        lines.extend(_format_llm_taste_profile(llm_taste_profile))

    lines.extend(["## Ranked Recommendations", ""])

    for index, gig in enumerate(ranked_gigs, start=1):
        analysis = gig.get("analysis", {})
        lines.extend(
            [
                f"### {index}. {gig.get('artist', 'Unknown Artist')} at {gig.get('venue', 'Unknown Venue')}",
                "",
                f"- Date: {gig.get('date', 'TBC')}",
                f"- Match score: {gig.get('match_score', 0)}",
                f"- Style: {analysis.get('style_summary', 'No style summary yet')}",
                f"- Similar artists: {', '.join(analysis.get('similar_artists', []))}",
                f"- Why I might like it: {analysis.get('why_i_might_like_it', 'TBC')}",
                f"- Why I might not: {analysis.get('why_i_might_not', 'TBC')}",
                f"- Confidence: {analysis.get('confidence_score', 0)}",
                f"- Suggested first song: {analysis.get('suggested_first_song', 'TBC')}",
                "",
            ]
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def _format_pairs(pairs: list[tuple[str, int]]) -> str:
    if not pairs:
        return "None yet"
    return ", ".join(f"{name} ({count})" for name, count in pairs)


def _format_llm_taste_profile(llm_taste_profile: dict[str, Any]) -> list[str]:
    lines = [
        "## LLM Taste Profile",
        "",
        str(llm_taste_profile.get("summary", "No summary generated.")),
        "",
        f"- Dominant styles: {_format_list(llm_taste_profile.get('dominant_styles', []))}",
        f"- Mood descriptors: {_format_list(llm_taste_profile.get('mood_descriptors', []))}",
        f"- Listening patterns: {_format_list(llm_taste_profile.get('listening_patterns', []))}",
        f"- Novelty profile: {llm_taste_profile.get('novelty_profile', 'Not assessed')}",
        f"- Likely gig preferences: {_format_list(llm_taste_profile.get('likely_gig_preferences', []))}",
        f"- Possible misfires: {_format_list(llm_taste_profile.get('possible_misfires', []))}",
        f"- LLM confidence: {llm_taste_profile.get('confidence', 'Not assessed')}",
        f"- Evidence: {_format_list(llm_taste_profile.get('evidence', []))}",
        "",
    ]
    return lines


def _format_list(values: list[str]) -> str:
    if not values:
        return "None yet"
    return "; ".join(str(value) for value in values)
