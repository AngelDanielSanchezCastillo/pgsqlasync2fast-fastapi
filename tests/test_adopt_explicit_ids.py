"""
Tests for adopt_explicit_ids(): migrate existing DB rows to manifest literals.

Covers:
- id rewrite matched by natural key
- FK cascade (children repointed to the new ids)
- idempotency (no-op when literals already adopted)
- dry_run (plan only, no writes)
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4, uuid7

import pytest
from sqlmodel import SQLModel, Field, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


class ACategory(SQLModel, table=True):
    __tablename__ = "a_categories"
    id: UUID = Field(primary_key=True)
    name: str = Field(max_length=50, unique=True)


class ARole(SQLModel, table=True):
    __tablename__ = "a_roles"
    id: UUID = Field(primary_key=True)
    name: str = Field(max_length=50, unique=True)


class APermission(SQLModel, table=True):
    __tablename__ = "a_permissions"
    id: UUID = Field(primary_key=True)
    name: str = Field(max_length=50, unique=True)
    permission_category_id: UUID = Field(foreign_key="a_categories.id")


class ARoleUser(SQLModel, table=True):
    __tablename__ = "a_role_users"
    id: UUID = Field(primary_key=True)
    role_id: UUID = Field(foreign_key="a_roles.id")
    user_name: str = Field(max_length=50)


REGISTRY = {
    "a_categories": ACategory,
    "a_roles": ARole,
    "a_permissions": APermission,
    "a_role_users": ARoleUser,
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


def write_manifest(tmp_path, tables: dict) -> str:
    manifest = {
        "version": "2.0",
        "strict": True,
        "boot": {"dev": {"tables": tables}},
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return str(path)


def write_data(tmp_path, name: str, rows: list[dict]) -> None:
    (tmp_path / name).write_text(json.dumps(rows))


def make_seeder(manifest_path, engine):
    from pgsqlasync2fast_fastapi.seeders import SeederEngine

    return SeederEngine(
        manifest_path=manifest_path,
        model_registry=REGISTRY,
        profile="dev",
        database_factory=lambda name: engine,
    )


async def seed_old_rows(engine) -> dict[str, UUID]:
    """Insert pre-existing rows with random (non-literal) ids."""
    cat_id, role_id = uuid4(), uuid4()
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        s.add(ACategory(id=cat_id, name="Admin"))
        s.add(ARole(id=role_id, name="admin"))
        s.add(APermission(id=uuid4(), name="read", permission_category_id=cat_id))
        s.add(ARoleUser(id=uuid4(), role_id=role_id, user_name="u1"))
        await s.commit()
    return {"cat": cat_id, "role": role_id}


async def fetch(session, model):
    return (await session.execute(select(model))).scalars().all()


# ============================================================
# Tests
# ============================================================


@pytest.mark.asyncio
async def test_adopt_rewrites_ids_and_cascades_fks(tmp_path, engine):
    from pgsqlasync2fast_fastapi.seeders import adopt_explicit_ids

    old = await seed_old_rows(engine)

    new_cat = uuid7()
    new_role = uuid7()
    new_perm = uuid7()
    write_data(tmp_path, "a_categories.json", [{"id": str(new_cat), "name": "Admin"}])
    write_data(tmp_path, "a_roles.json", [{"id": str(new_role), "name": "admin"}])
    write_data(tmp_path, "a_permissions.json", [
        {"id": str(new_perm), "name": "read", "permission_category_id": str(new_cat)},
    ])
    manifest_path = write_manifest(tmp_path, {
        "a_categories": {"file": "a_categories.json", "mode": "upsert", "natural_key": ["name"]},
        "a_roles": {"file": "a_roles.json", "mode": "upsert", "natural_key": ["name"]},
        "a_permissions": {"file": "a_permissions.json", "mode": "upsert", "natural_key": ["name"]},
    })
    seeder = make_seeder(manifest_path, engine)

    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        remaps = await adopt_explicit_ids(s, seeder, layer="boot")
        assert len(remaps) == 3
        await s.commit()

    async with maker() as s:
        cats = await fetch(s, ACategory)
        roles = await fetch(s, ARole)
        perms = await fetch(s, APermission)
        role_users = await fetch(s, ARoleUser)
        assert [c.id for c in cats] == [new_cat]
        assert [r.id for r in roles] == [new_role]
        assert [p.id for p in perms] == [new_perm]
        assert perms[0].permission_category_id == new_cat
        # child FK cascaded to the new role id
        assert role_users[0].role_id == new_role
        # no duplicates
        assert len(cats) == 1 and len(roles) == 1 and len(perms) == 1


@pytest.mark.asyncio
async def test_adopt_is_idempotent(tmp_path, engine):
    from pgsqlasync2fast_fastapi.seeders import adopt_explicit_ids

    await seed_old_rows(engine)
    new_cat, new_role, new_perm = uuid7(), uuid7(), uuid7()
    write_data(tmp_path, "a_categories.json", [{"id": str(new_cat), "name": "Admin"}])
    write_data(tmp_path, "a_roles.json", [{"id": str(new_role), "name": "admin"}])
    write_data(tmp_path, "a_permissions.json", [
        {"id": str(new_perm), "name": "read", "permission_category_id": str(new_cat)},
    ])
    manifest_path = write_manifest(tmp_path, {
        "a_categories": {"file": "a_categories.json", "mode": "upsert", "natural_key": ["name"]},
        "a_roles": {"file": "a_roles.json", "mode": "upsert", "natural_key": ["name"]},
        "a_permissions": {"file": "a_permissions.json", "mode": "upsert", "natural_key": ["name"]},
    })
    seeder = make_seeder(manifest_path, engine)

    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        assert len(await adopt_explicit_ids(s, seeder, layer="boot")) == 3
        await s.commit()
    async with maker() as s:
        assert await adopt_explicit_ids(s, seeder, layer="boot") == []
        await s.commit()


@pytest.mark.asyncio
async def test_adopt_dry_run_writes_nothing(tmp_path, engine):
    from pgsqlasync2fast_fastapi.seeders import adopt_explicit_ids

    old = await seed_old_rows(engine)
    new_cat = uuid7()
    write_data(tmp_path, "a_categories.json", [{"id": str(new_cat), "name": "Admin"}])
    manifest_path = write_manifest(tmp_path, {
        "a_categories": {"file": "a_categories.json", "mode": "upsert", "natural_key": ["name"]},
    })
    seeder = make_seeder(manifest_path, engine)

    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        remaps = await adopt_explicit_ids(s, seeder, layer="boot", dry_run=True)
        assert len(remaps) == 1
        await s.rollback()
    async with maker() as s:
        cats = await fetch(s, ACategory)
        assert [c.id for c in cats] == [old["cat"]]
