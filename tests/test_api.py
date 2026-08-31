"""Route tests: field autosave, completeness, exports, and the constraints that stop
bad data getting in."""
import datetime as dt

import pytest
from sqlalchemy import select, update

from app import config
from app.queries import create_session, session_stats, session_row, race_rows
from app.schema import races, sessions, shock_events, tracks


@pytest.fixture
def session_id(engine):
    with engine.begin() as c:
        return create_session(c, fmt="ffa")


def test_new_session_renders_expected_rows(client):
    r = client.post("/sessions", data={"fmt": "ffa"}, follow_redirects=False)
    assert r.status_code == 303
    sid = int(r.headers["location"].rsplit("/", 1)[1])
    body = client.get(f"/sessions/{sid}").text
    assert body.count('class="race-row"') == 12


def test_new_session_defaults_to_nearest_hour(client, engine, monkeypatch):
    monkeypatch.setattr(
        config, "utcnow", lambda: dt.datetime(2026, 8, 31, 23, 40, 27, 123456)
    )

    response = client.post("/sessions", follow_redirects=False)
    session_id = int(response.headers["location"].rsplit("/", 1)[1])

    with engine.begin() as conn:
        played_at = conn.execute(
            select(sessions.c.played_at).where(sessions.c.id == session_id)
        ).scalar_one()
    assert played_at == dt.datetime(2026, 9, 1, 0, 0)


def test_session_renders_derived_mmr_after(client, session_id):
    client.post(f"/api/sessions/{session_id}/field",
                json={"field": "own_mmr_before", "value": 4000})
    client.post(f"/api/sessions/{session_id}/field",
                json={"field": "mmr_delta", "value": 46})
    body = client.get(f"/sessions/{session_id}").text
    assert 'id="mmr-after"' in body
    assert 'value="4046"' in body


def test_sessions_summary_shows_current_mmr_and_last_ten_delta(client, engine, session_id):
    with engine.begin() as conn:
        conn.execute(update(sessions).where(sessions.c.id == session_id).values(
            own_mmr_before=4000, mmr_delta=0,
        ))
    body = client.get("/").text
    assert "Current MMR" in body
    assert "+0" in body
    assert "over last 10" in body
    assert "Incomplete" not in body
    assert "text-good-400" in body


def test_played_at_rounds_to_nearest_hour(client, engine, session_id):
    response = client.post(
        f"/api/sessions/{session_id}/field",
        json={"field": "played_at", "value": "2026-08-21T12:40"},
    )
    assert response.status_code == 200
    with engine.begin() as conn:
        played_at = conn.execute(
            select(sessions.c.played_at).where(sessions.c.id == session_id)
        ).scalar_one()
    assert played_at == dt.datetime(2026, 8, 21, 13, 0)

    body = client.get(f"/sessions/{session_id}").text
    assert 'type="datetime-local" step="3600"' in body


def test_race_header_uses_the_same_note_width_as_rows(client, session_id):
    body = client.get(f"/sessions/{session_id}").text
    assert '<span class="min-w-[6rem] flex-1 sm:max-w-[20rem]">Note</span>' in body


def test_start_column_precedes_place_column(client, session_id):
    body = client.get(f"/sessions/{session_id}").text
    assert body.index('>Start</span>') < body.index('>Place</span>')
    first_row = body[body.index('class="race-row"'):]
    assert first_row.index('data-field="start_position"') < first_row.index('data-field="placement"')


def test_tournament_defaults_to_eight_races(client):
    r = client.post("/sessions", data={"fmt": "tournament"}, follow_redirects=False)
    sid = int(r.headers["location"].rsplit("/", 1)[1])
    assert client.get(f"/api/sessions/{sid}").json()["session"]["expected_races"] == 8


def test_saving_a_placement_updates_the_running_average(client, session_id):
    for num, place in ((1, 4), (2, 8)):
        r = client.post(f"/api/sessions/{session_id}/races/{num}/field",
                        json={"field": "placement", "value": place})
        assert r.status_code == 200
    stats = r.json()["stats"]
    assert stats["placements_recorded"] == 2
    assert stats["avg_placement"] == pytest.approx(6.0)
    assert stats["is_complete"] is False


def test_saving_a_placement_infers_the_next_race_start(client, engine, session_id):
    r = client.post(f"/api/sessions/{session_id}/races/1/field",
                    json={"field": "placement", "value": 4})

    assert r.json()["inferred_start"] == {"race_num": 2, "start_position": 4}
    with engine.begin() as c:
        rows = race_rows(c, session_id)
    assert rows[1]["start_position"] == 4


