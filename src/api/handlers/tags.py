"""Tag management API handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web
from ...database.connection_helper import get_db_connection

if TYPE_CHECKING:
    from ..routes import PromptManagerAPI


class TagHandlers:
    """Handles tag-related API endpoints."""

    def __init__(self, api: "PromptManagerAPI"):
        """Initialize with API instance for access to tag service.

        Args:
            api: PromptManagerAPI instance providing access to services
        """
        self.api = api
        self.logger = api.logger

        # Initialize tag service
        from ...services.tag_service import TagService
        self.tag_service = TagService(api.db_path)

    async def list_tags(self, request: web.Request) -> web.Response:
        """List all tags with usage counts.

        GET /api/v1/tags?limit=500

        Query Parameters:
            limit (int): Maximum number of tags to return (default: 500)

        Returns:
            JSON response with list of tags
        """
        try:
            limit = int(request.query.get('limit', 500))
        except (TypeError, ValueError):
            limit = 500

        try:
            tags = self.tag_service.get_all_tags(limit)

            return web.json_response({
                'success': True,
                'data': tags,
                'total': len(tags)
            })

        except Exception as e:
            self.logger.error(f"Error listing tags: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def search_tags(self, request: web.Request) -> web.Response:
        """Search tags by partial name match.

        GET /api/v1/tags/search?q=query&limit=50

        Query Parameters:
            q (str): Search query string (required)
            limit (int): Maximum number of results (default: 50)

        Returns:
            JSON response with matching tags
        """
        query = request.query.get('q', '')
        if not query:
            return web.json_response({
                'success': False,
                'error': 'Query parameter "q" is required'
            }, status=400)

        try:
            limit = int(request.query.get('limit', 50))
        except (TypeError, ValueError):
            limit = 50

        try:
            tags = self.tag_service.search_tags(query, limit)

            return web.json_response({
                'success': True,
                'data': tags,
                'query': query
            })

        except Exception as e:
            self.logger.error(f"Error searching tags: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_popular_tags(self, request: web.Request) -> web.Response:
        """Get most popular tags by usage count.

        GET /api/v1/tags/popular?limit=20

        Query Parameters:
            limit (int): Number of top tags to return (default: 20)

        Returns:
            JSON response with popular tags
        """
        try:
            limit = int(request.query.get('limit', 20))
        except (TypeError, ValueError):
            limit = 20

        try:
            tags = self.tag_service.get_popular_tags(limit)

            return web.json_response({
                'success': True,
                'data': tags
            })

        except Exception as e:
            self.logger.error(f"Error getting popular tags: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_tag(self, request: web.Request) -> web.Response:
        """Get a specific tag by ID.

        GET /api/v1/tags/{id}

        Returns:
            JSON response with tag details
        """
        try:
            tag_id = int(request.match_info['id'])
        except (KeyError, ValueError):
            return web.json_response({
                'success': False,
                'error': 'Invalid tag ID'
            }, status=400)

        try:
            # Get tag from database
            import sqlite3
            with get_db_connection(self.api.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT id, name, usage_count, created_at, updated_at
                    FROM tags
                    WHERE id = ?
                """, (tag_id,))
                row = cursor.fetchone()

                if not row:
                    return web.json_response({
                        'success': False,
                        'error': 'Tag not found'
                    }, status=404)

                return web.json_response({
                    'success': True,
                    'data': dict(row)
                })

        except Exception as e:
            self.logger.error(f"Error getting tag {tag_id}: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def create_tag(self, request: web.Request) -> web.Response:
        """Create a new tag.

        POST /api/v1/tags
        Body: {name: str}

        Returns:
            JSON response with created tag
        """
        try:
            data = await request.json()
        except Exception:
            return web.json_response({
                'success': False,
                'error': 'Invalid JSON'
            }, status=400)

        name = data.get('name', '').strip()
        if not name:
            return web.json_response({
                'success': False,
                'error': 'Tag name is required'
            }, status=400)

        try:
            tag_id = self.tag_service.create_tag(name)

            if tag_id:
                # Get the created tag
                tag = self.tag_service.get_tag_by_name(name)
                return web.json_response({
                    'success': True,
                    'data': tag
                }, status=201)
            else:
                return web.json_response({
                    'success': False,
                    'error': 'Failed to create tag'
                }, status=500)

        except Exception as e:
            self.logger.error(f"Error creating tag '{name}': {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def delete_tag(self, request: web.Request) -> web.Response:
        """Delete a tag.

        DELETE /api/v1/tags/{id}

        Returns:
            JSON response with success status
        """
        try:
            tag_id = int(request.match_info['id'])
        except (KeyError, ValueError):
            return web.json_response({
                'success': False,
                'error': 'Invalid tag ID'
            }, status=400)

        try:
            success = self.tag_service.delete_tag(tag_id)

            if success:
                return web.json_response({
                    'success': True,
                    'message': 'Tag deleted successfully'
                })
            else:
                return web.json_response({
                    'success': False,
                    'error': 'Tag not found or failed to delete'
                }, status=404)

        except Exception as e:
            self.logger.error(f"Error deleting tag {tag_id}: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def sync_tags(self, request: web.Request) -> web.Response:
        """Synchronize tags from prompts table.

        POST /api/v1/tags/sync

        Returns:
            JSON response with number of tags synced
        """
        try:
            count = self.tag_service.sync_tags_from_prompts()

            return web.json_response({
                'success': True,
                'message': f'Synced {count} tags from prompts',
                'count': count
            })

        except Exception as e:
            self.logger.error(f"Error syncing tags: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def update_usage_counts(self, request: web.Request) -> web.Response:
        """Recalculate usage counts for all tags.

        POST /api/v1/tags/update-counts

        Returns:
            JSON response with number of tags updated
        """
        try:
            count = self.tag_service.update_tag_usage_counts()

            return web.json_response({
                'success': True,
                'message': f'Updated usage counts for {count} tags',
                'count': count
            })

        except Exception as e:
            self.logger.error(f"Error updating tag usage counts: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def cleanup_unused_tags(self, request: web.Request) -> web.Response:
        """Delete tags with zero usage count.

        POST /api/v1/tags/cleanup

        Query Parameters:
            threshold (int): Usage count threshold (default: 0)

        Returns:
            JSON response with number of tags deleted
        """
        try:
            threshold = int(request.query.get('threshold', 0))
        except (TypeError, ValueError):
            threshold = 0

        try:
            count = self.tag_service.cleanup_unused_tags(threshold)

            return web.json_response({
                'success': True,
                'message': f'Cleaned up {count} unused tags',
                'count': count
            })

        except Exception as e:
            self.logger.error(f"Error cleaning up tags: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_tag_stats(self, request: web.Request) -> web.Response:
        """Get statistics about tags.

        GET /api/v1/tags/stats

        Returns:
            JSON response with tag statistics
        """
        try:
            total_tags = self.tag_service.get_tag_count()
            popular_tags = self.tag_service.get_popular_tags(10)

            # Calculate total usage
            total_usage = sum(tag['usage_count'] for tag in popular_tags)

            return web.json_response({
                'success': True,
                'data': {
                    'total_tags': total_tags,
                    'total_usage': total_usage,
                    'top_tags': popular_tags
                }
            })

        except Exception as e:
            self.logger.error(f"Error getting tag stats: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)