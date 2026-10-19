"""Legacy parser rules ported into the inspection resolver (#240, Task 2.7b).

Every graph here is synthetic (neutral texts, generic names). Each class names
the rule from the parser reconciliation (R1, R3, ...) it ports. The default
(ingestion) profile must stay exactly as it was, so each rule also checks that
``DEFAULT_PROFILE`` ignores it.
"""

import json
import unittest

from utils import prompt_pair_resolver as resolver
from utils.prompt_inspector import inspect_prompt_evidence
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
        for workflow in (None, "nodes", {"nodes": "x"}, {"nodes": [1, None]}):
            with self.subTest(workflow=workflow):
                metadata = _overwritten()
                metadata["workflow"] = workflow
                self.assertEqual(_inspect(metadata)["state"], "resolved")

    def test_an_untouched_branch_beside_overwritten_nodes_resolves(self):
        # Only the overwritten nodes are refused: the negative is not one.
        metadata = _overwritten()
        metadata["prompt"]["3"]["inputs"]["positive"] = ["7", 0]
        self.assertEqual(_texts(_inspect(metadata)), (parity.NEG, parity.NEG))

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


if __name__ == "__main__":
    unittest.main()
