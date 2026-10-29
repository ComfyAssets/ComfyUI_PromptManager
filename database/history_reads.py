"""Snapshot-owned, bounded projections of immutable execution evidence."""

import codecs
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
from typing import Iterator

from .execution_contexts import _hints, _values, content_digest
from .execution_history import HistoryEpochChanged
from .execution_outputs import _capture, _REFERENCE_COLUMNS
from .library import _ELIGIBLE

try:
    from ..utils.hashing import prompt_key, stored_prompt_text
except ImportError:
    from utils.hashing import prompt_key, stored_prompt_text

RunKey = tuple[int, str, int, str]


class HistoryCaptureChanged(ValueError):
    """A continuation no longer describes the stored output capture header."""


@dataclass(frozen=True)
class WindowPosition:
    raw_text_id: str
    upper: RunKey
    before: RunKey


@dataclass(frozen=True)
class OutputPosition:
    capture_fingerprint: str
    after: tuple[str, int]


_DIGEST = re.compile(r"[0-9a-f]{64}")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def _valid_digest(value: object) -> bool:
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def _valid_run_key(value: object) -> bool:
    return (
        type(value) is tuple
        and len(value) == 4
        and all(type(value[i]) is int and 0 <= value[i] <= 2**63 - 1 for i in (0, 2))
        and all(
            type(value[i]) is str and _UUID.fullmatch(value[i]) is not None
            for i in (1, 3)
        )
    )


def _valid_coordinate(value: object) -> bool:
    if type(value) is not tuple or len(value) != 2:
        return False
    node, ordinal = value
    if not (
        type(node) is str
        and 1 <= len(node) <= 128
        and "\0" not in node
        and type(ordinal) is int
        and 0 <= ordinal <= 255
    ):
        return False
    try:
        return len(node.encode("utf-8")) <= 512
    except UnicodeError:
        return False


def _validate_window_position(position: WindowPosition | None) -> None:
    if position is not None and not (
        type(position) is WindowPosition
        and _valid_digest(position.raw_text_id)
        and _valid_run_key(position.upper)
        and _valid_run_key(position.before)
    ):
        raise ValueError("Invalid history window position")


def _validate_output_position(position: OutputPosition | None) -> None:
    if position is not None and not (
        type(position) is OutputPosition
        and _valid_digest(position.capture_fingerprint)
        and _valid_coordinate(position.after)
    ):
        raise ValueError("Invalid history output position")


_snapshots: ContextVar[tuple] = ContextVar("history_read_snapshots", default=())
_KEY = "(r.observed_dequeue_at,r.session_id,r.queue_item_id,r.run_id)"
_ORDER = (
    "r.observed_dequeue_at DESC,r.session_id COLLATE BINARY DESC,"
    "r.queue_item_id DESC,r.run_id COLLATE BINARY DESC"
)
# Stale-hint rows a newest-pair lookup inspects before it reports none.
_PAIR_RUN_CANDIDATES = 16
# The recorded texts of one scope, as a JSON array parameter (_scope_text_ids).
_IN_SCOPE = "IN (SELECT value FROM json_each(?))"
_MATCHING = (
    "SELECT DISTINCT rc.run_id FROM execution_prompt_snapshots s "
    "JOIN execution_contexts c ON c.positive_snapshot_id=s.snapshot_id "
    "JOIN execution_run_contexts rc ON rc.context_id=c.context_id "
    f"WHERE s.raw_text_id {_IN_SCOPE}"
)


def _scope_text_ids(conn: sqlite3.Connection, key: str) -> str:
    """Every recorded raw text whose prompt key is ``key``, as a JSON array.

    A run keeps its exact raw text, while the Library saves and counts it as
    the prompt with its key (outer whitespace #438, case #460). The values are
    compared, never only their digests; ``pm_prompt_key`` is Python's key.
    """
    return json.dumps(
        [
            row[0]
            for row in conn.execute(
                # Only snapshot raw texts: effective and affix texts are skipped
                "SELECT t.text_id FROM (SELECT DISTINCT raw_text_id AS text_id "
                "FROM execution_prompt_snapshots) s CROSS JOIN "
                "execution_context_texts t WHERE t.text_id=s.text_id AND "
                "pm_prompt_key(t.text_value)=? COLLATE BINARY",
                (key,),
            )
        ]
    )


