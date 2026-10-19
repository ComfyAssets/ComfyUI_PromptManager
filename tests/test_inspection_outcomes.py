"""Non-resolved prompt outcomes carry one short, truthful line per role."""

import json
import unittest
from dataclasses import replace
from unittest import mock

from utils.inspection_outcomes import (
    CLASS_NAME_LENGTH,
    REASON_CODES,
    outcome_message,
    project_resolution,
)
from utils.inspection_response import encode_inspection_response
from utils.prompt_inspector import inspect_prompt_evidence
from utils.prompt_pair_resolver import (
    EMPTY_POSITIVE_REASON,
    INSPECTION_PROFILE,
    MULTISTAGE_UNATTRIBUTED_REASON,
    SEMANTICS_UNAVAILABLE_REASON,
)

LIMITS = "Prompt exceeds inspection limits."


def _encode(text: str) -> dict:
    return {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["9", 0]}}


def _latent() -> dict:
    return {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 8, "height": 8, "batch_size": 1},
    }


def _ksampler(positive: str = "7") -> dict:
    return {
        "class_type": "KSampler",
        "inputs": {
            "positive": [positive, 0],
            "negative": ["6", 0],
            "latent_image": ["4", 0],
            "model": ["9", 0],
            "seed": 1,
            "steps": 2,
            "cfg": 1.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
        },
    }


def _dual_cfg() -> dict:
    return {
        "1": _encode("positive"),
        "6": _encode("negative"),
        "2": {
            "class_type": "DualCFGGuider",
            "inputs": {
                "cond1": ["1", 0],
                "cond2": ["1", 0],
                "negative": ["6", 0],
                "model": ["9", 0],
            },
        },
        "3": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "guider": ["2", 0],
                "noise": ["9", 0],
                "sampler": ["9", 0],
                "sigmas": ["9", 0],
                "latent_image": ["4", 0],
            },
        },
        "4": _latent(),
    }


def _runtime() -> dict:
    return {
        "1": {"class_type": "PromptSearchList", "inputs": {"search": "cat"}},
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ["1", 0], "clip": ["9", 0]},
        },
        "6": _encode("negative"),
        "3": _ksampler(),
        "4": _latent(),
    }


def _composite() -> dict:
    graph = _runtime()
    graph["7"] = {
        "class_type": "ConditioningCombine",
        "inputs": {"conditioning_1": ["6", 0], "conditioning_2": ["8", 0]},
    }
    graph["8"] = _encode("other")
    return graph


def _messages(evidence: dict) -> dict:
    return {role: side.get("message") for role, side in evidence["prompts"].items()}


def _statuses(evidence: dict) -> dict:
    return {role: side["status"] for role, side in evidence["prompts"].items()}


