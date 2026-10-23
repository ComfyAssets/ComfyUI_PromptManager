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
    from tests.open_handles import assert_closed  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
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

    def test_concurrent_commits_serialize(self):
        self._write({"keep": True})
        inside = threading.Event()
        release = threading.Event()
        errors = []

        def slow(config):
            inside.set()
            release.wait(10)  # hold the lock while the second commit waits
            config["first"] = 1
            return config

        def fast(config):
            config["second"] = 2
            return config

        def run(mutate):
            try:
                self.store.commit(mutate, expected_revision=None)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        first = threading.Thread(target=run, args=(slow,))
        first.start()
        self.assertTrue(inside.wait(10))
        second = threading.Thread(target=run, args=(fast,))
        second.start()
        release.set()
        first.join(10)
        second.join(10)

        self.assertEqual(errors, [])
        self.assertEqual(
            json.loads(self._bytes()), {"keep": True, "first": 1, "second": 2}
        )

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
