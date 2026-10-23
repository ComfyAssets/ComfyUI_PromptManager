"""Serve production pages and API routes with disposable state for browser tests."""

import argparse
import asyncio
import json
import os
import shutil
import socket
from pathlib import Path
import sys
import tempfile
import threading
import time
import types

from aiohttp import web
from aiohttp.web_runner import GracefulExit
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from tests.browser.request_hold import register_request_holds
from tests.browser import slow_reset_watch
from tests.browser.workspace_activity_fixture import register_activity_commands
from tests.browser.workspace_concept_fixture import (
    MAX_EXTRA_IMAGES as CONCEPT_MAX_EXTRA_IMAGES,
    MAX_SEEDLESS_IMAGES as CONCEPT_MAX_SEEDLESS_IMAGES,
    MAX_EPOCH_SECONDS as CONCEPT_MAX_EPOCH_SECONDS,
    seed_workspace_concept,
)
from tests.browser.workspace_visual_fixture import seed_workspace_visual
from tests.browser.workspace_inspector_fixture import seed_workspace_inspector
from tests.browser.workspace_history_fixture import (
    capture_workspace_history_zero_outputs,
    register_workspace_history_commands,
    seed_workspace_history,
)
from tests.browser.workspace_library_editor_fixture import (
    register_library_editor_commands,
)
from tests.browser.workspace_vocabulary_fixture import register_vocabulary_commands
from tests.browser.workspace_tagging_fixture import register_tagging_commands

CANONICAL_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_ASSETS = {
    "/__test__/canonical/workspace": (
        "docs/concepts/workspace/index.html",
        "text/html",
    ),
    "/workspace/app.js": ("docs/concepts/workspace/app.js", "text/javascript"),
    "/workspace/style.css": ("docs/concepts/workspace/style.css", "text/css"),
    "/toolkit/core.js": ("web/js/shared/toolkit/core.js", "text/javascript"),
    "/toolkit/controls.css": ("web/js/shared/toolkit/controls.css", "text/css"),
    "/vendor/viewer.js": ("web/lib/viewerjs/viewer.min.js", "text/javascript"),
    "/vendor/viewer.css": ("web/lib/viewerjs/viewer.min.css", "text/css"),
}


class TestModel:
    default_prompt = "Describe the image"
    wd14_general_threshold = 0.35
    wd14_character_threshold = 0.85
    custom_prompt = ""
    loaded = None
    # Models the Tag image dialog workflows mark as not downloaded.
    unavailable: set = set()
    # Scored names the concept comparisons answer instead of the fixed list
    # (``/__test__/tagging/suggestions``); None keeps the fixed list.
    suggestions: list | None = None

    def get_models_status(self) -> dict:
        return {
            name: {
                "downloaded": name not in self.unavailable,
                "loaded": self.loaded == name,
            }
            for name in ("gguf", "hf", "wd14-vit", "wd14-convnext", "wd14-swinv2")
        }

    def is_model_loaded(self) -> bool:
        return self.loaded is not None

    def get_loaded_model_type(self) -> str | None:
        return self.loaded

    def load_model(self, model_type: str, use_gpu: bool = True) -> bool:
        self.loaded = model_type
        return True

    def unload_model(self) -> None:
        self.loaded = None

    def generate_tags(self, image_path: str, **kwargs: object) -> list[str]:
        return ["generated", Path(image_path).parent.parent.name]

    def generate_tag_suggestions(
        self, image_path: str, *, limit: int, **kwargs: object
    ) -> list[tuple[str, float | None]]:
        """Fixed scored suggestions (WD14) or unscored ones (language models)."""
        scored = self.loaded is not None and self.loaded.startswith("wd14")
        if self.suggestions is not None:
            return [
                (name, confidence if scored else None)
                for name, confidence in self.suggestions[:limit]
            ]
        names = ["sandstone", "desert", "arch", "sky", "rock", "dune", "canyon"]
        names += [f"tag_{index:02d}" for index in range(100)]
        return [
            (name, round(0.98 - index * 0.008, 3) if scored else None)
            for index, name in enumerate(names[:limit])
        ]

    def download_model(self, model_type: str, progress_callback=None) -> bool:
        raise AssertionError("browser tests never download a model")


REVEAL_OUTCOMES = ("opened", "unavailable", "fail")


