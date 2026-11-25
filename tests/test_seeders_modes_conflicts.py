"""
Tests for seed modes, conflict resolution and polymorphic FK integration (TDD).

Covers:
- Modes: skip_if_exists, insert_only, replace_all
- unique_keys validation (conflict_resolution="validate")
- park_and_upsert (conflict_resolution="park_and_upsert")
- Polymorphic FKs through SeederEngine
"""

from __future__ import annotations

import json
from uuid import UUID, uuid5, NAMESPACE_URL

import pytest
from sqlmodel import SQLModel, Field, select
from sqlalchemy import UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pgsqlasync2fast_fastapi.seeders import seed_uuid


class MClient(SQLModel, table=True):
    __tablename__ = "m_clients"
    id: UUID = Field(primary_key=True)
    name: str = Field(max_length=100)


class MSetting(SQLModel, table=True):
    __tablename__ = "m_settings"
    id: UUID = Field(primary_key=True)
    key: str = Field(max_length=50)


class MStage(SQLModel, table=True):
    __tablename__ = "m_stages"
    __table_args__ = (UniqueConstraint("workflow_id", "stage_key"),)
    id: UUID = Field(primary_key=True)
    workflow_id: UUID = Field(foreign_key="m_workflows.id")
    stage_key: str = Field(max_length=50)
    pos_x: int = Field(default=0)


class MWorkflow(SQLModel, table=True):
    __tablename__ = "m_workflows"
    id: UUID = Field(primary_key=True)
    name: str = Field(max_length=50)


class MAddress(SQLModel, table=True):
    __tablename__ = "m_addresses"
    id: UUID = Field(primary_key=True)
    addressable_id: UUID
    addressable_type: str = Field(max_length=20)
    street: str = Field(max_length=100)


class MSupplier(SQLModel, table=True):
    __tablename__ = "m_suppliers"
    id: UUID = Field(primary_key=True)
    name: str = Field(max_length=100)


REGISTRY = {
    "m_clients": MClient,
    "m_settings": MSetting,
    "m_stages": MStage,
    "m_workflows": MWorkflow,
    "m_addresses": MAddress,
    "m_suppliers": MSupplier,
}


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield eng
    await eng.dispose()


def write_manifest(tmp_path, tables: dict, *, strict=True, on_failure="rollback_all",
                   polymorphic_fks=None) -> str:
    manifest = {
        "version": "2.0",
        "strict": strict,
        "on_failure": on_failure,
        "boot": {"dev": {"tables": tables}},
        "tables": {name: {"model": f"models.{name}"} for name in tables},
    }
    if polymorphic_fks:
        manifest["polymorphic_fks"] = polymorphic_fks
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return str(path)


def write_data(tmp_path, name: str, rows: list[dict]) -> None:
    (tmp_path / name).write_text(json.dumps(rows))


def make_engine(manifest_path, engine, **kwargs):
    from pgsqlasync2fast_fastapi.seeders import SeederEngine

    return SeederEngine(
        manifest_path=manifest_path,
        model_registry=REGISTRY,
        profile="dev",
        database_factory=lambda name: engine,
        **kwargs,
    )


async def fetch_all(engine, model):
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        return (await s.execute(select(model))).scalars().all()


# ============================================================
# Modes
# ============================================================


class TestModes:
    @pytest.mark.asyncio
    async def test_skip_if_exists_when_table_empty(self, tmp_path, engine):
        write_data(tmp_path, "m_settings.json", [{"id": 1, "key": "A"}])
        manifest_path = write_manifest(
            tmp_path, {"m_settings": {"file": "m_settings.json", "mode": "skip_if_exists"}}
        )
        seng = make_engine(manifest_path, engine)
        result = await seng.seed("db")
        assert result.success
        assert result.rows_inserted == 1

    @pytest.mark.asyncio
    async def test_skip_if_exists_when_table_has_rows(self, tmp_path, engine):
        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            s.add(MSetting(id=seed_uuid("m_settings", 5, uuid5(NAMESPACE_URL, "https://2fast.local/seed")), key="Existing"))
            await s.commit()

        write_data(tmp_path, "m_settings.json", [{"id": 1, "key": "A"}])
        manifest_path = write_manifest(
            tmp_path, {"m_settings": {"file": "m_settings.json", "mode": "skip_if_exists"}}
        )
        seng = make_engine(manifest_path, engine)
        result = await seng.seed("db")
        assert result.success
        assert result.rows_inserted == 0
        assert result.rows_skipped == 1

    @pytest.mark.asyncio
    async def test_insert_only_happy_path(self, tmp_path, engine):
        write_data(tmp_path, "m_clients.json", [{"id": 1, "name": "Acme"}])
        manifest_path = write_manifest(
            tmp_path, {"m_clients": {"file": "m_clients.json", "mode": "insert_only"}}
        )
        seng = make_engine(manifest_path, engine)
        result = await seng.seed("db")
        assert result.success
        assert result.rows_inserted == 1

    @pytest.mark.asyncio
    async def test_replace_all_replaces_rows(self, tmp_path, engine):
        # Pre-existing unrelated row
        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            s.add(MClient(id=uuid5(NAMESPACE_URL, "old"), name="Old"))
            await s.commit()

        write_data(tmp_path, "m_clients.json", [{"id": 1, "name": "New"}])
        manifest_path = write_manifest(
            tmp_path, {"m_clients": {"file": "m_clients.json", "mode": "replace_all"}}
        )
        seng = make_engine(manifest_path, engine)
        result = await seng.seed("db")
        assert result.success
        rows = await fetch_all(engine, MClient)
        assert {r.name for r in rows} == {"New"}


