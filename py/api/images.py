"""Image and gallery API routes for PromptManager."""

import asyncio
import base64
import binascii
import functools
import hashlib
import hmac
import io
import json
import os
import re
import stat
import subprocess  # nosec B404 - ffmpeg frames through run_encoder, fixed argv
import sys
import time as _time
import urllib.parse
from bisect import bisect_left, bisect_right, insort
from collections.abc import Mapping
from pathlib import Path
from typing import Callable, Iterable, NamedTuple

from aiohttp import web
from PIL import Image, UnidentifiedImageError

from .jobs import MAX_OUTCOME_ROWS, THUMBNAILS_JOB_KIND
from . import gallery_listing
from .gallery_listing import (
    DISCARDED,
    FAILED,
    MAX_DISCARDED_WALKS,
    WALK_STOPPED,
    WalkOutcome,
    walk_root,
)
from . import thumbnail_settings
from .thumbnail_encode import (
    PREVIEW_HELD,
    EncoderClosed,
    PreviewWriteHeld,
    replace_unless_held,
    run_encoder,
    sweep_stray_temps,
    temp_sibling,
    thumbnail_frame,
    writer_settings,
    write_thumbnail,
)
from .prompts import bad_request, parse_page_params, publish_image_paths
from .prompts import thumbnail_url_for
from .prompts import safe_error_message as _safe_error
from .inspection_service import (
    CurrentFileChanged,
    IMAGE_EXTENSIONS as _IMAGE_EXTENSIONS,
    SERVABLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    inspect_current_file,
)

try:
    from ...database.image_collections import CollectionMissing
    from ...database.image_favorites import MAX_KEY_LENGTH
    from ...database.library import display_title
    from ...database.models import normalize_image_path
    from ...utils.card_seed import card_seed, header_params, parsed_positive
    from ...utils.parallel import map_parallel
    from ...utils.json_strict import dumps_strict
    from ...utils.inspection_response import (
        encode_inspection_response,
        library_metadata,
    )
    from ...utils.prompt_inspector import MetadataReadError, read_inspection_file
    from ...utils.file_reveal import (
        OpenerUnavailable,
        RevealGate,
        RevealTargetChanged,
        is_local_request,
        reveal_executor,
        reveal_in_file_manager,
    )
except ImportError:
    from database.image_collections import CollectionMissing
    from database.image_favorites import MAX_KEY_LENGTH
    from database.library import display_title
    from database.models import normalize_image_path
    from utils.card_seed import card_seed, header_params, parsed_positive
    from utils.parallel import map_parallel
    from utils.json_strict import dumps_strict
    from utils.inspection_response import (
        encode_inspection_response,
        library_metadata,
    )
    from utils.prompt_inspector import MetadataReadError, read_inspection_file
    from utils.file_reveal import (
        OpenerUnavailable,
        RevealGate,
        RevealTargetChanged,
        is_local_request,
        reveal_executor,
        reveal_in_file_manager,
    )


IMAGE_EXTENSIONS = _IMAGE_EXTENSIONS
# The header records the card parses (see utils.card_seed.header_params).
_CARD_RECORDS = ("prompt", "workflow")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_LISTING_FAILED = "The image list could not be filtered. Check server logs for details."
_COLLECTION_ID = re.compile(r"[1-9][0-9]{0,8}")
COLLECTION_NOT_FOUND = "That collection no longer exists."


def collection_not_found() -> web.Response:
    """The 404 a write or a listing answers for an unknown collection (#331)."""
    return web.json_response(
        {
            "success": False,
            "code": "collection-not-found",
            "error": COLLECTION_NOT_FOUND,
        },
        status=404,
        headers={"Cache-Control": "no-store"},
    )


def _card_header(path: Path) -> tuple:
    """``(width, height, records)`` of one image card, every handle closed.

    A PNG's records come from the inspector's own reader
    (``read_inspection_file``: text after IDAT, the shared byte budgets, and
    the refusal of conflicting duplicate prompt or workflow chunks), so the
    card reads exactly what the inspector reads. When that reader refuses the
    file, the card keeps its dimensions and gets no records. Other formats
    keep Pillow's header ``info``, as the inspector's header read does.
    Raises OSError or ValueError for a file that cannot be read at all.
    """
    with path.open("rb") as stream:
        if stream.read(len(_PNG_SIGNATURE)) != _PNG_SIGNATURE:
            stream.seek(0)
            with Image.open(stream) as source:
                width, height = source.size
                return width, height, {k: source.info.get(k) for k in _CARD_RECORDS}
    try:
        inspection = read_inspection_file(path)
    except MetadataReadError:
        with path.open("rb") as stream, Image.open(stream) as source:
            width, height = source.size
        return width, height, None
    records = {k: inspection.metadata.get(k) for k in _CARD_RECORDS}
    return inspection.width, inspection.height, records


# The legacy gallery page's thumbnail generation, as Activity lists it (D80).
THUMBNAIL_GENERATION_TITLE = "Thumbnail generation (gallery page)"
# Its clear: automatic previews are not written while it runs.
THUMBNAIL_CLEAR_TITLE = "Thumbnail clear (gallery page)"


class _CardFacts(NamedTuple):
    """One image file's card facts, derived from its header alone.

    Cached per gallery scan by ``(path, mtime)`` (see ``_card_facts``); links
    and favorites are read fresh on every page.
    """

    width: int | None
    height: int | None
    seed: str | None
    positive_title: str | None  # _positive_title of the parsed positive


_NO_CARD_FACTS = _CardFacts(None, None, None, None)


def _unavailable_link() -> dict:
    """A card link summary when the database could not be read."""
    return {
        "status": "unavailable",
        "positive_id": None,
        "title": None,
        "title_source": None,
        "category": None,
        "rating": None,
    }


def _positive_title(positive: str | None) -> str | None:
    """The display title of a file's parsed positive (None without one)."""
    return None if positive is None else display_title((), positive)


def _verified_title(summary: dict | None, positive_title: str | None) -> str | None:
    """A linked file's Library title, withholding unconfirmed saved prompt text.

    The one rule for the card and the inspector. A stored title is user data and
    always shows. A title taken from the saved positive's first line shows only
    when ``positive_title`` (``_positive_title`` of the file's own positive as
    the shared parser reads it from the file's records) is the same title: a
    saved association never replaces file evidence.
    """
    if not isinstance(summary, dict) or summary.get("status") != "linked":
        return None
    title = summary.get("title")
    if summary.get("title_source") != "text":
        return title
    confirmed = positive_title is not None and positive_title == title
    return title if confirmed else None


def _card_link(summary: dict, positive_title: str | None) -> dict:
    """The card's link summary with an unconfirmed first-line title withheld."""
    if summary.get("title_source") != "text":
        return summary
    return {**summary, "title": _verified_title(summary, positive_title)}


def _inspected_positive(observed: dict) -> str | None:
    """The positive the shared parser reads from the inspected file's records.

    Videos give None, as on the card, which reads no video metadata.
    """
    inspection = observed.get("inspection")
    if inspection is None or observed["file"]["media_type"] == "video":
        return None
    return parsed_positive(header_params(inspection.metadata))


def _public_path(path):
    """Response-safe form of *path* (relative to the ComfyUI tree).

    Imported lazily: the package defines it after importing this module.
    """
    from . import _public_path as public_path

    return public_path(path)


def _canonical(path):
    """Resolve symlinks and normalise case so paths compare on every OS."""
    return Path(os.path.normcase(os.path.realpath(str(path))))


def _is_within(path, root):
    """True when *path* (after realpath) lives under *root* (after realpath)."""
    return _canonical(path).is_relative_to(_canonical(root))


def _has_traversal(relative):
    """True for absolute paths, drive-qualified paths or any ``..`` segment."""
    if not relative or os.path.isabs(relative) or os.path.splitdrive(relative)[0]:
        return True
    if relative[0] in ("/", "\\"):
        return True
    return ".." in re.split(r"[\\/]+", relative)


def _is_thumbnail_cache_path(relative):
    """True when *relative* names something in a root's ``thumbnails/``
    cache (any case, either separator): never inspected as a media file."""
    first = re.split(r"[\\/]+", relative.lstrip("\\/"), maxsplit=1)[0]
    return first.casefold() == "thumbnails"


def _forbidden(message="Access denied"):
    return web.json_response({"success": False, "error": message}, status=403)


def _media_access_error(path, allowed_dirs):
    """Return a 403 response when *path* must not be served, else None.

    Fails closed: an empty *allowed_dirs* denies everything.
    """
    if Path(path).suffix.lower() not in SERVABLE_EXTENSIONS:
        return _forbidden("Only media files can be served")
    if not allowed_dirs:
        return _forbidden("No allowed image directories configured")
    if not any(_is_within(path, root) for root in allowed_dirs):
        return _forbidden()
    return None


# Media types the gallery scans and thumbnails (a superset is never served).
GALLERY_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
MEDIA_EXTENSIONS = GALLERY_IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# Caps on request-triggered filesystem work.
MAX_SCAN_DEPTH = 12
MAX_THUMBNAIL_FILES = 10_000
# Already-fresh files a thumbnail job lists under Skipped (its counter stays exact).
MAX_FRESH_LISTED = MAX_OUTCOME_ROWS


def _worker_threads():
    """Configured thread count for thumbnail work.

    Imported lazily because py.config imports this package.
    """
    from ..config import PromptManagerConfig

    return PromptManagerConfig.WORKER_THREADS


def _thumbnail_batch_size(workers):
    """Files per executor job: enough to keep every worker thread busy."""
    return max(4, workers * 2)


# Preview files are written by thumbnail_encode in the configured format
# (thumbnail_settings, #317); these names stay importable from here.
_temp_sibling = temp_sibling
_replace_unless_held = replace_unless_held

# A preview that could not be replaced (#249 S5 M6). Windows refuses to rename
# over a file another process has open (WinError 5 or 32), such as a preview
# the gallery is serving; the old preview stays and the file counts as an error.
PREVIEW_IN_USE = "Preview in use by another program; the old one was kept"
PREVIEW_NOT_WRITABLE = "Permission denied writing the preview"
_WINDOWS_LOCKED = (5, 32)  # ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION


def _preview_write_error(exc):
    """The readable error for a PermissionError while writing a preview."""
    if getattr(exc, "winerror", None) in _WINDOWS_LOCKED:
        return PREVIEW_IN_USE
    return PREVIEW_NOT_WRITABLE


def _write_image_thumbnail(src, dst, settings, guard=None, track=None):
    """Write *src*'s preview into *dst* with *settings* (blocking, atomic).

    The first frame, fitted inside the edge, in the configured format;
    returns the path written (see thumbnail_encode.write_thumbnail).
    """
    with Image.open(src) as img:
        frame = thumbnail_frame(img, settings.edge)
    return write_thumbnail(frame, dst, settings, guard=guard, track=track)


