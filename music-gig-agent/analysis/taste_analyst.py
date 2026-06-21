from collections import Counter
from typing import Any


def build_taste_profile(recent_plays: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise recent listening into a compact taste profile."""
    genre_counts: Counter[str] = Counter()
    mood_counts: Counter[str] = Counter()
    artist_counts: Counter[str] = Counter()
    total_energy = 0.0
    energy_count = 0

    for play in recent_plays:
        artist = str(play.get("artist", "Unknown Artist"))
        artist_counts[artist] += 1

        for genre in play.get("genres", []):
            genre_counts[str(genre).lower()] += 1

        for mood in play.get("moods", []):
            mood_counts[str(mood).lower()] += 1

        energy = play.get("energy")
        if isinstance(energy, (int, float)):
            total_energy += float(energy)
            energy_count += 1

    average_energy = round(total_energy / energy_count, 2) if energy_count else 0.0

    return {
        "top_artists": artist_counts.most_common(8),
        "top_genres": genre_counts.most_common(8),
        "top_moods": mood_counts.most_common(6),
        "average_energy": average_energy,
        "play_count": len(recent_plays),
    }
