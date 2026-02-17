"""
Settings module for pgsqlasync2fast-fastapi
Handles configuration using Pydantic Settings with environment variables
"""

import os
from typing import Dict, Optional

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Look for .env in the current working directory (where the app is running)
DOTENV_PATH = os.path.join(os.getcwd(), ".env")


class DatabaseConnectionSettings(BaseModel):
    """Configuration for a single database connection."""

    host: str = Field(..., description="Database server host")
    port: int = Field(default=5432, description="Database server port")
    username: str = Field(..., description="Database username")
    password: SecretStr = Field(..., description="Database password")
    database: str = Field(..., description="Database name")
    
    # Superuser flag
    is_superuser: bool = Field(
        default=False,
        description="Whether this connection has superuser privileges for database creation"
    )
    
    # Connection pool settings
    pool_size: int = Field(
        default=5,
        description="Number of connections to maintain in the pool"
    )
    max_overflow: int = Field(
        default=10,
        description="Maximum number of connections that can be created beyond pool_size"
    )
    pool_timeout: int = Field(
        default=30,
        description="Timeout in seconds for getting a connection from the pool"
    )
    pool_recycle: int = Field(
        default=3600,
        description="Recycle connections after this many seconds (prevents stale connections)"
    )
    
    # SQLAlchemy settings
    echo: bool = Field(
        default=False,
        description="Enable SQLAlchemy echo mode for this connection"
    )

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        """Validate port is in valid range."""
        if not 1 <= v <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        return v

    @field_validator("pool_size")
    @classmethod
    def validate_pool_size(cls, v: int) -> int:
        """Validate pool size is positive."""
        if v <= 0:
            raise ValueError("pool_size must be greater than 0")
        return v

    @field_validator("max_overflow")
    @classmethod
    def validate_max_overflow(cls, v: int) -> int:
        """Validate max overflow is non-negative."""
        if v < 0:
            raise ValueError("max_overflow must be non-negative")
        return v


class DatabaseSettings(BaseSettings):
    """Main database configuration."""

    # Multiple named database connections
    connections: Dict[str, DatabaseConnectionSettings] = Field(
        default_factory=dict,
        description="Dictionary of named database connections (e.g., 'default', 'business')",
    )
    
    # Default connection to use when none is specified
    default_connection: str = Field(
        default="default",
        description="Name of default database connection to use"
    )
    
    # Global SQLAlchemy settings
    echo: bool = Field(
        default=False,
        description="Global SQLAlchemy echo mode (can be overridden per connection)"
    )

    model_config = SettingsConfigDict(
        env_file=DOTENV_PATH,
        env_file_encoding="utf-8",
        env_prefix="DB_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    def get_connection(self, connection_name: Optional[str] = None) -> DatabaseConnectionSettings:
        """
        Get database connection configuration by name.
        
        Args:
            connection_name: Name of the connection. If None, uses default_connection.
            
        Returns:
            DatabaseConnectionSettings for the requested connection.
            
        Raises:
            ValueError: If connection doesn't exist.
        """
        name = connection_name or self.default_connection
        
        if name not in self.connections:
            available = ", ".join(self.connections.keys()) if self.connections else "none"
            raise ValueError(
                f"Database connection '{name}' not found. Available connections: {available}"
            )
        
        return self.connections[name]

    def has_connection(self, connection_name: str) -> bool:
        """Check if a database connection exists."""
        return connection_name in self.connections

    def get_connection_url(self, connection_name: Optional[str] = None) -> str:
        """
        Get the database URL for a connection.
        
        Args:
            connection_name: Name of the connection. If None, uses default_connection.
            
        Returns:
            PostgreSQL async connection URL.
        """
        conn = self.get_connection(connection_name)
        return (
            f"postgresql+asyncpg://{conn.username}:{conn.password.get_secret_value()}"
            f"@{conn.host}:{conn.port}/{conn.database}"
        )

    def get_superuser_connection(self) -> Optional[DatabaseConnectionSettings]:
        """
        Get the first connection with superuser privileges.
        
        Returns:
            DatabaseConnectionSettings with is_superuser=True, or None if not found.
        """
        for conn in self.connections.values():
            if conn.is_superuser:
                return conn
        return None

    def get_superuser_connection_name(self) -> Optional[str]:
        """
        Get the name of the first connection with superuser privileges.
        
        Returns:
            Name of connection with is_superuser=True, or None if not found.
        """
        for name, conn in self.connections.items():
            if conn.is_superuser:
                return name
        return None


# Initialize settings with error handling
try:
    settings = DatabaseSettings()
    
    # Validate that at least one connection is configured
    if not settings.connections:
        print("⚠️  Warning: No database connections configured. Please add at least one connection.")
        print("   Example: DB_CONNECTIONS__DEFAULT__HOST=localhost")
        print("   Example: DB_CONNECTIONS__DEFAULT__USERNAME=myuser")
        print("   Example: DB_CONNECTIONS__DEFAULT__PASSWORD=mypassword")
        print("   Example: DB_CONNECTIONS__DEFAULT__DATABASE=mydb")
        
except Exception as e:
    import traceback

    print("🚨 Error loading database configuration:")
    print(e)
    traceback.print_exc()
    
    # Fallback to minimal configuration
    settings = DatabaseSettings()
    print("⚠️  Using fallback database configuration (no connections)")
