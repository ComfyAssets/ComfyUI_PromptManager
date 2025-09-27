"""
Database models for PromptManager v2.

Creates and manages the database schema with proper migrations.
"""

import datetime
import sqlite3
import os
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Set

# Import logging with proper fallback
try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger
except ImportError:  # pragma: no cover
    from loggers import get_logger


class PromptModel:
    """Database model for prompt storage and schema management."""

    def __init__(self, db_path: str):
        """
        Initialize the database model.

        Args:
            db_path: Full path to the SQLite database file
        """
        self.logger = get_logger('prompt_manager.database.models')
        self.logger.debug(f"Initializing database model with path: {db_path}")
        self.db_path = db_path
        self._ensure_database_exists()

    def _ensure_database_exists(self) -> None:
        """
        Create database and tables if they don't exist.

        Sets up the database schema including tables for prompts and generated images,
        creates necessary indexes, and applies any pending migrations.

        Raises:
            Exception: If database creation fails
        """
        try:
            # Ensure directory exists
            db_dir = Path(self.db_path).parent
            db_dir.mkdir(parents=True, exist_ok=True)

            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row

                # Enable WAL mode for better concurrency
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")  # 5 second timeout
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA mmap_size=268435456")  # 256MB

                conn.execute("PRAGMA foreign_keys = ON")
                self._create_tables(conn)
                self._migrate_schema(conn)
                self._create_indexes(conn)
                conn.commit()
                self.logger.info(f"Database initialized at: {self.db_path} (WAL mode enabled)")
        except Exception as e:
            self.logger.error(f"Error creating database: {e}")
            raise

    def _create_tables(self, conn: sqlite3.Connection) -> None:
        """
        Create the prompts and generated_images tables with all required columns.

        Args:
            conn: Active database connection

        Creates:
            - prompts table: Stores prompt text and metadata
            - generated_images table: Links generated images to their source prompts
        """
        self._create_prompts_table(conn)
        self._create_generated_images_table(conn)
        self._create_settings_table(conn)
        self._create_prompt_tracking_table(conn)

        self.logger.debug("Database tables created successfully")

    def _create_indexes(self, conn: sqlite3.Connection) -> None:
        """
        Create indexes for better query performance.

        Args:
            conn: Active database connection

        Creates indexes on:
            - Positive and negative prompts for search operations
            - Categories and tags for filtering
            - Timestamps for sorting
            - Hash values for duplicate detection
        """
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_prompts_positive ON prompts(positive_prompt)",
            "CREATE INDEX IF NOT EXISTS idx_prompts_negative ON prompts(negative_prompt)",
            "CREATE INDEX IF NOT EXISTS idx_prompts_category ON prompts(category)",
            "CREATE INDEX IF NOT EXISTS idx_prompts_created_at ON prompts(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_prompts_hash ON prompts(hash)",
            "CREATE INDEX IF NOT EXISTS idx_prompts_rating ON prompts(rating)",
            "CREATE INDEX IF NOT EXISTS idx_prompt_images ON generated_images(prompt_id)",
            "CREATE INDEX IF NOT EXISTS idx_image_path ON generated_images(image_path)",
            "CREATE INDEX IF NOT EXISTS idx_generation_time ON generated_images(generation_time)",
            "CREATE INDEX IF NOT EXISTS idx_tracking_session ON prompt_tracking(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_tracking_created ON prompt_tracking(created_at)",
        ]

        for index_sql in indexes:
            conn.execute(index_sql)

        self.logger.debug("Database indexes created successfully")

    def _create_prompts_table(self, conn: sqlite3.Connection) -> None:
        """Ensure the prompts table exists with the expected schema."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                positive_prompt TEXT NOT NULL,
                negative_prompt TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                category TEXT,
                tags TEXT,
                rating INTEGER CHECK(rating >= 1 AND rating <= 5),
                notes TEXT,
                hash TEXT UNIQUE,
                model_hash TEXT,
                sampler_settings TEXT,
                generation_params TEXT
            )
            """
        )

    def _create_generated_images_table(self, conn: sqlite3.Connection) -> None:
        """Ensure the generated_images table exists with the expected schema."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS generated_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_id INTEGER NOT NULL,
                image_path TEXT NOT NULL,
                filename TEXT NOT NULL,
                generation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                file_size INTEGER,
                width INTEGER,
                height INTEGER,
                format TEXT,
                workflow_data TEXT,
                prompt_metadata TEXT,
                parameters TEXT,
                FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE
            )
            """
        )

    def _create_settings_table(self, conn: sqlite3.Connection) -> None:
        """Ensure the settings table exists for configuration metadata."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    def _create_prompt_tracking_table(self, conn: sqlite3.Connection) -> None:
        """Ensure the prompt_tracking table exists for tracking metadata."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prompt_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                prompt_text TEXT NOT NULL,
                node_id TEXT,
                workflow_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT
            )
            """
        )

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """Upgrade legacy schemas in-place so new columns and indexes exist."""
        self._migrate_prompts_table(conn)
        self._migrate_generated_images_table(conn)

    def _migrate_prompts_table(self, conn: sqlite3.Connection) -> None:
        columns = self._get_table_columns(conn, "prompts")
        if not columns:
            return

        required_columns = {
            "id",
            "positive_prompt",
            "negative_prompt",
            "category",
            "tags",
            "rating",
            "notes",
            "hash",
            "model_hash",
            "sampler_settings",
            "generation_params",
            "created_at",
            "updated_at",
        }

        legacy_markers = {"prompt", "text", "workflow_name"}
        if required_columns.issubset(columns) and not (columns & legacy_markers):
            return  # Schema already matches expectations

        self.logger.info("Migrating legacy prompts table to v2 schema")
        temp_table = "prompts__legacy_backup"
        self._drop_table_if_exists(conn, temp_table)

        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute(f'ALTER TABLE prompts RENAME TO "{temp_table}"')
            self._create_prompts_table(conn)

            rows = conn.execute(f'SELECT * FROM "{temp_table}"').fetchall()
            for row in rows:
                record = dict(row)
                prompt_id = record.get("id")
                positive_prompt = self._coalesce_fields(
                    record,
                    ("positive_prompt", "prompt", "text", "positive"),
                    default="",
                ).strip()
                negative_prompt = self._coalesce_fields(
                    record,
                    ("negative_prompt", "negative", "negative_text"),
                    default="",
                ).strip()
                category = record.get("category")
                tags = record.get("tags")
                rating = record.get("rating")
                notes = record.get("notes")
                hash_value = record.get("hash")
                model_hash = record.get("model_hash")
                sampler_settings = record.get("sampler_settings") or record.get("sampler_config")
                generation_params = record.get("generation_params") or record.get("metadata")

                created_at = record.get("created_at") or record.get("created")
                updated_at = record.get("updated_at") or record.get("updated") or created_at
                timestamp_now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                if not created_at:
                    created_at = timestamp_now
                if not updated_at:
                    updated_at = timestamp_now

                conn.execute(
                    """
                    INSERT INTO prompts (
                        id, positive_prompt, negative_prompt, category, tags, rating, notes,
                        hash, model_hash, sampler_settings, generation_params, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prompt_id,
                        positive_prompt,
                        negative_prompt or None,
                        category,
                        tags,
                        rating,
                        notes,
                        hash_value,
                        model_hash,
                        sampler_settings,
                        generation_params,
                        created_at,
                        updated_at,
                    ),
                )

            conn.execute(f'DROP TABLE IF EXISTS "{temp_table}"')
        finally:
            conn.execute("PRAGMA foreign_keys = ON")

    def _migrate_generated_images_table(self, conn: sqlite3.Connection) -> None:
        columns = self._get_table_columns(conn, "generated_images")
        if not columns:
            return

        required_columns = {
            "id",
            "prompt_id",
            "image_path",
            "filename",
            "generation_time",
            "file_size",
            "width",
            "height",
            "format",
            "workflow_data",
            "prompt_metadata",
            "parameters",
        }

        legacy_markers = {"file_path", "file_name", "metadata"}
        if required_columns.issubset(columns) and not (columns & legacy_markers):
            return

        self.logger.info("Migrating legacy generated_images table to v2 schema")
        temp_table = "generated_images__legacy_backup"
        self._drop_table_if_exists(conn, temp_table)

        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute(f'ALTER TABLE generated_images RENAME TO "{temp_table}"')
            self._create_generated_images_table(conn)

            rows = conn.execute(f'SELECT * FROM "{temp_table}"').fetchall()
            for row in rows:
                record = dict(row)
                prompt_id = self._safe_int(record.get("prompt_id"))
                if prompt_id is None:
                    continue

                image_path = self._coalesce_fields(
                    record,
                    ("image_path", "file_path", "path", "filepath"),
                    default="",
                )
                if not image_path:
                    continue

                filename = self._coalesce_fields(
                    record,
                    ("filename", "file_name", "name"),
                    default=os.path.basename(image_path),
                )

                generation_time = record.get("generation_time")
                if not generation_time:
                    generation_time = datetime.datetime.now(datetime.timezone.utc).isoformat()

                file_size = self._safe_int(record.get("file_size"))
                width = self._safe_int(record.get("width") or record.get("image_width"))
                height = self._safe_int(record.get("height") or record.get("image_height"))
                format_value = self._coalesce_fields(
                    record,
                    ("format", "image_format"),
                    default=Path(image_path).suffix.lstrip("."),
                )

                workflow_data = record.get("workflow_data") or record.get("workflow")
                prompt_metadata = record.get("prompt_metadata")
                parameters = record.get("parameters")
                if not parameters:
                    parameters = record.get("metadata")

                conn.execute(
                    """
                    INSERT INTO generated_images (
                        id, prompt_id, image_path, filename, generation_time, file_size,
                        width, height, format, workflow_data, prompt_metadata, parameters
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.get("id"),
                        prompt_id,
                        image_path,
                        filename,
                        generation_time,
                        file_size,
                        width,
                        height,
                        format_value,
                        workflow_data,
                        prompt_metadata,
                        parameters,
                    ),
                )

            conn.execute(f'DROP TABLE IF EXISTS "{temp_table}"')
        finally:
            conn.execute("PRAGMA foreign_keys = ON")

    def _get_table_columns(self, conn: sqlite3.Connection, table: str) -> Set[str]:
        try:
            cursor = conn.execute(f"PRAGMA table_info({table})")
        except sqlite3.Error:
            return set()
        return {
            (row["name"] if isinstance(row, sqlite3.Row) else row[1])
            for row in cursor.fetchall()
        }

    def _drop_table_if_exists(self, conn: sqlite3.Connection, table: str) -> None:
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')

    def _coalesce_fields(
        self,
        record: Dict[str, Any],
        keys: Sequence[str],
        *,
        default: str = "",
    ) -> str:
        for key in keys:
            value = record.get(key)
            if value not in (None, ""):
                return str(value)
        return default

    def _safe_int(self, value: Optional[object]) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def get_connection(self) -> sqlite3.Connection:
        """
        Get a database connection with proper configuration.

        Returns:
            sqlite3.Connection: Configured database connection
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable dict-like access to rows
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def vacuum_database(self) -> None:
        """
        Optimize database by running VACUUM command.

        Reclaims unused space and defragments the database file,
        improving query performance and reducing file size.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("VACUUM")
                conn.commit()
                self.logger.info("Database vacuum completed")
        except Exception as e:
            self.logger.error(f"Error vacuuming database: {e}")

    def get_database_info(self) -> dict:
        """
        Get information about the database.

        Returns:
            dict: Database statistics and information
        """
        try:
            with self.get_connection() as conn:
                # Get prompt count
                cursor = conn.execute("SELECT COUNT(*) as total_prompts FROM prompts")
                total_prompts = cursor.fetchone()['total_prompts']

                # Get unique categories
                cursor = conn.execute(
                    "SELECT COUNT(DISTINCT category) as unique_categories FROM prompts WHERE category IS NOT NULL"
                )
                unique_categories = cursor.fetchone()['unique_categories']

                # Get average rating
                cursor = conn.execute(
                    "SELECT AVG(rating) as avg_rating FROM prompts WHERE rating IS NOT NULL"
                )
                avg_rating = cursor.fetchone()['avg_rating']

                # Get image count
                cursor = conn.execute("SELECT COUNT(*) as total_images FROM generated_images")
                total_images = cursor.fetchone()['total_images']

                # Get tracking count
                cursor = conn.execute("SELECT COUNT(*) as total_tracked FROM prompt_tracking")
                total_tracked = cursor.fetchone()['total_tracked']

                # Get database file size
                db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0

                return {
                    'total_prompts': total_prompts,
                    'unique_categories': unique_categories,
                    'average_rating': round(avg_rating, 2) if avg_rating else None,
                    'total_images': total_images,
                    'total_tracked': total_tracked,
                    'database_size_bytes': db_size,
                    'database_path': os.path.abspath(self.db_path)
                }
        except Exception as e:
            self.logger.error(f"Error getting database info: {e}")
            return {}

    def backup_database(self, backup_path: str) -> bool:
        """
        Create a backup of the database.

        Args:
            backup_path: Path where the backup should be saved

        Returns:
            bool: True if backup was successful, False otherwise
        """
        try:
            import shutil

            # Ensure backup directory exists
            backup_dir = Path(backup_path).parent
            backup_dir.mkdir(parents=True, exist_ok=True)

            shutil.copy2(self.db_path, backup_path)
            self.logger.info(f"Database backed up to: {backup_path}")
            return True
        except Exception as e:
            self.logger.error(f"Error creating database backup: {e}")
            return False
