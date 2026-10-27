"""Configuration module for ComfyUI PromptManager.

This module provides centralized configuration management for the PromptManager
extension, including gallery monitoring settings, database configuration,
web interface options, and performance tuning parameters.

The configuration is organized into two main classes:
- GalleryConfig: Settings for image monitoring and gallery functionality
- PromptManagerConfig: General settings for the PromptManager core features

Configuration can be loaded from and saved to JSON files for persistence.

Example:
    from config import PromptManagerConfig
    config = PromptManagerConfig.get_config()
    PromptManagerConfig.load_from_file('custom_config.json')
"""

# PromptManager/py/config.py

# Extension configuration
extension_name = "PromptManager"

# Get server instance and routes (same pattern as ComfyUI_Assets)
from server import PromptServer  # noqa: E402

server_instance = PromptServer.instance
routes = server_instance.routes

# Extension info
extension_uri = None  # Will be set in __init__.py

import math  # noqa: E402
import os  # noqa: E402
from typing import Any, Dict, List, Optional, Tuple  # noqa: E402

# Import logging system
try:
    from ..utils.logging_config import get_logger
    from ..utils import library_location
except ImportError:
    import sys

    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, current_dir)
    from utils.logging_config import get_logger
    from utils import library_location

# Initialize logger for config operations
config_logger = get_logger("prompt_manager.config")

# Environment variable listing extra directories (os.pathsep-separated) that
# may be used as gallery roots in addition to ComfyUI's own directories.
EXTRA_GALLERY_ROOTS_ENV = "PROMPT_MANAGER_EXTRA_GALLERY_ROOTS"

# The one answer for every path outside the allowed folders. The filesystem
# root and the home directory answer it too, so no caller learns the server's
# layout (home path, user name) from a refusal (#249 S4).
OUTSIDE_ALLOWED_REASON = (
    "Gallery root must be inside a ComfyUI directory (output, input, temp, user) "
    f"or one listed in {EXTRA_GALLERY_ROOTS_ENV}"
)

# Environment variable overriding where config.json is read from and saved to.
CONFIG_PATH_ENV = "PROMPT_MANAGER_CONFIG_PATH"

# ComfyUI folder_paths getters whose directories are valid gallery parents.
_COMFYUI_DIRECTORY_GETTERS = (
    "get_output_directory",
    "get_input_directory",
    "get_temp_directory",
    "get_user_directory",
)


def _canonical_path(path: str) -> str:
    """Return a path normalised for comparison (realpath + normcase)."""
    return os.path.normcase(os.path.realpath(path))


def write_private_json(path: str, data: Any) -> None:
    """Write ``data`` as JSON to ``path`` readable by the current user only.

    config.json can hold an API key (integrations.lora_manager), so it is
    created with mode 0600 and, on POSIX, an existing more open file is
    tightened too. Windows has no POSIX modes; there the chmod is a best
    effort that only affects the read-only bit. Parent directories are
    created as needed. Raises OSError when the file cannot be written.
    """
    import json

    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    if os.name == "posix":
        os.chmod(path, 0o600)


def _lexical_path(path: str) -> str:
    """``path`` normalised for comparison without touching the filesystem.

    normpath collapses ``.`` and ``..`` as text and normcase folds case on
    Windows; neither opens, stats nor resolves anything (no SMB request for a
    UNC path). A symlink is only seen by :func:`_canonical_path` later.
    """
    return os.path.normcase(os.path.normpath(path))


def _lexically_inside(path: str, parents: List[str]) -> bool:
    """Whether the normalised ``path`` is one of ``parents`` or below one.

    Pure text on normalised spellings (:func:`_lexical_path` or
    :func:`_canonical_path`): no filesystem access.
    """
    sep = os.path.sep
    return any(
        path == parent or path.startswith(parent.rstrip(sep) + sep)
        for parent in parents
    )


def _is_filesystem_root(path: str) -> bool:
    """True for '/' on POSIX and drive roots such as 'C:\\' on Windows."""
    return os.path.dirname(path) == path


def _import_folder_paths():
    """ComfyUI's folder_paths module, or None outside ComfyUI."""
    try:
        import folder_paths
    except ImportError:
        return None
    return folder_paths


def _comfyui_base_directory() -> Optional[str]:
    """ComfyUI's base directory: folder_paths.base_path, else the output dir's
    parent."""
    folder_paths = _import_folder_paths()
    if folder_paths is None:
        return None
    base = getattr(folder_paths, "base_path", None)
    if isinstance(base, str) and base:
        return base
    getter = getattr(folder_paths, "get_output_directory", None)
    if getter is None:
        return None
    try:
        output_dir = getter()
    except Exception:
        return None
    if isinstance(output_dir, str) and output_dir:
        return os.path.dirname(os.path.realpath(output_dir))
    return None


