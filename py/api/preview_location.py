"""The thumbnail location setting (#320): parsing, validation, source keys
and the thumbnail folder's marker.

``preview_store`` holds the runtime state and maps paths; this module holds
the rules it applies. Nothing here keeps state.

Schema (config.json ``thumbnails.location``)::

    {"mode": "folder", "path": "D:\\\\Thumbs", "id": "<uuid4>",
     "install": "<uuid4>", "cleanup": []}

The default (and a missing key) is ``{"mode": "source"}``: previews inside
each source. An invalid value never falls back to the sources: it parses as
a folder location with problem ``invalid``, so nothing is written anywhere.

Layout in folder mode: ``<folder>/<source key>/<rel dir>/<stem>_thumb<ext>``.
A source key is a readable slug plus a short hash of the source's identity
(``output-3f9a2c1b``), assigned once and stored in the marker
``.promptmanager-thumbnails.json`` with the identity it belongs to.
"""

import errno
import hashlib
import json
import logging
import ntpath
import os
import posixpath
import re
import stat as stat_module
import threading
import unicodedata
import uuid
from dataclasses import dataclass, field

try:
    from ...utils import library_location
except ImportError:  # tests import the package top-level
    from utils import library_location

logger = logging.getLogger("prompt_manager.thumbnails")

SOURCE_MODE = "source"
FOLDER_MODE = "folder"
MARKER_NAME = ".promptmanager-thumbnails.json"
MARKER_VERSION = 1
# A source key: one folder name, a slug and a 8-16 hex hash (``source_key``).
KEY_PATTERN = re.compile(r"^[a-z0-9_-]{1,24}-[0-9a-f]{8,16}$")
# A marker is a few keys; anything larger is not ours.
MAX_MARKER_BYTES = 4 * 2**20
# OS metadata files a folder may hold and still count as empty.
OS_METADATA = frozenset({"thumbs.db", "desktop.ini", ".ds_store"})
KEY_HASH_LENGTHS = (8, 12, 16)
SLUG_LENGTH = 24
# Folder + the longest key (24 + 1 + 16) + this margin must fit (Windows).
WINDOWS_FOLDER_LIMIT = 247
LONGEST_KEY = SLUG_LENGTH + 1 + KEY_HASH_LENGTHS[-1]
PATH_MARGIN = 40
# A validation stat on a share that does not answer in time is unavailable.
VALIDATION_TIMEOUT = 5.0
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.IGNORECASE)
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')

# Problem codes, their HTTP status and the text the UI shows (no path).
MESSAGES = {
    "location-not-absolute": (
        400,
        "Enter a full folder path, such as D:\\Thumbnails or /mnt/fast/thumbnails.",
    ),
    "location-device-path": (
        400,
        "Enter the folder as a drive letter or \\\\server\\share path, "
        "without \\\\?\\.",
    ),
    "location-invalid-name": (
        400,
        "This folder name can't be used on Windows. Choose another name.",
    ),
    "location-too-broad": (
        400,
        "Choose a folder inside a drive or share, not the drive itself.",
    ),
    "location-served": (
        400,
        "Choose a folder outside your image sources and ComfyUI's output, input "
        "and temp folders.",
    ),
    "location-node-folder": (
        400,
        "Choose a folder outside the Prompt Manager node folder.",
    ),
    "location-contains-source": (
        400,
        "This folder contains an image source. Choose a folder beside it instead.",
    ),
    "location-same": (400, "Thumbnails are already stored here."),
    "location-parent-missing": (
        400,
        "The folder that would hold it doesn't exist. Check the path and that its "
        "drive is connected.",
    ),
    "location-unavailable": (
        409,
        "This folder can't be reached right now. Check that its drive or share is "
        "connected.",
    ),
    "location-not-empty": (
        409,
        "This folder already holds other files. Choose an empty folder or one "
        "Prompt Manager used before.",
    ),
    "location-not-writable": (
        409,
        "Prompt Manager can't write to this folder. Check its permissions.",
    ),
    "location-path-too-long": (
        400,
        "This path is too long for Windows. Choose a shorter folder path.",
    ),
    "location-needs-move": (
        409,
        "Use Move thumbnails in System › Sources & scans.",
    ),
}
HOLDS_THUMBNAILS_REASON = "This folder holds Prompt Manager's thumbnails."


def message(code):
    """``(status, text)`` of a problem code."""
    return MESSAGES[code]


