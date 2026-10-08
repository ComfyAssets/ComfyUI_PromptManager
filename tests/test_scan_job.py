"""Unit tests for py/api/scan_job.py: fan-out of scan progress to SSE subscribers."""

import asyncio
import contextlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py.api.scan_job import ScanJob  # noqa: E402


async def _collect(job, stop_after=None):
    seen = []
    async with contextlib.aclosing(job.events()) as events:
        async for payload in events:
            seen.append(payload)
            if stop_after is not None and len(seen) >= stop_after:
                break
    return seen


class TestScanJob(unittest.IsolatedAsyncioTestCase):

    async def test_new_job_is_running_with_no_event(self):
        job = ScanJob()
        self.assertTrue(job.running)
        self.assertIsNone(job.last_event)

    async def test_subscriber_receives_events_until_finish(self):
        job = ScanJob()
        consumer = asyncio.ensure_future(_collect(job))
        await asyncio.sleep(0)

        job.publish({"type": "progress", "progress": 10})
        job.publish({"type": "complete"})
        job.finish()

        self.assertEqual(
            await asyncio.wait_for(consumer, 2),
            [{"type": "progress", "progress": 10}, {"type": "complete"}],
        )
        self.assertFalse(job.running)

    async def test_late_subscriber_gets_latest_snapshot_then_live_events(self):
        job = ScanJob()
        job.publish({"type": "progress", "progress": 10})
        job.publish({"type": "progress", "progress": 40})

        consumer = asyncio.ensure_future(_collect(job))
        await asyncio.sleep(0)
        job.publish({"type": "complete"})
        job.finish()

        self.assertEqual(
            await asyncio.wait_for(consumer, 2),
            [{"type": "progress", "progress": 40}, {"type": "complete"}],
        )

    async def test_subscriber_after_finish_gets_only_the_final_event(self):
        job = ScanJob()
        job.publish({"type": "progress", "progress": 99})
        job.publish({"type": "complete"})
        job.finish()

        self.assertEqual(await _collect(job), [{"type": "complete"}])

    async def test_two_subscribers_both_see_every_event(self):
        job = ScanJob()
        first = asyncio.ensure_future(_collect(job))
        second = asyncio.ensure_future(_collect(job))
        await asyncio.sleep(0)

        job.publish({"type": "progress", "progress": 1})
        job.finish()

        self.assertEqual(await first, [{"type": "progress", "progress": 1}])
        self.assertEqual(await second, [{"type": "progress", "progress": 1}])

    async def test_departed_subscriber_is_forgotten(self):
        job = ScanJob()
        job.publish({"type": "progress", "progress": 1})
        await _collect(job, stop_after=1)

        self.assertEqual(job.subscriber_count, 0)
        job.publish({"type": "complete"})  # must not raise or queue anywhere
        job.finish()


if __name__ == "__main__":
    unittest.main()
