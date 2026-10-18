"""Opt-in workspace gallery contracts against disposable SQLite and media."""

import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

try:
    from test_api_images import ImageAPITestCase, make_png
    from open_handles import assert_closed
except ImportError:
    from tests.test_api_images import ImageAPITestCase, make_png
    from tests.open_handles import assert_closed


class TestWorkspaceImages(ImageAPITestCase):
    async def tearDownAsync(self):
        self.api.db.close_all()
        assert_closed(self, self._temp_db.name, self._output_tmp.name)
        await super().tearDownAsync()

    def media(self, name, mtime=100, root=None):
        target = (root or self.output_dir) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix == ".mp4":
            target.write_bytes(b"video")
        else:
            make_png(target)
        os.utime(target, (mtime, mtime))
        return target

    async def get(self, **params):
        response = await self.client.get(
            "/prompt_manager/images/output", params={"mode": "workspace", **params}
        )
        return response, await response.json()

    async def test_keyset_survives_insert_and_deterministic_ties(self):
        for name, stamp in (("a.png", 300), ("b.png", 200), ("c.png", 200)):
            self.media(name, stamp)
        response, first = await self.get(limit=2)
        self.assertEqual(response.status, 200)
        self.assertEqual([i["filename"] for i in first["images"]], ["a.png", "b.png"])
        self.assertTrue(first["has_more"])
        self.assertNotIn("offset", first)
        self.media("new.png", 400)
        self.api.invalidate_gallery_cache()
        _, second = await self.get(limit=2, cursor=first["next_cursor"])
        self.assertEqual([i["filename"] for i in second["images"]], ["c.png"])
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(second["total"], 4)

    async def test_filters_apply_before_limit_and_sorts_are_complete(self):
        self.media("folder/Zebra.png", 200)
        self.media("folder/apple.png", 300)
        self.media("other/Zebra.mp4", 100)
        _, data = await self.get(q="ZEBRA", media="image", limit=1)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["images"][0]["filename"], "Zebra.png")
        for order, expected in (
            ("newest", ["apple.png", "Zebra.png", "Zebra.mp4"]),
            ("oldest", ["Zebra.mp4", "Zebra.png", "apple.png"]),
            ("name", ["apple.png", "Zebra.mp4", "Zebra.png"]),
            ("name-desc", ["Zebra.png", "Zebra.mp4", "apple.png"]),
        ):
            with self.subTest(order=order):
                _, data = await self.get(sort=order)
                self.assertEqual([i["filename"] for i in data["images"]], expected)
        _, data = await self.get(subfolder="folder", media="video")
        self.assertEqual(data["images"], [])
        self.assertIsNone(data["next_cursor"])

    async def test_cursor_cannot_be_reused_with_changed_filters_or_tampered(self):
        self.media("a.png")
        self.media("b.png")
        _, first = await self.get(limit=1)
        token = first["next_cursor"]
        self.assertIsInstance(token, str)
        for params in (
            {"cursor": token, "q": "a"},
            {"cursor": token, "sort": "oldest"},
            {"cursor": token + "x"},
            {"cursor": "garbage"},
            {"cursor": "x" * 4097},
        ):
            response, data = await self.get(**params)
            self.assertEqual(response.status, 400, params)
            self.assertNotIn(str(self.comfy_dir), str(data))

    async def test_invalid_input_is_safe_and_page_limit_is_capped(self):
        for params in (
            {"root": "-1"},
            {"root": "1"},
            {"root": "no"},
            {"media": "audio"},
            {"sort": "size"},
            {"q": "x" * 501},
            {"limit": "no"},
            {"subfolder": "../secret"},
        ):
            response, data = await self.get(**params)
            self.assertEqual(response.status, 400, params)
            self.assertNotIn(str(self.comfy_dir), str(data))
        _, data = await self.get(limit=999999)
        from py.api.prompts import MAX_PAGE_LIMIT

        self.assertEqual(data["limit"], MAX_PAGE_LIMIT)

    async def test_root_identity_and_root_specific_folders(self):
        second = self.comfy_dir / "second"
        second.mkdir()
        self.media("first/a.png")
        self.media("nested/b.png", root=second)
        self.api._get_all_output_dirs = lambda: [self.output_dir, second]
        _, data = await self.get(root=1)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["images"][0]["root_index"], 1)
        response = await self.client.get(
            "/prompt_manager/gallery/subfolders?mode=workspace&root=1"
        )
        folders = await response.json()
        self.assertEqual(folders["subfolders"], ["nested"])
        self.assertEqual([r["index"] for r in folders["roots"]], [0, 1])
        self.assertNotIn(str(self.comfy_dir), str(folders))
        response = await self.client.get(
            "/prompt_manager/gallery/subfolders?mode=workspace&root=7"
        )
        self.assertEqual(response.status, 400)

    async def test_workspace_dimensions_are_header_only_and_video_is_unknown(self):
        self.media("a.png")
        self.media("b.mp4")
        self.media("broken.png").write_bytes(b"invalid")
        with patch(
            "PIL.PngImagePlugin.PngImageFile.load",
            side_effect=AssertionError("Do not decode"),
        ):
            _, data = await self.get()
        rows = {row["filename"]: row for row in data["images"]}
        self.assertEqual((rows["a.png"]["width"], rows["a.png"]["height"]), (4, 4))
        self.assertIsNone(rows["b.mp4"]["width"])
        self.assertIsNone(rows["broken.png"]["height"])
        response = await self.client.get("/prompt_manager/images/output")
        self.assertNotIn("width", (await response.json())["images"][0])

    async def test_invalid_limit_does_not_echo_private_input(self):
        response, data = await self.get(limit=str(self.comfy_dir))
        self.assertEqual(response.status, 400)
        self.assertNotIn(str(self.comfy_dir), str(data))

    async def test_each_sort_can_traverse_ties_and_limit_can_change(self):
        for name in ("a.png", "b.png", "sub/b.png", "c.png"):
            self.media(name)
        for order in ("newest", "oldest", "name", "name-desc"):
            with self.subTest(order=order):
                _, complete = await self.get(sort=order)
                _, first = await self.get(sort=order, limit=1)
                _, rest = await self.get(
                    sort=order, limit=10, cursor=first["next_cursor"]
                )
                self.assertEqual(first["images"] + rest["images"], complete["images"])

    async def test_root_ties_are_distinct_and_cursor_rejects_root_changes(self):
        second = self.comfy_dir / "second"
        second.mkdir()
        self.media("same.png")
        self.media("same.png", root=second)
        self.api._get_all_output_dirs = lambda: [self.output_dir, second]
        _, first = await self.get(limit=1)
        _, second_page = await self.get(limit=1, cursor=first["next_cursor"])
        self.assertEqual(
            [first["images"][0]["root_index"], second_page["images"][0]["root_index"]],
            [0, 1],
        )
        response, _ = await self.get(root=0, cursor=first["next_cursor"])
        self.assertEqual(response.status, 400)
        self.api._get_all_output_dirs = lambda: [second, self.output_dir]
        response, _ = await self.get(cursor=first["next_cursor"])
        self.assertEqual(response.status, 400)

    async def test_empty_roots_are_successful_empty_workspace(self):
        self.api._get_all_output_dirs = lambda: []
        response, data = await self.get()
        self.assertEqual(response.status, 200)
        self.assertEqual(data["images"], [])
        self.assertEqual(data["total"], 0)
        self.assertIsNone(data["next_cursor"])

    async def test_deep_unicode_paths_emit_bounded_usable_cursor(self):
        folder = self.output_dir.joinpath(*(["嵐" * 80] * 12))
        entries = [
            (folder / name, 100, self.output_dir, 0) for name in ("a.png", "b.png")
        ]

        def format_page(page):
            return [{"filename": entry[0].name} for entry in page]

        with (
            patch.object(
                self.api,
                "_get_gallery_files",
                AsyncMock(return_value=[(entry[0], entry[1]) for entry in entries]),
            ),
            patch.object(self.api, "_format_workspace_page", side_effect=format_page),
        ):
            for order in ("newest", "oldest", "name", "name-desc"):
                with self.subTest(sort=order):
                    response, first = await self.get(sort=order, limit=1)
                    self.assertEqual(response.status, 200)
                    self.assertLessEqual(len(first["next_cursor"]), 4096)
                    response, second = await self.get(
                        sort=order, cursor=first["next_cursor"]
                    )
                    self.assertEqual(response.status, 200)
                    self.assertEqual(len(second["images"]), 1)
                    self.assertNotEqual(first["images"], second["images"])

    async def test_filter_and_sort_selection_runs_in_executor(self):
        self.media("a.png")
        import threading

        event_thread = threading.get_ident()
        from py.api import images as images_module

        original = images_module._workspace_key
        threads = []

        def observe(entry, order):
            threads.append(threading.get_ident())
            return original(entry, order)

        with patch.object(images_module, "_workspace_key", side_effect=observe):
            response, _ = await self.get()
        self.assertEqual(response.status, 200)
        self.assertTrue(threads)
        self.assertTrue(all(thread != event_thread for thread in threads))

    async def test_workspace_reuses_scan_cache_and_legacy_contract(self):
        self.media("a.png")
        with patch.object(
            self.api,
            "_scan_gallery_files_sync",
            wraps=self.api._scan_gallery_files_sync,
        ) as scan:
            await self.get()
            await self.get(q="a")
            self.assertEqual(scan.call_count, 1)
        response = await self.client.get(
            "/prompt_manager/images/output?limit=1&offset=0"
        )
        data = await response.json()
        self.assertEqual(data["offset"], 0)
        self.assertNotIn("next_cursor", data)
        response = await self.client.get("/prompt_manager/gallery/subfolders")
        self.assertNotIn("roots", await response.json())

    async def test_root_scope_workspace_descriptors_guard_original_and_thumbnail_urls(
        self,
    ):
        from urllib.parse import parse_qs, quote, urlsplit

        roots = [self.output_dir, self.comfy_dir / "input"]
        self.api._get_all_output_dirs = lambda: roots
        relative = "nested/猫 % #.png"
        for index, root in enumerate(roots):
            self.media(relative, root=root)
            self.media("thumbnails/nested/猫 % #_thumb.png", root=root)
            make_png(root / relative, color=(index, 0, 0))
            make_png(root / "thumbnails/nested/猫 % #_thumb.png", color=(index, 0, 0))
        _, result = await self.get(limit=10)
        for entry in result["images"]:
            self.assertRegex(entry.get("root_scope", ""), r"^[0-9a-f]{64}$")
            self.assertIn(quote(relative, safe="/"), entry["url"])
            for url in (entry["url"], entry["thumbnail_url"]):
                query = parse_qs(urlsplit(url).query)
                self.assertEqual(query["root_scope"], [entry["root_scope"]])
                self.assertEqual(query["root"], [str(entry["root_index"])])
                response = await self.client.get(url)
                self.assertEqual(response.status, 200)
                expected = roots[entry["root_index"]] / (
                    "thumbnails/nested/猫 % #_thumb.png"
                    if url == entry["thumbnail_url"]
                    else relative
                )
                self.assertEqual(await response.read(), expected.read_bytes())
            self.assertIn("v", parse_qs(urlsplit(entry["thumbnail_url"]).query))
            response = await self.client.get(
                f"/prompt_manager/images/inspect/{quote(relative, safe='/')}",
                params={"root": entry["root_index"], "root_scope": entry["root_scope"]},
            )
            data = await response.json()
            self.assertEqual(data["root_scope"], entry["root_scope"])
            self.assertEqual(
                data["image"],
                {
                    "root_index": entry["root_index"],
                    "path": relative,
                    "filename": "猫 % #.png",
                },
            )


class TestWorkspaceCursorEncoding(unittest.TestCase):
    def test_ntfs_unicode_basename_cursor_is_bounded_and_round_trips(self):
        from py.api.images import (
            _workspace_cursor,
            _workspace_cursor_key,
            _workspace_key,
        )

        root = Path("gallery")
        entry = (root / ("\u0390" * 240 + ".png"), 100, root, 0)
        for order in ("newest", "oldest", "name", "name-desc"):
            with self.subTest(sort=order):
                key = _workspace_key(entry, order)
                token = _workspace_cursor(key, "scope")
                self.assertLessEqual(len(token), 4096)
                self.assertEqual(_workspace_cursor_key(token, "scope", order), key)
