import argparse
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from analysis.gig_analyst import enrich_gigs
from analysis.live_taste_analyst import build_live_taste_profile
from analysis.llm_gig_analyst import (
    DEFAULT_GIG_ENRICHMENT_LIMIT,
    OpenAIGigEnrichmentError,
    build_llm_gig_enrichment_payload,
    enrich_gigs_with_llm,
    write_gig_enrichment_output,
    write_gig_enrichment_preview,
)
from analysis.llm_taste_analyst import (
    DEFAULT_MAX_RECENT_PLAYS,
    DEFAULT_OPENAI_MODEL,
    OpenAITasteProfileError,
    build_llm_taste_profile,
    build_llm_taste_profile_payload,
    write_llm_input_preview,
    write_llm_taste_profile,
)
from analysis.recommendation_scorer import score_and_rank_gigs
from analysis.taste_analyst import build_taste_profile
from collectors.gig_collector import load_gigs
from collectors.history_collector import load_gig_history
from collectors.spotify_collector import (
    build_config_from_env,
    load_recent_plays,
    load_spotify_taste_context,
    print_manual_authorization_url,
    save_token_from_authorization_code,
)
from collectors.web_gig_collector import (
    DEFAULT_GIG_SEARCH_MAX_RESULTS,
    GigSearchError,
    build_gig_search_payload,
    collect_gigs_from_sources,
    collect_gigs_with_openai,
    default_date_from,
    default_date_to,
    merge_fresh_collections,
    merge_with_existing_gig_pool,
    write_collected_gigs,
    write_gig_search_payload_preview,
    write_gig_search_snapshot,
)
from reporting.email_sender import EmailConfigError, send_report_email
from reporting.report_writer import write_monthly_report


BASE_DIR = Path(__file__).resolve().parent
SPOTIFY_DATA_PATH = BASE_DIR / "data" / "mock_spotify_recent.json"
GIG_DATA_PATH = BASE_DIR / "data" / "mock_gigs.json"
COLLECTED_GIG_DATA_PATH = BASE_DIR / "data" / "collected_gigs.json"
GIG_SEARCH_SNAPSHOT_DIR = BASE_DIR / "data" / "gig_search_snapshots"
GIG_HISTORY_PATH = BASE_DIR / "data" / "user_history" / "gigs_attended.json"
REPORT_PATH = BASE_DIR / "output" / "monthly_report.md"
HTML_REPORT_PATH = BASE_DIR / "output" / "monthly_report.html"
LLM_TASTE_PROFILE_PATH = BASE_DIR / "output" / "taste_profile_llm.json"
LLM_INPUT_PREVIEW_PATH = BASE_DIR / "output" / "llm_taste_profile_input.json"
GIG_ENRICHMENT_INPUT_PREVIEW_PATH = BASE_DIR / "output" / "gig_enrichment_input.json"
GIG_ENRICHMENT_OUTPUT_PATH = BASE_DIR / "output" / "gig_enrichment_output.json"
GIG_SEARCH_INPUT_PREVIEW_PATH = BASE_DIR / "output" / "gig_search_input.json"
SPOTIFY_SNAPSHOT_DIR = BASE_DIR / "data" / "spotify_snapshots"


