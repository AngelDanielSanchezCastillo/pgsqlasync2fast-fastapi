"""Migration helpers for converting BigInteger IDs to UUIDs.

DEPRECATED: These helpers are provided for migration purposes only.
They will be removed in 5 minor versions (around version 0.10.0).

Use these helpers only when migrating existing databases from BigInteger to UUID.
New databases should use UUIDIdMixin from the start.
"""
import warnings
from typing import List
from uuid import UUID, uuid7

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession


async def migrate_pk_to_uuid(
    session: AsyncSession,
    table_name: str,
) -> None:
    """
    Migrate a table's primary key from BigInteger to UUID.
    
    This function:
    1. Adds a new UUID column (id_uuid)
    2. Backfills with UUID v7 values for existing rows
    3. Updates foreign key references in dependent tables
    4. Swaps the old PK column with the new UUID column
    5. Recreates constraints and indexes
    
    DEPRECATED: This helper will be removed in 5 minor versions.
    
    Args:
        session: Async database session
        table_name: Name of the table to migrate
    
    Warning:
        This is a destructive operation. Backup your database before running.
        Foreign key relationships must be handled separately using migrate_fk_to_uuid().
    """
    warnings.warn(
        "migrate_pk_to_uuid is deprecated and will be removed in 5 minor versions "
        "(around version 0.10.0). Use it only for v0.x → v1.0 migrations. "
        "New databases should use UUIDIdMixin from the start.",
        DeprecationWarning,
        stacklevel=2
    )
    
    # Step 1: Add UUID column
    await session.execute(text(f"""
        ALTER TABLE {table_name}
        ADD COLUMN IF NOT EXISTS id_uuid UUID
    """))
    
    # Step 2: Backfill UUIDs for existing rows
    result = await session.execute(text(f"""
        SELECT id FROM {table_name} WHERE id_uuid IS NULL
    """))
    rows = result.fetchall()
    
    for row in rows:
        old_id = row[0]
        new_uuid = uuid7()
        await session.execute(
            text(f"UPDATE {table_name} SET id_uuid = :uuid WHERE id = :old_id"),
            {"uuid": str(new_uuid), "old_id": old_id}
        )
    
    # Step 3: Make UUID column NOT NULL
    await session.execute(text(f"""
        ALTER TABLE {table_name}
        ALTER COLUMN id_uuid SET NOT NULL
    """))
    
    # Step 4: Drop old primary key and rename UUID column
    # Note: This requires dropping and recreating constraints
    # For simplicity, we'll just rename the column
    await session.execute(text(f"""
        ALTER TABLE {table_name}
        DROP CONSTRAINT IF EXISTS {table_name}_pkey
    """))
    
    await session.execute(text(f"""
        ALTER TABLE {table_name}
        DROP COLUMN IF EXISTS id
    """))
    
    await session.execute(text(f"""
        ALTER TABLE {table_name}
        RENAME COLUMN id_uuid TO id
    """))
    
    await session.execute(text(f"""
        ALTER TABLE {table_name}
        ADD PRIMARY KEY (id)
    """))
    
    await session.commit()


async def migrate_fk_to_uuid(
    session: AsyncSession,
    table_name: str,
    fk_column: str,
    referenced_table: str,
) -> None:
    """
    Migrate a foreign key column from BigInteger to UUID.
    
    This function:
    1. Adds a new UUID column for the FK
    2. Backfills with UUIDs from the referenced table
    3. Drops the old FK column
    4. Renames the new column
    5. Recreates the foreign key constraint
    
    DEPRECATED: This helper will be removed in 5 minor versions.
    
    Args:
        session: Async database session
        table_name: Name of the table containing the FK
        fk_column: Name of the FK column to migrate
        referenced_table: Name of the table being referenced
    
    Warning:
        This is a destructive operation. Backup your database before running.
    """
    warnings.warn(
        "migrate_fk_to_uuid is deprecated and will be removed in 5 minor versions "
        "(around version 0.10.0). Use it only for v0.x → v1.0 migrations.",
        DeprecationWarning,
        stacklevel=2
    )
    
    new_column = f"{fk_column}_uuid"
    
    # Step 1: Add UUID column
    await session.execute(text(f"""
        ALTER TABLE {table_name}
        ADD COLUMN IF NOT EXISTS {new_column} UUID
    """))
    
    # Step 2: Backfill UUIDs from referenced table
    await session.execute(text(f"""
        UPDATE {table_name}
        SET {new_column} = (
            SELECT id FROM {referenced_table}
            WHERE {referenced_table}.id = {table_name}.{fk_column}
        )
        WHERE {new_column} IS NULL
    """))
    
    # Step 3: Drop old FK constraint and column
    constraint_name = f"{table_name}_{fk_column}_fkey"
    await session.execute(text(f"""
        ALTER TABLE {table_name}
        DROP CONSTRAINT IF EXISTS {constraint_name}
    """))
    
    await session.execute(text(f"""
        ALTER TABLE {table_name}
        DROP COLUMN IF EXISTS {fk_column}
    """))
    
    # Step 4: Rename new column
    await session.execute(text(f"""
        ALTER TABLE {table_name}
        RENAME COLUMN {new_column} TO {fk_column}
    """))
    
    # Step 5: Recreate FK constraint
    await session.execute(text(f"""
        ALTER TABLE {table_name}
        ADD CONSTRAINT {constraint_name}
        FOREIGN KEY ({fk_column}) REFERENCES {referenced_table}(id)
    """))
    
    await session.commit()


