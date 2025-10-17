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

from ...services.word_cloud_service import WordCloudService
from ...services.epic_stats_calculator import EpicStatsCalculator

logger = logging.getLogger(__name__)


class MaintenanceAPI:
    """API handlers for maintenance operations."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.extraction_in_progress = False
        self.extraction_progress = {}
        self.extraction_cancel = False
        self.word_cloud_service = WordCloudService(db_path)
        self.epic_stats_calculator = EpicStatsCalculator(db_path)
        self.stats_calculation_in_progress = False
        self.stats_calculation_progress = {}

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
                # Default to ComfyUI output directory using auto-detection
                try:
                    from utils.core.file_system import get_file_system
                    fs = get_file_system()
                    comfy_root = fs.resolve_comfyui_root()
                    directory = str(comfy_root / "output")
                except Exception as e:
                    logger.error(f"Could not auto-detect ComfyUI root: {e}")
                    return web.json_response({
                        "success": False,
                        "error": "Could not find ComfyUI output directory. Please specify 'directory' in request."
                    }, status=400)

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
            from ...services.workflow_metadata_extractor import WorkflowMetadataExtractor
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

            conn = DatabaseConnection.get_connection(self.db_path)

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
            from ...services.stats_cache_service import get_stats_cache_service

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

    async def recalculate_word_cloud(self, request: web.Request) -> web.Response:
        """
        Recalculate word cloud data from all prompts.
        This pre-calculates and caches the data for instant stats page loading.
        """
        try:
            # Run recalculation
            start_time = time.time()
            frequencies = self.word_cloud_service.calculate_word_frequencies(limit=100)
            calculation_time = time.time() - start_time

            # Get metadata about the calculation
            metadata = self.word_cloud_service.get_metadata()

            return web.json_response({
                "success": True,
                "message": f"Word cloud recalculated successfully in {calculation_time:.2f}s",
                "words_count": len(frequencies),
                "prompts_analyzed": metadata.get('total_prompts_analyzed', 0),
                "words_processed": metadata.get('total_words_processed', 0)
            })

        except Exception as e:
            logger.error(f"Failed to recalculate word cloud: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def get_word_cloud_status(self, request: web.Request) -> web.Response:
        """Get the current status of word cloud cache."""
        try:
            metadata = self.word_cloud_service.get_metadata()
            needs_recalc = self.word_cloud_service.needs_recalculation(hours=24)

            return web.json_response({
                "success": True,
                "last_calculated": metadata.get('last_calculated'),
                "total_prompts_analyzed": metadata.get('total_prompts_analyzed', 0),
                "total_words_processed": metadata.get('total_words_processed', 0),
                "calculation_time_ms": metadata.get('calculation_time_ms', 0),
                "needs_recalculation": needs_recalc
            })

        except Exception as e:
            logger.error(f"Failed to get word cloud status: {e}")
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def calculate_epic_stats(self, request: web.Request) -> web.Response:
        """
        Calculate comprehensive statistics for the dashboard.
        This is a heavy operation that analyzes all data in the database.
        """
        if self.stats_calculation_in_progress:
            return web.json_response({
                "success": False,
                "error": "Stats calculation already in progress"
            }, status=409)

        try:
            self.stats_calculation_in_progress = True
            self.stats_calculation_progress = {
                'percent': 0,
                'message': 'Starting epic stats calculation...'
            }

            # Run calculation in background
            asyncio.create_task(self._run_epic_stats_calculation())

            return web.json_response({
                "success": True,
                "message": "Epic stats calculation started"
            })

        except Exception as e:
            logger.error(f"Failed to start epic stats calculation: {e}")
            self.stats_calculation_in_progress = False
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def _run_epic_stats_calculation(self):
        """Run epic stats calculation in background."""
        try:
            def progress_callback(percent, message):
                self.stats_calculation_progress = {
                    'percent': percent,
                    'message': message
                }

            # Run the calculation
            results = self.epic_stats_calculator.calculate_all_stats(progress_callback)

            if results['success']:
                self.stats_calculation_progress = {
                    'percent': 100,
                    'message': f"Completed! Calculated {results['stats_calculated']} statistics in {results['calculation_time']:.2f}s"
                }
                # Also trigger word cloud calculation
                self.word_cloud_service.calculate_word_frequencies(limit=100)
            else:
                self.stats_calculation_progress = {
                    'percent': 100,
                    'message': f"Failed: {', '.join(results['errors'])}"
                }

        except Exception as e:
            logger.error(f"Epic stats calculation failed: {e}")
            self.stats_calculation_progress = {
                'percent': 100,
                'message': f"Error: {str(e)}"
            }
        finally:
            self.stats_calculation_in_progress = False

    async def get_stats_calculation_progress(self, request: web.Request) -> web.Response:
        """Get progress of stats calculation."""
        return web.json_response({
            "success": True,
            "in_progress": self.stats_calculation_in_progress,
            "progress": self.stats_calculation_progress
        })

    def add_routes(self, routes):
        """Add maintenance routes to the app."""
        routes.post('/api/v1/maintenance/extract-metadata')(self.extract_metadata_from_images)
        routes.get('/api/v1/maintenance/extraction-progress')(self.get_extraction_progress)
        routes.post('/api/v1/maintenance/cancel-extraction')(self.cancel_extraction)
        routes.post('/api/v1/maintenance/optimize-database')(self.optimize_database)
        routes.post('/api/v1/maintenance/rebuild-stats')(self.rebuild_stats_cache)
        routes.post('/api/v1/maintenance/recalculate-word-cloud')(self.recalculate_word_cloud)
        routes.get('/api/v1/maintenance/word-cloud-status')(self.get_word_cloud_status)
        routes.post('/api/v1/maintenance/calculate-epic-stats')(self.calculate_epic_stats)
        routes.get('/api/v1/maintenance/stats-calculation-progress')(self.get_stats_calculation_progress)