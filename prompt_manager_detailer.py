"""
PromptManagerDetailer - Specialized node for detailer workflows.

This node does NOT interfere with main prompt tracking. It's designed for
use with face detailers, upscalers, and other refinement nodes that should
not override the main generation's prompt tracking.

Key differences from PromptManagerV2:
- Does NOT register with PromptTracker (won't be selected for image linking)
- Does NOT save prompts to database (detailer prompts are ephemeral)
- Optionally accepts parent prompt_id for reference (not persisted)
- Separate class_type that won't interfere with tracking logic
- Returns prompt_id=0 (no database entry created)
"""

import os
import sys
from typing import Any, Dict, Optional, Tuple

# Make sure the custom node's root is on sys.path
_MODULE_ROOT = os.path.dirname(os.path.abspath(__file__))
_PARENT_ROOT = os.path.dirname(_MODULE_ROOT)

if _PARENT_ROOT not in sys.path:
    sys.path.insert(0, _PARENT_ROOT)
if _MODULE_ROOT not in sys.path:
    sys.path.insert(0, _MODULE_ROOT)

# Import ComfyUI types
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
        INT = "INT"

    InputTypeDict = dict

# Import utilities
try:
    from .loggers import get_logger
except ImportError:
    from loggers import get_logger

# Initialize logger
logger = get_logger(__name__)

# Check debug settings
DEBUG = os.getenv("PROMPTMANAGER_DEBUG", "0") == "1"
DISABLE_TRACKING = os.getenv("PROMPTMANAGER_DISABLE_TRACKING", "0") == "1"


class PromptManagerDetailer(ComfyNodeABC):
    """Detailer-specific PromptManager that doesn't interfere with main tracking.

    This node is designed for use in detailer workflows (face detailers,
    upscalers, refiners) where you don't want to override the main generation's
    prompt tracking. Images generated after this node will still be linked to
    the main PromptManager node, not this one.

    Features:
    - Prompt encoding without database persistence
    - Optional parent_prompt_id input for reference (not saved)
    - Does NOT register with PromptTracker (silent mode)
    - Does NOT save to database (ephemeral prompts only)
    - Returns prompt_id=0 (no database entry)
    """

    version = "2.0.0-detailer"

    def __init__(self):
        """Initialize the PromptManagerDetailer node."""
        super().__init__()

    @classmethod
    def INPUT_TYPES(cls) -> InputTypeDict:
        return {
            "required": {
                "text": (
                    IO.STRING,
                    {
                        "multiline": True,
                        "dynamicPrompts": True,
                        "default": "",
                        "tooltip": "Detailer positive prompt text to be encoded",
                    },
                ),
                "clip": (
                    IO.CLIP,
                    {"tooltip": "The CLIP model used for encoding the text"},
                ),
            },
            "optional": {
                "prepend_text": (
                    IO.STRING,
                    {"forceInput": True, "tooltip": "Text to prepend to the positive prompt (connection only)"}
                ),
                "append_text": (
                    IO.STRING,
                    {"forceInput": True, "tooltip": "Text to append to the positive prompt (connection only)"}
                ),
                "parent_prompt_id": (
                    "INT",
                    {
                        "forceInput": True,
                        "tooltip": "Optional: Link this detailer prompt to a parent prompt from main generation"
                    }
                ),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO"
            }
        }

    RETURN_TYPES = (IO.CONDITIONING, IO.STRING, "INT")
    RETURN_NAMES = ("positive", "positive_text", "prompt_id")
    OUTPUT_TOOLTIPS = (
        "Positive conditioning for the detailer model",
        "The positive prompt text that was encoded",
        "Database prompt ID for this detailer prompt",
    )
    FUNCTION = "encode"
    CATEGORY = "🫶 ComfyAssets/🧠 Prompts"
    DESCRIPTION = "Detailer-specific prompt manager (positive only) that doesn't interfere with main prompt tracking. Use this in face detailer, upscaler, and refiner workflows."

    def encode(
        self,
        clip,
        text: str,
        prepend_text: Optional[str] = None,
        append_text: Optional[str] = None,
        parent_prompt_id: Optional[int] = None,
        unique_id: Optional[str] = None,
        prompt: Optional[Dict] = None,
        extra_pnginfo: Optional[Dict] = None
    ) -> Tuple[Any, str, int]:
        """
        Encode detailer prompt (positive only) WITHOUT registering with PromptTracker.

        Args:
            clip: The CLIP model
            text: Detailer positive prompt text
            prepend_text: Text to prepend to positive prompt
            append_text: Text to append to positive prompt
            parent_prompt_id: Optional parent prompt ID to link to
            unique_id: Unique execution ID from ComfyUI
            prompt: Full prompt data from ComfyUI
            extra_pnginfo: Workflow and metadata from ComfyUI

        Returns:
            Tuple of (positive_conditioning, positive_text, prompt_id)
        """
        try:
            # Clean text
            positive_text = text.strip() if text else ""

            # Build positive prompt with prepend/append
            if prepend_text:
                prepend_text = prepend_text.strip()
                if prepend_text:
                    if positive_text and not prepend_text.endswith(','):
                        positive_text = f"{prepend_text}, {positive_text}"
                    else:
                        positive_text = f"{prepend_text}{positive_text}"

            if append_text:
                append_text = append_text.strip()
                if append_text:
                    if positive_text and not positive_text.endswith(','):
                        positive_text = f"{positive_text}, {append_text}"
                    else:
                        positive_text = f"{positive_text}{append_text}"

            # NOTE: We do NOT register with PromptTracker
            # This is intentional - detailer nodes should not override main tracking
            # We also do NOT save to database - detailer prompts are ephemeral
            if DEBUG:
                print(f"🔧 PromptManagerDetailer: Silent mode (NOT registering with tracker, NOT saving to DB)")
                if parent_prompt_id:
                    print(f"   └─ Parent prompt_id provided (for reference): {parent_prompt_id}")

            # Encode prompt with CLIP (positive only)
            tokens_positive = clip.tokenize(positive_text)
            positive_conditioning = clip.encode_from_tokens_scheduled(tokens_positive)

            # Return conditioning, text, and prompt_id (always 0 - no DB entry)
            return (
                positive_conditioning,
                positive_text,
                0  # Detailer nodes don't create database entries
            )

        except Exception as e:
            logger.error(f"Error in PromptManagerDetailer.encode: {e}", exc_info=True)
            # Return empty conditioning on error
            empty_tokens = clip.tokenize("")
            empty_cond = clip.encode_from_tokens_scheduled(empty_tokens)
            return (
                empty_cond,
                "",
                0
            )


# ComfyUI node registration
NODE_CLASS_MAPPINGS = {
    "PromptManagerDetailer": PromptManagerDetailer
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptManagerDetailer": "🔧 Prompt Manager (Detailer)"
}