# ============================================================
# Conflict resolution
# ============================================================


class TestConflictResolution:
    @pytest.mark.asyncio
    async def test_validate_rejects_duplicate_unique_keys(self, tmp_path, engine):
        write_data(tmp_path, "m_stages.json", [
            {"id": 1, "workflow_id": 1, "stage_key": "borrador"},
            {"id": 2, "workflow_id": 1, "stage_key": "borrador"},  # duplicate
        ])
        manifest_path = write_manifest(
            tmp_path,
            {
                "m_stages": {
                    "file": "m_stages.json",
                    "mode": "upsert",
                    "unique_keys": ["workflow_id", "stage_key"],
                    "conflict_resolution": "validate",
                }
            },
        )
        seng = make_engine(manifest_path, engine)
        with pytest.raises(Exception, match="duplicate unique key"):
            await seng.seed("db")

    @pytest.mark.asyncio
    async def test_park_and_upsert_allows_key_swap(self, tmp_path, engine):
        ns = uuid5(NAMESPACE_URL, "https://2fast.local/seed")
        wf_id = seed_uuid("m_workflows", 1, ns)

        # Pre-existing: workflow + stage1 has "a", stage2 has "b"
        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            s.add(MWorkflow(id=wf_id, name="W"))
            s.add(MStage(id=seed_uuid("m_stages", 1, ns), workflow_id=wf_id, stage_key="a"))
            s.add(MStage(id=seed_uuid("m_stages", 2, ns), workflow_id=wf_id, stage_key="b"))
            await s.commit()

        # Seed swaps the keys: stage1 -> "b", stage2 -> "a"
        write_data(tmp_path, "m_workflows.json", [{"id": 1, "name": "W"}])
        write_data(tmp_path, "m_stages.json", [
            {"id": 1, "workflow_id": 1, "stage_key": "b", "pos_x": 10},
            {"id": 2, "workflow_id": 1, "stage_key": "a", "pos_x": 20},
        ])
        manifest_path = write_manifest(
            tmp_path,
            {
                "m_workflows": {"file": "m_workflows.json", "mode": "upsert"},
                "m_stages": {
                    "file": "m_stages.json",
                    "mode": "upsert",
                    "unique_keys": ["workflow_id", "stage_key"],
                    "conflict_resolution": "park_and_upsert",
                },
            },
        )
        seng = make_engine(manifest_path, engine)
        result = await seng.seed("db")
        assert result.success

        stages = await fetch_all(engine, MStage)
        keys = {str(st.id): st.stage_key for st in stages}
        assert keys[str(seed_uuid("m_stages", 1, ns))] == "b"
        assert keys[str(seed_uuid("m_stages", 2, ns))] == "a"


class TestPolymorphic:
    @pytest.mark.asyncio
    async def test_polymorphic_fk_through_engine(self, tmp_path, engine):
        ns = uuid5(NAMESPACE_URL, "https://2fast.local/seed")
        write_data(tmp_path, "m_clients.json", [{"id": 1, "name": "Acme"}])
        write_data(tmp_path, "m_suppliers.json", [{"id": 1, "name": "Supp"}])
        write_data(tmp_path, "m_addresses.json", [
            {"id": 1, "addressable_id": 1, "addressable_type": "client", "street": "Main"},
            {"id": 2, "addressable_id": 1, "addressable_type": "supplier", "street": "Side"},
        ])
        manifest_path = write_manifest(
            tmp_path,
            {
                "m_clients": {"file": "m_clients.json", "mode": "upsert"},
                "m_suppliers": {"file": "m_suppliers.json", "mode": "upsert"},
                "m_addresses": {"file": "m_addresses.json", "mode": "upsert"},
            },
            polymorphic_fks={
                "m_addresses": {
                    "addressable_id": {
                        "discriminator": "addressable_type",
                        "map": {"client": "m_clients", "supplier": "m_suppliers"},
                    }
                }
            },
        )
        seng = make_engine(manifest_path, engine)
        result = await seng.seed("db")
        assert result.success

        addresses = await fetch_all(engine, MAddress)
        by_street = {a.street: a for a in addresses}
        assert by_street["Main"].addressable_id == seed_uuid("m_clients", 1, ns)
        assert by_street["Side"].addressable_id == seed_uuid("m_suppliers", 1, ns)
