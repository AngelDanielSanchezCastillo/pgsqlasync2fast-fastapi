# Seeder Format Documentation

Multi-package JSON seeder format for `pgsqlasync2fast-fastapi`.

## Overview

The seeder system uses JSON files organized by profile (e.g., `dev`, `prod`) with a manifest.json defining table structure and dependencies.

## Directory Structure

```
seeders/
├── manifest.json           # Table definitions and load order
├── dev/                    # Development profile
│   ├── roles.json
│   ├── categories.json
│   └── permissions.json
└── prod/                   # Production profile
    ├── roles.json
    ├── categories.json
    └── permissions.json
```

## manifest.json

The manifest defines which tables are seeded and their dependencies.

```json
{
  "tables": {
    "categories": {
      "file": "categories.json"
    },
    "roles": {
      "file": "roles.json"
    },
    "permissions": {
      "file": "permissions.json",
      "depends_on": ["categories"]
    }
  },
  "load_order": ["categories", "roles", "permissions"]
}
```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tables` | object | Yes | Dictionary of table configurations |
| `tables.{name}` | object | Yes | Configuration for each table |
| `tables.{name}.file` | string | Yes | JSON filename containing the data |
| `tables.{name}.depends_on` | array | No | Array of table names this table depends on |
| `load_order` | array | No | Explicit load order (overrides dependency-based sort) |

## Table JSON Files

Each table has its own JSON file containing an array of row objects.

### Format

```json
[
  {
    "id": 1,
    "name": "Admin",
    "description": "Administrator role"
  },
  {
    "id": 2,
    "name": "User",
    "description": "Regular user role"
  }
]
```

### Requirements

- **Required fields**: Each row MUST have an `id` field (integer or string)
- **Data types**: JSON primitives (string, number, boolean, null)
- **Relationships**: Use explicit IDs to reference related entities

## Profiles

Profiles allow the same table structure with different data for different environments.

### Common Profiles

- `dev` - Development data (more records, test data)
- `prod` - Production data (minimal, realistic data)

### Profile Resolution

When calling `seed_all("dev")`, the system looks for data in `{manifest_dir}/dev/` folder.

## Foreign Key (FK) Validation

Before any inserts, the system validates that FK references point to existing IDs.

### FK Naming Convention

The system uses a simple naming convention:
- Table `categories` → FK field `category_id`
- Table `roles` → FK field `role_id`
- Table `permissions` → FK field `permission_id`

### Example

If `permissions.json` contains:
```json
[
  {
    "id": 1,
    "name": "read:users",
    "category_id": 1
  }
]
```

The system validates that a category with `id=1` exists in `categories.json` before inserting.

### Validation Errors

If validation fails, `SeedValidationError` is raised with details:
```
Table 'permissions' row id=1 references non-existent categories id=999
```

## Priority

Packages have a `priority` value (default: 50) that determines execution order.

- **Lower values** = seeds first
- **Higher values** = seeds later

This allows controlling which package's data takes precedence when multiple packages are registered.

### Example

```python
register_seeder(SeederConfig(
    connection_name="auth",
    manifest_path="path/to/manifest.json",
    package_name="my-package",
    priority=50  # Seeds before packages with higher priority
))
```

## Conflict Detection

When registering a seeder, the system detects table conflicts between **distinct** packages.

### Rule

Two DIFFERENT packages (`(connection_name, package_name)` keys) CANNOT seed the same table in the same connection. Re-registering the same key is an override, never a conflict.

### Error

If a distinct package's tables overlap an already-registered package on the same connection, `SeederConflictError` is raised:
```
Tables {'roles'} conflict between 'pkg-a' and 'pkg-b' on connection 'auth'
```

## Override & Idempotency (single-entry)

The registry is keyed by `(connection_name, package_name)`, so registering the same package+connection repeatedly updates **one single entry** — no duplicate registry entries.

`register_seeder(config, mode=...)` supports two override behaviors:

| `mode` | Behavior |
|--------|----------|
| `"retain_base"` (default) | Replaces the entry's config fields but **merges** the prior entry's manifest `model_classes` into the new config's set. Base tables are preserved when an app extends a package seeder (e.g. keeps permissions2fast base tables while adding route tables). |
| `"replace"` | Replaces the prior entry wholesale. |

Re-registering the **same** `(connection, package)` key never raises `SeederConflictError`. Only a **distinct** package on the same connection with overlapping tables raises it.

### Example

```python
# Base permissions2fast seeder auto-registered on "auth"
register_seeder(SeederConfig(
    connection_name="auth",
    manifest_path="permissions2fast_fastapi/seeders/manifest.json",
    package_name="permissions2fast-fastapi",
    priority=60,
))

# App extends it by ADDING route tables, keeping the base manifest set
register_seeder(SeederConfig(
    connection_name="auth",
    manifest_path="app/rbac_route_manifest.json",
    package_name="permissions2fast-fastapi",   # same key -> override
    model_classes={"routes": RouteModel},
), mode="retain_base")
```

The result is a single `permissions2fast-fastapi` entry whose `model_classes` contain both the base `roles`/`permissions` models and the new `routes` model.

## Idempotency

The seeder is idempotent - running multiple times produces the same result.

- Rows with existing IDs are skipped
- Only new rows are inserted
- Registry registration is idempotent per key - no duplicate entries

This allows safe re-runs without duplicating data.

## Usage Example

```python
from pgsqlasync2fast_fastapi import seed_all, register_seeder, SeederConfig

# Register a package's seeder
register_seeder(SeederConfig(
    connection_name="auth",
    manifest_path="my_package/seeders/manifest.json",
    package_name="my-package",
    priority=50
))

# Execute all seeders with dev profile
result = await seed_all("dev")

print(f"Seeded {result.tables_seeded} tables, {result.rows_seeded} rows")
```

## Seed Flow

1. **Register**: `register_seeder()` validates no conflicts
2. **Resolve Order**: Tables sorted by `depends_on` + `priority`
3. **Load Data**: Each table's JSON loaded from profile folder
4. **Validate FKs**: All FK references verified before inserts
5. **Seed**: Idempotent inserts (skip existing IDs)
6. **Result**: Summary of seeded packages, tables, and rows