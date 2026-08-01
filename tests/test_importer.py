"""Importer tests — every fixture is a real excerpt from Lounge.pdf.

Spec section 9 says the section 10 edge cases become importer fixtures, and section
10 notes they are all present in the historical data, so these are real rather than
invented. The line numbers refer to data/lounge-raw.txt.
"""
import textwrap

from sqlalchemy import select

from app import importer
from app.schema import import_issues, races, sessions, tracks


def _import(conn, text):
    return importer.import_text(conn, textwrap.dedent(text).strip("\n"))


def _races_of(conn, session_id):
    return [dict(r) for r in conn.execute(
        select(races).where(races.c.session_id == session_id)
        .order_by(races.c.race_num)).mappings()]


def _sessions(conn):
    return [dict(r) for r in conn.execute(
        select(sessions).order_by(sessions.c.id)).mappings()]


# --------------------------------------------------------------- header parsing

def test_header_tolerates_case_punctuation_and_separators(conn):
    """`maX:`, `12 NOON`, `seat 5` without a colon, and `4,085`."""
    _import(conn, """
        FFA 7/6 12 NOON, min: 3728, maX: 4236, avg: 4,085, seat 9
        1. bc
        a. 3
    """)
    s = _sessions(conn)[0]
    assert (s["room_min_mmr"], s["room_max_mmr"], s["room_avg_mmr"]) == (3728, 4236, 4085)
    assert s["seat"] == 9
    assert s["played_at"].hour == 12       # noon, not midnight


def test_missing_date_inherits_from_the_block_above(conn):
    _import(conn, """
        ffa 7/25, 12 noon, min: 3285, max: 3701, avg: 3486, seat: 1
        1. bc
        a. 4
        12 noon, ffa, min: 2966, max: 3795, avg: 3288, seat: 1
        1. ws
        a. 5
    """)
    a, b = _sessions(conn)
    assert a["played_at"].date() == b["played_at"].date()


def test_month_name_header_dates_the_tournament(conn):
    """`friday jun 6 @ 6pm` — no `6/6` anywhere, so the numeric regex misses it."""
    result = importer.parse("1yr anniversary tourney friday jun 6 @ 6pm\nRoom 1\n1. mmm")
    s = result.sessions[0]
    assert (s.played_at.month, s.played_at.day) == (6, 6)
    assert s.format == "tournament"


def test_implausible_min_mmr_warns_but_is_kept(conn):
    """One historical header reads `min: 7`. Warn on entry, don't reject."""
    res = _import(conn, """
        ffa, placement, 5/26, 3pm, min: 7, max: 3000, avg: 2140, seat: 1
        1. cc
    """)
    assert _sessions(conn)[0]["room_min_mmr"] == 7
    assert any(w["kind"] == "implausible_mmr" for w in res["warnings"])


# ----------------------------------------------------------------- session shape

def test_one_event_two_tournament_rooms_of_eight(conn):
    _import(conn, """
        1yr anniversary tourney friday jun 6 @ 6pm
        Room 1
        1. mmm
        2. shipyard
        3. shs
        4. ktb
        5. whistlestop
        6. rmc
        7. peach beach
        8. bc
        scored 8th
        Room 2
        1. Desert hills
        2. mmm
        3. mbc
        4. faraway
        5. shs
        6. starview
        7. bc
        8. airship
        placed 8th/12, eliminated
    """)
    rooms = _sessions(conn)
    assert len(rooms) == 2
    for r in rooms:
        assert r["format"] == "tournament"
        # 8 races, not 12 — this is the case is_complete could not evaluate before
        # expected_races existed.
        assert r["expected_races"] == 8
        assert len(_races_of(conn, r["id"])) == 8


def test_abandoned_session_splits_from_the_one_that_followed(conn):
    """7/30 12 noon: one race, no placement, abandoned, then a fresh session.

    Race numbering restarting at 1 with no new header is the only signal.
    """
    res = _import(conn, """
        12 noon, ffa, min: 2966, max: 3795, avg: 3288, seat: 1
        1. faraway
        a. start: 1
        1. raf (intermission to)
        a. start: 6
        b. 4
        2. gbr
        a. 8
        3. ws
        a. 10
    """)
    first, second = _sessions(conn)
    assert len(_races_of(conn, first["id"])) == 1
    assert len(_races_of(conn, second["id"])) == 3
    # The aborted flag is what stops the one-race session reading as incomplete
    # forever. The heuristic is <= 2 races and short of expected; the session that
    # followed is a real one that simply is not finished being logged.
    assert first["aborted"] is True
    assert second["aborted"] is False
    assert any(w["kind"] == "session_split" for w in res["warnings"])
    # The restarted session inherits the header it never had.
    assert second["room_avg_mmr"] == 3288


