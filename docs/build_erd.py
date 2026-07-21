"""
build_erd.py — FEC Database ERD generator (v1.2)

هدف النسخة: تقليل تقاطع الـ arrows + توازن أفضل + ثيم فاتح/داكن.

THEME = "light"  → خلفية بيضاء (الافتراضي)
THEME = "dark"   → خلفية سوداء (النسخة القديمة)

ملاحظة على المواقع: committees لازم ينزل قليلاً عشان يصير فوقه هامش فاضٍ —
أسهم committees الطويلة بتلفّ في هذا الهامش فوق الجداول. و employer_cluster
لازم يكون تحت donor_employments بالكامل عشان أسهمه تنزل عمودياً.
"""

from __future__ import annotations
import os
from collections import defaultdict
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

load_dotenv()

OUT = os.path.join(os.path.dirname(__file__), "fec_database_erd_v1_2.svg")
OUT_PNG = os.path.join(os.path.dirname(__file__), "fec_database_erd_v1_2.png")
PNG_DPI = 200   # ~4461×3117 — crisp enough to read every column type when zoomed

# ─────────────────────────────────────────────────────────────
#  Theme — flip THEME to "dark" for the old palette.
# ─────────────────────────────────────────────────────────────
THEME = "light"

PALETTES = {
    "dark": {
        "bg": "#0f1419", "title": "#e2e8f0", "subtitle": "#718096",
        "box": "#1a202c", "box_border": "#2c5282",
        "head_table": "#1e3a5f", "head_view": "#4a2c5f", "head_text": "#ffffff",
        "head_fact": "#9c6a1a",   # fact table — gold, stands apart from dimensions
        "col_strong": "#e2e8f0", "col_normal": "#a0aec0", "type": "#5a6b82",
        "alt_fill": "#212a37",   # solid zebra stripe (no opacity — PNG-safe)
        "accent": "#4fd1c5", "array": "#f6ad55",
        "pk": "#f6e05e", "uq": "#f687b3", "marker": "#718096",
        "divider": "#1f2733", "divider_text": "#3d4756",
        "legend_bg": "#1a202c", "legend_border": "#2d3748",
        "legend_label": "#718096", "legend_text": "#a0aec0",
        "arrow_op": 0.45,
    },
    "light": {
        "bg": "#ffffff", "title": "#0f172a", "subtitle": "#64748b",
        "box": "#ffffff", "box_border": "#cbd5e1",
        "head_table": "#2c5282", "head_view": "#6b3fa0", "head_text": "#ffffff",
        "head_fact": "#b45309",   # fact table — amber/gold, stands apart from dimensions
        "col_strong": "#0f172a", "col_normal": "#475569", "type": "#94a3b8",
        "alt_fill": "#f1f5f9",   # solid zebra stripe (no opacity — PNG-safe)
        "accent": "#0d9488", "array": "#ea580c",
        "pk": "#ca8a04", "uq": "#db2777", "marker": "#64748b",
        "divider": "#cbd5e1", "divider_text": "#94a3b8",
        "legend_bg": "#f8fafc", "legend_border": "#cbd5e1",
        "legend_label": "#64748b", "legend_text": "#334155",
        "arrow_op": 0.6,
    },
}
T = PALETTES[THEME]

# ─────────────────────────────────────────────────────────────
#  Visual constants
# ─────────────────────────────────────────────────────────────
ROW_H = 20
HEAD_H = 36
PAD = 10
CHAR_W = 7.3

