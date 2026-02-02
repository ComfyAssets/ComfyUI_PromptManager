"""REST API module for ComfyUI PromptManager.

This module provides a comprehensive REST API for managing prompts, images, and
gallery functionality within ComfyUI. The API handles CRUD operations for prompts,
image gallery management, system administration, logging, and real-time image
monitoring with metadata extraction.

Key Features:
- Prompt management (create, read, update, delete, search)
- Image gallery with automatic ComfyUI output monitoring
- Bulk operations for efficiency
- Database maintenance and optimization
- System diagnostics and logging
- Thumbnail generation and management
- Metadata extraction from PNG files
- Real-time progress tracking

The API integrates with ComfyUI's aiohttp server and provides endpoints for
both programmatic access and web UI functionality.

Classes:
    PromptManagerAPI: Main API class handling all REST endpoints

Example:
    api = PromptManagerAPI()
    api.add_routes(server.routes)
"""

# PromptManager/py/api.py

import datetime
import json
import os
import traceback
from typing import Any, Dict, List, Optional

import server
from aiohttp import web
from PIL import Image

# Import database operations
try:
    from ..database.operations import PromptDatabase
    from ..utils.logging_config import get_logger
except ImportError:
    # Fallback for when module isn't imported as package
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from database.operations import PromptDatabase
    from utils.logging_config import get_logger


