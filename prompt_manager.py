"""
PromptManager: Main custom node implementation that extends CLIPTextEncode
with persistent prompt storage and search capabilities.
"""

import hashlib
import datetime
import json
import webbrowser
import os
from typing import Optional, Dict, Any, Tuple, List

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
    from .database.operations import PromptDatabase
    from .utils.prompt_tracker import PromptTracker, PromptExecutionContext
    from .utils.image_monitor import ImageMonitor
except ImportError:
    # For direct imports when not in a package
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from database.operations import PromptDatabase
    from utils.prompt_tracker import PromptTracker, PromptExecutionContext
    from utils.image_monitor import ImageMonitor


class PromptManager(ComfyNodeABC):
    """
    A ComfyUI custom node that functions like CLIPTextEncode but adds:
    - Persistent storage of all prompts in SQLite database
    - Search and retrieval capabilities
    - Metadata management (categories, tags, ratings, notes)
    - Duplicate detection via SHA256 hashing
    """
    
    def __init__(self):
        self.db = PromptDatabase()
        self.prompt_tracker = PromptTracker(self.db)
        self.image_monitor = ImageMonitor(self.db, self.prompt_tracker)
        
        # Start image monitoring automatically
        self._start_gallery_system()
    
    @classmethod
    def INPUT_TYPES(cls) -> InputTypeDict:
        return {
            "required": {
                "text": (IO.STRING, {
                    "multiline": True, 
                    "dynamicPrompts": True, 
                    "tooltip": "The text prompt to be encoded and saved to database."
                }),
                "clip": (IO.CLIP, {
                    "tooltip": "The CLIP model used for encoding the text."
                })
            },
            "optional": {
                "category": (IO.STRING, {
                    "default": "",
                    "tooltip": "Optional category for organizing prompts (e.g., 'landscapes', 'portraits')"
                }),
                "tags": (IO.STRING, {
                    "default": "",
                    "tooltip": "Comma-separated tags for the prompt (e.g., 'anime, detailed, sunset')"
                }),
                "search_text": (IO.STRING, {
                    "default": "",
                    "tooltip": "Search for past prompts containing this text"
                })
            }
        }
    
    RETURN_TYPES = (IO.CONDITIONING,)
    OUTPUT_TOOLTIPS = (
        "A conditioning containing the embedded text used to guide the diffusion model.",
    )
    FUNCTION = "encode"
    CATEGORY = "PromptManager/Text"
    DESCRIPTION = (
        "Encodes a text prompt using a CLIP model into an embedding that can be used to guide "
        "the diffusion model towards generating specific images. Additionally saves all prompts "
        "to a local SQLite database with optional metadata for search and retrieval."
    )
    
    def encode(
        self, 
        clip, 
        text: str,
        category: str = "",
        tags: str = "",
        search_text: str = ""
    ) -> Tuple[Any]:
        """
        Encode the text prompt and save it to the database.
        
        Args:
            clip: The CLIP model for encoding
            text: The text prompt to encode
            category: Optional category for organization
            tags: Comma-separated tags
            search_text: Text to search for in past prompts
            
        Returns:
            Tuple containing the conditioning for the diffusion model
            
        Raises:
            RuntimeError: If clip input is invalid
        """
        # Search functionality is now handled by the JavaScript UI
        # The search parameters are still available for backend processing if needed
        
        # Validate CLIP model
        if clip is None:
            raise RuntimeError(
                "ERROR: clip input is invalid: None\n\n"
                "If the clip is from a checkpoint loader node your checkpoint does not "
                "contain a valid clip or text encoder model."
            )
        
        # Save prompt to database and set execution context for gallery tracking
        prompt_id = None
        if text and text.strip():
            try:
                prompt_id = self._save_prompt_to_database(
                    text=text.strip(),  # Always strip whitespace
                    category=category.strip() if category else None,
                    tags=self._parse_tags(tags)
                )
                
                # Set current prompt for image tracking
                if prompt_id:
                    execution_id = self.prompt_tracker.set_current_prompt(
                        prompt_text=text.strip(),
                        additional_data={
                            'category': category.strip() if category else None,
                            'tags': self._parse_tags(tags),
                            'prompt_id': prompt_id
                        }
                    )
                    print(f"[PromptManager] Set execution context: {execution_id}")
                
            except Exception as e:
                # Log error but don't fail the encoding
                print(f"Warning: Failed to save prompt to database: {e}")
        
        # Perform standard CLIP text encoding
        tokens = clip.tokenize(text)
        conditioning = clip.encode_from_tokens_scheduled(tokens)
        
        return (conditioning,)
    
    def _save_prompt_to_database(
        self,
        text: str,
        category: Optional[str] = None,
        tags: Optional[list] = None
    ) -> Optional[int]:
        """
        Save the prompt to the SQLite database.
        
        Args:
            text: The prompt text
            category: Optional category
            tags: List of tags
            
        Returns:
            The prompt ID if saved successfully, None otherwise
        """
        try:
            # Generate hash for duplicate detection
            prompt_hash = self._generate_hash(text)
            
            # Check if prompt already exists
            existing = self.db.get_prompt_by_hash(prompt_hash)
            if existing:
                # Update metadata if this is a duplicate with new info
                if any([category, tags]):
                    self.db.update_prompt_metadata(
                        prompt_id=existing['id'],
                        category=category,
                        tags=tags
                    )
                return existing['id']
            
            # Save new prompt
            prompt_id = self.db.save_prompt(
                text=text,
                category=category,
                tags=tags,
                prompt_hash=prompt_hash
            )
            
            return prompt_id
            
        except Exception as e:
            print(f"Error saving prompt to database: {e}")
            return None
    
    def _generate_hash(self, text: str) -> str:
        """Generate SHA256 hash for the prompt text."""
        # Normalize text for consistent hashing (strip whitespace, normalize case)
        normalized_text = text.strip().lower()
        return hashlib.sha256(normalized_text.encode('utf-8')).hexdigest()
    
    def _parse_tags(self, tags_string: str) -> Optional[list]:
        """Parse comma-separated tags string into a list."""
        if not tags_string or not tags_string.strip():
            return None
        
        tags = [tag.strip() for tag in tags_string.split(',') if tag.strip()]
        return tags if tags else None
    
    def _search_prompts(self, search_text: str = "") -> List[Dict[str, Any]]:
        """Search for past prompts by text content."""
        try:
            if not search_text or not search_text.strip():
                return []
            
            results = self.db.search_prompts(
                text=search_text.strip(),
                category=None,
                tags=None,
                rating_min=None,
                limit=50
            )
            
            return results
            
        except Exception as e:
            print(f"Error searching prompts: {e}")
            return []
    
    def _open_web_interface(self):
        """Open the web interface in the default browser."""
        try:
            # Look for a web interface directory
            current_dir = os.path.dirname(os.path.abspath(__file__))
            web_dir = os.path.join(current_dir, "web_interface")
            
            if os.path.exists(web_dir):
                # If web interface exists, try to start it
                index_path = os.path.join(web_dir, "index.html")
                if os.path.exists(index_path):
                    webbrowser.open(f"file://{index_path}")
                    print("\n=== Web interface opened in browser ===")
                else:
                    print("\n=== Web interface directory found but no index.html ===")
                    print(f"Please check {web_dir} for setup instructions")
            else:
                print("\n=== Web interface not yet implemented ===")
                print("This feature will open a web-based prompt management interface")
                print("when the web_interface directory is created.")
                
        except Exception as e:
            print(f"Error opening web interface: {e}")
    
    def search_prompts_api(self, search_text: str = "") -> List[Dict[str, Any]]:
        """API method for JavaScript UI to search prompts."""
        return self._search_prompts(search_text=search_text)
    
    def get_recent_prompts_api(self, limit: int = 20) -> List[Dict[str, Any]]:
        """API method for JavaScript UI to get recent prompts."""
        try:
            return self.db.get_recent_prompts(limit=limit)
        except Exception as e:
            print(f"Error getting recent prompts: {e}")
            return []
    
    def _start_gallery_system(self):
        """Initialize and start the gallery monitoring system."""
        try:
            print("[PromptManager] Starting gallery system...")
            
            # Start image monitoring
            self.image_monitor.start_monitoring()
            
            print("[PromptManager] Gallery system started successfully")
            
        except Exception as e:
            print(f"[PromptManager] Failed to start gallery system: {e}")
            print("[PromptManager] Gallery features will be disabled")
    
    def get_gallery_status(self) -> Dict[str, Any]:
        """Get status of the gallery system."""
        return {
            'image_monitor': self.image_monitor.get_status(),
            'prompt_tracker': self.prompt_tracker.get_status()
        }
    
    def cleanup_gallery_system(self):
        """Clean up gallery system resources."""
        try:
            if hasattr(self, 'image_monitor'):
                self.image_monitor.stop_monitoring()
            print("[PromptManager] Gallery system cleaned up")
        except Exception as e:
            print(f"[PromptManager] Error cleaning up gallery system: {e}")
    
    def __del__(self):
        """Cleanup when object is destroyed."""
        self.cleanup_gallery_system()
    
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        """Always process to ensure database saving."""
        return float("NaN")  # Always execute