"""#516: the browser test server pins glibc's mmap threshold.

Large frees then go back to the OS instead of fragmenting per-thread arenas.
"""

import ctypes
import sys
import unittest
from unittest import mock

from tests.browser import malloc_tuning


class FakeLibc:
    def __init__(self, result=1):
        self.calls = []
        self.result = result

    def mallopt(self, option, value):
        self.calls.append((option, value))
        return self.result


class PinMmapThresholdTests(unittest.TestCase):
    def test_sets_a_fixed_threshold_through_mallopt(self):
        libc = FakeLibc()
        with mock.patch.object(sys, "platform", "linux"):
            self.assertTrue(malloc_tuning.pin_mmap_threshold(libc))
        # M_MMAP_THRESHOLD is -3; setting it also turns off glibc's dynamic
        # raising of the threshold.
        self.assertEqual(libc.calls, [(-3, 128 * 1024)])

    def test_reports_a_refused_setting(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertFalse(malloc_tuning.pin_mmap_threshold(FakeLibc(result=0)))

    def test_skips_a_libc_without_mallopt(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertFalse(malloc_tuning.pin_mmap_threshold(object()))

    def test_skips_other_platforms_without_loading_a_libc(self):
        for name in ("win32", "darwin"):
            with (
                self.subTest(name),
                mock.patch.object(sys, "platform", name),
                mock.patch.object(
                    ctypes, "CDLL", side_effect=AssertionError("no libc load")
                ),
            ):
                self.assertFalse(malloc_tuning.pin_mmap_threshold())

    def test_skips_when_the_process_libc_cannot_be_loaded(self):
        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch.object(ctypes, "CDLL", side_effect=OSError("no libc")),
        ):
            self.assertFalse(malloc_tuning.pin_mmap_threshold())

    @unittest.skipUnless(
        sys.platform.startswith("linux")
        and hasattr(ctypes.CDLL(None), "gnu_get_libc_version"),
        "glibc only",
    )
    def test_glibc_accepts_the_setting(self):
        # This pins the threshold for the rest of this test process too; the
        # test server does the same at start, so nothing here depends on it.
        self.assertTrue(malloc_tuning.pin_mmap_threshold())


class ServerMainTests(unittest.TestCase):
    def test_main_pins_the_threshold_before_it_starts_serving(self):
        from tests.browser import server

        order = []

        def bind(port):
            order.append("bind")
            raise RuntimeError("stop before serving")

        with (
            mock.patch.object(
                server, "pin_mmap_threshold", side_effect=lambda: order.append("pin")
            ),
            mock.patch.object(server, "bind_listener", side_effect=bind),
        ):
            with self.assertRaises(RuntimeError):
                server.main(["--port", "0"])
        self.assertEqual(order, ["pin", "bind"])


if __name__ == "__main__":
    unittest.main()
