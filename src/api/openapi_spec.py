"""OpenAPI specification generator for ComfyUI PromptManager API.

This module generates comprehensive OpenAPI 3.0 specification for the
PromptManager REST API, including interactive documentation and client SDKs.

The specification includes:
- All API endpoints with request/response schemas
- Authentication flows (when enabled)
- Rate limiting documentation
- WebSocket event documentation
- Error response schemas
- Example requests and responses

Classes:
    OpenAPIGenerator: Main specification generator
    SchemaBuilder: Builds OpenAPI schemas from Pydantic models
    ExampleGenerator: Generates realistic API examples

Example:
    generator = OpenAPIGenerator()
    spec = generator.generate_spec()
    # Spec can be served via /docs endpoint
"""

import json
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from pathlib import Path

# OpenAPI specification structure
OPENAPI_VERSION = "3.0.3"
API_VERSION = "1.0.0"


@dataclass
class APIInfo:
    """API information for OpenAPI spec."""
    title: str
    description: str
    version: str
    contact: Optional[Dict[str, str]] = None
    license: Optional[Dict[str, str]] = None


@dataclass
class ServerInfo:
    """Server information for OpenAPI spec."""
    url: str
    description: str


class SchemaBuilder:
    """Builds OpenAPI schemas and components."""

    def __init__(self):
        self.schemas = {}

    def build_schemas(self) -> Dict[str, Dict[str, Any]]:
        """Build all OpenAPI schemas."""
        return {
            # Response schemas
            'SuccessResponse': self._success_response_schema(),
            'ErrorResponse': self._error_response_schema(),
            'PaginationResponse': self._pagination_response_schema(),
            
            # Prompt schemas  
            'Prompt': self._prompt_schema(),
            'PromptCreate': self._prompt_create_schema(),
            'PromptUpdate': self._prompt_update_schema(),
            'PromptList': self._prompt_list_schema(),
            
            # Image schemas
            'Image': self._image_schema(),
            'ImageList': self._image_list_schema(),
            'ImageMetadata': self._image_metadata_schema(),
            
            # Execution schemas
            'ExecutionStatus': self._execution_status_schema(),
            'ExecutionUpdate': self._execution_update_schema(),
            
            # Statistics schemas
            'Statistics': self._statistics_schema(),
            
            # Bulk operation schemas
            'BulkDeleteRequest': self._bulk_delete_request_schema(),
            'BulkDeleteResponse': self._bulk_delete_response_schema()
        }

    def _success_response_schema(self) -> Dict[str, Any]:
        """Standard success response schema."""
        return {
            'type': 'object',
            'properties': {
                'status': {
                    'type': 'string',
                    'enum': ['success'],
                    'description': 'Response status'
                },
                'data': {
                    'description': 'Response data',
                    'oneOf': [
                        {'type': 'object'},
                        {'type': 'array'},
                        {'type': 'string'},
                        {'type': 'number'},
                        {'type': 'boolean'}
                    ]
                },
                'message': {
                    'type': 'string',
                    'description': 'Optional success message'
                }
            },
            'required': ['status']
        }

    def _error_response_schema(self) -> Dict[str, Any]:
        """Standard error response schema."""
        return {
            'type': 'object',
            'properties': {
                'status': {
                    'type': 'string',
                    'enum': ['error'],
                    'description': 'Response status'
                },
                'message': {
                    'type': 'string',
                    'description': 'Error message'
                },
                'error_id': {
                    'type': 'string',
                    'description': 'Unique error identifier for tracking'
                },
                'details': {
                    'type': 'object',
                    'description': 'Additional error details'
                }
            },
            'required': ['status', 'message']
        }

    def _pagination_response_schema(self) -> Dict[str, Any]:
        """Pagination metadata schema."""
        return {
            'type': 'object',
            'properties': {
                'page': {
                    'type': 'integer',
                    'minimum': 1,
                    'description': 'Current page number'
                },
                'per_page': {
                    'type': 'integer',
                    'minimum': 1,
                    'maximum': 100,
                    'description': 'Items per page'
                },
                'has_more': {
                    'type': 'boolean',
                    'description': 'Whether more pages are available'
                },
                'total': {
                    'type': 'integer',
                    'minimum': 0,
                    'description': 'Total number of items (when available)'
                }
            },
            'required': ['page', 'per_page', 'has_more']
        }

    def _prompt_schema(self) -> Dict[str, Any]:
        """Prompt object schema."""
        return {
            'type': 'object',
            'properties': {
                'id': {
                    'type': 'integer',
                    'description': 'Unique prompt ID'
                },
                'name': {
                    'type': 'string',
                    'maxLength': 200,
                    'description': 'Prompt name'
                },
                'positive_prompt': {
                    'type': 'string',
                    'maxLength': 10000,
                    'description': 'Positive prompt text'
                },
                'negative_prompt': {
                    'type': 'string',
                    'maxLength': 10000,
                    'description': 'Negative prompt text'
                },
                'category': {
                    'type': 'string',
                    'maxLength': 100,
                    'description': 'Prompt category'
                },
                'description': {
                    'type': 'string',
                    'maxLength': 1000,
                    'description': 'Prompt description'
                },
                'tags': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Prompt tags'
                },
                'rating': {
                    'type': 'integer',
                    'minimum': 1,
                    'maximum': 5,
                    'description': 'Prompt rating (1-5 stars)'
                },
                'created_at': {
                    'type': 'string',
                    'format': 'date-time',
                    'description': 'Creation timestamp'
                },
                'updated_at': {
                    'type': 'string',
                    'format': 'date-time',
                    'description': 'Last update timestamp'
                },
                'usage_count': {
                    'type': 'integer',
                    'minimum': 0,
                    'description': 'Number of times used'
                }
            },
            'required': ['id', 'name', 'positive_prompt', 'created_at']
        }

    def _prompt_create_schema(self) -> Dict[str, Any]:
        """Prompt creation schema."""
        return {
            'type': 'object',
            'properties': {
                'name': {
                    'type': 'string',
                    'maxLength': 200,
                    'description': 'Prompt name'
                },
                'positive_prompt': {
                    'type': 'string',
                    'maxLength': 10000,
                    'description': 'Positive prompt text'
                },
                'negative_prompt': {
                    'type': 'string',
                    'maxLength': 10000,
                    'description': 'Negative prompt text'
                },
                'category': {
                    'type': 'string',
                    'maxLength': 100,
                    'description': 'Prompt category'
                },
                'description': {
                    'type': 'string',
                    'maxLength': 1000,
                    'description': 'Prompt description'
                },
                'tags': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Prompt tags'
                },
                'rating': {
                    'type': 'integer',
                    'minimum': 1,
                    'maximum': 5,
                    'description': 'Prompt rating (1-5 stars)'
                }
            },
            'required': ['name', 'positive_prompt']
        }

    def _prompt_update_schema(self) -> Dict[str, Any]:
        """Prompt update schema (all fields optional)."""
        schema = self._prompt_create_schema()
        schema['required'] = []  # All fields optional for updates
        return schema

    def _prompt_list_schema(self) -> Dict[str, Any]:
        """Prompt list response schema."""
        return {
            'allOf': [
                {'$ref': '#/components/schemas/SuccessResponse'},
                {
                    'type': 'object',
                    'properties': {
                        'data': {
                            'type': 'array',
                            'items': {'$ref': '#/components/schemas/Prompt'}
                        },
                        'pagination': {
                            '$ref': '#/components/schemas/PaginationResponse'
                        }
                    }
                }
            ]
        }

    def _image_schema(self) -> Dict[str, Any]:
        """Image object schema."""
        return {
            'type': 'object',
            'properties': {
                'id': {
                    'type': 'integer',
                    'description': 'Unique image ID'
                },
                'filename': {
                    'type': 'string',
                    'description': 'Image filename'
                },
                'path': {
                    'type': 'string',
                    'description': 'Image file path'
                },
                'thumbnail_path': {
                    'type': 'string',
                    'description': 'Thumbnail file path'
                },
                'prompt_id': {
                    'type': 'integer',
                    'description': 'Associated prompt ID'
                },
                'execution_id': {
                    'type': 'string',
                    'description': 'ComfyUI execution ID'
                },
                'width': {
                    'type': 'integer',
                    'minimum': 1,
                    'description': 'Image width in pixels'
                },
                'height': {
                    'type': 'integer',
                    'minimum': 1,
                    'description': 'Image height in pixels'
                },
                'file_size': {
                    'type': 'integer',
                    'minimum': 0,
                    'description': 'File size in bytes'
                },
                'format': {
                    'type': 'string',
                    'enum': ['PNG', 'JPEG', 'WEBP'],
                    'description': 'Image format'
                },
                'created_at': {
                    'type': 'string',
                    'format': 'date-time',
                    'description': 'Creation timestamp'
                },
                'metadata': {
                    '$ref': '#/components/schemas/ImageMetadata'
                }
            },
            'required': ['id', 'filename', 'path', 'created_at']
        }

    def _image_list_schema(self) -> Dict[str, Any]:
        """Image list response schema."""
        return {
            'allOf': [
                {'$ref': '#/components/schemas/SuccessResponse'},
                {
                    'type': 'object',
                    'properties': {
                        'data': {
                            'type': 'array',
                            'items': {'$ref': '#/components/schemas/Image'}
                        },
                        'pagination': {
                            '$ref': '#/components/schemas/PaginationResponse'
                        }
                    }
                }
            ]
        }

    def _image_metadata_schema(self) -> Dict[str, Any]:
        """Image metadata schema."""
        return {
            'type': 'object',
            'properties': {
                'prompt': {
                    'type': 'string',
                    'description': 'Positive prompt text'
                },
                'negative_prompt': {
                    'type': 'string',
                    'description': 'Negative prompt text'
                },
                'seed': {
                    'type': 'integer',
                    'description': 'Generation seed'
                },
                'steps': {
                    'type': 'integer',
                    'description': 'Sampling steps'
                },
                'cfg': {
                    'type': 'number',
                    'description': 'CFG scale'
                },
                'sampler_name': {
                    'type': 'string',
                    'description': 'Sampler name'
                },
                'scheduler': {
                    'type': 'string',
                    'description': 'Scheduler name'
                },
                'model': {
                    'type': 'string',
                    'description': 'Model checkpoint name'
                },
                'denoise': {
                    'type': 'number',
                    'description': 'Denoise strength'
                },
                'workflow': {
                    'type': 'object',
                    'description': 'ComfyUI workflow data'
                }
            }
        }

    def _execution_status_schema(self) -> Dict[str, Any]:
        """Execution status schema."""
        return {
            'type': 'object',
            'properties': {
                'prompt_id': {
                    'type': 'string',
                    'description': 'ComfyUI prompt ID'
                },
                'state': {
                    'type': 'string',
                    'enum': ['queued', 'executing', 'completed', 'failed', 'cancelled'],
                    'description': 'Execution state'
                },
                'progress': {
                    'type': 'number',
                    'minimum': 0,
                    'maximum': 1,
                    'description': 'Execution progress (0-1)'
                },
                'current_node': {
                    'type': 'string',
                    'description': 'Currently executing node ID'
                },
                'execution_time': {
                    'type': 'number',
                    'description': 'Execution time in seconds'
                },
                'images_count': {
                    'type': 'integer',
                    'minimum': 0,
                    'description': 'Number of images generated'
                },
                'errors': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Error messages'
                }
            },
            'required': ['prompt_id', 'state']
        }

    def _execution_update_schema(self) -> Dict[str, Any]:
        """WebSocket execution update schema."""
        return {
            'type': 'object',
            'properties': {
                'event': {
                    'type': 'string',
                    'enum': ['prompt_manager_execution_update'],
                    'description': 'WebSocket event type'
                },
                'data': {
                    '$ref': '#/components/schemas/ExecutionStatus'
                }
            },
            'required': ['event', 'data']
        }

    def _statistics_schema(self) -> Dict[str, Any]:
        """System statistics schema."""
        return {
            'type': 'object',
            'properties': {
                'prompts': {
                    'type': 'object',
                    'properties': {
                        'total': {'type': 'integer'},
                        'by_category': {'type': 'object'},
                        'by_rating': {'type': 'object'}
                    }
                },
                'images': {
                    'type': 'object',
                    'properties': {
                        'total': {'type': 'integer'},
                        'total_size': {'type': 'integer'},
                        'by_format': {'type': 'object'},
                        'recent_count': {'type': 'integer'}
                    }
                },
                'executions': {
                    'type': 'object',
                    'properties': {
                        'total': {'type': 'integer'},
                        'success_rate': {'type': 'number'},
                        'avg_execution_time': {'type': 'number'},
                        'recent_count': {'type': 'integer'}
                    }
                },
                'system': {
                    'type': 'object',
                    'properties': {
                        'database_size': {'type': 'integer'},
                        'cache_hit_rate': {'type': 'number'},
                        'uptime': {'type': 'number'}
                    }
                }
            }
        }

    def _bulk_delete_request_schema(self) -> Dict[str, Any]:
        """Bulk delete request schema."""
        return {
            'type': 'object',
            'properties': {
                'type': {
                    'type': 'string',
                    'enum': ['prompts', 'images'],
                    'description': 'Resource type to delete'
                },
                'ids': {
                    'type': 'array',
                    'items': {'type': 'integer'},
                    'minItems': 1,
                    'maxItems': 1000,
                    'description': 'IDs to delete'
                }
            },
            'required': ['type', 'ids']
        }

    def _bulk_delete_response_schema(self) -> Dict[str, Any]:
        """Bulk delete response schema."""
        return {
            'allOf': [
                {'$ref': '#/components/schemas/SuccessResponse'},
                {
                    'type': 'object',
                    'properties': {
                        'data': {
                            'type': 'object',
                            'properties': {
                                'deleted_count': {
                                    'type': 'integer',
                                    'minimum': 0,
                                    'description': 'Number of items deleted'
                                }
                            },
                            'required': ['deleted_count']
                        }
                    }
                }
            ]
        }


