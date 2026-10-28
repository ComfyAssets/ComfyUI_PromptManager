"""The per-file prompt decision shared by the output scan and the monitor (#247).

``decide_file_prompt`` is the ladder: which prompt one output file names,
from its metadata alone plus, when the graph cannot decide, the save node
recorded history says wrote it. It does no file or database I/O; the caller
reads the metadata, the file size and history (through ``lookup``) and
stores the ``Decision``.

The default resolver profile decides first, as ingestion always has. Only
for a file it leaves ambiguous or unresolved does the ladder ask the
inspector's resolve (``inspection_resolution``: the inspection profile, then
the recorded saver anchor; never the file-name saver anchor, whose files go
to Needs review by the #390 ruling), then the #170 browser parser (the legacy floor).
The policies differ in one place: for a file the inspector calls ambiguous,
the scan keeps the legacy floor and the monitor stores nothing. Junk text
(``junk_text``) is never stored, whichever step found it.
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Union

from .generation_params import (
    PLACEHOLDERS,
    GenerationParamsError,
    extract_generation_params,
    parse_page_json,
)
from .prompt_graph import (
    PROMPT_MANAGER_TYPES,
    metadata_opted_out,
    save_setting,
    side_opted_out,
)
from .prompt_inspector import inspection_resolution
from .prompt_pair_resolver import _numeric, resolve_prompt_pair, sampler_less_prompt
from .review_capture import reviewable

try:
    from ..database.pair_ingestion import blank_positive
except ImportError:
    from database.pair_ingestion import blank_positive


class Policy(Enum):
    SCAN = "scan"
    MONITOR = "monitor"


PAIR = "pair"
POSITIVE = "positive"
HISTORY = "history"
NOT_SAVED = "not_saved"
NOT_SAVED_WIRED = "not_saved_wired"
NEVER = "never"
KINDS = (PAIR, POSITIVE, HISTORY, NOT_SAVED, NOT_SAVED_WIRED, NEVER)

_UNDECIDED = ("ambiguous", "unresolved")


@dataclass(frozen=True)
class Decision:
    """What to store for one file (see the design's "The ladder").

    ``kind``: one of ``KINDS``. ``text``: the storage text of the positive.
    ``canonical``: the parsed metadata stored with the link. ``resolution``:
    the resolution that decided a ``pair``, else the default profile's
    result. ``source``: which step named the text (log lines and the scan's
    outcome detail). ``review``: ``ambiguous`` or ``untraced`` when the
    default profile was ambiguous (the scan's Needs review lists).
    ``try_recorded_run``: the scan tries the recorded run's stored prompt
    when nothing else links. ``unlinked_status``: the scan status when
    nothing links (None: no prompt metadata).
    """

    kind: str
    text: Optional[str] = None
    canonical: dict = field(default_factory=dict)
    resolution: Optional[dict] = None
    source: str = "none"
    review: Optional[str] = None
    try_recorded_run: bool = False
    unlinked_status: Optional[str] = None


@dataclass(frozen=True)
class _File:
    """The ladder's inputs beside the metadata."""

    metadata: Mapping
    canonical: dict
    resolution: dict
    review: Optional[str]
    file_name: Optional[str]
    in_output: bool
    lookup: Optional[Callable[[], Any]]
    policy: Policy


def decide_file_prompt(
    metadata: Any,
    *,
    file_name: Optional[str],
    file_size: Union[None, int, Callable[[], Optional[int]]],
    in_output: bool,
    lookup: Optional[Callable[[], Any]],
    policy: Policy,
) -> Decision:
    """The ladder for one file's raw ``metadata``.

    ``prompt``/``workflow`` may be JSON strings (the scan) or parsed values
    (the monitor). ``lookup`` returns the file's ``RecordedOutput`` or None;
    it is None when history must not be asked yet. It is called at most
    once, and only for a graph the default profile leaves undecided.
    ``file_size`` may be a callable: it is read only for a file with prompt
    metadata whose ``file_info`` has no size.
    """
    if not metadata:
        return Decision(NEVER, try_recorded_run=True)
    canonical = _parsed(metadata)
    if metadata_opted_out(canonical):
        # Save to library was off for the run's prompt: nothing is stored,
        # no recorded-run fallback, no Needs review item (#90)
        return Decision(NOT_SAVED, canonical=canonical)
    if metadata_opted_out(canonical, unreadable=True):
        # The switch is wired from a node the file cannot show: only the
        # live run (the node saw the value) can link it
        return Decision(NOT_SAVED_WIRED, canonical=canonical)
    if not canonical.get("prompt") and not canonical.get("parameters"):
        return Decision(NEVER, canonical=canonical, try_recorded_run=True)
    _with_size(canonical, file_size)
    resolution = resolve_prompt_pair(canonical)
    if resolution["state"] == "resolved" and side_opted_out(
        canonical.get("prompt"), resolution["positive"]
    ):
        # The positive passes a node that is off, though the #90 graph walk
        # did not reach it (steps 5 and 7)
        return Decision(NOT_SAVED, canonical=canonical)
    if resolution["state"] == "resolved" and side_opted_out(
        canonical.get("prompt"), resolution["negative"]
    ):
        # Only the negative's node is off: the positive alone, never a pair
        positive = resolution["positive"]
        text = positive.get("storage_text", positive["text"]).strip()
        return _gated(
            Decision(
                POSITIVE,
                text=text,
                canonical=canonical,
                resolution=resolution,
                source="opted-out-negative",
            ),
            None,
        )
    if blank_positive(resolution):
        # Only prepend/append text: no stored prompt can own it (#342)
        resolution = {**resolution, "state": "unresolved"}
    found = _File(
        metadata,
        canonical,
        resolution,
        _review(resolution),
        file_name,
        in_output,
        lookup,
        policy,
    )
    return _gated(_decide(found), found)


def _decide(found: _File) -> Decision:
    state = found.resolution["state"]
    graph = found.canonical.get("prompt")
    if state == "resolved":
        positive = found.resolution["positive"]
        text = positive.get("storage_text", positive["text"])
        return _decision(found, PAIR, text, "resolver")
    if graph and state in _UNDECIDED:
        return _undecided_graph(found)
    if state == "ambiguous":
        return _never(found, _unlinked(found))
    if state == "unresolved" and not isinstance(found.canonical.get("parameters"), str):
        return _never(found, "unresolved")
    if graph:
        return _sampler_less(found, graph)
    text = _a1111_positive(found.metadata)
    if text is None:
        return _never(found, "found")
    return _decision(found, POSITIVE, text, "a1111")


def _undecided_graph(found: _File) -> Decision:
    recorded = found.lookup() if found.lookup is not None else None
    saver = recorded.saver_id if recorded is not None else None
    inspected = inspection_resolution(
        found.metadata, found.file_name, saver, name_anchor=False
    )
    text = _inspected_text(inspected.resolution)
    if text is not None and side_opted_out(
        found.canonical["prompt"], inspected.resolution["positive"]
    ):
        # The inspector follows pass-throughs the #90 graph walk does not
        return Decision(NOT_SAVED, canonical=found.canonical)
    if text is not None:
        source = "recorded-run" if inspected.by_record else "inspector"
        return _decision(found, POSITIVE, text, source)
    if found.policy is Policy.MONITOR:
        if found.lookup is None and found.in_output:
            return _decision(found, HISTORY, None, "none")
        if not inspected.malformed and inspected.resolution["state"] == "ambiguous":
            return _never(found, _unlinked(found))
    text = graph_prompt_text(found.metadata)
    if text is not None and _off_text(found.canonical["prompt"], text):
        # The legacy answer is a switched-off node's own text (#90)
        return Decision(NOT_SAVED, canonical=found.canonical)
    if text is not None and _joined_text(found.canonical["prompt"], text):
        # The legacy parser joins prepend + text + append: the text a
        # PromptManager node stores cannot be told from it (#343).
        return _never(found, _unlinked(found), try_recorded_run=True)
    if text is not None:
        return _decision(found, POSITIVE, text, "legacy-parser")
    return _never(found, _unlinked(found), try_recorded_run=True)


def _sampler_less(found: _File, graph: Any) -> Decision:
    # No node takes a positive or negative input (a guider or wrapper graph):
    # the one wired text, never a guess (#252).
    wired = sampler_less_prompt(graph)
    if wired["text"] is not None:
        return _decision(found, POSITIVE, wired["text"], "sampler-less")
    if isinstance(found.canonical.get("parameters"), str):
        # A metadata saver also wrote A1111 text: its positive prompt.
        text = _a1111_positive(found.metadata)
        if text is not None:
            return _decision(found, POSITIVE, text, "a1111")
    status = "unresolved" if wired["state"] == "ambiguous" else "found"
    return _never(found, status, try_recorded_run=True)


_UNDECIDED_SOURCES = ("inspector", "recorded-run", "legacy-parser")


def _off_text(graph: Any, text: str) -> bool:
    """Whether ``text`` is the literal text of a PromptManager-family node
    whose Save to library is not on."""
    if not isinstance(graph, dict):
        return False
    wanted = text.strip()
    for node_id, node in graph.items():
        if not isinstance(node, dict) or node.get("class_type") not in (
            PROMPT_MANAGER_TYPES
        ):
            continue
        inputs = node.get("inputs")
        literal = inputs.get("text") if isinstance(inputs, dict) else None
        if not isinstance(literal, str) or literal.strip() != wanted:
            continue
        if save_setting(graph, node_id) is not True:
            return True
    return False


def _joined_text(graph: Any, text: str) -> bool:
    """Whether ``text`` may hold a PromptManager node's prepend or append.

    True when a PromptManager-family node has a prepend or append part and
    ``text`` is not the literal ``text`` of one such node (the legacy
    parser's answer when it does not follow a linked part).
    """
    own = _prompt_manager_parts(graph)
    return own is not None and text.strip() not in own


def _prompt_manager_parts(graph: Any) -> Optional[set]:
    """The literal texts of PromptManager-family nodes with a prepend or
    append part, or None when no such node exists."""
    if not isinstance(graph, dict):
        return None
    found = None
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        name, inputs = node.get("class_type"), node.get("inputs")
        if not isinstance(name, str) or "promptmanager" not in name.lower():
            continue
        if not isinstance(inputs, dict) or not _has_part(inputs):
            continue
        found = found if found is not None else set()
        if isinstance(inputs.get("text"), str):
            found.add(inputs["text"].strip())
    return found


def _has_part(inputs: dict) -> bool:
    for key in ("prepend_text", "append_text"):
        part = inputs.get(key)
        if part is not None and not (isinstance(part, str) and not part.strip()):
            return True
    return False


def _gated(decision: Decision, found: Optional[_File]) -> Decision:
    """Junk text is never stored (step 13).

    A junk answer of the undecided-graph step keeps that step's unlinked
    status, so an ambiguous file is still a Needs review case; any other
    step reports ``found``.
    """
    if decision.kind not in (PAIR, POSITIVE):
        return decision
    junk = junk_text(decision.text)
    if junk is None:
        return decision
    return Decision(
        NEVER,
        canonical=decision.canonical,
        resolution=decision.resolution,
        source=f"junk:{junk}",
        review=decision.review,
        try_recorded_run=True,
        unlinked_status=(
            _unlinked(found)
            if found is not None and decision.source in _UNDECIDED_SOURCES
            else "found"
        ),
    )


def _decision(found: _File, kind: str, text: Optional[str], source: str) -> Decision:
    return Decision(
        kind,
        text=text,
        canonical=found.canonical,
        resolution=found.resolution,
        source=source,
        review=found.review,
    )


def _never(found: _File, status: str, *, try_recorded_run: bool = False) -> Decision:
    return Decision(
        NEVER,
        canonical=found.canonical,
        resolution=found.resolution,
        review=found.review,
        try_recorded_run=try_recorded_run,
        unlinked_status=status,
    )


def _review(resolution: dict) -> Optional[str]:
    if resolution["state"] != "ambiguous":
        return None
    return "ambiguous" if reviewable(resolution) else "untraced"


def _unlinked(found: _File) -> str:
    """Ambiguous without a choice is reported unresolved."""
    if found.review == "untraced":
        return "unresolved"
    return found.resolution["state"]


def _inspected_text(resolution: dict) -> Optional[str]:
    """The storage text of a positive the inspector shows, else None."""
    if resolution.get("state") in _UNDECIDED:
        return None
    positive = resolution.get("positive")
    if not isinstance(positive, dict):
        return None
    text = positive.get("storage_text", positive.get("text"))
    return text if isinstance(text, str) and text.strip() else None


def _parsed(metadata: Mapping) -> dict:
    canonical = dict(metadata)
    for key in ("prompt", "workflow"):
        if isinstance(canonical.get(key), str):
            try:
                canonical[key] = json.loads(canonical[key])
            except (ValueError, TypeError):
                # Preserve malformed metadata for conservative resolver rejection.
                pass
    return canonical


def _with_size(canonical: dict, file_size: Any) -> None:
    """Record the file's size in ``file_info`` unless the metadata carries one.

    Stored as ``generated_images.file_size``; the "new" scan scope skips a
    known file only while its size is unchanged.
    """
    info = canonical.get("file_info")
    info = dict(info) if isinstance(info, dict) else {}
    if info.get("size") is None:
        size = file_size() if callable(file_size) else file_size
        if size is not None:
            info["size"] = size
    if info:
        canonical["file_info"] = info


def _a1111_positive(metadata: Mapping) -> Optional[str]:
    text = parse_comfyui_prompt(metadata)["positive_prompt"]
    return text.strip() if text and text.strip() else None


_LORA_TAG = re.compile(r"<lora:[^>]*>")
_LORA_SEPARATORS = " \t\r\n\f\v,;|"
_MODEL_FILE = re.compile(r"\S*\.(?:safetensors|ckpt|pt|pth|bin|gguf|sft)", re.I)


def junk_text(text: Any) -> Optional[str]:
    """The #252 junk class of ``text``, or None when it may be a prompt."""
    stripped = text.strip() if isinstance(text, str) else ""
    if not stripped:
        return "blank"
    if _numeric(stripped):
        return "number"
    if not _LORA_TAG.sub("", stripped).strip(_LORA_SEPARATORS):
        return "lora-only"
    if _MODEL_FILE.fullmatch(stripped):
        return "model-file"
    return None


def graph_prompt_text(metadata: Mapping) -> Optional[str]:
    """The positive prompt the browser parser traces from the graphs, or None.

    Records parse as the legacy pages parsed them (NaN reads as null).
    """
    records = {}
    for key in ("prompt", "workflow"):
        value = metadata.get(key)
        records[key] = parse_page_json(value) if isinstance(value, str) else value
    try:
        positive = extract_generation_params(records)["positivePrompt"]
    except GenerationParamsError:
        return None
    if not isinstance(positive, str) or positive == PLACEHOLDERS["positivePrompt"]:
        return None
    return positive.strip() or None


_COMMON_FIELDS = (
    "positive",
    "negative",
    "steps",
    "cfg",
    "sampler",
    "scheduler",
    "seed",
)


def _json_or_raw(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def parse_comfyui_prompt(metadata: Mapping) -> dict:
    """Parse ComfyUI and A1111 prompt data from metadata.

    Accepts the file's strings or parsed values: a parsed ``prompt`` or
    ``workflow`` is kept as it is and a non-string ``parameters`` is ignored.
    """
    result = {
        "prompt": None,
        "workflow": None,
        "parameters": {},
        "positive_prompt": None,
        "negative_prompt": None,
    }
    params = metadata.get("parameters")
    if isinstance(params, str):
        _parse_a1111(params, result)
    if result["positive_prompt"] is None:
        for key in ("prompt", "workflow"):
            if key in metadata:
                result[key] = _json_or_raw(metadata[key])
        for name in _COMMON_FIELDS:
            if name in metadata:
                result["parameters"][name] = _json_or_raw(metadata[name])
    return result


_SETTINGS_FIELDS = ("Steps", "Sampler", "Schedule type", "CFG scale", "Seed")


def _parse_a1111(params: str, result: dict) -> None:
    """A1111/Forge ``parameters``: a possibly multi-line positive, then a
    "Negative prompt:" section, then a trailing "Steps: ..." settings line.
    Taking the first line as the positive would truncate a multi-line prompt,
    so the text is split on the settings line instead.
    """
    body = params
    settings = ""
    found = re.search(r"\nSteps:\s", body)
    if found:
        settings = body[found.start() + 1 :].strip()
        body = body[: found.start()]
    negative = None
    found = re.search(r"(?im)^\s*negative prompt:\s*", body)
    if found:
        negative = body[found.end() :].strip()
        body = body[: found.start()]
    elif body.lower().lstrip().startswith("negative prompt:"):
        negative = body.lower().lstrip()[len("negative prompt:") :].strip()
        body = ""
    result["positive_prompt"] = body.strip() or None
    if negative:
        result["negative_prompt"] = negative
    result["parameters"]["parameters"] = params
    for name in _SETTINGS_FIELDS if settings else ():
        found = re.search(
            r"(?i)(?:^|,\s*)" + re.escape(name) + r":\s*([^,]+)", settings
        )
        if found:
            key = name.lower().replace(" ", "_")
            result["parameters"][key] = found.group(1).strip()
