"""The page-workflow test server ends with its launcher (#274 review M2).

A Playwright worker that is SIGKILLed never runs its fixture teardown, so the
server it started must notice on its own: with ``--exit-on-stdin-eof`` it shuts
down when the launcher's end of its stdin pipe closes, and still removes its
temp root. This runs on every OS in CI, where Windows is the strict case.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_TIMEOUT_S = 60


class TestBrowserServerLifetime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def start(self, *extra):
        env = {**os.environ, "TMPDIR": self.tmp.name}
        env.update(TEMP=self.tmp.name, TMP=self.tmp.name)
        process = subprocess.Popen(
            [sys.executable, "-m", "tests.browser.server", "--port", "0", *extra],
            cwd=REPO_ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        def stop():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=SERVER_TIMEOUT_S)
            # communicate() would flush a closed stdin, which Python < 3.13 refuses.
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

        self.addCleanup(stop)
        line = process.stdout.readline().decode()
        self.assertTrue(line.startswith("PM_TEST_SERVER_PORT="), line)
        url = f"http://127.0.0.1:{int(line.split('=', 1)[1])}/prompt_manager/test"
        deadline = time.monotonic() + SERVER_TIMEOUT_S
        while True:
            try:
                with urllib.request.urlopen(url, timeout=1) as response:  # nosec B310
                    self.assertEqual(response.status, 200)
                return process
            except OSError:
                self.assertIsNone(process.poll(), "the server exited early")
                self.assertLess(time.monotonic(), deadline, "never ready")
                time.sleep(0.1)

    def roots(self):
        return sorted(path.name for path in Path(self.tmp.name).glob("pm-browser-*"))

    def test_server_exits_and_removes_its_temp_root_when_stdin_closes(self):
        process = self.start("--exit-on-stdin-eof")
        self.assertEqual(len(self.roots()), 1, "the server works in its own temp root")
        process.stdin.close()
        code = process.wait(timeout=SERVER_TIMEOUT_S)
        self.assertEqual(
            code, 0, process.stderr.read().decode(errors="replace")[-4000:]
        )
        self.assertEqual(self.roots(), [], "the temp root and its database are removed")

    def test_server_keeps_running_with_stdin_closed_without_the_flag(self):
        process = self.start()
        process.stdin.close()
        with self.assertRaises(subprocess.TimeoutExpired):
            process.wait(timeout=1)


class TestBrowserServerLoraIsolation(unittest.TestCase):
    """#355: page tests never read the developer's LoraManager settings."""

    def test_settings_folder_is_inside_the_server_root(self):
        from unittest.mock import patch

        from tests.browser.server import isolate_lora_manager_settings

        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {"LORA_MANAGER_SETTINGS_DIR": "/real/one"}):
                folder = isolate_lora_manager_settings(Path(root))
                self.assertEqual(os.environ["LORA_MANAGER_SETTINGS_DIR"], str(folder))
            self.assertEqual(folder.parent, Path(root))
            self.assertTrue(folder.is_dir())


if __name__ == "__main__":
    unittest.main()
