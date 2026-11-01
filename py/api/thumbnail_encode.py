"""Thumbnail encoders (#317): every preview file is written here.

``write_thumbnail(frame, dst, settings)`` encodes a prepared frame
(``thumbnail_frame``: the first frame, RGB, fitted inside the edge) in the
configured format into a hidden temp sibling, closes it, verifies that it
decodes, and renames it over ``dst`` (``os.replace``), so a reader never sees
a partial file and a failure keeps the old preview. ``guard()`` is asked just
before the rename (automatic previews' holds, #249).

Formats: WebP (Pillow, ``method=4``) and JPEG (Pillow, ``optimize``) always
work. JPEG XL needs no new dependency; the first encoder found is used:

1. Pillow, natively or through the ``pillow_jxl`` plugin (imported when
   installed: ``pip install pillow-jxl-plugin``), given the quality itself;
2. ffmpeg built with ``libjxl`` (``-distance``), fed a PNG on stdin;
3. ``cjxl`` from libjxl (``-d``), fed a temp PNG that is closed first.

Quality maps to a JPEG XL distance with libjxl's own
``JxlEncoderDistanceFromQuality``: ``0.1 + (100 - q) * 0.09`` from 30 up
(q80 → d1.9, q85 → d1.45, q90 → d1.0), the quadratic below 30, and q100 →
d0.1 (a preview is never lossless). Pillow's plugin applies the same mapping
inside libjxl. Detection is cached (``detect_jxl_encoder(refresh=True)``
probes again); "none found" is probed again on the settings read
(``encoder_status``) and after MISSING_ENCODER_TTL, so one slow probe never
turns JPEG XL off until a restart. An encoder that fails because its binary
or module is gone makes that write detect again: another encoder found is
used for that file, none at all writes it as WebP (logged once) and
refreshes what Appearance reads. Any other failure (one bad file) raises
without a probe.

Encoder processes (and ffmpeg's video frames, images.py) go through
``run_encoder``: tracked, bounded by a timeout, and killed by
``shutdown_encoders()`` at the app's cleanup, after which none starts.
"""

import importlib
import io
import os
import re
import shutil
import subprocess  # nosec B404 - fixed argv lists, never a shell
import threading
import time
from dataclasses import replace
from pathlib import Path

from PIL import Image

from . import preview_location, preview_store, thumbnail_settings

try:
    from ...utils.logging_config import get_logger
except ImportError:  # tests import the package top-level
    from utils.logging_config import get_logger

logger = get_logger("prompt_manager.thumbnails")

PILLOW_JXL_FORMAT = "JXL"
JXL_UNAVAILABLE = (
    "No JPEG XL encoder found. Install pillow-jxl-plugin "
    "(pip install pillow-jxl-plugin), ffmpeg built with libjxl, or cjxl from "
    "libjxl, then restart ComfyUI."
)
# Seconds an encoder process may run (ffmpeg's video frame shares it).
ENCODE_TIMEOUT = 30
PROBE_TIMEOUT = 10
JXL_EFFORT = 7
WEBP_METHOD = 4
JXL_SIGNATURES = (b"\xff\x0a", b"\x00\x00\x00\x0cJXL \r\n\x87\n")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class PreviewWriteHeld(Exception):
    """A preview write stopped before its rename: ``guard()`` said no (#249)."""


# The result error of a write held before its rename (never remembered).
PREVIEW_HELD = "Preview writes are held while another job runs"


class ThumbnailVerifyError(RuntimeError):
    """The encoded preview did not decode; it was never renamed in."""


class EncoderFailed(RuntimeError):
    """An encoder process failed or wrote nothing."""


class EncoderClosed(RuntimeError):
    """The app is shutting down: no encoder process runs any more."""


# ── temp files ───────────────────────────────────────────────────────


def temp_sibling(dst, tag="part"):
    """Hidden temp path next to *dst* that keeps its extension."""
    dst = Path(dst)
    token = f"{os.getpid()}-{threading.get_ident()}"
    return dst.with_name(f".{dst.stem}.{tag}-{token}{dst.suffix}")


def replace_unless_held(tmp, dst, guard=None):
    """Rename *tmp* over *dst*, unless ``guard()`` is false (PreviewWriteHeld).

    The check and the rename run under ``preview_store.write_lock`` (#320): a
    guard from ``preview_store.write_guard`` also refuses a write that started
    before the thumbnail location changed."""
    with preview_store.committed(guard) as allowed:
        if not allowed:
            raise PreviewWriteHeld(PREVIEW_HELD)
        os.replace(tmp, dst)


