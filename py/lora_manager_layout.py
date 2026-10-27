"""Where LoraManager keeps its files (#355).

LoraManager (``custom_nodes/comfyui-lora-manager``) stores one
``<name>.metadata.json`` sidecar per model, a preview image or video beside
it, and each model's downloaded CivitAI example images. Where it stores them
is a user setting, and its settings file itself can live in three places.
This module repeats LoraManager's own rules, reading its files only (never
importing it), so the LoRA import, the trigger-word cache and the image
server share one definition:

- ``load_layout`` finds the settings file (environment folder, portable
  plugin file, or the user config folder, in LoraManager's order), keeps only
  the seven keys the import needs, and resolves the LoRA roots, the sidecar
  root (centralized storage), the example-images root and the active
  LoraManager library. Every folder from the settings passes the folder
  guard (exists, not a filesystem root, not the home folder itself).
- ``discover_sidecars`` finds every sidecar whose model file exists inside a
  LoRA root: beside the models in ``alongside`` mode, under the sidecar root
  (matched to its model by ``file_path``) in ``centralized`` mode.
- ``local_media`` lists a LoRA's preview and example images that the image
  server may serve.

Standard library plus ``platformdirs`` only (LoraManager depends on it), so
it is testable outside ComfyUI.
"""

import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import (
    AbstractSet,
    Callable,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

# py/utils/settings_paths.py: SETTINGS_DIR_ENV, _LM_PORTABLE_ENV, APP_NAME.
SETTINGS_DIR_ENV = "LORA_MANAGER_SETTINGS_DIR"
PORTABLE_ENV = "LORA_MANAGER_PORTABLE"
APP_NAME = "ComfyUI-LoRA-Manager"
# py/utils/constants.py PREVIEW_EXTENSIONS, in LoraManager's order.
PREVIEW_EXTENSIONS = (
    ".webp",
    ".preview.webp",
    ".preview.png",
    ".preview.jpeg",
    ".preview.jpg",
    ".preview.mp4",
    ".png",
    ".jpeg",
    ".jpg",
    ".mp4",
    ".gif",
    ".webm",
    ".avif",
    ".jxl",
)
# LoraManager's LoRA scanner accepts .safetensors only (py/services/lora_scanner.py).
MODEL_SUFFIX = ".safetensors"
# py/utils/sidecar_paths.py METADATA_SUFFIX.
METADATA_SUFFIX = ".metadata.json"
MAX_SETTINGS_BYTES = 5 * 1024 * 1024
# The only settings the import reads; every other key (the file can hold API
# keys) is dropped right after parsing.
SETTINGS_KEYS = frozenset(
    {
        "sidecar_storage_mode",
        "sidecar_storage_path",
        "example_images_path",
        "folder_paths",
        "extra_folder_paths",
        "libraries",
        "active_library",
    }
)
# py/utils/sidecar_paths.py STORAGE_MODE_ALONGSIDE / STORAGE_MODE_CENTRALIZED.
MODE_ALONGSIDE = "alongside"
MODE_CENTRALIZED = "centralized"
# utils/video_metadata.py VIDEO_EXTENSIONS (not imported: that package needs
# the node's relative imports).
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wmv"})

SOURCE_ENVIRONMENT = "environment"
SOURCE_PORTABLE = "portable"
SOURCE_USER_CONFIG = "user config"

NO_INSTALL = "no LoraManager install found"
UNREADABLE = "the settings file could not be read"
NOT_JSON = "the settings file is not valid JSON"
NOT_OBJECT = "the settings file is not a JSON object"
TOO_LARGE = "the settings file is larger than 5 MB"
NO_PLATFORMDIRS = "platformdirs is not available"
NO_SIDECAR_ROOT = "the sidecar folder does not exist"
BAD_SIDECAR_ROOT = "the sidecar folder is a filesystem root or the home folder"


@dataclass(frozen=True)
class LoraManagerLayout:
    """What LoraManager's settings say about where its files are."""

    settings_path: Optional[Path]
    source: Optional[str]
    error: Optional[str]
    mode: str
    lora_roots: Tuple[str, ...]
    sidecar_root: Optional[str]
    example_images_root: Optional[str]
    library: str
    media_roots: Tuple[str, ...]


@dataclass(frozen=True)
class Sidecar:
    """One LoRA's metadata file and the model file it describes."""

    metadata_path: Path
    model_path: Path


@dataclass(frozen=True)
class Discovery:
    """The sidecars kept, every metadata file seen, and those unread."""

    layout: LoraManagerLayout
    sidecars: List[Sidecar]
    # Every ``*.metadata.json`` seen, kept or skipped: resolved, once each,
    # sorted by ``str(path)``. The import's fingerprint covers all of them.
    scanned_files: Tuple[Path, ...]
    unreadable: List[str]

    @property
    def scanned(self) -> int:
        """How many metadata files were seen."""
        return len(self.scanned_files)


class _SettingsError(Exception):
    """The settings file cannot be used, for the reason it carries."""


# ── paths ────────────────────────────────────────────────────────────


def _canonical(path: str, pathmod=os.path) -> str:
    # py/config.py _canonical_path (not imported: py.config imports ComfyUI's
    # ``server`` at import time).
    return pathmod.normcase(pathmod.realpath(path))


def _is_filesystem_root(path: str, pathmod=os.path) -> bool:
    # py/config.py _is_filesystem_root: '/', 'C:\\' and UNC share roots.
    return pathmod.dirname(path) == path


def _guard_folder(
    path: str,
    home: str,
    *,
    pathmod=os.path,
    isdir: Optional[Callable[[str], bool]] = None,
) -> Optional[str]:
    """The canonical form of ``path`` when it may be read and served, else None.

    Kept only when it is a folder, not a filesystem root, and neither the
    home folder nor one of its ancestors (``/home``, ``C:\\Users``: a
    security review ruled that a folder holding the home folder must never
    be read or served).
    """
    isdir = isdir or os.path.isdir
    try:
        canonical = _canonical(path, pathmod)
        if not isdir(canonical) or _is_filesystem_root(canonical, pathmod):
            return None
        if _inside(home, (canonical,), pathmod):
            return None
    except (OSError, ValueError):
        return None
    return canonical


def _inside(path, roots: Sequence[str], pathmod=os.path) -> bool:
    """True when ``path`` (canonical) is one of ``roots`` or below one."""
    try:
        candidate = _canonical(str(path), pathmod)
    except (OSError, ValueError):
        return False
    for root in roots:
        prefix = root if root.endswith(pathmod.sep) else root + pathmod.sep
        if candidate == root or candidate.startswith(prefix):
            return True
    return False


def _absolute(value) -> Optional[str]:
    """A settings path, stripped, ``~``-expanded and absolute; None when unset."""
    if not isinstance(value, str) or not value.strip():
        return None
    return os.path.abspath(os.path.expanduser(value.strip()))


# ── the settings file ───────────────────────────────────────────────


def _platform_user_config_dir() -> str:
    from platformdirs import user_config_dir

    return user_config_dir(APP_NAME, appauthor=False)


def _read_bytes(path: Path) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(MAX_SETTINGS_BYTES + 1)


def _read_settings(path: Path) -> Optional[Dict]:
    """The parsed object in ``path``; None when it does not exist."""
    try:
        raw = _read_bytes(path)
    except FileNotFoundError:
        return None
    except OSError:
        raise _SettingsError(UNREADABLE) from None
    if len(raw) > MAX_SETTINGS_BYTES:
        raise _SettingsError(TOO_LARGE)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _SettingsError(NOT_JSON) from None
    if not isinstance(data, dict):
        raise _SettingsError(NOT_OBJECT)
    return data


def _settings_subset(data: Mapping) -> Dict:
    """Only the keys the import reads."""
    return {key: data[key] for key in SETTINGS_KEYS if key in data}


def _portable_flag(environ: Mapping[str, str], plugin_settings: Path) -> bool:
    """LoraManager's portable mode (settings_paths._should_use_portable_settings)."""
    forced = environ.get(PORTABLE_ENV)
    if forced == "1":
        return True
    if forced == "0":
        return False
    try:
        data = _read_settings(plugin_settings)
    except _SettingsError:
        return False
    return bool(data) and data.get("use_portable_settings") is True


def _settings_location(
    lora_manager_path: str,
    environ: Mapping[str, str],
    user_config_dir: Callable[[], str],
) -> Tuple[Path, str]:
    """``(settings file, source)`` in LoraManager's lookup order."""
    override = environ.get(SETTINGS_DIR_ENV)
    if override:
        folder = os.path.abspath(os.path.expanduser(override))
        return Path(folder) / "settings.json", SOURCE_ENVIRONMENT
    plugin_settings = Path(lora_manager_path) / "settings.json"
    if _portable_flag(environ, plugin_settings):
        return plugin_settings, SOURCE_PORTABLE
    try:
        folder = user_config_dir()
    except ImportError:
        raise _SettingsError(NO_PLATFORMDIRS) from None
    return Path(folder) / "settings.json", SOURCE_USER_CONFIG


# ── libraries and folders ───────────────────────────────────────────


def _sanitize_library_name(name: str) -> str:
    # py/utils/example_images_paths.py sanitize_library_name.
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name or "")
    return safe or "default"


