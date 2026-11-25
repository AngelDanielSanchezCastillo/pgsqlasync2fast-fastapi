"""
Integration tests for SeederEngine end-to-end behavior (TDD).

Covers:
- Full seed flow (insert / skip / update)
- FK resolution across tables
- rollback_all / rollback_table / skip on runtime failure
- SeederFilters (table allow-list + row predicate)
"""

from __future__ import annotations

import json
from uuid import UUID, uuid5, NAMESPACE_URL

import pytest
from sqlmodel import SQLModel, Field, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# ---------------------------------------------------------------------------
# Models (table names match manifest keys, like real projects)
# ---------------------------------------------------------------------------


class Client(SQLModel, table=True):
    __tablename__ = "clients"
    id: UUID = Field(primary_key=True)
    name: str = Field(max_length=100)
    email: str | None = Field(default=None, max_length=100)


class Product(SQLModel, table=True):
    __tablename__ = "products"
    id: UUID = Field(primary_key=True)
    name: str = Field(max_length=100)
    price: float = Field(default=0.0)


class Order(SQLModel, table=True):
    __tablename__ = "orders"
    id: UUID = Field(primary_key=True)
    client_id: UUID = Field(foreign_key="clients.id")
    product_id: UUID = Field(foreign_key="products.id")
    quantity: int = Field(default=0)


class RenamedOrder(SQLModel, table=True):
    """Model whose FK column name differs from the JSON field name."""

    __tablename__ = "renamed_orders"
    id: UUID = Field(primary_key=True)
    customer_ref: UUID = Field(foreign_key="clients.id")


MODEL_REGISTRY = {
    "clients": Client,
    "products": Product,
    "orders": Order,
    "renamed_orders": RenamedOrder,
}


@pytest.fixture
async def engine():
    """Shared in-memory SQLite engine (StaticPool keeps one connection)."""
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s


def write_manifest(tmp_path, manifest: dict) -> str:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return str(path)


def write_data(tmp_path, rel_path: str, rows: list[dict]) -> None:
    path = tmp_path / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))


def base_manifest(tables: dict, *, strict=True, on_failure="rollback_all") -> dict:
    return {
        "version": "2.0",
        "strict": strict,
        "on_failure": on_failure,
        "boot": {"dev": {"tables": tables}},
        "tables": {name: {"model": f"models.{name}"} for name in tables},
    }


# ============================================================
# Full seed flow
# ============================================================


