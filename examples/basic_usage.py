"""
Basic usage example for pgsqlasync2fast-fastapi

This example demonstrates:
- Basic database connection
- Executing queries
- Using sessions
"""

import asyncio
from sqlalchemy import text
from pgsqlasync2fast_fastapi import get_manager, settings


async def main():
    """Main function demonstrating basic usage."""
    
    # Get the database manager
    manager = get_manager()
    
    print("📊 Basic PostgreSQL Async Usage Example")
    print("=" * 50)
    
    # List configured connections
    print(f"\n✅ Configured connections: {manager.list_connections()}")
    
    # Get a session for the default connection
    print(f"\n🔌 Connecting to default database...")
    session = await manager.get_session()
    
    try:
        # Execute a simple query
        result = await session.execute(text("SELECT version()"))
        version = result.scalar()
        print(f"✅ PostgreSQL version: {version}")
        
        # Execute another query
        result = await session.execute(text("SELECT current_database()"))
        db_name = result.scalar()
        print(f"✅ Current database: {db_name}")
        
        # Commit the session
        await session.commit()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        await session.rollback()
    finally:
        await session.close()
    
    # Cleanup
    print("\n🔌 Closing connections...")
    await manager.close_all()
    print("✅ Done!")


if __name__ == "__main__":
    asyncio.run(main())