def _active_library(settings: Mapping) -> Tuple[str, Optional[Dict], int]:
    """``(name, library dict or None, number of libraries)``."""
    libraries = settings.get("libraries")
    if not isinstance(libraries, dict) or not libraries:
        return "default", None, 0
    active = settings.get("active_library")
    if not isinstance(active, str) or active not in libraries:
        active = next(iter(libraries))
    library = libraries[active]
    return str(active), library if isinstance(library, dict) else None, len(libraries)


def _lora_list(settings: Mapping, library: Optional[Dict], key: str) -> List[str]:
    """The ``loras`` folders of ``key`` (the library's, else the top level)."""
    source = library if library is not None and key in library else settings
    section = source.get(key)
    if not isinstance(section, dict):
        return []
    value = section.get("loras")
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str) and entry.strip()]


def _guarded(paths, home: str) -> Tuple[str, ...]:
    kept = []
    for path in paths:
        absolute = _absolute(path)
        guarded = _guard_folder(absolute, home) if absolute else None
        if guarded and guarded not in kept:
            kept.append(guarded)
    return tuple(kept)


def _example_images_root(
    settings: Mapping, library: str, libraries: int, home: str
) -> Optional[str]:
    """py/utils/example_images_paths.py get_library_root, when the folder exists."""
    configured = _absolute(settings.get("example_images_path"))
    root = _guard_folder(configured, home) if configured else None
    if root is None or libraries <= 1:
        return root
    # Guarded (canonical) like its root: a library name keeps its case, and
    # the media check compares normcased paths.
    return _guard_folder(os.path.join(root, _sanitize_library_name(library)), home)


