"""
PromptManager with integrated tracking: Enhanced version with hidden inputs
for robust prompt-to-image tracking through ComfyUI's execution pipeline.

This file shows the modifications needed to integrate our tracking system.
"""

import os
import sys

# Make sure the custom node's root is on sys.path
_MODULE_ROOT = os.path.dirname(os.path.abspath(__file__))
_PARENT_ROOT = os.path.dirname(_MODULE_ROOT)

if _PARENT_ROOT not in sys.path:
    sys.path.insert(0, _PARENT_ROOT)
if _MODULE_ROOT not in sys.path:
    sys.path.insert(0, _MODULE_ROOT)

import datetime
import hashlib
import json
import time
import webbrowser
from typing import Any, Dict, List, Optional, Tuple

# Import our tracking system
try:
    from .src.tracking import PromptTracker, SaveImagePatcher
    from .src.tracking.singleton_tracker import SingletonTracker
except ImportError:
    try:
        from src.tracking import PromptTracker, SaveImagePatcher
        from src.tracking.singleton_tracker import SingletonTracker
    except ImportError:
        import sys
        import os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from src.tracking import PromptTracker, SaveImagePatcher
        from src.tracking.singleton_tracker import SingletonTracker

# Import hashing function once at module level to avoid sys.path modifications during inference
try:
    from utils.validation.hashing import generate_prompt_hash
except ImportError:
    # Fallback: add current directory to path and try again
    import sys
    import os
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)
    from utils.validation.hashing import generate_prompt_hash

# Import logging system
try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger
except ImportError:  # pragma: no cover
    from loggers import get_logger

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
    from .src.database import PromptDatabase
    from .utils.comfyui_integration import get_comfyui_integration
except ImportError:
    # For direct imports when not in a package
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from src.database import PromptDatabase
    from utils.comfyui_integration import get_comfyui_integration

# Initialize logger
logger = get_logger(__name__)

# Initialize global tracker (singleton pattern)
# Use sys.modules as persistent storage to prevent re-initialization on reload
import sys
if not hasattr(sys.modules[__name__], '_prompt_tracker'):
    sys.modules[__name__]._prompt_tracker = None
    sys.modules[__name__]._save_patcher = None
    sys.modules[__name__]._tracking_initialized = False

_prompt_tracker = sys.modules[__name__]._prompt_tracker
_save_patcher = sys.modules[__name__]._save_patcher
# Derive DEBUG from UI settings if available (UI overrides env)
DEBUG = os.getenv("PROMPTMANAGER_DEBUG", "0") == "1"
try:
    from utils.settings import load_settings as _pm_load_settings
    _cfg = _pm_load_settings()
    if isinstance(_cfg, dict) and 'promptDebugTracking' in _cfg:
        DEBUG = bool(_cfg.get('promptDebugTracking'))
except Exception:
    pass
DISABLE_TRACKING = os.getenv("PROMPTMANAGER_DISABLE_TRACKING", "0") == "1"

class _NoOpTracker:
    """No-op tracker used when tracking is disabled for diagnostics."""
    def __init__(self):
        self.db_instance = None
        self.metrics = {
            "total_tracked": 0,
            "successful_pairs": 0,
            "failed_pairs": 0,
            "multi_node_workflows": 0,
            "avg_confidence": 0.0,
        }

    # API parity with PromptTracker
    def register_prompt(self, *_, **__):
        return "noop"

    def register_connection(self, *_, **__):
        return None

    def get_prompt_for_save(self, *_, **__):
        return None

    def record_image_saved(self, *_, **__):
        return None

    def cleanup_old_tracking(self, *_, **__):
        return 0

    def get_metrics(self):
        return {**self.metrics, "accuracy_rate": 0.0, "active_prompts": 0, "graph_nodes": 0}

    def reset_metrics(self):
        self.metrics = {k: 0 for k in self.metrics}
        self.metrics["avg_confidence"] = 0.0

    async def start_cleanup_task(self):
        return None

