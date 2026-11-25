"""
Reusable Alembic helper: rebuild inbound FKs as ``ON UPDATE CASCADE``.

Used by package/app migration chains so seed-id adoption can rewrite primary
keys in place: the database then cascades the change to every child FK,
including tables that are not part of the seed data.

PostgreSQL only. On other dialects the functions are no-ops.
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import text

_FK_QUERY = text(
    """
    SELECT c.conname,
           n.nspname AS child_schema,
           rc.relname AS child_table,
           pn.nspname AS parent_schema,
           rp.relname AS parent_table,
           (
               SELECT array_agg(a.attname ORDER BY k.ord)
               FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
               JOIN pg_attribute a
                 ON a.attrelid = c.conrelid AND a.attnum = k.attnum
           ) AS child_cols,
           (
               SELECT array_agg(a.attname ORDER BY k.ord)
               FROM unnest(c.confkey) WITH ORDINALITY AS k(attnum, ord)
               JOIN pg_attribute a
                 ON a.attrelid = c.confrelid AND a.attnum = k.attnum
           ) AS parent_cols,
           c.confupdtype
    FROM pg_constraint c
    JOIN pg_class rc ON rc.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = rc.relnamespace
    JOIN pg_class rp ON rp.oid = c.confrelid
    JOIN pg_namespace pn ON pn.oid = rp.relnamespace
    WHERE c.contype = 'f'
      AND rp.relname = ANY(:tables)
    """
)


def _quote(bind: Any, identifier: str) -> str:
    return bind.dialect.identifier_preparer.quote(identifier)


def _rebuild(bind: Any, table_names: Iterable[str], on_update: str | None) -> list[str]:
    if bind is None or getattr(bind.dialect, "name", None) != "postgresql":
        return []

    rows = bind.execute(_FK_QUERY, {"tables": list(table_names)}).fetchall()
    rebuilt: list[str] = []

    for (
        conname,
        child_schema,
        child_table,
        parent_schema,
        parent_table,
        child_cols,
        parent_cols,
        _confupdtype,
    ) in rows:
        child = f"{_quote(bind, child_schema)}.{_quote(bind, child_table)}"
        parent = f"{_quote(bind, parent_schema)}.{_quote(bind, parent_table)}"
        cols = ", ".join(_quote(bind, col) for col in child_cols)
        pcols = ", ".join(_quote(bind, col) for col in parent_cols)
        constraint = _quote(bind, conname)

        bind.execute(text(f"ALTER TABLE {child} DROP CONSTRAINT {constraint}"))
        suffix = f" ON UPDATE {on_update}" if on_update else ""
        bind.execute(
            text(
                f"ALTER TABLE {child} ADD CONSTRAINT {constraint} "
                f"FOREIGN KEY ({cols}) REFERENCES {parent} ({pcols}){suffix}"
            )
        )
        rebuilt.append(conname)

    return rebuilt


def upgrade_fks_to_on_update_cascade(bind: Any, table_names: Iterable[str]) -> list[str]:
    """Rebuild every inbound FK of ``table_names`` with ``ON UPDATE CASCADE``."""
    return _rebuild(bind, table_names, "CASCADE")


def downgrade_fks_to_no_action(bind: Any, table_names: Iterable[str]) -> list[str]:
    """Restore inbound FKs of ``table_names`` to the default (``NO ACTION``)."""
    return _rebuild(bind, table_names, None)


__all__ = [
    "upgrade_fks_to_on_update_cascade",
    "downgrade_fks_to_no_action",
]
