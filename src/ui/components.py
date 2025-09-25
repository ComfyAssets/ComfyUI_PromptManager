"""Reusable UI components for the professional interface."""

from typing import Any, Dict, List, Optional, Union
import json


class BaseUIComponent:
    """Base class for UI components."""
    
    def __init__(self, component_id: str):
        """Initialize UI component.
        
        Args:
            component_id: Unique identifier for the component
        """
        self.component_id = component_id
        self.classes: List[str] = []
        self.attributes: Dict[str, Any] = {}
        self.data_attributes: Dict[str, Any] = {}
        
    def add_class(self, *class_names: str) -> "BaseUIComponent":
        """Add CSS classes to component.
        
        Args:
            *class_names: CSS class names to add
            
        Returns:
            Self for method chaining
        """
        self.classes.extend(class_names)
        return self
    
    def set_attribute(self, name: str, value: Any) -> "BaseUIComponent":
        """Set HTML attribute.
        
        Args:
            name: Attribute name
            value: Attribute value
            
        Returns:
            Self for method chaining
        """
        self.attributes[name] = value
        return self
    
    def set_data(self, key: str, value: Any) -> "BaseUIComponent":
        """Set data attribute.
        
        Args:
            key: Data attribute key (without data- prefix)
            value: Data attribute value
            
        Returns:
            Self for method chaining
        """
        self.data_attributes[key] = value
        return self
    
    def _render_attributes(self) -> str:
        """Render HTML attributes string.
        
        Returns:
            HTML attributes string
        """
        attrs = []
        
        # ID attribute
        if self.component_id:
            attrs.append(f'id="{self.component_id}"')
        
        # CSS classes
        if self.classes:
            class_string = " ".join(self.classes)
            attrs.append(f'class="{class_string}"')
        
        # Regular attributes
        for name, value in self.attributes.items():
            if isinstance(value, bool):
                if value:
                    attrs.append(name)
            else:
                attrs.append(f'{name}="{value}"')
        
        # Data attributes
        for key, value in self.data_attributes.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            attrs.append(f'data-{key}="{value}"')
        
        return " ".join(attrs)


class Button(BaseUIComponent):
    """Professional button component."""
    
    def __init__(
        self, 
        component_id: str, 
        text: str, 
        variant: str = "default",
        size: str = "medium",
        disabled: bool = False
    ):
        """Initialize button.
        
        Args:
            component_id: Unique button ID
            text: Button text
            variant: Button style variant (default, primary, success, warning, error)
            size: Button size (small, medium, large)
            disabled: Whether button is disabled
        """
        super().__init__(component_id)
        self.text = text
        self.variant = variant
        self.size = size
        self.disabled = disabled
        
        # Apply base classes
        self.add_class("pm-button")
        
        # Apply variant classes
        if variant != "default":
            self.add_class(f"pm-button-{variant}")
        
        # Apply size classes
        if size != "medium":
            self.add_class(f"pm-button-{size}")
        
        # Set disabled state
        if disabled:
            self.set_attribute("disabled", True)
    
    def render(self) -> str:
        """Render button HTML.
        
        Returns:
            HTML string
        """
        attrs = self._render_attributes()
        return f'<button {attrs}>{self.text}</button>'


class Card(BaseUIComponent):
    """Professional card container component."""
    
    def __init__(
        self, 
        component_id: str, 
        title: Optional[str] = None,
        hoverable: bool = True
    ):
        """Initialize card.
        
        Args:
            component_id: Unique card ID
            title: Optional card title
            hoverable: Whether card has hover effects
        """
        super().__init__(component_id)
        self.title = title
        self.hoverable = hoverable
        self.content: List[str] = []
        
        # Apply base classes
        self.add_class("pm-card")
        
        if not hoverable:
            self.add_class("pm-card-static")
    
    def add_content(self, content: str) -> "Card":
        """Add content to card.
        
        Args:
            content: HTML content to add
            
        Returns:
            Self for method chaining
        """
        self.content.append(content)
        return self
    
    def render(self) -> str:
        """Render card HTML.
        
        Returns:
            HTML string
        """
        attrs = self._render_attributes()
        html = [f'<div {attrs}>']
        
        # Add title if provided
        if self.title:
            html.append(f'<h3 class="pm-heading pm-heading-md">{self.title}</h3>')
        
        # Add content
        html.extend(self.content)
        
        html.append('</div>')
        return "".join(html)


