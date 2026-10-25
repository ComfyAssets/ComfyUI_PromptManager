"""A restore in two steps: stage the upload, then publish it (#249 System S8).

**Stage** (``stage_restore``) never touches the live database or the source.
In a new folder ``.pm-restore-*`` in the library's own folder (#318: never
the system temp folder, which may be another disk), it:

1. checks the source (``verify_database_file`` without reading every
   workflow document: a damaged one is repaired below, not refused);
2. copies it with SQLite's backup API (a rollback-journal file and a WAL file
   alike) and opens the copy as a ``PromptModel``, which migrates an older
   schema;
3. repairs damaged workflow documents (``repair_damaged_documents``),
   rotates the history epoch and counts what the copy holds;
4. makes the copy one self-contained file and verifies it fully (every
   current table, every document);
5. records the copy's SHA-256, so publishing binds exactly these bytes.

**Publish** (``publish_staged``) re-hashes the stage (``StageChanged`` if it
differs), keeps the current database as a uniquely named, standalone safety
backup beside it (``database/safety_backup.py``; not verified, because the
database being replaced may be the damaged one), copies the stage over the
live pages through the backup API (no file is swapped under open handles; an
``OperationalError`` there leaves the live data intact, #226), then
checkpoints the WAL with TRUNCATE so the copied pages do not leave a
database-sized ``-wal`` behind. A reader that keeps a snapshot past the busy
wait is logged, not fatal: the restore itself is done.

Every handle on the stage is closed before its folder is removed (Windows).
``sweep_restore_temps`` removes what an interrupted restore left (startup).
"""

import hashlib
import logging
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from . import workflow_documents as wd
from .execution_history_schema import rotate_history_epoch
from .safety_backup import take_safety_backup

logger = logging.getLogger("prompt_manager.database.restore_stage")

STAGE_PREFIX = ".pm-restore-"
UPLOAD_PREFIX = ".pm-restore-upload-"
STAGED_NAME = "staged.db"
# How long the post-restore TRUNCATE checkpoint waits for readers to leave.
CHECKPOINT_BUSY_MS = 30_000
_HASH_CHUNK = 1024 * 1024
SUMMARY_KEYS = (
    "prompts",
    "positives",
    "pairs",
    "archived_pairs",
    "image_links",
    "runs",
    "saved_views",
)


class StageChanged(ValueError):
    """The staged file is no longer the bytes that were reviewed."""


@dataclass
class StagedRestore:
    """A verified, migrated copy of an upload, ready to publish."""

    path: str
    digest: str
    size: int
    summary: Dict[str, int]
    schema: Dict[str, bool]
    documents: Dict[str, int] = field(
        default_factory=lambda: {"repaired": 0, "cleared": 0, "images": 0}
    )

    @property
    def directory(self) -> str:
        return os.path.dirname(self.path)

    def discard(self) -> None:
        """Remove the stage folder (idempotent; its handles are all closed)."""
        remove_stage_folder(self.directory)


def remove_stage_folder(directory: str) -> None:
    """Remove a stage folder and its files, logging what cannot be removed."""
    if not directory or not os.path.isdir(directory):
        return
    for name in os.listdir(directory):
        try:
            os.remove(os.path.join(directory, name))
        except OSError as error:
            logger.warning(f"Could not remove restore stage file {name}: {error}")
    try:
        os.rmdir(directory)
    except OSError as error:
        logger.warning(f"Could not remove the restore stage folder: {error}")


def file_digest(path: str) -> str:
    """``sha256:<hex>`` of the file's bytes (blocking)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _readonly(path: str) -> sqlite3.Connection:
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def _schema(path: str) -> list:
    """The file's schema objects, to tell whether staging migrated it."""
    with closing(_readonly(path)) as conn:
        return sorted(
            conn.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall(),
            key=repr,
        )


def _count(conn: sqlite3.Connection, table: str, where: str = "") -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return 0
    # ``table`` and ``where`` are constants of library_summary, never input.
    return conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0]  # nosec


def library_summary(conn: sqlite3.Connection) -> Dict[str, int]:
    """What a library holds, for a restore's review (reads only).

    ``positives`` are the Library's prompts (``library_counts``'s
    ``positive_prompts``); ``pairs`` the active saved pairs and
    ``archived_pairs`` the archived ones; ``image_links`` the indexed image
    rows; ``runs`` the recorded executions.
    """
    from .library import _ELIGIBLE

    has_state = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND "
        "name='prompt_library_state'"
    ).fetchone()
    has_pairs = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='prompt_pairs'"
    ).fetchone()
    if has_state and has_pairs:
        positives = conn.execute(
            "SELECT COUNT(*) FROM prompts p WHERE " + _ELIGIBLE
        ).fetchone()[0]
    else:
        positives = _count(conn, "prompts")
    return {
        "prompts": _count(conn, "prompts"),
        "positives": positives,
        "pairs": _count(conn, "prompt_pairs", "WHERE archived=0"),
        "archived_pairs": _count(conn, "prompt_pairs", "WHERE archived<>0"),
        "image_links": _count(conn, "generated_images"),
        "runs": _count(conn, "execution_runs"),
        "saved_views": _count(conn, "saved_views"),
    }


# The newest row of each table a restore would overwrite: with the counts,
# a write after the review (a new run, image, prompt or edit) changes it.
_FINGERPRINT_SQL = (
    ("prompts", "SELECT MAX(id), MAX(updated_at), MAX(last_used_at) FROM prompts"),
    ("generated_images", "SELECT MAX(id) FROM generated_images"),
    ("prompt_pairs", "SELECT MAX(id), MAX(updated_at) FROM prompt_pairs"),
    ("execution_runs", "SELECT COUNT(*), MAX(rowid) FROM execution_runs"),
    ("saved_views", "SELECT MAX(rowid) FROM saved_views"),
)