def main() -> None:
    load_local_env(BASE_DIR / ".env")
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

    spotify_taste_context = load_spotify_taste_context(spotify_config) if spotify_config else None
    recent_plays = (
        spotify_taste_context["recent_plays"]
        if spotify_taste_context
        else load_recent_plays(SPOTIFY_DATA_PATH)
    )
    source = "Spotify collector" if spotify_config else "mock Spotify data"
    print(f"Loaded {len(recent_plays)} recent plays from {source}.")
    if spotify_taste_context:
        counts = spotify_taste_context_counts(spotify_taste_context)
        print(
            "Loaded Spotify taste context: "
            f"{counts['top_artists']} top artists, "
            f"{counts['top_tracks']} top tracks, "
            f"{counts['saved_tracks']} saved tracks, "
            f"{counts['followed_artists']} followed artists."
        )

    if args.save_spotify_snapshot:
        snapshot_path = write_spotify_snapshot(
            SPOTIFY_SNAPSHOT_DIR,
            recent_plays,
            spotify_taste_context,
        )
        print(f"Saved Spotify recent-play snapshot: {snapshot_path}")

    taste_profile = build_taste_profile(recent_plays, spotify_taste_context)
    gig_history = load_gig_history(GIG_HISTORY_PATH)
    live_taste_profile = build_live_taste_profile(gig_history)
    if live_taste_profile["gig_count"]:
        print(f"Loaded {live_taste_profile['gig_count']} attended gigs from user history.")

    llm_taste_profile = None
    llm_model = args.openai_model or os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    llm_max_recent_plays = resolve_llm_max_recent_plays(args)
    if args.dry_run_llm_input:
        request_payload = build_llm_taste_profile_payload(
            recent_plays,
            taste_profile,
            spotify_taste_context,
            llm_model,
            llm_max_recent_plays,
        )
        write_llm_input_preview(LLM_INPUT_PREVIEW_PATH, request_payload)
        print(f"Saved OpenAI dry-run payload: {LLM_INPUT_PREVIEW_PATH}")
        print(f"No OpenAI API call was made. Model: {llm_model}.")
        print(f"Included recent plays: {min(len(recent_plays), llm_max_recent_plays)}.")
        return

    if args.llm_taste_profile:
        if not args.confirm_openai_cost:
            raise RuntimeError(
                "Refusing to call OpenAI without --confirm-openai-cost. "
                "Run --dry-run-llm-input first to inspect the payload."
            )
        try:
            llm_taste_profile = build_llm_taste_profile(
                recent_plays,
                taste_profile,
                spotify_taste_context=spotify_taste_context,
                model=llm_model,
                max_recent_plays=llm_max_recent_plays,
            )
        except OpenAITasteProfileError as error:
            raise SystemExit(f"OpenAI taste profile failed: {error}") from error
        write_llm_taste_profile(LLM_TASTE_PROFILE_PATH, llm_taste_profile)
        print(f"Saved LLM taste profile: {LLM_TASTE_PROFILE_PATH}")

    gig_search_model = args.gig_search_model or os.environ.get(
        "OPENAI_GIG_SEARCH_MODEL",
        os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
    )
    gig_search_date_from = args.gig_search_from or default_date_from()
    gig_search_date_to = args.gig_search_to or default_date_to()
    if args.dry_run_gig_search:
        priority_artists = build_gig_search_priority_artists(recent_plays, taste_profile)
        if args.deep_gig_search:
            payload = {
                "search_type": "deep_multi_pass",
                "passes": [
                    build_gig_search_payload(
                        args.gig_city,
                        gig_search_date_from,
                        gig_search_date_to,
                        args.gig_search_max_results,
                        gig_search_model,
                        search_mode="broad_discovery",
                    ),
                    build_gig_search_payload(
                        args.gig_city,
                        gig_search_date_from,
                        gig_search_date_to,
                        args.gig_search_max_results,
                        gig_search_model,
                        search_mode="venue_calendar_sweep",
                    ),
                    build_gig_search_payload(
                        args.gig_city,
                        gig_search_date_from,
                        gig_search_date_to,
                        args.gig_search_max_results,
                        gig_search_model,
                        search_mode="taste_artist_sweep",
                        priority_artists=priority_artists,
                    ),
                ],
            }
        else:
            payload = build_gig_search_payload(
                args.gig_city,
                gig_search_date_from,
                gig_search_date_to,
                args.gig_search_max_results,
                gig_search_model,
                priority_artists=priority_artists,
            )
        write_gig_search_payload_preview(GIG_SEARCH_INPUT_PREVIEW_PATH, payload)
        print(f"Saved OpenAI gig-search dry-run payload: {GIG_SEARCH_INPUT_PREVIEW_PATH}")
        print("No OpenAI API call was made.")
        return

    if args.collect_gigs:
        if not args.skip_openai_gig_search and not args.confirm_openai_search_cost:
            raise RuntimeError(
                "Refusing to call OpenAI web search without --confirm-openai-search-cost. "
                "Run --dry-run-gig-search first to inspect the payload."
            )
        try:
            priority_artists = build_gig_search_priority_artists(recent_plays, taste_profile)
            fresh_collections = []
            if args.deterministic_gig_search:
                source_gigs = collect_gigs_from_sources(
                    args.gig_city,
                    gig_search_date_from,
                    gig_search_date_to,
                    args.gig_search_max_results,
                    priority_artists=priority_artists,
                )
                fresh_collections.append(source_gigs)
                print(
                    "Collected "
                    f"{source_gigs['gig_count']} gigs with deterministic source search."
                )
            if not args.skip_openai_gig_search:
                openai_gigs = collect_gigs_with_openai(
                    args.gig_city,
                    gig_search_date_from,
                    gig_search_date_to,
                    args.gig_search_max_results,
                    gig_search_model,
                    priority_artists=priority_artists,
                    deep_search=args.deep_gig_search,
                )
                fresh_collections.append(openai_gigs)
                print(f"Collected {openai_gigs['gig_count']} gigs with OpenAI web search.")
            if not fresh_collections:
                raise GigSearchError("No gig search mode selected.")
            if len(fresh_collections) == 1:
                collected_gigs = fresh_collections[0]
            else:
                collected_gigs = merge_fresh_collections(
                    fresh_collections,
                    args.gig_city,
                    gig_search_date_from,
                    gig_search_date_to,
                    args.gig_search_max_results,
                )
        except GigSearchError as error:
            raise SystemExit(f"OpenAI gig search failed: {error}") from error
        snapshot_path = write_gig_search_snapshot(GIG_SEARCH_SNAPSHOT_DIR, collected_gigs)
        if args.replace_collected_gigs:
            merged_gigs = dict(collected_gigs)
            merged_gigs["source"] = "fresh_gig_pool"
            merged_gigs["pool_updated_at"] = datetime.now().astimezone().isoformat()
            merged_gigs["search_notes"] = [
                *collected_gigs.get("search_notes", []),
                "Replaced the rolling gig pool with this fresh search.",
            ]
        else:
            merged_gigs = merge_with_existing_gig_pool(
                COLLECTED_GIG_DATA_PATH,
                collected_gigs,
                gig_search_date_from,
                gig_search_date_to,
            )
        write_collected_gigs(COLLECTED_GIG_DATA_PATH, merged_gigs)
        print(f"Collected {collected_gigs['gig_count']} total fresh gigs.")
        if args.replace_collected_gigs:
            print(f"Replaced rolling gig pool with {merged_gigs['gig_count']} fresh gigs.")
        else:
            print(f"Merged rolling gig pool now has {merged_gigs['gig_count']} gigs.")
        print(f"Saved collected gigs: {COLLECTED_GIG_DATA_PATH}")
        print(f"Saved gig search snapshot: {snapshot_path}")

    gig_data_path = (
        COLLECTED_GIG_DATA_PATH
        if (args.use_collected_gigs or args.collect_gigs) and COLLECTED_GIG_DATA_PATH.exists()
        else GIG_DATA_PATH
    )
    gigs = load_gigs(gig_data_path)
    print(f"Loaded {len(gigs)} candidate gigs from {gig_data_path.name}.")
    enriched_gigs = enrich_gigs(gigs, taste_profile)

    gig_enrichment_model = args.gig_enrichment_model or os.environ.get(
        "OPENAI_GIG_ENRICHMENT_MODEL",
        os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
    )
    if args.dry_run_gig_enrichment_input:
        payload = build_llm_gig_enrichment_payload(
            enriched_gigs,
            taste_profile,
            live_taste_profile,
            llm_taste_profile,
            gig_enrichment_model,
            args.gig_enrichment_limit,
        )
        write_gig_enrichment_preview(GIG_ENRICHMENT_INPUT_PREVIEW_PATH, payload)
        print(f"Saved OpenAI gig-enrichment dry-run payload: {GIG_ENRICHMENT_INPUT_PREVIEW_PATH}")
        print("No OpenAI API call was made.")
        return

    if args.llm_gig_enrichment:
        if not args.confirm_openai_cost:
            raise RuntimeError(
                "Refusing to call OpenAI for gig enrichment without --confirm-openai-cost. "
                "Run --dry-run-gig-enrichment-input first to inspect the payload."
            )
        try:
            enriched_gigs = enrich_gigs_with_llm(
                enriched_gigs,
                taste_profile,
                live_taste_profile,
                llm_taste_profile,
                gig_enrichment_model,
                args.gig_enrichment_limit,
            )
        except OpenAIGigEnrichmentError as error:
            raise SystemExit(f"OpenAI gig enrichment failed: {error}") from error
        write_gig_enrichment_output(GIG_ENRICHMENT_OUTPUT_PATH, enriched_gigs)
        print(f"Saved LLM gig enrichment output: {GIG_ENRICHMENT_OUTPUT_PATH}")

    ranked_gigs = score_and_rank_gigs(
        enriched_gigs,
        taste_profile,
        live_taste_profile,
        llm_taste_profile,
    )

    write_monthly_report(
        REPORT_PATH,
        taste_profile,
        ranked_gigs,
        llm_taste_profile,
        live_taste_profile,
    )
    print(f"Created report: {REPORT_PATH}")
    if HTML_REPORT_PATH.exists():
        print(f"Created HTML report: {HTML_REPORT_PATH}")

    if args.email_report:
        try:
            send_report_email(
                REPORT_PATH,
                html_report_path=HTML_REPORT_PATH,
                to_address=args.email_to,
                subject=args.email_subject,
            )
        except EmailConfigError as error:
            raise SystemExit(f"Email report failed: {error}") from error
        print("Sent report email.")


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
    parser.add_argument(
        "--dry-run-llm-input",
        action="store_true",
        help="Write the OpenAI request payload for inspection without making an API call.",
    )
    parser.add_argument(
        "--confirm-openai-cost",
        action="store_true",
        help="Required before --llm-taste-profile will make an OpenAI API call.",
    )
    parser.add_argument(
        "--openai-model",
        help=f"OpenAI model for LLM taste profiling. Default: {DEFAULT_OPENAI_MODEL}.",
    )
    parser.add_argument(
        "--llm-max-recent-plays",
        type=int,
        help=f"Maximum recent plays sent to OpenAI. Default: {DEFAULT_MAX_RECENT_PLAYS}.",
    )
    parser.add_argument(
        "--collect-gigs",
        action="store_true",
        help="Use OpenAI web search to collect Birmingham gig listings.",
    )
    parser.add_argument(
        "--dry-run-gig-search",
        action="store_true",
        help="Write the OpenAI web-search payload for gig collection without making an API call.",
    )
    parser.add_argument(
        "--confirm-openai-search-cost",
        action="store_true",
        help="Required before --collect-gigs will make an OpenAI web-search call.",
    )
    parser.add_argument(
        "--use-collected-gigs",
        action="store_true",
        help="Use data/collected_gigs.json instead of data/mock_gigs.json if it exists.",
    )
    parser.add_argument(
        "--replace-collected-gigs",
        action="store_true",
        help="When collecting gigs, overwrite the rolling gig pool with the fresh search instead of merging old entries.",
    )
    parser.add_argument(
        "--gig-city",
        default="Birmingham",
        help="City to search for gig listings. Default: Birmingham.",
    )
    parser.add_argument(
        "--gig-search-from",
        help="Start date for gig search in YYYY-MM-DD format. Default: today.",
    )
    parser.add_argument(
        "--gig-search-to",
        help="End date for gig search in YYYY-MM-DD format. Default: 60 days from today.",
    )
    parser.add_argument(
        "--gig-search-max-results",
        type=int,
        default=DEFAULT_GIG_SEARCH_MAX_RESULTS,
        help=f"Maximum gig listings to request. Default: {DEFAULT_GIG_SEARCH_MAX_RESULTS}.",
    )
    parser.add_argument(
        "--deep-gig-search",
        action="store_true",
        help="Run broad discovery, venue-calendar, and taste-targeted artist search passes, then merge and dedupe the results.",
    )
    parser.add_argument(
        "--deterministic-gig-search",
        action="store_true",
        help="Query fixed listing sources with venue/artist terms and parse event pages before optional OpenAI discovery.",
    )
    parser.add_argument(
        "--skip-openai-gig-search",
        action="store_true",
        help="Use deterministic gig source search only; useful for debugging collection without an OpenAI web-search call.",
    )
    parser.add_argument(
        "--gig-search-model",
        help="OpenAI model for gig web search. Defaults to OPENAI_GIG_SEARCH_MODEL, OPENAI_MODEL, or taste-profile default.",
    )
    parser.add_argument(
        "--llm-gig-enrichment",
        action="store_true",
        help="Use OpenAI to enrich candidate gigs with personalized analysis and score hints.",
    )
    parser.add_argument(
        "--dry-run-gig-enrichment-input",
        action="store_true",
        help="Write the OpenAI gig-enrichment payload without making an API call.",
    )
    parser.add_argument(
        "--gig-enrichment-limit",
        type=int,
        default=DEFAULT_GIG_ENRICHMENT_LIMIT,
        help=f"Maximum gigs sent to OpenAI for enrichment. Default: {DEFAULT_GIG_ENRICHMENT_LIMIT}.",
    )
    parser.add_argument(
        "--gig-enrichment-model",
        help="OpenAI model for gig enrichment. Defaults to OPENAI_GIG_ENRICHMENT_MODEL, OPENAI_MODEL, or taste-profile default.",
    )
    parser.add_argument(
        "--email-report",
        action="store_true",
        help="Email output/monthly_report.md after the report is created.",
    )
    parser.add_argument(
        "--email-to",
        help="Recipient email address. Defaults to EMAIL_TO from .env or environment.",
    )
    parser.add_argument(
        "--email-subject",
        help="Email subject. Defaults to EMAIL_SUBJECT or a standard report subject.",
    )
    args = parser.parse_args()
    if args.llm_taste_profile and not args.confirm_openai_cost and not args.dry_run_llm_input:
        parser.error(
            "--llm-taste-profile requires --confirm-openai-cost. "
            "Use --dry-run-llm-input first to inspect the payload."
        )
    if args.collect_gigs and not args.confirm_openai_search_cost and not args.dry_run_gig_search:
        if not args.skip_openai_gig_search:
            parser.error(
                "--collect-gigs requires --confirm-openai-search-cost unless --skip-openai-gig-search is used. "
                "Use --dry-run-gig-search first to inspect the payload."
            )
    if args.skip_openai_gig_search and not args.deterministic_gig_search:
        parser.error("--skip-openai-gig-search requires --deterministic-gig-search.")
    if args.llm_gig_enrichment and not args.confirm_openai_cost and not args.dry_run_gig_enrichment_input:
        parser.error(
            "--llm-gig-enrichment requires --confirm-openai-cost. "
            "Use --dry-run-gig-enrichment-input first to inspect the payload."
        )
    return args


