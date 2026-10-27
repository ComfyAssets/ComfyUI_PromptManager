"""Scored top-N tag suggestions for a captured image (#249 B2).

``POST /prompt_manager/image-annotations/suggest`` runs the selected legacy
engine on the private capture (never the current file), behind the one
process-wide model gate shared with the legacy Auto-tag routes. It never
downloads a model and never writes. No real model is loaded: the engine is a
fake service.
"""

import asyncio
import json
import math
import os
import threading
import unittest
import uuid
from unittest.mock import MagicMock, patch

try:
    from test_api_annotations import AnnotationAPITestCase
    from test_api_images import make_png
except ImportError:
    from tests.test_api_annotations import AnnotationAPITestCase
    from tests.test_api_images import make_png

try:  # guarded so a missing module fails assertions, not test loading
    from py import model_gate
except ImportError:
    model_gate = None

from py import config as config_module

AutotagConfig = getattr(config_module, "AutotagConfig", None)

SUGGEST = "/prompt_manager/image-annotations/suggest"
WAIT = 30.0
WD14 = {"general_threshold": 0.35, "character_threshold": 0.85}
LLM = {"instructions": "Generate tags."}
UNAVAILABLE = "Download it in System › Auto-tagging."

SCORED = [("sandstone", 0.98), ("desert", 0.96)] + [
    (f"tag_{i:03d}", round(0.9 - i * 0.005, 4)) for i in range(150)
]


class FakeTagger:
    """Stand-in for ``AutoTagService`` with the suggestion entry point."""

    default_prompt = "server default prompt"
    custom_prompt = "legacy prompt"
    wd14_general_threshold = 0.35
    wd14_character_threshold = 0.85

    def __init__(self):
        self.models_config = {
            "wd14-swinv2": {},
            "wd14-vit": {},
            "gguf": {},
            "hf": {},
        }
        self.downloaded = {name: True for name in self.models_config}
        self.loaded = None
        self.load_calls = []
        self.unload_calls = 0
        self.calls = []
        self.read_paths = []
        self.read_bytes = []
        self.download_model = MagicMock(side_effect=AssertionError("downloaded"))
        self.load_error = None
        self.block = None  # (entered, proceed) events

    def get_models_status(self):
        return {
            name: {"downloaded": self.downloaded[name], "model_path": None}
            for name in self.models_config
        }

    def is_model_loaded(self):
        return self.loaded is not None

    def get_loaded_model_type(self):
        return self.loaded

    def load_model(self, model_type, use_gpu=True):
        self.load_calls.append((model_type, use_gpu))
        if self.load_error is not None:
            raise self.load_error
        self.loaded = model_type
        return True

    def unload_model(self):
        self.unload_calls += 1
        self.loaded = None

    def generate_tags(self, image_path, **kwargs):
        return ["legacy"]

    def generate_tag_suggestions(
        self,
        image_path,
        *,
        limit,
        prompt=None,
        general_threshold=None,
        character_threshold=None,
    ):
        self.read_paths.append(image_path)
        with open(image_path, "rb") as handle:
            self.read_bytes.append(handle.read())
        self.calls.append(
            {
                "limit": limit,
                "prompt": prompt,
                "general_threshold": general_threshold,
                "character_threshold": character_threshold,
                "model": self.loaded,
            }
        )
        if self.block is not None:
            entered, proceed = self.block
            entered.set()
            proceed.wait(WAIT)
        if self.loaded.startswith("wd14"):
            return list(SCORED[:limit])
        return [(name, None) for name, _ in SCORED[:limit]]


