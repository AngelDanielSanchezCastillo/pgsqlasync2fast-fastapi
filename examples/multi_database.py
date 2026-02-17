"""
Multi-database example for pgsqlasync2fast-fastapi

This example demonstrates:
- Configuring multiple database connections
- Switching between databases
- Using different connections simultaneously
"""

import asyncio
from sqlalchemy import text
from pgsqlasync2fast_fastapi import get_manager


async def main():
    """Main function demonstrating multi-database usage."""
    
    manager = get_manager()
    
    print("📊 Multi-Database Usage Example")
    print("=" * 50)
    
    # List all configured connections
    connections = manager.list_connections()
    print(f"\n✅ Available connections: {connections}")
    
    # Demonstrate using multiple connections
    for conn_name in connections:
        print(f"\n🔌 Connecting to '{conn_name}' database...")
        
        try:
            # Health check
            is_healthy = await manager.health_check(conn_name)
            if not is_healthy:
                print(f"❌ Connection '{conn_name}' is not healthy")
                continue
            
            # Get a session for this connection
            session = await manager.get_session(conn_name)
            
            try:
                # Get database info
                result = await session.execute(text("SELECT current_database()"))
                db_name = result.scalar()
                
                result = await session.execute(text("SELECT current_user"))
                user = result.scalar()
                
                is_super = manager.is_superuser_connection(conn_name)
                super_flag = " (SUPERUSER)" if is_super else ""
                
                print(f"✅ Database: {db_name}")
                print(f"✅ User: {user}{super_flag}")
                
                await session.commit()
                
            finally:
                await session.close()
                
        except Exception as e:
            print(f"❌ Error with connection '{conn_name}': {e}")
    
    # Cleanup
    print("\n🔌 Closing all connections...")
    await manager.close_all()
    print("✅ Done!")


if __name__ == "__main__":
    asyncio.run(main())
