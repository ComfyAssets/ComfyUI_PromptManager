"""Count prompt runs when ComfyUI dequeues a workflow, not when nodes execute.

ComfyUI skips nodes whose inputs are unchanged, so a PromptManager node does not
execute when the same prompt is re-run. Instead:

1. The on-prompt hook sees every submitted workflow before validation. It resolves
   the text of each PromptManager node feeding a sampler's positive input and
   stashes the hashes under the request's prompt_id (minting one when the client
   sent none; ComfyUI keeps a client-supplied id).
2. ``PromptQueue.get`` is wrapped. When ComfyUI dequeues a prompt for execution,
   its stashed hashes are counted: one run per prompt already in the database,
   otherwise a pending first use that the node consumes when it first saves it.

A prompt that fails validation or is deleted from the queue is never dequeued, so
it never counts. Servers without a ``prompt_queue`` fall back to counting at
submit time.

Text from known pure string nodes is resolved at queue time too. Anything else
(PromptSearchList batches, unknown nodes) is counted by the node as it executes,
and when ComfyUI serves such a node from its cache, the job replays what that
execution counted (``cached_runs``, #347).
"""

import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Callable, List, Optional, Tuple

try:
    from .hashing import generate_prompt_hash
    from .logging_config import get_logger
    from .prompt_graph import intrinsic_pair_nodes, resolve_text, run_prompt_components
    from .prompt_pair_resolver import resolve_text_input
    from .pair_execution import clear_pair_execution
    from .queue_observers import subscribe_dequeue
except ImportError:
    from utils.hashing import generate_prompt_hash
    from utils.logging_config import get_logger
    from utils.prompt_graph import (
        intrinsic_pair_nodes,
        resolve_text,
        run_prompt_components,
    )
    from utils.prompt_pair_resolver import resolve_text_input
    from utils.pair_execution import clear_pair_execution
    from utils.queue_observers import subscribe_dequeue

logger = get_logger("prompt_manager.usage_tracking")

PENDING_TTL_SECONDS = 3600  # a queued prompt that never executes is forgotten
PENDING_MAX_ENTRIES = 1000
QUEUED_MAX_ENTRIES = 1000  # prompts submitted but not yet dequeued

Run = Tuple[str, str]  # (prompt hash, resolved text)


class PendingFirstUse:
    """Dequeued runs of positive prompts that were not in the database yet.

    Counts per hash: the same new prompt can be run several times before its
    first job saves it, and later jobs may reuse ComfyUI's cached node.
    """

    def __init__(
        self,
        ttl_seconds: float = PENDING_TTL_SECONDS,
        max_entries: int = PENDING_MAX_ENTRIES,
    ):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._entries: OrderedDict[str, Tuple[int, float]] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, prompt_hash: str) -> None:
        with self._lock:
            count, _ = self._entries.pop(prompt_hash, (0, 0.0))
            self._entries[prompt_hash] = (count + 1, time.monotonic())
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def consume(self, prompt_hash: str) -> int:
        """Number of recent queued runs not yet counted (0 if none); clears them."""
        with self._lock:
            count, added = self._entries.pop(prompt_hash, (0, 0.0))
        return count if count and time.monotonic() - added <= self._ttl else 0


class QueuedRuns:
    """Runs resolved at submit time, keyed by prompt_id until ComfyUI dequeues them.

    Bounded: a prompt rejected by validation or deleted from the queue is never
    dequeued, so the oldest entries are evicted once the bound is reached.
    """

    def __init__(self, max_entries: int = QUEUED_MAX_ENTRIES):
        self._max = max_entries
        self._entries: OrderedDict[str, List[Run]] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, prompt_id: str, runs: List[Run]) -> None:
        with self._lock:
            self._entries.pop(prompt_id, None)
            if not runs:
                return
            self._entries[prompt_id] = list(runs)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def pop(self, prompt_id: str) -> List[Run]:
        with self._lock:
            return self._entries.pop(prompt_id, [])

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# Shared between the queue hook and the nodes
PENDING_FIRST_USE = PendingFirstUse()
QUEUED_RUNS = QueuedRuns()
PENDING_PAIR_FIRST_USE = PendingFirstUse()
QUEUED_PAIR_RUNS = QueuedRuns()


