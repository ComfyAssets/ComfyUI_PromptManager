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
        self.logger.debug(f"Initializing database operations with path: {db_path}")
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
            self.logger.debug(f"Successfully saved prompt with ID: {prompt_id}")
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
    
    def get_recent_prompts(self, limit: int = 10, offset: int = 0) -> Dict[str, Any]:
        """
        Get the most recent prompts with pagination support.
        
        Args:
            limit: Maximum number of prompts to return
            offset: Number of prompts to skip (for pagination)
            
        Returns:
            Dictionary containing prompt data and pagination info
        """
        with self.model.get_connection() as conn:
            # Get total count
            cursor = conn.execute("SELECT COUNT(*) as total FROM prompts")
            total_count = cursor.fetchone()["total"]
            
            # Get paginated results
            cursor = conn.execute(
                "SELECT * FROM prompts ORDER BY created_at DESC LIMIT ? OFFSET ?", 
                (limit, offset)
            )
            rows = cursor.fetchall()
            prompts = [self._row_to_dict(row) for row in rows]
            
            return {
                'prompts': prompts,
                'total': total_count,
                'limit': limit,
                'offset': offset,
                'has_more': (offset + limit) < total_count,
                'page': (offset // limit) + 1,
                'total_pages': (total_count + limit - 1) // limit  # Ceiling division
            }
    
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
                "SELECT DISTINCT TRIM(category) as category FROM prompts WHERE category IS NOT NULL AND TRIM(category) != '' ORDER BY category"
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
    
    def find_duplicates(self) -> List[Dict[str, Any]]:
        """
        Find duplicate prompts based on text content without removing them.
        
        Returns:
            List of duplicate groups, each containing:
            - text: The duplicate text content
            - prompts: List of prompt records with same text
        """
        self.logger.info("Scanning for duplicate prompts")
        try:
            with self.model.get_connection() as conn:
                # Find duplicates by text content (case-insensitive)
                # Note: Removed ORDER BY from GROUP_CONCAT for SQLite compatibility
                # We'll sort the IDs manually after fetching
                cursor = conn.execute("""
                    SELECT LOWER(TRIM(text)) as normalized_text, COUNT(*) as count, 
                           GROUP_CONCAT(id) as ids,
                           GROUP_CONCAT(created_at) as created_dates
                    FROM prompts 
                    GROUP BY LOWER(TRIM(text))
                    HAVING COUNT(*) > 1
                """)
                
                duplicate_groups = cursor.fetchall()
                self.logger.debug(f"Found {len(duplicate_groups)} groups of duplicate prompts")
                
                result = []
                
                for group in duplicate_groups:
                    ids = group['ids'].split(',')
                    created_dates = group['created_dates'].split(',')
                    
                    # Sort IDs by created_at date
                    id_date_pairs = list(zip(ids, created_dates))
                    id_date_pairs.sort(key=lambda x: x[1])  # Sort by date
                    ids = [pair[0] for pair in id_date_pairs]
                    
                    # Get full details for all prompts in this duplicate group
                    prompts = []
                    for prompt_id in ids:
                        cursor = conn.execute("""
                            SELECT id, text, category, rating, tags, created_at, updated_at
                            FROM prompts 
                            WHERE id = ?
                        """, (int(prompt_id),))
                        
                        prompt_data = cursor.fetchone()
                        if prompt_data:
                            prompt_dict = dict(prompt_data)
                            # Parse tags if they exist
                            if prompt_dict['tags']:
                                try:
                                    import json
                                    prompt_dict['tags'] = json.loads(prompt_dict['tags'])
                                except:
                                    prompt_dict['tags'] = []
                            else:
                                prompt_dict['tags'] = []
                            prompts.append(prompt_dict)
                    
                    if prompts:
                        result.append({
                            'text': prompts[0]['text'],  # Use the actual text (not normalized)
                            'prompts': prompts
                        })
                
                self.logger.info(f"Found {len(result)} groups with duplicates")
                return result
                
        except Exception as e:
            self.logger.error(f"Error finding duplicates: {e}")
            return []
    
    def cleanup_duplicates(self) -> int:
        """
        Remove duplicate prompts based on text content, preserving all image links.
        Merges metadata and transfers images to the retained prompt.
        
        Returns:
            int: Number of duplicates removed
        """
        self.logger.info("Starting duplicate cleanup process with image preservation")
        try:
            with self.model.get_connection() as conn:
                # Find duplicates by text content (case-insensitive)
                # Note: Removed ORDER BY from GROUP_CONCAT for SQLite compatibility
                # We'll sort the IDs manually after fetching
                cursor = conn.execute("""
                    SELECT LOWER(TRIM(text)) as normalized_text, COUNT(*) as count, 
                           GROUP_CONCAT(id) as ids,
                           GROUP_CONCAT(created_at) as created_dates
                    FROM prompts 
                    GROUP BY LOWER(TRIM(text))
                    HAVING COUNT(*) > 1
                """)
                
                duplicates = cursor.fetchall()
                self.logger.debug(f"Found {len(duplicates)} groups of duplicate prompts")
                total_removed = 0
                total_images_transferred = 0
                
                for duplicate in duplicates:
                    ids = duplicate['ids'].split(',')
                    created_dates = duplicate['created_dates'].split(',')
                    
                    # Sort IDs by created_at date to keep the oldest
                    id_date_pairs = list(zip(ids, created_dates))
                    id_date_pairs.sort(key=lambda x: x[1])  # Sort by date
                    sorted_ids = [int(pair[0]) for pair in id_date_pairs]
                    
                    # Keep the oldest one (first), merge and delete the rest
                    primary_id = sorted_ids[0]  # Keep the oldest
                    duplicate_ids = sorted_ids[1:]
                    
                    self.logger.debug(f"Merging duplicates: keeping {primary_id}, removing {duplicate_ids}")
                    
                    # Get primary prompt details
                    cursor = conn.execute("SELECT * FROM prompts WHERE id = ?", (primary_id,))
                    primary_prompt = cursor.fetchone()
                    if not primary_prompt:
                        continue
                    
                    # Collect and merge metadata from all duplicates
                    merged_metadata = self._merge_duplicate_metadata(conn, primary_id, duplicate_ids)
                    
                    # Transfer all images from duplicates to primary prompt
                    images_transferred = self._transfer_images_to_primary(conn, primary_id, duplicate_ids)
                    total_images_transferred += images_transferred
                    
                    # Update primary prompt with merged metadata
                    if merged_metadata:
                        self._update_primary_with_merged_metadata(conn, primary_id, merged_metadata)
                    
                    # Delete duplicate prompts (images already transferred)
                    for duplicate_id in duplicate_ids:
                        conn.execute("DELETE FROM prompts WHERE id = ?", (duplicate_id,))
                        total_removed += 1
                        self.logger.debug(f"Removed duplicate prompt {duplicate_id}")
                
                conn.commit()
                
                if total_removed > 0:
                    self.logger.info(f"Removed {total_removed} duplicate prompts, transferred {total_images_transferred} images")
                else:
                    self.logger.info("No duplicate prompts found")
                
                return total_removed
                
        except Exception as e:
            self.logger.error(f"Error cleaning up duplicates: {e}", exc_info=True)
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
        # Validate that prompt_id is a valid integer and exists in prompts table
        try:
            # Convert prompt_id to integer if it's a string number
            if isinstance(prompt_id, str) and prompt_id.isdigit():
                prompt_id_int = int(prompt_id)
            elif isinstance(prompt_id, int):
                prompt_id_int = prompt_id
            else:
                # Handle temporary IDs like "temp_123456" - skip linking
                if isinstance(prompt_id, str) and prompt_id.startswith('temp_'):
                    self.logger.debug(f"Skipping image linking for temporary prompt ID: {prompt_id}")
                    return 0
                else:
                    self.logger.warning(f"Invalid prompt_id format: {prompt_id}, skipping image linking")
                    return 0
            
            # Verify the prompt exists in the database
            with self.model.get_connection() as conn:
                cursor = conn.execute("SELECT id FROM prompts WHERE id = ?", (prompt_id_int,))
                if not cursor.fetchone():
                    self.logger.warning(f"Prompt ID {prompt_id_int} not found in database, skipping image linking")
                    return 0
                
                # Proceed with linking
                filename = os.path.basename(image_path)
                file_info = metadata.get('file_info', {}) if metadata else {}
                
                cursor = conn.execute(
                    """
                    INSERT INTO generated_images 
                    (prompt_id, image_path, filename, file_size, width, height, format, 
                     workflow_data, prompt_metadata, parameters)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prompt_id_int,
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
                
        except Exception as e:
            self.logger.error(f"Error linking image to prompt {prompt_id}: {e}")
            return 0

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

    def _clean_nan_values(self, obj: Any) -> Any:
        """
        Recursively clean NaN values from nested data structures.
        
        Args:
            obj: The object to clean (dict, list, or scalar)
            
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

    def _merge_duplicate_metadata(self, conn: sqlite3.Connection, primary_id: int, duplicate_ids: List[int]) -> Dict[str, Any]:
        """
        Merge metadata from duplicate prompts, prioritizing non-empty values.
        
        Args:
            conn: Database connection
            primary_id: ID of the prompt to keep
            duplicate_ids: List of duplicate prompt IDs
            
        Returns:
            Dict containing merged metadata
        """
        try:
            # Get primary prompt metadata
            cursor = conn.execute("SELECT category, tags, rating, notes FROM prompts WHERE id = ?", (primary_id,))
            primary_data = cursor.fetchone()
            if not primary_data:
                return {}
            
            merged = {
                'category': primary_data['category'],
                'tags': json.loads(primary_data['tags']) if primary_data['tags'] else [],
                'rating': primary_data['rating'],
                'notes': primary_data['notes'] or ''
            }
            
            # Merge data from duplicates
            for dup_id in duplicate_ids:
                cursor = conn.execute("SELECT category, tags, rating, notes FROM prompts WHERE id = ?", (dup_id,))
                dup_data = cursor.fetchone()
                if not dup_data:
                    continue
                
                # Merge category (keep first non-empty)
                if not merged['category'] and dup_data['category']:
                    merged['category'] = dup_data['category']
                
                # Merge tags (combine unique tags)
                if dup_data['tags']:
                    try:
                        dup_tags = json.loads(dup_data['tags'])
                        if isinstance(dup_tags, list):
                            for tag in dup_tags:
                                if tag not in merged['tags']:
                                    merged['tags'].append(tag)
                    except (json.JSONDecodeError, TypeError):
                        pass
                
                # Keep highest rating
                if dup_data['rating'] and (not merged['rating'] or dup_data['rating'] > merged['rating']):
                    merged['rating'] = dup_data['rating']
                
                # Combine notes
                if dup_data['notes'] and dup_data['notes'].strip():
                    if merged['notes']:
                        merged['notes'] += f" | {dup_data['notes']}"
                    else:
                        merged['notes'] = dup_data['notes']
            
            return merged
            
        except Exception as e:
            self.logger.error(f"Error merging metadata: {e}", exc_info=True)
            return {}
    
    def _transfer_images_to_primary(self, conn: sqlite3.Connection, primary_id: int, duplicate_ids: List[int]) -> int:
        """
        Transfer all images from duplicate prompts to the primary prompt.
        
        Args:
            conn: Database connection
            primary_id: ID of the prompt to keep
            duplicate_ids: List of duplicate prompt IDs
            
        Returns:
            Number of images transferred
        """
        transferred_count = 0
        try:
            for dup_id in duplicate_ids:
                # Update all images to point to primary prompt
                cursor = conn.execute(
                    "UPDATE generated_images SET prompt_id = ? WHERE prompt_id = ?",
                    (primary_id, dup_id)
                )
                transferred_count += cursor.rowcount
                
                if cursor.rowcount > 0:
                    self.logger.debug(f"Transferred {cursor.rowcount} images from prompt {dup_id} to {primary_id}")
            
            return transferred_count
            
        except Exception as e:
            self.logger.error(f"Error transferring images: {e}", exc_info=True)
            return 0
    
    def _update_primary_with_merged_metadata(self, conn: sqlite3.Connection, primary_id: int, merged_metadata: Dict[str, Any]) -> None:
        """
        Update the primary prompt with merged metadata.
        
        Args:
            conn: Database connection
            primary_id: ID of the prompt to update
            merged_metadata: Merged metadata dictionary
        """
        try:
            conn.execute(
                """
                UPDATE prompts 
                SET category = ?, tags = ?, rating = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    merged_metadata.get('category'),
                    json.dumps(merged_metadata.get('tags', [])),
                    merged_metadata.get('rating'),
                    merged_metadata.get('notes'),
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    primary_id
                )
            )
            self.logger.debug(f"Updated primary prompt {primary_id} with merged metadata")
            
        except Exception as e:
            self.logger.error(f"Error updating primary prompt metadata: {e}", exc_info=True)
    
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