"""Configuration module for ComfyUI PromptManager."""

# PromptManager/py/config.py

# Extension configuration
extension_name = "PromptManager"

# Get server instance and routes (same pattern as ComfyUI_Assets)
from server import PromptServer
server_instance = PromptServer.instance
routes = server_instance.routes

# Extension info
extension_uri = None  # Will be set in __init__.py