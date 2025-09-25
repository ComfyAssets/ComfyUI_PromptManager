"""Performance monitoring utilities for PromptManager.

Provides comprehensive performance tracking including timing decorators,
memory profiling, query optimization, bottleneck detection, and metrics collection.
"""

import asyncio
import functools
import gc
import json
import psutil
import resource
import sys
import threading
import time
import tracemalloc
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import cProfile
import pstats
from io import StringIO
import warnings

from .logging import get_logger
from .file_ops import AtomicWriter

logger = get_logger("promptmanager.performance")


class MetricType(Enum):
    """Types of performance metrics."""
    TIMER = "timer"
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    RATE = "rate"
    MEMORY = "memory"


class PerformanceLevel(Enum):
    """Performance monitoring levels."""
    BASIC = "basic"  # Essential metrics only
    STANDARD = "standard"  # Standard monitoring
    DETAILED = "detailed"  # Detailed profiling
    DEBUG = "debug"  # Everything including traces


@dataclass
class TimingResult:
    """Result of a timed operation."""
    name: str
    duration: float
    start_time: float
    end_time: float
    success: bool = True
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def duration_ms(self) -> float:
        """Duration in milliseconds."""
        return self.duration * 1000
    
    @property
    def duration_formatted(self) -> str:
        """Human-readable duration."""
        if self.duration < 0.001:
            return f"{self.duration * 1_000_000:.2f}μs"
        elif self.duration < 1:
            return f"{self.duration * 1000:.2f}ms"
        else:
            return f"{self.duration:.2f}s"


@dataclass
class MemorySnapshot:
    """Memory usage snapshot."""
    timestamp: float
    rss: int  # Resident Set Size
    vms: int  # Virtual Memory Size
    available: int
    percent: float
    swap_used: int
    swap_percent: float
    
    @property
    def rss_mb(self) -> float:
        """RSS in megabytes."""
        return self.rss / 1024 / 1024
    
    @property
    def available_gb(self) -> float:
        """Available memory in gigabytes."""
        return self.available / 1024 / 1024 / 1024


@dataclass
class PerformanceMetric:
    """Container for performance metrics."""
    name: str
    type: MetricType
    value: float
    timestamp: float
    tags: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'name': self.name,
            'type': self.type.value,
            'value': self.value,
            'timestamp': self.timestamp,
            'tags': self.tags
        }


