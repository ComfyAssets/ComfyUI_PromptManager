"""
Database schema and models for KikoTextEncode prompt storage.
"""

import contextlib
import datetime
import shutil
import sqlite3
import os
import threading
from pathlib import Path
from typing import Set, Tuple

# Import logging system
try:
    from ..utils.logging_config import get_logger
except ImportError:
    import sys

    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, current_dir)
    from utils.logging_config import get_logger

# last_used_at is UTC ISO text so ORDER BY can compare it as text. The backfill
# writes milliseconds (SQLite's %f); live writes use microseconds so runs in the
# same millisecond still order correctly. Both forms sort correctly together.
ISO_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%f+00:00"


def utc_now_iso() -> str:
    """Current UTC time as ISO text with microseconds.

    Example: 2026-10-07T18:39:06.758123+00:00
    """
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="microseconds"
    )


def normalize_image_path(path: str) -> str:
    """Canonical form of an image path for generated_images.file_path.

    Absolute, with '..' and '.' collapsed and the platform's case folding
    applied, so two spellings of one file (or the same basename in two date
    folders) compare the way the filesystem would.
    """
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


SQLITE_HEADER = b"SQLite format 3\x00"
REQUIRED_PROMPT_COLUMNS = ("id", "text", "created_at")


class PromptModel:
    """Database model for prompt storage and schema management."""

    # Paths (normalised, see _schema_key) whose schema has been created and
    # migrated in this process. Many PromptDatabase instances point at the
    # same file; only the first one pays for the schema check.
    _initialized_paths: Set[str] = set()
    _init_lock = threading.Lock()

    def __init__(self, db_path: str):
        """
        Initialize the database model.

        Args:
            db_path: Absolute path to the SQLite database file
        """
        self.logger = get_logger("prompt_manager.database.models")
        self.logger.debug(f"Initializing database model with path: {db_path}")
        self.db_path = db_path
        # One connection per thread (see get_connection); every open connection
        # is also tracked here so restore_from_file can close them all.
        self._local = threading.local()
        self._connections: Set[sqlite3.Connection] = set()
        self._conn_lock = threading.Lock()
        # Bumped by _close_all_connections so threads holding a closed
        # connection reopen on their next get_connection().
        self._generation = 0
        self._ensure_database_exists()

    def _ensure_database_exists(self) -> None:
        """
        Create database and tables if they don't exist.

        Sets up the database schema including tables for prompts and generated images,
        creates necessary indexes, and applies any pending migrations.

        Raises:
            Exception: If database creation fails
        """
        key = self._schema_key()
        with PromptModel._init_lock:
            if key in PromptModel._initialized_paths and os.path.exists(self.db_path):
                return
            try:
                conn = sqlite3.connect(self.db_path)
                try:
                    conn.execute("PRAGMA journal_mode = WAL")
                    conn.execute("PRAGMA busy_timeout = 5000")
                    conn.execute("PRAGMA foreign_keys = ON")
                    self._create_tables(conn)
                    self._create_indexes(conn)
                    conn.commit()
                finally:
                    conn.close()
            except Exception as e:
                self.logger.error(f"Error creating database: {e}")
                raise
            PromptModel._initialized_paths.add(key)

    def _schema_key(self) -> str:
        """Normalised identity of db_path for the once-per-process schema cache."""
        return os.path.normcase(os.path.realpath(self.db_path))

    @classmethod
    def reset_schema_cache(cls) -> None:
        """Forget which databases were initialised (tests and restores)."""
        with cls._init_lock:
            cls._initialized_paths.clear()

    @contextlib.contextmanager
    def _table_rebuild(self, conn: sqlite3.Connection):
        """Run a copy-and-swap table rebuild with foreign keys disabled.

        DROP TABLE on a parent table with foreign_keys ON performs an implicit
        DELETE that cascades into child rows (generated_images, prompt_tags),
        so the rebuild runs with the pragma off. The pragma is a no-op inside
        a transaction, hence the commits around it. Errors roll back and
        propagate after the pragma is restored.
        """
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        # Explicit BEGIN: in sqlite3's legacy transaction mode DDL autocommits,
        # so without it a failed copy would leave the *_new table behind.
        conn.execute("BEGIN")
        try:
            yield
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")

    def _create_tables(self, conn: sqlite3.Connection) -> None:
        """
        Create the prompts and generated_images tables with all required columns.

        Args:
            conn: Active database connection

        Creates:
            - prompts table: Stores prompt text and metadata
            - generated_images table: Links generated images to their source prompts
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                category TEXT,
                tags TEXT,
                rating INTEGER CHECK(rating >= 1 AND rating <= 5),
                notes TEXT,
                hash TEXT UNIQUE,
                last_used_at TIMESTAMP,
                run_count INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Create images table for gallery functionality
        conn.execute("""
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
                file_path TEXT,
                FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE,
                UNIQUE(prompt_id, file_path)
            )
        """)

        # Create normalized tag tables (junction table pattern)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prompt_tags (
                prompt_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (prompt_id, tag_id),
                FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )
        """)

        # Check if we need to migrate from old schema with workflow_name
        self._migrate_workflow_name_removal(conn)

        # Fix foreign key data type mismatch
        self._migrate_foreign_key_types(conn)

        # Migrate JSON tags to normalized junction tables
        self._migrate_json_tags_to_junction(conn)

        # Usage tracking columns (after any migration that rebuilds prompts)
        self._migrate_add_usage_columns(conn)

        # Image uniqueness by full path instead of basename (3.2.4)
        self._migrate_image_uniqueness_by_path(conn)

    def _create_indexes(self, conn: sqlite3.Connection) -> None:
        """
        Create indexes for better query performance.

        Args:
            conn: Active database connection

        Creates indexes on:
            - Text content for search operations
            - Categories and tags for filtering
            - Timestamps for sorting
            - Hash values for duplicate detection
        """
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_prompts_text ON prompts(text)",
            "CREATE INDEX IF NOT EXISTS idx_prompts_category ON prompts(category)",
            "CREATE INDEX IF NOT EXISTS idx_prompts_created_at ON prompts(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_prompts_hash ON prompts(hash)",
            "CREATE INDEX IF NOT EXISTS idx_prompts_rating ON prompts(rating)",
            "CREATE INDEX IF NOT EXISTS idx_prompts_last_used ON prompts(last_used_at)",
            "CREATE INDEX IF NOT EXISTS idx_prompts_run_count ON prompts(run_count)",
            "CREATE INDEX IF NOT EXISTS idx_prompt_images ON "
            "generated_images(prompt_id)",
            "CREATE INDEX IF NOT EXISTS idx_image_path ON generated_images(image_path)",
            "CREATE INDEX IF NOT EXISTS idx_image_file_path ON "
            "generated_images(file_path)",
            "CREATE INDEX IF NOT EXISTS idx_generation_time ON "
            "generated_images(generation_time)",
            "CREATE INDEX IF NOT EXISTS idx_prompt_tags_tag ON prompt_tags(tag_id)",
            "CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name)",
        ]

        for index_sql in indexes:
            conn.execute(index_sql)

    def get_connection(self) -> sqlite3.Connection:
        """
        Get the calling thread's database connection.

        Each thread gets its own ``sqlite3.Connection`` (kept in
        ``threading.local``), created on first use and reused by that thread
        thereafter. Every connection is configured with WAL journaling,
        ``row_factory = sqlite3.Row``, ``PRAGMA foreign_keys = ON`` and
        ``PRAGMA busy_timeout = 5000`` so concurrent writers wait instead of
        failing. Callers use it as a context manager (``with conn:``), which
        commits or rolls back that thread's transaction and leaves the
        connection open. No lock is held while a connection is in use.

        Returns:
            sqlite3.Connection: The current thread's configured connection
        """
        conn = getattr(self._local, "conn", None)
        if conn is not None and self._local.generation == self._generation:
            return conn

        # check_same_thread=False only so restore_from_file can close other
        # threads' connections; each connection is still used by one thread.
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        self._local.conn = conn
        self._local.generation = self._generation
        with self._conn_lock:
            self._connections.add(conn)
        return conn

    def _close_all_connections(self) -> None:
        """Close every thread's connection; they reopen lazily on next use."""
        with self._conn_lock:
            connections = list(self._connections)
            self._connections.clear()
            self._generation += 1
        self._local.conn = None
        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error as e:
                self.logger.warning(f"Error closing database connection: {e}")

    def close(self) -> None:
        """Close the calling thread's connection, if it has one.

        Other threads keep their connections. Call this before deleting or
        replacing the database file: Windows refuses to delete an open file.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        self._local.conn = None
        with self._conn_lock:
            self._connections.discard(conn)
        try:
            conn.close()
        except sqlite3.Error as e:
            self.logger.warning(f"Error closing database connection: {e}")

    def _migrate_workflow_name_removal(self, conn: sqlite3.Connection) -> None:
        """
        Remove workflow_name column if it exists in existing database.

        Args:
            conn: Active database connection

        This migration handles legacy schema updates by removing the deprecated
        workflow_name column while preserving all other data.

        Raises:
            sqlite3.Error: If the rebuild fails (logged, nothing half-applied)
        """
        try:
            cursor = conn.execute("PRAGMA table_info(prompts)")
            columns = [column[1] for column in cursor.fetchall()]
            if "workflow_name" not in columns:
                return

            self.logger.info("Migrating database: removing workflow_name column")
            with self._table_rebuild(conn):
                # A previous attempt may have died between CREATE and RENAME
                conn.execute("DROP TABLE IF EXISTS prompts_new")
                conn.execute("""
                    CREATE TABLE prompts_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        text TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        category TEXT,
                        tags TEXT,
                        rating INTEGER CHECK(rating >= 1 AND rating <= 5),
                        notes TEXT,
                        hash TEXT UNIQUE
                    )
                """)
                conn.execute("""
                    INSERT INTO prompts_new (id, text, created_at, updated_at,
                        category, tags, rating, notes, hash)
                    SELECT id, text, created_at, updated_at, category, tags, rating,
                        notes, hash
                    FROM prompts
                """)
                conn.execute("DROP TABLE prompts")
                conn.execute("ALTER TABLE prompts_new RENAME TO prompts")
            self.logger.info("Database migration completed")

        except sqlite3.Error as e:
            self.logger.error(f"Migration error (workflow_name removal): {e}")
            raise

    def _migrate_foreign_key_types(self, conn: sqlite3.Connection) -> None:
        """
        Fix foreign key data type mismatch in generated_images table.

        Args:
            conn: Active database connection

        Converts prompt_id from TEXT to INTEGER type to match the prompts table's
        primary key type, ensuring referential integrity.

        Raises:
            sqlite3.Error: If the rebuild fails (logged, nothing half-applied)
        """
        try:
            cursor = conn.execute("PRAGMA table_info(generated_images)")
            columns = {column[1]: column[2] for column in cursor.fetchall()}
            if columns.get("prompt_id") != "TEXT":
                return

            self.logger.info("Migrating foreign key types: prompt_id TEXT -> INTEGER")
            with self._table_rebuild(conn):
                conn.execute("DROP TABLE IF EXISTS generated_images_new")
                conn.execute("""
                    CREATE TABLE generated_images_new (
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
                """)
                conn.execute("""
                    INSERT INTO generated_images_new
                    (id, prompt_id, image_path, filename, generation_time, file_size,
                     width, height, format, workflow_data, prompt_metadata, parameters)
                    SELECT id, CAST(prompt_id AS INTEGER), image_path, filename,
                        generation_time,
                           file_size, width, height, format, workflow_data,
                               prompt_metadata, parameters
                    FROM generated_images
                    WHERE prompt_id != '' AND prompt_id IS NOT NULL
                    AND CAST(prompt_id AS INTEGER) IN (SELECT id FROM prompts)
                """)
                conn.execute("DROP TABLE generated_images")
                conn.execute(
                    "ALTER TABLE generated_images_new RENAME TO generated_images"
                )
            self.logger.info("Foreign key migration completed")

        except sqlite3.Error as e:
            self.logger.error(f"Migration error (foreign key types): {e}")
            raise

    def _migrate_json_tags_to_junction(self, conn: sqlite3.Connection) -> None:
        """
        Populate tags and prompt_tags tables from legacy JSON tags column.

        Runs once: skips if the tags table already has data. Uses json_each()
        to extract tag names from the JSON arrays stored in prompts.tags.

        Raises:
            sqlite3.Error: If the migration fails (logged)
        """
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM tags")
            if cursor.fetchone()[0] > 0:
                return  # Already migrated

            cursor = conn.execute(
                "SELECT COUNT(*) FROM prompts "
                "WHERE tags IS NOT NULL AND tags != '' AND tags != '[]'"
            )
            if cursor.fetchone()[0] == 0:
                return  # No tags to migrate

            self.logger.info("Migrating JSON tags to normalized junction tables")

            # Insert all unique tag names
            conn.execute(
                "INSERT OR IGNORE INTO tags (name) "
                "SELECT DISTINCT je.value FROM prompts, json_each(prompts.tags) AS je "
                "WHERE prompts.tags IS NOT NULL AND prompts.tags != '' AND "
                "prompts.tags != '[]'"
            )

            # Populate junction table
            conn.execute(
                "INSERT OR IGNORE INTO prompt_tags (prompt_id, tag_id) "
                "SELECT p.id, t.id "
                "FROM prompts p, json_each(p.tags) AS je "
                "JOIN tags t ON t.name = je.value "
                "WHERE p.tags IS NOT NULL AND p.tags != '' AND p.tags != '[]'"
            )

            tag_count = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
            link_count = conn.execute("SELECT COUNT(*) FROM prompt_tags").fetchone()[0]
            self.logger.info(
                f"Tag migration complete: {tag_count} unique tags, {link_count} "
                "prompt-tag links"
            )

        except sqlite3.Error as e:
            self.logger.error(f"Migration error (tag junction): {e}")
            raise

    def _migrate_add_usage_columns(self, conn: sqlite3.Connection) -> None:
        """
        Add last_used_at and run_count (3.2.4) and estimate usage for existing rows.

        The backfill runs only when the columns are added, i.e. on the upgrade
        from a version without usage tracking. Afterwards a NULL last_used_at
        means "never run": saving a prompt does not stamp it, only
        record_prompt_use does, and "Recently Used" relies on that.

        The estimate comes from linked images: run_count is the image count (at
        least 1) and last_used_at the newest image time, falling back to created_at.
        Timestamps are compared with julianday() because prompts use ISO 'T'
        timestamps while generated_images uses SQLite's CURRENT_TIMESTAMP format.
        """
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(prompts)")}
            added = False
            if "last_used_at" not in columns:
                self.logger.info("Adding prompt usage column last_used_at")
                conn.execute("ALTER TABLE prompts ADD COLUMN last_used_at TIMESTAMP")
                added = True
            if "run_count" not in columns:
                self.logger.info("Adding prompt usage column run_count")
                conn.execute(
                    "ALTER TABLE prompts ADD COLUMN run_count INTEGER NOT NULL "
                    "DEFAULT 0"
                )
                added = True
            if not added:
                return

            cursor = conn.execute(f"""
                UPDATE prompts SET
                    run_count = MAX(run_count, 1, (
                        SELECT COUNT(*) FROM generated_images gi
                        WHERE gi.prompt_id = prompts.id
                    )),
                    last_used_at = strftime('{ISO_TIMESTAMP_FORMAT}', MAX(
                        COALESCE(julianday(created_at), julianday('now')),
                        COALESCE((
                            SELECT MAX(julianday(gi.generation_time))
                            FROM generated_images gi WHERE gi.prompt_id = prompts.id
                        ), 0)
                    ))
                WHERE last_used_at IS NULL
            """)
            if cursor.rowcount:
                self.logger.info(f"Estimated usage for {cursor.rowcount} prompts")
        except sqlite3.Error as e:
            self.logger.error(f"Migration error (usage columns): {e}")
            raise

    def _migrate_image_uniqueness_by_path(self, conn: sqlite3.Connection) -> None:
        """
        Rebuild generated_images so an image is unique per (prompt_id, file_path).

        Older schemas were unique per (prompt_id, filename), i.e. the basename,
        so ComfyUI's same-named files in different date folders were silently
        dropped. file_path holds normalize_image_path(image_path); rows whose
        paths normalise to the same file collapse to the newest one.

        Raises:
            sqlite3.Error: If the rebuild fails (logged, nothing half-applied)
        """
        try:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(generated_images)")
            }
            if "file_path" in columns:
                return

            self.logger.info("Migrating database: image uniqueness by full path")
            with self._table_rebuild(conn):
                rows = conn.execute(
                    "SELECT id, prompt_id, image_path FROM generated_images ORDER BY id"
                ).fetchall()
                keep = {}
                for row_id, prompt_id, image_path in rows:
                    keep[(prompt_id, normalize_image_path(image_path))] = row_id

                conn.execute("DROP TABLE IF EXISTS generated_images_new")
                conn.execute("""
                    CREATE TABLE generated_images_new (
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
                        file_path TEXT,
                        FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE
                            CASCADE,
                        UNIQUE(prompt_id, file_path)
                    )
                """)
                conn.executemany(
                    """
                    INSERT INTO generated_images_new
                    (id, prompt_id, image_path, filename, generation_time, file_size,
                     width, height, format, workflow_data, prompt_metadata, parameters,
                     file_path)
                    SELECT id, prompt_id, image_path, filename, generation_time,
                        file_size,
                           width, height, format, workflow_data, prompt_metadata,
                               parameters,
                           ?
                    FROM generated_images WHERE id = ?
                    """,
                    [(file_path, row_id) for (_, file_path), row_id in keep.items()],
                )
                conn.execute("DROP TABLE generated_images")
                conn.execute(
                    "ALTER TABLE generated_images_new RENAME TO generated_images"
                )
            dropped = len(rows) - len(keep)
            if dropped:
                self.logger.info(f"Collapsed {dropped} duplicate image rows")
            self.logger.info("Image path uniqueness migration completed")

        except sqlite3.Error as e:
            self.logger.error(f"Migration error (image path uniqueness): {e}")
            raise

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
                cursor = conn.execute("SELECT COUNT(*) as total_prompts FROM prompts")
                total_prompts = cursor.fetchone()["total_prompts"]

                cursor = conn.execute(
                    "SELECT COUNT(DISTINCT category) as unique_categories FROM "
                    "prompts WHERE category IS NOT NULL"
                )
                unique_categories = cursor.fetchone()["unique_categories"]

                cursor = conn.execute(
                    "SELECT AVG(rating) as avg_rating FROM prompts WHERE rating IS "
                    "NOT NULL"
                )
                avg_rating = cursor.fetchone()["avg_rating"]

                # Get database file size
                db_size = (
                    os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
                )

                return {
                    "total_prompts": total_prompts,
                    "unique_categories": unique_categories,
                    "average_rating": round(avg_rating, 2) if avg_rating else None,
                    "database_size_bytes": db_size,
                    "database_path": os.path.abspath(self.db_path),
                }
        except Exception as e:
            self.logger.error(f"Error getting database info: {e}")
            return {}

    def backup_database(self, backup_path: str) -> bool:
        """
        Write a consistent copy of the database to backup_path.

        Uses the SQLite online backup API through this thread's connection,
        so rows that are committed but still sit in the -wal file are
        included and other threads may keep reading and writing meanwhile.

        Args:
            backup_path: Path where the backup should be saved (overwritten)

        Returns:
            bool: True if backup was successful, False otherwise
        """
        try:
            if os.path.exists(backup_path) and os.path.samefile(
                backup_path, self.db_path
            ):
                self.logger.error("Refusing to back up the database onto itself")
                return False
            if os.path.exists(backup_path):
                os.remove(backup_path)
            source = self.get_connection()
            destination = sqlite3.connect(backup_path)
            try:
                source.backup(destination)
            finally:
                destination.close()
            return True
        except (OSError, sqlite3.Error) as e:
            self.logger.error(f"Error creating database backup: {e}")
            return False

    @staticmethod
    def verify_database_file(path: str) -> Tuple[bool, str]:
        """
        Check that a file is a healthy prompts database before restoring it.

        Opens the file read-only, runs PRAGMA integrity_check and confirms the
        prompts table has the columns every version of this extension needs.

        Args:
            path: File to inspect

        Returns:
            (ok, reason): reason is "ok" when the file is acceptable, otherwise
            a human-readable explanation. Never raises for bad input.
        """
        if not os.path.isfile(path):
            return False, "File not found"
        try:
            with open(path, "rb") as fh:
                header = fh.read(len(SQLITE_HEADER))
        except OSError as e:
            return False, f"Cannot read file: {e}"
        if header != SQLITE_HEADER:
            return False, "Not a SQLite database file"

        uri = Path(os.path.abspath(path)).as_uri() + "?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True)
        except sqlite3.Error as e:
            return False, f"Invalid SQLite database: {e}"
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            verdict = row[0] if row else "no result"
            if verdict != "ok":
                return False, f"Integrity check failed: {verdict}"
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='prompts'"
            ).fetchone()
            if not table:
                return False, "Database does not contain a 'prompts' table"
            columns = {r[1] for r in conn.execute("PRAGMA table_info(prompts)")}
            missing = [c for c in REQUIRED_PROMPT_COLUMNS if c not in columns]
            if missing:
                return (
                    False,
                    "prompts table is missing required columns: " + ", ".join(missing),
                )
            return True, "ok"
        except sqlite3.Error as e:
            return False, f"Invalid SQLite database: {e}"
        finally:
            conn.close()

    def restore_from_file(self, src_path: str) -> str:
        """
        Replace the live database with the file at src_path.

        The current database is first backed up next to itself as
        ``<db_path>.backup_<YYYYmmdd_HHMMSS>`` (WAL content included), every
        thread's connection is closed, the file is copied over, stale -wal and
        -shm files are removed and the schema migrations run on the restored
        file. Connections reopen lazily on the next get_connection().

        Callers should run verify_database_file(src_path) first.

        Args:
            src_path: SQLite file to restore

        Returns:
            Path of the backup taken of the previous database, or "" when
            there was no database to back up.

        Raises:
            RuntimeError: If the previous database could not be backed up
            OSError: If the restored file could not be copied into place
        """
        backup_path = ""
        if os.path.exists(self.db_path):
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{self.db_path}.backup_{timestamp}"
            if not self.backup_database(backup_path):
                raise RuntimeError("Could not back up the current database")
            self.logger.info(f"Current database backed up to: {backup_path}")

        self._close_all_connections()
        shutil.copy2(src_path, self.db_path)
        for suffix in ("-wal", "-shm"):
            stale = self.db_path + suffix
            if os.path.exists(stale):
                os.remove(stale)
        with PromptModel._init_lock:
            PromptModel._initialized_paths.discard(self._schema_key())
        self._ensure_database_exists()
        self.logger.info(f"Database restored from {src_path}")
        return backup_path
