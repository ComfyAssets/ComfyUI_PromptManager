"""
Database schema and models for KikoTextEncode prompt storage.
"""

import sqlite3
import os
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
        self._ensure_database_exists()
    
    def _ensure_database_exists(self) -> None:
        """Create database and tables if they don't exist."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                self._create_tables(conn)
                self._create_indexes(conn)
                conn.commit()
        except Exception as e:
            self.logger.error(f"Error creating database: {e}")
            raise
    
    def _create_tables(self, conn: sqlite3.Connection) -> None:
        """Create the prompts table with all required columns."""
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
                FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE
            )
        """)
        
        # Check if we need to migrate from old schema with workflow_name
        self._migrate_workflow_name_removal(conn)
        
        # Fix foreign key data type mismatch
        self._migrate_foreign_key_types(conn)
    
    def _create_indexes(self, conn: sqlite3.Connection) -> None:
        """Create indexes for better query performance."""
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_prompts_text ON prompts(text)",
            "CREATE INDEX IF NOT EXISTS idx_prompts_category ON prompts(category)",
            "CREATE INDEX IF NOT EXISTS idx_prompts_created_at ON prompts(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_prompts_hash ON prompts(hash)",
            "CREATE INDEX IF NOT EXISTS idx_prompts_rating ON prompts(rating)",
            "CREATE INDEX IF NOT EXISTS idx_prompt_images ON generated_images(prompt_id)",
            "CREATE INDEX IF NOT EXISTS idx_image_path ON generated_images(image_path)",
            "CREATE INDEX IF NOT EXISTS idx_generation_time ON generated_images(generation_time)",
        ]
        
        for index_sql in indexes:
            conn.execute(index_sql)
    
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
    
    def _migrate_workflow_name_removal(self, conn: sqlite3.Connection) -> None:
        """Remove workflow_name column if it exists in existing database."""
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
        """Fix foreign key data type mismatch in generated_images table."""
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
    
    def migrate_database(self) -> None:
        """Apply any pending database migrations."""
        # Future migrations can be added here
        pass
    
    def vacuum_database(self) -> None:
        """Optimize database by running VACUUM command."""
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