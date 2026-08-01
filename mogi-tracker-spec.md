# mogi-tracker — build spec

A self-hosted web app for logging Mario Kart World lounge sessions (mogis) race-by-race
and computing per-track and per-session statistics. Single user, LAN access (Tailscale for
the occasional off-network session), deployed as a Docker container on TrueNAS SCALE.

Read this whole file before writing code. Section 3 (data model) and Section 5 (entry UX)
are the load-bearing parts; the rest is negotiable.

---

## 1. Why this exists

The data currently lives in a Google Doc as freeform nested lists (archived as `Lounge.pdf`
in this repo), gets pasted into an LLM for analysis, and has already produced three distinct
classes of error:

- **Ambiguous track codes.** The same track written as `WS`, `WSS`, `whistlestop`;
  `SHS` / `sundae` / `SKS` for one track; `DKP` vs `pass` vs `dksp` for two different
  tracks that were conflated for weeks.
- **Silent duplicates.** One session listed the same track twice (`raf` at races 1 and 11)
  and it was miscounted as a single appearance, which shifted that track's sample size and
  its computed mean.
- **Missing values.** Several races have no placement recorded at all, and the gap is
  invisible until something tries to average the column.

The scale of the first problem is now measured rather than estimated: the historical doc
uses **45 distinct spellings for 30 tracks** — 92 if you count case variants like `pASS`,
`BAZAAR`, and `maX` (see Appendix A). Two of those spellings are a two-character
transposition of each other and refer to different tracks — `SP` is Starview Peak, `PS` is
Peach Stadium.

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

**Data access: SQLAlchemy Core + Alembic.** Table definitions in Python, real versioned
migration history, no ORM session or lazy-loading complexity for what is four tables.
`pandas.read_sql` reads straight off the connection for Section 6; Pydantic stays at the
request edge where it belongs.

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

Seeded with every historical spelling. The full observed set — all 92 of them, extracted
from `Lounge.pdf` — is in **Appendix A**. This table is what makes the CSV/paste importer
(Section 7) work on historical data.

### `sessions`
| col | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `played_at` | TIMESTAMP NOT NULL | stored UTC, rendered via `TZ` (§8) |
| `format` | ENUM | `ffa`, `2v2`, `3v3`, `4v4`, `6v6`, `tournament` |
| `expected_races` | INTEGER NOT NULL | defaults from format: 12, or 8 for `tournament`. Overridable at entry |
| `aborted` | BOOLEAN DEFAULT 0 | session abandoned; suppresses the incomplete flag |
| `room_min_mmr` | INTEGER NULL | |
| `room_max_mmr` | INTEGER NULL | |
| `room_avg_mmr` | INTEGER NULL | |
| `seat` | INTEGER NULL | 1–12 |
| `mate_mmr` | INTEGER NULL | teams only |
| `own_mmr_before` | INTEGER NULL | |
| `mmr_delta` | INTEGER NULL | |
| `score` | INTEGER NULL | total points |
| `notes` | TEXT NULL | |
| `created_at` | TIMESTAMP | |
| `updated_at` | TIMESTAMP | |

`room_avg_mmr` should be **derived-but-overridable**: default to the seat-weighted average
if individual MMRs are ever entered, otherwise store what's typed.

`created_at` is deliberately distinct from `played_at` — Section 6's trend analysis needs to
know when a session was *played*, and the importer needs to know when a row was *entered*.

### `races`
| col | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `session_id` | FK → sessions | |
| `race_num` | INTEGER NOT NULL | 1-indexed |
| `track_id` | FK → tracks NULL | NULL = race logged with no track recorded (see §10) |
| `variant` | TEXT NOT NULL DEFAULT `'3lap'` | CHECK IN (`'3lap'`, `'intermission'`) |
| `placement` | INTEGER NULL | 1–12, NULL = not recorded |
| `start_position` | INTEGER NULL | grid position |
| `lap1_position` | INTEGER NULL | **the highest-value new field — see §6** |
| `shortcut_hit` | TEXT NULL | CHECK IN (`'hit'`, `'miss'`, `'na'`); NULL = not recorded |
| `mate_placement` | INTEGER NULL | teams only |
| `note` | TEXT NULL | free text, e.g. execution errors |
| `created_at` | TIMESTAMP | |
| `updated_at` | TIMESTAMP | |

