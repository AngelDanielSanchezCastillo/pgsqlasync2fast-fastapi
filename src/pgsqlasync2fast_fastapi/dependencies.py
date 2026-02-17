"""
FastAPI dependencies for database functionality
"""

from typing import AsyncGenerator, Optional

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from .connection import DatabaseManager, get_manager
from .settings import DatabaseSettings, settings


def get_db_manager(config: DatabaseSettings = Depends(lambda: settings)) -> DatabaseManager:
    """
    FastAPI dependency for DatabaseManager.
    
    Returns singleton instance.
    
    Usage:
        @app.get("/items")
        async def get_items(manager: DatabaseManager = Depends(get_db_manager)):
            engine = manager.get_engine("default")
            ...
    """
    return get_manager(config)


def get_db_engine(
    connection_name: str = "default",
    manager: DatabaseManager = Depends(get_db_manager)
) -> AsyncEngine:
    """
    FastAPI dependency for AsyncEngine.
    
    Args:
        connection_name: Name of the database connection to use.
        manager: DatabaseManager instance (injected).
        
    Returns:
        AsyncEngine for the specified connection.
        
    Usage:
        @app.get("/items")
        async def get_items(engine: AsyncEngine = Depends(get_db_engine)):
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT * FROM items"))
                ...
    """
    return manager.get_engine(connection_name)


async def get_db_session(
    connection_name: str = "default",
    manager: DatabaseManager = Depends(get_db_manager)
) -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for AsyncSession with automatic commit/rollback.
    
    Args:
        connection_name: Name of the database connection to use.
        manager: DatabaseManager instance (injected).
        
    Yields:
        AsyncSession for the specified connection.
        
    Usage:
        @app.get("/items")
        async def get_items(session: AsyncSession = Depends(get_db_session)):
            result = await session.execute(text("SELECT * FROM items"))
            items = result.scalars().all()
            return items
            
        # Or with a specific connection:
        from functools import partial
        
        get_business_session = partial(get_db_session, connection_name="business")
        
        @app.get("/business/items")
        async def get_business_items(session: AsyncSession = Depends(get_business_session)):
            ...
    """
    session = await manager.get_session(connection_name)
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def startup_database(config: Optional[DatabaseSettings] = None) -> DatabaseManager:
    """
    Initialize database connections on application startup.
    
    Usage in FastAPI:
        @app.on_event("startup")
        async def startup():
            await startup_database()
            
    Or with lifespan (FastAPI 0.93+):
        from contextlib import asynccontextmanager
        
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            # Startup
            await startup_database()
            yield
            # Shutdown
            await shutdown_database()
        
        app = FastAPI(lifespan=lifespan)
    
    Args:
        config: Database settings (uses global settings if not provided)
        
    Returns:
        Initialized DatabaseManager instance
    """
    manager = get_manager(config)
    
    # Optionally perform health checks on all connections
    print("🔌 Initializing database connections...")
    for conn_name in manager.list_connections():
        is_healthy = await manager.health_check(conn_name)
        status = "✅" if is_healthy else "❌"
        superuser = " (superuser)" if manager.is_superuser_connection(conn_name) else ""
        print(f"  {status} Connection '{conn_name}'{superuser}")
    
    return manager


async def shutdown_database() -> None:
    """
    Close all database connections on application shutdown.
    
    Usage in FastAPI:
        @app.on_event("shutdown")
        async def shutdown():
            await shutdown_database()
    """
    manager = get_manager()
    print("🔌 Closing database connections...")
    await manager.close_all()
    print("  ✅ All connections closed")