def queued_prompt_runs(graph: Any) -> List[Run]:
    """(hash, text) of each positive PromptManager node whose text is knowable.

    Each hash once: the same text in two positive nodes is one run of the prompt.
    """
    runs: List[Run] = []
    seen = set()
    for node_id, field in run_prompt_components(graph):
        node = graph.get(node_id, {}) if isinstance(graph, dict) else {}
        text = (
            resolve_text_input(graph, node_id, field)
            if node.get("class_type") == "PromptManagerPair"
            else resolve_text(graph, node_id)
        )
        if not text:
            continue
        prompt_hash = generate_prompt_hash(text)
        if prompt_hash in seen:
            continue
        seen.add(prompt_hash)
        runs.append((prompt_hash, text))
    return runs


def pair_run_key(positive: str, negative: str) -> str:
    """Stable ordered identity; an empty negative remains an explicit component."""
    return generate_prompt_hash(positive) + ":" + generate_prompt_hash(negative)


def queued_pair_runs(graph: Any) -> List[Tuple[str, str, str]]:
    """Resolve raw paired recipes only when both outputs form the used pair."""
    found, seen = [], set()
    for node_id in intrinsic_pair_nodes(graph):
        positive = resolve_text_input(graph, node_id, "text")
        negative = resolve_text_input(graph, node_id, "negative_text")
        if positive is None or not positive.strip() or negative is None:
            continue
        key = pair_run_key(positive, negative)
        if key not in seen:
            seen.add(key)
            found.append((key, positive, negative))
    return found


def find_pair_id(db: Any, positive: str, negative: str) -> Optional[int]:
    """Find a saved pair without creating or overwriting its metadata."""
    return find_pair_id_by_hashes(
        db,
        generate_prompt_hash(positive),
        generate_prompt_hash(negative) if negative.strip() else None,
    )


def find_pair_id_by_hashes(
    db: Any, positive_hash: str, negative_hash: Optional[str]
) -> Optional[int]:
    """The saved pair of two prompt hashes; ``None`` negative is the empty one."""
    positive_row = db.get_prompt_by_hash(positive_hash)
    negative_row = (
        db.get_prompt_by_hash(negative_hash) if negative_hash is not None else None
    )
    if not positive_row or (negative_hash is not None and not negative_row):
        return None
    row = (
        db.model.get_connection()
        .execute(
            "SELECT id FROM prompt_pairs "
            "WHERE positive_prompt_id=? AND negative_prompt_id IS ?",
            (positive_row["id"], negative_row["id"] if negative_row else None),
        )
        .fetchone()
    )
    return row["id"] if row else None


def survive_switch(db: Any, write: Callable[[], Any]) -> Any:
    """``write()`` through ``db.survive_library_switch`` when the database has it.

    A write that was waiting on the old file when Move library switched runs
    again on the new library instead of being lost (#249 S6b). Stand-in
    databases without the method (tests) just run ``write()``.
    """
    survive = getattr(type(db), "survive_library_switch", None)
    if survive is None:
        return write()
    return survive(db, write)


def count_pair_runs(
    runs: List[Tuple[str, str, str]],
    db: Any,
    pending: PendingFirstUse,
) -> None:
    """Count existing recipes at dequeue and defer new recipes until first save."""
    for key, positive, negative in runs:

        def count(positive=positive, negative=negative):
            pair_id = find_pair_id(db, positive, negative)
            if pair_id:
                db.record_prompt_pair_use(pair_id)
            return bool(pair_id)

        if not survive_switch(db, count):
            pending.add(key)


def count_runs(runs: List[Run], db: Any, pending: PendingFirstUse) -> None:
    """Record one use per prompt in the database; remember the rest for first save."""
    for prompt_hash, _text in runs:

        def count(prompt_hash=prompt_hash):
            existing = db.get_prompt_by_hash(prompt_hash)
            if existing:
                db.record_prompt_use(existing["id"])
            return bool(existing)

        if not survive_switch(db, count):
            pending.add(prompt_hash)


def handle_queued_prompt(
    json_data: Any,
    queued: QueuedRuns = QUEUED_RUNS,
    *,
    pair_queued: QueuedRuns = QUEUED_PAIR_RUNS,
) -> Any:
    """ComfyUI on-prompt handler body: stash the runs, always return the request.

    Adds a canonical UUID4 ``prompt_id`` when the client sent none, so the stash
    can be matched when ComfyUI dequeues the prompt. The database is not touched:
    the request has not been validated yet.
    """
    try:
        clear_pair_execution(json_data)
        graph = json_data.get("prompt") if isinstance(json_data, dict) else None
        runs = queued_prompt_runs(graph)
        pair_runs = queued_pair_runs(graph)
        if not runs and not pair_runs:
            if isinstance(json_data, dict) and json_data.get("prompt_id") is not None:
                queued.put(str(json_data["prompt_id"]), [])
                pair_queued.put(str(json_data["prompt_id"]), [])
            return json_data
        prompt_id = json_data.get("prompt_id")
        if prompt_id is None:
            prompt_id = str(uuid.uuid4())
            json_data["prompt_id"] = prompt_id
        queued.put(str(prompt_id), runs)
        pair_queued.put(str(prompt_id), pair_runs)
    except Exception as e:
        logger.warning(f"Could not resolve queued prompt runs: {e}")
    return json_data


