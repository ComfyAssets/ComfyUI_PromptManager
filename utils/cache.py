"""Cache management utilities for PromptManager.

Provides multi-layer caching with memory and disk backends, TTL management,
cache invalidation, statistics, and cache warming capabilities.
"""

import asyncio
import hashlib
import json
import pickle
import sqlite3
import threading
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import weakref
import gzip
import sys

from .logging import get_logger
from .file_ops import AtomicWriter, DirectoryManager, FileCleanup
from .validation.hashing import CacheKeyGenerator

logger = get_logger("promptmanager.cache")


class CacheStrategy(Enum):
    """Cache eviction strategies."""
    LRU = "lru"  # Least Recently Used
    LFU = "lfu"  # Least Frequently Used
    FIFO = "fifo"  # First In First Out
    TTL = "ttl"  # Time To Live based
    SIZE = "size"  # Size-based eviction


class CacheLevel(Enum):
    """Cache storage levels."""
    MEMORY = "memory"
    DISK = "disk"
    REDIS = "redis"  # Future extension
    DISTRIBUTED = "distributed"  # Future extension


@dataclass
class CacheEntry:
    """Container for cached data."""
    key: str
    value: Any
    size: int
    created_at: float
    accessed_at: float
    access_count: int = 0
    ttl: Optional[float] = None
    tags: Set[str] = field(default_factory=set)
    
    def is_expired(self) -> bool:
        """Check if entry has expired."""
        if self.ttl is None:
            return False
        return time.time() > self.created_at + self.ttl
    
    def touch(self):
        """Update access time and count."""
        self.accessed_at = time.time()
        self.access_count += 1


@dataclass
class CacheStats:
    """Cache performance statistics."""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expired: int = 0
    size_bytes: int = 0
    entry_count: int = 0
    
    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'hits': self.hits,
            'misses': self.misses,
            'hit_rate': f"{self.hit_rate:.2%}",
            'evictions': self.evictions,
            'expired': self.expired,
            'size_bytes': self.size_bytes,
            'size_mb': f"{self.size_bytes / 1024 / 1024:.2f}",
            'entry_count': self.entry_count
        }


