"""Exact, bounded output locators carry no authority to access files."""

import hashlib
import unittest
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch

from test_execution_contexts import load_module


class OutputContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.m = load_module("execution_outputs")

    def reference(self, **changes) -> object:
        reference = self.m.OutputReference(
            "7", 0, "output", "", "same.png", "posix", "a" * 64
        )
        return replace(reference, **changes)

    def test_exact_unicode_and_duplicate_locators_survive(self) -> None:
        m = self.m
        a = self.reference()
        b = replace(a, image_ordinal=1)
        bundle = m.OutputBundle((a, b), "complete", "reported")
        self.assertIsNone(m.validate_output_bundle(bundle))
        self.assertEqual(m.output_payload_size(bundle), 552)
        self.assertEqual(
            m.output_payload_size(bundle),
            128
            + sum(
                128
                + sum(
                    load_module("execution_contexts").text_size(v, 4096)
                    for v in (
                        r.output_node_id,
                        r.folder_type,
                        r.subfolder,
                        r.filename,
                        r.path_style,
                        r.root_key or "",
                    )
                )
                for r in bundle.references
            ),
        )
        variants = ("é.png", "e\u0301.png", "É.png", " é.png", "é.png ")
        references = tuple(
            self.reference(image_ordinal=i, filename=name, subfolder=" 雪 / Café ")
            for i, name in enumerate(variants)
        )
        exact = m.OutputBundle(references, "complete", "reported")
        self.assertIsNone(m.validate_output_bundle(exact))
        self.assertEqual(tuple(r.filename for r in exact.references), variants)
        self.assertEqual(exact.references[0].subfolder, " 雪 / Café ")
        self.assertEqual(
            m.output_payload_size(m.OutputBundle((), "complete", "none_reported")), 128
        )
        with self.assertRaises(FrozenInstanceError):
            a.filename = "changed.png"

    def test_posix_backslash_is_literal(self) -> None:
        m = self.m
        reference = self.reference(subfolder=r"a\b/雪", filename=r"a\b:c.png")
        self.assertIsNone(m.validate_output_reference(reference))
        self.assertEqual(reference.filename, r"a\b:c.png")
        for subfolder in (
            "/absolute",
            "../escape",
            "a/../b",
            "./a",
            "a//b",
            "a/",
            ".",
            "..",
        ):
            with self.subTest(subfolder=subfolder), self.assertRaises(ValueError):
                m.validate_output_reference(replace(reference, subfolder=subfolder))
        for filename in ("", "/absolute", "a/b", ".", "..", "a\x00b", "\ud800"):
            with self.subTest(filename=repr(filename)), self.assertRaises(ValueError):
                m.validate_output_reference(replace(reference, filename=filename))

    def test_windows_drive_unc_traversal_ads_rejected(self) -> None:
        m = self.m
        reference = self.reference(
            path_style="windows", subfolder=r"a\雪/b", filename=" image.png "
        )
        self.assertIsNone(m.validate_output_reference(reference))
        for subfolder in (
            r"C:\absolute",
            "C:relative",
            r"\\server\share",
            r"\rooted",
            "/rooted",
            "../escape",
            r"a\..\b",
            r"a\.\b",
            r"a\\b",
            "a//b",
            r"a:stream",
            "a/",
            "a\\",
            ".",
            "..",
        ):
            with self.subTest(subfolder=subfolder), self.assertRaises(ValueError):
                m.validate_output_reference(replace(reference, subfolder=subfolder))
        for filename in (r"a\b.png", "a/b.png", "C:foo.png", "a.png:stream", ".", ".."):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                m.validate_output_reference(replace(reference, filename=filename))

    def test_root_namespace_is_versioned_style_and_folder_specific(self) -> None:
        m = self.m
        roots = m.snapshot_output_roots("posix", "/a/../雪/root/", "/雪/root")
        self.assertEqual(roots.path_style, "posix")
        self.assertEqual(
            roots.output_key,
            hashlib.sha256('[1,"posix","output","/雪/root"]'.encode()).hexdigest(),
        )
        self.assertEqual(
            roots.temp_key,
            hashlib.sha256('[1,"posix","temp","/雪/root"]'.encode()).hexdigest(),
        )
        self.assertNotEqual(roots.output_key, roots.temp_key)
        self.assertNotEqual(
            roots.output_key,
            m.snapshot_output_roots("posix", "/雪/Root", None).output_key,
        )
        windows = m.snapshot_output_roots(
            "windows", r"C:\a\..\雪\root", r"\\server\share\a\.."
        )
        self.assertEqual(
            windows.output_key,
            hashlib.sha256(
                r'[1,"windows","output","C:\\雪\\root"]'.encode()
            ).hexdigest(),
        )
        self.assertIsNotNone(windows.temp_key)
        invalid = (
            None,
            True,
            b"/tmp",
            "relative",
            "",
            "/a\x00b",
            "/\ud800",
            "/" + "x" * 8192,
            "/" + "é" * 4096,
        )
        for root in invalid:
            with self.subTest(root_type=type(root).__name__):
                snapshot = m.snapshot_output_roots("posix", root, "/tmp")
                self.assertIsNone(snapshot.output_key)
                self.assertIsNotNone(snapshot.temp_key)
        self.assertIsNotNone(
            m.snapshot_output_roots("posix", "/" + "x" * 8191, None).output_key
        )
        self.assertIsNotNone(
            m.snapshot_output_roots("posix", "/" + "é" * 4095 + "x", None).output_key
        )
        for root in (
            r"\\server",
            "\\\\server\\",
            r"\\server\.",
            r"1:\root",
            r"C:relative",
            r"\rooted",
            "/rooted",
            r"\\?\C:\root",
            r"\\.\C:\root",
        ):
            with self.subTest(root=root):
                self.assertIsNone(
                    m.snapshot_output_roots("windows", root, None).output_key
                )
        for style in ("linux", "POSIX", None, True):
            with self.assertRaises(ValueError):
                m.snapshot_output_roots(style, "/tmp", "/tmp")

    def test_bounds_and_state_row_consistency(self) -> None:
        m = self.m
        a = self.reference()
        valid = {
            "complete": ("reported", "none_reported"),
            "partial": (
                "invalid_evidence",
                "unsupported",
                "limit_reached",
                "root_unavailable",
                "budget_exhausted",
            ),
            "unavailable": ("unavailable", "invalid_evidence", "budget_exhausted"),
            "conflicting": ("conflicting",),
        }
        reasons = (
            "reported",
            "none_reported",
            "invalid_evidence",
            "unsupported",
            "limit_reached",
            "root_unavailable",
            "budget_exhausted",
            "unavailable",
            "conflicting",
            "unknown",
            "",
        )
        for state in (*valid, "not_captured", "unknown"):
            for reason in reasons:
                for references in ((), (a,)):
                    accepted = reason in valid.get(state, ())
                    if state == "complete":
                        accepted = accepted and bool(references) == (
                            reason == "reported"
                        )
                    if state == "unavailable":
                        accepted = accepted and not references
                    if reason == "root_unavailable":
                        accepted = accepted and bool(references)
                    bundle = m.OutputBundle(references, state, reason)
                    with self.subTest(state=state, reason=reason, rows=len(references)):
                        if accepted:
                            self.assertIsNone(m.validate_output_bundle(bundle))
                        else:
                            with self.assertRaises(ValueError):
                                m.validate_output_bundle(bundle)
        null_root = replace(a, root_key=None)
        for state, reason in (
            ("partial", "root_unavailable"),
            ("conflicting", "conflicting"),
        ):
            m.validate_output_bundle(m.OutputBundle((null_root,), state, reason))
        with self.assertRaises(ValueError):
            m.validate_output_bundle(
                m.OutputBundle((null_root,), "complete", "reported")
            )
        for ordinal in (0, 255):
            m.validate_output_reference(replace(a, image_ordinal=ordinal))
        for changes in (
            {"image_ordinal": -1},
            {"image_ordinal": 256},
            {"image_ordinal": True},
            {"image_ordinal": 1.0},
            {"output_node_id": ""},
            {"output_node_id": "x" * 129},
            {"output_node_id": "\ud800"},
            {"output_node_id": "a\x00b"},
            {"filename": "é" * 513},
            {"subfolder": "雪" * 683},
            {"root_key": "A" * 64},
            {"root_key": "a" * 63},
            {"root_key": "z" * 64},
            {"folder_type": "input"},
            {"path_style": "native"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                m.validate_output_reference(replace(a, **changes))
        ordered = (
            replace(a, output_node_id="10"),
            replace(a, output_node_id="2"),
            a,
            replace(a, image_ordinal=1),
        )
        m.validate_output_bundle(m.OutputBundle(ordered, "complete", "reported"))
        for references in (
            (a, a),
            tuple(reversed(ordered)),
            tuple(replace(a, output_node_id=str(i).zfill(3)) for i in range(257)),
        ):
            with self.assertRaises(ValueError):
                m.validate_output_bundle(
                    m.OutputBundle(references, "complete", "reported")
                )
        maximum = replace(
            a,
            output_node_id="😀" * 128,
            filename="x" * 1024,
            subfolder="x" * 2048,
            path_style="windows",
        )
        self.assertIsNone(m.validate_output_reference(maximum))
        self.assertEqual(
            m.output_payload_size(m.OutputBundle((maximum,), "complete", "reported")),
            3917,
        )
        prefix = tuple(replace(maximum, image_ordinal=i) for i in range(34))
        tail = replace(maximum, image_ordinal=34, filename="x", subfolder="x" * 1400)
        exact = m.OutputBundle((*prefix, tail), "complete", "reported")
        self.assertEqual(m.output_payload_size(exact), 131072)
        with self.assertRaises(ValueError):
            m.output_payload_size(
                replace(
                    exact, references=(*prefix, replace(tail, subfolder="x" * 1401))
                )
            )
        for changes in (
            {"format_version": True},
            {"format_version": 2},
            {"source": "host_cache"},
            {"references": [a]},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                m.validate_output_bundle(
                    replace(m.OutputBundle((a,), "complete", "reported"), **changes)
                )

    def test_no_coercion_or_filesystem_calls(self) -> None:
        m = self.m

        class Hostile:
            def __str__(self):
                raise AssertionError("coerced arbitrary object")

            def __fspath__(self):
                raise AssertionError("read arbitrary path protocol")

            def __iter__(self):
                raise AssertionError("iterated arbitrary object")

        class Text(str):
            pass

        class Sequence(tuple):
            pass

        hostile = Hostile()
        for field in (
            "output_node_id",
            "folder_type",
            "subfolder",
            "filename",
            "path_style",
            "root_key",
        ):
            for value in (hostile, Text("output")):
                with self.subTest(field=field), self.assertRaises(ValueError):
                    m.validate_output_reference(self.reference(**{field: value}))
        for value in (hostile, Sequence((self.reference(),))):
            with self.assertRaises(ValueError):
                m.validate_output_bundle(m.OutputBundle(value, "complete", "reported"))
        for field in ("state", "reason", "source"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                m.validate_output_bundle(
                    replace(
                        m.OutputBundle((), "complete", "none_reported"),
                        **{field: hostile},
                    )
                )
        for value in (hostile, Text("/tmp")):
            self.assertIsNone(m.snapshot_output_roots("posix", value, None).output_key)
        with (
            patch("builtins.open", side_effect=AssertionError("filesystem open")),
            patch("os.stat", side_effect=AssertionError("filesystem stat")),
            patch("os.lstat", side_effect=AssertionError("filesystem lstat")),
            patch(
                "os.path.realpath", side_effect=AssertionError("filesystem resolution")
            ),
        ):
            roots = m.snapshot_output_roots(
                "posix", "/nonexistent/output", "/nonexistent/temp"
            )
            reference = self.reference(root_key=roots.output_key)
            self.assertIsNone(
                m.validate_output_bundle(
                    m.OutputBundle((reference,), "complete", "reported")
                )
            )
            self.assertEqual(
                m.output_payload_size(m.OutputBundle((), "unavailable", "unavailable")),
                128,
            )
