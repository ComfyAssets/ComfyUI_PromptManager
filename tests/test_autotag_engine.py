"""Tests for the AutoTagService tagging pipeline with an injected fake model.

No real ML model is loaded; ``model_loader`` returns small fakes that mimic
the onnxruntime session (WD14) and the llama.cpp chat model (GGUF).
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from py.autotag import MODELS, AutoTagService


class FakeInput:
    def __init__(self, name="input", shape=(1, 8, 8, 3)):
        self.name = name
        self.shape = list(shape)


class FakeOnnxSession:
    """Mimics onnxruntime.InferenceSession for a WD14 classifier."""

    def __init__(self, probabilities):
        self.probabilities = probabilities
        self.run_calls = []

    def get_inputs(self):
        return [FakeInput()]

    def get_providers(self):
        return ["CPUExecutionProvider"]

    def run(self, outputs, feeds):
        self.run_calls.append(feeds)
        return [[list(self.probabilities)]]


class FakeGgufModel:
    """Mimics llama_cpp.Llama.create_chat_completion."""

    def __init__(self, text):
        self.text = text
        self.calls = []

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        return {"choices": [{"message": {"content": self.text}}]}


WD14_TAGS = [
    {"name": "1girl", "category": "0"},
    {"name": "smile", "category": "0"},
    {"name": "hatsune_miku", "category": "4"},
    {"name": "rating:safe", "category": "9"},
    {"name": "", "category": "0"},
    {"name": "long_hair", "category": "0"},
]


def _touch_model_files(models_dir, model_type):
    config = MODELS[model_type]
    subdir = Path(models_dir) / config["subdir"]
    subdir.mkdir(parents=True, exist_ok=True)
    names = config.get("files") or [config["filename"], config["mmproj_filename"]]
    for name in names:
        (subdir / name).write_bytes(b"stub")


class EngineTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.models_dir = self.tmp / "models"
        self.image_path = self.tmp / "img.png"
        Image.new("RGB", (4, 6), (200, 100, 50)).save(self.image_path, "PNG")
        self.loader_calls = []

    def _service(self, loaded):
        def loader(model_type, use_gpu):
            self.loader_calls.append((model_type, use_gpu))
            return loaded

        return AutoTagService(models_dir=self.models_dir, model_loader=loader)


class TestModelInjection(EngineTestCase):

    def test_injected_loader_is_used_and_state_tracked(self):
        _touch_model_files(self.models_dir, "wd14-vit")
        service = self._service(("wd14", (FakeOnnxSession([0.0] * 6), WD14_TAGS)))

        self.assertTrue(service.load_model("wd14-vit", use_gpu=False))

        self.assertEqual(self.loader_calls, [("wd14-vit", False)])
        self.assertTrue(service.is_model_loaded())
        self.assertEqual(service.get_loaded_model_type(), "wd14-vit")

    def test_default_loader_is_the_lazy_dispatcher(self):
        service = AutoTagService(models_dir=self.models_dir)
        self.assertEqual(service._model_loader, service._load_model)

    def test_importing_autotag_does_not_import_ml_backends(self):
        for heavy in ("onnxruntime", "llama_cpp"):
            self.assertNotIn(heavy, sys.modules)

    def test_default_wd14_loader_reports_missing_onnxruntime(self):
        _touch_model_files(self.models_dir, "wd14-vit")
        service = AutoTagService(models_dir=self.models_dir)
        if "onnxruntime" in sys.modules:
            self.skipTest("onnxruntime is installed in this environment")

        with self.assertRaises(RuntimeError) as ctx:
            service.load_model("wd14-vit")

        self.assertIn("onnxruntime", str(ctx.exception))
        self.assertFalse(service.is_model_loaded())

    def test_invalid_model_type_raises_value_error(self):
        service = self._service(("gguf", FakeGgufModel("x")))
        with self.assertRaises(ValueError):
            service.load_model("nope")

    def test_not_downloaded_raises_runtime_error_before_loading(self):
        service = self._service(("gguf", FakeGgufModel("x")))

        with self.assertRaises(RuntimeError):
            service.load_model("gguf")

        self.assertEqual(self.loader_calls, [])

    def test_loader_failure_clears_state(self):
        _touch_model_files(self.models_dir, "gguf")

        def failing_loader(model_type, use_gpu):
            raise OSError("cuda exploded")

        service = AutoTagService(
            models_dir=self.models_dir, model_loader=failing_loader
        )

        with self.assertRaises(RuntimeError):
            service.load_model("gguf")

        self.assertFalse(service.is_model_loaded())
        self.assertIsNone(service.get_loaded_model_type())

    def test_loading_again_unloads_first(self):
        _touch_model_files(self.models_dir, "gguf")
        _touch_model_files(self.models_dir, "wd14-swinv2")
        service = self._service(("gguf", FakeGgufModel("x")))

        service.load_model("gguf")
        service.load_model("wd14-swinv2")

        self.assertEqual([c[0] for c in self.loader_calls], ["gguf", "wd14-swinv2"])
        self.assertEqual(service.get_loaded_model_type(), "wd14-swinv2")

    def test_unload_clears_state_and_is_idempotent(self):
        _touch_model_files(self.models_dir, "gguf")
        service = self._service(("gguf", FakeGgufModel("x")))
        service.load_model("gguf")

        service.unload_model()
        service.unload_model()

        self.assertFalse(service.is_model_loaded())
        self.assertIsNone(service.get_loaded_model_type())


class TestWd14Thresholding(EngineTestCase):

    def _loaded(self, probabilities):
        _touch_model_files(self.models_dir, "wd14-vit")
        self.session = FakeOnnxSession(probabilities)
        service = self._service(("wd14", (self.session, WD14_TAGS)))
        service.load_model("wd14-vit")
        return service

    def test_default_thresholds_apply_per_category(self):
        # 1girl 0.9 (general, keep), smile 0.3 (general, drop at 0.35),
        # miku 0.8 (character, drop at 0.85), rating 0.99 (ignored),
        # empty name 0.99 (ignored), long_hair 0.5 (keep)
        service = self._loaded([0.9, 0.3, 0.8, 0.99, 0.99, 0.5])

        tags = service.generate_tags(str(self.image_path))

        self.assertEqual(tags, ["1girl", "long_hair"])

    def test_explicit_thresholds_override_defaults(self):
        service = self._loaded([0.9, 0.3, 0.8, 0.99, 0.99, 0.5])

        tags = service.generate_tags(
            str(self.image_path), general_threshold=0.25, character_threshold=0.75
        )

        self.assertEqual(tags, ["1girl", "hatsune_miku", "long_hair", "smile"])

    def test_results_sorted_by_confidence(self):
        service = self._loaded([0.4, 0.95, 0.0, 0.0, 0.0, 0.6])

        self.assertEqual(
            service.generate_tags(str(self.image_path)),
            ["smile", "long_hair", "1girl"],
        )

    def test_fewer_probabilities_than_tags_is_safe(self):
        service = self._loaded([0.9, 0.9])

        self.assertEqual(
            service.generate_tags(str(self.image_path)), ["1girl", "smile"]
        )

    def test_image_is_fed_as_square_batch(self):
        service = self._loaded([0.0] * 6)

        service.generate_tags(str(self.image_path))

        feeds = self.session.run_calls[0]
        self.assertEqual(list(feeds.keys()), ["input"])
        self.assertEqual(tuple(feeds["input"].shape), (1, 8, 8, 3))

    def test_service_threshold_setters_clamp(self):
        service = self._loaded([0.0] * 6)
        service.wd14_general_threshold = 1.7
        service.wd14_character_threshold = -2
        self.assertEqual(service.wd14_general_threshold, 1.0)
        self.assertEqual(service.wd14_character_threshold, 0.0)


class TestGgufNormalisation(EngineTestCase):

    def _loaded(self, text):
        _touch_model_files(self.models_dir, "gguf")
        self.model = FakeGgufModel(text)
        service = self._service(("gguf", self.model))
        service.load_model("gguf")
        return service

    def test_tags_are_lowercased_underscored_deduped_and_filtered(self):
        service = self._loaded(
            "1Girl, Smile, smile, 'quoted tag', photo_realistic, meta:x, "
            "copyright:y, prepend:z, append:w, a, , Long Hair  "
        )

        tags = service.generate_tags(str(self.image_path))

        self.assertEqual(tags, ["1girl", "smile", "quoted_tag", "long_hair"])

    def test_custom_prompt_is_sent_to_the_model(self):
        service = self._loaded("tag")
        service.custom_prompt = "describe tersely"

        service.generate_tags(str(self.image_path))

        user_turn = self.model.calls[0]["messages"][1]["content"]
        self.assertEqual(user_turn[0]["text"], "describe tersely")
        self.assertTrue(user_turn[1]["image_url"]["url"].startswith("data:image/png"))

    def test_explicit_prompt_argument_wins(self):
        service = self._loaded("tag")

        service.generate_tags(str(self.image_path), prompt="one-off")

        self.assertEqual(
            self.model.calls[0]["messages"][1]["content"][0]["text"], "one-off"
        )


class TestGenerateTagsErrors(EngineTestCase):

    def test_no_model_loaded(self):
        service = self._service(("gguf", FakeGgufModel("x")))
        with self.assertRaises(RuntimeError):
            service.generate_tags(str(self.image_path))

    def test_missing_image_error_does_not_embed_the_full_path(self):
        _touch_model_files(self.models_dir, "gguf")
        service = self._service(("gguf", FakeGgufModel("x")))
        service.load_model("gguf")
        missing = self.tmp / "nope.png"

        with self.assertRaises(FileNotFoundError) as ctx:
            service.generate_tags(str(missing))

        self.assertNotIn(str(self.tmp), ctx.exception.strerror)
        self.assertEqual(ctx.exception.filename, str(missing))


class TestModelsStatus(EngineTestCase):

    def test_status_reflects_files_on_disk(self):
        _touch_model_files(self.models_dir, "wd14-vit")
        gguf_dir = self.models_dir / MODELS["gguf"]["subdir"]
        gguf_dir.mkdir(parents=True)
        (gguf_dir / MODELS["gguf"]["filename"]).write_bytes(b"stub")
        service = AutoTagService(models_dir=self.models_dir)

        status = service.get_models_status()

        self.assertTrue(status["wd14-vit"]["downloaded"])
        self.assertTrue(status["wd14-vit"]["model_path"].endswith("model.onnx"))
        self.assertFalse(status["wd14-swinv2"]["downloaded"])
        self.assertTrue(status["gguf"]["model_exists"])
        self.assertFalse(status["gguf"]["mmproj_exists"])
        self.assertFalse(status["gguf"]["downloaded"])
        self.assertFalse(status["hf"]["downloaded"])

    def test_hf_local_directory_counts_as_downloaded(self):
        hf_dir = self.models_dir / MODELS["hf"]["subdir"]
        hf_dir.mkdir(parents=True)
        (hf_dir / "config.json").write_text("{}")
        service = AutoTagService(models_dir=self.models_dir)

        status = service.get_models_status()

        self.assertTrue(status["hf"]["downloaded"])
        self.assertEqual(
            os.path.realpath(status["hf"]["model_path"]), os.path.realpath(str(hf_dir))
        )


if __name__ == "__main__":
    unittest.main()
