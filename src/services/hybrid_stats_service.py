"""
Hybrid Stats Service - Combines instant cache with calculated analytics.
Gets basic counts from cache, calculates complex analytics on demand.
"""

import json
import sqlite3
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from collections import Counter
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class HybridStatsService:
    """
    Hybrid approach: Instant basic stats + calculated analytics.
    Much faster than full recalculation but provides all needed data.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def get_overview(self) -> Dict[str, Any]:
        """
        Get complete stats overview with all analytics.
        Basic counts from cache (<10ms) + analytics calculation (~500ms).
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # 1. Get instant basic stats from cache
                cache_row = cursor.execute("""
                    SELECT * FROM stats_snapshot WHERE id = 1
                """).fetchone()

                if cache_row:
                    total_prompts = cache_row['total_prompts'] or 0
                    total_images = cache_row['total_images'] or 0
                    avg_rating = cache_row['avg_rating'] or 0.0
                    total_rated = cache_row['total_rated'] or 0
                else:
                    # Fallback if cache doesn't exist
                    total_prompts = cursor.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]
                    total_images = cursor.execute("SELECT COUNT(*) FROM generated_images").fetchone()[0]
                    rating_data = cursor.execute("""
                        SELECT AVG(rating) as avg, COUNT(*) as count
                        FROM prompts WHERE rating IS NOT NULL
                    """).fetchone()
                    avg_rating = rating_data['avg'] or 0.0
                    total_rated = rating_data['count'] or 0

                # 2. Calculate time analytics (for streaks and peak hours)
                time_analytics = self._calculate_time_analytics(cursor)

                # 3. Calculate prompt patterns (for word clouds and complexity)
                prompt_patterns = self._calculate_prompt_patterns(cursor)

                # 4. Calculate generation metrics
                generation_metrics = self._calculate_generation_metrics(cursor)

                # 5. Calculate user behavior
                user_behavior = self._calculate_user_behavior(cursor)

                # 6. Calculate quality metrics
                quality_metrics = self._calculate_quality_metrics(cursor, avg_rating, total_rated, prompt_patterns)

                # 7. Get category breakdown
                categories = cursor.execute("""
                    SELECT category, COUNT(*) as count
                    FROM prompts
                    WHERE category IS NOT NULL
                    GROUP BY category
                    ORDER BY count DESC
                    LIMIT 10
                """).fetchall()
                category_breakdown = {row['category']: row['count'] for row in categories}

                # 8. Get enhanced model performance
                model_performance = self._calculate_model_performance(cursor)

                # 9. Calculate trends
                trends = self._calculate_trends(cursor)

                # 10. Calculate workflow stats
                workflow_stats = self._calculate_workflow_stats(cursor)

                # Build complete response with ALL sections
                return {
                    'totalPrompts': total_prompts,
                    'totalImages': total_images,
                    'totalSessions': 0,  # Not tracked
                    'totals': {
                        'prompts': total_prompts,
                        'images': total_images,
                        'sessions': 0,
                        'collections': 0
                    },
                    'categoryBreakdown': category_breakdown,
                    'timeAnalytics': time_analytics,
                    'promptPatterns': prompt_patterns,
                    'generationMetrics': generation_metrics,
                    'userBehavior': user_behavior,
                    'qualityMetrics': quality_metrics,
                    'modelPerformance': model_performance,
                    'trends': trends,
                    'workflowStats': workflow_stats,
                    'recentActivity': {
                        'prompts_24h': time_analytics.get('prompts_24h', 0),
                        'prompts_7d': time_analytics.get('prompts_7d', 0),
                        'prompts_30d': time_analytics.get('prompts_30d', 0),
                        'images_24h': time_analytics.get('images_24h', 0),
                        'images_7d': time_analytics.get('images_7d', 0),
                        'images_30d': time_analytics.get('images_30d', 0)
                    },
                    'generatedAt': datetime.utcnow().isoformat() + 'Z',
                    'loadTime': 'hybrid'  # ~500ms instead of 2-3 minutes
                }

        except Exception as e:
            logger.error(f"Failed to get hybrid stats: {e}")
            return self._get_empty_stats()

    def _calculate_time_analytics(self, cursor) -> Dict[str, Any]:
        """Calculate time-based analytics including streaks and peak hours."""
        try:
            # Get activity by hour
            hourly = cursor.execute("""
                SELECT
                    strftime('%H', created_at) as hour,
                    COUNT(*) as count
                FROM prompts
                WHERE created_at IS NOT NULL
                GROUP BY hour
                ORDER BY count DESC
            """).fetchall()

            peak_hours = [
                {'hour': int(row['hour']), 'percentage': 100}
                for row in hourly[:3]
            ] if hourly else []

            # Calculate streak (simplified - just check recent days)
            recent_days = cursor.execute("""
                SELECT
                    DATE(created_at) as day,
                    COUNT(*) as count
                FROM prompts
                WHERE created_at >= date('now', '-30 days')
                GROUP BY day
                ORDER BY day DESC
            """).fetchall()

            current_streak = 0
            if recent_days:
                today = datetime.now().date()
                for i, row in enumerate(recent_days):
                    day_str = row['day']
                    if day_str:
                        day = datetime.strptime(day_str, '%Y-%m-%d').date()
                        expected = today - timedelta(days=i)
                        if day == expected:
                            current_streak += 1
                        else:
                            break

            # Get recent activity counts
            now = datetime.utcnow()
            day_ago = (now - timedelta(days=1)).isoformat()
            week_ago = (now - timedelta(days=7)).isoformat()
            month_ago = (now - timedelta(days=30)).isoformat()

            prompts_24h = cursor.execute(
                "SELECT COUNT(*) FROM prompts WHERE created_at >= ?", (day_ago,)
            ).fetchone()[0]

            prompts_7d = cursor.execute(
                "SELECT COUNT(*) FROM prompts WHERE created_at >= ?", (week_ago,)
            ).fetchone()[0]

            prompts_30d = cursor.execute(
                "SELECT COUNT(*) FROM prompts WHERE created_at >= ?", (month_ago,)
            ).fetchone()[0]

            # Check if generated_images has created_at column
            image_cols = cursor.execute("PRAGMA table_info(generated_images)").fetchall()
            has_image_created = any(col[1] == 'created_at' for col in image_cols)

            if has_image_created:
                images_24h = cursor.execute(
                    "SELECT COUNT(*) FROM generated_images WHERE created_at >= ?", (day_ago,)
                ).fetchone()[0]

                images_7d = cursor.execute(
                    "SELECT COUNT(*) FROM generated_images WHERE created_at >= ?", (week_ago,)
                ).fetchone()[0]

                images_30d = cursor.execute(
                    "SELECT COUNT(*) FROM generated_images WHERE created_at >= ?", (month_ago,)
                ).fetchone()[0]
            else:
                # Estimate based on prompt activity if no timestamp
                images_24h = prompts_24h * 10  # Assume ~10 images per prompt
                images_7d = prompts_7d * 10
                images_30d = prompts_30d * 10

            return {
                'peakHours': peak_hours,
                'currentStreak': current_streak,
                'longestStreak': current_streak,  # Simplified
                'prompts_24h': prompts_24h,
                'prompts_7d': prompts_7d,
                'prompts_30d': prompts_30d,
                'images_24h': images_24h,
                'images_7d': images_7d,
                'images_30d': images_30d
            }

        except Exception as e:
            logger.error(f"Failed to calculate time analytics: {e}")
            return {}

    def _calculate_prompt_patterns(self, cursor) -> Dict[str, Any]:
        """Calculate prompt patterns including word frequency and complexity."""
        try:
            # Get sample of recent prompts for analysis
            prompts = cursor.execute("""
                SELECT positive_prompt
                FROM prompts
                WHERE positive_prompt IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 500
            """).fetchall()

            if not prompts:
                return {}

            # Word frequency analysis
            word_counter = Counter()
            total_length = 0
            complexity_bins = {'simple': 0, 'moderate': 0, 'complex': 0}

            for row in prompts:
                prompt = row['positive_prompt']
                if prompt:
                    words = prompt.lower().split()
                    word_counter.update(words)
                    total_length += len(prompt)

                    # Complexity based on length
                    if len(prompt) < 50:
                        complexity_bins['simple'] += 1
                    elif len(prompt) < 150:
                        complexity_bins['moderate'] += 1
                    else:
                        complexity_bins['complex'] += 1

            # Get top words (excluding common ones)
            ignored_words = {'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
                           'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were'}

            filtered_words = {word: count for word, count in word_counter.items()
                            if word not in ignored_words and len(word) > 2}

            top_words = dict(Counter(filtered_words).most_common(20))

            return {
                'topWords': top_words,
                'vocabularySize': len(word_counter),
                'avgPromptLength': int(total_length / len(prompts)) if prompts else 0,
                'complexityDistribution': complexity_bins
            }

        except Exception as e:
            logger.error(f"Failed to calculate prompt patterns: {e}")
            return {}

    def _calculate_generation_metrics(self, cursor) -> Dict[str, Any]:
        """Calculate generation metrics like resolution distribution."""
        try:
            # Get resolution data
            resolutions = cursor.execute("""
                SELECT
                    json_extract(generation_params, '$.width') as width,
                    json_extract(generation_params, '$.height') as height,
                    COUNT(*) as count
                FROM prompts
                WHERE generation_params IS NOT NULL
                GROUP BY width, height
                ORDER BY count DESC
                LIMIT 10
            """).fetchall()

            resolution_dist = {}
            aspect_ratios = {}

            for row in resolutions:
                w = row['width']
                h = row['height']
                if w and h:
                    res_key = f"{w}x{h}"
                    resolution_dist[res_key] = row['count']

                    # Calculate aspect ratio
                    ratio = round(w / h, 2) if h else 1
                    ratio_key = f"{ratio:.2f}"
                    aspect_ratios[ratio_key] = aspect_ratios.get(ratio_key, 0) + row['count']

            # Get quality tiers (based on steps)
            quality_data = cursor.execute("""
                SELECT
                    CASE
                        WHEN json_extract(generation_params, '$.steps') < 20 THEN 'draft'
                        WHEN json_extract(generation_params, '$.steps') < 30 THEN 'standard'
                        WHEN json_extract(generation_params, '$.steps') < 50 THEN 'quality'
                        ELSE 'premium'
                    END as tier,
                    COUNT(*) as count
                FROM prompts
                WHERE generation_params IS NOT NULL
                GROUP BY tier
            """).fetchall()

            quality_tiers = {row['tier']: row['count'] for row in quality_data if row['tier']}

            return {
                'resolutionDistribution': resolution_dist,
                'aspectRatios': aspect_ratios,
                'qualityTiers': quality_tiers
            }

        except Exception as e:
            logger.error(f"Failed to calculate generation metrics: {e}")
            return {}

    def _calculate_user_behavior(self, cursor) -> Dict[str, Any]:
        """Calculate user behavior metrics."""
        try:
            # Get unique prompt count for experimentation score
            unique_prompts = cursor.execute("""
                SELECT COUNT(DISTINCT positive_prompt) as unique_count,
                       COUNT(*) as total_count
                FROM prompts
            """).fetchone()

            experimentation_score = 0
            if unique_prompts and unique_prompts['total_count']:
                ratio = unique_prompts['unique_count'] / unique_prompts['total_count']
                experimentation_score = int(ratio * 100)

            # Get iteration patterns (prompts with similar text)
            iteration_rate = cursor.execute("""
                SELECT COUNT(*) * 100.0 / (SELECT COUNT(*) FROM prompts) as rate
                FROM prompts p1
                WHERE EXISTS (
                    SELECT 1 FROM prompts p2
                    WHERE p2.id != p1.id
                    AND p2.positive_prompt LIKE '%' || substr(p1.positive_prompt, 1, 20) || '%'
                )
            """).fetchone()[0] or 0

            return {
                'experimentationScore': experimentation_score,
                'iterationRate': round(iteration_rate, 1),
                'favoriteTime': 'evening',  # Placeholder
                'avgSessionLength': 45  # Placeholder (minutes)
            }

        except Exception as e:
            logger.error(f"Failed to calculate user behavior: {e}")
            return {}

    def _calculate_quality_metrics(self, cursor, avg_rating, total_rated, prompt_patterns) -> Dict[str, Any]:
        """Calculate enhanced quality metrics."""
        try:
            # Calculate innovation index based on vocabulary diversity
            vocab_size = prompt_patterns.get('vocabularySize', 0)
            innovation_index = min(100, int((vocab_size / 30) * 100)) if vocab_size else 0

            # Calculate consistency score (how consistent ratings are)
            rating_variance = cursor.execute("""
                SELECT
                    AVG((rating - (SELECT AVG(rating) FROM prompts WHERE rating IS NOT NULL)) *
                        (rating - (SELECT AVG(rating) FROM prompts WHERE rating IS NOT NULL))) as variance
                FROM prompts
                WHERE rating IS NOT NULL
            """).fetchone()[0] or 0

            consistency_score = max(0, 100 - int(rating_variance * 20))

            # Calculate improvement rate (compare recent vs old ratings)
            recent_avg = cursor.execute("""
                SELECT AVG(rating) FROM (
                    SELECT rating FROM prompts
                    WHERE rating IS NOT NULL
                    ORDER BY created_at DESC
                    LIMIT 50
                )
            """).fetchone()[0] or 0

            old_avg = cursor.execute("""
                SELECT AVG(rating) FROM (
                    SELECT rating FROM prompts
                    WHERE rating IS NOT NULL
                    ORDER BY created_at ASC
                    LIMIT 50
                )
            """).fetchone()[0] or 0

            improvement_rate = round((recent_avg - old_avg) * 20, 1) if old_avg else 0

            return {
                'avgRating': avg_rating,
                'totalRated': total_rated,
                'innovationIndex': innovation_index,
                'consistencyScore': consistency_score,
                'improvementRate': improvement_rate
            }
        except Exception as e:
            logger.error(f"Failed to calculate quality metrics: {e}")
            return {
                'avgRating': avg_rating,
                'totalRated': total_rated,
                'innovationIndex': 0
            }

    def _calculate_model_performance(self, cursor) -> Dict[str, Any]:
        """Calculate enhanced model performance metrics."""
        try:
            # Get top models
            models = cursor.execute("""
                SELECT
                    json_extract(generation_params, '$.model') as model,
                    COUNT(*) as count,
                    AVG(rating) as avg_rating
                FROM prompts
                WHERE generation_params IS NOT NULL
                GROUP BY model
                ORDER BY count DESC
                LIMIT 10
            """).fetchall()

            top_models = [
                {
                    'model': row['model'] or 'Unknown',
                    'count': row['count'],
                    'avgRating': round(row['avg_rating'], 2) if row['avg_rating'] else 0
                }
                for row in models if row['model']
            ]

            # Get optimal configurations (best rated prompt/model combinations)
            optimal = cursor.execute("""
                SELECT
                    json_extract(generation_params, '$.model') as model,
                    positive_prompt,
                    rating
                FROM prompts
                WHERE rating = 5 AND generation_params IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 5
            """).fetchall()

            optimal_configs = [
                {
                    'model': row['model'] or 'Unknown',
                    'prompt_excerpt': (row['positive_prompt'] or '')[:50],
                    'rating': row['rating']
                }
                for row in optimal
            ]

            return {
                'topModels': top_models,
                'optimalConfigs': optimal_configs
            }
        except Exception as e:
            logger.error(f"Failed to calculate model performance: {e}")
            return {'topModels': [], 'optimalConfigs': []}

    def _calculate_trends(self, cursor) -> Dict[str, Any]:
        """Calculate trending styles and patterns."""
        try:
            # Analyze recent prompts for trending terms
            recent_prompts = cursor.execute("""
                SELECT positive_prompt FROM prompts
                WHERE created_at >= date('now', '-7 days')
                AND positive_prompt IS NOT NULL
                LIMIT 200
            """).fetchall()

            old_prompts = cursor.execute("""
                SELECT positive_prompt FROM prompts
                WHERE created_at < date('now', '-7 days')
                AND created_at >= date('now', '-30 days')
                AND positive_prompt IS NOT NULL
                LIMIT 200
            """).fetchall()

            # Count style keywords
            style_keywords = ['anime', 'realistic', 'fantasy', 'sci-fi', 'portrait',
                            'landscape', 'abstract', 'cartoon', 'photograph', 'digital art',
                            'oil painting', 'watercolor', '3d render', 'concept art']

            recent_styles = Counter()
            old_styles = Counter()

            for row in recent_prompts:
                prompt = (row['positive_prompt'] or '').lower()
                for style in style_keywords:
                    if style in prompt:
                        recent_styles[style] += 1

            for row in old_prompts:
                prompt = (row['positive_prompt'] or '').lower()
                for style in style_keywords:
                    if style in prompt:
                        old_styles[style] += 1

            # Calculate trending styles
            trending = []
            for style in recent_styles:
                recent_count = recent_styles[style]
                old_count = old_styles.get(style, 1)
                trend_score = round(recent_count / max(old_count, 1), 1)
                if trend_score > 1.2:  # At least 20% increase
                    trending.append({
                        'style': style,
                        'trendScore': trend_score,
                        'count': recent_count
                    })

            trending.sort(key=lambda x: x['trendScore'], reverse=True)

            # Calculate velocity score (activity increase)
            recent_count = len(recent_prompts)
            old_count = len(old_prompts)
            velocity_score = round((recent_count - old_count) / max(old_count, 1) * 100, 1)

            return {
                'trendingStyles': trending[:10],
                'velocityScore': velocity_score
            }
        except Exception as e:
            logger.error(f"Failed to calculate trends: {e}")
            return {'trendingStyles': [], 'velocityScore': 0}

    def _calculate_workflow_stats(self, cursor) -> Dict[str, Any]:
        """Calculate workflow complexity stats."""
        try:
            # Analyze generation parameters complexity
            params_complexity = cursor.execute("""
                SELECT
                    CASE
                        WHEN LENGTH(generation_params) < 100 THEN 'simple'
                        WHEN LENGTH(generation_params) < 500 THEN 'moderate'
                        ELSE 'complex'
                    END as complexity,
                    COUNT(*) as count
                FROM prompts
                WHERE generation_params IS NOT NULL
                GROUP BY complexity
            """).fetchall()

            complexity_dist = {row['complexity']: row['count'] for row in params_complexity}

            # Calculate average workflow components (based on param keys)
            avg_components = cursor.execute("""
                SELECT AVG(
                    (LENGTH(generation_params) - LENGTH(REPLACE(generation_params, '","', ''))) + 1
                ) as avg_keys
                FROM prompts
                WHERE generation_params IS NOT NULL AND generation_params LIKE '{%}'
            """).fetchone()[0] or 5

            return {
                'complexityDistribution': complexity_dist,
                'avgWorkflowComponents': int(avg_components)
            }
        except Exception as e:
            logger.error(f"Failed to calculate workflow stats: {e}")
            return {}

    def _get_empty_stats(self) -> Dict[str, Any]:
        """Return empty stats structure when database is unavailable."""
        return {
            'totalPrompts': 0,
            'totalImages': 0,
            'totalSessions': 0,
            'totals': {'prompts': 0, 'images': 0, 'sessions': 0, 'collections': 0},
            'categoryBreakdown': {},
            'timeAnalytics': {},
            'promptPatterns': {},
            'generationMetrics': {},
            'userBehavior': {},
            'qualityMetrics': {},
            'modelPerformance': {},
            'trends': {},
            'workflowStats': {},
            'recentActivity': {},
            'generatedAt': datetime.utcnow().isoformat() + 'Z',
            'loadTime': 'error'
        }