"""Pure helpers of the shared Images listing (py/api/gallery_listing.py, #348)."""

import ntpath
import os
import posixpath
import sys
import tempfile
import unittest
from pathlib import Path, PurePath, PureWindowsPath
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py.api import gallery_listing  # noqa: E402
from py.api.gallery_listing import (  # noqa: E402
    DISCARDED,
    FAILED,
    STORED,
    RootListing,
    WalkOutcome,
    folder_sets,
    rewalk_interval,
    root_contains,
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

    def test_folders_come_from_the_path_strings_not_relative_to(self):
        # relative_to per file was half of a 20,000-file walk (#348 live test).
        root = Path("/out")
        files = [(root / "a" / "b" / "one.png", 1.0), (root / "two.png", 2.0)]
        with patch.object(PurePath, "relative_to", side_effect=AssertionError):
            folders, with_ancestors = folder_sets(root, files)
        self.assertEqual(folders, {os.path.join("a", "b")})
        self.assertEqual(with_ancestors, {os.path.join("a", "b"), "a"})

    def test_a_path_not_spelled_like_the_root_falls_back_to_relative_to(self):
        # Windows folds case: the string prefix misses, relative_to matches.
        root = PureWindowsPath("C:/Out")
        files = [(PureWindowsPath("c:/out/x/y.png"), 1.0)]
        folders, with_ancestors = folder_sets(root, files)
        self.assertEqual(folders, {"x"})
        self.assertEqual(folders, _legacy_subfolders(root, files, False))
        self.assertEqual(with_ancestors, _legacy_subfolders(root, files, True))


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
        listed, folders, with_ancestors, resolved = walk_root(scan, self.root, None)
        self.assertEqual(calls, [self.root])
        self.assertEqual(listed, files)
        self.assertEqual(folders, frozenset({"sub"}))
        self.assertEqual(with_ancestors, frozenset({"sub"}))
        self.assertEqual(resolved, os.path.normcase(os.path.realpath(self.root)))

    def test_the_resolved_root_follows_a_symlinked_root(self):
        link = Path(self._tmp.name) / "linked"
        try:
            os.symlink(self.root, link, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks unavailable: {error}")
        scan, _calls = self._scan([])
        resolved = walk_root(scan, link, None)[3]
        self.assertEqual(resolved, os.path.normcase(os.path.realpath(self.root)))
        self.assertNotEqual(resolved, os.path.normcase(str(link)))

    def test_an_unchanged_walk_returns_the_previous_list_object(self):
        files = [(self.root / "sub" / "a.png", 2.0)]
        previous = list(files)
        scan, _calls = self._scan(files)
        listed, _folders, _ancestors, _resolved = walk_root(scan, self.root, previous)
        self.assertIs(listed, previous)

    def test_a_changed_walk_returns_the_new_list(self):
        previous = [(self.root / "sub" / "a.png", 2.0)]
        files = [(self.root / "sub" / "a.png", 3.0)]
        scan, _calls = self._scan(files)
        listed, _folders, _ancestors, _resolved = walk_root(scan, self.root, previous)
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

    def test_refresh_constants(self):
        self.assertEqual(gallery_listing.REFRESH_AFTER, 30)
        self.assertEqual(gallery_listing.REFRESH_DELAY, 1.0)
        self.assertIsNone(RootListing().rewalk_handle)

    def test_a_new_record_is_clean_and_never_walked(self):
        record = RootListing()
        self.assertFalse(record.dirty)
        self.assertIsNone(record.resolved_root)
        self.assertIsNone(record.last_started_at)
        self.assertIsNone(record.last_duration)

    def test_drop_keeps_the_resolved_root_and_the_walk_timing(self):
        # A drop clears the listing, not what is known about the root itself.
        record = RootListing(resolved_root="/o", last_started_at=1.0)
        record.last_duration = 0.5
        record.dirty = True
        record.drop()
        self.assertEqual(record.resolved_root, "/o")
        self.assertEqual((record.last_started_at, record.last_duration), (1.0, 0.5))
        self.assertTrue(record.dirty)


class RewalkIntervalTests(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(
            (
                gallery_listing.REWALK_FLOOR,
                gallery_listing.REWALK_FACTOR,
                gallery_listing.REWALK_CAP,
                gallery_listing.MIN_REWALK_DELAY,
            ),
            (5, 5, 15, 0.5),
        )

    def test_short_walks_wait_the_floor(self):
        self.assertEqual(rewalk_interval(0.2), 5)
        self.assertEqual(rewalk_interval(0), 5)
        self.assertEqual(rewalk_interval(None), 5)  # never walked

    def test_the_interval_scales_with_the_last_walk(self):
        self.assertEqual(rewalk_interval(1.0), 5)
        self.assertEqual(rewalk_interval(2.0), 10)
        self.assertAlmostEqual(rewalk_interval(2.5), 12.5)

    def test_slow_walks_are_capped(self):
        self.assertEqual(rewalk_interval(3.0), 15)
        self.assertEqual(rewalk_interval(60.0), 15)

    def test_the_constants_are_read_when_called(self):
        with (
            patch.object(gallery_listing, "REWALK_FLOOR", 0),
            patch.object(gallery_listing, "REWALK_CAP", 0),
        ):
            self.assertEqual(rewalk_interval(2.0), 0)


class RootContainsTests(unittest.TestCase):
    def test_posix_paths_under_the_root_match(self):
        root = posixpath.normcase("/srv/out")
        self.assertTrue(root_contains(root, "/srv/out/a.png", posixpath))
        self.assertTrue(root_contains(root, "/srv/out/sub/deep/a.png", posixpath))

    def test_a_sibling_sharing_the_prefix_does_not_match(self):
        self.assertFalse(root_contains("/srv/out", "/srv/out2/a.png", posixpath))
        self.assertFalse(root_contains("/srv/out", "/srv/outside.png", posixpath))
        self.assertFalse(root_contains("/srv/out", "/srv/other/a.png", posixpath))

    def test_posix_keeps_case(self):
        self.assertFalse(root_contains("/srv/out", "/srv/OUT/a.png", posixpath))

    def test_the_root_itself_is_not_an_output(self):
        self.assertFalse(root_contains("/srv/out", "/srv/out", posixpath))

    def test_windows_folds_case_and_separators(self):
        root = ntpath.normcase("C:\\ComfyUI\\Output")
        self.assertTrue(root_contains(root, "c:\\comfyui\\output\\a.png", ntpath))
        self.assertTrue(root_contains(root, "C:/ComfyUI/OUTPUT/sub/a.PNG", ntpath))
        self.assertFalse(root_contains(root, "C:\\ComfyUI\\Output2\\a.png", ntpath))
        self.assertFalse(root_contains(root, "D:\\ComfyUI\\Output\\a.png", ntpath))

    def test_a_windows_drive_root_and_a_unc_share(self):
        drive = ntpath.normcase("E:\\")
        self.assertTrue(root_contains(drive, "e:\\x.png", ntpath))
        self.assertFalse(root_contains(drive, "F:\\x.png", ntpath))
        share = ntpath.normcase("\\\\Server\\Share\\Out")
        self.assertTrue(root_contains(share, "\\\\server\\share\\out\\a.png", ntpath))
        self.assertFalse(root_contains(share, "\\\\server\\share\\a.png", ntpath))

    def test_a_windows_extended_length_prefix_is_ignored(self):
        root = ntpath.normcase("C:\\ComfyUI\\Output")
        long_file = "\\\\?\\C:\\ComfyUI\\Output\\deep\\a.png"
        self.assertTrue(root_contains(root, long_file, ntpath))
        prefixed_root = ntpath.normcase("\\\\?\\C:\\ComfyUI\\Output")
        self.assertTrue(
            root_contains(prefixed_root, "C:\\ComfyUI\\Output\\a.png", ntpath)
        )
        share = ntpath.normcase("\\\\Server\\Share\\Out")
        unc_file = "\\\\?\\UNC\\server\\share\\out\\a.png"
        self.assertTrue(root_contains(share, unc_file, ntpath))
        self.assertFalse(
            root_contains(root, "\\\\?\\C:\\ComfyUI\\Output2\\a.png", ntpath)
        )

    def test_posix_keeps_a_literal_question_mark_folder(self):
        self.assertFalse(root_contains("/srv/out", "//?/srv/out/a.png", posixpath))

    def test_the_default_is_this_platforms_rule(self):
        root = os.path.normcase(os.path.realpath(tempfile.gettempdir()))
        inside = os.path.join(root, "x.png")
        self.assertTrue(root_contains(root, inside))
        self.assertTrue(root_contains(root, inside.upper()) == (os.name == "nt"))


if __name__ == "__main__":
    unittest.main()
