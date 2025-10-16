"""Prompt repository implementation extending BaseRepository.

This module implements prompt-specific database operations,
inheriting all common functionality from BaseRepository.
"""

import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.base_repository import BaseRepository

try:  # pragma: no cover
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.repositories.prompts")



class PromptRepository(BaseRepository):
    """Repository for prompt management.
    
    Extends BaseRepository with prompt-specific functionality.
    All CRUD operations are inherited - only domain-specific logic added.
    """
    
    def __init__(self, db_path: str = None):
        """Initialize repository and ensure legacy compatibility.

        Also performs an automatic migration if a legacy 'positive_prompt' schema is detected.
        """
        super().__init__(db_path=db_path)
        # Migration disabled - using existing schema as-is
        # try:
        #     self._migrate_legacy_schema_if_needed()
        # except Exception as e:
        #     logger.error(f"Legacy schema migration check failed: {e}")

    def _get_table_name(self) -> str:
        """Return the prompts table name."""
        return "prompts"
    
    def _get_schema(self) -> str:
        """Return the SQL schema for prompts table."""
        # Note: Using positive_prompt to match existing database schema
        return """
            CREATE TABLE IF NOT EXISTS prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt TEXT NOT NULL,
                negative_prompt TEXT DEFAULT '',
                category TEXT DEFAULT 'uncategorized',
                tags TEXT DEFAULT '[]',
                rating INTEGER DEFAULT 0,
                notes TEXT DEFAULT '',
                hash TEXT UNIQUE,
                model_hash TEXT,
                sampler_settings TEXT,
                generation_params TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """
    
    def _to_dict(self, row) -> Optional[Dict[str, Any]]:
        """Convert database row to dictionary.
        
        Args:
            row: Database row
            
        Returns:
            Dictionary with prompt data
        """
        if not row:
            return None
        
        data = dict(row)

        if "prompt" in data and "positive_prompt" not in data:
            data["positive_prompt"] = data["prompt"]
        
        # Parse JSON fields
        if "tags" in data and isinstance(data["tags"], str):
            try:
                data["tags"] = json.loads(data["tags"])
            except json.JSONDecodeError:
                data["tags"] = []

        # Note: metadata and workflow fields removed - not in current schema
        # They can be added back when schema is extended
        
        return data

    def _get_columns(self) -> List[str]:
        """Get the list of columns for prompts table."""
        # Match actual database schema
        return [
            "positive_prompt", "negative_prompt", "category", "tags", "rating",
            "notes", "hash", "model_hash", "sampler_settings", "generation_params"
        ]

    def _from_dict(self, data: Dict[str, Any]) -> Tuple:
        """Convert dictionary to database row values.
        
        Args:
            data: Prompt data dictionary
            
        Returns:
            Tuple of values for database
        """
        # Calculate hash if not provided
        if "hash" not in data:
            # Use positive_prompt or prompt field
            prompt_text = data.get("positive_prompt") or data.get("prompt", "")
            negative_text = data.get("negative_prompt", "")
            data["hash"] = self.calculate_hash(prompt_text, negative_text)
        
        # Serialize JSON fields
        tags = data.get("tags", [])
        if isinstance(tags, list):
            tags = json.dumps(tags)
        elif not isinstance(tags, str):
            tags = json.dumps([])

        # Return values in the order they appear in the schema
        # Don't include auto-generated fields
        # Match actual database schema with positive_prompt
        prompt_text = data.get("positive_prompt") or data.get("prompt") or ""

        return (
            prompt_text,  # positive_prompt - NOT NULL
            data.get("negative_prompt", ""),
            data.get("category", "uncategorized"),
            tags,
            data.get("rating"),  # Allow None for unrated prompts
            data.get("notes", ""),
            data.get("hash"),
            data.get("model_hash"),
            data.get("sampler_settings"),
            data.get("generation_params")
        )
    
    # Prompt-specific methods
    
    def calculate_hash(self, prompt: str, negative_prompt: str = "") -> str:
        """Calculate SHA256 hash for prompt uniqueness.
        
        Args:
            prompt: Main prompt text
            negative_prompt: Negative prompt text
            
        Returns:
            SHA256 hash string
        """
        content = f"{prompt}|{negative_prompt}".strip()
        return hashlib.sha256(content.encode()).hexdigest()

    def _migrate_legacy_schema_if_needed(self) -> None:
        """Detect and migrate legacy prompts schema that used 'positive_prompt'.

        Upgrades a table with columns like (positive_prompt, negative_prompt, ...)
        to the current schema using 'prompt' and other columns expected by the API.
        """
        table = self._get_table_name()
        with self._get_connection() as conn:
            # Check if prompts table exists
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if not cur.fetchone():
                return

            # Inspect columns
            cur = conn.execute(f"PRAGMA table_info({table})")
            cols = [r[1] for r in cur.fetchall()]
            if "prompt" in cols:
                return  # Already current schema
            if "positive_prompt" not in cols:
                return  # Unknown schema; do not attempt automatic migration

            logger.info("Detected legacy prompts schema (positive_prompt). Migrating to current schema...")

            # Create new table with current schema
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prompts_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt TEXT NOT NULL,
                    negative_prompt TEXT DEFAULT '',
                    category TEXT DEFAULT 'uncategorized',
                    tags TEXT DEFAULT '[]',
                    rating INTEGER DEFAULT 0,
                    notes TEXT DEFAULT '',
                    hash TEXT UNIQUE NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    workflow TEXT DEFAULT '{}',
                    execution_count INTEGER DEFAULT 0,
                    last_used TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # Prepare column presence checks
            has_category = "category" in cols
            has_tags = "tags" in cols
            has_rating = "rating" in cols
            has_notes = "notes" in cols
            has_hash = "hash" in cols
            has_created = "created_at" in cols
            has_updated = "updated_at" in cols
            has_negative = "negative_prompt" in cols

            # Build SELECT list with safe fallbacks
            sel_id = "id" if "id" in cols else "NULL AS id"
            sel_prompt = "positive_prompt AS prompt"
            sel_negative = "negative_prompt" if has_negative else "'' AS negative_prompt"
            sel_category = "category" if has_category else "'uncategorized' AS category"
            sel_tags = "tags" if has_tags else "'[]' AS tags"
            sel_rating = "rating" if has_rating else "0 AS rating"
            sel_notes = "notes" if has_notes else "'' AS notes"
            sel_hash = "hash" if has_hash else "NULL AS hash"
            sel_created = "created_at" if has_created else "CURRENT_TIMESTAMP AS created_at"
            sel_updated = "updated_at" if has_updated else "CURRENT_TIMESTAMP AS updated_at"

            # Copy data mapping positive_prompt -> prompt
            insert_sql = f"""
                INSERT INTO prompts_new (
                    id, prompt, negative_prompt, category, tags, rating, notes, hash,
                    metadata, workflow, execution_count, last_used, created_at, updated_at
                )
                SELECT
                    {sel_id},
                    {sel_prompt},
                    {sel_negative},
                    {sel_category},
                    {sel_tags},
                    {sel_rating},
                    {sel_notes},
                    {sel_hash},
                    '{{}}' AS metadata,
                    '{{}}' AS workflow,
                    0 AS execution_count,
                    NULL AS last_used,
                    {sel_created},
                    {sel_updated}
                FROM {table}
            """
            conn.execute(insert_sql)

            # Replace old table
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE prompts_new RENAME TO {table}")

            # Recreate indexes expected by schema
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompts_hash ON prompts(hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompts_category ON prompts(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompts_rating ON prompts(rating)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompts_created ON prompts(created_at)")

            logger.info("Legacy prompts schema migration completed.")
    
    def get(self, prompt_id: int) -> Optional[Dict[str, Any]]:
        """Get a prompt by ID.
        
        Args:
            prompt_id: The prompt ID
            
        Returns:
            Prompt data or None if not found
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM prompts WHERE id = ?",
                (prompt_id,)
            )
            return self._to_dict(cursor.fetchone())
    
    def find_by_hash(self, hash_value: str) -> Optional[Dict[str, Any]]:
        """Find prompt by hash.
        
        Args:
            hash_value: SHA256 hash
            
        Returns:
            Prompt data or None
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"SELECT * FROM {self._get_table_name()} WHERE hash = ?",
                (hash_value,)
            )
            row = cursor.fetchone()
            return self._to_dict(row)
    
    def find_duplicates(self) -> List[Dict[str, Any]]:
        """Find all duplicate prompts.
        
        Returns:
            List of prompts that have duplicates
        """
        query = f"""
            SELECT p1.* 
            FROM {self._get_table_name()} p1
            INNER JOIN {self._get_table_name()} p2 
            ON p1.hash = p2.hash AND p1.id != p2.id
            ORDER BY p1.hash, p1.created_at
        """
        
        with self._get_connection() as conn:
            cursor = conn.execute(query)
            return [self._to_dict(row) for row in cursor.fetchall()]
    
    def increment_usage(self, prompt_id: int) -> bool:
        """Increment execution count and update last used time.
        
        Args:
            prompt_id: Prompt ID
            
        Returns:
            True if updated
        """
        query = f"""
            UPDATE {self._get_table_name()}
            SET execution_count = execution_count + 1,
                last_used = ?,
                updated_at = ?
            WHERE id = ?
        """
        
        now = datetime.utcnow().isoformat()
        
        with self._get_connection() as conn:
            cursor = conn.execute(query, (now, now, prompt_id))
            return cursor.rowcount > 0
    
    def get_recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recently used prompts, tolerating legacy schemas."""
        columns = self._get_table_columns()

        order_parts: List[str] = []

        if "last_used" in columns:
            order_parts.append("(last_used IS NOT NULL) DESC")
            order_parts.append("last_used DESC")
        if "updated_at" in columns:
            order_parts.append("updated_at DESC")
        if "created_at" in columns:
            order_parts.append("created_at DESC")
        if "id" in columns:
            order_parts.append("id DESC")

        order_by = ", ".join(order_parts) if order_parts else "rowid DESC"

        return self.list(limit=limit, order_by=order_by)
    
    def get_popular(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get most used prompts, tolerating legacy schemas."""
        columns = self._get_table_columns()

        order_parts: List[str] = []

        if "execution_count" in columns:
            order_parts.append("execution_count DESC")
        if "last_used" in columns:
            order_parts.append("(last_used IS NOT NULL) DESC")
            order_parts.append("last_used DESC")
        if "rating" in columns:
            order_parts.append("rating DESC")
        if "updated_at" in columns:
            order_parts.append("updated_at DESC")
        if "created_at" in columns:
            order_parts.append("created_at DESC")
        if "id" in columns:
            order_parts.append("id DESC")

        order_by = ", ".join(order_parts) if order_parts else "rowid DESC"

        return self.list(limit=limit, order_by=order_by)
    
    def get_by_category(self, category: str) -> List[Dict[str, Any]]:
        """Get prompts by category.
        
        Args:
            category: Category name
            
        Returns:
            List of prompts in category
        """
        return self.list(category=category)

    def list(self, 
             limit: int = 100, 
             offset: int = 0,
             order_by: str = "created_at DESC",
             **filters) -> List[Dict[str, Any]]:
        """List prompts with optional filtering, including multi-tag AND filtering.
        
        Overrides base list() to support multi-tag filtering with AND logic.
        When 'tags' filter is provided as a list, returns only prompts containing ALL tags.
        
        Args:
            limit: Maximum number of records
            offset: Number of records to skip
            order_by: SQL ORDER BY clause
            **filters: Column filters. Special handling for 'tags' as a list.
            
        Returns:
            List of dictionaries containing prompt data
            
        Example:
            # Single tag filter
            prompts = repo.list(tags="portrait")
            
            # Multi-tag AND filter - returns prompts with BOTH tags
            prompts = repo.list(tags=["portrait", "fantasy"])
        """
        # Extract tags filter if present
        tags_filter = filters.pop('tags', None)
        
        # Start with base filters (non-tag filters)
        table = self._get_table_name()
        where_clauses = []
        values = []
        
        # Handle standard filters
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
        
        # Handle multi-tag AND filtering
        if tags_filter is not None:
            if isinstance(tags_filter, list):
                # AND logic: prompt must contain ALL tags
                for tag in tags_filter:
                    # Tags are stored as JSON array, e.g., ["portrait", "fantasy"]
                    # Match the tag within the JSON representation
                    where_clauses.append(f"tags LIKE ?")
                    # Use JSON string format with quotes
                    values.append(f'%"{tag}"%')
            elif isinstance(tags_filter, str):
                # Single tag filter
                where_clauses.append(f"tags LIKE ?")
                values.append(f'%"{tags_filter}"%')
        
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
        """Count prompts with optional filtering, including multi-tag AND filtering.

        Overrides base count() to support multi-tag filtering with AND logic.

        Args:
            **filters: Column filters. Special handling for 'tags' as a list.

        Returns:
            Count of matching prompts
        """
        # Extract tags filter if present
        tags_filter = filters.pop('tags', None)

        # Start with base filters (non-tag filters)
        table = self._get_table_name()
        where_clauses = []
        values = []

        # Handle standard filters
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

        # Handle multi-tag AND filtering
        if tags_filter is not None:
            if isinstance(tags_filter, list):
                # AND logic: prompt must contain ALL tags
                for tag in tags_filter:
                    where_clauses.append(f"tags LIKE ?")
                    values.append(f'%"{tag}"%')
            elif isinstance(tags_filter, str):
                # Single tag filter
                where_clauses.append(f"tags LIKE ?")
                values.append(f'%"{tags_filter}"%')

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        query = f"SELECT COUNT(*) FROM {table} {where_sql}"

        with self._get_connection() as conn:
            cursor = conn.execute(query, values)
            return cursor.fetchone()[0]

    def get_categories(self) -> List[Dict[str, Any]]:
        """Get all categories with counts.

        Returns:
            List of category names with prompt counts
        """
        query = f"""
            SELECT category, COUNT(*) as count
            FROM {self._get_table_name()}
            GROUP BY category
            ORDER BY count DESC, category ASC
        """

        with self._get_connection() as conn:
            cursor = conn.execute(query)
            return [dict(row) for row in cursor.fetchall()]

    def get_statistics(self) -> Dict[str, Any]:
        """Compile aggregate statistics for dashboard views."""

        stats: Dict[str, Any] = {
            "prompt_count": 0,
            "rated_count": 0,
            "avg_rating": 0.0,
            "tag_count": 0,
            "top_categories": [],
        }

        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM prompts")
            stats["prompt_count"] = cursor.fetchone()[0] or 0

            cursor = conn.execute("SELECT COUNT(*) FROM prompts WHERE rating > 0")
            stats["rated_count"] = cursor.fetchone()[0] or 0

            cursor = conn.execute("SELECT AVG(rating) FROM prompts WHERE rating > 0")
            average = cursor.fetchone()[0]
            stats["avg_rating"] = float(average) if average is not None else 0.0

            cursor = conn.execute(
                "SELECT COUNT(DISTINCT json_each.value) AS total "
                "FROM prompts, json_each(CASE WHEN typeof(tags)='text' THEN tags ELSE json(tags) END)"
            )
            tag_row = cursor.fetchone()
            if tag_row and tag_row[0] is not None:
                stats["tag_count"] = tag_row[0]

            cursor = conn.execute(
                "SELECT category, COUNT(*) AS total "
                "FROM prompts GROUP BY category ORDER BY total DESC, category ASC LIMIT 10"
            )
            stats["top_categories"] = [
                {"name": row["category"], "count": row["total"]}
                for row in cursor.fetchall()
            ]

        return stats

    def update(self, id: int, data: Dict[str, Any]) -> bool:  # type: ignore[override]
        """Override base update to serialize JSON-compatible fields and support legacy schema."""
        normalized = dict(data)

        columns = self._get_table_columns()

        if "tags" in normalized:
            tags_value = normalized["tags"]
            if isinstance(tags_value, list):
                normalized["tags"] = json.dumps(tags_value)
            elif isinstance(tags_value, str):
                try:
                    parsed = json.loads(tags_value)
                    if isinstance(parsed, list):
                        normalized["tags"] = json.dumps(parsed)
                    else:
                        normalized["tags"] = json.dumps(_parse_tag_string(tags_value))
                except json.JSONDecodeError:
                    normalized["tags"] = json.dumps(_parse_tag_string(tags_value))
            elif tags_value is None:
                normalized["tags"] = json.dumps([])

        if "prompt" in normalized or "positive_prompt" in normalized:
            prompt_value = normalized.get("prompt") or normalized.get("positive_prompt")

            if "positive_prompt" in columns:
                normalized["positive_prompt"] = prompt_value
            else:
                normalized.pop("positive_prompt", None)

            if "prompt" in columns:
                normalized["prompt"] = prompt_value
            else:
                normalized.pop("prompt", None)

        return super().update(id, normalized)

    def get_tags(self) -> List[Dict[str, Any]]:
        """Get all unique tags with counts.

        Returns:
            List of tags with usage counts
        """
        # This would need a more complex query to parse JSON tags
        # For now, return empty list - can be implemented later
        return []
    
    def update_rating(self, prompt_id: int, rating: int) -> bool:
        """Update prompt rating.
        
        Args:
            prompt_id: Prompt ID
            rating: New rating (1-5)
            
        Returns:
            True if updated
        """
        return self.update(prompt_id, {"rating": rating})
    
    def bulk_update_category(self, prompt_ids: List[int], category: str) -> int:
        """Update category for multiple prompts.
        
        Args:
            prompt_ids: List of prompt IDs
            category: New category
            
        Returns:
            Number of updated prompts
        """
        if not prompt_ids:
            return 0
        
        placeholders = ",".join(["?" for _ in prompt_ids])
        query = f"""
            UPDATE {self._get_table_name()}
            SET category = ?, updated_at = ?
            WHERE id IN ({placeholders})
        """
        
        values = [category, datetime.utcnow().isoformat()] + prompt_ids
        
        with self._get_connection() as conn:
            cursor = conn.execute(query, values)
            return cursor.rowcount
def _parse_tag_string(value: str) -> List[str]:
    """Parse a comma-separated tag string into a normalized list."""
    return [
        part.strip().lower()
        for part in value.split(',')
        if part.strip()
    ]
