"""ComfyUI's EXIF metadata records (WebP, AVIF, JPEG).

PNG keeps the workflow and the prompt in text chunks (``utils.png_text``).
The formats without text chunks carry the same records as EXIF strings:
``SaveAnimatedWEBP`` writes ``prompt:{json}`` to tag 0x0110, then
``workflow:{json}`` and every other ``extra_pnginfo`` key to 0x010F and down,
and the AVIF writer uses that layout too. Pillow answers those tags only
through ``getexif()``; ``image.info`` holds the raw EXIF blob, so reading it
as text found no metadata at all.

``exif_text`` returns the ``{key: json}`` shape of ``utils.png_text`` so a
caller reads either source the same way. Only a value shaped like a ComfyUI
record counts, a key and a colon followed by a JSON object, array or string;
a camera's ``Make`` or a ``DateTime`` is not metadata.
"""

import re

# A record's key: ``workflow``, ``prompt`` or an extra_pnginfo key.
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
# What a record holds: the workflow and the prompt are objects, the pair
# records a list, a custom extra_pnginfo value may be a string. Numbers,
# dates and camera names start with anything else.
_STARTS = ("{", "[", '"')


def exif_text(image) -> dict:
    """ComfyUI's EXIF records of an opened image, or ``{}``.

    The image stays usable and nothing is decoded but the EXIF block. Formats
    whose records are text chunks (PNG) answer ``{}``; read those with
    ``utils.png_text.png_text`` first.
    """
    try:
        exif = image.getexif()
    except Exception:  # a broken EXIF block never fails a scan
        return {}
    records = {}
    for value in exif.values():
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        if not isinstance(value, str):
            continue
        key, separator, payload = value.partition(":")
        if separator and _KEY.match(key) and payload.lstrip()[:1] in _STARTS:
            records[key] = payload
    return records