def _media_roots(*groups) -> Tuple[str, ...]:
    roots = []
    for group in groups:
        for root in group:
            if root and root not in roots:
                roots.append(root)
    return tuple(roots)


def load_layout(
    lora_manager_path: Optional[str],
    *,
    default_roots: Callable[[], List[str]],
    environ: Mapping[str, str] = os.environ,
    user_config_dir: Callable[[], str] = _platform_user_config_dir,
) -> LoraManagerLayout:
    """Read LoraManager's settings the way LoraManager does (read only).

    ``lora_manager_path`` is the resolved install (``None`` when none was
    found). An unusable layout carries ``error``, alongside mode and the
    default LoRA roots, so serving and trigger words keep working.
    """
    home = str(Path.home())
    defaults = _guarded(default_roots(), home)
    fallback = LoraManagerLayout(
        settings_path=None,
        source=None,
        error=NO_INSTALL,
        mode=MODE_ALONGSIDE,
        lora_roots=defaults,
        sidecar_root=None,
        example_images_root=None,
        library="default",
        media_roots=defaults,
    )
    if not lora_manager_path:
        return fallback
    try:
        settings_path, source = _settings_location(
            lora_manager_path, environ, user_config_dir
        )
    except _SettingsError as error:
        return replace(fallback, error=str(error))
    fallback = replace(fallback, settings_path=settings_path, source=source)
    try:
        raw = _read_settings(settings_path)
    except _SettingsError as error:
        return replace(fallback, error=str(error))
    settings = _settings_subset(raw or {})
    return _layout_from_settings(settings, settings_path, source, defaults, home)


