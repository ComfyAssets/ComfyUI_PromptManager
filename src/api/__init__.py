"""API module for ComfyUI PromptManager.

This module provides a complete REST API infrastructure that integrates
seamlessly with ComfyUI's existing server architecture.

Components:
- client.py: HTTP client with advanced features (pooling, caching, circuit breaker)
- middleware.py: CORS, authentication, logging, validation, compression
- comfyui_integration.py: Deep ComfyUI integration with WebSocket support
- openapi_spec.py: Comprehensive OpenAPI 3.0 specification
- documentation.py: Interactive API documentation (Swagger UI, ReDoc)

Key Features:
- REST API endpoints for all PromptManager operations
- Real-time WebSocket updates via ComfyUI's existing infrastructure
- Enterprise-grade middleware stack
- Interactive documentation with examples
- Circuit breaker, rate limiting, and caching
- Full OpenAPI specification with client SDK generation

Integration with ComfyUI:
- Uses server.PromptServer.instance.routes for route registration
- Integrates with ComfyUI's WebSocket system for real-time updates
- Respects ComfyUI's architecture and execution flow
- No competing WebSocket servers or conflicting routes

Usage:
    from src.api import initialize_api_system
    
    # Initialize complete API system
    api_system = await initialize_api_system()
    
    # API available at:
    # http://localhost:8188/prompt_manager/...
    # 
    # Documentation at:
    # http://localhost:8188/prompt_manager/docs

Example Routes:
    GET  /prompt_manager/prompts          - List prompts
    POST /prompt_manager/prompts          - Create prompt
    GET  /prompt_manager/prompts/{id}     - Get prompt
    PUT  /prompt_manager/prompts/{id}     - Update prompt
    DELETE /prompt_manager/prompts/{id}   - Delete prompt
    
    GET  /prompt_manager/images           - List images
    GET  /prompt_manager/statistics       - System statistics
    POST /prompt_manager/bulk/delete      - Bulk operations
    
    GET  /prompt_manager/docs             - Swagger UI
    GET  /prompt_manager/redoc            - ReDoc documentation
    GET  /prompt_manager/openapi.json     - OpenAPI specification
"""

from typing import Dict, Any, Optional
import logging

# Optional re-exports – wrapped to avoid import-time failures when
# running in environments that only need a subset of the API package.
try:  # pragma: no cover - best-effort imports
    from .client import APIClient, get_api_client, initialize_api
except Exception:  # pylint: disable=broad-except
    APIClient = None  # type: ignore
    get_api_client = None  # type: ignore
    initialize_api = None  # type: ignore

try:  # pragma: no cover - best-effort imports
    from .middleware import (
        CORSMiddleware,
        ValidationMiddleware,
        RequestLogger,
        RateLimiter,
        CacheMiddleware,
    )
except Exception:  # pylint: disable=broad-except
    CORSMiddleware = None  # type: ignore
    ValidationMiddleware = None  # type: ignore
    RequestLogger = None  # type: ignore
    RateLimiter = None  # type: ignore
    CacheMiddleware = None  # type: ignore

try:  # pragma: no cover - best-effort imports
    from .comfyui_integration import (
        ComfyUIIntegration,
        get_integration,
        initialize_integration,
    )
except Exception:  # pylint: disable=broad-except
    ComfyUIIntegration = None  # type: ignore
    get_integration = None  # type: ignore
    initialize_integration = None  # type: ignore

try:  # pragma: no cover - best-effort imports
    from .openapi_spec import OpenAPIGenerator, generate_openapi_spec
except Exception:  # pylint: disable=broad-except
    OpenAPIGenerator = None  # type: ignore
    generate_openapi_spec = None  # type: ignore

try:  # pragma: no cover - best-effort imports
    from .documentation import (
        DocumentationServer,
        get_documentation_server,
        initialize_documentation,
    )
except Exception:  # pylint: disable=broad-except
    DocumentationServer = None  # type: ignore
    get_documentation_server = None  # type: ignore
    initialize_documentation = None  # type: ignore


