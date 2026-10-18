"""Own bounded queue-attempt capture without retaining host workflow payloads."""

from collections import OrderedDict
import atexit
import os
import threading
import time
from typing import Callable
from uuid import uuid4

try:
    from ..database import PromptDatabase
except ImportError:
    from database import PromptDatabase

from .execution_history_events import make_finish, make_start
from .execution_context_capture import (
    AttemptCapability,
    ContextCapture,
    active_capture,
    unbind_current,
)
from .execution_context_budget import SealedContext
from .execution_output_capture import MISSING_HISTORY, SealedOutputs, seal_outputs
from .execution_outputs import OutputRoots, snapshot_output_roots
from .execution_context_host import observe_cached_context, subscribe_executor_messages
from .execution_history_writer import HistoryWriter
from .logging_config import get_logger
from .queue_observers import subscribe_dequeue, subscribe_task_done

_logger = get_logger("prompt_manager.execution_history")
_STATE_ATTRIBUTE = "_prompt_manager_execution_history"
_registration_lock = threading.Lock()
_MAX_ACTIVE = 128


def _configured_output_roots() -> OutputRoots:
    import folder_paths

    return snapshot_output_roots(
        "windows" if os.name == "nt" else "posix",
        folder_paths.get_output_directory(),
        folder_paths.get_temp_directory(),
    )


class HistoryRegistration:
    """Own subscriptions, one asynchronous writer and bounded attempt identities."""

    def __init__(
        self,
        queue: object,
        session_id: str,
        db_factory: Callable[[], PromptDatabase],
        *,
        high_water: int = -1,
        roots_reader: Callable[[], OutputRoots] | None = None,
    ) -> None:
        self.writer = HistoryWriter(
            db_factory, on_invalidation=self._invalidate_capture
        )
        self.capture = ContextCapture(
            id(queue), self.writer.status, self.writer.context_budget
        )
        self._capabilities = {}
        self._roots = {}
        self._roots_reader = roots_reader or _configured_output_roots
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
        with self._lock:
            self._active.clear()
            self._capabilities.clear()
            self._roots.clear()
            self.capture.revoke_all()

    def _read_roots(self, path_style: str | None = None) -> OutputRoots:
        try:
            roots = self._roots_reader()
            if (
                type(roots) is not OutputRoots
                or type(roots.path_style) is not str
                or roots.path_style not in ("posix", "windows")
            ):
                raise ValueError("Invalid output roots")
            for key in (roots.output_key, roots.temp_key):
                if key is not None and (
                    type(key) is not str
                    or len(key) != 64
                    or any(char not in "0123456789abcdef" for char in key)
                ):
                    raise ValueError("Invalid output root key")
            return roots
        except Exception:
            return OutputRoots(
                path_style or ("windows" if os.name == "nt" else "posix"),
                None,
                None,
            )

    def _ready_epoch(self) -> str | None:
        status = self.writer.status()
        epoch = status.epoch if status.accepting else None
        if status.capture_generation != self._capture_generation or any(
            value != epoch for value in self._active.values()
        ):
            self._active.clear()
            self._capabilities.clear()
            self._roots.clear()
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
                binding = active_capture()
                unbind_current()
                if binding is not None and binding[0] is self.capture:
                    previous = binding[1]
                    self._roots.pop(previous.queue_item_id, None)
                    self._capabilities.pop(previous.queue_item_id, None)
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
                    if self.capture.valid(cap):
                        self._capabilities[command.queue_item_id] = cap
                        self._roots[command.queue_item_id] = (cap, self._read_roots())
        except Exception:
            unbind_current()
            self.writer.record_gap()

    def _task_done(self, args: tuple, kwargs: dict) -> None:
        sealed = None
        outputs = None
        cap = None
        roots_owner = None
        try:
            with self._lock:
                if self._disposed:
                    return
                epoch = self._ready_epoch()
                if epoch is None:
                    self.writer.record_gap()
                    return
                item_id = args[0] if args else kwargs.get("item_id")
                owned = False
                if type(item_id) is int:
                    cap = self._capabilities.pop(item_id, None)
                    roots_owner = self._roots.pop(item_id, None)
                    active_epoch = self._active.pop(item_id, None)
                    owned = (
                        type(cap) is AttemptCapability
                        and roots_owner is not None
                        and roots_owner[0] is cap
                        and active_epoch == epoch
                        and cap.session_id == self._session_id
                        and cap.queue_item_id == item_id
                        and cap.storage_epoch == epoch
                        and self.capture.valid(cap)
                    )
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
                if owned:
                    # Capture authority is consumed once; the local identity remains
                    # trusted after sealing discards the recorder and its samples.
                    try:
                        sealed = self.capture.seal(cap)
                    except Exception:
                        sealed = SealedContext(
                            None, None, "unavailable", "invalid_evidence"
                        )
                    finally:
                        self.capture.revoke(cap)
                    try:
                        context_bytes = sealed.lease.byte_count if sealed.lease else 0
                        history = (
                            args[1]
                            if len(args) > 1
                            else kwargs.get("history_result", MISSING_HISTORY)
                        )
                        outputs = seal_outputs(
                            history,
                            roots_owner[1],
                            self._read_roots(roots_owner[1].path_style),
                            budget=self.writer.context_budget,
                            capture_generation=cap.capture_generation,
                            context_bytes=context_bytes,
                        )
                    except Exception:
                        outputs = SealedOutputs(
                            None,
                            None,
                            "unavailable",
                            "invalid_evidence",
                            cap.capture_generation,
                        )
                    self.writer.submit_finalization(command, sealed, outputs)
                    sealed = outputs = None
                else:
                    self.writer.submit(command)
        except Exception:
            self.writer.record_gap()
        finally:
            if type(cap) is AttemptCapability:
                self.capture.revoke(cap)
            if roots_owner is not None:
                self.capture.revoke(roots_owner[0])
            for payload in (sealed, outputs):
                if payload is not None and payload.lease is not None:
                    payload.lease.release()

    def dispose(self, timeout: float = 2.0) -> bool:
        """Stop capture, release subscriptions and drain the owned writer boundedly."""
        with self._lock:
            if not self._disposed:
                self._disposed = True
                self._active.clear()
                self._capabilities.clear()
                self._roots.clear()
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
