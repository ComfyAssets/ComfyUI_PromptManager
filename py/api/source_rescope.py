"""Where a stale Images target is now (#430): ``POST /prompt_manager/images/rescope``.

A page addresses a listed file as ``{root_index, root_scope, path}``. When the
sources are renumbered its scope no longer names the root at that index, and
every per-file action answers 409 ``source-changed``. Before it gives up, the
page asks here. The target is mapped only when its scope is, at its old
index, the scope of one of the current roots: the server issued that scope
for exactly that folder, so the answer (the file's current listing
descriptor) names nothing the page could not already address. A removed or
replaced root, a scope of another key and a foreign scope stay refused.

Only the Images roots (``_get_all_output_dirs``) are mapped; a LoRA-linked
root's target is never re-scoped here.
"""

import hmac
from pathlib import Path

from aiohttp import web

from .images import (
    _forbidden,
    _is_thumbnail_cache_path,
    _json_body_or_error,
    _media_access_error,
)
from .media_target import media_target
from .prompts import bad_request

# Far above any real source count; keeps the index out of huge-int territory.
MAX_ROOT_INDEX = 4095
SOURCE_GONE = (
    "The source of this image changed or was removed. Refresh the image "
    "collection to find it again."
)


def _coded(status: int, code: str, message: str) -> web.Response:
    return web.json_response(
        {"success": False, "code": code, "error": message}, status=status
    )


def _no_store(response: web.Response) -> web.Response:
    response.headers["Cache-Control"] = "no-store"
    return response


class SourceRescopeMixin:
    """The rescope route; composed into ``PromptManagerAPI``."""

    def _register_rescope_routes(self, routes):
        @routes.post("/prompt_manager/images/rescope")
        async def rescope_route(request):
            return _no_store(await self.rescope_image(request))

    def _current_root_for(self, scope: str, old_index: int, roots: list) -> int | None:
        """The index of the first current root *scope* was issued for at *old_index*."""
        for index, root in enumerate(roots):
            if hmac.compare_digest(scope, self._gallery_root_scope(root, old_index)):
                return index
        return None

    def _rescope_blocking(self, scope: str, old_index: int, relative: str):
        """The answer to one rescope (blocking: it resolves paths)."""
        roots = list(self._get_all_output_dirs())
        index = self._current_root_for(scope, old_index, roots)
        if index is None:
            return _coded(409, "source-changed", SOURCE_GONE)
        root = Path(roots[index])
        path = root / relative
        denied = _media_access_error(path, [root])
        if denied is not None:
            return denied
        try:
            mtime = path.stat().st_mtime if path.is_file() else None
        except OSError:
            mtime = None
        if mtime is None:
            return _coded(404, "file-missing", "Image file not found")
        [image] = self._format_workspace_page([(path, mtime, root, index)])
        return web.json_response({"success": True, "image": image})

    async def rescope_image(self, request) -> web.Response:
        data, error = await _json_body_or_error(request)
        if error is not None:
            return error
        if set(data) != {"target"}:
            return bad_request("Send exactly one target")
        target, error = media_target(data["target"])
        if error is not None:
            return error
        old_index, scope, relative = target
        if old_index > MAX_ROOT_INDEX:
            return bad_request("Invalid gallery root")
        if _is_thumbnail_cache_path(relative):
            return _forbidden("Previews are not re-scoped; use the original")
        return await self._run_in_executor(
            self._rescope_blocking, scope, old_index, relative
        )