class PromptManagerAPI:
    """REST API handler for PromptManager operations and web interface.

    This class provides comprehensive REST API endpoints for managing prompts,
    images, and system operations. It handles database interactions, file
    operations, image processing, and web UI serving.

    The API is designed to integrate seamlessly with ComfyUI's aiohttp server
    and provides both JSON API endpoints and static file serving for the
    web interface.

    Attributes:
        logger: Configured logger instance for API operations
        db (PromptDatabase): Database connection and operations handler

    Example:
        api = PromptManagerAPI()
        api.add_routes(server_routes)
        # API endpoints are now available at /prompt_manager/*
    """

    def __init__(self):
        """Initialize the PromptManager API with database connection and cleanup.
        
        Sets up logging, initializes the database connection, and performs
        startup cleanup to remove any duplicate prompts that may exist.
        
        Raises:
            Exception: If database initialization fails, logs error but continues.
                      If cleanup fails, logs error but continues operation.
        """
        self.logger = get_logger('prompt_manager.api')
        self.logger.info("Initializing PromptManager API")
        
        self.db = PromptDatabase()

        # Run cleanup on initialization to remove any existing duplicates
        try:
            removed = self.db.cleanup_duplicates()
            if removed > 0:
                self.logger.info(f"Startup cleanup: removed {removed} duplicate prompts")
        except Exception as e:
            self.logger.error(f"Startup cleanup failed: {e}")
        
        self.logger.info("PromptManager API initialization completed")

    def add_routes(self, routes):
        """Add API routes to ComfyUI server using decorator pattern.
        
        Registers all API endpoints with the provided aiohttp routes object.
        Uses decorator pattern to define routes inline with their handlers.
        
        Categories of routes registered:
        - Core prompt operations (search, save, delete, categories, tags)
        - Database maintenance (cleanup, duplicates, maintenance)
        - Web UI serving (admin, gallery, metadata viewer)
        - Image operations (gallery, thumbnails, metadata extraction)
        - System operations (diagnostics, logging, statistics)
        - Bulk operations (delete, tag management, export)
        
        Args:
            routes: aiohttp RouteTableDef object from ComfyUI server instance.
                   Routes will be registered with this object for URL handling.
        
        Example:
            from server import PromptServer
            api = PromptManagerAPI()
            api.add_routes(PromptServer.instance.routes)
        """

        # Test route to verify registration works
        @routes.get("/prompt_manager/test")
        async def test_route(request):
            return web.json_response(
                {
                    "success": True,
                    "message": "PromptManager API is working!",
                    "timestamp": str(datetime.datetime.now()),
                }
            )

        @routes.get("/prompt_manager/search")
        async def search_prompts_route(request):
            return await self.search_prompts(request)

        @routes.get("/prompt_manager/recent")
        async def get_recent_prompts_route(request):
            return await self.get_recent_prompts(request)

        @routes.get("/prompt_manager/categories")
        async def get_categories_route(request):
            return await self.get_categories(request)

        @routes.get("/prompt_manager/tags")
        async def get_tags_route(request):
            return await self.get_tags(request)

        @routes.get("/prompt_manager/scan_duplicates")
        async def scan_duplicates_route(request):
            return await self.scan_duplicates_endpoint(request)

        @routes.post("/prompt_manager/delete_duplicate_images")
        async def delete_duplicate_images_route(request):
            return await self.delete_duplicate_images_endpoint(request)

        @routes.post("/prompt_manager/cleanup")
        async def cleanup_duplicates_route(request):
            return await self.cleanup_duplicates_endpoint(request)

        @routes.post("/prompt_manager/maintenance")
        async def maintenance_route(request):
            return await self.run_maintenance(request)

        @routes.post("/prompt_manager/save")
        async def save_prompt_route(request):
            return await self.save_prompt(request)

        @routes.delete("/prompt_manager/delete/{prompt_id}")
        async def delete_prompt_route(request):
            return await self.delete_prompt(request)

        # Serve the web UI HTML file
        @routes.get("/prompt_manager/web")
        async def serve_web_ui(request):
            try:
                import os

                # Get the path to the web directory
                current_dir = os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__))
                )
                html_path = os.path.join(current_dir, "web", "index.html")

                if os.path.exists(html_path):
                    with open(html_path, "r", encoding="utf-8") as f:
                        html_content = f.read()

                    return web.Response(
                        text=html_content, content_type="text/html", charset="utf-8"
                    )
                else:
                    return web.Response(
                        text="<h1>Web UI not found</h1><p>HTML file not located at expected path.</p>",
                        content_type="text/html",
                        status=404,
                    )

            except Exception as e:
                return web.Response(
                    text=f"<h1>Error</h1><p>Failed to load web UI: {str(e)}</p>",
                    content_type="text/html",
                    status=500,
                )

        # Serve the gallery interface
        @routes.get("/prompt_manager/gallery.html")
        async def serve_gallery_ui(request):
            try:
                import os

                current_dir = os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__))
                )
                html_path = os.path.join(current_dir, "web", "metadata.html")

                if os.path.exists(html_path):
                    with open(html_path, "r", encoding="utf-8") as f:
                        html_content = f.read()

                    return web.Response(
                        text=html_content, content_type="text/html", charset="utf-8"
                    )
                else:
                    return web.Response(
                        text="<h1>Gallery not found</h1><p>gallery.html file not located at expected path.</p>",
                        content_type="text/html",
                        status=404,
                    )

            except Exception as e:
                return web.Response(
                    text=f"<h1>Error</h1><p>Failed to load gallery: {str(e)}</p>",
                    content_type="text/html",
                    status=500,
                )

        # Serve the admin interface
        @routes.get("/prompt_manager/admin")
        async def serve_admin_ui(request):
            try:
                import os

                current_dir = os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__))
                )
                html_path = os.path.join(current_dir, "web", "admin.html")

                if os.path.exists(html_path):
                    with open(html_path, "r", encoding="utf-8") as f:
                        html_content = f.read()

                    return web.Response(
                        text=html_content, content_type="text/html", charset="utf-8"
                    )
                else:
                    return web.Response(
                        text="<h1>Admin UI not found</h1>",
                        content_type="text/html",
                        status=404,
                    )

            except Exception as e:
                return web.Response(
                    text=f"<h1>Error</h1><p>Failed to load admin UI: {str(e)}</p>",
                    content_type="text/html",
                    status=500,
                )

        # Serve the gallery interface (new admin gallery)
        @routes.get("/prompt_manager/gallery")
        async def serve_gallery_admin_ui(request):
            try:
                import os

                current_dir = os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__))
                )
                html_path = os.path.join(current_dir, "web", "gallery.html")

                if os.path.exists(html_path):
                    with open(html_path, "r", encoding="utf-8") as f:
                        html_content = f.read()

                    return web.Response(
                        text=html_content, content_type="text/html", charset="utf-8"
                    )
                else:
                    return web.Response(
                        text="<h1>Gallery not found</h1><p>gallery.html file not located at expected path.</p>",
                        content_type="text/html",
                        status=404,
                    )

            except Exception as e:
                return web.Response(
                    text=f"<h1>Error</h1><p>Failed to load gallery: {str(e)}</p>",
                    content_type="text/html",
                    status=500,
                )

        # Serve static files from web/lib directory
        @routes.get("/prompt_manager/lib/{filepath:.*}")
        async def serve_lib_static(request):
            """Serve static library files (JS, CSS) from web/lib directory."""
            import os

            # Explicit MIME type mapping
            MIME_TYPES = {
                ".js": "application/javascript",
                ".css": "text/css",
                ".json": "application/json",
                ".map": "application/json",
            }

            filepath = request.match_info.get("filepath", "")

            # Security: prevent directory traversal
            if ".." in filepath or filepath.startswith("/"):
                return web.Response(text="Forbidden", status=403)

            current_dir = os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )
            file_path = os.path.join(current_dir, "web", "lib", filepath)

            if not os.path.exists(file_path) or not os.path.isfile(file_path):
                return web.Response(text=f"Not Found: {filepath}", status=404)

            # Get extension and content type
            ext = os.path.splitext(file_path)[1].lower()
            content_type = MIME_TYPES.get(ext, "application/octet-stream")

            with open(file_path, "rb") as f:
                content = f.read()

            return web.Response(
                body=content,
                content_type=content_type,
            )

        # Statistics endpoint
        @routes.get("/prompt_manager/stats")
        async def get_stats_route(request):
            return await self.get_statistics(request)

        # Settings endpoints
        @routes.get("/prompt_manager/settings")
        async def get_settings_route(request):
            return await self.get_settings(request)

        @routes.post("/prompt_manager/settings")
        async def save_settings_route(request):
            return await self.save_settings(request)


        # Individual prompt management
        @routes.put("/prompt_manager/prompts/{prompt_id}")
        async def update_prompt_route(request):
            return await self.update_prompt(request)

        @routes.put("/prompt_manager/prompts/{prompt_id}/rating")
        async def update_rating_route(request):
            return await self.update_prompt_rating(request)

        @routes.post("/prompt_manager/prompts/{prompt_id}/tags")
        async def add_tag_route(request):
            return await self.add_prompt_tag(request)

        @routes.delete("/prompt_manager/prompts/{prompt_id}/tags")
        async def remove_tag_route(request):
            return await self.remove_prompt_tag(request)

        @routes.post("/prompt_manager/prompts/tags")
        async def add_tags_to_prompt_route(request):
            return await self.add_tags_to_prompt(request)

        # Bulk operations
        @routes.post("/prompt_manager/bulk/delete")
        async def bulk_delete_route(request):
            return await self.bulk_delete_prompts(request)

        @routes.post("/prompt_manager/bulk/tags")
        async def bulk_add_tags_route(request):
            return await self.bulk_add_tags(request)

        @routes.post("/prompt_manager/bulk/category")
        async def bulk_set_category_route(request):
            return await self.bulk_set_category(request)

        # Export functionality
        @routes.get("/prompt_manager/export")
        async def export_prompts_route(request):
            return await self.export_prompts(request)

        # Database backup and restore
        @routes.get("/prompt_manager/backup")
        async def backup_database_route(request):
            return await self.backup_database(request)

        @routes.post("/prompt_manager/restore")
        async def restore_database_route(request):
            return await self.restore_database(request)

        # Gallery endpoints
        @routes.get("/prompt_manager/prompts/{prompt_id}/images")
        async def get_prompt_images_route(request):
            return await self.get_prompt_images(request)

        @routes.get("/prompt_manager/images/recent")
        async def get_recent_images_route(request):
            return await self.get_recent_images(request)

        @routes.get("/prompt_manager/images/all")
        async def get_all_images_route(request):
            return await self.get_all_images(request)

        @routes.get("/prompt_manager/images/search")
        async def search_images_route(request):
            return await self.search_images(request)

        @routes.get("/prompt_manager/images/output")
        async def get_output_images_route(request):
            return await self.get_output_images(request)

        @routes.get("/prompt_manager/images/{image_id}/file")
        async def serve_image_route(request):
            return await self.serve_image(request)

        @routes.get("/prompt_manager/images/serve/{filepath:.*}")
        async def serve_output_image_route(request):
            return await self.serve_output_image(request)

        @routes.post("/prompt_manager/images/link")
        async def link_image_route(request):
            return await self.link_image_to_prompt(request)
            
        @routes.get("/prompt_manager/images/prompt/{image_path:.*}")
        async def get_image_prompt_route(request):
            return await self.get_image_prompt(request)

        @routes.delete("/prompt_manager/images/{image_id}")
        async def delete_image_route(request):
            return await self.delete_image(request)

        @routes.post("/prompt_manager/images/generate-thumbnails")
        async def generate_thumbnails_route(request):
            return await self.generate_thumbnails(request)

        @routes.get("/prompt_manager/images/generate-thumbnails/progress")
        async def generate_thumbnails_progress_route(request):
            return await self.generate_thumbnails_with_progress(request)

        @routes.post("/prompt_manager/images/clear-thumbnails")
        async def clear_thumbnails_route(request):
            return await self.clear_thumbnails(request)

        # Diagnostic endpoints
        @routes.get("/prompt_manager/diagnostics")
        async def run_diagnostics_route(request):
            return await self.run_diagnostics(request)

        @routes.post("/prompt_manager/diagnostics/test-link")
        async def test_image_link_route(request):
            return await self.test_image_link(request)

        # Scan endpoint
        @routes.post("/prompt_manager/scan")
        async def scan_images_route(request):
            return await self.scan_images(request)

        # Logging endpoints
        @routes.get("/prompt_manager/logs")
        async def get_logs_route(request):
            return await self.get_logs(request)

        @routes.get("/prompt_manager/logs/files")
        async def get_log_files_route(request):
            return await self.get_log_files(request)

        @routes.get("/prompt_manager/logs/download/{filename}")
        async def download_log_route(request):
            return await self.download_log_file(request)

        @routes.post("/prompt_manager/logs/truncate")
        async def truncate_logs_route(request):
            return await self.truncate_logs(request)

        @routes.get("/prompt_manager/logs/config")
        async def get_log_config_route(request):
            return await self.get_log_config(request)

        @routes.post("/prompt_manager/logs/config")
        async def update_log_config_route(request):
            return await self.update_log_config(request)

        @routes.get("/prompt_manager/logs/stats")
        async def get_log_stats_route(request):
            return await self.get_log_stats(request)

        # AutoTag endpoints
        @routes.get("/prompt_manager/autotag/models")
        async def get_autotag_models_route(request):
            return await self.get_autotag_models(request)

        @routes.get("/prompt_manager/autotag/download/{model_type}")
        async def download_autotag_model_route(request):
            return await self.download_autotag_model(request)

        @routes.get("/prompt_manager/autotag/start")
        async def start_autotag_route(request):
            return await self.start_autotag(request)

        @routes.post("/prompt_manager/autotag/single")
        async def autotag_single_route(request):
            return await self.autotag_single(request)

        @routes.post("/prompt_manager/autotag/apply")
        async def apply_autotag_route(request):
            return await self.apply_autotag(request)

        @routes.post("/prompt_manager/autotag/unload")
        async def unload_autotag_model_route(request):
            return await self.unload_autotag_model(request)

        @routes.get("/prompt_manager/scan_output_dir")
        async def scan_output_dir_route(request):
            return await self.scan_output_dir(request)

        self.logger.info("All routes registered with decorator pattern")

    async def search_prompts(self, request):
        """Search for prompts using multiple filter criteria.
        
        Provides comprehensive search functionality across prompt text, categories,
        tags, and ratings with configurable result limits.
        
        Query Parameters:
            text (str, optional): Search text to match against prompt content
            category (str, optional): Filter by specific category
            tags (str, optional): Comma-separated list of tags to filter by
            min_rating (int, optional): Minimum rating (1-5) to include
            limit (int, optional): Maximum results to return (default: 50, max: 1000)
        
        Args:
            request (aiohttp.web.Request): HTTP request object containing query parameters
        
        Returns:
            aiohttp.web.Response: JSON response with structure:
                {
                    "success": bool,
                    "results": List[Dict],  # List of matching prompt objects
                    "count": int            # Number of results returned
                }
        
        Raises:
            Returns 500 status with error details if search fails
        
        Example:
            GET /prompt_manager/search?text=portrait&category=photography&min_rating=3
        """
        try:
            # Get query parameters
            text = request.query.get("text", "").strip()
            category = request.query.get("category", "").strip()
            tags_str = request.query.get("tags", "").strip()
            min_rating = request.query.get("min_rating", 0)
            limit = int(request.query.get("limit", 50))

            # Parse tags
            tags = None
            if tags_str:
                tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()]

            # Parse min_rating
            try:
                min_rating = int(min_rating) if min_rating else None
            except ValueError:
                min_rating = None

            # Perform search
            results = self.db.search_prompts(
                text=text if text else None,
                category=category if category else None,
                tags=tags,
                rating_min=min_rating,
                limit=limit,
            )

            return web.json_response(
                {"success": True, "results": results, "count": len(results)}
            )

        except Exception as e:
            self.logger.error(f"Search error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": f"Search failed: {str(e)}", "results": []},
                status=500,
            )

    async def get_recent_prompts(self, request):
        """Retrieve recently created prompts with pagination support.
        
        Returns prompts sorted by creation date (newest first) with configurable
        pagination using either page-based or offset-based navigation.
        
        Query Parameters:
            limit (int, optional): Number of prompts per page (default: 50, max: 1000)
            page (int, optional): Page number for pagination (1-based, default: 1)
            offset (int, optional): Offset for results (takes precedence over page)
        
        Args:
            request (aiohttp.web.Request): HTTP request object containing query parameters
        
        Returns:
            aiohttp.web.Response: JSON response with structure:
                {
                    "success": bool,
                    "results": List[Dict],  # List of prompt objects
                    "pagination": {
                        "total": int,        # Total number of prompts
                        "limit": int,        # Items per page
                        "offset": int,       # Current offset
                        "page": int,         # Current page number
                        "total_pages": int,  # Total number of pages
                        "has_more": bool,    # Whether more pages exist
                        "count": int         # Number of items in current page
                    }
                }
        
        Raises:
            Returns 500 status with error details if retrieval fails
        
        Example:
            GET /prompt_manager/recent?limit=20&page=2
        """
        try:
            limit = int(request.query.get("limit", 50))
            page = int(request.query.get("page", 1))
            offset = int(request.query.get("offset", 0))
            
            # If page is provided, calculate offset from page
            if page > 1 and offset == 0:
                offset = (page - 1) * limit
            
            # Ensure reasonable limits
            if limit > 1000:
                limit = 1000
            elif limit < 1:
                limit = 1

            results = self.db.get_recent_prompts(limit=limit, offset=offset)

            return web.json_response({
                "success": True, 
                "results": results['prompts'],
                "pagination": {
                    "total": results['total'],
                    "limit": results['limit'],
                    "offset": results['offset'],
                    "page": results['page'],
                    "total_pages": results['total_pages'],
                    "has_more": results['has_more'],
                    "count": len(results['prompts'])
                }
            })

        except Exception as e:
            self.logger.error(f"Recent prompts error: {e}", exc_info=True)
            return web.json_response(
                {
                    "success": False,
                    "error": f"Failed to get recent prompts: {str(e)}",
                    "results": [],
                    "pagination": {"total": 0, "page": 1, "total_pages": 0}
                },
                status=500,
            )

    async def get_categories(self, request):
        """Retrieve all available prompt categories.
        
        Returns a list of all unique categories found in the database.
        
        Args:
            request (aiohttp.web.Request): HTTP request object
        
        Returns:
            aiohttp.web.Response: JSON response with structure:
                {
                    "success": bool,
                    "categories": List[str]  # List of category names
                }
        
        Raises:
            Returns 500 status with error details if retrieval fails
        
        Example:
            GET /prompt_manager/categories
        """
        try:
            categories = self.db.get_all_categories()

            return web.json_response({"success": True, "categories": categories})

        except Exception as e:
            self.logger.error(f"Categories error: {e}")
            return web.json_response(
                {
                    "success": False,
                    "error": f"Failed to get categories: {str(e)}",
                    "categories": [],
                },
                status=500,
            )

    async def get_tags(self, request):
        """Retrieve all available prompt tags.
        
        Returns a list of all unique tags found across all prompts in the database.
        
        Args:
            request (aiohttp.web.Request): HTTP request object
        
        Returns:
            aiohttp.web.Response: JSON response with structure:
                {
                    "success": bool,
                    "tags": List[str]  # List of unique tag names
                }
        
        Raises:
            Returns 500 status with error details if retrieval fails
        
        Example:
            GET /prompt_manager/tags
        """
        try:
            tags = self.db.get_all_tags()

            return web.json_response({"success": True, "tags": tags})

        except Exception as e:
            self.logger.error(f"Tags error: {e}")
            return web.json_response(
                {
                    "success": False,
                    "error": f"Failed to get tags: {str(e)}",
                    "tags": [],
                },
                status=500,
            )

    async def save_prompt(self, request):
        """Save a new prompt with metadata and duplicate detection.
        
        Creates a new prompt record with automatic duplicate detection based on
        SHA256 hash of the prompt text. If a duplicate is found, updates the
        existing record's metadata instead of creating a new one.
        
        Request Body (JSON):
            text (str, required): The prompt text content
            category (str, optional): Category for organization
            tags (List[str], optional): List of tags for classification
            rating (int, optional): Rating from 1-5
            notes (str, optional): Additional notes or description
        
        Args:
            request (aiohttp.web.Request): HTTP request with JSON body
        
        Returns:
            aiohttp.web.Response: JSON response with structure:
                {
                    "success": bool,
                    "prompt_id": int,          # ID of created/updated prompt
                    "message": str,            # Success/status message
                    "is_duplicate": bool       # True if prompt already existed
                }
        
        Raises:
            Returns 400 status if required fields are missing
            Returns 500 status with error details if save fails
        
        Example:
            POST /prompt_manager/save
            {
                "text": "A beautiful sunset over mountains",
                "category": "landscape",
                "tags": ["nature", "scenic"],
                "rating": 4,
                "notes": "Great for wallpapers"
            }
        """
        try:
            data = await request.json()

            text = data.get("text", "").strip()
            if not text:
                return web.json_response(
                    {"success": False, "error": "Text is required"}, status=400
                )

            category = data.get("category", "").strip() or None
            tags = data.get("tags", [])
            rating = data.get("rating") or None
            notes = data.get("notes", "").strip() or None

            # Generate hash for duplicate detection
            import hashlib

            prompt_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            
            # Check if prompt already exists
            existing = self.db.get_prompt_by_hash(prompt_hash)
            if existing:
                # Update metadata if this is a duplicate with new info
                if any([category, tags, rating, notes]):
                    self.db.update_prompt_metadata(
                        prompt_id=existing['id'],
                        category=category,
                        tags=tags,
                        rating=rating,
                        notes=notes
                    )
                return web.json_response(
                    {
                        "success": True,
                        "prompt_id": existing['id'],
                        "message": "Prompt already exists, metadata updated",
                        "is_duplicate": True
                    }
                )

            # Save new prompt
            prompt_id = self.db.save_prompt(
                text=text,
                category=category,
                tags=tags if tags else None,
                rating=rating,
                notes=notes,
                prompt_hash=prompt_hash,
            )

            return web.json_response(
                {
                    "success": True,
                    "prompt_id": prompt_id,
                    "message": "Prompt saved successfully",
                }
            )

        except Exception as e:
            self.logger.error(f"Save error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": f"Failed to save prompt: {str(e)}"},
                status=500,
            )

    async def delete_prompt(self, request):
        """Delete a specific prompt by ID.
        
        Permanently removes a prompt and all associated metadata from the database.
        Associated image links may also be removed depending on database configuration.
        
        URL Parameters:
            prompt_id (int): The unique identifier of the prompt to delete
        
        Args:
            request (aiohttp.web.Request): HTTP request with prompt_id in URL path
        
        Returns:
            aiohttp.web.Response: JSON response with structure:
                {
                    "success": bool,
                    "message": str  # Success or error message
                }
        
        Raises:
            Returns 404 status if prompt not found
            Returns 500 status with error details if deletion fails
        
        Example:
            DELETE /prompt_manager/delete/123
        """
        try:
            prompt_id = int(request.match_info["prompt_id"])

            success = self.db.delete_prompt(prompt_id)

            if success:
                return web.json_response(
                    {"success": True, "message": "Prompt deleted successfully"}
                )
            else:
                return web.json_response(
                    {
                        "success": False,
                        "error": "Prompt not found or could not be deleted",
                    },
                    status=404,
                )

        except ValueError:
            return web.json_response(
                {"success": False, "error": "Invalid prompt ID"}, status=400
            )
        except Exception as e:
            self.logger.error(f"Delete error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to delete prompt: {str(e)}"},
                status=500,
            )

    async def scan_duplicates_endpoint(self, request):
        """
        Scan for duplicate images without removing them.
        GET /prompt_manager/scan_duplicates
        """
        try:
            duplicates = await self.find_duplicate_images()

            return web.json_response(
                {
                    "success": True,
                    "duplicates": duplicates,
                    "message": f"Found {len(duplicates)} groups of duplicate images",
                }
            )

        except Exception as e:
            self.logger.error(f"Scan duplicates error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to scan duplicate images: {str(e)}"},
                status=500,
            )

    async def cleanup_duplicates_endpoint(self, request):
        """
        Cleanup duplicate prompts endpoint.
        POST /prompt_manager/cleanup
        """
        try:
            removed_count = self.db.cleanup_duplicates()

            return web.json_response(
                {
                    "success": True,
                    "message": f"Cleanup completed",
                    "duplicates_removed": removed_count,
                }
            )

        except Exception as e:
            self.logger.error(f"Cleanup error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to cleanup duplicates: {str(e)}"},
                status=500,
            )

    async def find_duplicate_images(self):
        """Find duplicate images in ComfyUI output directory using content hashing.
        
        Scans the ComfyUI output directory for image and video files, calculates
        SHA256 hashes of file contents, and identifies groups of files with
        identical content. Supports both images and videos with thumbnail detection.
        
        The method processes files efficiently, logging progress every 100 files,
        and handles various media formats including PNG, JPG, JPEG, WebP, GIF,
        and common video formats.
        
        Returns:
            List[Dict[str, Any]]: List of duplicate groups, where each group contains:
                - hash (str): SHA256 hash of the file content
                - images (List[Dict]): List of file info dictionaries with:
                    - id (str): Unique identifier based on file path hash
                    - filename (str): Original filename
                    - path (str): Absolute file path
                    - relative_path (str): Path relative to output directory
                    - url (str): URL for serving the file
                    - thumbnail_url (str, optional): URL for thumbnail if available
                    - size (int): File size in bytes
                    - modified_time (float): Last modification timestamp
                    - extension (str): File extension
                    - media_type (str): 'image' or 'video'
                    - is_video (bool): True if file is a video
                    - hash (str): SHA256 content hash
                - count (int): Number of duplicate files in this group
        
        Note:
            Files within each duplicate group are sorted by modification time
            (oldest first) to help users decide which files to keep.
        
        Example:
            duplicates = await api.find_duplicate_images()
            for group in duplicates:
                print(f"Found {group['count']} duplicates with hash {group['hash']}")
                for img in group['images']:
                    print(f"  - {img['filename']} ({img['size']} bytes)")
        """
        import hashlib
        from pathlib import Path
        
        self.logger.info("Scanning for duplicate images")
        
        try:
            # Find ComfyUI output directory
            output_dir = self._find_comfyui_output_dir()
            if not output_dir:
                self.logger.warning("ComfyUI output directory not found")
                return []
            
            output_path = Path(output_dir)

            # Extensions to check
            image_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff']
            video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.webm', '.gif']
            all_extensions = image_extensions + video_extensions

            # Find all media files, excluding thumbnails directory
            # Use a set to avoid duplicates on case-insensitive filesystems (Windows)
            media_files = []
            seen_paths = set()
            for ext in all_extensions:
                # Search for both lowercase and uppercase extensions
                for pattern in [f"*{ext.lower()}", f"*{ext.upper()}"]:
                    for media_path in output_path.rglob(pattern):
                        # Skip files in thumbnails directory
                        if 'thumbnails' not in media_path.parts:
                            # Normalize path for deduplication (case-insensitive on Windows)
                            normalized_path = str(media_path).lower()
                            if normalized_path not in seen_paths:
                                seen_paths.add(normalized_path)
                                media_files.append(media_path)

            self.logger.info(f"Found {len(media_files)} media files to analyze")

            # Calculate hash for each file
            file_hashes = {}
            processed = 0
            
            for media_path in media_files:
                try:
                    # Calculate file hash
                    file_hash = self._calculate_file_hash(media_path)
                    
                    if file_hash not in file_hashes:
                        file_hashes[file_hash] = []
                    
                    # Create image info object similar to get_output_images
                    stat = media_path.stat()
                    rel_path = media_path.relative_to(output_path)
                    extension = media_path.suffix.lower()
                    is_video = extension in [ext.lower() for ext in video_extensions]
                    media_type = 'video' if is_video else 'image'
                    
                    # Check if thumbnail exists
                    thumbnail_url = None
                    thumbnails_dir = output_path / "thumbnails"
                    if thumbnails_dir.exists():
                        thumbnail_ext = '.jpg' if is_video else extension
                        # Preserve subdirectory structure in thumbnail path
                        rel_path_no_ext = rel_path.with_suffix('')
                        thumbnail_rel_path = f"thumbnails/{rel_path_no_ext.as_posix()}_thumb{thumbnail_ext}"
                        thumbnail_abs_path = output_path / thumbnail_rel_path

                        if thumbnail_abs_path.exists():
                            thumbnail_url = f'/prompt_manager/images/serve/{Path(thumbnail_rel_path).as_posix()}'
                    
                    image_info = {
                        'id': str(hash(str(media_path))),
                        'filename': media_path.name,
                        'path': str(media_path),
                        'relative_path': str(rel_path),
                        'url': f'/prompt_manager/images/serve/{rel_path.as_posix()}',
                        'thumbnail_url': thumbnail_url,
                        'size': stat.st_size,
                        'modified_time': stat.st_mtime,
                        'extension': extension,
                        'media_type': media_type,
                        'is_video': is_video,
                        'hash': file_hash
                    }
                    
                    file_hashes[file_hash].append(image_info)
                    processed += 1
                    
                    # Log progress every 100 files
                    if processed % 100 == 0:
                        self.logger.info(f"Processed {processed}/{len(media_files)} files for duplicate detection")
                        
                except Exception as e:
                    self.logger.error(f"Error processing file {media_path}: {e}")
                    continue

            # Find duplicates (groups with more than one file)
            duplicates = []
            for file_hash, images in file_hashes.items():
                if len(images) > 1:
                    # Sort by modification time (oldest first) to help users decide which to keep
                    images.sort(key=lambda x: x['modified_time'])
                    duplicates.append({
                        'hash': file_hash,
                        'images': images,
                        'count': len(images)
                    })

            self.logger.info(f"Found {len(duplicates)} groups of duplicate images")
            return duplicates

        except Exception as e:
            self.logger.error(f"Error finding duplicate images: {e}")
            return []

    def _calculate_file_hash(self, file_path):
        """Calculate SHA-256 hash of a file's content.
        
        Reads the file in 4KB chunks to efficiently handle large files
        without loading the entire content into memory.
        
        Args:
            file_path (str): Path to the file to hash
        
        Returns:
            str: Hexadecimal SHA-256 hash of the file content
        
        Raises:
            IOError: If the file cannot be read
            OSError: If the file path is invalid or inaccessible
        """
        import hashlib
        
        hash_sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()

    async def delete_duplicate_images_endpoint(self, request):
        """
        Delete duplicate image files from disk.
        POST /prompt_manager/delete_duplicate_images
        """
        try:
            data = await request.json()
            image_paths = data.get('image_paths', [])
            
            if not image_paths:
                return web.json_response(
                    {"success": False, "error": "No image paths provided"},
                    status=400,
                )

            deleted_count = 0
            failed_count = 0
            failed_files = []

            for image_path in image_paths:
                try:
                    from pathlib import Path
                    import os
                    
                    # Ensure the path is within the output directory for security
                    output_dir = self._find_comfyui_output_dir()
                    if not output_dir:
                        failed_files.append(f"{image_path} (output directory not found)")
                        failed_count += 1
                        continue
                    
                    output_path = Path(output_dir)
                    file_path = Path(image_path)
                    
                    # Security check - ensure file is within output directory
                    try:
                        file_path.resolve().relative_to(output_path.resolve())
                    except ValueError:
                        self.logger.warning(f"Attempted to delete file outside output directory: {image_path}")
                        failed_files.append(f"{image_path} (outside output directory)")
                        failed_count += 1
                        continue
                    
                    if file_path.exists() and file_path.is_file():
                        os.remove(file_path)
                        deleted_count += 1
                        self.logger.info(f"Deleted duplicate image: {image_path}")
                        
                        # Also try to delete associated thumbnail if it exists
                        try:
                            rel_path = file_path.relative_to(output_path)
                            rel_path_no_ext = rel_path.with_suffix('')
                            thumbnail_path = output_path / "thumbnails" / f"{rel_path_no_ext.as_posix()}_thumb{file_path.suffix}"
                            if thumbnail_path.exists():
                                os.remove(thumbnail_path)
                                self.logger.debug(f"Deleted associated thumbnail: {thumbnail_path}")
                        except Exception as e:
                            self.logger.warning(f"Could not delete thumbnail for {image_path}: {e}")
                    else:
                        failed_files.append(f"{image_path} (file not found)")
                        failed_count += 1
                        
                except Exception as e:
                    self.logger.error(f"Error deleting file {image_path}: {e}")
                    failed_files.append(f"{image_path} ({str(e)})")
                    failed_count += 1

            response_data = {
                "success": True,
                "deleted_count": deleted_count,
                "failed_count": failed_count,
                "message": f"Deleted {deleted_count} files successfully"
            }
            
            if failed_count > 0:
                response_data["failed_files"] = failed_files
                response_data["message"] += f", {failed_count} failed"

            return web.json_response(response_data)

        except Exception as e:
            self.logger.error(f"Delete duplicate images error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to delete duplicate images: {str(e)}"},
                status=500,
            )

    async def get_statistics(self, request):
        """Get database statistics."""
        try:
            # Get basic stats from database
            with self.db.model.get_connection() as conn:
                cursor = conn.execute("SELECT COUNT(*) as total FROM prompts")
                total_prompts = cursor.fetchone()["total"]

                cursor = conn.execute(
                    "SELECT COUNT(DISTINCT TRIM(category)) as total FROM prompts WHERE category IS NOT NULL AND TRIM(category) != ''"
                )
                total_categories = cursor.fetchone()["total"]

                cursor = conn.execute(
                    "SELECT AVG(rating) as avg FROM prompts WHERE rating IS NOT NULL"
                )
                avg_rating = cursor.fetchone()["avg"]

                # Count unique tags
                cursor = conn.execute("SELECT tags FROM prompts WHERE tags IS NOT NULL")
                all_tags = set()
                for row in cursor.fetchall():
                    try:
                        tags = json.loads(row["tags"])
                        if isinstance(tags, list):
                            all_tags.update(tags)
                    except:
                        continue

                # Get all categories for debugging (including raw data)
                cursor = conn.execute(
                    "SELECT DISTINCT category FROM prompts WHERE category IS NOT NULL ORDER BY category"
                )
                raw_categories = [row['category'] for row in cursor.fetchall()]
                
                cursor = conn.execute(
                    "SELECT DISTINCT TRIM(category) as category FROM prompts WHERE category IS NOT NULL AND TRIM(category) != '' ORDER BY category"
                )
                filtered_categories = [row['category'] for row in cursor.fetchall()]
                
                # Debug logging with detailed category info
                self.logger.debug(f"Statistics calculated - Prompts: {total_prompts}, Categories: {total_categories}, Tags: {len(all_tags)}, Avg Rating: {avg_rating}")
                self.logger.debug(f"Raw categories from DB: {[(cat, len(cat), repr(cat)) for cat in raw_categories]}")
                self.logger.debug(f"Filtered categories: {[(cat, len(cat), repr(cat)) for cat in filtered_categories]}")

                return web.json_response(
                    {
                        "success": True,
                        "stats": {
                            "total_prompts": total_prompts,
                            "total_categories": total_categories,
                            "unique_categories": total_categories,  # Keep both for compatibility
                            "total_tags": len(all_tags),
                            "average_rating": (
                                round(avg_rating, 2) if avg_rating else None
                            ),
                            "avg_rating": (
                                round(avg_rating, 2) if avg_rating else None
                            ),  # Keep both for compatibility
                            "recent_prompts": total_prompts,  # For now, use total as recent count
                        },
                    }
                )

        except Exception as e:
            self.logger.error(f"Stats error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to get statistics: {str(e)}"},
                status=500,
            )

    async def get_settings(self, request):
        """Get current settings."""
        try:
            from .config import PromptManagerConfig
            
            # Return actual configuration settings
            return web.json_response({
                "success": True, 
                "settings": {
                    "result_timeout": PromptManagerConfig.RESULT_TIMEOUT,
                    "webui_display_mode": PromptManagerConfig.WEBUI_DISPLAY_MODE
                }
            })
        except Exception as e:
            return web.json_response(
                {"success": False, "error": f"Failed to get settings: {str(e)}"},
                status=500,
            )

    async def save_settings(self, request):
        """Save settings."""
        try:
            data = await request.json()
            # For now, just acknowledge the save
            # In the future, we could store this in database or config file
            return web.json_response(
                {"success": True, "message": "Settings saved successfully"}
            )
        except Exception as e:
            return web.json_response(
                {"success": False, "error": f"Failed to save settings: {str(e)}"},
                status=500,
            )

    async def update_prompt(self, request):
        """Update prompt text."""
        try:
            prompt_id = int(request.match_info["prompt_id"])
            data = await request.json()
            new_text = data.get("text", "").strip()

            if not new_text:
                return web.json_response(
                    {"success": False, "error": "Text cannot be empty"}, status=400
                )

            # Update the prompt in database
            with self.db.model.get_connection() as conn:
                cursor = conn.execute(
                    "UPDATE prompts SET text = ?, updated_at = ? WHERE id = ?",
                    (
                        new_text,
                        datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        prompt_id,
                    ),
                )
                conn.commit()

                if cursor.rowcount > 0:
                    return web.json_response(
                        {"success": True, "message": "Prompt updated successfully"}
                    )
                else:
                    return web.json_response(
                        {"success": False, "error": "Prompt not found"}, status=404
                    )

        except ValueError:
            return web.json_response(
                {"success": False, "error": "Invalid prompt ID"}, status=400
            )
        except Exception as e:
            self.logger.error(f"Update prompt error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to update prompt: {str(e)}"},
                status=500,
            )

    async def update_prompt_rating(self, request):
        """Update prompt rating."""
        try:
            prompt_id = int(request.match_info["prompt_id"])
            data = await request.json()
            rating = data.get("rating")

            if rating is not None and (rating < 1 or rating > 5):
                return web.json_response(
                    {"success": False, "error": "Rating must be between 1 and 5"},
                    status=400,
                )

            with self.db.model.get_connection() as conn:
                cursor = conn.execute(
                    "UPDATE prompts SET rating = ?, updated_at = ? WHERE id = ?",
                    (
                        rating,
                        datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        prompt_id,
                    ),
                )
                conn.commit()

                if cursor.rowcount > 0:
                    return web.json_response(
                        {"success": True, "message": "Rating updated successfully"}
                    )
                else:
                    return web.json_response(
                        {"success": False, "error": "Prompt not found"}, status=404
                    )

        except ValueError:
            return web.json_response(
                {"success": False, "error": "Invalid prompt ID"}, status=400
            )
        except Exception as e:
            self.logger.error(f"Update rating error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to update rating: {str(e)}"},
                status=500,
            )

    async def add_prompt_tag(self, request):
        """Add tag to prompt."""
        try:
            prompt_id = int(request.match_info["prompt_id"])
            data = await request.json()
            new_tag = data.get("tag", "").strip()

            if not new_tag:
                return web.json_response(
                    {"success": False, "error": "Tag cannot be empty"}, status=400
                )

            # Get current prompt
            prompt = self.db.get_prompt_by_id(prompt_id)
            if not prompt:
                return web.json_response(
                    {"success": False, "error": "Prompt not found"}, status=404
                )

            # Get current tags
            current_tags = prompt.get("tags", [])
            if not isinstance(current_tags, list):
                current_tags = []

            # Add new tag if not already present
            if new_tag not in current_tags:
                current_tags.append(new_tag)

                # Update database
                with self.db.model.get_connection() as conn:
                    cursor = conn.execute(
                        "UPDATE prompts SET tags = ?, updated_at = ? WHERE id = ?",
                        (
                            json.dumps(current_tags),
                            datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            prompt_id,
                        ),
                    )
                    conn.commit()

            return web.json_response(
                {"success": True, "message": "Tag added successfully"}
            )

        except ValueError:
            return web.json_response(
                {"success": False, "error": "Invalid prompt ID"}, status=400
            )
        except Exception as e:
            self.logger.error(f"Add tag error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to add tag: {str(e)}"}, status=500
            )

    async def add_tags_to_prompt(self, request):
        """Add multiple tags to a single prompt."""
        try:
            data = await request.json()
            prompt_id = data.get("prompt_id")
            new_tags = data.get("tags", [])

            if not prompt_id:
                return web.json_response(
                    {"success": False, "error": "Prompt ID is required"}, status=400
                )

            if not new_tags or not isinstance(new_tags, list):
                return web.json_response(
                    {"success": False, "error": "Tags must be a non-empty list"}, status=400
                )

            # Get current prompt
            prompt = self.db.get_prompt_by_id(prompt_id)
            if not prompt:
                return web.json_response(
                    {"success": False, "error": "Prompt not found"}, status=404
                )

            # Get current tags
            current_tags = prompt.get("tags", [])
            if not isinstance(current_tags, list):
                current_tags = []

            # Add new tags if not already present
            tags_added = 0
            for new_tag in new_tags:
                new_tag = new_tag.strip()
                if new_tag and new_tag not in current_tags:
                    current_tags.append(new_tag)
                    tags_added += 1

            # Update database if any tags were added
            if tags_added > 0:
                with self.db.model.get_connection() as conn:
                    cursor = conn.execute(
                        "UPDATE prompts SET tags = ?, updated_at = ? WHERE id = ?",
                        (
                            json.dumps(current_tags),
                            datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            prompt_id,
                        ),
                    )
                    conn.commit()

            message = f"{tags_added} tag(s) added successfully"
            if tags_added == 0:
                message = "No new tags to add (all tags already exist)"

            return web.json_response(
                {"success": True, "message": message, "tags_added": tags_added}
            )

        except ValueError:
            return web.json_response(
                {"success": False, "error": "Invalid prompt ID"}, status=400
            )
        except Exception as e:
            self.logger.error(f"Add tags error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to add tags: {str(e)}"}, status=500
            )

    async def remove_prompt_tag(self, request):
        """Remove tag from prompt."""
        try:
            prompt_id = int(request.match_info["prompt_id"])
            data = await request.json()
            tag_to_remove = data.get("tag", "").strip()

            # Get current prompt
            prompt = self.db.get_prompt_by_id(prompt_id)
            if not prompt:
                return web.json_response(
                    {"success": False, "error": "Prompt not found"}, status=404
                )

            # Get current tags
            current_tags = prompt.get("tags", [])
            if not isinstance(current_tags, list):
                current_tags = []

            # Remove tag if present
            if tag_to_remove in current_tags:
                current_tags.remove(tag_to_remove)

                # Update database
                with self.db.model.get_connection() as conn:
                    cursor = conn.execute(
                        "UPDATE prompts SET tags = ?, updated_at = ? WHERE id = ?",
                        (
                            json.dumps(current_tags),
                            datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            prompt_id,
                        ),
                    )
                    conn.commit()

            return web.json_response(
                {"success": True, "message": "Tag removed successfully"}
            )

        except ValueError:
            return web.json_response(
                {"success": False, "error": "Invalid prompt ID"}, status=400
            )
        except Exception as e:
            self.logger.error(f"Remove tag error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to remove tag: {str(e)}"},
                status=500,
            )

    async def bulk_delete_prompts(self, request):
        """Bulk delete prompts."""
        try:
            data = await request.json()
            prompt_ids = data.get("prompt_ids", [])

            if not prompt_ids:
                return web.json_response(
                    {"success": False, "error": "No prompt IDs provided"}, status=400
                )

            deleted_count = 0
            with self.db.model.get_connection() as conn:
                for prompt_id in prompt_ids:
                    # First delete related images to avoid foreign key constraint
                    conn.execute("DELETE FROM generated_images WHERE prompt_id = ?", (prompt_id,))
                    # Then delete the prompt
                    cursor = conn.execute(
                        "DELETE FROM prompts WHERE id = ?", (prompt_id,)
                    )
                    if cursor.rowcount > 0:
                        deleted_count += 1
                conn.commit()

            return web.json_response(
                {
                    "success": True,
                    "message": f"Deleted {deleted_count} prompts",
                    "deleted_count": deleted_count,
                }
            )

        except Exception as e:
            self.logger.error(f"Bulk delete error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to delete prompts: {str(e)}"},
                status=500,
            )

    async def bulk_add_tags(self, request):
        """Bulk add tags to prompts."""
        try:
            data = await request.json()
            prompt_ids = data.get("prompt_ids", [])
            new_tags = data.get("tags", [])

            if not prompt_ids or not new_tags:
                return web.json_response(
                    {"success": False, "error": "No prompt IDs or tags provided"},
                    status=400,
                )

            updated_count = 0
            with self.db.model.get_connection() as conn:
                for prompt_id in prompt_ids:
                    # Get current tags
                    cursor = conn.execute(
                        "SELECT tags FROM prompts WHERE id = ?", (prompt_id,)
                    )
                    row = cursor.fetchone()
                    if row:
                        current_tags = []
                        if row["tags"]:
                            try:
                                current_tags = json.loads(row["tags"])
                                if not isinstance(current_tags, list):
                                    current_tags = []
                            except:
                                current_tags = []

                        # Add new tags
                        for tag in new_tags:
                            if tag not in current_tags:
                                current_tags.append(tag)

                        # Update database
                        cursor = conn.execute(
                            "UPDATE prompts SET tags = ?, updated_at = ? WHERE id = ?",
                            (
                                json.dumps(current_tags),
                                datetime.datetime.now(
                                    datetime.timezone.utc
                                ).isoformat(),
                                prompt_id,
                            ),
                        )
                        if cursor.rowcount > 0:
                            updated_count += 1

                conn.commit()

            return web.json_response(
                {
                    "success": True,
                    "message": f"Added tags to {updated_count} prompts",
                    "updated_count": updated_count,
                }
            )

        except Exception as e:
            self.logger.error(f"Bulk add tags error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to add tags: {str(e)}"}, status=500
            )

    async def bulk_set_category(self, request):
        """Bulk set category for prompts."""
        try:
            data = await request.json()
            prompt_ids = data.get("prompt_ids", [])
            category = data.get("category", "").strip()

            if not prompt_ids:
                return web.json_response(
                    {"success": False, "error": "No prompt IDs provided"}, status=400
                )

            updated_count = 0
            with self.db.model.get_connection() as conn:
                for prompt_id in prompt_ids:
                    cursor = conn.execute(
                        "UPDATE prompts SET category = ?, updated_at = ? WHERE id = ?",
                        (
                            category,
                            datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            prompt_id,
                        ),
                    )
                    if cursor.rowcount > 0:
                        updated_count += 1
                conn.commit()

            return web.json_response(
                {
                    "success": True,
                    "message": f"Set category for {updated_count} prompts",
                    "updated_count": updated_count,
                }
            )

        except Exception as e:
            self.logger.error(f"Bulk set category error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to set category: {str(e)}"},
                status=500,
            )

    async def export_prompts(self, request):
        """Export all prompts to JSON."""
        try:
            # Get all prompts
            prompts = self.db.search_prompts(limit=10000)

            # Create export data
            export_data = {
                "export_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "total_prompts": len(prompts),
                "prompts": prompts,
            }

            # Return as JSON download
            json_data = json.dumps(export_data, indent=2, ensure_ascii=False)

            return web.Response(
                text=json_data,
                content_type="application/json",
                headers={
                    "Content-Disposition": f'attachment; filename="prompt_manager_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.json"'
                },
            )

        except Exception as e:
            self.logger.error(f"Export error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to export prompts: {str(e)}"},
                status=500,
            )

    # Gallery-related endpoints
    def _clean_nan_recursive(self, obj):
        """Recursively clean NaN values from nested data structures."""
        if isinstance(obj, dict):
            return {key: self._clean_nan_recursive(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._clean_nan_recursive(item) for item in obj]
        elif isinstance(obj, float) and str(obj) == 'nan':
            return None
        else:
            return obj

    async def get_prompt_images(self, request):
        """Get all images for a specific prompt."""
        try:
            prompt_id = request.match_info["prompt_id"]
            images = self.db.get_prompt_images(prompt_id)
            
            # Clean up any NaN values that cause JSON parsing errors (recursive)
            cleaned_images = [self._clean_nan_recursive(image) for image in images]
            
            # Additional fallback: convert to JSON string and clean NaN values manually
            import json
            import re
            try:
                response_data = {
                    'success': True,
                    'images': cleaned_images
                }
                # Convert to JSON string
                json_str = json.dumps(response_data, default=str)
                
                # Clean any remaining NaN values with regex
                json_str = re.sub(r':\s*NaN', ': null', json_str)
                json_str = re.sub(r'\[\s*NaN\s*\]', '[null]', json_str)
                json_str = re.sub(r',\s*NaN\s*,', ', null,', json_str)
                json_str = re.sub(r',\s*NaN\s*\]', ', null]', json_str)
                json_str = re.sub(r'\[\s*NaN\s*,', '[null,', json_str)
                
                # Parse back to verify it's valid JSON
                cleaned_data = json.loads(json_str)
                
                return web.json_response(cleaned_data)
            except Exception as json_error:
                self.logger.error(f"JSON cleaning error: {json_error}")
                # Fallback to original response
                return web.json_response({
                    'success': True,
                    'images': cleaned_images
                })
            
        except Exception as e:
            self.logger.error(f"Get prompt images error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_recent_images(self, request):
        """Get recently generated images."""
        try:
            limit = int(request.query.get('limit', 50))
            images = self.db.get_recent_images(limit)

            return web.json_response({
                'success': True,
                'images': images
            })
        except Exception as e:
            self.logger.error(f"Get recent images error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_all_images(self, request):
        """Get all generated images with linked prompts."""
        try:
            images = self.db.get_all_images()

            return web.json_response({
                'success': True,
                'images': images,
                'count': len(images)
            })
        except Exception as e:
            self.logger.error(f"Get all images error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def search_images(self, request):
        """Search images by prompt text."""
        try:
            query = request.query.get('q', '')
            if not query:
                return web.json_response({
                    'success': False,
                    'error': 'Search query required'
                }, status=400)
            
            images = self.db.search_images_by_prompt(query)
            
            return web.json_response({
                'success': True,
                'images': images,
                'query': query
            })
        except Exception as e:
            self.logger.error(f"Search images error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_output_images(self, request):
        """Get all images from ComfyUI output folder."""
        try:
            import os
            from pathlib import Path
            
            # Find ComfyUI output directory
            output_dir = self._find_comfyui_output_dir()
            if not output_dir:
                return web.json_response({
                    'success': False,
                    'error': 'ComfyUI output directory not found',
                    'images': []
                })
            
            # Get pagination parameters
            limit = int(request.query.get('limit', 100))
            offset = int(request.query.get('offset', 0))
            
            # Find all media files (images and videos)
            image_extensions = ['.png', '.jpg', '.jpeg', '.webp', '.gif']
            video_extensions = ['.mp4', '.webm', '.avi', '.mov', '.mkv', '.m4v', '.wmv']
            media_extensions = image_extensions + video_extensions
            all_images = []
            
            output_path = Path(output_dir)
            thumbnails_dir = output_path / 'thumbnails'
            
            # Find all media files, excluding thumbnails directory
            # Use a set to avoid duplicates on case-insensitive filesystems (Windows)
            seen_paths = set()
            for ext in media_extensions:
                # Search for both lowercase and uppercase extensions
                for pattern in [f"*{ext}", f"*{ext.upper()}"]:
                    for media_path in output_path.rglob(pattern):
                        # Skip files in thumbnails directory
                        if 'thumbnails' not in media_path.parts:
                            # Normalize path for deduplication (case-insensitive on Windows)
                            normalized_path = str(media_path).lower()
                            if normalized_path not in seen_paths:
                                seen_paths.add(normalized_path)
                                all_images.append(media_path)
            
            # Sort by modification time (newest first)
            all_images.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            
            # Apply pagination
            paginated_images = all_images[offset:offset + limit]
            
            # Format media data (images and videos)
            images = []
            for media_path in paginated_images:
                try:
                    stat = media_path.stat()
                    # Create a relative path from output directory for the URL
                    rel_path = media_path.relative_to(output_path)
                    
                    # Determine media type
                    extension = media_path.suffix.lower()
                    is_video = extension in video_extensions
                    media_type = 'video' if is_video else 'image'
                    
                    # Check if thumbnail exists for this media
                    thumbnail_url = None
                    if thumbnails_dir.exists():
                        # For videos, look for thumbnail with .jpg extension
                        thumbnail_ext = '.jpg' if is_video else extension
                        # Preserve subdirectory structure in thumbnail path
                        rel_path_no_ext = rel_path.with_suffix('')
                        thumbnail_rel_path = f"thumbnails/{rel_path_no_ext.as_posix()}_thumb{thumbnail_ext}"
                        thumbnail_abs_path = output_path / thumbnail_rel_path

                        if thumbnail_abs_path.exists():
                            thumbnail_url = f'/prompt_manager/images/serve/{Path(thumbnail_rel_path).as_posix()}'
                    
                    images.append({
                        'id': str(hash(str(media_path))),  # Simple hash for ID
                        'filename': media_path.name,
                        'path': str(media_path),
                        'relative_path': str(rel_path),
                        'url': f'/prompt_manager/images/serve/{rel_path.as_posix()}',  # Use forward slashes for URLs
                        'thumbnail_url': thumbnail_url,  # Thumbnail URL if exists
                        'size': stat.st_size,
                        'modified_time': stat.st_mtime,
                        'extension': extension,
                        'media_type': media_type,  # 'image' or 'video'
                        'is_video': is_video
                    })
                except Exception as e:
                    self.logger.error(f"Error processing media {media_path}: {e}")
                    continue
            
            return web.json_response({
                'success': True,
                'images': images,
                'total': len(all_images),
                'offset': offset,
                'limit': limit,
                'has_more': offset + limit < len(all_images)
            })
            
        except Exception as e:
            self.logger.error(f"Get output images error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e),
                'images': []
            }, status=500)

    async def serve_image(self, request):
        """Serve the actual image file."""
        try:
            image_id = int(request.match_info["image_id"])
            image = self.db.get_image_by_id(image_id)
            
            if not image:
                return web.json_response({'error': 'Image not found'}, status=404)
            
            import os
            from pathlib import Path
            
            image_path = Path(image['image_path'])
            if not image_path.exists():
                return web.json_response({'error': 'Image file not found'}, status=404)
            
            # Determine content type based on file extension
            content_type = 'image/jpeg'
            if image_path.suffix.lower() in ['.png']:
                content_type = 'image/png'
            elif image_path.suffix.lower() in ['.webp']:
                content_type = 'image/webp'
            elif image_path.suffix.lower() in ['.gif']:
                content_type = 'image/gif'
            
            # Read and serve the file
            with open(image_path, 'rb') as f:
                file_data = f.read()
            
            return web.Response(
                body=file_data,
                content_type=content_type,
                headers={
                    'Cache-Control': 'public, max-age=3600',
                    'Content-Length': str(len(file_data))
                }
            )
            
        except ValueError:
            return web.json_response({'error': 'Invalid image ID'}, status=400)
        except Exception as e:
            self.logger.error(f"Serve image error: {e}")
            return web.json_response({'error': str(e)}, status=500)

    async def serve_output_image(self, request):
        """Serve image file directly from ComfyUI output folder."""
        try:
            import os
            from pathlib import Path
            
            filepath = request.match_info["filepath"]
            
            # Find ComfyUI output directory
            output_dir = self._find_comfyui_output_dir()
            if not output_dir:
                return web.json_response({'error': 'ComfyUI output directory not found'}, status=404)
            
            # Construct full image path
            image_path = Path(output_dir) / filepath
            
            # Security check: make sure the path is within the output directory
            try:
                image_path = image_path.resolve()
                output_path = Path(output_dir).resolve()
                if not str(image_path).startswith(str(output_path)):
                    return web.json_response({'error': 'Access denied'}, status=403)
            except Exception:
                return web.json_response({'error': 'Invalid file path'}, status=400)
            
            if not image_path.exists():
                return web.json_response({'error': 'Image file not found'}, status=404)
            
            # Determine content type based on file extension
            content_type = 'image/jpeg'
            if image_path.suffix.lower() == '.png':
                content_type = 'image/png'
            elif image_path.suffix.lower() == '.webp':
                content_type = 'image/webp'
            elif image_path.suffix.lower() == '.gif':
                content_type = 'image/gif'
            
            # Read and serve the file
            with open(image_path, 'rb') as f:
                file_data = f.read()
            
            return web.Response(
                body=file_data,
                content_type=content_type,
                headers={
                    'Cache-Control': 'public, max-age=3600',
                    'Content-Length': str(len(file_data))
                }
            )
            
        except Exception as e:
            self.logger.error(f"Serve output image error: {e}")
            return web.json_response({'error': str(e)}, status=500)

    async def generate_thumbnails(self, request):
        """Generate thumbnails for all images and videos in the ComfyUI output directory.
        
        Creates optimized thumbnail versions of all media files in a separate
        'thumbnails' subdirectory. This process is safe and never modifies
        original files.
        
        Safety Features:
        - Read-only access to original files
        - Writes only to separate thumbnails directory  
        - Uses PIL context managers for safe file handling
        - All thumbnails clearly marked with '_thumb' suffix
        - Proper error handling prevents corruption
        
        Supported Formats:
        - Images: PNG, JPG, JPEG, WebP, GIF
        - Videos: MP4, AVI, MOV, WMV (generates frame thumbnails)
        
        Query Parameters:
            size (int, optional): Thumbnail size in pixels (default: 256)
            quality (int, optional): JPEG quality 1-100 (default: 85)
            overwrite (bool, optional): Regenerate existing thumbnails (default: false)
        
        Args:
            request (aiohttp.web.Request): HTTP request with optional query parameters
        
        Returns:
            aiohttp.web.Response: JSON response with structure:
                {
                    \"success\": bool,
                    \"message\": str,
                    \"processed\": int,     # Number of thumbnails created
                    \"skipped\": int,       # Number of files skipped
                    \"errors\": int,        # Number of processing errors
                    \"total_size\": str     # Total size of created thumbnails
                }
        
        Raises:
            Returns 500 status with error details if thumbnail generation fails
        
        Example:
            POST /prompt_manager/images/generate-thumbnails?size=512&quality=90
        """
        try:
            import os
            import time
            from pathlib import Path
            from PIL import Image
            
            # Get request parameters
            data = await request.json()
            quality = data.get('quality', 'medium')
            report_progress = data.get('report_progress', False)
            
            # Map quality to size
            size_map = {
                'low': (150, 150),
                'medium': (300, 300), 
                'high': (600, 600)
            }
            thumbnail_size = size_map.get(quality, (300, 300))
            
            # Find ComfyUI output directory
            output_dir = self._find_comfyui_output_dir()
            if not output_dir:
                return web.json_response({
                    'success': False,
                    'error': 'ComfyUI output directory not found'
                }, status=404)
            
            output_path = Path(output_dir)
            thumbnails_dir = output_path / 'thumbnails'
            thumbnails_dir.mkdir(exist_ok=True)
            
            # Find all media files (images and videos)
            self.logger.info("Scanning for media files to generate thumbnails...")
            image_extensions = ['.png', '.jpg', '.jpeg', '.webp', '.gif']
            video_extensions = ['.mp4', '.webm', '.avi', '.mov', '.mkv', '.m4v', '.wmv']
            media_extensions = image_extensions + video_extensions
            media_files = []
            for root, dirs, files in os.walk(output_path):
                # Skip thumbnails directory
                if 'thumbnails' in Path(root).parts:
                    continue
                for file in files:
                    if any(file.lower().endswith(ext) for ext in media_extensions):
                        media_files.append(Path(root) / file)
            
            total_media = len(media_files)
            self.logger.info(f"Found {total_media} media files to process for thumbnails")
            
            if total_media == 0:
                return web.json_response({
                    'success': True,
                    'count': 0,
                    'total_images': 0,
                    'message': 'No media files found to process',
                    'errors': []
                })
            
            generated_count = 0
            skipped_count = 0
            errors = []
            start_time = time.time()
            
            for i, media_file in enumerate(media_files):
                try:
                    # Create relative path for thumbnail
                    rel_path = media_file.relative_to(output_path)
                    
                    # Check if this is a video file
                    is_video = any(media_file.name.lower().endswith(ext) for ext in video_extensions)
                    
                    # For videos, always save thumbnail as .jpg
                    # Preserve subdirectory structure in thumbnail path
                    rel_path_no_ext = rel_path.with_suffix('')
                    if is_video:
                        thumbnail_path = thumbnails_dir / f"{rel_path_no_ext.as_posix()}_thumb.jpg"
                    else:
                        thumbnail_path = thumbnails_dir / f"{rel_path_no_ext.as_posix()}_thumb{rel_path.suffix}"
                    
                    # Skip if thumbnail already exists and is newer than original
                    if (thumbnail_path.exists() and 
                        thumbnail_path.stat().st_mtime > media_file.stat().st_mtime):
                        skipped_count += 1
                        continue
                    
                    # Create thumbnail directory structure if needed
                    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    # Generate thumbnail based on media type
                    if is_video:
                        # Generate video thumbnail
                        if self._generate_video_thumbnail(media_file, thumbnail_path, thumbnail_size):
                            generated_count += 1
                        else:
                            errors.append(f"Failed to generate video thumbnail for {media_file.name}")
                    else:
                        # Generate image thumbnail
                        with Image.open(media_file) as img:
                            # Convert to RGB if necessary (for PNG with transparency)
                            if img.mode in ('RGBA', 'LA', 'P'):
                                img = img.convert('RGB')
                            
                            # Create thumbnail maintaining aspect ratio
                            img.thumbnail(thumbnail_size, Image.Resampling.LANCZOS)
                            
                            # Save thumbnail
                            save_kwargs = {'quality': 85, 'optimize': True}
                            if thumbnail_path.suffix.lower() == '.png':
                                save_kwargs = {'optimize': True}
                            
                            img.save(thumbnail_path, **save_kwargs)
                            generated_count += 1
                    
                    # Log progress every 100 images or at 10% intervals
                    if generated_count % 100 == 0 or (i + 1) % max(1, total_images // 10) == 0:
                        progress = ((i + 1) / total_images) * 100
                        elapsed = time.time() - start_time
                        rate = (i + 1) / elapsed if elapsed > 0 else 0
                        eta = ((total_images - i - 1) / rate) if rate > 0 else 0
                        self.logger.info(f"Thumbnail progress: {i+1}/{total_images} ({progress:.1f}%) - "
                                       f"Generated: {generated_count}, Skipped: {skipped_count}, "
                                       f"Rate: {rate:.1f} img/s, ETA: {eta:.0f}s")
                        
                except Exception as e:
                    error_msg = f"Failed to generate thumbnail for {media_file.name}: {str(e)}"
                    errors.append(error_msg)
                    self.logger.warning(error_msg)
            
            elapsed_time = time.time() - start_time
            self.logger.info(f"Thumbnail generation completed: {generated_count} generated, "
                           f"{skipped_count} skipped, {len(errors)} errors in {elapsed_time:.1f}s")
            
            return web.json_response({
                'success': True,
                'count': generated_count,
                'skipped': skipped_count,
                'total_images': total_images,
                'errors': errors,
                'thumbnails_path': str(thumbnails_dir),
                'elapsed_time': round(elapsed_time, 2),
                'processing_rate': round((total_images / elapsed_time) if elapsed_time > 0 else 0, 2)
            })
            
        except ImportError:
            return web.json_response({
                'success': False,
                'error': 'PIL (Pillow) library not available. Install with: pip install Pillow'
            }, status=500)
        except Exception as e:
            self.logger.error(f"Generate thumbnails error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def generate_thumbnails_with_progress(self, request):
        """Generate thumbnails with Server-Sent Events progress updates."""
        try:
            import os
            import time
            import json
            import asyncio
            from pathlib import Path
            from PIL import Image
            
            # Parse query parameters
            quality = request.query.get('quality', 'medium')
            
            # Map quality to size
            size_map = {
                'low': (150, 150),
                'medium': (300, 300), 
                'high': (600, 600)
            }
            thumbnail_size = size_map.get(quality, (300, 300))
            
            # Set up SSE response
            response = web.StreamResponse(
                status=200,
                headers={
                    'Content-Type': 'text/event-stream',
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'Access-Control-Allow-Origin': '*',
                }
            )
            await response.prepare(request)
            
            async def send_progress(event_type, data):
                """Send SSE event to client."""
                try:
                    message = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                    await response.write(message.encode('utf-8'))
                    await asyncio.sleep(0.01)  # Small delay to ensure delivery
                except Exception as e:
                    self.logger.warning(f"Failed to send SSE message: {e}")
            
            try:
                # Find ComfyUI output directory
                output_dir = self._find_comfyui_output_dir()
                if not output_dir:
                    await send_progress('error', {
                        'error': 'ComfyUI output directory not found'
                    })
                    return response
                
                output_path = Path(output_dir)
                thumbnails_dir = output_path / 'thumbnails'
                thumbnails_dir.mkdir(exist_ok=True)
                
                # Send scanning event
                await send_progress('status', {
                    'phase': 'scanning',
                    'message': f'Scanning {output_path} for images and videos to process...'
                })
                
                self.logger.info(f"Starting thumbnail generation scan in: {output_path}")
                
                # Find all media files (images and videos)
                image_extensions = ['.png', '.jpg', '.jpeg', '.webp', '.gif']
                video_extensions = ['.mp4', '.webm', '.avi', '.mov', '.mkv', '.m4v', '.wmv']
                media_extensions = image_extensions + video_extensions
                media_files = []
                scanned_dirs = 0
                
                for root, dirs, files in os.walk(output_path):
                    # Skip thumbnails directory
                    if 'thumbnails' in Path(root).parts:
                        continue
                    
                    scanned_dirs += 1
                    # Send scanning progress for every directory
                    if scanned_dirs % 5 == 0:  # Update every 5 directories
                        await send_progress('status', {
                            'phase': 'scanning',
                            'message': f'Scanning directories... ({scanned_dirs} checked, {len(media_files)} files found)'
                        })
                    
                    for file in files:
                        if any(file.lower().endswith(ext) for ext in media_extensions):
                            media_files.append(Path(root) / file)
                
                self.logger.info(f"Scan complete: Found {len(media_files)} media files in {scanned_dirs} directories")
                
                total_images = len(media_files)
                
                # Count images vs videos for more detail
                image_count = sum(1 for f in media_files if any(f.name.lower().endswith(ext) for ext in image_extensions))
                video_count = total_images - image_count
                
                await send_progress('start', {
                    'total_images': total_images,
                    'phase': 'processing',
                    'message': f'Found {image_count} images and {video_count} videos to process',
                    'image_count': image_count,
                    'video_count': video_count
                })
                
                self.logger.info(f"Starting thumbnail generation for {image_count} images and {video_count} videos")
                
                if total_images == 0:
                    await send_progress('complete', {
                        'count': 0,
                        'skipped': 0,
                        'total_images': 0,
                        'elapsed_time': 0,
                        'message': 'No media files found to process'
                    })
                    return response
                
                generated_count = 0
                skipped_count = 0
                errors = []
                start_time = time.time()
                
                # Process images with progress updates
                # SAFETY: We only READ from original images, NEVER modify them
                for i, media_file in enumerate(media_files):
                    try:
                        # SAFETY: Verify we're only reading from original media files
                        if not media_file.exists() or not media_file.is_file():
                            continue
                            
                        # Check if this is a video file
                        is_video = any(media_file.name.lower().endswith(ext) for ext in video_extensions)
                        
                        # Create relative path for thumbnail (in separate thumbnails directory)
                        rel_path = media_file.relative_to(output_path)
                        
                        # For videos, always save thumbnail as .jpg
                        # Preserve subdirectory structure in thumbnail path
                        rel_path_no_ext = rel_path.with_suffix('')
                        if is_video:
                            thumbnail_path = thumbnails_dir / f"{rel_path_no_ext.as_posix()}_thumb.jpg"
                        else:
                            thumbnail_path = thumbnails_dir / f"{rel_path_no_ext.as_posix()}_thumb{rel_path.suffix}"
                        
                        # SAFETY: Ensure thumbnail path is within our thumbnails directory
                        try:
                            thumbnail_path = thumbnail_path.resolve()
                            thumbnails_dir_resolved = thumbnails_dir.resolve()
                            if not str(thumbnail_path).startswith(str(thumbnails_dir_resolved)):
                                self.logger.warning(f"Skipping thumbnail outside safe directory: {thumbnail_path}")
                                continue
                        except Exception as e:
                            self.logger.warning(f"Path validation failed for {rel_path}: {e}")
                            continue
                        
                        # Skip if thumbnail already exists and is newer than original
                        if (thumbnail_path.exists() and 
                            thumbnail_path.stat().st_mtime > media_file.stat().st_mtime):
                            skipped_count += 1
                        else:
                            # Create thumbnail directory structure if needed (only within thumbnails dir)
                            thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
                            
                            # Generate thumbnail based on media type
                            if is_video:
                                # Generate video thumbnail
                                if self._generate_video_thumbnail(media_file, thumbnail_path, thumbnail_size):
                                    generated_count += 1
                                else:
                                    errors.append(f"Failed to generate video thumbnail for {media_file.name}")
                            else:
                                # Generate image thumbnail (READ-ONLY operation on original)
                                # SAFETY: Image.open() with context manager ensures read-only access
                                with Image.open(media_file) as img:
                                    # SAFETY: Work with a copy of the image data, never modify original
                                    # Convert to RGB if necessary (for PNG with transparency)
                                    if img.mode in ('RGBA', 'LA', 'P'):
                                        img = img.convert('RGB')
                                    
                                    # Create thumbnail maintaining aspect ratio
                                    # SAFETY: thumbnail() modifies the in-memory copy, not the original file
                                    img.thumbnail(thumbnail_size, Image.Resampling.LANCZOS)
                                    
                                    # Save thumbnail to separate location
                                    # SAFETY: Only write to our thumbnails directory, never touch originals
                                    save_kwargs = {'quality': 85, 'optimize': True}
                                    if thumbnail_path.suffix.lower() == '.png':
                                        save_kwargs = {'optimize': True}
                                    
                                    img.save(thumbnail_path, **save_kwargs)
                                    generated_count += 1
                        
                        # Send progress update more frequently for better feedback
                        # Update every 5 images or every 1% for large sets
                        if (i + 1) % 5 == 0 or (i + 1) % max(1, total_images // 100) == 0 or i == total_images - 1:
                            elapsed = time.time() - start_time
                            progress_percent = ((i + 1) / total_images) * 100
                            rate = (i + 1) / elapsed if elapsed > 0 else 0
                            eta = ((total_images - i - 1) / rate) if rate > 0 else 0
                            
                            # Include more detailed file info
                            file_info = {
                                'name': media_file.name,
                                'dir': media_file.parent.name,
                                'type': 'video' if is_video else 'image',
                                'action': 'skipped' if thumbnail_path.exists() else 'generating'
                            }
                            
                            await send_progress('progress', {
                                'processed': i + 1,
                                'total_images': total_images,
                                'generated': generated_count,
                                'skipped': skipped_count,
                                'percentage': round(progress_percent, 1),
                                'rate': round(rate, 1),
                                'eta': round(eta, 0),
                                'elapsed': round(elapsed, 1),
                                'current_file': f"{file_info['dir']}/{file_info['name']}",
                                'file_type': file_info['type'],
                                'action': file_info['action']
                            })
                            
                            # Log progress
                            if (i + 1) % 50 == 0:  # Log every 50 files
                                self.logger.info(f"Thumbnail progress: {i+1}/{total_images} ({progress_percent:.1f}%) - Generated: {generated_count}, Skipped: {skipped_count}")
                        
                    except Exception as e:
                        error_msg = f"Failed to generate thumbnail for {media_file.name}: {str(e)}"
                        errors.append(error_msg)
                        self.logger.warning(error_msg)
                        
                        # Send error notification for immediate feedback
                        if len(errors) <= 5:  # Only send first 5 errors to avoid spam
                            await send_progress('status', {
                                'phase': 'processing',
                                'message': f'Error processing {media_file.name}: {str(e)}'
                            })
                
                elapsed_time = time.time() - start_time
                
                # Send detailed completion event
                completion_message = f'Successfully generated {generated_count} new thumbnails, skipped {skipped_count} existing'
                if errors:
                    completion_message += f' ({len(errors)} errors occurred)'
                
                await send_progress('complete', {
                    'count': generated_count,
                    'skipped': skipped_count,
                    'total_images': total_images,
                    'errors': errors[:10],  # Limit errors to first 10
                    'error_count': len(errors),
                    'elapsed_time': round(elapsed_time, 2),
                    'processing_rate': round((total_images / elapsed_time) if elapsed_time > 0 else 0, 2),
                    'message': completion_message
                })
                
                self.logger.info(f"Thumbnail generation completed: {generated_count} generated, {skipped_count} skipped, {len(errors)} errors in {elapsed_time:.2f}s")
                
            except Exception as e:
                await send_progress('error', {
                    'error': str(e),
                    'message': f'Thumbnail generation failed: {str(e)}'
                })
                
            return response
            
        except ImportError:
            return web.json_response({
                'success': False,
                'error': 'PIL (Pillow) library not available. Install with: pip install Pillow'
            }, status=500)
        except Exception as e:
            self.logger.error(f"Generate thumbnails with progress error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    def _generate_video_thumbnail(self, video_path, thumbnail_path, thumbnail_size):
        """
        Generate thumbnail from video file.
        Returns True if successful, False otherwise.
        """
        try:
            # Try using OpenCV first (most reliable)
            try:
                import cv2
                
                # Open video
                cap = cv2.VideoCapture(str(video_path))
                if not cap.isOpened():
                    return False
                
                # Get frame from 10% into the video (avoid black intro frames)
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                target_frame = max(1, int(frame_count * 0.1))
                cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                
                # Read frame
                ret, frame = cap.read()
                cap.release()
                
                if not ret or frame is None:
                    return False
                
                # Convert BGR to RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                
                # Convert to PIL Image and create thumbnail
                from PIL import Image
                img = Image.fromarray(frame_rgb)
                img.thumbnail(thumbnail_size, Image.Resampling.LANCZOS)
                
                # Save thumbnail
                img.save(thumbnail_path, 'JPEG', quality=85, optimize=True)
                self.logger.debug(f"Generated video thumbnail using OpenCV: {thumbnail_path}")
                return True
                
            except ImportError:
                # OpenCV not available, try ffmpeg
                pass
                
            # Fallback to ffmpeg
            try:
                import subprocess
                
                # Use ffmpeg to extract frame at 10% duration
                cmd = [
                    'ffmpeg', '-i', str(video_path),
                    '-ss', '00:00:01',  # Skip first second to avoid black frames
                    '-vframes', '1',
                    '-s', f"{thumbnail_size[0]}x{thumbnail_size[1]}",
                    '-y',  # Overwrite output
                    str(thumbnail_path)
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    self.logger.debug(f"Generated video thumbnail using ffmpeg: {thumbnail_path}")
                    return True
                else:
                    self.logger.warning(f"ffmpeg failed for {video_path}: {result.stderr}")
                    
            except (ImportError, subprocess.TimeoutExpired, FileNotFoundError):
                # ffmpeg not available
                pass
            
            # Last resort: create a placeholder thumbnail
            try:
                from PIL import Image, ImageDraw, ImageFont
                
                # Create a placeholder image
                img = Image.new('RGB', thumbnail_size, color=(50, 50, 50))
                draw = ImageDraw.Draw(img)
                
                # Add play button icon
                center_x, center_y = thumbnail_size[0] // 2, thumbnail_size[1] // 2
                triangle_size = min(thumbnail_size) // 4
                
                # Draw play triangle
                points = [
                    (center_x - triangle_size//2, center_y - triangle_size//2),
                    (center_x - triangle_size//2, center_y + triangle_size//2),
                    (center_x + triangle_size//2, center_y)
                ]
                draw.polygon(points, fill=(255, 255, 255))
                
                # Add text
                try:
                    # Try to get a font
                    font = ImageFont.load_default()
                    text = "VIDEO"
                    bbox = draw.textbbox((0, 0), text, font=font)
                    text_width = bbox[2] - bbox[0]
                    text_height = bbox[3] - bbox[1]
                    draw.text(
                        (center_x - text_width//2, center_y + triangle_size//2 + 10),
                        text, fill=(255, 255, 255), font=font
                    )
                except:
                    pass
                
                img.save(thumbnail_path, 'JPEG', quality=85)
                self.logger.debug(f"Generated placeholder video thumbnail: {thumbnail_path}")
                return True
                
            except Exception as e:
                self.logger.warning(f"Failed to create placeholder thumbnail for {video_path}: {e}")
                return False
                
        except Exception as e:
            self.logger.error(f"Video thumbnail generation failed for {video_path}: {e}")
            return False

    async def clear_thumbnails(self, request):
        """Safely clear only our generated thumbnails, never touch original images."""
        try:
            import os
            import shutil
            from pathlib import Path
            
            # Find ComfyUI output directory
            output_dir = self._find_comfyui_output_dir()
            if not output_dir:
                return web.json_response({
                    'success': False,
                    'error': 'ComfyUI output directory not found'
                }, status=404)
            
            output_path = Path(output_dir)
            thumbnails_dir = output_path / 'thumbnails'
            
            # SAFETY: Only operate within our thumbnails directory
            if not thumbnails_dir.exists():
                return web.json_response({
                    'success': True,
                    'message': 'No thumbnails directory found - nothing to clear',
                    'cleared_files': 0
                })
            
            # SAFETY: Verify this is actually our thumbnails directory
            try:
                thumbnails_dir_resolved = thumbnails_dir.resolve()
                output_path_resolved = output_path.resolve()
                
                # Ensure thumbnails dir is within output dir and named 'thumbnails'
                if (not str(thumbnails_dir_resolved).startswith(str(output_path_resolved)) or
                    thumbnails_dir.name != 'thumbnails'):
                    self.logger.error(f"Safety check failed: thumbnails directory path invalid: {thumbnails_dir}")
                    return web.json_response({
                        'success': False,
                        'error': 'Safety check failed: invalid thumbnails directory path'
                    }, status=400)
                    
            except Exception as e:
                self.logger.error(f"Path validation failed: {e}")
                return web.json_response({
                    'success': False,
                    'error': 'Path validation failed'
                }, status=500)
            
            # Count files before deletion
            cleared_count = 0
            cleared_size = 0
            
            # SAFETY: Only delete files within thumbnails directory that match our naming pattern
            for root, dirs, files in os.walk(thumbnails_dir):
                for file in files:
                    file_path = Path(root) / file
                    
                    # SAFETY: Additional check - only delete files with '_thumb' in name
                    if '_thumb' in file.lower() and any(file.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp', '.gif']):
                        try:
                            file_size = file_path.stat().st_size
                            file_path.unlink()  # Delete the file
                            cleared_count += 1
                            cleared_size += file_size
                            self.logger.debug(f"Cleared thumbnail: {file_path}")
                        except Exception as e:
                            self.logger.warning(f"Failed to delete thumbnail {file_path}: {e}")
            
            # SAFETY: Remove empty directories within thumbnails folder (but not the main thumbnails dir)
            try:
                for root, dirs, files in os.walk(thumbnails_dir, topdown=False):
                    if root != str(thumbnails_dir):  # Don't remove the main thumbnails directory
                        try:
                            Path(root).rmdir()  # Only removes if empty
                        except OSError:
                            pass  # Directory not empty, that's fine
            except Exception as e:
                self.logger.debug(f"Directory cleanup info: {e}")
            
            # Convert size to human readable format
            def format_size(bytes_size):
                for unit in ['B', 'KB', 'MB', 'GB']:
                    if bytes_size < 1024.0:
                        return f"{bytes_size:.1f} {unit}"
                    bytes_size /= 1024.0
                return f"{bytes_size:.1f} TB"
            
            self.logger.info(f"Thumbnail cleanup: cleared {cleared_count} files ({format_size(cleared_size)})")
            
            return web.json_response({
                'success': True,
                'cleared_files': cleared_count,
                'cleared_size': cleared_size,
                'cleared_size_formatted': format_size(cleared_size),
                'message': f'Cleared {cleared_count} thumbnail files ({format_size(cleared_size)})'
            })
            
        except Exception as e:
            self.logger.error(f"Clear thumbnails error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def link_image_to_prompt(self, request):
        """Link a generated image to a prompt."""
        try:
            data = await request.json()
            prompt_id = data.get('prompt_id')
            image_path = data.get('image_path')
            metadata = data.get('metadata', {})
            
            if not prompt_id or not image_path:
                return web.json_response({
                    'success': False,
                    'error': 'prompt_id and image_path are required'
                }, status=400)
            
            # Check if image file exists
            import os
            if not os.path.exists(image_path):
                return web.json_response({
                    'success': False,
                    'error': 'Image file not found'
                }, status=404)
            
            # Link image to prompt
            image_id = self.db.link_image_to_prompt(prompt_id, image_path, metadata)
            
            return web.json_response({
                'success': True,
                'image_id': image_id,
                'message': 'Image linked successfully'
            })
            
        except Exception as e:
            self.logger.error(f"Link image error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_image_prompt(self, request):
        """Get prompt information for a specific image path."""
        try:
            import urllib.parse
            import os
            import json
            from pathlib import Path
            
            # Get the image path from URL
            raw_image_path = request.match_info.get('image_path', '')
            image_path = urllib.parse.unquote(raw_image_path)
            
            if not image_path:
                return web.json_response({
                    'success': False,
                    'error': 'Image path is required'
                }, status=400)
            
            # Convert relative path to absolute if needed
            if not os.path.isabs(image_path):
                # If it's a relative path from ComfyUI output, make it absolute
                output_dir = self._find_comfyui_output_dir()
                if output_dir:
                    image_path = str(Path(output_dir) / image_path)
            
            # Look up the image in generated_images table
            try:
                with self.db.model.get_connection() as conn:
                    cursor = conn.execute(
                        """SELECT gi.prompt_id, p.text, p.category, p.tags, p.rating, p.notes,
                                  gi.workflow_data, gi.prompt_metadata, gi.generation_time
                           FROM generated_images gi 
                           JOIN prompts p ON gi.prompt_id = p.id 
                           WHERE gi.image_path = ? OR gi.image_path LIKE ?""",
                        (image_path, f'%{os.path.basename(image_path)}')
                    )
                    result = cursor.fetchone()
                    
                    if result:
                        # Convert to dict
                        prompt_data = {
                            'prompt_id': result[0],
                            'text': result[1],
                            'category': result[2],
                            'tags': json.loads(result[3]) if result[3] else [],
                            'rating': result[4],
                            'notes': result[5],
                            'workflow_data': json.loads(result[6]) if result[6] else None,
                            'prompt_metadata': json.loads(result[7]) if result[7] else None,
                            'generation_time': result[8],
                            'image_path': image_path
                        }
                        
                        return web.json_response({
                            'success': True,
                            'prompt': prompt_data
                        })
                    else:
                        # No linked prompt found - this is normal for many images
                        return web.json_response({
                            'success': False,
                            'error': 'No prompt found for this image',
                            'image_path': image_path
                        })
                        
            except Exception as db_error:
                self.logger.error(f"Database error in get_image_prompt: {db_error}")
                return web.json_response({
                    'success': False,
                    'error': 'Database error occurred'
                }, status=500)
            
        except Exception as e:
            self.logger.error(f"Get image prompt error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def delete_image(self, request):
        """Delete an image record."""
        try:
            image_id = int(request.match_info["image_id"])
            success = self.db.delete_image(image_id)
            
            if success:
                return web.json_response({
                    'success': True,
                    'message': 'Image deleted successfully'
                })
            else:
                return web.json_response({
                    'success': False,
                    'error': 'Image not found'
                }, status=404)
                
        except ValueError:
            return web.json_response({'error': 'Invalid image ID'}, status=400)
        except Exception as e:
            self.logger.error(f"Delete image error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    # Diagnostic endpoints
    async def run_diagnostics(self, request):
        """Run comprehensive system diagnostics and health checks.
        
        Performs various system health checks including database connectivity,
        file system access, ComfyUI integration status, and configuration
        validation. Useful for troubleshooting and system monitoring.
        
        Diagnostic Checks:
        - Database connection and integrity
        - ComfyUI output directory detection
        - File system permissions
        - Configuration validation
        - Memory and performance metrics
        - Extension loading status
        
        Args:
            request (aiohttp.web.Request): HTTP request object
        
        Returns:
            aiohttp.web.Response: JSON response with diagnostic results:
                {
                    "success": bool,
                    "diagnostics": {
                        "database": Dict,      # Database health info
                        "filesystem": Dict,   # File system status
                        "comfyui": Dict,      # ComfyUI integration status
                        "config": Dict,       # Configuration validation
                        "performance": Dict   # Performance metrics
                    },
                    "summary": str,           # Overall system status
                    "issues": List[str]      # List of identified issues
                }
        
        Example:
            POST /prompt_manager/diagnostics
        """
        try:
            # Simple diagnostics without importing complex modules
            import os
            import sqlite3
            
            results = {}
            
            # Check database
            try:
                db_path = "prompts.db"
                if os.path.exists(db_path):
                    with sqlite3.connect(db_path) as conn:
                        conn.row_factory = sqlite3.Row
                        cursor = conn.execute("SELECT COUNT(*) as count FROM prompts")
                        prompt_count = cursor.fetchone()['count']
                        
                        # Check if images table exists
                        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='generated_images'")
                        has_images_table = cursor.fetchone() is not None
                        
                        if has_images_table:
                            cursor = conn.execute("SELECT COUNT(*) as count FROM generated_images")
                            image_count = cursor.fetchone()['count']
                        else:
                            image_count = 0
                        
                        results['database'] = {
                            'status': 'ok',
                            'prompt_count': prompt_count,
                            'has_images_table': has_images_table,
                            'image_count': image_count
                        }
                else:
                    results['database'] = {
                        'status': 'error',
                        'message': f'Database file not found: {db_path}'
                    }
            except Exception as e:
                results['database'] = {
                    'status': 'error',
                    'message': f'Database error: {str(e)}'
                }
            
            # Check dependencies
            dependencies = {}
            try:
                import watchdog
                dependencies['watchdog'] = True
            except ImportError:
                dependencies['watchdog'] = False
            
            try:
                from PIL import Image
                dependencies['PIL'] = True
            except ImportError:
                dependencies['PIL'] = False
            
            try:
                import sqlite3
                dependencies['sqlite3'] = True
            except ImportError:
                dependencies['sqlite3'] = False
            
            results['dependencies'] = {
                'status': 'ok' if all(dependencies.values()) else 'error',
                'dependencies': dependencies
            }
            
            # Check output directories
            output_dirs = []
            potential_dirs = ["output", "../output", "../../output"]
            
            for dir_path in potential_dirs:
                abs_path = os.path.abspath(dir_path)
                if os.path.exists(abs_path):
                    output_dirs.append(abs_path)
            
            results['comfyui_output'] = {
                'status': 'ok' if output_dirs else 'warning',
                'output_dirs': output_dirs
            }

            # Check image monitor status
            try:
                from ..utils.image_monitor import _monitor_instance
                if _monitor_instance is not None:
                    monitor_status = _monitor_instance.get_status()
                    results['image_monitor'] = {
                        'status': 'ok' if monitor_status.get('observer_alive') else 'error',
                        **monitor_status
                    }
                else:
                    results['image_monitor'] = {
                        'status': 'error',
                        'message': 'Image monitor not initialized'
                    }
            except Exception as e:
                results['image_monitor'] = {
                    'status': 'error',
                    'message': f'Failed to get monitor status: {str(e)}'
                }

            return web.json_response({
                'success': True,
                'diagnostics': results
            })
            
        except Exception as e:
            self.logger.error(f"Diagnostics error: {e}", exc_info=True)
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def test_image_link(self, request):
        """Test creating an image link."""
        try:
            data = await request.json()
            prompt_id = data.get('prompt_id')
            test_image_path = data.get('image_path', '/test/fake/image.png')
            
            if not prompt_id:
                return web.json_response({
                    'success': False,
                    'error': 'prompt_id is required'
                }, status=400)
            
            # Test linking directly using the database manager
            test_metadata = {
                'file_info': {
                    'size': 1024000,
                    'dimensions': [512, 512],
                    'format': 'PNG'
                },
                'workflow': {'test': True},
                'prompt': {'test_prompt': 'This is a test image'}
            }
            
            try:
                image_id = self.db.link_image_to_prompt(
                    prompt_id=str(prompt_id),
                    image_path=test_image_path,
                    metadata=test_metadata
                )
                
                return web.json_response({
                    'success': True,
                    'result': {
                        'status': 'ok',
                        'image_id': image_id,
                        'message': f'Test image linked successfully with ID {image_id}'
                    }
                })
            except Exception as e:
                return web.json_response({
                    'success': False,
                    'result': {
                        'status': 'error',
                        'message': f'Failed to create test link: {str(e)}'
                    }
                })
                
        except Exception as e:
            self.logger.error(f"Test link error: {e}", exc_info=True)
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def run_maintenance(self, request):
        """Perform comprehensive database maintenance and optimization.
        
        Executes a series of database maintenance operations to optimize
        performance, clean up orphaned data, and ensure database integrity.
        This is a resource-intensive operation that should be run during
        low-traffic periods.
        
        Maintenance Operations:
        - Remove duplicate prompts based on content hash
        - Clean up orphaned image references
        - Remove missing file records from database
        - Optimize database with VACUUM operation
        - Update database statistics
        - Validate data integrity
        - Clean up temporary files
        
        Query Parameters:
            full (bool, optional): Perform full maintenance including VACUUM
            cleanup_images (bool, optional): Clean up missing image references
            optimize (bool, optional): Run database optimization
        
        Args:
            request (aiohttp.web.Request): HTTP request with optional parameters
        
        Returns:
            aiohttp.web.Response: JSON response with maintenance results:
                {
                    "success": bool,
                    "operations": {
                        "duplicates_removed": int,
                        "orphaned_images_cleaned": int,
                        "missing_files_removed": int,
                        "database_optimized": bool,
                        "integrity_check_passed": bool
                    },
                    "before_stats": Dict,    # Database stats before maintenance
                    "after_stats": Dict,     # Database stats after maintenance
                    "duration": float,       # Maintenance duration in seconds
                    "message": str
                }
        
        Raises:
            Returns 500 status with error details if maintenance fails
        
        Example:
            POST /prompt_manager/maintenance?full=true&cleanup_images=true
        """
        try:
            data = await request.json() if request.content_type == 'application/json' else {}
            operations = data.get('operations', ['cleanup_duplicates', 'vacuum', 'cleanup_orphaned_images'])
            
            results = {}
            
            # Clean up duplicate prompts
            if 'cleanup_duplicates' in operations:
                try:
                    duplicates_removed = self.db.cleanup_duplicates()
                    results['cleanup_duplicates'] = {
                        'success': True,
                        'removed_count': duplicates_removed,
                        'message': f'Removed {duplicates_removed} duplicate prompts'
                    }
                except Exception as e:
                    results['cleanup_duplicates'] = {
                        'success': False,
                        'error': str(e),
                        'message': 'Failed to cleanup duplicates'
                    }
            
            # Vacuum database
            if 'vacuum' in operations:
                try:
                    self.db.model.vacuum_database()
                    results['vacuum'] = {
                        'success': True,
                        'message': 'Database vacuum completed successfully'
                    }
                except Exception as e:
                    results['vacuum'] = {
                        'success': False,
                        'error': str(e),
                        'message': 'Failed to vacuum database'
                    }
            
            # Clean up orphaned image records
            if 'cleanup_orphaned_images' in operations:
                try:
                    orphaned_removed = self.db.cleanup_missing_images()
                    results['cleanup_orphaned_images'] = {
                        'success': True,
                        'removed_count': orphaned_removed,
                        'message': f'Removed {orphaned_removed} orphaned image records'
                    }
                except Exception as e:
                    results['cleanup_orphaned_images'] = {
                        'success': False,
                        'error': str(e),
                        'message': 'Failed to cleanup orphaned images'
                    }
            
            # Check for potential duplicate hashes
            if 'check_hash_duplicates' in operations:
                try:
                    with self.db.model.get_connection() as conn:
                        cursor = conn.execute("""
                            SELECT hash, COUNT(*) as count 
                            FROM prompts 
                            WHERE hash IS NOT NULL 
                            GROUP BY hash 
                            HAVING COUNT(*) > 1
                        """)
                        hash_duplicates = cursor.fetchall()
                        
                        results['check_hash_duplicates'] = {
                            'success': True,
                            'duplicate_hashes': len(hash_duplicates),
                            'message': f'Found {len(hash_duplicates)} duplicate hash groups'
                        }
                except Exception as e:
                    results['check_hash_duplicates'] = {
                        'success': False,
                        'error': str(e),
                        'message': 'Failed to check hash duplicates'
                    }
            
            # Get database statistics
            if 'statistics' in operations:
                try:
                    db_info = self.db.model.get_database_info()
                    results['statistics'] = {
                        'success': True,
                        'info': db_info,
                        'message': 'Database statistics retrieved'
                    }
                except Exception as e:
                    results['statistics'] = {
                        'success': False,
                        'error': str(e),
                        'message': 'Failed to get database statistics'
                    }
            
            # Prune orphaned prompts (prompts with no linked images)
            # Note: Prompts with the __protected__ tag are excluded from orphan cleanup
            # This allows users to add prompts manually without images that won't be deleted
            if 'prune_orphaned_prompts' in operations:
                try:
                    with self.db.model.get_connection() as conn:
                        # Find prompts that have no images linked to them
                        # Exclude prompts with the __protected__ tag
                        cursor = conn.execute("""
                            SELECT p.id
                            FROM prompts p
                            LEFT JOIN generated_images gi ON p.id = gi.prompt_id
                            WHERE gi.prompt_id IS NULL
                            AND (p.tags IS NULL OR p.tags NOT LIKE '%"__protected__"%')
                        """)
                        orphaned_prompts = cursor.fetchall()
                        orphaned_count = len(orphaned_prompts)
                        
                        if orphaned_count > 0:
                            # Delete the orphaned prompts
                            orphaned_ids = [row['id'] for row in orphaned_prompts]
                            placeholders = ','.join(['?'] * len(orphaned_ids))
                            cursor = conn.execute(f"DELETE FROM prompts WHERE id IN ({placeholders})", orphaned_ids)
                            conn.commit()
                            removed_count = cursor.rowcount
                        else:
                            removed_count = 0
                    
                    results['prune_orphaned_prompts'] = {
                        'success': True,
                        'removed_count': removed_count,
                        'message': f'Removed {removed_count} orphaned prompts (prompts with no linked images, excluding protected prompts)'
                    }
                except Exception as e:
                    results['prune_orphaned_prompts'] = {
                        'success': False,
                        'error': str(e),
                        'message': 'Failed to prune orphaned prompts'
                    }
            
            # Check for consistency issues
            if 'check_consistency' in operations:
                try:
                    consistency_issues = []
                    
                    with self.db.model.get_connection() as conn:
                        # Check for prompts with invalid JSON in tags
                        cursor = conn.execute("SELECT id, tags FROM prompts WHERE tags IS NOT NULL")
                        for row in cursor.fetchall():
                            try:
                                if row['tags']:
                                    json.loads(row['tags'])
                            except json.JSONDecodeError:
                                consistency_issues.append(f"Prompt {row['id']} has invalid JSON in tags")
                        
                        # Check for orphaned foreign key references
                        cursor = conn.execute("""
                            SELECT gi.id, gi.prompt_id 
                            FROM generated_images gi 
                            LEFT JOIN prompts p ON gi.prompt_id = p.id 
                            WHERE p.id IS NULL
                        """)
                        orphaned_refs = cursor.fetchall()
                        for ref in orphaned_refs:
                            consistency_issues.append(f"Image {ref['id']} references non-existent prompt {ref['prompt_id']}")
                    
                    results['check_consistency'] = {
                        'success': True,
                        'issues_found': len(consistency_issues),
                        'issues': consistency_issues[:10],  # Limit to first 10 issues
                        'message': f'Found {len(consistency_issues)} consistency issues'
                    }
                except Exception as e:
                    results['check_consistency'] = {
                        'success': False,
                        'error': str(e),
                        'message': 'Failed to check database consistency'
                    }
            
            # Overall success status
            all_successful = all(result.get('success', False) for result in results.values())
            
            return web.json_response({
                'success': True,
                'operations_completed': len(results),
                'all_successful': all_successful,
                'results': results,
                'message': f'Maintenance completed: {len(results)} operations processed'
            })
            
        except Exception as e:
            self.logger.error(f"Maintenance error: {e}", exc_info=True)
            return web.json_response({
                'success': False,
                'error': f'Maintenance failed: {str(e)}'
            }, status=500)

    async def backup_database(self, request):
        """
        Backup the entire prompts.db database file.
        GET /prompt_manager/backup
        """
        try:
            import os
            import shutil
            import tempfile
            from pathlib import Path

            # Get the database path
            db_path = "prompts.db"
            
            if not os.path.exists(db_path):
                return web.json_response({
                    'success': False,
                    'error': 'Database file not found'
                }, status=404)

            # Create a temporary copy of the database
            with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as temp_file:
                temp_path = temp_file.name
                
            # Copy the database file
            shutil.copy2(db_path, temp_path)
            
            # Read the file content
            with open(temp_path, 'rb') as f:
                file_data = f.read()
            
            # Clean up temporary file
            os.unlink(temp_path)
            
            # Generate filename with timestamp
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"prompts_backup_{timestamp}.db"
            
            return web.Response(
                body=file_data,
                content_type='application/octet-stream',
                headers={
                    'Content-Disposition': f'attachment; filename="{filename}"',
                    'Content-Length': str(len(file_data))
                }
            )

        except Exception as e:
            self.logger.error(f"Backup error: {e}", exc_info=True)
            return web.json_response({
                'success': False,
                'error': f'Failed to backup database: {str(e)}'
            }, status=500)

    async def restore_database(self, request):
        """
        Restore the prompts.db database from uploaded file.
        POST /prompt_manager/restore
        """
        try:
            import os
            import shutil
            import tempfile
            import sqlite3
            from pathlib import Path

            # Get the uploaded file
            reader = await request.multipart()
            field = await reader.next()
            
            if not field or field.name != 'database_file':
                return web.json_response({
                    'success': False,
                    'error': 'No database file uploaded. Expected field name: database_file'
                }, status=400)

            # Read the uploaded file content
            file_data = await field.read()
            
            if not file_data:
                return web.json_response({
                    'success': False,
                    'error': 'Uploaded file is empty'
                }, status=400)

            # Create a temporary file to validate the database
            with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as temp_file:
                temp_path = temp_file.name
                temp_file.write(file_data)

            try:
                # Validate that it's a valid SQLite database with expected structure
                with sqlite3.connect(temp_path) as conn:
                    conn.row_factory = sqlite3.Row
                    
                    # Check if it has the prompts table
                    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='prompts'")
                    if not cursor.fetchone():
                        raise ValueError("Database does not contain a 'prompts' table")
                    
                    # Check basic structure of prompts table
                    cursor = conn.execute("PRAGMA table_info(prompts)")
                    columns = [row['name'] for row in cursor.fetchall()]
                    required_columns = ['id', 'text', 'created_at']
                    
                    for col in required_columns:
                        if col not in columns:
                            raise ValueError(f"Database missing required column: {col}")
                    
                    # Get basic stats for validation
                    cursor = conn.execute("SELECT COUNT(*) as count FROM prompts")
                    prompt_count = cursor.fetchone()['count']

                # If validation passes, backup current database and restore
                db_path = "prompts.db"
                backup_path = f"{db_path}.backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
                
                # Create backup of current database if it exists
                if os.path.exists(db_path):
                    shutil.copy2(db_path, backup_path)
                    self.logger.info(f"Current database backed up to: {backup_path}")

                # Replace current database with uploaded one
                shutil.copy2(temp_path, db_path)
                
                # Reinitialize the database connection
                self.db = PromptDatabase()
                
                return web.json_response({
                    'success': True,
                    'message': f'Database restored successfully. Found {prompt_count} prompts.',
                    'prompt_count': prompt_count,
                    'backup_created': backup_path if os.path.exists(db_path) else None
                })

            except sqlite3.Error as e:
                return web.json_response({
                    'success': False,
                    'error': f'Invalid SQLite database: {str(e)}'
                }, status=400)
            except ValueError as e:
                return web.json_response({
                    'success': False,
                    'error': str(e)
                }, status=400)
            finally:
                # Clean up temporary file
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        except Exception as e:
            self.logger.error(f"Restore error: {e}", exc_info=True)
            return web.json_response({
                'success': False,
                'error': f'Failed to restore database: {str(e)}'
            }, status=500)

    async def scan_images(self, request):
        """
        Scan ComfyUI output images for prompt metadata and add them to the database.
        Streams progress updates to the client.
        """
        import json
        import asyncio
        from pathlib import Path
        from aiohttp import web
        
        async def stream_response():
            try:
                self.logger.info("Starting image scan operation")
                
                # Clear any active prompt tracking timers to avoid conflicts
                # Note: For now we'll skip this since we don't have easy access to the tracker instance
                # This is mainly important if scans are run during active generation, which is rare
                self.logger.info("Starting scan (timer clearing not implemented yet)")
                
                # Find ComfyUI output directory
                output_dir = self._find_comfyui_output_dir()
                if not output_dir:
                    self.logger.error("ComfyUI output directory not found")
                    yield f"data: {json.dumps({'type': 'error', 'message': 'ComfyUI output directory not found'})}\n\n"
                    return
                
                yield f"data: {json.dumps({'type': 'progress', 'progress': 0, 'status': 'Scanning for PNG files...', 'processed': 0, 'found': 0})}\n\n"
                
                # Find all PNG files
                png_files = list(Path(output_dir).rglob("*.png"))
                total_files = len(png_files)
                
                if total_files == 0:
                    yield f"data: {json.dumps({'type': 'complete', 'processed': 0, 'found': 0, 'added': 0})}\n\n"
                    return
                
                yield f"data: {json.dumps({'type': 'progress', 'progress': 5, 'status': f'Found {total_files} PNG files to process...', 'processed': 0, 'found': 0})}\n\n"
                
                processed_count = 0
                found_count = 0
                added_count = 0
                linked_count = 0  # Count of images linked to existing prompts
                
                for i, png_file in enumerate(png_files):
                    try:
                        # Extract metadata from PNG
                        metadata = self._extract_comfyui_metadata(str(png_file))
                        processed_count += 1
                        
                        if metadata:
                            self.logger.debug(f"Found metadata in {os.path.basename(png_file)}: {list(metadata.keys())}")
                            
                            # Parse ComfyUI prompt data
                            parsed_data = self._parse_comfyui_prompt(metadata)
                            self.logger.debug(f"Parsed data keys: {list(parsed_data.keys())}, has prompt: {bool(parsed_data.get('prompt'))}, has parameters: {bool(parsed_data.get('parameters'))}")
                            
                            # Check if we found any meaningful prompt data
                            if parsed_data.get('prompt') or parsed_data.get('parameters'):
                                found_count += 1
                                
                                # Extract readable prompt text
                                prompt_text = self._extract_readable_prompt(parsed_data)
                                
                                # Debug: print what we found and its type
                                if prompt_text:
                                    self.logger.debug(f"Found prompt in {os.path.basename(png_file)} (type: {type(prompt_text)}): {str(prompt_text)[:100]}...")
                                else:
                                    self.logger.debug(f"No readable prompt found in {os.path.basename(png_file)}, parsed_data keys: {list(parsed_data.keys())}")
                                
                                # Ensure prompt_text is a string
                                if prompt_text and not isinstance(prompt_text, str):
                                    self.logger.debug(f"Converting prompt_text from {type(prompt_text)} to string")
                                    prompt_text = str(prompt_text)
                                
                                if prompt_text and prompt_text.strip():
                                    # Try to save to database (will skip duplicates)
                                    try:
                                        # Generate hash for duplicate detection
                                        try:
                                            from ..utils.hashing import generate_prompt_hash
                                        except ImportError:
                                            import sys
                                            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                                            sys.path.insert(0, current_dir)
                                            from utils.hashing import generate_prompt_hash
                                        
                                        prompt_hash = generate_prompt_hash(prompt_text.strip())
                                        self.logger.debug(f"Generated hash for prompt: {prompt_hash[:16]}...")
                                        
                                        # Check if prompt already exists
                                        existing = self.db.get_prompt_by_hash(prompt_hash)
                                        if existing:
                                            self.logger.debug(f"Found existing prompt ID {existing['id']} for image {os.path.basename(png_file)}")
                                            # Link image to existing prompt
                                            try:
                                                self.db.link_image_to_prompt(existing['id'], str(png_file))
                                                linked_count += 1
                                                self.logger.debug(f"Linked image {os.path.basename(png_file)} to existing prompt {existing['id']}")
                                            except Exception as e:
                                                self.logger.error(f"Failed to link image {png_file} to existing prompt: {e}")
                                        else:
                                            # Save new prompt
                                            self.logger.debug(f"Saving new prompt from {os.path.basename(png_file)}")
                                            prompt_id = self.db.save_prompt(
                                                text=prompt_text.strip(),
                                                category='scanned',
                                                tags=['auto-scanned'],
                                                notes=f'Auto-scanned from {os.path.basename(png_file)}',
                                                prompt_hash=prompt_hash
                                            )
                                            
                                            if prompt_id:
                                                added_count += 1
                                                self.logger.info(f"Successfully saved new prompt with ID {prompt_id} from {os.path.basename(png_file)}")
                                                
                                                # Link image to new prompt
                                                try:
                                                    self.db.link_image_to_prompt(prompt_id, str(png_file))
                                                    self.logger.debug(f"Linked image {os.path.basename(png_file)} to new prompt {prompt_id}")
                                                except Exception as e:
                                                    self.logger.error(f"Failed to link image {png_file} to new prompt: {e}")
                                            else:
                                                self.logger.error(f"Failed to save prompt from {os.path.basename(png_file)} - no ID returned")
                                        
                                    except Exception as e:
                                        self.logger.error(f"Failed to save prompt from {png_file}: {e}")
                        
                        # Update progress every 10 files or so
                        if i % 10 == 0 or i == total_files - 1:
                            progress = int((i + 1) / total_files * 100)
                            status = f"Processing file {i + 1}/{total_files}..."
                            
                            yield f"data: {json.dumps({'type': 'progress', 'progress': progress, 'status': status, 'processed': processed_count, 'found': found_count})}\n\n"
                            
                            # Small delay to allow UI updates
                            await asyncio.sleep(0.01)
                    
                    except Exception as e:
                        self.logger.error(f"Error processing {png_file}: {e}")
                        continue
                
                # Send completion message
                self.logger.info(f"Scan completed: processed={processed_count}, found={found_count}, new_prompts_added={added_count}, images_linked_to_existing={linked_count}")
                yield f"data: {json.dumps({'type': 'complete', 'processed': processed_count, 'found': found_count, 'added': added_count, 'linked': linked_count})}\n\n"
                
            except Exception as e:
                self.logger.error(f"Scan error: {e}")
                import traceback
                self.logger.error(f"Scan error traceback: {traceback.format_exc()}")
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        
        # Return streaming response
        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive'
            }
        )
        
        await response.prepare(request)
        
        async for chunk in stream_response():
            await response.write(chunk.encode('utf-8'))
        
        await response.write_eof()
        return response
    
    def _find_comfyui_output_dir(self):
        """Locate the ComfyUI output directory using multiple detection strategies.
        
        Attempts to find the ComfyUI output directory by checking various
        possible locations relative to the current file and common installation
        patterns. Handles different ComfyUI installation types and structures.
        
        Detection Strategy:
        1. Look for 'output' directory in parent directories (up to 10 levels)
        2. Check common ComfyUI installation patterns
        3. Verify directory contains typical ComfyUI subdirectories
        4. Return first valid match found
        
        Returns:
            str or None: Absolute path to ComfyUI output directory, or None
                        if no valid directory is found
        
        Example:
            output_dir = api._find_comfyui_output_dir()
            if output_dir:
                print(f"Found ComfyUI output at: {output_dir}")
            else:
                print("ComfyUI output directory not found")
        """
        import os
        from pathlib import Path
        
        current_file = Path(__file__).resolve()
        self.logger.debug(f"Starting ComfyUI output search from: {current_file}")
        
        # Method 1: Search upward from current file location
        current_dir = current_file.parent
        max_depth = 10  # Prevent infinite loops
        
        for i in range(max_depth):
            # Check if current directory contains ComfyUI markers
            comfyui_markers = ['main.py', 'nodes.py', 'server.py']
            if any((current_dir / marker).exists() for marker in comfyui_markers):
                output_dir = current_dir / "output"
                if output_dir.exists() and output_dir.is_dir():
                    self.logger.debug(f"Found ComfyUI output directory via upward search: {output_dir}")
                    return str(output_dir)
            
            # Move up one directory
            parent = current_dir.parent
            if parent == current_dir:  # Reached filesystem root
                break
            current_dir = parent
        
        # Method 2: Try common installation patterns relative to this file
        base_dir = current_file.parent  # /path/to/ComfyUI/custom_nodes/ComfyUI_PromptManager/py
        possible_paths = [
            # Standard custom node installation: custom_nodes/ComfyUI_PromptManager/py -> output
            base_dir.parent.parent.parent / "output",  # ../../../output
            # Nested custom node: custom_nodes/promptmanager/py -> output  
            base_dir.parent.parent / "output",  # ../../output
            # Direct in ComfyUI root
            base_dir.parent / "output",  # ../output
            base_dir / "output",  # ./output
        ]
        
        # Method 3: Add common ComfyUI installation locations
        common_locations = [
            Path.home() / "ComfyUI" / "output",
            Path.cwd() / "output",
            Path.cwd() / ".." / "output", 
            Path.cwd() / ".." / ".." / "output",
        ]
        
        all_paths = possible_paths + common_locations
        
        for path in all_paths:
            try:
                abs_path = path.resolve()
                if abs_path.exists() and abs_path.is_dir():
                    self.logger.debug(f"Found ComfyUI output directory: {abs_path}")
                    return str(abs_path)
            except (OSError, RuntimeError):
                continue  # Skip invalid paths
        
        self.logger.warning("ComfyUI output directory not found. Searched paths:")
        for path in all_paths:
            try:
                self.logger.warning(f"  - {path.resolve()} (exists: {path.exists()})")
            except (OSError, RuntimeError):
                self.logger.warning(f"  - {path} (invalid path)")
        
        return None
    
    def _extract_comfyui_metadata(self, image_path):
        """Extract ComfyUI workflow metadata from PNG image files.
        
        Reads embedded metadata from PNG files generated by ComfyUI,
        extracting workflow information, parameters, and generation details
        stored in PNG text chunks.
        
        ComfyUI stores metadata in standard PNG text chunks:
        - 'workflow': Complete node graph workflow data
        - 'prompt': Simplified prompt/parameter data
        - Custom fields: Additional generation parameters
        
        Args:
            image_path (str): Path to the PNG image file to analyze
        
        Returns:
            Dict[str, Any]: Dictionary containing extracted metadata:
                - 'workflow': Raw workflow JSON data (if present)
                - 'prompt': Simplified prompt data (if present)
                - Additional custom fields from PNG text chunks
                - Empty dict if no metadata found or file is not PNG
        
        Raises:
            Exception: If file cannot be opened or read (handled gracefully,
                      returns empty dict)
        
        Example:
            metadata = api._extract_comfyui_metadata('output/image_001.png')
            if 'workflow' in metadata:
                print("Found ComfyUI workflow data")
            if 'prompt' in metadata:
                print(f"Prompt data: {metadata['prompt']}")
        """
        try:
            with Image.open(image_path) as img:
                metadata = {}
                if hasattr(img, 'text'):
                    for key, value in img.text.items():
                        metadata[key] = value
                return metadata
        except Exception as e:
            self.logger.error(f"Error reading {image_path}: {e}")
            return {}
    
    def _parse_comfyui_prompt(self, metadata):
        """Parse ComfyUI and A1111 prompt data from metadata."""
        result = {
            'prompt': None,
            'workflow': None,
            'parameters': {},
            'positive_prompt': None,
            'negative_prompt': None
        }
        
        # Check for A1111 style parameters first (like parse-metadata.py)
        if "parameters" in metadata:
            params = metadata["parameters"]
            lines = params.splitlines()
            if lines:
                result['positive_prompt'] = lines[0].strip()
            for line in lines:
                if line.lower().startswith("negative prompt:"):
                    result['negative_prompt'] = line.split(":", 1)[1].strip()
                    break
            # Store raw parameters too
            result['parameters']['parameters'] = params
        
        # If no A1111 format found, proceed with ComfyUI parsing
        if result['positive_prompt'] is None:
            # Check for direct prompt field
            if 'prompt' in metadata:
                try:
                    prompt_data = json.loads(metadata['prompt'])
                    result['prompt'] = prompt_data
                except json.JSONDecodeError:
                    result['prompt'] = metadata['prompt']
            
            # Check for workflow
            if 'workflow' in metadata:
                try:
                    workflow_data = json.loads(metadata['workflow'])
                    result['workflow'] = workflow_data
                except json.JSONDecodeError:
                    result['workflow'] = metadata['workflow']
            
            # Check for other common ComfyUI fields
            common_fields = ['positive', 'negative', 'steps', 'cfg', 'sampler', 'scheduler', 'seed']
            for field in common_fields:
                if field in metadata:
                    try:
                        result['parameters'][field] = json.loads(metadata[field])
                    except json.JSONDecodeError:
                        result['parameters'][field] = metadata[field]
        
        return result
    
    def _extract_readable_prompt(self, parsed_data):
        """Extract human-readable prompt text from ComfyUI/A1111 data using improved logic."""
        import json
        
        # Helper function to convert any value to string safely
        def safe_to_string(value):
            if isinstance(value, str):
                return value
            elif isinstance(value, list):
                # Join list elements with spaces
                return ' '.join(str(item) for item in value if item)
            elif value is not None:
                return str(value)
            return None
        
        # First check if we already extracted a positive prompt (A1111 format)
        if parsed_data.get('positive_prompt'):
            return parsed_data['positive_prompt']
        
        # Check if prompt is already a string
        if isinstance(parsed_data.get('prompt'), str):
            return parsed_data['prompt']
        
        # Check if prompt is a simple value that can be converted
        if parsed_data.get('prompt') and not isinstance(parsed_data.get('prompt'), dict):
            return safe_to_string(parsed_data['prompt'])
        
        prompt_data = parsed_data.get('prompt')
        if isinstance(prompt_data, dict):
            # Use the enhanced logic from parse-metadata.py
            positive_prompt = self._extract_positive_prompt_from_comfyui_data(prompt_data)
            if positive_prompt:
                return positive_prompt
        
        # Check workflow data if available
        workflow_data = parsed_data.get('workflow')
        if isinstance(workflow_data, dict):
            positive_prompt = self._extract_positive_prompt_from_comfyui_data(workflow_data)
            if positive_prompt:
                return positive_prompt
        
        # Check parameters for positive prompt
        if parsed_data.get('parameters', {}).get('positive'):
            return safe_to_string(parsed_data['parameters']['positive'])
        
        return None
    
    def _get_node_inputs(self, node):
        """
        Safely get inputs from a node, handling both dict and list formats.
        Returns a normalized dict format for consistent access.
        
        Old format: inputs is a dict with direct key-value pairs
            inputs = {"text": "my prompt", "seed": 123}
        
        New format: inputs is a list of connection objects
            inputs = [
                {"name": "text", "type": "STRING", "link": null, "widget": {"name": "text"}},
                {"name": "clip", "type": "CLIP", "link": 11}
            ]
        """
        if not isinstance(node, dict):
            return {}
        
        inputs = node.get("inputs", {})
        
        # If inputs is already a dict, return it
        if isinstance(inputs, dict):
            return inputs
        
        # If inputs is a list, convert to dict format
        if isinstance(inputs, list):
            inputs_dict = {}
            for input_item in inputs:
                if isinstance(input_item, dict) and "name" in input_item:
                    name = input_item["name"]
                    # For now, just mark that this input exists
                    # The actual value might be in widgets_values
                    inputs_dict[name] = input_item
            return inputs_dict
        
        # If inputs is neither dict nor list, return empty dict
        return {}
    
    def _find_text_in_node(self, node):
        """
        Try to find text content in a node using various strategies.
        Handles both old and new workflow formats.
        """
        if not isinstance(node, dict):
            return None
        
        # Strategy 1: Check normalized inputs for 'text' field
        inputs = self._get_node_inputs(node)
        if "text" in inputs and isinstance(inputs["text"], str):
            return inputs["text"]
        
        # Strategy 2: For text encoder nodes, check widgets_values
        class_type = node.get("class_type", node.get("type", ""))
        text_encoder_types = [
            'CLIPTextEncode', 'CLIPTextEncodeSDXL', 'CLIPTextEncodeSDXLRefiner',
            'CLIPTextEncodeFlux', 'PromptManager', 'PromptManagerText',
            'BNK_CLIPTextEncoder', 'Text Encoder', 'CLIP Text Encode'
        ]
        
        if any(encoder_type.lower() in class_type.lower() for encoder_type in text_encoder_types):
            widgets_values = node.get("widgets_values", [])
            if widgets_values and len(widgets_values) > 0:
                # First widget is usually the text for these nodes
                if isinstance(widgets_values[0], str) and widgets_values[0].strip():
                    return widgets_values[0]
        
        return None
    
    def _extract_positive_prompt_from_comfyui_data(self, data):
        """Extract positive prompt from ComfyUI data, handling both old and new formats."""
        if not isinstance(data, dict):
            return None
        
        # Build nodes dictionary
        nodes_by_id = {}
        if "nodes" in data:
            # Handle nodes array format
            for node in data["nodes"]:
                if isinstance(node, dict):
                    nid = node.get("id")
                    if nid is not None:
                        nodes_by_id[nid] = node
        else:
            # Handle flat dictionary format (node_id -> node_data)
            for nid_str, node in data.items():
                try:
                    nid = int(nid_str)
                except:
                    nid = nid_str
                if isinstance(node, dict):
                    if "id" in node:
                        nid = node["id"]
                    nodes_by_id[nid] = node
        
        if not nodes_by_id:
            return None
        
        # First, try to find positive/negative connection pattern
        pos_id = None
        for node in nodes_by_id.values():
            if isinstance(node, dict):
                inputs = self._get_node_inputs(node)  # Use our safe function
                if "positive" in inputs and "negative" in inputs:
                    try:
                        # Handle both old format (direct value) and new format (connection object)
                        pos_input = inputs["positive"]
                        if isinstance(pos_input, list) and len(pos_input) > 0:
                            pos_id = int(pos_input[0])
                            break
                    except:
                        continue
        
        # Get text from the positive node
        if pos_id is not None and pos_id in nodes_by_id:
            text_val = self._find_text_in_node(nodes_by_id[pos_id])
            if text_val:
                return text_val
        
        # Fallback: find any text encoder node with text content
        # Collect all text encoder nodes
        text_nodes = []
        for node in nodes_by_id.values():
            if isinstance(node, dict):
                class_type = node.get('class_type', node.get('type', ''))
                
                # Check if this is a text encoder node
                text_val = self._find_text_in_node(node)
                if text_val:
                    # Try to determine if this is positive or negative
                    node_title = node.get('title', '').lower()
                    if 'neg' not in node_title and 'negative' not in node_title:
                        # Prioritize non-negative prompts
                        text_nodes.insert(0, text_val)
                    else:
                        text_nodes.append(text_val)
        
        # Return the first positive-looking prompt
        if text_nodes:
            return text_nodes[0]
        
        return None

    # Logging API endpoints
    async def get_logs(self, request):
        """
        Get recent log entries.
        GET /prompt_manager/logs?limit=100&level=INFO
        """
        try:
            # Import logger here to avoid circular imports
            try:
                from ..utils.logging_config import get_logger_manager
            except ImportError:
                import sys
                current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                sys.path.insert(0, current_dir)
                from utils.logging_config import get_logger_manager
            
            logger_manager = get_logger_manager()
            
            # Get query parameters
            limit = int(request.query.get('limit', 100))
            level = request.query.get('level', None)
            
            # Validate limit
            if limit > 1000:
                limit = 1000
            elif limit < 1:
                limit = 1
            
            # Get logs from memory buffer
            logs = logger_manager.get_recent_logs(limit=limit, level=level)
            
            return web.json_response({
                'success': True,
                'logs': logs,
                'count': len(logs),
                'level_filter': level,
                'limit': limit
            })
            
        except Exception as e:
            self.logger.error(f"Get logs error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e),
                'logs': []
            }, status=500)

    async def get_log_files(self, request):
        """
        Get information about available log files.
        GET /prompt_manager/logs/files
        """
        try:
            try:
                from ..utils.logging_config import get_logger_manager
            except ImportError:
                import sys
                current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                sys.path.insert(0, current_dir)
                from utils.logging_config import get_logger_manager
            
            logger_manager = get_logger_manager()
            log_files = logger_manager.get_log_files()
            
            return web.json_response({
                'success': True,
                'files': log_files,
                'count': len(log_files)
            })
            
        except Exception as e:
            self.logger.error(f"Get log files error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e),
                'files': []
            }, status=500)

    async def download_log_file(self, request):
        """
        Download a specific log file.
        GET /prompt_manager/logs/download/{filename}
        """
        try:
            filename = request.match_info['filename']
            
            try:
                from ..utils.logging_config import get_logger_manager
            except ImportError:
                import sys
                current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                sys.path.insert(0, current_dir)
                from utils.logging_config import get_logger_manager
            
            logger_manager = get_logger_manager()
            
            # Validate filename for security
            if not filename or '..' in filename or '/' in filename or '\\' in filename:
                return web.json_response({
                    'success': False,
                    'error': 'Invalid filename'
                }, status=400)
            
            # Get the file content
            log_file_path = logger_manager.log_dir / filename
            
            if not log_file_path.exists():
                return web.json_response({
                    'success': False,
                    'error': 'Log file not found'
                }, status=404)
            
            # Read file content
            with open(log_file_path, 'rb') as f:
                file_content = f.read()
            
            return web.Response(
                body=file_content,
                content_type='text/plain',
                headers={
                    'Content-Disposition': f'attachment; filename="{filename}"',
                    'Content-Length': str(len(file_content))
                }
            )
            
        except Exception as e:
            self.logger.error(f"Download log file error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def truncate_logs(self, request):
        """
        Truncate all log files.
        POST /prompt_manager/logs/truncate
        """
        try:
            try:
                from ..utils.logging_config import get_logger_manager
            except ImportError:
                import sys
                current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                sys.path.insert(0, current_dir)
                from utils.logging_config import get_logger_manager
            
            logger_manager = get_logger_manager()
            results = logger_manager.truncate_logs()
            
            return web.json_response({
                'success': True,
                'message': f"Truncated {len(results['truncated'])} log files",
                'results': results
            })
            
        except Exception as e:
            self.logger.error(f"Truncate logs error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_log_config(self, request):
        """
        Get current logging configuration.
        GET /prompt_manager/logs/config
        """
        try:
            try:
                from ..utils.logging_config import get_logger_manager
            except ImportError:
                import sys
                current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                sys.path.insert(0, current_dir)
                from utils.logging_config import get_logger_manager
            
            logger_manager = get_logger_manager()
            config = logger_manager.get_config()
            
            return web.json_response({
                'success': True,
                'config': config
            })
            
        except Exception as e:
            self.logger.error(f"Get log config error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def update_log_config(self, request):
        """
        Update logging configuration.
        POST /prompt_manager/logs/config
        Body: {"level": "DEBUG", "console_logging": true, ...}
        """
        try:
            data = await request.json()
            
            try:
                from ..utils.logging_config import get_logger_manager
            except ImportError:
                import sys
                current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                sys.path.insert(0, current_dir)
                from utils.logging_config import get_logger_manager
            
            logger_manager = get_logger_manager()
            
            # Validate level if provided
            if 'level' in data:
                valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
                if data['level'].upper() not in valid_levels:
                    return web.json_response({
                        'success': False,
                        'error': f'Invalid log level. Must be one of: {valid_levels}'
                    }, status=400)
                data['level'] = data['level'].upper()
            
            logger_manager.update_config(data)
            
            return web.json_response({
                'success': True,
                'message': 'Logging configuration updated',
                'config': logger_manager.get_config()
            })
            
        except Exception as e:
            self.logger.error(f"Update log config error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def get_log_stats(self, request):
        """
        Get logging statistics.
        GET /prompt_manager/logs/stats
        """
        try:
            try:
                from ..utils.logging_config import get_logger_manager
            except ImportError:
                import sys
                current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                sys.path.insert(0, current_dir)
                from utils.logging_config import get_logger_manager

            logger_manager = get_logger_manager()
            stats = logger_manager.get_log_stats()

            return web.json_response({
                'success': True,
                'stats': stats
            })

        except Exception as e:
            self.logger.error(f"Get log stats error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    # ========================================================================
    # AutoTag Endpoints
    # ========================================================================

    async def get_autotag_models(self, request):
        """
        Get status of available AutoTag models.
        GET /prompt_manager/autotag/models

        Returns model availability, download status, loaded status, and configuration.
        """
        try:
            from .autotag import get_autotag_service

            service = get_autotag_service()
            models_status = service.get_models_status()

            return web.json_response({
                'success': True,
                'models': models_status,
                'default_prompt': service.default_prompt,
                'model_loaded': service.is_model_loaded(),
                'loaded_model_type': service.get_loaded_model_type()
            })

        except Exception as e:
            self.logger.error(f"Get autotag models error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def download_autotag_model(self, request):
        """
        Download an AutoTag model with streaming progress.
        POST /prompt_manager/autotag/download/{model_type}

        Streams SSE progress updates during download.
        """
        import json
        import asyncio

        model_type = request.match_info.get('model_type')

        async def stream_response():
            try:
                from .autotag import get_autotag_service

                service = get_autotag_service()

                if model_type not in service.models_config:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Invalid model type: {model_type}'})}\n\n"
                    return

                yield f"data: {json.dumps({'type': 'progress', 'progress': 0, 'status': 'Starting download...'})}\n\n"

                # Define progress callback
                progress_data = {'last_progress': 0}

                def progress_callback(status: str, progress: float):
                    progress_data['last_progress'] = progress

                # Perform download (blocking in thread)
                loop = asyncio.get_event_loop()
                success = await loop.run_in_executor(
                    None,
                    lambda: service.download_model(model_type, progress_callback)
                )

                if success:
                    yield f"data: {json.dumps({'type': 'complete', 'progress': 100, 'status': 'Download complete'})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Download failed'})}\n\n"

            except Exception as e:
                self.logger.error(f"Download model error: {e}")
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive'
            }
        )

        await response.prepare(request)

        async for chunk in stream_response():
            await response.write(chunk.encode('utf-8'))

        await response.write_eof()
        return response

    async def start_autotag(self, request):
        """
        Start batch auto-tagging with streaming progress.
        GET /prompt_manager/autotag/start

        Query params:
            model_type: "gguf" or "hf"
            prompt: custom prompt text
            keep_in_memory: "true" or "false" (default true) - keep model loaded after processing

        Streams SSE progress updates during processing.
        """
        import json
        import asyncio
        from pathlib import Path

        # Read from query params (EventSource only supports GET)
        model_type = request.query.get('model_type', 'gguf')
        custom_prompt = request.query.get('prompt', '')
        skip_tagged = request.query.get('skip_tagged', 'true').lower() == 'true'
        keep_in_memory = request.query.get('keep_in_memory', 'true').lower() == 'true'
        use_gpu = True

        async def stream_response():
            try:
                from .autotag import get_autotag_service

                service = get_autotag_service()

                # Check model is downloaded
                status = service.get_models_status()
                if not status.get(model_type, {}).get('downloaded'):
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Model {model_type} not downloaded'})}\n\n"
                    return

                yield f"data: {json.dumps({'type': 'progress', 'progress': 0, 'status': 'Loading model...'})}\n\n"

                # Load model in thread pool
                loop = asyncio.get_event_loop()
                try:
                    await loop.run_in_executor(
                        None,
                        lambda: service.load_model(model_type, use_gpu)
                    )
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to load model: {str(e)}'})}\n\n"
                    return

                # Set custom prompt if provided
                if custom_prompt:
                    service.custom_prompt = custom_prompt

                yield f"data: {json.dumps({'type': 'progress', 'progress': 5, 'status': 'Model loaded. Fetching all images from database...'})}\n\n"

                # Get ALL images from database (only images with linked prompts)
                images = self.db.get_all_images()

                total_files = len(images)
                if total_files == 0:
                    yield f"data: {json.dumps({'type': 'complete', 'processed': 0, 'tagged': 0, 'skipped': 0, 'status': 'No images with linked prompts found'})}\n\n"
                    service.unload_model()
                    return

                yield f"data: {json.dumps({'type': 'progress', 'progress': 10, 'status': f'Found {total_files} images. Processing...'})}\n\n"

                processed = 0
                tagged = 0
                skipped = 0
                errors = 0

                for i, image_data in enumerate(images):
                    image_path = image_data.get('image_path')
                    prompt_id = image_data.get('prompt_id')

                    if not image_path or not prompt_id:
                        skipped += 1
                        continue

                    # Check if file exists
                    if not Path(image_path).exists():
                        skipped += 1
                        continue

                    # Check if image already has real tags (skip_tagged option)
                    if skip_tagged:
                        prompt_tags = image_data.get('prompt_tags', [])
                        if isinstance(prompt_tags, str):
                            prompt_tags = [t.strip() for t in prompt_tags.split(',') if t.strip()]
                        # Filter out "auto-scanned" - it's not a real tag
                        real_tags = [t for t in prompt_tags if t != 'auto-scanned']
                        if real_tags:
                            skipped += 1
                            if i % 5 == 0 or i == total_files - 1:
                                progress = 10 + int((i + 1) / total_files * 85)
                                yield f"data: {json.dumps({'type': 'progress', 'progress': progress, 'status': f'Skipping {i+1}/{total_files} (already tagged)...', 'processed': processed, 'tagged': tagged, 'skipped': skipped})}\n\n"
                                await asyncio.sleep(0.01)
                            continue

                    try:
                        # Generate tags
                        tags = await loop.run_in_executor(
                            None,
                            lambda p=str(image_path): service.generate_tags(p)
                        )

                        processed += 1

                        if tags:
                            # Get existing prompt
                            existing_prompt = self.db.get_prompt_by_id(prompt_id)
                            if existing_prompt:
                                existing_tags = existing_prompt.get('tags', [])
                                if isinstance(existing_tags, str):
                                    existing_tags = [t.strip() for t in existing_tags.split(',') if t.strip()]

                                new_tags = [t for t in tags if t not in existing_tags]
                                if new_tags:
                                    all_tags = existing_tags + new_tags
                                    self.db.update_prompt_metadata(
                                        prompt_id,
                                        tags=all_tags
                                    )
                                    tagged += 1
                                else:
                                    skipped += 1
                            else:
                                skipped += 1
                        else:
                            skipped += 1

                        if i % 5 == 0 or i == total_files - 1:
                            progress = 10 + int((i + 1) / total_files * 85)
                            yield f"data: {json.dumps({'type': 'progress', 'progress': progress, 'status': f'Processing {i+1}/{total_files}...', 'processed': processed, 'tagged': tagged, 'skipped': skipped})}\n\n"
                            await asyncio.sleep(0.01)

                    except Exception as img_err:
                        self.logger.error(f"Error processing {image_path}: {img_err}")
                        errors += 1
                        processed += 1

                # Only unload model if keep_in_memory is False
                if not keep_in_memory:
                    service.unload_model()
                    model_status = 'Model unloaded'
                else:
                    model_status = 'Model kept in memory'

                yield f"data: {json.dumps({'type': 'complete', 'progress': 100, 'processed': processed, 'tagged': tagged, 'skipped': skipped, 'errors': errors, 'status': 'Complete', 'model_status': model_status, 'model_loaded': keep_in_memory})}\n\n"

            except Exception as e:
                self.logger.error(f"AutoTag error: {e}")
                import traceback
                self.logger.error(f"AutoTag traceback: {traceback.format_exc()}")
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive'
            }
        )

        await response.prepare(request)

        async for chunk in stream_response():
            await response.write(chunk.encode('utf-8'))

        await response.write_eof()
        return response

    async def autotag_single(self, request):
        """
        Generate tags for a single image (for Review mode).
        POST /prompt_manager/autotag/single

        Request body:
            {
                "image_path": "/path/to/image.png",
                "model_type": "gguf",
                "prompt": "optional custom prompt",
                "use_gpu": true
            }
        """
        try:
            data = await request.json()
            image_path = data.get('image_path')
            model_type = data.get('model_type', 'gguf')
            custom_prompt = data.get('prompt')
            use_gpu = data.get('use_gpu', True)

            if not image_path:
                return web.json_response({
                    'success': False,
                    'error': 'image_path is required'
                }, status=400)

            from .autotag import get_autotag_service
            import asyncio

            service = get_autotag_service()

            if not service.is_model_loaded() or service.get_loaded_model_type() != model_type:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: service.load_model(model_type, use_gpu)
                )

            if custom_prompt:
                service.custom_prompt = custom_prompt

            loop = asyncio.get_event_loop()
            tags = await loop.run_in_executor(
                None,
                lambda: service.generate_tags(image_path)
            )

            prompt_id = None
            try:
                with self.db.model.get_connection() as conn:
                    cursor = conn.execute(
                        "SELECT prompt_id FROM generated_images WHERE image_path = ?",
                        (image_path,)
                    )
                    row = cursor.fetchone()
                    if row:
                        prompt_id = row[0]
            except Exception as e:
                self.logger.warning(f"Could not find linked prompt: {e}")

            return web.json_response({
                'success': True,
                'tags': tags,
                'prompt_id': prompt_id,
                'image_path': image_path
            })

        except Exception as e:
            self.logger.error(f"AutoTag single error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def apply_autotag(self, request):
        """
        Apply selected tags to a prompt.
        POST /prompt_manager/autotag/apply

        Request body:
            {
                "prompt_id": 123,
                "tags": ["tag1", "tag2", ...]
            }
        """
        try:
            data = await request.json()
            prompt_id = data.get('prompt_id')
            tags = data.get('tags', [])

            if not prompt_id:
                return web.json_response({
                    'success': False,
                    'error': 'prompt_id is required'
                }, status=400)

            if not tags:
                return web.json_response({
                    'success': True,
                    'message': 'No tags to apply'
                })

            prompt = self.db.get_prompt_by_id(prompt_id)
            if not prompt:
                return web.json_response({
                    'success': False,
                    'error': f'Prompt {prompt_id} not found'
                }, status=404)

            existing_tags = prompt.get('tags', [])
            if isinstance(existing_tags, str):
                existing_tags = [t.strip() for t in existing_tags.split(',') if t.strip()]

            new_tags = [t for t in tags if t not in existing_tags]
            all_tags = existing_tags + new_tags

            self.db.update_prompt_metadata(
                prompt_id,
                tags=all_tags
            )

            return web.json_response({
                'success': True,
                'added_tags': new_tags,
                'total_tags': len(all_tags)
            })

        except Exception as e:
            self.logger.error(f"Apply autotag error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def unload_autotag_model(self, request):
        """
        Manually unload the AutoTag model from memory.
        POST /prompt_manager/autotag/unload

        Used to free up VRAM when the model is no longer needed.
        """
        try:
            from .autotag import get_autotag_service

            service = get_autotag_service()

            if not service.is_model_loaded():
                return web.json_response({
                    'success': True,
                    'message': 'No model was loaded'
                })

            model_type = service.get_loaded_model_type()
            service.unload_model()

            return web.json_response({
                'success': True,
                'message': f'{model_type.upper()} model unloaded successfully',
                'model_loaded': False
            })

        except Exception as e:
            self.logger.error(f"Unload autotag model error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)

    async def scan_output_dir(self, request):
        """
        Scan ComfyUI output directory for images.
        GET /prompt_manager/scan_output_dir

        Returns a list of images in the output directory for autotag review mode.
        """
        from pathlib import Path

        try:
            output_dir = self._find_comfyui_output_dir()
            if not output_dir:
                return web.json_response({
                    'success': False,
                    'error': 'ComfyUI output directory not found'
                }, status=404)

            output_path = Path(output_dir)
            image_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff']

            images = []
            seen_paths = set()

            for ext in image_extensions:
                for pattern in [f"*{ext.lower()}", f"*{ext.upper()}"]:
                    for image_path in output_path.rglob(pattern):
                        if 'thumbnails' not in image_path.parts:
                            normalized_path = str(image_path).lower()
                            if normalized_path not in seen_paths:
                                seen_paths.add(normalized_path)

                                rel_path = image_path.relative_to(output_path)

                                # Check for thumbnail
                                thumbnail_url = None
                                thumbnails_dir = output_path / "thumbnails"
                                if thumbnails_dir.exists():
                                    # Preserve subdirectory structure in thumbnail path
                                    rel_path_no_ext = rel_path.with_suffix('')
                                    thumbnail_path = thumbnails_dir / f"{rel_path_no_ext.as_posix()}_thumb{image_path.suffix}"
                                    if thumbnail_path.exists():
                                        thumbnail_url = f'/prompt_manager/images/serve/thumbnails/{rel_path_no_ext.as_posix()}_thumb{image_path.suffix}'

                                images.append({
                                    'filename': image_path.name,
                                    'path': str(image_path),
                                    'relative_path': str(rel_path),
                                    'url': f'/prompt_manager/images/serve/{rel_path.as_posix()}',
                                    'thumbnail_url': thumbnail_url
                                })

            # Sort by filename
            images.sort(key=lambda x: x['filename'])

            self.logger.info(f"Found {len(images)} images in output directory")

            return web.json_response({
                'success': True,
                'images': images,
                'count': len(images)
            })

        except Exception as e:
            self.logger.error(f"Scan output dir error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)
