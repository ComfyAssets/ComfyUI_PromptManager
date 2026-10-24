"""Reviewing duplicate image files: the checks and the plan (#249 S6, T17).

Pure helpers of ``py/api/duplicate_files.py``: the keep-list parsing, the
per-file check ``file_problem`` (containment decided as text before any
filesystem access, then a regular file of the found size and hash) and the
public ``duplicate_files`` plan.
"""

import hashlib
import json
import os
import stat as stat_module
from dataclasses import dataclass, field
from pathlib import Path

from .admin import DELETABLE_MEDIA_EXTENSIONS
from .maintenance import DUPLICATE_FILES

# Keep entries one review takes; with paths of a few hundred characters the
# list stays inside the preview body cap (DUPLICATE_PREVIEW_BODY_MAX, 1 MiB).
MAX_KEEP = 2000
MAX_DETAILS = 50
MAX_JOB_ID = 64
HASH_CHUNK = 1024 * 1024

# What ``file_problem`` finds, and how a plan counts it as protected.
OUTSIDE_SOURCE = "outside"
LINK = "link"
CHANGED_CONTENT = "changed"
GONE = "gone"
UNREADABLE = "unreadable"
KEEPER = "keeper"
SAME = "same"
MAYBE_SAME = "maybe-same"
NO_FILE_ID = "no-file-id"
# Two mounts of one share may round mtimes differently (SMB keeps 100 ns,
# FAT 2 s): within this, equal sizes on another device may be one file.
MOUNT_MTIME_SLACK_NS = 2_000_000_000
PROTECTED_REASONS = {
    MAYBE_SAME: (
        "file may be the same file reached through another mount or share",
        "files may be the same file reached through another mount or share",
    ),
    NO_FILE_ID: (
        "file: can't confirm this is a separate file on this drive",
        "files: can't confirm these are separate files on this drive",
    ),
    SAME: (
        "file is the same file as its kept copy",
        "files are the same file as their kept copy",
    ),
    OUTSIDE_SOURCE: (
        "file is not inside a configured source",
        "files are not inside a configured source",
    ),
    LINK: ("file is a link, not a regular file", "files are links, not regular files"),
    CHANGED_CONTENT: (
        "file changed since the search",
        "files changed since the search",
    ),
    GONE: ("file no longer exists", "files no longer exist"),
    UNREADABLE: ("file could not be read", "files could not be read"),
    KEEPER: (
        "file kept: its kept copy changed or is gone",
        "files kept: their kept copy changed or is gone",
    ),
}
# Job outcome rows (never an OS error text).
CHANGED = "The file changed since the review; it was kept."
KEEPER_CHANGED = "The kept copy changed or is gone; this file was kept."
SAME_FILE = "This is the same file as its kept copy; it was kept."
MAYBE_SAME_FILE = (
    "May be the same file reached through another mount or share; it was kept."
)
NO_FILE_ID_ROW = "Can't confirm this is a separate file on this drive; it was kept."
KEEPER_GONE = "keeper-gone"
KEEPER_WENT = (
    "The kept copy changed or went away during the removal; this file was kept "
    "and its links stay with it."
)
ROW_DETAILS = {
    KEEPER_GONE: None,  # filled below
    MAYBE_SAME: MAYBE_SAME_FILE,
    NO_FILE_ID: NO_FILE_ID_ROW,
    SAME: SAME_FILE,
    OUTSIDE_SOURCE: "The file is no longer inside its source; it was kept.",
    LINK: "The file is now a link, not a regular file; it was kept.",
    CHANGED_CONTENT: CHANGED,
    GONE: "The file was already gone.",
    UNREADABLE: "The file could not be read; it was kept.",
    KEEPER: KEEPER_CHANGED,
}
# An unlink fails only after the file's records moved to its kept copy.
LOCKED_FILE = (
    "The file is open in another program or read-only; it was not deleted, "
    "and its links now name the kept copy."
)
DELETE_FAILED = "The file could not be deleted; its links now name the kept copy."
NOT_ATTEMPTED = "Not attempted: the job stopped after an error."
KEPT_COPY = "Kept copy"
SOURCES_CHANGED = (
    "The sources changed since the review; nothing was deleted. Review again."
)
RECORDS_FAILED = (
    "The database could not be updated, so the file was not deleted. "
    "Its links and tags are unchanged."
)
FIND_FAILED = "The duplicate search failed."
FIND_ERROR_EVENT = "An internal error occurred. Check server logs for details."
FIND_CANCELLED = "Duplicate scan cancelled"


