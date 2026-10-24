"""Where the library lives (#249 S6b): the one resolver for every data path.

The library is ``prompts.db``, ``config.json`` (it can hold the CivitAI key),
the safety backups beside the database, the logs and the LoRA image cache.
Inside the node folder an uninstall, a reinstall or a folder-replacing update
deletes it, so new installs keep it in ComfyUI's system user folder
``user/__promptmanager`` (``folder_paths.get_system_user_directory``), which
ComfyUI's ``/userdata`` HTTP API refuses to serve.

Resolution order (``resolve_library``):

1. ``PROMPT_MANAGER_DATA_DIR``: an absolute folder outside every folder
   ComfyUI or Prompt Manager serves. It is used when it holds a library or
   when no node-folder library exists; an empty one never replaces a
   node-folder library (that one stays in use, and Move library targets the
   environment folder). An invalid value is ignored and reported.
2. An explicit ``database.default_path`` in config.json, honoured as before
   (a relative path is relative to the node folder). ``PROMPT_MANAGER_CONFIG_PATH``
   still names config.json itself.
3. The default data folder once a move finished there (its marker says
   ``moved``), even before the old file was renamed.
4. A node-folder ``prompts.db`` that was not moved: it stays in use until the
   user moves it.
5. The default data folder (new installs). Outside ComfyUI (no
   ``folder_paths``) the node folder, as before.

Nothing here moves, copies or creates data: it only reads the file system.
After a live move the process uses ``switch_to``'s location until restart.
This module imports only the standard library, so the logger can use it.
"""

import json
import os
import threading
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional, Tuple

DATA_DIR_ENV = "PROMPT_MANAGER_DATA_DIR"
CONFIG_PATH_ENV = "PROMPT_MANAGER_CONFIG_PATH"
EXTRA_GALLERY_ROOTS_ENV = "PROMPT_MANAGER_EXTRA_GALLERY_ROOTS"
SYSTEM_USER_NAME = "promptmanager"
SYSTEM_USER_PREFIX = "__"
DATABASE_NAME = "prompts.db"
CONFIG_NAME = "config.json"
MARKER_NAME = "library-location.json"
# config.json has always written its default; that value is not a choice.
DEFAULT_DB_SETTING = "prompts.db"

SOURCE_ENVIRONMENT = "environment"
SOURCE_CONFIG = "config"
SOURCE_NODE_FOLDER = "node_folder"
SOURCE_DEFAULT = "default"
SOURCES = (SOURCE_ENVIRONMENT, SOURCE_CONFIG, SOURCE_NODE_FOLDER, SOURCE_DEFAULT)

MARKER_PENDING = "pending"
MARKER_MOVED = "moved"

EXTENSION_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_UNSET = object()
_override_lock = threading.Lock()
_override: Optional["LibraryLocation"] = None


@dataclass(frozen=True)
class LibraryLocation:
    """Where the library is and why.

    ``target_dir`` is where Move library would put it (None outside ComfyUI)
    and ``target_source`` why (the environment folder or the default one);
    ``problem`` is a code for something the user should know (an invalid or
    empty ``PROMPT_MANAGER_DATA_DIR``, a move that did not finish).
    ``config_explicit``: config.json is named by ``PROMPT_MANAGER_CONFIG_PATH``
    (it is the user's and never moves).
    """

    source: str
    db_path: str
    config_path: str
    data_dir: str
    target_dir: Optional[str] = None
    problem: Optional[str] = None
    target_source: Optional[str] = None
    config_explicit: bool = False

    @property
    def movable(self) -> bool:
        return self.source == SOURCE_NODE_FOLDER and self.target_dir is not None


def import_folder_paths():
    """ComfyUI's ``folder_paths`` module, or None outside ComfyUI."""
    try:
        import folder_paths
    except ImportError:
        return None
    return folder_paths


def _folder_paths(value):
    return import_folder_paths() if value is _UNSET else value


def _call(module, name, *args) -> Optional[str]:
    getter = getattr(module, name, None)
    if not callable(getter):
        return None
    try:
        value = getter(*args)
    except Exception:  # an older or broken host: treat the folder as unknown
        return None
    return str(value) if value else None


