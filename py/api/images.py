"""Image and gallery API routes for PromptManager."""

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import threading
import time as _time
import urllib.parse
from bisect import bisect_left, bisect_right
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from aiohttp import web
from PIL import Image, UnidentifiedImageError

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
    from ...database.library import display_title
    from ...database.models import normalize_image_path
    from ...utils.card_seed import card_seed, header_params, parsed_positive
    from ...utils.parallel import map_parallel
    from ...utils.inspection_response import encode_inspection_response
except ImportError:
    from database.library import display_title
    from database.models import normalize_image_path
    from utils.card_seed import card_seed, header_params, parsed_positive
    from utils.parallel import map_parallel
    from utils.inspection_response import encode_inspection_response


IMAGE_EXTENSIONS = _IMAGE_EXTENSIONS
# The header records the card parses (see utils.card_seed.header_params).
_CARD_RECORDS = ("prompt", "workflow")


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


def _verified_title(summary: dict | None, positive: str | None) -> str | None:
    """A linked file's Library title, withholding unconfirmed saved prompt text.

    The one rule for the card and the inspector. A stored title is user data and
    always shows. A title taken from the saved positive's first line shows only
    when ``positive``, the file's own positive as the shared parser reads it from
    the file's records, gives the same title: a saved association never replaces
    file evidence.
    """
    if not isinstance(summary, dict) or summary.get("status") != "linked":
        return None
    title = summary.get("title")
    if summary.get("title_source") != "text":
        return title
    confirmed = positive is not None and display_title((), positive) == title
    return title if confirmed else None


def _card_link(summary: dict, positive: str | None) -> dict:
    """The card's link summary with an unconfirmed first-line title withheld."""
    if summary.get("title_source") != "text":
        return summary
    return {**summary, "title": _verified_title(summary, positive)}


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


def _worker_threads():
    """Configured thread count for thumbnail work.

    Imported lazily because py.config imports this package.
    """
    from ..config import PromptManagerConfig

    return PromptManagerConfig.WORKER_THREADS


def _thumbnail_batch_size(workers):
    """Files per executor job: enough to keep every worker thread busy."""
    return max(4, workers * 2)


def _temp_sibling(dst):
    """Hidden temp path next to *dst* that keeps its extension (for format sniffing)."""
    dst = Path(dst)
    token = f"{os.getpid()}-{threading.get_ident()}"
    return dst.with_name(f".{dst.stem}.part-{token}{dst.suffix}")


def _atomic_save(img, dst, fmt=None, **save_kwargs):
    """Save a PIL image to *dst* without a partially written file ever being
    visible at that path: write a temp sibling, then rename over it."""
    tmp = _temp_sibling(dst)
    try:
        img.save(tmp, fmt, **save_kwargs)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink()


def _write_image_thumbnail(src, dst, thumbnail_size):
    """Resize *src* into *dst* (blocking PIL work), atomically."""
    with Image.open(src) as img:
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        img.thumbnail(thumbnail_size, Image.Resampling.LANCZOS)
        save_kwargs = {"quality": 85, "optimize": True}
        if dst.suffix.lower() == ".png":
            save_kwargs = {"optimize": True}
        _atomic_save(img, dst, **save_kwargs)


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


def _workspace_key(entry: tuple, order: str) -> tuple:
    """The keyset sort key of *entry* (a _Listed or a 4-tuple entry)."""
    if not isinstance(entry, _Listed):
        entry = _listed(*entry)
    if order in ("name", "name-desc"):
        return (entry.name, entry.tie, entry.index, entry.mtime)
    mtime = -entry.mtime if order in ("newest", "rating") else entry.mtime
    return (mtime, entry.name, entry.tie, entry.index)


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