class MemoryCache:
    """In-memory cache with configurable eviction strategy."""
    
    def __init__(
        self,
        max_size: int = 100 * 1024 * 1024,  # 100MB
        max_entries: int = 10000,
        strategy: CacheStrategy = CacheStrategy.LRU,
        default_ttl: Optional[float] = None
    ):
        """Initialize memory cache.
        
        Args:
            max_size: Maximum cache size in bytes
            max_entries: Maximum number of entries
            strategy: Eviction strategy
            default_ttl: Default TTL in seconds
        """
        self.max_size = max_size
        self.max_entries = max_entries
        self.strategy = strategy
        self.default_ttl = default_ttl
        
        # Thread-safe cache storage
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._stats = CacheStats()
        
        # Strategy-specific data structures
        if strategy == CacheStrategy.LFU:
            self._frequency: Dict[str, int] = defaultdict(int)
        
        # Weak references for memory pressure handling
        self._weak_refs: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get value from cache.
        
        Args:
            key: Cache key
            default: Default value if not found
            
        Returns:
            Cached value or default
        """
        with self._lock:
            entry = self._cache.get(key)
            
            if entry is None:
                self._stats.misses += 1
                return default
            
            # Check expiration
            if entry.is_expired():
                self._remove_entry(key)
                self._stats.expired += 1
                self._stats.misses += 1
                return default
            
            # Update access info
            entry.touch()
            
            # Move to end for LRU
            if self.strategy == CacheStrategy.LRU:
                self._cache.move_to_end(key)
            
            # Update frequency for LFU
            elif self.strategy == CacheStrategy.LFU:
                self._frequency[key] += 1
            
            self._stats.hits += 1
            return entry.value
    
    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[float] = None,
        tags: Optional[Set[str]] = None
    ) -> bool:
        """Set value in cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds
            tags: Tags for group invalidation
            
        Returns:
            True if cached successfully
        """
        # Calculate size
        try:
            size = sys.getsizeof(value)
        except:
            # Fallback for complex objects
            size = len(pickle.dumps(value))
        
        # Check if value is too large
        if size > self.max_size:
            logger.warning(f"Value too large for cache: {size} bytes")
            return False
        
        with self._lock:
            # Remove existing entry if present
            if key in self._cache:
                self._remove_entry(key)
            
            # Make room if needed
            self._evict_if_needed(size)
            
            # Create new entry
            entry = CacheEntry(
                key=key,
                value=value,
                size=size,
                created_at=time.time(),
                accessed_at=time.time(),
                ttl=ttl or self.default_ttl,
                tags=tags or set()
            )
            
            # Add to cache
            self._cache[key] = entry
            self._stats.size_bytes += size
            self._stats.entry_count += 1
            
            # Update strategy-specific structures
            if self.strategy == CacheStrategy.LFU:
                self._frequency[key] = 1
            
            # Store weak reference for memory pressure
            try:
                self._weak_refs[key] = value
            except TypeError:
                # Some objects can't be weakly referenced
                pass
            
            return True
    
    def delete(self, key: str) -> bool:
        """Delete entry from cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if deleted
        """
        with self._lock:
            if key in self._cache:
                self._remove_entry(key)
                return True
            return False
    
    def clear(self):
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()
            if hasattr(self, '_frequency'):
                self._frequency.clear()
            self._weak_refs.clear()
            self._stats.size_bytes = 0
            self._stats.entry_count = 0
            logger.info("Cache cleared")
    
    def invalidate_by_tags(self, tags: Set[str]) -> int:
        """Invalidate entries with matching tags.
        
        Args:
            tags: Tags to match
            
        Returns:
            Number of invalidated entries
        """
        count = 0
        with self._lock:
            keys_to_remove = []
            
            for key, entry in self._cache.items():
                if entry.tags & tags:  # Intersection
                    keys_to_remove.append(key)
            
            for key in keys_to_remove:
                self._remove_entry(key)
                count += 1
        
        if count:
            logger.info(f"Invalidated {count} entries with tags {tags}")
        
        return count
    
    def get_stats(self) -> CacheStats:
        """Get cache statistics.
        
        Returns:
            Cache statistics
        """
        return self._stats
    
    def _evict_if_needed(self, size_needed: int):
        """Evict entries if cache is full.
        
        Args:
            size_needed: Size needed for new entry
        """
        # Check entry count limit
        while self._stats.entry_count >= self.max_entries:
            self._evict_one()
        
        # Check size limit
        while self._stats.size_bytes + size_needed > self.max_size:
            if not self._evict_one():
                break
    
    def _evict_one(self) -> bool:
        """Evict one entry based on strategy.
        
        Returns:
            True if evicted
        """
        if not self._cache:
            return False
        
        key_to_evict = None
        
        if self.strategy == CacheStrategy.LRU:
            # Remove least recently used (first item)
            key_to_evict = next(iter(self._cache))
        
        elif self.strategy == CacheStrategy.LFU:
            # Remove least frequently used
            if self._frequency:
                key_to_evict = min(self._frequency, key=self._frequency.get)
        
        elif self.strategy == CacheStrategy.FIFO:
            # Remove oldest (first item)
            key_to_evict = next(iter(self._cache))
        
        elif self.strategy == CacheStrategy.TTL:
            # Remove expired or oldest
            for key, entry in self._cache.items():
                if entry.is_expired():
                    key_to_evict = key
                    break
            if not key_to_evict:
                key_to_evict = next(iter(self._cache))
        
        elif self.strategy == CacheStrategy.SIZE:
            # Remove largest entry
            key_to_evict = max(
                self._cache.keys(),
                key=lambda k: self._cache[k].size
            )
        
        if key_to_evict:
            self._remove_entry(key_to_evict)
            self._stats.evictions += 1
            return True
        
        return False
    
    def _remove_entry(self, key: str):
        """Remove entry from cache.
        
        Args:
            key: Cache key
        """
        if key in self._cache:
            entry = self._cache[key]
            self._stats.size_bytes -= entry.size
            self._stats.entry_count -= 1
            del self._cache[key]
            
            if hasattr(self, '_frequency') and key in self._frequency:
                del self._frequency[key]
            
            if key in self._weak_refs:
                del self._weak_refs[key]


class DiskCache:
    """Persistent disk-based cache using SQLite."""
    
    def __init__(
        self,
        cache_dir: Union[str, Path] = None,
        max_size: int = 1024 * 1024 * 1024,  # 1GB
        default_ttl: Optional[float] = None,
        compress: bool = True
    ):
        """Initialize disk cache.
        
        Args:
            cache_dir: Cache directory
            max_size: Maximum cache size in bytes
            default_ttl: Default TTL in seconds
            compress: Whether to compress cached data
        """
        self.cache_dir = Path(cache_dir) if cache_dir else Path.cwd() / '.cache'
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.compress = compress
        
        # Create cache directory
        DirectoryManager.create_directory(self.cache_dir)
        
        # Initialize database
        self.db_path = self.cache_dir / 'cache.db'
        self._init_database()
        
        # Stats
        self._stats = CacheStats()
        self._load_stats()
    
    def _init_database(self):
        """Initialize SQLite database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value BLOB,
                    size INTEGER,
                    created_at REAL,
                    accessed_at REAL,
                    access_count INTEGER DEFAULT 0,
                    ttl REAL,
                    tags TEXT
                )
            ''')
            
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_accessed_at 
                ON cache(accessed_at)
            ''')
            
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_tags 
                ON cache(tags)
            ''')
            
            conn.commit()
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get value from cache.
        
        Args:
            key: Cache key
            default: Default value if not found
            
        Returns:
            Cached value or default
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                'SELECT value, created_at, ttl FROM cache WHERE key = ?',
                (key,)
            )
            row = cursor.fetchone()
            
            if row is None:
                self._stats.misses += 1
                return default
            
            value_blob, created_at, ttl = row
            
            # Check expiration
            if ttl and time.time() > created_at + ttl:
                conn.execute('DELETE FROM cache WHERE key = ?', (key,))
                conn.commit()
                self._stats.expired += 1
                self._stats.misses += 1
                return default
            
            # Update access info
            conn.execute(
                '''UPDATE cache 
                   SET accessed_at = ?, access_count = access_count + 1 
                   WHERE key = ?''',
                (time.time(), key)
            )
            conn.commit()
            
            # Deserialize value
            try:
                if self.compress:
                    value_blob = gzip.decompress(value_blob)
                value = pickle.loads(value_blob)
                self._stats.hits += 1
                return value
            except Exception as e:
                logger.error(f"Failed to deserialize cache value: {e}")
                self._stats.misses += 1
                return default
    
    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[float] = None,
        tags: Optional[Set[str]] = None
    ) -> bool:
        """Set value in cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds
            tags: Tags for group invalidation
            
        Returns:
            True if cached successfully
        """
        try:
            # Serialize value
            value_blob = pickle.dumps(value)
            
            if self.compress:
                value_blob = gzip.compress(value_blob)
            
            size = len(value_blob)
            
            # Check size limit
            if size > self.max_size:
                logger.warning(f"Value too large for disk cache: {size} bytes")
                return False
            
            # Make room if needed
            self._evict_if_needed(size)
            
            # Store in database
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    '''INSERT OR REPLACE INTO cache 
                       (key, value, size, created_at, accessed_at, ttl, tags)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (
                        key,
                        value_blob,
                        size,
                        time.time(),
                        time.time(),
                        ttl or self.default_ttl,
                        json.dumps(list(tags)) if tags else '[]'
                    )
                )
                conn.commit()
            
            self._update_stats()
            return True
            
        except Exception as e:
            logger.error(f"Failed to cache value: {e}")
            return False
    
    def delete(self, key: str) -> bool:
        """Delete entry from cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if deleted
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('DELETE FROM cache WHERE key = ?', (key,))
            deleted = cursor.rowcount > 0
            conn.commit()
        
        if deleted:
            self._update_stats()
        
        return deleted
    
    def clear(self):
        """Clear all cache entries."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('DELETE FROM cache')
            conn.commit()
        
        self._update_stats()
        logger.info("Disk cache cleared")
    
    def invalidate_by_tags(self, tags: Set[str]) -> int:
        """Invalidate entries with matching tags.
        
        Args:
            tags: Tags to match
            
        Returns:
            Number of invalidated entries
        """
        count = 0
        
        with sqlite3.connect(self.db_path) as conn:
            # Find entries with matching tags
            cursor = conn.execute('SELECT key, tags FROM cache')
            
            keys_to_delete = []
            for key, tags_json in cursor:
                entry_tags = set(json.loads(tags_json))
                if entry_tags & tags:
                    keys_to_delete.append(key)
            
            # Delete matching entries
            for key in keys_to_delete:
                conn.execute('DELETE FROM cache WHERE key = ?', (key,))
                count += 1
            
            conn.commit()
        
        if count:
            self._update_stats()
            logger.info(f"Invalidated {count} disk cache entries with tags {tags}")
        
        return count
    
    def cleanup_expired(self) -> int:
        """Remove expired entries.
        
        Returns:
            Number of removed entries
        """
        current_time = time.time()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                '''DELETE FROM cache 
                   WHERE ttl IS NOT NULL 
                   AND created_at + ttl < ?''',
                (current_time,)
            )
            removed = cursor.rowcount
            conn.commit()
        
        if removed:
            self._update_stats()
            logger.info(f"Removed {removed} expired entries from disk cache")
        
        return removed
    
    def get_stats(self) -> CacheStats:
        """Get cache statistics.
        
        Returns:
            Cache statistics
        """
        return self._stats
    
    def _evict_if_needed(self, size_needed: int):
        """Evict entries if cache is full.
        
        Args:
            size_needed: Size needed for new entry
        """
        with sqlite3.connect(self.db_path) as conn:
            # Get current total size
            cursor = conn.execute('SELECT SUM(size) FROM cache')
            total_size = cursor.fetchone()[0] or 0
            
            if total_size + size_needed <= self.max_size:
                return
            
            # Evict least recently accessed entries
            space_to_free = total_size + size_needed - self.max_size
            freed = 0
            
            cursor = conn.execute(
                'SELECT key, size FROM cache ORDER BY accessed_at ASC'
            )
            
            for key, size in cursor:
                conn.execute('DELETE FROM cache WHERE key = ?', (key,))
                freed += size
                self._stats.evictions += 1
                
                if freed >= space_to_free:
                    break
            
            conn.commit()
    
    def _update_stats(self):
        """Update cache statistics from database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                'SELECT COUNT(*), SUM(size) FROM cache'
            )
            count, total_size = cursor.fetchone()
            
            self._stats.entry_count = count or 0
            self._stats.size_bytes = total_size or 0
    
    def _load_stats(self):
        """Load initial statistics."""
        self._update_stats()


class MultiLevelCache:
    """Multi-level cache with memory and disk layers."""
    
    def __init__(
        self,
        memory_size: int = 50 * 1024 * 1024,  # 50MB
        disk_size: int = 500 * 1024 * 1024,  # 500MB
        cache_dir: Union[str, Path] = None,
        default_ttl: Optional[float] = None
    ):
        """Initialize multi-level cache.
        
        Args:
            memory_size: Memory cache size
            disk_size: Disk cache size
            cache_dir: Cache directory
            default_ttl: Default TTL
        """
        self.memory = MemoryCache(
            max_size=memory_size,
            strategy=CacheStrategy.LRU,
            default_ttl=default_ttl
        )
        
        self.disk = DiskCache(
            cache_dir=cache_dir,
            max_size=disk_size,
            default_ttl=default_ttl
        )
        
        # Cache warming queue
        self._warm_queue: asyncio.Queue = None
        self._warm_task: Optional[asyncio.Task] = None
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get value from cache (memory first, then disk).
        
        Args:
            key: Cache key
            default: Default value
            
        Returns:
            Cached value or default
        """
        # Try memory first
        value = self.memory.get(key)
        if value is not None:
            return value
        
        # Try disk
        value = self.disk.get(key)
        if value is not None:
            # Promote to memory
            self.memory.set(key, value)
            return value
        
        return default
    
    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[float] = None,
        tags: Optional[Set[str]] = None,
        levels: Optional[List[CacheLevel]] = None
    ) -> bool:
        """Set value in cache levels.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live
            tags: Tags for invalidation
            levels: Specific levels to cache in
            
        Returns:
            True if cached successfully
        """
        if levels is None:
            levels = [CacheLevel.MEMORY, CacheLevel.DISK]
        
        success = True
        
        if CacheLevel.MEMORY in levels:
            success &= self.memory.set(key, value, ttl, tags)
        
        if CacheLevel.DISK in levels:
            success &= self.disk.set(key, value, ttl, tags)
        
        return success
    
    def delete(self, key: str) -> bool:
        """Delete from all cache levels.
        
        Args:
            key: Cache key
            
        Returns:
            True if deleted from any level
        """
        memory_deleted = self.memory.delete(key)
        disk_deleted = self.disk.delete(key)
        return memory_deleted or disk_deleted
    
    def clear(self):
        """Clear all cache levels."""
        self.memory.clear()
        self.disk.clear()
    
    def invalidate_by_tags(self, tags: Set[str]) -> int:
        """Invalidate entries with matching tags.
        
        Args:
            tags: Tags to match
            
        Returns:
            Total invalidated entries
        """
        memory_count = self.memory.invalidate_by_tags(tags)
        disk_count = self.disk.invalidate_by_tags(tags)
        return memory_count + disk_count
    
    def get_stats(self) -> Dict[str, CacheStats]:
        """Get statistics for all levels.
        
        Returns:
            Dictionary of stats by level
        """
        return {
            'memory': self.memory.get_stats(),
            'disk': self.disk.get_stats()
        }
    
    async def warm_cache(self, keys: List[str]):
        """Warm cache by preloading keys.
        
        Args:
            keys: Keys to preload
        """
        if self._warm_queue is None:
            self._warm_queue = asyncio.Queue()
            self._warm_task = asyncio.create_task(self._warm_worker())
        
        for key in keys:
            await self._warm_queue.put(key)
    
    async def _warm_worker(self):
        """Background worker for cache warming."""
        while True:
            try:
                key = await self._warm_queue.get()
                
                # Load from disk to memory
                value = self.disk.get(key)
                if value is not None:
                    self.memory.set(key, value)
                    logger.debug(f"Warmed cache for key: {key}")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cache warming error: {e}")


