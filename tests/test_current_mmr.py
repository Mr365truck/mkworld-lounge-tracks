"""MKCentral current-MMR cache and manual session override behavior."""
import datetime as dt

from sqlalchemy import select

from app import config, current_mmr
from app.queries import create_session
from app.schema import lounge_mmr_cache


def _player(mmr):
    return {
        "lounge_player_id": 67656,
        "name": "Mr365truck",
        "country_code": "US",
        "mmr": mmr,
        "rank": 2632,
        "events_played": 54,
        "season": 3,
    }


def test_landing_mmr_uses_mkcentral_then_manual_session_until_next_refresh(
        client, engine, monkeypatch):
    now = [dt.datetime(2026, 8, 31, 5)]
    live_mmr = [4200]
    monkeypatch.setattr(config, "utcnow", lambda: now[0])
    monkeypatch.setattr(
        current_mmr.lounge, "get_leaderboard_player",
        lambda player_id: _player(live_mmr[0]),
    )

    assert current_mmr.refresh()["mmr"] == 4200
    assert "4,200" in client.get("/").text

    now[0] = dt.datetime(2026, 8, 31, 20)
    with engine.begin() as conn:
        session_id = create_session(conn)
    client.post(f"/api/sessions/{session_id}/field", json={
        "field": "own_mmr_before", "value": 4200,
    })
    client.post(f"/api/sessions/{session_id}/field", json={
        "field": "mmr_delta", "value": 50,
    })
    assert "4,250" in client.get("/").text

    now[0] = dt.datetime(2026, 9, 1, 5)
    live_mmr[0] = 4248
    assert current_mmr.refresh()["mmr"] == 4248
    body = client.get("/").text
    assert "4,248" in body
    assert "4,250" not in body


def test_failed_refresh_keeps_the_last_good_mmr(engine, monkeypatch):
    from app import lounge

    monkeypatch.setattr(
        current_mmr.lounge, "get_leaderboard_player", lambda player_id: _player(4236)
    )
    current_mmr.refresh()

    def unavailable(player_id):
        raise lounge.LoungeError("Lounge is temporarily unavailable")

    monkeypatch.setattr(current_mmr.lounge, "get_leaderboard_player", unavailable)
    result = current_mmr.refresh()

    assert result == {
        "updated": False, "error": "Lounge is temporarily unavailable",
    }
    with engine.connect() as conn:
        assert conn.execute(select(lounge_mmr_cache.c.mmr)).scalar_one() == 4236


def test_next_refresh_is_24_hours_after_the_cached_update(engine, monkeypatch):
    now = dt.datetime(2026, 8, 31, 5)
    monkeypatch.setattr(config, "utcnow", lambda: now)
    monkeypatch.setattr(config, "LOUNGE_MMR_REFRESH_HOURS", 24)
    monkeypatch.setattr(
        current_mmr.lounge, "get_leaderboard_player", lambda player_id: _player(4236)
    )
    current_mmr.refresh()

    assert current_mmr.next_refresh_at() == dt.datetime(
        2026, 9, 1, 5, tzinfo=dt.timezone.utc
    )
