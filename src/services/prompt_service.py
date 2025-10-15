"""Prompt service implementation extending BaseService.

This module implements prompt-specific business logic,
inheriting all common functionality from BaseService.
"""

from typing import Any, Dict, List, Optional

from ..core.base_service import BaseService
from ..core.validators import Validators, ValidationError
from ..repositories.prompt_repository import PromptRepository

# Import with fallbacks
try:
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.services.prompt")


class PromptService(BaseService):
    """Service for prompt management.
    
    Extends BaseService with prompt-specific business logic.
    All CRUD operations are inherited - only domain-specific logic added.
    """
    
    def __init__(self, repository: PromptRepository = None):
        """Initialize prompt service.
        
        Args:
            repository: PromptRepository instance (creates default if None)
        """
        if repository is None:
            repository = PromptRepository()
        super().__init__(repository)
        
        # Settings
        self.auto_save = True
        self.check_duplicates = True
        self.allowed_categories = None  # None means any category allowed
    
    def validate_create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate prompt data before creation.

        Args:
            data: Prompt data

        Returns:
            Validated and normalized data

        Raises:
            ValidationError: If validation fails
        """
        # Validate prompt text - accept either positive_prompt or prompt
        prompt_text = data.get("positive_prompt") or data.get("prompt", "")
        prompt = Validators.validate_prompt(
            prompt_text,
            allow_empty=False,
            max_length=10000
        )
        
        # Validate negative prompt
        negative_prompt = data.get("negative_prompt", "")
        if negative_prompt:
            negative_prompt = Validators.validate_prompt(
                negative_prompt,
                allow_empty=True,
                max_length=5000
            )
        
        # Validate category
        category = Validators.validate_category(
            data.get("category", ""),
            allowed_categories=self.allowed_categories
        )
        
        # Validate tags
        tags = Validators.validate_tags(data.get("tags", []))
        if not isinstance(tags, str):
            # Already a list, convert to JSON string for storage
            import json
            tags = json.dumps(tags)
        
        # Validate rating
        rating = 0
        if "rating" in data:
            rating = Validators.validate_rating(data["rating"])
        
        # Build validated data - use positive_prompt as the field name
        from datetime import datetime
        now = datetime.utcnow().isoformat()

        validated = {
            "positive_prompt": prompt,
            "negative_prompt": negative_prompt,
            "category": category,
            "tags": tags,
            "rating": rating,
            "notes": data.get("notes", ""),
            "created_at": now,
            "updated_at": now
        }

        # Add optional fields if they exist in the database schema
        # These can be added later when the schema is extended
        # "metadata": data.get("metadata", {}),
        # "workflow": data.get("workflow", {})
        
        # Check for duplicates if enabled
        if self.check_duplicates:
            hash_value = self.repository.calculate_hash(prompt, negative_prompt)
            existing = self.repository.find_by_hash(hash_value)
            if existing:
                raise ValidationError(
                    "prompt",
                    f"Duplicate prompt found (ID: {existing['id']})",
                    prompt
                )
        
        return validated
    
    def validate_update(self, id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate prompt data before update.
        
        Args:
            id: Prompt ID
            data: Update data
            
        Returns:
            Validated and normalized data
            
        Raises:
            ValidationError: If validation fails
        """
        validated = {}
        
        # Validate prompt if provided - accept either positive_prompt or prompt
        if "positive_prompt" in data or "prompt" in data:
            prompt_text = data.get("positive_prompt") or data.get("prompt", "")
            validated["positive_prompt"] = Validators.validate_prompt(
                prompt_text,
                allow_empty=False,
                max_length=10000
            )
        
        # Validate negative prompt if provided
        if "negative_prompt" in data:
            validated["negative_prompt"] = Validators.validate_prompt(
                data["negative_prompt"],
                allow_empty=True,
                max_length=5000
            )
        
        # Validate category if provided
        if "category" in data:
            validated["category"] = Validators.validate_category(
                data["category"],
                allowed_categories=self.allowed_categories
            )
        
        # Validate tags if provided
        if "tags" in data:
            tags = Validators.validate_tags(data["tags"])
            if not isinstance(tags, str):
                import json
                tags = json.dumps(tags)
            validated["tags"] = tags
        
        # Validate rating if provided
        if "rating" in data:
            validated["rating"] = Validators.validate_rating(data["rating"])
        
        # Pass through other fields that exist in the schema
        if "notes" in data:
            validated["notes"] = data["notes"]
        # Metadata and workflow fields can be added when schema is extended
        # for key in ["metadata", "workflow"]:
        #     if key in data:
        #         validated[key] = data[key]
        
        # Recalculate hash if positive_prompt or negative_prompt changed
        if "positive_prompt" in validated or "negative_prompt" in validated:
            existing = self.get(id)
            if existing:
                prompt = validated.get("positive_prompt", existing.get("positive_prompt", existing.get("prompt", "")))
                negative = validated.get("negative_prompt", existing.get("negative_prompt", ""))
                
                # Check for duplicates with new hash
                new_hash = self.repository.calculate_hash(prompt, negative)
                duplicate = self.repository.find_by_hash(new_hash)
                
                if duplicate and duplicate["id"] != id:
                    raise ValidationError(
                        "prompt",
                        f"Update would create duplicate (ID: {duplicate['id']})",
                        prompt
                    )
                
                validated["hash"] = new_hash
        
        return validated
    
    def _get_searchable_fields(self) -> List[str]:
        """Get default searchable fields.

        Returns:
            List of field names
        """
        return ["positive_prompt", "negative_prompt", "category", "notes"]
    
    # Prompt-specific methods
    
    def create_or_get(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create prompt or return existing if duplicate.
        
        Args:
            data: Prompt data
            
        Returns:
            Created or existing prompt
        """
        validated = self.validate_create(data)
        
        # Calculate hash
        hash_value = self.repository.calculate_hash(
            validated["positive_prompt"],
            validated["negative_prompt"]
        )
        
        # Check if exists
        existing = self.repository.find_by_hash(hash_value)
        if existing:
            # Increment usage count
            self.repository.increment_usage(existing["id"])
            return self.get(existing["id"], use_cache=False)
        
        # Create new
        return self.create(data)
    
    def use_prompt(self, prompt_id: int) -> bool:
        """Mark prompt as used (increment counter).
        
        Args:
            prompt_id: Prompt ID
            
        Returns:
            True if successful
        """
        success = self.repository.increment_usage(prompt_id)
        if success:
            self._invalidate_cache()
        return success
    
    def get_recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recently used prompts.
        
        Args:
            limit: Number of prompts
            
        Returns:
            List of recent prompts
        """
        return self.repository.get_recent(limit)
    
    def get_popular(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get most popular prompts.
        
        Args:
            limit: Number of prompts
            
        Returns:
            List of popular prompts
        """
        return self.repository.get_popular(limit)
    
    def get_by_category(self, category: str) -> List[Dict[str, Any]]:
        """Get prompts by category.
        
        Args:
            category: Category name
            
        Returns:
            List of prompts
        """
        return self.repository.get_by_category(category)
    
    def get_categories(self) -> List[Dict[str, Any]]:
        """Get all categories with counts.
        
        Returns:
            List of categories
        """
        return self.repository.get_categories()
    
    def rate_prompt(self, prompt_id: int, rating: int) -> Optional[Dict[str, Any]]:
        """Rate a prompt.
        
        Args:
            prompt_id: Prompt ID
            rating: Rating (1-5)
            
        Returns:
            Updated prompt or None
        """
        rating = Validators.validate_rating(rating)
        success = self.repository.update_rating(prompt_id, rating)
        
        if success:
            self._invalidate_cache()
            return self.get(prompt_id, use_cache=False)
        
        return None
    
    def find_duplicates(self) -> List[Dict[str, Any]]:
        """Find all duplicate prompts.
        
        Returns:
            List of duplicates
        """
        return self.repository.find_duplicates()
    
    def merge_duplicates(self, keep_id: int, remove_ids: List[int]) -> bool:
        """Merge duplicate prompts.
        
        Args:
            keep_id: ID of prompt to keep
            remove_ids: IDs of prompts to remove
            
        Returns:
            True if successful
        """
        if not remove_ids:
            return True
        
        # Get the prompt to keep
        keep_prompt = self.get(keep_id)
        if not keep_prompt:
            return False
        
        # Merge execution counts
        total_count = keep_prompt["execution_count"]
        for remove_id in remove_ids:
            remove_prompt = self.get(remove_id)
            if remove_prompt:
                total_count += remove_prompt["execution_count"]
        
        # Update kept prompt with total count
        self.update(keep_id, {"execution_count": total_count})
        
        # Delete duplicates
        deleted = self.bulk_delete(remove_ids)
        
        return deleted == len(remove_ids)
    
    def export_prompts(self, prompt_ids: List[int] = None) -> List[Dict[str, Any]]:
        """Export prompts for backup.
        
        Args:
            prompt_ids: Specific IDs to export (None for all)
            
        Returns:
            List of prompt data
        """
        if prompt_ids:
            return [self.get(pid) for pid in prompt_ids if self.get(pid)]
        else:
            return self.list(per_page=10000)["items"]
    
    def import_prompts(self, prompts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Import prompts from backup.
        
        Args:
            prompts: List of prompt data
            
        Returns:
            Import statistics
        """
        stats = {
            "total": len(prompts),
            "imported": 0,
            "skipped": 0,
            "errors": 0
        }
        
        for prompt_data in prompts:
            try:
                # Remove ID to create new
                prompt_data.pop("id", None)
                
                # Try to create or get existing
                result = self.create_or_get(prompt_data)
                if result:
                    stats["imported"] += 1
                else:
                    stats["skipped"] += 1
                    
            except Exception as e:
                logger.error(f"Failed to import prompt: {e}")
                stats["errors"] += 1
        
        return stats
    
    def set_allowed_categories(self, categories: List[str]):
        """Set allowed categories for validation.
        
        Args:
            categories: List of allowed category names
        """
        self.allowed_categories = categories
    
    def enable_auto_save(self):
        """Enable auto-save mode."""
        self.auto_save = True
        logger.info("Auto-save enabled")
    
    def disable_auto_save(self):
        """Disable auto-save mode."""
        self.auto_save = False
        logger.info("Auto-save disabled")
    
    def enable_duplicate_check(self):
        """Enable duplicate checking."""
        self.check_duplicates = True
        logger.info("Duplicate checking enabled")
    
    def disable_duplicate_check(self):
        """Disable duplicate checking."""
        self.check_duplicates = False
        logger.info("Duplicate checking disabled")