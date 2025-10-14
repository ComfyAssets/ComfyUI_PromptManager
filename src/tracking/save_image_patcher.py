"""SaveImage node patcher for automatic prompt tracking.

This module monkey-patches ComfyUI's SaveImage node to automatically
track prompt-to-image associations without requiring workflow changes.

- Tracking runs asynchronously to avoid blocking ComfyUI's workflow
- To enable verbose console printing, set PROMPTMANAGER_DEBUG=1
"""

import json
import os
import threading
import time
import warnings
from pathlib import Path
from queue import Queue
import torch
from typing import Any, Dict, Optional

# Import with fallbacks
try:
    from promptmanager.loggers import get_logger  # type: ignore
    from promptmanager.utils.settings import load_settings  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore
    from utils.settings import load_settings  # type: ignore

logger = get_logger("promptmanager.tracking.save_image_patcher")

PROMPT_NODE_TYPES = (
    "PromptManagerPositive",
    "PromptManagerV2",
    "PromptManager",
    "PromptManagerNegative",
)

PROMPT_NODE_PREFERENCE = (
    "PromptManagerPositive",
    "PromptManagerV2",
    "PromptManager",
    "PromptManagerNegative",
)


class SaveImagePatcher:
    """Patches SaveImage node to integrate prompt tracking."""
    
    def __init__(self, prompt_tracker):
        """Initialize the patcher.
        
        Args:
            prompt_tracker: Instance of PromptTracker
        """
        self.prompt_tracker = prompt_tracker
        self.original_save_func = None
        # Map of generically patched classes -> original save_images
        self._generic_patched: dict = {}
        self.patched = False
        # Debug + async tracking queue
        self.debug = os.getenv("PROMPTMANAGER_DEBUG", "0") == "1"
        # Always skip previews/temp files
        self.skip_previews = True
        self.final_only = os.getenv("PROMPTMANAGER_FINAL_ONLY", "0") == "1"
        self._queue: Queue = Queue()
        self._worker_started = False
        # Track queued images to prevent duplicates
        self._queued_images: set = set()
        self._queued_lock = threading.Lock()
        # Initialize options from UI settings if available (UI overrides env)
        try:
            s = load_settings()
            if isinstance(s, dict):
                if 'promptDebugTracking' in s:
                    self.debug = bool(s.get('promptDebugTracking'))
                # Always skip previews; ignore any legacy setting
                if 'promptFinalSaveOnly' in s:
                    self.final_only = bool(s.get('promptFinalSaveOnly'))
        except Exception:
            pass

    def _maybe_print(self, msg: str) -> None:
        if self.debug:
            print(msg)

    def _start_worker(self) -> None:
        if self._worker_started:
            return

        def _worker():
            while True:
                image_path, tracking_data, metadata = self._queue.get()
                try:
                    self.prompt_tracker.record_image_saved(
                        image_path,
                        tracking_data,
                        metadata=metadata,
                    )
                except Exception as e:
                    logger.error(f"Async tracking error: {e}", exc_info=True)
                finally:
                    # Remove from queued set when processing completes
                    with self._queued_lock:
                        self._queued_images.discard(image_path)
                    self._queue.task_done()

        t = threading.Thread(target=_worker, name="PM-TrackingWorker", daemon=True)
        t.start()
        self._worker_started = True

    def _select_prompt_node(
        self,
        prompt: Optional[Dict[str, Any]],
        extra_pnginfo: Optional[Dict[str, Any]],
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Determine which PromptManager-style node to attribute saved images to."""

        candidates: list[tuple[str, str]] = []
        prompt_dict: Dict[str, Any] = prompt if isinstance(prompt, dict) else {}

        if prompt_dict:
            logger.debug(
                "SaveImagePatcher: Analyzing prompt data with %d keys", len(prompt_dict)
            )
            all_class_types = {
                key: value.get("class_type")
                for key, value in prompt_dict.items()
                if isinstance(value, dict) and "class_type" in value
            }
            logger.debug(
                "SaveImagePatcher: All node class_types in prompt: %s", all_class_types
            )

            for key, value in prompt_dict.items():
                if not isinstance(value, dict):
                    continue
                class_type = value.get("class_type")
                if class_type in PROMPT_NODE_TYPES:
                    candidates.append((class_type, key))

        active_ids: set[str] = set()
        if hasattr(self.prompt_tracker, "debug_active_ids"):
            try:
                active_ids = set(self.prompt_tracker.debug_active_ids())
            except Exception:
                active_ids = set()

        # Get active prompts with timestamps for first-prompt-wins selection
        active_prompts = getattr(self.prompt_tracker, "_active_prompts", {})
        if isinstance(active_prompts, dict):
            active_ids = {str(getattr(t, "unique_id", "")) for t in active_prompts.values()}
        else:
            active_prompts = {}
            active_ids = set()

        def _select_from_candidates(require_active: bool) -> Optional[tuple[str, str, int]]:
            """Select candidate with LOWEST node ID (workflow order, not execution order)."""
            # Collect ALL matching candidates across all preference levels
            all_matches = []
            for class_type, key in candidates:
                if class_type not in PROMPT_NODE_TYPES:
                    continue
                if require_active and active_ids and key not in active_ids:
                    continue

                # Convert key to int for numerical sorting
                try:
                    node_id_num = int(key)
                except (ValueError, TypeError):
                    node_id_num = float('inf')  # Put non-numeric IDs at the end

                all_matches.append((class_type, key, node_id_num))

            # Sort by node ID (lowest first) - this represents workflow order
            if all_matches:
                all_matches.sort(key=lambda x: x[2])  # Sort by node ID (lowest first)
                return all_matches[0]
            return None

        selection = _select_from_candidates(require_active=True)
        if not selection:
            selection = _select_from_candidates(require_active=False)

        if selection:
            class_type, unique_id, node_id_num = selection
            if unique_id in active_ids or not active_ids:
                logger.info(
                    "SaveImagePatcher: Selected %s node - key=%s (node_id=%d, lowest)",
                    class_type, unique_id, node_id_num
                )
                return unique_id, f"{class_type}_{unique_id}", class_type

        # Fall back to active prompts tracked by the PromptTracker even if they
        # are not present in the current prompt payload (common with Kiko saves).
        # IMPORTANT: Select by LOWEST node ID (workflow order, not execution order)
        if active_prompts:
            # Collect ALL matching prompts across all types
            all_matches = []
            for tracking in active_prompts.values():
                node_id = getattr(tracking, "node_id", "")
                unique_id = getattr(tracking, "unique_id", None)
                if not node_id or not unique_id:
                    continue

                # Check if this is a PromptManager node (any type)
                is_prompt_node = any(node_id.startswith(f"{ptype}_") for ptype in PROMPT_NODE_TYPES)
                if is_prompt_node:
                    # Extract the node type
                    node_type = node_id.split("_")[0] if "_" in node_id else ""

                    # Convert unique_id to int for numerical sorting
                    try:
                        node_id_num = int(unique_id)
                    except (ValueError, TypeError):
                        node_id_num = float('inf')  # Put non-numeric IDs at the end

                    all_matches.append((node_id_num, unique_id, node_id, node_type))

            # Sort by node ID (lowest first) - this represents workflow order
            if all_matches:
                all_matches.sort(key=lambda x: x[0])  # Sort by node ID (lowest first)
                lowest_node_id_num, lowest_unique_id, lowest_node_id, lowest_type = all_matches[0]
                logger.info(
                    "SaveImagePatcher: Falling back to active %s node - key=%s (node_id=%d, lowest)",
                    lowest_type,
                    lowest_unique_id,
                    lowest_node_id_num,
                )
                return str(lowest_unique_id), lowest_node_id, lowest_type

        workflow_candidates: list[tuple[str, str]] = []
        if extra_pnginfo and isinstance(extra_pnginfo, dict):
            workflow = extra_pnginfo.get("workflow", {})
            workflow_nodes = workflow.get("nodes", []) if isinstance(workflow, dict) else []
            for node in workflow_nodes:
                node_type = node.get("type")
                if node_type in PROMPT_NODE_TYPES:
                    workflow_candidates.append((node_type, str(node.get("id", ""))))

        for preferred in PROMPT_NODE_PREFERENCE:
            for node_type, node_id in workflow_candidates:
                if node_type != preferred:
                    continue
                if prompt_dict and node_id in prompt_dict:
                    return node_id, f"{node_type}_{node_id}", node_type
                return node_id, f"{node_type}_{node_id}", node_type

        return None, None, None
        
    def patch(self) -> bool:
        """Apply the monkey patch to SaveImage node.

        Returns:
            True if patching succeeded, False otherwise
        """
        if self.patched:
            logger.warning("SaveImage already patched")
            return True

        try:
            # Import ComfyUI's SaveImage node
            import nodes

            if not hasattr(nodes, 'SaveImage'):
                logger.error("SaveImage node not found in ComfyUI")
                return False

            SaveImageClass = nodes.SaveImage

            # Store original save method (it's an instance method, not class method)
            self.original_save_func = SaveImageClass.save_images

            # Create patched version that accepts any arguments
            def patched_save_images(
                self_node,
                images,
                filename_prefix="ComfyUI",
                prompt=None,
                extra_pnginfo=None,
                *args,  # Accept any additional positional arguments
                **kwargs  # Accept any additional keyword arguments
            ):
                """Patched save_images that includes tracking."""

                self._start_worker()
                # Refresh settings on each interception (cheap and keeps runtime in sync with UI)
                try:
                    s = load_settings()
                    if isinstance(s, dict):
                        if 'promptDebugTracking' in s:
                            self.debug = bool(s.get('promptDebugTracking'))
                        # Always skip previews; ignore any legacy setting
                        if 'promptFinalSaveOnly' in s:
                            self.final_only = bool(s.get('promptFinalSaveOnly'))
                except Exception:
                    pass
                # Opportunistically re-scan for newly loaded save nodes
                try:
                    self._patch_generic_save_nodes()
                except Exception:
                    pass
                self._maybe_print("\n" + "="*80)
                self._maybe_print("🎯 SAVEIMAGE PATCHER - INTERCEPTED SAVE")
                self._maybe_print("="*80)
                self._maybe_print(f"📷 Images to save: {len(images) if images is not None else 0}")
                self._maybe_print(f"📝 filename_prefix: {filename_prefix}")
                self._maybe_print(f"📊 prompt provided: {prompt is not None}")
                self._maybe_print(f"📦 extra_pnginfo provided: {extra_pnginfo is not None}")
                node_class_name = self_node.__class__.__name__
                self._maybe_print(f"🏷️ Node type: {node_class_name}")
                self._maybe_print(f"🔧 Additional args: {args}")
                self._maybe_print(f"🔧 Additional kwargs: {kwargs}")

                # ONLY track for actual SaveImage and PreviewImage nodes
                # Skip other nodes that might use save_images (like ImageFilter)
                should_track = node_class_name in ['SaveImage', 'PreviewImage']

                if not should_track:
                    self._maybe_print(f"   ⏭️ Skipping tracking for {node_class_name} node")
                    # Just call the original function and return immediately
                    result = self.original_save_func(
                        self_node,
                        images,
                        filename_prefix,
                        prompt,
                        extra_pnginfo,
                        *args,
                        **kwargs
                    )
                    self._maybe_print(f"   ✅ Patcher passing through {node_class_name} result to ComfyUI")
                    return result

                # Extract tracking information
                unique_id, node_id, class_type = self._select_prompt_node(prompt, extra_pnginfo)

                if unique_id:
                    self._maybe_print(
                        f"   🎯 Selected prompt node {class_type} with unique_id={unique_id}"
                    )
                else:
                    logger.warning("SaveImagePatcher: Unable to locate a PromptManager node in workflow")
                
                # Call original save function FIRST, before any tracking
                # Pass all arguments including any extras
                result = self.original_save_func(
                    self_node,
                    images,
                    filename_prefix,
                    prompt,
                    extra_pnginfo,
                    *args,  # Pass through any additional positional arguments
                    **kwargs  # Pass through any additional keyword arguments
                )

                # Now do tracking AFTER the save completes successfully
                # Wrap in try-except to ensure we don't interfere with workflow
                try:
                    if result and "ui" in result:
                        images_info = result["ui"].get("images", [])

                        for img_info in images_info:
                            if "filename" in img_info:
                                # Get full path
                                filename = img_info["filename"]
                                subfolder = img_info.get("subfolder", "")

                                # Check if this is a preview/temp image
                                img_type = img_info.get("type", "output")
                                is_temp = "_temp_" in filename or img_type == "temp"

                                # Always skip previews/temp
                                if is_temp:
                                    self._maybe_print(f"   ⏭️ Skipping preview/temp image: {filename}")
                                    continue
                                # Final-save-only: only track output images
                                if self.final_only and img_type != 'output':
                                    self._maybe_print(f"   ⏭️ Skipping non-final image (type={img_type}): {filename}")
                                    continue

                                # Construct absolute + relative paths (align with ComfyUI output structure)
                                output_dir = getattr(self_node, "output_dir", "")
                                base_path = None
                                base_resolved = None

                                # First try to use ComfyUI's actual output directory function
                                try:
                                    import folder_paths
                                    # Get the actual output directory from ComfyUI
                                    actual_output_dir = folder_paths.get_output_directory()

                                    # If node has a specific output_dir (like "testing"), it's a subdirectory
                                    if output_dir and output_dir not in ["", "output"]:
                                        # output_dir like "testing" should be under the main output directory
                                        base_resolved = Path(actual_output_dir) / output_dir
                                        logger.debug(f"SaveImagePatcher: Resolved path with subdirectory - output_dir: {output_dir}, base_resolved: {base_resolved}")
                                    else:
                                        base_resolved = Path(actual_output_dir)
                                        logger.debug(f"SaveImagePatcher: Resolved path without subdirectory - base_resolved: {base_resolved}")

                                    base_resolved = base_resolved.expanduser().resolve()
                                except Exception as e:
                                    # Fallback to using the node's output_dir attribute
                                    if output_dir:
                                        try:
                                            # If output_dir is relative (like "output"), resolve it relative to ComfyUI root
                                            base_path = Path(output_dir)
                                            if not base_path.is_absolute():
                                                # Try to get ComfyUI root from folder_paths
                                                try:
                                                    import folder_paths
                                                    comfy_root = Path(folder_paths.base_path)
                                                    # If output_dir is a subdirectory name, it goes under "output"
                                                    if output_dir != "output":
                                                        base_path = comfy_root / "output" / output_dir
                                                    else:
                                                        base_path = comfy_root / output_dir
                                                except:
                                                    # Fallback: look for ComfyUI markers
                                                    import sys
                                                    for p in sys.path:
                                                        p_path = Path(p)
                                                        if (p_path / "comfy").exists() and (p_path / "web").exists():
                                                            if output_dir != "output":
                                                                base_path = p_path / "output" / output_dir
                                                            else:
                                                                base_path = p_path / output_dir
                                                            break
                                            base_resolved = base_path.expanduser().resolve()
                                        except Exception:
                                            try:
                                                base_resolved = Path(output_dir).absolute()
                                            except Exception:
                                                base_resolved = None

                                if base_resolved:
                                    if subfolder:
                                        full_path = base_resolved / Path(subfolder) / filename
                                    else:
                                        full_path = base_resolved / filename
                                    logger.debug(f"SaveImagePatcher: Final full_path: {full_path} (base_resolved: {base_resolved}, subfolder: {subfolder})")
                                else:
                                    if subfolder:
                                        full_path = Path(subfolder) / filename
                                    else:
                                        full_path = Path(filename)
                                    logger.debug(f"SaveImagePatcher: Final full_path (no base): {full_path}")

                                try:
                                    full_path = full_path.expanduser().resolve()
                                except Exception:
                                    full_path = full_path.absolute()

                                image_path = str(full_path)
                                relative_path = None
                                if base_resolved:
                                    try:
                                        relative_path = str(full_path.relative_to(base_resolved))
                                    except Exception:
                                        relative_path = None
                                if not relative_path:
                                    # Fallback to filename (fine for legacy gallery lookups)
                                    relative_path = filename

                                self._maybe_print(f"   📍 Path resolution:")
                                self._maybe_print(f"      output_dir: {output_dir}")
                                self._maybe_print(f"      base_resolved: {base_resolved}")
                                self._maybe_print(f"      subfolder: {subfolder}")
                                self._maybe_print(f"      full_path: {full_path}")
                                self._maybe_print(f"      image_path: {image_path}")

                                # Get tracking data
                                tracking_data = None

                                # Log the current state of the tracker
                                if hasattr(self.prompt_tracker, '_active_prompts'):
                                    active_ids = list(self.prompt_tracker._active_prompts.keys())
                                    logger.debug(f"SaveImagePatcher: Active prompts in tracker: {active_ids}")
                                    logger.debug(f"SaveImagePatcher: Looking for node_id={node_id}, unique_id={unique_id}")

                                    # Log details about each active prompt
                                    for aid in active_ids:
                                        prompt_data = self.prompt_tracker._active_prompts.get(aid)
                                        if prompt_data:
                                            logger.debug(f"  - Active ID {aid}: node_id={getattr(prompt_data, 'node_id', 'N/A')}")

                                self._maybe_print(f"   🔍 Looking for tracking data - node_id: {node_id}, unique_id: {unique_id}")
                                if node_id:
                                    logger.info(f"SaveImagePatcher: Calling get_prompt_for_save with node_id={node_id}, unique_id={unique_id}")
                                    tracking_data = self.prompt_tracker.get_prompt_for_save(
                                        node_id, unique_id
                                    )
                                    if tracking_data:
                                        logger.info(f"SaveImagePatcher: Successfully got tracking data for {filename}")
                                    else:
                                        logger.warning(f"SaveImagePatcher: No tracking data returned for {filename}")

                                if tracking_data:
                                    self._maybe_print(f"   ✅ Found tracking data for prompt: {tracking_data.prompt_text[:50]}...")
                                    # Enqueue for async processing to avoid blocking the save
                                    # Prepare metadata for DB linkage
                                    meta = {
                                        "filename_prefix": filename_prefix,
                                        "subfolder": subfolder,
                                        "type": img_type,
                                        "parameters": {
                                            "relative_path": relative_path,
                                            "absolute_path": image_path,
                                            "source": "save_image_patch",
                                            "subfolder": subfolder or '',
                                            "type": img_type,
                                        },
                                    }
                                    if isinstance(extra_pnginfo, dict):
                                        meta["workflow"] = extra_pnginfo.get("workflow")
                                    if isinstance(prompt, dict):
                                        meta["prompt"] = prompt

                                    # Deduplicate: only queue if not already queued
                                    with self._queued_lock:
                                        if image_path not in self._queued_images:
                                            self._queued_images.add(image_path)
                                            self._queue.put((image_path, tracking_data, meta))
                                            self._maybe_print(f"   💾 Tracked image {filename} with prompt (queued)")
                                            logger.debug(f"Tracked image {filename} with prompt")
                                        else:
                                            self._maybe_print(f"   ⏭️ Skipped duplicate queue entry for {filename}")
                                            logger.debug(f"Skipped duplicate queue entry for {filename}")
                                else:
                                    self._maybe_print(f"   ⚠️ No tracking data found for image {filename}")
                                    try:
                                        active_ids = self.prompt_tracker.debug_active_ids()
                                    except Exception:
                                        active_ids = []
                                    logger.info(
                                        f"No tracking data found for image {filename}; node_id={node_id} unique_id={unique_id} active_ids={active_ids}"
                                    )
                except Exception as tracking_error:
                    # Log the error but DON'T re-raise it - we don't want to break the workflow
                    self._maybe_print(f"   ❌ Error tracking image: {tracking_error}")
                    logger.error(f"Error tracking image: {tracking_error}", exc_info=True)

                # ALWAYS return the original result, even if tracking failed
                self._maybe_print(f"   ✅ Patcher completing, returning result to ComfyUI")
                return result
            
            # Apply the patch
            SaveImageClass.save_images = patched_save_images
            self.patched = True
            self._maybe_print("✅ SAVEIMAGE PATCHER - Successfully patched SaveImage node")
            logger.info("Successfully patched SaveImage node")
            # Try to patch additional custom save-like nodes generically
            self._patch_generic_save_nodes()
            return True
            
        except Exception as e:
            logger.error(f"Failed to patch SaveImage: {e}")
            return False

    def _patch_generic_save_nodes(self) -> None:
        """Patch all loaded classes that expose a save_images method.

        This avoids relying on specific node names. A wrapper is applied that:
        - Calls the original method
        - If the result contains UI images, attempts to link them using prompt/extra_pnginfo
        The wrapper is safe and returns original results regardless of tracking success.
        """
        try:
            import sys
            import types
            import nodes as comfy_nodes
            import warnings as _warnings
        except Exception:
            return
        
        # Try to import CryptographyDeprecationWarning once
        try:
            from cryptography.utils import CryptographyDeprecationWarning
            has_crypto_warning = True
        except ImportError:
            has_crypto_warning = False

        patched_count = 0
        for mod in list(sys.modules.values()):
            if not mod or not isinstance(mod, types.ModuleType):
                continue
            mod_name = getattr(mod, "__name__", "")
            if mod_name.startswith("torch.distributed"):
                continue
            try:
                for attr_name in dir(mod):
                    # Suppress noisy deprecation/future warnings from third-party modules
                    with _warnings.catch_warnings():
                        _warnings.simplefilter('ignore', category=DeprecationWarning)
                        _warnings.simplefilter('ignore', category=FutureWarning)
                        # Also ignore cryptography-specific warnings if available
                        if has_crypto_warning:
                            _warnings.simplefilter('ignore', category=CryptographyDeprecationWarning)
                        # Ignore UserWarning too (for torch.distributed warnings)
                        _warnings.simplefilter('ignore', category=UserWarning)
                        obj = getattr(mod, attr_name, None)
                    if not isinstance(obj, type):
                        continue
                    if obj is getattr(comfy_nodes, 'SaveImage', None):
                        # Already patched by the dedicated hook above
                        continue
                    # Suppress warnings during attribute checking
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        has_save_images = hasattr(obj, 'save_images') and callable(getattr(obj, 'save_images'))

                    if has_save_images:
                        if obj in self._generic_patched:
                            continue
                        original = getattr(obj, 'save_images')

                        def make_wrapper(klass, orig):
                            def wrapper(self_node, *args, **kwargs):
                                # Call original first
                                result = orig(self_node, *args, **kwargs)
                                # Extract common parameters if present
                                prompt = kwargs.get('prompt')
                                extra_pnginfo = kwargs.get('extra_pnginfo')
                                unique_kw = kwargs.get('unique_id')
                                filename_prefix = kwargs.get('filename_prefix', klass.__name__)

                                # Bind positional args to names when possible
                                try:
                                    import inspect as _inspect
                                    sig = _inspect.signature(orig)
                                    bound = sig.bind(self_node, *args, **kwargs)
                                    bound.apply_defaults()
                                    ba = bound.arguments
                                    prompt = ba.get('prompt', prompt)
                                    extra_pnginfo = ba.get('extra_pnginfo', extra_pnginfo)
                                    filename_prefix = ba.get('filename_prefix', filename_prefix)
                                    unique_kw = ba.get('unique_id', unique_kw)
                                except Exception:
                                    pass

                                try:
                                    if result and isinstance(result, dict) and 'ui' in result:
                                        images_info = result['ui'].get('images', [])
                                        if not images_info:
                                            return result

                                        unique_id = None
                                        node_id = None

                                        # Reuse main selection logic so positive/negative widgets are detected
                                        selected_unique, selected_node_id, _ = self._select_prompt_node(prompt, extra_pnginfo)  # type: ignore[attr-defined]
                                        if selected_unique and selected_node_id:
                                            unique_id = selected_unique
                                            node_id = selected_node_id
                                        elif isinstance(unique_kw, str):
                                            unique_id = unique_kw

                                        for img_info in images_info:
                                            if not isinstance(img_info, dict) or 'filename' not in img_info:
                                                continue
                                            filename = img_info.get('filename')
                                            subfolder = img_info.get('subfolder', '')

                                            # Construct full path using ComfyUI's output directory
                                            try:
                                                import folder_paths
                                                base_output_dir = folder_paths.get_output_directory()

                                                # Get the output_dir attribute from the node if available
                                                output_dir = getattr(obj, 'output_dir', '')

                                                # Build the full path
                                                if output_dir and output_dir not in ['', 'output']:
                                                    # Node has a custom output directory like "testing"
                                                    base_path = Path(base_output_dir) / output_dir
                                                else:
                                                    base_path = Path(base_output_dir)

                                                if subfolder:
                                                    full_path = base_path / subfolder / filename
                                                else:
                                                    full_path = base_path / filename

                                                image_path = str(full_path.resolve())
                                                logger.debug(f"Generic save: Constructed full path: {image_path}")

                                            except Exception as e:
                                                logger.warning(f"Failed to construct full path, using relative: {e}")
                                                # Fallback to relative path
                                                image_path = f"{subfolder}/{filename}" if subfolder else filename

                                            img_type = img_info.get('type', 'output')
                                            is_temp = ('_temp_' in filename) or (img_type == 'temp')
                                            if is_temp and self.skip_previews:
                                                continue

                                            tracking_data = None
                                            if node_id or unique_id:
                                                tracking_data = self.prompt_tracker.get_prompt_for_save(node_id or '', unique_id)

                                            if tracking_data:
                                                meta = {
                                                    'filename_prefix': filename_prefix,
                                                    'subfolder': subfolder,
                                                    'type': img_type,
                                                }
                                                if isinstance(extra_pnginfo, dict):
                                                    meta['workflow'] = extra_pnginfo.get('workflow')
                                                if isinstance(prompt, dict):
                                                    meta['prompt'] = prompt

                                                # Deduplicate: only queue if not already queued
                                                with self._queued_lock:
                                                    if image_path not in self._queued_images:
                                                        self._queued_images.add(image_path)
                                                        self._queue.put((image_path, tracking_data, meta))
                                                        logger.debug(f"Queued generic save image {filename} for tracking")
                                                    else:
                                                        logger.debug(f"Skipped duplicate queue entry for generic save {filename}")
                                            else:
                                                # Log summarised debug only
                                                try:
                                                    active_ids = self.prompt_tracker.debug_active_ids()
                                                except Exception:
                                                    active_ids = []

                                                # More detailed logging about why tracking failed
                                                logger.warning(f"❌ NO TRACKING for {klass.__name__} image {filename}")
                                                logger.info(f"  Searched for: node_id={node_id}, unique_id={unique_id}")
                                                logger.info(f"  Active IDs in tracker: {active_ids}")
                                                if hasattr(self.prompt_tracker, '_active_prompts'):
                                                    for aid, data in self.prompt_tracker._active_prompts.items():
                                                        logger.debug(f"    - ID {aid}: node_id={getattr(data, 'node_id', 'N/A')}")

                                                logger.info(
                                                    f"No tracking for {klass.__name__} image {filename}; node_id={node_id} unique_id={unique_id} active_ids={active_ids}"
                                                )
                                except Exception as e:
                                    logger.error(f"Error in generic save_images tracking for {klass.__name__}: {e}", exc_info=True)

                                return result
                            return wrapper

                        try:
                            setattr(obj, 'save_images', make_wrapper(obj, original))
                            self._generic_patched[obj] = original
                            patched_count += 1
                        except Exception:
                            continue
            except Exception:
                continue
        if patched_count:
            logger.info(f"Patched {patched_count} generic save_images classes for tracking")
    
    def unpatch(self) -> bool:
        """Remove the monkey patch.
        
        Returns:
            True if unpatching succeeded, False otherwise
        """
        if not self.patched:
            logger.warning("SaveImage not patched")
            return True
        
        try:
            import nodes
            
            if self.original_save_func:
                nodes.SaveImage.save_images = self.original_save_func
                self.patched = False
                logger.info("Successfully unpatched SaveImage node")
                # Try to unpatch custom nodes
                try:
                    for klass, orig in list(self._generic_patched.items()):
                        try:
                            setattr(klass, 'save_images', orig)
                        except Exception:
                            pass
                    self._generic_patched.clear()
                except Exception:
                    pass
                return True
            else:
                logger.error("Original save function not stored")
                return False
                
        except Exception as e:
            logger.error(f"Failed to unpatch SaveImage: {e}")
            return False
    
    def create_custom_save_node(self) -> Dict[str, Any]:
        """Create a custom SaveImageTracked node as an alternative to patching.
        
        Returns:
            Node definition dictionary for ComfyUI
        """
        class SaveImageTracked:
            """Custom save node with built-in tracking."""
            
            def __init__(self):
                self.output_dir = "output"
                self.type = "output"
            
            @classmethod
            def INPUT_TYPES(cls):
                return {
                    "required": {
                        "images": ("IMAGE",),
                        "filename_prefix": ("STRING", {"default": "ComfyUI"})
                    },
                    "hidden": {
                        "prompt": "PROMPT",
                        "extra_pnginfo": "EXTRA_PNGINFO",
                        "unique_id": "UNIQUE_ID"
                    }
                }
            
            RETURN_TYPES = ()
            FUNCTION = "save_images_tracked"
            OUTPUT_NODE = True
            CATEGORY = "image"
            
            def save_images_tracked(
                self,
                images,
                filename_prefix="ComfyUI",
                prompt=None,
                extra_pnginfo=None,
                unique_id=None
            ):
                """Save images with automatic tracking."""
                
                # Import here to avoid circular dependency
                import nodes
                
                # Use ComfyUI's standard save logic
                saver = nodes.SaveImage()
                result = saver.save_images(
                    images,
                    filename_prefix,
                    prompt,
                    extra_pnginfo
                )
                
                # Add tracking
                if result and "ui" in result:
                    images_info = result["ui"].get("images", [])
                    
                    for img_info in images_info:
                        if "filename" in img_info:
                            filename = img_info["filename"]
                            
                            # Get tracking data using unique_id
                            tracking_data = self.prompt_tracker.get_prompt_for_save(
                                self.__class__.__name__, unique_id
                            )
                            
                            if tracking_data:
                                self.prompt_tracker.record_image_saved(
                                    filename,
                                    tracking_data,
                                    metadata={"custom_node": True}
                                )
                
                return result
        
        # Bind the tracker to the class
        SaveImageTracked.prompt_tracker = self.prompt_tracker
        
        return {
            "SaveImageTracked": SaveImageTracked
        }
