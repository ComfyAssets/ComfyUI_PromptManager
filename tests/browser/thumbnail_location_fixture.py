"""The thumbnail location on the test server (#320), undone by every reset.

Folder mode is reachable from config.json and tests only until the Move
thumbnails job lands, so page tests set it here, inside the server's root:

- ``POST /__test__/thumbnail-location`` ``{"mode": "folder"}`` (or
  ``"source"``): previews live in ``<root>/thumbs/<key>`` from now on (a new
  folder with Prompt Manager's marker).
- ``GET /__test__/thumbnail-files``: the preview files in the thumbnail
  folder and whether any source grew a ``thumbnails/`` folder.
- ``POST /__test__/thumbnail-folder/remove``: the thumbnail folder goes, as
  when its drive is unplugged; the server notices it on its next check.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from aiohttp import web

if TYPE_CHECKING:
    from py.api import PromptManagerAPI

FOLDER_NAME = "thumbs"


def register_thumbnail_location_commands(
    routes: web.RouteTableDef, api: PromptManagerAPI, root: Path
) -> Callable[[], None]:
    """Register the commands; returns the reset every reset runs."""
    from py.api import preview_location, preview_store

    folder = root / FOLDER_NAME

    def reset() -> None:
        preview_store.configure(None)
        shutil.rmtree(folder, ignore_errors=True)

    def use(mode: str) -> dict:
        reset()
        if mode == "source":
            return {"mode": "source"}
        folder.mkdir()
        marker_id = preview_location.init_folder(str(folder), created_by_pm=True)
        location = preview_store.configure(
            {
                "mode": "folder",
                "path": str(folder),
                "id": marker_id,
                "install": "browser-tests",
            }
        )
        return {"mode": location.mode, "available": location.available}

    @routes.post("/__test__/thumbnail-location")
    async def set_location(request: web.Request) -> web.Response:
        data = await request.json()
        mode = data.get("mode") if isinstance(data, dict) else None
        if mode not in ("source", "folder"):
            raise web.HTTPBadRequest()
        return web.json_response(await api._run_in_executor(use, mode))

    def files() -> dict:
        previews = sorted(
            path.relative_to(folder).as_posix()
            for path in folder.rglob("*_thumb.*")
            if path.is_file()
        )
        sources = [str(path) for path in api._get_all_output_dirs()]
        return {
            "folder": previews,
            "source_thumbnails": any(
                (Path(source) / "thumbnails").exists() for source in sources
            ),
        }

    @routes.get("/__test__/thumbnail-files")
    async def thumbnail_files(_request: web.Request) -> web.Response:
        return web.json_response(await api._run_in_executor(files))

    def remove() -> bool:
        shutil.rmtree(folder, ignore_errors=True)
        return preview_store.refresh()

    @routes.post("/__test__/thumbnail-folder/remove")
    async def remove_folder(_request: web.Request) -> web.Response:
        available = await api._run_in_executor(remove)
        return web.json_response({"available": available})

    return reset
