"""Is ComfyUI stopped? The checks a library repair runs before writing (#247).

``comfyui_running`` looks for ``main.py`` processes of one ComfyUI folder
and a listener on its port. ``open_by_others`` lists the processes that hold
the library file, its ``-wal`` or its ``-shm`` open. Both read ``/proc``
(Linux); without it ``open_by_others`` answers ``[NO_PROC]``: it cannot
tell, and the caller refuses unless the user says ComfyUI is stopped.
Entries that cannot be read (another user's process, one that just exited)
are skipped.
"""

import os
import socket

NO_PROC = (
    "cannot list the processes that hold the library open (no /proc): stop "
    "ComfyUI and pass --i-stopped-comfyui"
)


def _same(path):
    return os.path.normcase(os.path.realpath(path))


def _pids(proc, own_pid=None):
    try:
        names = os.listdir(proc)
    except OSError:
        return []
    return [name for name in names if name.isdigit() and name != str(own_pid)]


def comfyui_running(comfyui_dir, port, proc="/proc"):
    """``main.py`` processes running from ``comfyui_dir`` and a listener on
    ``port`` (127.0.0.1), each as one line; empty when none is found."""
    found = []
    folder = _same(comfyui_dir)
    for pid in _pids(proc):
        try:
            with open(os.path.join(proc, pid, "cmdline"), "rb") as stream:
                parts = stream.read().split(b"\0")
            cwd = _same(os.readlink(os.path.join(proc, pid, "cwd")))
        except OSError:
            continue
        if any(part.endswith(b"main.py") for part in parts) and (
            cwd == folder or any(folder.encode() in part for part in parts)
        ):
            found.append(f"process {pid} (main.py in {cwd})")
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            found.append(f"something listens on port {port}")
    return found


def open_by_others(database, proc="/proc", own_pid=None):
    """Processes other than ``own_pid`` (default: this one) holding
    ``database``, its ``-wal`` or ``-shm`` open; ``[NO_PROC]`` without /proc."""
    if not os.path.isdir(proc):
        return [NO_PROC]
    own_pid = os.getpid() if own_pid is None else own_pid
    targets = {_same(database + suffix): suffix for suffix in ("", "-wal", "-shm")}
    found = []
    for pid in _pids(proc, own_pid):
        fd_dir = os.path.join(proc, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        held = set()
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            target = target.replace(" (deleted)", "")
            if os.path.isabs(target) and _same(target) in targets:
                held.add(os.path.basename(database) + targets[_same(target)])
        if held:
            found.append(f"process {pid} has {', '.join(sorted(held))} open")
    return found