# ── parsing ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ParsedLocation:
    """A ``thumbnails.location`` value, checked as data (no filesystem)."""

    mode: str = SOURCE_MODE
    path: str | None = None
    id: str | None = None
    install: str | None = None
    cleanup: tuple = field(default_factory=tuple)
    problem: str | None = None

    def to_config(self):
        """The config.json form (``invalid`` values are never written)."""
        if self.mode == SOURCE_MODE:
            value = {"mode": SOURCE_MODE}
        else:
            value = {"mode": FOLDER_MODE, "path": self.path, "id": self.id}
        if self.install:
            value["install"] = self.install
        value["cleanup"] = list(self.cleanup)
        return value


def _cleanup(value):
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def parse_location(value, *, flavor=os.path):
    """The location a ``thumbnails.location`` value names.

    Missing: inside each source. Anything else that is not a well-formed
    location (an unknown mode, a relative path, a missing id) becomes a
    folder location with problem ``invalid``: never a silent fall-back.
    """
    if value is None:
        return ParsedLocation()
    if not isinstance(value, dict):
        return ParsedLocation(mode=FOLDER_MODE, problem="invalid")
    mode = value.get("mode", SOURCE_MODE)
    install = value.get("install") if isinstance(value.get("install"), str) else None
    cleanup = _cleanup(value.get("cleanup"))
    if mode == SOURCE_MODE:
        return ParsedLocation(install=install, cleanup=cleanup)
    if mode != FOLDER_MODE:
        return ParsedLocation(mode=FOLDER_MODE, problem="invalid", cleanup=cleanup)
    path, marker_id = value.get("path"), value.get("id")
    if (
        not isinstance(path, str)
        or form_problem(path, flavor=flavor) is not None
        or not isinstance(marker_id, str)
        or not marker_id.strip()
    ):
        return ParsedLocation(
            mode=FOLDER_MODE,
            path=path if isinstance(path, str) else None,
            problem="invalid",
            cleanup=cleanup,
        )
    return ParsedLocation(
        mode=FOLDER_MODE,
        path=flavor.abspath(path),
        id=marker_id,
        install=install,
        cleanup=cleanup,
    )


# ── form (text only) ─────────────────────────────────────────────────


def _is_windows(flavor):
    return flavor is ntpath or getattr(flavor, "__name__", "") == "ntpath"


def form_problem(path, *, flavor=os.path):
    """The text-only problem of *path*, or None (no filesystem access).

    ``\\\\?\\`` and ``\\\\.\\`` prefixes, relative, drive-relative (``C:x``)
    and root-relative (``\\x``) spellings, and names Windows cannot hold
    (checked on every OS: the folder may be moved to Windows).
    """
    if not isinstance(path, str) or not path.strip() or path != path.lstrip():
        return "location-not-absolute"
    if path.replace("/", "\\").startswith(("\\\\?\\", "\\\\.\\")):
        return "location-device-path"
    if _is_windows(flavor):
        drive, rest = ntpath.splitdrive(path)
        if not drive or not rest.startswith(("\\", "/")):
            return "location-not-absolute"
    elif not posixpath.isabs(path):
        return "location-not-absolute"
    drive, rest = flavor.splitdrive(path)
    for part in re.split(r"[\\/]+", rest):
        if not part:
            continue
        if (
            _RESERVED.match(part)
            or part.endswith((".", " "))
            or _BAD_CHARS.search(part)
        ):
            return "location-invalid-name"
    return None


def is_too_broad(path, *, home=None, flavor=os.path):
    """A filesystem or share root, or the home folder itself."""
    normalized = flavor.normpath(path)
    drive, rest = flavor.splitdrive(normalized)
    if rest in ("", "\\", "/"):
        return True
    home = home if home is not None else os.path.expanduser("~")
    return bool(home) and flavor.normcase(normalized) == flavor.normcase(
        flavor.normpath(home)
    )


def too_long_for_windows(path, long_paths=None):
    """Folder + the longest key + a margin over the folder limit (Windows
    without long paths; *long_paths* injects the registry answer)."""
    if long_paths is None:
        long_paths = long_paths_enabled()
    if long_paths:
        return False
    return len(path) + 1 + LONGEST_KEY + PATH_MARGIN > WINDOWS_FOLDER_LIMIT


def long_paths_enabled():
    """``LongPathsEnabled`` on Windows (True on other systems)."""
    if os.name != "nt":
        return True
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
        ) as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


# ── links ────────────────────────────────────────────────────────────


