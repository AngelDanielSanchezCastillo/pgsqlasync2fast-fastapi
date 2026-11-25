"""
Tests for pgsqlasync2fast_fastapi.seeders primitives (TDD).

Covers:
- seed_uuid(): deterministic UUID v5 generation
- SeederLog: granular change tracking
- build_fk_map(): FK resolution maps
- resolve_fks(): rewrite integer FKs to UUIDs
- resolve_polymorphic_fk(): polymorphic FK resolution
- topological_sort(): dependency-based table ordering
"""

from __future__ import annotations

from uuid import UUID, uuid5, NAMESPACE_URL

import pytest


# ============================================================
# seed_uuid: Deterministic UUID v5
# ============================================================


class TestSeedUuid:
    """Tests for seed_uuid() deterministic UUID generation."""

    def test_same_input_same_output(self):
        """Same (table, id, namespace) always produces same UUID."""
        from pgsqlasync2fast_fastapi.seeders import seed_uuid

        ns = uuid5(NAMESPACE_URL, "https://test.local")
        uuid1 = seed_uuid("clients", 1, ns)
        uuid2 = seed_uuid("clients", 1, ns)
        assert uuid1 == uuid2

    def test_different_id_different_uuid(self):
        """Different IDs produce different UUIDs."""
        from pgsqlasync2fast_fastapi.seeders import seed_uuid

        ns = uuid5(NAMESPACE_URL, "https://test.local")
        uuid1 = seed_uuid("clients", 1, ns)
        uuid2 = seed_uuid("clients", 2, ns)
        assert uuid1 != uuid2

    def test_different_table_different_uuid(self):
        """Same ID in different tables produce different UUIDs."""
        from pgsqlasync2fast_fastapi.seeders import seed_uuid

        ns = uuid5(NAMESPACE_URL, "https://test.local")
        uuid1 = seed_uuid("clients", 1, ns)
        uuid2 = seed_uuid("products", 1, ns)
        assert uuid1 != uuid2

    def test_different_namespace_different_uuid(self):
        """Same (table, id) with different namespaces produce different UUIDs."""
        from pgsqlasync2fast_fastapi.seeders import seed_uuid

        ns1 = uuid5(NAMESPACE_URL, "https://test.local")
        ns2 = uuid5(NAMESPACE_URL, "https://other.local")
        uuid1 = seed_uuid("clients", 1, ns1)
        uuid2 = seed_uuid("clients", 1, ns2)
        assert uuid1 != uuid2

    def test_returns_uuid_instance(self):
        """seed_uuid returns a UUID instance."""
        from pgsqlasync2fast_fastapi.seeders import seed_uuid

        ns = uuid5(NAMESPACE_URL, "https://test.local")
        result = seed_uuid("clients", 1, ns)
        assert isinstance(result, UUID)

    def test_cross_tenant_consistency(self):
        """Same (table, id, namespace) across tenants produces same UUID.

        This is the key property: UUID is stable across tenants and runs.
        """
        from pgsqlasync2fast_fastapi.seeders import seed_uuid

        ns = uuid5(NAMESPACE_URL, "https://metal-erp.local/seed")
        # Simulate two different tenants
        tenant_a_uuid = seed_uuid("clients", 42, ns)
        tenant_b_uuid = seed_uuid("clients", 42, ns)
        assert tenant_a_uuid == tenant_b_uuid


# ============================================================
# SeederLog: Granular change tracking
# ============================================================


