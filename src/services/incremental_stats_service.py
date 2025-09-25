"""
Incremental Stats Service - Optimized analytics with delta updates.
Only processes new/changed data instead of full recalculation.
"""

import json
import sqlite3
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from pathlib import Path
import logging

from ..database import PromptDatabase
from ..config.app_config import AppConfig
from ..loggers import get_logger

logger = get_logger(__name__)


class IncrementalStatsService:
    """
    Optimized stats service that uses incremental updates.
    First run performs full calculation, subsequent runs only process deltas.
    """

    def __init__(self, database: Optional[PromptDatabase] = None, config: Optional[AppConfig] = None):
        """Initialize the incremental stats service."""
        self.db = database or PromptDatabase()
        self.config = config or AppConfig()
        self.db_path = self.config.get_database_path()
        self._ensure_cache_table()

    def _ensure_cache_table(self):
        """Create analytics cache table if it doesn't exist."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    metric_type TEXT NOT NULL,
                    metric_key TEXT NOT NULL,
                    metric_value TEXT NOT NULL,
                    calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(metric_type, metric_key)
                )
            """)

            # Create tracking table for last processed timestamps
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics_tracking (
                    id INTEGER PRIMARY KEY,
                    table_name TEXT UNIQUE NOT NULL,
                    last_processed_at TIMESTAMP,
                    last_processed_id INTEGER,
                    processing_status TEXT DEFAULT 'idle'
                )
            """)

            # Create indexes for performance
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_analytics_cache_type
                ON analytics_cache(metric_type)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_analytics_cache_updated
                ON analytics_cache(last_updated)
            """)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        """Create database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_last_processed(self, table_name: str) -> Tuple[Optional[str], Optional[int]]:
        """Get last processed timestamp and ID for a table."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_processed_at, last_processed_id FROM analytics_tracking WHERE table_name = ?",
                (table_name,)
            ).fetchone()

            if row:
                return row['last_processed_at'], row['last_processed_id']
            return None, None

    def update_last_processed(self, table_name: str, timestamp: str, last_id: int):
        """Update last processed tracking for a table."""
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO analytics_tracking (table_name, last_processed_at, last_processed_id)
                VALUES (?, ?, ?)
                ON CONFLICT(table_name) DO UPDATE SET
                    last_processed_at = excluded.last_processed_at,
                    last_processed_id = excluded.last_processed_id
            """, (table_name, timestamp, last_id))
            conn.commit()

    def get_cached_metric(self, metric_type: str, metric_key: str) -> Optional[Any]:
        """Retrieve cached metric value."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT metric_value FROM analytics_cache WHERE metric_type = ? AND metric_key = ?",
                (metric_type, metric_key)
            ).fetchone()

            if row:
                try:
                    return json.loads(row['metric_value'])
                except json.JSONDecodeError:
                    return row['metric_value']
            return None

    def update_cached_metric(self, metric_type: str, metric_key: str, value: Any):
        """Update or insert cached metric."""
        json_value = json.dumps(value) if not isinstance(value, str) else value

        with self._connect() as conn:
            conn.execute("""
                INSERT INTO analytics_cache (metric_type, metric_key, metric_value)
                VALUES (?, ?, ?)
                ON CONFLICT(metric_type, metric_key) DO UPDATE SET
                    metric_value = excluded.metric_value,
                    last_updated = CURRENT_TIMESTAMP
            """, (metric_type, metric_key, json_value))
            conn.commit()

    def calculate_incremental_stats(self) -> Dict[str, Any]:
        """
        Calculate stats incrementally, only processing new/changed data.
        """
        logger.info("Starting incremental stats calculation")

        stats = {
            'timestamp': datetime.now().isoformat(),
            'type': 'incremental'
        }

        # Check if this is first run (no cached data)
        is_first_run = self._is_first_run()

        if is_first_run:
            logger.info("First run detected, performing full calculation")
            stats['type'] = 'full'
            return self._calculate_full_stats()

        # Calculate incremental updates
        stats.update({
            'total_stats': self._update_totals(),
            'recent_activity': self._calculate_recent_activity(),
            'model_usage': self._update_model_usage(),
            'tag_distribution': self._update_tag_distribution(),
            'time_series': self._update_time_series(),
            'performance_metrics': self._calculate_performance()
        })

        logger.info(f"Incremental stats calculation complete: {stats['type']}")
        return stats

    def _is_first_run(self) -> bool:
        """Check if this is the first run (no cached metrics)."""
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) as cnt FROM analytics_cache").fetchone()
            return count['cnt'] == 0

    def _calculate_full_stats(self) -> Dict[str, Any]:
        """Perform full statistics calculation (only on first run)."""
        stats = {}

        # Total counts
        with self._connect() as conn:
            # Prompts count
            prompt_count = conn.execute("SELECT COUNT(*) as cnt FROM prompts").fetchone()['cnt']
            self.update_cached_metric('totals', 'prompt_count', prompt_count)

            # Images count
            image_count = conn.execute("SELECT COUNT(*) as cnt FROM generated_images").fetchone()['cnt']
            self.update_cached_metric('totals', 'image_count', image_count)

            # Collections count
            collection_count = conn.execute("SELECT COUNT(*) as cnt FROM collections").fetchone()['cnt']
            self.update_cached_metric('totals', 'collection_count', collection_count)

            # Model usage statistics
            model_stats = conn.execute("""
                SELECT model_name, COUNT(*) as usage_count
                FROM prompts
                WHERE model_name IS NOT NULL
                GROUP BY model_name
                ORDER BY usage_count DESC
            """).fetchall()

            model_usage = {row['model_name']: row['usage_count'] for row in model_stats}
            self.update_cached_metric('models', 'usage', model_usage)

            # Tag distribution
            tag_stats = self._calculate_tag_distribution_full(conn)
            self.update_cached_metric('tags', 'distribution', tag_stats)

            # Time series data (last 30 days)
            time_series = self._calculate_time_series_full(conn)
            self.update_cached_metric('time_series', 'daily', time_series)

            # Update tracking timestamps
            now = datetime.now().isoformat()
            max_prompt_id = conn.execute("SELECT MAX(id) as max_id FROM prompts").fetchone()['max_id'] or 0
            max_image_id = conn.execute("SELECT MAX(id) as max_id FROM generated_images").fetchone()['max_id'] or 0

            self.update_last_processed('prompts', now, max_prompt_id)
            self.update_last_processed('generated_images', now, max_image_id)
            self.update_last_processed('collections', now, collection_count)

        stats['total_stats'] = {
            'prompts': prompt_count,
            'images': image_count,
            'collections': collection_count
        }
        stats['model_usage'] = model_usage
        stats['tag_distribution'] = tag_stats
        stats['time_series'] = time_series

        return stats

    def _update_totals(self) -> Dict[str, int]:
        """Update total counts incrementally."""
        last_prompt_time, last_prompt_id = self.get_last_processed('prompts')
        last_image_time, last_image_id = self.get_last_processed('generated_images')

        with self._connect() as conn:
            # Get cached totals
            prompt_count = self.get_cached_metric('totals', 'prompt_count') or 0
            image_count = self.get_cached_metric('totals', 'image_count') or 0
            collection_count = self.get_cached_metric('totals', 'collection_count') or 0

            # Count new prompts
            if last_prompt_id:
                new_prompts = conn.execute(
                    "SELECT COUNT(*) as cnt FROM prompts WHERE id > ?",
                    (last_prompt_id,)
                ).fetchone()['cnt']
                prompt_count += new_prompts

                if new_prompts > 0:
                    max_id = conn.execute("SELECT MAX(id) as max_id FROM prompts").fetchone()['max_id']
                    self.update_last_processed('prompts', datetime.now().isoformat(), max_id)

            # Count new images
            if last_image_id:
                new_images = conn.execute(
                    "SELECT COUNT(*) as cnt FROM generated_images WHERE id > ?",
                    (last_image_id,)
                ).fetchone()['cnt']
                image_count += new_images

                if new_images > 0:
                    max_id = conn.execute("SELECT MAX(id) as max_id FROM generated_images").fetchone()['max_id']
                    self.update_last_processed('generated_images', datetime.now().isoformat(), max_id)

            # Update collections count (simpler - just recount)
            collection_count = conn.execute("SELECT COUNT(*) as cnt FROM collections").fetchone()['cnt']

            # Update cache
            self.update_cached_metric('totals', 'prompt_count', prompt_count)
            self.update_cached_metric('totals', 'image_count', image_count)
            self.update_cached_metric('totals', 'collection_count', collection_count)

            logger.info(f"Updated totals - Prompts: {prompt_count}, Images: {image_count}, Collections: {collection_count}")

            return {
                'prompts': prompt_count,
                'images': image_count,
                'collections': collection_count
            }

    def _calculate_recent_activity(self) -> Dict[str, Any]:
        """Calculate recent activity (last 24 hours)."""
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()

        with self._connect() as conn:
            recent_prompts = conn.execute(
                "SELECT COUNT(*) as cnt FROM prompts WHERE created_at > ?",
                (cutoff,)
            ).fetchone()['cnt']

            recent_images = conn.execute(
                "SELECT COUNT(*) as cnt FROM generated_images WHERE created_at > ?",
                (cutoff,)
            ).fetchone()['cnt']

            return {
                'prompts_24h': recent_prompts,
                'images_24h': recent_images
            }

    def _update_model_usage(self) -> Dict[str, int]:
        """Update model usage statistics incrementally."""
        last_time, last_id = self.get_last_processed('prompts')
        cached_usage = self.get_cached_metric('models', 'usage') or {}

        with self._connect() as conn:
            if last_id:
                # Get new model usage
                new_usage = conn.execute("""
                    SELECT model_name, COUNT(*) as cnt
                    FROM prompts
                    WHERE id > ? AND model_name IS NOT NULL
                    GROUP BY model_name
                """, (last_id,)).fetchall()

                # Update cached counts
                for row in new_usage:
                    model = row['model_name']
                    cached_usage[model] = cached_usage.get(model, 0) + row['cnt']

            self.update_cached_metric('models', 'usage', cached_usage)
            return cached_usage

    def _update_tag_distribution(self) -> Dict[str, int]:
        """Update tag distribution incrementally."""
        last_time, last_id = self.get_last_processed('prompts')
        cached_tags = self.get_cached_metric('tags', 'distribution') or {}

        with self._connect() as conn:
            if last_id:
                # Get new tags
                new_prompts = conn.execute(
                    "SELECT tags FROM prompts WHERE id > ? AND tags IS NOT NULL",
                    (last_id,)
                ).fetchall()

                # Process new tags
                for row in new_prompts:
                    try:
                        tags = json.loads(row['tags'])
                        for tag in tags:
                            cached_tags[tag] = cached_tags.get(tag, 0) + 1
                    except (json.JSONDecodeError, TypeError):
                        continue

            self.update_cached_metric('tags', 'distribution', cached_tags)
            return cached_tags

    def _update_time_series(self) -> List[Dict[str, Any]]:
        """Update time series data for last 30 days."""
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()

        with self._connect() as conn:
            daily_stats = conn.execute("""
                SELECT
                    DATE(created_at) as date,
                    COUNT(*) as prompt_count
                FROM prompts
                WHERE created_at > ?
                GROUP BY DATE(created_at)
                ORDER BY date
            """, (cutoff,)).fetchall()

            time_series = [
                {'date': row['date'], 'prompts': row['prompt_count']}
                for row in daily_stats
            ]

            self.update_cached_metric('time_series', 'daily', time_series)
            return time_series

    def _calculate_tag_distribution_full(self, conn: sqlite3.Connection) -> Dict[str, int]:
        """Calculate full tag distribution (first run only)."""
        tags_count = {}
        rows = conn.execute("SELECT tags FROM prompts WHERE tags IS NOT NULL").fetchall()

        for row in rows:
            try:
                tags = json.loads(row['tags'])
                for tag in tags:
                    tags_count[tag] = tags_count.get(tag, 0) + 1
            except (json.JSONDecodeError, TypeError):
                continue

        return tags_count

    def _calculate_time_series_full(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        """Calculate full time series data (first run only)."""
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()

        daily_stats = conn.execute("""
            SELECT
                DATE(created_at) as date,
                COUNT(*) as prompt_count
            FROM prompts
            WHERE created_at > ?
            GROUP BY DATE(created_at)
            ORDER BY date
        """, (cutoff,)).fetchall()

        return [
            {'date': row['date'], 'prompts': row['prompt_count']}
            for row in daily_stats
        ]

    def _calculate_performance(self) -> Dict[str, Any]:
        """Calculate performance metrics."""
        with self._connect() as conn:
            # Get average generation times if available
            avg_time = conn.execute("""
                SELECT AVG(CAST(json_extract(metadata, '$.generation_time') AS REAL)) as avg_time
                FROM prompts
                WHERE metadata IS NOT NULL
                AND json_extract(metadata, '$.generation_time') IS NOT NULL
            """).fetchone()

            return {
                'avg_generation_time': avg_time['avg_time'] if avg_time['avg_time'] else 0,
                'cache_hit_rate': 0.95,  # Placeholder - would calculate from actual cache hits
                'incremental_processing': True
            }

    def clear_cache(self):
        """Clear all cached analytics data (forces full recalculation)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM analytics_cache")
            conn.execute("DELETE FROM analytics_tracking")
            conn.commit()
            logger.info("Analytics cache cleared")