"""
Tests for the upsert_by_id primitive (pgsqlasync2fast-fastapi).

Upsert by ID: update if exists, insert if not.
This allows seeders to evolve over time (e.g., rename roles).

Run with:
  pytest tests/test_upsert_by_id.py -v
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import Field, SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession


DB_URL = "sqlite+aiosqlite:///:memory:"


class ARole(SQLModel, table=True):
    """Minimal SQLModel table with integer ID for upsert testing."""

    __tablename__ = "aroles"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str = Field(default="")


async def _engine():
    engine = create_async_engine(DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return engine


@pytest.mark.asyncio
async def test_upsert_inserts_when_not_exists():
    """A row with a new ID is inserted."""
    from pgsqlasync2fast_fastapi.seeder import upsert_by_id

    engine = await _engine()
    async with AsyncSession(engine) as session:
        row = await upsert_by_id(
            session,
            ARole,
            data={"id": 1, "name": "admin", "description": "Administrator"},
        )
        await session.commit()

        # Re-read to confirm persistence
        from sqlmodel import select

        stored = (await session.exec(select(ARole).where(ARole.id == 1))).one()
        assert stored.name == "admin"
        assert stored.description == "Administrator"
        assert row.id == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_upsert_updates_when_exists():
    """A row with an existing ID is updated with new values."""
    from pgsqlasync2fast_fastapi.seeder import upsert_by_id
    from sqlmodel import select

    engine = await _engine()
    async with AsyncSession(engine) as session:
        # Insert initial row
        await upsert_by_id(
            session,
            ARole,
            data={"id": 1, "name": "Admin", "description": "Old description"},
        )
        await session.commit()

        # Verify initial state
        initial = (await session.exec(select(ARole).where(ARole.id == 1))).one()
        assert initial.name == "Admin"
        assert initial.description == "Old description"

        # Upsert with new values (same ID)
        updated = await upsert_by_id(
            session,
            ARole,
            data={"id": 1, "name": "admin", "description": "New description"},
        )
        await session.commit()

        # Verify updated state
        final = (await session.exec(select(ARole).where(ARole.id == 1))).one()
        assert final.name == "admin"  # Updated
        assert final.description == "New description"  # Updated
        assert final.id == 1  # Same ID
    await engine.dispose()


@pytest.mark.asyncio
async def test_upsert_is_idempotent_for_unchanged_data():
    """Upserting the same data doesn't change the row (no-op)."""
    from pgsqlasync2fast_fastapi.seeder import upsert_by_id
    from sqlmodel import select

    engine = await _engine()
    async with AsyncSession(engine) as session:
        # Insert initial row
        first = await upsert_by_id(
            session,
            ARole,
            data={"id": 1, "name": "admin", "description": "Admin role"},
        )
        await session.commit()

        # Upsert with same data
        second = await upsert_by_id(
            session,
            ARole,
            data={"id": 1, "name": "admin", "description": "Admin role"},
        )
        await session.commit()

        # Only one row should exist
        rows = (await session.exec(select(ARole))).all()
        assert len(rows) == 1
        assert rows[0].name == "admin"
    await engine.dispose()


@pytest.mark.asyncio
async def test_upsert_raises_without_id():
    """Upsert requires 'id' field in data."""
    from pgsqlasync2fast_fastapi.seeder import upsert_by_id

    engine = await _engine()
    async with AsyncSession(engine) as session:
        with pytest.raises(ValueError, match="requires 'id' field"):
            await upsert_by_id(
                session,
                ARole,
                data={"name": "admin", "description": "No ID"},
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_upsert_multiple_rows():
    """Upsert works correctly with multiple rows in sequence."""
    from pgsqlasync2fast_fastapi.seeder import upsert_by_id
    from sqlmodel import select

    engine = await _engine()
    async with AsyncSession(engine) as session:
        # Insert 3 roles
        await upsert_by_id(
            session, ARole, data={"id": 1, "name": "admin", "description": "Admin"}
        )
        await upsert_by_id(
            session, ARole, data={"id": 2, "name": "user", "description": "User"}
        )
        await upsert_by_id(
            session, ARole, data={"id": 3, "name": "manager", "description": "Manager"}
        )
        await session.commit()

        # Verify all 3 exist
        rows = (await session.exec(select(ARole))).all()
        assert len(rows) == 3

        # Update role 2
        await upsert_by_id(
            session, ARole, data={"id": 2, "name": "member", "description": "Member"}
        )
        await session.commit()

        # Verify role 2 was updated
        role2 = (await session.exec(select(ARole).where(ARole.id == 2))).one()
        assert role2.name == "member"

        # Verify other roles unchanged
        role1 = (await session.exec(select(ARole).where(ARole.id == 1))).one()
        assert role1.name == "admin"
        role3 = (await session.exec(select(ARole).where(ARole.id == 3))).one()
        assert role3.name == "manager"
    await engine.dispose()
