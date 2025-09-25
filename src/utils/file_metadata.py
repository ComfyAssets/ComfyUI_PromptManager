"""Utilities for inspecting file metadata such as size and dimensions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

try:  # pragma: no cover - optional dependency
    from PIL import Image  # type: ignore
except ImportError:  # pragma: no cover
    Image = None  # type: ignore


@dataclass
class FileMetadata:
    """Container for file metadata information."""

    size: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    format: Optional[str] = None


PathLike = Union[str, Path]


def compute_file_metadata(path: PathLike) -> FileMetadata:
    """Return size and optional image dimensions for ``path``.

    Args:
        path: File path to inspect.

    Returns:
        FileMetadata with whatever information could be extracted. Missing or
        inaccessible files yield an instance with all ``None`` values.
    """
    metadata = FileMetadata()

    if not path:
        return metadata

    file_path = Path(path).expanduser()

    try:
        stat = file_path.stat()
        metadata.size = stat.st_size
    except (FileNotFoundError, OSError):
        return metadata

    # Attempt to get image dimensions if Pillow is available
    if Image is not None:
        try:
            with Image.open(file_path) as img:
                metadata.width, metadata.height = img.size
                metadata.format = img.format
        except Exception:  # pragma: no cover - best effort only
            pass

    if metadata.format is None and file_path.suffix:
        metadata.format = file_path.suffix.lstrip('.').upper()

    return metadata
