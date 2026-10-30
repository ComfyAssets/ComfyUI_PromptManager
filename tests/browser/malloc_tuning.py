"""Keep the browser test server's large frees going back to the OS (#516).

glibc serves an allocation of at least the mmap threshold (128 KiB at start) with its
own mmap and unmaps it on free. Freeing such a block also raises the threshold, up to
32 MiB, so the server's later image-sized buffers (PIL pixel data, PNG encoding,
whole-file reads in the executor threads) come from per-thread malloc arenas instead.
Those arenas fragment and keep their pages: a server grew from 13 MB to 0.6-1 GB RSS
over one page-workflow shard while its Python heap stayed near 12 MB. Setting
M_MMAP_THRESHOLD fixes the threshold at its start value and turns the raising off.
"""

import ctypes
import sys

M_MMAP_THRESHOLD = -3
MMAP_THRESHOLD_BYTES = 128 * 1024


def pin_mmap_threshold(libc=None) -> bool:
    """Fix glibc's mmap threshold; False where there is no glibc ``mallopt``."""
    if not sys.platform.startswith("linux"):
        return False
    if libc is None:
        try:
            libc = ctypes.CDLL(None)
        except OSError:
            return False
    mallopt = getattr(libc, "mallopt", None)
    if mallopt is None:
        return False
    return bool(mallopt(M_MMAP_THRESHOLD, MMAP_THRESHOLD_BYTES))
