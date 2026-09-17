"""
pgsqlasync2fast_fastapi.migrations - shared Alembic infrastructure for the
2fast family.

This package ships the shared pieces of the ``alembic-2fast`` migration
overhaul (Metal ERP change):

- ``env.py`` - shared async env implementation (official Alembic async recipe:
  ``async_engine_from_config`` + ``NullPool`` + ``dispose()`` per run). Each
  consumer chain dir ships a thin ``env.py`` shim that imports
  ``run_migrations_offline`` / ``run_migrations_online`` from here.
- ``registry.py`` - lane-keyed chain registry (``register_chain`` /
  ``get_lane_chains``), same idiom as ``register_seeder``.
- ``runner.py`` - ``ChainSpec`` and ``run_migrations(connection_name, chains)``:
  reads the URL from manager config only (never ``DatabaseManager._engines``)
  and upgrades each chain to head in declaration order.

Consumers resolve chains from the INSTALLED release via alembic-native
``pkg:path`` resource strings (``coerce_resource_to_filename``), never from a
repository checkout.
"""

from .env import (
    do_run_migrations,
    run_async_migrations,
    run_migrations_offline,
    run_migrations_online,
)
from .registry import (
    clear_chain_registry,
    get_lane_chains,
    register_chain,
)
from .runner import ChainSpec, run_migrations

__all__ = [
    # env impl
    "do_run_migrations",
    "run_async_migrations",
    "run_migrations_offline",
    "run_migrations_online",
    # registry
    "clear_chain_registry",
    "get_lane_chains",
    "register_chain",
    # runner
    "ChainSpec",
    "run_migrations",
]