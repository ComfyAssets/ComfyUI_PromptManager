"""Thumbnail settings and names (#317): the one place every preview writer reads.

Settings (System › Appearance, ``POST /prompt_manager/settings``, config.json
``thumbnails``): ``thumbnail_format`` (``webp`` default, ``jxl``, ``jpeg``),
``thumbnail_quality`` (1–100, default 80) and ``thumbnail_edge`` (the longest
edge, 128–2048 px, default 512). ``current()`` is the snapshot a writer takes
once per preview; automatic previews, the Activity job, the history route and
the legacy gallery routes all go through it.

Names: a media file ``<rel>/<stem>.<ext>`` previews as
``thumbnails/<rel>/<stem>_thumb<extension of the format>`` (``.webp``,
``.jxl``, ``.jpg``), images and videos alike. Two files with one stem in one
folder share that name (ruling R2). Older releases kept the source's own
extension (``.png``, ``.jpeg``, ``.gif``, ``.webp``, ``.jpg``; ``.jpg`` for a
video): those are read, served and converted, never written. A preview with a
format extension (``.webp``, ``.jxl``, ``.jpg``) belongs to every same-stem
source; a legacy-only one (``.png``, ``.jpeg``, ``.gif``) only to a source
with that extension (``owns``, ruling R4). Lookups take the first existing
preview the source owns, preferring the configured format.
"""

import os
from dataclasses import dataclass
from pathlib import Path

FORMATS = ("webp", "jxl", "jpeg")
FORMAT_EXTENSIONS = {"webp": ".webp", "jxl": ".jxl", "jpeg": ".jpg"}
FORMAT_LABELS = {"webp": "WebP", "jxl": "JPEG XL", "jpeg": "JPEG"}
DEFAULT_FORMAT = "webp"
DEFAULT_QUALITY = 80
DEFAULT_EDGE = 512
MIN_QUALITY, MAX_QUALITY = 1, 100
MIN_EDGE, MAX_EDGE = 128, 2048

# Extensions a format writes, then the ones only older releases wrote.
WRITTEN_EXTENSIONS = (".webp", ".jxl", ".jpg")
LEGACY_EXTENSIONS = (".png", ".jpeg", ".gif")
THUMBNAIL_EXTENSIONS = WRITTEN_EXTENSIONS + LEGACY_EXTENSIONS
CONTENT_TYPES = {
    ".webp": "image/webp",
    ".jxl": "image/jxl",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
}
THUMB_SUFFIX = "_thumb"


@dataclass(frozen=True)
class ThumbnailSettings:
    """One preview's settings: the format, the quality and the longest edge."""

    format: str
    quality: int
    edge: int

    @property
    def extension(self):
        return FORMAT_EXTENSIONS[self.format]

    @property
    def size(self):
        return (self.edge, self.edge)

    def describe(self):
        """``WebP · quality 80 · 512 px`` (log lines and the job's scope)."""
        return f"{FORMAT_LABELS[self.format]} · quality {self.quality} · {self.edge} px"


def current():
    """The configured settings now (read once per preview or per run)."""
    from ..config import PromptManagerConfig

    fmt = PromptManagerConfig.THUMBNAIL_FORMAT
    return ThumbnailSettings(
        fmt if fmt in FORMATS else DEFAULT_FORMAT,
        int(PromptManagerConfig.THUMBNAIL_QUALITY),
        int(PromptManagerConfig.THUMBNAIL_EDGE),
    )


def _whole(value, low, high, message):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(message)
    return value


def validate_format(value):
    if not isinstance(value, str) or value not in FORMATS:
        raise ValueError('thumbnail_format must be "webp", "jxl" or "jpeg"')
    return value


def validate_quality(value):
    return _whole(
        value,
        MIN_QUALITY,
        MAX_QUALITY,
        f"thumbnail_quality must be a whole number from {MIN_QUALITY} to {MAX_QUALITY}",
    )


def validate_edge(value):
    return _whole(
        value,
        MIN_EDGE,
        MAX_EDGE,
        "thumbnail_edge must be a whole number of pixels "
        f"from {MIN_EDGE} to {MAX_EDGE}",
    )


# ── names ────────────────────────────────────────────────────────────


def thumbnail_stem_rel(rel_path):
    """``thumbnails/<rel without extension>_thumb`` (POSIX)."""
    return f"thumbnails/{Path(rel_path).with_suffix('').as_posix()}{THUMB_SUFFIX}"


def thumbnail_rel(rel_path, extension=None):
    """The preview of *rel_path* in *extension* (the configured one when None)."""
    return thumbnail_stem_rel(rel_path) + (extension or current().extension)


def owns(thumb_extension, source_extension):
    """Whether a ``_thumb<thumb_extension>`` preview can belong to a source
    with *source_extension* (ruling R4)."""
    thumb_extension = thumb_extension.lower()
    if thumb_extension in WRITTEN_EXTENSIONS:
        return True
    if thumb_extension in LEGACY_EXTENSIONS:
        return thumb_extension == source_extension.lower()
    return False


def _legacy_extension(source_extension):
    from .inspection_service import VIDEO_EXTENSIONS

    source_extension = source_extension.lower()
    return ".jpg" if source_extension in VIDEO_EXTENSIONS else source_extension


def candidate_extensions(source_extension, preferred=None):
    """The preview extensions a source owns, in lookup order: the configured
    one, the name an older release gave it, then the other written ones."""
    preferred = preferred or current().extension
    ordered = [preferred, _legacy_extension(source_extension), *WRITTEN_EXTENSIONS]
    seen = []
    for extension in ordered:
        if extension not in seen and owns(extension, source_extension):
            seen.append(extension)
    return tuple(seen)


def _is_preview_file(path):
    try:
        return not path.is_symlink() and path.is_file()
    except OSError:
        return False


def _owned(root, rel_path, preferred):
    rel_path = Path(rel_path)
    stem = Path(root) / thumbnail_stem_rel(rel_path)
    for extension in candidate_extensions(rel_path.suffix, preferred):
        path = stem.with_name(stem.name + extension)
        if _is_preview_file(path):
            yield path


def owned_thumbnails(root, rel_path, preferred=None):
    """Every existing preview of *rel_path* under *root*, in lookup order."""
    return list(_owned(root, rel_path, preferred))


def existing_thumbnail(root, rel_path, preferred=None):
    """The preview a listing shows for *rel_path*: the first owned one that
    exists (the configured format first), or None."""
    return next(_owned(root, rel_path, preferred), None)


def content_type(path):
    """The Content-Type of a preview file by its extension, or None."""
    return CONTENT_TYPES.get(os.path.splitext(str(path))[1].lower())