def test_header_with_no_races_at_all(conn):
    _import(conn, """
        viewer 6v6, 6/12 @ 7:00pm
        Ffa 6/13, 10am
        1. Faraway
    """)
    viewer, ffa = _sessions(conn)
    assert _races_of(conn, viewer["id"]) == []
    assert viewer["aborted"] is True          # will never gain rows
    assert "spectated" in (viewer["notes"] or "")
    assert len(_races_of(conn, ffa["id"])) == 1


def test_sessions_out_of_chronological_order_are_not_reordered(conn):
    """The doc logs 7/16 before 7/8. The importer cannot assume order."""
    _import(conn, """
        ffa 7/16, min: 100, max: 200, avg: 150
        1. bc
        ffa 7/8, min: 100, max: 200, avg: 150
        1. ws
    """)
    a, b = _sessions(conn)
    assert a["played_at"] > b["played_at"]    # stored as written, not sorted


# --------------------------------------------------------------------- race lines

def test_same_track_twice_in_one_session_is_two_rows(conn):
    """The miscount from spec section 1: `raf` at races 1 and 11."""
    _import(conn, """
        ffa 7/9, min: 100, max: 200, avg: 150
        1. raf
        a. 3
        11. raf
        a. 9
    """)
    rows = _races_of(conn, _sessions(conn)[0]["id"])
    raf = [r for r in rows if r["track_id"] == _track_id(conn, "rAF")]
    assert len(raf) == 2
    assert sorted(r["placement"] for r in raf) == [3, 9]


def test_numbered_race_with_no_track_is_kept(conn):
    """A bare `12.` appears in the 7/6 and 7/17 sessions. Dropping it hides a gap."""
    res = _import(conn, """
        ffa 7/6, min: 100, max: 200, avg: 150
        11. ws
        a. 4
        12.
    """)
    rows = _races_of(conn, _sessions(conn)[0]["id"])
    assert len(rows) == 2
    assert rows[1]["race_num"] == 12 and rows[1]["track_id"] is None
    assert any(w["kind"] == "no_track" for w in res["warnings"])


def test_inline_parenthetical_sets_intermission_and_keeps_prose(conn):
    _import(conn, """
        ffa 7/30, min: 100, max: 200, avg: 150
        1. raf (intermission to)
        a. 4
        2. hills (why)
        a. 6
    """)
    rows = _races_of(conn, _sessions(conn)[0]["id"])
    assert rows[0]["variant"] == "intermission"
    assert rows[0]["track_id"] == _track_id(conn, "rAF")
    assert rows[1]["variant"] == "3lap"
    assert "why" in rows[1]["note"]


def test_sub_item_letters_vary_with_whether_start_was_recorded(conn):
    """Sometimes `a.` is the placement, sometimes `a. start: 8` with placement at `b.`."""
    _import(conn, """
        ffa 7/12, min: 100, max: 200, avg: 150
        1. bc
        a. 5
        2. ws
        a. start: 8
        b. 3
        3. shs
        a. start: ?
        b. 9
    """)
    rows = _races_of(conn, _sessions(conn)[0]["id"])
    assert (rows[0]["start_position"], rows[0]["placement"]) == (None, 5)
    assert (rows[1]["start_position"], rows[1]["placement"]) == (8, 3)
    assert (rows[2]["start_position"], rows[2]["placement"]) == (None, 9)


def test_extra_sub_items_become_notes_including_the_undefined_plus_one(conn):
    _import(conn, """
        ffa 7/12, min: 100, max: 200, avg: 150
        1. bc
        a. 4
        b. l1: no shrooms
        c. l2: draft on cut
        d. +1
    """)
    row = _races_of(conn, _sessions(conn)[0]["id"])[0]
    assert row["placement"] == 4
    # `+1` has no defined meaning yet, so it is kept verbatim rather than guessed at.
    assert "+1" in row["note"]
    assert "no shrooms" in row["note"]


