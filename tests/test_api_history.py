"""History HTTP contracts use disposable SQLite, exact cursors and restore fences."""

import asyncio
import base64
import copy
import importlib
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager, ExitStack
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import FormData, web

try:
    from test_api_images import ImageAPITestCase
except ImportError:
    from tests.test_api_images import ImageAPITestCase

from database.operations import PromptDatabase
from py.api.library import _encode, _scope
from tests.test_execution_context_database import bundle, snapshot
from tests.test_execution_history_database import JOB, SESSION
from utils.execution_contexts import ContextBundle, RecipeContext, TextEvidence
from utils.execution_history_events import make_finish, make_start

OTHER_SESSION = "aaaaaaaa-3333-4333-8333-333333333333"
MISSING_RUN = "bbbbbbbb-4444-4444-8444-444444444444"


class HistoryAPITests(ImageAPITestCase):
    async def tearDownAsync(self):
        if hasattr(self.api, "close_history_reads"):
            self.api.close_history_reads()
        await super().tearDownAsync()

    def capture(
        self, item=0, time=100, session=SESSION, context=True, negative="negative"
    ):
        start = make_start(
            session,
            ((0, JOB, {}, {}), item),
            self.api.db.get_execution_epoch(),
            time,
        )
        self.api.db.write_execution_start(start)
        self.api.db.write_execution_finish(
            make_finish(
                session, item, start.storage_epoch, SimpleNamespace(messages=[]), time
            )
        )
        if context:
            self.api.db.write_execution_contexts(
                start.run_id, start.storage_epoch, bundle(negative)
            )
        return start

    async def window(self, positive, params=None):
        response = await self.client.get(
            f"/prompt_manager/library/{positive}/history", params=params
        )
        self.assertEqual(
            response.content_type, "application/json", "history route missing"
        )
        return response, await response.json()

    async def detail(self, run, params=None):
        response = await self.client.get(
            f"/prompt_manager/history/runs/{run}", params=params
        )
        self.assertEqual(
            response.content_type, "application/json", "history route missing"
        )
        return response, await response.json()

    def assert_error(self, response, body, status, code, recovery=None):
        self.assertEqual(response.status, status, body)
        self.assertEqual(
            body,
            {
                "success": False,
                "history_version": 1,
                "code": code,
                "error": body["error"],
                "recovery": recovery,
            },
        )
        self.assertIsInstance(body["error"], str)
        self.assertLessEqual(len(body["error"]), 160)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertNotIn(str(self.comfy_dir), json.dumps(body))

    def cursor_payload(self, token):
        decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        return json.loads(decoded[32:])["key"]

    def signed(self, payload, kind="history-window"):
        return _encode(payload, _scope({"kind": kind}))

    async def test_window_defaults_bounds_and_no_context_recomposition(self):
        positive = self._save_prompt("positive")
        starts = [self.capture(i, i) for i in range(42)]
        with patch.object(
            self.api.db,
            "get_execution_contexts",
            side_effect=AssertionError("full contexts"),
        ):
            response, body = await self.window(positive)
        self.assertEqual(response.status, 200, body)
        self.assertEqual(body["history_version"], 1)
        self.assertEqual(body["history_epoch"], starts[0].storage_epoch)
        self.assertEqual(
            [r["run_id"] for r in body["items"]], [s.run_id for s in starts[2:]]
        )
        self.assertEqual(body["recorded_run_count"], 42)
        self.assertEqual(body["group"]["relation"], "exact_frozen_raw_text")
        self.assertEqual(len(body["context_summaries"]), 1)
        self.assertTrue(body["has_more"])
        self.assertIsInstance(body["next_cursor"], str)
        self.assertLessEqual(len(body["next_cursor"].encode()), 4096)
        self.assertNotIn("next_position", body)
        for limit, count in ((1, 1), (100, 42)):
            response, page = await self.window(positive, {"limit": str(limit)})
            self.assertEqual(response.status, 200)
            self.assertEqual(len(page["items"]), count)
        for params in (
            [("limit", "1"), ("limit", "2")],
            {"limit": "0"},
            {"limit": "101"},
            {"limit": "true"},
            {"limit": "false"},
            {"limit": "1.0"},
            {"limit": "01"},
            {"limit": "-1"},
            {"limit": ""},
            [("cursor", "a"), ("cursor", "b")],
        ):
            response, error = await self.window(positive, params)
            self.assert_error(response, error, 400, "invalid-request")

    async def test_exact_detail_preserves_frozen_roles_nulls_evidence_and_navigation(
        self,
    ):
        pair = self.api.db.save_prompt_pair("positive", "negative")
        positive = self.api.db.get_prompt_pair(pair)["positive_prompt_id"]
        start = self.capture(9007199254740993, 2**63 - 1)
        response, body = await self.detail(start.run_id, {"epoch": start.storage_epoch})
        self.assertEqual(response.status, 200, body)
        self.assertEqual(body["run"]["queue_item_id"], "9007199254740993")
        self.assertEqual(body["run"]["observed_dequeue_at"], "9223372036854775807")
        self.assertEqual(body["run"]["observed_callback_at"], "9223372036854775807")
        self.assertIsNone(body["run"]["executor_started_at"])
        context = body["contexts"][0]
        self.assertEqual(
            context["positive"],
            {"raw": "positive", "effective": "positive", "prepend": "", "append": ""},
        )
        self.assertEqual(
            context["negative"],
            {"raw": "negative", "effective": "negative", "prepend": "", "append": ""},
        )
        self.assertEqual(context["reference_epoch"], start.storage_epoch)
        self.assertEqual(
            context["navigation"],
            {
                "positive_available": True,
                "negative_available": True,
                "pair_available": True,
            },
        )
        for key in ("positive_snapshot_id", "negative_snapshot_id", "context_id"):
            self.assertRegex(context[key], r"^[0-9a-f]{64}$")
        self.assertEqual(
            context["evidence"]["positive"]["raw"]["provenance"], "recorded"
        )
        with self.api.db.model.get_connection() as conn:
            conn.execute("UPDATE prompts SET text='edited' WHERE id=?", (positive,))
        _, changed = await self.detail(start.run_id)
        self.assertEqual(changed["contexts"][0]["positive"], context["positive"])
        self.assertFalse(changed["contexts"][0]["navigation"]["positive_available"])
        self.assertFalse(changed["contexts"][0]["navigation"]["pair_available"])
        bare = self.capture(1, context=False)
        response, uncaptured = await self.detail(bare.run_id)
        self.assertEqual(response.status, 200)
        self.assertEqual(uncaptured["context_capture"]["state"], "not_captured")
        self.assertEqual(uncaptured["contexts"], [])
        self.assertIsNone(uncaptured["output_summary"]["retained_occurrence_count"])

    async def test_missing_and_invalid_ids_epochs_and_duplicate_queries(self):
        response, body = await self.window(999999)
        self.assert_error(response, body, 404, "not-found")
        response, body = await self.detail(MISSING_RUN)
        self.assert_error(response, body, 404, "not-found")
        for identity in ("0", "-1", "true", "9223372036854775808", "01"):
            response, body = await self.window(identity)
            self.assert_error(response, body, 400, "invalid-request")
        for run in ("not-a-uuid", MISSING_RUN.upper()):
            response, body = await self.detail(run)
            self.assert_error(response, body, 400, "invalid-request")
        start = self.capture()
        for params in (
            {"epoch": ""},
            {"epoch": "not-a-uuid"},
            [("epoch", start.storage_epoch), ("epoch", start.storage_epoch)],
        ):
            response, body = await self.detail(start.run_id, params)
            self.assert_error(response, body, 400, "invalid-request")
        response, body = await self.detail(start.run_id, {"epoch": OTHER_SESSION})
        self.assert_error(
            response, body, 409, "history-changed", {"action": "refresh-history"}
        )

    async def test_nullable_frozen_fields_and_every_recorded_time_remain_exact(self):
        start = make_start(
            SESSION,
            ((0, JOB, {}, {}), 9007199254740993),
            self.api.db.get_execution_epoch(),
            9007199254740993,
        )
        self.api.db.write_execution_start(start)
        nullable = replace(
            snapshot("positive"),
            effective=TextEvidence(None, "unknown", "unknown", "missing"),
        )
        self.api.db.write_execution_contexts(
            start.run_id,
            start.storage_epoch,
            ContextBundle(
                (RecipeContext("7", nullable, None, "inferred"),),
                "partial",
                "captured",
            ),
        )
        self.api.db.write_execution_finish(
            make_finish(
                SESSION,
                start.queue_item_id,
                start.storage_epoch,
                SimpleNamespace(
                    messages=[
                        (
                            "execution_start",
                            {"prompt_id": JOB, "timestamp": 9007199254740993},
                        ),
                        (
                            "execution_success",
                            {"prompt_id": JOB, "timestamp": 2**63 - 1},
                        ),
                    ]
                ),
                2**63 - 1,
            )
        )
        response, body = await self.detail(start.run_id)
        self.assertEqual(response.status, 200)
        self.assertEqual(body["run"]["executor_started_at"], "9007199254740993")
        self.assertEqual(body["run"]["terminal_event_at"], "9223372036854775807")
        self.assertEqual(body["run"]["observed_callback_at"], "9223372036854775807")
        context = body["contexts"][0]
        self.assertEqual(
            context["positive"],
            {"raw": "positive", "effective": None, "prepend": "", "append": ""},
        )
        self.assertIsNone(context["negative"])
        self.assertIsNone(context["negative_snapshot_id"])
        self.assertEqual(
            context["evidence"]["positive"]["effective"]["provenance"], "unknown"
        )

    async def test_rf2_decimal_keysets_survive_deletion_and_delayed_capture(
        self,
    ):
        positive = self._save_prompt("positive")
        low = self.capture(0, 100)
        delayed_old = self.capture(1, 100, context=False)
        middle = self.capture(9007199254740993, 100)
        deleted = self.capture(2**63 - 1, 100)
        delayed_passed = self.capture(0, 100, OTHER_SESSION, context=False)
        upper = self.capture(1, 100, OTHER_SESSION)
        response, first = await self.window(positive, {"limit": "2"})
        self.assertEqual(response.status, 200, first)
        self.assertEqual(
            [r["run_id"] for r in first["items"]], [deleted.run_id, upper.run_id]
        )
        self.assertEqual(first["items"][0]["queue_item_id"], "9223372036854775807")
        self.assertEqual(first["recorded_run_count"], 4)
        payload = self.cursor_payload(first["next_cursor"])
        self.assertEqual(
            set(payload),
            {
                "version",
                "epoch",
                "positive_id",
                "raw_text_id",
                "direction",
                "upper",
                "before",
            },
        )
        self.assertEqual(payload["direction"], "older")
        self.assertEqual(payload["positive_id"], str(positive))
        self.assertEqual(
            payload["before"], ["100", SESSION, "9223372036854775807", deleted.run_id]
        )
        with self.api.db.model.get_connection() as conn:
            conn.execute(
                "DELETE FROM execution_run_contexts WHERE run_id=?", (deleted.run_id,)
            )
            conn.execute(
                "DELETE FROM execution_context_capture WHERE run_id=?",
                (deleted.run_id,),
            )
            conn.execute("DELETE FROM execution_runs WHERE run_id=?", (deleted.run_id,))
        newer = self.capture(7, 101)
        for start in (delayed_old, delayed_passed):
            self.api.db.write_execution_contexts(
                start.run_id, start.storage_epoch, bundle()
            )
        _, second = await self.window(
            positive, {"limit": "2", "cursor": first["next_cursor"]}
        )
        self.assertEqual(
            [r["run_id"] for r in second["items"]], [delayed_old.run_id, middle.run_id]
        )
        self.assertEqual(second["items"][1]["queue_item_id"], "9007199254740993")
        self.assertEqual(second["recorded_run_count"], 5)
        _, last = await self.window(
            positive, {"limit": "2", "cursor": second["next_cursor"]}
        )
        self.assertEqual([r["run_id"] for r in last["items"]], [low.run_id])
        self.assertFalse(last["has_more"])
        self.assertIsNone(last["next_cursor"])
        pages = last["items"] + second["items"] + first["items"]
        keys = [
            (
                int(r["observed_dequeue_at"]),
                r["session_id"],
                int(r["queue_item_id"]),
                r["run_id"],
            )
            for r in pages
        ]
        self.assertEqual(keys, sorted(set(keys)))
        _, refreshed = await self.window(positive)
        self.assertEqual(refreshed["recorded_run_count"], 6)
        self.assertIn(newer.run_id, [r["run_id"] for r in refreshed["items"]])
        self.assertIn(delayed_passed.run_id, [r["run_id"] for r in refreshed["items"]])

    async def test_cursor_authentication_strict_shape_and_domain_precede_projection(
        self,
    ):
        positive = self._save_prompt("positive")
        for i in range(3):
            self.capture(i)
        _, first = await self.window(positive, {"limit": "1"})
        payload = self.cursor_payload(first["next_cursor"])
        changed = []
        for key, values in {
            "version": [2, True, "1"],
            "epoch": [OTHER_SESSION.upper(), "x"],
            "positive_id": [str(positive + 1), positive, "01", True],
            "raw_text_id": ["z" * 64, "A" * 64, None],
            "direction": ["newer", None],
            "upper": [
                [],
                ["1", SESSION, "1"],
                ["01", SESSION, "1", MISSING_RUN],
                [1, SESSION, "1", MISSING_RUN],
                ["1", SESSION, "9223372036854775808", MISSING_RUN],
                ["1", OTHER_SESSION.upper(), "1", MISSING_RUN],
            ],
            "before": [
                False,
                ["-1", SESSION, "0", MISSING_RUN],
                ["1.0", SESSION, "0", MISSING_RUN],
                ["1", SESSION, True, MISSING_RUN],
            ],
        }.items():
            for value in values:
                changed.append({**payload, key: value})
        changed.extend(
            [
                {key: value for key, value in payload.items() if key != "before"},
                {**payload, "private": "path"},
            ]
        )
        tokens = [
            "tampered",
            "!",
            "a" * 4097,
            "猫",
            self.signed(payload, "history-outputs"),
            _encode(payload, _scope({"kind": "history-window"}), name=True),
        ]
        tokens += [self.signed(value) for value in changed]
        token = first["next_cursor"]
        tokens.append(("A" if token[0] != "A" else "B") + token[1:])
        with patch.object(
            self.api.db,
            "list_history_window",
            side_effect=AssertionError("projection must not run"),
        ):
            for token in tokens:
                response, body = await self.window(positive, {"cursor": token})
                self.assert_error(response, body, 400, "invalid-cursor")
        with self.api.db.model.get_connection() as conn:
            conn.execute("UPDATE prompts SET text='different' WHERE id=?", (positive,))
        response, body = await self.window(positive, {"cursor": first["next_cursor"]})
        self.assert_error(response, body, 400, "invalid-cursor")

    async def test_valid_cursor_cannot_move_to_other_positive_or_raw_scope(self):
        positive = self._save_prompt("positive")
        duplicate = self._save_prompt("same saved row with another text")
        with self.api.db.model.get_connection() as conn:
            conn.execute("UPDATE prompts SET text='positive' WHERE id=?", (duplicate,))
        self.capture(0)
        self.capture(1)
        _, first = await self.window(positive, {"limit": "1"})
        with patch.object(
            self.api.db, "list_history_window", side_effect=AssertionError("projection")
        ):
            response, body = await self.window(
                duplicate, {"cursor": first["next_cursor"]}
            )
        self.assert_error(response, body, 400, "invalid-cursor")
        payload = self.cursor_payload(first["next_cursor"])
        response, body = await self.window(
            positive, {"cursor": self.signed({**payload, "raw_text_id": "0" * 64})}
        )
        self.assert_error(response, body, 400, "invalid-cursor")

    async def test_each_read_has_one_executor_job_and_ends_snapshot_before_validation(
        self,
    ):
        positive = self._save_prompt("positive")
        start = self.capture()
        fence = self.api._history_fence()
        validate = fence.validate
        executor = self.api._run_in_executor
        jobs = []
        validated = []

        def after_snapshot(epoch, generation):
            self.assertFalse(self.api.db.model.get_connection().in_transaction)
            validated.append(epoch)
            validate(epoch, generation)

        async def tracked_executor(func, *args, **kwargs):
            jobs.append(func)
            return await executor(func, *args, **kwargs)

        with patch.object(fence, "validate", side_effect=after_snapshot):
            with patch.object(
                self.api, "_run_in_executor", side_effect=tracked_executor
            ):
                await self.window(positive)
                await self.detail(start.run_id)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(validated, [start.storage_epoch, start.storage_epoch])

    async def test_final_storage_failure_and_invalid_serialization_cannot_succeed(self):
        positive = self._save_prompt("positive")
        start = self.capture()
        fence = self.api._history_fence()
        with patch.object(
            fence, "validate", side_effect=sqlite3.OperationalError("private/path")
        ):
            for read in (
                lambda: self.window(positive),
                lambda: self.detail(start.run_id),
            ):
                response, body = await read()
                self.assert_error(
                    response, body, 503, "storage-unavailable", {"action": "retry-read"}
                )
        original = self.api.db.get_history_run

        def invalid_payload(run_id):
            result = original(run_id)
            result["contexts"][0]["positive"]["raw"] = float("nan")
            return result

        with patch.object(self.api.db, "get_history_run", side_effect=invalid_payload):
            response, body = await self.detail(start.run_id)
        self.assert_error(
            response, body, 503, "storage-unavailable", {"action": "retry-read"}
        )

    async def test_cursor_decoder_recursion_failure_is_bounded_before_projection(self):
        positive = self._save_prompt("positive")
        raw = (
            b"x" * 32
            + b'{"scope":"wrong","key":'
            + b"[" * 1100
            + b"0"
            + b"]" * 1100
            + b"}"
        )
        token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        self.assertLess(len(token), 4096)
        module = importlib.import_module("py.api.history")
        original_loads = json.loads

        def supported_runtime_loads(value, *args, **kwargs):
            # Python 3.10's JSON decoder raises this for the actual deep token.
            if type(value) is bytes and value == raw[32:]:
                raise RecursionError("nested JSON")
            return original_loads(value, *args, **kwargs)

        with patch.object(
            self.api.db, "list_history_window", side_effect=AssertionError("projection")
        ):
            with patch.object(
                module.json, "loads", side_effect=supported_runtime_loads
            ):
                response, body = await self.window(positive, {"cursor": token})
        self.assert_error(response, body, 400, "invalid-cursor")

    async def test_epoch_change_is_409_but_tampering_remains_400(self):
        positive = self._save_prompt("positive")
        self.capture(0)
        self.capture(1)
        _, first = await self.window(positive, {"limit": "1"})
        source = str(self.comfy_dir / "restore.db")
        self.assertTrue(self.api.db.model.backup_database(source))
        self.api.db.model.restore_from_file(source)
        response, body = await self.window(positive, {"cursor": first["next_cursor"]})
        self.assert_error(
            response, body, 409, "history-changed", {"action": "refresh-history"}
        )
        response, body = await self.window(
            positive, {"cursor": "bad" + first["next_cursor"]}
        )
        self.assert_error(response, body, 400, "invalid-cursor")

    async def test_real_control_heavy_detail_uses_actual_utf8_budget_without_mutation(
        self,
    ):
        self._save_prompt("positive")
        negative = '猫😀\0\x01\n"\\' * 1500
        start = self.capture(negative=negative)
        response, body = await self.detail(start.run_id)
        self.assertEqual(response.status, 200, body)
        encoded = await response.read()
        self.assertEqual(body["contexts"][0]["negative"]["raw"], negative)
        self.assertIn("猫".encode(), encoded)
        self.assertIn(b"\\u0000", encoded)
        self.assertGreater(len(encoded), len(json.dumps(body, ensure_ascii=False)))
        module = importlib.import_module("py.api.history")
        with patch.object(module, "_RUN_MAX_BYTES", len(encoded)):
            at, same = await self.detail(start.run_id)
            self.assertEqual(at.status, 200)
            self.assertEqual(await at.read(), encoded)
            self.assertEqual(same, body)
        with patch.object(module, "_RUN_MAX_BYTES", len(encoded) - 1):
            above, error = await self.detail(start.run_id)
            self.assert_error(above, error, 422, "response-limit")
            self.assertNotIn("contexts", error)
        _, after = await self.detail(start.run_id)
        self.assertEqual(after, body)

    async def test_projected_detail_fixture_pins_defensive_eight_mib_encoder_boundary(
        self,
    ):
        start = self.capture()
        response, detail = await self.detail(start.run_id)
        self.assertEqual(response.status, 200, detail)

        def check_projection_limit():
            # A projected-detail fixture tests the defensive transport ceiling;
            # valid stored captures retain their existing one-MiB text allowance.
            projected = copy.deepcopy(detail)
            projected["contexts"][0]["positive"]["raw"] = "猫😀\0"
            baseline = json.dumps(
                projected, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode()
            projected["contexts"][0]["positive"]["raw"] += "x" * (
                8 * 1024 * 1024 - len(baseline)
            )
            module = importlib.import_module("py.api.history")
            encoded = module._serialize_history(projected, max_bytes=8 * 1024 * 1024)
            self.assertEqual(len(encoded), 8 * 1024 * 1024)
            self.assertEqual(json.loads(encoded), projected)
            projected["contexts"][0]["positive"]["raw"] += "x"
            with self.assertRaises(module.HistoryReadError) as error:
                module._serialize_history(projected, max_bytes=8 * 1024 * 1024)
            self.assertEqual(error.exception.code, "response-limit")

        await self.api._run_in_executor(check_projection_limit)

    async def test_storage_failure_is_bounded_retry_without_logging_private_values(
        self,
    ):
        positive = self._save_prompt("positive")
        start = self.capture()
        private = "SECRET_PROMPT /private/data/secret.db"
        for method, read in (
            ("list_history_window", lambda: self.window(positive)),
            ("get_history_run", lambda: self.detail(start.run_id)),
        ):
            for exc in (
                sqlite3.OperationalError("busy " + private),
                sqlite3.DatabaseError("corrupt " + private),
                OSError(private),
            ):
                with patch.object(self.api.db, method, side_effect=exc):
                    with self.assertLogs("prompt_manager.api", "WARNING") as logs:
                        response, body = await read()
                self.assert_error(
                    response, body, 503, "storage-unavailable", {"action": "retry-read"}
                )
                self.assertNotIn(private, json.dumps(body) + " ".join(logs.output))

    async def test_snapshot_restore_before_notification_rejects_both_public_reads(self):
        positive = self._save_prompt("positive")
        start = self.capture()
        source = str(self.comfy_dir / "source.db")
        self.assertTrue(self.api.db.model.backup_database(source))
        writer = PromptDatabase(self.api.db.model.db_path)
        self.addCleanup(writer.close_all)
        for method, read in (
            ("list_history_window", lambda: self.window(positive)),
            ("get_history_run", lambda: self.detail(start.run_id)),
        ):
            original = getattr(self.api.db, method)

            def during_snapshot(*args, **kwargs):
                result = original(*args, **kwargs)
                with patch("database.execution_history.notify_history_restore"):
                    writer.model.restore_from_file(source)
                return result

            with patch.object(self.api.db, method, side_effect=during_snapshot):
                response, body = await read()
            self.assert_error(
                response, body, 409, "history-changed", {"action": "refresh-history"}
            )

    async def test_one_lazy_subscription_cleanup_and_no_reinitialization(self):
        import database.execution_history as observers

        positive = self._save_prompt("positive")
        start = self.capture()
        identity = str(Path(self.api.db.model.db_path).resolve())
        self.assertNotIn(identity, observers._restore_observers)
        executor = self.api._run_in_executor

        async def subscribed_before_executor(func, *args, **kwargs):
            self.assertEqual(len(observers._restore_observers[identity]), 1)
            return await executor(func, *args, **kwargs)

        with patch.object(
            self.api, "_run_in_executor", side_effect=subscribed_before_executor
        ):
            await self.window(positive)
            await self.detail(start.run_id)
            await self.window(positive)
        self.assertEqual(len(observers._restore_observers[identity]), 1)
        self.api.close_history_reads()
        self.api.close_history_reads()
        self.assertNotIn(identity, observers._restore_observers)
        response, body = await self.detail(start.run_id)
        self.assert_error(
            response, body, 503, "storage-unavailable", {"action": "retry-read"}
        )
        self.assertNotIn(identity, observers._restore_observers)

    async def test_cleanup_before_first_read_does_not_subscribe(self):
        import database.execution_history as observers

        identity = str(Path(self.api.db.model.db_path).resolve())
        self.assertTrue(
            callable(getattr(self.api, "close_history_reads", None)),
            "API needs permanent history cleanup",
        )
        self.api.close_history_reads()
        response, body = await self.window(self._save_prompt("positive"))
        self.assert_error(
            response, body, 503, "storage-unavailable", {"action": "retry-read"}
        )
        self.assertNotIn(identity, observers._restore_observers)

    async def test_multipart_restore_then_read_uses_new_owner_and_current_epoch(self):
        positive = self._save_prompt("positive")
        start = self.capture()
        _, before = await self.window(positive)
        old_db = self.api.db
        source = str(self.comfy_dir / "upload.db")
        self.assertTrue(old_db.model.backup_database(source))
        form = FormData()
        form.add_field("database_file", Path(source).read_bytes(), filename="upload.db")
        response = await self.client.post("/prompt_manager/restore", data=form)
        self.assertEqual(response.status, 200, await response.text())
        self.assertIsNot(self.api.db, old_db)
        self.assertEqual(len(old_db.model._connections), 0)

        with patch.object(
            old_db.model, "get_connection", side_effect=AssertionError("retired owner")
        ):
            response, after = await self.window(positive)
            self.assertEqual(response.status, 200, after)
            response, detail = await self.detail(start.run_id)
            self.assertEqual(response.status, 200, detail)
        self.assertNotEqual(before["history_epoch"], after["history_epoch"])
        self.assertEqual(detail["history_epoch"], after["history_epoch"])
        self.assertEqual(detail["run"]["storage_epoch"], start.storage_epoch)
        self.assertEqual(detail["contexts"][0]["reference_epoch"], start.storage_epoch)
        self.assertEqual(
            detail["contexts"][0]["navigation"],
            {
                "positive_available": False,
                "negative_available": False,
                "pair_available": False,
            },
        )
        self.assertEqual(len(old_db.model._connections), 0)

    async def assert_projection_releases_retired_owner(
        self, route, *, acquisition=False, failure=None
    ):
        positive = self._save_prompt("positive")
        start = self.capture()
        await self.window(positive)
        old_db = self.api.db
        self.addCleanup(old_db.close_all)
        source = self.comfy_dir / "queued-restore.db"
        self.assertTrue(old_db.model.backup_database(str(source)))
        upload = source.read_bytes()
        reached = asyncio.Event()
        release = threading.Event() if acquisition else asyncio.Event()
        executor = self.api._run_in_executor
        connection = old_db.model.get_connection
        history = importlib.import_module("py.api.history")
        serialize = history._serialize_history
        fence = self.api._history_fence()
        validate = fence.validate
        loop = asyncio.get_running_loop()
        armed = True

        async def queued(func, *args, **kwargs):
            nonlocal armed
            if armed and func.__name__ == "project":
                armed = False
                reached.set()
                await asyncio.wait_for(release.wait(), 5)
            return await executor(func, *args, **kwargs)

        def selected_connection():
            nonlocal armed
            if armed:
                armed = False
                loop.call_soon_threadsafe(reached.set)
                if not release.wait(5):
                    raise AssertionError("connection-acquisition barrier timed out")
            return connection()

        @contextmanager
        def failed_snapshot(expected_epoch=None):
            old_db.model.get_connection()
            raise sqlite3.OperationalError("snapshot unavailable")
            yield

        def released_before_serialization(payload, **kwargs):
            self.assertEqual(len(old_db.model._connections), 0)
            return serialize(payload, **kwargs)

        def released_before_validation(epoch, generation):
            self.assertEqual(len(old_db.model._connections), 0)
            return validate(epoch, generation)

        with ExitStack() as patches:
            patches.enter_context(
                patch.object(
                    history,
                    "_serialize_history",
                    side_effect=released_before_serialization,
                )
            )
            patches.enter_context(
                patch.object(fence, "validate", side_effect=released_before_validation)
            )
            if acquisition:
                patches.enter_context(
                    patch.object(
                        old_db.model, "get_connection", side_effect=selected_connection
                    )
                )
            else:
                patches.enter_context(
                    patch.object(self.api, "_run_in_executor", side_effect=queued)
                )
            if failure == "snapshot":
                patches.enter_context(
                    patch.object(
                        old_db, "recorded_history_snapshot", side_effect=failed_snapshot
                    )
                )
            elif failure == "lookup":
                patches.enter_context(
                    patch.object(
                        old_db,
                        (
                            "list_history_window"
                            if route == "window"
                            else "get_history_run"
                        ),
                        side_effect=sqlite3.OperationalError("lookup unavailable"),
                    )
                )
            read = (
                self.window(positive)
                if route == "window"
                else self.detail(start.run_id)
            )
            pending = asyncio.create_task(read)
            try:
                await asyncio.wait_for(reached.wait(), 5)
                form = FormData()
                form.add_field("database_file", upload, filename="upload.db")
                restored = await self.client.post("/prompt_manager/restore", data=form)
                self.assertEqual(restored.status, 200, await restored.text())
                self.assertIsNot(self.api.db, old_db)
                self.assertEqual(len(old_db.model._connections), 0)
            finally:
                release.set()
                response, body = await asyncio.wait_for(pending, 5)

        if failure is not None:
            self.assert_error(
                response, body, 503, "storage-unavailable", {"action": "retry-read"}
            )
        elif acquisition:
            self.assert_error(
                response, body, 409, "history-changed", {"action": "refresh-history"}
            )
        else:
            self.assertEqual(response.status, 200, body)
            self.assertEqual(body["history_epoch"], self.api.db.get_execution_epoch())
            self.assertNotEqual(body["history_epoch"], start.storage_epoch)
            run = body["items"][0] if route == "window" else body["run"]
            self.assertEqual(run["run_id"], start.run_id)
            self.assertEqual(run["storage_epoch"], start.storage_epoch)
        self.assertEqual(
            len(old_db.model._connections),
            0,
            "completed projection retained a handle on the API-retired owner",
        )

    async def test_queued_window_across_multipart_restore_closes_retired_handle(self):
        await self.assert_projection_releases_retired_owner("window")

    async def test_queued_detail_across_multipart_restore_closes_retired_handle(self):
        await self.assert_projection_releases_retired_owner("detail")

    async def test_window_retirement_before_connection_acquisition_closes_handle(self):
        await self.assert_projection_releases_retired_owner("window", acquisition=True)

    async def test_detail_retirement_before_connection_acquisition_closes_handle(self):
        await self.assert_projection_releases_retired_owner("detail", acquisition=True)

    async def test_retired_window_handle_closes_after_snapshot_and_lookup_failures(
        self,
    ):
        for failure in ("snapshot", "lookup"):
            with self.subTest(failure=failure):
                await self.assert_projection_releases_retired_owner(
                    "window", acquisition=True, failure=failure
                )

    async def test_retired_detail_handle_closes_after_snapshot_and_lookup_failures(
        self,
    ):
        for failure in ("snapshot", "lookup"):
            with self.subTest(failure=failure):
                await self.assert_projection_releases_retired_owner(
                    "detail", acquisition=True, failure=failure
                )


class HistoryHostCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_root_registers_cleanup_on_actual_host_application(self):
        from tests.test_init_package import fake_server_module, load_package

        with tempfile.TemporaryDirectory() as directory:
            server = fake_server_module()
            server.PromptServer.instance.app = web.Application()
            with load_package(
                "history_cleanup_pkg",
                str(Path(directory) / "db.sqlite"),
                directory,
                server,
            ) as (package, host):
                self.assertIn(package.api.cleanup_history_reads, host.app.on_cleanup)
                api = package.api
                self.assertIsNone(getattr(api, "_history_read_fence", None))
                fence = api._history_fence()
                generation = fence.begin()
                epoch = api.db.get_execution_epoch()
                host.app.freeze()
                await host.app.cleanup()
                with self.assertRaises(sqlite3.Error):
                    fence.validate(epoch, generation)
                with self.assertRaises(sqlite3.Error):
                    api._history_fence()
