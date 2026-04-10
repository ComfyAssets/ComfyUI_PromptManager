"""
PromptSearchList: A ComfyUI node that searches prompts and outputs results as a list.

This node enables batch processing workflows by outputting prompt texts with
OUTPUT_IS_LIST=True, allowing direct connection to nodes that accept list inputs.
"""

import re
from typing import Any, Dict, List, Tuple

try:
    from .utils.logging_config import get_logger
except ImportError:
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from utils.logging_config import get_logger

try:
    from comfy.comfy_types import IO, ComfyNodeABC, InputTypeDict
except ImportError:

    class ComfyNodeABC:
        pass

    class IO:
        STRING = "STRING"
        INT = "INT"

    InputTypeDict = dict

try:
    from .database.operations import PromptDatabase
except ImportError:
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from database.operations import PromptDatabase


class PromptSearchList(ComfyNodeABC):
    """
    A ComfyUI node that searches the prompt database and outputs matching
    prompts as a list for batch processing workflows.

    Features:
    - Search by text content
    - Filter by category, tags, and minimum rating
    - Outputs as OUTPUT_IS_LIST for batch node compatibility
    - Read-only operation (no database writes)
    """

    def __init__(self):
        self.logger = get_logger("prompt_search_list.node")
        self.logger.debug("Initializing PromptSearchList node")
        self.db = PromptDatabase()

    @classmethod
    def INPUT_TYPES(cls) -> InputTypeDict:
        return {
            "required": {},
            "optional": {
                "text": (
                    IO.STRING,
                    {
                        "default": "",
                        "tooltip": "Search text to match against prompt content",
                    },
                ),
                "category": (
                    IO.STRING,
                    {
                        "default": "",
                        "tooltip": "Filter by specific category",
                    },
                ),
                "tags": (
                    IO.STRING,
                    {
                        "default": "",
                        "tooltip": "Comma-separated list of tags to filter by (partial match)",
                    },
                ),
                "min_rating": (
                    IO.INT,
                    {
                        "default": 0,
                        "min": 0,
                        "max": 5,
                        "tooltip": "Minimum rating (0-5) to include in results",
                    },
                ),
                "limit": (
                    IO.INT,
                    {
                        "default": 50,
                        "min": 1,
                        "max": 1000,
                        "tooltip": "Maximum number of results to return",
                    },
                ),
                "skip_multipart": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "Skip prompts containing Clip_1/Clip_2/etc. multi-part markers",
                    },
                ),
            },
        }

    RETURN_TYPES = (IO.STRING, IO.STRING, IO.INT)
    RETURN_NAMES = ("prompts", "preview", "count")
    OUTPUT_IS_LIST = (True, False, False)
    OUTPUT_TOOLTIPS = (
        "List of prompt texts matching the search criteria.",
        "Preview of all found prompts (for display).",
        "Number of prompts found.",
    )
    FUNCTION = "search"
    OUTPUT_NODE = True
    CATEGORY = "🫶 ComfyAssets/🧠 Prompts"
    DESCRIPTION = (
        "Searches the prompt database and outputs matching prompts as a list. "
        "Use this node to retrieve stored prompts for batch processing workflows. "
        "'prompts' output sends each prompt individually to downstream nodes (batch). "
        "'preview' output shows all found prompts as one text block. "
        "'count' output shows how many prompts were found."
    )

    def search(
        self,
        text: str = "",
        category: str = "",
        tags: str = "",
        min_rating: int = 0,
        limit: int = 50,
        skip_multipart: bool = True,
    ):
        """
        Search for prompts matching the given criteria.

        Args:
            text: Search text to match against prompt content
            category: Filter by specific category
            tags: Comma-separated list of tags to filter by
            min_rating: Minimum rating (0-5) to include
            limit: Maximum number of results to return

        Returns:
            Dict with 'ui' display info and 'result' tuple
        """
        try:
            # Parse tags from comma-separated string
            tags_list = None
            if tags and tags.strip():
                tags_list = [tag.strip() for tag in tags.split(",") if tag.strip()]

            # Perform search with partial tag matching for discovery
            results = self.db.search_prompts(
                text=text.strip() if text and text.strip() else None,
                category=category.strip() if category and category.strip() else None,
                tags=tags_list,
                tag_partial=True,
                rating_min=min_rating if min_rating > 0 else None,
                limit=limit,
            )

            # Extract prompt text, collapsing newlines to spaces so downstream
            # nodes that split on \n (e.g. StringOutputList) treat each DB
            # entry as a single prompt.
            prompt_texts = [
                " ".join(r["text"].split()) for r in results if r.get("text")
            ]

            # Filter out multi-part prompts (Clip_1/Clip_2 markers)
            if skip_multipart:
                prompt_texts = [
                    p for p in prompt_texts if not re.search(r"Clip_\d+", p)
                ]

            # Filter out prompts that are only LoRA tags with no actual content
            LORA_ONLY = re.compile(r"^(\s*<lora:[^>]+>\s*)+$")
            prompt_texts = [p for p in prompt_texts if not LORA_ONLY.match(p)]

            count = len(prompt_texts)

            # Build a preview: numbered list of truncated prompts
            preview_lines = []
            for i, p in enumerate(prompt_texts, 1):
                truncated = p[:120] + "..." if len(p) > 120 else p
                preview_lines.append(f"[{i}] {truncated}")
            preview = "\n".join(preview_lines) if preview_lines else "No results found"

            # Must return at least one element — ComfyUI's slice_dict
            # indexes into OUTPUT_IS_LIST outputs and crashes on empty lists.
            if not prompt_texts:
                self.logger.info("Search returned no results")
                return {
                    "ui": {"text": ["No results found"]},
                    "result": ([""], preview, count),
                }

            return {
                "ui": {"text": [f"Found {count} prompts"]},
                "result": (prompt_texts, preview, count),
            }

        except Exception as e:
            self.logger.error(f"Search error: {e}", exc_info=True)
            return {
                "ui": {"text": [f"Search error: {e}"]},
                "result": ([""], f"Error: {e}", 0),
            }

    @classmethod
    def IS_CHANGED(
        cls, text="", category="", tags="", min_rating=0, limit=50, skip_multipart=True
    ):
        """
        Always re-execute to get fresh results from the database.

        The database contents may have changed since the last execution,
        so we always return a unique value to trigger re-execution.
        """
        import time

        return time.time()
