"""Route tests for the autotag endpoints.

The ML engine is replaced by a fake service so no model is ever loaded and
no network request is made.
"""

import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if "folder_paths" not in sys.modules:
    _stub = types.ModuleType("folder_paths")
    _stub.models_dir = tempfile.gettempdir()
    _stub.base_path = tempfile.gettempdir()
    _stub.get_output_directory = lambda: tempfile.gettempdir()
    sys.modules["folder_paths"] = _stub

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from database.operations import PromptDatabase
from py.api import PromptManagerAPI
from utils.hashing import generate_prompt_hash


class FakeAutotagService:
    """Stand-in for AutoTagService that records calls and returns fixed tags."""

    def __init__(self, tags=None, downloaded=True):
        self.tags = tags if tags is not None else ["1girl", "smile"]
        self.downloaded = downloaded
        self.generate_calls = []
        self.load_calls = []
        self.download_calls = []
        self.unload_calls = 0
        self.custom_prompt = ""
        self.default_prompt = "default prompt"
        self.wd14_general_threshold = 0.35
        self.wd14_character_threshold = 0.85
        self.models_config = {"gguf": {}, "wd14-vit": {}}
        self._loaded_type = None
        self.generate_error = None
        self.download_result = True
        self.load_error = None

    def get_models_status(self):
        return {
            name: {"downloaded": self.downloaded, "model_path": None}
            for name in self.models_config
        }

    def is_model_loaded(self):
        return self._loaded_type is not None

    def get_loaded_model_type(self):
        return self._loaded_type

    def load_model(self, model_type, use_gpu=True):
        if self.load_error is not None:
            raise self.load_error
        self.load_calls.append((model_type, use_gpu))
        self._loaded_type = model_type
        return True

    def unload_model(self):
        self.unload_calls += 1
        self._loaded_type = None

    def download_model(self, model_type, progress_callback=None):
        self.download_calls.append(model_type)
        if progress_callback:
            progress_callback("Downloading...", 50)
        return self.download_result

    def generate_tags(
        self, image_path, prompt=None, general_threshold=None, character_threshold=None
    ):
        self.generate_calls.append(
            (image_path, prompt, general_threshold, character_threshold)
        )
        if self.generate_error is not None:
            raise self.generate_error
        return list(self.tags)


class AutotagAPITestCase(AioHTTPTestCase):
    """Stands up the PromptManager routes with a temp DB, temp output dir and a fake engine."""

    async def get_application(self):
        self._temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self._temp_db.close()
        self.tmp_root = Path(tempfile.mkdtemp())
        self.output_dir = self.tmp_root / "output"
        self.output_dir.mkdir()
        self.secret_dir = self.tmp_root / "secret"
        self.secret_dir.mkdir()

        self.service = FakeAutotagService()
        self._service_patch = patch(
            "py.autotag.get_autotag_service", return_value=self.service
        )
        self._service_patch.start()

        app = web.Application()
        routes = web.RouteTableDef()
        self.api = PromptManagerAPI()
        self.api.db = PromptDatabase(self._temp_db.name)
        self.api._get_all_output_dirs = lambda: [self.output_dir]
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
        self._service_patch.stop()
        for path in (
            self._temp_db.name,
            self._temp_db.name + "-wal",
            self._temp_db.name + "-shm",
        ):
            if os.path.exists(path):
                os.unlink(path)
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    # ── helpers ────────────────────────────────────────────────────────

    def _save_prompt(self, text="Test prompt", tags=None):
        return self.api.db.save_prompt(
            text=text,
            category=None,
            tags=tags or [],
            rating=None,
            prompt_hash=generate_prompt_hash(text),
        )

    def _make_image(self, directory, name="img.png"):
        path = directory / name
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return path

    def _link_image(self, prompt_id, path):
        return self.api.db.link_image_to_prompt(prompt_id, str(path))

    async def _post_json(self, url, body):
        return await self.client.request(
            "POST",
            url,
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

    @staticmethod
    def _sse_events(text):
        events = []
        for line in text.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
        return events


class TestAutotagSingleContainment(AutotagAPITestCase):

    async def test_image_id_tags_the_linked_image(self):
        prompt_id = self._save_prompt()
        image = self._make_image(self.output_dir)
        image_id = self._link_image(prompt_id, image)

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"image_id": image_id}
        )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tags"], ["1girl", "smile"])
        self.assertEqual(data["prompt_id"], prompt_id)
        self.assertEqual(len(self.service.generate_calls), 1)
        self.assertEqual(
            os.path.realpath(self.service.generate_calls[0][0]),
            os.path.realpath(str(image)),
        )

    async def test_path_inside_output_dir_is_tagged(self):
        image = self._make_image(self.output_dir)

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": str(image)}
        )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tags"], ["1girl", "smile"])
        self.assertEqual(len(self.service.generate_calls), 1)

    async def test_path_outside_output_dir_is_forbidden(self):
        secret = self._make_image(self.secret_dir, "password.png")

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": str(secret)}
        )

        self.assertEqual(resp.status, 403)
        data = await resp.json()
        self.assertFalse(data["success"])
        self.assertNotIn(str(self.secret_dir), data["error"])
        self.assertEqual(self.service.generate_calls, [])

    async def test_traversal_path_is_forbidden(self):
        self._make_image(self.secret_dir, "password.png")
        traversal = os.path.join(str(self.output_dir), "..", "secret", "password.png")

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": traversal}
        )

        self.assertEqual(resp.status, 403)
        self.assertEqual(self.service.generate_calls, [])

    async def test_legacy_image_path_field_is_still_contained(self):
        secret = self._make_image(self.secret_dir, "password.png")

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"image_path": str(secret)}
        )

        self.assertEqual(resp.status, 403)
        self.assertEqual(self.service.generate_calls, [])

    async def test_sibling_prefix_dir_is_forbidden(self):
        sibling = self.tmp_root / (self.output_dir.name + "2")
        sibling.mkdir()
        image = self._make_image(sibling, "x.png")

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": str(image)}
        )

        self.assertEqual(resp.status, 403)
        self.assertEqual(self.service.generate_calls, [])

    async def test_symlink_escaping_output_dir_is_forbidden(self):
        secret = self._make_image(self.secret_dir, "password.png")
        link = self.output_dir / "link.png"
        try:
            link.symlink_to(secret)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": str(link)}
        )

        self.assertEqual(resp.status, 403)
        self.assertEqual(self.service.generate_calls, [])

    async def test_directory_path_is_forbidden(self):
        sub = self.output_dir / "subdir"
        sub.mkdir()

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": str(sub)}
        )

        self.assertEqual(resp.status, 403)
        self.assertEqual(self.service.generate_calls, [])

    async def test_image_id_row_pointing_outside_is_forbidden(self):
        prompt_id = self._save_prompt()
        secret = self._make_image(self.secret_dir, "password.png")
        image_id = self._link_image(prompt_id, secret)

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"image_id": image_id}
        )

        self.assertEqual(resp.status, 403)
        data = await resp.json()
        self.assertNotIn(str(self.secret_dir), data["error"])
        self.assertEqual(self.service.generate_calls, [])

    async def test_missing_selector_is_400(self):
        resp = await self._post_json("/prompt_manager/autotag/single", {})
        self.assertEqual(resp.status, 400)
        self.assertFalse((await resp.json())["success"])

    async def test_unknown_image_id_is_404(self):
        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"image_id": 9999}
        )
        self.assertEqual(resp.status, 404)
        self.assertFalse((await resp.json())["success"])

    async def test_non_integer_image_id_is_400(self):
        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"image_id": "abc"}
        )
        self.assertEqual(resp.status, 400)


