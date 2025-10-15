"""Base service class for DRY business logic.

This module provides a single source of truth for all service operations,
eliminating code duplication across different services.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, TypeVar

from .core.base_repository import BaseRepository

# Import with fallbacks
try:
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.service")

T = TypeVar('T')


class BaseService(ABC):
    """Abstract base service providing common business logic.
    
    All services should inherit from this class to ensure consistent
    operation handling and eliminate code duplication.
    """
    
    def __init__(self, repository: BaseRepository):
        """Initialize the service with a repository.
        
        Args:
            repository: Repository instance for data access
        """
        self.repository = repository
        self._cache = {}
        self._cache_enabled = True
        
    @abstractmethod
    def validate_create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate data before creation.
        
        Args:
            data: Data to validate
            
        Returns:
            Validated and normalized data
            
        Raises:
            ValueError: If validation fails
        """
        pass
    
    @abstractmethod
    def validate_update(self, id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate data before update.
        
        Args:
            id: Record ID
            data: Data to validate
            
        Returns:
            Validated and normalized data
            
        Raises:
            ValueError: If validation fails
        """
        pass
    
    def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new record with validation.
        
        Args:
            data: Record data
            
        Returns:
            Created record with ID
        """
        # Validate
        validated_data = self.validate_create(data)
        
        # Create
        record_id = self.repository.create(validated_data)
        
        # Clear cache
        self._invalidate_cache()
        
        # Return created record
        return self.get(record_id)
    
    def get(self, id: int, use_cache: bool = True) -> Optional[Dict[str, Any]]:
        """Get a record by ID.
        
        Args:
            id: Record ID
            use_cache: Whether to use cache
            
        Returns:
            Record data or None
        """
        cache_key = f"record_{id}"
        
        if use_cache and self._cache_enabled and cache_key in self._cache:
            logger.debug(f"Cache hit for {cache_key}")
            return self._cache[cache_key]
        
        record = self.repository.read(id)
        
        if record and self._cache_enabled:
            self._cache[cache_key] = record
        
        return record
    
    def update(self, id: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a record with validation.
        
        Args:
            id: Record ID
            data: Update data
            
        Returns:
            Updated record or None if not found
        """
        # Check existence
        existing = self.get(id)
        if not existing:
            return None
        
        # Validate
        validated_data = self.validate_update(id, data)
        
        # Update
        success = self.repository.update(id, validated_data)
        
        if success:
            # Clear cache
            self._invalidate_cache()
            # Return updated record
            return self.get(id, use_cache=False)
        
        return None
    
    def delete(self, id: int) -> bool:
        """Delete a record.
        
        Args:
            id: Record ID
            
        Returns:
            True if deleted, False if not found
        """
        success = self.repository.delete(id)
        
        if success:
            self._invalidate_cache()
        
        return success
    
    def list(self,
             page: int = 1,
             per_page: int = 20,
             sort_by: str = "created_at",
             sort_desc: bool = True,
             sort_order: str = None,
             **filters) -> Dict[str, Any]:
        """List records with pagination.

        Args:
            page: Page number (1-based)
            per_page: Items per page
            sort_by: Sort field
            sort_desc: Sort descending (True/False)
            sort_order: Sort order (asc/desc) - overrides sort_desc if provided
            **filters: Additional filters

        Returns:
            Dictionary with items, total, page, per_page
        """
        offset = (page - 1) * per_page

        # Handle both sort_desc and sort_order parameters
        if sort_order:
            order_direction = sort_order.upper()
        else:
            order_direction = "DESC" if sort_desc else "ASC"

        order_by = f"{sort_by} {order_direction}"
        
        items = self.repository.list(
            limit=per_page,
            offset=offset,
            order_by=order_by,
            **filters
        )
        
        total = self.repository.count(**filters)
        
        return {
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page
        }
    
    def search(self, query: str, fields: List[str] = None) -> List[Dict[str, Any]]:
        """Search records.
        
        Args:
            query: Search query
            fields: Fields to search in (None for default)
            
        Returns:
            List of matching records
        """
        if not fields:
            fields = self._get_searchable_fields()
        
        return self.repository.search(query, fields)
    
    def bulk_create(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Create multiple records.
        
        Args:
            items: List of record data
            
        Returns:
            List of created records
        """
        validated_items = []
        for item in items:
            validated_items.append(self.validate_create(item))
        
        ids = self.repository.bulk_create(validated_items)
        
        self._invalidate_cache()
        
        return [self.get(id, use_cache=False) for id in ids]
    
    def bulk_delete(self, ids: List[int]) -> int:
        """Delete multiple records.
        
        Args:
            ids: List of record IDs
            
        Returns:
            Number of deleted records
        """
        count = self.repository.bulk_delete(ids)
        
        if count > 0:
            self._invalidate_cache()
        
        return count
    
    def exists(self, **filters) -> bool:
        """Check if a record exists.
        
        Args:
            **filters: Filter criteria
            
        Returns:
            True if exists, False otherwise
        """
        return self.repository.exists(**filters)
    
    def count(self, **filters) -> int:
        """Count records.
        
        Args:
            **filters: Filter criteria
            
        Returns:
            Number of matching records
        """
        return self.repository.count(**filters)
    
    # Cache management
    
    def enable_cache(self):
        """Enable caching."""
        self._cache_enabled = True
        logger.info("Cache enabled")
    
    def disable_cache(self):
        """Disable caching."""
        self._cache_enabled = False
        self._cache.clear()
        logger.info("Cache disabled")
    
    def _invalidate_cache(self):
        """Clear the cache."""
        self._cache.clear()
        logger.debug("Cache invalidated")
    
    # Abstract methods for subclasses
    
    @abstractmethod
    def _get_searchable_fields(self) -> List[str]:
        """Get default searchable fields.
        
        Returns:
            List of field names
        """
        pass
    
    # Hooks for subclasses
    
    def before_create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Hook called before creation.
        
        Args:
            data: Record data
            
        Returns:
            Modified data
        """
        return data
    
    def after_create(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Hook called after creation.
        
        Args:
            record: Created record
            
        Returns:
            Modified record
        """
        return record
    
    def before_update(self, id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """Hook called before update.
        
        Args:
            id: Record ID
            data: Update data
            
        Returns:
            Modified data
        """
        return data
    
    def after_update(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Hook called after update.
        
        Args:
            record: Updated record
            
        Returns:
            Modified record
        """
        return record
    
    def before_delete(self, id: int) -> bool:
        """Hook called before deletion.
        
        Args:
            id: Record ID
            
        Returns:
            True to proceed, False to cancel
        """
        return True
    
    def after_delete(self, id: int):
        """Hook called after deletion.
        
        Args:
            id: Record ID
        """
        pass