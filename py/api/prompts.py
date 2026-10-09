"""Prompt API routes for PromptManager."""

import datetime
import json
import os

from aiohttp import web
from urllib.parse import quote

# Upper bounds on page sizes and offsets for every list endpoint. The offset
# ceiling keeps values inside SQLite's 64-bit range and bounds skip work.
MAX_PAGE_LIMIT = 500
MAX_PAGE_OFFSET = 10_000_000

# The export walks the whole table in pages of this size instead of asking
# for one giant result set (and silently truncating large libraries).
EXPORT_PAGE_SIZE = 1000


def parse_page_params(query, default_limit=50, max_limit=MAX_PAGE_LIMIT):
    """Return ``(limit, offset)`` from a query mapping, clamped to safe bounds.

    ``limit`` is clamped to ``[1, max_limit]`` and ``offset`` to
    ``[0, MAX_PAGE_OFFSET]``. Non-integer values raise ``ValueError`` so the
    caller can answer 400.
    """
    limit = int(query.get("limit", default_limit))
    offset = int(query.get("offset", 0))
    return max(1, min(limit, max_limit)), max(0, min(offset, MAX_PAGE_OFFSET))


def bad_request(message):
    return web.json_response({"success": False, "error": message}, status=400)


def tags_error(tags):
    """A 400 response when *tags* fail ``validate_tags``, else None."""
    try:
        validate_tags(tags)
    except ValueError as exc:
        return bad_request(str(exc))
    return None


def safe_error_message(exc):
    """Describe *exc* for a client without leaking absolute server paths.

    OSError carries the offending path in ``filename``; only its basename
    is echoed back together with ``strerror``.
    """
    if isinstance(exc, OSError):
        reason = exc.strerror or exc.__class__.__name__
        if exc.filename:
            return f"{reason}: {os.path.basename(str(exc.filename))}"
        return reason
    return str(exc) or exc.__class__.__name__


def thumbnail_url_for(thumb_rel, thumb_abs):
    """Serve URL for an existing thumbnail, or None when the file is missing.

    The URL carries the file's mtime as ``?v=``: thumbnails are served with a
    long cache lifetime, so a regenerated file must get a new URL or browsers
    keep showing whatever they cached for the old one.
    """
    try:
        version = int(thumb_abs.stat().st_mtime)
    except OSError:
        return None
    return f"/prompt_manager/images/serve/{quote(thumb_rel, safe='/')}?v={version}"


def publish_image_paths(images, public_path):
    """Image dicts for a response: ``image_path`` in its public (ComfyUI
    relative) form and the absolute ``file_path`` duplicate removed.

    ``url``/``thumbnail_url`` added by enrichment are left untouched, so the
    frontend keeps working without ever seeing the server's directory layout.
    """
    published = []
    for image in images:
        if not isinstance(image, dict):
            published.append(image)
            continue
        image = {k: v for k, v in image.items() if k != "file_path"}
        if "image_path" in image:
            image["image_path"] = public_path(image["image_path"])
        published.append(image)
    return published


try:
    from ...utils.validators import (
        validate_prompt_text,
        validate_rating,
        validate_tags,
        validate_category,
        sanitize_input,
    )
    from ...utils.hashing import generate_prompt_hash