class SuggestTestCase(AnnotationAPITestCase):
    async def get_application(self):
        app = await super().get_application()
        self.assertIsNotNone(model_gate, "py.model_gate is not implemented")
        self.assertIsNotNone(AutotagConfig, "AutotagConfig is not implemented")
        self.tagger = FakeTagger()
        patcher = patch("py.autotag.get_autotag_service", return_value=self.tagger)
        patcher.start()
        self.addCleanup(patcher.stop)
        AutotagConfig.SUGGESTION_LIMIT = 10
        self.addCleanup(setattr, AutotagConfig, "SUGGESTION_LIMIT", 10)
        return app

    async def tearDownAsync(self):
        leaked = model_gate.holder() if model_gate is not None else None
        if leaked is not None:
            model_gate.release(leaked)
        await super().tearDownAsync()
        self.assertIsNone(leaked, "the suggest route left the model gate held")

    async def observed(self, path="sub/a.png", size=(8, 6)):
        make_png(self.output_dir / path, size=size)
        return await self.observe_ok(path)

    def suggest_body(self, observed, model_type="wd14-vit", options=None, **changes):
        if options is None:
            options = dict(WD14 if model_type.startswith("wd14") else LLM)
        return {
            "target": dict(observed["target"]),
            "capture_token": observed["capture"]["token"],
            "expected_epoch": observed["library_epoch"],
            "model_type": model_type,
            "options": options,
            **changes,
        }

    async def suggest(self, body):
        response = await self.client.post(SUGGEST, json=body)
        return response.status, await self.json_of(response)

    async def suggest_ok(self, body):
        status, data = await self.suggest(body)
        self.assertEqual(status, 200, data)
        return data

    def capture_path(self, observed):
        return self.api._captures.get(observed["capture"]["token"]).path

    async def wait_until_free(self):
        for _ in range(3000):
            if model_gate.holder() is None:
                return
            await asyncio.sleep(0.01)
        self.fail(f"model gate still held by {model_gate.holder()}")


class TestSuggestions(SuggestTestCase):
    async def test_wd14_suggestions_are_scored_top_n_from_the_capture(self):
        observed = await self.observed()

        body = await self.suggest_ok(self.suggest_body(observed))

        self.assertEqual(
            body["suggestions"][:2],
            [
                {"name": "sandstone", "confidence": 0.98},
                {"name": "desert", "confidence": 0.96},
            ],
        )
        self.assertEqual(len(body["suggestions"]), 10)
        self.assertEqual(body["model_type"], "wd14-vit")
        self.assertEqual(
            body["options"],
            {
                "general_threshold": 0.35,
                "character_threshold": 0.85,
                "use_gpu": True,
                "suggestion_limit": 10,
            },
        )
        self.assertEqual(self.tagger.read_paths, [self.capture_path(observed)])
        self.assertEqual(
            self.tagger.calls[0],
            {
                "limit": 10,
                "prompt": None,
                "general_threshold": 0.35,
                "character_threshold": 0.85,
                "model": "wd14-vit",
            },
        )
        self.assertEqual(self.tagger.load_calls, [("wd14-vit", True)])
        self.tagger.download_model.assert_not_called()
        self.assertIsNone(model_gate.holder())
        self.assert_no_paths(body)

    async def test_suggestions_keep_the_requested_limit(self):
        observed = await self.observed()
        for limit in (1, 10, 100):
            with self.subTest(limit=limit):
                options = {**WD14, "suggestion_limit": limit}
                body = await self.suggest_ok(
                    self.suggest_body(observed, options=options)
                )
                self.assertEqual(len(body["suggestions"]), limit)
                self.assertEqual(body["options"]["suggestion_limit"], limit)
        # Omitted: the persisted autotag.defaults.suggestion_limit.
        AutotagConfig.SUGGESTION_LIMIT = 3
        body = await self.suggest_ok(self.suggest_body(observed))
        self.assertEqual(len(body["suggestions"]), 3)
        self.assertEqual(body["options"]["suggestion_limit"], 3)

    async def test_llm_suggestions_send_instructions_and_null_confidence(self):
        observed = await self.observed()
        self.tagger.custom_prompt = "legacy prompt"
        for model_type in ("gguf", "hf"):
            with self.subTest(model_type=model_type):
                options = {"instructions": f"tags for {model_type}", "use_gpu": False}
                body = await self.suggest_ok(
                    self.suggest_body(observed, model_type, options)
                )
                self.assertEqual(body["model_type"], model_type)
                self.assertEqual(body["options"], {**options, "suggestion_limit": 10})
                self.assertEqual(
                    body["suggestions"][:2],
                    [
                        {"name": "sandstone", "confidence": None},
                        {"name": "desert", "confidence": None},
                    ],
                )
                call = self.tagger.calls[-1]
                self.assertEqual(call["prompt"], f"tags for {model_type}")
                self.assertIsNone(call["general_threshold"])
                self.assertEqual(self.tagger.load_calls[-1], (model_type, False))
        self.assertEqual(self.tagger.custom_prompt, "legacy prompt")

    async def test_loaded_model_is_reused_and_another_is_swapped_in(self):
        observed = await self.observed()
        self.tagger.loaded = "wd14-vit"
        await self.suggest_ok(self.suggest_body(observed))
        self.assertEqual(self.tagger.load_calls, [])
        await self.suggest_ok(self.suggest_body(observed, "wd14-swinv2"))
        self.assertEqual(self.tagger.load_calls, [("wd14-swinv2", True)])
        self.assertEqual(self.tagger.calls[-1]["model"], "wd14-swinv2")

    async def test_suggest_uses_capture_bytes_not_current_file(self):
        observed = await self.observed()
        original = (self.output_dir / "sub" / "a.png").read_bytes()
        make_png(self.output_dir / "sub" / "a.png", size=(9, 9), color=(0, 0, 255))
        self.assertNotEqual((self.output_dir / "sub" / "a.png").read_bytes(), original)

        await self.suggest_ok(self.suggest_body(observed))

        capture_path = self.capture_path(observed)
        self.assertEqual(self.tagger.read_paths, [capture_path])
        self.assertEqual(self.tagger.read_bytes, [original])
        self.assertNotEqual(
            os.path.normcase(capture_path),
            os.path.normcase(str(self.output_dir / "sub" / "a.png")),
        )

    async def test_suggest_writes_nothing(self):
        observed = await self.observed()
        before = self.db_snapshot()
        rows = self.bytes_rows()

        await self.suggest_ok(self.suggest_body(observed))
        await self.suggest_ok(self.suggest_body(observed, "gguf"))

        self.assertEqual(self.db_snapshot(), before)
        self.assertEqual(self.bytes_rows(), rows)
        # The capture stays usable for Save after Generate.
        self.assertIsNotNone(self.api._captures.get(observed["capture"]["token"]))


