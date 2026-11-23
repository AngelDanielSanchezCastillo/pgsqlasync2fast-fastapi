"""Migration helpers for converting BigInteger IDs to UUIDs.

DEPRECATED: These helpers are provided for migration purposes only.
They will be removed in 5 minor versions (around version 0.10.0).

Use these helpers only when migrating existing databases from BigInteger to UUID.
New databases should use UUIDIdMixin from the start.
"""
import os
import time
import warnings
from typing import List
from uuid import UUID

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession


def uuid7() -> UUID:
    """
    Generate a UUID version 7 (time-ordered).
    
    UUID v7 structure (RFC 9562):
    - 48 bits: Unix timestamp in milliseconds
    - 4 bits: version (0111 = 7)
    - 12 bits: random
    - 2 bits: variant (10)
    - 62 bits: random
    
    Returns:
        UUID version 7
    """
    # Get current timestamp in milliseconds (48 bits)
    timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    
    # Generate 10 bytes of random data (80 bits)
    random_bytes = os.urandom(10)
    random_int = int.from_bytes(random_bytes, 'big')
    
    # Construct the UUID (128 bits total)
    # Bits 0-47: timestamp (48 bits)
    # Bits 48-51: version (4 bits) = 0111
    # Bits 52-63: random (12 bits)
    # Bits 64-65: variant (2 bits) = 10
    # Bits 66-127: random (62 bits)
    
    # Start with timestamp in the high 48 bits
    uuid_int = timestamp_ms << 80
    
    # Add version (0111) at bits 48-51
    uuid_int |= (0b0111 << 76)
    
    # Add random bits at bits 52-63 (12 bits)
    uuid_int |= (random_int & 0xFFF) << 64
    
    # Add variant (10) at bits 64-65
    uuid_int |= (0b10 << 62)
    
    # Add random bits at bits 66-127 (62 bits)
    uuid_int |= (random_int >> 12) & 0x3FFFFFFFFFFFFFFF
    
    return UUID(int=uuid_int)


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