def live_fingerprint(conn: sqlite3.Connection):
    """``(summary, fingerprint)`` of a live library, read in one snapshot.

    The fingerprint changes when anything a restore would overwrite was
    added or edited since (counts plus the newest row and edit of each
    table); the apply refuses with ``plan-changed`` then.
    """
    import json

    with conn:
        if not conn.in_transaction:
            conn.execute("BEGIN")  # one read snapshot for every count
        summary = library_summary(conn)
        newest = []
        for table, sql in _FINGERPRINT_SQL:
            try:
                newest.append([table, list(conn.execute(sql).fetchone())])
            except sqlite3.OperationalError:
                newest.append([table, None])  # no such table or column
    text = json.dumps([summary, newest], sort_keys=True, default=str)
    return summary, "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _copy(src_path: str, staged_path: str) -> None:
    source = _readonly(src_path)
    try:
        with closing(sqlite3.connect(staged_path)) as destination:
            source.backup(destination)
    finally:
        source.close()


def _standalone(path: str) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=DELETE").fetchone()


def stage_restore(model_cls, db_path: str, src_path: str) -> StagedRestore:
    """Stage ``src_path`` beside ``db_path`` (blocking); see the module docstring.

    Raises ValueError with a readable reason when the source or the migrated
    stage does not verify; the stage folder is then already removed.
    """
    ok, reason = model_cls.verify_database_file(src_path, documents=False)
    if not ok:
        raise ValueError(reason)
    before = _schema(src_path)
    with closing(_readonly(src_path)) as conn:
        library_state = bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND "
                "name='prompt_library_state'"
            ).fetchone()
        )
    folder = os.path.dirname(os.path.abspath(db_path))
    directory = tempfile.mkdtemp(prefix=STAGE_PREFIX, dir=folder)
    staged_path = os.path.join(directory, STAGED_NAME)
    staged_key = os.path.normcase(os.path.realpath(staged_path))
    try:
        _copy(src_path, staged_path)
        staged = None
        try:
            staged = model_cls(staged_path)
            conn = staged.get_connection()
            documents = wd.repair_damaged_documents(conn)
            rotate_history_epoch(conn)
            summary = library_summary(conn)
        finally:
            if staged is not None:
                staged.close_all()
        _standalone(staged_path)
        ok, reason = model_cls.verify_database_file(
            staged_path, require_library_state=True
        )
        if not ok:
            raise ValueError(reason)
        result = StagedRestore(
            path=staged_path,
            digest=file_digest(staged_path),
            size=os.path.getsize(staged_path),
            summary=summary,
            schema={
                "migrated": _schema(staged_path) != before,
                "library_state": library_state,
            },
            documents=documents,
        )
    except BaseException:
        remove_stage_folder(directory)
        raise
    finally:
        with model_cls._init_lock:
            model_cls._initialized_paths.discard(staged_key)
    return result


def checkpoint_truncate(conn: sqlite3.Connection) -> bool:
    """Checkpoint the WAL and truncate it to 0 bytes; False when a reader held it."""
    previous = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(CHECKPOINT_BUSY_MS)}")
        busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
    finally:
        conn.execute(f"PRAGMA busy_timeout = {int(previous)}")
    return not busy


def publish_staged(model, staged: StagedRestore) -> str:
    """Publish ``staged`` over ``model``'s database; return the safety backup path.

    "" when there was no database to back up. Raises StageChanged before
    anything is written when the stage's bytes changed since staging, and
    SafetyBackupFailed when the current database could not be kept. An
    ``OperationalError`` from the page copy propagates with
    ``_prompt_manager_restore_backup_created`` set; the live data is intact.
    """
    if not os.path.isfile(staged.path) or file_digest(staged.path) != staged.digest:
        raise StageChanged("The staged restore changed since it was reviewed")
    backup_path = ""
    if os.path.exists(model.db_path):
        backup_path = take_safety_backup(model, verify=False)
        logger.info(f"Current database backed up to: {os.path.basename(backup_path)}")
    try:
        model._replace_contents_from(staged.path)
    except sqlite3.OperationalError as exc:
        exc._prompt_manager_restore_backup_created = bool(backup_path)
        raise
    with model._init_lock:
        model._initialized_paths.add(model._schema_key())
    _truncate_wal(model)
    return backup_path


def _truncate_wal(model) -> Optional[bool]:
    """TRUNCATE-checkpoint the live WAL after a publish (never raises)."""
    try:
        conn = model.get_connection()
        with conn.using():
            truncated = checkpoint_truncate(conn)
    except sqlite3.Error as error:
        logger.warning(f"The database log could not be truncated: {error}")
        return None
    if not truncated:
        logger.warning(
            "The database log could not be truncated after the restore: a "
            "reader kept it open. It shrinks at the next checkpoint with no "
            "reader."
        )
    return truncated


def sweep_restore_temps(db_path: str) -> list:
    """Remove the stages and uploads an interrupted restore left (startup)."""
    folder = os.path.dirname(os.path.abspath(db_path))
    removed = []
    try:
        names = os.listdir(folder)
    except OSError:
        return removed
    for name in names:
        path = os.path.join(folder, name)
        if name.startswith(UPLOAD_PREFIX) and os.path.isfile(path):
            try:
                os.remove(path)
                removed.append(name)
            except OSError:
                continue
        elif name.startswith(STAGE_PREFIX) and os.path.isdir(path):
            remove_stage_folder(path)
            if not os.path.exists(path):
                removed.append(name)
    return removed
