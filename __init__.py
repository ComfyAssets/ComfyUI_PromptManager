"""
PromptManager: A ComfyUI custom node that extends the standard text encoder 
with persistent prompt storage and advanced search capabilities using SQLite.
"""

# Import logging system
try:
    from .utils.logging_config import get_logger
    init_logger = get_logger('prompt_manager.init')
    init_logger.info("Starting to load PromptManager custom node...")
except ImportError:
    # Fallback to print if logging isn't available yet
    init_logger = None
    print("[PromptManager] Starting to load PromptManager custom node...")

def log_message(message, level='info'):
    """Helper to log messages with fallback to print."""
    if init_logger:
        getattr(init_logger, level)(message.replace("[PromptManager] ", ""))
    else:
        print(f"[PromptManager] {message}")

try:
    from .prompt_manager import PromptManager
    log_message("Successfully imported PromptManager class")
except Exception as e:
    log_message(f"ERROR: Failed to import PromptManager class: {e}", 'error')
    import traceback
    log_message(f"Traceback: {traceback.format_exc()}", 'error')
    raise

try:
    from .prompt_manager_text import PromptManagerText
    log_message("Successfully imported PromptManagerText class")
except Exception as e:
    log_message(f"ERROR: Failed to import PromptManagerText class: {e}", 'error')
    import traceback
    log_message(f"Traceback: {traceback.format_exc()}", 'error')
    raise

NODE_CLASS_MAPPINGS = {
    "PromptManager": PromptManager,
    "PromptManagerText": PromptManagerText,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptManager": "Prompt Manager",
    "PromptManagerText": "Prompt Manager Text",
}

log_message(f"NODE_CLASS_MAPPINGS: {NODE_CLASS_MAPPINGS}")
log_message(f"NODE_DISPLAY_NAME_MAPPINGS: {NODE_DISPLAY_NAME_MAPPINGS}")

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
    
    log_message("API routes registered successfully")
    log_message(f"Server instance: {config.server_instance}")
    log_message(f"Routes object: {routes}")
    
except Exception as e:
    log_message(f"Failed to register API routes: {e}", 'error')
    import traceback
    log_message(f"Traceback: {traceback.format_exc()}", 'error')

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

log_message("[SUCCESS] PromptManager custom node loaded successfully!")