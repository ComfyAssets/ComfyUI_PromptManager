"""
PromptManager Negative (text) - Text-only version for negative prompts.
Outputs raw text without CLIP encoding.
"""

import os
import sys
import hashlib
from typing import Dict, Optional

# Make sure the custom node's root is on sys.path
_MODULE_ROOT = os.path.dirname(os.path.abspath(__file__))
_PARENT_ROOT = os.path.dirname(_MODULE_ROOT)

if _PARENT_ROOT not in sys.path:
    sys.path.insert(0, _PARENT_ROOT)
if _MODULE_ROOT not in sys.path:
    sys.path.insert(0, _MODULE_ROOT)

# Import tracking system
try:
    from .src.tracking import PromptTracker, SaveImagePatcher
    from .src.tracking.singleton_tracker import SingletonTracker
except ImportError:
    from src.tracking import PromptTracker, SaveImagePatcher
    from src.tracking.singleton_tracker import SingletonTracker

# Import ComfyUI types
try:
    from comfy.comfy_types import IO, ComfyNodeABC, InputTypeDict
except ImportError:
    # Fallback for older ComfyUI versions
    class ComfyNodeABC:
        pass

    class IO:
        STRING = "STRING"

    InputTypeDict = dict

# Import database and utilities
try:
    from .src.database import PromptDatabase
    from .utils.comfyui_integration import get_comfyui_integration
    from .utils.validation.hashing import generate_prompt_hash
    from .prompt_manager_shared import set_negative_prompt
    from .loggers import get_logger
except ImportError:
    from src.database import PromptDatabase
    from utils.comfyui_integration import get_comfyui_integration
    from utils.validation.hashing import generate_prompt_hash
    from prompt_manager_shared import set_negative_prompt
    from loggers import get_logger

# Initialize logger
logger = get_logger(__name__)

# Check debug settings
DEBUG = os.getenv("PROMPTMANAGER_DEBUG", "0") == "1"
DISABLE_TRACKING = os.getenv("PROMPTMANAGER_DISABLE_TRACKING", "0") == "1"


def get_prompt_tracker(db_instance=None):
    """Get or create the global prompt tracker instance."""
    tracker = SingletonTracker.get_tracker()

    if tracker is None:
        if DEBUG:
            print("\n🚀 INITIALIZING PROMPT TRACKER (Negative Text)")

        if DISABLE_TRACKING:
            logger.info("Tracking disabled for Negative text widget")
            return None

        tracker = PromptTracker()

        # Patch SaveImage if needed
        if not os.getenv("PROMPTMANAGER_DISABLE_PATCH", "0") == "1":
            patcher = SaveImagePatcher(tracker)
            SingletonTracker.set_patcher(patcher)

            if DEBUG:
                print("🔧 Attempting to patch SaveImage node...")
            if not patcher.patch():
                logger.warning("Failed to patch SaveImage, falling back to file watcher")
            elif DEBUG:
                print("✅ SaveImage patched successfully!")

        SingletonTracker.set_tracker(tracker)

    if db_instance and tracker:
        tracker.db_instance = db_instance

    return tracker


class PromptManagerNegativeText(ComfyNodeABC):
    """Text-only negative prompt manager - outputs raw text."""

    version = "2.0.0-negative-text"

    def __init__(self):
        """Initialize the PromptManagerNegativeText node."""
        super().__init__()
        self.output_dir = get_comfyui_integration().get_output_directory()
        self.db = PromptDatabase()
        self.tracker = get_prompt_tracker(db_instance=self.db)
        logger.info(f"PromptManagerNegativeText v{self.version} initialized")

    @classmethod
    def INPUT_TYPES(cls) -> InputTypeDict:
        return {
            "required": {
                "text": (
                    IO.STRING,
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": "Negative prompt text",
                    },
                ),
            },
            "optional": {
                "prepend_text": (
                    IO.STRING,
                    {"forceInput": True, "tooltip": "Text to prepend (connection only)"}
                ),
                "append_text": (
                    IO.STRING,
                    {"forceInput": True, "tooltip": "Text to append (connection only)"}
                ),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO"
            }
        }

    RETURN_TYPES = (IO.STRING,)
    RETURN_NAMES = ("text",)
    OUTPUT_TOOLTIPS = ("The negative prompt text",)
    FUNCTION = "process"
    CATEGORY = "🫶 ComfyAssets/🧠 Prompts"
    DESCRIPTION = "Negative-only text prompt manager. Outputs raw text without CLIP encoding."

    def process(
        self,
        text: str,
        prepend_text: Optional[str] = None,
        append_text: Optional[str] = None,
        unique_id: Optional[str] = None,
        prompt: Optional[Dict] = None,
        extra_pnginfo: Optional[Dict] = None
    ) -> tuple:
        """
        Process negative prompt and save to database with tracking.

        Args:
            text: Negative prompt text
            prepend_text: Text to prepend
            append_text: Text to append
            unique_id: Unique execution ID from ComfyUI
            prompt: Full prompt data from ComfyUI
            extra_pnginfo: Workflow and metadata from ComfyUI

        Returns:
            Tuple containing (text,)
        """
        try:
            # Clean text
            negative_text = text.strip() if text else ""

            # Build negative prompt with prepend/append
            if prepend_text:
                prepend_text = prepend_text.strip()
                if prepend_text:
                    if negative_text and not prepend_text.endswith(','):
                        negative_text = f"{prepend_text}, {negative_text}"
                    else:
                        negative_text = f"{prepend_text}{negative_text}"

            if append_text:
                append_text = append_text.strip()
                if append_text:
                    if negative_text and not negative_text.endswith(','):
                        negative_text = f"{negative_text}, {append_text}"
                    else:
                        negative_text = f"{negative_text}{append_text}"

            # Extract unique_id if it's None
            if unique_id is None and prompt and isinstance(prompt, dict):
                for key, value in prompt.items():
                    if isinstance(value, dict) and value.get("class_type") == "PromptManagerNegativeText":
                        unique_id = key
                        logger.info(f"PromptManagerNegativeText: Extracted unique_id={unique_id}")
                        if DEBUG:
                            print(f"📍 Extracted unique_id={unique_id}")
                        break

            # Store negative prompt for pairing with positive node
            if unique_id:
                set_negative_prompt(unique_id, negative_text)
                if DEBUG:
                    print(f"📝 Stored negative prompt for unique_id={unique_id}")

            # Return text directly
            return (negative_text,)

        except Exception as e:
            logger.error(f"Error in PromptManagerNegativeText.process: {e}", exc_info=True)
            return ("",)


# ComfyUI node registration
NODE_CLASS_MAPPINGS = {
    "PromptManagerNegativeText": PromptManagerNegativeText
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptManagerNegativeText": "Prompt Manager (Negative/text)"
}
