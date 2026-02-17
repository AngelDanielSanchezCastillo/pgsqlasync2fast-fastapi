"""
pgsqlasync2fast-fastapi - Simple and fast PostgreSQL async module for FastAPI

A comprehensive PostgreSQL async module for FastAPI with multi-database support,
automatic database creation, and Pydantic settings configuration.

Features:
- Multiple database connection support
- Async database operations with SQLAlchemy
- Database creation utilities with superuser support
- Connection pooling and health checks
- FastAPI integration with dependencies
- Lazy engine creation for optimal resource usage
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
]