`UNIQUE(session_id, race_num)`. Explicitly **no** unique constraint on
`(session_id, track_id)` — the same track legitimately appears twice in one session, and
that's exactly the case that was previously miscounted.

**On `shortcut_hit` being four-state, not three.** A nullable boolean cannot distinguish
"I didn't log it" from "the gate wasn't in play this race," and collapsing those is failure
mode three from §1 wearing a different hat. `NULL` means not recorded; `'na'` means not
applicable.

**On `variant`.** Intermissions are banned in this lounge and only occasionally slip
through — the historical doc contains exactly one, written inline as
`1. raf (intermission to)`. `'3lap'` is therefore the overwhelming default, and the column
exists so that the rare exception can be excluded from a track's statistics rather than
silently contaminating them. The destination track goes in `track_id`; the route's origin
is not modelled, because at n=1 it would buy nothing.

**On 12-player rooms.** The 1–12 bounds on `placement`, `seat`, and `mate_placement` assume
12-player lobbies, which is what this lounge runs. Supporting 24-player rooms later would
need a `lobby_size` column *and* normalization of residuals to a common scale — raw
placements from differently-sized rooms are not poolable, since the neutral baseline moves
from 6.5 to 12.5. Noting it here so a future migration isn't discovered the hard way.

### Derived, not stored
Session average placement, per-track residuals, `is_complete`, and all regressions are
computed at read time. Volume is tiny; caching them invites staleness bugs.

```
is_complete = aborted OR (count(races WHERE placement IS NOT NULL) == expected_races)
```

The `aborted` flag is what stops the §10 abandoned session — one race, no placement — from
reading as permanently incomplete, and `expected_races` is what makes an 8-race tournament
room evaluable at all.

---

## 4. Screens

Four.

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

- One row per race, `expected_races` rows rendered at once. No wizard, no per-race save
  button. Rows can be added or removed for the sessions that don't fit the default.
- **Tab moves across fields, Enter moves to the next race.** Numeric fields accept bare
  digits. This is the single most important interaction detail in the app.
- Autosave on blur (HTMX `hx-post` per field, or debounced batch). Never lose a partial
  session to a closed tab.
- `shortcut_hit` only renders for tracks where `has_gate = 1` — currently four of thirty
  (§ Appendix A). It should be a single keypress (`y`/`n`/blank). Do not show it on the
  other 26 tracks; that's how it stays cheap enough to actually get logged.
- `lap1_position` renders for every race but is always optional.
- Running session average placement displayed live in the header as rows fill.
- Session-level MMR fields at the top, entered once, alongside `expected_races` and an
  **aborted** toggle. Aborted is the one control that resolves a session the app would
  otherwise flag as incomplete forever (§3), so it belongs next to the completeness
  indicator rather than buried in settings.

### Track field

A typeahead over codes, aliases, and full names. Never accept an unmapped free-text track.

- **Fuzzy subsequence matching across all three at once.** `bci` finds Boo Cinema, `whis`
  or `wsum` finds Whistlestop Summit, `cinema` finds it by name. Both codes and full names
  work without every mental shorthand needing to pre-exist as an alias row.
- **Exact code and alias hits always rank first.** This is what keeps `BC` resolving to
  Bowser's Castle and never to Boo Cinema (`BCi`), and `SP` to Starview Peak rather than
  Peach Stadium (`PS`).
- **Auto-commit only on an exact code or alias match.** Fuzzy and partial matches highlight
  but wait for Enter. Full speed on the codes typed confidently, confirmation on anything
  ambiguous — the right trade given §1's premise is that misidentified tracks have already
  corrupted this dataset once.
