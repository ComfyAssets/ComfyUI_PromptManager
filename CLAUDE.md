# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ComfyUI_PromptManager is a comprehensive ComfyUI custom node that extends text encoding with persistent prompt storage, advanced search capabilities, and an automatic image gallery system. The project provides two node types: PromptManager (CLIP encoding) and PromptManagerText (text-only STRING output), both with shared database and gallery features.

## Development Commands

### Testing
```bash
# Run basic tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_basic.py -v

# Test within ComfyUI environment (preferred method)
# Load ComfyUI with the custom node and test workflows manually
```

### Code Quality
```bash
# Format Python code (when development dependencies are installed)
black .

# Python linting
flake8 .

# Type checking
mypy .
```

### Installation and Setup
```bash
# Install required dependencies
pip install -r requirements.txt

# For development dependencies, uncomment and install from requirements.txt:
# pip install pytest black flake8 mypy
```

## Architecture Overview

### Core Node Architecture
- **PromptManager** (`prompt_manager.py`): Main CLIP encoding node that outputs CONDITIONING
- **PromptManagerText** (`prompt_manager_text.py`): Text-only node that outputs STRING with prepend/append functionality
- Both nodes share the same database backend and gallery system

### Database Layer
- **SQLite Backend**: Local persistent storage with automatic schema management
- **PromptModel** (`database/models.py`): Database schema and connection management
- **PromptDatabase** (`database/operations.py`): CRUD operations, search functionality, and data management
- **Hash-based Deduplication**: SHA256 hashing prevents duplicate prompt storage

### Gallery and Monitoring System
- **ImageMonitor** (`utils/image_monitor.py`): Automatic file system monitoring for ComfyUI output
- **PromptTracker** (`utils/prompt_tracker.py`): Links generated images to their source prompts
- **MetadataExtractor** (`utils/metadata_extractor.py`): PNG analysis for ComfyUI workflow extraction
- **Real-time Gallery**: Complete output directory monitoring with video support

### Web Interface
- **Admin Dashboard** (`web/admin.html`): Comprehensive management interface with modern dark theme
- **Metadata Viewer** (`web/metadata.html`): Standalone PNG analysis tool
- **API Backend** (`py/api.py`): REST endpoints for web interface functionality
- **Configuration System** (`py/config.py`): Centralized settings management

### Utility Systems
- **Logging** (`utils/logging_config.py`): Comprehensive logging with web-based log viewer
- **Diagnostics** (`utils/diagnostics.py`): System health checks and troubleshooting
- **Validators** (`utils/validators.py`): Input validation and sanitization
- **Hashing** (`utils/hashing.py`): SHA256 utilities for deduplication

## Key Integration Points

### ComfyUI Node Registration
Both nodes follow standard ComfyUI patterns:
- `INPUT_TYPES()`: Defines node inputs and UI elements
- `RETURN_TYPES`: Specifies output types (CONDITIONING or STRING)
- `encode()` method: Main processing function
- Category: "PromptManager/Text" for organization

### Database Schema
```sql
-- Core prompts table with metadata
CREATE TABLE prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    category TEXT,
    tags TEXT,  -- JSON array
    rating INTEGER CHECK(rating >= 1 AND rating <= 5),
    notes TEXT,
    hash TEXT UNIQUE,  -- SHA256 for deduplication
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- Image tracking with workflow metadata
CREATE TABLE generated_images (
    prompt_id TEXT NOT NULL,
    image_path TEXT NOT NULL,
    workflow_data TEXT,  -- JSON workflow metadata
    prompt_metadata TEXT,  -- JSON parameters
    generation_time TIMESTAMP,
    FOREIGN KEY (prompt_id) REFERENCES prompts(id)
);
```

### Web API Architecture
- **aiohttp Integration**: Routes registered with ComfyUI's server instance
- **RESTful Design**: Standard HTTP methods for CRUD operations
- **JSON Responses**: Structured data exchange for frontend
- **Error Handling**: Comprehensive error logging and user feedback

## Development Workflow

### Adding New Features
1. **Database First**: Update schema in `database/models.py` if needed
2. **Core Logic**: Implement in appropriate utility modules
3. **Node Integration**: Update node classes for new inputs/outputs
4. **API Endpoints**: Add REST endpoints in `py/api.py`
5. **Web Interface**: Update HTML/JavaScript for user interaction
6. **Testing**: Write tests and validate in ComfyUI environment

### Configuration Management
Settings are centralized in `py/config.py`:
- **GalleryConfig**: Image monitoring and processing settings
- **PromptManagerConfig**: Core node behavior and performance
- **Runtime Loading**: Configuration loaded from `config.json` if present

### Gallery System Integration
The gallery monitoring system:
- **Automatic Detection**: Monitors ComfyUI output directories
- **Workflow Linking**: Associates generated images with source prompts
- **Metadata Extraction**: Parses PNG chunks for ComfyUI workflow data
- **Performance Optimization**: Thumbnail generation and lazy loading

## Critical Implementation Notes

### ComfyUI Compatibility
- **Node Registration**: Uses `NODE_CLASS_MAPPINGS` and `NODE_DISPLAY_NAME_MAPPINGS`
- **Web Directory**: Exposes `WEB_DIRECTORY = "web"` for frontend assets
- **Server Integration**: API routes registered with ComfyUI's aiohttp server
- **Memory Management**: Proper cleanup of resources and database connections

### Database Operations
- **Thread Safety**: SQLite operations are thread-safe with proper connection management
- **Transaction Handling**: Atomic operations for data consistency
- **Index Optimization**: Proper indexing for search performance
- **Backup Support**: Built-in backup and restore functionality

### Error Handling Strategy
- **Graceful Degradation**: Node continues to function even if database operations fail
- **Comprehensive Logging**: All errors logged with context and stack traces
- **User Feedback**: Clear error messages in web interface
- **Recovery Mechanisms**: Automatic cleanup and repair operations

### Performance Considerations
- **Lazy Loading**: Gallery images loaded on demand
- **Concurrent Processing**: Limited concurrent metadata extraction
- **Memory Efficiency**: Proper resource cleanup and garbage collection
- **Database Optimization**: Regular VACUUM operations and duplicate cleanup

The codebase is designed for extensibility while maintaining compatibility with ComfyUI's architecture and providing a robust user experience through comprehensive error handling and logging.