"""Which still-image formats this install can collect (#652).

PNG, JPEG, WebP, GIF, BMP and TIFF are read by every Pillow PromptManager
supports. AVIF is not: Pillow's floor here is >= 10.1.0 (ComfyUI's), and
Pillow opens AVIF only from 11.0, and only when it was built with an AVIF
decoder. A file-type list that names ``.avif`` unconditionally would hand the
scan and the monitor files this install cannot open, so the lists name it (a
served or deletable file is a file, whatever this Pillow reads) and the
collection points ask :func:`collectable` before a file enters the library.

The scan (``py.api.admin``), the monitor (``utils.image_monitor``), the
gallery walk (``py.api.images``), auto-tagging's listing
(``py.api.autotag_routes``) and the capture store
(``utils.media_capture``) skip AVIF quietly when the answer is no, so a
Pillow without AVIF behaves exactly as it did before #652.
"""

AVIF_EXTENSION = ".avif"


def avif_supported() -> bool:
    """Whether this Pillow can decode AVIF (``PIL.features.check("avif")``).

    Pillow answers False on every version before 11.0 and on a build without
    an AVIF decoder, so it is the one answer the collection points need.
    """
    try:
        from PIL import features
    except ImportError:  # a partial install: no still image is read anyway
        return False
    try:
        return bool(features.check("avif"))
    except Exception:
        return False


def collectable(extension: str) -> bool:
    """Whether a still image with *extension* may enter the library.

    Every extension is collectable except ``.avif``, which needs
    :func:`avif_supported`. The extension is compared case-insensitively.
    """
    return str(extension).lower() != AVIF_EXTENSION or avif_supported()
