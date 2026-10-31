"""The macOS trash backend's decisions (#499), with a fake Foundation bridge.

No Objective-C is involved: the bridge is faked, ``uid``/``home``/``lstat``/
``access`` are injected, and the only real files are temp files used for the
"source still exists" and output-root identity checks. Runs on every OS.
"""

import os
import shutil
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import trash, trash_macos  # noqa: E402
from utils.trash_macos import Facts, MacTrashBackend, NSErr  # noqa: E402

LOCAL = Facts(local=True, read_only=False, root_fs=True, volume="/", ubiquitous=False)
COCOA = "NSCocoaErrorDomain"
NO_TRASH_DIR = NSErr(COCOA, 3328, "NSPOSIXErrorDomain", 2)


class FakeBridge:
    """The §6.3 bridge with canned answers; ``trash`` moves nothing by default."""

    def __init__(self, facts=LOCAL, trash_dir=("/Users/me/.Trash", None), result=None):
        self.facts_value = facts
        self.trash_dir_value = trash_dir
        self.result = result or (True, None, None)
        self.calls = []

    def facts(self, path):
        self.calls.append(("facts", path))
        return self.facts_value

    def trash_dir(self, path):
        self.calls.append(("trash_dir", path))
        return self.trash_dir_value

    def trash(self, path):
        self.calls.append(("trash", path))
        result = self.result
        return result(path) if callable(result) else result


def _stat(mode=stat.S_IFREG | 0o644, uid=501, flags=0):
    return SimpleNamespace(st_mode=mode, st_uid=uid, st_flags=flags)


class MacBackendTestCase(unittest.TestCase):
    def setUp(self):
        # realpath: the backend resolves paths (/var -> /private/var on macOS, 8.3
        # short names on Windows), so the lstat fakes must use the same spelling.
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="pm-mac-trash-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.root = os.path.join(self.tmp, "output")
        os.makedirs(os.path.join(self.home, ".Trash"))
        os.makedirs(self.root)
        self.file = os.path.join(self.root, "a.png")
        with open(self.file, "wb") as handle:
            handle.write(b"png")
        self.home_trash = os.path.join(self.home, ".Trash")
        self.stats = {}
        self.access_ok = True

    def _lstat(self, path):
        if path in self.stats:
            value = self.stats[path]
            if isinstance(value, Exception):
                raise value
            return value
        return os.lstat(path)

    def backend(self, **bridge_args):
        bridge_args.setdefault("trash_dir", (self.home_trash, None))
        self.bridge = FakeBridge(**bridge_args)
        return MacTrashBackend(
            bridge=self.bridge,
            uid=501,
            home=self.home,
            lstat=self._lstat,
            access=lambda path, mode: self.access_ok,
        )

    def moved_to(self, destination):
        """A ``trash`` result that really renames the file to *destination*."""

        def run(path):
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            os.rename(path, destination)
            return True, destination, None

        return run

    def assert_refused(self, backend, code):
        self.assertEqual(backend.available(self.file, [self.root]), code)
        with self.assertRaises(trash.TrashRefused) as caught:
            backend.move(self.file, [self.root])
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("trash", [name for name, _ in self.bridge.calls])
        self.assertTrue(os.path.exists(self.file))
        return caught.exception


class AvailableOrderTests(MacBackendTestCase):
    def test_a_writable_local_file_is_available(self):
        self.assertIsNone(self.backend().available(self.file, [self.root]))

    def test_network_volume_is_refused_without_trying(self):
        facts = Facts(False, False, False, "/Volumes/nas", False)
        self.assert_refused(self.backend(facts=facts), trash.NETWORK)

    def test_unknown_locality_counts_as_network(self):
        facts = Facts(None, False, True, "/", False)
        self.assert_refused(self.backend(facts=facts), trash.NETWORK)

    def test_read_only_volume_or_unknown_is_no_trash(self):
        for read_only in (True, None):
            with self.subTest(read_only=read_only):
                facts = Facts(True, read_only, False, "/Volumes/ro", False)
                self.assert_refused(self.backend(facts=facts), trash.NO_TRASH)

    def test_ubiquitous_item_is_cloud_sync(self):
        facts = Facts(True, False, True, "/", True)
        self.assert_refused(self.backend(facts=facts), trash.CLOUD_SYNC)

    def test_icloud_and_file_provider_folders_are_cloud_sync(self):
        for parts in (("Library", "Mobile Documents"), ("Library", "CloudStorage")):
            with self.subTest(parts=parts):
                folder = os.path.join(self.home, *parts, "Box")
                os.makedirs(folder)
                path = os.path.join(folder, "b.png")
                with open(path, "wb") as handle:
                    handle.write(b"x")
                backend = self.backend()
                self.assertEqual(backend.available(path, [folder]), trash.CLOUD_SYNC)
                with self.assertRaises(trash.TrashRefused):
                    backend.move(path, [folder])
                self.assertTrue(os.path.exists(path))

    def test_found_trash_folder_inside_an_output_root_is_trash_in_root(self):
        self.assertEqual(
            self.backend().available(self.file, [self.home]), trash.TRASH_IN_ROOT
        )

    def test_trash_dir_error_other_than_missing_is_no_trash(self):
        backend = self.backend(
            trash_dir=(None, NSErr(COCOA, 3328, "NSPOSIXErrorDomain", 30))
        )
        self.assert_refused(backend, trash.NO_TRASH)

    def test_missing_trash_on_the_startup_volume_is_no_trash(self):
        self.assert_refused(
            self.backend(trash_dir=(None, NO_TRASH_DIR)), trash.NO_TRASH
        )


