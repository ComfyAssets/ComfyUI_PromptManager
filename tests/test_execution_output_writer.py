"""Real-thread combined payload ownership and independently fenced persistence."""

from contextlib import nullcontext
from dataclasses import replace
from copy import copy
import gc
import os
import sqlite3
import threading
import time
import weakref
from types import SimpleNamespace
from unittest.mock import patch

from utils.execution_context_budget import ContextBudget, PayloadLease, SealedContext
from utils.execution_contexts import MAX_ATTEMPT_BYTES
from utils.execution_output_capture import seal_outputs
from utils.execution_outputs import snapshot_output_roots
from utils.execution_history_writer import HistoryWriter

try:
    from tests import test_execution_history_writer as fixtures
except ImportError:
    import test_execution_history_writer as fixtures


# Upper bound a blocked fake write waits for its release. Tests prove ordering
# with events, so this only caps a failing run and never decides a pass.
BLOCK_SECONDS = 10


class OutputWriterTests(fixtures.unittest.TestCase):
    setUp = fixtures.HistoryWriterTests.setUp
    cleanup_workers = fixtures.HistoryWriterTests.cleanup_workers
    wait_for = fixtures.HistoryWriterTests.wait_for
    writer = fixtures.HistoryWriterTests.writer
    start_command = fixtures.HistoryWriterTests.start_command
    finish_command = fixtures.HistoryWriterTests.finish_command
    block = fixtures.HistoryWriterTests.block
    sealed = fixtures.HistoryWriterTests.sealed

    def outputs(self, writer, context_bytes=0):
        roots = snapshot_output_roots("posix", "/output", "/temp")
        return seal_outputs(
            {
                "outputs": {
                    "save": {
                        "images": [
                            {"filename": "a.png", "subfolder": "", "type": "output"}
                        ]
                    }
                }
            },
            roots,
            roots,
            budget=writer.context_budget,
            capture_generation=writer.status().capture_generation,
            context_bytes=context_bytes,
        )

    def finalization(self, writer, start):
        context = self.sealed(writer)
        outputs = self.outputs(writer, context.lease.byte_count)
        self.assertTrue(
            writer.submit_finalization(self.finish_command(start), context, outputs)
        )
        return context, outputs

    def assert_idle_bundles_collectible(
        self,
        method: str | None = None,
        error_type: type[Exception] = ValueError,
        context_state: str = "complete",
        output_state: str = "complete",
        accepting: bool = True,
    ) -> None:
        writer = HistoryWriter(
            lambda: self.db, clock=self.clock, context_budget=ContextBudget()
        )
        self.writers.append(writer)
        writer.start()
        self.wait_for(lambda: writer.status().accepting)
        start = self.start_command(len(self.writers))
        self.db.write_execution_start(start)
        context = self.sealed(writer, "x" * 30000)
        roots = snapshot_output_roots("posix", "/output", "/temp")
        image = dict(filename="x" * 1024, subfolder="x" * 2048, type="output")
        tail = dict(filename="x", subfolder="x" * 1470, type="output")
        outputs = seal_outputs(
            {"outputs": {"😀" * 128: {"images": [image] * 34 + [tail]}}},
            roots,
            roots,
            budget=writer.context_budget,
            capture_generation=writer.status().capture_generation,
            context_bytes=context.lease.byte_count,
        )
        self.assertEqual(outputs.lease.byte_count, 131072)
        references = (weakref.ref(context.bundle), weakref.ref(outputs.bundle))

        def reject(_command: object, _bundle: object, **_kwargs: object) -> None:
            raise error_type("supplement rejected")

        write_patch = patch.object(self.db, method, reject) if method else nullcontext()
        try:
            with write_patch:
                self.assertTrue(
                    writer.submit_finalization(
                        self.finish_command(start), context, outputs
                    )
                )
                del context, outputs
                self.wait_for(lambda: writer.status().pending == 0)
                gc.collect()
                self.assertTrue(writer.status().alive)
                self.assertEqual(writer.status().pending, 0)
                self.assertEqual(writer.context_budget.charged_bytes, 0)
                self.assertFalse(
                    any(reference() is not None for reference in references),
                    "idle worker retained a bundle after releasing its payload lease",
                )
            self.assertEqual(writer.status().accepting, accepting)
            self.assertEqual(
                self.db.get_execution_run(start.run_id)["outcome"], "succeeded"
            )
            self.assertEqual(
                self.db.get_execution_contexts(start.run_id)["state"], context_state
            )
            self.assertEqual(
                self.db.get_execution_outputs(start.run_id)["state"], output_state
            )
        finally:
            self.assertTrue(writer.close(2))
            self.assertFalse(writer.status().alive)

    def test_successful_finalization_releases_bundles_while_worker_is_idle(
        self,
    ) -> None:
        self.assert_idle_bundles_collectible()

    def test_validation_omission_releases_bundles_while_worker_is_idle(self) -> None:
        for method, context_state, output_state in (
            ("write_execution_contexts_for_finish", "not_captured", "complete"),
            ("write_execution_outputs_for_finish", "complete", "not_captured"),
        ):
            with self.subTest(method=method):
                self.assert_idle_bundles_collectible(
                    method, context_state=context_state, output_state=output_state
                )

    def test_early_storage_failure_releases_bundles_while_worker_is_idle(self) -> None:
        self.assert_idle_bundles_collectible(
            "write_execution_contexts_for_finish",
            sqlite3.OperationalError,
            context_state="not_captured",
            output_state="not_captured",
            accepting=False,
        )

    def test_fifo_outcome_context_outputs_commit_and_custom_parent_identity(self):
        writer = self.writer()
        start = replace(self.start_command(), run_id=str(fixtures.uuid4()))
        self.assertTrue(writer.submit(start))
        self.finalization(writer, start)
        self.assertTrue(writer.close(2))
        self.assertEqual(
            self.db.get_execution_run(start.run_id)["outcome"], "succeeded"
        )
        self.assertEqual(
            self.db.get_execution_contexts(start.run_id)["state"], "complete"
        )
        self.assertEqual(
            self.db.get_execution_outputs(start.run_id)["references"][0]["filename"],
            "a.png",
        )
        self.assertEqual(writer.context_budget.charged_bytes, 0)

    def test_supplement_validation_is_independent_and_storage_failure_invalidates(
        self,
    ):
        for method, error, context_state, output_state, accepting in [
            (
                "write_execution_contexts_for_finish",
                ValueError("invalid"),
                "not_captured",
                "complete",
                True,
            ),
            (
                "write_execution_outputs_for_finish",
                ValueError("invalid"),
                "complete",
                "not_captured",
                True,
            ),
            (
                "write_execution_outputs_for_finish",
                sqlite3.OperationalError("private"),
                "complete",
                "not_captured",
                False,
            ),
        ]:
            with self.subTest(method=method, error=type(error)):
                writer = self.writer()
                start = self.start_command(len(self.writers))
                self.db.write_execution_start(start)
                with patch.object(self.db, method, side_effect=error):
                    self.finalization(writer, start)
                    self.wait_for(lambda: writer.status().pending == 0)
                self.assertEqual(
                    self.db.get_execution_run(start.run_id)["outcome"], "succeeded"
                )
                self.assertEqual(
                    self.db.get_execution_contexts(start.run_id)["state"], context_state
                )
                self.assertEqual(
                    self.db.get_execution_outputs(start.run_id)["state"], output_state
                )
                self.assertEqual(writer.status().accepting, accepting)
                self.assertEqual(writer.context_budget.charged_bytes, 0)
                self.assertTrue(writer.close())

    def test_bad_output_owners_are_omitted_without_losing_context(self):
        writer = self.writer()
        foreign = ContextBudget(10000)
        cases = [
            "inactive",
            "foreign",
            "forged",
            "generation",
            "missing_generation",
            "header",
            "missing_lease",
            "oversized_lease",
        ]
        for item_id, case in enumerate(cases, 1):
            with self.subTest(case=case):
                start = self.start_command(item_id)
                self.db.write_execution_start(start)
                context = self.sealed(writer)
                outputs = self.outputs(writer)
                if case == "inactive":
                    outputs.lease.release()
                elif case == "foreign":
                    outputs.lease.release()
                    outputs = replace(outputs, lease=foreign.reserve(10000))
                elif case == "forged":
                    outputs.lease.release()
                    outputs = replace(outputs, lease=object.__new__(PayloadLease))
                elif case == "generation":
                    outputs = replace(outputs, capture_generation=99)
                elif case == "missing_generation":
                    outputs = replace(outputs, capture_generation=None)
                elif case == "header":
                    outputs = replace(outputs, reason="private" * 100)
                elif case == "oversized_lease":
                    outputs.lease.release()
                    outputs = replace(
                        outputs, lease=writer.context_budget.reserve(131073)
                    )
                else:
                    outputs.lease.release()
                    outputs = replace(outputs, lease=None)
                self.assertTrue(
                    writer.submit_finalization(
                        self.finish_command(start), context, outputs
                    )
                )
                self.wait_for(lambda: writer.status().pending == 0)
                self.assertEqual(
                    self.db.get_execution_contexts(start.run_id)["state"], "complete"
                )
                self.assertEqual(
                    self.db.get_execution_outputs(start.run_id)["reason"],
                    "invalid_evidence",
                )
                self.assertEqual(writer.context_budget.charged_bytes, 0)
                self.assertEqual(foreign.charged_bytes, 0)

    def test_combined_payload_limit_omits_outputs_and_releases_both_owners(self):
        writer = self.writer()
        start = self.start_command()
        self.db.write_execution_start(start)
        context = self.sealed(writer)
        context.lease.release()
        context = replace(
            context, lease=writer.context_budget.reserve(MAX_ATTEMPT_BYTES)
        )
        outputs = self.outputs(writer)
        self.assertTrue(
            writer.submit_finalization(self.finish_command(start), context, outputs)
        )
        self.wait_for(lambda: writer.status().pending == 0)
        self.assertEqual(
            self.db.get_execution_contexts(start.run_id)["state"], "complete"
        )
        self.assertEqual(
            self.db.get_execution_outputs(start.run_id)["reason"], "budget_exhausted"
        )
        self.assertEqual(writer.context_budget.charged_bytes, 0)

    def isolated_writer(self) -> HistoryWriter:
        """A ready writer whose budget no other writer or test can charge."""
        writer = HistoryWriter(
            lambda: self.db, clock=self.clock, context_budget=ContextBudget()
        )
        self.writers.append(writer)
        writer.start()
        self.wait_for(lambda: writer.status().accepting)
        return writer

    def rotate_between_transactions(self, writer, start, after, restore, hold=None):
        """Wrap ``after`` so a restore or invalidation lands right after it.

        Returns the patch and an event set once that rotation has returned on
        the worker. A restore is a full verify/migrate/backup/replace cycle, so
        the handoff waits on this event with the generous ``BLOCK_SECONDS``
        ceiling instead of the short worker-behavior deadline (TESTING.md,
        Windows portability preflight).
        """
        backup = os.path.join(self.directory.name, f"restore-{start.run_id}.db")
        self.assertTrue(self.db.model.backup_database(backup))
        original = getattr(self.db, after)
        rotated = threading.Event()

        def write(*args, **kwargs):
            result = original(*args, **kwargs)
            try:
                if hold is not None:
                    entered, release = hold
                    entered.set()
                    release.wait(BLOCK_SECONDS)
                if restore:
                    self.db.model.restore_from_file(backup)
                else:
                    writer.invalidate()
            finally:
                rotated.set()
            return result

        return patch.object(self.db, after, write), rotated

    def test_restore_or_generation_rotation_between_transactions_discards_evidence(
        self,
    ):
        for after in ("write_execution_finish", "write_execution_contexts_for_finish"):
            for restore in (False, True):
                with self.subTest(after=after, restore=restore):
                    # Each subtest owns its writer and budget and retires them
                    # before the next starts, so one slow restore cannot leave
                    # in-flight charges or an epoch rotation for the next.
                    writer = self.isolated_writer()
                    try:
                        start = self.start_command(len(self.writers))
                        self.db.write_execution_start(start)
                        rotation, rotated = self.rotate_between_transactions(
                            writer, start, after, restore
                        )
                        with rotation:
                            self.finalization(writer, start)
                            self.assertTrue(rotated.wait(BLOCK_SECONDS))
                            self.wait_for(lambda: writer.status().pending == 0)
                        self.assertEqual(
                            self.db.get_execution_outputs(start.run_id)["state"],
                            "not_captured",
                        )
                        self.assertEqual(
                            self.db.get_execution_contexts(start.run_id)["state"],
                            (
                                "complete"
                                if after == "write_execution_contexts_for_finish"
                                and not restore
                                else "not_captured"
                            ),
                        )
                        self.assertEqual(writer.context_budget.charged_bytes, 0)
                    finally:
                        self.assertTrue(writer.close(BLOCK_SECONDS))

    def test_slow_restore_between_transactions_holds_charge_only_while_in_flight(
        self,
    ):
        writer = self.isolated_writer()
        start = self.start_command()
        self.db.write_execution_start(start)
        entered, release = self.block()
        rotation, rotated = self.rotate_between_transactions(
            writer, start, "write_execution_finish", True, (entered, release)
        )
        with rotation:
            context = self.sealed(writer)
            outputs = self.outputs(writer, context.lease.byte_count)
            charge = writer.context_budget.charged_bytes
            self.assertGreater(charge, 0)
            self.assertTrue(
                writer.submit_finalization(self.finish_command(start), context, outputs)
            )
            self.assertTrue(entered.wait(BLOCK_SECONDS))
            # The worker is between transactions: the payload is owned, not leaked.
            self.assertEqual(writer.status().pending, 1)
            self.assertEqual(writer.context_budget.charged_bytes, charge)
            release.set()
            self.assertTrue(rotated.wait(BLOCK_SECONDS))
            self.wait_for(lambda: writer.status().pending == 0)
        self.assertEqual(writer.context_budget.charged_bytes, 0)
        self.assertFalse(writer.status().accepting)
        self.assertEqual(
            self.db.get_execution_outputs(start.run_id)["state"], "not_captured"
        )
        # The restored epoch is probed again and new evidence is accepted.
        self.clock.advance(5)
        self.wait_for(lambda: writer.status().accepting)
        restored = self.start_command(2)
        self.db.write_execution_start(restored)
        self.finalization(writer, restored)
        self.wait_for(lambda: writer.status().pending == 0)
        self.assertEqual(
            self.db.get_execution_outputs(restored.run_id)["state"], "complete"
        )
        self.assertEqual(writer.context_budget.charged_bytes, 0)
        self.assertTrue(writer.close(BLOCK_SECONDS))

    def test_invalidation_during_output_preparation_rechecks_actual_commit(self):
        import database.execution_outputs as outputs_db

        writer = self.writer()
        start = self.start_command()
        self.db.write_execution_start(start)
        entered, release = self.block()
        original = outputs_db.validate_output_bundle

        def prepare(bundle):
            value = original(bundle)
            entered.set()
            release.wait(3)
            return value

        with patch.object(outputs_db, "validate_output_bundle", prepare):
            self.finalization(writer, start)
            self.assertTrue(entered.wait(1))
            writer.invalidate()
            release.set()
            self.wait_for(lambda: writer.status().pending == 0)
        self.assertEqual(
            self.db.get_execution_run(start.run_id)["outcome"], "succeeded"
        )
        self.assertEqual(
            self.db.get_execution_contexts(start.run_id)["state"], "complete"
        )
        self.assertEqual(
            self.db.get_execution_outputs(start.run_id)["state"], "not_captured"
        )
        self.assertEqual(writer.context_budget.charged_bytes, 0)

    def test_output_lock_failure_drops_backlog_and_never_retries_after_recovery(self):
        writer = self.writer()
        start, queued = self.start_command(), self.start_command(2)
        self.db.write_execution_start(start)
        entered, release = self.block()
        original = self.db.write_execution_outputs_for_finish

        def write(*args, **kwargs):
            entered.set()
            release.wait(3)
            return original(*args, **kwargs)

        lock = sqlite3.connect(self.db.model.db_path)
        self.addCleanup(lock.close)
        with patch.object(self.db, "write_execution_outputs_for_finish", write):
            self.finalization(writer, start)
            self.assertTrue(entered.wait(1))
            lock.execute("BEGIN IMMEDIATE")
            self.assertTrue(writer.submit(queued))
            self.finalization(writer, queued)
            release.set()
            self.wait_for(lambda: not writer.status().accepting)
        lock.rollback()
        self.assertEqual(writer.context_budget.charged_bytes, 0)
        self.clock.advance(5)
        self.wait_for(lambda: writer.status().accepting)
        self.assertIsNone(self.db.get_execution_run(queued.run_id))
        self.assertEqual(
            self.db.get_execution_outputs(start.run_id)["state"], "not_captured"
        )
        fresh = self.start_command(3)
        self.assertTrue(writer.submit(fresh))
        self.finalization(writer, fresh)
        self.wait_for(lambda: writer.status().pending == 0)
        self.assertEqual(
            self.db.get_execution_outputs(fresh.run_id)["state"], "complete"
        )
        self.assertEqual(writer.context_budget.charged_bytes, 0)

    def test_bounded_close_and_early_native_join_keep_inflight_combined_charge(self):
        for early in (False, True):
            with self.subTest(early_join=early):
                self.assert_bounded_close_keeps_inflight_charge(early)

    def assert_bounded_close_keeps_inflight_charge(self, early):
        # Every writer in this test charges the process-wide budget, so a
        # failing subtest must not leave its worker blocked (and charged) for
        # the next one: release and join it before the next subtest starts.
        writer = self.writer()
        self.assertEqual(writer.context_budget.charged_bytes, 0)
        start = self.start_command(len(self.writers))
        self.db.write_execution_start(start)
        entered, release = self.block()
        left = threading.Event()
        original = self.db.write_execution_outputs_for_finish

        def write(*args, **kwargs):
            entered.set()
            release.wait(BLOCK_SECONDS)
            left.set()
            return original(*args, **kwargs)

        try:
            with patch.object(self.db, "write_execution_outputs_for_finish", write):
                self.finalization(writer, start)
                self.assertTrue(entered.wait(BLOCK_SECONDS))
                charge = writer.context_budget.charged_bytes
                self.finalization(writer, start)
                clock = fixtures.ManualClock()
                native_join = writer._thread.join

                def return_early(timeout):
                    native_join(0)
                    clock.advance(min(timeout, 0.004))

                def sleep(delay):
                    clock.advance(delay)

                clock_patch = (
                    patch(
                        "utils.execution_history_writer.time",
                        SimpleNamespace(monotonic=clock, sleep=sleep),
                    )
                    if early
                    else nullcontext()
                )
                join_patch = (
                    patch.object(writer._thread, "join", return_early)
                    if early
                    else nullcontext()
                )
                with clock_patch, join_patch:
                    before = clock() if early else time.monotonic()
                    self.assertFalse(writer.close(0.02))
                    elapsed = (clock() if early else time.monotonic()) - before
                # close() waits until its own monotonic deadline, so the lower
                # bound holds on any runner.
                self.assertGreaterEqual(elapsed, 0.02)
                if early:
                    # Manual clock: only the patched join/sleep advance it, so
                    # this exact bound proves early joins do not spin forever.
                    self.assertLess(elapsed, 0.2)
                # Bounded, not unbounded: close() gave up while the in-flight
                # write was still blocked, instead of waiting it out.
                self.assertFalse(left.is_set())
                self.assertTrue(writer._thread.is_alive())
                self.assertEqual(writer.context_budget.charged_bytes, charge)
                replacement = HistoryWriter(lambda: self.db)
                self.writers.append(replacement)
                self.assertIs(replacement.context_budget, writer.context_budget)
                self.assertIsNone(replacement.context_budget.reserve(8388608))
                release.set()
                self.assertTrue(writer.close(2))
        finally:
            release.set()
            writer.close(BLOCK_SECONDS)
        self.assertEqual(writer.context_budget.charged_bytes, 0)

    def test_pending_saturation_and_invalid_command_release_both_payloads(self):
        writer = self.writer()
        start = self.start_command()
        context, outputs = self.sealed(writer), self.outputs(writer)
        self.assertFalse(writer.submit_finalization(object(), context, outputs))
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
                context = SealedContext(None, None, "unavailable", "budget_exhausted")
                self.assertTrue(
                    writer.submit_finalization(
                        self.finish_command(start), context, self.outputs(writer)
                    )
                )
            self.assertEqual(writer.status().pending, 1024)
            self.assertFalse(
                writer.submit_finalization(
                    self.finish_command(start),
                    self.sealed(writer),
                    self.outputs(writer),
                )
            )
            writer.invalidate()
            self.assertEqual(writer.context_budget.charged_bytes, 0)
            release.set()
        self.assertTrue(writer.close(2))

    def test_malformed_context_omission_preserves_independent_valid_output(self):
        writer = self.writer()
        start = self.start_command()
        self.db.write_execution_start(start)
        context = self.sealed(writer)
        context = replace(context, capture_generation=None)
        self.assertTrue(
            writer.submit_finalization(
                self.finish_command(start), context, self.outputs(writer)
            )
        )
        self.wait_for(lambda: writer.status().pending == 0)
        self.assertEqual(
            self.db.get_execution_contexts(start.run_id)["reason"], "invalid_evidence"
        )
        self.assertEqual(
            self.db.get_execution_outputs(start.run_id)["state"], "complete"
        )
        self.assertEqual(writer.context_budget.charged_bytes, 0)

    def test_generation_mismatch_after_same_epoch_recovery_cannot_write_old_outputs(
        self,
    ):
        writer = self.writer()
        start = self.start_command()
        context, outputs = self.sealed(writer), self.outputs(writer)
        writer.invalidate()
        self.clock.advance(5)
        self.wait_for(lambda: writer.status().accepting)
        self.assertEqual(writer.status().epoch, start.storage_epoch)
        self.assertTrue(writer.submit(start))
        self.assertTrue(
            writer.submit_finalization(self.finish_command(start), context, outputs)
        )
        self.wait_for(lambda: writer.status().pending == 0)
        self.assertEqual(
            self.db.get_execution_outputs(start.run_id)["reason"], "invalid_evidence"
        )
        self.assertEqual(
            self.db.get_execution_run(start.run_id)["outcome"], "succeeded"
        )
        self.assertEqual(writer.context_budget.charged_bytes, 0)

    def test_output_commit_serializes_invalidation_without_blocking_close_or_queue(
        self,
    ):
        import database.execution_outputs as outputs_db

        writer = self.writer()
        start = self.start_command()
        self.db.write_execution_start(start)
        entered, release = self.block()
        invalidating, invalidated = threading.Event(), threading.Event()
        committed = threading.Event()
        original = outputs_db._capture

        def capture(*args, **kwargs):
            entered.set()
            release.wait(BLOCK_SECONDS)
            committed.set()
            return original(*args, **kwargs)

        def invalidate():
            invalidating.set()
            writer.invalidate()
            invalidated.set()

        with patch.object(outputs_db, "_capture", capture):
            self.finalization(writer, start)
            self.assertTrue(entered.wait(BLOCK_SECONDS))
            invalidator = threading.Thread(target=invalidate)
            invalidator.start()
            try:
                self.assertTrue(invalidating.wait(BLOCK_SECONDS))
                self.assertFalse(invalidated.wait(0.03))
                self.assertTrue(writer.submit(self.finish_command(start)))
                self.assertFalse(writer.close(0))
                # submit() and close() returned while the commit still held the
                # gate: the blocked invalidation has not finished and the
                # commit is still waiting for its release. Had either call
                # queued behind the gate, it could only return after both.
                self.assertFalse(invalidated.is_set())
                self.assertFalse(committed.is_set())
                self.assertGreater(writer.context_budget.charged_bytes, 0)
            finally:
                release.set()
                invalidator.join(BLOCK_SECONDS)
            self.assertFalse(invalidator.is_alive())
            self.assertTrue(writer.close(2))
        self.assertEqual(
            self.db.get_execution_outputs(start.run_id)["state"], "complete"
        )
        self.assertEqual(writer.context_budget.charged_bytes, 0)

    def test_copied_payload_owner_is_rejected_without_spending_genuine_reservation(
        self,
    ):
        writer = self.writer()
        for item, copy_context in ((1, False), (2, True)):
            start = self.start_command(item)
            self.db.write_execution_start(start)
            context, outputs = self.sealed(writer), self.outputs(writer)
            genuine = context.lease if copy_context else outputs.lease
            self.addCleanup(genuine.release)
            charge = genuine.byte_count
            if copy_context:
                context = replace(context, lease=copy(genuine))
            else:
                outputs = replace(outputs, lease=copy(genuine))
            self.assertTrue(
                writer.submit_finalization(self.finish_command(start), context, outputs)
            )
            self.wait_for(lambda: writer.status().pending == 0)
            self.assertEqual(writer.context_budget.charged_bytes, charge)
            self.assertEqual(genuine.byte_count, charge)
            self.assertEqual(
                self.db.get_execution_outputs(start.run_id)["reason"],
                "reported" if copy_context else "invalid_evidence",
            )
            genuine.release()
            self.assertEqual(writer.context_budget.charged_bytes, 0)

    def test_unexpected_validation_failure_keeps_other_evidence_and_outcome(self):
        writer = self.writer()
        for item, fail_context in ((1, False), (2, True)):
            start = self.start_command(item)
            self.db.write_execution_start(start)
            context, outputs = self.sealed(writer), self.outputs(writer)
            validator = "bundle_payload_size" if fail_context else "output_payload_size"
            with patch(
                "utils.execution_history_writer." + validator,
                side_effect=RuntimeError("private"),
            ):
                self.assertTrue(
                    writer.submit_finalization(
                        self.finish_command(start), context, outputs
                    )
                )
            self.wait_for(lambda: writer.status().pending == 0)
            self.assertEqual(
                self.db.get_execution_run(start.run_id)["outcome"], "succeeded"
            )
            self.assertEqual(
                self.db.get_execution_outputs(start.run_id)["reason"],
                "reported" if fail_context else "invalid_evidence",
            )
            self.assertEqual(
                self.db.get_execution_contexts(start.run_id)["reason"],
                "invalid_evidence" if fail_context else "captured",
            )
            self.assertEqual(writer.context_budget.charged_bytes, 0)

    def test_queue_failure_after_owner_transfer_releases_both_payloads(self):
        writer = self.writer()
        start = self.start_command()
        self.db.write_execution_start(start)
        context, outputs = self.sealed(writer), self.outputs(writer)
        with patch.object(writer._queue, "put_nowait", side_effect=MemoryError):
            self.assertFalse(
                writer.submit_finalization(self.finish_command(start), context, outputs)
            )
        self.assertEqual(writer.context_budget.charged_bytes, 0)
        self.assertEqual(writer.status().pending, 0)
        self.assertEqual(self.db.get_execution_run(start.run_id)["outcome"], "observed")