def default_data_dir(folder_paths=_UNSET) -> Optional[str]:
    """``user/__promptmanager``, or None outside ComfyUI.

    ComfyUI builds without ``get_system_user_directory`` get the same folder
    joined by hand.
    """
    module = _folder_paths(folder_paths)
    if module is None:
        return None
    folder = _call(module, "get_system_user_directory", SYSTEM_USER_NAME)
    if folder:
        return os.path.abspath(folder)
    user = _call(module, "get_user_directory")
    if not user:
        return None
    return os.path.abspath(os.path.join(user, SYSTEM_USER_PREFIX + SYSTEM_USER_NAME))


def _identity(path: str) -> Optional[Tuple[int, int]]:
    try:
        stat = os.stat(path)
    except (OSError, ValueError):
        return None
    return (stat.st_dev, stat.st_ino)


def same_folder(first: str, second: str) -> bool:
    """Whether two spellings name one existing folder (links and case folded)."""
    one, two = _identity(first), _identity(second)
    if one is None or two is None:
        return False
    if one[1] == 0:  # no file id on this volume: compare the spelling
        return _spelling(first) == _spelling(second)
    return one == two


def _spelling(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _existing_ancestors(path: str) -> List[str]:
    """``path`` (resolved) and its parents that exist, nearest first."""
    current = os.path.realpath(path)
    found = []
    while True:
        if os.path.isdir(current):
            found.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            return found
        current = parent


def _inside_any(path: str, folders: Iterable[str]) -> bool:
    """Whether ``path`` is one of ``folders`` or below one.

    Compared by spelling (realpath + normcase) and by file identity of every
    existing ancestor, so symlinks and case-insensitive volumes (Windows,
    macOS) cannot hide a served folder.
    """
    folders = [folder for folder in folders if folder]
    if not folders:
        return False
    spelling = _spelling(path)
    sep = os.path.sep
    for folder in folders:
        root = _spelling(folder)
        if spelling == root or spelling.startswith(root.rstrip(sep) + sep):
            return True
    identities = {_identity(folder) for folder in folders}
    identities.discard(None)
    identities = {item for item in identities if item[1] != 0}
    return any(_identity(a) in identities for a in _existing_ancestors(path))


def _served_folders(module, environ, gallery_roots) -> List[str]:
    """Folders whose files ComfyUI or Prompt Manager hand out over HTTP."""
    folders = []
    if module is not None:
        for name in (
            "get_output_directory",
            "get_input_directory",
            "get_temp_directory",
        ):
            folder = _call(module, name)
            if folder:
                folders.append(folder)
    extra = (environ or {}).get(EXTRA_GALLERY_ROOTS_ENV, "")
    folders.extend(part for part in extra.split(os.pathsep) if part.strip())
    folders.extend(str(root) for root in gallery_roots or () if root)
    return folders


def _in_public_user_folder(path: str, module) -> bool:
    """Whether ``path`` lies in a ``/userdata``-served user folder (not ``__``)."""
    user = _call(module, "get_user_directory") if module is not None else None
    if not user:
        return False
    if not _inside_any(path, [user]) or same_folder(path, user):
        return False
    real_user = _spelling(user)
    relative = os.path.relpath(_spelling(path), real_user)
    first = relative.split(os.sep, 1)[0]
    if first.startswith(os.pardir):
        # Reached through a link or another spelling: judge by identity.
        for ancestor in _existing_ancestors(path):
            if same_folder(os.path.dirname(ancestor), user):
                first = os.path.basename(ancestor)
                break
        else:
            return False
    return not first.startswith(SYSTEM_USER_PREFIX)


def data_dir_problem(
    path, *, folder_paths=_UNSET, environ=None, gallery_roots=()
) -> Optional[str]:
    """Why ``path`` cannot hold the library, as a code, or None.

    ``data-dir-not-absolute``: empty or relative. ``data-dir-served``: inside
    ComfyUI's output, input or temp folder, a public user folder, an extra
    gallery root or a gallery source (their files are served over HTTP).
    """
    if not isinstance(path, str) or not path.strip() or not os.path.isabs(path):
        return "data-dir-not-absolute"
    module = _folder_paths(folder_paths)
    if _inside_any(path, _served_folders(module, environ, gallery_roots)):
        return "data-dir-served"
    if _in_public_user_folder(path, module):
        return "data-dir-served"
    return None


def read_marker(directory: Optional[str]) -> dict:
    """The move marker in ``directory``, or ``{}`` when absent or unreadable."""
    if not directory:
        return {}
    try:
        with open(os.path.join(directory, MARKER_NAME), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("state") not in (
        MARKER_PENDING,
        MARKER_MOVED,
    ):
        return {}
    return data


def _marker_state(directory) -> Optional[str]:
    return read_marker(directory).get("state")


def switch_to(location: LibraryLocation) -> None:
    """Use ``location`` for the rest of this process (after a live move)."""
    global _override
    with _override_lock:
        _override = location


def clear_override() -> None:
    """Forget a live move's location (tests)."""
    global _override
    with _override_lock:
        _override = None


def current_override() -> Optional[LibraryLocation]:
    with _override_lock:
        return _override


@dataclass(frozen=True)
class _Decision:
    data_dir: str
    source: str
    target_dir: Optional[str]
    problem: Optional[str]
    db_dir: Optional[str] = None  # when the database is not in data_dir
    target_source: Optional[str] = None


def _configured_sources(module, environ, extension_root, default, raw_env):
    """The image sources every config.json this resolver might read lists.

    ``config_location`` runs before any config.json is loaded, so it cannot be
    handed the configured sources; both it and ``resolve_library`` read them
    here, from each candidate file (the explicit one, the environment
    folder's, the node folder's and the default folder's), so the two always
    judge ``PROMPT_MANAGER_DATA_DIR`` alike. Relative sources are taken from
    ComfyUI's base folder, as the gallery resolves them.
    """
    candidates = [environ.get(CONFIG_PATH_ENV, "").strip()]
    if raw_env and os.path.isabs(raw_env):
        candidates.append(os.path.join(raw_env, CONFIG_NAME))
    candidates.append(os.path.join(extension_root, CONFIG_NAME))
    if default:
        candidates.append(os.path.join(default, CONFIG_NAME))
    base = getattr(module, "base_path", None) if module is not None else None
    sources = []
    for candidate in candidates:
        if not candidate or not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            directories = data["gallery"]["monitoring"]["directories"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not isinstance(directories, list):
            continue
        for directory in directories:
            if not isinstance(directory, str) or not directory.strip():
                continue
            directory = directory.strip()
            if not os.path.isabs(directory):
                if not base:
                    continue
                directory = os.path.join(str(base), directory)
            sources.append(directory)
    return sources


def _decide(environ, extension_root, folder_paths, gallery_roots) -> _Decision:
    """Which folder holds the library (before an explicit database path)."""
    module = _folder_paths(folder_paths)
    environ = os.environ if environ is None else environ
    legacy_db = os.path.isfile(os.path.join(extension_root, DATABASE_NAME))
    legacy = legacy_db or os.path.isfile(os.path.join(extension_root, CONFIG_NAME))
    default = default_data_dir(module)
    problem = None
    env_dir = None
    raw = environ.get(DATA_DIR_ENV, "")
    if raw.strip():
        sources = list(gallery_roots or ()) + _configured_sources(
            module, environ, extension_root, default, raw.strip()
        )
        problem = data_dir_problem(
            raw.strip(),
            folder_paths=module,
            environ=environ,
            gallery_roots=sources,
        )
        if problem is None:
            env_dir = os.path.abspath(raw.strip())
    target = env_dir or default
    target_source = SOURCE_ENVIRONMENT if env_dir else SOURCE_DEFAULT

    def decision(data_dir, source, problem=None, db_dir=None):
        return _Decision(
            data_dir, source, target, problem, db_dir, target_source if target else None
        )

    if env_dir is not None:
        state = _marker_state(env_dir)
        holds = os.path.isfile(os.path.join(env_dir, DATABASE_NAME))
        if state != MARKER_PENDING and (holds or not legacy_db):
            return decision(env_dir, SOURCE_ENVIRONMENT)
        problem = "move-unfinished" if state == MARKER_PENDING else "data-dir-empty"
        return decision(extension_root, SOURCE_NODE_FOLDER, problem)
    if default is None:
        return decision(extension_root, SOURCE_NODE_FOLDER, problem)
    if _marker_state(default) == MARKER_MOVED and os.path.isfile(
        os.path.join(default, DATABASE_NAME)
    ):
        return decision(default, SOURCE_DEFAULT, problem)
    if legacy:
        if _marker_state(default) == MARKER_PENDING:
            problem = problem or "move-unfinished"
        if legacy_db:
            return decision(extension_root, SOURCE_NODE_FOLDER, problem)
        # Only a node-folder config.json: keep reading it, but a new database
        # never goes into the node folder.
        return decision(extension_root, SOURCE_DEFAULT, problem, default)
    return decision(default, SOURCE_DEFAULT, problem)


def config_location(
    environ=None, extension_root=None, folder_paths=_UNSET, gallery_roots=()
) -> Tuple[str, str]:
    """``(config.json path, data folder)``; needs no config value (no cycle)."""
    override = current_override()
    if override is not None:
        return override.config_path, override.data_dir
    environ = os.environ if environ is None else environ
    root = extension_root or EXTENSION_ROOT
    decision = _decide(environ, root, folder_paths, gallery_roots)
    explicit = environ.get(CONFIG_PATH_ENV, "").strip()
    config_path = explicit or os.path.join(decision.data_dir, CONFIG_NAME)
    return config_path, decision.data_dir


def is_explicit_db_setting(value) -> bool:
    """Whether config.json's ``database.default_path`` names a chosen file."""
    return isinstance(value, str) and value.strip() not in ("", DEFAULT_DB_SETTING)


def resolve_library(
    default_path=None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    extension_root: Optional[str] = None,
    folder_paths=_UNSET,
    gallery_roots=(),
) -> LibraryLocation:
    """The library's location now (the module docstring has the order)."""
    override = current_override()
    if override is not None:
        return override
    environ = os.environ if environ is None else environ
    root = extension_root or EXTENSION_ROOT
    decision = _decide(environ, root, folder_paths, gallery_roots)
    explicit_config = environ.get(CONFIG_PATH_ENV, "").strip()
    config_path = explicit_config or os.path.join(decision.data_dir, CONFIG_NAME)
    common = {
        "target_source": decision.target_source,
        "config_explicit": bool(explicit_config),
    }
    if decision.source != SOURCE_ENVIRONMENT and is_explicit_db_setting(default_path):
        db_path = default_path.strip()
        if not os.path.isabs(db_path):
            db_path = os.path.normpath(os.path.join(root, db_path))
        return LibraryLocation(
            SOURCE_CONFIG,
            db_path,
            config_path,
            decision.data_dir,
            decision.target_dir,
            decision.problem,
            **common,
        )
    return LibraryLocation(
        decision.source,
        os.path.join(decision.db_dir or decision.data_dir, DATABASE_NAME),
        config_path,
        decision.data_dir,
        decision.target_dir,
        decision.problem,
        **common,
    )


def ensure_private_dir(path: str) -> str:
    """Create ``path`` (and parents) if missing; a new folder is owner-only.

    An existing folder is left as it is (its permissions are the user's).
    """
    if not os.path.isdir(path):
        os.makedirs(path, mode=0o700, exist_ok=True)
        if os.name == "posix":
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass
    return path


def logs_dir(**kwargs) -> str:
    """The folder for Prompt Manager's log files."""
    return os.path.join(config_location(**kwargs)[1], "logs")


def cache_dir(name: str, **kwargs) -> str:
    """A re-creatable cache folder, ``<data folder>/data/<name>``."""
    return os.path.join(config_location(**kwargs)[1], "data", name)
