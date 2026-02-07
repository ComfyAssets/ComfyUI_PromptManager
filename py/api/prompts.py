"""Prompt API routes for PromptManager."""

import datetime
import json

from aiohttp import web


class PromptRoutesMixin:
    """Mixin providing prompt-related API endpoints."""

    def _register_prompt_routes(self, routes):
        @routes.get("/prompt_manager/search")
        async def search_prompts_route(request):
            return await self.search_prompts(request)

        @routes.get("/prompt_manager/recent")
        async def get_recent_prompts_route(request):
            return await self.get_recent_prompts(request)

        @routes.get("/prompt_manager/categories")
        async def get_categories_route(request):
            return await self.get_categories(request)

        # Tag management endpoints (must be registered BEFORE /prompt_manager/tags)
        @routes.get("/prompt_manager/tags/stats")
        async def get_tags_stats_route(request):
            return await self.get_tags_stats(request)

        @routes.get("/prompt_manager/tags/filter")
        async def get_tags_filter_route(request):
            return await self.get_tags_filter(request)

        # Bulk tag operations (register BEFORE {tag_name} to avoid path param match)
        @routes.post("/prompt_manager/tags/merge")
        async def merge_tags_route(request):
            return await self.merge_tags_endpoint(request)

        @routes.put("/prompt_manager/tags/{tag_name}")
        async def rename_tag_route(request):
            return await self.rename_tag_endpoint(request)

        @routes.delete("/prompt_manager/tags/{tag_name}")
        async def delete_tag_route(request):
            return await self.delete_tag_endpoint(request)

        @routes.get("/prompt_manager/tags/{tag_name}/prompts")
        async def get_tag_prompts_route(request):
            return await self.get_tag_prompts(request)

        @routes.get("/prompt_manager/tags")
        async def get_tags_route(request):
            return await self.get_tags(request)

        @routes.post("/prompt_manager/save")
        async def save_prompt_route(request):
            return await self.save_prompt(request)

        @routes.delete("/prompt_manager/delete/{prompt_id}")
        async def delete_prompt_route(request):
            return await self.delete_prompt(request)

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

    async def search_prompts(self, request):
        """Search for prompts using multiple filter criteria."""
        try:
            text = request.query.get("text", "").strip()
            category = request.query.get("category", "").strip()
            tags_str = request.query.get("tags", "").strip()
            min_rating = request.query.get("min_rating", 0)
            limit = int(request.query.get("limit", 50))

            tags = None
            if tags_str:
                tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()]

            try:
                min_rating = int(min_rating) if min_rating else None
            except ValueError:
                min_rating = None

            results = await self._run_in_executor(
                self.db.search_prompts,
                text=text if text else None,
                category=category if category else None,
                tags=tags,
                rating_min=min_rating,
                limit=limit,
            )
            self._enrich_prompt_images(results)

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
        """Retrieve recently created prompts with pagination support."""
        try:
            limit = int(request.query.get("limit", 50))
            page = int(request.query.get("page", 1))
            offset = int(request.query.get("offset", 0))

            if page > 1 and offset == 0:
                offset = (page - 1) * limit

            if limit > 1000:
                limit = 1000
            elif limit < 1:
                limit = 1

            results = await self._run_in_executor(
                self.db.get_recent_prompts, limit=limit, offset=offset
            )
            self._enrich_prompt_images(results["prompts"])

            return web.json_response(
                {
                    "success": True,
                    "results": results["prompts"],
                    "pagination": {
                        "total": results["total"],
                        "limit": results["limit"],
                        "offset": results["offset"],
                        "page": results["page"],
                        "total_pages": results["total_pages"],
                        "has_more": results["has_more"],
                        "count": len(results["prompts"]),
                    },
                }
            )

        except Exception as e:
            self.logger.error(f"Recent prompts error: {e}", exc_info=True)
            return web.json_response(
                {
                    "success": False,
                    "error": f"Failed to get recent prompts: {str(e)}",
                    "results": [],
                    "pagination": {"total": 0, "page": 1, "total_pages": 0},
                },
                status=500,
            )

    async def get_categories(self, request):
        """Retrieve all available prompt categories."""
        try:
            categories = await self._run_in_executor(self.db.get_all_categories)
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
        """Retrieve all available prompt tags."""
        try:
            tags = await self._run_in_executor(self.db.get_all_tags)
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

    async def get_tags_stats(self, request):
        """Get tags with usage counts, search, sort, and pagination."""
        try:
            try:
                limit = int(request.query.get("limit", 50))
                offset = int(request.query.get("offset", 0))
            except (ValueError, TypeError):
                return web.json_response(
                    {"success": False, "error": "Invalid limit or offset parameter"},
                    status=400,
                )
            search = request.query.get("search", "").strip() or None
            sort = request.query.get("sort", "alpha_asc")

            result = await self._run_in_executor(
                self.db.get_tags_with_counts, limit, offset, search, sort
            )
            untagged_count = await self._run_in_executor(
                self.db.get_untagged_prompts_count
            )

            return web.json_response(
                {
                    "success": True,
                    "tags": result["tags"],
                    "untagged_count": untagged_count,
                    "pagination": {
                        "total": result["total"],
                        "limit": result["limit"],
                        "offset": result["offset"],
                        "has_more": result["has_more"],
                    },
                }
            )
        except Exception as e:
            self.logger.error(f"Tags stats error: {e}", exc_info=True)
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def get_tag_prompts(self, request):
        """Get prompts for a single tag."""
        try:
            from urllib.parse import unquote

            tag_name = unquote(request.match_info.get("tag_name", ""))
            if not tag_name:
                return web.json_response(
                    {"success": False, "error": "Tag name required"}, status=400
                )

            try:
                limit = int(request.query.get("limit", 20))
                offset = int(request.query.get("offset", 0))
            except (ValueError, TypeError):
                return web.json_response(
                    {"success": False, "error": "Invalid limit or offset parameter"},
                    status=400,
                )

            result = await self._run_in_executor(
                self.db.get_prompts_by_tags, [tag_name], "and", limit, offset
            )
            self._enrich_prompt_images(result["prompts"])

            return web.json_response(
                {
                    "success": True,
                    "tag": tag_name,
                    "prompts": result["prompts"],
                    "pagination": {
                        "total": result["total"],
                        "limit": result["limit"],
                        "offset": result["offset"],
                        "has_more": result["has_more"],
                    },
                }
            )
        except Exception as e:
            self.logger.error(f"Tag prompts error: {e}", exc_info=True)
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def get_tags_filter(self, request):
        """Get prompts matching multiple tags with AND/OR mode, or untagged prompts."""
        try:
            untagged = request.query.get("untagged", "").lower() == "true"

            if untagged:
                try:
                    limit = int(request.query.get("limit", 20))
                    offset = int(request.query.get("offset", 0))
                except (ValueError, TypeError):
                    return web.json_response(
                        {
                            "success": False,
                            "error": "Invalid limit or offset parameter",
                        },
                        status=400,
                    )
                result = await self._run_in_executor(
                    self.db.get_untagged_prompts, limit, offset
                )
                self._enrich_prompt_images(result["prompts"])
                return web.json_response(
                    {
                        "success": True,
                        "tags": [],
                        "mode": "untagged",
                        "prompts": result["prompts"],
                        "pagination": {
                            "total": result["total"],
                            "limit": result["limit"],
                            "offset": result["offset"],
                            "has_more": result["has_more"],
                        },
                    }
                )

            tags_str = request.query.get("tags", "").strip()
            if not tags_str:
                return web.json_response(
                    {"success": False, "error": "Tags parameter required"}, status=400
                )

            tags_list = [t.strip() for t in tags_str.split(",") if t.strip()]
            mode = request.query.get("mode", "and").lower()
            if mode not in ("and", "or"):
                mode = "and"

            try:
                limit = int(request.query.get("limit", 20))
                offset = int(request.query.get("offset", 0))
            except (ValueError, TypeError):
                return web.json_response(
                    {"success": False, "error": "Invalid limit or offset parameter"},
                    status=400,
                )

            result = await self._run_in_executor(
                self.db.get_prompts_by_tags, tags_list, mode, limit, offset
            )
            self._enrich_prompt_images(result["prompts"])

            return web.json_response(
                {
                    "success": True,
                    "tags": tags_list,
                    "mode": mode,
                    "prompts": result["prompts"],
                    "pagination": {
                        "total": result["total"],
                        "limit": result["limit"],
                        "offset": result["offset"],
                        "has_more": result["has_more"],
                    },
                }
            )
        except Exception as e:
            self.logger.error(f"Tags filter error: {e}", exc_info=True)
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def rename_tag_endpoint(self, request):
        """Rename a tag across all prompts."""
        try:
            from urllib.parse import unquote

            tag_name = unquote(request.match_info.get("tag_name", ""))
            if not tag_name:
                return web.json_response(
                    {"success": False, "error": "Tag name required"}, status=400
                )

            try:
                body = await request.json()
            except Exception:
                return web.json_response(
                    {"success": False, "error": "Invalid JSON body"}, status=400
                )
            new_name = body.get("new_name", "").strip()
            if not new_name:
                return web.json_response(
                    {"success": False, "error": "New tag name required"}, status=400
                )

            result = await self._run_in_executor(
                self.db.rename_tag_all_prompts, tag_name, new_name
            )
            resp = {
                "success": True,
                "old_name": tag_name,
                "new_name": new_name,
                "affected_count": result["affected_count"],
            }
            if result.get("skipped_count", 0) > 0:
                resp["skipped_count"] = result["skipped_count"]
                resp["warning"] = (
                    f"{result['skipped_count']} prompt(s) had corrupted tag data and were skipped"
                )
            return web.json_response(resp)
        except Exception as e:
            self.logger.error(f"Rename tag error: {e}", exc_info=True)
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def delete_tag_endpoint(self, request):
        """Delete a tag from all prompts."""
        try:
            from urllib.parse import unquote

            tag_name = unquote(request.match_info.get("tag_name", ""))
            if not tag_name:
                return web.json_response(
                    {"success": False, "error": "Tag name required"}, status=400
                )

            result = await self._run_in_executor(
                self.db.delete_tag_all_prompts, tag_name
            )
            resp = {
                "success": True,
                "tag_name": tag_name,
                "affected_count": result["affected_count"],
            }
            if result.get("skipped_count", 0) > 0:
                resp["skipped_count"] = result["skipped_count"]
                resp["warning"] = (
                    f"{result['skipped_count']} prompt(s) had corrupted tag data and were skipped"
                )
            return web.json_response(resp)
        except Exception as e:
            self.logger.error(f"Delete tag error: {e}", exc_info=True)
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def merge_tags_endpoint(self, request):
        """Merge source tags into a target tag."""
        try:
            try:
                body = await request.json()
            except Exception:
                return web.json_response(
                    {"success": False, "error": "Invalid JSON body"}, status=400
                )
            source_tags = body.get("source_tags", [])
            target_tag = body.get("target_tag", "").strip()

            if not source_tags:
                return web.json_response(
                    {"success": False, "error": "Source tags required"}, status=400
                )
            if not target_tag:
                return web.json_response(
                    {"success": False, "error": "Target tag required"}, status=400
                )

            result = await self._run_in_executor(
                self.db.merge_tags, source_tags, target_tag
            )
            resp = {
                "success": True,
                "target_tag": target_tag,
                "affected_count": result["affected_count"],
                "tags_merged": result["tags_merged"],
            }
            if result.get("skipped_count", 0) > 0:
                resp["skipped_count"] = result["skipped_count"]
                resp["warning"] = (
                    f"{result['skipped_count']} prompt(s) had corrupted tag data and were skipped"
                )
            return web.json_response(resp)
        except Exception as e:
            self.logger.error(f"Merge tags error: {e}", exc_info=True)
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def save_prompt(self, request):
        """Save a new prompt with metadata and duplicate detection."""
        try:
            from utils.validators import (
                validate_prompt_text,
                validate_rating,
                validate_tags,
                validate_category,
                sanitize_input,
            )

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

            try:
                validate_prompt_text(text)
                validate_category(category)
                validate_tags(tags)
                validate_rating(rating)
            except ValueError as ve:
                return web.json_response(
                    {"success": False, "error": str(ve)}, status=400
                )

            text = sanitize_input(text)

            from utils.hashing import generate_prompt_hash

            prompt_hash = generate_prompt_hash(text)

            existing = await self._run_in_executor(
                self.db.get_prompt_by_hash, prompt_hash
            )
            if existing:
                if any([category, tags, rating, notes]):
                    await self._run_in_executor(
                        self.db.update_prompt_metadata,
                        prompt_id=existing["id"],
                        category=category,
                        tags=tags,
                        rating=rating,
                        notes=notes,
                    )
                return web.json_response(
                    {
                        "success": True,
                        "prompt_id": existing["id"],
                        "message": "Prompt already exists, metadata updated",
                        "is_duplicate": True,
                    }
                )

            prompt_id = await self._run_in_executor(
                self.db.save_prompt,
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
        """Delete a specific prompt by ID."""
        try:
            prompt_id = int(request.match_info["prompt_id"])
            success = await self._run_in_executor(self.db.delete_prompt, prompt_id)

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

    async def update_prompt(self, request):
        """Update prompt text."""
        try:
            from utils.validators import validate_prompt_text, sanitize_input

            prompt_id = int(request.match_info["prompt_id"])
            data = await request.json()
            new_text = data.get("text", "").strip()

            if not new_text:
                return web.json_response(
                    {"success": False, "error": "Text cannot be empty"}, status=400
                )

            try:
                validate_prompt_text(new_text)
            except ValueError as ve:
                return web.json_response(
                    {"success": False, "error": str(ve)}, status=400
                )

            new_text = sanitize_input(new_text)

            updated = await self._run_in_executor(
                self.db.update_prompt_text, prompt_id, new_text
            )
            if updated:
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
            from utils.validators import validate_rating

            prompt_id = int(request.match_info["prompt_id"])
            data = await request.json()
            rating = data.get("rating")

            try:
                validate_rating(rating)
            except ValueError as ve:
                return web.json_response(
                    {"success": False, "error": str(ve)}, status=400
                )

            updated = await self._run_in_executor(
                self.db.update_prompt_rating, prompt_id, rating
            )
            if updated:
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

            prompt = await self._run_in_executor(self.db.get_prompt_by_id, prompt_id)
            if not prompt:
                return web.json_response(
                    {"success": False, "error": "Prompt not found"}, status=404
                )

            current_tags = prompt.get("tags", [])
            if not isinstance(current_tags, list):
                current_tags = []

            if new_tag not in current_tags:
                current_tags.append(new_tag)
                await self._run_in_executor(
                    self.db.set_prompt_tags, prompt_id, current_tags
                )

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
                    {"success": False, "error": "Tags must be a non-empty list"},
                    status=400,
                )

            prompt = await self._run_in_executor(self.db.get_prompt_by_id, prompt_id)
            if not prompt:
                return web.json_response(
                    {"success": False, "error": "Prompt not found"}, status=404
                )

            current_tags = prompt.get("tags", [])
            if not isinstance(current_tags, list):
                current_tags = []

            tags_added = 0
            for new_tag in new_tags:
                new_tag = new_tag.strip()
                if new_tag and new_tag not in current_tags:
                    current_tags.append(new_tag)
                    tags_added += 1

            if tags_added > 0:
                await self._run_in_executor(
                    self.db.set_prompt_tags, prompt_id, current_tags
                )

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

            prompt = await self._run_in_executor(self.db.get_prompt_by_id, prompt_id)
            if not prompt:
                return web.json_response(
                    {"success": False, "error": "Prompt not found"}, status=404
                )

            current_tags = prompt.get("tags", [])
            if not isinstance(current_tags, list):
                current_tags = []

            if tag_to_remove in current_tags:
                current_tags.remove(tag_to_remove)
                await self._run_in_executor(
                    self.db.set_prompt_tags, prompt_id, current_tags
                )

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

            deleted_count = await self._run_in_executor(
                self.db.bulk_delete_prompts, prompt_ids
            )

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

            updated_count = await self._run_in_executor(
                self.db.bulk_add_tags, prompt_ids, new_tags
            )

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

            updated_count = await self._run_in_executor(
                self.db.bulk_set_category, prompt_ids, category
            )

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
            prompts = await self._run_in_executor(self.db.search_prompts, limit=10000)

            export_data = {
                "export_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "total_prompts": len(prompts),
                "prompts": prompts,
            }

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
