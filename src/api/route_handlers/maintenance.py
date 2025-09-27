"""
Maintenance operations for PromptManager.
Manual operations that users can trigger for database maintenance.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, Any
from aiohttp import web
import logging

logger = logging.getLogger(__name__)


class MaintenanceAPI:
    """API handlers for maintenance operations."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.extraction_in_progress = False
        self.extraction_progress = {}
        self.extraction_cancel = False

    async def extract_metadata_from_images(self, request: web.Request) -> web.Response:
        """
        Start metadata extraction from ComfyUI output images.
        This is a long-running operation that processes images in the background.
        """
        if self.extraction_in_progress:
            return web.json_response({
                "success": False,
                "error": "Extraction already in progress"
            }, status=409)

        try:
            data = await request.json()
            directory = data.get("directory", "")
            max_files = data.get("max_files", 1000)  # Limit to prevent overload

            if not directory:
                # Default to ComfyUI output directory
                directory = str(Path.home() / "ai-apps/ComfyUI-3.12/output")
                if not Path(directory).exists():
                    return web.json_response({
                        "success": False,
                        "error": f"Directory not found: {directory}"
                    }, status=404)

            # Start extraction in background
            asyncio.create_task(self._run_extraction(directory, max_files))

            return web.json_response({
                "success": True,
                "message": "Metadata extraction started",
                "directory": directory,
                "max_files": max_files
            })

        except Exception as e:
            logger.error(f"Failed to start metadata extraction: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def get_extraction_progress(self, request: web.Request) -> web.Response:
        """Get progress of metadata extraction operation."""
        return web.json_response({
            "success": True,
            "in_progress": self.extraction_in_progress,
            "progress": self.extraction_progress
        })

    async def cancel_extraction(self, request: web.Request) -> web.Response:
        """Cancel ongoing metadata extraction."""
        if not self.extraction_in_progress:
            return web.json_response({
                "success": False,
                "error": "No extraction in progress"
            }, status=400)

        self.extraction_cancel = True
        return web.json_response({
            "success": True,
            "message": "Extraction cancellation requested"
        })

    async def _run_extraction(self, directory: str, max_files: int):
        """Run metadata extraction in background."""
        self.extraction_in_progress = True
        self.extraction_cancel = False
        self.extraction_progress = {
            "status": "starting",
            "current": 0,
            "total": 0,
            "processed": 0,
            "extracted": 0,
            "errors": 0,
            "current_file": "",
            "start_time": time.time()
        }

        try:
            from src.services.workflow_metadata_extractor import WorkflowMetadataExtractor
            extractor = WorkflowMetadataExtractor(self.db_path)

            # Count PNG files
            png_files = list(Path(directory).glob("*.png"))[:max_files]
            self.extraction_progress["total"] = len(png_files)
            self.extraction_progress["status"] = "processing"

            for i, png_path in enumerate(png_files):
                if self.extraction_cancel:
                    self.extraction_progress["status"] = "cancelled"
                    break

                self.extraction_progress["current"] = i + 1
                self.extraction_progress["current_file"] = png_path.name

                try:
                    # Extract metadata
                    params = extractor.extract_from_png_metadata(str(png_path))

                    if params and params.get("positive"):
                        # Save to database
                        if extractor.save_to_database(params, str(png_path)):
                            self.extraction_progress["extracted"] += 1

                    self.extraction_progress["processed"] += 1

                except Exception as e:
                    logger.debug(f"Error extracting from {png_path.name}: {e}")
                    self.extraction_progress["errors"] += 1

                # Small delay to prevent overwhelming the system
                await asyncio.sleep(0.01)

            # Calculate stats
            elapsed = time.time() - self.extraction_progress["start_time"]
            self.extraction_progress["elapsed_time"] = elapsed
            self.extraction_progress["status"] = "completed"
            self.extraction_progress["message"] = (
                f"Processed {self.extraction_progress['processed']} files, "
                f"extracted metadata from {self.extraction_progress['extracted']} images"
            )

        except Exception as e:
            logger.error(f"Extraction failed: {e}")
            self.extraction_progress["status"] = "error"
            self.extraction_progress["error"] = str(e)

        finally:
            self.extraction_in_progress = False

    async def optimize_database(self, request: web.Request) -> web.Response:
        """Run database optimization (VACUUM, ANALYZE, etc.)."""
        try:
            import sqlite3

            conn = sqlite3.connect(self.db_path)

            # Run optimization commands
            conn.execute("PRAGMA optimize")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            # Get database stats
            stats = {}
            stats["page_count"] = conn.execute("PRAGMA page_count").fetchone()[0]
            stats["page_size"] = conn.execute("PRAGMA page_size").fetchone()[0]
            stats["size_mb"] = (stats["page_count"] * stats["page_size"]) / (1024 * 1024)

            # Check integrity
            result = conn.execute("PRAGMA integrity_check").fetchone()
            stats["integrity"] = result[0] if result else "unknown"

            conn.close()

            return web.json_response({
                "success": True,
                "message": "Database optimized",
                "stats": stats
            })

        except Exception as e:
            logger.error(f"Database optimization failed: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def rebuild_stats_cache(self, request: web.Request) -> web.Response:
        """Force rebuild of stats cache."""
        try:
            from src.services.stats_cache_service import get_stats_cache_service

            service = get_stats_cache_service(self.db_path)
            service.force_rebuild()

            return web.json_response({
                "success": True,
                "message": "Stats cache rebuilt successfully"
            })

        except Exception as e:
            logger.error(f"Stats rebuild failed: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    def add_routes(self, routes):
        """Add maintenance routes to the app."""
        routes.post('/api/v1/maintenance/extract-metadata')(self.extract_metadata_from_images)
        routes.get('/api/v1/maintenance/extraction-progress')(self.get_extraction_progress)
        routes.post('/api/v1/maintenance/cancel-extraction')(self.cancel_extraction)
        routes.post('/api/v1/maintenance/optimize-database')(self.optimize_database)
        routes.post('/api/v1/maintenance/rebuild-stats')(self.rebuild_stats_cache)