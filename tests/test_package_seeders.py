"""
Tests for package-level seeder registration and validation (ODD-5 Fase 1.5).

Covers:
- validate_seeders(): structural validation usable by packages in CI
- register_package_seeders(): global package registry with conflict detection
- seed_all(): orchestrates every registered package via SeederEngine
"""

from __future__ import annotations

import json
from uuid import UUID, uuid5, uuid7, NAMESPACE_URL

import pytest
from sqlmodel import SQLModel, Field, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


class Role(SQLModel, table=True):
    __tablename__ = "roles"
    id: UUID = Field(primary_key=True)
    name: str = Field(max_length=50)


class Permission(SQLModel, table=True):
    __tablename__ = "permissions"
    id: UUID = Field(primary_key=True)
    name: str = Field(max_length=50)


class Counter(SQLModel, table=True):
    """Integer primary key model: integer seed ids are legitimate."""

    __tablename__ = "counters"
    id: int = Field(primary_key=True)
    name: str = Field(max_length=50)


REGISTRY = {"roles": Role, "permissions": Permission}


@pytest.fixture(autouse=True)
def _clear_registry():
    from pgsqlasync2fast_fastapi.seeders import clear_package_registry

    clear_package_registry()
    yield
    clear_package_registry()


def write_package(tmp_path, *, tables: dict, data: dict[str, list[dict]],
                  layer: str = "boot", profile: str = "dev") -> str:
    """Write a valid package manifest + data files. Returns manifest path."""
    root = tmp_path / "pkg"
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": "2.0",
        "strict": True,
        "on_failure": "rollback_all",
        layer: {profile: {"tables": tables}},
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    for name, rows in data.items():
        (root / name).write_text(json.dumps(rows))
    return str(root / "manifest.json")


# ============================================================
# validate_seeders
# ============================================================