def _scope_text_id(raw: str | None) -> str | None:
    """The scope digest of a raw text: that of its prompt key."""
    return None if raw is None else content_digest("text", (prompt_key(raw),))


def _limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("History limit must be an integer from 1 to 100")


def _key(row: dict) -> RunKey:
    return tuple(
        row[name]
        for name in ("observed_dequeue_at", "session_id", "queue_item_id", "run_id")
    )


def _context_capture(conn: sqlite3.Connection, run_id: str) -> dict:
    row = conn.execute(
        "SELECT state,reason,format_version FROM execution_context_capture "
        "WHERE run_id=?",
        (run_id,),
    ).fetchone()
    return (
        dict(row)
        if row is not None
        else {"state": "not_captured", "reason": "missing", "format_version": None}
    )


def _output_header(conn: sqlite3.Connection, run_id: str) -> tuple[dict, int | None]:
    row = _capture(conn, run_id)
    if row is None:
        return (
            dict(state="not_captured", reason=None, format_version=None, source=None),
            None,
        )
    count = conn.execute(
        "SELECT COUNT(*) FROM execution_run_outputs WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    return dict(row), count


def _list_status(capture: dict) -> str:
    if capture["state"] == "not_captured":
        return "not_captured"
    return "complete" if capture["state"] == "complete" else "incomplete"


def _output_summary(conn: sqlite3.Connection, run_id: str) -> dict:
    capture, count = _output_header(conn, run_id)
    return dict(
        capture, retained_occurrence_count=count, list_status=_list_status(capture)
    )


def _fingerprint(capture: dict, count: int | None) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                1,
                *(capture[k] for k in ("state", "reason", "format_version", "source")),
                count,
            ],
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _receipt(run_id: str, row: sqlite3.Row) -> dict:
    return {
        "occurrence": {
            "run_id": run_id,
            "output_node_id": row["output_node_id"],
            "image_ordinal": row["image_ordinal"],
        },
        "reference": {
            key: row[key]
            for key in (
                "folder_type",
                "subfolder",
                "filename",
                "path_style",
                "root_key",
            )
        },
        "file_identity": "unverified",
        "newly_generated": "unknown",
        "context_membership": "unknown",
        "transient": row["folder_type"] == "temp",
    }


