"""The shared folder skip every media walker uses (#501)."""

import unittest

from utils.media_folders import in_skipped_folder, is_skipped_folder, is_trash_folder


class TrashFolderNameTests(unittest.TestCase):
    def test_trash_folder_names_in_any_case(self):
        for name in (
            ".Trash",
            ".trash",
            ".Trash-1000",
            ".trash-0",
            ".Trashes",
            "$RECYCLE.BIN",
            "$Recycle.Bin",
        ):
            with self.subTest(name=name):
                self.assertTrue(is_trash_folder(name))
                self.assertTrue(is_skipped_folder(name))

    def test_other_names_are_not_trash(self):
        for name in (".Trash-abc", ".Trash-", "trash", "Recycle", "RECYCLE.BIN", "x"):
            with self.subTest(name=name):
                self.assertFalse(is_trash_folder(name))

    def test_thumbnails_is_skipped_exactly(self):
        self.assertTrue(is_skipped_folder("thumbnails"))
        self.assertFalse(is_skipped_folder("Thumbnails"))
        self.assertFalse(is_trash_folder("thumbnails"))

    def test_folder_parts(self):
        self.assertTrue(in_skipped_folder(("a", ".Trash-1000", "files")))
        self.assertTrue(in_skipped_folder(("thumbnails",)))
        self.assertFalse(in_skipped_folder(("a", "b")))
        self.assertFalse(in_skipped_folder(()))


if __name__ == "__main__":
    unittest.main()
