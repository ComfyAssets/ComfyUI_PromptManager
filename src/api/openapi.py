"""OpenAPI/Swagger documentation for API endpoints.

This module generates OpenAPI specifications for the API
endpoints and provides documentation utilities.
"""

from typing import Any, Dict, List

# OpenAPI specification
OPENAPI_SPEC = {
    "openapi": "3.0.0",
    "info": {
        "title": "PromptManager API",
        "version": "2.0.0",
        "description": "ComfyUI PromptManager REST API for managing prompts and images",
        "contact": {
            "name": "API Support",
            "email": "support@promptmanager.com"
        },
        "license": {
            "name": "MIT",
            "url": "https://opensource.org/licenses/MIT"
        }
    },
    "servers": [
        {
            "url": "http://localhost:8000/api/v1",
            "description": "Development server"
        },
        {
            "url": "http://localhost:8001/api/v1",
            "description": "Production server"
        }
    ],
    "tags": [
        {
            "name": "prompts",
            "description": "Prompt management operations"
        },
        {
            "name": "images",
            "description": "Image management operations"
        },
        {
            "name": "system",
            "description": "System operations"
        }
    ],
    "paths": {
        # Prompt endpoints
        "/prompts": {
            "get": {
                "tags": ["prompts"],
                "summary": "List prompts",
                "description": "List prompts with pagination and filtering",
                "parameters": [
                    {
                        "name": "page",
                        "in": "query",
                        "schema": {"type": "integer", "default": 1},
                        "description": "Page number"
                    },
                    {
                        "name": "per_page",
                        "in": "query",
                        "schema": {"type": "integer", "default": 20, "maximum": 100},
                        "description": "Items per page"
                    },
                    {
                        "name": "sort_by",
                        "in": "query",
                        "schema": {
                            "type": "string",
                            "enum": ["created_at", "updated_at", "rating", "execution_count"]
                        },
                        "description": "Sort field"
                    },
                    {
                        "name": "sort_desc",
                        "in": "query",
                        "schema": {"type": "boolean", "default": False},
                        "description": "Sort descending"
                    },
                    {
                        "name": "category",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "Filter by category"
                    },
                    {
                        "name": "min_rating",
                        "in": "query",
                        "schema": {"type": "integer", "minimum": 1, "maximum": 5},
                        "description": "Minimum rating filter"
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Success",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/PromptList"}
                            }
                        }
                    }
                }
            },
            "post": {
                "tags": ["prompts"],
                "summary": "Create prompt",
                "description": "Create new prompt",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/PromptCreate"}
                        }
                    }
                },
                "responses": {
                    "201": {
                        "description": "Created",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Prompt"}
                            }
                        }
                    },
                    "400": {
                        "description": "Bad request",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Error"}
                            }
                        }
                    }
                }
            }
        },
        "/prompts/{id}": {
            "get": {
                "tags": ["prompts"],
                "summary": "Get prompt",
                "description": "Get single prompt by ID",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                        "description": "Prompt ID"
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Success",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Prompt"}
                            }
                        }
                    },
                    "404": {
                        "description": "Not found",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Error"}
                            }
                        }
                    }
                }
            },
            "put": {
                "tags": ["prompts"],
                "summary": "Update prompt",
                "description": "Update existing prompt",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                        "description": "Prompt ID"
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/PromptUpdate"}
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "Success",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Prompt"}
                            }
                        }
                    }
                }
            },
            "delete": {
                "tags": ["prompts"],
                "summary": "Delete prompt",
                "description": "Delete prompt",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                        "description": "Prompt ID"
                    }
                ],
                "responses": {
                    "204": {
                        "description": "No content"
                    },
                    "404": {
                        "description": "Not found"
                    }
                }
            }
        },
        "/prompts/search": {
            "get": {
                "tags": ["prompts"],
                "summary": "Search prompts",
                "description": "Search prompts by text",
                "parameters": [
                    {
                        "name": "q",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                        "description": "Search query"
                    },
                    {
                        "name": "fields",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "Comma-separated fields to search"
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Success",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/PromptList"}
                            }
                        }
                    }
                }
            }
        },
        "/prompts/{id}/rate": {
            "post": {
                "tags": ["prompts"],
                "summary": "Rate prompt",
                "description": "Rate a prompt",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                        "description": "Prompt ID"
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "rating": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "maximum": 5
                                    }
                                },
                                "required": ["rating"]
                            }
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "Success",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Prompt"}
                            }
                        }
                    }
                }
            }
        },
        # Image endpoints
        "/images": {
            "get": {
                "tags": ["images"],
                "summary": "List images",
                "description": "List images with pagination and filtering",
                "parameters": [
                    {
                        "name": "page",
                        "in": "query",
                        "schema": {"type": "integer", "default": 1}
                    },
                    {
                        "name": "per_page",
                        "in": "query",
                        "schema": {"type": "integer", "default": 20}
                    },
                    {
                        "name": "checkpoint",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "Filter by checkpoint"
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Success",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ImageList"}
                            }
                        }
                    }
                }
            },
            "post": {
                "tags": ["images"],
                "summary": "Create image",
                "description": "Create new image record",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ImageCreate"}
                        }
                    }
                },
                "responses": {
                    "201": {
                        "description": "Created",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Image"}
                            }
                        }
                    }
                }
            }
        },
        "/images/{id}/file": {
            "get": {
                "tags": ["images"],
                "summary": "Get image file",
                "description": "Get actual image file",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"}
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Success",
                        "content": {
                            "image/png": {},
                            "image/jpeg": {}
                        }
                    }
                }
            }
        },
        "/images/{id}/thumbnail": {
            "get": {
                "tags": ["images"],
                "summary": "Get thumbnail",
                "description": "Get image thumbnail",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"}
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Success",
                        "content": {
                            "image/png": {},
                            "image/jpeg": {}
                        }
                    }
                }
            }
        },
        # WebSocket endpoint
        "/ws": {
            "get": {
                "tags": ["system"],
                "summary": "WebSocket",
                "description": "WebSocket endpoint for real-time updates",
                "responses": {
                    "101": {
                        "description": "Switching Protocols"
                    }
                }
            }
        }
    },
    "components": {
        "schemas": {
            "Prompt": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "prompt": {"type": "string"},
                    "negative_prompt": {"type": "string"},
                    "category": {"type": "string"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "rating": {"type": "number"},
                    "execution_count": {"type": "integer"},
                    "notes": {"type": "string"},
                    "hash": {"type": "string"},
                    "created_at": {"type": "string", "format": "date-time"},
                    "updated_at": {"type": "string", "format": "date-time"}
                }
            },
            "PromptCreate": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "negative_prompt": {"type": "string"},
                    "category": {"type": "string"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "rating": {"type": "integer", "minimum": 1, "maximum": 5},
                    "notes": {"type": "string"}
                },
                "required": ["prompt"]
            },
            "PromptUpdate": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "negative_prompt": {"type": "string"},
                    "category": {"type": "string"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "rating": {"type": "integer", "minimum": 1, "maximum": 5},
                    "notes": {"type": "string"}
                }
            },
            "PromptList": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "data": {
                        "type": "object",
                        "properties": {
                            "items": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/Prompt"}
                            },
                            "pagination": {
                                "type": "object",
                                "properties": {
                                    "page": {"type": "integer"},
                                    "per_page": {"type": "integer"},
                                    "total": {"type": "integer"},
                                    "pages": {"type": "integer"},
                                    "has_next": {"type": "boolean"},
                                    "has_prev": {"type": "boolean"}
                                }
                            }
                        }
                    }
                }
            },
            "Image": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "file_path": {"type": "string"},
                    "thumbnail_path": {"type": "string"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                    "file_size": {"type": "integer"},
                    "format": {"type": "string"},
                    "checkpoint": {"type": "string"},
                    "prompt_text": {"type": "string"},
                    "negative_prompt": {"type": "string"},
                    "seed": {"type": "integer"},
                    "steps": {"type": "integer"},
                    "cfg_scale": {"type": "number"},
                    "sampler": {"type": "string"},
                    "hash": {"type": "string"},
                    "created_at": {"type": "string", "format": "date-time"},
                    "updated_at": {"type": "string", "format": "date-time"}
                }
            },
            "ImageCreate": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                    "checkpoint": {"type": "string"},
                    "prompt_text": {"type": "string"},
                    "negative_prompt": {"type": "string"},
                    "seed": {"type": "integer"},
                    "steps": {"type": "integer"},
                    "cfg_scale": {"type": "number"},
                    "sampler": {"type": "string"}
                },
                "required": ["file_path", "width", "height"]
            },
            "ImageList": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "data": {
                        "type": "object",
                        "properties": {
                            "items": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/Image"}
                            },
                            "pagination": {
                                "$ref": "#/components/schemas/PromptList/properties/data/properties/pagination"
                            }
                        }
                    }
                }
            },
            "Error": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "default": False},
                    "error": {"type": "string"},
                    "details": {"type": "object"}
                }
            }
        }
    }
}