class Timer:
    """High-precision timer for performance measurement."""
    
    def __init__(self, name: str = "operation", auto_log: bool = True):
        """Initialize timer.
        
        Args:
            name: Operation name
            auto_log: Whether to log automatically
        """
        self.name = name
        self.auto_log = auto_log
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.duration: Optional[float] = None
        
    def __enter__(self):
        """Start timing."""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop timing and optionally log."""
        self.stop()
        
        if self.auto_log and self.duration is not None:
            if exc_type is None:
                logger.debug(f"{self.name}: {self.duration * 1000:.2f}ms")
            else:
                logger.warning(f"{self.name} failed after {self.duration * 1000:.2f}ms: {exc_val}")
    
    def start(self):
        """Start the timer."""
        self.start_time = time.perf_counter()
        self.end_time = None
        self.duration = None
    
    def stop(self) -> float:
        """Stop the timer and return duration.
        
        Returns:
            Duration in seconds
        """
        if self.start_time is None:
            raise RuntimeError("Timer not started")
        
        self.end_time = time.perf_counter()
        self.duration = self.end_time - self.start_time
        return self.duration
    
    def lap(self) -> float:
        """Get lap time without stopping.
        
        Returns:
            Time since start in seconds
        """
        if self.start_time is None:
            raise RuntimeError("Timer not started")
        
        return time.perf_counter() - self.start_time
    
    def reset(self):
        """Reset the timer."""
        self.start_time = None
        self.end_time = None
        self.duration = None


class PerformanceTracker:
    """Track and aggregate performance metrics."""
    
    def __init__(
        self,
        max_history: int = 1000,
        level: PerformanceLevel = PerformanceLevel.STANDARD
    ):
        """Initialize performance tracker.
        
        Args:
            max_history: Maximum metrics to keep in history
            level: Monitoring level
        """
        self.max_history = max_history
        self.level = level
        
        # Metrics storage
        self._timings: deque[TimingResult] = deque(maxlen=max_history)
        self._metrics: deque[PerformanceMetric] = deque(maxlen=max_history)
        self._counters: Dict[str, int] = defaultdict(int)
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        
        # Thread safety
        self._lock = threading.RLock()
        
        # Memory tracking
        self._memory_snapshots: deque[MemorySnapshot] = deque(maxlen=100)
        self._trace_malloc_started = False
    
    def record_timing(self, result: TimingResult):
        """Record a timing result.
        
        Args:
            result: Timing result to record
        """
        with self._lock:
            self._timings.append(result)
            
            # Update histogram
            self._histograms[result.name].append(result.duration)
            
            # Keep histogram size bounded
            if len(self._histograms[result.name]) > self.max_history:
                self._histograms[result.name] = self._histograms[result.name][-self.max_history:]
    
    def record_metric(
        self,
        name: str,
        value: float,
        type: MetricType = MetricType.GAUGE,
        tags: Optional[Dict[str, str]] = None
    ):
        """Record a performance metric.
        
        Args:
            name: Metric name
            value: Metric value
            type: Metric type
            tags: Optional tags
        """
        metric = PerformanceMetric(
            name=name,
            type=type,
            value=value,
            timestamp=time.time(),
            tags=tags or {}
        )
        
        with self._lock:
            self._metrics.append(metric)
            
            if type == MetricType.COUNTER:
                self._counters[name] += int(value)
            elif type == MetricType.GAUGE:
                self._gauges[name] = value
    
    def increment_counter(self, name: str, value: int = 1):
        """Increment a counter metric.
        
        Args:
            name: Counter name
            value: Increment value
        """
        with self._lock:
            self._counters[name] += value
            self.record_metric(name, value, MetricType.COUNTER)
    
    def set_gauge(self, name: str, value: float):
        """Set a gauge metric.
        
        Args:
            name: Gauge name
            value: Gauge value
        """
        with self._lock:
            self._gauges[name] = value
            self.record_metric(name, value, MetricType.GAUGE)
    
    def take_memory_snapshot(self) -> MemorySnapshot:
        """Take a memory usage snapshot.
        
        Returns:
            Memory snapshot
        """
        process = psutil.Process()
        memory_info = process.memory_info()
        virtual_memory = psutil.virtual_memory()
        swap_memory = psutil.swap_memory()
        
        snapshot = MemorySnapshot(
            timestamp=time.time(),
            rss=memory_info.rss,
            vms=memory_info.vms,
            available=virtual_memory.available,
            percent=virtual_memory.percent,
            swap_used=swap_memory.used,
            swap_percent=swap_memory.percent
        )
        
        with self._lock:
            self._memory_snapshots.append(snapshot)
        
        return snapshot
    
    def get_timing_stats(self, name: Optional[str] = None) -> Dict[str, Any]:
        """Get timing statistics.
        
        Args:
            name: Optional operation name filter
            
        Returns:
            Statistics dictionary
        """
        with self._lock:
            if name:
                timings = [t for t in self._timings if t.name == name]
            else:
                timings = list(self._timings)
        
        if not timings:
            return {}
        
        durations = [t.duration for t in timings]
        success_count = sum(1 for t in timings if t.success)
        
        return {
            'count': len(timings),
            'success_rate': success_count / len(timings),
            'mean': sum(durations) / len(durations),
            'min': min(durations),
            'max': max(durations),
            'median': sorted(durations)[len(durations) // 2],
            'total': sum(durations),
            'recent': timings[-1].duration if timings else 0
        }
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """Get memory statistics.
        
        Returns:
            Memory statistics
        """
        if not self._memory_snapshots:
            snapshot = self.take_memory_snapshot()
        else:
            snapshot = self._memory_snapshots[-1]
        
        return {
            'rss_mb': snapshot.rss_mb,
            'available_gb': snapshot.available_gb,
            'percent_used': snapshot.percent,
            'swap_percent': snapshot.swap_percent,
            'timestamp': snapshot.timestamp
        }
    
    def get_all_stats(self) -> Dict[str, Any]:
        """Get all performance statistics.
        
        Returns:
            Complete statistics dictionary
        """
        with self._lock:
            # Aggregate timing stats by operation
            timing_stats = {}
            operation_names = set(t.name for t in self._timings)
            
            for name in operation_names:
                timing_stats[name] = self.get_timing_stats(name)
            
            return {
                'timings': timing_stats,
                'counters': dict(self._counters),
                'gauges': dict(self._gauges),
                'memory': self.get_memory_stats(),
                'level': self.level.value
            }
    
    def reset(self):
        """Reset all metrics."""
        with self._lock:
            self._timings.clear()
            self._metrics.clear()
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._memory_snapshots.clear()


class MemoryProfiler:
    """Memory profiling and leak detection."""
    
    def __init__(self):
        """Initialize memory profiler."""
        self.baseline_snapshot = None
        self.snapshots: List[Any] = []
        self.trace_active = False
    
    def start_tracing(self):
        """Start memory allocation tracing."""
        if not self.trace_active:
            tracemalloc.start()
            self.trace_active = True
            logger.info("Memory tracing started")
    
    def stop_tracing(self):
        """Stop memory allocation tracing."""
        if self.trace_active:
            tracemalloc.stop()
            self.trace_active = False
            logger.info("Memory tracing stopped")
    
    def take_snapshot(self, name: str = "snapshot") -> Dict[str, Any]:
        """Take memory allocation snapshot.
        
        Args:
            name: Snapshot name
            
        Returns:
            Snapshot information
        """
        if not self.trace_active:
            self.start_tracing()
        
        snapshot = tracemalloc.take_snapshot()
        self.snapshots.append((name, snapshot))
        
        # Get top memory users
        top_stats = snapshot.statistics('lineno')[:10]
        
        stats = {
            'name': name,
            'timestamp': time.time(),
            'total_size': sum(stat.size for stat in top_stats),
            'total_count': sum(stat.count for stat in top_stats),
            'top_allocations': []
        }
        
        for stat in top_stats:
            stats['top_allocations'].append({
                'file': stat.traceback.format()[0] if stat.traceback else 'unknown',
                'size_kb': stat.size / 1024,
                'count': stat.count
            })
        
        return stats
    
    def compare_snapshots(
        self,
        baseline_name: str = None,
        current_name: str = None
    ) -> Dict[str, Any]:
        """Compare memory snapshots to find leaks.
        
        Args:
            baseline_name: Baseline snapshot name
            current_name: Current snapshot name
            
        Returns:
            Comparison results
        """
        if len(self.snapshots) < 2:
            return {'error': 'Need at least 2 snapshots to compare'}
        
        # Get snapshots
        if baseline_name:
            baseline = next((s for n, s in self.snapshots if n == baseline_name), None)
        else:
            baseline = self.snapshots[0][1]
        
        if current_name:
            current = next((s for n, s in self.snapshots if n == current_name), None)
        else:
            current = self.snapshots[-1][1]
        
        if not baseline or not current:
            return {'error': 'Snapshots not found'}
        
        # Compare
        top_stats = current.compare_to(baseline, 'lineno')[:10]
        
        results = {
            'increased_allocations': [],
            'decreased_allocations': [],
            'total_diff_kb': 0
        }
        
        for stat in top_stats:
            diff_kb = stat.size_diff / 1024
            results['total_diff_kb'] += diff_kb
            
            allocation = {
                'file': stat.traceback.format()[0] if stat.traceback else 'unknown',
                'size_diff_kb': diff_kb,
                'count_diff': stat.count_diff
            }
            
            if diff_kb > 0:
                results['increased_allocations'].append(allocation)
            else:
                results['decreased_allocations'].append(allocation)
        
        return results
    
    def find_memory_leaks(self, threshold_kb: float = 100) -> List[Dict[str, Any]]:
        """Find potential memory leaks.
        
        Args:
            threshold_kb: Size threshold for leak detection
            
        Returns:
            List of potential leaks
        """
        if len(self.snapshots) < 2:
            return []
        
        comparison = self.compare_snapshots()
        
        leaks = []
        for alloc in comparison.get('increased_allocations', []):
            if alloc['size_diff_kb'] > threshold_kb:
                leaks.append({
                    'location': alloc['file'],
                    'leaked_kb': alloc['size_diff_kb'],
                    'object_count': alloc['count_diff'],
                    'severity': 'high' if alloc['size_diff_kb'] > 1000 else 'medium'
                })
        
        return leaks
    
    def get_object_growth(self) -> Dict[str, int]:
        """Get growth in object counts by type.
        
        Returns:
            Dictionary of type to count difference
        """
        gc.collect()  # Force garbage collection
        
        current_objects = {}
        for obj in gc.get_objects():
            obj_type = type(obj).__name__
            current_objects[obj_type] = current_objects.get(obj_type, 0) + 1
        
        return current_objects


class CPUProfiler:
    """CPU profiling for bottleneck detection."""
    
    def __init__(self):
        """Initialize CPU profiler."""
        self.profiler = None
        self.stats = None
    
    def start(self):
        """Start CPU profiling."""
        self.profiler = cProfile.Profile()
        self.profiler.enable()
        logger.info("CPU profiling started")
    
    def stop(self) -> pstats.Stats:
        """Stop CPU profiling and return stats.
        
        Returns:
            Profile statistics
        """
        if self.profiler:
            self.profiler.disable()
            self.stats = pstats.Stats(self.profiler)
            logger.info("CPU profiling stopped")
            return self.stats
        return None
    
    def get_top_functions(self, count: int = 20) -> List[Dict[str, Any]]:
        """Get top time-consuming functions.
        
        Args:
            count: Number of functions to return
            
        Returns:
            List of function statistics
        """
        if not self.stats:
            return []
        
        # Capture stats output
        s = StringIO()
        self.stats.stream = s
        self.stats.sort_stats('cumulative')
        self.stats.print_stats(count)
        
        # Parse output
        lines = s.getvalue().split('\n')
        functions = []
        
        for line in lines:
            if line and not line.startswith(' '):
                parts = line.split()
                if len(parts) >= 6 and parts[0].replace('.', '').isdigit():
                    functions.append({
                        'calls': parts[0],
                        'total_time': float(parts[1]),
                        'per_call': float(parts[2]),
                        'cumulative': float(parts[3]),
                        'function': ' '.join(parts[5:])
                    })
        
        return functions
    
    def find_bottlenecks(self, threshold_percent: float = 5.0) -> List[Dict[str, Any]]:
        """Find performance bottlenecks.
        
        Args:
            threshold_percent: CPU time percentage threshold
            
        Returns:
            List of bottleneck functions
        """
        functions = self.get_top_functions()
        
        if not functions:
            return []
        
        total_time = sum(f['cumulative'] for f in functions)
        bottlenecks = []
        
        for func in functions:
            percent = (func['cumulative'] / total_time) * 100
            
            if percent > threshold_percent:
                bottlenecks.append({
                    'function': func['function'],
                    'cpu_percent': percent,
                    'total_time': func['cumulative'],
                    'calls': func['calls'],
                    'recommendation': self._get_optimization_recommendation(func, percent)
                })
        
        return bottlenecks
    
    def _get_optimization_recommendation(self, func: Dict[str, Any], percent: float) -> str:
        """Get optimization recommendation for a function.
        
        Args:
            func: Function statistics
            percent: CPU percentage
            
        Returns:
            Optimization recommendation
        """
        recommendations = []
        
        if percent > 20:
            recommendations.append("Critical bottleneck - prioritize optimization")
        
        if 'loop' in func['function'].lower() or 'iter' in func['function'].lower():
            recommendations.append("Consider loop optimization or vectorization")
        
        if 'sort' in func['function'].lower():
            recommendations.append("Review sorting algorithm efficiency")
        
        if 'database' in func['function'].lower() or 'query' in func['function'].lower():
            recommendations.append("Optimize database queries and add indexes")
        
        if 'file' in func['function'].lower() or 'io' in func['function'].lower():
            recommendations.append("Consider async I/O or caching")
        
        return '; '.join(recommendations) if recommendations else "Review for optimization opportunities"


class QueryOptimizer:
    """Database query optimization analyzer."""
    
    def __init__(self):
        """Initialize query optimizer."""
        self.query_times: Dict[str, List[float]] = defaultdict(list)
        self.slow_queries: List[Dict[str, Any]] = []
        self.query_plans: Dict[str, Any] = {}
    
    def record_query(
        self,
        query: str,
        duration: float,
        rows_affected: int = 0,
        plan: Optional[str] = None
    ):
        """Record query execution.
        
        Args:
            query: SQL query
            duration: Execution time in seconds
            rows_affected: Number of rows affected
            plan: Query execution plan
        """
        # Normalize query for grouping
        normalized = self._normalize_query(query)
        
        self.query_times[normalized].append(duration)
        
        # Track slow queries
        if duration > 1.0:  # Queries over 1 second
            self.slow_queries.append({
                'query': query,
                'normalized': normalized,
                'duration': duration,
                'rows': rows_affected,
                'timestamp': time.time()
            })
            
            # Keep only recent slow queries
            if len(self.slow_queries) > 100:
                self.slow_queries = self.slow_queries[-100:]
        
        # Store query plan
        if plan:
            self.query_plans[normalized] = plan
    
    def get_slow_queries(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get slowest queries.
        
        Args:
            limit: Number of queries to return
            
        Returns:
            List of slow query information
        """
        # Sort by duration
        sorted_queries = sorted(
            self.slow_queries,
            key=lambda x: x['duration'],
            reverse=True
        )[:limit]
        
        results = []
        for query_info in sorted_queries:
            results.append({
                'query': query_info['query'],
                'duration': query_info['duration'],
                'rows': query_info['rows'],
                'recommendations': self._get_query_recommendations(query_info['query'])
            })
        
        return results
    
    def analyze_query_patterns(self) -> Dict[str, Any]:
        """Analyze query patterns for optimization.
        
        Returns:
            Query pattern analysis
        """
        patterns = {
            'n_plus_one': [],
            'missing_index': [],
            'full_table_scan': [],
            'complex_joins': [],
            'no_limit': []
        }
        
        for query, times in self.query_times.items():
            avg_time = sum(times) / len(times)
            
            # N+1 query detection
            if len(times) > 10 and avg_time < 0.1:
                patterns['n_plus_one'].append({
                    'query': query,
                    'count': len(times),
                    'total_time': sum(times)
                })
            
            # Missing index detection
            if 'WHERE' in query.upper() and avg_time > 0.5:
                patterns['missing_index'].append({
                    'query': query,
                    'avg_time': avg_time
                })
            
            # Full table scan detection
            if 'SELECT *' in query.upper() and 'LIMIT' not in query.upper():
                patterns['full_table_scan'].append({
                    'query': query,
                    'avg_time': avg_time
                })
            
            # Complex joins
            join_count = query.upper().count('JOIN')
            if join_count > 3:
                patterns['complex_joins'].append({
                    'query': query,
                    'join_count': join_count,
                    'avg_time': avg_time
                })
            
            # Missing LIMIT
            if 'SELECT' in query.upper() and 'LIMIT' not in query.upper():
                patterns['no_limit'].append({
                    'query': query[:100] + '...' if len(query) > 100 else query
                })
        
        return patterns
    
    def _normalize_query(self, query: str) -> str:
        """Normalize query for pattern matching.
        
        Args:
            query: SQL query
            
        Returns:
            Normalized query
        """
        # Remove values for grouping similar queries
        import re
        
        # Replace numbers with placeholders
        normalized = re.sub(r'\d+', '?', query)
        
        # Replace strings with placeholders
        normalized = re.sub(r"'[^']*'", '?', normalized)
        normalized = re.sub(r'"[^"]*"', '?', normalized)
        
        # Remove extra whitespace
        normalized = ' '.join(normalized.split())
        
        return normalized
    
    def _get_query_recommendations(self, query: str) -> List[str]:
        """Get optimization recommendations for a query.
        
        Args:
            query: SQL query
            
        Returns:
            List of recommendations
        """
        recommendations = []
        query_upper = query.upper()
        
        if 'SELECT *' in query_upper:
            recommendations.append("Specify only needed columns instead of SELECT *")
        
        if 'WHERE' in query_upper and 'INDEX' not in query_upper:
            recommendations.append("Consider adding indexes on WHERE clause columns")
        
        if query_upper.count('JOIN') > 2:
            recommendations.append("Review join strategy and consider denormalization")
        
        if 'LIMIT' not in query_upper and 'SELECT' in query_upper:
            recommendations.append("Add LIMIT clause to prevent large result sets")
        
        if 'OR' in query_upper:
            recommendations.append("OR conditions may prevent index usage - consider UNION")
        
        if 'LIKE' in query_upper and query.count('%') > 1:
            recommendations.append("Leading wildcards in LIKE prevent index usage")
        
        return recommendations


