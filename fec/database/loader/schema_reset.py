"""Drop every object in the public schema so the loader can rebuild from scratch."""
from __future__ import annotations

from typing import Any

from fec.log import get_logger

from ._base import PG

logger = get_logger(__name__)


def reset_schema(conn: Any, cur: Any) -> None:
    """Drop every table/view/matview actually in public (SAVEPOINT per drop, ALTER OWNER first when not ours), then verify nothing remains."""
    logger.info("\n  Dropping all objects in public schema...")

    cur.execute("SELECT current_user")
    current_user = cur.fetchone()[0]

    # Discover what's actually there -- never trust a static list.
    cur.execute("SELECT matviewname, matviewowner FROM pg_matviews WHERE schemaname = 'public'")
    matviews = cur.fetchall()
    cur.execute("SELECT viewname,     viewowner    FROM pg_views    WHERE schemaname = 'public'")
    views = cur.fetchall()
    cur.execute("SELECT tablename,    tableowner   FROM pg_tables   WHERE schemaname = 'public'")
    tables = cur.fetchall()
    # CASCADE handles FKs; matviews -> views -> tables keeps error messages predictable.
    objects = (
        [(name, 'MATERIALIZED VIEW', owner) for name, owner in matviews]
        + [(name, 'VIEW', owner)            for name, owner in views]
        + [(name, 'TABLE', owner)           for name, owner in tables]
    )

    if not objects:
        logger.info("  Already empty (nothing to drop)")
        return

    dropped = 0
    failed: list[tuple[str, str, str, str]] = []  # (name, kind, owner, reason)

    for idx, (name, kind, owner) in enumerate(objects):
        # An earlier CASCADE may have taken this object already; gone counts as
        # dropped (ALTER OWNER, unlike DROP IF EXISTS, would fail on it).
        cur.execute("SELECT to_regclass(%s)", (f'public."{name}"',))
        if cur.fetchone()[0] is None:
            dropped += 1
            continue

        # DROP fails on objects we don't own -- try ALTER OWNER first.
        if owner != current_user:
            try:
                cur.execute(f"SAVEPOINT sp_alt_{idx}")
                cur.execute(f'ALTER {kind} "{name}" OWNER TO {current_user}')
                cur.execute(f"RELEASE SAVEPOINT sp_alt_{idx}")
            except Exception as error:
                cur.execute(f"ROLLBACK TO SAVEPOINT sp_alt_{idx}")
                failed.append((name, kind, owner, f'ALTER OWNER: {str(error).splitlines()[0][:80]}'))
                continue

        try:
            cur.execute(f"SAVEPOINT sp_drop_{idx}")
            cur.execute(f'DROP {kind} IF EXISTS "{name}" CASCADE')
            cur.execute(f"RELEASE SAVEPOINT sp_drop_{idx}")
            dropped += 1
        except Exception as error:
            cur.execute(f"ROLLBACK TO SAVEPOINT sp_drop_{idx}")
            failed.append((name, kind, owner, f'DROP: {str(error).splitlines()[0][:80]}'))

    conn.commit()
    logger.info(f"  Dropped {dropped}/{len(objects)} objects")

    if failed:
        logger.error(f"  {len(failed)} object(s) could NOT be dropped:")
        for name, kind, owner, reason in failed[:10]:
            logger.error(f"    - {kind} {name} (owner={owner}) -- {reason}")
        if len(failed) > 10:
            logger.error(f"    ... and {len(failed) - 10} more")
        raise RuntimeError(
            f"reset_schema: {len(failed)} object(s) could not be dropped "
            f"(likely owned by another role). Fix options:\n"
            f"  1. Run as superuser: sudo -u postgres psql -d {PG['dbname']} "
            f"-c \"REASSIGN OWNED BY <old_owner> TO {current_user}\"\n"
            f"  2. Manually DROP the listed objects as their owner"
        )

    # Post-drop verification: nothing should remain in public schema.
    cur.execute("""
        SELECT 'TABLE' AS kind, tablename AS name FROM pg_tables   WHERE schemaname='public'
        UNION ALL
        SELECT 'VIEW',          viewname              FROM pg_views    WHERE schemaname='public'
        UNION ALL
        SELECT 'MATVIEW',       matviewname           FROM pg_matviews WHERE schemaname='public'
    """)
    leftover = cur.fetchall()
    if leftover:
        raise RuntimeError(
            f"reset_schema: drops reported success but {len(leftover)} object(s) remain: "
            f"{[f'{kind} {name}' for kind, name in leftover[:5]]}. "
            f"Check for objects created outside the reset path."
        )
