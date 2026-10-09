"""Teardown guard: fail when a file about to be deleted is still open.

Windows refuses to delete an open file, so a sqlite connection that was not
closed explicitly turns into a PermissionError in CI there. Linux would let
the deletion through silently; this check makes the Linux run catch the same
mistake. It inspects /proc/self/fd and is a no-op where that does not exist.
"""

import os


def open_handles_under(path):
    """Basenames of this process's open files at *path* or below it."""
    fd_dir = "/proc/self/fd"
    if not os.path.isdir(fd_dir):
        return []
    real = os.path.realpath(str(path))
    hits = []
    for fd in os.listdir(fd_dir):
        try:
            target = os.readlink(os.path.join(fd_dir, fd))
        except OSError:
            continue
        if not target.startswith(os.sep):
            continue  # sockets, pipes, anon inodes
        target = target.replace(" (deleted)", "")
        if (
            target == real
            or target.startswith(real + os.sep)
            or target.startswith(real + "-")
        ):
            hits.append(os.path.basename(target))
    return sorted(hits)


def assert_closed(testcase, *paths):
    """Fail *testcase* if any of *paths* (files or directories) is still open."""
    hits = [hit for path in paths for hit in open_handles_under(path)]
    if hits:
        testcase.fail(
            "Files still open at teardown (Windows could not delete them): "
            + ", ".join(hits)
        )