# ─────────────────────────────────────────────────────────────
#  GROUPS — `addresses` sits in the CENTRE column as the shared dimension:
#  donor_addresses (left) and employers (left) both point right into it, and
#  it lines up with them so those arrows run almost straight. donors + the
#  contributions fact stay stacked beneath it. Geo reference tables sit in a
#  bottom band, lowered to clear the taller centre column.
# ─────────────────────────────────────────────────────────────
GROUPS = [
    # occupation_categories sits far-left, aligned with donor_employments so its
    # one arrow runs straight. The "referrers" column (employers + the two
    # bridges) sits directly LEFT of the centre so every arrow into the centre
    # is a single short hop — and rows are aligned so most run horizontally:
    #   employers ─→ addresses   ·   donor_addresses ─→ donors   ·
    #   donor_employments ─→ contributions' neighbours.
    # Zoning tuned so EVERY arrow is a single short hop (zero over-the-top arcs):
    #   dims (far-left) → donor cluster → centre → committees (right).
    # key_accomplices + leaders live in the CENTRE column with contributions, so
    # they sit between donors (left) and committees (right) and reach BOTH in one
    # hop — instead of arcing across the whole diagram.
    # The three core entities — donors (identity) · addresses (location) ·
    # contributions (the fact) — sit together in the CENTRE. Their feeders flank
    # them: the donor's own records on the left, the political side on the right,
    # the occupation lookup far-left. Every arrow stays a single short hop, and
    # donors (referenced by 5 tables) takes them from BOTH edges, so no single
    # side gets crowded.
    ("col_far",      60,  420, "v", 60, ["occupation_categories"]),
    ("col_left",    400,  150, "v", 60, ["employers", "donor_addresses", "donor_employments", "donor_images"]),
    ("col_center",  880,  150, "v", 60, ["addresses", "donors", "contributions"]),
    # committees sits LAST (bottom) so it lines up with contributions: the
    # fact->committee arrow stays horizontal instead of climbing past the
    # donor-bound arrows above it.
    ("col_right",  1300,  150, "v", 60, ["key_accomplices", "leaders", "leader_committees", "committees"]),
    ("geo_refs",     60,  920, "h", 80, ["us_states",
                                         "zcta_state_rel", "zip_centroids"]),
]

ISOLATED_KEYS = {"us_states", "zcta_state_rel", "zip_centroids"}
FACT_TABLES = {"contributions"}   # gold header — the central fact table


def fetch_schema():
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST"), port=os.getenv("PG_PORT"),
        user=os.getenv("PG_USER"), password=os.getenv("PG_PASSWORD"),
        dbname=os.getenv("PG_DBNAME"),
    )
    cur = conn.cursor()

    # Column types come straight from PostgreSQL via format_type() — the exact
    # canonical type ("double precision", "integer[]", "character varying(100)"),
    # no local mapping that could drift. The Schema-page explorer reads the same.
    cur.execute("""
        SELECT c.relname, a.attname,
               format_type(a.atttypid, a.atttypmod) AS pg_type,
               a.attnotnull
        FROM pg_attribute a
        JOIN pg_class c     ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v', 'm')
          AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY c.relname, a.attnum
    """)
    cols = defaultdict(list)
    for t, c, pg_type, notnull in cur.fetchall():
        cols[t].append((c, pg_type, None, notnull))

    cur.execute("""
        SELECT table_name, table_type FROM information_schema.tables
        WHERE table_schema = 'public'
    """)
    kind = {t: ("view" if k == "VIEW" else "table") for t, k in cur.fetchall()}

    cur.execute("""
        SELECT tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_name = tc.constraint_name
        WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = 'public'
    """)
    pk = defaultdict(set)
    for t, c in cur.fetchall():
        pk[t].add(c)

    cur.execute("""
        SELECT tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_name = tc.constraint_name
        WHERE tc.constraint_type = 'UNIQUE' AND tc.table_schema = 'public'
    """)
    uq = defaultdict(set)
    for t, c in cur.fetchall():
        uq[t].add(c)

    cur.execute("""
        SELECT tc.table_name, kcu.column_name,
               ccu.table_name AS ref_table, ccu.column_name AS ref_col
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_name = tc.constraint_name
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
    """)
    fks = []
    fk_cols = defaultdict(set)
    for t, c, rt, rc in cur.fetchall():
        fks.append((t, c, rt, rc))
        fk_cols[t].add(c)

    cur.close()
    conn.close()
    return cols, kind, pk, uq, fks, fk_cols


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def short_type(dt: str, ml=None) -> str:
    # The type is already PostgreSQL's own format_type() output — show it verbatim.
    return dt


def table_width(name, columns) -> int:
    longest = len(name) + 14
    for c, dt, ml, nn in columns:
        longest = max(longest, len(c) + len(short_type(dt, ml)) + 8)
    return max(200, int(longest * CHAR_W) + 2 * PAD)


def geometry(cols, layout_names) -> dict:
    g = {}
    for name in layout_names:
        if name not in cols:
            continue
        cset = cols[name]
        g[name] = (table_width(name, cset), HEAD_H + ROW_H * len(cset) + 6)
    return g


def calculate_layout(groups, geom) -> dict:
    pos = {}
    for _key, ax, ay, axis, gap, tables in groups:
        cx, cy = ax, ay
        for t in tables:
            if t not in geom:
                continue
            w, h = geom[t]
            pos[t] = (cx, cy)
            if axis == "v":
                cy += h + gap
            else:
                cx += w + gap
    return pos


