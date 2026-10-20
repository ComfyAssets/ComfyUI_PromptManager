"""Legacy parser rules ported into the inspection resolver (#240, Task 2.7b).

Every graph here is synthetic (neutral texts, generic names). Each class names
the rule from the parser reconciliation (R1, R3, ...) it ports. The default
(ingestion) profile must stay exactly as it was, so each rule also checks that
``DEFAULT_PROFILE`` ignores it.
"""

import io
import json
import tempfile
import unittest
import unittest.mock
import zlib
from pathlib import Path

from PIL import Image

from utils import prompt_pair_resolver as resolver
from utils.prompt_inspector import (
    DUPLICATE_ROLE_BYTES,
    MetadataReadError,
    inspect_prompt_evidence,
    read_inspection_file,
)
from utils.prompt_pair_resolver import resolve_prompt_pair

try:
    from tests import inspection_parity as parity
except ImportError:  # discovered with ``-s tests``
    import inspection_parity as parity


def _inspect(metadata: dict, **options) -> dict:
    return resolve_prompt_pair(metadata, profile=resolver.INSPECTION_PROFILE, **options)


def _texts(result: dict) -> tuple:
    return tuple(
        result[role]["text"] if isinstance(result[role], dict) else None
        for role in ("positive", "negative")
    )


def _pm_encoder(text: str) -> dict:
    """A PromptManager node whose API class was rewritten to CLIPTextEncode."""
    return {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": text,
            "category": "",
            "tags": "",
            "search_text": "",
            "clip": ["4", 1],
        },
    }


def _overwritten(widgets=("a cat", "a dog", "a bird"), api_text="a bird") -> dict:
    """Pre-3.2.3 shape: three PromptManager nodes, every API ``text`` overwritten.

    KSampler 3 reads node 17 (widget A) for its positive; the saved API text of
    17, 113 and 119 is the last-run node's prompt (C) for all three.
    """
    prompt = parity.build("f02")["prompt"]
    prompt["3"]["inputs"]["positive"] = ["17", 0]
    del prompt["6"]
    for node_id in ("17", "113", "119"):
        prompt[node_id] = _pm_encoder(api_text)
    workflow = {
        "nodes": [
            {"id": int(node_id), "type": "PromptManager", "widgets_values": [widget]}
            for node_id, widget in zip(("17", "113", "119"), widgets)
        ]
        + [{"id": 7, "type": "CLIPTextEncode", "widgets_values": [parity.NEG]}],
        "links": [],
    }
    return {"prompt": prompt, "workflow": workflow}


