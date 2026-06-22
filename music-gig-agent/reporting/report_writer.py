import html
from pathlib import Path
from typing import Any


def write_monthly_report(
    path: Path,
    taste_profile: dict[str, Any],
    ranked_gigs: list[dict[str, Any]],
    llm_taste_profile: dict[str, Any] | None = None,
    live_taste_profile: dict[str, Any] | None = None,
) -> None:
    """Write Markdown and HTML reports with ranked recommendations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    report_title = "West Midlands Gig Recommendations"
    lines = [
        f"# {report_title}",
        "",
        "A monthly report based on recent Spotify listening, live history, and sourced gig listings.",
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
    path.with_suffix(".html").write_text(
        _build_html_report(
            report_title,
            taste_profile,
            ranked_gigs,
            llm_taste_profile,
            live_taste_profile,
        ),
        encoding="utf-8",
    )


def _build_html_report(
    report_title: str,
    taste_profile: dict[str, Any],
    ranked_gigs: list[dict[str, Any]],
    llm_taste_profile: dict[str, Any] | None,
    live_taste_profile: dict[str, Any] | None,
) -> str:
    top_gigs = ranked_gigs[:8]
    play_count = taste_profile.get("play_count", 0)
    average_energy = taste_profile.get("average_energy", 0)
    collected_cities = sorted(
        {
            str(gig.get("city", "")).strip()
            for gig in ranked_gigs
            if str(gig.get("city", "")).strip()
        }
    )
    html_parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_escape(report_title)}</title>",
        f"<style>{_report_css()}</style>",
        "</head>",
        "<body>",
        '<main class="page">',
        '<section class="hero">',
        '<div class="hero-copy">',
        '<p class="eyebrow">Fresh gig radar</p>',
        f"<h1>{_escape(report_title)}</h1>",
        "<p>Ranked from your recent listening, live history, and freshly sourced listings across Birmingham and the wider West Midlands.</p>",
        "</div>",
        '<div class="stats-grid">',
        _stat_card("Candidates", len(ranked_gigs)),
        _stat_card("Spotify plays", play_count),
        _stat_card("Top score", top_gigs[0].get("match_score", 0) if top_gigs else 0),
        _stat_card("Avg energy", average_energy),
        "</div>",
        "</section>",
        '<section class="taste-band">',
        _metric_panel("Top Artists", _format_pairs(taste_profile.get("top_artists", [])[:6])),
        _metric_panel("Top Genres", _format_pairs(taste_profile.get("top_genres", [])[:6])),
        _metric_panel("Search Area", _format_list(collected_cities[:8])),
        "</section>",
    ]

    if llm_taste_profile:
        html_parts.extend(
            [
                '<section class="section">',
                "<h2>Taste Read</h2>",
                f'<p class="lede">{_escape(str(llm_taste_profile.get("summary", "No summary generated.")))}</p>',
                '<div class="chip-row">',
                *_chips(llm_taste_profile.get("dominant_styles", [])[:8]),
                "</div>",
                "</section>",
            ]
        )

    if live_taste_profile and live_taste_profile.get("gig_count"):
        html_parts.extend(
            [
                '<section class="section compact">',
                "<h2>Live History Signals</h2>",
                '<div class="signal-grid">',
                _signal("Logged gigs", live_taste_profile.get("gig_count", 0)),
                _signal("Average rating", live_taste_profile.get("average_rating", "Not rated")),
                _signal("Would go again", live_taste_profile.get("would_go_again_count", 0)),
                _signal("Best venues", _format_venues(live_taste_profile.get("best_venues", [])[:3])),
                "</div>",
                "</section>",
            ]
        )

    html_parts.extend(['<section class="section">', "<h2>Top Recommendations</h2>"])
    for index, gig in enumerate(top_gigs, start=1):
        html_parts.append(_gig_card(index, gig))
    html_parts.extend(["</section>", "</main>", "</body>", "</html>"])
    return "\n".join(html_parts)


def _gig_card(index: int, gig: dict[str, Any]) -> str:
    analysis = gig.get("analysis", {})
    breakdown = gig.get("score_breakdown", {})
    evidence = gig.get("evidence", {})
    source_url = gig.get("source_url") or evidence.get("source_url")
    source_name = gig.get("source_name") or evidence.get("source_name") or "Source"
    tags = analysis.get("semantic_tags", []) or gig.get("genres", [])
    score = gig.get("match_score", 0)
    confidence = analysis.get("confidence_score", 0)
    source_link = (
        f'<a href="{_escape_attr(str(source_url))}">{_escape(str(source_name))}</a>'
        if source_url
        else "Mock or manual listing"
    )
    return "\n".join(
        [
            '<article class="gig-card">',
            '<div class="rank-badge">',
            f"<span>{index}</span>",
            f"<strong>{_escape(str(score))}</strong>",
            "</div>",
            '<div class="gig-main">',
            f"<h3>{_escape(str(gig.get('artist', 'Unknown Artist')))}</h3>",
            f'<p class="venue">{_escape(str(gig.get("venue", "Unknown Venue")))} &middot; {_escape(str(gig.get("city", "Unknown City")))} &middot; {_escape(str(gig.get("date", "TBC")))}</p>',
            f'<p class="summary">{_escape(str(analysis.get("style_summary", "No style summary yet")))}</p>',
            '<div class="score-bars">',
            _score_bar("Music", breakdown.get("music_fit", 0)),
            _score_bar("Live", breakdown.get("live_fit", 0)),
            _score_bar("Venue", breakdown.get("venue_fit", 0)),
            _score_bar("Novelty", breakdown.get("novelty_fit", 0)),
            "</div>",
            '<div class="chip-row">',
            *_chips(tags[:6]),
            "</div>",
            '<div class="two-col">',
            f'<p><strong>Why it fits</strong><br>{_escape(str(analysis.get("why_i_might_like_it", "TBC")))}</p>',
            f'<p><strong>Watch out</strong><br>{_escape(str(analysis.get("why_i_might_not", "TBC")))}</p>',
            "</div>",
            f'<p class="small"><strong>Similar artists:</strong> {_escape(", ".join(analysis.get("similar_artists", [])) or "None listed")}</p>',
            f'<p class="small"><strong>First song:</strong> {_escape(str(analysis.get("suggested_first_song", "TBC")))}</p>',
            f'<p class="small"><strong>Evidence:</strong> {source_link} &middot; confidence {_escape(str(confidence))}</p>',
            "</div>",
            "</article>",
        ]
    )


def _stat_card(label: str, value: Any) -> str:
    return f'<div class="stat"><span>{_escape(label)}</span><strong>{_escape(str(value))}</strong></div>'


def _metric_panel(label: str, value: str) -> str:
    return f'<div class="metric"><h2>{_escape(label)}</h2><p>{_escape(value)}</p></div>'


def _signal(label: str, value: Any) -> str:
    return f'<div class="signal"><span>{_escape(label)}</span><strong>{_escape(str(value))}</strong></div>'


def _score_bar(label: str, value: Any) -> str:
    try:
        numeric = max(0, min(10, float(value)))
    except (TypeError, ValueError):
        numeric = 0
    width = int(numeric * 10)
    return (
        '<div class="bar">'
        f'<span>{_escape(label)}</span>'
        '<div><i style="width: '
        f'{width}%'
        '"></i></div>'
        f'<b>{numeric:.1f}</b>'
        '</div>'
    )


def _chips(values: list[Any]) -> list[str]:
    return [f'<span class="chip">{_escape(str(value))}</span>' for value in values if str(value).strip()]


def _escape(value: str) -> str:
    return html.escape(value, quote=False)


def _escape_attr(value: str) -> str:
    return html.escape(value, quote=True)


def _report_css() -> str:
    return """
