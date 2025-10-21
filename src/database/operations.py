"""
Database operations for PromptManager with v2 file system integration.

Uses the official ComfyUI directory structure for database storage.
"""

import sqlite3
import json
import datetime
import os
from pathlib import Path
from typing import Optional, List, Dict, Any, Union

# Import ComfyUIFileSystem for proper path resolution
try:
    from ..utils.core.file_system import ComfyUIFileSystem  # type: ignore
except ImportError:  # pragma: no cover - fallback for direct execution
    try:
        from utils.core.file_system import ComfyUIFileSystem  # type: ignore
    except ImportError:
        ComfyUIFileSystem = None

# Import utilities with proper fallback
try:
    from ..utils.core.file_system import get_file_system  # type: ignore
except ImportError:  # pragma: no cover - fallback for direct execution
    try:
        from utils.core.file_system import get_file_system  # type: ignore
    except ImportError:
        # Minimal file system fallback used during isolated tests
        class MinimalFileSystem:
            def get_database_path(self, filename="prompts.db"):
                return Path(filename)

            def get_backup_dir(self):
                backup_dir = Path("backups")
                backup_dir.mkdir(exist_ok=True)
                return backup_dir

            def get_directory_info(self):
                if ComfyUIFileSystem is not None:
                    try:
                        fs_helper = ComfyUIFileSystem()
                        comfyui_root = fs_helper.resolve_comfyui_root()
                        return {
                            'path': str(comfyui_root),
                            'is_custom': False
                        }
                    except Exception:  # noqa: BLE001 - runtime best effort fallback
                        pass

                raise RuntimeError(
                    "Cannot determine ComfyUI root directory. "
                    "Please ensure PromptManager is installed in ComfyUI/custom_nodes/ "
                    "or set COMFYUI_PATH environment variable."
                )

        def get_file_system():  # type: ignore
            return MinimalFileSystem()

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

# Import hashing function once at module level to avoid sys.path modifications during database operations
# CRITICAL: There are TWO utils directories:
#   1. /utils/ at package root (contains validation/hashing.py)
#   2. /src/utils/ (contains file_operations.py, file_metadata.py, etc.)
# When src/ is in sys.path, Python finds src/utils first, which doesn't have validation/
# We use importlib with explicit path manipulation to load from package root utils

import sys as _sys
import os as _os
import importlib.util as _importlib_util

# Calculate paths
_current_file = _os.path.abspath(__file__)
_database_dir = _os.path.dirname(_current_file)  # src/database/
_src_dir = _os.path.dirname(_database_dir)  # src/
_project_root = _os.path.dirname(_src_dir)  # package root
_hashing_module_path = _os.path.join(_project_root, 'utils', 'validation', 'hashing.py')

# Load the hashing module explicitly from package root
_spec = _importlib_util.spec_from_file_location("_hashing_module", _hashing_module_path)
_hashing_module = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(_hashing_module)
generate_prompt_hash = _hashing_module.generate_prompt_hash

from .models import PromptModel


