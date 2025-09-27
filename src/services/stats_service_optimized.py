"""
Optimized Statistics Service with lazy loading and better caching.
"""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import sqlite3
import threading
import logging

logger = logging.getLogger(__name__)


class OptimizedStatsService:
    """
    Optimized stats service with:
    - Lazy loading (don't load on startup)
    - Longer cache TTL (30 minutes default)
    - Lightweight queries for common operations
    - Background refresh without blocking
    """

    def __init__(self, db_path: str | Path, *, cache_ttl: float = 1800.0) -> None:
        self.db_path = str(db_path)
        self.cache_ttl = cache_ttl  # 30 minutes default (was 5 minutes)
        self._cached_snapshot: Optional[Dict[str, Any]] = None
        self._cached_at: float = 0.0
        self._is_warming = False
        self._warm_thread = None

    def get_overview(self, *, force: bool = False) -> Dict[str, Any]:
        """
        Return stats overview with improved caching strategy.
        """
        now = time.time()

        # Return cached if valid
        if (
            not force
            and self._cached_snapshot is not None
            and now - self._cached_at < self.cache_ttl
        ):
            return self._cached_snapshot.copy()

        # Return lightweight stats if currently warming
        if self._is_warming:
            return self._get_lightweight_stats()

        # Check if we should do background warming
        if self._cached_snapshot is None or now - self._cached_at > self.cache_ttl * 0.8:
            self._start_background_warm()

        # Return cached or lightweight stats while warming
        if self._cached_snapshot:
            return self._cached_snapshot.copy()
        else:
            return self._get_lightweight_stats()

    def _get_lightweight_stats(self) -> Dict[str, Any]:
        """
        Get minimal stats quickly without loading all data.
        Uses COUNT queries instead of loading full datasets.
        """
        try:
            with self._connect() as conn:
                cursor = conn.cursor()

                # Quick counts only
                prompt_count = cursor.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]
                image_count = cursor.execute("SELECT COUNT(*) FROM generated_images").fetchone()[0]
                session_count = cursor.execute(
                    "SELECT COUNT(DISTINCT session_id) FROM prompt_tracking"
                ).fetchone()[0]

                # Top categories (limited)
                categories = cursor.execute("""
                    SELECT category, COUNT(*) as cnt
                    FROM prompts
                    WHERE category IS NOT NULL
                    GROUP BY category
                    ORDER BY cnt DESC
                    LIMIT 10
                """).fetchall()

                return {
                    "totalPrompts": prompt_count,
                    "totalImages": image_count,
                    "totalSessions": session_count,
                    "totals": {
                        "prompts": prompt_count,
                        "images": image_count,
                        "sessions": session_count
                    },
                    "topCategories": {cat: cnt for cat, cnt in categories},
                    "isLightweight": True,
                    "generatedAt": datetime.utcnow().isoformat() + "Z"
                }
        except Exception as e:
            logger.error(f"Failed to get lightweight stats: {e}")
            return self._get_empty_stats()

    def _start_background_warm(self):
        """
        Start background thread to warm cache without blocking.
        """
        if self._is_warming or (self._warm_thread and self._warm_thread.is_alive()):
            return

        self._warm_thread = threading.Thread(target=self._warm_cache, daemon=True)
        self._warm_thread.start()

    def _warm_cache(self):
        """
        Warm cache in background thread.
        """
        try:
            self._is_warming = True
            logger.info("Starting background stats warming")
            start_time = time.time()

            # Build full snapshot
            prompts = self._fetch_prompts_optimized()
            images = self._fetch_images_optimized()
            tracking = self._fetch_tracking_optimized()

            snapshot = self._build_snapshot_optimized(prompts, images, tracking)
            snapshot["generatedAt"] = datetime.utcnow().isoformat() + "Z"

            # Update cache
            self._cached_snapshot = snapshot
            self._cached_at = time.time()

            elapsed = time.time() - start_time
            logger.info(f"Stats warming completed in {elapsed:.2f}s")

        except Exception as e:
            logger.error(f"Stats warming failed: {e}")
        finally:
            self._is_warming = False

    def _fetch_prompts_optimized(self) -> List[Dict[str, Any]]:
        """
        Fetch prompts with only needed columns for stats.
        """
        query = """
            SELECT id, category, tags, rating, created_at
            FROM prompts
            ORDER BY created_at DESC
            LIMIT 10000
        """
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query).fetchall()
            return [dict(row) for row in rows]

    def _fetch_images_optimized(self) -> List[Dict[str, Any]]:
        """
        Fetch images with only needed columns for stats.
        """
        query = """
            SELECT id, prompt_id, generation_time, file_size, created_at
            FROM generated_images
            ORDER BY created_at DESC
            LIMIT 50000
        """
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query).fetchall()
            return [dict(row) for row in rows]

    def _fetch_tracking_optimized(self) -> List[Dict[str, Any]]:
        """
        Fetch tracking data optimized for stats.
        """
        query = """
            SELECT session_id, created_at
            FROM prompt_tracking
            WHERE created_at > date('now', '-30 days')
            ORDER BY created_at DESC
        """
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query).fetchall()
            return [dict(row) for row in rows]

    def _build_snapshot_optimized(
        self,
        prompts: List[Dict[str, Any]],
        images: List[Dict[str, Any]],
        tracking: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Build snapshot with only essential analytics.
        """
        total_sessions = len({t["session_id"] for t in tracking})

        # Basic counts
        snapshot = {
            "totalPrompts": len(prompts),
            "totalImages": len(images),
            "totalSessions": total_sessions,
            "totals": {
                "prompts": len(prompts),
                "images": len(images),
                "sessions": total_sessions
            }
        }

        # Category breakdown (lightweight)
        category_counts = Counter(p.get("category", "Uncategorized") for p in prompts)
        snapshot["categoryBreakdown"] = dict(category_counts.most_common(20))

        # Recent activity (last 30 days)
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        recent_prompts = sum(1 for p in prompts if p.get("created_at", "") >= cutoff)
        recent_images = sum(1 for i in images if i.get("created_at", "") >= cutoff)

        snapshot["recentActivity"] = {
            "prompts_30d": recent_prompts,
            "images_30d": recent_images,
            "daily_avg_prompts": recent_prompts / 30,
            "daily_avg_images": recent_images / 30
        }

        return snapshot

    def _connect(self) -> sqlite3.Connection:
        """Create database connection."""
        return sqlite3.connect(self.db_path)

    def _get_empty_stats(self) -> Dict[str, Any]:
        """Return empty stats structure."""
        return {
            "totalPrompts": 0,
            "totalImages": 0,
            "totalSessions": 0,
            "totals": {"prompts": 0, "images": 0, "sessions": 0},
            "categoryBreakdown": {},
            "recentActivity": {},
            "isError": True,
            "generatedAt": datetime.utcnow().isoformat() + "Z"
        }

    def invalidate_cache(self):
        """Force cache invalidation."""
        self._cached_snapshot = None
        self._cached_at = 0

    def get_cache_info(self) -> Dict[str, Any]:
        """Get cache status information."""
        now = time.time()
        return {
            "has_cache": self._cached_snapshot is not None,
            "cache_age": now - self._cached_at if self._cached_at else None,
            "cache_ttl": self.cache_ttl,
            "is_warming": self._is_warming,
            "cache_valid": (
                self._cached_snapshot is not None
                and now - self._cached_at < self.cache_ttl
            )
        }