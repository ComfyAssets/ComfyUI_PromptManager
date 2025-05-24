"""
PromptManager: A ComfyUI custom node that extends the standard text encoder 
with persistent prompt storage and advanced search capabilities using SQLite.
"""

print("[PromptManager] Starting to load PromptManager custom node...")

try:
    from .prompt_manager import PromptManager
    print("[PromptManager] Successfully imported PromptManager class")
except Exception as e:
    print(f"[PromptManager] ERROR: Failed to import PromptManager class: {e}")
    import traceback
    print(traceback.format_exc())
    raise

NODE_CLASS_MAPPINGS = {
    "PromptManager": PromptManager,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptManager": "Prompt Manager",
}

print(f"[PromptManager] NODE_CLASS_MAPPINGS: {NODE_CLASS_MAPPINGS}")
print(f"[PromptManager] NODE_DISPLAY_NAME_MAPPINGS: {NODE_DISPLAY_NAME_MAPPINGS}")

# Define path to web directory for UI components
WEB_DIRECTORY = "web"

# Add API routes (same pattern as ComfyUI_Assets)
try:
    from .py import config
    from .py.api import PromptManagerAPI
    
    # Set extension URI
    import os
    extension_uri = os.path.dirname(__file__)
    config.extension_uri = extension_uri
    
    # Register API routes using the same pattern as ComfyUI_Assets
    routes = config.routes
    api = PromptManagerAPI()
    api.add_routes(routes)
    
    print("[PromptManager] API routes registered successfully")
    print(f"[PromptManager] Server instance: {config.server_instance}")
    print(f"[PromptManager] Routes object: {routes}")
    
except Exception as e:
    print(f"[PromptManager] Failed to register API routes: {e}")
    import traceback
    print(traceback.format_exc())

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

print("[PromptManager] ✅ PromptManager custom node loaded successfully!")