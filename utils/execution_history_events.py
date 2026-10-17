"""Pure, bounded execution-lifecycle records for the background ledger writer."""

from dataclasses import asdict, dataclass, replace
import json
from uuid import UUID, uuid5

MAX_COMMAND_BYTES = 2048
MAX_MESSAGES = 1024
MAX_INTEGER = 2**63 - 1
TERMINAL_KINDS = {
    "execution_success": ("succeeded", "terminal_success"),
    "execution_error": ("failed", "terminal_error"),
    "execution_interrupted": ("interrupted", "terminal_interrupted"),
}
EVENT_KINDS = frozenset((*TERMINAL_KINDS, "execution_start"))
MAX_EVENT_KIND_LENGTH = max(map(len, EVENT_KINDS))


@dataclass(frozen=True)
class RunStart:
    run_id: str
    session_id: str
    queue_item_id: int
    job_id: str
    storage_epoch: str
    observed_dequeue_at: int


@dataclass(frozen=True)
class LifecycleEvent:
    kind: str
    job_id: str
    timestamp: int | None
    node_id: str | None
    node_type: str | None
    exception_type: str | None


@dataclass(frozen=True)
class TerminalEvidence:
    events: tuple[LifecycleEvent, ...]
    invalid: bool
    limited: bool
    diagnostics_omitted: bool
    invalid_job_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunFinish:
    session_id: str
    queue_item_id: int
    storage_epoch: str
    observed_callback_at: int
    evidence: TerminalEvidence


@dataclass(frozen=True)
class Completion:
    outcome: str
    reason: str
    executor_started_at: int | None
    terminal_event_at: int | None
    node_id: str | None
    node_type: str | None
    exception_type: str | None
    diagnostics_omitted: bool


RunCommand = RunStart | RunFinish


def _valid_event_kind(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= MAX_EVENT_KIND_LENGTH
        and value in EVENT_KINDS
    )


def _valid_integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_INTEGER


