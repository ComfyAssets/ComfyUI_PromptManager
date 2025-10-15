"""Base controller for API endpoints.

This module provides shared functionality for all API controllers,
including error handling, response formatting, and common operations.
"""

import json
from typing import Any, Callable, Dict, List, Optional, Tuple
from functools import wraps

from ..core.validators import ValidationError

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.api.controller")


class BaseController:
    """Base class for API controllers with shared functionality."""
    
    # Standard HTTP status codes
    HTTP_OK = 200
    HTTP_CREATED = 201
    HTTP_NO_CONTENT = 204
    HTTP_BAD_REQUEST = 400
    HTTP_UNAUTHORIZED = 401
    HTTP_FORBIDDEN = 403
    HTTP_NOT_FOUND = 404
    HTTP_CONFLICT = 409
    HTTP_INTERNAL_ERROR = 500
    
    def __init__(self, service):
        """Initialize controller with service.
        
        Args:
            service: Service instance for business logic
        """
        self.service = service
    
    # Response formatting
    
    @staticmethod
    def success_response(data: Any = None, message: str = None, 
                        status: int = 200) -> Tuple[Dict[str, Any], int]:
        """Format successful response.
        
        Args:
            data: Response data
            message: Success message
            status: HTTP status code
            
        Returns:
            Tuple of (response_dict, status_code)
        """
        response = {"success": True}
        
        if data is not None:
            response["data"] = data
        
        if message:
            response["message"] = message
        
        return response, status
    
    @staticmethod
    def error_response(error: str, details: Any = None, 
                      status: int = 400) -> Tuple[Dict[str, Any], int]:
        """Format error response.
        
        Args:
            error: Error message
            details: Additional error details
            status: HTTP status code
            
        Returns:
            Tuple of (response_dict, status_code)
        """
        response = {
            "success": False,
            "error": error
        }
        
        if details:
            response["details"] = details
        
        return response, status
    
    @staticmethod
    def paginated_response(items: List[Any], page: int, per_page: int,
                          total: int) -> Dict[str, Any]:
        """Format paginated response.
        
        Args:
            items: List of items
            page: Current page
            per_page: Items per page
            total: Total item count
            
        Returns:
            Paginated response dict
        """
        total_pages = (total + per_page - 1) // per_page if per_page > 0 else 1
        
        return {
            "items": items,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1
            }
        }
    
    # Error handling decorators
    
    @staticmethod
    def handle_errors(f: Callable) -> Callable:
        """Decorator to handle common errors.
        
        Args:
            f: Function to wrap
            
        Returns:
            Wrapped function
        """
        @wraps(f)
        def wrapper(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except ValidationError as e:
                logger.warning(f"Validation error: {e}")
                return BaseController.error_response(
                    "Validation failed",
                    {"field": e.field, "message": str(e)},
                    BaseController.HTTP_BAD_REQUEST
                )
            except ValueError as e:
                logger.warning(f"Value error: {e}")
                return BaseController.error_response(
                    str(e),
                    status=BaseController.HTTP_BAD_REQUEST
                )
            except KeyError as e:
                logger.warning(f"Key error: {e}")
                return BaseController.error_response(
                    f"Missing required field: {e}",
                    status=BaseController.HTTP_BAD_REQUEST
                )
            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                return BaseController.error_response(
                    "Internal server error",
                    status=BaseController.HTTP_INTERNAL_ERROR
                )
        
        return wrapper
    
    @staticmethod
    def require_auth(f: Callable) -> Callable:
        """Decorator to require authentication.
        
        Args:
            f: Function to wrap
            
        Returns:
            Wrapped function
        """
        @wraps(f)
        def wrapper(*args, **kwargs):
            # Check for auth token in request
            # This would integrate with actual auth system
            auth_token = kwargs.get("auth_token")
            
            if not auth_token:
                return BaseController.error_response(
                    "Authentication required",
                    status=BaseController.HTTP_UNAUTHORIZED
                )
            
            # Validate token (placeholder)
            if not BaseController._validate_token(auth_token):
                return BaseController.error_response(
                    "Invalid authentication token",
                    status=BaseController.HTTP_UNAUTHORIZED
                )
            
            return f(*args, **kwargs)
        
        return wrapper
    
    @staticmethod
    def _validate_token(token: str) -> bool:
        """Validate authentication token.
        
        Args:
            token: Auth token
            
        Returns:
            True if valid
        """
        # Placeholder - would integrate with real auth
        return token and len(token) > 0
    
    # Common CRUD operations
    
    @handle_errors
    def list_items(self, page: int = 1, per_page: int = 20,
                   sort_by: str = None, sort_desc: bool = False,
                   **filters) -> Tuple[Dict[str, Any], int]:
        """List items with pagination.
        
        Args:
            page: Page number
            per_page: Items per page
            sort_by: Sort field
            sort_desc: Sort descending
            **filters: Additional filters
            
        Returns:
            Response tuple
        """
        result = self.service.list(
            page=page,
            per_page=per_page,
            sort_by=sort_by,
            sort_desc=sort_desc,
            **filters
        )
        
        response = self.paginated_response(
            result["items"],
            result["page"],
            result["per_page"],
            result["total"]
        )
        
        return self.success_response(response)
    
    @handle_errors
    def get_item(self, item_id: int) -> Tuple[Dict[str, Any], int]:
        """Get single item by ID.
        
        Args:
            item_id: Item ID
            
        Returns:
            Response tuple
        """
        item = self.service.get(item_id)
        
        if not item:
            return self.error_response(
                f"Item not found: {item_id}",
                status=self.HTTP_NOT_FOUND
            )
        
        return self.success_response(item)
    
    @handle_errors
    def create_item(self, data: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """Create new item.
        
        Args:
            data: Item data
            
        Returns:
            Response tuple
        """
        item = self.service.create(data)
        return self.success_response(item, status=self.HTTP_CREATED)
    
    @handle_errors
    def update_item(self, item_id: int, data: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """Update existing item.
        
        Args:
            item_id: Item ID
            data: Update data
            
        Returns:
            Response tuple
        """
        item = self.service.update(item_id, data)
        
        if not item:
            return self.error_response(
                f"Item not found: {item_id}",
                status=self.HTTP_NOT_FOUND
            )
        
        return self.success_response(item)
    
    @handle_errors
    def delete_item(self, item_id: int) -> Tuple[Dict[str, Any], int]:
        """Delete item.
        
        Args:
            item_id: Item ID
            
        Returns:
            Response tuple
        """
        success = self.service.delete(item_id)
        
        if not success:
            return self.error_response(
                f"Item not found: {item_id}",
                status=self.HTTP_NOT_FOUND
            )
        
        return self.success_response(
            message=f"Item {item_id} deleted",
            status=self.HTTP_NO_CONTENT
        )
    
    @handle_errors
    def search_items(self, query: str, fields: List[str] = None,
                    page: int = 1, per_page: int = 20) -> Tuple[Dict[str, Any], int]:
        """Search items.
        
        Args:
            query: Search query
            fields: Fields to search
            page: Page number
            per_page: Items per page
            
        Returns:
            Response tuple
        """
        result = self.service.search(
            query=query,
            fields=fields,
            page=page,
            per_page=per_page
        )
        
        response = self.paginated_response(
            result["items"],
            result["page"],
            result["per_page"],
            result["total"]
        )
        
        return self.success_response(response)
    
    @handle_errors
    def bulk_create(self, items: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], int]:
        """Bulk create items.
        
        Args:
            items: List of items to create
            
        Returns:
            Response tuple
        """
        count = self.service.bulk_create(items)
        
        return self.success_response(
            {"created": count},
            message=f"Created {count} items"
        )
    
    @handle_errors
    def bulk_delete(self, item_ids: List[int]) -> Tuple[Dict[str, Any], int]:
        """Bulk delete items.
        
        Args:
            item_ids: List of item IDs
            
        Returns:
            Response tuple
        """
        count = self.service.bulk_delete(item_ids)
        
        return self.success_response(
            {"deleted": count},
            message=f"Deleted {count} items"
        )
    
    # Validation helpers
    
    @staticmethod
    def validate_pagination(page: Any, per_page: Any) -> Tuple[int, int]:
        """Validate pagination parameters.
        
        Args:
            page: Page number
            per_page: Items per page
            
        Returns:
            Tuple of (page, per_page)
            
        Raises:
            ValueError: If invalid
        """
        try:
            page = int(page) if page else 1
            per_page = int(per_page) if per_page else 20
        except (TypeError, ValueError):
            raise ValueError("Invalid pagination parameters")
        
        if page < 1:
            raise ValueError("Page must be >= 1")
        
        if per_page < 1 or per_page > 100:
            raise ValueError("Per page must be between 1 and 100")
        
        return page, per_page
    
    @staticmethod
    def validate_sort(sort_by: str, allowed_fields: List[str]) -> str:
        """Validate sort field.

        Args:
            sort_by: Sort field
            allowed_fields: Allowed field names

        Returns:
            Validated sort field, defaults to first allowed field

        Raises:
            ValueError: If invalid
        """
        if not sort_by:
            return allowed_fields[0] if allowed_fields else "created_at"
        
        if sort_by not in allowed_fields:
            raise ValueError(f"Invalid sort field: {sort_by}")
        
        return sort_by
    
    @staticmethod
    def parse_json_body(body: str) -> Dict[str, Any]:
        """Parse JSON request body.
        
        Args:
            body: JSON string
            
        Returns:
            Parsed data
            
        Raises:
            ValueError: If invalid JSON
        """
        if not body:
            return {}
        
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")
    
    @staticmethod
    def parse_filters(params: Dict[str, Any], 
                     allowed_filters: List[str]) -> Dict[str, Any]:
        """Parse and validate filter parameters.
        
        Args:
            params: Request parameters
            allowed_filters: Allowed filter names
            
        Returns:
            Validated filters
        """
        filters = {}
        
        for key, value in params.items():
            if key in allowed_filters and value:
                filters[key] = value
        
        return filters
