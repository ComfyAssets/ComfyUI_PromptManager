"""
Prompt tracking system for linking generated images to prompts.
Tracks active prompt executions to associate them with generated images.
"""

import threading
import time
import uuid
import hashlib
from typing import Optional, Dict, Any
from datetime import datetime, timezone


class PromptTracker:
    """Thread-safe tracking of current prompt executions."""
    
    def __init__(self, db_manager):
        """
        Initialize the prompt tracker.
        
        Args:
            db_manager: Database manager instance
        """
        self.db_manager = db_manager
        self._local = threading.local()
        self.active_prompts = {}  # Global tracking for multiple threads
        self.lock = threading.Lock()
        self.cleanup_interval = 300  # 5 minutes
        self.prompt_timeout = 600    # 10 minutes (increased for longer generations)
        
        # Start cleanup thread
        self.cleanup_thread = threading.Thread(target=self._cleanup_expired_prompts, daemon=True)
        self.cleanup_thread.start()
    
    def set_current_prompt(self, prompt_text: str, additional_data: Optional[Dict[str, Any]] = None) -> str:
        """
        Set the current prompt for this thread for image tracking.
        Note: Prompt saving is handled by PromptManager to avoid duplicates.
        
        Args:
            prompt_text: The prompt text being executed
            additional_data: Additional prompt metadata (should include prompt_id from PromptManager)
            
        Returns:
            Execution ID for this prompt
        """
        execution_id = self.generate_execution_id()
        
        # Get prompt_id from additional_data (passed from PromptManager)
        # PromptTracker should NOT save prompts to avoid duplicates
        prompt_id = additional_data.get('prompt_id') if additional_data else None
        
        if not prompt_id:
            # Fallback: try to find existing prompt using consistent hash calculation
            try:
                # Use consistent hash calculation (same as PromptManager)
                import hashlib
                normalized_text = prompt_text.strip().lower()
                prompt_hash = hashlib.sha256(normalized_text.encode('utf-8')).hexdigest()
                existing_prompt = self.db_manager.get_prompt_by_hash(prompt_hash)
                
                if existing_prompt:
                    prompt_id = existing_prompt['id']
                    print(f"[PromptManager] Found existing prompt ID: {prompt_id}")
                else:
                    # Generate a temporary ID for tracking (prompt should be saved by PromptManager)
                    prompt_id = f"temp_{int(time.time())}"
                    print(f"[PromptManager] Using temporary ID for tracking: {prompt_id}")
            except Exception as e:
                print(f"[PromptManager] Error finding prompt: {e}")
                prompt_id = f"temp_{int(time.time())}"
        
        # Create execution context
        execution_context = {
            'id': prompt_id,
            'execution_id': execution_id,
            'text': prompt_text,
            'timestamp': time.time(),
            'thread_id': threading.current_thread().ident,
            'additional_data': additional_data or {}
        }
        
        # Store in thread-local storage
        self._local.current_prompt = execution_context
        
        # Store in global tracking with thread safety
        with self.lock:
            self.active_prompts[execution_id] = execution_context
        
        print(f"[PromptManager] Set current prompt: {execution_id} -> {prompt_text[:50]}... (thread: {threading.current_thread().ident})")
        print(f"[PromptManager] Active prompts count: {len(self.active_prompts)}")
        return execution_id
    
    def get_current_prompt(self) -> Optional[Dict[str, Any]]:
        """
        Get the current prompt context for this thread.
        
        Returns:
            Current prompt context or None
        """
        current = getattr(self._local, 'current_prompt', None)
        
        if current:
            # Check if prompt hasn't expired
            if time.time() - current['timestamp'] < self.prompt_timeout:
                return current
            else:
                print(f"[PromptManager] Current prompt expired: {current['execution_id']}")
                self.clear_current_prompt()
        
        # Fallback: try to find recent prompt from global tracking
        # This is crucial for image monitoring which runs in different threads
        recent_prompt = self._find_recent_prompt()
        if recent_prompt:
            print(f"[PromptManager] Using recent prompt from global tracking: {recent_prompt['execution_id']}")
            return recent_prompt
            
        print(f"[PromptManager] No prompt context found (thread: {threading.current_thread().ident})")
        return None
    
    def _find_recent_prompt(self) -> Optional[Dict[str, Any]]:
        """Find the most recent prompt that's still valid."""
        with self.lock:
            current_time = time.time()
            print(f"[PromptManager] Searching for recent prompt among {len(self.active_prompts)} active prompts")
            
            recent_prompts = []
            for exec_id, prompt in self.active_prompts.items():
                age_seconds = current_time - prompt['timestamp']
                print(f"[PromptManager] Prompt {exec_id}: age={age_seconds:.1f}s, timeout={self.prompt_timeout}s")
                
                if age_seconds < self.prompt_timeout:
                    recent_prompts.append(prompt)
                else:
                    print(f"[PromptManager] Prompt {exec_id} is expired")
            
            if recent_prompts:
                # Return the most recent one
                most_recent = max(recent_prompts, key=lambda p: p['timestamp'])
                print(f"[PromptManager] Found recent prompt: {most_recent['execution_id']}")
                return most_recent
            else:
                print(f"[PromptManager] No recent prompts found")
        
        return None
    
    def clear_current_prompt(self):
        """Clear the current prompt context for this thread."""
        current = getattr(self._local, 'current_prompt', None)
        if current:
            execution_id = current['execution_id']
            print(f"[PromptManager] Clearing current prompt: {execution_id} (thread: {threading.current_thread().ident})")
            
            # Clear from thread-local storage
            self._local.current_prompt = None
            
            # Remove from global tracking
            with self.lock:
                removed = self.active_prompts.pop(execution_id, None)
                if removed:
                    print(f"[PromptManager] Removed prompt from global tracking: {execution_id}")
                    print(f"[PromptManager] Remaining active prompts: {len(self.active_prompts)}")
                else:
                    print(f"[PromptManager] Prompt {execution_id} was not in global tracking")
        else:
            print(f"[PromptManager] No current prompt to clear (thread: {threading.current_thread().ident})")
    
    def extend_prompt_timeout(self, execution_id: str, additional_seconds: int = 60):
        """
        Extend the timeout for a specific prompt execution.
        
        Args:
            execution_id: The execution ID to extend
            additional_seconds: Additional seconds to add to timeout
        """
        with self.lock:
            if execution_id in self.active_prompts:
                self.active_prompts[execution_id]['timestamp'] = time.time()
                print(f"[PromptManager] Extended timeout for prompt: {execution_id}")
    
    def generate_execution_id(self) -> str:
        """Generate a unique execution ID."""
        return f"exec_{uuid.uuid4().hex[:8]}_{int(time.time())}"
    
    def _cleanup_expired_prompts(self):
        """Background thread to clean up expired prompts."""
        while True:
            try:
                current_time = time.time()
                expired_ids = []
                
                with self.lock:
                    for exec_id, prompt_data in self.active_prompts.items():
                        if current_time - prompt_data['timestamp'] > self.prompt_timeout:
                            expired_ids.append(exec_id)
                    
                    for exec_id in expired_ids:
                        self.active_prompts.pop(exec_id, None)
                
                if expired_ids:
                    print(f"[PromptManager] Cleaned up {len(expired_ids)} expired prompts")
                
                time.sleep(self.cleanup_interval)
                
            except Exception as e:
                print(f"[PromptManager] Error in cleanup thread: {e}")
                time.sleep(60)  # Wait a minute before retrying
    
    def get_active_prompts(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all currently active prompts.
        
        Returns:
            Dictionary of active prompts
        """
        with self.lock:
            return self.active_prompts.copy()
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get tracker status information.
        
        Returns:
            Status information dictionary
        """
        with self.lock:
            active_count = len(self.active_prompts)
            
        current_prompt = self.get_current_prompt()
        
        return {
            'active_prompts_count': active_count,
            'current_prompt_id': current_prompt['id'] if current_prompt else None,
            'current_execution_id': current_prompt['execution_id'] if current_prompt else None,
            'thread_id': threading.current_thread().ident,
            'prompt_timeout': self.prompt_timeout,
            'cleanup_interval': self.cleanup_interval
        }


class PromptExecutionContext:
    """Context manager for prompt executions."""
    
    def __init__(self, prompt_tracker: PromptTracker, prompt_text: str, **kwargs):
        """
        Initialize execution context.
        
        Args:
            prompt_tracker: PromptTracker instance
            prompt_text: The prompt text
            **kwargs: Additional prompt metadata
        """
        self.prompt_tracker = prompt_tracker
        self.prompt_text = prompt_text
        self.additional_data = kwargs
        self.execution_id = None
    
    def __enter__(self):
        """Enter the execution context."""
        self.execution_id = self.prompt_tracker.set_current_prompt(
            self.prompt_text, 
            self.additional_data
        )
        return self.execution_id
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the execution context."""
        # Don't clear immediately - let the timeout handle it
        # This allows images to be generated after the prompt execution completes
        pass