def draw_table(name, box, columns, pk, uq, fk_cols, is_view) -> list:
    x, y, w, h, _col_y = box
    head_bg = (T["head_fact"] if name in FACT_TABLES
               else T["head_view"] if is_view else T["head_table"])
    out = []

    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" '
               f'fill="{T["box"]}" stroke="{T["box_border"]}" stroke-width="1.6"/>')
    out.append(f'<path d="M {x},{y+7} a7,7 0 0 1 7,-7 h {w-14} a7,7 0 0 1 7,7 '
               f'v {HEAD_H-7} h -{w} z" fill="{head_bg}"/>')
    out.append(f'<text x="{x+PAD}" y="{y+24}" fill="{T["head_text"]}" font-size="16.5" '
               f'font-weight="700">{esc(name)}</text>')

    for i, (c, dt, ml, nn) in enumerate(columns):
        ry = y + HEAD_H + ROW_H * i
        if i % 2 == 1:
            out.append(f'<rect x="{x+1}" y="{ry}" width="{w-2}" height="{ROW_H}" '
                       f'fill="{T["alt_fill"]}"/>')
        cy = ry + ROW_H - 5
        is_pk = c in pk.get(name, set())
        is_fk = c in fk_cols.get(name, set())
        is_uq = c in uq.get(name, set())

        marker, mcolor = "", T["marker"]
        if is_pk:
            marker, mcolor = "PK", T["pk"]
        elif is_fk:
            marker, mcolor = "FK", T["accent"]
        elif is_uq:
            marker, mcolor = "U", T["uq"]
        if marker:
            out.append(f'<text x="{x+PAD}" y="{cy}" fill="{mcolor}" font-size="9.5" '
                       f'font-weight="700" font-family="monospace">{marker}</text>')

        if dt.endswith("[]"):
            ncolor, weight = T["array"], "700"
        elif is_pk or is_fk:
            ncolor, weight = T["col_strong"], ("700" if is_pk else "400")
        else:
            ncolor, weight = T["col_normal"], "400"
        out.append(f'<text x="{x+PAD+22}" y="{cy}" fill="{ncolor}" font-size="12.5" '
                   f'font-weight="{weight}" font-family="monospace">{esc(c)}</text>')

        tcolor = T["array"] if dt.endswith("[]") else T["type"]
        out.append(f'<text x="{x+w-PAD}" y="{cy}" fill="{tcolor}" font-size="11" '
                   f'text-anchor="end" font-family="monospace">'
                   f'{esc(short_type(dt, ml))}{" *" if nn else ""}</text>')
    return out


def _arrowhead(x, y, entry) -> str:
    """Filled triangle sitting ON the referenced table's edge, pointing INTO
    it — i.e. the FK → PK direction. `entry` is the edge it lands on."""
    a = T["accent"]; s = 6.5
    if entry == 'l':      # lands on the LEFT edge  → points right (into table)
        pts = f"{x},{y} {x-s},{y-s*0.62} {x-s},{y+s*0.62}"
    elif entry == 'r':    # lands on the RIGHT edge → points left
        pts = f"{x},{y} {x+s},{y-s*0.62} {x+s},{y+s*0.62}"
    elif entry == 'b':    # lands on the BOTTOM edge → points up
        pts = f"{x},{y} {x-s*0.62},{y+s} {x+s*0.62},{y+s}"
    else:                 # 't' — lands on the TOP edge → points down
        pts = f"{x},{y} {x-s*0.62},{y-s} {x+s*0.62},{y-s}"
    return f'<polygon points="{pts}" fill="{a}"/>'


def _edge(path, fx, fy, tx, ty, entry, dashed=False) -> list:
    # Direction is explicit: a small dot anchors the FK (many) side; a filled
    # arrowhead lands on the referenced table's edge pointing INTO it (FK → PK).
    # A "secondary" FK (e.g. previous_employer_id) is dashed so two arrows into
    # employers read as current vs previous — not a duplicate/mistake.
    a = T["accent"]
    dash = ' stroke-dasharray="6 4"' if dashed else ''
    return [
        f'<path d="{path}" stroke="{a}" stroke-width="1.7" fill="none" '
        f'opacity="{T["arrow_op"]}" stroke-linejoin="round"{dash}/>',
        f'<circle cx="{fx}" cy="{fy}" r="2.8" fill="{a}"/>',
        _arrowhead(tx, ty, entry),
    ]