class APISystem:
    """Complete API system for ComfyUI PromptManager."""
    
    def __init__(self):
        self.client: Optional[APIClient] = None
        self.integration: Optional[ComfyUIIntegration] = None
        self.documentation: Optional[DocumentationServer] = None
        self.is_initialized = False
        self.logger = logging.getLogger(f"{__name__}.APISystem")
    
    async def initialize(self) -> 'APISystem':
        """Initialize complete API system."""
        if self.is_initialized:
            return self
        
        try:
            self.logger.info("Initializing PromptManager API system...")
            
            # Initialize API client with routes
            self.client = initialize_api()
            self.logger.info("✅ API client initialized with routes")
            
            # Initialize ComfyUI integration  
            self.integration = await initialize_integration()
            self.logger.info("✅ ComfyUI integration initialized")
            
            # Initialize documentation server
            self.documentation = initialize_documentation()
            self.logger.info("✅ API documentation initialized")
            
            self.is_initialized = True
            self.logger.info("🚀 PromptManager API system ready!")
            
            # Log available endpoints
            self._log_available_endpoints()
            
            return self
            
        except Exception as e:
            self.logger.error(f"Failed to initialize API system: {e}")
            raise
    
    def _log_available_endpoints(self):
        """Log all available API endpoints."""
        base_url = "http://localhost:8188"
        
        endpoints = [
            # API Endpoints
            "📝 PROMPT MANAGEMENT:",
            f"   GET  {base_url}/prompt_manager/prompts",
            f"   POST {base_url}/prompt_manager/prompts",  
            f"   GET  {base_url}/prompt_manager/prompts/{{id}}",
            f"   PUT  {base_url}/prompt_manager/prompts/{{id}}",
            f"   DELETE {base_url}/prompt_manager/prompts/{{id}}",
            "",
            "🖼️ IMAGE GALLERY:",
            f"   GET  {base_url}/prompt_manager/images",
            "",
            "📊 SYSTEM:",
            f"   GET  {base_url}/prompt_manager/statistics",
            f"   GET  {base_url}/prompt_manager/health",
            "",
            "🔧 BULK OPERATIONS:",
            f"   POST {base_url}/prompt_manager/bulk/delete",
            "",
            "📚 DOCUMENTATION:",
            f"   GET  {base_url}/prompt_manager/docs (Swagger UI)",
            f"   GET  {base_url}/prompt_manager/redoc (ReDoc)",
            f"   GET  {base_url}/prompt_manager/openapi.json",
            f"   GET  {base_url}/prompt_manager/openapi.yaml"
        ]
        
        self.logger.info("Available API endpoints:")
        for endpoint in endpoints:
            self.logger.info(endpoint)
    
    async def get_system_status(self) -> Dict[str, Any]:
        """Get complete API system status."""
        status = {
            'system': 'PromptManager API',
            'initialized': self.is_initialized,
            'components': {}
        }
        
        if self.client:
            try:
                health = await self.client.health_check()
                status['components']['api_client'] = {
                    'status': 'healthy',
                    'health_check': health
                }
            except Exception as e:
                status['components']['api_client'] = {
                    'status': 'unhealthy',
                    'error': str(e)
                }
        
        if self.integration:
            status['components']['comfyui_integration'] = {
                'status': 'healthy' if self.integration.is_initialized else 'initializing',
                'initialized': self.integration.is_initialized
            }
        
        if self.documentation:
            try:
                stats = self.documentation.get_documentation_stats()
                status['components']['documentation'] = {
                    'status': 'healthy',
                    'statistics': stats
                }
            except Exception as e:
                status['components']['documentation'] = {
                    'status': 'unhealthy', 
                    'error': str(e)
                }
        
        return status


# Global API system instance
_api_system: Optional[APISystem] = None


async def initialize_api_system() -> APISystem:
    """Initialize the complete API system."""
    global _api_system
    if _api_system is None:
        _api_system = APISystem()
        await _api_system.initialize()
    return _api_system


def get_api_system() -> Optional[APISystem]:
    """Get the current API system instance."""
    return _api_system


# Export main classes and functions
__all__ = [
    # Main system
    'APISystem',
    'initialize_api_system',
    'get_api_system',
    
    # Client
    'APIClient',
    'get_api_client',
    'initialize_api',
    
    # Middleware  
    'CORSMiddleware',
    'AuthenticationMiddleware',
    'LoggingMiddleware',
    'CompressionMiddleware',
    'ValidationMiddleware', 
    'ErrorMiddleware',
    'create_middleware_stack',
    
    # ComfyUI Integration
    'ComfyUIIntegration',
    'get_integration',
    'initialize_integration',
    
    # Documentation
    'OpenAPIGenerator',
    'DocumentationServer',
    'generate_openapi_spec',
    'get_documentation_server',
    'initialize_documentation'
]
