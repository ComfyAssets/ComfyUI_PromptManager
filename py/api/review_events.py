"""Needs review change stream (#249 PR F2).

``GET /prompt_manager/review/events`` is a server-sent event stream for an
open Needs review page: ``event: ready`` when it opens, then
``event: change`` after each committed queue change (an item the scan or the
monitor recorded, a decision from any tab, a spelling refresh) and after a
restore. The first notice is sent at once; notices in the next
``COALESCE_SECONDS`` collapse into one trailing ``change`` (M9: a busy scan
records file after file, and each event costs every open page a re-read).
The page re-reads its queue on each ``change``; nothing polls. ``change``
carries no data (``{}``). ``ready`` carries the queue's version
``{library_epoch, review_revision}``, read after the stream subscribed, the
same pair a list read answers: a page whose first read already answered that
version skips the re-read (F3, one read per visit), and any later change is a
``change`` event. Nothing else about the queue is pushed.

A ``: keepalive`` comment every ``HEARTBEAT_SECONDS`` lets the server notice
a closed tab (its write fails) and drop the subscription; clients ignore
comments. Shutdown (``close_review_events``, an ``on_shutdown`` hook) ends
every stream, and a stream asked for afterwards is a 503.
"""

import asyncio
import json

from aiohttp import web

try:
    from ...database.execution_history import subscribe_history_restore
    from ...database.review_changes import subscribe_review_changes
except ImportError:
    from database.execution_history import subscribe_history_restore
    from database.review_changes import subscribe_review_changes

EVENTS_URL = "/prompt_manager/review/events"
HEARTBEAT_SECONDS = 25
# After a ``change``, the next one waits this long, collapsing the notices in between.
COALESCE_SECONDS = 0.4
_CHANGE = b"event: change\ndata: {}\n\n"
_KEEPALIVE = b": keepalive\n\n"


def _ready(version: dict) -> bytes:
    data = json.dumps(version, separators=(",", ":"), sort_keys=True)
    return f"event: ready\ndata: {data}\n\n".encode("utf-8")


class _Stream:
    """One open stream: a change flag set from any thread, and a close flag."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self.changed = asyncio.Event()
        self.closed = asyncio.Event()
        self.ready = False

    def wake(self) -> None:
        """Thread-safe: mark a change (the loop may already be gone)."""
        try:
            self._loop.call_soon_threadsafe(self.changed.set)
        except RuntimeError:  # the loop closed
            pass

    async def next(self, timeout: float) -> str:
        """``change``, ``closed`` or ``idle`` (nothing within ``timeout``)."""
        waits = {
            asyncio.ensure_future(self.changed.wait()): "change",
            asyncio.ensure_future(self.closed.wait()): "closed",
        }
        try:
            done, _ = await asyncio.wait(
                waits, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for waiter in waits:
                waiter.cancel()
        if self.closed.is_set():
            return "closed"
        if done:
            self.changed.clear()
            return "change"
        return "idle"

    async def closed_within(self, timeout: float) -> bool:
        """Wait up to ``timeout``; true when the stream closed meanwhile."""
        try:
            await asyncio.wait_for(self.closed.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True


class ReviewEventsMixin:
    """The Needs review change stream."""

    def _register_review_event_routes(self, routes: web.RouteTableDef) -> None:
        routes.get(EVENTS_URL)(self.review_events)

    def _review_streams(self) -> set:
        streams = getattr(self, "_open_review_streams", None)
        if streams is None:
            streams = self._open_review_streams = set()
        return streams

    def review_stream_count(self) -> int:
        """How many change streams have sent ``ready`` and are open (tests and
        diagnostics): a queue change after this count moved is a ``change``
        event on them, never part of the version ``ready`` named."""
        return sum(1 for stream in self._review_streams() if stream.ready)

    async def close_review_events(self, app) -> None:
        """End every open stream; later requests get 503 (shutdown hook)."""
        self._review_events_closed = True
        for stream in list(self._review_streams()):
            stream.closed.set()

    async def _queue_version(self) -> dict:
        """The queue version for ``ready``, read after subscribing (so a change
        after this read is an event); ``{}`` when it cannot be read, and the
        page then re-reads."""
        try:
            return await self._run_in_executor(self.db.review_queue_version)
        except Exception:
            self.logger.warning("Needs review version read failed", exc_info=True)
            return {}

    async def review_events(self, request: web.Request) -> web.StreamResponse:
        if getattr(self, "_review_events_closed", False):
            return web.json_response(
                {"success": False, "error": "The server is shutting down."},
                status=503,
            )
        stream = _Stream(asyncio.get_running_loop())
        db_path = self.db.model.db_path
        unsubscribers = (
            subscribe_review_changes(db_path, stream.wake),
            subscribe_history_restore(db_path, stream.wake),
        )
        streams = self._review_streams()
        streams.add(stream)
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )
        try:
            version = await self._queue_version()
            await response.prepare(request)
            await response.write(_ready(version))
            stream.ready = True
            while True:
                outcome = await stream.next(HEARTBEAT_SECONDS)
                if outcome == "closed":
                    break
                if outcome == "idle":
                    await response.write(_KEEPALIVE)
                    continue
                await response.write(_CHANGE)
                if await stream.closed_within(COALESCE_SECONDS):
                    break
            await response.write_eof()
        except (ConnectionError, RuntimeError) as exc:  # the tab went away
            self.logger.debug(f"Needs review stream closed ({exc})")
        finally:
            streams.discard(stream)
            for unsubscribe in unsubscribers:
                unsubscribe()
        return response