def is_link_like(path):
    """A symlink, a junction or any reparse point (one lstat), or missing.

    Raises OSError other than "not found" (an unreachable share).
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat_module.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    is_junction = getattr(os.path, "isjunction", None)
    return bool(is_junction and is_junction(path))


# ── source keys ──────────────────────────────────────────────────────


def slug(name):
    """``NFKD`` to ASCII, lower case, runs outside ``[a-z0-9_]`` as ``-``,
    trimmed and cut to 24 characters; empty becomes ``source``."""
    text = unicodedata.normalize("NFKD", str(name))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9_]+", "-", text).strip("-")
    text = text[:SLUG_LENGTH].strip("-")
    return text or "source"


def source_key(spelling, identity, taken):
    """The key of a source: ``slug(basename) + "-" + sha256(identity)[:n]``.

    *taken* maps existing keys to their identity; a key taken by another
    identity grows its hash to 12, then 16 characters.
    """
    base = slug(ntpath.basename(str(spelling).rstrip("\\/")))
    digest = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()
    for length in KEY_HASH_LENGTHS:
        key = f"{base}-{digest[:length]}"
        owner = taken.get(key)
        if owner is None or owner == identity:
            return key
    raise ValueError("no free source key")


def file_identity(path):
    """``[st_dev, st_ino]`` of an existing folder, or None (no file ids)."""
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        return None
    if not info.st_ino:
        return None
    return [info.st_dev, info.st_ino]


def find_key(keys, spelling, identity, dev_ino):
    """The marker key of a source: the same configured spelling, then the
    same identity text, then the same file id; or None."""
    keys = {k: v for k, v in keys.items() if valid_key(k) and isinstance(v, dict)}
    for key, entry in keys.items():
        if entry.get("path") == spelling:
            return key
    for key, entry in keys.items():
        if entry.get("identity") == identity:
            return key
    if dev_ino:
        for key, entry in keys.items():
            if entry.get("dev_ino") == list(dev_ino):
                return key
    return None


# ── the marker ───────────────────────────────────────────────────────


def marker_path(folder):
    return os.path.join(folder, MARKER_NAME)


def read_marker(folder):
    """The marker in *folder* as a dict, or None (absent or unreadable as
    ours). OSError other than "not found" propagates (an unreachable share)."""
    try:
        with open(marker_path(folder), "r", encoding="utf-8") as handle:
            text = handle.read(MAX_MARKER_BYTES + 1)
    except FileNotFoundError:
        return None
    except (ValueError, UnicodeDecodeError):
        return None
    if len(text) > MAX_MARKER_BYTES:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        return None
    data["keys"] = valid_keys(data.get("keys"))
    return data


def valid_key(key):
    """Whether *key* can name a source's folder: one folder name of the
    ``source_key`` form, never a separator, ``..`` or a drive."""
    return isinstance(key, str) and KEY_PATTERN.fullmatch(key) is not None


def valid_keys(keys):
    """The marker's keys that are safe to join onto the folder; a hand-edited
    or foreign entry that is not is dropped (and logged), never followed."""
    if not isinstance(keys, dict):
        return {}
    kept = {}
    for key, entry in keys.items():
        if valid_key(key) and isinstance(entry, dict):
            kept[key] = entry
        else:
            logger.warning(
                "Ignoring a thumbnail folder marker entry that is not a source " "key"
            )
    return kept


def write_marker(folder, mutate):
    """Re-read the marker, apply ``mutate(marker)`` and write it atomically
    (a temp sibling renamed over it). Callers hold ``preview_store.write_lock``.
    Returns the marker written."""
    current = read_marker(folder) or {}
    marker = {
        "version": MARKER_VERSION,
        "id": current.get("id"),
        "created_by_pm": bool(current.get("created_by_pm", False)),
        "keys": dict(current.get("keys") or {}),
    }
    mutate(marker)
    if not marker.get("id"):
        raise ValueError("a marker needs an id")
    target = marker_path(folder)
    temp = os.path.join(
        folder, f".{MARKER_NAME}.{os.getpid()}-{threading.get_ident()}.tmp"
    )
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(marker, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        try:
            os.unlink(temp)
        except OSError:
            pass  # gone with the rename; a leftover is swept with the temps
    return marker


def new_id():
    return str(uuid.uuid4())


def init_folder(folder, *, created_by_pm=False):
    """The marker id of *folder*: an existing marker is adopted; a folder
    without one gets a new marker (new id, no keys)."""
    existing = read_marker(folder)
    if existing is not None:
        return existing["id"]

    def create(marker):
        if not marker.get("id"):
            marker["id"] = new_id()
            marker["created_by_pm"] = created_by_pm

    return write_marker(folder, create)["id"]


def folder_state(path, expected_id):
    """``(available, problem, marker)`` of a configured thumbnail folder.

    Available: the folder exists, is a directory and not a link, junction or
    reparse point, and holds our marker with *expected_id*. Raises OSError
    when the folder cannot be reached (an I/O error other than not found).
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False, "missing", None
    except OSError as error:
        if error.errno in (errno.ENOENT, errno.ENOTDIR):
            return False, "missing", None
        raise
    if is_link_like(path):
        return False, "invalid", None
    if not stat_module.S_ISDIR(info.st_mode):
        return False, "invalid", None
    marker = read_marker(path)
    if marker is None:
        return False, "missing", None
    if marker["id"] != expected_id:
        return False, "marker-mismatch", marker
    return True, None, marker


