"""Missing images tagger service."""

import sqlite3
import json
import logging
from pathlib import Path
from typing import List, Dict, Any
from ..database.connection_helper import get_db_connection

logger = logging.getLogger(__name__)


class MissingImagesTagger:
    """Service to find and tag images with missing files."""

    def __init__(self, db_path: str):
        """Initialize the service.

        Args:
            db_path: Path to the database
        """
        self.db_path = db_path

    def find_missing_images(self) -> List[Dict[str, Any]]:
        """Find all images with missing files.

        Returns:
            List of image records with missing files
        """
        missing_images = []

        try:
            with get_db_connection(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Get all images
                cursor.execute("""
                    SELECT id, file_path, filename, metadata
                    FROM generated_images
                    WHERE file_path IS NOT NULL OR filename IS NOT NULL
                """)

                for row in cursor.fetchall():
                    # Check if file exists
                    file_path = row['file_path'] or row['filename']
                    if file_path and not Path(file_path).exists():
                        missing_images.append(dict(row))

                logger.info(f"Found {len(missing_images)} images with missing files")

        except Exception as e:
            logger.error(f"Error finding missing images: {e}")

        return missing_images

    def tag_missing_images(self, tag: str = "missing") -> Dict[str, Any]:
        """Add a tag to all images with missing files.

        Args:
            tag: Tag to add (default: "missing")

        Returns:
            Dictionary with statistics about the tagging operation
        """
        stats = {
            'total_missing': 0,
            'tagged': 0,
            'already_tagged': 0,
            'errors': 0
        }

        try:
            missing_images = self.find_missing_images()
            stats['total_missing'] = len(missing_images)

            with get_db_connection(self.db_path) as conn:
                cursor = conn.cursor()

                for image in missing_images:
                    try:
                        # Parse existing metadata
                        metadata_str = image.get('metadata', '{}') or '{}'
                        try:
                            metadata = json.loads(metadata_str)
                        except (json.JSONDecodeError, TypeError):
                            metadata = {}

                        # Get or create tags list
                        tags = metadata.get('tags', [])
                        if isinstance(tags, str):
                            # Handle string tags
                            tags = [t.strip() for t in tags.split(',') if t.strip()]
                        elif not isinstance(tags, list):
                            tags = []

                        # Check if already tagged
                        if tag in tags:
                            stats['already_tagged'] += 1
                            continue

                        # Add the tag
                        tags.append(tag)
                        metadata['tags'] = tags

                        # Update the database
                        cursor.execute("""
                            UPDATE generated_images
                            SET metadata = ?
                            WHERE id = ?
                        """, (json.dumps(metadata), image['id']))

                        stats['tagged'] += 1

                    except Exception as e:
                        logger.error(f"Error tagging image {image['id']}: {e}")
                        stats['errors'] += 1

                conn.commit()
                logger.info(f"Tagged {stats['tagged']} missing images")

        except Exception as e:
            logger.error(f"Error in tag_missing_images: {e}")

        return stats

    def remove_missing_tag(self, tag: str = "missing") -> Dict[str, Any]:
        """Remove the missing tag from images that now exist.

        Args:
            tag: Tag to remove (default: "missing")

        Returns:
            Dictionary with statistics about the operation
        """
        stats = {
            'checked': 0,
            'tag_removed': 0,
            'errors': 0
        }

        try:
            with get_db_connection(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Find images with the missing tag
                cursor.execute("""
                    SELECT id, file_path, filename, metadata
                    FROM generated_images
                    WHERE metadata LIKE ?
                """, (f'%"{tag}"%',))

                for row in cursor.fetchall():
                    stats['checked'] += 1

                    try:
                        # Check if file now exists
                        file_path = row['file_path'] or row['filename']
                        if file_path and Path(file_path).exists():
                            # Parse metadata
                            metadata_str = row['metadata'] or '{}'
                            try:
                                metadata = json.loads(metadata_str)
                            except (json.JSONDecodeError, TypeError):
                                metadata = {}

                            # Get tags
                            tags = metadata.get('tags', [])
                            if isinstance(tags, str):
                                tags = [t.strip() for t in tags.split(',') if t.strip()]
                            elif not isinstance(tags, list):
                                tags = []

                            # Remove the tag if present
                            if tag in tags:
                                tags.remove(tag)
                                metadata['tags'] = tags

                                # Update database
                                cursor.execute("""
                                    UPDATE generated_images
                                    SET metadata = ?
                                    WHERE id = ?
                                """, (json.dumps(metadata), row['id']))

                                stats['tag_removed'] += 1

                    except Exception as e:
                        logger.error(f"Error processing image {row['id']}: {e}")
                        stats['errors'] += 1

                conn.commit()
                logger.info(f"Removed {stats['tag_removed']} missing tags from found images")

        except Exception as e:
            logger.error(f"Error in remove_missing_tag: {e}")

        return stats

    def get_missing_images_summary(self) -> Dict[str, Any]:
        """Get a summary of missing images.

        Returns:
            Dictionary with summary statistics
        """
        summary = {
            'total_images': 0,
            'missing_files': 0,
            'tagged_missing': 0,
            'missing_by_directory': {}
        }

        try:
            with get_db_connection(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Total images
                summary['total_images'] = cursor.execute(
                    "SELECT COUNT(*) FROM generated_images"
                ).fetchone()[0]

                # Count missing files
                missing_images = self.find_missing_images()
                summary['missing_files'] = len(missing_images)

                # Count tagged as missing
                cursor.execute("""
                    SELECT COUNT(*) FROM generated_images
                    WHERE metadata LIKE '%"missing"%'
                """)
                summary['tagged_missing'] = cursor.fetchone()[0]

                # Group by directory
                for image in missing_images:
                    file_path = image.get('file_path') or image.get('filename')
                    if file_path:
                        directory = str(Path(file_path).parent)
                        summary['missing_by_directory'][directory] = \
                            summary['missing_by_directory'].get(directory, 0) + 1

        except Exception as e:
            logger.error(f"Error getting missing images summary: {e}")

        return summary