class SourcesChanged(Exception):
    """A source a review or removal names is no longer listed at its index."""


ROW_DETAILS[KEEPER_GONE] = KEEPER_WENT


def still_kept(target, hold):
    """Right before an unlink: the held kept copy is still the file at its
    path, and the file is still not the kept copy itself (``keeper_problem``).
    """
    try:
        if not os.path.samestat(os.fstat(hold.fileno()), os.stat(target["keeper_abs"])):
            return False
        info = os.lstat(target["abs"])
        problem = keeper_problem(
            target["abs"],
            target["root"],
            info,
            target["keeper_abs"],
            target["keeper_root"],
        )
        return problem is None
    except OSError:
        return False


def _plural(count, one, many=None):
    return f"{count:,} {one if count == 1 else (many or one + 's')}"


def human_bytes(size):
    """``10 B``, ``1.5 KB``, ``2.0 MB``…"""
    if size < 1024:
        return f"{size:,} B"
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024
        if size < 1024 or unit == "TB":
            return f"{size:,.1f} {unit}"
    return f"{size:,.1f} TB"  # pragma: no cover - unreachable


def _hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_problem(
    path,
    root,
    digest,
    size,
    hasher=None,
    *,
    hash_check=True,
    mtime_ns=None,
    inode=None,
    keeper=None,
    keeper_root=None,
):
    """Why ``path`` may not be treated as a copy in ``root``, or None (blocking).

    Containment is decided as text first, with no filesystem access: the
    normalized path must lie below the normalized root, outside
    ``thumbnails``, with a media extension. Only then is the file looked at:
    a regular file, not a link, whose resolved path is still inside the
    resolved root and that is not ``keeper`` itself (``keeper_problem``: a
    hardlink, one file reached through two spellings or two mounts, or a
    volume that cannot tell files apart), of ``size`` bytes.
    ``mtime_ns`` and ``inode`` (``[st_dev, st_ino]``), when given, bind it to
    what the search saw; ``hash_check`` compares its content hash with
    ``digest`` (the removal job, right before an unlink). Every handle is
    closed when this returns.
    """
    # py.config imports ComfyUI's server module: only when it is in use.
    from ..config import _canonical_path, _lexical_path, _lexically_inside

    lexical = _lexical_path(str(path))
    base = _lexical_path(str(root))
    if lexical == base or not _lexically_inside(lexical, [base]):
        return OUTSIDE_SOURCE
    parts = Path(os.path.relpath(lexical, base)).parts
    if "thumbnails" in parts or ".." in parts:
        return OUTSIDE_SOURCE
    if os.path.splitext(lexical)[1].lower() not in DELETABLE_MEDIA_EXTENSIONS:
        return OUTSIDE_SOURCE
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return GONE
    except OSError:
        return UNREADABLE
    if stat_module.S_ISLNK(info.st_mode):
        return LINK
    if not stat_module.S_ISREG(info.st_mode):
        return LINK
    try:
        if not _lexically_inside(_canonical_path(path), [_canonical_path(root)]):
            return OUTSIDE_SOURCE  # a linked folder on the way
        if keeper is not None:
            same = keeper_problem(path, root, info, keeper, keeper_root)
            if same:
                return same
        if info.st_size != size:
            return CHANGED_CONTENT
        if mtime_ns is not None and info.st_mtime_ns != mtime_ns:
            return CHANGED_CONTENT
        if inode is not None and [info.st_dev, info.st_ino] != list(inode):
            return CHANGED_CONTENT
        if hash_check and (hasher or _hash_file)(path) != digest:
            return CHANGED_CONTENT
    except FileNotFoundError:
        return GONE
    except OSError:
        return UNREADABLE
    return None


