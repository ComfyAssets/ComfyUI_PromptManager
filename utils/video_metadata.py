"""Prompt metadata from video containers.

Current ComfyUI video savers (core SaveVideo/SaveWEBM, VideoHelperSuite since
``c67ff3e``) write one container tag per key, each a JSON string:
``prompt``, ``workflow``, ``parameters``, ``comfyassets_prompt_pairs``. Older
VideoHelperSuite wrote one ``comment`` tag holding a JSON envelope
``{"prompt": "<graph json>", "workflow": {...}}``; ``description`` is read as a
fallback envelope. ffprobe reads the tags on every platform; when ffprobe is
not installed, videos are skipped with a single notice instead of one error
per file.

``select_video_carrier`` picks exactly one carrier for both the inspector and
the scan, so Images and ingestion read the same prompts from the same file.
The result uses the same shape as PNG text chunks (string values keyed
``prompt`` / ``workflow`` / ...) so the existing prompt parser handles both.
"""

import functools
import json
import logging
import os
import shutil

# Fixed argv, absolute binary, no shell.
import subprocess  # nosec B404

from .pair_execution import EXECUTION_KEY

VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wmv")
FFPROBE_TIMEOUT = 20  # seconds; a stuck probe must not hang a worker thread
# Carrier order: per-key tags, then the ``comment`` envelope, then ``description``.
ROLE_TAG_KEYS = ("prompt", "parameters", EXECUTION_KEY)
METADATA_TAG_KEYS = ("prompt", "workflow", "parameters", EXECUTION_KEY)
ENVELOPE_TAG_KEYS = ("comment", "description")
VIDEO_TAG_KEYS = METADATA_TAG_KEYS + ENVELOPE_TAG_KEYS
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flash on Windows

logger = logging.getLogger("prompt_manager.video")
_missing_notice_shown = False


def is_video_path(path) -> bool:
    return os.path.splitext(str(path))[1].lower() in VIDEO_EXTENSIONS


@functools.lru_cache(maxsize=1)
def ffprobe_path():
    """Absolute path of ffprobe, or None. Cached for the life of the process."""
    return shutil.which("ffprobe")


def reset_missing_notice():
    """Allow the 'ffprobe not found' notice to be logged again (tests)."""
    global _missing_notice_shown
    _missing_notice_shown = False


class DuplicateVideoTagError(ValueError):
    """Two container tags fold to the same metadata key with different text."""


def _folded_tags(tags: dict) -> dict:
    """Known tags keyed by their carrier name.

    Per-key prompt carriers fold ASCII case: Matroska muxers upper-case tag
    names and ffprobe reports them as written. Two tags folding to one key are
    a duplicate record: byte-identical duplicates are one record (as for PNG
    text chunks), differing ones fail the read. Envelope keys (``comment``,
    ``description``) are read exactly as the legacy readers did, never folded.
    """
    folded = {}
    for key, value in tags.items():
        if not isinstance(key, str):
            continue
        if key in ENVELOPE_TAG_KEYS:
            folded[key] = value
            continue
        name = key.lower()
        if name not in METADATA_TAG_KEYS:
            continue
        if name in folded and folded[name] != value:
            raise DuplicateVideoTagError(f"Duplicate video metadata tag: {name}")
        folded[name] = value
    return folded


def _record(value, key):
    """One carrier field as (metadata text or None, record format or None).

    A null marker (or a non-text role value) has no record format: it is kept
    in the metadata as ``None`` so the inspector refuses it instead of letting
    fallback evidence win, while scans drop it.
    """
    if key == "workflow" and not isinstance(value, (str, dict)):
        return None, "invalid"
    if isinstance(value, str):
        return value, "original-text"
    if isinstance(value, dict):
        return json.dumps(value), "reserialized-json"
    return None, None


def _carriers(folded: dict):
    """Candidate carriers in order: (fields, raw envelope text or None)."""
    per_key = {key: folded[key] for key in METADATA_TAG_KEYS if key in folded}
    if per_key:
        yield per_key, None
    for tag in ENVELOPE_TAG_KEYS:
        raw = folded.get(tag)
        if not isinstance(raw, str):
            continue
        try:
            envelope = json.loads(raw)
        except ValueError:
            continue
        if isinstance(envelope, dict):
            yield envelope, raw


def select_video_carrier(tags):
    """Choose the one container carrier of prompt metadata.

    Returns ``(metadata, record_formats, carrier_text)`` or ``None`` when no
    carrier holds a known key. ``metadata`` maps known keys to their text, or
    to ``None`` for an explicit null marker that the inspector must refuse;
    ``record_formats`` is ``original-text``, ``reserialized-json`` (an envelope
    object) or ``invalid`` (a workflow that is neither text nor an object), and
    ``carrier_text`` is the exact envelope for reserialized values (``None``
    for per-key tags). The first carrier holding a role key wins; otherwise the
    first holding ``workflow``. Carriers are never merged.
    Raises :class:`DuplicateVideoTagError` for differing fold-duplicate tags.
    """
    if not isinstance(tags, dict):
        return None
    candidates = list(_carriers(_folded_tags(tags)))
    selected = next(
        (item for item in candidates if any(key in item[0] for key in ROLE_TAG_KEYS)),
        None,
    )
    if selected is None:
        selected = next((item for item in candidates if "workflow" in item[0]), None)
    if selected is None:
        return None
    fields, carrier_text = selected
    metadata, formats = {}, {}
    for key in METADATA_TAG_KEYS:
        if key not in fields:
            continue
        text, record_format = _record(fields[key], key)
        if record_format == "invalid":
            formats[key] = record_format
            continue
        metadata[key] = text
        if record_format is not None:
            formats[key] = record_format
    return metadata, formats, carrier_text


def metadata_from_tags(tags) -> dict:
    """Normalise container tags into PNG-text-chunk shaped metadata for scans.

    Returns ``{}`` when no carrier holds a known key, and when fold-duplicate
    tags make the file's evidence untrustworthy.
    """
    try:
        selected = select_video_carrier(tags)
    except DuplicateVideoTagError as e:
        logger.debug(f"Video tags skipped: {e}")
        return {}
    if selected is None:
        return {}
    metadata, _, _ = selected
    return {key: value for key, value in metadata.items() if isinstance(value, str)}


def read_video_metadata(path, timeout=FFPROBE_TIMEOUT) -> dict:
    """Metadata embedded in the video at ``path``; ``{}`` when none or no ffprobe."""
    global _missing_notice_shown
    probe = ffprobe_path()
    if not probe:
        if not _missing_notice_shown:
            _missing_notice_shown = True
            logger.info(
                "ffprobe not found; prompts embedded in videos are skipped "
                "during scans. Install ffmpeg to include them."
            )
        return {}

    cmd = [
        probe,
        "-v",
        "quiet",
        "-show_entries",
        "format_tags",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(  # nosec B603
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        logger.debug(f"ffprobe failed for {os.path.basename(str(path))}: {e}")
        return {}
    if result.returncode != 0:
        return {}
    try:
        probed = json.loads(result.stdout or "{}")
    except ValueError:
        return {}
    if not isinstance(probed, dict):
        return {}
    fmt = probed.get("format")
    tags = fmt.get("tags") if isinstance(fmt, dict) else None
    return metadata_from_tags(tags)
