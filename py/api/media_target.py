"""The listing identity of one media file, as a request names it.

Shared by the image-annotation routes and the collection routes (#331): a
target is ``{root_index, root_scope, path}``, checked here without touching
the filesystem or the database. The scope is compared with the current roots
by the caller.
"""

import os
import re

from .images import _file_not_found, _forbidden, _has_traversal
from .prompts import bad_request

try:
    from ...utils.media_capture import CAPTURE_EXTENSIONS
    from ...utils.media_folders import in_trash_folder
except ImportError:
    from utils.media_capture import CAPTURE_EXTENSIONS
    from utils.media_folders import in_trash_folder

MAX_TARGET_PATH = 1024
TARGET_FIELDS = frozenset({"root_index", "root_scope", "path"})
_HEX64 = re.compile(r"[0-9a-f]{64}")
TAGGING_MEDIA_MESSAGE = "Only media files can be tagged"


def media_target(data: object, *, media_message: str = TAGGING_MEDIA_MESSAGE):
    """``((index, scope, relative), None)`` for a target, else ``(None, error)``.

    Only the listing identity is accepted: an int root index, the root's
    64-hex scope and a bounded root-relative POSIX media path, never an
    absolute, drive-qualified, backslash or ``..`` path (a 400 for a malformed
    shape, a 403 for an unsafe or non-media path, carrying *media_message*, a
    404 for a path in a trash folder, #543). A link or a short-name spelling
    is refused later by the capture store's alias check.
    """
    if not isinstance(data, dict) or set(data) != TARGET_FIELDS:
        return None, bad_request("target must name root_index, root_scope and path")
    index, scope, relative = data["root_index"], data["root_scope"], data["path"]
    if type(index) is not int or index < 0:
        return None, bad_request("Invalid gallery root")
    if not isinstance(scope, str) or _HEX64.fullmatch(scope) is None:
        return None, bad_request("Invalid gallery root scope")
    if not isinstance(relative, str) or not relative or len(relative) > MAX_TARGET_PATH:
        return None, bad_request("path must be a relative media path")
    parts = relative.split("/")
    if (
        "\0" in relative
        or "\\" in relative
        or relative.startswith("/")
        or _has_traversal(relative)
        or any(part in ("", ".", "..") or ":" in part for part in parts)
    ):
        return None, _forbidden()
    if os.path.splitext(parts[-1])[1].lower() not in CAPTURE_EXTENSIONS:
        return None, _forbidden(media_message)
    if in_trash_folder(relative):
        return None, _file_not_found()
    return (index, scope, relative), None
