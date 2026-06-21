import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from analysis.gig_analyst import enrich_gigs
from analysis.llm_taste_analyst import build_llm_taste_profile, write_llm_taste_profile
from analysis.matcher import rank_gigs
from analysis.taste_analyst import build_taste_profile
from collectors.gig_collector import load_gigs
from collectors.spotify_collector import (
    build_config_from_env,
    load_recent_plays,
    load_recent_plays_from_spotify,
    print_manual_authorization_url,
    save_token_from_authorization_code,
)
from reporting.report_writer import write_monthly_report


BASE_DIR = Path(__file__).resolve().parent
SPOTIFY_DATA_PATH = BASE_DIR / "data" / "mock_spotify_recent.json"
GIG_DATA_PATH = BASE_DIR / "data" / "mock_gigs.json"
REPORT_PATH = BASE_DIR / "output" / "monthly_report.md"
LLM_TASTE_PROFILE_PATH = BASE_DIR / "output" / "taste_profile_llm.json"
SPOTIFY_SNAPSHOT_DIR = BASE_DIR / "data" / "spotify_snapshots"


def main() -> None:
    args = parse_args()
    spotify_config = build_config_from_env(BASE_DIR) if uses_spotify(args) else None

    if args.spotify_auth_url:
        if spotify_config is None:
            raise RuntimeError("Spotify config was not created.")
        print("Spotify collector is configured for manual OAuth.")
        print("Open this Spotify authorization URL in your browser:")
        print(print_manual_authorization_url(spotify_config))
        print()
        print("After approval, copy the 'code' query parameter from the redirect URL.")
        print("Then run: python3 main.py --spotify-code '<code>'")
        return

    if args.spotify_code:
        if spotify_config is None:
            raise RuntimeError("Spotify config was not created.")
        save_token_from_authorization_code(spotify_config, args.spotify_code)
        print("Saved Spotify token cache for the Spotify collector.")

    recent_plays = (
        load_recent_plays_from_spotify(spotify_config)
        if spotify_config
        else load_recent_plays(SPOTIFY_DATA_PATH)
    )
    source = "Spotify collector" if spotify_config else "mock Spotify data"
    print(f"Loaded {len(recent_plays)} recent plays from {source}.")

    if args.save_spotify_snapshot:
        snapshot_path = write_spotify_snapshot(SPOTIFY_SNAPSHOT_DIR, recent_plays)
        print(f"Saved Spotify recent-play snapshot: {snapshot_path}")

    taste_profile = build_taste_profile(recent_plays)
    llm_taste_profile = None
    if args.llm_taste_profile:
        llm_taste_profile = build_llm_taste_profile(recent_plays, taste_profile)
        write_llm_taste_profile(LLM_TASTE_PROFILE_PATH, llm_taste_profile)
        print(f"Saved LLM taste profile: {LLM_TASTE_PROFILE_PATH}")

    gigs = load_gigs(GIG_DATA_PATH)
    enriched_gigs = enrich_gigs(gigs, taste_profile)
    ranked_gigs = rank_gigs(enriched_gigs, taste_profile)

    write_monthly_report(REPORT_PATH, taste_profile, ranked_gigs, llm_taste_profile)
    print(f"Created report: {REPORT_PATH}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recommend Birmingham gigs from listening data.")
    parser.add_argument(
        "--spotify",
        action="store_true",
        help="Fetch recent plays from Spotify instead of local mock data.",
    )
    parser.add_argument(
        "--spotify-auth-url",
        action="store_true",
        help="Print a Spotify authorization URL for manual OAuth.",
    )
    parser.add_argument(
        "--spotify-code",
        help="Exchange a manually copied Spotify authorization code, then run the pipeline.",
    )
    parser.add_argument(
        "--save-spotify-snapshot",
        action="store_true",
        help="Fetch Spotify plays and save a dated JSON snapshot under data/spotify_snapshots.",
    )
    parser.add_argument(
        "--llm-taste-profile",
        action="store_true",
        help="Use OpenAI to create a structured taste profile and add it to the report.",
    )
    return parser.parse_args()


def uses_spotify(args: argparse.Namespace) -> bool:
    return bool(
        args.spotify
        or args.spotify_auth_url
        or args.spotify_code
        or args.save_spotify_snapshot
    )


def write_spotify_snapshot(
    snapshot_dir: Path, recent_plays: list[dict[str, Any]]
) -> Path:
    collected_at = datetime.now().astimezone()
    filename = f"spotify_recent_{collected_at.strftime('%Y-%m-%d_%H%M%S')}.json"
    snapshot_path = snapshot_dir / filename
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "snapshot_type": "spotify_recent_plays",
        "source": "spotify",
        "collected_at": collected_at.isoformat(),
        "play_count": len(recent_plays),
        "recent_plays": recent_plays,
    }
    snapshot_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return snapshot_path


if __name__ == "__main__":
    main()
