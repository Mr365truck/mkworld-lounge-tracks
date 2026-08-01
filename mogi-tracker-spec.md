# mogi-tracker — build spec

A self-hosted web app for logging Mario Kart World lounge sessions (mogis) race-by-race
and computing per-track and per-session statistics. Single user, LAN + Tailscale access,
deployed as a Docker container on TrueNAS SCALE.

Read this whole file before writing code. Section 3 (data model) and Section 5 (entry UX)
are the load-bearing parts; the rest is negotiable.

---

## 1. Why this exists

The data currently lives in a Google Doc as freeform nested lists, gets pasted into an LLM
for analysis, and has already produced three distinct classes of error:

- **Ambiguous track codes.** The same track written as `WS`, `WSS`, `whistlestop`;
  `SHS` / `sundae` / `SKS` for one track; `DKP` vs `pass` vs `dksp` for two different
  tracks that were conflated for weeks.
- **Silent duplicates.** One session listed the same track twice (`raf` at races 1 and 11)
  and it was miscounted as a single appearance, which shifted that track's sample size and
  its computed mean.
- **Missing values.** Several races have no placement recorded at all, and the gap is
  invisible until something tries to average the column.

Every design decision below is downstream of preventing those three. **Constrained entry
beats free text.** If a choice trades entry speed for data integrity, take integrity —
but see Section 5, because entry speed is also a hard requirement and the two are
reconcilable.

---

## 2. Stack

Ranked, with honest tradeoffs. Default to Option A unless there's a reason not to.

**A. FastAPI + SQLite + HTMX + Tailwind (single container) — recommended**
- One process, one file to back up, no build step, no node_modules. Pydantic gives schema
  validation for free. HTMX keeps the entry form snappy without a SPA.
- Analytics in Python means pandas/scipy are available for Section 6 without a second
  service or a JS stats library.
- Tradeoff: HTMX is less ergonomic than React if the UI grows complex. It won't here —
  this is three screens.

**B. SvelteKit + SQLite (better-sqlite3), single container**
- Best entry-form UX; keyboard handling and optimistic updates are natural in Svelte.
- Tradeoff: stats layer in TypeScript means reimplementing regression/correlation by hand
  or pulling a weaker library. That's the app's whole point, so this is a real cost.

**C. Go + SQLite + templ, single static binary**
- Smallest image (~20MB scratch), fastest cold start, zero runtime deps.
- Tradeoff: same stats problem as B, plus slower iteration. Only worth it if the container
  needs to be tiny, which it doesn't.

**Storage: SQLite, not Postgres.** Single writer, low volume (~300 rows/month), and a
single-file DB on a ZFS dataset means ZFS snapshots *are* the backup strategy. Postgres
adds a container, a volume, and a restore procedure for zero benefit at this scale.
Enable WAL mode. `synchronous=NORMAL` is fine on ZFS.

---

## 3. Data model

Reference tables come first — they're what kills the ambiguity problem.

### `tracks`
| col | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `code` | TEXT UNIQUE NOT NULL | canonical short code, e.g. `WS`, `rDKP` |
| `full_name` | TEXT NOT NULL | `Whistlestop Summit` |
| `is_retro` | BOOLEAN NOT NULL | `r` prefix convention |
| `has_gate` | BOOLEAN DEFAULT 0 | binary-gate track (shortcut hit/miss dominates outcome) |
| `gate_note` | TEXT NULL | e.g. `NISC`, `2x shroom cut` |
| `good_from_first` | BOOLEAN DEFAULT 0 | lead is defensible |
| `good_from_first_if_shrooms` | BOOLEAN DEFAULT 0 | |
| `active` | BOOLEAN DEFAULT 1 | for tracks cut from rotation |

### `track_aliases`
| col | type | notes |
|---|---|---|
| `track_id` | FK → tracks | |
| `alias` | TEXT UNIQUE NOT NULL | lowercased on write |

Seeded with every historical spelling: `wss`→WS, `whistlestop`→WS, `sundae`→SHS,
`sks`→SHS, `salty`→SSS, `stadium`→rWSt, `castle`→BC, `hills`→rDH, `acorn`→AH,
`airship`→rAF, `dandelion`→DD, `pass`→rDKP, `dkp`→rDKP, `mc`→rMC, etc. This table is what
makes the CSV/paste importer (Section 7) work on historical data.

