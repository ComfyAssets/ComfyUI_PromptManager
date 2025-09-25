"""Database package for PromptManager."""

# Import the main database operations class
from .operations import PromptDatabase

# Also expose the model if needed
from .models import PromptModel

# Also expose the migration functionality
from .migration import DatabaseMigrator

# Import gallery extensions
from .gallery_operations import extend_prompt_database_with_gallery

# Export what nodes need
__all__ = [
    'PromptDatabase',
    'PromptModel',
    'DatabaseMigrator',
    'extend_prompt_database_with_gallery'
]