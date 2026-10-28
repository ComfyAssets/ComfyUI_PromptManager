"""Count a cached PromptManager node like the execution it reuses (#347).

The queue hook (``usage_tracking``) counts text it can read from the queued
graph. Text only known at run time (a PreviewAny or LLM link, a list node) is
counted by the node itself when it executes. When ComfyUI later serves the node
from its output cache the node does not execute, so without this module that
job is not counted.

When a PromptManager-family node executes it records a *receipt item*: the
role-independent facts its own count is made from (the stored text hashes)
and digests of the STRING outputs it returned. When ComfyUI reports the node
as cached for a later job (``execution_cached``), the receipt whose digests
equal the text in the cache entry is replayed once for that job. The node's
role (positive, negative, which Pair outputs are used) and what the hook
already saw are recomputed from that job's own graph, and both paths count
through the same ``own_counts`` so they cannot drift.

Fail closed: when the cached text cannot be matched to a receipt recorded in
this process, nothing is counted (the behavior before #347). In memory only and
bounded; starts no threads.
"""

import hashlib
import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, FrozenSet, List, NamedTuple, Optional, Tuple

try:
    from .hashing import generate_prompt_hash
    from .logging_config import get_logger
    from .prompt_graph import (
        PROMPT_MANAGER_TYPES,
        intrinsic_pair_nodes,
        resolve_text,
        run_prompt_components,
        run_prompt_nodes,
    )
    from .prompt_pair_resolver import resolve_text_input
    from .queue_observers import subscribe_dequeue, subscribe_task_done
    from .run_keys import prompt_node_key, upstream_nodes
    from .usage_tracking import find_pair_id_by_hashes, survive_switch
except ImportError:
    from utils.hashing import generate_prompt_hash
    from utils.logging_config import get_logger
    from utils.prompt_graph import (
        PROMPT_MANAGER_TYPES,
        intrinsic_pair_nodes,
        resolve_text,
        run_prompt_components,
        run_prompt_nodes,
    )
    from utils.prompt_pair_resolver import resolve_text_input
    from utils.queue_observers import subscribe_dequeue, subscribe_task_done
    from utils.run_keys import prompt_node_key, upstream_nodes
    from utils.usage_tracking import find_pair_id_by_hashes, survive_switch

logger = get_logger("prompt_manager.cached_runs")

PAIR = "PromptManagerPair"
MAX_KEYS = 256  # run keys remembered (the same bound as MAX_KEYED_RUNS)
MAX_RECEIPTS = 4  # receipts per run key: a recurring upstream value (A, B, A)
MAX_ITEMS = 64  # list items per receipt (the cache reader's bound)
MAX_CACHED_NODES = 500  # the execution_cached guard the history capture uses
_EMPTY_HASH = generate_prompt_hash("")

Count = Tuple[Any, ...]  # ("prompt", hash) or ("pair", positive, negative or None)


class Facts(NamedTuple):
    """What one execution stored: hashes of the stored text, None when nothing.

    ``positive_hash`` is None for an empty editor (or saving disabled);
    ``negative_hash`` (Pair only) is None for an empty negative, which the pair
    stores as ``negative_prompt_id IS NULL``.
    """

    positive_hash: Optional[str]
    negative_hash: Optional[str] = None


class Role(NamedTuple):
    """How a job's graph uses the node: which editors reach a sampler's positive."""

    used_fields: FrozenSet[str]
    pair_used: bool = False


class Seen(NamedTuple):
    """Which of the node's texts the queue hook resolved, and so already counted."""

    text: bool = False
    negative_text: bool = False
    pair: bool = False


class _Item(NamedTuple):
    outputs: Tuple[str, ...]
    facts: Facts


class _Receipt:
    __slots__ = ("item_id", "items")

    def __init__(self, item_id: int) -> None:
        self.item_id = item_id
        self.items: Dict[int, _Item] = {}

    def outputs(self) -> Dict[int, Tuple[str, ...]]:
        return {index: item.outputs for index, item in self.items.items()}


class _Attempt(NamedTuple):
    item_id: int
    prompt_id: str
    graph: Dict[str, Any]
    nodes: Dict[str, Tuple[str, str]]  # node id -> (class, run key)
    replayed: set


def text_digest(value: str) -> str:
    """Digest of one returned STRING value, exactly as returned."""
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def stored_facts(text: Any, negative_text: Any = None) -> Facts:
    """The facts of an execution that stores ``text`` (and a Pair's negative)."""
    positive = text if isinstance(text, str) and text.strip() else None
    negative = (
        negative_text
        if isinstance(negative_text, str) and negative_text.strip()
        else None
    )
    return Facts(
        generate_prompt_hash(positive) if positive is not None else None,
        generate_prompt_hash(negative) if negative is not None else None,
    )


