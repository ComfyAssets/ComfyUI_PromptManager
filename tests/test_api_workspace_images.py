"""Opt-in workspace gallery contracts against disposable SQLite and media."""

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess  # nosec B404
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image, PngImagePlugin

from utils.generation_params import extract_from_records

try:
    from test_api_images import ImageAPITestCase, make_png
    from open_handles import assert_closed
    from generation_params_cases import CASES, encoder, ksampler
except ImportError:
    from tests.test_api_images import ImageAPITestCase, make_png
    from tests.open_handles import assert_closed
    from tests.generation_params_cases import CASES, encoder, ksampler

# Wall-clock budgets run only on request and untraced (TESTING.md
# "Performance budgets"); their structure is asserted everywhere.
PERF_BUDGET = os.environ.get("PROMPTMANAGER_PERF_BUDGET") == "1"
TRACED = sys.gettrace() is not None or (
    sys.version_info >= (3, 12)
    and any(sys.monitoring.get_tool(tool) for tool in range(6))
)
SEED_HARNESS = (
    Path(__file__).resolve().parent / "js" / "support" / "inspection-seed.mjs"
)
NODE_TIMEOUT_SECONDS = 120


def embedded_png(path, positive, negative=""):
    """A PNG whose API prompt graph resolves to *positive* and *negative*.

    The sampler has a model link, as every ComfyUI KSampler does, so the shared
    parser (which needs one) and the server resolver both read the prompts.
    """
    graph = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": positive}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": negative}},
        "3": {
            "class_type": "KSampler",
            "inputs": {"positive": ["1", 0], "negative": ["2", 0], "model": ["4", 0]},
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "fixture.safetensors"},
        },
    }
    info = PngImagePlugin.PngInfo()
    info.add_text("prompt", json.dumps(graph))
    with Image.new("RGB", (4, 4)) as image:
        image.save(path, pnginfo=info)
    os.utime(path, (100, 100))
    return path


def records_png(path, records, mtime=100):
    """A PNG carrying the given text records (None values are left out)."""
    info = PngImagePlugin.PngInfo()
    for key, text in records.items():
        if text is not None:
            info.add_text(key, text)
    with Image.new("RGB", (4, 4)) as image:
        image.save(path, pnginfo=info)
    os.utime(path, (mtime, mtime))
    return path


def png_chunk(kind, data):
    """One PNG chunk: length, type, data and CRC."""
    import struct
    import zlib

    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data))
    )


def png_with_text_chunks(path, before=(), after=(), mtime=100):
    """A PNG with ``(keyword, text)`` tEXt chunks before and after its IDAT."""
    import io

    buffer = io.BytesIO()
    with Image.new("RGB", (4, 4)) as image:
        image.save(buffer, format="PNG")
    data = buffer.getvalue()
    idat = data.index(b"IDAT") - 4
    iend = data.index(b"IEND") - 4

    def chunks(records):
        return b"".join(
            png_chunk(b"tEXt", key.encode("latin-1") + b"\0" + text.encode("latin-1"))
            for key, text in records
        )

    path.write_bytes(
        data[:idat] + chunks(before) + data[idat:iend] + chunks(after) + data[iend:]
    )
    os.utime(path, (mtime, mtime))
    return path


def seeded_records(positive, seed):
    """API prompt records for a one-sampler graph with *positive* and *seed*."""
    graph = {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "concept.safetensors"},
        },
        "6": encoder(positive),
        "7": encoder("blurry"),
        "3": ksampler(("6", 0), ("7", 0), seed=seed),
    }
    return {"prompt": json.dumps(graph)}


