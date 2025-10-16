"""Tag management service for handling tag operations and synchronization."""

import sqlite3
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TagService:
    """Service for managing tags and tag-related operations."""

    def __init__(self, db_path: str):
        """Initialize the tag service.

        Args:
            db_path: Path to the SQLite database
        """
        self.db_path = db_path

    def get_all_tags(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Get all tags ordered by usage count.

        Args:
            limit: Maximum number of tags to return

        Returns:
            List of tag dictionaries with id, name, and usage_count
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT id, name, usage_count
                    FROM tags
                    ORDER BY usage_count DESC, name ASC
                    LIMIT ?
                """, (limit,))
                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            logger.error(f"Error getting all tags: {e}")
            return []

    def search_tags(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search tags by partial name match.

        Args:
            query: Search query string
            limit: Maximum number of results

        Returns:
            List of matching tag dictionaries
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT id, name, usage_count
                    FROM tags
                    WHERE name LIKE ?
                    ORDER BY usage_count DESC, name ASC
                    LIMIT ?
                """, (f'%{query}%', limit))
                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            logger.error(f"Error searching tags for '{query}': {e}")
            return []

    def get_popular_tags(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get most popular tags by usage count.

        Args:
            limit: Number of top tags to return

        Returns:
            List of most used tags
        """
        return self.get_all_tags(limit)

    def get_tag_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a specific tag by name.

        Args:
            name: Tag name to retrieve

        Returns:
            Tag dictionary or None if not found
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT id, name, usage_count, created_at, updated_at
                    FROM tags
                    WHERE name = ?
                """, (name,))
                row = cursor.fetchone()
                return dict(row) if row else None

        except Exception as e:
            logger.error(f"Error getting tag '{name}': {e}")
            return None

    def create_tag(self, name: str) -> Optional[int]:
        """Create a new tag.

        Args:
            name: Tag name

        Returns:
            New tag ID or None if creation failed
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    INSERT INTO tags (name, usage_count, created_at, updated_at)
                    VALUES (?, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(name) DO NOTHING
                """, (name.strip(),))
                conn.commit()

                if cursor.rowcount > 0:
                    return cursor.lastrowid

                # Tag already exists, return existing ID
                cursor = conn.execute("SELECT id FROM tags WHERE name = ?", (name.strip(),))
                row = cursor.fetchone()
                return row[0] if row else None

        except Exception as e:
            logger.error(f"Error creating tag '{name}': {e}")
            return None

    def delete_tag(self, tag_id: int) -> bool:
        """Delete a tag by ID.

        Args:
            tag_id: Tag ID to delete

        Returns:
            True if deleted successfully, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
                conn.commit()
                return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error deleting tag {tag_id}: {e}")
            return False

    def sync_tags_from_prompts(self) -> int:
        """Extract and sync tags from prompts table.

        Parses all tags from prompts and updates the tags table
        with new tags and usage counts.

        Returns:
            Number of tags processed
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Get all tags from prompts
                cursor = conn.execute("""
                    SELECT tags FROM prompts
                    WHERE tags IS NOT NULL AND tags != ''
                """)

                tags_count = {}
                for row in cursor.fetchall():
                    tag_string = row[0]

                    # Try to parse as JSON array first
                    tags_list = []
                    try:
                        import json
                        parsed = json.loads(tag_string)
                        if isinstance(parsed, list):
                            tags_list = parsed
                        else:
                            tags_list = [str(parsed)]
                    except (json.JSONDecodeError, TypeError):
                        # Fall back to comma-separated parsing
                        tags_list = tag_string.split(',')

                    # Clean up and count each tag
                    for tag in tags_list:
                        # Remove any remaining JSON artifacts and whitespace
                        tag = str(tag).strip().strip('"').strip("'").strip('[]').strip()
                        if tag and tag not in ['', 'null', 'None']:
                            tags_count[tag] = tags_count.get(tag, 0) + 1

                # Insert or update tags
                for tag_name, count in tags_count.items():
                    try:
                        conn.execute("""
                            INSERT INTO tags (name, usage_count, created_at, updated_at)
                            VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                            ON CONFLICT(name) DO UPDATE SET
                                usage_count = excluded.usage_count,
                                updated_at = CURRENT_TIMESTAMP
                        """, (tag_name, count))
                    except Exception as e:
                        logger.warning(f"Failed to sync tag '{tag_name}': {e}")

                conn.commit()
                logger.info(f"Synced {len(tags_count)} tags from prompts")
                return len(tags_count)

        except Exception as e:
            logger.error(f"Error syncing tags from prompts: {e}")
            return 0

    def update_tag_usage_counts(self) -> int:
        """Recalculate usage counts for all tags from prompts table.

        Returns:
            Number of tags updated
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Reset all counts to 0
                conn.execute("UPDATE tags SET usage_count = 0")

                # Get all tags from prompts and count occurrences
                cursor = conn.execute("""
                    SELECT tags FROM prompts
                    WHERE tags IS NOT NULL AND tags != ''
                """)

                tags_count = {}
                for row in cursor.fetchall():
                    tag_string = row[0]

                    # Try to parse as JSON array first
                    tags_list = []
                    try:
                        import json
                        parsed = json.loads(tag_string)
                        if isinstance(parsed, list):
                            tags_list = parsed
                        else:
                            tags_list = [str(parsed)]
                    except (json.JSONDecodeError, TypeError):
                        # Fall back to comma-separated parsing
                        tags_list = tag_string.split(',')

                    # Clean up and count each tag
                    for tag in tags_list:
                        # Remove any remaining JSON artifacts and whitespace
                        tag = str(tag).strip().strip('"').strip("'").strip('[]').strip()
                        if tag and tag not in ['', 'null', 'None']:
                            tags_count[tag] = tags_count.get(tag, 0) + 1

                # Update counts
                updated_count = 0
                for tag_name, count in tags_count.items():
                    cursor = conn.execute("""
                        UPDATE tags
                        SET usage_count = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE name = ?
                    """, (count, tag_name))
                    if cursor.rowcount > 0:
                        updated_count += 1

                conn.commit()
                logger.info(f"Updated usage counts for {updated_count} tags")
                return updated_count

        except Exception as e:
            logger.error(f"Error updating tag usage counts: {e}")
            return 0

    def increment_tag_usage(self, tag_name: str) -> bool:
        """Increment usage count for a tag.

        Args:
            tag_name: Name of the tag to increment

        Returns:
            True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Create tag if it doesn't exist, increment if it does
                conn.execute("""
                    INSERT INTO tags (name, usage_count, created_at, updated_at)
                    VALUES (?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(name) DO UPDATE SET
                        usage_count = usage_count + 1,
                        updated_at = CURRENT_TIMESTAMP
                """, (tag_name.strip(),))
                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error incrementing tag usage for '{tag_name}': {e}")
            return False

    def decrement_tag_usage(self, tag_name: str) -> bool:
        """Decrement usage count for a tag.

        Args:
            tag_name: Name of the tag to decrement

        Returns:
            True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    UPDATE tags
                    SET usage_count = MAX(0, usage_count - 1),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE name = ?
                """, (tag_name.strip(),))
                conn.commit()
                return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error decrementing tag usage for '{tag_name}': {e}")
            return False

    def process_prompt_tags(self, tags_string: Optional[str], increment: bool = True) -> None:
        """Process tags from a prompt (increment or decrement usage).

        Args:
            tags_string: Comma-separated tags string
            increment: True to increment usage, False to decrement
        """
        if not tags_string:
            return

        for tag in tags_string.split(','):
            tag = tag.strip()
            if tag:
                if increment:
                    self.increment_tag_usage(tag)
                else:
                    self.decrement_tag_usage(tag)

    def get_tag_count(self) -> int:
        """Get total number of unique tags.

        Returns:
            Total tag count
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM tags")
                return cursor.fetchone()[0]

        except Exception as e:
            logger.error(f"Error getting tag count: {e}")
            return 0

    def cleanup_unused_tags(self, threshold: int = 0) -> int:
        """Delete tags with usage count at or below threshold.

        Args:
            threshold: Usage count threshold (default: 0 = unused tags only)

        Returns:
            Number of tags deleted
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM tags WHERE usage_count <= ?",
                    (threshold,)
                )
                conn.commit()
                deleted = cursor.rowcount
                logger.info(f"Cleaned up {deleted} tags with usage <= {threshold}")
                return deleted

        except Exception as e:
            logger.error(f"Error cleaning up unused tags: {e}")
            return 0
