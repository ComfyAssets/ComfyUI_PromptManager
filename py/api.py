# PromptManager/py/api.py

import datetime
import json
import traceback
from typing import Any, Dict, List, Optional

import server
from aiohttp import web

# Import database operations
try:
    from ..database.operations import PromptDatabase
except ImportError:
    # Fallback for when module isn't imported as package
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from database.operations import PromptDatabase


class PromptManagerAPI:
    """API class for PromptManager database operations."""

    def __init__(self):
        self.db = PromptDatabase()

        # Run cleanup on initialization to remove any existing duplicates
        try:
            removed = self.db.cleanup_duplicates()
            if removed > 0:
                print(
                    f"[PromptManager] Startup cleanup: removed {removed} duplicate prompts"
                )
        except Exception as e:
            print(f"[PromptManager] Startup cleanup failed: {e}")

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

        @routes.get("/prompt_manager/stats")
        async def get_statistics_route(request):
            return await self.get_statistics(request)

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

        @routes.get("/prompt_manager/images/{image_id}/file")
        async def serve_image_route(request):
            return await self.serve_image(request)

        @routes.post("/prompt_manager/images/link")
        async def link_image_route(request):
            return await self.link_image_to_prompt(request)

        @routes.delete("/prompt_manager/images/{image_id}")
        async def delete_image_route(request):
            return await self.delete_image(request)

        # Diagnostic endpoints
        @routes.get("/prompt_manager/diagnostics")
        async def run_diagnostics_route(request):
            return await self.run_diagnostics(request)

        @routes.post("/prompt_manager/diagnostics/test-link")
        async def test_image_link_route(request):
            return await self.test_image_link(request)

        print("[PromptManager] All routes registered with decorator pattern")

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
            print(f"[PromptManager API] Search error: {e}")
            print(traceback.format_exc())
            return web.json_response(
                {"success": False, "error": f"Search failed: {str(e)}", "results": []},
                status=500,
            )

    async def get_recent_prompts(self, request):
        """
        Get recent prompts endpoint.
        GET /prompt_manager/recent?limit=...
        """
        try:
            limit = int(request.query.get("limit", 20))

            results = self.db.get_recent_prompts(limit=limit)

            return web.json_response(
                {"success": True, "results": results, "count": len(results)}
            )

        except Exception as e:
            print(f"[PromptManager API] Recent prompts error: {e}")
            print(traceback.format_exc())
            return web.json_response(
                {
                    "success": False,
                    "error": f"Failed to get recent prompts: {str(e)}",
                    "results": [],
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
            print(f"[PromptManager API] Categories error: {e}")
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
            print(f"[PromptManager API] Tags error: {e}")
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

            # Save prompt
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
            print(f"[PromptManager API] Save error: {e}")
            print(traceback.format_exc())
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
            print(f"[PromptManager API] Delete error: {e}")
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
            print(f"[PromptManager API] Cleanup error: {e}")
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
                    "SELECT COUNT(DISTINCT category) as total FROM prompts WHERE category IS NOT NULL"
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

                return web.json_response(
                    {
                        "success": True,
                        "stats": {
                            "total_prompts": total_prompts,
                            "unique_categories": total_categories,
                            "total_tags": len(all_tags),
                            "average_rating": (
                                round(avg_rating, 2) if avg_rating else None
                            ),
                            "recent_prompts": total_prompts,  # For now, use total as recent count
                        },
                    }
                )

        except Exception as e:
            print(f"[PromptManager API] Stats error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to get statistics: {str(e)}"},
                status=500,
            )

    async def get_settings(self, request):
        """Get current settings."""
        try:
            # For now, return default settings
            return web.json_response(
                {"success": True, "settings": {"result_timeout": 5}}
            )
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
            print(f"[PromptManager API] Update prompt error: {e}")
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
            print(f"[PromptManager API] Update rating error: {e}")
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
            print(f"[PromptManager API] Add tag error: {e}")
            return web.json_response(
                {"success": False, "error": f"Failed to add tag: {str(e)}"}, status=500
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
            print(f"[PromptManager API] Remove tag error: {e}")
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
            print(f"[PromptManager API] Bulk delete error: {e}")
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
            print(f"[PromptManager API] Bulk add tags error: {e}")
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
            print(f"[PromptManager API] Bulk set category error: {e}")
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
            print(f"[PromptManager API] Export error: {e}")
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
                print(f"[PromptManager API] JSON cleaning error: {json_error}")
                # Fallback to original response
                return web.json_response({
                    'success': True,
                    'images': cleaned_images
                })
            
        except Exception as e:
            print(f"[PromptManager API] Get prompt images error: {e}")
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
            print(f"[PromptManager API] Get recent images error: {e}")
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
            print(f"[PromptManager API] Search images error: {e}")
            return web.json_response({
                'success': False,
                'error': str(e)
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
            print(f"[PromptManager API] Serve image error: {e}")
            return web.json_response({'error': str(e)}, status=500)

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
            print(f"[PromptManager API] Link image error: {e}")
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
            print(f"[PromptManager API] Delete image error: {e}")
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
            print(f"[PromptManager API] Diagnostics error: {e}")
            import traceback
            traceback.print_exc()
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
            print(f"[PromptManager API] Test link error: {e}")
            import traceback
            traceback.print_exc()
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
            print(f"[PromptManager API] Maintenance error: {e}")
            import traceback
            traceback.print_exc()
            return web.json_response({
                'success': False,
                'error': f'Maintenance failed: {str(e)}'
            }, status=500)
