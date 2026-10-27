"""Utilities for LoraManager integration.

Provides detection, metadata reading, and trigger word lookup for
ComfyUI-Lora-Manager (https://github.com/willmiao/ComfyUI-Lora-Manager).

All functions are safe to call when LoraManager is not installed — they
return empty results rather than raising.
"""

import hashlib
import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

try:
    from ..utils.logging_config import get_logger
except ImportError:
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from utils.logging_config import get_logger

from dataclasses import replace

from .lora_layout_cache import LayoutCache
from .lora_manager_layout import (
    MODE_ALONGSIDE,
    METADATA_SUFFIX,
    LoraManagerLayout,
    discover_sidecars,
    load_layout,
)

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

    # 2. Auto-detect in every custom_nodes folder (case-insensitive scan):
    # ComfyUI's own first, then the extra ones it registered (#144).
    folders = []
    root = find_comfyui_root()
    if root:
        folders.append(root / "custom_nodes")
    folders.extend(custom_nodes_directories())
    seen = set()
    for custom_nodes in folders:
        key = os.path.normcase(os.path.abspath(str(custom_nodes)))
        if key in seen:
            continue
        seen.add(key)
        found = _lora_manager_in(Path(custom_nodes))
        if found:
            return found

    return None


def _lora_manager_in(custom_nodes: Path) -> Optional[str]:
    """The LoraManager install directly under ``custom_nodes``, or None."""
    try:
        entries = sorted(custom_nodes.iterdir()) if custom_nodes.is_dir() else []
    except OSError:
        return None
    for entry in entries:
        try:
            if (
                entry.is_dir()
                and "lora" in entry.name.lower()
                and "manager" in entry.name.lower()
                and _looks_like_lora_manager(entry)
            ):
                return str(entry.resolve())
        except OSError:
            continue
    return None


def _looks_like_lora_manager(path: Path) -> bool:
    """Heuristic: does this directory look like a LoraManager install?"""
    # Must have __init__.py (ComfyUI extension) or README.md
    has_init = (path / "__init__.py").exists()
    if not has_init:
        return False
    # Check for characteristic structure: py/ dir, or any .metadata.json nearby
    return (path / "py").is_dir() or (path / "lora_manager").is_dir()


def _public_path_anchors() -> List[str]:
    """Directories the API's public (relative) paths are rendered against."""
    try:
        from .config import GalleryConfig
    except ImportError:
        try:
            from config import GalleryConfig
        except ImportError:
            return []
    try:
        return list(GalleryConfig.path_anchors())
    except Exception:
        return []


def custom_nodes_directories() -> List[Path]:
    """ComfyUI ``custom_nodes`` directories a LoraManager install may live in.

    Inside ComfyUI these come from ``folder_paths`` (``base_path`` plus any
    registered ``custom_nodes`` folders); outside it, the parent of this
    package, which is ``custom_nodes`` for a normal install.
    """
    found: List[Path] = []

    def add(candidate) -> None:
        try:
            resolved = Path(candidate).resolve()
        except (OSError, RuntimeError, ValueError):
            return
        if resolved not in found:
            found.append(resolved)

    try:
        import folder_paths
    except ImportError:
        folder_paths = None
    if folder_paths is not None:
        base = getattr(folder_paths, "base_path", None)
        if isinstance(base, str) and base:
            add(Path(base) / "custom_nodes")
        getter = getattr(folder_paths, "get_folder_paths", None)
        if callable(getter):
            try:
                registered = getter("custom_nodes") or []
            except Exception:
                registered = []
            for entry in registered:
                add(entry)
    if not found:
        add(Path(__file__).resolve().parent.parent.parent)
    return found