# ``temp_sibling``'s names: ``.<stem>.<tag>-<pid>-<thread id><ext>``.
_TEMP_NAME = re.compile(r"^\..+\.(?:part|src)-(\d+)-\d+\.[A-Za-z0-9]+$")
_MOVE_TEMP_NAME = re.compile(r"\.pm-move-[0-9a-f]{8}\.tmp$")
# A temp at least this old (seconds) outlived its write: no encode runs longer.
STRAY_TEMP_AGE = 4 * ENCODE_TIMEOUT
# When this process started (this module's import): a temp carrying its PID
# that is older was left by an earlier run with the same PID (containers).
PROCESS_STARTED = time.time()


def sweep_stray_temps(folder, min_age=None):
    """Remove the temp files a crashed write left under *folder* (a preview
    folder, walked without following links); returns ``(count, bytes)``.

    Only ``temp_sibling`` names are touched, never a link: a folder that is a
    link, junction or reparse point is not walked into (Windows walks into a
    junction even without ``followlinks``), and each file passes
    ``preview_store.may_write`` before it goes. With *min_age*
    (seconds, the startup sweep) a temp younger than that, or one this
    process wrote since it started (it may still be writing it), is left;
    ``min_age=0`` (the clear, which holds every preview write) removes them
    all.
    """
    min_age = STRAY_TEMP_AGE if min_age is None else min_age
    now = time.time()
    own = str(os.getpid())
    count = size = 0
    try:
        if preview_location.is_link_like(folder):
            return count, size
    except OSError as exc:
        logger.debug(f"Not sweeping {folder}: {exc}")
        return count, size
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not _link_like(Path(root) / d)]
        for name in files:
            match = _TEMP_NAME.match(name)
            # A move's copy temp (#320): ``<name>.pm-move-<8 hex>.tmp``.
            move_temp = match is None and _MOVE_TEMP_NAME.search(name)
            if match is None and not move_temp:
                continue
            path = Path(root) / name
            try:
                stat = path.lstat()
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or _link_like(path)
                    or not preview_store.may_write(path)
                ):
                    continue
                if min_age and (
                    now - stat.st_mtime < min_age
                    or move_temp
                    and stat.st_mtime >= PROCESS_STARTED
                    or (
                        match is not None
                        and match.group(1) == own
                        and stat.st_mtime >= PROCESS_STARTED
                    )
                ):
                    continue
                path.unlink()
            except OSError as exc:
                logger.debug(f"Could not remove stray temp {name}: {exc}")
                continue
            count += 1
            size += stat.st_size
    return count, size


def _link_like(path):
    """``is_link_like``, with a path that cannot be checked counted as one."""
    try:
        return preview_location.is_link_like(path)
    except OSError:
        return True


def _unlink_quietly(path):
    try:
        Path(path).unlink()
    except FileNotFoundError:
        # already gone, which is what was wanted
        pass
    except OSError as exc:
        logger.debug(f"Could not remove temp file {Path(path).name}: {exc}")


# ── encoder processes ────────────────────────────────────────────────

_process_lock = threading.Lock()
_processes = set()
_closed = False


def run_encoder(cmd, *, input=None, timeout=ENCODE_TIMEOUT, track=None):
    """Run *cmd* (a fixed argv) with *input* on stdin; returns a
    CompletedProcess with bytes output.

    The process is tracked so ``shutdown_encoders`` can kill it; past
    *timeout* it is killed and TimeoutExpired raised. After the shutdown
    nothing starts (EncoderClosed). ``track(process)`` sees it too.
    """
    with _process_lock:
        if _closed:
            raise EncoderClosed("Encoders are shut down")
    process = subprocess.Popen(  # nosec B603 - fixed argv, no shell
        cmd,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_NO_WINDOW,
    )
    with _process_lock:
        _processes.add(process)
        closed = _closed
    try:
        if closed:
            process.kill()
        if track is not None:
            track(process)
        try:
            out, err = process.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
    finally:
        with _process_lock:
            _processes.discard(process)
            closed = _closed
    if closed:
        raise EncoderClosed("Encoder stopped by the shutdown")
    return subprocess.CompletedProcess(cmd, process.returncode, out, err)


def shutdown_encoders():
    """Kill every running encoder process; none starts afterwards."""
    global _closed
    with _process_lock:
        _closed = True
        running = list(_processes)
    for process in running:
        try:
            process.kill()
        except OSError:
            # the process has already exited
            pass


def reset_encoders():
    """Allow encoders again and forget the detection (tests, a reload)."""
    global _closed, _detected, _fallback_logged
    with _process_lock:
        _closed = False
    with _detect_lock:
        _detected = _UNSET
        _fallback_logged = False


# ── JPEG XL detection ────────────────────────────────────────────────

_UNSET = object()
_detect_lock = threading.Lock()
_detected = _UNSET
_detected_at = 0.0
# Seconds after which "no JPEG XL encoder" is probed again.
MISSING_ENCODER_TTL = 60
_fallback_logged = False