class PromptDatabase:
    """Database operations with ComfyUI directory structure support."""

    def __init__(self, db_filename: str = "prompts.db"):
        """
        Initialize database operations using proper ComfyUI directory.

        Args:
            db_filename: Database filename (not full path)
        """
        self.logger = get_logger('prompt_manager.database')
        self.file_system = get_file_system()

        # Get proper database path from file system
        self.db_path = self.file_system.get_database_path(db_filename)
        self.logger.info(f"Initializing database at: {self.db_path}")

        # Initialize model with proper path
        self.model = PromptModel(str(self.db_path))
        self.logger.debug("Database operations initialized successfully")

    def save_prompt(
        self,
        prompt: Optional[str] = None,
        *,
        # Backward-compat alias used by some callers
        text: Optional[str] = None,
        negative_prompt: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        rating: Optional[int] = None,
        notes: Optional[str] = None,
        prompt_hash: Optional[str] = None,
        model_hash: Optional[str] = None,
        sampler_settings: Optional[Dict] = None,
        generation_params: Optional[Dict] = None
    ) -> int:
        """
        Save a new prompt to the database.

        Args:
            positive_prompt: The positive prompt text (required)
            negative_prompt: The negative prompt text (optional, None for FLUX)
            category: Optional category
            tags: List of tags
            rating: Rating 1-5
            notes: Optional notes
            prompt_hash: SHA256 hash of the prompt
            model_hash: Hash of the model used
            sampler_settings: Sampler configuration
            generation_params: Other generation parameters

        Returns:
            int: The ID of the saved prompt

        Raises:
            ValueError: If required parameters are invalid
            sqlite3.Error: If database operation fails
        """
        main_prompt = prompt if prompt is not None else text
        if not main_prompt or not str(main_prompt).strip():
            raise ValueError("Prompt cannot be empty")

        if rating is not None and (rating < 1 or rating > 5):
            raise ValueError("Rating must be between 1 and 5")

        tags_json = json.dumps(tags) if tags else None
        sampler_json = json.dumps(sampler_settings) if sampler_settings else None
        params_json = json.dumps(generation_params) if generation_params else None

        # Handle negative prompt - None for FLUX, empty string converts to None
        processed_negative = negative_prompt.strip() if negative_prompt else None
        if processed_negative == "":
            processed_negative = None

        try:
            self.logger.debug(
                f"Saving prompt: length={len(str(main_prompt))}, has_negative={processed_negative is not None}, category={category}"
            )
        except Exception:
            pass

        # Compute prompt_hash if not provided; used for deduplication
        if not prompt_hash:
            prompt_hash = generate_prompt_hash(str(main_prompt))

        with self.model.get_connection() as conn:
            # Check for existing prompt with same hash (dedup)
            try:
                cur = conn.execute("SELECT id FROM prompts WHERE hash = ? LIMIT 1", (prompt_hash,))
                row = cur.fetchone()
                if row and row[0]:
                    existing_id = int(row[0])
                    self.logger.info(f"Duplicate prompt detected (hash match). Using existing ID: {existing_id}")
                    return existing_id
            except Exception:
                pass

            cursor = conn.execute(
                """
                INSERT INTO prompts (
                    positive_prompt, negative_prompt, category, tags, rating, notes,
                    hash, model_hash, sampler_settings, generation_params, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(main_prompt).strip(),
                    processed_negative or '',
                    category,
                    tags_json,
                    rating,
                    notes,
                    prompt_hash,
                    model_hash,
                    sampler_json,
                    params_json,
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
                "SELECT * FROM prompts WHERE id = ?",
                (prompt_id,)
            )
            row = cursor.fetchone()

            if row:
                return self._row_to_dict(row)
            return None

    def get_all_prompts(
        self,
        category: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get all prompts with optional filtering.

        Args:
            category: Filter by category
            limit: Maximum number of prompts to return
            offset: Number of prompts to skip

        Returns:
            List of prompt dictionaries
        """
        query = "SELECT * FROM prompts"
        params = []

        if category:
            query += " WHERE category = ?"
            params.append(category)

        query += " ORDER BY created_at DESC"

        if limit:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        with self.model.get_connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

            return [self._row_to_dict(row) for row in rows]

    def update_prompt(
        self,
        prompt_id: int,
        prompt: Optional[str] = None,
        *,
        # Backward-compat alias
        text: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        rating: Optional[int] = None,
        notes: Optional[str] = None
    ) -> bool:
        """
        Update an existing prompt.

        Args:
            prompt_id: ID of the prompt to update
            text: New prompt text
            category: New category
            tags: New tags list
            rating: New rating
            notes: New notes

        Returns:
            bool: True if update was successful
        """
        updates = []
        params = []

        new_prompt = prompt if prompt is not None else text
        if new_prompt is not None:
            updates.append("prompt = ?")
            params.append(str(new_prompt).strip())

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
        Delete a prompt by ID.

        Args:
            prompt_id: ID of the prompt to delete

        Returns:
            bool: True if deletion was successful
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM prompts WHERE id = ?",
                (prompt_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def search_prompts(
        self,
        search_text: str,
        search_in: List[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Search prompts by text.

        Args:
            search_text: Text to search for
            search_in: List of fields to search in ['text', 'category', 'tags', 'notes']

        Returns:
            List of matching prompts
        """
        if not search_in:
            search_in = ['positive_prompt', 'category', 'tags', 'notes']

        conditions = []
        params = []

        # Support both legacy 'text' and current 'positive_prompt' field names
        if 'text' in search_in or 'positive_prompt' in search_in or 'prompt' in search_in:
            conditions.append("positive_prompt LIKE ?")
            params.append(f"%{search_text}%")

        if 'category' in search_in:
            conditions.append("category LIKE ?")
            params.append(f"%{search_text}%")

        if 'tags' in search_in:
            conditions.append("tags LIKE ?")
            params.append(f"%{search_text}%")

        if 'notes' in search_in:
            conditions.append("notes LIKE ?")
            params.append(f"%{search_text}%")

        if not conditions:
            return []

        query = f"SELECT * FROM prompts WHERE {' OR '.join(conditions)} ORDER BY created_at DESC"

        with self.model.get_connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

            return [self._row_to_dict(row) for row in rows]

    def get_categories(self) -> List[str]:
        """
        Get all unique categories.

        Returns:
            List of category names
        """
        with self.model.get_connection() as conn:
            cursor = conn.execute(
                "SELECT DISTINCT category FROM prompts WHERE category IS NOT NULL ORDER BY category"
            )
            return [row[0] for row in cursor.fetchall()]

    def get_database_info(self) -> Dict[str, Any]:
        """
        Get database statistics and information.

        Returns:
            Dictionary with database stats
        """
        info = self.model.get_database_info()

        # Add file system info
        dir_info = self.file_system.get_directory_info()
        info['data_directory'] = dir_info['path']
        info['is_custom_path'] = dir_info['is_custom']

        return info

    def backup_database(self, backup_name: Optional[str] = None) -> Path:
        """
        Create a backup of the database.

        Args:
            backup_name: Optional backup filename

        Returns:
            Path to the backup file
        """
        if not backup_name:
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_name = f"prompts_backup_{timestamp}.db"

        backup_path = self.file_system.get_backup_dir() / backup_name
        success = self.model.backup_database(str(backup_path))

        if success:
            self.logger.info(f"Database backed up to: {backup_path}")
            return backup_path
        else:
            raise Exception("Failed to create database backup")

    def link_image_to_prompt(
        self,
        prompt_id: int,
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
        import os
        from pathlib import Path

        # DEBUG logging
        debug = os.getenv("PROMPTMANAGER_DEBUG", "0") == "1"
        if debug:
            print(f"\n📝 [link_image_to_prompt] Starting...")
            print(f"   prompt_id: {prompt_id}")
            print(f"   image_path: {image_path}")
            print(f"   metadata keys: {list(metadata.keys()) if metadata else 'None'}")

        if not image_path:
            raise ValueError("image_path is required")

        try:
            normalized_path = str(Path(image_path).expanduser().resolve())
            if debug:
                print(f"   normalized_path: {normalized_path}")
        except Exception as e:
            if debug:
                print(f"   ⚠️ Path normalization failed: {e}")
            normalized_path = os.path.abspath(image_path)

        filename = os.path.basename(normalized_path)
        if debug:
            print(f"   filename: {filename}")

        if debug:
            print(f"   Opening database connection...")

        with self.model.get_connection() as conn:
            if debug:
                print(f"   ✅ Database connection opened")
                print(f"   Checking for existing image...")

            existing = conn.execute(
                "SELECT id, prompt_id FROM generated_images WHERE image_path = ? LIMIT 1",
                (normalized_path,)
            ).fetchone()

            if existing:
                existing_id = int(existing[0])
                existing_prompt_id = existing[1] if len(existing) > 1 else None

                if debug:
                    print(f"   ⏭️ Image already exists (id: {existing_id}, old_prompt_id: {existing_prompt_id})")

                # If linked to a different prompt, update it to the new prompt
                if existing_prompt_id != prompt_id:
                    if debug:
                        print(f"   🔄 Updating link: {existing_prompt_id} → {prompt_id}")

                    conn.execute(
                        "UPDATE generated_images SET prompt_id = ? WHERE id = ?",
                        (prompt_id, existing_id)
                    )
                    # Commit is handled by context manager
                    self.logger.info(
                        f"Updated image {filename} link: prompt {existing_prompt_id} → {prompt_id} (image_id: {existing_id})"
                    )
                    if debug:
                        print(f"   ✅ Successfully updated prompt link!")
                else:
                    if debug:
                        print(f"   ℹ️ Already linked to correct prompt, no update needed")
                    self.logger.debug(
                        f"Image {filename} already linked to prompt {prompt_id} (image_id: {existing_id})"
                    )

                return existing_id

            if debug:
                print(f"   Image not found, creating new record...")

            # Get file info
            file_size = 0
            width = 0
            height = 0
            format_type = Path(normalized_path).suffix.lstrip('.')

            if os.path.exists(normalized_path):
                file_size = os.path.getsize(normalized_path)

                try:
                    from PIL import Image
                    with Image.open(normalized_path) as img:
                        width, height = img.size
                        format_type = img.format or format_type
                except Exception:
                    pass

            # Extract workflow data and parameters from metadata
            workflow_data = None
            parameters = None
            prompt_metadata = None

            if metadata:
                workflow_data = json.dumps(metadata.get('workflow', {})) if metadata.get('workflow') else None
                parameters = json.dumps(metadata.get('parameters', {})) if isinstance(metadata.get('parameters'), dict) else None
                prompt_metadata = json.dumps(metadata.get('prompt', {})) if metadata.get('prompt') else None

            query = """
                INSERT INTO generated_images (
                    prompt_id, image_path, filename, file_size,
                    width, height, format, workflow_data,
                    prompt_metadata, parameters
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

            params = (
                prompt_id, normalized_path, filename, file_size,
                width, height, format_type, workflow_data,
                prompt_metadata, parameters
            )

            if debug:
                print(f"   Executing INSERT query...")
                print(f"   Query params: prompt_id={prompt_id}, filename={filename}")

            cursor = conn.execute(query, params)

            if debug:
                print(f"   INSERT executed, committing...")

            # Note: commit is handled by the context manager in get_connection()
            image_id = cursor.lastrowid

            if debug:
                print(f"   ✅ Successfully linked image! image_id={image_id}")

            self.logger.info(f"Linked image {filename} to prompt {prompt_id} (image_id: {image_id})")
            return image_id

    def image_exists(self, image_path: str) -> bool:
        """Check if an image path is already linked in the database."""
        if not image_path:
            return False

        candidates = []
        try:
            candidates.append(str(Path(image_path).expanduser().resolve()))
        except Exception:
            pass
        candidates.append(str(image_path))

        seen = set()
        with self.model.get_connection() as conn:
            for candidate in candidates:
                if not candidate or candidate in seen:
                    continue
                seen.add(candidate)
                cur = conn.execute(
                    "SELECT 1 FROM generated_images WHERE image_path = ? LIMIT 1",
                    (candidate,)
                )
                if cur.fetchone():
                    return True
        return False

    def _row_to_dict(self, row) -> Dict[str, Any]:
        """
        Convert a database row to a dictionary.

        Args:
            row: SQLite row object

        Returns:
            Dictionary representation of the row
        """
        prompt_dict = dict(row)

        # Parse JSON fields
        if prompt_dict.get('tags'):
            try:
                prompt_dict['tags'] = json.loads(prompt_dict['tags'])
            except json.JSONDecodeError:
                prompt_dict['tags'] = []

        return prompt_dict


# Create global instance for convenience
_db_instance = None


def get_database() -> PromptDatabase:
    """Get the global database instance."""
    global _db_instance
    if _db_instance is None:
        _db_instance = PromptDatabase()
    return _db_instance
