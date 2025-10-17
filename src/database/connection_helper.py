"""
Database connection helper with proper WAL mode and connection pooling.
Ensures all connections use consistent settings and proper cleanup.
"""

import sqlite3
import threading
from contextlib import contextmanager
from typing import Optional, Dict, Any
import time
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class DatabaseMetrics:
    """Track database connection and query metrics."""

    def __init__(self):
        self._lock = threading.Lock()
        self.lock_retries = 0
        self.lock_failures = 0
        self.connection_reuses = 0
        self.connection_creates = 0
        self.query_count = 0
        self.total_query_time = 0.0
        self.retry_counts = defaultdict(int)  # Track retries per attempt
        self.slow_queries = []  # Track queries > 1 second

    def record_lock_retry(self, attempt: int):
        """Record a lock retry attempt."""
        with self._lock:
            self.lock_retries += 1
            self.retry_counts[attempt] += 1

    def record_lock_failure(self):
        """Record a lock failure (max retries exceeded)."""
        with self._lock:
            self.lock_failures += 1

    def record_connection_reuse(self):
        """Record a connection reuse."""
        with self._lock:
            self.connection_reuses += 1

    def record_connection_create(self):
        """Record a new connection creation."""
        with self._lock:
            self.connection_creates += 1

    def record_query(self, duration: float, query: str = ""):
        """Record a query execution."""
        with self._lock:
            self.query_count += 1
            self.total_query_time += duration

            # Track slow queries
            if duration > 1.0:
                self.slow_queries.append({
                    'duration': duration,
                    'query': query[:100],  # Truncate long queries
                    'timestamp': time.time()
                })
                # Keep only last 10 slow queries
                if len(self.slow_queries) > 10:
                    self.slow_queries.pop(0)

    def get_stats(self) -> Dict[str, Any]:
        """Get current metrics statistics."""
        with self._lock:
            total_connections = self.connection_reuses + self.connection_creates
            avg_query_time = (
                self.total_query_time / self.query_count
                if self.query_count > 0
                else 0
            )
            reuse_rate = (
                self.connection_reuses / total_connections
                if total_connections > 0
                else 0
            )

            return {
                'lock_retries': self.lock_retries,
                'lock_failures': self.lock_failures,
                'connection_reuses': self.connection_reuses,
                'connection_creates': self.connection_creates,
                'total_connections': total_connections,
                'reuse_rate': reuse_rate,
                'query_count': self.query_count,
                'avg_query_time_ms': avg_query_time * 1000,
                'total_query_time': self.total_query_time,
                'retry_distribution': dict(self.retry_counts),
                'slow_queries_count': len(self.slow_queries),
                'recent_slow_queries': self.slow_queries[-5:],  # Last 5
            }

    def reset(self):
        """Reset all metrics (useful for testing)."""
        with self._lock:
            self.lock_retries = 0
            self.lock_failures = 0
            self.connection_reuses = 0
            self.connection_creates = 0
            self.query_count = 0
            self.total_query_time = 0.0
            self.retry_counts.clear()
            self.slow_queries.clear()


# Global metrics instance
_metrics = DatabaseMetrics()


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
                _metrics.record_connection_reuse()
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

            _metrics.record_connection_create()
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
    start_time = time.time()

    for attempt in range(max_retries):
        try:
            conn = DatabaseConnection.get_connection(db_path)
            yield conn
            conn.commit()

            # Record successful query
            duration = time.time() - start_time
            _metrics.record_query(duration)
            return

        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                _metrics.record_lock_retry(attempt)
                if attempt < max_retries - 1:
                    logger.debug(f"Database locked, retry {attempt + 1}/{max_retries}")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    continue
                else:
                    _metrics.record_lock_failure()
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


def get_database_metrics() -> Dict[str, Any]:
    """
    Get current database connection metrics.

    Returns:
        Dictionary containing metrics like:
        - lock_retries: Number of lock retry attempts
        - lock_failures: Number of operations that failed due to locks
        - connection_reuses: Number of times connections were reused
        - connection_creates: Number of new connections created
        - reuse_rate: Percentage of connection reuses
        - query_count: Total number of queries executed
        - avg_query_time_ms: Average query time in milliseconds
        - slow_queries_count: Number of queries slower than 1 second
    """
    return _metrics.get_stats()


def reset_database_metrics():
    """Reset database metrics (useful for testing or monitoring resets)."""
    _metrics.reset()
    logger.info("Database metrics reset")