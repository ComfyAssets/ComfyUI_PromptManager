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
    """API class for PromptManager database operations."""

    def __init__(self):
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
        """Add API routes to ComfyUI server using decorator pattern."""

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

        self.logger.info("All routes registered with decorator pattern")

    async def search_prompts(self, request):
        """
        Search prompts endpoint.
        GET /prompt_manager/search?text=...&category=...&tags=...&min_rating=...&limit=...
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
        """
        Get recent prompts endpoint with pagination support.
        GET /prompt_manager/recent?limit=50&page=2&offset=100
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
        """
        Get all categories endpoint.
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
        """
        Get all tags endpoint.
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
        """
        Save a new prompt endpoint.
        POST /prompt_manager/save
        Body: {"text": "...", "category": "...", "tags": [...], "rating": 5, "notes": "..."}
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
        """
        Delete a prompt endpoint.
        DELETE /prompt_manager/delete/{prompt_id}
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
            for ext in media_extensions:
                for media_path in output_path.rglob(f"*{ext}"):
                    # Skip files in thumbnails directory
                    if 'thumbnails' not in media_path.parts:
                        all_images.append(media_path)
                for media_path in output_path.rglob(f"*{ext.upper()}"):
                    # Skip files in thumbnails directory
                    if 'thumbnails' not in media_path.parts:
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
                        thumbnail_rel_path = f"thumbnails/{media_path.stem}_thumb{thumbnail_ext}"
                        thumbnail_abs_path = output_path / thumbnail_rel_path
                        
                        if thumbnail_abs_path.exists():
                            thumbnail_url = f'/prompt_manager/images/serve/{thumbnail_rel_path}'
                    
                    images.append({
                        'id': str(hash(str(media_path))),  # Simple hash for ID
                        'filename': media_path.name,
                        'path': str(media_path),
                        'relative_path': str(rel_path),
                        'url': f'/prompt_manager/images/serve/{rel_path}',  # Always original media
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
        """
        Generate thumbnails for gallery images.
        
        SAFETY GUARANTEE: This function NEVER modifies original images.
        - Only READS from original image files (read-only access)
        - Only WRITES to separate thumbnails directory
        - Uses PIL's read-only operations with context managers
        - All generated files are clearly marked with '_thumb' suffix
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
                    if is_video:
                        thumbnail_path = thumbnails_dir / f"{rel_path.stem}_thumb.jpg"
                    else:
                        thumbnail_path = thumbnails_dir / f"{rel_path.stem}_thumb{rel_path.suffix}"
                    
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
                    'message': 'Scanning for images to process...'
                })
                
                # Find all media files (images and videos)
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
                
                total_images = len(media_files)
                
                await send_progress('start', {
                    'total_images': total_images,
                    'phase': 'processing',
                    'message': f'Found {total_images} media files to process'
                })
                
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
                        if is_video:
                            thumbnail_path = thumbnails_dir / f"{rel_path.stem}_thumb.jpg"
                        else:
                            thumbnail_path = thumbnails_dir / f"{rel_path.stem}_thumb{rel_path.suffix}"
                        
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
                        
                        # Send progress update every 10 images or every 2% 
                        if (i + 1) % 10 == 0 or (i + 1) % max(1, total_images // 50) == 0 or i == total_images - 1:
                            elapsed = time.time() - start_time
                            progress_percent = ((i + 1) / total_images) * 100
                            rate = (i + 1) / elapsed if elapsed > 0 else 0
                            eta = ((total_images - i - 1) / rate) if rate > 0 else 0
                            
                            await send_progress('progress', {
                                'processed': i + 1,
                                'total_images': total_images,
                                'generated': generated_count,
                                'skipped': skipped_count,
                                'percentage': round(progress_percent, 1),
                                'rate': round(rate, 1),
                                'eta': round(eta, 0),
                                'elapsed': round(elapsed, 1),
                                'current_file': media_file.name
                            })
                        
                    except Exception as e:
                        error_msg = f"Failed to generate thumbnail for {media_file.name}: {str(e)}"
                        errors.append(error_msg)
                        self.logger.warning(error_msg)
                
                elapsed_time = time.time() - start_time
                
                # Send completion event
                await send_progress('complete', {
                    'count': generated_count,
                    'skipped': skipped_count,
                    'total_images': total_images,
                    'errors': errors,
                    'elapsed_time': round(elapsed_time, 2),
                    'processing_rate': round((total_images / elapsed_time) if elapsed_time > 0 else 0, 2),
                    'message': f'Generated {generated_count} new thumbnails, skipped {skipped_count} existing'
                })
                
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
        """Run system diagnostics."""
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
        """
        Comprehensive database maintenance endpoint.
        POST /prompt_manager/maintenance
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
            if 'prune_orphaned_prompts' in operations:
                try:
                    with self.db.model.get_connection() as conn:
                        # Find prompts that have no images linked to them
                        cursor = conn.execute("""
                            SELECT p.id 
                            FROM prompts p 
                            LEFT JOIN generated_images gi ON p.id = gi.prompt_id 
                            WHERE gi.prompt_id IS NULL
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
                        'message': f'Removed {removed_count} orphaned prompts (prompts with no linked images)'
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
        """Find the ComfyUI output directory with improved detection logic."""
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
                    self.logger.info(f"Found ComfyUI output directory: {abs_path}")
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
        """Extract ComfyUI metadata from a PNG file."""
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
    
    def _extract_positive_prompt_from_comfyui_data(self, data):
        """Extract positive prompt from ComfyUI data using the logic from parse-metadata.py."""
        if not isinstance(data, dict):
            return None
        
        # Build nodes dictionary similar to parse-metadata.py
        nodes_by_id = {}
        if "nodes" in data:
            # Handle nodes array format
            for node in data["nodes"]:
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
            inputs = node.get("inputs", {})
            if "positive" in inputs and "negative" in inputs:
                try:
                    pos_id = int(inputs["positive"][0])
                    break
                except:
                    continue
        
        # Get text from the positive node
        if pos_id is not None and pos_id in nodes_by_id:
            text_val = nodes_by_id[pos_id].get("inputs", {}).get("text")
            if isinstance(text_val, str):
                return text_val
        
        # Fallback: find any text encoder node with text content
        text_encoder_types = [
            'CLIPTextEncode', 'CLIPTextEncodeSDXL', 'CLIPTextEncodeSDXLRefiner',
            'PromptManager', 'BNK_CLIPTextEncoder', 'Text Encoder', 'CLIP Text Encode'
        ]
        
        for node in nodes_by_id.values():
            if isinstance(node, dict):
                class_type = node.get('class_type', '')
                inputs = node.get('inputs', {})
                
                # Check if this is a text encoder node
                if any(encoder_type.lower() in class_type.lower() for encoder_type in text_encoder_types):
                    if 'text' in inputs and isinstance(inputs['text'], str):
                        return inputs['text']
        
        # Final fallback: collect all text fields and return the first non-empty one
        text_fields = []
        for node in nodes_by_id.values():
            if isinstance(node, dict):
                inputs = node.get("inputs", {})
                text_val = inputs.get("text")
                if isinstance(text_val, str) and text_val.strip():
                    text_fields.append(text_val)
        
        # Return the first text field (likely positive prompt)
        if text_fields:
            return text_fields[0]
        
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