class PerformanceMonitor:
    """Central performance monitoring system."""
    
    _instance: Optional['PerformanceMonitor'] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Singleton pattern."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize performance monitor."""
        if not hasattr(self, '_initialized'):
            self.tracker = PerformanceTracker()
            self.memory_profiler = MemoryProfiler()
            self.cpu_profiler = CPUProfiler()
            self.query_optimizer = QueryOptimizer()
            
            # Auto-save configuration
            self._auto_save_enabled = False
            self._save_interval = 300  # 5 minutes
            self._save_path = Path.cwd() / 'performance_metrics.json'
            self._save_task: Optional[asyncio.Task] = None
            
            self._initialized = True
    
    def enable_auto_save(
        self,
        path: Union[str, Path] = None,
        interval: int = 300
    ):
        """Enable automatic metrics saving.
        
        Args:
            path: Save file path
            interval: Save interval in seconds
        """
        if path:
            self._save_path = Path(path)
        self._save_interval = interval
        self._auto_save_enabled = True
        
        # Start save task if in async context
        try:
            loop = asyncio.get_running_loop()
            if self._save_task:
                self._save_task.cancel()
            self._save_task = loop.create_task(self._auto_save_worker())
        except RuntimeError:
            # Not in async context
            pass
        
        logger.info(f"Auto-save enabled: {self._save_path} every {interval}s")
    
    async def _auto_save_worker(self):
        """Background worker for auto-saving metrics."""
        while self._auto_save_enabled:
            try:
                await asyncio.sleep(self._save_interval)
                self.save_metrics()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Auto-save error: {e}")
    
    def save_metrics(self, path: Optional[Union[str, Path]] = None):
        """Save metrics to file.
        
        Args:
            path: Optional save path
        """
        save_path = Path(path) if path else self._save_path
        
        metrics = {
            'timestamp': time.time(),
            'performance': self.tracker.get_all_stats(),
            'slow_queries': self.query_optimizer.get_slow_queries(),
            'query_patterns': self.query_optimizer.analyze_query_patterns()
        }
        
        with AtomicWriter(save_path, 'w') as f:
            json.dump(metrics, f, indent=2, default=str)
        
        logger.debug(f"Metrics saved to {save_path}")
    
    def get_report(self) -> Dict[str, Any]:
        """Generate comprehensive performance report.
        
        Returns:
            Performance report
        """
        return {
            'summary': {
                'timestamp': time.time(),
                'level': self.tracker.level.value,
                'memory_mb': self.tracker.get_memory_stats()['rss_mb'],
                'counters': self.tracker._counters
            },
            'performance': self.tracker.get_all_stats(),
            'memory': {
                'current': self.tracker.get_memory_stats(),
                'leaks': self.memory_profiler.find_memory_leaks()
            },
            'bottlenecks': self.cpu_profiler.find_bottlenecks(),
            'database': {
                'slow_queries': self.query_optimizer.get_slow_queries(),
                'patterns': self.query_optimizer.analyze_query_patterns()
            }
        }
    
    def reset(self):
        """Reset all monitoring data."""
        self.tracker.reset()
        self.memory_profiler.snapshots.clear()
        self.query_optimizer.query_times.clear()
        self.query_optimizer.slow_queries.clear()
        logger.info("Performance monitoring reset")


