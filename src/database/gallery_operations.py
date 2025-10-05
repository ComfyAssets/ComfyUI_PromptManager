"""
Gallery-specific database operations for PromptManager.
Extends the base PromptDatabase with gallery-focused queries.
"""

from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import sqlite3
import json
from pathlib import Path

from .operations import PromptDatabase

try:  # pragma: no cover - import path differs between runtime contexts
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger(__name__)


class GalleryDatabaseOperations:
    """Extended database operations for gallery functionality"""

    def __init__(self, db: PromptDatabase):
        """Initialize with existing database connection"""
        self.db = db
        self.conn = db.conn
        self.cursor = db.cursor

    def get_gallery_items(self,
                         offset: int = 0,
                         limit: int = 20,
                         order_by: str = 'date-desc',
                         tags: Optional[List[str]] = None,
                         search: Optional[str] = None,
                         date_from: Optional[str] = None,
                         date_to: Optional[str] = None,
                         has_images: Optional[bool] = None,
                         metadata_filter: Optional[Dict] = None) -> List[Dict]:
        """
        Get gallery items with filtering and pagination.

        Args:
            offset: Number of items to skip
            limit: Maximum number of items to return
            order_by: Sort order (date-desc, date-asc, prompt-asc, prompt-desc)
            tags: Filter by tags
            search: Search in prompts
            date_from: Start date filter
            date_to: End date filter
            has_images: Filter by whether items have images
            metadata_filter: Additional metadata filters

        Returns:
            List of gallery items with associated images
        """
        try:
            # Build base query
            query = """
                SELECT DISTINCT
                    p.id,
                    p.id as prompt_id,
                    p.text as prompt_text,
                    p.negative_prompt,
                    p.created_at,
                    p.metadata,
                    p.tags,
                    p.model_name,
                    p.workflow_data,
                    p.generation_settings,
                    COUNT(DISTINCT i.id) as image_count
                FROM prompts p
                LEFT JOIN generated_images i ON p.id = i.prompt_id
                WHERE 1=1
            """

            params = []

            # Add search filter
            if search:
                query += " AND (p.text LIKE ? OR p.negative_prompt LIKE ?)"
                search_pattern = f"%{search}%"
                params.extend([search_pattern, search_pattern])

            # Add date filters
            if date_from:
                query += " AND p.created_at >= ?"
                params.append(date_from)

            if date_to:
                query += " AND p.created_at <= ?"
                params.append(date_to)

            # Add has_images filter
            if has_images is not None:
                if has_images:
                    query += " AND i.id IS NOT NULL"
                else:
                    query += " AND i.id IS NULL"

            # Add metadata filter (e.g., model)
            if metadata_filter:
                if 'model' in metadata_filter and metadata_filter['model']:
                    query += " AND p.model_name = ?"
                    params.append(metadata_filter['model'])

            # Add GROUP BY clause
            query += " GROUP BY p.id"

            # Add tags filter (after GROUP BY)
            if tags and len(tags) > 0:
                tag_conditions = []
                for tag in tags:
                    tag_conditions.append("p.tags LIKE ?")
                    params.append(f"%{tag}%")
                query += f" HAVING {' AND '.join(tag_conditions)}"

            # Add ORDER BY clause
            order_map = {
                'date-desc': 'p.created_at DESC',
                'date-asc': 'p.created_at ASC',
                'prompt-asc': 'p.text ASC',
                'prompt-desc': 'p.text DESC'
            }
            order_clause = order_map.get(order_by, 'p.created_at DESC')
            query += f" ORDER BY {order_clause}"

            # Add pagination
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            # Execute query
            self.cursor.execute(query, params)
            rows = self.cursor.fetchall()

            # Format results
            items = []
            for row in rows:
                item = {
                    'id': row['id'],
                    'prompt_id': row['prompt_id'],
                    'prompt_text': row['prompt_text'],
                    'negative_prompt': row['negative_prompt'],
                    'created_at': row['created_at'],
                    'model_name': row['model_name'],
                    'tags': json.loads(row['tags']) if row['tags'] else [],
                    'metadata': json.loads(row['metadata']) if row['metadata'] else {},
                    'workflow_data': json.loads(row['workflow_data']) if row['workflow_data'] else {},
                    'generation_settings': json.loads(row['generation_settings']) if row['generation_settings'] else {},
                    'image_count': row['image_count'],
                    'images': []
                }

                # Fetch associated images
                if item['image_count'] > 0:
                    img_query = """
                        SELECT id, filename, width, height, metadata, created_at
                        FROM generated_images
                        WHERE prompt_id = ?
                        ORDER BY created_at DESC
                    """
                    self.cursor.execute(img_query, (item['prompt_id'],))
                    images = self.cursor.fetchall()

                    for img in images:
                        item['images'].append({
                            'id': img['id'],
                            'filename': img['filename'],
                            'width': img['width'],
                            'height': img['height'],
                            'metadata': json.loads(img['metadata']) if img['metadata'] else {},
                            'created_at': img['created_at']
                        })

                items.append(item)

            return items

        except Exception as e:
            logger.error(f"Failed to get gallery items: {e}")
            return []

    def get_gallery_count(self,
                         tags: Optional[List[str]] = None,
                         search: Optional[str] = None,
                         metadata_filter: Optional[Dict] = None) -> int:
        """
        Get total count of gallery items matching filters.

        Args:
            tags: Filter by tags
            search: Search in prompts
            metadata_filter: Additional metadata filters

        Returns:
            Total count of matching items
        """
        try:
            query = """
                SELECT COUNT(DISTINCT p.id) as total
                FROM prompts p
                LEFT JOIN generated_images i ON p.id = i.prompt_id
                WHERE 1=1
            """

            params = []

            # Add search filter
            if search:
                query += " AND (p.text LIKE ? OR p.negative_prompt LIKE ?)"
                search_pattern = f"%{search}%"
                params.extend([search_pattern, search_pattern])

            # Add metadata filter
            if metadata_filter:
                if 'model' in metadata_filter and metadata_filter['model']:
                    query += " AND p.model_name = ?"
                    params.append(metadata_filter['model'])

            # Add tags filter
            if tags and len(tags) > 0:
                for tag in tags:
                    query += " AND p.tags LIKE ?"
                    params.append(f"%{tag}%")

            self.cursor.execute(query, params)
            result = self.cursor.fetchone()

            return result['total'] if result else 0

        except Exception as e:
            logger.error(f"Failed to get gallery count: {e}")
            return 0

    def get_unique_models(self) -> List[str]:
        """
        Get list of unique model names from database.

        Returns:
            List of unique model names
        """
        try:
            query = """
                SELECT DISTINCT model_name
                FROM prompts
                WHERE model_name IS NOT NULL AND model_name != ''
                ORDER BY model_name
            """

            self.cursor.execute(query)
            rows = self.cursor.fetchall()

            return [row['model_name'] for row in rows]

        except Exception as e:
            logger.error(f"Failed to get unique models: {e}")
            return []

    def get_items_by_ids(self, item_ids: List[int]) -> List[Dict]:
        """
        Get specific items by their IDs.

        Args:
            item_ids: List of prompt IDs

        Returns:
            List of items with full data
        """
        if not item_ids:
            return []

        try:
            placeholders = ','.join('?' * len(item_ids))
            query = f"""
                SELECT
                    p.id,
                    p.text as prompt_text,
                    p.negative_prompt,
                    p.created_at,
                    p.metadata,
                    p.tags,
                    p.model_name,
                    p.workflow_data,
                    p.generation_settings
                FROM prompts p
                WHERE p.id IN ({placeholders})
            """

            self.cursor.execute(query, item_ids)
            rows = self.cursor.fetchall()

            items = []
            for row in rows:
                item = {
                    'id': row['id'],
                    'prompt_text': row['prompt_text'],
                    'negative_prompt': row['negative_prompt'],
                    'created_at': row['created_at'],
                    'model_name': row['model_name'],
                    'tags': json.loads(row['tags']) if row['tags'] else [],
                    'metadata': json.loads(row['metadata']) if row['metadata'] else {},
                    'workflow_data': json.loads(row['workflow_data']) if row['workflow_data'] else {},
                    'generation_settings': json.loads(row['generation_settings']) if row['generation_settings'] else {},
                    'images': []
                }

                # Fetch associated images
                img_query = """
                    SELECT id, filename, width, height, metadata, created_at
                    FROM generated_images
                    WHERE prompt_id = ?
                    ORDER BY created_at DESC
                """
                self.cursor.execute(img_query, (item['id'],))
                images = self.cursor.fetchall()

                for img in images:
                    item['images'].append({
                        'id': img['id'],
                        'filename': img['filename'],
                        'width': img['width'],
                        'height': img['height'],
                        'metadata': json.loads(img['metadata']) if img['metadata'] else {},
                        'created_at': img['created_at']
                    })

                items.append(item)

            return items

        except Exception as e:
            logger.error(f"Failed to get items by IDs: {e}")
            return []

    def get_all_items_with_images(self) -> List[Dict]:
        """
        Get all items that have associated images.

        Returns:
            List of items with images
        """
        try:
            query = """
                SELECT DISTINCT
                    p.id,
                    p.text as prompt_text,
                    p.created_at,
                    i.id as image_id,
                    i.filename,
                    i.width,
                    i.height,
                    i.generation_time,
                    i.created_at as image_created_at
                FROM prompts p
                INNER JOIN generated_images i ON p.id = i.prompt_id
                ORDER BY COALESCE(i.generation_time, i.created_at, p.created_at) DESC
            """

            self.cursor.execute(query)
            rows = self.cursor.fetchall()

            # Group by prompt
            items_dict = {}
            for row in rows:
                prompt_id = row['id']
                if prompt_id not in items_dict:
                    items_dict[prompt_id] = {
                        'id': prompt_id,
                        'prompt_text': row['prompt_text'],
                        'created_at': row['created_at'],
                        'images': []
                    }

                items_dict[prompt_id]['images'].append({
                    'id': row['image_id'],
                    'filename': row['filename'],
                    'width': row['width'],
                    'height': row['height']
                })

            return list(items_dict.values())

        except Exception as e:
            logger.error(f"Failed to get items with images: {e}")
            return []

    def increment_view_count(self, item_id: int) -> bool:
        """
        Increment the view count for an item.

        Args:
            item_id: ID of the item to increment

        Returns:
            True if successful
        """
        try:
            # First check if view_count column exists
            self.cursor.execute("PRAGMA table_info(prompts)")
            columns = [col[1] for col in self.cursor.fetchall()]

            if 'view_count' not in columns:
                # Add view_count column if it doesn't exist
                self.cursor.execute("ALTER TABLE prompts ADD COLUMN view_count INTEGER DEFAULT 0")
                self.conn.commit()

            # Increment view count
            query = """
                UPDATE prompts
                SET view_count = COALESCE(view_count, 0) + 1
                WHERE id = ?
            """
            self.cursor.execute(query, (item_id,))
            self.conn.commit()

            return True

        except Exception as e:
            logger.error(f"Failed to increment view count: {e}")
            return False


# Extend the main PromptDatabase class with gallery methods
def extend_prompt_database_with_gallery(db: PromptDatabase):
    """
    Extend an existing PromptDatabase instance with gallery operations.

    Args:
        db: PromptDatabase instance to extend

    Returns:
        Extended database instance
    """
    gallery_ops = GalleryDatabaseOperations(db)

    # Add methods to the database instance
    db.get_gallery_items = gallery_ops.get_gallery_items
    db.get_gallery_count = gallery_ops.get_gallery_count
    db.get_unique_models = gallery_ops.get_unique_models
    db.get_items_by_ids = gallery_ops.get_items_by_ids
    db.get_all_items_with_images = gallery_ops.get_all_items_with_images
    db.increment_view_count = gallery_ops.increment_view_count

    return db