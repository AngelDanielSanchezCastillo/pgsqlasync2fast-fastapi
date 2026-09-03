"""
Tests for the seeder override primitive (pgsqlasync2fast-fastapi).

Covers the keyed (connection_name, package_name) registry + register_seeder(mode=)
override primitive:
- H1: an override can ADD new tables to an existing (connection, package) entry
- H2: an override can OVERWRITE an existing (connection, package) entry wholesale
- E1: a distinct (connection, package) on the same connection STILL raises
      SeederConflictError on table overlap
- T1: repeated registration of the same key is idempotent (single entry)

Run with: cd /Volumes/Desarrollo/Repos/Github/pgsqlasync2fast-fastapi \\
  && uv run pytest tests/test_seeder_override.py -v
"""

import json
import tempfile
from pathlib import Path

import pytest

from pgsqlasync2fast_fastapi.seeder import (
    SeederConfig,
    SeederConflictError,
    register_seeder,
    get_registered_seeders,
    clear_registry,
)


@pytest.fixture(autouse=True)
def clear_seeders():
    """Clear the seeder registry before and after each test."""
    clear_registry()
    yield
    clear_registry()


@pytest.fixture
def base_manifest():
    """Build a temp manifest seeding base tables on the 'auth' connection."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_dir = Path(tmpdir) / "seeders"
        dev_dir = manifest_dir / "dev"
        manifest_dir.mkdir()
        dev_dir.mkdir()

        manifest = {
            "tables": {
                "roles": {"file": "roles.json"},
                "permissions": {"file": "permissions.json"},
            },
            "load_order": ["roles", "permissions"],
        }
        manifest_path = manifest_dir / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)
        with open(dev_dir / "roles.json", "w") as f:
            json.dump([{"id": 1, "name": "Admin"}], f)
        with open(dev_dir / "permissions.json", "w") as f:
            json.dump([{"id": 1, "name": "read:users"}], f)

        yield (str(manifest_path), manifest, dev_dir)


def make_config(manifest_path, package, **kwargs) -> SeederConfig:
    """Build a SeederConfig for the given manifest and package."""
    return SeederConfig(
        connection_name="auth",
        manifest_path=manifest_path,
        package_name=package,
        model_classes=kwargs.pop("model_classes", {}),
        **kwargs,
    )


# ============================================================================
# T1 — Idempotency: same key registered twice -> single entry
# ============================================================================


class TestOverrideIdempotency:
    def test_same_key_twice_single_entry(self, base_manifest):
        manifest_path, _, dev_dir = base_manifest

        # New manifest that ADDS a table (extends the base manifest set)
        extended = {
            "tables": {
                "roles": {"file": "roles.json"},
                "permissions": {"file": "permissions.json"},
                "routes": {"file": "routes.json"},
            },
            "load_order": ["roles", "permissions", "routes"],
        }
        ext_path = Path(manifest_path).parent / "manifest_override.json"
        with open(ext_path, "w") as f:
            json.dump(extended, f)
        with open(Path(manifest_path).parent / "dev" / "routes.json", "w") as f:
            json.dump([{"id": 1, "path": "/admin", "method": "GET"}], f)

        base = make_config(manifest_path, "pkg-a",
                           model_classes={"roles": object, "permissions": object})
        override = make_config(str(ext_path), "pkg-a",
                               model_classes={"roles": object, "routes": object})

        # Register the same key twice with the default retain_base mode
        register_seeder(base)
        register_seeder(override)

        seeders = get_registered_seeders()
        assert len(seeders) == 1
        assert seeders[0].package_name == "pkg-a"


# ============================================================================
# H1 — retain_base ADD: base tables preserved, new tables added to model set
# ============================================================================


class TestOverrideAdd:
    def test_retain_base_merges_prior_model_classes(self, base_manifest):
        manifest_path, _, _ = base_manifest

        # Override manifest introduces a NEW table 'routes'
        ext_manifest = {
            "tables": {
                "roles": {"file": "roles.json"},
                "permissions": {"file": "permissions.json"},
                "routes": {"file": "routes.json"},
            },
            "load_order": ["roles", "permissions", "routes"],
        }
        ext_path = Path(manifest_path).parent / "manifest_override.json"
        with open(ext_path, "w") as f:
            json.dump(ext_manifest, f)
        with open(Path(manifest_path).parent / "dev" / "routes.json", "w") as f:
            json.dump([{"id": 1, "path": "/admin"}], f)

        class RoleModel:
            pass

        class RouteModel:
            pass

        base = make_config(manifest_path, "pkg-a",
                           model_classes={"roles": RoleModel, "permissions": object})
        override = make_config(str(ext_path), "pkg-a",
                               model_classes={"roles": RoleModel, "routes": RouteModel})

        register_seeder(base)
        register_seeder(override)

        seeders = get_registered_seeders()
        assert len(seeders) == 1
        entry = seeders[0]
        # Extends rather than replaces: the base 'permissions' model is preserved
        assert "permissions" in entry.model_classes
        # And the new 'routes' model is present
        assert entry.model_classes["routes"] is RouteModel


# ============================================================================
# H2 — replace: prior entry replaced wholesale
# ============================================================================


class TestOverrideReplace:
    def test_replace_overwrites_wholesale(self, base_manifest):
        manifest_path, _, _ = base_manifest

        ext_manifest = {
            "tables": {
                "roles": {"file": "roles.json"},
                "permissions": {"file": "permissions.json"},
                "routes": {"file": "routes.json"},
            },
            "load_order": ["roles", "permissions", "routes"],
        }
        ext_path = Path(manifest_path).parent / "manifest_override.json"
        with open(ext_path, "w") as f:
            json.dump(ext_manifest, f)
        with open(Path(manifest_path).parent / "dev" / "routes.json", "w") as f:
            json.dump([{"id": 1, "path": "/admin"}], f)

        class RouteModel:
            pass

        base = make_config(manifest_path, "pkg-a",
                           model_classes={"roles": object, "permissions": object})
        override = make_config(str(ext_path), "pkg-a",
                               model_classes={"routes": RouteModel}, priority=90)

        register_seeder(base)
        # SeederConflictError must NOT be raised for same-key replace
        register_seeder(override, mode="replace")

        seeders = get_registered_seeders()
        assert len(seeders) == 1
        entry = seeders[0]
        # Wholesale replace: only the new model set survives
        assert "routes" in entry.model_classes
        assert "permissions" not in entry.model_classes
        assert entry.priority == 90


# ============================================================================
# E1 — True conflict: distinct package, same connection, overlapping tables
# ============================================================================


class TestOverrideConflict:
    def test_distinct_package_same_connection_still_conflicts(self, base_manifest):
        manifest_path, _, _ = base_manifest

        # A second, DIFFERENT package on the same 'auth' connection that
        # overlaps on the 'roles' table -> true conflict
        conflict_manifest = {
            "tables": {"roles": {"file": "roles.json"}},
            "load_order": ["roles"],
        }
        conflict_path = Path(manifest_path).parent / "manifest_conflict.json"
        with open(conflict_path, "w") as f:
            json.dump(conflict_manifest, f)

        register_seeder(make_config(manifest_path, "pkg-a"))

        with pytest.raises(SeederConflictError) as exc_info:
            register_seeder(make_config(str(conflict_path), "pkg-b"))

        assert "roles" in str(exc_info.value)
        assert "pkg-a" in str(exc_info.value)
        assert "pkg-b" in str(exc_info.value)


# ============================================================================
# Sanity: registry remains keyed -> distinct keys stay separate entries
# ============================================================================


class TestOverrideKeying:
    def test_distinct_keys_two_entries(self, base_manifest):
        manifest_path, _, dev_dir = base_manifest

        other = {
            "tables": {"roles": {"file": "roles.json"}},
            "load_order": ["roles"],
        }
        other_path = Path(manifest_path).parent / "manifest_other.json"
        with open(other_path, "w") as f:
            json.dump(other, f)
        with open(dev_dir / "roles.json", "w") as f:
            json.dump([{"id": 1, "name": "Admin"}], f)

        # Distinct package on a DIFFERENT connection -> no conflict, two entries
        register_seeder(make_config(manifest_path, "pkg-a"))
        register_seeder(SeederConfig(
            connection_name="business",
            manifest_path=str(other_path),
            package_name="pkg-b",
        ))

        seeders = get_registered_seeders()
        assert len(seeders) == 2
