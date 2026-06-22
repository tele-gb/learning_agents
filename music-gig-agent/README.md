# music-gig-agent

A first working pipeline for recommending Birmingham gigs from recent Spotify listening.

By default it uses local JSON files and deterministic placeholder analysis so the end-to-end flow is easy to run. It can also fetch real recent plays from Spotify using OAuth with PKCE, call OpenAI for taste/gig analysis, and use OpenAI web search to collect sourced live listings.

## Current Status

- Mock pipeline: working.
- Spotify collector: working and tested against Spotify OAuth plus recent-played API. It now also collects top artists, top tracks, saved tracks, and followed artists for richer taste analysis.
- Token cache: `.spotify_token.json`, ignored by git.
- Spotify snapshots: use `--save-spotify-snapshot` to write dated live-listening and taste-context history under `data/spotify_snapshots/`.
- Gig attendance history: `data/user_history/gigs_attended.json` is loaded into a live taste profile for report context.
- LLM taste profile: use `--llm-taste-profile` with `OPENAI_API_KEY` to create `output/taste_profile_llm.json` and add an interpretive section to the report.
- Gig web search: use `--collect-gigs` with `--confirm-openai-search-cost` to collect sourced Birmingham and West Midlands listings into `data/collected_gigs.json`.
- Live Spotify enrichment: recent plays are enriched with Spotify artist genres, popularity, follower counts, and artist URLs.
- Live Spotify limitation: moods and energy are not reliable Spotify signals here; the LLM taste profile is instructed to ignore the local neutral energy placeholder.
- Handoff notes and next steps live in `PROJECT_LOG.md`.

## Pipeline

1. Load recent plays from `data/mock_spotify_recent.json`, or richer Spotify taste context with `--spotify`
2. Build a taste profile from recent plays, top artists, top tracks, saved tracks, followed artists, genres, moods, and energy levels
3. Load attended-gig history from `data/user_history/gigs_attended.json`
4. Build a live taste profile from ratings, tags, repeat intent, and venue history
5. Load Birmingham and West Midlands gig candidates from `data/mock_gigs.json`
6. Enrich each gig with placeholder analysis:
   - style summary
   - similar artists
   - why you might like it
   - why you might not
   - confidence score
   - suggested first song
7. Score and rank gigs with a transparent recommendation breakdown:
   - music fit
   - live fit
   - venue fit
   - novelty fit
   - evidence quality
8. Write `output/monthly_report.md` and `output/monthly_report.html`

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

The first run opens a browser for Spotify authorization and stores a local token cache in `.spotify_token.json`. The required scopes are:

```text
user-read-recently-played
user-top-read
user-library-read
user-follow-read
```

If an older cached token only has the recent-played scope, the app will ask Spotify for authorization again so it can read the broader taste signals.

Optional settings:

```bash
export SPOTIFY_REDIRECT_URI="http://127.0.0.1:8765/callback"
export SPOTIFY_TOKEN_CACHE=".spotify_token.json"
export SPOTIFY_PENDING_AUTH=".spotify_auth_pending.json"
export SPOTIFY_RECENT_LIMIT="50"
export SPOTIFY_TOP_LIMIT="50"
export SPOTIFY_SAVED_TRACK_LIMIT="50"
export SPOTIFY_FOLLOWED_ARTIST_LIMIT="50"
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

Spotify's recently played endpoint returns tracks and artists, not the mock-only mood and energy fields. The connector enriches recent plays with Spotify artist metadata so genres are available for taste analysis. Moods and energy are treated as unavailable for live Spotify data; the local `0.5` energy value is a neutral placeholder, not a Spotify judgement.

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

Snapshot files contain metadata, the normalized `recent_plays` list, and when using Spotify, the wider `spotify_taste_context` containing top artists, top tracks, saved tracks, and followed artists. These snapshots can later be combined to build longer-term taste profiles.

## Add Gig History

Personal live history belongs in:

```text
data/user_history/gigs_attended.json
```

The current loader expects:

```text
listener_profile
gigs_attended
live_history_summary
```

Each attended gig can include:

```text
artist
venue
rating
would_go_again
tags
notes
```

The report uses this to create a `Live Taste Profile`, which is deliberately separate from Spotify listening taste.

## Create an LLM Taste Profile

First inspect exactly what would be sent to OpenAI:

```bash
export OPENAI_API_KEY="your-openai-api-key"
export SPOTIFY_CLIENT_ID="your-client-id"
python3 main.py --spotify --dry-run-llm-input
```

You can also keep local secrets in an untracked `.env` file:

```text
OPENAI_API_KEY=your-openai-api-key
SPOTIFY_CLIENT_ID=your-client-id
```

This writes the request payload to:

```text
output/llm_taste_profile_input.json
```

No OpenAI API call is made during a dry run.

When you are happy with the payload and your account limits, explicitly confirm the paid API call:

```bash
python3 main.py --spotify --llm-taste-profile --confirm-openai-cost
```

By default this uses `gpt-5.2`. You can override the model:

```bash
export OPENAI_MODEL="gpt-5.2"
```

You can also cap how much listening history is sent:

```bash
export OPENAI_MAX_RECENT_PLAYS="50"
python3 main.py --spotify --dry-run-llm-input --llm-max-recent-plays 25
```

The structured profile is saved to:

```text
output/taste_profile_llm.json
```

The same profile is also included in `output/monthly_report.md`.

The app refuses to call OpenAI for taste profiling unless `--confirm-openai-cost` is present.

The LLM taste profile is deliberately told to ignore placeholder energy and empty moods. It should make stronger judgements from repeat artists, top artists/tracks across time ranges, saved tracks, followed artists, genre clusters, recency, and mainstream-vs-niche artist context, while listing weak or missing signals separately.

## Collect Real Gig Listings

First inspect the OpenAI web-search request:

```bash
python3 main.py --dry-run-gig-search
```

This writes:

```text
output/gig_search_input.json
```

No OpenAI API call is made during a dry run.

By default, gig search looks at the next 60 days and asks for a broad, high-volume discovery pool across Birmingham and the wider West Midlands, including nearby towns such as Wolverhampton, Warwick, Coventry, and Leamington Spa. It is intentionally not limited to obvious taste matches because weird sourced candidates are useful.

For a more reliable run, use deterministic gig search plus deep gig search. The deterministic pass queries fixed listing sources with venue and artist search terms, fetches event-looking pages, and parses structured event data. Deep search then adds OpenAI web-search passes for broad discovery, named venue calendars, and high-priority artists from your listening history. The results are merged and deduped:

```bash
python3 main.py --spotify --collect-gigs --deterministic-gig-search --deep-gig-search --confirm-openai-search-cost
```

To debug the deterministic collector without making an OpenAI web-search call:

```bash
python3 main.py --spotify --collect-gigs --deterministic-gig-search --skip-openai-gig-search
```

When ready, explicitly confirm the web-search call:

```bash
python3 main.py --collect-gigs --confirm-openai-search-cost
```

Each fresh search is saved as a dated snapshot, then merged into the rolling gig pool:

```text
data/collected_gigs.json
data/gig_search_snapshots/
```

Generated gig listings are ignored by git by default. The collector rejects results without a source URL, artist, venue, ISO-like date, source name, and minimum confidence. Existing pool entries outside the active search window are pruned, duplicates are collapsed by artist/date/venue, and the best-confidence evidence is kept. The report displays listing evidence for sourced gigs.

To start clean and avoid carrying older search results into the next report:

```bash
python3 main.py --spotify --collect-gigs --replace-collected-gigs --deterministic-gig-search --deep-gig-search --confirm-openai-search-cost
```

For a bigger discovery sweep:

```bash
python3 main.py --collect-gigs --confirm-openai-search-cost --gig-search-max-results 50
```

To use the latest collected listings without searching again:

```bash
python3 main.py --use-collected-gigs
```

## Enrich Gig Recommendations With OpenAI

First inspect the payload:

```bash
python3 main.py --use-collected-gigs --dry-run-gig-enrichment-input
```

This writes:

```text
output/gig_enrichment_input.json
```

No OpenAI API call is made during a dry run.

When ready, explicitly confirm the call:

```bash
python3 main.py --use-collected-gigs --llm-gig-enrichment --confirm-openai-cost
```

The enrichment step does not change listing facts. It adds better style summaries, personalized reasons, first-song suggestions, semantic tags, and bounded scoring hints. The scorer can use those hints, but the original listing evidence remains visible in the report.

## Email The Report

Add SMTP settings to your untracked `.env` file:

```text
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=your-smtp-username
SMTP_PASSWORD=your-smtp-password
SMTP_USE_TLS=true
SMTP_USE_SSL=false
EMAIL_FROM=you@example.com
EMAIL_TO=you@example.com
EMAIL_SUBJECT=Birmingham gig recommendations
```

Then run:

```bash
python3 main.py --use-collected-gigs --email-report
```

The report is sent with a styled HTML body, with `monthly_report.md` and `monthly_report.html` attached.

## Schedule Every 3 Days

The scheduler wrapper runs the full live pipeline, sends the email, and records the last successful scheduled run:

```bash
python3 scripts/run_every_three_days.py --force
```

For cron, run the wrapper every morning at 10am. The wrapper itself skips unless 3 days have passed since the last successful scheduled run:

```cron
0 10 * * * cd /home/kryz-wosik/dev/agents/hello-agent/music-gig-agent && /usr/bin/python3 scripts/run_every_three_days.py >> output/cron.log 2>&1
```

The scheduler writes its own log to:

```text
output/scheduled_pipeline.log
```

## Future Integration Points

- `collectors/spotify_collector.py`: add longer-term history loading from dated Spotify snapshots.
- `analysis/recommendation_scorer.py`: tune scoring weights and make live-history matching less keyword-based.
- `analysis/llm_gig_analyst.py`: tune personalized gig enrichment prompts after reviewing real outputs.
- `collectors/web_gig_collector.py`: improve source targeting and add venue-specific search strategies.