def pair_components(facts: Facts, role: Role) -> List[Tuple[str, str]]:
    """``(field, hash)`` of each used, stored Pair component; each hash once."""
    found, counted = [], set()
    for field, prompt_hash in (
        ("text", facts.positive_hash),
        ("negative_text", facts.negative_hash),
    ):
        if field not in role.used_fields or prompt_hash is None:
            continue
        if prompt_hash in counted:
            continue
        counted.add(prompt_hash)
        found.append((field, prompt_hash))
    return found


def own_counts(node_type: str, facts: Facts, role: Role, seen: Seen) -> List[Count]:
    """What one execution of a node counts itself: text the hook could not see.

    Shared by the executing node and by replay. Excludes the pending first-use
    catch-up, which only an execution that saves the prompt can consume.
    """
    if facts.positive_hash is None:
        return []
    if node_type != PAIR:
        if "text" in role.used_fields and not seen.text:
            return [("prompt", facts.positive_hash)]
        return []
    counts: List[Count] = [
        ("prompt", prompt_hash)
        for field, prompt_hash in pair_components(facts, role)
        if not getattr(seen, field)
    ]
    if role.pair_used and not seen.pair:
        counts.append(("pair", facts.positive_hash, facts.negative_hash))
    return counts


def graph_role(graph: Any, node_id: str, node_type: str) -> Role:
    """The node's role in a queued graph, by the functions the nodes use."""
    if node_type != PAIR:
        used = {"text"} if node_id in run_prompt_nodes(graph) else set()
        return Role(frozenset(used))
    used = {
        field for source, field in run_prompt_components(graph) if source == node_id
    }
    return Role(frozenset(used), node_id in intrinsic_pair_nodes(graph))


def _same(resolved: Optional[str], prompt_hash: Optional[str]) -> bool:
    return resolved is not None and generate_prompt_hash(resolved) == prompt_hash


def graph_seen(graph: Any, node_id: str, node_type: str, facts: Facts) -> Seen:
    """Which texts the queue hook resolved from ``graph`` (as the node decides)."""
    if node_type != PAIR:
        return Seen(text=_same(resolve_text(graph, node_id), facts.positive_hash))
    positive = _same(resolve_text_input(graph, node_id, "text"), facts.positive_hash)
    negative = _same(
        resolve_text_input(graph, node_id, "negative_text"),
        facts.negative_hash or _EMPTY_HASH,
    )
    return Seen(positive, negative, positive and negative)


def _host_context() -> Tuple[Optional[str], Optional[int]]:
    """(prompt id, list index) of the node executing on this thread, if known."""
    try:
        from comfy_execution.utils import get_executing_context
    except Exception:
        return None, None
    try:
        context = get_executing_context()
    except Exception:
        return None, None
    prompt_id = getattr(context, "prompt_id", None)
    index = getattr(context, "list_index", None)
    return (
        prompt_id if isinstance(prompt_id, str) and prompt_id else None,
        index if type(index) is int else None,
    )


def write_count(db: Any, count: Count) -> bool:
    """Record one counted run; False when the prompt or pair no longer exists."""

    def write() -> bool:
        if count[0] == "prompt":
            row = db.get_prompt_by_hash(count[1])
            if row:
                db.record_prompt_use(row["id"])
            return bool(row)
        pair_id = find_pair_id_by_hashes(db, count[1], count[2])
        if pair_id:
            db.record_prompt_pair_use(pair_id)
        return bool(pair_id)

    return bool(survive_switch(db, write))


