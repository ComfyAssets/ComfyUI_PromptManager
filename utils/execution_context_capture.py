"""Revocable, bounded runtime evidence owned by an actual dequeue attempt."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, replace
import threading
from typing import Callable, TYPE_CHECKING
from uuid import uuid4
import weakref

from .execution_context_budget import ContextBudget, PayloadLease, SealedContext
from .execution_context_graph import (
    NOT_SAVED,
    GraphProjection,
    GraphRole,
    SourceRef,
    project_contexts,
)
from .execution_contexts import (
    ContextBundle,
    PromptSnapshot,
    RecipeContext,
    TextEvidence,
    bundle_payload_size,
    text_size,
    MAX_ATTEMPT_BYTES,
)
from .execution_history_events import RunStart, encode_command

if TYPE_CHECKING:
    from .execution_history_writer import HistoryWriterStatus


@dataclass(frozen=True)
class AttemptCapability:
    nonce: str
    run_id: str
    session_id: str
    queue_item_id: int
    job_id: str
    storage_epoch: str
    capture_generation: int
    sources: tuple[SourceRef, ...]


@dataclass
class _Attempt:
    capability: AttemptCapability
    projection: GraphProjection
    lease: PayloadLease | None
    samples: dict
    retained_bytes: int
    reason: str = ""
    executor_identity: int | None = None
    executor_rejected: bool = False


_binding: ContextVar[tuple | None] = ContextVar("prompt_manager_attempt", default=None)


def active_capture() -> tuple[ContextCapture, AttemptCapability] | None:
    binding = _binding.get()
    if binding is not None:
        manager = binding[0]()
        if manager is not None and manager.valid(binding[1]):
            return manager, binding[1]
    return None


def unbind_current() -> None:
    binding = _binding.get()
    _binding.set(None)
    if binding is not None:
        manager = binding[0]()
        if manager is not None:
            manager.revoke(binding[1])


def _unknown(reason: str = "missing") -> PromptSnapshot:
    field = TextEvidence(None, "unknown", "unknown", reason)
    return PromptSnapshot(field, field, field, field)


def _snapshot_bytes(snapshot: PromptSnapshot) -> int:
    # Reuse the scalar validator, including provenance and per-field bounds.
    bundle = ContextBundle(
        (RecipeContext("x", snapshot, None, "inferred"),), "partial", "partial"
    )
    return bundle_payload_size(bundle)


def _bounded_snapshot(snapshot: PromptSnapshot) -> tuple[PromptSnapshot, bool]:
    if type(snapshot) is not PromptSnapshot:
        raise ValueError("invalid_snapshot")
    fields, limited = [], False
    for field in (snapshot.raw, snapshot.effective, snapshot.prepend, snapshot.append):
        if type(field) is not TextEvidence:
            raise ValueError("invalid_field")
        if field.value is not None:
            try:
                text_size(field.value)
            except ValueError:
                field = _unknown("limit_reached").raw
                limited = True
        fields.append(field)
    if fields[1].value is not None and fields[1].provenance != "recorded":
        fields[1] = _unknown("missing").effective
    bounded = PromptSnapshot(*fields)
    _snapshot_bytes(bounded)
    return bounded, limited


class ContextCapture:
    def __init__(
        self,
        queue_identity: int,
        status_reader: Callable[[], HistoryWriterStatus],
        budget: ContextBudget,
    ) -> None:
        self.queue_identity = queue_identity
        self._status_reader = status_reader
        self._budget = budget
        self._lock = threading.RLock()
        self._attempts: dict[str, _Attempt] = {}

    def _current(self, capability: AttemptCapability) -> _Attempt | None:
        attempt = self._attempts.get(capability.nonce)
        if attempt is None or attempt.capability is not capability:
            return None
        status = self._status_reader()
        if (
            not status.accepting
            or status.epoch != capability.storage_epoch
            or status.capture_generation != capability.capture_generation
        ):
            self._drop(capability)
            return None
        return attempt

    def valid(self, capability: AttemptCapability) -> bool:
        with self._lock:
            return self._current(capability) is not None

    def begin(
        self, start: RunStart, generation: int, graph: object, outputs: object
    ) -> AttemptCapability:
        unbind_current()
        encode_command(start)
        with self._lock:
            cap = AttemptCapability(
                str(uuid4()),
                start.run_id,
                start.session_id,
                start.queue_item_id,
                start.job_id,
                start.storage_epoch,
                generation,
                (),
            )
            if len(self._attempts) >= 128:
                return cap
            lease = self._budget.reserve(MAX_ATTEMPT_BYTES)
            projection = GraphProjection((), (), "")
            reason = "budget_exhausted" if lease is None else ""
            retained = 0
            if lease is not None:
                try:
                    projection = project_contexts(graph, outputs)
                    retained = (
                        sum(
                            _snapshot_bytes(role.inferred) if role.inferred else 0
                            for recipe in projection.recipes
                            for role in (recipe.positive, recipe.negative)
                        )
                        + sum(
                            _snapshot_bytes(snapshot)
                            for _, snapshot in projection.source_snapshots
                        )
                        + sum(
                            text_size(s.node_id, 512) + text_size(s.node_type, 128) + 32
                            for s in projection.sources
                        )
                    )
                    if retained > MAX_ATTEMPT_BYTES:
                        raise ValueError("projection_limit")
                except ValueError:
                    projection = GraphProjection((), (), "")
                    reason = "graph_limit"
                    retained = 0
                except Exception:
                    projection = GraphProjection((), (), "")
                    reason = "invalid_evidence"
                    retained = 0
                    lease.release()
                    lease = None
            cap = replace(cap, sources=projection.sources)
            self._attempts[cap.nonce] = _Attempt(
                cap, projection, lease, {}, retained, reason
            )
            _binding.set((weakref.ref(self), cap))
            self._current(cap)
            return cap

    def bind_executor(self, capability: AttemptCapability, identity: int) -> None:
        """Bind one scalar host identity; recursive starts invalidate cache proof."""
        with self._lock:
            attempt = self._current(capability)
            if attempt is not None:
                if attempt.executor_identity is not None:
                    attempt.executor_rejected = True
                else:
                    attempt.executor_identity = identity

    def matches_executor(self, capability: AttemptCapability, identity: int) -> bool:
        with self._lock:
            attempt = self._current(capability)
            return bool(
                attempt is not None
                and not attempt.executor_rejected
                and attempt.executor_identity == identity
            )

    def source_snapshot(
        self, capability: AttemptCapability, source: SourceRef
    ) -> PromptSnapshot | None:
        with self._lock:
            attempt = self._current(capability)
            if attempt is not None:
                for ref, snapshot in attempt.projection.source_snapshots:
                    if ref == source:
                        return snapshot
            return None

    def observe(
        self,
        capability: AttemptCapability,
        source: SourceRef,
        invocation_index: int,
        snapshot: PromptSnapshot,
    ) -> None:
        try:
            with self._lock:
                attempt = self._current(capability)
                if (
                    attempt is None
                    or attempt.lease is None
                    or source not in capability.sources
                    or type(source) is not SourceRef
                    or type(invocation_index) is not int
                    or not 0 <= invocation_index <= 2**63 - 1
                ):
                    return
                snapshot, limited = _bounded_snapshot(snapshot)
                size = _snapshot_bytes(snapshot)
                if (
                    limited
                    or any(
                        field.reason == "limit_reached"
                        for field in (
                            snapshot.raw,
                            snapshot.effective,
                            snapshot.prepend,
                            snapshot.append,
                        )
                    )
                ) and attempt.reason not in ("multiple_samples", "limit_reached"):
                    attempt.reason = "field_limit"
                key = source, invocation_index
                if key in attempt.samples:
                    if attempt.samples[key] != snapshot:
                        attempt.reason = "multiple_samples"
                    return
                if len(attempt.samples) >= 64:
                    attempt.reason = "multiple_samples"
                    return
                if attempt.retained_bytes + size > MAX_ATTEMPT_BYTES:
                    attempt.reason = "limit_reached"
                    return
                attempt.samples[key] = snapshot
                attempt.retained_bytes += size
        except (ValueError, AttributeError, TypeError):
            return

    @staticmethod
    def _discard_payload(attempt: _Attempt) -> None:
        attempt.projection = GraphProjection((), (), "")
        attempt.samples.clear()
        attempt.retained_bytes = 0

    def _drop(self, capability: AttemptCapability) -> _Attempt | None:
        attempt = self._attempts.get(capability.nonce)
        if attempt is not None and attempt.capability is capability:
            self._attempts.pop(capability.nonce)
            self._discard_payload(attempt)
            if attempt.lease is not None:
                attempt.lease.release()
            return attempt
        return None

    def revoke(self, capability: AttemptCapability) -> None:
        with self._lock:
            self._drop(capability)

    def revoke_all(self) -> None:
        with self._lock:
            for attempt in self._attempts.values():
                self._discard_payload(attempt)
                if attempt.lease is not None:
                    attempt.lease.release()
            self._attempts.clear()

    def seal(self, capability: AttemptCapability) -> SealedContext:
        with self._lock:
            attempt = self._current(capability)
            if attempt is None:
                return SealedContext(None, None, "unavailable", "unavailable")
            self._attempts.pop(capability.nonce)
            lease = attempt.lease
            if lease is None:
                return SealedContext(None, None, "unavailable", attempt.reason)
            try:
                ambiguous = attempt.reason == "multiple_samples"

                def role_snapshot(role: GraphRole) -> PromptSnapshot:
                    nonlocal ambiguous
                    if role.reason == "source-transformed":
                        return _unknown("unsupported")
                    if role.reason == NOT_SAVED:
                        return _unknown("unavailable")
                    if not role.sources:
                        return role.inferred or _unknown("unsupported")
                    values = [
                        snapshot
                        for (source, _), snapshot in attempt.samples.items()
                        if source in role.sources
                    ]
                    if len(values) > 1 or attempt.reason == "multiple_samples":
                        ambiguous = True
                        return _unknown("ambiguous")
                    if len(role.sources) == 1 and len(values) == 1:
                        return values[0]
                    inferred = role.inferred
                    if len(role.sources) == 1:
                        snapshots = attempt.projection.source_snapshots
                        inferred = next(
                            (
                                snapshot
                                for source, snapshot in snapshots
                                if source == role.sources[0]
                            ),
                            inferred,
                        )
                    if inferred is not None:
                        s = inferred
                        return PromptSnapshot(
                            s.raw, _unknown().effective, s.prepend, s.append
                        )
                    return _unknown()

                contexts = tuple(
                    RecipeContext(
                        r.anchor_id,
                        role_snapshot(r.positive),
                        role_snapshot(r.negative),
                        "inferred",
                    )
                    for r in attempt.projection.recipes
                )
                supported = any(
                    role.sources or role.inferred is not None
                    for r in attempt.projection.recipes
                    for role in (r.positive, r.negative)
                )
                complete = bool(contexts) and all(
                    field.value is not None
                    for r in contexts
                    for s in (r.positive, r.negative)
                    for field in (s.raw, s.effective, s.prepend, s.append)
                )
                state = (
                    "ambiguous" if ambiguous else "complete" if complete else "partial"
                )
                reason = (
                    "multiple_samples"
                    if ambiguous
                    else "captured" if complete else "partial"
                )
                if attempt.reason == "field_limit" and not ambiguous:
                    state, reason = "partial", "field_limit"
                if not supported:
                    contexts, state, reason = (), "unavailable", "no_supported_context"
                if attempt.reason in ("limit_reached", "graph_limit"):
                    contexts, state, reason = (), "unavailable", attempt.reason
                bundle = ContextBundle(contexts, state, reason)
                size = bundle_payload_size(bundle)
                if size + attempt.retained_bytes > MAX_ATTEMPT_BYTES:
                    raise ValueError("assembly_limit")
                self._discard_payload(attempt)
                lease.shrink_to(size)
                return SealedContext(
                    bundle, lease, state, reason, capability.capture_generation
                )
            except ValueError:
                self._discard_payload(attempt)
                lease.release()
                return SealedContext(None, None, "unavailable", "limit_reached")
            except Exception:
                self._discard_payload(attempt)
                lease.release()
                return SealedContext(None, None, "unavailable", "invalid_evidence")


def record_runtime_context(
    node_type: str,
    raw: str,
    prepend: str,
    append: str,
    effective: str,
    *,
    role: str | None = None,
) -> None:
    """Record only a successful node return in a trusted, bound host invocation."""
    try:
        binding = active_capture()
        if binding is None:
            return
        # Optional host import is independent of our package namespace.
        from comfy_execution.utils import get_executing_context

        host = get_executing_context()
        manager, cap = binding
        if (
            host is None
            or type(host.prompt_id) is not str
            or host.prompt_id != cap.job_id
        ):
            return
        if type(host.node_id) is not str or type(host.list_index) is not int:
            return
        if node_type == "PromptManagerPair":
            slots = (
                (0, 2) if role == "positive" else (1, 3) if role == "negative" else ()
            )
        else:
            slots = (
                (0,)
                if node_type == "PromptManagerText"
                else (0, 1) if role is None else ()
            )
        for slot in slots:
            source = SourceRef(host.node_id, node_type, slot)
            if source not in cap.sources:
                continue
            snapshot = PromptSnapshot(
                *(
                    TextEvidence(value, "recorded", "runtime_return", None)
                    for value in (raw, effective, prepend, append)
                )
            )
            manager.observe(cap, source, host.list_index, snapshot)
    except Exception:
        # Capture must never change the host result or expose input/exception text.
        return