class TestOutcomeMessages(unittest.TestCase):
    def assert_unresolved_with(self, evidence: dict, message: str) -> None:
        self.assertEqual(
            _statuses(evidence), {"positive": "unresolved", "negative": "unresolved"}
        )
        self.assertEqual(
            _messages(evidence), {"positive": message, "negative": message}
        )

    def test_unsupported_node_status_names_the_class(self):
        evidence = inspect_prompt_evidence({"prompt": _dual_cfg()})
        self.assertEqual(evidence["resolution"]["reason_code"], "unsupported-node")
        self.assert_unresolved_with(
            evidence,
            "Prompt uses a node the inspector does not support (DualCFGGuider).",
        )

    def test_runtime_and_composite_statuses(self):
        self.assert_unresolved_with(
            inspect_prompt_evidence({"prompt": _runtime()}),
            "Prompt is chosen at run time (PromptSearchList).",
        )
        self.assert_unresolved_with(
            inspect_prompt_evidence({"prompt": _composite()}),
            "Prompt combines several texts (ConditioningCombine).",
        )

    def test_limit_status(self):
        tight = replace(INSPECTION_PROFILE, per_candidate_visits=1, total_visits=1)
        evidence = inspect_prompt_evidence({"prompt": _runtime()}, profile=tight)
        self.assertEqual(evidence["resolution"]["reason_code"], "work-limit")
        self.assert_unresolved_with(evidence, LIMITS)
        for code in ("work-limit", "depth-limit", "text-limit", "graph-ceiling"):
            self.assertEqual(
                outcome_message({"state": "unresolved", "reason_code": code}), LIMITS
            )

    def test_every_closed_code_and_known_uncoded_reason_has_a_line(self):
        for code in REASON_CODES:
            message = outcome_message({"state": "unresolved", "reason_code": code})
            self.assertIsInstance(message, str, code)
            self.assertTrue(message.endswith("."), code)
            self.assertNotIn("could not be resolved", message)
        for reason, expected in (
            (EMPTY_POSITIVE_REASON, "Positive prompt is empty."),
            (
                SEMANTICS_UNAVAILABLE_REASON,
                "The inspector's node table is unavailable.",
            ),
            (
                MULTISTAGE_UNATTRIBUTED_REASON,
                "Several sampler stages were found; their prompts were not compared.",
            ),
            (
                "Media metadata could not be read",
                "Metadata in this file could not be read.",
            ),
        ):
            resolution = {"state": "unresolved", "reason": reason, "reason_code": None}
            self.assertEqual(outcome_message(resolution), expected)

    def test_named_codes_without_a_class_omit_the_name(self):
        for code, line in (
            ("unsupported-node", "Prompt uses a node the inspector does not support."),
            ("runtime-value", "Prompt is chosen at run time."),
            ("composite", "Prompt combines several texts."),
        ):
            for node in (None, {"id": "7", "class_type": None}):
                resolution = {"state": "unresolved", "reason_code": code, "node": node}
                self.assertEqual(outcome_message(resolution), line)

    def test_class_name_is_bounded(self):
        resolution = {
            "state": "unresolved",
            "reason_code": "unsupported-node",
            "node": {"id": "7", "class_type": "N" * 500},
        }
        self.assertEqual(
            outcome_message(resolution),
            "Prompt uses a node the inspector does not support "
            f"({'N' * CLASS_NAME_LENGTH}).",
        )
        self.assertEqual(CLASS_NAME_LENGTH, 200)

    def test_resolved_ambiguous_absent_and_unknown_outcomes_carry_no_line(self):
        for resolution in (
            {"state": "resolved", "reason_code": None},
            {"state": "ambiguous", "reason_code": "multistage-differs"},
            {"state": "ambiguous", "reason_code": None, "reason": "Multiple"},
            {"state": "absent", "reason_code": None, "reason": "No parsed"},
            {"state": "unresolved", "reason_code": "guess"},
            {"state": "unresolved", "reason_code": None, "reason": "Anything else"},
            None,
        ):
            self.assertIsNone(outcome_message(resolution), resolution)

    def test_inspector_lines_for_no_sampler_malformed_and_empty_positive(self):
        evidence = inspect_prompt_evidence({"prompt": {"1": _encode("x")}})
        self.assert_unresolved_with(evidence, "No supported sampler was found.")
        evidence = inspect_prompt_evidence({"prompt": "not-json"})
        self.assert_unresolved_with(
            evidence, "Prompt metadata in this file is malformed."
        )
        graph = _runtime()
        graph["7"] = _encode("   ")
        evidence = inspect_prompt_evidence({"prompt": graph})
        self.assertEqual(evidence["resolution"]["reason"], EMPTY_POSITIVE_REASON)
        self.assert_unresolved_with(evidence, "Positive prompt is empty.")

    def test_a_resolved_pair_with_an_empty_positive_explains_only_that_role(self):
        side = {"text": " ", "provenance": {"text_source": "graph-inferred"}}
        negative = {"text": "kept", "provenance": {"text_source": "graph-inferred"}}
        resolved = {
            "state": "resolved",
            "positive": side,
            "negative": negative,
            "sampler_id": "3",
            "reason": "",
            "reason_code": None,
            "node": None,
        }
        with mock.patch(
            "utils.prompt_inspector.resolve_prompt_pair", return_value=resolved
        ):
            evidence = inspect_prompt_evidence({"prompt": {}})
        self.assertEqual(
            evidence["prompts"]["positive"],
            {
                "status": "unresolved",
                "text": None,
                "source": None,
                "message": "Positive prompt is empty.",
            },
        )
        self.assertEqual(evidence["prompts"]["negative"]["text"], "kept")
        self.assertNotIn("message", evidence["prompts"]["negative"])

    def test_candidates_and_absence_keep_their_status_lines(self):
        graph = _runtime()
        graph["7"] = _encode("positive")
        graph["5"] = _ksampler()
        evidence = inspect_prompt_evidence({"prompt": graph})
        self.assertEqual(evidence["resolution"]["state"], "ambiguous")
        self.assertEqual(_messages(evidence), {"positive": None, "negative": None})
        evidence = inspect_prompt_evidence({})
        self.assertEqual(_messages(evidence), {"positive": None, "negative": None})
        graph = _runtime()
        graph["7"] = _encode("positive")
        evidence = inspect_prompt_evidence({"prompt": graph})
        self.assertEqual(evidence["resolution"]["state"], "resolved")
        self.assertEqual(_messages(evidence), {"positive": None, "negative": None})