def uses_spotify(args: argparse.Namespace) -> bool:
    return bool(
        args.spotify
        or args.spotify_auth_url
        or args.spotify_code
        or args.save_spotify_snapshot
    )


def write_spotify_snapshot(
    snapshot_dir: Path,
    recent_plays: list[dict[str, Any]],
    spotify_taste_context: dict[str, Any] | None = None,
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
    if spotify_taste_context:
        snapshot["spotify_taste_context"] = spotify_taste_context
        snapshot["spotify_taste_context_counts"] = spotify_taste_context_counts(
            spotify_taste_context
        )
    snapshot_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return snapshot_path


def spotify_taste_context_counts(spotify_taste_context: dict[str, Any]) -> dict[str, int]:
    return {
        "top_artists": sum(
            len(artists)
            for artists in spotify_taste_context.get("top_artists", {}).values()
        ),
        "top_tracks": sum(
            len(tracks)
            for tracks in spotify_taste_context.get("top_tracks", {}).values()
        ),
        "saved_tracks": len(spotify_taste_context.get("saved_tracks", [])),
        "followed_artists": len(spotify_taste_context.get("followed_artists", [])),
    }


def build_gig_search_priority_artists(
    recent_plays: list[dict[str, Any]],
    taste_profile: dict[str, Any],
    limit: int = 24,
) -> list[str]:
    counts: Counter[str] = Counter()
    for artist, count in taste_profile.get("top_artists", []):
        if str(artist).strip():
            counts[str(artist).strip()] += int(count)
    for play in recent_plays:
        artist = str(play.get("artist", "")).strip()
        if artist:
            counts[artist] += 1
    return [artist for artist, _count in counts.most_common(limit)]


def resolve_llm_max_recent_plays(args: argparse.Namespace) -> int:
    if args.llm_max_recent_plays is not None:
        return max(1, args.llm_max_recent_plays)
    if os.environ.get("OPENAI_MAX_RECENT_PLAYS"):
        return max(1, int(os.environ["OPENAI_MAX_RECENT_PLAYS"]))
    return DEFAULT_MAX_RECENT_PLAYS


def load_local_env(path: Path) -> None:
    """Load KEY=value pairs from a local .env file without overwriting exports."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


if __name__ == "__main__":
    main()
