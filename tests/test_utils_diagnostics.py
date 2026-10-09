"""GalleryDiagnostics against a temporary database and stubbed ComfyUI paths."""

import os
import pathlib
import sqlite3
from contextlib import closing
import sys
import tempfile
import types
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.operations import PromptDatabase
from utils import diagnostics
from utils.diagnostics import GalleryDiagnostics, run_diagnostics
from utils.hashing import generate_prompt_hash


def same_path(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(
        os.path.realpath(b)
    )


class DiagnosticsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "prompts.db")
        self.diag = GalleryDiagnostics(self.db_path)

    def _populate(self):
        db = PromptDatabase(self.db_path)
        try:
            prompt_id = db.save_prompt(
                text="a cat", prompt_hash=generate_prompt_hash("a cat")
            )
            image = os.path.join(self.tmp.name, "cat.png")
            with open(image, "wb") as f:
                f.write(b"x")
            db.link_image_to_prompt(prompt_id=prompt_id, image_path=image, metadata={})
        finally:
            db.close_all()  # Windows cannot delete the temp dir while it is open
        return prompt_id

    def _chdir(self, path):
        previous = os.getcwd()
        os.chdir(path)
        self.addCleanup(os.chdir, previous)


class TestDatabaseChecks(DiagnosticsTestCase):
    def test_missing_database_is_an_error(self):
        result = self.diag.check_database()
        self.assertEqual(result["status"], "error")
        self.assertIn(self.db_path, result["message"])

    def test_populated_database_is_ok(self):
        self._populate()
        result = self.diag.check_database()
        self.assertEqual(
            result, {"status": "ok", "prompt_count": 1, "has_images_table": True}
        )
        images = self.diag.check_images_table()
        self.assertEqual(images["status"], "ok")
        self.assertEqual(images["image_count"], 1)
        self.assertEqual(images["recent_images"][0]["filename"], "cat.png")
        self.assertEqual(images["recent_images"][0]["text"], "a cat")

    def test_corrupt_database_is_an_error(self):
        with open(self.db_path, "wb") as f:
            f.write(b"not sqlite at all" * 10)
        self.assertEqual(self.diag.check_database()["status"], "error")
        self.assertEqual(self.diag.check_images_table()["status"], "error")

    def test_database_without_images_table(self):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("CREATE TABLE prompts (id INTEGER PRIMARY KEY, text TEXT)")
        self.assertEqual(
            self.diag.check_database(),
            {"status": "ok", "prompt_count": 0, "has_images_table": False},
        )
        images = self.diag.check_images_table()
        self.assertEqual(images["status"], "error")
        self.assertIn("generated_images", images["message"])


class TestFileSystemCheck(DiagnosticsTestCase):
    def test_writable_current_directory(self):
        self._chdir(self.tmp.name)
        result = self.diag.check_file_system()
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["can_write"])
        self.assertTrue(same_path(result["current_dir"], self.tmp.name))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "test_write.tmp")))

    def test_unwritable_current_directory(self):
        self._chdir(self.tmp.name)
        with mock.patch("utils.diagnostics.open", create=True, side_effect=OSError):
            result = self.diag.check_file_system()
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["can_write"])

    def test_unexpected_errors_are_reported(self):
        with mock.patch("utils.diagnostics.os.getcwd", side_effect=OSError("gone")):
            result = self.diag.check_file_system()
        self.assertEqual(result["status"], "error")
        self.assertIn("gone", result["message"])


