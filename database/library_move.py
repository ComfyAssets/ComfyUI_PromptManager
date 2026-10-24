"""Move library (#249 S6b): copy the node-folder library, verify it, switch.

``plan_move`` checks a ``utils.library_location`` location can move and
sizes the copy; ``move_library`` does it, in this order:

1. **Disk check** on the target's volume (the copy, the backups and
   config.json, plus a margin). The source is never renamed across folders,
   so another drive is simply a copy.
2. **Copy** with SQLite's backup API from a private connection holding one
   read transaction: the per-table counts, the workflow documents' digest
   and ``PRAGMA data_version`` describe exactly the copied snapshot. The copy
   is a temp file in the target folder, made self-contained
   (``journal_mode=DELETE``), fsynced and owner-only.
3. **Verify**: ``verify_database_file`` (integrity check, schema, every
   workflow document decompressed against its hash, #315), the same row
   count in every table and the same documents.
4. **Copy files**: every pooled safety backup and pre-migration backup,
   checked by size and SHA-256 after the copy (their times are kept, so the
   pool prunes in the same order).
5. **Publish** under the old database's write lock (``BEGIN IMMEDIATE``) and
   the config.json lock: if any other connection wrote since the snapshot
   (``data_version``), steps 2–4 run again (``attempts``, then
   ``library-busy``). Else config.json is copied (owner-only, checked), a
   ``pending`` marker is written, the temp files take their final names
   (``os.replace`` inside the target folder), the marker turns ``moved``
   and ``switch`` points the process at the new library while the old file
   is still locked, so no write can land in it.
6. **Retire**: the old file's WAL is checkpointed and the old ``prompts.db``
   and ``config.json`` are renamed ``*.moved-to-<folder>`` (never deleted;
   the old backups stay where they are). A file still open elsewhere is left
   in place and reported (``old-in-use``): the marker already makes a restart
   use the new library.

Any failure before the marker says ``moved`` removes everything the move
wrote in the target folder and leaves the old library in use, untouched.
A crash at any point leaves either the old library in use (the marker is
absent or ``pending``; the next move replaces its own leftovers) or the new
one (``moved``). ``MoveFailed`` carries a code and a message without paths.
"""

import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .models import BUSY_TIMEOUT_SECONDS, PromptModel

try:
    from ..utils import library_location as ll
    from ..utils.logging_config import get_logger
except ImportError:
    from utils import library_location as ll
    from utils.logging_config import get_logger

logger = get_logger("prompt_manager.database.library_move")

TEMP_PREFIX = ".pm-move-"
MOVED_INFIX = ".moved-to-"
DISK_MARGIN_BYTES = 16 * 2**20
DEFAULT_ATTEMPTS = 3
_COPY_CHUNK = 4 * 2**20
# Backups that live beside the database and move with it.
BACKUP_INFIXES = (".backup_", ".pre-workflow-storage_")

MESSAGES = {
    "not-movable": (
        "This library is not in the node folder, so there is nothing to move."
    ),
    "same-folder": "The new folder is the node folder itself.",
    "target-invalid": "The new folder cannot hold the library.",
    "target-has-library": (
        "The new folder already holds a Prompt Manager library. Nothing was moved."
    ),
    "target-conflict": (
        "The new folder already holds files with the same names. Nothing was moved."
    ),
    "insufficient-space": (
        "There is not enough free space for the copy. Nothing was moved."
    ),
    "copy-failed": "The copy could not be written or did not match. Nothing was moved.",
    "verify-failed": "The copy did not pass verification. Nothing was moved.",
    "library-busy": (
        "Prompt Manager kept writing to the library while it was copied. "
        "Nothing was moved; try again when no workflow is running."
    ),
    "switch-failed": (
        "The library was copied and verified, but this session could not "
        "switch to it. Restart ComfyUI to use it."
    ),
}


class MoveFailed(Exception):
    """A move that did not happen (or did not finish), with a coded reason."""

    def __init__(self, code: str, message: Optional[str] = None):
        self.code = code
        self.message = message or MESSAGES.get(code, "The library was not moved.")
        super().__init__(self.message)


