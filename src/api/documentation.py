"""API documentation server for ComfyUI PromptManager.

This module serves interactive API documentation using the OpenAPI specification.
It provides Swagger UI, ReDoc, and raw specification endpoints that integrate 
with ComfyUI's existing server.

Classes:
    DocumentationServer: Serves API documentation
    SwaggerUIHandler: Swagger UI configuration
    ReDocHandler: ReDoc configuration

Routes:
    GET /prompt_manager/docs - Swagger UI
    GET /prompt_manager/redoc - ReDoc interface  
    GET /prompt_manager/openapi.json - OpenAPI specification
    GET /prompt_manager/openapi.yaml - YAML specification

Example:
    docs_server = DocumentationServer()
    docs_server.add_routes_to_server()
    # Documentation available at http://localhost:8188/prompt_manager/docs
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from aiohttp import web
from aiohttp.web import RouteTableDef, Response

from .openapi_spec import OpenAPIGenerator


class SwaggerUIHandler:
    """Handles Swagger UI serving and configuration."""

    def __init__(self, openapi_url: str = "/prompt_manager/openapi.json"):
        self.openapi_url = openapi_url
        self.logger = logging.getLogger(f"{__name__}.SwaggerUIHandler")

    def get_swagger_ui_html(self) -> str:
        """Generate Swagger UI HTML."""
        return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PromptManager API Documentation</title>
    <link rel="stylesheet" type="text/css" href="https://unpkg.com/swagger-ui-dist@4.15.5/swagger-ui.css" />
    <style>
        html {{
            box-sizing: border-box;
            overflow: -moz-scrollbars-vertical;
            overflow-y: scroll;
        }}

        *, *:before, *:after {{
            box-sizing: inherit;
        }}

        body {{
            margin:0;
            background: #fafafa;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }}

        .swagger-ui .topbar {{
            background-color: #0066cc;
        }}

        .swagger-ui .topbar .download-url-wrapper .select-label {{
            color: #fff;
        }}

        .swagger-ui .info .title {{
            color: #0066cc;
        }}
    </style>
</head>
<body>
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@4.15.5/swagger-ui-bundle.js"></script>
    <script src="https://unpkg.com/swagger-ui-dist@4.15.5/swagger-ui-standalone-preset.js"></script>
    <script>
        window.onload = function() {{
            const ui = SwaggerUIBundle({{
                url: '{self.openapi_url}',
                dom_id: '#swagger-ui',
                deepLinking: true,
                presets: [
                    SwaggerUIBundle.presets.apis,
                    SwaggerUIStandalonePreset
                ],
                plugins: [
                    SwaggerUIBundle.plugins.DownloadUrl
                ],
                layout: "StandaloneLayout",
                validatorUrl: null,
                tryItOutEnabled: true,
                supportedSubmitMethods: ['get', 'post', 'put', 'delete', 'patch'],
                onComplete: function() {{
                    console.log('PromptManager API documentation loaded');
                }},
                onFailure: function(error) {{
                    console.error('Failed to load API documentation:', error);
                }}
            }});
            
            window.ui = ui;
        }};
    </script>
</body>
</html>
        """.strip()


class ReDocHandler:
    """Handles ReDoc serving and configuration."""

    def __init__(self, openapi_url: str = "/prompt_manager/openapi.json"):
        self.openapi_url = openapi_url
        self.logger = logging.getLogger(f"{__name__}.ReDocHandler")

    def get_redoc_html(self) -> str:
        """Generate ReDoc HTML."""
        return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PromptManager API Reference</title>
    <link href="https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700" rel="stylesheet">
    <style>
        body {{
            margin: 0;
            padding: 0;
            font-family: 'Roboto', sans-serif;
        }}
    </style>
</head>
<body>
    <redoc spec-url='{self.openapi_url}'></redoc>
    <script src="https://cdn.jsdelivr.net/npm/redoc@2.0.0/bundles/redoc.standalone.js"></script>
    <script>
        console.log('PromptManager API reference loaded');
    </script>
