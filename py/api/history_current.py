"""Native reported locations and already-opened current media, without ownership."""

import asyncio
from dataclasses import dataclass
import errno
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import stat
from typing import BinaryIO

from aiohttp import web

from .images import _canonical, _public_path, SERVABLE_EXTENSIONS, VIDEO_EXTENSIONS

try:
    from ...utils.execution_outputs import (
        OutputReference,
        OutputRoots,
        snapshot_output_roots,
        validate_output_reference,
    )
except ImportError:
    from utils.execution_outputs import (
        OutputReference,
        OutputRoots,
        snapshot_output_roots,
        validate_output_reference,
    )

_SECRET = os.urandom(32)
_READ_SIZE = 64 * 1024


def _opaque(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hmac.new(_SECRET, raw, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class CurrentOutputScope:
    path_style: str
    lexical_output: str | None
    lexical_temp: str | None
    roots: OutputRoots
    canonical_output: Path | None
    canonical_temp: Path | None
    authorized_roots: tuple[Path, ...]
    current_scope: str


@dataclass(frozen=True)
class ResolvedCurrentCandidate:
    public: dict
    canonical_target: Path | None = None
    namespace: Path | None = None
    gallery_root: Path | None = None


class CurrentLocationUnavailable(Exception):
    """A bounded current-file failure without the underlying private path."""

    def __init__(self, reason: str, *, stale: bool = False) -> None:
        self.reason = reason
        self.stale = stale
        super().__init__(reason)


def _host_directory(name: str) -> str | None:
    try:
        import folder_paths

        value = getattr(folder_paths, name)()
    except (ImportError, AttributeError, OSError):
        return None
    return value if type(value) is str else None


def snapshot_current_output_scope(api: object) -> CurrentOutputScope:
    """Bind host lexical namespaces independently of current gallery authority."""
    from ..config import GalleryConfig

    style = "windows" if os.name == "nt" else "posix"
    output = _host_directory("get_output_directory")
    temp = _host_directory("get_temp_directory")
    roots = snapshot_output_roots(style, output, temp)
    canonical_output = _canonical(output) if roots.output_key is not None else None
    canonical_temp = _canonical(temp) if roots.temp_key is not None else None
    authorized = tuple(
        sorted({_canonical(path) for path in api._get_all_output_dirs()})
    )
    policy = []
    for entry in GalleryConfig.MONITORING_DIRECTORIES:
        valid, _ = GalleryConfig.validate_gallery_root(entry)
        policy.append((entry if type(entry) is str else None, valid))
    current_scope = _opaque(
        [
            "prompt-manager-current-output-v1",
            style,
            output,
            temp,
            roots.output_key,
            roots.temp_key,
            str(canonical_output) if canonical_output is not None else None,
            str(canonical_temp) if canonical_temp is not None else None,
            [str(path) for path in authorized],
            sorted(
                str(_canonical(path))
                for path in GalleryConfig.allowed_gallery_parents()
            ),
            sorted(policy, key=lambda item: (item[0] or "", item[1])),
            sorted(str(_canonical(path)) for path in GalleryConfig.path_anchors()),
            "gallery-validation-v1",
        ]
    )
    return CurrentOutputScope(
        style,
        output,
        temp,
        roots,
        canonical_output,
        canonical_temp,
        authorized,
        current_scope,
    )


def _descriptor_reason(reference: dict, scope: CurrentOutputScope) -> str | None:
    try:
        validate_output_reference(OutputReference("current", 0, **reference))
    except (TypeError, ValueError):
        return "namespace_mismatch"
    if reference["path_style"] != scope.path_style:
        return "foreign_path_style"
    folder = reference["folder_type"]
    expected = scope.roots.output_key if folder == "output" else scope.roots.temp_key
    if reference["root_key"] is None or expected is None:
        return "root_unavailable"
    if reference["root_key"] != expected:
        return "namespace_mismatch"
    return None


def candidate_locator(reference: dict, scope: CurrentOutputScope) -> str | None:
    """Validate the native namespace before constructing a lexical path; no IO."""
    if _descriptor_reason(reference, scope) is not None:
        return None
    root = (
        scope.lexical_output
        if reference["folder_type"] == "output"
        else scope.lexical_temp
    )
    return os.path.normcase(
        os.path.normpath(
            os.path.join(root, reference["subfolder"], reference["filename"])
        )
    )


def _authorized(target: Path, namespace: Path, scope: CurrentOutputScope) -> bool:
    return target.is_relative_to(namespace) and any(
        target.is_relative_to(root) for root in scope.authorized_roots
    )


def _unavailable(reason: str, *, error: bool = False) -> ResolvedCurrentCandidate:
    return ResolvedCurrentCandidate(
        {
            "status": "error" if error else "unavailable",
            "reason": reason,
            "evidence_class": "locator_candidate",
        }
    )


def resolve_current_candidate(
    reference: dict, scope: CurrentOutputScope, gallery: dict
) -> ResolvedCurrentCandidate:
    """Observe only this exact current locator and its snapshot-owned association."""
    reason = _descriptor_reason(reference, scope)
    if reason is not None:
        return _unavailable(reason)
    lexical = candidate_locator(reference, scope)
    namespace = (
        scope.canonical_output
        if reference["folder_type"] == "output"
        else scope.canonical_temp
    )
    try:
        if Path(reference["filename"]).suffix.lower() not in SERVABLE_EXTENSIONS:
            return _unavailable("unsupported_media")
        if namespace is None or not namespace.is_dir():
            return _unavailable("root_unavailable")
        target = _canonical(lexical)
        if not _authorized(target, namespace, scope):
            return _unavailable("not_authorized")
        if target.suffix.lower() not in SERVABLE_EXTENSIONS:
            return _unavailable("unsupported_media")
        observed = target.stat()
        if not stat.S_ISREG(observed.st_mode):
            return _unavailable("not_regular_file")
        if not os.access(target, os.R_OK) or not observed.st_mode & 0o444:
            return _unavailable("unreadable")
        matching = gallery.get(lexical, {})
        root = max(
            (root for root in scope.authorized_roots if target.is_relative_to(root)),
            key=lambda root: len(root.parts),
        )
        return ResolvedCurrentCandidate(
            {
                "status": "available",
                "reason": None,
                "evidence_class": "locator_candidate",
                "gallery_match": matching.get("gallery_match", "none"),
                "gallery_image_id": matching.get("gallery_image_id"),
                "source_label": _public_path(namespace),
                "relative_path": Path(
                    reference["subfolder"], reference["filename"]
                ).as_posix(),
                "current_scope": scope.current_scope,
                "location_key": _opaque(
                    ["current-location-v1", scope.current_scope, str(target)]
                ),
            },
            target,
            namespace,
            root,
        )
    except FileNotFoundError:
        return _unavailable("missing")
    except PermissionError:
        return _unavailable("unreadable")
    except (OSError, ValueError):
        return _unavailable("temporary_unavailable", error=True)


def _open_current_target(
    target: Path, namespace: Path, scope: CurrentOutputScope
) -> BinaryIO:
    """Open only the canonical target and verify authority against the actual handle."""
    if _canonical(target) != target:
        raise CurrentLocationUnavailable("not_authorized", stale=True)
    if not _authorized(target, namespace, scope):
        raise CurrentLocationUnavailable("not_authorized")
    if target.suffix.lower() not in SERVABLE_EXTENSIONS:
        raise CurrentLocationUnavailable("unsupported_media")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        fd = os.open(target, flags)
    except FileNotFoundError:
        raise CurrentLocationUnavailable("missing") from None
    except PermissionError:
        raise CurrentLocationUnavailable("unreadable") from None
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise CurrentLocationUnavailable("not_authorized", stale=True) from None
        raise CurrentLocationUnavailable("temporary_unavailable") from None
    try:
        observed = os.fstat(fd)
        if not stat.S_ISREG(observed.st_mode):
            raise CurrentLocationUnavailable("not_regular_file")
        if not observed.st_mode & 0o444 or not os.access(target, os.R_OK):
            raise CurrentLocationUnavailable("unreadable")
        current = os.stat(target, follow_symlinks=False)
        if (
            _canonical(target) != target
            or (observed.st_dev, observed.st_ino) != (current.st_dev, current.st_ino)
            or not _authorized(target, namespace, scope)
        ):
            raise CurrentLocationUnavailable("not_authorized", stale=True)
        return os.fdopen(fd, "rb")
    except FileNotFoundError:
        os.close(fd)
        raise CurrentLocationUnavailable("missing") from None
    except PermissionError:
        os.close(fd)
        raise CurrentLocationUnavailable("unreadable") from None
    except BaseException:
        os.close(fd)
        raise


def _reauthorize_current_target(
    resolved: ResolvedCurrentCandidate, scope: CurrentOutputScope
) -> None:
    if _canonical(
        resolved.canonical_target
    ) != resolved.canonical_target or not _authorized(
        resolved.canonical_target, resolved.namespace, scope
    ):
        raise CurrentLocationUnavailable("not_authorized", stale=True)


@dataclass
class _OpenedCurrentMedia:
    handle: BinaryIO
    size: int
    content_type: str

    def close(self) -> None:
        self.handle.close()


def _prepare_current_media(
    resolved: ResolvedCurrentCandidate, scope: CurrentOutputScope, *, thumbnail: bool
) -> _OpenedCurrentMedia:
    target = resolved.canonical_target
    if thumbnail:
        relative = target.relative_to(resolved.gallery_root)
        extension = (
            ".jpg"
            if target.suffix.lower() in VIDEO_EXTENSIONS
            else target.suffix.lower()
        )
        preview = (
            resolved.gallery_root
            / "thumbnails"
            / f"{relative.with_suffix('').as_posix()}_thumb{extension}"
        )
        try:
            preview.stat()
        except FileNotFoundError:
            pass
        else:
            target = _canonical(preview)
    handle = _open_current_target(target, resolved.namespace, scope)
    try:
        return _OpenedCurrentMedia(
            handle,
            os.fstat(handle.fileno()).st_size,
            mimetypes.guess_type(target.name)[0] or "application/octet-stream",
        )
    except BaseException:
        handle.close()
        raise


async def _current_media_response(
    request: web.Request, opened: _OpenedCurrentMedia
) -> web.StreamResponse:
    """Stream a bounded single range from a previously authorized handle."""
    try:
        try:
            selected = request.http_range
            start, stop = selected.start, selected.stop
            partial = request.headers.get("Range") is not None
            if (
                partial
                and request.headers["Range"].startswith("bytes=-")
                and start == 0
            ):
                raise ValueError("Empty suffix range")
            if start is not None and start < 0:
                start = max(0, opened.size + start)
            start = start or 0
            stop = min(opened.size, stop) if stop is not None else opened.size
            if partial and (start >= stop or start >= opened.size):
                raise ValueError("Unsatisfiable current media range")
        except ValueError:
            return web.Response(
                status=416,
                headers={
                    "Cache-Control": "no-store",
                    "Content-Range": f"bytes */{opened.size}",
                },
            )
        headers = {
            "Cache-Control": "no-store",
            "Accept-Ranges": "bytes",
            "Content-Type": opened.content_type,
            "Content-Length": str(stop - start),
        }
        if partial:
            headers["Content-Range"] = f"bytes {start}-{stop - 1}/{opened.size}"
        response = web.StreamResponse(status=206 if partial else 200, headers=headers)
        await response.prepare(request)
        if request.method != "HEAD":
            opened.handle.seek(start)
            remaining = stop - start
            while remaining:
                chunk = await asyncio.get_running_loop().run_in_executor(
                    None, opened.handle.read, min(_READ_SIZE, remaining)
                )
                if not chunk:
                    break
                remaining -= len(chunk)
                await response.write(chunk)
        await response.write_eof()
        return response
    finally:
        opened.close()
