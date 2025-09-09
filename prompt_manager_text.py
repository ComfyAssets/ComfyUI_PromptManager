"""
PromptManagerText: A text-only version of PromptManager that outputs STRING
without CLIP encoding, while maintaining all database and search features.
"""

import datetime
import hashlib
import json
import os
import time
import webbrowser
from typing import Any, Dict, List, Optional, Tuple

# Import logging system
try:
    from .utils.logging_config import get_logger
except ImportError:
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from utils.logging_config import get_logger

try:
    from comfy.comfy_types import IO, ComfyNodeABC, InputTypeDict
except ImportError:
    # Fallback for older ComfyUI versions
    class ComfyNodeABC:
        pass

    class IO:
        STRING = "STRING"
        CLIP = "CLIP"
        CONDITIONING = "CONDITIONING"

    InputTypeDict = dict

try:
    from .database.operations import PromptDatabase
    from .utils.comfyui_integration import get_comfyui_integration
    from .utils.image_monitor import ImageMonitor
    from .utils.prompt_tracker import PromptExecutionContext, PromptTracker
except ImportError:
    # For direct imports when not in a package
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from database.operations import PromptDatabase
    from utils.comfyui_integration import get_comfyui_integration
    from utils.image_monitor import ImageMonitor
    from utils.prompt_tracker import PromptExecutionContext, PromptTracker


