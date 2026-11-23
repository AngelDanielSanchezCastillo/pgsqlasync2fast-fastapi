"""Tests for the Alembic two-phase UUID migration helpers."""
from __future__ import annotations


class _Recorder:
    """Records Alembic-like operations so order/args can be asserted."""

    def __init__(self):
        self.calls = []

    def f(self, name):
        return name

    def add_column(self, table, column):
        self.calls.append(("add_column", table, column.name))

    def execute(self, sql):
        self.calls.append(("execute", " ".join(sql.split())))

    def alter_column(self, table, column, **kwargs):
        self.calls.append(("alter_column", table, column, kwargs))

    def drop_constraint(self, name, table, **kwargs):
        self.calls.append(("drop_constraint", table, name, kwargs.get("type_")))

    def drop_column(self, table, column):
        self.calls.append(("drop_column", table, column))

    def create_primary_key(self, name, table, columns):
        self.calls.append(("create_primary_key", table, name, tuple(columns)))

    def create_foreign_key(self, name, source, referent, local, remote):
        self.calls.append(
            ("create_foreign_key", source, name, referent, tuple(local), tuple(remote))
        )

    def create_index(self, name, table, columns, unique=False):
        self.calls.append(("create_index", table, name, tuple(columns), unique))

    def names(self):
        return [c[0] for c in self.calls]


def test_helpers_importable():
    from pgsqlasync2fast_fastapi.migration_helpers import (
        add_soft_delete_columns_alembic,
        prepare_pk_uuid_alembic,
        prepare_fk_uuid_alembic,
        finalize_uuid_alembic,
    )

    assert callable(add_soft_delete_columns_alembic)
    assert callable(prepare_pk_uuid_alembic)
    assert callable(prepare_fk_uuid_alembic)
    assert callable(finalize_uuid_alembic)


def test_prepare_pk_keeps_old_id_and_backfills():
    from pgsqlasync2fast_fastapi.migration_helpers import prepare_pk_uuid_alembic

    rec = _Recorder()
    prepare_pk_uuid_alembic(rec, "tenants")

    # Adds id_uuid, backfills, forces NOT NULL — never drops old id.
    assert ("add_column", "tenants", "id_uuid") in rec.calls
    assert any(
        c[0] == "execute" and "gen_random_uuid()" in c[1] for c in rec.calls
    )
    assert "drop_column" not in rec.names()


def test_prepare_fk_backfills_from_parent_id_uuid_via_old_int():
    from pgsqlasync2fast_fastapi.migration_helpers import prepare_fk_uuid_alembic

    rec = _Recorder()
    prepare_fk_uuid_alembic(rec, "tenant_users", "tenant_id", "tenants", nullable=False)

    assert ("add_column", "tenant_users", "tenant_id_uuid") in rec.calls
    update_sql = next(c[1] for c in rec.calls if c[0] == "execute")
    # Backfill joins parent.id_uuid ON parent.id = child.fk (old int columns).
    assert "parent.id_uuid" in update_sql
    assert "parent.id = child.tenant_id" in update_sql
    # nullable=False forces NOT NULL.
    assert any(
        c[0] == "alter_column" and c[2] == "tenant_id_uuid" and c[3]["nullable"] is False
        for c in rec.calls
    )
    assert "drop_column" not in rec.names()


def test_finalize_drops_fk_constraints_before_parent_pks():
    from pgsqlasync2fast_fastapi.migration_helpers import finalize_uuid_alembic

    rec = _Recorder()
    finalize_uuid_alembic(
        rec,
        pk_tables=["tenants", "tenant_users"],
        fk_relations=[("tenant_users", "tenant_id", "tenants")],
    )

    names = rec.names()
    # The child FK constraint must be dropped before any PK constraint/NOT NULL swap.
    fk_drop_idx = next(
        i
        for i, c in enumerate(rec.calls)
        if c[0] == "drop_constraint" and c[1] == "tenant_users" and c[3] == "foreignkey"
    )
    first_pk_drop_idx = next(
        i
        for i, c in enumerate(rec.calls)
        if c[0] == "drop_constraint" and c[3] is None
    )
    assert fk_drop_idx < first_pk_drop_idx

    # PKs are swapped (old id dropped, id_uuid renamed, PK rebuilt).
    assert ("drop_column", "tenants", "id") in rec.calls
    assert ("drop_column", "tenant_users", "id") in rec.calls
    assert ("create_primary_key", "tenants", "tenants_pkey", ("id",)) in rec.calls
    # FKs are recreated after PKs.
    assert (
        "create_foreign_key",
        "tenant_users",
        "tenant_users_tenant_id_fkey",
        "tenants",
        ("tenant_id",),
        ("id",),
    ) in rec.calls


def test_finalize_recreates_indexes():
    from pgsqlasync2fast_fastapi.migration_helpers import finalize_uuid_alembic

    rec = _Recorder()
    finalize_uuid_alembic(
        rec,
        pk_tables=["roles"],
        fk_relations=[("role_users", "role_id", "roles")],
    )

    assert ("create_index", "roles", "ix_roles_id", ("id",), False) in rec.calls
    assert (
        "create_index",
        "role_users",
        "ix_role_users_role_id",
        ("role_id",),
        False,
    ) in rec.calls
