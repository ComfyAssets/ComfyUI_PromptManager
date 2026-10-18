"""Ownership and accounting behavior for shared context payloads."""

import unittest
from copy import copy
from unittest.mock import patch


class ContextBudgetTests(unittest.TestCase):
    def test_reservation_transfer_shrink_and_idempotent_release(self):
        from utils.execution_context_budget import ContextBudget

        pool = ContextBudget(100)
        lease = pool.reserve(100)
        self.assertIsNotNone(lease)
        self.assertIsNone(pool.reserve(1))
        owner = lease.transfer()
        lease.release()
        self.assertEqual(pool.charged_bytes, 100)
        with self.assertRaises(ValueError):
            lease.transfer()
        with self.assertRaises(ValueError):
            lease.shrink_to(1)
        owner.shrink_to(60)
        self.assertEqual(owner.byte_count, 60)
        self.assertEqual(pool.charged_bytes, 60)
        for invalid in (-1, 61, True, 1.0):
            with self.assertRaises(ValueError):
                owner.shrink_to(invalid)
        owner.release()
        owner.release()
        self.assertEqual(pool.charged_bytes, 0)

    def test_default_pool_and_invalid_reservations(self):
        from utils.execution_context_budget import ContextBudget

        pool = ContextBudget()
        lease = pool.reserve(8388608)
        self.assertIsNotNone(lease)
        self.assertIsNone(pool.reserve(1))
        lease.release()
        for value in (-1, True, 1.0):
            with self.assertRaises(ValueError):
                pool.reserve(value)
            with self.assertRaises(ValueError):
                ContextBudget(value)

    def test_only_pool_issued_leases_can_charge_or_release(self):
        from utils.execution_context_budget import ContextBudget, PayloadLease

        pool = ContextBudget(100)
        with self.assertRaises(ValueError):
            PayloadLease(pool, 100)
        self.assertEqual(pool.charged_bytes, 0)

    def test_measured_payload_field_context_and_attempt_boundaries(self):
        from dataclasses import replace
        from utils.execution_contexts import (
            ContextBundle,
            RecipeContext,
            PromptSnapshot,
            TextEvidence,
            bundle_payload_size,
            validate_bundle,
        )

        def recipe(anchor, fourth):
            values = (
                "x" * 65536,
                "x" * 65536,
                "x" * 65536,
                "x" * fourth,
                "",
                "",
                "",
                "",
            )
            fields = tuple(
                TextEvidence(v, "recorded", "runtime_return", None) for v in values
            )
            return RecipeContext(
                anchor,
                PromptSnapshot(*fields[:4]),
                PromptSnapshot(*fields[4:]),
                "inferred",
            )

        # 8 * (8 provenance + 14 source) + 8 role + 1 anchor = 185.
        context = recipe("1", 65351)
        bundle = ContextBundle((context,), "complete", "captured")
        self.assertEqual(bundle_payload_size(bundle), 262160)
        self.assertIsNone(validate_bundle(bundle))
        with self.assertRaises(ValueError):
            bundle_payload_size(replace(bundle, contexts=(recipe("1", 65352),)))
        field = replace(context.positive.raw, value="é" * 32769)
        bad = replace(context, positive=replace(context.positive, raw=field))
        with self.assertRaises(ValueError):
            bundle_payload_size(replace(bundle, contexts=(bad,)))
        attempt = replace(
            bundle, contexts=tuple(recipe(str(i), 65347) for i in range(4))
        )
        self.assertEqual(bundle_payload_size(attempt), 1048576)
        with self.assertRaises(ValueError):
            bundle_payload_size(
                replace(attempt, contexts=(recipe("0", 65348), *attempt.contexts[1:]))
            )
        small = replace(context, positive=None, negative=None)
        sixteen = replace(
            bundle, contexts=tuple(replace(small, anchor_id=str(i)) for i in range(16))
        )
        validate_bundle(sixteen)
        with self.assertRaises(ValueError):
            bundle_payload_size(
                replace(
                    sixteen,
                    contexts=(*sixteen.contexts, replace(small, anchor_id="17")),
                )
            )

    def test_reserve_up_to_uses_only_available_space_and_same_pool(self):
        from utils.execution_context_budget import ContextBudget

        pool = ContextBudget(100)
        self.assertTrue(callable(getattr(pool, "reserve_up_to", None)))
        first = pool.reserve(70)
        second = pool.reserve_up_to(50)
        self.assertEqual(second.byte_count, 30)
        self.assertEqual(pool.charged_bytes, 100)
        self.assertIsNone(pool.reserve_up_to(1))
        first.release()
        third = pool.reserve_up_to(20)
        self.assertEqual(third.byte_count, 20)
        self.assertIsNone(pool.reserve_up_to(0))
        for invalid in (-1, True, 1.0):
            with self.assertRaises(ValueError):
                pool.reserve_up_to(invalid)
        third.release()
        second.release()
        self.assertEqual(pool.charged_bytes, 0)

    def test_reserve_up_to_is_atomic_under_competing_reservations(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from utils.execution_context_budget import ContextBudget

        pool = ContextBudget(100)
        self.assertTrue(callable(getattr(pool, "reserve_up_to", None)))
        barrier = Barrier(8)

        def reserve(_):
            barrier.wait()
            return pool.reserve_up_to(30)

        with ThreadPoolExecutor(max_workers=8) as executor:
            leases = list(executor.map(reserve, range(8)))
        self.assertEqual(
            sorted(lease.byte_count for lease in leases if lease), [10, 30, 30, 30]
        )
        self.assertEqual(pool.charged_bytes, 100)
        for lease in leases:
            if lease is not None:
                lease.release()
        self.assertEqual(pool.charged_bytes, 0)

    def test_copied_and_forged_handles_cannot_spend_or_release_genuine_credits(self):
        from utils.execution_context_budget import ContextBudget, PayloadLease

        pool = ContextBudget(100)
        genuine = pool.reserve(80)
        forged = object.__new__(PayloadLease)
        forged.__dict__.update(genuine.__dict__)
        for impostor in (copy(genuine), forged):
            with self.subTest(impostor=impostor):
                with self.assertRaises(ValueError):
                    _ = impostor.byte_count
                with self.assertRaises(ValueError):
                    impostor.transfer()
                with self.assertRaises(ValueError):
                    impostor.shrink_to(1)
                impostor.release()
                self.assertEqual(pool.charged_bytes, 80)
                self.assertEqual(genuine.byte_count, 80)
                self.assertIsNone(pool.reserve(21))
        owner = genuine.transfer()
        genuine.release()
        owner.shrink_to(20)
        self.assertEqual(pool.charged_bytes, 20)
        owner.release()
        self.assertEqual(pool.charged_bytes, 0)
        last = pool.reserve(100)
        self.assertIsNotNone(last)
        last.release()
        self.assertEqual(pool.charged_bytes, 0)

    def test_failed_owner_allocation_or_registration_never_orphans_credits(self):
        from utils.execution_context_budget import ContextBudget, PayloadLease

        pool = ContextBudget(100)
        genuine = pool.reserve(20)
        with patch.object(PayloadLease, "_new", side_effect=MemoryError):
            for operation in (
                lambda: pool.reserve(10),
                lambda: pool.reserve_up_to(10),
                genuine.transfer,
            ):
                with self.assertRaises(MemoryError):
                    operation()
                self.assertEqual(pool.charged_bytes, 20)
                self.assertEqual(genuine.byte_count, 20)

        class DeniedOwners(set):
            def add(self, owner):
                raise MemoryError

        pool._owners = DeniedOwners(pool._owners)
        for operation in (
            lambda: pool.reserve(10),
            lambda: pool.reserve_up_to(10),
            genuine.transfer,
        ):
            with self.assertRaises(MemoryError):
                operation()
            self.assertEqual(pool.charged_bytes, 20)
            self.assertEqual(genuine.byte_count, 20)
        genuine.release()
        self.assertEqual(pool.charged_bytes, 0)
