"""
Singleton tracker to prevent duplicate initialization across module reloads.

This module provides a cross-module singleton that survives reloads and
different import paths.
"""
import sys
import threading

# Global lock for thread-safe initialization
_init_lock = threading.Lock()

# Store initialization state in a separate module that won't be reloaded
_SINGLETON_STORAGE_KEY = '__promptmanager_tracking_singleton__'

class SingletonTracker:
    """Singleton tracker to prevent duplicate initialization."""
    
    @staticmethod
    def _get_storage():
        """Get or create the singleton storage module."""
        if _SINGLETON_STORAGE_KEY not in sys.modules:
            # Create a fake module to store our singleton data
            import types
            storage_module = types.ModuleType(_SINGLETON_STORAGE_KEY)
            storage_module.initialized = False
            storage_module.tracker = None
            storage_module.patcher = None
            sys.modules[_SINGLETON_STORAGE_KEY] = storage_module
        return sys.modules[_SINGLETON_STORAGE_KEY]
    
    @staticmethod
    def is_initialized():
        """Check if tracking has been initialized."""
        storage = SingletonTracker._get_storage()
        return getattr(storage, 'initialized', False)
    
    @staticmethod
    def mark_initialized():
        """Mark tracking as initialized."""
        with _init_lock:
            storage = SingletonTracker._get_storage()
            storage.initialized = True
    
    @staticmethod
    def get_tracker():
        """Get the singleton tracker instance."""
        with _init_lock:
            storage = SingletonTracker._get_storage()
            return getattr(storage, 'tracker', None)
    
    @staticmethod
    def set_tracker(tracker):
        """Set the singleton tracker instance."""
        with _init_lock:
            storage = SingletonTracker._get_storage()
            storage.tracker = tracker
    
    @staticmethod
    def get_patcher():
        """Get the singleton patcher instance."""
        with _init_lock:
            storage = SingletonTracker._get_storage()
            return getattr(storage, 'patcher', None)
    
    @staticmethod
    def set_patcher(patcher):
        """Set the singleton patcher instance."""
        with _init_lock:
            storage = SingletonTracker._get_storage()
            storage.patcher = patcher