class TestSeederLog:
    """Tests for SeederLog change tracking."""

    def test_empty_log_has_no_changes(self):
        """Empty log reports no changes."""
        from pgsqlasync2fast_fastapi.seeders import SeederLog

        log = SeederLog()
        assert log.total_changes() == 0
        assert not log.has_changes("clients")

    def test_log_insert(self):
        """log_insert records an insert."""
        from pgsqlasync2fast_fastapi.seeders import SeederLog

        log = SeederLog()
        log.log_insert("clients", {"id": 1, "name": "Test"})
        assert log.has_changes("clients")
        assert log.total_changes() == 1
        assert log.inserted_rows["clients"] == [{"id": 1, "name": "Test"}]

    def test_log_update(self):
        """log_update records an update with old values."""
        from pgsqlasync2fast_fastapi.seeders import SeederLog

        log = SeederLog()
        log.log_update("clients", 1, {"name": "Old Name"})
        assert log.has_changes("clients")
        assert log.total_changes() == 1
        assert log.updated_rows["clients"] == [
            {"id": 1, "old_values": {"name": "Old Name"}}
        ]

    def test_multiple_inserts(self):
        """Multiple inserts are tracked."""
        from pgsqlasync2fast_fastapi.seeders import SeederLog

        log = SeederLog()
        log.log_insert("clients", {"id": 1, "name": "A"})
        log.log_insert("clients", {"id": 2, "name": "B"})
        assert log.total_changes() == 2
        assert len(log.inserted_rows["clients"]) == 2

    def test_mixed_operations(self):
        """Inserts and updates are tracked separately."""
        from pgsqlasync2fast_fastapi.seeders import SeederLog

        log = SeederLog()
        log.log_insert("clients", {"id": 1, "name": "New"})
        log.log_update("products", 2, {"price": 10.0})
        assert log.total_changes() == 2
        assert log.has_changes("clients")
        assert log.has_changes("products")
        assert not log.has_changes("orders")

    def test_has_changes_per_table(self):
        """has_changes is per-table."""
        from pgsqlasync2fast_fastapi.seeders import SeederLog

        log = SeederLog()
        log.log_insert("clients", {"id": 1, "name": "A"})
        assert log.has_changes("clients")
        assert not log.has_changes("products")

    def test_affected_tables(self):
        """affected_tables returns tables with any changes."""
        from pgsqlasync2fast_fastapi.seeders import SeederLog

        log = SeederLog()
        log.log_insert("clients", {"id": 1, "name": "A"})
        log.log_update("products", 2, {"price": 10.0})
        assert set(log.affected_tables()) == {"clients", "products"}

    def test_affected_tables_empty(self):
        """affected_tables returns empty list when no changes."""
        from pgsqlasync2fast_fastapi.seeders import SeederLog

        log = SeederLog()
        assert log.affected_tables() == []


# ============================================================
# build_fk_map: FK resolution maps
# ============================================================


class TestBuildFkMap:
    """Tests for build_fk_map() FK resolution."""

    def test_build_map_from_rows(self):
        """Builds a map of old_id -> uuid from rows."""
        from pgsqlasync2fast_fastapi.seeders import build_fk_map, seed_uuid
        from uuid import uuid5, NAMESPACE_URL

        ns = uuid5(NAMESPACE_URL, "https://test.local")
        rows = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
        fk_map = build_fk_map("clients", rows, ns)
        assert 1 in fk_map
        assert 2 in fk_map
        assert fk_map[1] == seed_uuid("clients", 1, ns)
        assert fk_map[2] == seed_uuid("clients", 2, ns)

    def test_build_map_skips_non_int_ids(self):
        """Skips rows with non-integer IDs."""
        from pgsqlasync2fast_fastapi.seeders import build_fk_map
        from uuid import uuid5, NAMESPACE_URL

        ns = uuid5(NAMESPACE_URL, "https://test.local")
        rows = [{"id": 1, "name": "A"}, {"id": "uuid-already", "name": "B"}]
        fk_map = build_fk_map("clients", rows, ns)
        assert 1 in fk_map
        assert "uuid-already" not in fk_map

    def test_build_map_empty_rows(self):
        """Returns empty map for empty rows."""
        from pgsqlasync2fast_fastapi.seeders import build_fk_map
        from uuid import uuid5, NAMESPACE_URL

        ns = uuid5(NAMESPACE_URL, "https://test.local")
        fk_map = build_fk_map("clients", [], ns)
        assert fk_map == {}

    def test_build_all_fk_maps(self):
        """build_all_fk_maps builds maps for all tables."""
        from pgsqlasync2fast_fastapi.seeders import build_all_fk_maps
        from uuid import uuid5, NAMESPACE_URL

        ns = uuid5(NAMESPACE_URL, "https://test.local")
        loaded_tables = {
            "clients": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
            "products": [{"id": 10, "name": "P1"}],
        }
        maps = build_all_fk_maps(loaded_tables, ns)
        assert "clients" in maps
        assert "products" in maps
        assert 1 in maps["clients"]
        assert 10 in maps["products"]


