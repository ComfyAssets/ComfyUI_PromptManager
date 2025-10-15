"""Prompt API endpoints.

This module implements REST API endpoints for prompt management,
extending the BaseController with prompt-specific operations.
"""

from typing import Any, Dict, List, Tuple

from ..base_controller import BaseController
from ..middleware import (
    RateLimiter, 
    ValidationMiddleware,
    CacheMiddleware
)
from ..services.prompt_service import PromptService
try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.api.endpoints.prompt")


class PromptEndpoints(BaseController):
    """API endpoints for prompt management."""
    
    def __init__(self, service: PromptService = None):
        """Initialize prompt endpoints.
        
        Args:
            service: PromptService instance
        """
        if service is None:
            service = PromptService()
        super().__init__(service)
        
        # Endpoint-specific settings
        self.rate_limiter = RateLimiter(requests_per_minute=100)
        self.cache = CacheMiddleware(ttl=60)  # 1 minute cache for lists
    
    # Standard CRUD endpoints
    
    def list_prompts(self, request) -> Tuple[Dict[str, Any], int]:
        """List prompts with pagination and filtering.
        
        GET /api/prompts
        Query params: page, per_page, sort_by, sort_desc, category, min_rating
        """
        # Parse parameters
        page, per_page = self.validate_pagination(
            request.args.get("page", 1),
            request.args.get("per_page", 20)
        )
        
        sort_by = self.validate_sort(
            request.args.get("sort_by"),
            ["created_at", "updated_at", "rating", "execution_count"]
        )
        
        sort_desc = request.args.get("sort_desc", "false").lower() == "true"
        
        # Parse filters
        filters = self.parse_filters(request.args, [
            "category", "min_rating", "has_negative", "search"
        ])
        
        # Get prompts
        return self.list_items(
            page=page,
            per_page=per_page,
            sort_by=sort_by,
            sort_desc=sort_desc,
            **filters
        )
    
    def get_prompt(self, prompt_id: int) -> Tuple[Dict[str, Any], int]:
        """Get single prompt by ID.
        
        GET /api/prompts/{id}
        """
        return self.get_item(prompt_id)
    
    @ValidationMiddleware.validate_content_type("application/json")
    def create_prompt(self, request) -> Tuple[Dict[str, Any], int]:
        """Create new prompt.

        POST /api/prompts
        Body: {prompt, negative_prompt?, category?, tags?, rating?, notes?}
        """
        # Flask request object uses get_json() or data, not body
        data = request.get_json() if hasattr(request, 'get_json') else self.parse_json_body(request.data)

        # Validate required fields - check for either positive_prompt or prompt
        if "positive_prompt" not in data and "prompt" not in data:
            return self.error_response(
                "Missing required field: positive_prompt or prompt",
                status=self.HTTP_BAD_REQUEST
            )

        # Normalize field name to positive_prompt for database
        if "prompt" in data and "positive_prompt" not in data:
            data["positive_prompt"] = data.pop("prompt")

        return self.create_item(data)
    
    @ValidationMiddleware.validate_content_type("application/json")
    def update_prompt(self, prompt_id: int, request) -> Tuple[Dict[str, Any], int]:
        """Update existing prompt.

        PUT /api/prompts/{id}
        Body: {prompt?, negative_prompt?, category?, tags?, rating?, notes?}
        """
        # Flask request object uses get_json() or data, not body
        data = request.get_json() if hasattr(request, 'get_json') else self.parse_json_body(request.data)
        return self.update_item(prompt_id, data)
    
    def delete_prompt(self, prompt_id: int) -> Tuple[Dict[str, Any], int]:
        """Delete prompt.
        
        DELETE /api/prompts/{id}
        """
        return self.delete_item(prompt_id)
    
    # Prompt-specific endpoints
    
    def search_prompts(self, request) -> Tuple[Dict[str, Any], int]:
        """Search prompts by text.
        
        GET /api/prompts/search
        Query params: q (query), fields?, page?, per_page?
        """
        query = request.args.get("q", "")
        if not query:
            return self.error_response(
                "Search query required",
                status=self.HTTP_BAD_REQUEST
            )
        
        fields = request.args.get("fields", "").split(",") if request.args.get("fields") else None
        page, per_page = self.validate_pagination(
            request.args.get("page", 1),
            request.args.get("per_page", 20)
        )
        
        return self.search_items(query, fields, page, per_page)
    
    def get_recent_prompts(self, request) -> Tuple[Dict[str, Any], int]:
        """Get recently used prompts.
        
        GET /api/prompts/recent
        Query params: limit?
        """
        try:
            limit = int(request.args.get("limit", 10))
            limit = min(max(limit, 1), 100)  # Clamp between 1-100
        except (TypeError, ValueError):
            limit = 10
        
        prompts = self.service.get_recent(limit)
        return self.success_response(prompts)
    
    def get_popular_prompts(self, request) -> Tuple[Dict[str, Any], int]:
        """Get most popular prompts.
        
        GET /api/prompts/popular
        Query params: limit?
        """
        try:
            limit = int(request.args.get("limit", 10))
            limit = min(max(limit, 1), 100)
        except (TypeError, ValueError):
            limit = 10
        
        prompts = self.service.get_popular(limit)
        return self.success_response(prompts)
    
    def get_categories(self, request) -> Tuple[Dict[str, Any], int]:
        """Get all categories with counts.
        
        GET /api/prompts/categories
        """
        categories = self.service.get_categories()
        return self.success_response(categories)
    
    def get_prompts_by_category(self, category: str) -> Tuple[Dict[str, Any], int]:
        """Get prompts in a specific category.
        
        GET /api/prompts/category/{category}
        """
        prompts = self.service.get_by_category(category)
        return self.success_response(prompts)
    
    @ValidationMiddleware.validate_content_type("application/json")
    def rate_prompt(self, prompt_id: int, request) -> Tuple[Dict[str, Any], int]:
        """Rate a prompt.
        
        POST /api/prompts/{id}/rate
        Body: {rating: 1-5}
        """
        data = self.parse_json_body(request.body)
        
        if "rating" not in data:
            return self.error_response(
                "Rating required",
                status=self.HTTP_BAD_REQUEST
            )
        
        try:
            prompt = self.service.rate_prompt(prompt_id, data["rating"])
            if prompt:
                return self.success_response(prompt)
            else:
                return self.error_response(
                    f"Prompt not found: {prompt_id}",
                    status=self.HTTP_NOT_FOUND
                )
        except Exception as e:
            return self.error_response(str(e), status=self.HTTP_BAD_REQUEST)
    
    def use_prompt(self, prompt_id: int) -> Tuple[Dict[str, Any], int]:
        """Mark prompt as used (increment counter).
        
        POST /api/prompts/{id}/use
        """
        success = self.service.use_prompt(prompt_id)
        
        if success:
            return self.success_response(
                message=f"Prompt {prompt_id} usage recorded"
            )
        else:
            return self.error_response(
                f"Prompt not found: {prompt_id}",
                status=self.HTTP_NOT_FOUND
            )
    
    @ValidationMiddleware.validate_content_type("application/json")
    def create_or_get_prompt(self, request) -> Tuple[Dict[str, Any], int]:
        """Create prompt or return existing if duplicate.
        
        POST /api/prompts/create-or-get
        Body: {prompt, negative_prompt?, category?, tags?, notes?}
        """
        data = self.parse_json_body(request.body)
        
        if "prompt" not in data:
            return self.error_response(
                "Missing required field: prompt",
                status=self.HTTP_BAD_REQUEST
            )
        
        try:
            prompt = self.service.create_or_get(data)
            return self.success_response(prompt)
        except Exception as e:
            return self.error_response(str(e), status=self.HTTP_BAD_REQUEST)
    
    def find_duplicates(self, request) -> Tuple[Dict[str, Any], int]:
        """Find all duplicate prompts.
        
        GET /api/prompts/duplicates
        """
        duplicates = self.service.find_duplicates()
        return self.success_response({
            "duplicates": duplicates,
            "count": len(duplicates)
        })
    
    @ValidationMiddleware.validate_content_type("application/json")
    def merge_duplicates(self, request) -> Tuple[Dict[str, Any], int]:
        """Merge duplicate prompts.
        
        POST /api/prompts/merge
        Body: {keep_id, remove_ids: []}
        """
        data = self.parse_json_body(request.body)
        
        if "keep_id" not in data or "remove_ids" not in data:
            return self.error_response(
                "Missing required fields: keep_id, remove_ids",
                status=self.HTTP_BAD_REQUEST
            )
        
        try:
            success = self.service.merge_duplicates(
                data["keep_id"],
                data["remove_ids"]
            )
            
            if success:
                return self.success_response(
                    message=f"Merged {len(data['remove_ids'])} duplicates"
                )
            else:
                return self.error_response(
                    "Merge failed",
                    status=self.HTTP_INTERNAL_ERROR
                )
        except Exception as e:
            return self.error_response(str(e), status=self.HTTP_BAD_REQUEST)
    
    @ValidationMiddleware.validate_content_type("application/json")
    def bulk_create_prompts(self, request) -> Tuple[Dict[str, Any], int]:
        """Bulk create prompts.
        
        POST /api/prompts/bulk
        Body: {prompts: [{prompt, negative_prompt?, ...}]}
        """
        data = self.parse_json_body(request.body)
        
        if "prompts" not in data or not isinstance(data["prompts"], list):
            return self.error_response(
                "Invalid request: prompts array required",
                status=self.HTTP_BAD_REQUEST
            )
        
        return self.bulk_create(data["prompts"])
    
    @ValidationMiddleware.validate_content_type("application/json")
    def bulk_delete_prompts(self, request) -> Tuple[Dict[str, Any], int]:
        """Bulk delete prompts.
        
        DELETE /api/prompts/bulk
        Body: {ids: [1, 2, 3]}
        """
        data = self.parse_json_body(request.body)
        
        if "ids" not in data or not isinstance(data["ids"], list):
            return self.error_response(
                "Invalid request: ids array required",
                status=self.HTTP_BAD_REQUEST
            )
        
        return self.bulk_delete(data["ids"])
    
    def export_prompts(self, request) -> Tuple[Dict[str, Any], int]:
        """Export prompts for backup.
        
        GET /api/prompts/export
        Query params: ids? (comma-separated)
        """
        ids_param = request.args.get("ids", "")
        
        if ids_param:
            try:
                ids = [int(id_str) for id_str in ids_param.split(",")]
            except ValueError:
                return self.error_response(
                    "Invalid IDs format",
                    status=self.HTTP_BAD_REQUEST
                )
        else:
            ids = None
        
        prompts = self.service.export_prompts(ids)
        
        return self.success_response({
            "prompts": prompts,
            "count": len(prompts),
            "exported_at": datetime.utcnow().isoformat()
        })
    
    @ValidationMiddleware.validate_content_type("application/json")
    def import_prompts(self, request) -> Tuple[Dict[str, Any], int]:
        """Import prompts from backup.
        
        POST /api/prompts/import
        Body: {prompts: [...]}
        """
        data = self.parse_json_body(request.body)
        
        if "prompts" not in data or not isinstance(data["prompts"], list):
            return self.error_response(
                "Invalid request: prompts array required",
                status=self.HTTP_BAD_REQUEST
            )
        
        stats = self.service.import_prompts(data["prompts"])
        
        return self.success_response(stats, message="Import completed")
    
    # Route registration helper
    
    def register_routes(self, app):
        """Register all prompt endpoints with the application.
        
        Args:
            app: Application instance (Flask, FastAPI, etc.)
        """
        # Standard CRUD
        app.route("/api/prompts", methods=["GET"])(self.list_prompts)
        app.route("/api/prompts", methods=["POST"])(self.create_prompt)
        app.route("/api/prompts/<int:prompt_id>", methods=["GET"])(self.get_prompt)
        app.route("/api/prompts/<int:prompt_id>", methods=["PUT"])(self.update_prompt)
        app.route("/api/prompts/<int:prompt_id>", methods=["DELETE"])(self.delete_prompt)
        
        # Search and filtering
        app.route("/api/prompts/search", methods=["GET"])(self.search_prompts)
        app.route("/api/prompts/recent", methods=["GET"])(self.get_recent_prompts)
        app.route("/api/prompts/popular", methods=["GET"])(self.get_popular_prompts)
        app.route("/api/prompts/categories", methods=["GET"])(self.get_categories)
        app.route("/api/prompts/category/<category>", methods=["GET"])(self.get_prompts_by_category)
        
        # Prompt-specific operations
        app.route("/api/prompts/<int:prompt_id>/rate", methods=["POST"])(self.rate_prompt)
        app.route("/api/prompts/<int:prompt_id>/use", methods=["POST"])(self.use_prompt)
        app.route("/api/prompts/create-or-get", methods=["POST"])(self.create_or_get_prompt)
        
        # Duplicate management
        app.route("/api/prompts/duplicates", methods=["GET"])(self.find_duplicates)
        app.route("/api/prompts/merge", methods=["POST"])(self.merge_duplicates)
        
        # Bulk operations
        app.route("/api/prompts/bulk", methods=["POST"])(self.bulk_create_prompts)
        app.route("/api/prompts/bulk", methods=["DELETE"])(self.bulk_delete_prompts)
        
        # Import/Export
        app.route("/api/prompts/export", methods=["GET"])(self.export_prompts)
        app.route("/api/prompts/import", methods=["POST"])(self.import_prompts)
        
        logger.info("Prompt endpoints registered")


# Import datetime for export endpoint
from datetime import datetime