def _pillow_jxl_format():
    """Pillow's JPEG XL format id when it can save one, else None."""
    try:
        native = Image.registered_extensions().get(".jxl")
    except Exception:
        native = None
    if native and native in Image.SAVE:
        return native
    return PILLOW_JXL_FORMAT if PILLOW_JXL_FORMAT in Image.SAVE else None


def _pillow_can_save_jxl():
    if _pillow_jxl_format():
        return True
    try:
        importlib.import_module("pillow_jxl")
    except Exception:
        return False
    return _pillow_jxl_format() is not None


def _pillow_decodes_jxl():
    try:
        native = Image.registered_extensions().get(".jxl")
    except Exception:
        native = None
    return bool(native and native in Image.OPEN) or PILLOW_JXL_FORMAT in Image.OPEN


def _ffmpeg_has_libjxl():
    """Whether the ffmpeg on PATH lists the ``libjxl`` encoder."""
    if not shutil.which("ffmpeg"):
        return False
    try:
        done = run_encoder(
            ["ffmpeg", "-hide_banner", "-encoders"], timeout=PROBE_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError, EncoderClosed):
        return False
    listing = done.stdout
    if isinstance(listing, bytes):
        listing = listing.decode("utf-8", "replace")
    return any(
        len(line.split()) > 1 and line.split()[1] == "libjxl"
        for line in listing.splitlines()
    )


def _detect():
    if _pillow_can_save_jxl():
        return "pillow"
    if _ffmpeg_has_libjxl():
        return "ffmpeg"
    if shutil.which("cjxl"):
        return "cjxl"
    return None


def detect_jxl_encoder(refresh=False, recheck_missing=False):
    """``pillow``, ``ffmpeg``, ``cjxl`` or None (cached; blocking on a probe).

    A cached None is probed again with *recheck_missing* or once it is older
    than MISSING_ENCODER_TTL."""
    global _detected, _detected_at
    with _detect_lock:
        now = time.monotonic()
        stale_missing = _detected is None and (
            recheck_missing or now - _detected_at >= MISSING_ENCODER_TTL
        )
        if refresh or _detected is _UNSET or stale_missing:
            _detected = _detect()
            _detected_at = now
        return _detected


def _encoder_gone(encoder):
    """Whether *encoder*'s binary or module is no longer there."""
    if encoder == "pillow":
        return not _pillow_can_save_jxl()
    return shutil.which(encoder) is None


def encoder_status():
    """Which formats can be written now (``GET /prompt_manager/settings``;
    a "none found" is probed again here)."""
    encoder = detect_jxl_encoder(recheck_missing=True)
    return {
        "webp": {"available": True},
        "jpeg": {"available": True},
        "jxl": {
            "available": encoder is not None,
            "encoder": encoder,
            "reason": None if encoder is not None else JXL_UNAVAILABLE,
        },
    }


def jxl_distance(quality):
    """libjxl's distance for a 1–100 quality (q100 gives 0.1, never lossless)."""
    if quality >= 100:
        return 0.1
    if quality >= 30:
        return 0.1 + (100 - quality) * 0.09
    return 53.0 / 3000.0 * quality * quality - 23.0 / 20.0 * quality + 25.0


# ── frames and writers ───────────────────────────────────────────────


def thumbnail_frame(img, edge):
    """The first frame of *img* as RGB, fitted inside ``edge`` x ``edge``."""
    try:
        img.seek(0)
    except (EOFError, AttributeError, ValueError):
        # not seekable or a single frame: use the current one
        pass
    try:
        img.draft("RGB", (edge, edge))  # JPEG: decode at a reduced scale
    except (AttributeError, ValueError, OSError):
        # draft is only a speedup; the full decode below still works
        pass
    frame = img.convert("RGB") if img.mode != "RGB" else img.copy()
    frame.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    return frame


def _png_bytes(img):
    buffer = io.BytesIO()
    img.save(buffer, "PNG", compress_level=1)
    return buffer.getvalue()


def _write_webp(img, tmp, settings, track):
    img.save(tmp, "WEBP", quality=settings.quality, method=WEBP_METHOD)


def _write_jpeg(img, tmp, settings, track):
    img.save(tmp, "JPEG", quality=settings.quality, optimize=True)


def _check(done, tmp, name):
    if done.returncode != 0 or not Path(tmp).is_file():
        detail = (done.stderr or b"")[-300:]
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        raise EncoderFailed(f"{name} could not encode the preview: {detail.strip()}")


class _EncoderGone(RuntimeError):
    """No JPEG XL encoder is left: this file is written as WebP."""


