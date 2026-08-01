"""Statistics — spec section 6.

These are the queries the Google Doc could not answer. The load-bearing one is the
per-track residual, and it is **leave-one-out**: a race is measured against the mean
of the *other* races in its session, never against a mean that includes itself.

Including it biases every residual toward zero by roughly `1 - 1/n`, unevenly — more
in an 8-race tournament room than a 12-race session, and more again for a track
appearing twice in one session, which is exactly the case section 1 says was already
miscounted once. tests/test_analytics.py pins this with golden numbers.

Small-sample discipline, applied in two places:
  * per-track rows with n < 5 are flagged (`low_n`)
  * the 4-predictor session fit is flagged unreliable below 20 sessions
"""
import math

import numpy as np
import pandas as pd
from scipy import stats

from .schema import races, sessions, tracks

MIN_N_TRACK = 5           # below this a per-track row is greyed out
MIN_N_SESSION_MODEL = 20  # below this the multivariate fit is flagged unreliable
NEUTRAL_PLACEMENT = 6.5   # midpoint of a 12-player lobby


def load_frame(conn) -> pd.DataFrame:
    """One row per race, joined to its session and track."""
    q = (
        races.join(sessions, races.c.session_id == sessions.c.id)
             .join(tracks, races.c.track_id == tracks.c.id, isouter=True)
    )
    cols = [
        races.c.id.label("race_id"), races.c.session_id, races.c.race_num,
        races.c.track_id, races.c.variant, races.c.placement,
        races.c.start_position, races.c.lap1_position, races.c.shortcut_hit,
        races.c.mate_placement,
        sessions.c.played_at, sessions.c.format, sessions.c.expected_races,
        sessions.c.aborted, sessions.c.room_min_mmr, sessions.c.room_max_mmr,
        sessions.c.room_avg_mmr, sessions.c.seat, sessions.c.score,
        sessions.c.mmr_delta, sessions.c.own_mmr_before,
        tracks.c.code, tracks.c.full_name, tracks.c.has_gate,
        tracks.c.good_from_first, tracks.c.good_from_first_if_shrooms,
    ]
    import sqlalchemy as sa
    df = pd.read_sql(sa.select(*cols).select_from(q), conn)
    if df.empty:
        return df
    df["played_at"] = pd.to_datetime(df["played_at"])
    return df


