"""JSON endpoints: typeahead, field autosave, row management, analytics.

Every analytic is exposed as JSON alongside its HTML view (spec section 6), so a
script — or an LLM given API access — can pull structured data instead of parsing prose.
"""
from datetime import datetime

from fastapi import APIRouter, Body, HTTPException, Request
from sqlalchemy import delete, insert, select, update

from .. import analytics, config, db, queries
from ..importer import import_text
from ..matching import load_candidates, search
from ..schema import (FORMATS, SHORTCUT_STATES, VARIANTS, import_issues, races,
                      sessions, track_aliases, tracks)

router = APIRouter(prefix="/api")

RACE_INT_FIELDS = {"placement", "start_position", "lap1_position", "mate_placement"}
SESSION_INT_FIELDS = {
    "room_min_mmr", "room_max_mmr", "room_avg_mmr", "seat", "mate_mmr",
    "own_mmr_before", "mmr_delta", "score", "expected_races",
}


def _int_or_none(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s == "":
        return None
    try:
        return int(s)
    except ValueError:
        raise HTTPException(400, f"{v!r} is not a number")


# ------------------------------------------------------------------ typeahead

@router.get("/tracks/search")
def track_search(q: str = "", limit: int = 8):
    """Fuzzy subsequence over codes + aliases + names, exact hits ranked first.

    `auto_commit` is the client's licence to accept without an Enter. It is true only
    for an exact code/alias hit that is *not* a strict prefix of another candidate —
    otherwise `bc` would auto-commit to Bowser's Castle and make `bci` (Boo Cinema)
    unreachable. Spec section 5 asks for exact-match auto-commit; this is that rule
    with the prefix collision it did not anticipate carved out.
    """
    with db.read() as conn:
        candidates = load_candidates(conn)
    matches = search(candidates, q, limit=limit)
    key = (q or "").strip().lower()

    shadowed = any(
        other.startswith(key) and other != key
        for c in candidates
        for other in [c.code.lower(), *c.aliases]
    )
    results = [
        {
            "id": m.track.id, "code": m.track.code, "full_name": m.track.full_name,
            "has_gate": m.track.has_gate, "matched_on": m.matched_on,
            "exact": m.exact, "rank": m.rank,
        }
        for m in matches
    ]
    top = matches[0] if matches else None
    return {
        "query": q,
        "results": results,
        "auto_commit": bool(top and top.exact and not shadowed),
        "shadowed": shadowed,
    }


# --------------------------------------------------------------- field saves

@router.post("/sessions/{session_id}/field")
def save_session_field(session_id: int, payload: dict = Body(...)):
    field = payload.get("field")
    value = payload.get("value")

    with db.connect() as conn:
        if queries.session_row(conn, session_id) is None:
            raise HTTPException(404, "no such session")

        if field in SESSION_INT_FIELDS:
            value = _int_or_none(value)
            if field == "expected_races" and (value is None or value < 1):
                raise HTTPException(400, "expected_races must be at least 1")
            if field == "seat" and value is not None and not 1 <= value <= 12:
                raise HTTPException(400, "seat must be 1-12")
        elif field == "format":
            if value not in FORMATS:
                raise HTTPException(400, f"format must be one of {FORMATS}")
        elif field == "aborted":
            value = bool(value)
        elif field == "notes":
            value = (value or "").strip() or None
        elif field == "played_at":
            try:
                value = config.to_utc(datetime.fromisoformat(value))
            except (TypeError, ValueError):
                raise HTTPException(400, "played_at must be an ISO datetime")
        else:
            raise HTTPException(400, f"unknown field {field!r}")

        conn.execute(update(sessions).where(sessions.c.id == session_id)
                     .values(**{field: value}, updated_at=config.utcnow()))

        if field == "expected_races":
            queries.sync_race_rows(conn, session_id, value)
        elif field == "format" and value == "tournament":
            pass  # expected_races stays whatever it is; changing it is explicit

        session = queries.session_row(conn, session_id)
        rows = queries.race_rows(conn, session_id)
        return {"ok": True, "field": field, "value": value,
                "stats": queries.session_stats(session, rows),
                "n_rows": len(rows)}


@router.post("/sessions/{session_id}/races/{race_num}/field")
def save_race_field(session_id: int, race_num: int, payload: dict = Body(...)):
    field = payload.get("field")
    value = payload.get("value")

    with db.connect() as conn:
        row = conn.execute(select(races).where(
            (races.c.session_id == session_id) & (races.c.race_num == race_num)
        )).mappings().first()
        if row is None:
            raise HTTPException(404, "no such race")

        if field in RACE_INT_FIELDS:
            value = _int_or_none(value)
            if value is not None and not 1 <= value <= 12:
                raise HTTPException(400, f"{field} must be 1-12")
        elif field == "track_id":
            value = _int_or_none(value)
            if value is not None and conn.execute(
                    select(tracks.c.id).where(tracks.c.id == value)).scalar() is None:
                raise HTTPException(400, "no such track")
        elif field == "variant":
            if value not in VARIANTS:
                raise HTTPException(400, f"variant must be one of {VARIANTS}")
        elif field == "shortcut_hit":
            if value in ("", None):
                value = None                       # not recorded — never 'na'
            elif value not in SHORTCUT_STATES:
                raise HTTPException(400, f"shortcut_hit must be one of {SHORTCUT_STATES}")
        elif field == "note":
            value = (value or "").strip() or None
        else:
            raise HTTPException(400, f"unknown field {field!r}")

        conn.execute(update(races).where(races.c.id == row["id"])
                     .values(**{field: value}, updated_at=config.utcnow()))
        queries.touch_session(conn, session_id)

        session = queries.session_row(conn, session_id)
        rows = queries.race_rows(conn, session_id)
        updated = next(r for r in rows if r["race_num"] == race_num)
        return {
            "ok": True, "field": field, "value": value,
            "race": {
                "race_num": race_num,
                "track_id": updated["track_id"], "code": updated["code"],
                "full_name": updated["full_name"],
                # The client shows the shortcut cell only for gate tracks; it has to
                # learn about the change at the moment the track is set.
                "has_gate": bool(updated["has_gate"]),
                "gate_note": updated["gate_note"],
                "variant": updated["variant"],
            },
            "stats": queries.session_stats(session, rows),
        }


# ------------------------------------------------------------------ row edits

@router.post("/sessions/{session_id}/races")
def add_race(session_id: int):
    with db.connect() as conn:
        if queries.session_row(conn, session_id) is None:
            raise HTTPException(404, "no such session")
        num = queries.append_race(conn, session_id)
        session = queries.session_row(conn, session_id)
        rows = queries.race_rows(conn, session_id)
    return {"ok": True, "race_num": num, "stats": queries.session_stats(session, rows)}


@router.delete("/sessions/{session_id}/races/last")
def drop_last_race(session_id: int):
    with db.connect() as conn:
        ok = queries.remove_last_race(conn, session_id)
        session = queries.session_row(conn, session_id)
        rows = queries.race_rows(conn, session_id)
    if not ok:
        raise HTTPException(400, "last race has data; clear it before removing the row")
    return {"ok": True, "stats": queries.session_stats(session, rows)}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int):
    with db.connect() as conn:
        conn.execute(delete(races).where(races.c.session_id == session_id))
        conn.execute(delete(sessions).where(sessions.c.id == session_id))
    return {"ok": True}


