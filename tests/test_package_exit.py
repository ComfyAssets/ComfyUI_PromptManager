"""The package teardown runs at a normal interpreter exit (#301, #302).

ComfyUI catches Ctrl+C in ``main.py`` and returns, so its exit is a normal
return: ``atexit`` callbacks then run, newest first. A child process imports
the package with ComfyUI stubbed, leaves the image worker alive in its
processing delay and returns. Its own ``atexit`` reporter, registered before
the package, runs after the teardown and prints the result as one JSON line.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The parent never waits longer than this for the child (the hang watchdog
# allows 120 s per test).
CHILD_CEILING_SECONDS = 60
REPORT_PREFIX = "PM_EXIT_REPORT "

CHILD = textwrap.dedent("""
    import atexit
    import contextlib
    import io
    import json
    import os
    import sys
    import threading

    root, tmp = sys.argv[1], sys.argv[2]
    sys.path.insert(0, root)
    state = {}

    def report():
        package = state.get("package")
        result = getattr(package, "_background_work_result", None)
        alive = sorted(
            t.name
            for t in threading.enumerate()
            if t.name in ("PromptManagerImageWorker", "PromptManagerPromptCleanup")
        )
        print("PM_EXIT_REPORT " + json.dumps({"result": result, "alive": alive}))
        sys.stdout.flush()

    # First registered, so it runs last: after the package's teardown and
    # after the history dispose.
    atexit.register(report)

    from tests.test_init_package import _import_package, fake_server_module

    output = os.path.join(tmp, "output")
    with contextlib.redirect_stdout(io.StringIO()):
        package, _ = _import_package(
            "pm_exit_child",
            os.path.join(tmp, "prompts.db"),
            output,
            fake_server_module(queue_task_done=True),
        )
    state["package"] = package
    monitor = package._global_image_monitor
    handler = monitor.handler if monitor is not None else None
    if handler is None:
        sys.exit(3)
    handler.processing_delay = 3600
    path = os.path.join(tmp, "busy.png")  # outside the watched folder
    with open(path, "wb") as image:
        image.write(b"x" * 16)
    if not handler.schedule(path) or not handler._worker.is_alive():
        sys.exit(4)
    # Return normally with the worker alive, as ComfyUI does after Ctrl+C.
    """)


class TestPackageExit(unittest.TestCase):
    def test_teardown_runs_at_a_normal_exit_with_the_worker_alive(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "output"))
            script = os.path.join(tmp, "child.py")
            with open(script, "w", encoding="utf-8") as handle:
                handle.write(CHILD)
            env = dict(os.environ)
            env.pop("PROMPT_MANAGER_CONFIG_PATH", None)
            env["PYTHONIOENCODING"] = "utf-8"
            completed = subprocess.run(
                [sys.executable, script, ROOT, tmp],
                cwd=ROOT,
                env=env,
                capture_output=True,
                timeout=CHILD_CEILING_SECONDS,
                encoding="utf-8",
                errors="replace",
            )
            # Leaving the block removes the folder: on Windows that fails
            # if the child left a handle open, which it cannot once it exited,
            # so the check here is that the child exited at all.
        detail = f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        self.assertEqual(completed.returncode, 0, detail)
        reports = [
            line[len(REPORT_PREFIX) :]
            for line in completed.stdout.splitlines()
            if line.startswith(REPORT_PREFIX)
        ]
        self.assertEqual(len(reports), 1, detail)
        report = json.loads(reports[0])
        self.assertEqual(
            report["result"],
            {"monitor": True, "thumbnails": True, "tracker": True},
            detail,
        )
        self.assertEqual(report["alive"], [], detail)
        # The teardown's own warnings. Import-time warnings (the API's
        # output-folder search finds no ComfyUI here) are not about exit.
        for text in (
            "Traceback",
            "Background work not stopped in time",
            "still running after",
        ):
            self.assertNotIn(text, completed.stderr, detail)


if __name__ == "__main__":
    unittest.main()