</body>
</html>
        """.strip()


class DocumentationServer:
    """Main documentation server that integrates with ComfyUI."""

    def __init__(self, base_path: str = "/prompt_manager"):
        self.base_path = base_path
        self.openapi_generator = OpenAPIGenerator()
        self.swagger_handler = SwaggerUIHandler(f"{base_path}/openapi.json")
        self.redoc_handler = ReDocHandler(f"{base_path}/openapi.json")
        
        # Route table for integration
        self.routes = RouteTableDef()
        
        self.logger = logging.getLogger(f"{__name__}.DocumentationServer")
        
        # Setup routes
        self._setup_routes()

    def _setup_routes(self):
        """Setup documentation routes."""
        
        @self.routes.get(f'{self.base_path}/docs')
        async def swagger_ui(request):
            """Serve Swagger UI documentation."""
            try:
                html_content = self.swagger_handler.get_swagger_ui_html()
                return Response(
                    text=html_content,
                    content_type='text/html',
                    headers={'Cache-Control': 'no-cache'}
                )
            except Exception as e:
                self.logger.error(f"Failed to serve Swagger UI: {e}")
                return Response(
                    text=f"Error loading documentation: {str(e)}",
                    status=500,
                    content_type='text/plain'
                )

        @self.routes.get(f'{self.base_path}/redoc')
        async def redoc_ui(request):
            """Serve ReDoc documentation."""
            try:
                html_content = self.redoc_handler.get_redoc_html()
                return Response(
                    text=html_content,
                    content_type='text/html',
                    headers={'Cache-Control': 'no-cache'}
                )
            except Exception as e:
                self.logger.error(f"Failed to serve ReDoc: {e}")
                return Response(
                    text=f"Error loading documentation: {str(e)}",
                    status=500,
                    content_type='text/plain'
                )

        @self.routes.get(f'{self.base_path}/openapi.json')
        async def openapi_json(request):
            """Serve OpenAPI specification as JSON."""
            try:
                spec = self.openapi_generator.generate_spec()
                return web.json_response(
                    spec,
                    headers={
                        'Cache-Control': 'public, max-age=3600',
                        'Access-Control-Allow-Origin': 'http://localhost:3000, http://localhost:8188'
                    }
                )
            except Exception as e:
                self.logger.error(f"Failed to generate OpenAPI spec: {e}")
                return web.json_response(
                    {'error': f'Failed to generate specification: {str(e)}'},
                    status=500
                )

        @self.routes.get(f'{self.base_path}/openapi.yaml')
        async def openapi_yaml(request):
            """Serve OpenAPI specification as YAML."""
            try:
                import yaml
                spec = self.openapi_generator.generate_spec()
                yaml_content = yaml.dump(spec, default_flow_style=False, allow_unicode=True)
                return Response(
                    text=yaml_content,
                    content_type='application/x-yaml',
                    headers={
                        'Cache-Control': 'public, max-age=3600',
                        'Access-Control-Allow-Origin': 'http://localhost:3000, http://localhost:8188'
                    }
                )
            except ImportError:
                return web.json_response(
                    {'error': 'PyYAML not installed, cannot serve YAML format'},
                    status=501
                )
            except Exception as e:
                self.logger.error(f"Failed to generate YAML spec: {e}")
                return Response(
                    text=f"Error generating YAML: {str(e)}",
                    status=500,
                    content_type='text/plain'
                )

        @self.routes.get(f'{self.base_path}/docs/health')
        async def docs_health(request):
            """Documentation service health check."""
            try:
                # Test OpenAPI generation
                spec = self.openapi_generator.generate_spec()
                
                return web.json_response({
                    'status': 'healthy',
                    'service': 'documentation',
                    'endpoints': {
                        'swagger_ui': f'{self.base_path}/docs',
                        'redoc': f'{self.base_path}/redoc',
                        'openapi_json': f'{self.base_path}/openapi.json',
                        'openapi_yaml': f'{self.base_path}/openapi.yaml'
                    },
                    'specification': {
                        'version': spec.get('info', {}).get('version', 'unknown'),
                        'paths_count': len(spec.get('paths', {})),
                        'schemas_count': len(spec.get('components', {}).get('schemas', {}))
                    }
                })
            except Exception as e:
                self.logger.error(f"Documentation health check failed: {e}")
                return web.json_response(
                    {
                        'status': 'unhealthy',
                        'service': 'documentation',
                        'error': str(e)
                    },
                    status=503
                )

        @self.routes.get(f'{self.base_path}/')
        async def docs_redirect(request):
            """Redirect root to Swagger UI."""
            return web.HTTPFound(f'{self.base_path}/docs')

    def add_routes_to_server(self):
        """Add documentation routes to ComfyUI's server."""
        try:
            import server
            server.PromptServer.instance.routes.add_routes(self.routes)
            self.logger.info("Documentation routes added to ComfyUI server")

            # Get actual server URL from ComfyUI (respects --listen and --port)
            try:
                from utils.comfyui_utils import get_comfyui_server_url
                server_url = get_comfyui_server_url()
            except Exception:
                server_url = "http://127.0.0.1:8188"  # Fallback

            # Log available endpoints
            endpoints = [
                f"Swagger UI: {server_url}{self.base_path}/docs",
                f"ReDoc: {server_url}{self.base_path}/redoc",
                f"OpenAPI JSON: {server_url}{self.base_path}/openapi.json",
                f"OpenAPI YAML: {server_url}{self.base_path}/openapi.yaml"
            ]

            self.logger.info("API Documentation endpoints:")
            for endpoint in endpoints:
                self.logger.info(f"  - {endpoint}")
                
        except Exception as e:
            self.logger.error(f"Failed to add documentation routes: {e}")
            raise

    def generate_client_sdk(self, language: str, output_path: Path) -> bool:
        """Generate client SDK using OpenAPI Generator (if available)."""
        try:
            import subprocess
            
            # Save OpenAPI spec temporarily
            spec_path = output_path.parent / "openapi-temp.json"
            self.openapi_generator.save_spec(spec_path)
            
            # Generate client SDK
            cmd = [
                "openapi-generator-cli", "generate",
                f"-i", str(spec_path),
                f"-g", language,
                f"-o", str(output_path),
                "--additional-properties", "packageName=prompt_manager_client"
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            # Clean up temporary spec
            spec_path.unlink()
            
            if result.returncode == 0:
                self.logger.info(f"Generated {language} client SDK at {output_path}")
                return True
            else:
                self.logger.error(f"SDK generation failed: {result.stderr}")
                return False
                
        except FileNotFoundError:
            self.logger.error("openapi-generator-cli not found. Install from https://openapi-generator.tech/")
            return False
        except Exception as e:
            self.logger.error(f"SDK generation error: {e}")
            return False

    def export_postman_collection(self, output_path: Path) -> bool:
        """Export Postman collection for API testing."""
        try:
            spec = self.openapi_generator.generate_spec()
            
            # Convert OpenAPI to Postman collection format
            collection = self._openapi_to_postman(spec)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(collection, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"Postman collection exported to {output_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to export Postman collection: {e}")
            return False

    def _openapi_to_postman(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Convert OpenAPI spec to Postman collection format."""
        info = spec.get('info', {})
        servers = spec.get('servers', [])
        paths = spec.get('paths', {})

        # Get base_url from servers or from ComfyUI server
        if servers:
            base_url = servers[0]['url']
        else:
            try:
                from utils.comfyui_utils import get_comfyui_server_url
                base_url = get_comfyui_server_url()
            except Exception:
                base_url = 'http://127.0.0.1:8188'
        
        collection = {
            'info': {
                'name': info.get('title', 'PromptManager API'),
                'description': info.get('description', ''),
                'version': info.get('version', '1.0.0'),
                'schema': 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json'
            },
            'variable': [
                {
                    'key': 'baseUrl',
                    'value': base_url
                }
            ],
            'item': []
        }
        
        # Convert paths to Postman requests
        for path, methods in paths.items():
            folder = {
                'name': path,
                'item': []
            }
            
            for method, operation in methods.items():
                request = {
                    'name': operation.get('summary', f'{method.upper()} {path}'),
                    'request': {
                        'method': method.upper(),
                        'url': {
                            'raw': f'{{{{baseUrl}}}}{path}',
                            'host': ['{{baseUrl}}'],
                            'path': path.strip('/').split('/')
                        },
                        'header': [
                            {
                                'key': 'Content-Type',
                                'value': 'application/json'
                            }
                        ]
                    }
                }
                
                # Add request body for POST/PUT requests
                if 'requestBody' in operation:
                    request['request']['body'] = {
                        'mode': 'raw',
                        'raw': '{}',
                        'options': {
                            'raw': {
                                'language': 'json'
                            }
                        }
                    }
                
                folder['item'].append(request)
            
            collection['item'].append(folder)
        
        return collection

    def get_documentation_stats(self) -> Dict[str, Any]:
        """Get documentation statistics."""
        try:
            spec = self.openapi_generator.generate_spec()
            
            paths = spec.get('paths', {})
            schemas = spec.get('components', {}).get('schemas', {})
            
            endpoint_count = sum(len(methods) for methods in paths.values())
            
            return {
                'paths_count': len(paths),
                'endpoints_count': endpoint_count,
                'schemas_count': len(schemas),
                'openapi_version': spec.get('openapi'),
                'api_version': spec.get('info', {}).get('version'),
                'servers_count': len(spec.get('servers', [])),
                'tags_count': len(spec.get('tags', []))
            }
        except Exception as e:
            self.logger.error(f"Failed to get documentation stats: {e}")
            return {'error': str(e)}


# Global documentation server instance  
_documentation_server: Optional[DocumentationServer] = None

def get_documentation_server() -> DocumentationServer:
    """Get or create global documentation server instance."""
    global _documentation_server
    if _documentation_server is None:
        _documentation_server = DocumentationServer()
        # Add routes to ComfyUI server
        _documentation_server.add_routes_to_server()
    return _documentation_server


def initialize_documentation():
    """Initialize documentation server and register routes."""
    server = get_documentation_server()
    return server


if __name__ == '__main__':
    # Test documentation generation
    docs_server = DocumentationServer()
    
    # Generate and save OpenAPI spec
    spec_path = Path(__file__).parent / 'openapi.json'
    docs_server.openapi_generator.save_spec(spec_path)
    print(f"OpenAPI specification saved to {spec_path}")
    
    # Test HTML generation
    swagger_html = docs_server.swagger_handler.get_swagger_ui_html()
    redoc_html = docs_server.redoc_handler.get_redoc_html()
    
    print("Documentation HTML generated successfully")
    print("Available endpoints:")
    print("  - /prompt_manager/docs (Swagger UI)")
    print("  - /prompt_manager/redoc (ReDoc)")
    print("  - /prompt_manager/openapi.json (JSON spec)")
    print("  - /prompt_manager/openapi.yaml (YAML spec)")