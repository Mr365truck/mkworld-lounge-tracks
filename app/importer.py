"""Paste importer — spec section 7.

Accepts the Google Doc format directly: a session header line
(`date, time, format, min/max/avg mmr, seat`) followed by a numbered track list with
optional nested sub-items. Every edge case in spec section 10 is present in
`Lounge.pdf` and covered by the fixtures in tests/.

The governing rule is section 7's: anything that does not resolve cleanly goes to a
review queue (`import_issues`) rather than failing the whole import or being silently
dropped. Silent dropping is failure mode three from section 1.

Two defaults, stated in section 7 and enforced here:
  * `variant` is '3lap' unless the line carries an inline intermission marker.
  * `shortcut_hit` is NULL — never 'na'. The doc distinguishes neither, and writing
    'na' would claim the gate wasn't in play when the truth is that nobody logged it.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select

from . import config
from .matching import resolve_exact
from .schema import default_expected_races, import_issues, races, sessions

FORMATS = ["tournament", "tourney", "ffa", "2v2", "3v3", "4v4", "6v6"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun",
          "jul", "aug", "sep", "oct", "nov", "dec"]


@dataclass
class ParsedRace:
    race_num: int
    track_raw: str | None = None
    track_code: str | None = None      # resolved at apply time
    track_id: int | None = None
    variant: str = "3lap"
    placement: int | None = None
    start_position: int | None = None
    lap1_position: int | None = None
    mate_placement: int | None = None
    notes: list[str] = field(default_factory=list)
    unresolved: str | None = None


@dataclass
class ParsedSession:
    header_raw: str = ""
    index: int = 0
    format: str | None = None
    date: str | None = None
    time: str | None = None
    played_at: datetime | None = None
    room_min_mmr: int | None = None
    room_max_mmr: int | None = None
    room_avg_mmr: int | None = None
    seat: int | None = None
    mate_mmr: int | None = None
    own_mmr_before: int | None = None
    mmr_delta: int | None = None
    score: int | None = None
    spectated: bool = False
    event: str | None = None
    room: int | None = None
    date_inherited: bool = False
    restarted_from: int | None = None
    expected_races: int = 12
    aborted: bool = False
    races: list[ParsedRace] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    sessions: list[ParsedSession] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    def warn(self, session_index, message, race_num=None, kind="warning", raw=None):
        self.warnings.append({
            "session": session_index, "race_num": race_num,
            "kind": kind, "message": message, "raw": raw,
        })


# --------------------------------------------------------------------------- parse

def parse_time(text: str | None) -> tuple[int, int] | None:
    """'3pm' / '12 noon' / '7:00pm' / '10am' -> (hour24, minute)."""
    if not text:
        return None
    t = text.strip().lower().replace(" ", "")
    if t in ("noon", "12noon"):
        return (12, 0)
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", t)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3)
    if hour > 23 or minute > 59:
        return None
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    return (hour, minute)


def parse_played_at(date_str: str | None, time_str: str | None, year: int) -> datetime | None:
    """`5/26` + `3pm` -> naive UTC datetime, via the configured display timezone."""
    if not date_str:
        return None
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", date_str.strip())
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    if m.group(3):
        y = int(m.group(3))
        year = y + 2000 if y < 100 else y
    hm = parse_time(time_str) or (0, 0)
    try:
        local = datetime(year, month, day, hm[0], hm[1])
    except ValueError:
        return None
    return config.to_utc(local)


def parse_header(line: str) -> ParsedSession:
    s = ParsedSession(header_raw=line.strip())
    low = line.lower()

    if "viewer" in low:
        s.spectated = True
    for f in FORMATS:
        if re.search(r"\b" + re.escape(f) + r"\b", low):
            s.format = "tournament" if f in ("tournament", "tourney") else f
            break

    m = re.search(r"\b(\d{1,2}/\d{1,2})\b", line)
    if m:
        s.date = m.group(1)
    else:
        # month-name form, e.g. 'friday jun 6 @ 6pm'
        m = re.search(r"\b(" + "|".join(MONTHS) + r")[a-z]*\.?\s+(\d{1,2})\b", low)
        if m:
            s.date = f"{MONTHS.index(m.group(1)) + 1}/{int(m.group(2))}"

    m = re.search(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}\s*noon|noon)\b", low)
    if m:
        s.time = m.group(1).strip()

    # Tolerates 'maX:', 'seat 5' without a colon, and thousands separators ('4,085').
    for attr, pat in (("room_min_mmr", "min"), ("room_max_mmr", "max"),
                      ("room_avg_mmr", "avg"), ("seat", "seat"), ("mate_mmr", "mate")):
        m = re.search(pat + r"\s*:?\s*([\d,]+)", low)
        if m:
            setattr(s, attr, int(m.group(1).replace(",", "")))
    return s


def is_header(line: str) -> bool:
    low = line.lower()
    if re.match(r"^\d{1,2}\.", line) or re.match(r"^[a-z]\.", line):
        return False
    if "min:" in low or "min :" in low:
        return True
    if re.search(r"\b\d{1,2}/\d{1,2}\b", line) and any(f in low for f in FORMATS):
        return True
    return any(re.search(r"\b" + re.escape(f) + r"\b", low) for f in FORMATS)


def parse_prose(text: str) -> dict:
    """Session outcomes written as sentences: 'scored 88, +46 mmr'.

    `scored 8th` is a placement, not a score, so the ordinal suffix is excluded.
    """
    out = {}
    low = text.lower()
    m = re.search(r"scored\s+(\d+)(?!\s*(?:th|st|nd|rd))", low)
    if m:
        out["score"] = int(m.group(1))
    m = re.search(r"([+-]\s?\d+)\s*mmr", low) or re.search(r"mmr\s*([+-]\s?\d+)", low)
    if m:
        out["mmr_delta"] = int(m.group(1).replace(" ", ""))
    return out


def split_track_text(raw: str) -> tuple[str, str, list[str]]:
    """Strip inline parentheticals off a track line.

    '1. raf (intermission to)' -> ('raf', 'intermission', [])
    '10. hills (why)'          -> ('hills', '3lap', ['why'])
    """
    notes, variant = [], "3lap"
    t = raw.strip()
    for p in re.findall(r"\(([^)]*)\)", t):
        if "intermission" in p.lower():
            variant = "intermission"
        elif p.strip():
            notes.append(p.strip())
    t = re.sub(r"\([^)]*\)", "", t).strip().strip(" .,")
    return t, variant, notes


def parse(text: str, year: int | None = None) -> ParseResult:
    """Pure parse. No database, no track resolution — that happens in apply()."""
    year = year or config.IMPORT_DEFAULT_YEAR
    res = ParseResult()
    cur: ParsedSession | None = None
    cur_race: ParsedRace | None = None
    last_date = None
    pending_event = None

    def close():
        nonlocal cur, cur_race
        if cur is not None:
            res.sessions.append(cur)
        cur, cur_race = None, None

    def start(s: ParsedSession):
        nonlocal cur, cur_race, last_date
        close()
        if s.date:
            last_date = s.date
        elif last_date:
            s.date = last_date
            s.date_inherited = True
        s.index = len(res.sessions) + 1
        if s.event is None:
            s.event = pending_event
        cur = s
        cur_race = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^=====\s*PAGE\s+\d+", line):
            continue                    # tools/extract_lounge.py page marker
        if line.lower().startswith("log:"):
            break                       # trailing to-do block, not session data

        # Tournament rooms are separate sessions under one event header.
        m = re.match(r"^Room (\d+)$", line, re.I)
        if m:
            s = parse_header(pending_event or "")
            s.room = int(m.group(1))
            s.format = "tournament"
            s.event = pending_event
            s.header_raw = f"{pending_event or ''} / Room {m.group(1)}".strip(" /")
            start(s)
            continue

        mr = re.match(r"^(\d{1,2})\.\s*(.*)$", line)
        ms = re.match(r"^([a-z])\.\s*(.*)$", line)

        if mr:
            if cur is None:
                start(parse_header(""))
                res.warn(cur.index, "races began with no session header", kind="no_header")
            num, body = int(mr.group(1)), mr.group(2).strip()
            # Race numbering restarting mid-block means a new session began without
            # its own header — the abandoned-then-restarted case in section 10.
            if cur.races and num <= cur.races[-1].race_num:
                prev = cur
                s = ParsedSession(
                    header_raw=prev.header_raw + " [restart: no header]",
                    format=prev.format, date=prev.date, time=prev.time,
                    room_min_mmr=prev.room_min_mmr, room_max_mmr=prev.room_max_mmr,
                    room_avg_mmr=prev.room_avg_mmr, seat=prev.seat,
                    mate_mmr=prev.mate_mmr, spectated=prev.spectated,
                    room=prev.room, restarted_from=prev.index,
                )
                start(s)
                res.warn(prev.index,
                         f"race numbering restarted at {num}; split into session "
                         f"{cur.index} (abandoned session followed by a fresh one)",
                         kind="session_split")
            cur_race = ParsedRace(race_num=num, track_raw=body or None)
            if body:
                t, variant, notes = split_track_text(body)
                cur_race.track_raw = t
                cur_race.variant = variant
                cur_race.notes = notes
            else:
                res.warn(cur.index, "numbered race with no track",
                         race_num=num, kind="no_track")
            cur.races.append(cur_race)
            continue

        if ms and cur_race is not None:
            body = ms.group(2).strip()
            if not body:
                continue
            low = body.lower()
            m2 = re.match(r"^start:?\s*(\d+|\?)", low)
            if m2:
                if m2.group(1) != "?":
                    cur_race.start_position = int(m2.group(1))
                continue
            m2 = re.match(r"^(?:l1|lap ?1):?\s*(\d+)", low)
            if m2:
                cur_race.lap1_position = int(m2.group(1))
                continue
            m2 = re.match(r"^mate:?\s*(\d+)", low)
            if m2:
                cur_race.mate_placement = int(m2.group(1))
                continue
            m2 = re.fullmatch(r"(\d{1,2})", low)
            if m2 and cur_race.placement is None:
                cur_race.placement = int(m2.group(1))
                continue
            cur_race.notes.append(body)
            continue

        if is_header(line):
            low = line.lower()
            if ("tourney" in low or "tournament" in low) and "min" not in low:
                pending_event = line        # event line; Room N sessions follow
                close()
                continue
            pending_event = None
            start(parse_header(line))
            continue

        # Free-standing prose: a note on the current session, plus whatever is
        # cleanly extractable from it (section 10, "Results in prose").
        if cur is not None:
            cur.notes.append(line)
            for k, v in parse_prose(line).items():
                if getattr(cur, k) is None:
                    setattr(cur, k, v)
        else:
            res.warn(None, f"orphan line: {line!r}", kind="orphan", raw=line)

    close()

    for s in res.sessions:
        s.format = s.format or "ffa"
        s.expected_races = default_expected_races(s.format)
        n = len(s.races)
        # A session with <=2 races and no more coming is 'finished' in the only
        # sense that matters: it will never gain rows. Includes the spectated 6v6
        # and the 2v2 that was logged but never played.
        s.aborted = bool(n < s.expected_races and n <= 2)
        s.played_at = parse_played_at(s.date, s.time, year)
        if s.played_at is None:
            res.warn(s.index, f"no usable date in header {s.header_raw!r}", kind="no_date")
        if n and n != s.expected_races and not s.aborted:
            res.warn(s.index, f"{n} races, expected {s.expected_races}", kind="race_count")
        if s.spectated:
            s.notes.insert(0, "spectated (viewer)")
        if s.room is not None:
            s.notes.insert(0, f"Room {s.room}")
        if s.event:
            s.notes.insert(0, f"event: {s.event}")

    return res


# --------------------------------------------------------------------------- apply

# The extractor can merge two adjacent text runs into one token
# ('Farawayhandling'). Recovering the leading alias beats dropping the race.
def _resolve_track(conn, text: str) -> tuple[int | None, str | None, str | None]:
    """-> (track_id, recovered_suffix, unresolved_text)"""
    if not text:
        return None, None, None
    tid = resolve_exact(conn, text)
    if tid is not None:
        return tid, None, None

    from .schema import track_aliases
    key = text.strip().lower()
    rows = conn.execute(select(track_aliases.c.alias, track_aliases.c.track_id)).all()
    best = None
    for alias, track_id in rows:
        if key.startswith(alias) and len(key) > len(alias):
            if best is None or len(alias) > len(best[0]):
                best = (alias, track_id)
    if best:
        return best[1], key[len(best[0]):], None
    return None, None, text


def apply(conn, parsed: ParseResult, dry_run: bool = False) -> dict:
    """Write a ParseResult into the database. Returns a summary."""
    now = config.utcnow()
    written_sessions = 0
    written_races = 0
    issues: list[dict] = []

    for ps in parsed.sessions:
        played_at = ps.played_at or now
        notes = "\n".join(ps.notes) or None

        if dry_run:
            session_id = None
        else:
            session_id = conn.execute(sessions.insert().values(
                played_at=played_at,
                format=ps.format,
                expected_races=ps.expected_races,
                aborted=ps.aborted,
                room_min_mmr=ps.room_min_mmr,
                room_max_mmr=ps.room_max_mmr,
                room_avg_mmr=ps.room_avg_mmr,
                seat=ps.seat,
                mate_mmr=ps.mate_mmr,
                own_mmr_before=ps.own_mmr_before,
                mmr_delta=ps.mmr_delta,
                score=ps.score,
                notes=notes,
                created_at=now, updated_at=now,
            )).inserted_primary_key[0]
            written_sessions += 1

        for pr in ps.races:
            track_id, recovered, unresolved = (None, None, None)
            if pr.track_raw:
                track_id, recovered, unresolved = _resolve_track(conn, pr.track_raw)
            note_parts = list(pr.notes)
            if recovered:
                note_parts.append(f"unparsed trailing text: {recovered}")
                issues.append({"session_id": session_id, "race_num": pr.race_num,
                               "kind": "merged_token", "raw": pr.track_raw,
                               "detail": f"recovered leading alias; trailing {recovered!r}"})
            if unresolved:
                issues.append({"session_id": session_id, "race_num": pr.race_num,
                               "kind": "unresolved_track", "raw": pr.track_raw,
                               "detail": "no alias match; race stored with no track"})
            # A numbered race with no track is already warned about in parse();
            # re-recording it here would double up the review queue.

            pr.track_id = track_id
            if dry_run:
                written_races += 1
                continue

            conn.execute(races.insert().values(
                session_id=session_id,
                race_num=pr.race_num,
                track_id=track_id,
                variant=pr.variant,
                placement=pr.placement,
                start_position=pr.start_position,
                lap1_position=pr.lap1_position,
                shortcut_hit=None,      # never 'na' — the doc records neither
                mate_placement=pr.mate_placement,
                note="\n".join(note_parts) or None,
                created_at=now, updated_at=now,
            ))
            written_races += 1

        if not dry_run:
            for w in [w for w in parsed.warnings if w["session"] == ps.index]:
                conn.execute(import_issues.insert().values(
                    session_id=session_id, race_num=w.get("race_num"),
                    kind=w["kind"], raw=w.get("raw"), detail=w["message"],
                    created_at=now,
                ))

    if not dry_run:
        for i in issues:
            conn.execute(import_issues.insert().values(**i, created_at=now))

    return {
        "sessions": written_sessions if not dry_run else len(parsed.sessions),
        "races": written_races,
        "placements": sum(1 for s in parsed.sessions for r in s.races
                          if r.placement is not None),
        "unresolved": sum(1 for i in issues if i["kind"] == "unresolved_track"),
        "issues": len(issues) + len(parsed.warnings),
        "warnings": parsed.warnings,
        "dry_run": dry_run,
    }


def import_text(conn, text: str, dry_run: bool = False, year: int | None = None) -> dict:
    return apply(conn, parse(text, year=year), dry_run=dry_run)