def _is_secondary(col: str) -> bool:
    """FK columns that are a SECOND reference to a table already linked once
    (drawn dashed)."""
    return "previous" in (col or "").lower()


def draw_relationships(fks, boxes) -> list:
    """Orthogonal router with per-corridor LANE allocation.

    Every cross-column edge gets its OWN vertical lane inside the column-gap it
    crosses, so parallel arrows never sit on top of one another; lanes are
    ordered by the edge's mid-height to keep crossings to a minimum. Arrows that
    land on the same target row + side are fanned a few px apart so their heads
    don't pile into one blob. Edges between two tables stacked in the SAME column
    route in a slim channel hugging that column's left side.
    """
    out = []

    # Columns = tables sharing an x; record each column's right edge (widest
    # table) so we know the clear gap between adjacent columns.
    # Main columns = CORE tables only. The isolated geo tables sit in a separate
    # bottom band; their x-positions must NOT count as columns or they'd inflate
    # the hop count for core edges and wrongly trigger over-the-top routing.
    col_right = {}
    for name, (x, y, w, h, cy) in boxes.items():
        if name in ISOLATED_KEYS:
            continue
        col_right[round(x)] = max(col_right.get(round(x), 0.0), x + w)
    cidx = {cx: i for i, cx in enumerate(sorted(col_right))}

    # ── Build edges with base geometry (exit point, landing point, entry side). ──
    edges, iso = [], []
    for (t, c, rt, rc) in fks:
        if t not in boxes or rt not in boxes:
            continue
        sx, sy, sw, sh, scy = boxes[t]
        rx, ry, rw, rh, rcy = boxes[rt]
        fy = scy.get(c, sy + sh / 2)
        py = rcy.get(rc, ry + rh / 2)
        if t in ISOLATED_KEYS or rt in ISOLATED_KEYS:    # geo band — simple side arrow
            iso.append((sx, sw, fy, rx, rw, py, _is_secondary(c)))
            continue
        si, ti = cidx[round(sx)], cidx[round(rx)]
        same, hop = si == ti, abs(si - ti)
        if same or si < ti:
            entry, x1, x2 = "l", (sx if same else sx + sw), rx          # land on left edge
        else:
            entry, x1, x2 = "r", sx, rx + rw                            # land on right edge
        edges.append({"rt": rt, "same": same, "hop": hop, "entry": entry,
                      "sx": sx, "sw": sw, "sy": sy, "rx": rx, "rw": rw, "ry": ry,
                      "x1": x1, "x2": x2, "fy": fy, "py": py, "dashed": _is_secondary(c)})

    # ── Fan arrows that land on the same target row + side (1-hop only; the
    #    over-top edges land on the TOP edge and are fanned there instead). ──
    landing = defaultdict(list)
    for e in edges:
        if e["hop"] <= 1:
            landing[(e["rt"], e["entry"], round(e["py"]))].append(e)
    for g in landing.values():
        if len(g) > 1:
            g.sort(key=lambda e: e["fy"])
            for j, e in enumerate(g):
                e["py"] += (j - (len(g) - 1) / 2) * 8

    # ── Classify: same-column · adjacent cross · multi-hop (over the top). ──
    cross, same_col, overtop = defaultdict(list), defaultdict(list), []
    for e in edges:
        if e["same"]:
            same_col[round(e["sx"])].append(e)
        elif e["hop"] == 1:
            cross[min(cidx[round(e["sx"])], cidx[round(e["rx"])])].append(e)
        else:
            overtop.append(e)

    M = 20  # keep lanes clear of the column edges (room for same-column hugging)
    for elist in cross.values():
        # The gap is identical for every edge crossing this corridor.
        e0 = elist[0]
        if e0["entry"] == "l":
            gl, gr = col_right[round(e0["sx"])], e0["rx"]
        else:
            gl, gr = col_right[round(e0["rx"])], e0["sx"]
        a, b = (gl + M, gr - M) if (gr - gl) > 2 * M else (gl, gr)
        mid = (a + b) / 2
        # Split by DIRECTION: arrows heading right (into the right column) take
        # the gap's right half, arrows heading left take the left half — so the
        # two directions' vertical runs never cross. Within a half, sort by
        # mid-height to keep same-direction crossings minimal.
        right = sorted((e for e in elist if e["entry"] == "l"), key=lambda e: (e["fy"] + e["py"]) / 2)
        left  = sorted((e for e in elist if e["entry"] == "r"), key=lambda e: (e["fy"] + e["py"]) / 2)
        if left and right:
            bands = [(left, a, mid - 4), (right, mid + 4, b)]
        else:
            bands = [(left or right, a, b)]
        for band, lo, hi in bands:
            k = len(band)
            for i, e in enumerate(band):
                lane = lo + (hi - lo) * (i + 1) / (k + 1)
                out += _edge(f'M {e["x1"]},{e["fy"]} H {lane:.1f} V {e["py"]:.1f} H {e["x2"]}',
                             e["x1"], e["fy"], e["x2"], e["py"], e["entry"], e["dashed"])

    # ── Multi-hop: route up and OVER the top margin so the arrow never cuts
    #    through the tables it spans. Each gets its own top lane + a fanned
    #    landing on the target's top edge. ──
    if overtop:
        top_y = min(b[1] for b in boxes.values()) - 26
        overtop.sort(key=lambda e: e["fy"])
        n = len(overtop)
        for i, e in enumerate(overtop):
            tleft = cidx[round(e["rx"])] < cidx[round(e["sx"])]    # target is to the left
            sx_exit = e["sx"] if tleft else e["sx"] + e["sw"]
            slane = sx_exit - 16 if tleft else sx_exit + 16
            ty = top_y - i * 13
            tx = e["rx"] + e["rw"] / 2 + (i - (n - 1) / 2) * 16
            out += _edge(f'M {sx_exit},{e["fy"]} H {slane:.1f} V {ty:.1f} H {tx:.1f} V {e["ry"]}',
                         sx_exit, e["fy"], tx, e["ry"], "t", e["dashed"])

    # ── Same-column: hug the column's left side in nested channels. ──
    for elist in same_col.values():
        elist.sort(key=lambda e: min(e["fy"], e["py"]))
        for i, e in enumerate(elist):
            lane = e["sx"] - (10 + i * 7)
            out += _edge(f'M {e["sx"]},{e["fy"]} H {lane:.1f} V {e["py"]:.1f} H {e["rx"]}',
                         e["sx"], e["fy"], e["rx"], e["py"], "l", e["dashed"])

    # ── Isolated geo-band edges: a simple direct side arrow. ──
    for (sx, sw, fy, rx, rw, py, dashed) in iso:
        if rx >= sx:
            x1, x2, entry, chan = sx + sw, rx, "l", rx - 14
        else:
            x1, x2, entry, chan = sx, rx + rw, "r", rx + rw + 14
        out += _edge(f'M {x1},{fy} H {chan:.1f} V {py:.1f} H {x2}', x1, fy, x2, py, entry, dashed)
    return out


