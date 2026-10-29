"""Revalidated responses for the pages and static files PromptManager serves.

``Cache-Control: no-cache`` lets the browser keep a copy but makes it ask the
server before every reuse, so a PromptManager update reaches the browser on a
normal reload (#478). The ETag lets an unchanged file answer 304 without a
body, which keeps that reload cheap.

The tag hashes the bytes rather than the file's mtime and size: an update can
keep both (an archive extract, a same-length edit within one clock tick), and
the handlers read the whole file anyway. It is weak because the gzip
middleware may send the same content in a different encoding.
"""

import hashlib

from aiohttp import web

REVALIDATE = "no-cache"


def entity_tag(body: bytes) -> str:
    """Weak ETag for ``body``."""
    return f'W/"{hashlib.sha256(body).hexdigest()[:32]}"'


def etag_matches(if_none_match, etag: str) -> bool:
    """True when an If-None-Match header names ``etag`` (weak comparison)."""
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    opaque = etag.removeprefix("W/")
    return any(
        candidate.strip().removeprefix("W/") == opaque
        for candidate in if_none_match.split(",")
    )


def revalidated_response(request, body: bytes, content_type: str, charset=None):
    """200 with ``body``, or 304 when the request already holds this version."""
    etag = entity_tag(body)
    headers = {"Cache-Control": REVALIDATE, "ETag": etag}
    if etag_matches(request.headers.get("If-None-Match"), etag):
        return web.Response(status=304, headers=headers)
    return web.Response(
        body=body, content_type=content_type, charset=charset, headers=headers
    )