# Decorators for performance monitoring
def timed(name: Optional[str] = None, log_level: str = "debug"):
    """Decorator for timing function execution.
    
    Args:
        name: Custom operation name
        log_level: Logging level
        
    Returns:
        Decorated function
    """
    def decorator(func: Callable) -> Callable:
        operation_name = name or f"{func.__module__}.{func.__name__}"
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            timer = Timer(operation_name, auto_log=False)
            monitor = PerformanceMonitor()
            
            try:
                timer.start()
                result = func(*args, **kwargs)
                duration = timer.stop()
                
                timing_result = TimingResult(
                    name=operation_name,
                    duration=duration,
                    start_time=timer.start_time,
                    end_time=timer.end_time,
                    success=True
                )
                
                monitor.tracker.record_timing(timing_result)
                
                getattr(logger, log_level)(
                    f"{operation_name}: {duration * 1000:.2f}ms"
                )
                
                return result
                
            except Exception as e:
                duration = timer.stop() if timer.start_time else 0
                
                timing_result = TimingResult(
                    name=operation_name,
                    duration=duration,
                    start_time=timer.start_time or time.time(),
                    end_time=time.time(),
                    success=False,
                    error=str(e)
                )
                
                monitor.tracker.record_timing(timing_result)
                logger.error(f"{operation_name} failed after {duration * 1000:.2f}ms: {e}")
                raise
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            timer = Timer(operation_name, auto_log=False)
            monitor = PerformanceMonitor()
            
            try:
                timer.start()
                result = await func(*args, **kwargs)
                duration = timer.stop()
                
                timing_result = TimingResult(
                    name=operation_name,
                    duration=duration,
                    start_time=timer.start_time,
                    end_time=timer.end_time,
                    success=True
                )
                
                monitor.tracker.record_timing(timing_result)
                
                getattr(logger, log_level)(
                    f"{operation_name}: {duration * 1000:.2f}ms"
                )
                
                return result
                
            except Exception as e:
                duration = timer.stop() if timer.start_time else 0
                
                timing_result = TimingResult(
                    name=operation_name,
                    duration=duration,
                    start_time=timer.start_time or time.time(),
                    end_time=time.time(),
                    success=False,
                    error=str(e)
                )
                
                monitor.tracker.record_timing(timing_result)
                logger.error(f"{operation_name} failed after {duration * 1000:.2f}ms: {e}")
                raise
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return wrapper
    
    return decorator