class FreshVolumeTests(MacBackendTestCase):
    def setUp(self):
        super().setUp()
        self.volume = os.path.join(self.tmp, "vol")
        self.root = os.path.join(self.volume, "output")
        os.makedirs(self.root)
        self.file = os.path.join(self.root, "a.png")
        with open(self.file, "wb") as handle:
            handle.write(b"png")
        self.facts = Facts(True, False, False, self.volume, False)

    def test_missing_volume_trash_is_predicted(self):
        backend = self.backend(facts=self.facts, trash_dir=(None, NO_TRASH_DIR))
        self.assertIsNone(backend.available(self.file, [self.root]))

    def test_prediction_refused_when_trashes_is_a_link(self):
        os.symlink(self.tmp, os.path.join(self.volume, ".Trashes"))
        backend = self.backend(facts=self.facts, trash_dir=(None, NO_TRASH_DIR))
        self.assert_refused(backend, trash.NO_TRASH)

    def test_predicted_trash_inside_the_volume_root_is_trash_in_root(self):
        backend = self.backend(facts=self.facts, trash_dir=(None, NO_TRASH_DIR))
        self.assert_refused_root = backend.available(self.file, [self.volume])
        self.assertEqual(self.assert_refused_root, trash.TRASH_IN_ROOT)

    def test_move_to_a_predicted_trash_returns_the_volume_label(self):
        destination = os.path.join(self.volume, ".Trashes", "501", "a.png")
        backend = self.backend(
            facts=self.facts,
            trash_dir=(None, NO_TRASH_DIR),
            result=self.moved_to(destination),
        )
        self.assertEqual(
            backend.move(self.file, [self.root]), ".Trashes/501 on its drive"
        )
        self.assertFalse(os.path.exists(self.file))


class PermissionTests(MacBackendTestCase):
    def test_parent_not_writable_is_no_permission(self):
        self.access_ok = False
        self.assert_refused(self.backend(), trash.NO_PERMISSION)

    def test_immutable_or_append_flags_on_file_or_parent(self):
        for flag in ("UF_IMMUTABLE", "SF_IMMUTABLE", "UF_APPEND", "SF_APPEND"):
            for target in (self.file, self.root):
                with self.subTest(flag=flag, target=target):
                    mode = (
                        stat.S_IFDIR | 0o755
                        if target == self.root
                        else stat.S_IFREG | 0o644
                    )
                    self.stats = {target: _stat(mode=mode, flags=getattr(stat, flag))}
                    self.assert_refused(self.backend(), trash.NO_PERMISSION)

    def test_sticky_parent_and_foreign_owner(self):
        self.stats = {
            self.root: _stat(mode=stat.S_IFDIR | 0o1777),
            self.file: _stat(uid=502),
        }
        self.assert_refused(self.backend(), trash.NO_PERMISSION)

    def test_sticky_parent_with_own_file_is_allowed(self):
        self.stats = {self.root: _stat(mode=stat.S_IFDIR | 0o1777), self.file: _stat()}
        self.assertIsNone(self.backend().available(self.file, [self.root]))


