"""
Database operations for KikoTextEncode prompt storage and retrieval.
"""

import sqlite3
import json
import datetime
import os
from typing import Optional, List, Dict, Any, Union

from .models import PromptModel

# Import logging system
try:
    from ..utils.logging_config import get_logger
except ImportError:
    import sys
    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, current_dir)
    from utils.logging_config import get_logger


class PromptDatabase:
    """Database operations class for managing prompts."""
    
    def __init__(self, db_path: str = "prompts.db"):
        """
        Initialize the database operations.
        
        Args:
            db_path: Path to the SQLite database file
        """
        self.logger = get_logger('prompt_manager.database')
        self.logger.info(f"Initializing database operations with path: {db_path}")
        self.model = PromptModel(db_path)
        self.logger.debug("Database operations initialized successfully")
    
    def save_prompt(
        self,
        text: str,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        rating: Optional[int] = None,
        notes: Optional[str] = None,
        prompt_hash: Optional[str] = None
    ) -> int:
        """
        Save a new prompt to the database.
        
        Args:
            text: The prompt text
            category: Optional category
            tags: List of tags
            rating: Rating 1-5
            notes: Optional notes
            prompt_hash: SHA256 hash of the prompt
            
        Returns:
            int: The ID of the saved prompt
            
        Raises:
            ValueError: If required parameters are invalid
            sqlite3.Error: If database operation fails
        """
        if not text or not text.strip():
            raise ValueError("Prompt text cannot be empty")
        
        if rating is not None and (rating < 1 or rating > 5):
            raise ValueError("Rating must be between 1 and 5")
        
        tags_json = json.dumps(tags) if tags else None
        
        self.logger.debug(f"Saving prompt: text_length={len(text)}, category={category}, tags={tags}, rating={rating}")
        
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO prompts (
                    text, category, tags, rating, notes, hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    text.strip(),
                    category,
                    tags_json,
                    rating,
                    notes,
                    prompt_hash,
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    datetime.datetime.now(datetime.timezone.utc).isoformat()
                )
            )
            conn.commit()
            prompt_id = cursor.lastrowid
            self.logger.info(f"Successfully saved prompt with ID: {prompt_id}")
            return prompt_id
    
    def get_prompt_by_id(self, prompt_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a prompt by its ID.
        
        Args:
            prompt_id: The prompt ID
            
        Returns:
            Dict containing prompt data or None if not found
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM prompts WHERE id = ?", (prompt_id,)
            )
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None
    
    def get_prompt_by_hash(self, prompt_hash: str) -> Optional[Dict[str, Any]]:
        """
        Get a prompt by its hash.
        
        Args:
            prompt_hash: SHA256 hash of the prompt
            
        Returns:
            Dict containing prompt data or None if not found
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM prompts WHERE hash = ?", (prompt_hash,)
            )
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None
    
    def search_prompts(
        self,
        text: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        rating_min: Optional[int] = None,
        rating_max: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Search prompts with various filters.
        
        Args:
            text: Text to search for in prompt content
            category: Filter by category
            tags: Filter by tags (must contain all specified tags)
            rating_min: Minimum rating filter
            rating_max: Maximum rating filter
            date_from: Start date filter (ISO format)
            date_to: End date filter (ISO format)
            limit: Maximum number of results
            offset: Number of results to skip
            
        Returns:
            List of dictionaries containing prompt data
        """
        query_parts = ["SELECT * FROM prompts WHERE 1=1"]
        params = []
        
        if text:
            query_parts.append("AND text LIKE ?")
            params.append(f"%{text}%")
        
        if category:
            query_parts.append("AND category = ?")
            params.append(category)
        
        if tags:
            for tag in tags:
                query_parts.append("AND tags LIKE ?")
                params.append(f"%{tag}%")
        
        if rating_min is not None:
            query_parts.append("AND rating >= ?")
            params.append(rating_min)
        
        if rating_max is not None:
            query_parts.append("AND rating <= ?")
            params.append(rating_max)
        
        if date_from:
            query_parts.append("AND created_at >= ?")
            params.append(date_from)
        
        if date_to:
            query_parts.append("AND created_at <= ?")
            params.append(date_to)
        
        
        query_parts.append("ORDER BY created_at DESC LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        
        query = " ".join(query_parts)
        
        with self.model.get_connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]
    
    def get_recent_prompts(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get the most recent prompts.
        
        Args:
            limit: Maximum number of prompts to return
            
        Returns:
            List of dictionaries containing prompt data
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM prompts ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            rows = cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]
    
    def get_prompts_by_category(self, category: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get all prompts in a specific category.
        
        Args:
            category: The category name
            limit: Maximum number of prompts to return
            
        Returns:
            List of dictionaries containing prompt data
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM prompts WHERE category = ? ORDER BY created_at DESC LIMIT ?",
                (category, limit)
            )
            rows = cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]
    
    def get_top_rated_prompts(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get the highest rated prompts.
        
        Args:
            limit: Maximum number of prompts to return
            
        Returns:
            List of dictionaries containing prompt data
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM prompts 
                WHERE rating IS NOT NULL 
                ORDER BY rating DESC, created_at DESC 
                LIMIT ?
                """,
                (limit,)
            )
            rows = cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]
    
    def update_prompt_metadata(
        self,
        prompt_id: int,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        rating: Optional[int] = None,
        notes: Optional[str] = None
    ) -> bool:
        """
        Update metadata for an existing prompt.
        
        Args:
            prompt_id: The prompt ID
            category: New category
            tags: New tags list
            rating: New rating
            notes: New notes
            
        Returns:
            bool: True if update was successful, False otherwise
        """
        updates = []
        params = []
        
        if category is not None:
            updates.append("category = ?")
            params.append(category)
        
        if tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(tags))
        
        if rating is not None:
            if rating < 1 or rating > 5:
                raise ValueError("Rating must be between 1 and 5")
            updates.append("rating = ?")
            params.append(rating)
        
        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)
        
        
        if not updates:
            return False
        
        updates.append("updated_at = ?")
        params.append(datetime.datetime.now(datetime.timezone.utc).isoformat())
        params.append(prompt_id)
        
        query = f"UPDATE prompts SET {', '.join(updates)} WHERE id = ?"
        
        with self.model.get_connection() as conn:
            cursor = conn.execute(query, params)
            conn.commit()
            return cursor.rowcount > 0
    
    def delete_prompt(self, prompt_id: int) -> bool:
        """
        Delete a prompt by its ID.
        
        Args:
            prompt_id: The prompt ID to delete
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        with self.model.get_connection() as conn:
            # First delete related images to avoid foreign key constraint
            conn.execute("DELETE FROM generated_images WHERE prompt_id = ?", (prompt_id,))
            # Then delete the prompt
            cursor = conn.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))
            conn.commit()
            return cursor.rowcount > 0
    
    def get_all_categories(self) -> List[str]:
        """
        Get all unique categories from the database.
        
        Returns:
            List of category names
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "SELECT DISTINCT category FROM prompts WHERE category IS NOT NULL ORDER BY category"
            )
            return [row['category'] for row in cursor.fetchall()]
    
    def get_all_tags(self) -> List[str]:
        """
        Get all unique tags from the database.
        
        Returns:
            List of tag names
        """
        all_tags = set()
        
        with self.model.get_connection() as conn:
            cursor = conn.execute("SELECT tags FROM prompts WHERE tags IS NOT NULL")
            for row in cursor.fetchall():
                try:
                    tags = json.loads(row['tags'])
                    if isinstance(tags, list):
                        all_tags.update(tags)
                except (json.JSONDecodeError, TypeError):
                    continue
        
        return sorted(list(all_tags))
    
    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """
        Convert a database row to a dictionary with parsed JSON fields.
        
        Args:
            row: SQLite row object
            
        Returns:
            Dictionary representation of the row
        """
        data = dict(row)
        
        # Parse tags JSON
        if data.get('tags'):
            try:
                data['tags'] = json.loads(data['tags'])
            except (json.JSONDecodeError, TypeError):
                data['tags'] = []
        else:
            data['tags'] = []
        
        return data
    
    def export_prompts(self, file_path: str, format: str = "json") -> bool:
        """
        Export all prompts to a file.
        
        Args:
            file_path: Path to save the export file
            format: Export format ("json" or "csv")
            
        Returns:
            bool: True if export was successful, False otherwise
        """
        try:
            prompts = self.search_prompts(limit=10000)  # Get all prompts
            
            if format.lower() == "json":
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(prompts, f, indent=2, ensure_ascii=False)
            elif format.lower() == "csv":
                import csv
                if prompts:
                    with open(file_path, 'w', newline='', encoding='utf-8') as f:
                        writer = csv.DictWriter(f, fieldnames=prompts[0].keys())
                        writer.writeheader()
                        for prompt in prompts:
                            # Convert lists to strings for CSV
                            row = prompt.copy()
                            if isinstance(row.get('tags'), list):
                                row['tags'] = ', '.join(row['tags'])
                            writer.writerow(row)
            else:
                raise ValueError(f"Unsupported export format: {format}")
            
            return True
        except Exception as e:
            self.logger.error(f"Error exporting prompts: {e}")
            return False
    
    def cleanup_duplicates(self) -> int:
        """
        Remove duplicate prompts based on text content.
        
        Returns:
            int: Number of duplicates removed
        """
        self.logger.info("Starting duplicate cleanup process")
        try:
            with self.model.get_connection() as conn:
                # Find duplicates by text content (case-insensitive)
                cursor = conn.execute("""
                    SELECT LOWER(TRIM(text)) as normalized_text, COUNT(*) as count, 
                           GROUP_CONCAT(id) as ids
                    FROM prompts 
                    GROUP BY LOWER(TRIM(text))
                    HAVING COUNT(*) > 1
                """)
                
                duplicates = cursor.fetchall()
                self.logger.debug(f"Found {len(duplicates)} groups of duplicate prompts")
                total_removed = 0
                
                for duplicate in duplicates:
                    ids = duplicate['ids'].split(',')
                    # Keep the first one (oldest), delete the rest
                    ids_to_delete = ids[1:]  # Skip the first ID
                    
                    for id_to_delete in ids_to_delete:
                        # First delete related images to avoid foreign key constraint
                        conn.execute("DELETE FROM generated_images WHERE prompt_id = ?", (id_to_delete,))
                        # Then delete the prompt
                        conn.execute("DELETE FROM prompts WHERE id = ?", (int(id_to_delete),))
                        total_removed += 1
                
                conn.commit()
                
                if total_removed > 0:
                    self.logger.info(f"Removed {total_removed} duplicate prompts")
                else:
                    self.logger.info("No duplicate prompts found")
                
                return total_removed
                
        except Exception as e:
            self.logger.error(f"Error cleaning up duplicates: {e}")
            return 0

    # Gallery-related methods
    def link_image_to_prompt(
        self,
        prompt_id: str,
        image_path: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Link a generated image to a prompt.
        
        Args:
            prompt_id: ID of the prompt that generated this image
            image_path: Full path to the image file
            metadata: Optional metadata about the image
            
        Returns:
            int: The ID of the created image record
        """
        filename = os.path.basename(image_path)
        file_info = metadata.get('file_info', {}) if metadata else {}
        
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO generated_images 
                (prompt_id, image_path, filename, file_size, width, height, format, 
                 workflow_data, prompt_metadata, parameters)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prompt_id,
                    image_path,
                    filename,
                    file_info.get('size'),
                    file_info.get('dimensions', [None, None])[0] if file_info.get('dimensions') else None,
                    file_info.get('dimensions', [None, None])[1] if file_info.get('dimensions') else None,
                    file_info.get('format'),
                    json.dumps(metadata.get('workflow', {}) if metadata else {}),
                    json.dumps(metadata.get('prompt', {}) if metadata else {}),
                    json.dumps(metadata.get('parameters', {}) if metadata else {})
                )
            )
            conn.commit()
            return cursor.lastrowid

    def get_prompt_images(self, prompt_id: str) -> List[Dict[str, Any]]:
        """
        Get all images associated with a prompt.
        
        Args:
            prompt_id: The prompt ID
            
        Returns:
            List of image records
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM generated_images 
                WHERE prompt_id = ? 
                ORDER BY generation_time DESC
                """,
                (prompt_id,)
            )
            return [self._image_row_to_dict(row) for row in cursor.fetchall()]

    def get_recent_images(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get recently generated images across all prompts.
        
        Args:
            limit: Maximum number of images to return
            
        Returns:
            List of image records with prompt text
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT gi.*, p.text as prompt_text
                FROM generated_images gi
                LEFT JOIN prompts p ON gi.prompt_id = p.id
                ORDER BY gi.generation_time DESC
                LIMIT ?
                """,
                (limit,)
            )
            return [self._image_row_to_dict(row) for row in cursor.fetchall()]

    def search_images_by_prompt(self, search_term: str) -> List[Dict[str, Any]]:
        """
        Search images by prompt text.
        
        Args:
            search_term: Text to search for in prompt content
            
        Returns:
            List of image records with prompt text
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT gi.*, p.text as prompt_text
                FROM generated_images gi
                JOIN prompts p ON gi.prompt_id = p.id
                WHERE p.text LIKE ?
                ORDER BY gi.generation_time DESC
                """,
                (f"%{search_term}%",)
            )
            return [self._image_row_to_dict(row) for row in cursor.fetchall()]

    def get_image_by_id(self, image_id: int) -> Optional[Dict[str, Any]]:
        """
        Get an image record by its ID.
        
        Args:
            image_id: The image ID
            
        Returns:
            Image record or None if not found
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM generated_images WHERE id = ?",
                (image_id,)
            )
            row = cursor.fetchone()
            return self._image_row_to_dict(row) if row else None

    def delete_image(self, image_id: int) -> bool:
        """
        Delete an image record by its ID.
        
        Args:
            image_id: The image ID to delete
            
        Returns:
            bool: True if deletion was successful
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM generated_images WHERE id = ?",
                (image_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def cleanup_missing_images(self) -> int:
        """
        Remove image records where the actual file no longer exists.
        
        Returns:
            int: Number of orphaned records removed
        """
        removed_count = 0
        
        with self.model.get_connection() as conn:
            cursor = conn.execute("SELECT id, image_path FROM generated_images")
            images = cursor.fetchall()
            
            for image in images:
                if not os.path.exists(image['image_path']):
                    conn.execute("DELETE FROM generated_images WHERE id = ?", (image['id'],))
                    removed_count += 1
            
            conn.commit()
        
        return removed_count

    def _clean_nan_values(self, obj):
        """
        Recursively clean NaN values from nested data structures.
        
        Args:
            obj: The object to clean
            
        Returns:
            Cleaned object with NaN values replaced by None
        """
        if isinstance(obj, dict):
            return {key: self._clean_nan_values(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._clean_nan_values(item) for item in obj]
        elif isinstance(obj, float) and str(obj) == 'nan':
            return None
        else:
            return obj

    def _image_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """
        Convert an image database row to a dictionary with parsed JSON fields.
        
        Args:
            row: SQLite row object
            
        Returns:
            Dictionary representation of the row
        """
        data = dict(row)
        
        # Parse JSON fields
        for field in ['workflow_data', 'prompt_metadata', 'parameters']:
            if data.get(field):
                try:
                    parsed_data = json.loads(data[field])
                    data[field] = self._clean_nan_values(parsed_data)
                except (json.JSONDecodeError, TypeError):
                    data[field] = {}
            else:
                data[field] = {}
        
        return data