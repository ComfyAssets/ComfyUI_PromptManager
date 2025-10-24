"""Pending prompt registry for conditional prompt saving.

This module provides a registry for storing prompts in a pending state,
allowing them to be registered before being accepted and persisted to the database.
"""

import uuid
import time
import threading
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PendingPrompt:
    """Represents a prompt that is pending acceptance."""
    
    tracking_id: str
    positive_prompt: str
    negative_prompt: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    ttl_seconds: int = 86400  # 24 hours default
    
    @property
    def is_expired(self) -> bool:
        """Check if prompt has expired based on TTL."""
        return (time.time() - self.created_at) > self.ttl_seconds
    
    @property
    def age_seconds(self) -> float:
        """Get age of prompt in seconds."""
        return time.time() - self.created_at


class PendingPromptRegistry:
    """Registry for managing pending prompts awaiting acceptance.
    
    This class provides thread-safe storage and retrieval of prompts that have
    been registered but not yet accepted and persisted to the database.
    """
    
    def __init__(self, ttl_seconds: int = 86400):
        """Initialize the pending prompt registry.
        
        Args:
            ttl_seconds: Time-to-live for pending prompts in seconds (default: 24 hours)
        """
        self.ttl_seconds = ttl_seconds
        self._prompts: Dict[str, PendingPrompt] = {}
        self._lock = threading.RLock()
    
    def register_prompt(
        self,
        positive_prompt: str,
        negative_prompt: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Register a prompt as pending.
        
        Args:
            positive_prompt: The positive prompt text
            negative_prompt: Optional negative prompt text
            metadata: Optional metadata about the prompt
            
        Returns:
            A unique tracking_id for this prompt
        """
        tracking_id = str(uuid.uuid4())
        
        with self._lock:
            pending_prompt = PendingPrompt(
                tracking_id=tracking_id,
                positive_prompt=positive_prompt,
                negative_prompt=negative_prompt,
                metadata=metadata or {},
                ttl_seconds=self.ttl_seconds,
            )
            self._prompts[tracking_id] = pending_prompt
        
        return tracking_id
    
    def get_prompt(self, tracking_id: str) -> Optional[PendingPrompt]:
        """Retrieve a pending prompt by tracking ID.
        
        Args:
            tracking_id: The unique tracking ID
            
        Returns:
            The PendingPrompt if found and not expired, None otherwise
        """
        with self._lock:
            prompt = self._prompts.get(tracking_id)
            if prompt and not prompt.is_expired:
                return prompt
            # Remove expired prompt
            if prompt and prompt.is_expired:
                del self._prompts[tracking_id]
        
        return None
    
    def accept_prompt(self, tracking_id: str) -> Optional[PendingPrompt]:
        """Accept and remove a pending prompt from the registry.
        
        Args:
            tracking_id: The unique tracking ID
            
        Returns:
            The accepted PendingPrompt if found and not expired, None otherwise
        """
        with self._lock:
            prompt = self._prompts.pop(tracking_id, None)
            if prompt and prompt.is_expired:
                return None
            return prompt
    
    def list_prompts(self, include_expired: bool = False) -> list[PendingPrompt]:
        """List all pending prompts.
        
        Args:
            include_expired: Whether to include expired prompts
            
        Returns:
            List of pending prompts
        """
        with self._lock:
            prompts = list(self._prompts.values())
        
        if not include_expired:
            prompts = [p for p in prompts if not p.is_expired]
        
        return prompts
    
    def cleanup_expired(self) -> int:
        """Remove all expired prompts from the registry.
        
        Returns:
            Number of prompts removed
        """
        with self._lock:
            expired_ids = [
                tracking_id
                for tracking_id, prompt in self._prompts.items()
                if prompt.is_expired
            ]
            
            for tracking_id in expired_ids:
                del self._prompts[tracking_id]
        
        return len(expired_ids)
    
    def get_count(self) -> int:
        """Get the total number of pending prompts.
        
        Returns:
            Count of pending prompts
        """
        with self._lock:
            # Only count non-expired prompts
            return sum(1 for p in self._prompts.values() if not p.is_expired)
    
    def clear(self) -> int:
        """Clear all pending prompts from the registry.
        
        Returns:
            Number of prompts cleared
        """
        with self._lock:
            count = len(self._prompts)
            self._prompts.clear()
        
        return count
