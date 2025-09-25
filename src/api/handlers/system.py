"""System management API handlers."""

from aiohttp import web
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any


class SystemHandlers:
    """Handles system management API endpoints."""
    
    def __init__(self, db_manager, config):
        """Initialize with database manager and configuration."""
        self.db_manager = db_manager
        self.config = config
    
    async def health_check(self, request) -> web.Response:
        """GET /api/prompt_manager/health - Health check."""
        try:
            # Check database connection
            db_healthy = self.db_manager.health_check()
            
            # Check disk space
            stat = shutil.disk_usage(self.config.data_dir)
            disk_free_gb = stat.free / (1024**3)
            
            return web.json_response({
                'success': True,
                'status': 'healthy' if db_healthy else 'degraded',
                'database': 'connected' if db_healthy else 'error',
                'disk_free_gb': round(disk_free_gb, 2),
                'timestamp': datetime.now().isoformat()
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'status': 'unhealthy',
                'error': str(e)
            }, status=500)
    
    async def get_stats(self, request) -> web.Response:
        """GET /api/prompt_manager/stats - System statistics."""
        try:
            stats = self.db_manager.get_statistics()
            
            return web.json_response({
                'success': True,
                'stats': {
                    'total_prompts': stats.get('prompt_count', 0),
                    'total_images': stats.get('image_count', 0),
                    'total_collections': stats.get('collection_count', 0),
                    'database_size_mb': stats.get('db_size_mb', 0),
                    'most_used_models': stats.get('top_models', []),
                    'recent_activity': stats.get('recent_activity', [])
                }
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)
    
    async def create_backup(self, request) -> web.Response:
        """POST /api/prompt_manager/backup - Create backup."""
        try:
            data = await request.json() if request.body_exists else {}
            include_images = data.get('include_images', False)
            
            backup_path = self.db_manager.create_backup(
                include_images=include_images
            )
            
            return web.json_response({
                'success': True,
                'backup_path': str(backup_path),
                'timestamp': datetime.now().isoformat()
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)
    
    async def restore_backup(self, request) -> web.Response:
        """POST /api/prompt_manager/restore - Restore from backup."""
        try:
            data = await request.json()
            backup_path = data.get('backup_path')
            
            if not backup_path:
                return web.json_response({
                    'success': False,
                    'error': 'backup_path is required'
                }, status=400)
            
            success = self.db_manager.restore_backup(backup_path)
            
            return web.json_response({
                'success': success,
                'message': 'Backup restored successfully' if success else 'Restore failed'
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)
    
    async def vacuum_database(self, request) -> web.Response:
        """POST /api/prompt_manager/vacuum - Optimize database."""
        try:
            before_size = self.db_manager.get_database_size()
            self.db_manager.vacuum()
            after_size = self.db_manager.get_database_size()
            
            saved_mb = (before_size - after_size) / (1024 * 1024)
            
            return web.json_response({
                'success': True,
                'before_size_mb': round(before_size / (1024 * 1024), 2),
                'after_size_mb': round(after_size / (1024 * 1024), 2),
                'saved_mb': round(saved_mb, 2)
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)