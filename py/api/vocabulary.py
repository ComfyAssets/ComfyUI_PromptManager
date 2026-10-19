"""Native vocabulary reads: bounded pages with signed, epoch-scoped cursors."""

from aiohttp import web

from .library import _decode, _encode, _scope
from .pairs import pair_response

_DEFAULT_LIMIT = 100


def _vocabulary_limit(request: web.Request) -> int:
    value = int(request.query.get("limit", str(_DEFAULT_LIMIT)))
    if not 1 <= value <= 200:
        raise ValueError("limit must be between 1 and 200")
    return value


class VocabularyRoutesMixin:
    def _register_vocabulary_routes(self, routes: web.RouteTableDef) -> None:
        routes.get("/prompt_manager/vocabulary/tags")(self.list_vocabulary_tags)
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
