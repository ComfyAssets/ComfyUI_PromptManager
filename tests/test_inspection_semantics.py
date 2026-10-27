"""The declarative inspection node semantics table and its loader (#240, spec 7).

``web/js/inspection-semantics.json`` is the single table. Python validates it at
import and fails loudly; the browser facts tables are checked against its
``facts`` section by ``tests/js/inspection-semantics-sync.test.js``.
"""

import copy
import json
import tempfile
import unittest
import unittest.mock
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
        "lineage": [
            {"class": "VAEEncode", "slot": 0, "strategy": "continue", "input": "pixels"}
        ],
        "stage_inputs": {"shaped": ["positive"], "with_model": ["samples"]},
        "pass_through": {"skip_inputs": ["clip"]},
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
        # A non-empty wildcard replaces the detail prompt (Impact core.py:267).
        self.assertEqual(
            table.samplers["FaceDetailer"],
            {
                "strategy": "detailer-stage",
                "conditioning": pair,
                "image_stage": "image",
                "facts": "ksampler",
                "prompt_overrides": ("wildcard",),
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

        # Combine and concat keep every part in effect (additive); an average
        # blends its parts, so a part may be weighted away.
        self.assertEqual(
            table.composites["ConditioningCombine"],
            {
                "strategy": "combine",
                "inputs": ("conditioning_1", "conditioning_2"),
                "additive": True,
            },
        )
        for name, additive in (
            ("ConditioningConcat", {"additive": True}),
            ("ConditioningAverage", {}),
        ):
            self.assertEqual(
                table.composites[name],
                {
                    "strategy": "combine",
                    "inputs": ("conditioning_to", "conditioning_from"),
                    **additive,
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
        # LoraManager's TriggerWord Toggle outputs the LoRA's trigger words as
        # filtered at run time (option A leaves them out of a PromptManager).
        self.assertEqual(
            table.runtime,
            frozenset({"PromptSearchList", "TriggerWord Toggle (LoraManager)"}),
        )

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
            "XYZPrompt",
        ):
            self.assertNotIn(name, table.all_classes)
        # Existing code adapters stay in code, unchanged.
        for name in ("PromptManager", "PromptManagerPair", "PromptManagerText"):
            self.assertNotIn(name, table.all_classes)

    def test_legacy_parser_ports_are_listed_with_their_source_inputs(self):
        # Task 2.7b (R6): input names and slots read from each node's source;
        # the report lists the file and line for every one.
        table = load_semantics()
        pair = {"positive": "positive", "negative": "negative"}
        for name, image in (
            ("DetailerForEach", "image"),
            ("UltimateSDUpscale", "image"),
            ("UltimateSDUpscaleNoUpscale", "upscaled_image"),
        ):
            entry = dict(table.samplers[name])
            self.assertEqual(
                entry.pop("prompt_overrides", None) is not None,
                name == "DetailerForEach",
                name,
            )
            self.assertEqual(
                entry,
                {
                    "strategy": "detailer-stage",
                    "conditioning": pair,
                    "image_stage": image,
                    "facts": "ksampler",
                },
                name,
            )
        for (name, slot), source in {
            ("ControlNetInpaintingAliMamaApply", 0): "positive",
            ("ControlNetInpaintingAliMamaApply", 1): "negative",
            ("LTXVConditioning", 0): "positive",
            ("LTXVConditioning", 1): "negative",
            ("FluxKontextMultiReferenceLatentMethod", 0): "conditioning",
        }.items():
            self.assertEqual(
                table.conditioning[(name, slot)],
                {"strategy": "passthrough", "input": source},
            )
        self.assertNotIn(
            ("FluxKontextMultiReferenceLatentMethod", 1), table.conditioning
        )
        self.assertEqual(
            table.encoders[("Text to Conditioning", 0)],
            {"strategy": "single", "inputs": ("text",)},
        )
        for name in ("TextInput", "DisplayText"):
            self.assertEqual(
                table.strings[(name, 0)], {"strategy": "literal", "inputs": ("text",)}
            )

    def test_stage_inputs_are_the_one_source_for_both_stage_walks(self):
        # Review I-2: Python's resolver and comfy-metadata.js read these lists here.
        stage_inputs = load_semantics().stage_inputs
        self.assertEqual(
            stage_inputs["shaped"],
            (
                "latent_image",
                "guider",
                "positive",
                "negative",
                "pipe",
                "basic_pipe",
                "detailer_pipe",
                "sampler",
                "sigmas",
                "noise",
                "text_embeds",
                "hyvid_embeds",
            ),
        )
        self.assertEqual(
            stage_inputs["with_model"],
            ("conditioning", "neg_conditioning", "samples", "latent"),
        )
        # The default profile's own sampler-shape names stay a subset of the table's.
        from utils.prompt_pair_resolver import _SAMPLER_LIKE_INPUTS

        self.assertLessEqual(set(_SAMPLER_LIKE_INPUTS), set(stage_inputs["shaped"]))

    def test_facts_section_mirrors_the_browser_tables(self):
        facts = load_semantics().facts
        self.assertEqual(
            facts["configuration_outputs"],
            {
                "RandomNoise": {"kind": "noise", "fields": ("noise_seed",)},
                "KSamplerSelect": {"kind": "sampler", "fields": ("sampler_name",)},
                "BasicScheduler": {"kind": "sigmas", "fields": ("steps", "scheduler")},
                # D8: Flux2Scheduler has no scheduler input; its type names it.
                "Flux2Scheduler": {
                    "kind": "sigmas",
                    "fields": ("steps",),
                    "type_fields": ("scheduler",),
                },
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
            facts["model_passthroughs"],
            (
                "LoraLoader",
                "LoraLoaderModelOnly",
                "ModelSamplingFlux",
                "ModelSamplingAuraFlow",
                "ModelSamplingSD3",
                # Task 2.7b (R9): model in, model out, verified in their sources.
                "Lora Loader (LoraManager)",
                "Power Lora Loader (rgthree)",
                "ModelPatchTorchSettings",
                "CFGNorm",
                "PerturbedAttentionGuidance",
                "CR Apply LoRA Stack",
            ),
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

    def test_table_is_loaded_lazily_and_once(self):
        # Importing the module reads nothing; the inspection path asks for it.
        self.assertFalse(hasattr(semantics, "SEMANTICS"))
        semantics.get_semantics.cache_clear()
        first = semantics.get_semantics()
        self.assertIs(first, semantics.get_semantics())
        self.assertEqual(first, load_semantics())

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


class LineageTableTest(_TableCase):
    """``lineage``: outputs whose image or latent continues one input's."""

    def test_repository_lineage_lists_the_pass_through_nodes(self):
        table = load_semantics()
        self.assertEqual(
            {key: dict(entry) for key, entry in table.lineage.items()},
            {
                ("VAEDecode", 0): {"strategy": "continue", "input": "samples"},
                ("VAEDecodeTiled", 0): {"strategy": "continue", "input": "samples"},
                ("VAEEncode", 0): {"strategy": "continue", "input": "pixels"},
                ("VAEEncodeTiled", 0): {"strategy": "continue", "input": "pixels"},
                ("ImageUpscaleWithModel", 0): {
                    "strategy": "continue",
                    "input": "image",
                },
                ("ImageScale", 0): {"strategy": "continue", "input": "image"},
                ("ImageScaleBy", 0): {"strategy": "continue", "input": "image"},
                ("ImageScaleToTotalPixels", 0): {
                    "strategy": "continue",
                    "input": "image",
                },
                ("LatentUpscale", 0): {"strategy": "continue", "input": "samples"},
                ("LatentUpscaleBy", 0): {"strategy": "continue", "input": "samples"},
                ("Image Filter", 0): {"strategy": "continue", "input": "images"},
                ("Image Filter", 1): {"strategy": "continue", "input": "latents"},
            },
        )
        # Lineage describes no prompt: its classes are not prompt categories.
        self.assertFalse({name for name, _ in table.lineage} & table.all_classes)

    def test_lineage_rows_are_validated(self):
        def row(**changes):
            entry = {
                "class": "VAEEncode",
                "slot": 0,
                "strategy": "continue",
                "input": "pixels",
            }
            entry.update(changes)
            return entry

        cases = (
            ({"lineage": {}}, "must be a list"),
            ({"lineage": ["VAEEncode"]}, "must be an object"),
            ({"lineage": [row(strategy="passthrough")]}, "unknown strategy"),
            ({"lineage": [row(slot=-1)]}, "slot"),
            ({"lineage": [row(slot=True)]}, "slot"),
            ({"lineage": [row(input="")]}, "input name"),
            ({"lineage": [row(note="x")]}, "field"),
            ({"lineage": [row(), row()]}, "duplicate"),
            ({"lineage": [row(**{"class": ""})]}, "class"),
        )
        for index, (change, fragment) in enumerate(cases):
            with self.subTest(case=index):
                doc = _minimal()
                doc.update(change)
                self.assertRejected(doc, fragment)
        doc = _minimal()
        doc["lineage"].append(row(slot=1, input="latents"))
        self.assertEqual(len(self.load_doc(doc).lineage), 2)

    def test_a_lineage_class_may_also_carry_conditioning(self):
        # InpaintModelConditioning-like: conditioning slots, a latent slot.
        doc = _minimal()
        doc["lineage"].append(
            {"class": "FluxGuidance", "slot": 1, "strategy": "continue", "input": "x"}
        )
        self.assertIn(("FluxGuidance", 1), self.load_doc(doc).lineage)

    def test_composite_additive_must_be_a_boolean(self):
        doc = _minimal()
        doc["composites"]["ConditioningCombine"] = {
            "strategy": "combine",
            "inputs": ["conditioning_1", "conditioning_2"],
            "additive": "yes",
        }
        self.assertRejected(doc, "additive must be a boolean")
        doc["composites"]["ConditioningCombine"]["additive"] = True
        table = self.load_doc(doc)
        self.assertIs(table.composites["ConditioningCombine"]["additive"], True)


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
                "encoders": frozenset(
                    {"single", "parts-equal", "pm-compose", "flag-parts"}
                ),
                "strings": frozenset(
                    {"literal", "strip-newlines", "join-multi", "concat"}
                ),
                "savers": frozenset({"comfy-counter", "vhs-counter"}),
                "lineage": frozenset({"continue"}),
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
            (lambda d: d.pop("stage_inputs"), "category"),
            (lambda d: d["stage_inputs"].update(extra=["x"]), "stage_inputs"),
            (lambda d: d["stage_inputs"].pop("with_model"), "stage_inputs"),
            (lambda d: d["stage_inputs"].update(shaped=[]), "input name"),
            (lambda d: d["stage_inputs"].update(shaped="pipe"), "input name"),
            (lambda d: d["stage_inputs"].update(with_model=["positive"]), "both"),
            (lambda d: d["stage_inputs"].update(shaped=["model"]), "model"),
            (lambda d: d.pop("pass_through"), "category"),
            (lambda d: d["pass_through"].update(extra=["x"]), "pass_through"),
            (lambda d: d["pass_through"].update(skip_inputs="clip"), "input name"),
            (lambda d: d["pass_through"].update(skip_inputs=[]), "input name"),
            (
                lambda d: d["pass_through"].update(skip_inputs=["clip", "clip"]),
                "repeated",
            ),
            (
                lambda d: d["pass_through"].update(skip_inputs=["conditioning"]),
                "carries text",
            ),
            (
                lambda d: d["facts"]["configuration_outputs"].update(
                    X={"kind": "sigmas", "fields": ["steps"], "type_fields": []}
                ),
                "input name",
            ),
            (
                lambda d: d["facts"]["configuration_outputs"].update(
                    X={"kind": "sigmas", "fields": ["steps"], "type_fields": ["steps"]}
                ),
                "type_fields",
            ),
            (
                lambda d: d["facts"]["configuration_outputs"].update(
                    X={"kind": "sigmas", "fields": ["steps"], "names": ["x"]}
                ),
                "field",
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

    def test_oversized_table_is_rejected_before_parsing(self):
        text = json.dumps(_minimal()) + " " * semantics.MAX_TABLE_BYTES
        with self.assertRaises(SemanticsError) as caught:
            self.load_text(text)
        self.assertIn("exceeds", str(caught.exception))

    def test_deeply_nested_table_is_a_semantics_error(self):
        # Past the nesting bound, and far past the interpreter's recursion limit.
        for depth in (semantics.MAX_NESTING + 1, 100_000):
            with self.subTest(depth=depth):
                nested = "[" * depth + "]" * depth
                text = json.dumps(_minimal()).replace(
                    '"selectors": {}', '"selectors": ' + nested
                )
                with self.assertRaises(SemanticsError) as caught:
                    self.load_text(text)
                self.assertIn("nest", str(caught.exception))
        self.assertGreater(semantics.MAX_NESTING, _depth(json.loads(TABLE.read_text())))
        # Whichever interpreter limit the parser hits, it is a SemanticsError.
        with unittest.mock.patch.object(
            semantics.json, "loads", side_effect=RecursionError
        ):
            with self.assertRaises(SemanticsError) as caught:
                self.load_doc(_minimal())
        self.assertIn("nests deeper", str(caught.exception))


def _depth(value, level=1):
    children = value.values() if isinstance(value, dict) else value
    if not isinstance(value, (dict, list)):
        return level - 1
    return max([level] + [_depth(item, level + 1) for item in children])


class SamplerOptionsTest(_TableCase):
    """Task 2.7b: optional sampler lists are input names, or the table fails."""

    def test_upstream_verified_ports_are_listed_with_their_source_inputs(self):
        # Not installed here: read from the pinned upstream commits the saved
        # workflows name (the report lists repository, commit, file and line).
        table = load_semantics()
        pair = {"positive": "positive", "negative": "negative"}
        for name, latent, prompt_overrides, fact_overrides in (
            (
                "ClownsharKSampler_Beta",
                "latent_image",
                ("options",),
                ("options", "sigmas"),
            ),
            ("KSampler (Efficient)", "latent_image", ("script",), ("script",)),
            ("ttN KSampler_v2", "latent", ("adv_xyPlot",), ("adv_xyPlot",)),
        ):
            self.assertEqual(
                table.samplers[name],
                {
                    "strategy": "ksampler",
                    "conditioning": pair,
                    "latent": latent,
                    "facts": "ksampler",
                    "prompt_overrides": prompt_overrides,
                    "fact_overrides": fact_overrides,
                },
                name,
            )
        for (name, slot), source in {
            ("SeedVarianceEnhancer", 0): "conditioning",
            ("ApplyInstantIDAdvanced", 1): "positive",
            ("ApplyInstantIDAdvanced", 2): "negative",
        }.items():
            self.assertEqual(
                table.conditioning[(name, slot)],
                {"strategy": "passthrough", "input": source},
            )
        self.assertNotIn(("ApplyInstantIDAdvanced", 0), table.conditioning)
        self.assertEqual(
            table.selectors["Switch conditioning [Crystools]"],
            table.selectors["easy ifElse"],
        )
        self.assertEqual(
            table.encoders[("smZ CLIPTextEncode", 0)],
            {
                "strategy": "flag-parts",
                "inputs": ("text", "text_g", "text_l"),
                "params": {"flag": "with_SDXL"},
            },
        )
        for name, source in (
            ("ttN text", "text"),
            ("CR Text", "text"),
            ("CR Prompt Text", "prompt"),
            ("TextBoxMira", "text"),
        ):
            self.assertEqual(
                table.strings[(name, 0)], {"strategy": "literal", "inputs": (source,)}
            )
            self.assertNotIn((name, 1), table.strings)

    def test_flag_parts_needs_a_flag_and_two_inputs(self):
        doc = _minimal()
        entry = {
            "class": "smZ CLIPTextEncode",
            "slot": 0,
            "strategy": "flag-parts",
            "inputs": ["text", "text_g"],
            "params": {"flag": "with_SDXL"},
        }
        doc["encoders"].append(entry)
        self.assertEqual(
            self.load_doc(doc).encoders[("smZ CLIPTextEncode", 0)]["params"],
            {"flag": "with_SDXL"},
        )
        for change in (
            {"params": {}},
            {"params": {"flag": "x", "other": "y"}},
            {"params": {"flag": ""}},
            {"params": None},
            {"inputs": ["text"]},
            {"compose": True},
        ):
            with self.subTest(change=change):
                broken = _minimal()
                broken["encoders"].append({**entry, **change})
                with self.assertRaises(SemanticsError):
                    self.load_doc(broken)
        broken = _minimal()
        broken["encoders"].append(
            {key: item for key, item in entry.items() if key != "params"}
        )
        with self.assertRaises(SemanticsError):
            self.load_doc(broken)

    def test_prompt_overrides_are_input_names(self):
        doc = _minimal()
        doc["samplers"]["KSampler"]["prompt_overrides"] = ["wildcard"]
        sampler = self.load_doc(doc).samplers["KSampler"]
        self.assertEqual(sampler["prompt_overrides"], ("wildcard",))
        for value in ([], "wildcard", [""], ["a", "a"], [1]):
            with self.subTest(value=value):
                doc["samplers"]["KSampler"]["prompt_overrides"] = value
                with self.assertRaises(SemanticsError):
                    self.load_doc(doc)
        doc["samplers"]["KSampler"].pop("prompt_overrides")
        doc["samplers"]["KSampler"]["fact_overrides"] = ["script", "sigmas"]
        sampler = self.load_doc(doc).samplers["KSampler"]
        self.assertEqual(sampler["fact_overrides"], ("script", "sigmas"))
        doc["samplers"]["KSampler"]["fact_overrides"] = ["script", "script"]
        with self.assertRaises(SemanticsError):
            self.load_doc(doc)
        doc["samplers"]["KSampler"].pop("fact_overrides")
        doc["samplers"]["KSampler"]["overrides"] = ["wildcard"]
        self.assertRejected(doc, "unknown field")


if __name__ == "__main__":
    unittest.main()
