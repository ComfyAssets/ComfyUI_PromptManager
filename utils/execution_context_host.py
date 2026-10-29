"""Pinned-host v1 adapter for local cached STRING evidence only."""

import functools
import threading
from typing import Callable

from .execution_context_capture import active_capture
from .execution_contexts import PromptSnapshot, TextEvidence

_STATE_ATTRIBUTE = "_prompt_manager_executor_messages_v1"
_registration_lock = threading.Lock()


class _Messages:
    def __init__(self, executor_class: type) -> None:
        self.executor_class = executor_class
        self.lock = threading.Lock()
        self.callbacks = {}
        self.wrapper = None
        self.original = None
        self.installed = False

    def subscribe(self, key: str, callback: Callable) -> Callable[[], None]:
        with self.lock:
            if not self.installed:
                original = self.executor_class.add_message
                if not callable(original):
                    raise ValueError("Executor message capability unavailable")

                @functools.wraps(original)
                def observe(executor, *args, **kwargs):
                    result = original(executor, *args, **kwargs)
                    with self.lock:
                        callbacks = tuple(entry[1] for entry in self.callbacks.values())
                    for notify in callbacks:
                        try:
                            notify(executor, args, kwargs)
                        except Exception:
                            # Best-effort observers cannot change host results.
                            pass
                    return result

                self.executor_class.add_message = observe
                self.original, self.wrapper, self.installed = original, observe, True
            existing = self.callbacks.get(key)
            token = existing[0] if existing is not None else object()
            if existing is None:
                self.callbacks[key] = (token, callback)

        def unsubscribe() -> None:
            with self.lock:
                current = self.callbacks.get(key)
                if current is None or current[0] is not token:
                    return
                del self.callbacks[key]
                if (
                    not self.callbacks
                    and self.executor_class.add_message is self.wrapper
                ):
                    self.executor_class.add_message = self.original
                    self.installed = False

        return unsubscribe


def subscribe_executor_messages(key: str, callback: Callable) -> Callable[[], None]:
    """Observe successful original calls; preserve earlier and later wrappers."""
    from execution import PromptExecutor

    with _registration_lock:
        state = vars(PromptExecutor).get(_STATE_ATTRIBUTE)
        if state is None:
            state = _Messages(PromptExecutor)
            setattr(PromptExecutor, _STATE_ATTRIBUTE, state)
        return state.subscribe(key, callback)


# STRING output slots of each PromptManager-family node, in output order
STRING_SLOTS = {
    "PromptManager": (1,),
    "PromptManagerFocused": (1,),
    "PromptManagerSplit": (1,),
    "PromptManagerText": (0,),
    "PromptManagerPair": (2, 3),
}
MAX_CACHED_VALUES = 64


def _local_cache_reader(executor: object) -> Callable | None:
    """``node_id -> outputs`` of the executor's local output cache, or None.

    Only the supported cache classes, only ``get_local`` (never a provider or
    an async lookup), only a real ``CacheEntry`` with at most four outputs.
    """
    from comfy_execution import caching
    from execution import CacheEntry

    cache = executor.caches.outputs
    supported = tuple(
        cls
        for name in ("HierarchicalCache", "LRUCache", "RAMPressureCache")
        if isinstance(cls := getattr(caching, name, None), type)
    )
    if type(cache) not in supported:
        return None
    local = getattr(cache, "get_local", None)
    if not callable(local):
        return None

    def read(node_id: str) -> list | tuple | None:
        entry = local(node_id)
        if type(entry) is not CacheEntry:
            return None
        outputs = entry.outputs
        if type(outputs) not in (list, tuple) or not 0 < len(outputs) <= 4:
            return None
        return outputs

    return read


def cached_string_outputs(
    executor: object, node_id: str, node_type: str
) -> tuple[tuple[str, ...], ...] | None:
    """One tuple per STRING slot of a cached node's local entry, or None (#347).

    None when the cache or entry is not supported, a slot is missing or holds
    more than 64 values, any value is not a ``str``, or the slots differ in
    length. Never runs a provider or an async lookup.
    """
    try:
        slots = STRING_SLOTS.get(node_type)
        if slots is None or type(node_id) is not str:
            return None
        local = _local_cache_reader(executor)
        outputs = local(node_id) if local is not None else None
        if outputs is None or not max(slots) < len(outputs):
            return None
        found = []
        for slot in slots:
            values = outputs[slot]
            if type(values) not in (list, tuple) or len(values) > MAX_CACHED_VALUES:
                return None
            if any(type(value) is not str for value in values):
                return None
            found.append(tuple(values))
        if len({len(values) for values in found}) != 1:
            return None
        return tuple(found)
    except Exception:
        return None


