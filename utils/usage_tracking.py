"""Count prompt runs when a workflow is queued, not when nodes execute.

ComfyUI skips nodes whose inputs are unchanged, so a PromptManager node does not
execute when the same prompt is re-run. The on-prompt hook sees every queued
workflow instead: it counts a run for each PromptManager node feeding a sampler's
positive input. Prompts not yet in the database are remembered as pending and
counted by the node when it first saves them.

Text supplied by another node (PromptSearchList batches) is unknown at queue time;
those runs are counted by the node as each item executes.
"""

import threading
import time
from collections import OrderedDict
from typing import Any

try:
    from .hashing import generate_prompt_hash
    from .logging_config import get_logger
    from .prompt_graph import literal_text, run_prompt_nodes
except ImportError:
    from utils.hashing import generate_prompt_hash
    from utils.logging_config import get_logger
    from utils.prompt_graph import literal_text, run_prompt_nodes

logger = get_logger("prompt_manager.usage_tracking")

PENDING_TTL_SECONDS = 3600  # a queued prompt that never executes is forgotten
PENDING_MAX_ENTRIES = 1000


class PendingFirstUse:
    """Hashes of queued positive prompts that were not in the database yet."""

    def __init__(
        self,
        ttl_seconds: float = PENDING_TTL_SECONDS,
        max_entries: int = PENDING_MAX_ENTRIES,
    ):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._entries: "OrderedDict[str, float]" = OrderedDict()
        self._lock = threading.Lock()

    def add(self, prompt_hash: str) -> None:
        with self._lock:
            self._entries.pop(prompt_hash, None)
            self._entries[prompt_hash] = time.monotonic()
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def consume(self, prompt_hash: str) -> bool:
        """True (once) if this hash was queued recently and not yet counted."""
        with self._lock:
            added = self._entries.pop(prompt_hash, None)
        return added is not None and time.monotonic() - added <= self._ttl


# Shared between the queue hook and the nodes
PENDING_FIRST_USE = PendingFirstUse()


def handle_queued_prompt(json_data: Any, db: Any, pending: PendingFirstUse) -> Any:
    """ComfyUI on-prompt handler body: count runs, always return the request as-is."""
    try:
        graph = json_data.get("prompt") if isinstance(json_data, dict) else None
        counted = set()
        for node_id in run_prompt_nodes(graph):
            text = literal_text(graph, node_id)
            if not text:
                continue
            prompt_hash = generate_prompt_hash(text)
            if prompt_hash in counted:
                continue
            counted.add(prompt_hash)
            existing = db.get_prompt_by_hash(prompt_hash)
            if existing:
                db.record_prompt_use(existing["id"])
            else:
                pending.add(prompt_hash)
    except Exception as e:
        logger.warning(f"Could not count queued prompt runs: {e}")
    return json_data


_registered = False
_register_lock = threading.Lock()


def register_queue_hook(
    server: Any, db_factory: Any, pending: PendingFirstUse = PENDING_FIRST_USE
) -> bool:
    """Register the on-prompt handler once; db_factory is called lazily per request."""
    global _registered
    with _register_lock:
        if _registered:
            return False
        server.add_on_prompt_handler(
            lambda json_data: handle_queued_prompt(json_data, db_factory(), pending)
        )
        _registered = True
        logger.info("Registered prompt usage hook")
        return True