- An unknown string offers an inline "add as alias for…" prompt rather than a rejection.

### Variant field

- Rendered at the **right edge of each race row, outside the tab order** (`tabindex="-1"`).
  Reachable by click/tap or a dedicated modifier hotkey, never by Tab. It must not cost a
  tab stop on any of the ~99% of races that are ordinary 3-lap races.
- Defaults to `3lap` with no visual weight. When set to `intermission` the row gets a
  persistent marker, so the exception stays visible rather than hiding — same reasoning as
  the incomplete-session flag in §4.

### Surfaces

Entry splits roughly **75/25 desktop/mobile**. The desktop keyboard flow above is the thing
to optimize; the 90-second target is a desktop target.

Mobile still matters at a quarter of sessions: the entry form must work one-handed on a
phone in portrait, and numeric inputs get `inputmode="numeric"`.

**Write durability.** Entry is ~99% on the LAN, where per-field `hx-post` does not
meaningfully drop, so a full offline queue would be over-engineering. Specify instead: a
visible per-row unsaved indicator, and a `beforeunload` guard while any field write is in
flight. That satisfies "never lose a partial session to a closed tab" at a fraction of the
complexity, and a localStorage retry queue can be added later if remote entry grows.

---

## 6. Analytics

Implement these specifically. They're the queries the Google Doc couldn't answer.

Unless stated otherwise, every per-track statistic filters to `variant = '3lap'`.

**Per-track table** — the core view:
- pick count, pick rate (% of sessions in which the track appeared at least once)
- n placements, mean placement, SD
- **residual** = mean of (placement − the mean placement of *the other races in that
  session*). Negative = outperformed own form. This is the headline stat; raw mean
  conflates track difficulty with the difficulty of the sessions the track happened to
  appear in.

  The baseline is **leave-one-out** — it excludes the race being measured. Including it
  biases every residual toward zero by roughly `1 − 1/n_races`, and unevenly: more in an
  8-race tournament room than a 12-race session, and more again for a track appearing twice
  in one session, which is precisely the case §1 says was already miscounted once.
- frequency-weighted residual = residual × pick rate → expected places lost per session.
  This is what ranks improvement targets correctly; a −0.5 residual on a 96%-pick track
  matters more than a −1.5 residual on a 20% one.
- 95% CI on the residual (`t` interval), and **grey out or flag any row with n < 5**.
  Under-sampled tracks reading as signal is a live failure mode.

**Intermissions.** Excluded from the per-track table by default, shown in their own small
table so they aren't invisible, with a toggle to fold them in. Gate analysis is 3-lap only,
since the shortcut may not even be on the route.

**Session model:**
- OLS of session average placement on `room_max_mmr`, `room_avg_mmr`, `seat`, and
  `(max − min)` spread. Report r and R² per predictor plus a multivariate fit.
- Predicted vs actual per session, with residuals — surfaces genuinely good/bad sessions
  as distinct from favourable/unfavourable lobbies.
- **Report n alongside every R², and flag the multivariate fit as unreliable below ~20
  sessions.** Four predictors over ~24 sessions is badly underpowered and will otherwise
  produce confident-looking coefficients. Prefer the univariate fits until the sample
  supports more. This is the same discipline as the n < 5 rule above, applied to the place
  it was missing.

**Gate analysis** (tracks where `has_gate = 1` — Bowser's Castle, Great ? Block Ruins,
Whistlestop Summit, Acorn Heights):
- Two-component split on `shortcut_hit`: p(hit), mean placement given hit, mean given
  miss, and implied E[placement].
- Once `lap1_position` has data, decompose p(hit) into execution vs survival — i.e. how
  often the shortcut is reached in contention at all versus how often it's converted once
  reached. This is the thing the current dataset structurally cannot answer and the main
  reason the app is worth building.