class TestFullSeedFlow:
    @pytest.mark.asyncio
    async def test_seed_inserts_rows(self, tmp_path, engine):
        from pgsqlasync2fast_fastapi.seeders import SeederEngine

        write_data(tmp_path, "clients.json", [
            {"id": 1, "name": "Acme", "email": "acme@example.com"},
            {"id": 2, "name": "Globex", "email": "globex@example.com"},
        ])
        manifest_path = write_manifest(
            tmp_path,
            base_manifest({"clients": {"file": "clients.json", "mode": "upsert"}}),
        )

        seng = SeederEngine(
            manifest_path=manifest_path,
            model_registry=MODEL_REGISTRY,
            profile="dev",
            database_factory=lambda name: engine,
        )
        result = await seng.seed("tenant_a")

        assert result.success
        assert result.rows_inserted == 2
        assert result.rows_updated == 0
        assert result.rows_skipped == 0

        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            rows = (await s.execute(select(Client))).scalars().all()
            assert len(rows) == 2
            assert {r.name for r in rows} == {"Acme", "Globex"}

    @pytest.mark.asyncio
    async def test_reseed_skips_unchanged(self, tmp_path, engine):
        from pgsqlasync2fast_fastapi.seeders import SeederEngine

        write_data(tmp_path, "clients.json", [
            {"id": 1, "name": "Acme", "email": "acme@example.com"},
        ])
        manifest_path = write_manifest(
            tmp_path,
            base_manifest({"clients": {"file": "clients.json", "mode": "upsert"}}),
        )
        seng = SeederEngine(
            manifest_path=manifest_path,
            model_registry=MODEL_REGISTRY,
            profile="dev",
            database_factory=lambda name: engine,
        )

        first = await seng.seed("tenant_a")
        assert first.rows_inserted == 1

        second = await seng.seed("tenant_a")
        assert second.success
        assert second.rows_inserted == 0
        assert second.rows_updated == 0
        assert second.rows_skipped == 1
        assert second.log is not None
        assert second.log.total_changes() == 0

    @pytest.mark.asyncio
    async def test_reseed_updates_only_changed(self, tmp_path, engine):
        from pgsqlasync2fast_fastapi.seeders import SeederEngine

        write_data(tmp_path, "clients.json", [
            {"id": 1, "name": "Old", "email": "same@example.com"},
        ])
        manifest_path = write_manifest(
            tmp_path,
            base_manifest({"clients": {"file": "clients.json", "mode": "upsert"}}),
        )
        seng = SeederEngine(
            manifest_path=manifest_path,
            model_registry=MODEL_REGISTRY,
            profile="dev",
            database_factory=lambda name: engine,
        )
        await seng.seed("tenant_a")

        # Change only the name
        write_data(tmp_path, "clients.json", [
            {"id": 1, "name": "New", "email": "same@example.com"},
        ])
        result = await seng.seed("tenant_a")
        assert result.rows_updated == 1
        assert result.rows_skipped == 0

        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            row = (await s.execute(select(Client))).scalars().first()
            assert row.name == "New"

    @pytest.mark.asyncio
    async def test_field_mapping_renames_json_keys(self, tmp_path, engine):
        """field_mapping renames JSON keys to model field names before insert."""
        from pgsqlasync2fast_fastapi.seeders import SeederEngine, seed_uuid, validate_seeders

        ns = uuid5(NAMESPACE_URL, "https://2fast.local/seed")
        write_data(tmp_path, "clients.json", [{"id": 1, "name": "Acme"}])
        # JSON uses client_id; model column is customer_ref
        write_data(tmp_path, "renamed_orders.json", [{"id": 100, "client_id": 1}])
        manifest = base_manifest({
            "clients": {"file": "clients.json", "mode": "upsert"},
            "renamed_orders": {
                "file": "renamed_orders.json",
                "mode": "upsert",
                "field_mapping": {"client_id": "customer_ref"},
            },
        })
        manifest_path = write_manifest(tmp_path, manifest)

        # Validation must not flag client_id as an unknown key.
        assert validate_seeders(manifest_path, MODEL_REGISTRY) == []

        seng = SeederEngine(
            manifest_path=manifest_path,
            model_registry=MODEL_REGISTRY,
            profile="dev",
            database_factory=lambda name: engine,
        )
        result = await seng.seed("tenant_a")
        assert result.success

        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            row = (await s.execute(select(RenamedOrder))).scalars().first()
            assert row is not None
            assert row.customer_ref == seed_uuid("clients", 1, ns)

    @pytest.mark.asyncio
    async def test_fk_resolution_across_tables(self, tmp_path, engine):
        from pgsqlasync2fast_fastapi.seeders import SeederEngine, seed_uuid

        ns = uuid5(NAMESPACE_URL, "https://2fast.local/seed")
        write_data(tmp_path, "clients.json", [{"id": 1, "name": "Acme"}])
        write_data(tmp_path, "products.json", [{"id": 10, "name": "Widget", "price": 9.5}])
        write_data(tmp_path, "orders.json", [
            {"id": 100, "client_id": 1, "product_id": 10, "quantity": 3},
        ])
        manifest_path = write_manifest(
            tmp_path,
            base_manifest({
                "clients": {"file": "clients.json", "mode": "upsert"},
                "products": {"file": "products.json", "mode": "upsert"},
                "orders": {"file": "orders.json", "mode": "upsert"},
            }),
        )
        seng = SeederEngine(
            manifest_path=manifest_path,
            model_registry=MODEL_REGISTRY,
            profile="dev",
            database_factory=lambda name: engine,
        )
        result = await seng.seed("tenant_a")
        assert result.success

        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            order = (await s.execute(select(Order))).scalars().first()
            assert order is not None
            assert order.client_id == seed_uuid("clients", 1, ns)
            assert order.product_id == seed_uuid("products", 10, ns)
            assert order.quantity == 3


# ============================================================
# Rollback behavior
# ============================================================


def _two_table_failure_manifest(tmp_path):
    """clients (succeeds) then orders (insert_only collides)."""
    write_data(tmp_path, "clients.json", [{"id": 1, "name": "Acme"}])
    write_data(tmp_path, "orders.json", [
        # Same deterministic id as the pre-existing row inserted below.
        {"id": 99, "client_id": 1, "product_id": 1, "quantity": 1},
    ])
    return base_manifest({
        "clients": {"file": "clients.json", "mode": "upsert"},
        "orders": {"file": "orders.json", "mode": "insert_only"},
    })


async def _seed_preexisting_order(engine) -> UUID:
    """Insert a product + order that will collide with seed order id 99.

    Order insert_only collision is on primary key: we insert the SAME
    deterministic id the seeder would compute for orders:99.
    """
    ns = uuid5(NAMESPACE_URL, "https://2fast.local/seed")
    from pgsqlasync2fast_fastapi.seeders import seed_uuid

    product_id = seed_uuid("products", 1, ns)
    client_id = seed_uuid("clients", 1, ns)
    order_id = seed_uuid("orders", 99, ns)

    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        s.add(Product(id=product_id, name="P", price=1.0))
        s.add(Client(id=client_id, name="Existing"))
        s.add(Order(id=order_id, client_id=client_id, product_id=product_id, quantity=1))
        await s.commit()
    return client_id


