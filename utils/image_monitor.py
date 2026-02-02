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
        self.processing_delay = 2.0  # Wait 2 seconds before processing
        self.logger = get_logger('prompt_manager.image_monitor')
        
    def on_created(self, event):
        """Handle filesystem creation events.
        
        This method is called by watchdog when a new file is created in a monitored
        directory. It filters for image files and schedules them for processing after
        a small delay to ensure the file is fully written.
        
        Args:
            event: FileSystemEvent object containing event details
        """
        if not event.is_directory and self.is_image_file(event.src_path):
            self.logger.info(f"New image detected: {event.src_path}")
            # Small delay to ensure file is fully written
            threading.Timer(
                self.processing_delay,
                self.process_new_image,
                args=[event.src_path]
            ).start()
    
    def is_image_file(self, filepath: str) -> bool:
        """Check if file is a supported image format.
        
        Args:
            filepath: Path to the file to check
            
        Returns:
            True if the file has a supported image extension, False otherwise
        """
        return filepath.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif'))
    
    def process_new_image(self, image_path: str):
        """Process a newly created image file for gallery integration.
        
        This method handles the complete processing pipeline for a new image:
        1. Verifies the file still exists
        2. Gets the current prompt context from the tracker
        3. Extracts ComfyUI metadata from the image
        4. Links the image to the appropriate prompt in the database
        5. Handles fallback scenarios when no active prompt is available
        
        Args:
            image_path: Full path to the newly created image file
        """
        try:
            self.logger.info(f"Processing image: {image_path}")

            if not os.path.exists(image_path):
                self.logger.warning(f"Image file no longer exists: {image_path}")
                return

            # Get current prompt context first
            current_prompt = self.prompt_tracker.get_current_prompt()
            self.logger.info(f"Current prompt context: {current_prompt['id'] if current_prompt else 'None'}")
            
            if not current_prompt:
                self.logger.debug(f"No active prompt context for image: {image_path}")
                # Fallback: try to link to the most recent prompt in database
                current_prompt = self._get_fallback_prompt()
                if current_prompt:
                    self.logger.debug(f"Using fallback prompt: {current_prompt['id']}")
                else:
                    self.logger.warning(f"No fallback prompt available, skipping image")
                    return
            else:
                # Extend the timeout for this prompt since we're still getting images
                if 'execution_id' in current_prompt:
                    self.prompt_tracker.extend_prompt_timeout(current_prompt['execution_id'], 300)  # Add 5 more minutes
            
            # Extract ComfyUI metadata
            try:
                metadata = self.metadata_extractor.extract_metadata(image_path)
                self.logger.debug(f"Extracted metadata: {bool(metadata)}")
            except Exception as meta_error:
                self.logger.warning(f"Metadata extraction failed: {meta_error}")
                metadata = None
            
            if metadata:
                self.logger.debug(f"Linking image with full metadata to prompt {current_prompt['id']}")
                self.link_image_to_prompt(image_path, current_prompt, metadata)
            else:
                self.logger.debug(f"Linking image with basic info to prompt {current_prompt['id']}")
                # Link with basic file info even without metadata
                basic_metadata = self.get_basic_file_info(image_path)
                self.link_image_to_prompt(image_path, current_prompt, {'file_info': basic_metadata})
                
        except Exception as e:
            self.logger.error(f"Error processing image {image_path}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
    
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
            file_info = {
                'size': stat.st_size,
                'format': None,
                'dimensions': None
            }
            
            # Try to get image dimensions
            try:
                with Image.open(image_path) as img:
                    file_info['dimensions'] = list(img.size)
                    file_info['format'] = img.format
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
                    'id': prompt['id'],
                    'text': prompt['text'],
                    'timestamp': prompt.get('created_at'),
                    'fallback': True
                }
        except Exception as e:
            self.logger.error(f"Error getting fallback prompt: {e}")
        return None
    
    def link_image_to_prompt(self, image_path: str, prompt_context: Dict, metadata: Dict):
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
                prompt_id=prompt_context['id'],
                image_path=image_path,
                metadata=metadata
            )
            fallback_note = " (fallback)" if prompt_context.get('fallback') else ""
            self.logger.debug(f"Successfully linked image {image_id} to prompt {prompt_context['id']}{fallback_note}")
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
        self.logger = get_logger('prompt_manager.image_monitor')
        
    def start_monitoring(self, output_directories: Optional[list] = None):
        """
        Start monitoring ComfyUI output directories for new images.
        
        Begins filesystem watching on the specified directories. If no directories
        are provided, the system will attempt to auto-detect ComfyUI output locations.
        All monitoring is done recursively to catch images in subdirectories.
        
        Args:
            output_directories: List of directory paths to monitor. If None, uses auto-detection.
        """
        if self.observer:
            self.logger.warning("Image monitoring already running")
            return
        
        # Auto-detect ComfyUI output directory if none provided
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
            self.logger.info(f"Image monitoring started for {len(self.monitored_directories)} directories")
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
            self.logger.debug("ComfyUI folder_paths not available, using fallback detection")
        
        # Fallback: Look for common ComfyUI directory structures
        fallback_paths = [
            "output",
            "../output", 
            "../../output",
            "ComfyUI/output",
            "../ComfyUI/output"
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
            'running': self.observer is not None,
            'monitored_directories': self.monitored_directories,
            'handler_active': self.handler is not None,
            'observer_alive': observer_alive
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