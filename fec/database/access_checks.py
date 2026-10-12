"""Checks database roles, ownership and read access."""
from __future__ import annotations

from fec.database.check_kinds import Check, no_names_check, clean_sql, zero_check
from fec.env import DATABASE_OWNER, DATABASE_READER

_OWNER, _READER = DATABASE_OWNER, DATABASE_READER
# tables, views and materialized views in public
_RELATIONS = (
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
    " WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm')"
)
ACCESS_CHECKS: list[Check] = [
    no_names_check(
        "access: public is the only schema",
        clean_sql("""
            SELECT nspname FROM pg_namespace
            WHERE nspname NOT IN ('public', 'information_schema')
              AND nspname NOT LIKE 'pg\\_%'
            ORDER BY nspname
        """),
        "other schema(s)",
    ),
    no_names_check(
        f"access: {_OWNER} and {_READER} are the only logins to this database",
        clean_sql(f"""
            SELECT rolname FROM pg_roles
            WHERE rolcanlogin AND NOT rolsuper
              AND rolname NOT IN ('{_OWNER}', '{_READER}')
              AND has_database_privilege(oid, current_database(), 'CONNECT')
            ORDER BY rolname
        """),
        "other login role(s)",
    ),
    zero_check(
        f"access: {_OWNER} owns every table and view",
        clean_sql(f"SELECT COUNT(*) {_RELATIONS} AND pg_get_userbyid(c.relowner) <> '{_OWNER}'"),
        "object(s) owned by another role",
    ),
    zero_check(
        f"access: {_READER} reads every table, view and materialized view",
        clean_sql(f"SELECT COUNT(*) {_RELATIONS} AND NOT has_table_privilege('{_READER}', c.oid, 'SELECT')"),
        "object(s) it cannot read",
    ),
    zero_check(
        f"access: {_READER} cannot write",
        clean_sql(f"""
            SELECT COUNT(*) {_RELATIONS}
              AND has_table_privilege(
                  '{_READER}', c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER'
              )
        """),
        "object(s) it can change",
    ),
]
