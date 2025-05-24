"""
Input validation utilities for KikoTextEncode.
"""

import re
from typing import List, Optional, Union


def validate_prompt_text(text: str) -> bool:
    """
    Validate prompt text input.
    
    Args:
        text: The prompt text to validate
        
    Returns:
        bool: True if valid, False otherwise
        
    Raises:
        ValueError: If text is invalid with descriptive message
    """
    if not isinstance(text, str):
        raise ValueError("Prompt text must be a string")
    
    if not text or not text.strip():
        raise ValueError("Prompt text cannot be empty")
    
    if len(text.strip()) > 10000:  # Reasonable limit for prompt length
        raise ValueError("Prompt text is too long (maximum 10,000 characters)")
    
    return True


def validate_rating(rating: Optional[int]) -> bool:
    """
    Validate rating input.
    
    Args:
        rating: The rating to validate (1-5 or None)
        
    Returns:
        bool: True if valid, False otherwise
        
    Raises:
        ValueError: If rating is invalid
    """
    if rating is None:
        return True
    
    if not isinstance(rating, int):
        raise ValueError("Rating must be an integer")
    
    if rating < 1 or rating > 5:
        raise ValueError("Rating must be between 1 and 5")
    
    return True


def validate_tags(tags: Union[str, List[str], None]) -> bool:
    """
    Validate tags input.
    
    Args:
        tags: Tags as string, list, or None
        
    Returns:
        bool: True if valid, False otherwise
        
    Raises:
        ValueError: If tags are invalid
    """
    if tags is None:
        return True
    
    if isinstance(tags, str):
        # Parse comma-separated tags
        tag_list = [tag.strip() for tag in tags.split(',') if tag.strip()]
        tags = tag_list
    
    if not isinstance(tags, list):
        raise ValueError("Tags must be a string, list, or None")
    
    for tag in tags:
        if not isinstance(tag, str):
            raise ValueError("All tags must be strings")
        
        if not tag.strip():
            raise ValueError("Tags cannot be empty")
        
        if len(tag.strip()) > 50:
            raise ValueError("Individual tags cannot exceed 50 characters")
        
        # Check for invalid characters (optional - you can adjust this)
        if not re.match(r'^[a-zA-Z0-9\s\-_]+$', tag.strip()):
            raise ValueError(f"Tag '{tag}' contains invalid characters")
    
    if len(tags) > 20:  # Reasonable limit
        raise ValueError("Maximum 20 tags allowed")
    
    return True


def validate_category(category: Optional[str]) -> bool:
    """
    Validate category input.
    
    Args:
        category: The category to validate
        
    Returns:
        bool: True if valid, False otherwise
        
    Raises:
        ValueError: If category is invalid
    """
    if category is None:
        return True
    
    if not isinstance(category, str):
        raise ValueError("Category must be a string")
    
    category = category.strip()
    if not category:
        return True  # Empty category is valid (same as None)
    
    if len(category) > 100:
        raise ValueError("Category cannot exceed 100 characters")
    
    # Check for invalid characters (adjust as needed)
    if not re.match(r'^[a-zA-Z0-9\s\-_]+$', category):
        raise ValueError("Category contains invalid characters")
    
    return True


def validate_workflow_name(workflow_name: Optional[str]) -> bool:
    """
    Validate workflow name input.
    
    Args:
        workflow_name: The workflow name to validate
        
    Returns:
        bool: True if valid, False otherwise
        
    Raises:
        ValueError: If workflow name is invalid
    """
    if workflow_name is None:
        return True
    
    if not isinstance(workflow_name, str):
        raise ValueError("Workflow name must be a string")
    
    workflow_name = workflow_name.strip()
    if not workflow_name:
        return True  # Empty workflow name is valid
    
    if len(workflow_name) > 200:
        raise ValueError("Workflow name cannot exceed 200 characters")
    
    return True


def sanitize_input(text: str) -> str:
    """
    Sanitize text input by removing potentially harmful content.
    
    Args:
        text: The text to sanitize
        
    Returns:
        str: Sanitized text
    """
    if not isinstance(text, str):
        return ""
    
    # Remove null bytes and other control characters
    sanitized = text.replace('\x00', '').replace('\r\n', '\n').replace('\r', '\n')
    
    # Strip excessive whitespace but preserve single newlines
    lines = sanitized.split('\n')
    sanitized_lines = [line.strip() for line in lines]
    
    # Remove excessive empty lines (keep max 2 consecutive)
    result_lines = []
    empty_count = 0
    
    for line in sanitized_lines:
        if not line:
            empty_count += 1
            if empty_count <= 2:
                result_lines.append(line)
        else:
            empty_count = 0
            result_lines.append(line)
    
    return '\n'.join(result_lines).strip()


def parse_tags_string(tags_string: str) -> List[str]:
    """
    Parse a comma-separated tags string into a clean list.
    
    Args:
        tags_string: Comma-separated tags string
        
    Returns:
        List[str]: Cleaned list of unique tags
    """
    if not tags_string or not isinstance(tags_string, str):
        return []
    
    # Split by comma and clean each tag
    tags = []
    for tag in tags_string.split(','):
        clean_tag = sanitize_input(tag).strip()
        if clean_tag and clean_tag not in tags:  # Avoid duplicates
            tags.append(clean_tag)
    
    return tags[:20]  # Limit to 20 tags