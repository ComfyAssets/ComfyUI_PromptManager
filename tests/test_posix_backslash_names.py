"""A backslash inside a file or folder name on a POSIX server (#646).

A Windows-style ``filename_prefix`` (``model\\2026-11-17/name``) makes, on
Linux or macOS, one folder whose name holds a backslash. The listing names it;
every route that takes a listing path accepts it there, and keeps refusing it
on Windows, where a backslash separates folders.
"""

import ntpath
import os
import posixpath
import unittest
from unittest.mock import patch

try:
    from tests.test_image_delete_routes import ImageDeleteTestCase
except ImportError:  # discovered with tests/ as the top-level directory
    from test_image_delete_routes import ImageDeleteTestCase

from py.api import images, media_target as media_target_module
from py.api.media_target import media_target
from utils.media_folders import split_relative

POSIX_ONLY = unittest.skipIf(os.sep == "\\", "a backslash cannot be in a Windows name")
FOLDER = "model.safetensors\\2026-11-17"
NAME = f"{FOLDER}/a.png"
SCOPE = "a" * 64


class TestTargetsOnEitherServer(unittest.TestCase):
    """``media_target`` follows the server's separators; patched to try both."""

    def check(self, separators, path):
        pathmod = ntpath if "\\" in separators else posixpath
        with (
            patch.object(media_target_module, "path_separators", lambda: separators),
            patch.object(images, "path_separators", lambda: separators),
            patch.object(
                images, "split_relative", lambda text: split_relative(text, pathmod)
            ),
        ):
            return media_target({"root_index": 0, "root_scope": SCOPE, "path": path})

    def test_posix_accepts_a_backslash_inside_a_name(self):
        for path in (NAME, "a\\..\\b.png", "a\\b.png"):
            with self.subTest(path=path):
                target, error = self.check("/", path)
                self.assertIsNone(error)
                self.assertEqual(target, (0, SCOPE, path))

    def test_posix_still_refuses_traversal_and_absolute_paths(self):
        for path in ("../a.png", "a/../b.png", "/a.png", "a//b.png", "./a.png"):
            with self.subTest(path=path):
                self.assertIsNotNone(self.check("/", path)[1])

    def test_windows_refuses_every_backslash(self):
        for path in (NAME, "a\\..\\b.png", "\\a.png", "C:\\a.png", "a\\b.png"):
            with self.subTest(path=path):
                self.assertIsNotNone(self.check("/\\", path)[1])

    def test_traversal_check_splits_at_the_server_separators(self):
        self.assertEqual(split_relative("a\\..\\b", posixpath), ["a\\..\\b"])
        self.assertIn("..", split_relative("a\\..\\b", ntpath))
        if os.sep == "/":
            self.assertFalse(images._has_traversal("a\\..\\b.png"))
            self.assertFalse(images._has_traversal(NAME))
        self.assertTrue(images._has_traversal("a/../b.png"))
        self.assertTrue(images._has_traversal("/a.png"))


@POSIX_ONLY
class TestPosixRoutes(ImageDeleteTestCase):
    async def page(self, **params):
        response = await self.client.get(
            "/prompt_manager/images/output", params={"mode": "workspace", **params}
        )
        self.assertEqual(response.status, 200)
        return {image["path"]: image for image in (await response.json())["images"]}

    async def test_the_listing_marks_only_a_path_with_a_backslash(self):
        self.write(NAME, b"aaa")
        self.write("plain/b.png", b"bbb")
        listed = await self.page()
        self.assertEqual(listed[NAME]["path_style"], "posix")
        self.assertNotIn("path_style", listed["plain/b.png"])

    async def test_the_folder_filter_names_the_folder_with_its_backslash(self):
        self.write(NAME, b"aaa")
        self.write("model.safetensors/2026-11-17/b.png", b"bbb")
        self.assertEqual(list(await self.page(subfolder=FOLDER)), [NAME])
        self.assertEqual(
            list(await self.page(subfolder="model.safetensors/2026-11-17")),
            ["model.safetensors/2026-11-17/b.png"],
        )
        self.assertEqual(list(await self.page(path=NAME)), [NAME])

    async def test_a_favorite_lands_on_the_file_it_names(self):
        self.write(NAME, b"aaa")
        self.write("model.safetensors/2026-11-17/a.png", b"bbb")
        response = await self.client.put(
            "/prompt_manager/images/favorite",
            json={"root_index": 0, "path": NAME, "favorite": True},
        )
        self.assertEqual(response.status, 200, await response.text())
        listed = await self.page()
        self.assertTrue(listed[NAME]["favorite"])
        self.assertFalse(listed["model.safetensors/2026-11-17/a.png"]["favorite"])

    async def test_a_collection_takes_the_file(self):
        self.write(NAME, b"aaa")
        response = await self.client.post(
            "/prompt_manager/collections/items",
            json={"name": "c", "items": [self.target(NAME)]},
        )
        self.assertLess(response.status, 300, await response.text())
        collections = await (
            await self.client.get("/prompt_manager/collections")
        ).json()
        [collection] = [c["id"] for c in collections["collections"] if c["name"] == "c"]
        self.assertEqual(list(await self.page(collection=str(collection))), [NAME])

    async def test_delete_moves_only_that_file_to_the_trash(self):
        self.write(NAME, b"aaa")
        twin = self.write("model.safetensors/2026-11-17/a.png", b"bbb")
        _plan, job = await self.delete([NAME], "trash")
        self.assertEqual(job.state, "finished", job.summary())
        self.assertFalse((self.output_dir / NAME).exists())
        self.assertTrue(twin.exists())
        [(source, _destination)] = self.fake.moved
        self.assertEqual(source, str(self.output_dir / NAME))

    async def test_a_backslash_never_reaches_outside_the_root(self):
        # ``a\..\b.png`` is one name below the root on POSIX, not a way out of it.
        self.write("a\\..\\b.png", b"aaa")
        outside = self.write("b.png", b"bbb", folder=self.outside_dir)
        _plan, job = await self.delete(["a\\..\\b.png"], "trash")
        self.assertEqual(job.state, "finished", job.summary())
        self.assertTrue(outside.exists())
        self.assertFalse((self.output_dir / "a\\..\\b.png").exists())
