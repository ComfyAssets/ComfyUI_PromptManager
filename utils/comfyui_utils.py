"""Utilities for ComfyUI integration.

This module provides helper functions for integrating with ComfyUI's server instance.
"""

from typing import Optional, Tuple

# Import PromptServer at module level for mocking support in tests
try:
    from server import PromptServer
except ImportError:
    # Running outside ComfyUI context
    PromptServer = None  # type: ignore


def get_comfyui_server_url() -> str:
    """Get the ComfyUI server URL from PromptServer instance.

    This respects the --listen and --port arguments passed to ComfyUI.

    Returns:
        The server URL (e.g., "http://localhost:8188" or "http://0.0.0.0:6006")
        Falls back to "http://127.0.0.1:8188" if PromptServer is not available.
    """
    try:
        if PromptServer and PromptServer.instance is not None:
            # Get actual address and port from running server
            address = getattr(PromptServer.instance, 'address', '127.0.0.1')
            port = getattr(PromptServer.instance, 'port', 8188)

            # Determine if using TLS
            use_tls = getattr(PromptServer.instance, 'ssl_context', None) is not None
            scheme = 'https' if use_tls else 'http'

            # Format for display - use localhost for loopback addresses for better UX
            if address in ('0.0.0.0', '::', '127.0.0.1', '::1', 'localhost'):
                display_address = 'localhost'
            else:
                display_address = address

            return f"{scheme}://{display_address}:{port}"
    except AttributeError:
        # PromptServer not initialized yet
        pass

    # Fallback to default
    return "http://127.0.0.1:8188"


def get_comfyui_address_and_port() -> Tuple[str, int]:
    """Get the ComfyUI server address and port from PromptServer instance.

    Returns:
        Tuple of (address, port)
        Falls back to ("127.0.0.1", 8188) if PromptServer is not available.
    """
    try:
        if PromptServer and PromptServer.instance is not None:
            address = getattr(PromptServer.instance, 'address', '127.0.0.1')
            port = getattr(PromptServer.instance, 'port', 8188)
            return (address, port)
    except AttributeError:
        pass

    return ("127.0.0.1", 8188)


def format_server_address(address: str, port: int) -> str:
    """Format server address and port for configuration.

    Args:
        address: Server IP address
        port: Server port number

    Returns:
        Formatted address:port string
    """
    return f"{address}:{port}"