def get_prompt_tracker(db_instance=None):
    """Get or create the global prompt tracker instance.

    Args:
        db_instance: Optional PromptDatabase instance to use
    """
    # Use singleton storage that persists across module reloads
    tracker = SingletonTracker.get_tracker()
    
    if tracker is None:
        if DEBUG:
            print("\n🚀 INITIALIZING PROMPT TRACKER AND SAVEIMAGE PATCHER")
        if DISABLE_TRACKING:
            logger.info("PROMPTMANAGER_DISABLE_TRACKING=1 set; all PromptManager tracking disabled")
            tracker = _NoOpTracker()
        else:
            tracker = PromptTracker()

            # Allow disabling patch via env for diagnostics
            disable_patch = os.getenv("PROMPTMANAGER_DISABLE_PATCH", "0") == "1"
            if disable_patch:
                logger.info("PROMPTMANAGER_DISABLE_PATCH=1 set; skipping SaveImage patch")
            else:
                patcher = SaveImagePatcher(tracker)
                SingletonTracker.set_patcher(patcher)

                # Try to patch SaveImage node
                if DEBUG:
                    print("🔧 Attempting to patch SaveImage node...")
                if not patcher.patch():
                    if DEBUG:
                        print("❌ Failed to patch SaveImage, falling back to file watcher")
                    logger.warning("Failed to patch SaveImage, falling back to file watcher")
                else:
                    if DEBUG:
                        print("✅ SaveImage patched successfully!")
        
        # Store the tracker in singleton storage
        SingletonTracker.set_tracker(tracker)
        # Also update module-local storage for backward compatibility
        sys.modules[__name__]._prompt_tracker = tracker
        if hasattr(sys.modules[__name__], '_save_patcher'):
            sys.modules[__name__]._save_patcher = SingletonTracker.get_patcher()

    # Store the database instance for later use - ALWAYS update if provided
    if db_instance:
        tracker.db_instance = db_instance
        if DEBUG:
            print(f"📚 Database instance stored in tracker")

    return tracker