def _comfyui_directories() -> List[str]:
    """Directories reported by ComfyUI's folder_paths module, if importable."""
    folder_paths = _import_folder_paths()
    if folder_paths is None:
        return []

    found = []
    for getter_name in _COMFYUI_DIRECTORY_GETTERS:
        getter = getattr(folder_paths, getter_name, None)
        if getter is None:
            continue
        try:
            directory = getter()
        except Exception:
            continue
        if isinstance(directory, str) and directory:
            found.append(directory)
    return found


def _extra_gallery_roots() -> List[str]:
    """Directories the user opted in through PROMPT_MANAGER_EXTRA_GALLERY_ROOTS."""
    raw = os.environ.get(EXTRA_GALLERY_ROOTS_ENV, "")
    return [entry.strip() for entry in raw.split(os.pathsep) if entry.strip()]


class GalleryConfig:
    """Configuration class for the gallery monitoring and image processing system.

    This class manages all settings related to automatic image monitoring,
    prompt tracking, database cleanup, web interface display, and performance
    optimization for the gallery functionality.

    All configuration values are class attributes that can be modified at runtime
    or loaded from external configuration files.

    Attributes:
        MONITORING_ENABLED (bool): Enable/disable automatic image monitoring
        MONITORING_DIRECTORIES (List[str]): Directories to monitor for new images
        SUPPORTED_EXTENSIONS (List[str]): Image file extensions to process
        PROCESSING_DELAY (float): Delay in seconds before processing new files
        PROMPT_TIMEOUT (int): Seconds to keep prompt context active
        CLEANUP_INTERVAL (int): Seconds between cleanup of expired prompts
        AUTO_CLEANUP_MISSING_FILES (bool): Automatically remove missing file records
        MAX_IMAGE_AGE_DAYS (int): Maximum age in days before cleaning up images
        IMAGES_PER_PAGE (int): Number of images to display per page in web UI
        ENABLE_SEARCH (bool): Enable search functionality in web interface
        ENABLE_METADATA_VIEW (bool): Enable metadata viewing for images
        MAX_CONCURRENT_PROCESSING (int): Maximum concurrent image processing tasks
        METADATA_EXTRACTION_TIMEOUT (int): Timeout for metadata extraction operations
    """

    # Image monitoring settings
    MONITORING_ENABLED = True
    MONITORING_DIRECTORIES = []  # Auto-detect if empty
    SUPPORTED_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".gif"]
    PROCESSING_DELAY = 2.0  # Seconds to wait before processing new files

    # Prompt tracking settings
    PROMPT_TIMEOUT = (
        600  # Seconds to keep prompt context active (10 min for long generations)
    )
    CLEANUP_INTERVAL = 300  # Seconds between cleanup of expired prompts

    # Database settings
    AUTO_CLEANUP_MISSING_FILES = True
    MAX_IMAGE_AGE_DAYS = 365  # Clean up images older than this

    # Web interface settings
    IMAGES_PER_PAGE = 20
    ENABLE_SEARCH = True
    ENABLE_METADATA_VIEW = True

    # Performance settings
    MAX_CONCURRENT_PROCESSING = 3
    METADATA_EXTRACTION_TIMEOUT = 10  # Seconds

    @classmethod
    def get_config(cls) -> Dict[str, Any]:
        """Get the complete gallery configuration as a structured dictionary.

        Returns:
            Dict[str, Any]: A nested dictionary containing all gallery configuration
                sections: monitoring, tracking, database, web_interface, and
                performance. Each section contains the relevant configuration
                parameters as key-value pairs.

        Example:
            config = GalleryConfig.get_config()
            monitoring_enabled = config['monitoring']['enabled']
            images_per_page = config['web_interface']['images_per_page']
        """
        return {
            "monitoring": {
                "enabled": cls.MONITORING_ENABLED,
                "directories": cls.MONITORING_DIRECTORIES,
                "extensions": cls.SUPPORTED_EXTENSIONS,
                "processing_delay": cls.PROCESSING_DELAY,
            },
            "tracking": {
                "prompt_timeout": cls.PROMPT_TIMEOUT,
                "cleanup_interval": cls.CLEANUP_INTERVAL,
            },
            "database": {
                "auto_cleanup": cls.AUTO_CLEANUP_MISSING_FILES,
                "max_image_age_days": cls.MAX_IMAGE_AGE_DAYS,
            },
            "web_interface": {
                "images_per_page": cls.IMAGES_PER_PAGE,
                "enable_search": cls.ENABLE_SEARCH,
                "enable_metadata_view": cls.ENABLE_METADATA_VIEW,
            },
            "performance": {
                "max_concurrent_processing": cls.MAX_CONCURRENT_PROCESSING,
                "metadata_extraction_timeout": cls.METADATA_EXTRACTION_TIMEOUT,
            },
        }

    @classmethod
    def update_config(cls, new_config: Dict[str, Any]):
        """Update gallery configuration attributes from a dictionary.

        Takes a nested dictionary with gallery configuration sections and updates
        the corresponding class attributes. Only updates attributes that are
        present in the input dictionary, leaving others unchanged.

        Args:
            new_config (Dict[str, Any]): Nested dictionary containing gallery
                configuration updates. Should follow the same structure as returned
                by get_config(). Valid top-level keys are: 'monitoring', 'tracking',
                'database', 'web_interface', 'performance'.

        Example:
            gallery_settings = {
                'monitoring': {'enabled': False},
                'web_interface': {'images_per_page': 50}
            }
            GalleryConfig.update_config(gallery_settings)
        """
        monitoring = new_config.get("monitoring", {})
        if "enabled" in monitoring:
            if isinstance(monitoring["enabled"], bool):
                cls.MONITORING_ENABLED = monitoring["enabled"]
            else:  # a hand-edited "false" would otherwise switch it on
                config_logger.warning(
                    "Ignoring gallery monitoring enabled: expected true or false"
                )
        if "directories" in monitoring:
            directories = cls._validated_directories(monitoring["directories"])
            if directories is not None:
                cls.MONITORING_DIRECTORIES = directories
        if "extensions" in monitoring:
            cls.SUPPORTED_EXTENSIONS = monitoring["extensions"]
        if "processing_delay" in monitoring:
            cls.PROCESSING_DELAY = monitoring["processing_delay"]

        tracking = new_config.get("tracking", {})
        if "prompt_timeout" in tracking:
            cls.PROMPT_TIMEOUT = tracking["prompt_timeout"]
        if "cleanup_interval" in tracking:
            cls.CLEANUP_INTERVAL = tracking["cleanup_interval"]

        database = new_config.get("database", {})
        if "auto_cleanup" in database:
            cls.AUTO_CLEANUP_MISSING_FILES = database["auto_cleanup"]
        if "max_image_age_days" in database:
            cls.MAX_IMAGE_AGE_DAYS = database["max_image_age_days"]

        web_interface = new_config.get("web_interface", {})
        if "images_per_page" in web_interface:
            cls.IMAGES_PER_PAGE = web_interface["images_per_page"]
        if "enable_search" in web_interface:
            cls.ENABLE_SEARCH = web_interface["enable_search"]
        if "enable_metadata_view" in web_interface:
            cls.ENABLE_METADATA_VIEW = web_interface["enable_metadata_view"]

        performance = new_config.get("performance", {})
        if "max_concurrent_processing" in performance:
            cls.MAX_CONCURRENT_PROCESSING = performance["max_concurrent_processing"]
        if "metadata_extraction_timeout" in performance:
            cls.METADATA_EXTRACTION_TIMEOUT = performance["metadata_extraction_timeout"]

    @classmethod
    def _validated_directories(cls, directories: Any) -> Optional[List[str]]:
        """Gallery roots from a config payload that pass validate_gallery_root.

        A hand-edited config.json is as untrusted as a settings request:
        entries that fail validation are dropped with a warning (naming only
        the basename), and a value that is not a list is ignored entirely
        (``None`` is returned so the current roots stay as they are).
        """
        if not isinstance(directories, list):
            config_logger.warning(
                "Ignoring gallery monitoring directories: expected a list"
            )
            return None
        kept = []
        for entry in directories:
            ok, reason = cls.validate_gallery_root(entry)
            if ok:
                kept.append(entry)
                continue
            label = os.path.basename(entry) if isinstance(entry, str) else entry
            config_logger.warning(f"Ignoring gallery root {label!r}: {reason}")
        return kept

    @classmethod
    def allowed_gallery_parents(cls) -> List[str]:
        """Canonical directories under which gallery roots may live.

        Filesystem roots are never allowed as parents, even when listed in
        the environment, because that would re-open the whole disk.
        """
        return [canonical for canonical, _spelling in cls._allowed_parent_pairs()]

    @classmethod
    def _allowed_parent_pairs(cls) -> List[Tuple[str, str]]:
        """``(canonical, lexical spelling)`` of each allowed parent.

        Only trusted directories (ComfyUI's own and the environment's) are
        resolved here; the spelling is how ComfyUI or the environment wrote
        the directory, so a submitted path can be compared without resolving it.
        """
        pairs = []
        seen = set()
        for candidate in _comfyui_directories() + _extra_gallery_roots():
            try:
                canonical = _canonical_path(candidate)
                spelling = _lexical_path(os.path.abspath(candidate))
            except (OSError, ValueError):
                continue
            if _is_filesystem_root(canonical) or not os.path.isdir(canonical):
                continue
            if canonical not in seen:
                seen.add(canonical)
                pairs.append((canonical, spelling))
        return pairs

    @classmethod
    def path_anchors(cls) -> List[str]:
        """Canonical directories that relative gallery paths are resolved against.

        The ComfyUI base directory comes first, followed by the parent of each
        ComfyUI directory outside it (``--output-directory D:\\Renders``) and of
        each directory listed in PROMPT_MANAGER_EXTRA_GALLERY_ROOTS, so such a
        directory is addressed by its own name (``Renders/sub``) rather than by
        an absolute path, and two folders of one name stay apart. The same
        anchors drive the public (relative) form of paths in API responses.
        """
        anchors = []
        base = _comfyui_base_directory()
        candidates = [base] if base else []
        try:
            base_key = _canonical_path(base) if base else None
        except (OSError, ValueError):
            base_key = None
        for directory in _comfyui_directories():
            try:
                canonical = _canonical_path(directory)
            except (OSError, ValueError):
                continue
            if base_key is None or not _lexically_inside(canonical, [base_key]):
                candidates.append(os.path.dirname(canonical))
        candidates.extend(os.path.dirname(root) for root in _extra_gallery_roots())
        for candidate in candidates:
            try:
                canonical = _canonical_path(candidate)
            except (OSError, ValueError):
                continue
            if canonical not in anchors:
                anchors.append(canonical)
        return anchors

    @classmethod
    def resolve_gallery_root(cls, path: str) -> str:
        """Canonical absolute path for a gallery root given in any accepted form.

        Absolute paths are canonicalised as-is. Relative paths are tried
        against each :meth:`path_anchors` entry and the first existing match
        wins; otherwise the first anchor (or the current directory when there
        is none) is used, so the caller's existence check reports it.
        """
        path = path.strip()
        if os.path.isabs(path):
            return _canonical_path(path)
        anchors = cls.path_anchors()
        for anchor in anchors:
            candidate = os.path.join(anchor, path)
            if os.path.exists(candidate):
                return _canonical_path(candidate)
        return _canonical_path(os.path.join(anchors[0], path) if anchors else path)

    @classmethod
    def validate_gallery_root(cls, path: Any) -> Tuple[bool, str]:
        """Check whether ``path`` may be used as a gallery root.

        Returns:
            (True, "") when the directory lies inside one of
            :meth:`allowed_gallery_parents`; otherwise (False, reason).
        """
        code, reason = cls.gallery_root_problem(path)
        return code is None, reason

    @classmethod
    def gallery_root_problem(cls, path: Any) -> Tuple[Optional[str], str]:
        """``(code, reason)`` why ``path`` may not be a gallery root, else (None, "").

        Codes: ``empty``, ``unresolvable``, ``outside-allowed``, ``not-found``
        and ``not-directory``. No reason names the path.

        Containment is decided twice. First lexically, with no filesystem
        access at all: a path (UNC, ``\\\\?\\``, a device, anything) whose
        normalised spelling lies outside every allowed parent answers
        ``outside-allowed`` before it is resolved, so the server never opens
        or stats it (no SMB connection, no existence or timing oracle). Only
        then is it resolved and checked again, which catches a symlink out.
        The filesystem root and the home directory answer ``outside-allowed``
        too: one answer for every caller (validate-source, the settings save,
        config.json), and the detail goes to the log only.
        """
        if not isinstance(path, str) or not path.strip():
            return "empty", "Gallery root must be a non-empty path"

        pairs = cls._allowed_parent_pairs()
        spellings = [spelling for pair in pairs for spelling in pair]
        try:
            lexical = cls._lexical_candidates(path)
        except (TypeError, ValueError):
            return "unresolvable", "Gallery root could not be resolved"
        if not any(_lexically_inside(candidate, spellings) for candidate in lexical):
            return "outside-allowed", OUTSIDE_ALLOWED_REASON

        try:
            canonical = cls.resolve_gallery_root(path)
        except (OSError, ValueError):
            return "unresolvable", "Gallery root could not be resolved"

        if _is_filesystem_root(canonical):
            config_logger.info("Gallery root refused: a filesystem root")
            return "outside-allowed", OUTSIDE_ALLOWED_REASON
        if canonical == _canonical_path(os.path.expanduser("~")):
            config_logger.info("Gallery root refused: the home directory")
            return "outside-allowed", OUTSIDE_ALLOWED_REASON
        if not _lexically_inside(canonical, [canonical for canonical, _ in pairs]):
            return "outside-allowed", OUTSIDE_ALLOWED_REASON
        if not os.path.exists(canonical):
            return "not-found", "Gallery root does not exist"
        if not os.path.isdir(canonical):
            return "not-directory", "Gallery root is not a directory"
        return None, ""

    @classmethod
    def _lexical_candidates(cls, path: str) -> List[str]:
        """Every spelling ``path`` may resolve to, normalised as text (no IO).

        An absolute path is itself; a relative one is joined to each
        :meth:`path_anchors` entry, as :meth:`resolve_gallery_root` tries them.
        """
        path = path.strip()
        if os.path.isabs(path):
            return [_lexical_path(path)]
        anchors = cls.path_anchors()
        return [_lexical_path(os.path.join(anchor, path)) for anchor in anchors]


