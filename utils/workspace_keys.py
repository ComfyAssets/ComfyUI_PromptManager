"""The per-install secret behind the workspace's gallery scopes and cursors (#430).

A page addresses a listed file as ``{root_index, root_scope, path}``. The root
scope is an HMAC over the root's index and canonical path, so the server can
refuse an action aimed at a root that was renumbered or replaced since the page
listed it (``py/api/images.py`` ``_gallery_scope_error``). The listing's keyset
cursors are MACed with a second key, so a forged position never reaches the
sort code.

Both keys used to be drawn per process, so every ComfyUI restart made every
open workspace tab stale. They now derive from one random secret persisted in
``workspace-scope.key`` beside the database (never in ``config.json`` and never
in the database, so a backup or an export does not carry it). When the folder
cannot hold it, the process falls back to a per-process secret, as before.

Standard library only: a fresh process can derive the same scope without
importing the API.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import stat
import time
from typing import NamedTuple

logger = logging.getLogger("prompt_manager.api")

KEY_FILE_NAME = "workspace-scope.key"
SECRET_BYTES = 32
_HEX = re.compile(r"[0-9a-fA-F]{64}")
# Bounded read: a valid file is 64 hex characters and a newline.
_MAX_FILE_BYTES = 128
_ROOT_SCOPE_PURPOSE = b"prompt-manager/gallery-root-scope/v1"
_CURSOR_PURPOSE = b"prompt-manager/workspace-cursor/v1"
_SCOPE_TAG = "prompt-manager-gallery-root-v1"
_TEMP_PREFIX = f".{KEY_FILE_NAME}."
_TEMP_SUFFIX = ".tmp"
_STALE_TEMP_SECONDS = 60


class WorkspaceKeys(NamedTuple):
    """The derived keys; ``persisted`` is False for a per-process fallback."""

    root_scope: bytes
    cursor: bytes
    persisted: bool


def _read(path: str) -> bytes | None:
    """The secret in *path*, None when it is missing or not 64 hex characters.

    Raises OSError for anything but a missing file (a folder, no permission).
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read(_MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return None
    try:
        text = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return b""
    return bytes.fromhex(text) if _HEX.fullmatch(text) else b""


def _write_exclusive(path: str, secret: bytes) -> None:
    """Create *path* (it must not exist) holding *secret*, owner-only, fsynced."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write((secret.hex() + "\n").encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        _unlink(path)
        raise


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        # best effort cleanup; the next sweep removes a leftover temp
        pass


def _sweep_temps(directory: str) -> None:
    """Remove temp files a crashed first start left (older than a minute)."""
    try:
        names = os.listdir(directory)
    except OSError:
        return
    cutoff = time.time() - _STALE_TEMP_SECONDS
    for name in names:
        if name.startswith(_TEMP_PREFIX) and name.endswith(_TEMP_SUFFIX):
            path = os.path.join(directory, name)
            try:
                if os.stat(path).st_mtime < cutoff:
                    os.unlink(path)
            except OSError:
                # best effort: a later sweep removes what this one could not
                pass


def _tighten(path: str) -> None:
    """On POSIX an existing key readable by group or others becomes 0o600."""
    if os.name != "posix":
        return  # the file inherits the folder's ACL, as the database does
    try:
        if stat.S_IMODE(os.stat(path).st_mode) & 0o077:
            os.chmod(path, 0o600)
    except OSError:
        # best effort: the key still works with its old mode
        pass


def _store(directory: str, path: str, secret: bytes, *, replace: bool) -> None:
    """Publish *secret* at *path*; a new file never overwrites a concurrent winner.

    The secret is written to a temp file first. ``os.link`` then fails when
    *path* exists, so of two first starts exactly one file wins
    (FileExistsError for the other). A file system without hard links (FAT,
    some shares) creates *path* with ``O_EXCL`` instead. *replace* (a corrupt
    key) swaps the file with ``os.replace``; Windows refuses that while
    another process holds the file open, which the caller treats as unusable.
    """
    temp = os.path.join(
        directory, f"{_TEMP_PREFIX}{secrets.token_hex(8)}{_TEMP_SUFFIX}"
    )
    _write_exclusive(temp, secret)
    try:
        if replace:
            os.replace(temp, path)
            return
        try:
            os.link(temp, path)
        except FileExistsError:
            raise
        except OSError:
            # Not atomic: a second first start reading mid-write would see a
            # corrupt key. Rare (two first starts on FAT or a share at once);
            # each process still uses the file it reads back.
            _write_exclusive(path, secret)
    finally:
        _unlink(temp)


def load_install_secret(directory: str) -> bytes | None:
    """The persisted secret of *directory*, created on first use; None if unusable.

    After any write the file is read back and its content is what counts, so
    processes racing on a first start end with the one secret on disk.
    """
    path = os.path.join(directory, KEY_FILE_NAME)
    try:
        _sweep_temps(directory)
        current = _read(path)
        if current:
            _tighten(path)
            return current
        corrupt = current is not None
        try:
            _store(directory, path, secrets.token_bytes(SECRET_BYTES), replace=corrupt)
        except FileExistsError:
            pass  # another process created it first: its file counts
        stored = _read(path)
        if not stored:
            raise OSError(f"{KEY_FILE_NAME} is not readable after it was written")
        if corrupt:
            logger.warning(
                f"{KEY_FILE_NAME} was not a valid key and was replaced; "
                "open workspace pages must refresh once"
            )
        return stored
    except OSError as exc:
        logger.warning(f"Could not use {KEY_FILE_NAME}: {exc.__class__.__name__}")
        return None


def _derive(secret: bytes, purpose: bytes) -> bytes:
    return hmac.new(secret, purpose, hashlib.sha256).digest()


def workspace_keys_for(db_path: str | None) -> WorkspaceKeys:
    """The keys for the library whose database is *db_path* (blocking I/O)."""
    secret = None
    if isinstance(db_path, str) and db_path.strip() and db_path != ":memory:":
        secret = load_install_secret(os.path.dirname(os.path.abspath(db_path)))
    persisted = secret is not None
    if not persisted:
        logger.warning(
            "Workspace scopes use a per-process key: open workspace pages must "
            "refresh after a restart"
        )
        secret = secrets.token_bytes(SECRET_BYTES)
    return WorkspaceKeys(
        _derive(secret, _ROOT_SCOPE_PURPOSE),
        _derive(secret, _CURSOR_PURPOSE),
        persisted,
    )


def gallery_root_scope(keys: WorkspaceKeys, canonical_root: str, index: int) -> str:
    """The 64-hex scope of the root at *index* (its canonical path string)."""
    payload = json.dumps(
        [_SCOPE_TAG, index, canonical_root], separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hmac.new(keys.root_scope, payload, hashlib.sha256).hexdigest()
