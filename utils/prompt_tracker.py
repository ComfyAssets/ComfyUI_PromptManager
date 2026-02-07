"""Prompt tracking system for linking generated images to prompts.

This module provides thread-safe tracking of active prompt executions to enable automatic
association of generated images with their source prompts. The system maintains prompt
context across different execution threads and provides fallback mechanisms for reliable
image-prompt linking.

Key features:
- Thread-safe prompt tracking using threading.local and locks
- Automatic cleanup of expired prompt contexts
- Fallback mechanisms for cross-thread prompt access
- Support for execution timeouts and extensions
- Context manager for automatic prompt lifecycle management

Typical usage:
    from utils.prompt_tracker import PromptTracker

    tracker = PromptTracker(db_manager)
    execution_id = tracker.set_current_prompt("beautiful landscape")
    # Images generated after this point will be linked to this prompt

Or using the context manager:
    with PromptExecutionContext(tracker, "beautiful landscape"):
        # Generate images here
        pass
"""

import threading
import time
import uuid
import hashlib
from typing import Optional, Dict, Any
from datetime import datetime, timezone

# Import logging system
try:
    from .logging_config import get_logger
except ImportError:
    import sys
    import os

    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, current_dir)
    from utils.logging_config import get_logger


class PromptTracker:
    """Thread-safe tracking of current prompt executions.

    This class manages active prompt contexts across multiple threads, enabling
    the image monitoring system to correctly associate generated images with their
    source prompts. It uses both thread-local storage and global tracking to handle
    various execution scenarios.

    The tracker automatically cleans up expired prompts and provides fallback
    mechanisms when prompts are accessed from different threads (e.g., image
    monitoring running in a separate thread from prompt execution).

    Attributes:
        active_prompts: Global dictionary of active prompt contexts
        cleanup_interval: Seconds between cleanup operations (default: 300)
        prompt_timeout: Seconds before a prompt expires (default: 600)
    """

    def __init__(self, db_manager):
        """
        Initialize the prompt tracker.

        Args:
            db_manager: Database manager instance for prompt operations
        """
        self.logger = get_logger("prompt_manager.prompt_tracker")
        self.logger.debug("Initializing PromptTracker")

        self.db_manager = db_manager
        self._local = threading.local()
        self.active_prompts = {}  # Global tracking for multiple threads
        self.lock = threading.Lock()

        # Read from GalleryConfig if available, otherwise use defaults
        try:
            from ..py.config import GalleryConfig

            self.cleanup_interval = GalleryConfig.CLEANUP_INTERVAL
            self.prompt_timeout = GalleryConfig.PROMPT_TIMEOUT
        except Exception:
            self.cleanup_interval = 300  # 5 minutes
            self.prompt_timeout = 600  # 10 minutes

        # Start cleanup thread
        self.cleanup_thread = threading.Thread(
            target=self._cleanup_expired_prompts, daemon=True
        )
        self.cleanup_thread.start()
        self.logger.debug("PromptTracker initialization completed")

    def set_current_prompt(
        self, prompt_text: str, additional_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Set the current prompt for this thread for image tracking.

        This method establishes the prompt context that will be used to link any
        subsequently generated images. The prompt is stored in both thread-local
        storage and global tracking to handle cross-thread access scenarios.

        Note: Prompt saving is handled by PromptManager to avoid duplicates.

        Args:
            prompt_text: The prompt text being executed
            additional_data: Additional prompt metadata, should include prompt_id from PromptManager

        Returns:
            Unique execution ID for this prompt execution
        """
        execution_id = self.generate_execution_id()

        # Get prompt_id from additional_data (passed from PromptManager)
        # PromptTracker should NOT save prompts to avoid duplicates
        prompt_id = additional_data.get("prompt_id") if additional_data else None

        if not prompt_id:
            # Fallback: try to find existing prompt using consistent hash calculation
            try:
                # Use consistent hash calculation (same as PromptManager)
                import hashlib

                normalized_text = prompt_text.strip().lower()
                prompt_hash = hashlib.sha256(
                    normalized_text.encode("utf-8")
                ).hexdigest()
                existing_prompt = self.db_manager.get_prompt_by_hash(prompt_hash)

                if existing_prompt:
                    prompt_id = existing_prompt["id"]
                    self.logger.debug(f"Found existing prompt ID: {prompt_id}")
                else:
                    # Generate a temporary ID for tracking (prompt should be saved by PromptManager)
                    prompt_id = f"temp_{int(time.time())}"
                    self.logger.debug(f"Using temporary ID for tracking: {prompt_id}")
            except Exception as e:
                self.logger.error(f"Error finding prompt: {e}")
                prompt_id = f"temp_{int(time.time())}"

        # Create execution context
        execution_context = {
            "id": prompt_id,
            "execution_id": execution_id,
            "text": prompt_text,
            "timestamp": time.time(),
            "thread_id": threading.current_thread().ident,
            "additional_data": additional_data or {},
        }

        # Store in thread-local storage
        self._local.current_prompt = execution_context

        # Store in global tracking with thread safety
        with self.lock:
            self.active_prompts[execution_id] = execution_context

        self.logger.debug(
            f"Set current prompt: {execution_id} -> {prompt_text[:50]}... (thread: {threading.current_thread().ident})"
        )
        self.logger.debug(f"Active prompts count: {len(self.active_prompts)}")
        return execution_id

    def get_current_prompt(self) -> Optional[Dict[str, Any]]:
        """
        Get the current prompt context for this thread.

        Attempts to retrieve the current prompt context, first from thread-local
        storage, then from global tracking as a fallback. This enables image
        monitoring (which may run in a different thread) to access prompt context.

        Returns:
            Dictionary containing prompt context with keys:
            - id: Prompt ID in database
            - execution_id: Unique execution identifier
            - text: Prompt text
            - timestamp: When the prompt was set
            - thread_id: Thread identifier
            - additional_data: Any additional metadata
            Returns None if no valid prompt context is found
        """
        current = getattr(self._local, "current_prompt", None)

        if current:
            # Check if prompt hasn't expired
            if time.time() - current["timestamp"] < self.prompt_timeout:
                return current
            else:
                self.logger.debug(f"Current prompt expired: {current['execution_id']}")
                self.clear_current_prompt()

        # Fallback: try to find recent prompt from global tracking
        # This is crucial for image monitoring which runs in different threads
        recent_prompt = self._find_recent_prompt()
        if recent_prompt:
            self.logger.debug(
                f"Using recent prompt from global tracking: {recent_prompt['execution_id']}"
            )
            return recent_prompt

        self.logger.debug(
            f"No prompt context found (thread: {threading.current_thread().ident})"
        )
        return None

    def _find_recent_prompt(self) -> Optional[Dict[str, Any]]:
        """Find the most recent prompt that's still valid.

        Searches the global prompt tracking for the most recently set prompt
        that hasn't expired. This is used as a fallback when thread-local
        storage doesn't contain a current prompt.

        Returns:
            Most recent valid prompt context, or None if no valid prompts found
        """
        with self.lock:
            current_time = time.time()
            self.logger.debug(
                f"Searching for recent prompt among {len(self.active_prompts)} active prompts"
            )

            recent_prompts = []
            for exec_id, prompt in self.active_prompts.items():
                age_seconds = current_time - prompt["timestamp"]
                self.logger.debug(
                    f"Prompt {exec_id}: age={age_seconds:.1f}s, timeout={self.prompt_timeout}s"
                )

                if age_seconds < self.prompt_timeout:
                    recent_prompts.append(prompt)
                else:
                    self.logger.debug(f"Prompt {exec_id} is expired")

            if recent_prompts:
                # Return the most recent one
                most_recent = max(recent_prompts, key=lambda p: p["timestamp"])
                self.logger.debug(f"Found recent prompt: {most_recent['execution_id']}")
                return most_recent
            else:
                self.logger.debug(f"No recent prompts found")

        return None

    def clear_current_prompt(self):
        """Clear the current prompt context for this thread.

        Removes the prompt context from both thread-local storage and global
        tracking. This should be called when prompt execution is complete,
        though the system also handles automatic cleanup via timeouts.
        """
        current = getattr(self._local, "current_prompt", None)
        if current:
            execution_id = current["execution_id"]
            self.logger.debug(
                f"Clearing current prompt: {execution_id} (thread: {threading.current_thread().ident})"
            )

            # Clear from thread-local storage
            self._local.current_prompt = None

            # Remove from global tracking
            with self.lock:
                removed = self.active_prompts.pop(execution_id, None)
                if removed:
                    self.logger.debug(
                        f"Removed prompt from global tracking: {execution_id}"
                    )
                    self.logger.debug(
                        f"Remaining active prompts: {len(self.active_prompts)}"
                    )
                else:
                    self.logger.debug(
                        f"Prompt {execution_id} was not in global tracking"
                    )
        else:
            self.logger.debug(
                f"No current prompt to clear (thread: {threading.current_thread().ident})"
            )

    def extend_prompt_timeout(self, execution_id: str, additional_seconds: int = 60):
        """
        Extend the timeout for a specific prompt execution.

        This is useful for long-running image generation processes where images
        may continue to be generated well after the initial prompt execution.
        Updates the timestamp to prevent the prompt from being cleaned up.

        Args:
            execution_id: The execution ID to extend
            additional_seconds: Additional seconds to add to timeout (currently unused,
                              method just resets the timestamp to current time)
        """
        with self.lock:
            if execution_id in self.active_prompts:
                self.active_prompts[execution_id]["timestamp"] = time.time()
                self.logger.debug(f"Extended timeout for prompt: {execution_id}")

    def generate_execution_id(self) -> str:
        """Generate a unique execution ID.

        Creates a unique identifier for this prompt execution using UUID and timestamp.

        Returns:
            Unique execution ID in format 'exec_{uuid8}_{timestamp}'
        """
        return f"exec_{uuid.uuid4().hex[:8]}_{int(time.time())}"

    def _cleanup_expired_prompts(self):
        """Background thread to clean up expired prompts.

        Runs continuously as a daemon thread, periodically removing expired
        prompt contexts from the global tracking dictionary. This prevents
        memory leaks from accumulating old prompt data.
        """
        while True:
            try:
                current_time = time.time()
                expired_ids = []

                with self.lock:
                    for exec_id, prompt_data in self.active_prompts.items():
                        if (
                            current_time - prompt_data["timestamp"]
                            > self.prompt_timeout
                        ):
                            expired_ids.append(exec_id)

                    for exec_id in expired_ids:
                        self.active_prompts.pop(exec_id, None)

                if expired_ids:
                    self.logger.debug(f"Cleaned up {len(expired_ids)} expired prompts")

                time.sleep(self.cleanup_interval)

            except Exception as e:
                self.logger.error(f"Error in cleanup thread: {e}")
                time.sleep(60)  # Wait a minute before retrying

    def get_active_prompts(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all currently active prompts.

        Returns:
            Dictionary mapping execution IDs to prompt context dictionaries.
            This is a copy of the internal tracking dictionary.
        """
        with self.lock:
            return self.active_prompts.copy()

    def clear_all_active_prompts(self) -> int:
        """
        Clear all active prompts.

        Removes all prompt contexts from both global and thread-local storage.
        This is useful before batch operations or when resetting the tracking state.

        Returns:
            Number of prompts that were cleared from global tracking
        """
        with self.lock:
            cleared_count = len(self.active_prompts)
            self.active_prompts.clear()

        # Clear thread-local storage as well
        self._local.current_prompt = None

        self.logger.debug(f"Cleared {cleared_count} active prompts")
        return cleared_count

    def get_status(self) -> Dict[str, Any]:
        """
        Get tracker status information.

        Returns:
            Dictionary containing:
            - active_prompts_count: Number of currently active prompts
            - current_prompt_id: ID of current prompt in this thread (if any)
            - current_execution_id: Execution ID of current prompt in this thread (if any)
            - thread_id: Current thread identifier
            - prompt_timeout: Configured prompt timeout in seconds
            - cleanup_interval: Configured cleanup interval in seconds
        """
        with self.lock:
            active_count = len(self.active_prompts)

        current_prompt = self.get_current_prompt()

        return {
            "active_prompts_count": active_count,
            "current_prompt_id": current_prompt["id"] if current_prompt else None,
            "current_execution_id": (
                current_prompt["execution_id"] if current_prompt else None
            ),
            "thread_id": threading.current_thread().ident,
            "prompt_timeout": self.prompt_timeout,
            "cleanup_interval": self.cleanup_interval,
        }


class PromptExecutionContext:
    """Context manager for prompt executions.

    This context manager provides automatic prompt lifecycle management,
    ensuring prompt context is properly set and cleaned up. While the current
    implementation doesn't automatically clear prompts on exit (to allow for
    images generated after prompt execution), it provides a clean interface
    for prompt management.

    Example:
        with PromptExecutionContext(tracker, "beautiful landscape") as exec_id:
            # Generate images here
            # Images will be linked to this prompt context
            pass
        # Prompt context remains active for timeout period
    """

    def __init__(self, prompt_tracker: PromptTracker, prompt_text: str, **kwargs):
        """
        Initialize execution context.

        Args:
            prompt_tracker: PromptTracker instance to use for tracking
            prompt_text: The prompt text to track
            **kwargs: Additional prompt metadata to include in the context
        """
        self.prompt_tracker = prompt_tracker
        self.prompt_text = prompt_text
        self.additional_data = kwargs
        self.execution_id = None

    def __enter__(self):
        """Enter the execution context.

        Sets the current prompt in the tracker and returns the execution ID.

        Returns:
            Unique execution ID for this prompt
        """
        self.execution_id = self.prompt_tracker.set_current_prompt(
            self.prompt_text, self.additional_data
        )
        return self.execution_id

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the execution context.

        Currently does not clear the prompt context to allow images to be
        generated after prompt execution completes. The prompt will be cleaned
        up automatically after the timeout period.

        Args:
            exc_type: Exception type (if an exception occurred)
            exc_val: Exception value (if an exception occurred)
            exc_tb: Exception traceback (if an exception occurred)
        """
        # Don't clear immediately - let the timeout handle it
        # This allows images to be generated after the prompt execution completes
        pass


# Singleton instance management
_tracker_instance: Optional[PromptTracker] = None
_tracker_lock = threading.Lock()


def get_prompt_tracker(db_manager) -> PromptTracker:
    """Get or create the singleton PromptTracker instance.

    This ensures only one PromptTracker exists across all PromptManager nodes,
    preventing duplicate image linking when multiple nodes are used.

    Args:
        db_manager: Database manager instance for prompt operations

    Returns:
        The singleton PromptTracker instance
    """
    global _tracker_instance
    if _tracker_instance is None:
        with _tracker_lock:
            if _tracker_instance is None:
                _tracker_instance = PromptTracker(db_manager)
    return _tracker_instance
