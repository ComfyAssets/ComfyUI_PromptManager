"""The page-workflow test server ends with its launcher (#274 review M2).

A Playwright worker that is SIGKILLed never runs its fixture teardown, so the
server it started must notice on its own: with ``--exit-on-stdin-eof`` it shuts
down when the launcher's end of its stdin pipe closes, and still removes its
temp root. This runs on every OS in CI, where Windows is the strict case.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

try:
    from tests.process_cleanup import remove_when_released
except ImportError:  # discovered with tests/ as the top-level directory
    from process_cleanup import remove_when_released

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_TIMEOUT_S = 60


class BrowserServerCase(unittest.TestCase):
    """Starts the test server in a temp folder of its own; holds no tests."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(remove_when_released, self.tmp)

    def start(self, *extra):
        env = {**os.environ, "TMPDIR": self.tmp}
        env.update(TEMP=self.tmp, TMP=self.tmp)
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
        self.origin = f"http://127.0.0.1:{int(line.split('=', 1)[1])}"
        url = f"{self.origin}/prompt_manager/test"
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
        return sorted(path.name for path in Path(self.tmp).glob("pm-browser-*"))


class TestBrowserServerLifetime(BrowserServerCase):
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


class TestBrowserServerSeeding(BrowserServerCase):
    """The fixtures seed on every OS (#629): Windows roots are not POSIX paths."""

    def reset(self, path, body):
        request = urllib.request.Request(
            f"{self.origin}{path}",
            json.dumps(body).encode(),
            {"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(  # nosec B310
                request, timeout=SERVER_TIMEOUT_S
            ) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read().decode(errors="replace")

    def test_concept_fixture_seeds_its_recorded_runs(self):
        self.start()
        status, body = self.reset("/__test__/concept/reset", {})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["success"])

    def test_history_fixture_seeds_its_recorded_runs(self):
        self.start()
        status, body = self.reset("/__test__/reset", {"chronological_history": True})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["success"])


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
