"""Base gallery class for DRY UI components.

This module provides a single source of truth for all gallery operations,
eliminating code duplication across different gallery implementations.
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.gallery")


class ViewMode(Enum):
    """Gallery view modes."""
    GRID = "grid"
    LIST = "list"
    COMPACT = "compact"
    DETAILED = "detailed"


class SortOrder(Enum):
    """Sort order options."""
    ASC = "asc"
    DESC = "desc"


class BaseGallery(ABC):
    """Abstract base gallery providing common UI functionality.
    
    All galleries should inherit from this class to ensure consistent
    UI behavior and eliminate code duplication.
    """
    
    def __init__(self, 
                 items_per_page: int = 20,
                 view_mode: ViewMode = ViewMode.GRID):
        """Initialize the gallery.
        
        Args:
            items_per_page: Number of items per page
            view_mode: Initial view mode
        """
        self.items_per_page = items_per_page
        self.view_mode = view_mode
        self.current_page = 1
        self.sort_field = "created_at"
        self.sort_order = SortOrder.DESC
        self.filters = {}
        self.search_query = ""
        self._items = []
        self._total_items = 0
        self._selected_items = set()
        
    @abstractmethod
    def load_items(self) -> List[Dict[str, Any]]:
        """Load items from the data source.
        
        Returns:
            List of items to display
        """
        pass
    
    @abstractmethod
    def render_item(self, item: Dict[str, Any]) -> str:
        """Render a single item.
        
        Args:
            item: Item data
            
        Returns:
            HTML string for the item
        """
        pass
    
    @abstractmethod
    def get_item_thumbnail(self, item: Dict[str, Any]) -> Optional[str]:
        """Get thumbnail URL/path for an item.
        
        Args:
            item: Item data
            
        Returns:
            Thumbnail URL/path or None
        """
        pass
    
    @abstractmethod
    def get_item_metadata(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Get display metadata for an item.
        
        Args:
            item: Item data
            
        Returns:
            Dictionary of metadata to display
        """
        pass
    
    # Common gallery operations
    
    def refresh(self):
        """Refresh the gallery items."""
        self._items = self.load_items()
        self._apply_filters()
        self._apply_search()
        self._apply_sort()
        self._paginate()
        logger.info(f"Gallery refreshed: {len(self._items)} items")
    
    def set_page(self, page: int):
        """Set the current page.
        
        Args:
            page: Page number (1-based)
        """
        max_page = self.get_total_pages()
        self.current_page = max(1, min(page, max_page))
        self._paginate()
    
    def next_page(self):
        """Go to the next page."""
        self.set_page(self.current_page + 1)
    
    def previous_page(self):
        """Go to the previous page."""
        self.set_page(self.current_page - 1)
    
    def set_view_mode(self, mode: ViewMode):
        """Set the view mode.
        
        Args:
            mode: New view mode
        """
        self.view_mode = mode
        logger.info(f"View mode changed to {mode.value}")
    
    def set_sort(self, field: str, order: SortOrder = None):
        """Set sort field and order.
        
        Args:
            field: Field to sort by
            order: Sort order (toggles if None)
        """
        if order is None:
            # Toggle order if same field
            if field == self.sort_field:
                self.sort_order = (SortOrder.ASC if self.sort_order == SortOrder.DESC 
                                  else SortOrder.DESC)
            else:
                self.sort_order = SortOrder.DESC
        else:
            self.sort_order = order
        
        self.sort_field = field
        self._apply_sort()
        self._paginate()
    
    def set_filter(self, key: str, value: Any):
        """Set a filter.
        
        Args:
            key: Filter key
            value: Filter value (None to remove)
        """
        if value is None:
            self.filters.pop(key, None)
        else:
            self.filters[key] = value
        
        self._apply_filters()
        self._paginate()
    
    def clear_filters(self):
        """Clear all filters."""
        self.filters.clear()
        self._apply_filters()
        self._paginate()
    
    def search(self, query: str):
        """Search items.
        
        Args:
            query: Search query
        """
        self.search_query = query
        self._apply_search()
        self._paginate()
    
    def select_item(self, item_id: Any):
        """Select an item.
        
        Args:
            item_id: Item identifier
        """
        self._selected_items.add(item_id)
    
    def deselect_item(self, item_id: Any):
        """Deselect an item.
        
        Args:
            item_id: Item identifier
        """
        self._selected_items.discard(item_id)
    
    def toggle_selection(self, item_id: Any):
        """Toggle item selection.
        
        Args:
            item_id: Item identifier
        """
        if item_id in self._selected_items:
            self.deselect_item(item_id)
        else:
            self.select_item(item_id)
    
    def select_all(self):
        """Select all visible items."""
        for item in self.get_current_page_items():
            self._selected_items.add(self._get_item_id(item))
    
    def deselect_all(self):
        """Deselect all items."""
        self._selected_items.clear()
    
    def get_selected_items(self) -> List[Dict[str, Any]]:
        """Get selected items.
        
        Returns:
            List of selected items
        """
        return [item for item in self._items 
                if self._get_item_id(item) in self._selected_items]
    
    def get_current_page_items(self) -> List[Dict[str, Any]]:
        """Get items for the current page.
        
        Returns:
            List of items on current page
        """
        start = (self.current_page - 1) * self.items_per_page
        end = start + self.items_per_page
        return self._items[start:end]
    
    def get_total_pages(self) -> int:
        """Get total number of pages.
        
        Returns:
            Total pages
        """
        return max(1, (self._total_items + self.items_per_page - 1) // self.items_per_page)
    
    def get_page_info(self) -> Dict[str, int]:
        """Get pagination information.
        
        Returns:
            Dictionary with page info
        """
        return {
            "current_page": self.current_page,
            "total_pages": self.get_total_pages(),
            "items_per_page": self.items_per_page,
            "total_items": self._total_items,
            "start_item": (self.current_page - 1) * self.items_per_page + 1,
            "end_item": min(self.current_page * self.items_per_page, self._total_items)
        }
    
    # Rendering methods
    
    def render(self) -> str:
        """Render the gallery.
        
        Returns:
            HTML string for the gallery
        """
        if self.view_mode == ViewMode.GRID:
            return self._render_grid()
        elif self.view_mode == ViewMode.LIST:
            return self._render_list()
        elif self.view_mode == ViewMode.COMPACT:
            return self._render_compact()
        else:
            return self._render_detailed()
    
    def _render_grid(self) -> str:
        """Render grid view."""
        items_html = []
        for item in self.get_current_page_items():
            items_html.append(f'<div class="gallery-item grid-item">{self.render_item(item)}</div>')
        
        return f'<div class="gallery-grid">{"".join(items_html)}</div>'
    
    def _render_list(self) -> str:
        """Render list view."""
        items_html = []
        for item in self.get_current_page_items():
            items_html.append(f'<div class="gallery-item list-item">{self.render_item(item)}</div>')
        
        return f'<div class="gallery-list">{"".join(items_html)}</div>'
    
    def _render_compact(self) -> str:
        """Render compact view."""
        items_html = []
        for item in self.get_current_page_items():
            items_html.append(f'<div class="gallery-item compact-item">{self.render_item(item)}</div>')
        
        return f'<div class="gallery-compact">{"".join(items_html)}</div>'
    
    def _render_detailed(self) -> str:
        """Render detailed view."""
        items_html = []
        for item in self.get_current_page_items():
            items_html.append(f'<div class="gallery-item detailed-item">{self.render_item(item)}</div>')
        
        return f'<div class="gallery-detailed">{"".join(items_html)}</div>'
    
    # Internal methods
    
    def _apply_filters(self):
        """Apply filters to items."""
        if not self.filters:
            return
        
        filtered = []
        for item in self._items:
            include = True
            for key, value in self.filters.items():
                if not self._match_filter(item, key, value):
                    include = False
                    break
            if include:
                filtered.append(item)
        
        self._items = filtered
        self._total_items = len(self._items)
    
    def _apply_search(self):
        """Apply search to items."""
        if not self.search_query:
            return
        
        query = self.search_query.lower()
        filtered = []
        
        for item in self._items:
            if self._match_search(item, query):
                filtered.append(item)
        
        self._items = filtered
        self._total_items = len(self._items)
    
    def _apply_sort(self):
        """Apply sorting to items."""
        reverse = self.sort_order == SortOrder.DESC
        
        try:
            self._items.sort(
                key=lambda x: x.get(self.sort_field, ""),
                reverse=reverse
            )
        except Exception as e:
            logger.warning(f"Sort failed: {e}")
    
    def _paginate(self):
        """Update pagination."""
        self._total_items = len(self._items)
        max_page = self.get_total_pages()
        
        if self.current_page > max_page:
            self.current_page = max_page
    
    def _match_filter(self, item: Dict[str, Any], key: str, value: Any) -> bool:
        """Check if item matches filter.
        
        Args:
            item: Item to check
            key: Filter key
            value: Filter value
            
        Returns:
            True if matches
        """
        item_value = item.get(key)
        
        if callable(value):
            return value(item_value)
        elif isinstance(value, (list, tuple)):
            return item_value in value
        else:
            return item_value == value
    
    def _match_search(self, item: Dict[str, Any], query: str) -> bool:
        """Check if item matches search.
        
        Args:
            item: Item to check
            query: Search query (lowercase)
            
        Returns:
            True if matches
        """
        # Search in all string fields
        for value in item.values():
            if isinstance(value, str) and query in value.lower():
                return True
        return False
    
    def _get_item_id(self, item: Dict[str, Any]) -> Any:
        """Get item identifier.
        
        Args:
            item: Item data
            
        Returns:
            Item identifier
        """
        return item.get("id", id(item))
