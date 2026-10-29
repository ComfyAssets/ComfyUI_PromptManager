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

    return hashlib.sha256(prompt_key(text).encode("utf-8")).hexdigest()


def stored_prompt_text(text: str) -> str:
    """The text the Library stores for raw node text: outer whitespace removed.

    Every prompt writer stores ``text.strip()`` and the hash above strips too,
    so this is the form recorded raw run text is compared in (#438). Case and
    inner whitespace stay significant.
    """
    return text.strip()


# The code points str.strip() removes (every str.isspace() character), for
# SQLite's char(); tests/test_hashing.py pins it to the running Python.
STRIPPED_CODE_POINTS = (
    *range(0x09, 0x0E),
    *range(0x1C, 0x21),
    0x85,
    0xA0,
    0x1680,
    *range(0x2000, 0x200B),
    0x2028,
    0x2029,
    0x202F,
    0x205F,
    0x3000,
)
_STRIP_CODES = ",".join(map(str, STRIPPED_CODE_POINTS))


def prompt_key(text: str) -> str:
    """The dedup key: the stored text with case folded by ``str.lower()``.

    Two texts with one key are one prompt (the hash is sha256 of the key), so
    a run belongs to the history of the prompt with its key (#460).
    """
    return stored_prompt_text(text).lower()


def _sql_key(value):
    return prompt_key(value) if isinstance(value, str) else None


def _sql_hash(value):
    return generate_prompt_hash(value) if isinstance(value, str) else None


def register_prompt_functions(conn) -> None:
    """Give ``conn`` ``pm_prompt_key(x)`` and ``pm_prompt_hash(x)`` (#460).

    SQLite's lower() folds ASCII only; these call Python, so SQL gets exactly
    ``prompt_key`` / ``generate_prompt_hash`` (NULL for a non-text value).
    Never used in schema objects, so the file stays readable without them.
    """
    conn.create_function("pm_prompt_key", 1, _sql_key, deterministic=True)
    conn.create_function("pm_prompt_hash", 1, _sql_hash, deterministic=True)


def stored_prompt_text_sql(expression: str) -> str:
    """``stored_prompt_text`` as an SQLite expression over ``expression``.

    SQLite's one-argument trim() removes spaces only; trim(X, Y) removes every
    character of Y, multi-byte ones included, so it strips what Python does.
    """
    return f"trim({expression}, char({_STRIP_CODES}))"


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
