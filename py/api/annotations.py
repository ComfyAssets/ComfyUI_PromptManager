"""Image annotation routes: observe, capture preview and byte-exact tag saves.

Every route names its file through the listing identity ``{root_index,
root_scope, path}`` and checks the scope against the current gallery roots
before any file access. Observe copies the file into a private capture (the
token the preview and Save refer to), Save re-hashes the current file and
writes only when the bytes are still the captured ones. Responses carry no
server path and no locator.
"""

import asyncio
import datetime
import functools
import os
import re
import threading
from pathlib import Path

from aiohttp import web

from .annotation_suggest import (
    SUGGEST_FIELDS,
    OptionError,
    run_suggestions,
    suggestion_rows,
    unavailable_message,
    validate_options,
)
from .autotag_routes import model_busy_response
from .images import _forbidden, _has_traversal, _unavailable_link, _verified_title
from .prompts import bad_request
from .. import model_gate

try:
    from ...database.annotations import (
        MAX_IMAGE_TAGS,
        AnnotationCommandError,
        ImageTagLimitError,
        normalize_image_tags,
    )
    from ...database.execution_history import subscribe_history_restore
    from ...database.library_commands import LibraryCommandError
    from ...utils.media_capture import CAPTURE_EXTENSIONS, CaptureError, CaptureStore
except ImportError:
    from database.annotations import (
        MAX_IMAGE_TAGS,
        AnnotationCommandError,
        ImageTagLimitError,
        normalize_image_tags,
    )
    from database.execution_history import subscribe_history_restore
    from database.library_commands import LibraryCommandError
    from utils.media_capture import CAPTURE_EXTENSIONS, CaptureError, CaptureStore

# Request bodies of the annotation routes are small (a target plus a tag set).
MAX_BODY_BYTES = 64 * 1024
MAX_TARGET_PATH = 1024
CAPTURE_URL = "/prompt_manager/image-annotations/capture/"
_PREVIEW_CHUNK = 64 * 1024

_TARGET_FIELDS = frozenset({"root_index", "root_scope", "path"})
_OBSERVE_FIELDS = frozenset({"target"})
_SAVE_FIELDS = frozenset(
    {
        "target",
        "capture_token",
        "sha256",
        "expected_epoch",
        "expected_tag_revision",
        "tags",
    }
)
_HEX64 = re.compile(r"[0-9a-f]{64}")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
# secrets.token_urlsafe(32) is 43 characters; the bound only rejects junk.
_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,128}")
# The store is created on first use by the event loop, the scan's executor or
# the monitor's worker (Needs review), so its creation is serialized.
_CAPTURE_STORE_LOCK = threading.Lock()

_PREVIEW_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".wmv": "video/x-ms-wmv",
}

_STATUS = {
    "review-capacity": 429,
    "unsupported-media": 415,
    "file-changed": 409,
    "source-changed": 409,
    "storage-changed": 409,
    "tag-conflict": 409,
    "capture-expired": 409,
    "model-unavailable": 409,
    "tag-limit": 400,
    "metadata-conflict": 409,
    "vocabulary-conflict": 409,
}
_MESSAGES = {
    "source-changed": (
        "The source changed. Refresh the image collection before tagging this file."
    ),
    "file-changed": "The file changed since it was previewed. Reopen the Tag dialog.",
    "review-capacity": "Too many previews are open. Close a Tag dialog and try again.",
    "storage-changed": "Prompt storage was restored; reopen the Tag dialog.",
    "tag-conflict": (
        "The tags of this image changed since the dialog opened; review them first."
    ),
    "capture-expired": "The preview expired. Open the Tag dialog again.",
    "tag-limit": f"An image can carry at most {MAX_IMAGE_TAGS} tags.",
}
# Any other storage code (a conflict a later command adds) still answers a
# coded envelope rather than a bare 500.
_DEFAULT_STATUS = 409
_DEFAULT_MESSAGE = "The tags could not be saved. Reopen the Tag dialog and try again."
_OTHER_FILE = "The preview belongs to another file. Open the Tag dialog again."


def preview_content_type(extension: str) -> str:
    """The media type the preview is served as; never sniffed from bytes."""
    return _PREVIEW_TYPES.get(extension.lower(), "application/octet-stream")


