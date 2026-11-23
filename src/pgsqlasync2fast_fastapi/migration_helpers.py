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


def migrate_pk_to_uuid_alembic(op_obj, table_name: str) -> None:
    """
    Migrate a table's primary key from BigInteger to UUID using Alembic.
    
    This function:
    1. Adds a new UUID column (id_uuid)
    2. Backfills with UUID v4 values for existing rows (using gen_random_uuid())
    3. Swaps the old PK column with the new UUID column
    4. Recreates constraints and indexes
    
    Args:
        op_obj: Alembic operations object
        table_name: Name of the table to migrate
    
    Warning:
        This is a destructive operation. Backup your database before running.
        Foreign key relationships must be handled separately using migrate_fk_to_uuid_alembic().
    """
    import sqlalchemy as sa
    
    # Step 1: Add UUID column
    op_obj.add_column(table_name, sa.Column('id_uuid', sa.Uuid(), nullable=True))
    
    # Step 2: Backfill UUIDs for existing rows using gen_random_uuid()
    op_obj.execute(f"UPDATE {table_name} SET id_uuid = gen_random_uuid() WHERE id_uuid IS NULL")
    
    # Step 3: Make UUID column NOT NULL
    op_obj.alter_column(table_name, 'id_uuid', nullable=False,
                        existing_type=sa.Uuid(), existing_nullable=True)
    
    # Step 4: Drop old primary key constraint
    op_obj.drop_constraint(f'{table_name}_pkey', table_name)
    
    # Step 5: Drop old id column
    op_obj.drop_column(table_name, 'id')
    
    # Step 6: Rename UUID column to id
    op_obj.alter_column(table_name, 'id_uuid', new_column_name='id')
    
    # Step 7: Recreate primary key
    op_obj.create_primary_key(f'{table_name}_pkey', table_name, ['id'])
    
    # Step 8: Recreate index
    op_obj.create_index(op_obj.f(f'ix_{table_name}_id'), table_name, ['id'], unique=False)


def migrate_fk_to_uuid_alembic(
    op_obj,
    table_name: str,
    fk_column: str,
    referenced_table: str,
) -> None:
    """
    Migrate a foreign key column from BigInteger to UUID using Alembic.
    
    This function:
    1. Adds a new UUID column for the FK
    2. Backfills with UUIDs from the referenced table
    3. Drops the old FK column
    4. Renames the new column
    5. Recreates the foreign key constraint
    
    Args:
        op_obj: Alembic operations object
        table_name: Name of the table containing the FK
        fk_column: Name of the FK column to migrate
        referenced_table: Name of the table being referenced
    
    Warning:
        This is a destructive operation. Backup your database before running.
    """
    import sqlalchemy as sa
    
    new_column = f"{fk_column}_uuid"
    constraint_name = f"{table_name}_{fk_column}_fkey"
    
    # Step 1: Add UUID column
    op_obj.add_column(table_name, sa.Column(new_column, sa.Uuid(), nullable=True))
    
    # Step 2: Backfill UUIDs from referenced table
    op_obj.execute(f"""
        UPDATE {table_name}
        SET {new_column} = (
            SELECT id FROM {referenced_table}
            WHERE {referenced_table}.id = {table_name}.{fk_column}
        )
        WHERE {new_column} IS NULL
    """)
    
    # Step 3: Make UUID column NOT NULL
    op_obj.alter_column(table_name, new_column, nullable=False,
                        existing_type=sa.Uuid(), existing_nullable=True)
    
    # Step 4: Drop old FK constraint
    op_obj.drop_constraint(constraint_name, table_name, type_='foreignkey')
    
    # Step 5: Drop old FK column
    op_obj.drop_column(table_name, fk_column)
    
    # Step 6: Rename new column
    op_obj.alter_column(table_name, new_column, new_column_name=fk_column)
    
    # Step 7: Recreate FK constraint
    op_obj.create_foreign_key(
        constraint_name,
        table_name,
        referenced_table,
        [fk_column],
        ['id'],
    )
    
    # Step 8: Recreate index
    op_obj.create_index(op_obj.f(f'ix_{table_name}_{fk_column}'), table_name, [fk_column], unique=False)

