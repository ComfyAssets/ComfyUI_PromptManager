"""Pure helpers of the shared Images listing (py/api/gallery_listing.py, #348)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py.api import gallery_listing  # noqa: E402
from py.api.gallery_listing import (  # noqa: E402
    DISCARDED,
    FAILED,
    STORED,
    RootListing,
    WalkOutcome,
    folder_sets,
    same_listing,
    walk_root,
)


def _legacy_subfolders(root, files, include_ancestors):
    """``_root_subfolders`` as it was before #348, the reference for folder_sets."""
    subfolders = set()
    for f, _mtime in files:
        rel_dir = str(f.relative_to(root).parent)
        if rel_dir and rel_dir != ".":
            subfolders.add(rel_dir)
    if include_ancestors:
        ancestors = set()
        for folder in subfolders:
            parts = folder.replace("\\", "/").split("/")
            for i in range(1, len(parts)):
                ancestors.add("/".join(parts[:i]))
        subfolders.update(ancestors)
    return subfolders


class FolderSetsTests(unittest.TestCase):
    def test_nested_folders_match_the_previous_subfolder_listing(self):
        root = Path("/out")
        files = [
            (root / "top.png", 1.0),
            (root / "a" / "one.png", 2.0),
            (root / "a" / "b" / "c" / "two.png", 3.0),
            (root / "d" / "three.png", 4.0),
        ]
        folders, with_ancestors = folder_sets(root, files)
        self.assertEqual(folders, _legacy_subfolders(root, files, False))
        self.assertEqual(with_ancestors, _legacy_subfolders(root, files, True))
        self.assertIn("a/b", with_ancestors)
        self.assertNotIn("a/b", folders)
        self.assertIsInstance(folders, frozenset)
        self.assertIsInstance(with_ancestors, frozenset)

    def test_windows_separators_keep_the_previous_spelling(self):
        root = PureWindowsPath("C:/out")
        files = [
            (root / "x" / "y" / "z.png", 1.0),
            (root / "w.png", 2.0),
        ]
        folders, with_ancestors = folder_sets(root, files)
        self.assertEqual(folders, {"x\\y"})
        self.assertEqual(with_ancestors, {"x\\y", "x"})
        self.assertEqual(folders, _legacy_subfolders(root, files, False))
        self.assertEqual(with_ancestors, _legacy_subfolders(root, files, True))

    def test_an_empty_listing_has_no_folders(self):
        self.assertEqual(folder_sets(Path("/out"), []), (frozenset(), frozenset()))


class SameListingTests(unittest.TestCase):
    def test_equal_path_and_mtime_lists_are_the_same(self):
        old = [(Path("/o/a.png"), 1.0), (Path("/o/b.png"), 2.0)]
        self.assertTrue(same_listing(old, list(old)))
        self.assertTrue(same_listing(old, old))

    def test_a_changed_mtime_path_or_length_differs(self):
        old = [(Path("/o/a.png"), 1.0), (Path("/o/b.png"), 2.0)]
        self.assertFalse(same_listing(old, [(Path("/o/a.png"), 1.5), old[1]]))
        self.assertFalse(same_listing(old, [(Path("/o/c.png"), 1.0), old[1]]))
        self.assertFalse(same_listing(old, old[:1]))
        self.assertFalse(same_listing(None, old))


class WalkRootTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "output"
        (self.root / "sub").mkdir(parents=True)

    def _scan(self, files):
        calls = []

        def scan(root):
            calls.append(root)
            return list(files)

        return scan, calls

    def test_returns_the_listing_and_both_folder_sets(self):
        files = [(self.root / "sub" / "a.png", 2.0), (self.root / "b.png", 1.0)]
        scan, calls = self._scan(files)
        listed, folders, with_ancestors = walk_root(scan, self.root, None)
        self.assertEqual(calls, [self.root])
        self.assertEqual(listed, files)
        self.assertEqual(folders, frozenset({"sub"}))
        self.assertEqual(with_ancestors, frozenset({"sub"}))

    def test_an_unchanged_walk_returns_the_previous_list_object(self):
        files = [(self.root / "sub" / "a.png", 2.0)]
        previous = list(files)
        scan, _calls = self._scan(files)
        listed, _folders, _ancestors = walk_root(scan, self.root, previous)
        self.assertIs(listed, previous)

    def test_a_changed_walk_returns_the_new_list(self):
        previous = [(self.root / "sub" / "a.png", 2.0)]
        files = [(self.root / "sub" / "a.png", 3.0)]
        scan, _calls = self._scan(files)
        listed, _folders, _ancestors = walk_root(scan, self.root, previous)
        self.assertIsNot(listed, previous)
        self.assertEqual(listed, files)

    def test_a_deleted_root_raises_instead_of_listing_nothing(self):
        scan, calls = self._scan([])
        missing = self.root / "gone"
        with self.assertRaises(OSError):
            walk_root(scan, missing, None)
        self.assertEqual(calls, [])


class RecordTests(unittest.TestCase):
    def test_drop_clears_the_listing_and_bumps_the_generation(self):
        record = RootListing()
        record.files = [(Path("/o/a.png"), 1.0)]
        record.folders = frozenset({"x"})
        record.folders_with_ancestors = frozenset({"x"})
        record.walked_at = 5.0
        record.drop()
        record.drop()
        self.assertIsNone(record.files)
        self.assertIsNone(record.folders)
        self.assertIsNone(record.folders_with_ancestors)
        self.assertIsNone(record.walked_at)
        self.assertEqual(record.generation, 2)

    def test_outcomes(self):
        error = OSError("gone")
        self.assertEqual(WalkOutcome.stored().kind, STORED)
        self.assertEqual(WalkOutcome.discarded().kind, DISCARDED)
        failed = WalkOutcome.failed(error)
        self.assertEqual(failed.kind, FAILED)
        self.assertIs(failed.error, error)

    def test_refresh_constant(self):
        self.assertEqual(gallery_listing.REFRESH_AFTER, 30)


if __name__ == "__main__":
    unittest.main()