def draw_legend(lx, ly) -> list:
    out = [f'<rect x="{lx-12}" y="{ly-24}" width="270" height="167" rx="6" '
           f'fill="{T["legend_bg"]}" stroke="{T["legend_border"]}"/>',
           f'<text x="{lx}" y="{ly-4}" fill="{T["legend_label"]}" font-size="11" '
           f'font-weight="700" letter-spacing="1">LEGEND</text>']
    # base tables are normalized — no array columns (committee_ids etc. live in the
    # v_leaders VIEW, derived from the leader_committees junction shown here).
    items = [("PK", T["pk"], "primary key"), ("FK", T["accent"], "foreign key"),
             ("U", T["uq"], "unique"), ("*", T["type"], "NOT NULL")]
    yy = ly + 18
    for mk, col, label in items:
        out.append(f'<text x="{lx}" y="{yy}" fill="{col}" font-size="11.5" '
                   f'font-weight="700" font-family="monospace">{mk}</text>')
        out.append(f'<text x="{lx+28}" y="{yy}" fill="{T["legend_text"]}" font-size="11.5">{label}</text>')
        yy += 19
    # dashed line = a second FK to a table already linked (e.g. previous employer)
    out.append(f'<line x1="{lx}" y1="{yy-4}" x2="{lx+20}" y2="{yy-4}" stroke="{T["accent"]}" '
               f'stroke-width="1.4" stroke-dasharray="6 4" opacity="{T["arrow_op"]}"/>')
    out.append(f'<text x="{lx+28}" y="{yy}" fill="{T["legend_text"]}" font-size="11.5">'
               f'secondary FK (e.g. previous)</text>')
    yy += 19
    out.append(f'<line x1="{lx}" y1="{yy-4}" x2="{lx+12}" y2="{yy-4}" stroke="{T["accent"]}" '
               f'stroke-width="1.7" opacity="{T["arrow_op"]}"/>')
    out.append(_arrowhead(lx + 18, yy - 4, "l"))
    out.append(f'<text x="{lx+28}" y="{yy}" fill="{T["legend_text"]}" font-size="11.5">'
               f'arrow → references (FK → PK)</text>')
    return out


