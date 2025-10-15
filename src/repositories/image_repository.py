"""Image repository implementation extending BaseRepository.

This module implements image-specific database operations,
inheriting all common functionality from BaseRepository.
"""

import json
from datetime import datetime
from pathlib import Path
import os
from typing import Any, Dict, List, Optional, Tuple

from ..core.base_repository import BaseRepository

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.repositories.images")



class ImageRepository(BaseRepository):
    """Repository for image management.
    
    Extends BaseRepository with image-specific functionality.
    All CRUD operations are inherited - only domain-specific logic added.
    """
    
    def _get_table_name(self) -> str:
        """Return the images table name."""
        return "images"
    
    def _get_schema(self) -> str:
        """Return the SQL schema for images table."""
        return """
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
            );
        """
    
    def _to_dict(self, row) -> Optional[Dict[str, Any]]:
        """Convert database row to dictionary.
        
        Args:
            row: Database row
            
        Returns:
            Dictionary with image data
        """
        if not row:
            return None
        
        data = dict(row)
        
        # Parse JSON fields
        if "metadata" in data and isinstance(data["metadata"], str):
            try:
                data["metadata"] = json.loads(data["metadata"])
            except json.JSONDecodeError:
                data["metadata"] = {}
        
        if "workflow" in data and isinstance(data["workflow"], str):
            try:
                data["workflow"] = json.loads(data["workflow"])
            except json.JSONDecodeError:
                data["workflow"] = {}
        
        return data

    def _get_columns(self) -> List[str]:
        """Get the list of columns for images table."""
        return [
            "file_path", "filename", "width", "height", "file_size",
            "format", "checkpoint", "prompt_id", "prompt_text", "negative_prompt",
            "sampler", "steps", "cfg_scale", "seed", "model_hash",
            "image_hash", "thumbnail_path", "metadata", "workflow"
        ]

    def _from_dict(self, data: Dict[str, Any]) -> Tuple:
        """Convert dictionary to database row values.
        
        Args:
            data: Image data dictionary
            
        Returns:
            Tuple of values for database
        """
        # Serialize JSON fields
        metadata = data.get("metadata", {})
        if isinstance(metadata, dict):
            metadata = json.dumps(metadata)
        
        workflow = data.get("workflow", {})
        if isinstance(workflow, dict):
            workflow = json.dumps(workflow)
        
        # Return values in the order they appear in the schema
        # Don't include auto-generated fields
        return (
            data.get("file_path"),
            data.get("filename"),
            data.get("width"),
            data.get("height"),
            data.get("file_size", 0),
            data.get("format", "PNG"),
            data.get("checkpoint", ""),
            data.get("prompt_id"),
            data.get("prompt_text", ""),
            data.get("negative_prompt", ""),
            data.get("sampler", ""),
            data.get("steps", 0),
            data.get("cfg_scale", 0.0),
            data.get("seed", -1),
            data.get("model_hash", ""),
            data.get("image_hash", ""),
            data.get("thumbnail_path"),
            metadata,
            workflow
        )
    
    # Image-specific methods
    
    def find_by_hash(self, image_hash: str) -> Optional[Dict[str, Any]]:
        """Find image by hash.
        
        Args:
            image_hash: Image hash
            
        Returns:
            Image data or None
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"SELECT * FROM {self._get_table_name()} WHERE image_hash = ?",
                (image_hash,)
            )
            row = cursor.fetchone()
            return self._to_dict(row)
    
    def get_by_prompt(self, prompt_id: int) -> List[Dict[str, Any]]:
        """Get images by prompt ID.
        
        Args:
            prompt_id: Prompt ID
            
        Returns:
            List of images
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"SELECT * FROM {self._get_table_name()} WHERE prompt_id = ? ORDER BY created_at DESC",
                (prompt_id,)
            )
            return [self._to_dict(row) for row in cursor.fetchall()]
    
    def get_by_checkpoint(self, checkpoint: str) -> List[Dict[str, Any]]:
        """Get images by checkpoint.
        
        Args:
            checkpoint: Checkpoint name
            
        Returns:
            List of images
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"SELECT * FROM {self._get_table_name()} WHERE checkpoint = ? ORDER BY created_at DESC",
                (checkpoint,)
            )
            return [self._to_dict(row) for row in cursor.fetchall()]
    
    def get_unique_checkpoints(self) -> List[str]:
        """Get list of unique checkpoints.
        
        Returns:
            List of checkpoint names
        """
        query = f"""
            SELECT DISTINCT checkpoint 
            FROM {self._get_table_name()} 
            WHERE checkpoint != ''
            ORDER BY checkpoint
        """
        
        with self._get_connection() as conn:
            cursor = conn.execute(query)
            return [row[0] for row in cursor.fetchall()]
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get image statistics.
        
        Returns:
            Statistics dictionary
        """
        query = f"""
            SELECT 
                COUNT(*) as total_images,
                COUNT(DISTINCT checkpoint) as unique_checkpoints,
                COUNT(DISTINCT prompt_id) as unique_prompts,
                AVG(width) as avg_width,
                AVG(height) as avg_height,
                SUM(file_size) as total_size,
                MIN(created_at) as first_image,
                MAX(created_at) as last_image
            FROM {self._get_table_name()}
        """
        
        with self._get_connection() as conn:
            cursor = conn.execute(query)
            row = cursor.fetchone()
            
            if row:
                return {
                    "total_images": row[0] or 0,
                    "unique_checkpoints": row[1] or 0,
                    "unique_prompts": row[2] or 0,
                    "avg_width": round(row[3] or 0),
                    "avg_height": round(row[4] or 0),
                    "total_size": row[5] or 0,
                    "first_image": row[6],
                    "last_image": row[7]
                }
            
            return {
                "total_images": 0,
                "unique_checkpoints": 0,
                "unique_prompts": 0,
                "avg_width": 0,
                "avg_height": 0,
                "total_size": 0,
                "first_image": None,
                "last_image": None
            }
    
    def cleanup_orphaned(self) -> int:
        """Clean up orphaned image records.
        
        Returns:
            Number of records cleaned
        """
        # Find images where file doesn't exist
        import os
        
        orphaned_ids = []
        
        with self._get_connection() as conn:
            cursor = conn.execute(f"SELECT id, file_path FROM {self._get_table_name()}")
            for row in cursor.fetchall():
                if not os.path.exists(row[1]):
                    orphaned_ids.append(row[0])
        
        if orphaned_ids:
            placeholders = ",".join(["?" for _ in orphaned_ids])
            query = f"DELETE FROM {self._get_table_name()} WHERE id IN ({placeholders})"
            
            with self._get_connection() as conn:
                cursor = conn.execute(query, orphaned_ids)
                return cursor.rowcount
        
        return 0
    
    def get_by_date_range(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Get images within date range.
        
        Args:
            start_date: Start date (ISO format)
            end_date: End date (ISO format)
            
        Returns:
            List of images
        """
        query = f"""
            SELECT * FROM {self._get_table_name()}
            WHERE created_at BETWEEN ? AND ?
            ORDER BY created_at DESC
        """
        
        with self._get_connection() as conn:
            cursor = conn.execute(query, (start_date, end_date))
            return [self._to_dict(row) for row in cursor.fetchall()]
    
    def update_thumbnail(self, image_id: int, thumbnail_path: str) -> bool:
        """Update thumbnail path for an image.
        
        Args:
            image_id: Image ID
            thumbnail_path: Path to thumbnail
            
        Returns:
            True if updated
        """
        query = f"""
            UPDATE {self._get_table_name()}
            SET thumbnail_path = ?, updated_at = ?
            WHERE id = ?
        """
        
        now = datetime.utcnow().isoformat()
        
        with self._get_connection() as conn:
            cursor = conn.execute(query, (thumbnail_path, now, image_id))
            return cursor.rowcount > 0
