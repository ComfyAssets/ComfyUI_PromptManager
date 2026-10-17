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

    def seed(extra_images: int = 0, include_video: bool = False) -> None:
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
            [("output", "Cat portrait", "cats"), ("input", "Dog landscape", "dogs")]
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
        api._generate_gallery_thumbnails_sync(api._get_all_output_dirs(), (150, 150))
        if include_video:
            # Seed after thumbnail generation to exercise a real unthumbnailed video.
            video = root / "output" / "clip.webm"
            shutil.copyfile(
                Path(__file__).resolve().parents[1] / "fixtures" / "tiny.webm", video
            )
            api.db.link_image_to_prompt(video_prompt_id, str(video))
        api.invalidate_gallery_cache()

    @routes.post("/__test__/reset")
    async def reset(request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else {}
        extra = data.get("extra_images", 0)
        include_video = data.get("include_video", False)
        if (
            isinstance(extra, bool)
            or not isinstance(extra, int)
            or not 0 <= extra <= 200
            or not isinstance(include_video, bool)
        ):
            raise web.HTTPBadRequest()
        await api._run_in_executor(seed, extra, include_video)
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
