"""Route tests: field autosave, completeness, exports, and the constraints that stop
bad data getting in."""
import pytest
from sqlalchemy import select, update

from app.queries import create_session, session_stats, session_row, race_rows
from app.schema import races, sessions, tracks


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


def test_session_renders_derived_mmr_after(client, session_id):
    client.post(f"/api/sessions/{session_id}/field",
                json={"field": "own_mmr_before", "value": 4000})
    client.post(f"/api/sessions/{session_id}/field",
                json={"field": "mmr_delta", "value": 46})
    body = client.get(f"/sessions/{session_id}").text
    assert 'id="mmr-after"' in body
    assert 'value="4046"' in body


def test_race_header_uses_the_same_note_width_as_rows(client, session_id):
    body = client.get(f"/sessions/{session_id}").text
    assert '<span class="min-w-[6rem] flex-1 sm:max-w-[20rem]">Note</span>' in body


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
    for path in ("/", "/analytics", "/settings", "/import",
                 f"/sessions/{session_id}", f"/sessions/{session_id}/delete"):
        assert client.get(path).status_code == 200, path


def test_analytics_page_has_sorting_explanations_and_score_chart(client, engine, session_id):
    with engine.begin() as conn:
        track_id = conn.execute(select(tracks.c.id).where(tracks.c.code == "BC")).scalar_one()
        conn.execute(update(sessions).where(sessions.c.id == session_id)
                     .values(score=88, room_avg_mmr=4000))
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
    assert 'data-score-mode="weighted"' in body
    assert 'src="/static/analytics.js"' in body
    payload = client.get("/api/analytics").json()
    assert payload["score"]["points"][0]["score"] == 88


def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"


def test_404_is_html_for_pages_and_json_for_api(client):
    assert "text/html" in client.get("/nope").headers["content-type"]
    assert client.get("/api/sessions/9999").status_code == 404


def test_csv_export_has_the_residual_columns(client, session_id):
    for num, place in ((1, 2), (2, 6), (3, 10)):
        client.post(f"/api/sessions/{session_id}/races/{num}/field",
                    json={"field": "placement", "value": place})
    body = client.get("/export/races.csv").text
    header = body.splitlines()[0].split(",")
    assert "residual" in header and "loo_baseline" in header
    assert len(body.splitlines()) == 13      # header + 12 rows


def test_json_export_covers_every_table(client, session_id):
    payload = client.get("/export/db.json").json()
    for table in ("tracks", "track_aliases", "sessions", "races", "import_issues"):
        assert table in payload
    assert len(payload["tracks"]) == 30


def test_delete_session_removes_its_races(client, engine, session_id):
    assert client.delete(f"/api/sessions/{session_id}").status_code == 200
    with engine.begin() as c:
        assert c.execute(select(races).where(races.c.session_id == session_id)).all() == []


def test_sessions_page_links_to_delete_confirmation(client, session_id):
    response = client.get("/")
    assert response.status_code == 200
    assert f'href="/sessions/{session_id}/delete"' in response.text
