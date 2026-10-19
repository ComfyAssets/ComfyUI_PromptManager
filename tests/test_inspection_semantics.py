"""The declarative inspection node semantics table and its loader (#240, spec 7).

``web/js/inspection-semantics.json`` is the single table. Python validates it at
import and fails loudly; the browser facts tables are checked against its
``facts`` section by ``tests/js/inspection-semantics-sync.test.js``.
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from utils import inspection_semantics as semantics
from utils.inspection_semantics import SemanticsError, load_semantics

REPO = Path(__file__).resolve().parents[1]
TABLE = REPO / "web" / "js" / "inspection-semantics.json"


def _minimal():
    """A small valid table; mutation tests break exactly one thing in a copy."""
    return {
        "schema": 1,
        "samplers": {
            "KSampler": {
                "strategy": "ksampler",
                "conditioning": {"positive": "positive", "negative": "negative"},
                "latent": "latent_image",
                "facts": "ksampler",
            }
        },
        "guiders": {"BasicGuider": {"strategy": "basic", "positive": "conditioning"}},
        "conditioning": [
            {
                "class": "FluxGuidance",
                "slot": 0,
                "strategy": "passthrough",
                "input": "conditioning",
            }
        ],
        "selectors": {},
        "composites": {},
        "encoders": [
            {
                "class": "CLIPTextEncode",
                "slot": 0,
                "strategy": "single",
                "inputs": ["text"],
            }
        ],
        "strings": [],
        "runtime": [],
        "savers": {},
        "non_file_outputs": [],
        "facts": {
            "configuration_outputs": {},
            "model_outputs": {},
            "model_passthroughs": [],
            "scalar_outputs": {},
        },
    }


class _TableCase(unittest.TestCase):
    def load_text(self, text):
        # Windows: the file handle is closed before the directory is removed.
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "semantics.json"
            path.write_text(text, encoding="utf-8")
            return load_semantics(path)

    def load_doc(self, doc):
        return self.load_text(json.dumps(doc))

    def assertRejected(self, doc, fragment):
        with self.assertRaises(SemanticsError) as caught:
            self.load_doc(doc)
        self.assertIn(fragment, str(caught.exception))


class SpecEntriesTest(unittest.TestCase):
    def test_semantics_file_validates_and_lists_spec_entries(self):
        table = load_semantics()

        self.assertEqual(semantics.SEMANTICS_PATH, TABLE)
        self.assertEqual(
            semantics.SEMANTICS_PATH,
            Path(semantics.__file__).resolve().parents[1]
            / "web"
            / "js"
            / "inspection-semantics.json",
        )

        pair = {"positive": "positive", "negative": "negative"}
        for name in ("KSampler", "KSamplerAdvanced"):
            self.assertEqual(
                table.samplers[name],
                {
                    "strategy": "ksampler",
                    "conditioning": pair,
                    "latent": "latent_image",
                    "facts": "ksampler",
                },
            )
        self.assertEqual(
            table.samplers["SamplerCustom"],
            {
                "strategy": "ksampler",
                "conditioning": pair,
                "latent": "latent_image",
                "facts": "custom",
            },
        )
        self.assertEqual(
            table.samplers["SamplerCustomAdvanced"],
            {
                "strategy": "custom-advanced",
                "conditioning": {"guider": "guider"},
                "latent": "latent_image",
                "facts": "custom-advanced",
            },
        )
        # D19: FaceDetailer is a stage over its upstream image, ksampler-shaped facts.
        self.assertEqual(
            table.samplers["FaceDetailer"],
            {
                "strategy": "detailer-stage",
                "conditioning": pair,
                "image_stage": "image",
                "facts": "ksampler",
            },
        )

        self.assertEqual(
            table.guiders["CFGGuider"],
            {"strategy": "cfg", "positive": "positive", "negative": "negative"},
        )
        self.assertEqual(
            table.guiders["BasicGuider"],
            {
                "strategy": "basic",
                "positive": "conditioning",
                "absent_reason": "Guider has no negative input",
            },
        )
        # D17: DualCFGGuider has two positives and stays an unsupported node.
        self.assertNotIn("DualCFGGuider", table.guiders)
        self.assertNotIn("DualCFGGuider", table.all_classes)

        single_input = (
            "FluxGuidance",
            "ReferenceLatent",
            "ConditioningSetArea",
            "ConditioningSetAreaPercentage",
            "ConditioningSetAreaStrength",
            "ConditioningSetMask",
            "ConditioningSetTimestepRange",
            "ControlNetApply",
        )
        for name in single_input:
            self.assertEqual(
                table.conditioning[(name, 0)],
                {"strategy": "passthrough", "input": "conditioning"},
            )
            self.assertNotIn((name, 1), table.conditioning)
        for name in (
            "ControlNetApplyAdvanced",
            "ControlNetApplySD3",
            "InpaintModelConditioning",
            "WanImageToVideo",
        ):
            self.assertEqual(
                table.conditioning[(name, 0)],
                {"strategy": "passthrough", "input": "positive"},
            )
            self.assertEqual(
                table.conditioning[(name, 1)],
                {"strategy": "passthrough", "input": "negative"},
            )
            # Slot 2 (a latent) is not conditioning; spec 7.4 handles origins.
            self.assertNotIn((name, 2), table.conditioning)
        self.assertEqual(
            table.conditioning[("ConditioningZeroOut", 0)],
            {"strategy": "zeroed", "input": "conditioning"},
        )

        self.assertEqual(
            table.selectors["easy ifElse"],
            {
                "strategy": "boolean-select",
                "condition": "boolean",
                "branches": {"true": "on_true", "false": "on_false"},
            },
        )

        self.assertEqual(
            table.composites["ConditioningCombine"],
            {"strategy": "combine", "inputs": ("conditioning_1", "conditioning_2")},
        )
        for name in ("ConditioningConcat", "ConditioningAverage"):
            self.assertEqual(
                table.composites[name],
                {
                    "strategy": "combine",
                    "inputs": ("conditioning_to", "conditioning_from"),
                },
            )

        encoders = {
            ("CLIPTextEncode", 0): ("single", ("text",)),
            ("CLIPTextEncodeSDXL", 0): ("parts-equal", ("text_g", "text_l")),
            ("CLIPTextEncodeFlux", 0): ("parts-equal", ("clip_l", "t5xxl")),
            ("CLIPTextEncodeSD3", 0): ("parts-equal", ("clip_l", "clip_g", "t5xxl")),
            ("TextEncodeQwenImageEdit", 0): ("single", ("prompt",)),
            ("TextEncodeQwenImageEditPlus", 0): ("single", ("prompt",)),
            ("TextEncodeQwenImage21", 0): ("single", ("prompt",)),
            ("TextEncodeQwenImage21", 1): ("single", ("negative_prompt",)),
            # D11: a plain text encoder, no composition.
            ("KikoTextEncode", 0): ("single", ("text",)),
        }
        for key, (strategy, inputs) in encoders.items():
            self.assertEqual(
                table.encoders[key], {"strategy": strategy, "inputs": inputs}, key
            )
        self.assertNotIn(("TextEncodeQwenImage21", 2), table.encoders)

        self.assertEqual(
            table.strings[("StringConstant", 0)],
            {"strategy": "literal", "inputs": ("string",)},
        )
        self.assertEqual(
            table.strings[("StringConstantMultiline", 0)],
            {
                "strategy": "strip-newlines",
                "inputs": ("string",),
                "params": {"flag": "strip_newlines"},
            },
        )
        self.assertEqual(
            table.strings[("JoinStringMulti", 0)],
            {
                "strategy": "join-multi",
                "inputs": ("string_1",),
                "params": {
                    "count": "inputcount",
                    "delimiter": "delimiter",
                    "list_flag": "return_list",
                    "input_prefix": "string_",
                },
            },
        )

        # D18: decided at run time.
        self.assertEqual(table.runtime, frozenset({"PromptSearchList"}))

        comfy = "{name}_{counter:05}_.{ext}"
        for name, image in (
            ("SaveImage", "images"),
            ("SaveAnimatedWEBP", "images"),
            ("SaveAnimatedPNG", "images"),
            ("SaveVideo", "video"),
            ("SaveWEBM", "images"),
        ):
            self.assertEqual(
                table.savers[name],
                {
                    "strategy": "comfy-counter",
                    "prefix_input": "filename_prefix",
                    "image_input": image,
                    "pattern": comfy,
                },
            )
        self.assertEqual(
            table.savers["VHS_VideoCombine"],
            {
                "strategy": "vhs-counter",
                "prefix_input": "filename_prefix",
                "image_input": "images",
                "pattern": "{name}_{counter:05}.{ext}",
            },
        )
        # Until its naming is verified in pinned source it stays terminal-unknown.
        self.assertNotIn("KikoSaveImage", table.all_classes)
        self.assertEqual(
            table.non_file_outputs,
            frozenset(
                {
                    "PreviewImage",
                    "PreviewAny",
                    "SaveImageWebsocket",
                    "Image Comparer (rgthree)",
                }
            ),
        )

        # Not listed this slice: they stay unsupported-node with the class named.
        for name in (
            "ImpactWildcardProcessor",
            "RegexReplace",
            "LoRAfromText",
            "TextBoxMira",
            "XYZPrompt",
        ):
            self.assertNotIn(name, table.all_classes)
        # Existing code adapters stay in code, unchanged.
        for name in ("PromptManager", "PromptManagerPair", "PromptManagerText"):
            self.assertNotIn(name, table.all_classes)

    def test_facts_section_mirrors_the_browser_tables(self):
        facts = load_semantics().facts
        self.assertEqual(
            facts["configuration_outputs"],
            {
                "RandomNoise": {"kind": "noise", "fields": ("noise_seed",)},
                "KSamplerSelect": {"kind": "sampler", "fields": ("sampler_name",)},
                "BasicScheduler": {"kind": "sigmas", "fields": ("steps", "scheduler")},
                "CFGGuider": {"kind": "guider", "fields": ("cfg", "model")},
                "BasicGuider": {"kind": "guider", "fields": ("model",)},
            },
        )
        self.assertEqual(
            facts["model_outputs"],
            {
                "CheckpointLoader": "ckpt_name",
                "CheckpointLoaderSimple": "ckpt_name",
                "unCLIPCheckpointLoader": "ckpt_name",
                "UNETLoader": "unet_name",
            },
        )
        self.assertEqual(
            facts["model_passthroughs"], ("LoraLoader", "LoraLoaderModelOnly")
        )
        self.assertEqual(
            facts["scalar_outputs"],
            {
                "PrimitiveInt": "number",
                "PrimitiveFloat": "number",
                "PrimitiveString": "string",
                "PrimitiveStringMultiline": "string",
            },
        )

    def test_module_loads_the_table_once_at_import(self):
        self.assertEqual(semantics.SEMANTICS, load_semantics())

    def test_loaded_table_is_frozen(self):
        table = load_semantics()
        self.assertIsInstance(table.samplers, MappingProxyType)
        self.assertIsInstance(table.samplers["KSampler"], MappingProxyType)
        with self.assertRaises(TypeError):
            table.samplers["Other"] = {}
        with self.assertRaises(TypeError):
            table.samplers["KSampler"]["latent"] = "x"
        with self.assertRaises(AttributeError):
            table.samplers = {}
        self.assertIsInstance(table.composites["ConditioningCombine"]["inputs"], tuple)


class ValidationTest(_TableCase):
    def test_minimal_table_loads(self):
        table = self.load_doc(_minimal())
        self.assertEqual(table.encoders[("CLIPTextEncode", 0)]["inputs"], ("text",))
        self.assertEqual(table.runtime, frozenset())

    def test_unknown_strategy_rejected(self):
        for category, mutate in (
            ("samplers", lambda d: d["samplers"]["KSampler"].update(strategy="dual")),
            # A known strategy of another category is still unknown here.
            ("conditioning", lambda d: d["conditioning"][0].update(strategy="single")),
            ("encoders", lambda d: d["encoders"][0].update(strategy="guess")),
            ("guiders", lambda d: d["guiders"]["BasicGuider"].update(strategy=None)),
        ):
            with self.subTest(category=category):
                doc = _minimal()
                mutate(doc)
                self.assertRejected(doc, "unknown strategy")

    def test_strategies_are_the_closed_spec_set(self):
        self.assertEqual(
            dict(semantics.STRATEGIES),
            {
                "samplers": frozenset(
                    {"ksampler", "custom-advanced", "detailer-stage"}
                ),
                "guiders": frozenset({"cfg", "basic"}),
                "conditioning": frozenset({"passthrough", "zeroed"}),
                "selectors": frozenset({"boolean-select"}),
                "composites": frozenset({"combine"}),
                "encoders": frozenset({"single", "parts-equal", "pm-compose"}),
                "strings": frozenset(
                    {"literal", "strip-newlines", "join-multi", "concat"}
                ),
                "savers": frozenset({"comfy-counter", "vhs-counter"}),
            },
        )
        with self.assertRaises(TypeError):
            semantics.STRATEGIES["samplers"] = frozenset()

    def test_duplicate_output_key_rejected(self):
        doc = _minimal()
        doc["encoders"].append(copy.deepcopy(doc["encoders"][0]))
        self.assertRejected(doc, "duplicate")

        doc = _minimal()
        doc["conditioning"].append(
            {
                "class": "CLIPTextEncode",
                "slot": 0,
                "strategy": "passthrough",
                "input": "conditioning",
            }
        )
        self.assertRejected(doc, "duplicate")

        doc = _minimal()
        doc["runtime"] = ["KSampler"]
        self.assertRejected(doc, "duplicate")

        doc = _minimal()
        doc["non_file_outputs"] = ["PreviewImage", "PreviewImage"]
        self.assertRejected(doc, "duplicate")

        # JSON objects with a repeated key would silently keep the last one.
        text = json.dumps(_minimal())
        entry = json.dumps(_minimal()["samplers"]["KSampler"])
        text = text.replace(
            '"samplers": {', '"samplers": {"KSampler": ' + entry + ", ", 1
        )
        with self.assertRaises(SemanticsError) as caught:
            self.load_text(text)
        self.assertIn("duplicate", str(caught.exception))

    def test_slot_must_be_nonnegative_int(self):
        for slot in (-1, 1.5, True, "0", None, 2**31):
            with self.subTest(slot=slot):
                doc = _minimal()
                doc["encoders"][0]["slot"] = slot
                self.assertRejected(doc, "slot")
        doc = _minimal()
        del doc["conditioning"][0]["slot"]
        self.assertRejected(doc, "slot")

    def test_missing_input_name_rejected(self):
        cases = (
            lambda d: d["samplers"]["KSampler"]["conditioning"].pop("negative"),
            lambda d: d["samplers"]["KSampler"].pop("latent"),
            lambda d: d["guiders"]["BasicGuider"].update(positive=""),
            lambda d: d["conditioning"][0].pop("input"),
            lambda d: d["encoders"][0].update(inputs=[]),
            lambda d: d["encoders"][0].update(inputs=["text", "text"]),
            lambda d: d["encoders"][0].update(inputs=[3]),
            lambda d: d["encoders"][0].update(inputs="text"),
        )
        for index, mutate in enumerate(cases):
            with self.subTest(case=index):
                doc = _minimal()
                mutate(doc)
                self.assertRejected(doc, "input")

    def test_entry_shape_is_strict(self):
        cases = (
            (lambda d: d.update(extra={}), "category"),
            (lambda d: d.pop("guiders"), "category"),
            (lambda d: d.update(schema=2), "schema"),
            (lambda d: d["samplers"]["KSampler"].update(note="x"), "field"),
            (lambda d: d["samplers"]["KSampler"].update(facts="flux"), "facts"),
            (lambda d: d["guiders"]["BasicGuider"].update(negative="n"), "field"),
            (lambda d: d["encoders"][0].update({"class": ""}), "class"),
            (lambda d: d["facts"].update(extra={}), "facts"),
            (
                lambda d: d["facts"]["scalar_outputs"].update(PrimitiveInt="int"),
                "facts",
            ),
        )
        for index, (mutate, fragment) in enumerate(cases):
            with self.subTest(case=index):
                doc = _minimal()
                mutate(doc)
                self.assertRejected(doc, fragment)

    def test_strategy_specific_requirements(self):
        doc = _minimal()
        doc["samplers"]["X"] = {
            "strategy": "custom-advanced",
            "conditioning": {"positive": "positive", "negative": "negative"},
            "latent": "latent_image",
            "facts": "custom-advanced",
        }
        self.assertRejected(doc, "input")

        doc = _minimal()
        doc["samplers"]["D"] = {
            "strategy": "detailer-stage",
            "conditioning": {"positive": "positive", "negative": "negative"},
            "facts": "ksampler",
        }
        self.assertRejected(doc, "input")

        doc = _minimal()
        doc["guiders"]["G"] = {"strategy": "cfg", "positive": "positive"}
        self.assertRejected(doc, "input")

        doc = _minimal()
        doc["encoders"][0].update(strategy="parts-equal", inputs=["text_g"])
        self.assertRejected(doc, "input")

        doc = _minimal()
        doc["savers"]["S"] = {
            "strategy": "comfy-counter",
            "prefix_input": "filename_prefix",
            "image_input": "images",
            "pattern": "{name}_{counter:05}.{ext}",
        }
        self.assertRejected(doc, "pattern")

        doc = _minimal()
        doc["selectors"]["easy ifElse"] = {
            "strategy": "boolean-select",
            "condition": "boolean",
            "branches": {"true": "on_true"},
        }
        self.assertRejected(doc, "input")

        doc = _minimal()
        doc["strings"].append(
            {
                "class": "JoinStringMulti",
                "slot": 0,
                "strategy": "join-multi",
                "inputs": ["string_1"],
                "params": {"count": "inputcount"},
            }
        )
        self.assertRejected(doc, "params")

    def test_unreadable_table_fails_loudly(self):
        with self.assertRaises(SemanticsError):
            self.load_text("{not json")
        with self.assertRaises(SemanticsError):
            self.load_text("[]")
        with self.assertRaises(SemanticsError):
            self.load_text(
                json.dumps(_minimal()).replace('"schema": 1', '"schema": NaN')
            )
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(SemanticsError):
                load_semantics(Path(root) / "missing.json")


if __name__ == "__main__":
    unittest.main()
