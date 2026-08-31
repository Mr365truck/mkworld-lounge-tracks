"""Server-rendered HTML screens."""
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from .. import analytics, config, current_mmr, db, do_not_mogi, lounge, queries
from ..schema import (FORMATS, default_expected_races, import_issues, races,
                      sessions, shock_events, tracks)
from ..shocks import MINIMAPS
from ..templating import templates

router = APIRouter()


@router.get("/")
def home(request: Request):
    with db.read() as conn:
        rows = queries.session_list(conn)
        n_issues = conn.execute(
            select(func.count()).select_from(import_issues)
            .where(import_issues.c.resolved == False)  # noqa: E712
        ).scalar()
        totals = {
            "sessions": len(rows),
            "races": conn.execute(select(func.count()).select_from(races)).scalar(),
            "placements": conn.execute(
                select(func.count(races.c.placement))).scalar(),
        }
        totals["current_mmr"] = current_mmr.value(conn)
        recent_deltas = [
            r["mmr_delta"] for r in rows[:10] if r["mmr_delta"] is not None
        ]
        totals["last_10_delta"] = sum(recent_deltas) if recent_deltas else None
    return templates.TemplateResponse(request, "sessions.html", {
        "sessions": rows, "totals": totals, "n_issues": n_issues,
        "nav": "sessions",
    })


@router.post("/sessions")
def new_session(fmt: str = Form("ffa"), played_at: str = Form("")):
    if fmt not in FORMATS:
        raise HTTPException(400, "unknown format")
    when = None
    if played_at:
        try:
            when = config.to_utc(config.round_to_hour(datetime.fromisoformat(played_at)))
        except ValueError:
            when = None
    with db.connect() as conn:
        session_id = queries.create_session(conn, fmt=fmt, played_at=when)
    return RedirectResponse(f"/sessions/{session_id}", status_code=303)


@router.get("/sessions/{session_id}")
def session_detail(request: Request, session_id: int):
    with db.read() as conn:
        session = queries.session_row(conn, session_id)
        if session is None:
            raise HTTPException(404, "no such session")
        rows = queries.race_rows(conn, session_id)
        stats = queries.session_stats(session, rows)
        issues = [dict(r) for r in conn.execute(
            select(import_issues).where(import_issues.c.session_id == session_id)
        ).mappings()]
    is_team = session["format"] in ("2v2", "3v3", "4v4", "6v6")
    return templates.TemplateResponse(request, "session_detail.html", {
        "session": session, "races": rows, "stats": stats, "issues": issues,
        "formats": FORMATS, "is_team": is_team,
        "played_local": config.to_local(session["played_at"]),
        "nav": "sessions",
    })


@router.get("/analytics")
def analytics_page(request: Request, intermissions: bool = False):
    with db.read() as conn:
        data = analytics.overview(conn, include_intermissions=intermissions)
    return templates.TemplateResponse(request, "analytics.html", {
        "a": data, "show_intermissions": intermissions, "nav": "analytics",
    })


@router.get("/shocks")
def shocks_page(request: Request):
    codes = [code for code, _filename, _width, _height in MINIMAPS]
    with db.read() as conn:
        track_by_code = {
            row["code"]: dict(row)
            for row in conn.execute(
                select(tracks.c.id, tracks.c.code, tracks.c.full_name)
                .where(tracks.c.code.in_(codes))
            ).mappings()
        }
        events_by_track = defaultdict(list)
        for event in conn.execute(
            select(shock_events.c.id, shock_events.c.track_id, shock_events.c.x,
                   shock_events.c.y, shock_events.c.lap)
            .order_by(shock_events.c.id)
        ).mappings():
            events_by_track[event["track_id"]].append(dict(event))

    shock_tracks = []
    payload = {}
    for code, filename, width, height in MINIMAPS:
        track = track_by_code.get(code)
        if track is None:  # Defensive: a partially seeded DB should still render.
            continue
        event_rows = events_by_track[track["id"]]
        payload[str(track["id"])] = event_rows
        shock_tracks.append({
            **track,
            "filename": filename,
            "width": width,
            "height": height,
            "display_max_width": round(304 * width / height),
            "events": event_rows,
        })
    return templates.TemplateResponse(request, "shocks.html", {
        "tracks": shock_tracks, "shock_payload": payload, "nav": "shocks",
    })


@router.get("/do-not-mogi")
def do_not_mogi_page(request: Request):
    with db.read() as conn:
        players = do_not_mogi.list_players(conn)
    return templates.TemplateResponse(request, "do_not_mogi.html", {
        "players": players,
        "refresh_days": config.LOUNGE_NAME_REFRESH_DAYS,
        "lounge_game_label": lounge.game_label(),
        "lounge_profile_url": lounge.profile_url,
        "nav": "do_not_mogi",
    })


@router.get("/settings")
def settings_page(request: Request):
    with db.read() as conn:
        tracks_rows = queries.track_list(conn)
        issues = queries.open_issues(conn)
    return templates.TemplateResponse(request, "settings.html", {
        "tracks": tracks_rows, "issues": issues, "nav": "settings",
        "db_path": config.DATABASE_PATH, "tz": config.TZ_NAME,
    })


@router.get("/import")
def import_page(request: Request):
    return templates.TemplateResponse(request, "import.html", {"nav": "import"})


@router.get("/sessions/{session_id}/delete")
def confirm_delete(request: Request, session_id: int):
    with db.read() as conn:
        session = queries.session_row(conn, session_id)
        if session is None:
            raise HTTPException(404, "no such session")
        rows = queries.race_rows(conn, session_id)
    return templates.TemplateResponse(request, "confirm_delete.html", {
        "session": session, "races": rows, "nav": "sessions",
        "played_local": config.to_local(session["played_at"]),
    })
