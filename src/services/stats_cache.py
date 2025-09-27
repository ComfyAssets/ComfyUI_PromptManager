"""
Enhanced Statistics Cache Service
Provides multi-level caching with Redis support and background refresh.
"""

import time
import json
import pickle
import hashlib
from typing import Dict, Any, Optional, Callable
from functools import wraps
from threading import Lock, Thread
from collections import OrderedDict
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class CacheEntry:
    """Single cache entry with TTL and metadata."""

    def __init__(self, key: str, data: Any, ttl: int = 300):
        self.key = key
        self.data = data
        self.ttl = ttl
        self.created_at = time.time()
        self.access_count = 0
        self.last_accessed = time.time()

    def is_valid(self) -> bool:
        """Check if cache entry is still valid."""
        return time.time() - self.created_at < self.ttl

    def access(self) -> Any:
        """Access the cache entry and update metadata."""
        self.access_count += 1
        self.last_accessed = time.time()
        return self.data


class MultiLevelCache:
    """
    Multi-level cache implementation with memory and optional disk/Redis backing.
    """

    def __init__(self, max_memory_items: int = 100, disk_cache_path: Optional[str] = None):
        self.memory_cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.max_memory_items = max_memory_items
        self.disk_cache_path = disk_cache_path
        self.lock = Lock()
        self.stats = {
            'hits': 0,
            'misses': 0,
            'evictions': 0,
            'memory_usage': 0
        }

    def get(self, key: str) -> Optional[Any]:
        """Get item from cache."""
        with self.lock:
            # Check memory cache
            if key in self.memory_cache:
                entry = self.memory_cache[key]
                if entry.is_valid():
                    self.stats['hits'] += 1
                    # Move to end (LRU)
                    self.memory_cache.move_to_end(key)
                    return entry.access()
                else:
                    # Remove expired entry
                    del self.memory_cache[key]

            # Check disk cache if available
            if self.disk_cache_path:
                data = self._load_from_disk(key)
                if data is not None:
                    self.stats['hits'] += 1
                    # Promote to memory cache
                    self._add_to_memory(key, data)
                    return data

            self.stats['misses'] += 1
            return None

    def set(self, key: str, data: Any, ttl: int = 300):
        """Set item in cache."""
        with self.lock:
            # Evict if necessary
            if len(self.memory_cache) >= self.max_memory_items:
                self._evict_lru()

            # Add to memory cache
            self.memory_cache[key] = CacheEntry(key, data, ttl)

            # Also save to disk if configured
            if self.disk_cache_path:
                self._save_to_disk(key, data, ttl)

    def invalidate(self, key: Optional[str] = None):
        """Invalidate cache entries."""
        with self.lock:
            if key:
                self.memory_cache.pop(key, None)
                if self.disk_cache_path:
                    self._delete_from_disk(key)
            else:
                self.memory_cache.clear()
                if self.disk_cache_path:
                    self._clear_disk_cache()

    def _evict_lru(self):
        """Evict least recently used item."""
        if self.memory_cache:
            # Remove first item (oldest)
            self.memory_cache.popitem(last=False)
            self.stats['evictions'] += 1

    def _add_to_memory(self, key: str, data: Any, ttl: int = 300):
        """Add item to memory cache."""
        if len(self.memory_cache) >= self.max_memory_items:
            self._evict_lru()
        self.memory_cache[key] = CacheEntry(key, data, ttl)

    def _load_from_disk(self, key: str) -> Optional[Any]:
        """Load cached data from disk."""
        if not self.disk_cache_path:
            return None

        try:
            cache_file = f"{self.disk_cache_path}/{hashlib.md5(key.encode()).hexdigest()}.cache"
            with open(cache_file, 'rb') as f:
                entry = pickle.load(f)
                if entry['expires'] > time.time():
                    return entry['data']
        except Exception:
            return None

    def _save_to_disk(self, key: str, data: Any, ttl: int):
        """Save data to disk cache."""
        if not self.disk_cache_path:
            return

        try:
            import os
            os.makedirs(self.disk_cache_path, exist_ok=True)
            cache_file = f"{self.disk_cache_path}/{hashlib.md5(key.encode()).hexdigest()}.cache"
            entry = {
                'data': data,
                'expires': time.time() + ttl
            }
            with open(cache_file, 'wb') as f:
                pickle.dump(entry, f)
        except Exception as e:
            logger.warning(f"Failed to save to disk cache: {e}")

    def _delete_from_disk(self, key: str):
        """Delete item from disk cache."""
        if not self.disk_cache_path:
            return

        try:
            import os
            cache_file = f"{self.disk_cache_path}/{hashlib.md5(key.encode()).hexdigest()}.cache"
            if os.path.exists(cache_file):
                os.remove(cache_file)
        except Exception:
            pass

    def _clear_disk_cache(self):
        """Clear all disk cache files."""
        if not self.disk_cache_path:
            return

        try:
            import os
            for file in os.listdir(self.disk_cache_path):
                if file.endswith('.cache'):
                    os.remove(os.path.join(self.disk_cache_path, file))
        except Exception:
            pass

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self.lock:
            total_requests = self.stats['hits'] + self.stats['misses']
            hit_rate = self.stats['hits'] / max(total_requests, 1)

            return {
                'hits': self.stats['hits'],
                'misses': self.stats['misses'],
                'evictions': self.stats['evictions'],
                'hit_rate': hit_rate,
                'memory_items': len(self.memory_cache),
                'memory_size': sum(
                    len(str(entry.data)) for entry in self.memory_cache.values()
                )
            }


