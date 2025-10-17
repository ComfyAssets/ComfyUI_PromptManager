"""Prompt management API handlers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from ..routes import PromptManagerAPI


class PromptHandlers:
    """Handles all prompt-related endpoints."""

    def __init__(self, api: PromptManagerAPI):
        """Initialize with API instance for access to repos/services.

        Args:
            api: PromptManagerAPI instance providing access to repositories and services
        """
        self.api = api
        self.prompt_repo = api.prompt_repo
        self.generated_image_repo = getattr(api, 'generated_image_repo', None)
        self.logger = api.logger
        self.realtime = api.realtime

    async def create_prompt(self, request: web.Request) -> web.Response:
        """Create new prompt.

        POST /api/v1/prompts
        Body: {text: str, category?: str, tags?: list, rating?: int, notes?: str}
        """
        try:
            incoming = await request.json()
            self.logger.info(f"[CREATE] Incoming payload: {incoming}")

            data = self.api._normalize_prompt_payload(incoming)
            self.logger.info(f"[CREATE] Normalized data: {data}")

            prompt_text = data.get("positive_prompt") or data.get("prompt")
            if not prompt_text:
                self.logger.warning(f"[CREATE] No prompt text found in data: {data}")
                return web.json_response(
                    {"error": "Prompt text is required"},
                    status=400
                )

            self.logger.info(f"[CREATE] Creating prompt with text: {prompt_text[:50]}...")
            prompt_id = self.prompt_repo.create(data)
            self.logger.info(f"[CREATE] Created prompt with ID: {prompt_id}")

            prompt = self.api._format_prompt(self.prompt_repo.read(prompt_id))
            self.logger.info(f"[CREATE] Read back prompt: {prompt}")

            # Broadcast realtime update
            await self.realtime.notify_prompt_created(prompt)
            await self.realtime.send_toast(f"Prompt '{prompt.get('title', 'Untitled')}' created", 'success')

            return web.json_response({
                "success": True,
                "data": prompt
            }, status=201)

        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error creating prompt: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def list_prompts(self, request: web.Request) -> web.Response:
        """List prompts with pagination and filtering.

        GET /api/v1/prompts?page=1&limit=50&category=x&order_by=created_at
        """
        try:
            # Parse query parameters
            page = int(request.query.get("page", 1))
            limit = int(request.query.get("limit", 50))
            offset = (page - 1) * limit

            order_field = request.query.get("order_by", "id")
            order_dir = request.query.get("order_dir", "desc").upper()
            order_clause = f"{order_field} {'DESC' if order_dir == 'DESC' else 'ASC'}"

            filter_kwargs = {}
            if category := request.query.get("category"):
                filter_kwargs["category"] = category
            if rating := request.query.get("rating"):
                try:
                    filter_kwargs["rating"] = int(rating)
                except (TypeError, ValueError):
                    pass
            # Parse tags parameter (comma-separated string to list for AND filtering)
            if tags_param := request.query.get("tags"):
                tags_list = [tag.strip() for tag in tags_param.split(',') if tag.strip()]
                if tags_list:
                    filter_kwargs["tags"] = tags_list
            search_term = request.query.get("search")

            include_images_param = request.query.get("include_images") or request.query.get("include")
            include_images = False
            if include_images_param:
                include_images = any(
                    token.strip().lower() in {"1", "true", "yes", "images", "image"}
                    for token in include_images_param.split(",")
                )

            try:
                image_limit = int(request.query.get("image_limit", 4))
            except (TypeError, ValueError):
                image_limit = 4

            # Use search method if search term provided
            if search_term:
                try:
                    self.logger.info(f"[LIST] Searching for: {search_term}")
                    search_columns = ["positive_prompt", "negative_prompt", "tags", "category", "notes"]

                    raw_records = self.prompt_repo.search(
                        search_term,
                        columns=search_columns,
                        limit=limit,
                        offset=offset
                    )

                    search_total = self.prompt_repo.search_count(
                        search_term,
                        columns=search_columns
                    )
                    self.logger.info(f"[LIST] Search found {len(raw_records)} prompts (total: {search_total})")
                except Exception as e:
                    self.logger.error(f"Search error: {e}")
                    raw_records = []
                    search_total = 0
            else:
                raw_records = list(self.prompt_repo.list(
                    limit=limit,
                    offset=offset,
                    order_by=order_clause,
                    **filter_kwargs,
                ))
                self.logger.info(f"[LIST] Found {len(raw_records)} prompts from database")

            prompts = [
                self.api._format_prompt(
                    record,
                    include_images=include_images,
                    image_limit=image_limit,
                )
                for record in raw_records
            ]

            total = search_total if search_term else self.prompt_repo.count(**filter_kwargs)
            self.logger.info(f"[LIST] Total count: {total}")

            return web.json_response({
                "success": True,
                "data": prompts,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "total_pages": (total + limit - 1) // limit
                }
            })

        except ValueError as e:
            return web.json_response(
                {"error": f"Invalid parameter: {e}"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error listing prompts: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def get_prompt(self, request: web.Request) -> web.Response:
        """Get prompt by ID.

        GET /api/v1/prompts/{id}
        """
        try:
            prompt_id = request.match_info["id"]

            include_images_param = request.query.get("include_images") or request.query.get("include")
            include_images = False
            if include_images_param:
                include_images = any(
                    token.strip().lower() in {"1", "true", "yes", "images", "image"}
                    for token in include_images_param.split(",")
                )
            try:
                image_limit = int(request.query.get("image_limit", 8))
            except (TypeError, ValueError):
                image_limit = 8

            prompt = self.api._format_prompt(
                self.prompt_repo.read(prompt_id),
                include_images=include_images,
                image_limit=image_limit,
            )

            if not prompt:
                return web.json_response(
                    {"error": "Prompt not found"},
                    status=404
                )

            return web.json_response({
                "success": True,
                "data": prompt
            })

        except Exception as e:
            self.logger.error(f"Error getting prompt: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def update_prompt(self, request: web.Request) -> web.Response:
        """Update prompt by ID.

        PUT /api/v1/prompts/{id}
        Body: {text?: str, category?: str, tags?: list, rating?: int, notes?: str}
        """
        try:
            prompt_id = request.match_info["id"]
            raw_payload = await request.json()
            data = self.api._normalize_prompt_payload(raw_payload)

            if not self.prompt_repo.read(prompt_id):
                return web.json_response(
                    {"error": "Prompt not found"},
                    status=404
                )

            data.pop("positive_prompt", None)
            success = self.prompt_repo.update(prompt_id, data)

            if success:
                prompt = self.api._format_prompt(self.prompt_repo.read(prompt_id))

                # Broadcast realtime update
                await self.realtime.notify_prompt_updated(prompt)
                await self.realtime.send_toast(f"Prompt '{prompt.get('title', 'Untitled')}' updated", 'info')

                return web.json_response({
                    "success": True,
                    "data": prompt
                })
            else:
                return web.json_response(
                    {"error": "No fields to update"},
                    status=400
                )

        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error updating prompt: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def delete_prompt(self, request: web.Request) -> web.Response:
        """Delete prompt by ID.

        DELETE /api/v1/prompts/{id}
        """
        try:
            prompt_id = request.match_info["id"]

            if not self.prompt_repo.read(prompt_id):
                return web.json_response(
                    {"error": "Prompt not found"},
                    status=404
                )

            success = self.prompt_repo.delete(prompt_id)

            if success:
                # Broadcast realtime update
                await self.realtime.notify_prompt_deleted(int(prompt_id))
                await self.realtime.send_toast("Prompt deleted successfully", 'info')

            return web.json_response({
                "success": success,
                "message": "Prompt deleted successfully" if success else "Failed to delete prompt"
            })

        except Exception as e:
            self.logger.error(f"Error deleting prompt: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def search_prompts(self, request: web.Request) -> web.Response:
        """Search prompts with advanced filters.

        POST /api/v1/prompts/search
        Body: {search?: str, category?: str, tags?: list, rating?: int}
        """
        try:
            data = await request.json()

            search_term = data.get("search")
            if not search_term:
                return web.json_response({"error": "Search term is required"}, status=400)

            prompts = [
                self.api._format_prompt(record)
                for record in self.prompt_repo.search(search_term, ["prompt", "negative_prompt", "category"])
            ]

            if category := data.get("category"):
                prompts = [p for p in prompts if p.get("category") == category]

            if tags := data.get("tags"):
                tag_set = set(tags if isinstance(tags, list) else [tags])
                prompts = [
                    p
                    for p in prompts
                    if isinstance(p.get("tags"), list) and tag_set.intersection(p.get("tags", []))
                ]

            if rating := data.get("rating"):
                try:
                    rating_value = int(rating)
                except (TypeError, ValueError):
                    rating_value = None
                if rating_value is not None:
                    prompts = [p for p in prompts if p.get("rating") == rating_value]

            limit = max(1, min(int(data.get("limit", 100)), 500))
            offset = max(0, int(data.get("offset", 0)))
            paginated = prompts[offset : offset + limit]

            return web.json_response({
                "success": True,
                "data": paginated,
                "count": len(prompts)
            })

        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error searching prompts: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def bulk_create_prompts(self, request: web.Request) -> web.Response:
        """Bulk create prompts.

        POST /api/v1/prompts/bulk
        Body: {prompts: [{text: str, ...}, ...]}
        """
        try:
            data = await request.json()
            prompts_data = data.get("prompts", [])

            if not prompts_data:
                return web.json_response(
                    {"error": "No prompts provided"},
                    status=400
                )

            created = []
            errors = []

            for i, prompt_data in enumerate(prompts_data):
                try:
                    normalized = self.api._normalize_prompt_payload(prompt_data)
                    prompt_text = normalized.get("positive_prompt") or normalized.get("prompt")
                    if not prompt_text:
                        errors.append({
                            "index": i,
                            "error": "Text is required"
                        })
                        continue

                    prompt_id = self.prompt_repo.create(normalized)
                    created.append(prompt_id)

                except Exception as e:
                    errors.append({
                        "index": i,
                        "error": str(e)
                    })

            return web.json_response({
                "success": len(errors) == 0,
                "created": created,
                "errors": errors,
                "summary": {
                    "total": len(prompts_data),
                    "created": len(created),
                    "failed": len(errors)
                }
            })

        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error in bulk create: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def bulk_delete_prompts(self, request: web.Request) -> web.Response:
        """Bulk delete prompts.

        DELETE /api/v1/prompts/bulk
        Body: {ids: [1, 2, 3, ...]}
        """
        try:
            data = await request.json()
            ids = data.get("ids", [])

            if not ids:
                return web.json_response(
                    {"error": "No IDs provided"},
                    status=400
                )

            deleted = []
            errors = []

            for prompt_id in ids:
                try:
                    if self.prompt_repo.delete(prompt_id):
                        deleted.append(prompt_id)
                    else:
                        errors.append({
                            "id": prompt_id,
                            "error": "Not found"
                        })

                except Exception as e:
                    errors.append({
                        "id": prompt_id,
                        "error": str(e)
                    })

            return web.json_response({
                "success": len(errors) == 0,
                "deleted": deleted,
                "errors": errors,
                "summary": {
                    "total": len(ids),
                    "deleted": len(deleted),
                    "failed": len(errors)
                }
            })

        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error in bulk delete: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def get_recent_prompts(self, request: web.Request) -> web.Response:
        """Get recently created prompts.

        GET /api/v1/prompts/recent?limit=10
        """
        try:
            limit = int(request.query.get("limit", 10))
        except (TypeError, ValueError):
            limit = 10

        prompts = self.prompt_repo.get_recent(limit)

        include_images_param = request.query.get("include_images") or request.query.get("include")
        include_images = True
        if include_images_param:
            include_images = any(
                token.strip().lower() in {"1", "true", "yes", "images", "image"}
                for token in include_images_param.split(",")
            )
        try:
            image_limit = int(request.query.get("image_limit", 4))
        except (TypeError, ValueError):
            image_limit = 4

        prompts = [
            self.api._format_prompt(p, include_images=include_images, image_limit=image_limit)
            for p in prompts
        ]
        return web.json_response({"success": True, "data": prompts})

    async def get_popular_prompts(self, request: web.Request) -> web.Response:
        """Get most popular prompts.

        GET /api/v1/prompts/popular?limit=10
        """
        try:
            limit = int(request.query.get("limit", 10))
        except (TypeError, ValueError):
            limit = 10

        prompts = self.prompt_repo.get_popular(limit)

        include_images_param = request.query.get("include_images") or request.query.get("include")
        include_images = True
        if include_images_param:
            include_images = any(
                token.strip().lower() in {"1", "true", "yes", "images", "image"}
                for token in include_images_param.split(",")
            )
        try:
            image_limit = int(request.query.get("image_limit", 4))
        except (TypeError, ValueError):
            image_limit = 4

        prompts = [
            self.api._format_prompt(p, include_images=include_images, image_limit=image_limit)
            for p in prompts
        ]
        return web.json_response({"success": True, "data": prompts})

    async def get_prompt_categories(self, request: web.Request) -> web.Response:
        """Get all prompt categories.

        GET /api/v1/prompts/categories
        """
        return await self.api.get_categories(request)

    async def get_prompt_images(self, request: web.Request) -> web.Response:
        """Get images for a specific prompt.

        GET /api/v1/prompts/{prompt_id}/images?limit=12&order=desc
        """
        if not self.generated_image_repo:
            return web.json_response({"success": True, "data": [], "count": 0})

        try:
            prompt_id = int(request.match_info["prompt_id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "Invalid prompt id"}, status=400)

        try:
            limit = int(request.query.get("limit", 12))
        except (TypeError, ValueError):
            return web.json_response({"error": "Invalid limit"}, status=400)

        order_param = (request.query.get("order") or "desc").lower()
        order_by = "id ASC" if order_param in {"asc", "oldest"} else "id DESC"

        try:
            images = self.generated_image_repo.list_for_prompt(
                prompt_id,
                limit=limit,
                order_by=order_by,
            )
            total = self.generated_image_repo.count(prompt_id=prompt_id)
        except Exception as exc:
            self.logger.error("Failed to fetch generated images for prompt %s: %s", prompt_id, exc)
            return web.json_response({"error": str(exc)}, status=500)

        payload = [self.api._format_generated_image(img) for img in images if img]
        return web.json_response({"success": True, "data": payload, "count": total})
