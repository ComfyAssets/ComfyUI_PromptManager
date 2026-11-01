"""The thumbnail folder check answers for the folder as it is now (#320).

A check that started before the caller asked can miss what just happened: #592
hit it in the merge queue, where a folder deleted right after
``configure`` woke the probe stayed "available" until the next probe.
"""

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from py.api import preview_location, preview_store  # noqa: E402

WAIT = 5
HERE = (True, None, {"id": "x"})
GONE = (False, "missing", None)
KEY = ("/thumbs", "x")


class TestProbeCheck(unittest.TestCase):
    def setUp(self):
        self.probe = preview_store._Probe()

    def test_a_finished_check_nobody_collected_is_not_the_answer(self):
        with patch.object(preview_location, "folder_state", return_value=HERE):
            with self.probe._lock:
                _key, done, _result = self.probe._start_check(*KEY)
            self.assertTrue(done.wait(WAIT))
        with patch.object(preview_location, "folder_state", return_value=GONE):
            self.assertEqual(self.probe.check(*KEY), GONE)

    def test_a_check_already_running_is_followed_by_a_new_one(self):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def state(_path, _expected_id):
            calls.append(threading.current_thread().name)
            if len(calls) == 1:
                started.set()
                self.assertTrue(release.wait(WAIT))
                return HERE
            return GONE

        answers = []
        with patch.object(preview_location, "folder_state", state):
            with self.probe._lock:
                self.probe._start_check(*KEY)
            self.assertTrue(started.wait(WAIT))
            caller = threading.Thread(
                target=lambda: answers.append(self.probe.check(*KEY))
            )
            caller.start()
            release.set()
            caller.join(WAIT)
        self.assertFalse(caller.is_alive())
        self.assertEqual(answers, [GONE])
        self.assertEqual(len(calls), 2)  # one after the other, never two at once

    def test_a_stuck_check_is_never_doubled(self):
        release = threading.Event()
        self.addCleanup(release.set)
        calls = []

        def state(_path, _expected_id):
            calls.append(1)
            release.wait(WAIT)
            return HERE

        with (
            patch.object(preview_location, "folder_state", state),
            patch.object(preview_store, "IO_BUDGET", 0.05),
        ):
            with self.probe._lock:
                self.probe._start_check(*KEY)
            with self.assertRaises(TimeoutError):
                self.probe.check(*KEY)
        self.assertEqual(len(calls), 1)

    def test_an_error_from_the_new_check_reaches_the_caller(self):
        with patch.object(
            preview_location, "folder_state", side_effect=OSError("share gone")
        ):
            with self.assertRaises(OSError):
                self.probe.check(*KEY)


class TestRefreshOrder(unittest.TestCase):
    """Only the newest check's answer is published (#611)."""

    def test_an_older_check_never_overwrites_a_newer_answer(self):
        parsed = preview_location.ParsedLocation(
            mode=preview_store.FOLDER_MODE, path="/thumbs", id="x"
        )
        self.addCleanup(setattr, preview_store, "_available", preview_store._available)
        self.addCleanup(setattr, preview_store, "_problem", preview_store._problem)
        started = threading.Event()
        release = threading.Event()
        newer_published = threading.Event()
        calls = []
        publish = preview_store._set_state

        def state(_path, _expected_id):
            calls.append(1)
            if len(calls) == 1:
                started.set()
                self.assertTrue(release.wait(WAIT))
                return HERE
            return GONE

        def set_state(*args, **kwargs):
            if args[1]:  # the older check's "available" arrives last
                self.assertTrue(newer_published.wait(WAIT))
            publish(*args, **kwargs)

        def newer():
            preview_store.refresh()
            newer_published.set()

        with (
            patch.object(preview_store, "_parsed", parsed),
            patch.object(preview_location, "folder_state", state),
            patch.object(preview_store, "_set_state", set_state),
        ):
            first = threading.Thread(target=preview_store.refresh)
            first.start()
            self.assertTrue(started.wait(WAIT))
            second = threading.Thread(target=newer)
            second.start()
            release.set()
            second.join(WAIT)
            first.join(WAIT)
            self.assertFalse(first.is_alive() or second.is_alive())
            self.assertFalse(preview_store._available)
            self.assertEqual(preview_store._problem, "missing")


if __name__ == "__main__":
    unittest.main()
