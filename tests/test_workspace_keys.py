"""The per-install workspace secret (#430, utils/workspace_keys.py).

A ComfyUI restart must not change the gallery root scopes or the workspace
cursors while the output roots stay the same, so the secret that keys them is
persisted beside the database instead of drawn per process.
"""

import importlib
import json
import logging
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.workspace_keys as workspace_keys  # noqa: E402
from utils.workspace_keys import (  # noqa: E402
    KEY_FILE_NAME,
    WorkspaceKeys,
    gallery_root_scope,
    load_install_secret,
    workspace_keys_for,
)

REPO = Path(__file__).resolve().parents[1]


class TempFolderCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.folder = Path(os.path.realpath(folder.name))
        self.key_file = self.folder / KEY_FILE_NAME

    def leftovers(self):
        return sorted(p.name for p in self.folder.iterdir() if p.name != KEY_FILE_NAME)


class TestLoadInstallSecret(TempFolderCase):
    def test_a_missing_file_is_created_with_64_hex_and_owner_only_mode(self):
        secret = load_install_secret(str(self.folder))
        self.assertEqual(len(secret), 32)
        text = self.key_file.read_text(encoding="ascii")
        self.assertEqual(text, secret.hex() + "\n")
        self.assertEqual(self.leftovers(), [], "no temp file is left behind")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.key_file.stat().st_mode) & 0o077, 0)

    def test_an_existing_file_is_reused(self):
        first = load_install_secret(str(self.folder))
        self.assertEqual(load_install_secret(str(self.folder)), first)

    def test_an_uppercase_or_unterminated_file_is_still_read(self):
        self.key_file.write_text("AB" * 32, encoding="ascii")
        self.assertEqual(
            load_install_secret(str(self.folder)), bytes.fromhex("ab" * 32)
        )

    def test_a_corrupt_file_is_replaced_with_a_warning(self):
        for content in (b"", b"short", b"zz" * 32, b"ab" * 33, b"\xff" * 64):
            with self.subTest(content=content):
                self.key_file.write_bytes(content)
                with self.assertLogs("prompt_manager.api", logging.WARNING) as logs:
                    secret = load_install_secret(str(self.folder))
                self.assertEqual(len(secret), 32)
                self.assertEqual(
                    self.key_file.read_text(encoding="ascii"), secret.hex() + "\n"
                )
                self.assertIn("replaced", "\n".join(logs.output))
                self.assertNotIn(secret.hex(), "\n".join(logs.output))
                self.assertEqual(self.leftovers(), [])

    def test_a_lost_creation_race_reads_the_winners_file(self):
        winner = "cd" * 32 + "\n"
        real_link = os.link

        def racing_link(source, target, *args, **kwargs):
            Path(target).write_text(winner, encoding="ascii")
            return real_link(source, target, *args, **kwargs)  # FileExistsError

        with patch.object(workspace_keys.os, "link", racing_link):
            secret = load_install_secret(str(self.folder))
        self.assertEqual(secret, bytes.fromhex("cd" * 32))
        self.assertEqual(self.key_file.read_text(encoding="ascii"), winner)
        self.assertEqual(self.leftovers(), [])

    def test_a_file_system_without_hard_links_falls_back_to_replace(self):
        def no_link(*args, **kwargs):
            raise OSError(1, "Operation not permitted")

        with patch.object(workspace_keys.os, "link", no_link):
            secret = load_install_secret(str(self.folder))
        self.assertEqual(self.key_file.read_text(encoding="ascii"), secret.hex() + "\n")
        self.assertEqual(self.leftovers(), [])

    def test_an_unwritable_folder_returns_none_and_leaves_nothing(self):
        def refuse(*args, **kwargs):
            raise PermissionError(13, "Permission denied")

        with patch.object(workspace_keys.os, "open", refuse):
            with self.assertLogs("prompt_manager.api", logging.WARNING):
                self.assertIsNone(load_install_secret(str(self.folder)))
        self.assertFalse(self.key_file.exists())
        with self.assertLogs("prompt_manager.api", logging.WARNING):
            self.assertIsNone(load_install_secret(str(self.folder / "missing")))

    def test_an_unreadable_key_path_returns_none(self):
        self.key_file.mkdir()  # a folder where the file should be
        with self.assertLogs("prompt_manager.api", logging.WARNING):
            self.assertIsNone(load_install_secret(str(self.folder)))

    def test_a_refused_replace_of_a_corrupt_key_falls_back(self):
        # Windows refuses os.replace while another process holds the file open.
        self.key_file.write_text("broken", encoding="ascii")

        def refuse(*args, **kwargs):
            raise PermissionError(13, "Access is denied")

        with patch.object(workspace_keys.os, "replace", refuse):
            with self.assertLogs("prompt_manager.api", logging.WARNING):
                self.assertIsNone(load_install_secret(str(self.folder)))
        self.assertEqual(self.key_file.read_text(encoding="ascii"), "broken")
        self.assertEqual(self.leftovers(), [])

    def test_a_lost_race_against_an_unfinished_file_is_unusable_not_replaced(self):
        def racing_link(source, target, *args, **kwargs):
            Path(target).write_text("ab", encoding="ascii")  # still being written
            raise FileExistsError(target)

        with patch.object(workspace_keys.os, "link", racing_link):
            with self.assertLogs("prompt_manager.api", logging.WARNING):
                self.assertIsNone(load_install_secret(str(self.folder)))
        self.assertEqual(self.key_file.read_text(encoding="ascii"), "ab")

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits")
    def test_an_existing_key_readable_by_others_is_tightened(self):
        secret = load_install_secret(str(self.folder))
        os.chmod(self.key_file, 0o644)
        self.assertEqual(load_install_secret(str(self.folder)), secret)
        self.assertEqual(stat.S_IMODE(self.key_file.stat().st_mode), 0o600)

    def test_old_temp_files_are_swept_and_fresh_ones_kept(self):
        old = self.folder / f".{KEY_FILE_NAME}.dead.tmp"
        fresh = self.folder / f".{KEY_FILE_NAME}.live.tmp"
        other = self.folder / "prompts.db"
        for path in (old, fresh, other):
            path.write_text("x")
        os.utime(old, (1, 1))
        load_install_secret(str(self.folder))
        self.assertEqual(self.leftovers(), sorted([fresh.name, other.name]))