def _layout_from_settings(settings, settings_path, source, defaults, home):
    library, entry, libraries = _active_library(settings)
    lora_roots = _media_roots(
        defaults,
        _guarded(_lora_list(settings, entry, "folder_paths"), home),
        _guarded(_lora_list(settings, entry, "extra_folder_paths"), home),
    )
    mode = settings.get("sidecar_storage_mode")
    mode = MODE_CENTRALIZED if mode == MODE_CENTRALIZED else MODE_ALONGSIDE
    example_root = _example_images_root(settings, library, libraries, home)
    layout = LoraManagerLayout(
        settings_path=settings_path,
        source=source,
        error=None,
        mode=mode,
        lora_roots=lora_roots,
        sidecar_root=None,
        example_images_root=example_root,
        library=library,
        media_roots=_media_roots(lora_roots, [example_root]),
    )
    if mode != MODE_CENTRALIZED:
        return layout
    configured = _absolute(settings.get("sidecar_storage_path"))
    configured = configured or os.path.join(str(settings_path.parent), "sidecars")
    sidecar_root = _guard_folder(configured, home)
    if sidecar_root is None:
        reason = NO_SIDECAR_ROOT if not os.path.isdir(configured) else BAD_SIDECAR_ROOT
        return replace(
            layout,
            error=reason,
            lora_roots=defaults,
            example_images_root=None,
            media_roots=defaults,
        )
    return replace(
        layout,
        sidecar_root=sidecar_root,
        media_roots=_media_roots(lora_roots, [sidecar_root], [example_root]),
    )


# ── sidecars ────────────────────────────────────────────────────────


def _metadata_files(folder: str) -> Iterator[Path]:
    """Every ``*.metadata.json`` under ``folder``; an unlistable folder raises.

    Symlinked folders are followed, each real folder once (no loops).
    """

    def fail(error):
        raise error

    visited = set()
    for current, dirs, files in os.walk(folder, onerror=fail, followlinks=True):
        real = _canonical(current)
        if real in visited:
            dirs[:] = []
            continue
        visited.add(real)
        dirs.sort()
        for name in sorted(files):
            if name.endswith(METADATA_SUFFIX):
                yield Path(current) / name


