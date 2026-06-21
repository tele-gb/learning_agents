from collections import Counter, defaultdict
from typing import Any


def build_live_taste_profile(gig_history: dict[str, Any]) -> dict[str, Any]:
    """Summarise attended gig history into live-show preference signals."""
    gigs = [
        gig
        for gig in gig_history.get("gigs_attended", [])
        if isinstance(gig, dict)
    ]
    listener_profile = gig_history.get("listener_profile", {})
    live_history_summary = gig_history.get("live_history_summary", {})

    rated_gigs = [
        gig for gig in gigs if isinstance(gig.get("rating"), (int, float))
    ]
    average_rating = (
        round(sum(float(gig["rating"]) for gig in rated_gigs) / len(rated_gigs), 1)
        if rated_gigs
        else None
    )

    positive_tags: Counter[str] = Counter()
    negative_tags: Counter[str] = Counter()
    venue_ratings: dict[str, list[float]] = defaultdict(list)
    repeat_artists = []
    avoid_artists = []

    for gig in gigs:
        rating = gig.get("rating")
        venue = gig.get("venue")
        if isinstance(rating, (int, float)) and venue:
            venue_ratings[str(venue)].append(float(rating))

        tags = [str(tag) for tag in gig.get("tags", [])]
        if gig.get("would_go_again") is True or _rating_at_least(gig, 8):
            positive_tags.update(tags)
            repeat_artists.append(str(gig.get("artist", "Unknown Artist")))
        elif gig.get("would_go_again") is False or _rating_at_most(gig, 5):
            negative_tags.update(tags)
            avoid_artists.append(str(gig.get("artist", "Unknown Artist")))

    best_venues = _rank_venues(venue_ratings, reverse=True)
    lowest_venues = _rank_venues(venue_ratings, reverse=False)

    return {
        "gig_count": len(gigs),
        "average_rating": average_rating,
        "would_go_again_count": sum(1 for gig in gigs if gig.get("would_go_again") is True),
        "would_not_go_again_count": sum(
            1 for gig in gigs if gig.get("would_go_again") is False
        ),
        "repeat_artists": repeat_artists[:8],
        "avoid_artists": avoid_artists[:8],
        "positive_tags": positive_tags.most_common(10),
        "negative_tags": negative_tags.most_common(10),
        "best_venues": best_venues[:5],
        "lowest_venues": lowest_venues[:5],
        "listener_profile": listener_profile if isinstance(listener_profile, dict) else {},
        "live_history_summary": (
            live_history_summary if isinstance(live_history_summary, dict) else {}
        ),
    }


def _rating_at_least(gig: dict[str, Any], threshold: float) -> bool:
    rating = gig.get("rating")
    return isinstance(rating, (int, float)) and float(rating) >= threshold


def _rating_at_most(gig: dict[str, Any], threshold: float) -> bool:
    rating = gig.get("rating")
    return isinstance(rating, (int, float)) and float(rating) <= threshold


def _rank_venues(
    venue_ratings: dict[str, list[float]], reverse: bool
) -> list[tuple[str, float, int]]:
    ranked = [
        (venue, round(sum(ratings) / len(ratings), 1), len(ratings))
        for venue, ratings in venue_ratings.items()
        if ratings
    ]
    return sorted(ranked, key=lambda item: item[1], reverse=reverse)