def resolve_lora_manager_path(path) -> Optional[str]:
    """Canonical form of ``path`` when it may be used as the LoraManager install.

    Accepted: an existing directory that is a direct child of one of
    :func:`custom_nodes_directories` and whose name contains ``lora``
    (case-insensitive). A relative path is tried against the ComfyUI root
    and each custom_nodes directory. Anything else, including a symlink
    that escapes custom_nodes, yields None.
    """
    if not isinstance(path, str) or not path.strip():
        return None
    path = path.strip()
    roots = []
    for root in custom_nodes_directories():
        try:
            roots.append(Path(os.path.normcase(os.path.realpath(str(root)))))
        except (OSError, ValueError):
            continue

    candidates = [path]
    if not os.path.isabs(path):
        # Public (API) paths are relative to these anchors; the ComfyUI root
        # and the custom_nodes directories cover hand-typed relative paths.
        bases = list(_public_path_anchors())
        comfy_root = find_comfyui_root()
        if comfy_root:
            bases.append(comfy_root)
        bases.extend(root.parent for root in roots)
        bases.extend(roots)
        candidates = [os.path.join(str(base), path) for base in bases]

    for candidate in candidates:
        try:
            canonical = Path(os.path.normcase(os.path.realpath(candidate)))
            is_dir = canonical.is_dir()
        except (OSError, RuntimeError, ValueError):
            continue
        if not is_dir or "lora" not in canonical.name.lower():
            continue
        if canonical.parent in roots:
            return str(canonical)
    return None


# ── Metadata reading ─────────────────────────────────────────────────


def default_lora_roots() -> List[str]:
    """The LoRA folders ComfyUI knows, without LoraManager's settings.

    ComfyUI's ``models/loras``, the ``loras`` entries of
    ``extra_model_paths.yaml`` and, inside ComfyUI, every folder
    ``folder_paths`` registers for ``loras``. ``lora_manager_layout`` adds
    the folders LoraManager's own settings name.
    """
    dirs = set()

    root = find_comfyui_root()
    if root:
        models_loras = root / "models" / "loras"
        if models_loras.is_dir():
            dirs.add(str(models_loras.resolve()))

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

    return sorted(dirs)


def lora_manager_install(lora_manager_path: Optional[str]) -> Optional[str]:
    """The LoraManager install to read: the configured path when it is an
    allowed install under ``custom_nodes``, auto-detection when it is empty."""
    if isinstance(lora_manager_path, str) and lora_manager_path.strip():
        return resolve_lora_manager_path(lora_manager_path)
    detected = detect_lora_manager()
    return resolve_lora_manager_path(detected) if detected else None


def layout_for_install(install: Optional[str]) -> LoraManagerLayout:
    """The layout of an already resolved install (None: none was found)."""
    return load_layout(install, default_roots=default_lora_roots)


def lora_manager_layout(lora_manager_path: Optional[str]) -> LoraManagerLayout:
    """Where LoraManager keeps its files (``py/lora_manager_layout.py``).

    A fresh read: the import review, Start, the legacy scan and the trigger
    word cache use it. The image hot paths use ``cached_lora_manager_layout``.
    """
    return layout_for_install(lora_manager_install(lora_manager_path))


# The image hot paths' layout cache (#362). The lambdas look the functions up
# when called, so a test that patches them here is seen by the cache.
_layout_cache = LayoutCache(
    lambda path: lora_manager_install(path),
    lambda install: layout_for_install(install),
)


def cached_lora_manager_layout(
    configured: Optional[str],
) -> Tuple[Optional[str], LoraManagerLayout]:
    """``(install, layout)`` for the configured path, from the layout cache
    while LoraManager's settings files are unchanged (``py/lora_layout_cache.py``)."""
    return _layout_cache.get(configured or "")


def clear_layout_cache() -> None:
    """Drop the cached layout (after an import linked files in new folders)."""
    _layout_cache.clear()


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


_THUMB_MAX_SIZE = 512

# CivitAI's own domains (#340: civitai.red and civitai.green serve the same
# content as civitai.com).
CIVITAI_DOMAINS = ("civitai.com", "civitai.red", "civitai.green")
# Only these hosts may ever receive the CivitAI API key, and only over HTTPS.
CIVITAI_HOSTS = frozenset(
    f"{prefix}{domain}"
    for domain in CIVITAI_DOMAINS
    for prefix in ("", "www.", "api.", "image.")
)
MAX_CIVITAI_DOWNLOAD_BYTES = 50 * 1024 * 1024
_DOWNLOAD_CHUNK_BYTES = 64 * 1024


