"""System › Sources & scans backend (#249 S4 Task 9).

``GET /prompt_manager/settings`` reports the configured, effective and
watched sources and the monitoring setting; ``POST`` saves
``monitoring_enabled`` (a restart applies it) and refuses a source change
while a record-changing job runs; ``POST /prompt_manager/settings/validate-source``
checks one path and writes nothing. No response carries an absolute path.
"""

import asyncio
import functools
import json
import ntpath
import os
import sys
import types
import unittest
from contextlib import contextmanager
from unittest.mock import patch

try:
    from tests.test_api_admin import AdminAPITestCase
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_admin import AdminAPITestCase

from py import config as config_module  # noqa: E402
from py.api import settings_routes, sources  # noqa: E402
from py.config import GalleryConfig  # noqa: E402

VALIDATE = "/prompt_manager/settings/validate-source"
SETTINGS = "/prompt_manager/settings"


@contextmanager
def no_filesystem_access_for(marker, test):
    """Fail ``test`` if anything resolves, stats or opens a path holding ``marker``.

    Trusted directories (ComfyUI's, the anchors) may still be resolved.
    """
    real = {
        "realpath": os.path.realpath,
        "stat": os.stat,
        "lstat": os.lstat,
    }

    def guard(name):
        def call(path, *args, **kwargs):
            if isinstance(path, (str, bytes)) and marker in os.fsdecode(path):
                test.fail(f"{name}({path!r}) touched an outside path")
            return real[name](path, *args, **kwargs)

        return call

    with (
        patch("os.path.realpath", guard("realpath")),
        patch("os.stat", guard("stat")),
        patch("os.lstat", guard("lstat")),
    ):
        yield


class FakeMonitor:
    """The image monitor as get_settings sees it: status and scheduled roots."""

    def __init__(self, scheduled, alive=True):
        self.monitored_directories = [str(path) for path in scheduled]
        self.alive = alive

    def get_status(self):
        return {
            "running": True,
            "monitored_directories": self.monitored_directories,
            "handler_active": True,
            "observer_alive": self.alive,
        }


class SourcesTestCase(AdminAPITestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.a = self.output_dir / "a"
        self.b = self.output_dir / "b"
        self.a.mkdir()
        self.b.mkdir()
        enabled = GalleryConfig.MONITORING_ENABLED
        self.addCleanup(setattr, GalleryConfig, "MONITORING_ENABLED", enabled)
        GalleryConfig.MONITORING_ENABLED = True
        self._install_monitor(None)

    def _install_monitor(self, monitor):
        import utils.image_monitor as im_mod

        orig = im_mod._monitor_instance
        im_mod._monitor_instance = monitor
        self.addCleanup(setattr, im_mod, "_monitor_instance", orig)

    async def _get(self):
        resp = await self.client.request("GET", "/prompt_manager/settings")
        body = await resp.text()
        self.assertEqual(resp.status, 200, body)
        self.assertNotIn(self.tmpdir, body)
        return json.loads(body)

    async def _post(self, url, payload):
        resp = await self.client.request("POST", url, json=payload)
        body = await resp.text()
        self.assertNotIn(self.tmpdir, body)
        return resp.status, json.loads(body)

    def _config_bytes(self):
        if not os.path.exists(self.config_path):
            return None
        with open(self.config_path, "rb") as f:
            return f.read()

    def _states(self, data):
        return {row["path"]: row["watch"] for row in data["sources"]["effective"]}


class TestSourceStatus(SourcesTestCase):
    async def test_sources_list_configured_effective_and_auto_detected(self):
        missing = self.output_dir / "gone"
        missing.mkdir()
        GalleryConfig.MONITORING_DIRECTORIES = [
            self._root(self.a),
            self._root(missing),
        ]
        missing.rmdir()  # an unmounted drive: configured, no longer a directory

        data = await self._get()

        self.assertEqual(
            data["sources"]["configured"],
            [
                {
                    "label": "a",
                    "path": "output/a",
                    "valid": True,
                    "code": None,
                    "reason": None,
                },
                {
                    "label": "gone",
                    "path": "output/gone",
                    "valid": False,
                    "code": "not-found",
                    "reason": "Gallery root does not exist",
                },
            ],
        )
        self.assertEqual(
            data["sources"]["effective"],
            [
                {
                    "label": "a",
                    "path": "output/a",
                    "root_index": 0,
                    "watch": "manual",
                    "in_comfyui": True,
                }
            ],
        )
        self.assertFalse(data["sources"]["auto_detected"])
        # The legacy keys are unchanged.
        self.assertEqual(
            data["settings"]["gallery_root_paths"], ["output/a", "output/gone"]
        )

    async def test_empty_list_means_auto_detect(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.a)]
        self._write_config({"gallery": {"monitoring": {"directories": ["x"]}}})
        # Detection (the upward search for a ComfyUI tree) finds the output.
        self.api._find_comfyui_output_dir = lambda: str(self.output_dir)

        status, saved = await self._post(
            "/prompt_manager/settings", {"gallery_root_paths": []}
        )

        self.assertEqual(status, 200, saved)
        self.assertEqual(
            self._read_config()["gallery"]["monitoring"]["directories"], []
        )
        data = await self._get()
        self.assertEqual(data["sources"]["configured"], [])
        self.assertTrue(data["sources"]["auto_detected"])
        self.assertEqual(
            data["sources"]["effective"],
            [
                {
                    "label": "ComfyUI output",
                    "path": "output",
                    "root_index": 0,
                    "watch": "manual",
                    "in_comfyui": True,
                }
            ],
        )

    def test_a_source_outside_comfyui_is_not_marked_in_comfyui(self):
        # Sources & scans prefixes "ComfyUI/" only to a path relative to the
        # ComfyUI base; a folder outside it is shown by its own name.
        from py.api import sources

        self.assertTrue(sources.in_comfyui_base(str(self.a)))
        self.assertTrue(sources.in_comfyui_base(str(self.comfy_dir)))
        self.assertFalse(sources.in_comfyui_base(self.tmpdir))
        self.assertFalse(sources.in_comfyui_base(os.path.join(self.tmpdir, "Renders")))

    async def test_watching_requires_live_observer_with_root(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.a), self._root(self.b)]

        # No monitor in this process: nothing is watched, a restart cannot help.
        data = await self._get()
        self.assertEqual(
            data["monitor"],
            {"present": False, "observer_alive": False, "scheduled": []},
        )
        self.assertEqual(
            self._states(data), {"output/a": "manual", "output/b": "manual"}
        )

        # A dead observer watches nothing, even the roots it scheduled.
        self._install_monitor(FakeMonitor([self.a], alive=False))
        data = await self._get()
        self.assertEqual(
            data["monitor"],
            {"present": True, "observer_alive": False, "scheduled": ["output/a"]},
        )
        self.assertEqual(
            self._states(data),
            {"output/a": "restart_pending", "output/b": "restart_pending"},
        )

        # A live observer watches its scheduled root; a root configured since
        # it started waits for a restart.
        self._install_monitor(FakeMonitor([self.a]))
        data = await self._get()
        self.assertTrue(data["monitor"]["observer_alive"])
        self.assertEqual(
            self._states(data), {"output/a": "watching", "output/b": "restart_pending"}
        )

        # Monitoring switched off: the live observer still watches until the
        # restart ("stopping"), and nothing else is pending.
        GalleryConfig.MONITORING_ENABLED = False
        data = await self._get()
        self.assertFalse(data["settings"]["monitoring_enabled"])
        self.assertEqual(
            self._states(data), {"output/a": "stopping", "output/b": "manual"}
        )

    async def test_scheduled_root_matches_through_its_spelling(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.a)]
        spelled = os.path.join(str(self.output_dir), "b", "..", "a")
        self._install_monitor(FakeMonitor([spelled]))

        data = await self._get()

        self.assertEqual(self._states(data), {"output/a": "watching"})
        self.assertEqual(data["monitor"]["scheduled"], ["output/a"])

    async def test_status_failure_is_500_without_paths(self):
        def boom(*args, **kwargs):
            raise OSError(2, "No such file", f"{self.tmpdir}/secret")

        with patch.object(sources, "describe_sources", boom):
            resp = await self.client.request("GET", "/prompt_manager/settings")
        self.assertEqual(resp.status, 500)
        self.assertNotIn(self.tmpdir, await resp.text())


