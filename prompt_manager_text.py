"""
PromptManagerText: A text-only version of PromptManager that outputs STRING
without CLIP encoding, while maintaining all database and search features.
"""

import time
from typing import Tuple

try:
    from comfy.comfy_types import IO, ComfyNodeABC, InputTypeDict
except ImportError:
    # Fallback for older ComfyUI versions
    class ComfyNodeABC:
        pass

    class IO:
        STRING = "STRING"
        CLIP = "CLIP"
        CONDITIONING = "CONDITIONING"

    InputTypeDict = dict

try:
    from .prompt_manager_base import PromptManagerBase
except ImportError:
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from prompt_manager_base import PromptManagerBase


class PromptManagerText(PromptManagerBase, ComfyNodeABC):
    """
    A ComfyUI custom node that provides all PromptManager features but outputs
    only a STRING without CLIP encoding. Includes:
    - Persistent storage of all prompts in SQLite database
    - Search and retrieval capabilities
    - Metadata management (categories, tags, ratings, notes)
    - Duplicate detection via SHA256 hashing
    - Text concatenation with prepend/append functionality
    """

    def __init__(self):
        super().__init__(logger_name="prompt_manager_text.node")

    @classmethod
    def INPUT_TYPES(cls) -> InputTypeDict:
        return {
            "required": {
                "text": (
                    IO.STRING,
                    {
                        "multiline": True,
                        "dynamicPrompts": True,
                        "tooltip": "The text prompt to be processed and saved to database.",
                    },
                )
            },
            "optional": {
                "category": (
                    IO.STRING,
                    {
                        "default": "",
                        "tooltip": "Optional category for organizing prompts (e.g., 'landscapes', 'portraits')",
                    },
                ),
                "tags": (
                    IO.STRING,
                    {
                        "default": "",
                        "tooltip": "Comma-separated tags for the prompt (e.g., 'anime, detailed, sunset')",
                    },
                ),
                "search_text": (
                    IO.STRING,
                    {
                        "default": "",
                        "tooltip": "Search for past prompts containing this text",
                    },
                ),
                "prepend_text": (
                    IO.STRING,
                    {
                        "default": "",
                        "tooltip": "Text to prepend to the main prompt (connected STRING nodes will be added before the main text)",
                    },
                ),
                "append_text": (
                    IO.STRING,
                    {
                        "default": "",
                        "tooltip": "Text to append to the main prompt (connected STRING nodes will be added after the main text)",
                    },
                ),
            },
        }

    RETURN_TYPES = (IO.STRING,)
    OUTPUT_TOOLTIPS = (
        "The final combined text string (with prepend/append applied) ready for use in other nodes.",
    )
    FUNCTION = "process_text"
    CATEGORY = "🫶 ComfyAssets/🧠 Prompts"
    DESCRIPTION = (
        "Processes and manages text prompts with database storage and search capabilities. "
        "Outputs a plain STRING that can be used with any node that accepts text input. "
        "Includes all PromptManager features: categorization, tagging, search, and prepend/append functionality."
    )

    def process_text(
        self,
        text: str,
        category: str = "",
        tags: str = "",
        search_text: str = "",
        prepend_text: str = "",
        append_text: str = "",
    ) -> Tuple[str]:
        """
        Process the text prompt and save it to the database.

        Args:
            text: The text prompt to process
            category: Optional category for organization
            tags: Comma-separated tags
            search_text: Text to search for in past prompts
            prepend_text: Text to prepend to the main prompt
            append_text: Text to append to the main prompt

        Returns:
            Tuple containing the final processed text string
        """
        # Combine prepend, main text, and append text
        final_text = ""
        if prepend_text and prepend_text.strip():
            final_text += prepend_text.strip() + " "
        final_text += text
        if append_text and append_text.strip():
            final_text += " " + append_text.strip()

        # For database storage, save the original main text with metadata about prepend/append
        storage_text = text

        # Save prompt to database and set execution context for gallery tracking
        prompt_id = None
        if storage_text and storage_text.strip():
            self.logger.debug(f"Processing prompt text: {storage_text[:100]}...")

            # Add prepend/append info to tags if they exist
            extended_tags = self._parse_tags(tags) or []
            if prepend_text and prepend_text.strip():
                extended_tags.append(f"prepend:{prepend_text.strip()[:50]}")
            if append_text and append_text.strip():
                extended_tags.append(f"append:{append_text.strip()[:50]}")

            try:
                prompt_id = self._save_prompt_to_database(
                    text=storage_text.strip(),
                    category=category.strip() if category else None,
                    tags=extended_tags if extended_tags else None,
                )

                # Set current prompt for image tracking
                if prompt_id:
                    execution_id = self.prompt_tracker.set_current_prompt(
                        prompt_text=final_text.strip(),
                        additional_data={
                            "category": category.strip() if category else None,
                            "tags": extended_tags,
                            "prompt_id": prompt_id,
                            "prepend_text": (
                                prepend_text.strip() if prepend_text else None
                            ),
                            "append_text": append_text.strip() if append_text else None,
                        },
                    )
                    self.logger.debug(
                        f"Set execution context: {execution_id} for prompt ID: {prompt_id}"
                    )

            except Exception as e:
                # Log error but don't fail the processing
                self.logger.warning(f"Failed to save prompt to database: {e}")

        # Register with ComfyUI integration for standard metadata compatibility
        node_id = f"promptmanagertext_{int(time.time() * 1000)}"
        self.comfyui_integration.register_prompt(
            node_id,
            final_text.strip(),
            {
                "category": category.strip() if category else None,
                "tags": extended_tags,
                "prompt_id": prompt_id,
                "prepend_text": prepend_text.strip() if prepend_text else None,
                "append_text": append_text.strip() if append_text else None,
            },
        )

        self.logger.debug(f"Text processing completed: {final_text[:100]}...")
        return (final_text,)

    @classmethod
    def IS_CHANGED(
        cls,
        text="",
        category="",
        tags="",
        search_text="",
        prepend_text="",
        append_text="",
        **kwargs,
    ):
        """
        ComfyUI method to determine if node needs re-execution.

        Returns a hash of input values that affect the text output.
        This enables proper branch execution - only re-execute when inputs change.
        """
        import hashlib

        combined = f"{text}|{prepend_text}|{append_text}"
        return hashlib.sha256(combined.encode()).hexdigest()
