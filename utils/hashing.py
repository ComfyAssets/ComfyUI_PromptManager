"""Hashing utilities for PromptManager prompt deduplication.

This module provides cryptographic hashing functions to enable efficient
deduplication of prompts and content in the PromptManager system. It uses
SHA256 hashing with text normalization to ensure consistent hash generation
regardless of minor formatting differences.

Key features:
- SHA256-based prompt text hashing with normalization
- Content hashing for complex prompt metadata structures
- Duplicate detection utilities
- Consistent normalization (lowercase, trimmed whitespace)

Typical usage:
    from utils.hashing import generate_prompt_hash, is_duplicate_prompt

    hash1 = generate_prompt_hash("Beautiful landscape")
    hash2 = generate_prompt_hash(" beautiful landscape ")
    # hash1 == hash2 (normalization makes them identical)

    if is_duplicate_prompt(text1, text2):
        print("Duplicate prompts detected")

The hashing is designed for:
- Database deduplication (preventing duplicate prompt storage)
- Quick similarity checking
- Content integrity verification
- Efficient database indexing
"""

import hashlib


def generate_prompt_hash(text: str) -> str:
    """
    Generate a SHA256 hash for prompt text to enable deduplication.

    Normalizes the input text (strips whitespace, converts to lowercase)
    before hashing to ensure consistent results for functionally identical
    prompts with minor formatting differences.

    Args:
        text: The prompt text to hash

    Returns:
        SHA256 hexadecimal digest of the normalized text

    Raises:
        TypeError: If the input is not a string

    Example:
        >>> generate_prompt_hash("Beautiful Landscape")
        'c3d5f8a9b2e1...'
        >>> generate_prompt_hash(" beautiful landscape ")
        'c3d5f8a9b2e1...'
        # Both produce the same hash due to normalization
    """
    if not isinstance(text, str):
        raise TypeError("Text must be a string")

    # Normalize the text by stripping whitespace and converting to lowercase
    # for consistent hashing regardless of minor formatting differences
    normalized_text = text.strip().lower()

    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()


def is_duplicate_prompt(text1: str, text2: str, threshold: float = 0.95) -> bool:
    """
    Check if two prompts are likely duplicates using hash comparison.

    Compares the normalized hashes of two prompt texts to determine if
    they are functionally identical. This is an exact match after normalization,
    not a similarity measure.

    Args:
        text1: First prompt text to compare
        text2: Second prompt text to compare
        threshold: Similarity threshold (not used - kept for API compatibility)

    Returns:
        True if the prompts have identical normalized hashes (i.e., are duplicates),
        False otherwise

    Note:
        The threshold parameter is not used in the current implementation as
        this performs exact hash matching rather than similarity scoring.
        It's maintained for potential future fuzzy matching capabilities.
    """
    hash1 = generate_prompt_hash(text1)
    hash2 = generate_prompt_hash(text2)

    return hash1 == hash2
