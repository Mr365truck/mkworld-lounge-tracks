"""Persistence and weekly display-name refresh for the Do Not Mogi list."""
import logging
import re
from datetime import timedelta

from sqlalchemy import insert, select, update

from . import config, db, lounge
from .schema import do_not_mogi_players

log = logging.getLogger("mogi.do_not_mogi")

QUEUE_ENTRY_RE = re.compile(
    r"^\s*(?P<rank>\d+)\.\s+(?P<name>.+?)\s+"
    r"\((?P<mmr>[\d,]+)\s+MMR\)\s*(?:\*)?\s*$",
    re.IGNORECASE,
)


class QueueCheckError(ValueError):
    """The pasted queue could not identify a usable 12-player room."""


def _name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


def check_queue(conn, text: str, own_name: str) -> dict:
    """Locate the configured player and flag blocked names in the same rank group."""
    entries = []
    seen_ranks = set()
    for line in text.splitlines():
        match = QUEUE_ENTRY_RE.match(line)
        if match is None:
            continue
        rank = int(match.group("rank"))
        if rank < 1 or rank in seen_ranks:
            raise QueueCheckError("Queue ranks must be unique positive numbers")
        seen_ranks.add(rank)
        entries.append({
            "rank": rank,
            "name": match.group("name").strip(),
            "mmr": int(match.group("mmr").replace(",", "")),
        })

    if not entries:
        raise QueueCheckError("No numbered Lounge queue entries were found")

    own_key = _name_key(own_name)
    own_entries = [entry for entry in entries if _name_key(entry["name"]) == own_key]
    if not own_entries:
        raise QueueCheckError(f"Could not find {own_name} in the pasted queue")
    if len(own_entries) > 1:
        raise QueueCheckError(f"Found {own_name} more than once in the pasted queue")

    own_entry = own_entries[0]
    room_number = (own_entry["rank"] - 1) // 12 + 1
    first_rank = (room_number - 1) * 12 + 1
    last_rank = first_rank + 11
    room = sorted(
        (entry for entry in entries if first_rank <= entry["rank"] <= last_rank),
        key=lambda entry: entry["rank"],
    )

    blocked_by_name = {}
    for player in list_players(conn):
        blocked_by_name.setdefault(_name_key(player["name"]), []).append(player)

    matches = []
    for entry in room:
        for blocked in blocked_by_name.get(_name_key(entry["name"]), []):
            matches.append({
                **entry,
                "lounge_player_id": blocked["lounge_player_id"],
                "saved_name": blocked["name"],
            })

    return {
        "own_player": own_entry,
        "room_number": room_number,
        "first_rank": first_rank,
        "last_rank": last_rank,
        "room": room,
        "matches": matches,
        "parsed_players": len(entries),
    }


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
