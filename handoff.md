# Project handoff

## Current state

`mogi-tracker` is a working, self-hosted FastAPI/SQLite application for recording Mario Kart World lounge sessions and analyzing results. It uses SQLAlchemy Core and Alembic for persistence, Jinja/HTMX/Tailwind for the UI, and pandas/scipy for analytics. The app is intended for a single user on a trusted LAN and deliberately has no authentication.

The repository was clean at commit `4f45290` (`updates`) before this handoff file was added. The application schema migrates and the 30-course track table seeds automatically on startup.

## What is implemented

- Keyboard-oriented session entry with autosave, track typeahead/fuzzy matching, placement and optional race metadata, intermission handling, running averages, and format-specific race counts.
- Session list/detail views, incomplete/aborted status, delete confirmation links, settings, import preview/review, JSON/CSV exports, and analytics.
- Historical Lounge document extraction and parsing: 27 sessions, 281 races, 139 recorded placements, all 30 courses resolved, and four intentional review warnings.
- SQLite WAL storage, Alembic migrations, nightly in-process backups, Docker/TrueNAS deployment files, and GHCR publishing workflow.
- Analytics for track residuals, session trends/models, and gate shortcuts. Some outputs remain empty until enough corresponding data is entered.

## Latest changes

- Renamed Sky-High Sundae's canonical code from `SHS` to `rSHS`; migration `0003` updates existing databases and adds the `rshs` alias.
- Added a derived **MMR after** field to session entry. It displays `MMR before + MMR delta`; editing it recalculates and persists the delta rather than storing a third potentially inconsistent value.
- Added session deletion links to the session list.
- Matched the race-table Note header width to its row fields.
- Removed the parser warning for room minimum MMR values below 100.
- Refreshed historical parsed data and relevant documentation/tests for `rSHS` and the current import source.

## Verification

Run from the repository root:

```sh
.venv/bin/python -m pytest -q
```

Result on 2026-08-06: **102 passed** in 11.38 seconds, with one upstream Starlette deprecation warning about its TestClient/httpx compatibility import.

Note: `.venv/bin/pytest -q` failed here with `ModuleNotFoundError: app`, while invoking pytest through `.venv/bin/python -m pytest -q` passed. The README currently documents the former command and should either be corrected or the environment/import-path difference investigated.

Local development:

```sh
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Rebuild CSS only after changing templates or `app/static/src/input.css`:

```sh
tools/build_css.sh
```

## Important files

- `README.md` — setup, import, deployment, entry behavior, and known open work.
- `mogi-tracker-spec.md` — product decisions, schema rationale, analytics definitions, and track appendix.
- `app/main.py` — application startup and lifecycle.
- `app/schema.py` — SQLAlchemy Core schema.
- `app/importer.py` — pasted Lounge text parser.
- `app/matching.py` — track matching and ambiguity safeguards.
- `app/analytics.py` — statistical calculations.
- `app/routes/` — page, API, and export endpoints.
- `app/static/entry.js` — autosave and keyboard-entry behavior.
- `alembic/versions/` — migrations; latest revision is `0003`.
- `data/README.md` — historical data provenance and parser caveats.
- `tests/` — 102 passing tests using real Lounge excerpts where appropriate.

## Open decisions and follow-up

- Canonical track codes remain provisional, especially `rMC` versus `MC`, the inconsistent retro `r` prefix, and Rainbow Road's retro classification. Aliases make input tolerant, but canonical renames require deliberate data updates/migrations.
- `good_from_first` is unset for tracks, so the lead-defensibility analytic has no output yet.
- Historical `+1` annotations have no agreed meaning and remain preserved as race notes.
- Historical data has no `lap1_position`; execution-versus-survival analysis will only become useful as new sessions accumulate.
- The initial historical import is a manual UI step: paste `data/lounge-raw.txt` into **Import**, preview, then import. Do not assume it has already been loaded into a fresh database.
- Deployment is designed for TrueNAS SCALE via `docker-compose.yml`; make the GHCR image public and use a persistent dataset (the README recommends a 16K record size).

## Data cautions

- Early historical sessions recorded tracks without placements; incomplete status for those sessions is expected.
- Two historical races genuinely have no track, one PDF token was merged, and one session was abandoned. These produce the four expected importer review items and should not be "fixed" as parser failures.
- The historical document is not chronological; preserve timestamps rather than relying on source order.
