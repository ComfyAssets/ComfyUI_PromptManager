"""Unified logging accessor for PromptManager.

This module centralizes access to the logging configuration so runtime
environments with competing ``utils`` packages still resolve our
implementation without noisy fallbacks.
"""

from __future__ import annotations

import importlib
import logging
import sys
from types import ModuleType
from typing import Any, Iterable, Tuple

module = sys.modules.setdefault(__name__, sys.modules[__name__])
sys.modules.setdefault("promptmanager.loggers", module)
sys.modules.setdefault("loggers", module)
sys.modules.setdefault("custom_nodes.promptmanager.loggers", module)

_LOGGING_MODULE_CANDIDATES: Tuple[str, ...] = (
    "promptmanager.utils.core.logging_config",
    "promptmanager.utils.logging_config",
    "promptmanager.utils.logging",
    "utils.core.logging_config",
    "utils.logging_config",
)

_fallback_module: ModuleType | None = None


def _load_logging_module(candidates: Iterable[str] = _LOGGING_MODULE_CANDIDATES) -> ModuleType:
    """Return the first available logging configuration module."""
    for dotted_path in candidates:
        try:
            module = importlib.import_module(dotted_path)
        except Exception:  # noqa: BLE001 - continue to next candidate
            continue
        else:
            _register_aliases(module, dotted_path)
            return module

    module = _get_fallback_module()
    _register_aliases(module, "promptmanager.utils.core.logging_config")
    _register_aliases(module, "utils.core.logging_config")
    return module


def _register_aliases(module: ModuleType, dotted_path: str) -> None:
    """Expose the resolved module under legacy dotted paths."""
    sys.modules.setdefault(dotted_path, module)

    if dotted_path.endswith("logging_config"):
        # Ensure the legacy path resolves, even if another custom node installed a non-package ``utils``.
        sys.modules.setdefault("promptmanager.utils.core.logging_config", module)
        sys.modules.setdefault("utils.core.logging_config", module)


def _get_fallback_module() -> ModuleType:
    global _fallback_module
    if _fallback_module is not None:
        return _fallback_module

    fallback = ModuleType("promptmanager._fallback_logging")

    class _FallbackLoggerManager:
        """Fallback logger that ONLY configures promptmanager logger, never root logger.

        CRITICAL: Never use logging.basicConfig() or touch the root logger!
        This will break ComfyUI's logging system.
        """
        def __init__(self) -> None:
            self._configured = False
            self.config = {
                "level": "INFO",
                "console_logging": True,
                "file_logging": False,
            }

        def setup(self, level: str | None = None) -> None:
            """Setup OUR logger only, never the root logger."""
            if level:
                self.config["level"] = level
            if self._configured:
                return

            # CRITICAL: Configure ONLY our logger, not root!
            pm_logger = logging.getLogger("promptmanager")
            pm_logger.setLevel(getattr(logging, self.config["level"], logging.INFO))
            pm_logger.propagate = True  # Let ComfyUI's root logger handle output

            # Only add handler if console logging is enabled AND we don't have one
            if self.config["console_logging"] and not pm_logger.handlers:
                handler = logging.StreamHandler()
                handler.setFormatter(logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    "%Y-%m-%d %H:%M:%S"
                ))
                pm_logger.addHandler(handler)

            self._configured = True

        def get_logger(self, name: str = "promptmanager"):
            """Get a logger under the promptmanager hierarchy."""
            self.setup()
            return logging.getLogger(name)

    manager = _FallbackLoggerManager()

    def setup_logging(level: str | None = None, *args: Any, **kwargs: Any):
        manager.setup(level)
        return manager.get_logger()

    fallback.setup_logging = setup_logging  # type: ignore[attr-defined]
    fallback.get_logger = manager.get_logger  # type: ignore[attr-defined]
    fallback.get_logger_manager = lambda: manager  # type: ignore[attr-defined]
    fallback.PromptManagerLogger = _FallbackLoggerManager  # type: ignore[attr-defined]

    _fallback_module = fallback
    return fallback


def get_logger(name: str = "prompt_manager"):
    """Proxy to the concrete ``get_logger`` implementation."""
    module = _load_logging_module()
    if hasattr(module, "get_logger"):
        return module.get_logger(name)
    raise AttributeError("logging module lacks get_logger()")


def get_logger_manager() -> Any:
    module = _load_logging_module()
    if hasattr(module, "get_logger_manager"):
        return module.get_logger_manager()
    raise AttributeError("logging module lacks get_logger_manager()")


def setup_logging(*args: Any, **kwargs: Any) -> Any:
    module = _load_logging_module()
    if hasattr(module, "setup_logging"):
        return module.setup_logging(*args, **kwargs)
    raise AttributeError("logging module lacks setup_logging()")
