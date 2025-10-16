"""
PromptManager V2 (text) - Text-only version without CLIP encoding.
Outputs raw text for downstream text processing nodes.
"""

import os
import sys
import hashlib
from typing import Dict, Optional, Tuple

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
    from .loggers import get_logger
except ImportError:
    from src.database import PromptDatabase
    from utils.comfyui_integration import get_comfyui_integration
    from utils.validation.hashing import generate_prompt_hash
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
            print("\n🚀 INITIALIZING PROMPT TRACKER (V2 Text)")

        if DISABLE_TRACKING:
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


class PromptManagerV2Text(ComfyNodeABC):
    """Text-only V2 PromptManager - no CLIP encoding, outputs raw text."""

    version = "2.0.0-text"

    def __init__(self):
        """Initialize the PromptManagerV2Text node."""
        super().__init__()
        self.output_dir = get_comfyui_integration().get_output_directory()
        self.db = PromptDatabase()
        self.tracker = get_prompt_tracker(db_instance=self.db)

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
                        "tooltip": "Positive prompt text",
                    },
                ),
            },
            "optional": {
                "negative_text": (
                    IO.STRING,
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": "Negative prompt text",
                    },
                ),
                "prepend_text": (
                    IO.STRING,
                    {"forceInput": True, "tooltip": "Text to prepend to the positive prompt (connection only)"}
                ),
                "append_text": (
                    IO.STRING,
                    {"forceInput": True, "tooltip": "Text to append to the positive prompt (connection only)"}
                ),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO"
            }
        }

    RETURN_TYPES = (IO.STRING, IO.STRING)
    RETURN_NAMES = ("positive_text", "negative_text")
    OUTPUT_TOOLTIPS = (
        "The positive prompt text",
        "The negative prompt text",
    )
    FUNCTION = "process"
    CATEGORY = "🫶 ComfyAssets/🧠 Prompts"
    DESCRIPTION = "V2 Text-only prompt manager. Outputs raw text without CLIP encoding."

    def process(
        self,
        text: str,
        negative_text: str = "",
        prepend_text: Optional[str] = None,
        append_text: Optional[str] = None,
        unique_id: Optional[str] = None,
        prompt: Optional[Dict] = None,
        extra_pnginfo: Optional[Dict] = None
    ) -> Tuple[str, str]:
        """
        Process positive and negative prompts and save to database with tracking.

        Args:
            text: Positive prompt text
            negative_text: Negative prompt text
            prepend_text: Text to prepend to positive prompt
            append_text: Text to append to positive prompt
            unique_id: Unique execution ID from ComfyUI
            prompt: Full prompt data from ComfyUI
            extra_pnginfo: Workflow and metadata from ComfyUI

        Returns:
            Tuple of (positive_text, negative_text)
        """
        try:
            # Clean text
            positive_text = text.strip() if text else ""
            negative_text = negative_text.strip() if negative_text else ""

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

            # Generate hash for tracking
            prompt_hash = hashlib.sha256(
                f"{positive_text}|{negative_text}".encode()
            ).hexdigest()[:16]

            # Extract unique_id if it's None
            if unique_id is None and prompt and isinstance(prompt, dict):
                for key, value in prompt.items():
                    if isinstance(value, dict) and value.get("class_type") == "PromptManagerV2Text":
                        unique_id = key
                        logger.info(f"PromptManagerV2Text: Extracted unique_id={unique_id} from prompt data")
                        if DEBUG:
                            print(f"📍 Extracted unique_id={unique_id} from prompt data")
                        break

            # Register with tracker
            execution_id = None
            if self.tracker and unique_id and positive_text and not DISABLE_TRACKING:
                node_id = f"PromptManagerV2Text_{unique_id}"

                logger.info(f"PromptManagerV2Text: Registering prompt with tracker - node_id={node_id}, unique_id={unique_id}")

                execution_id = self.tracker.register_prompt(
                    node_id=node_id,
                    unique_id=unique_id,
                    prompt=positive_text,
                    negative_prompt=negative_text,
                    workflow=extra_pnginfo.get("workflow") if extra_pnginfo else None,
                    extra_data={
                        "version": "v2-text",
                        "widget": "text-only",
                        "prompt_hash": prompt_hash,
                    }
                )

                if DEBUG:
                    print(f"🔗 V2 Text Registered prompt - node_id: {node_id}, unique_id: {unique_id}, execution_id: {execution_id}")

            # Save prompt to database
            prompt_id = None
            if positive_text and not DISABLE_TRACKING:
                try:
                    if hasattr(self, "db") and self.db:
                        prompt_hash_db = generate_prompt_hash(positive_text)
                        prompt_id = self.db.save_prompt(
                            text=positive_text,
                            negative_prompt=negative_text,
                            prompt_hash=prompt_hash_db,
                        )
                        logger.info(
                            f"PromptManagerV2Text: Saved prompt to database with ID {prompt_id}"
                        )
                        if DEBUG:
                            print(f"💾 Saved prompt to database with ID {prompt_id}")

                        # Link the prompt_id to the tracking data
                        if prompt_id and hasattr(self.tracker, '_active_prompts') and unique_id in self.tracker._active_prompts:
                            try:
                                self.tracker._active_prompts[unique_id].metadata['prompt_id'] = prompt_id
                                logger.info(f"PromptManagerV2Text: Linked prompt_id {prompt_id} to tracking data")
                                if DEBUG:
                                    print(f"✅ Successfully linked prompt_id {prompt_id} to tracker metadata")
                            except Exception as e:
                                logger.warning(f"Failed to link prompt_id to tracking data: {e}")
                                if DEBUG:
                                    print(f"❌ Failed to link prompt_id to tracking data: {e}")
                except Exception as e:
                    logger.warning(f"Failed to save prompt to database: {e}")
                    if DEBUG:
                        print(f"⚠️ Failed to save prompt to database: {e}")

            # Return text directly (no CLIP encoding)
            return (positive_text, negative_text)

        except Exception as e:
            logger.error(f"Error in PromptManagerV2Text.process: {e}", exc_info=True)
            return ("", "")


# ComfyUI node registration
NODE_CLASS_MAPPINGS = {
    "PromptManagerV2Text": PromptManagerV2Text
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptManagerV2Text": "Prompt Manager V2 (text)"
}
