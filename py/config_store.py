"""The one serialized writer of config.json (#220).

Every save that changes config.json goes through ``ConfigStore.commit``: the
settings page, the LoRA integration and, later, every System section. A
commit runs under one process-wide lock, re-reads the file inside it, applies
the caller's change to a copy and replaces the file atomically. So two saves
never overwrite each other's keys, sections this process does not know are
kept byte-for-byte in meaning, and a write that fails leaves the old file in
place and raises instead of being logged and reported as saved.

A revision names the file content a client last saw: ``"sha256:<hex>"`` of
the file bytes, or ``"absent"`` when there is no file. A commit that names a
revision refuses to write over different content (``RevisionConflict``).

Reads take the same lock, so a read handle is never open while the file is
replaced (Windows refuses that), and a caller's ``publish`` runs inside the
lock right after its write, so runtimes publish saves in the order they were
written. A symlinked config.json is written beside its real target.

config.json can hold the CivitAI key, so the file is written owner-only.
"""

import copy
import hashlib
import json
import os
import tempfile
import threading
import time
from typing import Callable, Optional, Tuple

ABSENT_REVISION = "absent"

# One lock for the process: every ConfigStore writes the same file. It is
# re-entrant so a commit's ``publish`` callback may read the config again.
_COMMIT_LOCK = threading.RLock()

# Windows: an antivirus, indexer or backup handle on config.json makes
# os.replace fail with PermissionError for a moment, so it is retried briefly.
_REPLACE_ATTEMPTS = 5 if os.name == "nt" else 1
_REPLACE_BACKOFF_SECONDS = 0.05


class ConfigUnreadable(Exception):
    """config.json exists but is not a readable JSON object; it is left alone."""


class RevisionConflict(Exception):
    """config.json changed since the revision the caller saw."""

    def __init__(self, current: str):
        super().__init__("config.json changed since it was read")
        self.current = current


def revision_of(data: Optional[bytes]) -> str:
    """The revision of a file's bytes; ``None`` means the file is absent."""
    if data is None:
        return ABSENT_REVISION
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _default_path() -> str:
    from .config import PromptManagerConfig

    return PromptManagerConfig.get_config_path()


def _replace(source: str, target: str) -> None:
    """``os.replace`` with a short retry on a transient Windows PermissionError."""
    for attempt in range(1, _REPLACE_ATTEMPTS + 1):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS:
                raise
            time.sleep(_REPLACE_BACKOFF_SECONDS)


def _write_atomically(path: str, payload: bytes) -> None:
    """Replace ``path`` with ``payload``: temp file beside it, fsync, replace.

    The descriptor is closed before ``os.replace`` (Windows refuses to replace
    over an open handle), and on any failure the temp file is closed and
    removed, so a failed write leaves the old file and no stray temp file.
    ``path`` is resolved first: a symlink keeps pointing at the updated
    target. A single-file bind mount cannot be replaced (EBUSY) and fails as
    an OSError, which callers report as not saved.
    """
    path = os.path.realpath(path)
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=parent, prefix=".config-", suffix=".tmp")
    try:
        try:
            handle = os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)
            raise
        with handle:  # closed before the replace, also when a write fails
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(temp_path, 0o600)
        _replace(temp_path, path)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


class ConfigStore:
    """Serialized, atomic reads and commits of config.json."""

    def __init__(self, path_getter: Optional[Callable[[], str]] = None):
        self._path_getter = path_getter or _default_path

    @property
    def path(self) -> str:
        """The config file this store reads and writes (resolved per call)."""
        return self._path_getter()

    def read(self) -> Tuple[dict, str]:
        """``(config, revision)``; a missing file is ``({}, "absent")``.

        Raises ConfigUnreadable when the file cannot be read, is not JSON, or
        is not a JSON object. Waits for a running commit (see the module
        docstring).
        """
        with _COMMIT_LOCK:
            return self._read(self.path)

    @staticmethod
    def _read(path: str) -> Tuple[dict, str]:
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except (FileNotFoundError, NotADirectoryError):
            # No file there (a parent that is a file cannot hold one either);
            # a commit then fails on the write, as config-write-failed.
            return {}, ABSENT_REVISION
        except OSError as exc:
            raise ConfigUnreadable(f"config.json cannot be read: {exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ConfigUnreadable("config.json is not valid JSON") from exc
        if not isinstance(data, dict):
            raise ConfigUnreadable("config.json is not a JSON object")
        return data, revision_of(raw)

    def commit(
        self,
        mutate: Callable[[dict], dict],
        *,
        expected_revision: Optional[str],
        publish: Optional[Callable[[dict], None]] = None,
    ) -> Tuple[dict, str]:
        """Apply ``mutate`` to the current config and write the result.

        ``mutate`` receives a deep copy of the file's content and returns the
        whole new config (an object). ``expected_revision`` None merges into
        whatever is on disk (legacy partial updates); a revision refuses to
        write over different content. Returns ``(config, revision)`` as
        written. Raises ConfigUnreadable, RevisionConflict, TypeError (a
        non-object result) or OSError (the write failed; the old file stays).

        ``publish(config)``, when given, runs under the lock once the file
        holds the new config, so concurrent saves reach the runtime in the
        order they were written. It must handle its own errors: the values
        are already durable when it runs.
        """
        with _COMMIT_LOCK:
            path = self.path
            current, revision = self._read(path)
            if expected_revision is not None and expected_revision != revision:
                raise RevisionConflict(revision)
            candidate = mutate(copy.deepcopy(current))
            if not isinstance(candidate, dict):
                raise TypeError("A config commit must produce a JSON object")
            payload = json.dumps(candidate, indent=2).encode("utf-8")
            _write_atomically(path, payload)
            if publish is not None:
                publish(candidate)
            return candidate, revision_of(payload)


_store = ConfigStore()


def get_config_store() -> ConfigStore:
    """The process's config store (on ``PromptManagerConfig.get_config_path``)."""
    return _store
