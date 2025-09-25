"""Compatibility shim re-exporting core logging configuration."""

from __future__ import annotations

import sys

from .core.logging_config import (  # nosec B1
    MemoryBufferHandler,
    PromptManagerLogger,
    get_logger,
    get_logger_manager,
    setup_logging,
)

module = sys.modules.setdefault(__name__, sys.modules[__name__])
sys.modules.setdefault("promptmanager.utils.logging_config", module)
sys.modules.setdefault("utils.logging_config", module)
sys.modules.setdefault("custom_nodes.promptmanager.utils.logging_config", module)

__all__ = [
    "PromptManagerLogger",
    "MemoryBufferHandler",
    "get_logger",
    "get_logger_manager",
    "setup_logging",
]