def _valid_uuid(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _valid_identity(session_id: object, item_id: object, epoch: object) -> bool:
    return _valid_uuid(session_id) and _valid_integer(item_id) and _valid_uuid(epoch)


def make_start(
    session_id: str, queue_result: object, storage_epoch: str, observed_at: int
) -> RunStart | None:
    """Extract only the public job ID and queue item ID from a dequeue result."""
    if not isinstance(queue_result, (tuple, list)) or len(queue_result) != 2:
        return None
    queue_item, item_id = queue_result
    if not isinstance(queue_item, (tuple, list)) or len(queue_item) < 2:
        return None
    job_id = queue_item[1]
    if not (
        _valid_identity(session_id, item_id, storage_epoch)
        and _valid_uuid(job_id)
        and _valid_integer(observed_at)
    ):
        return None
    return RunStart(
        str(uuid5(UUID(session_id), str(item_id))),
        session_id,
        item_id,
        job_id,
        storage_epoch,
        observed_at,
    )


def _optional_diagnostic(value: object, limit: int) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    if isinstance(value, str) and len(value) <= limit:
        # Lone surrogates cannot be encoded as UTF-8 allowed data.
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return None, True
        return value, False
    return None, True


def make_finish(
    session_id: str,
    queue_item_id: int,
    storage_epoch: str,
    status: object,
    observed_at: int,
) -> RunFinish | None:
    """Capture allowed lifecycle fields without copying the host envelope."""
    if not (
        _valid_identity(session_id, queue_item_id, storage_epoch)
        and _valid_integer(observed_at)
    ):
        return None
    messages = getattr(status, "messages", None)
    events = {}
    invalid_job_ids = {}
    invalid = not isinstance(messages, (tuple, list))
    limited = False
    omitted = False
    if not invalid:
        limited = len(messages) > MAX_MESSAGES
        for index in range(min(len(messages), MAX_MESSAGES)):
            message = messages[index]
            usable_job_id = None
            if (
                isinstance(message, (tuple, list))
                and len(message) >= 2
                and isinstance(message[1], dict)
                and _valid_uuid(message[1].get("prompt_id"))
            ):
                usable_job_id = message[1]["prompt_id"]
            malformed = not isinstance(message, (tuple, list)) or len(message) != 2
            if not malformed:
                kind, data = message
                malformed = not isinstance(kind, str)
                if not malformed and not _valid_event_kind(kind):
                    continue
                malformed |= usable_job_id is None
            if not malformed:
                timestamp = data.get("timestamp")
                malformed = timestamp is not None and not _valid_integer(timestamp)
            if malformed:
                if usable_job_id is None:
                    invalid = True
                else:
                    invalid_job_ids[usable_job_id] = None
                continue
            diagnostics = []
            for field, limit in (
                ("node_id", 128),
                ("node_type", 128),
                ("exception_type", 256),
            ):
                value, dropped = _optional_diagnostic(data.get(field), limit)
                diagnostics.append(value)
                omitted |= dropped
            event = LifecycleEvent(kind, data["prompt_id"], timestamp, *diagnostics)
            events[event] = None
    command = RunFinish(
        session_id,
        queue_item_id,
        storage_epoch,
        observed_at,
        TerminalEvidence(
            tuple(events), invalid, limited, omitted, tuple(invalid_job_ids)
        ),
    )
    if len(_serialize(command)) <= MAX_COMMAND_BYTES:
        return command
    # Diagnostics yield to all candidate identities, kinds and event times.
    stripped = tuple(
        dict.fromkeys(
            replace(event, node_id=None, node_type=None, exception_type=None)
            for event in command.evidence.events
        )
    )
    had_diagnostics = any(
        event.node_id is not None
        or event.node_type is not None
        or event.exception_type is not None
        for event in command.evidence.events
    )
    command = replace(
        command,
        evidence=TerminalEvidence(
            stripped,
            invalid,
            limited,
            omitted or had_diagnostics,
            tuple(invalid_job_ids),
        ),
    )
    if len(_serialize(command)) > MAX_COMMAND_BYTES:
        # Required evidence could not fit: no partial terminal may prove success.
        command = replace(
            command,
            evidence=replace(
                command.evidence, events=(), limited=True, invalid_job_ids=()
            ),
        )
    return command


def classify_evidence(job_id: str, evidence: TerminalEvidence) -> Completion:
    """Classify only candidates matching the job ID persisted at dequeue."""
    events = tuple(event for event in evidence.events if event.job_id == job_id)
    start_times = [
        event.timestamp
        for event in events
        if event.kind == "execution_start" and event.timestamp is not None
    ]
    started_at = min(start_times, default=None)
    terminals = tuple(event for event in events if event.kind in TERMINAL_KINDS)
    kinds = {event.kind for event in terminals}
    reason = None
    if evidence.limited:
        reason = "evidence_limit"
    elif evidence.invalid or job_id in evidence.invalid_job_ids:
        reason = "invalid_evidence"
    elif len(kinds) > 1:
        reason = "conflicting_terminal"
    elif not kinds:
        reason = "missing_terminal"
    if reason is not None:
        return Completion(
            "unknown",
            reason,
            started_at,
            None,
            None,
            None,
            None,
            evidence.diagnostics_omitted,
        )
    outcome, reason = TERMINAL_KINDS[terminals[0].kind]
    terminal_times = [
        event.timestamp for event in terminals if event.timestamp is not None
    ]
    return Completion(
        outcome,
        reason,
        started_at,
        max(terminal_times, default=None),
        next((event.node_id for event in terminals if event.node_id is not None), None),
        next(
            (event.node_type for event in terminals if event.node_type is not None),
            None,
        ),
        next(
            (
                event.exception_type
                for event in terminals
                if event.exception_type is not None
            ),
            None,
        ),
        evidence.diagnostics_omitted,
    )


def _valid_evidence(evidence: object) -> bool:
    if type(evidence) is not TerminalEvidence or not isinstance(evidence.events, tuple):
        return False
    if len(evidence.events) > MAX_MESSAGES or any(
        type(flag) is not bool
        for flag in (evidence.invalid, evidence.limited, evidence.diagnostics_omitted)
    ):
        return False
    if (
        not isinstance(evidence.invalid_job_ids, tuple)
        or len(evidence.invalid_job_ids) > MAX_MESSAGES
        or not all(_valid_uuid(job_id) for job_id in evidence.invalid_job_ids)
    ):
        return False
    for event in evidence.events:
        if type(event) is not LifecycleEvent:
            return False
        if (
            not _valid_event_kind(event.kind)
            or not _valid_uuid(event.job_id)
            or (event.timestamp is not None and not _valid_integer(event.timestamp))
        ):
            return False
        for value, limit in (
            (event.node_id, 128),
            (event.node_type, 128),
            (event.exception_type, 256),
        ):
            if _optional_diagnostic(value, limit)[1]:
                return False
    return True


def _serialize(command: RunCommand) -> bytes:
    return json.dumps(
        asdict(command), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def encode_command(command: RunCommand) -> bytes:
    """Validate and encode an immutable command within the transport budget."""
    if type(command) not in (RunStart, RunFinish):
        raise ValueError("Invalid execution history command")
    valid = _valid_identity(
        command.session_id, command.queue_item_id, command.storage_epoch
    )
    if isinstance(command, RunStart):
        valid = (
            valid
            and _valid_uuid(command.run_id)
            and _valid_uuid(command.job_id)
            and _valid_integer(command.observed_dequeue_at)
        )
    else:
        valid = (
            valid
            and _valid_integer(command.observed_callback_at)
            and _valid_evidence(command.evidence)
        )
    if not valid:
        raise ValueError("Invalid execution history command")
    encoded = _serialize(command)
    if len(encoded) > MAX_COMMAND_BYTES:
        raise ValueError("Execution history command exceeds byte limit")
    return encoded
