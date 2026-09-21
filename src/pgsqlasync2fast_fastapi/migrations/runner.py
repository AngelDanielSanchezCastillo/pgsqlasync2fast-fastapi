"""
Async migration runner for package-owned Alembic chains (design D3 of the
``alembic-2fast`` change).

``run_migrations(connection_name, chains)`` upgrades each chain to ``head``
in declaration order. Key properties:

- URL resolution is CONFIG-ONLY: ``manager.config.get_connection_url(name)``.
  ``DatabaseManager._engines`` (pooled app engines) is never read or reused -
  each chain run gets a fresh ``NullPool`` async engine inside env.py that is
  disposed after the run.
- Each chain runs in a worker thread via ``asyncio.to_thread``, so env.py's
  ``asyncio.run`` owns a fresh event loop (and engine) per run - it can never
  collide with the app's lifespan loop.
- ``script_location`` is an alembic-native ``"<package>:<chain_path>"``
  resource string: Alembic resolves it from the INSTALLED release via
  ``coerce_resource_to_filename`` (importlib.resources), never from a
  checkout.
- Ownership filtering (design D9): ``include_object`` / ``include_name``
  attributes admit only tables (and their child objects) listed in
  ``owned_tables``, so autogenerate on a shared ``MetaData`` never proposes
  foreign tables.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData

from ..connection import get_manager

__all__ = [
    "ChainSpec",
    "run_migrations",
]

#: Object types whose ownership derives from their parent table (type names
#: verified against alembic 1.20.0 ``NameFilterType``).
_OWNED_PARENT_TYPES = frozenset(
    {
        "index",
        "unique_constraint",
        "foreign_key_constraint",
        "check_constraint",
    }
)


@dataclass(frozen=True)
class ChainSpec:
    """
    Description of one package-owned Alembic chain shipped inside a wheel.

    Attributes:
        package: Import name of the wheel's package, e.g. ``"oauth2fast_fastapi"``.
        script_path: Chain directory inside the package, e.g. ``"migrations/oauth"``.
        version_table: Name of the chain's version table, e.g.
            ``"alembic_version_oauth"`` (per-chain, never the default).
        target_metadata: Chain-owned ``MetaData`` for autogenerate comparison.
        owned_tables: Table names this chain owns; the autogenerate ownership
            filter admits only these (design D9).
    """

    package: str
    script_path: str
    version_table: str
    target_metadata: MetaData
    owned_tables: frozenset[str]


def _owner_filter(owned_tables: frozenset[str]) -> Callable[..., bool]:
    """
    Build an ownership filter usable for BOTH alembic include hooks (D9).

    Alembic calls ``include_object`` with
    ``(object_, name, type_, reflected, compare_to)`` and ``include_name``
    with ``(name, type_, parent_names)``; the returned callable dispatches on
    the argument count:

    - ``type_ == "table"`` -> the table's own name must be owned.
    - index / unique / FK / check constraint -> the object's *parent table*
      must be owned (no cross-lane objects are proposed).
    - anything else -> admitted.

    Args:
        owned_tables: Table names owned by the chain.

    Returns:
        A callable assigning to ``cfg.attributes["include_object"]`` and
        ``cfg.attributes["include_name"]``.
    """

    def _filter(*args: Any) -> bool:
        if len(args) == 3:  # include_name(name, type_, parent_names)
            name, type_, parent_names = args
            parent_table = None
        else:  # include_object(object_, name, type_, reflected, compare_to)
            object_, name, type_, _, _ = args
            parent_table = getattr(object_, "table", None)

        if type_ == "table":
            return name in owned_tables
        if type_ in _OWNED_PARENT_TYPES:
            if parent_table is not None:
                return parent_table.name in owned_tables
            return parent_names.get("table_name") in owned_tables
        return True

    return _filter


def _chain_config(chain: ChainSpec, url: str) -> Config:
    """
    Build the programmatic Alembic ``Config`` for one chain run (no ini file).

    Args:
        chain: The chain spec to configure.
        url: The connection URL (already resolved from manager config).

    Returns:
        A ``Config`` whose main options and attributes drive the shared env.
    """
    cfg = Config()
    cfg.set_main_option(
        "script_location", f"{chain.package}:{chain.script_path}"
    )
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.set_main_option("version_table", chain.version_table)
    cfg.attributes["target_metadata"] = chain.target_metadata
    cfg.attributes["include_object"] = _owner_filter(chain.owned_tables)
    cfg.attributes["include_name"] = _owner_filter(chain.owned_tables)
    return cfg


async def run_migrations(
    connection_name: str, chains: Sequence[ChainSpec]
) -> None:
    """
    Upgrade every chain to head for a named connection, in declared order.

    Args:
        connection_name: Named connection from the manager config (e.g.
            ``"auth"``, ``"tenant_1"``).
        chains: Chain specs to apply, in FK-proven order.

    Raises:
        ValueError: If the connection name is not configured.
        Any exception raised by Alembic's ``command.upgrade`` (fail-fast per
        chain; the caller decides lane error policy).
    """
    manager = get_manager()
    # Config only - pooled app engines under manager._engines are never used.
    url = manager.config.get_connection_url(connection_name)

    for chain in chains:
        await asyncio.to_thread(
            command.upgrade, _chain_config(chain, url), "head"
        )