async def backfill_uuids(
    session: AsyncSession,
    table_name: str,
    column_name: str = "id",
) -> int:
    """
    Backfill UUID values for rows that don't have them.
    
    DEPRECATED: This helper will be removed in 5 minor versions.
    
    Args:
        session: Async database session
        table_name: Name of the table
        column_name: Name of the UUID column (default: "id")
    
    Returns:
        Number of rows updated
    
    Warning:
        This helper is deprecated and will be removed in 5 minor versions.
    """
    warnings.warn(
        "backfill_uuids is deprecated and will be removed in 5 minor versions "
        "(around version 0.10.0). Use it only for v0.x → v1.0 migrations.",
        DeprecationWarning,
        stacklevel=2
    )
    
    # Find rows without UUIDs
    result = await session.execute(text(f"""
        SELECT id FROM {table_name}
        WHERE {column_name} IS NULL OR {column_name} = '00000000-0000-0000-0000-000000000000'
    """))
    rows = result.fetchall()
    
    count = 0
    for row in rows:
        old_id = row[0]
        new_uuid = uuid7()
        await session.execute(
            text(f"UPDATE {table_name} SET {column_name} = :uuid WHERE id = :old_id"),
            {"uuid": str(new_uuid), "old_id": old_id}
        )
        count += 1
    
    await session.commit()
    return count


# ============================================================================
# Alembic-specific helpers (synchronous, using op object)
# ============================================================================


def add_soft_delete_columns_alembic(op_obj, table_name: str) -> None:
    """
    Add soft-delete columns (deleted_at, deleted_by) to a table using Alembic.
    
    Args:
        op_obj: Alembic operations object
        table_name: Name of the table to modify
    """
    import sqlalchemy as sa
    
    op_obj.add_column(
        table_name,
        sa.Column(
            'deleted_at',
            sa.DateTime(timezone=True),
            nullable=True,
            comment='Timestamp when the record was soft-deleted. None means active.',
        ),
    )
    op_obj.add_column(
        table_name,
        sa.Column(
            'deleted_by',
            sa.UUID(),
            nullable=True,
            comment='ID of the user who soft-deleted this record.',
        ),
    )
    # Add index on deleted_at for efficient filtering
    op_obj.create_index(
        f'ix_{table_name}_deleted_at',
        table_name,
        ['deleted_at'],
        unique=False,
    )


def prepare_pk_uuid_alembic(op_obj, table_name: str) -> None:
    """
    Phase 1 of a two-phase BigInteger → UUID PK migration (Alembic).

    Adds an ``id_uuid`` column and backfills it with ``gen_random_uuid()`` for
    existing rows, keeping the old integer ``id`` column and primary key in
    place. The swap (drop old ``id`` + rename) happens later in
    :func:`finalize_uuid_alembic`.

    Keeping the old ``id`` during the prepare phase is what makes FK backfills
    correct: children can resolve ``parent.id_uuid`` while ``parent.id`` (int)
    still exists.

    Args:
        op_obj: Alembic operations object.
        table_name: Table whose PK is being prepared.
    """
    import sqlalchemy as sa

    op_obj.add_column(table_name, sa.Column("id_uuid", sa.Uuid(), nullable=True))
    op_obj.execute(
        f"UPDATE {table_name} SET id_uuid = gen_random_uuid() WHERE id_uuid IS NULL"
    )
    op_obj.alter_column(
        table_name,
        "id_uuid",
        nullable=False,
        existing_type=sa.Uuid(),
        existing_nullable=True,
    )