class IntegrationConfig:
    """Configuration for third-party extension integrations.

    Manages opt-in integration settings for extensions like LoraManager.
    All integrations are disabled by default so PromptManager works standalone.
    """

    # LoraManager integration
    LORA_MANAGER_ENABLED = False
    LORA_MANAGER_PATH = ""  # Auto-detected if empty
    LORA_TRIGGER_WORDS_ENABLED = False  # Auto-inject trigger words into prompts
    CIVITAI_API_KEY = ""  # Required to download NSFW example images

    @classmethod
    def get_config(cls) -> Dict[str, Any]:
        return {
            "lora_manager": {
                "enabled": cls.LORA_MANAGER_ENABLED,
                "path": cls.LORA_MANAGER_PATH,
                "trigger_words_enabled": cls.LORA_TRIGGER_WORDS_ENABLED,
                "civitai_api_key": cls.CIVITAI_API_KEY,
            },
        }

    @classmethod
    def update_config(cls, new_config: Dict[str, Any]):
        lora = new_config.get("lora_manager", {})
        if "enabled" in lora:
            cls.LORA_MANAGER_ENABLED = lora["enabled"]
        if "path" in lora:
            cls.LORA_MANAGER_PATH = lora["path"]
        if "trigger_words_enabled" in lora:
            cls.LORA_TRIGGER_WORDS_ENABLED = lora["trigger_words_enabled"]
        if "civitai_api_key" in lora:
            cls.CIVITAI_API_KEY = lora["civitai_api_key"]


