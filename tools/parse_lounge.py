"""Parse the extracted Lounge text into structured JSON.

Reads the output of tools/extract_lounge.py and produces one record per session
with its races, resolving track spellings through the alias table in Appendix A
of mogi-tracker-spec.md.

This is a prototype of the paste importer described in spec section 7. It follows
the same rule the spec sets out: anything that does not resolve cleanly is recorded
as a warning rather than guessed at or silently dropped.

Usage:  python3 tools/parse_lounge.py data/lounge-raw.txt > data/lounge-sessions.json
"""
import re, json, sys, os

# --- alias table (Appendix A). alias -> canonical code -------------------------
TRACKS = {
    'MBC':  ('Mario Bros. Circuit',   0, 0, ['mbc']),
    'CC':   ('Crown City',            0, 0, ['cc', 'crown city']),
    'WS':   ('Whistlestop Summit',    0, 1, ['ws', 'wss', 'whistlestop']),
    'DKSP': ('DK Spaceport',          0, 0, ['dksp']),
    'SP':   ('Starview Peak',         0, 0, ['sp', 'starview']),
    'FO':   ('Faraway Oasis',         0, 0, ['fo', 'faraway']),
    'PS':   ('Peach Stadium',         0, 0, ['ps']),
    'SSS':  ('Salty Salty Speedway',  0, 0, ['sss', 'salty']),
    'GBR':  ('Great ? Block Ruins',   0, 1, ['gbr']),
    'CCF':  ('Cheep Cheep Falls',     0, 0, ['ccf']),
    'DD':   ('Dandelion Depths',      0, 0, ['dd', 'dandelion']),
    'BCi':  ('Boo Cinema',            0, 0, ['bci']),
    'DBB':  ('Dry Bones Burnout',     0, 0, ['dbb']),
    'BC':   ("Bowser's Castle",       0, 1, ['bc', 'castle']),
    'AH':   ('Acorn Heights',         0, 1, ['ah', 'acorn']),
    'rMC':  ('Mario Circuit',      None, 0, ['rmc', 'mc']),
    'rDH':  ('Desert Hills',          1, 0, ['rdh', 'hills', 'desert hills']),
    'rSGB': ('Shy Guy Bazaar',        1, 0, ['rsgb', 'sgb', 'bazaar']),
    'rWSt': ('Wario Stadium',         1, 0, ['rwst', 'stadium']),
    'rAF':  ('Airship Fortress',      1, 0, ['raf', 'af', 'airship']),
    'rDKP': ('DK Pass',               1, 0, ['rdkp', 'dkp', 'pass']),
    'rSHS': ('Sky-High Sundae',       1, 0, ['shs', 'sks', 'sundae']),
    'rWSh': ('Wario Shipyard',        1, 0, ['rwsh', 'shipyard']),
    'rKTB': ('Koopa Troopa Beach',    1, 0, ['rktb', 'ktb']),
    'rPB':  ('Peach Beach',           1, 0, ['rpb', 'pb', 'peach beach']),
    'rDDJ': ('Dino Dino Jungle',      1, 0, ['rddj', 'ddj']),
    'rMMM': ('Moo Moo Meadows',       1, 0, ['rmmm', 'mmm']),
    'rCM':  ('Choco Mountain',        1, 0, ['rcm', 'cm', 'choco']),
    'rTF':  ("Toad's Factory",        1, 0, ['rtf', 'tf']),
    'RR':   ('Rainbow Road',       None, 0, ['rr']),
}
ALIAS = {}
for code, (_n, _r, _g, al) in TRACKS.items():
    ALIAS[code.lower()] = code
    for a in al:
        ALIAS[a] = code

FORMATS = ['tournament', 'tourney', 'ffa', '2v2', '3v3', '4v4', '6v6']

warnings = []
def warn(sess, msg):
    warnings.append({'session': sess, 'message': msg})