def prepare_fk_uuid_alembic(
    op_obj,
    table_name: str,
    fk_column: str,
    referenced_table: str,
    *,
    nullable: bool = True,
) -> None:
    """
    Phase 1 of a two-phase BigInteger → UUID FK migration (Alembic).

    Adds a ``{fk_column}_uuid`` column and backfills it from the parent table's
    already-prepared ``id_uuid`` (``prepare_pk_uuid_alembic`` must run on
    ``referenced_table`` first). The join uses the *old integer* columns, so it
    works while both old PK/FK columns still exist:

        UPDATE child SET fk_uuid = p.id_uuid
        FROM parent p WHERE p.id = child.fk_column

    The old FK column and constraint are dropped later in
    :func:`finalize_uuid_alembic`.

    Args:
        op_obj: Alembic operations object.
        table_name: Child table containing the FK.
        fk_column: FK column being migrated.
        referenced_table: Parent table (its ``id_uuid`` must already be filled).
        nullable: Whether the FK is nullable. Only forces NOT NULL when False.
    """
    import sqlalchemy as sa

    new_column = f"{fk_column}_uuid"
    op_obj.add_column(table_name, sa.Column(new_column, sa.Uuid(), nullable=True))
    op_obj.execute(
        f"""
        UPDATE {table_name} AS child
        SET {new_column} = parent.id_uuid
        FROM {referenced_table} AS parent
        WHERE parent.id = child.{fk_column}
        """
    )
    if not nullable:
        op_obj.alter_column(
            table_name,
            new_column,
            nullable=False,
            existing_type=sa.Uuid(),
            existing_nullable=True,
        )


def finalize_uuid_alembic(
    op_obj,
    *,
    pk_tables,
    fk_relations,
) -> None:
    """
    Phase 2 of the BigInteger → UUID migration (Alembic).

    Performs the destructive swap in the only order PostgreSQL accepts:

    1. Drop every old child FK constraint (must happen before parent PKs are
       dropped, otherwise Postgres refuses because dependents still reference
       the PK).
    2. For each PK table: drop the old PK constraint, drop the old integer
       ``id`` column (its indexes cascade-drop), rename ``id_uuid`` → ``id``,
       recreate the PK and the ``ix_<table>_id`` index.
    3. For each FK relation: drop the old integer FK column (its index
       cascade-drops), rename ``<fk>_uuid`` → ``<fk>``, recreate the FK
       constraint and the ``ix_<table>_<fk>`` index.

    Args:
        op_obj: Alembic operations object.
        pk_tables: Iterable of table names whose PK was prepared.
        fk_relations: Iterable of ``(child_table, fk_column, parent_table)``
            tuples whose FK was prepared.
    """
    fk_relations = list(fk_relations)
    pk_tables = list(pk_tables)

    # Step 1: drop old child FK constraints before touching parent PKs.
    for child, fk_column, _parent in fk_relations:
        op_obj.drop_constraint(
            f"{child}_{fk_column}_fkey", child, type_="foreignkey"
        )

    # Step 2: swap PKs.
    for table in pk_tables:
        op_obj.drop_constraint(f"{table}_pkey", table)
        op_obj.drop_column(table, "id")
        op_obj.alter_column(table, "id_uuid", new_column_name="id")
        op_obj.create_primary_key(f"{table}_pkey", table, ["id"])
        op_obj.create_index(
            op_obj.f(f"ix_{table}_id"), table, ["id"], unique=False
        )

    # Step 3: swap FKs and recreate their constraints/indexes.
    for child, fk_column, parent in fk_relations:
        new_column = f"{fk_column}_uuid"
        op_obj.drop_column(child, fk_column)
        op_obj.alter_column(child, new_column, new_column_name=fk_column)
        op_obj.create_foreign_key(
            f"{child}_{fk_column}_fkey",
            child,
            parent,
            [fk_column],
            ["id"],
        )
        op_obj.create_index(
            op_obj.f(f"ix_{child}_{fk_column}"),
            child,
            [fk_column],
            unique=False,
        )


