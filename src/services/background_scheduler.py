"""
Background Scheduler Service for periodic analytics updates.
Handles lightweight incremental updates instead of full recalculation.
"""

import asyncio
import threading
from typing import Optional, Callable, Any
from datetime import datetime, timedelta
import logging

from ..loggers import get_logger

logger = get_logger(__name__)


class BackgroundScheduler:
    """
    Lightweight background scheduler for periodic tasks.
    Runs incremental analytics updates efficiently.
    """

    def __init__(self,
                 task_function: Callable,
                 interval_seconds: int = 300,  # 5 minutes default
                 name: str = "BackgroundTask"):
        """
        Initialize scheduler.

        Args:
            task_function: Function to call periodically
            interval_seconds: Seconds between executions
            name: Task name for logging
        """
        self.task_function = task_function
        self.interval = interval_seconds
        self.name = name
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_run: Optional[datetime] = None
        self._run_count = 0
        self._total_runtime = 0.0

    def start(self):
        """Start the background scheduler."""
        if self._running:
            logger.warning(f"{self.name} already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(f"{self.name} started with {self.interval}s interval")

    def stop(self):
        """Stop the background scheduler."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info(f"{self.name} stopped after {self._run_count} runs")

    def _run_loop(self):
        """Main scheduler loop."""
        while self._running:
            try:
                start_time = datetime.now()

                # Run the task
                logger.debug(f"{self.name} executing task #{self._run_count + 1}")
                self.task_function()

                # Track metrics
                runtime = (datetime.now() - start_time).total_seconds()
                self._last_run = start_time
                self._run_count += 1
                self._total_runtime += runtime

                avg_runtime = self._total_runtime / self._run_count if self._run_count > 0 else 0

                logger.info(
                    f"{self.name} completed in {runtime:.2f}s "
                    f"(avg: {avg_runtime:.2f}s, runs: {self._run_count})"
                )

                # Wait for next interval
                import time
                time.sleep(self.interval)

            except Exception as e:
                logger.error(f"{self.name} error: {e}", exc_info=True)
                # Continue running even if task fails
                import time
                time.sleep(self.interval)

    def get_stats(self) -> dict:
        """Get scheduler statistics."""
        return {
            'name': self.name,
            'running': self._running,
            'interval_seconds': self.interval,
            'last_run': self._last_run.isoformat() if self._last_run else None,
            'run_count': self._run_count,
            'average_runtime': self._total_runtime / self._run_count if self._run_count > 0 else 0,
            'total_runtime': self._total_runtime
        }


class StatsScheduler:
    """
    Specialized scheduler for incremental stats updates.
    Manages the lifecycle of stats calculation.
    """

    def __init__(self, incremental_stats_service):
        """
        Initialize stats scheduler.

        Args:
            incremental_stats_service: IncrementalStatsService instance
        """
        self.stats_service = incremental_stats_service
        self.scheduler = BackgroundScheduler(
            task_function=self._update_stats,
            interval_seconds=300,  # 5 minutes
            name="IncrementalStatsUpdate"
        )
        self._first_run = True

    def _update_stats(self):
        """Execute incremental stats update."""
        try:
            if self._first_run:
                logger.info("Performing initial full stats calculation")
                self._first_run = False

            # Run incremental update
            stats = self.stats_service.calculate_incremental_stats()

            # Log summary
            if stats.get('type') == 'full':
                logger.info("Full stats calculation completed")
            else:
                logger.info(
                    f"Incremental update: {stats.get('total_stats', {}).get('prompts', 0)} prompts, "
                    f"{stats.get('total_stats', {}).get('images', 0)} images"
                )

        except Exception as e:
            logger.error(f"Stats update failed: {e}", exc_info=True)
            # Don't raise - let scheduler continue

    def start(self):
        """Start the stats scheduler."""
        self.scheduler.start()

    def stop(self):
        """Stop the stats scheduler."""
        self.scheduler.stop()

    def force_update(self):
        """Force an immediate stats update."""
        logger.info("Forcing immediate stats update")
        self._update_stats()

    def clear_cache(self):
        """Clear stats cache and force full recalculation."""
        logger.info("Clearing stats cache")
        self.stats_service.clear_cache()
        self._first_run = True

    def get_status(self) -> dict:
        """Get scheduler status and statistics."""
        status = self.scheduler.get_stats()
        status['cache_status'] = {
            'first_run_pending': self._first_run
        }
        return status