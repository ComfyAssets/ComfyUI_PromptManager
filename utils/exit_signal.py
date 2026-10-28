"""The process exit signal: running work stops at its next safe point (#376).

ComfyUI catches Ctrl+C in ``main.py`` and returns without closing its event
loop. The interpreter then runs ``threading._shutdown``, which first calls the
``threading._register_atexit`` callbacks, newest first. One of them,
``concurrent.futures``'s ``_python_exit``, joins every executor thread, and
only after that do the ``atexit`` callbacks run (the package teardown). A call
still running on the default executor (a scan batch, a model download, a
thumbnail batch) used to keep the process alive until it returned.

``install()`` registers a callback in that same list after ``concurrent.futures``
did, so it runs before the join. It sets the signal that ``requested()`` reads
(``Job.cancelled`` and ``map_parallel`` read it) and runs ``on_exit``.

``threading._register_atexit`` is CPython-internal. It has the same contract
on 3.12, 3.13 and 3.14. Without it, or when it refuses the registration, exit
behaves as before: the call in flight runs to its end.
"""

import logging
import threading

logger = logging.getLogger("prompt_manager.exit")

_requested = threading.Event()
_install_lock = threading.Lock()
_installed = False
_on_exit = []


class ExitRequested(Exception):
    """The interpreter is exiting: work not yet started is not started."""


def requested():
    """True once the interpreter has started exiting."""
    return _requested.is_set()


def request():
    """Set the signal (the exit hook; tests)."""
    _requested.set()


def checkpoint():
    """Raise ExitRequested once the interpreter is exiting."""
    if _requested.is_set():
        raise ExitRequested("ComfyUI is exiting")


def checked(func):
    """``func`` behind a checkpoint: a call that starts after the signal
    raises ExitRequested instead of running."""

    def run(*args, **kwargs):
        checkpoint()
        return func(*args, **kwargs)

    return run


def _hook():
    """The threading atexit callback. It never raises: an exception here
    would end ``threading._shutdown`` before it shuts the executors."""
    _requested.set()
    for action in tuple(_on_exit):
        try:
            action()
        except (Exception, KeyboardInterrupt):
            logger.error("Stopping work at exit failed", exc_info=True)


def install(on_exit=None):
    """Register the exit hook once per process; True when it is registered.

    ``on_exit`` (optional) runs inside the hook, after the signal is set.
    False (logged at DEBUG) when this Python has no ``_register_atexit`` or
    refuses it; exit then waits for running work as before.
    """
    global _installed
    with _install_lock:
        if on_exit is not None and on_exit not in _on_exit:
            _on_exit.append(on_exit)
        if _installed:
            return True
        # Imported first so its pool join is registered before our hook:
        # the callbacks run newest first, so ours runs before that join.
        import concurrent.futures.thread  # noqa: F401

        register = getattr(threading, "_register_atexit", None)
        if not callable(register):
            logger.debug("threading._register_atexit is missing; exit waits for jobs")
            return False
        try:
            register(_hook)
        except RuntimeError as error:  # already shutting down
            logger.debug(f"Exit hook not registered: {error}")
            return False
        _installed = True
        return True


def reset():
    """Clear the signal and forget the registration (tests only; a hook
    already registered stays in ``threading`` and sets the signal at exit)."""
    global _installed
    with _install_lock:
        _requested.clear()
        _installed = False
        _on_exit.clear()