class CacheDecorator:
    """Decorator for caching function results."""
    
    def __init__(
        self,
        cache: Union[MemoryCache, DiskCache, MultiLevelCache],
        ttl: Optional[float] = None,
        key_prefix: str = "",
        key_func: Optional[Callable] = None
    ):
        """Initialize cache decorator.
        
        Args:
            cache: Cache instance to use
            ttl: Time to live for cached results
            key_prefix: Prefix for cache keys
            key_func: Custom key generation function
        """
        self.cache = cache
        self.ttl = ttl
        self.key_prefix = key_prefix
        self.key_func = key_func or CacheKeyGenerator.generate_key
    
    def __call__(self, func: Callable) -> Callable:
        """Decorate function with caching.
        
        Args:
            func: Function to cache
            
        Returns:
            Wrapped function
        """
        def wrapper(*args, **kwargs):
            # Generate cache key
            cache_key = self.key_prefix + self.key_func(*args, **kwargs)
            
            # Try to get from cache
            result = self.cache.get(cache_key)
            if result is not None:
                logger.debug(f"Cache hit for {func.__name__}")
                return result
            
            # Call function
            result = func(*args, **kwargs)
            
            # Cache result
            self.cache.set(cache_key, result, ttl=self.ttl)
            logger.debug(f"Cached result for {func.__name__}")
            
            return result
        
        # Async version
        async def async_wrapper(*args, **kwargs):
            # Generate cache key
            cache_key = self.key_prefix + self.key_func(*args, **kwargs)
            
            # Try to get from cache
            result = self.cache.get(cache_key)
            if result is not None:
                logger.debug(f"Cache hit for {func.__name__}")
                return result
            
            # Call async function
            result = await func(*args, **kwargs)
            
            # Cache result
            self.cache.set(cache_key, result, ttl=self.ttl)
            logger.debug(f"Cached result for {func.__name__}")
            
            return result
        
        # Return appropriate wrapper
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return wrapper