class StatsCache:
    """
    Specialized cache for statistics with background refresh.
    """

    def __init__(self, compute_func: Callable, ttl: int = 300):
        self.compute_func = compute_func
        self.ttl = ttl
        self.cache = MultiLevelCache(max_memory_items=50)
        self.background_thread = None
        self.stop_background = False

    def get(self, key: str = 'default', force_refresh: bool = False) -> Any:
        """Get cached stats or compute if needed."""
        if not force_refresh:
            data = self.cache.get(key)
            if data is not None:
                return data

        # Compute new data
        data = self.compute_func(key)
        self.cache.set(key, data, self.ttl)
        return data

    def start_background_refresh(self, interval: int = 240):
        """Start background refresh thread."""
        if self.background_thread and self.background_thread.is_alive():
            return

        self.stop_background = False

        def refresh_worker():
            while not self.stop_background:
                try:
                    # Refresh main stats
                    self.get('overview', force_refresh=True)
                    logger.info("Background stats refresh completed")
                except Exception as e:
                    logger.error(f"Background refresh failed: {e}")

                # Sleep in small increments for responsive shutdown
                for _ in range(interval):
                    if self.stop_background:
                        break
                    time.sleep(1)

        self.background_thread = Thread(target=refresh_worker, daemon=True)
        self.background_thread.start()
        logger.info(f"Background refresh started (interval: {interval}s)")

    def stop_background_refresh(self):
        """Stop background refresh thread."""
        self.stop_background = True
        if self.background_thread:
            self.background_thread.join(timeout=5)
            logger.info("Background refresh stopped")

    def invalidate(self, key: Optional[str] = None):
        """Invalidate cache entries."""
        self.cache.invalidate(key)

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return self.cache.get_stats()


def cached_stats(ttl: int = 300):
    """
    Decorator for caching stats methods.
    """
    cache = {}
    lock = Lock()

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            cache_key = f"{func.__name__}:{str(args)}:{str(kwargs)}"

            with lock:
                # Check cache
                if cache_key in cache:
                    entry, timestamp = cache[cache_key]
                    if time.time() - timestamp < ttl:
                        return entry

                # Compute and cache
                result = func(*args, **kwargs)
                cache[cache_key] = (result, time.time())
                return result

        # Add cache control methods
        wrapper.invalidate = lambda: cache.clear()
        wrapper.cache_info = lambda: {'size': len(cache), 'keys': list(cache.keys())}

        return wrapper

    return decorator


# Singleton instances
_stats_cache = None


def get_stats_cache() -> StatsCache:
    """Get singleton stats cache instance."""
    global _stats_cache
    if _stats_cache is None:
        from src.services.stats_service import StatsService
        service = StatsService('/path/to/db')  # Will be configured
        _stats_cache = StatsCache(
            compute_func=lambda key: service.get_overview(force=True),
            ttl=300
        )
        # Start background refresh
        _stats_cache.start_background_refresh()
    return _stats_cache