# ============================================================
# topological_sort: Dependency-based ordering
# ============================================================


class TestTopologicalSort:
    """Tests for topological_sort() dependency resolution."""

    def test_no_dependencies(self):
        """Tables with no dependencies can be in any order."""
        from pgsqlasync2fast_fastapi.seeders import topological_sort

        deps = {"clients": set(), "products": set(), "suppliers": set()}
        order = topological_sort(deps)
        assert set(order) == {"clients", "products", "suppliers"}

    def test_linear_dependencies(self):
        """Linear chain: A -> B -> C sorts correctly."""
        from pgsqlasync2fast_fastapi.seeders import topological_sort

        deps = {
            "a": set(),
            "b": {"a"},
            "c": {"b"},
        }
        order = topological_sort(deps)
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    def test_diamond_dependencies(self):
        """Diamond: A -> B, A -> C, B -> D, C -> D."""
        from pgsqlasync2fast_fastapi.seeders import topological_sort

        deps = {
            "a": set(),
            "b": {"a"},
            "c": {"a"},
            "d": {"b", "c"},
        }
        order = topological_sort(deps)
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_cycle_detection(self):
        """Cycle detection raises ValueError."""
        from pgsqlasync2fast_fastapi.seeders import topological_sort

        deps = {
            "a": {"b"},
            "b": {"a"},
        }
        with pytest.raises(ValueError, match="[Cc]ycl"):
            topological_sort(deps)

    def test_self_cycle_detection(self):
        """Self-cycle raises ValueError."""
        from pgsqlasync2fast_fastapi.seeders import topological_sort

        deps = {"a": {"a"}}
        with pytest.raises(ValueError, match="[Cc]ycl"):
            topological_sort(deps)

    def test_ignores_unknown_deps(self):
        """Dependencies to unknown tables are ignored."""
        from pgsqlasync2fast_fastapi.seeders import topological_sort

        deps = {
            "a": set(),
            "b": {"a", "unknown_table"},
        }
        order = topological_sort(deps)
        assert set(order) == {"a", "b"}
        assert order.index("a") < order.index("b")


# ============================================================
# resolve_fks: Rewrite integer FKs to UUIDs
# ============================================================


