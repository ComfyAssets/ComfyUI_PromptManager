"""Base classes for shared functionality across components."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union
import logging

from .sorting import normalize_sort_value


class BaseComponent(ABC):
    """Base class for all PromptManager components.
    
    Provides common functionality for logging, configuration,
    and error handling that all components need.
    """

    def __init__(self, logger_name: str):
        """Initialize base component with logging.
        
        Args:
            logger_name: Name for the component's logger
        """
        self.logger = logging.getLogger(logger_name)
        self._config: Dict[str, Any] = {}

    def configure(self, config: Dict[str, Any]) -> None:
        """Configure the component with settings.
        
        Args:
            config: Configuration dictionary
        """
        self._config.update(config)

    def get_config(self, key: str, default: Any = None) -> Any:
        """Get configuration value.
        
        Args:
            key: Configuration key
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        return self._config.get(key, default)


class BaseDatabase(BaseComponent):
    """Base class for database operations.
    
    Provides common database connection management,
    transaction handling, and CRUD operation patterns.
    """

    def __init__(self, db_path: str):
        """Initialize database component.
        
        Args:
            db_path: Path to the database file
        """
        super().__init__("promptmanager.database")
        self.db_path = db_path

    @abstractmethod
    def get_connection(self):
        """Get database connection."""
        pass

    @abstractmethod
    def create(self, data: Dict[str, Any]) -> int:
        """Create new record.
        
        Args:
            data: Record data
            
        Returns:
            ID of created record
        """
        pass

    @abstractmethod
    def read(self, id: Union[int, str]) -> Optional[Dict[str, Any]]:
        """Read record by ID.
        
        Args:
            id: Record identifier
            
        Returns:
            Record data or None if not found
        """
        pass

    @abstractmethod
    def update(self, id: Union[int, str], data: Dict[str, Any]) -> bool:
        """Update record by ID.
        
        Args:
            id: Record identifier
            data: Updated data
            
        Returns:
            True if updated successfully
        """
        pass

    @abstractmethod
    def delete(self, id: Union[int, str]) -> bool:
        """Delete record by ID.
        
        Args:
            id: Record identifier
            
        Returns:
            True if deleted successfully
        """
        pass

    @abstractmethod
    def list(self, **filters) -> List[Dict[str, Any]]:
        """List records with optional filtering.
        
        Args:
            **filters: Filter parameters
            
        Returns:
            List of matching records
        """
        pass


