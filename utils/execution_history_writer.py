"""A bounded, single-worker transport for best-effort execution history."""

from dataclasses import dataclass
import math
import queue
import threading
import time
from typing import Callable
from uuid import UUID, uuid5

try:
    from ..database import PromptDatabase
    from ..database.execution_history import (
        HistoryEpochChanged,
        subscribe_history_restore,
    )
except ImportError:
    from database import PromptDatabase
    from database.execution_history import (
        HistoryEpochChanged,
        subscribe_history_restore,
    )

from .execution_context_budget import (
    SHARED_CONTEXT_BUDGET,
    ContextBudget,
    PayloadLease,
    SealedContext,
)
from .execution_contexts import ContextBundle, bundle_payload_size
from .execution_history_events import RunCommand, RunFinish, RunStart, encode_command
from .logging_config import get_logger

_logger = get_logger("prompt_manager.execution_history_writer")
_MAX_PENDING = 1024
_PROBE_INTERVAL = 5.0
_MAX_GAPS = 2**63 - 1


@dataclass(frozen=True)
class HistoryWriterStatus:
    epoch: str | None
    accepting: bool
    pending: int
    recording_gaps: int
    alive: bool
    capture_generation: int


@dataclass(frozen=True)
class _Finalization:
    command: RunFinish
    context: SealedContext


