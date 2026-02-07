"""
PromptManagerBase: Shared logic for PromptManager node variants.

Provides database initialization, prompt saving, hashing, tag parsing,
search, gallery system management, and cleanup — extracted from the
duplicate code in prompt_manager.py and prompt_manager_text.py.
"""

import hashlib
import os
import webbrowser
from typing import Any, Dict, List, Optional

try:
    from .utils.logging_config import get_logger
except ImportError:
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from utils.logging_config import get_logger

try:
    from .database.operations import PromptDatabase
    from .utils.comfyui_integration import get_comfyui_integration
    from .utils.image_monitor import get_image_monitor
    from .utils.prompt_tracker import get_prompt_tracker
except ImportError:
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from database.operations import PromptDatabase
    from utils.comfyui_integration import get_comfyui_integration
    from utils.image_monitor import get_image_monitor
    from utils.prompt_tracker import get_prompt_tracker


class PromptManagerBase:
    """Mixin providing shared prompt management logic for ComfyUI nodes.

    Handles database connection, prompt saving with deduplication,
    tag parsing, search, gallery system lifecycle, and cleanup.
    Subclasses only need to define ComfyUI-specific class attributes
    (INPUT_TYPES, RETURN_TYPES, FUNCTION) and their execution method.
    """

    def __init__(self, logger_name: str = "prompt_manager.node"):
        self.logger = get_logger(logger_name)
        self.logger.debug(f"Initializing {self.__class__.__name__} node")

        self.db = PromptDatabase()
        self.prompt_tracker = get_prompt_tracker(self.db)
        self.image_monitor = get_image_monitor(self.db, self.prompt_tracker)
        self.comfyui_integration = get_comfyui_integration()

        self._start_gallery_system()
        self.logger.debug(f"{self.__class__.__name__} node initialization completed")

    def _save_prompt_to_database(
        self, text: str, category: Optional[str] = None, tags: Optional[list] = None
    ) -> Optional[int]:
        """Save the prompt to the SQLite database.

        Args:
            text: The prompt text
            category: Optional category
            tags: List of tags

        Returns:
            The prompt ID if saved successfully, None otherwise
        """
        try:
            prompt_hash = self._generate_hash(text)
            self.logger.debug(f"Generated hash for prompt: {prompt_hash[:16]}...")

            existing = self.db.get_prompt_by_hash(prompt_hash)
            if existing:
                self.logger.info(
                    f"Found existing prompt with ID {existing['id']}, updating metadata"
                )
                if any([category, tags]):
                    self.db.update_prompt_metadata(
                        prompt_id=existing["id"], category=category, tags=tags
                    )
                    self.logger.debug("Updated metadata for existing prompt")
                return existing["id"]

            self.logger.debug(
                f"Saving new prompt with category: {category}, tags: {tags}"
            )
            prompt_id = self.db.save_prompt(
                text=text, category=category, tags=tags, prompt_hash=prompt_hash
            )

            if prompt_id:
                self.logger.debug(f"Successfully saved new prompt with ID: {prompt_id}")
            else:
                self.logger.warning("Failed to save prompt - no ID returned")

            return prompt_id

        except Exception as e:
            self.logger.error(f"Error saving prompt to database: {e}")
            return None

    def _generate_hash(self, text: str) -> str:
        """Generate SHA256 hash for the prompt text.

        Args:
            text: The prompt text to hash

        Returns:
            Hexadecimal string representation of the SHA256 hash
        """
        normalized_text = text.strip().lower()
        return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()

    def _parse_tags(self, tags_string: str) -> Optional[list]:
        """Parse comma-separated tags string into a list.

        Args:
            tags_string: Comma-separated string of tags

        Returns:
            List of parsed tags, or None if no valid tags found
        """
        if not tags_string or not tags_string.strip():
            return None

        tags = [tag.strip() for tag in tags_string.split(",") if tag.strip()]
        return tags if tags else None

    def _search_prompts(self, search_text: str = "") -> List[Dict[str, Any]]:
        """Search for past prompts by text content.

        Args:
            search_text: Text to search for in prompt database

        Returns:
            List of matching prompt dictionaries with metadata
        """
        try:
            if not search_text or not search_text.strip():
                return []

            try:
                from .py.config import PromptManagerConfig
                max_results = PromptManagerConfig.MAX_SEARCH_RESULTS
            except Exception:
                max_results = 100

            results = self.db.search_prompts(
                text=search_text.strip(),
                category=None,
                tags=None,
                rating_min=None,
                limit=max_results,
            )

            return results

        except Exception as e:
            self.logger.error(f"Error searching prompts: {e}")
            return []

    def _open_web_interface(self):
        """Open the web interface in the default browser."""
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            web_dir = os.path.join(current_dir, "web_interface")

            if os.path.exists(web_dir):
                index_path = os.path.join(web_dir, "index.html")
                if os.path.exists(index_path):
                    webbrowser.open(f"file://{index_path}")
                    self.logger.info("Web interface opened in browser")
                else:
                    self.logger.warning(
                        f"Web interface directory found but no index.html. "
                        f"Please check {web_dir} for setup instructions"
                    )
            else:
                self.logger.info(
                    "Web interface not yet implemented. This feature will open a "
                    "web-based prompt management interface when the web_interface "
                    "directory is created."
                )

        except Exception as e:
            self.logger.error(f"Error opening web interface: {e}")

    def search_prompts_api(self, search_text: str = "") -> List[Dict[str, Any]]:
        """API method for JavaScript UI to search prompts."""
        return self._search_prompts(search_text=search_text)

    def get_recent_prompts_api(self, limit: int = 20) -> List[Dict[str, Any]]:
        """API method for JavaScript UI to get recent prompts."""
        try:
            return self.db.get_recent_prompts(limit=limit)
        except Exception as e:
            self.logger.error(f"Error getting recent prompts: {e}")
            return []

    def _start_gallery_system(self):
        """Initialize and start the gallery monitoring system."""
        try:
            self.logger.debug("Starting gallery system...")
            self.image_monitor.start_monitoring()
            self.logger.debug("Gallery system started successfully")
        except Exception as e:
            self.logger.error(f"Failed to start gallery system: {e}")
            self.logger.warning("Gallery features will be disabled")

    def get_gallery_status(self) -> Dict[str, Any]:
        """Get status of the gallery system."""
        return {
            "image_monitor": self.image_monitor.get_status(),
            "prompt_tracker": self.prompt_tracker.get_status(),
        }

    def cleanup_gallery_system(self):
        """Clean up gallery system resources."""
        try:
            if hasattr(self, "image_monitor"):
                self.image_monitor.stop_monitoring()
            self.logger.debug("Gallery system cleaned up")
        except Exception as e:
            self.logger.error(f"Error cleaning up gallery system: {e}")

    def __del__(self):
        """Cleanup when object is destroyed."""
        self.cleanup_gallery_system()