def _video_placeholder_image(thumbnail_size):
    """The placeholder frame for a video no decoder could read: a play mark."""
    from PIL import ImageDraw, ImageFont

    img = Image.new("RGB", thumbnail_size, color=(50, 50, 50))
    draw = ImageDraw.Draw(img)
    center_x, center_y = thumbnail_size[0] // 2, thumbnail_size[1] // 2
    triangle_size = min(thumbnail_size) // 4
    points = [
        (center_x - triangle_size // 2, center_y - triangle_size // 2),
        (center_x - triangle_size // 2, center_y + triangle_size // 2),
        (center_x + triangle_size // 2, center_y),
    ]
    draw.polygon(points, fill=(255, 255, 255))
    try:
        font = ImageFont.load_default()
        text = "VIDEO"
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        draw.text(
            (center_x - text_width // 2, center_y + triangle_size // 2 + 10),
            text,
            fill=(255, 255, 255),
            font=font,
        )
    except (OSError, AttributeError):
        pass
    return img


def _is_decode_error(exc):
    """True when *exc* says the file itself cannot be decoded (#249 M4).

    Pillow's decoder errors are OSErrors without an errno; a real I/O error
    (disk full, a share gone) carries one and is worth another try.
    """
    if isinstance(exc, (UnidentifiedImageError, Image.DecompressionBombError)):
        return True
    if isinstance(exc, SyntaxError):
        return True
    return isinstance(exc, OSError) and exc.errno is None


def _new_thumbnail_stats():
    return {"generated": 0, "skipped": 0, "errors": []}


def _record_thumbnail_result(stats, result):
    action = result["action"]
    if action == "generated":
        stats["generated"] += 1
    elif action == "skipped":
        stats["skipped"] += 1
    else:
        stats["errors"].append(f"{result['file']}: {result.get('error', 'failed')}")


def _thumbnail_progress_payload(index, total, stats, result, start):
    elapsed = _time.monotonic() - start
    rate = index / elapsed if elapsed > 0 else 0
    eta = (total - index) / rate if rate > 0 else 0
    return {
        "processed": index,
        "total_images": total,
        "generated": stats["generated"],
        "skipped": stats["skipped"],
        "error_count": len(stats["errors"]),
        "percentage": round(index / total * 100, 1) if total else 100.0,
        "rate": round(rate, 1),
        "eta": round(eta),
        "elapsed": round(elapsed, 1),
        "current_file": f"{result['dir']}/{result['file']}",
        "file_type": result["type"],
        "action": result["action"],
    }


def _thumbnail_complete_payload(total, stats, start, remaining=0, processed=None):
    """Final payload of a thumbnail run.

    ``total`` counts every media file considered (pending, already fresh and
    left over), ``processed`` the files this run actually worked on (defaults
    to ``total``) and ``remaining`` the pending files left for the next run
    because of MAX_THUMBNAIL_FILES.
    """
    elapsed = _time.monotonic() - start
    processed = total if processed is None else processed
    errors = stats["errors"]
    message = (
        f"Generated {stats['generated']} new thumbnails, "
        f"skipped {stats['skipped']} existing"
    )
    if remaining:
        message += f", {remaining} more to generate on the next run"
    if errors:
        message += f" ({len(errors)} errors occurred)"
    return {
        "count": stats["generated"],
        "skipped": stats["skipped"],
        "total_images": total,
        "remaining": remaining,
        "errors": errors[:10],
        "error_count": len(errors),
        "elapsed_time": round(elapsed, 2),
        "processing_rate": round(processed / elapsed if elapsed > 0 else 0, 2),
        "message": message,
    }


# The per-file states a thumbnail plan counts (#317).
_PLAN_COUNTS = ("skipped", "remaining", "fresh", "outdated", "missing")


def _preview_names(folders, folder):
    """{normcased name: mtime} of the regular files in *folder*, listed
    once per plan (a missing folder is empty)."""
    names = folders.get(folder)
    if names is None:
        names = {}
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            stat = entry.stat(follow_symlinks=False)
                            names[os.path.normcase(entry.name)] = stat.st_mtime
                    except OSError:
                        continue
        except OSError:
            pass
        folders[folder] = names
    return names


def _preview_state(src, folder, stem, settings, folders):
    """fresh, outdated or missing for *src* (see _plan_thumbnails)."""
    names = _preview_names(folders, folder)
    try:
        src_mtime = src.stat().st_mtime
    except OSError:
        return "missing"
    current = names.get(os.path.normcase(stem + settings.extension))
    others = [
        names[key]
        for key in (
            os.path.normcase(stem + extension)
            for extension in thumbnail_settings.owned_extensions(
                src.suffix, settings.extension
            )
            if extension != settings.extension
        )
        if key in names
    ]
    current_fresh = current is not None and current >= src_mtime
    if current_fresh and not others:
        return "fresh"
    if others and (current_fresh or max(others) >= src_mtime):
        return "outdated"
    return "missing"


def _thumbnail_is_fresh(src, dst):
    """True when ``dst`` exists and is at least as new as ``src`` (blocking stat)."""
    try:
        return dst.stat().st_mtime >= src.stat().st_mtime
    except OSError:
        return False


# Caps on request-driven work (list sizes, scans, request bodies).
MAX_GALLERY_FILES = 50_000
MAX_BULK_LIMIT = 5_000
MAX_JSON_BODY_BYTES = 1024 * 1024
_READ_CHUNK = 64 * 1024


_WORKSPACE_CURSOR_SECRET = os.urandom(32)


def _workspace_root(query: dict, roots: list[Path]) -> int | None:
    value = query.get("root")
    if value is None:
        return None
    try:
        index = int(value)
    except ValueError:
        raise ValueError("Invalid gallery root") from None
    if not 0 <= index < len(roots):
        raise ValueError("Invalid gallery root")
    return index


WORKSPACE_ORDERS = ("newest", "oldest", "name", "name-desc", "rating")
# Library-compatible bounds on the linked-positive filters (database/library.py).
MAX_FILTER_CATEGORY = 200
MAX_FILTER_TAGS = 30
MAX_FILTER_TAG = 200


class _Listed(NamedTuple):
    """One listed file plus the derived values every filter and sort reads.

    Built once per gallery scan (see ``_workspace_orders``); the first four
    fields are the ``(path, mtime, root, index)`` entry the page formatter takes.
    """

    path: Path
    mtime: float
    root: Path
    index: int
    folded: str  # relative POSIX path, case-folded (filename search)
    folder: str  # str(relative.parent), native separators (subfolder filter)
    name: str  # case-folded basename (name sorts)
    tie: str  # sha256 of the relative POSIX path (bounded tie-break)
    video: bool
    normalized: str  # normalize_image_path, compared with linked rows


def _listed(path: Path, mtime: float, root: Path, index: int) -> _Listed:
    text, prefix = str(path), os.path.join(str(root), "")
    relative = (
        text[len(prefix) :] if text.startswith(prefix) else str(path.relative_to(root))
    )
    posix = relative if os.sep == "/" else relative.replace(os.sep, "/")
    name = relative.rpartition(os.sep)[2]
    return _Listed(
        path,
        mtime,
        root,
        index,
        posix.casefold(),
        os.path.dirname(relative) or ".",
        name.casefold(),
        hashlib.sha256(posix.encode("utf-8")).hexdigest(),
        os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS,
        normalize_image_path(text),
    )


class _ImageTagMatch(NamedTuple):
    """How one listed file's current image tags meet the filters (#249 B3)."""

    tags: bool  # every filter tag is in its image tags or linked positive tags
    query: bool  # ``q`` is a case-folded substring of one image tag name
    link: tuple | None  # (hit, rating, positive id) of its unambiguous link


def _current_fingerprint(path: Path) -> tuple[int, int] | None:
    """The file's present ``(st_size, st_mtime_ns)``; None when it is gone."""
    try:
        observed = os.stat(path)
    except OSError:
        return None
    return observed.st_size, observed.st_mtime_ns


def _workspace_key(entry: tuple, order: str) -> tuple:
    """The keyset sort key of *entry* (a _Listed or a 4-tuple entry)."""
    if not isinstance(entry, _Listed):
        entry = _listed(*entry)
    if order in ("name", "name-desc"):
        return (entry.name, entry.tie, entry.index, entry.mtime)
    mtime = -entry.mtime if order in ("newest", "rating") else entry.mtime
    return (mtime, entry.name, entry.tie, entry.index)


def _newest_key(entry: _Listed) -> tuple:
    return _workspace_key(entry, "newest")


def _rated_key(entry: _Listed, rank: int) -> tuple:
    """Highest rated: rating 5..1, linked unrated (0), unlinked (-1); then newest."""
    return (-rank, *_workspace_key(entry, "rating"))


def _workspace_cursor(key: tuple, scope: str) -> str:
    payload = json.dumps(
        {"v": 1, "scope": scope, "key": key}, separators=(",", ":"), ensure_ascii=False
    ).encode()
    signature = hmac.new(_WORKSPACE_CURSOR_SECRET, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(signature + payload).decode().rstrip("=")


def _workspace_cursor_key(token: str, scope: str, order: str) -> tuple:
    try:
        if len(token) > 4096:
            raise ValueError()
        raw = base64.b64decode(
            token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
        )
        signature, payload = raw[:32], raw[32:]
        expected = hmac.new(_WORKSPACE_CURSOR_SECRET, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        data = json.loads(payload)
        key = data["key"]
        if data["v"] != 1 or data["scope"] != scope or not isinstance(key, list):
            raise ValueError()
        types = (
            (str, str, int, (float, int))
            if order in ("name", "name-desc")
            else ((float, int), str, str, int)
        )
        if order == "rating":
            types = (int, *types)
        if len(key) != len(types) or not all(
            type(value) in (kind if isinstance(kind, tuple) else (kind,))
            for value, kind in zip(key, types)
        ):
            raise ValueError()
        return tuple(key)
    except (ValueError, KeyError, TypeError, UnicodeError, binascii.Error):
        raise ValueError("Invalid gallery cursor; refresh the gallery") from None


class _BodyTooLarge(Exception):
    """Raised when a request body exceeds MAX_JSON_BODY_BYTES."""


def _json_error(message, status):
    return web.json_response({"success": False, "error": message}, status=status)


async def _read_json_body(request):
    """Read a JSON object body in bounded chunks.

    Raises _BodyTooLarge past MAX_JSON_BODY_BYTES (declared or streamed) and
    ValueError when the body is not a JSON object.
    """
    limit = MAX_JSON_BODY_BYTES
    if request.content_length is not None and request.content_length > limit:
        raise _BodyTooLarge()
    chunks, size = [], 0
    while True:
        chunk = await request.content.read(_READ_CHUNK)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise _BodyTooLarge()
        chunks.append(chunk)
    data = json.loads(b"".join(chunks).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


async def _json_body_or_error(request):
    """Return ``(data, None)`` or ``(None, error_response)``."""
    try:
        return await _read_json_body(request), None
    except _BodyTooLarge:
        return None, _json_error("Request body too large", 413)
    except (ValueError, UnicodeDecodeError):
        return None, _json_error("Invalid JSON body", 400)


MAX_FAVORITE_PATH = 4096


def _favorite_target(data: Mapping, roots: list[Path]):
    """``((index, relative), None)`` for a favorite body, else ``(None, error)``.

    Rejects unknown roots, traversal, absolute or drive paths and non-media
    names before any filesystem or database access.
    """
    index, relative = data.get("root_index"), data.get("path")
    if type(index) is not int or not 0 <= index < len(roots):
        return None, bad_request("Invalid gallery root")
    if (
        not isinstance(relative, str)
        or not relative
        or len(relative) > MAX_FAVORITE_PATH
    ):
        return None, bad_request("path must be a relative media path")
    if (
        _has_traversal(relative)
        or "\0" in relative
        or re.match(r"^[a-zA-Z]:", relative)
    ):
        return None, _forbidden()
    if Path(relative).suffix.lower() not in MEDIA_EXTENSIONS:
        return None, _forbidden("Only media files can be favorites")
    return (index, relative), None


def _reveal_target(data: Mapping):
    """``((index, scope, relative), None)`` for a reveal body, else an error.

    Only the listing identity is accepted: an int root index, the root's
    64-hex scope and a bounded root-relative path, never an absolute,
    drive-qualified or ``..`` path.
    """
    index, scope, relative = (
        data.get("root_index"),
        data.get("root_scope"),
        data.get("path"),
    )
    if type(index) is not int or index < 0:
        return None, bad_request("Invalid gallery root")
    if not isinstance(scope, str) or re.fullmatch(r"[0-9a-f]{64}", scope) is None:
        return None, bad_request("Invalid gallery root scope")
    if (
        not isinstance(relative, str)
        or not relative
        or len(relative) > MAX_FAVORITE_PATH
        or "\0" in relative
        or _has_traversal(relative)
        or re.match(r"^[a-zA-Z]:", relative)
    ):
        return None, bad_request("path must be a relative media path")
    return (index, scope, relative), None


def _reveal_not_found():
    return web.json_response(
        {"success": False, "error": "Image file not found"}, status=404
    )


def _not_local():
    return web.json_response(
        {
            "success": False,
            "code": "not-local",
            "error": "Show in folder works only on the computer running ComfyUI.",
        },
        status=403,
    )


# The path module whose case rules key favorites (tests swap in ntpath).
_path_flavor = os.path


def _favorite_relative_key(relative: str) -> str:
    """Case-normalized POSIX key of a root-relative path.

    Lowercased with "/" separators where the platform folds case (Windows),
    identity on POSIX, so a case-only rename keeps its favorite on Windows
    and distinct Linux files stay distinct.
    """
    return _path_flavor.normcase(relative).replace(_path_flavor.sep, "/")


def _listing_posix(root, prefix, path) -> str:
    """*path*'s POSIX spelling below *root* (*prefix*: ``join(root, "")``)."""
    text = str(path)
    if text.startswith(prefix):
        relative = text[len(prefix) :]
        return relative if os.sep == "/" else relative.replace(os.sep, "/")
    return path.relative_to(root).as_posix()


def _favorite_listing_step(root, listing, added, removed) -> dict | None:
    """*listing* after a patched listing's change, or None to rebuild it.

    Only when every removed file is added again under the same spelling (a
    rewritten output) and no added file's key is already taken by another
    spelling: the full build keeps the first spelling in listing order.
    """
    prefix = os.path.join(str(root), "")
    spellings = {_listing_posix(root, prefix, path) for path, _mtime in added}
    if any(_listing_posix(root, prefix, path) not in spellings for path, _ in removed):
        return None
    listing = dict(listing)
    for posix in spellings:
        known = listing.setdefault(_favorite_relative_key(posix), posix)
        if known != posix:
            return None
    return listing


def _favorite_root_key(root) -> str:
    """Server-side identity of a gallery root: its real, case-normalized path.

    Never sent in a response; folder names alone are not unique across roots.
    """
    return _favorite_relative_key(os.path.realpath(str(root)))


def _name_suffix(name):
    """``Path(name).suffix`` as Python 3.10-3.13 compute it.

    The last ``.``-part unless the dot is the first or last character, so
    ``..png`` is ``.png`` and ``.png`` has none. Spelled out because 3.14
    changed ``Path.suffix`` and ``os.path.splitext`` differs for leading dots.
    """
    dot = name.rfind(".")
    return name[dot:] if 0 < dot < len(name) - 1 else ""


def _walk_media(output_path, max_files):
    """Yield the ``os.DirEntry`` of each media file under *output_path*.

    Symlinks are never followed, ``thumbnails/`` is skipped, descent stops
    at MAX_SCAN_DEPTH and at most *max_files* entries are produced. The order
    is ``os.walk``'s (top-down, subfolders in ``os.scandir`` order, files by
    name), which the gallery's ties in mtime keep. Each entry's type comes
    from the directory listing itself, so a walk builds no ``Path`` and makes
    no extra ``lstat`` per file (#348: a 20,000-file walk spent half its time
    there).
    """
    stack = [(os.fspath(output_path), ())]
    produced = 0
    while stack:
        top, rel_parts = stack.pop()
        if "thumbnails" in rel_parts:
            continue
        dirs, files = [], []
        try:
            with os.scandir(top) as entries:
                for entry in entries:
                    try:
                        is_dir = entry.is_dir()
                    except OSError:
                        is_dir = False
                    (dirs if is_dir else files).append(entry)
        except OSError:
            continue  # as os.walk: an unreadable folder is skipped
        for entry in sorted(files, key=lambda item: item.name):
            if _name_suffix(entry.name).lower() not in MEDIA_EXTENSIONS:
                continue
            if entry.is_symlink():
                continue  # never read through a link out of the output tree
            if produced >= max_files:
                return
            produced += 1
            yield entry
        if len(rel_parts) >= MAX_SCAN_DEPTH:
            continue
        for entry in reversed(dirs):
            if not entry.is_symlink():
                stack.append((entry.path, rel_parts + (entry.name,)))


def _settled_entry(root, relative):
    """``(Path, mtime)`` of a settled output as a walk would list it.

    *relative* is its part below *root* (``gallery_listing.relative_part``).
    None when a walk would not list it (``_walk_media``'s filters, and every
    folder on the way must be listable); raises ``OSError`` when that cannot
    be told (the file is gone or unreadable, or its on-disk spelling differs
    or cannot be read). Blocking: run it in the executor.
    """
    path = Path(os.path.join(os.fspath(root), relative))
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or stat.S_ISDIR(info.st_mode):
        return None
    # Before the name filters: they compare the walk's (stored) spelling.
    on_disk = _on_disk_path(path)
    if on_disk is not None and not on_disk.endswith(os.sep + relative):
        raise OSError(f"{relative} is spelled differently on disk")
    *folders, name = relative.split(os.sep)
    if _name_suffix(name).lower() not in MEDIA_EXTENSIONS:
        return None
    if "thumbnails" in folders or len(folders) > MAX_SCAN_DEPTH:
        return None
    folder = os.fspath(root)
    for part in ("", *folders):
        folder = os.path.join(folder, part) if part else folder
        if not os.access(folder, os.R_OK | os.X_OK):
            return None  # the walk cannot list this folder
    return path, info.st_mtime


def _on_disk_path(path, platform=None):
    """The file's path as its folders store the names, where that can differ.

    Windows and macOS file systems ignore case. Windows' ``realpath`` gives
    the stored case, but keeps the given one when it cannot open the file;
    macOS' ``realpath`` always keeps the given one. A walk lists the names as
    stored, so an add checks them: Windows through ``GetFinalPathNameByHandle``
    (``nt._getfinalpathname``), macOS through ``F_GETPATH``. Raises OSError
    when it cannot be read; None elsewhere (case-sensitive file systems).
    """
    platform = platform or sys.platform
    if platform == "win32":
        import nt

        return nt._getfinalpathname(os.fspath(path))
    if platform != "darwin":
        return None
    import fcntl

    # Non-blocking: a FIFO named like an image must not hold the executor.
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
        raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(1024))
    finally:
        os.close(descriptor)
    return os.fsdecode(raw.split(b"\0", 1)[0])


def _patch_listing(root, files, folders, with_ancestors, relatives):
    """The listing with each settled output in *relatives* added (blocking).

    Returns ``(files, folders, with_ancestors, unresolved)``; *files* is the
    same list when nothing changed. ``unresolved`` is True when an output
    could not be placed exactly, so the root needs a walk.
    """
    unresolved = False
    for relative in relatives:
        try:
            entry = _settled_entry(root, relative)
        except OSError:
            unresolved = True
            continue
        if entry is None:
            continue  # a walk would not list it either
        patched = gallery_listing.insert_entry(files, entry, MAX_GALLERY_FILES)
        if patched is None:
            unresolved = True
        elif patched is not files:
            files = patched
            folders, with_ancestors = gallery_listing.add_folder(
                root, entry[0], folders, with_ancestors
            )
    return files, folders, with_ancestors, unresolved


def _iter_media_files(output_path, max_files):
    """Yield the ``Path`` of each media file under *output_path* (see _walk_media)."""
    for entry in _walk_media(output_path, max_files):
        yield Path(entry.path)


def media_thumbnail_rel(rel_path, settings=None):
    """``thumbnails/<rel>_thumb<ext>`` (POSIX) for the media file *rel_path*:
    the name the preview gets when it is written now (#317), images and
    videos alike (``writer_settings``' extension)."""
    settings = settings or writer_settings()
    return thumbnail_settings.thumbnail_rel(rel_path, settings.extension)


def existing_media_thumbnail(output_path, rel_path):
    """The existing preview *rel_path* owns that a listing or cover shows
    (the format written now first, then older ones), or None. The Images
    listing and History's cover use this one lookup (review M5)."""
    return thumbnail_settings.existing_thumbnail(
        output_path, rel_path, writer_settings().extension
    )


def media_thumbnail(output_path, rel_path, root_index, *, root_scope=None):
    """``(thumbnail_url, ready)`` for *rel_path* under *output_path* (a few
    blocking stats). The Images listing, the Needs review queue cards and the
    Library use the same URL (#249 F, M10).

    An existing preview the file owns (the configured format first, then an
    older name such as ``_thumb.png``, #317) is listed with its mtime as
    ``v=`` and ``ready`` True. A missing one is still listed under the name
    it will get (without ``v=``): the serve route makes it on first view and
    falls back when it cannot (py/api/thumbnail_auto.py), so no card loads the
    full file because its preview was not made yet. Media inside a nested
    ``thumbnails`` folder is never previewed: ``(None, False)``, and its card
    shows the original.
    """
    rel_path = Path(rel_path)
    if "thumbnails" in rel_path.parts[:-1]:
        return None, False
    existing = existing_media_thumbnail(output_path, rel_path)
    thumbnail_url = None
    if existing is not None:
        thumb_rel = thumbnail_settings.thumbnail_stem_rel(rel_path) + existing.suffix
        thumbnail_url = thumbnail_url_for(thumb_rel, existing)
    ready = thumbnail_url is not None
    if thumbnail_url is None:
        thumb_rel = media_thumbnail_rel(rel_path)
        quoted = urllib.parse.quote(thumb_rel, safe="/")
        thumbnail_url = f"/prompt_manager/images/serve/{quoted}?root={root_index}"
    else:
        thumbnail_url += f"&root={root_index}"
    if root_scope is not None:
        thumbnail_url += f"&root_scope={root_scope}"
    return thumbnail_url, ready


def media_thumbnail_url(output_path, rel_path, root_index, *, root_scope=None):
    """The thumbnail URL of ``media_thumbnail`` (None for nested previews)."""
    return media_thumbnail(output_path, rel_path, root_index, root_scope=root_scope)[0]


def _output_image_entry(
    media_path, output_path, root_index, public_root=None, *, root_scope=None
):
    """Build one gallery entry for *media_path* (blocking stat).

    ``path`` is relative to its root (what delete/autotag resolve against) and
    ``root_dir`` is the root's public form; neither reveals the server layout.
    """
    if public_root is None:
        public_root = _public_path(output_path)
    stat = media_path.stat()
    rel_path = media_path.relative_to(output_path)
    extension = media_path.suffix.lower()
    is_video = extension in VIDEO_EXTENSIONS

    thumbnail_url, thumbnail_ready = media_thumbnail(
        output_path, rel_path, root_index, root_scope=root_scope
    )
    encoded_path = urllib.parse.quote(rel_path.as_posix(), safe="/")
    url = f"/prompt_manager/images/serve/{encoded_path}?root={root_index}"
    if root_scope is not None:
        url += f"&root_scope={root_scope}"

    entry = {
        "id": hashlib.sha1(
            str(media_path).encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:16],
        "filename": media_path.name,
        "path": rel_path.as_posix(),
        "relative_path": str(rel_path),
        "root_dir": public_root,
        "root_index": root_index,
        "url": url,
        "thumbnail_url": thumbnail_url,
        # Whether the preview file exists yet (the legacy gallery's dot, #249).
        "thumbnail_ready": thumbnail_ready,
        "size": stat.st_size,
        "modified_time": stat.st_mtime,
        "extension": extension,
        "media_type": "video" if is_video else "image",
        "is_video": is_video,
    }
    if root_scope is not None:
        entry["root_scope"] = root_scope
    return entry


def _workspace_filters(query: Mapping) -> dict:
    """Validate the workspace listing's filter and sort parameters.

    ``q`` (at most 500 characters) matches the relative path, case-folded, and
    the linked positive's text, title and tag names. ``category`` is exact,
    ``tags`` is a comma list that must all be present, ``sort=rating`` ranks by
    the linked positive, and ``favorites=1`` keeps only favorites (the
    Favorites page, #249 D51). ``collection=<id>`` (1 to 9 digits) keeps only
    that collection's members (#331); it wins over ``favorites`` (a page never
    sets both). Raises ValueError for any unknown or oversized value.
    """
    text = query.get("q", "").strip()
    folded = text.casefold()
    media = query.get("media", "all")
    order = query.get("sort", "newest")
    folder = query.get("subfolder", "").strip().replace("\\", "/")
    # ``path``: one exact relative path (Activity opens a scan row by it, D57).
    exact = query.get("path", "").strip().replace("\\", "/")
    category = query.get("category", "").strip() or None
    tags = sorted(
        {tag.strip() for tag in query.get("tags", "").split(",") if tag.strip()}
    )
    favorites = query.get("favorites", "0")
    collection = query.get("collection", "")
    if (
        favorites not in ("0", "1")
        or (collection != "" and _COLLECTION_ID.fullmatch(collection) is None)
        or len(text) > 500
        or len(folded) > 500
        or media not in ("all", "image", "video")
        or order not in WORKSPACE_ORDERS
        or (category is not None and len(category) > MAX_FILTER_CATEGORY)
        or len(tags) > MAX_FILTER_TAGS
        or any(len(tag) > MAX_FILTER_TAG for tag in tags)
    ):
        raise ValueError("Invalid gallery filters")
    if folder and (_has_traversal(folder) or len(folder) > 1024):
        raise ValueError("Invalid gallery folder")
    if exact and (_has_traversal(exact) or len(exact) > 1024):
        raise ValueError("Invalid gallery path")
    return {
        "text": text,
        "query": folded,
        "media": media,
        "order": order,
        "folder": folder,
        "path": exact.casefold(),
        "category": category,
        "tags": tags,
        "favorites": favorites == "1" and not collection,
        "collection": int(collection) if collection else None,
    }


# The listing parameters a bulk request may name as its filter (#392): the
# workspace query without mode, limit, cursor and the Activity-only ``path``,
# plus the chosen root's ``root_scope`` (a renumbered source is refused).
LISTING_FILTER_KEYS = frozenset(
    {
        "q",
        "subfolder",
        "category",
        "tags",
        "root",
        "root_scope",
        "media",
        "sort",
        "favorites",
        # A collection page's filter (#331): bulk actions act on its members.
        "collection",
    }
)
MAX_LISTING_FILTER_VALUE = 2048
# The most images "Select all N matching" acts on in one request (#392).
MAX_LISTING_SELECTION = 10_000


def parse_listing_filter(raw: object) -> dict:
    """A bulk request's listing filter (#392): the listing's own query as an object.

    Keys are a subset of LISTING_FILTER_KEYS, each value a string of at most
    MAX_LISTING_FILTER_VALUE characters, validated as the listing validates its
    query. The root's bounds need the current roots, so resolve_listing_filter
    checks those. Raises ValueError.
    """
    if not isinstance(raw, dict) or not set(raw) <= LISTING_FILTER_KEYS:
        raise ValueError("filter must be an object of listing parameters")
    if any(
        not isinstance(value, str) or len(value) > MAX_LISTING_FILTER_VALUE
        for value in raw.values()
    ):
        raise ValueError("filter values must be short strings")
    if "root_scope" in raw and "root" not in raw:
        raise ValueError("root_scope needs root")
    _workspace_filters(raw)
    return dict(raw)


class ListingSourceChanged(Exception):
    """A listing filter's ``root_scope`` no longer names its root (#392)."""


class ListingSelection(NamedTuple):
    """Every image a listing filter admits, in the listing's order (#392).

    ``targets`` are listing identities ``(root_index, root_scope, path)``;
    ``positive_ids`` the distinct linked positives (first seen), ``unlinked``
    the admitted images without one, and ``truncated`` that more than the cap
    matched (the lists then stop at the cap).
    """

    targets: list
    positive_ids: list
    unlinked: int
    truncated: bool


# A plain listing's cursor page leaves positive_count out (see
# _select_workspace_page); None instead means the count could not be read.
_COUNT_OMITTED = object()


def _admitted_entries(
    entries: list,
    filters: dict,
    links: dict | None,
    members: frozenset | None = None,
    image_tags: dict | None = None,
):
    """Yield ``(position, entry, link match)`` for each entry the filters admit.

    *links* maps normalized paths to (hit, rating, positive id) for every
    unambiguously linked image (see PromptDatabase.get_image_link_matches); a
    category or tag filter admits only those, and ``q`` admits a filename match
    or a linked-positive hit. *members*, when given, admits only those listed
    paths (the favorites of ``favorites=1`` or a ``collection``'s members).
    *image_tags* maps normalized paths to their _ImageTagMatch: a tag filter
    also admits a file whose current image tags (with its linked positive's
    tags) carry every tag, and ``q`` also admits an image tag name hit.
    """
    query, media, folder = filters["query"], filters["media"], filters["folder"]
    members_only = filters["category"] is not None or bool(filters["tags"])
    native_folder = folder.replace("/", os.sep)
    nested = native_folder + os.sep
    exact = filters.get("path", "")
    for position, entry in enumerate(entries):
        if exact and entry.folded != exact:
            continue
        if folder and not (
            entry.folder == native_folder or entry.folder.startswith(nested)
        ):
            continue
        if media != "all" and (media == "video") != entry.video:
            continue
        if members is not None and entry.path not in members:
            continue
        match = None if links is None else links.get(entry.normalized)
        image = None if image_tags is None else image_tags.get(entry.normalized)
        if members_only and match is None and not (image and image.tags):
            continue
        if (
            query
            and query not in entry.folded
            and not (match and match[0])
            and not (image and image.query)
        ):
            continue
        yield position, entry, match


def _filtered_page(
    entries: list,
    start: int,
    size: int,
    filters: dict,
    links: dict | None,
    members: frozenset | None = None,
    image_tags: dict | None = None,
) -> tuple[int, list, int | None]:
    """Count the *entries* the filters admit and take *size* from *start* on.

    Admission is _admitted_entries'. Returns the total, the page and the number
    of distinct linked positives among all admitted entries (None without
    links).
    """
    total, page, positives = 0, [], set()
    for position, entry, match in _admitted_entries(
        entries, filters, links, members, image_tags
    ):
        total += 1
        if match is not None:
            positives.add(match[2])
        if position >= start and len(page) < size:
            page.append(entry)
    return total, page, None if links is None else len(positives)


def _in_subfolder(rel_dir, subfolder):
    return rel_dir == subfolder or rel_dir.startswith(subfolder + os.sep)


class ImageRoutesMixin:
    """Mixin providing image and gallery-related API endpoints."""

    def _gallery_root_scope(self, root: Path, index: int) -> str:
        payload = json.dumps(
            ["prompt-manager-gallery-root-v1", index, str(_canonical(root))],
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hmac.new(_WORKSPACE_CURSOR_SECRET, payload, hashlib.sha256).hexdigest()

    def _gallery_scope_error(
        self, query: Mapping, roots: list[Path]
    ) -> web.Response | None:
        if "root_scope" not in query:
            return None
        scope, root = query.get("root_scope"), query.get("root")
        if (
            not isinstance(scope, str)
            or re.fullmatch(r"[0-9a-f]{64}", scope) is None
            or not isinstance(root, str)
            or re.fullmatch(r"[0-9]+", root) is None
        ):
            return bad_request("Invalid gallery root scope")
        try:
            index = int(root)
        except ValueError:
            return bad_request("Invalid gallery root scope")
        if index >= len(roots) or not hmac.compare_digest(
            scope, self._gallery_root_scope(roots[index], index)
        ):
            return web.json_response(
                {
                    "success": False,
                    "code": "source-changed",
                    "error": (
                        "The source changed. Refresh the image collection before "
                        "inspecting this file."
                    ),
                },
                status=409,
                headers={"Cache-Control": "no-store"},
            )
        return None

    def _register_image_routes(self, routes):
        @routes.get("/prompt_manager/prompts/{prompt_id}/images")
        async def get_prompt_images_route(request):
            return await self.get_prompt_images(request)

        @routes.get("/prompt_manager/images/recent")
        async def get_recent_images_route(request):
            return await self.get_recent_images(request)

        @routes.get("/prompt_manager/images/all")
        async def get_all_images_route(request):
            return await self.get_all_images(request)

        @routes.get("/prompt_manager/images/search")
        async def search_images_route(request):
            return await self.search_images(request)

        @routes.get("/prompt_manager/images/output")
        async def get_output_images_route(request):
            return await self.get_output_images(request)

        @routes.get("/prompt_manager/images/{image_id}/file")
        async def serve_image_route(request):
            return await self.serve_image(request)

        @routes.get("/prompt_manager/images/serve/{filepath:.*}")
        async def serve_output_image_route(request):
            return await self.serve_output_image(request)

        @routes.post("/prompt_manager/images/link")
        async def link_image_route(request):
            return await self.link_image_to_prompt(request)

        @routes.get("/prompt_manager/images/prompt/{image_path:.*}")
        async def get_image_prompt_route(request):
            return await self.get_image_prompt(request)

        @routes.get("/prompt_manager/images/inspect/{image_path:.*}")
        async def inspect_image_route(request):
            return await self.inspect_image(request)

        @routes.delete("/prompt_manager/images/{image_id}")
        async def delete_image_route(request):
            return await self.delete_image(request)

        @routes.get("/prompt_manager/images/thumbnails/status")
        async def thumbnail_status_route(request):
            return await self.get_thumbnail_status(request)

        @routes.get("/prompt_manager/images/thumbnails/plan")
        async def thumbnail_plan_route(request):
            return await self.get_thumbnail_plan(request)

        @routes.post("/prompt_manager/images/generate-thumbnails")
        async def generate_thumbnails_route(request):
            return await self.generate_thumbnails(request)

        # POST only: the stream writes files, so it must not be reachable
        # from a prefetch, link preview or <img src>.
        @routes.post("/prompt_manager/images/generate-thumbnails/progress")
        async def generate_thumbnails_progress_route(request):
            return await self.generate_thumbnails_with_progress(request)

        @routes.post("/prompt_manager/images/clear-thumbnails")
        async def clear_thumbnails_route(request):
            return await self.clear_thumbnails(request)

        @routes.get("/prompt_manager/gallery/subfolders")
        async def get_gallery_subfolders_route(request):
            return await self.get_gallery_subfolders(request)

        @routes.put("/prompt_manager/images/favorite")
        async def set_image_favorite_route(request):
            return await self.update_image_favorite(request, favorite=True)

        @routes.delete("/prompt_manager/images/favorite")
        async def clear_image_favorite_route(request):
            return await self.update_image_favorite(request, favorite=False)

        # POST, local-only: opens this computer's file manager (D38).
        @routes.post("/prompt_manager/images/reveal")
        async def reveal_image_route(request):
            return await self.reveal_image(request)

        # POST: drops the server-side listing cache and rewalks the output roots.
        @routes.post("/prompt_manager/gallery/rescan")
        async def rescan_gallery_route(request):
            return await self.rescan_gallery(request)

    def _present_images(self, images):
        """Image dicts ready for a response: urls added, server paths hidden."""
        self._enrich_images(images)
        return publish_image_paths(images, _public_path)

    async def get_prompt_images(self, request):
        """Get all images for a specific prompt."""
        try:
            prompt_id = request.match_info["prompt_id"]
            images = await self._run_in_executor(self.db.get_prompt_images, prompt_id)

            # Clean up any NaN values that cause JSON parsing errors (recursive)
            cleaned_images = [self._clean_nan_recursive(image) for image in images]

            # Add url and thumbnail_url so the frontend can serve them
            cleaned_images = self._present_images(cleaned_images)

            # Rows an older version stored may still hold NaN/Infinity: answer
            # strict JSON, and leave prompt text that spells "NaN" untouched.
            return web.json_response(
                {"success": True, "images": cleaned_images},
                dumps=functools.partial(dumps_strict, default=str),
            )

        except Exception as e:
            self.logger.error(f"Get prompt images error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    async def get_recent_images(self, request):
        """Get recently generated images (bounded window, newest first)."""
        try:
            try:
                limit, offset = parse_page_params(request.query)
            except ValueError:
                return bad_request("limit and offset must be integers")

            images = self._present_images(
                await self._run_in_executor(self.db.get_recent_images, limit, offset)
            )

            return web.json_response(
                {
                    "success": True,
                    "images": images,
                    "pagination": {
                        "limit": limit,
                        "offset": offset,
                        "count": len(images),
                    },
                }
            )
        except Exception as e:
            self.logger.error(f"Get recent images error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    async def get_all_images(self, request):
        """Get generated images with linked prompts (bulk-capped pages)."""
        try:
            try:
                limit, offset = parse_page_params(
                    request.query,
                    default_limit=MAX_BULK_LIMIT,
                    max_limit=MAX_BULK_LIMIT,
                )
            except ValueError:
                return bad_request("limit and offset must be integers")

            images = self._present_images(
                await self._run_in_executor(
                    self.db.get_all_images, limit=limit, offset=offset
                )
            )

            return web.json_response(
                {
                    "success": True,
                    "images": images,
                    "count": len(images),
                    "pagination": {
                        "limit": limit,
                        "offset": offset,
                        "count": len(images),
                        "has_more": len(images) == limit,
                    },
                }
            )
        except Exception as e:
            self.logger.error(f"Get all images error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    async def search_images(self, request):
        """Search images by prompt text."""
        try:
            query = request.query.get("q", "")
            if not query:
                return web.json_response(
                    {"success": False, "error": "Search query required"}, status=400
                )

            try:
                limit, offset = parse_page_params(request.query)
            except ValueError:
                return bad_request("limit and offset must be integers")

            images = self._present_images(
                await self._run_in_executor(
                    self.db.search_images_by_prompt, query, limit, offset
                )
            )

            return web.json_response(
                {
                    "success": True,
                    "images": images,
                    "query": query,
                    "pagination": {
                        "limit": limit,
                        "offset": offset,
                        "count": len(images),
                    },
                }
            )
        except Exception as e:
            self.logger.error(f"Search images error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    def _schedule_workspace_warm(self):
        """Rebuild the workspace caches behind a changed listing (on the loop).

        Only after the host's startup warm-up (warm_gallery_listings), so the
        first Images open after a restart, and the first read after a refresh
        found new files, pay no per-file rebuild. A walk stored while a build
        runs asks for one more build instead of a second one at once.
        """
        if not self._workspace_warm_enabled:
            return
        task = self._workspace_warm_task
        if task is not None and not task.done():
            self._workspace_warm_again = True
            return
        self._workspace_warm_task = asyncio.get_running_loop().create_task(
            self._warm_workspace()
        )

    async def _warm_workspace(self):
        while True:
            self._workspace_warm_again = False
            try:
                listings = self._stored_listings()
                if listings is None:
                    return  # a root has no listing yet; its walk asks again
                await self._run_in_executor(self._build_workspace_caches, listings)
            except Exception as e:
                self.logger.warning(f"Could not prepare the Images page: {e}")
                return
            if not self._workspace_warm_again:
                return

    def _stored_listings(self):
        """``[(index, root, files)]`` of every output root, or None if one has none."""
        listings = []
        for index, root in enumerate(self._get_all_output_dirs()):
            record = self._root_listings.get(str(root))
            if record is None or record.files is None:
                return None
            listings.append((index, root, record.files))
        return listings

    def _build_workspace_caches(self, listings):
        """Build what a first workspace page reads, as that page builds it (blocking).

        The default page's selection builds the per-file values, the newest
        order, the wanted paths and reads the prompt links (warming SQLite's
        cache); the favorites filter builds each root's favorite listing.
        """
        self._select_workspace_page(
            listings, _workspace_filters({}), None, 1, "workspace-warm-up"
        )
        self._workspace_favorite_paths(listings)

    def _scan_gallery_files_sync(self, output_path):
        """Scan output directory for media files (blocking I/O, run in executor).

        Returns list of (path, mtime) tuples sorted by mtime descending; the
        walk is bounded by MAX_SCAN_DEPTH and MAX_GALLERY_FILES.
        """
        found = []
        for entry in _walk_media(output_path, MAX_GALLERY_FILES):
            try:
                # Never a symlink (_walk_media skips them), so this is the
                # file's own mtime, read from the listing where it can be.
                mtime = entry.stat(follow_symlinks=False).st_mtime
            except OSError:
                continue
            found.append((Path(entry.path), mtime))
        if len(found) >= MAX_GALLERY_FILES:
            self.logger.warning(f"Gallery scan capped at {MAX_GALLERY_FILES} files")
        found.sort(key=lambda x: x[1], reverse=True)
        return found

    async def _get_gallery_files(self, output_path):
        """The shared listing of one output root: ``[(path, mtime)]``, newest first."""
        return (await self._listing(output_path)).files

    async def _listing(self, output_path, *, fresh=False):
        """The shared ``RootListing`` of *output_path*, with a listing (#348).

        A listing that exists is returned at once; when it is older than
        ``REFRESH_AFTER`` one background walk replaces it. With no listing,
        the caller waits for the root's single walk. ``fresh`` (Rescan) waits
        for a walk that started after this call, and raises when it fails
        even if a previous listing exists.
        """
        record = self._root_listing(output_path)
        record.retired = False  # a configured root: read, so listened to
        needed = record.walk_seq + 1 if fresh else 0
        discarded = 0
        while True:
            if record.files is not None and record.stored_seq >= needed:
                if not fresh:
                    self._refresh_if_stale(output_path, record)
                return record
            flight = record.flight
            if flight is None:
                flight = self._start_walk(output_path, record)
            joined = record.walk_seq
            # Shielded: a client that goes away never cancels a shared walk.
            outcome = await asyncio.shield(flight)
            if joined < needed:
                continue  # an older walk ended; Rescan needs a newer one
            if outcome.kind == FAILED:
                raise outcome.error
            if outcome.kind == DISCARDED:
                discarded += 1
                if discarded >= MAX_DISCARDED_WALKS:
                    raise RuntimeError(
                        "The output listing kept changing while it was read"
                    )

    def _refresh_if_stale(self, output_path, record):
        """Ask for a background walk when the listing is stale or dirty.

        A stale listing (older than REFRESH_AFTER) is walked REFRESH_DELAY
        after this read, so the walk does not share the GIL with the page's
        own requests; a dirty one as soon as its interval allows.
        """
        age = _time.monotonic() - (record.walked_at or 0.0)
        if age >= gallery_listing.REFRESH_AFTER:
            self._schedule_walk(output_path, gallery_listing.REFRESH_DELAY)
        elif record.dirty:
            self._schedule_walk(output_path)

    def output_settled(self, path):
        """The monitor settled an output at *path*: list it (on the loop).

        *path* is resolved by the monitor's thread (``notify_output_settled``).
        A root with a stored listing and no walk running gets the file added
        in the executor (``_run_adds``), without a walk. Every other root
        holding it is marked dirty (``mark_root_dirty``): one with a walk
        running gets the trailing walk, as that walk may have passed the
        file's folder already.
        """
        for key, record in self._settled_records(path):
            relative = None
            if (
                record.files is not None
                and record.flight is None
                and record.resolved_root is not None
            ):
                relative = gallery_listing.relative_part(record.resolved_root, path)
            if relative is None:
                self._mark_dirty(key, record)
                continue
            record.pending_adds.append(relative)
            if record.add_task is None:
                task = asyncio.get_running_loop().create_task(
                    self._run_adds(Path(key), record)
                )
                record.add_task = task
                self._listing_tasks.add(task)
                task.add_done_callback(self._listing_tasks.discard)

    def mark_root_dirty(self, path):
        """A settled output at *path*: walk every root holding it again (on the loop).

        *path* is resolved by the monitor's thread (``notify_output_settled``),
        so this does no file I/O: it compares ``normcase`` strings with each
        walk's ``resolved_root``. Only roots with a listing, or a walk running,
        are marked; a root without either is walked by its next read anyway.
        """
        for key, record in self._settled_records(path):
            self._mark_dirty(key, record)

    def _settled_records(self, path):
        """``(key, record)`` of every listed or walking root holding *path*."""
        if self._listings_closed:
            return []
        found = []
        for key, record in list(self._root_listings.items()):
            if record.retired or (record.files is None and record.flight is None):
                continue
            resolved = record.resolved_root or os.path.normcase(key)
            if gallery_listing.root_contains(resolved, path):
                found.append((key, record))
        return found

    def _mark_dirty(self, key, record):
        if self._listings_closed or record.retired:
            return
        if record.files is None and record.flight is None:
            return  # walked by its next read anyway
        record.dirty = True
        self._schedule_walk(Path(key))

    async def _run_adds(self, root, record):
        """Add the root's pending settled outputs to its listing (on the loop).

        Each batch is placed in the executor against the stored listing, and
        stored only when no walk started and no drop happened meanwhile: a
        walk started after the outputs settled lists them itself, and a
        dropped listing is walked by its next read. An output that cannot be
        placed exactly marks the root dirty instead.
        """
        key = str(root)
        try:
            while record.pending_adds:
                relatives, record.pending_adds = record.pending_adds, []
                if record.files is None or record.flight is not None:
                    continue  # dropped, or a walk started after they settled
                base, generation, seq = record.files, record.generation, record.walk_seq
                files, folders, with_ancestors, unresolved = (
                    await self._run_in_executor(
                        _patch_listing,
                        root,
                        base,
                        record.folders,
                        record.folders_with_ancestors,
                        relatives,
                    )
                )
                if self._listings_closed or record.retired:
                    return
                if record.generation != generation or record.walk_seq != seq:
                    continue
                if files is not base:
                    record.files = files
                    record.folders = folders
                    record.folders_with_ancestors = with_ancestors
                    record.patched += 1
                    self._schedule_workspace_warm()
                if unresolved:
                    self._mark_dirty(key, record)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.warning(f"Could not add a new output to the listing: {exc}")
            record.pending_adds = []
            self._mark_dirty(key, record)
        finally:
            record.add_task = None

    def _schedule_walk(self, output_path, delay=0.0):
        """Start or defer a background walk of *output_path* (on the loop).

        - A walk is running: nothing; its end re-checks ``dirty``.
        - Otherwise the walk is due *delay* from now, and never sooner than
          ``rewalk_interval`` after the previous walk started. Due now: start
          it. Later: one deferred walk per root, at least MIN_REWALK_DELAY
          away; when one is already pending, the earlier deadline wins.
        Every walk start cancels the pending one (``_start_walk``).
        """
        if self._listings_closed:
            return
        record = self._root_listing(output_path)
        if record.flight is not None or record.retired:
            return
        now = _time.monotonic()
        due = now + delay
        if record.last_started_at is not None:
            interval = gallery_listing.rewalk_interval(record.last_duration)
            due = max(due, record.last_started_at + interval)
        if due <= now:
            self._start_walk(output_path, record)
            return
        wait = max(gallery_listing.MIN_REWALK_DELAY, due - now)
        loop = asyncio.get_running_loop()
        handle = record.rewalk_handle
        if handle is not None:
            if handle.when() - loop.time() <= wait:
                return  # the pending walk comes first
            handle.cancel()
        record.rewalk_handle = loop.call_later(
            wait, self._delayed_walk, output_path, record
        )

    def _delayed_walk(self, output_path, record):
        """The deferred walk's timer: clear the handle first, then walk.

        It was armed for a stale or dirty listing at the earliest moment the
        interval allowed. Only a walk start clears either reason, and every
        start cancels this timer, so when it fires the walk is still due.
        """
        record.rewalk_handle = None
        if self._listings_closed or record.flight is not None or record.retired:
            return
        self._start_walk(output_path, record)

    def _start_walk(self, output_path, record):
        """Start the root's one walk; returns its flight future (on the loop)."""
        loop = asyncio.get_running_loop()
        self._listing_loop = loop
        handle, record.rewalk_handle = record.rewalk_handle, None
        if handle is not None:
            handle.cancel()  # this walk is the one it was waiting to start
        # This walk lists every output reported so far.
        record.dirty = False
        record.pending_adds = []
        record.last_started_at = _time.monotonic()
        record.walk_seq += 1
        flight = loop.create_future()
        record.flight = flight
        task = loop.create_task(
            self._run_walk(
                output_path, record, flight, record.generation, record.walk_seq
            )
        )
        self._listing_tasks.add(task)
        task.add_done_callback(self._listing_tasks.discard)
        return flight

    async def _run_walk(self, output_path, record, flight, generation, seq):
        """Walk in the executor, then store, discard or fail (on the loop).

        The flight is always resolved with a WalkOutcome, never failed, so an
        error never reaches a future nobody awaits.
        """
        outcome = None
        cancelled = False
        started = record.last_started_at
        try:
            files, folders, with_ancestors, resolved = await self._run_in_executor(
                walk_root, self._scan_gallery_files_sync, output_path, record.files
            )
        except asyncio.CancelledError:
            cancelled = True
            outcome = WalkOutcome.failed(RuntimeError(WALK_STOPPED))
            raise
        except Exception as exc:
            record.walked_at = _time.monotonic()
            if not record.failing:
                self.logger.warning(
                    f"Could not list output folder {output_path}: {exc}"
                )
            record.failing = True
            # Retried by the 30 s rule, not by a trailing walk.
            record.dirty = False
            outcome = WalkOutcome.failed(exc)
        else:
            record.failing = False
            record.resolved_root = resolved
            if record.generation == generation:
                changed = files is not record.files
                record.patched = 0
                record.files = files
                record.folders = folders
                record.folders_with_ancestors = with_ancestors
                record.walked_at = _time.monotonic()
                record.stored_seq = seq
                outcome = WalkOutcome.stored()
                if changed:
                    self._schedule_workspace_warm()
            else:
                outcome = WalkOutcome.discarded()
        finally:
            record.last_duration = _time.monotonic() - started
            if record.flight is flight:
                record.flight = None
            if not flight.done():
                flight.set_result(outcome)
            if record.dirty and not cancelled:
                # An output settled during this walk: the trailing walk.
                self._schedule_walk(output_path)

    async def get_output_images(self, request):
        """Get all images from ComfyUI output folder(s)."""
        try:
            output_dirs = self._get_all_output_dirs()
            if request.query.get("mode") == "workspace":
                return await self._get_workspace_images(request, list(output_dirs))
            if not output_dirs:
                return web.json_response(
                    {
                        "success": False,
                        "error": "No output directories found",
                        "images": [],
                    },
                )
            try:
                limit, offset = parse_page_params(request.query, default_limit=100)
            except ValueError:
                return bad_request("limit and offset must be integers")
            subfolder = request.query.get("subfolder", "").strip()

            all_images = await self._collect_output_images(output_dirs, subfolder)
            total = len(all_images)
            page = all_images[offset : offset + limit]
            images = await self._run_in_executor(self._format_output_page, page)

            return web.json_response(
                {
                    "success": True,
                    "images": images,
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                    "has_more": offset + limit < total,
                }
            )
        except Exception as e:
            self.logger.error(f"Output images error: {e}", exc_info=True)
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    async def _get_workspace_images(
        self, request, output_dirs: list[Path]
    ) -> web.Response:
        try:
            limit, _offset = parse_page_params(request.query, default_limit=100)
        except ValueError:
            return bad_request("limit and offset must be integers")
        try:
            root_index = _workspace_root(request.query, output_dirs)
            filters = _workspace_filters(request.query)
            scope = hashlib.sha256(
                json.dumps(
                    [
                        filters["query"],
                        filters["text"],
                        filters["media"],
                        filters["order"],
                        filters["folder"],
                        filters["path"],
                        root_index,
                        [str(root) for root in output_dirs],
                        filters["category"],
                        filters["tags"],
                        filters["favorites"],
                        filters["collection"],
                    ]
                ).encode()
            ).hexdigest()
            token = request.query.get("cursor")
            after = (
                _workspace_cursor_key(token, scope, filters["order"]) if token else None
            )
        except ValueError as exc:
            return bad_request(str(exc))

        try:
            if filters["collection"] is not None and not await self._run_in_executor(
                self.db.collection_exists, filters["collection"]
            ):
                raise CollectionMissing("No such collection")
            listings = await self._workspace_listings(output_dirs, root_index)
            page, total, has_more, next_cursor, positive_count = (
                await self._run_in_executor(
                    self._select_workspace_page, listings, filters, after, limit, scope
                )
            )
        except CollectionMissing:
            return collection_not_found()
        except Exception:
            # A filter that needs the prompt links or the image tags cannot be
            # served without them. Never echo the exception: it can carry SQL
            # or database text. The traceback goes to the server log.
            self.logger.error(
                "Workspace image list could not be filtered", exc_info=True
            )
            return web.json_response(
                {
                    "success": False,
                    "code": "listing-failed",
                    "error": _LISTING_FAILED,
                },
                status=500,
            )
        images = await self._run_in_executor(self._format_workspace_page, page)
        result = {
            "success": True,
            "images": images,
            "total": total,
            "positive_count": positive_count,
            "limit": limit,
            "has_more": has_more,
            "next_cursor": next_cursor,
        }
        if positive_count is _COUNT_OMITTED:
            del result["positive_count"]
        return web.json_response(result)

    def _workspace_listed(self, index: int, root: Path, files: list) -> list:
        """The _Listed values of one root's scan, rebuilt only where it changed.

        The gallery cache hands out the same list object until it rescans.
        A rescan reuses the value of every unchanged ``(path, mtime)`` and,
        when nothing changed, the previous list itself, so the cached sort
        orders stay valid. Only a gallery-roots change drops these values.
        """
        key = (index, str(root))
        cached = self._workspace_listed_cache.get(key)
        if cached is not None and cached[0] is files:
            return cached[1]
        if cached is not None:
            delta = gallery_listing.listing_delta(cached[0], files)
            if delta is not None:
                return self._workspace_listed_step(key, cached, files, delta)
        previous = cached[2] if cached is not None else {}
        listed = []
        for path, mtime in files:
            entry = previous.get((path, mtime))
            listed.append(entry or _listed(path, mtime, root, index))
        if (
            cached is not None
            and len(listed) == len(cached[1])
            and all(new is old for new, old in zip(listed, cached[1]))
        ):
            listed, lookup = cached[1], previous
        else:
            lookup = {(entry.path, entry.mtime): entry for entry in listed}
            # Card facts follow the per-file values: keep unchanged files only.
            cards = self._workspace_card_cache.get(key)
            if cards:
                self._workspace_card_cache[key] = {
                    file: facts for file, facts in cards.items() if file in lookup
                }
        self._workspace_listed_cache[key] = (files, listed, lookup, None)
        return listed

    def _workspace_listed_step(self, key, cached, files, delta) -> list:
        """The _Listed values after a few files were added or replaced.

        A listing patched by a settled output (``output_settled``) shares
        every other entry with the cached one, so only the added files are
        built; the order of the values does not matter (every sort is a total
        order). The step ``(previous values, added, removed)`` lets the sort
        orders follow it the same way (``_workspace_orders``).
        """
        index, root = key[0], Path(key[1])
        _files, old_listed, old_lookup, _step = cached
        added, removed = delta
        if not added and not removed:
            self._workspace_listed_cache[key] = (files, old_listed, old_lookup, None)
            return old_listed
        lookup = dict(old_lookup)
        gone = [lookup.pop(item) for item in removed if item in lookup]
        fresh = [_listed(path, mtime, root, index) for path, mtime in added]
        for entry in fresh:
            lookup[(entry.path, entry.mtime)] = entry
        if gone:
            gone_ids = {id(entry) for entry in gone}
            listed = [entry for entry in old_listed if id(entry) not in gone_ids]
            cards = self._workspace_card_cache.get(key)
            if cards:
                self._workspace_card_cache[key] = {
                    file: facts for file, facts in cards.items() if file in lookup
                }
        else:
            listed = list(old_listed)
        listed.extend(fresh)
        step = (old_listed, fresh, gone)
        self._workspace_listed_cache[key] = (files, listed, lookup, step)
        return listed

    def _workspace_orders(self, listings: list[tuple]) -> dict:
        """The selected roots' files in newest order, plus lazily added orders."""
        parts = tuple(
            self._workspace_listed(index, root, files)
            for index, root, files in listings
        )
        key = tuple((index, str(root)) for index, root, _files in listings)
        cached = self._workspace_order_cache.get(key)
        if cached is not None and all(
            old is new for old, new in zip(cached[0], parts, strict=True)
        ):
            return cached[1]
        newest = None
        if cached is not None:
            newest = self._newest_step(listings, parts, cached)
        if newest is None:
            newest = sorted(
                (entry for part in parts for entry in part),
                key=_newest_key,
            )
        # The other orders, the wanted paths and the locators follow lazily.
        orders = {"newest": newest}
        self._workspace_order_cache[key] = (parts, orders)
        return orders

    def _newest_step(self, listings, parts, cached) -> list | None:
        """The cached newest order with each part's last step applied, or None.

        Only when every changed part is exactly the value list this call
        built (*parts*) and one step (``_workspace_listed_step``) past the
        part the cached order was built from: the added values are placed by
        bisection instead of sorting the whole listing again. Page reads and
        the warm build run in executor threads at once, so the per-file cache
        may already hold a newer step than *parts*; then this sorts in full.
        """
        old_parts, orders = cached
        added, gone = [], []
        for (index, root, _files), part, old in zip(
            listings, parts, old_parts, strict=True
        ):
            if part is old:
                continue
            listed = self._workspace_listed_cache.get((index, str(root)))
            if listed is None or listed[1] is not part:
                return None
            step = listed[3]
            if step is None or step[0] is not old:
                return None
            added.extend(step[1])
            gone.extend(step[2])
        newest = orders["newest"]
        if gone:
            gone_ids = {id(entry) for entry in gone}
            newest = [entry for entry in newest if id(entry) not in gone_ids]
        else:
            newest = list(newest)
        for entry in added:
            insort(newest, entry, key=_newest_key)
        return newest

    def _workspace_wanted(self, listings: list[tuple]) -> frozenset:
        """The selected listing's normalized paths (cached with its orders)."""
        orders = self._workspace_orders(listings)
        if "wanted" not in orders:
            orders["wanted"] = frozenset(entry.normalized for entry in orders["newest"])
        return orders["wanted"]

    def _workspace_order(self, listings: list[tuple], order: str) -> list:
        """The listing sorted by a filesystem order (built lazily, cached)."""
        orders = self._workspace_orders(listings)
        if order not in orders:
            if order == "name-desc":
                orders[order] = self._workspace_order(listings, "name")[::-1]
            else:
                orders[order] = sorted(
                    orders["newest"], key=lambda entry: _workspace_key(entry, order)
                )
        return orders[order]

    def _workspace_locators(self, listings: list[tuple]) -> dict:
        """The selected listing's private locators -> _Listed (cached).

        A locator is ``normcase(join(realpath(root), relative))``, the key
        image tags are stored under (database/annotations.py); the listing
        never follows symlinks, so it equals the listed file's own locator.
        """
        orders = self._workspace_orders(listings)
        reals = tuple(os.path.realpath(root) for _index, root, _files in listings)
        key = ("locators", reals)
        if key not in orders:
            located = {}
            for (index, root, files), real in zip(listings, reals):
                prefix = os.path.join(str(root), "")
                for entry in self._workspace_listed(index, root, files):
                    text = str(entry.path)
                    relative = (
                        text[len(prefix) :]
                        if text.startswith(prefix)
                        else str(entry.path.relative_to(root))
                    )
                    located[os.path.normcase(os.path.join(real, relative))] = entry
            orders[key] = located
        return orders[key]

    def _image_tag_matches(self, listings: list[tuple], filters: dict) -> dict:
        """Normalized path -> _ImageTagMatch for files whose image tags count.

        Looks the relevant tag names up (exact under Tags, a case-folded
        substring for ``q``) and their files through the tag index, keeping
        only listed locators; only those candidates are
        stat'ed (never every listed file), and a candidate's tags count only
        while a byte record is verified at its present ``(size, mtime_ns)``
        (spec amendment S1). Under a Tags filter the candidates' linked
        positives are read too, so their tags and category combine.
        """
        tags, query, category = filters["tags"], filters["query"], filters["category"]
        wanted = set(tags)
        located = self._workspace_locators(listings)
        # The tag index answers which files carry a relevant name (#379); no
        # read streams every image tag row.
        tag_ids = self.db.image_tag_ids(wanted, query)
        candidates = {}
        if tag_ids:
            for locator in self.db.image_tag_locators(tag_ids):
                entry = located.get(locator)
                if entry is not None:
                    candidates[locator] = entry
        stats = {}
        for locator, entry in candidates.items():
            fingerprint = _current_fingerprint(entry.path)
            if fingerprint is not None:
                stats[locator] = fingerprint
        current = self.db.current_image_tags(stats) if stats else {}
        facts = {}
        if wanted and current:
            facts = self.db.get_image_link_facts(
                [candidates[locator].normalized for locator in current],
                filters["text"],
            )
        matches = {}
        for locator, names in current.items():
            entry = candidates[locator]
            link = facts.get(entry.normalized)
            carried = set(names).union(link[4] if link else ())
            matches[entry.normalized] = _ImageTagMatch(
                bool(wanted)
                and wanted <= carried
                and (category is None or (link is not None and link[3] == category)),
                bool(query) and any(query in name.casefold() for name in names),
                link[:3] if link else None,
            )
        return matches

    def _workspace_admission(
        self, listings: list[tuple], filters: dict, *, read_links: bool, strict=False
    ) -> tuple:
        """The ordered entries and what admits them (blocking; run in executor).

        Returns ``(entries, key, links, image_tags, members)``: the listing in
        the filters' order, its keyset key, and the link, image-tag and member
        maps _admitted_entries reads (members: a collection's, else the
        favorites, else None). A collection deleted since the route checked it
        raises CollectionMissing. One SELECT (get_image_link_matches) reads
        every linked positive when *read_links*. A failed read raises when the
        filters need it (category, tags, the rating sort) or *strict*; else the
        listing stays usable without links (a search then matches filenames
        only). A failed image-tag read raises under a Tags filter; a search then
        matches without image tags.
        """
        order, category, tags = filters["order"], filters["category"], filters["tags"]
        links = None
        if read_links:
            try:
                links = self.db.get_image_link_matches(
                    filters["text"],
                    category,
                    tags,
                    every_link=True,
                    wanted=self._workspace_wanted(listings),
                )
            except Exception:
                if strict or category is not None or tags or order == "rating":
                    raise
                self.logger.warning(
                    "Workspace prompt links could not be read; %s",
                    (
                        "searching filenames only"
                        if filters["query"]
                        else "positive count unknown"
                    ),
                    exc_info=True,
                )
        image_tags = None
        if filters["query"] or tags:
            try:
                image_tags = self._image_tag_matches(listings, filters)
            except Exception:
                if tags:
                    raise
                self.logger.warning(
                    "Workspace image tags could not be read; searching without "
                    "image tags",
                    exc_info=True,
                )
        if image_tags and links is not None:
            # A linked file admitted through its image tags keeps its rating
            # and counts its positive.
            admitted = {
                path: match.link
                for path, match in image_tags.items()
                if match.tags and match.link is not None and path not in links
            }
            if admitted:
                links = {**links, **admitted}
        if order == "rating":

            def rank(entry):
                match = links.get(entry.normalized)
                return -1 if match is None else (match[1] or 0)

            # Stable: within one rank the newest order (the tie-break) stays.
            entries = sorted(
                self._workspace_order(listings, "newest"),
                key=lambda entry: -rank(entry),
            )

            def key(entry):
                return _rated_key(entry, rank(entry))

        else:
            entries = self._workspace_order(listings, order)

            def key(entry):
                return _workspace_key(entry, order)

        return (
            entries,
            key,
            links,
            image_tags,
            self._workspace_members(listings, filters),
        )

    def _select_workspace_page(
        self,
        listings: list[tuple],
        filters: dict,
        after: tuple | None,
        limit: int,
        scope: str,
    ) -> tuple:
        """One keyset page of the filtered listing (blocking; run in executor).

        The filters, the rating sort and the positive count share one link
        read (_workspace_admission). A plain listing (no ``q``, category, tags
        or rating sort) reads it only for its first page; its cursor pages read
        no links and the returned count is _COUNT_OMITTED, so the response
        leaves ``positive_count`` out and the frontend keeps the first page's
        value. On that plain first page a failed read leaves the listing usable
        and the count unknown (None); so does a failed read under ``q`` alone,
        whose search then matches filenames only (as before the linked search
        existed).
        """
        order = filters["order"]
        needed = (
            bool(filters["query"])
            or filters["category"] is not None
            or bool(filters["tags"])
            or order == "rating"
        )
        entries, key, links, image_tags, members = self._workspace_admission(
            listings, filters, read_links=needed or after is None
        )
        start = 0
        if after is not None:
            if order == "name-desc":
                ascending = self._workspace_order(listings, "name")
                start = len(entries) - bisect_left(ascending, after, key=key)
            else:
                start = bisect_right(entries, after, key=key)
        total, page, positive_count = _filtered_page(
            entries, start, limit + 1, filters, links, members, image_tags
        )
        if not needed and after is not None:
            positive_count = _COUNT_OMITTED
        has_more = len(page) > limit
        page = page[:limit]
        next_cursor = _workspace_cursor(key(page[-1]), scope) if has_more else None
        return (
            [(entry.path, entry.mtime, entry.root, entry.index) for entry in page],
            total,
            has_more,
            next_cursor,
            positive_count,
        )

    def _select_workspace_matches(
        self, listings: list[tuple], filters: dict, cap: int
    ) -> ListingSelection:
        """Every admitted entry, up to *cap*, as a ListingSelection (blocking).

        The links are read strictly: the positives must be the listing's own.
        """
        entries, _key, links, image_tags, members = self._workspace_admission(
            listings, filters, read_links=True, strict=True
        )
        targets, positives, unlinked, scopes = [], {}, 0, {}
        for _position, entry, match in _admitted_entries(
            entries, filters, links, members, image_tags
        ):
            if len(targets) == cap:
                return ListingSelection(targets, list(positives), unlinked, True)
            scope = scopes.get(entry.index)
            if scope is None:
                scope = scopes[entry.index] = self._gallery_root_scope(
                    entry.root, entry.index
                )
            relative = entry.path.relative_to(entry.root).as_posix()
            targets.append((entry.index, scope, relative))
            if match is None or match[2] is None:
                unlinked += 1
            else:
                positives.setdefault(match[2])
        return ListingSelection(targets, list(positives), unlinked, False)

    async def resolve_listing_filter(
        self, params: Mapping, cap: int = MAX_LISTING_SELECTION
    ) -> ListingSelection:
        """The images a parse_listing_filter result admits right now (#392).

        Raises ValueError for a root outside the current roots,
        ListingSourceChanged when the filter's ``root_scope`` no longer names
        its root (the sources were renumbered since the listing), and
        CollectionMissing when its ``collection`` no longer exists (checked
        before any scan and again after the members are read).
        """
        output_dirs = list(self._get_all_output_dirs())
        root_index = _workspace_root(params, output_dirs)
        error = self._gallery_scope_error(params, output_dirs)
        if error is not None:
            if error.status == 409:
                raise ListingSourceChanged()
            raise ValueError("Invalid gallery root scope")
        filters = _workspace_filters(params)
        if filters["collection"] is not None and not await self._run_in_executor(
            self.db.collection_exists, filters["collection"]
        ):
            raise CollectionMissing("No such collection")
        listings = await self._workspace_listings(output_dirs, root_index)
        return await self._run_in_executor(
            self._select_workspace_matches, listings, filters, cap
        )

    async def serve_image(self, request):
        """Serve the actual image file using streamed FileResponse."""
        try:
            image_id = int(request.match_info["image_id"])
            image = await self._run_in_executor(self.db.get_image_by_id, image_id)

            if not image:
                return web.json_response(
                    {"success": False, "error": "Image not found"}, status=404
                )

            image_path = Path(image["image_path"])
            lora_dirs = await self._run_in_executor(self._lora_image_dirs)
            allowed_dirs = list(self._get_all_output_dirs()) + lora_dirs

            denied = _media_access_error(image_path, allowed_dirs)
            if denied is not None:
                return denied

            if not image_path.is_file():
                return web.json_response(
                    {"success": False, "error": "Image file not found"}, status=404
                )

            return self._file_response(image_path)

        except ValueError:
            return web.json_response(
                {"success": False, "error": "Invalid image ID"}, status=400
            )
        except Exception as e:
            self.logger.error(f"Serve image error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    def _media_roots(self, outputs=None):
        """The roots a prompt's linked media is listed and inspected under.

        The output folders first (``outputs``, else ``_get_all_output_dirs``),
        so every output ``root_index`` keeps its meaning, then, while the
        LoRA integration is on, the guarded LoraManager folders and the
        CivitAI download cache (``_lora_image_dirs``, #355). Media under those
        is linked-only: ``serve_output_image`` serves a file under them only
        when the database records it, and no preview file is made there.
        """
        roots = list(self._get_all_output_dirs() if outputs is None else outputs)
        seen = {_canonical(root) for root in roots}
        for extra in self._lora_image_dirs():
            key = _canonical(extra)
            if key not in seen:
                seen.add(key)
                roots.append(Path(extra))
        return roots

    def _lora_image_dirs(self):
        """Extra directories served when the LoRA integration is enabled.

        LoraManager's guarded LoRA roots, sidecar root and example-images
        root (``lora_manager_layout.media_roots``, #355) and PromptManager's
        CivitAI download cache. An unusable LoraManager layout gives the
        default LoRA roots and the cache. The layout comes from the layout
        cache (``lora_utils.cached_lora_manager_layout``, #362), which reloads
        when LoraManager's settings files change; blocking, so the routes
        call it in the executor.
        """
        try:
            from ..config import IntegrationConfig

            if not IntegrationConfig.LORA_MANAGER_ENABLED:
                return []
            from ..lora_utils import (
                cached_lora_manager_layout,
                get_lora_image_cache_dir,
            )

            configured = IntegrationConfig.LORA_MANAGER_PATH or ""
            install, layout = cached_lora_manager_layout(configured)
            if configured.strip() and install is None:
                # A configured path comes from config.json, which can be hand
                # edited: only a LoraManager install under custom_nodes may
                # widen the serving allow-list. An empty path keeps the
                # auto-detection, which itself only scans custom_nodes.
                return []

            dirs = [Path(d) for d in layout.media_roots]
            # Named, not made: listing and serving never create the cache.
            dirs.append(Path(get_lora_image_cache_dir(create=False)))
            return dirs
        except Exception:
            # LoRA integration is optional; never widen access on failure.
            return []

    @staticmethod
    def _file_response(path):
        response = web.FileResponse(path)
        response.headers["Cache-Control"] = "public, max-age=3600"
        # #317: name the image types explicitly; the platform's mimetypes
        # table (the Windows registry) may not know .webp or .jxl.
        content_type = thumbnail_settings.content_type(path)
        if content_type is not None:
            response.headers["Content-Type"] = content_type
        return response

    @staticmethod
    def _select_root(output_dirs, root_param):
        """Narrow *output_dirs* to the ``?root=N`` entry when it is valid."""
        if root_param is None:
            return output_dirs
        try:
            idx = int(root_param)
        except ValueError:
            return []
        if 0 <= idx < len(output_dirs):
            return [output_dirs[idx]]
        return []

    async def serve_output_image(self, request):
        """Serve a media file from the ComfyUI output folder(s).

        With ``?root=N`` past the output folders (a LoRA-linked root,
        ``_media_roots``, #355) only a file the database records is served,
        and no preview is served or made there.
        """
        try:
            outputs = list(self._get_all_output_dirs())
            root_param = request.query.get("root")
            roots = outputs
            if root_param is not None and re.fullmatch(r"[0-9]+", root_param):
                if int(root_param) >= len(outputs):
                    # Only a LoRA-linked request reads LoraManager's layout;
                    # output media (and its previews) never waits on it.
                    roots = await self._run_in_executor(self._media_roots, outputs)
            scope_error = self._gallery_scope_error(request.query, roots)
            if scope_error is not None:
                return scope_error
            filepath = request.match_info["filepath"]
            if _has_traversal(filepath):
                return _forbidden()
            suffix = Path(filepath).suffix.lower()
            if suffix not in SERVABLE_EXTENSIONS and not (
                # A JPEG XL preview is served from thumbnails/ only (#317).
                suffix in thumbnail_settings.THUMBNAIL_EXTENSIONS
                and _is_thumbnail_cache_path(filepath)
            ):
                return _forbidden("Only media files can be served")

            output_dirs = self._select_root(roots, root_param)
            if not output_dirs:
                return _forbidden("No allowed image directories configured")
            if len(outputs) <= int(root_param or 0):
                return await self._serve_linked_only(
                    output_dirs[0], filepath, int(root_param), len(outputs)
                )

            for output_path in output_dirs:
                image_path = output_path / filepath
                if not image_path.exists():
                    continue
                # A symlink that points outside the root is a hard deny, not
                # a fall-through to the next root.
                if not _is_within(image_path, output_path):
                    return _forbidden()
                if image_path.is_file():
                    return self._file_response(image_path)

            # A listed preview that was not made yet is made now (#249).
            preview = await self._serve_missing_thumbnail(output_dirs, filepath)
            if preview is not None:
                return preview

            # ComfyUI's cache middleware would otherwise let browsers cache this
            # miss for an hour, hiding a thumbnail generated a moment later.
            return web.json_response(
                {"success": False, "error": "Image file not found"},
                status=404,
                headers={"Cache-Control": "no-store"},
            )

        except Exception as e:
            self.logger.error(f"Serve output image error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    def _is_recorded_media(self, path) -> bool:
        """Whether the database records ``path`` as a linked image.

        Imported local media is stored resolved; downloaded CivitAI images
        keep the cache folder's own spelling (a symlinked or junctioned data
        folder), so either spelling names the record.
        """
        spellings = dict.fromkeys([str(path), os.path.realpath(path)])
        return any(
            self.db.get_prompt_id_for_image(spelling) is not None
            for spelling in spellings
        )

    async def _linked_only_error(self, index: int, outputs: int, path):
        """The one gate for a LoRA-linked root (``_media_roots`` past the
        ``outputs`` output folders, #355): only a file the database records
        is served, inspected or revealed there; anything else is 403.
        Output roots pass (None)."""
        if index < outputs:
            return None
        if not await self._run_in_executor(self._is_recorded_media, path):
            return _forbidden()
        return None

    async def _serve_linked_only(self, root, filepath, index, outputs):
        """A file under a LoRA-linked root, served only when it is recorded."""
        if _is_thumbnail_cache_path(filepath):
            return _forbidden()
        image_path = root / filepath
        if not _is_within(image_path, root):
            return _forbidden()
        if not image_path.is_file():
            return web.json_response(
                {"success": False, "error": "Image file not found"}, status=404
            )
        denied = await self._linked_only_error(index, outputs, image_path)
        if denied is not None:
            return denied
        return self._file_response(image_path)

    async def rescan_gallery(self, request):
        """Rewalk every output root now and answer with the new listing.

        Readers are served the shared listing and refresh it in the
        background (``gallery_listing.REFRESH_AFTER``); the gallery's Rescan
        button needs files added or removed on disk to show up at once, so it
        waits for a walk that started after the click and fails when it does.
        """
        try:
            output_dirs = list(self._get_all_output_dirs())
            total = 0
            for output_path in output_dirs:
                total += len((await self._listing(output_path, fresh=True)).files)
            return web.json_response(
                {"success": True, "total": total, "roots": len(output_dirs)}
            )
        except Exception:
            self.logger.exception("Gallery rescan error")
            return web.json_response(
                {"success": False, "error": "Failed to rescan the gallery"}, status=500
            )

    async def _root_subfolders(self, output_path, include_ancestors):
        """The distinct folders holding listed files under one output root.

        Built with the walk, in the executor (gallery_listing.folder_sets).
        """
        listing = await self._listing(output_path)
        if include_ancestors:
            return listing.folders_with_ancestors
        return listing.folders

    async def get_gallery_subfolders(self, request):
        """Get distinct subfolders from gallery output directories.

        In workspace mode every root also lists its own folders, so the
        Refine dialog can offer only the chosen Source's folders.
        """
        try:
            output_dirs = list(self._get_all_output_dirs())
            workspace = request.query.get("mode") == "workspace"
            include_ancestors = (
                request.query.get("include_ancestors", "").lower() == "true"
            )
            selected = None
            if workspace:
                try:
                    selected = _workspace_root(request.query, output_dirs)
                except ValueError as exc:
                    return bad_request(str(exc))
            walked = [
                await self._root_subfolders(output_path, include_ancestors)
                for output_path in output_dirs
            ]
            chosen = walked if selected is None else [walked[selected]]
            subfolders = set().union(*chosen)
            payload = {"success": True, "subfolders": sorted(subfolders)}
            if workspace:
                payload["roots"] = [
                    {
                        "index": index,
                        "label": _public_path(root),
                        "subfolders": sorted(walked[index]),
                    }
                    for index, root in enumerate(output_dirs)
                ]
            return web.json_response(payload)
        except Exception as e:
            self.logger.error(f"Subfolders error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    async def generate_thumbnails(self, request):
        """Generate every thumbnail in one executor job (POST, blocking).

        Holds the registry as guarded work: refused while a record-changing
        job runs, and such jobs are refused while it runs. A body that cannot
        be read is refused before the hold, so it is never listed as a job.
        The legacy ``quality`` preset is accepted and ignored: previews follow
        the server's thumbnail settings (#317 R3).
        """
        data, error = await _json_body_or_error(request)
        if error is not None:
            return error
        return await self._run_held(
            THUMBNAILS_JOB_KIND,
            THUMBNAIL_GENERATION_TITLE,
            "guarded",
            lambda: self._generate_thumbnails(data),
        )

    async def _generate_thumbnails(self, data):
        try:
            output_dirs = self._get_all_output_dirs()
            if not output_dirs:
                return web.json_response(
                    {"success": False, "error": "ComfyUI output directory not found"},
                    status=404,
                )

            result = await self._run_in_executor(
                self._generate_gallery_thumbnails_sync, output_dirs
            )
            return web.json_response(result)

        except Exception as e:
            self.logger.error(f"Generate thumbnails error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    def _generate_thumbnails_sync(self, output_path, thumbnails_dir, settings=None):
        """Blocking thumbnail generation loop (run in executor)."""
        settings = writer_settings(settings)
        start = _time.monotonic()
        plan = self._plan_thumbnails(output_path, thumbnails_dir, settings=settings)
        payload = self._execute_thumbnail_plan(plan, settings, start)
        payload["thumbnails_path"] = _public_path(thumbnails_dir)
        return payload

    def _generate_gallery_thumbnails_sync(
        self, output_dirs: list[Path], settings=None
    ) -> dict:
        settings = writer_settings(settings)
        start = _time.monotonic()
        plan = self._plan_gallery_thumbnails(output_dirs, settings=settings)
        payload = self._execute_thumbnail_plan(plan, settings, start)
        payload["thumbnails_path"] = _public_path(output_dirs[0] / "thumbnails")
        return payload

    def _execute_thumbnail_plan(self, plan: dict, settings, start: float) -> dict:
        targets = plan["targets"]
        stats = _new_thumbnail_stats()
        stats["skipped"] = plan["skipped"]
        for result in self._generate_batch(targets, settings):
            _record_thumbnail_result(stats, result)
        payload = _thumbnail_complete_payload(
            len(targets) + plan["skipped"] + plan["remaining"],
            stats,
            start,
            remaining=plan["remaining"],
            processed=len(targets),
        )
        payload["success"] = True
        self.logger.info(payload["message"])
        return payload

    def _plan_gallery_thumbnails(
        self,
        output_dirs: list[Path],
        mode: str = "missing",
        stop=None,
        settings=None,
        exclude=None,
    ) -> dict:
        """Plan every gallery root with one shared per-batch generation cap.

        See ``_plan_thumbnails``; ``fresh_files`` keeps at most
        MAX_FRESH_LISTED files across the roots. ``stop()`` true ends the walk
        at the next file (a cancelled job); the partial plan is returned.
        *exclude* (a set of source paths) leaves files a job already worked
        on out of a later batch's plan.
        """
        settings = writer_settings(settings)
        combined = {key: 0 for key in _PLAN_COUNTS}
        combined.update(targets=[], fresh_files=[])
        for root in output_dirs:
            if stop is not None and stop():
                break
            root = Path(root)
            plan = self._plan_thumbnails(
                root,
                root / "thumbnails",
                mode=mode,
                stop=stop,
                settings=settings,
                exclude=exclude,
            )
            available = max(0, MAX_THUMBNAIL_FILES - len(combined["targets"]))
            combined["targets"].extend(plan["targets"][:available])
            for key in _PLAN_COUNTS:
                combined[key] += plan[key]
            room = max(0, MAX_FRESH_LISTED - len(combined["fresh_files"]))
            combined["fresh_files"].extend(plan["fresh_files"][:room])
            combined["remaining"] += max(0, len(plan["targets"]) - available)
        return combined

    async def get_thumbnail_status(self, request: web.Request) -> web.Response:
        """Count previews off-loop without generating files.

        ``pending`` previews are missing, ``outdated`` ones exist in another
        format than the one written now (Convert outdated, #317) and ``ready``
        counts every file with a preview, outdated or not.
        """
        try:
            output_dirs = self._get_all_output_dirs()
            plan = await self._run_in_executor(
                self._plan_gallery_thumbnails, output_dirs
            )
            pending = len(plan["targets"]) + plan["remaining"]
            collection = json.dumps(sorted(str(_canonical(p)) for p in output_dirs))
            return web.json_response(
                {
                    "success": True,
                    "total_images": pending + plan["skipped"],
                    "pending": pending,
                    "ready": plan["skipped"],
                    "outdated": plan["outdated"],
                    "format": writer_settings().format,
                    # Files whose automatic preview failed this session (#249).
                    "failed": self._auto_thumbnail_failures(),
                    "collection_id": hashlib.sha256(collection.encode()).hexdigest()[
                        :16
                    ],
                },
                headers={"Cache-Control": "no-store"},
            )
        except Exception as exc:
            self.logger.exception("Thumbnail status failed")
            return web.json_response(
                {"success": False, "error": _safe_error(exc)}, status=500
            )

    def _plan_thumbnails(
        self,
        output_path,
        thumbnails_dir,
        mode="missing",
        stop=None,
        settings=None,
        exclude=None,
    ):
        """Decide which media files need a preview (blocking walk).

        Each file is ``fresh`` (its preview in the format written now is at
        least as new as the file, and no other owned preview is left),
        ``outdated`` (an owned preview in another format: a legacy
        ``_thumb.png`` or a format switched away from, #317) or ``missing``.
        ``mode`` picks the targets: ``missing`` the missing ones (fresh and
        outdated are ``skipped`` and never queued, so a large library that is
        mostly done only pays for the files that changed), ``rebuild`` every
        file, ``convert`` the outdated ones (missing ones are left to the
        missing run). Returns ``{"targets": [(src, dst, is_video)], "skipped",
        "remaining", "fresh", "outdated", "missing", "fresh_files": [src]}``;
        ``fresh_files`` lists the first MAX_FRESH_LISTED fresh files. The walk
        is bounded like the gallery listing (MAX_SCAN_DEPTH, MAX_GALLERY_FILES,
        no symlinks), each preview folder is listed once, and at most
        MAX_THUMBNAIL_FILES targets are queued per batch (a job plans again
        after each batch); ``remaining`` counts the targets beyond that cap.
        Sources in *exclude* (worked on by an earlier batch) are left out.
        ``stop()`` true ends the walk before the next file.
        """
        settings = writer_settings(settings)
        output_path = Path(output_path)
        thumbnails_dir = Path(thumbnails_dir)
        plan = {key: 0 for key in _PLAN_COUNTS}
        plan.update(targets=[], fresh_files=[])
        folders = {}
        for src in _iter_media_files(output_path, MAX_GALLERY_FILES):
            if stop is not None and stop():
                break
            if exclude and src in exclude:
                continue
            rel_no_ext = src.relative_to(output_path).with_suffix("")
            stem = f"{rel_no_ext.name}{thumbnail_settings.THUMB_SUFFIX}"
            dst = thumbnails_dir / rel_no_ext.parent / (stem + settings.extension)
            if not _is_within(dst, thumbnails_dir):
                self.logger.warning(f"Skipping thumbnail outside safe dir: {src.name}")
                continue
            state = _preview_state(src, dst.parent, stem, settings, folders)
            plan[state] += 1
            target = (
                mode == "rebuild"
                or (mode == "missing" and state == "missing")
                or (mode == "convert" and state == "outdated")
            )
            if not target:
                plan["skipped"] += 1
                if state == "fresh" and len(plan["fresh_files"]) < MAX_FRESH_LISTED:
                    plan["fresh_files"].append(src)
                continue
            if len(plan["targets"]) >= MAX_THUMBNAIL_FILES:
                plan["remaining"] += 1
                continue
            is_video = src.suffix.lower() in VIDEO_EXTENSIONS
            plan["targets"].append((src, dst, is_video))
        if plan["remaining"]:
            self.logger.warning(
                f"Thumbnail batch capped at {MAX_THUMBNAIL_FILES} files; "
                f"{plan['remaining']} more are left for the next batch"
            )
        return plan

    def _thumbnail_targets(self, output_path, thumbnails_dir):
        """Pending (src, dst, is_video) triples; see _plan_thumbnails."""
        return self._plan_thumbnails(output_path, thumbnails_dir)["targets"]

    def _generate_batch(self, targets, settings, **options):
        """Create thumbnails for ``targets`` on the configured worker threads.

        Blocking; returns one result dict per target, in order. ``options``
        go to ``_generate_one`` (``rebuild``, ``stop``).
        """

        def one(target):
            src, dst, is_video = target
            return self._generate_one(src, dst, settings, is_video, **options)

        return map_parallel(one, targets, _worker_threads())

    def _generate_one(
        self,
        src,
        dst,
        settings,
        is_video,
        rebuild=False,
        stop=None,
        guard=None,
        track=None,
        placeholder=True,
    ):
        """Create one thumbnail (blocking). Never raises; returns a result dict.

        *settings* (``thumbnail_settings.ThumbnailSettings``) give the format,
        quality and edge; *dst* takes the format's extension. ``stop()`` true
        before the file starts gives ``action: "cancelled"`` and touches
        nothing (a job's file boundary). ``rebuild`` writes a fresh preview
        again and never replaces an existing video preview with the
        placeholder. Every write is atomic and verified, so a failure keeps
        the old preview. ``guard()`` false just before the rename gives
        ``action: "error"`` with ``held: True`` and writes nothing;
        ``track(process)`` is handed every encoder process (both for automatic
        previews, #249). An error result carries ``decode: True`` when the
        file itself cannot be decoded (an I/O error does not). A generated
        result names the file written as ``path``. Without ``placeholder``
        (Convert outdated) a video with no decodable frame fails instead.
        """
        result = {
            "file": src.name,
            "dir": src.parent.name,
            "type": "video" if is_video else "image",
        }
        dst = Path(dst)
        if dst.suffix != settings.extension:
            dst = dst.with_suffix(settings.extension)
        try:
            if stop is not None and stop():
                return {**result, "action": "cancelled"}
            if not src.is_file():
                return {**result, "action": "error", "error": "File no longer exists"}
            if not rebuild and _thumbnail_is_fresh(src, dst):
                return {**result, "action": "skipped", "path": str(dst)}
            dst.parent.mkdir(parents=True, exist_ok=True)
            if is_video:
                made = self._generate_video_thumbnail(
                    src,
                    dst,
                    settings,
                    keep_existing=rebuild,
                    guard=guard,
                    track=track,
                    placeholder=placeholder,
                )
                if not made:
                    return {
                        **result,
                        "action": "error",
                        "error": "Video decode failed",
                        "decode": True,
                    }
                written = made if isinstance(made, Path) else dst
            else:
                written = _write_image_thumbnail(src, dst, settings, guard, track)
            return {**result, "action": "generated", "path": str(written)}
        except PreviewWriteHeld:
            return {**result, "action": "error", "error": PREVIEW_HELD, "held": True}
        except UnidentifiedImageError:
            return {
                **result,
                "action": "error",
                "error": "Not a valid image file",
                "decode": True,
            }
        except PermissionError as e:
            message = _preview_write_error(e)
            self.logger.warning(f"Thumbnail failed for {src.name}: {message}")
            return {**result, "action": "error", "error": message}
        except Exception as e:
            self.logger.warning(f"Thumbnail failed for {src.name}", exc_info=True)
            failed = {**result, "action": "error", "error": _safe_error(e)}
            if _is_decode_error(e):
                failed["decode"] = True
            return failed

    async def _emit_sse(self, response, event, data) -> bool:
        """Write one SSE frame. Returns False once the client has gone away."""
        try:
            frame = f"event: {event}\ndata: {json.dumps(data)}\n\n"
            await response.write(frame.encode("utf-8"))
            return True
        except Exception as e:
            self.logger.warning(f"Failed to send SSE message: {e}")
            return False

    def _generate_video_thumbnail(
        self,
        video_path,
        thumbnail_path,
        settings,
        keep_existing=False,
        *,
        guard=None,
        track=None,
        placeholder=True,
    ):
        """Write a video's preview; returns the path written, or False.

        The frame comes from OpenCV, else ffmpeg, else the placeholder frame:
        a decoder that cannot open or read the clip (an AV1 file OpenCV was
        built without) falls through to the next one (#249 I1). Every frame
        is fitted inside the edge and encoded in *settings*' format like an
        image's preview (#317). With ``keep_existing`` an existing preview is
        kept, and the run fails, when no frame could be decoded, rather than
        replaced by the placeholder. A PermissionError while replacing the
        preview propagates (``_generate_one`` names it), as do
        PreviewWriteHeld when ``guard()`` refuses the rename and
        EncoderClosed at shutdown. ``track(process)`` is handed the ffmpeg
        process so a shutdown can kill it. Without ``placeholder`` no
        placeholder is written: the run fails when no frame decodes.
        """
        try:
            frame = self._opencv_frame(video_path, settings.size)
            source = "OpenCV"
            if frame is None:
                frame = self._ffmpeg_frame(video_path, settings.size, track)
                source = "ffmpeg"
            if frame is not None:
                written = write_thumbnail(
                    thumbnail_frame(frame, settings.edge),
                    thumbnail_path,
                    settings,
                    guard=guard,
                    track=track,
                )
                self.logger.debug(
                    f"Generated video thumbnail using {source}: {written}"
                )
                return written
            if not placeholder or (keep_existing and thumbnail_path.exists()):
                self.logger.warning(
                    f"No frame decoded for {video_path.name}; keeping its preview"
                )
                return False
            # Last resort: the placeholder frame.
            try:
                written = write_thumbnail(
                    _video_placeholder_image(settings.size),
                    thumbnail_path,
                    settings,
                    guard=guard,
                    track=track,
                )
                self.logger.debug(f"Generated placeholder video thumbnail: {written}")
                return written
            except (PermissionError, PreviewWriteHeld, EncoderClosed):
                raise
            except Exception as e:
                self.logger.warning(
                    f"Failed to create placeholder thumbnail for {video_path}: {e}"
                )
                return False
        except (PermissionError, PreviewWriteHeld, EncoderClosed):
            raise
        except Exception as e:
            self.logger.error(
                f"Video thumbnail generation failed for {video_path}: {e}"
            )
            return False

    def _opencv_frame(self, video_path, thumbnail_size):
        """A frame about 10% in, fitted to *thumbnail_size*, or None (no
        OpenCV, or it could not open or read the clip)."""
        try:
            import cv2
        except ImportError:
            return None
        try:
            cap = cv2.VideoCapture(str(video_path))
            try:
                if not cap.isOpened():
                    return None
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(1, int(frame_count * 0.1)))
                ret, frame = cap.read()
            finally:
                cap.release()
            if not ret or frame is None:
                return None
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            img.thumbnail(thumbnail_size, Image.Resampling.LANCZOS)
            return img
        except Exception as e:
            self.logger.debug(f"OpenCV could not read {video_path}: {e}")
            return None

    def _ffmpeg_frame(self, video_path, thumbnail_size, track=None):
        """ffmpeg's frame at 1 s, fitted to *thumbnail_size*, as a PIL image
        (a PNG on stdout, never a temp file), or None. EncoderClosed (the
        shutdown) propagates."""
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            # Read as a file, never as a protocol URL (review M6).
            f"file:{video_path}",
            "-ss",
            "00:00:01",
            "-vframes",
            "1",
            "-vf",
            # Fit inside the edge, never upscale a small clip.
            f"scale='min(iw,{thumbnail_size[0]})':'min(ih,{thumbnail_size[1]})'"
            ":force_original_aspect_ratio=decrease",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ]
        try:
            done = run_encoder(cmd, track=track)
        except EncoderClosed:
            raise
        except (OSError, subprocess.SubprocessError) as e:
            self.logger.debug(f"ffmpeg could not read {video_path}: {e}")
            return None
        if done.returncode != 0 or not done.stdout:
            detail = done.stderr or b""
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", "replace")
            detail = detail[-300:]
            self.logger.warning(f"ffmpeg failed for {video_path}: {detail}")
            return None
        try:
            frame = Image.open(io.BytesIO(done.stdout))
            frame.load()
            return frame
        except Exception as e:
            self.logger.warning(f"ffmpeg gave no readable frame for {video_path}: {e}")
            return None

    async def clear_thumbnails(self, request: web.Request) -> web.Response:
        """Clear generated thumbnails across gallery roots, preserving originals.

        Invalid thumbnail directories return 400; unavailable gallery roots
        return 404, and unexpected filesystem failures return 500. Holds the
        registry as guarded work, like generation.
        """
        return await self._run_held(
            THUMBNAILS_JOB_KIND,
            THUMBNAIL_CLEAR_TITLE,
            "guarded",
            self._clear_thumbnails,
        )

    async def _clear_thumbnails(self) -> web.Response:
        try:
            output_dirs = self._get_all_output_dirs()
            if not output_dirs:
                return web.json_response(
                    {"success": False, "error": "ComfyUI output directory not found"},
                    status=404,
                )
            cleared_count, cleared_size = await self._run_in_executor(
                self._clear_gallery_thumbnails_sync, output_dirs
            )

            def format_size(bytes_size: int) -> str:
                size = float(bytes_size)
                for unit in ["B", "KB", "MB", "GB"]:
                    if size < 1024.0:
                        return f"{size:.1f} {unit}"
                    size /= 1024.0
                return f"{size:.1f} TB"

            size_formatted = format_size(cleared_size)
            self.logger.info(
                f"Thumbnail cleanup: cleared {cleared_count} files ({size_formatted})"
            )
            return web.json_response(
                {
                    "success": True,
                    "cleared_files": cleared_count,
                    "cleared_size": cleared_size,
                    "cleared_size_formatted": size_formatted,
                    "message": f"Cleared {cleared_count} thumbnail files "
                    f"({size_formatted})",
                }
            )
        except ValueError as exc:
            self.logger.error(f"Thumbnail path validation failed: {exc}")
            return web.json_response(
                {"success": False, "error": _safe_error(exc)}, status=400
            )
        except Exception as exc:
            self.logger.error(f"Clear thumbnails error: {exc}")
            return web.json_response(
                {"success": False, "error": _safe_error(exc)}, status=500
            )

    def sweep_thumbnail_temps(self):
        """Remove the old temp files crashed preview writes left in every
        root's ``thumbnails/`` folder (blocking; run at startup on its own
        thread). Returns how many went."""
        removed = 0
        try:
            output_dirs = self._get_all_output_dirs()
        except Exception as exc:
            self.logger.debug(f"No roots to sweep for preview temp files: {exc}")
            return 0
        for output_path in output_dirs:
            folder = Path(output_path) / "thumbnails"
            if folder.is_symlink() or not folder.is_dir():
                continue
            removed += sweep_stray_temps(folder)[0]
        if removed:
            self.logger.info(f"Removed {removed} interrupted preview temp files")
        return removed

    def _clear_gallery_thumbnails_sync(
        self, output_dirs: list[Path]
    ) -> tuple[int, int]:
        """Validate all thumbnail directories before deleting generated files.

        Raises ValueError for symlinked or out-of-root thumbnail directories.
        Filesystem errors during path validation propagate to the route.
        """
        directories = []
        for output_path in output_dirs:
            output_path = Path(output_path)
            thumbnails_dir = output_path / "thumbnails"
            if not thumbnails_dir.exists() and not thumbnails_dir.is_symlink():
                continue
            if (
                thumbnails_dir.is_symlink()
                or not _is_within(thumbnails_dir, output_path)
                or _canonical(thumbnails_dir) == _canonical(output_path)
            ):
                raise ValueError(
                    "Safety check failed: invalid thumbnails directory path"
                )
            directories.append(thumbnails_dir)

        cleared_count = 0
        cleared_size = 0
        for thumbnails_dir in directories:
            # Temp files a crashed write left (review I1): the clear holds
            # every preview write, so none is in use.
            temps, temp_bytes = sweep_stray_temps(thumbnails_dir, min_age=0)
            cleared_count += temps
            cleared_size += temp_bytes
            for root, _dirs, files in os.walk(thumbnails_dir):
                for name in files:
                    path = Path(root) / name
                    if (
                        "_thumb" not in name.lower()
                        or path.suffix.lower()
                        not in thumbnail_settings.THUMBNAIL_EXTENSIONS
                        or path.is_symlink()
                    ):
                        continue
                    try:
                        size = path.stat().st_size
                        path.unlink()
                        cleared_count += 1
                        cleared_size += size
                    except OSError as exc:
                        self.logger.warning(f"Failed to delete thumbnail {path}: {exc}")
            for root, _dirs, _files in os.walk(thumbnails_dir, topdown=False):
                if Path(root) != thumbnails_dir:
                    try:
                        Path(root).rmdir()
                    except OSError as exc:
                        self.logger.debug(
                            "Keeping thumbnail directory %s: %s", root, exc
                        )
        return cleared_count, cleared_size

    @staticmethod
    def _resolve_client_media_path(raw_path, allowed_dirs):
        """Absolute path for a client-supplied media path, or None when it
        does not name a file inside an allowed root.

        Absolute paths are used as given (the caller still runs the access
        check). Relative paths are the forms this API hands out: relative to
        a gallery root (``sub/a.png``) or to a ComfyUI anchor
        (``output/sub/a.png``), and must resolve inside an allowed root.
        """
        if os.path.isabs(raw_path):
            return raw_path
        from ..config import GalleryConfig

        for base in [*allowed_dirs, *GalleryConfig.path_anchors()]:
            candidate = Path(base) / raw_path
            if candidate.is_file() and any(
                _is_within(candidate, root) for root in allowed_dirs
            ):
                return str(candidate)
        return None

    async def link_image_to_prompt(self, request):
        """Link a generated image (inside an allowed directory) to a prompt."""
        try:
            data, error = await _json_body_or_error(request)
            if error is not None:
                return error
            prompt_id = data.get("prompt_id")
            image_path = data.get("image_path")
            metadata = data.get("metadata", {})

            if not prompt_id or not isinstance(image_path, str) or not image_path:
                return bad_request("prompt_id and image_path are required")
            if not os.path.isabs(image_path) and _has_traversal(image_path):
                return _forbidden()

            allowed_dirs = list(self._get_all_output_dirs())
            resolved = self._resolve_client_media_path(image_path, allowed_dirs)
            if resolved is None:
                if Path(image_path).suffix.lower() not in SERVABLE_EXTENSIONS:
                    return _forbidden("Only media files can be served")
                return web.json_response(
                    {"success": False, "error": "Image file not found"}, status=404
                )
            image_path = resolved

            denied = _media_access_error(image_path, allowed_dirs)
            if denied is not None:
                return denied

            if not os.path.isfile(image_path):
                return web.json_response(
                    {"success": False, "error": "Image file not found"}, status=404
                )

            image_id = await self._run_in_executor(
                self.db.link_image_to_prompt, prompt_id, image_path, metadata
            )

            return web.json_response(
                {
                    "success": True,
                    "image_id": image_id,
                    "message": "Image linked successfully",
                }
            )

        except Exception as e:
            self.logger.error(f"Link image error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    async def update_image_favorite(self, request, *, favorite: bool):
        """PUT/DELETE one favorite, named by ``{root_index, path}``.

        A favorite is set only on a file in the root's listing; clearing also
        accepts a well-formed path that is no longer listed, so a deleted
        file's favorite can still be removed. Both are idempotent.
        """
        data, error = await _json_body_or_error(request)
        if error is not None:
            return error
        roots = list(self._get_all_output_dirs())
        target, error = _favorite_target(data, roots)
        if error is not None:
            return error
        index, relative = target
        root = roots[index]
        relative = Path(relative).as_posix()
        try:
            files = await self._get_gallery_files(root)
            listing = await self._run_in_executor(self._favorite_listing, root, files)
            key = _favorite_relative_key(relative)
            listed = listing.get(key)
            if listed is None and favorite:
                return web.json_response(
                    {"success": False, "error": "Image file not found"}, status=404
                )
            root_key = _favorite_root_key(root)
            if favorite and max(len(root_key), len(key)) > MAX_KEY_LENGTH:
                # A stored key past the schema bound would fail the next
                # startup or restore validation.
                return _forbidden("Path is too long to favorite")
            update = (
                self.db.set_image_favorite if favorite else self.db.clear_image_favorite
            )
            await self._run_in_executor(update, root_key, key)
        except Exception:
            self.logger.exception("Favorite update failed")
            return web.json_response(
                {"success": False, "error": "Favorite update failed"}, status=500
            )
        return web.json_response(
            {
                "success": True,
                "favorite": favorite,
                "root_index": index,
                "path": listed or relative,
            }
        )

    # Swapped by tests and the disposable browser server; never a shell.
    _reveal_opener = staticmethod(reveal_in_file_manager)
    # One per process (a class attribute): single flight plus a cooldown.
    _reveal_gate = RevealGate()

    @staticmethod
    def _reveal_path(relative: str, root: Path) -> Path | None:
        """The real path of a regular media file under *root*, else None."""
        resolved = ImageRoutesMixin._resolve_client_media_path(relative, [root])
        if resolved is None or _media_access_error(resolved, [root]) is not None:
            return None
        real = Path(os.path.realpath(resolved))
        return real if real.is_file() and _is_within(real, root) else None

    async def reveal_image(self, request):
        """POST ``{root_index, root_scope, path}``: show the file in its folder.

        Local-only (a same-origin browser request from a loopback client, no
        forwarding header) and JSON-only: the browser must run on the
        computer whose file manager opens. One reveal at a time (429 busy).
        """
        if not is_local_request(request.remote, request.headers, request.scheme):
            return _not_local()
        if request.content_type != "application/json":
            # A non-JSON POST is a CORS "simple" request with no preflight.
            return web.json_response(
                {
                    "success": False,
                    "code": "unsupported-media-type",
                    "error": "Send the request as application/json.",
                },
                status=415,
            )
        data, error = await _json_body_or_error(request)
        if error is not None:
            return error
        target, error = _reveal_target(data)
        if error is not None:
            return error
        index, scope, relative = target
        # A LoRA-linked root too (#355), for the files the import linked only.
        outputs = list(self._get_all_output_dirs())
        roots = await self._run_in_executor(self._media_roots, outputs)
        error = self._gallery_scope_error(
            {"root_scope": scope, "root": str(index)}, roots
        )
        if error is not None:
            return error
        root = roots[index]
        if Path(relative).suffix.lower() not in SERVABLE_EXTENSIONS:
            return _forbidden("Only media files can be shown")
        denied = await self._linked_only_error(index, len(outputs), root / relative)
        if denied is not None:
            return denied
        gate = self._reveal_gate
        if not gate.try_enter():
            return web.json_response(
                {
                    "success": False,
                    "code": "busy",
                    "error": "The folder is already opening.",
                },
                status=429,
            )
        try:
            return await self._reveal(relative, root, linked_only=index >= len(outputs))
        finally:
            gate.leave()

    async def _reveal(self, relative: str, root: Path, *, linked_only=False):
        """Resolve and open, inside the reveal gate.

        Under a LoRA-linked root the file that would open is checked again
        (it may resolve through a path anchor): only a recorded one opens.
        """
        try:
            path = await self._run_in_executor(self._reveal_path, relative, root)
        except (OSError, ValueError) as exc:
            # ENAMETOOLONG and the like: the client's path is not logged.
            self.logger.debug(
                "Show in folder could not resolve: %s", type(exc).__name__
            )
            return _reveal_not_found()
        if path is None:
            return _reveal_not_found()
        if linked_only and not await self._run_in_executor(
            self._is_recorded_media, path
        ):
            return _forbidden()
        try:
            await asyncio.get_running_loop().run_in_executor(
                reveal_executor(), self._reveal_opener, path, root
            )
        except RevealTargetChanged:
            return _reveal_not_found()
        except OpenerUnavailable:
            return web.json_response(
                {
                    "success": False,
                    "code": "unavailable",
                    "error": "No file manager is available on this computer.",
                },
                status=501,
            )
        except Exception as exc:
            # The exception text can hold the path: only its type at error
            # level, the traceback at debug.
            self.logger.error("Show in folder failed: %s", type(exc).__name__)
            self.logger.debug("Show in folder failure", exc_info=True)
            return web.json_response(
                {"success": False, "error": "The folder could not be opened."},
                status=500,
            )
        return web.json_response({"success": True})

    def _favorite_listing(self, root: Path, files: list) -> dict[str, str]:
        """``{relative key: listing POSIX spelling}`` for one root's scan.

        Built once per scan (the gallery cache hands out the same list until
        it rescans), so a toggle is a dictionary lookup.
        """
        cache_key = str(root)
        cached = self._favorite_listing_cache.get(cache_key)
        if cached is not None and cached[0] is files:
            return cached[1]
        listing = None
        if cached is not None:
            delta = gallery_listing.listing_delta(cached[0], files)
            if delta is not None:
                listing = _favorite_listing_step(root, cached[1], *delta)
        if listing is None:
            # From the path strings, as _listed does: relative_to per file was
            # the largest cost of a cold first open (#348).
            prefix = os.path.join(str(root), "")
            listing = {}
            for path, _mtime in files:
                posix = _listing_posix(root, prefix, path)
                listing.setdefault(_favorite_relative_key(posix), posix)
        self._favorite_listing_cache[cache_key] = (files, listing)
        return listing

    def _workspace_members(self, listings: list[tuple], filters: dict):
        """The member set the filters ask for, or None (blocking)."""
        collection = filters.get("collection")
        if collection is not None:
            members = self._workspace_member_paths(
                listings,
                lambda root_key: self.db.collection_members(collection, root_key),
            )
            # Checked after the reads: a collection deleted before or while
            # they ran is a 404, never an empty page passing for it.
            if not self.db.collection_exists(collection):
                raise CollectionMissing("No such collection")
            return members
        if filters["favorites"]:
            return self._workspace_favorite_paths(listings)
        return None

    def _workspace_favorite_paths(self, listings: list[tuple]) -> frozenset:
        """The listed paths of every favorite in the selected roots (blocking)."""
        return self._workspace_member_paths(listings, self.db.get_root_favorites)

    def _workspace_member_paths(
        self, listings: list[tuple], keys_for_source: Callable[[str], Iterable[str]]
    ) -> frozenset:
        """The listed paths of the stored keys of each selected root (blocking).

        One read per root (``keys_for_source(root key)``: the favorites' or a
        collection's primary-key range), matched against the root's cached
        ``{relative key: listing spelling}``, so the work follows the members
        rather than the files, and a key folds case exactly as it was stored
        (Windows). A member whose file is gone matches nothing. A failed read
        raises: an unfiltered page must never pass for the members.
        """
        paths = set()
        for _index, root, files in listings:
            listing = self._favorite_listing(root, files)
            for key in keys_for_source(_favorite_root_key(root)):
                listed = listing.get(key)
                if listed is not None:
                    paths.add(root / listed)
        return frozenset(paths)

    async def _workspace_listings(
        self, output_dirs: list[Path], root_index: int | None
    ) -> list[tuple]:
        """``(index, root, scan)`` of every selected root (cached scans)."""
        return [
            (index, root, await self._get_gallery_files(root))
            for index, root in enumerate(output_dirs)
            if root_index is None or index == root_index
        ]

    def _workspace_favorites(self, page: list[tuple]) -> set[tuple[int, str]]:
        """``(root index, listing path)`` of the favorites among *page* entries."""
        by_root: dict[tuple[int, Path], dict[str, str]] = {}
        for path, _mtime, root, index in page:
            posix = path.relative_to(root).as_posix()
            by_root.setdefault((index, root), {})[_favorite_relative_key(posix)] = posix
        try:
            found = set()
            for (index, root), keys in by_root.items():
                for key in self.db.get_image_favorites(
                    _favorite_root_key(root), list(keys)
                ):
                    found.add((index, keys[key]))
            return found
        except Exception:
            # The listing stays usable; stars show as not favorited.
            self.logger.warning("Image favorites could not be read")
            return set()

    async def inspect_image(self, request):
        """Read prompt evidence for one explicitly rooted physical media file.

        The roots include LoRA-linked folders (``_media_roots``, #355), so the
        inspector opens a LoRA import's media (recorded files only); it only
        reads them."""
        outputs = list(self._get_all_output_dirs())
        roots = await self._run_in_executor(self._media_roots, outputs)
        scope_error = self._gallery_scope_error(request.query, roots)
        if scope_error is not None:
            return scope_error
        try:
            index = _workspace_root(request.query, roots)
            if index is None:
                raise ValueError("Gallery root is required")
        except ValueError as error:
            return bad_request(str(error))
        root_scope = request.query.get("root_scope") or self._gallery_root_scope(
            roots[index], index
        )
        relative = request.match_info.get("image_path", "")
        if (
            _has_traversal(relative)
            or "\0" in relative
            or re.match(r"^[a-zA-Z]:", relative)
        ):
            return _forbidden()
        if _is_thumbnail_cache_path(relative):
            # A preview is a placeholder: it carries no metadata (#317).
            return _forbidden("Previews are not inspected; inspect the original")
        path = Path(roots[index]) / relative
        denied = _media_access_error(path, [roots[index]])
        if denied is None:
            denied = await self._linked_only_error(index, len(outputs), path)
        if denied is not None:
            return denied
        if not path.is_file():
            return web.json_response(
                {"success": False, "error": "Image file not found"}, status=404
            )
        try:

            def inspect() -> bytes | None:
                saved, unavailable, summary = None, False, None
                try:
                    saved = self.db.get_image_prompt_info(
                        str(path), False, report_ambiguity=True
                    )
                except Exception:
                    unavailable = True
                    self.logger.warning("Saved inspection display could not be read")
                if saved and saved.get("association_status") != "ambiguous":
                    try:
                        summary = self.db.get_image_link_summaries([str(path)], False)
                        summary = summary[str(path)]
                    except Exception:
                        # The link stays known; only its title is withheld.
                        self.logger.warning("Inspection link title could not be read")
                try:
                    observed = inspect_current_file(
                        path,
                        source_label=_public_path(roots[index]),
                        saved=saved,
                        library=self._inspection_library(
                            saved, unavailable=unavailable
                        ),
                        image_tags=self._inspection_image_tags(
                            roots[index], Path(relative).parts
                        ),
                    )
                except CurrentFileChanged:
                    return None
                observed["library"]["title"] = _verified_title(
                    summary, _positive_title(_inspected_positive(observed))
                )
                return encode_inspection_response(
                    image={
                        "root_index": index,
                        "path": Path(relative).as_posix(),
                        "filename": path.name,
                    },
                    root_scope=root_scope,
                    **observed,
                )

            body = await self._run_in_executor(inspect)
            if body is None:
                return web.json_response(
                    {
                        "success": False,
                        "error": "The file changed while being inspected. Try again.",
                    },
                    status=409,
                    headers={"Cache-Control": "no-store"},
                )
            return web.Response(
                body=body,
                content_type="application/json",
                charset="utf-8",
                headers={"Cache-Control": "no-store"},
            )
        except Exception:
            self.logger.exception("Prompt inspection failed")
            return web.json_response(
                {"success": False, "error": "Prompt inspection failed"}, status=500
            )

    def _inspection_image_tags(self, root: Path, parts: tuple) -> Callable:
        """The inspector's ``image_tags`` reader for one rooted file (S1).

        The returned function takes the inspection's own stable stat and
        answers ``current`` only for a byte record verified at exactly that
        ``(size, mtime_ns)``; a failed read is ``unavailable``, never an error.
        """
        locator = os.path.normcase(os.path.join(os.path.realpath(root), *parts))

        def read(size: int, mtime_ns: int) -> dict:
            try:
                found = self.db.current_image_tags({locator: (size, mtime_ns)})
            except Exception:
                self.logger.warning(
                    "Inspection image tags could not be read", exc_info=True
                )
                return {"state": "unavailable", "tags": []}
            tags = found.get(locator)
            if tags is None:
                return {"state": "none", "tags": []}
            return {"state": "current", "tags": list(tags)}

        return read

    def _inspection_library(self, saved: dict | None, *, unavailable: bool) -> dict:
        """The inspector's Library facts; the route sets the title afterwards."""
        result = {
            "status": "unavailable" if unavailable else "none",
            "scope": "saved-library",
            "positive_id": None,
            "pair_id": None,
            "title": None,
            "category": None,
            "tags": [],
            "category_scope": None,
            "tags_scope": None,
        }
        if not isinstance(saved, dict) or not saved:
            return result
        if saved.get("association_status") == "ambiguous":
            result["status"] = "ambiguous"
            return result
        pair = saved.get("pair")
        recipe = isinstance(pair, dict)
        result.update(
            status="linked",
            positive_id=(
                pair.get("positive_prompt_id") if recipe else saved.get("prompt_id")
            ),
            pair_id=pair.get("id") if recipe else None,
            **library_metadata(
                saved.get("category"),
                saved.get("tags"),
                (pair.get("category"), pair.get("tags")) if recipe else None,
            ),
        )
        return result

    async def get_image_prompt(self, request):
        """Get prompt information for a specific image path."""
        try:
            # Get the image path from URL
            raw_image_path = request.match_info.get("image_path", "")
            image_path = raw_image_path

            if not image_path:
                return web.json_response(
                    {"success": False, "error": "Image path is required"}, status=400
                )

            # Convert relative path to absolute if needed
            if "root" in request.query:
                roots = self._select_root(
                    self._get_all_output_dirs(), request.query["root"]
                )
                if not roots:
                    return _forbidden()
                if not os.path.isabs(image_path):
                    image_path = str(Path(roots[0]) / image_path)
                if not _is_within(image_path, roots[0]):
                    return _forbidden()
            elif not os.path.isabs(image_path):
                output_dir = self._find_comfyui_output_dir()
                if output_dir:
                    image_path = str(Path(output_dir) / image_path)

            # Look up the image in generated_images table
            try:
                args = (image_path, False) if "root" in request.query else (image_path,)
                prompt_data = await self._run_in_executor(
                    self.db.get_image_prompt_info, *args
                )
                if prompt_data:
                    prompt_data["image_path"] = _public_path(image_path)
                    return web.json_response({"success": True, "prompt": prompt_data})
                return web.json_response(
                    {
                        "success": False,
                        "error": "No prompt found for this image",
                        "image_path": _public_path(image_path),
                    }
                )

            except Exception as db_error:
                self.logger.error(f"Database error in get_image_prompt: {db_error}")
                return web.json_response(
                    {"success": False, "error": "Database error occurred"}, status=500
                )

        except Exception as e:
            self.logger.error(f"Get image prompt error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    async def delete_image(self, request):
        """Delete an image record."""
        try:
            image_id = int(request.match_info["image_id"])
            success = await self._run_in_executor(self.db.delete_image, image_id)

            if success:
                return web.json_response(
                    {"success": True, "message": "Image deleted successfully"}
                )
            else:
                return web.json_response(
                    {"success": False, "error": "Image not found"}, status=404
                )

        except ValueError:
            return web.json_response(
                {"success": False, "error": "Invalid image ID"}, status=400
            )
        except Exception as e:
            self.logger.error(f"Delete image error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    async def _collect_output_images(self, output_dirs, subfolder):
        """Merge cached per-root listings, newest first, optionally filtered."""
        merged = []
        for root_idx, output_path in enumerate(output_dirs):
            for img_path, mtime in await self._get_gallery_files(output_path):
                merged.append((img_path, mtime, output_path, root_idx))
        merged.sort(key=lambda x: x[1], reverse=True)
        if not subfolder:
            return merged
        return [
            entry
            for entry in merged
            if _in_subfolder(str(entry[0].relative_to(entry[2]).parent), subfolder)
        ]

    def _workspace_link_summaries(self, paths: list[str]) -> dict[str, dict]:
        """Card link summaries, matched exactly as the inspection route does."""
        try:
            return self.db.get_image_link_summaries(paths, False)
        except Exception:
            # The listing stays usable; cards fall back to their filenames.
            self.logger.warning("Workspace link summaries could not be read")
            return {}

    def _card_parse(self, path: Path, records: dict | None) -> tuple:
        """One card's ``(seed, positive title)``; ``(None, None)`` on any error.

        One file's unexpected parse failure must not fail the whole page: its
        card shows no seed and withholds a first-line title.
        """
        try:
            params = header_params(records)
            return card_seed(params), _positive_title(parsed_positive(params))
        except Exception:
            self.logger.warning(
                "Workspace card facts could not be parsed for %s",
                path.name,
                exc_info=True,
            )
            return None, None

    def _read_card_facts(self, path: Path, *, parse: bool) -> _CardFacts | None:
        """Read one image's header; ``parse`` adds the seed and positive title.

        None when the file cannot be read at all (corrupt, locked, vanishing).
        """
        try:
            width, height, records = _card_header(path)
        except (OSError, ValueError, Image.DecompressionBombError):
            # A corrupt or disappearing preview must not hide the listing.
            return None
        if not parse:
            return _CardFacts(width, height, None, None)
        # Parsed after the handle closes, so a concurrent rename or delete
        # (which Windows refuses on open files) is not held up by it.
        return _CardFacts(width, height, *self._card_parse(path, records))

    def _card_facts(self, entry: tuple, *, parse: bool) -> _CardFacts:
        """One listed image's card facts, read once per ``(path, mtime)``.

        Kept beside the scan's per-file values (``_workspace_listed``) and
        invalidated the same way: a rescan keeps the facts of unchanged files
        and drops the rest, and a roots change drops them all. Only files of
        the current scan are stored, so the cache is bounded by the listing.
        """
        path, mtime, root, index = entry[:4]
        root_key, key = (index, str(root)), (path, mtime)
        cached = self._workspace_card_cache.get(root_key, {}).get(key)
        if cached is not None:
            return cached
        facts = self._read_card_facts(path, parse=parse)
        if facts is None:
            # Not stored: a locked or half-written file may read next time.
            return _NO_CARD_FACTS
        listed = self._workspace_listed_cache.get(root_key)
        if parse and listed is not None and key in listed[2]:
            self._workspace_card_cache.setdefault(root_key, {})[key] = facts
        return facts

    def _format_workspace_page(
        self, page: list[tuple], *, linked: bool = True
    ) -> list[dict]:
        """Workspace descriptors; ``linked`` adds card facts (link, seed, favorite)."""
        images = self._format_output_page(page, scoped=True)
        entries = {
            (entry[3], entry[0].relative_to(entry[2]).as_posix()): entry
            for entry in page
        }
        summaries = (
            self._workspace_link_summaries(
                [str(entry[0]) for entry in entries.values()]
            )
            if linked
            else {}
        )
        for image in images:
            entry = entries[(image["root_index"], image["path"])]
            # Videos: the card reads no video metadata. Library previews show
            # no card facts, so they skip the parse (``parse=linked``).
            facts = (
                _NO_CARD_FACTS
                if image["is_video"]
                else self._card_facts(entry, parse=linked)
            )
            image["width"], image["height"] = facts.width, facts.height
            if not linked:
                continue
            image["seed"] = facts.seed
            summary = summaries.get(str(entry[0])) or _unavailable_link()
            image["linked"] = _card_link(summary, facts.positive_title)
        if linked:
            # Image cards carry the star; Library previews show none.
            favorites = self._workspace_favorites(page)
            for image in images:
                image["favorite"] = (image["root_index"], image["path"]) in favorites
        return images

    def _format_output_page(self, page, *, scoped=False):
        entries = []
        public_roots = {}
        for media_path, _mtime, output_path, root_index in page:
            if output_path not in public_roots:
                public_roots[output_path] = _public_path(output_path)
            try:
                entries.append(
                    _output_image_entry(
                        media_path,
                        output_path,
                        root_index,
                        public_roots[output_path],
                        root_scope=(
                            self._gallery_root_scope(output_path, root_index)
                            if scoped
                            else None
                        ),
                    )
                )
            except Exception as e:
                self.logger.error(f"Error processing media {media_path.name}: {e}")
        return entries
