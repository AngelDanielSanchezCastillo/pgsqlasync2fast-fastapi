"""
Database creation example for pgsqlasync2fast-fastapi

This example demonstrates:
- Checking if a database exists
- Creating new databases
- Listing all databases
- Dropping databases

IMPORTANT: This requires a connection configured with is_superuser=true
"""

import asyncio
from pgsqlasync2fast_fastapi import (
    database_exists,
    create_database,
    drop_database,
    list_databases,
    get_manager,
)


async def main():
    """Main function demonstrating database creation utilities."""
    
    print("📊 Database Creation Example")
    print("=" * 50)
    
    # Check if we have a superuser connection
    manager = get_manager()
    superuser_conn = manager.config.get_superuser_connection_name()
    
    if not superuser_conn:
        print("❌ No superuser connection configured!")
        print("   Please configure a connection with IS_SUPERUSER=true")
        print("   Example: DB_CONNECTIONS__ADMIN__IS_SUPERUSER=true")
        return
    
    print(f"✅ Using superuser connection: '{superuser_conn}'")
    
    # List all existing databases
    print("\n📋 Listing all databases...")
    databases = await list_databases()
    for db in databases:
        print(f"  - {db}")
    
    # Test database name
    test_db_name = "test_created_db"
    
    # Check if test database exists
    print(f"\n🔍 Checking if '{test_db_name}' exists...")
    exists = await database_exists(test_db_name)
    print(f"  {'✅ Exists' if exists else '❌ Does not exist'}")
    
    # Create the database if it doesn't exist
    if not exists:
        print(f"\n🔨 Creating database '{test_db_name}'...")
        created = await create_database(test_db_name)
        if created:
            print(f"  ✅ Database '{test_db_name}' created successfully!")
        else:
            print(f"  ❌ Failed to create database")
    else:
        print(f"\n⚠️  Database '{test_db_name}' already exists, skipping creation")
    
    # Verify it exists now
    print(f"\n🔍 Verifying '{test_db_name}' exists...")
    exists = await database_exists(test_db_name)
    print(f"  {'✅ Confirmed' if exists else '❌ Not found'}")
    
    # List databases again to show the new one
    print("\n📋 Listing all databases (after creation)...")
    databases = await list_databases()
    for db in databases:
        marker = " ← NEW" if db == test_db_name else ""
        print(f"  - {db}{marker}")
    
    # Optional: Drop the test database
    print(f"\n🗑️  Cleaning up: dropping '{test_db_name}'...")
    dropped = await drop_database(test_db_name, force=True)
    if dropped:
        print(f"  ✅ Database '{test_db_name}' dropped successfully!")
    else:
        print(f"  ❌ Failed to drop database")
    
    # Cleanup
    print("\n🔌 Closing connections...")
    await manager.close_all()
    print("✅ Done!")


if __name__ == "__main__":
    asyncio.run(main())
