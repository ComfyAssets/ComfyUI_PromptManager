"""Image monitoring system for ComfyUI generated images.

This module provides real-time monitoring of ComfyUI output directories to automatically
detect newly generated images and associate them with their corresponding prompts. The system
uses filesystem watchers to detect image creation events and extract metadata from the images
to maintain a gallery system.

The main components are:
- ImageGenerationHandler: Handles filesystem events for new image creation
- ImageMonitor: Main monitoring system that manages directory watching

Typical usage:
    from utils.image_monitor import ImageMonitor

    monitor = ImageMonitor(db_manager, prompt_tracker)
    monitor.start_monitoring(['/path/to/comfyui/output'])

The system automatically:
- Detects new image files in monitored directories
- Extracts ComfyUI workflow metadata from PNG chunks
- Links images to active prompts using the prompt tracker
- Handles fallback linking when no active prompt is available
- Provides status information and monitoring control
"""

import os
import time
import threading
import json
from pathlib import Path
from typing import Optional, Dict, Any, Callable
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .metadata_extractor import ComfyUIMetadataExtractor
from .logging_config import get_logger


class ImageGenerationHandler(FileSystemEventHandler):
    """Filesystem event handler for detecting new image generation.

    This handler extends watchdog's FileSystemEventHandler to specifically handle
    new image file creation events in ComfyUI output directories. When a new image
    is detected, it attempts to:
    1. Extract ComfyUI metadata from the image
    2. Associate the image with the currently active prompt
    3. Store the relationship in the database

    The handler implements a small delay before processing to ensure files are
    completely written before attempting to read them.
    """

    def __init__(self, db_manager, prompt_tracker):
        """
        Initialize the image generation handler.

        Args:
            db_manager: Database manager instance for storing image-prompt relationships
            prompt_tracker: Prompt tracking instance for getting current active prompts
        """
        self.db_manager = db_manager
        self.prompt_tracker = prompt_tracker
        self.metadata_extractor = ComfyUIMetadataExtractor()
        self.logger = get_logger("prompt_manager.image_monitor")

        # Read from GalleryConfig if available, otherwise use defaults
        try:
            from ..py.config import GalleryConfig

            self.processing_delay = GalleryConfig.PROCESSING_DELAY
            self.supported_extensions = tuple(GalleryConfig.SUPPORTED_EXTENSIONS)
        except Exception:
            self.processing_delay = 2.0
            self.supported_extensions = (".png", ".jpg", ".jpeg", ".webp", ".gif")

    def on_created(self, event):
        """Handle filesystem creation events.

        This method is called by watchdog when a new file is created in a monitored
        directory. It filters for image files and schedules them for processing after
        a small delay to ensure the file is fully written.

        The prompt context is captured immediately (before the delay) to handle
        batch workflows where the prompt tracker advances before images are processed.

        Args:
            event: FileSystemEvent object containing event details
        """
        if not event.is_directory and self.is_image_file(event.src_path):
            self.logger.info(f"New image detected: {event.src_path}")
            # Snapshot prompt context NOW before the delay — in batch workflows
            # the tracker advances to the next prompt before images are processed.
            prompt_snapshot = self.prompt_tracker.get_current_prompt()
            if prompt_snapshot:
                self.logger.debug(
                    f"Snapshot prompt {prompt_snapshot.get('id', '?')} for {os.path.basename(event.src_path)}"
                )
            threading.Timer(
                self.processing_delay,
                self.process_new_image,
                args=[event.src_path],
                kwargs={"prompt_snapshot": prompt_snapshot},
            ).start()

    def is_image_file(self, filepath: str) -> bool:
        """Check if file is a supported image format and not a thumbnail.

        Args:
            filepath: Path to the file to check

        Returns:
            True if the file has a supported image extension and is not
            inside a thumbnails directory, False otherwise
        """
        # Skip files in thumbnails directory - those are derivatives, not generated images
        if "/thumbnails/" in filepath or "\\thumbnails\\" in filepath:
            return False
        return filepath.lower().endswith(self.supported_extensions)

    def process_new_image(self, image_path: str, prompt_snapshot=None):
        """Process a newly created image file for gallery integration.

        This method handles the complete processing pipeline for a new image:
        1. Verifies the file still exists
        2. Extracts ComfyUI metadata from the image
        3. Tries to identify the correct prompt via metadata-based lookup
        4. Falls back to the prompt snapshot captured at event time
        5. Links the image to the appropriate prompt in the database

        Args:
            image_path: Full path to the newly created image file
            prompt_snapshot: Prompt context captured at file-creation time (optional)
        """
        try:
            self.logger.info(f"Processing image: {image_path}")

            if not os.path.exists(image_path):
                self.logger.warning(f"Image file no longer exists: {image_path}")
                return

            # Extract ComfyUI metadata first — needed both for linking and prompt lookup
            metadata = None
            try:
                metadata = self.metadata_extractor.extract_metadata(image_path)
                self.logger.debug(f"Extracted metadata: {bool(metadata)}")
            except Exception as meta_error:
                self.logger.warning(f"Metadata extraction failed: {meta_error}")

            # Strategy 1: Pop from batch queue (most reliable for batch workflows).
            # Prompts are queued during CLIP encoding in order; images save in
            # the same order, so FIFO pop gives the correct prompt per image.
            current_prompt = self.prompt_tracker.pop_next_prompt()
            if current_prompt:
                self.logger.info(
                    f"Queue match: prompt {current_prompt['id']} for "
                    f"{os.path.basename(image_path)}"
                )

            # Strategy 2: Find prompt from image metadata
            if not current_prompt:
                current_prompt = self._find_prompt_from_metadata(metadata)
                if current_prompt:
                    self.logger.info(f"Metadata match: prompt {current_prompt['id']}")

            # Strategy 3: Use snapshot captured at file-creation time
            if not current_prompt and prompt_snapshot:
                current_prompt = prompt_snapshot
                self.logger.info(
                    f"Snapshot match: prompt {current_prompt.get('id', 'unknown')}"
                )

            # Strategy 4: Check live prompt tracker
            if not current_prompt:
                current_prompt = self.prompt_tracker.get_current_prompt()
                if current_prompt:
                    self.logger.info(
                        f"Live tracker match: prompt {current_prompt['id']}"
                    )

            # Strategy 5: Fallback to most recent prompt in DB
            if not current_prompt:
                current_prompt = self._get_fallback_prompt()
                if current_prompt:
                    self.logger.debug(f"Fallback match: prompt {current_prompt['id']}")
                else:
                    self.logger.warning(
                        f"No prompt context available, skipping image: {image_path}"
                    )
                    return

            # Extend timeout if we have an active execution
            if current_prompt.get("execution_id"):
                self.prompt_tracker.extend_prompt_timeout(
                    current_prompt["execution_id"], 300
                )

            if metadata:
                self.logger.debug(
                    f"Linking image with full metadata to prompt {current_prompt['id']}"
                )
                self.link_image_to_prompt(image_path, current_prompt, metadata)
            else:
                self.logger.debug(
                    f"Linking image with basic info to prompt {current_prompt['id']}"
                )
                basic_metadata = self.get_basic_file_info(image_path)
                self.link_image_to_prompt(
                    image_path, current_prompt, {"file_info": basic_metadata}
                )

        except Exception as e:
            self.logger.error(f"Error processing image {image_path}: {e}")
            import traceback

            self.logger.error(traceback.format_exc())

    def _find_prompt_from_metadata(self, metadata):
        """Extract prompt text from image metadata and look up the matching DB prompt.

        Parses the ComfyUI workflow/prompt data embedded in the image to find
        PromptManager node inputs, then matches against the database by hash.

        Args:
            metadata: Extracted metadata dict from the image, or None

        Returns:
            Prompt context dict with 'id' and 'text', or None if not found
        """
        if not metadata:
            return None

        prompt_text = None

        # Try to find PromptManager node in the prompt execution data
        prompt_data = metadata.get("prompt")
        if isinstance(prompt_data, dict):
            for node_id, node_info in prompt_data.items():
                if not isinstance(node_info, dict):
                    continue
                class_type = node_info.get("class_type", "")
                if class_type in ("PromptManager", "PromptManagerText"):
                    inputs = node_info.get("inputs", {})
                    text = inputs.get("text", "")
                    if text and isinstance(text, str) and text.strip():
                        prompt_text = text.strip()
                        break

        # Fallback: check text_encoder_nodes from workflow, but only if
        # the text input is NOT connected (connected inputs override widget values,
        # so the widget value would be stale in batch workflows).
        if not prompt_text:
            text_nodes = metadata.get("text_encoder_nodes", [])
            for node in text_nodes:
                node_type = node.get("type") or node.get("class_type") or ""
                if "PromptManager" in node_type:
                    # Check if text input is connected — if so, widget value is stale
                    text_connected = False
                    for inp in node.get("inputs", []):
                        if isinstance(inp, dict) and inp.get("name") == "text":
                            if inp.get("link") is not None:
                                text_connected = True
                            break
                    if text_connected:
                        self.logger.debug(
                            "PromptManager text input is connected — "
                            "skipping stale widget value, deferring to snapshot"
                        )
                        continue
                    widgets = node.get("widgets_values", [])
                    if widgets and isinstance(widgets[0], str) and widgets[0].strip():
                        prompt_text = widgets[0].strip()
                        break

        if not prompt_text:
            return None

        # Look up by hash in database
        try:
            import hashlib

            normalized = prompt_text.strip().lower()
            prompt_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            existing = self.db_manager.get_prompt_by_hash(prompt_hash)
            if existing:
                self.logger.debug(
                    f"Found DB prompt {existing['id']} from metadata text"
                )
                return {
                    "id": existing["id"],
                    "text": existing["text"],
                    "from_metadata": True,
                }
        except Exception as e:
            self.logger.warning(f"Metadata-based prompt lookup failed: {e}")

        return None

    def get_basic_file_info(self, image_path: str) -> Dict[str, Any]:
        """Get basic file information when metadata extraction fails.

        Provides fallback file information when ComfyUI metadata cannot be extracted
        from the image. Includes file size, format, and dimensions when possible.

        Args:
            image_path: Path to the image file

        Returns:
            Dictionary containing basic file information:
            - size: File size in bytes
            - format: Image format (PNG, JPEG, etc.)
            - dimensions: Image width and height as list [width, height]
        """
        try:
            from PIL import Image

            stat = os.stat(image_path)
            file_info = {"size": stat.st_size, "format": None, "dimensions": None}

            # Try to get image dimensions
            try:
                with Image.open(image_path) as img:
                    file_info["dimensions"] = list(img.size)
                    file_info["format"] = img.format
            except Exception:
                pass

            return file_info
        except Exception as e:
            self.logger.error(f"Error getting file info: {e}")
            return {}

    def _get_fallback_prompt(self) -> Optional[Dict[str, Any]]:
        """Get the most recent prompt from database as fallback.

        When no active prompt is available from the tracker, this method attempts
        to find the most recently created prompt in the database to use as a fallback
        for image linking.

        Returns:
            Dictionary containing prompt information with 'fallback' flag set to True,
            or None if no recent prompt is available
        """
        try:
            recent_prompts = self.db_manager.get_recent_prompts(limit=1)
            if recent_prompts:
                prompt = recent_prompts[0]
                return {
                    "id": prompt["id"],
                    "text": prompt["text"],
                    "timestamp": prompt.get("created_at"),
                    "fallback": True,
                }
        except Exception as e:
            self.logger.error(f"Error getting fallback prompt: {e}")
        return None

    def link_image_to_prompt(
        self, image_path: str, prompt_context: Dict, metadata: Dict
    ):
        """Link an image to a prompt in the database.

        Creates a database record associating the generated image with its source prompt,
        including any extracted metadata from the image file.

        Args:
            image_path: Full path to the image file
            prompt_context: Dictionary containing prompt information including ID and text
            metadata: Extracted metadata from the image file (workflow, parameters, etc.)
        """
        try:
            image_id = self.db_manager.link_image_to_prompt(
                prompt_id=prompt_context["id"], image_path=image_path, metadata=metadata
            )
            fallback_note = " (fallback)" if prompt_context.get("fallback") else ""
            self.logger.debug(
                f"Successfully linked image {image_id} to prompt {prompt_context['id']}{fallback_note}"
            )
        except Exception as e:
            self.logger.error(f"Failed to link image to prompt: {e}")