def register_reveal_stub(routes, api, root: Path):
    """D38: Show in folder never opens a real file manager here.

    The API's opener records the resolved path; ``POST /__test__/reveal``
    sets the next outcome and ``GET /__test__/reveal`` lists the opened paths.
    """
    from utils.file_reveal import OpenerUnavailable, RevealGate

    state = {"outcome": "opened", "opened": []}

    def opener(path: Path, gallery_root: Path) -> None:
        if state["outcome"] == "unavailable":
            raise OpenerUnavailable()
        if state["outcome"] == "fail":
            raise OSError("stubbed start failure")
        state["opened"].append(Path(path).relative_to(root.resolve()).as_posix())

    api._reveal_opener = opener
    # Each click gets an answer here; the cooldown is a unit-tested default.
    api._reveal_gate = RevealGate(cooldown=0)

    @routes.post("/__test__/reveal")
    async def set_reveal_outcome(request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else None
        if (
            not isinstance(data, dict)
            or set(data) != {"outcome"}
            or data["outcome"] not in REVEAL_OUTCOMES
        ):
            raise web.HTTPBadRequest()
        state["outcome"] = data["outcome"]
        return web.json_response({"success": True})

    @routes.get("/__test__/reveal")
    async def reveal_calls(request: web.Request) -> web.Response:
        return web.json_response({"opened": state["opened"]})

    def reset() -> None:
        state["outcome"] = "opened"
        state["opened"].clear()

    return types.SimpleNamespace(reset=reset)


def create_app(root: Path) -> web.Application:
    os.environ["PROMPT_MANAGER_CONFIG_PATH"] = str(root / "config.json")
    os.chdir(root)
    app = web.Application()
    routes = web.RouteTableDef()
    server = types.ModuleType("server")
    server.PromptServer = types.SimpleNamespace(
        instance=types.SimpleNamespace(app=app, routes=routes)
    )
    sys.modules["server"] = server
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.base_path = str(root)
    folder_paths.models_dir = str(root / "models")
    for name in ("output", "input", "temp", "user"):
        directory = root / name
        directory.mkdir()
        setattr(folder_paths, f"get_{name}_directory", lambda path=directory: str(path))
    sys.modules["folder_paths"] = folder_paths

    from py.config import GalleryConfig, PromptManagerConfig

    PromptManagerConfig.DEFAULT_DB_PATH = str(root / "browser.db")
    GalleryConfig.MONITORING_DIRECTORIES = [str(root / "output"), str(root / "input")]

    from py.api import PromptManagerAPI
    from py import autotag
    from utils.hashing import generate_prompt_hash

    model = TestModel()
    autotag.get_autotag_service = lambda: model
    api = PromptManagerAPI()
    api.add_routes(routes)
    history_commands = register_workspace_history_commands(routes, api, root)
    library_editor_commands = register_library_editor_commands(routes, api, root)
    register_vocabulary_commands(routes, api, root)
    register_tagging_commands(routes, api, root, model)
    reveal_stub = register_reveal_stub(routes, api, root)
    register_activity_commands(app, routes, api)
    request_holds = register_request_holds(app, routes)
    slow_reset_watch.install(app, api)

    async def canonical_asset(request: web.Request) -> web.Response:
        filename, content_type = CANONICAL_ASSETS[request.path]
        return web.Response(
            body=(CANONICAL_ROOT / filename).read_bytes(),
            content_type=content_type,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    for path in CANONICAL_ASSETS:
        routes.get(path)(canonical_asset)

    def seed(
        extra_images: int = 0,
        include_video: bool = False,
        prompt_inspector: bool = False,
        native_library: bool = False,
        extra_prompts: int = 0,
        extra_variants: int = 0,
    ) -> None:
        with api.db.model.get_connection() as conn:
            for table in (
                "saved_views",
                "image_favorites",
                "media_image_tags",
                "media_annotation_bytes",
                "media_annotation_files",
                "image_prompt_pairs",
                "prompt_pair_tags",
                "prompt_pairs",
                "generated_images",
                "prompt_tags",
                "tags",
                "prompts",
            ):
                conn.execute(f"DELETE FROM {table}")
        for name in ("output", "input"):
            shutil.rmtree(root / name)
            (root / name).mkdir()
        for index, (directory, text, tag) in enumerate(
            [
                ("output", "Cat portrait", "cats"),
                (
                    "input",
                    "Cat portrait" if native_library else "Dog landscape",
                    "dogs",
                ),
            ]
        ):
            image_path = root / directory / "shared" / "same.png"
            image_path.parent.mkdir(exist_ok=True)
            info = PngInfo()
            info.add_text(
                "prompt",
                json.dumps(
                    {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": text}}}
                ),
            )
            with Image.new("RGB", (64, 64), (index * 255, 64, 128)) as image:
                image.save(image_path, pnginfo=info)
            if prompt_inspector or native_library:
                graph = {
                    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": text}},
                    "2": {
                        "class_type": "CLIPTextEncode",
                        "inputs": {
                            "text": "blurry, watermark" if index == 0 else "low quality"
                        },
                    },
                    "3": {
                        "class_type": "KSampler",
                        "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
                    },
                    "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}},
                }
                info = PngInfo()
                info.add_text("prompt", json.dumps(graph))
                with Image.new("RGB", (64, 64), (index * 255, 64, 128)) as image:
                    image.save(image_path, pnginfo=info)
            stamp = time.time() - index * 10
            os.utime(image_path, (stamp, stamp))
            pid = api.db.save_prompt(
                text, tags=[tag], prompt_hash=generate_prompt_hash(text)
            )
            api.db.link_image_to_prompt(pid, str(image_path))
            if index == 0:
                video_prompt_id = pid
                for number in range(extra_images):
                    extra = root / directory / "bulk" / f"{number:04}.png"
                    extra.parent.mkdir(exist_ok=True)
                    shutil.copyfile(image_path, extra)
                    api.db.link_image_to_prompt(pid, str(extra))
        if native_library:
            positive = api.db.get_prompt_by_hash(generate_prompt_hash("Cat portrait"))
            positive_id = positive["id"]
            api.db.update_prompt_metadata(
                positive_id, category="Animals", tags=["cats", "studio"]
            )
            variants = []
            for index, negative in enumerate(
                ["blurry, watermark", "low quality", "low contrast", ""]
            ):
                pair_id = api.db.save_prompt_pair(
                    "Cat portrait",
                    negative,
                    title=f"Portrait variant {index + 1}",
                    category="Portraits" if index == 0 else "Experiments",
                    tags=["studio"] if index == 0 else ["outdoors"],
                )
                variants.append(pair_id)
            with api.db.model.get_connection() as conn:
                images = conn.execute(
                    "SELECT id FROM generated_images WHERE prompt_id=? ORDER BY id",
                    (positive_id,),
                ).fetchall()
            for index, image in enumerate(images[:2]):
                api.db.link_image_to_pair(
                    variants[index], image["id"], {"source": "fixture", "anchor": index}
                )
            api.db.link_image_to_pair(
                variants[0], images[0]["id"], {"source": "fixture second evidence"}
            )
            api.db.record_prompt_pair_use(variants[0], times=3)
            for number in range(extra_variants):
                api.db.save_prompt_pair("Cat portrait", f"Negative extra {number:04}")
            legacy = root / "output" / "legacy-unassigned.png"
            shutil.copyfile(root / "output" / "shared" / "same.png", legacy)
            api.db.link_image_to_prompt(positive_id, str(legacy))
            api.db.save_prompt("Dog landscape", category="Animals", tags=["dogs"])
            api.db.save_prompt_pair("Dual role portrait", "Cat portrait")
            for number in range(extra_prompts):
                api.db.save_prompt(
                    f"Library extra {number:04}", category="Bulk", tags=["bulk"]
                )
        if prompt_inspector:
            for name, parameters in (
                (
                    "empty-negative.png",
                    "Explicit empty negative\nNegative prompt: \n"
                    "Steps: 20, Sampler: Euler",
                ),
                ("uncertain.png", None),
            ):
                info = PngInfo()
                if parameters:
                    info.add_text("parameters", parameters)
                with Image.new("RGB", (64, 64), (100, 80, 120)) as image:
                    image.save(root / "output" / name, pnginfo=info)
                os.utime(root / "output" / name, (time.time() - 100, time.time() - 100))
        api._generate_gallery_thumbnails_sync(api._get_all_output_dirs(), (150, 150))
        if include_video:
            # Seed after thumbnail generation to exercise a real unthumbnailed video.
            video = root / "output" / "clip.webm"
            shutil.copyfile(
                Path(__file__).resolve().parents[1] / "fixtures" / "tiny.webm", video
            )
            api.db.link_image_to_prompt(video_prompt_id, str(video))
        api.invalidate_gallery_cache()

    history_fixture = None

    @routes.post("/__test__/history/capture-zero-outputs")
    async def capture_history_outputs(request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else None
        if (
            not isinstance(data, dict)
            or set(data) != {"run_id"}
            or history_fixture is None
            or data["run_id"] not in history_fixture["runs"].values()
        ):
            raise web.HTTPBadRequest()
        captured = await api._run_in_executor(
            capture_workspace_history_zero_outputs, api, data["run_id"]
        )
        return web.json_response({"success": captured})

    @routes.post("/__test__/reset")
    async def reset(request: web.Request) -> web.Response:
        nonlocal history_fixture
        request_holds.release()
        library_editor_commands.reset()
        data = await request.json() if request.can_read_body else {}
        if not isinstance(data, dict):
            raise web.HTTPBadRequest()
        chronological_history = data.get("chronological_history", False)
        history_runs = data.get("history_runs", 0)
        if (
            not isinstance(chronological_history, bool)
            or isinstance(history_runs, bool)
            or not isinstance(history_runs, int)
            or not 0 <= history_runs <= 1200
            or (not chronological_history and "history_runs" in data)
            or (
                chronological_history
                and set(data) - {"chronological_history", "history_runs"}
            )
        ):
            raise web.HTTPBadRequest()
        if chronological_history:
            manifest = await api._run_in_executor(
                lambda: seed_workspace_history(api, root, history_runs=history_runs)
            )
            history_fixture = manifest
            return web.json_response({"success": True, **manifest})
        history_commands.reset()
        reveal_stub.reset()
        workspace_visual = data.get("workspace_visual", False)
        full_inspector = data.get("full_inspector", False)
        extra = data.get("extra_images", 0)
        include_video = data.get("include_video", False)
        prompt_inspector = data.get("prompt_inspector", False)
        native_library = data.get("native_library", False)
        extra_prompts = data.get("extra_prompts", 0)
        extra_variants = data.get("extra_variants", 0)
        if (
            not isinstance(workspace_visual, bool)
            or not isinstance(full_inspector, bool)
            or (
                full_inspector
                and (
                    workspace_visual
                    or prompt_inspector
                    or native_library
                    or extra_prompts != 0
                    or extra_variants != 0
                )
            )
            or (
                workspace_visual
                and any(
                    key in data
                    for key in (
                        "extra_images",
                        "include_video",
                        "prompt_inspector",
                        "native_library",
                        "extra_prompts",
                        "extra_variants",
                        "full_inspector",
                    )
                )
            )
            or isinstance(extra, bool)
            or not isinstance(extra, int)
            or not 0 <= extra <= 200
            or not isinstance(include_video, bool)
            or not isinstance(prompt_inspector, bool)
            or not isinstance(native_library, bool)
            or isinstance(extra_prompts, bool)
            or not isinstance(extra_prompts, int)
            or not 0 <= extra_prompts <= 200
            or isinstance(extra_variants, bool)
            or not isinstance(extra_variants, int)
            or not 0 <= extra_variants <= 200
        ):
            raise web.HTTPBadRequest()
        if workspace_visual:
            history_fixture = None
            await api._run_in_executor(seed_workspace_visual, api, root)
            return web.json_response({"success": True})
        if full_inspector:
            history_fixture = None
            await api._run_in_executor(
                lambda: seed_workspace_inspector(
                    api, root, extra_images=extra, include_video=include_video
                )
            )
            return web.json_response({"success": True})
        history_fixture = None
        await api._run_in_executor(
            seed,
            extra,
            include_video,
            prompt_inspector,
            native_library,
            extra_prompts,
            extra_variants,
        )
        return web.json_response({"success": True})

    @routes.post("/__test__/concept/reset")
    async def concept_reset(request: web.Request) -> web.Response:
        """Seed the concept-shaped fixture (D30); only a bounded image count."""
        nonlocal history_fixture
        data = await request.json() if request.can_read_body else {}
        limits = {
            "extra_images": CONCEPT_MAX_EXTRA_IMAGES,
            "seedless_images": CONCEPT_MAX_SEEDLESS_IMAGES,
        }
        if (
            request.query
            or not isinstance(data, dict)
            or set(data) - set(limits) - {"short_seedless", "now"}
            or not isinstance(data.get("short_seedless", False), bool)
            # A chosen "now" (epoch seconds) pins every time the fixture writes.
            or (
                "now" in data
                and (
                    isinstance(data["now"], bool)
                    or not isinstance(data["now"], (int, float))
                    or not 0 < data["now"] < CONCEPT_MAX_EPOCH_SECONDS
                )
            )
            or any(
                isinstance(data.get(key, 0), bool)
                or not isinstance(data.get(key, 0), int)
                or not 0 <= data.get(key, 0) <= limit
                for key, limit in limits.items()
            )
        ):
            raise web.HTTPBadRequest()
        history_commands.reset()
        library_editor_commands.reset()
        reveal_stub.reset()
        history_fixture = None
        manifest = await api._run_in_executor(
            lambda: seed_workspace_concept(
                api,
                root,
                extra_images=data.get("extra_images", 0),
                seedless_images=data.get("seedless_images", 0),
                short_seedless=data.get("short_seedless", False),
                now=data.get("now"),
            )
        )
        return web.json_response({"success": True, **manifest})

    @routes.post("/__test__/saved-views/raw")
    async def insert_raw_saved_view(request: web.Request) -> web.Response:
        """Store a view row as an older build wrote it, bypassing the API's rules."""
        data = await request.json()
        if (
            not isinstance(data, dict)
            or set(data) != {"page", "name", "state"}
            or not all(isinstance(data[key], str) for key in data)
        ):
            raise web.HTTPBadRequest()

        def insert() -> int:
            with api.db.model.get_connection() as conn:
                return conn.execute(
                    "INSERT INTO saved_views (page, name, state, position, "
                    "created_at, updated_at) VALUES (?, ?, ?, 0, 'x', 'x')",
                    (data["page"], data["name"], data["state"]),
                ).lastrowid

        return web.json_response(
            {"success": True, "id": await api._run_in_executor(insert)}
        )

    @routes.post("/__test__/add-nested-image")
    async def add_nested_image(request: web.Request) -> web.Response:
        destination = root / "output" / "project" / "day" / "new-output.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "output" / "shared" / "same.png", destination)
        return web.json_response({"success": True})

    @routes.post("/__test__/add-local-inspect-file")
    async def add_local_inspect_file(request: web.Request) -> web.Response:
        """Serve one committed Inspect fixture from the output folder.

        The local Inspect page reads a chosen file in the browser; its page
        workflow compares that rail with the Images viewer for the same bytes.
        Only a committed fixture name is accepted.
        """
        data = await request.json() if request.can_read_body else None
        fixtures = CANONICAL_ROOT / "tests" / "fixtures"
        allowed = {
            path.name: path
            for folder in ("inspection_parity", "local-inspect")
            for path in (fixtures / folder).iterdir()
            if path.suffix.lower() in {".png", ".jpg", ".webp", ".webm", ".mp4"}
        }
        if (
            not isinstance(data, dict)
            or set(data) != {"name"}
            or data["name"] not in allowed
        ):
            raise web.HTTPBadRequest()
        destination = root / "output" / "local-inspect" / data["name"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(allowed[data["name"]], destination)
        return web.json_response({"success": True})

    @routes.get("/api/settings/Comfy.ColorPalette")
    async def palette(request: web.Request) -> web.Response:
        return web.json_response("dark")

    async def cleanup(application: web.Application) -> None:
        api.db.close_all()

    seed()
    app.on_cleanup.append(cleanup)
    app.add_routes(routes)
    return app


HOST = "127.0.0.1"
PORT_ANNOUNCEMENT = "PM_TEST_SERVER_PORT="


def bind_listener(port: int) -> socket.socket:
    """Bind the server's socket before the app is built.

    ``port`` 0 lets the OS choose a free port, which this process then holds,
    so parallel launchers never race for one between choosing and binding.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if port and os.name != "nt":
            # aiohttp's own default on POSIX; on Windows it would let a
            # second process share the port.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((HOST, port))
        listener.listen(128)
    except OSError:
        listener.close()
        raise
    return listener


def _graceful_exit() -> None:
    raise GracefulExit()


def exit_when_stdin_closes(loop: asyncio.AbstractEventLoop) -> threading.Thread:
    """Stop the app gracefully once stdin reaches end of file.

    The launcher keeps the write end of this process's stdin pipe, and the OS
    closes it however the launcher dies, SIGKILL included. GracefulExit runs
    aiohttp's cleanup (closing the database) and lets the temp root be removed,
    on every OS, where a signal from a dead parent would not.
    """

    def watch() -> None:
        while sys.stdin.buffer.read(65536):
            pass
        loop.call_soon_threadsafe(_graceful_exit)

    thread = threading.Thread(target=watch, name="stdin-eof", daemon=True)
    thread.start()
    return thread


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--port", type=int, default=8765, help="0 binds an OS-chosen free port"
    )
    parser.add_argument(
        "--exit-on-stdin-eof",
        action="store_true",
        help="shut down when stdin closes (the launcher's parent-death guard)",
    )
    args = parser.parse_args(argv)
    listener = bind_listener(args.port)
    # One machine-readable line tells the launcher which port this process holds.
    print(f"{PORT_ANNOUNCEMENT}{listener.getsockname()[1]}", flush=True)
    loop = asyncio.new_event_loop()
    if args.exit_on_stdin_eof:
        exit_when_stdin_closes(loop)
    original_cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="pm-browser-") as directory:
        try:
            web.run_app(create_app(Path(directory).resolve()), sock=listener, loop=loop)
        finally:
            # create_app works inside the root; Windows cannot remove a process's cwd.
            os.chdir(original_cwd)


if __name__ == "__main__":
    main()