class TestMonitoringSetting(SourcesTestCase):
    async def test_monitoring_enabled_strict_bool_and_restart_flag(self):
        self._write_config({"custom": {"keep": True}})
        before = self._config_bytes()
        for value in ("false", 0, 1, None, [], {"on": True}):
            with self.subTest(value=value):
                status, data = await self._post(
                    "/prompt_manager/settings", {"monitoring_enabled": value}
                )
                self.assertEqual(status, 400, data)
                self.assertFalse(data["success"])
                self.assertEqual(self._config_bytes(), before)
                self.assertTrue(GalleryConfig.MONITORING_ENABLED)

        self._install_monitor(FakeMonitor([self.output_dir]))
        status, data = await self._post(
            "/prompt_manager/settings", {"monitoring_enabled": False}
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["persisted"])
        self.assertTrue(data["restart_required"])
        self.assertEqual(data["restart_required_fields"], ["monitoring_enabled"])
        saved = self._read_config()
        self.assertIs(saved["gallery"]["monitoring"]["enabled"], False)
        self.assertEqual(saved["custom"], {"keep": True})
        self.assertFalse(GalleryConfig.MONITORING_ENABLED)
        self.assertFalse((await self._get())["settings"]["monitoring_enabled"])

        # Back on while the observer still runs: nothing waits for a restart.
        status, data = await self._post(
            "/prompt_manager/settings", {"monitoring_enabled": True}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["restart_required_fields"], [])
        self.assertIs(self._read_config()["gallery"]["monitoring"]["enabled"], True)

    async def test_enabling_without_a_live_observer_needs_a_restart(self):
        GalleryConfig.MONITORING_ENABLED = False
        status, data = await self._post(
            "/prompt_manager/settings", {"monitoring_enabled": True}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["restart_required_fields"], ["monitoring_enabled"])
        self.assertTrue(GalleryConfig.MONITORING_ENABLED)

    def test_config_file_enabled_must_be_a_bool(self):
        GalleryConfig.MONITORING_ENABLED = True
        with self.assertLogs("prompt_manager.config", level="WARNING"):
            GalleryConfig.update_config({"monitoring": {"enabled": "false"}})
        self.assertIs(GalleryConfig.MONITORING_ENABLED, True)
        GalleryConfig.update_config({"monitoring": {"enabled": False}})
        self.assertIs(GalleryConfig.MONITORING_ENABLED, False)