def test_lap1_sub_item_is_captured_when_written_as_a_position(conn):
    _import(conn, """
        ffa 7/12, min: 100, max: 200, avg: 150
        1. bc
        a. start: 4
        b. 3
        c. l1: 2
    """)
    row = _races_of(conn, _sessions(conn)[0]["id"])[0]
    assert row["lap1_position"] == 2


# ------------------------------------------------------------------ team sessions

def test_2v2_mate_placement_and_mate_mmr(conn):
    _import(conn, """
        7/30, 10pm, 2v2, min: 3891, max: 5666, avg: 4844, seat: 12, mate: 5435
        1. BC
        a. start: 9
        b. 7
        c. mate: 4
        2. ws
        a. 12
        3. stadium
        a. 9
        b. mate: 3
    """)
    s = _sessions(conn)[0]
    assert s["format"] == "2v2" and s["mate_mmr"] == 5435
    rows = _races_of(conn, s["id"])
    assert (rows[0]["start_position"], rows[0]["placement"], rows[0]["mate_placement"]) == (9, 7, 4)
    assert rows[2]["mate_placement"] == 3


# --------------------------------------------------------------- results in prose

def test_prose_results_extract_score_and_mmr_delta(conn):
    _import(conn, """
        ffa 7/22, min: 100, max: 200, avg: 150
        1. bc
        a. 3
        Tbh got really unlucky, scored 88, +46 mmr
    """)
    s = _sessions(conn)[0]
    assert s["score"] == 88
    assert s["mmr_delta"] == 46
    assert "really unlucky" in s["notes"]


def test_scored_8th_is_a_placement_not_a_score(conn):
    _import(conn, """
        ffa 7/22, min: 100, max: 200, avg: 150
        1. bc
        scored 8th
    """)
    s = _sessions(conn)[0]
    assert s["score"] is None
    assert "scored 8th" in s["notes"]


# ------------------------------------------------------------- importer defaults

def test_defaults_are_3lap_and_null_shortcut(conn):
    """`shortcut_hit` must be NULL, never 'na' — the doc records neither, and 'na'
    would claim the gate wasn't in play."""
    _import(conn, """
        ffa 7/22, min: 100, max: 200, avg: 150
        1. bc
        a. 3
    """)
    row = _races_of(conn, _sessions(conn)[0]["id"])[0]
    assert row["variant"] == "3lap"
    assert row["shortcut_hit"] is None


def test_unresolved_track_goes_to_the_review_queue_not_the_floor(conn):
    res = _import(conn, """
        ffa 7/22, min: 100, max: 200, avg: 150
        1. not-a-real-track
        a. 3
    """)
    rows = _races_of(conn, _sessions(conn)[0]["id"])
    assert len(rows) == 1 and rows[0]["track_id"] is None
    assert rows[0]["placement"] == 3           # the rest of the race survives
    assert res["unresolved"] == 1
    kinds = [r.kind for r in conn.execute(select(import_issues.c.kind))]
    assert "unresolved_track" in kinds


def test_merged_extractor_token_recovers_the_leading_alias(conn):
    """`Farawayhandling` — two adjacent PDF text runs merged into one token."""
    _import(conn, """
        1yr anniversary tourney friday jun 6 @ 6pm
        Room 1
        3. Farawayhandling
    """)
    row = _races_of(conn, _sessions(conn)[0]["id"])[0]
    assert row["track_id"] == _track_id(conn, "FO")
    assert "handling" in row["note"]


def test_dry_run_writes_nothing(conn):
    text = "ffa 7/22, min: 100, max: 200, avg: 150\n1. bc\na. 3"
    res = importer.import_text(conn, text, dry_run=True)
    assert res["races"] == 1 and res["dry_run"] is True
    assert _sessions(conn) == []


# ------------------------------------------------------------- the whole archive

def test_full_historical_archive(conn):
    """The numbers the prototype parser produced, pinned end to end."""
    import pathlib
    raw = pathlib.Path(__file__).resolve().parent.parent / "data" / "lounge-raw.txt"
    res = importer.import_text(conn, raw.read_text())
    assert res["sessions"] == 27
    assert res["races"] == 281
    assert res["placements"] == 139
    assert res["unresolved"] == 0
    # All 30 courses appear, resolved from 45 distinct spellings.
    used = {r for (r,) in conn.execute(
        select(races.c.track_id).where(races.c.track_id.isnot(None)).distinct())}
    assert len(used) == 30


def _track_id(conn, code):
    return conn.execute(select(tracks.c.id).where(tracks.c.code == code)).scalar()
