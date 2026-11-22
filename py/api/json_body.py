"""The one bounded reader for JSON object request bodies.

The body is read in chunks, so an oversized or chunked upload is refused as
soon as it crosses the cap rather than buffered whole. ``read_json_object``
raises; ``read_json_body`` answers with the API's ``{"success": false}``
envelope.
"""

import json

from aiohttp import web

JSON_BODY_MAX_BYTES = 1_000_000
_READ_CHUNK = 64 * 1024
INVALID_JSON = "Request body must be valid JSON"
NOT_AN_OBJECT = "Request body must be a JSON object"


class BodyTooLarge(Exception):
    """The request body is longer than the reader's ``max_bytes``."""


def json_error(message, status):
    return web.json_response({"success": False, "error": message}, status=status)


async def read_json_object(request, max_bytes=JSON_BODY_MAX_BYTES):
    """The request's JSON object body of at most ``max_bytes``.

    Raises:
        BodyTooLarge: the declared or streamed body crosses ``max_bytes``.
        ValueError: the body is not UTF-8 JSON (``INVALID_JSON``) or not a
            JSON object (``NOT_AN_OBJECT``).
    """
    declared = request.content_length
    if declared is not None and declared > max_bytes:
        raise BodyTooLarge()
    chunks, size = [], 0
    async for chunk in request.content.iter_chunked(_READ_CHUNK):
        size += len(chunk)
        if size > max_bytes:
            raise BodyTooLarge()
        chunks.append(chunk)
    try:
        data = json.loads(b"".join(chunks).decode("utf-8"))
    except ValueError:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        raise ValueError(INVALID_JSON) from None
    if not isinstance(data, dict):
        raise ValueError(NOT_AN_OBJECT)
    return data


async def read_json_body(request, max_bytes=JSON_BODY_MAX_BYTES):
    """``(data, None)``, or ``(None, response)`` with a 413 or 400 refusal."""
    try:
        return await read_json_object(request, max_bytes), None
    except BodyTooLarge:
        return None, json_error(f"Request body too large (max {max_bytes} bytes)", 413)
    except ValueError as exc:
        return None, json_error(str(exc), 400)
