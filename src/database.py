"""Database initialization and management.

This module handles database setup, migrations, and connection management
for the PromptManager application.
"""

import sqlite3
import json
import os
from typing import Optional, Any, Dict, List
from contextlib import contextmanager
from pathlib import Path

from .config import config

try:  # pragma: no cover - import path differs between runtime contexts
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.database")


class Database:
    """Database manager for PromptManager."""
    
    def __init__(self, db_path: Optional[str] = None):
        """Initialize database manager.
        
        Args:
            db_path: Database file path
        """
        if db_path:
            self.db_path = db_path
        else:
            # Prefer unified ComfyUI user directory path for consistency with UI and migration
            try:
                from utils.core.file_system import get_file_system
                self.db_path = str(get_file_system().get_database_path("prompts.db"))
            except Exception:
                # Fallback to config path
                self.db_path = config.database.path
        self._ensure_database_exists()
    
    def _ensure_database_exists(self):
        """Ensure database file exists."""
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        if not path.exists():
            logger.info(f"Creating new database: {self.db_path}")
            self.initialize()
    
    @contextmanager
    def get_connection(self):
        """Get database connection context manager.
        
        Yields:
            Database connection
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def initialize(self):
        """Initialize database schema."""
        logger.info("Initializing database schema")

        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Create prompts table with all required fields
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prompts (
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
            """)
            conn.commit()  # Commit the prompts table before creating images

            # Create images table with all required fields
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    file_size INTEGER DEFAULT 0,
                    format TEXT DEFAULT 'PNG',
                    checkpoint TEXT DEFAULT '',
                    prompt_id INTEGER,
                    prompt_text TEXT DEFAULT '',
                    negative_prompt TEXT DEFAULT '',
                    sampler TEXT DEFAULT '',
                    steps INTEGER DEFAULT 0,
                    cfg_scale REAL DEFAULT 0.0,
                    seed INTEGER DEFAULT -1,
                    model_hash TEXT DEFAULT '',
                    image_hash TEXT UNIQUE,
                    thumbnail_path TEXT,
                    metadata TEXT DEFAULT '{}',
                    workflow TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()  # Commit the images table before creating indexes

            # We'll defer prompt_images creation until after prompts and images tables exist
            # cursor.execute("""
            #     CREATE TABLE IF NOT EXISTS prompt_images (
            #         id INTEGER PRIMARY KEY AUTOINCREMENT,
            #         prompt_id INTEGER NOT NULL,
            #         image_id INTEGER NOT NULL,
            #         created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            #         FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE,
            #         FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
            #         UNIQUE(prompt_id, image_id)
            #     )
            # """)
            
            # Create categories table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    color TEXT DEFAULT '#888888',
                    icon TEXT,
                    parent_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (parent_id) REFERENCES categories(id) ON DELETE CASCADE
                )
            """)
            
            # Create tags table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    usage_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create workflows table for ComfyUI integration
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS workflows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    workflow_json TEXT NOT NULL,
                    description TEXT,
                    checkpoints TEXT DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create tracking table for ComfyUI execution
            # Defer this until after prompts and images tables exist
            # cursor.execute("""
            #     CREATE TABLE IF NOT EXISTS tracking (
            #         id INTEGER PRIMARY KEY AUTOINCREMENT,
            #         node_id TEXT NOT NULL,
            #         node_type TEXT NOT NULL,
            #         prompt_id INTEGER,
            #         image_id INTEGER,
            #         workflow_id INTEGER,
            #         execution_time REAL,
            #         status TEXT DEFAULT 'pending',
            #         error_message TEXT,
            #         metadata TEXT DEFAULT '{}',
            #         created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            #         FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE SET NULL,
            #         FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE SET NULL,
            #         FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE SET NULL
            #     )
            # """)
            
            # Create indexes for performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prompts_hash ON prompts(hash)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prompts_category ON prompts(category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prompts_rating ON prompts(rating)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prompts_created ON prompts(created_at)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_images_hash ON images(image_hash)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_images_checkpoint ON images(checkpoint)")
            # cursor.execute("CREATE INDEX IF NOT EXISTS idx_images_prompt ON images(prompt_id)")  # Skip for now
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_images_created ON images(created_at)")

            # cursor.execute("CREATE INDEX IF NOT EXISTS idx_prompt_images_prompt ON prompt_images(prompt_id)")
            # cursor.execute("CREATE INDEX IF NOT EXISTS idx_prompt_images_image ON prompt_images(image_id)")

            # cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracking_node ON tracking(node_id)")
            # cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracking_status ON tracking(status)")

            self._ensure_column(cursor, 'prompts', 'execution_count', 'INTEGER DEFAULT 0')
            self._ensure_column(cursor, 'prompts', 'last_used', 'TEXT')
            self._ensure_column(cursor, 'prompts', 'updated_at', 'TEXT DEFAULT CURRENT_TIMESTAMP')

            # Triggers will be created after tables exist
            # cursor.execute("""
            #     CREATE TRIGGER IF NOT EXISTS update_prompts_timestamp
            #     AFTER UPDATE ON prompts
            #     FOR EACH ROW
            #     BEGIN
            #         UPDATE prompts SET updated_at = CURRENT_TIMESTAMP
            #         WHERE id = NEW.id;
            #     END
            # """)

            # cursor.execute("""
            #     CREATE TRIGGER IF NOT EXISTS update_images_timestamp
            #     AFTER UPDATE ON images
            #     FOR EACH ROW
            #     BEGIN
            #         UPDATE images SET updated_at = CURRENT_TIMESTAMP
            #         WHERE id = NEW.id;
            #     END
            # """)
            
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS update_workflows_timestamp
                AFTER UPDATE ON workflows
                FOR EACH ROW
                BEGIN
                    UPDATE workflows SET updated_at = CURRENT_TIMESTAMP
                    WHERE id = NEW.id;
                END
            """)
            
            # Insert default categories
            default_categories = [
                ("character", "Character descriptions", "#FF6B6B"),
                ("environment", "Environment and scenes", "#4ECDC4"),
                ("style", "Art styles and techniques", "#45B7D1"),
                ("object", "Objects and items", "#96CEB4"),
                ("action", "Actions and poses", "#FFEAA7"),
                ("quality", "Quality modifiers", "#DDA0DD"),
                ("uncategorized", "Uncategorized prompts", "#888888")
            ]
            
            for name, desc, color in default_categories:
                cursor.execute("""
                    INSERT OR IGNORE INTO categories (name, description, color)
                    VALUES (?, ?, ?)
                """, (name, desc, color))
            
            conn.commit()
            logger.info("Database schema initialized successfully")

    @staticmethod
    def _ensure_column(cursor, table: str, column: str, definition: str) -> None:
        cursor.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cursor.fetchall()}
        if column not in existing:
            logger.info(f"Adding missing column '{column}' to table '{table}'")
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            if table == 'prompts' and column == 'last_used':
                cursor.execute("UPDATE prompts SET last_used = created_at WHERE last_used IS NULL")
            if table == 'prompts' and column == 'updated_at':
                cursor.execute("UPDATE prompts SET updated_at = created_at WHERE updated_at IS NULL")
    
    def migrate(self, version: Optional[int] = None):
        """Run database migrations.
        
        Args:
            version: Target version to migrate to
        """
        logger.info(f"Running database migrations to version {version}")
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get current version
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("SELECT MAX(version) FROM schema_version")
            result = cursor.fetchone()
            current_version = result[0] if result[0] else 0
            
            # Migration scripts
            migrations = {
                1: self._migration_v1,
                2: self._migration_v2,
                # Add more migrations as needed
            }
            
            # Run migrations
            target_version = version or max(migrations.keys())
            
            for v in range(current_version + 1, target_version + 1):
                if v in migrations:
                    logger.info(f"Applying migration v{v}")
                    migrations[v](cursor)
                    cursor.execute(
                        "INSERT INTO schema_version (version) VALUES (?)",
                        (v,)
                    )
            
            conn.commit()
            logger.info(f"Database migrated to version {target_version}")
    
    def _migration_v1(self, cursor):
        """Migration version 1: Add workflow metadata."""
        cursor.execute("""
            ALTER TABLE workflows 
            ADD COLUMN thumbnail TEXT
        """)
        
        cursor.execute("""
            ALTER TABLE workflows 
            ADD COLUMN usage_count INTEGER DEFAULT 0
        """)
    
    def _migration_v2(self, cursor):
        """Migration version 2: Add user preferences."""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    
    def backup(self, backup_path: Optional[str] = None):
        """Create database backup.
        
        Args:
            backup_path: Backup file path
        """
        if not backup_path:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{self.db_path}.backup_{timestamp}"
        
        logger.info(f"Creating database backup: {backup_path}")
        
        with self.get_connection() as source:
            backup = sqlite3.connect(backup_path)
            source.backup(backup)
            backup.close()
        
        logger.info("Database backup completed")
        return backup_path
    
    def restore(self, backup_path: str):
        """Restore database from backup.
        
        Args:
            backup_path: Backup file path
        """
        logger.info(f"Restoring database from: {backup_path}")
        
        if not Path(backup_path).exists():
            raise FileNotFoundError(f"Backup file not found: {backup_path}")
        
        # Create a temporary backup of current database
        temp_backup = self.backup()
        
        try:
            # Restore from backup
            backup = sqlite3.connect(backup_path)
            with self.get_connection() as target:
                backup.backup(target)
            backup.close()
            
            logger.info("Database restored successfully")
            
            # Remove temporary backup
            Path(temp_backup).unlink()
            
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            logger.info(f"Restoring from temporary backup: {temp_backup}")
            
            # Restore from temporary backup
            backup = sqlite3.connect(temp_backup)
            with self.get_connection() as target:
                backup.backup(target)
            backup.close()
            
            raise e
    
    def vacuum(self):
        """Vacuum database to reclaim space."""
        logger.info("Vacuuming database")
        
        with self.get_connection() as conn:
            conn.execute("VACUUM")
        
        logger.info("Database vacuum completed")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics.
        
        Returns:
            Database statistics
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            stats = {}
            
            # Table counts (ignore tables that may not exist)
            tables = ["prompts", "images", "workflows", "categories", "tags"]
            for table in tables:
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    stats[f"{table}_count"] = cursor.fetchone()[0]
                except Exception:
                    stats[f"{table}_count"] = 0
            
            # Database size
            cursor.execute("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()")
            stats["size_bytes"] = cursor.fetchone()[0]
            
            # Schema version
            try:
                cursor.execute("SELECT MAX(version) FROM schema_version")
                result = cursor.fetchone()
                stats["schema_version"] = result[0] if result and result[0] else 0
            except sqlite3.OperationalError:
                stats["schema_version"] = 0

            return stats

    def save_prompt(self, text: str, category: str = "default", tags: List[str] = None,
                    notes: str = "", prompt_hash: str = None) -> Optional[int]:
        """Save a prompt to the database.

        Args:
            text: The prompt text
            category: Category for the prompt
            tags: List of tags
            notes: Additional notes
            prompt_hash: Hash for duplicate detection

        Returns:
            The ID of the saved prompt, or None if failed
        """
        try:
            # Generate hash if not provided (required by schema)
            if not prompt_hash:
                from utils import generate_prompt_hash
                prompt_hash = generate_prompt_hash(text)

            with self.get_connection() as conn:
                cursor = conn.cursor()

                # Check for duplicates - return existing ID to allow image linking
                cursor.execute("SELECT id FROM prompts WHERE hash = ?", (prompt_hash,))
                existing = cursor.fetchone()
                if existing:
                    existing_id = existing[0]
                    logger.debug(f"Prompt with hash {prompt_hash[:16]}... already exists with ID {existing_id}")
                    return existing_id  # Return existing ID so images can be linked

                # Insert the prompt - use the actual schema columns
                cursor.execute("""
                    INSERT INTO prompts (prompt, negative_prompt, category, tags, notes, hash, created_at)
                    VALUES (?, '', ?, ?, ?, ?, datetime('now'))
                """, (text, category, json.dumps(tags or []), notes, prompt_hash))

                prompt_id = cursor.lastrowid
                conn.commit()

                logger.info(f"Saved prompt with ID {prompt_id}")
                return prompt_id

        except Exception as e:
            logger.error(f"Failed to save prompt: {e}")
            return None

    def get_prompt_by_hash(self, prompt_hash: str) -> Optional[Dict[str, Any]]:
        """Get a prompt by its hash.

        Args:
            prompt_hash: The hash to search for

        Returns:
            The prompt data or None if not found
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.row_factory = sqlite3.Row

                cursor.execute("""
                    SELECT id, prompt as text, category, tags, notes, hash as prompt_hash, created_at
                    FROM prompts
                    WHERE hash = ?
                """, (prompt_hash,))

                row = cursor.fetchone()
                if row:
                    return dict(row)

                return None

        except Exception as e:
            logger.error(f"Failed to get prompt by hash: {e}")
            return None

    def link_image_to_prompt(self, prompt_id: int, image_path: str) -> bool:
        """Link an image to a prompt.

        Args:
            prompt_id: The prompt ID
            image_path: Path to the image file

        Returns:
            True if successful, False otherwise
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # Create prompt_images table if it doesn't exist
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS prompt_images (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        prompt_id INTEGER NOT NULL,
                        image_path TEXT NOT NULL,
                        linked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (prompt_id) REFERENCES prompts(id),
                        UNIQUE(prompt_id, image_path)
                    )
                """)

                # Insert the link (ignore if already exists)
                cursor.execute("""
                    INSERT OR IGNORE INTO prompt_images (prompt_id, image_path)
                    VALUES (?, ?)
                """, (prompt_id, image_path))

                conn.commit()

                if cursor.rowcount > 0:
                    logger.debug(f"Linked image {os.path.basename(image_path)} to prompt {prompt_id}")

                return True

        except Exception as e:
            logger.error(f"Failed to link image to prompt: {e}")
            return False


# Global database instance
# db = Database()  # Don't create instance at module level - causes import issues
