"""
Hashing utilities for KikoTextEncode prompt deduplication.
"""

import hashlib


def generate_prompt_hash(text: str) -> str:
    """
    Generate a SHA256 hash for prompt text to enable deduplication.
    
    Args:
        text: The prompt text to hash
        
    Returns:
        str: SHA256 hexdigest of the text
        
    Example:
        >>> generate_prompt_hash("beautiful landscape")
        'a1b2c3d4e5f6...'
    """
    if not isinstance(text, str):
        raise TypeError("Text must be a string")
    
    # Normalize the text by stripping whitespace and converting to lowercase
    # for consistent hashing regardless of minor formatting differences
    normalized_text = text.strip().lower()
    
    return hashlib.sha256(normalized_text.encode('utf-8')).hexdigest()


def generate_content_hash(content: dict) -> str:
    """
    Generate a hash for prompt content including metadata.
    
    Args:
        content: Dictionary containing prompt data
        
    Returns:
        str: SHA256 hexdigest of the content
    """
    import json
    
    # Create a normalized representation of the content
    normalized = {
        'text': content.get('text', '').strip().lower(),
        'category': content.get('category', '').strip().lower() if content.get('category') else '',
        'tags': sorted([tag.strip().lower() for tag in content.get('tags', []) if tag.strip()]),
        'workflow_name': content.get('workflow_name', '').strip().lower() if content.get('workflow_name') else ''
    }
    
    # Convert to JSON string for consistent hashing
    content_str = json.dumps(normalized, sort_keys=True)
    
    return hashlib.sha256(content_str.encode('utf-8')).hexdigest()


def is_duplicate_prompt(text1: str, text2: str, threshold: float = 0.95) -> bool:
    """
    Check if two prompts are likely duplicates using hash comparison.
    
    Args:
        text1: First prompt text
        text2: Second prompt text
        threshold: Similarity threshold (not used for exact hash matching)
        
    Returns:
        bool: True if prompts are likely duplicates
    """
    hash1 = generate_prompt_hash(text1)
    hash2 = generate_prompt_hash(text2)
    
    return hash1 == hash2