def _error(status: int, code: str, message: str, **extra) -> web.Response:
    return web.json_response(
        {"success": False, "code": code, "error": message, **extra},
        status=status,
        headers={"Cache-Control": "no-store"},
    )


def _coded(code: str, message: str | None = None, **extra) -> web.Response:
    status = _STATUS.get(code, _DEFAULT_STATUS)
    return _error(
        status, code, message or _MESSAGES.get(code, _DEFAULT_MESSAGE), **extra
    )


def _open_capture(path: str):
    """Worker thread: the capture file opened for reading."""
    return open(path, "rb")


async def _settled(future, cleanup):
    """Await an executor *future*; a cancelled caller still cleans up after it.

    The future is shielded, so the worker call always runs to its end. When the
    caller is cancelled first, ``cleanup(result)`` runs once the call is done
    (``cleanup(None)`` when it failed): a handle is closed, a capture released.
    """
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        future.add_done_callback(functools.partial(_cleanup_result, cleanup))
        raise


def _cleanup_result(cleanup, future) -> None:
    if future.cancelled() or future.exception() is not None:
        cleanup(None)
    else:
        cleanup(future.result())


def _submit(func, *args, **kwargs):
    return asyncio.get_running_loop().run_in_executor(
        None, functools.partial(func, *args, **kwargs)
    )


def _capture_error(exc: CaptureError) -> web.Response:
    """The envelope for a capture outcome; messages never name a path."""
    if exc.code not in _STATUS:
        return _error(500, exc.code, "The file could not be captured.")
    return _coded(exc.code, _MESSAGES.get(exc.code) or str(exc))


def _target(data: object):
    """``((index, scope, relative), None)`` for a target, else ``(None, error)``.

    Only the listing identity is accepted: an int root index, the root's
    64-hex scope and a bounded root-relative POSIX media path, never an
    absolute, drive-qualified, backslash or ``..`` path. Nothing here touches
    the filesystem or the database.
    """
    if not isinstance(data, dict) or set(data) != _TARGET_FIELDS:
        return None, bad_request("target must name root_index, root_scope and path")
    index, scope, relative = data["root_index"], data["root_scope"], data["path"]
    if type(index) is not int or index < 0:
        return None, bad_request("Invalid gallery root")
    if not isinstance(scope, str) or _HEX64.fullmatch(scope) is None:
        return None, bad_request("Invalid gallery root scope")
    if not isinstance(relative, str) or not relative or len(relative) > MAX_TARGET_PATH:
        return None, bad_request("path must be a relative media path")
    parts = relative.split("/")
    if (
        "\0" in relative
        or "\\" in relative
        or relative.startswith("/")
        or _has_traversal(relative)
        or any(part in ("", ".", "..") or ":" in part for part in parts)
    ):
        return None, _forbidden()
    if os.path.splitext(parts[-1])[1].lower() not in CAPTURE_EXTENSIONS:
        return None, _forbidden("Only media files can be tagged")
    return (index, scope, relative), None


def _expires_at(seconds: float | None) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=max(0.0, seconds or 0.0)
    )
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _capture_info(store: CaptureStore, capture) -> dict:
    return {
        "token": capture.token,
        "expires_at": _expires_at(store.expires_in(capture.token)),
        "preview_url": CAPTURE_URL + capture.token,
        "sha256": capture.sha256,
        "size": capture.size,
        "width": capture.width,
        "height": capture.height,
        "media_type": capture.media_type,
        "generate": {
            "supported": capture.inferable,
            "reason": capture.inferable_reason,
        },
    }


