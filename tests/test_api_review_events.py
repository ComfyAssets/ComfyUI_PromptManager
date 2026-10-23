"""Needs review change notices (#249 PR F2).

``GET /prompt_manager/review/events`` is a server-sent event stream: one
``ready`` event when it opens, then one ``change`` event after every
committed queue change (an item recorded by the scan or the monitor, a
spelling refresh, a decision from any tab) and after a restore. An open
Needs review page re-reads its queue on ``change``; nothing polls. A refused
decision and a duplicate recording change nothing and send nothing.
"""

import asyncio
import json
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
        return (await self._event_with_data(resp))[0]

    async def _event_with_data(self, resp):
        """The next event's name and its parsed ``data`` line."""
        name, data = None, None
        while True:
            line = await asyncio.wait_for(resp.content.readline(), READ_SECONDS)
            self.assertTrue(line, "the stream ended")
            text = line.decode("utf-8").rstrip("\n")
            if text.startswith("event: "):
                name = text[len("event: ") :]
            elif text.startswith("data: "):
                data = json.loads(text[len("data: ") :])
            elif text == "" and name is not None:
                return name, data

    async def _ready_version(self):
        resp = await self.client.get(EVENTS_URL)
        self.assertEqual(resp.status, 200)
        self.addAsyncCleanup(self._close, resp)
        name, data = await self._event_with_data(resp)
        self.assertEqual(name, "ready")
        return data

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

    async def test_ready_carries_the_version_a_list_read_answers(self):
        # F3 (no double read per visit): the page skips the re-read on
        # ``ready`` when its first read already answered this version.
        self._require_events()
        self._record(self._ambiguous())
        page = await self._list()
        self.assertIsInstance(page.get("review_revision"), int, page)
        version = await self._ready_version()
        self.assertEqual(
            version,
            {
                "library_epoch": page["library_epoch"],
                "review_revision": page["review_revision"],
            },
        )

    async def test_a_queue_change_moves_the_version_ready_carries(self):
        self._require_events()
        before = await self._list()
        self._record(self._ambiguous())
        after = await self._list()
        self.assertIn("review_revision", after)
        self.assertGreater(after["review_revision"], before["review_revision"])
        version = await self._ready_version()
        self.assertEqual(version["review_revision"], after["review_revision"])
        self.assertNotEqual(version["review_revision"], before["review_revision"])

    async def test_ready_names_no_version_when_it_cannot_be_read(self):
        # The page then re-reads, as it did before versions existed.
        self._require_events()

        def broken():
            raise sqlite3.DatabaseError("locked")

        self.api.db.review_queue_version = broken
        self.assertEqual(await self._ready_version(), {})

    async def test_a_stream_counts_as_open_once_it_has_sent_ready(self):
        # The browser fixture waits on this count before it changes the
        # queue, so a change after it is a ``change`` event and never part of
        # the version ``ready`` named.
        self._require_events()
        reading, release = asyncio.Event(), asyncio.Event()
        original = self.api._queue_version

        async def held():
            reading.set()
            await release.wait()
            return await original()

        self.api._queue_version = held
        opening = asyncio.ensure_future(self._ready_version())
        await asyncio.wait_for(reading.wait(), READ_SECONDS)
        self.assertEqual(self.api.review_stream_count(), 0)
        release.set()
        await asyncio.wait_for(opening, READ_SECONDS)
        self.assertEqual(self.api.review_stream_count(), 1)

    async def test_change_events_carry_no_data(self):
        self._require_events()
        stream = await self._stream()
        self._record(self._ambiguous())
        self.assertEqual(await self._event_with_data(stream), ("change", {}))

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
