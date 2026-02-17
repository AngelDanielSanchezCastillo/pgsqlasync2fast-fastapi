"""
FastAPI integration example for pgsqlasync2fast-fastapi

This example demonstrates:
- FastAPI application setup
- Dependency injection
- Startup/shutdown events
- Using multiple database connections in endpoints
"""

from functools import partial
from fastapi import FastAPI, Depends
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine
from sqlalchemy import text

from pgsqlasync2fast_fastapi import (
    get_db_session,
    get_db_engine,
    get_db_manager,
    startup_database,
    shutdown_database,
    DatabaseManager,
)

# Create FastAPI app
app = FastAPI(
    title="PostgreSQL Async Example",
    description="Example FastAPI application with pgsqlasync2fast-fastapi"
)


@app.on_event("startup")
async def startup():
    """Initialize database connections on startup."""
    await startup_database()


@app.on_event("shutdown")
async def shutdown():
    """Close database connections on shutdown."""
    await shutdown_database()


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "PostgreSQL Async FastAPI Example",
        "endpoints": [
            "/health",
            "/connections",
            "/db/info",
            "/db/version",
        ]
    }


@app.get("/health")
async def health_check(engine: AsyncEngine = Depends(get_db_engine)):
    """Health check endpoint using default database."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "default"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


@app.get("/connections")
async def list_connections(manager: DatabaseManager = Depends(get_db_manager)):
    """List all configured database connections."""
    connections = manager.list_connections()
    
    connection_info = []
    for conn_name in connections:
        is_super = manager.is_superuser_connection(conn_name)
        connection_info.append({
            "name": conn_name,
            "is_superuser": is_super
        })
    
    return {
        "total": len(connections),
        "connections": connection_info
    }


@app.get("/db/info")
async def get_db_info(session: AsyncSession = Depends(get_db_session)):
    """Get database information using default connection."""
    try:
        # Get database name
        result = await session.execute(text("SELECT current_database()"))
        db_name = result.scalar()
        
        # Get current user
        result = await session.execute(text("SELECT current_user"))
        user = result.scalar()
        
        # Get server version
        result = await session.execute(text("SELECT version()"))
        version = result.scalar()
        
        return {
            "database": db_name,
            "user": user,
            "version": version
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/db/version")
async def get_version(session: AsyncSession = Depends(get_db_session)):
    """Get PostgreSQL version."""
    result = await session.execute(text("SELECT version()"))
    version = result.scalar()
    return {"version": version}


# Example: Using a specific database connection
# Create a dependency for a specific connection (e.g., "business")
get_business_session = partial(get_db_session, connection_name="business")


@app.get("/business/info")
async def get_business_info(session: AsyncSession = Depends(get_business_session)):
    """Get database information using business connection."""
    try:
        result = await session.execute(text("SELECT current_database()"))
        db_name = result.scalar()
        
        result = await session.execute(text("SELECT current_user"))
        user = result.scalar()
        
        return {
            "connection": "business",
            "database": db_name,
            "user": user
        }
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    
    print("🚀 Starting FastAPI application...")
    print("📖 API docs available at: http://localhost:8000/docs")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
