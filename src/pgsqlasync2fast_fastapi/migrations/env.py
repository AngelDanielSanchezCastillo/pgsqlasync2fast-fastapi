"""
Shared Alembic async env implementation (design D1 of the ``alembic-2fast``
change).

Consumer packages ship a ~12-line ``env.py`` shim inside each chain directory
that imports ``run_migrations_offline`` / ``run_migrations_online`` from this
module and dispatches on ``context.is_offline_mode()``. Alembic's
``env_py_location`` is fixed at ``<script_location>/env.py``, so the shared
logic lives here and the per-chain shims stay thin.

Configuration contract (set programmatically by ``runner._chain_config``):

- main option ``script_location`` - ``"<package>:<chain_path>"`` (installed
  release resolution via ``coerce_resource_to_filename``).
- main option ``sqlalchemy.url`` - the connection URL (config only).
- main option ``version_table`` - the chain's version table. Alembic 1.20.0
  does NOT transport a ``version_table`` main option into ``MigrationContext``
  (it is read from configure ``opts`` only), so it is passed EXPLICITLY to
  ``context.configure(version_table=...)`` below (design D1).
- attribute ``target_metadata`` - chain-owned ``MetaData`` for autogenerate.
- attributes ``include_object`` / ``include_name`` - ownership filters from
  ``runner._owner_filter`` (design D9).

Async pattern (official recipe): a fresh engine per run via
``async_engine_from_config`` with ``NullPool``, disposed after the run. The
runner executes each chain via ``asyncio.to_thread(command.upgrade, ...)``, so
every run owns its own event loop in its own worker thread.

IMPORTANT (shared-module reuse): this module is imported ONCE per process
while Alembic re-executes each chain's ``env.py`` shim on every run. Nothing
here may bind ``context.config`` at module level - every function resolves
``context.config`` lazily at call time so each run sees its own ``Config``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

if TYPE_CHECKING:
    from alembic.config import Config

__all__ = [
    "do_run_migrations",
    "run_async_migrations",
    "run_migrations_offline",
    "run_migrations_online",
]


def _shared_configure_kwargs(config: "Config") -> dict[str, Any]:
    """Options shared by the offline and online ``configure`` calls."""
    kwargs: dict[str, Any] = {
        "target_metadata": config.attributes.get("target_metadata"),
        "include_object": config.attributes.get("include_object"),
        "include_name": config.attributes.get("include_name"),
    }
    version_table = config.get_main_option("version_table")
    if version_table:
        # Deliberate explicit pass-through: alembic 1.20.0 reads version_table
        # from the MigrationContext opts only, never from main options.
        kwargs["version_table"] = version_table
    return kwargs


def do_run_migrations(connection: Connection) -> None:
    """Configure ``context`` for an online run against ``connection``."""
    config = context.config
    context.configure(
        connection=connection,
        **_shared_configure_kwargs(config),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Render the chain's SQL without a database connection (offline mode)."""
    config = context.config
    kwargs = _shared_configure_kwargs(config)
    kwargs.update(
        {
            "url": config.get_main_option("sqlalchemy.url"),
            "literal_binds": True,
            "dialect_opts": {"paramstyle": "named"},
        }
    )
    context.configure(**kwargs)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Fresh NullPool async engine, upgraded, disposed - all per run."""
    config = context.config
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point called by chain shims in online mode.

    Runs in the worker thread that ``runner.run_migrations`` spawned via
    ``asyncio.to_thread``, so no event loop is running here and
    ``asyncio.run`` owns a fresh loop (and therefore the engine) per run.
    """
    asyncio.run(run_async_migrations())