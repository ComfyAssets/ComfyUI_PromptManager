"""One process-wide, non-blocking gate around the tagging models (#249 B2).

Loading, swapping, running, downloading and unloading a tagging model all
happen under this gate, whether the caller is a legacy Auto-tag route, the
Tag dialog's Generate or (later) a bulk job. A caller that cannot take the
gate is refused at once (``409 model-busy``), never queued.

Work that runs in a worker thread is admitted with :meth:`ModelGate.run`. A
release requested while such work still runs (a client disconnect cancels
the waiting coroutine, not the thread) takes effect only when the last
admitted call really exits, so nobody can swap the model under a running
inference.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from typing import Any, Callable, Optional


class GateNotHeld(RuntimeError):
    """Raised when work is admitted for an owner that does not hold the gate."""


class ModelGate:
    """A single-owner gate whose release waits for admitted worker calls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holder: Optional[str] = None
        self._inflight = 0
        self._releasing = False

    def try_acquire(self, owner: str) -> bool:
        """Take the gate for *owner*; False at once when anyone holds it."""
        with self._lock:
            if self._holder is not None:
                return False
            self._holder = owner
            self._inflight = 0
            self._releasing = False
            return True

    def release(self, owner: str) -> None:
        """Let go; deferred until admitted work of *owner* has exited."""
        with self._lock:
            if self._holder != owner:
                return
            if self._inflight:
                self._releasing = True
            else:
                self._clear_locked()

    def holder(self) -> Optional[str]:
        with self._lock:
            return self._holder

    def begin(self, owner: str) -> None:
        """Admit one worker call; the holder must still hold the gate."""
        with self._lock:
            if self._holder != owner or self._releasing:
                raise GateNotHeld(f"{owner} does not hold the model gate")
            self._inflight += 1

    def end(self, owner: str) -> None:
        """One admitted call exited; completes a deferred release."""
        with self._lock:
            self._end_locked(owner)

    async def run(
        self,
        owner: str,
        func: Callable[..., Any],
        *args,
        on_skipped: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        """Run ``func`` in the default executor while *owner* holds the gate.

        The executor future is shielded: cancelling the caller never cancels
        a call that has not started yet (which would leave it admitted
        forever) and never ends the admission of a call that is running.
        ``on_skipped`` runs once when ``func`` never starts (the gate is not
        held, the executor refuses the call, or a loop shutdown drops it), so
        a caller can free what ``func`` would have freed.
        """
        try:
            self.begin(owner)
        except GateNotHeld:
            if on_skipped is not None:
                on_skipped()
            raise
        state = {"started": False, "finished": False}

        def finish() -> None:
            with self._lock:
                if not state["finished"]:
                    state["finished"] = True
                    self._end_locked(owner)

        def work():
            with self._lock:
                if state["finished"]:
                    # Already declared skipped (the future was cancelled as
                    # the thread picked the call up): never run it twice over.
                    return None
                state["started"] = True
            try:
                return func(*args, **kwargs)
            finally:
                finish()

        def never_ran(future) -> None:
            # Only a loop shutdown cancels the shielded future; a call that
            # never started must still give its admission back.
            if future.cancelled():
                with self._lock:
                    skipped = not state["started"] and not state["finished"]
                    if skipped:
                        state["finished"] = True
                        self._end_locked(owner)
                if skipped and on_skipped is not None:
                    on_skipped()

        try:
            future = asyncio.get_running_loop().run_in_executor(None, work)
        except BaseException:
            finish()
            if on_skipped is not None:
                on_skipped()
            raise
        future.add_done_callback(never_ran)
        return await asyncio.shield(future)

    def _end_locked(self, owner: str) -> None:
        if self._holder != owner or self._inflight == 0:
            return
        self._inflight -= 1
        if self._releasing and self._inflight == 0:
            self._clear_locked()

    def _clear_locked(self) -> None:
        self._holder = None
        self._inflight = 0
        self._releasing = False


_GATE = ModelGate()


def new_owner(kind: str) -> str:
    """A unique owner name such as ``legacy-single:<uuid>``."""
    return f"{kind}:{uuid.uuid4()}"


def try_acquire(owner: str) -> bool:
    return _GATE.try_acquire(owner)


def release(owner: str) -> None:
    _GATE.release(owner)


def holder() -> Optional[str]:
    return _GATE.holder()


async def run(
    owner: str,
    func: Callable[..., Any],
    *args,
    on_skipped: Optional[Callable[[], None]] = None,
    **kwargs,
):
    return await _GATE.run(owner, func, *args, on_skipped=on_skipped, **kwargs)
