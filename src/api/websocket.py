"""WebSocket support for real-time updates.

This module provides WebSocket handlers for broadcasting
real-time updates to connected clients.
"""

import json
import asyncio
from typing import Any, Dict, List, Set
from dataclasses import dataclass, asdict
from enum import Enum
from datetime import datetime

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.api.websocket")


class EventType(Enum):
    """WebSocket event types."""
    
    # Prompt events
    PROMPT_CREATED = "prompt.created"
    PROMPT_UPDATED = "prompt.updated"
    PROMPT_DELETED = "prompt.deleted"
    PROMPT_RATED = "prompt.rated"
    PROMPT_USED = "prompt.used"
    
    # Image events
    IMAGE_CREATED = "image.created"
    IMAGE_UPDATED = "image.updated"
    IMAGE_DELETED = "image.deleted"
    IMAGE_THUMBNAIL_READY = "image.thumbnail_ready"
    
    # Processing events
    BATCH_STARTED = "batch.started"
    BATCH_PROGRESS = "batch.progress"
    BATCH_COMPLETED = "batch.completed"
    BATCH_ERROR = "batch.error"
    
    # System events
    SYSTEM_STATUS = "system.status"
    CLIENT_CONNECTED = "client.connected"
    CLIENT_DISCONNECTED = "client.disconnected"


@dataclass
class WebSocketEvent:
    """WebSocket event structure."""
    
    event_type: EventType
    payload: Dict[str, Any]
    timestamp: float = None
    client_id: str = None
    
    def __post_init__(self):
        """Initialize timestamp if not provided."""
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().timestamp()
    
    def to_json(self) -> str:
        """Convert event to JSON string.
        
        Returns:
            JSON string representation
        """
        data = {
            "type": self.event_type.value,
            "payload": self.payload,
            "timestamp": self.timestamp
        }
        
        if self.client_id:
            data["client_id"] = self.client_id
        
        return json.dumps(data)


class WebSocketManager:
    """Manages WebSocket connections and broadcasting."""
    
    def __init__(self):
        """Initialize WebSocket manager."""
        self.connections: Set[Any] = set()
        self.client_subscriptions: Dict[str, Set[EventType]] = {}
        self.message_queue: asyncio.Queue = asyncio.Queue()
        self.running = False
        
    async def connect(self, websocket, client_id: str = None):
        """Register new WebSocket connection.
        
        Args:
            websocket: WebSocket connection object
            client_id: Optional client identifier
        """
        self.connections.add(websocket)
        
        if client_id:
            # Subscribe to all events by default
            self.client_subscriptions[client_id] = set(EventType)
        
        # Send connection confirmation
        event = WebSocketEvent(
            event_type=EventType.CLIENT_CONNECTED,
            payload={"client_id": client_id, "status": "connected"}
        )
        
        await self.send_to_client(websocket, event)
        logger.info(f"Client connected: {client_id or 'anonymous'}")
    
    async def disconnect(self, websocket, client_id: str = None):
        """Unregister WebSocket connection.
        
        Args:
            websocket: WebSocket connection object
            client_id: Optional client identifier
        """
        self.connections.discard(websocket)
        
        if client_id and client_id in self.client_subscriptions:
            del self.client_subscriptions[client_id]
        
        logger.info(f"Client disconnected: {client_id or 'anonymous'}")
    
    async def subscribe(self, client_id: str, event_types: List[EventType]):
        """Subscribe client to specific event types.
        
        Args:
            client_id: Client identifier
            event_types: List of event types to subscribe to
        """
        if client_id not in self.client_subscriptions:
            self.client_subscriptions[client_id] = set()
        
        self.client_subscriptions[client_id].update(event_types)
        logger.debug(f"Client {client_id} subscribed to {len(event_types)} events")
    
    async def unsubscribe(self, client_id: str, event_types: List[EventType]):
        """Unsubscribe client from specific event types.
        
        Args:
            client_id: Client identifier
            event_types: List of event types to unsubscribe from
        """
        if client_id in self.client_subscriptions:
            for event_type in event_types:
                self.client_subscriptions[client_id].discard(event_type)
            
            logger.debug(f"Client {client_id} unsubscribed from {len(event_types)} events")
    
    async def broadcast(self, event: WebSocketEvent):
        """Broadcast event to all connected clients.
        
        Args:
            event: Event to broadcast
        """
        if not self.connections:
            return
        
        disconnected = set()
        
        for websocket in self.connections:
            try:
                await self.send_to_client(websocket, event)
            except Exception as e:
                logger.warning(f"Failed to send to client: {e}")
                disconnected.add(websocket)
        
        # Clean up disconnected clients
        for websocket in disconnected:
            await self.disconnect(websocket)
    
    async def send_to_client(self, websocket, event: WebSocketEvent):
        """Send event to specific client.
        
        Args:
            websocket: WebSocket connection
            event: Event to send
        """
        await websocket.send(event.to_json())
    
    async def send_to_clients(self, client_ids: List[str], event: WebSocketEvent):
        """Send event to specific clients.
        
        Args:
            client_ids: List of client IDs
            event: Event to send
        """
        for websocket in self.connections:
            # This would need client ID mapping in real implementation
            await self.send_to_client(websocket, event)
    
    async def queue_event(self, event: WebSocketEvent):
        """Queue event for processing.
        
        Args:
            event: Event to queue
        """
        await self.message_queue.put(event)
    
    async def process_queue(self):
        """Process queued events."""
        self.running = True
        
        while self.running:
            try:
                # Wait for event with timeout
                event = await asyncio.wait_for(
                    self.message_queue.get(),
                    timeout=1.0
                )
                
                # Broadcast to all clients
                await self.broadcast(event)
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Error processing queue: {e}")
    
    async def start(self):
        """Start WebSocket manager."""
        logger.info("Starting WebSocket manager")
        asyncio.create_task(self.process_queue())
    
    async def stop(self):
        """Stop WebSocket manager."""
        logger.info("Stopping WebSocket manager")
        self.running = False
        
        # Disconnect all clients
        for websocket in list(self.connections):
            await self.disconnect(websocket)