def keeper_problem(path, root, info, keeper, keeper_root=None):
    """Why ``path`` (its ``lstat`` is ``info``) may be ``keeper`` itself, or None.

    - ``NO_FILE_ID``: a volume reports no file id (``st_ino`` 0), so the two
      cannot be told apart;
    - ``SAME``: ``os.path.samestat`` (a hardlink, or one file reached through
      two spellings of one mount);
    - ``MAYBE_SAME``: another device, the same size, mtimes within
      ``MOUNT_MTIME_SLACK_NS``, and the same ``st_ino`` (NFS and SMB report
      the server's file id) or the same path within their sources, or one
      ending with the other on a folder name (one share mounted twice, a
      FUSE pool over a listed disk, sources listed at different depths).
      Protect, never guess: a true copy on another disk at the same relative
      path is kept too.

    Raises OSError when the kept copy cannot be read (the caller's GONE or
    UNREADABLE).
    """
    kept = os.stat(keeper)
    if not info.st_ino or not kept.st_ino:
        return NO_FILE_ID
    if os.path.samestat(info, kept):
        return SAME
    if info.st_dev == kept.st_dev or info.st_size != kept.st_size:
        return None
    if abs(info.st_mtime_ns - kept.st_mtime_ns) > MOUNT_MTIME_SLACK_NS:
        return None
    if info.st_ino == kept.st_ino:
        return MAYBE_SAME
    if keeper_root is not None and _same_tail(
        _relative_key(path, root), _relative_key(keeper, keeper_root)
    ):
        return MAYBE_SAME
    return None


def _same_tail(one, other):
    """Whether one relative path is the other, or ends with it on a folder name.

    Two mounts of one tree may be listed at different depths (``2024/x.png``
    in one source, ``x.png`` in the other); ``ax.png`` never matches ``x.png``.
    """
    if one is None or other is None:
        return False
    return one == other or one.endswith("/" + other) or other.endswith("/" + one)


def _relative_key(path, root):
    try:
        relative = os.path.relpath(str(path), str(root))
    except ValueError:  # another drive (Windows)
        return None
    return os.path.normcase(relative).replace("\\", "/")


@dataclass
class HeldFind:
    """One find: where it looked and, once it finished, what it found."""

    origin: str  # "workspace" or "gallery"
    job_id: str = ""
    roots: list = field(default_factory=list)  # [(source index or None, root)]
    legacy_dir: str = None
    groups: list = None  # [{group_id, hash, size, files:[record]}]

    def covers(self, legacy_dir):
        """Whether its events answer the legacy gallery for ``legacy_dir``."""
        if self.origin == "gallery":
            return True
        if not legacy_dir:
            return False
        wanted = _identity(legacy_dir)
        return any(_identity(root) == wanted for _index, root in self.roots)


def _identity(path):
    return os.path.normcase(os.path.realpath(str(path)))


def _group_order(group):
    return (-(len(group["files"]) - 1) * group["size"], group["hash"])


def _keep_entry(entry):
    """``((group_id, root_index, path), None)`` or ``(None, message)``."""
    if not isinstance(entry, dict) or set(entry) != {"group_id", "root_index", "path"}:
        return None, "Each keep entry is {group_id, root_index, path}"
    group_id, index, path = entry["group_id"], entry["root_index"], entry["path"]
    if not isinstance(group_id, str) or not 0 < len(group_id) <= 128:
        return None, "group_id must be a group's id"
    if index is not None and (
        isinstance(index, bool) or not isinstance(index, int) or index < 0
    ):
        return None, "root_index must be a source index"
    if not isinstance(path, str) or not 0 < len(path) <= 4096:
        return None, "path must be a file's path in its group"
    return (group_id, index, path), None