class LegacyBaseGallery(BaseComponent):
    """Base class for gallery functionality.
    
    Provides common gallery operations like loading items,
    rendering views, filtering, sorting, and pagination.
    """

    def __init__(self, name: str):
        """Initialize gallery component.
        
        Args:
            name: Gallery name/identifier
        """
        super().__init__(f"promptmanager.gallery.{name}")
        self.name = name
        self._items: List[Dict[str, Any]] = []
        self._filters: Dict[str, Any] = {}
        self._sort_key: str = "created_at"
        self._sort_reverse: bool = True
        self._page_size: int = 50
        self._current_page: int = 1

    @abstractmethod
    def load_items(self, **kwargs) -> List[Dict[str, Any]]:
        """Load gallery items from source.
        
        Args:
            **kwargs: Load parameters
            
        Returns:
            List of gallery items
        """
        pass

    @abstractmethod
    def render_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Render individual gallery item.
        
        Args:
            item: Item data
            
        Returns:
            Rendered item data for frontend
        """
        pass

    def apply_filters(self, filters: Dict[str, Any]) -> None:
        """Apply filters to gallery items.
        
        Args:
            filters: Filter parameters
        """
        self._filters.update(filters)
        self._current_page = 1  # Reset to first page

    def set_sort(self, key: str, reverse: bool = False) -> None:
        """Set sorting parameters.
        
        Args:
            key: Sort key/field
            reverse: Sort in reverse order
        """
        self._sort_key = key
        self._sort_reverse = reverse
        self._current_page = 1  # Reset to first page

    def set_pagination(self, page: int, page_size: int = None) -> None:
        """Set pagination parameters.
        
        Args:
            page: Page number (1-based)
            page_size: Items per page
        """
        self._current_page = max(1, page)
        if page_size is not None:
            self._page_size = max(1, page_size)

    def get_paginated_items(self) -> Dict[str, Any]:
        """Get current page of filtered and sorted items.

        Returns:
            Dictionary with items, pagination info
        """
        # Ensure pagination values are set
        if self._page_size is None:
            self._page_size = 50
        if self._current_page is None:
            self._current_page = 1

        # Load items for current page (already paginated from database)
        self._items = self.load_items()

        # For server-side pagination, we need the total count separately
        # Check if the subclass has a get_total_count method
        if hasattr(self, 'get_total_count'):
            total_items = self.get_total_count(**self._filters)
        else:
            # Fallback: if no total count method, assume items are all items
            # This maintains backward compatibility but won't work well for large datasets
            total_items = len(self._items)

        # Calculate total pages based on total count
        total_pages = (total_items + self._page_size - 1) // self._page_size if self._page_size > 0 else 1

        # Render items for frontend (items are already the correct page)
        rendered_items = [self.render_item(item) for item in self._items]
        
        return {
            "items": rendered_items,
            "pagination": {
                "current_page": self._current_page,
                "total_pages": total_pages,
                "total_items": total_items,
                "page_size": self._page_size,
                "has_next": self._current_page < total_pages,
                "has_prev": self._current_page > 1
            },
            "filters": self._filters,
            "sort": {
                "key": self._sort_key,
                "reverse": self._sort_reverse
            }
        }

    def _apply_filters(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply current filters to items list.
        
        Args:
            items: Items to filter
            
        Returns:
            Filtered items list
        """
        if not self._filters:
            return items
            
        filtered = []
        for item in items:
            if self._item_matches_filters(item):
                filtered.append(item)
        return filtered

    def _item_matches_filters(self, item: Dict[str, Any]) -> bool:
        """Check if item matches current filters.
        
        Args:
            item: Item to check
            
        Returns:
            True if item matches all filters
        """
        for key, value in self._filters.items():
            if not self._matches_filter(item, key, value):
                return False
        return True

    def _matches_filter(self, item: Dict[str, Any], key: str, value: Any) -> bool:
        """Check if item matches specific filter.
        
        Args:
            item: Item to check
            key: Filter key
            value: Filter value
            
        Returns:
            True if item matches filter
        """
        item_value = item.get(key)
        
        if value is None:
            return True
            
        if isinstance(value, str):
            # Text search (case insensitive)
            return value.lower() in str(item_value or "").lower()
        elif isinstance(value, (list, tuple)):
            # Multiple values (OR logic)
            return item_value in value
        else:
            # Exact match
            return item_value == value

    def _apply_sorting(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply current sorting to items list.

        Args:
            items: Items to sort

        Returns:
            Sorted items list
        """
        try:
            normalized = sorted(
                items,
                key=lambda x: normalize_sort_value(x.get(self._sort_key)),
            )
            if self._sort_reverse:
                normalized.reverse()
            return normalized
        except TypeError as sort_error:
            self.logger.warning(
                "Falling back to string sort for %s due to %s",
                self._sort_key,
                sort_error,
            )
            try:
                fallback = sorted(
                    items,
                    key=lambda x: (
                        x.get(self._sort_key) is None,
                        str(x.get(self._sort_key)),
                    ),
                    reverse=self._sort_reverse,
                )
                return fallback
            except Exception as fallback_error:
                self.logger.error(
                    "Fallback sort failed for %s: %s",
                    self._sort_key,
                    fallback_error,
                )
                return items
        except Exception as e:
            self.logger.warning(f"Failed to sort by {self._sort_key}: {e}")
            return items


class BaseMetadataViewer(BaseComponent):
    """Base class for metadata viewing functionality.
    
    Provides common metadata parsing, formatting, and display
    operations that different metadata viewers share.
    """

    def __init__(self, name: str):
        """Initialize metadata viewer.
        
        Args:
            name: Viewer name/identifier
        """
        super().__init__(f"promptmanager.metadata.{name}")
        self.name = name

    @abstractmethod
    def parse_metadata(self, source: Any) -> Dict[str, Any]:
        """Parse metadata from source.
        
        Args:
            source: Metadata source (file, data, etc.)
            
        Returns:
            Parsed metadata dictionary
        """
        pass

    @abstractmethod
    def format_for_display(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Format metadata for display in UI.
        
        Args:
            metadata: Raw metadata
            
        Returns:
            Formatted metadata for frontend display
        """
        pass

    def extract_key_info(self, metadata: Dict[str, Any]) -> Dict[str, str]:
        """Extract key information for quick display.
        
        Args:
            metadata: Metadata dictionary
            
        Returns:
            Key-value pairs for summary display
        """
        key_info = {}
        
        # Common keys across different metadata types
        common_keys = [
            "prompt", "negative_prompt", "steps", "sampler", "cfg_scale",
            "seed", "size", "model", "created_at", "filename"
        ]
        
        for key in common_keys:
            if key in metadata and metadata[key] is not None:
                value = str(metadata[key])
                if len(value) > 100:  # Truncate long values
                    value = value[:100] + "..."
                key_info[key] = value
                
        return key_info

    def validate_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and clean metadata.
        
        Args:
            metadata: Raw metadata
            
        Returns:
            Validated and cleaned metadata
        """
        cleaned = {}
        
        for key, value in metadata.items():
            # Clean key name
            clean_key = key.strip().lower().replace(" ", "_")
            
            # Clean value
            if isinstance(value, str):
                clean_value = value.strip()
                if clean_value:
                    cleaned[clean_key] = clean_value
            elif value is not None:
                cleaned[clean_key] = value
                
        return cleaned