def test_clearing_a_placement_clears_the_inferred_start(client, engine, session_id):
    client.post(f"/api/sessions/{session_id}/races/1/field",
                json={"field": "placement", "value": 4})
    r = client.post(f"/api/sessions/{session_id}/races/1/field",
                    json={"field": "placement", "value": ""})

    assert r.json()["inferred_start"] == {"race_num": 2, "start_position": None}
    with engine.begin() as c:
        rows = race_rows(c, session_id)
    assert rows[1]["start_position"] is None


def test_completeness_needs_every_expected_race(client, session_id):
    for num in range(1, 13):
        client.post(f"/api/sessions/{session_id}/races/{num}/field",
                    json={"field": "placement", "value": 6})
    assert client.get(f"/api/sessions/{session_id}").json()["stats"]["is_complete"] is True


def test_aborted_resolves_an_otherwise_permanently_incomplete_session(client, session_id):
    client.post(f"/api/sessions/{session_id}/races/1/field",
                json={"field": "placement", "value": 5})
    assert client.get(f"/api/sessions/{session_id}").json()["stats"]["is_complete"] is False
    r = client.post(f"/api/sessions/{session_id}/field",
                    json={"field": "aborted", "value": True})
    assert r.json()["stats"]["is_complete"] is True


def test_expected_races_change_adds_and_trims_blank_rows(client, session_id):
    r = client.post(f"/api/sessions/{session_id}/field",
                    json={"field": "expected_races", "value": 8})
    assert r.json()["n_rows"] == 8
    r = client.post(f"/api/sessions/{session_id}/field",
                    json={"field": "expected_races", "value": 14})
    assert r.json()["n_rows"] == 14


def test_shrinking_never_deletes_a_race_that_carries_data(client, session_id):
    client.post(f"/api/sessions/{session_id}/races/12/field",
                json={"field": "placement", "value": 3})
    r = client.post(f"/api/sessions/{session_id}/field",
                    json={"field": "expected_races", "value": 4})
    # Trimming stops at the first row from the end that carries data, so nothing is
    # removed here. Deleting rows 5-11 around a logged race 12 would both lose the
    # contiguous block and make the row numbering a lie.
    assert r.json()["n_rows"] == 12

    # Clear it and the trim goes through.
    client.post(f"/api/sessions/{session_id}/races/12/field",
                json={"field": "placement", "value": ""})
    r = client.post(f"/api/sessions/{session_id}/field",
                    json={"field": "expected_races", "value": 4})
    assert r.json()["n_rows"] == 4


def test_placement_outside_the_lobby_is_rejected(client, session_id):
    for bad in (0, 13, 99):
        r = client.post(f"/api/sessions/{session_id}/races/1/field",
                        json={"field": "placement", "value": bad})
        assert r.status_code == 400


def test_blank_clears_a_field_rather_than_erroring(client, session_id):
    client.post(f"/api/sessions/{session_id}/races/1/field",
                json={"field": "placement", "value": 7})
    r = client.post(f"/api/sessions/{session_id}/races/1/field",
                    json={"field": "placement", "value": ""})
    assert r.status_code == 200 and r.json()["value"] is None


def test_unknown_field_is_refused(client, session_id):
    r = client.post(f"/api/sessions/{session_id}/races/1/field",
                    json={"field": "drop table", "value": 1})
    assert r.status_code == 400


def test_shortcut_hit_accepts_four_states_and_rejects_others(client, session_id):
    for value in ("hit", "miss", "na", ""):
        r = client.post(f"/api/sessions/{session_id}/races/1/field",
                        json={"field": "shortcut_hit", "value": value})
        assert r.status_code == 200
    assert r.json()["value"] is None            # "" means not recorded, not 'na'
    r = client.post(f"/api/sessions/{session_id}/races/1/field",
                    json={"field": "shortcut_hit", "value": "maybe"})
    assert r.status_code == 400


def test_setting_a_track_reports_whether_it_has_a_gate(client, engine, session_id):
    with engine.begin() as c:
        gate = c.execute(select(tracks.c.id).where(tracks.c.code == "BC")).scalar()
        plain = c.execute(select(tracks.c.id).where(tracks.c.code == "MBC")).scalar()

    r = client.post(f"/api/sessions/{session_id}/races/1/field",
                    json={"field": "track_id", "value": gate})
    assert r.json()["race"]["has_gate"] is True
    r = client.post(f"/api/sessions/{session_id}/races/2/field",
                    json={"field": "track_id", "value": plain})
    assert r.json()["race"]["has_gate"] is False


