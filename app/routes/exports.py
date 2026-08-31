"""Raw database exports.

CSV is one row per stored race with its stored session fields repeated. JSON mirrors
the database tables directly. Neither export calculates analytics or derived stats.
"""
import csv
import io
import json
from datetime import date, datetime

from fastapi import APIRouter
from fastapi.responses import Response
from sqlalchemy import select

from .. import db
from ..schema import (do_not_mogi_players, import_issues, lounge_mmr_cache,
                      races, sessions, shock_events, track_aliases, tracks)

router = APIRouter(prefix="/export")

CSV_COLUMNS = [
    # Stored session data.
    "session_id", "played_at", "format", "expected_races", "aborted",
    "room_min_mmr", "room_max_mmr", "room_avg_mmr", "seat", "mate_mmr",
    "own_mmr_before", "mmr_delta", "score", "session_notes",
    "session_mmr_updated_at", "session_created_at", "session_updated_at",
    # Stored race data.
    "race_id", "race_num", "track_id", "variant", "placement",
    "start_position", "lap1_position", "shortcut_hit", "mate_placement",
    "race_note", "race_created_at", "race_updated_at",
    # Reference labels for the stored track_id; no values are derived.
    "track_code", "track_name",
]


@router.get("/races.csv")
def races_csv():
    query = (
        select(
            sessions.c.id.label("session_id"),
            sessions.c.played_at,
            sessions.c.format,
            sessions.c.expected_races,
            sessions.c.aborted,
            sessions.c.room_min_mmr,
            sessions.c.room_max_mmr,
            sessions.c.room_avg_mmr,
            sessions.c.seat,
            sessions.c.mate_mmr,
            sessions.c.own_mmr_before,
            sessions.c.mmr_delta,
            sessions.c.score,
            sessions.c.notes.label("session_notes"),
            sessions.c.mmr_updated_at.label("session_mmr_updated_at"),
            sessions.c.created_at.label("session_created_at"),
            sessions.c.updated_at.label("session_updated_at"),
            races.c.id.label("race_id"),
            races.c.race_num,
            races.c.track_id,
            races.c.variant,
            races.c.placement,
            races.c.start_position,
            races.c.lap1_position,
            races.c.shortcut_hit,
            races.c.mate_placement,
            races.c.note.label("race_note"),
            races.c.created_at.label("race_created_at"),
            races.c.updated_at.label("race_updated_at"),
            tracks.c.code.label("track_code"),
            tracks.c.full_name.label("track_name"),
        )
        .select_from(
            sessions.join(races, races.c.session_id == sessions.c.id)
            .join(tracks, races.c.track_id == tracks.c.id, isouter=True)
        )
        .order_by(sessions.c.id, races.c.race_num)
    )

    with db.read() as conn:
        rows = conn.execute(query).mappings().all()

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_value(row[key]) for key in CSV_COLUMNS})

    stamp = datetime.now().strftime("%Y%m%d")
    return Response(
        buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="mogi-races-{stamp}.csv"'},
    )


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


@router.get("/db.json")
def db_json():
    """Direct dump of every application table, with no computed fields."""
    payload = {}
    with db.read() as conn:
        for name, table in (("tracks", tracks), ("track_aliases", track_aliases),
                            ("sessions", sessions), ("races", races),
                            ("shock_events", shock_events),
                            ("do_not_mogi_players", do_not_mogi_players),
                            ("lounge_mmr_cache", lounge_mmr_cache),
                            ("import_issues", import_issues)):
            payload[name] = [
                {key: _jsonable(value) for key, value in dict(row).items()}
                for row in conn.execute(select(table).order_by(table.c.id)).mappings()
            ]
    stamp = datetime.now().strftime("%Y%m%d")
    return Response(
        json.dumps(payload, indent=2), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="mogi-db-{stamp}.json"'},
    )