def render_svg() -> str:
    """Build the ERD and return the SVG as a string (no file written).

    The single source of the diagram — used both by the CLI (build_svg writes it
    to a file)."""
    cols, kind, pk, uq, fks, fk_cols = fetch_schema()

    layout_names = [t for _k, _x, _y, _a, _g, ts in GROUPS for t in ts]
    geom = geometry(cols, layout_names)
    pos = calculate_layout(GROUPS, geom)

    boxes = {}
    for name, (x, y) in pos.items():
        w, h = geom[name]
        col_y = {c: y + HEAD_H + ROW_H * i + ROW_H / 2
                 for i, (c, *_rest) in enumerate(cols[name])}
        boxes[name] = (x, y, w, h, col_y)

    max_x = max(x + boxes[n][2] for n, (x, y) in pos.items()) + 60
    max_y = max(y + boxes[n][3] for n, (x, y) in pos.items()) + 60
    y_isolated = min(pos[n][1] for n in ISOLATED_KEYS if n in pos) - 34

    n_tables = sum(1 for n in pos if kind.get(n) == "table")
    today = datetime.now().strftime("%Y-%m-%d")

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{max_x}" height="{max_y}" '
           f'viewBox="0 0 {max_x} {max_y}" font-family="Segoe UI, Arial, sans-serif">',
           f'<rect width="{max_x}" height="{max_y}" fill="{T["bg"]}"/>',
           f'<text x="40" y="44" fill="{T["title"]}" font-size="26" font-weight="700">'
           f'FEC Database — Schema v1.2</text>',
           f'<text x="40" y="66" fill="{T["subtitle"]}" font-size="13">'
           f'{n_tables} tables · live from {esc(os.getenv("PG_DBNAME",""))} · generated {today}</text>']

    svg.append(f'<line x1="40" y1="{y_isolated}" x2="{max_x-40}" y2="{y_isolated}" '
               f'stroke="{T["divider"]}" stroke-width="1.5" stroke-dasharray="5 7"/>')
    svg.append(f'<text x="50" y="{y_isolated-10}" fill="{T["divider_text"]}" font-size="12" '
               f'font-weight="600" letter-spacing="2">CORE (linked to donors)</text>')
    svg.append(f'<text x="50" y="{y_isolated+22}" fill="{T["divider_text"]}" font-size="12" '
               f'font-weight="600" letter-spacing="2">'
               f'REFERENCE &amp; RAW ARCHIVE (standalone — no FK to core)</text>')

    svg += draw_relationships(fks, boxes)
    for name, (x, y) in pos.items():
        svg += draw_table(name, boxes[name], cols[name], pk, uq, fk_cols,
                          kind.get(name) == "view")
    svg += draw_legend(90, 130)

    svg.append("</svg>")

    missing = [n for n in cols if kind.get(n) == "table" and n not in pos]
    if missing:
        print(f"     NOT placed (add to a GROUP): {missing}")
    return "\n".join(svg)


def write_png(svg_path=OUT, png_path=OUT_PNG, dpi=PNG_DPI):
    """Rasterize the SVG we just wrote into a PNG — same single source, just a
    different format for sharing/printing. Needs svglib + reportlab."""
    from svglib.svglib import svg2rlg
    from reportlab.graphics import renderPM
    drawing = svg2rlg(svg_path)
    renderPM.drawToFile(drawing, png_path, fmt="PNG", dpi=dpi)
    return int(drawing.width), int(drawing.height)


def build_svg():
    """CLI: render the ERD to the docs SVG file, then a PNG from that same SVG."""
    svg = render_svg()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"[OK] wrote {OUT}  (theme={THEME})")
    try:
        w, h = write_png()
        print(f"[OK] wrote {OUT_PNG}  ({int(w*PNG_DPI/72)}x{int(h*PNG_DPI/72)} @ {PNG_DPI}dpi)")
    except Exception as e:
        print(f"[skip] PNG not written ({e}); SVG is the source of truth")


if __name__ == "__main__":
    build_svg()
