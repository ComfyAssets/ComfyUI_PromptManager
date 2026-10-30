"""Native proof of the macOS trash backend (#499 design §10.2).

Runs only on macOS with ``PM_NATIVE_TRASH=macos`` (the ``native-trash`` job in
portability.yml), because it puts files into the real Trash of the user
running it. With the variable set, a bridge that cannot load fails the test
rather than skipping it. Files moved into a trash folder are left there: the
runner is disposable and this test never deletes from a trash folder.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import trash  # noqa: E402

NATIVE = sys.platform == "darwin" and os.environ.get("PM_NATIVE_TRASH") == "macos"
HDIUTIL_SECONDS = 60
# Measured: macos-15-intel kept a fresh image busy after a trash (run 40954618922).
DETACH_WAIT_SECONDS = 30


class SpyBridge:
    """The real bridge, recording every ``trash`` result."""

    def __init__(self, real):
        self.real = real
        self.results = []

    def facts(self, path):
        return self.real.facts(path)

    def trash_dir(self, path):
        return self.real.trash_dir(path)

    def trash(self, path):
        result = self.real.trash(path)
        self.results.append(result)
        return result


def _hdiutil(*args):
    return subprocess.run(
        ["hdiutil", *args], capture_output=True, text=True, timeout=HDIUTIL_SECONDS
    )


def _our_open_files(mount):
    """Files this process holds open under *mount* (``lsof -p``): the handle proof."""
    listed = subprocess.run(
        ["lsof", "-Fn", "-p", str(os.getpid())],
        capture_output=True,
        text=True,
        timeout=HDIUTIL_SECONDS,
    )
    prefix = os.path.realpath(mount) + os.sep
    return [
        line[1:]
        for line in listed.stdout.splitlines()
        if line.startswith("n") and os.path.realpath(line[1:]).startswith(prefix)
    ]


def _detach(mount):
    """Detach *mount*; None on success, else why (after a forced detach).

    This process must hold nothing under it. A volume macOS has just written a
    trash folder to can stay busy for a moment (Spotlight, fseventsd), so the
    plain detach is retried until the system releases it, within a bound.
    """
    ours = _our_open_files(mount)
    if ours:
        _hdiutil("detach", mount, "-force")
        return f"this test still held files open under {mount}: {ours}"
    deadline = time.monotonic() + DETACH_WAIT_SECONDS
    while True:
        if not os.path.ismount(mount):
            return None  # a "busy" detach can still finish after it returns
        done = _hdiutil("detach", mount)
        if done.returncode == 0:
            return None
        if time.monotonic() > deadline:
            holders = subprocess.run(
                ["lsof", "+f", "--", mount],
                capture_output=True,
                text=True,
                timeout=HDIUTIL_SECONDS,
            ).stdout
            _hdiutil("detach", mount, "-force")
            return f"detach {mount}: {done.stderr.strip()}; held by: {holders!r}"
        time.sleep(1)  # poll the volume's release (a state), never a fixed wait


@unittest.skipUnless(NATIVE, "macOS native trash proof (PM_NATIVE_TRASH=macos)")
class MacTrashNativeTests(unittest.TestCase):
    def setUp(self):
        from utils import trash_macos

        self.trash_macos = trash_macos
        self.spy = SpyBridge(trash_macos.load_bridge())  # fails, never skips
        self.backend = trash_macos.MacTrashBackend(bridge=self.spy)
        self._tmp = tempfile.TemporaryDirectory(prefix="pm-native-trash-")
        self.tmp = os.path.realpath(self._tmp.name)
        self._mounts = []
        self._modes = []

    def tearDown(self):
        failures = []
        try:
            for path, mode in reversed(self._modes):
                os.chmod(path, mode)
            for mount in reversed(self._mounts):
                problem = _detach(mount)
                if problem:
                    failures.append(problem)
        finally:
            self._tmp.cleanup()
        self.assertEqual(failures, [])

    def _file(self, folder, name="a.png", content=None):
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(content or os.urandom(64))
        return path

    def _image(self, name, fs="APFS"):
        image = os.path.join(self.tmp, name + ".dmg")
        mount = os.path.join(self.tmp, name + "-mnt")
        os.makedirs(mount)
        made = _hdiutil("create", "-size", "40m", "-fs", fs, "-volname", name, image)
        self.assertEqual(made.returncode, 0, made.stderr)
        attached = _hdiutil("attach", image, "-nobrowse", "-mountpoint", mount)
        self.assertEqual(attached.returncode, 0, attached.stderr)
        self._mounts.append(mount)
        return image, mount

    def _assert_trashed(self, source, content, info, folder):
        self.assertFalse(os.path.lexists(source))
        ok, resulting, error = self.spy.results[-1]
        self.assertTrue(ok, error)
        self.assertEqual(os.path.realpath(os.path.dirname(resulting)), folder)
        moved = os.lstat(resulting)
        self.assertEqual((moved.st_dev, moved.st_ino), (info.st_dev, info.st_ino))
        with open(resulting, "rb") as handle:
            self.assertEqual(handle.read(), content)
        return resulting

    def _assert_kept(self, path, content):
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), content)

    def _assert_refused(self, path, roots, code):
        with open(path, "rb") as handle:
            content = handle.read()
        self.assertEqual(self.backend.available(path, roots), code)
        with self.assertRaises(trash.TrashRefused) as caught:
            self.backend.move(path, roots)
        self.assertEqual(caught.exception.code, code)
        self._assert_kept(path, content)

    def test_n1_startup_volume_goes_to_the_home_trash(self):
        root = os.path.join(self.tmp, "output")
        source = self._file(root)
        with open(source, "rb") as handle:
            content = handle.read()
        info = os.lstat(source)
        self.assertIsNone(self.backend.available(source, [root]))
        self.assertEqual(self.backend.move(source, [root]), trash.HOME_TRASH_LABEL)
        home_trash = os.path.realpath(os.path.expanduser("~/.Trash"))
        self._assert_trashed(source, content, info, home_trash)

    def test_n2_a_name_clash_never_replaces(self):
        first_root = os.path.join(self.tmp, "one")
        second_root = os.path.join(self.tmp, "two")
        first = self._file(first_root, "same.png", b"first")
        second = self._file(second_root, "same.png", b"second")
        self.backend.move(first, [first_root])
        self.backend.move(second, [second_root])
        (_, one, _), (_, two, _) = self.spy.results
        self.assertNotEqual(one, two)
        self._assert_kept(one, b"first")
        self._assert_kept(two, b"second")

    def test_n3_fresh_external_volume_uses_its_own_trash(self):
        for fs in ("APFS", "MS-DOS FAT32"):
            with self.subTest(fs=fs):
                _image, mount = self._image("fresh" + fs.split()[0], fs)
                root = os.path.join(mount, "output")
                source = self._file(root)
                with open(source, "rb") as handle:
                    content = handle.read()
                info = os.lstat(source)
                self.assertFalse(os.path.lexists(os.path.join(mount, ".Trashes")))
                self.assertIsNone(self.backend.available(source, [root]))
                label = self.backend.move(source, [root])
                uid = str(os.getuid())
                self.assertEqual(label, f".Trashes/{uid} on its drive")
                folder = os.path.realpath(os.path.join(mount, ".Trashes", uid))
                self._assert_trashed(source, content, info, folder)

    def test_n4_volume_root_as_output_root_is_trash_in_root(self):
        _image, mount = self._image("rooted")
        source = self._file(os.path.join(mount, "output"))
        self._assert_refused(source, [mount], trash.TRASH_IN_ROOT)
        self.assertEqual(self.spy.results, [])

    def test_n5_read_only_volume_is_no_trash(self):
        image, mount = self._image("readonly")
        source = self._file(mount, "ro.png", b"read-only bytes")
        self.assertIsNone(_detach(mount))
        self._mounts.remove(mount)
        attached = _hdiutil(
            "attach", image, "-nobrowse", "-readonly", "-mountpoint", mount
        )
        self.assertEqual(attached.returncode, 0, attached.stderr)
        self._mounts.append(mount)
        self._assert_refused(source, [os.path.join(mount, "output")], trash.NO_TRASH)

    def test_n6_unwritable_folder_is_no_permission_and_macos_agrees(self):
        root = os.path.join(self.tmp, "locked")
        source = self._file(root, "p.png", b"kept")
        self._modes.append((root, 0o755))
        os.chmod(root, 0o555)
        self._assert_refused(source, [self.tmp], trash.NO_PERMISSION)
        self.assertEqual(self.spy.results, [])
        ok, _resulting, error = self.spy.real.trash(source)
        self.assertFalse(ok)
        self.assertEqual((error.domain, error.code), ("NSCocoaErrorDomain", 513))
        self._assert_kept(source, b"kept")


if __name__ == "__main__":
    unittest.main()