def test_the_same_track_twice_in_one_session_is_allowed(client, engine, session_id):
    with engine.begin() as c:
        raf = c.execute(select(tracks.c.id).where(tracks.c.code == "rAF")).scalar()
    for num in (1, 11):
        r = client.post(f"/api/sessions/{session_id}/races/{num}/field",
                        json={"field": "track_id", "value": raf})
        assert r.status_code == 200


def test_track_search_endpoint_reports_auto_commit(client):
    assert client.get("/api/tracks/search?q=raf").json()["auto_commit"] is True
    # `bc` is an exact hit but `bci` shadows it, so it waits for Enter.
    body = client.get("/api/tracks/search?q=bc").json()
    assert body["results"][0]["code"] == "BC"
    assert body["auto_commit"] is False


def test_add_alias_then_it_resolves(client, engine):
    with engine.begin() as c:
        bc = c.execute(select(tracks.c.id).where(tracks.c.code == "BC")).scalar()
    assert client.post(f"/api/tracks/{bc}/aliases", json={"alias": "bowser"}).status_code == 200
    assert client.get("/api/tracks/search?q=bowser").json()["results"][0]["code"] == "BC"


def test_alias_collision_is_refused_rather_than_stolen(client, engine):
    with engine.begin() as c:
        ws = c.execute(select(tracks.c.id).where(tracks.c.code == "WS")).scalar()
    r = client.post(f"/api/tracks/{ws}/aliases", json={"alias": "castle"})
    assert r.status_code == 409


def test_variant_defaults_to_3lap_and_toggles(client, session_id):
    assert client.get(f"/api/sessions/{session_id}").json()["races"][0]["variant"] == "3lap"
    r = client.post(f"/api/sessions/{session_id}/races/1/field",
                    json={"field": "variant", "value": "intermission"})
    assert r.json()["race"]["variant"] == "intermission"


def test_add_and_drop_race_rows(client, session_id):
    assert client.post(f"/api/sessions/{session_id}/races").json()["race_num"] == 13
    assert client.delete(f"/api/sessions/{session_id}/races/last").status_code == 200
    client.post(f"/api/sessions/{session_id}/races/12/field",
                json={"field": "placement", "value": 4})
    r = client.delete(f"/api/sessions/{session_id}/races/last")
    assert r.status_code == 400          # would silently delete a logged race


def test_pages_render(client, session_id):
    for path in ("/", "/analytics", "/shocks", "/do-not-mogi", "/settings", "/import",
                 f"/sessions/{session_id}", f"/sessions/{session_id}/delete"):
        assert client.get(path).status_code == 200, path


def test_analytics_page_has_sorting_explanations_and_score_chart(client, engine, session_id):
    with engine.begin() as conn:
        track_id = conn.execute(select(tracks.c.id).where(tracks.c.code == "BC")).scalar_one()
        conn.execute(update(sessions).where(sessions.c.id == session_id)
                     .values(score=88, room_avg_mmr=4000,
                             own_mmr_before=3900, mmr_delta=100))
        conn.execute(update(races)
                     .where((races.c.session_id == session_id) & (races.c.race_num == 1))
                     .values(track_id=track_id, placement=4))

    body = client.get("/analytics").text
    assert 'id="track-table"' in body
    assert body.count('data-sort-type=') == 9
    assert "standard deviation of placements" in body
    assert "How to read the session model" in body
    assert "Adj R²" in body
    assert 'id="score-chart"' in body
    assert 'id="mmr-chart"' in body
    assert 'aria-label="Score summary"' in body
    assert "Median" in body
    assert "Scored sessions" in body
    assert 'data-score-mode="weighted"' in body
    assert 'src="/static/analytics.js"' in body
    payload = client.get("/api/analytics").json()
    assert payload["score"]["points"][0]["score"] == 88
    assert payload["score"]["summary"]["median"] == 88


def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"


def test_shocks_page_has_all_30_standard_minimaps(client):
    body = client.get("/shocks").text
    assert body.count('class="card shock-card"') == 30
    assert body.count('class="shock-map-target"') == 30
    assert '/static/minimaps/mbc.png' in body
    assert '/static/minimaps/rr.png' in body
    assert 'src="/static/shocks.js"' in body


