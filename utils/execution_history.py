"""Own bounded queue-attempt capture without retaining host workflow payloads."""

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
        self, queue: object, session_id: str, db_factory: Callable[[], PromptDatabase]
    ) -> None:
        self.writer = HistoryWriter(db_factory)
        self._session_id = session_id
        self._lock = threading.Lock()
        self._active: dict[int, str] = {}
        self._disposed = False
        self._unsubscribers: list[Callable[[], None]] = []
        try:
            self._unsubscribers.append(
                subscribe_dequeue(queue, "execution_history", self._dequeue)
            )
            self._unsubscribers.append(
                subscribe_task_done(queue, "execution_history", self._task_done)
            )
            self.writer.start()
            atexit.register(self.dispose)
        except Exception:
            self.dispose()
            raise

    def _ready_epoch(self) -> str | None:
        status = self.writer.status()
        epoch = status.epoch if status.accepting else None
        if any(value != epoch for value in self._active.values()):
            self._active.clear()
        return epoch

    def _dequeue(self, result: object) -> None:
        try:
            with self._lock:
                if self._disposed:
                    return
                epoch = self._ready_epoch()
                if epoch is None:
                    self.writer.record_gap()
                    return
                command = make_start(
                    self._session_id, result, epoch, time.time_ns() // 1_000_000
                )
                if command is None:
                    self.writer.record_gap()
                    return
                item_id = command.queue_item_id
                if item_id in self._active:
                    return
                if len(self._active) >= _MAX_ACTIVE:
                    self.writer.record_gap()
                    return
                if self.writer.submit(command):
                    self._active[item_id] = epoch
        except Exception:
            self.writer.record_gap()

    def _task_done(self, args: tuple, kwargs: dict) -> None:
        try:
            with self._lock:
                if self._disposed:
                    return
                epoch = self._ready_epoch()
                if epoch is None:
                    self.writer.record_gap()
                    return
                item_id = args[0] if args else kwargs.get("item_id")
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
                self._active.pop(command.queue_item_id, None)
                self.writer.submit(command)
        except Exception:
            self.writer.record_gap()

    def dispose(self, timeout: float = 2.0) -> bool:
        """Stop capture, release subscriptions and drain the owned writer boundedly."""
        with self._lock:
            if not self._disposed:
                self._disposed = True
                self._active.clear()
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
            registration = HistoryRegistration(queue, state["session_id"], db_factory)
            state["registration"] = registration
            return registration
    except Exception:
        _logger.warning("Execution history queue capability unavailable")
        return None
