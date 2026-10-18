"""Batch drain detects stalls without imposing a SQLite throughput target."""

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import test_execution_history_hooks as hooks
from utils.execution_history_writer import HistoryWriterStatus


class StepClock:
    def __init__(self, step: float):
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


class ObservedWriter:
    def __init__(self, pending: int, *, progress: bool = False, alive: bool = True):
        self.pending = pending
        self.progress = progress
        self.alive = alive
        self.context_budget = SimpleNamespace(charged_bytes=281)

    def status(self) -> HistoryWriterStatus:
        status = HistoryWriterStatus("epoch", True, self.pending, 0, self.alive, 0)
        if self.progress and self.pending:
            self.pending -= 1
        return status


class HistoryDrainTests(unittest.TestCase):
    wait_for = hooks.HistoryHooksTests.wait_for

    def drain(self, writer: ObservedWriter, clock: StepClock) -> None:
        with patch.object(hooks, "time", SimpleNamespace(monotonic=clock)):
            hooks.HistoryHooksTests.drain(self, SimpleNamespace(writer=writer))

    def test_batch_with_progress_can_exceed_short_stall_deadline(self):
        writer = ObservedWriter(128, progress=True)
        clock = StepClock(0.04)
        self.drain(writer, clock)
        self.assertEqual(writer.pending, 0)
        self.assertGreater(clock.value, 3)
        self.assertLess(clock.value, 30)

    def test_no_progress_fails_with_worker_and_lease_diagnostics(self):
        writer = ObservedWriter(52)
        clock = StepClock(0.5)
        with self.assertRaisesRegex(
            AssertionError, r"worker stalled.*pending=52.*charged_bytes=281"
        ):
            self.drain(writer, clock)
        self.assertLessEqual(clock.value, 4)

    def test_progress_cannot_extend_batch_past_hard_ceiling(self):
        writer = ObservedWriter(1000, progress=True)
        clock = StepClock(0.5)
        with self.assertRaisesRegex(AssertionError, "batch deadline"):
            self.drain(writer, clock)
        self.assertGreater(writer.pending, 0)
        self.assertGreaterEqual(clock.value, 30)
        self.assertLessEqual(clock.value, 31)

    def test_dead_worker_with_pending_work_fails_immediately(self):
        writer = ObservedWriter(1, alive=False)
        clock = StepClock(0.5)
        with self.assertRaisesRegex(AssertionError, r"worker stopped.*alive=False"):
            self.drain(writer, clock)
        self.assertLess(clock.value, 3)

    def test_oscillating_pending_is_not_new_progress(self):
        writer = ObservedWriter(1)
        original = writer.status
        calls = 0

        def oscillating() -> HistoryWriterStatus:
            nonlocal calls
            calls += 1
            return replace(original(), pending=1 + calls % 2)

        writer.status = oscillating
        clock = StepClock(0.5)
        with self.assertRaisesRegex(AssertionError, "worker stalled"):
            self.drain(writer, clock)
        self.assertLessEqual(clock.value, 5)