class TestValidateSeeders:
    def test_valid_seeders_return_no_errors(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import validate_seeders

        path = write_package(
            tmp_path,
            tables={"roles": {"file": "roles.json", "mode": "upsert"}},
            data={"roles.json": [{"id": 1, "name": "Admin"}]},
        )
        assert validate_seeders(path, REGISTRY) == []

    def test_unknown_table_model_reported(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import validate_seeders

        path = write_package(
            tmp_path,
            tables={"unknown": {"file": "unknown.json"}},
            data={"unknown.json": [{"id": 1}]},
        )
        errors = validate_seeders(path, REGISTRY)
        assert any("no model class registered" in e for e in errors)

    def test_unknown_keys_reported(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import validate_seeders

        path = write_package(
            tmp_path,
            tables={"roles": {"file": "roles.json"}},
            data={"roles.json": [{"id": 1, "name": "Admin", "bogus": True}]},
        )
        errors = validate_seeders(path, REGISTRY)
        assert any("unknown keys" in e for e in errors)

    def test_missing_file_reported(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import validate_seeders

        path = write_package(
            tmp_path,
            tables={"roles": {"file": "roles.json"}},
            data={},  # no file written
        )
        errors = validate_seeders(path, REGISTRY)
        assert any("seed file not found" in e for e in errors)

    def test_duplicate_unique_keys_reported(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import validate_seeders

        path = write_package(
            tmp_path,
            tables={
                "roles": {
                    "file": "roles.json",
                    "unique_keys": ["name"],
                }
            },
            data={"roles.json": [
                {"id": 1, "name": "Admin"},
                {"id": 2, "name": "Admin"},
            ]},
        )
        errors = validate_seeders(path, REGISTRY)
        assert any("duplicate unique key" in e for e in errors)

    def test_invalid_json_reported(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import validate_seeders

        path = write_package(
            tmp_path,
            tables={"roles": {"file": "roles.json"}},
            data={},
        )
        (tmp_path / "pkg" / "roles.json").write_text("{not valid json")
        errors = validate_seeders(path, REGISTRY)
        assert any("invalid JSON" in e for e in errors)

    def test_missing_profile_reported(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import validate_seeders

        path = write_package(
            tmp_path,
            tables={"roles": {"file": "roles.json"}},
            data={"roles.json": [{"id": 1, "name": "Admin"}]},
        )
        errors = validate_seeders(path, REGISTRY, profile="prod")
        assert any("prod" in e for e in errors)

    def test_integer_ids_against_uuid_pk_are_warnings(self, tmp_path):
        """Integer ids for a UUID-pk table are advisory warnings, not errors."""
        from pgsqlasync2fast_fastapi.seeders import validate_seeders_report

        path = write_package(
            tmp_path,
            tables={"roles": {"file": "roles.json"}},
            data={"roles.json": [{"id": 1, "name": "Admin"}]},
        )
        report = validate_seeders_report(path, REGISTRY)
        assert report.errors == []
        assert report.ok
        assert any("integer id" in w for w in report.warnings)

    def test_uuid_ids_produce_no_warnings(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import validate_seeders_report

        path = write_package(
            tmp_path,
            tables={"roles": {"file": "roles.json"}},
            data={"roles.json": [
                {"id": "edc8dff4-4309-5de9-bdf8-63e2289a2a50", "name": "Admin"},
            ]},
        )
        report = validate_seeders_report(path, REGISTRY)
        assert report.errors == []
        assert report.warnings == []

    def test_integer_pk_allows_integer_ids(self, tmp_path):
        """A table with an integer PK legitimately uses integer ids."""
        from pgsqlasync2fast_fastapi.seeders import validate_seeders_report

        path = write_package(
            tmp_path,
            tables={"counters": {"file": "counters.json"}},
            data={"counters.json": [{"id": 1, "name": "c"}]},
        )
        report = validate_seeders_report(path, {"counters": Counter})
        assert report.errors == []
        assert report.warnings == []


# ============================================================
# register_package_seeders
# ============================================================


class TestRegisterPackageSeeders:
    def test_register_and_list(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import (
            PackageSeederConfig,
            register_package_seeders,
            get_registered_packages,
        )

        path = write_package(
            tmp_path,
            tables={"roles": {"file": "roles.json"}},
            data={"roles.json": [{"id": 1, "name": "Admin"}]},
        )
        register_package_seeders(PackageSeederConfig(
            package_name="permissions2fast",
            manifest_path=path,
            model_registry=REGISTRY,
            connection="auth",
            priority=10,
        ))
        packages = get_registered_packages()
        assert len(packages) == 1
        assert packages[0].package_name == "permissions2fast"
        assert packages[0].priority == 10

    def test_re_register_same_package_replaces(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import (
            PackageSeederConfig,
            register_package_seeders,
            get_registered_packages,
        )

        path = write_package(
            tmp_path,
            tables={"roles": {"file": "roles.json"}},
            data={"roles.json": [{"id": 1, "name": "Admin"}]},
        )
        cfg = dict(package_name="pkg", manifest_path=path, model_registry=REGISTRY, connection="auth")
        register_package_seeders(PackageSeederConfig(priority=10, **cfg))
        register_package_seeders(PackageSeederConfig(priority=20, **cfg))
        packages = get_registered_packages()
        assert len(packages) == 1
        assert packages[0].priority == 20

    def test_conflict_between_packages_same_tables(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import (
            PackageSeederConfig,
            register_package_seeders,
            PackageSeederConflictError,
        )

        path_a = write_package(
            tmp_path / "a",
            tables={"roles": {"file": "roles.json"}},
            data={"roles.json": [{"id": 1, "name": "Admin"}]},
        )
        path_b = write_package(
            tmp_path / "b",
            tables={"roles": {"file": "roles.json"}},
            data={"roles.json": [{"id": 1, "name": "Admin"}]},
        )
        register_package_seeders(PackageSeederConfig(
            package_name="pkg-a", manifest_path=path_a,
            model_registry=REGISTRY, connection="auth",
        ))
        with pytest.raises(PackageSeederConflictError, match="roles"):
            register_package_seeders(PackageSeederConfig(
                package_name="pkg-b", manifest_path=path_b,
                model_registry=REGISTRY, connection="auth",
            ))

    def test_no_conflict_different_connections(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import (
            PackageSeederConfig,
            register_package_seeders,
            get_registered_packages,
        )

        path_a = write_package(
            tmp_path / "a",
            tables={"roles": {"file": "roles.json"}},
            data={"roles.json": [{"id": 1, "name": "A"}]},
        )
        path_b = write_package(
            tmp_path / "b",
            tables={"roles": {"file": "roles.json"}},
            data={"roles.json": [{"id": 1, "name": "B"}]},
        )
        register_package_seeders(PackageSeederConfig(
            package_name="pkg-a", manifest_path=path_a,
            model_registry=REGISTRY, connection="auth",
        ))
        register_package_seeders(PackageSeederConfig(
            package_name="pkg-b", manifest_path=path_b,
            model_registry=REGISTRY, connection="tenant",
        ))
        assert len(get_registered_packages()) == 2


# ============================================================
# seed_all orchestration
# ============================================================


class TestSeedAll:
    @pytest.mark.asyncio
    async def test_seed_all_runs_registered_packages(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import (
            PackageSeederConfig,
            register_package_seeders,
            seed_all,
        )

        path = write_package(
            tmp_path,
            tables={"roles": {"file": "roles.json"}, "permissions": {"file": "permissions.json"}},
            data={
                "roles.json": [{"id": 1, "name": "Admin"}],
                "permissions.json": [{"id": 1, "name": "read"}],
            },
        )
        register_package_seeders(PackageSeederConfig(
            package_name="permissions2fast",
            manifest_path=path,
            model_registry=REGISTRY,
            connection="auth",
        ))

        eng = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with eng.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        results = await seed_all("dev", engine_resolver=lambda connection: eng)
        assert "permissions2fast" in results
        assert results["permissions2fast"].success
        assert results["permissions2fast"].rows_inserted == 2

        maker = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            assert len((await s.execute(select(Role))).scalars().all()) == 1
            assert len((await s.execute(select(Permission))).scalars().all()) == 1
        await eng.dispose()

    @pytest.mark.asyncio
    async def test_seed_all_priority_order(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import (
            PackageSeederConfig,
            register_package_seeders,
            seed_all,
        )

        path_low = write_package(
            tmp_path / "low",
            tables={"roles": {"file": "roles.json"}},
            data={"roles.json": [{"id": 1, "name": "Low"}]},
        )
        path_high = write_package(
            tmp_path / "high",
            tables={"permissions": {"file": "permissions.json"}},
            data={"permissions.json": [{"id": 1, "name": "High"}]},
        )
        register_package_seeders(PackageSeederConfig(
            package_name="late", manifest_path=path_low,
            model_registry=REGISTRY, connection="auth", priority=90,
        ))
        register_package_seeders(PackageSeederConfig(
            package_name="early", manifest_path=path_high,
            model_registry=REGISTRY, connection="auth", priority=10,
        ))

        eng = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with eng.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        results = await seed_all("dev", engine_resolver=lambda connection: eng)
        assert list(results.keys()) == ["early", "late"]
        await eng.dispose()


# ============================================================
# Composition: additive base + app overrides / opt-out
# ============================================================


async def _sqlite_engine():
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return eng


class TestSeederComposition:
    @pytest.mark.asyncio
    async def test_additive_packages_all_run(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import (
            PackageSeederConfig,
            register_package_seeders,
            seed_all,
        )

        path_roles = write_package(
            tmp_path / "roles",
            tables={"roles": {"file": "roles.json"}},
            data={"roles.json": [{"id": 1, "name": "Admin"}]},
        )
        path_perms = write_package(
            tmp_path / "perms",
            tables={"permissions": {"file": "permissions.json"}},
            data={"permissions.json": [{"id": 1, "name": "read"}]},
        )
        register_package_seeders(PackageSeederConfig(
            package_name="base", manifest_path=path_roles,
            model_registry=REGISTRY, connection="auth", priority=10,
        ))
        register_package_seeders(PackageSeederConfig(
            package_name="app", manifest_path=path_perms,
            model_registry=REGISTRY, connection="auth", priority=20,
        ))

        eng = await _sqlite_engine()
        results = await seed_all("dev", engine_resolver=lambda _c: eng)
        assert set(results) == {"base", "app"}
        assert results["base"].rows_inserted == 1
        assert results["app"].rows_inserted == 1
        await eng.dispose()

    @pytest.mark.asyncio
    async def test_override_takes_table_over(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import (
            PackageSeederConfig,
            register_package_seeders,
            seed_all,
        )

        path_base = write_package(
            tmp_path / "base",
            tables={"roles": {"file": "roles.json"}},
            data={"roles.json": [{"id": 1, "name": "BaseAdmin"}]},
        )
        path_app = write_package(
            tmp_path / "app",
            tables={
                "roles": {"file": "roles.json"},
                "permissions": {"file": "permissions.json"},
            },
            data={
                "roles.json": [{"id": 1, "name": "AppAdmin"}],
                "permissions.json": [{"id": 1, "name": "read"}],
            },
        )
        register_package_seeders(PackageSeederConfig(
            package_name="base", manifest_path=path_base,
            model_registry=REGISTRY, connection="auth", priority=10,
        ))
        register_package_seeders(PackageSeederConfig(
            package_name="app", manifest_path=path_app,
            model_registry=REGISTRY, connection="auth", priority=20,
            overrides=["roles"],
        ))

        eng = await _sqlite_engine()
        results = await seed_all("dev", engine_resolver=lambda _c: eng)
        # base lost its only table -> not run; app owns roles + permissions
        assert "base" not in results
        assert results["app"].rows_inserted == 2

        maker = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with maker() as s:
            roles = (await s.execute(select(Role))).scalars().all()
            assert [r.name for r in roles] == ["AppAdmin"]
        await eng.dispose()

    @pytest.mark.asyncio
    async def test_disable_package_skips_it(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import (
            PackageSeederConfig,
            register_package_seeders,
            disable_package_seeders,
            seed_all,
        )

        path = write_package(
            tmp_path,
            tables={"roles": {"file": "roles.json"}},
            data={"roles.json": [{"id": 1, "name": "Admin"}]},
        )
        register_package_seeders(PackageSeederConfig(
            package_name="base", manifest_path=path,
            model_registry=REGISTRY, connection="auth",
        ))
        disable_package_seeders("base")

        eng = await _sqlite_engine()
        results = await seed_all("dev", engine_resolver=lambda _c: eng)
        assert results == {}
        await eng.dispose()

    @pytest.mark.asyncio
    async def test_exclude_packages_for_one_run(self, tmp_path):
        from pgsqlasync2fast_fastapi.seeders import (
            PackageSeederConfig,
            register_package_seeders,
            seed_all,
        )

        path = write_package(
            tmp_path,
            tables={"roles": {"file": "roles.json"}},
            data={"roles.json": [{"id": 1, "name": "Admin"}]},
        )
        register_package_seeders(PackageSeederConfig(
            package_name="base", manifest_path=path,
            model_registry=REGISTRY, connection="auth",
        ))
        eng = await _sqlite_engine()
        assert await seed_all("dev", engine_resolver=lambda _c: eng, exclude_packages=["base"]) == {}
        await eng.dispose()


@pytest.mark.asyncio
async def test_seed_all_adopts_explicit_ids_by_default(tmp_path):
    """seed_all migrates existing ids to the manifest literals before seeding."""
    from pgsqlasync2fast_fastapi.seeders import (
        PackageSeederConfig,
        register_package_seeders,
        seed_all,
    )

    literal = uuid7()
    path = write_package(
        tmp_path,
        tables={"roles": {"file": "roles.json", "natural_key": ["name"]}},
        data={"roles.json": [{"id": str(literal), "name": "Admin"}]},
    )
    register_package_seeders(PackageSeederConfig(
        package_name="base", manifest_path=path,
        model_registry=REGISTRY, connection="auth",
    ))

    eng = await _sqlite_engine()
    old_id = uuid7()
    maker = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        s.add(Role(id=old_id, name="Admin"))
        await s.commit()

    results = await seed_all("dev", engine_resolver=lambda _c: eng)
    assert results["base"].success

    async with maker() as s:
        roles = (await s.execute(select(Role))).scalars().all()
        assert [r.id for r in roles] == [literal]
    await eng.dispose()


def test_insert_if_missing_available_from_new_module():
    from pgsqlasync2fast_fastapi.seeders import insert_if_missing

    assert callable(insert_if_missing)
