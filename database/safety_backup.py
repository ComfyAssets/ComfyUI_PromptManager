"""Safety backups beside the live database: one writer for every caller.

A safety backup is ``<db>.<label>_<YYYYmmdd_HHMMSS_ffffff>[_n]`` in the
library's own folder. It is written by SQLite's backup API into a temp file
beside the database (``SAFETY_TEMP_PREFIX``, swept at startup after a crash),
made one self-contained file (rollback journal), optionally verified, fsynced,
made private (``0o600`` on POSIX) and only then given its unique name, so a
backup name never carries a half-written file. The names are unique within
one second (microseconds, then a counter): on Windows two backups in the same
second must not overwrite each other.

Maintenance applies take a verified backup (``py/api/maintenance.py``); a
restore takes an unverified one, because the database it replaces may be the
damaged one the user is recovering from (``database/restore_stage.py``).
"""

import datetime
import os
import sqlite3
import tempfile
from contextlib import closing

SAFETY_TEMP_PREFIX = ".pm-safety-"
_NAME_ATTEMPTS = 1000


class SafetyBackupFailed(Exception):
    """The safety backup could not be written, verified or put in place.

    ``reason`` is the verify reason when the copy did not verify.
    """

    def __init__(self, message, reason=None):
        super().__init__(message)
        self.reason = reason


def _now():
    return datetime.datetime.now()


def remove_quietly(*paths):
    """Remove each existing path, ignoring errors (cleanup after a failure)."""
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


def backup_names(db_path, label="backup"):
    """Candidate names, unique within one second (microseconds, then ``_n``)."""
    base = f"{db_path}.{label}_{_now().strftime('%Y%m%d_%H%M%S_%f')}"
    for attempt in range(_NAME_ATTEMPTS):
        yield base if attempt == 0 else f"{base}_{attempt}"


def reserve_backup_name(db_path, label="backup"):
    """Create and return an unused ``<db>.<label>_<stamp>[_n]`` (O_EXCL)."""
    for candidate in backup_names(db_path, label):
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(fd)
        return candidate
    raise SafetyBackupFailed("No free backup name")


def place_backup(temp, db_path, label="backup"):
    """Give the finished ``temp`` its unique backup name; return that path.

    ``os.link`` is atomic and refuses an existing name, so no empty or half
    file ever carries a backup name, even across a crash. A filesystem
    without hard links falls back to reserving the name (``O_EXCL``) and
    ``os.replace``. The temp file is removed by the caller.
    """
    for candidate in backup_names(db_path, label):
        try:
            os.link(temp, candidate)
        except FileExistsError:
            continue
        except (OSError, NotImplementedError):
            break  # no hard links here
        else:
            return candidate
    else:
        raise SafetyBackupFailed("No free backup name")
    final = reserve_backup_name(db_path, label)
    try:
        os.replace(temp, final)
    except OSError:
        remove_quietly(final)
        raise
    return final


def sweep_safety_temps(db_path):
    """Remove the temp copies an interrupted safety backup left (blocking).

    Called once at startup, before any job can take a backup. Returns the
    names removed.
    """
    folder = os.path.dirname(os.path.abspath(db_path))
    removed = []
    try:
        names = os.listdir(folder)
    except OSError:
        return removed
    for name in names:
        if not (name.startswith(SAFETY_TEMP_PREFIX) and ".tmp" in name):
            continue
        try:
            os.remove(os.path.join(folder, name))
            removed.append(name)
        except OSError:
            continue
    return removed


def make_standalone(path):
    """Make the copy one self-contained file (rollback journal, not WAL).

    The backup API copies the live database's WAL flag; a WAL file opened
    read-only later (a verify, a restore preview) would leave ``-wal`` and
    ``-shm`` sidecars beside it.
    """
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("PRAGMA journal_mode=DELETE").fetchone()
    except sqlite3.Error as exc:
        raise SafetyBackupFailed("The backup could not be finished") from exc


def fsync_file(path):
    with open(path, "rb+") as handle:
        os.fsync(handle.fileno())


def safety_temp(db_path):
    """A new, closed temp file beside the database (``SAFETY_TEMP_PREFIX``)."""
    fd, temp = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(db_path)),
        prefix=SAFETY_TEMP_PREFIX,
        suffix=".tmp",
    )
    os.close(fd)
    return temp


def take_safety_backup(model, label="backup", *, verify=True):
    """Write (and by default verify) a safety backup; return its path (blocking).

    The copy is made in a temp file beside the database and only renamed
    onto its final, unique name ``<db>.<label>_<stamp>`` once it is complete
    (and verified), so a backup file on disk is always a whole one. ``label``
    "backup" joins the pruned pool; another label (the workflow storage
    migration's ``pre-workflow-storage``) is kept until the user removes it.
    Raises SafetyBackupFailed, leaving no file.
    """
    db_path = os.path.abspath(model.db_path)
    temp = safety_temp(db_path)
    final = None
    try:
        if not model.backup_database(temp):
            raise SafetyBackupFailed("The backup could not be written")
        make_standalone(temp)
        if verify:
            ok, reason = model.verify_database_file(temp)
            if not ok:
                raise SafetyBackupFailed("The backup did not verify", reason)
        fsync_file(temp)
        if os.name != "nt":
            os.chmod(temp, 0o600)
        final = place_backup(temp, db_path, label)
        return final
    except OSError as exc:
        remove_quietly(final)
        raise SafetyBackupFailed("The backup could not be put in place") from exc
    except SafetyBackupFailed:
        remove_quietly(final)
        raise
    finally:
        remove_quietly(temp, temp + "-journal", temp + "-wal", temp + "-shm")
