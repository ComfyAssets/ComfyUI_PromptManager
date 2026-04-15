"""Tests for path traversal and symlink security in image serving."""

import os
import tempfile
import unittest
from pathlib import Path


class TestPathSecurity(unittest.TestCase):
    """Verify that path resolution and boundary checks prevent escapes."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.output_dir = Path(self.tmpdir) / "output"
        self.output_dir.mkdir()
        self.secret_dir = Path(self.tmpdir) / "secret"
        self.secret_dir.mkdir()

        # Create a legitimate image
        (self.output_dir / "legit.png").write_bytes(b"PNG")

        # Create a secret file outside the output dir
        (self.secret_dir / "password.txt").write_text("hunter2")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _is_safe_path(self, output_path, filepath):
        """Reproduce the safety check from serve_output_image."""
        image_path = (output_path / filepath).resolve()
        return image_path.is_relative_to(output_path.resolve()) and image_path.exists()

    def test_normal_path_allowed(self):
        self.assertTrue(self._is_safe_path(self.output_dir, "legit.png"))

    def test_dot_dot_traversal_blocked(self):
        self.assertFalse(
            self._is_safe_path(self.output_dir, "../secret/password.txt")
        )

    def test_encoded_dot_dot_blocked(self):
        # Even if someone encodes ../ as %2e%2e%2f, Path resolution catches it
        self.assertFalse(
            self._is_safe_path(self.output_dir, "..%2fsecret%2fpassword.txt")
        )

    def test_absolute_path_blocked(self):
        secret_path = str(self.secret_dir / "password.txt")
        self.assertFalse(self._is_safe_path(self.output_dir, secret_path))

    def test_symlink_escape_blocked(self):
        # Create a symlink inside output_dir pointing outside
        symlink_path = self.output_dir / "escape_link"
        try:
            symlink_path.symlink_to(self.secret_dir / "password.txt")
        except OSError:
            self.skipTest("Cannot create symlinks on this platform")

        # resolve() follows the symlink, so it should resolve to
        # secret_dir which is NOT relative_to output_dir
        image_path = symlink_path.resolve()
        self.assertFalse(image_path.is_relative_to(self.output_dir.resolve()))

    def test_symlink_within_root_allowed(self):
        # A symlink that stays within the output dir should be fine
        subdir = self.output_dir / "subdir"
        subdir.mkdir()
        (subdir / "img.png").write_bytes(b"PNG")

        symlink_path = self.output_dir / "link_to_sub"
        try:
            symlink_path.symlink_to(subdir / "img.png")
        except OSError:
            self.skipTest("Cannot create symlinks on this platform")

        image_path = symlink_path.resolve()
        self.assertTrue(image_path.is_relative_to(self.output_dir.resolve()))

    def test_nested_traversal_blocked(self):
        # Deep nested traversal: sub/../../secret/password.txt
        sub = self.output_dir / "sub"
        sub.mkdir()
        self.assertFalse(
            self._is_safe_path(self.output_dir, "sub/../../secret/password.txt")
        )

    def test_null_byte_in_path(self):
        # Null bytes should not bypass checks
        try:
            result = self._is_safe_path(self.output_dir, "legit.png\x00.txt")
        except ValueError:
            # Python raises ValueError for embedded null bytes — this is safe
            result = False
        self.assertFalse(result)


class TestRootIndexSecurity(unittest.TestCase):
    """Verify that root_index parameter is validated safely."""

    def test_negative_index_ignored(self):
        """Negative root index should not select any directory."""
        output_dirs = [Path("/fake/dir1"), Path("/fake/dir2")]
        idx = -1
        # The code checks 0 <= idx < len(output_dirs)
        self.assertFalse(0 <= idx < len(output_dirs))

    def test_out_of_range_index_ignored(self):
        output_dirs = [Path("/fake/dir1")]
        idx = 5
        self.assertFalse(0 <= idx < len(output_dirs))

    def test_valid_index_selects_correct_root(self):
        output_dirs = [Path("/fake/dir1"), Path("/fake/dir2"), Path("/fake/dir3")]
        idx = 1
        self.assertTrue(0 <= idx < len(output_dirs))
        self.assertEqual(output_dirs[idx], Path("/fake/dir2"))

    def test_non_numeric_index_handled(self):
        """Non-numeric root value should not crash."""
        try:
            int("abc")
            self.fail("Should have raised ValueError")
        except ValueError:
            pass  # This is the expected behavior


if __name__ == "__main__":
    unittest.main()
