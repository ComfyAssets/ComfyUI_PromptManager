"""Opt-in workspace gallery contracts against disposable SQLite and media."""

import asyncio
import json
import os
import shutil
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

    def _link_fixtures(self):
        """Linked (titled, first-line, pair, untitled pair), ambiguous and none."""
        db = self.api.db
        long_line = "lantern " * 15
        titled = self._save_prompt("Titled positive", category="Night", rating=5)
        with db.model.get_connection() as connection:
            connection.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (titled, "Stored library title"),
            )
        plain = self._save_prompt(long_line)
        pair = db.save_prompt_pair("Pair positive", "Noise", title="Pair title")
        bare = db.save_prompt_pair("Bare pair positive\nmore", "Blur")
        first, second = self._save_prompt("Owner one"), self._save_prompt("Owner two")
        files = {
            name: self.media(f"{name}.png")
            for name in ("titled", "pair", "ambiguous", "none")
        }
        # First-line titles come from files whose own prompt evidence agrees.
        files["plain"] = embedded_png(self.output_dir / "plain.png", long_line)
        files["bare"] = embedded_png(
            self.output_dir / "bare.png", "Bare pair positive\nmore", "Blur"
        )
        db.link_image_to_prompt(titled, str(files["titled"]))
        db.link_image_to_prompt(plain, str(files["plain"]))
        for name, pair_id in (("pair", pair), ("bare", bare)):
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
                "Pair title",
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
        # The page's link summary, and the positive count's one link read.
        self.assertEqual(len(selects), 2, selects)
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
                "seeded.png": 42001,
                "plain.png": None,
                "clip.mp4": None,
                "broken.png": None,
            },
        )

    async def test_card_reads_each_header_once(self):
        for index in range(3):
            records_png(
                self.output_dir / f"{index}.png", seeded_records("a fox", index)
            )
        self.media("clip.mp4")
        with patch("PIL.Image.open", wraps=Image.open) as opened:
            _, data = await self.get()
        self.assertEqual(len(data["images"]), 4)
        self.assertEqual(opened.call_count, 3)

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
        self.assertEqual(items["ok.png"]["seed"], 5)
        self.assertIsNone(items["evil.png"]["seed"])
        self.assertIsNone(items["evil.png"]["linked"]["title"])
        response = await self.client.get(
            "/prompt_manager/images/inspect/evil.png",
            params={"root": 0, "root_scope": items["evil.png"]["root_scope"]},
        )
        self.assertEqual(response.status, 200, await response.text())
        self.assertIsNone((await response.json())["library"]["title"])

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
        real_open, real_parse = Image.open, header_params_of_images()

        def opening(stream, *args, **kwargs):
            streams.append(stream)
            return real_open(stream, *args, **kwargs)

        def parsing(info):
            closed.append(all(stream.closed for stream in streams))
            return real_parse(info)

        with (
            patch("PIL.Image.open", side_effect=opening),
            patch("py.api.images.header_params", side_effect=parsing),
        ):
            _, data = await self.get()
        self.assertEqual(data["images"][0]["seed"], 5)
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
                expected[name] = seed
        # ComfyUI's randomized seeds mostly exceed 2**53, where JSON.parse rounds.
        big = 2**63 + 12345
        records_png(self.output_dir / "big-seed.png", seeded_records("big", big))
        expected["big-seed.png"] = big
        for case, records in CASES.items():
            records_png(self.output_dir / f"case-{case}.png", records)
        records_png(
            self.output_dir / "a1111.png",
            {"parameters": "a fox\nNegative prompt: blur\nSteps: 20, Seed: 9"},
        )
        self.media("bare.png")
        return expected

    async def _inspector_seeds(self, items):
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
            input=json.dumps(bodies).encode("utf-8"),
            capture_output=True,
            timeout=NODE_TIMEOUT_SECONDS,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8"))
        return json.loads(completed.stdout)

    async def test_card_seed_equals_inspector_seed_when_both_present(self):
        expected = self._seed_files()
        _, data = await self.get(limit=200)
        items = data["images"]
        self.assertEqual(len(items), len(CASES) + len(expected) + 2)
        inspector = await self._inspector_seeds(items)
        both = 0
        for item in items:
            with self.subTest(file=item["path"]):
                card, shown = item["seed"], inspector[item["path"]]
                if item["path"] in expected:
                    self.assertEqual(card, expected[item["path"]])
                if card is None:
                    continue
                # The card never shows a seed the inspector does not.
                self.assertEqual(shown, {"status": "known", "value": str(card)})
                both += 1
        self.assertGreaterEqual(both, len(expected) + 10)

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
            # Text, title, tag, pair title (its positive) and the filename match.
            ({"q": "ZEBRA"}, 6, 4),
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
        self.assertEqual(len(selects), 2, selects)
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
        self.assertEqual(len(selects), 1, selects)  # the page's link summary only
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
        # A filter or the rating sort needs the read, so its failure is an error.
        for params in ({"q": "zebra"}, {"category": "Night"}, {"sort": "rating"}):
            with (
                self.subTest(failing=params),
                patch.object(
                    db, "get_image_link_matches", side_effect=RuntimeError("gone")
                ),
            ):
                response, data = await self.get(**params)
                self.assertEqual(response.status, 500, data)
                self.assertNotIn("images", data)

    async def test_search_matches_filename_prompt_text_and_tags(self):
        from database.models import normalize_image_path

        files = self._filter_fixtures()
        _, data = await self.get(q="ZEBRA", sort="name")
        # Text, stored title, tag, pair title and filename; never an ambiguous link.
        self.assertEqual(
            self._names(data),
            [
                "day_ab.png",
                "night_ab.png",
                "paired.png",
                "titled.png",
                "zebra_unlinked.png",
            ],
        )
        self.assertEqual(data["total"], 5)
        _, data = await self.get(q="0% w")
        self.assertEqual(self._names(data), ["percent.png"])
        _, data = await self.get(q="%")
        self.assertEqual(self._names(data), ["percent.png"])
        _, data = await self.get(q="harbor")
        self.assertEqual(self._names(data), ["night_a.png"])
        self.assertEqual(
            self.api.db.get_image_paths_matching("zebra", None, []),
            {
                normalize_image_path(str(files[name]))
                for name in ("night_ab", "day_ab", "titled", "paired")
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
        self.assertEqual(len(selects), 2, selects)

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
                "", "Night", [], every_link=True, wanted=wanted
            ),
            {
                normalize_image_path(str(files["night_ab"])): (
                    True,
                    5,
                    self.api.db.get_prompt_id_for_image(str(files["night_ab"])),
                ),
                "/elsewhere/7.png": (True, 5, elsewhere),
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
        from database.operations import link_filter_query

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
                    self.assertEqual(len(selects()), 1)
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
        sql, params = link_filter_query("lantern", "cat-3", ["tag-1"], every_link=True)
        with self.api.db.model.get_connection() as conn:
            plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]
        self.assertIn(
            "SEARCH other USING COVERING INDEX idx_image_file_path (file_path=?)", plan
        )
        self.assertFalse([step for step in plan if step.startswith("SCAN other")])

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
                token = _workspace_cursor(key, "scope")
                self.assertLessEqual(len(token), 4096)
                self.assertEqual(_workspace_cursor_key(token, "scope", order), key)
