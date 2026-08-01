"""Typeahead tests — spec section 5.

The ranking rules are the thing standing between the entry form and the ambiguous
codes that corrupted the dataset once already, so the collisions get named tests.
"""
import pytest

from app.matching import (RANK_EXACT_ALIAS, RANK_EXACT_CODE, load_candidates,
                          resolve_exact, search, subsequence_cost)


@pytest.fixture
def cands(conn):
    return load_candidates(conn)


def codes(matches):
    return [m.track.code for m in matches]


# ------------------------------------------------------------------- collisions

def test_bc_ranks_bowsers_castle_above_boo_cinema(cands):
    """`BC` -> Bowser's Castle, never Boo Cinema, now that `BCi` exists."""
    out = search(cands, "bc")
    assert out[0].track.code == "BC"
    assert out[0].rank in (RANK_EXACT_CODE, RANK_EXACT_ALIAS)
    assert "BCi" in codes(out)          # still reachable, just not first


def test_bci_reaches_boo_cinema(cands):
    assert search(cands, "bci")[0].track.code == "BCi"


def test_sp_and_ps_are_a_transposition_apart_and_stay_distinct(cands):
    """Starview Peak and Peach Stadium. Exact-match-first is what separates them."""
    assert search(cands, "sp")[0].track.code == "SP"
    assert search(cands, "ps")[0].track.code == "PS"


def test_dkp_and_dksp_are_the_pair_that_were_conflated_for_weeks(cands):
    assert search(cands, "dkp")[0].track.code == "rDKP"
    assert search(cands, "dksp")[0].track.code == "DKSP"
    assert search(cands, "pass")[0].track.code == "rDKP"


def test_ws_ranks_whistlestop_first_despite_wsh_and_wst(cands):
    assert search(cands, "ws")[0].track.code == "WS"


# ------------------------------------------------------------ fuzzy subsequence

@pytest.mark.parametrize("query,expected", [
    ("whis", "WS"),          # alias prefix
    ("wsum", "WS"),          # W + Sum, across two words of the full name
    ("cinema", "BCi"),       # by full name only
    ("castle", "BC"),        # alias
    ("acorn", "AH"),
    ("oasis", "FO"),
    ("bazaar", "rSGB"),
    ("choco", "rCM"),
    ("sundae", "SHS"),
    ("stadium", "rWSt"),
    ("shipyard", "rWSh"),
])
def test_fuzzy_finds_the_intended_track(cands, query, expected):
    assert search(cands, query)[0].track.code == expected


def test_word_boundaries_beat_a_tighter_span(cands):
    """`wsum` fits "wario stadium" in a shorter window than "whistlestop summit".

    Span alone therefore ranks Wario Stadium first, which is wrong: the match a human
    means is W-histlestop Sum-mit. Word-start scoring is what fixes it.
    """
    assert subsequence_cost("wsum", "whistlestop summit") < \
           subsequence_cost("wsum", "wario stadium")


def test_non_subsequence_returns_none():
    assert subsequence_cost("xyz", "bowser's castle") is None
    assert subsequence_cost("", "anything") is None
    assert subsequence_cost("toolongquery", "abc") is None


def test_garbage_matches_nothing(cands):
    assert search(cands, "zzqx") == []


def test_empty_query_returns_nothing(cands):
    assert search(cands, "") == []
    assert search(cands, "   ") == []


def test_matching_is_case_insensitive(cands):
    """The doc's case variants — `BAZAAR`, `pASS`, `Shs` — all land on one track."""
    assert search(cands, "BAZAAR")[0].track.code == "rSGB"
    assert search(cands, "pASS")[0].track.code == "rDKP"
    assert search(cands, "Shs")[0].track.code == "SHS"
    assert search(cands, "RaF")[0].track.code == "rAF"


def test_results_are_capped(cands):
    assert len(search(cands, "a", limit=3)) <= 3


# ---------------------------------------------------------------- exact resolve

def test_resolve_exact_is_exact_only(conn):
    """The importer must not fuzz bulk input — a near-miss becomes a review item."""
    assert resolve_exact(conn, "bc") is not None
    assert resolve_exact(conn, "BC") is not None
    assert resolve_exact(conn, "  castle  ") is not None
    assert resolve_exact(conn, "bcx") is None
    assert resolve_exact(conn, "") is None
    assert resolve_exact(conn, None) is None


def test_every_canonical_code_resolves_as_an_alias(conn, cands):
    """Typing the code shown in the UI always works, even when the seed's alias list
    did not spell it out."""
    for c in cands:
        assert resolve_exact(conn, c.code) == c.id


def test_inactive_tracks_are_excluded_from_the_typeahead(conn):
    from sqlalchemy import update
    from app.schema import tracks
    conn.execute(update(tracks).where(tracks.c.code == "RR").values(active=False))
    assert "RR" not in codes(search(load_candidates(conn), "rr"))
    assert "RR" in codes(search(load_candidates(conn, include_inactive=True), "rr"))
