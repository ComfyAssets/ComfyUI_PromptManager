"""Image sources as System › Sources & scans shows them (#249 S4).

- ``source_form_error`` is the lexical check a submitted source path passes
  before any filesystem access: no control characters, no ``..`` part, and
  only the native path forms (on Windows a drive or a UNC share, never the
  ``\\\\?\\`` and ``\\\\.\\`` namespaces, a drive-relative path, an alternate
  data stream, a device name or a part Windows would silently trim; on POSIX
  no Windows drive or backslash form).
- ``describe_sources`` (blocking: it resolves paths) reports the configured
  sources with their validity, the effective sources with their listing
  index and watch state, and the image monitor's state. Every path in it is
  a public path (``_public_path``), never an absolute one.

A source's watch state is ``watching`` when the monitor's observer is alive
and scheduled that directory; ``manual`` when monitoring is off or this
process has no monitor; else ``restart_pending`` (a source added since the
monitor started, monitoring switched on, or an observer that stopped).
"""

import ntpath
import os
import re

MAX_SOURCE_PATH = 4096  # characters
FORM_REASONS = {
    "empty": "Gallery root must be a non-empty path",
    "invalid-form": "Gallery root is not a folder path this server can use",
    "traversal": "Gallery root must not contain '..'",
}
_DRIVE = re.compile(r"[A-Za-z]:")
_POSIX_FOREIGN = re.compile(r"^[A-Za-z]:")
# Reserved device names: Windows opens the device for any folder of that name.
_DEVICE_NAME = re.compile(r"^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(\..*)?$", re.I)
# folder_paths getters and the label of the directory each one reports.
_COMFYUI_LABELS = (
    ("get_output_directory", "ComfyUI output"),
    ("get_input_directory", "ComfyUI input"),
    ("get_temp_directory", "ComfyUI temp"),
    ("get_user_directory", "ComfyUI user"),
)
WATCH_STATES = ("watching", "manual", "restart_pending")


def source_form_error(text, *, windows=None):
    """The form code that refuses ``text`` as a source path, or None.

    ``windows`` selects the path rules (default: this OS). No filesystem
    access happens here.
    """
    if windows is None:
        windows = os.name == "nt"
    if not isinstance(text, str) or len(text) > MAX_SOURCE_PATH:
        return "invalid-form"
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        return "invalid-form"
    text = text.strip()
    if not text:
        return "empty"
    return _windows_form_error(text) if windows else _posix_form_error(text)


def _posix_form_error(text):
    if "\\" in text or _POSIX_FOREIGN.match(text):
        return "invalid-form"  # a Windows path names nothing on this server
    return "traversal" if ".." in text.split("/") else None


def _windows_form_error(text):
    portable = text.replace("/", "\\")
    if portable.startswith(("\\\\?\\", "\\\\.\\")):
        return "invalid-form"  # verbatim and device namespaces
    if portable.startswith("\\\\"):
        drive, rest = ntpath.splitdrive(portable)
        share = drive[2:].split("\\")
        if len(share) != 2 or any(part in ("", ".", "..") for part in share):
            return "invalid-form"
    else:
        drive, rest = ntpath.splitdrive(portable)
        if drive and (not _DRIVE.fullmatch(drive) or not rest.startswith("\\")):
            return "invalid-form"  # "C:folder" is relative to that drive's cwd
    if ":" in rest:
        return "invalid-form"  # an alternate data stream
    parts = rest.split("\\")
    if ".." in parts:
        return "traversal"
    for part in parts:
        if part in ("", "."):
            continue
        if part[-1] in ". " or _DEVICE_NAME.match(part):
            return "invalid-form"
    return None


def identity(path):
    """The comparison key of a directory: resolved and case-folded (Windows)."""
    return os.path.normcase(os.path.realpath(str(path)))


def _comfyui_labels():
    """``{identity: label}`` of the directories ComfyUI reports."""
    try:
        import folder_paths
    except ImportError:
        return {}
    labels = {}
    for getter_name, label in _COMFYUI_LABELS:
        getter = getattr(folder_paths, getter_name, None)
        if getter is None:
            continue
        try:
            directory = getter()
        except Exception:
            continue
        if isinstance(directory, str) and directory:
            labels.setdefault(identity(directory), label)
    return labels


def source_label(path, labels=None):
    """ "ComfyUI output" for ComfyUI's own directories, else the folder name."""
    if labels is None:
        labels = _comfyui_labels()
    label = labels.get(identity(path))
    if label:
        return label
    return os.path.basename(os.path.normpath(str(path))) or str(path)


def _configured(entry, labels, public_path):
    from ..config import GalleryConfig

    code, reason = GalleryConfig.gallery_root_problem(entry)
    try:
        resolved = GalleryConfig.resolve_gallery_root(entry)
    except (OSError, TypeError, ValueError):
        resolved = str(entry)
    return {
        "label": source_label(resolved, labels),
        "path": public_path(resolved),
        "valid": code is None,
        "code": code,
        "reason": reason or None,
    }


def monitor_state(monitor):
    """``(present, observer_alive, scheduled directories)`` of the image monitor."""
    if monitor is None:
        return False, False, []
    status = monitor.get_status()
    scheduled = [str(d) for d in status.get("monitored_directories") or []]
    return True, bool(status.get("observer_alive")), scheduled


def watch_state(path, *, present, alive, scheduled_keys, enabled):
    """``watching``, ``manual`` or ``restart_pending`` (see the module docstring)."""
    if alive and identity(path) in scheduled_keys:
        return "watching"
    if not present or not enabled:
        return "manual"
    return "restart_pending"


def describe_sources(*, configured, output_dirs, auto_detected, monitor, enabled):
    """``(sources, monitor)`` for GET /settings (blocking; public paths only).

    ``configured`` is the configured roots, ``output_dirs`` and
    ``auto_detected`` what ``_output_dirs_with_origin`` returned, ``monitor``
    the running ImageMonitor or None, ``enabled`` the monitoring setting.
    """
    from . import _public_path

    labels = _comfyui_labels()
    present, alive, scheduled = monitor_state(monitor)
    scheduled_keys = {identity(path) for path in scheduled}
    effective = [
        {
            "label": source_label(path, labels),
            "path": _public_path(path),
            "root_index": index,
            "watch": watch_state(
                path,
                present=present,
                alive=alive,
                scheduled_keys=scheduled_keys,
                enabled=enabled,
            ),
        }
        for index, path in enumerate(output_dirs)
    ]
    return (
        {
            "configured": [
                _configured(entry, labels, _public_path) for entry in configured
            ],
            "effective": effective,
            "auto_detected": bool(auto_detected),
        },
        {
            "present": present,
            "observer_alive": alive,
            "scheduled": [_public_path(path) for path in scheduled],
        },
    )


def validate_source(text):
    """``{valid, label, path, code, reason}`` for one submitted path (blocking).

    Writes nothing. An invalid path answers its code and reason with no
    label or path, so a response never echoes what was typed.
    """
    from ..config import GalleryConfig
    from . import _public_path

    code = source_form_error(text)
    reason = FORM_REASONS.get(code)
    if code is None:
        code, reason = GalleryConfig.gallery_root_problem(text)
    if code is not None:
        return {
            "valid": False,
            "label": None,
            "path": None,
            "code": code,
            "reason": reason,
        }
    resolved = GalleryConfig.resolve_gallery_root(text)
    return {
        "valid": True,
        "label": source_label(resolved),
        "path": _public_path(resolved),
        "code": None,
        "reason": None,
    }