**Lead defensibility** (uses `good_from_first` / `good_from_first_if_shrooms`):
- Mean placement and residual conditioned on `start_position = 1`, split by the flag. Does
  the lead actually hold on the tracks believed to be defensible? These two columns are
  stored reference data; this is the analytic that makes them worth storing.

**Not yet analysed:** `races.mate_placement` is written by team sessions (§5, §7) but no
statistic above reads it. The obvious candidate — mean combined team placement per track,
and whether your residual correlates with your mate's — is deferred rather than forgotten;
there are only two 2v2 sessions in the historical data, well under the n < 5 bar. Revisit
once team formats accumulate.

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

Importer defaults: `variant` to `'3lap'` unless the line carries an inline intermission
marker, and `shortcut_hit` to `NULL` — never `'na'` — since the historical doc distinguishes
neither. Every §10 edge case is present in `Lounge.pdf` and should become a test fixture.

---

## 8. Deployment (TrueNAS SCALE)

Target: Custom App via the Apps UI, installed by pasting compose YAML into the web UI. **No
SSH.** That constraint drives most of what follows.

- **CI builds the image; the NAS pulls it.** A GitHub Actions workflow builds the multi-stage
  image and pushes to GHCR on push; the Custom App YAML references the resulting tag.
  TrueNAS's Install-via-YAML screen is built around pulling — it exposes Image Repository,
  Image Tag, and Image Pull Policy — so a `build:` directive in pasted YAML is not the
  supported path. The workflow is what makes the paste-YAML flow work at all.
- **Publish the GHCR package public.** The image carries no secrets, and a private package
  would need registry credentials on the NAS, which the paste-YAML flow has nowhere clean to
  put.
- Multi-stage `Dockerfile`, non-root user. **Run as UID/GID 568:568** (`apps` on TrueNAS
  SCALE) so the bind-mounted dataset permissions work without ACL surgery.
- Single volume: `/data` → a dedicated ZFS dataset (e.g. `tank/apps/mogi-tracker`),
  containing `mogi.db` and any exports. Set `recordsize=16K` on that dataset for SQLite;
  the 128K default causes write amplification on small random writes. Dataset creation and
  `recordsize` are both settable in the TrueNAS UI, so the no-SSH constraint holds end to end.
- One port, HTTP only. Bind to the host on the LAN, which is ~99% of access; Tailscale is
  available for the occasional off-network session.
- **No auth in v1.** Single user on a trusted LAN — which means anything on that network can
  read and write the DB. That's the accepted posture at this scale, stated plainly so a
  future reader doesn't inherit "it's behind Tailscale" as a security assumption. Structure
  the code so a middleware can be dropped in later, but don't build a login screen now.
- `docker-compose.yml` with `restart: unless-stopped` and a `/healthz` endpoint.
- ZFS snapshots on the dataset are the backup. Additionally: nightly `VACUUM INTO` to a
  timestamped file in `/data/backups`, keep 30. A snapshot of a live SQLite file is
  usually recoverable given WAL, but a checkpointed copy is unambiguously safe. **This runs
  in-process** (APScheduler or a background task), not as a host cron — there is no SSH.

Environment:

| var | default | purpose |
|---|---|---|
| `TZ` | `UTC` | render timezone for `played_at`; set to local |
| `DATABASE_PATH` | `/data/mogi.db` | |
| `PORT` | `8000` | |

---

## 9. Build order

1. Schema + Alembic migrations + the seeded track/alias tables (Appendix A). Get the
   reference data right first; everything else depends on it.
2. Paste importer + the 24 historical sessions. Do this **before** the entry UI — it
   surfaces every edge case in the schema (duplicate tracks, missing placements, aborted
   sessions, tournament rooms with 8 races instead of 12) while the schema is still cheap
   to change.
3. Session entry UI with the keyboard flow.
4. Session list.
5. Per-track analytics table.
6. Regression + gate analysis.
7. Docker + compose + GitHub Actions + deploy.
8. CSV/JSON export.

