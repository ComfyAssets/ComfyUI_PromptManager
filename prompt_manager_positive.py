"""
PromptManager Positive - Slim widget for positive prompts only.
Minimal interface with database tracking functionality.
"""

import os
import sys
import hashlib
import time
from typing import Any, Dict, Optional, Tuple

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
        CLIP = "CLIP"
        CONDITIONING = "CONDITIONING"
    
    InputTypeDict = dict

# Import database and utilities
try:
    from .src.database import PromptDatabase
    from .utils.comfyui_integration import get_comfyui_integration
    from .utils.validation.hashing import generate_prompt_hash
    from .prompt_manager_shared import get_negative_prompt, pop_negative_prompt
    from .loggers import get_logger
except ImportError:
    from src.database import PromptDatabase
    from utils.comfyui_integration import get_comfyui_integration
    from utils.validation.hashing import generate_prompt_hash
    from prompt_manager_shared import get_negative_prompt, pop_negative_prompt
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
            print("\n🚀 INITIALIZING PROMPT TRACKER (Positive)")
        
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


class PromptManagerPositive(ComfyNodeABC):
    """Slim V2 PromptManager for positive prompts only."""
    
    version = "2.0.0-positive"
    
    def __init__(self):
        """Initialize the PromptManagerPositive node."""
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
                        "tooltip": "Positive prompt text to be encoded",
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
                    # No widget dict = connection only
                    {"forceInput": True, "tooltip": "Text to prepend to the prompt (connection only)"}
                ),
                "append_text": (
                    IO.STRING,
                    # No widget dict = connection only
                    {"forceInput": True, "tooltip": "Text to append to the prompt (connection only)"}
                ),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO"
            }
        }
    
    RETURN_TYPES = (IO.CONDITIONING, IO.STRING)
    RETURN_NAMES = ("CONDITIONING", "STRING")
    OUTPUT_TOOLTIPS = (
        "Positive conditioning for the diffusion model",
        "The positive prompt text that was encoded",
    )
    FUNCTION = "encode"
    CATEGORY = "🫶 ComfyAssets/🧠 Prompts"
    DESCRIPTION = "V2 Slim prompt manager for positive prompts only. Minimal interface with database tracking."
    
    def encode(
        self,
        clip,
        text: str,
        prepend_text: Optional[str] = None,
        append_text: Optional[str] = None,
        unique_id: Optional[str] = None,
        prompt: Optional[Dict] = None,
        extra_pnginfo: Optional[Dict] = None
    ) -> Tuple[Any, str]:
        """
        Encode positive prompt and save to database with tracking.

        Args:
            clip: The CLIP model
            text: Positive prompt text
            prepend_text: Text to prepend to prompt
            append_text: Text to append to prompt
            unique_id: Unique execution ID from ComfyUI
            prompt: Full prompt data from ComfyUI
            extra_pnginfo: Workflow and metadata from ComfyUI

        Returns:
            Tuple of (positive_conditioning, positive_text)
        """
        try:
            # Clean text
            positive_text = text.strip() if text else ""

            # Build prompt with prepend/append
            if prepend_text:
                prepend_text = prepend_text.strip()
                if prepend_text:
                    # Add comma separator if needed
                    if positive_text and not prepend_text.endswith(','):
                        positive_text = f"{prepend_text}, {positive_text}"
                    else:
                        positive_text = f"{prepend_text}{positive_text}"

            if append_text:
                append_text = append_text.strip()
                if append_text:
                    # Add comma separator if needed
                    if positive_text and not positive_text.endswith(','):
                        positive_text = f"{positive_text}, {append_text}"
                    else:
                        positive_text = f"{positive_text}{append_text}"
            
            # Generate hash for tracking
            prompt_hash = hashlib.sha256(positive_text.encode()).hexdigest()[:16]

            # Try to discover companion negative prompt
            negative_text = ""
            negative_key: Optional[str] = None

            if prompt and isinstance(prompt, dict):
                for key, value in prompt.items():
                    if not isinstance(value, dict):
                        continue
                    if value.get("class_type") != "PromptManagerNegative":
                        continue

                    cached = get_negative_prompt(key)
                    if cached:
                        negative_text = cached
                        negative_key = key
                        break

                    candidate = value.get("inputs", {}).get("text")
                    if isinstance(candidate, str) and candidate.strip():
                        negative_text = candidate.strip()
                        negative_key = key
                        break

            # Extract unique_id if it's None (critical for tracking!)
            if unique_id is None and prompt and isinstance(prompt, dict):
                # Find our own node in the prompt data
                for key, value in prompt.items():
                    if isinstance(value, dict) and value.get("class_type") == "PromptManagerPositive":
                        unique_id = key
                        logger.info(f"PromptManagerPositive: Extracted unique_id={unique_id} from prompt data")
                        if DEBUG:
                            print(f"📍 Extracted unique_id={unique_id} from prompt data")
                        break

            # Register with tracker FIRST (before database save)
            # This ensures tracking works even if database save fails
            if self.tracker and unique_id and positive_text and not DISABLE_TRACKING:
                node_id = f"PromptManagerPositive_{unique_id}"

                # Log before registration
                logger.info(f"PromptManagerPositive: Registering prompt with tracker - node_id={node_id}, unique_id={unique_id}")

                # More detailed registration matching the original
                result = self.tracker.register_prompt(
                    node_id=node_id,
                    unique_id=unique_id,
                    prompt=positive_text,
                    negative_prompt=negative_text,
                    workflow=extra_pnginfo.get("workflow") if extra_pnginfo else None,
                    extra_data={
                        "version": "v2",
                        "widget": "positive",
                        "prompt_hash": prompt_hash,
                        "negative_prompt": negative_text,
                    }
                )

                if negative_key:
                    pop_negative_prompt(negative_key)

                if DEBUG:
                    print(f"🔗 Positive Registered prompt EARLY - node_id: {node_id}, unique_id: {unique_id}, result: {result}")

            # Save to database and capture prompt_id for tracking
            prompt_id = None
            if positive_text and not DISABLE_TRACKING:
                try:
                    if hasattr(self, "db") and self.db:
                        prompt_hash_db = generate_prompt_hash(positive_text)
                        prompt_id = self.db.save_prompt(
                            text=positive_text,
                            negative_prompt=negative_text or None,
                            prompt_hash=prompt_hash_db,
                        )
                        logger.info(
                            f"PromptManagerPositive: Saved prompt to database with ID {prompt_id}"
                        )
                        if DEBUG:
                            print(f"💾 Saved prompt to database with ID {prompt_id}")

                        # Link the prompt_id to the tracking data
                        if prompt_id and hasattr(self.tracker, '_active_prompts') and unique_id in self.tracker._active_prompts:
                            try:
                                self.tracker._active_prompts[unique_id].metadata['prompt_id'] = prompt_id
                                logger.info(f"PromptManagerPositive: Linked prompt_id {prompt_id} to tracking data")
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
            
            # Encode prompt with CLIP (matching original implementation)
            tokens = clip.tokenize(positive_text)
            conditioning = clip.encode_from_tokens_scheduled(tokens)

            # Return conditioning and text (matching ComfyUI format)
            return (conditioning, positive_text)
            
        except Exception as e:
            logger.error(f"Error in PromptManagerPositive.encode: {e}", exc_info=True)
            # Return empty conditioning on error (matching original format)
            empty_tokens = clip.tokenize("")
            empty_cond = clip.encode_from_tokens_scheduled(empty_tokens)
            return (empty_cond, "")


# ComfyUI node registration
NODE_CLASS_MAPPINGS = {
    "PromptManagerPositive": PromptManagerPositive
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptManagerPositive": "Prompt Manager (Positive)"
}
