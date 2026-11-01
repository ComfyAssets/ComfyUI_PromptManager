"""``release_free_memory``: glibc's malloc_trim(0), a no-op elsewhere (#523)."""

import sys
import unittest
import unittest.mock as mock

from utils import native_memory


class ReleaseFreeMemoryTests(unittest.TestCase):
    def setUp(self):
        # Each test looks the symbol up again.
        patcher = mock.patch.object(
            native_memory, "_trim", native_memory._NOT_LOOKED_UP
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @unittest.skipUnless(native_memory._glibc(), "glibc only")
    def test_glibc_trims_once_per_call(self):
        self.assertTrue(native_memory.release_free_memory())
        self.assertTrue(native_memory.release_free_memory())

    def test_other_platforms_do_nothing(self):
        for platform in ("win32", "darwin", "cygwin"):
            with self.subTest(platform), mock.patch.object(sys, "platform", platform):
                native_memory._trim = native_memory._NOT_LOOKED_UP
                with mock.patch.object(native_memory.ctypes, "CDLL") as cdll:
                    self.assertFalse(native_memory.release_free_memory())
                cdll.assert_not_called()

    def test_musl_does_nothing(self):
        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch.object(
                native_memory.os, "confstr", side_effect=ValueError("unknown name")
            ),
            mock.patch.object(native_memory.ctypes, "CDLL") as cdll,
        ):
            self.assertFalse(native_memory.release_free_memory())
        cdll.assert_not_called()

    def test_a_missing_symbol_is_false(self):
        library = mock.Mock(spec=[])  # no malloc_trim attribute
        with (
            mock.patch.object(native_memory, "_glibc", return_value=True),
            mock.patch.object(native_memory.ctypes, "CDLL", return_value=library),
        ):
            self.assertFalse(native_memory.release_free_memory())

    def test_an_unloadable_libc_is_false(self):
        with (
            mock.patch.object(native_memory, "_glibc", return_value=True),
            mock.patch.object(native_memory.ctypes, "CDLL", side_effect=OSError),
        ):
            self.assertFalse(native_memory.release_free_memory())

    def test_a_failing_trim_never_raises(self):
        trim = mock.Mock(side_effect=OSError("boom"))
        library = mock.Mock(malloc_trim=trim)
        with (
            mock.patch.object(native_memory, "_glibc", return_value=True),
            mock.patch.object(native_memory.ctypes, "CDLL", return_value=library),
        ):
            self.assertFalse(native_memory.release_free_memory())
        trim.assert_called_once_with(0)

    def test_the_symbol_is_looked_up_once(self):
        trim = mock.Mock(return_value=1)
        library = mock.Mock(malloc_trim=trim)
        with (
            mock.patch.object(native_memory, "_glibc", return_value=True),
            mock.patch.object(
                native_memory.ctypes, "CDLL", return_value=library
            ) as cdll,
        ):
            self.assertTrue(native_memory.release_free_memory())
            self.assertTrue(native_memory.release_free_memory())
        cdll.assert_called_once_with(None)
        self.assertEqual(trim.call_count, 2)


if __name__ == "__main__":
    unittest.main()