class CachedRuns:
    """Receipts of executed nodes and the one job currently executing."""

    def __init__(
        self,
        *,
        max_keys: int = MAX_KEYS,
        max_receipts: int = MAX_RECEIPTS,
        max_items: int = MAX_ITEMS,
        context: Callable[[], Tuple[Optional[str], Optional[int]]] = _host_context,
    ) -> None:
        self._max_keys = max_keys
        self._max_receipts = max_receipts
        self._max_items = max_items
        self._context = context
        self._receipts: "OrderedDict[str, List[_Receipt]]" = OrderedDict()
        self._attempt: Optional[_Attempt] = None
        self._lock = threading.Lock()

    # -- the job ------------------------------------------------------------

    def begin_attempt(self, result: Any) -> None:
        """A job was dequeued: ``result`` is ``PromptQueue.get``'s return value."""
        attempt = None
        try:
            item, item_id = result[0], result[1]
            prompt_id, graph, outputs = item[1], item[2], item[4]
            if type(item_id) is int and isinstance(prompt_id, str):
                if isinstance(graph, dict):
                    attempt = _Attempt(
                        item_id, prompt_id, graph, _job_nodes(graph, outputs), set()
                    )
        except Exception as error:
            logger.debug(f"Dequeued job not tracked for cached runs: {error}")
        with self._lock:
            self._attempt = attempt

    def end_attempt(self, item_id: Any) -> None:
        """The job finished: its receipts are complete and replace equal older ones.

        Deduplicating only complete receipts keeps a list execution that is
        still recording ([A] so far of [A, B]) from dropping an older [A].
        """
        with self._lock:
            if self._attempt is not None and self._attempt.item_id == item_id:
                self._attempt = None
            for run_key, receipts in self._receipts.items():
                if receipts and receipts[0].item_id == item_id:
                    self._receipts[run_key] = _deduplicated(receipts)

    def _current(self) -> Tuple[Optional[_Attempt], Optional[int]]:
        """The attempt the executing node belongs to, and its list index."""
        prompt_id, index = self._context()
        with self._lock:
            attempt = self._attempt
        if attempt is None or prompt_id is None or prompt_id != attempt.prompt_id:
            return None, None
        return attempt, index

    # -- node execution -----------------------------------------------------

    def replayed(self, node_id: Any) -> bool:
        """Whether this job already counted the executing node by replay."""
        attempt, _ = self._current()
        if attempt is None:
            return False
        with self._lock:
            return str(node_id) in attempt.replayed

    def record_item(self, node_id: Any, outputs: Tuple[Any, ...], facts: Facts) -> None:
        """Remember what this execution returned and was entitled to count."""
        try:
            attempt, index = self._current()
            node = attempt.nodes.get(str(node_id)) if attempt else None
            if node is None:
                return
            index = 0 if index is None else index
            if not 0 <= index < self._max_items:
                return
            if any(not isinstance(value, str) for value in outputs):
                return
            item = _Item(tuple(text_digest(value) for value in outputs), facts)
            self._store(node[1], attempt.item_id, index, item)
        except Exception as error:
            logger.debug(f"Could not record a cached-run receipt: {error}")

    def _store(self, run_key: str, item_id: int, index: int, item: _Item) -> None:
        with self._lock:
            receipts = self._receipts.pop(run_key, [])
            if receipts and receipts[0].item_id == item_id:
                receipt = receipts[0]
            else:
                # The previous job's receipt is complete (its task_done may
                # never have fired): it replaces older receipts it equals.
                receipts = _deduplicated(receipts)
                receipt = _Receipt(item_id)
                receipts.insert(0, receipt)
            receipt.items[index] = item
            self._receipts[run_key] = receipts[: self._max_receipts]
            while len(self._receipts) > self._max_keys:
                self._receipts.popitem(last=False)

    # -- cached nodes -------------------------------------------------------

    def replay(
        self,
        executor: Any,
        prompt_id: Any,
        nodes: Any,
        db_factory: Callable[[], Any],
        reader: Optional[Callable[[Any, str, str], Any]] = None,
    ) -> None:
        """Count the job's cached nodes whose cached text a receipt proves."""
        if reader is None:
            reader = _host().cached_string_outputs
        with self._lock:
            attempt = self._attempt
        if attempt is None or prompt_id != attempt.prompt_id:
            return
        if type(nodes) not in (list, tuple) or len(nodes) > MAX_CACHED_NODES:
            return
        db = None
        for node_id in nodes:
            node = attempt.nodes.get(node_id) if type(node_id) is str else None
            if node is None:
                continue
            node_type, run_key = node
            items = self._match(run_key, reader(executor, node_id, node_type))
            if items is None:
                logger.debug(
                    f"Cached node {node_id} not counted: no matching execution"
                )
                continue
            with self._lock:
                if node_id in attempt.replayed:
                    continue  # a repeated message or id never counts twice
                attempt.replayed.add(node_id)
            counts: List[Count] = []
            role = graph_role(attempt.graph, node_id, node_type)
            for item in items:
                seen = graph_seen(attempt.graph, node_id, node_type, item.facts)
                counts.extend(own_counts(node_type, item.facts, role, seen))
            if not counts:
                continue
            try:
                db = db if db is not None else db_factory()
                for count in counts:
                    if not write_count(db, count):
                        logger.info(
                            f"Cached node {node_id}: its {count[0]} no longer exists, "
                            "not counted"
                        )
            except Exception as error:
                logger.warning(f"Could not count cached node {node_id}: {error}")

    def _match(self, run_key: str, values: Any) -> Optional[List[_Item]]:
        """The items of the one receipt whose outputs equal ``values``, in order."""
        if not values:
            return None
        length = len(values[0])
        if length == 0 or length > self._max_items:
            return None
        if any(len(slot) != length for slot in values):
            return None
        wanted = {
            index: tuple(text_digest(slot[index]) for slot in values)
            for index in range(length)
        }
        with self._lock:
            receipts = self._receipts.get(run_key)
            if not receipts:
                return None
            matches = [receipt for receipt in receipts if receipt.outputs() == wanted]
            if not matches:
                return None
            self._receipts.move_to_end(run_key)
            facts = {
                tuple(receipt.items[index].facts for index in range(length))
                for receipt in matches
            }
            if len(facts) != 1:
                return None
            return [matches[0].items[index] for index in range(length)]


