"""Lightweight settings loader/saver for PromptManager.

Reads and writes a JSON settings file in the PromptManager user directory.
Provides simple helpers and sensible fallbacks when ComfyUI helpers are unavailable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

try:
    from .logging import get_logger
except Exception:  # Fallback minimal logger
    import logging

    def get_logger(name: str):
        return logging.getLogger(name)

logger = get_logger("promptmanager.settings")


def _default_settings_path() -> Path:
    """Compute a default settings path under the user's ComfyUI directory."""
    # Try ComfyUI folder_paths if available
    try:
        import folder_paths  # type: ignore
        user_dir = Path(folder_paths.get_user_directory())
        return user_dir / "default" / "PromptManager" / "settings.json"
    except Exception:
        # Fallback to home dir
        base = Path(os.path.expanduser("~")) / ".comfyui" / "promptmanager"
        base.mkdir(parents=True, exist_ok=True)
        return base / "settings.json"


def _ensure_parent(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def load_settings() -> Dict[str, Any]:
    """Load settings JSON. Returns empty dict if missing/corrupt."""
    path = _default_settings_path()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning(f"Failed to load settings: {e}")
    return {}


def save_settings(settings: Dict[str, Any]) -> bool:
    """Persist settings JSON."""
    path = _default_settings_path()
    try:
        _ensure_parent(path)
        path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Settings saved to {path}")
        return True
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        return False


def get_setting(key: str, default: Any = None) -> Any:
    """Get a flat key from the settings dict."""
    return load_settings().get(key, default)


def set_setting(key: str, value: Any) -> bool:
    """Set a flat key and save settings."""
    data = load_settings()
    data[key] = value
    return save_settings(data)