class Gallery(BaseUIComponent):
    """Professional gallery component for displaying images."""
    
    def __init__(
        self, 
        component_id: str,
        columns: int = 4,
        gap: str = "base"
    ):
        """Initialize gallery.
        
        Args:
            component_id: Unique gallery ID
            columns: Number of columns in grid
            gap: Gap size between items
        """
        super().__init__(component_id)
        self.columns = columns
        self.gap = gap
        self.items: List[Dict[str, Any]] = []
        
        # Apply base classes
        self.add_class("pm-gallery")
        self.set_data("columns", columns)
        self.set_data("gap", gap)
    
    def add_item(
        self, 
        image_url: str, 
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> "Gallery":
        """Add item to gallery.
        
        Args:
            image_url: URL/path to image
            title: Optional item title
            metadata: Optional metadata dictionary
            
        Returns:
            Self for method chaining
        """
        item = {
            "image_url": image_url,
            "title": title,
            "metadata": metadata or {}
        }
        self.items.append(item)
        return self
    
    def render(self) -> str:
        """Render gallery HTML.
        
        Returns:
            HTML string
        """
        attrs = self._render_attributes()
        html = [f'<div {attrs}>']
        
        # Generate grid items
        for i, item in enumerate(self.items):
            item_id = f"{self.component_id}-item-{i}"
            item_html = self._render_gallery_item(item_id, item)
            html.append(item_html)
        
        html.append('</div>')
        return "".join(html)
    
    def _render_gallery_item(self, item_id: str, item: Dict[str, Any]) -> str:
        """Render individual gallery item.
        
        Args:
            item_id: Unique item ID
            item: Item data
            
        Returns:
            HTML string for gallery item
        """
        metadata_json = json.dumps(item["metadata"])
        
        html = [
            f'<div id="{item_id}" class="pm-gallery-item" data-metadata=\'{metadata_json}\'>',
            f'  <img src="{item["image_url"]}" alt="{item.get("title", "")}" class="pm-gallery-image">',
        ]
        
        if item.get("title"):
            html.append(f'  <div class="pm-gallery-title">{item["title"]}</div>')
        
        html.append('</div>')
        return "".join(html)


class MetadataPanel(BaseUIComponent):
    """Professional metadata viewing panel."""
    
    def __init__(self, component_id: str, collapsible: bool = True):
        """Initialize metadata panel.
        
        Args:
            component_id: Unique panel ID
            collapsible: Whether panel can be collapsed
        """
        super().__init__(component_id)
        self.collapsible = collapsible
        self.metadata: Dict[str, Any] = {}
        self.collapsed = False
        
        # Apply base classes
        self.add_class("pm-metadata-panel")
        
        if collapsible:
            self.add_class("pm-metadata-collapsible")
    
    def set_metadata(self, metadata: Dict[str, Any]) -> "MetadataPanel":
        """Set metadata to display.
        
        Args:
            metadata: Metadata dictionary
            
        Returns:
            Self for method chaining
        """
        self.metadata = metadata
        return self
    
    def render(self) -> str:
        """Render metadata panel HTML.
        
        Returns:
            HTML string
        """
        attrs = self._render_attributes()
        html = [f'<div {attrs}>']
        
        # Panel header
        html.append('<div class="pm-metadata-header">')
        html.append('<h3 class="pm-heading pm-heading-md">Metadata</h3>')
        
        if self.collapsible:
            html.append('<button class="pm-button pm-metadata-toggle" data-action="toggle">−</button>')
        
        html.append('</div>')
        
        # Panel content
        content_class = "pm-metadata-content"
        if self.collapsed:
            content_class += " pm-hidden"
        
        html.append(f'<div class="{content_class}">')
        
        if self.metadata:
            html.append(self._render_metadata_table())
        else:
            html.append('<p class="pm-text-secondary">No metadata available</p>')
        
        html.append('</div>')
        html.append('</div>')
        
        return "".join(html)
    
    def _render_metadata_table(self) -> str:
        """Render metadata as table.
        
        Returns:
            HTML table string
        """
        html = ['<table class="pm-metadata-table">']
        
        for key, value in self.metadata.items():
            # Format key for display
            display_key = key.replace("_", " ").title()
            
            # Format value for display
            if isinstance(value, (dict, list)):
                display_value = f'<code class="pm-text-mono">{json.dumps(value, indent=2)}</code>'
            elif isinstance(value, str) and len(value) > 100:
                display_value = f'{value[:100]}...'
            else:
                display_value = str(value)
            
            html.append(f'<tr>')
            html.append(f'  <td class="pm-metadata-key">{display_key}</td>')
            html.append(f'  <td class="pm-metadata-value">{display_value}</td>')
            html.append(f'</tr>')
        
        html.append('</table>')
        return "".join(html)


class SearchBox(BaseUIComponent):
    """Professional search input component."""
    
    def __init__(
        self, 
        component_id: str, 
        placeholder: str = "Search...",
        live_search: bool = True
    ):
        """Initialize search box.
        
        Args:
            component_id: Unique search box ID
            placeholder: Placeholder text
            live_search: Whether to trigger search as user types
        """
        super().__init__(component_id)
        self.placeholder = placeholder
        self.live_search = live_search
        
        # Apply base classes
        self.add_class("pm-search-box")
        
        # Set attributes
        self.set_attribute("type", "search")
        self.set_attribute("placeholder", placeholder)
        
        if live_search:
            self.set_data("live-search", "true")
    
    def render(self) -> str:
        """Render search box HTML.
        
        Returns:
            HTML string
        """
        attrs = self._render_attributes()
        return f'''
        <div class="pm-search-container">
            <input {attrs} class="pm-input pm-search-input">
            <button class="pm-button pm-search-button" type="button">
                <span class="pm-sr-only">Search</span>
                🔍
            </button>
        </div>
        '''


class Pagination(BaseUIComponent):
    """Professional pagination component."""
    
    def __init__(
        self,
        component_id: str,
        current_page: int = 1,
        total_pages: int = 1,
        show_info: bool = True
    ):
        """Initialize pagination.
        
        Args:
            component_id: Unique pagination ID
            current_page: Current page number
            total_pages: Total number of pages
            show_info: Whether to show page info text
        """
        super().__init__(component_id)
        self.current_page = current_page
        self.total_pages = total_pages
        self.show_info = show_info
        
        # Apply base classes
        self.add_class("pm-pagination")
        
        # Set data attributes
        self.set_data("current-page", current_page)
        self.set_data("total-pages", total_pages)
    
    def render(self) -> str:
        """Render pagination HTML.
        
        Returns:
            HTML string
        """
        if self.total_pages <= 1:
            return ""
        
        attrs = self._render_attributes()
        html = [f'<div {attrs}>']
        
        # Previous button
        prev_disabled = self.current_page <= 1
        prev_class = "pm-button pm-pagination-prev"
        if prev_disabled:
            prev_class += " pm-button-disabled"
        
        html.append(f'<button class="{prev_class}" data-page="{self.current_page - 1}" {"disabled" if prev_disabled else ""}>‹ Previous</button>')
        
        # Page numbers
        html.append('<div class="pm-pagination-pages">')
        
        # Calculate page range to display
        start_page = max(1, self.current_page - 2)
        end_page = min(self.total_pages, self.current_page + 2)
        
        # First page + ellipsis if needed
        if start_page > 1:
            html.append('<button class="pm-button pm-pagination-page" data-page="1">1</button>')
            if start_page > 2:
                html.append('<span class="pm-pagination-ellipsis">...</span>')
        
        # Page range
        for page in range(start_page, end_page + 1):
            page_class = "pm-button pm-pagination-page"
            if page == self.current_page:
                page_class += " pm-button-primary"
            
            html.append(f'<button class="{page_class}" data-page="{page}">{page}</button>')
        
        # Last page + ellipsis if needed
        if end_page < self.total_pages:
            if end_page < self.total_pages - 1:
                html.append('<span class="pm-pagination-ellipsis">...</span>')
            html.append(f'<button class="pm-button pm-pagination-page" data-page="{self.total_pages}">{self.total_pages}</button>')
        
        html.append('</div>')
        
        # Next button
        next_disabled = self.current_page >= self.total_pages
        next_class = "pm-button pm-pagination-next"
        if next_disabled:
            next_class += " pm-button-disabled"
        
        html.append(f'<button class="{next_class}" data-page="{self.current_page + 1}" {"disabled" if next_disabled else ""}>Next ›</button>')
        
        # Page info
        if self.show_info:
            html.append(f'<div class="pm-pagination-info pm-text-secondary">Page {self.current_page} of {self.total_pages}</div>')
        
        html.append('</div>')
        return "".join(html)