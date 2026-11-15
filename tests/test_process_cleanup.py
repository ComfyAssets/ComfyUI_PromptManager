"""remove_when_released waits for a dying process's files, within a bound (#636)."""

import os
import tempfile
import unittest
from unittest import mock

try:
    from tests import process_cleanup
except ImportError:  # discovered with tests/ as the top-level directory
    import process_cleanup


class TestRemoveWhenReleased(unittest.TestCase):
    def test_removes_the_folder(self):
        folder = tempfile.mkdtemp()
        with open(os.path.join(folder, "server.log"), "w") as handle:
            handle.write("x")
        process_cleanup.remove_when_released(folder)
        self.assertFalse(os.path.exists(folder))

    def test_a_missing_folder_is_already_removed(self):
        folder = tempfile.mkdtemp()
        os.rmdir(folder)
        process_cleanup.remove_when_released(folder)

    def test_retries_while_another_process_still_holds_a_file(self):
        held = PermissionError(32, "being used by another process")
        with (
            mock.patch.object(
                process_cleanup.shutil, "rmtree", side_effect=[held, held, None]
            ) as rmtree,
            mock.patch.object(process_cleanup.time, "sleep") as sleep,
        ):
            process_cleanup.remove_when_released("folder")
        self.assertEqual(rmtree.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_raises_the_last_error_after_the_bound(self):
        held = PermissionError(32, "being used by another process")
        clock = iter([0.0, 0.0, 5.0, 11.0])
        with (
            mock.patch.object(process_cleanup.shutil, "rmtree", side_effect=held),
            mock.patch.object(process_cleanup.time, "sleep"),
            mock.patch.object(
                process_cleanup.time, "monotonic", side_effect=lambda: next(clock)
            ),
        ):
            with self.assertRaises(PermissionError):
                process_cleanup.remove_when_released("folder", timeout=10)

    def test_other_errors_are_not_retried(self):
        with mock.patch.object(
            process_cleanup.shutil, "rmtree", side_effect=OSError("disk gone")
        ) as rmtree:
            with self.assertRaises(OSError):
                process_cleanup.remove_when_released("folder")
        self.assertEqual(rmtree.call_count, 1)


if __name__ == "__main__":
    unittest.main()
