"""One bounded current-file inspection worker shared by physical identities."""

import os
import stat
from pathlib import Path

try:
    from ...utils.prompt_inspector import (
        MetadataReadError,
        inspect_prompt_evidence,
        read_inspection_file,
        unreadable_evidence,
    )
    from ...utils.video_metadata import is_video_path
except ImportError:
    from utils.prompt_inspector import (
        MetadataReadError,
        inspect_prompt_evidence,
        read_inspection_file,
        unreadable_evidence,
    )
    from utils.video_metadata import is_video_path


# Only these file types are served or inspected within an authorized directory.
IMAGE_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".bmp",
        ".tiff",
        ".tif",
        ".mp4",
        ".webm",
        ".mov",
    }
)
VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".avi", ".mov", ".mkv", ".m4v", ".wmv"})
SERVABLE_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


class CurrentFileChanged(Exception):
    """File facts are invalid or changed across the bounded metadata observation."""


def _current_file_facts(path: Path) -> os.stat_result:
    if path.suffix.lower() not in SERVABLE_EXTENSIONS:
        raise CurrentFileChanged
    try:
        observed = path.stat()
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        raise CurrentFileChanged from None
    if (
        not stat.S_ISREG(observed.st_mode)
        or not observed.st_mode & 0o444
        or not os.access(path, os.R_OK)
    ):
        raise CurrentFileChanged
    return observed


def inspect_current_file(
    path: Path, *, source_label: str, saved: dict | None, library: dict
) -> dict:
    before = _current_file_facts(path)
    try:
        inspection = read_inspection_file(path)
    except MetadataReadError:
        inspection = None
    after = _current_file_facts(path)
    fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, key) != getattr(after, key) for key in fields):
        raise CurrentFileChanged
    if inspection is None:
        evidence = unreadable_evidence()
    else:
        role_saved = (
            None if saved and saved.get("association_status") == "ambiguous" else saved
        )
        evidence = inspect_prompt_evidence(inspection.metadata, role_saved)
    return {
        "file": {
            "observation": "current-file",
            "source_label": source_label,
            "media_type": "video" if is_video_path(path) else "image",
            "extension": path.suffix.lower(),
            "size": before.st_size,
            "modified_time": before.st_mtime,
            "format": None,
            "width": None,
            "height": None,
        },
        **evidence,
        "inspection": inspection,
        "library": library,
    }
