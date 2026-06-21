# Project Log

## 2026-06-21

Current state:

- The first working `music-gig-agent` pipeline exists and runs with `python3 main.py`.
- The default mode still uses local mock Spotify data and mock Birmingham gig listings.
- A real Spotify collector now exists in `collectors/spotify_collector.py`.
- The Spotify collector supports OAuth Authorization Code with PKCE, token caching, token refresh, and `GET /me/player/recently-played`.
- The Spotify collector enriches recent plays with artist metadata from Spotify's `GET /artists` endpoint, including genres, popularity, follower counts, and artist URLs.
- Spotify OAuth was tested from this environment using the user's Spotify app client ID.
- A usable `.spotify_token.json` cache was created locally and is ignored by git.
- Running `python3 -u main.py --spotify` successfully regenerated `output/monthly_report.md` from 50 real Spotify recent plays.
- `--save-spotify-snapshot` saves dated live Spotify recent-play snapshots under `data/spotify_snapshots/` for future taste-history work.
- `--llm-taste-profile` creates a structured OpenAI taste profile at `output/taste_profile_llm.json` and adds it to the report. It requires `OPENAI_API_KEY`.

Important implementation note:

- Spotify recent-played data gives track and artist data, but not moods or audio energy in the shape the mock pipeline used.
- Real Spotify plays now get artist genres from Spotify artist metadata, but `moods` are still empty and `energy` remains neutral at `0.5`.

Next steps:

1. Improve taste analysis for real Spotify data.
   - Use artist frequency, track frequency, and enriched artist genres.
   - Consider lowering the effect of mood and energy when they are unavailable.
   - Add a history loader that combines dated snapshots from `data/spotify_snapshots/`.

2. Add OpenAI-powered gig enrichment.
   - Replace deterministic placeholder text in `analysis/gig_analyst.py`.
   - Generate style summaries, similar artists, reasons for and against, and first-song suggestions.
   - Feed the saved LLM taste profile into gig analysis once the report format has settled.

3. Add a real gig collector.
   - Start with Birmingham venue pages, RSS feeds, ticketing APIs, or curated sources.
   - Keep `data/mock_gigs.json` as a test fixture.

4. Add tests.
   - Unit test Spotify response normalization.
   - Unit test Spotify artist metadata enrichment.
   - Unit test matching scores with and without genres/moods.
   - Add a no-network test path using mock Spotify API payloads.