def profile_memory(func: Callable) -> Callable:
    """Decorator for memory profiling.
    
    Args:
        func: Function to profile
        
    Returns:
        Decorated function
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        monitor = PerformanceMonitor()
        profiler = monitor.memory_profiler
        
        # Take before snapshot
        before = profiler.take_snapshot(f"before_{func.__name__}")
        
        try:
            result = func(*args, **kwargs)
            
            # Take after snapshot
            after = profiler.take_snapshot(f"after_{func.__name__}")
            
            # Log memory change
            comparison = profiler.compare_snapshots(
                f"before_{func.__name__}",
                f"after_{func.__name__}"
            )
            
            if comparison.get('total_diff_kb', 0) > 100:
                logger.warning(
                    f"{func.__name__} increased memory by "
                    f"{comparison['total_diff_kb']:.1f}KB"
                )
            
            return result
            
        except Exception as e:
            logger.error(f"{func.__name__} failed: {e}")
            raise
    
    return wrapper


def profile_cpu(func: Callable) -> Callable:
    """Decorator for CPU profiling.
    
    Args:
        func: Function to profile
        
    Returns:
        Decorated function
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        monitor = PerformanceMonitor()
        profiler = monitor.cpu_profiler
        
        profiler.start()
        
        try:
            result = func(*args, **kwargs)
            stats = profiler.stop()
            
            # Find bottlenecks
            bottlenecks = profiler.find_bottlenecks()
            
            if bottlenecks:
                logger.warning(
                    f"{func.__name__} has {len(bottlenecks)} bottlenecks: "
                    f"{', '.join(b['function'] for b in bottlenecks[:3])}"
                )
            
            return result
            
        except Exception as e:
            profiler.stop()
            logger.error(f"{func.__name__} failed: {e}")
            raise
    
    return wrapper


