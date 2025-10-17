"""Shared database utilities for DRY database operations.

This module provides a single source of truth for all database utilities,
eliminating code duplication across different components.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

from ..database.connection_helper import DatabaseConnection

logger = get_logger("promptmanager.database")


class DatabaseManager:
    """Centralized database management."""
    
    _instance = None
    _connections = {}
    
    def __new__(cls):
        """Singleton pattern for database manager."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize database manager."""
        if not hasattr(self, '_initialized'):
            self._initialized = True
            self._default_db_path = None
            self._migrations = []
    
    def set_default_path(self, path: str):
        """Set default database path.
        
        Args:
            path: Default database path
        """
        self._default_db_path = path
        logger.info(f"Default database path set to: {path}")
    
    def get_default_path(self) -> str:
        """Get default database path.
        
        Returns:
            Default database path
        """
        if self._default_db_path is None:
            import os
            # Use user directory to avoid polluting ComfyUI installation
            db_dir = Path.home() / ".comfyui" / "promptmanager" / "data"
            db_dir.mkdir(parents=True, exist_ok=True)
            self._default_db_path = str(db_dir / "prompts.db")
        
        return self._default_db_path
    
    @contextmanager
    def get_connection(self, db_path: str = None):
        """Get database connection with connection pooling.
        
        Args:
            db_path: Database path (uses default if None)
            
        Yields:
            Database connection
        """
        if db_path is None:
            db_path = self.get_default_path()
        
        # Ensure database exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = DatabaseConnection.get_connection(db_path)
        conn.row_factory = sqlite3.Row

        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()
    
    def execute_migration(self, db_path: str, migration_sql: str, version: int):
        """Execute a database migration.
        
        Args:
            db_path: Database path
            migration_sql: SQL migration script
            version: Migration version number
        """
        with self.get_connection(db_path) as conn:
            # Create migrations table if doesn't exist
            conn.execute("""
                CREATE TABLE IF NOT EXISTS migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
            """)
            
            # Check if migration already applied
            cursor = conn.execute(
                "SELECT version FROM migrations WHERE version = ?",
                (version,)
            )
            
            if cursor.fetchone():
                logger.info(f"Migration {version} already applied")
                return
            
            # Execute migration
            conn.executescript(migration_sql)
            
            # Record migration
            conn.execute(
                "INSERT INTO migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.utcnow().isoformat())
            )
            
            logger.info(f"Migration {version} applied successfully")
    
    def backup_database(self, db_path: str = None, backup_path: str = None) -> str:
        """Create database backup.
        
        Args:
            db_path: Database to backup
            backup_path: Backup destination
            
        Returns:
            Path to backup file
        """
        if db_path is None:
            db_path = self.get_default_path()
        
        if backup_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{db_path}.backup_{timestamp}"

        source = DatabaseConnection.get_connection(db_path)
        dest = DatabaseConnection.get_connection(backup_path)

        try:
            source.backup(dest)
            logger.info(f"Database backed up to: {backup_path}")
            return backup_path
        finally:
            source.close()
            dest.close()
    
    def optimize_database(self, db_path: str = None):
        """Optimize database (VACUUM and ANALYZE).
        
        Args:
            db_path: Database to optimize
        """
        if db_path is None:
            db_path = self.get_default_path()
        
        with self.get_connection(db_path) as conn:
            conn.execute("VACUUM")
            conn.execute("ANALYZE")
            logger.info("Database optimized")