class TestComfyUIOutputCheck(DiagnosticsTestCase):
    def setUp(self):
        super().setUp()
        self.output = os.path.join(self.tmp.name, "comfy_output")
        os.makedirs(os.path.join(self.output, "sub"))
        with open(os.path.join(self.output, "sub", "a.png"), "wb") as f:
            f.write(b"x")
        self.folder_paths = types.ModuleType("folder_paths")
        self.folder_paths.get_output_directory = lambda: self.output

    def _config(self, directories):
        module = types.ModuleType("py.config")
        module.GalleryConfig = types.SimpleNamespace(MONITORING_DIRECTORIES=directories)
        return module

    def test_comfyui_output_directory_is_found(self):
        with mock.patch.dict(
            sys.modules, {"folder_paths": self.folder_paths, "py.config": None}
        ):
            result = self.diag.check_comfyui_output()
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["message"])
        self.assertEqual(len(result["output_dirs"]), 1)
        self.assertTrue(same_path(result["output_dirs"][0], self.output))

    def test_configured_directories_come_first_and_are_not_duplicated(self):
        extra = os.path.join(self.tmp.name, "extra")
        os.makedirs(extra)
        config = self._config([extra, self.output, os.path.join(self.tmp.name, "nope")])
        with mock.patch.dict(
            sys.modules, {"folder_paths": self.folder_paths, "py.config": config}
        ):
            result = self.diag.check_comfyui_output()
        self.assertEqual(len(result["output_dirs"]), 2)
        self.assertTrue(same_path(result["output_dirs"][0], extra))
        self.assertTrue(same_path(result["output_dirs"][1], self.output))

    def test_without_comfyui_falls_back_to_relative_paths(self):
        workdir = os.path.join(self.tmp.name, "work")
        os.makedirs(os.path.join(workdir, "output"))
        self._chdir(workdir)
        with mock.patch.dict(sys.modules, {"folder_paths": None, "py.config": None}):
            result = self.diag.check_comfyui_output()
        self.assertEqual(result["status"], "ok")
        self.assertTrue(
            same_path(result["output_dirs"][0], os.path.join(workdir, "output"))
        )

    def test_nothing_found_is_a_warning(self):
        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty)
        self._chdir(empty)
        with mock.patch.dict(sys.modules, {"folder_paths": None, "py.config": None}):
            result = self.diag.check_comfyui_output()
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["message"], "No output directories found")
        self.assertEqual(result["output_dirs"], [])

    def test_scan_errors_do_not_fail_the_check(self):
        with mock.patch.dict(
            sys.modules, {"folder_paths": self.folder_paths, "py.config": None}
        ):
            with mock.patch.object(
                pathlib.Path, "rglob", side_effect=OSError("denied")
            ):
                result = self.diag.check_comfyui_output()
        self.assertEqual(result["status"], "ok")


class TestDependenciesAndFullRun(DiagnosticsTestCase):
    def test_all_dependencies_present(self):
        result = self.diag.check_dependencies()
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["message"])
        self.assertEqual(
            result["dependencies"], {"watchdog": True, "PIL": True, "sqlite3": True}
        )

    def test_watchdog_without_a_version_attribute_is_still_present(self):
        with mock.patch.dict(sys.modules, {"watchdog": types.ModuleType("watchdog")}):
            result = self.diag.check_dependencies()
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["dependencies"]["watchdog"])

    def test_missing_dependency_is_an_error(self):
        with mock.patch.dict(sys.modules, {"watchdog": None}):
            result = self.diag.check_dependencies()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "Missing dependencies")
        self.assertFalse(result["dependencies"]["watchdog"])

    def test_full_diagnostic_reports_every_category(self):
        self._populate()
        self._chdir(self.tmp.name)
        with mock.patch.dict(sys.modules, {"folder_paths": None, "py.config": None}):
            results = self.diag.run_full_diagnostic()
        self.assertEqual(
            sorted(results),
            [
                "comfyui_output",
                "database",
                "dependencies",
                "file_system",
                "images_table",
            ],
        )
        self.assertEqual(results["database"]["status"], "ok")
        self.assertEqual(results["comfyui_output"]["status"], "warning")

    def test_run_diagnostics_uses_the_default_instance(self):
        with mock.patch.object(diagnostics, "GalleryDiagnostics") as cls:
            cls.return_value.run_full_diagnostic.return_value = {
                "database": {"status": "ok"}
            }
            self.assertEqual(run_diagnostics(), {"database": {"status": "ok"}})


class TestCreateTestImageLink(DiagnosticsTestCase):
    def test_links_a_fake_image_through_the_database(self):
        with mock.patch("database.operations.PromptDatabase") as db_cls:
            db_cls.return_value.link_image_to_prompt.return_value = 7
            result = self.diag.create_test_image_link(3)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["image_id"], 7)
        kwargs = db_cls.return_value.link_image_to_prompt.call_args.kwargs
        self.assertEqual(kwargs["prompt_id"], "3")
        self.assertEqual(kwargs["image_path"], "/fake/test/image.png")
        self.assertIn("file_info", kwargs["metadata"])

    def test_uses_the_given_image_path(self):
        with mock.patch("database.operations.PromptDatabase") as db_cls:
            self.diag.create_test_image_link(3, test_image_path="custom.png")
        kwargs = db_cls.return_value.link_image_to_prompt.call_args.kwargs
        self.assertEqual(kwargs["image_path"], "custom.png")

    def test_failures_are_reported(self):
        with mock.patch(
            "database.operations.PromptDatabase", side_effect=RuntimeError("no db")
        ):
            result = self.diag.create_test_image_link(3)
        self.assertEqual(result["status"], "error")
        self.assertIn("no db", result["message"])


if __name__ == "__main__":
    unittest.main()
