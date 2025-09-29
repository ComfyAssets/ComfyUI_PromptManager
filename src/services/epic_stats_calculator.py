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
        self._ensure_stats_tables()

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
                total_steps = 10
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

            # Get hourly prompt counts
            cursor.execute("""
                SELECT
                    CAST(strftime('%H', created_at) AS INTEGER) as hour,
                    COUNT(*) as count
                FROM prompts
                WHERE 1=1  -- No soft delete column in this schema
                GROUP BY hour
            """)

            prompt_hours = {row[0]: row[1] for row in cursor.fetchall()}

            # Get hourly image counts
            cursor.execute("""
                SELECT
                    CAST(strftime('%H', generation_time) AS INTEGER) as hour,
                    COUNT(*) as count
                FROM generated_images
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

            # Get model usage stats - note: generated_images doesn't have checkpoint or model_hash columns
            # We'll need to extract this from metadata if available
            cursor.execute("""
                SELECT
                    COALESCE(json_extract(gi.parameters, '$.checkpoint'), 'Unknown') as checkpoint,
                    COALESCE(json_extract(gi.parameters, '$.model_hash'), '') as model_hash,
                    COUNT(*) as usage_count,
                    AVG(p.rating) as avg_rating,
                    MIN(gi.generation_time) as first_used,
                    MAX(gi.generation_time) as last_used
                FROM generated_images gi
                LEFT JOIN prompts p ON gi.prompt_id = p.id
                GROUP BY checkpoint
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
            cursor.execute("""
                SELECT
                    DATE(gi.generation_time) as period_date,
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
                WHERE gi.generation_time >= date('now', '-30 days')
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
            cursor.execute("""
                SELECT
                    DATE(gi.generation_time, 'weekday 0', '-6 days') as week_start,
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
                WHERE gi.generation_time >= date('now', '-84 days')
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
            cursor.execute("""
                SELECT
                    gi.workflow_data as workflow,
                    COUNT(*) as count,
                    AVG(p.rating) as avg_rating
                FROM generated_images gi
                LEFT JOIN prompts p ON gi.prompt_id = p.id
                WHERE gi.workflow_data IS NOT NULL AND gi.workflow_data != '{}'
                GROUP BY gi.workflow_data
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
            cursor.execute("""
                SELECT
                    CAST(strftime('%H', generation_time) AS INTEGER) as hour,
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
            cursor.execute("""
                SELECT
                    strftime('%w', generation_time) as day_of_week,
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

            # Get all prompts
            cursor.execute("""
                SELECT positive_prompt FROM prompts
                WHERE positive_prompt IS NOT NULL
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
            cursor.execute("""
                SELECT COUNT(*), AVG(p.rating)
                FROM generated_images gi
                LEFT JOIN prompts p ON gi.prompt_id = p.id
                WHERE gi.generation_time >= date('now', '-30 days')
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

            # Try to extract sampler info from parameters JSON or prompts table
            cursor.execute("""
                SELECT
                    COALESCE(
                        json_extract(gi.parameters, '$.sampler'),
                        json_extract(p.sampler_settings, '$.sampler_name'),
                        'Unknown'
                    ) as sampler,
                    COUNT(*) as usage_count,
                    AVG(
                        CAST(COALESCE(
                            json_extract(gi.parameters, '$.steps'),
                            json_extract(p.sampler_settings, '$.steps'),
                            20
                        ) AS REAL)
                    ) as avg_steps,
                    AVG(
                        CAST(COALESCE(
                            json_extract(gi.parameters, '$.cfg_scale'),
                            json_extract(p.sampler_settings, '$.cfg_scale'),
                            7.0
                        ) AS REAL)
                    ) as avg_cfg
                FROM generated_images gi
                LEFT JOIN prompts p ON gi.prompt_id = p.id
                GROUP BY sampler
                HAVING sampler != 'Unknown'
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