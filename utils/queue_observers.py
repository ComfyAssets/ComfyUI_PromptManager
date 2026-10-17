"""Keyed, independently removable observers for successful queue operations."""

import functools
import threading
import time
from typing import Callable

from .logging_config import get_logger

_logger = get_logger("prompt_manager.queue_observers")
_STATE_ATTRIBUTE = "_prompt_manager_queue_observers"
_registration_lock = threading.Lock()


class _Observation:
    def __init__(self, queue: object, method: str) -> None:
        self.queue = queue
        self.method = method
        self.lock = threading.Lock()
        self.callbacks: dict[str, tuple[object, Callable]] = {}
        self.original = None
        self.wrapper = None
        self.installed = False
        self.next_warning = 0.0

    def install(self) -> None:
        original = getattr(self.queue, self.method, None)
        if not callable(original):
            raise ValueError("Queue capability unavailable")

        @functools.wraps(original)
        def observe(*args: object, **kwargs: object) -> object:
            result = original(*args, **kwargs)
            if self.method == "task_done" or result is not None:
                with self.lock:
                    callbacks = tuple(entry[1] for entry in self.callbacks.values())
                for callback in callbacks:
                    try:
                        if self.method == "get":
                            callback(result)
                        else:
                            callback(args, kwargs)
                    except Exception:
                        with self.lock:
                            now = time.monotonic()
                            warn = now >= self.next_warning
                            if warn:
                                self.next_warning = now + 5.0
                        if warn:
                            _logger.warning("Queue observation callback failed")
            return result

        setattr(self.queue, self.method, observe)
        self.original, self.wrapper, self.installed = original, observe, True

    def subscribe(self, key: str, callback: Callable) -> Callable[[], None]:
        with self.lock:
            if not self.installed:
                self.install()
            existing = self.callbacks.get(key)
            token = existing[0] if existing is not None else object()
            if existing is None:
                self.callbacks[key] = (token, callback)

        def unsubscribe() -> None:
            with self.lock:
                current = self.callbacks.get(key)
                if current is None or current[0] is not token:
                    return
                del self.callbacks[key]
                if (
                    not self.callbacks
                    and getattr(self.queue, self.method) is self.wrapper
                ):
                    setattr(self.queue, self.method, self.original)
                    self.installed = False

        return unsubscribe


def _subscribe(
    queue: object, method: str, key: str, callback: Callable
) -> Callable[[], None]:
    with _registration_lock:
        states = getattr(queue, _STATE_ATTRIBUTE, None)
        if states is None:
            states = {}
            try:
                setattr(queue, _STATE_ATTRIBUTE, states)
            except (AttributeError, TypeError) as error:
                raise ValueError("Queue does not support observation") from error
        state = states.get(method)
        if state is None:
            state = _Observation(queue, method)
            states[method] = state
        return state.subscribe(key, callback)


def subscribe_dequeue(
    queue: object, key: str, callback: Callable[[object], None]
) -> Callable[[], None]:
    """Observe each actual get result once per key, after the host returns."""
    return _subscribe(queue, "get", key, callback)


def subscribe_task_done(
    queue: object, key: str, callback: Callable[[tuple, dict], None]
) -> Callable[[], None]:
    """Observe successful task_done calls with their original argument objects."""
    return _subscribe(queue, "task_done", key, callback)
