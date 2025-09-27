"""
Database connection helper with proper WAL mode and connection pooling.
Ensures all connections use consistent settings and proper cleanup.
"""

import sqlite3
import threading
from contextlib import contextmanager
from typing import Optional
import time
import logging

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """Thread-safe database connection with WAL mode."""

    # Class-level lock for connection creation
    _lock = threading.Lock()
    _connections = {}  # Thread-local connections

    @classmethod
    def get_connection(cls, db_path: str) -> sqlite3.Connection:
        """
        Get or create a thread-local database connection.

        Args:
            db_path: Path to database file

        Returns:
            Configured SQLite connection
        """
        thread_id = threading.get_ident()
        conn_key = f"{thread_id}_{db_path}"

        # Check if we have a valid connection for this thread
        if conn_key in cls._connections:
            conn = cls._connections[conn_key]
            try:
                # Test if connection is still valid
                conn.execute("SELECT 1")
                return conn
            except:
                # Connection is dead, remove it
                del cls._connections[conn_key]

        # Create new connection with proper settings
        with cls._lock:
            conn = sqlite3.connect(db_path, timeout=30.0)  # 30 second timeout

            # Enable WAL mode and optimizations
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")  # 10 seconds
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA mmap_size=268435456")  # 256MB

            # Store connection for reuse
            cls._connections[conn_key] = conn

            return conn

    @classmethod
    def close_all(cls):
        """Close all connections (call on shutdown)."""
        with cls._lock:
            for conn in cls._connections.values():
                try:
                    conn.close()
                except:
                    pass
            cls._connections.clear()


@contextmanager
def get_db_connection(db_path: str):
    """
    Context manager for database connections.

    Usage:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM prompts")
    """
    conn = None
    max_retries = 5
    retry_delay = 0.1

    for attempt in range(max_retries):
        try:
            conn = DatabaseConnection.get_connection(db_path)
            yield conn
            conn.commit()
            return

        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    continue
                else:
                    logger.error(f"Database locked after {max_retries} retries")
                    raise
            else:
                raise

        except Exception as e:
            if conn:
                conn.rollback()
            raise

        finally:
            # Don't close the connection - let it be reused
            pass


def execute_query(db_path: str, query: str, params=None, fetch_one=False):
    """
    Execute a query with automatic retry and connection handling.

    Args:
        db_path: Path to database
        query: SQL query to execute
        params: Query parameters
        fetch_one: If True, return fetchone() else fetchall()

    Returns:
        Query results or None
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)

        if query.strip().upper().startswith("SELECT"):
            return cursor.fetchone() if fetch_one else cursor.fetchall()
        else:
            return cursor.lastrowid if query.strip().upper().startswith("INSERT") else cursor.rowcount


def init_database_settings(db_path: str):
    """
    Initialize database with proper settings.
    Call this once at application startup.

    Args:
        db_path: Path to database file
    """
    try:
        with get_db_connection(db_path) as conn:
            # Force WAL mode
            result = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            logger.info(f"Database journal mode: {result[0]}")

            # Verify settings
            settings = [
                ("busy_timeout", 10000),
                ("synchronous", 1),  # NORMAL
                ("temp_store", 2),   # MEMORY
            ]

            for setting, expected in settings:
                result = conn.execute(f"PRAGMA {setting}").fetchone()
                if result and result[0] != expected:
                    logger.warning(f"Database {setting}: {result[0]} (expected {expected})")

    except Exception as e:
        logger.error(f"Failed to initialize database settings: {e}")
        raise