class DatabaseUtils:
    """Collection of database utility functions."""
    
    @staticmethod
    def dict_to_row(data: Dict[str, Any]) -> Tuple:
        """Convert dictionary to database row values.
        
        Args:
            data: Dictionary
            
        Returns:
            Tuple of values
        """
        # Handle special types
        processed = {}
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                processed[key] = json.dumps(value)
            elif isinstance(value, datetime):
                processed[key] = value.isoformat()
            elif isinstance(value, bool):
                processed[key] = int(value)
            else:
                processed[key] = value
        
        return tuple(processed.values())
    
    @staticmethod
    def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        """Convert database row to dictionary.
        
        Args:
            row: Database row
            
        Returns:
            Dictionary
        """
        data = dict(row)
        
        # Handle JSON fields
        json_fields = ['metadata', 'extra_data', 'workflow', 'settings', 'config']
        for field in json_fields:
            if field in data and isinstance(data[field], str):
                try:
                    data[field] = json.loads(data[field])
                except json.JSONDecodeError:
                    pass
        
        return data
    
    @staticmethod
    def build_where_clause(filters: Dict[str, Any]) -> Tuple[str, List[Any]]:
        """Build WHERE clause from filters.
        
        Args:
            filters: Filter dictionary
            
        Returns:
            Tuple of (WHERE clause, values)
        """
        if not filters:
            return "", []
        
        clauses = []
        values = []
        
        for key, value in filters.items():
            if value is None:
                clauses.append(f"{key} IS NULL")
            elif isinstance(value, (list, tuple)):
                placeholders = ",".join(["?" for _ in value])
                clauses.append(f"{key} IN ({placeholders})")
                values.extend(value)
            elif isinstance(value, str) and "%" in value:
                clauses.append(f"{key} LIKE ?")
                values.append(value)
            elif isinstance(value, dict):
                # Range query
                if "min" in value:
                    clauses.append(f"{key} >= ?")
                    values.append(value["min"])
                if "max" in value:
                    clauses.append(f"{key} <= ?")
                    values.append(value["max"])
            else:
                clauses.append(f"{key} = ?")
                values.append(value)
        
        where_clause = " WHERE " + " AND ".join(clauses)
        return where_clause, values
    
    @staticmethod
    def sanitize_sql_identifier(identifier: str) -> str:
        """Sanitize SQL identifier to prevent injection.
        
        Args:
            identifier: Table or column name
            
        Returns:
            Sanitized identifier
        """
        # Only allow alphanumeric and underscore
        import re
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', identifier):
            raise ValueError(f"Invalid SQL identifier: {identifier}")
        return identifier
    
    @staticmethod
    def batch_insert(conn: sqlite3.Connection,
                     table: str,
                     columns: List[str],
                     data: List[Tuple]) -> List[int]:
        """Perform batch insert.
        
        Args:
            conn: Database connection
            table: Table name
            columns: Column names
            data: List of value tuples
            
        Returns:
            List of inserted row IDs
        """
        if not data:
            return []
        
        placeholders = ",".join(["?" for _ in columns])
        query = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
        
        cursor = conn.cursor()
        ids = []
        
        for values in data:
            cursor.execute(query, values)
            ids.append(cursor.lastrowid)
        
        return ids
    
    @staticmethod
    def upsert(conn: sqlite3.Connection,
               table: str,
               data: Dict[str, Any],
               unique_columns: List[str]) -> int:
        """Insert or update record.
        
        Args:
            conn: Database connection
            table: Table name
            data: Record data
            unique_columns: Columns that determine uniqueness
            
        Returns:
            Row ID
        """
        # Build INSERT ... ON CONFLICT UPDATE
        columns = list(data.keys())
        placeholders = ",".join(["?" for _ in columns])
        
        update_clause = ",".join([f"{col}=excluded.{col}" for col in columns 
                                 if col not in unique_columns])
        
        query = f"""
            INSERT INTO {table} ({','.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT ({','.join(unique_columns)})
            DO UPDATE SET {update_clause}
        """
        
        cursor = conn.execute(query, tuple(data.values()))
        return cursor.lastrowid


class TransactionManager:
    """Manage database transactions."""
    
    def __init__(self, conn: sqlite3.Connection):
        """Initialize transaction manager.
        
        Args:
            conn: Database connection
        """
        self.conn = conn
        self._savepoint_counter = 0
    
    @contextmanager
    def transaction(self):
        """Context manager for transactions."""
        self.conn.execute("BEGIN")
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
    
    @contextmanager
    def savepoint(self, name: str = None):
        """Context manager for savepoints.
        
        Args:
            name: Savepoint name
        """
        if name is None:
            self._savepoint_counter += 1
            name = f"sp_{self._savepoint_counter}"
        
        self.conn.execute(f"SAVEPOINT {name}")
        try:
            yield
            self.conn.execute(f"RELEASE SAVEPOINT {name}")
        except Exception:
            self.conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
            raise


# Global instance
db_manager = DatabaseManager()