class TestOverwrittenPromptManagerText(unittest.TestCase):
    """R1 (comfy-metadata.js:52-86): never resolve to the overwritten text."""

    def test_overwritten_text_is_refused_not_resolved(self):
        result = _inspect(_overwritten())
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "malformed")
        self.assertEqual(result["reason"], resolver.OVERWRITTEN_PROMPT_REASON)
        self.assertIsNone(result["positive"])

    def test_the_inspector_passes_the_workflow_to_the_resolver(self):
        metadata = _overwritten()
        evidence = inspect_prompt_evidence(
            {
                "prompt": json.dumps(metadata["prompt"]),
                "workflow": json.dumps(metadata["workflow"]),
            }
        )
        self.assertEqual(evidence["resolution"]["reason_code"], "malformed")
        self.assertEqual(evidence["prompts"]["positive"]["status"], "unresolved")
        self.assertNotEqual(evidence["prompts"]["positive"]["text"], "a bird")
        self.assertEqual(
            evidence["prompts"]["positive"]["message"],
            "Prompt metadata in this file is malformed.",
        )

    def test_untouched_shapes_still_resolve(self):
        cases = {
            # One PromptManager node, or several whose widgets agree.
            "single": ("a bird",),
            "agreeing widgets": ("a bird", "a bird", "a bird"),
        }
        for label, widgets in cases.items():
            with self.subTest(label):
                metadata = _overwritten()
                metadata["workflow"]["nodes"] = [
                    node
                    for node in metadata["workflow"]["nodes"]
                    if node["type"] != "PromptManager"
                ] + [
                    {"id": node_id, "type": "PromptManager", "widgets_values": [text]}
                    for node_id, text in zip((17, 113, 119), widgets)
                ]
                self.assertEqual(_inspect(metadata)["state"], "resolved")
        # Distinct API texts are what ran, whatever the widgets say.
        metadata = _overwritten()
        metadata["prompt"]["113"]["inputs"]["text"] = "a dog"
        metadata["prompt"]["119"]["inputs"]["text"] = "a cat"
        metadata["prompt"]["17"]["inputs"]["text"] = "a fish"
        self.assertEqual(_texts(_inspect(metadata)), ("a fish", parity.NEG))
        # A PromptManagerPair widget is its positive and negative: not the signature.
        metadata = _overwritten()
        for node in metadata["workflow"]["nodes"]:
            node["type"] = node["type"].replace("PromptManager", "PromptManagerPair")
        self.assertEqual(_inspect(metadata)["state"], "resolved")
        # No workflow, or a malformed one, cannot show the signature.
        orphans = {
            "nodes": [{"id": 5, "type": "PromptManager", "widgets_values": ["a"]}]
        }
        for workflow in (None, "nodes", {"nodes": "x"}, {"nodes": [1, None]}, orphans):
            with self.subTest(workflow=workflow):
                metadata = _overwritten()
                metadata["workflow"] = workflow
                self.assertEqual(_inspect(metadata)["state"], "resolved")

    def test_an_untouched_branch_beside_overwritten_nodes_resolves(self):
        # Only the overwritten nodes are refused: the negative is not one.
        metadata = _overwritten()
        metadata["prompt"]["3"]["inputs"]["positive"] = ["7", 0]
        self.assertEqual(_texts(_inspect(metadata)), (parity.NEG, parity.NEG))

    def test_overwritten_nodes_inside_subgraphs_are_refused(self):
        # Review M-1: subgraph nodes run as "<instance>:<inner>" API IDs.
        def nested(depth: int = 1) -> dict:
            metadata = _overwritten()
            prompt, workflow = metadata["prompt"], metadata["workflow"]
            prefix = ":".join(["50", "60", "70"][:depth]) + ":"
            for node_id in ("17", "113", "119"):
                prompt[prefix + node_id] = prompt.pop(node_id)
            prompt["3"]["inputs"]["positive"] = [prefix + "17", 0]
            inner = [n for n in workflow["nodes"] if n["type"] == "PromptManager"]
            outer = [n for n in workflow["nodes"] if n["type"] != "PromptManager"]
            subgraphs = []
            for level in range(depth, 0, -1):
                nodes = (
                    inner
                    if level == depth
                    else [{"id": [50, 60, 70][level], "type": f"sub-{level + 1}"}]
                )
                subgraphs.append({"id": f"sub-{level}", "nodes": nodes})
            metadata["workflow"] = {
                "nodes": outer + [{"id": 50, "type": "sub-1", "widgets_values": []}],
                "links": [],
                "definitions": {"subgraphs": subgraphs},
            }
            return metadata

        for depth in (1, 2, 3):
            with self.subTest(depth=depth):
                for anchor in (None, "9"):
                    result = _inspect(nested(depth), output_node_id=anchor)
                    self.assertEqual(
                        result["reason"], resolver.OVERWRITTEN_PROMPT_REASON, anchor
                    )
        # A subgraph definition with no instance runs nothing.
        metadata = nested()
        metadata["workflow"]["nodes"] = metadata["workflow"]["nodes"][:-1]
        self.assertEqual(_inspect(metadata)["state"], "resolved")
        # Malformed definitions never raise.
        for definitions in ("x", {"subgraphs": "x"}, {"subgraphs": [1, {"id": 3}]}):
            metadata = nested()
            metadata["workflow"]["definitions"] = definitions
            self.assertEqual(_inspect(metadata)["state"], "resolved", definitions)
        # A self-referencing subgraph is bounded.
        metadata = nested()
        metadata["workflow"]["definitions"]["subgraphs"].append(
            {"id": "loop", "nodes": [{"id": 1, "type": "loop"}]}
        )
        metadata["workflow"]["nodes"].append({"id": 99, "type": "loop"})
        self.assertEqual(
            _inspect(metadata)["reason"], resolver.OVERWRITTEN_PROMPT_REASON
        )

    def test_object_widget_values_name_the_text(self):
        metadata = _overwritten()
        for node in metadata["workflow"]["nodes"]:
            node["widgets_values"] = {"text": node["widgets_values"][0]}
        self.assertEqual(
            _inspect(metadata)["reason"], resolver.OVERWRITTEN_PROMPT_REASON
        )

    def test_the_workflow_is_parsed_only_for_prompt_manager_graphs(self):
        # Review M-4: no PromptManager-like node, no workflow parse.

        from utils import prompt_inspector

        prompt = parity.build("f02")["prompt"]
        with unittest.mock.patch.object(
            prompt_inspector,
            "_parsed_workflow",
            wraps=prompt_inspector._parsed_workflow,
        ) as parsed:
            inspect_prompt_evidence(
                {"prompt": json.dumps(prompt), "workflow": json.dumps({"nodes": []})}
            )
            self.assertEqual(parsed.call_count, 0)
            metadata = _overwritten()
            inspect_prompt_evidence(
                {
                    "prompt": json.dumps(metadata["prompt"]),
                    "workflow": json.dumps(metadata["workflow"]),
                }
            )
            self.assertEqual(parsed.call_count, 1)

    def test_default_profile_ignores_the_workflow(self):
        result = resolve_prompt_pair(_overwritten())
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_texts(result), ("a bird", parity.NEG))