class TestRollback:
    @pytest.mark.asyncio
    async def test_rollback_all_on_failure(self, tmp_path, engine):
        from pgsqlasync2fast_fastapi.seeders import SeederEngine

        await _seed_preexisting_order(engine)
        manifest_path = write_manifest(
            tmp_path, _two_table_failure_manifest(tmp_path)
        )
        seng = SeederEngine(
            manifest_path=manifest_path,
            model_registry=MODEL_REGISTRY,
            profile="dev",
            database_factory=lambda name: engine,
            on_failure="rollback_all",
        )
        result = await seng.seed("tenant_a")

        assert not result.success
        assert result.rolled_back is True
        # clients insert must have been rolled back
        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            names = {r.name for r in (await s.execute(select(Client))).scalars().all()}
            assert "Acme" not in names
            assert "Existing" in names  # pre-existing row untouched

    @pytest.mark.asyncio
    async def test_rollback_table_keeps_other_tables(self, tmp_path, engine):
        from pgsqlasync2fast_fastapi.seeders import SeederEngine

        await _seed_preexisting_order(engine)
        manifest_path = write_manifest(
            tmp_path, _two_table_failure_manifest(tmp_path)
        )
        seng = SeederEngine(
            manifest_path=manifest_path,
            model_registry=MODEL_REGISTRY,
            profile="dev",
            database_factory=lambda name: engine,
            on_failure="rollback_table",
        )
        result = await seng.seed("tenant_a")

        assert not result.success
        assert result.table_rolled_back == "orders"
        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            names = {r.name for r in (await s.execute(select(Client))).scalars().all()}
            assert "Acme" in names  # committed before failure, kept

    @pytest.mark.asyncio
    async def test_skip_keeps_committed_tables(self, tmp_path, engine):
        from pgsqlasync2fast_fastapi.seeders import SeederEngine

        await _seed_preexisting_order(engine)
        manifest_path = write_manifest(
            tmp_path, _two_table_failure_manifest(tmp_path)
        )
        seng = SeederEngine(
            manifest_path=manifest_path,
            model_registry=MODEL_REGISTRY,
            profile="dev",
            database_factory=lambda name: engine,
            on_failure="skip",
        )
        result = await seng.seed("tenant_a")

        assert not result.success
        assert result.rolled_back is False
        assert result.table_rolled_back is None
        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            names = {r.name for r in (await s.execute(select(Client))).scalars().all()}
            assert "Acme" in names


# ============================================================
# Filters
# ============================================================


class TestFilters:
    @pytest.mark.asyncio
    async def test_table_allow_list(self, tmp_path, engine):
        from pgsqlasync2fast_fastapi.seeders import SeederEngine, SeederFilters

        write_data(tmp_path, "clients.json", [{"id": 1, "name": "Acme"}])
        write_data(tmp_path, "products.json", [{"id": 10, "name": "Widget"}])
        manifest_path = write_manifest(
            tmp_path,
            base_manifest({
                "clients": {"file": "clients.json", "mode": "upsert"},
                "products": {"file": "products.json", "mode": "upsert"},
            }),
        )
        seng = SeederEngine(
            manifest_path=manifest_path,
            model_registry=MODEL_REGISTRY,
            profile="dev",
            database_factory=lambda name: engine,
        )
        result = await seng.seed(
            "tenant_a", filters=SeederFilters(tables=["clients"])
        )
        assert result.success

        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            assert len((await s.execute(select(Client))).scalars().all()) == 1
            assert len((await s.execute(select(Product))).scalars().all()) == 0

    @pytest.mark.asyncio
    async def test_row_predicate(self, tmp_path, engine):
        from pgsqlasync2fast_fastapi.seeders import SeederEngine, SeederFilters

        write_data(tmp_path, "clients.json", [
            {"id": 1, "name": "Demo", "email": None},
            {"id": 2, "name": "Real", "email": None},
        ])
        manifest_path = write_manifest(
            tmp_path,
            base_manifest({"clients": {"file": "clients.json", "mode": "upsert"}}),
        )
        seng = SeederEngine(
            manifest_path=manifest_path,
            model_registry=MODEL_REGISTRY,
            profile="dev",
            database_factory=lambda name: engine,
        )
        result = await seng.seed(
            "tenant_a",
            filters=SeederFilters(rows={"clients": lambda r: r["name"] == "Demo"}),
        )
        assert result.success
        assert result.rows_inserted == 1

        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            names = {r.name for r in (await s.execute(select(Client))).scalars().all()}
            assert names == {"Demo"}