def on_dequeue(
    prompt_id: str,
    queued: QueuedRuns,
    db_factory: Callable[[], Any],
    pending: PendingFirstUse,
    *,
    pair_queued: QueuedRuns = QUEUED_PAIR_RUNS,
    pair_pending: PendingFirstUse = PENDING_PAIR_FIRST_USE,
) -> None:
    """Count the runs stashed for a prompt ComfyUI is about to execute."""
    try:
        runs = queued.pop(str(prompt_id))
        pair_runs = pair_queued.pop(str(prompt_id))
        if runs or pair_runs:
            db = db_factory()
            count_runs(runs, db, pending)
            count_pair_runs(pair_runs, db, pair_pending)
    except Exception as e:
        logger.warning(f"Could not count runs of dequeued prompt {prompt_id}: {e}")


def count_queued_prompt(
    json_data: Any, db_factory: Callable[[], Any], pending: PendingFirstUse
) -> Any:
    """Legacy on-prompt handler body: count at submit time (no prompt_queue)."""
    try:
        clear_pair_execution(json_data)
        graph = json_data.get("prompt") if isinstance(json_data, dict) else None
        runs = queued_prompt_runs(graph)
        pair_runs = queued_pair_runs(graph)
        if runs or pair_runs:
            db = db_factory()
            count_runs(runs, db, pending)
            count_pair_runs(pair_runs, db, PENDING_PAIR_FIRST_USE)
    except Exception as e:
        logger.warning(f"Could not count queued prompt runs: {e}")
    return json_data


_WRAPPED_MARKER = "_prompt_manager_dequeue_hook"


def _wrap_queue_get(prompt_queue: Any, on_dequeued: Callable[[str], None]) -> bool:
    """Wrap ``prompt_queue.get`` so each dequeued prompt_id is reported once.

    The wrapper forwards every argument and result unchanged. Idempotent: a queue
    whose ``get`` is already wrapped is left alone.
    """
    original = getattr(prompt_queue, "get", None)
    if not callable(original):
        return False
    if getattr(prompt_queue, _WRAPPED_MARKER, False) or getattr(
        original, _WRAPPED_MARKER, False
    ):
        return False

    def report(result: object) -> None:
        try:
            on_dequeued(str(result[0][1]))
        except Exception as e:
            logger.warning(f"Dequeue hook failed: {e}")

    subscribe_dequeue(prompt_queue, "usage_tracking", report)
    setattr(prompt_queue.get, _WRAPPED_MARKER, True)
    setattr(prompt_queue, _WRAPPED_MARKER, True)
    return True


_hook_registered = threading.Event()
_register_lock = threading.Lock()


def register_queue_hook(
    server: Any,
    db_factory: Callable[[], Any],
    pending: PendingFirstUse = PENDING_FIRST_USE,
    queued: Optional[QueuedRuns] = None,
) -> bool:
    """Register the on-prompt handler once and wrap the prompt queue's ``get``.

    ``db_factory`` is called lazily, inside the guarded handlers, so a database
    that is unavailable never raises into ComfyUI. Without a usable
    ``server.prompt_queue`` runs are counted at submit time instead.
    """
    if queued is None:
        queued = QUEUED_RUNS
    with _register_lock:
        if _hook_registered.is_set():
            return False
        prompt_queue = getattr(server, "prompt_queue", None)
        if callable(getattr(prompt_queue, "get", None)):
            server.add_on_prompt_handler(
                lambda json_data: handle_queued_prompt(json_data, queued)
            )
            _wrap_queue_get(
                prompt_queue,
                lambda prompt_id: on_dequeue(prompt_id, queued, db_factory, pending),
            )
            logger.info("Registered prompt usage hook (counting at dequeue)")
        else:
            logger.warning(
                "PromptServer has no prompt_queue; counting prompt runs at queue "
                "time, so rejected prompts may be counted"
            )
            server.add_on_prompt_handler(
                lambda json_data: count_queued_prompt(json_data, db_factory, pending)
            )
        _hook_registered.set()
        return True
