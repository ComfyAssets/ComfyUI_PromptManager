"""PNG text chunks without decoding pixels (#523).

Pillow's ``PngImageFile.text`` calls ``load()``, which decodes every pixel, to
reach text chunks written after the image data. On a scan or a busy image
monitor that is a full-size buffer per file for a few kilobytes of text.

``png_text`` returns the same dictionary as ``image.text`` for an opened,
single-frame PNG, by walking the chunks after the first IDAT with Pillow's own
chunk handlers (same decoding, limits and duplicate rule) and seeking over the
pixel data. It follows Pillow's ``load_end`` loop: chunk checksums after the
pixels are not checked, an unreadable chunk header ends the walk, and another
chunk that runs past the end of the file fails like ``ImageFile._safe_read``
(unless ``ImageFile.LOAD_TRUNCATED_IMAGES``).

The one difference is pixel data, which is never decoded: pixel data that is
undecodable, or cut short, no longer hides the text read before it. Pillow
itself accepts a file cut inside its last pixel chunk once the decoder has
all rows, and only a decode can tell those apart, so the walk never reads
less than ``.text`` did (the PR #170 "legacy parity floor").

Anything else (other formats, animated PNGs, an image already loaded) is
answered by Pillow itself.
"""

import os
import struct

from PIL import ImageFile

_PIXEL_CHUNKS = (b"IDAT", b"fdAT")
# Signature, IHDR chunk and IDAT chunk header: the earliest pixel data offset.
_FIRST_DATA_OFFSET = 8 + 25 + 8


def png_text(image) -> dict:
    """``dict(image.text)`` for an opened image, without a pixel decode.

    The image stays usable: its file position and Pillow state are restored.
    Non-PNG images answer ``{}`` (only PNG has text chunks in Pillow).
    """
    if not _walkable(image):
        return dict(getattr(image, "text", None) or {})
    fp = image.fp
    resume = fp.tell()
    try:
        return _walk_after_pixels(image.png, fp, image.tile[0][2] - 8)
    finally:
        fp.seek(resume)


def _walkable(image) -> bool:
    """A plain single-frame PNG whose header pass stopped at its first IDAT."""
    tile = getattr(image, "tile", None)
    return (
        image.format == "PNG"
        and getattr(image, "png", None) is not None
        and getattr(image, "fp", None) is not None
        and not getattr(image, "is_animated", False)
        and isinstance(tile, list)
        and len(tile) == 1
        and tile[0][0] == "zip"
        # The first IDAT's data offset; anything else is not a layout we know.
        and isinstance(tile[0][2], int)
        and tile[0][2] >= _FIRST_DATA_OFFSET
    )


def _walk_after_pixels(stream, fp, offset: int) -> dict:
    """Pillow's ``load_end`` chunk loop from the first IDAT header, skipping pixels.

    ``stream`` is the image's own ``PngStream``: its text so far and its text
    memory budget carry on exactly as a ``load()`` would carry them. The text
    and info dictionaries are copied first, so ``image.text`` and
    ``image.info`` are unchanged. Like ``load_end``, a handler for a chunk
    misplaced after the pixels (a second IHDR or PLTE) may still update the
    stream's other header fields; Pillow ignores them once the image is open.
    """
    end = fp.seek(0, os.SEEK_END)
    saved = stream.im_text, stream.im_info, stream.text_memory
    stream.im_text, stream.im_info = dict(saved[0]), dict(saved[1])
    try:
        fp.seek(offset)
        while True:
            try:
                kind, position, length = stream.read()
            except (struct.error, SyntaxError):
                break
            if kind == b"IEND":
                break
            try:
                handled = kind not in _PIXEL_CHUNKS and _handle(
                    stream, kind, position, length
                )
            except UnicodeDecodeError:
                break
            if not handled:
                if position + length > end:
                    if kind in _PIXEL_CHUNKS or ImageFile.LOAD_TRUNCATED_IMAGES:
                        break
                    raise OSError("Truncated File Read")
                fp.seek(position + length + 4)
                continue
            fp.read(4)  # checksum: not verified after the pixels, as in Pillow
        return stream.im_text
    finally:
        stream.im_text, stream.im_info, stream.text_memory = saved


def _handle(stream, kind: bytes, position: int, length: int) -> bool:
    """Run Pillow's handler; ``False`` when the chunk's bytes must be skipped."""
    try:
        stream.call(kind, position, length)
    except (EOFError, AttributeError):
        # A chunk Pillow reads past (EOFError) or has no handler for.
        return False
    return True
