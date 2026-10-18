"""Host history is reduced to a bounded, scalar prefix under one payload pool."""

import gc
import unittest
import weakref
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch

from test_execution_contexts import load_module


class Hostile:
    def __iter__(self):
        raise AssertionError("host protocol iteration")

    def __str__(self):
        raise AssertionError("host scalar coercion")

    def __bool__(self):
        raise AssertionError("host truth test")

    def __eq__(self, other):
        raise AssertionError("host equality")

    def __hash__(self):
        return 42


class Text(str):
    def encode(self, *args, **kwargs):
        raise AssertionError("subclass encoding")


class Mapping(dict):
    def get(self, *args):
        raise AssertionError("mapping protocol")

    def items(self):
        raise AssertionError("mapping iteration")


class Sequence(list):
    def __iter__(self):
        raise AssertionError("sequence protocol")


class OutputCaptureTests(unittest.TestCase):
    def setUp(self):
        self.m = load_module("execution_output_capture")
        self.dto = load_module("execution_outputs")
        self.budget = load_module("execution_context_budget")
        self.pool = self.budget.ContextBudget()
        self.roots = self.dto.snapshot_output_roots("posix", "/output", "/temp")

    def image(self, **changes):
        return dict(filename="same.png", subfolder="", type="output", **changes)

    def capture(self, history, *, start=None, finish=None, **kwargs):
        sealed = self.m.seal_outputs(
            history,
            self.roots if start is None else start,
            self.roots if finish is None else finish,
            budget=kwargs.pop("budget", self.pool),
            capture_generation=kwargs.pop("capture_generation", 7),
            context_bytes=kwargs.pop("context_bytes", 0),
            **kwargs,
        )
        sealed.validate_header()
        if sealed.lease is not None:
            self.addCleanup(sealed.lease.release)
        return sealed

    def outputs(self, images):
        return {"outputs": {"7": {"images": images}}}

    def test_fresh_cached_duplicates_and_sorted_outer_identity(self):
        history = {
            "outputs": {
                "7": {"images": [self.image(), self.image()]},
                "10": {"images": [self.image()]},
                "2": {
                    "images": [
                        dict(filename=" é.png ", subfolder=" 雪 / Café ", type="temp")
                    ]
                },
            },
            "meta": Hostile(),
        }
        first = self.capture(history)
        second = self.capture(history)
        self.assertEqual(first.bundle, second.bundle)
        self.assertEqual((first.state, first.reason), ("complete", "reported"))
        refs = first.bundle.references
        self.assertEqual(
            [(r.output_node_id, r.image_ordinal) for r in refs],
            [("10", 0), ("2", 0), ("7", 0), ("7", 1)],
        )
        self.assertEqual(
            (refs[1].filename, refs[1].subfolder, refs[1].folder_type),
            (" é.png ", " 雪 / Café ", "temp"),
        )
        self.assertEqual(refs[1].root_key, self.roots.temp_key)
        self.assertEqual(
            first.lease.byte_count, self.dto.output_payload_size(first.bundle)
        )
        self.assertEqual(first.capture_generation, 7)
        with self.assertRaises(FrozenInstanceError):
            first.state = "partial"

    def test_empty_and_text_only_are_positive_empty_evidence(self):
        absent = self.dto.snapshot_output_roots("posix", None, None)
        for outputs in ({}, {"7": {}}, {"7": {"text": Hostile(), "tensor": Hostile()}}):
            with self.subTest(outputs=len(outputs)):
                sealed = self.capture({"outputs": outputs}, start=absent, finish=absent)
                self.assertEqual(
                    (sealed.state, sealed.reason), ("complete", "none_reported")
                )
                self.assertEqual(sealed.bundle.references, ())
                self.assertEqual(sealed.lease.byte_count, 128)
                sealed.lease.release()
        self.assertEqual(self.pool.charged_bytes, 0)

    def test_missing_and_invalid_envelopes_have_lease_free_omission_headers(self):
        for value, reason in (
            (None, "invalid_evidence"),
            ({}, "invalid_evidence"),
            ({"outputs": None}, "invalid_evidence"),
            (Hostile(), "invalid_evidence"),
            (Mapping(outputs={}), "invalid_evidence"),
            ({"outputs": Mapping()}, "invalid_evidence"),
        ):
            with self.subTest(kind=type(value).__name__):
                sealed = self.capture(value)
                self.assertEqual((sealed.state, sealed.reason), ("unavailable", reason))
                self.assertIsNone(sealed.bundle)
                self.assertIsNone(sealed.lease)
        self.assertEqual(self.pool.charged_bytes, 0)

    def test_wrong_container_or_scalar_types_never_invoke_host_protocols(self):
        invalid_ui = [None, Hostile(), Mapping(images=[])]
        for ui in invalid_ui:
            sealed = self.capture({"outputs": {"7": ui}})
            self.assertEqual(
                (sealed.state, sealed.reason), ("partial", "invalid_evidence")
            )
        for images in (None, Hostile(), Sequence(), (), {}):
            sealed = self.capture({"outputs": {"7": {"images": images}}})
            self.assertEqual(sealed.reason, "invalid_evidence")
        for entry in (
            None,
            True,
            Hostile(),
            Mapping(filename="x", subfolder="", type="output"),
        ):
            sealed = self.capture(self.outputs([entry, self.image()]))
            self.assertEqual(sealed.reason, "invalid_evidence")
            self.assertEqual(sealed.bundle.references[0].image_ordinal, 1)
        for field in ("filename", "subfolder", "type"):
            for value in (None, True, 1, Hostile(), Text("output")):
                image = self.image()
                image[field] = value
                sealed = self.capture(self.outputs([image, self.image()]))
                self.assertEqual(
                    (sealed.state, sealed.reason), ("partial", "invalid_evidence")
                )
                self.assertEqual(len(sealed.bundle.references), 1)
        for node_id in (None, True, 1, Hostile(), Text("7")):
            sealed = self.capture(
                {"outputs": {node_id: Hostile(), "good": {"images": [self.image()]}}}
            )
            self.assertEqual(sealed.reason, "invalid_evidence")
            self.assertEqual(sealed.bundle.references[0].output_node_id, "good")

    def test_missing_required_descriptor_fields_are_invalid(self):
        for field in ("filename", "subfolder", "type"):
            image = self.image()
            del image[field]
            sealed = self.capture(self.outputs([image]))
            self.assertEqual(
                (sealed.state, sealed.reason), ("partial", "invalid_evidence")
            )
            self.assertEqual(sealed.bundle.references, ())

    def test_unsafe_paths_reject_entries_without_repair_or_coercion(self):
        fixtures = {
            "posix": {
                "filename": ("", ".", "..", "a/b", "/absolute", "x\x00", "\ud800"),
                "subfolder": (
                    "/absolute",
                    "../x",
                    "x/../y",
                    "x/./y",
                    "x//y",
                    "x/",
                    ".",
                    "..",
                    "\ud800",
                    "x\x00",
                ),
            },
            "windows": {
                "filename": ("a/b", "a\\b", "C:x", "x:stream"),
                "subfolder": (
                    "C:x",
                    "C:\\x",
                    "\\\\server\\share",
                    "\\x",
                    "x\\..\\y",
                    "x\\.\\y",
                    "x\\\\y",
                    "x:stream",
                ),
            },
        }
        for style, fields in fixtures.items():
            roots = self.dto.snapshot_output_roots(
                style, "/output" if style == "posix" else "C:\\output", None
            )
            for field, values in fields.items():
                for value in values:
                    image = self.image()
                    image[field] = value
                    with self.subTest(style=style, field=field, value=repr(value)):
                        sealed = self.capture(
                            self.outputs([image, self.image()]),
                            start=roots,
                            finish=roots,
                        )
                        self.assertEqual(sealed.reason, "invalid_evidence")
                        self.assertEqual(sealed.bundle.references[0].image_ordinal, 1)
        image = dict(filename="a\\b:c.png ", subfolder=" x\\y /雪 ", type="output")
        sealed = self.capture(self.outputs([image]))
        self.assertEqual(sealed.reason, "reported")
        self.assertEqual(sealed.bundle.references[0].filename, image["filename"])

    def test_roots_missing_changed_or_style_changed_keep_unrooted_rows(self):
        for finish in (
            self.dto.snapshot_output_roots("posix", None, "/temp"),
            self.dto.snapshot_output_roots("posix", "/changed", "/temp"),
            self.dto.snapshot_output_roots("windows", "C:\\output", "C:\\temp"),
        ):
            sealed = self.capture(self.outputs([self.image()]), finish=finish)
            self.assertEqual(
                (sealed.state, sealed.reason), ("partial", "root_unavailable")
            )
            self.assertIsNone(sealed.bundle.references[0].root_key)
            self.assertEqual(sealed.bundle.references[0].path_style, "posix")
        missing = self.dto.snapshot_output_roots("posix", None, "/temp")
        sealed = self.capture(self.outputs([self.image()]), start=missing)
        self.assertIsNone(sealed.bundle.references[0].root_key)
        sealed = self.capture(
            {
                "outputs": {
                    "7": {"images": [dict(filename="x", subfolder="", type="temp")]}
                }
            },
            finish=missing,
        )
        self.assertEqual(sealed.reason, "reported")

    def test_node_boundary_counts_text_and_rejected_entries_before_sort(self):
        for count, reason in ((500, "none_reported"), (501, "limit_reached")):
            outputs = {str(i): {} for i in range(count)}
            sealed = self.capture({"outputs": outputs})
            self.assertEqual(sealed.reason, reason)
        outputs = {i: Hostile() for i in range(500)}
        outputs["accepted-if-overrun"] = {"images": [self.image()]}
        sealed = self.capture({"outputs": outputs})
        self.assertEqual((sealed.state, sealed.reason), ("partial", "limit_reached"))
        self.assertEqual(sealed.bundle.references, ())
        outputs = {
            "z": {"images": [self.image()]},
            **{str(i): {} for i in range(499)},
            "a": {"images": [self.image()]},
        }
        sealed = self.capture({"outputs": outputs})
        self.assertEqual(
            [(r.output_node_id, r.image_ordinal) for r in sealed.bundle.references],
            [("z", 0)],
        )

    def test_image_boundary_counts_malformed_positions_across_nodes(self):
        for count, reason in ((256, "reported"), (257, "limit_reached")):
            sealed = self.capture(self.outputs([self.image()] * count))
            self.assertEqual(sealed.reason, reason)
            self.assertEqual(len(sealed.bundle.references), 256)
        sealed = self.capture(
            {
                "outputs": {
                    "7": {"images": [None] * 255 + [self.image()]},
                    "8": {"images": [self.image()]},
                }
            }
        )
        self.assertEqual(sealed.reason, "limit_reached")
        self.assertEqual(
            [(r.output_node_id, r.image_ordinal) for r in sealed.bundle.references],
            [("7", 255)],
        )
        sealed = self.capture(self.outputs([None] * 256 + [Hostile()]))
        self.assertEqual(sealed.reason, "limit_reached")
        self.assertEqual(sealed.bundle.references, ())

    def test_scalar_bounds_accept_exact_bytes_and_stop_before_later_entries(self):
        fixtures = (
            ("node", "😀" * 128, "😀" * 129),
            ("filename", "é" * 512, "é" * 513),
            ("subfolder", "é" * 1024, "é" * 1025),
        )
        for field, exact, over in fixtures:
            for value, expected in ((exact, "reported"), (over, "limit_reached")):
                image = self.image()
                if field == "node":
                    node = value
                else:
                    node = "7"
                    image[field] = value
                sealed = self.capture(
                    {
                        "outputs": {
                            node: {"images": [image]},
                            "tail": {"images": [self.image()]},
                        }
                    }
                )
                with self.subTest(field=field, expected=expected):
                    self.assertEqual(sealed.reason, expected)
                    self.assertEqual(
                        len(sealed.bundle.references),
                        2 if expected == "reported" else 0,
                    )
        sealed = self.capture(
            {"outputs": {"x" * 129: {}, "tail": {"images": [self.image()]}}}
        )
        self.assertEqual(sealed.reason, "limit_reached")
        self.assertEqual(sealed.bundle.references, ())

    def test_bundle_byte_boundary_retains_only_prefix_and_shrinks_exactly(self):
        node = "😀" * 128
        image = dict(filename="x" * 1024, subfolder="x" * 2048, type="output")
        # POSIX references charge 3,787 bytes; 34 + a 2,186-byte tail = 131,072.
        tail = dict(filename="x", subfolder="x" * 1470, type="output")
        exact = self.capture({"outputs": {node: {"images": [image] * 34 + [tail]}}})
        self.assertEqual(exact.reason, "reported")
        self.assertEqual(exact.lease.byte_count, 131072)
        tail["subfolder"] += "x"
        over = self.capture(
            {"outputs": {node: {"images": [image] * 34 + [tail, self.image()]}}}
        )
        self.assertEqual((over.state, over.reason), ("partial", "limit_reached"))
        self.assertEqual(len(over.bundle.references), 34)
        self.assertEqual(over.lease.byte_count, 128886)

    def test_pool_denial_too_small_and_attempt_remaining_space(self):
        for available in (0, 1, 127):
            pool = self.budget.ContextBudget(available)
            sealed = self.capture(self.outputs([Hostile()]), budget=pool)
            self.assertEqual(
                (sealed.state, sealed.reason), ("unavailable", "budget_exhausted")
            )
            self.assertIsNone(sealed.bundle)
            self.assertIsNone(sealed.lease)
            self.assertEqual(pool.charged_bytes, 0)
        for remaining, rows, reason in (
            (127, None, "budget_exhausted"),
            (128, 0, "budget_exhausted"),
            (339, 0, "budget_exhausted"),
            (340, 1, "reported"),
        ):
            sealed = self.capture(
                self.outputs([self.image()]), context_bytes=1048576 - remaining
            )
            self.assertEqual(sealed.reason, reason)
            if rows is None:
                self.assertEqual(sealed.state, "unavailable")
            else:
                self.assertEqual(len(sealed.bundle.references), rows)
                self.assertLessEqual(sealed.lease.byte_count, remaining)
        empty = self.capture({"outputs": {}}, budget=self.budget.ContextBudget(128))
        self.assertEqual((empty.state, empty.reason), ("complete", "none_reported"))

    def test_pool_space_and_prior_fault_reason_precedence(self):
        # A 340-byte rooted row plus the 128-byte header fits exactly.
        pool = self.budget.ContextBudget(340)
        sealed = self.capture(
            self.outputs(
                [
                    None,
                    dict(filename="x", subfolder="", type="input"),
                    self.image(),
                    self.image(),
                ]
            ),
            budget=pool,
        )
        self.assertEqual((sealed.state, sealed.reason), ("partial", "budget_exhausted"))
        self.assertEqual(len(sealed.bundle.references), 1)
        for entries, reason in (
            ([dict(filename="x", subfolder="", type="input")], "unsupported"),
            (
                [dict(filename="x", subfolder="", type="input"), None],
                "invalid_evidence",
            ),
            ([None] * 257, "limit_reached"),
        ):
            sealed = self.capture(self.outputs(entries))
            self.assertEqual((sealed.state, sealed.reason), ("partial", reason))
        changed = self.dto.snapshot_output_roots("posix", "/changed", None)
        sealed = self.capture(
            self.outputs(
                [self.image(), dict(filename="x", subfolder="", type="input")]
            ),
            finish=changed,
        )
        self.assertEqual(sealed.reason, "unsupported")

    def test_reserve_precedes_copy_and_transfer_release_ownership(self):
        pool = self.budget.ContextBudget(8388608)
        context = pool.reserve(7000000)
        old_writer = pool.reserve(1000000)
        self.addCleanup(context.release)
        self.addCleanup(old_writer.release)
        sealed = self.capture(self.outputs([self.image()]), budget=pool)
        self.assertEqual(pool.charged_bytes, 8000340)
        owner = sealed.lease.transfer()
        self.addCleanup(owner.release)
        sealed.lease.release()
        self.assertEqual(pool.charged_bytes, 8000340)
        old_writer.release()
        context.release()
        self.assertEqual(pool.charged_bytes, 340)
        owner.release()
        owner.release()
        self.assertEqual(pool.charged_bytes, 0)
        original = self.dto.OutputReference

        def check_reserved(*args, **kwargs):
            self.assertEqual(pool.charged_bytes, 131072)
            return original(*args, **kwargs)

        # Construction is the first scalar-retention boundary.
        with patch.object(self.m, "OutputReference", side_effect=check_reserved):
            sealed = self.capture(self.outputs([self.image()]), budget=pool)
        self.assertEqual(sealed.lease.byte_count, 340)

    def test_exceptions_release_reservations_and_do_not_keep_host_envelope(self):
        for target in ("OutputReference", "output_payload_size"):
            with patch.object(
                self.m, target, side_effect=RuntimeError("injected assembly failure")
            ):
                with self.assertRaises(RuntimeError):
                    self.capture(self.outputs([self.image()]))
            self.assertEqual(self.pool.charged_bytes, 0)
        host = Hostile()
        ref = weakref.ref(host)
        history = self.outputs([self.image()])
        history["tensor"] = host
        history["outputs"]["7"]["metadata"] = host
        sealed = self.capture(history)
        history.clear()
        del host, history
        gc.collect()
        self.assertIsNone(ref())
        self.assertEqual(set(vars(sealed.lease)), {"_pool", "_bytes", "_active"})
        self.assertEqual(
            set(vars(sealed.bundle.references[0])),
            {
                "output_node_id",
                "image_ordinal",
                "folder_type",
                "subfolder",
                "filename",
                "path_style",
                "root_key",
            },
        )

    def test_no_filesystem_or_sqlite_io(self):
        with (
            patch("builtins.open", side_effect=AssertionError("file read")),
            patch("os.stat", side_effect=AssertionError("stat")),
            patch("os.path.realpath", side_effect=AssertionError("resolve")),
            patch("sqlite3.connect", side_effect=AssertionError("database")),
        ):
            sealed = self.capture(self.outputs([self.image()]))
        self.assertEqual(sealed.reason, "reported")

    def test_invalid_arguments_and_headers_cannot_leave_pool_charged(self):
        for field, values in (
            ("context_bytes", (-1, True, 1.0, 1048577)),
            ("capture_generation", (-1, True, None, 2**63)),
        ):
            for value in values:
                with (
                    self.subTest(field=field, value=value),
                    self.assertRaises(ValueError),
                ):
                    self.capture({"outputs": {}}, **{field: value})
                self.assertEqual(self.pool.charged_bytes, 0)
        sealed = self.capture({"outputs": {}})
        for changes in (
            {"state": Text("complete")},
            {"reason": Hostile()},
            {"state": "partial", "reason": "reported"},
            {"capture_generation": True},
            {"capture_generation": 2**63},
        ):
            with self.assertRaises(ValueError):
                replace(sealed, **changes).validate_header()

    def test_missing_history_has_distinct_omission_from_present_none(self):
        self.assertTrue(hasattr(self.m, "MISSING_HISTORY"))
        missing = self.capture(self.m.MISSING_HISTORY)
        malformed = self.capture(None)
        self.assertEqual(
            (missing.state, missing.reason), ("unavailable", "unavailable")
        )
        self.assertEqual(
            (malformed.state, malformed.reason), ("unavailable", "invalid_evidence")
        )
        self.assertEqual(self.pool.charged_bytes, 0)

    def test_status_construction_failure_after_transfer_releases_new_owner(self):
        with patch.object(
            self.m.SealedOutputs,
            "validate_header",
            side_effect=RuntimeError("invalid header"),
        ):
            with self.assertRaises(RuntimeError):
                self.capture(self.outputs([self.image()]))
        self.assertEqual(self.pool.charged_bytes, 0)

    def test_all_lease_assembly_failures_release_the_owned_reservation(self):
        for operation in ("shrink_to", "transfer"):
            with patch.object(
                self.budget.PayloadLease,
                operation,
                side_effect=RuntimeError("lease operation"),
            ):
                with self.assertRaises(RuntimeError):
                    self.capture(self.outputs([self.image()]))
            self.assertEqual(self.pool.charged_bytes, 0)

    def test_empty_pool_and_full_attempt_take_precedence_before_history_inspection(
        self,
    ):
        for history in (None, {}, {"outputs": {}}, self.outputs([self.image()])):
            for kwargs in (
                {"budget": self.budget.ContextBudget(0)},
                {"context_bytes": 1048576},
            ):
                sealed = self.capture(history, **kwargs)
                self.assertEqual(
                    (sealed.state, sealed.reason), ("unavailable", "budget_exhausted")
                )
                self.assertIsNone(sealed.bundle)
                self.assertIsNone(sealed.lease)
        sealed = self.capture(
            self.outputs([self.image()]), budget=self.budget.ContextBudget(128)
        )
        self.assertEqual((sealed.state, sealed.reason), ("partial", "budget_exhausted"))
        self.assertEqual(sealed.bundle.references, ())
        self.assertEqual(sealed.lease.byte_count, 128)

    def test_oversize_scalars_stop_on_character_precheck_without_host_encoding(self):
        for field in ("filename", "subfolder"):
            image = self.image()
            image[field] = "x" * 1000000
            sealed = self.capture(self.outputs([self.image(), image, self.image()]))
            self.assertEqual(sealed.reason, "limit_reached")
            self.assertEqual([r.image_ordinal for r in sealed.bundle.references], [0])
        for node in ("x" * 129, "x" * 1000000):
            sealed = self.capture(
                {
                    "outputs": {
                        "first": {"images": [self.image()]},
                        node: Hostile(),
                        "tail": {"images": [self.image()]},
                    }
                }
            )
            self.assertEqual(sealed.reason, "limit_reached")
            self.assertEqual(
                [r.output_node_id for r in sealed.bundle.references], ["first"]
            )

    def test_sealed_references_outlive_later_history_mutations(self):
        image = self.image()
        images = [image, self.image()]
        history = self.outputs(images)
        sealed = self.capture(history)
        image["filename"] = "replacement.png"
        images.clear()
        history.clear()
        self.assertEqual(
            [r.filename for r in sealed.bundle.references], ["same.png", "same.png"]
        )
        self.assertEqual([r.image_ordinal for r in sealed.bundle.references], [0, 1])