def _deduplicated(receipts: List[_Receipt]) -> List[_Receipt]:
    """The newest receipt replaces older ones with the same outputs."""
    if not receipts:
        return receipts
    outputs = receipts[0].outputs()
    return [receipts[0]] + [r for r in receipts[1:] if r.outputs() != outputs]


def _job_nodes(graph: Dict[str, Any], outputs: Any) -> Dict[str, Tuple[str, str]]:
    """PromptManager-family nodes the job's outputs need, with their run keys."""
    starts = (
        [str(node) for node in outputs] if isinstance(outputs, (list, tuple)) else []
    )
    needed = upstream_nodes(graph, starts)
    nodes = {}
    for node_id in needed:
        node = graph.get(node_id)
        node_type = node.get("class_type") if isinstance(node, dict) else None
        if node_type not in PROMPT_MANAGER_TYPES:
            continue
        run_key = prompt_node_key(graph, node_id)
        if run_key is not None:
            nodes[node_id] = (node_type, run_key)
    return nodes


def _host() -> Any:
    """The host adapter module (imported late: it needs ComfyUI's modules)."""
    try:
        from . import execution_context_host
    except ImportError:
        from utils import execution_context_host
    return execution_context_host


# Shared between the nodes and the host observers
CACHED_RUNS = CachedRuns()

_registered = threading.Event()
_register_lock = threading.Lock()


def register_cached_runs(
    server: Any, db_factory: Callable[[], Any], runs: Optional[CachedRuns] = None
) -> bool:
    """Observe dequeue, ``execution_cached`` and ``task_done``; once per process.

    Registers nothing when the host lacks any of them: counting is then as
    before #347.
    """
    runs = CACHED_RUNS if runs is None else runs
    with _register_lock:
        if _registered.is_set():
            return False
        queue = getattr(server, "prompt_queue", None)
        if not callable(getattr(queue, "get", None)) or not callable(
            getattr(queue, "task_done", None)
        ):
            logger.info("Cached runs not counted: the prompt queue is not observable")
            return False
        try:
            from execution import PromptExecutor
        except Exception:
            logger.info("Cached runs not counted: no ComfyUI executor")
            return False
        if not callable(getattr(PromptExecutor, "add_message", None)):
            logger.info("Cached runs not counted: executor messages unavailable")
            return False
        subscribe_executor_messages = _host().subscribe_executor_messages

        def on_message(executor: Any, args: tuple, kwargs: dict) -> None:
            try:
                event = args[0] if args else kwargs.get("event")
                if event != "execution_cached":
                    return
                server_of = getattr(executor, "server", None)
                if getattr(server_of, "prompt_queue", None) is not queue:
                    return
                data = args[1] if len(args) > 1 else kwargs.get("data")
                if type(data) is not dict:
                    return
                runs.replay(
                    executor, data.get("prompt_id"), data.get("nodes"), db_factory
                )
            except Exception as error:
                logger.warning(f"Could not count cached nodes: {error}")

        unsubscribers = []
        try:
            unsubscribers.append(
                subscribe_dequeue(queue, "cached_runs", runs.begin_attempt)
            )
            unsubscribers.append(
                subscribe_task_done(
                    queue,
                    "cached_runs",
                    lambda args, kwargs: runs.end_attempt(
                        args[0] if args else kwargs.get("item_id")
                    ),
                )
            )
            unsubscribers.append(subscribe_executor_messages("cached_runs", on_message))
        except Exception:
            for unsubscribe in unsubscribers:
                unsubscribe()
            raise
        _registered.set()
        logger.info("Counting cached runs of runtime-linked prompt text")
        return True
