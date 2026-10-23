"""Image annotation routes: observe, capture preview and tag save.

Every request names its file through the listing identity
``{root_index, root_scope, path}``; the scope is checked before any file
access, tag reads and saves are byte-exact (the capture's digest, re-hashed
at Save) and no response ever carries a server path or a locator.
"""

import asyncio
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

try:
    from test_api_images import ImageAPITestCase, make_png
    from open_handles import assert_closed, open_handles_under
except ImportError:
    from tests.test_api_images import ImageAPITestCase, make_png
    from tests.open_handles import assert_closed, open_handles_under

from database.execution_history_schema import rotate_history_epoch
from utils import media_capture
from utils.media_capture import CaptureStore

try:
    from py.api import annotations as annotations_module
except ImportError:  # the module under test is not there yet (RED)
    annotations_module = None

OBSERVE = "/prompt_manager/image-annotations/observe"
TAGS = "/prompt_manager/image-annotations/tags"
CAPTURE = "/prompt_manager/image-annotations/capture/"
MAX_BODY = 64 * 1024
WAIT = 30.0  # ceiling for cross-thread handoffs, never a short clock

# Columns that an observe may legitimately refresh (S1) or that never matter
# for "nothing changed" comparisons.
_SNAPSHOT_TABLES = {
    "prompts": "*",
    "generated_images": "*",
    "tags": "*",
    "prompt_tags": "*",
    "prompt_pair_tags": "*",
    "image_favorites": "*",
    "media_annotation_files": "id, locator",
    "media_annotation_bytes": "id, file_id, sha256, first_seen_at, tag_revision",
    "media_image_tags": "*",
}


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


class AnnotationAPITestCase(ImageAPITestCase):
    """The image API app plus helpers for the annotation routes."""

    async def get_application(self):
        app = await super().get_application()
        self.assertIsNotNone(
            annotations_module, "py.api.annotations is not implemented"
        )
        self.epoch = self.api.db.get_execution_epoch()
        return app

    async def tearDownAsync(self):
        store = getattr(self.api, "_captures", None)
        if store is not None:
            # Every preview handle must already be closed (Windows unlink).
            assert_closed(self, store.directory)
        self.api.close_annotation_captures()
        if store is not None:
            self.assertFalse(os.path.exists(store.directory))
        await super().tearDownAsync()

    # -- helpers ---------------------------------------------------------

    def scope(self, index=0, roots=None):
        roots = roots if roots is not None else self.api._get_all_output_dirs()
        return self.api._gallery_root_scope(roots[index], index)

    def target(self, path="sub/a.png", **changes):
        return {"root_index": 0, "root_scope": self.scope(), "path": path, **changes}

    async def json_of(self, response):
        """The JSON envelope; an unrouted path (no JSON) fails as an assertion."""
        self.assertEqual(
            response.content_type,
            "application/json",
            f"{response.method} {response.url.path} answered {response.status}",
        )
        return await response.json()

    async def observe(self, body):
        response = await self.client.post(OBSERVE, json=body)
        return response.status, await self.json_of(response)

    async def observe_ok(self, path="sub/a.png"):
        status, body = await self.observe({"target": self.target(path)})
        self.assertEqual(status, 200, body)
        return body

    async def put_tags(self, body):
        response = await self.client.put(TAGS, json=body)
        return response.status, await self.json_of(response)

    def save_body(self, observed, tags, **changes):
        return {
            "target": dict(observed["target"]),
            "capture_token": observed["capture"]["token"],
            "sha256": observed["capture"]["sha256"],
            "expected_epoch": observed["library_epoch"],
            "expected_tag_revision": observed["tag_revision"],
            "tags": list(tags),
            **changes,
        }

    def locator(self, path):
        return os.path.normcase(os.path.realpath(str(self.output_dir / path)))

    def write_bytes(self, path, data, *, keep_mtime=False):
        """Rewrite *path* in place, optionally restoring its ``mtime_ns``."""
        full = self.output_dir / path
        before = full.stat()
        full.write_bytes(data)
        if keep_mtime:
            os.utime(full, ns=(before.st_atime_ns, before.st_mtime_ns))
        return full

    def db_snapshot(self):
        conn = sqlite3.connect(self._temp_db.name)
        try:
            snapshot = {}
            for table, columns in _SNAPSHOT_TABLES.items():
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if exists is None:
                    continue
                rows = conn.execute(f"SELECT {columns} FROM {table}").fetchall()
                snapshot[table] = sorted(map(repr, rows))
            return snapshot
        finally:
            conn.close()

    def bytes_rows(self):
        conn = sqlite3.connect(self._temp_db.name)
        try:
            return conn.execute(
                "SELECT sha256, size, mtime_ns, tag_revision FROM "
                "media_annotation_bytes ORDER BY first_seen_at"
            ).fetchall()
        finally:
            conn.close()

    def saved_set(self, path, data, tags):
        """Store *tags* for the exact *data* bytes of *path*; return the receipt."""
        full = self.output_dir / path
        stat = full.stat()
        return self.api.db.replace_image_tags(
            locator=self.locator(path),
            sha256=hashlib.sha256(data).hexdigest(),
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            expected_epoch=self.epoch,
            expected_tag_revision=0,
            tags=tags,
        )

    def use_fake_clock(self):
        clock = FakeClock()
        self.api.close_annotation_captures()
        self.api._captures = CaptureStore(clock=clock)
        return clock

    def assert_no_paths(self, body):
        text = json.dumps(body)
        self.assertNotIn(str(self.comfy_dir), text)
        self.assertNotIn(str(self.output_dir), text)
        self.assertNotIn(os.path.realpath(self.output_dir), text)
        store = getattr(self.api, "_captures", None)
        if store is not None:
            self.assertNotIn(store.directory, text)
        self.assertNotIn("\\\\", text.replace('\\"', ""))
        self.assertNotIn(os.sep + "output" + os.sep, text)


