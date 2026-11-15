"""Remove a folder that a killed test server may still hold for a moment (#636).

On Windows a virtual environment's ``python.exe`` is a launcher: it starts
the real interpreter as a child process and ends it with itself. Killing the
launcher returns at once, while Windows is still ending that child, which
still has its log file open, so removing the folder at that moment fails with
WinError 32. ``remove_when_released`` waits until the folder can be removed,
up to a bound, and raises the last error after it.
"""

import shutil
import time

RELEASE_TIMEOUT_S = 30
POLL_S = 0.05


def remove_when_released(path: str, timeout: float = RELEASE_TIMEOUT_S) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(POLL_S)
