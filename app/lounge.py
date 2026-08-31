"""Small client for MKCentral's public MKWorld Lounge player API.

The leaderboard endpoint is the same search used by lounge.mkcentral.com. The
single-player endpoint accepts its stable player ID, which lets us follow renames
without trying to find a player by an obsolete display name.
"""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import config


class LoungeError(RuntimeError):
    """The public Lounge API could not provide a usable response."""


def game_label() -> str:
    return {
        "mkworld": "MKWorld",
        "mkworld12p": "MKWorld 12P",
        "mkworld24p": "MKWorld 24P",
    }.get(config.LOUNGE_GAME, config.LOUNGE_GAME)


def profile_url(lounge_player_id: int) -> str:
    player_id = int(lounge_player_id)
    p = "12" if config.LOUNGE_GAME == "mkworld12p" else (
        "24" if config.LOUNGE_GAME == "mkworld24p" else None
    )
    query = f"?p={p}" if p else ""
    return f"{config.LOUNGE_BASE_URL.rstrip('/')}/mkworld/PlayerDetails/{player_id}{query}"


def _get_json(path: str, params: dict) -> dict:
    base = config.LOUNGE_BASE_URL.rstrip("/")
    url = f"{base}{path}?{urlencode(params)}"
    request = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "mogi-tracker/1.0",
    })
    try:
        with urlopen(request, timeout=config.LOUNGE_HTTP_TIMEOUT) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise LoungeError(f"Lounge returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise LoungeError("Lounge is temporarily unavailable") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LoungeError("Lounge returned an invalid response") from exc
    if not isinstance(payload, dict):
        raise LoungeError("Lounge returned an invalid response")
    return payload


def _identity_fields(raw: dict) -> dict:
    try:
        player_id = int(raw["id"])
        raw_name = raw["name"]
        name = str(raw_name).strip() if raw_name is not None else ""
    except (KeyError, TypeError, ValueError) as exc:
        raise LoungeError("Lounge returned an incomplete player") from exc
    if player_id < 1 or not name:
        raise LoungeError("Lounge returned an incomplete player")

    out = {
        "lounge_player_id": player_id,
        "name": name,
        "country_code": (str(raw["countryCode"]).upper()
                         if raw.get("countryCode") else None),
    }
    return out


def _leaderboard_fields(raw: dict) -> dict:
    out = _identity_fields(raw)
    try:
        out.update({
            "mmr": int(raw["mmr"]) if raw.get("mmr") is not None else None,
            "rank": (int(raw["overallRank"])
                     if raw.get("overallRank") is not None else None),
            "events_played": int(raw.get("eventsPlayed") or 0),
        })
    except (TypeError, ValueError) as exc:
        raise LoungeError("Lounge returned an incomplete leaderboard player") from exc
    return out


def search_players(query: str, limit: int = 8) -> dict:
    query = (query or "").strip()
    if not query:
        return {"query": query, "season": None, "total": 0, "results": []}
    limit = max(1, min(int(limit), 20))
    payload = _get_json("/api/player/leaderboard", {
        "game": config.LOUNGE_GAME,
        "search": query,
        "pageSize": limit,
    })
    try:
        raw_players = payload["data"]
        season = int(payload["season"])
        total = int(payload["totalPlayers"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LoungeError("Lounge returned an invalid leaderboard") from exc
    if not isinstance(raw_players, list):
        raise LoungeError("Lounge returned an invalid leaderboard")
    return {
        "query": query,
        "season": season,
        "total": total,
        "results": [_leaderboard_fields(player) for player in raw_players[:limit]],
    }


def get_player(lounge_player_id: int) -> dict:
    try:
        lounge_player_id = int(lounge_player_id)
    except (TypeError, ValueError) as exc:
        raise LoungeError("invalid Lounge player ID") from exc
    if lounge_player_id < 1:
        raise LoungeError("invalid Lounge player ID")
    # Unlike a season-specific game record, this identity survives the start of a
    # new season before the player has raced. It also avoids persisting account-only
    # fields included in the response.
    payload = _get_json("/api/player/allgames", {
        "id": lounge_player_id,
    })
    player = _identity_fields(payload)
    if player["lounge_player_id"] != lounge_player_id:
        raise LoungeError("Lounge returned the wrong player")
    return player


def get_leaderboard_player(lounge_player_id: int) -> dict:
    """Return a stable-ID-matched player from the current configured leaderboard."""
    identity = get_player(lounge_player_id)
    leaderboard = search_players(identity["name"], limit=20)
    player = next(
        (item for item in leaderboard["results"]
         if item["lounge_player_id"] == identity["lounge_player_id"]),
        None,
    )
    if player is None or player["mmr"] is None:
        raise LoungeError("player is not on the current Lounge leaderboard")
    return {**player, "season": leaderboard["season"]}