# Number of tag suggestions the Tag dialogs request by default (D68). The cap
# is the image tag set limit: more suggestions could never all be kept.
DEFAULT_SUGGESTION_LIMIT = 10
MAX_SUGGESTION_LIMIT = 100  # pinned to MAX_IMAGE_TAGS by tests/test_config.py

# The tagging engines (``py/autotag.py`` MODELS) and the options they take.
WD14_MODEL_TYPES = frozenset({"wd14-swinv2", "wd14-vit"})
LLM_MODEL_TYPES = frozenset({"gguf", "hf"})
MODEL_TYPES = WD14_MODEL_TYPES | LLM_MODEL_TYPES
MAX_INSTRUCTION_CHARS = 4000
MAX_INSTRUCTION_BYTES = 16 * 1024
# What a batch does with a prompt that already has tags (#328).
EXISTING_POLICIES = ("skip", "merge", "replace_after_review")

# The language-model tag template every page starts from until one is saved.
DEFAULT_TAG_TEMPLATE = (
    "Generate only comma-separated Danbooru tags (lowercase_underscores, no spaces).\n"
    "Include: subject_count (1girl/1boy/etc), gender, ethnicity (asian, caucasian, "
    "etc), age_approx (teen, young_woman), body_attributes, face_features, "
    "hairstyle, appearance, clothing, accessories, pose, expression, action, "
    "camera_angle, camera_shot, scene_composition (wide_shot, long_shot, "
    "dutch_angle), environment, background_elements, lighting, mood.\n"
    "Prioritize visual accuracy.\n"
    "No sentences, no commentary, no captions. Only tags. Keep length short but "
    "descriptive."
)