def _historical(class_type: str, slot: int, inputs: dict, kind: str) -> dict:
    """``f02`` whose positive reads ``class_type`` output ``slot`` (node 10).

    A conditioning output feeds the sampler directly; a string output feeds a
    CLIPTextEncode (node 6).
    """
    prompt = parity.build("f02")["prompt"]
    prompt["10"] = {"class_type": class_type, "inputs": {"clip": ["4", 1], **inputs}}
    if kind == "conditioning":
        prompt["3"]["inputs"]["positive"] = ["10", slot]
    else:
        prompt["6"]["inputs"]["text"] = ["10", slot]
    return {"prompt": prompt}


class TestHistoricalPromptManagerNodes(unittest.TestCase):
    """R7: this repository's node classes up to 07a5af3, with their own join."""

    COMPOSED = {"text": " a cat ", "prepend_text": " trig ", "append_text": "sky "}

    def test_v2_outputs_and_comma_join(self):
        inputs = {**self.COMPOSED, "negative_text": " bad hands "}
        for slot, kind, expected in (
            (0, "conditioning", "trig, a cat, sky"),
            (1, "conditioning", "bad hands"),
            (2, "string", "trig, a cat, sky"),
            (3, "string", "bad hands"),
        ):
            with self.subTest(slot=slot):
                result = _inspect(_historical("PromptManagerV2", slot, inputs, kind))
                self.assertEqual(result["state"], "resolved")
                self.assertEqual(result["positive"]["text"], expected)

    def test_comma_rule_matches_the_historical_code(self):
        cases = (
            ({"text": "a cat", "prepend_text": "trig,"}, "trig,a cat"),
            ({"text": "a cat,", "append_text": "sky"}, "a cat,sky"),
            ({"text": "", "prepend_text": "trig", "append_text": "sky"}, "trig, sky"),
            ({"text": "  ", "prepend_text": "  ", "append_text": "sky"}, "sky"),
            ({"text": "a cat"}, "a cat"),
            # Review M-5: an explicit null part is skipped, as ``if prepend_text:``.
            ({"text": "a cat", "prepend_text": None, "append_text": None}, "a cat"),
        )
        for inputs, expected in cases:
            with self.subTest(inputs=inputs):
                result = _inspect(_historical("PromptManagerV2", 2, inputs, "string"))
                self.assertEqual(result["positive"]["text"], expected)

    def test_single_role_classes(self):
        cases = (
            ("PromptManagerPositive", 0, "conditioning", "trig, a cat, sky"),
            ("PromptManagerPositive", 1, "string", "trig, a cat, sky"),
            ("PromptManagerDetailer", 0, "conditioning", "trig, a cat, sky"),
            ("PromptManagerDetailer", 1, "string", "trig, a cat, sky"),
            ("PromptManagerV2Text", 0, "string", "trig, a cat, sky"),
            ("PromptManagerPositiveText", 0, "string", "trig, a cat, sky"),
            ("PromptManagerNegativeText", 0, "string", "trig, a cat, sky"),
            # The negative-only encoder never composes.
            ("PromptManagerNegative", 0, "conditioning", "a cat"),
            ("PromptManagerNegative", 1, "string", "a cat"),
        )
        for class_type, slot, kind, expected in cases:
            with self.subTest(node=class_type, slot=slot):
                result = _inspect(_historical(class_type, slot, self.COMPOSED, kind))
                self.assertEqual(result["state"], "resolved")
                self.assertEqual(result["positive"]["text"], expected)
        result = _inspect(
            _historical(
                "PromptManagerV2Text",
                1,
                {"text": "a cat", "negative_text": " bad "},
                "string",
            )
        )
        self.assertEqual(result["positive"]["text"], "bad")

    def test_optional_negative_defaults_to_empty(self):
        result = _inspect(
            _historical("PromptManagerV2", 0, {"text": "a cat"}, "conditioning")
        )
        self.assertEqual(result["positive"]["text"], "a cat")
        prompt = _historical("PromptManagerV2", 0, {"text": "a cat"}, "conditioning")
        prompt["prompt"]["3"]["inputs"]["negative"] = ["10", 1]
        self.assertEqual(_texts(_inspect(prompt)), ("a cat", ""))

    def test_unlisted_outputs_and_kinds_are_not_guessed(self):
        # PromptManagerDetailer output 2 is an INT prompt id.
        result = _inspect(
            _historical("PromptManagerDetailer", 2, self.COMPOSED, "string")
        )
        self.assertEqual(result["reason_code"], "unsupported-node")
        # A STRING output read as conditioning is a malformed link.
        result = _inspect(
            _historical("PromptManagerV2", 2, self.COMPOSED, "conditioning")
        )
        self.assertEqual(result["reason_code"], "malformed")

    def test_linked_parts_are_traced(self):
        source = _historical(
            "PromptManagerV2",
            0,
            {"text": ["11", 0], "prepend_text": ["12", 0]},
            "conditioning",
        )
        source["prompt"]["11"] = {
            "class_type": "PrimitiveString",
            "inputs": {"value": "a cat"},
        }
        source["prompt"]["12"] = {
            "class_type": "PrimitiveString",
            "inputs": {"value": "trig"},
        }
        result = _inspect(source)
        self.assertEqual(result["positive"]["text"], "trig, a cat")
        self.assertEqual(
            [step["node_id"] for step in result["positive"]["provenance"]["path"]],
            ["10", "12", "11"],
        )

    def test_overwritten_historical_nodes_are_still_refused(self):
        metadata = _overwritten()
        for node_id in ("17", "113", "119"):
            metadata["prompt"][node_id]["class_type"] = "PromptManagerV2"
        for node in metadata["workflow"]["nodes"]:
            if node["type"] == "PromptManager":
                node["type"] = "PromptManagerV2"
        self.assertEqual(_inspect(metadata)["reason_code"], "malformed")

    def test_default_profile_keeps_them_unsupported(self):
        result = resolve_prompt_pair(
            _historical("PromptManagerV2", 0, self.COMPOSED, "conditioning")
        )
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason"], "Unsupported node semantics or output slot")