def holds_only_metadata(path):
    """Whether an existing folder is empty but for OS metadata files."""
    with os.scandir(path) as entries:
        for entry in entries:
            name = entry.name.lower()
            if name in OS_METADATA or name.startswith("._"):
                continue
            return False
    return True


# ── validation ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Context:
    """What a location is checked against (gathered by the caller)."""

    roots: tuple = ()  # every image source of every root list
    contained: tuple = ()  # ComfyUI output/input/temp and the library folder
    node_folder: str | None = None
    default_path: str | None = None
    current_path: str | None = None  # the folder in use (folder mode)
    home: str | None = None
    flavor: object = os.path
    long_paths: bool | None = None
    environ: object = None
    # ComfyUI's folder_paths module (None: none; unset: imported).
    folder_paths: object = library_location._UNSET


def _timed(func, timeout):
    """``func()`` on a daemon thread; raises TimeoutError past *timeout*.
    A stuck call is left to finish on its own thread."""
    result = {}
    done = threading.Event()

    def run():
        try:
            result["value"] = func()
        except BaseException as error:  # handed to the caller
            result["error"] = error
        finally:
            done.set()

    threading.Thread(target=run, name="pm-preview-stat", daemon=True).start()
    if not done.wait(timeout):
        raise TimeoutError("the folder did not answer in time")
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _inside(path, folders):
    return library_location._inside_any(path, folders)


def _same(first, second):
    return library_location.same_folder(first, second) or os.path.normcase(
        os.path.realpath(first)
    ) == os.path.normcase(os.path.realpath(second))


def _served(path, context):
    environ = os.environ if context.environ is None else context.environ
    return (
        library_location.data_dir_problem(
            path,
            folder_paths=context.folder_paths,
            environ=environ,
            gallery_roots=list(context.roots),
        )
        == "data-dir-served"
    )


def _probe_write(path):
    """Create and delete a probe file in *path*; False when refused."""
    probe = os.path.join(path, f".pm-probe-{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(probe, "wb") as handle:
            handle.write(b"")
    except OSError:
        return False
    try:
        os.unlink(probe)
    except OSError:
        return False
    return True


def location_problem(path, *, purpose, context, timeout=VALIDATION_TIMEOUT):
    """Why *path* cannot be the thumbnail folder, as a code, or None.

    *purpose*: ``move`` (every rule), ``startup`` (no ``same``, ``empty``,
    ``writable`` or parent rules: availability covers them) or ``recreate``
    (the folder is missing and is made again).
    """
    flavor = context.flavor
    code = form_problem(path, flavor=flavor)
    if code is not None:
        return code
    path = flavor.abspath(path)
    default = bool(context.default_path) and flavor.normcase(path) == flavor.normcase(
        flavor.abspath(context.default_path)
    )
    if is_too_broad(path, home=context.home, flavor=flavor):
        return "location-too-broad"
    try:
        return _timed(
            lambda: _filesystem_problem(path, purpose, context, default), timeout
        )
    except (TimeoutError, OSError):
        return "location-unavailable"


def _filesystem_problem(path, purpose, context, default):
    if _served(path, context):
        return "location-served"
    if context.node_folder and not default and _inside(path, [context.node_folder]):
        return "location-node-folder"
    inner = list(context.roots) + list(context.contained)
    if any(folder and _inside(folder, [path]) for folder in inner):
        if not default:
            return "location-contains-source"
    if (
        purpose == "move"
        and context.current_path
        and (
            os.path.normcase(path) == os.path.normcase(context.current_path)
            or (os.path.isdir(path) and _same(path, context.current_path))
        )
    ):
        return "location-same"
    # Windows, or a test injecting its limit (``long_paths=False``).
    if os.name == "nt" or context.long_paths is False:
        if too_long_for_windows(path, context.long_paths):
            return "location-path-too-long"
    if purpose == "startup":
        return None
    if is_link_like(path):
        # Never through a link: the folder itself must be a real folder.
        return "location-not-empty"
    exists = os.path.isdir(path)
    if not exists:
        if os.path.lexists(path):
            return "location-not-empty"
        if not default and not os.path.isdir(os.path.dirname(path)):
            return "location-parent-missing"
        return None
    if read_marker(path) is None and not holds_only_metadata(path):
        return "location-not-empty"
    if not _probe_write(path):
        return "location-not-writable"
    return None
