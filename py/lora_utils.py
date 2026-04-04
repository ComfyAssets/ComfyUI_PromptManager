"""Utilities for LoraManager integration.

Provides detection, metadata reading, and trigger word lookup for
ComfyUI-Lora-Manager (https://github.com/willmiao/ComfyUI-Lora-Manager).

All functions are safe to call when LoraManager is not installed — they
return empty results rather than raising.
"""

import hashlib
import json
import os
import re
import threading
import urllib.request
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
        # folder_paths unavailable — not running inside ComfyUI runtime
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
        # folder_paths unavailable — not running inside ComfyUI runtime
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


def _get_civitai(metadata: Dict) -> Dict:
    """Safely get the civitai dict, handling None values."""
    return metadata.get("civitai") or {}


def get_trigger_words_from_metadata(metadata: Dict) -> List[str]:
    """Extract trigger words from a parsed LoraManager metadata dict."""
    civitai = _get_civitai(metadata)
    words = civitai.get("trainedWords", [])
    if isinstance(words, list):
        return [w.strip() for w in words if isinstance(w, str) and w.strip()]
    return []


def get_example_prompt_from_metadata(metadata: Dict) -> Optional[str]:
    """Extract an example prompt from civitai image metadata.

    Looks at civitai.images[].meta.prompt for the first available example.
    """
    civitai = _get_civitai(metadata)
    images = civitai.get("images", []) or []
    for img in images:
        if not isinstance(img, dict):
            continue
        meta = img.get("meta")
        if isinstance(meta, dict):
            prompt = meta.get("prompt", "")
            if isinstance(prompt, str) and prompt.strip():
                return prompt.strip()
    return None


def get_civitai_image_urls(metadata: Dict) -> List[str]:
    """Extract all civitai example image URLs from metadata."""
    civitai = _get_civitai(metadata)
    urls = []
    for img in civitai.get("images", []) or []:
        if not isinstance(img, dict):
            continue
        url = img.get("url", "")
        if isinstance(url, str) and url.strip():
            urls.append(url.strip())
    return urls


def get_model_name_from_metadata(metadata: Dict) -> str:
    """Extract the model display name from metadata."""
    name = metadata.get("model_name", "")
    if not name:
        civitai = _get_civitai(metadata)
        model = civitai.get("model") or {}
        name = model.get("name", "")
    if not name:
        name = metadata.get("file_name", "unknown")
    return name


def get_preview_images_from_metadata(metadata: Dict, metadata_path: Path) -> List[str]:
    """Find all local preview/example image paths for a LoRA.

    Returns:
        List of absolute path strings to image files.
    """
    results = []
    lora_dir = metadata_path.parent
    file_name = metadata.get("file_name", "")
    if not file_name:
        stem = metadata_path.name.replace(".metadata.json", "")
        file_name = stem

    base_name = Path(file_name).stem

    # Check standard preview naming conventions
    for ext in (
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".preview.png",
        ".preview.jpg",
        ".preview.jpeg",
    ):
        candidate = lora_dir / f"{base_name}{ext}"
        if candidate.exists():
            results.append(str(candidate.resolve()))

    return results


def get_preview_image_from_metadata(
    metadata: Dict, metadata_path: Path
) -> Optional[str]:
    """Find the first preview image path for a LoRA (backward compat)."""
    images = get_preview_images_from_metadata(metadata, metadata_path)
    return images[0] if images else None


_THUMB_MAX_SIZE = 512


def _download_one(url: str, local_path: Path, api_key: str) -> Optional[str]:
    """Download a single image, resize to thumbnail, save as JPEG."""
    try:
        headers = {"User-Agent": "ComfyUI-PromptManager/1.0"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()

        # Resize to thumbnail to save disk space
        from io import BytesIO

        from PIL import Image

        img = Image.open(BytesIO(raw))
        img.thumbnail((_THUMB_MAX_SIZE, _THUMB_MAX_SIZE), Image.LANCZOS)
        img = img.convert("RGB")
        img.save(str(local_path), "JPEG", quality=85)

        return str(local_path.resolve())
    except Exception as e:
        logger.debug(f"Failed to download {url}: {e}")
        return None


def download_civitai_images(
    metadata: Dict, metadata_path: Path, cache_dir: Path, api_key: str = ""
) -> List[str]:
    """Download civitai example images to a local cache directory.

    Uses thumbnail URLs (512px) instead of full-size originals, and
    downloads in parallel (up to 8 concurrent) for speed.

    Args:
        api_key: CivitAI API key for authenticated downloads (NSFW content).

    Returns:
        List of absolute paths to downloaded image files.
    """
    from concurrent.futures import ThreadPoolExecutor

    civitai = _get_civitai(metadata)
    images = civitai.get("images", []) or []
    if not images:
        return []

    file_name = metadata.get("file_name", "")
    if not file_name:
        file_name = metadata_path.name.replace(".metadata.json", "")
    lora_stem = Path(file_name).stem

    lora_cache = cache_dir / lora_stem
    lora_cache.mkdir(parents=True, exist_ok=True)

    # Build download tasks
    cached = []
    tasks = []  # (url, local_path)
    for img in images:
        if not isinstance(img, dict):
            continue
        url = img.get("url", "")
        if not isinstance(url, str) or not url.startswith("http"):
            continue

        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        local_path = lora_cache / f"{url_hash}.jpg"

        if local_path.exists():
            cached.append(str(local_path.resolve()))
        else:
            tasks.append((url, local_path))

    if not tasks:
        return cached

    # Download in parallel
    downloaded = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(_download_one, url, path, api_key) for url, path in tasks
        ]
        for fut in futures:
            result = fut.result()
            if result:
                downloaded.append(result)

    return cached + downloaded


def get_lora_image_cache_dir() -> Path:
    """Get the directory used to cache downloaded LoRA example images."""
    # Store in the extension's own directory
    ext_root = Path(__file__).resolve().parent.parent
    cache = ext_root / "data" / "lora_images"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


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
