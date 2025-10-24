"""Service for accepting and persisting pending prompts to the database.

This module provides functionality to accept pending prompts and persist them
to the database, optionally linking them to generated images.
"""

import logging
from typing import Optional, Dict, Any, List

from ..tracking.pending_registry import PendingPromptRegistry, PendingPrompt
from ..repositories.prompt_repository import PromptRepository

try:
    from promptmanager.loggers import get_logger
except ImportError:
    from loggers import get_logger

logger = get_logger("promptmanager.services.prompt_acceptance")


class PromptAcceptanceService:
    """Service for accepting pending prompts and persisting to database.
    
    Handles the workflow of:
    1. Accepting a pending prompt from the registry
    2. Validating the prompt data
    3. Persisting to the database
    4. Optionally linking to generated images
    """
    
    def __init__(self, registry: PendingPromptRegistry, prompt_repo: PromptRepository):
        """Initialize the acceptance service.
        
        Args:
            registry: The PendingPromptRegistry instance
            prompt_repo: The PromptRepository for database operations
        """
        self.registry = registry
        self.prompt_repo = prompt_repo
    
    def accept_prompt(
        self,
        tracking_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Accept a pending prompt and persist it to the database.
        
        Args:
            tracking_id: The unique tracking ID of the pending prompt
            metadata: Optional additional metadata to merge with the prompt
            
        Returns:
            Dictionary with persisted prompt data including ID, or None if not found
        """
        pending_prompt = self.registry.accept_prompt(tracking_id)
        
        if not pending_prompt:
            logger.warning(f"Pending prompt not found or expired: {tracking_id}")
            return None
        
        return self._persist_prompt(pending_prompt, metadata)
    
    def reject_prompt(self, tracking_id: str) -> bool:
        """Reject and remove a pending prompt without persisting.
        
        Args:
            tracking_id: The unique tracking ID of the pending prompt
            
        Returns:
            True if prompt was removed, False if not found
        """
        pending_prompt = self.registry.accept_prompt(tracking_id)
        
        if not pending_prompt:
            logger.warning(f"Pending prompt not found: {tracking_id}")
            return False
        
        logger.info(f"Rejected pending prompt: {tracking_id}")
        return True
    
    def _persist_prompt(
        self,
        pending_prompt: PendingPrompt,
        additional_metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Persist a pending prompt to the database.
        
        If a prompt with the same hash already exists, this will:
        1. Find the existing prompt
        2. Update its timestamp to bring it to the top of the dashboard
        3. Return the existing prompt (so images get associated with it)
        
        This encourages prompt reuse and keeps actively used prompts visible.
        
        Args:
            pending_prompt: The pending prompt to persist
            additional_metadata: Optional metadata to merge with existing metadata
            
        Returns:
            Dictionary with persisted prompt data including ID, or None on error
        """
        import sqlite3
        from datetime import datetime
        
        try:
            prompt_data = {
                "positive_prompt": pending_prompt.positive_prompt,
                "negative_prompt": pending_prompt.negative_prompt or "",
                "category": pending_prompt.metadata.get("category", "uncategorized"),
                "tags": pending_prompt.metadata.get("tags", []),
                "notes": pending_prompt.metadata.get("notes", ""),
                "model_hash": pending_prompt.metadata.get("model_hash"),
                "sampler_settings": pending_prompt.metadata.get("sampler_settings"),
                "generation_params": pending_prompt.metadata.get("generation_params"),
            }
            
            if additional_metadata:
                prompt_data.update(additional_metadata)
            
            prompt_id = self.prompt_repo.create(prompt_data)
            
            logger.info(f"Persisted pending prompt {pending_prompt.tracking_id} as ID {prompt_id}")
            
            persisted = self.prompt_repo.get(prompt_id)
            return persisted
        
        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed: prompts.hash" in str(e):
                # Duplicate prompt detected - find and refresh the existing one
                prompt_hash = self.prompt_repo.calculate_hash(
                    pending_prompt.positive_prompt,
                    pending_prompt.negative_prompt or ""
                )
                
                existing = self.prompt_repo.find_by_hash(prompt_hash)
                
                if existing:
                    # Update timestamp to bring it to the top of the dashboard
                    self.prompt_repo.update(
                        existing['id'],
                        {'updated_at': datetime.utcnow().isoformat()}
                    )
                    
                    logger.info(
                        f"Prompt {pending_prompt.tracking_id} matches existing prompt {existing['id']} "
                        f"- refreshed timestamp to surface in dashboard"
                    )
                    
                    # Return refreshed prompt
                    return self.prompt_repo.get(existing['id'])
                else:
                    logger.error(f"Hash constraint failed but couldn't find existing prompt: {prompt_hash}")
                    return None
            else:
                # Some other integrity error
                raise
        
        except Exception as e:
            logger.error(
                f"Failed to persist pending prompt {pending_prompt.tracking_id}: {e}",
                exc_info=True
            )
            return None
    
    def get_pending_prompt(self, tracking_id: str) -> Optional[Dict[str, Any]]:
        """Get a pending prompt without accepting it.
        
        Args:
            tracking_id: The unique tracking ID
            
        Returns:
            Dictionary representation of the pending prompt, or None
        """
        pending_prompt = self.registry.get_prompt(tracking_id)
        
        if not pending_prompt:
            return None
        
        return {
            "tracking_id": pending_prompt.tracking_id,
            "positive_prompt": pending_prompt.positive_prompt,
            "negative_prompt": pending_prompt.negative_prompt,
            "metadata": pending_prompt.metadata,
            "created_at": pending_prompt.created_at,
            "age_seconds": pending_prompt.age_seconds,
            "is_expired": pending_prompt.is_expired,
        }
    
    def list_pending_prompts(self, include_expired: bool = False) -> List[Dict[str, Any]]:
        """List all pending prompts.
        
        Args:
            include_expired: Whether to include expired prompts
            
        Returns:
            List of pending prompt dictionaries
        """
        pending_prompts = self.registry.list_prompts(include_expired=include_expired)
        
        return [
            {
                "tracking_id": p.tracking_id,
                "positive_prompt": p.positive_prompt,
                "negative_prompt": p.negative_prompt,
                "metadata": p.metadata,
                "created_at": p.created_at,
                "age_seconds": p.age_seconds,
                "is_expired": p.is_expired,
            }
            for p in pending_prompts
        ]
    
    def get_pending_count(self) -> int:
        """Get count of pending (non-expired) prompts.
        
        Returns:
            Number of pending prompts
        """
        return self.registry.get_count()
