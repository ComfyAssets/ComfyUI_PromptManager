"""Exact successful host callbacks persist independently bounded output evidence."""

from dataclasses import replace
import threading
import sys
from types import SimpleNamespace
from unittest.mock import patch

from utils.execution_history import HistoryRegistration
from utils.execution_context_budget import ContextBudget
from utils.execution_outputs import snapshot_output_roots
from utils.queue_observers import subscribe_dequeue, subscribe_task_done

try:
    from tests import test_execution_history_hooks as fixtures
except ImportError:
    import test_execution_history_hooks as fixtures


def history(filename="a.png"):
    return {
        "outputs": {
            "save": {
                "images": [{"filename": filename, "subfolder": "", "type": "output"}]
            }
        }
    }


class OutputHooksTests(fixtures.unittest.TestCase):
    setUp = fixtures.HistoryHooksTests.setUp
    restore_logger = fixtures.HistoryHooksTests.restore_logger
    cleanup_registrations = fixtures.HistoryHooksTests.cleanup_registrations
    wait_for = fixtures.HistoryHooksTests.wait_for
    dequeue = fixtures.HistoryHooksTests.dequeue
    rows = fixtures.HistoryHooksTests.rows
    drain = fixtures.HistoryHooksTests.drain

    def register(self, roots_reader=None, pool=None):
        if roots_reader is None:

            def roots_reader():
                return snapshot_output_roots("posix", "/output", "/temp")

        if pool is None:
            registration = HistoryRegistration(
                self.queue, fixtures.JOB, lambda: self.db, roots_reader=roots_reader
            )
        else:
            from utils.execution_history_writer import HistoryWriter

            with patch(
                "utils.execution_history.HistoryWriter",
                lambda factory, **kw: HistoryWriter(factory, context_budget=pool, **kw),
            ):
                registration = HistoryRegistration(
                    self.queue, fixtures.JOB, lambda: self.db, roots_reader=roots_reader
                )
        self.registrations.append(registration)
        self.wait_for(lambda: registration.writer.status().accepting)
        return registration

    def outputs(self, item_id):
        row = next(row for row in self.rows() if row["queue_item_id"] == item_id)
        return self.db.get_execution_outputs(row["run_id"])

    def test_positional_keyword_and_text_only_outputs_use_exact_fresh_attempt(self):
        registration = self.register()
        for i in range(3):
            self.dequeue(i)
            payload = history("a.png" if i < 2 else "b.png")
            if i == 1:
                self.queue.task_done(
                    item_id=i,
                    history_result=payload,
                    status=fixtures.status("execution_error"),
                )
            else:
                self.assertIs(
                    self.queue.task_done(i, payload, fixtures.status()),
                    self.queue.done_result,
                )
        self.drain(registration)
        self.assertEqual(
            [self.outputs(i)["references"][0]["filename"] for i in range(3)],
            ["a.png", "a.png", "b.png"],
        )
        self.assertEqual(
            [r["outcome"] for r in self.rows()], ["succeeded", "failed", "succeeded"]
        )
        self.assertEqual(registration.writer.context_budget.charged_bytes, 0)

    def test_budget_denied_context_still_captures_independently_affordable_outputs(
        self,
    ):
        pool = ContextBudget(2048)
        registration = self.register(pool=pool)
        self.dequeue(0)
        self.queue.task_done(0, history(), fixtures.status())
        self.drain(registration)
        self.assertEqual(self.outputs(0)["state"], "complete")
        self.assertEqual(
            self.db.get_execution_contexts(self.rows()[0]["run_id"])["reason"],
            "budget_exhausted",
        )
        self.assertEqual(pool.charged_bytes, 0)

    def test_missing_history_and_explicit_none_are_distinct_from_empty_evidence(self):
        registration = self.register()
        for i in range(4):
            self.dequeue(i)
            if i == 0:
                self.queue.task_done(item_id=i, status=fixtures.status())
            else:
                self.queue.task_done(
                    i,
                    [None, {"outputs": {}}, {"outputs": {"text": {"text": ["ok"]}}}][
                        i - 1
                    ],
                    fixtures.status(),
                )
        self.drain(registration)
        self.assertEqual(
            [(self.outputs(i)["state"], self.outputs(i)["reason"]) for i in range(4)],
            [
                ("unavailable", "unavailable"),
                ("unavailable", "invalid_evidence"),
                ("complete", "none_reported"),
                ("complete", "none_reported"),
            ],
        )

    def test_replay_old_dequeue_reused_uuid_and_forged_capability_cannot_rebind(self):
        registration = self.register()
        old = self.dequeue(1)
        self.queue.task_done(1, history("first.png"), fixtures.status())
        self.dequeue(2)
        registration._dequeue(old)
        self.queue.task_done(1, history("replayed.png"), fixtures.status())
        self.queue.task_done(2, history("second.png"), fixtures.status())
        self.dequeue(3)
        cap = registration._capabilities[3]
        registration._capabilities[3] = replace(cap)
        self.queue.task_done(3, history("forged.png"), fixtures.status())
        registration.capture.revoke(cap)
        self.drain(registration)
        self.assertEqual(self.outputs(1)["references"][0]["filename"], "first.png")
        self.assertEqual(self.outputs(2)["references"][0]["filename"], "second.png")
        self.assertEqual(self.outputs(3)["state"], "not_captured")
        self.assertEqual(registration.writer.context_budget.charged_bytes, 0)

    def test_root_snapshot_mutation_and_reader_errors_preserve_outcome_and_reference(
        self,
    ):
        roots = [snapshot_output_roots("posix", "/before", "/temp")]

        def reader():
            if isinstance(roots[0], Exception):
                raise roots[0]
            return roots[0]

        registration = self.register(reader)
        self.dequeue(0)
        roots[0] = snapshot_output_roots("posix", "/after", "/temp")
        self.queue.task_done(0, history(), fixtures.status())
        self.dequeue(1)
        roots[0] = RuntimeError("private")
        self.queue.task_done(1, history(), fixtures.status())
        self.dequeue(2)
        self.queue.task_done(2, history(), fixtures.status())
        self.drain(registration)
        for i in range(3):
            self.assertEqual(self.outputs(i)["reason"], "root_unavailable")
            self.assertIsNone(self.outputs(i)["references"][0]["root_key"])
        self.assertEqual([r["outcome"] for r in self.rows()], ["succeeded"] * 3)

    def test_unexpected_output_producer_error_preserves_sealed_context_and_outcome(
        self,
    ):
        registration = self.register()
        graph = {
            "p": {
                "class_type": "PromptManagerPair",
                "inputs": {"text": "raw", "negative_text": "neg"},
            },
            "s": {
                "class_type": "KSampler",
                "inputs": {"positive": ["p", 0], "negative": ["p", 1]},
            },
        }
        self.queue.results.append(((0, fixtures.JOB, graph, {}, ["s"]), 0))
        self.queue.get()
        with patch(
            "utils.execution_output_capture._extract",
            side_effect=RuntimeError("private"),
        ):
            self.queue.task_done(0, history(), fixtures.status())
        self.drain(registration)
        self.assertEqual(self.rows()[0]["outcome"], "succeeded")
        self.assertEqual(self.outputs(0)["reason"], "invalid_evidence")
        self.assertEqual(
            self.db.get_execution_contexts(self.rows()[0]["run_id"])["reason"],
            "partial",
        )
        self.assertEqual(
            self.db.get_execution_contexts(self.rows()[0]["run_id"])["contexts"][0][
                "positive"
            ]["raw"],
            "raw",
        )
        self.assertEqual(registration.writer.context_budget.charged_bytes, 0)

    def test_unexpected_context_seal_and_projection_errors_do_not_lose_output(self):
        registration = self.register()
        with patch(
            "utils.execution_context_capture.project_contexts",
            side_effect=RuntimeError("private"),
        ):
            self.dequeue(0)
        self.queue.task_done(0, history(), fixtures.status())
        self.dequeue(1)
        with patch(
            "utils.execution_context_capture.bundle_payload_size",
            side_effect=RuntimeError("private"),
        ):
            self.queue.task_done(1, history(), fixtures.status())
        self.drain(registration)
        self.assertEqual([self.outputs(i)["state"] for i in range(2)], ["complete"] * 2)
        self.assertEqual([r["outcome"] for r in self.rows()], ["succeeded"] * 2)
        self.assertEqual(registration.writer.context_budget.charged_bytes, 0)

    def test_original_failure_missing_callback_disposal_and_observer_coexistence(self):
        calls = []
        before_get, before_done = self.queue.get, self.queue.task_done
        unsubscribe_get = subscribe_dequeue(
            self.queue, "other", lambda result: calls.append(result[1])
        )
        unsubscribe_done = subscribe_task_done(
            self.queue, "other", lambda args, kwargs: calls.append(args[0])
        )
        self.addCleanup(unsubscribe_get)
        self.addCleanup(unsubscribe_done)
        registration = self.register()
        self.dequeue(0)
        late = registration._task_done
        self.assertTrue(registration.dispose())
        thread = threading.Thread(
            target=lambda: late((0, history(), fixtures.status()), {})
        )
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.queue.task_done(0, history(), fixtures.status())
        self.assertEqual(calls, [0, 0])
        self.assertEqual(self.outputs(0)["state"], "not_captured")
        unsubscribe_get()
        unsubscribe_done()
        self.assertEqual(self.queue.get, before_get)
        self.assertEqual(self.queue.task_done, before_done)
        self.assertEqual(registration.writer.context_budget.charged_bytes, 0)

    def test_rejected_invalid_revoked_or_unfresh_dequeue_never_reads_roots(self):
        reads = []

        def reader():
            reads.append(1)
            return snapshot_output_roots("posix", "/output", "/temp")

        registration = self.register(reader)
        self.dequeue(10)
        self.dequeue(9)
        self.queue.task_done(9, history(), fixtures.status())
        self.queue.task_done(10, history(), fixtures.status())
        self.dequeue(11, job="invalid")
        self.queue.task_done(11, history(), fixtures.status())
        self.drain(registration)
        self.assertEqual(reads, [1])  # 10 was revoked by the next actual dequeue.
        self.assertEqual(self.outputs(9)["state"], "not_captured")
        self.assertEqual(self.outputs(10)["state"], "not_captured")
        self.assertEqual(registration.writer.context_budget.charged_bytes, 0)

    def test_original_failures_preserve_exception_and_leave_outputs_missing(
        self,
    ):
        error = RuntimeError("host error")

        def fail(*args, **kwargs):
            raise error

        original_get = self.queue.get
        self.queue.get = fail
        registration = self.register()
        with self.assertRaises(RuntimeError) as raised:
            self.queue.get()
        self.assertIs(raised.exception, error)
        self.assertEqual(self.rows(), [])
        self.assertTrue(registration.dispose())
        self.queue.get = original_get
        self.queue.task_done = fail
        registration = self.register()
        self.dequeue(1)
        with self.assertRaises(RuntimeError) as raised:
            self.queue.task_done(1, history(), fixtures.status())
        self.assertIs(raised.exception, error)
        self.drain(registration)
        self.assertEqual(self.rows()[0]["outcome"], "observed")
        self.assertEqual(self.outputs(1)["state"], "not_captured")
        self.assertTrue(registration.dispose())
        self.assertEqual(registration.writer.context_budget.charged_bytes, 0)

    def test_default_adapter_uses_configured_output_and_temp_namespaces(self):
        folder_paths = SimpleNamespace(
            get_output_directory=lambda: "/output", get_temp_directory=lambda: "/temp"
        )
        with patch.dict(sys.modules, {"folder_paths": folder_paths}):
            registration = HistoryRegistration(
                self.queue, fixtures.JOB, lambda: self.db
            )
            self.registrations.append(registration)
            self.wait_for(lambda: registration.writer.status().accepting)
            self.dequeue(0)
            self.queue.task_done(0, history(), fixtures.status())
            self.drain(registration)
        self.assertEqual(self.outputs(0)["state"], "complete")
        self.assertEqual(len(self.outputs(0)["references"][0]["root_key"]), 64)
        self.assertEqual(registration._roots, {})

    def test_sealed_context_is_discarded_before_output_reservation(self):
        pool = ContextBudget(1048576)
        registration = self.register(pool=pool)
        self.dequeue(0)
        self.assertEqual(pool.charged_bytes, 1048576)
        self.queue.task_done(0, history(), fixtures.status())
        self.drain(registration)
        self.assertEqual(self.outputs(0)["state"], "complete")
        self.assertEqual(pool.charged_bytes, 0)

    def test_saturated_start_cannot_capture_outputs_or_retain_previous_roots(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = self.db.write_execution_start

        def blocked(command):
            entered.set()
            release.wait(3)
            return original(command)

        with patch.object(self.db, "write_execution_start", blocked):
            registration = self.register()
            self.dequeue(0)
            self.assertTrue(entered.wait(1))
            from utils.execution_history_events import make_start

            epoch = registration.writer.status().epoch
            for item in range(1023):
                command = make_start(
                    registration._session_id, fixtures.item(1000 + item), epoch, 0
                )
                self.assertTrue(registration.writer.submit(command))
            self.dequeue(1)
            self.assertEqual(registration._roots, {})
            self.queue.task_done(0, history("revoked.png"), fixtures.status())
            self.queue.task_done(1, history("rejected.png"), fixtures.status())
            registration.writer.invalidate()
            release.set()
            self.assertTrue(registration.dispose(2))
        self.assertEqual(self.outputs(0)["state"], "not_captured")
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(registration.writer.context_budget.charged_bytes, 0)
