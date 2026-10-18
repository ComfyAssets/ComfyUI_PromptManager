"""One bounded current-file inspection worker shared by physical identities."""

from pathlib import Path

try:
    from ...utils.prompt_inspector import (
        MetadataReadError,
        inspect_prompt_evidence,
        read_inspection_file,
    )
    from ...utils.video_metadata import is_video_path
except ImportError:
    from utils.prompt_inspector import (
        MetadataReadError,
        inspect_prompt_evidence,
        read_inspection_file,
    )
    from utils.video_metadata import is_video_path


class CurrentFileChanged(Exception):
    """File facts changed across the bounded metadata observation."""


def inspect_current_file(
    path: Path, *, source_label: str, saved: dict | None, library: dict
) -> dict:
    before = path.stat()
    try:
        inspection = read_inspection_file(path)
    except MetadataReadError:
        inspection = None
    try:
        after = path.stat()
    except FileNotFoundError:
        raise CurrentFileChanged from None
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, key) != getattr(after, key) for key in fields):
        raise CurrentFileChanged
    if inspection is None:
        evidence = {
            "prompts": {
                role: {"status": "unresolved", "text": None, "source": None}
                for role in ("positive", "negative")
            },
            "resolution": {
                "state": "unresolved",
                "sampler_id": None,
                "reason": "Media metadata could not be read",
            },
        }
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
