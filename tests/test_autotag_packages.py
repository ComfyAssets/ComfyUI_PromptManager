"""#424: each tagging backend reports the Python packages it cannot import.

``find_spec`` is patched throughout, and a backend an earlier test imported is
hidden where it matters, so these pass with or without the ML packages (#472).
"""

import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from test_autotag_engine import (
        EngineTestCase,
        FakeGgufModel,
        _patch_modules,
        _touch_model_files,
    )
except ImportError:  # pragma: no cover - run as a package
    from tests.test_autotag_engine import (
        EngineTestCase,
        FakeGgufModel,
        _patch_modules,
        _touch_model_files,
    )

from py import autotag  # noqa: E402
from py.autotag import (  # noqa: E402
    BACKEND_PACKAGES,
    MODELS,
    AutoTagService,
    MissingBackendPackages,
    missing_packages,
    package_fields,
)

GGUF_CUDA = 'For a CUDA build: CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python'


def _find_spec_without(*absent):
    """A ``find_spec`` that knows every module except *absent*."""

    def find_spec(name, *args):
        return None if name in absent else object()

    return find_spec


def _missing_from(*absent):
    return patch.object(
        autotag.importlib.util, "find_spec", side_effect=_find_spec_without(*absent)
    )


class TestMissingPackages(unittest.TestCase):

    def test_every_backend_names_its_modules_and_pip_names(self):
        self.assertEqual(BACKEND_PACKAGES["gguf"], (("llama_cpp", "llama-cpp-python"),))
        self.assertEqual(
            [pip for _module, pip in BACKEND_PACKAGES["hf"]],
            ["torch", "transformers", "accelerate", "bitsandbytes"],
        )
        for model in ("wd14-swinv2", "wd14-vit"):
            self.assertEqual(BACKEND_PACKAGES[model], (("onnxruntime", "onnxruntime"),))
        self.assertEqual(set(BACKEND_PACKAGES), set(MODELS))

    def test_reports_pip_names_of_modules_find_spec_cannot_find(self):
        with _missing_from("llama_cpp", "accelerate", "bitsandbytes"):
            self.assertEqual(missing_packages("gguf"), ["llama-cpp-python"])
            self.assertEqual(missing_packages("hf"), ["accelerate", "bitsandbytes"])
            self.assertEqual(missing_packages("wd14-vit"), [])

    def test_find_spec_errors_count_as_missing(self):
        for error in (ValueError("no spec"), ImportError("broken"), OSError("io")):
            with self.subTest(error=type(error).__name__):
                # Hide an onnxruntime an earlier test imported, so the check
                # reaches find_spec (#472).
                with (
                    _patch_modules({"onnxruntime": None}),
                    patch.object(
                        autotag.importlib.util, "find_spec", side_effect=error
                    ),
                ):
                    self.assertEqual(missing_packages("wd14-vit"), ["onnxruntime"])

    def test_a_module_already_imported_is_present_without_a_lookup(self):
        with _patch_modules({"llama_cpp": types.ModuleType("llama_cpp")}):
            with patch.object(
                autotag.importlib.util, "find_spec", side_effect=AssertionError
            ):
                self.assertEqual(missing_packages("gguf"), [])

    def test_the_check_imports_nothing(self):
        before = set(sys.modules)
        missing_packages("gguf")
        missing_packages("hf")
        missing_packages("wd14-vit")
        imported = set(sys.modules) - before
        heavy = {"llama_cpp", "torch", "transformers", "onnxruntime"}
        self.assertFalse(imported & heavy, imported)

    def test_unknown_model_has_no_packages(self):
        self.assertEqual(missing_packages("nope"), [])

    def test_package_fields_and_the_one_sentence(self):
        self.assertEqual(
            package_fields("gguf", []),
            {"missing_packages": [], "install_command": None, "install_note": None},
        )
        fields = package_fields("gguf", ["llama-cpp-python"])
        self.assertEqual(fields["missing_packages"], ["llama-cpp-python"])
        self.assertEqual(fields["install_command"], "pip install llama-cpp-python")
        self.assertEqual(fields["install_note"], GGUF_CUDA)
        self.assertEqual(
            package_fields("hf", ["torch", "bitsandbytes"])["install_command"],
            "pip install torch bitsandbytes",
        )
        self.assertIsNone(package_fields("hf", ["torch"])["install_note"])
        self.assertIn(
            "onnxruntime-gpu",
            package_fields("wd14-vit", ["onnxruntime"])["install_note"],
        )
        error = MissingBackendPackages("gguf", ["llama-cpp-python"])
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(
            str(error),
            "GGUF JoyCaption needs llama-cpp-python. Install it into ComfyUI's "
            "Python: pip install llama-cpp-python. " + GGUF_CUDA,
        )
        self.assertEqual(
            str(MissingBackendPackages("hf", ["accelerate", "bitsandbytes"])),
            "HuggingFace JoyCaption needs accelerate and bitsandbytes. Install them "
            "into ComfyUI's Python: pip install accelerate bitsandbytes.",
        )


