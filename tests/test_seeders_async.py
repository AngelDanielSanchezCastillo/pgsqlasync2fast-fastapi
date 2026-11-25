"""
Tests for pgsqlasync2fast_fastapi.seeders async primitives (TDD).

Covers:
- upsert_with_optimization(): only UPDATE if fields changed
- SeederEngine: main orchestrator with manifest loading
- Rollback: granular rollback (all, table, skip)
- Validation: strict mode, seed data validation
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from typing import Any

import pytest
from sqlmodel import SQLModel, Field
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


# Test models
class ClientModel(SQLModel, table=True):
    __tablename__ = "test_clients"
    id: UUID = Field(primary_key=True)
    name: str = Field(max_length=100)
    email: str | None = Field(default=None, max_length=100)


class ProductModel(SQLModel, table=True):
    __tablename__ = "test_products"
    id: UUID = Field(primary_key=True)
    name: str = Field(max_length=100)
    price: float = Field(default=0.0)


@pytest.fixture
async def session():
    """Create in-memory SQLite session for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
    
    await engine.dispose()


# ============================================================
# upsert_with_optimization: Only UPDATE if changed
# ============================================================


class TestUpsertWithOptimization:
    """Tests for upsert_with_optimization() with change detection."""

    @pytest.mark.asyncio
    async def test_insert_new_row(self, session: AsyncSession):
        """Inserts new row when it doesn't exist."""
        from pgsqlasync2fast_fastapi.seeders import upsert_with_optimization, SeederLog

        log = SeederLog()
        ns = uuid5(NAMESPACE_URL, "https://test.local")
        row_id = uuid5(ns, "clients:1")
        row = {"id": row_id, "name": "Test Client", "email": "test@example.com"}
        
        result = await upsert_with_optimization(session, ClientModel, row, log)
        
        assert result == "inserted"
        assert log.total_changes() == 1
        assert log.has_changes("test_clients")

    @pytest.mark.asyncio
    async def test_skip_unchanged_row(self, session: AsyncSession):
        """Skips update when row exists with same values."""
        from pgsqlasync2fast_fastapi.seeders import upsert_with_optimization, SeederLog

        log = SeederLog()
        ns = uuid5(NAMESPACE_URL, "https://test.local")
        row_id = uuid5(ns, "clients:1")
        
        # Insert first
        row1 = {"id": row_id, "name": "Test Client", "email": "test@example.com"}
        await upsert_with_optimization(session, ClientModel, row1, log)
        await session.commit()
        
        # Try to upsert same data
        log2 = SeederLog()
        row2 = {"id": row_id, "name": "Test Client", "email": "test@example.com"}
        result = await upsert_with_optimization(session, ClientModel, row2, log2)
        
        assert result == "skipped"
        assert log2.total_changes() == 0
        assert not log2.has_changes("test_clients")

    @pytest.mark.asyncio
    async def test_update_changed_row(self, session: AsyncSession):
        """Updates row when fields change."""
        from pgsqlasync2fast_fastapi.seeders import upsert_with_optimization, SeederLog

        log = SeederLog()
        ns = uuid5(NAMESPACE_URL, "https://test.local")
        row_id = uuid5(ns, "clients:1")
        
        # Insert first
        row1 = {"id": row_id, "name": "Old Name", "email": "old@example.com"}
        await upsert_with_optimization(session, ClientModel, row1, log)
        await session.commit()
        
        # Update with changed data
        log2 = SeederLog()
        row2 = {"id": row_id, "name": "New Name", "email": "new@example.com"}
        result = await upsert_with_optimization(session, ClientModel, row2, log2)
        
        assert result == "updated"
        assert log2.total_changes() == 1
        assert log2.has_changes("test_clients")
        
        # Verify old values are logged
        update_log = log2.updated_rows["test_clients"][0]
        assert update_log["id"] == row_id
        assert "name" in update_log["old_values"]
        assert update_log["old_values"]["name"] == "Old Name"

    @pytest.mark.asyncio
    async def test_update_partial_change(self, session: AsyncSession):
        """Updates row even if only one field changes."""
        from pgsqlasync2fast_fastapi.seeders import upsert_with_optimization, SeederLog

        log = SeederLog()
        ns = uuid5(NAMESPACE_URL, "https://test.local")
        row_id = uuid5(ns, "clients:1")
        
        # Insert first
        row1 = {"id": row_id, "name": "Test", "email": "test@example.com"}
        await upsert_with_optimization(session, ClientModel, row1, log)
        await session.commit()
        
        # Update with only email changed
        log2 = SeederLog()
        row2 = {"id": row_id, "name": "Test", "email": "new@example.com"}
        result = await upsert_with_optimization(session, ClientModel, row2, log2)
        
        assert result == "updated"
        assert log2.total_changes() == 1

    @pytest.mark.asyncio
    async def test_multiple_rows(self, session: AsyncSession):
        """Handles multiple rows correctly."""
        from pgsqlasync2fast_fastapi.seeders import upsert_with_optimization, SeederLog

        log = SeederLog()
        ns = uuid5(NAMESPACE_URL, "https://test.local")
        
        # Insert 3 rows
        for i in range(3):
            row_id = uuid5(ns, f"clients:{i}")
            row = {"id": row_id, "name": f"Client {i}", "email": f"client{i}@example.com"}
            result = await upsert_with_optimization(session, ClientModel, row, log)
            assert result == "inserted"
        
        assert log.total_changes() == 3
        await session.commit()
        
        # Try to upsert same data (should skip all)
        log2 = SeederLog()
        for i in range(3):
            row_id = uuid5(ns, f"clients:{i}")
            row = {"id": row_id, "name": f"Client {i}", "email": f"client{i}@example.com"}
            result = await upsert_with_optimization(session, ClientModel, row, log2)
            assert result == "skipped"
        
        assert log2.total_changes() == 0


