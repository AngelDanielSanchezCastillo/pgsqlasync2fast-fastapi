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

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid5

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
]
