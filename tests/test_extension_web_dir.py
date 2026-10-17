"""ComfyUI loads only the canvas extension, not the admin or gallery bundles.

ComfyUI serves every .js file under WEB_DIRECTORY into the canvas page, so the
directory must hold nothing but the node extension.
"""

import contextlib
import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS_DIR)

from test_init_package import load_package  # noqa: E402


class TestExtensionWebDir(unittest.TestCase):
    package_name = "prompt_manager_pkg_web"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        output_dir = os.path.join(cls.tmp.name, "output")
        os.makedirs(output_dir)
        package_context = contextlib.ExitStack()
        cls.addClassCleanup(package_context.close)
        cls.package, _ = package_context.enter_context(
            load_package(
                cls.package_name, os.path.join(cls.tmp.name, "prompts.db"), output_dir
            )
        )

    def test_web_directory_is_the_canvas_extension_folder(self):
        self.assertEqual(self.package.WEB_DIRECTORY, "web/comfy")
        self.assertIn("WEB_DIRECTORY", self.package.__all__)

    def test_comfyui_resolves_the_folder_next_to_the_package(self):
        # ComfyUI joins the module directory with WEB_DIRECTORY and checks isdir
        folder = os.path.join(ROOT, self.package.WEB_DIRECTORY)
        self.assertTrue(os.path.isdir(folder))
        self.assertEqual(sorted(os.listdir(folder)), ["prompt_manager.js"])

    def test_extension_was_moved_not_copied(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "web", "prompt_manager.js")))

    def test_extension_registers_with_the_canvas_app(self):
        path = os.path.join(ROOT, "web", "comfy", "prompt_manager.js")
        with open(path, encoding="utf-8") as f:
            source = f.read()
        self.assertIn("app.registerExtension(", source)
        # Served at /extensions/<name>/prompt_manager.js, so ComfyUI's app module
        # is two levels up regardless of where the file lives on disk.
        self.assertIn('from "../../scripts/app.js"', source)


if __name__ == "__main__":
    unittest.main()
