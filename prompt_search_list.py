"""
PromptSearchList: A ComfyUI node that searches prompts and outputs results as a list.

This node enables batch processing workflows by outputting prompt texts with
OUTPUT_IS_LIST=True, allowing direct connection to nodes that accept list inputs.
"""

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
                        "tooltip": "Comma-separated list of tags to filter by",
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
            },
        }

    RETURN_TYPES = (IO.STRING,)
    RETURN_NAMES = ("prompts",)
    OUTPUT_IS_LIST = (True,)
    OUTPUT_TOOLTIPS = ("List of prompt texts matching the search criteria.",)
    FUNCTION = "search"
    CATEGORY = "🫶 ComfyAssets/🧠 Prompts"
    DESCRIPTION = (
        "Searches the prompt database and outputs matching prompts as a list. "
        "Use this node to retrieve stored prompts for batch processing workflows. "
        "Connect to nodes that accept list inputs like String OutputList or batch processors."
    )

    def search(
        self,
        text: str = "",
        category: str = "",
        tags: str = "",
        min_rating: int = 0,
        limit: int = 50,
    ) -> Tuple[List[str]]:
        """
        Search for prompts matching the given criteria.

        Args:
            text: Search text to match against prompt content
            category: Filter by specific category
            tags: Comma-separated list of tags to filter by
            min_rating: Minimum rating (0-5) to include
            limit: Maximum number of results to return

        Returns:
            Tuple containing a list of prompt text strings
        """
        try:
            # Parse tags from comma-separated string
            tags_list = None
            if tags and tags.strip():
                tags_list = [tag.strip() for tag in tags.split(",") if tag.strip()]

            # Perform search
            results = self.db.search_prompts(
                text=text.strip() if text and text.strip() else None,
                category=category.strip() if category and category.strip() else None,
                tags=tags_list,
                rating_min=min_rating if min_rating > 0 else None,
                limit=limit,
            )

            # Extract just the prompt text from results
            prompt_texts = [r["text"] for r in results if r.get("text")]

            self.logger.debug(
                f"Search returned {len(prompt_texts)} prompts "
                f"(text='{text[:20]}...' if text else '', category='{category}', "
                f"tags={tags_list}, min_rating={min_rating}, limit={limit})"
            )

            # Return empty list if no results (not an error)
            if not prompt_texts:
                self.logger.info("Search returned no results")
                return ([],)

            return (prompt_texts,)

        except Exception as e:
            self.logger.error(f"Search error: {e}", exc_info=True)
            # Return empty list on error to avoid breaking workflows
            return ([],)

    @classmethod
    def IS_CHANGED(cls, text="", category="", tags="", min_rating=0, limit=50):
        """
        Always re-execute to get fresh results from the database.

        The database contents may have changed since the last execution,
        so we always return a unique value to trigger re-execution.
        """
        import time

        return time.time()