def _iter_media_files(output_path, max_files):
    """Yield media files under *output_path* with bounded effort.

    Symlinks are never followed, ``thumbnails/`` is skipped, descent stops
    at MAX_SCAN_DEPTH and at most *max_files* paths are produced.
    """
    output_path = Path(output_path)
    root_depth = len(output_path.parts)
    produced = 0
    for root, dirs, files in os.walk(output_path):
        root_path = Path(root)
        rel_parts = root_path.parts[root_depth:]
        if "thumbnails" in rel_parts:
            dirs[:] = []
            continue
        if len(rel_parts) >= MAX_SCAN_DEPTH:
            dirs[:] = []
        for name in sorted(files):
            if Path(name).suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            if (root_path / name).is_symlink():
                continue  # never read through a link out of the output tree
            if produced >= max_files:
                return
            produced += 1
            yield root_path / name


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

    thumb_rel = (
        f"thumbnails/{rel_path.with_suffix('').as_posix()}_thumb"
        f"{'.jpg' if is_video else extension}"
    )
    thumbnail_url = thumbnail_url_for(thumb_rel, output_path / thumb_rel)
    if thumbnail_url is not None:
        thumbnail_url += f"&root={root_index}"
    encoded_path = urllib.parse.quote(rel_path.as_posix(), safe="/")
    url = f"/prompt_manager/images/serve/{encoded_path}?root={root_index}"
    if root_scope is not None:
        url += f"&root_scope={root_scope}"
        if thumbnail_url is not None:
            thumbnail_url += f"&root_scope={root_scope}"

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
    the linked positive. Raises ValueError for any unknown or oversized value.
    """
    text = query.get("q", "").strip()
    folded = text.casefold()
    media = query.get("media", "all")
    order = query.get("sort", "newest")
    folder = query.get("subfolder", "").strip().replace("\\", "/")
    category = query.get("category", "").strip() or None
    tags = sorted(
        {tag.strip() for tag in query.get("tags", "").split(",") if tag.strip()}
    )
    if (
        len(text) > 500
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
    return {
        "text": text,
        "query": folded,
        "media": media,
        "order": order,
        "folder": folder,
        "category": category,
        "tags": tags,
    }


# A plain listing's cursor page leaves positive_count out (see
# _select_workspace_page); None instead means the count could not be read.
_COUNT_OMITTED = object()


def _filtered_page(
    entries: list, start: int, size: int, filters: dict, links: dict | None
) -> tuple[int, list, int | None]:
    """Count the *entries* the filters admit and take *size* from *start* on.

    *links* maps normalized paths to (hit, rating, positive id) for every
    unambiguously linked image (see PromptDatabase.get_image_link_matches); a
    category or tag filter admits only those, and ``q`` admits a filename match
    or a linked-positive hit. Returns the total, the page and the number of
    distinct linked positives among all admitted entries (None without links).
    """
    query, media, folder = filters["query"], filters["media"], filters["folder"]
    members_only = filters["category"] is not None or bool(filters["tags"])
    native_folder = folder.replace("/", os.sep)
    nested = native_folder + os.sep
    total, page, positives = 0, [], set()
    for position, entry in enumerate(entries):
        if folder and not (
            entry.folder == native_folder or entry.folder.startswith(nested)
        ):
            continue
        if media != "all" and (media == "video") != entry.video:
            continue
        match = None if links is None else links.get(entry.normalized)
        if members_only and match is None:
            continue
        if query and query not in entry.folded and not (match and match[0]):
            continue
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

            # Additional fallback: convert to JSON string and clean NaN values manually
            try:
                response_data = {"success": True, "images": cleaned_images}
                # Convert to JSON string
                json_str = json.dumps(response_data, default=str)

                # Clean any remaining NaN values with regex
                json_str = re.sub(r":\s*NaN", ": null", json_str)
                json_str = re.sub(r"\[\s*NaN\s*\]", "[null]", json_str)
                json_str = re.sub(r",\s*NaN\s*,", ", null,", json_str)
                json_str = re.sub(r",\s*NaN\s*\]", ", null]", json_str)
                json_str = re.sub(r"\[\s*NaN\s*,", "[null,", json_str)

                # Parse back to verify it's valid JSON
                cleaned_data = json.loads(json_str)

                return web.json_response(cleaned_data)
            except Exception as json_error:
                self.logger.error(f"JSON cleaning error: {json_error}")
                # Fallback to original response
                return web.json_response({"success": True, "images": cleaned_images})

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

    def _scan_gallery_files_sync(self, output_path):
        """Scan output directory for media files (blocking I/O, run in executor).

        Returns list of (path, mtime) tuples sorted by mtime descending; the
        walk is bounded by MAX_SCAN_DEPTH and MAX_GALLERY_FILES.
        """
        found = []
        for media_path in _iter_media_files(output_path, MAX_GALLERY_FILES):
            try:
                found.append((media_path, media_path.stat().st_mtime))
            except OSError:
                continue
        if len(found) >= MAX_GALLERY_FILES:
            self.logger.warning(f"Gallery scan capped at {MAX_GALLERY_FILES} files")
        found.sort(key=lambda x: x[1], reverse=True)
        return found

    async def _get_gallery_files(self, output_path):
        """Get gallery files with per-directory TTL cache."""
        now = _time.monotonic()
        key = str(output_path)
        cached = self._gallery_cache.get(key)
        if cached and (now - cached[1]) < self._gallery_cache_ttl:
            return cached[0]

        files = await self._run_in_executor(self._scan_gallery_files_sync, output_path)
        self._gallery_cache[key] = (files, now)
        return files

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
            self.logger.error(f"Output images error: {e}")
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
                        root_index,
                        [str(root) for root in output_dirs],
                        filters["category"],
                        filters["tags"],
                    ]
                ).encode()
            ).hexdigest()
            token = request.query.get("cursor")
            after = (
                _workspace_cursor_key(token, scope, filters["order"]) if token else None
            )
        except ValueError as exc:
            return bad_request(str(exc))

        listings = [
            (index, root, await self._get_gallery_files(root))
            for index, root in enumerate(output_dirs)
            if root_index is None or index == root_index
        ]
        page, total, has_more, next_cursor, positive_count = (
            await self._run_in_executor(
                self._select_workspace_page, listings, filters, after, limit, scope
            )
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
        self._workspace_listed_cache[key] = (files, listed, lookup)
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
        newest = sorted(
            (entry for part in parts for entry in part),
            key=lambda entry: _workspace_key(entry, "newest"),
        )
        orders = {"newest": newest}
        self._workspace_order_cache[key] = (parts, orders)
        return orders

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

    def _select_workspace_page(
        self,
        listings: list[tuple],
        filters: dict,
        after: tuple | None,
        limit: int,
        scope: str,
    ) -> tuple:
        """One keyset page of the filtered listing (blocking; run in executor).

        One SELECT (get_image_link_matches) reads every linked positive of the
        listing: the filters, the rating sort and the positive count share it.
        A plain listing (no ``q``, category, tags or rating sort) reads it only
        for its first page; its cursor pages read no links and the returned
        count is _COUNT_OMITTED, so the response leaves ``positive_count`` out
        and the frontend keeps the first page's value. On that plain first
        page a failed read leaves the listing usable and the count unknown
        (None).
        """
        order, category, tags = filters["order"], filters["category"], filters["tags"]
        needed = (
            bool(filters["query"])
            or category is not None
            or bool(tags)
            or order == "rating"
        )
        links = None
        if needed or after is None:
            try:
                links = self.db.get_image_link_matches(
                    filters["text"],
                    category,
                    tags,
                    every_link=True,
                    wanted=self._workspace_wanted(listings),
                )
            except Exception:
                if needed:
                    raise
                self.logger.warning(
                    "Workspace positive count could not be read", exc_info=True
                )
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

        start = 0
        if after is not None:
            if order == "name-desc":
                ascending = self._workspace_order(listings, "name")
                start = len(entries) - bisect_left(ascending, after, key=key)
            else:
                start = bisect_right(entries, after, key=key)
        total, page, positive_count = _filtered_page(
            entries, start, limit + 1, filters, links
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
            allowed_dirs = list(self._get_all_output_dirs()) + self._lora_image_dirs()

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

    def _lora_image_dirs(self):
        """Extra directories served when the LoRA integration is enabled."""
        try:
            from ..config import IntegrationConfig

            if not IntegrationConfig.LORA_MANAGER_ENABLED:
                return []
            from ..lora_utils import find_lora_directories, get_lora_image_cache_dir

            lora_root = IntegrationConfig.LORA_MANAGER_PATH or ""
            if lora_root.strip():
                # A configured path comes from config.json, which can be hand
                # edited: only a LoraManager install under custom_nodes may
                # widen the serving allow-list. An empty path keeps the
                # auto-detection, which itself only scans custom_nodes.
                from ..lora_utils import resolve_lora_manager_path

                lora_root = resolve_lora_manager_path(lora_root)
                if not lora_root:
                    return []

            dirs = [Path(d) for d in find_lora_directories(lora_root)]
            dirs.append(Path(get_lora_image_cache_dir()))
            return dirs
        except Exception:
            # LoRA integration is optional; never widen access on failure.
            return []

    @staticmethod
    def _file_response(path):
        response = web.FileResponse(path)
        response.headers["Cache-Control"] = "public, max-age=3600"
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
        """Serve a media file from the ComfyUI output folder(s)."""
        try:
            roots = list(self._get_all_output_dirs())
            scope_error = self._gallery_scope_error(request.query, roots)
            if scope_error is not None:
                return scope_error
            filepath = request.match_info["filepath"]
            if _has_traversal(filepath):
                return _forbidden()
            if Path(filepath).suffix.lower() not in SERVABLE_EXTENSIONS:
                return _forbidden("Only media files can be served")

            output_dirs = self._select_root(roots, request.query.get("root"))
            if not output_dirs:
                return _forbidden("No allowed image directories configured")

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

    async def rescan_gallery(self, request):
        """Forget the cached listing and rewalk every output root now.

        The listing is cached for ``_gallery_cache_ttl`` seconds; the gallery's
        Rescan button needs files added or removed on disk to show up at once.
        """
        try:
            self.invalidate_gallery_cache()
            output_dirs = list(self._get_all_output_dirs())
            total = 0
            for output_path in output_dirs:
                total += len(await self._get_gallery_files(output_path))
            return web.json_response(
                {"success": True, "total": total, "roots": len(output_dirs)}
            )
        except Exception:
            self.logger.exception("Gallery rescan error")
            return web.json_response(
                {"success": False, "error": "Failed to rescan the gallery"}, status=500
            )

    async def get_gallery_subfolders(self, request):
        """Get distinct subfolders from gallery output directories."""
        try:
            output_dirs = list(self._get_all_output_dirs())
            workspace = request.query.get("mode") == "workspace"
            roots = None
            if workspace:
                try:
                    selected = _workspace_root(request.query, output_dirs)
                except ValueError as exc:
                    return bad_request(str(exc))
                roots = [
                    {"index": index, "label": _public_path(root)}
                    for index, root in enumerate(output_dirs)
                ]
                if selected is not None:
                    output_dirs = [output_dirs[selected]]
            subfolders = set()
            for output_path in output_dirs:
                files = await self._get_gallery_files(output_path)
                for f, _mtime in files:
                    rel_dir = str(f.relative_to(output_path).parent)
                    if rel_dir and rel_dir != ".":
                        subfolders.add(rel_dir)

            include_ancestors = (
                request.query.get("include_ancestors", "").lower() == "true"
            )
            if include_ancestors:
                ancestors = set()
                for folder in subfolders:
                    parts = folder.replace("\\", "/").split("/")
                    for i in range(1, len(parts)):
                        ancestors.add("/".join(parts[:i]))
                subfolders.update(ancestors)

            payload = {"success": True, "subfolders": sorted(subfolders)}
            if workspace:
                payload["roots"] = roots
            return web.json_response(payload)
        except Exception as e:
            self.logger.error(f"Subfolders error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    # Fixed quality presets: clients choose a name, never a raw pixel size.
    THUMBNAIL_SIZES = {"low": (150, 150), "medium": (300, 300), "high": (600, 600)}

    @classmethod
    def _thumbnail_size(cls, quality):
        """Map a quality preset name to a pixel size; anything else is medium."""
        if not isinstance(quality, str):
            return cls.THUMBNAIL_SIZES["medium"]
        return cls.THUMBNAIL_SIZES.get(quality, cls.THUMBNAIL_SIZES["medium"])

    async def generate_thumbnails(self, request):
        """Generate every thumbnail in one executor job (POST, blocking)."""
        try:
            data, error = await _json_body_or_error(request)
            if error is not None:
                return error
            thumbnail_size = self._thumbnail_size(data.get("quality", "medium"))

            output_dirs = self._get_all_output_dirs()
            if not output_dirs:
                return web.json_response(
                    {"success": False, "error": "ComfyUI output directory not found"},
                    status=404,
                )

            result = await self._run_in_executor(
                self._generate_gallery_thumbnails_sync, output_dirs, thumbnail_size
            )
            self.invalidate_gallery_cache()
            return web.json_response(result)

        except Exception as e:
            self.logger.error(f"Generate thumbnails error: {e}")
            return web.json_response(
                {"success": False, "error": _safe_error(e)}, status=500
            )

    def _generate_thumbnails_sync(self, output_path, thumbnails_dir, thumbnail_size):
        """Blocking thumbnail generation loop (run in executor)."""
        start = _time.monotonic()
        plan = self._plan_thumbnails(output_path, thumbnails_dir)
        payload = self._execute_thumbnail_plan(plan, thumbnail_size, start)
        payload["thumbnails_path"] = _public_path(thumbnails_dir)
        return payload

    def _generate_gallery_thumbnails_sync(
        self, output_dirs: list[Path], thumbnail_size: tuple[int, int]
    ) -> dict:
        start = _time.monotonic()
        plan = self._plan_gallery_thumbnails(output_dirs)
        payload = self._execute_thumbnail_plan(plan, thumbnail_size, start)
        payload["thumbnails_path"] = _public_path(output_dirs[0] / "thumbnails")
        return payload

    def _execute_thumbnail_plan(
        self, plan: dict, thumbnail_size: tuple[int, int], start: float
    ) -> dict:
        targets = plan["targets"]
        stats = _new_thumbnail_stats()
        stats["skipped"] = plan["skipped"]
        for result in self._generate_batch(targets, thumbnail_size):
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

    def _plan_gallery_thumbnails(self, output_dirs: list[Path]) -> dict:
        """Plan every gallery root with one shared per-run generation cap."""
        combined = {"targets": [], "skipped": 0, "remaining": 0}
        for root in output_dirs:
            root = Path(root)
            plan = self._plan_thumbnails(root, root / "thumbnails")
            available = max(0, MAX_THUMBNAIL_FILES - len(combined["targets"]))
            combined["targets"].extend(plan["targets"][:available])
            combined["skipped"] += plan["skipped"]
            combined["remaining"] += plan["remaining"] + max(
                0, len(plan["targets"]) - available
            )
        return combined

    async def get_thumbnail_status(self, request: web.Request) -> web.Response:
        """Count pending thumbnails off-loop without generating files."""
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

    def _plan_thumbnails(self, output_path, thumbnails_dir):
        """Decide which media files need a thumbnail (blocking walk).

        Returns ``{"targets": [(src, dst, is_video)], "skipped": n, "remaining": n}``.
        Files whose thumbnail is already newer than the source are counted as
        skipped and never queued, so a large library that is mostly done only
        pays for the files that changed. The walk is bounded like the gallery
        listing (MAX_SCAN_DEPTH, MAX_GALLERY_FILES, no symlinks) and at most
        MAX_THUMBNAIL_FILES pending files are queued per run; ``remaining``
        counts the pending files beyond that cap.
        """
        output_path = Path(output_path)
        targets = []
        skipped = 0
        remaining = 0
        for src in _iter_media_files(output_path, MAX_GALLERY_FILES):
            suffix = src.suffix.lower()
            is_video = suffix in VIDEO_EXTENSIONS
            rel_no_ext = src.relative_to(output_path).with_suffix("")
            thumb_suffix = ".jpg" if is_video else suffix
            dst = thumbnails_dir / f"{rel_no_ext.as_posix()}_thumb{thumb_suffix}"
            if not _is_within(dst, thumbnails_dir):
                self.logger.warning(f"Skipping thumbnail outside safe dir: {src.name}")
                continue
            if _thumbnail_is_fresh(src, dst):
                skipped += 1
                continue
            if len(targets) >= MAX_THUMBNAIL_FILES:
                remaining += 1
                continue
            targets.append((src, dst, is_video))
        if remaining:
            self.logger.warning(
                f"Thumbnail run capped at {MAX_THUMBNAIL_FILES} new files; "
                f"{remaining} more will be generated on the next run"
            )
        return {"targets": targets, "skipped": skipped, "remaining": remaining}

    def _thumbnail_targets(self, output_path, thumbnails_dir):
        """Pending (src, dst, is_video) triples; see _plan_thumbnails."""
        return self._plan_thumbnails(output_path, thumbnails_dir)["targets"]

    def _generate_batch(self, targets, thumbnail_size):
        """Create thumbnails for ``targets`` on the configured worker threads.

        Blocking; returns one result dict per target, in order.
        """

        def one(target):
            src, dst, is_video = target
            return self._generate_one(src, dst, thumbnail_size, is_video)

        return map_parallel(one, targets, _worker_threads())

    def _generate_one(self, src, dst, thumbnail_size, is_video):
        """Create one thumbnail (blocking). Never raises; returns a result dict."""
        result = {
            "file": src.name,
            "dir": src.parent.name,
            "type": "video" if is_video else "image",
        }
        try:
            if not src.is_file():
                return {**result, "action": "error", "error": "File no longer exists"}
            if _thumbnail_is_fresh(src, dst):
                return {**result, "action": "skipped"}
            dst.parent.mkdir(parents=True, exist_ok=True)
            if is_video:
                if not self._generate_video_thumbnail(src, dst, thumbnail_size):
                    return {**result, "action": "error", "error": "Video decode failed"}
            else:
                _write_image_thumbnail(src, dst, thumbnail_size)
            return {**result, "action": "generated"}
        except UnidentifiedImageError:
            return {**result, "action": "error", "error": "Not a valid image file"}
        except Exception as e:
            self.logger.warning(f"Thumbnail failed for {src.name}", exc_info=True)
            return {**result, "action": "error", "error": _safe_error(e)}

    async def generate_thumbnails_with_progress(self, request):
        """Generate thumbnails with Server-Sent Events progress updates (POST).

        ``quality`` is read from the query string so a POST-capable SSE
        client can keep using the same URL shape. Every blocking step (scan,
        PIL/ffmpeg) runs in the executor; the event loop only writes SSE
        frames between files.
        """
        thumbnail_size = self._thumbnail_size(request.query.get("quality", "medium"))
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)
        try:
            output_dirs = self._get_all_output_dirs()
            if not output_dirs:
                await self._emit_sse(
                    response, "error", {"error": "ComfyUI output directory not found"}
                )
                return response

            await self._emit_sse(
                response,
                "status",
                {"phase": "scanning", "message": "Scanning for images and videos..."},
            )
            plan = await self._run_in_executor(
                self._plan_gallery_thumbnails, output_dirs
            )
            await self._stream_thumbnails(
                response,
                plan["targets"],
                thumbnail_size,
                skipped=plan["skipped"],
                remaining=plan["remaining"],
            )
            self.invalidate_gallery_cache()
        except Exception:
            self.logger.exception("Thumbnail generation failed")
            await self._emit_sse(
                response,
                "error",
                {
                    "error": "An internal error occurred",
                    "message": "Thumbnail generation failed. Check server logs.",
                },
            )
        return response

    async def _stream_thumbnails(
        self, response, targets, thumbnail_size, skipped=0, remaining=0
    ):
        """Process pending *targets* in worker-thread batches, emitting SSE per file.

        ``skipped`` files already had fresh thumbnails and ``remaining`` were
        left for the next run; both only feed the start/complete payloads.
        """
        total = len(targets)
        video_count = sum(1 for _, _, is_video in targets if is_video)
        message = (
            f"Found {total - video_count} images and {video_count} videos to process"
        )
        if skipped:
            message += f" ({skipped} already have thumbnails)"
        connected = await self._emit_sse(
            response,
            "start",
            {
                "total_images": total,
                "skipped": skipped,
                "remaining": remaining,
                "phase": "processing",
                "image_count": total - video_count,
                "video_count": video_count,
                "message": message,
            },
        )
        if not connected:
            return
        stats = _new_thumbnail_stats()
        stats["skipped"] = skipped
        start = _time.monotonic()
        batch_size = _thumbnail_batch_size(_worker_threads())
        index = 0
        for batch_start in range(0, total, batch_size):
            batch = targets[batch_start : batch_start + batch_size]
            results = await self._run_in_executor(
                self._generate_batch, batch, thumbnail_size
            )
            for result in results:
                index += 1
                _record_thumbnail_result(stats, result)
                if result["action"] == "error":
                    await self._emit_sse(
                        response,
                        "file_error",
                        {"file": result["file"], "error": result["error"]},
                    )
                connected = await self._emit_sse(
                    response,
                    "progress",
                    _thumbnail_progress_payload(index, total, stats, result, start),
                )
                if not connected:
                    self.logger.info("Thumbnail client disconnected; stopping early")
                    return
            await asyncio.sleep(0)  # let other requests run between batches
        payload = _thumbnail_complete_payload(
            total + skipped + remaining,
            stats,
            start,
            remaining=remaining,
            processed=total,
        )
        await self._emit_sse(response, "complete", payload)
        self.logger.info(payload["message"])

    async def _emit_sse(self, response, event, data) -> bool:
        """Write one SSE frame. Returns False once the client has gone away."""
        try:
            frame = f"event: {event}\ndata: {json.dumps(data)}\n\n"
            await response.write(frame.encode("utf-8"))
            return True
        except Exception as e:
            self.logger.warning(f"Failed to send SSE message: {e}")
            return False

    def _generate_video_thumbnail(self, video_path, thumbnail_path, thumbnail_size):
        """Generate thumbnail from video file. Returns True if successful."""
        try:
            # Try using OpenCV first (most reliable)
            try:
                import cv2

                cap = cv2.VideoCapture(str(video_path))
                if not cap.isOpened():
                    return False

                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                target_frame = max(1, int(frame_count * 0.1))
                cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)

                ret, frame = cap.read()
                cap.release()

                if not ret or frame is None:
                    return False

                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                img = Image.fromarray(frame_rgb)
                img.thumbnail(thumbnail_size, Image.Resampling.LANCZOS)
                _atomic_save(img, thumbnail_path, "JPEG", quality=85, optimize=True)
                self.logger.debug(
                    f"Generated video thumbnail using OpenCV: {thumbnail_path}"
                )
                return True

            except ImportError:
                pass

            # Fallback to ffmpeg
            try:
                import subprocess

                tmp_path = _temp_sibling(thumbnail_path)
                cmd = [
                    "ffmpeg",
                    "-i",
                    str(video_path),
                    "-ss",
                    "00:00:01",
                    "-vframes",
                    "1",
                    "-s",
                    f"{thumbnail_size[0]}x{thumbnail_size[1]}",
                    "-y",
                    str(tmp_path),
                ]

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0 and tmp_path.exists():
                    os.replace(tmp_path, thumbnail_path)
                    self.logger.debug(
                        f"Generated video thumbnail using ffmpeg: {thumbnail_path}"
                    )
                    return True
                else:
                    self.logger.warning(
                        f"ffmpeg failed for {video_path}: {result.stderr}"
                    )

            except (ImportError, subprocess.TimeoutExpired, FileNotFoundError):
                pass
            finally:
                try:
                    tmp_path.unlink()
                except (NameError, OSError):
                    pass

            # Last resort: create a placeholder thumbnail
            try:
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
                        (
                            center_x - text_width // 2,
                            center_y + triangle_size // 2 + 10,
                        ),
                        text,
                        fill=(255, 255, 255),
                        font=font,
                    )
                except (OSError, AttributeError):
                    pass

                _atomic_save(img, thumbnail_path, "JPEG", quality=85)
                self.logger.debug(
                    f"Generated placeholder video thumbnail: {thumbnail_path}"
                )
                return True

            except Exception as e:
                self.logger.warning(
                    f"Failed to create placeholder thumbnail for {video_path}: {e}"
                )
                return False

        except Exception as e:
            self.logger.error(
                f"Video thumbnail generation failed for {video_path}: {e}"
            )
            return False

    async def clear_thumbnails(self, request: web.Request) -> web.Response:
        """Clear generated thumbnails across gallery roots, preserving originals.

        Invalid thumbnail directories return 400; unavailable gallery roots
        return 404, and unexpected filesystem failures return 500.
        """
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
            self.invalidate_gallery_cache()

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
            for root, _dirs, files in os.walk(thumbnails_dir):
                for name in files:
                    path = Path(root) / name
                    if (
                        "_thumb" not in name.lower()
                        or path.suffix.lower() not in GALLERY_IMAGE_EXTENSIONS
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

    async def inspect_image(self, request):
        """Read prompt evidence for one explicitly rooted physical media file."""
        roots = list(self._get_all_output_dirs())
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
        path = Path(roots[index]) / relative
        denied = _media_access_error(path, [roots[index]])
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
                    )
                except CurrentFileChanged:
                    return None
                observed["library"]["title"] = _verified_title(
                    summary, _inspected_positive(observed)
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
            "metadata_scope": None,
        }
        if not isinstance(saved, dict) or not saved:
            return result
        if saved.get("association_status") == "ambiguous":
            result["status"] = "ambiguous"
            return result
        pair = saved.get("pair")
        if isinstance(pair, dict):
            result.update(
                status="linked",
                positive_id=pair.get("positive_prompt_id"),
                pair_id=pair.get("id"),
                category=pair.get("category"),
                tags=pair.get("tags"),
                metadata_scope="pair",
            )
        else:
            result.update(
                status="linked",
                positive_id=saved.get("prompt_id"),
                category=saved.get("category"),
                tags=saved.get("tags"),
                metadata_scope="positive",
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

    def _format_workspace_page(
        self, page: list[tuple], *, linked: bool = True
    ) -> list[dict]:
        """Workspace descriptors; ``linked`` adds card facts (link summary, seed)."""
        images = self._format_output_page(page, scoped=True)
        paths = {
            (entry[3], entry[0].relative_to(entry[2]).as_posix()): entry[0]
            for entry in page
        }
        summaries = (
            self._workspace_link_summaries([str(path) for path in paths.values()])
            if linked
            else {}
        )
        for image in images:
            path = paths[(image["root_index"], image["path"])]
            image["width"] = None
            image["height"] = None
            records = None
            if not image["is_video"]:
                try:
                    with path.open("rb") as stream:
                        with Image.open(stream) as source:
                            image["width"], image["height"] = source.size
                            records = {k: source.info.get(k) for k in _CARD_RECORDS}
                except (OSError, ValueError, Image.DecompressionBombError):
                    # A corrupt or disappearing preview must not hide the listing.
                    pass
            if not linked:
                # Library previews show no card facts; skip the parse.
                continue
            # Parsed after the handle closes, so a concurrent rename or delete
            # (which Windows refuses on open files) is not held up by it.
            params = header_params(records)
            image["seed"] = card_seed(params)
            summary = summaries.get(str(path)) or _unavailable_link()
            image["linked"] = _card_link(summary, parsed_positive(params))
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
