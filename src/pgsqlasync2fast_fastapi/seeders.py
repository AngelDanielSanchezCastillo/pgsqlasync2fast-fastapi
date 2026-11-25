"""
pgsqlasync2fast_fastapi.seeders - Reusable Seeder Primitives

This module provides generic seeder primitives that can be used by any package
in the 2fast ecosystem. It does NOT know about tenants — that's the job of
tenants2fast's TenantSeederOrchestrator.

Key primitives:
- seed_uuid(): Deterministic UUID v5 generation
- SeederLog: Granular change tracking for efficient rollback
- build_fk_map() / build_all_fk_maps(): FK resolution maps
- resolve_fks(): Rewrite integer FKs to deterministic UUIDs
- resolve_polymorphic_fk(): Resolve polymorphic FKs with discriminator
- topological_sort(): Dependency-based table ordering

Example usage:
    from pgsqlasync2fast_fastapi.seeders import (
        seed_uuid,
        SeederLog,
        build_all_fk_maps,
        resolve_fks,
        topological_sort,
    )

    # Generate deterministic UUID
    uuid = seed_uuid("clients", 1, namespace)

    # Track changes for rollback
    log = SeederLog()
    log.log_insert("clients", {"id": uuid, "name": "Test"})

    # Build FK maps and resolve
    fk_maps = build_all_fk_maps(loaded_tables, namespace)
    resolved = resolve_fks("orders", rows, fk_maps, fk_columns)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

logger = logging.getLogger(__name__)


# ============================================================================
# Deterministic UUID Generation
# ============================================================================


def seed_uuid(table_name: str, old_id: object, namespace: UUID) -> UUID:
    """
    Generate a deterministic UUID v5 for a seed row.

    The same (table_name, old_id, namespace) always produces the same UUID,
    ensuring consistency across tenants and runs. This is the foundation of
    stable FK references in seeders.

    Args:
        table_name: Name of the table (e.g., "clients", "products")
        old_id: Original integer ID from the JSON seed data
        namespace: UUID namespace (should be consistent across the project)

    Returns:
        Deterministic UUID v5

    Example:
        >>> from uuid import uuid5, NAMESPACE_URL
        >>> ns = uuid5(NAMESPACE_URL, "https://myapp.local/seed")
        >>> uuid = seed_uuid("clients", 1, ns)
        >>> uuid == seed_uuid("clients", 1, ns)  # Always true
        True
    """
    return uuid5(namespace, f"{table_name}:{old_id}")


# ============================================================================
# SeederLog: Granular Change Tracking
# ============================================================================


@dataclass
class SeederLog:
    """
    Track real changes during seeding for efficient rollback.

    Only logs ACTUAL changes (not all intents). If a row is skipped because
    it already exists with the same values, it's NOT logged. This makes
    rollback efficient: only tables with real changes need rollback.

    Attributes:
        inserted_rows: Dict of table_name -> list of inserted rows
        updated_rows: Dict of table_name -> list of {id, old_values}

    Example:
        >>> log = SeederLog()
        >>> log.log_insert("clients", {"id": uuid, "name": "Test"})
        >>> log.has_changes("clients")
        True
        >>> log.total_changes()
        1
    """

    inserted_rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    updated_rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def log_insert(self, table_name: str, row: dict[str, Any]) -> None:
        """Log an inserted row."""
        self.inserted_rows.setdefault(table_name, []).append(row)

    def log_update(self, table_name: str, row_id: Any, old_values: dict[str, Any]) -> None:
        """
        Log an updated row with its old values.

        Args:
            table_name: Name of the table
            row_id: ID of the updated row
            old_values: Dict of field -> old_value (only changed fields)
        """
        self.updated_rows.setdefault(table_name, []).append({
            "id": row_id,
            "old_values": old_values,
        })

    def has_changes(self, table_name: str) -> bool:
        """True if the table had any inserts or updates."""
        return bool(
            self.inserted_rows.get(table_name) or self.updated_rows.get(table_name)
        )

    def total_changes(self) -> int:
        """Total number of changes across all tables."""
        return sum(len(v) for v in self.inserted_rows.values()) + sum(
            len(v) for v in self.updated_rows.values()
        )

    def affected_tables(self) -> list[str]:
        """List of tables with any changes."""
        tables = set(self.inserted_rows.keys()) | set(self.updated_rows.keys())
        return sorted(tables)


# ============================================================================
# FK Map Building
# ============================================================================


def build_fk_map(
    table_name: str,
    rows: list[dict[str, Any]],
    namespace: UUID,
) -> dict[int, UUID]:
    """
    Build a map of old_id -> deterministic UUID for a table.

    Args:
        table_name: Name of the table
        rows: List of row dicts with "id" field
        namespace: UUID namespace

    Returns:
        Dict mapping integer old_id to deterministic UUID

    Example:
        >>> rows = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
        >>> fk_map = build_fk_map("clients", rows, namespace)
        >>> fk_map[1]  # UUID for clients:1
        UUID(...)
    """
    return {
        row["id"]: seed_uuid(table_name, row["id"], namespace)
        for row in rows
        if isinstance(row.get("id"), int)
    }


def build_all_fk_maps(
    loaded_tables: dict[str, list[dict[str, Any]]],
    namespace: UUID,
) -> dict[str, dict[int, UUID]]:
    """
    Build FK maps for all loaded tables.

    Args:
        loaded_tables: Dict of table_name -> list of rows
        namespace: UUID namespace

    Returns:
        Dict of table_name -> {old_id -> UUID}

    Example:
        >>> loaded = {"clients": [{"id": 1}], "products": [{"id": 10}]}
        >>> maps = build_all_fk_maps(loaded, namespace)
        >>> maps["clients"][1]  # UUID for clients:1
        UUID(...)
    """
    return {
        table_name: build_fk_map(table_name, rows, namespace)
        for table_name, rows in loaded_tables.items()
    }


# ============================================================================
# Topological Sort
# ============================================================================


def topological_sort(dependencies: dict[str, set[str]]) -> list[str]:
    """
    Order tables by dependencies using topological sort (Kahn's algorithm).

    Args:
        dependencies: Dict of table_name -> set of dependency table names

    Returns:
        List of table names in dependency order (parents first)

    Raises:
        ValueError: If circular dependencies are detected

    Example:
        >>> deps = {"a": set(), "b": {"a"}, "c": {"b"}}
        >>> topological_sort(deps)
        ['a', 'b', 'c']
    """
    # Filter out unknown dependencies
    known_tables = set(dependencies.keys())
    filtered_deps = {
        table: deps & known_tables for table, deps in dependencies.items()
    }

    # Kahn's algorithm
    in_degree = {table: 0 for table in known_tables}
    for table, deps in filtered_deps.items():
        for dep in deps:
            if dep in in_degree:
                in_degree[table] += 1

    # Start with tables that have no dependencies
    queue = sorted([t for t in known_tables if in_degree[t] == 0])
    result = []

    while queue:
        current = queue.pop(0)
        result.append(current)

        # Reduce in-degree for tables that depend on current
        for table, deps in filtered_deps.items():
            if current in deps:
                in_degree[table] -= 1
                if in_degree[table] == 0:
                    queue.append(table)
                    queue.sort()  # Keep deterministic order

    if len(result) != len(known_tables):
        remaining = known_tables - set(result)
        raise ValueError(
            f"Cyclic dependency detected involving tables: {remaining}"
        )

    return result


# ============================================================================
# FK Resolution
# ============================================================================


def resolve_fks(
    table_name: str,
    rows: list[dict[str, Any]],
    fk_maps: dict[str, dict[int, UUID]],
    fk_columns: dict[str, str],
    namespace: UUID,
) -> list[dict[str, Any]]:
    """
    Resolve integer FKs to deterministic UUIDs.

    Args:
        table_name: Name of the table being resolved
        rows: List of row dicts with integer FKs
        fk_maps: Dict of parent_table -> {old_id -> UUID}
        fk_columns: Dict of field_name -> parent_table_name
        namespace: UUID namespace for generating deterministic UUIDs

    Returns:
        List of row dicts with UUIDs instead of integer FKs

    Raises:
        ValueError: If a FK reference is not found in the map

    Example:
        >>> fk_maps = {"clients": {1: uuid_client_1}}
        >>> fk_columns = {"client_id": "clients"}
        >>> rows = [{"id": 1, "client_id": 1, "quantity": 5}]
        >>> resolved = resolve_fks("orders", rows, fk_maps, fk_columns, namespace)
        >>> resolved[0]["client_id"] == uuid_client_1
        True
    """
    resolved = []
    for row in rows:
        new_row = dict(row)

        # Replace row's own ID with deterministic UUID
        old_id = new_row.get("id")
        if isinstance(old_id, int):
            new_row["id"] = seed_uuid(table_name, old_id, namespace)

        # Resolve FK columns
        for field_name, parent_table in fk_columns.items():
            if field_name not in new_row:
                continue
            value = new_row[field_name]
            if not isinstance(value, int):
                continue  # Already a UUID or None

            parent_map = fk_maps.get(parent_table, {})
            if value not in parent_map:
                raise ValueError(
                    f"Seed FK {table_name}.{field_name}={value} has no mapped "
                    f"parent in table '{parent_table}'"
                )
            new_row[field_name] = parent_map[value]

        resolved.append(new_row)

    return resolved


def resolve_polymorphic_fk(
    table_name: str,
    rows: list[dict[str, Any]],
    fk_maps: dict[str, dict[int, UUID]],
    polymorphic_config: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Resolve polymorphic FKs using discriminator field.

    Args:
        table_name: Name of the table being resolved
        rows: List of row dicts with polymorphic FKs
        fk_maps: Dict of parent_table -> {old_id -> UUID}
        polymorphic_config: Dict of fk_field -> {discriminator, map}
            Example: {
                "addressable_id": {
                    "discriminator": "addressable_type",
                    "map": {"client": "clients", "supplier": "suppliers"}
                }
            }

    Returns:
        List of row dicts with UUIDs instead of integer FKs

    Raises:
        ValueError: If discriminator value is not in the map

    Example:
        >>> config = {
        ...     "addressable_id": {
        ...         "discriminator": "addressable_type",
        ...         "map": {"client": "clients"}
        ...     }
        ... }
        >>> rows = [{"id": 1, "addressable_id": 1, "addressable_type": "client"}]
        >>> resolved = resolve_polymorphic_fk("addresses", rows, fk_maps, config)
    """
    resolved = []
    for row in rows:
        new_row = dict(row)

        for fk_field, config in polymorphic_config.items():
            if fk_field not in new_row:
                continue

            discriminator_field = config["discriminator"]
            type_map = config["map"]

            discriminator_value = new_row.get(discriminator_field)
            if discriminator_value is None:
                continue

            discriminator_str = str(discriminator_value).lower()
            if discriminator_str not in type_map:
                raise ValueError(
                    f"Polymorphic FK {table_name}.{fk_field} has unknown "
                    f"discriminator value '{discriminator_value}' "
                    f"(expected one of: {list(type_map.keys())})"
                )

            parent_table = type_map[discriminator_str]
            fk_value = new_row[fk_field]

            if not isinstance(fk_value, int):
                continue  # Already a UUID

            parent_map = fk_maps.get(parent_table, {})
            if fk_value not in parent_map:
                raise ValueError(
                    f"Polymorphic FK {table_name}.{fk_field}={fk_value} has no "
                    f"mapped parent in table '{parent_table}'"
                )

            new_row[fk_field] = parent_map[fk_value]

        resolved.append(new_row)

    return resolved


# ============================================================================
# Validation Error
# ============================================================================


class SeedValidationError(ValueError):
    """
    Raised when seed data fails validation.

    This is separate from ``SeedValidationError`` in ``seeder.py`` (legacy) to
    keep the new primitives decoupled. Both are ``ValueError`` subclasses.
    """


class PackageSeederConflictError(ValueError):
    """Raised when two registered packages seed overlapping tables on one connection."""


# ============================================================================
# Seeding Result
# ============================================================================


@dataclass
class SeedingResult:
    """
    Result of a SeederEngine.seed() run.

    Attributes:
        success: True when all tables seeded without errors
        database_name: Target database name
        profile: Profile used (e.g. "dev")
        tables_seeded: Number of tables processed
        rows_inserted: Rows inserted
        rows_updated: Rows updated (fields actually changed)
        rows_skipped: Rows skipped (already up to date)
        errors: List of error messages
        rolled_back: True when a full rollback was performed
        table_rolled_back: Table name rolled back (partial rollback)
        log: The SeederLog with all real changes
    """

    success: bool = False
    database_name: str = ""
    profile: str = ""
    tables_seeded: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)
    rolled_back: bool = False
    table_rolled_back: str | None = None
    log: SeederLog | None = None


