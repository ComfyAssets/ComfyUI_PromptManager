"""System › Sources & scans backend (#249 S4 Task 9).

``GET /prompt_manager/settings`` reports the configured, effective and
watched sources and the monitoring setting; ``POST`` saves
``monitoring_enabled`` (a restart applies it) and refuses a source change
while a record-changing job runs; ``POST /prompt_manager/settings/validate-source``
checks one path and writes nothing. No response carries an absolute path.
"""

import asyncio
import json
import ntpath
import os
import unittest
from unittest.mock import patch

try:
    from tests.test_api_admin import AdminAPITestCase
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_admin import AdminAPITestCase

from py.api import settings_routes, sources  # noqa: E402
from py.config import GalleryConfig  # noqa: E402

VALIDATE = "/prompt_manager/settings/validate-source"


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
            [{"label": "a", "path": "output/a", "root_index": 0, "watch": "manual"}],
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
                }
            ],
        )

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
        # restart, and nothing else is pending.
        GalleryConfig.MONITORING_ENABLED = False
        data = await self._get()
        self.assertFalse(data["settings"]["monitoring_enabled"])
        self.assertEqual(
            self._states(data), {"output/a": "watching", "output/b": "manual"}
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
            patch.object(
                GalleryConfig, "validate_gallery_root", return_value=(True, "")
            ),
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
        (self.outside_dir / "x").mkdir()
        answers = []
        for path in (self.outside_dir / "x", self.tmpdir + "/nothing/x"):
            status, data = await self._post(
                "/prompt_manager/settings", {"gallery_root_paths": [str(path)]}
            )
            answers.append((status, data))
        self.assertEqual(answers[0][0], 400)
        self.assertEqual(answers[0], answers[1])

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
