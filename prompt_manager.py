"""
PromptManager: Main custom node implementation that extends CLIPTextEncode
with persistent prompt storage and search capabilities.
"""

import time
from typing import Any, Tuple

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


class PromptManager(PromptManagerBase, ComfyNodeABC):
    """
    A ComfyUI custom node that functions like CLIPTextEncode but adds:
    - Persistent storage of all prompts in SQLite database
    - Search and retrieval capabilities
    - Metadata management (categories, tags, ratings, notes)
    - Duplicate detection via SHA256 hashing
    """

    def __init__(self):
        super().__init__(logger_name="prompt_manager.node")

    @classmethod
    def INPUT_TYPES(cls) -> InputTypeDict:
        return {
            "required": {
                "text": (
                    IO.STRING,
                    {
                        "multiline": True,
                        "dynamicPrompts": True,
                        "tooltip": "The text prompt to be encoded and saved to database.",
                    },
                ),
                "clip": (
                    IO.CLIP,
                    {"tooltip": "The CLIP model used for encoding the text."},
                ),
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
                        "tooltip": "Text to prepend to the main prompt (connected STRING nodes will be added before the main text)"
                    },
                ),
                "append_text": (
                    IO.STRING,
                    {
                        "tooltip": "Text to append to the main prompt (connected STRING nodes will be added after the main text)"
                    },
                ),
            },
        }

    RETURN_TYPES = (IO.CONDITIONING, IO.STRING)
    OUTPUT_TOOLTIPS = (
        "A conditioning containing the embedded text used to guide the diffusion model.",
        "The final combined text string (with prepend/append applied) that was encoded.",
    )
    FUNCTION = "encode_prompt"
    CATEGORY = "🫶 ComfyAssets/🧠 Prompts"
    DESCRIPTION = (
        "Encodes a text prompt using a CLIP model into an embedding that can be used to guide "
        "the diffusion model towards generating specific images. Additionally saves all prompts "
        "to a local SQLite database with optional metadata for search and retrieval."
    )

    def encode_prompt(
        self,
        clip,
        text: str,
        category: str = "",
        tags: str = "",
        search_text: str = "",
        prepend_text: str = "",
        append_text: str = "",
    ) -> Tuple[Any]:
        """
        Encode the text prompt and save it to the database.

        Args:
            clip: The CLIP model for encoding
            text: The text prompt to encode
            category: Optional category for organization
            tags: Comma-separated tags
            search_text: Text to search for in past prompts
            prepend_text: Text to prepend to the main prompt
            append_text: Text to append to the main prompt

        Returns:
            Tuple containing the conditioning for the diffusion model and the final text string

        Raises:
            RuntimeError: If clip input is invalid
        """
        # Combine prepend, main text, and append text
        final_text = ""
        if prepend_text and prepend_text.strip():
            final_text += prepend_text.strip() + " "
        final_text += text if text else ""
        if append_text and append_text.strip():
            final_text += " " + append_text.strip()

        # Use the combined text for encoding
        encoding_text = final_text

        # For database storage, save the original main text with metadata about prepend/append
        storage_text = text

        # Validate CLIP model
        if clip is None:
            error_msg = (
                "ERROR: clip input is invalid: None\n\n"
                "If the clip is from a checkpoint loader node your checkpoint does not "
                "contain a valid clip or text encoder model."
            )
            self.logger.error("CLIP validation failed: clip input is None")
            raise RuntimeError(error_msg)

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
                        prompt_text=encoding_text.strip(),
                        additional_data={
                            "category": category.strip() if category else None,
                            "tags": extended_tags,
                            "prompt_id": prompt_id,
                            "prepend_text": (
                                prepend_text.strip() if prepend_text else None
                            ),
                            "append_text": append_text.strip() if append_text else None,
                            "final_text": encoding_text.strip(),
                        },
                    )
                    self.logger.debug(
                        f"Set execution context: {execution_id} for prompt ID: {prompt_id}"
                    )

            except Exception as e:
                # Log error but don't fail the encoding
                self.logger.warning(f"Failed to save prompt to database: {e}")

        # Perform standard CLIP text encoding using the combined text
        self.logger.debug(
            f"Performing CLIP text encoding on combined text: {encoding_text[:100]}..."
        )
        tokens = clip.tokenize(encoding_text)
        conditioning = clip.encode_from_tokens_scheduled(tokens)

        # Register with ComfyUI integration for standard metadata compatibility
        node_id = f"promptmanager_{int(time.time() * 1000)}"
        self.comfyui_integration.register_prompt(
            node_id,
            encoding_text.strip(),
            {
                "category": category.strip() if category else None,
                "tags": extended_tags,
                "prompt_id": prompt_id,
                "prepend_text": prepend_text.strip() if prepend_text else None,
                "append_text": append_text.strip() if append_text else None,
            },
        )

        self.logger.info(f"CLIP encoding completed, text: {repr(encoding_text)[:80]}")
        return (conditioning, encoding_text)

    @classmethod
    def IS_CHANGED(cls, clip, text="", category="", tags="", search_text="",
                   prepend_text="", append_text="", **kwargs):
        """
        ComfyUI method to determine if node needs re-execution.

        Returns a hash of input values that affect the conditioning output.
        This enables proper branch execution - only re-execute when inputs change.
        """
        import hashlib

        combined = f"{text}|{prepend_text}|{append_text}"
        return hashlib.sha256(combined.encode()).hexdigest()
