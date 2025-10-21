"""File system utilities - re-exports from utils.core.file_system.

This module provides a compatibility layer for imports from src.utils.file_system.
"""

from utils.core.file_system import get_file_system, ComfyUIFileSystem

__all__ = ['get_file_system', 'ComfyUIFileSystem']