# ============================================================
# SeederEngine: Main orchestrator
# ============================================================


class TestSeederEngine:
    """Tests for SeederEngine orchestrator."""

    @pytest.mark.asyncio
    async def test_load_manifest(self, tmp_path):
        """Loads manifest from JSON file."""
        from pgsqlasync2fast_fastapi.seeders import SeederEngine
        
        manifest_content = {
            "version": "2.0",
            "strict": True,
            "on_failure": "rollback_all",
            "boot": {
                "dev": {
                    "tables": {
                        "clients": {"file": "clients.json", "mode": "upsert"}
                    }
                }
            },
            "tables": {
                "clients": {"model": "test_models.ClientModel"}
            }
        }
        
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(manifest_content))
        
        engine = SeederEngine(
            manifest_path=str(manifest_file),
            model_registry={"test_clients": ClientModel},
            profile="dev",
        )
        
        assert engine.manifest["version"] == "2.0"
        assert engine.strict is True
        assert engine.on_failure == "rollback_all"

    @pytest.mark.asyncio
    async def test_strict_mode_validation_error(self, tmp_path):
        """Strict mode raises on validation error."""
        from pgsqlasync2fast_fastapi.seeders import SeederEngine, SeedValidationError
        
        manifest_content = {
            "version": "2.0",
            "strict": True,
            "boot": {
                "dev": {
                    "tables": {
                        "unknown_table": {"file": "unknown.json", "mode": "upsert"}
                    }
                }
            },
            "tables": {}
        }
        
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(manifest_content))
        
        engine = SeederEngine(
            manifest_path=str(manifest_file),
            model_registry={},
            profile="dev",
        )
        
        with pytest.raises(SeedValidationError):
            await engine.seed("test_db")

    @pytest.mark.asyncio
    async def test_non_strict_mode_continues(self, tmp_path):
        """Non-strict mode logs warning and continues."""
        from pgsqlasync2fast_fastapi.seeders import SeederEngine
        
        manifest_content = {
            "version": "2.0",
            "strict": False,
            "boot": {
                "dev": {
                    "tables": {
                        "unknown_table": {"file": "unknown.json", "mode": "upsert"}
                    }
                }
            },
            "tables": {}
        }
        
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(manifest_content))
        
        engine = SeederEngine(
            manifest_path=str(manifest_file),
            model_registry={},
            profile="dev",
        )
        
        # Should not raise, just return error in result
        result = await engine.seed("test_db")
        assert not result.success
        assert len(result.errors) > 0