def validate_threshold(name: str, value: Any) -> float:
    """A WD14 confidence threshold in [0, 1], or ValueError naming ``name``."""
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return float(value)


def validate_template(value: Any, name: str = "instructions") -> str:
    """Non-empty language-model instructions within the character and byte
    caps, or ValueError naming ``name``."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    if len(value) > MAX_INSTRUCTION_CHARS:
        raise ValueError(f"{name} must be at most {MAX_INSTRUCTION_CHARS} characters")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{name} must be valid Unicode text") from None
    if len(encoded) > MAX_INSTRUCTION_BYTES:
        raise ValueError(f"{name} {'are' if name == 'instructions' else 'is'} too long")
    return value


def _valid_model_type(value: Any) -> str:
    if not isinstance(value, str) or value not in MODEL_TYPES:
        raise ValueError("must be one of " + ", ".join(sorted(MODEL_TYPES)))
    return value


def _valid_existing(value: Any) -> str:
    if not isinstance(value, str) or value not in EXISTING_POLICIES:
        raise ValueError("must be one of " + ", ".join(EXISTING_POLICIES))
    return value


def _valid_suggestion_limit(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= MAX_SUGGESTION_LIMIT:
        raise ValueError(f"must be an integer from 1 to {MAX_SUGGESTION_LIMIT}")
    return value


class AutotagConfig:
    """Persisted tagging defaults under ``autotag.defaults`` in config.json.

    One tagging configuration (#328): the model, what a batch does with
    prompts already tagged, the two WD14 thresholds, the language-model
    template and how many suggestions Generate returns. Each key is checked
    alone on load; a hand-edited value that fails its check falls back to
    its default with one warning rather than failing the load.
    """

    MODEL_TYPE = "wd14-swinv2"
    EXISTING = "skip"
    GENERAL_THRESHOLD = 0.35
    CHARACTER_THRESHOLD = 0.85
    TEMPLATE = DEFAULT_TAG_TEMPLATE
    SUGGESTION_LIMIT = DEFAULT_SUGGESTION_LIMIT

    # config.json key -> (attribute, validator, default)
    _KEYS = {
        "model_type": ("MODEL_TYPE", _valid_model_type, "wd14-swinv2"),
        "existing": ("EXISTING", _valid_existing, "skip"),
        "general_threshold": (
            "GENERAL_THRESHOLD",
            lambda v: validate_threshold("value", v),
            0.35,
        ),
        "character_threshold": (
            "CHARACTER_THRESHOLD",
            lambda v: validate_threshold("value", v),
            0.85,
        ),
        "template": ("TEMPLATE", validate_template, DEFAULT_TAG_TEMPLATE),
        "suggestion_limit": (
            "SUGGESTION_LIMIT",
            _valid_suggestion_limit,
            DEFAULT_SUGGESTION_LIMIT,
        ),
    }

    @classmethod
    def suggestion_limit(cls) -> int:
        return cls.SUGGESTION_LIMIT

    @staticmethod
    def valid_suggestion_limit(value: Any) -> bool:
        return (
            type(value) is int and 1 <= value <= MAX_SUGGESTION_LIMIT
        )  # bool, float and str are rejected

    @classmethod
    def get_config(cls) -> Dict[str, Any]:
        return {
            "defaults": {
                key: getattr(cls, attr) for key, (attr, _v, _d) in cls._KEYS.items()
            }
        }

    @classmethod
    def update_config(cls, new_config: Any) -> None:
        if not isinstance(new_config, dict):
            config_logger.warning("Ignoring autotag settings that are not an object")
            return
        defaults = new_config.get("defaults", {})
        if not isinstance(defaults, dict):
            config_logger.warning("Ignoring autotag.defaults that is not an object")
            return
        for key, (attr, validate, default) in cls._KEYS.items():
            if key not in defaults:
                continue
            try:
                setattr(cls, attr, validate(defaults[key]))
            except ValueError as error:
                config_logger.warning(
                    f"autotag.defaults.{key} {error}; using {default!r}"
                    if key != "template"
                    else f"autotag.defaults.template {error}; using the built-in one"
                )
                setattr(cls, attr, default)


class PromptManagerConfig:
    """Main configuration class for PromptManager core functionality.

    This class manages configuration for database operations, web UI behavior,
    performance settings, and integrates gallery configuration. It provides
    methods for loading and saving configuration from/to JSON files.

    The configuration is organized into logical sections:
    - Database: Settings for SQLite operations and data management
    - Web UI: User interface behavior and display options
    - Performance: Optimization and resource management settings
    - Gallery: Embedded gallery configuration (via GalleryConfig)

    Attributes:
        DEFAULT_DB_PATH (str): Default path for the SQLite database file
        ENABLE_DUPLICATE_DETECTION (bool): Enable automatic duplicate detection
        ENABLE_AUTO_SAVE (bool): Enable automatic saving of prompts
        RESULT_TIMEOUT (int): Auto-hide timeout for results in ComfyUI node
        SHOW_TEST_BUTTON (bool): Show API test button in node interface
        WEBUI_DISPLAY_MODE (str): Display mode for web UI ('popup' or 'newtab')
        MAX_SEARCH_RESULTS (int): Maximum number of search results to return
        ENABLE_FUZZY_SEARCH (bool): Enable fuzzy search capabilities
        AUTO_BACKUP_INTERVAL (int): Hours between automatic database backups
    """

    # Database settings
    DEFAULT_DB_PATH = "prompts.db"
    ENABLE_DUPLICATE_DETECTION = True
    ENABLE_AUTO_SAVE = True

    # Web UI settings
    RESULT_TIMEOUT = 5  # Seconds to auto-hide results in ComfyUI node
    SHOW_TEST_BUTTON = False  # Show API test button in node UI
    WEBUI_DISPLAY_MODE = "newtab"  # 'popup' or 'newtab'
    INFINITE_SCROLL = False  # Dashboard list: load the next page on scroll by default

    # Performance settings
    MAX_SEARCH_RESULTS = 100
    ENABLE_FUZZY_SEARCH = False  # Requires fuzzywuzzy
    AUTO_BACKUP_INTERVAL = 24  # Hours
    # Threads for the output scan and thumbnail generation; half the cores by
    # default so ComfyUI's own generation keeps CPU headroom. 1..max_worker_threads().
    DEFAULT_WORKER_THREADS = max(1, (os.cpu_count() or 1) // 2)
    WORKER_THREADS = DEFAULT_WORKER_THREADS

    # Maintenance: a reviewed apply takes a verified safety backup of the
    # database first (System › Backup & export › Recovery policy, #249 S6).
    SAFETY_BACKUP = True

    # "Keep it here" (#249 S6b): the user chose to keep a node-folder library
    # where it is, so the workspace stops offering to move it.
    KEEP_LIBRARY_IN_NODE_FOLDER = False

    # Thumbnails (#317): every preview writer reads these through
    # py/api/thumbnail_settings.py (System › Appearance). The legacy
    # ``gallery.web_interface.thumbnail_size`` was never read and is ignored.
    THUMBNAIL_FORMATS = ("webp", "jxl", "jpeg")
    THUMBNAIL_FORMAT = "webp"
    THUMBNAIL_QUALITY = 80
    THUMBNAIL_EDGE = 512
    THUMBNAIL_EDGE_RANGE = (128, 2048)

    @classmethod
    def max_worker_threads(cls) -> int:
        """Largest accepted worker-thread count: the detected core count."""
        return os.cpu_count() or 1

    @classmethod
    def clamp_worker_threads(cls, value, fallback: int) -> int:
        """Coerce a worker count from config.json into ``[1, max_worker_threads()]``.

        Non-numeric values (and booleans) return ``fallback`` so a hand-edited
        file cannot disable the setting.
        """
        if isinstance(value, bool):
            return fallback
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if not isinstance(value, int):
            return fallback
        return max(1, min(value, cls.max_worker_threads()))

    @classmethod
    def get_config(cls) -> Dict[str, Any]:
        """Get the complete PromptManager configuration as a structured dictionary.

        Returns:
            Dict[str, Any]: A nested dictionary containing all configuration sections:
                - database: Database-related settings
                - web_ui: Web interface configuration
                - performance: Performance and optimization settings
                - gallery: Complete gallery configuration (from GalleryConfig)

        Example:
            config = PromptManagerConfig.get_config()
            db_path = config['database']['default_path']
            max_results = config['performance']['max_search_results']
        """
        return {
            "database": {
                "default_path": cls.DEFAULT_DB_PATH,
                "enable_duplicate_detection": cls.ENABLE_DUPLICATE_DETECTION,
                "enable_auto_save": cls.ENABLE_AUTO_SAVE,
            },
            "web_ui": {
                "result_timeout": cls.RESULT_TIMEOUT,
                "show_test_button": cls.SHOW_TEST_BUTTON,
                "webui_display_mode": cls.WEBUI_DISPLAY_MODE,
                "infinite_scroll": cls.INFINITE_SCROLL,
            },
            "performance": {
                "max_search_results": cls.MAX_SEARCH_RESULTS,
                "enable_fuzzy_search": cls.ENABLE_FUZZY_SEARCH,
                "auto_backup_interval": cls.AUTO_BACKUP_INTERVAL,
                "worker_threads": cls.WORKER_THREADS,
            },
            "maintenance": {"safety_backup": cls.SAFETY_BACKUP},
            "library": {"keep_in_node_folder": cls.KEEP_LIBRARY_IN_NODE_FOLDER},
            "thumbnails": {
                "format": cls.THUMBNAIL_FORMAT,
                "quality": cls.THUMBNAIL_QUALITY,
                "edge": cls.THUMBNAIL_EDGE,
            },
            "gallery": GalleryConfig.get_config(),
            "integrations": IntegrationConfig.get_config(),
            "autotag": AutotagConfig.get_config(),
        }

    @classmethod
    def get_config_path(cls) -> str:
        """Path of the persisted config.json.

        Honours ``PROMPT_MANAGER_CONFIG_PATH`` when set; otherwise it lives in
        the library's data folder (``utils.library_location``): the node
        folder for a library that was not moved, else ComfyUI's
        ``user/__promptmanager``.
        """
        return library_location.config_location()[0]

    @classmethod
    def load_from_file(cls, config_path: Optional[str] = None):
        """Load configuration settings from a JSON file.

        Reads configuration from the specified JSON file and updates the current
        configuration attributes. If the file doesn't exist or contains invalid
        JSON, logs an appropriate message and continues with default values.

        Args:
            config_path (str): Path to the JSON configuration file to load.
                            Can be relative or absolute path. Defaults to
                            :meth:`get_config_path`.

        Raises:
            The method handles all exceptions internally and logs errors rather
            than propagating them, ensuring the system continues with defaults.

        Example:
            PromptManagerConfig.load_from_file('custom_config.json')
            PromptManagerConfig.load_from_file('/path/to/config.json')
        """
        import json

        if config_path is None:
            config_path = cls.get_config_path()

        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
                cls.update_config(config)
                config_logger.info(f"Loaded configuration from {config_path}")
            except Exception as e:
                config_logger.error(f"Error loading config from {config_path}: {e}")
        else:
            config_logger.info(f"Config file not found: {config_path}, using defaults")

    @classmethod
    def save_to_file(cls, config_path: Optional[str] = None):
        """Save the current configuration to a JSON file.

        Serializes the complete configuration (including gallery settings) to
        a JSON file. Creates the directory structure if it doesn't exist.

        Args:
            config_path (str): Path where the JSON configuration file should be saved.
                            Parent directories will be created if they don't exist.
                            Defaults to :meth:`get_config_path`.

        Raises:
            The method handles all exceptions internally and logs errors rather
            than propagating them.

        Example:
            PromptManagerConfig.save_to_file('backup_config.json')
            PromptManagerConfig.save_to_file('/etc/comfyui/prompt_manager.json')
        """
        if config_path is None:
            config_path = cls.get_config_path()

        try:
            write_private_json(config_path, cls.get_config())
            config_logger.info(f"Saved configuration to {config_path}")
        except Exception as e:
            config_logger.error(f"Error saving config to {config_path}: {e}")

    @classmethod
    def update_config(cls, new_config: Dict[str, Any]):
        """Update configuration attributes from a dictionary.

        Takes a nested dictionary with configuration sections and updates
        the corresponding class attributes. Only updates attributes that
        are present in the input dictionary, leaving others unchanged.

        Args:
            new_config (Dict[str, Any]): Nested dictionary containing configuration
                updates. Should follow the same structure as returned by get_config().
                Valid top-level keys are: 'database', 'web_ui', 'performance',
                'gallery'.

        Example:
            new_settings = {
                'database': {'default_path': 'custom.db'},
                'performance': {'max_search_results': 50}
            }
            PromptManagerConfig.update_config(new_settings)
        """
        database = new_config.get("database", {})
        if "default_path" in database:
            cls.DEFAULT_DB_PATH = database["default_path"]
        if "enable_duplicate_detection" in database:
            cls.ENABLE_DUPLICATE_DETECTION = database["enable_duplicate_detection"]
        if "enable_auto_save" in database:
            cls.ENABLE_AUTO_SAVE = database["enable_auto_save"]

        web_ui = new_config.get("web_ui", {})
        if "result_timeout" in web_ui:
            cls.RESULT_TIMEOUT = web_ui["result_timeout"]
        if "show_test_button" in web_ui:
            cls.SHOW_TEST_BUTTON = web_ui["show_test_button"]
        if "webui_display_mode" in web_ui:
            cls.WEBUI_DISPLAY_MODE = web_ui["webui_display_mode"]
        if isinstance(web_ui.get("infinite_scroll"), bool):
            cls.INFINITE_SCROLL = web_ui["infinite_scroll"]

        performance = new_config.get("performance", {})
        if "max_search_results" in performance:
            cls.MAX_SEARCH_RESULTS = performance["max_search_results"]
        if "enable_fuzzy_search" in performance:
            cls.ENABLE_FUZZY_SEARCH = performance["enable_fuzzy_search"]
        if "auto_backup_interval" in performance:
            cls.AUTO_BACKUP_INTERVAL = performance["auto_backup_interval"]
        if "worker_threads" in performance:
            cls.WORKER_THREADS = cls.clamp_worker_threads(
                performance["worker_threads"], cls.WORKER_THREADS
            )

        maintenance = new_config.get("maintenance", {})
        if isinstance(maintenance, dict) and isinstance(
            maintenance.get("safety_backup"), bool
        ):
            cls.SAFETY_BACKUP = maintenance["safety_backup"]

        library = new_config.get("library", {})
        if isinstance(library, dict) and isinstance(
            library.get("keep_in_node_folder"), bool
        ):
            cls.KEEP_LIBRARY_IN_NODE_FOLDER = library["keep_in_node_folder"]

        cls._update_thumbnails(new_config.get("thumbnails"))

        # Update gallery config
        if "gallery" in new_config:
            GalleryConfig.update_config(new_config["gallery"])

        # Update integration config
        if "integrations" in new_config:
            IntegrationConfig.update_config(new_config["integrations"])

        if "autotag" in new_config:
            AutotagConfig.update_config(new_config["autotag"])

    @classmethod
    def _update_thumbnails(cls, section: Any) -> None:
        """Apply a ``thumbnails`` section; a hand-edited bad value is ignored."""
        if not isinstance(section, dict):
            return
        fmt = section.get("format")
        if fmt in cls.THUMBNAIL_FORMATS:
            cls.THUMBNAIL_FORMAT = fmt
        quality = section.get("quality")
        if type(quality) is int and 1 <= quality <= 100:
            cls.THUMBNAIL_QUALITY = quality
        edge = section.get("edge")
        low, high = cls.THUMBNAIL_EDGE_RANGE
        if type(edge) is int and low <= edge <= high:
            cls.THUMBNAIL_EDGE = edge


# Load configuration on import
try:
    PromptManagerConfig.load_from_file()
except Exception as e:
    config_logger.error(f"Error during config initialization: {e}")
