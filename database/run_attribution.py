"""Attribute an output file to a stored prompt through recorded history (#342).

Execution history records, per run, the files ComfyUI reported
(``execution_run_outputs``) and the PromptManager texts the run used
(``execution_run_contexts``, whose ``positive_prompt_id`` hint is set only
while the raw text matches exactly one stored prompt). A file is attributed
only when all of this holds:

- exactly one recorded run reported the file's output location (same output
  root, subfolder and name);
- the file was last written while that run was executing, from its dequeue
  to its terminal event (else the finish callback): a later run that history
  did not record may have reused the name;
- every positive context of that run names the same stored prompt.

Anything else returns None: the caller leaves the file unlinked. Read-only.
"""

import os
import sqlite3
from typing import Any, Optional

try:
    from ..utils.execution_outputs import snapshot_output_roots
    from ..utils.library_location import import_folder_paths
except ImportError:
    from utils.execution_outputs import snapshot_output_roots
    from utils.library_location import import_folder_paths

# File times may trail or lead the recorded run times a little (coarse file
# system clocks, the write finishing just after the host reported it).
MTIME_SLACK_MS = 2000

_RUNS_FOR_OUTPUT = (
    "SELECT DISTINCT o.run_id, r.observed_dequeue_at, "
    "COALESCE(r.terminal_event_at, r.observed_callback_at) "
    "FROM execution_run_outputs o JOIN execution_runs r ON r.run_id = o.run_id "
    "WHERE o.folder_type = 'output' AND o.path_style = ? AND o.root_key = ? "
    "AND o.subfolder = ? AND o.filename = ? LIMIT 2"
)


def host_output_directory() -> Optional[str]:
    """ComfyUI's output directory, or None outside ComfyUI."""
    folder_paths = import_folder_paths()
    if folder_paths is None:
        return None
    try:
        value = folder_paths.get_output_directory()
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


def _location(image_path: str, output_root: str, path_style: str):
    """(root key, subfolder, filename) as history records them, or None."""
    root_key = snapshot_output_roots(path_style, output_root, output_root).output_key
    if root_key is None:
        return None
    try:
        relative = os.path.relpath(
            os.path.abspath(image_path), os.path.abspath(output_root)
        )
    except ValueError:  # another drive on Windows
        return None
    if os.path.isabs(relative) or relative == os.pardir:
        return None
    if relative.startswith(os.pardir + os.sep):
        return None
    subfolder, filename = os.path.split(relative)
    if not filename:
        return None
    return root_key, subfolder, filename


def _written_during(image_path: str, started: Any, finished: Any) -> bool:
    if not isinstance(started, int) or not isinstance(finished, int):
        return False
    try:
        written = os.stat(image_path).st_mtime_ns // 1_000_000
    except OSError:
        return False
    return started - MTIME_SLACK_MS <= written <= finished + MTIME_SLACK_MS


def stored_run_prompt(
    db: Any,
    image_path: str,
    output_root: Optional[str],
    path_style: Optional[str] = None,
) -> Optional[int]:
    """The stored prompt id history attributes to ``image_path``, else None."""
    if not image_path or not output_root:
        return None
    style = path_style or ("windows" if os.name == "nt" else "posix")
    location = _location(image_path, output_root, style)
    if location is None:
        return None
    root_key, subfolder, filename = location
    try:
        rows = (
            db.model.get_connection()
            .execute(_RUNS_FOR_OUTPUT, (style, root_key, subfolder, filename))
            .fetchall()
        )
    except sqlite3.Error:
        return None  # no history tables (a library without history)
    if len(rows) != 1:
        return None
    run_id, started, finished = rows[0][0], rows[0][1], rows[0][2]
    if not _written_during(image_path, started, finished):
        return None
    contexts = db.get_execution_contexts(run_id).get("contexts") or []
    named = {
        context.get("positive_prompt_id")
        for context in contexts
        if context.get("positive") is not None
    }
    if len(named) != 1:
        return None
    (prompt_id,) = named
    return prompt_id if isinstance(prompt_id, int) else None
