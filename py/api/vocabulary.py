"""Native vocabulary: bounded, signed-cursor reads and guarded tag commands."""

from aiohttp import web

from .library import (
    _command_body,
    _decode,
    _encode,
    _scope,
    library_command_response,
)
from .pairs import pair_response

_DEFAULT_LIMIT = 100
_CREATE_TAG_FIELDS = frozenset({"expected_epoch", "name"})
_RENAME_TAG_FIELDS = frozenset({"expected_epoch", "name", "new_name"})
_DELETE_TAG_FIELDS = frozenset({"expected_epoch", "name"})


def _tag_id(request: web.Request) -> int:
    try:
        value = int(request.match_info["tag_id"])
    except ValueError:
        raise ValueError("Invalid tag ID") from None
    if not 1 <= value <= 2**63 - 1:
        raise ValueError("Invalid tag ID")
    return value


def _vocabulary_limit(request: web.Request) -> int:
    value = int(request.query.get("limit", str(_DEFAULT_LIMIT)))
    if not 1 <= value <= 200:
        raise ValueError("limit must be between 1 and 200")
    return value


class VocabularyRoutesMixin:
    def _register_vocabulary_routes(self, routes: web.RouteTableDef) -> None:
        routes.get("/prompt_manager/vocabulary/tags")(self.list_vocabulary_tags)
        routes.post("/prompt_manager/vocabulary/tags")(self.create_vocabulary_tag)
        routes.patch("/prompt_manager/vocabulary/tags/{tag_id}")(
            self.rename_vocabulary_tag
        )
        routes.post("/prompt_manager/vocabulary/tags/{tag_id}/delete")(
            self.delete_vocabulary_tag
        )
        routes.get("/prompt_manager/vocabulary/tags/{tag_id}/impact")(
            self.get_vocabulary_tag_impact
        )
        routes.get("/prompt_manager/vocabulary/categories")(
            self.list_vocabulary_categories
        )

    async def _vocabulary_page(
        self, request: web.Request, kind: str, reader_name: str, name: bool
    ) -> web.Response:
        limit = _vocabulary_limit(request)
        cursor = request.query.get("cursor")

        def read() -> dict:
            # The snapshot stays open while the cursor is decoded and encoded,
            # so its epoch scope is the epoch the page was read from.
            with self.db.library_snapshot() as (conn, epoch, _ceiling):
                scope = _scope({"kind": kind, "epoch": epoch})
                result = getattr(self.db, reader_name)(
                    conn, epoch, limit=limit, after=_decode(cursor, scope)
                )
                result["next_cursor"] = _encode(
                    result.pop("next_key"), scope, name=name
                )
            return result

        result = await self._run_in_executor(read)
        return web.json_response({"success": True, **result})

    @pair_response
    async def list_vocabulary_tags(self, request: web.Request) -> web.Response:
        return await self._vocabulary_page(
            request, "vocabulary-tags", "_vocabulary_tags", False
        )

    @pair_response
    async def list_vocabulary_categories(self, request: web.Request) -> web.Response:
        return await self._vocabulary_page(
            request, "vocabulary-categories", "_vocabulary_categories", True
        )

    @library_command_response
    async def create_vocabulary_tag(self, request: web.Request) -> web.Response:
        body = await _command_body(request, _CREATE_TAG_FIELDS)
        result = await self._run_in_executor(
            self.db.create_vocabulary_tag,
            body["name"],
            expected_epoch=body["expected_epoch"],
        )
        return web.json_response({"success": True, **result})

    @library_command_response
    async def rename_vocabulary_tag(self, request: web.Request) -> web.Response:
        tag_id = _tag_id(request)
        body = await _command_body(request, _RENAME_TAG_FIELDS)
        result = await self._run_in_executor(
            self.db.rename_vocabulary_tag,
            tag_id,
            expected_epoch=body["expected_epoch"],
            name=body["name"],
            new_name=body["new_name"],
        )
        return web.json_response({"success": True, **result})

    @library_command_response
    async def delete_vocabulary_tag(self, request: web.Request) -> web.Response:
        tag_id = _tag_id(request)
        body = await _command_body(request, _DELETE_TAG_FIELDS)
        result = await self._run_in_executor(
            self.db.delete_vocabulary_tag,
            tag_id,
            expected_epoch=body["expected_epoch"],
            name=body["name"],
        )
        return web.json_response({"success": True, **result})

    @pair_response
    async def get_vocabulary_tag_impact(self, request: web.Request) -> web.Response:
        result = await self._run_in_executor(
            self.db.vocabulary_tag_impact, _tag_id(request)
        )
        if result is None:
            return web.json_response(
                {"success": False, "error": "Tag not found"}, status=404
            )
        return web.json_response({"success": True, **result})