class AnnotationRoutesMixin:
    """Observe, capture preview and tag save routes over PR A's storage."""

    def _register_annotation_routes(self, routes: web.RouteTableDef) -> None:
        routes.post("/prompt_manager/image-annotations/observe")(self.observe_image)
        routes.get(CAPTURE_URL + "{token}")(self.get_capture_preview)
        routes.delete(CAPTURE_URL + "{token}")(self.release_capture)
        routes.put("/prompt_manager/image-annotations/tags")(self.save_image_tags)
        routes.post("/prompt_manager/image-annotations/suggest")(
            self.suggest_image_tags
        )

    # -- capture store lifetime ------------------------------------------------

    def _capture_store(self) -> CaptureStore:
        """The process's capture store, created on first use.

        Created lazily so an API object that never observes a file (most tests,
        a server that never opens the Tag dialog) owns no temp directory. A
        restore of the database dooms every capture (``invalidate_all``).
        Thread-safe: Needs review recording calls it off the event loop.
        """
        with _CAPTURE_STORE_LOCK:
            if self._captures is None:
                self._captures = CaptureStore()
            if self._captures_unsubscribe is None:
                self._captures_unsubscribe = subscribe_history_restore(
                    self.db.model.db_path, self._invalidate_captures
                )
            return self._captures

    def _invalidate_captures(self) -> None:
        store = self._captures
        if store is not None:
            store.invalidate_all()

    def close_annotation_captures(self) -> None:
        """Remove every capture file and the owned directory. Idempotent."""
        unsubscribe, self._captures_unsubscribe = self._captures_unsubscribe, None
        if unsubscribe is not None:
            unsubscribe()
        store, self._captures = self._captures, None
        if store is not None:
            store.close()

    async def cleanup_annotation_captures(self, app: web.Application) -> None:
        self.close_annotation_captures()

    # -- shared request steps --------------------------------------------------

    async def _annotation_body(self, request: web.Request, fields: frozenset):
        data, error = await self._read_json_body(request, max_bytes=MAX_BODY_BYTES)
        if error is not None:
            return None, error
        if set(data) != fields:
            return None, bad_request(
                "Request must contain exactly " + ", ".join(sorted(fields))
            )
        return data, None

    def _scoped_root(self, index: int, scope: str):
        """``(root, None)`` when the scope names a current root, else an error."""
        roots = list(self._get_all_output_dirs())
        error = self._gallery_scope_error(
            {"root_scope": scope, "root": str(index)}, roots
        )
        if error is not None:
            if error.status == 409:
                return None, _coded("source-changed")
            return None, error
        return roots[index], None

    # -- observe ---------------------------------------------------------------

    async def observe_image(self, request: web.Request) -> web.Response:
        """POST ``{target}``: capture the file and answer its saved tag set.

        The capture is the byte-exact copy the preview and Save refer to; the
        tags are those of exactly these bytes (an empty set at revision 0 for
        unseen bytes). Observing never writes tags; equal bytes refresh the
        record's verified fingerprint (S1).
        """
        data, error = await self._annotation_body(request, _OBSERVE_FIELDS)
        if error is not None:
            return error
        target, error = _target(data["target"])
        if error is not None:
            return error
        index, scope, relative = target
        root, error = self._scoped_root(index, scope)
        if error is not None:
            return error
        store = self._capture_store()
        try:
            epoch = await self._run_in_executor(self.db.get_execution_epoch)
            capture = await _settled(
                _submit(
                    store.capture,
                    root,
                    relative,
                    epoch=epoch,
                    scope=scope,
                    root_index=index,
                ),
                # A cancelled observe never hands its capture out: release it.
                lambda made: made is not None and store.release(made.token),
            )
        except CaptureError as exc:
            return _capture_error(exc)
        except Exception:
            self.logger.exception("Image observation failed")
            return _error(500, "observe-failed", "The image could not be observed.")
        try:
            stored, title = await self._run_in_executor(
                self._observe_capture, store, capture, root, relative
            )
        except asyncio.CancelledError:
            store.release(capture.token)
            raise
        except Exception:
            store.release(capture.token)
            self.logger.exception("Image observation failed")
            return _error(500, "observe-failed", "The image could not be observed.")
        return web.json_response(
            {
                "success": True,
                "library_epoch": capture.epoch,
                "target": {"root_index": index, "root_scope": scope, "path": relative},
                "capture": _capture_info(store, capture),
                "title": title,
                "filename": relative.rsplit("/", 1)[-1],
                "tags": stored["tags"],
                "tag_revision": stored["tag_revision"],
            },
            headers={"Cache-Control": "no-store"},
        )

    def _observe_capture(self, store: CaptureStore, capture, root, relative: str):
        """Blocking part of observe: fingerprint refresh, tag read, title."""
        self.db.refresh_image_fingerprint(
            capture.locator, capture.sha256, capture.size, capture.mtime_ns
        )
        stored = self.db.read_image_tags(capture.locator, capture.sha256)
        return stored, self._observed_title(store, capture, root, relative)

    def _observed_title(self, store: CaptureStore, capture, root, relative: str):
        """The dialog's title under the card and inspector rule (``_verified_title``).

        A stored Library title always shows; a first-line title only when the
        captured bytes' own records confirm it. An ambiguous association has no
        title. Videos read no card metadata, so only a stored title shows.
        """
        path = str(Path(root) / relative)
        summary = self._workspace_link_summaries([path]).get(path)
        if summary is None:
            summary = _unavailable_link()
        positive_title = None
        if (
            capture.media_type != "video"
            and summary.get("title_source") == "text"
            and store.acquire(capture.token) is not None
        ):
            try:
                facts = self._read_card_facts(Path(capture.path), parse=True)
            finally:
                store.release_use(capture.token)
            positive_title = facts.positive_title if facts is not None else None
        return _verified_title(summary, positive_title)

    # -- capture preview -------------------------------------------------------

    async def get_capture_preview(self, request: web.Request) -> web.StreamResponse:
        """GET the captured bytes; the token is pinned while they stream."""
        token = request.match_info["token"]
        store = self._captures
        if store is None or _TOKEN.fullmatch(token) is None:
            return _error(404, "capture-expired", _MESSAGES["capture-expired"])
        capture = store.acquire(token)
        if capture is None:
            return _error(404, "capture-expired", _MESSAGES["capture-expired"])
        pinned = True
        try:
            _root, error = self._scoped_root(capture.root_index, capture.scope)
            if error is not None:
                # The roots changed under the token: it is no longer usable.
                store.release(token)
                return error
            try:
                handle = await _settled(
                    _submit(_open_capture, capture.path),
                    functools.partial(_close_then_release, store, token),
                )
            except asyncio.CancelledError:
                # Cancelled while the file opens: the pin ends only after the
                # handle closes (Windows cannot unlink an open file).
                pinned = False
                raise
            except OSError:
                store.release(token)
                return _error(404, "capture-expired", _MESSAGES["capture-expired"])
            try:
                return await self._stream_capture(request, capture, handle)
            finally:
                handle.close()
        finally:
            if pinned:
                store.release_use(token)

    @staticmethod
    async def _stream_capture(request, capture, handle) -> web.StreamResponse:
        """The capture's bytes, or the one byte range a ``Range`` header asks for.

        The response is written here, inside the caller's pin, so the handle
        is closed and the pin ended only after the last byte is sent.
        """
        size = capture.size
        headers = {
            "Content-Type": preview_content_type(Path(capture.path).suffix),
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }
        status, start, end = 200, 0, size
        if "Range" in request.headers:
            wanted = _byte_range(request, size)
            if wanted is None:
                return web.Response(
                    status=416,
                    headers={**headers, "Content-Range": f"bytes */{size}"},
                )
            status, (start, end) = 206, wanted
            headers["Content-Range"] = f"bytes {start}-{end - 1}/{size}"
        headers["Content-Length"] = str(end - start)
        response = web.StreamResponse(status=status, headers=headers)
        await response.prepare(request)
        loop = asyncio.get_running_loop()
        if start:
            await loop.run_in_executor(None, handle.seek, start)
        remaining = end - start
        while remaining > 0 and request.method != "HEAD":
            chunk = await loop.run_in_executor(
                None, handle.read, min(_PREVIEW_CHUNK, remaining)
            )
            if not chunk:
                break
            remaining -= len(chunk)
            await response.write(chunk)
        await response.write_eof()
        return response

    async def release_capture(self, request: web.Request) -> web.Response:
        """DELETE a capture token; releasing an unknown token is a success."""
        token = request.match_info["token"]
        store = self._captures
        if store is not None and _TOKEN.fullmatch(token) is not None:
            store.release(token)
        return web.json_response(
            {"success": True}, headers={"Cache-Control": "no-store"}
        )

    # -- save ------------------------------------------------------------------

    async def save_image_tags(self, request: web.Request) -> web.Response:
        """PUT the tag set of the previewed bytes, re-hashing the current file.

        Writes only when the file still holds the captured bytes
        (``file-changed`` with zero writes otherwise), under the storage epoch
        and the tag revision the dialog read; an identical retry is
        ``replayed``. The hash never runs inside a database transaction.
        """
        data, error = await self._annotation_body(request, _SAVE_FIELDS)
        if error is not None:
            return error
        target, error = _target(data["target"])
        if error is not None:
            return error
        guards, error = _save_guards(data)
        if error is not None:
            return error
        token, sha256, expected_epoch, revision, tags = guards
        index, scope, relative = target
        root, error = self._scoped_root(index, scope)
        if error is not None:
            return error
        store = self._captures
        capture = store.get(token) if store is not None else None
        if capture is None:
            return _coded("capture-expired")
        if capture.root_index != index or capture.scope != scope:
            return _coded("capture-expired", _OTHER_FILE)
        if sha256 != capture.sha256:
            return bad_request("sha256 does not match the preview")
        if expected_epoch != capture.epoch:
            current = await self._run_in_executor(self.db.get_execution_epoch)
            return _coded("storage-changed", library_epoch=current)
        try:
            facts = await self._run_in_executor(
                _verify_captured_file, store, capture, root, relative
            )
        except CaptureError as exc:
            return _capture_error(exc)
        if facts is None or facts.locator != capture.locator:
            return _coded("capture-expired", _OTHER_FILE)
        if facts.sha256 != capture.sha256:
            return _coded("file-changed")
        try:
            receipt = await self._run_in_executor(
                self.db.replace_image_tags,
                locator=facts.locator,
                sha256=facts.sha256,
                size=facts.size,
                mtime_ns=facts.mtime_ns,
                expected_epoch=expected_epoch,
                expected_tag_revision=revision,
                tags=tags,
            )
        except AnnotationCommandError as exc:
            current = exc.current or {}
            return _coded(
                exc.code,
                library_epoch=exc.epoch,
                tags=current.get("tags", []),
                tag_revision=current.get("tag_revision", 0),
            )
        except LibraryCommandError as exc:
            return _coded(exc.code, library_epoch=exc.epoch)
        except ValueError as exc:
            return bad_request(str(exc))
        except Exception:
            self.logger.exception("Image tag save failed")
            return _error(500, "save-failed", "The tags could not be saved.")
        return web.json_response(
            {"success": True, **receipt}, headers={"Cache-Control": "no-store"}
        )

    # -- suggest ---------------------------------------------------------------

    async def suggest_image_tags(self, request: web.Request) -> web.Response:
        """POST: the top N tags of the captured bytes from one legacy engine.

        Runs only on the private capture (pinned for the whole inference),
        under the shared model gate (``model-busy`` while anything else holds
        it, including a legacy batch), never downloads (``model-unavailable``)
        and never writes.
        """
        from ..autotag import MODELS, get_autotag_service
        from ..config import MAX_SUGGESTION_LIMIT, AutotagConfig

        data, error = await self._annotation_body(request, SUGGEST_FIELDS)
        if error is not None:
            return error
        target, error = _target(data["target"])
        if error is not None:
            return error
        try:
            model_type, options = validate_options(
                data["model_type"],
                data["options"],
                default_limit=AutotagConfig.suggestion_limit(),
                max_limit=MAX_SUGGESTION_LIMIT,
            )
        except OptionError as exc:
            return bad_request(str(exc))
        token, epoch = data["capture_token"], data["expected_epoch"]
        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            return bad_request("Invalid capture token")
        if not isinstance(epoch, str) or _UUID.fullmatch(epoch) is None:
            return bad_request("expected_epoch must be a canonical UUID")
        index, scope, relative = target
        root, error = self._scoped_root(index, scope)
        if error is not None:
            return error
        store = self._captures
        capture = store.acquire(token) if store is not None else None
        if capture is None:
            return _coded("capture-expired")
        started = False
        try:
            error = await self._suggest_guards(capture, target, root, epoch)
            if error is not None:
                return error
            service = get_autotag_service()
            status = await self._run_in_executor(service.get_models_status)
            if not status.get(model_type, {}).get("downloaded"):
                name = MODELS.get(model_type, {}).get("name", model_type)
                return _coded("model-unavailable", unavailable_message(name))
            owner = model_gate.new_owner("image-annotations")
            if not model_gate.try_acquire(owner):
                return model_busy_response()
            try:
                # From here the pin is the gate's to end: the worker ends it,
                # or ``on_skipped`` does when the worker never starts.
                started = True
                pairs = await model_gate.run(
                    owner,
                    _pinned_suggestions,
                    store,
                    token,
                    service,
                    capture,
                    model_type,
                    options,
                    on_skipped=functools.partial(store.release_use, token),
                )
            except Exception:
                self.logger.exception("Tag suggestion failed")
                return _error(
                    500,
                    "suggest-failed",
                    "Auto-tagging failed. Check server logs for details.",
                )
            finally:
                model_gate.release(owner)
        finally:
            if not started:
                store.release_use(token)
        return web.json_response(
            {
                "success": True,
                "model_type": model_type,
                "options": options,
                "suggestions": suggestion_rows(pairs),
            },
            headers={"Cache-Control": "no-store"},
        )

    async def _suggest_guards(self, capture, target, root, epoch):
        """The capture must be this target's, of this epoch, and inferable."""
        index, scope, relative = target
        expected = os.path.normcase(
            os.path.join(os.path.realpath(root), *relative.split("/"))
        )
        if (
            capture.root_index != index
            or capture.scope != scope
            or capture.locator != expected
        ):
            return _coded("capture-expired", _OTHER_FILE)
        if epoch != capture.epoch:
            current = await self._run_in_executor(self.db.get_execution_epoch)
            return _coded("storage-changed", library_epoch=current)
        if not capture.inferable:
            return _coded("unsupported-media", capture.inferable_reason)
        return None


