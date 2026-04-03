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
    """Walk upward from this file to find the ComfyUI root (contains main.py)."""
    current = Path(__file__).resolve().parent
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

    # 2. Auto-detect via custom_nodes
    root = find_comfyui_root()
    if root:
        candidate = root / "custom_nodes" / _LORA_MANAGER_DIR_NAME
        if candidate.is_dir() and _looks_like_lora_manager(candidate):
            return str(candidate.resolve())

    return None


def _looks_like_lora_manager(path: Path) -> bool:
    """Heuristic: does this directory look like a LoraManager install?"""
    # Check for characteristic files
    markers = ["__init__.py", "README.md"]
    has_marker = any((path / m).exists() for m in markers)
    # Check for characteristic subdirectories or module name
    has_lora_ref = (
        (path / "lora_manager").is_dir()
        or (path / "py").is_dir()
        or "lora" in path.name.lower()
    )
    return has_marker and has_lora_ref


# ── Metadata reading ─────────────────────────────────────────────────


def find_lora_directories(lora_manager_path: str) -> List[str]:
    """Find directories that contain LoRA models (with .metadata.json files).

    Searches common locations: the LoraManager extension dir itself,
    and the ComfyUI models/loras directory.
    """
    dirs = set()
    lm_path = Path(lora_manager_path)

    # Check ComfyUI models/loras
    root = find_comfyui_root()
    if root:
        models_loras = root / "models" / "loras"
        if models_loras.is_dir():
            dirs.add(str(models_loras.resolve()))

    # Check for any .metadata.json in the LoraManager dir tree
    # (some users store loras inside the extension)
    for meta in lm_path.rglob("*.metadata.json"):
        dirs.add(str(meta.parent.resolve()))

    return sorted(dirs)


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
            for meta_file in dir_path.glob("*.metadata.json"):
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
