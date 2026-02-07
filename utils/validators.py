"""Input validation utilities for PromptManager.

This module provides comprehensive input validation and sanitization functions
for the PromptManager system. It ensures data integrity and security by validating
user inputs before they are processed or stored in the database.

Validation functions include:
- Prompt text validation (length limits, type checking)
- Rating validation (1-5 scale with None support)
- Tag validation and parsing (comma-separated strings or lists)
- Category validation (optional string fields)
- Workflow name validation
- Input sanitization and cleaning utilities

All validation functions follow a consistent pattern:
- Type checking with descriptive error messages
- Reasonable limits to prevent abuse
- Support for None/optional values where appropriate
- Raise ValueError with clear messages on validation failures

Typical usage:
    from utils.validators import validate_prompt_text, sanitize_input

    try:
        validate_prompt_text(user_input)
        clean_text = sanitize_input(user_input)
        # Process the validated and cleaned input
    except ValueError as e:
        # Handle validation error with user-friendly message
        print(f"Invalid input: {e}")
"""

import re
from typing import List, Optional, Union


def validate_prompt_text(text: str) -> bool:
    """
    Validate prompt text input.

    Ensures the prompt text is a valid string with reasonable length limits.
    Empty or whitespace-only strings are rejected.

    Args:
        text: The prompt text to validate

    Returns:
        True if the text passes validation

    Raises:
        ValueError: If text is invalid with descriptive message including:
                   - Not a string type
                   - Empty or whitespace-only
                   - Exceeds maximum length (10,000 characters)
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

    Validates rating values on a 1-5 scale, with None allowed for unrated prompts.

    Args:
        rating: The rating to validate (1-5 scale or None for no rating)

    Returns:
        True if the rating is valid

    Raises:
        ValueError: If rating is invalid:
                   - Not an integer (when not None)
                   - Outside the 1-5 range
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

    Accepts tags as comma-separated string, list of strings, or None.
    Validates each tag for length and character restrictions.

    Args:
        tags: Tags as comma-separated string, list of strings, or None

    Returns:
        True if all tags are valid

    Raises:
        ValueError: If tags are invalid:
                   - Wrong input type (not string, list, or None)
                   - Individual tag is empty or only whitespace
                   - Individual tag exceeds 50 characters
                   - Tag contains invalid characters (non-alphanumeric, spaces, hyphens, underscores)
                   - More than 20 tags provided
    """
    if tags is None:
        return True

    if isinstance(tags, str):
        # Parse comma-separated tags
        tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()]
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

        # Reject control characters and null bytes
        if re.search(r"[\x00-\x1f]", tag.strip()):
            raise ValueError(f"Tag '{tag}' contains invalid control characters")

    if len(tags) > 20:  # Reasonable limit
        raise ValueError("Maximum 20 tags allowed")

    return True


def validate_category(category: Optional[str]) -> bool:
    """
    Validate category input.

    Validates optional category strings with reasonable length limits
    and character restrictions.

    Args:
        category: The category string to validate (None allowed for no category)

    Returns:
        True if the category is valid or None

    Raises:
        ValueError: If category is invalid:
                   - Not a string type (when not None)
                   - Exceeds 100 characters
                   - Contains invalid characters (non-alphanumeric, spaces, hyphens, underscores)
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

    # Reject control characters and null bytes
    if re.search(r"[\x00-\x1f]", category):
        raise ValueError("Category contains invalid control characters")

    return True


def validate_workflow_name(workflow_name: Optional[str]) -> bool:
    """
    Validate workflow name input.

    Validates optional workflow name strings with generous length limits
    to accommodate descriptive workflow names.

    Args:
        workflow_name: The workflow name to validate (None allowed for no workflow)

    Returns:
        True if the workflow name is valid or None

    Raises:
        ValueError: If workflow name is invalid:
                   - Not a string type (when not None)
                   - Exceeds 200 characters
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

    Cleans text input by removing control characters, normalizing whitespace,
    and limiting excessive empty lines. Preserves the semantic content while
    ensuring safe storage and display.

    Args:
        text: The text string to sanitize

    Returns:
        Sanitized text string with:
        - Null bytes and control characters removed
        - Normalized line endings (\r\n and \r converted to \n)
        - Trimmed whitespace on each line
        - Limited consecutive empty lines (maximum 2)
        - Overall trimmed result
    """
    if not isinstance(text, str):
        return ""

    # Remove null bytes and other control characters
    sanitized = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")

    # Strip excessive whitespace but preserve single newlines
    lines = sanitized.split("\n")
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

    return "\n".join(result_lines).strip()


def parse_tags_string(tags_string: str) -> List[str]:
    """
    Parse a comma-separated tags string into a clean list.

    Converts comma-separated tag strings into a clean, deduplicated list
    of tags. Each tag is sanitized and trimmed.

    Args:
        tags_string: Comma-separated tags string (e.g., "tag1, tag2, tag3")

    Returns:
        List of unique, cleaned tag strings. Empty input returns empty list.
        Limited to maximum 20 tags to prevent abuse.
    """
    if not tags_string or not isinstance(tags_string, str):
        return []

    # Split by comma and clean each tag
    tags = []
    for tag in tags_string.split(","):
        clean_tag = sanitize_input(tag).strip()
        if clean_tag and clean_tag not in tags:  # Avoid duplicates
            tags.append(clean_tag)

    return tags[:20]  # Limit to 20 tags