# Context managers for performance monitoring
@contextmanager
def timer(name: str = "operation"):
    """Context manager for timing operations.
    
    Args:
        name: Operation name
        
    Yields:
        Timer instance
    """
    t = Timer(name)
    t.start()
    
    try:
        yield t
    finally:
        t.stop()
        
        monitor = PerformanceMonitor()
        monitor.tracker.record_timing(
            TimingResult(
                name=name,
                duration=t.duration,
                start_time=t.start_time,
                end_time=t.end_time,
                success=True
            )
        )


@contextmanager
def memory_tracking(name: str = "operation"):
    """Context manager for memory tracking.
    
    Args:
        name: Operation name
        
    Yields:
        Memory profiler
    """
    monitor = PerformanceMonitor()
    profiler = monitor.memory_profiler
    
    before = profiler.take_snapshot(f"before_{name}")
    
    try:
        yield profiler
    finally:
        after = profiler.take_snapshot(f"after_{name}")
        comparison = profiler.compare_snapshots(f"before_{name}", f"after_{name}")
        
        if comparison.get('total_diff_kb', 0) > 0:
            logger.debug(f"{name}: Memory +{comparison['total_diff_kb']:.1f}KB")


@contextmanager
def cpu_profiling(name: str = "operation"):
    """Context manager for CPU profiling.
    
    Args:
        name: Operation name
        
    Yields:
        CPU profiler
    """
    monitor = PerformanceMonitor()
    profiler = monitor.cpu_profiler
    
    profiler.start()
    
    try:
        yield profiler
    finally:
        stats = profiler.stop()
        
        # Log top functions
        top_funcs = profiler.get_top_functions(5)
        if top_funcs:
            logger.debug(
                f"{name}: Top CPU users: "
                f"{', '.join(f['function'] for f in top_funcs[:3])}"
            )


# Convenience functions
def get_performance_report() -> Dict[str, Any]:
    """Get current performance report.
    
    Returns:
        Performance report
    """
    monitor = PerformanceMonitor()
    return monitor.get_report()


def enable_monitoring(level: PerformanceLevel = PerformanceLevel.STANDARD):
    """Enable performance monitoring.
    
    Args:
        level: Monitoring level
    """
    monitor = PerformanceMonitor()
    monitor.tracker.level = level
    
    if level == PerformanceLevel.DETAILED:
        monitor.memory_profiler.start_tracing()
    
    logger.info(f"Performance monitoring enabled at {level.value} level")


def save_performance_metrics(path: Union[str, Path] = None):
    """Save performance metrics to file.
    
    Args:
        path: Save path
    """
    monitor = PerformanceMonitor()
    monitor.save_metrics(path)