FOOTER = "Steps: 20, Sampler: Euler, CFG scale: 7, Seed: 5"


def _parameters(text: str, *, legacy: bool = False) -> dict:
    profile = resolver.DEFAULT_PROFILE if legacy else resolver.INSPECTION_PROFILE
    return resolve_prompt_pair({"parameters": text}, profile=profile)


class TestA1111Text(unittest.TestCase):
    """R4 and R5 (#165, py/api/__init__.py:738-750) for inspection."""

    def test_text_starting_with_the_negative_delimiter_has_no_positive(self):
        for text in (
            f"Negative prompt: bad\n{FOOTER}",
            f"\nNegative prompt: bad\n{FOOTER}",
        ):
            with self.subTest(text=text):
                result = _parameters(text)
                self.assertEqual(result["state"], "unresolved")
                self.assertEqual(result["reason"], resolver.EMPTY_POSITIVE_REASON)
                self.assertIsNone(result["positive"])
        # Two delimiters, one at the start, are still two.
        result = _parameters(f"Negative prompt: a\nNegative prompt: b\n{FOOTER}")
        self.assertEqual(result["state"], "ambiguous")

    def test_dynamic_prompts_template_lines_follow_the_footer(self):
        base = f"a cat\nNegative prompt: bad\n{FOOTER}"
        for trailer in (
            "\nTemplate: a {cat|dog}",
            "\nTemplate: a {cat|dog}\nNegative Template: bad",
            "\nNegative Template: bad",
            "\nTemplate: a {cat|dog}\nNegative Template: bad\n",
        ):
            with self.subTest(trailer=trailer):
                result = _parameters(base + trailer)
                self.assertEqual(result["state"], "resolved")
                self.assertEqual(_texts(result), ("a cat", "bad"))
        for trailer in (
            "\nTemplate: a\nTemplate: b",
            "\nNegative Template: bad\nTemplate: a",
            "\nTemplate: a\nmore template text",
            "\ntrailing",
        ):
            with self.subTest(trailer=trailer):
                result = _parameters(base + trailer)
                self.assertEqual(result["reason_code"], "malformed")

    def test_footer_with_only_steps(self):
        result = _parameters("a cat\nNegative prompt: bad\nSteps: 20")
        self.assertEqual(_texts(result), ("a cat", "bad"))
        result = _parameters("a cat\nSteps: 20\nTemplate: a {cat|dog}")
        self.assertEqual((result["state"], _texts(result)[0]), ("absent", "a cat"))
        # A full footer wins over a bare ``Steps: N`` line in the prompt text.
        result = _parameters(f"a cat\nSteps: 3\nNegative prompt: bad\n{FOOTER}")
        self.assertEqual(_texts(result), ("a cat\nSteps: 3", "bad"))
        result = _parameters("a cat\nSteps: 3\nNegative prompt: bad\nSteps: 20")
        self.assertEqual(result["reason_code"], "malformed")

    def test_default_profile_keeps_the_historical_split(self):
        result = _parameters(f"Negative prompt: bad\n{FOOTER}", legacy=True)
        self.assertEqual(_texts(result), ("Negative prompt: bad", None))
        for text in (
            f"a cat\nNegative prompt: bad\n{FOOTER}\nTemplate: a {{cat|dog}}",
            "a cat\nNegative prompt: bad\nSteps: 20",
        ):
            with self.subTest(text=text):
                result = _parameters(text, legacy=True)
                self.assertEqual(
                    result["reason"], "A1111 settings footer is not unique"
                )


def _chunk(keyword: str, text: str, kind: bytes = b"tEXt") -> bytes:
    body = text.encode("latin-1")
    if kind == b"zTXt":
        body = b"\0" + zlib.compress(body)
    elif kind == b"iTXt":
        body = b"\0\0\0\0" + text.encode("utf-8")
    return parity._raw_chunk(kind, keyword.encode("latin-1") + b"\0" + body)


def _batch(index: int, positive: str = "a cat", seed: int = 5) -> str:
    return (
        f"{positive}\nNegative prompt: bad\nSteps: 20, Sampler: Euler, "
        f"CFG scale: 7, Seed: {seed}, Batch index: {index}, Batch size: 12"
    )


