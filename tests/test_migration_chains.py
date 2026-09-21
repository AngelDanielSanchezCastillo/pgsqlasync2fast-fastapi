"""
Tests for the shared Alembic migration infrastructure (pgsqlasync2fast-fastapi).

This module verifies the runner + chain registry shipped by this package for
the 2fast-family migration overhaul (Metal ERP change ``alembic-2fast``):

1. ``ChainSpec`` — frozen dataclass describing an installed wheel's chain.
2. ``register_chain`` / ``get_lane_chains`` / ``clear_chain_registry`` —
   lane-keyed chain registry (same idiom as ``register_seeder``).
3. ``run_migrations(connection_name, chains)`` — per-run async engine via
   ``async_engine_from_config`` + ``NullPool`` + ``dispose()`` in the shared
   env; the URL comes from ``manager.config`` only and ``DatabaseManager._engines``
   is NEVER touched.
4. Shared ``migrations/env.py`` — resolves ``context.config`` lazily per run
   (the module is imported once per process while alembic re-executes each
   chain's env.py shim), reads ``version_table`` via
   ``config.get_main_option("version_table")`` and passes it EXPLICITLY to
   ``context.configure(version_table=...)``.

No PostgreSQL is required: ``command.upgrade`` is mocked and the env contract
is asserted at the ``context.configure`` level.

Run with:
  uv run pytest tests/test_migration_chains.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alembic.config import Config
from contextlib import nullcontext
from pydantic import SecretStr
from sqlalchemy import MetaData, pool

from pgsqlasync2fast_fastapi.connection import DatabaseManager
from pgsqlasync2fast_fastapi.migrations import (
    ChainSpec,
    clear_chain_registry,
    get_lane_chains,
    register_chain,
    run_migrations,
)
from pgsqlasync2fast_fastapi.settings import DatabaseConnectionSettings, DatabaseSettings


@pytest.fixture(autouse=True)
def clear_chains():
    """Clear the chain registry before and after each test."""
    clear_chain_registry()
    yield
    clear_chain_registry()


def _chain(
    package: str = "oauth2fast_fastapi",
    script_path: str = "migrations/oauth",
    version_table: str = "alembic_version_oauth",
    owned: tuple[str, ...] = ("users", "roles"),
) -> ChainSpec:
    return ChainSpec(
        package=package,
        script_path=script_path,
        version_table=version_table,
        target_metadata=MetaData(),
        owned_tables=frozenset(owned),
    )


def _manager_with_auth() -> DatabaseManager:
    """Database manager whose config holds a single 'auth' connection."""
    config = DatabaseSettings(
        connections={
            "auth": DatabaseConnectionSettings(
                host="localhost",
                port=5433,
                username="mig_user",
                password=SecretStr("mig_pass"),
                database="authdb",
            )
        }
    )
    return DatabaseManager(config)


# ============================================================================
# Registry: register_chain / get_lane_chains / clear_chain_registry
# ============================================================================


def test_register_and_get_lane_chains_preserve_registration_order():
    oauth = _chain()
    permissions = _chain(
        package="permissions2fast_fastapi",
        script_path="migrations/permissions-global",
        version_table="alembic_version_permissions",
    )

    register_chain("auth", oauth)
    register_chain("auth", permissions)

    assert get_lane_chains("auth") == [oauth, permissions]
    assert get_lane_chains("tenant") == []


def test_register_chain_same_key_updates_in_place():
    register_chain("auth", _chain(version_table="alembic_version_oauth"))

    updated = _chain(version_table="alembic_version_oauth_v2")
    register_chain("auth", updated)

    chains = get_lane_chains("auth")
    assert chains == [updated]  # same position, same key, not duplicated


def test_get_lane_chains_returns_defensive_copy():
    register_chain("auth", _chain())

    chains = get_lane_chains("auth")
    chains.clear()

    assert len(get_lane_chains("auth")) == 1


def test_clear_chain_registry_empties_all_lanes():
    register_chain("auth", _chain())
    register_chain("tenant", _chain(version_table="alembic_version_tenant_auth"))

    clear_chain_registry()

    assert get_lane_chains("auth") == []
    assert get_lane_chains("tenant") == []


def test_chain_spec_is_frozen():
    chain = _chain()
    with pytest.raises(Exception):
        chain.version_table = "alembic_version_other"


# ============================================================================
# _owner_filter: include_object / include_name ownership semantics
# ============================================================================


def test_owner_filter_admits_only_owned_tables():
    from pgsqlasync2fast_fastapi.migrations.runner import _owner_filter

    owned = frozenset({"users"})
    owner = _owner_filter(owned)

    users = MagicMock()
    users.name = "users"
    roles = MagicMock()
    roles.name = "roles"

    # include_object(object_, name, type_, reflected, compare_to)
    assert owner(users, "users", "table", False, None) is True
    assert owner(roles, "roles", "table", False, None) is False


def test_owner_filter_constraints_follow_owning_table():
    from pgsqlasync2fast_fastapi.migrations.runner import _owner_filter

    owner = _owner_filter(frozenset({"users"}))

    owned_idx = MagicMock()
    owned_idx.table = MagicMock()
    owned_idx.table.name = "users"
    foreign_idx = MagicMock()
    foreign_idx.table = MagicMock()
    foreign_idx.table.name = "roles"

    # include_object on index/constraint types dispatches by parent table
    assert owner(owned_idx, "ix_users_a", "index", False, None) is True
    assert owner(foreign_idx, "ix_roles_a", "index", False, None) is False


def test_owner_filter_include_name_reflected_signature():
    from pgsqlasync2fast_fastapi.migrations.runner import _owner_filter

    owner = _owner_filter(frozenset({"users"}))

    # include_name(name, type_, parent_names)
    assert owner("users", "table", {}) is True
    assert owner("roles", "table", {}) is False
    assert owner("ix_users_a", "index", {"table_name": "users"}) is True
    assert owner("ix_roles_a", "index", {"table_name": "roles"}) is False


# ============================================================================
# _chain_config: programmatic alembic Config per chain
# ============================================================================


def test_chain_config_sets_script_location_url_and_version_table():
    from pgsqlasync2fast_fastapi.migrations.runner import _chain_config

    cfg = _chain_config(_chain(), "postgresql+asyncpg://u:p@h:5432/authdb")

    assert cfg.get_main_option("script_location") == "oauth2fast_fastapi:migrations/oauth"
    assert cfg.get_main_option("sqlalchemy.url") == "postgresql+asyncpg://u:p@h:5432/authdb"
    assert cfg.get_main_option("version_table") == "alembic_version_oauth"
    assert cfg.attributes["target_metadata"] is not None
    assert callable(cfg.attributes["include_object"])
    assert callable(cfg.attributes["include_name"])


# ============================================================================
# run_migrations: config-only URL, per-chain async upgrade, no pooled engines
# ============================================================================


@pytest.mark.asyncio
async def test_run_migrations_applies_chains_in_order_with_config_url():
    manager = _manager_with_auth()
    oauth = _chain()
    permissions = _chain(
        package="permissions2fast_fastapi",
        script_path="migrations/permissions-global",
        version_table="alembic_version_permissions",
    )

    with (
        patch("pgsqlasync2fast_fastapi.migrations.runner.get_manager", return_value=manager),
        patch("pgsqlasync2fast_fastapi.migrations.runner.command.upgrade") as mock_upgrade,
    ):
        await run_migrations("auth", [oauth, permissions])

    assert mock_upgrade.call_count == 2
    first_cfg, first_rev = mock_upgrade.call_args_list[0].args
    second_cfg, second_rev = mock_upgrade.call_args_list[1].args

    assert first_rev == "head"
    assert second_rev == "head"
    assert first_cfg.get_main_option("script_location") == "oauth2fast_fastapi:migrations/oauth"
    assert second_cfg.get_main_option("script_location") == "permissions2fast_fastapi:migrations/permissions-global"
    assert first_cfg.get_main_option("version_table") == "alembic_version_oauth"
    assert second_cfg.get_main_option("version_table") == "alembic_version_permissions"

    expected_url = "postgresql+asyncpg://mig_user:mig_pass@localhost:5433/authdb"
    assert first_cfg.get_main_option("sqlalchemy.url") == expected_url
    assert second_cfg.get_main_option("sqlalchemy.url") == expected_url


@pytest.mark.asyncio
async def test_run_migrations_never_touches_pooled_engines():
    manager = _manager_with_auth()

    with (
        patch("pgsqlasync2fast_fastapi.migrations.runner.get_manager", return_value=manager),
        patch("pgsqlasync2fast_fastapi.migrations.runner.command.upgrade"),
    ):
        await run_migrations("auth", [_chain()])

    # The pooled engine registry must remain untouched: app traffic keeps its
    # engines, migrations get a fresh NullPool engine created/disposed per run.
    assert manager._engines == {}
    assert manager._session_makers == {}


# ============================================================================
# Shared env: version_table pass-through, NullPool, dispose per run, lazy config
# ============================================================================


def test_env_do_run_migrations_passes_version_table_explicitly(monkeypatch):
    import pgsqlasync2fast_fastapi.migrations.env as env_mod

    cfg = Config()
    cfg.set_main_option("version_table", "alembic_version_oauth")
    cfg.attributes["target_metadata"] = MetaData()
    include_object = MagicMock(return_value=True)
    include_name = MagicMock(return_value=True)
    cfg.attributes["include_object"] = include_object
    cfg.attributes["include_name"] = include_name

    captured: dict = {}

    class FakeContext:
        config = cfg

        def configure(self, **kwargs):
            captured.update(kwargs)

        def begin_transaction(self):
            return nullcontext()

        def run_migrations(self):
            return None

    monkeypatch.setattr(env_mod, "context", FakeContext())

    env_mod.do_run_migrations(connection=MagicMock())

    # version_table is passed EXPLICITLY — alembic 1.20.0 does not transport
    # the main option into MigrationContext on its own (design D1).
    assert captured["version_table"] == "alembic_version_oauth"
    assert captured["target_metadata"] is cfg.attributes["target_metadata"]
    assert captured["include_object"] is include_object
    assert captured["include_name"] is include_name
    assert captured["connection"] is not None


def test_env_offline_configure_uses_url_and_version_table(monkeypatch):
    import pgsqlasync2fast_fastapi.migrations.env as env_mod

    cfg = Config()
    cfg.set_main_option("sqlalchemy.url", "postgresql+asyncpg://u:p@h:5432/authdb")
    cfg.set_main_option("version_table", "alembic_version_oauth")

    captured: dict = {}

    class FakeContext:
        config = cfg

        def configure(self, **kwargs):
            captured.update(kwargs)

        def begin_transaction(self):
            return nullcontext()

        def run_migrations(self):
            return None

    monkeypatch.setattr(env_mod, "context", FakeContext())

    env_mod.run_migrations_offline()

    assert captured["url"] == "postgresql+asyncpg://u:p@h:5432/authdb"
    assert captured["version_table"] == "alembic_version_oauth"
    assert captured["literal_binds"] is True


@pytest.mark.asyncio
async def test_env_async_uses_nullpool_and_disposes_per_run(monkeypatch):
    import pgsqlasync2fast_fastapi.migrations.env as env_mod

    cfg = Config()
    cfg.set_main_option("sqlalchemy.url", "postgresql+asyncpg://u:p@h:5432/authdb")

    connection = MagicMock()
    connection.run_sync = AsyncMock()
    engine = MagicMock()
    # connect() is a SYNC call returning an async context manager, whose
    # __aenter__ yields the connection used by run_sync.
    engine.connect = MagicMock()
    engine.connect.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
    engine.dispose = AsyncMock()

    seen: dict = {}

    def fake_async_engine_from_config(section, prefix, poolclass):
        seen["prefix"] = prefix
        seen["poolclass"] = poolclass
        return engine

    monkeypatch.setattr(env_mod, "async_engine_from_config", fake_async_engine_from_config)
    monkeypatch.setattr(env_mod, "context", SimpleNamespace(config=cfg))

    await env_mod.run_async_migrations()

    assert seen["prefix"] == "sqlalchemy."
    assert seen["poolclass"] is pool.NullPool
    connection.run_sync.assert_awaited_once()
    engine.dispose.assert_awaited_once()


def test_shared_env_resolves_config_lazily_per_run(monkeypatch):
    """The shared env module is imported once but each chain run must see its
    own Config — funcions resolve ``context.config`` at call time."""
    import pgsqlasync2fast_fastapi.migrations.env as env_mod

    captured: list[dict] = []

    class FakeContext:
        def __init__(self, config):
            self.config = config

        def configure(self, **kwargs):
            captured.append(kwargs)

        def begin_transaction(self):
            return nullcontext()

        def run_migrations(self):
            return None

    oauth_cfg = Config()
    oauth_cfg.set_main_option("sqlalchemy.url", "postgresql+asyncpg://u:p@h:5432/authdb")
    oauth_cfg.set_main_option("version_table", "alembic_version_oauth")

    app_cfg = Config()
    app_cfg.set_main_option("sqlalchemy.url", "postgresql+asyncpg://u:p@h:5432/appdb")
    app_cfg.set_main_option("version_table", "alembic_version_app")

    monkeypatch.setattr(env_mod, "context", FakeContext(oauth_cfg))
    env_mod.run_migrations_offline()

    # Simulate the next chain's run: alembic re-executes the shim with a new
    # Config; the shared module must NOT be bound to the first run's config.
    monkeypatch.setattr(env_mod, "context", FakeContext(app_cfg))
    env_mod.run_migrations_offline()

    assert captured[0]["version_table"] == "alembic_version_oauth"
    assert captured[0]["url"] == "postgresql+asyncpg://u:p@h:5432/authdb"
    assert captured[1]["version_table"] == "alembic_version_app"
    assert captured[1]["url"] == "postgresql+asyncpg://u:p@h:5432/appdb"