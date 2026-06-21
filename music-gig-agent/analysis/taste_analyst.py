from collections import Counter
from typing import Any


def build_taste_profile(
    recent_plays: list[dict[str, Any]],
    spotify_taste_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarise recent listening into a compact taste profile."""
    genre_counts: Counter[str] = Counter()
    mood_counts: Counter[str] = Counter()
    artist_counts: Counter[str] = Counter()
    track_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    total_energy = 0.0
    energy_count = 0

    for play in recent_plays:
        _add_track_signal(play, artist_counts, track_counts, genre_counts, source_counts, weight=1)

        for mood in play.get("moods", []):
            mood_counts[str(mood).lower()] += 1

        energy = play.get("energy")
        if isinstance(energy, (int, float)):
            total_energy += float(energy)
            energy_count += 1

    if spotify_taste_context:
        _add_spotify_taste_context(
            spotify_taste_context,
            artist_counts,
            track_counts,
            genre_counts,
            source_counts,
        )

    average_energy = round(total_energy / energy_count, 2) if energy_count else 0.0

    return {
        "top_artists": artist_counts.most_common(8),
        "top_tracks": track_counts.most_common(8),
        "top_genres": genre_counts.most_common(8),
        "top_moods": mood_counts.most_common(6),
        "average_energy": average_energy,
        "play_count": len(recent_plays),
        "spotify_context_counts": _spotify_context_counts(spotify_taste_context),
        "spotify_signal_sources": source_counts.most_common(),
    }


def _add_spotify_taste_context(
    spotify_taste_context: dict[str, Any],
    artist_counts: Counter[str],
    track_counts: Counter[str],
    genre_counts: Counter[str],
    source_counts: Counter[str],
) -> None:
    for time_range, artists in spotify_taste_context.get("top_artists", {}).items():
        weight = _time_range_weight(str(time_range))
        for artist in artists:
            _add_artist_signal(artist, artist_counts, genre_counts, source_counts, weight)

    for time_range, tracks in spotify_taste_context.get("top_tracks", {}).items():
        weight = _time_range_weight(str(time_range))
        for track in tracks:
            _add_track_signal(track, artist_counts, track_counts, genre_counts, source_counts, weight)

    for track in spotify_taste_context.get("saved_tracks", []):
        _add_track_signal(track, artist_counts, track_counts, genre_counts, source_counts, weight=2)

    for artist in spotify_taste_context.get("followed_artists", []):
        _add_artist_signal(artist, artist_counts, genre_counts, source_counts, weight=2)


def _add_track_signal(
    track: dict[str, Any],
    artist_counts: Counter[str],
    track_counts: Counter[str],
    genre_counts: Counter[str],
    source_counts: Counter[str],
    weight: int,
) -> None:
    artist = str(track.get("artist", "Unknown Artist"))
    title = str(track.get("track", "Unknown Track"))
    artist_counts[artist] += weight
    track_counts[f"{artist} - {title}"] += weight
    source_counts[str(track.get("source", "unknown"))] += 1
    for genre in track.get("genres", []):
        genre_counts[str(genre).lower()] += weight


def _add_artist_signal(
    artist: dict[str, Any],
    artist_counts: Counter[str],
    genre_counts: Counter[str],
    source_counts: Counter[str],
    weight: int,
) -> None:
    artist_name = str(artist.get("artist", "Unknown Artist"))
    artist_counts[artist_name] += weight
    source_counts[str(artist.get("source", "unknown"))] += 1
    for genre in artist.get("genres", []):
        genre_counts[str(genre).lower()] += weight


def _time_range_weight(time_range: str) -> int:
    return {
        "short_term": 3,
        "medium_term": 2,
        "long_term": 2,
    }.get(time_range, 1)


def _spotify_context_counts(
    spotify_taste_context: dict[str, Any] | None,
) -> dict[str, int]:
    if not spotify_taste_context:
        return {}
    return {
        "recent_plays": len(spotify_taste_context.get("recent_plays", [])),
        "top_artists_short_term": len(spotify_taste_context.get("top_artists", {}).get("short_term", [])),
        "top_artists_medium_term": len(spotify_taste_context.get("top_artists", {}).get("medium_term", [])),
        "top_artists_long_term": len(spotify_taste_context.get("top_artists", {}).get("long_term", [])),
        "top_tracks_short_term": len(spotify_taste_context.get("top_tracks", {}).get("short_term", [])),
        "top_tracks_medium_term": len(spotify_taste_context.get("top_tracks", {}).get("medium_term", [])),
        "top_tracks_long_term": len(spotify_taste_context.get("top_tracks", {}).get("long_term", [])),
        "saved_tracks": len(spotify_taste_context.get("saved_tracks", [])),
        "followed_artists": len(spotify_taste_context.get("followed_artists", [])),
    }