def _receipt_fields(
    receipts: object, executor: object, job_id: str, source: object
) -> list | None:
    """Per list index, the ``(raw, prepend, append)`` of the execution reused (#403).

    From the #347 receipt whose digests equal every STRING slot of the cache
    entry; None when there is none or the answer is malformed.
    """
    try:
        if receipts is None:
            try:
                from .cached_runs import CACHED_RUNS as receipts
            except ImportError:
                from utils.cached_runs import CACHED_RUNS as receipts
        values = cached_string_outputs(executor, source.node_id, source.node_type)
        if values is None:
            return None
        found = receipts.recorded_fields(job_id, source.node_id, values)
        if type(found) is not list or len(found) != len(values[0]):
            return None
        position = (
            (1 if source.output_slot in (1, 3) else 0)
            if source.node_type == "PromptManagerPair"
            else 0
        )
        fields = []
        for slots in found:
            slot = slots[position] if type(slots) is tuple else None
            if not (
                type(slot) is tuple
                and len(slot) == 3
                and all(type(value) is str for value in slot)
            ):
                return None
            fields.append(slot)
        return fields
    except Exception:
        return None


def _agrees(inferred: PromptSnapshot, fields: tuple) -> bool:
    """Every field the graph knows equals the receipt's; else fill nothing."""
    known = (inferred.raw, inferred.prepend, inferred.append)
    return all(
        field.value is None or field.value == value
        for field, value in zip(known, fields)
    )


def _filled(inferred: TextEvidence, value: str) -> TextEvidence:
    """A field the graph cannot show, taken from the reused execution (#403)."""
    if inferred.value is not None:
        return inferred
    return TextEvidence(value, "inferred", "graph_semantics", None)


def observe_cached_context(
    executor: object,
    message_type: object,
    message_data: object,
    *,
    receipts: object = None,
) -> None:
    """Copy bounded local strings without cache/provider execution or retention.

    Raw, prepend and append come from graph inference; a field it leaves
    unknown (runtime-only text) is taken from the receipt of the execution the
    cache entry is proven to come from (``CachedRuns.recorded_fields``), so the
    cached job is a tracked run of its prompt (#403). ``receipts`` defaults to
    the process ``CACHED_RUNS``.
    """
    try:
        binding = active_capture()
        if binding is None or type(message_type) is not str:
            return
        manager, cap = binding
        if (
            type(message_data) is not dict
            or type(message_data.get("prompt_id")) is not str
            or message_data["prompt_id"] != cap.job_id
            or id(executor.server.prompt_queue) != manager.queue_identity
        ):
            return
        if message_type == "execution_start":
            manager.bind_executor(cap, id(executor))
            return
        if message_type != "execution_cached" or not manager.matches_executor(
            cap, id(executor)
        ):
            return
        nodes = message_data.get("nodes")
        if type(nodes) not in (list, tuple) or len(nodes) > 500:
            return
        local = _local_cache_reader(executor)
        if local is None:
            return
        # Only source IDs retained by the bounded submitted graph are queried.
        for source in cap.sources:
            if not any(type(node) is str and node == source.node_id for node in nodes):
                continue
            slot = (
                (3 if source.output_slot in (1, 3) else 2)
                if source.node_type == "PromptManagerPair"
                else 0 if source.node_type == "PromptManagerText" else 1
            )
            outputs = local(source.node_id)
            if outputs is None or not slot < len(outputs):
                continue
            values = outputs[slot]
            if type(values) not in (list, tuple) or len(values) > 64:
                continue
            inferred = manager.source_snapshot(cap, source)
            if inferred is None:
                continue
            fields = None
            if any(
                field.value is None
                for field in (inferred.raw, inferred.prepend, inferred.append)
            ):
                fields = _receipt_fields(receipts, executor, cap.job_id, source)
            for index, value in enumerate(values):
                if type(value) is not str:
                    continue
                raw, prepend, append = inferred.raw, inferred.prepend, inferred.append
                if fields is not None and _agrees(inferred, fields[index]):
                    raw = _filled(raw, fields[index][0])
                    prepend = _filled(prepend, fields[index][1])
                    append = _filled(append, fields[index][2])
                manager.observe(
                    cap,
                    source,
                    index,
                    PromptSnapshot(
                        raw,
                        TextEvidence(value, "recorded", "host_cache_output", None),
                        prepend,
                        append,
                    ),
                )
    except Exception:
        # Unknown host versions and malformed entries leave evidence unavailable.
        return
