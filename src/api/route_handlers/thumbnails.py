"""Thumbnail API endpoints for PromptManager."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from aiohttp import web

from ...services.enhanced_thumbnail_service import EnhancedThumbnailService, ThumbnailTask, ThumbnailStatus
from ...services.thumbnail_reconciliation_service import ThumbnailReconciliationService
from ...utils.ffmpeg_finder import (
    DEFAULT_TIMEOUT as FFMPEG_TIMEOUT,
    find_ffmpeg_candidates,
    verify_ffmpeg_path,
)

from utils.cache import CacheManager
from ...database.connection_helper import DatabaseConnection, get_db_connection

try:
    # Ensure parent directory is in path
    import sys
    from pathlib import Path
    parent_dir = Path(__file__).parent.parent.parent
    if str(parent_dir) not in sys.path:
        sys.path.insert(0, str(parent_dir))

    from ...database import PromptDatabase as Database
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

from ...config import config
from utils.logging import get_logger

if TYPE_CHECKING:
    from ..realtime_events import RealtimeEvents

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
        app.router.add_post('/api/v1/thumbnails/rebuild-all-from-scratch', self.rebuild_all_from_scratch)

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

            sizes = data.get('sizes', ['small', 'medium'])
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

            # Get user's enabled sizes, or use provided sizes, or fall back to default
            default_sizes = self._get_user_thumbnail_size_preferences() or ['small', 'medium']
            sizes = data.get('sizes', default_sizes)
            include_videos = data.get('include_videos', True)

            missing_images = []
            total_operations = 0

            try:
                # Get all image paths from database
                # Create direct connection to database
                import sqlite3
                conn = DatabaseConnection.get_connection(self.db.db_path)
                cursor = conn.cursor()

                # Check if this is a v2 database (has generated_images table)
                cursor.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name = 'generated_images'
                """)
                has_v2_table = cursor.fetchone() is not None

                if has_v2_table:
                    # V2 schema: Get images and check for missing ID-based thumbnails
                    cursor.execute("""
                        SELECT id, image_path, filename,
                               thumbnail_small_path, thumbnail_medium_path, thumbnail_large_path
                        FROM generated_images
                        WHERE image_path IS NOT NULL AND image_path != ''
                    """)

                    images = cursor.fetchall()

                    for image in images:
                        # image is a tuple: (id, path, filename, small, medium, large)
                        db_id = image[0]  # id at index 0
                        path = Path(image[1])  # path is at index 1
                        if not path.exists():
                            continue

                        # Check which sizes are missing (use ID-based paths)
                        missing_sizes = []
                        for size_name in sizes:
                            expected_thumbnail = self.thumbnail_service.get_thumbnail_path(path, size_name, image_id=db_id)
                            if not expected_thumbnail.exists():
                                missing_sizes.append(size_name)
                                total_operations += 1

                        if missing_sizes:
                            # Check if blacklisted
                            if self.thumbnail_service._is_blacklisted(path):
                                continue
                            # Check if it's a video or image
                            if include_videos or not self.thumbnail_service.ffmpeg.is_video(path):
                                missing_images.append({
                                    'path': str(path),
                                    'missing_sizes': missing_sizes
                                })
                else:
                    # V1 schema fallback - use hash-based checking
                    logger.warning("V1 schema detected - ID-based thumbnails not supported")

                conn.close()
            except Exception as db_error:
                logger.warning(f"Database query error during scan: {db_error}")
                # Continue without database images

            # Return the results using ID-based path checking
            return web.json_response({
                'missing_count': len(missing_images),
                'total_operations': total_operations,
                'missing_images': missing_images,
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

            sizes = data.get('sizes', ['small', 'medium'])
            skip_existing = data.get('skip_existing', True)

            # Get all image paths from database
            image_paths = []
            missing_thumbnail_count = 0
            try:
                import sqlite3
                conn = DatabaseConnection.get_connection(self.db.db_path)
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

                # Store tuples of (db_id, path) instead of just path
                image_data = []
                for image in images:
                    db_id = image[0]  # id is at index 0
                    path = Path(image[1])  # image_path is at index 1
                    if path.exists():
                        image_data.append((db_id, path))

                conn.close()

            except Exception as e:
                logger.error(f"Error fetching images for rebuild: {e}")
                return web.json_response({
                    'error': 'Failed to fetch images from database',
                    'details': str(e)
                }, status=500)

            # Create tasks only for requested sizes
            all_tasks = []
            for db_id, path in image_data:
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
                        thumbnail_path=self.thumbnail_service.get_thumbnail_path(path, size_name, image_id=db_id),
                        size=size_dims,
                        format=path.suffix.lower()[1:],
                        size_name=size_name,
                        is_video=is_video,
                        db_id=db_id  # ID-based thumbnail naming
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
            thumbnails_dir = self.thumbnail_service.thumbnail_dir

            # Query database for actual image statistics
            # Images are tracked in generated_images table with their full ComfyUI output paths
            import sqlite3
            conn = DatabaseConnection.get_connection(self.db.db_path)
            conn.row_factory = sqlite3.Row  # Enable dict-like access
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    COUNT(*) as file_count,
                    COALESCE(SUM(file_size), 0) as size_bytes
                FROM generated_images
                WHERE file_size IS NOT NULL
            """)
            row = cursor.fetchone()
            images_stats = {
                'path': 'ComfyUI output directory',
                'size_bytes': row['size_bytes'] if row else 0,
                'size_mb': round((row['size_bytes'] if row else 0) / MB, 2),
                'file_count': row['file_count'] if row else 0,
            }
            conn.close()
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
        
        valid_sizes = {'small', 'medium', 'large'}
        if size not in valid_sizes:
            size = 'medium'

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

        # Get user's enabled sizes preference
        enabled_sizes = self._get_user_thumbnail_size_preferences() or ['small', 'medium']

        settings = {
            'cache_dir': str(self.thumbnail_service.thumbnail_dir),
            'auto_generate': config.get('thumbnail.auto_generate', True),
            'enabled_sizes': enabled_sizes,  # Add user's enabled sizes
            'sizes': {
                size: {
                    'enabled': size in enabled_sizes,  # Mark as enabled only if user has it enabled
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
                cache_dir = Path(data['cache_dir']).resolve()
                if not str(cache_dir).startswith('/') or '..' in str(cache_dir):
                    return web.json_response({'error': 'Invalid cache directory'}, status=400)
                self.thumbnail_service.thumbnail_dir = cache_dir
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

            # Get user's enabled sizes, or use provided sizes, or fall back to default
            default_sizes = self._get_user_thumbnail_size_preferences() or ['small', 'medium']
            sizes = data.get('sizes', default_sizes)
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

            # Note: send_progress disabled - V2 modal has its own progress tracking via polling
            # Prevents duplicate floating progress dialogs
            # if self.realtime:
            #     await self.realtime.send_progress(
            #         'thumbnail_scan',
            #         100,
            #         'Scan complete',
            #         task_id=task_id,
            #         result=self.active_generations[task_id]['result']
            #     )

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
            sizes = data.get('sizes', ['small', 'medium'])
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

            # Note: send_progress disabled - V2 modal has its own progress tracking via polling
            # Prevents duplicate floating progress dialogs
            if self.realtime:
                # await self.realtime.send_progress(
                #     'thumbnail_rebuild',
                #     100,
                #     'Rebuild complete' if status == 'completed' else 'Rebuild cancelled',
                #     task_id=task_id,
                #     result=summary
                # )

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

    async def rebuild_all_from_scratch(self, request: web.Request) -> web.Response:
        """Nuclear option: Delete all thumbnails, rescan output folder, regenerate everything.

        This performs a complete rebuild from scratch:
        1. Delete all thumbnail files from disk
        2. Reset all database thumbnail paths to NULL
        3. Scan output folder for all images (like "Scan ComfyUI Images")
        4. Add any missing images to database
        5. Generate all thumbnails for selected sizes

        Args:
            request: HTTP request

        Returns:
            JSON response with task ID
        """
        try:
            data = await request.json()
            sizes = data.get('sizes', ['small', 'medium'])

            logger.info(f"Starting nuclear rebuild-all-from-scratch with sizes: {sizes}")

            # Generate task ID
            task_id = f"rebuild_all_{datetime.now().timestamp()}"
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
                self._run_rebuild_all_from_scratch(task_id, sizes, cancel_event)
            )

            return web.json_response({
                'task_id': task_id,
                'message': 'Rebuild All from Scratch started'
            })

        except Exception as e:
            logger.error(f"Rebuild-all-from-scratch error: {e}")
            return web.json_response(
                {'error': str(e)},
                status=500
            )

    async def _run_rebuild_all_from_scratch(
        self,
        task_id: str,
        sizes: List[str],
        cancel_event: threading.Event
    ):
        """Run full rebuild from scratch in background.

        Args:
            task_id: Unique task identifier
            sizes: Thumbnail sizes to generate
            cancel_event: Event for cancellation
        """
        try:
            logger.info(f"Starting rebuild-all-from-scratch for task {task_id}")

            # Get the event loop for thread-safe updates
            loop = asyncio.get_event_loop()

            # Progress callback
            def progress_callback(progress_data):
                def update():
                    self.active_generations[task_id]['progress'] = progress_data
                loop.call_soon_threadsafe(update)

            # Step 1: Delete all thumbnail files
            logger.info("Step 1: Deleting all thumbnail files...")
            deleted_count = 0
            for size in ['small', 'medium', 'large', 'xlarge']:
                size_dir = self.thumbnail_service.thumbnail_dir / size
                if size_dir.exists():
                    for thumb_file in size_dir.glob('*'):
                        try:
                            thumb_file.unlink()
                            deleted_count += 1
                        except Exception as e:
                            logger.warning(f"Failed to delete {thumb_file}: {e}")

            logger.info(f"Deleted {deleted_count} thumbnail files")

            progress_callback({
                'operation': 'deleting_thumbnails',
                'percentage': 10,
                'current_file': f'Deleted {deleted_count} files',
                'stats': {'deleted': deleted_count}
            })

            # Step 2: Reset all database thumbnail paths
            logger.info("Step 2: Resetting database thumbnail paths...")
            from ...database.connection_helper import get_db_connection

            with get_db_connection(self.db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE generated_images
                    SET thumbnail_small_path = NULL,
                        thumbnail_medium_path = NULL,
                        thumbnail_large_path = NULL,
                        thumbnail_xlarge_path = NULL
                """)
                reset_count = cursor.rowcount

            logger.info(f"Reset {reset_count} database records")

            progress_callback({
                'operation': 'resetting_database',
                'percentage': 20,
                'current_file': f'Reset {reset_count} records',
                'stats': {'reset': reset_count}
            })

            # Step 3: Scan output folder and add missing images to database
            logger.info("Step 3: Scanning output folder for images...")
            from ...tracking.prompt_tracker import PromptTracker

            tracker = PromptTracker(self.db)
            output_dir = Path.home() / 'ai-apps/ComfyUI/output'

            if output_dir.exists():
                image_files = []
                for ext in ['*.png', '*.jpg', '*.jpeg', '*.webp']:
                    image_files.extend(output_dir.rglob(ext))

                added_count = 0
                for idx, img_path in enumerate(image_files):
                    if cancel_event.is_set():
                        logger.info("Rebuild cancelled during scan")
                        break

                    # Try to add to database (will skip if already exists)
                    try:
                        tracker.track_generation(
                            image_path=str(img_path),
                            prompt="",  # Will be extracted from metadata if available
                            metadata={}
                        )
                        added_count += 1
                    except Exception as e:
                        pass  # Already exists, skip

                    if idx % 100 == 0:
                        progress_callback({
                            'operation': 'scanning_output',
                            'percentage': 20 + (idx / len(image_files) * 30),
                            'current_file': img_path.name,
                            'stats': {'scanned': idx + 1, 'total': len(image_files)}
                        })

                logger.info(f"Scanned {len(image_files)} files, added {added_count} new images")

            progress_callback({
                'operation': 'scanning_complete',
                'percentage': 50,
                'current_file': 'Scan complete',
                'stats': {'scanned': len(image_files) if 'image_files' in locals() else 0}
            })

            # Step 4: Get all images from database and create thumbnail tasks
            logger.info("Step 4: Preparing thumbnail generation for all images...")
            with get_db_connection(self.db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, image_path
                    FROM generated_images
                    WHERE image_path IS NOT NULL AND image_path != ''
                """)
                all_images = cursor.fetchall()

            # Create tasks for all size combinations
            from ...services.enhanced_thumbnail_service import ThumbnailTask
            tasks = []

            for db_id, image_path in all_images:
                source_path = Path(image_path)
                if not source_path.exists():
                    continue

                for size in sizes:
                    is_video = self.thumbnail_service.ffmpeg.is_video(source_path)
                    size_dims = self.thumbnail_service.base_generator.SIZES.get(size, (300, 300))

                    task = ThumbnailTask(
                        image_id=hashlib.md5(str(source_path).encode()).hexdigest(),
                        source_path=source_path,
                        thumbnail_path=self.thumbnail_service.get_thumbnail_path(source_path, size, image_id=db_id),
                        size=size_dims,
                        format=source_path.suffix.lower()[1:] if not is_video else 'jpg',
                        size_name=size,
                        is_video=is_video,
                        db_id=db_id
                    )
                    tasks.append(task)

            logger.info(f"Created {len(tasks)} thumbnail generation tasks")

            # Step 5: Generate all thumbnails
            logger.info("Step 5: Generating all thumbnails...")

            def gen_progress_callback(prog):
                if progress_callback:
                    progress_callback({
                        'operation': 'generating_all',
                        'percentage': 50 + (prog.get('completed', 0) / len(tasks) * 50),
                        'current_file': prog.get('current_file'),
                        'stats': {
                            'generated': prog.get('completed', 0),
                            'failed': prog.get('failed', 0),
                            'total': len(tasks)
                        }
                    })

            result = await self.thumbnail_service.generate_batch(
                tasks,
                progress_callback=gen_progress_callback,
                cancel_event=cancel_event
            )

            # Update task status
            status = 'cancelled' if cancel_event.is_set() else 'completed'
            summary = {
                'completed': result.get('completed', 0),
                'failed': result.get('failed', 0),
                'total': len(tasks),
                'deleted_old': deleted_count,
                'reset_database': reset_count,
                'scanned_files': len(image_files) if 'image_files' in locals() else 0
            }

            self.active_generations[task_id]['status'] = status
            self.active_generations[task_id]['result'] = {'stats': summary}

            logger.info(f"Rebuild-all task {task_id} {status}: {summary}")

            if self.realtime:
                message = f"Rebuilt {summary['completed']} thumbnails from scratch"
                toast_type = 'success' if summary['failed'] == 0 else 'warning'
                await self.realtime.send_toast(message, toast_type)

                if summary['completed'] > 0:
                    await self.realtime.notify_gallery_refresh()

        except Exception as e:
            logger.error(f"Background rebuild-all error for task {task_id}: {e}", exc_info=True)
            self.active_generations[task_id]['status'] = 'failed'
            self.active_generations[task_id]['error'] = str(e)

            if self.realtime:
                await self.realtime.send_toast(f'Rebuild All failed: {e}', 'error')

    async def cancel_generation(self, request: web.Request) -> web.Response:
        """Cancel an active thumbnail generation task.

        Args:
            request: HTTP request

        Returns:
            JSON response with cancellation status
        """
        try:
            data = await request.json()
            task_id = data.get('task_id')

            if not task_id:
                return web.json_response(
                    {'error': 'task_id is required'},
                    status=400
                )

            task = self.active_generations.get(task_id)
            if not task:
                return web.json_response(
                    {'error': f'Task {task_id} not found'},
                    status=404
                )

            # Set the cancel event if it exists
            cancel_event = task.get('cancel_event')
            if cancel_event and isinstance(cancel_event, threading.Event):
                cancel_event.set()
                logger.info(f"Cancellation requested for task {task_id}")
                return web.json_response({
                    'success': True,
                    'message': f'Task {task_id} cancellation requested'
                })
            else:
                return web.json_response({
                    'success': False,
                    'message': f'Task {task_id} cannot be cancelled (no cancel_event)'
                })

        except Exception as e:
            logger.error(f"Cancel generation error: {e}")
            return web.json_response(
                {'error': str(e)},
                status=500
            )

    async def get_task_status(self, request: web.Request) -> web.Response:
        """Get the status of a thumbnail generation task.

        Args:
            request: HTTP request

        Returns:
            JSON response with task status
        """
        task_id = request.match_info['task_id']

        task = self.active_generations.get(task_id)
        if not task:
            return web.json_response(
                {'error': f'Task {task_id} not found'},
                status=404
            )

        response = {
            'task_id': task_id,
            'status': task['status'],
            'progress': task.get('progress'),
            'result': task.get('result')
        }

        # Include error if present
        if 'error' in task:
            response['error'] = task['error']

        return web.json_response(response)

    async def rebuild_thumbnails(self, request: web.Request) -> web.Response:
        """Rebuild thumbnails for all images.

        Args:
            request: HTTP request

        Returns:
            JSON response with task ID
        """
        try:
            # Handle empty or invalid JSON body gracefully
            try:
                data = await request.json() if request.body_exists else {}
            except (json.JSONDecodeError, ValueError):
                data = {}

            sizes = data.get('sizes', ['small', 'medium'])
            skip_existing = data.get('skip_existing', True)

            # Get all image paths and their existing thumbnails from database
            image_data = []
            try:
                import sqlite3
                conn = DatabaseConnection.get_connection(self.db.db_path)
                cursor = conn.cursor()

                # Get ALL images with their thumbnail paths
                cursor.execute("""
                    SELECT id, image_path, filename,
                           thumbnail_small_path, thumbnail_medium_path, thumbnail_large_path
                    FROM generated_images
                    WHERE image_path IS NOT NULL AND image_path != ''
                """)

                images = cursor.fetchall()
                conn.close()

                for image in images:
                    db_id = image[0]  # id is at index 0
                    path = Path(image[1])  # image_path is at index 1
                    if not path.exists():
                        continue

                    # Determine which sizes need to be generated
                    missing_sizes = []
                    for size_name in sizes:
                        # Get the expected thumbnail path WITH database ID for unique naming
                        expected_thumbnail_path = self.thumbnail_service.get_thumbnail_path(path, size_name, image_id=db_id)

                        if skip_existing:
                            # Check if thumbnail file actually exists on disk
                            needs_generation = not expected_thumbnail_path.exists()
                        else:
                            # Regenerate all requested sizes
                            needs_generation = True

                        if needs_generation:
                            missing_sizes.append(size_name)

                    if missing_sizes:
                        image_data.append((db_id, path, missing_sizes))

                logger.info(f"Found {len(image_data)} images needing thumbnail generation")

            except Exception as e:
                logger.error(f"Error fetching images for rebuild: {e}")
                return web.json_response({
                    'error': 'Failed to fetch images from database',
                    'details': str(e)
                }, status=500)

            # Create tasks only for missing sizes
            all_tasks = []
            for db_id, path, missing_sizes in image_data:
                is_video = self.thumbnail_service.ffmpeg.is_video(path)

                for size_name in missing_sizes:
                    # Skip invalid size names
                    if size_name not in self.thumbnail_service.base_generator.SIZES:
                        logger.warning(f"Invalid size name '{size_name}' skipped")
                        continue

                    size_dims = self.thumbnail_service.base_generator.SIZES[size_name]
                    task = ThumbnailTask(
                        image_id=hashlib.md5(str(path).encode()).hexdigest(),
                        source_path=path,
                        thumbnail_path=self.thumbnail_service.get_thumbnail_path(path, size_name, image_id=db_id),
                        size=size_dims,
                        format=path.suffix.lower()[1:],
                        size_name=size_name,
                        db_id=db_id,
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

    async def _generate_with_tracking(
        self,
        task_id: str,
        tasks: List[ThumbnailTask],
        cancel_event: threading.Event
    ):
        """Generate thumbnails with progress tracking.

        Args:
            task_id: Unique task identifier
            tasks: List of thumbnail tasks to process
            cancel_event: Event for cancellation
        """
        try:
            # Validate event loop is running
            try:
                loop = asyncio.get_running_loop()
                logger.info(f"[{task_id}] Event loop validated and running")
            except RuntimeError as e:
                logger.error(f"[{task_id}] No event loop running: {e}")
                self.active_generations[task_id]['status'] = 'failed'
                self.active_generations[task_id]['error'] = 'No event loop available'
                return

            logger.info(f"[{task_id}] Starting thumbnail generation for {len(tasks)} tasks")

            completed = 0
            failed = 0
            total = len(tasks)
            start_time = time.time()

            # Process tasks
            for i, task in enumerate(tasks):
                # Check if cancelled
                if cancel_event.is_set():
                    logger.info(f"[{task_id}] Cancelled after {completed} completions")
                    break

                try:
                    # Log detailed progress at DEBUG level only
                    logger.debug(f"[{task_id}] Processing {i+1}/{total}: {task.source_path.name}")

                    # Generate thumbnail
                    task = await self.thumbnail_service.generate_thumbnail(task)

                    # Check if thumbnail generation was successful
                    if task.status == ThumbnailStatus.GENERATED:
                        # Update database with thumbnail path ONLY if generation succeeded
                        try:
                            column_map = {
                                'small': 'thumbnail_small_path',
                                'medium': 'thumbnail_medium_path',
                                'large': 'thumbnail_large_path',
                                'xlarge': 'thumbnail_xlarge_path'
                            }

                            if task.size_name in column_map and task.db_id is not None:
                                column_name = column_map[task.size_name]
                                # Use context manager for proper connection handling
                                with get_db_connection(self.db.db_path) as conn:
                                    cursor = conn.cursor()
                                    # Use db_id for precise update (handles duplicate filenames correctly!)
                                    cursor.execute(
                                        f"UPDATE generated_images SET {column_name} = ? WHERE id = ?",
                                        (str(task.thumbnail_path), task.db_id)
                                    )
                                    rows_affected = cursor.rowcount
                                    logger.info(f"[{task_id}] DB updated - ID {task.db_id}, {column_name}, rows: {rows_affected}")
                            else:
                                logger.warning(f"[{task_id}] DB update skipped - invalid size_name or db_id")
                        except Exception as db_error:
                            logger.error(f"[{task_id}] DB update failed for ID {task.db_id}: {db_error}", exc_info=True)

                        logger.debug(f"[{task_id}] Completed {i+1}/{total}: {task.source_path.name}")
                        completed += 1
                    elif task.status == ThumbnailStatus.FAILED:
                        logger.error(f"[{task_id}] Generation failed for {task.source_path}: {task.error}")
                        failed += 1
                    elif task.status == ThumbnailStatus.SKIPPED:
                        logger.info(f"[{task_id}] Generation skipped for {task.source_path}: {task.error}")
                        # Don't count skipped as completed or failed
                    else:
                        logger.warning(f"[{task_id}] Unexpected status {task.status} for {task.source_path}")
                        failed += 1

                except Exception as e:
                    logger.error(f"[{task_id}] Failed to generate thumbnail for {task.source_path}: {e}", exc_info=True)
                    failed += 1

                # Update progress
                def update_progress():
                    # Calculate time remaining
                    elapsed = time.time() - start_time
                    if i + 1 > 0:
                        avg_time_per_task = elapsed / (i + 1)
                        remaining_tasks = total - (i + 1)
                        estimated_time_remaining = int(avg_time_per_task * remaining_tasks)
                    else:
                        estimated_time_remaining = 0

                    self.active_generations[task_id]['progress'] = {
                        'current': i + 1,
                        'total': total,
                        'completed': completed,
                        'failed': failed,
                        'percentage': round(((i + 1) / total) * 100, 1),
                        'estimated_time_remaining': estimated_time_remaining
                    }

                loop.call_soon_threadsafe(update_progress)

                # Send realtime updates every 10 tasks
                if self.realtime and (i + 1) % 10 == 0:
                    # Calculate time remaining for SSE event
                    elapsed = time.time() - start_time
                    if i + 1 > 0:
                        avg_time_per_task = elapsed / (i + 1)
                        remaining_tasks = total - (i + 1)
                        estimated_time_remaining = int(avg_time_per_task * remaining_tasks)
                    else:
                        estimated_time_remaining = 0

                    await self.realtime.send_progress(
                        'thumbnail_generation',
                        round(((i + 1) / total) * 100, 1),
                        f'Generated {completed} of {total} thumbnails',
                        task_id=task_id,
                        stats={
                            'completed': completed,
                            'failed': failed,
                            'total': total,
                            'estimated_time_remaining': estimated_time_remaining
                        }
                    )

            # Update task status
            status = 'cancelled' if cancel_event.is_set() else 'completed'
            self.active_generations[task_id]['status'] = status
            self.active_generations[task_id]['result'] = {
                'completed': completed,
                'failed': failed,
                'total': total,
                'cancelled': cancel_event.is_set()
            }

            logger.info(f"Generation task {task_id} {status}: {completed} completed, {failed} failed")

            if self.realtime:
                await self.realtime.send_progress(
                    'thumbnail_generation',
                    100,
                    'Generation complete' if status == 'completed' else 'Generation cancelled',
                    task_id=task_id,
                    result=self.active_generations[task_id]['result']
                )

                if status == 'completed':
                    message = f"Generated {completed} thumbnails"
                    if failed > 0:
                        message += f", {failed} failed"
                    await self.realtime.send_toast(message, 'success' if failed == 0 else 'warning')

        except Exception as e:
            logger.error(f"Background generation error for task {task_id}: {e}", exc_info=True)
            self.active_generations[task_id]['status'] = 'failed'
            self.active_generations[task_id]['error'] = str(e)

            if self.realtime:
                await self.realtime.send_toast(f'Thumbnail generation failed: {e}', 'error')