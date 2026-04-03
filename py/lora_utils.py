"""Utilities for LoraManager integration.

Provides detection, metadata reading, and trigger word lookup for
ComfyUI-Lora-Manager (https://github.com/willmiao/ComfyUI-Lora-Manager).

All functions are safe to call when LoraManager is not installed — they
return empty results rather than raising.
"""

import json
import os
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from ..utils.logging_config import get_logger
except ImportError:
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from utils.logging_config import get_logger

logger = get_logger("prompt_manager.lora_utils")

# ── LoraManager detection ────────────────────────────────────────────

_LORA_MANAGER_DIR_NAME = "ComfyUI-Lora-Manager"


def find_comfyui_root() -> Optional[Path]:
    """Walk upward from this file to find the ComfyUI root (contains main.py).

    Tries both the resolved (real) path and the unresolved path to handle
    symlinked custom_nodes installations.
    """
    start_paths = [Path(__file__).resolve().parent]

    # If installed via symlink, the unresolved path leads through custom_nodes/
    raw_path = Path(__file__).parent
    if raw_path.resolve() != raw_path:
        start_paths.append(raw_path)

    # Also try via folder_paths if available (ComfyUI runtime)
    try:
        import folder_paths

        base = Path(folder_paths.base_path)
        if base.is_dir():
            return base
    except (ImportError, AttributeError):
        pass

    for start in start_paths:
        current = start
        for _ in range(10):
            if (current / "main.py").exists() and (current / "custom_nodes").exists():
                return current
            parent = current.parent
            if parent == current:
                break
            current = parent

    return None


def detect_lora_manager(custom_path: str = "") -> Optional[str]:
    """Return the absolute path to ComfyUI-Lora-Manager if installed.

    Args:
        custom_path: User-provided override path. Checked first.

    Returns:
        Absolute path string, or None if not found.
    """
    # 1. User override
    if custom_path:
        p = Path(custom_path)
        if p.is_dir() and _looks_like_lora_manager(p):
            return str(p.resolve())

    # 2. Auto-detect via custom_nodes (case-insensitive scan)
    root = find_comfyui_root()
    if root:
        custom_nodes = root / "custom_nodes"
        if custom_nodes.is_dir():
            for entry in custom_nodes.iterdir():
                if (
                    entry.is_dir()
                    and "lora" in entry.name.lower()
                    and "manager" in entry.name.lower()
                    and _looks_like_lora_manager(entry)
                ):
                    return str(entry.resolve())

    return None


def _looks_like_lora_manager(path: Path) -> bool:
    """Heuristic: does this directory look like a LoraManager install?"""
    # Must have __init__.py (ComfyUI extension) or README.md
    has_init = (path / "__init__.py").exists()
    if not has_init:
        return False
    # Check for characteristic structure: py/ dir, or any .metadata.json nearby
    return (path / "py").is_dir() or (path / "lora_manager").is_dir()


# ── Metadata reading ─────────────────────────────────────────────────


def find_lora_directories(lora_manager_path: str) -> List[str]:
    """Find directories that contain LoRA models (with .metadata.json files).

    Searches: ComfyUI models/loras, extra_model_paths.yaml lora dirs,
    and the LoraManager extension dir itself.
    """
    dirs = set()
    lm_path = Path(lora_manager_path)

    root = find_comfyui_root()
    if root:
        # Default models/loras
        models_loras = root / "models" / "loras"
        if models_loras.is_dir():
            dirs.add(str(models_loras.resolve()))

        # Extra model paths from ComfyUI config
        for extra_dir in _get_extra_lora_paths(root):
            if extra_dir.is_dir():
                dirs.add(str(extra_dir.resolve()))

    # Also try folder_paths at runtime (catches all configured paths)
    try:
        import folder_paths

        for p in folder_paths.get_folder_paths("loras"):
            pp = Path(p)
            if pp.is_dir():
                dirs.add(str(pp.resolve()))
    except (ImportError, AttributeError):
        pass

    # Check for any .metadata.json in the LoraManager dir tree
    for meta in lm_path.rglob("*.metadata.json"):
        dirs.add(str(meta.parent.resolve()))

    return sorted(dirs)


def _get_extra_lora_paths(comfyui_root: Path) -> List[Path]:
    """Parse extra_model_paths.yaml for additional LoRA directories."""
    results = []
    for name in ("extra_model_paths.yaml", "extra_model_paths.yml"):
        config_file = comfyui_root / name
        if not config_file.exists():
            continue
        try:
            import yaml

            config = yaml.safe_load(config_file.read_text())
            if not isinstance(config, dict):
                continue
            for section in config.values():
                if not isinstance(section, dict):
                    continue
                base = Path(section.get("base_path", ""))
                loras_val = section.get("loras", "")
                if not loras_val:
                    continue
                for line in str(loras_val).strip().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    p = Path(line)
                    if not p.is_absolute():
                        p = base / line
                    if p.is_dir():
                        results.append(p)
        except Exception as e:
            logger.debug(f"Failed to parse {config_file}: {e}")
    return results


def read_lora_metadata(metadata_path: Path) -> Optional[Dict]:
    """Read and parse a single .metadata.json file.

    Returns:
        Parsed dict, or None on failure.
    """
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.debug(f"Failed to read {metadata_path}: {e}")
        return None


