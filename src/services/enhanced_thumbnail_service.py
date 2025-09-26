"""Enhanced thumbnail service with batch processing and progress tracking.

This module extends the existing ThumbnailGenerator to provide:
- Batch thumbnail generation with progress tracking
- Video thumbnail support via ffmpeg
- Missing thumbnail detection
- Database integration for tracking
- Multi-format support
"""

import asyncio
import hashlib
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Callable
from enum import Enum

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = None
# import aiofiles  # Optional dependency - using sync file operations for now

# Import from utils directly (it's in sys.path)
from utils.image_processing import ThumbnailGenerator, ImageProcessor

from utils.logging import get_logger
from src.config import config
from utils.cache import CacheManager

try:
    # Ensure parent directory is in path
    import sys
    from pathlib import Path
    parent_dir = Path(__file__).parent.parent.parent
    if str(parent_dir) not in sys.path:
        sys.path.insert(0, str(parent_dir))
    
    from src.database import PromptDatabase as Database
except ImportError:
    # Use the alternative import method if direct import fails
    import importlib.util
    import sys
    from pathlib import Path

    def _load_database_module():
        """Safely import the database module."""
        module_name = "promptmanager_database_operations"
        if module_name in sys.modules:
            return sys.modules[module_name]

        module_path = Path(__file__).resolve().parent.parent.parent / "database" / "operations.py"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        sys.modules[module_name] = module
        return module

    database_module = _load_database_module()
    Database = database_module.PromptDatabase

logger = get_logger("promptmanager.enhanced_thumbnail_service")


class ThumbnailStatus(Enum):
    """Thumbnail generation status."""
    PENDING = "pending"
    GENERATING = "generating"
    GENERATED = "generated"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ThumbnailTask:
    """Individual thumbnail generation task."""
    image_id: str
    source_path: Path
    thumbnail_path: Path
    size: Tuple[int, int]
    format: str
    is_video: bool = False
    status: ThumbnailStatus = ThumbnailStatus.PENDING
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    file_size: Optional[int] = None


@dataclass
class ThumbnailProgress:
    """Progress tracking for batch operations."""
    total: int
    completed: int
    failed: int
    skipped: int
    current_file: Optional[str] = None
    percentage: float = 0.0
    estimated_time_remaining: Optional[int] = None
    errors: List[Dict[str, Any]] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        self.update_percentage()

    def update_percentage(self):
        """Update completion percentage."""
        if self.total > 0:
            self.percentage = ((self.completed + self.failed + self.skipped) / self.total) * 100

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        processed = self.completed + self.failed + self.skipped
        return {
            'total': self.total,
            'completed': self.completed,
            'failed': self.failed,
            'skipped': self.skipped,
            'processed': processed,
            'current_file': self.current_file,
            'percentage': round(self.percentage, 2),
            'estimated_time_remaining': self.estimated_time_remaining,
            'errors': self.errors
        }