class HistoryWriter:
    """Keep database initialization and all writes off producer threads."""

    def __init__(
        self,
        db_factory: Callable[[], PromptDatabase],
        *,
        clock: Callable[[], float] = time.monotonic,
        context_budget: ContextBudget | None = None,
    ) -> None:
        self.context_budget = (
            SHARED_CONTEXT_BUDGET if context_budget is None else context_budget
        )
        self._db_factory = db_factory
        self._clock = clock
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._queue: queue.Queue[RunCommand | _Finalization] = queue.Queue(
            maxsize=_MAX_PENDING
        )
        self._thread: threading.Thread | None = None
        self._epoch: str | None = None
        self._accepting = False
        self._pending = 0
        self._gaps = 0
        self._generation = 0
        self._closing = False
        self._close_deadline = 0.0
        self._next_probe = 0.0

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or self._closing:
                return
            self._thread = threading.Thread(
                target=self._run, name="prompt-manager-history", daemon=True
            )
            self._thread.start()

    def submit(self, command: RunCommand) -> bool:
        try:
            encode_command(command)
        except ValueError:
            with self._lock:
                self._record_gap_locked()
            self._wake.set()
            return False
        with self._lock:
            if (
                not self._accepting
                or command.storage_epoch != self._epoch
                or self._pending >= _MAX_PENDING
            ):
                self._record_gap_locked()
                accepted = False
            else:
                self._queue.put_nowait(command)
                self._pending += 1
                accepted = True
        self._wake.set()
        return accepted

    @staticmethod
    def _release_context(context: SealedContext) -> None:
        if isinstance(context, SealedContext) and type(context.lease) is PayloadLease:
            context.lease.release()

    def _consume_context_locked(self, context: SealedContext) -> SealedContext:
        try:
            if type(context) is not SealedContext:
                raise ValueError("Invalid sealed context")
            context.validate_header()
            if context.bundle is None:
                if context.lease is not None:
                    raise ValueError("Unexpected payload lease")
                return context
            size = bundle_payload_size(context.bundle)
            if (
                context.capture_generation != self._generation
                or context.capture_generation is None
                or type(context.lease) is not PayloadLease
                or context.lease._pool is not self.context_budget
                or context.lease.byte_count < size
                or context.state != context.bundle.state
                or context.reason != context.bundle.reason
            ):
                raise ValueError("Invalid context ownership or generation")
            owner = context.lease.transfer()
            return SealedContext(
                context.bundle,
                owner,
                context.state,
                context.reason,
                context.capture_generation,
            )
        except ValueError:
            self._release_context(context)
            return SealedContext(None, None, "unavailable", "invalid_evidence")

    def submit_finalization(self, command: RunFinish, context: SealedContext) -> bool:
        try:
            if type(command) is not RunFinish:
                raise ValueError("Finalization requires finish")
            encode_command(command)
        except ValueError:
            self._release_context(context)
            self.record_gap()
            return False
        with self._lock:
            if (
                not self._accepting
                or command.storage_epoch != self._epoch
                or self._pending >= _MAX_PENDING
            ):
                self._release_context(context)
                self._record_gap_locked()
                accepted = False
            else:
                owned = self._consume_context_locked(context)
                self._queue.put_nowait(_Finalization(command, owned))
                self._pending += 1
                accepted = True
        self._wake.set()
        return accepted

    def _record_gap_locked(self) -> None:
        self._gaps = min(_MAX_GAPS, self._gaps + 1)

    def record_gap(self) -> None:
        """Account for unavailable capture without constructing a fake command."""
        with self._lock:
            self._record_gap_locked()
        self._wake.set()

    def invalidate(self) -> None:
        with self._lock:
            self._accepting = False
            self._epoch = None
            self._generation += 1
            self._next_probe = self._clock() + _PROBE_INTERVAL
            self._discard_locked()
        self._wake.set()

    def status(self) -> HistoryWriterStatus:
        with self._lock:
            return HistoryWriterStatus(
                self._epoch,
                self._accepting,
                self._pending,
                self._gaps,
                self._thread is not None and self._thread.is_alive(),
                self._generation,
            )

    def close(self, timeout: float = 2.0) -> bool:
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("Shutdown timeout must be finite and nonnegative")
        with self._lock:
            deadline = time.monotonic() + timeout
            if not self._closing:
                self._closing = True
                self._close_deadline = deadline
                self._accepting = False
            thread = self._thread
        self._wake.set()
        if thread is not None:
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            if self._closing and time.monotonic() >= self._close_deadline:
                self._discard_locked()
        return thread is None or not thread.is_alive()

    def _discard_locked(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if isinstance(item, _Finalization):
                self._release_context(item.context)
            self._pending -= 1
            self._record_gap_locked()

    def _run(self) -> None:
        db: PromptDatabase | None = None
        unsubscribe: Callable[[], None] | None = None
        reported_gaps = 0
        next_diagnostic = 0.0
        try:
            while True:
                self._wake.clear()
                with self._lock:
                    if self._closing and (
                        self._pending == 0 or time.monotonic() >= self._close_deadline
                    ):
                        self._discard_locked()
                        break
                    generation = self._generation
                    probe = (
                        self._epoch is None
                        and not self._closing
                        and self._clock() >= self._next_probe
                    )
                    if probe:
                        self._next_probe = self._clock() + _PROBE_INTERVAL
                    command = None
                    if self._epoch is not None:
                        try:
                            command = self._queue.get_nowait()
                        except queue.Empty:
                            pass
                if probe:
                    try:
                        if db is None:
                            db = self._db_factory()
                        with self._lock:
                            closing = self._closing
                        if closing:
                            continue
                        if unsubscribe is None:
                            unsubscribe = subscribe_history_restore(
                                db.model.db_path, self.invalidate
                            )
                        db.model.get_connection().execute("PRAGMA busy_timeout=250")
                        epoch = db.get_execution_epoch()
                        with self._lock:
                            if not self._closing and generation == self._generation:
                                self._epoch = epoch
                                self._accepting = True
                    except Exception:
                        # The next scheduled probe opens a clean managed connection.
                        if db is not None:
                            db.model.close()
                        with self._lock:
                            self._record_gap_locked()
                if command is not None:
                    failed = False
                    context = None
                    if isinstance(command, _Finalization):
                        context = command.context
                        command = command.command
                    try:
                        # Restore may have reopened this thread's managed connection.
                        db.model.get_connection().execute("PRAGMA busy_timeout=250")
                        if isinstance(command, RunStart):
                            db.write_execution_start(command)
                        else:
                            db.write_execution_finish(command)
                        if context is not None:
                            with self._lock:
                                current = (
                                    generation == self._generation
                                    and command.storage_epoch == self._epoch
                                    and (
                                        context.capture_generation is None
                                        or context.capture_generation
                                        == self._generation
                                    )
                                )
                            if current:
                                try:
                                    db.write_execution_contexts(
                                        str(
                                            uuid5(
                                                UUID(command.session_id),
                                                str(command.queue_item_id),
                                            )
                                        ),
                                        command.storage_epoch,
                                        (
                                            context.bundle
                                            if context.bundle is not None
                                            else ContextBundle(
                                                (), context.state, context.reason
                                            )
                                        ),
                                    )
                                except ValueError:
                                    # Validation/collision cannot revoke the outcome.
                                    with self._lock:
                                        self._record_gap_locked()
                    except HistoryEpochChanged:
                        # The exception's storage_epoch is only a hint; readiness
                        # comes from a later probe and never replays this command.
                        failed = True
                    except Exception:
                        failed = True
                    finally:
                        if context is not None:
                            self._release_context(context)
                            context = None
                    with self._lock:
                        self._pending -= 1
                        if failed:
                            self._record_gap_locked()
                            self._accepting = False
                            self._epoch = None
                            self._generation += 1
                            self._next_probe = self._clock() + _PROBE_INTERVAL
                            self._discard_locked()
                with self._lock:
                    gaps = self._gaps
                    has_work = self._epoch is not None and not self._queue.empty()
                now = self._clock()
                if gaps != reported_gaps and now >= next_diagnostic:
                    _logger.warning(
                        "Execution history recording gaps: %s", gaps - reported_gaps
                    )
                    reported_gaps = gaps
                    next_diagnostic = now + _PROBE_INTERVAL
                if not has_work:
                    self._wake.wait(0.05)
        finally:
            with self._lock:
                self._accepting = False
                self._epoch = None
                self._discard_locked()
            if unsubscribe is not None:
                unsubscribe()
            if db is not None:
                db.model.close()