### `sessions`
| col | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `played_at` | TIMESTAMP NOT NULL | date + time, local tz |
| `format` | ENUM | `ffa`, `2v2`, `3v3`, `4v4`, `6v6`, `tournament` |
| `room_min_mmr` | INTEGER NULL | |
| `room_max_mmr` | INTEGER NULL | |
| `room_avg_mmr` | INTEGER NULL | |
| `seat` | INTEGER NULL | 1–12 |
| `mate_mmr` | INTEGER NULL | teams only |
| `own_mmr_before` | INTEGER NULL | |
| `mmr_delta` | INTEGER NULL | |
| `score` | INTEGER NULL | total points |
| `notes` | TEXT NULL | |
| `is_complete` | BOOLEAN | computed: all races have a placement |

`room_avg_mmr` should be **derived-but-overridable**: default to the seat-weighted average
if individual MMRs are ever entered, otherwise store what's typed.

### `races`
| col | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `session_id` | FK → sessions | |
| `race_num` | INTEGER NOT NULL | 1-indexed |
| `track_id` | FK → tracks NOT NULL | |
| `placement` | INTEGER NULL | 1–12, NULL = not recorded |
| `start_position` | INTEGER NULL | grid position |
| `lap1_position` | INTEGER NULL | **the highest-value new field — see §6** |
| `shortcut_hit` | BOOLEAN NULL | tri-state: hit / miss / not-applicable |
| `mate_placement` | INTEGER NULL | teams only |
| `note` | TEXT NULL | free text, e.g. execution errors |

`UNIQUE(session_id, race_num)`. Explicitly **no** unique constraint on
`(session_id, track_id)` — the same track legitimately appears twice in one session, and
that's exactly the case that was previously miscounted.

### Derived, not stored
Session average placement, per-track residuals, and all regressions are computed at read
time. Volume is tiny; caching them invites staleness bugs.

---

## 4. Screens

Three, plus a settings page.

1. **New session / edit session** — the primary screen, optimized hard for speed (§5).
2. **Session list** — reverse-chronological table: date, format, room avg/max, seat, avg
   placement, score, completeness indicator. Click to edit. Incomplete sessions flagged
   visually so missing placements can't hide.
3. **Analytics** — §6.
4. **Settings** — track table CRUD, alias management, CSV import/export.

---

## 5. Entry UX — the make-or-break requirement

If entry is slower than typing into a Google Doc, the app is abandoned. Target: **a full
12-race session logged in under 90 seconds, keyboard-only, no mouse.**

Design:

- One row per race, all 12 rendered at once. No wizard, no per-race save button.
- **Track field is a typeahead over codes + aliases + full names.** Type `wh` → Whistlestop
  Summit. Type an unknown string → inline "add as alias for…" prompt rather than a
  rejection. Never accept an unmapped free-text track.
- **Tab moves across fields, Enter moves to the next race.** Numeric fields accept bare
  digits. This is the single most important interaction detail in the app.
- Autosave on blur (HTMX `hx-post` per field, or debounced batch). Never lose a partial
  session to a closed tab.
- `shortcut_hit` only renders for tracks where `has_gate = 1`. It should be a single
  keypress (`y`/`n`/blank). Do not show it on the other 25 tracks — that's how it stays
  cheap enough to actually get logged.
- `lap1_position` renders for every race but is always optional.
- Running session average placement displayed live in the header as rows fill.
- Session-level MMR fields at the top, entered once.

Mobile: the entry form must work one-handed on a phone in portrait. Numeric inputs get
`inputmode="numeric"`. This is likely how most sessions actually get logged, right after
playing.

---

## 6. Analytics

Implement these specifically. They're the queries the Google Doc couldn't answer.

