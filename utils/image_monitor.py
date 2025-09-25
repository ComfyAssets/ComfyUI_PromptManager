"""Image monitoring system for automatic gallery updates.

Watches ComfyUI output directories for new images and automatically
processes them with metadata extraction and database insertion.
"""

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent

from src.core.database import ImageRepository, PromptRepository
from src.metadata.extractor import MetadataExtractor
from src.galleries.image_gallery import ImageGallery


class ImageMonitor(FileSystemEventHandler):
    """Monitor directories for new image files.
    
    Automatically detects new images, extracts metadata,
    creates database records, and triggers gallery updates.
    """

    def __init__(
        self, 
        output_dir: str,
        db_path: str = "prompts.db",
        auto_extract: bool = True,
        create_thumbnails: bool = True
    ):
        """Initialize image monitor.
        
        Args:
            output_dir: Directory to monitor for new images
            db_path: Path to database file
            auto_extract: Whether to automatically extract metadata
            create_thumbnails: Whether to create thumbnails for new images
        """
        super().__init__()
        self.output_dir = Path(output_dir)
        self.db_path = db_path
        self.auto_extract = auto_extract
        self.create_thumbnails = create_thumbnails
        
        # Initialize components
        self.image_repo = ImageRepository(db_path)
        self.prompt_repo = PromptRepository(db_path)
        self.metadata_extractor = MetadataExtractor()
        self.gallery = ImageGallery(db_path)
        
        # File tracking
        self.processed_files: Set[str] = set()
        self.pending_files: Dict[str, float] = {}  # file -> timestamp
        self.supported_formats = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        
        # Callbacks
        self.on_new_image: Optional[Callable] = None
        self.on_metadata_extracted: Optional[Callable] = None
        self.on_error: Optional[Callable] = None
        
        # Observer for file system events
        self.observer: Optional[Observer] = None
        
        # Processing settings
        self.debounce_seconds = 2.0  # Wait for file to stabilize
        self.batch_size = 10  # Process in batches
        
        # Logger
        import logging
        self.logger = logging.getLogger("promptmanager.monitor")
        
        # Load existing files to avoid reprocessing
        self._load_existing_files()

    def _load_existing_files(self) -> None:
        """Load existing files from database to avoid reprocessing."""
        try:
            existing_images = self.image_repo.list()
            for image in existing_images:
                self.processed_files.add(image["filename"])
            
            self.logger.info(f"Loaded {len(self.processed_files)} existing files")
        except Exception as e:
            self.logger.error(f"Error loading existing files: {e}")

    def start(self) -> None:
        """Start monitoring the output directory."""
        if not self.output_dir.exists():
            self.logger.error(f"Output directory does not exist: {self.output_dir}")
            return
        
        # Create observer
        self.observer = Observer()
        self.observer.schedule(self, str(self.output_dir), recursive=False)
        self.observer.start()
        
        # Start processing loop
        asyncio.create_task(self._process_pending_loop())
        
        self.logger.info(f"Started monitoring: {self.output_dir}")

    def stop(self) -> None:
        """Stop monitoring."""
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None
        
        self.logger.info("Stopped monitoring")

    def on_created(self, event: FileCreatedEvent) -> None:
        """Handle file creation events.
        
        Args:
            event: File system event
        """
        if event.is_directory:
            return
        
        file_path = Path(event.src_path)
        
        # Check if it's a supported image format
        if file_path.suffix.lower() not in self.supported_formats:
            return
        
        # Skip if already processed
        if file_path.name in self.processed_files:
            return
        
        # Add to pending with timestamp
        self.pending_files[str(file_path)] = time.time()
        self.logger.debug(f"Detected new file: {file_path.name}")

    async def _process_pending_loop(self) -> None:
        """Process pending files after debounce period."""
        while True:
            try:
                await asyncio.sleep(1)  # Check every second
                
                # Find files ready to process (stable for debounce period)
                current_time = time.time()
                ready_files = []
                
                for file_path, timestamp in list(self.pending_files.items()):
                    if current_time - timestamp >= self.debounce_seconds:
                        ready_files.append(file_path)
                
                # Process ready files in batches
                if ready_files:
                    for i in range(0, len(ready_files), self.batch_size):
                        batch = ready_files[i:i + self.batch_size]
                        await self._process_batch(batch)
                    
                    # Remove processed files from pending
                    for file_path in ready_files:
                        del self.pending_files[file_path]
                        
            except Exception as e:
                self.logger.error(f"Error in processing loop: {e}")
                if self.on_error:
                    self.on_error(e)

    async def _process_batch(self, file_paths: List[str]) -> None:
        """Process a batch of image files.
        
        Args:
            file_paths: List of file paths to process
        """
        for file_path_str in file_paths:
            try:
                file_path = Path(file_path_str)
                
                # Skip if file no longer exists
                if not file_path.exists():
                    continue
                
                # Skip if already processed
                if file_path.name in self.processed_files:
                    continue
                
                # Process the image
                await self._process_image(file_path)
                
                # Mark as processed
                self.processed_files.add(file_path.name)
                
            except Exception as e:
                self.logger.error(f"Error processing {file_path_str}: {e}")
                if self.on_error:
                    self.on_error(e)

    async def _process_image(self, file_path: Path) -> None:
        """Process a single image file.
        
        Args:
            file_path: Path to image file
        """
        self.logger.info(f"Processing image: {file_path.name}")
        
        # Extract basic file info
        stat = file_path.stat()
        image_data = {
            "image_path": str(file_path.absolute()),
            "filename": file_path.name,
            "file_size": stat.st_size,
            "generation_time": datetime.fromtimestamp(stat.st_mtime).isoformat()
        }
        
        # Extract image dimensions
        try:
            from PIL import Image
            with Image.open(file_path) as img:
                image_data["width"] = img.width
                image_data["height"] = img.height
                image_data["format"] = img.format or file_path.suffix[1:].upper()
        except Exception as e:
            self.logger.warning(f"Could not extract image info: {e}")
        
        # Extract metadata if enabled
        if self.auto_extract:
            try:
                metadata = self.metadata_extractor.extract_from_file(str(file_path))
                
                if metadata:
                    # Store workflow data
                    if "workflow" in metadata:
                        image_data["workflow_data"] = metadata.get("workflow", {})
                    
                    # Store prompt metadata
                    prompt_meta = {}
                    for key in ["prompt", "negative_prompt", "steps", "sampler", 
                               "cfg_scale", "seed", "model"]:
                        if key in metadata:
                            prompt_meta[key] = metadata[key]
                    
                    if prompt_meta:
                        image_data["prompt_metadata"] = prompt_meta
                    
                    # Store other parameters
                    image_data["parameters"] = {
                        k: v for k, v in metadata.items() 
                        if k not in ["workflow", "prompt", "negative_prompt"]
                    }
                    
                    # Try to link to prompt in database
                    if "prompt" in metadata:
                        prompt_text = metadata["prompt"]
                        prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
                        
                        # Find or create prompt
                        existing_prompt = self.prompt_repo.find_by_hash(prompt_hash)
                        if existing_prompt:
                            image_data["prompt_id"] = existing_prompt["id"]
                        else:
                            # Create new prompt record
                            prompt_id = self.prompt_repo.create({
                                "text": prompt_text,
                                "metadata": prompt_meta
                            })
                            image_data["prompt_id"] = prompt_id
                    
                    # Trigger metadata callback
                    if self.on_metadata_extracted:
                        self.on_metadata_extracted(file_path.name, metadata)
                        
            except Exception as e:
                self.logger.warning(f"Could not extract metadata: {e}")
        
        # Create thumbnail if enabled
        if self.create_thumbnails:
            try:
                thumbnail_path = self.gallery.create_thumbnail(str(file_path))
                if thumbnail_path:
                    image_data["thumbnail_path"] = thumbnail_path
            except Exception as e:
                self.logger.warning(f"Could not create thumbnail: {e}")
        
        # Save to database
        try:
            image_id = self.image_repo.create(image_data)
            self.logger.info(f"Added image to database with ID: {image_id}")
            
            # Trigger new image callback
            if self.on_new_image:
                self.on_new_image(image_id, image_data)
                
        except Exception as e:
            self.logger.error(f"Failed to save image to database: {e}")
            raise

    def scan_existing(self) -> int:
        """Scan directory for existing images not in database.
        
        Returns:
            Number of new images found and processed
        """
        if not self.output_dir.exists():
            self.logger.error(f"Output directory does not exist: {self.output_dir}")
            return 0
        
        new_count = 0
        
        for file_path in self.output_dir.iterdir():
            if file_path.is_file() and file_path.suffix.lower() in self.supported_formats:
                if file_path.name not in self.processed_files:
                    try:
                        # Process synchronously for scanning
                        asyncio.run(self._process_image(file_path))
                        self.processed_files.add(file_path.name)
                        new_count += 1
                    except Exception as e:
                        self.logger.error(f"Error processing {file_path}: {e}")
        
        self.logger.info(f"Scan complete: found {new_count} new images")
        return new_count

    def set_callback(self, event_type: str, callback: Callable) -> None:
        """Set callback for specific events.
        
        Args:
            event_type: Type of event ("new_image", "metadata_extracted", "error")
            callback: Callback function
        """
        if event_type == "new_image":
            self.on_new_image = callback
        elif event_type == "metadata_extracted":
            self.on_metadata_extracted = callback
        elif event_type == "error":
            self.on_error = callback
        else:
            raise ValueError(f"Unknown event type: {event_type}")

    def get_statistics(self) -> Dict[str, Any]:
        """Get monitoring statistics.
        
        Returns:
            Dictionary with monitoring stats
        """
        return {
            "output_directory": str(self.output_dir),
            "monitoring_active": self.observer is not None and self.observer.is_alive(),
            "processed_files": len(self.processed_files),
            "pending_files": len(self.pending_files),
            "auto_extract": self.auto_extract,
            "create_thumbnails": self.create_thumbnails,
            "debounce_seconds": self.debounce_seconds,
            "batch_size": self.batch_size
        }


