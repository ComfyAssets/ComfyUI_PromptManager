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

Lookups are batched (``stored_run_prompts``): one parameterized query per
chunk of at most ``LOOKUP_CHUNK`` distinct file names, matched in memory. The
history schema is left exactly as it is (no extra index), so older builds can
still open the library.
"""

import logging
import os
import sqlite3
import threading
import weakref
from typing import Any, Callable, Dict, Iterable, List, Optional

try:
    from ..utils.execution_outputs import snapshot_output_roots
    from ..utils.library_location import import_folder_paths
except ImportError:
    from utils.execution_outputs import snapshot_output_roots
    from utils.library_location import import_folder_paths

# File times may trail or lead the recorded run times a little (coarse file
# system clocks, the write finishing just after the host reported it).
MTIME_SLACK_MS = 2000

# Distinct file names per lookup query: well under SQLite's bound-parameter
# limit (999 on old builds), so a scan batch costs one query per chunk.
LOOKUP_CHUNK = 500

_RUNS_FOR_NAMES = (
    "SELECT DISTINCT o.filename, o.subfolder, o.root_key, o.path_style, o.run_id, "
    "r.observed_dequeue_at, COALESCE(r.terminal_event_at, r.observed_callback_at) "
    "FROM execution_run_outputs o JOIN execution_runs r ON r.run_id = o.run_id "
    "WHERE o.folder_type = 'output' AND o.filename IN ({marks})"
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
    if not image_path:
        return None
    return stored_run_prompts(db, [image_path], output_root, path_style).get(image_path)


def _runs_by_location(db: Any, names: List[str]) -> Optional[Dict[tuple, dict]]:
    """``{(filename, subfolder, root_key, style): {run_id: (start, end)}}``.

    One query per ``LOOKUP_CHUNK`` names; None without history tables.
    """
    found: Dict[tuple, dict] = {}
    conn = db.model.get_connection()
    for start in range(0, len(names), LOOKUP_CHUNK):
        chunk = names[start : start + LOOKUP_CHUNK]
        query = _RUNS_FOR_NAMES.format(marks=",".join("?" for _ in chunk))
        try:
            rows = conn.execute(query, chunk).fetchall()  # nosec B608
        except sqlite3.Error:
            return None  # no history tables (a library without history)
        for name, subfolder, root_key, style, run_id, began, ended in rows:
            runs = found.setdefault((name, subfolder, root_key, style), {})
            runs[run_id] = (began, ended)
    return found


def _named_prompt(db: Any, run_id: str) -> Optional[int]:
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


def stored_run_prompts(
    db: Any,
    image_paths: Iterable[str],
    output_root: Optional[str],
    path_style: Optional[str] = None,
) -> Dict[str, Optional[int]]:
    """``{path: stored prompt id or None}`` for many files in few queries.

    The output lookup is one query per ``LOOKUP_CHUNK`` distinct names; each
    run that alone reported a file is then read once for its contexts.
    """
    paths = list(dict.fromkeys(path for path in image_paths if path))
    result: Dict[str, Optional[int]] = dict.fromkeys(paths)
    if not paths or not output_root:
        return result
    style = path_style or ("windows" if os.name == "nt" else "posix")
    located = {}
    for path in paths:
        location = _location(path, output_root, style)
        if location is not None:
            located[path] = location
    if not located:
        return result
    names = sorted({filename for _key, _sub, filename in located.values()})
    runs = _runs_by_location(db, names)
    if not runs:
        return result
    prompts: Dict[str, Optional[int]] = {}
    for path, (root_key, subfolder, filename) in located.items():
        reported = runs.get((filename, subfolder, root_key, style), {})
        if len(reported) != 1:
            continue
        ((run_id, (began, ended)),) = reported.items()
        if not _written_during(path, began, ended):
            continue
        if run_id not in prompts:
            prompts[run_id] = _named_prompt(db, run_id)
        result[path] = prompts[run_id]
    return result


_LOGGER = logging.getLogger("prompt_manager.run_attribution")
_SEALED_LISTENERS: List[weakref.WeakMethod] = []
_SEALED_LOCK = threading.Lock()


def subscribe_outputs_sealed(method: Callable[[str, List[dict]], None]) -> None:
    """Call bound ``method(run_id, references)`` after a run's outputs are recorded.

    Held weakly: a listener that is garbage collected is dropped.
    """
    with _SEALED_LOCK:
        _SEALED_LISTENERS.append(weakref.WeakMethod(method))


def notify_outputs_sealed(run_id: str, references: Iterable[dict]) -> None:
    """Tell listeners a run's output references were just committed.

    A listener failure is logged and never reaches the history writer.
    """
    references = list(references)
    with _SEALED_LOCK:
        alive = [ref for ref in _SEALED_LISTENERS if ref() is not None]
        _SEALED_LISTENERS[:] = alive
        listeners = [ref() for ref in alive]
    for listener in listeners:
        if listener is None:
            continue
        try:
            listener(run_id, references)
        except Exception:
            _LOGGER.warning("Output seal listener failed", exc_info=True)
