"""The workflow storage migration and Reclaim space (#249 S6 carry-over).

``migrate_workflow_storage`` rewrites old-form image rows (workflow and
prompt graph text inline) to the new form (a ``workflow_documents`` id,
``database/workflow_documents.py``) in batches:

- each batch is one ``BEGIN IMMEDIATE`` transaction over at most
  ``BATCH_ROWS`` rows or about ``BATCH_BYTES`` of text, found by a rowid
  range search (``NEXT_IDS_SQL``) and read one row at a time, so memory is
  bounded by one row plus the batch's distinct documents;
- each text is stored (or its document reused) and read back through the
  one accessor before its row changes; a text that would not come back
  identical fails the batch (``DocumentMismatch``) and rolls it back whole;
- a value that is not text (or is over ``MAX_DOCUMENT_BYTES``) stays inline
  and is counted as protected;
- each row runs in its own savepoint: one that cannot be rewritten (text
  SQLite cannot decode, a text that would not come back) stays inline and
  is listed, and the rest of the batch goes on;
- a batch ends after ``BATCH_SECONDS``, and the write lock is then left
  free for ``BATCH_PAUSE``, so other writers are never starved.

A crash or a stop between batches leaves committed batches in the new form
and the rest in the old form; both read the same, and a rerun continues
from the first old-form row. Only database rows change: no image or video
file is opened.

``reclaim_space`` is the VACUUM afterwards: it sweeps unreferenced
documents, rebuilds the file and checkpoints the WAL with TRUNCATE, so the
freed pages go back to the disk.
"""

import logging
import os
import sqlite3
import time
from contextlib import closing

from .workflow_documents import (
    TEXT_COLUMNS,
    DocumentMismatch,
    read_stored_text,
    release_documents,
    storable,
    store_text,
    sweep_documents,
)

OPERATION = "workflow_storage"
# The first apply's backup: ``<db>.pre-workflow-storage_<stamp>``, outside the
# pruned ``<db>.backup_*`` pool. Only one is ever made; later applies take a
# pooled safety backup.
PRE_MIGRATION_LABEL = "pre-workflow-storage"
BATCH_ROWS = 200
BATCH_BYTES = 16 * 2**20  # characters of text rewritten per transaction
# A batch holds the write lock: it ends after this long, and the loop then
# leaves the lock free for BATCH_PAUSE, longer than SQLite's longest busy
# poll (100 ms), so the image monitor and the run counting get in between.
BATCH_SECONDS = 0.5
BATCH_PAUSE = 0.15


def _row_error(error) -> bool:
    """Whether ``error`` is one row's own cause, which keeps only that row inline.

    Text SQLite cannot decode for Python, and a text that would not come
    back identical. Anything else (a full disk, an I/O error, a malformed
    database, a bug) fails the whole batch. The decode error is matched by
    its message: ``sqlite_errorcode`` needs Python 3.11.
    """
    if isinstance(error, (DocumentMismatch, UnicodeError)):
        return True
    return isinstance(error, sqlite3.OperationalError) and str(error).startswith(
        "Could not decode"
    )


logger = logging.getLogger("prompt_manager.database.workflow_storage")
_clock = time.monotonic

# typeof() reads a column's type from the record header: no overflow page.
NEXT_IDS_SQL = (
    "SELECT id FROM generated_images WHERE id>? AND "
    "(typeof(workflow_data)!='null' OR typeof(prompt_metadata)!='null') "
    "ORDER BY id LIMIT ?"
)
ROW_SQL = (
    "SELECT workflow_data, prompt_metadata, workflow_document_id, "
    "prompt_document_id FROM generated_images WHERE id=?"
)
UPDATE_SQL = (
    "UPDATE generated_images SET {assignments} WHERE id=?"  # columns are constants
)
# One pass over the record headers: rows with inline text, and inline
# values that are not text (they stay inline).
INLINE_COUNTS_SQL = (
    "SELECT "
    "SUM(typeof(workflow_data)='text' OR typeof(prompt_metadata)='text'), "
    "SUM(typeof(workflow_data) NOT IN ('null','text')) "
    "+ SUM(typeof(prompt_metadata) NOT IN ('null','text')) "
    "FROM generated_images"
)
# octet_length() (SQLite 3.43+) reads no overflow page either.
INLINE_BYTES_SQL = (
    "SELECT SUM(octet_length(workflow_data)), SUM(octet_length(prompt_metadata)) "
    "FROM generated_images"
)

