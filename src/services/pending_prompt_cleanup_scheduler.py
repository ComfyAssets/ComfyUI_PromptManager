"""Scheduled cleanup service for pending prompts.

This module provides a background task that periodically cleans up expired
pending prompts from the registry.
"""

import threading
import time
import logging
from typing import Optional

from ..tracking.pending_registry import PendingPromptRegistry


logger = logging.getLogger("promptmanager.services.pending_cleanup")


class PendingPromptCleanupScheduler:
    """Background scheduler for cleaning up expired pending prompts.
    
    This service runs a background thread that periodically removes expired
    prompts from the pending registry.
    """
    
    def __init__(
        self,
        registry: PendingPromptRegistry,
        interval_seconds: int = 21600,  # 6 hours
    ):
        """Initialize the cleanup scheduler.
        
        Args:
            registry: The PendingPromptRegistry instance to clean up
            interval_seconds: Interval between cleanup runs in seconds (default: 6 hours)
        """
        self.registry = registry
        self.interval_seconds = interval_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._is_running = False
    
    def start(self) -> None:
        """Start the cleanup scheduler."""
        with self._lock:
            if self._is_running:
                logger.warning("Cleanup scheduler is already running")
                return
            
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="PendingPromptCleanupScheduler",
            )
            self._thread.start()
            self._is_running = True
            logger.info(f"Pending prompt cleanup scheduler started (interval: {self.interval_seconds}s)")
    
    def stop(self) -> None:
        """Stop the cleanup scheduler."""
        with self._lock:
            if not self._is_running:
                return
            
            self._stop_event.set()
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=5.0)
            
            self._is_running = False
            logger.info("Pending prompt cleanup scheduler stopped")
    
    def _run(self) -> None:
        """Run the cleanup loop (executed in background thread)."""
        try:
            while not self._stop_event.is_set():
                try:
                    # Wait for interval or stop signal
                    if self._stop_event.wait(timeout=self.interval_seconds):
                        # Stop signal received
                        break
                    
                    # Perform cleanup
                    removed_count = self.registry.cleanup_expired()
                    if removed_count > 0:
                        logger.debug(f"Cleaned up {removed_count} expired pending prompts")
                
                except Exception as e:
                    logger.error(f"Error during pending prompt cleanup: {e}", exc_info=True)
        
        except Exception as e:
            logger.error(f"Fatal error in cleanup scheduler: {e}", exc_info=True)
    
    @property
    def is_running(self) -> bool:
        """Check if the scheduler is currently running."""
        with self._lock:
            return self._is_running
    
    def __del__(self):
        """Cleanup on deletion."""
        try:
            self.stop()
        except Exception:
            pass
