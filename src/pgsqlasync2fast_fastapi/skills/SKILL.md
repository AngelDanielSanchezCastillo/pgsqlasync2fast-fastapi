---
name: pgsqlasync2fast-fastapi
description: "Trigger: working on or with pgsqlasync2fast-fastapi. Multi-database async engine manager for FastAPI: DB_CONNECTIONS config, get_db_session deps, seeder orchestrator, tenant engines. Prevails over the 2fast-handbook base skill for this package."
license: MIT
metadata:
  author: AngelDanielSanchezCastillo
  version: "2.1"
---

## Purpose

Connection-management foundation of the 2fast stack: multiple PostgreSQL
engines/session factories keyed by connection name (`default`, `auth`,
`business`, `tenant_{id}`), lifecycle, and the generic seeder orchestrator.

## Import quirk

- Dist `pgsqlasync2fast-fastapi` → import `pgsqlasync2fast_fastapi` (dash→underscore only).
- Two "default connection" paths DISAGREE: `get_db_session`/`get_db_engine` default `connection_name="default"` (literal string), while `DatabaseManager.get_engine`/`get_connection` fall back `None → config.default_connection`. Never rely on `"default"`: name your primary `default` or always pass an explicit name via `functools.partial` (oauth2fast pattern: `get_auth_session = partial(get_db_session, connection_name="auth")`).

## Public API

- `get_manager(config=None)` — **global singleton**; honors a custom config only on its FIRST call. `get_db_manager` (FastAPI dep) always injects the module `settings`, so per-app config via the dep path is impossible.
- `get_db_engine(connection_name="default")`, `get_db_session(connection_name="default")` (yields session; commits on success, rolls back on exception, closes in finally).
- `startup_database()` — **NOT lazy**: eagerly creates and health-checks EVERY configured connection. README's "lazy loading" is wrong.
- `shutdown_database()` → `manager.close_all()` (disposes all engines).
- DB ops `database_exists/create_database/drop_database/list_databases` — ALL require a superuser connection (`is_superuser=True`; first configured one wins); use AUTOCOMMIT isolation.
- Seeder: `SeederConfig`, `register_seeder`, `seed_all`, `get_registered_seeders`, `SeederConflictError`.
- `insert_if_missing(session, model, lookup, defaults=None)` — **shared idempotent
  insert-if-missing** primitive: SELECT by natural-key `lookup` fields, return the
  existing row if present or insert (merge `lookup`+`defaults`) and flush if absent.
  Consumer packages (permissions2fast GLOBAL routes, tenants2fast TENANT routes)
  reuse this instead of hand-rolling per package (RBAC standardization D2). No
  commit — caller owns the transaction boundary. Also re-exported from the top-level
  `pgsqlasync2fast_fastapi` package.

## Architecture

- `DatabaseSettings.connections: dict[str, DatabaseConnectionSettings]` — any lowercase name is a legal key (env `DB_CONNECTIONS__{NAME}__*`).
- Engine: `create_async_engine(url, echo, pool_size=5, max_overflow=10, pool_timeout=30, pool_recycle=3600, pool_pre_ping=True)` (pre_ping hard-coded). URL: `postgresql+asyncpg://user:pass@host:port/db`.
- Each engine has a parallel `async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)` (SQLModel session — supports BOTH `session.execute` and `session.exec`).
- `echo` resolution QUIRK: `conn.echo if conn.echo else global echo` — a connection-level `echo=False` cannot override a global `echo=True`.

## Dynamic/tenant engines (private contract)

- There is **no public API to register an engine at runtime**. tenants2fast writes the manager's PRIVATE (`manager._engines`, `manager._session_makers`, `manager.config.connections`) directly. No LRU, no cap — dynamic engines accumulate unboundedly (N tenants × up to 15 connections each). Dispose idle tenant engines explicitly.
- `get_manager(config)` honors custom config only once; the FastAPI dep always passes module settings.

## Wiring

```python
from pgsqlasync2fast_fastapi import get_db_session, startup_database, shutdown_database
# startup(shutdown) events: await startup_database() / await shutdown_database()
session: AsyncSession = Depends(get_db_session)  # or partial with connection_name
```

Downstream imports (stable contracts): oauth2fast `get_db_session`→`partial(..., connection_name="auth")`; permissions2fast `SeederConfig, register_seeder`; tenants2fast `create_database, drop_database`, `connection.get_manager`, `settings.settings, DatabaseConnectionSettings`.

## Seeder orchestrator

- The seeder registry is **keyed by `(connection_name, package_name)`** — re-registering the same key updates that single entry in place (idempotent, no duplicates).
- `register_seeder(config, mode="retain_base")` is the **override primitive**:
  - `mode="retain_base"` (default, backward-compatible): an existing same-key entry's config fields are replaced but its prior manifest `model_classes` are **merged** into the new config's set — base tables are preserved when an app extends a package seeder.
  - `mode="replace"`: the prior same-key entry is replaced wholesale.
  - Re-registering the same key NEVER raises `SeederConflictError`.
- Registration-time **table-conflict detection** applies only to *distinct* `(connection, package)` keys: `SeederConflictError` if two different packages on the same connection share a manifest `tables` key.
- `seed_all(profile, package_filter=None)` sorts registered values by `priority` (LOWER first). **Skips** registered seeders with `is_tenant_seeder=True` and no `seed_fn` (warns — tenant seeding must be driven by `seed_all_tenants`/the tenant package itself).
- Generic path: topological sort of `depends_on` (cycle → `SeedValidationError`), FK resolution `fk_field_mapping`/`fk_fields`/`rstrip('s')+'_id'`, insert **per row** with explicit `id` → idempotent (SELECT by id, skip if exists, commit per row).

## Settings

`DatabaseSettings`, prefix `DB_`, nested `__`, `.env` from **process CWD**. Top: `DB_DEFAULT_CONNECTION` (default "default"), `DB_ECHO` (False). Per connection: `HOST, USERNAME, PASSWORD, DATABASE` (**all required** — import crashes otherwise), `PORT` (5432), `IS_SUPERUSER` (False), `POOL_SIZE` (5), `MAX_OVERFLOW` (10), `POOL_TIMEOUT` (30), `POOL_RECYCLE` (3600), `ECHO` (False). Missing `.env` does NOT crash at import — it yields an empty manager that fails on first `get_engine` (plus a Spanish warning).

## Gotchas

- `create_database`/`drop_database` require a configured superuser connection.
- Engines are disposed only by `shutdown_database`/`close_all` — or by tenants2fast's `dispose_tenant_engine` for individual tenant engines; dropping a tenant DB without disposing first leaks connections.

## Golden rule (inherited)

Follow the 2fast-handbook base skill for layout/versioning/naming/README/commits/release.
Local edits are fine; NEVER bump/publish on your own — prepare the exact command and hand it to the developer.