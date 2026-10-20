"""Opt-in workspace gallery contracts against disposable SQLite and media."""

import json
import os
import shutil
import subprocess  # nosec B404
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
        self.assertEqual(len(selects), 1, selects)

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