class TestResolutionProjection(unittest.TestCase):
    def test_closed_codes_and_named_nodes_pass_unchanged(self):
        for code in REASON_CODES:
            resolution = {
                "state": "unresolved",
                "sampler_id": "3",
                "reason": "r",
                "reason_code": code,
                "node": None,
            }
            self.assertEqual(project_resolution(resolution), resolution)
        named = {
            "state": "unresolved",
            "sampler_id": None,
            "reason": "r",
            "reason_code": "composite",
            "node": {"id": "7", "class_type": None},
        }
        self.assertEqual(project_resolution(named), named)

    def test_unknown_codes_and_unbounded_nodes_fail_closed(self):
        base = {"state": "unresolved", "sampler_id": None, "reason": "r"}
        for code, node in (
            ("guess", None),
            (1, None),
            ("malformed", {"id": "7", "class_type": "X"}),
            (None, {"id": "7", "class_type": "X"}),
            ("unsupported-node", {"id": 7, "class_type": "X"}),
            ("unsupported-node", {"id": "", "class_type": "X"}),
            ("unsupported-node", {"id": "7"}),
            ("unsupported-node", {"id": "7", "class_type": 7}),
            ("unsupported-node", {"id": "7", "class_type": "X", "extra": 1}),
            ("unsupported-node", "X"),
        ):
            projected = project_resolution({**base, "reason_code": code, "node": node})
            self.assertIsNone(projected["node"], (code, node))
            if code not in REASON_CODES:
                self.assertIsNone(projected["reason_code"], code)
        for state in ("resolved", "absent"):
            projected = project_resolution(
                {**base, "state": state, "reason_code": "work-limit", "node": None}
            )
            self.assertIsNone(projected["reason_code"])
        long_name = project_resolution(
            {
                **base,
                "reason_code": "runtime-value",
                "node": {"id": "7" * 300, "class_type": "N" * 300},
            }
        )["node"]
        self.assertEqual(
            long_name, {"id": "7" * CLASS_NAME_LENGTH, "class_type": "N" * 200}
        )
        unnamed = project_resolution(
            {
                **base,
                "reason_code": "composite",
                "node": {"id": "7", "class_type": ""},
            }
        )["node"]
        self.assertEqual(unnamed, {"id": "7", "class_type": None})

    def test_wire_carries_role_lines_and_projected_resolution(self):
        evidence = inspect_prompt_evidence({"prompt": _dual_cfg()})
        resolution = dict(evidence["resolution"], reason_code="guess")
        body = json.loads(
            encode_inspection_response(
                image={"root_index": 0, "path": "a.png", "filename": "a.png"},
                root_scope="a" * 64,
                file={
                    "observation": "current-file",
                    "source_label": "output",
                    "media_type": "image",
                    "extension": ".png",
                    "size": 1,
                    "modified_time": 1,
                },
                prompts=evidence["prompts"],
                resolution=resolution,
                inspection=None,
                library={"status": "none", "tags": []},
            )
        )
        self.assertEqual(body["prompts"], evidence["prompts"])
        self.assertIsNone(body["resolution"]["reason_code"])
        self.assertIsNone(body["resolution"]["node"])


if __name__ == "__main__":
    unittest.main()