class PromptManager(ComfyNodeABC):
    """Enhanced PromptManager with integrated tracking for robust prompt-to-image association."""
    
    version = "2.0.0"  # Version with tracking
    
    def __init__(self):
        """Initialize the PromptManager node."""
        super().__init__()
        self.output_dir = get_comfyui_integration().get_output_directory()
        self.db = PromptDatabase()
        self.tracker = get_prompt_tracker(db_instance=self.db)  # Pass the database instance
        logger.info(f"PromptManager v{self.version} initialized with tracking")
    
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
            # CRITICAL ADDITION: Hidden inputs for tracking
            "hidden": {
                "unique_id": "UNIQUE_ID",      # ComfyUI provides this automatically
                "prompt": "PROMPT",            # Full prompt data from ComfyUI
                "extra_pnginfo": "EXTRA_PNGINFO"  # Workflow and metadata
            }
        }
    
    RETURN_TYPES = (IO.CONDITIONING, IO.STRING)
    OUTPUT_TOOLTIPS = (
        "A conditioning containing the embedded text used to guide the diffusion model.",
        "The final combined text string (with prepend/append applied) that was encoded.",
    )
    FUNCTION = "encode"
    CATEGORY = "🫶 ComfyAssets/🧠 Prompts"
    DESCRIPTION = (
        "Encodes a text prompt using a CLIP model into an embedding that can be used to guide "
        "the diffusion model towards generating specific images. Additionally saves all prompts "
        "to a local SQLite database with optional metadata for search and retrieval. "
        "Version 2.0 includes robust prompt-to-image tracking."
    )
    
    def encode(
        self,
        clip,
        text: str,
        category: str = "",
        tags: str = "",
        search_text: str = "",
        prepend_text: str = "",
        append_text: str = "",
        # Hidden inputs for tracking
        unique_id: Optional[str] = None,
        prompt: Optional[Dict] = None,
        extra_pnginfo: Optional[Dict] = None
    ) -> Tuple[Any, str]:
        """
        Encode the text and save to database with tracking.

        Args:
            clip: The CLIP model
            text: Main prompt text
            category: Optional category
            tags: Comma-separated tags
            search_text: Text to search in database
            prepend_text: Text to add before main prompt
            append_text: Text to add after main prompt
            unique_id: Unique execution ID from ComfyUI
            prompt: Full prompt data from ComfyUI
            extra_pnginfo: Workflow and metadata from ComfyUI

        Returns:
            Tuple of (conditioning, final_text)
        """
        try:
            # Debug logging for tracking data
            if DEBUG:
                print("\n" + "="*80)
                print("🔍 PROMPTMANAGER DEBUG - TRACKING DATA")
                print("="*80)
                print(f"📍 unique_id: {unique_id}")
                print(f"📍 unique_id type: {type(unique_id)}")

                if prompt:
                    print(f"📊 prompt keys: {prompt.keys() if isinstance(prompt, dict) else 'not a dict'}")
                    if isinstance(prompt, dict):
                        # Show first level of prompt data
                        for key, value in prompt.items():
                            if key in ['client_id', 'unique_id']:
                                print(f"   - {key}: {value}")
                            elif isinstance(value, dict):
                                print(f"   - {key}: dict with {len(value)} keys")
                            elif isinstance(value, list):
                                print(f"   - {key}: list with {len(value)} items")
                            else:
                                print(f"   - {key}: {type(value).__name__}")
                else:
                    print("📊 prompt: None")

                if extra_pnginfo:
                    print(f"📦 extra_pnginfo keys: {extra_pnginfo.keys() if isinstance(extra_pnginfo, dict) else 'not a dict'}")
                    if isinstance(extra_pnginfo, dict) and 'workflow' in extra_pnginfo:
                        workflow = extra_pnginfo['workflow']
                        if isinstance(workflow, dict) and 'nodes' in workflow:
                            print(f"   - workflow has {len(workflow.get('nodes', []))} nodes")
                            # Find PromptManager and SaveImage nodes
                            pm_nodes = [n for n in workflow['nodes'] if n.get('type') == 'PromptManager']
                            si_nodes = [n for n in workflow['nodes'] if n.get('type') == 'SaveImage']
                            print(f"   - PromptManager nodes: {len(pm_nodes)}")
                            print(f"   - SaveImage nodes: {len(si_nodes)}")
                            if pm_nodes:
                                print(f"   - PromptManager node IDs: {[n.get('id') for n in pm_nodes]}")
                            if si_nodes:
                                print(f"   - SaveImage node IDs: {[n.get('id') for n in si_nodes]}")
                else:
                    print("📦 extra_pnginfo: None")

                print("="*80 + "\n")

            # Combine text with prepend/append
            combined_text = self._combine_text(prepend_text, text, append_text)

            # Clean and normalize the text
            final_text = self._clean_text(combined_text)

            # Get negative prompt if in workflow
            negative_prompt = ""
            if extra_pnginfo and "workflow" in extra_pnginfo:
                # Try to extract negative prompt from workflow
                # This would need to be customized based on workflow structure
                pass

            # Log unique_id extraction
            logger.info(f"PromptManager: About to check tracking - unique_id={unique_id}, type={type(unique_id)}, DISABLE_TRACKING={DISABLE_TRACKING}")

            # If unique_id is None, try to extract from prompt data
            if unique_id is None:
                logger.warning("PromptManager: unique_id is None! Attempting to extract from prompt data...")
                if prompt and isinstance(prompt, dict):
                    # Find our own node in the prompt data
                    for key, value in prompt.items():
                        if isinstance(value, dict) and value.get("class_type") == "PromptManager":
                            unique_id = key
                            logger.info(f"PromptManager: Extracted unique_id={unique_id} from prompt data")
                            break

            # Register with tracker for robust tracking
            if unique_id and not DISABLE_TRACKING:
                # Extract node ID from prompt data if available
                node_id = self._get_node_id(prompt, extra_pnginfo)
                # Use the unique_id as the node_id since it's what we'll match on
                effective_node_id = f"PromptManager_{unique_id}"
                logger.info(f"PromptManager: Registering prompt with tracker - node_id={effective_node_id}, unique_id={unique_id}")
                if DEBUG:
                    print(f"🔗 Registering prompt with tracker - node_id: {effective_node_id}, unique_id: {unique_id}")

                # Log the current state before registration
                if hasattr(self.tracker, '_active_prompts'):
                    before_count = len(self.tracker._active_prompts)
                    logger.debug(f"PromptManager: Tracker has {before_count} active prompts before registration")

                result = self.tracker.register_prompt(
                    node_id=effective_node_id,
                    unique_id=unique_id,
                    prompt=final_text,
                    negative_prompt=negative_prompt,
                    workflow=extra_pnginfo.get("workflow") if extra_pnginfo else None,
                    extra_data={
                        "category": category,
                        "tags": tags,
                        "timestamp": time.time()
                    }
                )

                # Log the state after registration
                if hasattr(self.tracker, '_active_prompts'):
                    after_count = len(self.tracker._active_prompts)
                    logger.info(f"PromptManager: Tracker has {after_count} active prompts after registration")
                    if unique_id in self.tracker._active_prompts:
                        logger.info(f"PromptManager: Successfully registered unique_id={unique_id}")
                    else:
                        logger.error(f"PromptManager: FAILED to register unique_id={unique_id}!")

                logger.debug(f"Registered prompt with tracker (ID: {unique_id}, result={result})")
            
            # Save to database
            metadata = {
                "category": category,
                "tags": [tag.strip() for tag in tags.split(",") if tag.strip()],
                "generation_params": {},
                "has_prepend": bool(prepend_text),
                "has_append": bool(append_text),
                "tracked": bool(unique_id),  # Mark as tracked
                "unique_id": unique_id,       # Store for debugging
            }
            
            # If searching, perform search
            if search_text:
                search_results = self.db.search_prompts(search_text)
                if search_results:
                    logger.info(f"Found {len(search_results)} matching prompts")
                    # Could return first result or show in UI
            
            # Save the prompt (extract individual parameters from metadata)
            category = metadata.get("category", "")
            tags = metadata.get("tags", [])
            
            # Compute dedup hash and save (will return existing ID if duplicate)
            p_hash = generate_prompt_hash(final_text)

            prompt_id = self.db.save_prompt(
                text=final_text,
                category=category if category else None,
                tags=tags if tags else None,
                prompt_hash=p_hash
            )
            logger.info(f"Saved prompt ID {prompt_id} with tracking")

            # Store the prompt_id in the tracking data so we can use it later
            if not DISABLE_TRACKING and unique_id and self.tracker and hasattr(self.tracker, '_active_prompts'):
                if unique_id in getattr(self.tracker, '_active_prompts', {}):
                    try:
                        self.tracker._active_prompts[unique_id].metadata['prompt_id'] = prompt_id
                        if DEBUG:
                            print(f"📝 Stored prompt_id {prompt_id} in tracking data")
                    except Exception:
                        pass
            
            # Encode the text using CLIP (exact same approach as original working code)
            try:
                logger.debug(f"Performing CLIP text encoding on combined text: {final_text[:100]}...")
                tokens = clip.tokenize(final_text)
                conditioning = clip.encode_from_tokens_scheduled(tokens)
                logger.debug("CLIP encoding completed successfully")
                
                return (conditioning, final_text)
                
            except Exception as e:
                logger.error(f"Error encoding with CLIP: {e}")
                # Fallback encoding - create basic conditioning
                conditioning = [[final_text, {}]]
                return (conditioning, final_text)
            
        except Exception as e:
            logger.error(f"Error in PromptManager.encode: {e}", exc_info=True)
            # Return something valid to not break the workflow
            return ([[text, {}]], text)
    
    def _combine_text(self, prepend: str, main: str, append: str) -> str:
        """Combine prepend, main, and append text."""
        parts = []
        if prepend and prepend.strip():
            parts.append(prepend.strip())
        if main and main.strip():
            parts.append(main.strip())
        if append and append.strip():
            parts.append(append.strip())
        
        # Join with comma and space for prompt format
        return ", ".join(parts) if parts else ""
    
    def _clean_text(self, text: str) -> str:
        """Clean and normalize prompt text."""
        # Remove extra whitespace
        text = " ".join(text.split())
        # Remove duplicate commas
        text = ", ".join(filter(None, [t.strip() for t in text.split(",")]))
        return text
    
    def _get_node_id(self, prompt_data: Optional[Dict], extra_pnginfo: Optional[Dict]) -> Optional[str]:
        """Extract node ID from ComfyUI data."""
        # Try to get from prompt data
        if prompt_data:
            # This would need to be adapted based on ComfyUI's structure
            # Typically in prompt_data[node_id] format
            pass
        
        # Try to get from workflow
        if extra_pnginfo and "workflow" in extra_pnginfo:
            workflow = extra_pnginfo["workflow"]
            # Find this PromptManager node in the workflow
            for node in workflow.get("nodes", []):
                if node.get("type") == "PromptManager":
                    return str(node.get("id", ""))
        
        return None
    
    @classmethod
    def IS_CHANGED(cls, text="", category="", tags="", search_text="",
                    prepend_text="", append_text="", **kwargs):
        """
        ComfyUI method to determine if node needs re-execution.

        This method properly tracks input changes to avoid unnecessary
        re-execution while still ensuring prompts are saved when inputs change.

        Returns:
            A hash of the input values that changes when any input changes
        """
        # Create a hash of all the text inputs that affect the output
        # This ensures the node only re-executes when inputs actually change
        import hashlib

        # Combine all text inputs that affect the conditioning output
        combined = f"{text}|{category}|{tags}|{prepend_text}|{append_text}"

        # Return a hash that will change when inputs change
        # Note: We don't include search_text as it doesn't affect the output conditioning
        return hashlib.sha256(combined.encode()).hexdigest()


# Node registration for ComfyUI
NODE_CLASS_MAPPINGS = {
    "PromptManager": PromptManager
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptManager": "🧠 Prompt Manager (Tracked)"
}

# Initialize tracking when module loads
def initialize_tracking():
    """Initialize the tracking system when ComfyUI loads the node."""
    # Use singleton tracker to prevent duplicate initialization across different module names
    if SingletonTracker.is_initialized():
        logger.debug(f"Tracking system already initialized globally (current module: {__name__}), skipping duplicate initialization")
        return
    
    SingletonTracker.mark_initialized()
    logger.info(f"Initializing tracking system for module: {__name__}")
    tracker = get_prompt_tracker()
    logger.info("PromptManager tracking system initialized" if not DISABLE_TRACKING else "PromptManager tracking disabled (diagnostics mode)")
    
    # Start cleanup task if in async environment
    if not DISABLE_TRACKING:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            loop.create_task(tracker.start_cleanup_task())
        except:
            # Not in async environment, that's OK
            pass

# Run initialization
initialize_tracking()
