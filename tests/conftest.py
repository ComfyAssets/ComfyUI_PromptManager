"""
Comprehensive test fixtures for ComfyUI PromptManager testing framework.

This module provides all necessary fixtures for testing database operations,
API endpoints, file system operations, and ComfyUI integration components.
"""

import pytest
import tempfile
import shutil
import sqlite3
import json
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch, Mock
from datetime import datetime, timezone
import sys
import os
from typing import Dict, Any, List, Optional
from PIL import Image
import io

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Import after path setup
from src.database.models import PromptModel
from src.database.operations import PromptDatabase
from src.repositories.prompt_repository import PromptRepository
from src.repositories.generated_image_repository import GeneratedImageRepository
from src.services.image_service import ImageService
from src.services.enhanced_thumbnail_service import EnhancedThumbnailService
from src.config import Config


# ============================================================================
# Core Fixtures
# ============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    temp_path = tempfile.mkdtemp()
    yield Path(temp_path)
    shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture
def test_db_path(temp_dir):
    """Create a temporary database path."""
    return str(temp_dir / "test_promptmanager.db")


@pytest.fixture
def test_config(temp_dir):
    """Create a test configuration with temporary directories."""
    config = Config()
    config.storage.output_dir = str(temp_dir / 'output')
    config.storage.input_dir = str(temp_dir / 'input')
    config.storage.temp_dir = str(temp_dir / 'temp')
    config.database.path = str(temp_dir / 'promptmanager.db')
    config.storage.thumbnail_dir = str(temp_dir / 'thumbnails')
    config.storage.max_file_size = 10 * 1024 * 1024  # 10MB
    config.api.max_workers = 2  # Reduced for testing
    config.database.batch_size = 10  # Smaller batches for testing
    config.storage.enable_auto_scan = False  # Disable during tests
    config.storage.scan_interval = 30  # Shorter interval for testing
    
    # Create directories
    for dir_path in [config.storage.output_dir, config.storage.input_dir,
                     config.storage.temp_dir, config.storage.thumbnail_dir]:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
    
    return config


# ============================================================================
# Database Fixtures
# ============================================================================

@pytest.fixture
def db_model(test_db_path):
    """Create a fresh PromptModel instance with test database."""
    model = PromptModel(test_db_path)
    yield model
    # Cleanup is automatic when temp_dir is cleaned up


@pytest.fixture
def db_operations(db_model):
    """Create a PromptDatabase operations instance."""
    return PromptDatabase(db_model.db_path)


@pytest.fixture
def db_connection(db_model):
    """Create a database connection for direct SQL operations."""
    conn = db_model.get_connection()
    yield conn
    conn.close()


@pytest.fixture
def populated_db(db_operations):
    """Create a database populated with test data."""
    # Insert test prompts
    test_prompts = [
        {
            'prompt': 'A beautiful landscape with mountains and lakes',
            'negative_prompt': 'blurry, low quality',
            'category': 'landscapes',
            'tags': ['nature', 'mountains', 'lakes'],
            'rating': 5,
            'notes': 'Test landscape prompt'
        },
        {
            'prompt': 'Portrait of a cyberpunk character',
            'negative_prompt': 'ugly, distorted',
            'category': 'portraits',
            'tags': ['cyberpunk', 'character', 'portrait'],
            'rating': 4,
            'notes': 'Test character prompt'
        },
        {
            'prompt': 'Abstract geometric patterns',
            'negative_prompt': 'realistic, photo',
            'category': 'abstract',
            'tags': ['geometric', 'abstract', 'patterns'],
            'rating': 3,
            'notes': 'Test abstract prompt'
        }
    ]
    
    prompt_ids = []
    for prompt_data in test_prompts:
        prompt_id = db_operations.save_prompt(**prompt_data)
        prompt_ids.append(prompt_id)
    
    return db_operations, prompt_ids


# ============================================================================
# Repository Fixtures
# ============================================================================

@pytest.fixture
def prompt_repository(db_model):
    """Create a PromptRepository instance."""
    return PromptRepository(db_model.db_path)


@pytest.fixture
def image_repository(db_model):
    """Create a GeneratedImageRepository instance."""
    return GeneratedImageRepository(db_model.db_path)


# ============================================================================
# Service Fixtures
# ============================================================================

@pytest.fixture
def image_service(image_repository):
    """Create an ImageService instance."""
    return ImageService(repository=image_repository)


