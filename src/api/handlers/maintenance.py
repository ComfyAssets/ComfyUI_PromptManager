"""Database maintenance and optimization API handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from ..routes import PromptManagerAPI


class MaintenanceHandlers:
    """Handles all database maintenance and optimization endpoints."""

    def __init__(self, api: PromptManagerAPI):
        """Initialize with API instance for access to repos/services.

        Args:
            api: PromptManagerAPI instance providing access to repositories and services
        """
        self.api = api
        self.logger = api.logger
        self.db_path = api.db_path
        self.realtime = api.realtime

    async def get_maintenance_stats(self, request: web.Request) -> web.Response:
        """Get database maintenance statistics.

        GET /api/v1/maintenance/stats
        """
        try:
            from ...services.maintenance_service import MaintenanceService
            service = MaintenanceService(self.api)
            stats = service.get_statistics()

            return web.json_response({
                "success": True,
                "data": stats
            })
        except Exception as e:
            self.logger.error(f"Error getting maintenance stats: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def remove_duplicates(self, request: web.Request) -> web.Response:
        """Remove duplicate image links.

        POST /api/v1/maintenance/deduplicate
        """
        try:
            from ...services.maintenance_service import MaintenanceService
            service = MaintenanceService(self.api)
            result = service.remove_duplicates()

            # Broadcast realtime update
            if hasattr(self.api, 'sse'):
                await self.realtime.send_toast(
                    result.get('message', 'Duplicates removed'),
                    'success' if result.get('success') else 'error'
                )

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error removing duplicates: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def clean_orphans(self, request: web.Request) -> web.Response:
        """Remove orphaned image links.

        POST /api/v1/maintenance/clean-orphans
        """
        try:
            from ...services.maintenance_service import MaintenanceService
            service = MaintenanceService(self.api)
            result = service.clean_orphans()

            # Broadcast realtime update
            if hasattr(self.api, 'sse'):
                await self.realtime.send_toast(
                    result.get('message', 'Orphans cleaned'),
                    'success' if result.get('success') else 'error'
                )

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error cleaning orphans: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def validate_paths(self, request: web.Request) -> web.Response:
        """Validate file paths in database.

        POST /api/v1/maintenance/validate-paths
        """
        try:
            from ...services.maintenance_service import MaintenanceService
            service = MaintenanceService(self.api)
            result = service.validate_paths()

            # Broadcast realtime update
            if hasattr(self.api, 'sse'):
                await self.realtime.send_toast(
                    result.get('message', 'Paths validated'),
                    'success' if result.get('success') else 'error'
                )

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error validating paths: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def optimize_database(self, request: web.Request) -> web.Response:
        """Optimize database.

        POST /api/v1/maintenance/optimize
        """
        try:
            from ...services.maintenance_service import MaintenanceService
            service = MaintenanceService(self.api)
            result = service.optimize_database()

            # Broadcast realtime update
            if hasattr(self.api, 'sse'):
                await self.realtime.send_toast(
                    result.get('message', 'Database optimized'),
                    'success' if result.get('success') else 'error'
                )

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error optimizing database: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def create_backup(self, request: web.Request) -> web.Response:
        """Create database backup.

        POST /api/v1/maintenance/backup
        """
        try:
            from ...services.maintenance_service import MaintenanceService
            service = MaintenanceService(self.api)
            result = service.create_backup()

            # Broadcast realtime update
            if hasattr(self.api, 'sse'):
                await self.realtime.send_toast(
                    result.get('message', 'Backup created'),
                    'success' if result.get('success') else 'error'
                )

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error creating backup: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def fix_broken_links(self, request: web.Request) -> web.Response:
        """Fix broken image links by finding relocated files.

        POST /api/v1/maintenance/fix-broken-links
        """
        try:
            from ...services.maintenance_service import MaintenanceService
            service = MaintenanceService(self.api)
            result = service.fix_broken_links()

            # Broadcast realtime update
            if hasattr(self.api, 'sse'):
                await self.realtime.send_toast(
                    result.get('message', 'Fixed broken links'),
                    'success' if result.get('success') else 'error'
                )

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error fixing broken links: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def remove_missing_files(self, request: web.Request) -> web.Response:
        """Remove entries with missing files.

        POST /api/v1/maintenance/remove-missing
        """
        try:
            from ...services.maintenance_service import MaintenanceService
            service = MaintenanceService(self.api)
            result = service.remove_missing_files()

            # Broadcast realtime update
            if hasattr(self.api, 'sse'):
                await self.realtime.send_toast(
                    result.get('message', 'Missing files removed'),
                    'success' if result.get('success') else 'error'
                )

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error removing missing files: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def refresh_file_metadata(self, request: web.Request) -> web.Response:
        """Recalculate file metadata such as size and dimensions.

        POST /api/v1/maintenance/update-file-metadata?batch_size=500
        """
        try:
            from ...services.maintenance_service import MaintenanceService

            batch_size_param = request.query.get('batch_size', '500') or '500'
            try:
                batch_size = max(1, int(batch_size_param))
            except ValueError:
                batch_size = 500

            service = MaintenanceService(self.api)
            result = service.refresh_file_metadata(batch_size=batch_size)

            if hasattr(self.api, 'sse'):
                await self.realtime.send_toast(
                    result.get('message', 'File metadata refreshed'),
                    'success' if result.get('success') else 'error'
                )

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error refreshing file metadata: {e}")
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500,
            )

    async def check_integrity(self, request: web.Request) -> web.Response:
        """Check database integrity.

        POST /api/v1/maintenance/check-integrity
        """
        try:
            from ...services.maintenance_service import MaintenanceService
            service = MaintenanceService(self.api)
            result = service.check_integrity()

            # Broadcast realtime update
            if hasattr(self.api, 'sse'):
                await self.realtime.send_toast(
                    result.get('message', 'Integrity checked'),
                    'success' if result.get('success') else 'error'
                )

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error checking integrity: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def reindex_database(self, request: web.Request) -> web.Response:
        """Reindex database.

        POST /api/v1/maintenance/reindex
        """
        try:
            from ...services.maintenance_service import MaintenanceService
            service = MaintenanceService(self.api)
            result = service.reindex_database()

            # Broadcast realtime update
            if hasattr(self.api, 'sse'):
                await self.realtime.send_toast(
                    result.get('message', 'Database reindexed'),
                    'success' if result.get('success') else 'error'
                )

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error reindexing database: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def tag_missing_images(self, request: web.Request) -> web.Response:
        """Tag images with missing files.

        POST /api/v1/maintenance/tag-missing-images
        Body: {action?: "tag" | "untag" | "summary"}
        """
        try:
            from ...services.missing_images_tagger import MissingImagesTagger
            tagger = MissingImagesTagger(self.db_path)

            # Get the action from request
            data = await request.json() if request.body_exists else {}
            action = data.get('action', 'tag')  # 'tag', 'untag', or 'summary'

            if action == 'summary':
                result = tagger.get_missing_images_summary()
            elif action == 'untag':
                stats = tagger.remove_missing_tag()
                result = {
                    "success": True,
                    "message": f"Removed 'missing' tag from {stats['tag_removed']} images that were found",
                    "stats": stats
                }
            else:  # Default to 'tag'
                stats = tagger.tag_missing_images()
                result = {
                    "success": True,
                    "message": f"Tagged {stats['tagged']} missing images out of {stats['total_missing']} total missing",
                    "stats": stats
                }

            # Broadcast realtime update
            if hasattr(self, 'realtime') and action != 'summary':
                await self.realtime.send_toast(
                    result.get('message', 'Missing images processed'),
                    'success'
                )

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error tagging missing images: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def export_backup(self, request: web.Request) -> web.Response:
        """Export database backup.

        POST /api/v1/maintenance/export
        """
        try:
            from ...services.maintenance_service import MaintenanceService
            service = MaintenanceService(self.api)
            result = service.export_backup()

            # Broadcast realtime update
            if hasattr(self.api, 'sse'):
                await self.realtime.send_toast(
                    result.get('message', 'Backup exported'),
                    'success' if result.get('success') else 'error'
                )

            return web.json_response(result)
        except Exception as e:
            self.logger.error(f"Error exporting backup: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def calculate_epic_stats(self, request: web.Request) -> web.Response:
        """Calculate epic statistics.

        POST /api/v1/maintenance/calculate-epic-stats
        """
        try:
            from ...services.epic_stats_calculator import EpicStatsCalculator
            calculator = EpicStatsCalculator(self.db_path)

            # This could be a long-running operation
            stats = calculator.calculate_all_stats(progress_callback=None)

            return web.json_response({
                "success": True,
                "message": "Epic stats calculated successfully",
                "data": stats
            })
        except Exception as e:
            self.logger.error(f"Error calculating epic stats: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def calculate_word_cloud(self, request: web.Request) -> web.Response:
        """Calculate word cloud data.

        POST /api/v1/maintenance/calculate-word-cloud
        """
        try:
            from ...services.word_cloud_service import WordCloudService
            service = WordCloudService(self.db_path)

            # Calculate word frequencies
            frequencies = service.calculate_word_frequencies(limit=100)
            metadata = service.get_metadata()

            return web.json_response({
                "success": True,
                "message": f"Word cloud data generated with {len(frequencies)} unique words",
                "data": {
                    "frequencies": frequencies,
                    "metadata": metadata
                }
            })
        except Exception as e:
            self.logger.error(f"Error calculating word cloud: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)
