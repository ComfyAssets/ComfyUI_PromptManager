"""Needs review change stream (#249 PR F2).

``GET /prompt_manager/review/events`` is a server-sent event stream for an
open Needs review page: ``event: ready`` when it opens, then
``event: change`` after each committed queue change (an item the scan or the
monitor recorded, a decision from any tab, a spelling refresh) and after a
restore. Notices that arrive while one is being written collapse into one.
The page re-reads its queue on each; nothing polls. The events carry no data
(``{}``), so nothing about the queue is pushed.

A ``: keepalive`` comment every ``HEARTBEAT_SECONDS`` lets the server notice
a closed tab (its write fails) and drop the subscription; clients ignore
comments. Shutdown (``close_review_events``, an ``on_shutdown`` hook) ends
every stream, and a stream asked for afterwards is a 503.
"""

import asyncio

from aiohttp import web

try:
    from ...database.execution_history import subscribe_history_restore
    from ...database.review_changes import subscribe_review_changes
except ImportError:
    from database.execution_history import subscribe_history_restore
    from database.review_changes import subscribe_review_changes

EVENTS_URL = "/prompt_manager/review/events"
HEARTBEAT_SECONDS = 25
_READY = b"event: ready\ndata: {}\n\n"
_CHANGE = b"event: change\ndata: {}\n\n"
_KEEPALIVE = b": keepalive\n\n"


class _Stream:
    """One open stream: a change flag set from any thread, and a close flag."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self.changed = asyncio.Event()
        self.closed = asyncio.Event()

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
        """How many change streams are open (tests and diagnostics)."""
        return len(self._review_streams())

    async def close_review_events(self, app) -> None:
        """End every open stream; later requests get 503 (shutdown hook)."""
        self._review_events_closed = True
        for stream in list(self._review_streams()):
            stream.closed.set()

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
            await response.prepare(request)
            await response.write(_READY)
            while True:
                outcome = await stream.next(HEARTBEAT_SECONDS)
                if outcome == "closed":
                    break
                await response.write(_CHANGE if outcome == "change" else _KEEPALIVE)
            await response.write_eof()
        except (ConnectionError, RuntimeError) as exc:  # the tab went away
            self.logger.debug(f"Needs review stream closed ({exc})")
        finally:
            streams.discard(stream)
            for unsubscribe in unsubscribers:
                unsubscribe()
        return response