def _close_then_release(store: CaptureStore, token: str, handle) -> None:
    """Close a preview handle a cancelled request opened, then end its pin."""
    try:
        if handle is not None:
            handle.close()
    finally:
        store.release_use(token)


def _byte_range(request: web.Request, size: int):
    """``(start, end)`` of the one satisfiable byte range asked for, else None.

    A malformed, multi-range or unsatisfiable header is None (416), as
    aiohttp's own file responses answer it.
    """
    try:
        wanted = request.http_range
    except ValueError:
        return None
    start, stop = wanted.start, wanted.stop
    if start is None:
        return None
    if start < 0:  # a suffix: the last -start bytes
        start = max(0, size + start)
        stop = size
    stop = size if stop is None else min(stop, size)
    if start >= size or stop <= start:
        return None
    return start, stop


def _pinned_suggestions(store, token, service, capture, model_type, options):
    """Worker thread: infer on the capture, then end its pin (even on error)."""
    try:
        return run_suggestions(service, capture.path, model_type, options)
    finally:
        store.release_use(token)


def _verify_captured_file(store: CaptureStore, capture, root, relative: str):
    """Re-hash the current file, or None when the token names another file.

    The cheap locator comparison runs first, so a token sent for another
    target never costs a whole-file hash.
    """
    expected = os.path.normcase(
        os.path.join(os.path.realpath(root), *relative.split("/"))
    )
    if expected != capture.locator:
        return None
    return store.verify(root, relative)


def _save_guards(data: dict):
    """Validated ``(token, sha256, epoch, revision, tags)`` or ``(None, error)``."""
    token = data["capture_token"]
    if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
        return None, bad_request("Invalid capture token")
    sha256 = data["sha256"]
    if not isinstance(sha256, str) or _HEX64.fullmatch(sha256) is None:
        return None, bad_request("sha256 must be 64 lowercase hexadecimal characters")
    epoch = data["expected_epoch"]
    if not isinstance(epoch, str) or _UUID.fullmatch(epoch) is None:
        return None, bad_request("expected_epoch must be a canonical UUID")
    revision = data["expected_tag_revision"]
    if type(revision) is not int or revision < 0:
        return None, bad_request("expected_tag_revision must be a non-negative integer")
    try:
        tags = normalize_image_tags(data["tags"])
    except ImageTagLimitError as exc:
        return None, _error(400, "tag-limit", str(exc))
    except ValueError as exc:
        return None, bad_request(str(exc))
    return (token, sha256, epoch, revision, tags), None
