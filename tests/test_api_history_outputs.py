"""Real occurrence routes preserve receipts and fence current-file publication."""

import asyncio
import errno
import importlib
import json
import sqlite3
import threading
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from tests.test_api_images import ImageAPITestCase, make_png
from tests.test_api_prompt_inspector import graph, png
from tests.test_execution_context_database import bundle as context_bundle
from tests.test_execution_history_database import JOB, SESSION
from utils.execution_contexts import ContextBundle
from utils.execution_history_events import make_finish, make_start
from utils.execution_outputs import OutputBundle, OutputReference, snapshot_output_roots
from py.config import GalleryConfig


class HistoryOutputAPITests(ImageAPITestCase):
    async def get_application(self):
        app = await super().get_application()
        self.temp_dir = self.comfy_dir / "temp"
        self.temp_dir.mkdir()
        self.host_output = str(self.output_dir)
        self.host_temp = str(self.temp_dir)
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(
            patch(
                "folder_paths.get_output_directory",
                lambda: self.host_output,
                create=True,
            )
        )
        self.patches.enter_context(
            patch(
                "folder_paths.get_temp_directory", lambda: self.host_temp, create=True
            )
        )
        self.patches.enter_context(
            patch.object(
                GalleryConfig, "allowed_gallery_parents", lambda: [str(self.comfy_dir)]
            )
        )
        return app

    async def tearDownAsync(self):
        self.api.close_history_reads()
        self.patches.close()
        await super().tearDownAsync()

    def ref(self, node="1", ordinal=0, filename="image.png", **kwargs):
        roots = snapshot_output_roots("posix", self.host_output, self.host_temp)
        return OutputReference(
            node,
            ordinal,
            kwargs.get("folder_type", "output"),
            kwargs.get("subfolder", ""),
            filename,
            kwargs.get("path_style", "posix"),
            kwargs.get("root_key", roots.output_key),
        )

    def capture(self, references=None, item=0, state="complete", reason=None):
        start = make_start(
            SESSION, ((0, JOB, {}, {}), item), self.api.db.get_execution_epoch(), item
        )
        self.api.db.write_execution_start(start)
        finish = make_finish(
            SESSION, item, start.storage_epoch, SimpleNamespace(messages=[]), item
        )
        self.api.db.write_execution_finish(finish)
        if references is not None:
            value = OutputBundle(
                tuple(references),
                state,
                reason or ("reported" if references else "none_reported"),
            )
            self.assertTrue(
                self.api.db.write_execution_outputs_for_finish(finish, value)
            )
        return start

    async def outputs(self, start, params=None):
        query = {"epoch": start.storage_epoch}
        if params is not None:
            query = params
        response = await self.client.get(
            f"/prompt_manager/history/runs/{start.run_id}/outputs", params=query
        )
        self.assertEqual(
            response.content_type,
            "application/json",
            "reported-output route is missing",
        )
        return response, await response.json()

    async def current(self, url, **kwargs):
        response = await self.client.request(kwargs.pop("method", "GET"), url, **kwargs)
        return response

    async def media(self, start):
        response, body = await self.outputs(start)
        self.assertEqual(response.status, 200, body)
        return body["items"][0]["current_candidate"]["media"]

    def assert_error(self, response, body, status, code):
        self.assertEqual(response.status, status, body)
        self.assertFalse(body["success"])
        self.assertEqual(body["history_version"], 1)
        self.assertEqual(body["code"], code)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertNotIn(str(self.comfy_dir), json.dumps(body))

    def saved_state(self):
        conn = self.api.db.model.get_connection()
        return {
            table: [
                tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")
            ]
            for table in (
                "prompts",
                "prompt_pairs",
                "generated_images",
                "image_prompt_pairs",
            )
        }

    def restore_fixture(self):
        source = str(self.comfy_dir / "restore.db")
        self.assertTrue(self.api.db.model.backup_database(source))
        return source

    async def test_ordered_receipts_repeat_locators_without_context_ownership(self):
        make_png(self.output_dir / "image.png")
        start = self.capture([self.ref("1", 0), self.ref("1", 1), self.ref("2", 0)])
        contexts = (
            context_bundle("frozen A", anchor="1").contexts[0],
            context_bundle("frozen B", anchor="2").contexts[0],
        )
        self.api.db.write_execution_contexts(
            start.run_id,
            start.storage_epoch,
            ContextBundle(contexts, "complete", "captured"),
        )
        repeated = self.capture([self.ref()], item=1)
        with patch.object(
            self.api.db,
            "get_execution_contexts",
            side_effect=AssertionError("contexts x outputs"),
        ):
            response, page = await self.outputs(
                start, {"epoch": start.storage_epoch, "limit": "2"}
            )
        self.assertEqual(response.status, 200, page)
        self.assertEqual(page["retained_occurrence_count"], 3)
        self.assertEqual(page["list_status"], "complete")
        self.assertEqual(
            [
                (r["occurrence"]["output_node_id"], r["occurrence"]["image_ordinal"])
                for r in page["items"]
            ],
            [("1", 0), ("1", 1)],
        )
        self.assertTrue(page["has_more"])
        self.assertLessEqual(len(page["next_cursor"]), 4096)
        _, second = await self.outputs(
            start, {"epoch": start.storage_epoch, "cursor": page["next_cursor"]}
        )
        self.assertEqual(second["items"][0]["occurrence"]["output_node_id"], "2")
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_cursor"])
        _, other = await self.outputs(repeated)
        self.assertNotEqual(other["items"][0]["occurrence"]["run_id"], start.run_id)
        for receipt in page["items"] + second["items"] + other["items"]:
            self.assertEqual(receipt["file_identity"], "unverified")
            self.assertEqual(receipt["newly_generated"], "unknown")
            self.assertEqual(receipt["context_membership"], "unknown")
            self.assertNotIn("context_id", receipt)
            candidate = receipt["current_candidate"]
            self.assertEqual(candidate["status"], "available")
            self.assertEqual(candidate["evidence_class"], "locator_candidate")
            self.assertEqual(candidate["media"]["occurrence"], receipt["occurrence"])
            self.assertEqual(candidate["media"]["reference"], receipt["reference"])
            self.assertEqual(candidate["media"]["kind"], "reported-location")
            for field in ("url", "thumbnail_url", "inspection_url"):
                self.assertTrue(
                    candidate["media"][field].startswith(
                        "/prompt_manager/history/runs/"
                        f"{receipt['occurrence']['run_id']}/output/current/"
                    )
                )
                self.assertNotIn("root=", candidate["media"][field])
                self.assertNotIn(str(self.comfy_dir), candidate["media"][field])

    async def test_complete_zero_incomplete_unavailable_and_not_captured_are_distinct(
        self,
    ):
        cases = [
            ((), "complete", "none_reported", 0, "complete"),
            ((self.ref(),), "partial", "limit_reached", 1, "incomplete"),
            ((), "unavailable", "unavailable", 0, "incomplete"),
            (None, "not_captured", None, None, "not_captured"),
        ]
        for item, (refs, state, reason, count, status) in enumerate(cases):
            start = self.capture(refs, item, state, reason)
            response, page = await self.outputs(start)
            self.assertEqual(response.status, 200, page)
            self.assertEqual(page["capture"]["state"], state)
            self.assertEqual(page["retained_occurrence_count"], count)
            self.assertEqual(page["list_status"], status)
            self.assertFalse(page["has_more"])

    async def test_epoch_limits_cursor_scope_and_header_conflict(self):
        start = self.capture([self.ref("1"), self.ref("2")])
        allowed, page = await self.outputs(
            start, {"epoch": start.storage_epoch, "limit": "1"}
        )
        self.assertEqual(allowed.status, 200, page)
        for params in (
            {},
            {"epoch": "bad"},
            {"epoch": start.storage_epoch, "limit": "0"},
            {"epoch": start.storage_epoch, "limit": "101"},
            {"epoch": start.storage_epoch, "cursor": "bad"},
        ):
            response, error = await self.outputs(start, params)
            self.assertEqual(response.status, 400, error)
        token = page["next_cursor"]
        other = self.capture([self.ref()], item=1)
        response, error = await self.outputs(
            other, {"epoch": start.storage_epoch, "cursor": token}
        )
        self.assert_error(response, error, 400, "invalid-cursor")
        finish = make_finish(
            SESSION, 0, start.storage_epoch, SimpleNamespace(messages=[]), 0
        )
        self.api.db.write_execution_outputs_for_finish(
            finish,
            OutputBundle((self.ref(filename="other.png"),), "complete", "reported"),
        )
        response, error = await self.outputs(
            start, {"epoch": start.storage_epoch, "cursor": token}
        )
        self.assert_error(response, error, 409, "capture-changed")
        _, current = await self.outputs(start)
        self.assertEqual(current["capture"]["state"], "conflicting")
        self.assertEqual(len(current["items"]), 2)
        source = self.restore_fixture()
        self.api.db.model.restore_from_file(source)
        response, error = await self.outputs(start)
        self.assert_error(response, error, 409, "history-changed")

    async def test_metadata_free_malformed_and_replacement_remain_read_only(self):
        path = make_png(self.output_dir / "image.png")
        start = self.capture([self.ref()])
        self.api.db.write_execution_contexts(
            start.run_id,
            start.storage_epoch,
            context_bundle("Frozen-only negative", "Frozen-only positive"),
        )
        frozen_outputs = self.api.db.get_execution_outputs(start.run_id)
        before = self.saved_state()
        for metadata in (
            {},
            {"prompt": "{malformed"},
            {"prompt": graph("Current file prompt")},
        ):
            png(path, **metadata)
            descriptor = await self.media(start)
            response = await self.current(descriptor["url"])
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.read(), path.read_bytes())
            response = await self.current(descriptor["inspection_url"])
            body = await response.json()
            self.assertEqual(response.status, 200, body)
            identity = body["current_output"]
            self.assertEqual(body.get("history_version"), 1)
            self.assertEqual(body.get("history_epoch"), start.storage_epoch)
            self.assertEqual(identity["occurrence"], descriptor["occurrence"])
            self.assertEqual(identity["reference"], descriptor["reference"])
            self.assertEqual(identity["history_epoch"], start.storage_epoch)
            self.assertEqual(identity["current_scope"], descriptor["current_scope"])
            self.assertEqual(identity["location_key"], descriptor["location_key"])
            self.assertEqual(identity["file_identity"], "unverified")
            self.assertEqual(body["file"]["observation"], "current-file")
            self.assertNotIn("Frozen-only negative", json.dumps(body))
            self.assertNotIn("Frozen-only positive", json.dumps(body))
            self.assertEqual(self.saved_state(), before)
        self.assertEqual(body["prompts"]["positive"]["text"], "Current file prompt")
        self.assertEqual(
            self.api.db.get_execution_outputs(start.run_id), frozen_outputs
        )

    async def test_exact_gallery_matches_do_not_choose_duplicate_owners_or_basename(
        self,
    ):
        path = make_png(self.output_dir / "image.png")
        other = self.temp_dir / path.name
        make_png(other)
        self._link_image(other, "Other root")
        start = self.capture([self.ref()])
        _, page = await self.outputs(start)
        self.assertEqual(page["items"][0]["current_candidate"]["gallery_match"], "none")
        first = self._link_image(path, "first owner")
        _, page = await self.outputs(start)
        self.assertEqual(
            page["items"][0]["current_candidate"]["gallery_image_id"], first
        )
        self._link_image(path, "second owner")
        descriptor = await self.media(start)
        _, page = await self.outputs(start)
        candidate = page["items"][0]["current_candidate"]
        self.assertEqual(candidate["gallery_match"], "ambiguous")
        self.assertIsNone(candidate["gallery_image_id"])
        response = await self.current(descriptor["inspection_url"])
        body = await response.json()
        self.assertEqual(response.status, 200, body)
        self.assertEqual(body["library"]["status"], "ambiguous")
        self.assertIsNone(body["library"].get("positive_id"))

    async def test_scope_reconfiguration_invalidates_urls_without_root_index_fallback(
        self,
    ):
        make_png(self.output_dir / "image.png")
        make_png(self.temp_dir / "image.png", color=(0, 255, 0))
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        self.api._get_all_output_dirs = lambda: [self.temp_dir, self.output_dir]
        for field in ("url", "thumbnail_url", "inspection_url"):
            response = await self.current(descriptor[field])
            self.assert_error(response, await response.json(), 409, "candidate-stale")
        current = await self.media(start)
        response = await self.current(current["url"])
        self.assertEqual(
            await response.read(), (self.output_dir / "image.png").read_bytes()
        )
        self.api._get_all_output_dirs = lambda: [self.output_dir, self.temp_dir]
        response = await self.current(current["url"])
        self.assertEqual(response.status, 200)
        self.assertEqual(
            await response.read(), (self.output_dir / "image.png").read_bytes()
        )
        self.host_output = str(self.temp_dir)
        _, page = await self.outputs(start)
        self.assertEqual(
            page["items"][0]["current_candidate"]["reason"], "namespace_mismatch"
        )

    async def test_occurrence_query_is_strict_and_viewer_cachebuster_is_compatible(
        self,
    ):
        make_png(self.output_dir / "image.png")
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        allowed = await self.current(descriptor["url"] + "&_pm_viewer=123-2")
        self.assertEqual(allowed.status, 200)
        await allowed.read()
        path = descriptor["url"].split("?")[0]
        base = {
            "node": "1",
            "ordinal": "0",
            "epoch": start.storage_epoch,
            "current_scope": descriptor["current_scope"],
        }
        for key in base:
            params = dict(base)
            del params[key]
            response = await self.current(path, params=params)
            self.assertEqual(response.status, 400)
            await response.read()
        for values in (
            {"node": ""},
            {"node": "x" * 129},
            {"node": "\0"},
            {"ordinal": "256"},
            {"ordinal": "01"},
            {"current_scope": "A" * 64},
            {"current_scope": "a" * 63},
        ):
            response = await self.current(path, params={**base, **values})
            self.assertEqual(response.status, 400, values)
            await response.read()
        response = await self.current(descriptor["url"] + "&node=1")
        self.assertEqual(response.status, 400)
        await response.read()

    async def test_media_ranges_head_and_existing_thumbnail_without_generation(self):
        path = self.output_dir / "clip.mp4"
        payload = bytes(range(256)) * 600
        path.write_bytes(payload)
        start = self.capture([self.ref(filename=path.name)])
        descriptor = await self.media(start)
        self.assertTrue(descriptor["is_video"])
        for header, status, expected in (
            (None, 200, payload),
            ("bytes=3-7", 206, payload[3:8]),
            ("bytes=-4", 206, payload[-4:]),
            ("bytes=153599-", 206, payload[-1:]),
        ):
            response = await self.current(
                descriptor["url"], headers={} if header is None else {"Range": header}
            )
            self.assertEqual(response.status, status)
            self.assertEqual(await response.read(), expected)
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
            self.assertEqual(response.headers.get("Accept-Ranges"), "bytes")
        for header in ("bytes=999999-", "bytes=0-2,5-9", "bytes=bad", "bytes=-0"):
            response = await self.current(descriptor["url"], headers={"Range": header})
            self.assertEqual(response.status, 416, header)
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
            await response.read()
        response = await self.current(descriptor["url"], method="HEAD")
        self.assertEqual(response.status, 200)
        self.assertEqual(int(response.headers["Content-Length"]), len(payload))
        self.assertEqual(await response.read(), b"")
        response = await self.current(descriptor["thumbnail_url"])
        self.assertEqual(await response.read(), payload)
        preview = make_png(self.output_dir / "thumbnails" / "clip_thumb.jpg")
        response = await self.current(descriptor["thumbnail_url"])
        self.assertEqual(await response.read(), preview.read_bytes())

    async def test_saved_lookup_failure_preserves_receipts_and_file_evidence(
        self,
    ):
        png(self.output_dir / "image.png", prompt=graph("File evidence"))
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        history = importlib.import_module("py.api.history")
        with (
            self.assertLogs("prompt_manager.api", "WARNING"),
            patch.object(
                history,
                "read_history_gallery",
                side_effect=sqlite3.OperationalError("private/path"),
            ),
        ):
            response, page = await self.outputs(start)
            self.assertEqual(response.status, 200, page)
            self.assertEqual(len(page["items"]), 1)
            self.assertEqual(page["items"][0]["current_candidate"]["status"], "error")
            self.assertNotIn("media", page["items"][0]["current_candidate"])
            response = await self.current(descriptor["inspection_url"])
            body = await response.json()
        self.assertEqual(response.status, 200, body)
        self.assertEqual(body["prompts"]["positive"]["text"], "File evidence")
        self.assertEqual(body["library"]["status"], "unavailable")

    async def test_restore_during_candidate_stat_rejects_page(self):
        path = make_png(self.output_dir / "image.png")
        start = self.capture([self.ref()])
        source = self.restore_fixture()
        original = Path.stat
        armed = True

        def stat(candidate, *args, **kwargs):
            nonlocal armed
            result = original(candidate, *args, **kwargs)
            if candidate == path and armed:
                armed = False
                self.api.db.model.restore_from_file(source)
            return result

        with patch.object(Path, "stat", stat):
            response, body = await self.outputs(start)
        self.assert_error(response, body, 409, "history-changed")

    async def test_scope_change_during_candidate_stat_rejects_page(self):
        path = make_png(self.output_dir / "image.png")
        start = self.capture([self.ref()])
        original = Path.stat
        armed = True

        def stat(candidate, *args, **kwargs):
            nonlocal armed
            result = original(candidate, *args, **kwargs)
            if candidate == path and armed:
                armed = False
                self.host_output = str(self.temp_dir)
            return result

        with patch.object(Path, "stat", stat):
            response, body = await self.outputs(start)
        self.assert_error(response, body, 409, "candidate-stale")

    async def test_restore_during_metadata_read_rejects_inspection(self):
        path = png(self.output_dir / "image.png", prompt=graph())
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        source = self.restore_fixture()
        original = Path.open
        armed = True
        handles = []

        def opened(candidate, *args, **kwargs):
            nonlocal armed
            handle = original(candidate, *args, **kwargs)
            if candidate == path and args and args[0] == "rb":
                handles.append(handle)
                if armed:
                    armed = False
                    self.api.db.model.restore_from_file(source)
            return handle

        with patch.object(Path, "open", opened):
            response = await self.current(descriptor["inspection_url"])
            body = await response.json()
        self.assert_error(response, body, 409, "history-changed")
        self.assertTrue(handles)
        self.assertTrue(all(handle.closed for handle in handles))

    async def test_restore_during_open_closes_handle_before_error_headers(self):
        make_png(self.output_dir / "image.png")
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        source = self.restore_fixture()
        mod = importlib.import_module("py.api.history_current")
        original = mod._open_current_target
        handles = []

        def opened(*args, **kwargs):
            handle = original(*args, **kwargs)
            handles.append(handle)
            self.api.db.model.restore_from_file(source)
            return handle

        with patch.object(mod, "_open_current_target", side_effect=opened):
            response = await self.current(descriptor["url"])
            body = await response.json()
        self.assert_error(response, body, 409, "history-changed")
        self.assertTrue(all(handle.closed for handle in handles))

    async def test_primary_projection_and_fence_failure_never_return_empty_success(
        self,
    ):
        make_png(self.output_dir / "image.png")
        start = self.capture([self.ref()])
        self.assertEqual((await self.outputs(start))[0].status, 200)
        with (
            self.assertLogs("prompt_manager.api", "WARNING"),
            patch.object(
                self.api.db,
                "list_history_outputs",
                side_effect=sqlite3.OperationalError("private"),
            ),
        ):
            response, body = await self.outputs(start)
        self.assert_error(response, body, 503, "storage-unavailable")
        with (
            self.assertLogs("prompt_manager.api", "WARNING"),
            patch.object(
                self.api._history_fence(),
                "validate",
                side_effect=sqlite3.DatabaseError("private"),
            ),
        ):
            response, body = await self.outputs(start)
        self.assert_error(response, body, 503, "storage-unavailable")

    async def test_page_deduplicates_locators_and_closes_connection_before_io(
        self,
    ):
        make_png(self.output_dir / "image.png")
        start = self.capture([self.ref("1", i) for i in range(105)])
        self.assertEqual((await self.outputs(start))[0].status, 200)
        history = importlib.import_module("py.api.history")
        original = history.resolve_current_candidate
        locators = []

        def resolved(reference, scope, gallery):
            self.assertIsNone(getattr(self.api.db.model._local, "conn", None))
            locators.append(reference)
            return original(reference, scope, gallery)

        with patch.object(history, "resolve_current_candidate", side_effect=resolved):
            response, body = await self.outputs(
                start, {"epoch": start.storage_epoch, "limit": "100"}
            )
        self.assertEqual(response.status, 200, body)
        self.assertEqual(len(body["items"]), 100)
        self.assertEqual(len(locators), 1)

    async def test_actual_control_heavy_page_uses_serialized_bytes_and_smaller_retry(
        self,
    ):
        name = "\x01\n猫.png"
        make_png(self.output_dir / name)
        refs = [self.ref("\x01\n猫" * 40, i, filename=name) for i in range(40)]
        start = self.capture(refs)
        response, body = await self.outputs(start)
        self.assertEqual(response.status, 200, body)
        encoded = await response.read()
        self.assertIn(b"\\u0001", encoded)
        self.assertIn(b"%01", encoded)
        module = importlib.import_module("py.api.history")
        with patch.object(module, "_OUTPUTS_MAX_BYTES", len(encoded)):
            response, same = await self.outputs(start)
            self.assertEqual(response.status, 200, same)
        with patch.object(module, "_OUTPUTS_MAX_BYTES", len(encoded) - 1):
            response, error = await self.outputs(start)
            self.assert_error(response, error, 422, "response-limit")
            self.assertEqual(
                error["recovery"], {"action": "retry-outputs", "limit": 20}
            )
            response, smaller = await self.outputs(
                start, {"epoch": start.storage_epoch, "limit": "20"}
            )
            self.assertEqual(response.status, 200, smaller)
            self.assertEqual(len(smaller["items"]), 20)
        with patch.object(module, "_OUTPUTS_MAX_BYTES", 1):
            response, error = await self.outputs(
                start, {"epoch": start.storage_epoch, "limit": "1"}
            )
            self.assert_error(response, error, 422, "response-limit")
            self.assertIsNone(error["recovery"])

    async def test_legal_capture_can_exceed_one_mib_and_recover_without_lost_receipts(
        self,
    ):
        subfolder = "/".join(["\x01" * 175] * 4)
        filename = "\x02" * 20 + ".png"
        make_png(self.output_dir / subfolder / filename)
        start = self.capture(
            [
                self.ref("\x03" * 128, i, filename, subfolder=subfolder)
                for i in range(100)
            ]
        )
        response, error = await self.outputs(
            start, {"epoch": start.storage_epoch, "limit": "100"}
        )
        self.assert_error(response, error, 422, "response-limit")
        self.assertEqual(error["recovery"], {"action": "retry-outputs", "limit": 50})
        response, page = await self.outputs(
            start, {"epoch": start.storage_epoch, "limit": "50"}
        )
        self.assertEqual(response.status, 200, page)
        self.assertLessEqual(len(await response.read()), 1024 * 1024)
        self.assertEqual(len(page["items"]), 50)
        response, next_page = await self.outputs(
            start,
            {
                "epoch": start.storage_epoch,
                "limit": "50",
                "cursor": page["next_cursor"],
            },
        )
        self.assertEqual(response.status, 200, next_page)
        self.assertEqual(
            [
                r["occurrence"]["image_ordinal"]
                for r in page["items"] + next_page["items"]
            ],
            list(range(100)),
        )

    async def test_native_alias_allowed_and_foreign_null_temp_denials_keep_receipts(
        self,
    ):
        path = make_png(self.output_dir / "image.png")
        alias = self.comfy_dir / "alias"
        alias.symlink_to(self.output_dir, target_is_directory=True)
        self.host_output = str(alias)
        allowed = self.capture([self.ref()])
        descriptor = await self.media(allowed)
        response = await self.current(descriptor["url"])
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), path.read_bytes())
        make_png(self.temp_dir / "image.png")
        temp_key = snapshot_output_roots(
            "posix", self.host_output, self.host_temp
        ).temp_key
        denied = self.capture(
            [
                self.ref("1", root_key=None),
                self.ref("2", path_style="windows", root_key="a" * 64),
                self.ref("3", folder_type="temp", root_key=temp_key),
            ],
            item=1,
            state="partial",
            reason="root_unavailable",
        )
        response, page = await self.outputs(denied)
        self.assertEqual(response.status, 200, page)
        self.assertEqual(
            [r["current_candidate"]["reason"] for r in page["items"]],
            ["root_unavailable", "foreign_path_style", "not_authorized"],
        )
        self.assertTrue(
            all("media" not in r["current_candidate"] for r in page["items"])
        )
        self.assertTrue(page["items"][2]["transient"])

    async def test_unavailable_files_keep_receipts_and_current_routes_fail_closed(self):
        path = make_png(self.output_dir / "image.png")
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        path.unlink()
        response, page = await self.outputs(start)
        self.assertEqual(page["items"][0]["current_candidate"]["reason"], "missing")
        for field in ("url", "thumbnail_url", "inspection_url"):
            response = await self.current(descriptor[field])
            self.assert_error(response, await response.json(), 404, "not-found")
        path.mkdir()
        _, page = await self.outputs(start)
        self.assertEqual(
            page["items"][0]["current_candidate"]["reason"], "not_regular_file"
        )
        path.rmdir()
        path.write_bytes(b"media")
        with patch("os.access", return_value=False):
            _, page = await self.outputs(start)
            self.assertEqual(
                page["items"][0]["current_candidate"]["reason"], "unreadable"
            )
            response = await self.current(descriptor["url"])
            self.assert_error(response, await response.json(), 403, "not-authorized")
        path.unlink()
        outside = make_png(self.temp_dir / "private.png")
        path.symlink_to(outside)
        _, page = await self.outputs(start)
        self.assertEqual(
            page["items"][0]["current_candidate"]["reason"], "not_authorized"
        )
        unsupported = self.capture([self.ref(filename="notes.txt")], item=1)
        (self.output_dir / "notes.txt").symlink_to(outside)
        self.api._get_all_output_dirs = lambda: [self.output_dir, self.temp_dir]
        _, page = await self.outputs(unsupported)
        self.assertEqual(
            page["items"][0]["current_candidate"]["reason"], "unsupported_media"
        )

    async def test_scope_change_during_metadata_read_and_open_rejects_before_headers(
        self,
    ):
        png(self.output_dir / "image.png", prompt=graph())
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        service = importlib.import_module("py.api.inspection_service")
        read = service.read_inspection_file

        def changed(path):
            result = read(path)
            self.host_output = str(self.temp_dir)
            return result

        with patch.object(service, "read_inspection_file", side_effect=changed):
            response = await self.current(descriptor["inspection_url"])
            self.assert_error(response, await response.json(), 409, "candidate-stale")
        self.host_output = str(self.output_dir)
        mod = importlib.import_module("py.api.history_current")
        opened = mod._open_current_target
        handles = []

        def changed_open(*args, **kwargs):
            handle = opened(*args, **kwargs)
            handles.append(handle)
            self.host_output = str(self.temp_dir)
            return handle

        with patch.object(mod, "_open_current_target", side_effect=changed_open):
            response = await self.current(descriptor["url"])
            self.assert_error(response, await response.json(), 409, "candidate-stale")
        self.assertTrue(all(handle.closed for handle in handles))

    async def test_canonical_target_symlink_retarget_at_open_never_opens_replacement(
        self,
    ):
        path = make_png(self.output_dir / "image.png")
        outside = make_png(self.temp_dir / "private.png")
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        mod = importlib.import_module("py.api.history_current")
        opened = mod._open_current_target

        def retarget(target, *args, **kwargs):
            path.unlink()
            path.symlink_to(outside)
            return opened(target, *args, **kwargs)

        with patch.object(mod, "_open_current_target", side_effect=retarget):
            response = await self.current(descriptor["url"])
            self.assert_error(response, await response.json(), 409, "candidate-stale")

    async def test_late_open_handle_closes_when_request_is_cancelled_during_executor(
        self,
    ):
        make_png(self.output_dir / "image.png")
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        request = make_mocked_request(
            "GET", descriptor["url"], match_info={"run_id": start.run_id}
        )
        mod = importlib.import_module("py.api.history_current")
        history = importlib.import_module("py.api.history")
        opened = mod._open_current_target
        discard = history._close_prepared_result
        reached, closed = asyncio.Event(), asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        handles = []

        def barrier(*args, **kwargs):
            handle = opened(*args, **kwargs)
            handles.append(handle)
            loop.call_soon_threadsafe(reached.set)
            if not release.wait(5):
                handle.close()
                raise AssertionError("late-open barrier timed out")
            return handle

        def discarded(task):
            discard(task)
            closed.set()

        with (
            patch.object(mod, "_open_current_target", side_effect=barrier),
            patch.object(history, "_close_prepared_result", side_effect=discarded),
        ):
            pending = asyncio.create_task(self.api.get_history_current_media(request))
            try:
                await asyncio.wait_for(reached.wait(), 5)
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
            finally:
                release.set()
                await asyncio.wait_for(closed.wait(), 5)
        self.assertEqual(len(handles), 1)
        self.assertTrue(handles[0].closed)

    async def test_opened_handle_closes_if_stream_is_cancelled_before_headers(self):
        make_png(self.output_dir / "image.png")
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        request = make_mocked_request(
            "GET", descriptor["url"], match_info={"run_id": start.run_id}
        )
        mod = importlib.import_module("py.api.history_current")
        opened = mod._open_current_target
        handles = []

        def tracked(*args, **kwargs):
            handle = opened(*args, **kwargs)
            handles.append(handle)
            return handle

        with (
            patch.object(mod, "_open_current_target", side_effect=tracked),
            patch.object(
                web.StreamResponse, "prepare", side_effect=asyncio.CancelledError
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await self.api.get_history_current_media(request)
        self.assertEqual(len(handles), 1)
        self.assertTrue(handles[0].closed)

    async def test_restore_after_final_fence_finishes_coherent_already_opened_media(
        self,
    ):
        path = make_png(self.output_dir / "image.png")
        expected = path.read_bytes()
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        source = self.restore_fixture()
        fence = self.api._history_fence()
        validate = fence.validate

        def after(epoch, generation):
            validate(epoch, generation)
            self.api.db.model.restore_from_file(source)
            path.unlink()

        with patch.object(fence, "validate", side_effect=after):
            response = await self.current(descriptor["url"])
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.read(), expected)
        response = await self.current(descriptor["url"])
        self.assert_error(response, await response.json(), 409, "history-changed")

    async def test_current_inspection_shares_response_limit_and_saved_display_bound(
        self,
    ):
        path = png(self.output_dir / "image.png", prompt=graph("File evidence"))
        image = self._link_image(path, "Saved text is never frozen-run text")
        with self.api.db.model.get_connection() as conn:
            conn.execute(
                "UPDATE prompts SET category=? WHERE id="
                "(SELECT prompt_id FROM generated_images WHERE id=?)",
                ("猫" * 12000, image),
            )
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        response = await self.current(descriptor["inspection_url"])
        body = await response.json()
        self.assertEqual(response.status, 200, body)
        self.assertEqual(body["library"]["status"], "limited")
        self.assertEqual(body["prompts"]["positive"]["text"], "File evidence")
        with patch("utils.inspection_response.MAX_RESPONSE_BYTES", 1):
            response = await self.current(descriptor["inspection_url"])
            self.assert_error(response, await response.json(), 422, "response-limit")

    async def test_optional_candidate_failure_preserves_every_occurrence_without_urls(
        self,
    ):
        make_png(self.output_dir / "image.png")
        start = self.capture([self.ref("1"), self.ref("2")])
        self.assertEqual((await self.outputs(start))[0].status, 200)
        history = importlib.import_module("py.api.history")
        with patch.object(
            history, "resolve_current_candidate", side_effect=OSError("private/path")
        ):
            response, page = await self.outputs(start)
        self.assertEqual(response.status, 200, page)
        self.assertEqual(len(page["items"]), 2)
        self.assertEqual(page["retained_occurrence_count"], 2)
        for receipt in page["items"]:
            self.assertEqual(
                receipt["current_candidate"],
                {
                    "status": "error",
                    "reason": "temporary_unavailable",
                    "evidence_class": "locator_candidate",
                },
            )
            self.assertEqual(receipt["file_identity"], "unverified")
        self.assertNotIn("private", json.dumps(page))

    async def test_page_observes_at_most_one_hundred_distinct_targets(self):
        for ordinal in range(105):
            make_png(self.output_dir / f"image-{ordinal}.png")
        start = self.capture([self.ref("1", i, f"image-{i}.png") for i in range(105)])
        observed = []
        stat = Path.stat

        def traced(path, *args, **kwargs):
            if path.parent == self.output_dir and path.name.startswith("image-"):
                observed.append(path.name)
            return stat(path, *args, **kwargs)

        with patch.object(Path, "stat", traced):
            response, page = await self.outputs(
                start, {"epoch": start.storage_epoch, "limit": "100"}
            )
        self.assertEqual(response.status, 200, page)
        self.assertEqual(len(page["items"]), 100)
        self.assertEqual(observed, [f"image-{i}.png" for i in range(100)])

    async def test_inspection_retarget_during_read_rejects_metadata(self):
        path = png(self.output_dir / "image.png", prompt=graph("Authorized"))
        outside = png(self.temp_dir / "private.png", prompt=graph("Private"))
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        service = importlib.import_module("py.api.inspection_service")
        read = service.read_inspection_file

        def changed(target):
            result = read(target)
            path.unlink()
            path.symlink_to(outside)
            return result

        with patch.object(service, "read_inspection_file", side_effect=changed):
            response = await self.current(descriptor["inspection_url"])
            body = await response.json()
        self.assert_error(response, body, 409, "candidate-stale")
        self.assertNotIn("Authorized", json.dumps(body))
        self.assertNotIn("Private", json.dumps(body))

    async def test_stale_epoch_rejects_urls_without_unbounded_saved_lookup(self):
        make_png(self.output_dir / "image.png")
        start = self.capture([self.ref()])
        with patch.object(
            self.api.db,
            "get_image_prompt_info",
            side_effect=AssertionError("unbounded convenience lookup"),
        ):
            descriptor = await self.media(start)
            response = await self.current(descriptor["inspection_url"])
            self.assertEqual(response.status, 200)
            await response.read()
        source = self.restore_fixture()
        self.api.db.model.restore_from_file(source)
        for field in ("url", "thumbnail_url", "inspection_url"):
            response = await self.current(descriptor[field])
            self.assert_error(response, await response.json(), 409, "history-changed")

    async def test_existing_authorized_preview_alias_is_allowed_and_escape_is_denied(
        self,
    ):
        make_png(self.output_dir / "image.png")
        preview = make_png(
            self.output_dir / "previews" / "image_thumb.png", color=(0, 255, 0)
        )
        alias = self.output_dir / "thumbnails"
        alias.symlink_to(preview.parent, target_is_directory=True)
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        response = await self.current(descriptor["thumbnail_url"])
        self.assertEqual(response.status, 200, await response.read())
        self.assertEqual(await response.read(), preview.read_bytes())
        make_png(self.temp_dir / "image_thumb.png")
        alias.unlink()
        alias.symlink_to(self.temp_dir, target_is_directory=True)
        response = await self.current(descriptor["thumbnail_url"])
        self.assert_error(response, await response.json(), 403, "not-authorized")

    async def test_disappearance_before_open_is_missing_and_closes_handle(
        self,
    ):
        path = make_png(self.output_dir / "image.png")
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        mod = importlib.import_module("py.api.history_current")
        opened = mod._open_current_target

        def removed(target, *args, **kwargs):
            path.unlink()
            return opened(target, *args, **kwargs)

        with patch.object(mod, "_open_current_target", side_effect=removed):
            response = await self.current(descriptor["url"])
            self.assert_error(response, await response.json(), 404, "not-found")

    async def test_unexpected_inspection_failure_is_bounded_and_does_not_log_paths(
        self,
    ):
        make_png(self.output_dir / "image.png")
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        private = "SECRET_PROMPT /private/root/image.png"
        with patch(
            "py.api.inspection_service.read_inspection_file",
            side_effect=RuntimeError(private),
        ):
            with self.assertLogs("prompt_manager.api", "WARNING") as logs:
                response = await self.current(descriptor["inspection_url"])
                self.assertEqual(response.content_type, "application/json")
                body = await response.json()
        self.assert_error(response, body, 503, "storage-unavailable")
        self.assertNotIn(private, json.dumps(body) + " ".join(logs.output))

    async def test_changed_epoch_or_scope_wins_over_failed_metadata_observation(self):
        png(self.output_dir / "image.png", prompt=graph())
        service = importlib.import_module("py.api.inspection_service")
        read = service.read_inspection_file
        for item, code in enumerate(("history-changed", "candidate-stale")):
            with self.subTest(code=code):
                start = self.capture([self.ref()], item=item)
                descriptor = await self.media(start)
                source = self.restore_fixture()

                def failed(path):
                    read(path)
                    if code == "history-changed":
                        self.api.db.model.restore_from_file(source)
                    else:
                        self.host_output = str(self.temp_dir)
                    raise RuntimeError("private observation failure")

                with patch.object(service, "read_inspection_file", side_effect=failed):
                    response = await self.current(descriptor["inspection_url"])
                    self.assert_error(response, await response.json(), 409, code)

    async def test_current_media_open_io_failure_is_retryable_without_private_paths(
        self,
    ):
        make_png(self.output_dir / "image.png")
        start = self.capture([self.ref()])
        descriptor = await self.media(start)
        response = await self.current(descriptor["url"])
        self.assertEqual(response.status, 200)
        await response.read()
        with patch(
            "py.api.history_current.os.open",
            side_effect=OSError(errno.EIO, "private/root"),
        ):
            response = await self.current(descriptor["url"])
            body = await response.json()
            self.assert_error(response, body, 503, "storage-unavailable")
            self.assertEqual(body["recovery"], {"action": "retry-read"})
