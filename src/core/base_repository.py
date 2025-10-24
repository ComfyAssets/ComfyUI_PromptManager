"""Base repository class for DRY database operations.

This module provides a single source of truth for all database operations,
eliminating code duplication across different repositories.
"""

import json
import sqlite3
from ..database.connection_helper import DatabaseConnection
from datetime import datetime
from pathlib import Path
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Set, Tuple, Type, TypeVar

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.repositories.base")


T = TypeVar('T')


class BaseRepository(ABC):
    """Abstract base repository providing common database operations.
    
    All repositories should inherit from this class to ensure consistent
    database handling and eliminate code duplication.
    """
    
    def __init__(self, db_path: str = None):
        """Initialize the repository with database connection.
        
        Args:
            db_path: Path to SQLite database. If None, uses default.
        """
        if db_path is None:
            db_path = self._get_default_db_path()
        
        self.db_path = db_path
        self._connection = None
        self._table_columns_cache: Optional[Set[str]] = None
        
        # For in-memory databases, keep a persistent connection
        if db_path == ":memory:":
            self._connection = DatabaseConnection.get_connection(db_path)
            self._connection.row_factory = sqlite3.Row
            self._connection.executescript(self._get_schema())
            self._connection.commit()
        else:
            self._ensure_database_exists()
            self._init_schema()
        
    @abstractmethod
    def _get_table_name(self) -> str:
        """Return the table name for this repository."""
        pass
    
    @abstractmethod
    def _get_schema(self) -> str:
        """Return the SQL schema for creating the table."""
        pass
    
    @abstractmethod
    def _to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a database row to a dictionary."""
        pass
    
    @abstractmethod
    def _from_dict(self, data: Dict[str, Any]) -> Tuple:
        """Convert a dictionary to database row values."""
        pass

    @abstractmethod
    def _get_columns(self) -> List[str]:
        """Get the list of columns for this table (excluding auto-generated fields)."""
        pass
    
    def _get_default_db_path(self) -> str:
        """Get the default database path.

        Uses the official ComfyUI user directory: ComfyUI/user/default/PromptManager/prompts.db
        via the shared file system utility to ensure consistency with migration and UI.
        """
        try:
            # Prefer unified file system path used across the extension
            from utils.core.file_system import get_file_system
            fs = get_file_system()
            return str(fs.get_database_path("prompts.db"))
        except Exception:
            # Fallback to previous home-based path if utils is unavailable
            db_dir = Path.home() / ".comfyui" / "promptmanager" / "data"
            db_dir.mkdir(parents=True, exist_ok=True)
            return str(db_dir / "prompts.db")
    
    def _ensure_database_exists(self):
        """Ensure the database file and directory exist."""
        db_path = Path(self.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

    def _get_table_columns(self) -> Set[str]:
        """Return the set of columns available on the backing table."""
        if self._table_columns_cache is not None:
            return self._table_columns_cache

        table = self._get_table_name()
        with self._get_connection() as conn:
            cursor = conn.execute(f"PRAGMA table_info({table})")
            columns = {row["name"] for row in cursor.fetchall()}

        self._table_columns_cache = columns
        return columns

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections.

        Ensures connections are properly closed and transactions committed.
        """
        # Use persistent connection for in-memory databases
        if self._connection:
            try:
                yield self._connection
                self._connection.commit()
            except Exception as e:
                self._connection.rollback()
                logger.error(f"Database error: {e}")
                raise
        else:
            conn = DatabaseConnection.get_connection(self.db_path)
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
    
    def _init_schema(self):
        """Initialize the database schema."""
        # Skip schema creation - handled by Database class
        logger.info(f"Using existing schema for {self._get_table_name()}")
    
    # CRUD Operations - Single source of truth
    
    def create(self, data: Dict[str, Any]) -> int:
        """Create a new record.
        
        Args:
            data: Dictionary containing record data
            
        Returns:
            ID of the created record
        """
        table = self._get_table_name()
        values = self._from_dict(data)
        
        # Get column names by inspecting the schema
        # For now, use a simpler approach - get all non-None values from processed data
        processed_values = list(values)
        
        # Get column list from the specific repository implementation
        schema_columns = self._get_columns()
        
        # Filter to only include columns that have values
        filtered_columns = []
        filtered_values = []
        
        for i, value in enumerate(processed_values):
            if value is not None and i < len(schema_columns):
                filtered_columns.append(schema_columns[i])
                filtered_values.append(value)
        
        placeholders = ",".join(["?" for _ in filtered_values])
        columns_str = ",".join(filtered_columns)

        query = f"INSERT INTO {table} ({columns_str}) VALUES ({placeholders})"

        # Debug logging
        import os
        if os.getenv("PROMPTMANAGER_DEBUG", "0") == "1":
            print(f"\n🔍 [BaseRepository.create] SQL Debug:")
            print(f"   Table: {table}")
            print(f"   Schema columns: {schema_columns}")
            print(f"   Processed values: {processed_values}")
            print(f"   Filtered columns: {filtered_columns}")
            print(f"   Filtered values: {filtered_values}")
            print(f"   Query: {query}")

        with self._get_connection() as conn:
            cursor = conn.execute(query, filtered_values)
            return cursor.lastrowid
    
    def read(self, id: int) -> Optional[Dict[str, Any]]:
        """Read a single record by ID.
        
        Args:
            id: Record ID
            
        Returns:
            Dictionary containing record data or None
        """
        table = self._get_table_name()
        query = f"SELECT * FROM {table} WHERE id = ?"
        
        with self._get_connection() as conn:
            cursor = conn.execute(query, (id,))
            row = cursor.fetchone()
            return self._to_dict(row) if row else None
    
    def update(self, id: int, data: Dict[str, Any]) -> bool:
        """Update a record.
        
        Args:
            id: Record ID
            data: Dictionary containing updated data
            
        Returns:
            True if updated, False if not found
        """
        table = self._get_table_name()
        data['updated_at'] = datetime.utcnow().isoformat()
        
        set_clause = ",".join([f"{k} = ?" for k in data.keys()])
        values = list(data.values()) + [id]
        
        query = f"UPDATE {table} SET {set_clause} WHERE id = ?"
        
        with self._get_connection() as conn:
            cursor = conn.execute(query, values)
            return cursor.rowcount > 0
    
    def delete(self, id: int) -> bool:
        """Delete a record.
        
        Args:
            id: Record ID
            
        Returns:
            True if deleted, False if not found
        """
        table = self._get_table_name()
        query = f"DELETE FROM {table} WHERE id = ?"
        
        with self._get_connection() as conn:
            cursor = conn.execute(query, (id,))
            return cursor.rowcount > 0
    
    def list(self, 
             limit: int = 100, 
             offset: int = 0,
             order_by: str = "created_at DESC",
             **filters) -> List[Dict[str, Any]]:
        """List records with optional filtering.
        
        Args:
            limit: Maximum number of records
            offset: Number of records to skip
            order_by: SQL ORDER BY clause
            **filters: Column filters
            
        Returns:
            List of dictionaries containing record data
        """
        table = self._get_table_name()
        
        where_clauses = []
        values = []
        
        for key, value in filters.items():
            if value is not None:
                if isinstance(value, (list, tuple)):
                    placeholders = ",".join(["?" for _ in value])
                    where_clauses.append(f"{key} IN ({placeholders})")
                    values.extend(value)
                elif isinstance(value, str) and "%" in value:
                    where_clauses.append(f"{key} LIKE ?")
                    values.append(value)
                else:
                    where_clauses.append(f"{key} = ?")
                    values.append(value)
        
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        query = f"""
            SELECT * FROM {table}
            {where_sql}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
        """
        
        values.extend([limit, offset])
        
        with self._get_connection() as conn:
            cursor = conn.execute(query, values)
            return [self._to_dict(row) for row in cursor.fetchall()]
    
    def count(self, **filters) -> int:
        """Count records with optional filtering.
        
        Args:
            **filters: Column filters
            
        Returns:
            Number of matching records
        """
        table = self._get_table_name()
        
        where_clauses = []
        values = []
        
        for key, value in filters.items():
            if value is not None:
                where_clauses.append(f"{key} = ?")
                values.append(value)
        
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        query = f"SELECT COUNT(*) FROM {table} {where_sql}"
        
        with self._get_connection() as conn:
            cursor = conn.execute(query, values)
            return cursor.fetchone()[0]
    
    def exists(self, **filters) -> bool:
        """Check if a record exists.
        
        Args:
            **filters: Column filters
            
        Returns:
            True if exists, False otherwise
        """
        return self.count(**filters) > 0
    
    def search_count(self, search_term: str, columns: List[str]) -> int:
        """Count search results across multiple columns.

        Args:
            search_term: Search term
            columns: Columns to search in

        Returns:
            Number of matching records
        """
        table = self._get_table_name()

        search_clauses = [f"{col} LIKE ?" for col in columns]
        where_sql = f"WHERE {' OR '.join(search_clauses)}"

        values = [f"%{search_term}%" for _ in columns]

        query = f"SELECT COUNT(*) FROM {table} {where_sql}"

        with self._get_connection() as conn:
            cursor = conn.execute(query, values)
            return cursor.fetchone()[0]

    def search(self, search_term: str, columns: List[str], limit: int = None, offset: int = None) -> List[Dict[str, Any]]:
        """Search records across multiple columns.

        Args:
            search_term: Search term
            columns: Columns to search in
            limit: Maximum number of records to return
            offset: Number of records to skip

        Returns:
            List of matching records
        """
        table = self._get_table_name()

        search_clauses = [f"{col} LIKE ?" for col in columns]
        where_sql = f"WHERE {' OR '.join(search_clauses)}"

        values = [f"%{search_term}%" for _ in columns]

        # Use id DESC as fallback for sorting (created_at might be NULL)
        query = f"SELECT * FROM {table} {where_sql} ORDER BY id DESC"

        # Add pagination if requested
        if limit is not None:
            query += f" LIMIT {limit}"
            if offset is not None:
                query += f" OFFSET {offset}"

        with self._get_connection() as conn:
            cursor = conn.execute(query, values)
            return [self._to_dict(row) for row in cursor.fetchall()]
    
    def bulk_create(self, items: List[Dict[str, Any]]) -> List[int]:
        """Create multiple records in a single transaction.
        
        Args:
            items: List of dictionaries containing record data
            
        Returns:
            List of created record IDs
        """
        if not items:
            return []
        
        table = self._get_table_name()
        columns = items[0].keys()
        placeholders = ",".join(["?" for _ in columns])
        
        query = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
        
        ids = []
        with self._get_connection() as conn:
            for item in items:
                values = self._from_dict(item)
                cursor = conn.execute(query, values)
                ids.append(cursor.lastrowid)
        
        return ids
    
    def bulk_delete(self, ids: List[int]) -> int:
        """Delete multiple records.
        
        Args:
            ids: List of record IDs
            
        Returns:
            Number of deleted records
        """
        if not ids:
            return 0
        
        table = self._get_table_name()
        placeholders = ",".join(["?" for _ in ids])
        
        query = f"DELETE FROM {table} WHERE id IN ({placeholders})"
        
        with self._get_connection() as conn:
            cursor = conn.execute(query, ids)
            return cursor.rowcount