class PromptManagerText(ComfyNodeABC):
    """
    A ComfyUI custom node that provides all PromptManager features but outputs
    only a STRING without CLIP encoding. Includes:
    - Persistent storage of all prompts in SQLite database
    - Search and retrieval capabilities
    - Metadata management (categories, tags, ratings, notes)
    - Duplicate detection via SHA256 hashing
    - Text concatenation with prepend/append functionality
    """

    def __init__(self):
        self.logger = get_logger("prompt_manager_text.node")
        self.logger.debug("Initializing PromptManagerText node")

        self.db = PromptDatabase()
        self.prompt_tracker = PromptTracker(self.db)
        self.image_monitor = ImageMonitor(self.db, self.prompt_tracker)
        self.comfyui_integration = get_comfyui_integration()

        # Start image monitoring automatically
        self._start_gallery_system()
        self.logger.debug("PromptManagerText node initialization completed")

    @classmethod
    def INPUT_TYPES(cls) -> InputTypeDict:
        return {
            "required": {
                "text": (
                    IO.STRING,
                    {
                        "multiline": True,
                        "dynamicPrompts": True,
                        "tooltip": "The text prompt to be processed and saved to database.",
                    },
                )
            },
            "optional": {
                "category": (
                    IO.STRING,
                    {
                        "default": "",
                        "tooltip": "Optional category for organizing prompts (e.g., 'landscapes', 'portraits')",
                    },
                ),
                "tags": (
                    IO.STRING,
                    {
                        "default": "",
                        "tooltip": "Comma-separated tags for the prompt (e.g., 'anime, detailed, sunset')",
                    },
                ),
                "search_text": (
                    IO.STRING,
                    {
                        "default": "",
                        "tooltip": "Search for past prompts containing this text",
                    },
                ),
                "prepend_text": (
                    IO.STRING,
                    {
                        "default": "",
                        "tooltip": "Text to prepend to the main prompt (connected STRING nodes will be added before the main text)",
                    },
                ),
                "append_text": (
                    IO.STRING,
                    {
                        "default": "",
                        "tooltip": "Text to append to the main prompt (connected STRING nodes will be added after the main text)",
                    },
                ),
            },
        }

    RETURN_TYPES = (IO.STRING,)
    OUTPUT_TOOLTIPS = (
        "The final combined text string (with prepend/append applied) ready for use in other nodes.",
    )
    FUNCTION = "process_text"
    CATEGORY = "🫶 ComfyAssets/🧠 Prompts"
    DESCRIPTION = (
        "Processes and manages text prompts with database storage and search capabilities. "
        "Outputs a plain STRING that can be used with any node that accepts text input. "
        "Includes all PromptManager features: categorization, tagging, search, and prepend/append functionality."
    )

    def process_text(
        self,
        text: str,
        category: str = "",
        tags: str = "",
        search_text: str = "",
        prepend_text: str = "",
        append_text: str = "",
    ) -> Tuple[str]:
        """
        Process the text prompt and save it to the database.

        Args:
            text: The text prompt to process
            category: Optional category for organization
            tags: Comma-separated tags
            search_text: Text to search for in past prompts
            prepend_text: Text to prepend to the main prompt
            append_text: Text to append to the main prompt

        Returns:
            Tuple containing the final processed text string
        """
        # Combine prepend, main text, and append text
        final_text = ""
        if prepend_text and prepend_text.strip():
            final_text += prepend_text.strip() + " "
        final_text += text
        if append_text and append_text.strip():
            final_text += " " + append_text.strip()

        # For database storage, save the original main text with metadata about prepend/append
        storage_text = text

        # Search functionality is now handled by the JavaScript UI
        # The search parameters are still available for backend processing if needed

        # Save prompt to database and set execution context for gallery tracking
        prompt_id = None
        if storage_text and storage_text.strip():
            self.logger.debug(f"Processing prompt text: {storage_text[:100]}...")

            # Add prepend/append info to tags if they exist
            extended_tags = self._parse_tags(tags) or []
            if prepend_text and prepend_text.strip():
                extended_tags.append(f"prepend:{prepend_text.strip()[:50]}")
            if append_text and append_text.strip():
                extended_tags.append(f"append:{append_text.strip()[:50]}")

            try:
                prompt_id = self._save_prompt_to_database(
                    text=storage_text.strip(),  # Always strip whitespace
                    category=category.strip() if category else None,
                    tags=extended_tags if extended_tags else None,
                )

                # Set current prompt for image tracking
                if prompt_id:
                    execution_id = self.prompt_tracker.set_current_prompt(
                        prompt_text=final_text.strip(),  # Use final combined text for tracking
                        additional_data={
                            "category": category.strip() if category else None,
                            "tags": extended_tags,
                            "prompt_id": prompt_id,
                            "prepend_text": (
                                prepend_text.strip() if prepend_text else None
                            ),
                            "append_text": append_text.strip() if append_text else None,
                        },
                    )
                    self.logger.debug(
                        f"Set execution context: {execution_id} for prompt ID: {prompt_id}"
                    )

            except Exception as e:
                # Log error but don't fail the processing
                self.logger.warning(f"Failed to save prompt to database: {e}")

        # Register with ComfyUI integration for standard metadata compatibility
        node_id = f"promptmanagertext_{int(time.time() * 1000)}"  # Unique node ID
        self.comfyui_integration.register_prompt(
            node_id,
            final_text.strip(),
            {
                "category": category.strip() if category else None,
                "tags": extended_tags,
                "prompt_id": prompt_id,
                "prepend_text": prepend_text.strip() if prepend_text else None,
                "append_text": append_text.strip() if append_text else None,
            },
        )

        self.logger.debug(f"Text processing completed: {final_text[:100]}...")
        return (final_text,)

    def _save_prompt_to_database(
        self, text: str, category: Optional[str] = None, tags: Optional[list] = None
    ) -> Optional[int]:
        """
        Save the prompt to the SQLite database.

        Args:
            text: The prompt text
            category: Optional category
            tags: List of tags

        Returns:
            The prompt ID if saved successfully, None otherwise
        """
        try:
            # Generate hash for duplicate detection
            prompt_hash = self._generate_hash(text)
            self.logger.debug(f"Generated hash for prompt: {prompt_hash[:16]}...")

            # Check if prompt already exists
            existing = self.db.get_prompt_by_hash(prompt_hash)
            if existing:
                self.logger.info(
                    f"Found existing prompt with ID {existing['id']}, updating metadata"
                )
                # Update metadata if this is a duplicate with new info
                if any([category, tags]):
                    self.db.update_prompt_metadata(
                        prompt_id=existing["id"], category=category, tags=tags
                    )
                    self.logger.debug("Updated metadata for existing prompt")
                return existing["id"]

            # Save new prompt
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
        """
        Generate SHA256 hash for the prompt text.
        
        Args:
            text: The prompt text to hash
            
        Returns:
            Hexadecimal string representation of the SHA256 hash
        """
        # Normalize text for consistent hashing (strip whitespace, normalize case)
        normalized_text = text.strip().lower()
        return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()

    def _parse_tags(self, tags_string: str) -> Optional[list]:
        """
        Parse comma-separated tags string into a list.
        
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
        """
        Search for past prompts by text content.
        
        Args:
            search_text: Text to search for in prompt database
            
        Returns:
            List of matching prompt dictionaries with metadata
        """
        try:
            if not search_text or not search_text.strip():
                return []

            results = self.db.search_prompts(
                text=search_text.strip(),
                category=None,
                tags=None,
                rating_min=None,
                limit=50,
            )

            return results

        except Exception as e:
            self.logger.error(f"Error searching prompts: {e}")
            return []

    def _open_web_interface(self):
        """
        Open the web interface in the default browser.
        
        Attempts to locate and open the web interface HTML file.
        Logs warnings if the interface is not properly configured.
        """
        try:
            # Look for a web interface directory
            current_dir = os.path.dirname(os.path.abspath(__file__))
            web_dir = os.path.join(current_dir, "web_interface")

            if os.path.exists(web_dir):
                # If web interface exists, try to start it
                index_path = os.path.join(web_dir, "index.html")
                if os.path.exists(index_path):
                    webbrowser.open(f"file://{index_path}")
                    self.logger.info("Web interface opened in browser")
                else:
                    self.logger.warning(
                        f"Web interface directory found but no index.html. Please check {web_dir} for setup instructions"
                    )
            else:
                self.logger.info(
                    "Web interface not yet implemented. This feature will open a web-based prompt management interface when the web_interface directory is created."
                )

        except Exception as e:
            self.logger.error(f"Error opening web interface: {e}")

    def search_prompts_api(self, search_text: str = "") -> List[Dict[str, Any]]:
        """
        API method for JavaScript UI to search prompts.
        
        Args:
            search_text: Text to search for in prompts
            
        Returns:
            List of matching prompt dictionaries
        """
        return self._search_prompts(search_text=search_text)

    def get_recent_prompts_api(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        API method for JavaScript UI to get recent prompts.
        
        Args:
            limit: Maximum number of recent prompts to retrieve
            
        Returns:
            List of recent prompt dictionaries ordered by creation time
        """
        try:
            return self.db.get_recent_prompts(limit=limit)
        except Exception as e:
            self.logger.error(f"Error getting recent prompts: {e}")
            return []

    def _start_gallery_system(self):
        """
        Initialize and start the gallery monitoring system.
        
        Starts the image monitor which watches for new generated images
        and links them to their source prompts in the database.
        """
        try:
            self.logger.debug("Starting gallery system...")

            # Start image monitoring
            self.image_monitor.start_monitoring()

            self.logger.debug("Gallery system started successfully")

        except Exception as e:
            self.logger.error(f"Failed to start gallery system: {e}")
            self.logger.warning("Gallery features will be disabled")

    def get_gallery_status(self) -> Dict[str, Any]:
        """
        Get status of the gallery system.
        
        Returns:
            Dictionary containing status information for image monitor and prompt tracker
        """
        return {
            "image_monitor": self.image_monitor.get_status(),
            "prompt_tracker": self.prompt_tracker.get_status(),
        }

    def cleanup_gallery_system(self):
        """
        Clean up gallery system resources.
        
        Stops image monitoring and releases associated resources.
        Called automatically during object destruction.
        """
        try:
            if hasattr(self, "image_monitor"):
                self.image_monitor.stop_monitoring()
            self.logger.debug("Gallery system cleaned up")
        except Exception as e:
            self.logger.error(f"Error cleaning up gallery system: {e}")

    def __del__(self):
        """
        Cleanup when object is destroyed.
        
        Ensures proper resource cleanup by stopping the gallery system.
        """
        self.cleanup_gallery_system()

    @classmethod
    def IS_CHANGED(cls, text="", category="", tags="", search_text="", 
                    prepend_text="", append_text="", **kwargs):
        """
        ComfyUI method to determine if node needs re-execution.
        
        This method now properly tracks input changes to avoid unnecessary
        re-execution while still ensuring prompts are saved when inputs change.
        
        Returns:
            A hash of the input values that changes when any input changes
        """
        # Create a hash of all the text inputs that affect the output
        # This ensures the node only re-executes when inputs actually change
        import hashlib
        
        # Combine all text inputs that affect the output
        combined = f"{text}|{category}|{tags}|{prepend_text}|{append_text}"
        
        # Return a hash that will change when inputs change
        # Note: We don't include search_text as it doesn't affect the output
        return hashlib.sha256(combined.encode()).hexdigest()
