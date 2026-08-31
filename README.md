# mogi-tracker

Self-hosted tracker for Mario Kart World lounge sessions. Logs mogis race-by-race and
computes per-track and per-session statistics that the Google Doc it replaces could
not answer.

Built to `mogi-tracker-spec.md` — read that for the reasoning; this file is how to run it.

```
FastAPI · SQLite (WAL) · SQLAlchemy Core + Alembic · HTMX · Tailwind · pandas/scipy
```

## Run it locally

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then open <http://localhost:8000>. The schema migrates and the 30-course track table
seeds themselves on first start — there is nothing to run by hand.

```sh
.venv/bin/pytest -q            # run the full suite
tools/build_css.sh             # only after editing templates or input.css
```

## Load the history

`data/lounge-raw.txt` is the text recovered from `Lounge.pdf`. Paste it into
**Import**, hit **Preview**, then **Import**:

| | |
|---|---|
| 27 sessions | 25 with races |
| 281 races | 139 with a placement |
| 30 courses | resolved from 45 distinct spellings, 0 unresolved |

Four parser notes land in the review queue under **Settings**. All four are real data
conditions — two trackless races, one merged PDF token, one abandoned session — not
parse failures.

Placements only start at session 15 (7/21); the first 14 sessions logged the track
list alone. So the residual analytics run on ~139 placements across 11 sessions until
the backlog grows.

## Deploy to TrueNAS SCALE

No SSH required.

1. Push to `main`. `.github/workflows/publish.yml` builds the image and pushes it to
   GHCR.
2. Make the GHCR package **public** once (Packages → Package settings → Change
   visibility). The image holds no secrets, and a private one would need registry
   credentials the paste-YAML flow has nowhere to put.
3. Create a dataset for the database, e.g. `tank/apps/mogi-tracker`, and set its
   **Record Size to 16K** — the 128K default causes write amplification on SQLite's
   small random writes. Both are settable in the TrueNAS UI.
4. Apps → Discover → Custom App → **Install via YAML**, paste `docker-compose.yml`,
   adjust `TZ` and the volume path.

### Environment

| var | default | purpose |
|---|---|---|
| `TZ` | `UTC` | render timezone for `played_at`; storage is always UTC |
| `DATABASE_PATH` | `/data/mogi.db` | |
| `PORT` | `8000` | |
| `BACKUP_DIR` | `/data/backups` | nightly `VACUUM INTO` target |
| `BACKUP_KEEP` | `30` | backups retained |
| `BACKUP_HOUR` | `4` | hour of day, in `TZ` |
| `BACKUP_ENABLED` | `1` | set `0` to disable database backups |
| `IMPORT_DEFAULT_YEAR` | `2026` | year for the importer's bare `5/26`-style dates |
| `LOUNGE_GAME` | `mkworld12p` | MKCentral leaderboard used for player search |
| `LOUNGE_BASE_URL` | `https://lounge.mkcentral.com` | MKCentral Lounge service root |
| `LOUNGE_HTTP_TIMEOUT` | `10` | seconds before a Lounge request fails |
| `LOUNGE_PLAYER_ID` | `67656` | stable MKCentral player ID used for Current MMR |
| `LOUNGE_MMR_REFRESH_HOURS` | `24` | hours between current-MMR leaderboard checks |
| `LOUNGE_NAME_REFRESH_DAYS` | `7` | minimum age before a saved Lounge name is checked again |
| `LOUNGE_REFRESH_HOUR` | `5` | local hour for the daily stale-name check |
| `LOUNGE_REFRESH_ENABLED` | `1` | set `0` to disable automatic Lounge data refreshes |

### Backups

ZFS snapshots on the dataset are the backup. On top of that, a nightly `VACUUM INTO`
writes a checkpointed copy to `/data/backups` and keeps the newest 30. It runs
**in-process** via APScheduler, not host cron — there is no SSH.

## Entry, briefly

The landing page caches Current MMR from MKCentral every 24 hours. If a session's
MMR fields are edited after that refresh, the manually entered post-session MMR is
shown until the next successful MKCentral update. A failed request retains the last
good cached value.

The entry screen is the one that decides whether this gets used. Target: a full
12-race session, keyboard only, no mouse.

- **Type a track code.** An unambiguous exact code (`raf`, `rshs`, `ah`) commits itself
  and hands focus straight to the placement box.
- **Ambiguous ones wait for Enter.** `bc` is an exact hit, but `bci` (Boo Cinema)
  starts with it, so BC highlights and Enter confirms. Same for `ws`/`wss` and
  `dd`/`ddj`. One extra keystroke, exactly where the dataset has been corrupted before.
- **Fuzzy works too.** `whis`, `wsum`, and `cinema` all find what you mean. Free text
  is never accepted as a track; an unknown string offers `Alt+↵` to add it as an alias.
- **Enter moves to the next race. Tab moves across fields.**
- The shortcut field renders on gate tracks only — BC, GBR, WS, AH.
- `Alt+I` marks a race as an intermission. It never costs a tab stop.

## Shock maps

The **Shocks** tab shows all 30 standard minimaps. Click a
location, choose lap 1–3, and the normalized point is stored in SQLite. The page can
filter every heatmap to one lap, and each card can undo its most recent entry.

Shock data lives in its own `shock_events` table and is included in the full JSON
database export. Updating the container runs the additive Alembic migration without
replacing the existing session or race tables.

## Do Not Mogi

The **Do Not Mogi** tab is a personal list of Lounge players to avoid. Its search is
backed by MKCentral's current MKWorld 12P leaderboard. Entries are stored by stable
Lounge player ID rather than display name, and a scheduled stale-name check refreshes
each entry about once a week. A manual **Refresh names** action is also available.

Only the fields needed for identity are saved: Lounge player ID, display name, and
country. Leaderboard rank and MMR are shown only while searching. The upstream player
response also contains account fields that this app deliberately discards.

## No auth

Single user on a trusted LAN, which means anything on that network can read and write
the database. That is a deliberate call at this scale, recorded plainly so nobody
later inherits "it's behind Tailscale" as a security assumption. The routes are
grouped so a middleware can drop in without touching them.

## Layout

```
app/
  schema.py      SQLAlchemy Core tables
  seed_data.py   Appendix A — 30 courses, every observed spelling
  matching.py    typeahead ranking (the BC/BCi and SP/PS guard rails)
  importer.py    Google Doc paste parser
  analytics.py   leave-one-out residuals, session model, gate split
  shocks.py      canonical 30-map asset manifest
  routes/        pages · api · exports
alembic/         versioned migrations
tools/           PDF extraction, CSS build
data/            recovered history + regeneration instructions
tests/           route, schema, safety, analytics, and real importer fixtures
```

## Still open

- **Track codes are provisional.** `rMC` vs `MC`, the inconsistent `r` prefix, and
  Rainbow Road's retro flag are unresolved (spec Appendix A). Aliases are rows, not
  schema, so renaming a canonical code is a data edit — not a migration.
- **`good_from_first` flags are unset.** The lead-defensibility analytic stays empty
  until they are set in Settings.
- **`+1`** appears as a sub-item on a few historical races with no defined meaning.
  It is kept verbatim in the race note rather than guessed at.
- **`lap1_position` has no historical data**, so the execution-vs-survival split of
  p(hit) starts accumulating from the next session logged here.