class TestResolveFks:
    """Tests for resolve_fks() FK rewriting."""

    def test_resolve_simple_fk(self):
        """Resolves integer FK to UUID."""
        from pgsqlasync2fast_fastapi.seeders import resolve_fks, seed_uuid
        from uuid import uuid5, NAMESPACE_URL

        ns = uuid5(NAMESPACE_URL, "https://test.local")
        fk_maps = {
            "clients": {1: seed_uuid("clients", 1, ns)},
            "products": {10: seed_uuid("products", 10, ns)},
        }
        # Simulate model columns: orders has client_id -> clients, product_id -> products
        fk_columns = {
            "client_id": "clients",
            "product_id": "products",
        }
        rows = [{"id": 1, "client_id": 1, "product_id": 10, "quantity": 5}]
        resolved = resolve_fks("orders", rows, fk_maps, fk_columns, ns)
        assert resolved[0]["client_id"] == fk_maps["clients"][1]
        assert resolved[0]["product_id"] == fk_maps["products"][10]
        assert resolved[0]["quantity"] == 5  # non-FK int unchanged

    def test_resolve_missing_fk_raises(self):
        """Missing FK reference raises ValueError."""
        from pgsqlasync2fast_fastapi.seeders import resolve_fks, seed_uuid
        from uuid import uuid5, NAMESPACE_URL

        ns = uuid5(NAMESPACE_URL, "https://test.local")
        fk_maps = {"clients": {1: seed_uuid("clients", 1, ns)}}
        fk_columns = {"client_id": "clients"}
        rows = [{"id": 1, "client_id": 999}]  # client 999 not in map
        with pytest.raises(ValueError, match="999"):
            resolve_fks("orders", rows, fk_maps, fk_columns, ns)

    def test_resolve_skips_non_fk_integers(self):
        """Non-FK integer columns are not resolved."""
        from pgsqlasync2fast_fastapi.seeders import resolve_fks, seed_uuid
        from uuid import uuid5, NAMESPACE_URL

        ns = uuid5(NAMESPACE_URL, "https://test.local")
        fk_maps = {"clients": {1: seed_uuid("clients", 1, ns)}}
        fk_columns = {"client_id": "clients"}  # only client_id is FK
        rows = [{"id": 1, "client_id": 1, "quantity": 42, "sort_order": 1}]
        resolved = resolve_fks("orders", rows, fk_maps, fk_columns, ns)
        assert resolved[0]["quantity"] == 42
        assert resolved[0]["sort_order"] == 1

    def test_resolve_replaces_id_with_uuid(self):
        """Row's own ID is replaced with deterministic UUID."""
        from pgsqlasync2fast_fastapi.seeders import resolve_fks, seed_uuid
        from uuid import uuid5, NAMESPACE_URL

        ns = uuid5(NAMESPACE_URL, "https://test.local")
        fk_maps = {}
        fk_columns = {}
        rows = [{"id": 1, "name": "Test"}]
        resolved = resolve_fks("clients", rows, fk_maps, fk_columns, ns)
        assert resolved[0]["id"] == seed_uuid("clients", 1, ns)


# ============================================================
# resolve_polymorphic_fk: Polymorphic FK resolution
# ============================================================


class TestResolvePolymorphicFk:
    """Tests for polymorphic FK resolution."""

    def test_resolve_polymorphic(self):
        """Resolves polymorphic FK based on discriminator."""
        from pgsqlasync2fast_fastapi.seeders import (
            resolve_polymorphic_fk,
            seed_uuid,
        )
        from uuid import uuid5, NAMESPACE_URL

        ns = uuid5(NAMESPACE_URL, "https://test.local")
        fk_maps = {
            "clients": {1: seed_uuid("clients", 1, ns)},
            "suppliers": {1: seed_uuid("suppliers", 1, ns)},
        }
        polymorphic_config = {
            "addressable_id": {
                "discriminator": "addressable_type",
                "map": {"client": "clients", "supplier": "suppliers"},
            }
        }
        rows = [
            {"id": 1, "addressable_id": 1, "addressable_type": "client"},
            {"id": 2, "addressable_id": 1, "addressable_type": "supplier"},
        ]
        resolved = resolve_polymorphic_fk("addresses", rows, fk_maps, polymorphic_config)
        assert resolved[0]["addressable_id"] == fk_maps["clients"][1]
        assert resolved[1]["addressable_id"] == fk_maps["suppliers"][1]

    def test_unknown_discriminator_raises(self):
        """Unknown discriminator value raises ValueError."""
        from pgsqlasync2fast_fastapi.seeders import resolve_polymorphic_fk, seed_uuid
        from uuid import uuid5, NAMESPACE_URL

        ns = uuid5(NAMESPACE_URL, "https://test.local")
        fk_maps = {"clients": {1: seed_uuid("clients", 1, ns)}}
        polymorphic_config = {
            "addressable_id": {
                "discriminator": "addressable_type",
                "map": {"client": "clients"},
            }
        }
        rows = [{"id": 1, "addressable_id": 1, "addressable_type": "unknown"}]
        with pytest.raises(ValueError, match="unknown"):
            resolve_polymorphic_fk("addresses", rows, fk_maps, polymorphic_config)
