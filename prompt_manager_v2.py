"""
PromptManager V2 - Slim combined widget with both positive and negative prompts.
Removes metadata fields and buttons while keeping database tracking functionality.
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
            print("\n🚀 INITIALIZING PROMPT TRACKER (V2)")
        
        if DISABLE_TRACKING:
            logger.info("Tracking disabled for V2 widgets")
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


class PromptManagerV2(ComfyNodeABC):
    """Slim V2 PromptManager with combined positive and negative prompts.

    Compatible with CLIPTextEncode - outputs standard CONDITIONING format.
    """

    version = "2.0.0-slim"
    # Compatibility aliases for parsers that look for specific patterns
    __aliases__ = ["CLIPTextEncode", "CLIPTextEncodeSDXL", "CLIPTextEncodeV2"]
    
    def __init__(self):
        """Initialize the PromptManagerV2 node."""
        super().__init__()
        self.output_dir = get_comfyui_integration().get_output_directory()
        self.db = PromptDatabase()
        self.tracker = get_prompt_tracker(db_instance=self.db)
        logger.info(f"PromptManagerV2 v{self.version} initialized")
    
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
                "negative_text": (
                    IO.STRING,
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": "Negative prompt text to be encoded",
                    },
                ),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO"
            }
        }
    
    RETURN_TYPES = (IO.CONDITIONING, IO.CONDITIONING, IO.STRING, IO.STRING)
    RETURN_NAMES = ("positive", "negative", "positive_text", "negative_text")
    OUTPUT_TOOLTIPS = (
        "Positive conditioning for the diffusion model",
        "Negative conditioning for the diffusion model",
        "The positive prompt text that was encoded",
        "The negative prompt text that was encoded",
    )
    FUNCTION = "encode"
    CATEGORY = "🫶 ComfyAssets/🧠 Prompts"
    DESCRIPTION = "V2 Slim prompt manager with combined positive and negative prompts. Simplified interface with database tracking."
    
    def encode(
        self,
        clip,
        text: str,
        negative_text: str = "",
        unique_id: Optional[str] = None,
        prompt: Optional[Dict] = None,
        extra_pnginfo: Optional[Dict] = None
    ) -> Tuple[Any, Any, str, str]:
        """
        Encode positive and negative prompts and save to database with tracking.
        
        Args:
            clip: The CLIP model
            text: Positive prompt text
            negative_text: Negative prompt text
            unique_id: Unique execution ID from ComfyUI
            prompt: Full prompt data from ComfyUI
            extra_pnginfo: Workflow and metadata from ComfyUI
        
        Returns:
            Tuple of (positive_conditioning, negative_conditioning, positive_text, negative_text)
        """
        try:
            # Clean text
            positive_text = text.strip() if text else ""
            negative_text = negative_text.strip() if negative_text else ""
            
            # Generate hash for tracking
            prompt_hash = hashlib.sha256(
                f"{positive_text}|{negative_text}".encode()
            ).hexdigest()[:16]

            # Extract unique_id if it's None (critical for tracking!)
            if unique_id is None and prompt and isinstance(prompt, dict):
                # Find our own node in the prompt data
                for key, value in prompt.items():
                    if isinstance(value, dict) and value.get("class_type") == "PromptManagerV2":
                        unique_id = key
                        logger.info(f"PromptManagerV2: Extracted unique_id={unique_id} from prompt data")
                        if DEBUG:
                            print(f"📍 Extracted unique_id={unique_id} from prompt data")
                        break

            # Save prompt to database and get prompt_id
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
                            f"PromptManagerV2: Saved prompt to database with ID {prompt_id}"
                        )
                        if DEBUG:
                            print(f"💾 Saved prompt to database with ID {prompt_id}")
                except Exception as e:
                    logger.warning(f"Failed to save prompt to database: {e}")
                    if DEBUG:
                        print(f"⚠️ Failed to save prompt to database: {e}")
            
            # Register with tracker for image association
            if self.tracker and unique_id and positive_text and not DISABLE_TRACKING:
                node_id = f"PromptManagerV2_{unique_id}"

                # Log before registration
                logger.info(f"PromptManagerV2: Registering prompt with tracker - node_id={node_id}, unique_id={unique_id}")

                # More detailed registration matching the original
                result = self.tracker.register_prompt(
                    node_id=node_id,
                    unique_id=unique_id,
                    prompt=positive_text,
                    negative_prompt=negative_text,
                    workflow=extra_pnginfo.get("workflow") if extra_pnginfo else None,
                    extra_data={
                        "version": "v2",
                        "widget": "combined",
                        "prompt_hash": prompt_hash,
                        "prompt_id": prompt_id  # Add prompt_id to extra_data
                    }
                )

                if DEBUG:
                    print(f"🔗 V2 Registered prompt - node_id: {node_id}, unique_id: {unique_id}, prompt_id: {prompt_id}, result: {result}")
                    
                # Store the prompt_id in the tracking data metadata for later use
                if prompt_id and hasattr(self.tracker, '_active_prompts'):
                    if unique_id in self.tracker._active_prompts:
                        try:
                            self.tracker._active_prompts[unique_id].metadata['prompt_id'] = prompt_id
                            logger.info(f"PromptManagerV2: Stored prompt_id {prompt_id} in tracking metadata")
                            if DEBUG:
                                print(f"✅ Successfully stored prompt_id {prompt_id} in tracker metadata")
                        except Exception as e:
                            logger.warning(f"Failed to store prompt_id in tracking data: {e}")
                            if DEBUG:
                                print(f"❌ Failed to store prompt_id in tracking data: {e}")
            
            # Encode prompts with CLIP (matching original implementation)
            tokens_positive = clip.tokenize(positive_text)
            positive_conditioning = clip.encode_from_tokens_scheduled(tokens_positive)

            tokens_negative = clip.tokenize(negative_text) if negative_text else clip.tokenize("")
            negative_conditioning = clip.encode_from_tokens_scheduled(tokens_negative)

            # Return both conditionings and text (matching ComfyUI format)
            return (
                positive_conditioning,
                negative_conditioning,
                positive_text,
                negative_text
            )
            
        except Exception as e:
            logger.error(f"Error in PromptManagerV2.encode: {e}", exc_info=True)
            # Return empty conditionings on error (matching original format)
            empty_tokens = clip.tokenize("")
            empty_cond = clip.encode_from_tokens_scheduled(empty_tokens)
            return (
                empty_cond,
                empty_cond,
                "",
                ""
            )


# ComfyUI node registration
NODE_CLASS_MAPPINGS = {
    "PromptManagerV2": PromptManagerV2
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptManagerV2": "Prompt Manager V2"
}
