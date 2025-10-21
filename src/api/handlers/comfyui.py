"""ComfyUI Integration API handlers.

This module provides API endpoints for ComfyUI node registry and prompt sending.
It enables the "Send to ComfyUI" feature that sends prompts from the dashboard
directly to CLIPTextEncode nodes in active ComfyUI workflows.

Communication Flow:
1. Frontend calls GET /api/comfyui/get-registry
2. Backend sends 'prompt_registry_refresh' WebSocket event to ComfyUI
3. ComfyUI extension scans for prompt nodes and calls POST /api/comfyui/register-nodes
4. Backend returns available nodes to frontend
5. Frontend calls POST /api/comfyui/send-prompt with selected node and prompt
6. Backend sends 'prompt_update' WebSocket event to ComfyUI
7. ComfyUI extension updates the node's widget value

Endpoints:
    POST /api/comfyui/register-nodes - Register nodes from ComfyUI extension
    GET /api/comfyui/get-registry - Get available prompt nodes
    POST /api/comfyui/send-prompt - Send prompt to specific node(s)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from aiohttp import web

try:
    from server import PromptServer
except ImportError:
    # Mock for testing
    class MockPromptServer:
        def __init__(self):
            self.instance = self

        def send_sync(self, event, data, sid=None):
            pass

    class MockServer:
        PromptServer = MockPromptServer()

    PromptServer = MockServer.PromptServer

from ...services.comfyui_node_registry import get_node_registry

if TYPE_CHECKING:
    from ..routes import PromptManagerAPI


class ComfyUIHandlers:
    """Handles ComfyUI integration endpoints."""

    def __init__(self, api: PromptManagerAPI):
        """Initialize with API instance for access to repos/services.

        Args:
            api: PromptManagerAPI instance providing access to repositories and services
        """
        self.api = api
        self.logger = api.logger
        self.node_registry = get_node_registry()

    async def register_nodes(self, request: web.Request) -> web.Response:
        """Register nodes from ComfyUI extension.

        POST /api/comfyui/register-nodes
        Body: {
            nodes: [
                {
                    node_id: int,
                    graph_id: str,
                    type: str,
                    title: str,
                    widgets?: list,
                    bgcolor?: str,
                    graph_name?: str,
                    metadata?: dict
                }
            ]
        }

        This endpoint is called by the ComfyUI extension after it scans the
        active workflow for nodes that can receive prompts (CLIPTextEncode,
        PromptManager nodes, etc.).

        Returns:
            JSON response with success status and registered count
        """
        try:
            data = await request.json()
            nodes = data.get("nodes", [])

            if not isinstance(nodes, list):
                return web.json_response(
                    {"success": False, "error": "nodes must be an array"}, status=400
                )

            # Register nodes in the registry
            await self.node_registry.register_nodes(nodes)

            registered_count = len(nodes)
            self.logger.info(
                f"[COMFYUI] Registered {registered_count} prompt nodes from ComfyUI extension"
            )

            return web.json_response(
                {
                    "success": True,
                    "message": f"{registered_count} node(s) registered",
                    "count": registered_count,
                }
            )

        except json.JSONDecodeError:
            return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)
        except Exception as e:
            self.logger.error(f"[COMFYUI] Error registering nodes: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def get_registry(self, request: web.Request) -> web.Response:
        """Get available prompt nodes from ComfyUI.

        GET /api/comfyui/get-registry

        This endpoint sends a WebSocket refresh event to ComfyUI, waits for
        the extension to respond with updated node information, then returns
        the registry of available nodes.

        The workflow is:
        1. Send 'prompt_registry_refresh' WebSocket event
        2. Wait up to 1 second for ComfyUI extension to respond
        3. Extension scans workflow and calls register-nodes endpoint
        4. Return the updated registry to the frontend

        Returns:
            JSON response with registry data:
            {
                success: bool,
                data: {
                    nodes: {
                        "graph_id:node_id": {
                            id: int,
                            graph_id: str,
                            unique_id: str,
                            type: str,
                            title: str,
                            widgets: list,
                            bgcolor?: str,
                            graph_name?: str
                        }
                    },
                    node_count: int
                }
            }
        """
        try:
            self.logger.info("[COMFYUI] Sending prompt_registry_refresh WebSocket event")

            # Send WebSocket event to ComfyUI to trigger node scan
            try:
                PromptServer.instance.send_sync("prompt_registry_refresh", {})
            except Exception as e:
                self.logger.warning(
                    f"[COMFYUI] Failed to send WebSocket event (ComfyUI may not be running): {e}"
                )

            # Wait for the ComfyUI extension to respond with node registrations
            registry_updated = await self.node_registry.wait_for_update(timeout=1.0)

            if not registry_updated:
                self.logger.warning(
                    "[COMFYUI] Registry refresh timeout - ComfyUI may not be running or no compatible nodes found"
                )

            # Get the current registry (may be empty if timeout occurred)
            registry_info = await self.node_registry.get_registry()

            self.logger.info(
                f"[COMFYUI] Returning registry with {registry_info['node_count']} nodes"
            )

            return web.json_response({"success": True, "data": registry_info})

        except Exception as e:
            self.logger.error(f"[COMFYUI] Error getting registry: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def send_prompt(self, request: web.Request) -> web.Response:
        """Send prompt to specific ComfyUI node(s).

        POST /api/comfyui/send-prompt
        Body: {
            prompt_id: int,
            node_ids: list[str],  // Format: ["graph_id:node_id"]
            mode: "append" | "replace"  // Default: "append"
        }

        This endpoint sends prompt text to one or more nodes in ComfyUI.
        It fetches the prompt from the database, then sends WebSocket events
        to update the node widgets in ComfyUI.

        Mode:
        - append: Add prompt to existing text in node (default)
        - replace: Replace entire text in node with new prompt

        Returns:
            JSON response with success status
        """
        try:
            data = await request.json()

            # Validate required fields
            prompt_id = data.get("prompt_id")
            node_ids = data.get("node_ids", [])
            mode = data.get("mode", "append")
            prompt_type = data.get("type", "both")  # 'positive', 'negative', or 'both'

            if not prompt_id:
                return web.json_response(
                    {"success": False, "error": "prompt_id is required"}, status=400
                )

            if not node_ids or not isinstance(node_ids, list):
                return web.json_response(
                    {"success": False, "error": "node_ids must be a non-empty array"},
                    status=400,
                )

            if mode not in ["append", "replace"]:
                return web.json_response(
                    {"success": False, "error": "mode must be 'append' or 'replace'"},
                    status=400,
                )
            
            if prompt_type not in ["positive", "negative", "both"]:
                return web.json_response(
                    {"success": False, "error": "type must be 'positive', 'negative', or 'both'"},
                    status=400,
                )

            # Fetch prompt from database
            try:
                prompt = self.api.prompt_repo.read(prompt_id)
                if not prompt:
                    return web.json_response(
                        {"success": False, "error": f"Prompt {prompt_id} not found"},
                        status=404,
                    )
            except Exception as e:
                self.logger.error(f"[COMFYUI] Error fetching prompt {prompt_id}: {e}")
                return web.json_response(
                    {"success": False, "error": f"Failed to fetch prompt: {str(e)}"},
                    status=500,
                )

            positive_prompt = prompt.get("positive_prompt", "")
            negative_prompt = prompt.get("negative_prompt", "")

            self.logger.info(
                f"[COMFYUI] Sending prompt {prompt_id} to {len(node_ids)} node(s) with mode={mode}, type={prompt_type}"
            )

            # Send WebSocket events for each node
            sent_count = 0
            failed_nodes = []

            for node_id in node_ids:
                try:
                    # Parse unique_id format: "graph_id:node_id"
                    if ":" not in node_id:
                        self.logger.warning(
                            f"[COMFYUI] Invalid node_id format: {node_id} (expected 'graph_id:node_id')"
                        )
                        failed_nodes.append({"node_id": node_id, "error": "Invalid format"})
                        continue

                    graph_id, actual_node_id = node_id.split(":", 1)

                    # Verify node exists in registry
                    node = await self.node_registry.get_node(node_id)
                    if not node:
                        self.logger.warning(f"[COMFYUI] Node {node_id} not found in registry")
                        failed_nodes.append({"node_id": node_id, "error": "Not in registry"})
                        continue

                    # Send positive prompt if requested
                    if positive_prompt and prompt_type in ["positive", "both"]:
                        event_data = {
                            "graph_id": graph_id,
                            "node_id": int(actual_node_id),
                            "prompt_text": positive_prompt,
                            "type": "positive",
                            "mode": mode,
                            "prompt_id": prompt_id,
                        }

                        try:
                            PromptServer.instance.send_sync("prompt_update", event_data)
                            self.logger.info(
                                f"[COMFYUI] Sent positive prompt to node {node_id}"
                            )
                        except Exception as ws_error:
                            self.logger.error(
                                f"[COMFYUI] WebSocket error sending to {node_id}: {ws_error}"
                            )
                            failed_nodes.append(
                                {"node_id": node_id, "error": f"WebSocket: {str(ws_error)}"}
                            )
                            continue

                    # Send negative prompt if requested
                    # All tracked PromptManager nodes and CLIPTextEncode can handle negative prompts
                    if negative_prompt and prompt_type in ["negative", "both"] and node.node_type in [
                        "CLIPTextEncode",
                        "PromptManager",
                        "PromptManagerV2",
                        "PromptManagerNegative",
                        "PromptManagerText",
                        "PromptManagerV2Text",
                        "PromptManagerNegativeText",
                    ]:
                        event_data = {
                            "graph_id": graph_id,
                            "node_id": int(actual_node_id),
                            "prompt_text": negative_prompt,
                            "type": "negative",
                            "mode": mode,
                            "prompt_id": prompt_id,
                        }

                        try:
                            PromptServer.instance.send_sync("prompt_update", event_data)
                            self.logger.info(
                                f"[COMFYUI] Sent negative prompt to node {node_id}"
                            )
                        except Exception as ws_error:
                            self.logger.error(
                                f"[COMFYUI] WebSocket error sending negative to {node_id}: {ws_error}"
                            )

                    sent_count += 1

                except ValueError as e:
                    self.logger.error(f"[COMFYUI] Error parsing node_id {node_id}: {e}")
                    failed_nodes.append({"node_id": node_id, "error": str(e)})
                except Exception as e:
                    self.logger.error(f"[COMFYUI] Error sending to node {node_id}: {e}")
                    failed_nodes.append({"node_id": node_id, "error": str(e)})

            response_data = {
                "success": sent_count > 0,
                "message": f"Sent prompt to {sent_count} node(s)",
                "sent_count": sent_count,
                "total_nodes": len(node_ids),
            }

            if failed_nodes:
                response_data["failed_nodes"] = failed_nodes
                self.logger.warning(
                    f"[COMFYUI] Failed to send to {len(failed_nodes)} node(s)"
                )

            status_code = 200 if sent_count > 0 else 500
            return web.json_response(response_data, status=status_code)

        except json.JSONDecodeError:
            return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)
        except Exception as e:
            self.logger.error(f"[COMFYUI] Error in send_prompt: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)