class ExampleGenerator:
    """Generates realistic API examples."""

    def generate_examples(self) -> Dict[str, Dict[str, Any]]:
        """Generate all API examples."""
        return {
            'prompt_example': self._prompt_example(),
            'prompt_create_example': self._prompt_create_example(),
            'image_example': self._image_example(),
            'execution_status_example': self._execution_status_example(),
            'statistics_example': self._statistics_example(),
            'error_example': self._error_example()
        }

    def _prompt_example(self) -> Dict[str, Any]:
        """Example prompt object."""
        return {
            "id": 123,
            "name": "Fantasy Portrait",
            "positive_prompt": "beautiful fantasy portrait, detailed face, magical lighting, digital art, masterpiece",
            "negative_prompt": "blurry, low quality, distorted, ugly",
            "category": "Portrait",
            "description": "High quality fantasy portrait with magical elements",
            "tags": ["fantasy", "portrait", "magical", "detailed"],
            "rating": 5,
            "created_at": "2024-01-15T10:30:00Z",
            "updated_at": "2024-01-15T10:30:00Z",
            "usage_count": 15
        }

    def _prompt_create_example(self) -> Dict[str, Any]:
        """Example prompt creation request."""
        return {
            "name": "Cyberpunk Cityscape",
            "positive_prompt": "futuristic cyberpunk city, neon lights, flying cars, detailed architecture",
            "negative_prompt": "blurry, low quality, bad composition",
            "category": "Cityscape",
            "description": "Futuristic cyberpunk city scene",
            "tags": ["cyberpunk", "futuristic", "city", "neon"],
            "rating": 4
        }

    def _image_example(self) -> Dict[str, Any]:
        """Example image object."""
        return {
            "id": 456,
            "filename": "ComfyUI_00123_.png",
            "path": "/output/ComfyUI_00123_.png",
            "thumbnail_path": "/thumbnails/ComfyUI_00123__thumb.jpg",
            "prompt_id": 123,
            "execution_id": "prompt_123456789",
            "width": 512,
            "height": 512,
            "file_size": 245760,
            "format": "PNG",
            "created_at": "2024-01-15T10:35:00Z",
            "metadata": {
                "prompt": "beautiful fantasy portrait, detailed face",
                "seed": 12345,
                "steps": 20,
                "cfg": 7.5,
                "sampler_name": "DPM++ 2M Karras",
                "model": "dreamshaper_8.safetensors"
            }
        }

    def _execution_status_example(self) -> Dict[str, Any]:
        """Example execution status."""
        return {
            "prompt_id": "prompt_123456789",
            "state": "executing",
            "progress": 0.65,
            "current_node": "3",
            "execution_time": 12.5,
            "images_count": 0,
            "errors": []
        }

    def _statistics_example(self) -> Dict[str, Any]:
        """Example statistics response."""
        return {
            "prompts": {
                "total": 1250,
                "by_category": {
                    "Portrait": 350,
                    "Landscape": 280,
                    "Abstract": 120,
                    "Fantasy": 500
                },
                "by_rating": {
                    "5": 400,
                    "4": 320,
                    "3": 180,
                    "2": 50,
                    "1": 10
                }
            },
            "images": {
                "total": 5680,
                "total_size": 2147483648,
                "by_format": {
                    "PNG": 4500,
                    "JPEG": 1100,
                    "WEBP": 80
                },
                "recent_count": 45
            },
            "executions": {
                "total": 3450,
                "success_rate": 0.96,
                "avg_execution_time": 18.5,
                "recent_count": 12
            },
            "system": {
                "database_size": 52428800,
                "cache_hit_rate": 0.85,
                "uptime": 86400
            }
        }

    def _error_example(self) -> Dict[str, Any]:
        """Example error response."""
        return {
            "status": "error",
            "message": "Prompt not found",
            "error_id": "err_1642251600123"
        }