:root {
  color-scheme: light;
  --ink: #171614;
  --muted: #625d55;
  --line: #ddd7ce;
  --paper: #fbfaf7;
  --panel: #ffffff;
  --red: #c94634;
  --green: #2f7d68;
  --gold: #d99b2b;
  --blue: #326f9d;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--paper); color: var(--ink); font: 15px/1.5 Arial, sans-serif; }
a { color: var(--blue); }
.page { max-width: 980px; margin: 0 auto; padding: 24px 16px 40px; }
.hero { background: linear-gradient(135deg, #171614, #43382e); color: #fff; border-radius: 8px; padding: 28px; display: grid; grid-template-columns: 1.3fr 1fr; gap: 24px; }
.eyebrow { color: #f2c36b; font-weight: 700; text-transform: uppercase; font-size: 12px; margin: 0 0 8px; }
h1 { font-size: 36px; line-height: 1.05; margin: 0 0 12px; }
h2 { font-size: 20px; margin: 0 0 12px; }
h3 { font-size: 24px; line-height: 1.15; margin: 0; }
.hero p { margin: 0; color: #f2eee7; }
.stats-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.stat, .metric, .signal { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; }
.stat { color: var(--ink); }
.stat span, .signal span { color: var(--muted); display: block; font-size: 12px; text-transform: uppercase; }
.stat strong { font-size: 26px; }
.taste-band, .signal-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 16px; }
.metric p, .lede { margin: 0; color: var(--muted); }
.section { margin-top: 28px; }
.compact { margin-top: 18px; }
.gig-card { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin: 14px 0; display: grid; grid-template-columns: 74px 1fr; gap: 16px; }
.rank-badge { border-right: 1px solid var(--line); padding-right: 14px; text-align: center; }
.rank-badge span { background: var(--red); color: #fff; width: 36px; height: 36px; border-radius: 50%; display: inline-grid; place-items: center; font-weight: 700; }
.rank-badge strong { display: block; font-size: 24px; margin-top: 10px; color: var(--green); }
.venue, .small { color: var(--muted); margin: 5px 0; }
.summary { font-size: 16px; margin: 12px 0; }
.score-bars { display: grid; gap: 8px; margin: 14px 0; }
.bar { display: grid; grid-template-columns: 64px 1fr 34px; gap: 8px; align-items: center; font-size: 12px; color: var(--muted); }
.bar div { background: #ece7df; height: 8px; border-radius: 8px; overflow: hidden; }
.bar i { display: block; height: 100%; background: linear-gradient(90deg, var(--green), var(--gold)); }
.chip-row { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0; }
.chip { border: 1px solid var(--line); background: #f6f2eb; border-radius: 999px; padding: 4px 9px; font-size: 12px; color: #403b35; }
.two-col { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
.two-col p { margin: 8px 0; }
@media (max-width: 720px) {
  .hero, .taste-band, .signal-grid, .gig-card, .two-col { grid-template-columns: 1fr; }
  h1 { font-size: 30px; }
  .rank-badge { border-right: 0; border-bottom: 1px solid var(--line); padding: 0 0 12px; text-align: left; }
  .rank-badge span, .rank-badge strong { display: inline-grid; margin-right: 10px; vertical-align: middle; }
}
"""


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
