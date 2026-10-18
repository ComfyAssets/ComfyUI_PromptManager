"""Pure, bounded final-history extraction under the shared payload budget."""

from dataclasses import dataclass
import json

from .execution_context_budget import ContextBudget, PayloadLease
from .execution_contexts import MAX_ATTEMPT_BYTES
from .execution_outputs import (
    BUNDLE_OVERHEAD,
    MAX_BUNDLE_BYTES,
    MAX_IMAGE_POSITIONS,
    OutputBundle,
    OutputReference,
    OutputRoots,
    STATE_REASONS,
    _reference_size,
    output_payload_size,
)

MISSING_HISTORY = object()


@dataclass(frozen=True)
class SealedOutputs:
    bundle: OutputBundle | None
    lease: PayloadLease | None
    state: str
    reason: str
    capture_generation: int | None = None

    def validate_header(self) -> None:
        if (
            type(self.state) is not str
            or type(self.reason) is not str
            or self.reason not in STATE_REASONS.get(self.state, ())
            or (
                self.capture_generation is not None
                and (
                    type(self.capture_generation) is not int
                    or not 0 <= self.capture_generation <= 2**63 - 1
                )
            )
        ):
            raise ValueError("Invalid output capture status")
        header = json.dumps(
            [self.state, self.reason, self.capture_generation], separators=(",", ":")
        ).encode("utf-8")
        if len(header) > 256:
            raise ValueError("Oversized output capture status")


def _scalar_status(value: object, limit: int, *, char_limit: int | None = None) -> str:
    """Check before retaining/encoding, distinguishing rejection from a stop bound."""
    if type(value) is not str:
        return "invalid_evidence"
    if len(value) > (limit if char_limit is None else char_limit):
        return "limit_reached"
    size = 0
    for char in value:
        code = ord(char)
        if code == 0 or 0xD800 <= code <= 0xDFFF:
            return "invalid_evidence"
        size += 1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4
        if size > limit:
            return "limit_reached"
    return ""


def _root_key(start: OutputRoots, finish: OutputRoots, folder: str) -> str | None:
    if start.path_style != finish.path_style:
        return None
    before = start.output_key if folder == "output" else start.temp_key
    after = finish.output_key if folder == "output" else finish.temp_key
    return before if before == after else None


def _extract(
    outputs: dict,
    start: OutputRoots,
    finish: OutputRoots,
    allowance: int,
) -> OutputBundle:
    references = []
    reasons = set()
    used = BUNDLE_OVERHEAD
    positions = 0
    stopped = False
    for node_count, (node_id, ui) in enumerate(outputs.items()):
        if node_count == 500:
            reasons.add("limit_reached")
            break
        status = _scalar_status(node_id, 512, char_limit=128)
        if status:
            reasons.add(status)
            if status == "limit_reached":
                break
            continue
        if not node_id or type(ui) is not dict:
            reasons.add("invalid_evidence")
            continue
        if "images" not in ui:
            continue
        images = ui["images"]
        if type(images) is not list:
            reasons.add("invalid_evidence")
            continue
        for ordinal, image in enumerate(images):
            if positions == MAX_IMAGE_POSITIONS:
                reasons.add("limit_reached")
                stopped = True
                break
            positions += 1
            if type(image) is not dict:
                reasons.add("invalid_evidence")
                continue
            folder = image.get("type")
            if type(folder) is not str:
                reasons.add("invalid_evidence")
                continue
            if folder not in ("output", "temp"):
                reasons.add("unsupported")
                continue
            filename, subfolder = image.get("filename"), image.get("subfolder")
            statuses = (
                _scalar_status(filename, 1024),
                _scalar_status(subfolder, 2048),
            )
            if "limit_reached" in statuses:
                reasons.add("limit_reached")
                stopped = True
                break
            if "invalid_evidence" in statuses:
                reasons.add("invalid_evidence")
                continue
            root = _root_key(start, finish, folder)
            reference = OutputReference(
                node_id, ordinal, folder, subfolder, filename, start.path_style, root
            )
            try:
                size = _reference_size(reference)
            except ValueError:
                reasons.add("invalid_evidence")
                continue
            if used + size > allowance:
                reasons.add(
                    "limit_reached"
                    if allowance == MAX_BUNDLE_BYTES
                    else "budget_exhausted"
                )
                stopped = True
                break
            references.append(reference)
            used += size
            if root is None:
                reasons.add("root_unavailable")
        if stopped:
            break
    reason = next(
        (
            candidate
            for candidate in (
                "budget_exhausted",
                "limit_reached",
                "invalid_evidence",
                "unsupported",
                "root_unavailable",
            )
            if candidate in reasons
        ),
        "reported" if references else "none_reported",
    )
    references.sort(
        key=lambda reference: (reference.output_node_id, reference.image_ordinal)
    )
    return OutputBundle(tuple(references), "partial" if reasons else "complete", reason)


def seal_outputs(
    history_result: object,
    roots_at_start: OutputRoots,
    roots_at_finish: OutputRoots,
    *,
    budget: ContextBudget,
    capture_generation: int,
    context_bytes: int,
) -> SealedOutputs:
    """Return one immutable prefix and its lease, or a lease-free omission header."""
    if type(capture_generation) is not int or not 0 <= capture_generation <= 2**63 - 1:
        raise ValueError("Invalid output capture generation")
    if type(context_bytes) is not int or not 0 <= context_bytes <= MAX_ATTEMPT_BYTES:
        raise ValueError("Invalid retained context bytes")
    for roots in (roots_at_start, roots_at_finish):
        if (
            type(roots) is not OutputRoots
            or type(roots.path_style) is not str
            or roots.path_style not in ("posix", "windows")
        ):
            raise ValueError("Invalid output roots")

    def omission(reason: str) -> SealedOutputs:
        return SealedOutputs(None, None, "unavailable", reason, capture_generation)

    lease = budget.reserve_up_to(
        min(MAX_BUNDLE_BYTES, MAX_ATTEMPT_BYTES - context_bytes)
    )
    if lease is None:
        return omission("budget_exhausted")
    handed_off = False
    try:
        if lease.byte_count < BUNDLE_OVERHEAD:
            return omission("budget_exhausted")
        if history_result is MISSING_HISTORY:
            return omission("unavailable")
        if type(history_result) is not dict:
            return omission("invalid_evidence")
        outputs = history_result.get("outputs")
        if type(outputs) is not dict:
            return omission("invalid_evidence")
        bundle = _extract(outputs, roots_at_start, roots_at_finish, lease.byte_count)
        lease.shrink_to(output_payload_size(bundle))
        lease = lease.transfer()
        sealed = SealedOutputs(
            bundle, lease, bundle.state, bundle.reason, capture_generation
        )
        sealed.validate_header()
        handed_off = True
        return sealed
    finally:
        if not handed_off:
            lease.release()
