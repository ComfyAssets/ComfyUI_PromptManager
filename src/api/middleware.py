"""API middleware for request processing.

This module provides middleware components for authentication,
validation, rate limiting, and error handling.
"""

import json
import time
from typing import Any, Callable, Dict, Optional
from functools import wraps
from collections import defaultdict

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.api.middleware")


class RateLimiter:
    """Rate limiting middleware."""
    
    def __init__(self, requests_per_minute: int = 60):
        """Initialize rate limiter.
        
        Args:
            requests_per_minute: Maximum requests per minute
        """
        self.requests_per_minute = requests_per_minute
        self.requests = defaultdict(list)
    
    def check_rate_limit(self, client_id: str) -> bool:
        """Check if client has exceeded rate limit.
        
        Args:
            client_id: Client identifier (IP, user ID, etc.)
            
        Returns:
            True if within limits, False if exceeded
        """
        current_time = time.time()
        minute_ago = current_time - 60
        
        # Clean old requests
        self.requests[client_id] = [
            req_time for req_time in self.requests[client_id]
            if req_time > minute_ago
        ]
        
        # Check limit
        if len(self.requests[client_id]) >= self.requests_per_minute:
            return False
        
        # Record request
        self.requests[client_id].append(current_time)
        return True


class CORSMiddleware:
    """CORS (Cross-Origin Resource Sharing) middleware."""
    
    def __init__(self, 
                 allowed_origins: list = None,
                 allowed_methods: list = None,
                 allowed_headers: list = None,
                 max_age: int = 3600):
        """Initialize CORS middleware.
        
        Args:
            allowed_origins: List of allowed origins
            allowed_methods: List of allowed methods
            allowed_headers: List of allowed headers
            max_age: Preflight cache duration
        """
        self.allowed_origins = allowed_origins or ["*"]
        self.allowed_methods = allowed_methods or [
            "GET", "POST", "PUT", "DELETE", "OPTIONS"
        ]
        self.allowed_headers = allowed_headers or [
            "Content-Type", "Authorization", "X-Requested-With"
        ]
        self.max_age = max_age


class RequestLogger:
    """Request/response logging middleware."""
    
    def __init__(self, log_body: bool = False, max_body_length: int = 1000):
        """Initialize request logger.
        
        Args:
            log_body: Whether to log request/response bodies
            max_body_length: Maximum body length to log
        """
        self.log_body = log_body
        self.max_body_length = max_body_length


class ValidationMiddleware:
    """Request validation middleware."""
    
    @staticmethod
    def validate_content_type(expected: str = "application/json"):
        """Validate request content type.
        
        Args:
            expected: Expected content type
            
        Returns:
            Decorator function
        """
        def decorator(f: Callable) -> Callable:
            @wraps(f)
            def wrapper(self_or_request, *args, **kwargs):
                # Handle both instance methods (self, request) and functions (request)
                if hasattr(self_or_request, '__class__') and hasattr(self_or_request.__class__, '__name__'):
                    # This is likely a self argument, request is the next one
                    self_arg = self_or_request
                    request = args[0] if args else kwargs.get('request')
                else:
                    # This is likely the request argument directly
                    self_arg = None
                    request = self_or_request

                content_type = request.headers.get("Content-Type", "") if hasattr(request, 'headers') else ""

                if not content_type.startswith(expected):
                    return {
                        "success": False,
                        "error": f"Invalid content type. Expected {expected}"
                    }, 400

                if self_arg:
                    return f(self_arg, request, *args[1:], **kwargs)
                else:
                    return f(request, *args, **kwargs)

            return wrapper
        return decorator


class CacheMiddleware:
    """Response caching middleware."""
    
    def __init__(self, ttl: int = 300):
        """Initialize cache middleware.
        
        Args:
            ttl: Cache time-to-live in seconds
        """
        self.ttl = ttl
        self.cache = {}
        self.timestamps = {}
