"""Strict JSON output for values that may carry ComfyUI's non-finite numbers.

ComfyUI writes its metadata with Python's default ``json.dumps``, so a graph can
hold ``NaN``, ``Infinity`` and ``-Infinity`` (an ``IS_CHANGED`` that returns
``float("nan")`` puts ``"is_changed": [NaN]`` into every saved prompt). Those
literals are not JSON: browsers' ``JSON.parse`` and ``allow_nan=False`` reject
them. Reading follows the legacy pages (``parse_page_json`` where the browser
parser is mirrored; Python's ``json.loads`` elsewhere). Whatever PromptManager
stores or answers as JSON goes through ``dumps_strict``, which writes each
non-finite number as ``null`` (the legacy pages' own replacement for NaN) and
leaves strings, including the words ``NaN`` or ``Infinity`` inside prompt
text, untouched.
"""

import json
from typing import Any

__all__ = ["dumps_strict"]


def _null(_constant: str) -> None:
    return None


def dumps_strict(value: Any, **kwargs: Any) -> str:
    """``json.dumps(value, allow_nan=False, **kwargs)`` with non-finite numbers null.

    A value without non-finite numbers serializes byte for byte as the plain
    call does. Any other error (a cycle, an unserializable object) still raises.
    """
    kwargs.pop("allow_nan", None)
    try:
        return json.dumps(value, allow_nan=False, **kwargs)
    except ValueError as error:
        if "Out of range float" not in str(error):
            raise
    # The non-strict text spells each non-finite number as a bare literal, never
    # inside a string, so parsing it back maps exactly those numbers to null.
    lenient = json.dumps(value, allow_nan=True, default=kwargs.get("default"))
    return json.dumps(
        json.loads(lenient, parse_constant=_null), allow_nan=False, **kwargs
    )
