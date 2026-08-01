"""Track typeahead — spec section 5.

Fuzzy subsequence matching across codes, aliases, and full names at once, with exact
code/alias hits always ranked first. Only an exact hit is allowed to auto-commit;
everything else highlights and waits for Enter.

That split is the whole point. Section 1's premise is that misidentified tracks have
already corrupted this dataset once, so `BC` must resolve to Bowser's Castle and never
to Boo Cinema, and `SP` to Starview Peak and never to Peach Stadium — even though each
pair is a plausible fuzzy match for the other.
"""
from dataclasses import dataclass, field

from sqlalchemy import select

from .schema import track_aliases, tracks

# Rank bands. Lower sorts first; within a band, ties break on the fuzzy span and
# then alphabetically, so results are stable across keystrokes.
RANK_EXACT_CODE = 0
RANK_EXACT_ALIAS = 1
RANK_PREFIX_CODE = 2
RANK_PREFIX_ALIAS = 3
RANK_PREFIX_NAME = 4
RANK_SUBSEQ_CODE = 5
RANK_SUBSEQ_ALIAS = 6
RANK_SUBSEQ_NAME = 7


@dataclass
class Candidate:
    id: int
    code: str
    full_name: str
    has_gate: bool
    aliases: list[str] = field(default_factory=list)

    @property
    def haystacks(self):
        return (self.code.lower(), self.full_name.lower(), self.aliases)


@dataclass
class Match:
    track: Candidate
    rank: int
    score: float          # lower is a better fit within the rank band
    matched_on: str

    @property
    def exact(self) -> bool:
        return self.rank in (RANK_EXACT_CODE, RANK_EXACT_ALIAS)


def load_candidates(conn, include_inactive: bool = False) -> list[Candidate]:
    q = select(tracks.c.id, tracks.c.code, tracks.c.full_name, tracks.c.has_gate)
    if not include_inactive:
        q = q.where(tracks.c.active == True)  # noqa: E712 — SQL boolean, not Python
    rows = conn.execute(q.order_by(tracks.c.code)).all()
    by_id = {
        r.id: Candidate(id=r.id, code=r.code, full_name=r.full_name, has_gate=bool(r.has_gate))
        for r in rows
    }
    for a in conn.execute(select(track_aliases.c.track_id, track_aliases.c.alias)):
        if a.track_id in by_id:
            by_id[a.track_id].aliases.append(a.alias)
    return list(by_id.values())


_WORD_BREAK = " -'."

# Cost of landing a matched character. Continuing the previous character costs
# nothing, starting a word is cheap, and jumping into the middle of one is dear.
_COST_CONTIGUOUS = 0.0
_COST_WORD_START = 1.0
_COST_MID_WORD = 3.0


def _is_word_start(target: str, j: int) -> bool:
    return j == 0 or target[j - 1] in _WORD_BREAK


def subsequence_cost(query: str, target: str) -> float | None:
    """How awkwardly `query` fits inside `target` as a subsequence. Lower is better.

    A plain "tightest window" measure gets this wrong. `wsum` fits "wario stadium"
    in a 13-character window and "whistlestop summit" in a 15-character one, so span
    alone ranks Wario Stadium first — while the match a human means is
    **W**histlestop **Sum**mit. Scoring word starts cheaply and mid-word jumps dearly
    reproduces the intent: `w` + the contiguous run `sum` at a word boundary beats
    four scattered letters.

    Returns None when `query` is not a subsequence of `target` at all.
    """
    n, m = len(query), len(target)
    if n == 0 or n > m:
        return None

    inf = float("inf")
    # best[j] = cheapest cost of matching query[:i+1] with query[i] landing at j
    best = [inf] * m
    for j in range(m):
        if target[j] == query[0]:
            # Prefer earlier anchors, but only as a tiebreak.
            best[j] = (_COST_WORD_START if _is_word_start(target, j) else _COST_MID_WORD) + j * 0.01

    for i in range(1, n):
        nxt = [inf] * m
        running = inf          # cheapest way to have matched query[:i] before j
        for j in range(1, m):
            if best[j - 1] < running:
                running = best[j - 1]
            if target[j] != query[i] or running == inf:
                continue
            contiguous = best[j - 1] + _COST_CONTIGUOUS
            jump = running + (_COST_WORD_START if _is_word_start(target, j)
                              else _COST_MID_WORD)
            nxt[j] = min(contiguous, jump)
        best = nxt

    result = min(best)
    return None if result == inf else result


def search(candidates: list[Candidate], query: str, limit: int = 8) -> list[Match]:
    q = (query or "").strip().lower()
    if not q:
        return []

    results: dict[int, Match] = {}

    def offer(cand: Candidate, rank: int, score: float, matched_on: str):
        prev = results.get(cand.id)
        if prev is None or (rank, score) < (prev.rank, prev.score):
            results[cand.id] = Match(track=cand, rank=rank, score=score, matched_on=matched_on)

    for cand in candidates:
        code, name, aliases = cand.haystacks

        if q == code:
            offer(cand, RANK_EXACT_CODE, 0, cand.code)
        for alias in aliases:
            if q == alias:
                offer(cand, RANK_EXACT_ALIAS, 0, alias)

        # Shorter targets win a prefix tie: `dk` prefers DKSP over a longer name.
        if code.startswith(q):
            offer(cand, RANK_PREFIX_CODE, len(code) - len(q), cand.code)
        for alias in aliases:
            if alias.startswith(q):
                offer(cand, RANK_PREFIX_ALIAS, len(alias) - len(q), alias)
        if name.startswith(q):
            offer(cand, RANK_PREFIX_NAME, len(name) - len(q), cand.full_name)
        else:
            for w in name.split():
                if w.startswith(q):
                    offer(cand, RANK_PREFIX_NAME, len(name) - len(q) + 0.5, cand.full_name)
                    break

        cost = subsequence_cost(q, code)
        if cost is not None:
            offer(cand, RANK_SUBSEQ_CODE, cost, cand.code)
        for alias in aliases:
            cost = subsequence_cost(q, alias)
            if cost is not None:
                offer(cand, RANK_SUBSEQ_ALIAS, cost, alias)
        cost = subsequence_cost(q, name)
        if cost is not None:
            offer(cand, RANK_SUBSEQ_NAME, cost, cand.full_name)

    ordered = sorted(results.values(), key=lambda m: (m.rank, m.score, m.track.code.lower()))
    return ordered[:limit]


def resolve_exact(conn, text: str) -> int | None:
    """Alias/code lookup for the importer. Exact only — no fuzzing on bulk input."""
    if not text:
        return None
    key = text.strip().lower()
    if not key:
        return None
    return conn.execute(
        select(track_aliases.c.track_id).where(track_aliases.c.alias == key)
    ).scalar()
