"""POST /prompt_manager/images/rescope: a stale target's current identity (#430).

A page that holds ``{root_index, root_scope, path}`` after the sources were
renumbered asks where that file is now. The server maps the target only when
its scope was issued, at its old index, for one of the current roots, so the
answer names nothing the page could not already address; a removed or
replaced root, a foreign scope or a changed key stay refused.
"""

import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from test_api_images import ImageAPITestCase, make_png
except ImportError:  # imported as tests.test_api_rescope
    from tests.test_api_images import ImageAPITestCase, make_png

from utils.workspace_keys import WorkspaceKeys  # noqa: E402

RESCOPE = "/prompt_manager/images/rescope"


class TestRescopeRoute(ImageAPITestCase):
    async def get_application(self):
        app = await super().get_application()
        self.other_dir = self.comfy_dir / "other"
        self.other_dir.mkdir()
        make_png(self.output_dir / "sub" / "a.png")
        make_png(self.other_dir / "sub" / "a.png", color=(0, 0, 255))
        return app

    def target(self, path="sub/a.png", index=0, root=None):
        root = root or self.output_dir
        return {
            "root_index": index,
            "root_scope": self.api._gallery_root_scope(root, index),
            "path": path,
        }

    async def post(self, body):
        response = await self.client.post(RESCOPE, json=body)
        data = await response.json()
        self.assertNotIn(str(self.comfy_dir), json.dumps(data))
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        return response.status, data

    async def listed(self):
        response = await self.client.get(
            "/prompt_manager/images/output", params={"mode": "workspace"}
        )
        return {
            (item["root_index"], item["path"]): item
            for item in (await response.json())["images"]
        }

    async def test_an_unchanged_root_answers_the_listing_descriptor(self):
        status, data = await self.post({"target": self.target()})
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["image"], (await self.listed())[(0, "sub/a.png")])

    async def test_a_renumbered_root_maps_to_its_new_index(self):
        held = self.target()
        self.api._get_all_output_dirs = lambda: [self.other_dir, self.output_dir]
        status, data = await self.post({"target": held})
        self.assertEqual(status, 200)
        image = data["image"]
        self.assertEqual((image["root_index"], image["path"]), (1, "sub/a.png"))
        self.assertEqual(
            image["root_scope"], self.api._gallery_root_scope(self.output_dir, 1)
        )
        self.assertEqual(image, (await self.listed())[(1, "sub/a.png")])

    async def test_a_removed_replaced_or_foreign_root_is_source_changed(self):
        held = self.target()
        cases = {
            "removed": lambda: [],
            "replaced": lambda: [self.other_dir],
        }
        for name, roots in cases.items():
            with self.subTest(name):
                self.api._get_all_output_dirs = roots
                status, data = await self.post({"target": held})
                self.assertEqual((status, data["code"]), (409, "source-changed"))
        self.api._get_all_output_dirs = lambda: [self.output_dir]
        for scope in ("0" * 64, self.api._gallery_root_scope(self.output_dir, 1)):
            with self.subTest(scope=scope[:8]):
                status, data = await self.post(
                    {"target": {**held, "root_scope": scope}}
                )
                self.assertEqual((status, data["code"]), (409, "source-changed"))

    async def test_a_changed_key_cannot_be_mapped(self):
        held = self.target()
        rotated = WorkspaceKeys(b"n" * 32, b"m" * 32, False)
        with patch.object(self.api, "_workspace_keys", return_value=rotated):
            status, data = await self.post({"target": held})
        self.assertEqual((status, data["code"]), (409, "source-changed"))

    async def test_the_first_of_two_spellings_of_one_root_wins(self):
        held = self.target()
        self.api._get_all_output_dirs = lambda: [
            self.other_dir,
            self.output_dir,
            self.output_dir / "sub" / "..",
        ]
        status, data = await self.post({"target": held})
        self.assertEqual((status, data["image"]["root_index"]), (200, 1))

    async def test_a_missing_file_is_not_found(self):
        status, data = await self.post({"target": self.target("sub/gone.png")})
        self.assertEqual((status, data["code"]), (404, "file-missing"))
        (self.output_dir / "dir.png").mkdir()
        status, data = await self.post({"target": self.target("dir.png")})
        self.assertEqual((status, data["code"]), (404, "file-missing"))

    async def test_a_path_leaving_the_root_is_refused(self):
        outside = make_png(self.comfy_dir / "outside.png")
        self._make_symlink(outside, self.output_dir / "escape.png")
        status, _data = await self.post({"target": self.target("escape.png")})
        self.assertEqual(status, 403)

    async def test_previews_are_not_rescoped(self):
        make_png(self.output_dir / "thumbnails" / "a_thumb.png")
        status, _data = await self.post(
            {"target": self.target("thumbnails/a_thumb.png")}
        )
        self.assertEqual(status, 403)

    async def test_malformed_bodies_are_refused_before_any_file_access(self):
        good = self.target()
        bodies = [
            b"{",
            [],
            {},
            {"target": good, "extra": 1},
            {"target": {**good, "root_index": -1}},
            {"target": {**good, "root_index": True}},
            {"target": {**good, "root_index": 4096}},
            # Past Python's int-to-text digit limit: never a 500.
            b'{"target": {"root_index": 1' + b"0" * 5000 + b"}}",
            {"target": {**good, "root_scope": "A" * 64}},
            {"target": {**good, "root_scope": "abc"}},
            {"target": {**good, "path": ""}},
            {"target": {**good, "path": "../a.png"}},
            {"target": {**good, "path": "/abs.png"}},
            {"target": {**good, "path": "C:/a.png"}},
            {"target": {**good, "path": "sub\\a.png"}},
            {"target": {**good, "path": "sub//a.png"}},
            {"target": {**good, "path": "notes.txt"}},
            {"target": {**good, "path": "x" * 2000 + ".png"}},
        ]
        with patch("pathlib.Path.is_file", side_effect=AssertionError("file access")):
            for body in bodies:
                with self.subTest(body=str(body)[:60]):
                    if isinstance(body, bytes):
                        response = await self.client.post(
                            RESCOPE,
                            data=body,
                            headers={"Content-Type": "application/json"},
                        )
                    else:
                        response = await self.client.post(RESCOPE, json=body)
                    self.assertIn(response.status, (400, 403))
                    self.assertFalse((await response.json())["success"])
