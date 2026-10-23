"""Needs review change notices (#249 PR F2).

``GET /prompt_manager/review/events`` is a server-sent event stream: one
``ready`` event when it opens, then one ``change`` event after every
committed queue change (an item recorded by the scan or the monitor, a
spelling refresh, a decision from any tab) and after a restore. An open
Needs review page re-reads its queue on ``change``; nothing polls. A refused
decision and a duplicate recording change nothing and send nothing.
"""

import asyncio
import sqlite3
import threading

try:
    from tests.test_api_review import ReviewRoutesTestCase
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_review import ReviewRoutesTestCase

try:  # guarded so a missing module fails assertions, not test loading
    from database import review_changes
except ImportError:
    review_changes = None
try:
    from py.api import review_events
except ImportError:
    review_events = None

EVENTS_URL = "/prompt_manager/review/events"
# A safety bound for one read; every wait below ends on an event first.
READ_SECONDS = 10


class ReviewEventsTestCase(ReviewRoutesTestCase):
    def _require_events(self):
        self.assertIsNotNone(review_changes, "database.review_changes is missing")
        self.assertIsNotNone(review_events, "py.api.review_events is missing")

    async def _stream(self):
        resp = await self.client.get(EVENTS_URL)
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "text/event-stream")
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.addAsyncCleanup(self._close, resp)
        self.assertEqual(await self._event(resp), "ready")
        return resp

    @staticmethod
    async def _close(resp):
        resp.close()

    async def _event(self, resp):
        """The next event's name (keepalive comments are skipped)."""
        name = None
        while True:
            line = await asyncio.wait_for(resp.content.readline(), READ_SECONDS)
            self.assertTrue(line, "the stream ended")
            text = line.decode("utf-8").rstrip("\n")
            if text.startswith("event: "):
                name = text[len("event: ") :]
            elif text == "" and name is not None:
                return name

    async def _record_in_thread(self, path):
        """Record as the monitor does: from a worker thread, not the loop."""
        self._roots()
        outcomes = []
        thread = threading.Thread(
            target=lambda: outcomes.append(self.api.record_review_file(str(path)))
        )
        thread.start()
        await asyncio.get_running_loop().run_in_executor(None, thread.join)
        return outcomes[0]


class TestReviewEvents(ReviewEventsTestCase):
    async def test_monitor_recording_sends_one_change(self):
        self._require_events()
        stream = await self._stream()
        self.assertEqual(await self._record_in_thread(self._ambiguous()), "recorded")
        self.assertEqual(await self._event(stream), "change")

    async def test_duplicate_recording_and_refused_decision_send_nothing(self):
        self._require_events()
        path = self._ambiguous("two.png")
        self._record(path)
        stream = await self._stream()
        # The same bytes again: nothing changes.
        self.assertEqual(await self._record_in_thread(path), "duplicate")
        item, detail, observed = await self._open()
        # A stale revision: refused with zero writes.
        body = {**self._body(detail, observed, "unresolved"), "expected_revision": 9}
        status, data = await self._decide(item["id"], body)
        self.assertEqual(status, 409, data)
        # The next notice is the next real change, so nothing came before it.
        self._record(self._ambiguous("other.png", color=(0, 0, 9)))
        self.assertEqual(await self._event(stream), "change")
        self.assertEqual(len(self._items()), 2)

    async def test_decision_from_any_tab_sends_a_change(self):
        self._require_events()
        self._record(self._ambiguous())
        stream = await self._stream()
        item, detail, observed = await self._open()
        status, data = await self._decide(
            item["id"], self._body(detail, observed, "unresolved")
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(await self._event(stream), "change")

    async def test_restore_sends_a_change(self):
        self._require_events()
        stream = await self._stream()
        from database.execution_history import notify_history_restore

        notify_history_restore(self.api.db.model.db_path)
        self.assertEqual(await self._event(stream), "change")

    async def test_scan_recording_sends_a_change(self):
        self._require_events()
        self._roots()
        self._ambiguous("sub/two.png")
        stream = await self._stream()
        job = await self._scan("all")
        self.assertEqual(job["scope"]["review_items"], 1)
        self.assertEqual(await self._event(stream), "change")

    async def test_closing_the_stream_unsubscribes(self):
        self._require_events()
        path = self.api.db.model.db_path
        stream = await self._stream()
        self.assertEqual(review_changes.observer_count(path), 1)
        self.assertEqual(self.api.review_stream_count(), 1)
        stream.close()
        # The server notices the closed socket at its next write: a change.
        self._record(self._ambiguous())
        for _ in range(100):
            if not self.api.review_stream_count():
                break
            await asyncio.sleep(0.02)
        self.assertEqual(self.api.review_stream_count(), 0)
        self.assertEqual(review_changes.observer_count(path), 0)

    async def test_shutdown_ends_every_stream(self):
        self._require_events()
        stream = await self._stream()
        await self.api.close_review_events(None)
        line = await asyncio.wait_for(stream.content.read(), READ_SECONDS)
        self.assertEqual(line, b"")
        self.assertEqual(self.api.review_stream_count(), 0)
        # A stream opened after shutdown ends at once.
        resp = await self.client.get(EVENTS_URL)
        self.assertEqual(resp.status, 503)
        resp.close()

    async def test_keepalive_comment_is_skipped_by_clients(self):
        self._require_events()
        with self.patch_heartbeat(0.05):
            stream = await self._stream()
            line = await asyncio.wait_for(stream.content.readline(), READ_SECONDS)
            self.assertEqual(line, b": keepalive\n")

    def patch_heartbeat(self, seconds):
        from unittest.mock import patch

        return patch.object(review_events, "HEARTBEAT_SECONDS", seconds)


class TestReviewChangeRegistry(ReviewEventsTestCase):
    def test_a_failing_observer_never_fails_a_write(self):
        self._require_events()
        path = self.api.db.model.db_path
        seen = []

        def broken():
            raise RuntimeError("observer failed")

        unsubscribe_broken = review_changes.subscribe_review_changes(path, broken)
        unsubscribe = review_changes.subscribe_review_changes(
            path, lambda: seen.append(1)
        )
        try:
            with self.assertLogs(
                "prompt_manager.database.review_changes", level="WARNING"
            ):
                self._record(self._ambiguous())
            self.assertEqual(seen, [1])
        finally:
            unsubscribe_broken()
            unsubscribe()
        self.assertEqual(review_changes.observer_count(path), 0)

    def test_notice_comes_after_the_commit(self):
        self._require_events()
        path = self.api.db.model.db_path
        seen = []

        def committed():
            # A separate connection sees only committed rows.
            other = sqlite3.connect(path)
            try:
                seen.append(
                    other.execute("SELECT COUNT(*) FROM media_review_items").fetchone()[
                        0
                    ]
                )
            finally:
                other.close()

        unsubscribe = review_changes.subscribe_review_changes(path, committed)
        try:
            self._record(self._ambiguous())
        finally:
            unsubscribe()
        self.assertEqual(seen, [1])