class TestModelAvailability(SuggestTestCase):
    async def test_suggest_never_downloads_and_reports_model_unavailable(self):
        observed = await self.observed()
        self.tagger.downloaded["wd14-vit"] = False

        status, body = await self.suggest(self.suggest_body(observed))

        self.assertEqual((status, body["code"]), (409, "model-unavailable"))
        self.assertIn(UNAVAILABLE, body["error"])
        self.assertIn("WD14 ViT", body["error"])
        self.tagger.download_model.assert_not_called()
        self.assertEqual(self.tagger.load_calls, [])
        self.assertEqual(self.tagger.calls, [])
        self.assertIsNone(model_gate.holder())

    async def test_load_failure_is_a_generic_error_and_frees_everything(self):
        observed = await self.observed()
        self.tagger.load_error = RuntimeError(f"cannot load {self.output_dir}")

        status, body = await self.suggest(self.suggest_body(observed))

        self.assertEqual((status, body["code"]), (500, "suggest-failed"))
        self.assert_no_paths(body)
        self.assertIsNone(model_gate.holder())
        entry = self.api._captures._entries[observed["capture"]["token"]]
        self.assertEqual(entry.uses, 0)

    async def test_gate_work_that_never_starts_releases_the_capture_pin(self):
        observed = await self.observed()
        loop = asyncio.get_running_loop()
        real = loop.run_in_executor

        def refuse_gate_work(executor, func, *args):
            if getattr(func, "__qualname__", "").startswith("ModelGate.run"):
                raise RuntimeError("cannot schedule new futures after shutdown")
            return real(executor, func, *args)

        with patch.object(loop, "run_in_executor", refuse_gate_work):
            status, body = await self.suggest(self.suggest_body(observed))

        self.assertEqual((status, body["code"]), (500, "suggest-failed"))
        self.assertIsNone(model_gate.holder())
        entry = self.api._captures._entries[observed["capture"]["token"]]
        self.assertEqual(entry.uses, 0)
        self.assertEqual(self.tagger.calls, [])


