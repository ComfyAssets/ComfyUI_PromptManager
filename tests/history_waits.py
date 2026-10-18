"""Finite test waits for real SQLite batches without a throughput requirement."""

import threading
import time
from typing import Callable

from utils.execution_history_writer import HistoryWriter


def drain_writer(
    writer: HistoryWriter, *, clock: Callable[[], float] = time.monotonic
) -> None:
    initial = current = writer.status()
    started = last_progress = clock()
    lowest_pending = initial.pending
    while current.pending:
        now = clock()
        if current.pending < lowest_pending:
            lowest_pending = current.pending
            last_progress = now
        reason = None
        if not current.alive:
            reason = "worker stopped"
        elif now - started >= 30:
            reason = "batch deadline"
        elif now - last_progress >= 3:
            reason = "worker stalled"
        if reason is not None:
            raise AssertionError(
                f"history drain {reason}: elapsed={now - started:.3f}s, "
                f"stalled={now - last_progress:.3f}s, "
                f"initial={initial}, current={current}, "
                f"charged_bytes={writer.context_budget.charged_bytes}"
            )
        threading.Event().wait(0.005)
        current = writer.status()
