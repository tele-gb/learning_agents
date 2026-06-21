# Project Log

## 2026-06-21

Current state:

- The first working `music-gig-agent` pipeline exists and runs with `python3 main.py`.
- The default mode still uses local mock Spotify data and mock Birmingham gig listings.
- A real Spotify collector now exists in `collectors/spotify_collector.py`.
- The Spotify collector supports OAuth Authorization Code with PKCE, token caching, token refresh, and `GET /me/player/recently-played`.
- The Spotify collector enriches recent plays with artist metadata from Spotify's `GET /artists` endpoint, including genres, popularity, follower counts, and artist URLs.
- Spotify taste analysis now goes beyond the latest 50 recent plays. The connector also requests `user-top-read`, `user-library-read`, and `user-follow-read`, then fetches top artists, top tracks, saved tracks, and followed artists for deterministic and LLM taste profiling.
- Existing Spotify token caches that only have the old recent-played scope trigger reauthorization so the wider taste-analysis scopes are granted.
- Spotify OAuth was tested from this environment using the user's Spotify app client ID.
- A usable `.spotify_token.json` cache was created locally and is ignored by git.
- Running `python3 -u main.py --spotify` successfully regenerated `output/monthly_report.md` from 50 real Spotify recent plays.
- `--save-spotify-snapshot` saves dated live Spotify recent-play snapshots plus the wider Spotify taste context under `data/spotify_snapshots/` for future taste-history work.
- `--dry-run-llm-input` writes `output/llm_taste_profile_input.json` with the exact OpenAI request payload and makes no API call.
- `--llm-taste-profile` creates a structured OpenAI taste profile at `output/taste_profile_llm.json` and adds it to the report. It requires `OPENAI_API_KEY` and `--confirm-openai-cost`.
- `--llm-max-recent-plays` or `OPENAI_MAX_RECENT_PLAYS` caps how much listening history is sent to OpenAI.
- Local secrets can be stored in an untracked `music-gig-agent/.env` file; `main.py` loads it before reading API settings.
- `data/user_history/gigs_attended.json` is loaded by `collectors/history_collector.py`.
- `analysis/live_taste_analyst.py` builds a live taste profile from attended gigs, ratings, tags, repeat intent, venues, and the listener profile metadata.
- `collectors/web_gig_collector.py` uses OpenAI web search to collect sourced Birmingham gig listings. It defaults to a 60-day search window and broad high-volume discovery mode, requires `--confirm-openai-search-cost`, supports `--dry-run-gig-search`, and rejects listings without source evidence.
- Fresh gig searches are now merged into a rolling `data/collected_gigs.json` pool instead of overwriting it. Raw search results still get dated snapshots under `data/gig_search_snapshots/`; the rolling pool dedupes by artist/date/venue, keeps better-confidence evidence, and prunes events outside the active search window.
- `analysis/recommendation_scorer.py` ranks gigs with a transparent score breakdown: music fit, live fit, venue fit, novelty fit, and evidence quality. The report now prints scoring reasons and warnings for each recommendation.
- `analysis/llm_gig_analyst.py` can enrich candidate gigs with personalized style summaries, reasons, first-song suggestions, semantic tags, and bounded score hints. It requires `--confirm-openai-cost` and supports `--dry-run-gig-enrichment-input`.
- `--email-report` sends `output/monthly_report.md` through SMTP settings from `.env`. `scripts/run_every_three_days.py` runs the full live pipeline with email and records successful scheduled runs so cron can call it daily at 10am while the wrapper only runs the expensive pipeline every 3 days.

Important implementation note:

- Spotify recent-played data gives track and artist data, but not moods or audio energy in the shape the mock pipeline used.
- Real Spotify plays now get artist genres from Spotify artist metadata, but `moods` are still empty and `energy` remains neutral at `0.5`.
- The LLM taste-profile payload strips placeholder `average_energy` and mood counts from the deterministic profile, adds unavailable-signal notes, and asks the model for stronger opinions plus weak-signal caveats.

Next steps:

1. Improve taste analysis for real Spotify data.
   - Use artist frequency, track frequency, and enriched artist genres.
   - Consider lowering the effect of mood and energy when they are unavailable.
   - Add a history loader that combines dated snapshots from `data/spotify_snapshots/`.

2. Tune transparent ranking.
   - Improve the first-pass keyword matching in `analysis/recommendation_scorer.py`.
   - Calibrate weights after looking at real recommendations.
   - Feed LLM taste-profile fields and live-history notes into gig enrichment and scoring more precisely.

3. Tune OpenAI-powered gig enrichment.
   - Review real `--llm-gig-enrichment` outputs.
   - Tune prompt/schema for sharper live-history reasoning.
   - Decide whether to cache enriched gigs between report runs.

4. Add a real gig collector.
   - Improve `collectors/web_gig_collector.py` with venue-specific search strategies.
   - Consider adding non-LLM source fetchers for venue pages, RSS feeds, ticketing APIs, or curated sources.
   - Keep `data/mock_gigs.json` as a test fixture.

5. Add tests.
   - Unit test Spotify response normalization.
   - Unit test Spotify artist metadata enrichment.
   - Unit test attended-gig history loading and live taste profile generation.
   - Unit test matching scores with and without genres/moods.
   - Add a no-network test path using mock Spotify API payloads.
