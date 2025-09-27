"""
ComfyUI Output Monitor Service
Monitors ComfyUI output directory for new images and extracts metadata
"""

import os
import time
import json
import threading
from pathlib import Path
from typing import Optional, Set
from datetime import datetime, timedelta


class ComfyUIMonitor:
    """
    Monitors ComfyUI output directory and automatically extracts metadata
    from new images to populate the database.
    """

    def __init__(self, output_dir: str | Path, db_path: str | Path,
                 poll_interval: int = 5):
        """
        Initialize the ComfyUI monitor.

        Args:
            output_dir: ComfyUI output directory to monitor
            db_path: Path to PromptManager database
            poll_interval: Seconds between directory scans
        """
        self.output_dir = Path(output_dir)
        self.db_path = str(db_path)
        self.poll_interval = poll_interval
        self.processed_files: Set[str] = set()
        self.running = False
        self.thread = None
        self.processing_lock = threading.Lock()  # Add lock for thread safety

        # Import extractor
        from .workflow_metadata_extractor import WorkflowMetadataExtractor
        self.extractor = WorkflowMetadataExtractor(db_path)

        # Load previously processed files from database
        self._load_processed_files()

    def _load_processed_files(self):
        """Load list of already processed files from database."""
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path)

            # Enable WAL mode for better concurrency
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")

            cursor = conn.cursor()

            # Get all filenames already in database
            cursor.execute("SELECT filename FROM generated_images")
            self.processed_files = {row[0] for row in cursor.fetchall()}

            cursor.close()
            conn.close()

            print(f"[ComfyUIMonitor] Loaded {len(self.processed_files)} processed files")

        except Exception as e:
            print(f"[ComfyUIMonitor] Error loading processed files: {e}")

    def start(self):
        """Start monitoring in background thread."""
        if self.running:
            return

        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        print(f"[ComfyUIMonitor] Started monitoring {self.output_dir}")

    def stop(self):
        """Stop monitoring."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        print("[ComfyUIMonitor] Stopped monitoring")

    def _monitor_loop(self):
        """Main monitoring loop running in background thread."""
        while self.running:
            try:
                self._scan_directory()
            except Exception as e:
                print(f"[ComfyUIMonitor] Error in monitor loop: {e}")

            time.sleep(self.poll_interval)

    def _scan_directory(self):
        """Scan output directory for new images."""
        if not self.output_dir.exists():
            return

        # Look for PNG files (ComfyUI default output format)
        for png_path in self.output_dir.glob("*.png"):
            filename = png_path.name

            # Skip if already processed
            if filename in self.processed_files:
                continue

            # Skip if file is too recent (might still be writing)
            if self._is_file_writing(png_path):
                continue

            # Process the new image
            if self._process_image(png_path):
                self.processed_files.add(filename)

    def _is_file_writing(self, file_path: Path, wait_seconds: int = 2) -> bool:
        """
        Check if file is still being written to.

        Args:
            file_path: Path to file
            wait_seconds: Consider file done if not modified for this many seconds

        Returns:
            True if file might still be writing
        """
        try:
            mtime = file_path.stat().st_mtime
            current_time = time.time()
            return (current_time - mtime) < wait_seconds
        except:
            return True

    def _process_image(self, image_path: Path) -> bool:
        """
        Process a single image file with thread-safe database access.

        Args:
            image_path: Path to PNG image

        Returns:
            True if processed successfully
        """
        # Use lock to prevent database conflicts
        with self.processing_lock:
            try:
                print(f"[ComfyUIMonitor] Processing new image: {image_path.name}")

                # Extract metadata from PNG
                params = self.extractor.extract_from_png_metadata(str(image_path))

                if not params or not params.get("positive"):
                    print(f"[ComfyUIMonitor] No metadata found in {image_path.name}")
                    return False

                # Save to database with retry logic for lock errors
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        success = self.extractor.save_to_database(params, str(image_path))
                        if success:
                            print(f"[ComfyUIMonitor] ✅ Saved metadata for {image_path.name}")
                        return success
                    except Exception as e:
                        if "database is locked" in str(e) and attempt < max_retries - 1:
                            time.sleep(0.5)  # Wait before retry
                            continue
                        raise

            except Exception as e:
                if "database is locked" not in str(e):  # Don't spam lock errors
                    print(f"[ComfyUIMonitor] Error processing {image_path.name}: {e}")
                return False

    def process_existing_images(self, max_age_days: int = 30) -> int:
        """
        Process existing images in output directory.

        Args:
            max_age_days: Only process images newer than this many days

        Returns:
            Number of images processed
        """
        if not self.output_dir.exists():
            return 0

        processed = 0
        cutoff_time = datetime.now() - timedelta(days=max_age_days)

        for png_path in self.output_dir.glob("*.png"):
            # Skip if already processed
            if png_path.name in self.processed_files:
                continue

            # Check age
            try:
                mtime = datetime.fromtimestamp(png_path.stat().st_mtime)
                if mtime < cutoff_time:
                    continue
            except:
                continue

            # Process the image
            if self._process_image(png_path):
                self.processed_files.add(png_path.name)
                processed += 1

        return processed


class ComfyUIMonitorService:
    """
    Singleton service for managing ComfyUI monitoring.
    """

    _instance = None
    _monitor: Optional[ComfyUIMonitor] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def initialize(self, output_dir: str, db_path: str):
        """
        Initialize the monitor service.

        Args:
            output_dir: ComfyUI output directory
            db_path: PromptManager database path
        """
        if self._monitor is None:
            self._monitor = ComfyUIMonitor(output_dir, db_path)

            # Start monitoring first (don't process existing during startup)
            self._monitor.start()

            # Schedule processing of existing images for later (after startup)
            def process_existing_later():
                time.sleep(10)  # Wait 10 seconds after startup
                print("[ComfyUIMonitor] Processing existing images...")
                count = self._monitor.process_existing_images(max_age_days=1)  # Only process last day
                if count > 0:
                    print(f"[ComfyUIMonitor] Processed {count} existing images")

            # Run in background thread
            threading.Thread(target=process_existing_later, daemon=True).start()

    def stop(self):
        """Stop the monitor service."""
        if self._monitor:
            self._monitor.stop()
            self._monitor = None

    @property
    def is_running(self) -> bool:
        """Check if monitor is running."""
        return self._monitor is not None and self._monitor.running


# Global service instance
_monitor_service = ComfyUIMonitorService()


def start_comfyui_monitor(output_dir: str, db_path: str):
    """
    Start the ComfyUI output monitor service.

    Args:
        output_dir: ComfyUI output directory (usually 'ComfyUI/output')
        db_path: Path to PromptManager database
    """
    _monitor_service.initialize(output_dir, db_path)


def stop_comfyui_monitor():
    """Stop the ComfyUI output monitor service."""
    _monitor_service.stop()


def is_monitor_running() -> bool:
    """Check if monitor is running."""
    return _monitor_service.is_running