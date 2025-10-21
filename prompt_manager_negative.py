"""
PromptManager Negative - Slim widget for negative prompts only.
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
    from .prompt_manager_shared import set_negative_prompt
    from .loggers import get_logger
except ImportError:
    from src.database import PromptDatabase
    from utils.comfyui_integration import get_comfyui_integration
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
            print("\n🚀 INITIALIZING PROMPT TRACKER (Negative)")
        
        if DISABLE_TRACKING:
            return None
        
        # Initialize thumbnail service for auto-generation
        thumbnail_service = None
        try:
            # Try relative imports first (when loaded as package)
            try:
                from .src.services.enhanced_thumbnail_service import EnhancedThumbnailService
                from .utils.cache import CacheManager
            except ImportError:
                # Fall back to absolute imports (when running as script)
                from src.services.enhanced_thumbnail_service import EnhancedThumbnailService
                from utils.cache import CacheManager

            cache_manager = CacheManager()
            if db_instance:
                thumbnail_service = EnhancedThumbnailService(db=db_instance, cache=cache_manager)
            else:
                try:
                    from .src.database import PromptDatabase
                except ImportError:
                    from src.database import PromptDatabase
                db = PromptDatabase()
                thumbnail_service = EnhancedThumbnailService(db=db, cache=cache_manager)

            if DEBUG:
                print("✅ Thumbnail service initialized for auto-generation")
        except Exception as e:
            logger.warning(f"Could not initialize thumbnail service: {e}")
            # Continue without auto-generation
        
        tracker = PromptTracker(thumbnail_service=thumbnail_service)
        
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


class PromptManagerNegative(ComfyNodeABC):
    """Slim V2 PromptManager for negative prompts only."""
    
    version = "2.0.0-negative"
    
    def __init__(self):
        """Initialize the PromptManagerNegative node."""
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
                        "tooltip": "Negative prompt text to be encoded",
                    },
                ),
                "clip": (
                    IO.CLIP,
                    {"tooltip": "The CLIP model used for encoding the text"},
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
        "Negative conditioning for the diffusion model",
        "The negative prompt text that was encoded",
    )
    FUNCTION = "encode"
    CATEGORY = "🫶 ComfyAssets/🧠 Prompts"
    DESCRIPTION = "V2 Slim prompt manager for negative prompts only. Minimal interface with database tracking."
    
    def encode(
        self,
        clip,
        text: str,
        unique_id: Optional[str] = None,
        prompt: Optional[Dict] = None,
        extra_pnginfo: Optional[Dict] = None
    ) -> Tuple[Any, str]:
        """
        Encode negative prompt and save to database with tracking.

        Args:
            clip: The CLIP model
            text: Negative prompt text
            unique_id: Unique execution ID from ComfyUI
            prompt: Full prompt data from ComfyUI
            extra_pnginfo: Workflow and metadata from ComfyUI

        Returns:
            Tuple of (negative_conditioning, negative_text)
        """
        try:
            # Clean text
            negative_text = text.strip() if text else ""
            
            # Generate hash for tracking (used for logging/diagnostics)
            prompt_hash = hashlib.sha256(f"negative:{negative_text}".encode()).hexdigest()[:16]

            # Extract unique_id if it's None (critical for tracking!)
            if unique_id is None and prompt and isinstance(prompt, dict):
                # Find our own node in the prompt data
                for key, value in prompt.items():
                    if isinstance(value, dict) and value.get("class_type") == "PromptManagerNegative":
                        unique_id = key
                        logger.info(f"PromptManagerNegative: Extracted unique_id={unique_id} from prompt data")
                        if DEBUG:
                            print(f"📍 Extracted unique_id={unique_id} from prompt data")
                        break

            # Cache the negative prompt so the positive widget can attach it to the stored record
            set_negative_prompt(unique_id, negative_text)

            # Register with tracker for image association
            if self.tracker and unique_id and negative_text and not DISABLE_TRACKING:
                node_id = f"PromptManagerNegative_{unique_id}"

                # Log before registration
                logger.info(f"PromptManagerNegative: Registering prompt with tracker - node_id={node_id}, unique_id={unique_id}")

                # More detailed registration matching the original
                result = self.tracker.register_prompt(
                    node_id=node_id,
                    unique_id=unique_id,
                    prompt="",
                    negative_prompt=negative_text,
                    workflow=extra_pnginfo.get("workflow") if extra_pnginfo else None,
                    extra_data={
                        "version": "v2",
                        "widget": "negative",
                        "prompt_hash": prompt_hash,
                        "prompt_id": None,
                        "negative_prompt": negative_text,
                    }
                )

                if DEBUG:
                    print(f"🔗 Negative Registered prompt - node_id: {node_id}, unique_id: {unique_id}, result: {result}")
                    # Check if registration worked
                    if hasattr(self.tracker, '_active_prompts'):
                        if unique_id in self.tracker._active_prompts:
                            print(f"✅ Successfully registered in tracker")
                        else:
                            print(f"❌ Failed to register in tracker")
            
            # Encode prompt with CLIP (matching original implementation)
            tokens = clip.tokenize(negative_text) if negative_text else clip.tokenize("")
            conditioning = clip.encode_from_tokens_scheduled(tokens)

            # Return conditioning and text (matching ComfyUI format)
            return (conditioning, negative_text)
            
        except Exception as e:
            logger.error(f"Error in PromptManagerNegative.encode: {e}", exc_info=True)
            # Return empty conditioning on error (matching original format)
            empty_tokens = clip.tokenize("")
            empty_cond = clip.encode_from_tokens_scheduled(empty_tokens)
            return (empty_cond, "")


# ComfyUI node registration
NODE_CLASS_MAPPINGS = {
    "PromptManagerNegative": PromptManagerNegative
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptManagerNegative": "Prompt Manager (Negative)"
}
