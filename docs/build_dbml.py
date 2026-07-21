"""
build_dbml.py — generate dbdiagram.io DBML from the LIVE database (v1.2).

Reads information_schema so the .dbml can never drift from the real schema.
Paste the output file into https://dbdiagram.io (the editor pane, not SQL import).

Usage:  env\\Scripts\\python.exe docs/build_dbml.py
Output: docs/fec_database_dbdiagram.dbml
"""
from __future__ import annotations

import os
from collections import defaultdict

import psycopg2
from dotenv import load_dotenv

load_dotenv()
OUT = os.path.join(os.path.dirname(__file__), "fec_database_dbdiagram.dbml")

# Logical table order (groups related tables so dbdiagram's auto-layout is sane)
TABLE_ORDER = [
    "occupation_categories", "committees", "donors", "addresses", "employers",
    "donor_addresses", "donor_employments", "contributions",
    "leaders", "leader_committees", "key_accomplices",
    "us_states", "zcta_state_rel", "zip_centroids",
]


def dbml_type(dt, ml):
    m = {
        "integer": "int", "text": "text", "numeric": "decimal",
        "double precision": "double", "date": "date",
        "character varying": f"varchar({ml})" if ml else "varchar",
        "timestamp without time zone": "timestamp",
        "ARRAY": "int[]", "boolean": "boolean",
    }
    return m.get(dt, dt)


def main():
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST"), port=os.getenv("PG_PORT"),
        user=os.getenv("PG_USER"), password=os.getenv("PG_PASSWORD"),
        dbname=os.getenv("PG_DBNAME"),
    )
    cur = conn.cursor()

    cur.execute("""
        SELECT table_name, column_name, data_type, character_maximum_length, is_nullable
        FROM information_schema.columns c
        WHERE table_schema='public'
          AND table_name IN (SELECT table_name FROM information_schema.tables
                             WHERE table_schema='public' AND table_type='BASE TABLE')
        ORDER BY table_name, ordinal_position
    """)
    cols = defaultdict(list)
    for t, c, dt, ml, nn in cur.fetchall():
        cols[t].append((c, dt, ml, nn == "NO"))

    cur.execute("""
        SELECT tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu ON kcu.constraint_name=tc.constraint_name
        WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_schema='public'
    """)
    pk = defaultdict(set)
    for t, c in cur.fetchall():
        pk[t].add(c)

    # composite uniques -> index block; single-col uniques -> inline [unique]
    cur.execute("""
        SELECT tc.constraint_name, tc.table_name, kcu.column_name, kcu.ordinal_position
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu ON kcu.constraint_name=tc.constraint_name
        WHERE tc.constraint_type='UNIQUE' AND tc.table_schema='public'
        ORDER BY tc.constraint_name, kcu.ordinal_position
    """)
    uq_by_con = defaultdict(list)
    con_table = {}
    for cn, t, c, _ in cur.fetchall():
        uq_by_con[cn].append(c)
        con_table[cn] = t
    single_uq = defaultdict(set)       # table -> {col}
    composite_uq = defaultdict(list)   # table -> [ [cols], ... ]
    for cn, clist in uq_by_con.items():
        t = con_table[cn]
        if len(clist) == 1:
            single_uq[t].add(clist[0])
        else:
            composite_uq[t].append(clist)

    cur.execute("""
        SELECT tc.table_name, kcu.column_name, ccu.table_name, ccu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu ON kcu.constraint_name=tc.constraint_name
        JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name=tc.constraint_name
        WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public'
    """)
    fks = cur.fetchall()

    # COMMENT ON metadata (objsubid=0 → table comment, >0 → column comment)
    cur.execute("""
        SELECT c.relname, d.objsubid, a.attname, d.description
        FROM pg_description d
        JOIN pg_class c     ON c.oid = d.objoid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.objsubid
        WHERE n.nspname = 'public' AND c.relkind = 'r'
    """)
    tbl_comment, col_comment = {}, {}
    for relname, subid, attname, desc in cur.fetchall():
        if subid == 0:
            tbl_comment[relname] = desc
        else:
            col_comment[(relname, attname)] = desc

    # row counts (for a comment header)
    counts = {}
    for t in cols:
        cur.execute(f"SELECT count(*) FROM {t}")
        counts[t] = cur.fetchone()[0]
    cur.close()
    conn.close()

    # dbdiagram note string — triple-quoted so apostrophes inside are safe
    def note(s):
        return "'''" + s.replace("'''", "''") + "'''"

    out = []
    out.append("// FEC Database — Schema v1.2  (generated from live DB)")
    out.append("// Paste into the dbdiagram.io EDITOR pane (not SQL import).")
    out.append("// Regenerate: env\\Scripts\\python.exe docs/build_dbml.py")
    out.append("")

    ordered = [t for t in TABLE_ORDER if t in cols] + [t for t in cols if t not in TABLE_ORDER]
    for t in ordered:
        out.append(f"Table {t} {{  // {counts.get(t,0):,} rows")
        for c, dt, ml, nn in cols[t]:
            attrs = []
            is_pk = c in pk.get(t, set())
            if is_pk:
                attrs.append("pk")
            if c in single_uq.get(t, set()):
                attrs.append("unique")
            # pk implies NOT NULL in dbdiagram — only state it for non-pk cols
            if nn and not is_pk:
                attrs.append("not null")
            if (t, c) in col_comment:
                attrs.append(f"note: {note(col_comment[(t, c)])}")
            a = f" [{', '.join(attrs)}]" if attrs else ""
            out.append(f"  {c} {dbml_type(dt, ml)}{a}")
        if composite_uq.get(t):
            out.append("  indexes {")
            for clist in composite_uq[t]:
                out.append(f"    ({', '.join(clist)}) [unique]")
            out.append("  }")
        if t in tbl_comment:
            out.append(f"  Note: {note(tbl_comment[t])}")
        out.append("}")
        out.append("")

    out.append("// ── Foreign keys (real, enforced) ──")
    for t, c, rt, rc in sorted(fks):
        out.append(f"Ref: {t}.{c} > {rt}.{rc}")
    out.append("")
    out.append("// Note: leader↔committee is a real M:N via the leader_committees junction.")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"[OK] wrote {OUT}")
    print(f"     {len(ordered)} tables, {len(fks)} FKs")


if __name__ == "__main__":
    main()