class TestBatchIndexDuplicates(unittest.TestCase):
    """R3: an accumulating batch saver repeats ``parameters`` per batch item."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def png(self, chunks, name="batch.png"):
        buffer = io.BytesIO()
        with Image.new("RGB", (4, 4), (40, 80, 120)) as image:
            image.save(buffer, format="PNG")
        data = buffer.getvalue()
        iend = data.rindex(b"IEND") - 4
        path = self.directory / name
        path.write_bytes(data[:iend] + b"".join(chunks) + data[iend:])
        return path

    def test_copies_differing_only_in_batch_index_read_as_the_last(self):
        cases = {
            "same length": [0, 1, 2],
            "growing length": [8, 9, 10, 11],
            "any order": [3, 0],
            "repeat of an earlier copy": [0, 1, 0],
        }
        for label, indexes in cases.items():
            with self.subTest(label):
                path = self.png([_chunk("parameters", _batch(i)) for i in indexes])
                read = read_inspection_file(path)
                self.assertEqual(read.metadata["parameters"], _batch(indexes[-1]))
                self.assertEqual(read.record_formats["parameters"], "original-text")
                evidence = inspect_prompt_evidence(read.metadata)
                self.assertEqual(evidence["resolution"]["state"], "resolved")
                self.assertEqual(evidence["prompts"]["positive"]["text"], "a cat")
        # Encodings may differ; the decoded copies are compared.
        path = self.png(
            [
                _chunk("parameters", _batch(0)),
                _chunk("parameters", _batch(1), b"zTXt"),
                _chunk("parameters", _batch(2), b"iTXt"),
            ]
        )
        self.assertEqual(read_inspection_file(path).metadata["parameters"], _batch(2))

    def test_any_other_difference_is_still_a_conflict(self):
        cases = {
            "positive": [_batch(0), _batch(1, positive="a dog")],
            "seed": [_batch(0), _batch(1, seed=6)],
            "no batch index": [_batch(0), _batch(0).replace(", Batch index: 0", "")],
            "two batch indexes": [_batch(0), _batch(1) + ", Batch index: 2"],
            "prose": [_batch(0), _batch(1).replace("a cat", "a cat Batch index: 1")],
            # Review I-1: the field is matched only in the settings footer.
            "prompt field": [
                _batch(0).replace("a cat", "a cat, Batch index: 0"),
                _batch(0).replace("a cat", "a cat, Batch index: 1"),
            ],
            "prompt only": [
                "a cat, Batch index: 0\nNegative prompt: bad\nSteps: 20, Seed: 5",
                "a cat, Batch index: 1\nNegative prompt: bad\nSteps: 20, Seed: 5",
            ],
            "negative field": [
                _batch(0).replace("bad", "bad, Batch index: 0"),
                _batch(1).replace("bad", "bad, Batch index: 1"),
            ],
            "no footer": [
                "a cat\nBatch index: 0, Batch size: 2",
                "a cat\nBatch index: 1, Batch size: 2",
            ],
        }
        for label, texts in cases.items():
            with self.subTest(label):
                path = self.png([_chunk("parameters", text) for text in texts])
                with self.assertRaisesRegex(
                    MetadataReadError, "Duplicate prompt metadata"
                ):
                    read_inspection_file(path)
        # The rule is only for ``parameters``: prompt graphs stay byte-identical.
        prompt = json.dumps(parity.build("f02")["prompt"])
        path = self.png([_chunk("prompt", prompt), _chunk("prompt", prompt + " ")])
        with self.assertRaisesRegex(MetadataReadError, "Duplicate prompt metadata"):
            read_inspection_file(path)

    def test_decoded_copies_are_bounded(self):
        # Small compressed copies must not decode unbounded text.
        text = _batch(0) + "," + " " * (DUPLICATE_ROLE_BYTES // 8)
        chunks = [
            _chunk("parameters", text.replace("index: 0", f"index: {i}"), b"zTXt")
            for i in range(10)
        ]
        path = self.png(chunks)
        with self.assertRaisesRegex(MetadataReadError, "exceeds inspection limits"):
            read_inspection_file(path)
        # The kept copy still fits the role budget when a later one is longer.
        from utils.prompt_inspector import ROLE_METADATA_BYTES

        first = _batch(9)
        first += "," + " " * (ROLE_METADATA_BYTES - len(first) - 1)
        later = first.replace("index: 9", "index: 10")
        for copy in (later, later + " " * ROLE_METADATA_BYTES):
            with self.subTest(size=len(copy)):
                path = self.png(
                    [
                        _chunk("parameters", first, b"zTXt"),
                        _chunk("parameters", copy, b"zTXt"),
                    ]
                )
                with self.assertRaisesRegex(
                    MetadataReadError, "exceeds inspection limits"
                ):
                    read_inspection_file(path)


def _f02(**changes) -> dict:
    """``f02`` (KSampler 3, encoders 6 and 7) with nodes added or replaced."""
    prompt = parity.build("f02")["prompt"]
    prompt.update(changes)
    return prompt


def _stage(class_type: str, image_input: str, positive=("6", 0), **inputs) -> dict:
    """``f19``'s shape with ``class_type`` as the detailer stage (node 50)."""
    prompt = parity.build("f19")["prompt"]
    if "upscale_model" in inputs:
        prompt["12"] = {"class_type": "UpscaleModelLoader", "inputs": {}}
    if "segs" in inputs:
        prompt["13"] = {"class_type": "SegsDetector", "inputs": {"image": ["8", 0]}}
    prompt["50"] = {
        "class_type": class_type,
        "inputs": {
            image_input: ["8", 0],
            "model": ["4", 0],
            "vae": ["4", 2],
            "positive": list(positive),
            "negative": ["7", 0],
            "seed": 7,
            "steps": 12,
            "cfg": 4.5,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 0.4,
            **inputs,
        },
    }
    return prompt


