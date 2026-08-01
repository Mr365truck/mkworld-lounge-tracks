"""Read/write helpers shared by the routes.

`is_complete` is derived here and never stored (spec section 3):

    is_complete = aborted OR (count(races WHERE placement IS NOT NULL) == expected_races)
"""
from datetime import datetime

from sqlalchemy import delete, func, insert, select, update

from . import config
from .schema import default_expected_races, import_issues, races, sessions, tracks


def session_row(conn, session_id: int):
    return conn.execute(select(sessions).where(sessions.c.id == session_id)).mappings().first()


def race_rows(conn, session_id: int):
    q = (
        select(
            races,
            tracks.c.code, tracks.c.full_name,
            tracks.c.has_gate, tracks.c.gate_note,
        )
        .select_from(races.join(tracks, races.c.track_id == tracks.c.id, isouter=True))
        .where(races.c.session_id == session_id)
        .order_by(races.c.race_num)
    )
    return [dict(r) for r in conn.execute(q).mappings()]


def session_stats(session, race_list) -> dict:
    placements = [r["placement"] for r in race_list if r["placement"] is not None]
    n = len(placements)
    expected = session["expected_races"]
    return {
        "placements_recorded": n,
        "expected_races": expected,
        "avg_placement": (sum(placements) / n) if n else None,
        "is_complete": bool(session["aborted"] or n == expected),
        "missing": max(expected - n, 0),
    }


def create_session(conn, fmt: str = "ffa", played_at: datetime | None = None,
                   expected_races: int | None = None) -> int:
    now = config.utcnow()
    expected = expected_races or default_expected_races(fmt)
    session_id = conn.execute(insert(sessions).values(
        played_at=played_at or now, format=fmt, expected_races=expected,
        created_at=now, updated_at=now,
    )).inserted_primary_key[0]
    # Render all expected rows at once — no wizard, no per-race save button.
    conn.execute(insert(races), [
        {"session_id": session_id, "race_num": i, "variant": "3lap",
         "created_at": now, "updated_at": now}
        for i in range(1, expected + 1)
    ])
    return session_id


def sync_race_rows(conn, session_id: int, expected: int) -> None:
    """Add or trim trailing race rows after `expected_races` changes.

    Rows that carry any data are never removed — shrinking a session must not
    silently delete a logged race.
    """
    rows = race_rows(conn, session_id)
    now = config.utcnow()
    if len(rows) < expected:
        conn.execute(insert(races), [
            {"session_id": session_id, "race_num": i, "variant": "3lap",
             "created_at": now, "updated_at": now}
            for i in range(len(rows) + 1, expected + 1)
        ])
    else:
        for r in reversed(rows[expected:]):
            if _is_blank(r):
                conn.execute(delete(races).where(races.c.id == r["id"]))
            else:
                break


def _is_blank(r) -> bool:
    return all(r[k] is None for k in
               ("track_id", "placement", "start_position", "lap1_position",
                "shortcut_hit", "mate_placement", "note"))


def append_race(conn, session_id: int) -> int:
    now = config.utcnow()
    next_num = (conn.execute(
        select(func.max(races.c.race_num)).where(races.c.session_id == session_id)
    ).scalar() or 0) + 1
    conn.execute(insert(races).values(
        session_id=session_id, race_num=next_num, variant="3lap",
        created_at=now, updated_at=now,
    ))
    return next_num


def remove_last_race(conn, session_id: int) -> bool:
    rows = race_rows(conn, session_id)
    if not rows:
        return False
    last = rows[-1]
    if not _is_blank(last):
        return False
    conn.execute(delete(races).where(races.c.id == last["id"]))
    return True


def touch_session(conn, session_id: int) -> None:
    conn.execute(update(sessions).where(sessions.c.id == session_id)
                 .values(updated_at=config.utcnow()))


def session_list(conn) -> list[dict]:
    placed = (
        select(
            races.c.session_id,
            func.count(races.c.id).label("n_races"),
            func.count(races.c.placement).label("n_placements"),
            func.avg(races.c.placement).label("avg_placement"),
        ).group_by(races.c.session_id).subquery()
    )
    q = (
        select(sessions, placed.c.n_races, placed.c.n_placements, placed.c.avg_placement)
        .select_from(sessions.join(placed, sessions.c.id == placed.c.session_id, isouter=True))
        .order_by(sessions.c.played_at.desc(), sessions.c.id.desc())
    )
    out = []
    for r in conn.execute(q).mappings():
        d = dict(r)
        d["n_races"] = d["n_races"] or 0
        d["n_placements"] = d["n_placements"] or 0
        d["is_complete"] = bool(d["aborted"] or d["n_placements"] == d["expected_races"])
        d["played_local"] = config.to_local(d["played_at"])
        out.append(d)
    return out


def open_issues(conn, limit: int = 200) -> list[dict]:
    q = (select(import_issues).where(import_issues.c.resolved == False)  # noqa: E712
         .order_by(import_issues.c.id.desc()).limit(limit))
    return [dict(r) for r in conn.execute(q).mappings()]


def track_list(conn) -> list[dict]:
    from .schema import track_aliases
    rows = [dict(r) for r in conn.execute(
        select(tracks).order_by(tracks.c.is_retro, tracks.c.code)).mappings()]
    alias_map: dict[int, list[str]] = {}
    for a in conn.execute(select(track_aliases.c.track_id, track_aliases.c.alias)
                          .order_by(track_aliases.c.alias)):
        alias_map.setdefault(a.track_id, []).append(a.alias)
    picks = dict(conn.execute(
        select(races.c.track_id, func.count()).group_by(races.c.track_id)).all())
    for r in rows:
        r["aliases"] = alias_map.get(r["id"], [])
        r["picks"] = picks.get(r["id"], 0)
    return rows