class CacheManager:
    """Global cache manager for application-wide caching."""
    
    _instance: Optional['CacheManager'] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Singleton pattern."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize cache manager."""
        if not hasattr(self, '_initialized'):
            self._caches: Dict[str, Any] = {}
            self._initialized = True
    
    def register_cache(
        self,
        name: str,
        cache: Union[MemoryCache, DiskCache, MultiLevelCache]
    ):
        """Register a named cache.
        
        Args:
            name: Cache name
            cache: Cache instance
        """
        self._caches[name] = cache
        logger.info(f"Registered cache: {name}")
    
    def get_cache(self, name: str) -> Optional[Any]:
        """Get cache by name.
        
        Args:
            name: Cache name
            
        Returns:
            Cache instance or None
        """
        return self._caches.get(name)
    
    def clear_all(self):
        """Clear all registered caches."""
        for name, cache in self._caches.items():
            cache.clear()
            logger.info(f"Cleared cache: {name}")
    
    def get_all_stats(self) -> Dict[str, Any]:
        """Get statistics for all caches.
        
        Returns:
            Dictionary of stats by cache name
        """
        stats = {}
        
        for name, cache in self._caches.items():
            if hasattr(cache, 'get_stats'):
                cache_stats = cache.get_stats()
                if isinstance(cache_stats, dict):
                    stats[name] = cache_stats
                else:
                    stats[name] = cache_stats.to_dict()
        
        return stats
    
    def cleanup_expired(self):
        """Clean up expired entries in all caches."""
        for name, cache in self._caches.items():
            if hasattr(cache, 'cleanup_expired'):
                removed = cache.cleanup_expired()
                if removed:
                    logger.info(f"Removed {removed} expired entries from {name}")


