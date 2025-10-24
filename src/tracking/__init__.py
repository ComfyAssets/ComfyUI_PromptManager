"""Robust prompt-to-image tracking system.

This module implements ComfyUI pipeline integration for accurate
prompt-to-image association, replacing the fragile file watcher approach.
"""

# Always available - no torch dependency
from .pending_registry import PendingPromptRegistry, PendingPrompt

# Requires torch - import with error handling
try:
    from .prompt_tracker import PromptTracker, TrackingData
    from .graph_analyzer import GraphAnalyzer
    from .save_image_patcher import SaveImagePatcher
    from .singleton_tracker import SingletonTracker
except ImportError:
    PromptTracker = None
    TrackingData = None
    GraphAnalyzer = None
    SaveImagePatcher = None
    SingletonTracker = None

__all__ = [
    "PendingPromptRegistry",
    "PendingPrompt",
    "PromptTracker",
    "TrackingData",
    "GraphAnalyzer", 
    "SaveImagePatcher",
    "SingletonTracker",
]