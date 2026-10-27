"""Browser-test media folders are emptied by a rename, deleted later (#384)."""

import tempfile
import threading
import unittest
from pathlib import Path

from tests.browser.folder_trash import TRASH_NAME, close_trash, replace_folder


class TestFolderTrash(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.addCleanup(close_trash, self.root)

    def trash_threads(self):
        return [t for t in threading.enumerate() if t.name == "pm-reset-trash"]

    def test_replaces_the_folder_with_an_empty_one(self):
        folder = self.root / "output"
        (folder / "nested").mkdir(parents=True)
        (folder / "nested" / "image.png").write_bytes(b"png")
        replace_folder(folder)
        self.assertTrue(folder.is_dir())
        self.assertEqual(list(folder.iterdir()), [])
        close_trash(self.root)
        self.assertFalse((self.root / TRASH_NAME).exists())
        self.assertEqual(self.trash_threads(), [])

    def test_a_missing_folder_is_created_without_a_trash(self):
        replace_folder(self.root / "input")
        self.assertTrue((self.root / "input").is_dir())
        self.assertFalse((self.root / TRASH_NAME).exists())
        self.assertEqual(self.trash_threads(), [])

    def test_repeated_replacements_share_one_thread_and_all_are_removed(self):
        folder = self.root / "output"
        for number in range(5):
            folder.mkdir(exist_ok=True)
            (folder / f"{number}.png").write_bytes(b"png")
            replace_folder(folder)
        self.assertEqual(len(self.trash_threads()), 1)
        close_trash(self.root)
        self.assertEqual(list(folder.iterdir()), [])
        self.assertFalse((self.root / TRASH_NAME).exists())
        close_trash(self.root)  # idempotent


class TestFolderTrashAfterClose(unittest.TestCase):
    def test_a_replacement_after_close_deletes_in_place_without_a_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            close_trash(root)
            folder = root / "output"
            folder.mkdir()
            (folder / "late.png").write_bytes(b"png")
            replace_folder(folder)
            self.assertEqual(list(folder.iterdir()), [])
            self.assertFalse((root / TRASH_NAME).exists())
            names = [t.name for t in threading.enumerate()]
            self.assertNotIn("pm-reset-trash", names)


if __name__ == "__main__":
    unittest.main()
