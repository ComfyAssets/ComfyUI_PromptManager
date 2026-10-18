"""Own bounded queue-attempt capture without retaining host workflow payloads."""

from collections import OrderedDict
import atexit
import threading
import time
from typing import Callable
from uuid import uuid4

try:
    from ..database import PromptDatabase
except ImportError:
    from database import PromptDatabase

from .execution_history_events import make_finish, make_start
from .execution_context_capture import ContextCapture, active_capture, unbind_current
from .execution_context_host import observe_cached_context, subscribe_executor_messages
from .execution_history_writer import HistoryWriter
from .logging_config import get_logger
from .queue_observers import subscribe_dequeue, subscribe_task_done

_logger = get_logger("prompt_manager.execution_history")
_STATE_ATTRIBUTE = "_prompt_manager_execution_history"
_registration_lock = threading.Lock()
_MAX_ACTIVE = 128


class HistoryRegistration:
    """Own subscriptions, one asynchronous writer and bounded attempt identities."""

    def __init__(
        self,
        queue: object,
        session_id: str,
        db_factory: Callable[[], PromptDatabase],
        *,
        high_water: int = -1,
    ) -> None:
        self.writer = HistoryWriter(
            db_factory, on_invalidation=self._invalidate_capture
        )
        self.capture = ContextCapture(
            id(queue), self.writer.status, self.writer.context_budget
        )
        self._capabilities = {}
        self._accepted = OrderedDict()
        self._high_water = high_water
        self._session_id = session_id
        self._lock = threading.Lock()
        self._active: dict[int, str] = {}
        self._disposed = False
        self._capture_generation = None
        self._unsubscribers: list[Callable[[], None]] = []
        try:
            self._unsubscribers.append(
                subscribe_dequeue(queue, "execution_history", self._dequeue)
            )
            self._unsubscribers.append(
                subscribe_task_done(queue, "execution_history", self._task_done)
            )
            try:
                self._unsubscribers.append(
                    subscribe_executor_messages(
                        f"execution_history_{id(self)}", self._executor_message
                    )
                )
            except (ImportError, AttributeError, TypeError, ValueError):
                # Optional cache evidence must not prevent lifecycle tracking.
                pass
            self.writer.start()
            atexit.register(self.dispose)
        except Exception:
            self.dispose()
            raise

    def _executor_message(self, executor: object, args: tuple, kwargs: dict) -> None:
        binding = active_capture()
        if binding is None or binding[0] is not self.capture:
            return
        event = args[0] if args else kwargs.get("event")
        data = args[1] if len(args) > 1 else kwargs.get("data")
        observe_cached_context(executor, event, data)

    def _invalidate_capture(self) -> None:
        self.capture.revoke_all()

    def _ready_epoch(self) -> str | None:
        status = self.writer.status()
        epoch = status.epoch if status.accepting else None
        if status.capture_generation != self._capture_generation or any(
            value != epoch for value in self._active.values()
        ):
            self._active.clear()
            self._capabilities.clear()
            self.capture.revoke_all()
        self._capture_generation = status.capture_generation
        return epoch

    def _dequeue(self, result: object) -> None:
        try:
            with self._lock:
                epoch = self._ready_epoch()
                command = (
                    make_start(
                        self._session_id, result, epoch, time.time_ns() // 1_000_000
                    )
                    if epoch
                    else None
                )
                accepted = (
                    self._accepted.get(command.queue_item_id)
                    if command is not None
                    else None
                )
                if command is not None and accepted == (
                    command.job_id,
                    command.storage_epoch,
                ):
                    return
                unbind_current()
                if self._disposed:
                    return
                if command is None or command.queue_item_id in self._active:
                    self.writer.record_gap()
                    return
                if len(self._active) >= _MAX_ACTIVE:
                    self.writer.record_gap()
                    return
                fresh = command.queue_item_id > self._high_water
                self._high_water = max(self._high_water, command.queue_item_id)
                if self.writer.submit(command):
                    self._accepted[command.queue_item_id] = (
                        command.job_id,
                        command.storage_epoch,
                    )
                    if len(self._accepted) > 128:
                        self._accepted.popitem(last=False)
                    self._active[command.queue_item_id] = epoch
                    if not fresh:
                        return
                    queue_item = result[0]
                    graph = queue_item[2] if len(queue_item) > 2 else None
                    outputs = queue_item[4] if len(queue_item) > 4 else None
                    cap = self.capture.begin(
                        command, self.writer.status().capture_generation, graph, outputs
                    )
                    self._capabilities[command.queue_item_id] = cap
        except Exception:
            unbind_current()
            self.writer.record_gap()

    def _task_done(self, args: tuple, kwargs: dict) -> None:
        sealed = None
        try:
            with self._lock:
                if self._disposed:
                    return
                epoch = self._ready_epoch()
                if epoch is None:
                    self.writer.record_gap()
                    return
                item_id = args[0] if args else kwargs.get("item_id")
                if type(item_id) is int:
                    cap = self._capabilities.pop(item_id, None)
                    self._active.pop(item_id, None)
                    if cap is not None:
                        sealed = self.capture.seal(cap)
                status = args[2] if len(args) > 2 else kwargs.get("status")
                command = make_finish(
                    self._session_id,
                    item_id,
                    epoch,
                    status,
                    time.time_ns() // 1_000_000,
                )
                if command is None:
                    self.writer.record_gap()
                    return
                if sealed is not None:
                    self.writer.submit_finalization(command, sealed)
                    sealed = None
                else:
                    self.writer.submit(command)
        except Exception:
            self.writer.record_gap()
        finally:
            if sealed is not None and sealed.lease is not None:
                sealed.lease.release()

    def dispose(self, timeout: float = 2.0) -> bool:
        """Stop capture, release subscriptions and drain the owned writer boundedly."""
        with self._lock:
            if not self._disposed:
                self._disposed = True
                self._active.clear()
                self._capabilities.clear()
                self.capture.revoke_all()
                for unsubscribe in self._unsubscribers:
                    unsubscribe()
                self._unsubscribers.clear()
                atexit.unregister(self.dispose)
        return self.writer.close(timeout)


def register_history_hook(
    server: object, db_factory: Callable[[], PromptDatabase]
) -> HistoryRegistration | None:
    """Register only on queues supporting actual dequeue and final callbacks."""
    try:
        queue = getattr(server, "prompt_queue", None)
        if not all(
            callable(getattr(queue, method, None)) for method in ("get", "task_done")
        ):
            return None
        with _registration_lock:
            state = getattr(queue, _STATE_ATTRIBUTE, None)
            if state is None:
                state = {"session_id": str(uuid4()), "registration": None}
                setattr(queue, _STATE_ATTRIBUTE, state)
            previous = state["registration"]
            if previous is not None and not previous._disposed:
                return previous
            registration = HistoryRegistration(
                queue,
                state["session_id"],
                db_factory,
                high_water=previous._high_water if previous is not None else -1,
            )
            state["registration"] = registration
            return registration
    except Exception:
        _logger.warning("Execution history queue capability unavailable")
        return None