Ship 1–4 before touching analytics. An app that logs reliably and computes nothing is
useful; one that computes elegantly over data nobody enters is not.

**Testing: pytest, from step 1.** The §10 edge cases become importer fixtures — they are all
present in `Lounge.pdf`, so they can be real fixtures rather than invented ones — and the §6
statistics get golden-number tests. The leave-one-out residual correction in §6 is exactly
the class of error that ships silently without them.

---

## 10. Edge cases to handle explicitly

These are all present in the historical data (`Lounge.pdf`), with the doc location noted
where it helps.

**Session shape**
- A session with fewer than 12 races (tournament rooms have 8).
- One event containing **two sessions** — the 1yr anniversary tourney logs "Room 1" and
  "Room 2" under a single header, 8 races each.
- An **aborted session** — one race played, then abandoned, then a fresh session started.
  The 7/30 12-noon entry has exactly this shape (`faraway`, start position 1, no placement,
  then a full 12-race session immediately after).
- A session with a header but **no races at all** (`2v2 12p, 7/8, 7:00pm`), and a
  spectated one (`viewer 6v6, 6/12 @ 7:00pm`).
- Sessions **out of chronological order** in the source doc — 7/16 is logged before 7/8.
  The importer cannot assume order.

**Header parsing**
- Sessions with no MMR data at all (the earliest entries).
- A session where `room_min_mmr` is implausible (one historical entry reads `min: 7`) —
  warn on entry, don't reject.
- MMR with a thousands separator: `avg: 4,085`.
- Missing date (`12 noon, ffa, min: 2966…` inherits its date from the block above) and
  missing time (`FFA 7/23, min: 3634…`).
- Inconsistent case and punctuation: `maX:`, `pASS`, `seat 5` without a colon, `12 NOON`,
  `1PM`, `10am`.

**Race lines**
- The same track twice in one session.
- Races with a track but no placement.
- A **numbered race with no track at all** — `12.` appears bare in the 7/6 and 7/17 sessions.
  This is why `races.track_id` is nullable.
- Inline parentheticals on the track line: `10. hills (why)`,
  `1. raf (intermission to)` — the latter is the sole `variant = 'intermission'` race.
- Sub-item ordering varies: sometimes `a.` is the placement, sometimes `a. start: 8th` with
  the placement at `b.`. Sometimes `a. start: ?`. Sometimes `a.` is empty.
- Extra sub-items carrying notes: per-lap commentary
  (`b. l1: no shrooms / c. l2: draft on cut / d. l3: failed the cut in 4?`), free text
  (`b. somehow`), and an undefined `+1` marker whose meaning needs a ruling before import.

**Team sessions**
- 2v2 sessions with a mate placement per race and a `mate_mmr` on the session (`mate: 5435`).
- The mate placement's sub-item letter varies with whether a start position was recorded.

**Results in prose**
- Session outcomes written as sentences rather than fields: `Tbh got really unlucky, scored
  88, +46 mmr`, `scored 8th`, `placed 8th/12, eliminated`. Parse what's cleanly extractable
  (score, mmr delta) and keep the rest in `sessions.notes`.

---

## Appendix A — track and alias seed data

**Status: codes are provisional and need a ruling — see the open questions below.** The
`full_name` and alias columns are extracted from `Lounge.pdf` and are complete: all 30
MKWorld courses appear in the historical data, under 45 distinct spellings (92 counting
case variants, which the lowercase-on-write rule in §3 collapses). Two aliases below —
`wss` and `sks` — come from §1 rather than the doc; the table is a superset.

`has_gate` is taken from the note on the last page of `Lounge.pdf` — *"shortcut flag on bc,
gbr, ws, ah"*.