class TestWorkspaceKeys(TempFolderCase):
    def test_purposes_are_domain_separated_and_stable(self):
        keys = workspace_keys_for(str(self.folder / "prompts.db"))
        self.assertTrue(keys.persisted)
        self.assertNotEqual(keys.root_scope, keys.cursor)
        again = workspace_keys_for(str(self.folder / "prompts.db"))
        self.assertEqual(
            (again.root_scope, again.cursor), (keys.root_scope, keys.cursor)
        )
        self.assertNotIn(
            self.key_file.read_text().strip(),
            (keys.root_scope.hex(), keys.cursor.hex()),
        )

    def test_another_database_folder_gets_other_keys(self):
        other = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(other.cleanup)
        first = workspace_keys_for(str(self.folder / "prompts.db"))
        second = workspace_keys_for(os.path.join(other.name, "prompts.db"))
        self.assertNotEqual(first.root_scope, second.root_scope)

    def test_an_unusable_folder_falls_back_to_a_process_key_with_a_warning(self):
        with patch.object(workspace_keys, "load_install_secret", return_value=None):
            with self.assertLogs("prompt_manager.api", logging.WARNING) as logs:
                keys = workspace_keys_for(str(self.folder / "prompts.db"))
        self.assertFalse(keys.persisted)
        self.assertEqual(len(keys.root_scope), 32)
        self.assertIn("restart", "\n".join(logs.output))

    def test_memory_and_blank_paths_never_write_into_the_working_folder(self):
        for path in (":memory:", "", None):
            with self.subTest(path=path):
                with patch.object(workspace_keys, "load_install_secret") as load:
                    with self.assertLogs("prompt_manager.api", logging.WARNING):
                        keys = workspace_keys_for(path)
                load.assert_not_called()
                self.assertFalse(keys.persisted)

    def test_the_scope_is_the_historic_payload_under_the_root_scope_key(self):
        import hashlib
        import hmac

        keys = WorkspaceKeys(b"r" * 32, b"c" * 32, True)
        payload = json.dumps(
            ["prompt-manager-gallery-root-v1", 3, "/x/ü"],
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        self.assertEqual(
            gallery_root_scope(keys, "/x/ü", 3),
            hmac.new(b"r" * 32, payload, hashlib.sha256).hexdigest(),
        )


class TestRestartKeepsScopes(TempFolderCase):
    """A new process or a reloaded module derives the same scope (#430)."""

    def test_a_module_reload_keeps_the_scope(self):
        db = str(self.folder / "prompts.db")
        before = gallery_root_scope(workspace_keys_for(db), "/out", 0)
        reloaded = importlib.reload(workspace_keys)
        self.addCleanup(importlib.reload, workspace_keys)
        self.assertEqual(
            reloaded.gallery_root_scope(reloaded.workspace_keys_for(db), "/out", 0),
            before,
        )

    def test_a_new_process_derives_the_same_scope(self):
        db = str(self.folder / "prompts.db")
        before = gallery_root_scope(workspace_keys_for(db), "/out", 0)
        code = (
            "import sys\n"
            "from utils.workspace_keys import gallery_root_scope, workspace_keys_for\n"
            "print(gallery_root_scope(workspace_keys_for(sys.argv[1]), '/out', 0))\n"
        )
        # -I would drop the repo from sys.path; the child imports only stdlib code.
        result = subprocess.run(
            [sys.executable, "-c", code, db],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), before)


if __name__ == "__main__":
    unittest.main()
