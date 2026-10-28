"""Work running at Ctrl+C stops at its next safe point (#376).

ComfyUI catches Ctrl+C in ``main.py`` and returns without closing its event
loop. Python then joins every executor thread (``concurrent.futures``'s
``_python_exit``, a ``threading._register_atexit`` callback) before any
``atexit`` callback runs, so a call still running on the default executor
used to keep the process alive until it returned. The package registers its
own ``threading._register_atexit`` hook, which runs before that join: it sets
the exit signal that ``Job.cancelled`` and ``map_parallel`` read, and kills
the encoder processes.

The child-process test starts three long calls on the default executor (a
registry job checking ``job.cancelled`` between files, a ``map_parallel``
batch and an encoder process that ignores the exit, as ffmpeg does on
Windows), returns from its main module and must exit far sooner than any of
them would end.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    from utils import exit_signal  # noqa: E402
except ImportError:  # before #376: only the child-process test can run
    exit_signal = None

# The parent never waits longer than this for the child (the hang watchdog
# allows 120 s per test). Each fake job alone would run 300 s or more.
CHILD_CEILING_SECONDS = 60
# From the child's return to its last atexit callback: the stopped calls end
# at their next file, item or chunk (the encoder is killed).
EXIT_BOUND_SECONDS = 15
JOB_SECONDS = 600
# The fake encoder outlives the ceiling, yet a failing run leaks it only
# briefly (the parent cannot reach the child's own child to kill it).
ENCODER_SECONDS = 90
REPORT_PREFIX = "PM_EXIT_REPORT "

CHILD = textwrap.dedent("""
    import asyncio
    import atexit
    import contextlib
    import importlib
    import io
    import json
    import os
    import sys
    import threading
    import time

    root, tmp = sys.argv[1], sys.argv[2]
    job_seconds, encoder_seconds = float(sys.argv[3]), int(sys.argv[4])
    sys.path.insert(0, root)
    state = {"stopped": {}}

    def report():
        returned = state.get("returned")
        print("PM_EXIT_REPORT " + json.dumps({
            "exit_seconds": None if returned is None else time.monotonic() - returned,
            "stopped": state["stopped"],
        }))
        sys.stdout.flush()

    # Registered first, so it runs after every other atexit callback, and
    # every atexit callback runs after the executor threads were joined.
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
    parallel = importlib.import_module("pm_exit_child.utils.parallel")
    encode = importlib.import_module("pm_exit_child.py.api.thumbnail_encode")
    registry = package.api._jobs
    entered = {name: threading.Event() for name in ("job", "batch", "encoder")}

    def job_work(job):
        # A job's thread work: one "file" at a time, checking its cancel
        # between files the way the thumbnail and import jobs do.
        entered["job"].set()
        deadline = time.monotonic() + job_seconds
        while time.monotonic() < deadline:
            if job.cancelled:
                state["stopped"]["job"] = True
                return
            time.sleep(0.05)

    def batch_item(index):
        entered["batch"].set()
        time.sleep(0.5)
        return index

    def batch_work():
        try:
            parallel.map_parallel(batch_item, range(int(job_seconds)), 2)
        except Exception as error:
            state["stopped"]["batch"] = type(error).__name__

    def encoder_work():
        # Stands for ffmpeg on Windows, which runs without a console and
        # never sees Ctrl+C: only a kill ends it before its timeout.
        cmd = [sys.executable, "-c", "import time; time.sleep(%d)" % encoder_seconds]
        try:
            encode.run_encoder(
                cmd,
                timeout=encoder_seconds,
                track=lambda _process: entered["encoder"].set(),
            )
        except Exception as error:
            state["stopped"]["encoder"] = type(error).__name__

    async def begin():
        loop = asyncio.get_running_loop()

        async def runner(job):
            await loop.run_in_executor(None, job_work, job)

        registry.start("exit-test", "Exit test", admission="read", runner=runner)
        loop.run_in_executor(None, batch_work)
        loop.run_in_executor(None, encoder_work)
        for name, event in entered.items():
            if not await loop.run_in_executor(None, event.wait, 30):
                sys.exit("not started: " + name)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(begin())
    state["returned"] = time.monotonic()
    # Return with the three calls running and the loop open, as ComfyUI does
    # after Ctrl+C.
    """)


class TestExitStopsRunningWork(unittest.TestCase):
    def test_running_executor_work_stops_at_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "output"))
            script = os.path.join(tmp, "child.py")
            with open(script, "w", encoding="utf-8") as handle:
                handle.write(CHILD)
            env = dict(os.environ)
            env.pop("PROMPT_MANAGER_CONFIG_PATH", None)
            env["PROMPT_MANAGER_DATA_DIR"] = tmp
            env["PYTHONIOENCODING"] = "utf-8"
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        script,
                        ROOT,
                        tmp,
                        str(JOB_SECONDS),
                        str(ENCODER_SECONDS),
                    ],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    timeout=CHILD_CEILING_SECONDS,
                    encoding="utf-8",
                    errors="replace",
                )
            except subprocess.TimeoutExpired as hung:
                self.fail(
                    f"the child did not exit within {CHILD_CEILING_SECONDS} s\n"
                    f"stdout:\n{hung.stdout}\nstderr:\n{hung.stderr}"
                )
        detail = f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        self.assertEqual(completed.returncode, 0, detail)
        reports = [
            line[len(REPORT_PREFIX) :]
            for line in completed.stdout.splitlines()
            if line.startswith(REPORT_PREFIX)
        ]
        self.assertEqual(len(reports), 1, detail)
        report = json.loads(reports[0])
        self.assertLess(report["exit_seconds"], EXIT_BOUND_SECONDS, detail)
        self.assertEqual(
            report["stopped"],
            {"job": True, "batch": "ExitRequested", "encoder": "EncoderClosed"},
            detail,
        )
        for text in ("Traceback", "Background work not stopped in time"):
            self.assertNotIn(text, completed.stderr, detail)


class _Isolated(unittest.TestCase):
    """Each test starts with the signal clear and no hook installed."""

    def setUp(self):
        exit_signal.reset()
        self.addCleanup(exit_signal.reset)


class TestInstall(_Isolated):
    def test_the_hook_is_registered_once_before_the_executor_join(self):
        calls = []
        with mock.patch.object(
            exit_signal.threading,
            "_register_atexit",
            side_effect=lambda func: calls.append(func),
            create=True,
        ):
            self.assertTrue(exit_signal.install())
            self.assertTrue(exit_signal.install())
        self.assertEqual(len(calls), 1)
        self.assertFalse(exit_signal.requested())
        calls[0]()
        self.assertTrue(exit_signal.requested())

    def test_a_missing_internal_hook_degrades_to_the_old_exit(self):
        with mock.patch.object(exit_signal, "threading") as fake:
            del fake._register_atexit
            self.assertFalse(exit_signal.install())
        self.assertFalse(exit_signal.requested())

    def test_a_refused_registration_degrades_to_the_old_exit(self):
        refuse = RuntimeError("can't register atexit after shutdown")
        with mock.patch.object(
            exit_signal.threading, "_register_atexit", side_effect=refuse, create=True
        ):
            self.assertFalse(exit_signal.install())

    def test_a_failing_exit_action_never_escapes_the_hook(self):
        # An exception out of a threading atexit callback would abort
        # threading._shutdown and skip the executor join itself.
        calls = []

        def boom():
            raise KeyboardInterrupt  # a second Ctrl+C

        with mock.patch.object(
            exit_signal.threading,
            "_register_atexit",
            side_effect=lambda func: calls.append(func),
            create=True,
        ):
            exit_signal.install(boom)
        with self.assertLogs(exit_signal.logger, "ERROR"):
            calls[0]()
        self.assertTrue(exit_signal.requested())


class TestCheckpoints(_Isolated):
    def test_checked_runs_until_the_signal_then_raises(self):
        seen = []
        record = exit_signal.checked(seen.append)
        record(1)
        exit_signal.request()
        with self.assertRaises(exit_signal.ExitRequested):
            record(2)
        self.assertEqual(seen, [1])

    def test_map_parallel_starts_no_item_after_the_signal(self):
        from utils.parallel import map_parallel

        started = []
        gate = threading.Event()

        def item(index):
            started.append(index)
            if index == 0:
                exit_signal.request()
                gate.set()
            return index

        with self.assertRaises(exit_signal.ExitRequested):
            map_parallel(item, range(50), 1)
        self.assertTrue(gate.is_set())
        self.assertEqual(started, [0])

    def test_job_cancelled_follows_the_signal_and_its_own_cancel(self):
        from py.api.jobs import Job

        job = Job("scan", "Scan", admission="records")
        self.assertFalse(job.cancelled)
        job.cancelled = True
        self.assertTrue(job.cancelled)
        other = Job("scan", "Scan", admission="records", cancellable=False)
        exit_signal.request()
        self.assertTrue(other.cancelled)

    def test_the_scan_walk_stops_between_directories(self):
        from py.api.admin import _collect_media_files

        with tempfile.TemporaryDirectory() as tmp:
            for name in ("a", "b"):
                os.makedirs(os.path.join(tmp, name))
                with open(os.path.join(tmp, name, "x.png"), "wb") as image:
                    image.write(b"x")
            self.assertEqual(len(_collect_media_files([tmp], [".png"])), 2)
            exit_signal.request()
            with self.assertRaises(exit_signal.ExitRequested):
                _collect_media_files([tmp], [".png"])


class TestLoraImportStopsAtExit(_Isolated):
    def test_staging_stops_before_the_next_sidecar_whatever_the_caller_checks(self):
        import types

        from py import lora_import

        discovery = types.SimpleNamespace(
            sidecars=[object(), object()], unreadable=[], layout=None
        )
        exit_signal.request()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(lora_import.ImportCancelled):
                lora_import.stage(
                    discovery,
                    tmp,
                    media_extensions=frozenset(),
                    cancelled=lambda: False,  # the legacy scan's own check
                )


class _FakeTqdm:
    """huggingface_hub.utils.tqdm as far as a download uses it."""

    def __init__(self, *args, **kwargs):
        self.n = 0

    def update(self, n=1):
        self.n += n

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_hub(chunks_before_exit):
    """A ``huggingface_hub`` whose download writes chunks to a temp file and,
    like huggingface_hub 1.x, removes it on any failure before the rename."""
    import types

    def hf_hub_download(
        repo_id, filename, local_dir=None, local_dir_use_symlinks=None, tqdm_class=None
    ):
        final = os.path.join(local_dir, filename)
        partial = final + ".abc.incomplete"
        bar = (tqdm_class or _FakeTqdm)(total=10)
        try:
            with open(partial, "wb") as handle:
                for index in range(10):
                    if index == chunks_before_exit:
                        exit_signal.request()
                    bar.update(1)
                    handle.write(b"x")
            os.replace(partial, final)
        finally:
            if os.path.exists(partial):
                os.unlink(partial)
        return final

    hub = types.ModuleType("huggingface_hub")
    hub.hf_hub_download = hf_hub_download
    hub.snapshot_download = lambda **kwargs: None
    utils = types.ModuleType("huggingface_hub.utils")
    utils.tqdm = _FakeTqdm
    hub.utils = utils
    return {"huggingface_hub": hub, "huggingface_hub.utils": utils}


class TestModelDownloadStopsAtExit(_Isolated):
    def test_a_download_stops_between_chunks_and_leaves_no_file(self):
        from py.autotag import AutoTagService

        with tempfile.TemporaryDirectory() as tmp:
            service = AutoTagService(models_dir=tmp)
            with mock.patch.dict(sys.modules, _fake_hub(chunks_before_exit=3)):
                with self.assertLogs(service.logger, "INFO") as logs:
                    self.assertFalse(service.download_model("wd14-vit"))
            folder = os.path.join(tmp, "WD14-ViT")
            self.assertEqual(os.listdir(folder), [])
        self.assertTrue(any("exiting" in line for line in logs.output), logs.output)
        self.assertFalse(any("Download failed" in line for line in logs.output))

    def test_a_download_without_the_signal_finishes(self):
        from py.autotag import AutoTagService

        with tempfile.TemporaryDirectory() as tmp:
            service = AutoTagService(models_dir=tmp)
            with mock.patch.dict(sys.modules, _fake_hub(chunks_before_exit=None)):
                self.assertTrue(service.download_model("wd14-vit"))
            folder = os.path.join(tmp, "WD14-ViT")
            self.assertEqual(
                sorted(os.listdir(folder)), ["model.onnx", "selected_tags.csv"]
            )

    def test_xet_transfers_are_aborted_the_way_hub_aborts_on_ctrl_c(self):
        import types

        from py.autotag import abort_hub_downloads

        aborted = []
        xet = types.ModuleType("huggingface_hub.utils._xet")
        xet.abort_xet_session = lambda: aborted.append(True)
        hub = _fake_hub(chunks_before_exit=None)
        hub["huggingface_hub.utils._xet"] = xet
        hub["huggingface_hub.utils"]._xet = xet
        with mock.patch.dict(sys.modules, hub):
            abort_hub_downloads()
        self.assertEqual(aborted, [True])
        with mock.patch.dict(sys.modules):
            sys.modules.pop("huggingface_hub", None)
            abort_hub_downloads()  # never imported: nothing to abort
        self.assertEqual(aborted, [True])

    def test_an_aborted_xet_download_is_logged_as_stopped(self):
        from py.autotag import AutoTagService

        hub = _fake_hub(chunks_before_exit=None)

        def aborted(*args, **kwargs):
            exit_signal.request()  # the hook ran, then hub raised
            raise RuntimeError("xet session aborted")

        hub["huggingface_hub"].hf_hub_download = aborted
        with tempfile.TemporaryDirectory() as tmp:
            service = AutoTagService(models_dir=tmp)
            with mock.patch.dict(sys.modules, hub):
                with self.assertLogs(service.logger, "INFO") as logs:
                    self.assertFalse(service.download_model("wd14-vit"))
        self.assertTrue(any("exiting" in line for line in logs.output), logs.output)
        self.assertFalse(any("Download failed" in line for line in logs.output))

    def test_an_old_hub_without_tqdm_class_downloads_as_before(self):
        from py.autotag import AutoTagService

        hub = _fake_hub(chunks_before_exit=None)
        full = hub["huggingface_hub"].hf_hub_download

        def old_download(
            repo_id, filename, local_dir=None, local_dir_use_symlinks=None
        ):
            return full(repo_id, filename, local_dir, local_dir_use_symlinks)

        hub["huggingface_hub"].hf_hub_download = old_download
        with tempfile.TemporaryDirectory() as tmp:
            service = AutoTagService(models_dir=tmp)
            with mock.patch.dict(sys.modules, hub):
                self.assertTrue(service.download_model("wd14-vit"))


if __name__ == "__main__":
    unittest.main()