class ComfyUIMonitor(ImageMonitor):
    """Specialized monitor for ComfyUI output integration.
    
    Extends ImageMonitor with ComfyUI-specific features like
    workflow tracking and real-time updates via WebSocket.
    """

    def __init__(
        self,
        comfyui_output_dir: str = None,
        db_path: str = "prompts.db"
    ):
        """Initialize ComfyUI monitor.
        
        Args:
            comfyui_output_dir: ComfyUI output directory (auto-detect if None)
            db_path: Path to database file
        """
        # Auto-detect ComfyUI output directory if not provided
        if not comfyui_output_dir:
            comfyui_output_dir = self._find_comfyui_output_dir()
        
        super().__init__(
            output_dir=comfyui_output_dir,
            db_path=db_path,
            auto_extract=True,
            create_thumbnails=True
        )
        
        # ComfyUI specific settings
        self.workflow_cache: Dict[str, Any] = {}
        self.enable_websocket = True
        self.websocket_clients: List[Any] = []

    def _find_comfyui_output_dir(self) -> str:
        """Auto-detect ComfyUI output directory.
        
        Returns:
            Path to ComfyUI output directory
        """
        # Common ComfyUI output locations
        possible_paths = [
            Path("output"),
            Path("../output"),
            Path("../../output"),
            Path.home() / "ComfyUI" / "output",
            Path("/workspace/ComfyUI/output"),
        ]
        
        for path in possible_paths:
            if path.exists() and path.is_dir():
                self.logger.info(f"Found ComfyUI output directory: {path}")
                return str(path.absolute())
        
        # Default to current directory's output folder
        default = Path("output")
        default.mkdir(exist_ok=True)
        return str(default.absolute())

    async def notify_clients(self, event_type: str, data: Any) -> None:
        """Notify WebSocket clients of events.
        
        Args:
            event_type: Type of event
            data: Event data
        """
        if not self.enable_websocket:
            return
        
        message = json.dumps({
            "type": event_type,
            "data": data,
            "timestamp": datetime.now().isoformat()
        })
        
        # Send to all connected clients
        for client in self.websocket_clients[:]:
            try:
                await client.send_str(message)
            except Exception as e:
                self.logger.warning(f"Failed to send to client: {e}")
                self.websocket_clients.remove(client)

    def add_websocket_client(self, ws) -> None:
        """Add WebSocket client for notifications.
        
        Args:
            ws: WebSocket connection
        """
        self.websocket_clients.append(ws)
        self.logger.debug(f"Added WebSocket client, total: {len(self.websocket_clients)}")

    def remove_websocket_client(self, ws) -> None:
        """Remove WebSocket client.
        
        Args:
            ws: WebSocket connection
        """
        if ws in self.websocket_clients:
            self.websocket_clients.remove(ws)
            self.logger.debug(f"Removed WebSocket client, total: {len(self.websocket_clients)}")

    async def _process_image(self, file_path: Path) -> None:
        """Process image with ComfyUI-specific handling.
        
        Args:
            file_path: Path to image file
        """
        # Call parent processing
        await super()._process_image(file_path)
        
        # Notify WebSocket clients
        await self.notify_clients("new_image", {
            "filename": file_path.name,
            "path": str(file_path)
        })