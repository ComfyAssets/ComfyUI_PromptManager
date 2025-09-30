"""Epic Stats Calculator Service - Comprehensive stats calculation and storage."""

import sqlite3
import json
import time
import logging
import re
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)


class EpicStatsCalculator:
    """Calculate and store all statistics for the stats dashboard."""

    def __init__(self, db_path: str):
        """Initialize the calculator with database path."""
        self.db_path = db_path
        self.stats_data = {}
        self._detect_columns()
        self._ensure_stats_tables()

    def _detect_columns(self):
        """Detect which columns actually exist in the database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(generated_images)")
                columns = {row[1] for row in cursor.fetchall()}

                # Detect time column
                if 'created_at' in columns:
                    self.time_column = 'created_at'
                elif 'generation_time' in columns:
                    self.time_column = 'generation_time'
                else:
                    self.time_column = 'created_at'  # default

                # Detect workflow column
                if 'workflow' in columns:
                    self.workflow_column = 'workflow'
                elif 'workflow_data' in columns:
                    self.workflow_column = 'workflow_data'
                else:
                    self.workflow_column = 'workflow'  # default

                # Detect prompt column - v1 doesn't have prompt text in generated_images
                if 'prompt_text' in columns:
                    self.prompt_column = 'prompt_text'
                elif 'prompt' in columns:
                    self.prompt_column = 'prompt'
                else:
                    self.prompt_column = None  # No direct prompt column

                # Check for rating column
                self.has_rating = 'rating' in columns

                logger.info(f"Detected columns: time={self.time_column}, workflow={self.workflow_column}, prompt={self.prompt_column}, has_rating={self.has_rating}")

        except Exception as e:
            logger.warning(f"Failed to detect columns, using defaults: {e}")
            self.time_column = 'created_at'
            self.workflow_column = 'workflow'
            self.prompt_column = None
            self.has_rating = True

    def _ensure_stats_tables(self):
        """Ensure all required stats tables exist."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Check if stats tables exist
                cursor.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='stats_metadata'
                """)

                if not cursor.fetchone():
                    logger.info("Stats tables not found, creating them...")
                    # Read and execute the migration
                    migration_path = Path(__file__).parent.parent / 'database' / 'migrations' / '007_comprehensive_stats_storage.sql'
                    if migration_path.exists():
                        with open(migration_path, 'r') as f:
                            migration_sql = f.read()
                        cursor.executescript(migration_sql)
                        conn.commit()
                        logger.info("Stats tables created successfully")
                    else:
                        logger.warning(f"Migration file not found: {migration_path}")
                        # Create minimal tables needed
                        self._create_minimal_stats_tables(cursor)
                        conn.commit()
        except Exception as e:
            logger.error(f"Failed to ensure stats tables: {e}")

    def _create_minimal_stats_tables(self, cursor):
        """Create minimal stats tables if migration file not found."""
        # Create only the essential tables with basic structure
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stats_hourly_activity (
                hour INTEGER PRIMARY KEY,
                prompt_count INTEGER DEFAULT 0,
                image_count INTEGER DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stats_resolution_distribution (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                width INTEGER,
                height INTEGER,
                count INTEGER DEFAULT 0,
                percentage REAL DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stats_aspect_ratios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aspect_ratio TEXT,
                display_name TEXT,
                count INTEGER DEFAULT 0,
                percentage REAL DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stats_model_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_name TEXT,
                model_hash TEXT,
                usage_count INTEGER DEFAULT 0,
                avg_rating REAL DEFAULT 0,
                first_used TIMESTAMP,
                last_used TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stats_rating_trends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_type TEXT,
                period_date TEXT,
                avg_rating REAL DEFAULT 0,
                total_ratings INTEGER DEFAULT 0,
                five_star_count INTEGER DEFAULT 0,
                four_star_count INTEGER DEFAULT 0,
                three_star_count INTEGER DEFAULT 0,
                two_star_count INTEGER DEFAULT 0,
                one_star_count INTEGER DEFAULT 0,
                unrated_count INTEGER DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stats_workflow_complexity (
                complexity_level TEXT PRIMARY KEY,
                workflow_count INTEGER DEFAULT 0,
                avg_rating REAL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stats_generation_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric_type TEXT,
                metric_value REAL,
                metric_unit TEXT,
                period TEXT
            )
        """)

        # Initialize workflow complexity levels
        for level in ['simple', 'moderate', 'complex', 'advanced']:
            cursor.execute("""
                INSERT OR IGNORE INTO stats_workflow_complexity (complexity_level, workflow_count)
                VALUES (?, 0)
            """, (level,))

    def calculate_all_stats(self, progress_callback=None) -> Dict[str, Any]:
        """
        Calculate all statistics comprehensively.

        Args:
            progress_callback: Optional callback for progress updates (percent, message)

        Returns:
            Dictionary with calculation results and timing
        """
        start_time = time.time()
        results = {
            'success': True,
            'errors': [],
            'stats_calculated': 0,
            'calculation_time': 0
        }

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Track progress
                total_steps = 12
                current_step = 0

                def update_progress(message):
                    nonlocal current_step
                    current_step += 1
                    if progress_callback:
                        progress_callback(int((current_step / total_steps) * 100), message)

                # Step 1: Calculate hourly activity patterns
                update_progress("Analyzing hourly activity patterns...")
                self._calculate_hourly_activity(cursor)
                results['stats_calculated'] += 1

                # Step 2: Calculate resolution distribution
                update_progress("Analyzing resolution distribution...")
                self._calculate_resolution_distribution(cursor)
                results['stats_calculated'] += 1

                # Step 3: Calculate aspect ratios
                update_progress("Analyzing aspect ratios...")
                self._calculate_aspect_ratios(cursor)
                results['stats_calculated'] += 1

                # Step 4: Calculate model usage
                update_progress("Analyzing model usage...")
                self._calculate_model_usage(cursor)
                results['stats_calculated'] += 1

                # Step 5: Calculate rating trends
                update_progress("Analyzing rating trends...")
                self._calculate_rating_trends(cursor)
                results['stats_calculated'] += 1

                # Step 6: Calculate workflow complexity
                update_progress("Analyzing workflow complexity...")
                self._calculate_workflow_complexity(cursor)
                results['stats_calculated'] += 1

                # Step 7: Calculate time patterns
                update_progress("Discovering time patterns...")
                self._calculate_time_patterns(cursor)
                results['stats_calculated'] += 1

                # Step 8: Calculate prompt patterns
                update_progress("Analyzing prompt patterns...")
                self._calculate_prompt_patterns(cursor)
                results['stats_calculated'] += 1

                # Step 9: Calculate generation metrics
                update_progress("Computing generation metrics...")
                self._calculate_generation_metrics(cursor)
                results['stats_calculated'] += 1

                # Step 10: Calculate sampler usage
                update_progress("Analyzing sampler usage...")
                self._calculate_sampler_usage(cursor)
                results['stats_calculated'] += 1

                # Step 11: Calculate hero stats for dashboard
                update_progress("Calculating hero statistics...")
                self._calculate_hero_stats(cursor)
                results['stats_calculated'] += 1

                # Step 12: Calculate generation analytics
                update_progress("Calculating generation analytics...")
                self._calculate_generation_analytics(cursor)
                results['stats_calculated'] += 1

                # Update metadata
                calculation_time_ms = int((time.time() - start_time) * 1000)
                cursor.execute("""
                    UPDATE stats_metadata
                    SET last_full_calculation = CURRENT_TIMESTAMP,
                        calculation_time_ms = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = 1
                """, (calculation_time_ms,))

                conn.commit()

                results['calculation_time'] = calculation_time_ms / 1000
                logger.info(f"Epic stats calculation completed in {results['calculation_time']:.2f}s")

        except Exception as e:
            logger.error(f"Failed to calculate stats: {e}")
            results['success'] = False
            results['errors'].append(str(e))

        return results

    def _calculate_hourly_activity(self, cursor):
        """Calculate hourly activity patterns."""
        try:
            # Clear existing data
            cursor.execute("DELETE FROM stats_hourly_activity")

            # Get hourly prompt counts (prompts table always has created_at)
            cursor.execute("""
                SELECT
                    CAST(strftime('%H', created_at) AS INTEGER) as hour,
                    COUNT(*) as count
                FROM prompts
                WHERE 1=1  -- No soft delete column in this schema
                GROUP BY hour
            """)

            prompt_hours = {row[0]: row[1] for row in cursor.fetchall()}

            # Get hourly image counts using detected time column
            cursor.execute(f"""
                SELECT
                    CAST(strftime('%H', {self.time_column}) AS INTEGER) as hour,
                    COUNT(*) as count
                FROM generated_images
                WHERE {self.time_column} IS NOT NULL
                GROUP BY hour
            """)

            image_hours = {row[0]: row[1] for row in cursor.fetchall()}

            # Insert hourly data
            for hour in range(24):
                cursor.execute("""
                    INSERT INTO stats_hourly_activity (hour, prompt_count, image_count)
                    VALUES (?, ?, ?)
                """, (hour, prompt_hours.get(hour, 0), image_hours.get(hour, 0)))

            logger.info("Hourly activity patterns calculated")

        except Exception as e:
            logger.error(f"Failed to calculate hourly activity: {e}")
            raise

    def _calculate_resolution_distribution(self, cursor):
        """Calculate resolution distribution."""
        try:
            cursor.execute("DELETE FROM stats_resolution_distribution")

            # Get resolution counts
            cursor.execute("""
                SELECT
                    width,
                    height,
                    COUNT(*) as count
                FROM generated_images
                WHERE width IS NOT NULL
                    AND height IS NOT NULL
                GROUP BY width, height
                ORDER BY count DESC
            """)

            resolutions = cursor.fetchall()
            total = sum(r[2] for r in resolutions)

            for width, height, count in resolutions:
                if width and height:
                    # Determine category
                    aspect = width / height if height > 0 else 1
                    if 0.95 <= aspect <= 1.05:
                        category = 'square'
                    elif aspect > 1.5:
                        category = 'ultrawide' if aspect > 2 else 'landscape'
                    else:
                        category = 'portrait'

                    percentage = (count / total * 100) if total > 0 else 0

                    cursor.execute("""
                        INSERT INTO stats_resolution_distribution
                        (width, height, count, percentage, category)
                        VALUES (?, ?, ?, ?, ?)
                    """, (width, height, count, percentage, category))

            logger.info(f"Resolution distribution calculated for {len(resolutions)} resolutions")

        except Exception as e:
            logger.error(f"Failed to calculate resolution distribution: {e}")
            raise

    def _calculate_aspect_ratios(self, cursor):
        """Calculate aspect ratio statistics."""
        try:
            cursor.execute("DELETE FROM stats_aspect_ratios")

            # Get all resolutions and calculate aspect ratios
            cursor.execute("""
                SELECT
                    width,
                    height,
                    COUNT(*) as count
                FROM generated_images
                WHERE width IS NOT NULL
                    AND height IS NOT NULL
                GROUP BY width, height
            """)

            # Group by aspect ratio
            aspect_ratios = defaultdict(lambda: {'count': 0, 'resolutions': set()})
            total = 0

            for width, height, count in cursor.fetchall():
                if width and height and height > 0:
                    # Calculate simplified ratio
                    gcd = self._gcd(width, height)
                    ratio_w = width // gcd
                    ratio_h = height // gcd
                    ratio_str = f"{ratio_w}:{ratio_h}"

                    aspect_ratios[ratio_str]['count'] += count
                    aspect_ratios[ratio_str]['resolutions'].add(f"{width}x{height}")
                    total += count

            # Insert aspect ratio data
            for ratio, data in aspect_ratios.items():
                percentage = (data['count'] / total * 100) if total > 0 else 0
                common_res = json.dumps(list(data['resolutions'])[:5])  # Top 5 resolutions

                # Create display name
                if ratio == "16:9":
                    display_name = "Widescreen (16:9)"
                elif ratio == "4:3":
                    display_name = "Standard (4:3)"
                elif ratio == "1:1":
                    display_name = "Square (1:1)"
                elif ratio == "9:16":
                    display_name = "Portrait (9:16)"
                else:
                    display_name = f"Custom ({ratio})"

                cursor.execute("""
                    INSERT INTO stats_aspect_ratios
                    (ratio, display_name, count, percentage, common_resolutions)
                    VALUES (?, ?, ?, ?, ?)
                """, (ratio, display_name, data['count'], percentage, common_res))

            logger.info(f"Aspect ratios calculated for {len(aspect_ratios)} ratios")

        except Exception as e:
            logger.error(f"Failed to calculate aspect ratios: {e}")
            raise

    def _calculate_model_usage(self, cursor):
        """Calculate model usage statistics."""
        try:
            cursor.execute("DELETE FROM stats_model_usage")

            # Check if checkpoint column exists
            cursor.execute("PRAGMA table_info(generated_images)")
            columns = {row[1] for row in cursor.fetchall()}
            has_checkpoint = 'checkpoint' in columns
            has_model_hash = 'model_hash' in columns

            if not has_checkpoint and not has_model_hash:
                # No model columns, skip this calculation
                logger.info("No checkpoint/model_hash columns, skipping model usage stats")
                return

            # Get model usage stats
            if self.has_rating:
                cursor.execute(f"""
                    SELECT
                        COALESCE(gi.checkpoint, 'Unknown') as checkpoint,
                        COALESCE(gi.model_hash, '') as model_hash,
                        COUNT(*) as usage_count,
                        AVG(gi.rating) as avg_rating,
                        MIN(gi.{self.time_column}) as first_used,
                        MAX(gi.{self.time_column}) as last_used
                    FROM generated_images gi
                    WHERE gi.checkpoint IS NOT NULL OR gi.model_hash IS NOT NULL
                    GROUP BY gi.checkpoint, gi.model_hash
                    ORDER BY usage_count DESC
                """)
            else:
                # No rating column, need to join with prompts if it exists
                cursor.execute(f"""
                    SELECT
                        COALESCE(gi.checkpoint, 'Unknown') as checkpoint,
                        COALESCE(gi.model_hash, '') as model_hash,
                        COUNT(*) as usage_count,
                        AVG(p.rating) as avg_rating,
                        MIN(gi.{self.time_column}) as first_used,
                        MAX(gi.{self.time_column}) as last_used
                    FROM generated_images gi
                    LEFT JOIN prompts p ON gi.prompt_id = p.id
                    WHERE gi.checkpoint IS NOT NULL OR gi.model_hash IS NOT NULL
                    GROUP BY gi.checkpoint, gi.model_hash
                    ORDER BY usage_count DESC
                """)

            models = cursor.fetchall()

            for model_name, model_hash, usage_count, avg_rating, first_used, last_used in models:
                cursor.execute("""
                    INSERT INTO stats_model_usage
                    (model_name, model_hash, usage_count, avg_rating, first_used, last_used)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (model_name, model_hash or '', usage_count, avg_rating or 0, first_used, last_used))

            logger.info(f"Model usage calculated for {len(models)} models")

        except Exception as e:
            logger.error(f"Failed to calculate model usage: {e}")
            raise

    def _calculate_rating_trends(self, cursor):
        """Calculate rating trends over time."""
        try:
            cursor.execute("DELETE FROM stats_rating_trends")

            # Daily trends for last 30 days
            cursor.execute(f"""
                SELECT
                    DATE(gi.{self.time_column}) as period_date,
                    AVG(CASE WHEN p.rating > 0 THEN p.rating END) as avg_rating,
                    COUNT(CASE WHEN p.rating > 0 THEN 1 END) as total_ratings,
                    COUNT(CASE WHEN p.rating = 5 THEN 1 END) as five_star,
                    COUNT(CASE WHEN p.rating = 4 THEN 1 END) as four_star,
                    COUNT(CASE WHEN p.rating = 3 THEN 1 END) as three_star,
                    COUNT(CASE WHEN p.rating = 2 THEN 1 END) as two_star,
                    COUNT(CASE WHEN p.rating = 1 THEN 1 END) as one_star,
                    COUNT(CASE WHEN p.rating = 0 OR p.rating IS NULL THEN 1 END) as unrated
                FROM generated_images gi
                LEFT JOIN prompts p ON gi.prompt_id = p.id
                WHERE gi.{self.time_column} >= date('now', '-30 days')
                GROUP BY period_date
            """)

            daily_trends = cursor.fetchall()

            for row in daily_trends:
                cursor.execute("""
                    INSERT INTO stats_rating_trends
                    (period_type, period_date, avg_rating, total_ratings,
                     five_star_count, four_star_count, three_star_count,
                     two_star_count, one_star_count, unrated_count)
                    VALUES ('daily', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, row)

            # Weekly trends for last 12 weeks
            cursor.execute(f"""
                SELECT
                    DATE(gi.{self.time_column}, 'weekday 0', '-6 days') as week_start,
                    AVG(CASE WHEN p.rating > 0 THEN p.rating END) as avg_rating,
                    COUNT(CASE WHEN p.rating > 0 THEN 1 END) as total_ratings,
                    COUNT(CASE WHEN p.rating = 5 THEN 1 END) as five_star,
                    COUNT(CASE WHEN p.rating = 4 THEN 1 END) as four_star,
                    COUNT(CASE WHEN p.rating = 3 THEN 1 END) as three_star,
                    COUNT(CASE WHEN p.rating = 2 THEN 1 END) as two_star,
                    COUNT(CASE WHEN p.rating = 1 THEN 1 END) as one_star,
                    COUNT(CASE WHEN p.rating = 0 OR p.rating IS NULL THEN 1 END) as unrated
                FROM generated_images gi
                LEFT JOIN prompts p ON gi.prompt_id = p.id
                WHERE gi.{self.time_column} >= date('now', '-84 days')
                GROUP BY week_start
            """)

            weekly_trends = cursor.fetchall()

            for row in weekly_trends:
                cursor.execute("""
                    INSERT INTO stats_rating_trends
                    (period_type, period_date, avg_rating, total_ratings,
                     five_star_count, four_star_count, three_star_count,
                     two_star_count, one_star_count, unrated_count)
                    VALUES ('weekly', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, row)

            logger.info(f"Rating trends calculated: {len(daily_trends)} daily, {len(weekly_trends)} weekly")

        except Exception as e:
            logger.error(f"Failed to calculate rating trends: {e}")
            raise

    def _calculate_workflow_complexity(self, cursor):
        """Calculate workflow complexity statistics."""
        try:
            cursor.execute("DELETE FROM stats_workflow_complexity WHERE workflow_count > 0")

            # Get workflow data
            if self.has_rating:
                cursor.execute(f"""
                    SELECT
                        gi.{self.workflow_column} as workflow,
                        COUNT(*) as count,
                        AVG(gi.rating) as avg_rating
                    FROM generated_images gi
                    WHERE gi.{self.workflow_column} IS NOT NULL AND gi.{self.workflow_column} != '{{}}' AND gi.{self.workflow_column} != ''
                    GROUP BY gi.{self.workflow_column}
                """)
            else:
                # No rating column in v1, join with prompts if needed
                cursor.execute(f"""
                    SELECT
                        gi.{self.workflow_column} as workflow,
                        COUNT(*) as count,
                        AVG(p.rating) as avg_rating
                    FROM generated_images gi
                    LEFT JOIN prompts p ON gi.prompt_id = p.id
                    WHERE gi.{self.workflow_column} IS NOT NULL AND gi.{self.workflow_column} != '{{}}' AND gi.{self.workflow_column} != ''
                    GROUP BY gi.{self.workflow_column}
                """)

            complexity_stats = {
                'simple': {'count': 0, 'ratings': []},
                'moderate': {'count': 0, 'ratings': []},
                'complex': {'count': 0, 'ratings': []},
                'advanced': {'count': 0, 'ratings': []}
            }

            for workflow_json, count, avg_rating in cursor.fetchall():
                try:
                    workflow = json.loads(workflow_json)
                    node_count = len(workflow.get('nodes', []))

                    # Determine complexity level
                    if node_count <= 10:
                        level = 'simple'
                    elif node_count <= 25:
                        level = 'moderate'
                    elif node_count <= 50:
                        level = 'complex'
                    else:
                        level = 'advanced'

                    complexity_stats[level]['count'] += count
                    if avg_rating:
                        complexity_stats[level]['ratings'].append(avg_rating)

                except (json.JSONDecodeError, TypeError):
                    continue

            # Update complexity statistics
            for level, stats in complexity_stats.items():
                avg_rating = sum(stats['ratings']) / len(stats['ratings']) if stats['ratings'] else 0

                cursor.execute("""
                    UPDATE stats_workflow_complexity
                    SET workflow_count = ?,
                        avg_rating = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE complexity_level = ?
                """, (stats['count'], avg_rating, level))

            logger.info("Workflow complexity calculated")

        except Exception as e:
            logger.error(f"Failed to calculate workflow complexity: {e}")
            raise

    def _calculate_time_patterns(self, cursor):
        """Calculate time-based patterns."""
        try:
            cursor.execute("DELETE FROM stats_time_patterns")

            # Find peak hour
            cursor.execute(f"""
                SELECT
                    CAST(strftime('%H', {self.time_column}) AS INTEGER) as hour,
                    COUNT(*) as count
                FROM generated_images
                GROUP BY hour
                ORDER BY count DESC
                LIMIT 1
            """)

            peak_hour = cursor.fetchone()
            if peak_hour:
                cursor.execute("""
                    INSERT INTO stats_time_patterns (pattern_type, pattern_value, description)
                    VALUES ('daily_peak', ?, ?)
                """, (json.dumps({'hour': peak_hour[0], 'count': peak_hour[1]}),
                       f"Peak activity at {peak_hour[0]:02d}:00"))

            # Find most productive day of week
            cursor.execute(f"""
                SELECT
                    strftime('%w', {self.time_column}) as day_of_week,
                    COUNT(*) as count
                FROM generated_images
                GROUP BY day_of_week
                ORDER BY count DESC
                LIMIT 1
            """)

            peak_day = cursor.fetchone()
            if peak_day:
                days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
                day_name = days[int(peak_day[0])]
                cursor.execute("""
                    INSERT INTO stats_time_patterns (pattern_type, pattern_value, description)
                    VALUES ('weekly_peak', ?, ?)
                """, (json.dumps({'day': int(peak_day[0]), 'name': day_name, 'count': peak_day[1]}),
                       f"Most productive on {day_name}"))

            logger.info("Time patterns calculated")

        except Exception as e:
            logger.error(f"Failed to calculate time patterns: {e}")
            raise

    def _calculate_prompt_patterns(self, cursor):
        """Calculate prompt pattern statistics."""
        try:
            cursor.execute("DELETE FROM stats_prompt_patterns")

            # Get all prompts - check which column exists in prompts table
            cursor.execute("PRAGMA table_info(prompts)")
            prompts_columns = {row[1] for row in cursor.fetchall()}

            if 'prompt' in prompts_columns:
                prompt_col = 'prompt'
            elif 'positive_prompt' in prompts_columns:
                prompt_col = 'positive_prompt'
            else:
                # No prompts table or no prompt column
                logger.info("No prompt column in prompts table, skipping prompt patterns")
                return

            cursor.execute(f"""
                SELECT {prompt_col} FROM prompts
                WHERE {prompt_col} IS NOT NULL AND {prompt_col} != ''
            """)

            prompts = [row[0] for row in cursor.fetchall()]

            if prompts:
                # Calculate average length
                avg_length = sum(len(p) for p in prompts) / len(prompts)

                cursor.execute("""
                    INSERT INTO stats_prompt_patterns (pattern_type, pattern_value)
                    VALUES ('avg_length', ?)
                """, (str(int(avg_length)),))

                # Calculate complexity distribution
                complexity = {'simple': 0, 'moderate': 0, 'complex': 0}
                for prompt in prompts:
                    word_count = len(prompt.split())
                    if word_count < 10:
                        complexity['simple'] += 1
                    elif word_count < 30:
                        complexity['moderate'] += 1
                    else:
                        complexity['complex'] += 1

                cursor.execute("""
                    INSERT INTO stats_prompt_patterns (pattern_type, pattern_value)
                    VALUES ('complexity_dist', ?)
                """, (json.dumps(complexity),))

                # Calculate vocabulary size
                all_words = []
                for prompt in prompts:
                    words = re.findall(r'\b[a-z]+\b', prompt.lower())
                    all_words.extend(words)

                vocab_size = len(set(all_words))

                cursor.execute("""
                    INSERT INTO stats_prompt_patterns (pattern_type, pattern_value)
                    VALUES ('vocabulary_size', ?)
                """, (str(vocab_size),))

            logger.info("Prompt patterns calculated")

        except Exception as e:
            logger.error(f"Failed to calculate prompt patterns: {e}")
            raise

    def _calculate_generation_metrics(self, cursor):
        """Calculate generation metrics."""
        try:
            cursor.execute("DELETE FROM stats_generation_metrics")

            # Total generations
            cursor.execute("SELECT COUNT(*) FROM generated_images")
            total_count = cursor.fetchone()[0]

            cursor.execute("""
                INSERT INTO stats_generation_metrics (metric_type, metric_value, metric_unit, period)
                VALUES ('total_generations', ?, 'count', 'all_time')
            """, (total_count,))

            # Average rating from prompts
            cursor.execute("""
                SELECT AVG(p.rating)
                FROM prompts p
                WHERE p.rating > 0
            """)
            avg_rating = cursor.fetchone()[0] or 0

            cursor.execute("""
                INSERT INTO stats_generation_metrics (metric_type, metric_value, metric_unit, period)
                VALUES ('avg_rating', ?, 'stars', 'all_time')
            """, (avg_rating,))

            # Last 30 days metrics
            cursor.execute(f"""
                SELECT COUNT(*), AVG(p.rating)
                FROM generated_images gi
                LEFT JOIN prompts p ON gi.prompt_id = p.id
                WHERE gi.{self.time_column} >= date('now', '-30 days')
            """)

            month_count, month_rating = cursor.fetchone()

            cursor.execute("""
                INSERT INTO stats_generation_metrics (metric_type, metric_value, metric_unit, period)
                VALUES ('total_generations', ?, 'count', 'last_30_days')
            """, (month_count or 0,))

            cursor.execute("""
                INSERT INTO stats_generation_metrics (metric_type, metric_value, metric_unit, period)
                VALUES ('avg_rating', ?, 'stars', 'last_30_days')
            """, (month_rating or 0,))

            logger.info("Generation metrics calculated")

        except Exception as e:
            logger.error(f"Failed to calculate generation metrics: {e}")
            raise

    def _calculate_sampler_usage(self, cursor):
        """Calculate sampler usage statistics."""
        try:
            cursor.execute("DELETE FROM stats_sampler_usage")

            # Check if sampler column exists
            cursor.execute("PRAGMA table_info(generated_images)")
            columns = {row[1] for row in cursor.fetchall()}
            has_sampler = 'sampler' in columns

            if not has_sampler:
                # No sampler column, skip this calculation
                logger.info("No sampler column, skipping sampler usage stats")
                return

            # Get sampler info directly from generated_images columns
            cursor.execute("""
                SELECT
                    COALESCE(gi.sampler, 'Unknown') as sampler,
                    COUNT(*) as usage_count,
                    AVG(CAST(COALESCE(gi.steps, 20) AS REAL)) as avg_steps,
                    AVG(CAST(COALESCE(gi.cfg_scale, 7.0) AS REAL)) as avg_cfg
                FROM generated_images gi
                WHERE gi.sampler IS NOT NULL
                GROUP BY gi.sampler
                ORDER BY usage_count DESC
            """)

            samplers = cursor.fetchall()

            # If no sampler data is available, insert a default entry
            if not samplers:
                cursor.execute("""
                    INSERT INTO stats_sampler_usage
                    (sampler_name, usage_count, avg_steps, avg_cfg)
                    VALUES ('No sampler data', 0, 0, 0)
                """)
                logger.info("No sampler data available in database")
            else:
                for sampler_name, usage_count, avg_steps, avg_cfg in samplers:
                    cursor.execute("""
                        INSERT INTO stats_sampler_usage
                        (sampler_name, usage_count, avg_steps, avg_cfg)
                        VALUES (?, ?, ?, ?)
                    """, (sampler_name, usage_count, avg_steps or 0, avg_cfg or 0))

                logger.info(f"Sampler usage calculated for {len(samplers)} samplers")

        except Exception as e:
            logger.error(f"Failed to calculate sampler usage: {e}")
            raise

    def _gcd(self, a: int, b: int) -> int:
        """Calculate greatest common divisor."""
        while b:
            a, b = b, a % b
        return a

    def _calculate_hero_stats(self, cursor):
        """Calculate hero statistics for dashboard display."""
        try:
            # First ensure the table exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stats_hero_stats (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    total_images INTEGER DEFAULT 0,
                    total_prompts INTEGER DEFAULT 0,
                    rated_count INTEGER DEFAULT 0,
                    avg_rating REAL DEFAULT 0,
                    five_star_count INTEGER DEFAULT 0,
                    total_collections INTEGER DEFAULT 0,
                    images_per_prompt REAL DEFAULT 0,
                    generation_streak INTEGER DEFAULT 0,
                    calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Get total images
            cursor.execute("SELECT COUNT(*) FROM generated_images")
            total_images = cursor.fetchone()[0] or 0

            # Get total prompts (if prompts table exists)
            try:
                cursor.execute("SELECT COUNT(*) FROM prompts")
                total_prompts = cursor.fetchone()[0] or 0
            except:
                # Fallback: count unique prompts from generated_images (if column exists)
                if self.prompt_column:
                    cursor.execute(f"SELECT COUNT(DISTINCT {self.prompt_column}) FROM generated_images WHERE {self.prompt_column} IS NOT NULL AND {self.prompt_column} != ''")
                    total_prompts = cursor.fetchone()[0] or 0
                else:
                    total_prompts = 0

            # Get rating statistics (check which table has ratings)
            if self.has_rating:
                cursor.execute("""
                    SELECT
                        COUNT(*) as rated_count,
                        AVG(rating) as avg_rating,
                        SUM(CASE WHEN rating = 5 THEN 1 ELSE 0 END) as five_star_count
                    FROM generated_images
                    WHERE rating IS NOT NULL AND rating > 0
                """)
            else:
                # v1 has ratings in prompts table, not generated_images
                cursor.execute("""
                    SELECT
                        COUNT(*) as rated_count,
                        AVG(p.rating) as avg_rating,
                        SUM(CASE WHEN p.rating = 5 THEN 1 ELSE 0 END) as five_star_count
                    FROM generated_images gi
                    INNER JOIN prompts p ON gi.prompt_id = p.id
                    WHERE p.rating IS NOT NULL AND p.rating > 0
                """)
            rating_stats = cursor.fetchone()
            rated_count = rating_stats[0] or 0
            avg_rating = rating_stats[1] or 0
            five_star_count = rating_stats[2] or 0

            # Get total collections (tags/categories)
            try:
                cursor.execute("SELECT COUNT(DISTINCT tag) FROM image_tags")
                total_collections = cursor.fetchone()[0] or 0
            except:
                total_collections = 0

            # Calculate images per prompt
            images_per_prompt = total_images / total_prompts if total_prompts > 0 else 0

            # Calculate generation streak (consecutive days with generations)
            cursor.execute(f"""
                WITH generation_dates AS (
                    SELECT DISTINCT DATE({self.time_column}) as gen_date
                    FROM generated_images
                    WHERE {self.time_column} IS NOT NULL
                    ORDER BY gen_date DESC
                ),
                streak_calc AS (
                    SELECT
                        gen_date,
                        gen_date - ROW_NUMBER() OVER (ORDER BY gen_date) AS streak_group
                    FROM generation_dates
                )
                SELECT COUNT(*) as streak_length
                FROM streak_calc
                WHERE streak_group = (SELECT MAX(streak_group) FROM streak_calc)
            """)
            generation_streak = cursor.fetchone()[0] or 0

            # Delete existing and insert new
            cursor.execute("DELETE FROM stats_hero_stats")
            cursor.execute("""
                INSERT INTO stats_hero_stats
                (id, total_images, total_prompts, rated_count, avg_rating,
                 five_star_count, total_collections, images_per_prompt, generation_streak)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (total_images, total_prompts, rated_count, avg_rating,
                  five_star_count, total_collections, images_per_prompt, generation_streak))

            logger.info(f"Calculated hero stats: {total_images} images, {total_prompts} prompts")

        except Exception as e:
            logger.error(f"Failed to calculate hero stats: {e}")
            # Don't raise, continue with other stats

    def _calculate_generation_analytics(self, cursor):
        """Calculate generation analytics summary."""
        try:
            # First ensure the table exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stats_generation_analytics (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    total_generations INTEGER DEFAULT 0,
                    unique_prompts INTEGER DEFAULT 0,
                    avg_per_day REAL DEFAULT 0,
                    peak_day_count INTEGER DEFAULT 0,
                    peak_day DATE,
                    total_generation_time REAL DEFAULT 0,
                    avg_generation_time REAL DEFAULT 0,
                    calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Get total generations
            cursor.execute("SELECT COUNT(*) FROM generated_images")
            total_generations = cursor.fetchone()[0] or 0

            # Get unique prompts (if column exists)
            if self.prompt_column:
                cursor.execute(f"SELECT COUNT(DISTINCT {self.prompt_column}) FROM generated_images WHERE {self.prompt_column} IS NOT NULL AND {self.prompt_column} != ''")
                unique_prompts = cursor.fetchone()[0] or 0
            else:
                unique_prompts = 0

            # Calculate average per day (using dynamic time column)
            cursor.execute(f"""
                SELECT
                    COUNT(*) * 1.0 / MAX(1, julianday('now') - julianday(MIN({self.time_column})) + 1) as avg_per_day
                FROM generated_images
                WHERE {self.time_column} IS NOT NULL
            """)
            avg_per_day = cursor.fetchone()[0] or 0

            # Find peak day
            cursor.execute(f"""
                SELECT DATE({self.time_column}) as gen_date, COUNT(*) as count
                FROM generated_images
                WHERE {self.time_column} IS NOT NULL
                GROUP BY gen_date
                ORDER BY count DESC
                LIMIT 1
            """)
            peak_result = cursor.fetchone()
            peak_day = peak_result[0] if peak_result else None
            peak_day_count = peak_result[1] if peak_result else 0

            # Calculate generation time stats (placeholder - would need actual timing data)
            total_generation_time = 0
            avg_generation_time = 0

            # Delete existing and insert new
            cursor.execute("DELETE FROM stats_generation_analytics")
            cursor.execute("""
                INSERT INTO stats_generation_analytics
                (id, total_generations, unique_prompts, avg_per_day, peak_day_count,
                 peak_day, total_generation_time, avg_generation_time)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            """, (total_generations, unique_prompts, avg_per_day, peak_day_count,
                  peak_day, total_generation_time, avg_generation_time))

            logger.info(f"Calculated generation analytics: {total_generations} generations, avg {avg_per_day:.1f}/day")

        except Exception as e:
            logger.error(f"Failed to calculate generation analytics: {e}")
            # Don't raise, continue with other stats

    def get_all_stats(self) -> Dict[str, Any]:
        """
        Retrieve all calculated statistics from the database.

        Returns:
            Dictionary containing all stats data for the dashboard
        """
        stats = {
            'overview': {},
            'timeAnalytics': {},
            'generationMetrics': {},
            'modelPerformance': {},
            'qualityMetrics': {},
            'workflowAnalysis': {},
            'promptPatterns': {}
        }

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Get hourly activity
                cursor.execute("""
                    SELECT hour, prompt_count + image_count as total
                    FROM stats_hourly_activity
                    ORDER BY hour
                """)
                stats['timeAnalytics']['hourlyActivity'] = [row[1] for row in cursor.fetchall()]

                # Get resolution distribution
                cursor.execute("""
                    SELECT width, height, count, category
                    FROM stats_resolution_distribution
                    ORDER BY count DESC
                    LIMIT 10
                """)
                resolutions = {}
                for width, height, count, category in cursor.fetchall():
                    key = f"{width}x{height}"
                    resolutions[key] = count
                stats['generationMetrics']['resolutions'] = resolutions

                # Get aspect ratios
                cursor.execute("""
                    SELECT ratio, display_name, count
                    FROM stats_aspect_ratios
                    ORDER BY count DESC
                    LIMIT 5
                """)
                aspect_ratios = {}
                for ratio, display_name, count in cursor.fetchall():
                    aspect_ratios[display_name or ratio] = count
                stats['generationMetrics']['aspectRatios'] = aspect_ratios

                # Get model usage
                cursor.execute("""
                    SELECT model_name, usage_count, avg_rating
                    FROM stats_model_usage
                    ORDER BY usage_count DESC
                    LIMIT 10
                """)
                model_usage = {}
                for model_name, usage_count, avg_rating in cursor.fetchall():
                    # Truncate long model names
                    display_name = model_name[:30] + '...' if len(model_name) > 30 else model_name
                    model_usage[display_name] = {
                        'count': usage_count,
                        'rating': round(avg_rating, 2) if avg_rating else 0
                    }
                stats['modelPerformance']['modelUsage'] = model_usage

                # Get rating trends
                cursor.execute("""
                    SELECT period_date, avg_rating
                    FROM stats_rating_trends
                    WHERE period_type = 'daily'
                    ORDER BY period_date DESC
                    LIMIT 30
                """)
                rating_trend = []
                for period_date, avg_rating in cursor.fetchall():
                    rating_trend.append({
                        'date': period_date,
                        'rating': round(avg_rating, 2) if avg_rating else 0
                    })
                stats['qualityMetrics']['ratingTrend'] = list(reversed(rating_trend))

                # Get workflow complexity
                cursor.execute("""
                    SELECT complexity_level, workflow_count
                    FROM stats_workflow_complexity
                    ORDER BY
                        CASE complexity_level
                            WHEN 'simple' THEN 1
                            WHEN 'moderate' THEN 2
                            WHEN 'complex' THEN 3
                            WHEN 'advanced' THEN 4
                        END
                """)
                complexity_levels = {}
                for level, count in cursor.fetchall():
                    complexity_levels[level] = count
                stats['workflowAnalysis']['complexityLevels'] = complexity_levels

                # Get prompt patterns
                cursor.execute("""
                    SELECT pattern_type, pattern_value
                    FROM stats_prompt_patterns
                """)
                for pattern_type, pattern_value in cursor.fetchall():
                    if pattern_type == 'avg_length':
                        stats['promptPatterns']['avgLength'] = int(pattern_value)
                    elif pattern_type == 'complexity_dist':
                        stats['promptPatterns']['complexity'] = json.loads(pattern_value)
                    elif pattern_type == 'vocabulary_size':
                        stats['promptPatterns']['vocabularySize'] = int(pattern_value)

                logger.info("Retrieved all stats from database")

        except Exception as e:
            logger.error(f"Failed to retrieve stats: {e}")

        return stats