DETAILERS = (
    ("UltimateSDUpscale", "image", {"upscale_model": ["12", 0], "upscale_by": 2}),
    ("UltimateSDUpscaleNoUpscale", "upscaled_image", {}),
    ("DetailerForEach", "image", {"segs": ["13", 0], "wildcard": ""}),
)


class TestInstalledNodeSemantics(unittest.TestCase):
    """R6 entries verified against the installed node sources (see the report)."""

    def test_upscale_and_segs_detailers_are_stages(self):
        for class_type, image, extra in DETAILERS:
            with self.subTest(node=class_type):
                same = _inspect({"prompt": _stage(class_type, image, **extra)})
                self.assertEqual(same["state"], "resolved")
                self.assertEqual(same["sampler_id"], "50")
                self.assertEqual(_texts(same), (parity.POS, parity.NEG))
                other = _inspect(
                    {"prompt": _stage(class_type, image, ("10", 0), **extra)}
                )
                self.assertEqual(other["state"], "ambiguous")
                self.assertEqual(other["reason_code"], "multistage-differs")
                self.assertEqual(
                    sorted(item["sampler_id"] for item in other["candidates"]),
                    ["3", "50"],
                )

    def test_a_detailer_wildcard_replaces_its_prompt(self):
        # Impact Pack core.py: a non-empty wildcard is encoded instead of positive.
        for class_type in ("DetailerForEach", "FaceDetailer"):
            with self.subTest(node=class_type):
                result = _inspect(
                    {"prompt": _stage(class_type, "image", wildcard="a smiling face")}
                )
                self.assertEqual(result["state"], "unresolved")
                self.assertEqual(result["reason_code"], "runtime-value")
                self.assertEqual(result["node"], {"id": "50", "class_type": class_type})
                linked = _stage(class_type, "image", wildcard=["14", 0])
                linked["14"] = {"class_type": "WildcardSource", "inputs": {}}
                result = _inspect({"prompt": linked})
                self.assertEqual(result["reason_code"], "unsupported-node")
                self.assertEqual(
                    result["node"], {"id": "14", "class_type": "WildcardSource"}
                )
                for empty in ("", None):
                    result = _inspect(
                        {"prompt": _stage(class_type, "image", wildcard=empty)}
                    )
                    self.assertEqual(result["state"], "resolved", empty)

    def test_conditioning_passthroughs_keep_each_role(self):
        pair = {"positive": ["6", 0], "negative": ["7", 0]}
        cases = {
            "ControlNetInpaintingAliMamaApply": (
                {
                    **pair,
                    "control_net": ["11", 0],
                    "vae": ["4", 2],
                    "image": ["12", 0],
                    "mask": ["12", 1],
                    "strength": 1.0,
                },
                0,
                1,
            ),
            "LTXVConditioning": ({**pair, "frame_rate": 25.0}, 0, 1),
            "FluxKontextMultiReferenceLatentMethod": (
                {"conditioning": ["6", 0], "reference_latents_method": "offset"},
                0,
                None,
            ),
        }
        for class_type, (inputs, positive, negative) in cases.items():
            with self.subTest(node=class_type):
                prompt = _f02(**{"20": {"class_type": class_type, "inputs": inputs}})
                prompt["3"]["inputs"]["positive"] = ["20", positive]
                if negative is not None:
                    prompt["3"]["inputs"]["negative"] = ["20", negative]
                result = _inspect({"prompt": prompt})
                self.assertEqual(result["state"], "resolved")
                self.assertEqual(_texts(result), (parity.POS, parity.NEG))
                self.assertEqual(
                    result["positive"]["provenance"]["path"][0]["node_id"], "20"
                )
                if negative is None:
                    prompt["3"]["inputs"]["negative"] = ["20", 1]
                    self.assertEqual(
                        _inspect({"prompt": prompt})["reason_code"], "unsupported-node"
                    )

    def test_text_nodes(self):
        prompt = _f02(
            **{
                "6": {
                    "class_type": "Text to Conditioning",
                    "inputs": {"clip": ["4", 1], "text": ["21", 0]},
                },
                "21": {"class_type": "DisplayText", "inputs": {"text": ["22", 0]}},
                "22": {"class_type": "TextInput", "inputs": {"text": parity.POS}},
            }
        )
        result = _inspect({"prompt": prompt})
        self.assertEqual(_texts(result), (parity.POS, parity.NEG))
        self.assertEqual(
            [step["node_id"] for step in result["positive"]["provenance"]["path"]],
            ["6", "21", "22"],
        )
        # Text to Conditioning has one output; TextInput reads only ``text``.
        prompt["3"]["inputs"]["positive"] = ["6", 1]
        self.assertEqual(
            _inspect({"prompt": prompt})["reason_code"], "unsupported-node"
        )

    def test_overwritten_text_behind_a_new_passthrough_is_still_refused(self):
        # R1 before R6: the pass-through must not let overwritten text resolve.
        metadata = _overwritten()
        metadata["prompt"]["20"] = {
            "class_type": "FluxKontextMultiReferenceLatentMethod",
            "inputs": {"conditioning": ["17", 0], "reference_latents_method": "offset"},
        }
        metadata["prompt"]["3"]["inputs"]["positive"] = ["20", 0]
        result = _inspect(metadata)
        self.assertEqual(result["reason"], resolver.OVERWRITTEN_PROMPT_REASON)

    def test_default_profile_keeps_them_unsupported(self):
        for class_type, image, extra in DETAILERS:
            with self.subTest(node=class_type):
                result = resolve_prompt_pair(
                    {"prompt": _stage(class_type, image, **extra)}
                )
                # The historical rule: the one KSampler, whatever reads its image.
                self.assertEqual(result["sampler_id"], "3")
        prompt = _f02(
            **{"6": {"class_type": "Text to Conditioning", "inputs": {"text": "a"}}}
        )
        self.assertEqual(
            resolve_prompt_pair({"prompt": prompt})["reason"],
            "Unsupported node semantics or output slot",
        )