def _write_jxl(img, tmp, settings, track):
    """Encode with the detected encoder. One that failed because it is gone
    makes the write detect again once (another one is used, none raises
    _EncoderGone); any other failure raises without a probe."""
    encoder = detect_jxl_encoder()
    try:
        _encode_jxl(encoder, img, tmp, settings, track)
        return
    except (OSError, EncoderFailed) as exc:
        if encoder is not None and not _encoder_gone(encoder):
            raise
        again = detect_jxl_encoder(refresh=True)
        if again is None:
            raise _EncoderGone(str(exc)) from exc
        if again == encoder:
            raise
        logger.info(f"JPEG XL encoder {encoder} failed; using {again}")
    _unlink_quietly(tmp)
    _encode_jxl(again, img, tmp, settings, track)


def _encode_jxl(encoder, img, tmp, settings, track):
    distance = f"{jxl_distance(settings.quality):.2f}"
    if encoder == "pillow":
        img.save(
            tmp,
            _pillow_jxl_format() or PILLOW_JXL_FORMAT,
            quality=min(settings.quality, 99),
            effort=JXL_EFFORT,
            num_threads=1,
        )
        return
    if encoder == "ffmpeg":
        cmd = [
            "ffmpeg", "-v", "error", "-f", "png_pipe", "-i", "-",
            "-frames:v", "1", "-c:v", "libjxl", "-distance", distance,
            "-effort", str(JXL_EFFORT), "-f", "image2", "-update", "1",
            "-y", str(tmp),
        ]  # fmt: skip
        _check(run_encoder(cmd, input=_png_bytes(img), track=track), tmp, "ffmpeg")
        return
    if encoder == "cjxl":
        source = temp_sibling(Path(tmp).with_suffix(".png"), "src")
        try:
            with open(source, "wb") as handle:
                handle.write(_png_bytes(img))
            cmd = ["cjxl", str(source), str(tmp), "-d", distance, "-e", str(JXL_EFFORT)]
            _check(run_encoder(cmd, track=track), tmp, "cjxl")
        finally:
            _unlink_quietly(source)
        return
    raise EncoderFailed(JXL_UNAVAILABLE)


_WRITERS = {"webp": _write_webp, "jpeg": _write_jpeg, "jxl": _write_jxl}


def verify_thumbnail(path, fmt):
    """Raise ThumbnailVerifyError unless *path* decodes as a preview.

    A JPEG XL file Pillow cannot read (no plugin) is checked by signature.
    """
    try:
        if fmt == "jxl" and not _pillow_decodes_jxl():
            with open(path, "rb") as handle:
                head = handle.read(12)
            if not head.startswith(JXL_SIGNATURES):
                raise ThumbnailVerifyError("The JPEG XL preview has no signature")
            return
        with Image.open(path) as written:
            written.load()
    except ThumbnailVerifyError:
        raise
    except Exception as exc:
        raise ThumbnailVerifyError(f"The new preview does not decode: {exc}") from exc


def _jxl_fallback():
    """Log once that JPEG XL previews are being written as WebP."""
    global _fallback_logged
    with _detect_lock:
        first, _fallback_logged = not _fallback_logged, True
    if first:
        logger.warning(
            "JPEG XL is the thumbnail format but no JPEG XL encoder is available "
            "any more; previews are written as WebP until one is installed"
        )


def writer_settings(settings=None):
    """The settings a preview is written with now: the configured ones, but
    WebP while JPEG XL has no encoder (logged once). Its extension names the
    file, so "outdated" means a preview this server would not write now."""
    settings = settings or thumbnail_settings.current()
    if settings.format == "jxl" and detect_jxl_encoder() is None:
        _jxl_fallback()
        return replace(settings, format="webp")
    return settings


def write_thumbnail(frame, dst, settings, *, guard=None, track=None):
    """Encode *frame* into *dst* in ``settings``' format; returns the path
    written (``.webp`` when JPEG XL fell back). Atomic and verified; raises
    PreviewWriteHeld, ThumbnailVerifyError, EncoderFailed, EncoderClosed or
    the OSError of the write."""
    dst = Path(dst)
    fmt = settings.format
    if fmt == "jxl" and detect_jxl_encoder() is None:
        _jxl_fallback()
        fmt, dst = "webp", dst.with_suffix(".webp")
    try:
        return _write_verified(frame, dst, fmt, settings, guard, track)
    except _EncoderGone:
        _jxl_fallback()
        dst = dst.with_suffix(".webp")
        return _write_verified(frame, dst, "webp", settings, guard, track)


def _write_verified(frame, dst, fmt, settings, guard, track):
    tmp = temp_sibling(dst)
    try:
        _WRITERS[fmt](frame, tmp, settings, track)
        verify_thumbnail(tmp, fmt)
        replace_unless_held(tmp, dst, guard)
    finally:
        _unlink_quietly(tmp)
    return dst
