"""The System browser hooks only ever reset their own server's config.json (#249 S3)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.browser.workspace_system_fixture import register_system_commands


class TestWorkspaceSystemFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "server"
        self.root.mkdir()

    def test_refuses_a_config_path_outside_its_root(self):
        elsewhere = Path(self.tmp.name) / "config.json"
        elsewhere.write_bytes(b'{"real": true}')
        for value in (str(elsewhere), None):
            env = {
                k: v for k, v in os.environ.items() if k != "PROMPT_MANAGER_CONFIG_PATH"
            }
            if value is not None:
                env["PROMPT_MANAGER_CONFIG_PATH"] = value
            with (
                self.subTest(value=value),
                mock.patch.dict(os.environ, env, clear=True),
            ):
                with self.assertRaisesRegex(RuntimeError, "PROMPT_MANAGER_CONFIG_PATH"):
                    register_system_commands(None, None, None, self.root)
        self.assertEqual(elsewhere.read_bytes(), b'{"real": true}')


if __name__ == "__main__":
    unittest.main()
