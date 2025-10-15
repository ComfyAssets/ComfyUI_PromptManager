"""Thumbnail API endpoints for PromptManager."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from aiohttp import web

from ..services.enhanced_thumbnail_service import EnhancedThumbnailService, ThumbnailTask
from ..services.thumbnail_reconciliation_service import ThumbnailReconciliationService
from .utils.ffmpeg_finder import (
    DEFAULT_TIMEOUT as FFMPEG_TIMEOUT,
    find_ffmpeg_candidates,
    verify_ffmpeg_path,
)

from utils.cache import CacheManager

try:
    # Ensure parent directory is in path
    import sys
    from pathlib import Path
    parent_dir = Path(__file__).parent.parent.parent
    if str(parent_dir) not in sys.path:
        sys.path.insert(0, str(parent_dir))
    
    from .database import PromptDatabase as Database
except ImportError:
    # Use the alternative import method if direct import fails
    import importlib.util
    import sys

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

from .config import config
from utils.logging import get_logger

if TYPE_CHECKING:
    from .api.realtime_events import RealtimeEvents

logger = get_logger("promptmanager.api.thumbnails")


class ThumbnailAPI:
    """API endpoints for thumbnail operations."""

    def __init__(self, db: Database, cache: CacheManager, events: Optional["RealtimeEvents"] = None):
        """Initialize thumbnail API.

        Args:
            db: Database instance
            cache: Cache manager instance
        """
        self.db = db
        self.cache = cache
        self.thumbnail_service = EnhancedThumbnailService(db, cache)
        self.reconciliation_service = ThumbnailReconciliationService(db, cache, self.thumbnail_service)
        self.realtime = events

        # Track active generation tasks
        self.active_generations = {}

    def register_routes(self, app: web.Application):
        """Register thumbnail API routes.

        Args:
            app: aiohttp application
        """
        # Thumbnail operations
        app.router.add_post('/api/v1/thumbnails/scan', self.scan_missing_thumbnails)
        app.router.add_post('/api/v1/thumbnails/scan/all', self.scan_all_thumbnails)
        app.router.add_post('/api/v1/thumbnails/generate', self.generate_thumbnails)
        app.router.add_post('/api/v1/thumbnails/cancel', self.cancel_generation)
        app.router.add_get('/api/v1/thumbnails/status/{task_id}', self.get_task_status)
        app.router.add_post('/api/v1/thumbnails/rebuild', self.rebuild_thumbnails)

        # V2 Reconciliation endpoints
        app.router.add_post('/api/v1/thumbnails/comprehensive-scan', self.comprehensive_scan)
        app.router.add_post('/api/v1/thumbnails/rebuild-unified', self.rebuild_unified)

        # Thumbnail serving
        app.router.add_get('/api/v1/thumbnails/{image_id}/{size}', self.serve_thumbnail)
        app.router.add_get('/api/v1/thumbnails/{image_id}', self.serve_thumbnail)

        # Cache management
        app.router.add_get('/api/v1/thumbnails/cache/stats', self.get_cache_stats)
        app.router.add_delete('/api/v1/thumbnails/cache', self.clear_cache)
        app.router.add_delete('/api/v1/thumbnails/cache/{size}', self.clear_cache_size)

        # Disk usage
        app.router.add_get('/api/v1/thumbnails/disk-usage', self.get_disk_usage)

        # Settings
        app.router.add_get('/api/v1/settings/thumbnails', self.get_settings)
        app.router.add_put('/api/v1/settings/thumbnails', self.update_settings)

        # FFmpeg testing
        app.router.add_post('/api/v1/thumbnails/test-ffmpeg', self.test_ffmpeg)

        # Health check endpoint
        app.router.add_get('/api/v1/thumbnails/health', self.health_check)

    async def scan_all_thumbnails(self, request: web.Request) -> web.Response:
        """Scan ALL images including those with existing thumbnails.

        Args:
            request: HTTP request

        Returns:
            JSON response with complete scan results including existing thumbnails
        """
        try:
            # Handle empty or invalid JSON body gracefully
            try:
                data = await request.json() if request.body_exists else {}
            except (json.JSONDecodeError, ValueError):
                data = {}

            sizes = data.get('sizes', ['small', 'medium', 'large'])
            sample_limit = data.get('sample_limit', 6)

            # Use the service method to scan thumbnails
            result = await self.thumbnail_service.scan_all_thumbnails(
                sizes=sizes,
                sample_limit=sample_limit
            )

            return web.json_response(result)

        except Exception as e:
            logger.error(f"Scan all error: {e}")
            return web.json_response(
                {'error': str(e)},
                status=500
            )

    async def scan_missing_thumbnails(self, request: web.Request) -> web.Response:
        """Scan for images missing thumbnails.

        Args:
            request: HTTP request

        Returns:
            JSON response with missing thumbnail count and list
        """
        try:
            # Handle empty or invalid JSON body gracefully
            try:
                data = await request.json() if request.body_exists else {}
            except (json.JSONDecodeError, ValueError):
                data = {}

            sizes = data.get('sizes', ['small', 'medium', 'large'])
            include_videos = data.get('include_videos', True)

            image_paths = []

            try:
                # Get all image paths from database
                # Create direct connection to database
                import sqlite3
                conn = sqlite3.connect(self.db.db_path)
                cursor = conn.cursor()
                logger.info(f"Starting thumbnail scan... DB path: {self.db.db_path}")

                # Check for both v1 images table and v2 generated_images table
                cursor.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name IN ('images', 'generated_images')
                """)
                tables = cursor.fetchall()
                logger.info(f"Found tables: {tables}")
                
                if tables:
                    # Prefer generated_images (v2) over images (v1)
                    if ('generated_images',) in tables:
                        logger.info("Using generated_images table (v2)")
                        cursor.execute("""
                            SELECT id, image_path, filename
                            FROM generated_images
                            WHERE (thumbnail_small_path IS NULL OR thumbnail_small_path = '')
                            AND (thumbnail_small_path IS NULL OR thumbnail_small_path != 'FAILED')
                            AND image_path IS NOT NULL AND image_path != ''
                        """)
                    else:
                        logger.info("Using images table (v1)")
                        cursor.execute("""
                            SELECT id, file_path, filename
                            FROM images
                            WHERE file_path IS NOT NULL AND file_path != ''
                        """)
                    
                    images = cursor.fetchall()
                    logger.info(f"Found {len(images)} images in database")

                    for image in images:
                        # image is a tuple: (id, path, filename)
                        path = Path(image[1])  # path is at index 1
                        if path.exists():
                            # Check if it's a video or image
                            if include_videos or not self.thumbnail_service.ffmpeg.is_video(path):
                                image_paths.append(path)
                
                logger.info(f"Found {len(image_paths)} valid image paths")
                conn.close()
            except Exception as db_error:
                logger.warning(f"Database query error during scan: {db_error}")
                # Continue without database images

            # Scan for missing thumbnails, excluding blacklisted files
            logger.info(f"Scanning {len(image_paths)} images for missing thumbnails")
            logger.info(f"Currently {self.thumbnail_service.get_blacklisted_count()} files are blacklisted")
            
            # Filter out blacklisted files BEFORE scanning
            non_blacklisted_paths = [
                path for path in image_paths 
                if not self.thumbnail_service._is_blacklisted(path)
            ]
            
            blacklisted_count = len(image_paths) - len(non_blacklisted_paths)
            if blacklisted_count > 0:
                logger.info(f"Filtered out {blacklisted_count} blacklisted files from scan")
            
            missing_tasks = await self.thumbnail_service.scan_missing_thumbnails(
                non_blacklisted_paths,
                sizes
            )

            # Group by image for better reporting
            missing_by_image = {}
            for task in missing_tasks:
                image_id = task.image_id
                if image_id not in missing_by_image:
                    missing_by_image[image_id] = {
                        'path': str(task.source_path),
                        'missing_sizes': []
                    }

                # Determine size name from dimensions
                for size_name, dims in self.thumbnail_service.base_generator.SIZES.items():
                    if dims == task.size:
                        missing_by_image[image_id]['missing_sizes'].append(size_name)
                        break

            return web.json_response({
                'missing_count': len(missing_by_image),
                'total_operations': len(missing_tasks),
                'missing_images': list(missing_by_image.values()),
                'sizes_checked': sizes
            })

        except Exception as e:
            logger.error(f"Scan error: {e}")
            return web.json_response(
                {'error': str(e)},
                status=500
            )

    async def generate_thumbnails(self, request: web.Request) -> web.Response:
        """Generate missing thumbnails.

        Args:
            request: HTTP request

        Returns:
            JSON response with generation task ID or summary
        """
        try:
            # Handle empty or invalid JSON body gracefully
            try:
                data = await request.json() if request.body_exists else {}
            except (json.JSONDecodeError, ValueError):
                data = {}

            sizes = data.get('sizes', ['small', 'medium', 'large'])
            force_regenerate = data.get('force', False)

            # Get all image paths from database
            image_paths = []
            missing_thumbnail_count = 0
            try:
                import sqlite3
                conn = sqlite3.connect(self.db.db_path)
                cursor = conn.cursor()

                # First, count images without thumbnails in database
                cursor.execute("""
                    SELECT COUNT(*)
                    FROM generated_images
                    WHERE (thumbnail_small_path IS NULL OR thumbnail_small_path = '')
                    AND image_path IS NOT NULL AND image_path != ''
                """)
                missing_thumbnail_count = cursor.fetchone()[0]
                logger.info(f"Found {missing_thumbnail_count} images without thumbnail paths in database")

                # Get images without thumbnails for scanning
                # Exclude images marked as FAILED (permanently can't have thumbnails)
                cursor.execute("""
                    SELECT id, image_path, filename
                    FROM generated_images
                    WHERE (thumbnail_small_path IS NULL OR thumbnail_small_path = '')
                    AND (thumbnail_small_path IS NULL OR thumbnail_small_path != 'FAILED')
                    AND image_path IS NOT NULL AND image_path != ''
                """)
                images = cursor.fetchall()

                for image in images:
                    path = Path(image[1])  # image_path is at index 1
                    if path.exists():
                        image_paths.append(path)

                conn.close()
                        
            except Exception as e:
                logger.error(f"Error fetching images: {e}")
                # Return empty response if we can't get images
                return web.json_response({
                    'error': 'Failed to fetch images from database',
                    'details': str(e)
                }, status=500)

            # Scan for missing thumbnails, excluding blacklisted files
            logger.info(f"Scanning {len(image_paths)} images for missing thumbnails")
            logger.info(f"Currently {self.thumbnail_service.get_blacklisted_count()} files are blacklisted")
            
            # Filter out blacklisted files BEFORE scanning
            non_blacklisted_paths = [
                path for path in image_paths 
                if not self.thumbnail_service._is_blacklisted(path)
            ]
            
            blacklisted_count = len(image_paths) - len(non_blacklisted_paths)
            if blacklisted_count > 0:
                logger.info(f"Filtered out {blacklisted_count} blacklisted files from scan")
            
            tasks = await self.thumbnail_service.scan_missing_thumbnails(
                non_blacklisted_paths,
                sizes
            )

            if not tasks:
                return web.json_response({
                    'message': 'No missing thumbnails found',
                    'missing_count': 0,
                    'total': 0
                })

            # Generate unique task ID and cancellation flag
            task_id = f"thumb_gen_{datetime.now().timestamp()}"
            cancel_event = threading.Event()
            self.active_generations[task_id] = {
                'status': 'running',
                'progress': None,
                'result': None,
                'cancel_event': cancel_event,
            }

            # Start generation in background
            asyncio.create_task(self._generate_with_tracking(task_id, tasks, cancel_event))

            return web.json_response({
                'task_id': task_id,
                'total': len(tasks),
                'missing_count': len(tasks),  # Frontend expects this field
                'message': 'Generation started'
            })

        except Exception as e:
            logger.error(f"Generation error: {e}")
            return web.json_response(
                {'error': str(e)},
                status=500
            )

    async def _generate_with_tracking(
        self,
        task_id: str,
        tasks: List,
        cancel_event: Optional[threading.Event] = None,
    ):
        """Generate thumbnails with progress tracking.

        Args:
            task_id: Unique task identifier
            tasks: List of thumbnail tasks
        """
        try:
            logger.info(f"Starting thumbnail generation for task {task_id} with {len(tasks)} tasks")

            # Get the event loop for thread-safe updates
            loop = asyncio.get_event_loop()

            # Define progress callback that can be called from thread pool
            def progress_callback(progress_data):
                # Schedule update in main event loop (thread-safe)
                def update():
                    self.active_generations[task_id]['progress'] = progress_data
                    # Log every progress update for debugging
                    completed = progress_data.get('completed', 0)
                    failed = progress_data.get('failed', 0)
                    total = progress_data.get('total', 0)
                    percentage = progress_data.get('percentage', 0)

                    # Log every 5% or every 10 items, whichever is smaller
                    processed = completed + failed
                    if processed % max(1, min(10, total // 20)) == 0:
                        logger.info(f"Task {task_id}: {percentage:.1f}% complete "
                                  f"({completed} done, {failed} failed of {total} total)")

                    if self.realtime:
                        asyncio.create_task(
                            self.realtime.send_progress(
                                'thumbnails',
                                int(progress_data.get('percentage', 0)),
                                progress_data.get('current_file', ''),
                                task_id=task_id,
                                stats=progress_data,
                            )
                        )

                # Thread-safe update via event loop
                loop.call_soon_threadsafe(update)

            # Generate thumbnails
            result = await self.thumbnail_service.generate_batch(
                tasks,
                progress_callback,
                cancel_event=cancel_event,
            )

            # Update task status
            status = 'cancelled' if result.get('cancelled') else 'completed'
            self.active_generations[task_id]['status'] = status
            self.active_generations[task_id]['result'] = result
            logger.info(f"Task {task_id} completed: {result}")

            progress_snapshot = self.active_generations[task_id].get('progress') or {
                'total': result.get('total', 0),
                'completed': result.get('completed', 0),
                'failed': result.get('failed', 0),
                'skipped': result.get('skipped', 0),
                'processed': result.get('processed', 0),
                'percentage': 100,
                'errors': result.get('errors', []),
                'current_file': None,
                'duration_seconds': result.get('duration_seconds', result.get('duration', 0)),
            }

            if self.realtime:
                await self.realtime.send_progress(
                    'thumbnails',
                    100,
                    'Thumbnail generation cancelled' if status == 'cancelled' else 'Thumbnail generation complete',
                    task_id=task_id,
                    stats=progress_snapshot,
                    result=result,
                )

                summary = result or {}
                message = (
                    f"Generated {summary.get('completed', 0)} thumbnails"
                    f" (failed {summary.get('failed', 0)})"
                )
                toast_type = 'success' if summary.get('failed', 0) == 0 else 'warning'
                await self.realtime.send_toast(message, toast_type)

                if summary.get('completed', 0):
                    await self.realtime.notify_gallery_refresh()

        except Exception as e:
            logger.error(f"Background generation error for task {task_id}: {e}", exc_info=True)
            self.active_generations[task_id]['status'] = 'failed'
            self.active_generations[task_id]['error'] = str(e)

            if self.realtime:
                await self.realtime.send_progress(
                    'thumbnails',
                    int(self.active_generations[task_id].get('progress', {}).get('percentage', 0) or 0),
                    'Thumbnail generation failed',
                    task_id=task_id,
                    error=str(e),
                )
                await self.realtime.send_toast(f'Thumbnail generation failed: {e}', 'error')

    async def cancel_generation(self, request: web.Request) -> web.Response:
        """Request cancellation of an active thumbnail generation task."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            data = {}

        task_id = data.get('task_id') or request.query.get('task_id')
        if not task_id:
            return web.json_response({'error': 'task_id is required'}, status=400)

        task = self.active_generations.get(task_id)
        if not task:
            return web.json_response({'error': 'Task not found', 'task_id': task_id}, status=404)

        cancel_event = task.get('cancel_event')
        if not isinstance(cancel_event, threading.Event):
            return web.json_response({'error': 'Task cannot be cancelled', 'task_id': task_id}, status=409)

        if cancel_event.is_set():
            return web.json_response({'status': task.get('status', 'unknown'), 'task_id': task_id})

        cancel_event.set()
        task['status'] = 'cancelling'

        if self.realtime:
            await self.realtime.send_toast('Cancelling thumbnail generation…', 'info')

        return web.json_response({'status': 'cancelling', 'task_id': task_id})

    async def get_task_status(self, request: web.Request) -> web.Response:
        """Get current status of a thumbnail generation task.

        Args:
            request: HTTP request

        Returns:
            JSON response with task status
        """
        task_id = request.match_info.get('task_id')

        if not task_id or task_id not in self.active_generations:
            logger.debug(f"Task {task_id} not found. Active tasks: {list(self.active_generations.keys())}")
            return web.json_response(
                {'error': 'Task not found', 'task_id': task_id},
                status=404
            )

        task = self.active_generations[task_id]
        progress = task.get('progress', {})

        # Log status check for debugging
        if progress:
            logger.debug(f"Task {task_id} status check: {task.get('status')}, "
                        f"progress: {progress.get('completed', 0)}/{progress.get('total', 0)} "
                        f"({progress.get('percentage', 0)}%)")

        # Return current status
        return web.json_response({
            'task_id': task_id,
            'status': task.get('status', 'unknown'),
            'progress': {
                'completed': progress.get('completed', 0),
                'failed': progress.get('failed', 0),
                'skipped': progress.get('skipped', 0),
                'total': progress.get('total', 0),
                'percentage': progress.get('percentage', 0),
                'current_file': progress.get('current_file', ''),
                'estimated_time_remaining': progress.get('estimated_time_remaining', 0)
            },
            'result': task.get('result') if task.get('status') == 'completed' else None,
            'error': task.get('error') if task.get('status') == 'failed' else None
        })

    async def rebuild_thumbnails(self, request: web.Request) -> web.Response:
        """Rebuild all thumbnails (force regenerate).

        Args:
            request: HTTP request

        Returns:
            JSON response with task information
        """
        try:
            # Get parameters from request
            data = await request.json()
            sizes = data.get('sizes')
            skip_existing = data.get('skip_existing', True)  # NEW: default to True

            # If no sizes specified, try to get from user settings/preferences
            if not sizes:
                # Try to load from saved preferences
                sizes = self._get_user_thumbnail_size_preferences()
                if sizes:
                    logger.info("No sizes specified for rebuild, using saved preferences: %s", sizes)
                else:
                    # Default to common sizes if no preferences saved
                    sizes = ['small', 'medium', 'large']
                    logger.info("No sizes specified for rebuild, using defaults: %s", sizes)
            else:
                logger.info("Rebuilding thumbnails for sizes: %s", sizes)

            # Clear existing cache only if NOT skipping existing
            if not skip_existing:
                for size in sizes:
                    if size in self.thumbnail_service.base_generator.SIZES:
                        await self.thumbnail_service.clear_cache(size)

            # Get images from database
            image_paths = []
            try:
                import sqlite3
                conn = sqlite3.connect(self.db.db_path)
                cursor = conn.cursor()

                if skip_existing:
                    # Only get images WITHOUT thumbnails
                    logger.info("Skipping existing thumbnails - only generating missing")
                    cursor.execute("""
                        SELECT id, image_path, filename
                        FROM generated_images
                        WHERE (thumbnail_small_path IS NULL OR thumbnail_small_path = '')
                        AND image_path IS NOT NULL AND image_path != ''
                    """)
                else:
                    # Get ALL images (existing behavior for overwrite)
                    logger.info("Overwriting all thumbnails - regenerating everything")
                    cursor.execute("""
                        SELECT id, image_path, filename
                        FROM generated_images
                        WHERE image_path IS NOT NULL AND image_path != ''
                    """)

                images = cursor.fetchall()

                for image in images:
                    path = Path(image[1])  # image_path is at index 1
                    if path.exists():
                        image_paths.append(path)

                conn.close()

            except Exception as e:
                logger.error(f"Error fetching images for rebuild: {e}")
                return web.json_response({
                    'error': 'Failed to fetch images from database',
                    'details': str(e)
                }, status=500)

            # Create tasks only for requested sizes
            all_tasks = []
            for path in image_paths:
                is_video = self.thumbnail_service.ffmpeg.is_video(path)

                for size_name in sizes:
                    # Skip invalid size names
                    if size_name not in self.thumbnail_service.base_generator.SIZES:
                        logger.warning(f"Invalid size name '{size_name}' skipped")
                        continue

                    size_dims = self.thumbnail_service.base_generator.SIZES[size_name]
                    task = ThumbnailTask(
                        image_id=hashlib.md5(str(path).encode()).hexdigest(),
                        source_path=path,
                        thumbnail_path=self.thumbnail_service.get_thumbnail_path(path, size_name),
                        size=size_dims,
                        format=path.suffix.lower()[1:],
                        is_video=is_video
                    )
                    all_tasks.append(task)

            # Start rebuild
            task_id = f"rebuild_{datetime.now().timestamp()}"
            cancel_event = threading.Event()
            self.active_generations[task_id] = {
                'status': 'running',
                'progress': None,
                'result': None,
                'cancel_event': cancel_event
            }

            asyncio.create_task(self._generate_with_tracking(task_id, all_tasks, cancel_event))

            return web.json_response({
                'task_id': task_id,
                'total': len(all_tasks),
                'message': 'Rebuild started'
            })

        except Exception as e:
            logger.error(f"Rebuild error: {e}")
            return web.json_response(
                {'error': str(e)},
                status=500
            )

    async def get_disk_usage(self, request: web.Request) -> web.Response:
        """Get storage usage breakdown for images, thumbnails, and caches."""

        try:
            MB = 1024 * 1024

            def _directory_stats(path: Path) -> Dict[str, Any]:
                size_bytes = 0
                file_count = 0

                if path.exists():
                    for item in path.rglob('*'):
                        try:
                            if item.is_file():
                                size_bytes += item.stat().st_size
                                file_count += 1
                        except (OSError, PermissionError):
                            continue

                return {
                    'path': str(path),
                    'size_bytes': size_bytes,
                    'size_mb': round(size_bytes / MB, 2),
                    'file_count': file_count,
                }

            base_dir = Path(config.storage.base_path)
            images_dir = base_dir / config.storage.images_path
            thumbnails_dir = self.thumbnail_service.thumbnail_dir

            images_stats = _directory_stats(images_dir)
            thumbnail_stats = self.thumbnail_service.get_cache_statistics()
            thumbnail_total_bytes = thumbnail_stats.get('total_size', 0)

            cache_total_bytes = 0
            cache_entry_count = 0
            cache_details: List[Dict[str, Any]] = []

            cache_stats = self.cache.get_all_stats() if self.cache else {}
            cache_instances = getattr(self.cache, '_caches', {}) if self.cache else {}

            for name, stats in cache_stats.items():
                if hasattr(stats, 'to_dict'):
                    stats_dict = stats.to_dict()
                else:
                    stats_dict = dict(stats)

                size_bytes = int(stats_dict.get('size_bytes', 0) or 0)
                entry_count = int(stats_dict.get('entry_count', 0) or 0)
                cache_total_bytes += size_bytes
                cache_entry_count += entry_count

                cache_path: Optional[str] = None
                cache_obj = cache_instances.get(name) if isinstance(cache_instances, dict) else None
                if cache_obj is not None and hasattr(cache_obj, 'cache_dir'):
                    cache_path = str(getattr(cache_obj, 'cache_dir'))

                cache_details.append({
                    'name': name,
                    'size_bytes': size_bytes,
                    'size_mb': round(size_bytes / MB, 2),
                    'entry_count': entry_count,
                    'path': cache_path,
                })

            disk_usage = shutil.disk_usage(str(base_dir))

            total_bytes = (
                images_stats['size_bytes']
                + thumbnail_total_bytes
                + cache_total_bytes
            )

            response_payload = {
                'summary': {
                    'total_bytes': total_bytes,
                    'total_mb': round(total_bytes / MB, 2),
                    'disk_total_bytes': disk_usage.total,
                    'disk_free_bytes': disk_usage.free,
                    'disk_used_bytes': disk_usage.used,
                    'disk_total_mb': round(disk_usage.total / MB, 2),
                    'disk_free_mb': round(disk_usage.free / MB, 2),
                    'percentage_of_disk': round((total_bytes / disk_usage.total) * 100, 2) if disk_usage.total > 0 else 0,
                    'total_files': images_stats['file_count'] + thumbnail_stats.get('file_count', 0),
                    'cache_entries': cache_entry_count,
                },
                'breakdown': {
                    'images': images_stats,
                    'thumbnails': {
                        'path': str(thumbnails_dir),
                        'size_bytes': thumbnail_total_bytes,
                        'size_mb': round(thumbnail_total_bytes / MB, 2),
                        'file_count': thumbnail_stats.get('file_count', 0),
                        'sizes': thumbnail_stats.get('sizes', {}),
                    },
                    'cache': {
                        'size_bytes': cache_total_bytes,
                        'size_mb': round(cache_total_bytes / MB, 2),
                        'entry_count': cache_entry_count,
                        'caches': cache_details,
                    },
                },
                'timestamp': datetime.utcnow().isoformat() + 'Z',
            }

            return web.json_response(response_payload)

        except Exception as e:
            logger.error(f"Disk usage error: {e}")
            return web.json_response(
                {'error': str(e)},
                status=500
            )

    async def serve_thumbnail(self, request: web.Request) -> web.Response:
        """Serve thumbnail image with fallback.

        Args:
            request: HTTP request

        Returns:
            Image file response or 404
        """
        image_id = request.match_info['image_id']
        size = request.match_info.get('size', 'medium')

        try:
            # Get thumbnail path
            thumbnail_path = await self.thumbnail_service.serve_thumbnail(
                image_id,
                size,
                fallback_to_original=True
            )

            if not thumbnail_path or not thumbnail_path.exists():
                return web.Response(status=404)

            # Serve the file
            return web.FileResponse(
                thumbnail_path,
                headers={
                    'Cache-Control': 'public, max-age=86400',  # 1 day
                    'X-Thumbnail-Size': size
                }
            )

        except Exception as e:
            logger.error(f"Serve error: {e}")
            return web.Response(status=500)

    async def get_cache_stats(self, request: web.Request) -> web.Response:
        """Get thumbnail cache statistics.

        Args:
            request: HTTP request

        Returns:
            JSON response with cache statistics
        """
        try:
            stats = self.thumbnail_service.get_cache_statistics()
            return web.json_response(stats)

        except Exception as e:
            logger.error(f"Stats error: {e}")
            return web.json_response(
                {'error': str(e)},
                status=500
            )

    async def clear_cache(self, request: web.Request) -> web.Response:
        """Clear entire thumbnail cache.

        Args:
            request: HTTP request

        Returns:
            JSON response with deletion count
        """
        try:
            deleted = await self.thumbnail_service.clear_cache()

            return web.json_response({
                'success': True,
                'deleted': deleted,
                'message': f'Cleared {deleted} thumbnail files'
            })

        except Exception as e:
            logger.error(f"Clear cache error: {e}")
            return web.json_response(
                {'error': str(e)},
                status=500
            )

    async def clear_cache_size(self, request: web.Request) -> web.Response:
        """Clear thumbnail cache for specific size.

        Args:
            request: HTTP request

        Returns:
            JSON response with deletion count
        """
        size = request.match_info['size']

        if size not in self.thumbnail_service.base_generator.SIZES:
            return web.json_response(
                {'error': f'Invalid size: {size}'},
                status=400
            )

        try:
            deleted = await self.thumbnail_service.clear_cache(size)

            return web.json_response({
                'success': True,
                'deleted': deleted,
                'size': size,
                'message': f'Cleared {deleted} {size} thumbnails'
            })

        except Exception as e:
            logger.error(f"Clear cache error: {e}")
            return web.json_response(
                {'error': str(e)},
                status=500
            )

    async def get_settings(self, request: web.Request) -> web.Response:
        """Get thumbnail settings.

        Args:
            request: HTTP request

        Returns:
            JSON response with current settings
        """
        max_parallel_raw = config.get('thumbnail.max_parallel', 4)
        try:
            max_parallel = int(max_parallel_raw)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid thumbnail.max_parallel value %r; defaulting to 4",
                max_parallel_raw
            )
            max_parallel = 4

        settings = {
            'cache_dir': str(self.thumbnail_service.thumbnail_dir),
            'auto_generate': config.get('thumbnail.auto_generate', True),
            'sizes': {
                size: {
                    'enabled': True,
                    'dimensions': f'{dims[0]}x{dims[1]}'
                }
                for size, dims in self.thumbnail_service.base_generator.SIZES.items()
            },
            'ffmpeg_path': self.thumbnail_service.ffmpeg.ffmpeg_path or '',
            'ffmpeg_auto_detect': not bool(config.get('thumbnail.ffmpeg_path')),
            'video_enabled': bool(self.thumbnail_service.ffmpeg.ffmpeg_path),
            'video_timestamp': config.get('thumbnail.video_timestamp', 1.0),
            'jpeg_quality': config.get('thumbnail.jpeg_quality', 85),
            'webp_quality': config.get('thumbnail.webp_quality', 85),
            'max_parallel': max_parallel,
            'cache_ttl': config.get('thumbnail.cache_ttl', 604800),
            'max_cache_size_gb': config.get('thumbnail.max_cache_size_gb', 10)
        }

        return web.json_response(settings)

    def _get_user_thumbnail_size_preferences(self) -> Optional[List[str]]:
        """Get user's saved thumbnail size preferences.

        Returns:
            List of size names or None if not set
        """
        try:
            # Try to get from config
            enabled_sizes = []
            size_config = config.get('thumbnail.enabled_sizes')

            if size_config:
                # If stored as string, parse it
                if isinstance(size_config, str):
                    enabled_sizes = [s.strip() for s in size_config.split(',') if s.strip()]
                elif isinstance(size_config, list):
                    enabled_sizes = size_config

                # Validate sizes
                valid_sizes = []
                for size in enabled_sizes:
                    if size in self.thumbnail_service.base_generator.SIZES:
                        valid_sizes.append(size)

                return valid_sizes if valid_sizes else None

            return None

        except Exception as e:
            logger.warning(f"Failed to load thumbnail size preferences: {e}")
            return None

    def _save_user_thumbnail_size_preferences(self, sizes: List[str]) -> bool:
        """Save user's thumbnail size preferences.

        Args:
            sizes: List of size names to save

        Returns:
            True if saved successfully
        """
        try:
            # Validate sizes
            valid_sizes = [s for s in sizes if s in self.thumbnail_service.base_generator.SIZES]

            if valid_sizes:
                # Save as comma-separated string for compatibility
                config.set('thumbnail.enabled_sizes', ','.join(valid_sizes))
                config.save()
                logger.info(f"Saved thumbnail size preferences: {valid_sizes}")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to save thumbnail size preferences: {e}")
            return False

    async def update_settings(self, request: web.Request) -> web.Response:
        """Update thumbnail settings.

        Args:
            request: HTTP request

        Returns:
            JSON response with success status
        """
        try:
            data = await request.json()

            updates: Dict[str, Any] = dict(data)

            if 'max_parallel' in updates:
                try:
                    updates['max_parallel'] = int(updates['max_parallel'])
                except (TypeError, ValueError):
                    return web.json_response(
                        {
                            'success': False,
                            'error': 'max_parallel must be an integer'
                        },
                        status=400
                    )

            # Handle enabled sizes separately
            if 'enabled_sizes' in updates:
                sizes = updates.pop('enabled_sizes')
                self._save_user_thumbnail_size_preferences(sizes)

            # Update configuration
            for key, value in updates.items():
                config_key = f'thumbnail.{key}'
                config.set(config_key, value)

            # Save configuration
            config.save()

            # Reinitialize service if needed
            if 'cache_dir' in data:
                self.thumbnail_service.thumbnail_dir = Path(data['cache_dir'])
                self.thumbnail_service.thumbnail_dir.mkdir(parents=True, exist_ok=True)

            if 'max_parallel' in updates:
                self.thumbnail_service.set_parallel_workers(updates['max_parallel'])

            return web.json_response({
                'success': True,
                'message': 'Settings updated successfully'
            })

        except Exception as e:
            logger.error(f"Settings update error: {e}")
            return web.json_response(
                {'error': str(e)},
                status=500
            )

    async def health_check(self, request: web.Request) -> web.Response:
        """Health check endpoint for thumbnail API.

        Args:
            request: HTTP request

        Returns:
            JSON response with health status
        """
        return web.json_response({
            'status': 'ok',
            'service': 'thumbnail_api',
            'thumbnail_dir': str(self.thumbnail_service.thumbnail_dir),
            'cache_enabled': bool(self.cache),
            'ffmpeg_available': bool(self.thumbnail_service.ffmpeg.ffmpeg_path)
        })

    async def test_ffmpeg(self, request: web.Request) -> web.Response:
        """Test ffmpeg availability.

        Args:
            request: HTTP request

        Returns:
            JSON response with ffmpeg status
        """
        try:
            data = await request.json()
        except Exception:
            data = {}

        try:
            requested_path = data.get('ffmpeg_path') or data.get('path')
            include_ffprobe = bool(data.get('include_ffprobe', True))
            timeout = float(data.get('timeout', FFMPEG_TIMEOUT))

            def _best_response(candidate: dict) -> web.Response:
                self.thumbnail_service.ffmpeg.ffmpeg_path = candidate['path']
                return web.json_response(
                    {
                        'success': True,
                        'path': candidate['path'],
                        'version': candidate.get('version'),
                        'candidate': candidate,
                    }
                )

            if requested_path:
                candidate = verify_ffmpeg_path(
                    requested_path,
                    include_ffprobe=include_ffprobe,
                    timeout=timeout,
                )

                if candidate.get('reachable'):
                    return _best_response(candidate)

                candidate.pop('reachable', None)
                return web.json_response(
                    {
                        'success': False,
                        'error': candidate.get('error') or 'ffmpeg not reachable',
                        'candidate': candidate,
                    }
                )

            candidates = find_ffmpeg_candidates(
                include_ffprobe=include_ffprobe,
                timeout=timeout,
            )
            best = next((c for c in candidates if c.get('reachable')), None)

            if best:
                self.thumbnail_service.ffmpeg.ffmpeg_path = best['path']
                return web.json_response(
                    {
                        'success': True,
                        'path': best['path'],
                        'version': best.get('version'),
                        'best_candidate': best,
                        'candidates': candidates,
                    }
                )

            return web.json_response(
                {
                    'success': False,
                    'error': 'ffmpeg not found',
                    'candidates': candidates,
                }
            )

        except Exception as e:
            logger.error(f"FFmpeg test error: {e}")
            return web.json_response(
                {'error': str(e)},
                status=500
            )

    async def comprehensive_scan(self, request: web.Request) -> web.Response:
        """Perform comprehensive thumbnail scan (V2).

        Args:
            request: HTTP request

        Returns:
            JSON response with task ID and initial categories
        """
        try:
            data = await request.json() if request.body_exists else {}
            sizes = data.get('sizes', ['small', 'medium', 'large'])
            sample_limit = data.get('sample_limit', 6)

            # Generate task ID
            task_id = f"scan_{datetime.now().timestamp()}"

            # Create task tracking
            self.active_generations[task_id] = {
                'status': 'running',
                'progress': None,
                'result': None,
                'cancel_event': None,
            }

            # Start scan in background
            asyncio.create_task(self._run_comprehensive_scan(task_id, sizes, sample_limit))

            return web.json_response({
                'task_id': task_id,
                'message': 'Scan started'
            })

        except Exception as e:
            logger.error(f"Comprehensive scan error: {e}")
            return web.json_response(
                {'error': str(e)},
                status=500
            )

    async def _run_comprehensive_scan(self, task_id: str, sizes: List[str], sample_limit: int):
        """Run comprehensive scan in background with progress tracking.

        Args:
            task_id: Unique task identifier
            sizes: Thumbnail sizes to check
            sample_limit: Sample limit for true orphans
        """
        try:
            logger.info(f"Starting comprehensive scan for task {task_id}")

            # Get the event loop for thread-safe updates
            loop = asyncio.get_event_loop()

            # Define progress callback
            def progress_callback(progress_data):
                def update():
                    self.active_generations[task_id]['progress'] = {
                        'phase': progress_data.phase,
                        'current': progress_data.current,
                        'total': progress_data.total,
                        'percentage': progress_data.percentage,
                        'message': progress_data.message
                    }
                    # Note: Disabled realtime progress to avoid duplicate progress windows
                    # The V2 modal has its own progress tracking via polling

                loop.call_soon_threadsafe(update)

            # Perform scan
            results = await self.reconciliation_service.comprehensive_scan(
                sizes=sizes,
                sample_limit=sample_limit,
                progress_callback=progress_callback
            )

            # Update task status
            self.active_generations[task_id]['status'] = 'completed'
            self.active_generations[task_id]['result'] = {
                'total_images': results.total_images,
                'categories': results.categories,
                'breakdown': results.breakdown,
                'true_orphans': results.true_orphans,
                'estimated_time_seconds': results.estimated_time_seconds
            }

            logger.info(f"Scan task {task_id} completed: {results.categories}")

            if self.realtime:
                await self.realtime.send_progress(
                    'thumbnail_scan',
                    100,
                    'Scan complete',
                    task_id=task_id,
                    result=self.active_generations[task_id]['result']
                )

        except Exception as e:
            logger.error(f"Background scan error for task {task_id}: {e}", exc_info=True)
            self.active_generations[task_id]['status'] = 'failed'
            self.active_generations[task_id]['error'] = str(e)

            if self.realtime:
                await self.realtime.send_toast(f'Thumbnail scan failed: {e}', 'error')

    async def rebuild_unified(self, request: web.Request) -> web.Response:
        """Execute unified rebuild operations (V2).

        Args:
            request: HTTP request

        Returns:
            JSON response with task ID
        """
        try:
            data = await request.json()
            operations = data.get('operations', {
                'fix_broken_links': True,
                'link_orphans': True,
                'generate_missing': True,
                'delete_true_orphans': False
            })
            sizes = data.get('sizes', ['small', 'medium', 'large'])
            scan_results = data.get('scan_results', {})

            # Generate task ID
            task_id = f"rebuild_unified_{datetime.now().timestamp()}"
            cancel_event = threading.Event()

            # Create task tracking
            self.active_generations[task_id] = {
                'status': 'running',
                'progress': None,
                'result': None,
                'cancel_event': cancel_event
            }

            # Start rebuild in background
            asyncio.create_task(
                self._run_unified_rebuild(task_id, operations, sizes, scan_results, cancel_event)
            )

            return web.json_response({
                'task_id': task_id,
                'message': 'Rebuild started'
            })

        except Exception as e:
            logger.error(f"Unified rebuild error: {e}")
            return web.json_response(
                {'error': str(e)},
                status=500
            )

    async def _run_unified_rebuild(
        self,
        task_id: str,
        operations: Dict[str, bool],
        sizes: List[str],
        scan_results: Dict[str, List],
        cancel_event: threading.Event
    ):
        """Run unified rebuild in background with progress tracking.

        Args:
            task_id: Unique task identifier
            operations: Operations to perform
            sizes: Thumbnail sizes to process
            scan_results: Results from comprehensive scan
            cancel_event: Event for cancellation
        """
        try:
            logger.info(f"Starting unified rebuild for task {task_id}")

            # Get the event loop for thread-safe updates
            loop = asyncio.get_event_loop()

            # Define progress callback
            def progress_callback(progress_data):
                def update():
                    self.active_generations[task_id]['progress'] = progress_data
                    # Note: Disabled realtime progress to avoid duplicate progress windows
                    # The V2 modal has its own progress tracking via polling

                loop.call_soon_threadsafe(update)

            # Perform rebuild
            summary = await self.reconciliation_service.rebuild_unified(
                operations=operations,
                sizes=sizes,
                scan_results=scan_results,
                progress_callback=progress_callback,
                cancel_event=cancel_event
            )

            # Update task status
            status = 'cancelled' if cancel_event.is_set() else 'completed'
            self.active_generations[task_id]['status'] = status
            self.active_generations[task_id]['result'] = summary

            logger.info(f"Rebuild task {task_id} {status}: {summary}")

            if self.realtime:
                await self.realtime.send_progress(
                    'thumbnail_rebuild',
                    100,
                    'Rebuild complete' if status == 'completed' else 'Rebuild cancelled',
                    task_id=task_id,
                    result=summary
                )

                message = f"Rebuilt {summary['completed']} thumbnails"
                toast_type = 'success' if summary['stats']['failed'] == 0 else 'warning'
                await self.realtime.send_toast(message, toast_type)

                if summary['stats'].get('generated', 0) > 0:
                    await self.realtime.notify_gallery_refresh()

        except Exception as e:
            logger.error(f"Background rebuild error for task {task_id}: {e}", exc_info=True)
            self.active_generations[task_id]['status'] = 'failed'
            self.active_generations[task_id]['error'] = str(e)

            if self.realtime:
                await self.realtime.send_toast(f'Thumbnail rebuild failed: {e}', 'error')
