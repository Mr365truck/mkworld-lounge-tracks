"""Do Not Mogi list, Lounge search, and rename refresh tests."""
import datetime as dt

from sqlalchemy import select, update

from app import backup, config, current_mmr, do_not_mogi, lounge
from app.schema import do_not_mogi_players


def player(player_id=26176, name="Li4z"):
    return {
        "lounge_player_id": player_id,
        "name": name,
        "country_code": "JP",
    }


def test_adds_a_canonical_lounge_player_and_renders_the_page(
        client, engine, monkeypatch):
    monkeypatch.setattr(do_not_mogi.lounge, "get_player", lambda player_id: player(player_id))

    response = client.post("/api/do-not-mogi", json={"lounge_player_id": 26176})

    assert response.status_code == 201
    assert response.json()["player"]["name"] == "Li4z"
    with engine.connect() as conn:
        stored = conn.execute(select(do_not_mogi_players)).mappings().one()
    assert stored["lounge_player_id"] == 26176
    assert stored["country_code"] == "JP"
    page = client.get("/do-not-mogi")
    assert page.status_code == 200
    assert "Do Not Mogi" in page.text
    assert "Li4z" in page.text
    assert "PlayerDetails/26176?p=12" in page.text


def test_duplicate_add_is_idempotent(client, engine, monkeypatch):
    monkeypatch.setattr(do_not_mogi.lounge, "get_player", lambda player_id: player(player_id))
    client.post("/api/do-not-mogi", json={"lounge_player_id": 26176})
    response = client.post("/api/do-not-mogi", json={"lounge_player_id": 26176})

    assert response.status_code == 201
    assert response.json()["already"] is True
    with engine.connect() as conn:
        assert len(conn.execute(select(do_not_mogi_players)).all()) == 1


def test_leaderboard_search_marks_people_already_on_the_list(
        client, monkeypatch):
    monkeypatch.setattr(do_not_mogi.lounge, "get_player", lambda player_id: player(player_id))
    client.post("/api/do-not-mogi", json={"lounge_player_id": 26176})
    monkeypatch.setattr(lounge, "search_players", lambda query, limit=8: {
        "query": query,
        "season": 3,
        "total": 2,
        "results": [
            {**player(26176), "mmr": 10562, "rank": 93, "events_played": 5},
            {**player(99, "Somebody"), "mmr": 9000,
             "rank": 94, "events_played": 8},
        ],
    })

    body = client.get("/api/do-not-mogi/search?q=li").json()

    assert body["results"][0]["listed"] is True
    assert body["results"][1]["listed"] is False


def test_weekly_refresh_uses_stable_id_and_skips_fresh_rows(
        client, engine, monkeypatch):
    replies = [player(name="Old name"), player(name="New name")]
    calls = []

    def get_player(player_id):
        calls.append(player_id)
        return replies.pop(0)

    monkeypatch.setattr(do_not_mogi.lounge, "get_player", get_player)
    client.post("/api/do-not-mogi", json={"lounge_player_id": 26176})

    assert do_not_mogi.refresh_names() == {
        "due": 0, "refreshed": 0, "names_changed": 0, "failed": 0,
    }
    assert calls == [26176]

    stale = config.utcnow() - dt.timedelta(days=config.LOUNGE_NAME_REFRESH_DAYS + 1)
    with engine.begin() as conn:
        conn.execute(update(do_not_mogi_players).values(last_refreshed_at=stale))
    result = do_not_mogi.refresh_names()

    assert result == {"due": 1, "refreshed": 1, "names_changed": 1, "failed": 0}
    assert calls == [26176, 26176]
    with engine.connect() as conn:
        stored = conn.execute(select(do_not_mogi_players)).mappings().one()
    assert stored["name"] == "New name"


def test_remove_player_and_json_export(client, monkeypatch):
    monkeypatch.setattr(do_not_mogi.lounge, "get_player", lambda player_id: player(player_id))
    client.post("/api/do-not-mogi", json={"lounge_player_id": 26176})

    exported = client.get("/export/db.json").json()
    assert exported["do_not_mogi_players"][0]["lounge_player_id"] == 26176
    assert client.delete("/api/do-not-mogi/26176").status_code == 200
    assert client.get("/api/do-not-mogi").json()["players"] == []
    assert client.delete("/api/do-not-mogi/26176").status_code == 404


def test_lounge_failure_is_reported_as_bad_gateway(client, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise lounge.LoungeError("Lounge is temporarily unavailable")

    monkeypatch.setattr(lounge, "search_players", unavailable)
    response = client.get("/api/do-not-mogi/search?q=someone")
    assert response.status_code == 502
    assert response.json()["detail"] == "Lounge is temporarily unavailable"


def test_lounge_client_omits_season_so_upstream_uses_current(monkeypatch):
    calls = []

    def fake_get(path, params):
        calls.append((path, params))
        return {
            "totalPlayers": 1,
            "game": "mkworld12p",
            "season": 3,
            "data": [{
                "id": 26176, "name": "Li4z", "countryCode": "JP",
                "mmr": 10562, "overallRank": 93, "eventsPlayed": 5,
            }],
        }

    monkeypatch.setattr(lounge, "_get_json", fake_get)
    result = lounge.search_players("Li4z")

    assert result["results"][0]["lounge_player_id"] == 26176
    assert calls[0][0] == "/api/player/leaderboard"
    assert "season" not in calls[0][1]


def test_name_refresh_identity_is_not_tied_to_a_season(monkeypatch):
    calls = []

    def fake_get(path, params):
        calls.append((path, params))
        return {"id": 26176, "name": "Li4z", "countryCode": "JP"}

    monkeypatch.setattr(lounge, "_get_json", fake_get)
    result = lounge.get_player(26176)

    assert result == player()
    assert calls == [("/api/player/allgames", {"id": 26176})]


def test_current_mmr_lookup_matches_the_stable_player_id(monkeypatch):
    monkeypatch.setattr(lounge, "get_player", lambda player_id: player(player_id))
    monkeypatch.setattr(lounge, "search_players", lambda query, limit=8: {
        "query": query,
        "season": 3,
        "total": 2,
        "results": [
            {**player(99, "Similar"), "mmr": 9000, "rank": 1,
             "events_played": 10},
            {**player(), "mmr": 4236, "rank": 2632, "events_played": 54},
        ],
    })

    result = lounge.get_leaderboard_player(26176)

    assert result["lounge_player_id"] == 26176
    assert result["mmr"] == 4236
    assert result["season"] == 3


def test_name_refresh_is_scheduled_even_when_backups_are_disabled(engine, monkeypatch):
    monkeypatch.setattr(config, "BACKUP_ENABLED", False)
    monkeypatch.setattr(config, "LOUNGE_REFRESH_ENABLED", True)
    monkeypatch.setattr(current_mmr, "refresh", lambda: {"updated": False})
    scheduler = backup.start_scheduler()
    try:
        assert {job.id for job in scheduler.get_jobs()} == {
            "current-lounge-mmr-refresh", "do-not-mogi-name-refresh",
        }
        mmr_job = scheduler.get_job("current-lounge-mmr-refresh")
        assert mmr_job.trigger.interval == dt.timedelta(hours=24)
    finally:
        scheduler.shutdown(wait=False)
