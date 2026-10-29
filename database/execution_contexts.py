"""Immutable execution capture and exact, epoch-fenced library navigation hints."""

from contextlib import ExitStack
import json
import sqlite3
from typing import Callable, ContextManager, Iterator

try:
    from ..utils.execution_history_events import RunFinish, encode_command
    from ..utils.hashing import stored_prompt_text, stored_prompt_text_sql
    from ..utils.execution_contexts import (
        CAPTURE_REASONS,
        ContextBundle,
        content_digest,
        validate_bundle,
    )
except ImportError:
    from utils.execution_history_events import RunFinish, encode_command
    from utils.hashing import stored_prompt_text, stored_prompt_text_sql
    from utils.execution_contexts import (
        CAPTURE_REASONS,
        ContextBundle,
        content_digest,
        validate_bundle,
    )


FIELDS = ("raw", "effective", "prepend", "append")
ROLES = ("positive", "negative")


def _prepare(bundle: ContextBundle) -> list[dict]:
    validate_bundle(bundle)
    if bundle.reason not in CAPTURE_REASONS:
        raise ValueError("Invalid capture reason")
    prepared = []
    complete = bool(bundle.contexts)
    for context in bundle.contexts:
        evidence = {"format_version": 1, "role_provenance": context.role_provenance}
        snapshots = []
        for role in ROLES:
            snapshot = getattr(context, role)
            if snapshot is None:
                snapshots.append(None)
                evidence[role] = None
                complete = False
                continue
            values = tuple(getattr(snapshot, field).value for field in FIELDS)
            complete = complete and all(value is not None for value in values)
            snapshots.append((content_digest("snapshot", values), values))
            evidence[role] = {
                field: {
                    "provenance": getattr(snapshot, field).provenance,
                    "source": getattr(snapshot, field).source,
                    "reason": getattr(snapshot, field).reason,
                }
                for field in FIELDS
            }
        if all(snapshot is None for snapshot in snapshots):
            raise ValueError("Context must contain a snapshot")
        context_id = content_digest(
            "context", tuple(s[0] if s else None for s in snapshots)
        )
        encoded = json.dumps(
            evidence,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(encoded.encode("utf-8")) > 8192:
            raise ValueError("Oversized context evidence")
        prepared.append(
            {
                "anchor_id": context.anchor_id,
                "context_id": context_id,
                "snapshots": snapshots,
                "evidence_json": encoded,
            }
        )
    if bundle.state == "complete" and not complete:
        raise ValueError("Incomplete scalar capture")
    return sorted(prepared, key=lambda item: item["anchor_id"])


def _existing_row(
    conn: sqlite3.Connection, table: str, columns: tuple[str, ...], values: tuple
) -> bool:
    row = conn.execute(
        f"SELECT {','.join(columns[1:])} FROM {table} WHERE {columns[0]}=?",
        (values[0],),
    ).fetchone()
    if row is None:
        return False
    if tuple(row) != values[1:]:
        raise ValueError("Execution context digest collision")
    return True


def _content_rows(item: dict) -> Iterator[tuple[str, tuple[str, ...], tuple]]:
    snapshot_ids = []
    for snapshot in item["snapshots"]:
        if snapshot is None:
            snapshot_ids.append(None)
            continue
        snapshot_id, values = snapshot
        text_ids = []
        for value in values:
            text_id = None if value is None else content_digest("text", (value,))
            if value is not None:
                yield "execution_context_texts", ("text_id", "text_value"), (
                    text_id,
                    value,
                )
            text_ids.append(text_id)
        yield "execution_prompt_snapshots", (
            "snapshot_id",
            *(f"{field}_text_id" for field in FIELDS),
        ), (snapshot_id, *text_ids)
        snapshot_ids.append(snapshot_id)
    yield "execution_contexts", (
        "context_id",
        "positive_snapshot_id",
        "negative_snapshot_id",
    ), (item["context_id"], *snapshot_ids)


def _insert_exact(
    conn: sqlite3.Connection, table: str, columns: tuple[str, ...], values: tuple
) -> None:
    if not _existing_row(conn, table, columns, values):
        names = ",".join(columns)
        placeholders = ",".join("?" for _ in columns)
        conn.execute(f"INSERT INTO {table} ({names}) VALUES ({placeholders})", values)


def _role_hints(role: str) -> str:
    return (
        f"SELECT r.{role}_prompt_id, tx.text_value, "
        f"{stored_prompt_text_sql('tx.text_value')} "
        "FROM execution_run_contexts r "
        "JOIN execution_contexts c ON c.context_id = r.context_id "
        f"JOIN execution_prompt_snapshots s ON s.snapshot_id = c.{role}_snapshot_id "
        "JOIN execution_context_texts tx ON tx.text_id = s.raw_text_id "
        f"WHERE r.{role}_prompt_id IS NOT NULL AND r.reference_epoch = ("
        "SELECT storage_epoch FROM execution_history_metadata WHERE singleton_id = 1)"
    )


# Set form of the read-time prompt-hint rule in get_execution_contexts():
# a stored role hint is live only in the current storage epoch, and only when
# exactly one prompt has its raw snapshot text or that text's stored form
# (outer whitespace stripped, #438), and that is the hinted one (_prompt_hint).
# Evaluated once per statement, never per prompt row.
# Defines the CTE ``live_hint_prompts(prompt_id)``; prefix it with WITH.
LIVE_HINT_CTES = (
    "stored_hints(prompt_id, raw, stored) AS ("
    + _role_hints("positive")
    + " UNION "
    + _role_hints("negative")
    + "), hint_matches(prompt_id, raw, stored, match_id) AS ("
    "SELECT h.prompt_id, h.raw, h.stored, p.id FROM stored_hints h "
    "JOIN prompts p ON p.text COLLATE BINARY IN (h.raw, h.stored)"
    "), live_hint_prompts(prompt_id) AS ("
    "SELECT prompt_id FROM hint_matches GROUP BY prompt_id, raw, stored "
    "HAVING COUNT(*) = 1 AND MIN(match_id) = prompt_id)"
)
LIVE_HINT_PROMPT_IDS_SQL = (
    "WITH " + LIVE_HINT_CTES + " SELECT DISTINCT prompt_id FROM live_hint_prompts"
)


def _prompt_hint(conn: sqlite3.Connection, raw: str | None) -> int | None:
    """The one prompt whose text is ``raw`` or the stored form of it (#438).

    The Library stores ``text.strip()``; the exact form keeps hints of older
    rows whose stored text kept outer whitespace.
    """
    if raw is None:
        return None
    rows = conn.execute(
        "SELECT id FROM prompts WHERE text COLLATE BINARY IN (?,?) LIMIT 2",
        (raw, stored_prompt_text(raw)),
    ).fetchall()
    return rows[0][0] if len(rows) == 1 else None


def _hints(
    conn: sqlite3.Connection, positive: str | None, negative: str | None
) -> tuple:
    pos_id, neg_id = _prompt_hint(conn, positive), _prompt_hint(conn, negative)
    stored = None if negative is None else stored_prompt_text(negative)
    pair_id = None
    negative_ambiguous = (
        stored is not None
        and neg_id is None
        and conn.execute(
            "SELECT 1 FROM prompts WHERE text COLLATE BINARY IN (?,?) LIMIT 1",
            (negative, stored),
        ).fetchone()
        is not None
    )
    if pos_id is not None and stored is not None and not negative_ambiguous:
        rows = conn.execute(
            "SELECT pp.id FROM prompt_pairs pp LEFT JOIN prompts n ON "
            "n.id=pp.negative_prompt_id "
            "WHERE pp.positive_prompt_id=? AND ((pp.negative_prompt_id IS NULL AND "
            "?='') "
            "OR (pp.negative_prompt_id=? AND n.text COLLATE BINARY IN (?,?))) "
            "LIMIT 2",
            (pos_id, stored, neg_id, negative, stored),
        ).fetchall()
        if len(rows) == 1:
            pair_id = rows[0][0]
    return pos_id, neg_id, pair_id


def _values(conn: sqlite3.Connection, snapshot_id: str | None) -> dict | None:
    if snapshot_id is None:
        return None
    row = conn.execute(
        "SELECT r.text_value,e.text_value,p.text_value,a.text_value "
        "FROM execution_prompt_snapshots s "
        "LEFT JOIN execution_context_texts r ON r.text_id=s.raw_text_id "
        "LEFT JOIN execution_context_texts e ON e.text_id=s.effective_text_id "
        "LEFT JOIN execution_context_texts p ON p.text_id=s.prepend_text_id "
        "LEFT JOIN execution_context_texts a ON a.text_id=s.append_text_id "
        "WHERE s.snapshot_id=?",
        (snapshot_id,),
    ).fetchone()
    if row is None:
        raise sqlite3.DatabaseError("Missing execution context snapshot")
    return dict(zip(FIELDS, row))


def _write_capture(
    conn: sqlite3.Connection,
    run_id: str,
    expected_epoch: str,
    bundle: ContextBundle,
    prepared: list[dict],
) -> bool:
    capture = conn.execute(
        "SELECT state,reason,format_version FROM execution_context_capture "
        "WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if capture is not None:
        for item in prepared:
            for table, columns, values in _content_rows(item):
                _existing_row(conn, table, columns, values)
        links = conn.execute(
            "SELECT anchor_id,context_id,evidence_json FROM "
            "execution_run_contexts WHERE run_id=? ORDER BY anchor_id",
            (run_id,),
        ).fetchall()
        expected = [
            (item["anchor_id"], item["context_id"], item["evidence_json"])
            for item in prepared
        ]
        # Compare JSON objects so serialization order never creates a conflict.
        original = [(row[0], row[1], json.loads(row[2])) for row in links]
        proposed = [
            (anchor, context, json.loads(evidence))
            for anchor, context, evidence in expected
        ]
        if capture[0] != "conflicting" and (
            tuple(capture) != (bundle.state, bundle.reason, bundle.format_version)
            or original != proposed
        ):
            conn.execute(
                "UPDATE execution_context_capture SET "
                "state='conflicting',reason='conflicting' WHERE run_id=?",
                (run_id,),
            )
        return True
    for item in prepared:
        for table, columns, values in _content_rows(item):
            _insert_exact(conn, table, columns, values)
        raw = [snapshot[1][0] if snapshot else None for snapshot in item["snapshots"]]
        positive_id, negative_id, pair_id = _hints(conn, *raw)
        conn.execute(
            "INSERT INTO "
            "execution_run_contexts(run_id,anchor_id,context_id,evidence_json,"
            "positive_prompt_id,negative_prompt_id,pair_id,reference_epoch,"
            "capture_version) VALUES(?,?,?,?,?,?,?,?,1)",
            (
                run_id,
                item["anchor_id"],
                item["context_id"],
                item["evidence_json"],
                positive_id,
                negative_id,
                pair_id,
                expected_epoch,
            ),
        )
    conn.execute(
        "INSERT INTO "
        "execution_context_capture(run_id,state,reason,format_version) "
        "VALUES(?,?,?,?)",
        (run_id, bundle.state, bundle.reason, bundle.format_version),
    )
    return True


class ExecutionContextOperations:
    """Compose with ExecutionHistoryOperations on PromptDatabase."""

    def write_execution_contexts(
        self, run_id: str, expected_epoch: str, bundle: ContextBundle
    ) -> bool:
        prepared = _prepare(bundle)
        with self._execution_transaction(expected_epoch) as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM execution_runs WHERE run_id=? AND storage_epoch=?",
                    (run_id, expected_epoch),
                ).fetchone()
                is None
            ):
                return False
            return _write_capture(conn, run_id, expected_epoch, bundle, prepared)

    def write_execution_contexts_for_finish(
        self,
        command: RunFinish,
        bundle: ContextBundle,
        *,
        generation_guard: Callable[[], ContextManager[bool]] | None = None,
    ) -> bool:
        encode_command(command)
        if type(command) is not RunFinish:
            raise ValueError("Expected execution finish command")
        prepared = _prepare(bundle)
        # Enter the guard after BEGIN, and keep it until COMMIT/ROLLBACK exits.
        with ExitStack() as guards:
            with self._execution_transaction(command.storage_epoch) as conn:
                if generation_guard is not None and not guards.enter_context(
                    generation_guard()
                ):
                    return False
                row = conn.execute(
                    "SELECT run_id FROM execution_runs WHERE session_id=? "
                    "AND queue_item_id=? AND storage_epoch=?",
                    (command.session_id, command.queue_item_id, command.storage_epoch),
                ).fetchone()
                if row is None:
                    return False
                return _write_capture(
                    conn, row[0], command.storage_epoch, bundle, prepared
                )

    def get_execution_contexts(self, run_id: str) -> dict:
        """Return first-capture scalars; hints are None unless currently valid.

        reference_epoch remains historical evidence even when hints are invalidated.
        Reads never rebind a first-capture reference or alter stored capture data.
        """
        conn = self.model.get_connection()
        own_transaction = not conn.in_transaction
        if own_transaction:
            conn.execute("BEGIN")
        try:
            capture = conn.execute(
                "SELECT state,reason,format_version FROM execution_context_capture "
                "WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if capture is None:
                return {
                    "state": "not_captured",
                    "reason": "missing",
                    "format_version": None,
                    "contexts": [],
                }
            epoch = self.get_execution_epoch()
            contexts = []
            rows = conn.execute(
                "SELECT r.*,c.positive_snapshot_id,c.negative_snapshot_id FROM "
                "execution_run_contexts r JOIN execution_contexts c ON "
                "c.context_id=r.context_id WHERE r.run_id=? ORDER BY r.anchor_id",
                (run_id,),
            )
            for row in rows:
                positive = _values(conn, row["positive_snapshot_id"])
                negative = _values(conn, row["negative_snapshot_id"])
                hints = (None, None, None)
                if row["reference_epoch"] == epoch:
                    current = _hints(
                        conn,
                        positive["raw"] if positive else None,
                        negative["raw"] if negative else None,
                    )
                    hints = tuple(
                        row[key] if row[key] == value else None
                        for key, value in zip(
                            ("positive_prompt_id", "negative_prompt_id", "pair_id"),
                            current,
                        )
                    )
                contexts.append(
                    {
                        "anchor_id": row["anchor_id"],
                        "context_id": row["context_id"],
                        "positive": positive,
                        "negative": negative,
                        "evidence": json.loads(row["evidence_json"]),
                        "positive_prompt_id": hints[0],
                        "negative_prompt_id": hints[1],
                        "pair_id": hints[2],
                        "reference_epoch": row["reference_epoch"],
                    }
                )
            return {
                "state": capture[0],
                "reason": capture[1],
                "format_version": capture[2],
                "contexts": contexts,
            }
        finally:
            if own_transaction:
                conn.rollback()
