"""Repository for generated image links stored in generated_images table."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sqlite3

from src.core.base_repository import BaseRepository

try:
    from src.utils.file_metadata import compute_file_metadata, FileMetadata
except ImportError:  # pragma: no cover - CLI/tests fallback
    from utils.file_metadata import compute_file_metadata, FileMetadata  # type: ignore

try:  # pragma: no cover - ComfyUI runtime
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover - tests / CLI
    from loggers import get_logger  # type: ignore


LOGGER = get_logger("promptmanager.repositories.generated_images")


class GeneratedImageRepository(BaseRepository):
    """Light-weight repository exposing generated image references."""

    def __init__(self, db_path: str = None):
        super().__init__(db_path=db_path)
        self._ensure_compat_columns()

    def _get_table_name(self) -> str:
        return "generated_images"

    def _get_schema(self) -> str:
        """Return a schema compatible with migration output.

        The migration pipeline creates this table; we keep the definition here
        for completeness in case an empty database is initialised.
        """

        return """
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
            );
            
            CREATE TABLE IF NOT EXISTS prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                positive_prompt TEXT NOT NULL,
                negative_prompt TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                category TEXT,
                tags TEXT,
                rating INTEGER CHECK(rating >= 1 AND rating <= 5),
                notes TEXT,
                hash TEXT UNIQUE,
                model_hash TEXT,
                sampler_settings TEXT,
                generation_params TEXT
            );
        """

    def _get_columns(self) -> List[str]:
        """Return insertable columns, including optional legacy fields."""

        base_columns = [
            "prompt_id",
            "image_path",
            "filename",
            "generation_time",
            "file_size",
            "width",
            "height",
            "format",
            "workflow_data",
            "prompt_metadata",
            "parameters",
        ]

        table_columns = self._get_table_columns()
        if "media_type" in table_columns and "media_type" not in base_columns:
            base_columns.append("media_type")
        if "thumbnail_url" in table_columns and "thumbnail_url" not in base_columns:
            base_columns.append("thumbnail_url")

        return base_columns

    def _to_dict(self, row) -> Dict[str, Any]:
        data = dict(row)
        metadata = data.get("metadata")
        if isinstance(metadata, str) and metadata:
            try:
                data["metadata"] = json.loads(metadata)
            except json.JSONDecodeError:
                LOGGER.debug("Ignoring malformed metadata for generated image id=%s", data.get("id"))
                data["metadata"] = None
        file_path = data.get("file_path") or data.get("image_path")
        if file_path:
            data["file_path"] = file_path
            data["image_path"] = file_path
        return data

    def _from_dict(self, data: Dict[str, Any]) -> Tuple:
        # Handle metadata - could come as 'metadata' or 'prompt_metadata'
        metadata = data.get("prompt_metadata") or data.get("metadata")
        if isinstance(metadata, (dict, list)):
            metadata = json.dumps(metadata)

        image_path = data.get("image_path") or data.get("file_path")
        filename = data.get("filename") or data.get("file_name")
        if not filename and image_path:
            filename = Path(image_path).name

        column_values: List[Any] = []
        for column in self._get_columns():
            if column == "prompt_id":
                column_values.append(data.get("prompt_id"))
            elif column == "image_path":
                column_values.append(image_path)
            elif column == "filename":
                column_values.append(filename)
            elif column == "generation_time":
                column_values.append(data.get("generation_time"))
            elif column == "file_size":
                column_values.append(data.get("file_size"))
            elif column == "width":
                column_values.append(data.get("width"))
            elif column == "height":
                column_values.append(data.get("height"))
            elif column == "format":
                column_values.append(data.get("format"))
            elif column == "workflow_data":
                column_values.append(data.get("workflow_data"))
            elif column == "prompt_metadata":
                column_values.append(metadata)
            elif column == "parameters":
                column_values.append(data.get("parameters"))
            elif column == "media_type":
                column_values.append(data.get("media_type"))
            elif column == "thumbnail_url":
                column_values.append(data.get("thumbnail_url"))
            else:
                column_values.append(data.get(column))

        return tuple(column_values)

    # ------------------------------------------------------------------
    # Metadata helpers
    # ------------------------------------------------------------------

    def update_file_metadata(
        self,
        image_id: int,
        path: str,
        metadata: Optional[FileMetadata] = None,
    ) -> Optional[FileMetadata]:
        """Populate file metadata columns for a specific image.

        Args:
            image_id: Database identifier for the generated image record.
            path: File system path to the image.
            metadata: Pre-computed metadata to avoid re-reading the file.

        Returns:
            ``FileMetadata`` describing the file or ``None`` if the path is invalid.
        """

        if not path:
            return None

        metadata = metadata or compute_file_metadata(path)
        columns = self._get_table_columns()

        updates: Dict[str, Any] = {}
        if metadata.size is not None and "file_size" in columns:
            updates["file_size"] = metadata.size
        if metadata.width is not None and "width" in columns:
            updates["width"] = metadata.width
        if metadata.height is not None and "height" in columns:
            updates["height"] = metadata.height
        if metadata.format and "format" in columns:
            updates.setdefault("format", metadata.format)

        if not updates:
            return metadata

        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values()) + [image_id]

        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE generated_images SET {set_clause} WHERE id = ?",
                values,
            )

        return metadata

    def populate_missing_file_metadata(self, batch_size: int = 500) -> Dict[str, int]:
        """Populate missing metadata (size/dimensions) for all images."""

        stats = {
            "processed": 0,
            "updated": 0,
            "missing_path": 0,
        }

        columns = self._get_table_columns()
        if not {"file_size", "width", "height"}.intersection(columns):
            return stats

        offset = 0
        while True:
            batch = self.list(limit=batch_size, offset=offset, order_by="id ASC")
            if not batch:
                break

            for record in batch:
                stats["processed"] += 1
                path = record.get("image_path") or record.get("file_path")
                if not path:
                    stats["missing_path"] += 1
                    continue

                needs_size = "file_size" in columns and (record.get("file_size") in (None, 0))
                needs_width = "width" in columns and (record.get("width") in (None, 0))
                needs_height = "height" in columns and (record.get("height") in (None, 0))

                if not any([needs_size, needs_width, needs_height]):
                    continue

                metadata = self.update_file_metadata(record["id"], path)
                if metadata and any([metadata.size, metadata.width, metadata.height]):
                    stats["updated"] += 1

            offset += len(batch)

        return stats

    # Convenience helpers -------------------------------------------------

    def list_for_prompt(
        self,
        prompt_id: int,
        *,
        limit: Optional[int] = None,
        order_by: str = "id DESC",
    ) -> List[Dict[str, Any]]:
        """Return generated images linked to a prompt."""

        kwargs: Dict[str, Any] = {"prompt_id": prompt_id, "order_by": order_by}
        if limit is not None:
            kwargs["limit"] = limit
        try:
            return self.list(**kwargs)
        except sqlite3.OperationalError:
            LOGGER.debug("generated_images table missing while listing prompt %s", prompt_id)
            return []

    def resolve_path(self, record: Dict[str, Any]) -> Optional[Path]:
        """Return a resolved Path for the stored file if available."""

        file_path = record.get("file_path")
        if not file_path:
            return None
        return Path(file_path).expanduser()

    def count(self, **filters: Any) -> int:  # type: ignore[override]
        try:
            return super().count(**filters)
        except sqlite3.OperationalError:
            LOGGER.debug("generated_images table missing while counting")
            return 0

    def _has_column(self, column: str) -> bool:
        try:
            return column in self._get_table_columns()
        except sqlite3.OperationalError:
            return False

    def find_by_prompt_and_path(self, prompt_id: int, image_path: str) -> Optional[Dict[str, Any]]:
        """Check if an image is already linked to a prompt."""
        try:
            with self._get_connection() as conn:
                params: Tuple[Any, ...]
                if self._has_column('file_path'):
                    query = """
                        SELECT * FROM generated_images
                        WHERE prompt_id = ? AND (image_path = ? OR file_path = ?)
                        LIMIT 1
                    """
                    params = (prompt_id, image_path, image_path)
                else:
                    query = """
                        SELECT * FROM generated_images
                        WHERE prompt_id = ? AND image_path = ?
                        LIMIT 1
                    """
                    params = (prompt_id, image_path)
                cursor = conn.execute(query, params)
                row = cursor.fetchone()
                if row:
                    return self._to_dict(row)
                return None
        except sqlite3.OperationalError as e:
            LOGGER.debug("Error checking for existing link: %s", e)
            return None

    def find_by_path(self, image_path: str) -> Optional[Dict[str, Any]]:
        """Find an image by file path only."""
        try:
            with self._get_connection() as conn:
                if self._has_column('file_path'):
                    query = """
                        SELECT * FROM generated_images
                        WHERE image_path = ? OR file_path = ?
                        LIMIT 1
                    """
                    params = (image_path, image_path)
                else:
                    query = """
                        SELECT * FROM generated_images
                        WHERE image_path = ?
                        LIMIT 1
                    """
                    params = (image_path,)
                cursor = conn.execute(query, params)
                row = cursor.fetchone()
                if row:
                    return self._to_dict(row)
                return None
        except sqlite3.OperationalError as e:
            LOGGER.debug("Error finding image by path: %s", e)
            return None

    def remove_duplicates(self) -> int:
        """Remove duplicate image links, keeping the oldest."""
        try:
            with self._get_connection() as conn:
                # First, let's count the duplicates
                count_query = """
                    SELECT COUNT(*) FROM generated_images
                    WHERE id NOT IN (
                        SELECT MIN(id)
                        FROM generated_images
                        GROUP BY prompt_id, image_path
                    )
                """
                count_cursor = conn.execute(count_query)
                duplicate_count = count_cursor.fetchone()[0]
                LOGGER.info(f"Found {duplicate_count} duplicate records to remove")

                # Find and remove duplicates, keeping the one with lowest ID
                query = """
                    DELETE FROM generated_images
                    WHERE id NOT IN (
                        SELECT MIN(id)
                        FROM generated_images
                        GROUP BY prompt_id, image_path
                    )
                """
                cursor = conn.execute(query)
                removed = cursor.rowcount
                conn.commit()
                LOGGER.info(f"Removed {removed} duplicate image records")
                return removed
        except sqlite3.OperationalError as e:
            LOGGER.error("Error removing duplicates: %s", e)
            return 0

    def find_orphaned(self) -> List[Dict[str, Any]]:
        """Find images linked to non-existent prompts."""
        try:
            with self._get_connection() as conn:
                query = """
                    SELECT gi.* FROM generated_images gi
                    LEFT JOIN prompts p ON gi.prompt_id = p.id
                    WHERE p.id IS NULL
                """
                cursor = conn.execute(query)
                return [self._to_dict(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError as e:
            LOGGER.error("Error finding orphaned images: %s", e)
            return []

    def list(self,
             limit: int = 100,
             offset: int = 0,
             order_by: str = "generation_time DESC",
             **filters) -> List[Dict[str, Any]]:
        """List generated images with correct column names.

        Override base list to use generation_time instead of created_at.
        """
        # Call parent with corrected order_by
        return super().list(limit=limit, offset=offset, order_by=order_by, **filters)

    def validate_paths(self, batch_size: int = 1000) -> Dict[str, List[Dict[str, Any]]]:
        """Check if image files exist on disk.

        Args:
            batch_size: Number of images to process at once (default 1000)
        """
        missing = []
        valid = []

        try:
            # Get total count
            total_count = self.count()
            LOGGER.info(f"Validating paths for {total_count} images...")

            # Process in batches for memory efficiency
            offset = 0
            while offset < total_count:
                batch = self.list(
                    limit=batch_size,
                    offset=offset,
                    order_by="generation_time DESC"
                )

                for image in batch:
                    path = image.get("image_path") or image.get("file_path")
                    if path:
                        if Path(path).exists():
                            valid.append(image)
                        else:
                            missing.append(image)

                offset += batch_size

                # Log progress every 5000 images
                if offset % 5000 == 0:
                    LOGGER.info(f"Validated {min(offset, total_count)}/{total_count} images...")

            LOGGER.info(f"Validation complete: {len(valid)} valid, {len(missing)} missing")
            return {"missing": missing, "valid": valid}
        except Exception as e:
            LOGGER.error("Error validating paths: %s", e)
            return {"missing": [], "valid": []}

    def _ensure_compat_columns(self) -> None:
        """Ensure legacy installations have required columns."""
        try:
            columns = self._get_table_columns()
        except sqlite3.OperationalError:
            return

        # Add image_path column if missing
        if "image_path" not in columns:
            try:
                with self._get_connection() as conn:
                    conn.execute("ALTER TABLE generated_images ADD COLUMN image_path TEXT")
                    LOGGER.info("Added image_path column to generated_images table")
            except sqlite3.OperationalError as exc:
                LOGGER.debug("Could not add image_path column: %s", exc)

        # Add media_type column for tracking video/audio/image types
        if "media_type" not in columns:
            try:
                with self._get_connection() as conn:
                    conn.execute("ALTER TABLE generated_images ADD COLUMN media_type TEXT DEFAULT 'image'")
                    LOGGER.info("Added media_type column to generated_images table")
            except sqlite3.OperationalError as exc:
                LOGGER.debug("Could not add media_type column: %s", exc)