def get_trigger_words_from_metadata(metadata: Dict) -> List[str]:
    """Extract trigger words from a parsed LoraManager metadata dict."""
    civitai = metadata.get("civitai", {})
    if not civitai:
        return []
    words = civitai.get("trainedWords", [])
    if isinstance(words, list):
        return [w.strip() for w in words if isinstance(w, str) and w.strip()]
    return []


def get_model_name_from_metadata(metadata: Dict) -> str:
    """Extract the model display name from metadata."""
    # Try civitai model name first, then file_name, then fallback
    name = metadata.get("model_name", "")
    if not name:
        civitai = metadata.get("civitai", {})
        model = civitai.get("model", {})
        name = model.get("name", "")
    if not name:
        name = metadata.get("file_name", "unknown")
    return name


def get_preview_image_from_metadata(
    metadata: Dict, metadata_path: Path
) -> Optional[str]:
    """Find the preview image path for a LoRA from its metadata.

    Returns:
        Absolute path string to the preview image, or None.
    """
    lora_dir = metadata_path.parent
    file_name = metadata.get("file_name", "")
    if not file_name:
        # Derive from metadata filename: "foo.metadata.json" -> "foo"
        stem = metadata_path.name.replace(".metadata.json", "")
        file_name = stem

    base_name = Path(file_name).stem

    # Check standard preview naming conventions
    for ext in (".png", ".jpg", ".jpeg", ".preview.png", ".preview.jpg"):
        candidate = lora_dir / f"{base_name}{ext}"
        if candidate.exists():
            return str(candidate.resolve())

    return None


def get_example_images_dir(lora_manager_path: str) -> Optional[str]:
    """Find the LoraManager example_images directory."""
    lm_path = Path(lora_manager_path)

    # Direct subdirectory
    candidate = lm_path / "example_images"
    if candidate.is_dir():
        return str(candidate.resolve())

    # Search one level in user data dirs
    for child in lm_path.iterdir():
        if child.is_dir():
            sub = child / "example_images"
            if sub.is_dir():
                return str(sub.resolve())

    return None


# ── Trigger word cache & injection ───────────────────────────────────

_LORA_PATTERN = re.compile(r"<lora:([^:>]+):[^>]+>", re.IGNORECASE)


class TriggerWordCache:
    """Thread-safe cache mapping LoRA names to their trigger words.

    Built lazily on first access, refreshable on demand.
    """

    def __init__(self):
        self._cache: Dict[str, List[str]] = {}
        self._lock = threading.Lock()
        self._loaded = False

    def load(self, lora_manager_path: str) -> int:
        """Scan LoRA metadata files and build the trigger word mapping.

        Returns:
            Number of LoRAs with trigger words found.
        """
        new_cache: Dict[str, List[str]] = {}

        lora_dirs = find_lora_directories(lora_manager_path)
        for lora_dir in lora_dirs:
            dir_path = Path(lora_dir)
            for meta_file in dir_path.rglob("*.metadata.json"):
                metadata = read_lora_metadata(meta_file)
                if not metadata:
                    continue

                words = get_trigger_words_from_metadata(metadata)
                if not words:
                    continue

                # Key by filename stem (what appears in <lora:NAME:weight>)
                file_name = metadata.get("file_name", "")
                if file_name:
                    stem = Path(file_name).stem
                    new_cache[stem.lower()] = words

                # Also key by the metadata file stem
                meta_stem = meta_file.name.replace(".metadata.json", "")
                if meta_stem.lower() not in new_cache:
                    new_cache[meta_stem.lower()] = words

        with self._lock:
            self._cache = new_cache
            self._loaded = True

        logger.info(
            f"Trigger word cache loaded: {len(new_cache)} LoRAs with trigger words"
        )
        return len(new_cache)

    def get_trigger_words(self, lora_name: str) -> List[str]:
        """Look up trigger words for a LoRA by name (case-insensitive)."""
        with self._lock:
            return self._cache.get(lora_name.lower(), [])

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return self._loaded

    def clear(self):
        with self._lock:
            self._cache.clear()
            self._loaded = False


# Module-level singleton
_trigger_cache = TriggerWordCache()


def get_trigger_cache() -> TriggerWordCache:
    return _trigger_cache


def inject_trigger_words(text: str, cache: TriggerWordCache) -> Tuple[str, List[str]]:
    """Scan text for <lora:NAME:WEIGHT> tags and append trigger words.

    Args:
        text: The prompt text potentially containing lora tags.
        cache: Populated TriggerWordCache instance.

    Returns:
        Tuple of (modified_text, list_of_injected_words).
        If no trigger words found, returns the original text unchanged.
    """
    if not cache.is_loaded:
        return text, []

    matches = _LORA_PATTERN.findall(text)
    if not matches:
        return text, []

    all_words = []
    for lora_name in matches:
        words = cache.get_trigger_words(lora_name)
        for w in words:
            if w.lower() not in text.lower() and w not in all_words:
                all_words.append(w)

    if not all_words:
        return text, []

    injected = ", ".join(all_words)
    return f"{text}, {injected}", all_words
