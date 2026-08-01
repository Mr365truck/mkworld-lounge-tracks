"""Extract text from Lounge.pdf.

The PDF is a Google Docs export with subset TrueType fonts: text is stored as raw
glyph ids with no ToUnicode CMap, so no off-the-shelf extractor recovers it. The
subset happens to use the standard glyph ordering, where glyph_id + 29 == codepoint
across the printable ASCII range -- see dec() below.

Usage:  python3 tools/extract_lounge.py [path/to/Lounge.pdf] > data/lounge-raw.txt
"""
import re, zlib, sys, os

PDF = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'Lounge.pdf')

data = open(PDF, 'rb').read()

# --- collect indirect objects ---
objs = {}
for m in re.finditer(rb'(\d+)\s+(\d+)\s+obj(.*?)endobj', data, re.S):
    objs[int(m.group(1))] = m.group(3)

def stream_of(body):
    m = re.search(rb'stream\r?\n(.*?)\r?\nendstream', body, re.S)
    if not m:
        return None
    raw = m.group(1)
    if b'/FlateDecode' in body:
        try:
            return zlib.decompress(raw)
        except Exception:
            return None
    return raw

# --- find page objects in document order ---
page_objs = []
for num, body in sorted(objs.items()):
    if re.search(rb'/Type\s*/Page[^s]', body):
        page_objs.append((num, body))

def contents_for(body):
    m = re.search(rb'/Contents\s+(\d+)\s+\d+\s+R', body)
    if m:
        return stream_of(objs.get(int(m.group(1)), b''))
    m = re.search(rb'/Contents\s*\[(.*?)\]', body, re.S)
    if m:
        out = b''
        for r in re.finditer(rb'(\d+)\s+\d+\s+R', m.group(1)):
            s = stream_of(objs.get(int(r.group(1)), b''))
            if s: out += s + b'\n'
        return out
    return None

def dec(gid):
    """Subset font glyph id -> ascii. Verified: glyph+29 == codepoint for 0x20-0x7E."""
    cp = gid + 29
    return chr(cp) if 0x20 <= cp <= 0x7E else ''

num_re = rb'(-?[\d.]+)'

def parse_page(content):
    lines = {}          # y -> list of (x, text)
    ctm_y = 0.0
    ctm_sy = 1.0
    ctm_sx = 1.0
    ctm_x = 0.0
    stack = []
    tm_y = 0.0
    cur_x = cur_y = 0.0
    buf = []
    start_x = 0.0

    def flush():
        nonlocal buf
        if buf:
            y = round(ctm_y + ctm_sy * (tm_y + cur_y), 1)
            lines.setdefault(y, []).append((ctm_x + ctm_sx * start_x, ''.join(buf)))
            buf = []

    for tok in re.finditer(
        rb'q|Q|'
        rb'([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+cm|'
        rb'1\s+0\s+0\s+-1\s+[\d.\-]+\s+([\d.\-]+)\s+Tm|'
        rb'([\d.\-]+)\s+([\d.\-]+)\s+Td|'
        rb'<([0-9A-Fa-f]+)>\s*Tj|'
        rb'BT|ET', content):
        t = tok.group(0)
        if t == b'q':
            stack.append((ctm_x, ctm_sx, ctm_y, ctm_sy))
        elif t == b'Q':
            if stack: ctm_x, ctm_sx, ctm_y, ctm_sy = stack.pop()
        elif t.endswith(b'cm'):
            sx = float(tok.group(1)); sy = float(tok.group(4))
            tx = float(tok.group(5)); ty = float(tok.group(6))
            ctm_x = ctm_x + ctm_sx * tx
            ctm_sx = ctm_sx * sx
            ctm_y = ctm_y + ctm_sy * ty
            ctm_sy = ctm_sy * sy
        elif t.endswith(b'Tm'):
            flush()
            tm_y = float(tok.group(7))
            cur_x = cur_y = 0.0
        elif t.endswith(b'Td'):
            dx = float(tok.group(8)); dy = float(tok.group(9))
            if dy != 0:
                flush()
                cur_y += dy
                cur_x = dx
                start_x = cur_x
            else:
                cur_x += dx
        elif t.endswith(b'Tj'):
            if not buf:
                start_x = cur_x
            buf.append(dec(int(tok.group(10), 16)))
        elif t == b'ET':
            flush()
    flush()

    out = []
    for y in sorted(lines, reverse=True):
        segs = sorted(lines[y], key=lambda p: p[0])
        out.append(''.join(s for _, s in segs))
    return out

all_lines = []
for i, (num, body) in enumerate(page_objs, 1):
    c = contents_for(body)
    if not c:
        continue
    pl = parse_page(c)
    if pl:
        all_lines.append(f'===== PAGE {i} (obj {num}) =====')
        all_lines.extend(pl)

print('\n'.join(all_lines))
