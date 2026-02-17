"""
Database creation and management utilities
"""

from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .connection import get_manager
from .settings import settings


async def database_exists(
    database_name: str,
    connection_name: Optional[str] = None
) -> bool:
    """
    Check if a database exists.
    
    Args:
        database_name: Name of the database to check.
        connection_name: Name of the superuser connection to use.
                        If None, uses the first connection with is_superuser=True.
        
    Returns:
        True if database exists, False otherwise.
        
    Raises:
        ValueError: If no superuser connection is available.
        
    Example:
        exists = await database_exists("my_new_db")
        if not exists:
            await create_database("my_new_db")
    """
    manager = get_manager()
    
    # Find superuser connection
    if connection_name is None:
        connection_name = settings.get_superuser_connection_name()
        if connection_name is None:
            raise ValueError(
                "No superuser connection available. "
                "Please configure a connection with is_superuser=true"
            )
    
    # Verify the connection has superuser privileges
    if not manager.is_superuser_connection(connection_name):
        raise ValueError(
            f"Connection '{connection_name}' does not have superuser privileges. "
            f"Set is_superuser=true in the connection configuration."
        )
    
    engine = manager.get_engine(connection_name)
    
    async with engine.connect() as conn:
        # Use isolation_level to allow database operations
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        
        result = await conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :dbname"),
            {"dbname": database_name}
        )
        return result.scalar() is not None


async def create_database(
    database_name: str,
    owner: Optional[str] = None,
    connection_name: Optional[str] = None
) -> bool:
    """
    Create a new database.
    
    Args:
        database_name: Name of the database to create.
        owner: Optional owner username for the database.
        connection_name: Name of the superuser connection to use.
                        If None, uses the first connection with is_superuser=True.
        
    Returns:
        True if database was created, False if it already exists.
        
    Raises:
        ValueError: If no superuser connection is available.
        Exception: If database creation fails.
        
    Example:
        created = await create_database("my_new_db", owner="myuser")
        if created:
            print("Database created successfully!")
    """
    manager = get_manager()
    
    # Find superuser connection
    if connection_name is None:
        connection_name = settings.get_superuser_connection_name()
        if connection_name is None:
            raise ValueError(
                "No superuser connection available. "
                "Please configure a connection with is_superuser=true"
            )
    
    # Verify the connection has superuser privileges
    if not manager.is_superuser_connection(connection_name):
        raise ValueError(
            f"Connection '{connection_name}' does not have superuser privileges. "
            f"Set is_superuser=true in the connection configuration."
        )
    
    # Check if database already exists
    if await database_exists(database_name, connection_name):
        print(f"⚠️  Database '{database_name}' already exists")
        return False
    
    engine = manager.get_engine(connection_name)
    
    async with engine.connect() as conn:
        # Use AUTOCOMMIT isolation level for CREATE DATABASE
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        
        # Build CREATE DATABASE query
        if owner:
            query = text(f'CREATE DATABASE "{database_name}" OWNER "{owner}"')
        else:
            query = text(f'CREATE DATABASE "{database_name}"')
        
        await conn.execute(query)
        print(f"✅ Database '{database_name}' created successfully")
        return True


async def drop_database(
    database_name: str,
    connection_name: Optional[str] = None,
    force: bool = False
) -> bool:
    """
    Drop a database.
    
    Args:
        database_name: Name of the database to drop.
        connection_name: Name of the superuser connection to use.
                        If None, uses the first connection with is_superuser=True.
        force: If True, terminates all connections to the database before dropping.
        
    Returns:
        True if database was dropped, False if it doesn't exist.
        
    Raises:
        ValueError: If no superuser connection is available or attempting to drop
                   a protected database (postgres, template0, template1).
        Exception: If database drop fails.
        
    Warning:
        This operation is irreversible! Use with caution.
        
    Example:
        dropped = await drop_database("old_db", force=True)
        if dropped:
            print("Database dropped successfully!")
    """
    # Safety check: prevent dropping system databases
    protected_databases = ["postgres", "template0", "template1"]
    if database_name in protected_databases:
        raise ValueError(
            f"Cannot drop protected database '{database_name}'. "
            f"Protected databases: {', '.join(protected_databases)}"
        )
    
    manager = get_manager()
    
    # Find superuser connection
    if connection_name is None:
        connection_name = settings.get_superuser_connection_name()
        if connection_name is None:
            raise ValueError(
                "No superuser connection available. "
                "Please configure a connection with is_superuser=true"
            )
    
    # Verify the connection has superuser privileges
    if not manager.is_superuser_connection(connection_name):
        raise ValueError(
            f"Connection '{connection_name}' does not have superuser privileges. "
            f"Set is_superuser=true in the connection configuration."
        )
    
    # Check if database exists
    if not await database_exists(database_name, connection_name):
        print(f"⚠️  Database '{database_name}' does not exist")
        return False
    
    engine = manager.get_engine(connection_name)
    
    async with engine.connect() as conn:
        # Use AUTOCOMMIT isolation level for DROP DATABASE
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        
        # Terminate existing connections if force=True
        if force:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pg_stat_activity.pid) "
                    "FROM pg_stat_activity "
                    "WHERE pg_stat_activity.datname = :dbname "
                    "AND pid <> pg_backend_pid()"
                ),
                {"dbname": database_name}
            )
        
        # Drop the database
        await conn.execute(text(f'DROP DATABASE "{database_name}"'))
        print(f"✅ Database '{database_name}' dropped successfully")
        return True


async def list_databases(connection_name: Optional[str] = None) -> List[str]:
    """
    List all databases.
    
    Args:
        connection_name: Name of the superuser connection to use.
                        If None, uses the first connection with is_superuser=True.
        
    Returns:
        List of database names.
        
    Raises:
        ValueError: If no superuser connection is available.
        
    Example:
        databases = await list_databases()
        for db in databases:
            print(f"  - {db}")
    """
    manager = get_manager()
    
    # Find superuser connection
    if connection_name is None:
        connection_name = settings.get_superuser_connection_name()
        if connection_name is None:
            raise ValueError(
                "No superuser connection available. "
                "Please configure a connection with is_superuser=true"
            )
    
    # Verify the connection has superuser privileges
    if not manager.is_superuser_connection(connection_name):
        raise ValueError(
            f"Connection '{connection_name}' does not have superuser privileges. "
            f"Set is_superuser=true in the connection configuration."
        )
    
    engine = manager.get_engine(connection_name)
    
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname")
        )
        return [row[0] for row in result.fetchall()]
