"""Tests for the AutoTagService tagging pipeline with an injected fake model.

No real ML model is loaded; ``model_loader`` returns small fakes that mimic
the onnxruntime session (WD14) and the llama.cpp chat model (GGUF).
"""

import contextlib
import csv
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from py import autotag
from py.autotag import MODELS, AutoTagService


@contextlib.contextmanager
def _patch_modules(mapping):
    """Temporarily set entries of sys.modules, restoring only those keys.

    unittest.mock.patch.dict restores by clearing the whole dict, which evicts
    any heavy module first imported inside the window (torch cannot be
    re-imported in-process), so this patcher touches only the named keys.
    """
    missing = object()
    saved = {name: sys.modules.get(name, missing) for name in mapping}
    try:
        for name, module in mapping.items():
            sys.modules[name] = module
        yield
    finally:
        for name, previous in saved.items():
            if previous is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


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


# ── Model directory resolution and singleton ──────────────────────────


class TestModelsDirResolution(unittest.TestCase):

    def test_uses_folder_paths_models_dir(self):
        fake = types.ModuleType("folder_paths")
        fake.models_dir = os.path.join(tempfile.gettempdir(), "fp_models")
        with _patch_modules({"folder_paths": fake}):
            service = AutoTagService()
        self.assertEqual(service.models_dir, Path(fake.models_dir) / "LLM")

    def test_falls_back_without_folder_paths(self):
        with _patch_modules({"folder_paths": None}):
            service = AutoTagService()
        self.assertEqual(service.models_dir.name, "LLM")

    def test_singleton_is_created_once(self):
        with patch.object(autotag, "_service_instance", None):
            first = autotag.get_autotag_service()
            second = autotag.get_autotag_service()
        self.assertIs(first, second)
        self.assertIsInstance(first, AutoTagService)


# ── HuggingFace cache lookup ──────────────────────────────────────────


def _fake_hub(snapshot_dir=None, repo_id=None, raise_scan=False):
    hub = types.ModuleType("huggingface_hub")
    hub.HFCacheInfo = object

    def scan_cache_dir():
        if raise_scan:
            raise OSError("no cache")
        revision = types.SimpleNamespace(snapshot_path=str(snapshot_dir))
        repo = types.SimpleNamespace(
            repo_id=repo_id, repo_type="model", revisions=[revision]
        )
        other = types.SimpleNamespace(
            repo_id="someone/else", repo_type="dataset", revisions=[]
        )
        return types.SimpleNamespace(repos=[other, repo])

    hub.scan_cache_dir = scan_cache_dir
    return hub


class TestHfCacheLookup(EngineTestCase):

    def test_cache_snapshot_with_config_is_found(self):
        snapshot = self.tmp / "snap"
        snapshot.mkdir()
        (snapshot / "config.json").write_text("{}")
        hub = _fake_hub(snapshot, MODELS["hf"]["repo"])
        service = AutoTagService(models_dir=self.models_dir)

        with _patch_modules({"huggingface_hub": hub}):
            self.assertTrue(service._check_hf_model())
            status = service.get_models_status()

        self.assertTrue(status["hf"]["downloaded"])
        self.assertEqual(Path(status["hf"]["model_path"]), snapshot)

    def test_snapshot_without_config_is_ignored(self):
        snapshot = self.tmp / "snap"
        snapshot.mkdir()
        hub = _fake_hub(snapshot, MODELS["hf"]["repo"])
        service = AutoTagService(models_dir=self.models_dir)
        with _patch_modules({"huggingface_hub": hub}):
            self.assertIsNone(service._get_hf_model_path())

    def test_scan_errors_and_missing_hub_give_none(self):
        service = AutoTagService(models_dir=self.models_dir)
        with _patch_modules({"huggingface_hub": _fake_hub(raise_scan=True)}):
            self.assertIsNone(service._get_hf_cache_path("x/y"))
        with _patch_modules({"huggingface_hub": None}):
            self.assertIsNone(service._get_hf_cache_path("x/y"))


