"""
Tests for the reusable idempotent `insert_if_missing` primitive
(pgsqlasync2fast-fastapi).

This is the shared insert-if-missing-by-natural-key helper that consumer
packages (permissions2fast GLOBAL routes, tenants2fast TENANT routes) reuse
instead of hand-rolling per package (RBAC standardization decision D2).

Run with:
  cd /Volumes/Desarrollo/Repos/Github/pgsqlasync2fast-fastapi \
    && uv run pytest tests/test_insert_if_missing.py -v
"""

from __future__ import annotations

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


class AFruit(SQLModel, table=True):
    """Minimal SQLModel table to exercise insert_if_missing."""

    __tablename__ = "afruits"

    name: str = Field(primary_key=True)
    color: str = Field(default="")


async def _engine():
    engine = create_async_engine(DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return engine


@pytest.mark.asyncio
async def test_insert_if_missing_inserts_new_row():
    """A row that matches no natural key is inserted."""
    from pgsqlasync2fast_fastapi.seeder import insert_if_missing

    engine = await _engine()
    async with AsyncSession(engine) as session:
        row = await insert_if_missing(
            session, AFruit, lookup={"name": "apple"}, defaults={"color": "red"}
        )
        await session.commit()

        # Re-read via a fresh query to confirm persistence
        from sqlmodel import select

        stored = (await session.exec(select(AFruit).where(AFruit.name == "apple"))).one()
        assert stored.color == "red"
        assert row.name == "apple"
    await engine.dispose()


@pytest.mark.asyncio
async def test_insert_if_missing_is_idempotent():
    """Calling twice with the same natural key returns the existing row."""
    from pgsqlasync2fast_fastapi.seeder import insert_if_missing

    engine = await _engine()
    async with AsyncSession(engine) as session:
        first = await insert_if_missing(
            session, AFruit, lookup={"name": "pear"}, defaults={"color": "green"}
        )
        await session.commit()

        second = await insert_if_missing(
            session, AFruit, lookup={"name": "pear"}, defaults={"color": "green"}
        )
        await session.commit()

        assert second is first  # same object instance returned (cached in session identity map)
        # Only one row exists
        from sqlmodel import select

        rows = (await session.exec(select(AFruit))).all()
        assert len(rows) == 1
    await engine.dispose()