class MoveOutcomeTests(MacBackendTestCase):
    def test_success_into_the_home_trash(self):
        destination = os.path.join(self.home_trash, "a.png")
        backend = self.backend(result=self.moved_to(destination))
        self.assertEqual(backend.move(self.file, [self.root]), trash.HOME_TRASH_LABEL)
        self.assertTrue(os.path.exists(destination))

    def test_cocoa_errors_map_to_codes_with_detail(self):
        cases = {
            3328: trash.NO_TRASH,
            642: trash.NO_TRASH,
            513: trash.NO_PERMISSION,
            257: trash.NO_PERMISSION,
            4: trash.FAILED,
            640: trash.FAILED,
        }
        for code, expected in cases.items():
            with self.subTest(code=code):
                error = NSErr(COCOA, code, "NSOSStatusErrorDomain", -5000)
                backend = self.backend(result=(False, None, error))
                with self.assertRaises(trash.TrashRefused) as caught:
                    backend.move(self.file, [self.root])
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(
                    caught.exception.detail,
                    f"NSCocoaErrorDomain {code} (NSOSStatusErrorDomain -5000)",
                )
                self.assertTrue(os.path.exists(self.file))

    def test_other_domain_or_missing_error_is_failed(self):
        for error in (NSErr("NSPOSIXErrorDomain", 13), None):
            with self.subTest(error=error):
                backend = self.backend(result=(False, None, error))
                with self.assertRaises(trash.TrashRefused) as caught:
                    backend.move(self.file, [self.root])
                self.assertEqual(caught.exception.code, trash.FAILED)

    def test_yes_with_the_source_still_present_is_failed(self):
        backend = self.backend(
            result=(True, os.path.join(self.home_trash, "a.png"), None)
        )
        with self.assertRaises(trash.TrashRefused) as caught:
            backend.move(self.file, [self.root])
        self.assertEqual(caught.exception.code, trash.FAILED)
        self.assertTrue(os.path.exists(self.file))

    def test_yes_with_no_result_path_is_unconfirmed(self):
        def vanished(path):
            os.rename(path, os.path.join(self.tmp, "elsewhere.png"))
            return True, None, None

        backend = self.backend(result=vanished)
        with self.assertRaises(trash.TrashUnconfirmed):
            backend.move(self.file, [self.root])

    def test_yes_into_an_output_root_is_unconfirmed(self):
        backend = self.backend(
            result=self.moved_to(os.path.join(self.root, "x", "a.png"))
        )
        with self.assertRaises(trash.TrashUnconfirmed):
            backend.move(self.file, [self.root])

    def test_yes_into_an_unplanned_folder_is_trashed_and_logged(self):
        other = os.path.join(self.tmp, "other-trash", "a.png")
        backend = self.backend(result=self.moved_to(other))
        with self.assertLogs("prompt_manager.trash", level="WARNING") as logs:
            label = backend.move(self.file, [self.root])
        self.assertEqual(label, os.path.realpath(os.path.dirname(other)))
        self.assertIn(other, "\n".join(logs.output))


class IdentityTests(MacBackendTestCase):
    @unittest.skipUnless(hasattr(os, "symlink") and os.name == "posix", "symlinks")
    def test_root_given_through_a_symlink_still_contains_its_trash(self):
        alias = os.path.join(self.tmp, "home-alias")
        os.symlink(self.home, alias)
        self.assertEqual(
            self.backend().available(self.file, [alias]), trash.TRASH_IN_ROOT
        )

    def test_missing_roots_are_ignored(self):
        missing = os.path.join(self.tmp, "nope")
        self.assertIsNone(self.backend().available(self.file, [missing, self.root]))


class LoadingTests(unittest.TestCase):
    def test_default_backend_for_darwin_does_not_load_the_bridge(self):
        with mock.patch.object(trash_macos, "load_bridge") as loader:
            backend = trash.default_backend("darwin")
        self.assertIsInstance(backend, MacTrashBackend)
        loader.assert_not_called()

    def test_failed_load_answers_no_trash_and_is_cached_and_logged_once(self):
        backend = MacTrashBackend(uid=501)
        with (
            mock.patch.object(
                trash_macos, "load_bridge", side_effect=OSError("no Foundation")
            ) as loader,
            self.assertLogs("prompt_manager.trash", level="WARNING") as logs,
        ):
            self.assertEqual(backend.available("/x/a.png", ["/x"]), trash.NO_TRASH)
            with self.assertRaises(trash.TrashRefused) as caught:
                backend.move("/x/a.png", ["/x"])
        self.assertEqual(caught.exception.code, trash.NO_TRASH)
        self.assertEqual(loader.call_count, 1)
        self.assertEqual(len(logs.output), 1)

    def test_load_bridge_off_macos_raises(self):
        if sys.platform == "darwin":
            self.skipTest("the real bridge loads on macOS (native test covers it)")
        with self.assertRaises(OSError):
            trash_macos.load_bridge()


class ErrorTextTests(unittest.TestCase):
    def test_detail_without_underlying(self):
        self.assertEqual(
            trash_macos.error_detail(NSErr(COCOA, 4)), "NSCocoaErrorDomain 4"
        )
        self.assertEqual(trash_macos.error_detail(None), "no NSError")

    def test_refusal_keeps_detail(self):
        refused = trash.TrashRefused(trash.FAILED, "why")
        self.assertEqual(
            (refused.code, refused.detail, str(refused)), ("failed", "why", "why")
        )
        self.assertEqual(trash.TrashRefused(trash.FAILED).detail, "")


if __name__ == "__main__":
    unittest.main()