# A redirect is followed (#337: image.civitai.com now answers with a redirect
# to blobs-b2.civitai.com) only over HTTPS to one of CIVITAI_DOMAINS or a
# subdomain of one, at most this many hops, and never with the API key.
MAX_CIVITAI_REDIRECTS = 3
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_USER_AGENT = "ComfyUI-PromptManager/1.0"


class _DownloadFailed(Exception):
    """A download failed for the short, loggable ``reason`` it carries."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect inside urllib: it would re-send the Bearer
    header. A 3xx surfaces as ``HTTPError``; ``_download_one`` decides."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_opener(*handlers) -> urllib.request.OpenerDirector:
    """Build the outbound opener; extra handlers let tests serve canned responses."""
    return urllib.request.build_opener(_RefuseRedirects, *handlers)


_opener = _build_opener()


def _open_url(req: urllib.request.Request, timeout: float = 10):
    """Open ``req`` through the redirect-refusing opener."""
    return _opener.open(req, timeout=timeout)


def _loggable(url: str) -> str:
    """``url`` without its query, fragment or credentials (signed URLs)."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return "<unparsable URL>"
    return f"{parts.scheme}://{host}{parts.path}"


def _on_civitai_domain(hostname: str) -> bool:
    """True when ``hostname`` is one of CIVITAI_DOMAINS or a subdomain of one."""
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in CIVITAI_DOMAINS
    )


def is_civitai_redirect_target(url: str) -> bool:
    """True when a redirect to ``url`` may be followed: HTTPS on one of
    CIVITAI_DOMAINS or a subdomain of one, matched on the parsed host name,
    default port and no credentials in the URL."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    hostname = (parts.hostname or "").lower()
    return (
        parts.scheme == "https"
        and _on_civitai_domain(hostname)
        and parts.username is None
        and parts.password is None
        and port in (None, 443)
    )


def _redirect_target(url: str, error) -> Optional[str]:
    """The URL a 3xx ``error`` for ``url`` points to, or None when absent."""
    if error.code not in _REDIRECT_CODES:
        return None
    location = (error.headers.get("Location") or "").strip()
    if not location:
        return None
    return urljoin(url, location)


def _open_following_civitai_redirects(url: str, api_key: str):
    """Open ``url``, following only allowed CivitAI redirects (keyless).

    The first request carries the key; every redirected one carries the
    User-Agent alone. Returns the open response; raises ``_DownloadFailed``
    when a redirect is refused or the hop limit is reached. Other HTTP
    errors propagate.
    """
    headers = {"User-Agent": _USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    current = url
    for hop in range(MAX_CIVITAI_REDIRECTS + 1):
        req = urllib.request.Request(current, headers=headers)
        try:
            return _open_url(req, timeout=10)
        except urllib.error.HTTPError as error:
            with error:
                target = _redirect_target(current, error)
            if target is None:
                raise
        if hop == MAX_CIVITAI_REDIRECTS:
            raise _DownloadFailed(f"more than {MAX_CIVITAI_REDIRECTS} redirects")
        if not is_civitai_redirect_target(target):
            raise _DownloadFailed(f"redirect refused to {_loggable(target)}")
        logger.debug(f"Following redirect to {_loggable(target)}")
        current = target
        headers = {"User-Agent": _USER_AGENT}  # never the key across a redirect
    raise AssertionError("unreachable")  # pragma: no cover - loop ends above


def is_civitai_url(url: str) -> bool:
    """True when ``url`` is an HTTPS URL on an allow-listed CivitAI host."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    hostname = (parts.hostname or "").lower()
    return (
        parts.scheme == "https"
        and hostname in CIVITAI_HOSTS
        and parts.username is None
        and parts.password is None
        and port in (None, 443)
    )