class TestModelGate(SuggestTestCase):
    async def _blocked_suggest(self, observed, model_type="wd14-vit"):
        entered, proceed = threading.Event(), threading.Event()
        self.tagger.block = (entered, proceed)
        task = asyncio.ensure_future(
            self.client.post(SUGGEST, json=self.suggest_body(observed, model_type))
        )
        await asyncio.get_running_loop().run_in_executor(None, entered.wait, WAIT)
        self.assertTrue(entered.is_set())
        return task, proceed

    async def test_suggest_reports_busy_while_legacy_batch_runs(self):
        observed = await self.observed()
        prompt_id = self._save_prompt("batch prompt")
        self.api.db.link_image_to_prompt(prompt_id, str(self.output_dir / "sub/a.png"))
        started, proceed = threading.Event(), threading.Event()

        def slow_generate(image_path, **kwargs):
            started.set()
            proceed.wait(WAIT)
            return ["batch"]

        self.tagger.generate_tags = slow_generate
        batch = asyncio.ensure_future(
            self.client.post("/prompt_manager/autotag/start?model_type=wd14-vit")
        )
        try:
            await asyncio.get_running_loop().run_in_executor(None, started.wait, WAIT)
            status, body = await self.suggest(self.suggest_body(observed, "gguf"))
            self.assertEqual((status, body["code"]), (409, "model-busy"))
            self.assertEqual(self.tagger.calls, [])
            self.assertEqual(self.tagger.loaded, "wd14-vit")  # not swapped
        finally:
            proceed.set()
            response = await batch
            await response.read()
        await self.wait_until_free()
        await self.suggest_ok(self.suggest_body(observed))

    async def test_legacy_unload_returns_model_busy_while_native_generate_runs(self):
        observed = await self.observed()
        task, proceed = await self._blocked_suggest(observed)
        try:
            response = await self.client.post("/prompt_manager/autotag/unload")
            self.assertEqual(response.status, 409)
            self.assertEqual((await response.json())["code"], "model-busy")
            response = await self.client.post(
                "/prompt_manager/autotag/single",
                json={"root_index": 0, "path": "sub/a.png", "model_type": "gguf"},
            )
            self.assertEqual(response.status, 409)
            status, body = await self.suggest(self.suggest_body(observed, "gguf"))
            self.assertEqual((status, body["code"]), (409, "model-busy"))
            self.assertEqual(self.tagger.unload_calls, 0)
            self.assertEqual(self.tagger.loaded, "wd14-vit")
        finally:
            proceed.set()
            response = await task
        self.assertEqual(response.status, 200)
        await self.wait_until_free()
        self.assertEqual([call["model"] for call in self.tagger.calls], ["wd14-vit"])

    async def test_gate_held_after_client_disconnect_until_executor_finishes(self):
        observed = await self.observed()
        token = observed["capture"]["token"]
        task, proceed = await self._blocked_suggest(observed)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        try:
            # The client is gone; the inference still runs, so the gate and
            # the capture pin stay until the worker thread exits.
            self.assertIsNotNone(model_gate.holder())
            response = await self.client.post("/prompt_manager/autotag/unload")
            self.assertEqual(response.status, 409)
            self.assertEqual(self.api._captures._entries[token].uses, 1)
        finally:
            proceed.set()
        await self.wait_until_free()
        for _ in range(3000):
            if self.api._captures._entries[token].uses == 0:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.api._captures._entries[token].uses, 0)
        response = await self.client.post("/prompt_manager/autotag/unload")
        self.assertEqual(response.status, 200)


