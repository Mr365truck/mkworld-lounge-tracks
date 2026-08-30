"""Persistence and weekly display-name refresh for the Do Not Mogi list."""
import logging
from datetime import timedelta

from sqlalchemy import insert, select, update

from . import config, db, lounge
from .schema import do_not_mogi_players

log = logging.getLogger("mogi.do_not_mogi")


def list_players(conn) -> list[dict]:
    rows = conn.execute(
        select(do_not_mogi_players).order_by(
            do_not_mogi_players.c.name.collate("NOCASE"),
            do_not_mogi_players.c.id,
        )
    ).mappings()
    return [dict(row) for row in rows]


def add_player(lounge_player_id: int) -> tuple[dict, bool]:
    """Fetch and add a canonical player. Returns (row, already_listed)."""
    player = lounge.get_player(lounge_player_id)
    with db.connect() as conn:
        existing = conn.execute(select(do_not_mogi_players).where(
            do_not_mogi_players.c.lounge_player_id == player["lounge_player_id"]
        )).mappings().first()
        if existing is not None:
            return dict(existing), True
        now = config.utcnow()
        result = conn.execute(insert(do_not_mogi_players).values(
            **player, added_at=now, last_refreshed_at=now, updated_at=now,
        ))
        row = conn.execute(select(do_not_mogi_players).where(
            do_not_mogi_players.c.id == result.inserted_primary_key[0]
        )).mappings().one()
        return dict(row), False


def refresh_names(*, force: bool = False) -> dict:
    """Refresh stale entries without holding a DB transaction during HTTP calls."""
    now = config.utcnow()
    cutoff = now - timedelta(days=max(config.LOUNGE_NAME_REFRESH_DAYS, 1))
    query = select(
        do_not_mogi_players.c.lounge_player_id,
        do_not_mogi_players.c.name,
    )
    if not force:
        query = query.where(do_not_mogi_players.c.last_refreshed_at <= cutoff)
    with db.read() as conn:
        due = [dict(row) for row in conn.execute(query).mappings()]

    refreshed = changed = failed = 0
    for stored in due:
        try:
            player = lounge.get_player(stored["lounge_player_id"])
            checked_at = config.utcnow()
            with db.connect() as conn:
                conn.execute(update(do_not_mogi_players).where(
                    do_not_mogi_players.c.lounge_player_id
                    == stored["lounge_player_id"]
                ).values(
                    name=player["name"],
                    country_code=player["country_code"],
                    last_refreshed_at=checked_at,
                    updated_at=checked_at,
                ))
            refreshed += 1
            changed += int(player["name"] != stored["name"])
        except lounge.LoungeError as exc:
            failed += 1
            log.warning("could not refresh Lounge player %s: %s",
                        stored["lounge_player_id"], exc)
    result = {
        "due": len(due), "refreshed": refreshed,
        "names_changed": changed, "failed": failed,
    }
    if due:
        log.info("Lounge name refresh: %s", result)
    return result
