from typing import Any


def rank_gigs(
    enriched_gigs: list[dict[str, Any]], taste_profile: dict[str, Any]
) -> list[dict[str, Any]]:
    """Score and rank gigs against the taste profile."""
    top_genres = dict(taste_profile.get("top_genres", []))
    top_moods = dict(taste_profile.get("top_moods", []))
    average_energy = float(taste_profile.get("average_energy", 0.5))

    ranked = []
    for gig in enriched_gigs:
        score = _score_gig(gig, top_genres, top_moods, average_energy)
        ranked_gig = dict(gig)
        ranked_gig["match_score"] = score
        ranked.append(ranked_gig)

    return sorted(ranked, key=lambda gig: gig["match_score"], reverse=True)


def _score_gig(
    gig: dict[str, Any],
    top_genres: dict[str, int],
    top_moods: dict[str, int],
    average_energy: float,
) -> float:
    genre_score = sum(top_genres.get(str(genre).lower(), 0) for genre in gig.get("genres", []))
    mood_score = sum(top_moods.get(str(mood).lower(), 0) for mood in gig.get("moods", []))

    gig_energy = float(gig.get("energy", 0.5))
    energy_fit = max(0.0, 1.0 - abs(gig_energy - average_energy))

    confidence = float(gig.get("analysis", {}).get("confidence_score", 0.5))
    weighted_score = (genre_score * 1.8) + (mood_score * 1.2) + (energy_fit * 4) + (confidence * 3)
    return round(weighted_score, 2)