class TestRequestValidation(SuggestTestCase):
    async def test_strict_option_validation(self):
        observed = await self.observed()
        bad_options = {
            "wd14-vit": [
                {**WD14, "general_threshold": True},
                {**WD14, "general_threshold": math.nan},
                {**WD14, "general_threshold": math.inf},
                {**WD14, "general_threshold": "0.3"},
                {**WD14, "general_threshold": 1.5},
                {**WD14, "character_threshold": -0.1},
                {"general_threshold": 0.35},
                {**WD14, "instructions": "x"},
                {**WD14, "unknown": 1},
                {**WD14, "use_gpu": "yes"},
                {**WD14, "suggestion_limit": 0},
                {**WD14, "suggestion_limit": 101},
                {**WD14, "suggestion_limit": 2.5},
                {**WD14, "suggestion_limit": "10"},
                {**WD14, "suggestion_limit": True},
            ],
            "gguf": [
                {**LLM, "general_threshold": 0.35},
                {**LLM, "character_threshold": 0.85},
                {"instructions": "x" * 4001},
                {"instructions": "\ud800"},  # not encodable as UTF-8
                {"instructions": "   "},
                {"instructions": 5},
                {},
                {**LLM, "suggestion_limit": 0},
            ],
        }
        for model_type, cases in bad_options.items():
            for options in cases:
                with self.subTest(model_type=model_type, options=repr(options)[:80]):
                    status, body = await self.suggest(
                        self.suggest_body(observed, model_type, options)
                    )
                    self.assertEqual(status, 400, body)
        for body in (
            {**self.suggest_body(observed), "extra": 1},
            {k: v for k, v in self.suggest_body(observed).items() if k != "options"},
            self.suggest_body(observed, options=[]),
            self.suggest_body(observed, model_type="ollama"),
            self.suggest_body(observed, model_type=7, options=dict(WD14)),
            self.suggest_body(observed, capture_token="../x"),
            self.suggest_body(observed, expected_epoch="nope"),
            self.suggest_body(observed, target={"root_index": 0}),
        ):
            with self.subTest(body=json.dumps(body)[:120]):
                status, _ = await self.suggest(body)
                self.assertEqual(status, 400)
        # The edges that are allowed.
        await self.suggest_ok(
            self.suggest_body(
                observed,
                options={
                    "general_threshold": 0,
                    "character_threshold": 1,
                    "suggestion_limit": 100,
                },
            )
        )
        await self.suggest_ok(
            self.suggest_body(observed, "hf", options={"instructions": "x" * 4000})
        )
        self.assertEqual(len(self.tagger.calls), 2)
        self.tagger.download_model.assert_not_called()

    async def test_capture_token_must_match_target_and_epoch(self):
        observed = await self.observed()
        make_png(self.output_dir / "sub" / "b.png")
        other = await self.observe_ok("sub/b.png")

        status, body = await self.suggest(
            self.suggest_body(observed, capture_token="x" * 43)
        )
        self.assertEqual((status, body["code"]), (409, "capture-expired"))
        status, body = await self.suggest(
            self.suggest_body(observed, capture_token=other["capture"]["token"])
        )
        self.assertEqual((status, body["code"]), (409, "capture-expired"))
        status, body = await self.suggest(
            self.suggest_body(observed, expected_epoch=str(uuid.uuid4()))
        )
        self.assertEqual((status, body["code"]), (409, "storage-changed"))
        self.assertEqual(body["library_epoch"], self.epoch)
        status, body = await self.suggest(
            self.suggest_body(
                observed, target={**observed["target"], "root_scope": "0" * 64}
            )
        )
        self.assertEqual((status, body["code"]), (409, "source-changed"))
        self.api._captures.release(observed["capture"]["token"])
        status, body = await self.suggest(self.suggest_body(observed))
        self.assertEqual((status, body["code"]), (409, "capture-expired"))
        self.assertEqual(self.tagger.calls, [])
        for token in (observed, other):
            entry = self.api._captures._entries.get(token["capture"]["token"])
            self.assertTrue(entry is None or entry.uses == 0)

    async def test_video_and_oversized_dimensions_unsupported_media(self):
        (self.output_dir / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" * 4)
        video = await self.observe_ok("clip.mp4")
        self.assertFalse(video["capture"]["generate"]["supported"])
        big = await self.observed("big.png", size=(4097, 1))
        self.assertFalse(big["capture"]["generate"]["supported"])

        for observed in (video, big):
            status, body = await self.suggest(self.suggest_body(observed))
            self.assertEqual((status, body["code"]), (415, "unsupported-media"))
            self.assertEqual(body["error"], observed["capture"]["generate"]["reason"])
        self.assertEqual(self.tagger.calls, [])
        self.assertEqual(self.tagger.load_calls, [])
        self.assertIsNone(model_gate.holder())


if __name__ == "__main__":
    unittest.main()
