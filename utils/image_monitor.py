"""
Image monitoring system for ComfyUI generated images.
Automatically detects new images and links them to prompts.
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


class ImageGenerationHandler(FileSystemEventHandler):
    """Handler for new image file creation events."""
    
    def __init__(self, db_manager, prompt_tracker):
        """
        Initialize the image handler.
        
        Args:
            db_manager: Database manager instance
            prompt_tracker: Prompt tracking instance
        """
        self.db_manager = db_manager
        self.prompt_tracker = prompt_tracker
        self.metadata_extractor = ComfyUIMetadataExtractor()
        self.processing_delay = 2.0  # Wait 2 seconds before processing
        
    def on_created(self, event):
        """Handle file creation events."""
        if not event.is_directory and self.is_image_file(event.src_path):
            print(f"[PromptManager] New image detected: {event.src_path}")
            # Small delay to ensure file is fully written
            threading.Timer(
                self.processing_delay, 
                self.process_new_image, 
                args=[event.src_path]
            ).start()
    
    def is_image_file(self, filepath: str) -> bool:
        """Check if file is a supported image format."""
        return filepath.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif'))
    
    def process_new_image(self, image_path: str):
        """Process a newly created image file."""
        try:
            print(f"[PromptManager] Processing image: {image_path}")
            
            if not os.path.exists(image_path):
                print(f"[PromptManager] Image file no longer exists: {image_path}")
                return
            
            # Get current prompt context first
            current_prompt = self.prompt_tracker.get_current_prompt()
            print(f"[PromptManager] Current prompt context: {current_prompt['id'] if current_prompt else 'None'}")
            
            if not current_prompt:
                print(f"[PromptManager] No active prompt context for image: {image_path}")
                # Fallback: try to link to the most recent prompt in database
                current_prompt = self._get_fallback_prompt()
                if current_prompt:
                    print(f"[PromptManager] Using fallback prompt: {current_prompt['id']}")
                else:
                    print(f"[PromptManager] No fallback prompt available, skipping image")
                    return
            else:
                # Extend the timeout for this prompt since we're still getting images
                if 'execution_id' in current_prompt:
                    self.prompt_tracker.extend_prompt_timeout(current_prompt['execution_id'], 300)  # Add 5 more minutes
            
            # Extract ComfyUI metadata
            try:
                metadata = self.metadata_extractor.extract_metadata(image_path)
                print(f"[PromptManager] Extracted metadata: {bool(metadata)}")
            except Exception as meta_error:
                print(f"[PromptManager] Metadata extraction failed: {meta_error}")
                metadata = None
            
            if metadata:
                print(f"[PromptManager] Linking image with full metadata to prompt {current_prompt['id']}")
                self.link_image_to_prompt(image_path, current_prompt, metadata)
            else:
                print(f"[PromptManager] Linking image with basic info to prompt {current_prompt['id']}")
                # Link with basic file info even without metadata
                basic_metadata = self.get_basic_file_info(image_path)
                self.link_image_to_prompt(image_path, current_prompt, {'file_info': basic_metadata})
                
        except Exception as e:
            print(f"[PromptManager] Error processing image {image_path}: {e}")
            import traceback
            traceback.print_exc()
    
    def get_basic_file_info(self, image_path: str) -> Dict[str, Any]:
        """Get basic file information when metadata extraction fails."""
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
            print(f"[PromptManager] Error getting file info: {e}")
            return {}
    
    def _get_fallback_prompt(self) -> Optional[Dict[str, Any]]:
        """Get the most recent prompt from database as fallback."""
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
            print(f"[PromptManager] Error getting fallback prompt: {e}")
        return None
    
    def link_image_to_prompt(self, image_path: str, prompt_context: Dict, metadata: Dict):
        """Link an image to a prompt in the database."""
        try:
            image_id = self.db_manager.link_image_to_prompt(
                prompt_id=prompt_context['id'],
                image_path=image_path,
                metadata=metadata
            )
            fallback_note = " (fallback)" if prompt_context.get('fallback') else ""
            print(f"[PromptManager] Successfully linked image {image_id} to prompt {prompt_context['id']}{fallback_note}")
        except Exception as e:
            print(f"[PromptManager] Failed to link image to prompt: {e}")


class ImageMonitor:
    """Main image monitoring system."""
    
    def __init__(self, db_manager, prompt_tracker):
        """
        Initialize the image monitor.
        
        Args:
            db_manager: Database manager instance
            prompt_tracker: Prompt tracking instance
        """
        self.db_manager = db_manager
        self.prompt_tracker = prompt_tracker
        self.observer = None
        self.handler = None
        self.monitored_directories = []
        
    def start_monitoring(self, output_directories: Optional[list] = None):
        """
        Start monitoring ComfyUI output directories.
        
        Args:
            output_directories: List of directories to monitor. If None, auto-detect.
        """
        if self.observer:
            print("[PromptManager] Image monitoring already running")
            return
        
        # Auto-detect ComfyUI output directory if none provided
        if not output_directories:
            output_directories = self.detect_comfyui_output_dirs()
        
        if not output_directories:
            print("[PromptManager] No output directories found to monitor")
            return
        
        # Create event handler
        self.handler = ImageGenerationHandler(self.db_manager, self.prompt_tracker)
        
        # Start observer
        self.observer = Observer()
        
        for output_dir in output_directories:
            if os.path.exists(output_dir):
                self.observer.schedule(self.handler, output_dir, recursive=True)
                self.monitored_directories.append(output_dir)
                print(f"[PromptManager] Monitoring directory: {output_dir}")
            else:
                print(f"[PromptManager] Directory does not exist: {output_dir}")
        
        if self.monitored_directories:
            self.observer.start()
            print(f"[PromptManager] Image monitoring started for {len(self.monitored_directories)} directories")
        else:
            print("[PromptManager] No valid directories to monitor")
    
    def stop_monitoring(self):
        """Stop the image monitoring system."""
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None
            self.handler = None
            self.monitored_directories = []
            print("[PromptManager] Image monitoring stopped")
    
    def detect_comfyui_output_dirs(self) -> list:
        """Auto-detect ComfyUI output directories."""
        potential_dirs = []
        
        try:
            # Try to import ComfyUI's folder_paths
            import folder_paths
            output_dir = folder_paths.get_output_directory()
            if output_dir and os.path.exists(output_dir):
                potential_dirs.append(output_dir)
                print(f"[PromptManager] Detected ComfyUI output directory: {output_dir}")
        except ImportError:
            print("[PromptManager] ComfyUI folder_paths not available, using fallback detection")
        
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
                print(f"[PromptManager] Found output directory: {abs_path}")
        
        return potential_dirs
    
    def get_status(self) -> Dict[str, Any]:
        """Get monitoring status information."""
        return {
            'running': self.observer is not None,
            'monitored_directories': self.monitored_directories,
            'handler_active': self.handler is not None
        }