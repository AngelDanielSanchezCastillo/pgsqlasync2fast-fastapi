"""
pgsqlasync2fast_fastapi - Seeder Module

Multi-package JSON seeder orchestrator that executes seeders from multiple packages
following the standard seeder format with manifest.json per package.

This module provides the foundation for the seeder standard, including:
- SeederConfig: Configuration dataclass for package seeders
- register_seeder(): Register a package's seeder with conflict detection
- seed_all(): Execute all registered seeders in dependency order
- Idempotent seeding: rows are skipped if they already exist by ID

Example usage:
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

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


# ============================================================================
# Exceptions
# ============================================================================


class SeederException(Exception):
    """Base exception for seeder errors."""
    pass


class SeederConflictError(SeederException):
    """
    Raised when two packages define overlapping tables in the same connection.

    This prevents silent data corruption where two packages might try to seed
    the same table with different data.

    Example:
        Package A and Package B both try to seed the "roles" table.
        This would cause unpredictable behavior - which data wins?
        SeederConflictError is raised to prevent this.
    """
    pass


class SeedValidationError(SeederException):
    """
    Raised when FK references point to non-existent IDs or data is invalid.

    This ensures data integrity before attempting any inserts.

    Example:
        A permission row references category_id=999, but no category
        with id=999 exists in the data. This is caught before any inserts.
    """
    pass


# ============================================================================
# Dataclasses
# ============================================================================


@dataclass
class SeederConfig:
    """
    Configuration for a package's seeder.

    Attributes:
        connection_name: Name of the database connection to use
        manifest_path: Path to the package's manifest.json
        is_tenant_seeder: If True, this seeder seeds individual tenants
        priority: Lower values seed first (default 50)
        package_name: Name for conflict error messages and filtering
        seed_fn: Async function that executes seeding (for complex seeders like tenants)
        model_classes: Dict mapping table_name -> SQLModel class for inserts
        fk_field_mapping: Optional FK field name mapping for JSON-to-model conversion
                         e.g., {"permissions": {"category_id": "permission_category_id"}}
    """
    connection_name: str
    manifest_path: str
    is_tenant_seeder: bool = False
    priority: int = 50
    package_name: str = ""
    seed_fn: Any = None  # Async function: async def seed(profile) -> dict
    model_classes: dict[str, type] = field(default_factory=dict)  # table_name -> Model class
    fk_field_mapping: dict[str, dict[str, str]] = field(default_factory=dict)  # table -> {json_field: model_field}


@dataclass
class SeederResult:
    """
    Result of a seed_all operation.

    Attributes:
        seeded_packages: List of package names that were seeded
        tables_seeded: Total number of tables processed
        rows_seeded: Total number of rows inserted
        errors: List of error messages (empty if all succeeded)
        skipped: List of tables that were skipped (already existed)
    """
    seeded_packages: list[str] = field(default_factory=list)
    tables_seeded: int = 0
    rows_seeded: int = 0
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# ============================================================================
# Registry
# ============================================================================


_SEEDER_REGISTRY: list[SeederConfig] = []


def register_seeder(config: SeederConfig) -> None:
    """
    Register a package's seeder configuration.

    Validates that there are no table conflicts between packages using
    the same connection name. Two packages cannot seed the same table
    in the same connection.

    The validation happens at registration time (not execution time) to fail
    fast and provide clear error messages.

    Args:
        config: SeederConfig with connection_name, manifest_path, and priority

    Raises:
        SeederConflictError: If tables overlap with an already-registered seeder

    Example:
        register_seeder(SeederConfig(
            connection_name="auth",
            manifest_path="permissions2fast_fastapi/seeders/manifest.json",
            package_name="permissions2fast-fastapi",
            priority=60
        ))
    """
    # Load manifests to check for conflicts
    for existing in _SEEDER_REGISTRY:
        if existing.connection_name != config.connection_name:
            continue

        # Same connection - check for table overlap
        tables_a = set(_load_manifest(existing.manifest_path)["tables"].keys())
        tables_b = set(_load_manifest(config.manifest_path)["tables"].keys())
        overlap = tables_a & tables_b

        if overlap:
            pkg_a = existing.package_name or "unknown"
            pkg_b = config.package_name or "unknown"
            raise SeederConflictError(
                f"Tables {overlap} conflict between "
                f"'{pkg_a}' and '{pkg_b}' "
                f"on connection '{config.connection_name}'"
            )

    _SEEDER_REGISTRY.append(config)
    logger.debug(f"Registered seeder: {config.package_name or config.connection_name} "
                 f"(priority={config.priority}, is_tenant={config.is_tenant_seeder})")


def get_registered_seeders() -> list[SeederConfig]:
    """
    Return a copy of the seeder registry.

    Returns:
        List of all registered SeederConfig objects
    """
    return list(_SEEDER_REGISTRY)


def clear_registry() -> None:
    """Clear all registered seeders. Useful for testing."""
    global _SEEDER_REGISTRY
    _SEEDER_REGISTRY = []


# ============================================================================
# Manifest and Data Loading
# ============================================================================


def _load_manifest(manifest_path: str) -> dict[str, Any]:
    """
    Load and parse a manifest.json file.

    The manifest is the source of truth for:
    - Which tables this package seeds
    - Which JSON file contains each table's data
    - Dependencies between tables (for load order)

    Args:
        manifest_path: Path to manifest.json

    Returns:
        Parsed manifest dictionary with 'tables' and optional 'load_order' keys

    Raises:
        FileNotFoundError: If manifest doesn't exist
        json.JSONDecodeError: If manifest is invalid JSON

    Example manifest.json:
        {
            "tables": {
                "categories": {"file": "categories.json"},
                "roles": {"file": "roles.json"},
                "permissions": {"file": "permissions.json", "depends_on": ["categories"]}
            },
            "load_order": ["categories", "roles", "permissions"]
        }
    """
    path = Path(manifest_path)
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_table_data(table_name: str, manifest_path: str, profile: str) -> list[dict[str, Any]]:
    """
    Load table data from a JSON file in the profile folder.

    Each table has its own JSON file containing rows with explicit IDs.
    The file is looked up in: {manifest_dir}/{profile}/{file}

    Args:
        table_name: Name of the table (used to find the file in manifest)
        manifest_path: Path to the package's manifest.json
        profile: Profile folder name (e.g., "dev", "prod")

    Returns:
        List of row dictionaries with explicit IDs

    Raises:
        FileNotFoundError: If the JSON file doesn't exist
        json.JSONDecodeError: If JSON is invalid
        SeedValidationError: If data is not a list

    Example roles.json:
        [
            {"id": 1, "name": "Admin", "description": "Administrator role"},
            {"id": 2, "name": "User", "description": "Regular user role"}
        ]
    """
    manifest = _load_manifest(manifest_path)
    table_config = manifest["tables"].get(table_name, {})
    file_name = table_config.get("file", f"{table_name}.json")

    # Find the JSON file in the profile folder
    manifest_dir = Path(manifest_path).parent
    json_path = manifest_dir / profile / file_name

    if not json_path.exists():
        logger.warning(
            f"Seed data not found for table '{table_name}' "
            f"in profile '{profile}': {json_path}"
        )
        return []

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise SeedValidationError(
            f"Table '{table_name}' data must be a list, got {type(data).__name__}"
        )

    return data


# Default FK field naming convention: singularize table name + "_id"
# e.g., "categories" -> "category_id", "roles" -> "role_id"
_DEFAULT_FK_MAPPING: dict[str, str] = {
    "categories": "category_id",
    "roles": "role_id",
    "permissions": "permission_id",
    "routes": "route_id",
}

# Package can override FK field mapping via manifest
# manifest can have: "fk_fields": {"categories": "category_id", ...}
_FK_FIELD_MAPPING: dict[str, dict[str, str]] = {}


def _get_fk_field(table_name: str, manifest: dict[str, Any] | None = None) -> str:
    """
    Get the FK field name for a given table.

    The FK field is looked up in this order:
    1. Package-level override in manifest's fk_fields
    2. _FK_FIELD_MAPPING (if set by package)
    3. Default convention: table_name.rstrip('s') + "_id"

    This allows packages to override the default naming for:
    - Irregular plurals (e.g., "categories" -> "category_id")
    - Non-English table names
    - Explicit foreign key naming conventions

    Args:
        table_name: Plural name of the table (e.g., "categories")
        manifest: Optional manifest dict to check for fk_fields override

    Returns:
        FK field name (e.g., "category_id")
    """
    # Check manifest first
    if manifest:
        fk_fields = manifest.get("fk_fields", {})
        if table_name in fk_fields:
            return fk_fields[table_name]

    # Check global mapping
    if table_name in _FK_FIELD_MAPPING:
        return _FK_FIELD_MAPPING[table_name]

    # Check default mapping
    if table_name in _DEFAULT_FK_MAPPING:
        return _DEFAULT_FK_MAPPING[table_name]

    # Fallback to simple convention
    return f"{table_name.rstrip('s')}_id"


# ============================================================================
# Validation
# ============================================================================


def _validate_fk_references(
    table_name: str,
    rows: list[dict[str, Any]],
    manifest_path: str,
    profile: str,
    loaded_tables: dict[str, list[dict[str, Any]]]
) -> None:
    """
    Validate that all FK references in rows point to existing IDs.

    This is called BEFORE any inserts to prevent partial data or
    FK constraint violations.

    The FK resolution uses configurable naming convention:
    - Check manifest's fk_fields override first
    - Then _DEFAULT_FK_MAPPING (e.g., "categories" -> "category_id")
    - Finally fallback to simple rstrip('s') + "_id"

    Args:
        table_name: Name of the table being validated
        rows: List of row dictionaries
        manifest_path: Path to manifest.json
        profile: Profile folder
        loaded_tables: Dict of table_name -> list of loaded rows

    Raises:
        SeedValidationError: If a referenced ID doesn't exist

    Example:
        If permissions.json has {"name": "read", "category_id": 1}
        and categories.json doesn't have any row with id=1,
        this raises SeedValidationError.
    """
    manifest = _load_manifest(manifest_path)
    table_config = manifest["tables"].get(table_name, {})
    depends_on = table_config.get("depends_on", [])

    for row in rows:
        row_id = row.get("id")
        if row_id is None:
            raise SeedValidationError(
                f"Table '{table_name}' row missing 'id' field: {row}"
            )

        # Check each dependency
        for dep_table in depends_on:
            # Get FK field name using configurable mapping
            fk_field = _get_fk_field(dep_table, manifest)
            fk_value = row.get(fk_field)

            if fk_value is not None:
                # Find the dependency table's data
                dep_data = loaded_tables.get(dep_table, [])
                dep_ids = {dep_row.get("id") for dep_row in dep_data}

                if fk_value not in dep_ids:
                    raise SeedValidationError(
                        f"Table '{table_name}' row id={row_id} references "
                        f"non-existent {dep_table} id={fk_value} "
                        f"(FK field: {fk_field})"
                    )


# ============================================================================
# Topological Sort for Load Order
# ============================================================================


def _resolve_load_order(manifest: dict[str, Any]) -> list[str]:
    """
    Resolve the correct load order using topological sort.

    Tables are sorted by:
    1. Explicit depends_on relationships (tables dependencies first)
    2. If no dependencies, use load_order from manifest
    3. If no load_order either, use table name alphabetical

    Uses Kahn's algorithm for topological sorting with cycle detection.

    Args:
        manifest: Parsed manifest dictionary with 'tables' key

    Returns:
        List of table names in correct load order

    Raises:
        SeedValidationError: If circular dependencies are detected

    Example:
        If permissions depends_on categories, categories appears before permissions
        in the returned list.
    """
    tables = manifest.get("tables", {})
    explicit_order = manifest.get("load_order", [])

    # Build adjacency list for dependency graph
    # graph[table] = set of tables that table depends on
    graph: dict[str, set[str]] = {}
    in_degree: dict[str, int] = {}

    for table_name in tables:
        table_config = tables[table_name]
        deps = table_config.get("depends_on", [])
        graph[table_name] = set(deps)
        in_degree[table_name] = 0

    # Calculate in-degrees (how many tables depend on each table)
    for table_name in tables:
        for dep in graph[table_name]:
            if dep in in_degree:
                in_degree[table_name] += 1

    # Kahn's algorithm for topological sort
    # Start with nodes that have no dependencies
    queue = [t for t in tables if in_degree[t] == 0]
    sorted_tables = []

    while queue:
        # Sort queue to ensure deterministic order (alphabetical by name)
        queue.sort()
        current = queue.pop(0)
        sorted_tables.append(current)

        # Reduce in-degree for all tables that depend on current
        for table_name in tables:
            if current in graph[table_name]:
                in_degree[table_name] -= 1
                if in_degree[table_name] == 0:
                    queue.append(table_name)

    # Check for circular dependencies
    if len(sorted_tables) != len(tables):
        remaining = set(tables.keys()) - set(sorted_tables)
        raise SeedValidationError(
            f"Circular dependency detected involving tables: {remaining}"
        )

    # If explicit order exists, merge it with dependency order
    # Tables not in explicit_order get appended at the end
    if explicit_order:
        final_order = []
        for table in explicit_order:
            if table in tables and table not in final_order:
                final_order.append(table)
        for table in sorted_tables:
            if table not in final_order:
                final_order.append(table)
        return final_order

    return sorted_tables


# ============================================================================
# Core Seeding Logic
# ============================================================================


async def _seed_table_idempotent(
    session: Any,
    table_name: str,
    rows: list[dict[str, Any]],
    model_class: type | None = None
) -> tuple[int, int]:
    """
    Seed a table idempotently by checking if rows exist by ID.

    This function is idempotent - running it multiple times with the same
    data produces the same result (no duplicates).

    Args:
        session: SQLModel AsyncSession
        table_name: Name of the table (for logging)
        rows: List of row dictionaries with explicit IDs
        model_class: SQLModel class for the table (optional for custom handling)

    Returns:
        Tuple of (rows_inserted, rows_skipped)
    """
    from sqlmodel import select

    rows_inserted = 0
    rows_skipped = 0

    for row in rows:
        row_id = row.get("id")
        if row_id is None:
            logger.warning(f"Skipping row without ID in table '{table_name}'")
            rows_skipped += 1
            continue

        # Check if row already exists by ID
        if model_class is not None:
            result = await session.exec(
                select(model_class).where(model_class.id == row_id)
            )
            existing = result.one_or_none()
        else:
            # Fallback: assume table has 'id' column
            existing = None

        if existing is not None:
            logger.debug(f"Skipping existing row id={row_id} in table '{table_name}'")
            rows_skipped += 1
            continue

        # Insert new row
        try:
            if model_class is not None:
                obj = model_class(**row)
                session.add(obj)
                await session.commit()
                rows_inserted += 1
                logger.debug(f"Inserted row id={row_id} in table '{table_name}'")
            else:
                # Cannot insert without model class - skip
                logger.warning(f"Cannot insert without model class for table '{table_name}'")
                rows_skipped += 1
        except Exception as e:
            await session.rollback()
            logger.error(f"Failed to insert row in table '{table_name}': {e}")
            raise

    return rows_inserted, rows_skipped


async def _seed_table_idempotent_generic(
    session: Any,
    table_name: str,
    rows: list[dict[str, Any]],
    model_class: type,
    fk_field_mapping: dict[str, dict[str, str]] | None = None
) -> tuple[int, int]:
    """
    Generic idempotent seed function for packages that use the orchestrator.

    This is used when the package doesn't provide its own seed_fn.
    The orchestrator handles all the seeding logic using the model_classes
    provided in the SeederConfig.

    Args:
        session: SQLModel AsyncSession
        table_name: Name of the table (for logging)
        rows: List of row dictionaries with explicit IDs
        model_class: SQLModel class for the table
        fk_field_mapping: Optional mapping for FK field names (JSON -> model)

    Returns:
        Tuple of (rows_inserted, rows_skipped)
    """
    from sqlmodel import select

    rows_inserted = 0
    rows_skipped = 0

    # Get FK field mapping for this table if provided
    table_fk_map = (fk_field_mapping or {}).get(table_name, {})

    for row in rows:
        # Apply FK field mapping if needed (e.g., JSON has "category_id" but model uses "permission_category_id")
        if table_fk_map:
            row = {table_fk_map.get(k, k): v for k, v in row.items()}

        row_id = row.get("id")
        if row_id is None:
            logger.warning(f"Skipping row without ID in table '{table_name}'")
            rows_skipped += 1
            continue

        # Check if row already exists by ID
        result = await session.exec(
            select(model_class).where(model_class.id == row_id)
        )
        existing = result.one_or_none()

        if existing is not None:
            logger.debug(f"Skipping existing row id={row_id} in table '{table_name}'")
            rows_skipped += 1
            continue

        # Insert new row
        try:
            obj = model_class(**row)
            session.add(obj)
            await session.commit()
            rows_inserted += 1
            logger.debug(f"Inserted row id={row_id} in table '{table_name}'")
        except Exception as e:
            await session.rollback()
            logger.error(f"Failed to insert row in table '{table_name}': {e}")
            raise

    return rows_inserted, rows_skipped


# ============================================================================
# Main Orchestrator
# ============================================================================


async def seed_all(
    profile: str,
    package_filter: list[str] | None = None
) -> SeederResult:
    """
    Execute all registered seeders in dependency order.

    Seeders are executed in priority order (lower priority first).
    Each package's tables are loaded in dependency order based on
    the topological sort of depends_on relationships.

    Args:
        profile: Profile folder to use (e.g., "dev", "prod")
        package_filter: Optional list of package names to include.
                       If None, all registered seeders run.

    Returns:
        SeederResult with counts and any errors

    Raises:
        Any exceptions from manifest loading or data validation are propagated.

    Example:
        # Seed all packages with dev profile
        result = await seed_all("dev")

        # Seed only permissions2fast-fastapi
        result = await seed_all("dev", package_filter=["permissions2fast-fastapi"])
    """
    result = SeederResult()

    # Sort by priority (lower = first)
    sorted_seeders = sorted(get_registered_seeders(), key=lambda s: s.priority)

    for seeder in sorted_seeders:
        # Apply package filter if specified
        if package_filter and seeder.package_name not in package_filter:
            continue

        try:
            pkg_name = seeder.package_name or seeder.connection_name
            logger.info(f"Seeding package: {pkg_name}")

            # If package provides its own seed_fn, delegate to it
            if seeder.seed_fn is not None:
                logger.debug(f"Delegating to package's seed_fn for {pkg_name}")
                seed_result = await seeder.seed_fn(profile)
                # Merge result
                result.seeded_packages.append(pkg_name)
                result.errors.extend(seed_result.get("errors", []))
                result.tables_seeded += seed_result.get("tables_seeded", 0)
                result.rows_seeded += seed_result.get("rows_seeded", 0)
                logger.info(f"Completed seeding via package fn: {pkg_name}")
                continue

            # Generic seeding for packages without seed_fn
            # (Tenant seeders handle this differently - they use their own seed_all_tenants)
            if seeder.is_tenant_seeder:
                logger.warning(f"Tenant seeder {pkg_name} has no seed_fn - skipping (use seed_all_tenants directly)")
                continue

            # Get database session for this connection
            from pgsqlasync2fast_fastapi.connection import get_manager
            from sqlmodel.ext.asyncio.session import AsyncSession

            manager = get_manager()
            engine = manager.get_engine(seeder.connection_name)

            async with AsyncSession(engine) as session:
                # Load manifest
                manifest = _load_manifest(seeder.manifest_path)

                # Resolve load order using topological sort
                table_order = _resolve_load_order(manifest)

                # Track loaded tables for FK validation
                loaded_tables: dict[str, list[dict[str, Any]]] = {}

                for table_name in table_order:
                    rows = _load_table_data(table_name, seeder.manifest_path, profile)
                    if not rows:
                        continue

                    # Store for FK validation of dependent tables
                    loaded_tables[table_name] = rows

                    # Validate FK references before inserting any data
                    _validate_fk_references(
                        table_name, rows, seeder.manifest_path, profile, loaded_tables
                    )

                # Seed each table using model classes from seeder config
                for table_name in table_order:
                    rows = loaded_tables.get(table_name, [])
                    if not rows:
                        continue

                    model_class = seeder.model_classes.get(table_name)
                    if model_class is None:
                        logger.warning(f"No model class for table '{table_name}', skipping")
                        continue

                    # Use generic seed logic with model classes and FK mapping
                    inserted, skipped = await _seed_table_idempotent_generic(
                        session, table_name, rows, model_class,
                        fk_field_mapping=seeder.fk_field_mapping
                    )
                    result.rows_seeded += inserted
                    result.tables_seeded += 1

            logger.info(f"Completed seeding: {pkg_name}")

        except FileNotFoundError as e:
            error_msg = f"Failed to seed {seeder.package_name or seeder.connection_name}: {e}"
            logger.error(error_msg)
            result.errors.append(error_msg)
        except SeedValidationError as e:
            error_msg = f"Validation error in {seeder.package_name or seeder.connection_name}: {e}"
            logger.error(error_msg)
            result.errors.append(error_msg)
        except Exception as e:
            error_msg = f"Failed to seed {seeder.package_name or seeder.connection_name}: {e}"
            logger.error(error_msg)
            result.errors.append(error_msg)

    return result


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    # Dataclasses
    "SeederConfig",
    "SeederResult",
    # Exceptions
    "SeederException",
    "SeederConflictError",
    "SeedValidationError",
    # Registry functions
    "register_seeder",
    "get_registered_seeders",
    "clear_registry",
    # Main orchestrator
    "seed_all",
]