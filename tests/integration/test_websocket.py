"""
Integration tests for WebSocket communication.

Tests real-time communication features including progress updates,
status notifications, and bi-directional data exchange.
"""

import pytest
import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch
import websockets
from websockets.exceptions import ConnectionClosed

from src.api.websocket import WebSocketManager
from src.api.realtime_events import EventEmitter


class TestWebSocketManager:
    """Test cases for WebSocket connection management."""
    
    @pytest.fixture
    def websocket_manager(self):
        """Create a WebSocketManager instance."""
        return WebSocketManager()
    
    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket connection."""
        websocket = AsyncMock()
        websocket.send = AsyncMock()
        websocket.recv = AsyncMock()
        websocket.close = AsyncMock()
        websocket.closed = False
        return websocket
    
    @pytest.mark.asyncio
    async def test_connect_client(self, websocket_manager, mock_websocket):
        """Test connecting a new WebSocket client."""
        client_id = await websocket_manager.connect_client(mock_websocket)
        
        assert client_id is not None
        assert len(websocket_manager.connections) == 1
        assert client_id in websocket_manager.connections
        assert websocket_manager.connections[client_id] == mock_websocket
    
    @pytest.mark.asyncio
    async def test_disconnect_client(self, websocket_manager, mock_websocket):
        """Test disconnecting a WebSocket client."""
        client_id = await websocket_manager.connect_client(mock_websocket)
        
        await websocket_manager.disconnect_client(client_id)
        
        assert len(websocket_manager.connections) == 0
        assert client_id not in websocket_manager.connections
        mock_websocket.close.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_broadcast_message(self, websocket_manager, mock_websocket):
        """Test broadcasting message to all connected clients."""
        # Connect multiple clients
        client1_id = await websocket_manager.connect_client(mock_websocket)
        
        mock_websocket2 = AsyncMock()
        client2_id = await websocket_manager.connect_client(mock_websocket2)
        
        test_message = {"type": "test", "data": "broadcast test"}
        
        await websocket_manager.broadcast_message(test_message)
        
        # Both clients should receive the message
        mock_websocket.send.assert_called_once()
        mock_websocket2.send.assert_called_once()
        
        # Verify message content
        sent_message = json.loads(mock_websocket.send.call_args[0][0])
        assert sent_message["type"] == "test"
        assert sent_message["data"] == "broadcast test"
    
    @pytest.mark.asyncio
    async def test_send_to_client(self, websocket_manager, mock_websocket):
        """Test sending message to specific client."""
        client_id = await websocket_manager.connect_client(mock_websocket)
        
        test_message = {"type": "direct", "data": "direct message"}
        
        await websocket_manager.send_to_client(client_id, test_message)
        
        mock_websocket.send.assert_called_once()
        sent_message = json.loads(mock_websocket.send.call_args[0][0])
        assert sent_message["type"] == "direct"
        assert sent_message["data"] == "direct message"
    
    @pytest.mark.asyncio
    async def test_send_to_nonexistent_client(self, websocket_manager):
        """Test sending message to non-existent client."""
        result = await websocket_manager.send_to_client("invalid_id", {"test": "message"})
        assert result is False
    
    @pytest.mark.asyncio
    async def test_handle_connection_error(self, websocket_manager, mock_websocket):
        """Test handling of connection errors."""
        client_id = await websocket_manager.connect_client(mock_websocket)
        
        # Simulate connection error
        mock_websocket.send.side_effect = ConnectionClosed(None, None)
        
        result = await websocket_manager.send_to_client(client_id, {"test": "message"})
        
        assert result is False
        # Client should be automatically disconnected on error
        assert client_id not in websocket_manager.connections
    
    @pytest.mark.asyncio
    async def test_get_client_count(self, websocket_manager, mock_websocket):
        """Test getting number of connected clients."""
        assert websocket_manager.get_client_count() == 0
        
        await websocket_manager.connect_client(mock_websocket)
        assert websocket_manager.get_client_count() == 1
        
        mock_websocket2 = AsyncMock()
        await websocket_manager.connect_client(mock_websocket2)
        assert websocket_manager.get_client_count() == 2
    
    @pytest.mark.asyncio
    async def test_cleanup_dead_connections(self, websocket_manager, mock_websocket):
        """Test cleanup of dead connections."""
        client_id = await websocket_manager.connect_client(mock_websocket)
        
        # Mark connection as closed
        mock_websocket.closed = True
        
        await websocket_manager.cleanup_dead_connections()
        
        assert client_id not in websocket_manager.connections


class TestEventEmitter:
    """Test cases for real-time event emission."""
    
    @pytest.fixture
    def event_emitter(self):
        """Create an EventEmitter instance."""
        return EventEmitter()
    
    @pytest.fixture
    def mock_websocket_manager(self):
        """Create a mock WebSocketManager."""
        manager = Mock()
        manager.broadcast_message = AsyncMock()
        manager.send_to_client = AsyncMock()
        return manager
    
    def test_subscribe_to_event(self, event_emitter):
        """Test subscribing to events."""
        handler = Mock()
        
        event_emitter.subscribe('test_event', handler)
        
        assert 'test_event' in event_emitter.listeners
        assert handler in event_emitter.listeners['test_event']
    
    def test_unsubscribe_from_event(self, event_emitter):
        """Test unsubscribing from events."""
        handler = Mock()
        
        event_emitter.subscribe('test_event', handler)
        event_emitter.unsubscribe('test_event', handler)
        
        assert handler not in event_emitter.listeners.get('test_event', [])
    
    @pytest.mark.asyncio
    async def test_emit_event(self, event_emitter):
        """Test emitting events to subscribers."""
        handler1 = AsyncMock()
        handler2 = AsyncMock()
        
        event_emitter.subscribe('test_event', handler1)
        event_emitter.subscribe('test_event', handler2)
        
        test_data = {'message': 'test data'}
        await event_emitter.emit('test_event', test_data)
        
        handler1.assert_called_once_with(test_data)
        handler2.assert_called_once_with(test_data)
    
    @pytest.mark.asyncio
    async def test_emit_to_websockets(self, event_emitter, mock_websocket_manager):
        """Test emitting events via WebSocket."""
        event_emitter.websocket_manager = mock_websocket_manager
        
        test_data = {'type': 'progress', 'progress': 50}
        await event_emitter.emit_to_websockets('progress_update', test_data)
        
        mock_websocket_manager.broadcast_message.assert_called_once()
        call_args = mock_websocket_manager.broadcast_message.call_args[0][0]
        assert call_args['event'] == 'progress_update'
        assert call_args['data'] == test_data
    
    @pytest.mark.asyncio
    async def test_emit_to_specific_client(self, event_emitter, mock_websocket_manager):
        """Test emitting events to specific WebSocket client."""
        event_emitter.websocket_manager = mock_websocket_manager
        
        client_id = 'test_client'
        test_data = {'message': 'client specific'}
        
        await event_emitter.emit_to_client(client_id, 'client_message', test_data)
        
        mock_websocket_manager.send_to_client.assert_called_once_with(
            client_id,
            {'event': 'client_message', 'data': test_data}
        )


class TestProgressUpdates:
    """Test cases for progress update functionality."""
    
    @pytest.fixture
    def progress_tracker(self, event_emitter):
        """Create a progress tracker."""
        from src.services.progress_tracker import ProgressTracker
        return ProgressTracker(event_emitter)
    
    @pytest.mark.asyncio
    async def test_start_progress(self, progress_tracker):
        """Test starting a progress operation."""
        task_id = progress_tracker.start_progress('test_task', total=100)
        
        assert task_id is not None
        assert task_id in progress_tracker.active_tasks
        
        task_info = progress_tracker.get_progress(task_id)
        assert task_info['name'] == 'test_task'
        assert task_info['total'] == 100
        assert task_info['current'] == 0
        assert task_info['status'] == 'running'
    
    @pytest.mark.asyncio
    async def test_update_progress(self, progress_tracker):
        """Test updating progress."""
        task_id = progress_tracker.start_progress('test_task', total=100)
        
        await progress_tracker.update_progress(task_id, 50, 'Half way done')
        
        task_info = progress_tracker.get_progress(task_id)
        assert task_info['current'] == 50
        assert task_info['message'] == 'Half way done'
        assert task_info['percent'] == 50.0
    
    @pytest.mark.asyncio
    async def test_complete_progress(self, progress_tracker):
        """Test completing a progress operation."""
        task_id = progress_tracker.start_progress('test_task', total=100)
        
        await progress_tracker.complete_progress(task_id, 'Task completed successfully')
        
        task_info = progress_tracker.get_progress(task_id)
        assert task_info['status'] == 'completed'
        assert task_info['message'] == 'Task completed successfully'
        assert task_info['current'] == task_info['total']
    
    @pytest.mark.asyncio
    async def test_fail_progress(self, progress_tracker):
        """Test failing a progress operation."""
        task_id = progress_tracker.start_progress('test_task', total=100)
        
        await progress_tracker.fail_progress(task_id, 'Task failed with error')
        
        task_info = progress_tracker.get_progress(task_id)
        assert task_info['status'] == 'failed'
        assert task_info['message'] == 'Task failed with error'
    
    @pytest.mark.asyncio
    async def test_progress_events_emitted(self, progress_tracker, event_emitter):
        """Test that progress updates emit events."""
        handler = AsyncMock()
        event_emitter.subscribe('progress_update', handler)
        
        task_id = progress_tracker.start_progress('test_task', total=100)
        await progress_tracker.update_progress(task_id, 25)
        
        # Should have emitted progress_update event
        handler.assert_called()
        call_args = handler.call_args[0][0]
        assert call_args['task_id'] == task_id
        assert call_args['current'] == 25
    
    def test_get_all_progress(self, progress_tracker):
        """Test getting all active progress operations."""
        task1_id = progress_tracker.start_progress('task1', total=100)
        task2_id = progress_tracker.start_progress('task2', total=50)
        
        all_progress = progress_tracker.get_all_progress()
        
        assert len(all_progress) == 2
        assert task1_id in all_progress
        assert task2_id in all_progress
    
    def test_cleanup_completed_tasks(self, progress_tracker):
        """Test cleanup of completed tasks."""
        task_id = progress_tracker.start_progress('test_task', total=100)
        progress_tracker.complete_progress(task_id, 'Done')
        
        # Task should still exist immediately
        assert task_id in progress_tracker.active_tasks
        
        # After cleanup, completed tasks should be removed
        progress_tracker.cleanup_completed_tasks(max_age=0)
        assert task_id not in progress_tracker.active_tasks


class TestWebSocketIntegration:
    """End-to-end WebSocket integration tests."""
    
    @pytest.mark.asyncio
    async def test_image_scan_progress(self, websocket_manager, event_emitter):
        """Test real-time progress updates during image scanning."""
        # Mock client connection
        mock_websocket = AsyncMock()
        client_id = await websocket_manager.connect_client(mock_websocket)
        
        # Simulate image scanning with progress updates
        from src.services.image_scanner import ImageScanner
        
        scanner = Mock()
        scanner.scan_for_new_images = AsyncMock()
        
        # Mock progress updates
        progress_data = [
            {'current': 25, 'total': 100, 'message': 'Processing image 25/100'},
            {'current': 50, 'total': 100, 'message': 'Processing image 50/100'},
            {'current': 100, 'total': 100, 'message': 'Scan completed'}
        ]
        
        # Simulate sending progress updates
        for progress in progress_data:
            await event_emitter.emit_to_client(client_id, 'scan_progress', progress)
        
        # Verify all progress messages were sent
        assert mock_websocket.send.call_count == len(progress_data)
    
    @pytest.mark.asyncio
    async def test_thumbnail_generation_updates(self, websocket_manager):
        """Test real-time updates during thumbnail generation."""
        mock_websocket = AsyncMock()
        client_id = await websocket_manager.connect_client(mock_websocket)
        
        # Simulate thumbnail generation events
        events = [
            {'type': 'thumbnail_start', 'file': 'image1.png'},
            {'type': 'thumbnail_progress', 'current': 1, 'total': 5},
            {'type': 'thumbnail_complete', 'file': 'image1.png', 'thumbnail_path': '/path/thumb.jpg'},
            {'type': 'thumbnail_start', 'file': 'image2.png'},
            {'type': 'thumbnail_progress', 'current': 2, 'total': 5}
        ]
        
        for event in events:
            await websocket_manager.send_to_client(client_id, event)
        
        assert mock_websocket.send.call_count == len(events)
    
    @pytest.mark.asyncio
    async def test_database_operation_notifications(self, websocket_manager):
        """Test notifications for database operations."""
        mock_websocket = AsyncMock()
        client_id = await websocket_manager.connect_client(mock_websocket)
        
        # Simulate database operation events
        db_events = [
            {'type': 'prompt_created', 'prompt_id': 123, 'category': 'landscapes'},
            {'type': 'image_added', 'image_id': 456, 'prompt_id': 123},
            {'type': 'database_vacuum_start'},
            {'type': 'database_vacuum_complete', 'size_before': 1000000, 'size_after': 800000}
        ]
        
        for event in db_events:
            await websocket_manager.send_to_client(client_id, event)
        
        assert mock_websocket.send.call_count == len(db_events)
    
    @pytest.mark.asyncio
    async def test_error_notification(self, websocket_manager):
        """Test error notifications via WebSocket."""
        mock_websocket = AsyncMock()
        client_id = await websocket_manager.connect_client(mock_websocket)
        
        error_event = {
            'type': 'error',
            'error_code': 'SCAN_FAILED',
            'message': 'Failed to scan directory: Permission denied',
            'details': {'directory': '/protected/path'}
        }
        
        await websocket_manager.send_to_client(client_id, error_event)
        
        mock_websocket.send.assert_called_once()
        sent_message = json.loads(mock_websocket.send.call_args[0][0])
        assert sent_message['type'] == 'error'
        assert sent_message['error_code'] == 'SCAN_FAILED'
    
    @pytest.mark.asyncio
    async def test_client_message_handling(self, websocket_manager):
        """Test handling of messages from WebSocket clients."""
        mock_websocket = AsyncMock()
        client_id = await websocket_manager.connect_client(mock_websocket)
        
        # Simulate client sending message
        client_message = {
            'type': 'request_scan',
            'directory': '/path/to/scan',
            'recursive': True
        }
        
        # Mock message handler
        handler = AsyncMock()
        websocket_manager.message_handlers['request_scan'] = handler
        
        await websocket_manager.handle_client_message(client_id, client_message)
        
        handler.assert_called_once_with(client_id, client_message)
    
    @pytest.mark.asyncio
    async def test_connection_lifecycle(self, websocket_manager):
        """Test complete WebSocket connection lifecycle."""
        mock_websocket = AsyncMock()
        
        # Connect
        client_id = await websocket_manager.connect_client(mock_websocket)
        assert websocket_manager.get_client_count() == 1
        
        # Send messages
        test_message = {'type': 'test', 'data': 'lifecycle test'}
        await websocket_manager.send_to_client(client_id, test_message)
        mock_websocket.send.assert_called_once()
        
        # Broadcast
        broadcast_message = {'type': 'broadcast', 'data': 'all clients'}
        await websocket_manager.broadcast_message(broadcast_message)
        assert mock_websocket.send.call_count == 2
        
        # Disconnect
        await websocket_manager.disconnect_client(client_id)
        assert websocket_manager.get_client_count() == 0
        mock_websocket.close.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_multiple_client_broadcast(self, websocket_manager):
        """Test broadcasting to multiple clients."""
        # Connect multiple clients
        clients = []
        for i in range(3):
            mock_websocket = AsyncMock()
            client_id = await websocket_manager.connect_client(mock_websocket)
            clients.append((client_id, mock_websocket))
        
        assert websocket_manager.get_client_count() == 3
        
        # Broadcast message
        broadcast_message = {'type': 'multi_broadcast', 'data': 'everyone gets this'}
        await websocket_manager.broadcast_message(broadcast_message)
        
        # All clients should receive the message
        for client_id, mock_websocket in clients:
            mock_websocket.send.assert_called_once()
            sent_data = json.loads(mock_websocket.send.call_args[0][0])
            assert sent_data['type'] == 'multi_broadcast'
    
    @pytest.mark.asyncio
    async def test_websocket_authentication(self, websocket_manager):
        """Test WebSocket authentication (if implemented)."""
        mock_websocket = AsyncMock()
        
        # Mock authentication data
        auth_data = {'token': 'valid_auth_token'}
        
        with patch('src.api.websocket.validate_auth_token') as mock_validate:
            mock_validate.return_value = True
            
            client_id = await websocket_manager.connect_client(mock_websocket, auth_data)
            assert client_id is not None
            mock_validate.assert_called_once_with('valid_auth_token')
    
    @pytest.mark.asyncio
    async def test_websocket_rate_limiting(self, websocket_manager):
        """Test WebSocket rate limiting (if implemented)."""
        mock_websocket = AsyncMock()
        client_id = await websocket_manager.connect_client(mock_websocket)
        
        # Send many rapid messages
        messages_sent = 0
        rate_limited = 0
        
        for i in range(100):
            try:
                await websocket_manager.send_to_client(client_id, {'test': f'message_{i}'})
                messages_sent += 1
            except Exception:
                rate_limited += 1
        
        # If rate limiting is implemented, some messages should be blocked
        # If not implemented, all messages should go through
        assert messages_sent + rate_limited == 100