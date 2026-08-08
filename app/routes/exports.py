"""CSV and JSON export — spec section 7.

CSV is flat and wide, one row per race joined to its session and track: the format to
hand to an analysis tool. JSON is the whole database, for archival.
"""
import csv
import io
import json
from datetime import date, datetime

from fastapi import APIRouter
from fastapi.responses import Response
from sqlalchemy import select

from .. import analytics, config, db
from ..schema import import_issues, races, sessions, shock_events, track_aliases, tracks

router = APIRouter(prefix="/export")

CSV_COLUMNS = [
    "session_id", "played_at_utc", "played_at_local", "format", "expected_races",
    "aborted", "is_complete", "room_min_mmr", "room_max_mmr", "room_avg_mmr",
    "mmr_spread", "seat", "mate_mmr", "own_mmr_before", "mmr_delta", "score",
    "race_num", "track_code", "track_name", "is_retro", "has_gate", "variant",
    "placement", "start_position", "lap1_position", "shortcut_hit",
    "mate_placement", "session_avg_placement", "loo_baseline", "residual",
    "race_note", "session_notes",
]


@router.get("/races.csv")
def races_csv():
    with db.read() as conn:
        df = analytics.add_residuals(analytics.load_frame(conn))
        session_notes = dict(conn.execute(select(sessions.c.id, sessions.c.notes)).all())
        race_notes = dict(conn.execute(select(races.c.id, races.c.note)).all())
        retro = dict(conn.execute(select(tracks.c.id, tracks.c.is_retro)).all())

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()

    if not df.empty:
        avg = df.groupby("session_id")["placement"].transform("mean")
        for (_, r), sess_avg in zip(df.iterrows(), avg):
            played = r["played_at"].to_pydatetime() if r["played_at"] is not None else None
            n_placed = int(df[df["session_id"] == r["session_id"]]["placement"].notna().sum())
            w.writerow({
                "session_id": int(r["session_id"]),
                "played_at_utc": played.isoformat() if played else "",
                "played_at_local": config.to_local(played).isoformat() if played else "",
                "format": r["format"],
                "expected_races": int(r["expected_races"]),
                "aborted": int(bool(r["aborted"])),
                "is_complete": int(bool(r["aborted"]) or n_placed == int(r["expected_races"])),
                "room_min_mmr": _num(r["room_min_mmr"]),
                "room_max_mmr": _num(r["room_max_mmr"]),
                "room_avg_mmr": _num(r["room_avg_mmr"]),
                "mmr_spread": _spread(r["room_max_mmr"], r["room_min_mmr"]),
                "seat": _num(r["seat"]),
                "mate_mmr": "", "own_mmr_before": _num(r["own_mmr_before"]),
                "mmr_delta": _num(r["mmr_delta"]), "score": _num(r["score"]),
                "race_num": int(r["race_num"]),
                "track_code": r["code"] or "", "track_name": r["full_name"] or "",
                "is_retro": _num(retro.get(r["track_id"])),
                "has_gate": _num(r["has_gate"]),
                "variant": r["variant"],
                "placement": _num(r["placement"]),
                "start_position": _num(r["start_position"]),
                "lap1_position": _num(r["lap1_position"]),
                "shortcut_hit": r["shortcut_hit"] or "",
                "mate_placement": _num(r["mate_placement"]),
                "session_avg_placement": _round(sess_avg),
                "loo_baseline": _round(r["loo_baseline"]),
                "residual": _round(r["residual"]),
                "race_note": (race_notes.get(int(r["race_id"])) or "").replace("\n", " | "),
                "session_notes": (session_notes.get(int(r["session_id"])) or "").replace("\n", " | "),
            })

    stamp = datetime.now().strftime("%Y%m%d")
    return Response(
        buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="mogi-races-{stamp}.csv"'},
    )


def _num(v):
    import pandas as pd
    if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v):
        return ""
    return int(v) if float(v).is_integer() else v


def _round(v, places: int = 4):
    import pandas as pd
    if v is None or pd.isna(v):
        return ""
    return round(float(v), places)


def _spread(hi, lo):
    import pandas as pd
    if hi is None or lo is None or pd.isna(hi) or pd.isna(lo):
        return ""
    return int(hi) - int(lo)


def _jsonable(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


@router.get("/db.json")
def db_json():
    """Whole-database dump, for archival."""
    payload = {"exported_at": datetime.now(config.local_tz()).isoformat(),
               "tz": config.TZ_NAME}
    with db.read() as conn:
        for name, table in (("tracks", tracks), ("track_aliases", track_aliases),
                            ("sessions", sessions), ("races", races),
                            ("shock_events", shock_events),
                            ("import_issues", import_issues)):
            payload[name] = [
                {k: _jsonable(v) for k, v in dict(row).items()}
                for row in conn.execute(select(table)).mappings()
            ]
    stamp = datetime.now().strftime("%Y%m%d")
    return Response(
        json.dumps(payload, indent=2), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="mogi-db-{stamp}.json"'},
    )
