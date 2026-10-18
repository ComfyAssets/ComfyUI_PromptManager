"""Serve production pages and API routes with disposable state for browser tests."""

import argparse
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import time
import types

from aiohttp import web
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from tests.browser.workspace_visual_fixture import seed_workspace_visual
from tests.browser.workspace_inspector_fixture import seed_workspace_inspector
from tests.browser.workspace_history_fixture import (
    capture_workspace_history_zero_outputs,
    seed_workspace_history,
)


class TestModel:
    default_prompt = "Describe the image"
    wd14_general_threshold = 0.35
    wd14_character_threshold = 0.85
    custom_prompt = ""
    loaded = None

    def get_models_status(self) -> dict:
        return {
            name: {"downloaded": True, "loaded": self.loaded == name}
            for name in ("gguf", "wd14-vit", "wd14-convnext", "wd14-swinv2")
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

    @routes.post("/__test__/add-nested-image")
    async def add_nested_image(request: web.Request) -> web.Response:
        destination = root / "output" / "project" / "day" / "new-output.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "output" / "shared" / "same.png", destination)
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="pm-browser-") as directory:
        web.run_app(
            create_app(Path(directory).resolve()), host="127.0.0.1", port=args.port
        )


if __name__ == "__main__":
    main()