# ============================================================================
# Filters
# ============================================================================


@dataclass
class SeederFilters:
    """
    Generic filters applied before seeding.

    Attributes:
        tables: Optional allow-list of table names to seed. None = all tables.
        rows: Optional per-table predicate ``predicate(row) -> bool``. Rows
            returning False are dropped before seeding.

    Example:
        >>> filters = SeederFilters(
        ...     tables=["clients", "products"],
        ...     rows={"clients": lambda r: r["business_name"] == "Demo"},
        ... )
    """

    tables: list[str] | None = None
    rows: dict[str, Any] = field(default_factory=dict)

    def allows_table(self, table_name: str) -> bool:
        return self.tables is None or table_name in self.tables

    def apply_rows(self, table_name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        predicate = self.rows.get(table_name)
        if predicate is None:
            return rows
        return [row for row in rows if predicate(row)]


# ============================================================================
# Async Upsert with Change Detection
# ============================================================================


async def upsert_with_optimization(
    session: Any,
    model: type,
    row: dict[str, Any],
    log: SeederLog | None = None,
    table_name: str | None = None,
) -> str:
    """
    Upsert a single row, updating ONLY when fields actually changed.

    Comparison is per-field: if every field already matches, no UPDATE is
    issued (returns ``"skipped"``). This keeps repeated boot seeding cheap.

    Args:
        session: Async session (SQLModel or SQLAlchemy).
        model: SQLModel/SQLAlchemy model class.
        row: Full row dict including ``id``.
        log: Optional SeederLog to record real changes.
        table_name: Override for the log key (defaults to ``model.__tablename__``).

    Returns:
        One of ``"inserted"``, ``"updated"``, ``"skipped"``.

    Raises:
        ValueError: If ``id`` is missing.
    """
    from sqlmodel import select

    row_id = row.get("id")
    if row_id is None:
        raise ValueError("upsert_with_optimization requires 'id' field in row")

    log_key = table_name or model.__tablename__

    result = await session.execute(select(model).where(getattr(model, "id") == row_id))
    existing = result.scalars().first()

    if existing is None:
        obj = model(**row)
        session.add(obj)
        await session.flush()
        if log is not None:
            log.log_insert(log_key, row)
        return "inserted"

    changed_old_values: dict[str, Any] = {}
    for key, value in row.items():
        if key == "id":
            continue
        if not hasattr(existing, key):
            continue
        current = getattr(existing, key)
        if current != value:
            changed_old_values[key] = current
            setattr(existing, key, value)

    if not changed_old_values:
        return "skipped"

    session.add(existing)
    await session.flush()
    if log is not None:
        log.log_update(log_key, row_id, changed_old_values)
    return "updated"


# ============================================================================
# Rollback
# ============================================================================


async def rollback_all(
    session: Any,
    log: SeederLog,
    model_registry: dict[str, type],
) -> None:
    """
    Roll back every real change recorded in ``log``.

    Deletes inserted rows (children first, reverse table order) and restores
    updated rows to their logged old values.
    """
    from sqlmodel import delete, update

    # Delete inserts in reverse insertion order to respect FK constraints.
    tables_in_insert_order = [t for t in log.affected_tables() if log.inserted_rows.get(t)]
    for table_name in reversed(tables_in_insert_order):
        model = model_registry.get(table_name)
        if model is None:
            logger.warning("Rollback: no model for table '%s', skipping deletes", table_name)
            continue
        ids = [row["id"] for row in log.inserted_rows[table_name]]
        if ids:
            await session.execute(
                delete(model).where(getattr(model, "id").in_(ids))
            )

    # Restore updates.
    for table_name, updates in log.updated_rows.items():
        model = model_registry.get(table_name)
        if model is None:
            logger.warning("Rollback: no model for table '%s', skipping updates", table_name)
            continue
        for entry in updates:
            await session.execute(
                update(model)
                .where(getattr(model, "id") == entry["id"])
                .values(**entry["old_values"])
            )

    await session.flush()


async def rollback_table(
    session: Any,
    log: SeederLog,
    model_registry: dict[str, type],
    table_name: str,
) -> None:
    """
    Roll back real changes for a single table.

    No-op when the table has no recorded changes (efficient partial rollback).
    """
    from sqlmodel import delete, update

    if not log.has_changes(table_name):
        logger.info("Rollback: no changes for table '%s', skipping", table_name)
        return

    model = model_registry.get(table_name)
    if model is None:
        logger.warning("Rollback: no model for table '%s', skipping", table_name)
        return

    inserted = log.inserted_rows.get(table_name, [])
    ids = [row["id"] for row in inserted]
    if ids:
        await session.execute(delete(model).where(getattr(model, "id").in_(ids)))

    for entry in log.updated_rows.get(table_name, []):
        await session.execute(
            update(model)
            .where(getattr(model, "id") == entry["id"])
            .values(**entry["old_values"])
        )

    await session.flush()


# ============================================================================
# SeederEngine: Manifest-Driven Orchestrator
# ============================================================================


def _collect_table_errors(
    tables_cfg: dict[str, dict[str, Any]],
    manifest_dir: Path,
    model_registry: dict[str, type],
) -> list[str]:
    """
    Validate a ``tables`` config block and its JSON data.

    Checks: model registered, file exists, valid JSON list, no unknown keys,
    and no duplicate values for declared ``unique_keys``.
    """
    errors: list[str] = []

    for table_name, cfg in tables_cfg.items():
        model = model_registry.get(table_name)
        if model is None:
            errors.append(f"{table_name}: no model class registered")
            continue

        file_name = cfg.get("file", f"{table_name}.json")
        path = Path(file_name)
        if not path.is_absolute():
            path = manifest_dir / path
        if not path.exists():
            errors.append(f"{table_name}: seed file not found: {path}")
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - surface any parse error
            errors.append(f"{table_name}: invalid JSON: {exc}")
            continue

        if not isinstance(data, list):
            errors.append(
                f"{table_name}: data must be a list, got {type(data).__name__}"
            )
            continue

        allowed = set(getattr(model, "model_fields", {}))
        field_mapping = cfg.get("field_mapping", {})
        unknown: set[str] = set()
        for row in data:
            mapped_keys = {field_mapping.get(key, key) for key in row}
            unknown |= mapped_keys - allowed
        if unknown:
            errors.append(f"{table_name}: unknown keys {sorted(unknown)}")

        unique_keys = cfg.get("unique_keys")
        if unique_keys:
            seen: set[tuple] = set()
            for row in data:
                key = tuple(row.get(k) for k in unique_keys)
                if key in seen:
                    errors.append(
                        f"{table_name}: duplicate unique key {key} for "
                        f"columns {unique_keys}"
                    )
                else:
                    seen.add(key)

    return errors


class SeederEngine:
    """
    Generic, tenant-agnostic seeder engine driven by a manifest v2.0.

    Layers:
        ``boot``      - automatic seeders (run on app startup)
        ``on_demand`` - manual seeders (run via CLI / code)

    The engine knows nothing about tenants: it seeds a single database at a
    time through a ``database_factory`` callable. tenants2fast wraps it to
    iterate tenants and apply tenant filters.

    Args:
        manifest_path: Path to the manifest.json file.
        model_registry: Mapping ``table_name -> SQLModel class``.
        profile: Profile name (e.g. "dev", "prod", "demo").
        database_factory: Callable ``(database_name) -> AsyncEngine``.
        strict: Override manifest ``strict``. None = use manifest value.
        on_failure: Override manifest ``on_failure``. None = use manifest value.
        uuid_namespace: Override manifest ``uuid_namespace``.

    Example:
        >>> engine = SeederEngine(
        ...     manifest_path="tenant/seeders/manifest.json",
        ...     model_registry={"clients": Client},
        ...     profile="dev",
        ...     database_factory=get_engine,
        ... )
        >>> result = await engine.seed("tenant_testcompany")
    """

    _DEFAULT_NAMESPACE_URL = "https://2fast.local/seed"

    def __init__(
        self,
        manifest_path: str,
        model_registry: dict[str, type],
        profile: str = "dev",
        database_factory: Any = None,
        strict: bool | None = None,
        on_failure: str | None = None,
        uuid_namespace: UUID | None = None,
    ) -> None:
        self.manifest_path = manifest_path
        self.manifest_dir = Path(manifest_path).parent
        self.model_registry = model_registry
        self.profile = profile
        self.database_factory = database_factory

        manifest = _load_manifest_file(manifest_path)
        self.manifest = manifest

        self.strict = strict if strict is not None else manifest.get("strict", True)
        self.on_failure = on_failure if on_failure is not None else manifest.get(
            "on_failure", "rollback_all"
        )

        if uuid_namespace is not None:
            self.uuid_namespace = uuid_namespace
        elif manifest.get("uuid_namespace"):
            self.uuid_namespace = UUID(manifest["uuid_namespace"])
        else:
            self.uuid_namespace = uuid5(NAMESPACE_URL, self._DEFAULT_NAMESPACE_URL)

    # -- Manifest helpers ---------------------------------------------------

    def _get_layer_tables(self, layer: str) -> dict[str, dict[str, Any]]:
        """Return ``tables`` config for the current profile in ``layer``."""
        layer_cfg = self.manifest.get(layer, {})
        profile_cfg = layer_cfg.get(self.profile, {})
        return profile_cfg.get("tables", {})

    def _get_polymorphic_config(self, table_name: str) -> dict[str, dict[str, Any]]:
        """Return polymorphic FK config for a table (root-level ``polymorphic_fks``)."""
        return self.manifest.get("polymorphic_fks", {}).get(table_name, {})

    def _load_table_data(self, table_name: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
        """Load a table's JSON data (path relative to manifest dir unless absolute)."""
        file_name = cfg.get("file", f"{table_name}.json")
        path = Path(file_name)
        if not path.is_absolute():
            path = self.manifest_dir / path

        if not path.exists():
            logger.warning("Seed data not found for '%s': %s", table_name, path)
            return []

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise SeedValidationError(
                f"Table '{table_name}' data must be a list, got {type(data).__name__}"
            )
        return data

    # -- Validation ---------------------------------------------------------

    def _validate(self, tables_cfg: dict[str, dict[str, Any]]) -> list[str]:
        """
        Validate the seed configuration/data before any write.

        Returns a list of error strings. Raises ``SeedValidationError`` when
        errors exist AND ``strict`` is True.
        """
        errors = _collect_table_errors(
            tables_cfg, self.manifest_dir, self.model_registry
        )
        if errors and self.strict:
            raise SeedValidationError(
                f"Invalid seed data for profile '{self.profile}': " + "; ".join(errors)
            )
        return errors

    # -- Dependency resolution ---------------------------------------------

    def _resolve_dependencies(self, table_names: list[str], layer: str) -> dict[str, set[str]]:
        """
        Resolve table dependencies.

        Preference order:
        1. Explicit ``dependencies`` in the manifest for the given layer/profile.
        2. Explicit ``dependencies`` in the other layer/profile.
        3. Model FK introspection.
        """
        known = set(table_names)

        primary = self.manifest.get(layer, {}).get(self.profile, {})
        other_layer = "on_demand" if layer == "boot" else "boot"
        secondary = self.manifest.get(other_layer, {}).get(self.profile, {})

        explicit = primary.get("dependencies") or secondary.get("dependencies")
        if explicit:
            return {
                name: {dep for dep in explicit.get(name, []) if dep in known}
                for name in table_names
            }

        # Model FK introspection.
        deps: dict[str, set[str]] = {}
        for name in table_names:
            model = self.model_registry.get(name)
            if model is None:
                deps[name] = set()
                continue
            deps[name] = {
                fk.column.table.name
                for fk in model.__table__.foreign_keys
                if fk.column.table.name in known and fk.column.table.name != name
            }
        return deps

    def _fk_columns(self, table_name: str, model: type) -> dict[str, str]:
        """Map FK field name -> parent table name from model introspection."""
        columns: dict[str, str] = {}
        for column in model.__table__.columns:
            if column.foreign_keys:
                columns[column.name] = next(iter(column.foreign_keys)).column.table.name
        return columns

    # -- Seeding ------------------------------------------------------------

    async def seed(
        self,
        database_name: str,
        filters: SeederFilters | None = None,
        layer: str = "boot",
    ) -> SeedingResult:
        """
        Seed a single database with the configured layer/profile.

        Args:
            database_name: Logical database name passed to ``database_factory``.
            filters: Optional generic filters.
            layer: ``"boot"`` (default) or ``"on_demand"``.

        Returns:
            SeedingResult with counters and errors.
        """
        result = SeedingResult(database_name=database_name, profile=self.profile)

        tables_cfg = self._get_layer_tables(layer)
        validation_errors = self._validate(tables_cfg)
        if validation_errors:
            result.errors.extend(validation_errors)
            return result

        # Apply table filter.
        if filters is not None:
            tables_cfg = {
                name: cfg for name, cfg in tables_cfg.items() if filters.allows_table(name)
            }

        # Load all rows first (fail before writing anything).
        loaded: dict[str, list[dict[str, Any]]] = {}
        for table_name, cfg in tables_cfg.items():
            rows = self._load_table_data(table_name, cfg)
            if filters is not None:
                rows = filters.apply_rows(table_name, rows)
            if rows:
                loaded[table_name] = rows

        if not loaded:
            result.success = True
            return result

        if self.database_factory is None:
            result.errors.append("No database_factory configured")
            return result

        fk_maps = build_all_fk_maps(loaded, self.uuid_namespace)
        dependencies = self._resolve_dependencies(list(loaded.keys()), layer)
        order = topological_sort(dependencies)

        engine = self.database_factory(database_name)
        from sqlalchemy.ext.asyncio import AsyncSession

        log = SeederLog()
        async with AsyncSession(engine) as session:
            current_table: str | None = None
            try:
                for table_name in order:
                    rows = loaded.get(table_name)
                    if not rows:
                        continue
                    current_table = table_name
                    model = self.model_registry[table_name]
                    cfg = tables_cfg.get(table_name, {})
                    mode = cfg.get("mode", "upsert")
                    unique_keys = cfg.get("unique_keys")
                    conflict_resolution = cfg.get("conflict_resolution", "validate")

                    resolved = self._resolve_rows(
                        table_name,
                        rows,
                        model,
                        fk_maps,
                        cfg.get("field_mapping"),
                    )

                    inserted, updated, skipped = await self._seed_table(
                        session=session,
                        model=model,
                        rows=resolved,
                        mode=mode,
                        log=log,
                        table_name=table_name,
                        unique_keys=unique_keys,
                        conflict_resolution=conflict_resolution,
                    )
                    await session.commit()

                    result.tables_seeded += 1
                    result.rows_inserted += inserted
                    result.rows_updated += updated
                    result.rows_skipped += skipped

                result.success = True
            except Exception as exc:  # noqa: BLE001 - handled per on_failure
                await session.rollback()
                message = f"Seeding failed for '{database_name}' on table '{current_table}': {exc}"
                logger.error(message)
                result.errors.append(message)
                result.log = log

                if self.on_failure == "rollback_all":
                    try:
                        await rollback_all(
                            session, log, self.model_registry
                        )
                        await session.commit()
                        result.rolled_back = True
                    except Exception as rb_exc:  # noqa: BLE001
                        result.errors.append(f"Rollback failed: {rb_exc}")
                elif self.on_failure == "rollback_table" and current_table:
                    try:
                        await rollback_table(
                            session, log, self.model_registry, current_table
                        )
                        await session.commit()
                        result.table_rolled_back = current_table
                    except Exception as rb_exc:  # noqa: BLE001
                        result.errors.append(f"Rollback failed: {rb_exc}")
                # on_failure == "skip": leave committed tables as-is.

        result.log = log
        return result

    def _resolve_rows(
        self,
        table_name: str,
        rows: list[dict[str, Any]],
        model: type,
        fk_maps: dict[str, dict[int, UUID]],
        field_mapping: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        if field_mapping:
            rows = [
                {field_mapping.get(key, key): value for key, value in row.items()}
                for row in rows
            ]
        resolved = resolve_fks(
            table_name,
            rows,
            fk_maps,
            self._fk_columns(table_name, model),
            self.uuid_namespace,
        )
        polymorphic = self._get_polymorphic_config(table_name)
        if polymorphic:
            resolved = resolve_polymorphic_fk(
                table_name, resolved, fk_maps, polymorphic
            )
        return resolved

    async def _seed_table(
        self,
        session: Any,
        model: type,
        rows: list[dict[str, Any]],
        mode: str,
        log: SeederLog,
        table_name: str,
        unique_keys: list[str] | None,
        conflict_resolution: str,
    ) -> tuple[int, int, int]:
        """Seed one table according to its mode. Returns (inserted, updated, skipped)."""
        if mode not in ("upsert", "insert_only", "skip_if_exists", "replace_all"):
            raise ValueError(f"Unsupported seed mode '{mode}' for table '{table_name}'")

        if mode == "replace_all":
            from sqlmodel import delete

            await session.execute(delete(model))

        if mode == "skip_if_exists":
            existing = (await session.execute(select_count(model))).first()
            if existing and existing[0]:
                return 0, 0, len(rows)

        if unique_keys and conflict_resolution == "park_and_upsert":
            await self._park_unique_keys(session, model, rows, unique_keys)

        inserted = updated = skipped = 0
        for row in rows:
            if mode == "insert_only":
                outcome = await self._insert_only(session, model, row, log, table_name)
            else:
                outcome = await upsert_with_optimization(
                    session, model, row, log, table_name
                )

            if outcome == "inserted":
                inserted += 1
            elif outcome == "updated":
                updated += 1
            else:
                skipped += 1

        return inserted, updated, skipped

    async def _insert_only(
        self,
        session: Any,
        model: type,
        row: dict[str, Any],
        log: SeederLog,
        table_name: str,
    ) -> str:
        from sqlmodel import select

        row_id = row.get("id")
        result = await session.execute(
            select(model).where(getattr(model, "id") == row_id)
        )
        if result.scalars().first() is not None:
            raise SeedValidationError(
                f"insert_only violation: row id={row_id} already exists in '{table_name}'"
            )
        session.add(model(**row))
        await session.flush()
        log.log_insert(table_name, row)
        return "inserted"

    async def _park_unique_keys(
        self,
        session: Any,
        model: type,
        rows: list[dict[str, Any]],
        unique_keys: list[str],
    ) -> None:
        """Park unique keys of existing seed rows to avoid transient collisions.

        Only text columns are parked: UUID/FK columns cannot hold a temporary
        string value, and breaking one text component of a composite unique
        key is enough to avoid transient collisions.
        """
        from sqlalchemy import String
        from sqlmodel import select

        columns = model.__table__.c

        def _is_text(column: Any) -> bool:
            impl = getattr(column.type, "impl", column.type)
            return isinstance(impl, String)

        parkable = [
            key
            for key in unique_keys
            if key in columns and not columns[key].foreign_keys and _is_text(columns[key])
        ]
        if not parkable:
            logger.debug(
                "No parkable text columns in unique keys %s for '%s'",
                unique_keys,
                model.__tablename__,
            )
            return

        ids = [row["id"] for row in rows if row.get("id") is not None]
        if not ids:
            return
        result = await session.execute(
            select(model).where(getattr(model, "id").in_(ids))
        )
        for index, obj in enumerate(result.scalars().all()):
            for key in parkable:
                setattr(obj, key, f"__tmp_{index}")
            session.add(obj)
        await session.flush()


def _load_manifest_file(manifest_path: str) -> dict[str, Any]:
    """Load a manifest.json file into a dict."""
    path = Path(manifest_path)
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def select_count(model: type) -> Any:
    """Return a ``SELECT count(*)`` statement for a model."""
    from sqlalchemy import func, select as sa_select

    return sa_select(func.count()).select_from(model)


# ============================================================================
# Package Registry + Orchestration (ODD-5 Fase 1.5)
# ============================================================================


@dataclass
class PackageSeederConfig:
    """
    Configuration for a package that ships its own seeders.

    Packages register at import time so a consuming app only needs to turn on
    ``RUN_SEEDERS=true`` to get every package's boot data.

    Attributes:
        package_name: Unique package identifier (registry key).
        manifest_path: Path to the package's manifest.json.
        model_registry: Mapping ``table_name -> SQLModel class``.
        connection: Logical connection/lane this package seeds (e.g. "auth").
        priority: Lower runs first (default 50).
        layer: Default manifest layer ("boot" or "on_demand").
        description: Optional human-readable description.
    """

    package_name: str
    manifest_path: str
    model_registry: dict[str, type]
    connection: str = "default"
    priority: int = 50
    layer: str = "boot"
    description: str = ""


_PACKAGE_REGISTRY: dict[str, PackageSeederConfig] = {}


def _manifest_all_tables(manifest_path: str) -> set[str]:
    """Return every table name declared in a manifest (all layers/profiles)."""
    manifest = _load_manifest_file(manifest_path)
    tables: set[str] = set()
    for layer_name in ("boot", "on_demand"):
        layer = manifest.get(layer_name, {})
        for profile_cfg in layer.values():
            tables |= set(profile_cfg.get("tables", {}).keys())
    return tables


def register_package_seeders(config: PackageSeederConfig) -> None:
    """
    Register a package's seeders in the global registry.

    Re-registering the same ``package_name`` replaces the previous entry
    (idempotent). A *different* package registering overlapping tables on the
    same connection raises ``PackageSeederConflictError`` to prevent silent
    data races.

    Args:
        config: Package seeder configuration.

    Raises:
        PackageSeederConflictError: On overlapping tables for a distinct
            package on the same connection.
    """
    for existing_name, existing in _PACKAGE_REGISTRY.items():
        if existing_name == config.package_name:
            continue
        if existing.connection != config.connection:
            continue
        overlap = _manifest_all_tables(existing.manifest_path) & _manifest_all_tables(
            config.manifest_path
        )
        if overlap:
            raise PackageSeederConflictError(
                f"Tables {sorted(overlap)} conflict between "
                f"'{existing.package_name}' and '{config.package_name}' "
                f"on connection '{config.connection}'"
            )

    _PACKAGE_REGISTRY[config.package_name] = config
    logger.debug(
        "Registered package seeders: %s (priority=%s, connection=%s)",
        config.package_name,
        config.priority,
        config.connection,
    )


def get_registered_packages() -> list[PackageSeederConfig]:
    """Return a copy of the package registry (sorted by priority)."""
    return sorted(_PACKAGE_REGISTRY.values(), key=lambda c: c.priority)


def clear_package_registry() -> None:
    """Clear the package registry. Useful for tests."""
    _PACKAGE_REGISTRY.clear()


def validate_seeders(
    manifest_path: str,
    model_registry: dict[str, type],
    layer: str = "boot",
    profile: str = "dev",
) -> list[str]:
    """
    Validate a package's seeder structure without touching a database.

    Intended for CI: every package can assert ``validate_seeders(...) == []``
    so a broken manifest/JSON never reaches a release. Consumers (e.g.
    Metal-ERP) can reuse it to validate third-party package seeders too.

    Args:
        manifest_path: Path to the manifest.json.
        model_registry: Mapping ``table_name -> SQLModel class``.
        layer: Manifest layer to validate ("boot" or "on_demand").
        profile: Profile inside the layer (e.g. "dev", "prod").

    Returns:
        List of error strings (empty when valid).
    """
    try:
        manifest = _load_manifest_file(manifest_path)
    except FileNotFoundError as exc:
        return [str(exc)]

    layer_cfg = manifest.get(layer, {})
    if not layer_cfg:
        return [f"manifest has no '{layer}' layer"]

    profile_cfg = layer_cfg.get(profile)
    if profile_cfg is None:
        available = sorted(layer_cfg.keys())
        return [f"no '{layer}' config for profile '{profile}' (available: {available})"]

    tables_cfg = profile_cfg.get("tables", {})
    if not tables_cfg:
        return [f"no tables declared for '{layer}.{profile}'"]

    return _collect_table_errors(
        tables_cfg, Path(manifest_path).parent, model_registry
    )


async def seed_all(
    profile: str,
    *,
    engine_resolver: Any = None,
    layer: str = "boot",
    package_filter: list[str] | None = None,
) -> dict[str, SeedingResult]:
    """
    Execute every registered package's seeder in priority order.

    Each package is seeded through its own ``SeederEngine`` on the package's
    configured connection. This is the app-facing entry point: packages that
    registered themselves at import time are seeded automatically.

    Args:
        profile: Profile to seed (e.g. "dev", "prod").
        engine_resolver: ``Callable[[connection_name], AsyncEngine]``. When
            None, uses ``pgsqlasync2fast_fastapi.connection.get_manager()``.
        layer: Manifest layer to seed (default "boot").
        package_filter: Optional allow-list of package names.

    Returns:
        Dict of ``package_name -> SeedingResult`` in execution order.
    """
    if engine_resolver is None:
        from pgsqlasync2fast_fastapi.connection import get_manager

        engine_resolver = lambda connection: get_manager().get_engine(connection)  # noqa: E731

    results: dict[str, SeedingResult] = {}

    for config in get_registered_packages():
        if package_filter and config.package_name not in package_filter:
            continue

        engine = SeederEngine(
            manifest_path=config.manifest_path,
            model_registry=config.model_registry,
            profile=profile,
            database_factory=(
                lambda _name, conn=config.connection: engine_resolver(conn)
            ),
        )
        results[config.package_name] = await engine.seed(
            config.connection, layer=config.layer or layer
        )

    return results


# ============================================================================
# Exports
# ============================================================================


__all__ = [
    # Deterministic UUID
    "seed_uuid",
    # Change tracking
    "SeederLog",
    # FK resolution
    "build_fk_map",
    "build_all_fk_maps",
    "resolve_fks",
    "resolve_polymorphic_fk",
    # Topological sort
    "topological_sort",
    # Async upsert with optimization
    "upsert_with_optimization",
    # Validation
    "SeedValidationError",
    # Results + filters
    "SeedingResult",
    "SeederFilters",
    # Rollback
    "rollback_all",
    "rollback_table",
    # Engine
    "SeederEngine",
    # Package registry + orchestration
    "PackageSeederConfig",
    "PackageSeederConflictError",
    "register_package_seeders",
    "get_registered_packages",
    "clear_package_registry",
    "validate_seeders",
    "seed_all",
]
