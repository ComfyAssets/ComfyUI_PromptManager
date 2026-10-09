"""Prompt metadata from video containers.

ComfyUI's video savers (core SaveVideo, VideoHelperSuite, ...) write the
queued graph into the container's ``comment`` tag as JSON:
``{"prompt": "<graph json>", "workflow": {...}}``. ffprobe reads it on every
platform; when ffprobe is not installed, videos are skipped with a single
notice instead of one error per file.

The result uses the same shape as PNG text chunks (string values keyed
``prompt`` / ``workflow`` / ``parameters``) so the existing prompt parser
handles both.
"""

import functools
import json
import logging
import os
import shutil

# Fixed argv, absolute binary, no shell.
import subprocess  # nosec B404

VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wmv")
FFPROBE_TIMEOUT = 20  # seconds; a stuck probe must not hang a worker thread
_COMMENT_TAGS = ("comment", "description")
_METADATA_KEYS = ("prompt", "workflow", "parameters")
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


def metadata_from_tags(tags) -> dict:
    """Normalise container tags into PNG-text-chunk shaped metadata.

    Returns ``{}`` when no tag carries a JSON object with a known key.
    """
    if not isinstance(tags, dict):
        return {}
    for tag in _COMMENT_TAGS:
        raw = tags.get(tag)
        if not isinstance(raw, str):
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        found = {}
        for key in _METADATA_KEYS:
            value = data.get(key)
            if value is None:
                continue
            found[key] = value if isinstance(value, str) else json.dumps(value)
        if found:
            return found
    return {}


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