QUERY_PLAN_STATEMENTS = {
    "next old-form rows": (NEXT_IDS_SQL, (0, BATCH_ROWS)),
    "one row": (ROW_SQL, (1,)),
}


def _rewrite_row(conn, image_id, row, verified, checked):
    """Store the row's inline texts as documents; returns (changed, protected, size).

    ``verified`` maps document ids already read back in this batch to their
    text, and ``checked`` holds the reused ids ``store_text`` already
    checked, so a document shared by many rows is decompressed once.
    """
    changes, replaced = {}, []
    protected = size = 0
    for index, (column, reference) in enumerate(TEXT_COLUMNS):
        value, current = row[index], row[index + len(TEXT_COLUMNS)]
        if value is None:
            continue
        if not storable(value):
            protected += 1
            continue
        document_id = store_text(conn, value, checked)
        if document_id not in verified:
            verified[document_id] = read_stored_text(conn, None, document_id)
        if verified[document_id] != value:
            raise DocumentMismatch(f"Image {image_id}: {column} would change")
        changes[column], changes[reference] = None, document_id
        if current is not None and current != document_id:
            replaced.append(current)
        size += len(value)
    if changes:
        assignments = ",".join(f"{key}=?" for key in changes)
        conn.execute(
            UPDATE_SQL.format(assignments=assignments),
            [*changes.values(), image_id],
        )
    release_documents(conn, replaced)
    return bool(changes), protected, size


def migrate_batch(
    conn,
    after_id,
    max_rows=BATCH_ROWS,
    max_bytes=BATCH_BYTES,
    max_seconds=BATCH_SECONDS,
):
    """Rewrite the next batch after ``after_id`` in one transaction.

    The batch ends after ``max_rows`` rows, about ``max_bytes`` of text or
    ``max_seconds``, whichever comes first. Each row runs in its own
    savepoint: a row that cannot be rewritten (``_row_error``) is rolled
    back alone, stays inline and is listed in ``errors``; anything else rolls
    the whole batch back and propagates. Returns ``{last_id, rows,
    protected, errors, done}``: the last row looked at, rows rewritten,
    inline values kept (not text), the ids kept by an error and whether
    there was nothing left to look at.
    """
    if conn.in_transaction:
        raise RuntimeError("The migration needs its own transaction")
    started = _clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        ids = [row[0] for row in conn.execute(NEXT_IDS_SQL, (after_id, max_rows))]
        result = {
            "last_id": after_id,
            "rows": 0,
            "protected": 0,
            "errors": [],
            "done": not ids,
        }
        verified, checked, size = {}, set(), 0
        for image_id in ids:
            conn.execute("SAVEPOINT workflow_row")
            try:
                row = conn.execute(ROW_SQL, (image_id,)).fetchone()
                changed, protected, row_size = _rewrite_row(
                    conn, image_id, row, verified, checked
                )
            except Exception as error:
                if not _row_error(error):
                    raise
                conn.execute("ROLLBACK TO workflow_row")
                conn.execute("RELEASE workflow_row")
                verified.clear()  # the row's documents were rolled back too
                checked.clear()
                logger.warning(f"Image record {image_id} kept inline: {error}")
                result["errors"].append(image_id)
                changed, protected, row_size = False, 0, 0
            else:
                conn.execute("RELEASE workflow_row")
            result["rows"] += int(changed)
            result["protected"] += protected
            result["last_id"] = image_id
            size += row_size
            if size >= max_bytes or _clock() - started >= max_seconds:
                break
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise


def pre_migration_backups(db_path):
    """The kept pre-migration backups beside ``db_path``, earliest first."""
    db_path = os.path.abspath(db_path)
    prefix = os.path.basename(db_path) + "." + PRE_MIGRATION_LABEL + "_"
    folder = os.path.dirname(db_path)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(
        name
        for name in names
        if name.startswith(prefix) and os.path.isfile(os.path.join(folder, name))
    )


def _file_bytes(db_path):
    return sum(
        os.path.getsize(path)
        for path in (db_path, db_path + "-wal")
        if os.path.exists(path)
    )


class WorkflowStorageOperations:
    """PromptDatabase mixin: the workflow storage migration and Reclaim space."""

    def migrate_workflow_storage(
        self,
        *,
        on_batch=None,
        should_stop=None,
        max_rows=BATCH_ROWS,
        max_bytes=BATCH_BYTES,
    ) -> dict:
        """Rewrite every old-form row, batch by batch (blocking).

        ``on_batch(progress)`` runs after each committed batch with the
        running totals ``{rows, protected, batches, last_id}``;
        ``should_stop()`` is asked before each batch. Each committed batch is
        followed by ``BATCH_PAUSE`` with the write lock free. Returns
        ``{rows, protected, batches, stopped, errors, documents_removed}``
        (``errors``: the ids of rows kept inline by an error). Unreferenced
        documents are swept once every row was looked at.
        """
        totals = {"rows": 0, "protected": 0, "batches": 0, "last_id": 0}
        errors = []
        stopped = False
        while True:
            if should_stop is not None and should_stop():
                stopped = True
                break
            batch = self.migrate_workflow_batch(totals["last_id"], max_rows, max_bytes)
            if batch["done"]:
                break
            time.sleep(BATCH_PAUSE)  # leave the write lock to other writers
            errors += batch["errors"]
            totals["rows"] += batch["rows"]
            totals["protected"] += batch["protected"]
            totals["batches"] += 1
            totals["last_id"] = batch["last_id"]
            if on_batch is not None:
                on_batch(dict(totals))
        removed = 0 if stopped else self.sweep_workflow_documents()
        return {
            "rows": totals["rows"],
            "protected": totals["protected"],
            "batches": totals["batches"],
            "stopped": stopped,
            "errors": errors,
            "documents_removed": removed,
        }

    def migrate_workflow_batch(
        self, after_id, max_rows=BATCH_ROWS, max_bytes=BATCH_BYTES
    ) -> dict:
        """One batch on the calling thread's connection (``migrate_batch``)."""
        return migrate_batch(self.model.get_connection(), after_id, max_rows, max_bytes)

    def sweep_workflow_documents(self) -> int:
        """Remove every document no image row references (one transaction)."""
        conn = self.model.get_connection()
        with conn:
            return sweep_documents(conn)

    def workflow_storage_status(self) -> dict:
        """``workflow_storage_counts`` on one read-only snapshot (blocking)."""
        from .maintenance import _read_snapshot

        return _read_snapshot(self.model.db_path, self.workflow_storage_counts)

    def workflow_storage_counts(self, conn) -> dict:
        """``{rows, protected, documents, inline_bytes}`` on ``conn`` (reads only).

        ``inline_bytes`` is None where SQLite has no ``octet_length``.
        """
        rows, protected = conn.execute(INLINE_COUNTS_SQL).fetchone()
        documents = conn.execute("SELECT COUNT(*) FROM workflow_documents").fetchone()
        inline_bytes = None
        if sqlite3.sqlite_version_info >= (3, 43, 0):
            row = conn.execute(INLINE_BYTES_SQL).fetchone()
            inline_bytes = sum(value or 0 for value in row)
        return {
            "rows": rows or 0,
            "protected": protected or 0,
            "documents": documents[0],
            "inline_bytes": inline_bytes,
        }

    def reclaim_space(self) -> dict:
        """Sweep, VACUUM and truncate the WAL (blocking); file bytes before/after.

        The caller holds the registry's ``exclusive`` slot. Raises whatever
        stopped VACUUM; the database is then unchanged.
        """
        db_path = self.model.db_path
        removed = self.sweep_workflow_documents()
        before = _file_bytes(db_path)
        self.model.vacuum_database()
        with closing(sqlite3.connect(db_path)) as checkpoint:
            busy = checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
        return {
            "before_bytes": before,
            "after_bytes": _file_bytes(db_path),
            "documents_removed": removed,
            "checkpoint_busy": bool(busy),
        }

    # ── preview ───────────────────────────────────────────────────────

    def _plan_workflow_storage(self):
        """The reviewed plan of the migration (one read-only snapshot)."""
        from .maintenance import (
            MaintenanceDraft,
            _Fingerprint,
            _megabytes,
            _plural,
            _read_snapshot,
        )

        def read(conn):
            sizes = [
                conn.execute(f"PRAGMA {name}").fetchone()[0]
                for name in ("page_size", "page_count", "freelist_count")
            ]
            return sizes, self.workflow_storage_counts(conn)

        (page_size, page_count, free_pages), counts = _read_snapshot(
            self.model.db_path, read
        )
        size = page_size * page_count
        details = [
            {
                "label": "Database file",
                "detail": f"{_megabytes(size)}, of which "
                f"{_megabytes(free_pages * page_size)} is free pages",
            },
            {
                "label": "Image records",
                "detail": f"{_plural(counts['rows'], 'record')} keep their workflow "
                "or prompt graph inline; each distinct text is stored once, "
                "compressed",
            },
        ]
        if not counts["rows"]:
            details[1]["detail"] = NOTHING_TO_DO
        if counts["inline_bytes"] is not None:
            details.append(
                {
                    "label": "Inline text",
                    "detail": f"{_megabytes(counts['inline_bytes'])} now",
                }
            )
        if counts["documents"]:
            details.append(
                {
                    "label": "Already stored once",
                    "detail": _plural(counts["documents"], "workflow document"),
                }
            )
        name = os.path.basename(self.model.db_path)
        kept = pre_migration_backups(self.model.db_path)
        backup = (
            POOLED_BACKUP_NOTE.format(
                name=f"{name}.backup_<date and time>", kept=kept[0]
            )
            if kept
            else BACKUP_NOTE.format(
                name=f"{name}.{PRE_MIGRATION_LABEL}_<date and time>"
            )
        )
        details += [
            {"label": "Backup", "detail": backup},
            {"label": "Reclaim space", "detail": RECLAIM_NOTE},
            {"label": "Older versions", "detail": OLDER_VERSIONS_NOTE},
        ]
        protected = counts["protected"]
        return MaintenanceDraft(
            operation=OPERATION,
            fingerprint=_Fingerprint(OPERATION).hexdigest(),
            targets=[],
            scope={
                "count": counts["rows"],
                "label": (
                    _plural(counts["rows"], "image record") + " to rewrite"
                    if counts["rows"]
                    else "Nothing to rewrite"
                ),
            },
            protected={
                "count": protected,
                "reasons": (
                    [f"{_plural(protected, 'stored value')} not text; kept as is"]
                    if protected
                    else []
                ),
            },
            media={"deletes_files": False, "label": MEDIA_NOTE},
            details=details,
        )


MEDIA_NOTE = "No image or video file is opened or changed; only the database."
RECLAIM_NOTE = (
    "When every record is rewritten, Reclaim space runs alone to give the freed "
    "space back to the disk (VACUUM). The database is locked while it runs."
)
BACKUP_NOTE = (
    "Always taken first, whatever the safety backup setting: {name} beside "
    "the database, verified, and kept until you remove it (it is never pruned)."
)
POOLED_BACKUP_NOTE = (
    "A safety backup is taken first, whatever the safety backup setting: {name} "
    "among the pooled backups; the pre-migration backup {kept} is already kept."
)
NOTHING_TO_DO = "Every image record already stores its workflow once."
OLDER_VERSIONS_NOTE = (
    "This version stores the workflows of images it records in the new form, "
    "whether or not this job runs, and this job rewrites the rest. An older "
    "Prompt Manager version shows those images without their workflow until you "
    "update again. The way back to an older version is the earliest "
    "pre-workflow-storage backup."
)
