"""Give glibc's free heap pages back to the OS after a long image job (#523).

Image work (decode, resize, encode) leaves image-sized free blocks in the
malloc arenas of the threads that did it; glibc keeps those pages resident.
``release_free_memory`` calls ``malloc_trim(0)`` once, which returns the free
pages of every arena and changes no allocator setting (no ``mallopt``, no
``MALLOC_ARENA_MAX``: ComfyUI owns this process's allocator).

glibc only: on Windows, macOS and musl (Alpine) it does nothing. It never
raises; a missing or failing ``malloc_trim`` is a quiet ``False``.
"""

import ctypes
import os
import sys
import threading

_lock = threading.Lock()
# Not looked up yet; after the lookup, the function or None.
_NOT_LOOKED_UP = object()
_trim = _NOT_LOOKED_UP


def _glibc() -> bool:
    """Whether this process runs on glibc (musl has no CS_GNU_LIBC_VERSION)."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        version = os.confstr("CS_GNU_LIBC_VERSION")
    except (AttributeError, ValueError, OSError):
        return False
    return bool(version) and version.startswith("glibc")


def _malloc_trim():
    """glibc's ``malloc_trim``, looked up once; None where there is none."""
    global _trim
    with _lock:
        if _trim is _NOT_LOOKED_UP:
            _trim = _lookup()
        return _trim


def _lookup():
    if not _glibc():
        return None
    try:
        trim = ctypes.CDLL(None).malloc_trim
    except (OSError, AttributeError):
        return None
    trim.argtypes = [ctypes.c_size_t]
    trim.restype = ctypes.c_int
    return trim


def release_free_memory() -> bool:
    """Return free heap pages to the OS once (glibc); True when it ran."""
    trim = _malloc_trim()
    if trim is None:
        return False
    try:
        trim(0)
    except Exception:  # a foreign libc symbol: never fail the caller's job
        return False
    return True
