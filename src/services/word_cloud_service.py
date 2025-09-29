"""Word Cloud Pre-calculation Service for efficient stats rendering."""

import sqlite3
import re
import time
import logging
from typing import Dict, List, Tuple
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)


class WordCloudService:
    """Service for pre-calculating and caching word cloud data."""

    def __init__(self, db_path: str):
        """Initialize with database path."""
        self.db_path = db_path

        # Common words to ignore (stop words)
        self.stop_words = {
            'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
            'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
            'could', 'should', 'may', 'might', 'must', 'can', 'this', 'that',
            'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they', 'what',
            'which', 'who', 'when', 'where', 'why', 'how', 'all', 'each', 'every',
            'both', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'not',
            'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just', 'there'
        }

    def calculate_word_frequencies(self, limit: int = 100) -> Dict[str, int]:
        """
        Calculate word frequencies from all prompts.

        Args:
            limit: Maximum number of top words to return (default 100)

        Returns:
            Dictionary of word -> frequency mappings
        """
        start_time = time.time()

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Get all prompts
                cursor.execute("""
                    SELECT positive_prompt FROM prompts
                    WHERE positive_prompt IS NOT NULL
                """)

                prompts = [row[0] for row in cursor.fetchall()]

                if not prompts:
                    logger.info("No prompts found for word cloud generation")
                    return {}

                # Process all prompts
                word_counter = Counter()
                total_words = 0

                for prompt in prompts:
                    # Extract words (alphanumeric only)
                    words = re.findall(r'\b[a-z]+\b', prompt.lower())

                    # Filter out stop words and short words
                    filtered_words = [
                        word for word in words
                        if word not in self.stop_words and len(word) > 2
                    ]

                    word_counter.update(filtered_words)
                    total_words += len(filtered_words)

                # Get top words
                top_words = dict(word_counter.most_common(limit))

                # Calculate processing time
                calculation_time_ms = int((time.time() - start_time) * 1000)

                # Store in cache
                self._cache_word_frequencies(top_words, len(prompts), total_words, calculation_time_ms)

                logger.info(f"Generated word cloud with {len(top_words)} words from {len(prompts)} prompts in {calculation_time_ms}ms")

                return top_words

        except Exception as e:
            logger.error(f"Failed to calculate word frequencies: {e}")
            return {}

    def _cache_word_frequencies(self, frequencies: Dict[str, int], prompts_count: int, words_count: int, calc_time: int):
        """Store calculated frequencies in cache table."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Clear existing cache
                cursor.execute("DELETE FROM word_cloud_cache")

                # Insert new frequencies
                for word, freq in frequencies.items():
                    cursor.execute("""
                        INSERT INTO word_cloud_cache (word, frequency, updated_at)
                        VALUES (?, ?, CURRENT_TIMESTAMP)
                    """, (word, freq))

                # Update metadata
                cursor.execute("""
                    UPDATE word_cloud_metadata
                    SET last_calculated = CURRENT_TIMESTAMP,
                        total_prompts_analyzed = ?,
                        total_words_processed = ?,
                        calculation_time_ms = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = 1
                """, (prompts_count, words_count, calc_time))

                conn.commit()

        except Exception as e:
            logger.error(f"Failed to cache word frequencies: {e}")

    def get_cached_frequencies(self, limit: int = 100) -> Dict[str, int]:
        """
        Retrieve cached word frequencies.

        Args:
            limit: Maximum number of words to return

        Returns:
            Dictionary of word -> frequency mappings
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Get cached frequencies
                cursor.execute("""
                    SELECT word, frequency
                    FROM word_cloud_cache
                    ORDER BY frequency DESC
                    LIMIT ?
                """, (limit,))

                frequencies = {row[0]: row[1] for row in cursor.fetchall()}

                if not frequencies:
                    logger.info("No cached word cloud data found, calculating...")
                    return self.calculate_word_frequencies(limit)

                return frequencies

        except sqlite3.OperationalError as e:
            # Table might not exist yet
            logger.warning(f"Word cloud cache table not found: {e}")
            return {}
        except Exception as e:
            logger.error(f"Failed to get cached frequencies: {e}")
            return {}

    def get_metadata(self) -> Dict:
        """Get word cloud metadata including last calculation time."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT last_calculated, total_prompts_analyzed,
                           total_words_processed, calculation_time_ms
                    FROM word_cloud_metadata
                    WHERE id = 1
                """)

                row = cursor.fetchone()
                if row:
                    return {
                        'last_calculated': row[0],
                        'total_prompts_analyzed': row[1],
                        'total_words_processed': row[2],
                        'calculation_time_ms': row[3]
                    }

                return {}

        except Exception as e:
            logger.error(f"Failed to get word cloud metadata: {e}")
            return {}

    def needs_recalculation(self, hours: int = 24) -> bool:
        """
        Check if word cloud needs recalculation.

        Args:
            hours: Hours since last calculation to consider stale (default 24)

        Returns:
            True if recalculation is needed
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Check if cache is empty
                cursor.execute("SELECT COUNT(*) FROM word_cloud_cache")
                if cursor.fetchone()[0] == 0:
                    return True

                # Check last calculation time
                cursor.execute("""
                    SELECT
                        CAST((julianday('now') - julianday(last_calculated)) * 24 AS INTEGER) as hours_ago
                    FROM word_cloud_metadata
                    WHERE id = 1 AND last_calculated IS NOT NULL
                """)

                row = cursor.fetchone()
                if not row or row[0] is None:
                    return True

                return row[0] >= hours

        except Exception as e:
            logger.error(f"Failed to check recalculation need: {e}")
            return True