# ── Downloads (hub patched, no network) ───────────────────────────────


class _FakeDownloadHub:
    """huggingface_hub stand-in that materialises files instead of fetching."""

    def __init__(self):
        self.module = types.ModuleType("huggingface_hub")
        self.module.hf_hub_download = self.hf_hub_download
        self.module.snapshot_download = self.snapshot_download
        self.calls = []

    def hf_hub_download(self, repo_id, filename, local_dir, **kwargs):
        self.calls.append(("file", repo_id, filename))
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        (Path(local_dir) / filename).write_bytes(b"model")
        return str(Path(local_dir) / filename)

    def snapshot_download(self, repo_id, local_dir, **kwargs):
        self.calls.append(("snapshot", repo_id))
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        (Path(local_dir) / "config.json").write_text("{}")
        return local_dir


class TestDownloadModel(EngineTestCase):

    def setUp(self):
        super().setUp()
        self.hub = _FakeDownloadHub()
        self.service = AutoTagService(models_dir=self.models_dir)
        self.progress = []

    def _cb(self, status, progress):
        self.progress.append((status, progress))

    def test_invalid_type_raises(self):
        with self.assertRaises(ValueError):
            self.service.download_model("nope")

    def test_missing_hub_reports_failure(self):
        with _patch_modules({"huggingface_hub": None}):
            self.assertFalse(self.service.download_model("gguf", self._cb))
        self.assertEqual(self.progress[-1][1], 0)
        self.assertIn("huggingface_hub", self.progress[-1][0])

    def test_wd14_downloads_each_missing_file(self):
        with _patch_modules({"huggingface_hub": self.hub.module}):
            self.assertTrue(self.service.download_model("wd14-vit", self._cb))
            self.assertTrue(self.service.download_model("wd14-vit", self._cb))

        self.assertEqual(
            [c[2] for c in self.hub.calls], ["model.onnx", "selected_tags.csv"]
        )
        self.assertTrue(self.service.get_models_status()["wd14-vit"]["downloaded"])
        self.assertEqual(self.progress[-1], ("Download complete", 100))

    def test_gguf_downloads_model_then_mmproj(self):
        with _patch_modules({"huggingface_hub": self.hub.module}):
            self.assertTrue(self.service.download_model("gguf", self._cb))

        self.assertEqual(
            [c[1] for c in self.hub.calls],
            [MODELS["gguf"]["repo"], MODELS["gguf"]["mmproj_repo"]],
        )
        self.assertTrue(self.service.get_models_status()["gguf"]["downloaded"])
        self.assertIn(("Main model ready", 50), self.progress)

    def test_gguf_skips_files_already_present(self):
        _touch_model_files(self.models_dir, "gguf")
        with _patch_modules({"huggingface_hub": self.hub.module}):
            self.assertTrue(self.service.download_model("gguf"))
        self.assertEqual(self.hub.calls, [])

    def test_hf_snapshot_download(self):
        with _patch_modules({"huggingface_hub": self.hub.module}):
            self.assertTrue(self.service.download_model("hf", self._cb))
            self.assertTrue(self.service.download_model("hf"))

        self.assertEqual(self.hub.calls, [("snapshot", MODELS["hf"]["repo"])])
        self.assertTrue(self.service.get_models_status()["hf"]["downloaded"])

    def test_download_errors_are_reported_not_raised(self):
        def boom(**kwargs):
            raise OSError("disk full")

        self.hub.module.hf_hub_download = boom
        with _patch_modules({"huggingface_hub": self.hub.module}):
            self.assertFalse(self.service.download_model("wd14-vit", self._cb))
        self.assertTrue(self.progress[-1][0].startswith("Error:"))


# ── Default backend loaders with the ML libraries faked ───────────────


