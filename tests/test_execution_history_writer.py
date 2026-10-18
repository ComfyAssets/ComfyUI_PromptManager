"""Real-thread, disposable-database coverage for the bounded history writer."""

from dataclasses import FrozenInstanceError, replace
import logging
import os
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from database import PromptDatabase
from utils.execution_history_events import (
    LifecycleEvent,
    RunFinish,
    TerminalEvidence,
    make_finish,
    make_start,
)
from utils.execution_history_writer import HistoryWriter


class ManualClock:
    def __init__(self):
        self.value = 0.0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.value

    def advance(self, seconds):
        with self.lock:
            self.value += seconds


class HistoryWriterTests(unittest.TestCase):
    def setUp(self):
        logger = logging.getLogger("prompt_manager.execution_history_writer")
        handlers, propagate, level = logger.handlers, logger.propagate, logger.level
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        logger.setLevel(logging.WARNING)

        def restore_logging():
            logger.handlers, logger.propagate, logger.level = handlers, propagate, level

        self.addCleanup(restore_logging)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = PromptDatabase(os.path.join(self.directory.name, "history.db"))
        self.addCleanup(self.db.model.reset_schema_cache)
        self.addCleanup(self.db.model.close_all)
        self.clock = ManualClock()
        self.session = str(uuid4())
        self.job = str(uuid4())
        self.releases = []
        self.writers = []
        self.addCleanup(self.cleanup_workers)

    def cleanup_workers(self):
        for release in self.releases:
            release.set()
        for writer in self.writers:
            self.assertTrue(writer.close(3), "test-created worker leaked")

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            threading.Event().wait(0.005)
        self.fail("finite deadline expired waiting for worker behavior")

    def writer(self, factory=None, ready=True):
        writer = HistoryWriter(factory or (lambda: self.db), clock=self.clock)
        self.writers.append(writer)
        writer.start()
        if ready:
            self.wait_for(lambda: writer.status().accepting)
        return writer

    def start_command(self, item=1, epoch=None):
        return make_start(
            self.session,
            ((0, self.job), item),
            epoch or self.db.get_execution_epoch(),
            100,
        )

    def finish_command(self, start):
        return make_finish(
            start.session_id,
            start.queue_item_id,
            start.storage_epoch,
            SimpleNamespace(messages=[("execution_success", {"prompt_id": self.job})]),
            200,
        )

    def block(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        return entered, release

    def sealed(self, writer, value="exact text", generation=None):
        from utils.execution_context_budget import SealedContext
        from utils.execution_contexts import (
            ContextBundle,
            RecipeContext,
            PromptSnapshot,
            TextEvidence,
            bundle_payload_size,
        )

        field = TextEvidence(value, "recorded", "runtime_return", None)
        snapshot = PromptSnapshot(field, field, field, field)
        bundle = ContextBundle(
            (RecipeContext("1", snapshot, snapshot, "inferred"),),
            "complete",
            "captured",
        )
        lease = writer.context_budget.reserve(bundle_payload_size(bundle))
        return SealedContext(
            bundle,
            lease,
            "complete",
            "captured",
            writer.status().capture_generation if generation is None else generation,
        )

    def test_context_success_releases_and_legacy_finish_preserves_capture(self):
        writer = self.writer()
        start = self.start_command()
        sealed = self.sealed(writer)
        self.assertTrue(writer.submit(start))
        self.assertTrue(writer.submit_finalization(self.finish_command(start), sealed))
        self.wait_for(lambda: writer.status().pending == 0)
        self.assertEqual(writer.context_budget.charged_bytes, 0)
        self.assertEqual(
            self.db.get_execution_contexts(start.run_id)["state"], "complete"
        )
        self.assertTrue(writer.submit(self.finish_command(start)))
        self.wait_for(lambda: writer.status().pending == 0)
        self.assertEqual(
            self.db.get_execution_contexts(start.run_id)["state"], "complete"
        )

    def test_context_saturation_keeps_lifecycle_and_status(self):
        from utils.execution_context_budget import SealedContext
        from utils.execution_history_events import encode_command

        writer = self.writer()
        lease = writer.context_budget.reserve(8388608)
        self.assertIsNone(writer.context_budget.reserve(1))
        start = self.start_command()
        self.assertTrue(writer.submit(start))
        finish = self.finish_command(start)
        self.assertLessEqual(len(encode_command(finish)), 2048)
        self.assertTrue(
            writer.submit_finalization(
                finish, SealedContext(None, None, "unavailable", "budget_exhausted")
            )
        )
        self.wait_for(lambda: writer.status().pending == 0)
        self.assertEqual(
            self.db.get_execution_run(start.run_id)["outcome"], "succeeded"
        )
        self.assertEqual(
            self.db.get_execution_contexts(start.run_id)["reason"], "budget_exhausted"
        )
        lease.release()
        self.assertEqual(writer.context_budget.charged_bytes, 0)

    def test_outcome_survives_context_transaction_failure(self):
        for error, ready in (
            (ValueError("invalid evidence"), True),
            (sqlite3.OperationalError("unavailable"), False),
        ):
            with self.subTest(error=type(error)):
                writer = self.writer()
                start = self.start_command(10 if ready else 11)
                self.assertTrue(writer.submit(start))
                sealed = self.sealed(writer)
                entered, release = self.block()

                def fail_context(*args, **kwargs):
                    entered.set()
                    release.wait(3)
                    raise error

                with patch.object(
                    self.db, "write_execution_contexts_for_finish", fail_context
                ):
                    self.assertTrue(
                        writer.submit_finalization(self.finish_command(start), sealed)
                    )
                    self.assertTrue(entered.wait(1))
                    self.assertEqual(
                        self.db.get_execution_run(start.run_id)["outcome"], "succeeded"
                    )
                    self.assertGreater(writer.context_budget.charged_bytes, 0)
                    release.set()
                    self.wait_for(lambda: writer.status().pending == 0)
                self.assertEqual(
                    self.db.get_execution_run(start.run_id)["outcome"], "succeeded"
                )
                self.assertEqual(writer.status().accepting, ready)
                self.assertEqual(writer.context_budget.charged_bytes, 0)
                if not ready:
                    self.clock.advance(5)
                    self.wait_for(lambda: writer.status().accepting)
                self.assertEqual(
                    self.db.get_execution_contexts(start.run_id)["state"],
                    "not_captured",
                )
                self.assertTrue(writer.close())

    def test_same_epoch_recovery_changes_capture_generation(self):
        writer = self.writer()
        start = self.start_command()
        old_generation = writer.status().capture_generation
        sealed = self.sealed(writer)
        writer.invalidate()
        self.clock.advance(5)
        self.wait_for(lambda: writer.status().accepting)
        self.assertEqual(writer.status().epoch, start.storage_epoch)
        self.assertGreater(writer.status().capture_generation, old_generation)
        self.assertTrue(writer.submit(start))
        self.assertTrue(writer.submit_finalization(self.finish_command(start), sealed))
        self.wait_for(lambda: writer.status().pending == 0)
        self.assertEqual(
            self.db.get_execution_run(start.run_id)["outcome"], "succeeded"
        )
        self.assertEqual(writer.context_budget.charged_bytes, 0)
        self.assertEqual(self.db.get_execution_contexts(start.run_id)["contexts"], [])

    def test_restore_between_transactions_rejects_context(self):
        writer = self.writer()
        start = self.start_command()
        self.db.write_execution_start(start)
        backup = os.path.join(self.directory.name, "between.db")
        self.assertTrue(self.db.model.backup_database(backup))
        original = self.db.write_execution_finish

        def finish(command):
            result = original(command)
            self.db.model.restore_from_file(backup)
            return result

        with patch.object(self.db, "write_execution_finish", finish):
            self.assertTrue(
                writer.submit_finalization(
                    self.finish_command(start), self.sealed(writer)
                )
            )
            self.wait_for(lambda: writer.status().pending == 0)
        self.assertEqual(
            self.db.get_execution_contexts(start.run_id)["state"], "not_captured"
        )
        self.assertEqual(writer.context_budget.charged_bytes, 0)
        self.assertFalse(writer.status().accepting)

    def test_reject_discard_and_bounded_close_release_leases(self):
        writer = self.writer()
        start = self.start_command()
        sealed = self.sealed(writer)
        self.assertFalse(writer.submit_finalization(object(), sealed))
        self.assertEqual(writer.context_budget.charged_bytes, 0)
        entered, release = self.block()
        original = self.db.write_execution_start

        def write(command):
            entered.set()
            release.wait(3)
            return original(command)

        with patch.object(self.db, "write_execution_start", write):
            self.assertTrue(writer.submit(start))
            self.assertTrue(entered.wait(1))
            for _ in range(1023):
                self.assertTrue(
                    writer.submit_finalization(
                        self.finish_command(start), self.sealed(writer)
                    )
                )
            self.assertEqual(writer.status().pending, 1024)
            self.assertFalse(
                writer.submit_finalization(
                    self.finish_command(start), self.sealed(writer)
                )
            )
            self.assertFalse(writer.close(0.01))
            release.set()
            self.wait_for(lambda: not writer.status().alive)
        self.assertEqual(writer.context_budget.charged_bytes, 0)
        self.assertEqual(
            self.db.get_execution_contexts(start.run_id)["state"], "not_captured"
        )

    def test_bounded_close_keeps_inflight_charge_and_releases_queued(self):
        from utils.execution_context_budget import ContextBudget

        pool = ContextBudget(1000)
        writer = HistoryWriter(lambda: self.db, clock=self.clock, context_budget=pool)
        self.writers.append(writer)
        writer.start()
        self.wait_for(lambda: writer.status().accepting)
        start = self.start_command()
        self.db.write_execution_start(start)
        entered, release = self.block()
        original = self.db.write_execution_contexts_for_finish
        first = self.sealed(writer)
        charge = first.lease.byte_count

        def write(*args, **kwargs):
            entered.set()
            release.wait(3)
            return original(*args, **kwargs)

        with patch.object(self.db, "write_execution_contexts_for_finish", write):
            self.assertTrue(
                writer.submit_finalization(self.finish_command(start), first)
            )
            self.assertTrue(entered.wait(1))
            self.assertTrue(
                writer.submit_finalization(
                    self.finish_command(start), self.sealed(writer)
                )
            )
            before = time.monotonic()
            self.assertFalse(writer.close(0.02))
            self.assertLess(time.monotonic() - before, 0.2)
            self.assertEqual(pool.charged_bytes, charge)
            replacement = HistoryWriter(lambda: self.db, context_budget=pool)
            self.writers.append(replacement)
            self.assertIsNone(replacement.context_budget.reserve(1000))
            release.set()
            self.wait_for(lambda: not writer.status().alive)
        self.assertEqual(pool.charged_bytes, 0)

    def test_same_epoch_invalidation_between_transactions_drops_payload(self):
        writer = self.writer()
        start = self.start_command()
        self.db.write_execution_start(start)
        original = self.db.write_execution_finish

        def finish(command):
            result = original(command)
            writer.invalidate()
            return result

        with patch.object(self.db, "write_execution_finish", finish):
            self.assertTrue(
                writer.submit_finalization(
                    self.finish_command(start), self.sealed(writer)
                )
            )
            self.wait_for(lambda: writer.status().pending == 0)
        self.assertEqual(
            self.db.get_execution_run(start.run_id)["outcome"], "succeeded"
        )
        self.assertEqual(
            self.db.get_execution_contexts(start.run_id)["state"], "not_captured"
        )
        self.assertEqual(writer.context_budget.charged_bytes, 0)
        self.clock.advance(5)
        self.wait_for(lambda: writer.status().accepting)
        self.assertEqual(
            self.db.get_execution_contexts(start.run_id)["state"], "not_captured"
        )

    def test_invalid_seals_release_and_keep_valid_outcome(self):
        from utils.execution_context_budget import ContextBudget

        writer = self.writer()
        cases = [
            dict(state="private text" * 100),
            dict(reason="arbitrary_exception"),
            dict(capture_generation=True),
            dict(capture_generation=-1),
            dict(capture_generation=None),
            dict(lease=None),
        ]
        for item, changes in enumerate(cases, 20):
            start = self.start_command(item)
            self.db.write_execution_start(start)
            sealed = self.sealed(writer)
            malformed = replace(sealed, **changes)
            if changes == {"lease": None}:
                sealed.lease.release()
            self.assertTrue(
                writer.submit_finalization(self.finish_command(start), malformed)
            )
            self.wait_for(lambda: writer.status().pending == 0)
            self.assertEqual(
                self.db.get_execution_run(start.run_id)["outcome"], "succeeded"
            )
            self.assertEqual(
                self.db.get_execution_contexts(start.run_id)["reason"],
                "invalid_evidence",
            )
            self.assertEqual(writer.context_budget.charged_bytes, 0)
        foreign = ContextBudget(1000)
        sealed = self.sealed(writer)
        sealed.lease.release()
        sealed = replace(sealed, lease=foreign.reserve(1000))
        start = self.start_command(30)
        self.db.write_execution_start(start)
        self.assertTrue(writer.submit_finalization(self.finish_command(start), sealed))
        self.wait_for(lambda: writer.status().pending == 0)
        self.assertEqual(foreign.charged_bytes, 0)
        self.assertEqual(self.db.get_execution_contexts(start.run_id)["contexts"], [])

    def test_custom_run_identity_receives_finalized_context(self):
        writer = self.writer()
        start = replace(self.start_command(), run_id=str(uuid4()))
        self.assertTrue(writer.submit(start))
        self.assertTrue(
            writer.submit_finalization(self.finish_command(start), self.sealed(writer))
        )
        self.wait_for(lambda: writer.status().pending == 0)
        self.assertEqual(
            self.db.get_execution_run(start.run_id)["outcome"], "succeeded"
        )
        self.assertEqual(
            self.db.get_execution_contexts(start.run_id)["state"], "complete"
        )
        self.assertEqual(writer.context_budget.charged_bytes, 0)

    def test_invalidation_during_context_preparation_fences_actual_transaction(self):
        import database.execution_contexts as contexts_db

        writer = self.writer()
        start = self.start_command()
        self.db.write_execution_start(start)
        entered, release = self.block()
        original = contexts_db._prepare

        def prepare(bundle):
            prepared = original(bundle)
            entered.set()
            release.wait(3)
            return prepared

        with patch.object(contexts_db, "_prepare", prepare):
            self.assertTrue(
                writer.submit_finalization(
                    self.finish_command(start), self.sealed(writer)
                )
            )
            self.assertTrue(entered.wait(1))
            self.assertEqual(
                self.db.get_execution_run(start.run_id)["outcome"], "succeeded"
            )
            generation = writer.status().capture_generation
            writer.invalidate()
            self.assertGreater(writer.status().capture_generation, generation)
            release.set()
            self.wait_for(lambda: writer.status().pending == 0)
        self.assertEqual(
            self.db.get_execution_contexts(start.run_id)["state"], "not_captured"
        )
        self.assertEqual(writer.context_budget.charged_bytes, 0)
        self.clock.advance(5)
        self.wait_for(lambda: writer.status().accepting)
        self.assertEqual(writer.status().epoch, start.storage_epoch)
        self.assertEqual(
            self.db.get_execution_contexts(start.run_id)["state"], "not_captured"
        )

    def test_context_commit_serializes_invalidation_without_blocking_queue_or_close(
        self,
    ):
        import database.execution_contexts as contexts_db

        writer = self.writer()
        start = self.start_command()
        self.db.write_execution_start(start)
        entered, release = self.block()
        invalidating, invalidated = threading.Event(), threading.Event()
        original = contexts_db._write_capture

        def capture(*args, **kwargs):
            entered.set()
            release.wait(3)
            return original(*args, **kwargs)

        def invalidate():
            invalidating.set()
            writer.invalidate()
            invalidated.set()

        with patch.object(contexts_db, "_write_capture", capture):
            self.assertTrue(
                writer.submit_finalization(
                    self.finish_command(start), self.sealed(writer)
                )
            )
            self.assertTrue(entered.wait(1))
            invalidator = threading.Thread(target=invalidate)
            invalidator.start()
            try:
                self.assertTrue(invalidating.wait(1))
                self.assertFalse(invalidated.wait(0.03))
                before = time.monotonic()
                self.assertTrue(writer.status().accepting)
                self.assertTrue(writer.submit(self.finish_command(start)))
                self.assertLess(time.monotonic() - before, 0.1)
                self.assertFalse(writer.close(0.02))
                self.assertGreater(writer.context_budget.charged_bytes, 0)
                self.assertEqual(writer.status().pending, 1)
            finally:
                release.set()
                invalidator.join(3)
            self.assertFalse(invalidator.is_alive())
            self.assertTrue(invalidated.is_set())
            self.wait_for(lambda: not writer.status().alive)
        self.assertEqual(
            self.db.get_execution_run(start.run_id)["outcome"], "succeeded"
        )
        self.assertEqual(
            self.db.get_execution_contexts(start.run_id)["state"], "complete"
        )
        self.assertEqual(writer.context_budget.charged_bytes, 0)

    def test_fifo_single_worker_and_thread_local_connection_cleanup(self):
        caller_conn = self.db.model.get_connection()
        threads, timeouts = [], []
        original = self.db.write_execution_start

        def write(command):
            threads.append(threading.get_ident())
            timeouts.append(
                self.db.model.get_connection()
                .execute("PRAGMA busy_timeout")
                .fetchone()[0]
            )
            return original(command)

        with patch.object(self.db, "write_execution_start", write):
            writer = self.writer()
            writer.start()
            start = self.start_command()
            self.assertTrue(writer.submit(start))
            self.assertTrue(writer.submit(self.finish_command(start)))
            self.wait_for(
                lambda: (self.db.get_execution_run(start.run_id) or {}).get("outcome")
                == "succeeded"
            )
            self.assertTrue(writer.submit(self.start_command(2)))
            self.assertTrue(writer.close(2))
        self.assertEqual(len(set(threads)), 1)
        self.assertNotEqual(threads[0], threading.get_ident())
        self.assertEqual(timeouts, [250, 250])
        self.assertFalse(writer.status().alive)
        self.assertFalse(writer.status().accepting)
        self.assertEqual(writer.status().pending, 0)
        self.assertEqual(
            caller_conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000
        )
        self.assertEqual(len(self.db.model._connections), 1)
        with self.assertRaises(FrozenInstanceError):
            writer.status().epoch = "changed"
        self.assertFalse(writer.submit(start))

    def test_blocked_initialization_never_accepts_or_blocks_producers(self):
        entered, release = self.block()

        def factory():
            entered.set()
            release.wait(3)
            return self.db

        writer = self.writer(factory, ready=False)
        self.assertTrue(entered.wait(1))
        before = time.monotonic()
        self.assertFalse(writer.submit(self.start_command()))
        self.assertIsNone(writer.status().epoch)
        self.assertLess(time.monotonic() - before, 0.1)
        self.assertIsNone(self.db.get_execution_run(self.start_command().run_id))
        before = time.monotonic()
        self.assertFalse(writer.close(0.03))
        self.assertLess(time.monotonic() - before, 0.2)
        release.set()
        self.wait_for(lambda: not writer.status().alive)
        self.assertFalse(writer.status().accepting)
        self.assertEqual(len(self.db.model._connections), 1)

    def test_saturation_bounds_all_pending_commands_and_coalesces_diagnostics(self):
        entered, release = self.block()
        original = self.db.write_execution_start

        def write(command):
            entered.set()
            release.wait(3)
            return original(command)

        with patch.object(self.db, "write_execution_start", write):
            writer = self.writer()
            command = self.start_command()
            self.assertTrue(writer.submit(command))
            self.assertTrue(entered.wait(1))
            for _ in range(1023):
                self.assertTrue(writer.submit(command))
            for _ in range(100):
                self.assertFalse(writer.submit(command))
            self.assertEqual(writer.status().pending, 1024)
            self.assertEqual(writer.status().recording_gaps, 100)
            with self.assertLogs(
                "prompt_manager.execution_history_writer", "WARNING"
            ) as logs:
                release.set()
                self.wait_for(lambda: writer.status().pending == 0)
                self.assertTrue(writer.close(2))
            self.assertEqual(len(logs.records), 1)

    def test_rejects_oversized_invalid_and_wrong_epoch_commands(self):
        writer = self.writer()
        start = self.start_command()
        oversized = RunFinish(
            self.session,
            1,
            start.storage_epoch,
            200,
            TerminalEvidence(
                tuple(
                    LifecycleEvent(
                        "execution_error", self.job, i, "n" * 128, None, None
                    )
                    for i in range(20)
                ),
                False,
                False,
                False,
            ),
        )
        for command in (
            oversized,
            object(),
            replace(start, storage_epoch=str(uuid4())),
        ):
            self.assertFalse(writer.submit(command))
        self.assertEqual(writer.status().pending, 0)
        self.assertEqual(writer.status().recording_gaps, 3)
        self.assertIsNone(self.db.get_execution_run(start.run_id))

    def test_failed_command_once_discards_backlog_and_waits_five_seconds(self):
        entered, release = self.block()
        probes, writes = [], []
        original_epoch = self.db.get_execution_epoch
        original_write = self.db.write_execution_start

        def epoch():
            probes.append(self.clock())
            return original_epoch()

        def write(command):
            writes.append(command.queue_item_id)
            if command.queue_item_id == 1:
                entered.set()
                release.wait(3)
                raise sqlite3.OperationalError("private failure text")
            return original_write(command)

        start, queued, fresh = (self.start_command(i) for i in (1, 2, 3))
        with (
            patch.object(self.db, "get_execution_epoch", epoch),
            patch.object(self.db, "write_execution_start", write),
        ):
            writer = self.writer()
            self.assertTrue(writer.submit(start))
            self.assertTrue(entered.wait(1))
            self.assertTrue(writer.submit(queued))
            release.set()
            self.wait_for(lambda: not writer.status().accepting)
            self.assertFalse(writer.submit(fresh))
            self.clock.advance(4.99)
            threading.Event().wait(0.08)
            self.assertEqual(probes, [0])
            self.clock.advance(0.01)
            self.wait_for(lambda: writer.status().accepting)
            self.assertEqual(probes, [0, 5])
            self.assertTrue(writer.submit(fresh))
            self.wait_for(lambda: self.db.get_execution_run(fresh.run_id) is not None)
        self.assertEqual(writes, [1, 3])
        self.assertIsNone(self.db.get_execution_run(start.run_id))
        self.assertIsNone(self.db.get_execution_run(queued.run_id))

    def test_factory_failure_recovery_probes_are_bounded(self):
        calls = []

        def factory():
            calls.append(self.clock())
            if len(calls) < 3:
                raise sqlite3.OperationalError("unavailable")
            return self.db

        writer = self.writer(factory, ready=False)
        self.wait_for(lambda: len(calls) == 1)
        self.clock.advance(4.99)
        threading.Event().wait(0.08)
        self.assertEqual(calls, [0])
        self.clock.advance(0.01)
        self.wait_for(lambda: len(calls) == 2)
        self.clock.advance(4.99)
        threading.Event().wait(0.08)
        self.assertEqual(calls, [0, 5])
        self.clock.advance(0.01)
        self.wait_for(lambda: writer.status().accepting)
        self.assertEqual(calls, [0, 5, 10])
        command = self.start_command()
        self.assertTrue(writer.submit(command))
        self.wait_for(lambda: self.db.get_execution_run(command.run_id) is not None)

    def test_failed_epoch_probe_closes_only_worker_connection_and_recovers(self):
        command = self.start_command()
        original = self.db.get_execution_epoch
        failed = threading.Event()

        def epoch():
            if not failed.is_set():
                failed.set()
                raise sqlite3.DatabaseError("metadata unavailable")
            return original()

        with patch.object(self.db, "get_execution_epoch", epoch):
            writer = self.writer(ready=False)
            self.wait_for(lambda: writer.status().recording_gaps == 1)
            self.assertIsNone(writer.status().epoch)
            self.assertFalse(writer.submit(command))
            self.assertEqual(len(self.db.model._connections), 1)
            self.clock.advance(5)
            self.wait_for(lambda: writer.status().accepting)
            self.assertTrue(writer.submit(command))
            self.wait_for(lambda: self.db.get_execution_run(command.run_id) is not None)

    def test_invalidation_during_epoch_probe_cannot_publish_stale_readiness(self):
        entered, release = self.block()
        original = self.db.get_execution_epoch
        stale_epoch = original()
        fresh_epoch = str(uuid4())

        def epoch():
            result = original()
            if not entered.is_set():
                entered.set()
                release.wait(3)
            return result

        with patch.object(self.db, "get_execution_epoch", epoch):
            writer = self.writer(ready=False)
            self.assertTrue(entered.wait(1))
            with self.db.model.get_connection() as conn:
                conn.execute(
                    "UPDATE execution_history_metadata SET storage_epoch=?",
                    (fresh_epoch,),
                )
            writer.invalidate()
            release.set()
            threading.Event().wait(0.08)
            self.assertFalse(writer.status().accepting)
            self.assertIsNone(writer.status().epoch)
            self.assertFalse(writer.submit(self.start_command(epoch=stale_epoch)))
            self.clock.advance(5)
            self.wait_for(lambda: writer.status().epoch == fresh_epoch)
            command = self.start_command(epoch=fresh_epoch)
            self.assertTrue(writer.submit(command))
            self.wait_for(lambda: self.db.get_execution_run(command.run_id) is not None)

    def test_held_sqlite_lock_does_not_block_submit_and_is_not_retried(self):
        writer = self.writer()
        command = self.start_command()
        lock = sqlite3.connect(self.db.model.db_path)
        self.addCleanup(lock.close)
        lock.execute("BEGIN IMMEDIATE")
        before = time.monotonic()
        self.assertTrue(writer.submit(command))
        self.assertLess(time.monotonic() - before, 0.1)
        self.wait_for(lambda: not writer.status().accepting)
        lock.rollback()
        self.clock.advance(5)
        self.wait_for(lambda: writer.status().accepting)
        self.assertIsNone(self.db.get_execution_run(command.run_id))
        fresh = self.start_command(2)
        self.assertTrue(writer.submit(fresh))
        self.wait_for(lambda: self.db.get_execution_run(fresh.run_id) is not None)

    def test_external_epoch_rotation_drops_stale_commands_then_records_fresh(self):
        writer = self.writer()
        command = self.start_command()
        new_epoch = str(uuid4())
        with self.db.model.get_connection() as conn:
            conn.execute(
                "UPDATE execution_history_metadata SET storage_epoch=?", (new_epoch,)
            )
        self.assertTrue(writer.submit(command))
        self.wait_for(lambda: not writer.status().accepting)
        self.assertIsNone(writer.status().epoch)
        self.clock.advance(5)
        self.wait_for(lambda: writer.status().epoch == new_epoch)
        self.assertFalse(writer.submit(command))
        fresh = self.start_command(2)
        self.assertTrue(writer.submit(fresh))
        self.wait_for(lambda: self.db.get_execution_run(fresh.run_id) is not None)
        self.assertIsNone(self.db.get_execution_run(command.run_id))

    def test_restore_invalidates_queued_and_inflight_commands(self):
        backup = os.path.join(self.directory.name, "backup.db")
        command = self.start_command()
        self.db.write_execution_start(command)
        self.assertTrue(self.db.model.backup_database(backup))
        writer = self.writer()
        entered, release = self.block()
        original = self.db.write_execution_start

        def write(command):
            entered.set()
            release.wait(3)
            return original(command)

        with patch.object(self.db, "write_execution_start", write):
            self.assertTrue(writer.submit(command))
            self.assertTrue(entered.wait(1))
            self.assertTrue(writer.submit(self.finish_command(command)))
            self.db.model.restore_from_file(backup)
            self.assertFalse(writer.status().accepting)
            self.assertIsNone(writer.status().epoch)
            self.assertFalse(writer.submit(command))
            release.set()
            self.wait_for(lambda: writer.status().pending == 0)
        self.clock.advance(5)
        self.wait_for(lambda: writer.status().accepting)
        self.assertNotEqual(writer.status().epoch, command.storage_epoch)
        self.assertEqual(
            self.db.get_execution_run(command.run_id)["outcome"], "observed"
        )
        fresh = self.start_command(2)
        timeouts = []

        def write_fresh(command):
            timeouts.append(
                self.db.model.get_connection()
                .execute("PRAGMA busy_timeout")
                .fetchone()[0]
            )
            return original(command)

        with patch.object(self.db, "write_execution_start", write_fresh):
            self.assertTrue(writer.submit(fresh))
            self.assertTrue(writer.submit(self.finish_command(fresh)))
            self.wait_for(
                lambda: (self.db.get_execution_run(fresh.run_id) or {}).get("outcome")
                == "succeeded"
            )
        self.assertEqual(timeouts, [250])

    def test_bounded_close_discards_backlog_and_disposes_after_blocked_write(self):
        entered, release = self.block()
        original = self.db.write_execution_start

        def write(command):
            entered.set()
            release.wait(3)
            return original(command)

        with patch.object(self.db, "write_execution_start", write):
            writer = self.writer()
            first, queued = self.start_command(), self.start_command(2)
            self.assertTrue(writer.submit(first))
            self.assertTrue(entered.wait(1))
            self.assertTrue(writer.submit(queued))
            before = time.monotonic()
            self.assertFalse(writer.close(0.03))
            self.assertLess(time.monotonic() - before, 0.2)
            release.set()
            self.wait_for(lambda: not writer.status().alive)
        self.assertIsNone(self.db.get_execution_run(queued.run_id))
        self.assertEqual(writer.status().pending, 0)
        self.assertEqual(len(self.db.model._connections), 1)

    def test_close_before_start_is_terminal(self):
        writer = HistoryWriter(lambda: self.fail("closed writer initialized database"))
        self.assertTrue(writer.close())
        writer.start()
        self.assertFalse(writer.status().alive)
        self.assertFalse(writer.submit(self.start_command()))

    def test_external_capture_gaps_are_nonblocking_and_saturate_all_rejections(self):
        writer = HistoryWriter(lambda: self.fail("producer accessed database"))
        self.assertTrue(callable(getattr(writer, "record_gap", None)))
        writer.record_gap()
        self.assertEqual(writer.status().recording_gaps, 1)
        with writer._lock:
            writer._gaps = 2**63 - 2
        writer.record_gap()
        writer.record_gap()
        writer.submit(self.start_command())
        writer.submit(object())
        self.assertEqual(writer.status().recording_gaps, 2**63 - 1)
        self.assertTrue(writer.close())


if __name__ == "__main__":
    unittest.main()