def resolve_track(raw, sess, race_num):
    """raw track text -> (code, variant, notes, unresolved_raw)."""
    notes, variant = [], '3lap'
    t = raw.strip()

    # inline parentheticals: '(intermission to)', '(why)'
    for p in re.findall(r'\(([^)]*)\)', t):
        if 'intermission' in p.lower():
            variant = 'intermission'
        else:
            notes.append(p.strip())
    t = re.sub(r'\([^)]*\)', '', t).strip()

    key = t.lower().strip(' .,')
    if key in ALIAS:
        return ALIAS[key], variant, notes, None

    # 'Farawayhandling' is a known extractor artifact: two adjacent text runs
    # merged. Recover the leading alias rather than dropping the race.
    for alias in sorted(ALIAS, key=len, reverse=True):
        if key.startswith(alias) and len(key) > len(alias):
            notes.append('unparsed trailing text: ' + key[len(alias):])
            warn(sess, f'race {race_num}: recovered {ALIAS[alias]!r} from merged token {t!r}')
            return ALIAS[alias], variant, notes, None

    warn(sess, f'race {race_num}: unresolved track {t!r}')
    return None, variant, notes, t


def parse_header(line):
    """Pull structured fields out of a session header line."""
    h = {'header_raw': line.strip(), 'format': None, 'date': None, 'time': None,
         'room_min_mmr': None, 'room_max_mmr': None, 'room_avg_mmr': None,
         'seat': None, 'mate_mmr': None, 'spectated': False}
    low = line.lower()

    if 'viewer' in low:
        h['spectated'] = True
    for f in FORMATS:
        if re.search(r'\b' + re.escape(f) + r'\b', low):
            h['format'] = 'tournament' if f in ('tournament', 'tourney') else f
            break

    m = re.search(r'\b(\d{1,2}/\d{1,2})\b', line)
    if m:
        h['date'] = m.group(1)
    else:
        # month-name form, e.g. 'friday jun 6 @ 6pm'
        MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun',
                  'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
        m = re.search(r'\b(' + '|'.join(MONTHS) + r')[a-z]*\.?\s+(\d{1,2})\b', low)
        if m:
            h['date'] = f'{MONTHS.index(m.group(1)) + 1}/{int(m.group(2))}'

    m = re.search(r'\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}\s*noon|noon)\b', low)
    if m:
        h['time'] = m.group(1).strip()

    # int fields; tolerate 'maX:', 'seat 5' without a colon, and '4,085'
    for key, pat in (('room_min_mmr', 'min'), ('room_max_mmr', 'max'),
                     ('room_avg_mmr', 'avg'), ('seat', 'seat'), ('mate_mmr', 'mate')):
        m = re.search(pat + r'\s*:?\s*([\d,]+)', low)
        if m:
            h[key] = int(m.group(1).replace(',', ''))
    return h


def is_header(line):
    low = line.lower()
    if re.match(r'^\d{1,2}\.', line) or re.match(r'^[a-z]\.', line):
        return False
    if 'min:' in low or 'min :' in low:
        return True
    if re.search(r'\b\d{1,2}/\d{1,2}\b', line) and any(f in low for f in FORMATS):
        return True
    return any(re.search(r'\b' + re.escape(f) + r'\b', low) for f in FORMATS)


