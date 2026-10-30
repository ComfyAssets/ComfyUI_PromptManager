"""The freedesktop trash backend (#480): rename into a trash or refuse, never unlink.

Every test builds a disposable tree: a fake ``XDG_DATA_HOME`` and, through the
backend's ``mounts`` seam, a fake "drive" mount point, so the real trash of the
user running the tests is never touched. Moves are real renames on one
filesystem.
"""

import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import trash  # noqa: E402

POSIX = os.name == "posix" and sys.platform != "darwin"


@unittest.skipUnless(POSIX, "freedesktop trash is the Linux/BSD backend")
class FreedesktopTrashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pm-trash-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data_home = os.path.join(self.tmp, "xdg")
        self.drive = os.path.join(self.tmp, "drive")
        self.root = os.path.join(self.drive, "output")
        self.home_root = os.path.join(self.tmp, "home-output")
        for folder in (self.data_home, self.root, self.home_root):
            os.makedirs(folder)
        self.mounts = ["/", os.path.realpath(self.drive)]
        self.backend = trash.FreedesktopBackend(
            data_home=self.data_home, mounts=lambda: list(self.mounts)
        )

    def _file(self, folder, name="a.png", content=b"png-bytes"):
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(content)
        return path

    def _info(self, trash_dir, name):
        with open(os.path.join(trash_dir, "info", name + ".trashinfo")) as handle:
            return handle.read()

    # ── choosing the trash ────────────────────────────────────────────

    def test_file_on_the_home_mount_goes_to_the_home_trash(self):
        path = self._file(self.home_root)
        label = self.backend.move(path, [self.home_root])
        home_trash = os.path.join(self.data_home, "Trash")
        self.assertEqual(label, trash.HOME_TRASH_LABEL)
        self.assertFalse(os.path.exists(path))
        with open(os.path.join(home_trash, "files", "a.png"), "rb") as handle:
            self.assertEqual(handle.read(), b"png-bytes")
        info = self._info(home_trash, "a.png")
        self.assertIn("[Trash Info]\n", info)
        self.assertIn("Path=" + os.path.realpath(path) + "\n", info)
        self.assertRegex(info, r"DeletionDate=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\n")

    def test_file_on_another_mount_uses_its_trash_dash_uid(self):
        path = self._file(self.root, "b c%.png")
        self.backend.move(path, [self.root])
        top = os.path.join(self.drive, f".Trash-{os.getuid()}")
        info_mode = stat.S_IMODE(os.lstat(top).st_mode)
        self.assertEqual(info_mode, 0o700)
        self.assertTrue(os.path.exists(os.path.join(top, "files", "b c%.png")))
        # A top-directory trash records the path relative to its top folder,
        # percent-encoded.
        self.assertIn("Path=output/b%20c%25.png\n", self._info(top, "b c%.png"))

    def test_sticky_dot_trash_folder_is_used(self):
        shared = os.path.join(self.drive, ".Trash")
        os.mkdir(shared)
        os.chmod(shared, 0o1777)
        path = self._file(self.root)
        self.backend.move(path, [self.root])
        self.assertTrue(
            os.path.exists(os.path.join(shared, str(os.getuid()), "files", "a.png"))
        )

    def test_dot_trash_without_sticky_bit_is_skipped(self):
        shared = os.path.join(self.drive, ".Trash")
        os.mkdir(shared, 0o777)
        os.chmod(shared, 0o777)
        path = self._file(self.root)
        self.backend.move(path, [self.root])
        self.assertFalse(os.path.exists(os.path.join(shared, str(os.getuid()))))
        top = os.path.join(self.drive, f".Trash-{os.getuid()}")
        self.assertTrue(os.path.exists(os.path.join(top, "files", "a.png")))

    def test_dot_trash_that_is_a_link_is_skipped(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.mkdir(elsewhere)
        os.chmod(elsewhere, 0o1777)
        os.symlink(elsewhere, os.path.join(self.drive, ".Trash"))
        path = self._file(self.root)
        self.backend.move(path, [self.root])
        self.assertEqual(os.listdir(elsewhere), [])

    def test_trash_dash_uid_that_is_a_link_is_refused(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.mkdir(elsewhere)
        os.symlink(elsewhere, os.path.join(self.drive, f".Trash-{os.getuid()}"))
        path = self._file(self.root)
        with self.assertRaises(trash.TrashRefused) as caught:
            self.backend.move(path, [self.root])
        self.assertEqual(caught.exception.code, trash.NO_TRASH)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(os.listdir(elsewhere), [])

    def test_trash_inside_an_output_root_is_refused(self):
        # A root above the drive's top folder would hold the drive's .Trash-uid.
        # (A root that is exactly the top folder is allowed: #543,
        # tests/test_trash_mount_root.py.)
        path = self._file(self.root)
        with self.assertRaises(trash.TrashRefused) as caught:
            self.backend.move(path, [self.tmp])
        self.assertEqual(caught.exception.code, trash.TRASH_IN_ROOT)
        self.assertTrue(os.path.exists(path))
        self.assertFalse(
            os.path.exists(os.path.join(self.drive, f".Trash-{os.getuid()}"))
        )

    def test_home_trash_inside_an_output_root_is_refused(self):
        path = self._file(self.home_root)
        with self.assertRaises(trash.TrashRefused) as caught:
            self.backend.move(path, [self.home_root, self.tmp])
        self.assertEqual(caught.exception.code, trash.TRASH_IN_ROOT)
        self.assertTrue(os.path.exists(path))

    def test_read_only_top_folder_is_no_trash(self):
        path = self._file(self.root)
        os.chmod(self.drive, 0o555)
        self.addCleanup(os.chmod, self.drive, 0o755)
        if os.access(self.drive, os.W_OK):
            self.skipTest("running as root: permissions are not enforced")
        self.assertEqual(self.backend.available(path, [self.root]), trash.NO_TRASH)
        with self.assertRaises(trash.TrashRefused) as caught:
            self.backend.move(path, [self.root])
        self.assertEqual(caught.exception.code, trash.NO_TRASH)
        self.assertTrue(os.path.exists(path))

    def test_available_reports_without_writing(self):
        path = self._file(self.root)
        self.assertIsNone(self.backend.available(path, [self.root]))
        self.assertEqual(self.backend.available(path, [self.tmp]), trash.TRASH_IN_ROOT)
        self.assertIsNone(self.backend.available(path, [self.drive]))
        self.assertEqual(os.listdir(self.drive), ["output"])

    # ── names and the no-replace rename ───────────────────────────────

    def test_existing_name_in_the_trash_is_never_replaced(self):
        top = os.path.join(self.drive, f".Trash-{os.getuid()}")
        os.makedirs(os.path.join(top, "files"), mode=0o700)
        os.makedirs(os.path.join(top, "info"), mode=0o700)
        os.chmod(top, 0o700)
        # A stray trashed file with no info file (another tool crashed).
        stray = self._file(os.path.join(top, "files"), "a.png", b"older-file")
        path = self._file(self.root)
        self.backend.move(path, [self.root])
        with open(stray, "rb") as handle:
            self.assertEqual(handle.read(), b"older-file")
        self.assertTrue(os.path.exists(os.path.join(top, "files", "a.1.png")))
        self.assertTrue(os.path.exists(os.path.join(top, "info", "a.1.png.trashinfo")))
        self.assertFalse(os.path.exists(os.path.join(top, "info", "a.png.trashinfo")))

    def test_existing_info_name_is_skipped(self):
        path = self._file(self.root)
        self.backend.move(path, [self.root])
        second = self._file(self.root)
        self.backend.move(second, [self.root])
        top = os.path.join(self.drive, f".Trash-{os.getuid()}")
        self.assertEqual(
            sorted(os.listdir(os.path.join(top, "files"))), ["a.1.png", "a.png"]
        )

    def test_missing_no_replace_rename_is_no_trash(self):
        path = self._file(self.root)
        for error in (38, 22):  # ENOSYS, EINVAL
            with self.subTest(error=error):
                backend = trash.FreedesktopBackend(
                    data_home=self.data_home,
                    mounts=lambda: list(self.mounts),
                    rename=mock.Mock(side_effect=OSError(error, "no")),
                )
                with self.assertRaises(trash.TrashRefused) as caught:
                    backend.move(path, [self.root])
                self.assertEqual(caught.exception.code, trash.NO_TRASH)
                self.assertTrue(os.path.exists(path))
                top = os.path.join(self.drive, f".Trash-{os.getuid()}")
                self.assertEqual(os.listdir(os.path.join(top, "info")), [])

    def test_cross_device_rename_is_no_trash_and_never_copies(self):
        path = self._file(self.root)
        backend = trash.FreedesktopBackend(
            data_home=self.data_home,
            mounts=lambda: list(self.mounts),
            rename=mock.Mock(side_effect=OSError(18, "EXDEV")),
        )
        with mock.patch("shutil.move") as move, mock.patch("shutil.copy2") as copy:
            with self.assertRaises(trash.TrashRefused) as caught:
                backend.move(path, [self.root])
        self.assertEqual(caught.exception.code, trash.NO_TRASH)
        move.assert_not_called()
        copy.assert_not_called()
        self.assertTrue(os.path.exists(path))

    def test_other_rename_failure_is_failed_and_removes_only_our_info(self):
        path = self._file(self.root)
        backend = trash.FreedesktopBackend(
            data_home=self.data_home,
            mounts=lambda: list(self.mounts),
            rename=mock.Mock(side_effect=OSError(5, "EIO")),
        )
        with self.assertRaises(trash.TrashRefused) as caught:
            backend.move(path, [self.root])
        self.assertEqual(caught.exception.code, trash.FAILED)
        top = os.path.join(self.drive, f".Trash-{os.getuid()}")
        self.assertEqual(os.listdir(os.path.join(top, "info")), [])
        self.assertTrue(os.path.exists(path))

    def test_never_removes_or_moves_the_source(self):
        path = self._file(self.root)
        real_unlink = os.unlink

        def guarded_unlink(target, *args, **kwargs):
            # Only our own .trashinfo may ever be removed.
            self.assertTrue(str(target).endswith(".trashinfo"), target)
            return real_unlink(target, *args, **kwargs)

        backend = trash.FreedesktopBackend(
            data_home=self.data_home,
            mounts=lambda: list(self.mounts),
            rename=mock.Mock(side_effect=OSError(5, "EIO")),
        )
        with (
            mock.patch("os.unlink", guarded_unlink),
            mock.patch("os.remove", guarded_unlink),
            mock.patch("shutil.move") as move,
            mock.patch("os.rename") as rename,
        ):
            with self.assertRaises(trash.TrashRefused):
                backend.move(path, [self.root])
        move.assert_not_called()
        rename.assert_not_called()
        self.assertTrue(os.path.exists(path))

    def test_mountinfo_parsing_decodes_escapes(self):
        text = (
            "36 35 98:0 / / rw - ext4 /dev/root rw\n"
            "37 36 98:1 / /mnt/my\\040drive rw - ext4 /dev/sdb1 rw\n"
        )
        self.assertEqual(trash.parse_mountinfo(text), ["/", "/mnt/my drive"])

    def test_mount_of_picks_the_longest_containing_mount(self):
        mounts = ["/", "/mnt", "/mnt/data"]
        self.assertEqual(trash.mount_of("/mnt/data/x/y.png", mounts), "/mnt/data")
        self.assertEqual(trash.mount_of("/mnt/datax/y.png", mounts), "/mnt")
        self.assertEqual(trash.mount_of("/home/u/y.png", mounts), "/")


class UnavailableBackendTests(unittest.TestCase):
    def test_reports_no_trash_and_leaves_the_file(self):
        backend = trash.UnavailableBackend()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "a.png")
            with open(path, "wb") as handle:
                handle.write(b"x")
            self.assertEqual(backend.available(path, [folder]), trash.NO_TRASH)
            with self.assertRaises(trash.TrashRefused) as caught:
                backend.move(path, [folder])
            self.assertEqual(caught.exception.code, trash.NO_TRASH)
            self.assertTrue(os.path.exists(path))

    def test_each_platform_gets_its_backend(self):
        from utils import trash_macos

        self.assertIsInstance(trash.default_backend("win32"), trash.UnavailableBackend)
        self.assertIsInstance(
            trash.default_backend("darwin"), trash_macos.MacTrashBackend
        )
        self.assertIsInstance(trash.default_backend("linux"), trash.FreedesktopBackend)

    def test_windows_says_why_the_recycle_bin_is_not_used(self):
        # #500 ruling (a): the Recycle Bin can delete permanently, so it is never used.
        backend = trash.default_backend("win32")
        self.assertEqual(
            backend.available("C:/x/a.png", ["C:/x"]), trash.WINDOWS_RECYCLE
        )
        with self.assertRaises(trash.TrashRefused) as caught:
            backend.move("C:/x/a.png", ["C:/x"])
        self.assertEqual(caught.exception.code, trash.WINDOWS_RECYCLE)
        self.assertIn(trash.WINDOWS_RECYCLE, trash.CODES)

    def test_module_functions_use_the_replaceable_backend(self):
        fake = mock.Mock()
        fake.available.return_value = "network"
        fake.move.return_value = "label"
        with mock.patch.object(trash, "BACKEND", fake):
            self.assertEqual(trash.trash_available("/x/a.png", ["/x"]), "network")
            self.assertEqual(trash.move_to_trash("/x/a.png", ["/x"]), "label")


if __name__ == "__main__":
    unittest.main()