def test_add_filter_and_undo_shock(client, engine):
    with engine.begin() as conn:
        track_id = conn.execute(
            select(tracks.c.id).where(tracks.c.code == "MBC")
        ).scalar_one()

    response = client.post("/api/shocks", json={
        "track_id": track_id, "x": 0.25, "y": 0.75, "lap": 2,
    })
    assert response.status_code == 201
    event = response.json()["event"]
    assert event == {"id": event["id"], "track_id": track_id,
                     "x": 0.25, "y": 0.75, "lap": 2}

    assert client.get(f"/api/shocks?track_id={track_id}&lap=1").json()["events"] == []
    assert client.get(f"/api/shocks?track_id={track_id}&lap=2").json()["events"] == [event]
    assert client.delete(f'/api/shocks/{event["id"]}').status_code == 200
    with engine.begin() as conn:
        assert conn.execute(select(shock_events)).all() == []


def test_shock_input_is_bounded_and_accepts_rainbow_road(client, engine):
    with engine.begin() as conn:
        mbc = conn.execute(select(tracks.c.id).where(tracks.c.code == "MBC")).scalar_one()
        rainbow = conn.execute(select(tracks.c.id).where(tracks.c.code == "RR")).scalar_one()

    for payload in (
        {"track_id": mbc, "x": -0.01, "y": 0.5, "lap": 1},
        {"track_id": mbc, "x": 0.5, "y": 1.01, "lap": 1},
        {"track_id": mbc, "x": 0.5, "y": 0.5, "lap": 4},
    ):
        assert client.post("/api/shocks", json=payload).status_code == 400

    response = client.post("/api/shocks", json={
        "track_id": rainbow, "x": 0.5, "y": 0.5, "lap": 1,
    })
    assert response.status_code == 201
    assert client.delete(f'/api/shocks/{response.json()["event"]["id"]}').status_code == 200


def test_404_is_html_for_pages_and_json_for_api(client):
    assert "text/html" in client.get("/nope").headers["content-type"]
    assert client.get("/api/sessions/9999").status_code == 404


def test_csv_export_contains_raw_session_and_race_data_without_stats(
        client, session_id):
    client.post(f"/api/sessions/{session_id}/field",
                json={"field": "score", "value": 91})
    client.post(f"/api/sessions/{session_id}/races/1/field",
                json={"field": "placement", "value": 2})

    import csv
    import io
    rows = list(csv.DictReader(io.StringIO(client.get("/export/races.csv").text)))

    assert len(rows) == 12
    assert rows[0]["session_id"] == str(session_id)
    assert rows[0]["score"] == "91"
    assert rows[0]["race_num"] == "1"
    assert rows[0]["placement"] == "2"
    assert "session_created_at" in rows[0] and "race_created_at" in rows[0]
    for computed in ("is_complete", "mmr_spread", "session_avg_placement",
                     "loo_baseline", "residual"):
        assert computed not in rows[0]


def test_json_export_covers_every_table(client, session_id):
    payload = client.get("/export/db.json").json()
    for table in ("tracks", "track_aliases", "sessions", "races", "shock_events",
                  "do_not_mogi_players", "lounge_mmr_cache", "import_issues"):
        assert table in payload
    assert len(payload["tracks"]) == 30


def test_json_export_rows_match_raw_table_columns(client, engine, session_id):
    client.post(f"/api/sessions/{session_id}/field",
                json={"field": "score", "value": 87})
    client.post(f"/api/sessions/{session_id}/races/1/field",
                json={"field": "placement", "value": 3})
    payload = client.get("/export/db.json").json()

    assert set(payload) == {
        "tracks", "track_aliases", "sessions", "races", "shock_events",
        "do_not_mogi_players", "lounge_mmr_cache", "import_issues",
    }
    assert set(payload["sessions"][0]) == set(sessions.c.keys())
    assert set(payload["races"][0]) == set(races.c.keys())
    exported_session = next(row for row in payload["sessions"] if row["id"] == session_id)
    exported_race = next(row for row in payload["races"]
                         if row["session_id"] == session_id and row["race_num"] == 1)
    assert exported_session["score"] == 87
    assert exported_race["placement"] == 3


def test_delete_session_removes_its_races(client, engine, session_id):
    assert client.delete(f"/api/sessions/{session_id}").status_code == 200
    with engine.begin() as c:
        assert c.execute(select(races).where(races.c.session_id == session_id)).all() == []


def test_sessions_page_links_to_delete_confirmation(client, session_id):
    response = client.get("/")
    assert response.status_code == 200
    assert f'href="/sessions/{session_id}/delete"' in response.text