def main(path):
    lines = [l.rstrip() for l in open(path)]
    sessions, cur, cur_race = [], None, None
    last_date = None
    pending_event = None
    page = 0

    def close():
        nonlocal cur, cur_race
        if cur is not None:
            sessions.append(cur)
        cur, cur_race = None, None

    def start(h):
        nonlocal cur, cur_race, last_date
        close()
        if h['date']:
            last_date = h['date']
        elif last_date:
            h['date'] = last_date
            h['date_inherited'] = True
        h['index'] = len(sessions) + 1
        h['page'] = page
        h['event'] = pending_event
        h['races'] = []
        h['notes'] = []
        cur = h
        cur_race = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = re.match(r'^===== PAGE (\d+)', line)
        if m:
            page = int(m.group(1))
            continue

        if line.lower().startswith('log:'):
            break  # trailing to-do block, not session data

        # tournament rooms are separate sessions under one event header
        m = re.match(r'^Room (\d+)$', line, re.I)
        if m:
            base = dict(cur) if cur else {}
            start({**parse_header(pending_event or ''), 'room': int(m.group(1)),
                   'format': 'tournament'})
            cur['header_raw'] = f"{pending_event or ''} / Room {m.group(1)}".strip(' /')
            continue

        mr = re.match(r'^(\d{1,2})\.\s*(.*)$', line)
        ms = re.match(r'^([a-z])\.\s*(.*)$', line)

        if mr:
            if cur is None:
                start(parse_header(''))
                warn(cur['index'], 'races began with no session header')
            num, body = int(mr.group(1)), mr.group(2).strip()
            # Race numbering restarting mid-block means a new session began without
            # its own header -- the abandoned-then-restarted case in spec section 10.
            if cur['races'] and num <= cur['races'][-1]['race_num']:
                prev = cur
                start({**{k: v for k, v in prev.items()
                          if k not in ('races', 'notes', 'index', 'event')},
                       'header_raw': prev['header_raw'] + ' [restart: no header]',
                       'restarted_from': prev['index']})
                warn(prev['index'],
                     f'race numbering restarted at {num}; split into session '
                     f'{cur["index"]} (abandoned session followed by a fresh one)')
            cur_race = {'race_num': num, 'track_raw': body or None,
                        'track_code': None, 'variant': '3lap', 'placement': None,
                        'start_position': None, 'mate_placement': None, 'notes': []}
            if body:
                code, variant, notes, unresolved = resolve_track(body, cur['index'], num)
                cur_race.update(track_code=code, variant=variant, notes=notes)
                if unresolved:
                    cur_race['unresolved'] = unresolved
            else:
                warn(cur['index'], f'race {num}: numbered race with no track')
            cur['races'].append(cur_race)
            continue

        if ms and cur_race is not None:
            body = ms.group(2).strip()
            if not body:
                continue
            low = body.lower()
            m2 = re.match(r'^start:?\s*(\d+|\?)', low)
            if m2:
                cur_race['start_position'] = None if m2.group(1) == '?' else int(m2.group(1))
                continue
            m2 = re.match(r'^mate:?\s*(\d+)', low)
            if m2:
                cur_race['mate_placement'] = int(m2.group(1))
                continue
            m2 = re.fullmatch(r'(\d{1,2})', low)
            if m2 and cur_race['placement'] is None:
                cur_race['placement'] = int(m2.group(1))
                continue
            cur_race['notes'].append(body)
            continue

        if is_header(line):
            low = line.lower()
            if ('tourney' in low or 'tournament' in low) and 'min' not in low:
                pending_event = line          # event line; rooms follow
                close()
                continue
            pending_event = None
            start(parse_header(line))
            continue

        # free-standing prose: a note on the current session
        if cur is not None:
            cur['notes'].append(line)
        else:
            warn(None, f'orphan line: {line!r}')

    close()

    # derive expected_races / aborted per spec section 3
    for s in sessions:
        n = len(s['races'])
        s['expected_races'] = 8 if s.get('format') == 'tournament' else 12
        # A session with no races at all (spectated, or logged then never played)
        # is 'finished' in the only sense that matters: it will never gain rows.
        s['aborted'] = bool(n < s['expected_races'] and n <= 2)
        s['placements_recorded'] = sum(1 for r in s['races'] if r['placement'] is not None)
        s['is_complete'] = bool(s['aborted'] or
                                s['placements_recorded'] == s['expected_races'])
        if n and n != s['expected_races'] and not s['aborted']:
            warn(s['index'], f"{n} races, expected {s['expected_races']}")

    out = {
        'source': 'Lounge.pdf',
        'note': 'Prototype of the spec section 7 paste importer. '
                'Codes follow Appendix A and are provisional.',
        'track_table': {c: {'full_name': n, 'is_retro': r, 'has_gate': g, 'aliases': a}
                        for c, (n, r, g, a) in TRACKS.items()},
        'counts': {
            'sessions': len(sessions),
            'sessions_with_races': sum(1 for s in sessions if s['races']),
            'races': sum(len(s['races']) for s in sessions),
            'placements': sum(s['placements_recorded'] for s in sessions),
            'unresolved_tracks': sum(1 for s in sessions for r in s['races']
                                     if r.get('unresolved')),
            'warnings': len(warnings),
        },
        'sessions': sessions,
        'warnings': warnings,
    }
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'data/lounge-raw.txt')