class TestObserve(AnnotationAPITestCase):
    async def test_observe_requires_valid_scope_before_any_file_access(self):
        make_png(self.output_dir / "sub" / "a.png")
        other = self.comfy_dir / "other"
        other.mkdir()
        make_png(other / "sub" / "a.png")
        stale_scope = self.api._gallery_root_scope(other, 0)
        reordered = self.api._gallery_root_scope(self.output_dir, 1)
        with patch.object(
            media_capture.CaptureStore, "capture", autospec=True
        ) as capture:
            capture.side_effect = AssertionError("capture must not run")
            for name, changes, status in (
                ("stale root", {"root_scope": stale_scope}, 409),
                ("reordered root", {"root_scope": reordered}, 409),
                ("removed root", {"root_index": 1, "root_scope": reordered}, 409),
                ("wrong scope", {"root_scope": "0" * 64}, 409),
                ("uppercase scope", {"root_scope": "A" * 64}, 400),
                ("short scope", {"root_scope": "abc"}, 400),
                ("bool index", {"root_index": True}, 400),
                ("negative index", {"root_index": -1}, 400),
                ("string index", {"root_index": "0"}, 400),
                ("traversal", {"path": "../a.png"}, 403),
                ("dot segment", {"path": "./a.png"}, 403),
                ("empty segment", {"path": "sub//a.png"}, 403),
                ("absolute", {"path": "/etc/a.png"}, 403),
                ("backslash", {"path": "sub\\a.png"}, 403),
                ("drive", {"path": "C:/a.png"}, 403),
                ("drive segment", {"path": "sub/C:a.png"}, 403),
                ("nul", {"path": "a\x00.png"}, 403),
                ("non-media", {"path": "notes.txt"}, 403),
                ("empty path", {"path": ""}, 400),
                ("long path", {"path": "a/" * 600 + "a.png"}, 400),
                ("int path", {"path": 7}, 400),
            ):
                with self.subTest(name):
                    status_, body = await self.observe(
                        {"target": self.target(**changes)}
                    )
                    self.assertEqual(status_, status, body)
                    self.assertFalse(body["success"])
                    if status == 409:
                        self.assertEqual(body["code"], "source-changed")
                    self.assert_no_paths(body)
            for name, body in (
                ("missing target", {}),
                ("extra field", {"target": self.target(), "extra": 1}),
                ("target not an object", {"target": "sub/a.png"}),
                ("target missing path", {"target": {"root_index": 0}}),
                ("target extra field", {"target": self.target(sha256="x")}),
            ):
                with self.subTest(name):
                    status_, body_ = await self.observe(body)
                    self.assertEqual(status_, 400, body_)
                    self.assertFalse(body_["success"])
            with self.subTest("body over 64 KiB"):
                padding = "x" * MAX_BODY
                response = await self.client.post(
                    OBSERVE,
                    data=json.dumps({"target": self.target(), "pad": padding}),
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(response.status, 413)
            with self.subTest("not json"):
                response = await self.client.post(
                    OBSERVE, data=b"{oops", headers={"Content-Type": "application/json"}
                )
                self.assertEqual(response.status, 400)
            capture.assert_not_called()

    async def test_observe_denies_case_spellings_of_the_listed_file(self):
        make_png(self.output_dir / "sub" / "a.png")
        for alias in ("sub/A.PNG", "SUB/a.png"):
            with self.subTest(alias=alias):
                status, body = await self.observe({"target": self.target(alias)})
                # Denied as an alias, like a symlink to the listed file.
                self.assertEqual((status, body["code"]), (409, "source-changed"))
                self.assert_no_paths(body)
        store = self.api._captures
        self.assertFalse(any(Path(store.directory).iterdir()))
        await self.observe_ok("sub/a.png")

    async def test_observe_returns_saved_tags_for_exact_bytes_only(self):
        path = make_png(self.output_dir / "sub" / "a.png")
        original = path.read_bytes()
        receipt = self.saved_set("sub/a.png", original, ["Forest", "mist"])
        self.assertEqual(receipt["tag_revision"], 1)

        body = await self.observe_ok()
        self.assertEqual(body["tags"], ["Forest", "mist"])
        self.assertEqual(body["tag_revision"], 1)
        self.assertEqual(body["library_epoch"], self.epoch)
        self.assertEqual(body["filename"], "a.png")
        self.assertIsNone(body["title"])
        self.assertEqual(
            body["target"],
            {"root_index": 0, "root_scope": self.scope(), "path": "sub/a.png"},
        )
        capture = body["capture"]
        self.assertEqual(
            sorted(capture),
            sorted(
                [
                    "token",
                    "expires_at",
                    "preview_url",
                    "sha256",
                    "size",
                    "width",
                    "height",
                    "media_type",
                    "generate",
                ]
            ),
        )
        self.assertEqual(capture["sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(capture["size"], len(original))
        self.assertEqual((capture["width"], capture["height"]), (4, 4))
        self.assertEqual(capture["media_type"], "image")
        self.assertEqual(capture["generate"], {"supported": True, "reason": None})
        self.assertEqual(capture["preview_url"], CAPTURE + capture["token"])
        self.assertTrue(capture["expires_at"].endswith("Z"))

        # Different bytes at the same path: an empty set at revision 0.
        changed = make_png(path, color=(0, 255, 0)).read_bytes()
        self.assertNotEqual(changed, original)
        body = await self.observe_ok()
        self.assertEqual((body["tags"], body["tag_revision"]), ([], 0))
        self.assertEqual(body["capture"]["sha256"], hashlib.sha256(changed).hexdigest())

        # The original bytes again (new mtime): the saved set, and S1 refreshes
        # the record's verified fingerprint so cheap reads see it as current.
        self.write_bytes("sub/a.png", original)
        stat = path.stat()
        current = self.api.db.current_image_tags(
            {self.locator("sub/a.png"): (stat.st_size, stat.st_mtime_ns)}
        )
        body = await self.observe_ok()
        self.assertEqual((body["tags"], body["tag_revision"]), (["Forest", "mist"], 1))
        refreshed = self.api.db.current_image_tags(
            {self.locator("sub/a.png"): (stat.st_size, stat.st_mtime_ns)}
        )
        if stat.st_mtime_ns != self.bytes_rows()[0][2]:
            self.assertEqual(current, {})
        self.assertEqual(refreshed, {self.locator("sub/a.png"): ["Forest", "mist"]})

    async def test_observe_changes_no_tags_revisions_prompts_or_links(self):
        path = make_png(self.output_dir / "sub" / "a.png")
        data = path.read_bytes()
        self.saved_set("sub/a.png", data, ["forest"])
        make_png(self.output_dir / "sub" / "b.png", color=(1, 2, 3))
        self._link_image(self.output_dir / "sub" / "a.png", "a prompt")
        prompt_id = self._save_prompt("tagged prompt", tags=["forest", "night"])
        self.assertTrue(prompt_id)
        before = self.db_snapshot()

        for _ in range(2):
            await self.observe_ok("sub/a.png")
            await self.observe_ok("sub/b.png")

        self.assertEqual(self.db_snapshot(), before)
        # Unseen bytes (b.png) never create a record.
        self.assertEqual(len(self.bytes_rows()), 1)
        self.assertEqual(self.bytes_rows()[0][3], 1)

    async def test_observe_title_follows_the_card_rule(self):
        make_png(self.output_dir / "sub" / "a.png")
        prompt_id = self._save_prompt("Same words\nrest")
        self.api.db.link_image_to_prompt(prompt_id, str(self.output_dir / "sub/a.png"))
        body = await self.observe_ok()
        # A first-line title is withheld unless the file's own records confirm
        # it (the card rule); this PNG carries no prompt metadata.
        self.assertIsNone(body["title"])
        with self.api.db.model.get_connection() as connection:
            connection.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (prompt_id, "Same words"),
            )
        # A stored title is user data and always shows.
        body = await self.observe_ok()
        self.assertEqual(body["title"], "Same words")

    async def test_observe_video_is_manual_only(self):
        clip = self.output_dir / "clip.mp4"
        clip.parent.mkdir(exist_ok=True)
        clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
        body = await self.observe_ok("clip.mp4")
        capture = body["capture"]
        self.assertEqual(capture["media_type"], "video")
        self.assertEqual((capture["width"], capture["height"]), (None, None))
        self.assertEqual(
            capture["generate"],
            {"supported": False, "reason": media_capture.VIDEO_ONLY_REASON},
        )
        self.assertIsNone(body["title"])
        response = await self.client.get(capture["preview_url"])
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "video/mp4")
        self.assertEqual(await response.read(), clip.read_bytes())

    async def test_observe_maps_capture_outcomes_to_codes(self):
        make_png(self.output_dir / "sub" / "a.png")
        outside = make_png(self.comfy_dir / "outside.png")
        self._make_symlink(outside, self.output_dir / "sub" / "alias.png")
        status, body = await self.observe({"target": self.target("sub/alias.png")})
        self.assertEqual((status, body["code"]), (409, "source-changed"))
        self.assert_no_paths(body)

        status, body = await self.observe({"target": self.target("sub/missing.png")})
        self.assertEqual((status, body["code"]), (409, "file-changed"))
        self.assert_no_paths(body)

        self.api.close_annotation_captures()
        self.api._captures = CaptureStore(max_file=16)
        status, body = await self.observe({"target": self.target("sub/a.png")})
        self.assertEqual((status, body["code"]), (415, "unsupported-media"))
        self.assert_no_paths(body)

        self.api.close_annotation_captures()
        self.api._captures = CaptureStore(max_captures=1)
        await self.observe_ok("sub/a.png")
        status, body = await self.observe({"target": self.target("sub/a.png")})
        self.assertEqual((status, body["code"]), (429, "review-capacity"))
        self.assert_no_paths(body)


class TestCaptureRoutes(AnnotationAPITestCase):
    async def test_capture_route_serves_exact_bytes_no_store_nosniff(self):
        path = make_png(self.output_dir / "sub" / "a.png")
        source_bytes = path.read_bytes()
        body = await self.observe_ok()
        token = body["capture"]["token"]
        # The source may change after the capture; the preview never does.
        make_png(path, color=(9, 9, 9))

        resp = await self.client.get(body["capture"]["preview_url"])
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(resp.headers["Content-Type"], "image/png")
        self.assertEqual(resp.headers["Content-Length"], str(len(source_bytes)))
        self.assertEqual(await resp.read(), source_bytes)

        store = self.api._captures
        entry = store._entries[token]
        self.assertEqual(entry.uses, 0)
        self.assertEqual(open_handles_under(store.directory), [])
        self.assertIsNotNone(store.get(token))

    async def test_capture_route_rejects_unknown_expired_and_rescoped_tokens(self):
        make_png(self.output_dir / "sub" / "a.png")
        clock = self.use_fake_clock()
        for token in ("nope", "A" * 300, "x.y", "%2e%2e%2fx", "a b"):
            with self.subTest(token=token):
                resp = await self.client.get(CAPTURE + token)
                self.assertEqual(resp.status, 404)
                data = await self.json_of(resp)
                self.assertEqual(data["code"], "capture-expired")
                self.assertEqual(resp.headers["Cache-Control"], "no-store")

        body = await self.observe_ok()
        url = body["capture"]["preview_url"]
        self.assertEqual((await self.client.get(url)).status, 200)

        # Released explicitly: gone, and releasing again stays successful.
        resp = await self.client.delete(url)
        self.assertEqual(
            (resp.status, await self.json_of(resp)), (200, {"success": True})
        )
        self.assertEqual((await self.client.get(url)).status, 404)
        resp = await self.client.delete(url)
        self.assertEqual(
            (resp.status, await self.json_of(resp)), (200, {"success": True})
        )

        # Idle expiry.
        body = await self.observe_ok()
        url = body["capture"]["preview_url"]
        clock.now += media_capture.DEFAULT_IDLE_SECONDS + 1
        resp = await self.client.get(url)
        self.assertEqual(resp.status, 404)
        self.assertEqual((await self.json_of(resp))["code"], "capture-expired")

        # The roots changed under a live token: denied and released.
        body = await self.observe_ok()
        url = body["capture"]["preview_url"]
        moved = self.comfy_dir / "moved"
        moved.mkdir()
        original_roots = self.api._get_all_output_dirs
        self.api._get_all_output_dirs = lambda: [moved]
        try:
            resp = await self.client.get(url)
            self.assertEqual(resp.status, 409)
            data = await self.json_of(resp)
            self.assertEqual(data["code"], "source-changed")
            self.assert_no_paths(data)
        finally:
            self.api._get_all_output_dirs = original_roots
        resp = await self.client.get(url)
        self.assertEqual(resp.status, 404)
        self.assertFalse(any(Path(self.api._captures.directory).iterdir()))

    async def test_preview_pins_the_capture_while_it_streams(self):
        path = make_png(self.output_dir / "sub" / "a.png", size=(64, 64))
        source_bytes = path.read_bytes()
        body = await self.observe_ok()
        token = body["capture"]["token"]
        store = self.api._captures
        capture_path = store.get(token).path
        stream = annotations_module.AnnotationRoutesMixin._stream_capture
        seen = {}

        async def release_mid_stream(request, capture, handle):
            # A concurrent DELETE (or sweep) lands while the bytes stream.
            self.assertEqual(store._entries[token].uses, 1)
            self.assertTrue(store.release(token))
            seen["kept"] = os.path.exists(capture_path)
            return await stream(request, capture, handle)

        with (
            patch.object(
                annotations_module.AnnotationRoutesMixin,
                "_stream_capture",
                staticmethod(release_mid_stream),
            ),
            patch.object(annotations_module, "_PREVIEW_CHUNK", 16),
        ):
            resp = await self.client.get(body["capture"]["preview_url"])
            self.assertEqual(resp.status, 200)
            self.assertEqual(await resp.read(), source_bytes)
        self.assertTrue(seen["kept"])
        # Removed with the last use, after its handle closed.
        self.assertFalse(os.path.exists(capture_path))
        self.assertEqual(
            (await self.client.get(body["capture"]["preview_url"])).status, 404
        )

    async def test_preview_answers_byte_ranges(self):
        # Safari and WebKit play <video> only from byte-range responses.
        path = make_png(self.output_dir / "sub" / "a.png", size=(64, 64))
        data = path.read_bytes()
        size = len(data)
        body = await self.observe_ok()
        url = body["capture"]["preview_url"]
        token = body["capture"]["token"]

        full = await self.client.get(url)
        self.assertEqual(full.status, 200)
        self.assertEqual(full.headers["Accept-Ranges"], "bytes")
        self.assertEqual(await full.read(), data)
        for header, start, end in (
            ("bytes=0-15", 0, 16),
            ("bytes=10-", 10, size),
            ("bytes=-12", size - 12, size),
            (f"bytes=5-{size + 100}", 5, size),
        ):
            with self.subTest(range=header):
                resp = await self.client.get(url, headers={"Range": header})
                self.assertEqual(resp.status, 206)
                self.assertEqual(
                    resp.headers["Content-Range"], f"bytes {start}-{end - 1}/{size}"
                )
                self.assertEqual(resp.headers["Content-Length"], str(end - start))
                self.assertEqual(resp.headers["Cache-Control"], "no-store")
                self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(resp.headers["Content-Type"], "image/png")
                self.assertEqual(await resp.read(), data[start:end])
        for header in (f"bytes={size}-", "bytes=9-3", "items=0-1", "bytes=0-1,4-5"):
            with self.subTest(unsatisfiable=header):
                resp = await self.client.get(url, headers={"Range": header})
                self.assertEqual(resp.status, 416)
                self.assertEqual(resp.headers["Content-Range"], f"bytes */{size}")
                self.assertEqual(resp.headers["Cache-Control"], "no-store")
        store = self.api._captures
        self.assertEqual(store._entries[token].uses, 0)
        self.assertEqual(open_handles_under(store.directory), [])
        self.assertIsNotNone(store.get(token))

    async def test_preview_of_a_vanished_capture_file_is_expired(self):
        make_png(self.output_dir / "sub" / "a.png")
        body = await self.observe_ok()
        os.unlink(self.api._captures.get(body["capture"]["token"]).path)
        resp = await self.client.get(body["capture"]["preview_url"])
        self.assertEqual(resp.status, 404)
        data = await self.json_of(resp)
        self.assertEqual(data["code"], "capture-expired")
        self.assert_no_paths(data)
        self.assertIsNone(self.api._captures.get(body["capture"]["token"]))

    async def test_restore_invalidates_captures(self):
        make_png(self.output_dir / "sub" / "a.png")
        body = await self.observe_ok()
        url = body["capture"]["preview_url"]
        self.assertEqual((await self.client.get(url)).status, 200)
        backup = str(self.comfy_dir / "backup.db")
        self.assertTrue(self.api.db.model.backup_database(backup))
        safety = self.api.db.model.restore_from_file(backup)
        try:
            resp = await self.client.get(url)
            self.assertEqual(resp.status, 404)
            self.assertEqual((await self.json_of(resp))["code"], "capture-expired")
            status, saved = await self.put_tags(self.save_body(body, ["forest"]))
            self.assertEqual((status, saved["code"]), (409, "capture-expired"))
            self.assertEqual(self.bytes_rows(), [])
        finally:
            for path in (safety, backup):
                for suffix in ("", "-wal", "-shm"):
                    if path and os.path.exists(path + suffix):
                        os.unlink(path + suffix)


class TestCancellation(AnnotationAPITestCase):
    """A cancelled handler never leaks a pin, a handle or a capture."""

    async def cancel_when(self, coro, entered):
        task = asyncio.ensure_future(coro)
        await asyncio.get_running_loop().run_in_executor(None, entered.wait, WAIT)
        self.assertTrue(entered.is_set())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def settle(self, done, what):
        for _ in range(3000):
            if done():
                return
            await asyncio.sleep(0.01)
        self.fail(what)

    def observe_request(self, path="sub/a.png"):
        body = AsyncMock(return_value=({"target": self.target(path)}, None))
        patcher = patch.object(self.api, "_annotation_body", body)
        patcher.start()
        self.addCleanup(patcher.stop)
        return make_mocked_request("POST", OBSERVE)

    async def test_cancelled_preview_open_closes_the_handle_before_release(self):
        make_png(self.output_dir / "sub" / "a.png")
        body = await self.observe_ok()
        token = body["capture"]["token"]
        store = self.api._captures
        capture_path = store.get(token).path
        entered, proceed = threading.Event(), threading.Event()
        handles = []

        def slow_open(path):
            entered.set()
            proceed.wait(WAIT)
            handle = open(path, "rb")
            handles.append(handle)
            return handle

        request = make_mocked_request(
            "GET", CAPTURE + token, match_info={"token": token}
        )
        with patch.object(annotations_module, "_open_capture", slow_open):
            await self.cancel_when(self.api.get_capture_preview(request), entered)
            # A DELETE lands meanwhile; the file stays until the open settles.
            store.release(token)
            self.assertTrue(os.path.exists(capture_path))
            proceed.set()
            await self.settle(
                lambda: not os.path.exists(capture_path), "capture never removed"
            )
        self.assertEqual(len(handles), 1)
        self.assertTrue(handles[0].closed)

    async def test_cancelled_observe_capture_releases_the_new_capture(self):
        make_png(self.output_dir / "sub" / "a.png")
        store = self.api._capture_store()
        real_capture = store.capture
        entered, proceed = threading.Event(), threading.Event()
        made = []

        def slow_capture(*args, **kwargs):
            entered.set()
            proceed.wait(WAIT)
            capture = real_capture(*args, **kwargs)
            made.append(capture)
            return capture

        request = self.observe_request()
        with patch.object(store, "capture", slow_capture):
            await self.cancel_when(self.api.observe_image(request), entered)
            proceed.set()
            await self.settle(
                lambda: made and store.get(made[0].token) is None,
                "the cancelled observe left its capture",
            )
        self.assertFalse(any(Path(store.directory).iterdir()))

    async def test_cancelled_observe_read_releases_the_capture(self):
        make_png(self.output_dir / "sub" / "a.png")
        store = self.api._capture_store()
        real_read = self.api._observe_capture
        entered, proceed, finished = (threading.Event() for _ in range(3))
        tokens = []

        def slow_read(store_, capture, root, relative):
            tokens.append(capture.token)
            entered.set()
            proceed.wait(WAIT)
            try:
                return real_read(store_, capture, root, relative)
            finally:
                finished.set()

        request = self.observe_request()
        with patch.object(self.api, "_observe_capture", slow_read):
            await self.cancel_when(self.api.observe_image(request), entered)
            self.assertIsNone(store.get(tokens[0]))
            proceed.set()
            await asyncio.get_running_loop().run_in_executor(None, finished.wait, WAIT)
        await self.settle(
            lambda: not any(Path(store.directory).iterdir()), "capture file left"
        )


class TestSaveTags(AnnotationAPITestCase):
    async def test_put_saves_then_rereads_the_same_set(self):
        make_png(self.output_dir / "sub" / "a.png")
        observed = await self.observe_ok()
        status, receipt = await self.put_tags(
            self.save_body(observed, ["Forest", "mist", "forest"])
        )
        self.assertEqual(status, 200, receipt)
        self.assertEqual(
            receipt,
            {
                "success": True,
                "library_epoch": self.epoch,
                "sha256": observed["capture"]["sha256"],
                "tags": ["Forest", "mist"],
                "tag_revision": 1,
                "replayed": False,
            },
        )
        body = await self.observe_ok()
        self.assertEqual((body["tags"], body["tag_revision"]), (["Forest", "mist"], 1))

    async def test_put_rehashes_current_file_and_rejects_changed_bytes(self):
        path = make_png(self.output_dir / "sub" / "a.png")
        original = path.read_bytes()
        observed = await self.observe_ok()
        before = self.db_snapshot()
        # Same size, same mtime, other bytes: only the digest tells.
        replacement = bytearray(original)
        replacement[-1] ^= 0xFF
        self.write_bytes("sub/a.png", bytes(replacement), keep_mtime=True)
        self.assertEqual(path.stat().st_size, len(original))

        status, body = await self.put_tags(self.save_body(observed, ["forest"]))
        self.assertEqual((status, body["code"]), (409, "file-changed"))
        self.assertEqual(self.db_snapshot(), before)
        self.assert_no_paths(body)

        # Reopening shows the new bytes as an unseen, empty set.
        reopened = await self.observe_ok()
        self.assertEqual((reopened["tags"], reopened["tag_revision"]), ([], 0))
        self.assertNotEqual(
            reopened["capture"]["sha256"], observed["capture"]["sha256"]
        )

        # A vanished file is also file-changed with zero writes.
        path.unlink()
        status, body = await self.put_tags(self.save_body(reopened, ["forest"]))
        self.assertEqual((status, body["code"]), (409, "file-changed"))
        self.assertEqual(self.db_snapshot(), before)

    async def test_put_rejects_capture_for_other_target_or_epoch(self):
        make_png(self.output_dir / "sub" / "a.png")
        make_png(self.output_dir / "sub" / "b.png", color=(0, 0, 255))
        observed_a = await self.observe_ok("sub/a.png")
        observed_b = await self.observe_ok("sub/b.png")
        before = self.db_snapshot()

        # b's token for a's target: refused before any whole-file hash.
        body = self.save_body(observed_a, ["forest"])
        body["capture_token"] = observed_b["capture"]["token"]
        body["sha256"] = observed_b["capture"]["sha256"]
        with patch.object(
            media_capture.CaptureStore, "verify", autospec=True
        ) as verify:
            status, result = await self.put_tags(body)
            verify.assert_not_called()
        self.assertEqual((status, result["code"]), (409, "capture-expired"))

        # A digest that is not the capture's.
        body = self.save_body(observed_a, ["forest"], sha256="0" * 64)
        status, result = await self.put_tags(body)
        self.assertEqual(status, 400)

        # An unknown token.
        body = self.save_body(observed_a, ["forest"], capture_token="unknown-token")
        status, result = await self.put_tags(body)
        self.assertEqual((status, result["code"]), (409, "capture-expired"))

        # Another epoch than the capture's.
        body = self.save_body(observed_a, ["forest"], expected_epoch=str(uuid.uuid4()))
        status, result = await self.put_tags(body)
        self.assertEqual((status, result["code"]), (409, "storage-changed"))
        self.assertEqual(result["library_epoch"], self.epoch)

        # A target the token was not captured for (scope of another root).
        other = self.comfy_dir / "other"
        other.mkdir()
        make_png(other / "sub" / "a.png")
        roots = [self.output_dir, other]
        self.api._get_all_output_dirs = lambda: roots
        body = self.save_body(observed_a, ["forest"])
        body["target"] = {
            "root_index": 1,
            "root_scope": self.scope(1, roots),
            "path": "sub/a.png",
        }
        status, result = await self.put_tags(body)
        self.assertEqual((status, result["code"]), (409, "capture-expired"))

        self.assertEqual(self.db_snapshot(), before)
        for result_ in (result,):
            self.assert_no_paths(result_)

    async def test_put_conflict_storage_changed_and_limit_codes(self):
        make_png(self.output_dir / "sub" / "a.png")
        observed = await self.observe_ok()
        status, first = await self.put_tags(self.save_body(observed, ["forest"]))
        self.assertEqual(status, 200, first)

        # A stale revision with a different set: the current set comes back.
        status, body = await self.put_tags(self.save_body(observed, ["woods"]))
        self.assertEqual((status, body["code"]), (409, "tag-conflict"))
        self.assertEqual((body["tags"], body["tag_revision"]), (["forest"], 1))
        self.assertEqual(body["library_epoch"], self.epoch)

        # Validation at the boundary, before any file or database work.
        with patch.object(
            media_capture.CaptureStore, "verify", autospec=True
        ) as verify:
            verify.side_effect = AssertionError("verify must not run")
            cases = (
                ("too many tags", {"tags": [f"t{i}" for i in range(101)]}, 400),
                ("tag not a string", {"tags": ["ok", 3]}, 400),
                ("tags not a list", {"tags": "forest"}, 400),
                ("blank tag", {"tags": ["  "]}, 400),
                ("bool revision", {"expected_tag_revision": True}, 400),
                ("negative revision", {"expected_tag_revision": -1}, 400),
                ("bad epoch", {"expected_epoch": "not-a-uuid"}, 400),
                ("bad digest", {"sha256": "xyz"}, 400),
                ("bad token type", {"capture_token": 5}, 400),
                ("traversal", {"target": self.target("../a.png")}, 403),
                ("wrong scope", {"target": self.target(root_scope="f" * 64)}, 409),
            )
            for name, changes, expected in cases:
                with self.subTest(name):
                    body_ = {**self.save_body(observed, ["forest"]), **changes}
                    status, result = await self.put_tags(body_)
                    self.assertEqual(status, expected, result)
                    self.assertFalse(result["success"])
                    if name == "too many tags":
                        self.assertEqual(result["code"], "tag-limit")
            with self.subTest("extra field"):
                body_ = self.save_body(observed, ["forest"])
                body_["extra"] = 1
                status, result = await self.put_tags(body_)
                self.assertEqual(status, 400)
            with self.subTest("missing field"):
                body_ = self.save_body(observed, ["forest"])
                del body_["tags"]
                status, result = await self.put_tags(body_)
                self.assertEqual(status, 400)
            with self.subTest("body over 64 KiB"):
                body_ = self.save_body(observed, ["forest"])
                body_["tags"] = ["x" * 60] * 1200
                response = await self.client.put(
                    TAGS,
                    data=json.dumps(body_),
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(response.status, 413)
            verify.assert_not_called()

        # Storage restored under the dialog: storage-changed, zero writes.
        before = self.db_snapshot()
        fresh = await self.observe_ok()
        rotate_history_epoch(self.api.db.model.get_connection())
        status, body = await self.put_tags(self.save_body(fresh, ["woods"]))
        self.assertEqual((status, body["code"]), (409, "storage-changed"))
        self.assertEqual(body["library_epoch"], self.api.db.get_execution_epoch())
        self.assertNotEqual(body["library_epoch"], self.epoch)
        self.assertEqual(self.db_snapshot(), before)

    async def test_put_replay_returns_replayed_true(self):
        make_png(self.output_dir / "sub" / "a.png")
        observed = await self.observe_ok()
        body = self.save_body(observed, ["forest", "mist"])
        status, first = await self.put_tags(body)
        self.assertEqual(
            (status, first["replayed"], first["tag_revision"]), (200, False, 1)
        )
        before = self.db_snapshot()

        status, again = await self.put_tags(body)
        self.assertEqual(status, 200, again)
        self.assertEqual(again, {**first, "replayed": True})
        self.assertEqual(self.db_snapshot(), before)

        # The same set at the new revision is a replay as well.
        body["expected_tag_revision"] = 1
        status, third = await self.put_tags(body)
        self.assertEqual(
            (status, third["replayed"], third["tag_revision"]), (200, True, 1)
        )
        self.assertEqual(self.db_snapshot(), before)

    async def test_put_saves_only_the_target_byte_set(self):
        """Tagging one file never touches a sibling that shares its prompt."""
        a = make_png(self.output_dir / "sub" / "a.png")
        make_png(self.output_dir / "sub" / "b.png", color=(2, 2, 2))
        twin = self.output_dir / "sub" / "twin.png"
        twin.write_bytes(a.read_bytes())
        prompt_id = self._save_prompt("shared prompt", tags=["shared"])
        for name in ("a.png", "b.png", "twin.png"):
            self.api.db.link_image_to_prompt(
                prompt_id, str(self.output_dir / "sub" / name)
            )
        observed_b = await self.observe_ok("sub/b.png")
        self.saved_set(
            "sub/b.png", (self.output_dir / "sub" / "b.png").read_bytes(), ["b"]
        )
        legacy_before = {
            k: v
            for k, v in self.db_snapshot().items()
            if k
            not in (
                "media_annotation_files",
                "media_annotation_bytes",
                "media_image_tags",
            )
        }

        observed = await self.observe_ok("sub/a.png")
        status, receipt = await self.put_tags(self.save_body(observed, ["forest"]))
        self.assertEqual(status, 200, receipt)

        after = self.db_snapshot()
        legacy_after = {
            k: v
            for k, v in after.items()
            if k
            not in (
                "media_annotation_files",
                "media_annotation_bytes",
                "media_image_tags",
            )
        }
        # The tag registry gained "forest"; prompts, links and prompt tags are equal.
        self.assertEqual(
            {k: v for k, v in legacy_after.items() if k != "tags"},
            {k: v for k, v in legacy_before.items() if k != "tags"},
        )
        self.assertEqual((await self.observe_ok("sub/b.png"))["tags"], ["b"])
        self.assertEqual((await self.observe_ok("sub/twin.png"))["tags"], [])
        self.assertEqual(observed_b["tags"], [])


class TestFailures(AnnotationAPITestCase):
    async def test_unexpected_failures_answer_generic_envelopes(self):
        make_png(self.output_dir / "sub" / "a.png")
        secret = str(self.output_dir / "sub" / "a.png")
        with patch.object(
            media_capture.CaptureStore,
            "capture",
            autospec=True,
            side_effect=media_capture.CaptureError(secret),
        ):
            status, body = await self.observe({"target": self.target()})
        self.assertEqual((status, body["code"]), (500, "capture-error"))
        self.assert_no_paths(body)

        with (
            patch.object(
                media_capture.CaptureStore,
                "capture",
                autospec=True,
                side_effect=OSError(5, "I/O error", secret),
            ),
            self.assertLogs("prompt_manager.api", "ERROR"),
        ):
            status, body = await self.observe({"target": self.target()})
        self.assertEqual((status, body["code"]), (500, "observe-failed"))
        self.assert_no_paths(body)

        with (
            patch.object(
                self.api.db, "read_image_tags", side_effect=RuntimeError(secret)
            ),
            self.assertLogs("prompt_manager.api", "ERROR"),
        ):
            status, body = await self.observe({"target": self.target()})
        self.assertEqual((status, body["code"]), (500, "observe-failed"))
        self.assert_no_paths(body)
        # The capture of the failed observation is released at once.
        self.assertEqual(self.api._captures.stats()["captures"], 0)

        observed = await self.observe_ok()
        with (
            patch.object(
                self.api.db, "replace_image_tags", side_effect=RuntimeError(secret)
            ),
            self.assertLogs("prompt_manager.api", "ERROR"),
        ):
            status, body = await self.put_tags(self.save_body(observed, ["forest"]))
        self.assertEqual((status, body["code"]), (500, "save-failed"))
        self.assert_no_paths(body)
        self.assertEqual(self.bytes_rows(), [])

    async def test_title_lookup_failure_still_observes(self):
        make_png(self.output_dir / "sub" / "a.png")
        with patch.object(
            self.api.db, "get_image_link_summaries", side_effect=RuntimeError("db")
        ):
            body = await self.observe_ok()
        self.assertIsNone(body["title"])
        self.assertEqual((body["tags"], body["tag_revision"]), ([], 0))

    async def test_save_of_a_released_capture_is_expired(self):
        make_png(self.output_dir / "sub" / "a.png")
        observed = await self.observe_ok()
        resp = await self.client.delete(observed["capture"]["preview_url"])
        self.assertEqual(resp.status, 200)
        status, body = await self.put_tags(self.save_body(observed, ["forest"]))
        self.assertEqual((status, body["code"]), (409, "capture-expired"))
        self.assertEqual(self.bytes_rows(), [])

    async def test_cleanup_removes_the_capture_directory_and_subscription(self):
        import database.execution_history as observers

        make_png(self.output_dir / "sub" / "a.png")
        await self.observe_ok()
        directory = self.api._captures.directory
        identity = os.path.normcase(os.path.realpath(self.api.db.model.db_path))
        self.assertEqual(len(observers._restore_observers[identity]), 1)
        await self.api.cleanup_annotation_captures(None)
        await self.api.cleanup_annotation_captures(None)
        self.assertFalse(os.path.exists(directory))
        self.assertNotIn(identity, observers._restore_observers)
        # The shutdown cleanup is final: a later observation (or a monitor
        # recording still in flight) creates no new store or directory.
        status, body = await self.observe({"target": self.target("sub/a.png")})
        self.assertEqual((status, body["code"]), (500, "capture-error"), body)
        self.assertIsNone(self.api._captures)
        self.assertNotIn(identity, observers._restore_observers)
        # An explicit, non-final close still lets a later observation start
        # a fresh store (the tests' own store swaps rely on it).
        self.api._captures_closed = False
        await self.observe_ok()
        self.assertNotEqual(self.api._captures.directory, directory)


class TestEnvelopes(AnnotationAPITestCase):
    async def test_responses_never_contain_absolute_paths_or_locators(self):
        make_png(self.output_dir / "sub" / "a.png")
        bodies = []
        observed = await self.observe_ok()
        bodies.append(observed)
        status, body = await self.observe({"target": self.target("sub/missing.png")})
        bodies.append(body)
        status, body = await self.observe({"target": self.target("../a.png")})
        bodies.append(body)
        status, body = await self.put_tags(self.save_body(observed, ["forest"]))
        bodies.append(body)
        status, body = await self.put_tags(self.save_body(observed, ["woods"]))
        bodies.append(body)
        resp = await self.client.get(CAPTURE + "missing")
        bodies.append(await self.json_of(resp))
        resp = await self.client.delete(observed["capture"]["preview_url"])
        bodies.append(await self.json_of(resp))
        status, body = await self.put_tags(self.save_body(observed, ["forest"]))
        bodies.append(body)
        for body in bodies:
            self.assert_no_paths(body)
            self.assertNotIn(self.locator("sub/a.png"), json.dumps(body))
            self.assertNotIn("locator", json.dumps(body))

    async def test_legacy_inspect_and_autotag_shapes_unchanged(self):
        make_png(self.output_dir / "image.png")
        resp = await self.client.get(
            "/prompt_manager/images/inspect/image.png", params={"root": "0"}
        )
        self.assertEqual(resp.status, 200)
        data = await self.json_of(resp)
        # Additive only: B3 adds the image_tags facet beside the Library facts.
        self.assertEqual(
            sorted(data),
            [
                "file",
                "image",
                "image_tags",
                "inspection_version",
                "library",
                "metadata",
                "prompts",
                "resolution",
                "root_scope",
                "success",
            ],
        )
        self.assertEqual(sorted(data["image"]), ["filename", "path", "root_index"])
        self.assertEqual(data["image_tags"], {"state": "none", "tags": []})
        self.assertEqual(
            sorted(data["library"]),
            [
                "category",
                "category_scope",
                "pair_id",
                "positive_id",
                "scope",
                "status",
                "tags",
                "tags_scope",
                "title",
            ],
        )

        class FakeService:
            custom_prompt = ""
            default_prompt = "p"
            wd14_general_threshold = 0.35
            wd14_character_threshold = 0.85

            def get_models_status(self):
                return {"wd14-vit": {"downloaded": False, "model_path": None}}

            def is_model_loaded(self):
                return False

            def get_loaded_model_type(self):
                return None

        with patch("py.autotag.get_autotag_service", return_value=FakeService()):
            resp = await self.client.get("/prompt_manager/autotag/models")
        self.assertEqual(resp.status, 200)
        self.assertEqual(
            sorted(await self.json_of(resp)),
            [
                "default_prompt",
                "loaded_model_type",
                "model_loaded",
                "models",
                "success",
                "suggestion_limit",  # B2 adds it (D68); nothing else changes
                "wd14_character_threshold",
                "wd14_general_threshold",
            ],
        )

    def test_every_storage_code_has_a_coded_envelope(self):
        cases = {
            "tag-limit": 400,
            "tag-conflict": 409,
            "storage-changed": 409,
            "metadata-conflict": 409,
            "vocabulary-conflict": 409,
            "some-future-code": 409,
        }
        for code, status in cases.items():
            with self.subTest(code=code):
                response = annotations_module._coded(code)
                self.assertEqual(response.status, status)
                body = json.loads(response.body)
                self.assertEqual((body["success"], body["code"]), (False, code))
                self.assertTrue(body["error"])
                self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_save_maps_an_unlisted_storage_code_to_an_envelope(self):
        from database.annotations import AnnotationCommandError
        from database.library_commands import LibraryCommandError

        make_png(self.output_dir / "sub" / "a.png")
        observed = await self.observe_ok()
        for error, status in (
            (AnnotationCommandError("tag-limit", self.epoch, None), 400),
            (LibraryCommandError("vocabulary-conflict", self.epoch), 409),
        ):
            with (
                self.subTest(code=error.code),
                patch.object(self.api.db, "replace_image_tags", side_effect=error),
            ):
                status_, body = await self.put_tags(
                    self.save_body(observed, ["forest"])
                )
                self.assertEqual((status_, body["code"]), (status, error.code))
                self.assertEqual(body["library_epoch"], self.epoch)
                self.assert_no_paths(body)

    def test_preview_types_cover_every_capture_extension(self):
        for extension in media_capture.CAPTURE_EXTENSIONS:
            kind = annotations_module.preview_content_type(extension)
            self.assertTrue(kind.startswith(("image/", "video/")), extension)
        self.assertEqual(
            annotations_module.preview_content_type(".exe"), "application/octet-stream"
        )


class AnnotationHostCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_root_registers_capture_cleanup_on_the_host_application(self):
        from aiohttp import web

        try:
            from tests.test_init_package import fake_server_module, load_package
        except ImportError:
            from test_init_package import fake_server_module, load_package

        with tempfile.TemporaryDirectory() as directory:
            server = fake_server_module()
            server.PromptServer.instance.app = web.Application()
            with load_package(
                "annotation_cleanup_pkg",
                str(Path(directory) / "db.sqlite"),
                directory,
                server,
            ) as (package, host):
                api = package.api
                self.assertIn(api.cleanup_annotation_captures, host.app.on_cleanup)
                store = api._capture_store()
                host.app.freeze()
                await host.app.cleanup()
                self.assertFalse(os.path.exists(store.directory))
                self.assertIsNone(api._captures)
