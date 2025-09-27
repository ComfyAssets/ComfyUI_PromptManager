"""
Optimized Statistics API Routes
Server-side processing with caching and efficient queries.
"""

from aiohttp import web
import json
import time
from typing import Dict, Any
from src.services.stats_service import StatsService


class StatsRoutes:
    """Optimized stats API routes with server-side processing."""

    def __init__(self, db_path: str):
        self.stats_service = StatsService(db_path, cache_ttl=300.0)
        self._last_update = 0

    async def get_overview(self, request: web.Request) -> web.Response:
        """
        Get stats overview - all processing server-side.
        Cached for 5 minutes by default.
        """
        try:
            # Check if force refresh requested
            force = request.query.get('force', '').lower() == 'true'

            # Get stats from service (uses cache)
            stats = self.stats_service.get_overview(force=force)

            return web.json_response({
                'success': True,
                'data': stats,
                'cached': not force and time.time() - self._last_update < 300
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_category_stats(self, request: web.Request) -> web.Response:
        """Get stats for specific category."""
        try:
            category = request.match_info.get('category', '')
            if not category:
                return web.json_response({
                    'success': False,
                    'error': 'Category required'
                }, status=400)

            stats = self.stats_service.get_category_stats(category)

            return web.json_response({
                'success': True,
                'data': stats
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_tag_stats(self, request: web.Request) -> web.Response:
        """Get stats for specific tag."""
        try:
            tag = request.match_info.get('tag', '')
            if not tag:
                return web.json_response({
                    'success': False,
                    'error': 'Tag required'
                }, status=400)

            stats = self.stats_service.get_tag_stats(tag)

            return web.json_response({
                'success': True,
                'data': stats
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_recent_activity(self, request: web.Request) -> web.Response:
        """Get recent activity stats (paginated)."""
        try:
            # Pagination params
            page = int(request.query.get('page', 0))
            size = min(int(request.query.get('size', 50)), 100)  # Max 100

            # Get paginated data
            activity = self.stats_service.get_recent_activity(page, size)

            return web.json_response({
                'success': True,
                'data': activity,
                'page': page,
                'size': size
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def invalidate_cache(self, request: web.Request) -> web.Response:
        """Force cache invalidation (admin only)."""
        try:
            # TODO: Add authentication check here

            self.stats_service._cached_snapshot = None
            self.stats_service._cached_at = 0

            return web.json_response({
                'success': True,
                'message': 'Cache invalidated'
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    def setup_routes(self, app: web.Application):
        """Setup all stats routes."""
        app.router.add_get('/api/stats/overview', self.get_overview)
        app.router.add_get('/api/stats/category/{category}', self.get_category_stats)
        app.router.add_get('/api/stats/tag/{tag}', self.get_tag_stats)
        app.router.add_get('/api/stats/recent', self.get_recent_activity)
        app.router.add_post('/api/stats/invalidate', self.invalidate_cache)