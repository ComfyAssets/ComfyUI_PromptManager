"""One bounded current-file inspection worker shared by physical identities."""

import os
import stat
from pathlib import Path
from typing import Any, Callable

try:
    from ...utils.prompt_inspector import (
        MetadataReadError,
        inspect_prompt_evidence,
        read_inspection_file,
        unreadable_evidence,
    )
    from ...utils.prompt_pair_resolver import (
        INSPECTION_PROFILE,
        PREVIEW_INSPECTION_PROFILE,
    )
    from ...utils.video_metadata import is_video_path
except ImportError:
    from utils.prompt_inspector import (
        MetadataReadError,
        inspect_prompt_evidence,
        read_inspection_file,
        unreadable_evidence,
    )
    from utils.prompt_pair_resolver import (
        INSPECTION_PROFILE,
        PREVIEW_INSPECTION_PROFILE,
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


def _in_host_temp(path: Path) -> bool:
    """Whether a file lies in ComfyUI's temp folder, where previews are written.

    An unknown temp folder reads as no: the file is then inspected as saved
    output.
    """
    try:
        import folder_paths

        temp = folder_paths.get_temp_directory()
    except (ImportError, AttributeError, OSError):
        return False
    if type(temp) is not str or not temp:
        return False
    try:
        folder = os.path.normcase(os.path.realpath(temp))
        target = os.path.normcase(os.path.realpath(path))
        return os.path.commonpath([folder, target]) == folder
    except (OSError, ValueError):
        return False


def image_tags_reader(
    db: Any, locator: str, logger: Any, *, close: bool = False
) -> Callable[[int, int], dict]:
    """The inspector's ``image_tags`` reader for one file *locator* (S1).

    The returned function takes the inspection's own stable stat and answers
    ``current`` only for a byte record verified at exactly that
    ``(size, mtime_ns)``; a failed read is ``unavailable``, never an error.
    ``close`` closes the calling thread's connection afterwards, for routes
    that close theirs after reading (History).
    """

    def read(size: int, mtime_ns: int) -> dict:
        try:
            found = db.current_image_tags({locator: (size, mtime_ns)})
        except Exception:
            logger.warning("Inspection image tags could not be read", exc_info=True)
            return {"state": "unavailable", "tags": []}
        finally:
            if close:
                db.model.close()
        tags = found.get(locator)
        if tags is None:
            return {"state": "none", "tags": []}
        return {"state": "current", "tags": list(tags)}

    return read


def inspect_current_file(
    path: Path,
    *,
    source_label: str,
    saved: dict | None,
    library: dict,
    image_tags: Callable[[int, int], dict] | None = None,
    recorded: Callable[[Path], Any] | None = None,
) -> dict:
    """Current-file evidence read between two equal stats of *path*.

    *image_tags*, when given, is called once with that stable
    ``(st_size, st_mtime_ns)`` and its facet is returned as ``image_tags``
    (spec amendment S1: tags of a byte record verified at exactly that stat).
    *recorded*, when given, is asked within the same stat window for what
    recorded run history knows about *path* (``RecordedOutput`` or None: the
    save node that wrote it and the texts PromptManager recorded, #386).
    """
    before = _current_file_facts(path)
    try:
        inspection = read_inspection_file(path)
    except MetadataReadError:
        inspection = None
    run = recorded(path) if recorded is not None and inspection is not None else None
    after = _current_file_facts(path)
    fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, key) != getattr(after, key) for key in fields):
        raise CurrentFileChanged
    facets = {}
    if image_tags is not None:
        facets["image_tags"] = image_tags(before.st_size, before.st_mtime_ns)
    if inspection is None:
        evidence = unreadable_evidence()
    else:
        role_saved = (
            None if saved and saved.get("association_status") == "ambiguous" else saved
        )
        # A preview may be the output of a stage that reaches only previews.
        profile = (
            PREVIEW_INSPECTION_PROFILE if _in_host_temp(path) else INSPECTION_PROFILE
        )
        # The file name anchors on the saver that wrote it (#381); recorded run
        # history names that saver when the file alone cannot (#386).
        evidence = inspect_prompt_evidence(
            inspection.metadata,
            role_saved,
            profile=profile,
            file_name=path.name,
            recorded_saver_id=run.saver_id if run is not None else None,
            recorded_texts=run.texts if run is not None else None,
        )
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
        **facets,
    }
