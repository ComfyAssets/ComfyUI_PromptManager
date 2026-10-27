"""Actual dequeue and completion capture through the owned SQLite worker."""

from collections import namedtuple
import logging
import gc
import contextlib
import io
import os
import tempfile
import threading
import time
import sys
import types
import weakref
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.history_waits import drain_writer
from database import PromptDatabase
from utils import usage_tracking
from utils.execution_history_writer import HistoryWriter

try:
    from utils.execution_history import register_history_hook
except ImportError:
    register_history_hook = None

Status = namedtuple("ExecutionStatus", "status_str completed messages")
JOB = "a0c26da3-eb26-405c-b143-c662a84c3112"
# A cross-thread wait or a duration check fails only past this (#370).
CEILING_SECONDS = 30.0


class HostQueue:
    def __init__(self):
        self.results = []
        self.done_result = object()
        self.done_calls = []

    def get(self, *args, **kwargs):
        return self.results.pop(0) if self.results else None

    def task_done(self, *args, **kwargs):
        self.done_calls.append((args, kwargs))
        return self.done_result


def item(item_id, job=JOB, graph=None, extra=None, sensitive=None):
    return ((0, job, graph or {}, extra or {}, [], sensitive or {}), item_id)


def status(kind="execution_success", job=JOB):
    return Status("success", True, [(kind, {"prompt_id": job, "timestamp": 20})])


class HistoryHooksTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = PromptDatabase(os.path.join(self.directory.name, "history.db"))
        self.addCleanup(self.db.model.reset_schema_cache)
        self.addCleanup(self.db.model.close_all)
        self.queue = HostQueue()
        self.server = SimpleNamespace(prompt_queue=self.queue)
        self.registrations, self.releases = [], []
        self.addCleanup(self.cleanup_registrations)
        for name in ("execution_history_writer", "execution_history"):
            logger = logging.getLogger("prompt_manager." + name)
            previous = logger.handlers, logger.propagate, logger.level
            logger.handlers, logger.propagate, logger.level = (
                [logging.NullHandler()],
                False,
                logging.WARNING,
            )
            self.addCleanup(self.restore_logger, logger, previous)

    def restore_logger(self, logger, previous):
        logger.handlers, logger.propagate, logger.level = previous

    def cleanup_registrations(self):
        for registration in self.registrations:
            registration.writer.invalidate()
        for release in self.releases:
            release.set()
        for registration in self.registrations:
            self.assertTrue(registration.dispose(3), "owned worker leaked")

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            threading.Event().wait(0.005)
        self.fail("finite deadline expired waiting for recorded history")

    def register(self, factory=None, ready=True):
        self.assertTrue(
            callable(register_history_hook), "history registration is absent"
        )
        registration = register_history_hook(self.server, factory or (lambda: self.db))
        self.assertIsNotNone(registration)
        self.registrations.append(registration)
        if ready:
            self.wait_for(lambda: registration.writer.status().accepting)
        return registration

    def dequeue(self, item_id=0, **kwargs):
        result = item(item_id, **kwargs)
        self.queue.results.append(result)
        self.assertIs(self.queue.get(), result)
        return result

    def rows(self):
        return [
            dict(row)
            for row in self.db.model.get_connection()
            .execute("SELECT * FROM execution_runs ORDER BY queue_item_id")
            .fetchall()
        ]

    def drain(self, registration):
        drain_writer(registration.writer, clock=time.monotonic)

    def test_package_history_initializes_when_monitor_and_usage_setup_fail(self):
        try:
            from tests.test_init_package import (
                fake_server_module,
                load_package,
            )
        except ImportError:
            from test_init_package import (
                fake_server_module,
                load_package,
            )
        name = "prompt_manager_pkg_history_independent"
        server = fake_server_module()
        server.PromptServer.instance.prompt_queue = self.queue
        server.PromptServer.instance.app = SimpleNamespace(middlewares=[])

        def reject_usage(_):
            raise RuntimeError("usage registration unavailable")

        server.PromptServer.instance.add_on_prompt_handler = reject_usage
        output = os.path.join(self.directory.name, "output")
        os.makedirs(output)
        monitor_module = types.ModuleType(name + ".utils.image_monitor")
        # Missing monitor import fails before shared database initialization.
        with (
            patch.dict(sys.modules, {monitor_module.__name__: monitor_module}),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertLogs("prompt_manager.init", level="ERROR"),
        ):
            with load_package(name, self.db.model.db_path, output, server) as (
                package,
                _,
            ):
                registration = getattr(package, "_history_registration", None)
                self.assertIsNotNone(
                    registration, "history setup depended on another hook"
                )
                self.assertIsNone(package._global_db)
                self.wait_for(lambda: registration.writer.status().accepting)
                self.dequeue(0)
                self.queue.task_done(0, {}, status())
                self.drain(registration)
                self.assertEqual(self.rows()[0]["outcome"], "succeeded")

    def test_distinct_actual_attempts_for_reused_job_and_cached_rerun(self):
        registration = self.register()
        self.dequeue(0)
        self.queue.task_done(0, {"outputs": {}}, status("execution_error"))
        self.dequeue(1)
        self.queue.task_done(item_id=1, history_result={}, status=status())
        self.drain(registration)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["job_id"] for r in rows], [JOB, JOB])
        self.assertEqual([r["outcome"] for r in rows], ["failed", "succeeded"])
        self.assertNotEqual(rows[0]["run_id"], rows[1]["run_id"])

    def test_later_conflicting_completion_resolves_completed_stored_identity(self):
        registration = self.register()
        self.dequeue(0)
        self.queue.task_done(0, {}, status())
        self.drain(registration)
        self.queue.task_done(0, {}, status("execution_error"))
        self.drain(registration)
        self.assertEqual(self.rows()[0]["outcome"], "unknown")
        self.assertEqual(self.rows()[0]["reason"], "conflicting_terminal")

    def test_repeated_registration_cleanup_and_reregistration_keep_session_and_wrappers(
        self,
    ):
        before = self.queue.get
        calls = []

        def unrelated_before(*a, **k):
            calls.append("before")
            return before(*a, **k)

        self.queue.get = unrelated_before
        first = self.register()
        self.assertIs(register_history_hook(self.server, lambda: self.db), first)
        observed = self.queue.get

        def unrelated_after(*a, **k):
            calls.append("after")
            return observed(*a, **k)

        self.queue.get = unrelated_after
        self.dequeue(0)
        self.queue.task_done(0, {}, status())
        # The writer drains first: this test checks wrappers and the session,
        # not the 2 s bound, which a slow runner can spend on the write (#370).
        self.drain(first)
        self.assertTrue(first.dispose())
        self.assertTrue(first.dispose())
        self.assertIs(self.queue.get, unrelated_after)
        second = self.register()
        self.dequeue(1)
        self.queue.task_done(1, {}, status())
        self.drain(second)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.rows()[0]["session_id"], self.rows()[1]["session_id"])
        self.assertEqual(calls, ["after", "before"] * 2)

    def test_concurrent_handle_capacity_never_reassigns_active_attempts(self):
        registration = self.register()
        self.queue.results.extend(item(index) for index in range(129))
        ready = threading.Barrier(130)
        errors = []

        def dequeue():
            try:
                ready.wait(3)
                self.queue.get()
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=dequeue) for _ in range(129)]
        for thread in threads:
            thread.start()
        ready.wait(3)
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.drain(registration)
        rows = self.rows()
        admitted = {row["queue_item_id"] for row in rows}
        self.assertEqual(len(rows), 128)
        missing = (set(range(129)) - admitted).pop()
        oldest = min(admitted)
        self.queue.task_done(oldest, {}, status())
        self.queue.task_done(missing, {}, status())
        self.drain(registration)
        by_item = {row["queue_item_id"]: row for row in self.rows()}
        self.assertEqual(by_item[oldest]["outcome"], "succeeded")
        self.assertNotIn(missing, by_item)
        self.assertGreater(registration.writer.status().recording_gaps, 0)
        self.dequeue(129)
        self.queue.task_done(129, {}, status())
        self.drain(registration)
        self.assertEqual(self.rows()[-1]["queue_item_id"], 129)

    def test_active_handles_do_not_retain_graph_extra_or_sensitive_envelopes(self):
        class Envelope(dict):
            pass

        registration = self.register()
        graph, extra, sensitive = (
            Envelope({"g": object()}),
            Envelope({"e": object()}),
            Envelope({"s": object()}),
        )
        references = [weakref.ref(value) for value in (graph, extra, sensitive)]
        self.dequeue(0, graph=graph, extra=extra, sensitive=sensitive)
        self.drain(registration)
        del graph, extra, sensitive
        gc.collect()
        self.assertEqual([reference() for reference in references], [None, None, None])
        self.queue.task_done(0, {}, status())
        self.drain(registration)
        self.assertEqual(self.rows()[0]["outcome"], "succeeded")

    def test_capture_uses_no_database_methods_on_the_host_thread(self):
        registration = self.register()
        host_thread = threading.get_ident()
        original = self.db.model.get_connection

        def connection():
            if threading.get_ident() == host_thread:
                self.fail("history queried SQLite on the host thread")
            return original()

        with patch.object(self.db.model, "get_connection", connection):
            self.dequeue(0)
            self.queue.task_done(0, {}, status())
            self.drain(registration)
        self.assertEqual(self.rows()[0]["outcome"], "succeeded")

    def test_capture_never_copies_or_serializes_sensitive_objects(self):
        class Sensitive:
            def __deepcopy__(self, memo):
                raise AssertionError("sensitive copy")

            def __repr__(self):
                raise AssertionError("sensitive serialization")

        registration = self.register()
        secret = Sensitive()
        graph, extra, sensitive = {"graph": secret}, {"extra": secret}, {"auth": secret}
        result = self.dequeue(0, graph=graph, extra=extra, sensitive=sensitive)
        payload = {"outputs": secret, "meta": secret}
        completion = status()
        completion.messages[0][1].update(
            {"traceback": secret, "current_inputs": secret}
        )
        self.assertIs(
            self.queue.task_done(0, payload, completion), self.queue.done_result
        )
        self.drain(registration)
        self.assertIs(result[0][2], graph)
        self.assertIs(result[0][3], extra)
        self.assertIs(result[0][5], sensitive)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["outcome"], "succeeded")
        self.assertEqual(
            set(completion.messages[0][1]),
            {"prompt_id", "timestamp", "traceback", "current_inputs"},
        )

    def test_callbacks_return_while_writer_blocked_and_disposal_is_bounded(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = self.db.write_execution_start

        def blocked(command):
            entered.set()
            release.wait(5)
            return original(command)

        with patch.object(self.db, "write_execution_start", blocked):
            registration = self.register()
            self.dequeue(0)
            # Ceilings, not tight wall clocks (#370): the return values show
            # the bound; a ceiling still fails a call that waits unbounded.
            self.assertTrue(entered.wait(CEILING_SECONDS))
            began = time.monotonic()
            self.queue.task_done(0, {}, status())
            self.dequeue(1)
            self.assertLess(time.monotonic() - began, CEILING_SECONDS)
            began = time.monotonic()
            self.assertFalse(registration.dispose())
            self.assertGreaterEqual(time.monotonic() - began, 1.8)
            self.assertLess(time.monotonic() - began, CEILING_SECONDS)
            release.set()
            # Drain first: the 2 s bound is not under test here (#370).
            self.drain(registration)
            self.assertTrue(registration.dispose(2))

    def test_restore_disables_capture_and_clears_handles_before_fresh_epoch(self):
        try:
            from tests.test_execution_history_writer import ManualClock
        except ImportError:
            from test_execution_history_writer import ManualClock
        clock = ManualClock()
        with patch(
            "utils.execution_history.HistoryWriter",
            lambda factory, **kwargs: HistoryWriter(factory, clock=clock, **kwargs),
        ):
            registration = self.register()
        for index in range(128):
            self.dequeue(index)
        self.drain(registration)
        old_epoch = registration.writer.status().epoch
        backup = os.path.join(self.directory.name, "backup.db")
        self.assertTrue(self.db.model.backup_database(backup))
        self.db.model.restore_from_file(backup)
        self.assertFalse(registration.writer.status().accepting)
        self.assertIsNone(registration.writer.status().epoch)
        self.queue.task_done(0, {}, status())
        self.dequeue(128)
        self.assertGreater(registration.writer.status().recording_gaps, 0)
        clock.advance(5)
        self.wait_for(lambda: registration.writer.status().accepting)
        self.assertNotEqual(registration.writer.status().epoch, old_epoch)
        # A late final descriptor cannot complete rows from the restored epoch.
        self.queue.task_done(0, {}, status())
        self.dequeue(129)
        self.queue.task_done(129, {}, status())
        self.drain(registration)
        rows = self.rows()
        self.assertEqual(len(rows), 129)
        self.assertEqual(rows[0]["outcome"], "observed")
        self.assertEqual(rows[-1]["queue_item_id"], 129)
        self.assertEqual(rows[-1]["outcome"], "succeeded")

    def test_unavailable_database_and_invalid_identity_never_escape_host(self):
        entered = threading.Event()

        def broken():
            entered.set()
            raise RuntimeError("secret unavailable database")

        registration = self.register(broken, ready=False)
        self.assertTrue(entered.wait(1))
        result = self.dequeue(0)
        self.assertEqual(result[1], 0)
        self.queue.task_done(0, {}, status())
        self.assertEqual(self.rows(), [])
        self.assertGreater(registration.writer.status().recording_gaps, 0)
        self.assertTrue(registration.dispose())
        registration = self.register()
        self.dequeue(0, job="invalid id")
        self.queue.task_done(True, {}, object())
        self.drain(registration)
        self.assertEqual(self.rows(), [])
        self.assertGreater(registration.writer.status().recording_gaps, 0)

    def test_invalid_evidence_is_recorded_unknown_and_raising_status_is_isolated(self):
        registration = self.register()
        self.dequeue(0)
        self.queue.task_done(0, {}, object())
        self.drain(registration)
        self.assertEqual(self.rows()[0]["outcome"], "unknown")
        self.assertEqual(self.rows()[0]["reason"], "invalid_evidence")

        class BrokenStatus:
            @property
            def messages(self):
                raise RuntimeError("secret failure")

        self.dequeue(1)
        self.queue.task_done(1, {}, BrokenStatus())
        self.drain(registration)
        self.assertEqual(self.rows()[1]["outcome"], "observed")
        self.assertGreater(registration.writer.status().recording_gaps, 0)

    def test_unsupported_queue_does_not_create_writer_or_submit_fallback(self):
        self.assertTrue(
            callable(register_history_hook), "history registration is absent"
        )
        for host in (
            object(),
            SimpleNamespace(prompt_queue=SimpleNamespace(get=lambda: None)),
        ):
            self.assertIsNone(
                register_history_hook(host, lambda: self.fail("DB touched"))
            )

    def test_existing_usage_callback_counts_once_alongside_history(self):
        registration = self.register()
        seen = []
        self.assertTrue(usage_tracking._wrap_queue_get(self.queue, seen.append))
        self.assertFalse(usage_tracking._wrap_queue_get(self.queue, seen.append))
        self.dequeue(0)
        self.queue.task_done(0, {}, status())
        self.drain(registration)
        self.assertEqual(seen, [JOB])
        self.assertEqual(len(self.rows()), 1)

    def test_fixture_cleanup_discards_saturation_before_release_and_database_close(
        self,
    ):
        from utils.execution_history_events import make_start

        entered, release, rescue = (threading.Event() for _ in range(3))
        self.releases.append(release)
        pending_at_release, writes = [], []
        original = self.db.write_execution_start

        def blocked(command):
            entered.set()
            release.wait(5)
            pending = registration.writer.status().pending
            pending_at_release.append(pending)
            # Model a slow drain without executing 1,024 SQLite transactions.
            if pending > 1:
                rescue.wait(5)
            writes.append(command.run_id)
            return original(command)

        with patch.object(self.db, "write_execution_start", blocked):
            registration = self.register()
            try:
                self.dequeue(1)
                self.assertTrue(entered.wait(1))
                epoch = registration.writer.status().epoch
                for i in range(1023):
                    command = make_start(
                        registration._session_id, item(1000 + i), epoch, 0
                    )
                    self.assertTrue(registration.writer.submit(command))
                self.assertEqual(registration.writer.status().pending, 1024)
                before = time.monotonic()
                self.cleanup_registrations()
                self.assertLess(time.monotonic() - before, 2)
                self.assertEqual(pending_at_release, [1])
                self.assertEqual(len(writes), 1)
                self.assertFalse(registration.writer.status().alive)
                self.assertEqual(registration.writer.status().pending, 0)
                self.db.model.close_all()
            finally:
                registration.writer.invalidate()
                rescue.set()
                release.set()
                self.assertTrue(
                    registration.dispose(3), "regression rescue leaked worker"
                )

    def test_rejected_new_start_revokes_previous_worker_capability(self):
        from utils.execution_context_capture import active_capture
        from utils.execution_history_events import make_start

        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = self.db.write_execution_start

        def blocked(command):
            entered.set()
            release.wait(5)
            return original(command)

        with patch.object(self.db, "write_execution_start", blocked):
            registration = self.register()
            self.dequeue(1)
            self.assertTrue(entered.wait(1))
            previous = active_capture()
            self.assertIsNotNone(previous)
            epoch = registration.writer.status().epoch
            for i in range(1023):
                command = make_start(registration._session_id, item(1000 + i), epoch, 0)
                self.assertTrue(registration.writer.submit(command))
            self.assertEqual(registration.writer.status().pending, 1024)
            self.dequeue(2)
            self.assertIsNone(active_capture())
            self.assertFalse(previous[0].valid(previous[1]))
            registration.writer.invalidate()
            release.set()
            self.assertTrue(registration.dispose(3), "owned worker leaked")

    def test_late_finish_does_not_clear_new_attempt_binding(self):
        from utils.execution_context_capture import active_capture

        registration = self.register()
        self.dequeue(1)
        self.dequeue(2)
        current = active_capture()
        self.assertIsNotNone(current)
        self.queue.task_done(1, {}, status())
        self.assertEqual(active_capture(), current)
        # An identical callback replay keeps the already accepted binding.
        registration._dequeue(item(2))
        self.assertEqual(active_capture(), current)
        self.queue.task_done(2, {}, status())
        self.assertIsNone(active_capture())

    def test_invalidation_releases_capture_immediately_outside_writer_locks(self):
        from utils.execution_context_capture import active_capture

        registration = self.register()
        self.dequeue(1)
        current = active_capture()
        pool = registration.writer.context_budget
        self.assertGreater(pool.charged_bytes, 0)
        registration.writer.invalidate()
        self.assertEqual(pool.charged_bytes, 0)
        self.assertFalse(current[0].valid(current[1]))

    def test_replayed_older_accepted_dequeue_cannot_revoke_current_binding(self):
        from utils.execution_context_capture import active_capture

        registration = self.register()
        old = self.dequeue(1)
        self.dequeue(2)
        current = active_capture()
        registration._dequeue(old)
        self.assertEqual(active_capture(), current)

    def test_broken_finish_releases_active_capture(self):
        from utils.execution_context_capture import active_capture

        registration = self.register()
        self.dequeue(1)

        class Broken:
            @property
            def messages(self):
                raise RuntimeError("broken status")

        self.queue.task_done(1, {}, Broken())
        self.assertEqual(registration.writer.context_budget.charged_bytes, 0)
        self.assertIsNone(active_capture())

    def test_finished_accepted_replay_preserves_new_same_uuid_attempt(self):
        from utils.execution_context_capture import active_capture

        registration = self.register()
        old = self.dequeue(1)
        self.queue.task_done(1, {}, status())
        self.dequeue(2)
        current = active_capture()
        registration._dequeue(old)
        self.assertEqual(active_capture(), current)
        self.queue.task_done(2, {}, status())
        self.drain(registration)
        self.assertEqual(len(self.rows()), 2)

    def test_observed_upstream_survives_downstream_failure_in_database(self):
        from utils.execution_context_capture import record_runtime_context

        g = {
            "p": {
                "class_type": "PromptManagerPair",
                "inputs": {"text": "raw", "negative_text": ""},
            },
            "s": {
                "class_type": "KSampler",
                "inputs": {"positive": ["p", 0], "negative": ["p", 1]},
            },
        }
        registration = self.register()
        self.queue.results.append(((0, JOB, g, {}, ["s"]), 1))
        self.queue.get()
        host = types.ModuleType("comfy_execution.utils")
        host.get_executing_context = lambda: SimpleNamespace(
            prompt_id=JOB, node_id="p", list_index=0
        )
        with patch.dict(sys.modules, {"comfy_execution.utils": host}):
            record_runtime_context(
                "PromptManagerPair",
                " raw ",
                " pre ",
                "",
                "pre raw TRIGGER",
                role="positive",
            )
            record_runtime_context("PromptManagerPair", "", "", "", "", role="negative")
        self.queue.task_done(1, {}, status("execution_error"))
        self.drain(registration)
        row = self.rows()[0]
        self.assertEqual(row["outcome"], "failed")
        result = self.db.get_execution_contexts(row["run_id"])
        self.assertEqual(result["state"], "complete")
        self.assertEqual(
            result["contexts"][0]["positive"],
            {
                "raw": " raw ",
                "effective": "pre raw TRIGGER",
                "prepend": " pre ",
                "append": "",
            },
        )
        self.assertEqual(result["contexts"][0]["negative"]["effective"], "")

    def test_close_releases_capture_while_writer_is_blocked(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = self.db.write_execution_start

        def blocked(command):
            entered.set()
            release.wait(3)
            return original(command)

        with patch.object(self.db, "write_execution_start", blocked):
            registration = self.register()
            self.dequeue(1)
            self.assertTrue(entered.wait(1))
            self.assertGreater(registration.writer.context_budget.charged_bytes, 0)
            self.assertFalse(registration.writer.close(0))
            self.assertEqual(registration.writer.context_budget.charged_bytes, 0)
            release.set()

    def test_reregistration_cannot_rebind_already_observed_queue_item(self):
        from utils.execution_context_capture import active_capture

        first = self.register()
        self.dequeue(10)
        self.queue.task_done(10, {}, status())
        self.drain(first)
        self.assertTrue(first.dispose())
        second = self.register()
        self.dequeue(10)
        self.assertIsNone(active_capture())
        self.queue.task_done(10, {}, status())
        self.drain(second)
        self.assertEqual(len(self.rows()), 1)

    def test_cached_executor_subscription_persists_strings_and_disposes_cooperatively(
        self,
    ):
        from utils.execution_context_host import subscribe_executor_messages

        calls = []
        execution = types.ModuleType("execution")
        caching = types.ModuleType("comfy_execution.caching")
        package = types.ModuleType("comfy_execution")
        entry_type = namedtuple("CacheEntry", "ui outputs")

        class PromptExecutor:
            def add_message(self, event, data, broadcast):
                calls.append(event)
                return "host-result"

        class HierarchicalCache:
            def get_local(self, node_id):
                return entry_type(None, [object(), object(), ["old trigger"], [""]])

        execution.PromptExecutor, execution.CacheEntry = PromptExecutor, entry_type
        caching.HierarchicalCache = HierarchicalCache
        package.caching = caching
        with patch.dict(
            sys.modules,
            {
                "execution": execution,
                "comfy_execution": package,
                "comfy_execution.caching": caching,
            },
        ):
            observer_calls = []
            independent = subscribe_executor_messages(
                "independent", lambda *args: observer_calls.append(args)
            )
            try:
                registration = self.register()
                g = {
                    "p": {
                        "class_type": "PromptManagerPair",
                        "inputs": {"text": "raw", "negative_text": ""},
                    },
                    "s": {
                        "class_type": "KSampler",
                        "inputs": {"positive": ["p", 0], "negative": ["p", 1]},
                    },
                }
                self.queue.results.append(((0, JOB, g, {}, ["s"]), 1))
                self.queue.get()
                executor = PromptExecutor()
                executor.server = self.server
                executor.caches = SimpleNamespace(outputs=HierarchicalCache())
                self.assertEqual(
                    executor.add_message("execution_start", {"prompt_id": JOB}, False),
                    "host-result",
                )
                executor.add_message(
                    event="execution_cached",
                    data={"prompt_id": JOB, "nodes": ["p"]},
                    broadcast=False,
                )
                self.queue.task_done(1, {}, status())
                self.drain(registration)
                result = self.db.get_execution_contexts(self.rows()[0]["run_id"])
                self.assertEqual(
                    result["contexts"][0]["positive"]["effective"], "old trigger"
                )
                self.assertEqual(result["contexts"][0]["negative"]["effective"], "")
                self.assertTrue(registration.dispose())
                executor.add_message(
                    "execution_cached", {"prompt_id": JOB, "nodes": ["p"]}, False
                )
                self.assertEqual(len(observer_calls), 3)
            finally:
                independent()
        self.assertEqual(
            calls, ["execution_start", "execution_cached", "execution_cached"]
        )
