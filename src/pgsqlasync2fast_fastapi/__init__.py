"""
pgsqlasync2fast-fastapi - PostgreSQL async extensions for FastAPI

This package provides database management and seeding capabilities.

The seeder module provides multi-package JSON seeder orchestration.

Example:
    from pgsqlasync2fast_fastapi import seed_all, register_seeder, SeederConfig

    # Register a package's seeder
    register_seeder(SeederConfig(
        connection_name="auth",
        manifest_path="path/to/manifest.json",
        package_name="my-package",
        priority=50
    ))

    # Execute all seeders
    result = await seed_all("dev")
"""

from .__version__ import __version__
from .connection import DatabaseManager, get_manager
from .database import create_database, database_exists, drop_database, list_databases
from .dependencies import (
    get_db_engine,
    get_db_manager,
    get_db_session,
    shutdown_database,
    startup_database,
)
from .settings import DatabaseConnectionSettings, DatabaseSettings, settings

# Import seeder module
from pgsqlasync2fast_fastapi import seeder

# Re-export seeder-specific exports
from pgsqlasync2fast_fastapi.seeder import (
    # Dataclasses
    SeederConfig,
    SeederResult,
    # Exceptions
    SeederException,
    SeederConflictError,
    SeedValidationError,
    # Registry functions
    register_seeder,
    get_registered_seeders,
    clear_registry,
    # Main orchestrator
    seed_all,
    # Shared idempotent insert-if-missing primitive
    insert_if_missing,
    # Shared PostgreSQL sequence re-sync primitive
    sync_table_sequence,
)

# Re-export migration-specific exports
from pgsqlasync2fast_fastapi.migrations import (
    # Chain spec + runner
    ChainSpec,
    run_migrations,
    # Lane-keyed chain registry
    register_chain,
    get_lane_chains,
    clear_chain_registry,
)

# Re-export migration helpers (DEPRECATED - for v0.x → v1.0 migrations only)
from pgsqlasync2fast_fastapi.migration_helpers import (
    migrate_pk_to_uuid,
    migrate_fk_to_uuid,
    backfill_uuids,
)

__all__ = [
    # Version
    "__version__",
    # Main classes
    "DatabaseManager",
    "get_manager",
    # Settings
    "DatabaseSettings",
    "DatabaseConnectionSettings",
    "settings",
    # FastAPI dependencies
    "get_db_manager",
    "get_db_engine",
    "get_db_session",
    "startup_database",
    "shutdown_database",
    # Database utilities
    "database_exists",
    "create_database",
    "drop_database",
    "list_databases",
    # Dataclasses (seeder)
    "SeederConfig",
    "SeederResult",
    # Exceptions (seeder)
    "SeederException",
    "SeederConflictError",
    "SeedValidationError",
    # Registry functions (seeder)
    "register_seeder",
    "get_registered_seeders",
    "clear_registry",
    # Main orchestrator (seeder)
    "seed_all",
    # Shared idempotent insert-if-missing primitive
    "insert_if_missing",
    # Shared PostgreSQL sequence re-sync primitive
    "sync_table_sequence",
    # Migration infrastructure (alembic-2fast)
    "ChainSpec",
    "run_migrations",
    "register_chain",
    "get_lane_chains",
    "clear_chain_registry",
    # Migration helpers (DEPRECATED)
    "migrate_pk_to_uuid",
    "migrate_fk_to_uuid",
    "backfill_uuids",
]
