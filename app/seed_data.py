"""Track and alias reference data — spec Appendix A.

All 30 MKWorld courses, with every spelling observed in `Lounge.pdf` carried as an
alias. `wss` and `sks` come from spec section 1 rather than the doc, so the alias set
is a strict superset of what was actually logged.

`has_gate` is the note on the last page of Lounge.pdf: "shortcut flag on bc, gbr, ws, ah".

Codes are provisional. Appendix A's open questions are unresolved:
  * `rMC` vs `MC` — Mario Circuit is listed as a *new* MKWorld course, so the `r`
    prefix contradicts the section 3 convention.
  * The `r` prefix is inconsistent in the source data; the codes here apply it
    uniformly, which means several differ from what the doc actually wrote.
  * Rainbow Road's `is_retro` — a returning name but a new layout. Set 0 pending a
    ruling; the same goes for `rMC`.
  * `SP` (Starview Peak) and `PS` (Peach Stadium) are a two-character transposition
    apart and are different tracks. Section 5's exact-match-first rule keeps them apart.

Aliases are rows, not schema — renaming a canonical code later is a data edit, not a
migration.

`good_from_first` / `good_from_first_if_shrooms` are left 0 for every track. They are
judgment calls, and unlike `has_gate` there is no note in the doc to take them from;
they are set from the Settings screen.
"""

# (code, full_name, is_retro, has_gate, [aliases])
TRACKS = [
    ("MBC", "Mario Bros. Circuit", 0, 0, ["mbc"]),
    ("CC", "Crown City", 0, 0, ["cc", "crown city"]),
    ("WS", "Whistlestop Summit", 0, 1, ["ws", "wss", "whistlestop"]),
    ("DKSP", "DK Spaceport", 0, 0, ["dksp"]),
    ("SP", "Starview Peak", 0, 0, ["sp", "starview"]),
    ("FO", "Faraway Oasis", 0, 0, ["fo", "faraway"]),
    ("PS", "Peach Stadium", 0, 0, ["ps"]),
    ("SSS", "Salty Salty Speedway", 0, 0, ["sss", "salty"]),
    ("GBR", "Great ? Block Ruins", 0, 1, ["gbr"]),
    ("CCF", "Cheep Cheep Falls", 0, 0, ["ccf"]),
    ("DD", "Dandelion Depths", 0, 0, ["dd", "dandelion"]),
    ("BCi", "Boo Cinema", 0, 0, ["bci"]),
    ("DBB", "Dry Bones Burnout", 0, 0, ["dbb"]),
    ("BC", "Bowser's Castle", 0, 1, ["bc", "castle"]),
    ("AH", "Acorn Heights", 0, 1, ["ah", "acorn"]),
    ("rMC", "Mario Circuit", 0, 0, ["rmc", "mc"]),          # is_retro unresolved
    ("rDH", "Desert Hills", 1, 0, ["rdh", "hills", "desert hills"]),
    ("rSGB", "Shy Guy Bazaar", 1, 0, ["rsgb", "sgb", "bazaar"]),
    ("rWSt", "Wario Stadium", 1, 0, ["rwst", "stadium"]),
    ("rAF", "Airship Fortress", 1, 0, ["raf", "af", "airship"]),
    ("rDKP", "DK Pass", 1, 0, ["rdkp", "dkp", "pass"]),
    ("SHS", "Sky-High Sundae", 1, 0, ["shs", "sks", "sundae"]),
    ("rWSh", "Wario Shipyard", 1, 0, ["rwsh", "shipyard"]),
    ("rKTB", "Koopa Troopa Beach", 1, 0, ["rktb", "ktb"]),
    ("rPB", "Peach Beach", 1, 0, ["rpb", "pb", "peach beach"]),
    ("rDDJ", "Dino Dino Jungle", 1, 0, ["rddj", "ddj"]),
    ("rMMM", "Moo Moo Meadows", 1, 0, ["rmmm", "mmm"]),
    ("rCM", "Choco Mountain", 1, 0, ["rcm", "cm", "choco"]),
    ("rTF", "Toad's Factory", 1, 0, ["rtf", "tf"]),
    ("RR", "Rainbow Road", 0, 0, ["rr"]),                   # is_retro unresolved
]

GATE_TRACKS = {code for code, _, _, gate, _ in TRACKS if gate}