def header_params_of_images():
    """The card parser the images module calls (unpatched)."""
    from py.api import images

    return images.header_params


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

    async def test_an_unchanged_refresh_keeps_the_listing_and_derived_caches(self):
        from py.api import gallery_listing

        self.media("a.png", 300)
        self.media("b.png", 200)
        _, first = await self.get()
        response = await self.client.put(
            "/prompt_manager/images/favorite",
            json={"root_index": 0, "path": "a.png"},
        )
        self.assertEqual(response.status, 200)
        record = self.api._root_listings[str(self.output_dir)]
        files = record.files
        key = ((0, str(self.output_dir)),)
        newest = self.api._workspace_order_cache[key][1]["newest"]
        favorites = self.api._favorite_listing_cache[str(self.output_dir)]
        record.walked_at -= gallery_listing.REFRESH_AFTER + 1
        record.last_started_at -= gallery_listing.REFRESH_AFTER + 1
        with patch.object(
            self.api,
            "_scan_gallery_files_sync",
            wraps=self.api._scan_gallery_files_sync,
        ) as scan:
            with patch.object(gallery_listing, "REFRESH_DELAY", 0):
                await self.get()  # served at once; one refresh walk follows
            while record.rewalk_handle is not None:
                await asyncio.sleep(0.01)
            while self.api._listing_tasks:
                await asyncio.gather(*list(self.api._listing_tasks))
                # A finished walk leaves the set in a done callback, which needs
                # a loop turn; gather over finished tasks does not yield one.
                await asyncio.sleep(0)
            _, again = await self.get()
        self.assertEqual(scan.call_count, 1)
        self.assertIs(record.files, files)
        self.assertIs(self.api._workspace_order_cache[key][1]["newest"], newest)
        self.assertIs(self.api._favorite_listing_cache[str(self.output_dir)], favorites)
        self.assertEqual(again["total"], first["total"])

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

    async def test_path_filter_lists_only_that_exact_file(self):
        # Activity (D57): a scan row opens its file by root and exact relative path,
        # never by a substring that also matches the same name in other folders.
        self.media("ComfyUI_00001_.png")
        for day in range(3):
            self.media(f"2026-10-0{day + 1}/ComfyUI_00001_.png")
        self.media("2026-10-01/ComfyUI_00001_.png.bak.png")
        _, data = await self.get(path="ComfyUI_00001_.png")
        self.assertEqual([i["path"] for i in data["images"]], ["ComfyUI_00001_.png"])
        _, data = await self.get(path="2026-10-02/ComfyUI_00001_.png")
        self.assertEqual(
            [i["path"] for i in data["images"]], ["2026-10-02/ComfyUI_00001_.png"]
        )
        _, data = await self.get(path="missing.png")
        self.assertEqual(data["images"], [])
        for bad in ("../outside.png", "a" * 1025):
            with self.subTest(bad=bad[:12]):
                response, _ = await self.get(path=bad)
                self.assertEqual(response.status, 400)

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

    async def test_workspace_roots_list_their_own_folders(self):
        # "Refine this view" offers only the chosen Source's folders (#249 m1).
        second = self.comfy_dir / "second"
        second.mkdir()
        self.media("first/deep/a.png")
        self.media("nested/b.png", root=second)
        self.api._get_all_output_dirs = lambda: [self.output_dir, second]
        deep = os.path.join("first", "deep")
        response = await self.client.get(
            "/prompt_manager/gallery/subfolders?mode=workspace&include_ancestors=true"
        )
        folders = await response.json()
        self.assertEqual(folders["subfolders"], sorted(["first", deep, "nested"]))
        self.assertEqual(
            [(r["index"], r["subfolders"]) for r in folders["roots"]],
            [(0, sorted(["first", deep])), (1, ["nested"])],
        )
        # A chosen root narrows the union; every root still lists its own.
        response = await self.client.get(
            "/prompt_manager/gallery/subfolders?mode=workspace&root=1"
        )
        folders = await response.json()
        self.assertEqual(folders["subfolders"], ["nested"])
        self.assertEqual(
            [r["subfolders"] for r in folders["roots"]], [[deep], ["nested"]]
        )

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

    def _link_fixtures(self):
        """Linked (titled, first-line, pair, variant-titled pair, untitled pair),
        ambiguous and none."""
        db = self.api.db
        long_line = "lantern " * 15
        titled = self._save_prompt("Titled positive", category="Night", rating=5)
        with db.model.get_connection() as connection:
            connection.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (titled, "Stored library title"),
            )
        plain = self._save_prompt(long_line)
        # A pair's own title names its negative variant; the card shows the
        # linked positive's title (D10/D20), never the variant name.
        pair = db.save_prompt_pair("Pair positive", "Noise", title="Pair title")
        with db.model.get_connection() as connection:
            connection.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (db.get_prompt_pair(pair)["positive_prompt_id"], "Pair positive title"),
            )
        variant = db.save_prompt_pair("Variant positive", "Grain", title="Variant name")
        bare = db.save_prompt_pair("Bare pair positive\nmore", "Blur")
        first, second = self._save_prompt("Owner one"), self._save_prompt("Owner two")
        files = {
            name: self.media(f"{name}.png")
            for name in ("titled", "pair", "variant", "ambiguous", "none")
        }
        # First-line titles come from files whose own prompt evidence agrees.
        files["plain"] = embedded_png(self.output_dir / "plain.png", long_line)
        files["bare"] = embedded_png(
            self.output_dir / "bare.png", "Bare pair positive\nmore", "Blur"
        )
        db.link_image_to_prompt(titled, str(files["titled"]))
        db.link_image_to_prompt(plain, str(files["plain"]))
        for name, pair_id in (("pair", pair), ("variant", variant), ("bare", bare)):
            positive = db.get_prompt_pair(pair_id)["positive_prompt_id"]
            image = db.link_image_to_prompt(positive, str(files[name]))
            db.link_image_to_pair(pair_id, image, {"source": "fixture"})
        db.link_image_to_prompt(first, str(files["ambiguous"]))
        db.link_image_to_prompt(second, str(files["ambiguous"]))
        return {
            "titled": ("linked", titled, "Stored library title"),
            "plain": ("linked", plain, long_line.strip()[:80] + "\u2026"),
            "pair": (
                "linked",
                db.get_prompt_pair(pair)["positive_prompt_id"],
                "Pair positive title",
            ),
            # No stored title and no file evidence: the card shows the filename.
            "variant": (
                "linked",
                db.get_prompt_pair(variant)["positive_prompt_id"],
                None,
            ),
            "bare": (
                "linked",
                db.get_prompt_pair(bare)["positive_prompt_id"],
                "Bare pair positive",
            ),
            "ambiguous": ("ambiguous", None, None),
            "none": ("none", None, None),
        }

    async def test_workspace_items_carry_link_summary_matching_inspection(self):
        expected = self._link_fixtures()
        _, data = await self.get(limit=50)
        items = {Path(item["filename"]).stem: item for item in data["images"]}
        self.assertEqual(set(items), set(expected))
        for name, (status, positive_id, title) in expected.items():
            with self.subTest(file=name):
                linked = items[name]["linked"]
                self.assertEqual(
                    set(linked),
                    {
                        "status",
                        "positive_id",
                        "title",
                        "title_source",
                        "category",
                        "rating",
                    },
                )
                self.assertEqual(
                    (linked["status"], linked["positive_id"], linked["title"]),
                    (status, positive_id, title),
                )
                response = await self.client.get(
                    f"/prompt_manager/images/inspect/{name}.png",
                    params={"root": 0, "root_scope": items[name]["root_scope"]},
                )
                self.assertEqual(response.status, 200)
                library = (await response.json())["library"]
                self.assertEqual(
                    (library["status"], library["positive_id"], library["title"]),
                    (linked["status"], linked["positive_id"], linked["title"]),
                )
        self.assertEqual(
            (
                items["titled"]["linked"]["category"],
                items["titled"]["linked"]["rating"],
            ),
            ("Night", 5),
        )

    async def test_stored_title_shows_on_card_and_inspector_without_evidence(self):
        # A stored title is user data: shown on both, even when it equals the
        # first line and the file carries no prompt metadata.
        prompt = self._save_prompt("Same words\nrest")
        with self.api.db.model.get_connection() as connection:
            connection.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (prompt, "Same words"),
            )
        path = self.media("stored.png")
        self.api.db.link_image_to_prompt(prompt, str(path))
        _, data = await self.get()
        linked = data["images"][0]["linked"]
        self.assertEqual(
            (linked["title"], linked["title_source"]), ("Same words", "stored")
        )
        library = await self.inspected("stored.png", data["images"][0])
        self.assertEqual(library["title"], linked["title"])
        self.assertEqual(library["positive_id"], prompt)

    async def test_unconfirmed_text_title_is_withheld_on_card_and_inspector(self):
        prompt = self._save_prompt("Secret first line\nrest")
        path = self.media("plain.png")
        self.api.db.link_image_to_prompt(prompt, str(path))
        _, data = await self.get()
        item = data["images"][0]
        response = await self.client.get(
            "/prompt_manager/images/inspect/plain.png",
            params={"root": 0, "root_scope": item["root_scope"]},
        )
        body = await response.json()
        self.assertEqual(body["library"]["positive_id"], prompt)
        self.assertIsNone(body["library"]["title"])
        self.assertNotIn("Secret first line", json.dumps(body))
        self.assertIsNone(item["linked"]["title"])
        self.assertNotIn("Secret first line", json.dumps(data))

    async def test_summary_failure_keeps_inspector_link(self):
        prompt = self._save_prompt("Readable link")
        path = self.media("a.png")
        self.api.db.link_image_to_prompt(prompt, str(path))
        _, data = await self.get()
        with patch.object(
            self.api.db,
            "get_image_link_summaries",
            side_effect=RuntimeError("summary gone"),
        ):
            library = await self.inspected("a.png", data["images"][0])
        self.assertEqual(library["status"], "linked")
        self.assertEqual(library["positive_id"], prompt)
        self.assertIsNone(library["title"])

    async def inspected(self, name, item):
        response = await self.client.get(
            f"/prompt_manager/images/inspect/{name}",
            params={"root": 0, "root_scope": item["root_scope"]},
        )
        self.assertEqual(response.status, 200)
        return (await response.json())["library"]

    def test_link_summary_reads_no_pair_title(self):
        # The pair's own title names its negative variant and never titles a
        # card (D10), so the summary query does not read it (#249 m4).
        from database.operations import _LINK_SUMMARY_SQL

        self.assertNotIn("pp.title", _LINK_SUMMARY_SQL)

    async def test_link_summary_uses_one_query_per_page(self):
        prompt = self._save_prompt("Paged prompt")
        for index in range(12):
            path = self.media(f"{index:02}.png", mtime=100 + index)
            if index % 2:
                self.api.db.link_image_to_prompt(prompt, str(path))
        statements = []
        original = self.api.db.model.get_connection

        def trace_connection():
            connection = original()
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(
            self.api.db.model, "get_connection", side_effect=trace_connection
        ):
            response, data = await self.get(limit=12)
        self.assertEqual(response.status, 200)
        self.assertEqual(len(data["images"]), 12)
        self.assertEqual(
            sorted(item["linked"]["status"] for item in data["images"]),
            ["linked"] * 6 + ["none"] * 6,
        )
        selects = [
            query
            for query in statements
            if query.lstrip().upper().startswith(("SELECT", "WITH"))
        ]
        # The page's link summary, the positive count's one link read and
        # the page's one favorites read.
        self.assertEqual(len(selects), 3, selects)
        self.assertEqual(sum("image_favorites" in q for q in selects), 1, selects)
        self.assertEqual(data["positive_count"], 1)  # six files, one prompt

    async def test_link_summary_runs_in_executor_and_survives_database_errors(self):
        import threading

        self.media("a.png")
        event_thread = threading.get_ident()
        threads = []

        def broken(paths, allow_filename_fallback=True):
            threads.append(threading.get_ident())
            raise RuntimeError("database is gone")

        with patch.object(self.api.db, "get_image_link_summaries", side_effect=broken):
            response, data = await self.get()
        self.assertEqual(response.status, 200)
        self.assertTrue(threads)
        self.assertNotEqual(threads[0], event_thread)
        self.assertEqual(data["images"][0]["linked"]["status"], "unavailable")
        self.assertIsNone(data["images"][0]["linked"]["title"])
        self.assertIsNone(data["images"][0]["linked"]["title_source"])
        self.assertNotIn("database is gone", str(data))

    async def test_items_carry_seed_from_header_records(self):
        records_png(self.output_dir / "seeded.png", seeded_records("a fox", 42001))
        self.media("plain.png")
        self.media("clip.mp4")
        self.media("broken.png").write_bytes(b"invalid")
        _, data = await self.get()
        seeds = {item["filename"]: item["seed"] for item in data["images"]}
        self.assertEqual(
            seeds,
            {
                "seeded.png": "42001",
                "plain.png": None,
                "clip.mp4": None,
                "broken.png": None,
            },
        )

    async def test_card_reads_each_header_once(self):
        from py.api import images

        for index in range(3):
            records_png(
                self.output_dir / f"{index}.png", seeded_records("a fox", index)
            )
        self.media("clip.mp4")
        with (
            patch.object(
                images, "read_inspection_file", wraps=images.read_inspection_file
            ) as read,
            patch("PIL.Image.open", wraps=Image.open) as opened,
        ):
            _, data = await self.get()
        self.assertEqual(len(data["images"]), 4)
        # One inspector read per PNG (dimensions and records); no decoder open.
        self.assertEqual(read.call_count, 3)
        self.assertEqual(opened.call_count, 0)

    async def test_card_reads_png_records_as_the_inspector_does(self):
        one, two = seeded_records("a fox", 1), seeded_records("a fox", 2)
        # Conflicting duplicate prompt chunks: the inspector refuses the file,
        # so the card shows no seed (Pillow alone would keep the last one).
        png_with_text_chunks(
            self.output_dir / "conflict.png",
            before=[("prompt", one["prompt"]), ("prompt", two["prompt"])],
            mtime=300,
        )
        # Text after IDAT: Pillow's header open misses it; the inspector reads it.
        png_with_text_chunks(
            self.output_dir / "late.png", after=[("prompt", one["prompt"])], mtime=200
        )
        png_with_text_chunks(
            self.output_dir / "repeat.png",
            before=[("prompt", one["prompt"]), ("prompt", one["prompt"])],
        )
        response, data = await self.get()
        self.assertEqual(response.status, 200, data)
        items = {item["filename"]: item for item in data["images"]}
        self.assertIsNone(items["conflict.png"]["seed"])
        self.assertEqual(
            (items["conflict.png"]["width"], items["conflict.png"]["height"]), (4, 4)
        )
        self.assertEqual(items["late.png"]["seed"], "1")
        self.assertEqual(items["repeat.png"]["seed"], "1")
        shown = await self._inspector_seeds(data["images"])
        self.assertNotEqual(shown["conflict.png"]["status"], "known")
        self.assertEqual(shown["late.png"], {"status": "known", "value": "1"})

    async def test_deeply_nested_record_still_lists_and_inspects(self):
        nested = "[" * 900 + "1" + "]" * 900
        workflow = (
            '{"nodes":[{"id":%s,"type":"KSampler","widgets_values":[1]}],'
            '"links":[[%s,1,0,2,0]]}' % (nested, nested)
        )
        records_png(self.output_dir / "ok.png", seeded_records("a fox", 5), mtime=200)
        records_png(self.output_dir / "evil.png", {"workflow": workflow})
        prompt = self._save_prompt("Evil first line")
        self.api.db.link_image_to_prompt(prompt, str(self.output_dir / "evil.png"))
        response, data = await self.get()
        self.assertEqual(response.status, 200, data)
        items = {item["filename"]: item for item in data["images"]}
        self.assertEqual(items["ok.png"]["seed"], "5")
        response = await self.client.get(
            "/prompt_manager/images/inspect/evil.png",
            params={"root": 0, "root_scope": items["evil.png"]["root_scope"]},
        )
        self.assertEqual(response.status, 200, await response.text())
        library = (await response.json())["library"]
        self.assertEqual(library["title"], items["evil.png"]["linked"]["title"])
        shown = (await self._inspector_seeds([items["evil.png"]]))["evil.png"]
        card = items["evil.png"]["seed"]
        if card is None:
            self.assertNotEqual(shown["status"], "known")
        else:
            self.assertEqual(shown, {"status": "known", "value": card})

    async def test_warm_page_reuses_card_facts_per_scan(self):
        from py.api import images

        a = records_png(self.output_dir / "a.png", seeded_records("a fox", 1), 300)
        b = records_png(self.output_dir / "b.png", seeded_records("a cat", 2), 200)
        gone = records_png(self.output_dir / "gone.png", seeded_records("x", 3), 100)
        real_read, real_parse = images.read_inspection_file, images.header_params
        read, parsed = [], []

        def reading(path):
            read.append(path.name)
            return real_read(path)

        def parsing(info):
            parsed.append(info)
            return real_parse(info)

        async def page():
            read.clear()
            parsed.clear()
            response, data = await self.get()
            self.assertEqual(response.status, 200, data)
            return {item["filename"]: item for item in data["images"]}

        with (
            patch.object(images, "read_inspection_file", side_effect=reading),
            patch.object(images, "header_params", side_effect=parsing),
        ):
            cold = await page()
            self.assertEqual(sorted(read), ["a.png", "b.png", "gone.png"])
            self.assertEqual(len(parsed), 3)
            # A warm page reads and parses no header.
            prompt = self._save_prompt("a fox")
            self.api.db.link_image_to_prompt(prompt, str(a))
            warm = await page()
            self.assertEqual((read, parsed), ([], []))
            self.assertEqual(warm["a.png"]["seed"], "1")
            self.assertEqual((warm["a.png"]["width"], warm["a.png"]["height"]), (4, 4))
            # Links are never cached: the new link's confirmed title shows.
            self.assertIsNone(cold["a.png"]["linked"]["title"])
            self.assertEqual(warm["a.png"]["linked"]["title"], "a fox")
            # A monitor invalidation keeps the values of unchanged files.
            records_png(b, seeded_records("a cat", 22), mtime=250)
            gone.unlink()
            self.api.invalidate_gallery_cache()
            changed = await page()
            self.assertEqual(read, ["b.png"])
            self.assertEqual(changed["b.png"]["seed"], "22")
            cards = self.api._workspace_card_cache[(0, str(self.output_dir))]
            self.assertEqual(
                sorted(path.name for path, _mtime in cards), ["a.png", "b.png"]
            )
            # A header that cannot be read (locked, half-written) is not stored.
            records_png(self.output_dir / "c.png", seeded_records("a cow", 4), 400)
            self.api.invalidate_gallery_cache()
            with patch.object(images, "_card_header", side_effect=OSError("busy")):
                locked = await page()
            self.assertIsNone(locked["c.png"]["seed"])
            self.assertEqual(locked["a.png"]["seed"], "1")
            self.assertEqual((await page())["c.png"]["seed"], "4")
            self.assertEqual(read, ["c.png"])
            # A roots change drops them, as it drops the per-file values.
            self.api.reset_gallery_roots_cache()
            self.assertEqual(self.api._workspace_card_cache, {})
            await page()
            self.assertEqual(sorted(read), ["a.png", "b.png", "c.png"])

    async def test_unbounded_digit_string_seed_still_lists(self):
        records_png(
            self.output_dir / "huge.png", seeded_records("a fox", "9" * 5000), 200
        )
        records_png(self.output_dir / "ok.png", seeded_records("a fox", 5))
        response, data = await self.get()
        self.assertEqual(response.status, 200, data)
        items = {item["filename"]: item for item in data["images"]}
        self.assertIsNone(items["huge.png"]["seed"])
        self.assertEqual(items["ok.png"]["seed"], "5")

    async def test_one_cards_parse_error_spares_the_page(self):
        prompt = self._save_prompt("a fox")
        # Cards are read on the image pool (#523), in no fixed order: the
        # parser fails on bad.png's own record (seed 6), not on a call count.
        for name, mtime, seed in (("bad.png", 200, 6), ("ok.png", 100, 5)):
            path = records_png(
                self.output_dir / name, seeded_records("a fox", seed), mtime
            )
            self.api.db.link_image_to_prompt(prompt, str(path))
        real_parse = header_params_of_images()

        def parsing(info):
            if '"seed": 6' in info["prompt"]:
                raise RuntimeError("unexpected parser bug")
            return real_parse(info)

        with (
            patch("py.api.images.header_params", side_effect=parsing),
            self.assertLogs(self.api.logger, "WARNING") as logged,
        ):
            response, data = await self.get()
        self.assertEqual(response.status, 200, data)
        items = {item["filename"]: item for item in data["images"]}
        self.assertIsNone(items["bad.png"]["seed"])
        self.assertIsNone(items["bad.png"]["linked"]["title"])
        self.assertEqual(items["bad.png"]["linked"]["status"], "linked")
        self.assertEqual(items["ok.png"]["seed"], "5")
        self.assertEqual(items["ok.png"]["linked"]["title"], "a fox")
        self.assertNotIn("unexpected parser bug", json.dumps(data))
        self.assertIn("unexpected parser bug", "\n".join(logged.output))

    async def test_library_previews_skip_card_facts(self):
        records_png(self.output_dir / "a.png", seeded_records("a fox", 5))
        page = await self.api._collect_output_images(
            self.api._get_all_output_dirs(), ""
        )
        with patch("py.api.images.header_params") as parsed:
            items = self.api._format_workspace_page(page, linked=False)
        parsed.assert_not_called()
        self.assertNotIn("seed", items[0])
        self.assertEqual((items[0]["width"], items[0]["height"]), (4, 4))

    async def test_card_facts_parse_after_the_file_is_closed(self):
        records_png(self.output_dir / "a.png", seeded_records("a fox", 5))
        streams, closed = [], []
        real_open, real_parse = Path.open, header_params_of_images()

        def opening(path, *args, **kwargs):
            stream = real_open(path, *args, **kwargs)
            if path.name == "a.png":
                streams.append(stream)
            return stream

        def parsing(info):
            closed.append(bool(streams) and all(s.closed for s in streams))
            return real_parse(info)

        with (
            patch.object(Path, "open", autospec=True, side_effect=opening),
            patch("py.api.images.header_params", side_effect=parsing),
        ):
            _, data = await self.get()
        self.assertEqual(data["images"][0]["seed"], "5")
        self.assertEqual(closed, [True])

    def _seed_files(self):
        """Concept-style one-sampler files plus every shared parser case."""
        expected = {}
        for i in range(2):
            for j in range(3):
                name = f"concept-{i}-{j}.png"
                seed = 42001 + i * 100 + j
                records_png(
                    self.output_dir / name, seeded_records(f"concept {i} {j}", seed)
                )
                expected[name] = str(seed)
        # ComfyUI's randomized seeds mostly exceed 2**53, where JSON.parse rounds.
        big = 2**63 + 12345
        records_png(self.output_dir / "big-seed.png", seeded_records("big", big))
        expected["big-seed.png"] = str(big)
        for case, records in CASES.items():
            records_png(self.output_dir / f"case-{case}.png", records)
        records_png(
            self.output_dir / "a1111.png",
            {"parameters": "a fox\nNegative prompt: blur\nSteps: 20, Seed: 9"},
        )
        self.media("bare.png")
        return expected

    async def _inspector_seeds(self, items):
        """The seed the inspector shows for each listed item (Node harness)."""
        return (await self._node_seeds(items))["inspector"]

    async def _node_seeds(self, items, listing=None):
        """The harness output: inspector seeds and, from *listing*, card seeds.

        *listing* is the exact listing response text; the harness JSON.parses
        it as the browser does, so the card seeds are what a page would read.
        """
        node = shutil.which("node")
        if node is None:  # pragma: no cover - CI runners and dev hosts ship Node
            if os.environ.get("CI"):
                self.fail("Node.js is required in CI for the seed parity check")
            self.skipTest("Node.js is not installed")
        bodies = []
        for item in items:
            response = await self.client.get(
                f"/prompt_manager/images/inspect/{item['path']}",
                params={"root": 0, "root_scope": item["root_scope"]},
            )
            self.assertEqual(response.status, 200, item["path"])
            bodies.append({"name": item["path"], "body": await response.text()})
        # Fixed local Node harness, no shell.
        completed = subprocess.run(  # nosec B603
            [node, str(SEED_HARNESS)],
            input=json.dumps({"listing": listing, "inspections": bodies}).encode(
                "utf-8"
            ),
            capture_output=True,
            timeout=NODE_TIMEOUT_SECONDS,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8"))
        return json.loads(completed.stdout)

    async def test_card_seed_equals_inspector_seed_when_both_present(self):
        expected = self._seed_files()
        response = await self.client.get(
            "/prompt_manager/images/output",
            params={"mode": "workspace", "limit": 200},
        )
        listing = await response.text()
        items = json.loads(listing)["images"]
        self.assertEqual(len(items), len(CASES) + len(expected) + 2)
        seeds = await self._node_seeds(items, listing)
        inspector, browser = seeds["inspector"], seeds["card"]
        both = 0
        for item in items:
            with self.subTest(file=item["path"]):
                card, shown = item["seed"], inspector[item["path"]]
                # Sent as a decimal string, so JSON.parse keeps every digit.
                self.assertTrue(card is None or isinstance(card, str), card)
                self.assertEqual(browser[item["path"]], card)
                if item["path"] in expected:
                    self.assertEqual(card, expected[item["path"]])
                if card is None:
                    continue
                # The card never shows a seed the inspector does not.
                self.assertEqual(shown, {"status": "known", "value": card})
                both += 1
        self.assertGreaterEqual(both, len(expected) + 10)
        self.assertGreater(int(browser["big-seed.png"]), 2**53)

    async def test_text_title_needs_the_files_parsed_positive(self):
        prompt = self._save_prompt("Saved words\nrest")
        other = records_png(
            self.output_dir / "other.png", seeded_records("Different words", 1)
        )
        shared = records_png(
            self.output_dir / "shared.png", CASES["shared-prompt-pipeline"]
        )
        clip = self.media("clip.mp4")
        shared_prompt = self._save_prompt(
            extract_from_records(CASES["shared-prompt-pipeline"]["prompt"], None)[
                "positivePrompt"
            ]
        )
        for path, owner in ((other, prompt), (shared, shared_prompt), (clip, prompt)):
            self.api.db.link_image_to_prompt(owner, str(path))
        _, data = await self.get()
        items = {item["filename"]: item for item in data["images"]}
        for name in ("other.png", "clip.mp4"):
            with self.subTest(file=name):
                self.assertEqual(items[name]["linked"]["status"], "linked")
                self.assertEqual(items[name]["linked"]["title_source"], "text")
                self.assertIsNone(items[name]["linked"]["title"])
        library = await self.inspected("other.png", items["other.png"])
        self.assertIsNone(library["title"])
        self.assertNotIn("Saved words", json.dumps(data))
        # The resolver cannot decide this graph; the shared parser (and so the
        # inspector's prompt) confirms the saved first line on both.
        title = items["shared.png"]["linked"]["title"]
        self.assertIsNotNone(title)
        self.assertEqual(
            (await self.inspected("shared.png", items["shared.png"]))["title"], title
        )

    # B3: search, category/tag filters and rating sort over linked positives.

    def _names(self, data):
        return [item["filename"] for item in data["images"]]

    def _filter_fixtures(self):
        """Linked, pair-linked, ambiguous and unlinked files for the B3 filters."""
        db = self.api.db
        night_ab = self._save_prompt(
            "A ZEBRA under neon", category="Night", tags=["a", "b"], rating=5
        )
        night_a = self._save_prompt("Quiet harbor", category="Night", tags=["a"])
        day_ab = self._save_prompt(
            "Morning field", category="Day", tags=["a", "b", "zebra-tag"], rating=3
        )
        titled = self._save_prompt("Plain words", category="Day", rating=1)
        with db.model.get_connection() as connection:
            connection.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (titled, "Zebra stored title"),
            )
        percent = self._save_prompt("100% wild_card", category="Day")
        pair_positive = self._save_prompt("Pair positive", category="Night", rating=4)
        pair = db.save_prompt_pair("Pair positive", "Noise", title="Zebra pair")
        owners = (
            self._save_prompt("Zebra one", category="Night"),
            self._save_prompt("Zebra two", category="Night"),
        )
        files = {
            name: self.media(f"{name}.png", mtime=100 + index)
            for index, name in enumerate(
                (
                    "night_ab",
                    "night_a",
                    "day_ab",
                    "titled",
                    "percent",
                    "paired",
                    "ambiguous",
                    "zebra_unlinked",
                    "plain_unlinked",
                )
            )
        }
        db.link_image_to_prompt(night_ab, str(files["night_ab"]))
        # A non-normalized spelling of the same file still compares equal.
        db.link_image_to_prompt(
            night_a, str(self.output_dir / "sub" / ".." / "night_a.png")
        )
        db.link_image_to_prompt(day_ab, str(files["day_ab"]))
        db.link_image_to_prompt(titled, str(files["titled"]))
        db.link_image_to_prompt(percent, str(files["percent"]))
        self.assertEqual(db.get_prompt_pair(pair)["positive_prompt_id"], pair_positive)
        image = db.link_image_to_prompt(pair_positive, str(files["paired"]))
        db.link_image_to_pair(pair, image, {"source": "fixture"})
        for owner in owners:
            db.link_image_to_prompt(owner, str(files["ambiguous"]))
        return files

    async def test_bulk_category_change_shows_on_the_next_listing(self):
        files = self._filter_fixtures()
        db = self.api.db
        night_a = db.get_prompt_id_for_image(str(files["night_a"]))
        # Warm every listing cache (scan, orders, card facts, link reads).
        for params in ({}, {"category": "Landscapes"}, {"category": "Night"}):
            response, data = await self.get(**params)
            self.assertEqual(response.status, 200, data)
        self.assertEqual(data["total"], 3)
        response = await self.client.post(
            "/prompt_manager/bulk/category",
            json={"prompt_ids": [night_a], "category": "Landscapes"},
        )
        self.assertEqual(response.status, 200, await response.json())
        _, moved = await self.get(category="Landscapes")
        self.assertEqual(self._names(moved), ["night_a.png"])
        self.assertEqual(moved["images"][0]["linked"]["category"], "Landscapes")
        _, night = await self.get(category="Night")
        self.assertEqual(night["total"], 2)
        self.assertNotIn("night_a.png", self._names(night))

    async def test_positive_count_counts_distinct_linked_positives(self):
        files = self._filter_fixtures()
        db = self.api.db
        night_ab = db.get_prompt_id_for_image(str(files["night_ab"]))
        # A second file of one positive still counts that positive once.
        again = self.media("again.png", mtime=50)
        db.link_image_to_prompt(night_ab, str(again))
        cases = (
            # Six unambiguous positives; the ambiguous file and unlinked count none.
            ({}, 10, 6),
            # Text, stored title, tag and the filename match (never a pair's
            # own variant title, D12).
            ({"q": "ZEBRA"}, 5, 3),
            ({"category": "Night"}, 4, 3),
            ({"tags": "a,b"}, 3, 2),
            ({"q": "plain_unlinked"}, 1, 0),
            ({"media": "video"}, 0, 0),
            ({"sort": "rating"}, 10, 6),
            ({"sort": "name", "category": "Day"}, 3, 3),
        )
        for params, total, positives in cases:
            with self.subTest(**params):
                response, data = await self.get(**params)
                self.assertEqual(response.status, 200, data)
                self.assertEqual(data["total"], total)
                self.assertEqual(data["positive_count"], positives)
        # The count covers the whole matching set. A filter or the rating
        # sort reads the links on every page, so its cursor pages carry it too.
        for params in ({"q": "ZEBRA"}, {"sort": "rating"}):
            with self.subTest(cursor=params):
                response, first = await self.get(limit=2, **params)
                response, second = await self.get(
                    limit=2, cursor=first["next_cursor"], **params
                )
                self.assertEqual(response.status, 200, second)
                self.assertEqual(second["positive_count"], first["positive_count"])
        # A plain cursor page skips the read and omits the key (absent, not
        # null): the frontend keeps the first page's value.
        for params in ({}, {"sort": "oldest"}, {"sort": "name"}):
            with self.subTest(plain_cursor=params):
                response, first = await self.get(limit=2, **params)
                self.assertEqual(first["positive_count"], 6)
                response, second = await self.get(
                    limit=2, cursor=first["next_cursor"], **params
                )
                self.assertEqual(response.status, 200, second)
                self.assertNotIn("positive_count", second)
                self.assertEqual(second["total"], 10)
        # One SELECT counts the positives; the page's link summary is the other.
        statements = []
        original = db.model.get_connection

        def trace_connection():
            connection = original()
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(db.model, "get_connection", side_effect=trace_connection):
            response, data = await self.get()
        self.assertEqual(response.status, 200)
        selects = [
            query
            for query in statements
            if query.lstrip().upper().startswith(("SELECT", "WITH"))
        ]
        self.assertEqual(len(selects), 3, selects)
        self.assertEqual(sum("image_favorites" in q for q in selects), 1, selects)
        _, first = await self.get(limit=2)
        statements.clear()
        with patch.object(db.model, "get_connection", side_effect=trace_connection):
            response, data = await self.get(limit=2, cursor=first["next_cursor"])
        self.assertEqual(response.status, 200)
        selects = [
            query
            for query in statements
            if query.lstrip().upper().startswith(("SELECT", "WITH"))
        ]
        # The page's link summary and its favorites read only.
        self.assertEqual(len(selects), 2, selects)
        self.assertEqual(sum("image_favorites" in q for q in selects), 1, selects)
        self.assertNotIn("gi.file_path, p.rating", selects[0])
        # Without filters the listing outlives a database failure; the count
        # is then unknown rather than wrong.
        with patch.object(
            db, "get_image_link_matches", side_effect=RuntimeError("database gone")
        ):
            response, data = await self.get()
        self.assertEqual(response.status, 200, data)
        self.assertEqual(data["total"], 10)
        self.assertIsNone(data["positive_count"])
        self.assertNotIn("database gone", json.dumps(data))
        # A linked filter or the rating sort needs the read, so its failure is
        # an error (filename-only search survives it, see below).
        for params in (
            {"category": "Night"},
            {"tags": "a"},
            {"q": "zebra", "category": "Night"},
            {"sort": "rating"},
        ):
            leak = 'near "gi": syntax error FROM generation_images'
            with (
                self.subTest(failing=params),
                patch.object(
                    db, "get_image_link_matches", side_effect=RuntimeError(leak)
                ),
                self.assertLogs("prompt_manager", level="ERROR") as logged,
            ):
                response, data = await self.get(**params)
                self.assertEqual(response.status, 500, data)
                self.assertNotIn("images", data)
                self.assertEqual(data["code"], "listing-failed")
                self.assertNotIn(leak, json.dumps(data))
                self.assertNotIn("syntax error", json.dumps(data))
                self.assertTrue(any(r.exc_info for r in logged.records))

    async def test_filename_search_survives_a_database_failure(self):
        for name, mtime in (("zebra-1.png", 300), ("zebra-2.png", 200)):
            self.media(name, mtime)
        self.media("other.png", 100)
        with (
            patch.object(
                self.api.db,
                "get_image_link_matches",
                side_effect=RuntimeError("database gone"),
            ),
            self.assertLogs(self.api.logger, "WARNING") as logged,
        ):
            response, first = await self.get(q="ZEBRA", limit=1)
            self.assertEqual(response.status, 200, first)
            response, second = await self.get(
                q="ZEBRA", limit=1, cursor=first["next_cursor"]
            )
        self.assertEqual(response.status, 200, second)
        # Filename matches only; the count of linked positives is unknown.
        self.assertEqual(self._names(first), ["zebra-1.png"])
        self.assertEqual(self._names(second), ["zebra-2.png"])
        for page in (first, second):
            self.assertEqual(page["total"], 2)
            self.assertIsNone(page["positive_count"])
            self.assertNotIn("database gone", json.dumps(page))
        self.assertTrue(
            all(record.exc_info for record in logged.records), logged.output
        )

    async def test_search_matches_filename_prompt_text_and_tags(self):
        from database.models import normalize_image_path

        files = self._filter_fixtures()
        _, data = await self.get(q="ZEBRA", sort="name")
        # Text, stored title, tag and filename; never an ambiguous link.
        self.assertEqual(
            self._names(data),
            [
                "day_ab.png",
                "night_ab.png",
                "titled.png",
                "zebra_unlinked.png",
            ],
        )
        self.assertEqual(data["total"], 4)
        # D12: a pair's own title names its negative variant, so the variant
        # name alone matches nothing; the linked positive's text still does.
        _, data = await self.get(q="Zebra pair")
        self.assertEqual(self._names(data), [])
        self.assertEqual(data["total"], 0)
        _, data = await self.get(q="pair positive")
        self.assertEqual(self._names(data), ["paired.png"])
        _, data = await self.get(q="0% w")
        self.assertEqual(self._names(data), ["percent.png"])
        _, data = await self.get(q="%")
        self.assertEqual(self._names(data), ["percent.png"])
        _, data = await self.get(q="harbor")
        self.assertEqual(self._names(data), ["night_a.png"])
        self.assertEqual(
            self.api.db.get_image_paths_matching("zebra", []),
            {
                normalize_image_path(str(files[name]))
                for name in ("night_ab", "day_ab", "titled")
            },
        )

    async def test_category_and_tags_filter_linked_images_only(self):
        self._filter_fixtures()
        cases = (
            ({"category": "Night"}, ["night_ab.png", "night_a.png", "paired.png"]),
            ({"category": "night"}, []),
            ({"tags": "a,b"}, ["night_ab.png", "day_ab.png"]),
            ({"tags": " a , b ,"}, ["night_ab.png", "day_ab.png"]),
            ({"category": "Night", "tags": "a,b"}, ["night_ab.png"]),
            ({"category": "Night", "q": "harbor"}, ["night_a.png"]),
            ({"category": "Night", "q": "paired"}, ["paired.png"]),
            ({"category": "Day", "q": "zebra"}, ["day_ab.png", "titled.png"]),
            ({"category": ""}, None),
            ({"category": "  Night "}, ["night_ab.png", "night_a.png", "paired.png"]),
            ({"category": "   "}, None),
        )
        _, everything = await self.get(sort="name")
        for params, expected in cases:
            with self.subTest(**params):
                response, data = await self.get(sort="name", **params)
                self.assertEqual(response.status, 200, data)
                if expected is None:
                    self.assertEqual(data["images"], everything["images"])
                else:
                    self.assertEqual(self._names(data), sorted(expected))
                    self.assertEqual(data["total"], len(expected))
        for params in (
            {"category": "x" * 201},
            {"tags": ",".join(f"t{i}" for i in range(31))},
            {"tags": "x" * 201},
        ):
            response, data = await self.get(**params)
            self.assertEqual(response.status, 400, params)
        # The filters read one SELECT besides the page's link summary.
        statements = []
        original = self.api.db.model.get_connection

        def trace_connection():
            connection = original()
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(
            self.api.db.model, "get_connection", side_effect=trace_connection
        ):
            response, _ = await self.get(
                category="Night", tags="a", q="zebra", sort="rating"
            )
        self.assertEqual(response.status, 200)
        selects = [
            query
            for query in statements
            if query.lstrip().upper().startswith(("SELECT", "WITH"))
        ]
        # Plus the one stream of tagged image records q and tags read (B3),
        # and the category's own-category read of the one source (#332).
        self.assertEqual(len(selects), 5, selects)
        self.assertEqual(sum("image_categories" in q for q in selects), 1, selects)
        self.assertEqual(sum("image_favorites" in q for q in selects), 1, selects)
        self.assertEqual(sum("media_image_tags" in q for q in selects), 1, selects)

    async def test_rating_sort_orders_linked_then_unlinked(self):
        self._filter_fixtures()
        response, data = await self.get(sort="rating")
        self.assertEqual(response.status, 200, data)
        self.assertEqual(
            self._names(data),
            [
                "night_ab.png",  # 5
                "paired.png",  # 4, the pair's positive
                "day_ab.png",  # 3
                "titled.png",  # 1
                # linked without a rating, newest first
                "percent.png",
                "night_a.png",
                # unlinked (and ambiguous) last, newest first
                "plain_unlinked.png",
                "zebra_unlinked.png",
                "ambiguous.png",
            ],
        )
        _, first = await self.get(sort="rating", limit=4)
        _, rest = await self.get(sort="rating", limit=10, cursor=first["next_cursor"])
        self.assertEqual(first["images"] + rest["images"], data["images"])
        _, night = await self.get(sort="rating", category="Night")
        self.assertEqual(
            self._names(night), ["night_ab.png", "paired.png", "night_a.png"]
        )

    async def test_cursor_rejects_changed_filters(self):
        self._filter_fixtures()
        _, first = await self.get(category="Night", limit=1)
        token = first["next_cursor"]
        self.assertIsInstance(token, str)
        response, _ = await self.get(category="Night", limit=1, cursor=token)
        self.assertEqual(response.status, 200)
        for params in (
            {"category": "Day"},
            {},
            {"category": "Night", "tags": "a"},
            {"category": "Night", "q": "zebra"},
            {"category": "Night", "sort": "rating"},
        ):
            with self.subTest(**params):
                response, _ = await self.get(cursor=token, **params)
                self.assertEqual(response.status, 400)
        _, rated = await self.get(sort="rating", limit=1)
        response, _ = await self.get(sort="rating", cursor=rated["next_cursor"])
        self.assertEqual(response.status, 200)
        response, _ = await self.get(sort="newest", cursor=rated["next_cursor"])
        self.assertEqual(response.status, 400)
        _, tagged = await self.get(tags="a,b", limit=1)
        response, _ = await self.get(tags="b,a", cursor=tagged["next_cursor"])
        self.assertEqual(response.status, 200)
        response, _ = await self.get(tags="a", cursor=tagged["next_cursor"])
        self.assertEqual(response.status, 400)
        for params in ({"sort": "rated"}, {"sort": "rating-desc"}, {"media": "x"}):
            response, _ = await self.get(**params)
            self.assertEqual(response.status, 400, params)

    async def test_windows_style_link_paths_compare_normalized(self):
        import ntpath

        def windows_normalize(path):
            return ntpath.normcase(ntpath.normpath(path))

        prompt = self._save_prompt("Case words", category="Night", rating=2)
        listed = self.media("Sub/Night.PNG")
        self.media("Sub/other.png")
        stored = str(listed).upper().replace("/", "\\")
        with (
            patch("database.operations.normalize_image_path", windows_normalize),
            patch("py.api.images.normalize_image_path", windows_normalize),
        ):
            self.api.db.link_image_to_prompt(prompt, stored)
            _, data = await self.get(category="Night")
            self.assertEqual(self._names(data), ["Night.PNG"])
            _, data = await self.get(q="case words")
            self.assertEqual(self._names(data), ["Night.PNG"])
            _, data = await self.get(sort="rating")
            self.assertEqual(self._names(data), ["Night.PNG", "other.png"])

    def _unlisted_link_rows(self, count):
        """*count* linked rows for files outside the listing, written in bulk."""
        prompt = self._save_prompt("Elsewhere", category="Night", rating=5)
        with self.api.db.model.get_connection() as conn:
            conn.executemany(
                "INSERT INTO generated_images(prompt_id,image_path,filename,file_path)"
                " VALUES(?,?,?,?)",
                (
                    (prompt, f"/elsewhere/{i}.png", f"{i}.png", f"/elsewhere/{i}.png")
                    for i in range(count)
                ),
            )

    async def test_rating_sort_reads_only_listed_paths_past_the_old_cap(self):
        from database.models import normalize_image_path

        files = self._filter_fixtures()
        # More rows than the former 500,000-row cap, none of them listed.
        await asyncio.get_running_loop().run_in_executor(
            None, self._unlisted_link_rows, 500_001
        )
        response, data = await self.get(sort="rating", limit=3)
        self.assertEqual(response.status, 200, data)
        self.assertEqual(
            self._names(data), ["night_ab.png", "paired.png", "day_ab.png"]
        )
        response, data = await self.get(category="Night", sort="name")
        self.assertEqual(response.status, 200, data)
        self.assertEqual(
            self._names(data), ["night_a.png", "night_ab.png", "paired.png"]
        )
        wanted = {normalize_image_path(str(files["night_ab"])), "/elsewhere/7.png"}
        elsewhere = self.api.db.get_prompt_id_for_image("/elsewhere/7.png")
        self.assertEqual(
            self.api.db.get_image_link_matches(
                "", [], every_link=True, wanted=wanted, category="Night"
            ),
            {
                normalize_image_path(str(files["night_ab"])): (
                    True,
                    5,
                    self.api.db.get_prompt_id_for_image(str(files["night_ab"])),
                    "Night",
                ),
                "/elsewhere/7.png": (True, 5, elsewhere, "Night"),
            },
        )

    def _budget_fixture(self, files, links):
        """*files* listed paths and *links* linked rows, written in bulk."""
        from database.models import normalize_image_path

        listed = [
            (self.output_dir / f"day-{i % 50:02}" / f"img_{i:05}.png", 1_000.0 + i)
            for i in range(files)
        ]
        prompts = min(2_000, links)
        with self.api.db.model.get_connection() as conn:
            conn.executemany(
                "INSERT INTO tags(id,name) VALUES(?,?)",
                [(i + 1, f"tag-{i}") for i in range(20)],
            )
            conn.executemany(
                "INSERT INTO prompts(id,text,category,rating,hash) VALUES(?,?,?,?,?)",
                [
                    (
                        i + 1,
                        f"prompt {i} " + "lantern " * (i % 7) + "harbor " * 20,
                        f"cat-{i % 10}",
                        (i % 6) or None,
                        f"hash-{i}",
                    )
                    for i in range(prompts)
                ],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO prompt_tags(prompt_id,tag_id) VALUES(?,?)",
                [(i + 1, t) for i in range(prompts) for t in (i % 20 + 1, 1)],
            )
            conn.executemany(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                [(i + 1, f"Title {i}") for i in range(0, prompts, 3)],
            )
            conn.executemany(
                "INSERT INTO generated_images(prompt_id,image_path,filename,file_path)"
                " VALUES(?,?,?,?)",
                [
                    (
                        i % prompts + 1,
                        str(listed[i * 2][0]),
                        listed[i * 2][0].name,
                        normalize_image_path(str(listed[i * 2][0])),
                    )
                    for i in range(links)
                ],
            )
        # 2,000 images (one in 25 at 50,000 files) carry their own category
        # (#332), so a category filter also reads and matches those.
        from py.api.images import _favorite_root_key

        root_key = _favorite_root_key(self.output_dir)
        with self.api.db.model.get_connection() as conn:
            conn.executemany(
                "INSERT INTO image_categories VALUES(?,?,?,?)",
                [
                    (
                        root_key,
                        listed[i][0].relative_to(self.output_dir).as_posix(),
                        f"cat-{i % 10}",
                        "2026-10-27T00:00:00+00:00",
                    )
                    for i in range(1, files, 25)[:2_000]
                ],
            )
        return listed

    _BUDGET_REQUESTS = (
        # First, so the timed loop also measures a cursor page of each.
        {},
        {"sort": "oldest"},
        {"q": "lantern"},
        {"q": "img_0123"},
        {"q": "Title 1"},
        {"category": "cat-3"},
        {"tags": "tag-0,tag-2"},
        {"sort": "rating"},
        {"sort": "rating", "category": "cat-4"},
        {"q": "harbor", "category": "cat-5", "tags": "tag-5"},
        {"sort": "name", "q": "tag-7"},
        {"subfolder": "day-07", "q": "lantern"},
    )

    @staticmethod
    def _stub_page(page):
        return [{"filename": entry[0].name} for entry in page]

    async def test_filter_work_is_bounded(self):
        """The budget's structure, counted rather than timed (runs everywhere)."""
        import math

        from py.api import images as images_module
        from database.operations import link_filter_query, link_matches_json_query

        listed = await asyncio.get_running_loop().run_in_executor(
            None, self._budget_fixture, 5_000, 2_000
        )
        scans = [listed]
        built, keyed = [], []
        real_listed, real_key = images_module._listed, images_module._workspace_key

        def counting_listed(*args):
            built.append(args[0])
            return real_listed(*args)

        def counting_key(entry, order):
            keyed.append(order)
            return real_key(entry, order)

        statements = []
        original = self.api.db.model.get_connection

        def trace_connection():
            connection = original()
            connection.set_trace_callback(statements.append)
            return connection

        async def scan(_root):
            return scans[-1]

        def selects():
            found = [
                query
                for query in statements
                if query.lstrip().upper().startswith(("SELECT", "WITH"))
            ]
            statements.clear()
            return found

        with (
            patch.object(self.api, "_get_gallery_files", side_effect=scan),
            patch.object(self.api, "_format_workspace_page", self._stub_page),
            patch.object(images_module, "_listed", counting_listed),
            patch.object(images_module, "_workspace_key", counting_key),
            patch.object(
                self.api.db.model, "get_connection", side_effect=trace_connection
            ),
        ):
            response, _ = await self.get(limit=10)
            self.assertEqual(response.status, 200)
            self.assertEqual(len(built), len(listed))
            # No filter still reads the linked positives once, for the count.
            self.assertEqual(len(selects()), 1)
            newest = self.api._workspace_order_cache[((0, str(self.output_dir)),)]
            newest = newest[1]["newest"]
            for params in self._BUDGET_REQUESTS:
                with self.subTest(**params):
                    built.clear()
                    response, data = await self.get(limit=10, **params)
                    self.assertEqual(response.status, 200, data)
                    self.assertEqual(built, [])
                    found = selects()
                    # q and tags also stream the tagged image records once
                    # (#249 B3); none are tagged here, so nothing is stat'ed.
                    streamed = "q" in params or "tags" in params
                    # A category also reads the own categories, one primary
                    # key range per source (#332).
                    own = "category" in params
                    self.assertEqual(len(found), 1 + streamed + own, found)
                    self.assertEqual(
                        sum("image_categories" in query for query in found), int(own)
                    )
                    self.assertEqual(
                        sum("media_image_tags" in query for query in found),
                        int(streamed),
                    )
            self.assertIs(
                self.api._workspace_order_cache[((0, str(self.output_dir)),)][1][
                    "newest"
                ],
                newest,
            )
            bound = math.ceil(math.log2(len(listed))) + 3
            for order in ("newest", "name", "name-desc", "oldest", "rating"):
                with self.subTest(cursor=order):
                    await self.get(sort=order, limit=10)  # builds a lazy order
                    _, first = await self.get(sort=order, limit=10)
                    keyed.clear()
                    selects()
                    response, page = await self.get(
                        sort=order, limit=10, cursor=first["next_cursor"]
                    )
                    self.assertEqual(response.status, 200)
                    self.assertEqual(len(page["images"]), 10)
                    self.assertLessEqual(len(keyed), bound)
                    # A plain cursor page reads no links; rating needs them.
                    plain = order != "rating"
                    self.assertEqual(len(selects()), 0 if plain else 1)
                    self.assertEqual("positive_count" in page, not plain)
            key = ((0, str(self.output_dir)),)
            # A TTL rescan that found the same files rebuilds nothing.
            built.clear()
            scans.append(list(listed))
            response, data = await self.get(limit=10)
            self.assertEqual(response.status, 200)
            self.assertEqual(built, [])
            self.assertIs(self.api._workspace_order_cache[key][1]["newest"], newest)
            # One changed file is the only one rebuilt.
            added = (self.output_dir / "day-00" / "new.png", 9_999_999.0)
            scans.append([added, *listed[1:]])
            response, data = await self.get(limit=10)
            self.assertEqual(built, [added[0]])
            self.assertEqual(data["total"], len(listed))
            self.assertEqual(data["images"][0]["filename"], "new.png")
            # The monitor invalidates the gallery when it sees a new file: the
            # next request rebuilds only that file's values, and search finds it.
            self.api.invalidate_gallery_cache()
            self.assertNotEqual(self.api._workspace_listed_cache, {})
            monitored = (self.output_dir / "day-01" / "monitored.png", 9_999_999.5)
            scans.append([monitored, *scans[-1]])
            built.clear()
            response, data = await self.get(limit=10, q="monitored")
            self.assertEqual(response.status, 200)
            self.assertEqual(built, [monitored[0]])
            self.assertEqual(self._names(data), ["monitored.png"])
            # A roots change drops every derived value.
            with patch("py.config.GalleryConfig.MONITORING_DIRECTORIES", []):
                self.assertTrue(self.api._apply_gallery_roots([str(self.output_dir)]))
            self.assertEqual(self.api._workspace_listed_cache, {})
            self.assertEqual(self.api._workspace_order_cache, {})
        # The per-row ambiguity probe is an index search, never a scan.
        # The read runs it wrapped in one JSON aggregate (#354): same plan.
        sql, params = link_filter_query("lantern", ["tag-1"], every_link=True)
        for query in (sql, link_matches_json_query(sql)):
            with self.subTest(wrapped=query is not sql):
                with self.api.db.model.get_connection() as conn:
                    plan = [
                        row[3]
                        for row in conn.execute("EXPLAIN QUERY PLAN " + query, params)
                    ]
                self.assertIn(
                    "SEARCH other USING COVERING INDEX idx_image_file_path "
                    "(file_path=?)",
                    plan,
                )
                self.assertFalse(
                    [step for step in plan if step.startswith("SCAN other")]
                )
        # The category's narrowing clause (#332) keeps every join an index
        # search, plain and wrapped with the raw categories.
        sql, params = link_filter_query(
            "", ["tag-1"], every_link=True, category="cat-3"
        )
        for query in (sql, link_matches_json_query(sql, categories=True)):
            with self.subTest(category_wrapped=query is not sql):
                with self.api.db.model.get_connection() as conn:
                    plan = [
                        row[3]
                        for row in conn.execute("EXPLAIN QUERY PLAN " + query, params)
                    ]
                self.assertIn(
                    "SEARCH other USING COVERING INDEX idx_image_file_path "
                    "(file_path=?)",
                    plan,
                )
                scans = [step for step in plan if step.startswith("SCAN")]
                self.assertTrue(all(step.startswith("SCAN gi") for step in scans), plan)

    @unittest.skipUnless(
        PERF_BUDGET and not TRACED,
        "wall-clock budget: set PROMPTMANAGER_PERF_BUDGET=1 and run untraced",
    )
    async def test_filter_budget(self):
        """p95 < 150 ms at 50,000 files / 20,000 links (TESTING.md budgets)."""
        import math
        import time

        # Built off the event loop: the bulk inserts take a few hundred ms.
        listed = await asyncio.get_running_loop().run_in_executor(
            None, self._budget_fixture, 50_000, 20_000
        )
        rounds, plain = [], {"first": [], "cursor": []}
        with (
            patch.object(
                self.api, "_get_gallery_files", AsyncMock(return_value=listed)
            ),
            patch.object(self.api, "_format_workspace_page", self._stub_page),
        ):
            # The first request after a scan builds the cached sort orders.
            started = time.perf_counter()
            response, _ = await self.get(sort="rating")
            cold = time.perf_counter() - started
            self.assertEqual(response.status, 200)
            for _round in range(3):
                timings = []
                for index in range(20):
                    requests = self._BUDGET_REQUESTS
                    shape = requests[index % len(requests)]
                    params = dict(shape, limit=100)
                    if index >= len(requests):
                        _, first = await self.get(**params)
                        if first["next_cursor"]:
                            params["cursor"] = first["next_cursor"]
                    started = time.perf_counter()
                    response, data = await self.get(**params)
                    timings.append(time.perf_counter() - started)
                    if shape.get("sort", "newest") in ("newest", "oldest") and not (
                        set(shape) - {"sort"}
                    ):
                        # Plain first pages read the links for the count;
                        # plain cursor pages read none.
                        plain["cursor" if "cursor" in params else "first"].append(
                            timings[-1]
                        )
                    self.assertEqual(response.status, 200, (params, data))
                timings.sort()
                rounds.append(timings)
        p95s = [timings[math.ceil(0.95 * len(timings)) - 1] for timings in rounds]
        best = min(p95s)
        plain_ms = {
            kind: sorted(round(t * 1000, 1) for t in found)
            for kind, found in plain.items()
        }
        print(
            "\nworkspace filter budget: best p95="
            f"{best * 1000:.1f} ms, rounds="
            f"{[round(p * 1000, 1) for p in p95s]} ms, cold={cold * 1000:.1f} ms, "
            f"plain listing ms={plain_ms}",
            file=sys.stderr,
        )
        self.assertLess(
            best, 0.150, [[round(t * 1000, 1) for t in timings] for timings in rounds]
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
                token = _workspace_cursor(key, "scope", b"k" * 32)
                self.assertLessEqual(len(token), 4096)
                self.assertEqual(
                    _workspace_cursor_key(token, "scope", order, b"k" * 32), key
                )
                with self.assertRaises(ValueError):
                    _workspace_cursor_key(token, "scope", order, b"o" * 32)


# The pre-#379 candidate read, kept as the differential reference for the
# indexed lookup (`image_tag_ids` + `image_tag_locators`): every byte record
# streamed with its tag names, a locator kept when any record's names are
# relevant (`_image_tags_relevant`).
_REFERENCE_RECORDS = (
    "SELECT f.locator,b.id,t.name FROM media_annotation_files f "
    "JOIN media_annotation_bytes b ON b.file_id=f.id "
    "LEFT JOIN media_image_tags mit ON mit.byte_id=b.id "
    "LEFT JOIN tags t ON t.id=mit.tag_id "
    "ORDER BY f.locator,b.first_seen_at,b.id,mit.position"
)


def _image_tags_relevant(names, wanted, query):
    if wanted and not wanted.isdisjoint(names):
        return True
    return bool(query) and any(query in name.casefold() for name in names)


def reference_candidates(db, wanted, query):
    """Locators the old full stream admitted for (``wanted``, ``query``)."""
    records = {}
    for locator, byte_id, name in db.model.get_connection().execute(_REFERENCE_RECORDS):
        names = records.setdefault((locator, byte_id), [])
        if name is not None:
            names.append(name)
    return {
        locator
        for (locator, _byte), names in records.items()
        if _image_tags_relevant(names, set(wanted), query)
    }


class TestWorkspaceImageTags(ImageAPITestCase):
    """B3: image tags in the Tags filter and search (spec amendment S1)."""

    media = TestWorkspaceImages.media
    get = TestWorkspaceImages.get

    async def tearDownAsync(self):
        self.api.db.close_all()
        assert_closed(self, self._temp_db.name, self._output_tmp.name)
        await super().tearDownAsync()

    def tag(self, path, tags):
        """Save *tags* for the current bytes of *path*, as the Tag dialog does."""
        import hashlib

        stat = path.stat()
        return self.api.db.replace_image_tags(
            locator=os.path.normcase(os.path.realpath(path)),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            expected_epoch=self.api.db.get_execution_epoch(),
            expected_tag_revision=0,
            tags=tags,
        )

    def names(self, data):
        return [item["path"] for item in data["images"]]

    async def test_tags_filter_admits_unlinked_image_with_current_image_tags(self):
        tagged = self.media("unlinked_tagged.png", mtime=103)
        self.media("plain.png", mtime=102)
        linked = self.media("linked.png", mtime=101)
        self.api.db.link_image_to_prompt(
            self._save_prompt("Linked", tags=["other"]), str(linked)
        )
        self.tag(tagged, ["dunes", "warm_light"])
        for tags in ("dunes", "dunes,warm_light", "warm_light"):
            with self.subTest(tags=tags):
                response, body = await self.get(tags=tags)
                self.assertEqual(response.status, 200, body)
                self.assertEqual(
                    [i["path"] for i in body["images"]], ["unlinked_tagged.png"]
                )
                self.assertEqual(body["total"], 1)
                self.assertEqual(body["positive_count"], 0)
        # Exact names, as for prompt tags; a missing tag excludes the file.
        for tags in ("Dunes", "dunes,missing", "dune"):
            with self.subTest(tags=tags):
                _, body = await self.get(tags=tags)
                self.assertEqual(self.names(body), [])
        # A category filter reads only the linked positive.
        _, body = await self.get(tags="dunes", category="Night")
        self.assertEqual(self.names(body), [])

    async def test_tags_filter_combines_image_and_positive_tags(self):
        both = self._save_prompt("Both", category="Night", tags=["a", "b"], rating=2)
        only_a = self._save_prompt("Only a", category="Night", tags=["a"], rating=5)
        day_a = self._save_prompt("Day a", category="Day", tags=["a"], rating=4)
        files = {
            name: self.media(f"{name}.png", mtime=100 + index)
            for index, name in enumerate(
                ("positive_ab", "positive_a_image_b", "image_ab", "image_a", "day")
            )
        }
        self.api.db.link_image_to_prompt(both, str(files["positive_ab"]))
        self.api.db.link_image_to_prompt(only_a, str(files["positive_a_image_b"]))
        self.api.db.link_image_to_prompt(day_a, str(files["day"]))
        self.tag(files["positive_a_image_b"], ["b"])
        self.tag(files["image_ab"], ["a", "b"])
        self.tag(files["image_a"], ["a"])
        self.tag(files["day"], ["b"])
        _, body = await self.get(tags="a,b", sort="name")
        self.assertEqual(
            self.names(body),
            ["day.png", "image_ab.png", "positive_a_image_b.png", "positive_ab.png"],
        )
        self.assertEqual(body["total"], 4)
        self.assertEqual(body["positive_count"], 3)
        _, body = await self.get(tags="a,b", category="Night", sort="name")
        self.assertEqual(
            self.names(body), ["positive_a_image_b.png", "positive_ab.png"]
        )
        # The rating sort ranks an image-admitted linked file by its positive.
        _, body = await self.get(tags="a,b", sort="rating")
        self.assertEqual(
            self.names(body),
            ["positive_a_image_b.png", "day.png", "positive_ab.png", "image_ab.png"],
        )
        # With q, a linked positive hit admits as before.
        _, body = await self.get(tags="b", q="only", sort="name")
        self.assertEqual(self.names(body), ["positive_a_image_b.png"])

    async def test_q_matches_image_tag_names(self):
        tagged = self.media("unlinked_tagged.png", mtime=102)
        self.media("plain.png", mtime=101)
        self.tag(tagged, ["Sand Dunes", "warm_light"])
        for query in ("dune", "DUNES", "sand d", "warm_"):
            with self.subTest(q=query):
                response, body = await self.get(q=query)
                self.assertEqual(response.status, 200, body)
                self.assertEqual(self.names(body), ["unlinked_tagged.png"])
        _, body = await self.get(q="plain")
        self.assertEqual(self.names(body), ["plain.png"])
        _, body = await self.get(q="nothing")
        self.assertEqual(self.names(body), [])

    async def test_filter_ignores_tags_of_a_changed_fingerprint(self):
        moved = self.media("touched.png", mtime=102)
        grown = self.media("grown.png", mtime=101)
        kept = self.media("kept.png", mtime=100)
        for path in (moved, grown, kept):
            self.tag(path, ["dunes"])
        _, body = await self.get(tags="dunes")
        self.assertEqual(self.names(body), ["touched.png", "grown.png", "kept.png"])
        os.utime(moved, (500, 500))
        with grown.open("ab") as handle:
            handle.write(b"\0")
        os.utime(grown, (101, 101))
        self.api.invalidate_gallery_cache()
        _, body = await self.get(tags="dunes")
        self.assertEqual(self.names(body), ["kept.png"])
        _, body = await self.get(q="dunes")
        self.assertEqual(self.names(body), ["kept.png"])

    async def test_stat_calls_equal_tagged_candidates_not_listing_size(self):
        from py.api import images as images_module

        listed = [
            (self.output_dir / f"day-{i % 50:02}" / f"img_{i:05}.png", 1_000.0 + i)
            for i in range(1_997)
        ]
        tagged = [self.media(f"tagged_{i}.png", mtime=5_000 + i) for i in range(3)]
        listed += [(path, path.stat().st_mtime) for path in tagged]
        # Tagged but outside the listing, and listed with a non-matching tag.
        outside = self.media("elsewhere.png", root=self.comfy_dir)
        unrelated = self.media("unrelated.png", mtime=4_000)
        listed.append((unrelated, unrelated.stat().st_mtime))
        for path in tagged:
            self.tag(path, ["dunes"])
        self.tag(outside, ["dunes"])
        self.tag(unrelated, ["other"])
        self.assertEqual(len(listed), 2_001)
        stat_calls = 0
        real = getattr(images_module, "_current_fingerprint", None)
        self.assertIsNotNone(real, "the candidate stat helper is not implemented")

        def counting(path):
            nonlocal stat_calls
            stat_calls += 1
            return real(path)

        with (
            patch.object(
                self.api, "_get_gallery_files", AsyncMock(return_value=listed)
            ),
            patch.object(
                self.api, "_format_workspace_page", TestWorkspaceImages._stub_page
            ),
            patch.object(images_module, "_current_fingerprint", counting),
        ):
            response, body = await self.get(tags="dunes", limit=10)
        self.assertEqual(response.status, 200, body)
        self.assertEqual(
            sorted(i["filename"] for i in body["images"]),
            ["tagged_0.png", "tagged_1.png", "tagged_2.png"],
        )
        self.assertEqual(stat_calls, 3)

    async def test_plain_listing_reads_no_image_tags(self):
        prompt = self._save_prompt("Linked", category="Night", tags=["a"])
        for index in range(4):
            path = self.media(f"{index}.png", mtime=100 + index)
            self.tag(path, ["dunes"])
            self.api.db.link_image_to_prompt(prompt, str(path))
        statements = []
        original = self.api.db.model.get_connection

        def trace_connection():
            connection = original()
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(
            self.api.db.model, "get_connection", side_effect=trace_connection
        ):
            _, first = await self.get(limit=2)
            _, rest = await self.get(limit=2, cursor=first["next_cursor"])
            for params in (
                {"sort": "rating"},
                {"category": "Night"},
                {"media": "image"},
            ):
                response, _ = await self.get(**params)
                self.assertEqual(response.status, 200)
        self.assertEqual(len(first["images"] + rest["images"]), 4)
        self.assertFalse(
            [q for q in statements if "media_" in q or "annotation" in q], statements
        )

    async def test_image_tag_read_failure_fails_tags_and_degrades_search(self):
        tagged = self.media("unlinked_tagged.png", mtime=102)
        self.media("dunes_by_name.png", mtime=101)
        self.tag(tagged, ["dunes"])
        leak = "no such table: media_annotation_files"
        with patch.object(
            self.api.db,
            "image_tag_locators",
            side_effect=sqlite3.OperationalError(leak),
        ):
            # As a failed link read does under a Tags filter: a fixed coded
            # sentence, never the exception or SQL text; the traceback is logged.
            with self.assertLogs("prompt_manager", level="ERROR") as failed:
                response, body = await self.get(tags="dunes")
            self.assertEqual((response.status, body["success"]), (500, False))
            self.assertEqual(body["code"], "listing-failed")
            self.assertNotIn(leak, body["error"])
            self.assertNotIn("media_annotation_files", json.dumps(body))
            self.assertTrue(any(r.exc_info for r in failed.records))
            with self.assertLogs("prompt_manager", level="WARNING") as logged:
                response, body = await self.get(q="dunes")
        self.assertEqual(response.status, 200, body)
        self.assertEqual(self.names(body), ["dunes_by_name.png"])
        self.assertIn("image tags", "\n".join(logged.output))

    def seed_differential(self):
        """Mixed-case and underscore names, a stale record, an unlisted file."""
        a = self.media("a.png", mtime=101)
        b = self.media("b.png", mtime=102)
        c = self.media("c.png", mtime=103)
        self.tag(a, ["Beach", "long_hair", "1girl"])
        self.tag(b, ["beach", "waves"])
        self.tag(c, ["Sunset"])
        # c's bytes change: its old record (Sunset) is stale, the new one current.
        make_png(c, size=(5, 5))
        os.utime(c, (104, 104))
        self.tag(c, ["night_sky", "BEACH"])
        outside = Path(self._output_tmp.name) / "elsewhere" / "x.png"
        outside.parent.mkdir(parents=True, exist_ok=True)
        make_png(outside)
        self.tag(outside, ["beach", "outside_only"])

    async def test_indexed_candidates_equal_the_full_stream(self):
        self.seed_differential()
        cases = [
            (["beach"], ""),
            (["Beach"], ""),
            (["long_hair", "waves"], ""),
            ([], "beach"),
            ([], "hair"),
            ([], "_"),
            ([], "sun"),
            (["nope"], "zzz"),
            (["outside_only"], ""),
            (["beach"], "night"),
        ]
        db = self.api.db
        for wanted, query in cases:
            with self.subTest(wanted=wanted, query=query):
                indexed = set(db.image_tag_locators(db.image_tag_ids(wanted, query)))
                self.assertEqual(indexed, reference_candidates(db, wanted, query))
        # The API answers are the indexed lookup's: exact names under Tags, a
        # case-folded substring under q, current records only.
        _, body = await self.get(tags="beach")
        self.assertEqual(sorted(self.names(body)), ["b.png"])
        _, body = await self.get(q="beach")
        self.assertEqual(sorted(self.names(body)), ["a.png", "b.png", "c.png"])
        _, body = await self.get(q="sunset")
        self.assertEqual(self.names(body), [])

    async def test_tag_search_reads_image_tags_only_by_tag_id(self):
        self.seed_differential()
        statements = []
        original = self.api.db.model.get_connection

        def trace_connection():
            connection = original()
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(
            self.api.db.model, "get_connection", side_effect=trace_connection
        ):
            for params in ({"tags": "beach"}, {"q": "hair"}):
                response, _ = await self.get(**params)
                self.assertEqual(response.status, 200)
        reads = [
            sql
            for sql in statements
            if "media_image_tags" in sql and sql.lstrip().upper().startswith("SELECT")
        ]
        self.assertTrue(reads)
        for sql in reads:
            # Only a tag-id lookup, an EXISTS probe on the tag index, or the
            # current records' own tags (byte_id IN) read the junction.
            self.assertTrue(
                "tag_id IN" in sql or "EXISTS" in sql or "byte_id IN" in sql, sql
            )

    async def test_the_image_tag_lookup_never_reads_prompt_tags(self):
        self.seed_differential()
        linked = self.media("linked.png", mtime=105)
        prompt = self._save_prompt(
            "Prompt with its own tag", tags=["prompt_only_beach"]
        )
        self.api.db.link_image_to_prompt(prompt, str(linked))
        db = self.api.db
        # The tag is only on a prompt: the image-tag lookup never yields it,
        # under the Tags filter or the text search.
        self.assertEqual(db.image_tag_ids(["prompt_only_beach"], ""), [])
        self.assertEqual(db.image_tag_ids([], "prompt_only"), [])
        self.assertEqual(
            db.image_tag_locators(db.image_tag_ids([], "beach")),
            reference_candidates(db, [], "beach"),
        )
        # The search still matches through the linked positive's prompt tags
        # (the design keeps that, #379 "Behavior that does not change").
        _, body = await self.get(q="prompt_only")
        self.assertEqual(self.names(body), ["linked.png"])

    @unittest.skipUnless(os.environ.get("PM_PERF") == "1", "timing run (PM_PERF=1)")
    async def test_tag_search_timing(self):
        """Linux timing for the PR body: 23,600 records, 10 tags each."""
        import hashlib
        import statistics
        import time

        db = self.api.db
        epoch = db.get_execution_epoch()
        names = [f"name_{n:04d}" for n in range(2000)]
        root = os.path.realpath(self.output_dir)
        for n in range(23_600):
            path = self.output_dir / "perf" / f"{n:05d}.png"
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"x")
            os.utime(path, (n + 1, n + 1))
            stat = path.stat()
            db.replace_image_tags(
                locator=os.path.normcase(os.path.join(root, "perf", path.name)),
                sha256=hashlib.sha256(str(n).encode()).hexdigest(),
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                expected_epoch=epoch,
                expected_tag_revision=0,
                tags=[names[(n * 7 + k) % 2000] for k in range(10)],
            )
        await self.get(limit=1)  # the listing, warm
        for label, params in (
            ("one tag", {"tags": "name_0042"}),
            ("text", {"q": "e_1"}),
        ):
            times = []
            for _ in range(5):
                started = time.perf_counter()
                response, _ = await self.get(limit=100, **params)
                times.append(time.perf_counter() - started)
                self.assertEqual(response.status, 200)
            print(f"PM_PERF {label}: median {statistics.median(times) * 1000:.0f} ms")

    async def test_legacy_output_failure_logs_its_traceback(self):
        self.media("legacy.png", mtime=100)
        with (
            patch.object(
                self.api, "_collect_output_images", side_effect=RuntimeError("x")
            ),
            self.assertLogs("prompt_manager", level="ERROR") as logged,
        ):
            response = await self.client.get("/prompt_manager/images/output")
        self.assertEqual(response.status, 500)
        self.assertTrue(any(r.exc_info for r in logged.records), logged.output)

    async def test_legacy_gallery_and_search_shapes_unchanged(self):
        path = self.media("legacy.png", mtime=100)
        self.media("other.png", mtime=99)
        self._link_image(path, "dunes at dusk")

        async def legacy():
            bodies = []
            for url, params in (
                ("/prompt_manager/images/output", {}),
                ("/prompt_manager/images/search", {"q": "dunes"}),
                ("/prompt_manager/images/all", {}),
            ):
                response = await self.client.get(url, params=params)
                self.assertEqual(response.status, 200, url)
                bodies.append(await response.json())
            return bodies

        before = await legacy()
        self.tag(path, ["dunes", "warm_light"])
        self.tag(self.output_dir / "other.png", ["dunes"])
        self.api.invalidate_gallery_cache()
        self.assertEqual(await legacy(), before)

    def test_link_facts_resolve_pairs_skip_ambiguous_and_chunk(self):
        from database.models import normalize_image_path

        db = self.api.db
        plain = self._save_prompt("Plain dunes", category="Day", tags=["a", "b"])
        positive = self._save_prompt("Pair positive", category="Night", tags=["p"])
        pair = db.save_prompt_pair("Pair positive", "Noise", title="Pair")
        owners = (self._save_prompt("Owner one"), self._save_prompt("Owner two"))
        paths = {
            name: str(self.output_dir / f"{name}.png")
            for name in ("plain", "paired", "ambiguous")
        }
        db.link_image_to_prompt(plain, paths["plain"])
        image = db.link_image_to_prompt(positive, paths["paired"])
        db.link_image_to_pair(pair, image, {"source": "fixture"})
        for owner in owners:
            db.link_image_to_prompt(owner, paths["ambiguous"])
        keys = {name: normalize_image_path(path) for name, path in paths.items()}
        padding = [f"/nowhere/{index}.png" for index in range(600)]
        facts = db.get_image_link_facts([*padding, *keys.values(), keys["plain"]])
        self.assertEqual(
            facts,
            {
                keys["plain"]: (True, None, plain, "Day", ("a", "b")),
                keys["paired"]: (True, None, positive, "Night", ("p",)),
            },
        )
        searched = db.get_image_link_facts(list(keys.values()), "dunes")
        self.assertEqual(
            {path: row[0] for path, row in searched.items()},
            {keys["plain"]: True, keys["paired"]: False},
        )
        self.assertEqual(db.get_image_link_facts([]), {})

    def test_new_link_fact_reads_use_indexes(self):
        from database.operations import (
            _LINK_FACTS_SQL,
            _LINK_FILTER_HIT,
            _POSITIVE_TAG_NAMES_SQL,
        )

        with self.api.db.model.get_connection() as conn:
            conn.executemany(
                "INSERT INTO prompts(id,text,hash) VALUES(?,?,?)",
                [(i, f"prompt {i}", f"hash-{i}") for i in range(1, 3_001)],
            )
            conn.executemany(
                "INSERT INTO generated_images(prompt_id,image_path,filename,"
                "file_path) VALUES(?,?,?,?)",
                [
                    (i, f"/x/{i}.png", f"{i}.png", f"/x/{i}.png")
                    for i in range(1, 3_001)
                ],
            )
            for hit, params in (
                ("1", {}),
                (_LINK_FILTER_HIT, {"pattern": "%prompt 1%"}),
            ):
                sql = _LINK_FACTS_SQL.format(hit=hit, marks=":path0,:path1")
                bound = {**params, "path0": "/x/1.png", "path1": "/x/2.png"}
                plan = [
                    row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, bound)
                ]
                self.assertIn(
                    "SEARCH gi USING INDEX idx_image_file_path (file_path=?)", plan
                )
                self.assertFalse(
                    [s for s in plan if s.startswith(("SCAN gi", "SCAN other"))], plan
                )
            plan = [
                row[3]
                for row in conn.execute(
                    "EXPLAIN QUERY PLAN " + _POSITIVE_TAG_NAMES_SQL.format(marks="?,?"),
                    (1, 2),
                )
            ]
        self.assertFalse([step for step in plan if step.startswith("SCAN")], plan)