class HistoryReadOperations:
    """Read evidence only inside an explicitly owned, epoch-first snapshot."""

    @contextmanager
    def recorded_history_snapshot(
        self, expected_epoch: str | None = None
    ) -> Iterator[str]:
        conn = self.model.get_connection()
        if conn.in_transaction:
            raise sqlite3.ProgrammingError(
                "History reads require their own transaction"
            )
        conn.execute("BEGIN")
        token = _snapshots.set((*_snapshots.get(), conn))
        try:
            epoch = self.get_execution_epoch()
            if expected_epoch is not None and epoch != expected_epoch:
                raise HistoryEpochChanged(epoch)
            yield epoch
        finally:
            try:
                conn.rollback()
            finally:
                _snapshots.reset(token)

    def _history_connection(self) -> sqlite3.Connection:
        conn = self.model.get_connection()
        if not conn.in_transaction or conn not in _snapshots.get():
            raise sqlite3.ProgrammingError(
                "History read requires recorded_history_snapshot"
            )
        return conn

    def list_history_window(
        self,
        positive_id: int,
        *,
        limit: int = 40,
        after: WindowPosition | None = None,
        restart: bool = False,
    ) -> dict | None:
        conn = self._history_connection()
        _limit(limit)
        _validate_window_position(after)
        if type(restart) is not bool or (
            restart and (after is None or after.before != after.upper)
        ):
            raise ValueError("Invalid history restart position")
        saved = conn.execute(
            "SELECT CASE WHEN typeof(p.text)='text' AND "
            "length(CAST(p.text AS BLOB))<=65536 THEN p.text ELSE NULL END AS text "
            f"FROM prompts p WHERE p.id=? AND ({_ELIGIBLE})",
            (positive_id,),
        ).fetchone()
        if saved is None:
            return None
        text = saved["text"]
        raw_id = _scope_text_id(text)
        # A position from before #460 names the scope by the stripped text
        # alone; it is the same scope, so a page open across the upgrade resumes.
        legacy_id = (
            None
            if text is None
            else content_digest("text", (stored_prompt_text(text),))
        )
        if after is not None and after.raw_text_id not in (raw_id, legacy_id):
            raise ValueError("History position text scope changed")
        group = {
            "positive_id": positive_id,
            "raw_text_id": raw_id,
            "relation": "exact_frozen_raw_text",
            "scope_status": "uncapturable_text" if text is None else "no_matches",
        }
        result = dict(
            group=group,
            recorded_run_count=0,
            items=[],
            context_summaries=[],
            has_more=False,
            next_position=None,
            resume_position=None,
        )
        if raw_id is None:
            return result
        scope = _scope_text_ids(conn, prompt_key(text))
        where = f"r.run_id IN ({_MATCHING})"
        params = [scope]
        if after is not None:
            where += f" AND {_KEY}<=(?,?,?,?)"
            params.extend(after.upper)
        count = conn.execute(
            f"SELECT COUNT(*) FROM execution_runs r WHERE {where}", params
        ).fetchone()[0]
        result["recorded_run_count"] = count
        group["scope_status"] = "matched" if count else "no_matches"
        if after is not None and not restart:
            where += f" AND {_KEY}<(?,?,?,?)"
            params.extend(after.before)
        rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT r.* FROM execution_runs r WHERE {where} "
                f"ORDER BY {_ORDER} LIMIT ?",
                (*params, limit + 1),
            )
        ]
        more = len(rows) > limit
        rows = rows[:limit]
        if not rows:
            return result
        upper = after.upper if after is not None else _key(rows[0])
        result["resume_position"] = WindowPosition(raw_id, upper, upper)
        if more:
            result["next_position"] = WindowPosition(raw_id, upper, _key(rows[-1]))
        result["has_more"] = more
        rows.reverse()
        by_run = {row["run_id"]: row for row in rows}
        for row in rows:
            row["diagnostics_available"] = any(
                row[field] is not None
                for field in ("node_id", "node_type", "exception_type")
            )
            for field in ("node_id", "node_type", "exception_type"):
                del row[field]
            row["diagnostics_omitted"] = bool(row["diagnostics_omitted"])
            row["context_capture"] = _context_capture(conn, row["run_id"])
            row["output_summary"] = _output_summary(conn, row["run_id"])
            row["matching_anchors"] = []
        placeholders = ",".join("?" for _ in rows)
        anchors = conn.execute(
            "SELECT rc.run_id,rc.anchor_id,rc.context_id,rc.evidence_json,"
            "c.positive_snapshot_id,c.negative_snapshot_id,"
            "substr(CAST(t.text_value AS BLOB),1,512) AS prefix,"
            "length(CAST(t.text_value AS BLOB)) AS byte_length "
            "FROM execution_run_contexts rc JOIN execution_contexts c "
            "ON c.context_id=rc.context_id JOIN execution_prompt_snapshots p "
            "ON p.snapshot_id=c.positive_snapshot_id "
            "LEFT JOIN execution_prompt_snapshots n "
            "ON n.snapshot_id=c.negative_snapshot_id "
            "LEFT JOIN execution_context_texts t ON t.text_id=n.raw_text_id "
            f"WHERE rc.run_id IN ({placeholders}) AND p.raw_text_id {_IN_SCOPE} "
            "ORDER BY rc.anchor_id COLLATE BINARY,rc.run_id COLLATE BINARY",
            (*by_run, scope),
        )
        summaries = {}
        evidence_by_context = {}
        for anchor in anchors:
            reference = {
                key: anchor[key]
                for key in (
                    "anchor_id",
                    "context_id",
                    "positive_snapshot_id",
                    "negative_snapshot_id",
                )
            }
            by_run[anchor["run_id"]]["matching_anchors"].append(reference)
            context_id = anchor["context_id"]
            if context_id not in summaries:
                prefix = b"" if anchor["byte_length"] == 0 else anchor["prefix"]
                preview = (
                    None
                    if prefix is None
                    else codecs.getincrementaldecoder("utf-8")().decode(
                        prefix, final=False
                    )[:100]
                )
                evidence_by_context[context_id] = []
                summaries[context_id] = {
                    key: anchor[key]
                    for key in (
                        "context_id",
                        "positive_snapshot_id",
                        "negative_snapshot_id",
                    )
                }
                summaries[context_id]["negative_raw"] = {
                    "state": (
                        "unknown"
                        if preview is None
                        else "explicit_empty" if not preview else "known"
                    ),
                    "preview": preview,
                    "truncated": (
                        False
                        if preview is None
                        else len(preview.encode("utf-8")) < anchor["byte_length"]
                    ),
                    "evidence": evidence_by_context[context_id],
                }
            negative = json.loads(anchor["evidence_json"])["negative"]
            evidence = (
                negative["raw"]
                if negative is not None
                else dict(provenance="unknown", source="unknown", reason="missing")
            )
            evidence_by_context[context_id].append(
                {
                    "run_id": anchor["run_id"],
                    "anchor_id": anchor["anchor_id"],
                    **evidence,
                }
            )
        # Context order follows its first chronological matching run, then anchor.
        ordered = {}
        for row in rows:
            for anchor in row["matching_anchors"]:
                ordered.setdefault(
                    anchor["context_id"], summaries[anchor["context_id"]]
                )
        result["items"] = rows
        result["context_summaries"] = list(ordered.values())
        return result

    def get_newest_pair_run(self, positive_id: int, pair_id: int) -> dict | None:
        """The newest matching run whose current saved-pair hint names `pair_id`.

        None when the positive is not an eligible saved prompt or the pair is
        not one of its pairs; ``{"run": None}`` when no recorded run names it.
        Only a hint captured in this epoch and still resolving to the same pair
        counts, as in ``get_history_run``.
        """
        conn = self._history_connection()
        saved = conn.execute(
            "SELECT CASE WHEN typeof(p.text)='text' AND "
            "length(CAST(p.text AS BLOB))<=65536 THEN p.text ELSE NULL END AS text "
            f"FROM prompts p WHERE p.id=? AND ({_ELIGIBLE})",
            (positive_id,),
        ).fetchone()
        if saved is None or (
            conn.execute(
                "SELECT 1 FROM prompt_pairs WHERE id=? AND positive_prompt_id=?",
                (pair_id, positive_id),
            ).fetchone()
            is None
        ):
            return None
        if saved["text"] is None:
            return {"run": None}
        epoch = self.get_execution_epoch()
        key = prompt_key(saved["text"])
        candidates = conn.execute(
            "SELECT r.run_id,rc.anchor_id,rc.context_id,"
            "c.positive_snapshot_id,c.negative_snapshot_id "
            "FROM execution_runs r JOIN execution_run_contexts rc "
            "ON rc.run_id=r.run_id JOIN execution_contexts c "
            "ON c.context_id=rc.context_id JOIN execution_prompt_snapshots s "
            "ON s.snapshot_id=c.positive_snapshot_id "
            f"WHERE s.raw_text_id {_IN_SCOPE} AND rc.pair_id=? "
            "AND rc.reference_epoch=? "
            f"ORDER BY {_ORDER},rc.anchor_id COLLATE BINARY LIMIT ?",
            (
                _scope_text_ids(conn, key),
                pair_id,
                epoch,
                _PAIR_RUN_CANDIDATES,
            ),
        ).fetchall()
        for row in candidates:
            positive = _values(conn, row["positive_snapshot_id"])
            negative = _values(conn, row["negative_snapshot_id"])
            if (
                positive is None
                or positive["raw"] is None
                or prompt_key(positive["raw"]) != key
            ):
                continue
            hints = _hints(conn, positive["raw"], negative["raw"] if negative else None)
            if hints[2] == pair_id:
                return {
                    "run": {
                        key: row[key] for key in ("run_id", "anchor_id", "context_id")
                    }
                }
        return {"run": None}

    def get_history_run(self, run_id: str) -> dict | None:
        conn = self._history_connection()
        run = self.get_execution_run(run_id)
        if run is None:
            return None
        capture = self.get_execution_contexts(run_id)
        snapshots = {
            row["anchor_id"]: row
            for row in conn.execute(
                "SELECT rc.anchor_id,c.positive_snapshot_id,c.negative_snapshot_id "
                "FROM execution_run_contexts rc JOIN execution_contexts c "
                "ON c.context_id=rc.context_id WHERE rc.run_id=?",
                (run_id,),
            )
        }
        contexts = capture.pop("contexts")
        for context in contexts:
            row = snapshots[context["anchor_id"]]
            context.update(
                {
                    key: row[key]
                    for key in ("positive_snapshot_id", "negative_snapshot_id")
                },
                # The history scope this context belongs to (#438)
                positive_raw_text_id=_scope_text_id(
                    (context["positive"] or {}).get("raw")
                ),
            )
            pair = context["pair_id"]
            context["navigation"] = {
                "positive_available": context["positive_prompt_id"] is not None
                and conn.execute(
                    f"SELECT 1 FROM prompts p WHERE p.id=? AND ({_ELIGIBLE})",
                    (context["positive_prompt_id"],),
                ).fetchone()
                is not None,
                "negative_available": context["negative_prompt_id"] is not None,
                "pair_available": pair is not None
                and conn.execute(
                    "SELECT 1 FROM prompt_pairs WHERE id=? AND archived=0", (pair,)
                ).fetchone()
                is not None,
            }
        return dict(
            run=run,
            context_capture=capture,
            contexts=contexts,
            output_summary=_output_summary(conn, run_id),
        )

    def list_history_outputs(
        self,
        run_id: str,
        *,
        limit: int = 40,
        after: OutputPosition | None = None,
    ) -> dict | None:
        conn = self._history_connection()
        _limit(limit)
        _validate_output_position(after)
        # Validate header/orphans even when the requested parent does not exist.
        capture, count = _output_header(conn, run_id)
        if self.get_execution_run(run_id) is None:
            return None
        fingerprint = _fingerprint(capture, count)
        if after is not None and after.capture_fingerprint != fingerprint:
            raise HistoryCaptureChanged("History output capture changed")
        where = "run_id=?"
        params = [run_id]
        if after is not None:
            where += " AND (output_node_id COLLATE BINARY,image_ordinal)>(?,?)"
            params.extend(after.after)
        rows = list(
            conn.execute(
                f"SELECT {_REFERENCE_COLUMNS} FROM execution_run_outputs WHERE {where} "
                "ORDER BY output_node_id COLLATE BINARY,image_ordinal LIMIT ?",
                (*params, limit + 1),
            )
        )
        more = len(rows) > limit
        rows = rows[:limit]
        position = (
            OutputPosition(
                fingerprint, (rows[-1]["output_node_id"], rows[-1]["image_ordinal"])
            )
            if more
            else None
        )
        return dict(
            capture=capture,
            retained_occurrence_count=count,
            list_status=_list_status(capture),
            capture_fingerprint=fingerprint,
            items=[_receipt(run_id, row) for row in rows],
            has_more=more,
            next_position=position,
        )

    def get_history_occurrence(
        self, run_id: str, node: str, ordinal: int
    ) -> dict | None:
        conn = self._history_connection()
        capture, _ = _output_header(conn, run_id)
        row = conn.execute(
            f"SELECT {_REFERENCE_COLUMNS} FROM execution_run_outputs "
            "WHERE run_id=? AND output_node_id=? COLLATE BINARY AND image_ordinal=?",
            (run_id, node, ordinal),
        ).fetchone()
        return (
            dict(capture=capture, receipt=_receipt(run_id, row))
            if row is not None
            else None
        )