# --------------------------------------------------------------------- tracks

@router.post("/tracks/{track_id}/aliases")
def add_alias(track_id: int, payload: dict = Body(...)):
    """Backs the inline 'add as alias for...' prompt in section 5.

    An unknown string offers this rather than being rejected — and free text is still
    never accepted as a track.
    """
    alias = (payload.get("alias") or "").strip().lower()
    if not alias:
        raise HTTPException(400, "alias is required")
    with db.connect() as conn:
        if conn.execute(select(tracks.c.id).where(tracks.c.id == track_id)).scalar() is None:
            raise HTTPException(404, "no such track")
        owner = conn.execute(select(track_aliases.c.track_id)
                             .where(track_aliases.c.alias == alias)).scalar()
        if owner == track_id:
            return {"ok": True, "alias": alias, "already": True}
        if owner is not None:
            code = conn.execute(select(tracks.c.code).where(tracks.c.id == owner)).scalar()
            raise HTTPException(409, f"{alias!r} already maps to {code}")
        conn.execute(insert(track_aliases).values(track_id=track_id, alias=alias))
    return {"ok": True, "alias": alias}


@router.delete("/tracks/aliases/{alias_id}")
def drop_alias(alias_id: int):
    with db.connect() as conn:
        conn.execute(delete(track_aliases).where(track_aliases.c.id == alias_id))
    return {"ok": True}


@router.post("/tracks/{track_id}")
def update_track(track_id: int, payload: dict = Body(...)):
    allowed = {"has_gate", "good_from_first", "good_from_first_if_shrooms",
               "active", "is_retro"}
    values = {k: bool(v) for k, v in payload.items() if k in allowed}
    if "gate_note" in payload:
        values["gate_note"] = (payload["gate_note"] or "").strip() or None
    if "code" in payload and payload["code"]:
        values["code"] = payload["code"].strip()
    if "full_name" in payload and payload["full_name"]:
        values["full_name"] = payload["full_name"].strip()
    if not values:
        raise HTTPException(400, "nothing to update")
    with db.connect() as conn:
        conn.execute(update(tracks).where(tracks.c.id == track_id)
                     .values(**values, updated_at=config.utcnow()))
    return {"ok": True, "updated": values}


# --------------------------------------------------------------------- import

@router.post("/import")
def paste_import(payload: dict = Body(...)):
    text = payload.get("text") or ""
    dry_run = bool(payload.get("dry_run", True))
    if not text.strip():
        raise HTTPException(400, "nothing to import")
    with db.connect() as conn:
        result = import_text(conn, text, dry_run=dry_run)
    return result


@router.post("/import-issues/{issue_id}/resolve")
def resolve_issue(issue_id: int):
    with db.connect() as conn:
        conn.execute(update(import_issues).where(import_issues.c.id == issue_id)
                     .values(resolved=True))
    return {"ok": True}


# ------------------------------------------------------------------ analytics

@router.get("/analytics")
def analytics_json(intermissions: bool = False):
    with db.read() as conn:
        return analytics.overview(conn, include_intermissions=intermissions)


@router.get("/analytics/tracks/{track_id}/trend")
def track_trend(track_id: int, window: int = 5):
    with db.read() as conn:
        df = analytics.add_residuals(analytics.load_frame(conn))
    return {"track_id": track_id, "window": window,
            "points": analytics.track_trend(df, track_id, window)}


@router.get("/sessions/{session_id}")
def session_json(session_id: int):
    with db.read() as conn:
        session = queries.session_row(conn, session_id)
        if session is None:
            raise HTTPException(404, "no such session")
        rows = queries.race_rows(conn, session_id)
        return {"session": dict(session), "races": rows,
                "stats": queries.session_stats(session, rows)}
