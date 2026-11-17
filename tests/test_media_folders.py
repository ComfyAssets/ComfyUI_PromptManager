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


class ServerSeparatorTests(unittest.TestCase):
    """#646: ``\\`` separates names only on Windows; on POSIX it is part of one."""

    def test_posix_keeps_a_backslash_inside_one_name(self):
        import posixpath

        from utils.media_folders import path_separators, split_relative

        self.assertEqual(path_separators(posixpath), "/")
        self.assertEqual(
            split_relative("m.safetensors\\2026-11-17/x.png", posixpath),
            ["m.safetensors\\2026-11-17", "x.png"],
        )
        self.assertEqual(split_relative("a\\..\\b", posixpath), ["a\\..\\b"])

    def test_windows_splits_on_both(self):
        import ntpath

        from utils.media_folders import path_separators, split_relative

        self.assertEqual(path_separators(ntpath), "/\\")
        self.assertEqual(
            split_relative("m.safetensors\\2026-11-17/x.png", ntpath),
            ["m.safetensors", "2026-11-17", "x.png"],
        )
        self.assertEqual(split_relative("a\\..\\b", ntpath), ["a", "..", "b"])

    def test_trash_detection_follows_the_server_separators(self):
        import ntpath
        import posixpath

        from utils.media_folders import in_trash_folder

        self.assertTrue(in_trash_folder(".Trash-1000\\x.png", ntpath))
        self.assertFalse(in_trash_folder(".Trash-1000\\x.png", posixpath))
        self.assertTrue(in_trash_folder("a/.Trash-1000/x.png", posixpath))