| code | full_name | is_retro | has_gate | aliases observed (case-insensitive) |
|---|---|---|---|---|
| `MBC` | Mario Bros. Circuit | 0 | 0 | `mbc` |
| `CC` | Crown City | 0 | 0 | `cc`, `crown city` |
| `WS` | Whistlestop Summit | 0 | **1** | `ws`, `wss`, `whistlestop` |
| `DKSP` | DK Spaceport | 0 | 0 | `dksp` |
| `SP` | Starview Peak | 0 | 0 | `sp`, `starview` |
| `FO` | Faraway Oasis | 0 | 0 | `faraway`, `fo` |
| `PS` | Peach Stadium | 0 | 0 | `ps` |
| `SSS` | Salty Salty Speedway | 0 | 0 | `sss`, `salty` |
| `GBR` | Great ? Block Ruins | 0 | **1** | `gbr` |
| `CCF` | Cheep Cheep Falls | 0 | 0 | `ccf` |
| `DD` | Dandelion Depths | 0 | 0 | `dd`, `dandelion` |
| `BCi` | Boo Cinema | 0 | 0 | `bci` |
| `DBB` | Dry Bones Burnout | 0 | 0 | `dbb` |
| `BC` | Bowser's Castle | 0 | **1** | `bc`, `castle` |
| `AH` | Acorn Heights | 0 | **1** | `ah`, `acorn` |
| `rMC` | Mario Circuit | ? | 0 | `rmc`, `mc` |
| `rDH` | Desert Hills | 1 | 0 | `rdh`, `hills`, `desert hills` |
| `rSGB` | Shy Guy Bazaar | 1 | 0 | `bazaar`, `sgb` |
| `rWSt` | Wario Stadium | 1 | 0 | `stadium`, `rwst` |
| `rAF` | Airship Fortress | 1 | 0 | `raf`, `af`, `airship` |
| `rDKP` | DK Pass | 1 | 0 | `rdkp`, `dkp`, `pass` |
| `SHS` | Sky-High Sundae | 1 | 0 | `shs`, `sks`, `sundae` |
| `rWSh` | Wario Shipyard | 1 | 0 | `shipyard`, `rwsh` |
| `rKTB` | Koopa Troopa Beach | 1 | 0 | `ktb`, `rktb` |
| `rPB` | Peach Beach | 1 | 0 | `pb`, `peach beach` |
| `rDDJ` | Dino Dino Jungle | 1 | 0 | `rddj`, `ddj` |
| `rMMM` | Moo Moo Meadows | 1 | 0 | `mmm`, `rmmm` |
| `rCM` | Choco Mountain | 1 | 0 | `choco`, `rcm`, `cm` |
| `rTF` | Toad's Factory | 1 | 0 | `rtf`, `tf` |
| `RR` | Rainbow Road | ? | 0 | `rr` |

`good_from_first` and `good_from_first_if_shrooms` are **TODO** — judgment calls that only
you can make, and unlike `has_gate` there's no note in the doc to take them from.

### Open questions on codes

1. **`rMC` vs `MC`.** Both appear in the doc for what is presumably Mario Circuit. Nintendo
   lists Mario Circuit as a *new* course in MKWorld, so the `r` prefix contradicts the
   convention in §3. Either the prefix is wrong or the convention is looser than "retro."
   Both spellings are aliased to one track above — confirm that's actually one track and not
   a second DKP/DKSP-style conflation.
2. **The `r` prefix is inconsistent in the source data.** Sky-High Sundae, Wario Shipyard,
   Shy Guy Bazaar, Choco Mountain, and Peach Beach are all returning courses that were
   logged *without* the prefix, while Mario Circuit was logged *with* one. The codes above
   apply the convention uniformly, which means several differ from what's in the doc — the
   aliases cover the historical spellings either way, but the canonical codes need your call.
3. **Rainbow Road's `is_retro`.** Sources list it as returning, but MKWorld's is a new
   layout and it's unlockable rather than in a cup. Marked `?`.
4. **`SP` / `PS` stay dangerous.** Starview Peak and Peach Stadium are a two-character
   transposition apart. They are distinct tracks and the typeahead's exact-match-first rule
   (§5) is what keeps them apart — worth confirming you're comfortable with both codes rather
   than renaming one.