def _read_capped(resp, cap: int) -> Optional[bytes]:
    """Read a response body, refusing anything larger than ``cap`` bytes."""
    declared = resp.headers.get("Content-Length")
    if declared is not None:
        try:
            if int(declared) > cap:
                return None
        except ValueError:
            pass

    chunks = []
    total = 0
    while True:
        chunk = resp.read(_DOWNLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _network_reason(error: BaseException) -> str:
    """A short reason for a failed request: a timeout or a network error."""
    cause = getattr(error, "reason", error)  # URLError wraps the socket error
    if isinstance(cause, TimeoutError):
        return "timed out"
    return f"network error: {cause}"


def _fetch_civitai_body(url: str, api_key: str) -> bytes:
    """The body behind ``url`` (redirects as allowed, size capped), or
    ``_DownloadFailed`` with the reason it could not be fetched."""
    try:
        with _open_following_civitai_redirects(url, api_key) as resp:
            raw = _read_capped(resp, MAX_CIVITAI_DOWNLOAD_BYTES)
    except urllib.error.HTTPError as error:
        error.close()
        raise _DownloadFailed(f"HTTP {error.code}") from None
    except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
        raise _DownloadFailed(_network_reason(error)) from None
    if raw is None:
        cap = MAX_CIVITAI_DOWNLOAD_BYTES
        size = f"{cap // (1024 * 1024)} MB" if cap >= 1024 * 1024 else f"{cap} byte"
        raise _DownloadFailed(f"larger than the {size} cap")
    return raw


def _thumbnail(raw: bytes):
    """``raw`` decoded and shrunk to an RGB thumbnail, or ``_DownloadFailed``."""
    from io import BytesIO

    from PIL import Image

    try:
        with Image.open(BytesIO(raw)) as img:
            img.thumbnail((_THUMB_MAX_SIZE, _THUMB_MAX_SIZE), Image.LANCZOS)
            return img.convert("RGB")
    except Exception as error:  # PIL raises several types for bad data
        raise _DownloadFailed(
            f"image could not be decoded ({type(error).__name__})"
        ) from None


def _download_one(url: str, local_path: Path, api_key: str) -> Optional[str]:
    """Download a single CivitAI image, resize to thumbnail, save as JPEG.

    Refuses any URL that is not HTTPS on an allow-listed CivitAI host so the
    API key is never sent elsewhere, follows a redirect only as
    ``_open_following_civitai_redirects`` allows (without the key), and
    refuses bodies larger than MAX_CIVITAI_DOWNLOAD_BYTES. A failure logs
    one warning with a short reason and the URL without query or
    credentials (#340), and returns None.
    """
    try:
        if not is_civitai_url(url):
            raise _DownloadFailed("not an allowed CivitAI URL")
        image = _thumbnail(_fetch_civitai_body(url, api_key))
        try:
            _save_atomically(image, local_path)
        except OSError as error:
            raise _DownloadFailed(
                f"image could not be saved ({error.strerror or type(error).__name__})"
            ) from None
    except _DownloadFailed as failure:
        reason = failure.reason
    except Exception as error:  # best effort: an import never fails on one image
        reason = f"unexpected {type(error).__name__}"
    else:
        return str(local_path.resolve())
    logger.warning(f"CivitAI image download failed: {_loggable(url)}: {reason}")
    return None


_PART_PREFIX = ".pm-"
_PART_SUFFIX = ".part"
# A temp download older than this was left by a killed process, not a live one
# (each download has a 10 s timeout).
STALE_PART_SECONDS = 3600


def _save_atomically(image, local_path: Path) -> None:
    """Write ``image`` as JPEG so ``local_path`` only ever names a whole file.

    The image goes to a ``.pm-*.part`` file in the same folder, which is
    closed before ``os.replace`` puts it in place (Windows cannot replace an
    open file); a failure removes the temp file. A reader (another import's
    cache check) never sees a half-written image. The temp file is opened
    like ``img.save`` opens a file, so the image keeps the umask's mode.
    """
    import secrets

    while True:
        temp = local_path.parent / (
            f"{_PART_PREFIX}{secrets.token_hex(8)}{_PART_SUFFIX}"
        )
        try:
            handle = open(temp, "xb")
        except FileExistsError:
            continue
        break
    try:
        with handle:
            image.save(handle, "JPEG", quality=85)
        os.replace(temp, local_path)
    except BaseException:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise


def sweep_stale_downloads(cache_dir: Path) -> None:
    """Remove ``.pm-*.part`` files a killed download left in the image cache.

    Only that exact pattern, only in ``cache_dir`` and its LoRA folders, and
    only once older than ``STALE_PART_SECONDS``; a file that cannot be read
    or removed is left for a later import.
    """
    import time

    cutoff = time.time() - STALE_PART_SECONDS
    try:
        folders = [cache_dir] + [p for p in cache_dir.iterdir() if p.is_dir()]
    except OSError:
        return
    for folder in folders:
        try:
            candidates = list(folder.glob(f"{_PART_PREFIX}*{_PART_SUFFIX}"))
        except OSError:
            continue
        for path in candidates:
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    os.remove(path)
            except OSError:
                logger.debug(f"Could not remove stale download {path}")


def _civitai_cache_plan(
    metadata: Dict, metadata_path: Path, cache_dir: Path
) -> Tuple[Path, List[str], List[Tuple[str, Path]]]:
    """``(lora_cache, cached, tasks)`` for a LoRA's CivitAI example images.

    ``cached`` are the images already in the cache, ``tasks`` the
    ``(url, local_path)`` downloads still needed. Reads only; creates nothing.
    """
    civitai = _get_civitai(metadata)
    images = civitai.get("images", []) or []

    file_name = metadata.get("file_name", "")
    if not file_name:
        file_name = metadata_path.name.replace(".metadata.json", "")
    lora_cache = cache_dir / Path(file_name).stem

    cached = []
    tasks = []  # (url, local_path)
    for img in images:
        if not isinstance(img, dict):
            continue
        url = img.get("url", "")
        if not isinstance(url, str) or not is_civitai_url(url):
            continue

        url_hash = hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()[:12]
        local_path = lora_cache / f"{url_hash}.jpg"

        if local_path.exists():
            cached.append(str(local_path.resolve()))
        else:
            tasks.append((url, local_path))
    return lora_cache, cached, tasks


def planned_civitai_downloads(
    metadata: Dict, metadata_path: Path, cache_dir: Path
) -> int:
    """How many example images an import would download (no request, no write)."""
    return len(_civitai_cache_plan(metadata, metadata_path, cache_dir)[2])


def download_civitai_images(
    metadata: Dict,
    metadata_path: Path,
    cache_dir: Path,
    api_key: str = "",
    stats: Optional[Dict[str, int]] = None,
) -> List[str]:
    """Download civitai example images to a local cache directory.

    Uses thumbnail URLs (512px) instead of full-size originals, and
    downloads in parallel (up to 8 concurrent) for speed.

    Args:
        api_key: CivitAI API key for authenticated downloads (NSFW content).
        stats: When given, ``downloaded`` and ``download_errors`` are
            increased by the downloads that succeeded and failed.

    Returns:
        List of absolute paths to downloaded image files.
    """
    from concurrent.futures import ThreadPoolExecutor

    lora_cache, cached, tasks = _civitai_cache_plan(metadata, metadata_path, cache_dir)
    if not tasks:
        return cached
    lora_cache.mkdir(parents=True, exist_ok=True)

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

    if stats is not None:
        stats["downloaded"] = stats.get("downloaded", 0) + len(downloaded)
        stats["download_errors"] = (
            stats.get("download_errors", 0) + len(tasks) - len(downloaded)
        )
    return cached + downloaded


def get_lora_image_cache_dir(create: bool = True) -> Path:
    """Get the directory used to cache downloaded LoRA example images.

    ``<data folder>/data/lora_images`` (``utils.library_location``): the node
    folder for a library that was not moved, else ComfyUI's
    ``user/__promptmanager``. ``create=False`` only names it (a preview
    writes nothing).
    """
    try:
        from ..utils import library_location
    except ImportError:
        from utils import library_location

    ext_root = Path(__file__).resolve().parent.parent
    cache = Path(
        library_location.cache_dir("lora_images", extension_root=str(ext_root))
    )
    if create:
        cache.mkdir(parents=True, exist_ok=True)
    return cache


# ── Trigger word cache & injection ───────────────────────────────────

_LORA_PATTERN = re.compile(r"<lora:([^:>]+):[^>]+>", re.IGNORECASE)


# A failed load is retried by a running node only after this long (an explicit
# reload, from the settings routes, always runs).
TRIGGER_RETRY_SECONDS = 60


class TriggerWordCache:
    """Thread-safe cache mapping LoRA names to their trigger words.

    Built lazily on first access, refreshable on demand.
    """

    def __init__(self):
        self._cache: Dict[str, List[str]] = {}
        self._lock = threading.Lock()
        self._loaded = False
        self._failed_at: Optional[float] = None

    def load(self, lora_manager_path: str) -> int:
        """Build the trigger word map from the sidecars LoraManager uses.

        Never raises (a node calls this while it runs): a folder that cannot
        be listed, or any other failure, is logged and the previous map is
        kept. When LoraManager's settings cannot be used, the reason is
        logged and the default LoRA folders are read in alongside mode.

        Returns:
            Number of LoRAs with trigger words in the map.
        """
        try:
            new_cache = self._build(lora_manager_path)
        except OSError as error:
            logger.warning(f"Trigger word cache not refreshed: {error}")
            return self._failed()
        except Exception:
            # Not a disk error: keep the traceback (#362).
            logger.exception("Trigger word cache not refreshed")
            return self._failed()

        with self._lock:
            self._cache = new_cache
            self._loaded = True
            self._failed_at = None

        logger.info(
            f"Trigger word cache loaded: {len(new_cache)} LoRAs with trigger words"
        )
        return len(new_cache)

    def _failed(self) -> int:
        """Keep the previous map and note when this load failed."""
        with self._lock:
            self._failed_at = time.monotonic()
            return len(self._cache)

    @staticmethod
    def _build(lora_manager_path: str) -> Dict[str, List[str]]:
        layout = lora_manager_layout(lora_manager_path)
        if layout.error is not None:
            logger.warning(
                f"LoraManager settings not usable ({layout.error}); trigger words "
                "come from the default LoRA folders"
            )
            layout = replace(
                layout,
                error=None,
                mode=MODE_ALONGSIDE,
                sidecar_root=None,
                example_images_root=None,
            )
        new_cache: Dict[str, List[str]] = {}
        for sidecar in discover_sidecars(layout).sidecars:
            metadata = read_lora_metadata(sidecar.metadata_path)
            if not metadata:
                continue
            words = get_trigger_words_from_metadata(metadata)
            if not words:
                continue
            # Key by the model file name (what appears in <lora:NAME:weight>)
            # and by the sidecar's name.
            new_cache[sidecar.model_path.stem.lower()] = words
            sidecar_stem = sidecar.metadata_path.name[: -len(METADATA_SUFFIX)]
            new_cache.setdefault(sidecar_stem.lower(), words)
        return new_cache

    def get_trigger_words(self, lora_name: str) -> List[str]:
        """Look up trigger words for a LoRA by name (case-insensitive)."""
        with self._lock:
            return self._cache.get(lora_name.lower(), [])

    def retry_due(self) -> bool:
        """Whether a node may try loading again: never failed, or the last
        failure is TRIGGER_RETRY_SECONDS old (no rescan on every prompt run)."""
        with self._lock:
            failed_at = self._failed_at
        return failed_at is None or time.monotonic() - failed_at >= (
            TRIGGER_RETRY_SECONDS
        )

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return self._loaded

    def clear(self):
        with self._lock:
            self._cache.clear()
            self._loaded = False
            self._failed_at = None


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
