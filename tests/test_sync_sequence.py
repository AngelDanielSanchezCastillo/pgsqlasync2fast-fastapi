"""
Tests for the shared PostgreSQL sequence re-sync primitive
`sync_table_sequence` (pgsqlasync2fast-fastapi).

The generic and by-id seeders insert rows with explicit ``id`` values from the
manifest JSON. PostgreSQL's plain ``serial``/``sequence``-backed columns do not
advance the sequence when a row is inserted with an explicit id, so a later
sequence-backed insert (e.g. ``insert_if_missing``) can generate an id that
collides with an existing explicit-id row (UniqueViolation).

This module verifies:
1. ``sync_table_sequence`` is a no-op on non-PostgreSQL dialects (SQLite).
2. On PostgreSQL it issues ``setval`` to the table's ``MAX(id)``.
3. Both seeding paths (``_seed_table_idempotent`` and
   ``_seed_table_idempotent_generic``) call the primitive.

Run with:
  cd /Volumes/Desarrollo/Repos/Github/pgsqlasync2fast-fastapi \
    && uv run pytest tests/test_sync_sequence.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import BigInteger
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlmodel import Field, SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession


@compiles(BigInteger, "sqlite")
def _compile_bigint_sqlite(type_, compiler, **kw):
    return "INTEGER"


DB_URL = "sqlite+aiosqlite:///:memory:"


class SeqFruit(SQLModel, table=True):
    """Minimal SQLModel table to exercise the sequence helper."""

    __tablename__ = "seq_fruits"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(default="")


async def _engine():
    engine = create_async_engine(DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return engine


# ============================================================================
# Unit: no-op on SQLite / selects dialect before setval
# ============================================================================


@pytest.mark.asyncio
async def test_sync_sequence_noop_on_sqlite():
    """On SQLite the helper returns without issuing any setval SQL."""
    from pgsqlasync2fast_fastapi.seeder import sync_table_sequence

    engine = await _engine()
    async with AsyncSession(engine) as session:
        # Should not raise, and must not attempt PostgreSQL sequence SQL.
        await sync_table_sequence(session, SeqFruit)
    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_sequence_skips_non_postgresql():
    """A non-PostgreSQL bind is detected and skipped before any query."""
    from pgsqlasync2fast_fastapi.seeder import sync_table_sequence

    # Bind that reports a non-PostgreSQL dialect with a tracked .begin()
    bind = MagicMock()
    bind.dialect.name = "sqlite"

    session = AsyncMock()
    session.bind = bind

    await sync_table_sequence(session, SeqFruit)

    bind.begin.assert_not_called()


# ============================================================================
# Unit: PostgreSQL path issues setval to MAX(id)
# ============================================================================


@pytest.mark.asyncio
async def test_sync_sequence_issues_setval_to_max_id():
    """On PostgreSQL the helper selects MAX(id) and runs setval."""
    from pgsqlasync2fast_fastapi.seeder import sync_table_sequence

    conn = AsyncMock()
    conn.scalar = AsyncMock(return_value=11)
    conn.execute = AsyncMock()

    bind = MagicMock()
    bind.dialect.name = "postgresql"
    bind.begin.return_value.__aenter__.return_value = conn

    session = AsyncMock()
    session.bind = bind

    await sync_table_sequence(session, SeqFruit)

    # MAX(id) was read from the table
    conn.scalar.assert_awaited_once()
    assert '"seq_fruits"' in str(conn.scalar.await_args.args[0])
    # setval ran with the sequence name and MAX(id)
    conn.execute.assert_awaited_once()
    args, kwargs = conn.execute.await_args
    assert "setval" in str(args[0])
    # parameters passed as second positional arg: {"seq": ..., "val": 11}
    params = args[1] if len(args) > 1 else kwargs
    assert params["val"] == 11
    assert params["seq"] == "seq_fruits_id_seq"


@pytest.mark.asyncio
async def test_sync_sequence_skips_setval_when_table_empty():
    """MAX(id) of 0 means no setval is issued (empty table)."""
    from pgsqlasync2fast_fastapi.seeder import sync_table_sequence

    conn = AsyncMock()
    conn.scalar = AsyncMock(return_value=0)
    conn.execute = AsyncMock()

    bind = MagicMock()
    bind.dialect.name = "postgresql"
    bind.begin.return_value.__aenter__.return_value = conn

    session = AsyncMock()
    session.bind = bind

    await sync_table_sequence(session, SeqFruit)

    conn.execute.assert_not_called()


# ============================================================================
# Unit: both seeding paths invoke the primitive
# ============================================================================


@pytest.mark.asyncio
async def test_idempotent_seeder_calls_sync_sequence():
    """_seed_table_idempotent invokes sync_table_sequence at the end."""
    from pgsqlasync2fast_fastapi.seeder import _seed_table_idempotent

    session = AsyncMock()
    exec_result = MagicMock()
    exec_result.one_or_none = MagicMock(return_value=None)
    session.exec = AsyncMock(return_value=exec_result)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    with patch(
        "pgsqlasync2fast_fastapi.seeder.sync_table_sequence",
        new=AsyncMock(),
    ) as mock_sync:
        inserted, skipped = await _seed_table_idempotent(
            session, "seq_fruits", [{"id": 1, "name": "apple"}], SeqFruit
        )

    assert inserted == 1
    mock_sync.assert_awaited_once_with(session, SeqFruit)


@pytest.mark.asyncio
async def test_generic_seeder_calls_sync_sequence():
    """_seed_table_idempotent_generic invokes sync_table_sequence at the end."""
    from pgsqlasync2fast_fastapi.seeder import _seed_table_idempotent_generic

    session = AsyncMock()
    exec_result = MagicMock()
    exec_result.one_or_none = MagicMock(return_value=None)
    session.exec = AsyncMock(return_value=exec_result)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    with patch(
        "pgsqlasync2fast_fastapi.seeder.sync_table_sequence",
        new=AsyncMock(),
    ) as mock_sync:
        inserted, skipped = await _seed_table_idempotent_generic(
            session, "seq_fruits", [{"id": 1, "name": "apple"}], SeqFruit
        )

    assert inserted == 1
    mock_sync.assert_awaited_once_with(session, SeqFruit)