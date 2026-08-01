"""Analytics tests — spec section 6.

The leave-one-out residual gets golden numbers because it is exactly the class of
error that ships silently: a self-inclusive baseline still produces plausible-looking
output, just biased toward zero by roughly 1 - 1/n.
"""
import math

import pandas as pd
import pytest
from sqlalchemy import select

from app import analytics
from app.queries import create_session
from app.schema import races, tracks


def _seed_session(conn, placements, fmt="ffa", tracks_by_code=None, **session_kwargs):
    """Create a session whose races carry `placements` (None allowed)."""
    from sqlalchemy import update
    from app.schema import sessions as S
    sid = create_session(conn, fmt=fmt, expected_races=len(placements))
    if session_kwargs:
        conn.execute(update(S).where(S.c.id == sid).values(**session_kwargs))
    codes = tracks_by_code or []
    for i, p in enumerate(placements, start=1):
        values = {"placement": p}
        if i <= len(codes) and codes[i - 1]:
            values["track_id"] = conn.execute(
                select(tracks.c.id).where(tracks.c.code == codes[i - 1])).scalar()
        conn.execute(update(races)
                     .where((races.c.session_id == sid) & (races.c.race_num == i))
                     .values(**values))
    return sid


def test_leave_one_out_baseline_excludes_the_race_being_measured(conn):
    _seed_session(conn, [2, 4, 6, 8], tracks_by_code=["BC", "WS", "AH", "GBR"])
    df = analytics.add_residuals(analytics.load_frame(conn))

    # Session mean is 5.0. Self-inclusive residuals would be -3, -1, +1, +3.
    # Leave-one-out: race 1 is measured against mean(4,6,8) = 6.0 -> -4.0.
    got = df.sort_values("race_num")["residual"].tolist()
    assert got == pytest.approx([-4.0, -4.0 / 3, 4.0 / 3, 4.0])

    baselines = df.sort_values("race_num")["loo_baseline"].tolist()
    assert baselines == pytest.approx([6.0, 16 / 3, 14 / 3, 4.0])


def test_leave_one_out_residuals_are_larger_than_self_inclusive_ones(conn):
    """The correction's whole point: the biased version shrinks by 1 - 1/n."""
    _seed_session(conn, [1, 5, 9], tracks_by_code=["BC", "WS", "AH"])
    df = analytics.add_residuals(analytics.load_frame(conn))
    n = 3
    self_inclusive = df["placement"] - df["placement"].mean()
    # The race sitting exactly on the session mean divides 0 by 0 and drops out;
    # the rest all scale by the same factor.
    ratio = (df["residual"] / self_inclusive).dropna().tolist()
    assert ratio == pytest.approx([n / (n - 1)] * len(ratio))
    assert len(ratio) == 2


def test_a_track_appearing_twice_in_one_session_is_the_worst_case(conn):
    """Spec section 1's miscount. Both rows count, and each is measured against the
    other races rather than a baseline it helped set."""
    _seed_session(conn, [3, 6, 9, 3], tracks_by_code=["rAF", "WS", "AH", "rAF"])
    df = analytics.add_residuals(analytics.load_frame(conn))
    rows = analytics.track_table(df)
    raf = next(r for r in rows if r["code"] == "rAF")
    assert raf["picks"] == 2
    assert raf["n_placements"] == 2
    # Each rAF race is measured against mean of the other three.
    assert raf["residual"] == pytest.approx(3 - (6 + 9 + 3) / 3)


def test_single_placement_session_yields_no_residual(conn):
    """With one placed race there are no 'other races' to compare against."""
    _seed_session(conn, [4, None, None], tracks_by_code=["BC", "WS", "AH"])
    df = analytics.add_residuals(analytics.load_frame(conn))
    assert df["residual"].notna().sum() == 0
    assert df["placement"].notna().sum() == 1


def test_missing_placements_do_not_shift_the_baseline(conn):
    _seed_session(conn, [2, None, 8], tracks_by_code=["BC", "WS", "AH"])
    df = analytics.add_residuals(analytics.load_frame(conn)).sort_values("race_num")
    assert df["residual"].tolist()[0] == pytest.approx(2 - 8)
    assert math.isnan(df["residual"].tolist()[1])


def test_pick_rate_and_weighted_residual(conn):
    _seed_session(conn, [2, 6, 10], tracks_by_code=["BC", "WS", "AH"])
    _seed_session(conn, [4, 8, 12], tracks_by_code=["BC", "GBR", "SHS"])
    df = analytics.add_residuals(analytics.load_frame(conn))
    rows = {r["code"]: r for r in analytics.track_table(df)}
    assert rows["BC"]["pick_rate"] == pytest.approx(1.0)     # both sessions
    assert rows["WS"]["pick_rate"] == pytest.approx(0.5)
    bc = rows["BC"]
    assert bc["weighted_residual"] == pytest.approx(bc["residual"] * bc["pick_rate"])


def test_low_n_flag_at_the_documented_threshold(conn):
    _seed_session(conn, [1, 2, 3, 4, 5, 6], tracks_by_code=["BC"] * 5 + ["WS"])
    df = analytics.add_residuals(analytics.load_frame(conn))
    rows = {r["code"]: r for r in analytics.track_table(df)}
    assert rows["BC"]["n_placements"] == 5 and rows["BC"]["low_n"] is False
    assert rows["WS"]["n_placements"] == 1 and rows["WS"]["low_n"] is True


