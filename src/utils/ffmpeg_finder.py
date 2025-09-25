"""Utilities for locating FFmpeg executables across platforms."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_TIMEOUT = 3.0

COMMON_PATHS: List[Optional[str]] = [
    os.environ.get("FFMPEG_PATH", None),
    shutil.which("ffmpeg"),
    "/usr/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/opt/homebrew/bin/ffmpeg",
    "C:/Program Files/ffmpeg/bin/ffmpeg.exe",
    "C:/ffmpeg/bin/ffmpeg.exe",
]


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def _iter_path_candidates(exe_name: str = "ffmpeg") -> Iterable[Path]:
    """Yield executables named *exe_name* found on PATH."""

    env_path = os.environ.get("PATH", "")
    if not env_path:
        return

    if os.name == "nt":
        pathext_raw = os.environ.get("PATHEXT", ".EXE;.BAT;.CMD")
        pathexts: Sequence[str] = tuple(
            ext.strip().lower() for ext in pathext_raw.split(";") if ext
        )
        if ".exe" not in pathexts:
            pathexts = (*pathexts, ".exe")
    else:
        pathexts = ("",)

    for directory in env_path.split(os.pathsep):
        if not directory:
            continue
        base = Path(directory).expanduser()
        for ext in pathexts:
            candidate = (base / f"{exe_name}{ext}").resolve()
            if _is_executable(candidate):
                yield candidate


def _describe(path: Path, timeout: float = DEFAULT_TIMEOUT) -> Tuple[str, Optional[str]]:
    """Return (summary_line, full_version_output or None)."""

    try:
        completed = subprocess.run(  # noqa: S603
            [str(path), "-version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
        first_line = (
            completed.stdout.splitlines()[0] if completed.stdout else "no output"
        )
        return f"{path} -> {first_line}", completed.stdout
    except Exception as exc:  # pragma: no cover - subprocess failure details
        return f"{path} -> unreachable ({exc})", None


def _sibling_ffprobe(ffmpeg_path: Path) -> Optional[Path]:
    """Return ffprobe executable located beside *ffmpeg_path* (if any)."""

    probe_name = "ffprobe.exe" if ffmpeg_path.suffix.lower() == ".exe" else "ffprobe"
    if os.name == "nt":
        probe_name = "ffprobe.exe"
    candidate = ffmpeg_path.parent / probe_name
    return candidate if _is_executable(candidate) else None


def _collect_candidates() -> List[Path]:
    seen: set[Path] = set()
    hits: List[Path] = []

    def _add(path: Path) -> None:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if resolved not in seen and _is_executable(resolved):
            seen.add(resolved)
            hits.append(resolved)

    for raw in filter(None, COMMON_PATHS):
        _add(Path(raw).expanduser())

    for path in _iter_path_candidates("ffmpeg"):
        _add(path)

    hits.sort(key=lambda p: (len(str(p)), str(p).lower()))
    return hits


def _candidate_dict(
    path: Path,
    *,
    include_ffprobe: bool,
    timeout: float,
) -> Dict[str, object]:
    summary, version_output = _describe(path, timeout)
    version_line = summary.split("->", 1)[1].strip() if "->" in summary else summary
    candidate: Dict[str, object] = {
        "path": str(path),
        "reachable": version_output is not None,
        "summary": summary,
        "version": version_line,
    }

    if include_ffprobe:
        probe_path = _sibling_ffprobe(path)
        if probe_path:
            probe_summary, probe_version_output = _describe(probe_path, timeout)
            candidate["ffprobe"] = {
                "path": str(probe_path),
                "reachable": probe_version_output is not None,
                "summary": probe_summary,
            }
        else:
            candidate["ffprobe"] = None

    return candidate


def find_ffmpeg_candidates(
    *,
    timeout: float = DEFAULT_TIMEOUT,
    include_ffprobe: bool = False,
) -> List[Dict[str, object]]:
    """Return metadata for all ffmpeg executables discovered on the host."""

    hits = _collect_candidates()
    return [
        _candidate_dict(path, include_ffprobe=include_ffprobe, timeout=timeout)
        for path in hits
    ]


def verify_ffmpeg_path(
    path: str | os.PathLike[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    include_ffprobe: bool = False,
) -> Dict[str, object]:
    """Return metadata for the provided ffmpeg *path*."""

    candidate_path = Path(path).expanduser()
    candidate = _candidate_dict(
        candidate_path, include_ffprobe=include_ffprobe, timeout=timeout
    )

    if not candidate["reachable"]:
        candidate["error"] = "ffmpeg executable not reachable"

    return candidate


__all__ = [
    "find_ffmpeg_candidates",
    "verify_ffmpeg_path",
    "DEFAULT_TIMEOUT",
]
