"""Shared state helpers for PromptManager widgets."""

from __future__ import annotations

from typing import Dict, Optional

# Cache negative prompts keyed by their node unique_id
_NEGATIVE_PROMPT_CACHE: Dict[str, str] = {}


def set_negative_prompt(unique_id: Optional[str], text: str) -> None:
    """Store negative prompt text for later retrieval."""
    if not unique_id:
        return
    _NEGATIVE_PROMPT_CACHE[str(unique_id)] = text


def pop_negative_prompt(unique_id: Optional[str]) -> Optional[str]:
    """Remove and return cached negative prompt."""
    if not unique_id:
        return None
    return _NEGATIVE_PROMPT_CACHE.pop(str(unique_id), None)


def get_negative_prompt(unique_id: Optional[str]) -> Optional[str]:
    """Retrieve cached negative prompt without removing it."""
    if not unique_id:
        return None
    return _NEGATIVE_PROMPT_CACHE.get(str(unique_id))


def iter_cached_negative_prompts():
    """Yield cached (unique_id, text) pairs for inspection."""
    return list(_NEGATIVE_PROMPT_CACHE.items())
