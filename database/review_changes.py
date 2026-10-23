"""Needs review queue change notices (#249 PR F2).

Every committed change to the pending queue (an item recorded by the scan or
the monitor, a spelling refresh, a decision) calls ``notify_review_change``
after its transaction, from whichever thread wrote it. The review event
stream (``py/api/review_events.py``) subscribes, so an open Needs review page
re-reads its queue when it changes instead of polling. Observers are keyed by
the database file, like ``subscribe_history_restore``; an observer that fails
is logged and never fails the write.
"""

import os
import threading
from typing import Callable

try:
    from ..utils.logging_config import get_logger
except ImportError:
    from utils.logging_config import get_logger

_logger = get_logger("prompt_manager.database.review_changes")
_lock = threading.Lock()
_observers: dict[str, dict[object, Callable[[], None]]] = {}


def _identity(db_path: str) -> str:
    return os.path.normcase(os.path.realpath(db_path))


def subscribe_review_changes(
    db_path: str, callback: Callable[[], None]
) -> Callable[[], None]:
    """Call ``callback()`` after each committed queue change; return the unsubscribe."""
    identity = _identity(db_path)
    token = object()
    with _lock:
        _observers.setdefault(identity, {})[token] = callback

    def unsubscribe() -> None:
        with _lock:
            observers = _observers.get(identity)
            if observers is not None:
                observers.pop(token, None)
                if not observers:
                    _observers.pop(identity, None)

    return unsubscribe


def observer_count(db_path: str) -> int:
    """How many observers watch ``db_path`` (tests and diagnostics)."""
    with _lock:
        return len(_observers.get(_identity(db_path), {}))


def notify_review_change(db_path: str) -> None:
    """Tell every observer of ``db_path``; call it after the commit."""
    with _lock:
        callbacks = tuple(_observers.get(_identity(db_path), {}).values())
    for callback in callbacks:
        try:
            callback()
        except Exception:
            _logger.warning("Needs review change observer failed", exc_info=True)
