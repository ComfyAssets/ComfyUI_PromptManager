"""
ComfyUI integration utilities for PromptManager.
Provides hooks and patches to ensure PromptManager metadata appears in standard ComfyUI metadata.
"""

import threading
import time
import json
from typing import Dict, Any, Optional

try:
    from .logging_config import get_logger
except ImportError:
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from utils.logging_config import get_logger


class ComfyUIMetadataIntegration:
    """Integrates PromptManager with ComfyUI's standard metadata system."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        
        self.logger = get_logger('prompt_manager.comfyui_integration')
        self._current_prompts = {}
        self._thread_local = threading.local()
        self._saveimage_patched = False
        self._initialized = True
        
        # Try to patch SaveImage node on initialization
        self._patch_saveimage_node()
    
    def register_prompt(self, node_id: str, prompt_text: str, metadata: Dict[str, Any]):
        """
        Register a prompt from PromptManager for inclusion in ComfyUI metadata.
        
        Args:
            node_id: The node ID (unique identifier for this prompt)
            prompt_text: The actual prompt text that was encoded
            metadata: Additional metadata from PromptManager
        """
        thread_id = threading.current_thread().ident
        
        # Store in thread-local storage
        if not hasattr(self._thread_local, 'prompts'):
            self._thread_local.prompts = {}
        
        self._thread_local.prompts[node_id] = {
            'text': prompt_text,
            'metadata': metadata,
            'timestamp': time.time(),
            'thread_id': thread_id
        }
        
        # Also store globally for cross-thread access
        with self._lock:
            self._current_prompts[f"{thread_id}_{node_id}"] = {
                'text': prompt_text,
                'metadata': metadata,
                'timestamp': time.time(),
                'thread_id': thread_id
            }
        
        self.logger.debug(f"Registered prompt for node {node_id}: {prompt_text[:50]}...")
    
    def get_current_prompt_text(self, node_id: str = None) -> Optional[str]:
        """
        Get the current prompt text for metadata inclusion.
        
        Args:
            node_id: Optional specific node ID to get prompt for
            
        Returns:
            The prompt text or None
        """
        # First try thread-local storage
        if hasattr(self._thread_local, 'prompts'):
            if node_id and node_id in self._thread_local.prompts:
                return self._thread_local.prompts[node_id]['text']
            elif self._thread_local.prompts:
                # Return the most recent prompt from this thread
                latest = max(self._thread_local.prompts.values(), key=lambda x: x['timestamp'])
                return latest['text']
        
        # Fallback to global storage
        thread_id = threading.current_thread().ident
        with self._lock:
            # Look for prompts from current thread
            thread_prompts = {k: v for k, v in self._current_prompts.items() 
                            if v['thread_id'] == thread_id}
            
            if thread_prompts:
                latest = max(thread_prompts.values(), key=lambda x: x['timestamp'])
                return latest['text']
            
            # Last resort: return the most recent prompt from any thread
            if self._current_prompts:
                latest = max(self._current_prompts.values(), key=lambda x: x['timestamp'])
                # Only return if it's recent (within last 5 minutes)
                if time.time() - latest['timestamp'] < 300:
                    return latest['text']
        
        return None
    
    def _patch_saveimage_node(self):
        """
        Patch ComfyUI's SaveImage node to include PromptManager prompts in metadata.
        """
        try:
            import nodes
            
            if not hasattr(nodes, 'SaveImage'):
                self.logger.warning("SaveImage node not found in ComfyUI nodes")
                return
            
            # Store original save_images method
            original_save_images = nodes.SaveImage.save_images
            integration = self  # Capture self reference
            
            def patched_save_images(self_node, images, filename_prefix="ComfyUI", prompt=None, extra_pnginfo=None):
                """Patched save_images method that includes PromptManager prompts."""
                
                # Get current prompt text from PromptManager
                current_prompt_text = integration.get_current_prompt_text()
                
                if current_prompt_text:
                    integration.logger.debug(f"Including PromptManager prompt in SaveImage metadata: {current_prompt_text[:50]}...")
                    
                    # If no prompt provided, create one with our text
                    if prompt is None:
                        prompt = {}
                    
                    # Ensure prompt has the standard structure ComfyUI expects
                    if not isinstance(prompt, dict):
                        prompt = {}
                    
                    # Find PromptManager nodes and fix them for standard parser compatibility
                    prompt_updated = False
                    for node_id, node_data in prompt.items():
                        if isinstance(node_data, dict):
                            class_type = node_data.get('class_type', '')
                            if 'promptmanager' in class_type.lower():
                                # Update the inputs to include our actual prompt text
                                if 'inputs' not in node_data:
                                    node_data['inputs'] = {}
                                node_data['inputs']['text'] = current_prompt_text
                                
                                # SIMPLE FIX: Change class_type to CLIPTextEncode for standard parser compatibility
                                # Keep original class_type in metadata for reference
                                if '_meta' not in node_data:
                                    node_data['_meta'] = {}
                                node_data['_meta']['original_class_type'] = class_type
                                node_data['class_type'] = 'CLIPTextEncode'
                                
                                prompt_updated = True
                                integration.logger.debug(f"Fixed PromptManager node {node_id} - changed class_type to CLIPTextEncode for compatibility")
                    
                    # If no PromptManager nodes found, add a standalone one
                    if not prompt_updated:
                        virtual_node_id = "promptmanager_text"
                        prompt[virtual_node_id] = {
                            "class_type": "CLIPTextEncode",  # Use CLIPTextEncode for compatibility
                            "inputs": {
                                "text": current_prompt_text
                            },
                            "_meta": {
                                "original_class_type": "PromptManager",
                                "virtual": True
                            }
                        }
                        integration.logger.debug("Added standalone CLIPTextEncode node with PromptManager text")
                
                # Call original method with potentially modified prompt
                return original_save_images(self_node, images, filename_prefix, prompt, extra_pnginfo)
            
            # Apply the patch
            nodes.SaveImage.save_images = patched_save_images
            self._saveimage_patched = True
            self.logger.info("Successfully patched SaveImage node for PromptManager integration")
            
        except Exception as e:
            self.logger.error(f"Failed to patch SaveImage node: {e}")
            self.logger.warning("PromptManager prompts may not appear in standard ComfyUI metadata")
    
    def cleanup_old_prompts(self, max_age_seconds: int = 600):
        """
        Clean up old prompt registrations.
        
        Args:
            max_age_seconds: Maximum age in seconds before cleanup
        """
        current_time = time.time()
        
        with self._lock:
            old_keys = [
                key for key, prompt_data in self._current_prompts.items()
                if current_time - prompt_data['timestamp'] > max_age_seconds
            ]
            
            for key in old_keys:
                del self._current_prompts[key]
        
        if old_keys:
            self.logger.debug(f"Cleaned up {len(old_keys)} old prompt registrations")


# Global instance
_integration_instance = None

def get_comfyui_integration() -> ComfyUIMetadataIntegration:
    """Get the global ComfyUI integration instance."""
    global _integration_instance
    if _integration_instance is None:
        _integration_instance = ComfyUIMetadataIntegration()
    return _integration_instance