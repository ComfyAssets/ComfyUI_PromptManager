"""ComfyUI integration module.

This module provides integration between PromptManager and ComfyUI,
enabling automatic tracking of prompts and generated images.
"""

import json
import asyncio
import websockets
from typing import Any, Dict, Optional, List, Callable
from dataclasses import dataclass
import hashlib
from pathlib import Path

from src.config import config
from src.services.prompt_service import PromptService
from src.services.image_service import ImageService
from src.repositories.prompt_repository import PromptRepository
from src.repositories.image_repository import ImageRepository
from src.tracking.graph_analyzer import GraphAnalyzer
from src.tracking.workflow_tracker import WorkflowTracker
try:  # pragma: no cover - metadata import path differs in tests
    from promptmanager.utils.metadata_extractor import MetadataExtractor  # type: ignore
except ImportError:  # pragma: no cover
    from utils.metadata_extractor import MetadataExtractor  # type: ignore

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore
from src.database import db

logger = get_logger("promptmanager.comfyui")


@dataclass
class ComfyUIMessage:
    """ComfyUI WebSocket message structure."""
    
    type: str
    data: Dict[str, Any]
    client_id: Optional[str] = None


class ComfyUIClient:
    """Client for ComfyUI WebSocket communication."""
    
    def __init__(self, server_address: Optional[str] = None):
        """Initialize ComfyUI client.
        
        Args:
            server_address: ComfyUI server address
        """
        self.server_address = server_address or config.comfyui.server_address
        self.client_id = config.comfyui.client_id
        self.websocket = None
        self.running = False
        
        # Services for data management
        prompt_repo = PromptRepository(db.db_path)
        image_repo = ImageRepository(db.db_path)
        self.prompt_service = PromptService(prompt_repo)
        self.image_service = ImageService(image_repo)
        
        # Tracking components
        self.graph_analyzer = GraphAnalyzer()
        self.workflow_tracker = WorkflowTracker(
            prompt_service=self.prompt_service,
            image_service=self.image_service
        )
        
        # Message handlers
        self.handlers = {
            "execution_start": self.handle_execution_start,
            "execution_cached": self.handle_execution_cached,
            "executing": self.handle_executing,
            "progress": self.handle_progress,
            "executed": self.handle_executed,
            "execution_error": self.handle_execution_error,
            "execution_complete": self.handle_execution_complete
        }
        
        # Current execution state
        self.current_execution = {}
        self.current_workflow = None
        self.tracked_prompts = {}
        self.tracked_images = {}
    
    async def connect(self):
        """Connect to ComfyUI WebSocket server."""
        ws_url = f"ws://{self.server_address}/ws?clientId={self.client_id}"
        
        try:
            self.websocket = await websockets.connect(ws_url)
            self.running = True
            logger.info(f"Connected to ComfyUI at {ws_url}")
            
            # Start message handler
            await self.handle_messages()
            
        except Exception as e:
            logger.error(f"Failed to connect to ComfyUI: {e}")
            raise
    
    async def disconnect(self):
        """Disconnect from ComfyUI server."""
        self.running = False
        
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
            
        logger.info("Disconnected from ComfyUI")
    
    async def handle_messages(self):
        """Handle incoming WebSocket messages."""
        while self.running:
            try:
                message = await self.websocket.recv()
                await self.process_message(message)
                
            except websockets.ConnectionClosed:
                logger.warning("ComfyUI connection closed")
                break
                
            except Exception as e:
                logger.error(f"Error handling message: {e}")
    
    async def process_message(self, message: str):
        """Process incoming WebSocket message.
        
        Args:
            message: Raw message string
        """
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            
            if msg_type in self.handlers:
                handler = self.handlers[msg_type]
                await handler(data.get("data", {}))
            else:
                logger.debug(f"Unhandled message type: {msg_type}")
                
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON message: {e}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    async def handle_execution_start(self, data: Dict[str, Any]):
        """Handle execution start event.
        
        Args:
            data: Event data
        """
        prompt_id = data.get("prompt_id")
        
        if not prompt_id:
            return
        
        logger.info(f"Execution started: {prompt_id}")
        
        # Reset tracking for new execution
        self.current_execution[prompt_id] = {
            "status": "started",
            "nodes": {},
            "prompts": {},
            "images": []
        }
        
        # Get workflow if available
        if "workflow" in data:
            self.current_workflow = data["workflow"]
            
            # Analyze workflow graph
            analysis = self.graph_analyzer.analyze_workflow(self.current_workflow)
            self.current_execution[prompt_id]["analysis"] = analysis
            
            # Track workflow start
            self.workflow_tracker.track_execution_start(
                prompt_id,
                self.current_workflow
            )
    
    async def handle_executing(self, data: Dict[str, Any]):
        """Handle node execution event.
        
        Args:
            data: Event data
        """
        node_id = data.get("node")
        prompt_id = data.get("prompt_id")
        
        if not node_id or not prompt_id:
            return
        
        logger.debug(f"Executing node {node_id} for prompt {prompt_id}")
        
        if prompt_id in self.current_execution:
            self.current_execution[prompt_id]["nodes"][node_id] = {
                "status": "executing",
                "type": data.get("node_type"),
                "started_at": data.get("timestamp")
            }
            
            # Track node execution
            self.workflow_tracker.track_node_execution(
                prompt_id,
                node_id,
                data
            )
    
    async def handle_executed(self, data: Dict[str, Any]):
        """Handle node executed event.
        
        Args:
            data: Event data
        """
        node_id = data.get("node")
        prompt_id = data.get("prompt_id")
        output = data.get("output", {})
        
        if not node_id or not prompt_id:
            return
        
        logger.debug(f"Executed node {node_id} for prompt {prompt_id}")
        
        if prompt_id in self.current_execution:
            self.current_execution[prompt_id]["nodes"][node_id]["status"] = "completed"
            self.current_execution[prompt_id]["nodes"][node_id]["output"] = output
            
            # Process node output
            await self.process_node_output(prompt_id, node_id, output)
    
    async def handle_progress(self, data: Dict[str, Any]):
        """Handle progress update event.
        
        Args:
            data: Event data
        """
        node_id = data.get("node")
        prompt_id = data.get("prompt_id")
        value = data.get("value", 0)
        max_value = data.get("max", 100)
        
        logger.debug(f"Progress for node {node_id}: {value}/{max_value}")
        
        # Broadcast progress to PromptManager clients
        from src.api.websocket import broadcast_batch_progress
        
        await broadcast_batch_progress(
            prompt_id,
            value,
            max_value,
            f"Processing node {node_id}"
        )
    
    async def handle_execution_error(self, data: Dict[str, Any]):
        """Handle execution error event.
        
        Args:
            data: Event data
        """
        prompt_id = data.get("prompt_id")
        node_id = data.get("node")
        error = data.get("error")
        
        logger.error(f"Execution error in node {node_id}: {error}")
        
        if prompt_id in self.current_execution:
            self.current_execution[prompt_id]["status"] = "error"
            self.current_execution[prompt_id]["error"] = {
                "node": node_id,
                "message": error
            }
    
    async def handle_execution_cached(self, data: Dict[str, Any]):
        """Handle cached execution event.
        
        Args:
            data: Event data
        """
        nodes = data.get("nodes", [])
        prompt_id = data.get("prompt_id")
        
        logger.debug(f"Cached nodes for {prompt_id}: {nodes}")
        
        if prompt_id in self.current_execution:
            for node_id in nodes:
                self.current_execution[prompt_id]["nodes"][node_id] = {
                    "status": "cached",
                    "cached": True
                }
    
    async def handle_execution_complete(self, data: Dict[str, Any]):
        """Handle execution complete event.
        
        Args:
            data: Event data
        """
        prompt_id = data.get("prompt_id")
        
        if not prompt_id:
            return
        
        logger.info(f"Execution completed: {prompt_id}")
        
        if prompt_id in self.current_execution:
            self.current_execution[prompt_id]["status"] = "completed"
            
            # Process completed execution
            await self.process_completed_execution(prompt_id)
            
            # Clean up
            del self.current_execution[prompt_id]
    
    async def process_node_output(self, prompt_id: str, node_id: str, 
                                 output: Dict[str, Any]):
        """Process node output for tracking.
        
        Args:
            prompt_id: Prompt/execution ID
            node_id: Node ID
            output: Node output data
        """
        # Check for text prompts
        if "text" in output:
            text = output["text"]
            if isinstance(text, list) and text:
                text = text[0]
            
            # Track prompt text
            if text and len(text) > 10:  # Minimum length for valid prompt
                await self.track_prompt(prompt_id, node_id, text)
        
        # Check for images
        if "images" in output:
            images = output["images"]
            if isinstance(images, list):
                for img_data in images:
                    await self.track_image(prompt_id, node_id, img_data)
        
        # Check for UI elements (contains images)
        if "ui" in output:
            ui_data = output["ui"]
            if "images" in ui_data:
                for img_info in ui_data["images"]:
                    await self.track_image(prompt_id, node_id, img_info)
    
    async def track_prompt(self, prompt_id: str, node_id: str, text: str):
        """Track prompt text.
        
        Args:
            prompt_id: Prompt/execution ID
            node_id: Node ID
            text: Prompt text
        """
        # Check if it's a positive or negative prompt
        is_negative = "negative" in node_id.lower() or "neg" in node_id.lower()
        
        if is_negative:
            # Store as negative prompt
            if prompt_id not in self.tracked_prompts:
                self.tracked_prompts[prompt_id] = {}
            self.tracked_prompts[prompt_id]["negative_prompt"] = text
        else:
            # Store as positive prompt
            if prompt_id not in self.tracked_prompts:
                self.tracked_prompts[prompt_id] = {}
            self.tracked_prompts[prompt_id]["prompt"] = text
        
        logger.debug(f"Tracked {'negative' if is_negative else 'positive'} prompt from node {node_id}")
    
    async def track_image(self, prompt_id: str, node_id: str, img_data: Any):
        """Track generated image.
        
        Args:
            prompt_id: Prompt/execution ID
            node_id: Node ID
            img_data: Image data
        """
        # Extract image path
        if isinstance(img_data, dict):
            filename = img_data.get("filename")
            subfolder = img_data.get("subfolder", "")
            type_folder = img_data.get("type", "output")
        elif isinstance(img_data, str):
            filename = img_data
            subfolder = ""
            type_folder = "output"
        else:
            return
        
        if not filename:
            return
        
        # Build full path
        comfyui_output = Path("output") / type_folder
        if subfolder:
            comfyui_output = comfyui_output / subfolder
        image_path = comfyui_output / filename
        
        # Track image
        if prompt_id not in self.tracked_images:
            self.tracked_images[prompt_id] = []
        
        self.tracked_images[prompt_id].append({
            "node_id": node_id,
            "path": str(image_path),
            "filename": filename
        })
        
        logger.debug(f"Tracked image from node {node_id}: {filename}")
    
    async def process_completed_execution(self, prompt_id: str):
        """Process completed execution for storage.
        
        Args:
            prompt_id: Prompt/execution ID
        """
        if prompt_id not in self.tracked_prompts and prompt_id not in self.tracked_images:
            return
        
        # Get prompt data
        prompt_data = self.tracked_prompts.get(prompt_id, {})
        prompt_text = prompt_data.get("prompt", "")
        negative_prompt = prompt_data.get("negative_prompt", "")
        
        # Create or get prompt record
        if prompt_text:
            prompt_record = self.prompt_service.create_or_get({
                "prompt": prompt_text,
                "negative_prompt": negative_prompt,
                "category": "comfyui"
            })
            prompt_record_id = prompt_record["id"]
        else:
            prompt_record_id = None
        
        # Process tracked images
        for img_info in self.tracked_images.get(prompt_id, []):
            image_path = img_info["path"]
            
            if not Path(image_path).exists():
                continue
            
            # Extract metadata from image
            metadata = MetadataExtractor.extract_from_file(image_path)
            gen_info = MetadataExtractor.extract_generation_info(image_path)
            
            # Create image record
            image_data = {
                "file_path": image_path,
                "width": metadata.get("width", 0),
                "height": metadata.get("height", 0),
                "prompt_text": prompt_text,
                "negative_prompt": negative_prompt
            }
            
            # Add generation info if available
            if gen_info:
                image_data.update({
                    "checkpoint": gen_info.get("checkpoint"),
                    "seed": gen_info.get("seed"),
                    "steps": gen_info.get("steps"),
                    "cfg_scale": gen_info.get("cfg_scale"),
                    "sampler": gen_info.get("sampler")
                })
            
            # Create image record
            image_record = self.image_service.create(image_data)
            
            # Link prompt and image if both exist
            if prompt_record_id and image_record:
                self.workflow_tracker.link_prompt_image(
                    prompt_record_id,
                    image_record["id"]
                )
        
        # Clean up tracking data
        if prompt_id in self.tracked_prompts:
            del self.tracked_prompts[prompt_id]
        if prompt_id in self.tracked_images:
            del self.tracked_images[prompt_id]
        
        logger.info(f"Processed execution {prompt_id}: stored prompt and images")
    
    async def get_workflow(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        """Get workflow for a prompt ID.
        
        Args:
            prompt_id: Prompt ID
            
        Returns:
            Workflow data or None
        """
        # This would connect to ComfyUI API to get workflow
        # For now, return cached workflow
        return self.current_workflow
    
    async def execute_workflow(self, workflow: Dict[str, Any]) -> str:
        """Execute a workflow in ComfyUI.
        
        Args:
            workflow: Workflow definition
            
        Returns:
            Prompt ID for tracking
        """
        # This would send workflow to ComfyUI for execution
        # Implementation depends on ComfyUI API
        pass


class ComfyUIMonitor:
    """Monitor for automatic ComfyUI integration."""
    
    def __init__(self):
        """Initialize ComfyUI monitor."""
        self.client = ComfyUIClient()
        self.running = False
    
    async def start(self):
        """Start monitoring ComfyUI."""
        if not config.comfyui.enabled:
            logger.info("ComfyUI integration disabled")
            return
        
        self.running = True
        
        while self.running:
            try:
                # Connect to ComfyUI
                await self.client.connect()
                
            except Exception as e:
                logger.error(f"ComfyUI connection error: {e}")
                
                # Retry after delay
                await asyncio.sleep(5)
    
    async def stop(self):
        """Stop monitoring ComfyUI."""
        self.running = False
        await self.client.disconnect()


# Global monitor instance
monitor = ComfyUIMonitor()


def start_comfyui_monitor():
    """Start ComfyUI monitor in background."""
    if not config.comfyui.enabled:
        return
    
    async def run_monitor():
        """Run monitor async."""
        await monitor.start()
    
    # Create task for monitor
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(run_monitor())
    except Exception as e:
        logger.error(f"Monitor error: {e}")
    finally:
        loop.close()
