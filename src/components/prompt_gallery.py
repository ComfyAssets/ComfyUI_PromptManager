"""Prompt gallery implementation extending BaseGallery.

This module implements prompt-specific gallery functionality,
inheriting all common functionality from BaseGallery.
"""

from typing import Any, Dict, List, Optional

from ..components.base_gallery import BaseGallery, ViewMode
from ..services.prompt_service import PromptService

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.components.prompt_gallery")


class PromptGallery(BaseGallery):
    """Gallery for displaying and managing prompts.
    
    Extends BaseGallery with prompt-specific rendering and functionality.
    All common gallery operations are inherited - only domain-specific logic added.
    """
    
    def __init__(self, service: PromptService = None):
        """Initialize prompt gallery.
        
        Args:
            service: PromptService instance (creates default if None)
        """
        if service is None:
            service = PromptService()
        super().__init__(service)
        
        # Prompt-specific settings
        self.show_negative_prompts = True
        self.show_execution_count = True
        self.show_rating = True
        self.truncate_length = 200  # Characters to show in grid view
    
    def load_items(self):
        """Load items from service."""
        result = self.service.list(
            page=self.current_page,
            per_page=self.per_page,
            sort_by=self.sort_by,
            sort_desc=self.sort_desc,
            **self.filter_params
        )
        
        self.items = result.get("items", [])
        self.total_items = result.get("total", 0)
        self.total_pages = result.get("pages", 1)
    
    def render_item(self, item: Dict[str, Any]) -> str:
        """Render a single item based on current view mode."""
        if self.view_mode == ViewMode.GRID:
            return self.render_item_grid(item)
        elif self.view_mode == ViewMode.LIST:
            return self.render_item_list(item)
        elif self.view_mode == ViewMode.COMPACT:
            return self.render_item_compact(item)
        elif self.view_mode == ViewMode.DETAILED:
            return self.render_item_detailed(item)
        else:
            return self.render_item_list(item)
    
    def get_item_thumbnail(self, item: Dict[str, Any]) -> Optional[str]:
        """Get thumbnail for an item (not applicable for prompts)."""
        return None
    
    def get_item_metadata(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Get metadata for an item."""
        return {
            "id": item.get("id"),
            "prompt": item.get("prompt"),
            "negative_prompt": item.get("negative_prompt"),
            "category": item.get("category"),
            "tags": item.get("tags", []),
            "rating": item.get("rating", 0),
            "execution_count": item.get("execution_count", 0),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at")
        }
    
    def render_item_grid(self, item: Dict[str, Any]) -> str:
        """Render a prompt in grid view.
        
        Args:
            item: Prompt data
            
        Returns:
            HTML string for grid item
        """
        # Truncate prompt for grid view
        prompt_text = item.get("prompt", "")
        if len(prompt_text) > self.truncate_length:
            prompt_text = prompt_text[:self.truncate_length] + "..."
        
        # Build grid item HTML
        html = f"""
        <div class="prompt-grid-item" data-id="{item['id']}">
            <div class="prompt-text">{prompt_text}</div>
            <div class="prompt-meta">
                <span class="category">{item.get('category', 'uncategorized')}</span>
                {self._render_rating(item.get('rating', 0))}
                <span class="usage-count">Used: {item.get('execution_count', 0)}</span>
            </div>
            {self._render_tags(item.get('tags', []))}
        </div>
        """
        return html
    
    def render_item_list(self, item: Dict[str, Any]) -> str:
        """Render a prompt in list view.
        
        Args:
            item: Prompt data
            
        Returns:
            HTML string for list item
        """
        html = f"""
        <div class="prompt-list-item" data-id="{item['id']}">
            <div class="prompt-main">
                <div class="prompt-text">{item.get('prompt', '')}</div>
                {self._render_negative_prompt(item.get('negative_prompt', ''))}
            </div>
            <div class="prompt-sidebar">
                <div class="category">{item.get('category', 'uncategorized')}</div>
                {self._render_rating(item.get('rating', 0))}
                <div class="usage">Used {item.get('execution_count', 0)} times</div>
                <div class="updated">{item.get('updated_at', '')}</div>
            </div>
        </div>
        """
        return html
    
    def render_item_compact(self, item: Dict[str, Any]) -> str:
        """Render a prompt in compact view.
        
        Args:
            item: Prompt data
            
        Returns:
            HTML string for compact item
        """
        # Very minimal display for compact view
        prompt_preview = item.get("prompt", "")[:100]
        if len(item.get("prompt", "")) > 100:
            prompt_preview += "..."
        
        html = f"""
        <div class="prompt-compact-item" data-id="{item['id']}">
            <span class="prompt-preview">{prompt_preview}</span>
            <span class="prompt-category">{item.get('category', '')}</span>
            <span class="prompt-rating">{self._render_stars(item.get('rating', 0))}</span>
        </div>
        """
        return html
    
    def render_item_detailed(self, item: Dict[str, Any]) -> str:
        """Render a prompt in detailed view.
        
        Args:
            item: Prompt data
            
        Returns:
            HTML string for detailed item
        """
        html = f"""
        <div class="prompt-detailed-item" data-id="{item['id']}">
            <div class="prompt-header">
                <h3>Prompt #{item['id']}</h3>
                <div class="prompt-actions">
                    <button class="btn-edit" data-id="{item['id']}">Edit</button>
                    <button class="btn-duplicate" data-id="{item['id']}">Duplicate</button>
                    <button class="btn-delete" data-id="{item['id']}">Delete</button>
                </div>
            </div>
            
            <div class="prompt-content">
                <div class="prompt-section">
                    <label>Main Prompt:</label>
                    <div class="prompt-text">{item.get('prompt', '')}</div>
                </div>
                
                {self._render_negative_prompt_section(item.get('negative_prompt', ''))}
                
                <div class="prompt-metadata">
                    <div class="meta-item">
                        <label>Category:</label>
                        <span>{item.get('category', 'uncategorized')}</span>
                    </div>
                    <div class="meta-item">
                        <label>Rating:</label>
                        {self._render_rating(item.get('rating', 0))}
                    </div>
                    <div class="meta-item">
                        <label>Usage Count:</label>
                        <span>{item.get('execution_count', 0)}</span>
                    </div>
                    <div class="meta-item">
                        <label>Last Used:</label>
                        <span>{item.get('last_used', 'Never')}</span>
                    </div>
                </div>
                
                {self._render_tags_section(item.get('tags', []))}
                
                {self._render_notes_section(item.get('notes', ''))}
                
                <div class="prompt-footer">
                    <span class="created">Created: {item.get('created_at', '')}</span>
                    <span class="updated">Updated: {item.get('updated_at', '')}</span>
                    <span class="hash">Hash: {item.get('hash', '')[:8]}...</span>
                </div>
            </div>
        </div>
        """
        return html
    
    # Prompt-specific rendering helpers
    
    def _render_rating(self, rating: int) -> str:
        """Render rating as stars.
        
        Args:
            rating: Rating value (0-5)
            
        Returns:
            HTML string for rating
        """
        if not self.show_rating:
            return ""
        
        stars = self._render_stars(rating)
        return f'<span class="rating" data-rating="{rating}">{stars}</span>'
    
    def _render_stars(self, rating: int) -> str:
        """Render star characters for rating.
        
        Args:
            rating: Rating value (0-5)
            
        Returns:
            Star string
        """
        filled = "★" * rating
        empty = "☆" * (5 - rating)
        return filled + empty
    
    def _render_negative_prompt(self, negative_prompt: str) -> str:
        """Render negative prompt if enabled.
        
        Args:
            negative_prompt: Negative prompt text
            
        Returns:
            HTML string or empty
        """
        if not self.show_negative_prompts or not negative_prompt:
            return ""
        
        return f"""
        <div class="negative-prompt">
            <span class="label">Negative:</span>
            <span class="text">{negative_prompt}</span>
        </div>
        """
    
    def _render_negative_prompt_section(self, negative_prompt: str) -> str:
        """Render negative prompt section for detailed view.
        
        Args:
            negative_prompt: Negative prompt text
            
        Returns:
            HTML string or empty
        """
        if not negative_prompt:
            return ""
        
        return f"""
        <div class="prompt-section">
            <label>Negative Prompt:</label>
            <div class="negative-prompt-text">{negative_prompt}</div>
        </div>
        """
    
    def _render_tags(self, tags: List[str]) -> str:
        """Render tags list.
        
        Args:
            tags: List of tag strings
            
        Returns:
            HTML string for tags
        """
        if not tags:
            return ""
        
        tag_html = "".join([f'<span class="tag">{tag}</span>' for tag in tags])
        return f'<div class="tags">{tag_html}</div>'
    
    def _render_tags_section(self, tags: List[str]) -> str:
        """Render tags section for detailed view.
        
        Args:
            tags: List of tag strings
            
        Returns:
            HTML string or empty
        """
        if not tags:
            return ""
        
        tag_html = "".join([f'<span class="tag">{tag}</span>' for tag in tags])
        return f"""
        <div class="prompt-section">
            <label>Tags:</label>
            <div class="tags-list">{tag_html}</div>
        </div>
        """
    
    def _render_notes_section(self, notes: str) -> str:
        """Render notes section for detailed view.
        
        Args:
            notes: Notes text
            
        Returns:
            HTML string or empty
        """
        if not notes:
            return ""
        
        return f"""
        <div class="prompt-section">
            <label>Notes:</label>
            <div class="notes-text">{notes}</div>
        </div>
        """
    
    # Gallery-specific methods
    
    def get_categories(self) -> List[Dict[str, Any]]:
        """Get all categories with counts.
        
        Returns:
            List of category data
        """
        return self.service.get_categories()
    
    def filter_by_category(self, category: str):
        """Filter gallery by category.
        
        Args:
            category: Category name
        """
        self.filter_params["category"] = category
        self.current_page = 1
        self.load_items()
    
    def filter_by_rating(self, min_rating: int):
        """Filter gallery by minimum rating.
        
        Args:
            min_rating: Minimum rating (1-5)
        """
        self.filter_params["min_rating"] = min_rating
        self.current_page = 1
        self.load_items()
    
    def get_recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recently used prompts.
        
        Args:
            limit: Number of prompts
            
        Returns:
            List of recent prompts
        """
        return self.service.get_recent(limit)
    
    def get_popular(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get most popular prompts.
        
        Args:
            limit: Number of prompts
            
        Returns:
            List of popular prompts
        """
        return self.service.get_popular(limit)
    
    def rate_prompt(self, prompt_id: int, rating: int) -> bool:
        """Rate a prompt.
        
        Args:
            prompt_id: Prompt ID
            rating: Rating (1-5)
            
        Returns:
            True if successful
        """
        result = self.service.rate_prompt(prompt_id, rating)
        if result:
            # Refresh the item in gallery
            self.load_items()
        return result is not None
    
    def duplicate_prompt(self, prompt_id: int) -> Optional[Dict[str, Any]]:
        """Duplicate a prompt.
        
        Args:
            prompt_id: Prompt ID to duplicate
            
        Returns:
            New prompt data or None
        """
        original = self.service.get(prompt_id)
        if not original:
            return None
        
        # Remove ID and timestamps for duplication
        duplicate_data = original.copy()
        duplicate_data.pop("id", None)
        duplicate_data.pop("created_at", None)
        duplicate_data.pop("updated_at", None)
        duplicate_data.pop("last_used", None)
        duplicate_data["notes"] = f"Duplicated from #{prompt_id}"
        
        return self.service.create(duplicate_data)
    
    def export_selected(self, prompt_ids: List[int]) -> List[Dict[str, Any]]:
        """Export selected prompts.
        
        Args:
            prompt_ids: List of prompt IDs
            
        Returns:
            List of prompt data for export
        """
        return self.service.export_prompts(prompt_ids)
    
    def import_prompts(self, prompts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Import prompts from export.
        
        Args:
            prompts: List of prompt data
            
        Returns:
            Import statistics
        """
        stats = self.service.import_prompts(prompts)
        # Refresh gallery after import
        self.load_items()
        return stats