def _parse_options(options):
    """``(job_id, keep, None)`` for duplicate_files options, else a message."""
    if not isinstance(options, dict) or set(options) != {"job_id", "keep"}:
        return None, None, "duplicate_files takes options {job_id, keep}"
    job_id, keep = options["job_id"], options["keep"]
    if not isinstance(job_id, str) or not 0 < len(job_id) <= MAX_JOB_ID:
        return None, None, "job_id must be a duplicate search's job id"
    if not isinstance(keep, list) or not 0 < len(keep) <= MAX_KEEP:
        return None, None, f"keep must list 1 to {MAX_KEEP:,} files to keep"
    parsed = []
    for entry in keep:
        key, message = _keep_entry(entry)
        if message:
            return None, None, message
        parsed.append(key)
    return job_id, parsed, None


def _select(held, keep):
    """``({group_id: (group, [keeper records])}, None)`` or ``(None, message)``."""
    groups = {group["group_id"]: group for group in held.groups}
    selection = {}
    for group_id, index, path in keep:
        group = groups.get(group_id)
        if group is None:
            return None, "A kept file names a group this search did not find"
        match = [
            record
            for record in group["files"]
            if record["root_index"] == index and record["rel"] == path
        ]
        if not match:
            return None, "A kept file is not in its group"
        _group, keepers = selection.setdefault(group_id, (group, []))
        if match[0] not in keepers:
            keepers.append(match[0])
    return selection, None


SOURCES_CHANGED_REVIEW = (
    "The sources changed since the search. Search for duplicates again."
)


def _fingerprint(targets):
    digest = hashlib.sha256()
    for target in sorted(targets, key=lambda t: _identity(t["abs"])):
        part = [
            os.path.normcase(target["abs"]),
            target["hash"],
            target["size"],
            os.path.normcase(target["keeper_abs"]),
        ]
        digest.update(json.dumps(part).encode("utf-8") + b"\n")
    return "sha256:" + digest.hexdigest()


def _record_parts(records, target):
    """What moves with one removal: its links, tags and Needs review items."""
    found = records.get(target["abs"], {})
    links, tags, review = (found.get(key, 0) for key in ("links", "tags", "review"))
    parts = [
        (
            (
                f"{_plural(links, 'database link')} "
                f"{'moves' if links == 1 else 'move'} to the kept copy"
            )
            if links
            else "no database links"
        )
    ]
    if tags:
        parts.append("its image tags join the kept copy's")
    if review:
        item = "Needs review item is" if review == 1 else "Needs review items are"
        parts.append(f"{review:,} {item} settled")
    return parts


def _public_plan(targets, protected, records):
    count = len(targets)
    groups = len({t["group_id"] for t in targets})
    size = sum(t["size"] for t in targets)
    reasons = [
        f"{n:,} {PROTECTED_REASONS[code][0 if n == 1 else 1]}"
        for code, n in sorted(protected.items())
    ]
    details = [
        {
            "label": target["rel"],
            "detail": f"{target['root_label']} · a copy of {target['keeper_rel']}"
            + "".join(f" · {part}" for part in _record_parts(records, target)),
        }
        for target in targets[:MAX_DETAILS]
    ]
    label = f"{_plural(count, 'duplicate file')} in {_plural(groups, 'group')}"
    media = (
        f"Deletes {_plural(count, 'file')} ({human_bytes(size)}) from disk; the "
        "kept copies stay. The safety backup covers the database, not these files."
    )
    tagged = sum(1 for t in targets if records.get(t["abs"], {}).get("tags"))
    review = sum(records.get(t["abs"], {}).get("review", 0) for t in targets)
    if tagged:
        media += (
            f" The image tags of {_plural(tagged, 'removed copy', 'removed copies')}"
            " join their kept copy's."
        )
    if review:
        media += (
            f" {_plural(review, 'pending Needs review item')} of the removed "
            f"{'copy is' if review == 1 else 'copies are'} settled."
        )
    return {
        "operation": DUPLICATE_FILES,
        "scope": {"count": count, "label": label},
        "protected": {"count": sum(protected.values()), "reasons": reasons},
        "media": {"deletes_files": True, "label": media},
        "details": details,
    }