@pytest.fixture
def thumbnail_service(test_config):
    """Create an EnhancedThumbnailService instance."""
    return EnhancedThumbnailService(test_config)


# ============================================================================
# Mock Fixtures
# ============================================================================

@pytest.fixture
def mock_comfyui_app():
    """Create a mock ComfyUI application instance."""
    app = MagicMock()
    app.ui = MagicMock()
    app.output_dir = tempfile.mkdtemp()
    app.input_dir = tempfile.mkdtemp()
    app.models_dir = tempfile.mkdtemp()
    app.server = MagicMock()
    app.server.PromptServer = MagicMock()
    return app


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket connection."""
    websocket = AsyncMock()
    websocket.send = AsyncMock()
    websocket.receive = AsyncMock()
    websocket.close = AsyncMock()
    return websocket


@pytest.fixture
def mock_file_monitor():
    """Create a mock file system monitor."""
    with patch('watchdog.observers.Observer') as mock_observer:
        mock_observer.return_value.start = Mock()
        mock_observer.return_value.stop = Mock()
        mock_observer.return_value.join = Mock()
        yield mock_observer


# ============================================================================
# Test Data Fixtures
# ============================================================================

@pytest.fixture
def sample_prompt_data():
    """Create sample prompt data for testing."""
    return {
        'positive_prompt': 'A beautiful sunset over a mountain lake, highly detailed, 8k',
        'negative_prompt': 'blurry, low quality, distorted, ugly',
        'category': 'landscapes',
        'tags': ['landscape', 'nature', 'sunset', 'mountains', '8k'],
        'rating': 4,
        'notes': 'Beautiful landscape prompt for testing'
    }


@pytest.fixture
def sample_image_metadata():
    """Create sample image metadata for testing."""
    return {
        'prompt': 'test prompt',
        'negative_prompt': 'test negative',
        'steps': 20,
        'sampler': 'euler',
        'cfg_scale': 7.5,
        'seed': 12345,
        'model': 'test_checkpoint.safetensors',
        'width': 512,
        'height': 512,
        'workflow': {
            'nodes': [
                {
                    'id': 1,
                    'class_type': 'CLIPTextEncode',
                    'inputs': {'text': 'test prompt'}
                }
            ]
        }
    }


@pytest.fixture
def sample_workflow_data():
    """Create sample ComfyUI workflow data."""
    return {
        'nodes': [
            {
                'id': '1',
                'class_type': 'CheckpointLoaderSimple',
                'inputs': {'ckpt_name': 'test_model.safetensors'}
            },
            {
                'id': '2', 
                'class_type': 'CLIPTextEncode',
                'inputs': {'text': 'beautiful landscape', 'clip': ['1', 1]}
            },
            {
                'id': '3',
                'class_type': 'CLIPTextEncode', 
                'inputs': {'text': 'blurry, ugly', 'clip': ['1', 1]}
            },
            {
                'id': '4',
                'class_type': 'KSampler',
                'inputs': {
                    'seed': 12345,
                    'steps': 20,
                    'cfg': 7.5,
                    'sampler_name': 'euler',
                    'scheduler': 'normal',
                    'positive': ['2', 0],
                    'negative': ['3', 0],
                    'model': ['1', 0]
                }
            }
        ],
        'extra': {},
        'version': 0.4
    }


# ============================================================================
# File System Fixtures
# ============================================================================

@pytest.fixture
def test_image_file(temp_dir):
    """Create a test PNG image file with metadata."""
    image_path = temp_dir / "test_image.png"
    
    # Create a simple test image
    img = Image.new('RGB', (512, 512), color='red')
    
    # Save with PNG metadata
    metadata = {
        'prompt': 'test prompt for image',
        'workflow': '{"nodes": [{"id": 1, "type": "test"}]}',
        'parameters': '{"steps": 20, "cfg": 7.5}'
    }
    
    pnginfo = Image.PngInfo()
    for key, value in metadata.items():
        pnginfo.add_text(key, value)
    
    img.save(image_path, pnginfo=pnginfo)
    return image_path


@pytest.fixture
def test_video_file(temp_dir):
    """Create a mock video file for testing."""
    video_path = temp_dir / "test_video.mp4"
    # Create empty file to simulate video
    video_path.write_bytes(b"fake video data")
    return video_path


@pytest.fixture
def test_files_structure(temp_dir, test_config):
    """Create a complete test file structure."""
    output_dir = Path(test_config.storage.output_dir)
    
    # Create subdirectories
    (output_dir / "subdir1").mkdir()
    (output_dir / "subdir2").mkdir()
    
    files = []
    
    # Create test images with different formats
    for i, fmt in enumerate(['png', 'jpg', 'webp']):
        image_path = output_dir / f"test_image_{i}.{fmt}"
        img = Image.new('RGB', (256, 256), color=['red', 'green', 'blue'][i])
        img.save(image_path)
        files.append(image_path)
    
    # Create test video files
    for i, fmt in enumerate(['mp4', 'webm']):
        video_path = output_dir / f"test_video_{i}.{fmt}"
        video_path.write_bytes(f"fake {fmt} video data".encode())
        files.append(video_path)
    
    # Create files in subdirectories
    sub_image = output_dir / "subdir1" / "nested_image.png"
    img = Image.new('RGB', (128, 128), color='yellow')
    img.save(sub_image)
    files.append(sub_image)
    
    return files


# ============================================================================
# API Testing Fixtures
# ============================================================================

@pytest.fixture
def api_client():
    """Create a test API client."""
    from fastapi.testclient import TestClient
    from src.api.routes import app
    
    return TestClient(app)


@pytest.fixture
def mock_request():
    """Create a mock HTTP request."""
    request = Mock()
    request.method = 'GET'
    request.url = 'http://test.example.com/api/test'
    request.headers = {'content-type': 'application/json'}
    request.query_params = {}
    return request


@pytest.fixture
def mock_response():
    """Create a mock HTTP response."""
    response = Mock()
    response.status_code = 200
    response.headers = {'content-type': 'application/json'}
    response.json = Mock(return_value={'status': 'ok'})
    return response


# ============================================================================
# Async Testing Fixtures
# ============================================================================

@pytest.fixture
async def async_db_operations(test_db_path):
    """Create async database operations for testing."""
    # For future async database operations
    db_ops = PromptDatabase(test_db_path)
    yield db_ops


# ============================================================================
# Performance Testing Fixtures
# ============================================================================

@pytest.fixture
def large_dataset(populated_db):
    """Create a large dataset for performance testing."""
    db_operations, _ = populated_db

    # Add many more prompts for performance testing
    for i in range(100):
        db_operations.save_prompt(
            prompt=f'Performance test prompt {i}',
            negative_prompt=f'negative {i}',
            category=f'category_{i % 10}',
            tags=[f'tag_{i}', f'perf_test'],
            rating=(i % 5) + 1
        )

    return db_operations


# ============================================================================
# Cleanup Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def cleanup_environment():
    """Automatically cleanup test environment after each test."""
    yield
    # Reset any global state
    # Clear caches, close connections, etc.
    import gc
    gc.collect()


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Set up the test environment at session start."""
    # Set environment variables for testing
    os.environ['TESTING'] = '1'
    os.environ['LOG_LEVEL'] = 'DEBUG'
    
    yield
    
    # Cleanup at session end
    if 'TESTING' in os.environ:
        del os.environ['TESTING']
    if 'LOG_LEVEL' in os.environ:
        del os.environ['LOG_LEVEL']


# ============================================================================
# Utility Functions for Tests
# ============================================================================

def create_test_prompt(
    positive: str = "test prompt",
    negative: str = "test negative",
    category: str = "test",
    tags: List[str] = None,
    rating: int = 3
) -> Dict[str, Any]:
    """Helper function to create test prompt data."""
    return {
        'prompt': positive,
        'negative_prompt': negative,
        'category': category,
        'tags': tags or ['test'],
        'rating': rating,
        'notes': 'Test prompt created by helper function'
    }


def create_test_image_record(
    prompt_id: int,
    filename: str = "test.png",
    width: int = 512,
    height: int = 512
) -> Dict[str, Any]:
    """Helper function to create test image record data."""
    return {
        'prompt_id': prompt_id,
        'image_path': f'/test/path/{filename}',
        'filename': filename,
        'file_size': 1024 * 100,  # 100KB
        'width': width,
        'height': height,
        'format': 'PNG',
        'workflow_data': '{"test": "workflow"}',
        'parameters': '{"steps": 20, "cfg": 7.5}'
    }