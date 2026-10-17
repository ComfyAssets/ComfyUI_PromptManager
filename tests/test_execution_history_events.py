"""Bounded, immutable evidence captured from real host-shaped queue callbacks."""

from collections import namedtuple
from dataclasses import FrozenInstanceError, dataclass, replace
import json
import unittest
from uuid import UUID, uuid5

from utils.execution_history_events import (
    LifecycleEvent,
    RunFinish,
    TerminalEvidence,
    classify_evidence,
    encode_command,
    make_finish,
    make_start,
)

SESSION = "11111111-1111-4111-8111-111111111111"
JOB = "22222222-2222-4222-8222-222222222222"
OTHER_JOB = "33333333-3333-4333-8333-333333333333"
EPOCH = "44444444-4444-4444-8444-444444444444"
Status = namedtuple("ExecutionStatus", "status_str completed messages")


def queue_result(item_id=0, job_id=JOB):
    return ((1.0, job_id, {"secret": "graph"}, {"token": "private"}, []), item_id)


def message(kind, job_id=JOB, **fields):
    return (kind, {"prompt_id": job_id, **fields})


def finish(messages, completed=True):
    return make_finish(SESSION, 0, EPOCH, Status("success", completed, messages), 500)


class ExecutionHistoryEventsTests(unittest.TestCase):
    def test_dequeue_identity_is_deterministic_per_session_and_item(self):
        start = make_start(SESSION, queue_result(), EPOCH, 100)
        repeated = make_start(SESSION, queue_result(), EPOCH, 100)
        second = make_start(SESSION, queue_result(1), EPOCH, 100)
        self.assertEqual(start.run_id, repeated.run_id)
        self.assertNotEqual(start.run_id, second.run_id)
        self.assertEqual(start.run_id, str(uuid5(UUID(SESSION), "0")))
        self.assertEqual(start.queue_item_id, 0)
        self.assertEqual(start.job_id, JOB)
        with self.assertRaises(FrozenInstanceError):
            start.job_id = OTHER_JOB

    def test_required_identity_and_observation_inputs_are_rejected(self):
        invalid = [None, "bad", JOB.upper(), "", True, 1]
        # The UUID fixture contains digits only; use an upper-case UUID with letters.
        invalid[2] = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(make_start(value, queue_result(), EPOCH, 100))
                self.assertIsNone(
                    make_start(SESSION, queue_result(job_id=value), EPOCH, 100)
                )
                self.assertIsNone(make_start(SESSION, queue_result(), value, 100))
                self.assertIsNone(make_finish(value, 0, EPOCH, None, 100))
        for value in [-1, True, 1.5, "0", 2**63]:
            with self.subTest(value=value):
                self.assertIsNone(make_start(SESSION, queue_result(value), EPOCH, 100))
                self.assertIsNone(make_finish(SESSION, value, EPOCH, None, 100))
                self.assertIsNone(make_start(SESSION, queue_result(), EPOCH, value))
                self.assertIsNone(make_finish(SESSION, 0, EPOCH, None, value))
        for malformed in [None, (), ((), 0), ("bad", 0), (queue_result()[0],)]:
            self.assertIsNone(make_start(SESSION, malformed, EPOCH, 0))

    def test_terminal_messages_override_aggregate_flags_and_keep_safe_fields(self):
        command = finish(
            [
                message("execution_start", timestamp=100),
                message(
                    "execution_error",
                    timestamp=200,
                    node_id="9",
                    node_type="Sampler",
                    exception_type="RuntimeError",
                    exception_message="SECRET",
                    traceback=["SECRET"],
                    inputs={"SECRET": 1},
                ),
            ]
        )
        result = classify_evidence(JOB, command.evidence)
        self.assertEqual((result.outcome, result.reason), ("failed", "terminal_error"))
        self.assertEqual(
            (result.executor_started_at, result.terminal_event_at), (100, 200)
        )
        self.assertEqual(
            (result.node_id, result.node_type, result.exception_type),
            ("9", "Sampler", "RuntimeError"),
        )
        self.assertNotIn(b"SECRET", encode_command(command))
        self.assertNotIn(b"completed", encode_command(command))

    def test_success_interruption_and_duplicate_terminals(self):
        for kind, outcome, reason in [
            ("execution_success", "succeeded", "terminal_success"),
            ("execution_interrupted", "interrupted", "terminal_interrupted"),
        ]:
            with self.subTest(kind=kind):
                event = message(kind, timestamp=200)
                result = classify_evidence(JOB, finish([event, event], False).evidence)
                self.assertEqual((result.outcome, result.reason), (outcome, reason))
        result = classify_evidence(
            JOB,
            finish(
                [message("execution_success"), message("execution_interrupted")]
            ).evidence,
        )
        self.assertEqual(
            (result.outcome, result.reason), ("unknown", "conflicting_terminal")
        )

    def test_absent_unknown_and_foreign_messages_do_not_prove_completion(self):
        for messages in [
            [],
            [message("executed")],
            [message("execution_success", OTHER_JOB)],
        ]:
            with self.subTest(messages=messages):
                result = classify_evidence(JOB, finish(messages).evidence)
                self.assertEqual(
                    (result.outcome, result.reason), ("unknown", "missing_terminal")
                )
        for status in [
            None,
            Status("success", True, None),
            Status("success", True, {}),
        ]:
            command = make_finish(SESSION, 0, EPOCH, status, 100)
            self.assertEqual(
                classify_evidence(JOB, command.evidence).reason, "invalid_evidence"
            )

    def test_malformed_evidence_is_unknown_even_with_success(self):
        for malformed in [
            None,
            (),
            ("execution_error",),
            ("execution_error", None),
            ("execution_error", {}),
            message("execution_error", timestamp=-1),
            message("execution_error", timestamp=True),
            message("execution_error", timestamp=float("nan")),
        ]:
            with self.subTest(malformed=malformed):
                result = classify_evidence(
                    JOB, finish([message("execution_success"), malformed]).evidence
                )
                self.assertEqual(
                    (result.outcome, result.reason), ("unknown", "invalid_evidence")
                )

    def test_missing_timestamps_remain_null(self):
        result = classify_evidence(
            JOB,
            finish(
                [
                    message("execution_start", timestamp=None),
                    message("execution_success"),
                ]
            ).evidence,
        )
        self.assertEqual(result.outcome, "succeeded")
        self.assertIsNone(result.executor_started_at)
        self.assertIsNone(result.terminal_event_at)

    def test_optional_oversize_and_invalid_fields_are_omitted_without_losing_failure(
        self,
    ):
        for value in ["x" * 129, "x" * 1_000_000, {}, 12]:
            with self.subTest(value_type=type(value).__name__):
                command = finish(
                    [
                        message(
                            "execution_error",
                            node_id=value,
                            node_type=value,
                            exception_type="x" * 257,
                        )
                    ]
                )
                result = classify_evidence(JOB, command.evidence)
                self.assertEqual(result.outcome, "failed")
                self.assertTrue(result.diagnostics_omitted)
                self.assertIsNone(result.node_id)
                self.assertIsNone(result.node_type)
                self.assertIsNone(result.exception_type)

    def test_unicode_diagnostics_fit_utf8_budget_and_preserve_failure(self):
        command = finish(
            [
                message(
                    "execution_error",
                    node_id="😀" * 128,
                    node_type="😀" * 128,
                    exception_type="😀" * 256,
                )
            ]
        )
        encoded = encode_command(command)
        self.assertLessEqual(len(encoded), 2048)
        self.assertIsInstance(json.loads(encoded), dict)
        result = classify_evidence(JOB, command.evidence)
        self.assertEqual(result.outcome, "failed")
        self.assertTrue(result.diagnostics_omitted)

    def test_message_limit_never_trusts_partial_success(self):
        command = finish([message("execution_success")] * 1025)
        result = classify_evidence(JOB, command.evidence)
        self.assertEqual((result.outcome, result.reason), ("unknown", "evidence_limit"))
        self.assertLessEqual(len(encode_command(command)), 2048)
        self.assertEqual(
            classify_evidence(
                JOB, finish([message("execution_success")] * 1024).evidence
            ).outcome,
            "succeeded",
        )

    def test_required_event_budget_exhaustion_is_not_truncated_success(self):
        messages = [message("execution_success", timestamp=i) for i in range(100)]
        command = finish(messages)
        self.assertEqual(
            classify_evidence(JOB, command.evidence).reason, "evidence_limit"
        )
        self.assertLessEqual(len(encode_command(command)), 2048)

    def test_classification_uses_stored_job_id_after_capture(self):
        command = finish(
            [message("execution_error", OTHER_JOB), message("execution_success", JOB)]
        )
        self.assertEqual(classify_evidence(JOB, command.evidence).outcome, "succeeded")
        self.assertEqual(
            classify_evidence(OTHER_JOB, command.evidence).outcome, "failed"
        )

    def test_encode_rejects_forged_invalid_or_unbounded_commands(self):
        start = make_start(SESSION, queue_result(), EPOCH, 100)
        command = finish([message("execution_success")])
        bad_event = LifecycleEvent("execution_success", JOB, -1, None, None, None)
        for invalid in [
            None,
            {},
            replace(start, run_id="bad"),
            replace(start, job_id="x" * 129),
            replace(start, queue_item_id=True),
            replace(command, observed_callback_at=-1),
            replace(
                command, evidence=TerminalEvidence((bad_event,), False, False, False)
            ),
            replace(command, evidence=TerminalEvidence((), 1, False, False)),
            replace(
                command,
                evidence=TerminalEvidence(
                    command.evidence.events * 100, False, False, False
                ),
            ),
        ]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    encode_command(invalid)
        self.assertLessEqual(len(encode_command(start)), 2048)
        self.assertIsInstance(command, RunFinish)

    def test_encoder_rejects_unknown_fields_and_nonstring_event_kinds(self):
        @dataclass(frozen=True)
        class ExtendedFinish(RunFinish):
            secret: str = "credentials"

        command = finish([message("execution_success")])
        extended = ExtendedFinish(SESSION, 0, EPOCH, 100, command.evidence)
        invalid_kind = LifecycleEvent([], JOB, None, None, None, None)
        for invalid in [
            extended,
            replace(
                command, evidence=TerminalEvidence((invalid_kind,), False, False, False)
            ),
        ]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    encode_command(invalid)

    def test_malformed_foreign_timestamp_cannot_discredit_matching_success(self):
        command = finish(
            [
                message("execution_error", OTHER_JOB, timestamp=-1),
                message("execution_success", JOB, timestamp=300),
            ]
        )
        matching = classify_evidence(JOB, command.evidence)
        self.assertEqual(
            (matching.outcome, matching.reason), ("succeeded", "terminal_success")
        )
        self.assertEqual(
            classify_evidence(OTHER_JOB, command.evidence).reason, "invalid_evidence"
        )
        self.assertLessEqual(len(encode_command(command)), 2048)

    def test_malformed_foreign_envelope_is_scoped_when_identity_is_usable(self):
        command = finish(
            [
                ("execution_error", {"prompt_id": OTHER_JOB}, "unexpected"),
                message("execution_success", JOB),
            ]
        )
        self.assertEqual(classify_evidence(JOB, command.evidence).outcome, "succeeded")
        self.assertEqual(
            classify_evidence(OTHER_JOB, command.evidence).reason, "invalid_evidence"
        )

    def test_diagnostic_budget_reduction_retains_job_scoped_invalidity(self):
        command = finish(
            [
                message("execution_error", JOB, timestamp=-1),
                message(
                    "execution_success",
                    JOB,
                    node_id="😀" * 128,
                    node_type="😀" * 128,
                    exception_type="😀" * 256,
                ),
            ]
        )
        result = classify_evidence(JOB, command.evidence)
        self.assertEqual(
            (result.outcome, result.reason), ("unknown", "invalid_evidence")
        )
        self.assertLessEqual(len(encode_command(command)), 2048)

    def test_job_scoped_invalidity_budget_overflow_is_evidence_limit(self):
        command = finish(
            [
                message("execution_error", str(UUID(int=i)), timestamp=-1)
                for i in range(100)
            ]
            + [message("execution_success")]
        )
        self.assertEqual(
            classify_evidence(JOB, command.evidence).reason, "evidence_limit"
        )
        self.assertLessEqual(len(encode_command(command)), 2048)

    def test_oversized_unknown_kind_is_ignored_without_hashing(self):
        class OversizedKind(str):
            def __hash__(self):
                raise AssertionError("Oversized event kind must not be hashed")

        command = finish(
            [
                message(OversizedKind("x" * 1_000_000)),
                message("execution_success", timestamp=300),
            ]
        )
        result = classify_evidence(JOB, command.evidence)
        self.assertEqual(
            (result.outcome, result.reason), ("succeeded", "terminal_success")
        )
        self.assertLessEqual(len(encode_command(command)), 2048)

    def test_encoder_rejects_oversized_kind_without_hashing(self):
        class OversizedKind(str):
            def __hash__(self):
                raise AssertionError("Oversized event kind must not be hashed")

        command = finish([message("execution_success")])
        event = LifecycleEvent(
            OversizedKind("x" * 1_000_000), JOB, None, None, None, None
        )
        with self.assertRaises(ValueError):
            encode_command(
                replace(
                    command, evidence=TerminalEvidence((event,), False, False, False)
                )
            )
