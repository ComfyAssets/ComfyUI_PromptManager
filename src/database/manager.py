"""Database manager service for PromptManager.

This replaces the scattered database operations from the disaster __init__.py
with a proper service layer following single responsibility principle.
"""

import sqlite3
from .connection_helper import DatabaseConnection
import json
import hashlib
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from contextlib import contextmanager


class DatabaseManager:
    """Manages all database operations for PromptManager."""
    
    def __init__(self, config):
        """Initialize database manager with configuration."""
        self.config = config
        self.db_path = config.db_path
        self._init_database()
    
    @contextmanager
    def get_connection(self):
        """Context manager for database connections."""
        conn = DatabaseConnection.get_connection(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_database(self):
        """Initialize database with schema if needed."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Create prompts table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prompts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    positive TEXT NOT NULL,
                    negative TEXT DEFAULT '',
                    metadata TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    hash TEXT UNIQUE,
                    collection_id INTEGER,
                    FOREIGN KEY (collection_id) REFERENCES collections(id)
                )
            """)
            
            # Create images table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    hash TEXT UNIQUE
                )
            """)
            
            # Create prompt_images junction table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prompt_images (
                    prompt_id INTEGER,
                    image_id INTEGER,
                    PRIMARY KEY (prompt_id, image_id),
                    FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE,
                    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
                )
            """)
            
            # Create collections table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create indices for performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prompts_hash ON prompts(hash)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prompts_created ON prompts(created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_images_hash ON images(hash)")
    
    # ========== PROMPT OPERATIONS ==========
    
    def get_prompts(self, limit=100, offset=0, search='') -> List[Dict]:
        """Get prompts with pagination and optional search."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if search:
                query = """
                    SELECT * FROM prompts 
                    WHERE positive LIKE ? OR negative LIKE ?
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                """
                params = (f'%{search}%', f'%{search}%', limit, offset)
            else:
                query = """
                    SELECT * FROM prompts 
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                """
                params = (limit, offset)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            return [self._row_to_dict(row) for row in rows]
    
    def get_prompt(self, prompt_id: int) -> Optional[Dict]:
        """Get a single prompt by ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,))
            row = cursor.fetchone()
            
            if row:
                prompt = self._row_to_dict(row)
                # Get associated images
                cursor.execute("""
                    SELECT i.* FROM images i
                    JOIN prompt_images pi ON i.id = pi.image_id
                    WHERE pi.prompt_id = ?
                """, (prompt_id,))
                prompt['images'] = [self._row_to_dict(img) for img in cursor.fetchall()]
                return prompt
            
            return None
    
    def create_prompt(self, positive: str, negative='', metadata=None, images=None) -> int:
        """Create a new prompt."""
        # Calculate hash for duplicate detection
        prompt_hash = self._calculate_prompt_hash(positive, negative)
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check for duplicates
            cursor.execute("SELECT id FROM prompts WHERE hash = ?", (prompt_hash,))
            existing = cursor.fetchone()
            if existing:
                return existing['id']
            
            # Insert new prompt
            cursor.execute("""
                INSERT INTO prompts (positive, negative, metadata, hash)
                VALUES (?, ?, ?, ?)
            """, (positive, negative, json.dumps(metadata or {}), prompt_hash))
            
            prompt_id = cursor.lastrowid
            
            # Link images if provided
            if images:
                for image_data in images:
                    image_id = self._ensure_image(cursor, image_data)
                    cursor.execute("""
                        INSERT OR IGNORE INTO prompt_images (prompt_id, image_id)
                        VALUES (?, ?)
                    """, (prompt_id, image_id))
            
            return prompt_id
    
    def update_prompt(self, prompt_id: int, positive=None, negative=None, metadata=None) -> bool:
        """Update an existing prompt."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Build update query dynamically
            updates = []
            params = []
            
            if positive is not None:
                updates.append("positive = ?")
                params.append(positive)
            if negative is not None:
                updates.append("negative = ?")
                params.append(negative)
            if metadata is not None:
                updates.append("metadata = ?")
                params.append(json.dumps(metadata))
            
            if not updates:
                return True  # Nothing to update
            
            updates.append("updated_at = CURRENT_TIMESTAMP")
            
            query = f"UPDATE prompts SET {', '.join(updates)} WHERE id = ?"
            params.append(prompt_id)
            
            cursor.execute(query, params)
            return cursor.rowcount > 0
    
    def delete_prompt(self, prompt_id: int) -> bool:
        """Delete a prompt."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))
            return cursor.rowcount > 0
    
    def search_prompts(self, query: str, limit=50) -> List[Dict]:
        """Search prompts by text."""
        return self.get_prompts(limit=limit, search=query)
    
    def find_duplicate_prompts(self) -> List[Dict]:
        """Find prompts with duplicate content."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT hash, COUNT(*) as count, 
                       GROUP_CONCAT(id) as ids,
                       MAX(positive) as positive
                FROM prompts
                GROUP BY hash
                HAVING count > 1
            """)
            
            duplicates = []
            for row in cursor.fetchall():
                duplicates.append({
                    'hash': row['hash'],
                    'count': row['count'],
                    'ids': [int(id) for id in row['ids'].split(',')],
                    'positive': row['positive']
                })
            
            return duplicates
    
    # ========== SYSTEM OPERATIONS ==========
    
    def health_check(self) -> bool:
        """Check if database is accessible."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                return cursor.fetchone() is not None
        except Exception:
            return False
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get database statistics."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            stats = {}
            
            # Count prompts
            cursor.execute("SELECT COUNT(*) FROM prompts")
            stats['prompt_count'] = cursor.fetchone()[0]
            
            # Count images
            cursor.execute("SELECT COUNT(*) FROM images")
            stats['image_count'] = cursor.fetchone()[0]
            
            # Count collections
            cursor.execute("SELECT COUNT(*) FROM collections")
            stats['collection_count'] = cursor.fetchone()[0]
            
            # Database size
            if self.db_path.exists():
                stats['db_size_mb'] = self.db_path.stat().st_size / (1024 * 1024)
            
            # Recent activity
            cursor.execute("""
                SELECT DATE(created_at) as date, COUNT(*) as count
                FROM prompts
                WHERE created_at > datetime('now', '-7 days')
                GROUP BY DATE(created_at)
                ORDER BY date DESC
            """)
            stats['recent_activity'] = [
                {'date': row['date'], 'count': row['count']}
                for row in cursor.fetchall()
            ]
            
            return stats
    
    def get_database_size(self) -> int:
        """Get database file size in bytes."""
        if self.db_path.exists():
            return self.db_path.stat().st_size
        return 0
    
    def vacuum(self):
        """Optimize database by running VACUUM."""
        with self.get_connection() as conn:
            conn.execute("VACUUM")
    
    def create_backup(self, include_images=False) -> Path:
        """Create database backup."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.config.backups_dir / f"backup_{timestamp}.db"
        
        # Copy database file
        shutil.copy2(self.db_path, backup_path)
        
        # Optionally backup images directory
        if include_images and self.config.images_dir.exists():
            images_backup = self.config.backups_dir / f"images_{timestamp}"
            shutil.copytree(self.config.images_dir, images_backup)
        
        return backup_path
    
    def restore_backup(self, backup_path: str) -> bool:
        """Restore database from backup."""
        try:
            backup_file = Path(backup_path)
            if not backup_file.exists():
                return False
            
            # Create safety backup of current database
            if self.db_path.exists():
                safety_backup = self.db_path.with_suffix('.pre_restore.db')
                shutil.copy2(self.db_path, safety_backup)
            
            # Restore from backup
            shutil.copy2(backup_file, self.db_path)
            
            return True
        except Exception:
            return False
    
    # ========== HELPER METHODS ==========
    
    def _row_to_dict(self, row) -> Dict:
        """Convert database row to dictionary."""
        result = dict(row)
        # Parse JSON fields
        if 'metadata' in result and isinstance(result['metadata'], str):
            try:
                result['metadata'] = json.loads(result['metadata'])
            except json.JSONDecodeError:
                result['metadata'] = {}
        return result
    
    def _calculate_prompt_hash(self, positive: str, negative: str) -> str:
        """Calculate hash for duplicate detection."""
        content = f"{positive.strip()}|{negative.strip()}"
        return hashlib.sha256(content.encode()).hexdigest()
    
    def _ensure_image(self, cursor, image_data: Dict) -> int:
        """Ensure image exists in database, return its ID."""
        path = image_data.get('path', '')
        filename = image_data.get('filename', Path(path).name)
        
        # Calculate image hash
        image_hash = hashlib.sha256(path.encode()).hexdigest()
        
        # Check if exists
        cursor.execute("SELECT id FROM images WHERE hash = ?", (image_hash,))
        existing = cursor.fetchone()
        if existing:
            return existing['id']
        
        # Insert new image
        cursor.execute("""
            INSERT INTO images (path, filename, metadata, hash)
            VALUES (?, ?, ?, ?)
        """, (path, filename, json.dumps(image_data.get('metadata', {})), image_hash))
        
        return cursor.lastrowid