class FFmpegHandler:
    """Handle video thumbnail generation using ffmpeg."""

    def __init__(self):
        """Initialize ffmpeg handler."""
        self.ffmpeg_path = self._find_ffmpeg()
        if self.ffmpeg_path:
            logger.info(f"Found ffmpeg at: {self.ffmpeg_path}")
        else:
            logger.warning("ffmpeg not found - video thumbnails will not be generated")
        self.supported_formats = {
            '.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv',
            '.webm', '.m4v', '.mpg', '.mpeg', '.3gp'
        }

    def _find_ffmpeg(self) -> Optional[str]:
        """Find ffmpeg executable path.

        Returns:
            Path to ffmpeg or None if not found
        """
        # Check config first
        if hasattr(config, 'ffmpeg') and config.ffmpeg.path:
            if Path(config.ffmpeg.path).exists():
                return config.ffmpeg.path

        # Try to find in PATH
        ffmpeg = shutil.which('ffmpeg')
        if ffmpeg:
            return ffmpeg

        # Check common locations
        common_paths = [
            '/usr/bin/ffmpeg',
            '/usr/local/bin/ffmpeg',
            'C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe',
            'C:\\ffmpeg\\bin\\ffmpeg.exe'
        ]

        for path in common_paths:
            if Path(path).exists():
                return path

        logger.warning("ffmpeg not found. Video thumbnails will not be generated.")
        return None

    def is_video(self, file_path: Path) -> bool:
        """Check if file is a supported video format.

        Args:
            file_path: Path to check

        Returns:
            True if video format
        """
        return file_path.suffix.lower() in self.supported_formats

    def generate_thumbnail(
        self,
        video_path: Path,
        output_path: Path,
        size: Tuple[int, int],
        timestamp: float = 1.0
    ) -> bool:
        """Generate thumbnail from video.

        Args:
            video_path: Path to video file
            output_path: Path for thumbnail output
            size: Thumbnail size (width, height)
            timestamp: Time in seconds to capture frame

        Returns:
            True if successful
        """
        if not self.ffmpeg_path:
            logger.error("ffmpeg not available for video thumbnail generation")
            return False

        try:
            # Build ffmpeg command
            width, height = size
            vf_filter = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
            )

            cmd = [
                self.ffmpeg_path,
                '-ss', str(timestamp),  # Seek to timestamp
                '-i', str(video_path),  # Input file
                '-vframes', '1',  # Extract one frame
                '-vf', vf_filter,
                '-y',  # Overwrite output
                str(output_path)
            ]

            # Execute ffmpeg
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                logger.error(f"ffmpeg failed: {result.stderr}")
                return False

            return output_path.exists()

        except subprocess.TimeoutExpired:
            logger.error(f"ffmpeg timeout for {video_path}")
            return False
        except Exception as e:
            logger.error(f"ffmpeg error: {e}")
            return False