@dataclass(frozen=True)
class MovePlan:
    location: "ll.LibraryLocation"
    db_path: str
    source_dir: str
    target_dir: str
    config_path: Optional[str]
    backups: Tuple[str, ...]
    db_bytes: int
    files_bytes: int

    @property
    def required_bytes(self) -> int:
        return self.db_bytes + self.files_bytes + DISK_MARGIN_BYTES

    @property
    def target_location(self) -> "ll.LibraryLocation":
        """Where the library is once moved (an explicit config.json stays)."""
        source = self.location.target_source or ll.SOURCE_DEFAULT
        config = (
            self.location.config_path
            if self.location.config_explicit
            else os.path.join(self.target_dir, ll.CONFIG_NAME)
        )
        return ll.LibraryLocation(
            source,
            os.path.join(self.target_dir, ll.DATABASE_NAME),
            config,
            self.target_dir,
            self.target_dir,
            None,
            source,
            self.location.config_explicit,
        )


@dataclass
class MoveResult:
    location: "ll.LibraryLocation"
    counts: Dict[str, int]
    files: List[str]
    renamed: List[str] = field(default_factory=list)
    retire_problem: Optional[str] = None


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _free_bytes(path: str) -> int:
    return shutil.disk_usage(path).free


def _existing_ancestor(path: str) -> str:
    current = os.path.abspath(path)
    while not os.path.isdir(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


def backup_names(db_path: str) -> Tuple[str, ...]:
    """The pooled and pre-migration backups beside ``db_path`` (names)."""
    folder = os.path.dirname(os.path.abspath(db_path))
    base = os.path.basename(db_path)
    prefixes = tuple(base + infix for infix in BACKUP_INFIXES)
    try:
        names = os.listdir(folder)
    except OSError:
        return ()
    return tuple(
        sorted(
            name
            for name in names
            if name.startswith(prefixes) and os.path.isfile(os.path.join(folder, name))
        )
    )


def plan_move(location) -> MovePlan:
    """Check ``location`` can move to its target; raises MoveFailed."""
    if not location.movable:
        raise MoveFailed("not-movable")
    target = os.path.abspath(location.target_dir)
    source_dir = os.path.dirname(os.path.abspath(location.db_path))
    if ll.same_folder(target, source_dir) or os.path.normcase(
        os.path.realpath(target)
    ) == os.path.normcase(os.path.realpath(source_dir)):
        raise MoveFailed("same-folder")
    if os.path.exists(target) and not os.path.isdir(target):
        raise MoveFailed("target-invalid")
    backups = backup_names(location.db_path)
    # config.json moves with a node-folder library; one named by
    # PROMPT_MANAGER_CONFIG_PATH is the user's and stays where it is.
    config = None if location.config_explicit else location.config_path
    config = config if config and os.path.isfile(config) else None
    if ll.read_marker(target).get("state") != ll.MARKER_PENDING:
        if os.path.exists(os.path.join(target, ll.DATABASE_NAME)):
            raise MoveFailed("target-has-library")
        names = [ll.CONFIG_NAME] if config else []
        if any(os.path.exists(os.path.join(target, n)) for n in names + list(backups)):
            raise MoveFailed("target-conflict")
    db_bytes = _size(location.db_path) + _size(location.db_path + "-wal")
    files_bytes = sum(_size(os.path.join(source_dir, n)) for n in backups)
    files_bytes += _size(config) if config else 0
    return MovePlan(
        location,
        os.path.abspath(location.db_path),
        source_dir,
        target,
        config,
        backups,
        db_bytes,
        files_bytes,
    )


def space_problem(plan: MovePlan) -> Optional[Tuple[int, int]]:
    """``(required, free)`` when the target's volume is short, else None."""
    free = _free_bytes(_existing_ancestor(plan.target_dir))
    if free < plan.required_bytes:
        return plan.required_bytes, free
    return None


# ── copy and verify ───────────────────────────────────────────────────


def _tables(conn) -> List[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _describe(conn) -> Tuple[Dict[str, int], str]:
    """Row counts per table and a digest of the workflow documents."""
    counts = {}
    for table in _tables(conn):
        # A name read from sqlite_master, quoted as an identifier.
        quoted = '"' + table.replace('"', '""') + '"'
        sql = f"SELECT COUNT(*) FROM {quoted}"  # nosec B608
        counts[table] = conn.execute(sql).fetchone()[0]
    digest = hashlib.sha256()
    if "workflow_documents" in counts:
        for row in conn.execute(
            "SELECT id, sha256, byte_length FROM workflow_documents ORDER BY id"
        ):
            digest.update(repr((row[0], bytes(row[1]), row[2])).encode())
    return counts, digest.hexdigest()


def _fsync_file(path: str) -> None:
    with open(path, "rb+") as handle:
        os.fsync(handle.fileno())


def _fsync_dir(path: str) -> None:
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _private(path: str) -> None:
    if os.name == "posix":
        os.chmod(path, 0o600)


def _copy_snapshot(source: sqlite3.Connection, temp: str):
    """Back up ``source``'s current snapshot to ``temp``; return what it holds."""
    source.execute("BEGIN")
    try:
        version = source.execute("PRAGMA data_version").fetchone()[0]
        counts, documents = _describe(source)
        destination = sqlite3.connect(temp)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.execute("COMMIT")
    with contextlib.closing(sqlite3.connect(temp)) as conn:
        conn.execute("PRAGMA journal_mode=DELETE").fetchone()
    _fsync_file(temp)
    _private(temp)
    return version, counts, documents


def _verify_copy(temp: str, counts, documents) -> None:
    ok, reason = PromptModel.verify_database_file(temp)
    if not ok:
        logger.error(f"Library move: the copy did not verify: {reason}")
        detail = reason if reason.startswith("Workflow document") else None
        raise MoveFailed(
            "verify-failed",
            f"{MESSAGES['verify-failed']} {detail}" if detail else None,
        )
    uri = "file:" + temp.replace(os.sep, "/") + "?mode=ro"
    try:
        with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
            copied = _describe(conn)
    except sqlite3.Error as error:
        logger.error(f"Library move: the copy could not be read: {error}")
        raise MoveFailed("verify-failed") from error
    if copied != (counts, documents):
        logger.error("Library move: the copy's rows or documents differ")
        raise MoveFailed("verify-failed")


def _hash_copy(source: str, destination: str) -> Tuple[int, str]:
    """Copy ``source`` to ``destination`` (fsynced, owner-only, times kept)."""
    digest = hashlib.sha256()
    size = 0
    with open(source, "rb") as reader, open(destination, "wb") as writer:
        while True:
            chunk = reader.read(_COPY_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    stat = os.stat(source)
    os.utime(destination, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    _private(destination)
    return size, digest.hexdigest()


def _file_digest(path: str) -> Tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as reader:
        while True:
            chunk = reader.read(_COPY_CHUNK)
            if not chunk:
                return size, digest.hexdigest()
            digest.update(chunk)
            size += len(chunk)


def _check_copies(copies) -> None:
    for source, temp, expected in copies:
        if _file_digest(temp) != expected or _file_digest(source) != expected:
            logger.error(
                f"Library move: {os.path.basename(source)} did not copy unchanged"
            )
            raise MoveFailed("copy-failed")


def write_marker(directory: str, state: str, source_db: str) -> str:
    """Atomically write the move marker; return its path."""
    path = os.path.join(directory, ll.MARKER_NAME)
    temp = os.path.join(directory, f"{TEMP_PREFIX}{uuid.uuid4().hex}.marker")
    payload = json.dumps(
        {
            "version": 1,
            "state": state,
            "from": source_db,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        indent=2,
    ).encode("utf-8")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _private(temp)
        os.replace(temp, path)
    except BaseException:
        _remove(temp)
        raise
    _fsync_dir(directory)
    return path


def _remove(*paths) -> None:
    for path in paths:
        try:
            if path and os.path.lexists(path):
                os.remove(path)
        except OSError:
            logger.warning(f"Library move: could not remove {os.path.basename(path)}")


def sweep_move_temps(directory: str) -> List[str]:
    """Remove the temp files an interrupted move left in ``directory``."""
    removed = []
    try:
        names = os.listdir(directory)
    except OSError:
        return removed
    for name in names:
        if name.startswith(TEMP_PREFIX):
            _remove(os.path.join(directory, name))
            removed.append(name)
    return removed


# ── the move ─────────────────────────────────────────────────────────


def _noop(*_args, **_kwargs):
    return None


def switch_process(old_db: str, location) -> None:
    """Point this process at ``location``: resolver, every model, observers."""
    from .execution_history import rekey_history_restore
    from .review_changes import rekey_review_changes

    ll.switch_to(location)
    PromptModel.repoint_instances(old_db, location.db_path)
    rekey_review_changes(old_db, location.db_path)
    rekey_history_restore(old_db, location.db_path)


class _Move:
    def __init__(self, plan, *, switch, config_lock, progress, checkpoint):
        self.plan = plan
        self.switch = switch
        self.config_lock = config_lock
        self.progress = progress
        self.checkpoint = checkpoint
        self.created_dir = False
        self.temps: List[str] = []
        self.published: List[str] = []
        self.marker: Optional[str] = None
        self.moved = False

    def temp(self, suffix: str) -> str:
        path = os.path.join(
            self.plan.target_dir, f"{TEMP_PREFIX}{uuid.uuid4().hex}{suffix}"
        )
        self.temps.append(path)
        return path

    def prepare_target(self) -> None:
        target = self.plan.target_dir
        self.created_dir = not os.path.isdir(target)
        ll.ensure_private_dir(target)
        sweep_move_temps(target)
        if ll.read_marker(target).get("state") == ll.MARKER_PENDING:
            # An earlier move stopped before it switched: its files are ours.
            for name in (ll.DATABASE_NAME, ll.CONFIG_NAME) + self.plan.backups:
                _remove(os.path.join(target, name))
            for suffix in ("-wal", "-shm", "-journal"):
                _remove(os.path.join(target, ll.DATABASE_NAME + suffix))

    def copy_round(self, source):
        """Steps 2–4 once: the verified temp copies and the snapshot version."""
        self.progress("Copying the database")
        temp_db = self.temp(".db")
        version, counts, documents = _copy_snapshot(source, temp_db)
        self.checkpoint("copied")
        self.progress("Verifying the copy")
        _verify_copy(temp_db, counts, documents)
        self.checkpoint("verified")
        copies = []
        for name in self.plan.backups:
            src = os.path.join(self.plan.source_dir, name)
            temp = self.temp(".bak")
            try:
                expected = _hash_copy(src, temp)
            except OSError as error:
                logger.error(f"Library move: copying {name} failed: {error}")
                raise MoveFailed("copy-failed") from error
            copies.append((src, temp, expected, name))
        if copies:
            self.progress(f"Copied {len(copies)} backup file(s)")
        self.checkpoint("files")
        _check_copies([(s, t, e) for s, t, e, _ in copies])
        return version, counts, temp_db, copies

    def discard_round(self, temp_db, copies) -> None:
        _remove(temp_db, *[temp for _s, temp, _e, _n in copies])

    def publish(self, temp_db, copies) -> List[str]:
        """Under the locks: config.json, marker, final names, marker moved."""
        target = self.plan.target_dir
        files = []
        if self.plan.config_path:
            temp = self.temp(".config")
            try:
                expected = _hash_copy(self.plan.config_path, temp)
            except OSError as error:
                raise MoveFailed("copy-failed") from error
            _check_copies([(self.plan.config_path, temp, expected)])
            copies = copies + [(self.plan.config_path, temp, expected, ll.CONFIG_NAME)]
        self.marker = write_marker(target, ll.MARKER_PENDING, self.plan.db_path)
        self.checkpoint("pending")
        for _src, temp, _expected, name in copies:
            final = os.path.join(target, name)
            os.replace(temp, final)
            self.published.append(final)
            files.append(name)
        final_db = os.path.join(target, ll.DATABASE_NAME)
        os.replace(temp_db, final_db)
        self.published.append(final_db)
        files.append(ll.DATABASE_NAME)
        _fsync_dir(target)
        self.checkpoint("published")
        write_marker(target, ll.MARKER_MOVED, self.plan.db_path)
        self.moved = True
        self.checkpoint("marked")
        return files

    def cleanup(self) -> None:
        if self.moved:
            return
        _remove(*self.temps, *self.published, self.marker)
        if self.created_dir:
            try:
                os.rmdir(self.plan.target_dir)
            except OSError:
                pass


def _unique(path: str) -> str:
    candidate, number = path, 1
    while os.path.lexists(candidate):
        candidate = f"{path}_{number}"
        number += 1
    return candidate


def retire_old_files(plan: MovePlan) -> Tuple[List[str], Optional[str]]:
    """Rename the old database and config.json; ``(renamed, problem code)``.

    Every connection to the old file must be closed. The WAL is checkpointed
    first; when it cannot be emptied (another process still has the file
    open) nothing is renamed (``old-in-use``).
    """
    old = plan.db_path
    folder = os.path.basename(os.path.normpath(plan.target_dir))
    try:
        with contextlib.closing(sqlite3.connect(old, timeout=1)) as conn:
            busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
    except sqlite3.Error as error:
        logger.warning(
            f"Library move: the old database could not be checkpointed: {error}"
        )
        return [], "old-in-use"
    if busy or _size(old + "-wal") > 0:
        return [], "old-in-use"
    renamed = []
    try:
        for path in (old, plan.config_path):
            if not path or not os.path.isfile(path):
                continue
            new_name = _unique(path + MOVED_INFIX + folder)
            os.rename(path, new_name)
            renamed.append(os.path.basename(new_name))
        for suffix in ("-wal", "-shm"):
            if os.path.isfile(old + suffix) and _size(old + suffix) == 0:
                os.remove(old + suffix)
    except OSError as error:
        logger.warning(f"Library move: the old files could not be renamed: {error}")
        return renamed, "old-locked"
    return renamed, None


def move_library(
    plan: MovePlan,
    *,
    switch: Callable = switch_process,
    config_lock=None,
    progress: Callable[[str], None] = _noop,
    checkpoint: Callable[[str], None] = _noop,
    attempts: int = DEFAULT_ATTEMPTS,
) -> MoveResult:
    """Move the library as planned (blocking; see the module docstring)."""
    short = space_problem(plan)
    if short is not None:
        raise MoveFailed("insufficient-space")
    lock = config_lock if config_lock is not None else contextlib.nullcontext()
    move = _Move(
        plan,
        switch=switch,
        config_lock=lock,
        progress=progress,
        checkpoint=checkpoint,
    )
    location = plan.target_location
    try:
        move.prepare_target()
        files, counts = _copy_and_switch(move, location, attempts)
    except Exception:
        move.cleanup()
        raise
    from .execution_history import notify_history_restore

    notify_history_restore(location.db_path)
    progress("Renaming the old files")
    renamed, problem = retire_old_files(plan)
    checkpoint("retired")
    return MoveResult(location, counts, files, renamed, problem)


def _copy_and_switch(move: _Move, location, attempts: int):
    plan = move.plan
    for attempt in range(1, attempts + 1):
        source = sqlite3.connect(
            plan.db_path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None
        )
        try:
            source.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_SECONDS * 1000}")
            version, counts, temp_db, copies = move.copy_round(source)
            source.execute("BEGIN IMMEDIATE")
            try:
                if source.execute("PRAGMA data_version").fetchone()[0] != version:
                    move.progress("The library changed during the copy; copying again")
                    move.discard_round(temp_db, copies)
                    continue
                move.checkpoint("locked")
                with move.config_lock:
                    files = move.publish(temp_db, copies)
                    try:
                        move.switch(plan.db_path, location)
                    except Exception as error:
                        logger.exception("Library move: switching failed")
                        raise MoveFailed("switch-failed") from error
                    move.checkpoint("switched")
            finally:
                if source.in_transaction:
                    source.execute("ROLLBACK")
            return files, counts
        finally:
            source.close()
    raise MoveFailed("library-busy")