def add_residuals(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the leave-one-out session baseline and residual to each placed race.

    The baseline spans every placed race in the session regardless of `variant` —
    "the other races in that session" is literal, and a session's form is a
    session's form. Only the *reporting* filters to 3lap.
    """
    if df.empty:
        return df.assign(loo_baseline=pd.Series(dtype=float),
                         residual=pd.Series(dtype=float))
    out = df.copy()
    placed = out["placement"].notna()
    grp = out.loc[placed].groupby("session_id")["placement"]
    ssum = out["session_id"].map(grp.sum())
    scount = out["session_id"].map(grp.count())

    # n == 1 leaves no "other races" to compare against; that race gets no residual.
    denom = (scount - 1).where(placed & (scount > 1))
    out["loo_baseline"] = ((ssum - out["placement"]) / denom).where(placed & (scount > 1))
    out["residual"] = out["placement"] - out["loo_baseline"]
    return out


def _t_ci(values: np.ndarray, conf: float = 0.95):
    n = len(values)
    if n < 2:
        return (None, None)
    sd = float(np.std(values, ddof=1))
    if sd == 0 or not math.isfinite(sd):
        m = float(np.mean(values))
        return (m, m)
    half = stats.t.ppf(0.5 + conf / 2, n - 1) * sd / math.sqrt(n)
    m = float(np.mean(values))
    return (m - half, m + half)


def track_table(df: pd.DataFrame, include_intermissions: bool = False) -> list[dict]:
    """The core view. Filters to variant='3lap' unless asked otherwise."""
    if df.empty:
        return []
    d = df if include_intermissions else df[df["variant"] == "3lap"]
    d = d[d["track_id"].notna()]
    if d.empty:
        return []

    # Denominator for pick rate: sessions that actually contain at least one race.
    session_ids = df.loc[df["race_num"].notna(), "session_id"].unique()
    n_sessions = len(session_ids)

    rows = []
    for track_id, g in d.groupby("track_id"):
        placements = g["placement"].dropna().to_numpy(dtype=float)
        residuals = g["residual"].dropna().to_numpy(dtype=float)
        pick_sessions = g["session_id"].nunique()
        pick_rate = pick_sessions / n_sessions if n_sessions else 0.0

        mean_placement = float(np.mean(placements)) if len(placements) else None
        sd = float(np.std(placements, ddof=1)) if len(placements) > 1 else None
        residual = float(np.mean(residuals)) if len(residuals) else None
        lo, hi = _t_ci(residuals) if len(residuals) else (None, None)

        rows.append({
            "track_id": int(track_id),
            "code": g["code"].iloc[0],
            "full_name": g["full_name"].iloc[0],
            "has_gate": bool(g["has_gate"].iloc[0]),
            "picks": int(len(g)),
            "pick_sessions": int(pick_sessions),
            "pick_rate": pick_rate,
            "n_placements": int(len(placements)),
            "mean_placement": mean_placement,
            "sd": sd,
            "n_residual": int(len(residuals)),
            "residual": residual,
            "residual_ci_low": lo,
            "residual_ci_high": hi,
            # Expected places lost per session: a -0.5 residual on a 96%-pick track
            # matters more than a -1.5 on a 20% one.
            "weighted_residual": residual * pick_rate if residual is not None else None,
            "low_n": len(placements) < MIN_N_TRACK,
        })

    rows.sort(key=lambda r: (r["weighted_residual"] is None,
                             -(r["weighted_residual"] or 0)))
    return rows


def intermission_table(df: pd.DataFrame) -> list[dict]:
    """Intermissions get their own small table so they are not invisible."""
    if df.empty:
        return []
    d = df[(df["variant"] == "intermission") & df["track_id"].notna()]
    return [
        {
            "code": r["code"], "full_name": r["full_name"],
            "session_id": int(r["session_id"]), "race_num": int(r["race_num"]),
            "played_at": r["played_at"],
            "placement": None if pd.isna(r["placement"]) else int(r["placement"]),
            "residual": None if pd.isna(r["residual"]) else float(r["residual"]),
        }
        for _, r in d.iterrows()
    ]


def session_frame(df: pd.DataFrame) -> pd.DataFrame:
    """One row per session: average placement plus the lobby predictors."""
    if df.empty:
        return pd.DataFrame()
    placed = df[df["placement"].notna()]
    if placed.empty:
        return pd.DataFrame()
    agg = placed.groupby("session_id").agg(
        avg_placement=("placement", "mean"),
        n_races=("placement", "count"),
        played_at=("played_at", "first"),
        format=("format", "first"),
        room_min_mmr=("room_min_mmr", "first"),
        room_max_mmr=("room_max_mmr", "first"),
        room_avg_mmr=("room_avg_mmr", "first"),
        seat=("seat", "first"),
        score=("score", "first"),
        mmr_delta=("mmr_delta", "first"),
        own_mmr_before=("own_mmr_before", "first"),
    ).reset_index()
    agg["spread"] = agg["room_max_mmr"] - agg["room_min_mmr"]
    return agg.sort_values("played_at")


PREDICTORS = ["room_max_mmr", "room_avg_mmr", "seat", "spread"]


def session_model(df: pd.DataFrame) -> dict:
    """Univariate fits per predictor plus a multivariate fit, each reported with n."""
    sf = session_frame(df)
    out = {
        "n_sessions": int(len(sf)),
        "univariate": [],
        "multivariate": None,
        "predictions": [],
        "min_n_multivariate": MIN_N_SESSION_MODEL,
    }
    if sf.empty:
        return out

    y_all = sf["avg_placement"].astype(float)
    for p in PREDICTORS:
        sub = sf[[p, "avg_placement"]].dropna()
        if len(sub) < 3 or sub[p].nunique() < 2:
            out["univariate"].append({"predictor": p, "n": int(len(sub)),
                                      "r": None, "r2": None, "slope": None,
                                      "p_value": None, "usable": False})
            continue
        lr = stats.linregress(sub[p].astype(float), sub["avg_placement"].astype(float))
        out["univariate"].append({
            "predictor": p, "n": int(len(sub)),
            "r": float(lr.rvalue), "r2": float(lr.rvalue ** 2),
            "slope": float(lr.slope), "intercept": float(lr.intercept),
            "p_value": float(lr.pvalue), "usable": True,
        })

    sub = sf[PREDICTORS + ["avg_placement", "session_id", "played_at"]].dropna()
    k = len(PREDICTORS)
    if len(sub) >= k + 2:
        X = np.column_stack([np.ones(len(sub))] + [sub[p].astype(float).to_numpy()
                                                   for p in PREDICTORS])
        y = sub["avg_placement"].astype(float).to_numpy()
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        pred = X @ beta
        ss_res = float(np.sum((y - pred) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else None
        n = len(sub)
        adj = (1 - (1 - r2) * (n - 1) / (n - k - 1)) if (r2 is not None and n > k + 1) else None
        out["multivariate"] = {
            "n": int(n), "r2": r2, "adj_r2": adj,
            "coefficients": dict(zip(["intercept"] + PREDICTORS,
                                     [float(b) for b in beta])),
            # Four predictors over ~24 sessions is badly underpowered and will
            # otherwise produce confident-looking coefficients.
            "unreliable": n < MIN_N_SESSION_MODEL,
        }
        out["predictions"] = [
            {"session_id": int(sid), "played_at": pa,
             "actual": float(a), "predicted": float(p_), "residual": float(a - p_)}
            for sid, pa, a, p_ in zip(sub["session_id"], sub["played_at"], y, pred)
        ]
    else:
        out["multivariate"] = {"n": int(len(sub)), "r2": None, "adj_r2": None,
                               "coefficients": None, "unreliable": True}
    # Session-level rows are useful even when the fit is not.
    out["sessions"] = sf.to_dict("records")
    out["overall_mean_placement"] = float(y_all.mean())
    return out


def gate_analysis(df: pd.DataFrame) -> list[dict]:
    """Two-component split on shortcut_hit. 3-lap only — the cut may not be on the
    intermission route at all."""
    if df.empty:
        return []
    d = df[(df["variant"] == "3lap") & (df["has_gate"] == 1)]
    rows = []
    for track_id, g in d.groupby("track_id"):
        known = g[g["shortcut_hit"].isin(["hit", "miss"])]
        hit = known[known["shortcut_hit"] == "hit"]["placement"].dropna()
        miss = known[known["shortcut_hit"] == "miss"]["placement"].dropna()
        n_known = int(len(known))
        p_hit = float(len(known[known["shortcut_hit"] == "hit"]) / n_known) if n_known else None
        mean_hit = float(hit.mean()) if len(hit) else None
        mean_miss = float(miss.mean()) if len(miss) else None
        implied = (p_hit * mean_hit + (1 - p_hit) * mean_miss
                   if None not in (p_hit, mean_hit, mean_miss) else None)
        # Execution vs survival needs lap1_position, which no historical race has.
        lap1 = g["lap1_position"].dropna()
        rows.append({
            "track_id": int(track_id), "code": g["code"].iloc[0],
            "full_name": g["full_name"].iloc[0],
            "races": int(len(g)), "n_recorded": n_known,
            "p_hit": p_hit, "n_hit": int(len(hit)), "n_miss": int(len(miss)),
            "mean_given_hit": mean_hit, "mean_given_miss": mean_miss,
            "implied_placement": implied,
            "n_lap1": int(len(lap1)),
            "decomposable": len(lap1) >= MIN_N_TRACK,
        })
    rows.sort(key=lambda r: r["code"])
    return rows


def lead_defensibility(df: pd.DataFrame) -> dict:
    """Does the lead actually hold on the tracks believed to be defensible?

    This is the analytic that makes `good_from_first` worth storing at all.
    """
    empty = {"groups": [], "n_from_first": 0, "flags_set": False}
    if df.empty:
        return empty
    d = df[(df["variant"] == "3lap") & (df["start_position"] == 1)
           & df["track_id"].notna()]
    flags_set = bool(df["good_from_first"].fillna(0).astype(int).sum()
                     or df["good_from_first_if_shrooms"].fillna(0).astype(int).sum())
    if d.empty:
        return {**empty, "flags_set": flags_set}

    groups = []
    for label, mask in (
        ("good from first", d["good_from_first"] == 1),
        ("good from first (with shrooms)", d["good_from_first_if_shrooms"] == 1),
        ("not flagged", (d["good_from_first"] != 1) & (d["good_from_first_if_shrooms"] != 1)),
    ):
        g = d[mask]
        placements = g["placement"].dropna()
        residuals = g["residual"].dropna()
        groups.append({
            "label": label, "races": int(len(g)), "n": int(len(placements)),
            "mean_placement": float(placements.mean()) if len(placements) else None,
            "residual": float(residuals.mean()) if len(residuals) else None,
            "low_n": len(placements) < MIN_N_TRACK,
        })
    return {"groups": groups, "n_from_first": int(len(d)), "flags_set": flags_set}


def track_trend(df: pd.DataFrame, track_id: int, window: int = 5) -> list[dict]:
    """Rolling mean residual for one track. A track can improve substantially and
    still show a bad lifetime mean; only the time series shows it."""
    if df.empty:
        return []
    d = df[(df["track_id"] == track_id) & (df["variant"] == "3lap")
           & df["residual"].notna()].sort_values("played_at")
    if d.empty:
        return []
    roll = d["residual"].rolling(window=window, min_periods=1).mean()
    return [
        {"played_at": pa, "session_id": int(sid), "residual": float(r),
         "rolling": float(rv)}
        for pa, sid, r, rv in zip(d["played_at"], d["session_id"], d["residual"], roll)
    ]


def mmr_trend(df: pd.DataFrame) -> list[dict]:
    """MMR over time from own_mmr_before + mmr_delta."""
    sf = session_frame(df)
    if sf.empty:
        return []
    out = []
    for _, r in sf.iterrows():
        before = None if pd.isna(r["own_mmr_before"]) else int(r["own_mmr_before"])
        delta = None if pd.isna(r["mmr_delta"]) else int(r["mmr_delta"])
        if before is None and delta is None:
            continue
        out.append({
            "played_at": r["played_at"], "session_id": int(r["session_id"]),
            "before": before, "delta": delta,
            "after": (before + delta) if (before is not None and delta is not None) else None,
        })
    return out


def overview(conn, include_intermissions: bool = False) -> dict:
    """Everything section 6 asks for, in one pass over the data."""
    df = add_residuals(load_frame(conn))
    tracks_rows = track_table(df, include_intermissions)
    return {
        "tracks": tracks_rows,
        "intermissions": intermission_table(df),
        "session_model": session_model(df),
        "gates": gate_analysis(df),
        "lead": lead_defensibility(df),
        "mmr": mmr_trend(df),
        "counts": {
            "races": int(len(df)),
            "placements": int(df["placement"].notna().sum()) if not df.empty else 0,
            "residuals": int(df["residual"].notna().sum()) if not df.empty else 0,
            "sessions": int(df["session_id"].nunique()) if not df.empty else 0,
            "tracks_with_data": len(tracks_rows),
        },
        "min_n_track": MIN_N_TRACK,
        "neutral_placement": NEUTRAL_PLACEMENT,
    }
