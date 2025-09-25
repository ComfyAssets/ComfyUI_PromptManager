"""Helper utilities for safe sorting of mixed-type data."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Tuple


def normalize_sort_value(value: Any) -> Tuple[int, int, Any]:
    """Normalize values so mixed types can be sorted safely.

    Returns ``(missing_flag, type_order, comparable_value)`` where
    ``missing_flag`` pushes ``None`` values to the end, ``type_order`` ensures
    different kinds of data remain comparable, and ``comparable_value`` keeps
    natural ordering within the same type group.
    """
    if value is None:
        return (1, 3, 0)

    if isinstance(value, (int, float)):
        return (0, 0, float(value))

    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return (0, 1, parsed.timestamp())
        except ValueError:
            return (0, 2, value.lower())

    # Fallback: convert to string representation
    return (0, 2, str(value).lower())
