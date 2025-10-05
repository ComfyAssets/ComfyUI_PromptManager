"""
Incremental Stats Service - Optimized analytics with delta updates.
Only processes new/changed data instead of full recalculation.
"""

import json
import sqlite3
from typing import Dict, Any, Optional, List, Tuple, Set
from datetime import datetime, timedelta
from pathlib import Path

from ..database import PromptDatabase
from ..config import Config

try:  # pragma: no cover - import path differs between runtime contexts
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger(__name__)


class IncrementalStatsService:
    """
    Optimized stats service that uses incremental updates.
    First run performs full calculation, subsequent runs only process deltas.
    """

    def __init__(self, database: Optional[PromptDatabase] = None, config: Optional[Config] = None):
        """Initialize the incremental stats service."""
        self.db = database or PromptDatabase()
        self.config = config or Config()
        self.db_path = self.config.database.path
        self._table_columns_cache: Dict[str, Set[str]] = {}
        self._ensure_cache_table()

    def _get_table_columns(
        self,
        table: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Set[str]:
        """Return cached column names for the requested table."""

        if table in self._table_columns_cache:
            return self._table_columns_cache[table]

        close_conn = False
        if conn is None:
            conn = self._connect()
            close_conn = True

        try:
            cursor = conn.execute(f"PRAGMA table_info({table})")
            columns: Set[str] = set()
            for row in cursor.fetchall():
                if isinstance(row, sqlite3.Row):
                    columns.add(row["name"])
                else:
                    columns.add(row[1])
            self._table_columns_cache[table] = columns
            return columns
        finally:
            if close_conn and conn is not None:
                conn.close()

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

            # Collections count (check if table exists first)
            try:
                # Check if collections table exists
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='collections'")
                if cursor.fetchone():
                    collection_count = conn.execute("SELECT COUNT(*) as cnt FROM collections").fetchone()['cnt']
                else:
                    collection_count = 0
                self.update_cached_metric('totals', 'collection_count', collection_count)
            except Exception as e:
                LOGGER.warning(f"Failed to count collections: {e}")
                self.update_cached_metric('totals', 'collection_count', 0)

            # Model usage statistics (check if column exists first)
            try:
                # Check if model_name column exists
                cursor = conn.execute("PRAGMA table_info(prompts)")
                columns = {row[1] for row in cursor.fetchall()}
                
                if 'model_name' in columns:
                    model_stats = conn.execute("""
                        SELECT model_name, COUNT(*) as usage_count
                        FROM prompts
                        WHERE model_name IS NOT NULL
                        GROUP BY model_name
                        ORDER BY usage_count DESC
                    """).fetchall()
                    model_usage = {row['model_name']: row['usage_count'] for row in model_stats}
                elif 'model_hash' in columns:
                    # Fallback to model_hash for v1 databases
                    model_stats = conn.execute("""
                        SELECT model_hash, COUNT(*) as usage_count
                        FROM prompts
                        WHERE model_hash IS NOT NULL
                        GROUP BY model_hash
                        ORDER BY usage_count DESC
                    """).fetchall()
                    model_usage = {row['model_hash']: row['usage_count'] for row in model_stats}
                else:
                    model_usage = {}
                
                self.update_cached_metric('models', 'usage', model_usage)
            except Exception as e:
                LOGGER.warning(f"Failed to calculate model stats: {e}")
                self.update_cached_metric('models', 'usage', {})

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

            # Update collections count (check if table exists)
            try:
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='collections'")
                if cursor.fetchone():
                    collection_count = conn.execute("SELECT COUNT(*) as cnt FROM collections").fetchone()['cnt']
                else:
                    collection_count = 0
            except Exception:
                collection_count = 0

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

            # Check for generation_time or created_at column
            try:
                cursor = conn.execute("PRAGMA table_info(generated_images)")
                columns = {row[1] for row in cursor.fetchall()}
                
                if 'generation_time' in columns:
                    recent_images = conn.execute(
                        "SELECT COUNT(*) as cnt FROM generated_images WHERE generation_time > ?",
                        (cutoff,)
                    ).fetchone()['cnt']
                elif 'created_at' in columns:
                    recent_images = conn.execute(
                        "SELECT COUNT(*) as cnt FROM generated_images WHERE created_at > ?",
                        (cutoff,)
                    ).fetchone()['cnt']
                else:
                    recent_images = 0
            except Exception:
                recent_images = 0

            return {
                'prompts_24h': recent_prompts,
                'images_24h': recent_images
            }

    def _update_model_usage(self) -> Dict[str, int]:
        """Update model usage statistics incrementally."""
        last_time, last_id = self.get_last_processed('prompts')
        cached_usage = self.get_cached_metric('models', 'usage') or {}

        with self._connect() as conn:
            # Check if model_name or model_hash column exists
            cursor = conn.execute("PRAGMA table_info(prompts)")
            columns = {row[1] for row in cursor.fetchall()}
            
            if last_id:
                # Get new model usage - check which column exists
                if 'model_name' in columns:
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
                        
                elif 'model_hash' in columns:
                    # Fallback to model_hash for v1 databases
                    new_usage = conn.execute("""
                        SELECT model_hash, COUNT(*) as cnt
                        FROM prompts
                        WHERE id > ? AND model_hash IS NOT NULL
                        GROUP BY model_hash
                    """, (last_id,)).fetchall()
                    
                    # Update cached counts
                    for row in new_usage:
                        model = row['model_hash']
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
            avg_generation_time = 0.0

            try:
                row = None
                prompt_columns = self._get_table_columns("prompts", conn)
                if "metadata" in prompt_columns:
                    row = conn.execute("""
                        SELECT AVG(CAST(json_extract(metadata, '$.generation_time') AS REAL)) AS avg_time
                        FROM prompts
                        WHERE metadata IS NOT NULL
                          AND json_extract(metadata, '$.generation_time') IS NOT NULL
                    """).fetchone()
                elif "generation_params" in prompt_columns:
                    row = conn.execute("""
                        SELECT AVG(CAST(json_extract(generation_params, '$.generation_time') AS REAL)) AS avg_time
                        FROM prompts
                        WHERE generation_params IS NOT NULL
                          AND json_extract(generation_params, '$.generation_time') IS NOT NULL
                    """).fetchone()

                # Fall back to generated_images metadata if prompt fields are absent
                if (not row or row["avg_time"] is None):
                    image_columns = self._get_table_columns("generated_images", conn)
                    if "metadata" in image_columns:
                        row = conn.execute("""
                            SELECT AVG(CAST(json_extract(metadata, '$.generation_time') AS REAL)) AS avg_time
                            FROM generated_images
                            WHERE metadata IS NOT NULL
                              AND json_extract(metadata, '$.generation_time') IS NOT NULL
                        """).fetchone()
                    elif "parameters" in image_columns:
                        row = conn.execute("""
                            SELECT AVG(CAST(json_extract(parameters, '$.generation_time') AS REAL)) AS avg_time
                            FROM generated_images
                            WHERE parameters IS NOT NULL
                              AND json_extract(parameters, '$.generation_time') IS NOT NULL
                        """).fetchone()

                if row and row["avg_time"] is not None:
                    avg_generation_time = float(row["avg_time"])
            except sqlite3.Error as exc:  # pragma: no cover - defensive logging
                logger.debug("Unable to calculate avg generation time: %s", exc)

            return {
                'avg_generation_time': avg_generation_time if avg_generation_time else 0,
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
