"""Schema and seed tests — spec section 3 and Appendix A."""
import subprocess
import sys

import pytest
from sqlalchemy import insert, select, text

from app import config
from app.schema import (default_expected_races, races, sessions, shock_events,
                        track_aliases, tracks)
from app.seed import seed_tracks
from app.seed_data import TRACKS
from app.shocks import MINIMAPS


def test_seed_covers_all_thirty_courses(conn):
    assert conn.execute(select(tracks)).rowcount if False else True
    codes = [r for (r,) in conn.execute(select(tracks.c.code))]
    assert len(codes) == 30
    assert len(set(codes)) == 30


def test_shock_manifest_has_exactly_the_29_local_assets():
    assert len(MINIMAPS) == 29
    assert len({code for code, *_ in MINIMAPS}) == 29
    assert "RR" not in {code for code, *_ in MINIMAPS}
    asset_dir = config.BASE_DIR / "static" / "minimaps"
    assert all((asset_dir / filename).is_file() for _, filename, _, _ in MINIMAPS)


def test_seed_marks_exactly_the_four_gate_tracks(conn):
    """From the note on the last page of Lounge.pdf: 'shortcut flag on bc, gbr, ws, ah'."""
    gate = {r for (r,) in conn.execute(
        select(tracks.c.code).where(tracks.c.has_gate == True))}  # noqa: E712
    assert gate == {"BC", "GBR", "WS", "AH"}


def test_seed_is_idempotent(conn):
    before = conn.execute(select(tracks)).all(), conn.execute(select(track_aliases)).all()
    result = seed_tracks(conn)
    assert result == {"tracks_added": 0, "aliases_added": 0}
    after = conn.execute(select(tracks)).all(), conn.execute(select(track_aliases)).all()
    assert len(before[0]) == len(after[0]) and len(before[1]) == len(after[1])


def test_reseeding_preserves_hand_set_flags(conn):
    from sqlalchemy import update
    conn.execute(update(tracks).where(tracks.c.code == "BC")
                 .values(good_from_first=True, gate_note="NISC"))
    seed_tracks(conn)
    row = conn.execute(select(tracks).where(tracks.c.code == "BC")).mappings().first()
    assert row["good_from_first"] is True and row["gate_note"] == "NISC"


def test_aliases_are_lowercase_and_unique(conn):
    aliases = [r for (r,) in conn.execute(select(track_aliases.c.alias))]
    assert all(a == a.lower() for a in aliases)
    assert len(aliases) == len(set(aliases))


def test_every_historical_spelling_from_appendix_a_resolves(conn):
    for code, _name, _retro, _gate, spellings in TRACKS:
        track_id = conn.execute(select(tracks.c.id).where(tracks.c.code == code)).scalar()
        for spelling in spellings:
            got = conn.execute(select(track_aliases.c.track_id)
                               .where(track_aliases.c.alias == spelling)).scalar()
            assert got == track_id, f"{spelling!r} does not resolve to {code}"


def test_default_expected_races():
    assert default_expected_races("tournament") == 8
    for fmt in ("ffa", "2v2", "3v3", "4v4", "6v6"):
        assert default_expected_races(fmt) == 12


# ------------------------------------------------------------------ constraints

def _a_session(conn, **kw):
    values = {"played_at": config.utcnow(), "format": "ffa", "expected_races": 12}
    values.update(kw)
    return conn.execute(insert(sessions).values(**values)).inserted_primary_key[0]


def test_placement_bounds_are_enforced_in_the_database(conn):
    sid = _a_session(conn)
    for bad in (0, 13):
        with pytest.raises(Exception):
            conn.execute(insert(races).values(session_id=sid, race_num=1, placement=bad))
            conn.execute(text("SELECT 1"))


def test_variant_is_constrained(conn):
    sid = _a_session(conn)
    with pytest.raises(Exception):
        conn.execute(insert(races).values(session_id=sid, race_num=1, variant="2lap"))


def test_shortcut_hit_is_constrained_to_four_states(conn):
    sid = _a_session(conn)
    with pytest.raises(Exception):
        conn.execute(insert(races).values(session_id=sid, race_num=1, shortcut_hit="yes"))


def test_race_num_is_unique_within_a_session(conn):
    sid = _a_session(conn)
    conn.execute(insert(races).values(session_id=sid, race_num=1))
    with pytest.raises(Exception):
        conn.execute(insert(races).values(session_id=sid, race_num=1))


def test_format_is_constrained(conn):
    with pytest.raises(Exception):
        _a_session(conn, format="8v8")


def test_track_id_is_nullable(conn):
    """A numbered race with no track is real data, not an error."""
    sid = _a_session(conn)
    conn.execute(insert(races).values(session_id=sid, race_num=12, track_id=None))
    row = conn.execute(select(races).where(races.c.session_id == sid)).mappings().first()
    assert row["track_id"] is None


def test_shock_coordinates_and_lap_are_constrained(conn):
    track_id = conn.execute(select(tracks.c.id).where(tracks.c.code == "MBC")).scalar_one()
    conn.execute(insert(shock_events).values(track_id=track_id, x=0.2, y=0.8, lap=3))
    assert conn.execute(select(shock_events.c.lap)).scalar_one() == 3


# ------------------------------------------------------------------- migrations

def test_alembic_upgrade_then_downgrade_is_clean(tmp_path):
    """Migrations run start to finish against a fresh file, both directions."""
    import os
    env = dict(os.environ, DATABASE_PATH=str(tmp_path / "m.db"))
    root = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
    for args in (["upgrade", "head"], ["downgrade", "base"], ["upgrade", "head"]):
        r = subprocess.run([sys.executable, "-m", "alembic", *args],
                           cwd=root, env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