# Convenience functions and decorators
def memory_cache(ttl: Optional[float] = None, key_prefix: str = ""):
    """Decorator for memory caching.
    
    Args:
        ttl: Time to live in seconds
        key_prefix: Prefix for cache keys
        
    Returns:
        Cache decorator
    """
    cache = MemoryCache()
    return CacheDecorator(cache, ttl=ttl, key_prefix=key_prefix)


def disk_cache(ttl: Optional[float] = None, key_prefix: str = "", cache_dir: Optional[Path] = None):
    """Decorator for disk caching.
    
    Args:
        ttl: Time to live in seconds
        key_prefix: Prefix for cache keys
        cache_dir: Cache directory
        
    Returns:
        Cache decorator
    """
    cache = DiskCache(cache_dir=cache_dir)
    return CacheDecorator(cache, ttl=ttl, key_prefix=key_prefix)


def cached(cache_name: str, ttl: Optional[float] = None):
    """Decorator using named cache from manager.
    
    Args:
        cache_name: Name of registered cache
        ttl: Time to live
        
    Returns:
        Cache decorator
    """
    manager = CacheManager()
    cache = manager.get_cache(cache_name)
    
    if cache is None:
        # Create default cache if not exists
        cache = MemoryCache()
        manager.register_cache(cache_name, cache)
    
    return CacheDecorator(cache, ttl=ttl)


# Initialize default caches
def init_default_caches():
    """Initialize default application caches."""
    manager = CacheManager()
    
    # Image cache (memory + disk)
    image_cache = MultiLevelCache(
        memory_size=100 * 1024 * 1024,  # 100MB
        disk_size=1024 * 1024 * 1024,   # 1GB
        cache_dir=Path.cwd() / '.cache' / 'images'
    )
    manager.register_cache('images', image_cache)
    
    # Prompt cache (memory only)
    prompt_cache = MemoryCache(
        max_size=50 * 1024 * 1024,  # 50MB
        strategy=CacheStrategy.LRU,
        default_ttl=3600  # 1 hour
    )
    manager.register_cache('prompts', prompt_cache)
    
    # API response cache (disk)
    api_cache = DiskCache(
        cache_dir=Path.cwd() / '.cache' / 'api',
        max_size=200 * 1024 * 1024,  # 200MB
        default_ttl=300  # 5 minutes
    )
    manager.register_cache('api', api_cache)
    
    logger.info("Default caches initialized")