class TestSaveForeignRunsSetting(SourcesTestCase):
    """#247 D-Q4: "Save prompts from other workflows", applied live."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        saved = GalleryConfig.SAVE_FOREIGN_RUNS
        self.addCleanup(setattr, GalleryConfig, "SAVE_FOREIGN_RUNS", saved)
        GalleryConfig.SAVE_FOREIGN_RUNS = True

    async def test_read_and_write_published_live_without_a_restart(self):
        self.assertIs((await self._get())["settings"]["save_foreign_runs"], True)
        self._install_monitor(FakeMonitor([self.output_dir]))
        status, data = await self._post(
            "/prompt_manager/settings", {"save_foreign_runs": False}
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["persisted"])
        self.assertEqual(data["restart_required_fields"], [])
        saved = self._read_config()
        self.assertIs(saved["gallery"]["monitoring"]["save_foreign_runs"], False)
        self.assertIs(GalleryConfig.SAVE_FOREIGN_RUNS, False)
        self.assertIs((await self._get())["settings"]["save_foreign_runs"], False)

    async def test_only_true_or_false_is_accepted(self):
        self._write_config({"custom": {"keep": True}})
        before = self._config_bytes()
        for value in ("false", 0, None, []):
            with self.subTest(value=value):
                status, data = await self._post(
                    "/prompt_manager/settings", {"save_foreign_runs": value}
                )
                self.assertEqual(status, 400, data)
                self.assertIn("save_foreign_runs must be true or false", data["error"])
                self.assertEqual(self._config_bytes(), before)
                self.assertIs(GalleryConfig.SAVE_FOREIGN_RUNS, True)


class TestSourceChangeHold(SourcesTestCase):
    async def test_root_save_refused_while_scan_job_runs(self):
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.a)]
        self._write_config({"gallery": {"monitoring": {"directories": ["kept"]}}})
        before = self._config_bytes()
        gate = asyncio.Event()

        async def runner(job):
            await gate.wait()

        scan = self.api._jobs.start(
            "scan", "Scan for metadata", record_changing=True, runner=runner
        )
        try:
            status, data = await self._post(
                "/prompt_manager/settings",
                {"gallery_root_paths": [str(self.b)]},
            )
            self.assertEqual(status, 409, data)
            self.assertEqual(data["code"], "busy")
            self.assertEqual(
                data["running"], {"id": scan.id, "title": "Scan for metadata"}
            )
            self.assertEqual(self._config_bytes(), before)
            self.assertEqual(GalleryConfig.MONITORING_DIRECTORIES, [self._root(self.a)])

            # A save without sources, or one resending the current sources
            # (the legacy page always does), is not a source change.
            status, data = await self._post(
                "/prompt_manager/settings", {"result_timeout": 9}
            )
            self.assertEqual(status, 200, data)
            status, data = await self._post(
                "/prompt_manager/settings",
                {"result_timeout": 8, "gallery_root_paths": [str(self.a)]},
            )
            self.assertEqual(status, 200, data)
        finally:
            gate.set()
            await asyncio.wait_for(scan.task, 5)

    async def test_root_save_runs_as_a_source_change_hold(self):
        seen = []
        original = settings_routes.get_config_store

        def store():
            seen.extend(
                (job.kind, job.title, job.admission, job.cancellable)
                for job in self.api._jobs.list()
                if job.running
            )
            return original()

        with patch.object(settings_routes, "get_config_store", store):
            status, data = await self._post(
                "/prompt_manager/settings", {"gallery_root_paths": [str(self.b)]}
            )
        self.assertEqual(status, 200, data)
        self.assertIn(("source_change", "Source change", "records", False), seen)
        self.assertFalse(any(job.running for job in self.api._jobs.list()))

    async def test_failed_root_save_ends_the_hold_failed(self):
        os.makedirs(self.config_path)  # config.json is a directory: unreadable
        status, data = await self._post(
            "/prompt_manager/settings", {"gallery_root_paths": [str(self.b)]}
        )
        self.assertGreaterEqual(status, 400, data)
        held = [j for j in self.api._jobs.list() if j.kind == "source_change"]
        self.assertEqual([job.state for job in held], ["failed"])

    async def test_mixed_case_windows_paths_dedupe(self):
        """Two spellings of one Windows folder are saved once (normcase)."""
        with (
            patch.object(sources, "source_problem", return_value=(None, "")),
            patch.object(
                GalleryConfig, "resolve_gallery_root", side_effect=lambda path: path
            ),
            patch.object(settings_routes, "_source_identity", ntpath.normcase),
            patch.object(self.api, "_apply_gallery_roots", return_value=True),
        ):
            roots, error = self.api._parse_gallery_roots(
                {
                    "gallery_root_paths": [
                        "C:\\ComfyUI\\Output\\Renders",
                        "c:/comfyui/output/renders",
                        "C:\\ComfyUI\\output\\other",
                    ]
                }
            )
        self.assertIsNone(error)
        self.assertEqual(
            roots, ["C:\\ComfyUI\\Output\\Renders", "C:\\ComfyUI\\output\\other"]
        )

    async def test_case_variant_of_one_folder_is_saved_once_natively(self):
        variant = str(self.a)[:-1] + "A"
        if not os.path.isdir(variant):
            self.skipTest("case-sensitive filesystem")
        status, data = await self._post(
            "/prompt_manager/settings", {"gallery_root_paths": [str(self.a), variant]}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(len(GalleryConfig.MONITORING_DIRECTORIES), 1)


class TestValidateSource(SourcesTestCase):
    async def test_validate_source_writes_nothing(self):
        self._write_config({"gallery": {"monitoring": {"directories": []}}})
        before = self._config_bytes()
        roots = list(GalleryConfig.MONITORING_DIRECTORIES)

        status, data = await self._post(VALIDATE, {"path": str(self.a)})

        self.assertEqual(status, 200, data)
        self.assertEqual(
            data,
            {
                "success": True,
                "valid": True,
                "label": "a",
                "path": "output/a",
                "code": None,
                "reason": None,
            },
        )
        self.assertEqual(self._config_bytes(), before)
        self.assertEqual(GalleryConfig.MONITORING_DIRECTORIES, roots)
        self.assertEqual(self.api._jobs.list(), [])

    async def test_relative_path_resolves_against_comfyui(self):
        status, data = await self._post(VALIDATE, {"path": "output/b"})
        self.assertEqual((status, data["valid"], data["path"]), (200, True, "output/b"))
        status, data = await self._post(VALIDATE, {"path": "output"})
        self.assertEqual(data["label"], "ComfyUI output")

    async def test_invalid_sources_answer_a_code(self):
        a_file = self.output_dir / "file.png"
        a_file.write_bytes(b"x")
        cases = {
            "": "empty",
            "   ": "empty",
            str(self.output_dir / "missing"): "not-found",
            str(a_file): "not-directory",
            str(self.outside_dir): "outside-allowed",
            os.path.abspath(os.sep): "outside-allowed",
            os.path.expanduser("~"): "outside-allowed",
            os.path.join(str(self.output_dir), "b", "..", "a"): "traversal",
            "output/../output/a": "traversal",
            str(self.a) + "\x00": "invalid-form",
            "\\\\?\\C:\\ComfyUI\\output": "invalid-form",
            "x" * 5000: "invalid-form",
        }
        if os.name != "nt":
            cases["C:\\ComfyUI\\output"] = "invalid-form"
        for path, code in cases.items():
            with self.subTest(code=code, path=path[:40]):
                status, data = await self._post(VALIDATE, {"path": path})
                self.assertEqual(status, 200, data)
                self.assertFalse(data["valid"])
                self.assertEqual(data["code"], code)
                self.assertIsInstance(data["reason"], str)
                self.assertIsNone(data["label"])
                self.assertIsNone(data["path"])
                self.assertNotIn(os.path.expanduser("~"), json.dumps(data))

    async def test_outside_paths_answer_alike_whether_they_exist(self):
        """No existence oracle: outside the allowed folders every path looks
        the same, existing or not, file or folder."""
        a_file = self.outside_dir / "file.txt"
        a_file.write_bytes(b"x")
        answers = []
        for path in (
            self.outside_dir,
            self.outside_dir / "missing",
            a_file,
            os.path.join(self.tmpdir, "no", "such", "tree"),
            os.path.abspath(os.sep),
            os.path.expanduser("~"),
        ):
            status, data = await self._post(VALIDATE, {"path": str(path)})
            answers.append((status, data))
        self.assertEqual(answers[0][1]["code"], "outside-allowed")
        self.assertEqual([answer for answer in answers], [answers[0]] * len(answers))

    async def test_settings_save_refuses_outside_paths_alike(self):
        """The legacy save answers every outside path alike too: the
        filesystem root, the home directory and a link to it included."""
        (self.outside_dir / "x").mkdir()
        link = os.path.join(self.tmpdir, "link-home")
        try:
            os.symlink(os.path.expanduser("~"), link, target_is_directory=True)
        except (OSError, NotImplementedError):
            link = None
        paths = [
            str(self.outside_dir / "x"),
            self.tmpdir + "/nothing/x",
            os.path.abspath(os.sep),
            os.path.expanduser("~"),
        ]
        if link:
            paths.append(link)
        answers = []
        for path in paths:
            resp = await self.client.request(
                "POST", SETTINGS, json={"gallery_root_paths": [path]}
            )
            body = await resp.text()
            self.assertNotIn(os.path.expanduser("~"), body)
            data = json.loads(body)
            # Only the name the caller sent may differ.
            answers.append(
                (
                    resp.status,
                    {k: v for k, v in data.items() if k not in ("source", "error")},
                )
            )
            self.assertEqual(
                data["error"],
                f"{data['source']} can't be used: "
                f"{config_module.OUTSIDE_ALLOWED_REASON}. Remove it, then save.",
            )
        self.assertEqual(
            answers[0],
            (
                400,
                {
                    "success": False,
                    "code": "invalid-source",
                    "index": 0,
                    "reason": config_module.OUTSIDE_ALLOWED_REASON,
                },
            ),
        )
        self.assertEqual(answers, [answers[0]] * len(answers))
        self.assertEqual(GalleryConfig.MONITORING_DIRECTORIES, [])

    def test_every_caller_folds_root_and_home_alike(self):
        for path in (os.path.abspath(os.sep), os.path.expanduser("~")):
            with self.subTest(path=path):
                self.assertEqual(
                    GalleryConfig.gallery_root_problem(path),
                    ("outside-allowed", config_module.OUTSIDE_ALLOWED_REASON),
                )
                self.assertEqual(
                    GalleryConfig.validate_gallery_root(path),
                    (False, config_module.OUTSIDE_ALLOWED_REASON),
                )
                self.assertEqual(
                    sources.validate_source(path)["code"], "outside-allowed"
                )

    async def test_outside_path_is_never_resolved_or_stat(self):
        """Containment is decided as text first: no realpath, stat or lstat of
        an outside path, existing or not, by any caller (I2)."""
        probe = self.outside_dir / "probe-x"
        probe.mkdir()
        (probe / "deep").mkdir()
        cases = [str(probe), str(probe / "deep"), str(probe / "missing")]
        with no_filesystem_access_for("probe-x", self):
            for path in cases:
                with self.subTest(path=path):
                    self.assertEqual(
                        GalleryConfig.gallery_root_problem(path)[0], "outside-allowed"
                    )
                    self.assertEqual(
                        sources.validate_source(path)["code"], "outside-allowed"
                    )
                    _roots, error = self.api._parse_gallery_roots(
                        {"gallery_root_paths": [path]}
                    )
                    self.assertEqual(error["code"], "invalid-source")
                    status, data = await self._post(VALIDATE, {"path": path})
                    self.assertEqual(data["code"], "outside-allowed")
                    status, data = await self._post(
                        SETTINGS, {"gallery_root_paths": [path]}
                    )
                    self.assertEqual((status, data["code"]), (400, "invalid-source"))
            # A traversal that normalises outside is refused as text too.
            sneaky = os.path.join(
                str(self.output_dir), "..", "..", "outside", "probe-x"
            )
            self.assertEqual(
                GalleryConfig.gallery_root_problem(sneaky)[0], "outside-allowed"
            )

    async def test_settings_save_applies_the_form_checks(self):
        """POST /settings refuses what validate-source refuses, by its form."""
        for path, code in {
            str(self.a) + "\x00": "invalid-form",
            os.path.join(str(self.output_dir), "b", "..", "a"): "traversal",
        }.items():
            with self.subTest(code=code):
                status, data = await self._post(
                    SETTINGS, {"gallery_root_paths": [path]}
                )
                self.assertEqual((status, data["code"]), (400, "invalid-source"))
                self.assertEqual(data["reason"], sources.FORM_REASONS[code])
        windows = functools.partial(sources.source_form_error, windows=True)
        with (
            patch.object(sources, "source_form_error", windows),
            # The probes hold a token no trusted directory does (on Windows the
            # temporary ComfyUI tree itself sits on C:).
            no_filesystem_access_for("PMProbe", self),
        ):
            for path in (
                "\\\\?\\C:\\PMProbe\\output",
                "\\\\.\\pipe\\PMProbe",
                "C:\\PMProbe\\output\\CON",
                "C:\\PMProbe\\output\\a:stream",
                "C:PMProbe",
            ):
                with self.subTest(path=path):
                    _roots, error = self.api._parse_gallery_roots(
                        {"gallery_root_paths": ["output/a", path]}
                    )
                    self.assertEqual(error["code"], "invalid-source")
                    self.assertEqual(error["index"], 1)
                    self.assertEqual(
                        error["reason"], sources.FORM_REASONS["invalid-form"]
                    )

    async def test_inside_paths_say_why_they_are_not_usable(self):
        a_file = self.output_dir / "file.png"
        a_file.write_bytes(b"x")
        status, missing = await self._post(
            VALIDATE, {"path": str(self.output_dir / "missing")}
        )
        status, not_dir = await self._post(VALIDATE, {"path": str(a_file)})
        self.assertEqual(
            (missing["code"], not_dir["code"]), ("not-found", "not-directory")
        )

    async def test_malformed_request_is_400(self):
        for body in ({}, {"path": 42}, {"path": None}, ["x"]):
            with self.subTest(body=body):
                status, data = await self._post(VALIDATE, body)
                self.assertEqual(status, 400, data)
                self.assertEqual(data["code"], "invalid-request")
                self.assertFalse(data["success"])


class TestSourceEdges(SourcesTestCase):
    async def test_validation_failure_is_500_without_paths(self):
        def boom(path):
            raise OSError(13, "Permission denied", f"{self.tmpdir}/secret")

        with patch.object(sources, "validate_source", boom):
            status, data = await self._post(VALIDATE, {"path": str(self.a)})
        self.assertEqual(status, 500)
        self.assertEqual(data["error"], "Failed to check the source")

    async def test_oversized_body_is_413_too_large(self):
        resp = await self.client.request(
            "POST", VALIDATE, data=json.dumps({"path": "x" * (2 << 20)})
        )
        self.assertEqual(resp.status, 413)
        self.assertEqual((await resp.json())["code"], "too-large")

    async def test_unreadable_monitor_counts_as_no_live_observer(self):
        class Broken:
            def get_status(self):
                raise RuntimeError("observer gone")

        self._install_monitor(Broken())
        status, data = await self._post(
            "/prompt_manager/settings", {"monitoring_enabled": True}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["restart_required_fields"], ["monitoring_enabled"])

    def test_labels_skip_getters_that_fail_or_are_missing(self):
        import sys
        import types

        def fails():
            raise RuntimeError("no input dir")

        fake = types.SimpleNamespace(
            get_output_directory=lambda: str(self.output_dir),
            get_input_directory=fails,
            get_temp_directory=lambda: None,
        )
        with patch.dict(sys.modules, {"folder_paths": fake}):
            self.assertEqual(sources.source_label(self.output_dir), "ComfyUI output")
            self.assertEqual(sources.source_label(self.a), "a")
        with patch.dict(sys.modules, {"folder_paths": None}):
            self.assertEqual(sources.source_label(self.output_dir), "output")


class TestOutsideBaseDirectories(SourcesTestCase):
    """ComfyUI folders outside the base directory (``--output-directory``):
    their sources round-trip and two folders of one name stay apart (I3)."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.renders = self.outside_dir / "renders"
        self.input_dir = self.comfy_dir / "input"
        (self.renders / "portraits").mkdir(parents=True)
        (self.input_dir / "portraits").mkdir(parents=True)
        sys.modules["folder_paths"] = types.SimpleNamespace(
            base_path=str(self.comfy_dir),
            get_output_directory=lambda: str(self.renders),
            get_input_directory=lambda: str(self.input_dir),
        )

    async def test_source_outside_base_round_trips(self):
        status, rendered = await self._post(
            VALIDATE, {"path": str(self.renders / "portraits")}
        )
        self.assertEqual(
            (rendered["valid"], rendered["path"]), (True, "renders/portraits")
        )
        status, data = await self._post(
            SETTINGS, {"gallery_root_paths": [rendered["path"]]}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(
            GalleryConfig.MONITORING_DIRECTORIES,
            [self._root(self.renders / "portraits")],
        )
        configured = (await self._get())["sources"]["configured"]
        self.assertEqual(
            [(row["path"], row["valid"]) for row in configured],
            [("renders/portraits", True)],
        )
        # The page resends what it read: still the same folder, no change.
        status, data = await self._post(
            SETTINGS, {"gallery_root_paths": ["renders/portraits"]}
        )
        self.assertEqual((status, data["restart_required_fields"]), (200, []))

    async def test_same_name_folders_stay_apart(self):
        answers = []
        for path in (self.renders / "portraits", self.input_dir / "portraits"):
            status, data = await self._post(VALIDATE, {"path": str(path)})
            answers.append(data["path"])
        self.assertEqual(answers, ["renders/portraits", "input/portraits"])
        status, data = await self._post(SETTINGS, {"gallery_root_paths": answers})
        self.assertEqual(status, 200, data)
        sources_ = (await self._get())["sources"]
        self.assertEqual([row["path"] for row in sources_["configured"]], answers)
        self.assertEqual([row["path"] for row in sources_["effective"]], answers)


class TestUnusableSourceSave(SourcesTestCase):
    async def test_unusable_source_is_named_and_saves_nothing(self):
        """A configured source that went missing blocks the save and is named."""
        gone = self.output_dir / "gone"
        gone.mkdir()
        GalleryConfig.MONITORING_DIRECTORIES = [self._root(self.a), self._root(gone)]
        self._write_config({"custom": 1})
        before = self._config_bytes()
        gone.rmdir()  # an unplugged drive

        status, data = await self._post(
            SETTINGS,
            {
                "gallery_root_paths": ["output/a", "output/gone", "output/b"],
                "worker_threads": 2,
            },
        )

        self.assertEqual(status, 400, data)
        self.assertEqual(
            data,
            {
                "success": False,
                "code": "invalid-source",
                "source": "output/gone",
                "index": 1,
                "reason": "Gallery root does not exist",
                "error": "output/gone can't be used: Gallery root does not exist. "
                "Remove it or reconnect it, then save.",
            },
        )
        self.assertEqual(self._config_bytes(), before)

    async def test_malformed_roots_are_coded_invalid_request(self):
        for roots in ("output/a", [1]):
            with self.subTest(roots=roots):
                status, data = await self._post(SETTINGS, {"gallery_root_paths": roots})
                self.assertEqual((status, data["code"]), (400, "invalid-request"))


class TestUnchangedRootsTakeNoHold(SourcesTestCase):
    async def test_resent_spelling_of_configured_roots_is_no_change(self):
        """config.json holds a hand-written spelling; the legacy page resends
        it resolved: no source change, so no hold and no 409 during a scan (M1)."""
        GalleryConfig.MONITORING_DIRECTORIES = ["output/a"]
        gate = asyncio.Event()

        async def runner(job):
            await gate.wait()
            job.finish("finished")

        self.api._jobs.start(
            "scan", "Metadata scan", record_changing=True, runner=runner
        )
        try:
            status, data = await self._post(
                SETTINGS, {"gallery_root_paths": [str(self.a)], "result_timeout": 30}
            )
            self.assertEqual(status, 200, data)
            self.assertEqual(data["restart_required_fields"], [])
            self.assertEqual([j.kind for j in self.api._jobs.list()], ["scan"])
            status, data = await self._post(
                SETTINGS, {"gallery_root_paths": [str(self.b)]}
            )
            self.assertEqual((status, data["code"]), (409, "busy"))
        finally:
            gate.set()


class TestWindowsContainment(unittest.TestCase):
    """Windows path forms, with ntpath fakes: anything outside the allowed
    folders (a UNC share, the verbatim and device namespaces, another drive)
    is refused as text, before any path is resolved or opened (I2)."""

    ALLOWED = "c:\\comfyui\\output"
    NAS = "\\\\nas\\share\\renders"

    def setUp(self):
        self.touched = []

        def realpath(path, *args, **kwargs):
            self.touched.append(path)
            return ntpath.normcase(ntpath.normpath(path))

        def probe(path):
            self.touched.append(path)
            return True

        names = ("normcase", "normpath", "isabs", "join", "dirname", "basename", "sep")
        fake_path = types.SimpleNamespace(
            **{name: getattr(ntpath, name) for name in names},
            realpath=realpath,
            exists=probe,
            isdir=probe,
            expanduser=lambda path: "c:\\users\\someone",
        )
        fake_os = types.SimpleNamespace(
            path=fake_path, environ={}, pathsep=";", name="nt"
        )
        for target, value in (
            (patch.object(config_module, "os", fake_os), None),
            (
                patch.object(
                    GalleryConfig,
                    "_allowed_parent_pairs",
                    classmethod(
                        lambda cls: [(self.ALLOWED, self.ALLOWED), (self.NAS, self.NAS)]
                    ),
                ),
                None,
            ),
            (
                patch.object(
                    GalleryConfig,
                    "path_anchors",
                    classmethod(lambda cls: ["c:\\comfyui", "\\\\nas\\share"]),
                ),
                None,
            ),
        ):
            target.start()
            self.addCleanup(target.stop)

    def test_outside_forms_are_refused_untouched(self):
        for path in (
            "\\\\attacker\\share\\x",
            "//attacker/share/x",
            "\\\\?\\C:\\ComfyUI\\output\\a",
            "\\\\?\\UNC\\attacker\\share",
            "\\\\.\\pipe\\x",
            "\\\\.\\C:\\ComfyUI\\output",
            "D:\\Renders",
            "C:\\Windows",
            "C:\\ComfyUI\\output\\..\\..\\Windows",
            "C:\\ComfyUI\\output2",
            "\\\\nas\\share\\other",
            "..\\..\\Windows",
        ):
            with self.subTest(path=path):
                self.touched.clear()
                self.assertEqual(
                    GalleryConfig.gallery_root_problem(path),
                    ("outside-allowed", config_module.OUTSIDE_ALLOWED_REASON),
                )
                self.assertEqual(self.touched, [])

    def test_allowed_spellings_are_accepted(self):
        for path in (
            "C:\\ComfyUI\\output\\a",
            "c:/comfyui/OUTPUT/a",
            "output\\a",
            "\\\\NAS\\share\\renders\\a",  # a UNC spelling of an allowed extra root
        ):
            with self.subTest(path=path):
                self.assertEqual(GalleryConfig.gallery_root_problem(path), (None, ""))


class TestSourceFormError(unittest.TestCase):
    """The lexical check that runs before any filesystem access."""

    def test_windows_forms(self):
        err = sources.source_form_error
        for path in (
            "C:\\ComfyUI\\output",
            "C:/ComfyUI/output",
            "\\\\server\\share\\output",
            "D:\\images",
        ):
            with self.subTest(path=path):
                self.assertIsNone(err(path, windows=True))
        for path, code in {
            "C:output": "invalid-form",  # drive-relative
            "C:\\out\\a:stream": "invalid-form",  # alternate data stream
            "C:\\out\\CON": "invalid-form",
            "C:\\out\\nul.txt": "invalid-form",
            "C:\\out\\folder.": "invalid-form",
            "C:\\out\\folder \\sub": "invalid-form",
            "\\\\?\\C:\\out": "invalid-form",
            "//?/C:/out": "invalid-form",
            "\\\\.\\pipe\\x": "invalid-form",
            "\\\\server": "invalid-form",
            "C:\\out\\..\\in": "traversal",
            "output\\..\\input": "traversal",
        }.items():
            with self.subTest(path=path):
                self.assertEqual(err(path, windows=True), code)

    def test_posix_forms(self):
        err = sources.source_form_error
        for path in ("/srv/comfy/output", "output/renders", "out/CON", "a b/c."):
            with self.subTest(path=path):
                self.assertIsNone(err(path, windows=False))
        for path, code in {
            "C:\\ComfyUI\\output": "invalid-form",
            "C:/ComfyUI/output": "invalid-form",
            "\\\\server\\share": "invalid-form",
            "out\\renders": "invalid-form",
            "/srv/../etc": "traversal",
            "../output": "traversal",
            "/srv/out\x00": "invalid-form",
            "/srv/out\n": "invalid-form",
            "": "empty",
        }.items():
            with self.subTest(path=path):
                self.assertEqual(err(path, windows=False), code)


if __name__ == "__main__":
    unittest.main()


class TestAutotagDefaultsSettings(SourcesTestCase):
    """#328: the tagging defaults save through POST /settings like the others."""

    ATTRS = (
        "MODEL_TYPE",
        "EXISTING",
        "GENERAL_THRESHOLD",
        "CHARACTER_THRESHOLD",
        "TEMPLATE",
        "SUGGESTION_LIMIT",
        "SKIPPED_TAGS",
    )
    SAVED = {
        "autotag_model_type": "gguf",
        "autotag_existing": "merge",
        "autotag_general_threshold": 0.4,
        "autotag_character_threshold": 0.8,
        "autotag_template": "Tags only.",
        "autotag_suggestion_limit": 15,
    }

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from py.config import AutotagConfig

        self.autotag = AutotagConfig
        for attr in self.ATTRS:
            self.addCleanup(setattr, AutotagConfig, attr, getattr(AutotagConfig, attr))

    def _runtime(self):
        return {attr: getattr(self.autotag, attr) for attr in self.ATTRS}

    async def test_autotag_defaults_save_round_trip(self):
        self._write_config({"custom": {"keep": True}})
        revision = (await self._get())["revision"]
        status, data = await self._post(
            "/prompt_manager/settings", {"expected_revision": revision, **self.SAVED}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["restart_required_fields"], [])
        saved = self._read_config()
        self.assertEqual(
            saved["autotag"]["defaults"],
            {key[len("autotag_") :]: value for key, value in self.SAVED.items()},
        )
        self.assertEqual(saved["custom"], {"keep": True})
        self.assertEqual(
            self._runtime(),
            {
                "MODEL_TYPE": "gguf",
                "EXISTING": "merge",
                "GENERAL_THRESHOLD": 0.4,
                "CHARACTER_THRESHOLD": 0.8,
                "TEMPLATE": "Tags only.",
                "SUGGESTION_LIMIT": 15,
                "SKIPPED_TAGS": (),
            },
        )
        settings = (await self._get())["settings"]
        self.assertEqual({key: settings[key] for key in self.SAVED}, self.SAVED)

    async def test_threshold_near_the_grid_is_saved_on_it(self):
        """#369: a computed value a hair off the 0.05 grid is stored on it, so
        every page's step check accepts what /settings and the file return."""
        revision = (await self._get())["revision"]
        status, data = await self._post(
            "/prompt_manager/settings",
            {"expected_revision": revision, "autotag_general_threshold": 0.1 + 0.2},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(
            repr(self._read_config()["autotag"]["defaults"]["general_threshold"]),
            "0.3",
        )
        self.assertEqual(
            repr((await self._get())["settings"]["autotag_general_threshold"]), "0.3"
        )

    async def test_stale_revision_changes_no_default(self):
        revision = (await self._get())["revision"]
        self._write_config({"custom": {"edited": "elsewhere"}})
        before = (self._config_bytes(), self._runtime())
        status, data = await self._post(
            "/prompt_manager/settings", {"expected_revision": revision, **self.SAVED}
        )
        self.assertEqual(status, 409, data)
        self.assertEqual(data["code"], "settings-changed")
        self.assertEqual((self._config_bytes(), self._runtime()), before)

    async def test_bad_default_message_names_the_setting(self):
        cases = (
            (
                "autotag_general_threshold",
                1.5,
                "General-tag confidence must be between 0 and 1.",
            ),
            (
                "autotag_general_threshold",
                0.37,
                "General-tag confidence must be a multiple of 0.05.",
            ),
            (
                "autotag_character_threshold",
                0.33,
                "Character-tag confidence must be a multiple of 0.05.",
            ),
            (
                "autotag_character_threshold",
                "0.8",
                "Character-tag confidence must be a finite number.",
            ),
            (
                "autotag_character_threshold",
                True,
                "Character-tag confidence must be a finite number.",
            ),
            (
                "autotag_model_type",
                "wd14-convnext",
                "Tagging model must be one of gguf, hf, wd14-swinv2, wd14-vit.",
            ),
            (
                "autotag_existing",
                "replace",
                "Existing tags must be skip, merge or replace_after_review.",
            ),
            ("autotag_template", " ", "LLM tag template must be non-empty text."),
            (
                "autotag_template",
                "x" * 4001,
                "LLM tag template must be at most 4000 characters.",
            ),
            (
                "autotag_suggestion_limit",
                0,
                "Suggestions per image must be a whole number from 1 to 100.",
            ),
            (
                "autotag_suggestion_limit",
                2.5,
                "Suggestions per image must be a whole number from 1 to 100.",
            ),
        )
        self._write_config({"custom": {"keep": True}})
        before = (self._config_bytes(), self._runtime())
        for key, value, message in cases:
            with self.subTest(key=key, value=value):
                status, data = await self._post(
                    "/prompt_manager/settings", {**self.SAVED, key: value}
                )
                self.assertEqual(status, 400, data)
                self.assertEqual(data["error"], message)
                self.assertEqual((self._config_bytes(), self._runtime()), before)

    async def test_skipped_tags_save_trimmed_and_read_back(self):
        """#431: the list is saved as typed (trimmed), published at once and
        read back by GET /settings."""
        self._write_config({"custom": {"keep": True}})
        settings = (await self._get())["settings"]
        self.assertEqual(settings["autotag_skipped_tags"], [])
        revision = (await self._get())["revision"]
        status, data = await self._post(
            "/prompt_manager/settings",
            {
                "expected_revision": revision,
                "autotag_skipped_tags": [" blurry ", "closed eyes", "(fate)"],
            },
        )
        self.assertEqual(status, 200, data)
        saved = self._read_config()
        self.assertEqual(
            saved["autotag"]["defaults"],
            {"skipped_tags": ["blurry", "closed eyes", "(fate)"]},
        )
        self.assertEqual(saved["custom"], {"keep": True})
        self.assertEqual(self.autotag.SKIPPED_TAGS, ("blurry", "closed eyes", "(fate)"))
        settings = (await self._get())["settings"]
        self.assertEqual(
            settings["autotag_skipped_tags"], ["blurry", "closed eyes", "(fate)"]
        )

    async def test_bad_skipped_tags_are_refused_with_their_sentence(self):
        self._write_config({"custom": {"keep": True}})
        revision = (await self._get())["revision"]
        before = (self._config_bytes(), self._runtime())
        for value, message in (
            (["sky", "  "], "Skipped tags: entry 2 is empty."),
            (["x" * 51], "Skipped tags: entry 1 must be at most 50 characters."),
            (
                ["a, b"],
                "Skipped tags: entry 1 must not contain a comma or a line break.",
            ),
            (["blurry", "Blurry"], "Skipped tags: entry 2 repeats entry 1."),
            ([f"w{i}" for i in range(201)], "Skipped tags: at most 200 entries."),
        ):
            with self.subTest(value=value[:3]):
                status, data = await self._post(
                    "/prompt_manager/settings",
                    {"expected_revision": revision, "autotag_skipped_tags": value},
                )
                self.assertEqual(status, 400, data)
                self.assertEqual(data["error"], message)
                self.assertEqual((self._config_bytes(), self._runtime()), before)

    async def test_skipped_tags_without_a_revision_are_refused(self):
        """A list saved without expected_revision could merge a stale copy."""
        self._write_config({"custom": {"keep": True}})
        before = (self._config_bytes(), self._runtime())
        status, data = await self._post(
            "/prompt_manager/settings", {"autotag_skipped_tags": ["blurry"]}
        )
        self.assertEqual(status, 400, data)
        self.assertEqual(data["error"], "Reload the page and try again.")
        self.assertEqual((self._config_bytes(), self._runtime()), before)