except ImportError:
    from utils.validators import (
        validate_prompt_text,
        validate_rating,
        validate_tags,
        validate_category,
        sanitize_input,
    )
    from utils.hashing import generate_prompt_hash


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

        @routes.get("/prompt_manager/subfolders")
        async def get_subfolders_route(request):
            return await self.get_subfolders(request)

        # Export functionality
        @routes.get("/prompt_manager/export")
        async def export_prompts_route(request):
            return await self.export_prompts(request)

    def _present_prompts(self, prompts):
        """Prompts ready for a response: image urls added, server paths hidden."""
        self._enrich_prompt_images(prompts)
        for prompt in prompts:
            prompt["images"] = publish_image_paths(
                prompt.get("images", []), self._public_path
            )
        return prompts

    async def search_prompts(self, request):
        """Search for prompts using multiple filter criteria."""
        try:
            text = request.query.get("text", "").strip()
            category = request.query.get("category", "").strip()
            tags_str = request.query.get("tags", "").strip()
            min_rating = request.query.get("min_rating", 0)
            try:
                limit, offset = parse_page_params(request.query)
            except ValueError:
                return bad_request("limit and offset must be integers")

            folder = request.query.get("folder", "").strip() or None
            # Validated against a whitelist in the database layer
            sort = request.query.get("sort") or None

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
                offset=offset,
                folder=folder,
                sort=sort,
            )
            self._present_prompts(results)

            return web.json_response(
                {
                    "success": True,
                    "results": results,
                    "count": len(results),
                    "pagination": {
                        "limit": limit,
                        "offset": offset,
                        "count": len(results),
                    },
                }
            )

        except Exception as e:
            self.logger.error(f"Search error: {e}", exc_info=True)
            return web.json_response(
                {
                    "success": False,
                    "error": f"Search failed: {safe_error_message(e)}",
                    "results": [],
                },
                status=500,
            )

    async def get_subfolders(self, request):
        """Get distinct subfolder values derived from generated image paths."""
        try:
            from ..config import GalleryConfig

            root_dirs = list(GalleryConfig.MONITORING_DIRECTORIES) or None
            include_ancestors = (
                request.query.get("include_ancestors", "").lower() == "true"
            )
            subfolders = await self._run_in_executor(
                self.db.get_prompt_subfolders, root_dirs, include_ancestors
            )
            return web.json_response({"success": True, "subfolders": subfolders})
        except Exception as e:
            self.logger.error(f"Subfolders error: {e}")
            return web.json_response(
                {"success": False, "error": safe_error_message(e)}, status=500
            )

    async def get_recent_prompts(self, request):
        """Retrieve prompts with pagination and an optional sort (default newest
        first)."""
        try:
            try:
                limit, offset = parse_page_params(request.query)
                page = int(request.query.get("page", 1))
            except ValueError:
                return bad_request("limit, offset and page must be integers")

            if page > 1 and offset == 0:
                offset = min((page - 1) * limit, MAX_PAGE_OFFSET)

            # Validated against a whitelist in the database layer
            sort = request.query.get("sort") or None
            results = await self._run_in_executor(
                self.db.get_recent_prompts, limit=limit, offset=offset, sort=sort
            )
            self._present_prompts(results["prompts"])

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
                    "error": f"Failed to get recent prompts: {safe_error_message(e)}",
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
                    "error": f"Failed to get categories: {safe_error_message(e)}",
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
                    "error": f"Failed to get tags: {safe_error_message(e)}",
                    "tags": [],
                },
                status=500,
            )

    async def get_tags_stats(self, request):
        """Get tags with usage counts, search, sort, and pagination."""
        try:
            try:
                limit, offset = parse_page_params(request.query)
            except ValueError:
                return bad_request("limit and offset must be integers")
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
            return web.json_response(
                {"success": False, "error": safe_error_message(e)}, status=500
            )

    async def get_tag_prompts(self, request):
        """Get prompts for a single tag."""
        try:
            # aiohttp has already percent-decoded match_info; decoding again
            # would turn a tag literally named "%41" into "A".
            tag_name = request.match_info.get("tag_name", "")
            if not tag_name:
                return web.json_response(
                    {"success": False, "error": "Tag name required"}, status=400
                )

            try:
                limit, offset = parse_page_params(request.query, default_limit=20)
            except ValueError:
                return bad_request("limit and offset must be integers")

            result = await self._run_in_executor(
                self.db.get_prompts_by_tags, [tag_name], "and", limit, offset
            )
            self._present_prompts(result["prompts"])

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
            return web.json_response(
                {"success": False, "error": safe_error_message(e)}, status=500
            )

    async def get_tags_filter(self, request):
        """Get prompts matching multiple tags with AND/OR mode, or untagged prompts."""
        try:
            untagged = request.query.get("untagged", "").lower() == "true"

            if untagged:
                try:
                    limit, offset = parse_page_params(request.query, default_limit=20)
                except ValueError:
                    return bad_request("limit and offset must be integers")
                result = await self._run_in_executor(
                    self.db.get_untagged_prompts, limit, offset
                )
                self._present_prompts(result["prompts"])
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
                limit, offset = parse_page_params(request.query, default_limit=20)
            except ValueError:
                return bad_request("limit and offset must be integers")

            result = await self._run_in_executor(
                self.db.get_prompts_by_tags, tags_list, mode, limit, offset
            )
            self._present_prompts(result["prompts"])

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
            return web.json_response(
                {"success": False, "error": safe_error_message(e)}, status=500
            )

    async def rename_tag_endpoint(self, request):
        """Rename a tag across all prompts."""
        try:
            # aiohttp has already percent-decoded match_info; decoding again
            # would turn a tag literally named "%41" into "A".
            tag_name = request.match_info.get("tag_name", "")
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
            raw_name = body.get("new_name")
            if not raw_name:
                return web.json_response(
                    {"success": False, "error": "New tag name required"}, status=400
                )
            error = tags_error([raw_name])
            if error is not None:
                return error
            new_name = raw_name.strip()

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
                    f"{result['skipped_count']} prompt(s) had corrupted tag data "
                    "and were skipped"
                )
            return web.json_response(resp)
        except Exception as e:
            self.logger.error(f"Rename tag error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": safe_error_message(e)}, status=500
            )

    async def delete_tag_endpoint(self, request):
        """Delete a tag from all prompts."""
        try:
            # aiohttp has already percent-decoded match_info; decoding again
            # would turn a tag literally named "%41" into "A".
            tag_name = request.match_info.get("tag_name", "")
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
                    f"{result['skipped_count']} prompt(s) had corrupted tag data "
                    "and were skipped"
                )
            return web.json_response(resp)
        except Exception as e:
            self.logger.error(f"Delete tag error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": safe_error_message(e)}, status=500
            )

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
            raw_target = body.get("target_tag")

            if not source_tags:
                return web.json_response(
                    {"success": False, "error": "Source tags required"}, status=400
                )
            if not raw_target:
                return web.json_response(
                    {"success": False, "error": "Target tag required"}, status=400
                )
            if not isinstance(source_tags, list):
                return bad_request("Source tags must be a list")
            error = tags_error([*source_tags, raw_target])
            if error is not None:
                return error
            target_tag = raw_target.strip()

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
                    f"{result['skipped_count']} prompt(s) had corrupted tag data "
                    "and were skipped"
                )
            return web.json_response(resp)
        except Exception as e:
            self.logger.error(f"Merge tags error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": safe_error_message(e)}, status=500
            )

    async def save_prompt(self, request):
        """Save a new prompt with metadata and duplicate detection."""
        try:
            data = await request.json()

            text = (data.get("text") or "").strip()
            if not text:
                return web.json_response(
                    {"success": False, "error": "Text is required"}, status=400
                )

            category = (data.get("category") or "").strip() or None
            tags = data.get("tags", [])
            rating = data.get("rating") or None
            notes = (data.get("notes") or "").strip() or None

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
                {
                    "success": False,
                    "error": f"Failed to save prompt: {safe_error_message(e)}",
                },
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
                {
                    "success": False,
                    "error": f"Failed to delete prompt: {safe_error_message(e)}",
                },
                status=500,
            )

    async def update_prompt(self, request):
        """Update prompt text."""
        try:
            prompt_id = int(request.match_info["prompt_id"])
            data = await request.json()
            new_text = (data.get("text") or "").strip()

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
                {
                    "success": False,
                    "error": f"Failed to update prompt: {safe_error_message(e)}",
                },
                status=500,
            )

    async def update_prompt_rating(self, request):
        """Update prompt rating."""
        try:
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
                {
                    "success": False,
                    "error": f"Failed to update rating: {safe_error_message(e)}",
                },
                status=500,
            )

    async def add_prompt_tag(self, request):
        """Add tag to prompt."""
        try:
            prompt_id = int(request.match_info["prompt_id"])
            data = await request.json()
            raw_tag = data.get("tag")

            if not raw_tag:
                return web.json_response(
                    {"success": False, "error": "Tag cannot be empty"}, status=400
                )
            error = tags_error([raw_tag])
            if error is not None:
                return error
            new_tag = raw_tag.strip()

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
                {
                    "success": False,
                    "error": f"Failed to add tag: {safe_error_message(e)}",
                },
                status=500,
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
            # Blank entries are skipped below, so only the rest is validated.
            error = tags_error(
                [t for t in new_tags if not (isinstance(t, str) and not t.strip())]
            )
            if error is not None:
                return error

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
                {
                    "success": False,
                    "error": f"Failed to add tags: {safe_error_message(e)}",
                },
                status=500,
            )

    async def remove_prompt_tag(self, request):
        """Remove tag from prompt."""
        try:
            prompt_id = int(request.match_info["prompt_id"])
            data = await request.json()
            tag_to_remove = (data.get("tag") or "").strip()

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
                {
                    "success": False,
                    "error": f"Failed to remove tag: {safe_error_message(e)}",
                },
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
                {
                    "success": False,
                    "error": f"Failed to delete prompts: {safe_error_message(e)}",
                },
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
            if not isinstance(new_tags, list):
                return bad_request("Tags must be a list")
            error = tags_error(new_tags)
            if error is not None:
                return error

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
                {
                    "success": False,
                    "error": f"Failed to add tags: {safe_error_message(e)}",
                },
                status=500,
            )

    async def bulk_set_category(self, request):
        """Bulk set category for prompts."""
        try:
            data = await request.json()
            prompt_ids = data.get("prompt_ids", [])
            category = (data.get("category") or "").strip()

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
                {
                    "success": False,
                    "error": f"Failed to set category: {safe_error_message(e)}",
                },
                status=500,
            )

    def _all_prompts_for_export(self):
        """Every prompt, fetched in EXPORT_PAGE_SIZE pages (blocking)."""
        prompts, offset = [], 0
        while True:
            page = self.db.search_prompts(limit=EXPORT_PAGE_SIZE, offset=offset)
            prompts.extend(page)
            if len(page) < EXPORT_PAGE_SIZE:
                return prompts
            offset += EXPORT_PAGE_SIZE

    async def export_prompts(self, request):
        """Export all prompts to JSON."""
        try:
            prompts = await self._run_in_executor(self._all_prompts_for_export)

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
                    "Content-Disposition": (
                        'attachment; filename="prompt_manager_'
                        f'{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.json"'
                    )
                },
            )

        except Exception as e:
            self.logger.error(f"Export error: {e}")
            return web.json_response(
                {
                    "success": False,
                    "error": f"Failed to export prompts: {safe_error_message(e)}",
                },
                status=500,
            )
