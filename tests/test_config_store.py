"""Tests for the serialized config store (py/config_store.py, #220).

Every test works on a config.json inside its own temporary directory; the
repository's config.json (live user data) is never read or written.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py import config_store  # noqa: E402
from py.config_store import (  # noqa: E402
    ABSENT_REVISION,
    ConfigStore,
    ConfigUnreadable,
    RevisionConflict,
    get_config_store,
    revision_of,
)

try:
    from tests.lock_probe import ProbeLock  # noqa: E402
    from tests.open_handles import assert_closed  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from lock_probe import ProbeLock  # noqa: E402
    from open_handles import assert_closed  # noqa: E402


class ConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = os.path.realpath(tempfile.mkdtemp())
        self.path = os.path.join(self.tmpdir, "config.json")
        self.store = ConfigStore(lambda: self.path)

    def tearDown(self):
        try:
            assert_closed(self, self.tmpdir)
        finally:
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, data):
        Path(self.path).write_text(json.dumps(data), encoding="utf-8")

    def _bytes(self):
        return Path(self.path).read_bytes()

    def _leftovers(self):
        return sorted(name for name in os.listdir(self.tmpdir) if name != "config.json")

    # ── read ──────────────────────────────────────────────────────────

    def test_read_of_a_missing_file_is_empty_and_absent(self):
        self.assertEqual(self.store.read(), ({}, ABSENT_REVISION))

    def test_read_reports_the_sha256_of_the_file_bytes(self):
        self._write({"web_ui": {"result_timeout": 3}})
        data, revision = self.store.read()
        self.assertEqual(data, {"web_ui": {"result_timeout": 3}})
        expected = "sha256:" + hashlib.sha256(self._bytes()).hexdigest()
        self.assertEqual(revision, expected)
        self.assertEqual(revision_of(self._bytes()), expected)

    def test_non_object_config_is_unreadable(self):
        self._write([1, 2])
        with self.assertRaises(ConfigUnreadable):
            self.store.read()

    def test_unreadable_path_is_unreadable(self):
        os.mkdir(self.path)  # a directory where the file should be
        with self.assertRaises(ConfigUnreadable):
            self.store.read()

    # ── commit ────────────────────────────────────────────────────────

    def test_commit_preserves_unknown_sections_and_secret(self):
        self._write(
            {
                "custom_section": {"keep": True},
                "integrations": {"lora_manager": {"civitai_api_key": "secret"}},
                "web_ui": {"result_timeout": 1, "show_test_button": True},
            }
        )

        def mutate(config):
            config.setdefault("web_ui", {})["result_timeout"] = 7
            return config

        saved, revision = self.store.commit(mutate, expected_revision=None)

        on_disk = json.loads(self._bytes())
        self.assertEqual(saved, on_disk)
        self.assertEqual(on_disk["custom_section"], {"keep": True})
        self.assertEqual(
            on_disk["integrations"]["lora_manager"]["civitai_api_key"], "secret"
        )
        self.assertEqual(
            on_disk["web_ui"], {"result_timeout": 7, "show_test_button": True}
        )
        self.assertEqual(revision, revision_of(self._bytes()))
        self.assertEqual(self.store.read(), (on_disk, revision))

    def test_commit_creates_a_missing_file_and_its_parent(self):
        self.path = os.path.join(self.tmpdir, "nested", "config.json")
        saved, revision = self.store.commit(
            lambda config: {**config, "a": 1}, expected_revision=ABSENT_REVISION
        )
        self.assertEqual(saved, {"a": 1})
        self.assertEqual(json.loads(Path(self.path).read_text()), {"a": 1})
        self.assertNotEqual(revision, ABSENT_REVISION)

    def test_mutate_works_on_a_copy(self):
        self._write({"web_ui": {"result_timeout": 1}})
        seen = {}

        def mutate(config):
            seen["config"] = config
            config["web_ui"]["result_timeout"] = 2
            raise RuntimeError("validation failed late")

        before = self._bytes()
        with self.assertRaises(RuntimeError):
            self.store.commit(mutate, expected_revision=None)
        self.assertEqual(self._bytes(), before)
        self.assertEqual(self.store.read()[0], {"web_ui": {"result_timeout": 1}})

    def test_mutate_must_return_an_object(self):
        self._write({"a": 1})
        before = self._bytes()
        with self.assertRaises(TypeError):
            self.store.commit(lambda config: None, expected_revision=None)
        self.assertEqual(self._bytes(), before)

    def test_malformed_existing_config_is_refused_and_untouched(self):
        Path(self.path).write_bytes(b"{not json")
        calls = []
        with self.assertRaises(ConfigUnreadable):
            self.store.commit(
                lambda config: calls.append(config) or config, expected_revision=None
            )
        self.assertEqual(calls, [])
        self.assertEqual(self._bytes(), b"{not json")
        self.assertEqual(self._leftovers(), [])

    def test_write_failure_leaves_bytes_and_raises(self):
        self._write({"a": 1})
        before = self._bytes()
        with patch.object(config_store.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.commit(lambda c: {**c, "b": 2}, expected_revision=None)
        self.assertEqual(self._bytes(), before)
        self.assertEqual(self._leftovers(), [])

    def test_unwritable_directory_raises_oserror(self):
        blocker = os.path.join(self.tmpdir, "afile")
        Path(blocker).write_text("x")
        self.path = os.path.join(blocker, "config.json")  # parent is a file
        with self.assertRaises(OSError):
            self.store.commit(lambda c: {"a": 1}, expected_revision=None)

    def test_stale_revision_conflicts_without_write(self):
        self._write({"a": 1})
        _data, revision = self.store.read()
        self._write({"a": 2})  # another writer got there first
        before = self._bytes()
        calls = []
        with self.assertRaises(RevisionConflict) as caught:
            self.store.commit(
                lambda c: calls.append(c) or c, expected_revision=revision
            )
        self.assertEqual(caught.exception.current, revision_of(before))
        self.assertEqual(calls, [])
        self.assertEqual(self._bytes(), before)

    def test_matching_revision_commits(self):
        self._write({"a": 1})
        _data, revision = self.store.read()
        saved, _ = self.store.commit(
            lambda c: {**c, "b": 2}, expected_revision=revision
        )
        self.assertEqual(saved, {"a": 1, "b": 2})

    def test_temp_file_removed_after_replace_failure(self):
        self._write({"a": 1})
        before = self._bytes()
        with patch.object(
            config_store.os, "replace", side_effect=PermissionError("locked")
        ):
            with self.assertRaises(OSError):
                self.store.commit(lambda c: {**c, "b": 2}, expected_revision=None)
        self.assertEqual(self._bytes(), before)
        self.assertEqual(self._leftovers(), [])

    @unittest.skipUnless(os.name == "posix", "POSIX file modes")
    def test_committed_file_is_private_to_the_user(self):
        import stat

        self._write({"a": 1})
        os.chmod(self.path, 0o644)
        self.store.commit(lambda c: c, expected_revision=None)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def _probe_lock(self):
        probe = ProbeLock()
        patcher = patch.object(config_store, "_COMMIT_LOCK", probe)
        patcher.start()
        self.addCleanup(patcher.stop)
        return probe

    def _hold_lock_in_a_commit(self, value="first"):
        """Start a commit that holds the lock until the returned event is set."""
        inside = threading.Event()
        release = threading.Event()
        errors = []

        def slow(config):
            inside.set()
            release.wait(10)
            config[value] = 1
            return config

        def run():
            try:
                self.store.commit(slow, expected_revision=None)
            except Exception as exc:  # pragma: no cover - reported by the test
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(inside.wait(10))
        return thread, release, errors

    def test_concurrent_commits_serialize(self):
        # The second commit is observed blocked on the lock before the first
        # one may finish, so this fails whenever commit stops taking the lock.
        self._write({"keep": True})
        probe = self._probe_lock()
        first, release, errors = self._hold_lock_in_a_commit()
        calls = []

        def fast(config):
            calls.append(dict(config))
            config["second"] = 2
            return config

        second = threading.Thread(
            target=lambda: self.store.commit(fast, expected_revision=None)
        )
        second.start()
        self.assertTrue(probe.contended.wait(10), "second commit never waited")
        self.assertEqual(calls, [])  # not called while the first holds the lock
        release.set()
        first.join(10)
        second.join(10)

        self.assertEqual(errors, [])
        self.assertEqual(calls, [{"keep": True, "first": 1}])  # re-read inside
        self.assertEqual(
            json.loads(self._bytes()), {"keep": True, "first": 1, "second": 2}
        )

    def test_read_waits_for_a_running_commit(self):
        # Windows cannot replace a file another handle has open, so a read
        # runs under the commit lock and never overlaps a replace.
        self._write({"keep": True})
        probe = self._probe_lock()
        first, release, errors = self._hold_lock_in_a_commit()
        seen = []
        reader = threading.Thread(target=lambda: seen.append(self.store.read()))
        reader.start()
        self.assertTrue(probe.contended.wait(10), "read did not take the lock")
        self.assertEqual(seen, [])
        release.set()
        first.join(10)
        reader.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(seen[0][0], {"keep": True, "first": 1})
        self.assertEqual(seen[0][1], revision_of(self._bytes()))

    def test_replace_is_retried_after_a_transient_permission_error(self):
        # Windows: an antivirus or indexer handle makes os.replace fail with
        # PermissionError for a moment; the commit retries before failing.
        self._write({"a": 1})
        real_replace = os.replace
        attempts = []

        def flaky(src, dst):
            attempts.append(dst)
            if len(attempts) == 1:
                raise PermissionError("in use by another process")
            return real_replace(src, dst)

        with (
            patch.object(config_store, "_REPLACE_ATTEMPTS", 5),
            patch.object(config_store.os, "replace", side_effect=flaky),
            patch.object(config_store.time, "sleep") as sleep,
        ):
            saved, _ = self.store.commit(
                lambda c: {**c, "b": 2}, expected_revision=None
            )
        self.assertEqual(saved, {"a": 1, "b": 2})
        self.assertEqual(json.loads(self._bytes()), {"a": 1, "b": 2})
        self.assertEqual(len(attempts), 2)
        sleep.assert_called_once_with(config_store._REPLACE_BACKOFF_SECONDS)
        self.assertEqual(self._leftovers(), [])

    def test_replace_gives_up_after_the_last_attempt(self):
        self._write({"a": 1})
        before = self._bytes()
        with (
            patch.object(config_store, "_REPLACE_ATTEMPTS", 3),
            patch.object(
                config_store.os, "replace", side_effect=PermissionError("locked")
            ) as replace,
            patch.object(config_store.time, "sleep") as sleep,
        ):
            with self.assertRaises(PermissionError):
                self.store.commit(lambda c: {**c, "b": 2}, expected_revision=None)
        self.assertEqual((replace.call_count, sleep.call_count), (3, 2))
        self.assertEqual(self._bytes(), before)
        self.assertEqual(self._leftovers(), [])

    def test_replace_is_tried_once_outside_windows(self):
        expected = 5 if os.name == "nt" else 1
        self.assertEqual(config_store._REPLACE_ATTEMPTS, expected)

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_symlinked_config_is_written_through_the_link(self):
        # A config.json that is a symlink (or a single-file bind mount) is
        # written beside its real target, so the link stays a link.
        real_dir = os.path.join(self.tmpdir, "real")
        os.mkdir(real_dir)
        target = os.path.join(real_dir, "settings.json")
        Path(target).write_text(json.dumps({"a": 1}), encoding="utf-8")
        try:
            os.symlink(target, self.path)
        except (OSError, NotImplementedError):  # pragma: no cover - Windows
            self.skipTest("symlinks are not permitted here")

        saved, revision = self.store.commit(
            lambda c: {**c, "b": 2}, expected_revision=None
        )

        self.assertTrue(os.path.islink(self.path))
        self.assertEqual(json.loads(Path(target).read_text()), {"a": 1, "b": 2})
        self.assertEqual(self.store.read(), (saved, revision))
        self.assertEqual(os.listdir(real_dir), ["settings.json"])

    def test_publish_runs_under_the_lock_after_the_write(self):
        # Runtimes publish inside the commit, so two saves publish in the
        # order they were written and the runtime ends on the last one.
        self._write({"a": 1})
        seen = []

        # The lock is re-entrant, so another thread probes whether it is held.
        def probe_publish(config):
            result = []
            thread = threading.Thread(
                target=lambda: result.append(
                    config_store._COMMIT_LOCK.acquire(blocking=False)
                )
            )
            thread.start()
            thread.join(10)
            seen.append((config, json.loads(self._bytes()), result[0]))

        saved, _ = self.store.commit(
            lambda c: {**c, "b": 2}, expected_revision=None, publish=probe_publish
        )
        self.assertEqual(seen, [(saved, {"a": 1, "b": 2}, False)])

    def test_publish_is_skipped_when_the_write_fails(self):
        self._write({"a": 1})
        published = []
        with patch.object(config_store.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.commit(
                    lambda c: {**c, "b": 2},
                    expected_revision=None,
                    publish=published.append,
                )
        self.assertEqual(published, [])

    def test_handles_closed_after_commit(self):
        self._write({"a": 1})
        self.store.read()
        self.store.commit(lambda c: {**c, "b": 2}, expected_revision=None)
        assert_closed(self, self.tmpdir)
        os.unlink(self.path)  # Windows refuses this while a handle is open
        self.assertEqual(self._leftovers(), [])


class SingletonTests(unittest.TestCase):
    def test_get_config_store_is_one_process_store_on_the_configured_path(self):
        store = get_config_store()
        self.assertIs(store, get_config_store())
        tmpdir = tempfile.mkdtemp()
        try:
            custom = os.path.join(tmpdir, "c.json")
            with patch.dict(os.environ, {"PROMPT_MANAGER_CONFIG_PATH": custom}):
                self.assertEqual(store.path, custom)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