class TestListingFilterSelection(ImageAPITestCase):
    """#392: "Select all N matching" resolves the listing filter on the server."""

    async def tearDownAsync(self):
        self.api.db.close_all()
        await super().tearDownAsync()

    def media(self, name, mtime=100):
        target = self.output_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        make_png(target)
        os.utime(target, (mtime, mtime))
        return target

    async def listing(self, **params):
        response = await self.client.get(
            "/prompt_manager/images/output",
            params={"mode": "workspace", "limit": 500, **params},
        )
        return (await response.json())["images"]

    async def test_resolve_lists_every_match_in_listing_order_beyond_a_page(self):
        for number in range(7):
            self.media(f"bulk/{number:02}.png", 100 + number)
        self.media("other/x.png", 500)
        from py.api.images import parse_listing_filter

        params = parse_listing_filter({"subfolder": "bulk", "sort": "newest"})
        resolved = await self.api.resolve_listing_filter(params, cap=100)

        listed = await self.listing(subfolder="bulk", sort="newest")
        self.assertEqual(
            [list(target) for target in resolved.targets],
            [[i["root_index"], i["root_scope"], i["path"]] for i in listed],
        )
        self.assertEqual(len(resolved.targets), 7)
        self.assertFalse(resolved.truncated)

    async def test_resolve_reports_linked_positives_and_unlinked_count(self):
        first = self.media("a.png", 300)
        second = self.media("b.png", 200)
        self.media("c.png", 100)
        prompt = self._save_prompt("shared positive")
        self.api.db.link_image_to_prompt(prompt, str(first))
        self.api.db.link_image_to_prompt(prompt, str(second))
        from py.api.images import parse_listing_filter

        resolved = await self.api.resolve_listing_filter(parse_listing_filter({}), 100)

        self.assertEqual(resolved.positive_ids, [prompt])
        self.assertEqual(resolved.unlinked, 1)

    async def test_resolve_marks_more_than_the_cap_as_truncated(self):
        for number in range(4):
            self.media(f"{number}.png", 100 + number)
        from py.api.images import parse_listing_filter

        resolved = await self.api.resolve_listing_filter(parse_listing_filter({}), 3)

        self.assertTrue(resolved.truncated)

    def test_parse_listing_filter_rejects_anything_but_listing_strings(self):
        from py.api.images import parse_listing_filter

        self.assertEqual(
            parse_listing_filter({"q": "cat", "tags": "a,b", "root": "0"}),
            {"q": "cat", "tags": "a,b", "root": "0"},
        )
        for bad in (
            None,
            [],
            "q=cat",
            {"mode": "workspace"},
            {"cursor": "x"},
            {"path": "a.png"},
            {"q": 1},
            {"q": "x" * 2049},
            {"sort": "sideways"},
            {"media": "audio"},
            {"favorites": "yes"},
            {"subfolder": "../up"},
            {"root": "-1"},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    params = parse_listing_filter(bad)
                    # Root bounds need the roots: the resolver checks them.
                    from py.api.images import _workspace_root

                    _workspace_root(params, [self.output_dir])

    async def test_bulk_category_takes_the_listing_filter(self):
        first = self.media("keep/a.png", 300)
        second = self.media("keep/b.png", 200)
        self.media("keep/c.png", 100)
        other = self.media("skip/d.png", 400)
        prompt = self._save_prompt("in the filter")
        outside = self._save_prompt("outside the filter")
        self.api.db.link_image_to_prompt(prompt, str(first))
        self.api.db.link_image_to_prompt(prompt, str(second))
        self.api.db.link_image_to_prompt(outside, str(other))

        response = await self.client.post(
            "/prompt_manager/bulk/category",
            json={"images_filter": {"subfolder": "keep"}, "category": "Kept"},
        )
        data = await response.json()

        self.assertEqual(response.status, 200, data)
        self.assertEqual(
            (data["updated_count"], data["prompt_count"], data["skipped"]), (1, 1, 1)
        )
        self.assertEqual(self.api.db.get_prompt_by_id(prompt)["category"], "Kept")
        self.assertNotEqual(self.api.db.get_prompt_by_id(outside)["category"], "Kept")

    async def test_bulk_category_filter_with_no_linked_image_changes_nothing(self):
        self.media("a.png")

        response = await self.client.post(
            "/prompt_manager/bulk/category",
            json={"images_filter": {}, "category": "Kept"},
        )
        data = await response.json()

        self.assertEqual(response.status, 200, data)
        self.assertEqual(
            (data["updated_count"], data["prompt_count"], data["skipped"]), (0, 0, 1)
        )

    async def test_bulk_category_refuses_a_bad_or_doubled_filter(self):
        self.media("a.png")
        prompt = self._save_prompt("p")
        for body in (
            {"images_filter": {"sort": "sideways"}, "category": "x"},
            {"images_filter": "all", "category": "x"},
            {"images_filter": {}, "prompt_ids": [prompt], "category": "x"},
            {"images_filter": {"root": "9"}, "category": "x"},
        ):
            with self.subTest(body=body):
                response = await self.client.post(
                    "/prompt_manager/bulk/category", json=body
                )
                self.assertEqual(response.status, 400, await response.text())

    async def test_bulk_category_refuses_a_renumbered_source(self):
        self.media("a.png")

        response = await self.client.post(
            "/prompt_manager/bulk/category",
            json={
                "images_filter": {"root": "0", "root_scope": "f" * 64},
                "category": "x",
            },
        )

        self.assertEqual(response.status, 409)
        self.assertEqual((await response.json())["code"], "source-changed")


class TestWorkspaceCollectionFilter(ImageAPITestCase):
    """#331: ``collection=<id>`` keeps only that collection's listed members."""

    media = TestWorkspaceImages.media
    get = TestWorkspaceImages.get
    _names = TestWorkspaceImages._names

    async def tearDownAsync(self):
        self.api.db.close_all()
        assert_closed(self, self._temp_db.name, self._output_tmp.name)
        await super().tearDownAsync()

    def keys(self, root, relative):
        from py.api.images import _favorite_relative_key, _favorite_root_key

        return (_favorite_root_key(root), _favorite_relative_key(relative))

    def collect(self, name, *members):
        """A collection holding ``(root, relative)`` members; its id."""
        result = self.api.db.add_to_collection(
            name=name, items=[self.keys(root, rel) for root, rel in members]
        )
        return result["collection"]["id"]

    def two_sources(self):
        second = self.comfy_dir / "second"
        second.mkdir()
        self.api._get_all_output_dirs = lambda: [self.output_dir, second]
        return second

    async def test_listing_keeps_only_collection_members(self):
        second = self.two_sources()
        self.media("a.png", 300)
        self.media("b.png", 200)
        self.media("sub/c.png", 100, root=second)
        self.media("d.png", 50, root=second)
        grids = self.collect(
            "Grids",
            (self.output_dir, "a.png"),
            (second, "sub/c.png"),
            (self.output_dir, "gone.png"),  # stored, never listed
        )
        self.collect("Other", (self.output_dir, "b.png"))
        response, data = await self.get(collection=str(grids))
        self.assertEqual(response.status, 200, data)
        self.assertEqual(self._names(data), ["a.png", "c.png"])
        self.assertEqual(data["total"], 2)
        _, data = await self.get(collection=str(grids), root="1")
        self.assertEqual(self._names(data), ["c.png"])
        # Without the filter the listing is unchanged.
        _, data = await self.get()
        self.assertEqual(data["total"], 4)

    async def test_collection_filter_combines_with_the_other_filters(self):
        night = self._save_prompt("Neon zebra", category="Night", tags=["a"])
        day = self._save_prompt("Morning field", category="Day", tags=["a"])
        files = {
            name: self.media(name, 100 + index)
            for index, name in enumerate(
                ("night.png", "day.png", "zebra_plain.png", "clip.mp4", "out.png")
            )
        }
        self.api.db.link_image_to_prompt(night, str(files["night.png"]))
        self.api.db.link_image_to_prompt(day, str(files["day.png"]))
        members = [(self.output_dir, name) for name in files if name != "out.png"]
        grids = str(self.collect("Grids", *members))
        cases = (
            ({}, ["clip.mp4", "day.png", "night.png", "zebra_plain.png"]),
            ({"q": "zebra"}, ["night.png", "zebra_plain.png"]),
            ({"category": "Night"}, ["night.png"]),
            ({"tags": "a"}, ["day.png", "night.png"]),
            ({"media": "video"}, ["clip.mp4"]),
            ({"media": "image", "q": "day"}, ["day.png"]),
        )
        for params, expected in cases:
            with self.subTest(**params):
                response, data = await self.get(collection=grids, sort="name", **params)
                self.assertEqual(response.status, 200, data)
                self.assertEqual(self._names(data), expected)
                self.assertEqual(data["total"], len(expected))

    async def test_collection_filter_combines_with_an_images_own_category(self):
        """#331 with #332: a member is admitted by its effective category."""
        from py.api.images import image_category_key

        night = self._save_prompt("Neon zebra", category="Night")
        files = {
            name: self.media(name, 100 + index)
            for index, name in enumerate(
                ("linked.png", "overridden.png", "own.png", "outside.png")
            )
        }
        for name in ("linked.png", "overridden.png"):
            self.api.db.link_image_to_prompt(night, str(files[name]))
        sources = self.api._get_all_output_dirs()

        def own(name, value):
            key = image_category_key(sources, files[name])
            self.api.db.set_image_categories([key], value)

        own("overridden.png", "Portraits")
        own("own.png", "Night")  # unlinked: only its own category
        own("outside.png", "Night")  # not a member
        members = [(self.output_dir, name) for name in files if name != "outside.png"]
        grids = str(self.collect("Grids", *members))
        for category, expected in (
            ("Night", ["linked.png", "own.png"]),
            ("Portraits", ["overridden.png"]),
        ):
            with self.subTest(category=category):
                response, data = await self.get(
                    collection=grids, category=category, sort="name"
                )
                self.assertEqual(response.status, 200, data)
                self.assertEqual(self._names(data), expected)
                self.assertEqual(data["total"], len(expected))

    async def test_invalid_collection_is_a_400(self):
        for value in ("x", "0", "-1", "1.5", "1234567890", " 1"):
            with self.subTest(value=value):
                response, data = await self.get(collection=value)
                self.assertEqual(response.status, 400, data)

    async def test_unknown_collection_is_a_404(self):
        self.media("a.png")
        response, data = await self.get(collection="77")
        self.assertEqual(response.status, 404)
        self.assertEqual(data["code"], "collection-not-found")
        self.assertNotIn("images", data)
        self.api._get_all_output_dirs = lambda: []
        response, data = await self.get(collection="77")
        self.assertEqual((response.status, data["code"]), (404, "collection-not-found"))

    async def test_a_collection_deleted_during_the_read_is_a_404(self):
        self.media("a.png")
        grids = self.collect("Grids", (self.output_dir, "a.png"))
        real = self.api.db.collection_members

        def deleted_meanwhile(collection_id, root_key):
            self.api.db.delete_collection(collection_id)
            return real(collection_id, root_key)

        with patch.object(
            self.api.db, "collection_members", side_effect=deleted_meanwhile
        ):
            response, data = await self.get(collection=str(grids))
        self.assertEqual((response.status, data["code"]), (404, "collection-not-found"))

    async def test_a_failed_members_read_is_a_500_never_an_unfiltered_page(self):
        self.media("a.png")
        self.media("b.png")
        grids = self.collect("Grids", (self.output_dir, "a.png"))

        def broken(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

        with patch.object(self.api.db, "collection_members", side_effect=broken):
            response, data = await self.get(collection=str(grids))
        self.assertEqual(response.status, 500)
        self.assertNotIn("images", data)
        self.assertNotIn("locked", json.dumps(data))

    async def test_reordering_sources_keeps_members(self):
        second = self.two_sources()
        self.media("a.png", root=second)
        self.media("b.png")
        grids = str(self.collect("Grids", (second, "a.png")))
        _, data = await self.get(collection=grids)
        self.assertEqual(
            [(i["root_index"], i["path"]) for i in data["images"]], [(1, "a.png")]
        )
        self.api._get_all_output_dirs = lambda: [second, self.output_dir]
        _, data = await self.get(collection=grids)
        self.assertEqual(
            [(i["root_index"], i["path"]) for i in data["images"]], [(0, "a.png")]
        )

    async def test_cursor_from_another_collection_is_refused(self):
        for index, name in enumerate(("a.png", "b.png", "c.png")):
            self.media(name, 100 + index)
        everything = [(self.output_dir, name) for name in ("a.png", "b.png", "c.png")]
        first = str(self.collect("First", *everything))
        second = str(self.collect("Second", *everything))
        _, page = await self.get(collection=first, limit=1)
        token = page["next_cursor"]
        self.assertIsInstance(token, str)
        response, _ = await self.get(collection=first, limit=1, cursor=token)
        self.assertEqual(response.status, 200)
        for params in ({"collection": second}, {}, {"favorites": "1"}):
            with self.subTest(**params):
                response, _ = await self.get(cursor=token, limit=1, **params)
                self.assertEqual(response.status, 400)

    async def test_collection_wins_over_favorites(self):
        self.media("a.png")
        self.media("b.png")
        grids = str(self.collect("Grids", (self.output_dir, "a.png")))
        from py.api.images import _favorite_root_key

        self.api.db.set_image_favorite(_favorite_root_key(self.output_dir), "b.png")
        _, data = await self.get(collection=grids, favorites="1")
        self.assertEqual(self._names(data), ["a.png"])
