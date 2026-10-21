"""Saved views (D16): list, create, rename and delete named toolbar states.

The page, the name and the whitelisted per-page state are validated here
(through ``database/saved_views.py``) before anything is written.
"""

from functools import wraps
from typing import Callable

from aiohttp import web

try:
    from ...database.saved_views import encode_state, validate_name
except ImportError:
    from database.saved_views import encode_state, validate_name

_CREATE_FIELDS = frozenset({"page", "name", "state"})
_RENAME_FIELDS = frozenset({"name"})
_MAX_ID = 2**63 - 1


class SavedViewNotFound(LookupError):
    pass


def saved_view_response(handler: Callable) -> Callable:
    """``{success:false,error}`` with 400, 404 or 500 on every failure."""

    @wraps(handler)
    async def wrapped(self, request: web.Request) -> web.Response:
        try:
            return await handler(self, request)
        except SavedViewNotFound:
            return web.json_response(
                {"success": False, "error": "Saved view not found"}, status=404
            )
        except (ValueError, TypeError) as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        except Exception:
            self.logger.exception("Saved view operation failed")
            return web.json_response(
                {"success": False, "error": "Saved view operation failed"}, status=500
            )

    return wrapped


async def _body(request: web.Request, fields: frozenset) -> dict:
    """A JSON object whose keys are exactly ``fields``."""
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("Expected a JSON object")
    if set(body) != fields:
        raise ValueError("Request must contain exactly " + ", ".join(sorted(fields)))
    return body


def _view_id(request: web.Request) -> int:
    raw = request.match_info["view_id"]
    if not raw.isascii() or not raw.isdigit():
        raise ValueError("Invalid saved view ID")
    value = int(raw)
    if not 1 <= value <= _MAX_ID:
        raise ValueError("Invalid saved view ID")
    return value


class SavedViewRoutesMixin:
    def _register_saved_view_routes(self, routes: web.RouteTableDef) -> None:
        routes.get("/prompt_manager/saved-views")(self.list_saved_views)
        routes.post("/prompt_manager/saved-views")(self.create_saved_view)
        routes.patch("/prompt_manager/saved-views/{view_id}")(self.rename_saved_view)
        routes.delete("/prompt_manager/saved-views/{view_id}")(self.delete_saved_view)

    @saved_view_response
    async def list_saved_views(self, request: web.Request) -> web.Response:
        views = await self._run_in_executor(self.db.list_saved_views)
        return web.json_response({"success": True, "views": views})

    @saved_view_response
    async def create_saved_view(self, request: web.Request) -> web.Response:
        body = await _body(request, _CREATE_FIELDS)
        encode_state(body["page"], body["state"])
        validate_name(body["name"])
        view = await self._run_in_executor(
            self.db.create_saved_view, body["page"], body["name"], body["state"]
        )
        return web.json_response({"success": True, "view": view}, status=201)

    @saved_view_response
    async def rename_saved_view(self, request: web.Request) -> web.Response:
        view_id = _view_id(request)
        body = await _body(request, _RENAME_FIELDS)
        validate_name(body["name"])
        view = await self._run_in_executor(
            self.db.rename_saved_view, view_id, body["name"]
        )
        if view is None:
            raise SavedViewNotFound
        return web.json_response({"success": True, "view": view})

    @saved_view_response
    async def delete_saved_view(self, request: web.Request) -> web.Response:
        view_id = _view_id(request)
        if not await self._run_in_executor(self.db.delete_saved_view, view_id):
            raise SavedViewNotFound
        return web.json_response({"success": True, "id": view_id})