class _FakeOrt:
    def __init__(self):
        self.module = types.ModuleType("onnxruntime")
        self.module.InferenceSession = self.InferenceSession
        self.sessions = []

    def InferenceSession(self, path, providers):
        session = FakeOnnxSession([0.9, 0.1])
        session.path = path
        session.providers = providers
        self.sessions.append(session)
        return session


class TestDefaultLoaders(EngineTestCase):

    def test_wd14_loader_builds_session_and_reads_tags(self):
        _touch_model_files(self.models_dir, "wd14-vit")
        csv_path = self.models_dir / MODELS["wd14-vit"]["subdir"] / "selected_tags.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["tag_id", "name", "category"])
            writer.writeheader()
            writer.writerow({"tag_id": 1, "name": "1girl", "category": "0"})
            writer.writerow({"tag_id": 2, "name": "smile", "category": "0"})
        ort = _FakeOrt()
        service = AutoTagService(models_dir=self.models_dir)

        with _patch_modules({"onnxruntime": ort.module}):
            service.load_model("wd14-vit", use_gpu=False)
            cpu_providers = ort.sessions[-1].providers
            service.load_model("wd14-vit", use_gpu=True)
            gpu_providers = ort.sessions[-1].providers

        self.assertEqual(cpu_providers, ["CPUExecutionProvider"])
        self.assertEqual(gpu_providers[0], "CUDAExecutionProvider")
        self.assertEqual(service.generate_tags(str(self.image_path)), ["1girl"])

    def test_gguf_loader_wires_llama_with_mmproj(self):
        _touch_model_files(self.models_dir, "gguf")
        llama_cpp = types.ModuleType("llama_cpp")
        chat_format = types.ModuleType("llama_cpp.llama_chat_format")
        created = {}

        class Llama:
            def __init__(self, **kwargs):
                created.update(kwargs)

            def create_chat_completion(self, **kwargs):
                return {"choices": [{"message": {"content": "Aa, Bb"}}]}

        class Llava15ChatHandler:
            def __init__(self, clip_model_path):
                created["clip"] = clip_model_path

        llama_cpp.Llama = Llama
        chat_format.Llava15ChatHandler = Llava15ChatHandler
        service = AutoTagService(models_dir=self.models_dir)

        with _patch_modules(
            {"llama_cpp": llama_cpp, "llama_cpp.llama_chat_format": chat_format},
        ):
            service.load_model("gguf", use_gpu=False)

        self.assertEqual(created["n_gpu_layers"], 0)
        self.assertTrue(created["clip"].endswith(MODELS["gguf"]["mmproj_filename"]))
        self.assertEqual(service.generate_tags(str(self.image_path)), ["aa", "bb"])

    def _fake_torch(self, cuda=False):
        torch = MagicMock(name="torch")
        torch.cuda.is_available.return_value = cuda
        torch.float16 = "f16"
        torch.bfloat16 = "bf16"
        torch.inference_mode.return_value.__enter__ = lambda s: None
        torch.inference_mode.return_value.__exit__ = lambda s, *a: False
        torch.cuda.amp.autocast.return_value.__enter__ = lambda s: None
        torch.cuda.amp.autocast.return_value.__exit__ = lambda s, *a: False
        return torch

    def _fake_transformers(self):
        transformers = MagicMock(name="transformers")
        processor = MagicMock(name="processor")
        processor.apply_chat_template.return_value = "<convo>"
        inputs = {"input_ids": MagicMock(shape=(1, 3)), "pixel_values": MagicMock()}
        processed = MagicMock(name="inputs")
        processed.to.return_value = inputs
        processor.return_value = processed
        processor.tokenizer.decode.return_value = "Red Hat, blue sky"
        transformers.AutoProcessor.from_pretrained.return_value = processor
        model = MagicMock(name="model")
        model.generate.return_value = [list(range(10))]
        transformers.LlavaForConditionalGeneration.from_pretrained.return_value = model
        return transformers, model, processor, inputs

    def test_hf_loader_8bit_on_cpu_and_generation(self):
        hf_dir = self.models_dir / MODELS["hf"]["subdir"]
        hf_dir.mkdir(parents=True)
        (hf_dir / "config.json").write_text("{}")
        torch = self._fake_torch(cuda=False)
        transformers, model, processor, inputs = self._fake_transformers()
        pixel = inputs["pixel_values"]
        service = AutoTagService(models_dir=self.models_dir)

        with _patch_modules({"torch": torch, "transformers": transformers}):
            service.load_model("hf")
            tags = service.generate_tags(str(self.image_path))

        self.assertEqual(tags, ["red_hat", "blue_sky"])
        kwargs = (
            transformers.LlavaForConditionalGeneration.from_pretrained.call_args.kwargs
        )
        self.assertEqual(kwargs["device_map"], "cpu")
        self.assertIn("quantization_config", kwargs)
        model.eval.assert_called_once()
        pixel.to.assert_called_with("f16")
        processor.tokenizer.decode.assert_called_once()
        decoded = processor.tokenizer.decode.call_args.args[0]
        self.assertEqual(decoded, list(range(3, 10)))

    def test_hf_loader_without_quantization_uses_bf16_and_cuda(self):
        hf_dir = self.models_dir / MODELS["hf"]["subdir"]
        hf_dir.mkdir(parents=True)
        (hf_dir / "config.json").write_text("{}")
        torch = self._fake_torch(cuda=True)
        transformers, model, processor, inputs = self._fake_transformers()
        pixel = inputs["pixel_values"]
        service = AutoTagService(models_dir=self.models_dir)

        with _patch_modules({"torch": torch, "transformers": transformers}):
            kind, (loaded, _, device, dtype) = service._load_hf_tagger(
                quantization="none"
            )
            service._tagger = (kind, (loaded, processor, device, dtype))
            service._current_model_type = "hf"
            service.generate_tags(str(self.image_path))
            service.unload_model()

        self.assertEqual((kind, device, dtype), ("hf", "cuda", "bf16"))
        kwargs = (
            transformers.LlavaForConditionalGeneration.from_pretrained.call_args.kwargs
        )
        self.assertNotIn("quantization_config", kwargs)
        pixel.to.assert_called_with("bf16")
        torch.cuda.empty_cache.assert_called_once()

    def test_hf_loader_without_model_raises(self):
        service = AutoTagService(models_dir=self.models_dir)
        with _patch_modules(
            {"torch": self._fake_torch(), "transformers": self._fake_transformers()[0]},
        ):
            with self.assertRaises(RuntimeError):
                service._load_hf_tagger()


class TestUnloadDoesNotImportTorch(EngineTestCase):

    def test_unload_skips_torch_when_not_imported(self):
        _touch_model_files(self.models_dir, "gguf")
        service = self._service(("gguf", FakeGgufModel("x")))
        service.load_model("gguf")

        with _patch_modules({"torch": None}):
            with patch("builtins.__import__", side_effect=ImportError("no torch")):
                service.unload_model()

        self.assertFalse(service.is_model_loaded())


class TestImageConversion(EngineTestCase):

    def test_grayscale_images_are_converted_to_rgb(self):
        _touch_model_files(self.models_dir, "wd14-vit")
        gray = self.tmp / "gray.png"
        Image.new("L", (3, 3), 128).save(gray, "PNG")
        session = FakeOnnxSession([0.9] * 6)
        service = self._service(("wd14", (session, WD14_TAGS)))
        service.load_model("wd14-vit")

        tags = service.generate_tags(str(gray))

        self.assertIn("1girl", tags)
        self.assertEqual(tuple(session.run_calls[0]["input"].shape), (1, 8, 8, 3))


if __name__ == "__main__":
    unittest.main()
