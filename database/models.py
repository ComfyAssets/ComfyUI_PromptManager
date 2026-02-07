"""
Database schema and models for KikoTextEncode prompt storage.
"""

import sqlite3
import os
import threading
from typing import Optional

# Import logging system
try:
    from ..utils.logging_config import get_logger
except ImportError:
    import sys
    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, current_dir)
    from utils.logging_config import get_logger


class PromptModel:
    """Database model for prompt storage and schema management."""
    
    def __init__(self, db_path: str = "prompts.db"):
        """
        Initialize the database model.
        
        Args:
            db_path: Path to the SQLite database file
        """
        self.logger = get_logger('prompt_manager.database.models')
        self.logger.debug(f"Initializing database model with path: {db_path}")
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._conn_lock = threading.Lock()
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
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA busy_timeout = 5000")
                conn.execute("PRAGMA foreign_keys = ON")
                self._create_tables(conn)
                self._create_indexes(conn)
                conn.commit()
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
                hash TEXT UNIQUE
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
                FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE,
                UNIQUE(prompt_id, filename)
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

        # Add unique constraint to existing databases (migration)
        self._migrate_add_unique_constraint(conn)

        # Check if we need to migrate from old schema with workflow_name
        self._migrate_workflow_name_removal(conn)

        # Fix foreign key data type mismatch
        self._migrate_foreign_key_types(conn)

        # Migrate JSON tags to normalized junction tables
        self._migrate_json_tags_to_junction(conn)
    
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
            "CREATE INDEX IF NOT EXISTS idx_prompt_images ON generated_images(prompt_id)",
            "CREATE INDEX IF NOT EXISTS idx_image_path ON generated_images(image_path)",
            "CREATE INDEX IF NOT EXISTS idx_generation_time ON generated_images(generation_time)",
            "CREATE INDEX IF NOT EXISTS idx_prompt_tags_tag ON prompt_tags(tag_id)",
            "CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name)",
        ]
        
        for index_sql in indexes:
            conn.execute(index_sql)
    
    def get_connection(self) -> sqlite3.Connection:
        """
        Get the persistent database connection.

        Returns a thread-safe, reusable connection protected by a lock.
        The connection is created on first call and reused thereafter.
        Callers use it as a context manager; the lock is held for the
        duration of the ``with`` block.

        Returns:
            sqlite3.Connection: Configured database connection
        """
        with self._conn_lock:
            if self._conn is None:
                self._conn = sqlite3.connect(
                    self.db_path, check_same_thread=False
                )
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA journal_mode = WAL")
                self._conn.execute("PRAGMA foreign_keys = ON")
                self._conn.execute("PRAGMA busy_timeout = 5000")
        return self._conn
    
    def _migrate_workflow_name_removal(self, conn: sqlite3.Connection) -> None:
        """
        Remove workflow_name column if it exists in existing database.
        
        Args:
            conn: Active database connection
            
        This migration handles legacy schema updates by removing the deprecated
        workflow_name column while preserving all other data.
        """
        try:
            # Check if workflow_name column exists
            cursor = conn.execute("PRAGMA table_info(prompts)")
            columns = [column[1] for column in cursor.fetchall()]
            
            if 'workflow_name' in columns:
                self.logger.info("Migrating database: removing workflow_name column")
                
                # Create new table without workflow_name
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
                
                # Copy data from old table to new table
                conn.execute("""
                    INSERT INTO prompts_new (id, text, created_at, updated_at, category, tags, rating, notes, hash)
                    SELECT id, text, created_at, updated_at, category, tags, rating, notes, hash
                    FROM prompts
                """)
                
                # Drop old table and rename new one
                conn.execute("DROP TABLE prompts")
                conn.execute("ALTER TABLE prompts_new RENAME TO prompts")
                
                self.logger.info("Database migration completed")
                
        except Exception as e:
            self.logger.error(f"Migration error: {e}")
            # If migration fails, the table creation will handle it
    
    def _migrate_foreign_key_types(self, conn: sqlite3.Connection) -> None:
        """
        Fix foreign key data type mismatch in generated_images table.
        
        Args:
            conn: Active database connection
            
        Converts prompt_id from TEXT to INTEGER type to match the prompts table's
        primary key type, ensuring referential integrity.
        """
        try:
            # Check if generated_images table exists and has TEXT prompt_id
            cursor = conn.execute("PRAGMA table_info(generated_images)")
            columns = {column[1]: column[2] for column in cursor.fetchall()}
            
            if 'prompt_id' in columns and columns['prompt_id'] == 'TEXT':
                self.logger.info("Migrating foreign key types: prompt_id TEXT -> INTEGER")
                
                # Create new table with correct types
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
                
                # Copy data, converting prompt_id from TEXT to INTEGER
                conn.execute("""
                    INSERT INTO generated_images_new 
                    (id, prompt_id, image_path, filename, generation_time, file_size, 
                     width, height, format, workflow_data, prompt_metadata, parameters)
                    SELECT id, CAST(prompt_id AS INTEGER), image_path, filename, generation_time, 
                           file_size, width, height, format, workflow_data, prompt_metadata, parameters
                    FROM generated_images
                    WHERE prompt_id != '' AND prompt_id IS NOT NULL
                    AND CAST(prompt_id AS INTEGER) IN (SELECT id FROM prompts)
                """)
                
                # Drop old table and rename new one
                conn.execute("DROP TABLE generated_images")
                conn.execute("ALTER TABLE generated_images_new RENAME TO generated_images")
                
                self.logger.info("Foreign key migration completed")
                
        except Exception as e:
            self.logger.error(f"Foreign key migration error: {e}")
            # If migration fails, continue with existing schema
    
    def _migrate_add_unique_constraint(self, conn: sqlite3.Connection) -> None:
        """
        Add UNIQUE constraint on (prompt_id, filename) to prevent duplicate image entries.

        This migration:
        1. Checks if the constraint already exists
        2. Removes duplicate entries (keeping the most recent)
        3. Recreates the table with the UNIQUE constraint

        Args:
            conn: Active database connection
        """
        try:
            # Check if the unique constraint already exists by looking at table info
            cursor = conn.execute("PRAGMA index_list(generated_images)")
            indexes = cursor.fetchall()

            # Check if we have a unique index on prompt_id, filename
            has_unique_constraint = False
            for idx in indexes:
                if idx[2] == 1:  # unique flag
                    cursor = conn.execute(f"PRAGMA index_info({idx[1]})")
                    columns = [col[2] for col in cursor.fetchall()]
                    if 'prompt_id' in columns and 'filename' in columns:
                        has_unique_constraint = True
                        break

            if has_unique_constraint:
                return  # Already migrated

            self.logger.info("Migrating database: adding UNIQUE constraint on (prompt_id, filename)")

            # First, remove duplicates keeping only the most recent (highest id)
            conn.execute("""
                DELETE FROM generated_images
                WHERE id NOT IN (
                    SELECT MAX(id) FROM generated_images
                    GROUP BY prompt_id, filename
                )
            """)

            duplicates_removed = conn.total_changes
            if duplicates_removed > 0:
                self.logger.info(f"Removed {duplicates_removed} duplicate image entries")

            # Create new table with UNIQUE constraint
            conn.execute("""
                CREATE TABLE IF NOT EXISTS generated_images_new (
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
                    FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE,
                    UNIQUE(prompt_id, filename)
                )
            """)

            # Copy data
            conn.execute("""
                INSERT INTO generated_images_new
                (id, prompt_id, image_path, filename, generation_time, file_size,
                 width, height, format, workflow_data, prompt_metadata, parameters)
                SELECT id, prompt_id, image_path, filename, generation_time, file_size,
                       width, height, format, workflow_data, prompt_metadata, parameters
                FROM generated_images
            """)

            # Drop old table and rename new one
            conn.execute("DROP TABLE generated_images")
            conn.execute("ALTER TABLE generated_images_new RENAME TO generated_images")

            # Recreate indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_images ON generated_images(prompt_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_image_path ON generated_images(image_path)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_generation_time ON generated_images(generation_time)")

            self.logger.info("UNIQUE constraint migration completed successfully")

        except Exception as e:
            self.logger.error(f"UNIQUE constraint migration error: {e}")
            # Continue with existing schema if migration fails

    def _migrate_json_tags_to_junction(self, conn: sqlite3.Connection) -> None:
        """
        Populate tags and prompt_tags tables from legacy JSON tags column.

        Runs once: skips if the tags table already has data. Uses json_each()
        to extract tag names from the JSON arrays stored in prompts.tags.
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
                "WHERE prompts.tags IS NOT NULL AND prompts.tags != '' AND prompts.tags != '[]'"
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
                f"Tag migration complete: {tag_count} unique tags, {link_count} prompt-tag links"
            )

        except Exception as e:
            self.logger.error(f"Tag junction migration error: {e}")

    def migrate_database(self) -> None:
        """
        Apply any pending database migrations.

        This method serves as an entry point for future schema migrations.
        Add new migration logic here as the database evolves.
        """
        # Future migrations can be added here
        pass
    
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
                total_prompts = cursor.fetchone()['total_prompts']
                
                cursor = conn.execute(
                    "SELECT COUNT(DISTINCT category) as unique_categories FROM prompts WHERE category IS NOT NULL"
                )
                unique_categories = cursor.fetchone()['unique_categories']
                
                cursor = conn.execute(
                    "SELECT AVG(rating) as avg_rating FROM prompts WHERE rating IS NOT NULL"
                )
                avg_rating = cursor.fetchone()['avg_rating']
                
                # Get database file size
                db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
                
                return {
                    'total_prompts': total_prompts,
                    'unique_categories': unique_categories,
                    'average_rating': round(avg_rating, 2) if avg_rating else None,
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
            shutil.copy2(self.db_path, backup_path)
            return True
        except Exception as e:
            self.logger.error(f"Error creating database backup: {e}")
            return False