def _read_sidecar(path: Path) -> Optional[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _model_for(path: Path, layout: LoraManagerLayout, unreadable: List[str]):
    """The model file a sidecar describes, when it exists inside a LoRA root."""
    if layout.mode == MODE_ALONGSIDE:
        base = path.name[: -len(METADATA_SUFFIX)]
        model = path.parent / f"{base}{MODEL_SUFFIX}"
        return model if model.is_file() else None
    metadata = _read_sidecar(path)
    if metadata is None:
        unreadable.append(path.name)
        return None
    file_path = metadata.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        return None
    model = Path(file_path)
    if not model.is_file() or not _inside(model, layout.lora_roots):
        return None
    return model


def discover_sidecars(layout: LoraManagerLayout) -> Discovery:
    """Every sidecar whose model exists inside a LoRA root, sorted, once each.

    Raises ``OSError`` when a folder cannot be listed: a discovery that
    cannot see every folder must not replace the collection with part of it.
    """
    if layout.error is not None:
        return Discovery(layout, [], (), [])
    if layout.mode == MODE_CENTRALIZED:
        folders = [layout.sidecar_root]
    else:
        folders = list(layout.lora_roots)
    seen = set()
    scanned: List[Path] = []
    unreadable: List[str] = []
    sidecars = []
    for folder in folders:
        for path in _metadata_files(folder):
            resolved = Path(os.path.realpath(path))
            key = os.path.normcase(str(resolved))
            if key in seen:
                continue
            seen.add(key)
            scanned.append(resolved)
            model = _model_for(resolved, layout, unreadable)
            if model is not None:
                sidecars.append(Sidecar(resolved, Path(os.path.realpath(model))))
    sidecars.sort(key=lambda sidecar: str(sidecar.metadata_path))
    scanned.sort(key=str)
    return Discovery(layout, sidecars, tuple(scanned), unreadable)


# ── images ──────────────────────────────────────────────────────────


def _natural_key(name: str):
    parts = re.split(r"(\d+)", name)
    return [int(part) if part.isdigit() else part.lower() for part in parts], name


def _example_files(folder: str) -> List[str]:
    try:
        with os.scandir(folder) as entries:
            names = [entry.name for entry in entries if entry.is_file()]
    except OSError:
        return []
    return [os.path.join(folder, name) for name in sorted(names, key=_natural_key)]


def _preview_candidates(sidecar: Sidecar, metadata: Dict) -> Iterator[str]:
    """The paths a preview is looked for at, in LoraManager's order."""
    url = metadata.get("preview_url")
    if isinstance(url, str) and url.strip() and os.path.isabs(url.strip()):
        yield url.strip()
    base = sidecar.metadata_path.name[: -len(METADATA_SUFFIX)]
    for suffix in PREVIEW_EXTENSIONS:
        yield str(sidecar.metadata_path.parent / f"{base}{suffix}")


def local_media(
    sidecar: Sidecar,
    metadata: Dict,
    layout: LoraManagerLayout,
    media_extensions: AbstractSet[str],
) -> List[str]:
    """The LoRA's preview, then its example images: absolute, resolved paths.

    A file counts only when its extension is in ``media_extensions`` (what the
    image server serves) and it lies inside ``layout.media_roots``.
    """

    def usable(path: str) -> bool:
        return (
            os.path.splitext(path)[1].lower() in media_extensions
            and os.path.isfile(path)
            and _inside(path, layout.media_roots)
        )

    found: List[str] = []
    for path in _preview_candidates(sidecar, metadata):
        if usable(path):
            found.append(path)
            break
    sha256 = metadata.get("sha256")
    if layout.example_images_root and isinstance(sha256, str) and sha256.strip():
        folder = os.path.join(layout.example_images_root, sha256.strip().lower())
        found.extend(path for path in _example_files(folder) if usable(path))
    media: List[str] = []
    for path in found:
        resolved = os.path.realpath(path)
        if resolved not in media:
            media.append(resolved)
    return _cover_first(media)


def _cover_first(media: List[str]) -> List[str]:
    """A still image in front of a leading video (no thumbnails for these yet)."""
    if not media or os.path.splitext(media[0])[1].lower() not in VIDEO_SUFFIXES:
        return media
    for index, path in enumerate(media):
        if os.path.splitext(path)[1].lower() not in VIDEO_SUFFIXES:
            return [path] + media[:index] + media[index + 1 :]
    return media


# ── the review ──────────────────────────────────────────────────────


def layout_summary(layout: LoraManagerLayout, home: Path) -> Dict:
    """What the review shows about the layout (no settings values)."""
    settings_file = None
    if layout.settings_path is not None:
        settings_file = str(layout.settings_path)
        try:
            relative = Path(settings_file).relative_to(home)
            settings_file = os.path.join("~", str(relative))
        except ValueError:
            pass
    return {
        "source": layout.source,
        "settings_file": settings_file,
        "error": layout.error,
        "mode": layout.mode,
        "library": layout.library,
        "example_images": layout.example_images_root is not None,
    }
