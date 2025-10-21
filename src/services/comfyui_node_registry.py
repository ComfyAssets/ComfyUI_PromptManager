"""ComfyUI Node Registry Service.

This service tracks ComfyUI nodes that can receive prompts from PromptManager.
It maintains a thread-safe registry of nodes capable of accepting text prompts
(primarily CLIPTextEncode nodes for positive/negative prompts).

The registry is updated via WebSocket communication:
1. PromptManager sends 'prompt_registry_refresh' event to ComfyUI
2. ComfyUI extension scans for compatible nodes
3. Extension calls POST /prompt_manager/api/register-nodes
4. Registry stores node information for targeting

Typical node types tracked:
- CLIPTextEncode (positive/negative prompts)
- Custom text input nodes
- Any node with text widget inputs

Thread Safety:
- All operations use asyncio.Lock for thread-safe access
- Event-based notification system for registry updates
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger
except ImportError:  # pragma: no cover
    from loggers import get_logger


logger = get_logger('promptmanager.services.comfyui_node_registry')


class NodeType(Enum):
    """Supported node types for prompt injection."""

    CLIP_TEXT_ENCODE = "CLIPTextEncode"
    PROMPT_MANAGER_POSITIVE = "PromptManager_Positive"
    PROMPT_MANAGER_NEGATIVE = "PromptManager_Negative"
    CUSTOM_TEXT = "CustomText"
    UNKNOWN = "unknown"


@dataclass
class PromptNode:
    """Information about a node that can receive prompts."""

    node_id: int
    graph_id: str
    unique_id: str  # Format: "graph_id:node_id"
    node_type: str
    title: str
    widgets: List[str] = field(default_factory=list)
    bgcolor: Optional[str] = None
    graph_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            'id': self.node_id,
            'graph_id': self.graph_id,
            'unique_id': self.unique_id,
            'type': self.node_type,
            'title': self.title,
            'widgets': self.widgets,
            'bgcolor': self.bgcolor,
            'graph_name': self.graph_name,
            'metadata': self.metadata
        }


class ComfyUINodeRegistry:
    """Thread-safe registry for tracking prompt-compatible nodes in ComfyUI workflows.

    This registry maintains a real-time view of nodes in the active ComfyUI workflow
    that can receive prompt text. It coordinates with ComfyUI's frontend extension
    to discover and track these nodes.

    Usage:
        registry = ComfyUINodeRegistry()

        # Register nodes (called by ComfyUI extension)
        await registry.register_nodes([
            {
                'node_id': 5,
                'graph_id': 'root',
                'type': 'CLIPTextEncode',
                'title': 'Positive Prompt',
                'widgets': ['text']
            }
        ])

        # Get available nodes (called by API)
        nodes_info = await registry.get_registry()
        print(f"Found {nodes_info['node_count']} prompt nodes")

        # Wait for registry update
        updated = await registry.wait_for_update(timeout=2.0)
    """

    def __init__(self):
        """Initialize the node registry."""
        self._lock = asyncio.Lock()
        self._nodes: Dict[str, PromptNode] = {}
        self._registry_updated = asyncio.Event()
        self.logger = logger

    async def register_nodes(self, nodes: List[Dict[str, Any]]) -> None:
        """Register nodes from ComfyUI extension.

        This method is called when the ComfyUI frontend extension responds
        to a registry refresh request. It clears the existing registry and
        populates it with the current workflow's prompt-compatible nodes.

        Args:
            nodes: List of node dictionaries from ComfyUI extension
                Each dict should contain:
                - node_id: int - The node's ID
                - graph_id: str - The graph ID (usually 'root')
                - type: str - Node class type (e.g., 'CLIPTextEncode')
                - title: str - Node title
                - widgets: List[str] - Widget names (optional)
                - bgcolor: str - Background color (optional)
                - graph_name: str - Graph name (optional)

        Example:
            await registry.register_nodes([
                {
                    'node_id': 5,
                    'graph_id': 'root',
                    'type': 'CLIPTextEncode',
                    'title': 'Positive Prompt',
                    'widgets': ['text']
                }
            ])
        """
        async with self._lock:
            # Clear existing registry
            self._nodes.clear()

            # Register each node
            for node_data in nodes:
                try:
                    node_id = int(node_data['node_id'])
                    graph_id = str(node_data['graph_id'])
                    unique_id = f"{graph_id}:{node_id}"

                    prompt_node = PromptNode(
                        node_id=node_id,
                        graph_id=graph_id,
                        unique_id=unique_id,
                        node_type=node_data.get('type', 'unknown'),
                        title=node_data.get('title', f'Node {node_id}'),
                        widgets=node_data.get('widgets', []),
                        bgcolor=node_data.get('bgcolor'),
                        graph_name=node_data.get('graph_name'),
                        metadata=node_data.get('metadata', {})
                    )

                    self._nodes[unique_id] = prompt_node

                except (KeyError, ValueError, TypeError) as e:
                    self.logger.warning(f"Failed to register node {node_data}: {e}")
                    continue

            self.logger.info(f"Registered {len(self._nodes)} prompt nodes")

            # Signal that registry was updated
            self._registry_updated.set()

    async def get_registry(self) -> Dict[str, Any]:
        """Get current registry information.

        Returns a snapshot of all registered prompt nodes. This is typically
        called by the API to determine which nodes are available for sending
        prompts to.

        Returns:
            Dictionary containing:
            - nodes: Dict[str, Dict] - Nodes keyed by unique_id
            - node_count: int - Number of registered nodes

        Example:
            {
                'nodes': {
                    'root:5': {
                        'id': 5,
                        'graph_id': 'root',
                        'type': 'CLIPTextEncode',
                        'title': 'Positive Prompt',
                        'widgets': ['text']
                    }
                },
                'node_count': 1
            }
        """
        async with self._lock:
            nodes_dict = {
                unique_id: node.to_dict()
                for unique_id, node in self._nodes.items()
            }

            return {
                'nodes': nodes_dict,
                'node_count': len(self._nodes)
            }

    async def get_node(self, unique_id: str) -> Optional[PromptNode]:
        """Get a specific node by unique ID.

        Args:
            unique_id: Unique node identifier (format: "graph_id:node_id")

        Returns:
            PromptNode if found, None otherwise
        """
        async with self._lock:
            return self._nodes.get(unique_id)

    async def wait_for_update(self, timeout: float = 1.0) -> bool:
        """Wait for registry to be updated.

        This is used when requesting a registry refresh. After sending the
        refresh request to ComfyUI, we wait for the extension to respond
        with updated node information.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if registry was updated within timeout, False otherwise

        Example:
            # Send refresh request
            await send_refresh_event()

            # Wait for response
            if await registry.wait_for_update(timeout=2.0):
                nodes = await registry.get_registry()
            else:
                # Timeout - ComfyUI may not be responsive
                raise TimeoutError("Registry refresh timeout")
        """
        # Clear the event before waiting
        self._registry_updated.clear()

        try:
            await asyncio.wait_for(
                self._registry_updated.wait(),
                timeout=timeout
            )
            return True
        except asyncio.TimeoutError:
            self.logger.warning(
                f"Registry update timeout after {timeout}s"
            )
            return False

    async def clear(self) -> None:
        """Clear all registered nodes.

        This is typically called when ComfyUI is disconnected or when
        starting a fresh registration process.
        """
        async with self._lock:
            self._nodes.clear()
            self.logger.info("Registry cleared")

    async def get_node_count(self) -> int:
        """Get number of registered nodes.

        Returns:
            Number of nodes currently in registry
        """
        async with self._lock:
            return len(self._nodes)

    async def has_nodes(self) -> bool:
        """Check if any nodes are registered.

        Returns:
            True if at least one node is registered, False otherwise
        """
        async with self._lock:
            return len(self._nodes) > 0


# Global registry instance
_registry: Optional[ComfyUINodeRegistry] = None


def get_node_registry() -> ComfyUINodeRegistry:
    """Get or create the global node registry instance.

    Returns:
        ComfyUINodeRegistry instance
    """
    global _registry
    if _registry is None:
        _registry = ComfyUINodeRegistry()
    return _registry


async def initialize_node_registry() -> ComfyUINodeRegistry:
    """Initialize the global node registry.

    This is called during application startup to ensure the registry
    is ready to receive node registrations.

    Returns:
        Initialized ComfyUINodeRegistry instance
    """
    registry = get_node_registry()
    logger.info("ComfyUI Node Registry initialized")
    return registry
