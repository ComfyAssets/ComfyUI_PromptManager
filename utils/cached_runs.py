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

The receipt also keeps the raw, prepend and append text the node gave the
execution history (#403), so the history capture of a cached job can record
the raw text the graph cannot show (``recorded_fields``) and the job becomes a
tracked run of its prompt, as it is a counted one.

Fail closed: when the cached text cannot be matched to a receipt recorded in
this process, nothing is counted (the behavior before #347) and the history
fields stay unknown. In memory only and bounded; starts no threads.
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
    from .execution_contexts import MAX_ATTEMPT_BYTES, MAX_FIELD_BYTES, text_size
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
    from utils.execution_contexts import MAX_ATTEMPT_BYTES, MAX_FIELD_BYTES, text_size

logger = get_logger("prompt_manager.cached_runs")

PAIR = "PromptManagerPair"
MAX_KEYS = 256  # run keys remembered (the same bound as MAX_KEYED_RUNS)
MAX_RECEIPTS = 4  # receipts per run key: a recurring upstream value (A, B, A)
MAX_ITEMS = 64  # list items per receipt (the cache reader's bound)
MAX_CACHED_NODES = 500  # the execution_cached guard the history capture uses
MAX_FIELD_TEXT_BYTES = 8 * 1024 * 1024  # history fields all receipts keep
MAX_RECEIPT_FIELD_BYTES = MAX_ATTEMPT_BYTES  # one receipt: what one run's history holds
_EMPTY_HASH = generate_prompt_hash("")

Count = Tuple[Any, ...]  # ("prompt", hash) or ("pair", positive, negative or None)
# (raw, prepend, append) per STRING slot, as given to record_runtime_context
Fields = Tuple[Tuple[str, str, str], ...]
# Executions with equal outputs recorded different fields: none can be named
_CONFLICT: Fields = ()


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
    fields: Optional[Fields] = None
    field_bytes: int = 0


class _Receipt:
    __slots__ = ("item_id", "items")

    def __init__(self, item_id: int) -> None:
        self.item_id = item_id
        self.items: Dict[int, _Item] = {}

    def outputs(self) -> Dict[int, Tuple[str, ...]]:
        return {index: item.outputs for index, item in self.items.items()}

    def field_bytes(self) -> int:
        return sum(item.field_bytes for item in self.items.values())

    def strip_fields(self) -> int:
        """Drop the history fields (counting is unchanged); the bytes freed."""
        freed = 0
        for index, item in list(self.items.items()):
            if item.field_bytes:
                freed += item.field_bytes
                self.items[index] = item._replace(fields=None, field_bytes=0)
        return freed


class _Attempt(NamedTuple):
    item_id: int
    prompt_id: str
    graph: Dict[str, Any]
    nodes: Dict[str, Tuple[str, str]]  # node id -> (class, run key)
    replayed: set


def _field_size(fields: Any, slots: int) -> Optional[int]:
    """UTF-8 size of well-formed fields for ``slots`` outputs, else None."""
    if type(fields) is not tuple or len(fields) != slots:
        return None
    size = 0
    for slot in fields:
        if type(slot) is not tuple or len(slot) != 3:
            return None
        for value in slot:
            try:
                size += text_size(value, MAX_FIELD_BYTES)  # the capture's own check
            except ValueError:
                return None
    return size


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
        max_field_bytes: int = MAX_FIELD_TEXT_BYTES,
        context: Callable[[], Tuple[Optional[str], Optional[int]]] = _host_context,
    ) -> None:
        self._max_keys = max_keys
        self._max_receipts = max_receipts
        self._max_items = max_items
        self._max_field_bytes = max_field_bytes
        self._field_bytes = 0  # UTF-8 bytes of the fields all receipts keep
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
                    before = _bytes(receipts)
                    deduplicated = _deduplicated(receipts)
                    self._field_bytes += _bytes(deduplicated) - before
                    self._receipts[run_key] = deduplicated

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

    def record_item(
        self,
        node_id: Any,
        outputs: Tuple[Any, ...],
        facts: Facts,
        fields: Optional[Fields] = None,
    ) -> None:
        """Remember what this execution returned and was entitled to count.

        ``fields`` are the texts the node gave the execution history, one
        ``(raw, prepend, append)`` per output; kept only when well formed,
        each within the history's field limit and the store within its budget.
        """
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
            size = _field_size(fields, len(outputs)) if fields is not None else None
            item = _Item(
                tuple(text_digest(value) for value in outputs),
                facts,
                fields if size is not None else None,
                size or 0,
            )
            self._store(node[1], attempt.item_id, index, item)
        except Exception as error:
            logger.debug(f"Could not record a cached-run receipt: {error}")

    def _store(self, run_key: str, item_id: int, index: int, item: _Item) -> None:
        with self._lock:
            receipts = self._take(run_key)
            if receipts and receipts[0].item_id == item_id:
                receipt = receipts[0]
            else:
                # The previous job's receipt is complete (its task_done may
                # never have fired): it replaces older receipts it equals.
                receipts = _deduplicated(receipts)
                receipt = _Receipt(item_id)
                receipts.insert(0, receipt)
            receipt.items[index] = item
            receipts = receipts[: self._max_receipts]
            if item.field_bytes and (
                receipt.field_bytes() > MAX_RECEIPT_FIELD_BYTES
                or not self._make_room(_bytes(receipts))
            ):
                # Counting is as before; this job's history fields stay unknown
                receipt.items[index] = item._replace(fields=None, field_bytes=0)
            self._put(run_key, receipts)
            while len(self._receipts) > self._max_keys:
                _, dropped = self._receipts.popitem(last=False)
                self._field_bytes -= _bytes(dropped)

    def _make_room(self, needed: int) -> bool:
        """Free the fields of the least recently used receipts (under the lock).

        Old receipts of workflows never run again must not hold the budget:
        their counting stays, only their history fields go. False when
        ``needed`` does not fit even then.
        """
        if needed > self._max_field_bytes:
            return False
        for receipts in self._receipts.values():  # least recently used first
            if self._field_bytes + needed <= self._max_field_bytes:
                break
            for receipt in receipts:
                self._field_bytes -= receipt.strip_fields()
        return self._field_bytes + needed <= self._max_field_bytes

    def _take(self, run_key: str) -> List[_Receipt]:
        """Remove a key's receipts and their bytes from the store (under the lock)."""
        receipts = self._receipts.pop(run_key, [])
        self._field_bytes -= _bytes(receipts)
        return list(receipts)

    def _put(self, run_key: str, receipts: List[_Receipt]) -> None:
        """Store a key's receipts as the most recently used (under the lock)."""
        self._receipts[run_key] = receipts
        self._field_bytes += _bytes(receipts)

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
        with self._lock:
            matches = self._matching(run_key, values)
            if not matches:
                return None
            length = len(values[0])
            facts = {
                tuple(receipt.items[index].facts for index in range(length))
                for receipt in matches
            }
            if len(facts) != 1:
                return None
            return [matches[0].items[index] for index in range(length)]

    def _matching(self, run_key: str, values: Any) -> List[_Receipt]:
        """Receipts of ``run_key`` whose outputs equal ``values`` (under the lock)."""
        if type(values) not in (list, tuple) or not values:
            return []
        if any(type(slot) not in (list, tuple) for slot in values):
            return []
        length = len(values[0])
        if length == 0 or length > self._max_items:
            return []
        if any(len(slot) != length for slot in values):
            return []
        if any(type(value) is not str for slot in values for value in slot):
            return []
        wanted = {
            index: tuple(text_digest(slot[index]) for slot in values)
            for index in range(length)
        }
        receipts = self._receipts.get(run_key)
        if not receipts:
            return []
        matches = [receipt for receipt in receipts if receipt.outputs() == wanted]
        if matches:
            self._receipts.move_to_end(run_key)
        return matches

    # -- the history capture (#403) -------------------------------------------

    def recorded_fields(
        self, prompt_id: Any, node_id: Any, values: Any
    ) -> Optional[List[Fields]]:
        """The history fields of the execution a cached node reuses, per list item.

        ``values`` are the node's cached STRING outputs (``cached_string_outputs``).
        Only for a node of the job currently executing, and only when every
        receipt matching ``values`` kept the same fields; None otherwise. Never
        counts and never marks the node replayed.
        """
        try:
            with self._lock:
                attempt = self._attempt
                if (
                    attempt is None
                    or type(prompt_id) is not str
                    or prompt_id != attempt.prompt_id
                    or type(node_id) is not str
                    or node_id not in attempt.nodes
                ):
                    return None
                matches = self._matching(attempt.nodes[node_id][1], values)
                if not matches:
                    return None
                found = []
                for index in range(len(values[0])):
                    fields = {receipt.items[index].fields for receipt in matches}
                    if len(fields) != 1 or None in fields or _CONFLICT in fields:
                        return None
                    found.append(fields.pop())
                return found
        except Exception as error:
            logger.debug(f"Could not read a cached run's history fields: {error}")
            return None


def _deduplicated(receipts: List[_Receipt]) -> List[_Receipt]:
    """The newest receipt replaces older ones with the same outputs.

    Counting needs only the outputs. When a replaced receipt recorded other
    history fields for equal outputs (a raw text the outputs do not show),
    the newest item's fields become a conflict, so no cached job of it is
    given either text (#403). A conflict stays until the run key is dropped.
    """
    if not receipts:
        return receipts
    newest, outputs = receipts[0], receipts[0].outputs()
    kept = [newest]
    for receipt in receipts[1:]:
        if receipt.outputs() != outputs:
            kept.append(receipt)
            continue
        for index, item in list(newest.items.items()):
            older = receipt.items[index].fields
            if older == _CONFLICT or (
                older is not None and item.fields is not None and older != item.fields
            ):
                newest.items[index] = item._replace(fields=_CONFLICT, field_bytes=0)
    return kept


def _bytes(receipts: List[_Receipt]) -> int:
    """UTF-8 bytes of the history fields ``receipts`` keep."""
    return sum(
        item.field_bytes for receipt in receipts for item in receipt.items.values()
    )


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
