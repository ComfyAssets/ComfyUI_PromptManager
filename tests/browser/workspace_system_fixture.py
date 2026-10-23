"""System test hooks for the browser tests (#249 System S2 Task 6).

The data is the concept-shaped real fixture (``POST /__test__/concept/reset``,
``workspace_concept_fixture.py``): its parents, saved pairs, images, ratings
and runs are what the Overview, the Statistics dialog and the health check
read. These per-server hooks only change what a System test needs on top:

- ``POST /__test__/system/reset`` restores the configured sources, forgets the
  session's health report and ends every injected failure;
- ``POST /__test__/system/fail`` ``{path, times}`` answers the next ``times``
  requests for ``path`` (a ``/prompt_manager/system/`` route) with the routes'
  500 error envelope;
- ``POST /__test__/system/missing-source`` configures one more source that
  does not exist (an unmounted drive);
- ``POST /__test__/system/missing-file`` deletes the newest linked image's
  file from disk, leaving its database row (a missing-file link);
- ``POST /__test__/system/unthumbnailed`` ``{count}`` writes ``count`` plain
  PNGs with no thumbnail into the output folder.

Delays use the shared request holds (``request_hold.py``).
"""

import os
from pathlib import Path

from aiohttp import web
from PIL import Image

FAILABLE_PREFIX = "/prompt_manager/system/"
MAX_UNTHUMBNAILED = 50


def register_system_commands(app, routes, api, root: Path):
    from py.config import GalleryConfig, PromptManagerConfig

    configured = list(GalleryConfig.MONITORING_DIRECTORIES)
    # System › Appearance saves the host fields to this server's config.json (S3);
    # reset restores both.
    config_path = Path(os.environ["PROMPT_MANAGER_CONFIG_PATH"])
    config_bytes = config_path.read_bytes() if config_path.exists() else None
    host_values = (
        PromptManagerConfig.RESULT_TIMEOUT,
        PromptManagerConfig.WEBUI_DISPLAY_MODE,
    )
    failures: dict[str, int] = {}

    @web.middleware
    async def inject_failures(request, handler):
        left = failures.get(request.path, 0)
        if left <= 0:
            return await handler(request)
        failures[request.path] = left - 1
        return web.json_response(
            {"success": False, "code": None, "error": "Injected failure"}, status=500
        )

    app.middlewares.append(inject_failures)

    def reset():
        failures.clear()
        GalleryConfig.MONITORING_DIRECTORIES = list(configured)
        api._health_report = None
        PromptManagerConfig.RESULT_TIMEOUT, PromptManagerConfig.WEBUI_DISPLAY_MODE = (
            host_values
        )
        if config_bytes is None:
            config_path.unlink(missing_ok=True)
        else:
            config_path.write_bytes(config_bytes)

    @routes.post("/__test__/system/reset")
    async def system_reset(request):
        reset()
        return web.json_response({"success": True})

    @routes.post("/__test__/system/fail")
    async def system_fail(request):
        data = await request.json()
        path, times = data.get("path"), data.get("times", 1)
        if (
            not isinstance(path, str)
            or not path.startswith(FAILABLE_PREFIX)
            or isinstance(times, bool)
            or not isinstance(times, int)
            or not 1 <= times <= 10
        ):
            raise web.HTTPBadRequest()
        failures[path] = times
        return web.json_response({"success": True})

    @routes.post("/__test__/system/missing-source")
    async def missing_source(request):
        absent = str(root / "unmounted-drive")
        if os.path.exists(absent):
            raise web.HTTPConflict()
        GalleryConfig.MONITORING_DIRECTORIES = [*configured, absent]
        return web.json_response(
            {"success": True, "configured": len(GalleryConfig.MONITORING_DIRECTORIES)}
        )

    @routes.post("/__test__/system/missing-file")
    async def missing_file(request):
        with api.db.model.get_connection() as conn:
            row = conn.execute(
                "SELECT file_path FROM generated_images ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            raise web.HTTPNotFound()
        Path(row[0]).unlink()
        api.invalidate_gallery_cache()
        return web.json_response({"success": True, "filename": Path(row[0]).name})

    @routes.post("/__test__/system/unthumbnailed")
    async def unthumbnailed(request):
        data = await request.json()
        count = data.get("count")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 1 <= count <= MAX_UNTHUMBNAILED
        ):
            raise web.HTTPBadRequest()
        folder = root / "output" / "unthumbnailed"
        folder.mkdir(exist_ok=True)
        for number in range(count):
            with Image.new("RGB", (64, 64), "#2b3442") as image:
                image.save(folder / f"plain_{number + 1:03}.png")
        api.invalidate_gallery_cache()
        return web.json_response({"success": True})

    return reset
