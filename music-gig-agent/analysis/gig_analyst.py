from typing import Any


def enrich_gigs(
    gigs: list[dict[str, Any]], taste_profile: dict[str, Any]
) -> list[dict[str, Any]]:
    """Add deterministic placeholder analysis to each gig."""
    # TODO: Replace this enrichment with OpenAI API analysis of artists, reviews, and listings.
    top_artists = [artist for artist, _count in taste_profile.get("top_artists", [])]
    top_genres = [genre for genre, _count in taste_profile.get("top_genres", [])]

    enriched_gigs = []
    for gig in gigs:
        gig_genres = [str(genre).lower() for genre in gig.get("genres", [])]
        overlapping_genres = sorted(set(gig_genres).intersection(top_genres))
        similar_artists = _choose_similar_artists(top_artists, gig_genres)

        enriched = dict(gig)
        enriched["analysis"] = {
            "style_summary": _style_summary(gig),
            "similar_artists": similar_artists,
            "why_i_might_like_it": _like_reason(gig, overlapping_genres),
            "why_i_might_not": _not_like_reason(gig),
            "confidence_score": _placeholder_confidence(gig, overlapping_genres),
            "suggested_first_song": gig.get("starter_song", "Start with the latest single"),
        }
        enriched_gigs.append(enriched)

    return enriched_gigs


def _choose_similar_artists(top_artists: list[str], gig_genres: list[str]) -> list[str]:
    if not top_artists:
        return ["A familiar artist from your recent listening"]

    if "jazz" in gig_genres or "soul" in gig_genres:
        return top_artists[:2]
    if "indie" in gig_genres or "post-punk" in gig_genres:
        return top_artists[1:4] or top_artists[:2]
    if "electronic" in gig_genres or "dance" in gig_genres:
        return top_artists[-3:] or top_artists[:2]
    return top_artists[:3]


def _style_summary(gig: dict[str, Any]) -> str:
    genres = ", ".join(gig.get("genres", []))
    moods = ", ".join(gig.get("moods", []))
    if genres and moods:
        return f"{genres} with a {moods} feel"
    if genres:
        return genres
    if moods:
        return f"{moods} feel"
    return f"{genres} with a {moods} feel"


def _like_reason(gig: dict[str, Any], overlapping_genres: list[str]) -> str:
    if overlapping_genres:
        return "It overlaps with your recent interest in " + ", ".join(overlapping_genres) + "."
    return "It adds some variety while staying close to your broader listening mood."


def _not_like_reason(gig: dict[str, Any]) -> str:
    intensity = gig.get("intensity", "medium")
    if intensity == "high":
        return "It may be a louder, more full-on night than your calmer recent plays."
    if intensity == "low":
        return "It may feel a little restrained if you want a high-energy show."
    return "It may not stand out if you are looking for something more extreme."


def _placeholder_confidence(gig: dict[str, Any], overlapping_genres: list[str]) -> float:
    base_score = 0.55 + (0.1 * len(overlapping_genres))
    if gig.get("local_pick"):
        base_score += 0.05
    return round(min(base_score, 0.92), 2)
