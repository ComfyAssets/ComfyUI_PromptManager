"""Reusable ordered prompt combinations, separate from legacy prompt CRUD."""

from functools import wraps
from typing import Callable

from aiohttp import web

from .prompts import parse_page_params

try:
    from ...database.pairs import PairDependencyError
    from ...utils.validators import (
        validate_category,
        validate_prompt_text,
        validate_rating,
        validate_tags,
    )
except ImportError:
    from database.pairs import PairDependencyError
    from utils.validators import (
        validate_category,
        validate_prompt_text,
        validate_rating,
        validate_tags,
    )


def pair_response(handler: Callable) -> Callable:
    @wraps(handler)
    async def wrapped(self, request: web.Request) -> web.Response:
        try:
            return await handler(self, request)
        except PairDependencyError as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=409)
        except (ValueError, TypeError) as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        except Exception:
            self.logger.exception("Prompt pair operation failed")
            return web.json_response(
                {"success": False, "error": "Prompt pair operation failed"}, status=500
            )

    return wrapped


def metadata_fields(data: dict) -> dict:
    fields = {}
    for name in ("title", "category", "tags", "rating", "notes"):
        if name not in data:
            continue
        value = data[name]
        if name == "tags":
            if not isinstance(value, list):
                raise ValueError("Tags must be a list of strings")
            validate_tags(value)
        elif name == "rating":
            if isinstance(value, bool):
                raise ValueError("Rating must be an integer")
            validate_rating(value)
        else:
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
            if name == "category":
                validate_category(value)
            elif value is not None and len(value) > 10000:
                raise ValueError(f"{name} is too long")
        fields[name] = value
    return fields


async def object_body(request: web.Request) -> dict:
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def missing_pair() -> web.Response:
    return web.json_response({"success": False, "error": "Pair not found"}, status=404)


class PairRoutesMixin:
    def _register_pair_routes(self, routes: web.RouteTableDef) -> None:
        routes.get("/prompt_manager/pairs/export")(self.export_prompt_pairs)
        routes.get("/prompt_manager/pairs")(self.list_prompt_pairs)
        routes.post("/prompt_manager/pairs")(self.save_prompt_pair)
        routes.get("/prompt_manager/pairs/{pair_id}")(self.get_prompt_pair)
        routes.patch("/prompt_manager/pairs/{pair_id}")(self.update_prompt_pair)
        routes.delete("/prompt_manager/pairs/{pair_id}")(self.delete_prompt_pair)

    @pair_response
    async def save_prompt_pair(self, request: web.Request) -> web.Response:
        data = await object_body(request)
        positive = data.get("positive_text")
        negative = data.get("negative_text")
        validate_prompt_text(positive)
        if not isinstance(negative, str):
            raise ValueError(
                "negative_text must be explicit; use an empty string for none"
            )
        if negative.strip():
            validate_prompt_text(negative)
        fields = metadata_fields(data)
        pair_id = await self._run_in_executor(
            self.db.save_prompt_pair,
            positive,
            negative,
            # The node's Save pair button: as a run, a new prompt gets the metadata.
            fill_new_positive=True,
            **fields,
        )
        pair = await self._run_in_executor(self.db.get_prompt_pair, pair_id)
        return web.json_response({"success": True, "pair": pair})

    @pair_response
    async def get_prompt_pair(self, request: web.Request) -> web.Response:
        pair = await self._run_in_executor(
            self.db.get_prompt_pair, int(request.match_info["pair_id"])
        )
        return (
            web.json_response({"success": True, "pair": pair})
            if pair
            else missing_pair()
        )

    @pair_response
    async def list_prompt_pairs(self, request: web.Request) -> web.Response:
        limit, offset = parse_page_params(request.query)
        archived = request.query.get("include_archived", "false")
        if archived not in ("true", "false"):
            raise ValueError("include_archived must be true or false")
        result = await self._run_in_executor(
            self.db.list_prompt_pairs,
            text=request.query.get("text"),
            category=request.query.get("category"),
            tags=request.query.getall("tag", []),
            include_archived=archived == "true",
            sort=request.query.get("sort", "newest"),
            limit=limit,
            offset=offset,
        )
        return web.json_response({"success": True, **result})

    @pair_response
    async def update_prompt_pair(self, request: web.Request) -> web.Response:
        pair_id = int(request.match_info["pair_id"])
        data = await object_body(request)
        allowed = {"title", "category", "tags", "rating", "notes", "archived"}
        if not data or set(data) - allowed:
            raise ValueError(
                "Update metadata or archive; save a new pair to change text"
            )
        fields = metadata_fields(data)
        if "archived" in data:
            if not isinstance(data["archived"], bool) or fields:
                raise ValueError("Archive must be a separate boolean update")
            updated = await self._run_in_executor(
                self.db.archive_prompt_pair, pair_id, data["archived"]
            )
        else:
            updated = await self._run_in_executor(
                self.db.update_prompt_pair_metadata, pair_id, **fields
            )
        pair = await self._run_in_executor(self.db.get_prompt_pair, pair_id)
        if not updated and not pair:
            return missing_pair()
        return web.json_response({"success": True, "pair": pair})

    @pair_response
    async def delete_prompt_pair(self, request: web.Request) -> web.Response:
        deleted = await self._run_in_executor(
            self.db.delete_prompt_pair, int(request.match_info["pair_id"])
        )
        return web.json_response({"success": True}) if deleted else missing_pair()

    @pair_response
    async def export_prompt_pairs(self, request: web.Request) -> web.Response:
        pairs = []
        offset = 0
        while True:
            result = await self._run_in_executor(
                self.db.list_prompt_pairs,
                include_archived=True,
                limit=500,
                offset=offset,
            )
            for pair in result["pairs"]:
                pairs.append(
                    {
                        key: pair[key]
                        for key in (
                            "positive_text",
                            "negative_text",
                            "title",
                            "category",
                            "tags",
                            "rating",
                            "notes",
                            "archived",
                        )
                    }
                )
            offset += len(result["pairs"])
            if not result["has_more"] or not result["pairs"]:
                break
        return web.json_response(
            {"schema_version": 1, "kind": "prompt-manager-pairs", "pairs": pairs},
            headers={"Content-Disposition": 'attachment; filename="prompt-pairs.json"'},
        )
