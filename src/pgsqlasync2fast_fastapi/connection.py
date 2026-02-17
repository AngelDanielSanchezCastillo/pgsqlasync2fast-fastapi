"""
Database connection and engine management
"""

from typing import AsyncGenerator, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import text

from .settings import DatabaseSettings, settings


class DatabaseManager:
    """
    Manages multiple async database engines and sessions.
    
    This class provides:
    - Lazy engine creation (engines created only when first accessed)
    - Multiple named connections support
    - Session factory for each connection
    - Connection health checks
    - Cleanup utilities
    """

    def __init__(self, config: DatabaseSettings):
        """
        Initialize the database manager.
        
        Args:
            config: Database settings configuration
        """
        self.config = config
        self._engines: Dict[str, AsyncEngine] = {}
        self._session_makers: Dict[str, async_sessionmaker[AsyncSession]] = {}

    def get_engine(self, connection_name: Optional[str] = None) -> AsyncEngine:
        """
        Get or create an async engine for a connection.
        
        Args:
            connection_name: Name of the connection. If None, uses default_connection.
            
        Returns:
            AsyncEngine for the requested connection.
            
        Raises:
            ValueError: If connection doesn't exist.
        """
        name = connection_name or self.config.default_connection
        
        # Return existing engine if already created
        if name in self._engines:
            return self._engines[name]
        
        # Get connection settings
        conn_settings = self.config.get_connection(name)
        
        # Build connection URL
        url = self.config.get_connection_url(name)
        
        # Determine echo mode (connection-specific overrides global)
        echo = conn_settings.echo if conn_settings.echo else self.config.echo
        
        # Create engine with pool settings
        engine = create_async_engine(
            url,
            echo=echo,
            pool_size=conn_settings.pool_size,
            max_overflow=conn_settings.max_overflow,
            pool_timeout=conn_settings.pool_timeout,
            pool_recycle=conn_settings.pool_recycle,
            pool_pre_ping=True,  # Verify connections before using them
        )
        
        # Store engine
        self._engines[name] = engine
        
        # Create session maker for this engine
        self._session_makers[name] = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        
        return engine

    def get_session_maker(self, connection_name: Optional[str] = None) -> async_sessionmaker[AsyncSession]:
        """
        Get session maker for a connection.
        
        Args:
            connection_name: Name of the connection. If None, uses default_connection.
            
        Returns:
            Session maker for the requested connection.
        """
        name = connection_name or self.config.default_connection
        
        # Ensure engine and session maker exist
        if name not in self._session_makers:
            self.get_engine(name)
        
        return self._session_makers[name]

    async def get_session(self, connection_name: Optional[str] = None) -> AsyncSession:
        """
        Create a new async session for a connection.
        
        Args:
            connection_name: Name of the connection. If None, uses default_connection.
            
        Returns:
            New AsyncSession instance.
            
        Note:
            Remember to close the session when done, or use it in an async context manager.
        """
        session_maker = self.get_session_maker(connection_name)
        return session_maker()

    async def close_all(self) -> None:
        """
        Dispose all engines and close all connections.
        
        This should be called on application shutdown.
        """
        for engine in self._engines.values():
            await engine.dispose()
        
        self._engines.clear()
        self._session_makers.clear()

    async def health_check(self, connection_name: Optional[str] = None) -> bool:
        """
        Check if a database connection is healthy.
        
        Args:
            connection_name: Name of the connection. If None, uses default_connection.
            
        Returns:
            True if connection is healthy, False otherwise.
        """
        try:
            engine = self.get_engine(connection_name)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            print(f"Health check failed for connection '{connection_name}': {e}")
            return False

    def list_connections(self) -> list[str]:
        """
        List all configured connection names.
        
        Returns:
            List of connection names.
        """
        return list(self.config.connections.keys())

    def is_superuser_connection(self, connection_name: Optional[str] = None) -> bool:
        """
        Check if a connection has superuser privileges.
        
        Args:
            connection_name: Name of the connection. If None, uses default_connection.
            
        Returns:
            True if connection has superuser privileges.
        """
        conn = self.config.get_connection(connection_name)
        return conn.is_superuser


# Global singleton instance
_manager: Optional[DatabaseManager] = None


def get_manager(config: Optional[DatabaseSettings] = None) -> DatabaseManager:
    """
    Get the global DatabaseManager singleton.
    
    Args:
        config: Optional database settings. If not provided, uses global settings.
        
    Returns:
        DatabaseManager singleton instance.
    """
    global _manager
    
    if _manager is None:
        cfg = config or settings
        _manager = DatabaseManager(cfg)
    
    return _manager
