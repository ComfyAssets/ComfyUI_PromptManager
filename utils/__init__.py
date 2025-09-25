"""Utility package exposing PromptManager helper modules.

Exposes commonly used helpers lazily to avoid import cycles during the
ComfyUI startup sequence.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Dict

__all__ = [
    "generate_prompt_hash",
    "validate_prompt_text",
    "validate_rating",
    "validate_tags",
    "get_logger",
    "setup_logging",
    "load_settings",
    "save_settings",
    "get_setting",
    "set_setting",
    "DuplicateDetector",
    "MetadataExtractor",
]

_LAZY_IMPORTS: Dict[str, tuple[str, str]] = {
    "generate_prompt_hash": (".validation.hashing", "generate_prompt_hash"),
    "validate_prompt_text": (".validation.validators", "validate_prompt_text"),
    "validate_rating": (".validation.validators", "validate_rating"),
    "validate_tags": (".validation.validators", "validate_tags"),
    "get_logger": ("..loggers", "get_logger"),
    "setup_logging": ("..loggers", "setup_logging"),
    "load_settings": (".settings", "load_settings"),
    "save_settings": (".settings", "save_settings"),
    "get_setting": (".settings", "get_setting"),
    "set_setting": (".settings", "set_setting"),
    "DuplicateDetector": (".duplicate_detector", "DuplicateDetector"),
    "MetadataExtractor": (".metadata_extractor", "MetadataExtractor"),
}


def __getattr__(name: str) -> Any:
    if name not in _LAZY_IMPORTS:
        raise AttributeError(f"module 'utils' has no attribute '{name}'")

    module_path, attr_name = _LAZY_IMPORTS[name]
    module = import_module(module_path, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__))
