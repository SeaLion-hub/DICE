"""
db_pool.py - PostgreSQL connection pool utility for FastAPI application

Provides global connection pool management using psycopg2's SimpleConnectionPool.
Thread-safe connection pooling to improve performance and resource utilization.
"""

import os
import logging
from contextlib import contextmanager
from typing import Optional, Generator
from urllib.parse import urlparse, unquote
import psycopg2
import psycopg2.extensions
from psycopg2.pool import SimpleConnectionPool, PoolError
from fastapi import HTTPException  # <-- 1. HTTPException을 import 합니다

# Module-level logger
logger = logging.getLogger(__name__)

# Global pool instance
_pool: Optional[SimpleConnectionPool] = None


def init_pool(minconn: int = 1, maxconn: int = 10) -> SimpleConnectionPool:
    """
    Initialize the global connection pool.
    
    Creates a SimpleConnectionPool using DATABASE_URL from environment.
    If pool already exists, returns the existing pool without recreating.
    
    Args:
        minconn: Minimum number of connections to maintain
        maxconn: Maximum number of connections allowed
        
    Returns:
        SimpleConnectionPool instance
        
    Raises:
        RuntimeError: If DATABASE_URL not set or pool initialization fails
    """
    global _pool
    
    # Return existing pool if already initialized
    if _pool is not None:
        logger.debug("Connection pool already initialized, returning existing pool")
        return _pool
    
    # Get DATABASE_URL from environment
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL not set in environment")
    
    # Parse the database URL
    try:
        parsed = urlparse(database_url)
        
        # Extract connection parameters with unquote for special characters
        # Using 'dbname' as the standard key for psycopg2
        db_params = {
            'host': parsed.hostname,
            'port': parsed.port or 5432,
            'dbname': parsed.path.lstrip('/'),
            'user': unquote(parsed.username) if parsed.username else None,
            'password': unquote(parsed.password) if parsed.password else None,
            'client_encoding': 'utf8'
        }
        
        # Remove None values
        db_params = {k: v for k, v in db_params.items() if v is not None}
        
    except Exception as e:
        raise RuntimeError(f"Failed to parse DATABASE_URL: {e}") from e
    
    # Create the connection pool
    try:
        _pool = SimpleConnectionPool(
            minconn,
            maxconn,
            **db_params
        )
        logger.info(f"Connection pool initialized (min={minconn}, max={maxconn})")
        return _pool
        
    except psycopg2.Error as e:
        raise RuntimeError(f"Failed to initialize connection pool: {e}") from e
    except Exception as e:
        raise RuntimeError(f"Unexpected error initializing pool: {e}") from e


def close_pool() -> None:
    """
    Close all connections in the pool and reset to None.
    
    Safe to call multiple times - no-op if pool doesn't exist.
    """
    global _pool
    
    if _pool is not None:
        try:
            _pool.closeall()
            logger.info("Connection pool closed")
        except Exception as e:
            logger.error(f"Error closing connection pool: {e}")
        finally:
            _pool = None


@contextmanager
def get_conn() -> Generator[psycopg2.extensions.connection, None, None]:
    """
    Context manager to get a connection from the pool.
    
    Automatically initializes pool if not already done.
    Ensures connection is returned to pool after use.
    
    Usage:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                
    Yields:
        psycopg2 connection object
        
    Raises:
        RuntimeError: If pool initialization or connection retrieval fails
    """
    global _pool
    
    # Auto-initialize pool if needed
    if _pool is None:
        init_pool()
    
    conn = None
    try:
        # Get connection from pool
        conn = _pool.getconn()
        if conn is None:
            raise RuntimeError("Failed to get connection from pool")
            
        yield conn
        
    except HTTPException as e:  # <-- 2. HTTPException을 먼저 잡아서
        # Do not wrap HTTPException. Let it propagate to FastAPI.
        raise e  # <-- 3. 그대로 다시 raise 합니다.
        
    except PoolError as e:
        logger.error(f"Pool error in connection context: {e}")
        raise RuntimeError(f"Pool error: {e}") from e
    except psycopg2.Error as e:
        # Handle actual DB errors (e.g., connection lost during query)
        logger.error(f"Database error in connection context: {e}")
        raise RuntimeError(f"Database error: {e}") from e
    except Exception as e:
        # Handle other unexpected errors
        logger.error(f"Unexpected error in connection context: {e}")
        raise RuntimeError(f"Unexpected connection error: {e}") from e
    finally:
        # Always return connection to pool
        if conn is not None and _pool is not None:
            try:
                _pool.putconn(conn)
            except Exception as e:
                logger.error(f"Error returning connection to pool: {e}")