class TestPathContainmentHelper(unittest.TestCase):
    """Pure checks on the containment predicate used by autotag/single."""

    def setUp(self):
        from py.api.autotag_routes import path_is_within

        self.path_is_within = path_is_within
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "out"
        self.root.mkdir()

    def test_child_inside_root(self):
        self.assertTrue(self.path_is_within(self.root / "a" / "b.png", self.root))

    def test_root_itself_is_not_a_child(self):
        self.assertFalse(self.path_is_within(self.root, self.root))

    def test_prefix_sibling_is_outside(self):
        self.assertFalse(self.path_is_within(self.tmp / "out2" / "b.png", self.root))

    def test_dotdot_is_outside(self):
        self.assertFalse(
            self.path_is_within(self.root / ".." / "secret" / "b.png", self.root)
        )

    def test_case_differences_are_folded_by_normcase(self):
        upper = Path(str(self.root).upper()) / "B.PNG"
        with patch("os.path.normcase", side_effect=lambda p: p.lower()):
            self.assertTrue(self.path_is_within(upper, self.root))

    def test_separator_differences_do_not_matter(self):
        mixed = str(self.root / "a" / "b.png").replace(os.sep, "/")
        self.assertTrue(self.path_is_within(mixed, self.root))


class TestAutotagSideEffectRoutesArePostOnly(AutotagAPITestCase):

    async def test_get_download_is_405(self):
        resp = await self.client.request("GET", "/prompt_manager/autotag/download/gguf")
        self.assertEqual(resp.status, 405)
        self.assertEqual(self.service.download_calls, [])

    async def test_get_start_is_405(self):
        resp = await self.client.request("GET", "/prompt_manager/autotag/start")
        self.assertEqual(resp.status, 405)
        self.assertEqual(self.service.load_calls, [])

    async def test_post_download_streams_completion(self):
        resp = await self.client.request(
            "POST", "/prompt_manager/autotag/download/gguf"
        )

        self.assertEqual(resp.status, 200)
        events = self._sse_events(await resp.text())
        self.assertEqual(events[-1]["type"], "complete")
        self.assertEqual(self.service.download_calls, ["gguf"])

    async def test_post_start_with_no_images_completes(self):
        resp = await self.client.request(
            "POST", "/prompt_manager/autotag/start?model_type=gguf"
        )

        self.assertEqual(resp.status, 200)
        events = self._sse_events(await resp.text())
        self.assertEqual(events[-1]["type"], "complete")
        self.assertEqual(events[-1]["processed"], 0)
        self.assertEqual(self.service.load_calls, [("gguf", True)])

    async def test_models_status_stays_get(self):
        resp = await self.client.request("GET", "/prompt_manager/autotag/models")
        self.assertEqual(resp.status, 200)
        self.assertTrue((await resp.json())["success"])


if __name__ == "__main__":
    unittest.main()
