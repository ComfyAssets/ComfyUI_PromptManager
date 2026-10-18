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


def observe_cached_context(
    executor: object, message_type: object, message_data: object
) -> None:
    """Copy bounded local strings without cache/provider execution or retention."""
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
        from comfy_execution import caching
        from execution import CacheEntry

        cache = executor.caches.outputs
        supported = tuple(
            cls
            for name in ("HierarchicalCache", "LRUCache", "RAMPressureCache")
            if isinstance(cls := getattr(caching, name, None), type)
        )
        if type(cache) not in supported:
            return
        local = getattr(cache, "get_local", None)
        if not callable(local):
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
            entry = local(source.node_id)
            if type(entry) is not CacheEntry:
                continue
            outputs = entry.outputs
            if type(outputs) not in (list, tuple) or not slot < len(outputs) <= 4:
                continue
            values = outputs[slot]
            if type(values) not in (list, tuple) or len(values) > 64:
                continue
            inferred = manager.source_snapshot(cap, source)
            if inferred is None:
                continue
            for index, value in enumerate(values):
                if type(value) is not str:
                    continue
                manager.observe(
                    cap,
                    source,
                    index,
                    PromptSnapshot(
                        inferred.raw,
                        TextEvidence(value, "recorded", "host_cache_output", None),
                        inferred.prepend,
                        inferred.append,
                    ),
                )
    except Exception:
        # Unknown host versions and malformed entries leave evidence unavailable.
        return
