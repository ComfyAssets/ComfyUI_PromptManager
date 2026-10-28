"""Source changes under an open workspace page (#430), undone by every reset.

- ``POST /__test__/sources/reverse``: the image sources are listed in reverse
  order, as System › Sources would renumber them, so every held
  ``root_scope`` names another index and the page must re-scope its file.
- ``POST /__test__/scope-key/rotate``: the API's scope and cursor keys
  change, as when ``workspace-scope.key`` is lost before a restart: a held
  scope then cannot be mapped and the page offers Refresh.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Callable

from aiohttp import web

from utils.workspace_keys import WorkspaceKeys

if TYPE_CHECKING:
    from py.api import PromptManagerAPI

_CACHE = "_workspace_keys_cache"


def register_source_change_commands(
    routes: web.RouteTableDef, api: PromptManagerAPI
) -> Callable[[], None]:
    """Register the commands; returns the restore every reset runs."""
    # Imported here: py.config needs the host stubs create_app installs first.
    from py.config import GalleryConfig

    saved: dict = {}

    def restore() -> None:
        if "roots" in saved:
            GalleryConfig.MONITORING_DIRECTORIES = saved.pop("roots")
            api.invalidate_gallery_cache(retire=True)
        if "keys" in saved:
            keys = saved.pop("keys")
            if keys is None:
                api.__dict__.pop(_CACHE, None)
            else:
                api.__dict__[_CACHE] = keys

    @routes.post("/__test__/sources/reverse")
    async def reverse(request: web.Request) -> web.Response:
        if request.query or request.can_read_body:
            raise web.HTTPBadRequest()
        roots = list(GalleryConfig.MONITORING_DIRECTORIES)
        saved.setdefault("roots", roots)
        GalleryConfig.MONITORING_DIRECTORIES = roots[::-1]
        await api._run_in_executor(api.invalidate_gallery_cache, True)
        return web.json_response({"success": True})

    @routes.post("/__test__/scope-key/rotate")
    async def rotate(request: web.Request) -> web.Response:
        if request.query or request.can_read_body:
            raise web.HTTPBadRequest()
        saved.setdefault("keys", api.__dict__.get(_CACHE))
        api.__dict__[_CACHE] = WorkspaceKeys(
            secrets.token_bytes(32), secrets.token_bytes(32), False
        )
        return web.json_response({"success": True})

    return restore