**Per-track table** — the core view:
- pick count, pick rate (% of sessions in which the track appeared at least once)
- n placements, mean placement, SD
- **residual** = mean of (placement − that session's own average placement). Negative =
  outperformed own form. This is the headline stat; raw mean conflates track difficulty
  with the difficulty of the sessions the track happened to appear in.
- frequency-weighted residual = residual × pick rate → expected places lost per session.
  This is what ranks improvement targets correctly; a −0.5 residual on a 96%-pick track
  matters more than a −1.5 residual on a 20% one.
- 95% CI on the residual (`t` interval), and **grey out or flag any row with n < 5**.
  Under-sampled tracks reading as signal is a live failure mode.

**Session model:**
- OLS of session average placement on `room_max_mmr`, `room_avg_mmr`, `seat`, and
  `(max − min)` spread. Report r and R² per predictor plus a multivariate fit.
- Predicted vs actual per session, with residuals — surfaces genuinely good/bad sessions
  as distinct from favourable/unfavourable lobbies.

**Gate analysis** (tracks where `has_gate = 1`):
- Two-component split on `shortcut_hit`: p(hit), mean placement given hit, mean given
  miss, and implied E[placement].
- Once `lap1_position` has data, decompose p(hit) into execution vs survival — i.e. how
  often the shortcut is reached in contention at all versus how often it's converted once
  reached. This is the thing the current dataset structurally cannot answer and the main
  reason the app is worth building.

**Trend:**
- Rolling mean residual per track over time, to detect practice effects. A track can
  improve substantially and still show a bad lifetime mean; only the time series shows it.
- MMR over time from `own_mmr_before` + `mmr_delta`.

Expose all of it as JSON endpoints alongside the HTML views, so a future script (or an
LLM given API access) can pull structured data instead of parsing prose.

---

## 7. Import / export

- **CSV export** of `races` joined to `sessions` and `tracks`, one row per race. Flat and
  wide. This is the format to hand to an analysis tool.
- **JSON export** of the whole DB, for archival.
- **Paste importer** — accepts the existing Google Doc format (session header line with
  `date, time, format, min/max/avg mmr, seat`, then a numbered track list with optional
  nested placement lines). Resolve track names through `track_aliases`; anything
  unresolved goes to a review queue rather than failing the whole import or silently
  dropping. Roughly 24 historical sessions need to come in this way, and getting them in
  is what makes the analytics non-trivial on day one.

---

## 8. Deployment (TrueNAS SCALE)

Target: Custom App via the Apps UI, or `docker compose` under a dataset if the
docker-compose path is already in use on this system.

- Multi-stage `Dockerfile`, non-root user. **Run as UID/GID 568:568** (`apps` on TrueNAS
  SCALE) so the bind-mounted dataset permissions work without ACL surgery.
- Single volume: `/data` → a dedicated ZFS dataset (e.g. `tank/apps/mogi-tracker`),
  containing `mogi.db` and any exports. Set `recordsize=16K` on that dataset for SQLite;
  the 128K default causes write amplification on small random writes.
- One port, HTTP only. TLS and access control are handled upstream — Tailscale is already
  in use here, so bind to the host and reach it over the tailnet rather than exposing it.
- No auth in v1. Single user behind Tailscale. Structure the code so a middleware can be
  dropped in later, but don't build a login screen now.
- `docker-compose.yml` with `restart: unless-stopped` and a `/healthz` endpoint.
- ZFS snapshots on the dataset are the backup. Additionally: nightly `VACUUM INTO` to a
  timestamped file in `/data/backups`, keep 30. A snapshot of a live SQLite file is
  usually recoverable given WAL, but a checkpointed copy is unambiguously safe.

---

## 9. Build order

1. Schema + migrations + the seeded track/alias tables. Get the reference data right first;
   everything else depends on it.
2. Paste importer + the 24 historical sessions. Do this **before** the entry UI — it
   surfaces every edge case in the schema (duplicate tracks, missing placements, aborted
   sessions, tournament rooms with 8 races instead of 12) while the schema is still cheap
   to change.
3. Session entry UI with the keyboard flow.
4. Session list.
5. Per-track analytics table.
6. Regression + gate analysis.
7. Docker + compose + deploy.
8. CSV/JSON export.

Ship 1–4 before touching analytics. An app that logs reliably and computes nothing is
useful; one that computes elegantly over data nobody enters is not.

---

## 10. Edge cases to handle explicitly

These are all present in the historical data:

- A session with fewer than 12 races (tournament rooms have 8).
- An **aborted session** — one race played, then abandoned, then a fresh session started.
  One historical entry has exactly this shape (`faraway`, start position 1, no placement,
  then a full 12-race session immediately after).
- The same track twice in one session.
- Races with a track but no placement.
- Sessions with no MMR data at all (the earliest entries).
- 2v2 sessions with a mate placement per race and a `mate_mmr` on the session.
- A session where `room_min_mmr` is implausible (one historical entry reads `min: 7`) —
  warn on entry, don't reject.
