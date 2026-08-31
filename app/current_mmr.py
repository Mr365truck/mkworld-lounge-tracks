"""Cached MKCentral MMR with short-lived manual session overrides."""
import logging
from datetime import timedelta, timezone

from sqlalchemy import insert, select, update

from . import config, db, lounge
from .schema import lounge_mmr_cache, sessions

log = logging.getLogger("mogi.current_mmr")


def _cache_row(conn) -> dict | None:
    row = conn.execute(
        select(lounge_mmr_cache).where(lounge_mmr_cache.c.id == 1)
    ).mappings().first()
    return dict(row) if row is not None else None


def value(conn) -> int | None:
    """Current MMR: a post-refresh manual session value, then MKCentral, then history."""
    cached = _cache_row(conn)
    manual = select(sessions).where(sessions.c.own_mmr_before.is_not(None))
    if cached is not None:
        manual = manual.where(sessions.c.mmr_updated_at > cached["refreshed_at"])
        manual = manual.order_by(
            sessions.c.mmr_updated_at.desc(), sessions.c.played_at.desc(),
            sessions.c.id.desc(),
        )
    else:
        # Preserve the old landing-page behavior until the first successful fetch.
        manual = manual.order_by(sessions.c.played_at.desc(), sessions.c.id.desc())
    latest = conn.execute(manual.limit(1)).mappings().first()
    if latest is not None:
        return latest["own_mmr_before"] + (latest["mmr_delta"] or 0)
    return cached["mmr"] if cached is not None else None


def next_refresh_at():
    """Return an aware UTC time for the first refresh after a process starts."""
    now = config.utcnow()
    with db.read() as conn:
        cached = _cache_row(conn)
    if cached is None:
        due = now
    else:
        due = max(
            now,
            cached["refreshed_at"]
            + timedelta(hours=config.LOUNGE_MMR_REFRESH_HOURS),
        )
    return due.replace(tzinfo=timezone.utc)


def refresh() -> dict:
    """Fetch and persist one current MMR value, retaining the cache on failure."""
    try:
        player = lounge.get_leaderboard_player(config.LOUNGE_PLAYER_ID)
    except lounge.LoungeError as exc:
        log.warning("could not refresh current Lounge MMR: %s", exc)
        return {"updated": False, "error": str(exc)}

    refreshed_at = config.utcnow()
    values = {
        "lounge_player_id": player["lounge_player_id"],
        "player_name": player["name"],
        "mmr": player["mmr"],
        "season": player["season"],
        "refreshed_at": refreshed_at,
    }
    with db.connect() as conn:
        if _cache_row(conn) is None:
            conn.execute(insert(lounge_mmr_cache).values(id=1, **values))
        else:
            conn.execute(update(lounge_mmr_cache).where(
                lounge_mmr_cache.c.id == 1
            ).values(**values))
    log.info("refreshed current Lounge MMR: %s", values)
    return {"updated": True, **values}