class ImageMonitor:
    """Main image monitoring system for ComfyUI gallery integration.

    This class manages the overall image monitoring system, including:
    - Setting up filesystem watchers for output directories
    - Auto-detecting ComfyUI output locations
    - Managing the lifecycle of monitoring operations
    - Providing status information

    The monitor uses watchdog to efficiently watch filesystem changes and can
    monitor multiple directories simultaneously with recursive subdirectory support.
    """

    def __init__(self, db_manager, prompt_tracker):
        """
        Initialize the image monitor.

        Args:
            db_manager: Database manager instance for storing image relationships
            prompt_tracker: Prompt tracking instance for getting active prompt context
        """
        self.db_manager = db_manager
        self.prompt_tracker = prompt_tracker
        self.observer = None
        self.handler = None
        self.monitored_directories = []
        self.logger = get_logger("prompt_manager.image_monitor")

    def start_monitoring(self, output_directories: Optional[list] = None):
        """
        Start monitoring ComfyUI output directories for new images.

        Begins filesystem watching on the specified directories. If no directories
        are provided, the system will first check GalleryConfig.MONITORING_DIRECTORIES,
        then fall back to auto-detecting ComfyUI output locations.
        All monitoring is done recursively to catch images in subdirectories.

        Args:
            output_directories: List of directory paths to monitor. If None, uses config or auto-detection.
        """
        if self.observer:
            self.logger.warning("Image monitoring already running")
            return

        # Check config for monitoring settings
        try:
            from ..py.config import GalleryConfig

            if not GalleryConfig.MONITORING_ENABLED:
                self.logger.info("Image monitoring disabled in config")
                return

            # Use configured directories if set
            if not output_directories and GalleryConfig.MONITORING_DIRECTORIES:
                output_directories = GalleryConfig.MONITORING_DIRECTORIES
                self.logger.info(
                    f"Using configured monitoring directories: {output_directories}"
                )
        except Exception:
            pass

        # Auto-detect ComfyUI output directory if still none
        if not output_directories:
            output_directories = self.detect_comfyui_output_dirs()

        if not output_directories:
            self.logger.warning("No output directories found to monitor")
            return

        # Create event handler
        self.handler = ImageGenerationHandler(self.db_manager, self.prompt_tracker)

        # Start observer
        self.observer = Observer()

        for output_dir in output_directories:
            if os.path.exists(output_dir):
                self.observer.schedule(self.handler, output_dir, recursive=True)
                self.monitored_directories.append(output_dir)
                self.logger.info(f"Monitoring directory (recursive): {output_dir}")
            else:
                self.logger.warning(f"Directory does not exist: {output_dir}")

        if self.monitored_directories:
            self.observer.start()
            self.logger.info(
                f"Image monitoring started for {len(self.monitored_directories)} directories"
            )
        else:
            self.logger.warning("No valid directories to monitor")

    def stop_monitoring(self):
        """Stop the image monitoring system.

        Cleanly shuts down the filesystem watcher and clears all monitoring state.
        This method should be called before program exit to ensure proper cleanup.
        """
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None
            self.handler = None
            self.monitored_directories = []
            self.logger.debug("Image monitoring stopped")

    def detect_comfyui_output_dirs(self) -> list:
        """Auto-detect ComfyUI output directories.

        Attempts to locate ComfyUI output directories using multiple strategies:
        1. Import ComfyUI's folder_paths module to get the configured output directory
        2. Search common relative paths where ComfyUI output directories are typically located
        3. Verify that detected directories actually exist

        Returns:
            List of absolute paths to detected output directories
        """
        potential_dirs = []

        try:
            # Try to import ComfyUI's folder_paths
            import folder_paths

            output_dir = folder_paths.get_output_directory()
            if output_dir and os.path.exists(output_dir):
                potential_dirs.append(output_dir)
                self.logger.debug(f"Detected ComfyUI output directory: {output_dir}")
        except ImportError:
            self.logger.debug(
                "ComfyUI folder_paths not available, using fallback detection"
            )

        # Fallback: Look for common ComfyUI directory structures
        fallback_paths = [
            "output",
            "../output",
            "../../output",
            "ComfyUI/output",
            "../ComfyUI/output",
        ]

        for path in fallback_paths:
            abs_path = os.path.abspath(path)
            if os.path.exists(abs_path) and abs_path not in potential_dirs:
                potential_dirs.append(abs_path)
                self.logger.debug(f"Found output directory: {abs_path}")

        return potential_dirs

    def get_status(self) -> Dict[str, Any]:
        """Get monitoring status information.

        Returns:
            Dictionary containing:
            - running: Boolean indicating if monitoring is active
            - monitored_directories: List of currently monitored directory paths
            - handler_active: Boolean indicating if the event handler is active
            - observer_alive: Boolean indicating if observer thread is alive
        """
        observer_alive = False
        if self.observer is not None:
            try:
                observer_alive = self.observer.is_alive()
            except Exception:
                pass
        return {
            "running": self.observer is not None,
            "monitored_directories": self.monitored_directories,
            "handler_active": self.handler is not None,
            "observer_alive": observer_alive,
        }


# Singleton instance management
_monitor_instance: Optional[ImageMonitor] = None
_monitor_lock = threading.Lock()


def get_image_monitor(db_manager, prompt_tracker) -> ImageMonitor:
    """Get or create the singleton ImageMonitor instance.

    This ensures only one ImageMonitor exists across all PromptManager nodes,
    preventing duplicate image detection when multiple nodes are used.

    Args:
        db_manager: Database manager instance for storing image relationships
        prompt_tracker: Prompt tracking instance for getting active prompt context

    Returns:
        The singleton ImageMonitor instance
    """
    global _monitor_instance
    if _monitor_instance is None:
        with _monitor_lock:
            if _monitor_instance is None:
                _monitor_instance = ImageMonitor(db_manager, prompt_tracker)
    return _monitor_instance