class EnhancedThumbnailService:
    """Enhanced thumbnail service with batch processing and progress tracking."""

    def __init__(self, db: Database, cache: CacheManager):
        """Initialize enhanced thumbnail service.

        Args:
            db: Database instance
            cache: Cache manager instance
        """
        if not HAS_PIL:
            raise ImportError("PIL/Pillow is required for thumbnail service. Install with: pip install Pillow")

        self.db = db
        self.cache_manager = cache

        # Create or get a specific cache for thumbnails
        from utils.cache import MemoryCache
        self.cache = self.cache_manager.get_cache('thumbnails')
        if not self.cache:
            # Create a memory cache for thumbnail paths
            self.cache = MemoryCache(
                max_size=100 * 1024 * 1024,  # 100MB
                default_ttl=3600  # 1 hour TTL
            )
            self.cache_manager.register_cache('thumbnails', self.cache)
        self.base_generator = ThumbnailGenerator()
        self.ffmpeg = FFmpegHandler()

        # Configure paths
        self.thumbnail_dir = Path(config.storage.base_path) / 'thumbnails'
        self.thumbnail_dir.mkdir(parents=True, exist_ok=True)

        # Create subdirectories for each size
        for size_name in ['small', 'medium', 'large', 'xlarge']:
            size_dir = self.thumbnail_dir / size_name
            size_dir.mkdir(parents=True, exist_ok=True)

        # Progress tracking
        self.current_progress: Optional[ThumbnailProgress] = None
        self.progress_callbacks: List[Callable] = []

        # Thread pool configuration
        self._default_max_workers = 2
        self._max_parallel_config = self._parse_max_parallel(
            config.get('thumbnail.max_parallel', self._default_max_workers)
        )
        self._effective_workers = self._resolve_effective_workers(self._max_parallel_config)

        # Thread pool for parallel processing - sized from configuration
        self.executor = ThreadPoolExecutor(
            max_workers=self._effective_workers,
            thread_name_prefix='PromptManagerThumb'
        )
        if self._effective_workers is None:
            logger.info(
                "Thumbnail generator using unlimited thread pool size (system default)"
            )
        else:
            logger.info(
                "Thumbnail generator configured with %s parallel workers",
                self._effective_workers
            )

        # Supported image formats
        self.image_formats = {
            '.jpg', '.jpeg', '.png', '.gif', '.webp',
            '.bmp', '.tiff', '.tif', '.heic', '.heif'
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_max_parallel(self, value: Any) -> int:
        """Normalize stored configuration for max parallel generation."""
        if value is None:
            return self._default_max_workers

        try:
            parsed = int(value)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid thumbnail.max_parallel value %r; using default %s",
                value,
                self._default_max_workers
            )
            return self._default_max_workers

        return parsed

    def _resolve_effective_workers(self, configured: int) -> Optional[int]:
        """Translate configured parallelism into ThreadPoolExecutor size."""
        if configured <= 0:
            # None signals ThreadPoolExecutor to use its default heuristic
            return None

        # Always enforce at least one worker when positive
        return max(1, configured)

    def set_parallel_workers(self, configured: Any) -> None:
        """Resize the worker pool when settings change."""
        new_config = self._parse_max_parallel(configured)
        new_effective = self._resolve_effective_workers(new_config)

        if (
            new_config == self._max_parallel_config
            and new_effective == self._effective_workers
        ):
            return

        logger.info(
            "Updating thumbnail parallel generation from %s to %s",
            'unlimited' if self._effective_workers is None else self._effective_workers,
            'unlimited' if new_effective is None else new_effective
        )

        # Shutdown existing executor without cancelling in-flight work
        try:
            self.executor.shutdown(wait=False, cancel_futures=False)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to shutdown existing executor cleanly: %s", exc)

        self._max_parallel_config = new_config
        self._effective_workers = new_effective
        self.executor = ThreadPoolExecutor(
            max_workers=self._effective_workers,
            thread_name_prefix='PromptManagerThumb'
        )


    def get_thumbnail_path(
        self,
        source_path: Path,
        size: str = 'medium'
    ) -> Path:
        """Get thumbnail path for given source.

        Args:
            source_path: Source image/video path
            size: Size name (small, medium, large, xlarge)

        Returns:
            Path to thumbnail
        """
        # Generate unique hash for path
        path_hash = hashlib.md5(str(source_path).encode()).hexdigest()

        # Determine output extension
        ext = source_path.suffix.lower()
        if self.ffmpeg.is_video(source_path):
            ext = '.jpg'
        elif ext not in self.image_formats:
            ext = '.jpg'

        # Build thumbnail filename
        filename = f"{path_hash}_{size}{ext}"

        # Return full path
        return self.thumbnail_dir / size / filename

    async def scan_missing_thumbnails(
        self,
        image_paths: List[Path],
        sizes: List[str] = None
    ) -> List[ThumbnailTask]:
        """Scan for images missing thumbnails.

        Args:
            image_paths: List of image paths to check
            sizes: Thumbnail sizes to check (default: all)

        Returns:
            List of thumbnail tasks for missing thumbnails
        """
        if sizes is None:
            sizes = list(self.base_generator.SIZES.keys())

        missing_tasks = []

        for image_path in image_paths:
            if not image_path.exists():
                continue

            # Check if video or image
            is_video = self.ffmpeg.is_video(image_path)

            if not is_video and image_path.suffix.lower() not in self.image_formats:
                continue

            # Check each size
            for size_name in sizes:
                thumbnail_path = self.get_thumbnail_path(image_path, size_name)

                if not thumbnail_path.exists():
                    # Get size dimensions
                    size_dims = self.base_generator.SIZES.get(
                        size_name,
                        (300, 300)
                    )

                    ext_name = 'jpg'
                    if not is_video:
                        ext_name = image_path.suffix.lower().lstrip('.') or 'jpg'

                    # Create task
                    task = ThumbnailTask(
                        image_id=hashlib.md5(str(image_path).encode()).hexdigest(),
                        source_path=image_path,
                        thumbnail_path=thumbnail_path,
                        size=size_dims,
                        format=ext_name,
                        is_video=is_video
                    )
                    missing_tasks.append(task)

        logger.info(f"Found {len(missing_tasks)} missing thumbnails")
        return missing_tasks

    def _generate_single_thumbnail(self, task: ThumbnailTask) -> ThumbnailTask:
        """Generate a single thumbnail.

        Args:
            task: Thumbnail task to process

        Returns:
            Updated task with status
        """
        try:
            task.status = ThumbnailStatus.GENERATING

            # Ensure directory exists
            task.thumbnail_path.parent.mkdir(parents=True, exist_ok=True)

            if task.is_video:
                # Generate video thumbnail
                if not self.ffmpeg.ffmpeg_path:
                    task.status = ThumbnailStatus.FAILED
                    task.error = "ffmpeg not available"
                    logger.warning(f"Skipping video thumbnail for {task.source_path}: ffmpeg not found")
                else:
                    success = self.ffmpeg.generate_thumbnail(
                        task.source_path,
                        task.thumbnail_path,
                        task.size
                    )

                    if success:
                        task.status = ThumbnailStatus.GENERATED
                        task.created_at = datetime.now()
                        task.file_size = task.thumbnail_path.stat().st_size
                    else:
                        task.status = ThumbnailStatus.FAILED
                        task.error = "Video thumbnail generation failed"
                        logger.error(f"Failed to generate video thumbnail for {task.source_path}")
            else:
                # Generate thumbnail directly using PIL
                img = Image.open(task.source_path)

                # Convert RGBA to RGB if needed
                if img.mode == 'RGBA':
                    # Create white background
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    background.paste(img, mask=img.split()[-1])
                    img = background
                elif img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')

                # Resize with proper aspect ratio
                img.thumbnail(task.size, Image.Resampling.LANCZOS)

                # Save directly to target path
                img.save(task.thumbnail_path, quality=85, optimize=True)

                task.status = ThumbnailStatus.GENERATED
                task.created_at = datetime.now()
                task.file_size = task.thumbnail_path.stat().st_size

        except Exception as e:
            logger.error(f"Thumbnail generation failed for {task.source_path}: {e}")
            task.status = ThumbnailStatus.FAILED
            task.error = str(e)

        return task

    async def generate_batch(
        self,
        tasks: List[ThumbnailTask],
        progress_callback: Optional[Callable] = None,
        cancel_event: Optional['Event'] = None
    ) -> Dict[str, Any]:
        """Generate thumbnails in batch with progress tracking.

        Args:
            tasks: List of thumbnail tasks
            progress_callback: Optional callback for progress updates

        Returns:
            Summary of generation results
        """
        if not tasks:
            return {
                'total': 0,
                'completed': 0,
                'failed': 0,
                'skipped': 0,
                'processed': 0,
                'duration': 0.0,
                'duration_seconds': 0.0,
                'errors': [],
            }

        # Initialize progress
        self.current_progress = ThumbnailProgress(
            total=len(tasks),
            completed=0,
            failed=0,
            skipped=0
        )

        if progress_callback:
            self.progress_callbacks.append(progress_callback)

        # Process tasks in smaller batches to avoid overwhelming the system
        BATCH_SIZE = 20  # Process 20 thumbnails at a time
        completed_tasks = []
        start_time = datetime.now()

        cancelled = False

        # Process in chunks
        for batch_start in range(0, len(tasks), BATCH_SIZE):
            if cancel_event and cancel_event.is_set():
                logger.info("Thumbnail generation cancellation requested before batch %s", batch_start)
                cancelled = True
                break
            batch_end = min(batch_start + BATCH_SIZE, len(tasks))
            batch_tasks = tasks[batch_start:batch_end]

            logger.info(f"Processing batch {batch_start//BATCH_SIZE + 1}: tasks {batch_start+1}-{batch_end} of {len(tasks)}")

            # Submit batch to thread pool
            futures = []
            for task in batch_tasks:
                if cancel_event and cancel_event.is_set():
                    cancelled = True
                    logger.info("Cancellation requested during batch scheduling")
                    break
                future = self.executor.submit(self._generate_single_thumbnail, task)
                futures.append(future)

            if cancelled:
                break

            # Wait for this batch to complete before starting the next
            for future in as_completed(futures):
                try:
                    completed_task = future.result()
                    completed_tasks.append(completed_task)

                    # Update progress
                    if completed_task.status == ThumbnailStatus.GENERATED:
                        self.current_progress.completed += 1
                    elif completed_task.status == ThumbnailStatus.FAILED:
                        self.current_progress.failed += 1
                        self.current_progress.errors.append({
                            'file': str(completed_task.source_path),
                            'error': completed_task.error
                        })
                    elif completed_task.status == ThumbnailStatus.SKIPPED:
                        self.current_progress.skipped += 1

                    processed = (
                        self.current_progress.completed
                        + self.current_progress.failed
                        + self.current_progress.skipped
                    )
                    display_name = completed_task.source_path.name
                    if processed:
                        display_name = f"{processed}/{self.current_progress.total} • {display_name}"
                    self.current_progress.current_file = display_name
                    self.current_progress.update_percentage()

                    # Calculate estimated time
                    elapsed = (datetime.now() - start_time).total_seconds()
                    if processed > 0:
                        rate = elapsed / processed
                        remaining = self.current_progress.total - processed
                        self.current_progress.estimated_time_remaining = int(rate * remaining)

                    # Notify callbacks synchronously (we're in a thread pool context)
                    if self.progress_callbacks:
                        progress_data = self.current_progress.to_dict()
                        for callback in self.progress_callbacks:
                            try:
                                # Call callback - it should be thread-safe
                                callback(progress_data)
                            except Exception as e:
                                logger.error(f"Progress callback error: {e}", exc_info=True)

                except Exception as e:
                    logger.error(f"Task processing error: {e}")
                    self.current_progress.failed += 1

                if cancel_event and cancel_event.is_set():
                    cancelled = True
                    logger.info("Cancellation flag detected while awaiting batch results")
                    break

            # After each batch, yield to event loop to allow realtime/status updates
            await asyncio.sleep(0.1)
            logger.info(f"Batch complete: {self.current_progress.completed} completed, "
                       f"{self.current_progress.failed} failed, "
                       f"{self.current_progress.skipped} skipped")

            if cancelled:
                break

        if cancelled:
            remaining = self.current_progress.total - (
                self.current_progress.completed
                + self.current_progress.failed
                + self.current_progress.skipped
            )
            if remaining > 0:
                self.current_progress.skipped += remaining
                self.current_progress.update_percentage()

        # Store results in database
        await self._update_database(completed_tasks)

        # Clear cache for updated thumbnails
        if hasattr(self.cache, 'clear_pattern'):
            self.cache.clear_pattern('thumbnail:*')
        else:
            self.cache.clear_all()

        # Final summary
        elapsed = (datetime.now() - start_time).total_seconds()
        processed = (
            self.current_progress.completed
            + self.current_progress.failed
            + self.current_progress.skipped
        )

        summary = {
            'total': self.current_progress.total,
            'completed': self.current_progress.completed,
            'failed': self.current_progress.failed,
            'skipped': self.current_progress.skipped,
            'processed': processed,
            'duration': elapsed,
            'duration_seconds': elapsed,
            'errors': self.current_progress.errors,
        }

        if cancelled:
            summary['cancelled'] = True

        # Reset progress
        self.current_progress = None
        self.progress_callbacks.clear()

        return summary


    async def _update_database(self, tasks: List[ThumbnailTask]):
        """Update database with thumbnail information.

        Args:
            tasks: Completed thumbnail tasks
        """
        # Prepare batch update data
        updates = []
        for task in tasks:
            if task.status == ThumbnailStatus.GENERATED:
                updates.append({
                    'image_id': task.image_id,
                    'thumbnail_path': str(task.thumbnail_path),
                    'thumbnail_size': f"{task.size[0]}x{task.size[1]}",
                    'thumbnail_created': task.created_at,
                    'thumbnail_file_size': task.file_size
                })

        if updates:
            # Batch update database
            # This assumes the database has been extended with thumbnail columns
            # The actual implementation depends on your database schema
            try:
                await self.db.batch_update_thumbnails(updates)
                logger.info(f"Updated {len(updates)} thumbnail records in database")
            except Exception as e:
                logger.error(f"Database update failed: {e}")

    async def serve_thumbnail(
        self,
        image_id: str,
        size: str = 'medium',
        fallback_to_original: bool = True
    ) -> Optional[Path]:
        """Serve thumbnail with fallback to original.

        Args:
            image_id: Image identifier or path
            size: Thumbnail size
            fallback_to_original: Fall back to original if thumbnail missing

        Returns:
            Path to thumbnail or original
        """
        from pathlib import Path
        logger.debug(f"serve_thumbnail called with image_id={image_id}, size={size}")

        # Check cache first
        cache_key = f"thumbnail:{image_id}:{size}"
        cached_path = self.cache.get(cache_key) if self.cache else None
        if cached_path and Path(cached_path).exists():
            logger.debug(f"Found in cache: {cached_path}")
            return Path(cached_path)

        # Try to get image info from generated_images table if image_id is numeric
        source_path = None
        thumbnail_path = None

        try:
            image_id_int = int(image_id)
            logger.debug(f"Looking for image ID {image_id_int} in database")

            # Use asyncio.to_thread for the database query to make it non-blocking
            import asyncio
            import sqlite3

            def query_db():
                """Helper function to query database in thread."""
                conn = sqlite3.connect(str(self.db.db_path))
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    f"SELECT image_path, thumbnail_{size}_path FROM generated_images WHERE id = ?",
                    (image_id_int,)
                )
                row = cursor.fetchone()
                conn.close()
                return row

            row = await asyncio.to_thread(query_db)
            logger.debug(f"Database query result: {row}")

            if row:
                if row['image_path']:  # image_path
                    source_path = Path(row['image_path'])
                    logger.debug(f"Found source_path: {source_path}")
                if row[f'thumbnail_{size}_path']:  # thumbnail_X_path from database
                    thumbnail_path = Path(row[f'thumbnail_{size}_path'])
                    logger.debug(f"Found thumbnail_path from DB: {thumbnail_path}, exists: {thumbnail_path.exists()}")
                    if thumbnail_path.exists():
                        # Cache and return the thumbnail path from DB
                        if self.cache:
                            self.cache.set(cache_key, str(thumbnail_path), ttl=3600)
                        logger.debug(f"Returning thumbnail from DB: {thumbnail_path}")
                        return thumbnail_path
        except (ValueError, TypeError) as e:
            logger.debug(f"Not a numeric ID ({image_id}), trying as path: {e}")
            # image_id might be a path or file name
            # Try to resolve it relative to the output directory
            possible_path = Path(config.comfyui.output_dir) / image_id
            if possible_path.exists():
                source_path = possible_path
            else:
                # Try as absolute path
                possible_path = Path(image_id)
                if possible_path.exists():
                    source_path = possible_path

        if not source_path or not source_path.exists():
            logger.warning(f"No source path found or doesn't exist: {source_path}")
            return None

        # If we didn't find it in DB, try to compute the path
        if not thumbnail_path:
            thumbnail_path = self.get_thumbnail_path(source_path, size)
            logger.debug(f"Computed thumbnail path: {thumbnail_path}")

        if thumbnail_path and thumbnail_path.exists():
            # Cache the path
            if self.cache:
                self.cache.set(cache_key, str(thumbnail_path), ttl=3600)
            logger.debug(f"Returning computed thumbnail path: {thumbnail_path}")
            return thumbnail_path

        if fallback_to_original and source_path.exists():
            logger.debug(f"Falling back to original: {source_path}")
            return source_path

        logger.debug(f"No thumbnail or original found for image_id={image_id}, size={size}")
        return None

    def get_cache_statistics(self) -> Dict[str, Any]:
        """Get thumbnail cache statistics.

        Returns:
            Dictionary with cache statistics
        """
        stats = {
            'cache_directory': str(self.thumbnail_dir),
            'total_size': 0,
            'file_count': 0,
            'sizes': {}
        }

        # Calculate statistics for each size
        for size_name in self.base_generator.SIZES.keys():
            size_dir = self.thumbnail_dir / size_name
            if size_dir.exists():
                files = list(size_dir.glob('*'))
                total_size = sum(f.stat().st_size for f in files if f.is_file())
                stats['sizes'][size_name] = {
                    'count': len(files),
                    'size_bytes': total_size,
                    'size_mb': round(total_size / (1024 * 1024), 2)
                }
                stats['total_size'] += total_size
                stats['file_count'] += len(files)

        stats['total_size_mb'] = round(stats['total_size'] / (1024 * 1024), 2)

        return stats

    async def clear_cache(self, size: Optional[str] = None) -> int:
        """Clear thumbnail cache.

        Args:
            size: Optional specific size to clear

        Returns:
            Number of files deleted
        """
        deleted = 0

        if size:
            # Clear specific size
            size_dir = self.thumbnail_dir / size
            if size_dir.exists():
                for file in size_dir.glob('*'):
                    file.unlink()
                    deleted += 1
        else:
            # Clear all sizes
            for size_name in self.base_generator.SIZES.keys():
                size_dir = self.thumbnail_dir / size_name
                if size_dir.exists():
                    for file in size_dir.glob('*'):
                        file.unlink()
                        deleted += 1

        # Clear memory cache
        self.cache.clear_pattern('thumbnail:*')

        logger.info(f"Cleared {deleted} thumbnail files")
        return deleted
