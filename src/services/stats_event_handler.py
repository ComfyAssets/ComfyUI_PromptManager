"""
Event Handler for Real-time Stats Updates.
Handles incremental updates when data changes.
"""

import json
import sqlite3
from typing import Dict, Any, Optional
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class StatsEventHandler:
    """
    Handles real-time stats updates via events.
    Updates specific stats fields without recalculation.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def on_prompt_created(self, prompt: Dict[str, Any]):
        """
        Handle prompt creation event.
        Updates: total_prompts, category_counts, tag_counts
        """
        try:
            with self._connect() as conn:
                cursor = conn.cursor()

                # Increment total prompts (trigger handles this)
                # But we need to update category and tag counts

                category = prompt.get('category')
                tags = prompt.get('tags', [])

                if category:
                    self._update_category_count(cursor, category, 1)

                if tags:
                    if isinstance(tags, str):
                        tags = json.loads(tags) if tags else []
                    for tag in tags:
                        self._update_tag_count(cursor, tag, 1)

                # Update recent counts
                self._update_recent_counts(cursor)

                conn.commit()
                logger.debug(f"Stats updated for new prompt: {prompt.get('id')}")

        except Exception as e:
            logger.error(f"Failed to update stats for prompt creation: {e}")

    def on_prompt_deleted(self, prompt: Dict[str, Any]):
        """
        Handle prompt deletion event.
        Updates: total_prompts, category_counts, tag_counts
        """
        try:
            with self._connect() as conn:
                cursor = conn.cursor()

                category = prompt.get('category')
                tags = prompt.get('tags', [])

                if category:
                    self._update_category_count(cursor, category, -1)

                if tags:
                    if isinstance(tags, str):
                        tags = json.loads(tags) if tags else []
                    for tag in tags:
                        self._update_tag_count(cursor, tag, -1)

                # Update recent counts
                self._update_recent_counts(cursor)

                conn.commit()
                logger.debug(f"Stats updated for deleted prompt: {prompt.get('id')}")

        except Exception as e:
            logger.error(f"Failed to update stats for prompt deletion: {e}")

    def on_image_generated(self, image: Dict[str, Any]):
        """
        Handle image generation event.
        Updates: total_images, generation_time stats
        """
        try:
            with self._connect() as conn:
                cursor = conn.cursor()

                # Trigger handles total_images increment
                # Update generation time stats if available
                gen_time = image.get('generation_time', 0)
                if gen_time:
                    cursor.execute("""
                        UPDATE stats_snapshot
                        SET
                            total_generation_time = total_generation_time + ?,
                            avg_generation_time = total_generation_time / total_images,
                            last_updated = CURRENT_TIMESTAMP
                        WHERE id = 1
                    """, (gen_time,))

                # Update recent counts
                self._update_recent_counts(cursor)

                conn.commit()
                logger.debug(f"Stats updated for new image: {image.get('id')}")

        except Exception as e:
            logger.error(f"Failed to update stats for image generation: {e}")

    def on_image_deleted(self, image: Dict[str, Any]):
        """
        Handle image deletion event.
        Updates: total_images
        """
        try:
            with self._connect() as conn:
                cursor = conn.cursor()

                # Trigger handles total_images decrement
                # Update recent counts
                self._update_recent_counts(cursor)

                conn.commit()
                logger.debug(f"Stats updated for deleted image: {image.get('id')}")

        except Exception as e:
            logger.error(f"Failed to update stats for image deletion: {e}")

    def on_collection_changed(self, collection: Dict[str, Any], action: str):
        """
        Handle collection changes.
        Updates: total_collections
        """
        # Triggers handle the count updates automatically
        logger.debug(f"Collection {action}: {collection.get('id')}")

    def _update_category_count(self, cursor, category: str, delta: int):
        """Update category count by delta (+1 or -1)."""
        # Get current counts
        row = cursor.execute(
            "SELECT category_counts FROM stats_snapshot WHERE id = 1"
        ).fetchone()

        counts = json.loads(row[0] or '{}')

        # Update count
        if category in counts:
            counts[category] = max(0, counts[category] + delta)
            if counts[category] == 0:
                del counts[category]
        elif delta > 0:
            counts[category] = delta

        # Save back
        cursor.execute("""
            UPDATE stats_snapshot
            SET category_counts = ?,
                last_updated = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (json.dumps(counts),))

    def _update_tag_count(self, cursor, tag: str, delta: int):
        """Update tag count by delta (+1 or -1)."""
        # Get current counts
        row = cursor.execute(
            "SELECT tag_counts FROM stats_snapshot WHERE id = 1"
        ).fetchone()

        counts = json.loads(row[0] or '{}')

        # Update count
        if tag in counts:
            counts[tag] = max(0, counts[tag] + delta)
            if counts[tag] == 0:
                del counts[tag]
        elif delta > 0:
            counts[tag] = delta

        # Keep only top 100 tags
        if len(counts) > 100:
            # Sort and keep top 100
            sorted_tags = sorted(counts.items(), key=lambda x: x[1], reverse=True)
            counts = dict(sorted_tags[:100])

        # Save back
        cursor.execute("""
            UPDATE stats_snapshot
            SET tag_counts = ?,
                last_updated = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (json.dumps(counts),))

    def _update_recent_counts(self, cursor):
        """Update recent activity counts (24h, 7d, 30d)."""
        # This is lightweight enough to run on each event
        cursor.execute("""
            UPDATE stats_snapshot
            SET
                prompts_last_24h = (
                    SELECT COUNT(*) FROM prompts
                    WHERE datetime(created_at) > datetime('now', '-1 day')
                ),
                images_last_24h = (
                    SELECT COUNT(*) FROM generated_images
                    WHERE datetime(created_at) > datetime('now', '-1 day')
                ),
                last_updated = CURRENT_TIMESTAMP
            WHERE id = 1
        """)

    def _connect(self) -> sqlite3.Connection:
        """Create database connection."""
        return sqlite3.connect(self.db_path)


# Global instance for event handling
_event_handler = None


def get_stats_event_handler(db_path: str) -> StatsEventHandler:
    """Get singleton event handler instance."""
    global _event_handler
    if _event_handler is None:
        _event_handler = StatsEventHandler(db_path)
    return _event_handler


def register_event_hooks(app, db_path: str):
    """
    Register event hooks with the application.
    Call this during app initialization.
    """
    handler = get_stats_event_handler(db_path)

    # Register hooks for different events
    # These would be called from your existing code when events occur

    def on_prompt_save(prompt):
        handler.on_prompt_created(prompt)

    def on_prompt_delete(prompt):
        handler.on_prompt_deleted(prompt)

    def on_image_save(image):
        handler.on_image_generated(image)

    def on_image_delete(image):
        handler.on_image_deleted(image)

    # Return hooks for registration
    return {
        'prompt_created': on_prompt_save,
        'prompt_deleted': on_prompt_delete,
        'image_created': on_image_save,
        'image_deleted': on_image_delete
    }