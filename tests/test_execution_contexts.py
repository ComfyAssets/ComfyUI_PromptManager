"""Exact identities and bounded scalar records; normalization would lose history."""

import hashlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path


def load_module(name):
    package = "execution_context_test_utils"
    if package not in sys.modules:
        module = types.ModuleType(package)
        module.__path__ = [str(Path(__file__).parents[1] / "utils")]
        sys.modules[package] = module
    qualified = package + "." + name
    if qualified not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            qualified, Path(__file__).parents[1] / "utils" / (name + ".py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
    return sys.modules[qualified]


class ExactContextsTests(unittest.TestCase):
    def setUp(self):
        self.m = load_module("execution_contexts")

    def evidence(self, value, provenance="graph-inferred"):
        return self.m.TextEvidence(value, provenance, "node", None)

    def snapshot(self, value):
        return self.m.PromptSnapshot(
            *(self.evidence(v) for v in (value, value, "", ""))
        )

    def test_exact_identity(self):
        m = self.m
        self.assertEqual(
            m.canonical_content("snapshot", ("é", None, "", "")),
            '[1,"snapshot","é",null,"",""]'.encode(),
        )
        values = ("cat", "Cat", " cat", "cat ", "é", "e\u0301", None, "")
        digests = {m.content_digest("snapshot", (v, v, "", "")) for v in values}
        self.assertEqual(len(digests), len(values))
        self.assertEqual(
            m.content_digest("snapshot", ("cat", "cat", "", "")),
            hashlib.sha256(b'[1,"snapshot","cat","cat","",""]').hexdigest(),
        )
        a = self.snapshot("cat")
        b = m.PromptSnapshot(
            *(self.evidence(v, "execution") for v in ("cat", "cat", "", ""))
        )
        self.assertEqual(m.snapshot_digest(a), m.snapshot_digest(b))
        self.assertNotEqual(
            m.context_digest(m.RecipeContext("1", a, None, "declared")),
            m.context_digest(m.RecipeContext("1", None, a, "declared")),
        )

    def test_invalid_text_and_bounds(self):
        m = self.m
        for value in ("\ud800", "x" * 65537, "é" * 32769, object(), True):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    m.canonical_content("snapshot", (value,))
        m.canonical_content("snapshot", ("é" * 32768,))
        for identifier in ("x" * 129, object()):
            with self.assertRaises(ValueError):
                m.validate_bundle(
                    m.ContextBundle(
                        (m.RecipeContext(identifier, None, None, "declared"),),
                        "unknown",
                        "missing",
                    )
                )
        with self.assertRaises(ValueError):
            m.validate_bundle(
                m.ContextBundle(
                    tuple(
                        m.RecipeContext(str(i), None, None, "declared")
                        for i in range(17)
                    ),
                    "partial",
                    "limit",
                )
            )
        with self.assertRaises(ValueError):
            m.validate_bundle(
                m.ContextBundle(
                    (m.RecipeContext("1", self.snapshot("\ud800"), None, "declared"),),
                    "complete",
                    "",
                )
            )

    def test_bundle_preserves_unknown_and_empty(self):
        m = self.m
        bundle = m.ContextBundle(
            (m.RecipeContext("1", self.snapshot(""), self.snapshot(None), "declared"),),
            "partial",
            "unknown",
        )
        self.assertIsNone(m.validate_bundle(bundle))
        with self.assertRaises(ValueError):
            m.validate_bundle(
                m.ContextBundle(bundle.contexts, "partial", "", format_version=2)
            )

    def test_identifier_limit_counts_characters(self):
        m = self.m
        m.validate_bundle(
            m.ContextBundle(
                (m.RecipeContext("é" * 128, None, None, "declared"),), "unknown", ""
            )
        )
        with self.assertRaises(ValueError):
            m.validate_bundle(
                m.ContextBundle(
                    (m.RecipeContext("é" * 129, None, None, "declared"),), "unknown", ""
                )
            )

    def test_aggregate_payload_limits(self):
        m = self.m
        large = self.snapshot("x" * 65536)
        with self.assertRaises(ValueError):
            m.validate_bundle(
                m.ContextBundle(
                    (m.RecipeContext("1", large, large, "declared"),), "complete", ""
                )
            )
        contexts = tuple(
            m.RecipeContext(str(i), large, None, "declared") for i in range(8)
        )
        with self.assertRaises(ValueError):
            m.validate_bundle(m.ContextBundle(contexts, "complete", ""))

    def test_equal_repeated_samples_are_ambiguous(self):
        # Projection contains references, never runtime samples. Duplicate anchors
        # remain separate invalid records even if their prompt values are equal.
        m = self.m
        context = m.RecipeContext(
            "1", self.snapshot("same"), self.snapshot("same"), "declared"
        )
        with self.assertRaises(ValueError):
            m.validate_bundle(m.ContextBundle((context, context), "complete", ""))
