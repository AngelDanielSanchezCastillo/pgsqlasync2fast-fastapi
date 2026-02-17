# pgsqlasync2fast-fastapi

Simple and fast PostgreSQL async module for FastAPI with multi-database support and automatic database creation.

## Features

- ✅ **Multiple Database Connections**: Configure and manage multiple PostgreSQL databases
- ✅ **Async Support**: Built on SQLAlchemy 2.0+ async engine with asyncpg
- ✅ **Database Creation**: Automatic database creation with superuser support
- ✅ **FastAPI Integration**: Ready-to-use dependencies for seamless FastAPI integration
- ✅ **Connection Pooling**: Configurable connection pools per database
- ✅ **Health Checks**: Built-in connection health monitoring
- ✅ **Lazy Loading**: Engines created only when needed
- ✅ **Type Safe**: Full Pydantic settings with validation

## Installation

```bash
pip install pgsqlasync2fast-fastapi
```

## Quick Start

### 1. Configure your databases in `.env`

```env
# Default database connection
DB_CONNECTIONS__DEFAULT__HOST=localhost
DB_CONNECTIONS__DEFAULT__PORT=5432
DB_CONNECTIONS__DEFAULT__USERNAME=myuser
DB_CONNECTIONS__DEFAULT__PASSWORD=mypassword
DB_CONNECTIONS__DEFAULT__DATABASE=mydb

# Business database connection
DB_CONNECTIONS__BUSINESS__HOST=localhost
DB_CONNECTIONS__BUSINESS__PORT=5432
DB_CONNECTIONS__BUSINESS__USERNAME=business_user
DB_CONNECTIONS__BUSINESS__PASSWORD=business_password
DB_CONNECTIONS__BUSINESS__DATABASE=business_db

# Admin connection with superuser privileges
DB_CONNECTIONS__ADMIN__HOST=localhost
DB_CONNECTIONS__ADMIN__PORT=5432
DB_CONNECTIONS__ADMIN__USERNAME=postgres
DB_CONNECTIONS__ADMIN__PASSWORD=postgres_password
DB_CONNECTIONS__ADMIN__DATABASE=postgres
DB_CONNECTIONS__ADMIN__IS_SUPERUSER=true
```

### 2. Use in FastAPI

```python
from fastapi import FastAPI, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from pgsqlasync2fast_fastapi import (
    get_db_session,
    startup_database,
    shutdown_database
)

app = FastAPI()

@app.on_event("startup")
async def startup():
    await startup_database()

@app.on_event("shutdown")
async def shutdown():
    await shutdown_database()

@app.get("/users")
async def get_users(session: AsyncSession = Depends(get_db_session)):
    result = await session.execute(text("SELECT * FROM users"))
    users = result.fetchall()
    return {"users": users}
```

### 3. Use Multiple Databases

```python
from functools import partial
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pgsqlasync2fast_fastapi import get_db_session

# Create dependency for business database
get_business_session = partial(get_db_session, connection_name="business")

@app.get("/business/data")
async def get_business_data(session: AsyncSession = Depends(get_business_session)):
    result = await session.execute(text("SELECT * FROM business_data"))
    return {"data": result.fetchall()}
```

### 4. Create Databases Programmatically

```python
from pgsqlasync2fast_fastapi import create_database, database_exists

# Create a new database (requires a connection with is_superuser=true)
if not await database_exists("new_database"):
    await create_database("new_database", owner="myuser")
    print("Database created!")
```

## Configuration

### Environment Variables

All configuration is done through environment variables with the prefix `DB_`:

#### Connection Settings

```env
DB_CONNECTIONS__<NAME>__HOST=localhost          # Database host
DB_CONNECTIONS__<NAME>__PORT=5432               # Database port
DB_CONNECTIONS__<NAME>__USERNAME=user           # Database username
DB_CONNECTIONS__<NAME>__PASSWORD=pass           # Database password
DB_CONNECTIONS__<NAME>__DATABASE=dbname         # Database name
DB_CONNECTIONS__<NAME>__IS_SUPERUSER=false      # Superuser privileges
```

#### Pool Settings (Optional)

```env
DB_CONNECTIONS__<NAME>__POOL_SIZE=5             # Connection pool size
DB_CONNECTIONS__<NAME>__MAX_OVERFLOW=10         # Max overflow connections
DB_CONNECTIONS__<NAME>__POOL_TIMEOUT=30         # Pool timeout in seconds
DB_CONNECTIONS__<NAME>__POOL_RECYCLE=3600       # Recycle connections after seconds
```

#### Global Settings

```env
DB_DEFAULT_CONNECTION=default                    # Default connection name
DB_ECHO=false                                    # Global SQLAlchemy echo mode
```

## API Reference

### Dependencies

#### `get_db_session(connection_name: str = "default")`

FastAPI dependency that provides an async database session with automatic commit/rollback.

```python
@app.get("/items")
async def get_items(session: AsyncSession = Depends(get_db_session)):
    result = await session.execute(text("SELECT * FROM items"))
    return result.fetchall()
```

#### `get_db_engine(connection_name: str = "default")`

FastAPI dependency that provides the async engine for a connection.

```python
@app.get("/health")
async def health_check(engine: AsyncEngine = Depends(get_db_engine)):
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "healthy"}
```

#### `get_db_manager()`

FastAPI dependency that provides the DatabaseManager singleton.

```python
@app.get("/connections")
async def list_connections(manager: DatabaseManager = Depends(get_db_manager)):
    return {"connections": manager.list_connections()}
```

### Database Utilities

#### `create_database(database_name: str, owner: Optional[str] = None, connection_name: Optional[str] = None) -> bool`

Create a new database using a superuser connection.

```python
created = await create_database("my_new_db", owner="myuser")
if created:
    print("Database created successfully!")
```

#### `database_exists(database_name: str, connection_name: Optional[str] = None) -> bool`

Check if a database exists.

```python
if await database_exists("my_db"):
    print("Database exists!")
```

#### `drop_database(database_name: str, connection_name: Optional[str] = None, force: bool = False) -> bool`

Drop a database (requires superuser).

```python
dropped = await drop_database("old_db", force=True)
```

#### `list_databases(connection_name: Optional[str] = None) -> List[str]`

List all databases.

```python
databases = await list_databases()
for db in databases:
    print(f"  - {db}")
```

### Startup/Shutdown

#### `startup_database(config: Optional[DatabaseSettings] = None) -> DatabaseManager`

Initialize database connections on application startup.

```python
@app.on_event("startup")
async def startup():
    await startup_database()
```

#### `shutdown_database() -> None`

Close all database connections on application shutdown.

```python
@app.on_event("shutdown")
async def shutdown():
    await shutdown_database()
```

## Examples

See the `examples/` directory for complete working examples:

- `basic_usage.py` - Basic database connection and queries
- `multi_database.py` - Using multiple database connections
- `fastapi_integration.py` - Complete FastAPI application
- `database_creation.py` - Creating databases programmatically

## License

MIT License - see LICENSE file for details.

## Author

Angel Daniel Sanchez Castillo - angeldaniel.sanchezcastillo@gmail.com
