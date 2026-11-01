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
    from py.api import thumbnail_location, thumbnail_move_plan
    from py.config import PromptManagerConfig
    from py.config_store import get_config_store

    folder = root / FOLDER_NAME
    default = root / "default-thumbs"
    custom = root / "custom-thumbs"  # a folder a page types in
    journal = root / "data" / "thumbnail-move.json"
    # The move's journal and default folder live in this server's root, never
    # in the checkout's data folder.
    thumbnail_move_plan.journal_path = lambda: str(journal)
    thumbnail_move_plan.default_path = lambda: str(default)
    thumbnail_location.default_path = lambda: str(default)
    api.recover_thumbnail_move()

    def forget_config(config: dict) -> dict:
        section = config.get("thumbnails")
        if isinstance(section, dict):
            section.pop("location", None)
        return config

    real_free = thumbnail_move_plan._free

    def reset() -> None:
        thumbnail_move_plan._free = real_free
        preview_store.configure(None)
        PromptManagerConfig.THUMBNAIL_LOCATION = None
        store = get_config_store()
        config, _revision = store.read()
        if (
            isinstance(config.get("thumbnails"), dict)
            and "location" in config["thumbnails"]
        ):
            store.commit(forget_config, expected_revision=None)
        for path in (folder, default, custom):
            shutil.rmtree(path, ignore_errors=True)
        journal.unlink(missing_ok=True)

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
        setting = preview_store.setting()
        where = Path(setting.path) if setting.path else folder
        previews = sorted(
            path.relative_to(where).as_posix()
            for path in where.rglob("*_thumb.*")
            if path.is_file()
        )
        sources = [str(path) for path in api._get_all_output_dirs()]
        return {
            "folder": previews,
            "folder_exists": folder.exists() or default.exists() or custom.exists(),
            "custom_path": str(custom),
            "source_thumbnails": any(
                (Path(source) / "thumbnails").exists() for source in sources
            ),
        }

    @routes.get("/__test__/thumbnail-files")
    async def thumbnail_files(_request: web.Request) -> web.Response:
        return web.json_response(await api._run_in_executor(files))

    def remove() -> bool:
        setting = preview_store.setting()
        shutil.rmtree(setting.path or folder, ignore_errors=True)
        return preview_store.refresh()

    @routes.post("/__test__/thumbnail-folder/remove")
    async def remove_folder(_request: web.Request) -> web.Response:
        available = await api._run_in_executor(remove)
        return web.json_response({"available": available})

    def foreign() -> bool:
        """Another install recreated the folder: its marker has another id."""
        setting = preview_store.setting()

        def other(marker: dict) -> None:
            marker["id"] = "another-install"

        preview_location.write_marker(setting.path, other)
        return preview_store.refresh()

    @routes.post("/__test__/thumbnail-folder/foreign")
    async def foreign_folder(_request: web.Request) -> web.Response:
        return web.json_response({"available": await api._run_in_executor(foreign)})

    def unlisted() -> None:
        """A source this install no longer lists left its thumbnails here."""
        setting = preview_store.setting()

        def add(marker: dict) -> None:
            marker["keys"]["gone-0123abcd"] = {
                "path": "/gone",
                "identity": "/gone",
                "install": setting.install,
            }

        preview_location.write_marker(setting.path, add)
        gone = Path(setting.path) / "gone-0123abcd"
        gone.mkdir()
        (gone / "x_thumb.webp").write_bytes(b"preview")
        preview_store.refresh()

    @routes.post("/__test__/thumbnail-folder/unlisted")
    async def unlisted_source(_request: web.Request) -> web.Response:
        await api._run_in_executor(unlisted)
        return web.json_response({"success": True})

    @routes.post("/__test__/thumbnail-space/full")
    async def space_full(_request: web.Request) -> web.Response:
        """Every target disk reports 1 byte free until the next reset."""
        thumbnail_move_plan._free = lambda _path: 1
        return web.json_response({"success": True})

    return reset