def get_openapi_spec() -> Dict[str, Any]:
    """Get OpenAPI specification.
    
    Returns:
        OpenAPI specification dictionary
    """
    return OPENAPI_SPEC


def get_swagger_ui_html(spec_url: str = "/api/v1/openapi.json") -> str:
    """Generate Swagger UI HTML.
    
    Args:
        spec_url: URL to OpenAPI spec
        
    Returns:
        HTML string for Swagger UI
    """
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>PromptManager API Documentation</title>
        <link rel="stylesheet" type="text/css" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
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
                margin: 0;
                background: #fafafa;
            }}
        </style>
    </head>
    <body>
        <div id="swagger-ui"></div>
        <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-standalone-preset.js"></script>
        <script>
            window.onload = function() {{
                window.ui = SwaggerUIBundle({{
                    url: "{spec_url}",
                    dom_id: '#swagger-ui',
                    deepLinking: true,
                    presets: [
                        SwaggerUIBundle.presets.apis,
                        SwaggerUIStandalonePreset
                    ],
                    plugins: [
                        SwaggerUIBundle.plugins.DownloadUrl
                    ],
                    layout: "StandaloneLayout"
                }})
            }}
        </script>
    </body>
    </html>
    """


def get_redoc_html(spec_url: str = "/api/v1/openapi.json") -> str:
    """Generate ReDoc HTML.
    
    Args:
        spec_url: URL to OpenAPI spec
        
    Returns:
        HTML string for ReDoc
    """
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>PromptManager API Documentation</title>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                margin: 0;
                padding: 0;
            }}
        </style>
    </head>
    <body>
        <redoc spec-url="{spec_url}"></redoc>
        <script src="https://cdn.jsdelivr.net/npm/redoc/bundles/redoc.standalone.js"></script>
    </body>
    </html>
    """