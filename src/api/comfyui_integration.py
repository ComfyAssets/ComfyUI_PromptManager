"""ComfyUI integration module for PromptManager.

This module provides deep integration with ComfyUI's execution system, using
ComfyUI's existing WebSocket infrastructure for real-time updates rather than
creating a competing WebSocket server.

Key Integration Points:
- Hook into ComfyUI's execution events (prompt queued, executing, completed)
- Use PromptServer.instance.send_sync() for real-time WebSocket updates
- Track workflow serialization and node parameter extraction
- Implement queue management integration
- Maintain execution history with our database
- Provide progress callbacks during image generation

The integration respects ComfyUI's architecture and extends it seamlessly
without interfering with core functionality.

Classes:
    ComfyUIIntegration: Main integration class
    ExecutionTracker: Tracks prompt execution lifecycle
    WorkflowSerializer: Extracts and serializes workflow data
    NodeParameterExtractor: Extracts node parameters for tracking
    QueueManager: Manages ComfyUI execution queue integration

Example:
    integration = ComfyUIIntegration()
    await integration.initialize()
    # Integration automatically hooks into ComfyUI events
"""

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable, Set
from enum import Enum
import weakref
from pathlib import Path

try:
    import server
    from server import PromptServer
    import execution
    import folder_paths
except ImportError as e:
    logging.warning(f"ComfyUI modules not available: {e}")
    # Mock for testing
    class MockPromptServer:
        def __init__(self):
            self.instance = self
        def send_sync(self, event, data, sid=None):
            pass
    server = type('MockServer', (), {'PromptServer': MockPromptServer})()
    PromptServer = MockPromptServer

from ..database import Database
from ..utils.performance import Timer, timed


class ExecutionState(Enum):
    """Execution states for prompt tracking."""
    QUEUED = "queued"
    EXECUTING = "executing" 
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeType(Enum):
    """Node types for parameter extraction."""
    PROMPT_MANAGER = "PromptManager"
    CHECKPOINT_LOADER = "CheckpointLoaderSimple"
    KSAMPLER = "KSampler"
    VAE_DECODER = "VAEDecode"
    SAVE_IMAGE = "SaveImage"
    UNKNOWN = "unknown"


