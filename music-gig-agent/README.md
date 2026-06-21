# music-gig-agent

A first working pipeline for recommending Birmingham gigs from recent Spotify listening.

By default it uses local JSON files and deterministic placeholder analysis so the end-to-end flow is easy to run. It can also fetch real recent plays from Spotify using OAuth with PKCE. It does not call OpenAI or live gig web search yet.

## Current Status

- Mock pipeline: working.
- Spotify collector: working and tested against Spotify OAuth plus recent-played API.
- Token cache: `.spotify_token.json`, ignored by git.
- Spotify snapshots: use `--save-spotify-snapshot` to write dated live-listening history under `data/spotify_snapshots/`.
- LLM taste profile: use `--llm-taste-profile` with `OPENAI_API_KEY` to create `output/taste_profile_llm.json` and add an interpretive section to the report.
- Live Spotify enrichment: recent plays are enriched with Spotify artist genres, popularity, follower counts, and artist URLs.
- Live Spotify limitation: moods and energy are still placeholders.
- Handoff notes and next steps live in `PROJECT_LOG.md`.

## Pipeline

1. Load recent plays from `data/mock_spotify_recent.json`, or from Spotify with `--spotify`
2. Build a taste profile from artists, genres, moods, and energy levels
3. Load Birmingham gig candidates from `data/mock_gigs.json`
4. Enrich each gig with placeholder analysis:
   - style summary
   - similar artists
   - why you might like it
   - why you might not
   - confidence score
   - suggested first song
5. Match and rank gigs against the taste profile
6. Write `output/monthly_report.md`

## Run

```bash
cd music-gig-agent
python3 main.py
```

The generated report will be written to:

```text
output/monthly_report.md
```

## Run With Spotify

Create an app in the Spotify Developer Dashboard and add this redirect URI:

```text
http://127.0.0.1:8765/callback
```

Then run:

```bash
cd music-gig-agent
export SPOTIFY_CLIENT_ID="your-client-id"
python3 main.py --spotify
```

The first run opens a browser for Spotify authorization and stores a local token cache in `.spotify_token.json`. The required scope is `user-read-recently-played`.

Optional settings:

```bash
export SPOTIFY_REDIRECT_URI="http://127.0.0.1:8765/callback"
export SPOTIFY_TOKEN_CACHE=".spotify_token.json"
export SPOTIFY_PENDING_AUTH=".spotify_auth_pending.json"
export SPOTIFY_RECENT_LIMIT="50"
```

If the localhost callback is not reachable from your browser, use the manual flow:

```bash
export SPOTIFY_CLIENT_ID="your-client-id"
python3 main.py --spotify-auth-url
```

Open the printed URL, approve the app, and copy the `code` query parameter from the redirect URL. Then run:

```bash
python3 main.py --spotify-code "the-code-from-the-redirect"
```

Spotify's recently played endpoint returns tracks and artists, not the mock-only mood and energy fields. The connector enriches recent plays with Spotify artist metadata so genres are available for taste analysis, while moods and energy remain placeholders for a later OpenAI or metadata enrichment step.

## Save Spotify Snapshots

To build a listening history without changing the stable mock fixture:

```bash
export SPOTIFY_CLIENT_ID="your-client-id"
python3 main.py --save-spotify-snapshot
```

This fetches live Spotify recent plays, writes the normal report, and saves a dated snapshot like:

```text
data/spotify_snapshots/spotify_recent_2026-06-21_132500.json
```

Snapshot files contain metadata plus the normalized `recent_plays` list and can later be used to build longer-term taste profiles.

## Create an LLM Taste Profile

To ask OpenAI to turn the recent plays and deterministic counts into a structured taste profile:

```bash
export OPENAI_API_KEY="your-openai-api-key"
python3 main.py --spotify --llm-taste-profile
```

By default this uses `gpt-5.2`. You can override the model:

```bash
export OPENAI_MODEL="gpt-5.2"
```

The structured profile is saved to:

```text
output/taste_profile_llm.json
```

The same profile is also included in `output/monthly_report.md`.

## Future Integration Points

- `collectors/spotify_collector.py`: add longer-term history loading from dated Spotify snapshots.
- `analysis/gig_analyst.py`: replace placeholder enrichment with OpenAI-powered artist and gig analysis.
- `collectors/gig_collector.py`: replace local JSON loading with web search, venue feeds, or ticketing APIs.
