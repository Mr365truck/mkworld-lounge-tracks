"""Canonical three-lap minimaps used by the shock-location screen."""

# Game/gallery order. Rainbow Road is intentionally omitted: this feature uses the
# requested 29 standard maps from the sibling Discord status project's manifest.
# (code, static filename, source width, source height)
MINIMAPS = [
    ("MBC", "mbc.png", 293, 361),
    ("CC", "cc.png", 363, 366),
    ("WS", "ws.png", 289, 399),
    ("DKSP", "dksp.png", 384, 312),
    ("rDH", "rdh.png", 245, 357),
    ("rSGB", "rsgb.png", 228, 378),
    ("rWSt", "rwst.png", 282, 294),
    ("rAF", "raf.png", 253, 372),
    ("rDKP", "rdkp.png", 244, 428),
    ("SP", "sp.png", 313, 323),
    ("rSHS", "rshs.png", 116, 450),
    ("rWSh", "rwsh.png", 318, 336),
    ("rKTB", "rktb.png", 280, 320),
    ("FO", "fo.png", 443, 470),
    ("rPB", "rpb.png", 238, 423),
    ("SSS", "sss.png", 308, 353),
    ("rDDJ", "rddj.png", 352, 283),
    ("GBR", "gbr.png", 142, 456),
    ("CCF", "ccf.png", 278, 382),
    ("DD", "dd.png", 352, 250),
    ("BCi", "bci.png", 353, 291),
    ("DBB", "dbb.png", 203, 437),
    ("rMMM", "rmmm.png", 318, 355),
    ("rCM", "rcm.png", 300, 357),
    ("rTF", "rtf.png", 211, 264),
    ("BC", "bc.png", 240, 450),
    ("AH", "ah.png", 218, 428),
    ("rMC", "rmc.png", 371, 294),
    ("PS", "ps.png", 297, 385),
]

MINIMAP_BY_CODE = {
    code: {"filename": filename, "width": width, "height": height}
    for code, filename, width, height in MINIMAPS
}