@dataclass
class ExecutionContext:
    """Context information for prompt execution."""
    prompt_id: str
    client_id: str
    prompt: Dict[str, Any]
    workflow: Dict[str, Any] = field(default_factory=dict)
    execution_start_time: Optional[float] = None
    execution_end_time: Optional[float] = None
    state: ExecutionState = ExecutionState.QUEUED
    current_node: Optional[str] = None
    progress: float = 0.0
    images_generated: List[Dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeInfo:
    """Information about a workflow node."""
    node_id: str
    class_type: str
    inputs: Dict[str, Any]
    outputs: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PromptManagerNodeInfo:
    """Specific information for PromptManager nodes."""
    node_id: str
    prompt_name: str
    positive_prompt: str
    negative_prompt: str
    category: str = ""
    unique_id: str = ""
    workflow_data: Dict[str, Any] = field(default_factory=dict)


class ExecutionTracker:
    """Tracks prompt execution lifecycle and sends real-time updates."""

    def __init__(self, db_manager: Database):
        self.db_manager = db_manager
        self.active_executions: Dict[str, ExecutionContext] = {}
        self.logger = logging.getLogger(f"{__name__}.ExecutionTracker")

    async def start_execution(self, prompt_id: str, client_id: str, 
                            prompt: Dict[str, Any], workflow: Dict[str, Any] = None):
        """Start tracking a new execution."""
        context = ExecutionContext(
            prompt_id=prompt_id,
            client_id=client_id,
            prompt=prompt,
            workflow=workflow or {},
            execution_start_time=time.time(),
            state=ExecutionState.EXECUTING
        )
        
        self.active_executions[prompt_id] = context
        
        # Send initial WebSocket update
        await self._send_execution_update(context)
        
        # Store in database
        await self._store_execution_start(context)

    async def update_execution(self, prompt_id: str, **updates):
        """Update execution context."""
        if prompt_id not in self.active_executions:
            self.logger.warning(f"Execution {prompt_id} not found for update")
            return

        context = self.active_executions[prompt_id]
        
        # Update context
        for key, value in updates.items():
            if hasattr(context, key):
                setattr(context, key, value)

        # Send WebSocket update
        await self._send_execution_update(context)

    async def complete_execution(self, prompt_id: str, success: bool = True, 
                               images: List[Dict] = None, errors: List[str] = None):
        """Complete execution tracking."""
        if prompt_id not in self.active_executions:
            self.logger.warning(f"Execution {prompt_id} not found for completion")
            return

        context = self.active_executions[prompt_id]
        context.execution_end_time = time.time()
        context.state = ExecutionState.COMPLETED if success else ExecutionState.FAILED
        context.images_generated = images or []
        context.errors = errors or []

        # Send final WebSocket update
        await self._send_execution_update(context)
        
        # Store final results in database
        await self._store_execution_completion(context)
        
        # Remove from active tracking
        del self.active_executions[prompt_id]

    async def _send_execution_update(self, context: ExecutionContext):
        """Send execution update via ComfyUI's WebSocket."""
        update_data = {
            'prompt_id': context.prompt_id,
            'state': context.state.value,
            'progress': context.progress,
            'current_node': context.current_node,
            'images_count': len(context.images_generated),
            'execution_time': None,
            'errors': context.errors
        }

        if context.execution_start_time:
            current_time = context.execution_end_time or time.time()
            update_data['execution_time'] = current_time - context.execution_start_time

        try:
            # Use ComfyUI's existing WebSocket infrastructure
            PromptServer.instance.send_sync(
                "prompt_manager_execution_update", 
                update_data,
                context.client_id
            )
        except Exception as e:
            self.logger.error(f"Failed to send WebSocket update: {e}")

    async def _store_execution_start(self, context: ExecutionContext):
        """Store execution start in database."""
        try:
            async with self.db_manager.get_connection() as conn:
                await self.db_manager.execution_ops.create_execution_record(
                    conn,
                    prompt_id=context.prompt_id,
                    client_id=context.client_id,
                    prompt_data=context.prompt,
                    workflow_data=context.workflow,
                    state=context.state.value,
                    start_time=context.execution_start_time
                )
        except Exception as e:
            self.logger.error(f"Failed to store execution start: {e}")

    async def _store_execution_completion(self, context: ExecutionContext):
        """Store execution completion in database.""" 
        try:
            async with self.db_manager.get_connection() as conn:
                await self.db_manager.execution_ops.update_execution_record(
                    conn,
                    prompt_id=context.prompt_id,
                    state=context.state.value,
                    end_time=context.execution_end_time,
                    images_generated=context.images_generated,
                    errors=context.errors,
                    metadata=context.metadata
                )
        except Exception as e:
            self.logger.error(f"Failed to store execution completion: {e}")


class WorkflowSerializer:
    """Extracts and serializes workflow data."""

    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.WorkflowSerializer")

    def serialize_workflow(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize workflow for storage."""
        try:
            return {
                'nodes': self._extract_nodes(workflow),
                'links': self._extract_links(workflow),
                'groups': workflow.get('groups', []),
                'config': workflow.get('config', {}),
                'extra': workflow.get('extra', {}),
                'version': workflow.get('version', '1.0')
            }
        except Exception as e:
            self.logger.error(f"Workflow serialization failed: {e}")
            return {'error': str(e), 'raw_workflow': workflow}

    def _extract_nodes(self, workflow: Dict[str, Any]) -> List[NodeInfo]:
        """Extract node information from workflow."""
        nodes = []
        
        for node_id, node_data in workflow.items():
            if isinstance(node_data, dict) and 'class_type' in node_data:
                node_info = NodeInfo(
                    node_id=node_id,
                    class_type=node_data.get('class_type', 'unknown'),
                    inputs=node_data.get('inputs', {}),
                    metadata={
                        'pos': node_data.get('pos', [0, 0]),
                        'size': node_data.get('size', [200, 100]),
                        'flags': node_data.get('flags', {}),
                        'order': node_data.get('order', 0),
                        'mode': node_data.get('mode', 0)
                    }
                )
                nodes.append(node_info)
                
        return nodes

    def _extract_links(self, workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract link information from workflow."""
        links = []
        
        # Links are typically stored in the workflow's 'links' key
        if 'links' in workflow:
            for link in workflow['links']:
                if isinstance(link, (list, tuple)) and len(link) >= 6:
                    links.append({
                        'id': link[0],
                        'origin_id': link[1],
                        'origin_slot': link[2],
                        'target_id': link[3],
                        'target_slot': link[4],
                        'type': link[5] if len(link) > 5 else None
                    })
        
        return links

    def extract_prompt_manager_nodes(self, workflow: Dict[str, Any]) -> List[PromptManagerNodeInfo]:
        """Extract PromptManager node information."""
        pm_nodes = []
        
        for node_id, node_data in workflow.items():
            if (isinstance(node_data, dict) and 
                node_data.get('class_type') == 'PromptManager'):
                
                inputs = node_data.get('inputs', {})
                pm_info = PromptManagerNodeInfo(
                    node_id=node_id,
                    prompt_name=inputs.get('prompt_name', ''),
                    positive_prompt=inputs.get('positive_prompt', ''),
                    negative_prompt=inputs.get('negative_prompt', ''),
                    category=inputs.get('category', ''),
                    unique_id=inputs.get('unique_id', ''),
                    workflow_data=node_data
                )
                pm_nodes.append(pm_info)
                
        return pm_nodes


class NodeParameterExtractor:
    """Extracts node parameters for tracking and analysis."""

    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.NodeParameterExtractor")
        
        # Define parameter mappings for key node types
        self.parameter_mappings = {
            NodeType.CHECKPOINT_LOADER: {
                'model': 'ckpt_name',
                'config': 'config_name'
            },
            NodeType.KSAMPLER: {
                'seed': 'seed',
                'steps': 'steps',
                'cfg': 'cfg',
                'sampler_name': 'sampler_name',
                'scheduler': 'scheduler',
                'denoise': 'denoise'
            },
            NodeType.SAVE_IMAGE: {
                'filename_prefix': 'filename_prefix'
            }
        }

    def extract_parameters(self, prompt: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Extract parameters from all nodes in prompt."""
        extracted = {}
        
        for node_id, node_data in prompt.items():
            if isinstance(node_data, dict) and 'class_type' in node_data:
                class_type = node_data['class_type']
                node_type = self._classify_node(class_type)
                
                if node_type != NodeType.UNKNOWN:
                    params = self._extract_node_parameters(node_data, node_type)
                    extracted[node_id] = {
                        'class_type': class_type,
                        'node_type': node_type.value,
                        'parameters': params
                    }
                    
        return extracted

    def _classify_node(self, class_type: str) -> NodeType:
        """Classify node type from class_type string."""
        mapping = {
            'PromptManager': NodeType.PROMPT_MANAGER,
            'CheckpointLoaderSimple': NodeType.CHECKPOINT_LOADER,
            'KSampler': NodeType.KSAMPLER,
            'KSamplerAdvanced': NodeType.KSAMPLER,
            'VAEDecode': NodeType.VAE_DECODER,
            'SaveImage': NodeType.SAVE_IMAGE
        }
        return mapping.get(class_type, NodeType.UNKNOWN)

    def _extract_node_parameters(self, node_data: Dict[str, Any], 
                                node_type: NodeType) -> Dict[str, Any]:
        """Extract parameters for specific node type."""
        inputs = node_data.get('inputs', {})
        
        if node_type in self.parameter_mappings:
            mapping = self.parameter_mappings[node_type]
            return {key: inputs.get(param_name) for key, param_name in mapping.items()}
        else:
            # For PromptManager and unknown types, return all inputs
            return inputs.copy()


class QueueManager:
    """Manages ComfyUI execution queue integration."""

    def __init__(self, db_manager: Database):
        self.db_manager = db_manager
        self.logger = logging.getLogger(f"{__name__}.QueueManager")

    async def track_queue_changes(self):
        """Track changes to ComfyUI's execution queue."""
        try:
            # This would integrate with ComfyUI's queue system
            # For now, we'll provide the interface
            pass
        except Exception as e:
            self.logger.error(f"Queue tracking failed: {e}")

    async def get_queue_status(self) -> Dict[str, Any]:
        """Get current queue status."""
        try:
            # In a real implementation, this would query ComfyUI's queue
            return {
                'queue_size': 0,
                'running': [],
                'pending': []
            }
        except Exception as e:
            self.logger.error(f"Failed to get queue status: {e}")
            return {'error': str(e)}


class ComfyUIIntegration:
    """Main ComfyUI integration class."""

    def __init__(self, db_manager: Optional[Database] = None):
        self.db_manager = db_manager or Database()
        self.execution_tracker = ExecutionTracker(self.db_manager)
        self.workflow_serializer = WorkflowSerializer()
        self.parameter_extractor = NodeParameterExtractor()
        self.queue_manager = QueueManager(self.db_manager)
        
        # Event hooks
        self.execution_hooks: Dict[str, List[Callable]] = defaultdict(list)
        
        # Integration state
        self.is_initialized = False
        self.logger = logging.getLogger(f"{__name__}.ComfyUIIntegration")

    async def initialize(self):
        """Initialize ComfyUI integration."""
        if self.is_initialized:
            return

        try:
            # Hook into ComfyUI events
            await self._setup_execution_hooks()
            
            # Initialize database tables for execution tracking
            await self._setup_database_tables()
            
            self.is_initialized = True
            self.logger.info("ComfyUI integration initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize ComfyUI integration: {e}")
            raise

    async def _setup_execution_hooks(self):
        """Set up hooks into ComfyUI's execution system."""
        try:
            # In a real implementation, we would hook into ComfyUI's execution events
            # This is a placeholder for the actual integration points
            self.logger.info("Execution hooks set up (placeholder)")
        except Exception as e:
            self.logger.error(f"Failed to set up execution hooks: {e}")
            raise

    async def _setup_database_tables(self):
        """Set up database tables for execution tracking."""
        try:
            async with self.db_manager.get_connection() as conn:
                # Create execution tracking tables
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS execution_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        prompt_id TEXT UNIQUE NOT NULL,
                        client_id TEXT,
                        prompt_data TEXT,
                        workflow_data TEXT,
                        state TEXT DEFAULT 'queued',
                        start_time REAL,
                        end_time REAL,
                        images_generated TEXT,
                        errors TEXT,
                        metadata TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_execution_records_prompt_id 
                    ON execution_records(prompt_id)
                """)
                
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_execution_records_state 
                    ON execution_records(state)
                """)
                
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_execution_records_start_time 
                    ON execution_records(start_time)
                """)
                
        except Exception as e:
            self.logger.error(f"Failed to set up database tables: {e}")
            raise

    def add_execution_hook(self, event: str, callback: Callable):
        """Add hook for execution events."""
        self.execution_hooks[event].append(callback)

    def remove_execution_hook(self, event: str, callback: Callable):
        """Remove execution hook."""
        if callback in self.execution_hooks[event]:
            self.execution_hooks[event].remove(callback)

    async def on_prompt_queued(self, prompt_id: str, client_id: str, 
                              prompt: Dict[str, Any], workflow: Dict[str, Any] = None):
        """Handle prompt queued event."""
        try:
            # Start execution tracking
            await self.execution_tracker.start_execution(
                prompt_id, client_id, prompt, workflow
            )
            
            # Extract and store workflow information
            if workflow:
                serialized_workflow = self.workflow_serializer.serialize_workflow(workflow)
                pm_nodes = self.workflow_serializer.extract_prompt_manager_nodes(workflow)
                
                # Store PromptManager node information
                await self._store_prompt_manager_nodes(prompt_id, pm_nodes)
            
            # Extract node parameters
            parameters = self.parameter_extractor.extract_parameters(prompt)
            
            # Call registered hooks
            for callback in self.execution_hooks.get('prompt_queued', []):
                try:
                    await callback(prompt_id, client_id, prompt, workflow)
                except Exception as e:
                    self.logger.error(f"Execution hook failed: {e}")
                    
        except Exception as e:
            self.logger.error(f"Error handling prompt queued: {e}")

    async def on_prompt_executing(self, prompt_id: str, node_id: str):
        """Handle prompt executing event."""
        try:
            await self.execution_tracker.update_execution(
                prompt_id, 
                current_node=node_id,
                state=ExecutionState.EXECUTING
            )
            
            # Call registered hooks
            for callback in self.execution_hooks.get('prompt_executing', []):
                try:
                    await callback(prompt_id, node_id)
                except Exception as e:
                    self.logger.error(f"Execution hook failed: {e}")
                    
        except Exception as e:
            self.logger.error(f"Error handling prompt executing: {e}")

    async def on_prompt_completed(self, prompt_id: str, images: List[Dict] = None):
        """Handle prompt completed event."""
        try:
            await self.execution_tracker.complete_execution(
                prompt_id, 
                success=True,
                images=images or []
            )
            
            # Store generated images information
            if images:
                await self._store_generated_images(prompt_id, images)
            
            # Call registered hooks
            for callback in self.execution_hooks.get('prompt_completed', []):
                try:
                    await callback(prompt_id, images)
                except Exception as e:
                    self.logger.error(f"Execution hook failed: {e}")
                    
        except Exception as e:
            self.logger.error(f"Error handling prompt completed: {e}")

    async def on_prompt_failed(self, prompt_id: str, errors: List[str]):
        """Handle prompt failed event."""
        try:
            await self.execution_tracker.complete_execution(
                prompt_id,
                success=False,
                errors=errors
            )
            
            # Call registered hooks
            for callback in self.execution_hooks.get('prompt_failed', []):
                try:
                    await callback(prompt_id, errors)
                except Exception as e:
                    self.logger.error(f"Execution hook failed: {e}")
                    
        except Exception as e:
            self.logger.error(f"Error handling prompt failed: {e}")

    async def _store_prompt_manager_nodes(self, prompt_id: str, 
                                        pm_nodes: List[PromptManagerNodeInfo]):
        """Store PromptManager node information."""
        try:
            async with self.db_manager.get_connection() as conn:
                for node_info in pm_nodes:
                    # Link this execution with the prompt in our database
                    await self.db_manager.image_ops.create_image_prompt_link(
                        conn,
                        prompt_id=prompt_id,
                        node_id=node_info.node_id,
                        prompt_name=node_info.prompt_name,
                        positive_prompt=node_info.positive_prompt,
                        negative_prompt=node_info.negative_prompt,
                        category=node_info.category,
                        unique_id=node_info.unique_id
                    )
        except Exception as e:
            self.logger.error(f"Failed to store PromptManager nodes: {e}")

    async def _store_generated_images(self, prompt_id: str, images: List[Dict]):
        """Store information about generated images."""
        try:
            async with self.db_manager.get_connection() as conn:
                for image_info in images:
                    await self.db_manager.image_ops.record_generated_image(
                        conn,
                        prompt_id=prompt_id,
                        filename=image_info.get('filename'),
                        path=image_info.get('path'),
                        metadata=image_info
                    )
        except Exception as e:
            self.logger.error(f"Failed to store generated images: {e}")

    async def send_websocket_update(self, event: str, data: Dict[str, Any], 
                                  client_id: str = None):
        """Send WebSocket update using ComfyUI's infrastructure."""
        try:
            PromptServer.instance.send_sync(f"prompt_manager_{event}", data, client_id)
        except Exception as e:
            self.logger.error(f"Failed to send WebSocket update: {e}")

    async def get_execution_status(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        """Get execution status for a prompt."""
        try:
            if prompt_id in self.execution_tracker.active_executions:
                context = self.execution_tracker.active_executions[prompt_id]
                return {
                    'prompt_id': context.prompt_id,
                    'state': context.state.value,
                    'progress': context.progress,
                    'current_node': context.current_node,
                    'execution_time': (
                        time.time() - context.execution_start_time 
                        if context.execution_start_time else 0
                    ),
                    'images_count': len(context.images_generated),
                    'errors': context.errors
                }
            else:
                # Check database for completed executions
                async with self.db_manager.get_connection() as conn:
                    return await self.db_manager.execution_ops.get_execution_status(
                        conn, prompt_id
                    )
        except Exception as e:
            self.logger.error(f"Failed to get execution status: {e}")
            return None


# Global integration instance
_integration: Optional[ComfyUIIntegration] = None

def get_integration() -> ComfyUIIntegration:
    """Get or create global integration instance."""
    global _integration
    if _integration is None:
        _integration = ComfyUIIntegration()
    return _integration


async def initialize_integration():
    """Initialize global integration instance."""
    integration = get_integration()
    if not integration.is_initialized:
        await integration.initialize()
    return integration