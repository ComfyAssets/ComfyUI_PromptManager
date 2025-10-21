"""Utilities for ComfyUI integration.

This module provides helper functions for integrating with ComfyUI's server instance.
"""

import socket
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
    When the server binds to 0.0.0.0 (all interfaces), it uses the actual
    hostname instead of "localhost" to accurately reflect network accessibility.

    Returns:
        The server URL (e.g., "http://localhost:8188" or "http://myserver:6006")
        Falls back to "http://127.0.0.1:8188" if PromptServer is not available.
    """
    try:
        if PromptServer and PromptServer.instance is not None:
            import sys

            # Default values
            address = '127.0.0.1'
            port = 8188

            # Try to parse from sys.argv (ComfyUI command-line arguments)
            if hasattr(sys, 'argv'):
                argv = sys.argv

                # Look for --listen and --port arguments
                for i, arg in enumerate(argv):
                    if arg == '--listen':
                        # Check if next argument is an IP/hostname (not another flag)
                        if i + 1 < len(argv) and not argv[i + 1].startswith('-'):
                            address = argv[i + 1]
                        else:
                            # --listen without IP means listen on all interfaces
                            address = '0.0.0.0'
                    elif arg == '--port' and i + 1 < len(argv):
                        try:
                            port = int(argv[i + 1])
                        except ValueError:
                            pass

            # Determine if using TLS
            use_tls = getattr(PromptServer.instance, 'ssl_context', None) is not None
            scheme = 'https' if use_tls else 'http'

            # Format for display
            # - For 0.0.0.0/:: (all interfaces): use actual hostname (not "localhost")
            # - For 127.0.0.1/::1/localhost: use "localhost"
            # - For specific IPs: use that IP
            if address in ('0.0.0.0', '::'):
                # Server listening on all interfaces - show actual hostname
                try:
                    display_address = socket.gethostname()
                except Exception:
                    display_address = 'localhost'  # Fallback if hostname detection fails
            elif address in ('127.0.0.1', '::1', 'localhost'):
                # Actual loopback address
                display_address = 'localhost'
            else:
                # Specific IP or hostname
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