class OpenAPIGenerator:
    """Main OpenAPI specification generator."""

    def __init__(self):
        self.schema_builder = SchemaBuilder()
        self.example_generator = ExampleGenerator()

    def generate_spec(self) -> Dict[str, Any]:
        """Generate complete OpenAPI specification."""
        return {
            'openapi': OPENAPI_VERSION,
            'info': self._get_api_info(),
            'servers': self._get_servers(),
            'paths': self._get_paths(),
            'components': self._get_components(),
            'tags': self._get_tags(),
            'externalDocs': self._get_external_docs()
        }

    def _get_api_info(self) -> Dict[str, Any]:
        """Get API information."""
        return {
            'title': 'ComfyUI PromptManager API',
            'description': '''
Comprehensive REST API for ComfyUI PromptManager - a professional prompt and image management system.

## Features

- **Prompt Management**: Complete CRUD operations for prompts with categories, tags, and ratings
- **Image Gallery**: Automated image tracking with metadata extraction and thumbnails
- **Real-time Updates**: WebSocket integration for live execution tracking
- **Search & Filter**: Advanced search across prompts and images
- **Bulk Operations**: Efficient bulk delete and management operations
- **Statistics**: Comprehensive system statistics and analytics

## Authentication

Authentication is optional and typically disabled for local ComfyUI installations. 
When enabled, use Bearer token authentication:

```
Authorization: Bearer <your-token>
```

## Rate Limiting

API requests are rate limited using a token bucket algorithm:
- Default: 100 requests per minute per client
- Burst: Up to 100 requests at once
- Headers indicate remaining quota

## WebSocket Events

Real-time updates are sent via ComfyUI's existing WebSocket connection:
- `prompt_manager_execution_update`: Execution progress updates
- Connect to ComfyUI's WebSocket at `/ws`

## Error Handling

All errors follow a consistent format with HTTP status codes:
- `400` - Bad Request (validation errors)
- `401` - Unauthorized (authentication required)  
- `403` - Forbidden (insufficient permissions)
- `404` - Not Found (resource doesn't exist)
- `413` - Request Too Large (file/request size limits)
- `429` - Too Many Requests (rate limiting)
- `500` - Internal Server Error (server issues)
            ''',
            'version': API_VERSION,
            'contact': {
                'name': 'PromptManager Support',
                'url': 'https://github.com/ComfyUI/PromptManager'
            },
            'license': {
                'name': 'MIT',
                'url': 'https://opensource.org/licenses/MIT'
            }
        }

    def _get_servers(self) -> List[Dict[str, Any]]:
        """Get server configurations."""
        return [
            {
                'url': 'http://localhost:8188',
                'description': 'Local ComfyUI development server'
            },
            {
                'url': 'https://your-comfyui-server.com',
                'description': 'Production ComfyUI server'
            }
        ]

    def _get_paths(self) -> Dict[str, Any]:
        """Generate all API paths."""
        return {
            # Prompt endpoints
            '/prompt_manager/prompts': {
                'get': self._get_prompts_endpoint(),
                'post': self._create_prompt_endpoint()
            },
            '/prompt_manager/prompts/{id}': {
                'get': self._get_prompt_endpoint(),
                'put': self._update_prompt_endpoint(),
                'delete': self._delete_prompt_endpoint()
            },
            
            # Image endpoints
            '/prompt_manager/images': {
                'get': self._get_images_endpoint()
            },
            
            # Statistics endpoint
            '/prompt_manager/statistics': {
                'get': self._get_statistics_endpoint()
            },
            
            # Bulk operations
            '/prompt_manager/bulk/delete': {
                'post': self._bulk_delete_endpoint()
            },
            
            # Health check
            '/prompt_manager/health': {
                'get': self._health_check_endpoint()
            }
        }

    def _get_prompts_endpoint(self) -> Dict[str, Any]:
        """GET /prompts endpoint specification."""
        return {
            'summary': 'List prompts',
            'description': 'Get a paginated list of prompts with optional filtering and search',
            'tags': ['Prompts'],
            'parameters': [
                {
                    'name': 'page',
                    'in': 'query',
                    'description': 'Page number (1-based)',
                    'schema': {'type': 'integer', 'minimum': 1, 'default': 1}
                },
                {
                    'name': 'per_page',
                    'in': 'query',
                    'description': 'Items per page',
                    'schema': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 50}
                },
                {
                    'name': 'search',
                    'in': 'query',
                    'description': 'Search in prompt names and content',
                    'schema': {'type': 'string', 'maxLength': 200}
                },
                {
                    'name': 'category',
                    'in': 'query',
                    'description': 'Filter by category',
                    'schema': {'type': 'string', 'maxLength': 100}
                },
                {
                    'name': 'sort_by',
                    'in': 'query',
                    'description': 'Sort field',
                    'schema': {
                        'type': 'string',
                        'enum': ['name', 'created_at', 'updated_at', 'rating', 'usage_count'],
                        'default': 'created_at'
                    }
                },
                {
                    'name': 'sort_order',
                    'in': 'query',
                    'description': 'Sort order',
                    'schema': {
                        'type': 'string',
                        'enum': ['asc', 'desc'],
                        'default': 'desc'
                    }
                }
            ],
            'responses': {
                '200': {
                    'description': 'Successful response',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/PromptList'},
                            'example': {
                                'status': 'success',
                                'data': [self.example_generator._prompt_example()],
                                'pagination': {
                                    'page': 1,
                                    'per_page': 50,
                                    'has_more': True
                                }
                            }
                        }
                    }
                },
                '400': {
                    'description': 'Bad request',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/ErrorResponse'}
                        }
                    }
                },
                '500': {
                    'description': 'Internal server error',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/ErrorResponse'}
                        }
                    }
                }
            }
        }

    def _create_prompt_endpoint(self) -> Dict[str, Any]:
        """POST /prompts endpoint specification.""" 
        return {
            'summary': 'Create prompt',
            'description': 'Create a new prompt',
            'tags': ['Prompts'],
            'requestBody': {
                'required': True,
                'content': {
                    'application/json': {
                        'schema': {'$ref': '#/components/schemas/PromptCreate'},
                        'example': self.example_generator._prompt_create_example()
                    }
                }
            },
            'responses': {
                '201': {
                    'description': 'Prompt created successfully',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/SuccessResponse'},
                            'example': {
                                'status': 'success',
                                'data': {'id': 124},
                                'message': 'Prompt created successfully'
                            }
                        }
                    }
                },
                '400': {
                    'description': 'Validation error',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/ErrorResponse'}
                        }
                    }
                },
                '500': {
                    'description': 'Internal server error',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/ErrorResponse'}
                        }
                    }
                }
            }
        }

    def _get_prompt_endpoint(self) -> Dict[str, Any]:
        """GET /prompts/{id} endpoint specification."""
        return {
            'summary': 'Get prompt',
            'description': 'Get a specific prompt by ID',
            'tags': ['Prompts'],
            'parameters': [
                {
                    'name': 'id',
                    'in': 'path',
                    'required': True,
                    'description': 'Prompt ID',
                    'schema': {'type': 'integer', 'minimum': 1}
                }
            ],
            'responses': {
                '200': {
                    'description': 'Successful response',
                    'content': {
                        'application/json': {
                            'schema': {
                                'allOf': [
                                    {'$ref': '#/components/schemas/SuccessResponse'},
                                    {
                                        'type': 'object',
                                        'properties': {
                                            'data': {'$ref': '#/components/schemas/Prompt'}
                                        }
                                    }
                                ]
                            },
                            'example': {
                                'status': 'success',
                                'data': self.example_generator._prompt_example()
                            }
                        }
                    }
                },
                '404': {
                    'description': 'Prompt not found',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/ErrorResponse'}
                        }
                    }
                }
            }
        }

    def _update_prompt_endpoint(self) -> Dict[str, Any]:
        """PUT /prompts/{id} endpoint specification."""
        return {
            'summary': 'Update prompt',
            'description': 'Update an existing prompt',
            'tags': ['Prompts'],
            'parameters': [
                {
                    'name': 'id',
                    'in': 'path',
                    'required': True,
                    'description': 'Prompt ID',
                    'schema': {'type': 'integer', 'minimum': 1}
                }
            ],
            'requestBody': {
                'required': True,
                'content': {
                    'application/json': {
                        'schema': {'$ref': '#/components/schemas/PromptUpdate'}
                    }
                }
            },
            'responses': {
                '200': {
                    'description': 'Prompt updated successfully',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/SuccessResponse'},
                            'example': {
                                'status': 'success',
                                'message': 'Prompt updated successfully'
                            }
                        }
                    }
                },
                '400': {'$ref': '#/components/responses/ValidationError'},
                '404': {'$ref': '#/components/responses/NotFound'}
            }
        }

    def _delete_prompt_endpoint(self) -> Dict[str, Any]:
        """DELETE /prompts/{id} endpoint specification."""
        return {
            'summary': 'Delete prompt',
            'description': 'Delete a specific prompt',
            'tags': ['Prompts'],
            'parameters': [
                {
                    'name': 'id',
                    'in': 'path',
                    'required': True,
                    'description': 'Prompt ID',
                    'schema': {'type': 'integer', 'minimum': 1}
                }
            ],
            'responses': {
                '200': {
                    'description': 'Prompt deleted successfully',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/SuccessResponse'}
                        }
                    }
                },
                '404': {'$ref': '#/components/responses/NotFound'}
            }
        }

    def _get_images_endpoint(self) -> Dict[str, Any]:
        """GET /images endpoint specification."""
        return {
            'summary': 'List images',
            'description': 'Get a paginated list of images with optional filtering',
            'tags': ['Images'],
            'parameters': [
                {
                    'name': 'page',
                    'in': 'query',
                    'description': 'Page number',
                    'schema': {'type': 'integer', 'minimum': 1, 'default': 1}
                },
                {
                    'name': 'per_page',
                    'in': 'query', 
                    'description': 'Items per page',
                    'schema': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 50}
                },
                {
                    'name': 'prompt_id',
                    'in': 'query',
                    'description': 'Filter by prompt ID',
                    'schema': {'type': 'integer', 'minimum': 1}
                },
                {
                    'name': 'date_from',
                    'in': 'query',
                    'description': 'Filter images from date (ISO 8601)',
                    'schema': {'type': 'string', 'format': 'date-time'}
                },
                {
                    'name': 'date_to',
                    'in': 'query',
                    'description': 'Filter images to date (ISO 8601)',
                    'schema': {'type': 'string', 'format': 'date-time'}
                }
            ],
            'responses': {
                '200': {
                    'description': 'Successful response',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/ImageList'},
                            'example': {
                                'status': 'success',
                                'data': [self.example_generator._image_example()],
                                'pagination': {
                                    'page': 1,
                                    'per_page': 50,
                                    'has_more': False
                                }
                            }
                        }
                    }
                }
            }
        }

    def _get_statistics_endpoint(self) -> Dict[str, Any]:
        """GET /statistics endpoint specification."""
        return {
            'summary': 'Get system statistics',
            'description': 'Get comprehensive system statistics and analytics',
            'tags': ['System'],
            'responses': {
                '200': {
                    'description': 'Successful response',
                    'content': {
                        'application/json': {
                            'schema': {
                                'allOf': [
                                    {'$ref': '#/components/schemas/SuccessResponse'},
                                    {
                                        'type': 'object',
                                        'properties': {
                                            'data': {'$ref': '#/components/schemas/Statistics'}
                                        }
                                    }
                                ]
                            },
                            'example': {
                                'status': 'success',
                                'data': self.example_generator._statistics_example()
                            }
                        }
                    }
                }
            }
        }

    def _bulk_delete_endpoint(self) -> Dict[str, Any]:
        """POST /bulk/delete endpoint specification."""
        return {
            'summary': 'Bulk delete resources',
            'description': 'Delete multiple prompts or images in a single operation',
            'tags': ['Bulk Operations'],
            'requestBody': {
                'required': True,
                'content': {
                    'application/json': {
                        'schema': {'$ref': '#/components/schemas/BulkDeleteRequest'},
                        'example': {
                            'type': 'prompts',
                            'ids': [1, 2, 3, 4, 5]
                        }
                    }
                }
            },
            'responses': {
                '200': {
                    'description': 'Bulk delete completed',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/BulkDeleteResponse'},
                            'example': {
                                'status': 'success',
                                'data': {'deleted_count': 5},
                                'message': 'Deleted 5 prompts'
                            }
                        }
                    }
                }
            }
        }

    def _health_check_endpoint(self) -> Dict[str, Any]:
        """GET /health endpoint specification."""
        return {
            'summary': 'Health check',
            'description': 'Check API and system health status',
            'tags': ['System'],
            'responses': {
                '200': {
                    'description': 'System healthy',
                    'content': {
                        'application/json': {
                            'schema': {
                                'type': 'object',
                                'properties': {
                                    'status': {'type': 'string', 'enum': ['healthy', 'degraded']},
                                    'database': {'type': 'string', 'enum': ['healthy', 'unhealthy']},
                                    'circuit_breaker': {'type': 'string'},
                                    'metrics': {'type': 'object'}
                                }
                            },
                            'example': {
                                'status': 'healthy',
                                'database': 'healthy',
                                'circuit_breaker': 'closed',
                                'metrics': {
                                    'total_requests': 1250,
                                    'success_rate': 0.96
                                }
                            }
                        }
                    }
                },
                '503': {
                    'description': 'System unhealthy',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/ErrorResponse'}
                        }
                    }
                }
            }
        }

    def _get_components(self) -> Dict[str, Any]:
        """Get OpenAPI components."""
        return {
            'schemas': self.schema_builder.build_schemas(),
            'responses': {
                'ValidationError': {
                    'description': 'Validation error',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/ErrorResponse'}
                        }
                    }
                },
                'NotFound': {
                    'description': 'Resource not found',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/ErrorResponse'}
                        }
                    }
                },
                'Unauthorized': {
                    'description': 'Authentication required',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/ErrorResponse'}
                        }
                    }
                },
                'RateLimit': {
                    'description': 'Rate limit exceeded',
                    'content': {
                        'application/json': {
                            'schema': {'$ref': '#/components/schemas/ErrorResponse'}
                        }
                    }
                }
            },
            'securitySchemes': {
                'BearerAuth': {
                    'type': 'http',
                    'scheme': 'bearer',
                    'description': 'JWT Bearer token authentication (optional)'
                }
            }
        }

    def _get_tags(self) -> List[Dict[str, Any]]:
        """Get API tags."""
        return [
            {
                'name': 'Prompts',
                'description': 'Prompt management operations'
            },
            {
                'name': 'Images', 
                'description': 'Image gallery and metadata operations'
            },
            {
                'name': 'System',
                'description': 'System statistics and health monitoring'
            },
            {
                'name': 'Bulk Operations',
                'description': 'Bulk operations for efficiency'
            }
        ]

    def _get_external_docs(self) -> Dict[str, Any]:
        """Get external documentation links."""
        return {
            'description': 'ComfyUI PromptManager Documentation',
            'url': 'https://github.com/ComfyUI/PromptManager/docs'
        }

    def save_spec(self, file_path: Path):
        """Save OpenAPI spec to file."""
        spec = self.generate_spec()
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(spec, f, indent=2, ensure_ascii=False)

    def save_spec_yaml(self, file_path: Path):
        """Save OpenAPI spec as YAML."""
        try:
            import yaml
            spec = self.generate_spec()
            with open(file_path, 'w', encoding='utf-8') as f:
                yaml.dump(spec, f, default_flow_style=False, allow_unicode=True)
        except ImportError:
            raise ImportError("PyYAML is required to save YAML format")


def generate_openapi_spec() -> Dict[str, Any]:
    """Generate OpenAPI specification."""
    generator = OpenAPIGenerator()
    return generator.generate_spec()


def save_openapi_spec(file_path: Path, format: str = 'json'):
    """Save OpenAPI specification to file."""
    generator = OpenAPIGenerator()
    
    if format.lower() == 'yaml':
        generator.save_spec_yaml(file_path)
    else:
        generator.save_spec(file_path)


if __name__ == '__main__':
    # Generate and save specification
    generator = OpenAPIGenerator()
    spec_path = Path(__file__).parent / 'openapi.json'
    generator.save_spec(spec_path)
    print(f"OpenAPI specification saved to {spec_path}")