def _sampled(class_type: str, latent: str = "latent_image", **inputs) -> dict:
    """``f02`` with its KSampler (node 3) replaced by ``class_type``."""
    prompt = _f02()
    sampler = dict(prompt["3"]["inputs"], **inputs)
    sampler[latent] = sampler.pop("latent_image")
    prompt["3"] = {"class_type": class_type, "inputs": sampler}
    return prompt


SAMPLERS = (
    ("ClownsharKSampler_Beta", "latent_image", "options"),
    ("KSampler (Efficient)", "latent_image", "script"),
    ("ttN KSampler_v2", "latent", "adv_xyPlot"),
)


class TestUpstreamVerifiedNodeSemantics(unittest.TestCase):
    """R6 entries for nodes not installed here, read from their pinned sources.

    Each source is the commit the maintainer's saved workflows name in the
    node's ``cnr_id``/``ver`` properties (the report lists repository, commit,
    file and line).
    """

    def test_ksampler_shaped_samplers_resolve(self):
        for class_type, latent, _ in SAMPLERS:
            with self.subTest(node=class_type):
                result = _inspect({"prompt": _sampled(class_type, latent)})
                self.assertEqual(result["state"], "resolved")
                self.assertEqual(result["sampler_id"], "3")
                self.assertEqual(_texts(result), (parity.POS, parity.NEG))

    def test_inputs_that_replace_the_prompt_are_not_followed(self):
        # RES4LYF options may carry a guider; Efficiency scripts and the ttN
        # XY plot re-run the sampler with other settings.
        for class_type, latent, override in SAMPLERS:
            with self.subTest(node=class_type):
                prompt = _sampled(class_type, latent, **{override: ["30", 0]})
                prompt["30"] = {"class_type": "SomeScript", "inputs": {}}
                result = _inspect({"prompt": prompt})
                self.assertEqual(result["reason_code"], "unsupported-node")
                self.assertEqual(
                    result["node"], {"id": "30", "class_type": "SomeScript"}
                )

    def test_chained_samplers_are_stages(self):
        prompt = _sampled("ClownsharKSampler_Beta")
        prompt["31"] = dict(prompt["3"], inputs=dict(prompt["3"]["inputs"]))
        prompt["31"]["inputs"]["latent_image"] = ["3", 0]
        prompt["8"]["inputs"]["samples"] = ["31", 0]
        # Unanchored, two listed samplers are two branches, as for KSampler.
        self.assertEqual(_inspect({"prompt": prompt})["state"], "ambiguous")
        result = _inspect({"prompt": prompt}, output_node_id="9")
        self.assertEqual(result["reason"], resolver.MULTISTAGE_UNATTRIBUTED_REASON)

    def test_seed_variance_enhancer_passes_its_conditioning(self):
        prompt = _f02(
            **{
                "20": {
                    "class_type": "SeedVarianceEnhancer",
                    "inputs": {"conditioning": ["6", 0], "seed": 1, "strength": 20.0},
                }
            }
        )
        prompt["3"]["inputs"]["positive"] = ["20", 0]
        self.assertEqual(_texts(_inspect({"prompt": prompt})), (parity.POS, parity.NEG))

    def test_overwritten_text_behind_seed_variance_is_still_refused(self):
        # R1 before R6: the recon's 24 files differ from this only in names.
        metadata = _overwritten()
        metadata["prompt"]["20"] = {
            "class_type": "SeedVarianceEnhancer",
            "inputs": {"conditioning": ["17", 0], "seed": 1},
        }
        metadata["prompt"]["3"]["inputs"]["positive"] = ["20", 0]
        result = _inspect(metadata)
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason"], resolver.OVERWRITTEN_PROMPT_REASON)

    def test_instantid_advanced_outputs(self):
        prompt = _f02(
            **{
                "20": {
                    "class_type": "ApplyInstantIDAdvanced",
                    "inputs": {
                        "model": ["4", 0],
                        "positive": ["6", 0],
                        "negative": ["7", 0],
                        "ip_weight": 0.8,
                    },
                }
            }
        )
        prompt["3"]["inputs"].update(positive=["20", 1], negative=["20", 2])
        self.assertEqual(_texts(_inspect({"prompt": prompt})), (parity.POS, parity.NEG))
        prompt["3"]["inputs"]["positive"] = ["20", 0]  # output 0 is the MODEL
        self.assertEqual(
            _inspect({"prompt": prompt})["reason_code"], "unsupported-node"
        )

    def test_crystools_conditioning_switch(self):
        def switch(condition) -> dict:
            prompt = _f02(
                **{
                    "11": _pm_encoder("a dog"),
                    "20": {
                        "class_type": "Switch conditioning [Crystools]",
                        "inputs": {
                            "on_true": ["6", 0],
                            "on_false": ["11", 0],
                            "boolean": condition,
                        },
                    },
                    "21": {"class_type": "PrimitiveBoolean", "inputs": {"value": True}},
                }
            )
            prompt["3"]["inputs"]["positive"] = ["20", 0]
            return _inspect({"prompt": prompt})

        self.assertEqual(_texts(switch(True))[0], parity.POS)
        self.assertEqual(_texts(switch(False))[0], "a dog")
        result = switch(["21", 0])
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(
            sorted(_texts(item)[0] for item in result["candidates"]),
            sorted([parity.POS, "a dog"]),
        )

    def test_smz_encoder_reads_text_unless_sdxl_may_apply(self):
        def smz(**inputs) -> dict:
            node = {
                "class_type": "smZ CLIPTextEncode",
                "inputs": {
                    "clip": ["4", 1],
                    "text": "a cat",
                    "text_g": "a cat",
                    "text_l": "a cat",
                    "with_SDXL": False,
                    "parser": "comfy",
                    **inputs,
                },
            }
            return _inspect({"prompt": _f02(**{"6": node})})

        self.assertEqual(_texts(smz(text_g="", text_l="x")), ("a cat", parity.NEG))
        # with_SDXL: the connected CLIP decides at run time which inputs are read.
        self.assertEqual(_texts(smz(with_SDXL=True)), ("a cat", parity.NEG))
        for parts in ({"text_g": "x"}, {"text_l": ""}, {"text": "x"}):
            with self.subTest(parts=parts):
                result = smz(with_SDXL=True, **parts)
                self.assertEqual(result["reason_code"], "runtime-value")
                self.assertEqual(
                    result["node"], {"id": "6", "class_type": "smZ CLIPTextEncode"}
                )
        linked = smz(with_SDXL=["21", 0])
        self.assertEqual(linked["reason_code"], "unsupported-node")
        for broken in ({"with_SDXL": "yes"}, {"with_SDXL": None}):
            with self.subTest(flag=broken):
                self.assertEqual(smz(**broken)["reason_code"], "malformed")

    def test_string_sources(self):
        cases = (
            ("ttN text", "text"),
            ("CR Text", "text"),
            ("CR Prompt Text", "prompt"),
            ("TextBoxMira", "text"),
        )
        for class_type, name in cases:
            with self.subTest(node=class_type):
                prompt = _f02(
                    **{"10": {"class_type": class_type, "inputs": {name: parity.POS}}}
                )
                prompt["6"]["inputs"]["text"] = ["10", 0]
                self.assertEqual(
                    _texts(_inspect({"prompt": prompt})), (parity.POS, parity.NEG)
                )
                # Output 1 of the Comfyroll nodes is their help URL.
                prompt["6"]["inputs"]["text"] = ["10", 1]
                self.assertEqual(
                    _inspect({"prompt": prompt})["reason_code"], "unsupported-node"
                )

    def test_default_profile_keeps_them_unsupported(self):
        for class_type, latent, _ in SAMPLERS:
            with self.subTest(node=class_type):
                result = resolve_prompt_pair({"prompt": _sampled(class_type, latent)})
                self.assertEqual(result["reason"], "No supported sampler pair found")


if __name__ == "__main__":
    unittest.main()
