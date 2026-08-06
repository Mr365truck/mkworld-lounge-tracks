# Historical data

Machine-readable extraction of `Lounge.pdf`, the Google Doc archive of every mogi
logged before this app existed. This is the input for build step 2 in
`mogi-tracker-spec.md` (the paste importer) and the fixture source for its tests.

| file | what it is |
|---|---|
| `lounge-raw.txt` | Plain text recovered from `Lounge.pdf`, one line per doc line |
| `lounge-sessions.json` | Structured sessions + races, tracks resolved to Appendix A codes |

Regenerate both:

```sh
python3 tools/extract_lounge.py > data/lounge-raw.txt
python3 tools/parse_lounge.py data/lounge-raw.txt > data/lounge-sessions.json
```

`Lounge.pdf` is a Google Docs export using subset fonts with no ToUnicode map, so
ordinary PDF text extraction returns nothing. `tools/extract_lounge.py` decodes the
glyph ids directly; see its docstring.

## What's in it

- **27 sessions**, 25 with races — 281 races, 139 recorded placements.
- **All 30 MKWorld courses appear**, under 45 distinct spellings. Every one resolves
  through the Appendix A alias table; **0 unresolved**.
- Placements only start being logged at session 15 (7/21). Sessions 1–14 record the
  track list only, which is why 14 sessions come through `is_complete: false`.
- Exactly **one intermission** race: session 23, race 1, `rAF`.
- Most-picked: `BC` 23, `WS` 21, `AH` 20, `rAF` 18, `rSHS` 17, `FO` 16.

## Parser warnings — all four are real, none are bugs

1. **Session 2, race 3** — `Farawayhandling`, two text runs merged by the PDF
   extractor. Recovered as `FO` with the trailing text kept as a note.
2. **Session 9, race 12** and **13, race 12** — numbered races with no track at all.
   This is why `races.track_id` is nullable in the spec.
3. **Session 22 → 23** — race numbering restarts at 1 with no new header. This is the
   abandoned-session case from spec §10: `faraway` from start position 1, no placement,
   then a fresh 12-race session. The parser splits them and marks 22 `aborted`.

## Caveats

- **Codes are provisional.** Appendix A's open questions are unresolved — `rMC` vs `MC`,
  the inconsistent `r` prefix, and Rainbow Road's `is_retro`. The aliases are safe; the
  canonical codes may be renamed.
- `expected_races` and `aborted` are *derived* here (12 races, 8 for tournaments;
  aborted if ≤2 races). Real imports should let these be set explicitly.
- The doc is **not in chronological order** — 7/16 is logged before 7/8. Order in the
  JSON follows the document, not time.
- An undefined `+1` sub-item appears on a few races and is kept verbatim in `notes`.
  It needs a ruling before it means anything.