class WebSocketHandler:
    """Handles WebSocket requests."""
    
    def __init__(self, manager: WebSocketManager = None):
        """Initialize WebSocket handler.
        
        Args:
            manager: WebSocket manager instance
        """
        self.manager = manager or WebSocketManager()
    
    async def handle_connection(self, websocket, path):
        """Handle new WebSocket connection.
        
        Args:
            websocket: WebSocket connection
            path: Request path
        """
        client_id = self._extract_client_id(path)
        
        try:
            # Register connection
            await self.manager.connect(websocket, client_id)
            
            # Handle messages
            async for message in websocket:
                await self.handle_message(websocket, message, client_id)
                
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            # Unregister connection
            await self.manager.disconnect(websocket, client_id)
    
    async def handle_message(self, websocket, message: str, client_id: str):
        """Handle WebSocket message.
        
        Args:
            websocket: WebSocket connection
            message: Message string
            client_id: Client identifier
        """
        try:
            data = json.loads(message)
            message_type = data.get("type")
            
            if message_type == "subscribe":
                event_types = [
                    EventType(et) for et in data.get("events", [])
                ]
                await self.manager.subscribe(client_id, event_types)
                
            elif message_type == "unsubscribe":
                event_types = [
                    EventType(et) for et in data.get("events", [])
                ]
                await self.manager.unsubscribe(client_id, event_types)
                
            elif message_type == "ping":
                # Respond with pong
                await websocket.send(json.dumps({"type": "pong"}))
                
            else:
                logger.warning(f"Unknown message type: {message_type}")
                
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON message: {message}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")
    
    def _extract_client_id(self, path: str) -> str:
        """Extract client ID from path.
        
        Args:
            path: Request path
            
        Returns:
            Client ID or None
        """
        # Example: /ws/client123
        parts = path.strip("/").split("/")
        if len(parts) > 1:
            return parts[1]
        return None


# Global WebSocket manager instance
ws_manager = WebSocketManager()


# Event broadcasting helpers

async def broadcast_prompt_created(prompt: Dict[str, Any]):
    """Broadcast prompt created event.
    
    Args:
        prompt: Prompt data
    """
    event = WebSocketEvent(
        event_type=EventType.PROMPT_CREATED,
        payload=prompt
    )
    await ws_manager.queue_event(event)


async def broadcast_prompt_updated(prompt: Dict[str, Any]):
    """Broadcast prompt updated event.
    
    Args:
        prompt: Updated prompt data
    """
    event = WebSocketEvent(
        event_type=EventType.PROMPT_UPDATED,
        payload=prompt
    )
    await ws_manager.queue_event(event)


async def broadcast_prompt_deleted(prompt_id: int):
    """Broadcast prompt deleted event.
    
    Args:
        prompt_id: Deleted prompt ID
    """
    event = WebSocketEvent(
        event_type=EventType.PROMPT_DELETED,
        payload={"id": prompt_id}
    )
    await ws_manager.queue_event(event)


async def broadcast_image_created(image: Dict[str, Any]):
    """Broadcast image created event.
    
    Args:
        image: Image data
    """
    event = WebSocketEvent(
        event_type=EventType.IMAGE_CREATED,
        payload=image
    )
    await ws_manager.queue_event(event)


async def broadcast_batch_progress(batch_id: str, current: int, total: int, 
                                  status: str = None):
    """Broadcast batch processing progress.
    
    Args:
        batch_id: Batch identifier
        current: Current item number
        total: Total items
        status: Optional status message
    """
    event = WebSocketEvent(
        event_type=EventType.BATCH_PROGRESS,
        payload={
            "batch_id": batch_id,
            "current": current,
            "total": total,
            "progress": current / total if total > 0 else 0,
            "status": status
        }
    )
    await ws_manager.queue_event(event)


async def broadcast_system_status(status: Dict[str, Any]):
    """Broadcast system status update.
    
    Args:
        status: System status data
    """
    event = WebSocketEvent(
        event_type=EventType.SYSTEM_STATUS,
        payload=status
    )
    await ws_manager.queue_event(event)