class TestStatusReportsPackages(EngineTestCase):

    def test_downloaded_keeps_its_files_only_meaning(self):
        _touch_model_files(self.models_dir, "gguf")
        _touch_model_files(self.models_dir, "wd14-vit")
        service = AutoTagService(models_dir=self.models_dir)

        with _missing_from("llama_cpp"):
            status = service.get_models_status()

        self.assertTrue(status["gguf"]["downloaded"])
        self.assertEqual(status["gguf"]["missing_packages"], ["llama-cpp-python"])
        self.assertEqual(
            status["gguf"]["install_command"], "pip install llama-cpp-python"
        )
        self.assertEqual(status["gguf"]["install_note"], GGUF_CUDA)
        self.assertEqual(status["wd14-vit"]["missing_packages"], [])
        self.assertIsNone(status["wd14-vit"]["install_command"])
        # A model not on disk still says which packages it will need.
        self.assertFalse(status["wd14-swinv2"]["downloaded"])
        self.assertIn("missing_packages", status["hf"])

    def test_an_injected_loader_brings_its_own_backend(self):
        service = self._service(("gguf", FakeGgufModel("x")))
        with _missing_from("llama_cpp"):
            self.assertEqual(
                service.get_models_status()["gguf"]["missing_packages"], []
            )

    def test_an_injected_probe_is_used_for_status_and_load(self):
        _touch_model_files(self.models_dir, "gguf")
        service = AutoTagService(
            models_dir=self.models_dir,
            model_loader=lambda *_: ("gguf", FakeGgufModel("x")),
            package_probe=lambda model_type: ["llama-cpp-python"],
        )
        self.assertEqual(
            service.get_models_status()["gguf"]["missing_packages"],
            ["llama-cpp-python"],
        )
        with self.assertRaises(MissingBackendPackages):
            service.load_model("gguf")


class TestLoadFailsFast(EngineTestCase):

    def test_missing_package_refuses_before_the_loader_without_an_error_log(self):
        _touch_model_files(self.models_dir, "gguf")
        service = AutoTagService(models_dir=self.models_dir)
        calls = []
        service._model_loader = lambda *args: calls.append(args)

        with (
            _missing_from("llama_cpp"),
            self.assertLogs(service.logger, level="DEBUG") as logs,
        ):
            with self.assertRaises(MissingBackendPackages) as ctx:
                service.load_model("gguf")

        self.assertEqual(calls, [])
        self.assertEqual(ctx.exception.model_type, "gguf")
        self.assertEqual(ctx.exception.missing, ["llama-cpp-python"])
        self.assertIn("pip install llama-cpp-python", str(ctx.exception))
        self.assertFalse(service.is_model_loaded())
        levels = {record.levelname for record in logs.records}
        self.assertNotIn("ERROR", levels)
        self.assertIn("WARNING", levels)
        self.assertTrue(all(record.exc_info is None for record in logs.records))

    def test_not_downloaded_is_reported_before_packages(self):
        service = AutoTagService(models_dir=self.models_dir)
        with _missing_from("llama_cpp"):
            with self.assertRaises(RuntimeError) as ctx:
                service.load_model("gguf")
        self.assertNotIsInstance(ctx.exception, MissingBackendPackages)

    def test_a_module_not_found_from_the_loader_becomes_the_same_refusal(self):
        _touch_model_files(self.models_dir, "gguf")

        def loader(model_type, use_gpu):
            raise ModuleNotFoundError("No module named 'llama_cpp'", name="llama_cpp")

        service = AutoTagService(
            models_dir=self.models_dir,
            model_loader=loader,
            package_probe=lambda _model: [],
        )
        with self.assertLogs(service.logger, level="DEBUG") as logs:
            with self.assertRaises(MissingBackendPackages) as ctx:
                service.load_model("gguf")
        self.assertEqual(ctx.exception.missing, ["llama-cpp-python"])
        self.assertNotIn("ERROR", {record.levelname for record in logs.records})
        self.assertFalse(service.is_model_loaded())

    def test_an_unknown_module_not_found_names_the_module(self):
        def loader(model_type, use_gpu):
            raise ModuleNotFoundError(
                "No module named 'sentencepiece'", name="sentencepiece"
            )

        hf_dir = self.models_dir / MODELS["hf"]["subdir"]
        hf_dir.mkdir(parents=True, exist_ok=True)
        (hf_dir / "config.json").write_text("{}")
        service = AutoTagService(
            models_dir=self.models_dir,
            model_loader=loader,
            package_probe=lambda _model: [],
        )
        with self.assertRaises(MissingBackendPackages) as ctx:
            service.load_model("hf")
        self.assertEqual(ctx.exception.missing, ["sentencepiece"])


if __name__ == "__main__":
    unittest.main()
