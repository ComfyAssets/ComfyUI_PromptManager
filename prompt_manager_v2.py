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
                "prepend_text": (
                    IO.STRING,
                    # No widget dict = connection only
                    {"forceInput": True, "tooltip": "Text to prepend to the positive prompt (connection only)"}
                ),
                "append_text": (
                    IO.STRING,
                    # No widget dict = connection only
                    {"forceInput": True, "tooltip": "Text to append to the positive prompt (connection only)"}
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
        prepend_text: Optional[str] = None,
        append_text: Optional[str] = None,
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
            prepend_text: Text to prepend to positive prompt
            append_text: Text to append to positive prompt
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

            # Build positive prompt with prepend/append
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

            # Register with tracker FIRST (before database save)
            # This ensures tracking works even if database save fails
            execution_id = None
            if self.tracker and unique_id and positive_text and not DISABLE_TRACKING:
                node_id = f"PromptManagerV2_{unique_id}"
                
                logger.info(f"PromptManagerV2: Registering prompt with tracker - node_id={node_id}, unique_id={unique_id}")
                
                execution_id = self.tracker.register_prompt(
                    node_id=node_id,
                    unique_id=unique_id,
                    prompt=positive_text,
                    negative_prompt=negative_text,
                    workflow=extra_pnginfo.get("workflow") if extra_pnginfo else None,
                    extra_data={
                        "version": "v2",
                        "widget": "combined",
                        "prompt_hash": prompt_hash,
                    }
                )
                
                if DEBUG:
                    print(f"🔗 V2 Registered prompt EARLY - node_id: {node_id}, unique_id: {unique_id}, execution_id: {execution_id}")
            
            # Register prompt to pending registry (conditional saving v3)
            # or fallback to immediate database save (v2 behavior)
            tracking_id = None
            prompt_id = None
            if positive_text and not DISABLE_TRACKING:
                try:
                    # Try to use pending registry from API (conditional saving)
                    pending_registry = None
                    try:
                        integration = get_comfyui_integration()
                        pending_registry = integration.get_pending_registry()
                    except Exception as e:
                        if DEBUG:
                            print(f"⚠️ PromptManagerV2: Failed to get pending registry: {e}")

                    if pending_registry:
                        # Register to pending registry (conditional saving v3)
                        if DEBUG:
                            print(f"   📝 Using pending registry (id={id(pending_registry)}, count={pending_registry.get_count()})")
                        tracking_id = pending_registry.register_prompt(
                            positive_prompt=positive_text,
                            negative_prompt=negative_text,
                            metadata={
                                "node_id": "PromptManagerV2",
                                "unique_id": unique_id,
                                "execution_id": execution_id,
                            }
                        )
                        logger.info(
                            f"PromptManagerV2: Registered prompt to pending registry with tracking_id {tracking_id}"
                        )
                        if DEBUG:
                            print(f"⏳ Pending: Registered with tracking_id {tracking_id} (registry now has {pending_registry.get_count()} prompts)")
                        
                        # Add tracking_id to tracking data so it can be used when image is saved
                        if self.tracker and unique_id and hasattr(self.tracker, '_active_prompts'):
                            if unique_id in self.tracker._active_prompts:
                                self.tracker._active_prompts[unique_id].metadata['tracking_id'] = tracking_id
                                if DEBUG:
                                    print(f"   ✅ Added tracking_id to tracking metadata")
                    elif hasattr(self, "db") and self.db:
                        # Fallback to immediate database save (v2 behavior)
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

                        # Link the prompt_id to the tracking data
                        if DEBUG:
                            has_active = hasattr(self.tracker, '_active_prompts')
                            in_active = unique_id in self.tracker._active_prompts if has_active else False
                            print(f"\n🔗 Attempting to link prompt_id {prompt_id}:")
                            print(f"   unique_id: {unique_id}")
                            print(f"   has _active_prompts: {has_active}")
                            print(f"   unique_id in _active_prompts: {in_active}")
                            if has_active:
                                print(f"   active_prompts keys: {list(self.tracker._active_prompts.keys())}")

                        if prompt_id and hasattr(self.tracker, '_active_prompts') and unique_id in self.tracker._active_prompts:
                            try:
                                self.tracker._active_prompts[unique_id].metadata['prompt_id'] = prompt_id
                                logger.info(f"PromptManagerV2: Linked prompt_id {prompt_id} to tracking data")
                                if DEBUG:
                                    print(f"✅ Successfully linked prompt_id {prompt_id} to tracker metadata")
                                    print(f"   Metadata now: {self.tracker._active_prompts[unique_id].metadata}")
                            except Exception as e:
                                logger.warning(f"Failed to link prompt_id to tracking data: {e}")
                                if DEBUG:
                                    print(f"❌ Failed to link prompt_id to tracking data: {e}")
                        elif prompt_id:
                            if DEBUG:
                                print(f"⚠️ Could not link prompt_id {prompt_id} - tracking data not found!")
                except Exception as e:
                    logger.warning(f"Failed to register prompt: {e}")
                    if DEBUG:
                        print(f"⚠️ Failed to register prompt: {e}")
            
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