def test_intermissions_excluded_by_default_and_listed_separately(conn):
    from sqlalchemy import update
    sid = _seed_session(conn, [2, 12], tracks_by_code=["BC", "BC"])
    conn.execute(update(races)
                 .where((races.c.session_id == sid) & (races.c.race_num == 2))
                 .values(variant="intermission"))
    df = analytics.add_residuals(analytics.load_frame(conn))

    default = {r["code"]: r for r in analytics.track_table(df)}
    assert default["BC"]["picks"] == 1                  # the 3lap race only
    folded = {r["code"]: r for r in analytics.track_table(df, include_intermissions=True)}
    assert folded["BC"]["picks"] == 2

    inter = analytics.intermission_table(df)
    assert len(inter) == 1 and inter[0]["placement"] == 12


def test_gate_analysis_splits_on_shortcut_hit(conn):
    from sqlalchemy import update
    sid = _seed_session(conn, [2, 3, 9, 10], tracks_by_code=["BC"] * 4)
    for num, state in ((1, "hit"), (2, "hit"), (3, "miss"), (4, "miss")):
        conn.execute(update(races)
                     .where((races.c.session_id == sid) & (races.c.race_num == num))
                     .values(shortcut_hit=state))
    df = analytics.add_residuals(analytics.load_frame(conn))
    g = next(r for r in analytics.gate_analysis(df) if r["code"] == "BC")
    assert g["p_hit"] == pytest.approx(0.5)
    assert g["mean_given_hit"] == pytest.approx(2.5)
    assert g["mean_given_miss"] == pytest.approx(9.5)
    assert g["implied_placement"] == pytest.approx(6.0)


def test_gate_analysis_ignores_intermission_races(conn):
    from sqlalchemy import update
    sid = _seed_session(conn, [2, 3], tracks_by_code=["BC", "BC"])
    conn.execute(update(races).where(races.c.session_id == sid)
                 .values(shortcut_hit="hit"))
    conn.execute(update(races)
                 .where((races.c.session_id == sid) & (races.c.race_num == 2))
                 .values(variant="intermission"))
    df = analytics.add_residuals(analytics.load_frame(conn))
    g = next(r for r in analytics.gate_analysis(df) if r["code"] == "BC")
    assert g["n_recorded"] == 1


def test_session_model_flags_the_multivariate_fit_as_underpowered(conn):
    for i in range(6):
        _seed_session(conn, [3 + i % 4, 5, 7], tracks_by_code=["BC", "WS", "AH"],
                      room_min_mmr=3000 + i * 10, room_max_mmr=4000 + i * 50,
                      room_avg_mmr=3500 + i * 20, seat=(i % 12) + 1)
    with_data = analytics.add_residuals(analytics.load_frame(conn))
    model = analytics.session_model(with_data)
    assert model["n_sessions"] == 6
    assert model["multivariate"]["unreliable"] is True
    assert model["multivariate"]["n"] < analytics.MIN_N_SESSION_MODEL
    # n is reported next to every fit, not just the flagged one.
    assert all("n" in u for u in model["univariate"])


def test_lead_defensibility_splits_on_the_flag(conn):
    from sqlalchemy import update
    sid = _seed_session(conn, [1, 8], tracks_by_code=["BC", "WS"])
    conn.execute(update(races).where(races.c.session_id == sid).values(start_position=1))
    bc = conn.execute(select(tracks.c.id).where(tracks.c.code == "BC")).scalar()
    conn.execute(update(tracks).where(tracks.c.id == bc).values(good_from_first=True))

    df = analytics.add_residuals(analytics.load_frame(conn))
    lead = analytics.lead_defensibility(df)
    assert lead["flags_set"] is True
    assert lead["n_from_first"] == 2
    flagged = next(g for g in lead["groups"] if g["label"] == "good from first")
    assert flagged["mean_placement"] == pytest.approx(1.0)


def test_overview_is_empty_but_well_formed_with_no_data(conn):
    ov = analytics.overview(conn)
    assert ov["tracks"] == []
    assert ov["counts"]["races"] == 0
    assert ov["session_model"]["n_sessions"] == 0
    assert ov["gates"] == []


def test_track_trend_is_ordered_and_rolling(conn):
    import datetime as dt
    for i, p in enumerate([12, 10, 8, 6, 4]):
        _seed_session(conn, [p, 6, 6], tracks_by_code=["BC", "WS", "AH"],
                      played_at=dt.datetime(2026, 7, i + 1, 12, 0))
    df = analytics.add_residuals(analytics.load_frame(conn))
    bc = conn.execute(select(tracks.c.id).where(tracks.c.code == "BC")).scalar()
    points = analytics.track_trend(df, bc, window=2)
    assert len(points) == 5
    assert [p["played_at"] for p in points] == sorted(p["played_at"] for p in points)
    # A track can improve substantially and still show a bad lifetime mean.
    assert points[-1]["rolling"] < points[0]["rolling"]
