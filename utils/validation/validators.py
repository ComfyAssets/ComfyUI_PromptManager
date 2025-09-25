"""Validators for PromptManager data validation and security.

Provides comprehensive validation for prompts, metadata, file operations,
and security checks including SQL injection and XSS prevention.
"""

import re
import html
import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

# Import ComfyUIFileSystem for proper path resolution
try:
    from ..core.file_system import ComfyUIFileSystem  # type: ignore
except ImportError:  # pragma: no cover - fallback for direct execution
    try:
        from utils.core.file_system import ComfyUIFileSystem  # type: ignore
    except ImportError:
        ComfyUIFileSystem = None

logger = get_logger("promptmanager.validators")


def _get_validator_base_dir() -> Path:
    """Get the base directory for validation using ComfyUI root detection."""
    if ComfyUIFileSystem is not None:
        try:
            fs_helper = ComfyUIFileSystem()
            comfyui_root = fs_helper.resolve_comfyui_root()
            return Path(comfyui_root)
        except Exception:
            pass

    # Fallback to current directory if ComfyUIFileSystem fails
    return Path.cwd()


class ValidationError(Exception):
    """Custom exception for validation failures."""
    pass


class PromptValidator:
    """Validates prompt text and metadata."""
    
    # Limits
    MAX_PROMPT_LENGTH = 10000  # Characters
    MAX_NEGATIVE_LENGTH = 5000
    MAX_CATEGORY_LENGTH = 100
    MAX_TAG_LENGTH = 50
    MAX_TAGS = 20
    MIN_PROMPT_LENGTH = 1
    
    # Patterns
    VALID_CATEGORY_PATTERN = re.compile(r'^[a-zA-Z0-9\s\-_]+$')
    VALID_TAG_PATTERN = re.compile(r'^[a-zA-Z0-9\s\-_]+$')
    
    # Dangerous patterns for SQL injection prevention
    SQL_INJECTION_PATTERNS = [
        r'(\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|EXEC|EXECUTE|UNION|FROM|WHERE)\b)',
        r'(--|\#|\/\*|\*\/)',  # SQL comments
        r'(\bOR\b.*=.*)',  # OR conditions
        r'(;\s*(DELETE|DROP|INSERT|UPDATE))',  # Chained commands
        r'(xp_cmdshell|sp_executesql)',  # SQL Server specific
        r'(CAST\s*\(|CONVERT\s*\()',  # Type conversion attacks
    ]
    
    # XSS prevention patterns
    XSS_PATTERNS = [
        r'<script[^>]*>.*?</script>',
        r'javascript:',
        r'on\w+\s*=',  # Event handlers
        r'<iframe[^>]*>',
        r'<embed[^>]*>',
        r'<object[^>]*>',
        r'eval\s*\(',
        r'expression\s*\(',
    ]
    
    @classmethod
    def validate_prompt_text(
        cls,
        text: str,
        allow_empty: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """Validate prompt text for length and content.
        
        Args:
            text: Prompt text to validate
            allow_empty: Whether to allow empty prompts
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not text and not allow_empty:
            return False, "Prompt text cannot be empty"
        
        if not allow_empty and len(text.strip()) < cls.MIN_PROMPT_LENGTH:
            return False, f"Prompt must be at least {cls.MIN_PROMPT_LENGTH} character"
        
        if len(text) > cls.MAX_PROMPT_LENGTH:
            return False, f"Prompt exceeds maximum length of {cls.MAX_PROMPT_LENGTH} characters"
        
        # Check for SQL injection attempts
        for pattern in cls.SQL_INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                logger.warning(f"SQL injection pattern detected: {pattern[:30]}...")
                return False, "Prompt contains potentially unsafe SQL patterns"
        
        # Check for XSS attempts
        for pattern in cls.XSS_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                logger.warning(f"XSS pattern detected: {pattern[:30]}...")
                return False, "Prompt contains potentially unsafe HTML/JavaScript"
        
        return True, None
    
    @classmethod
    def validate_negative_prompt(cls, text: str) -> Tuple[bool, Optional[str]]:
        """Validate negative prompt text.
        
        Args:
            text: Negative prompt text
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if len(text) > cls.MAX_NEGATIVE_LENGTH:
            return False, f"Negative prompt exceeds maximum length of {cls.MAX_NEGATIVE_LENGTH}"
        
        # Apply same security checks as regular prompts
        for pattern in cls.SQL_INJECTION_PATTERNS + cls.XSS_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return False, "Negative prompt contains potentially unsafe patterns"
        
        return True, None
    
    @classmethod
    def validate_category(cls, category: str) -> Tuple[bool, Optional[str]]:
        """Validate category name.
        
        Args:
            category: Category name
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not category:
            return True, None  # Empty category is valid
        
        if len(category) > cls.MAX_CATEGORY_LENGTH:
            return False, f"Category exceeds maximum length of {cls.MAX_CATEGORY_LENGTH}"
        
        if not cls.VALID_CATEGORY_PATTERN.match(category):
            return False, "Category contains invalid characters (only alphanumeric, spaces, hyphens, and underscores allowed)"
        
        return True, None
    
    @classmethod
    def validate_tags(cls, tags: Union[str, List[str]]) -> Tuple[bool, Optional[str]]:
        """Validate tags.
        
        Args:
            tags: Comma-separated string or list of tags
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if isinstance(tags, str):
            if not tags:
                return True, None
            tag_list = [t.strip() for t in tags.split(',')]
        else:
            tag_list = tags
        
        if len(tag_list) > cls.MAX_TAGS:
            return False, f"Too many tags (maximum {cls.MAX_TAGS})"
        
        for tag in tag_list:
            if len(tag) > cls.MAX_TAG_LENGTH:
                return False, f"Tag '{tag[:20]}...' exceeds maximum length of {cls.MAX_TAG_LENGTH}"
            
            if tag and not cls.VALID_TAG_PATTERN.match(tag):
                return False, f"Tag '{tag}' contains invalid characters"
        
        return True, None
    
    @classmethod
    def sanitize_html(cls, text: str) -> str:
        """Sanitize text for HTML display.
        
        Args:
            text: Text to sanitize
            
        Returns:
            Sanitized text safe for HTML
        """
        return html.escape(text)
    
    @classmethod
    def validate_metadata(cls, metadata: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate prompt metadata dictionary.
        
        Args:
            metadata: Metadata dictionary
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not isinstance(metadata, dict):
            return False, "Metadata must be a dictionary"
        
        # Check for oversized metadata
        import json
        try:
            serialized = json.dumps(metadata)
            if len(serialized) > 50000:  # 50KB limit
                return False, "Metadata too large (exceeds 50KB)"
        except (TypeError, ValueError) as e:
            return False, f"Metadata not JSON serializable: {e}"
        
        # Validate specific fields if present
        if 'category' in metadata:
            valid, error = cls.validate_category(metadata['category'])
            if not valid:
                return False, f"Invalid category: {error}"
        
        if 'tags' in metadata:
            valid, error = cls.validate_tags(metadata['tags'])
            if not valid:
                return False, f"Invalid tags: {error}"
        
        return True, None


class FileValidator:
    """Validates file operations and paths."""
    
    # File size limits
    MAX_IMAGE_SIZE = 50 * 1024 * 1024  # 50MB
    MAX_JSON_SIZE = 10 * 1024 * 1024   # 10MB
    MAX_TEXT_SIZE = 5 * 1024 * 1024    # 5MB
    
    # Allowed file extensions
    ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}
    ALLOWED_TEXT_EXTENSIONS = {'.txt', '.md', '.json', '.yaml', '.yml'}
    
    # Dangerous file patterns
    DANGEROUS_EXTENSIONS = {'.exe', '.dll', '.so', '.sh', '.bat', '.cmd', '.ps1'}
    
    @classmethod
    def validate_file_type(
        cls,
        file_path: Union[str, Path],
        allowed_types: Optional[set] = None
    ) -> Tuple[bool, Optional[str]]:
        """Validate file type based on extension.
        
        Args:
            file_path: Path to file
            allowed_types: Set of allowed extensions (with dots)
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        path = Path(file_path)
        extension = path.suffix.lower()
        
        # Check for dangerous extensions
        if extension in cls.DANGEROUS_EXTENSIONS:
            return False, f"File type {extension} is not allowed for security reasons"
        
        if allowed_types:
            if extension not in allowed_types:
                return False, f"File type {extension} not in allowed types: {allowed_types}"
        
        return True, None
    
    @classmethod
    def validate_file_size(
        cls,
        file_path: Union[str, Path],
        max_size: Optional[int] = None
    ) -> Tuple[bool, Optional[str]]:
        """Validate file size.
        
        Args:
            file_path: Path to file
            max_size: Maximum size in bytes
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        path = Path(file_path)
        
        if not path.exists():
            return False, "File does not exist"
        
        file_size = path.stat().st_size
        
        if max_size and file_size > max_size:
            return False, f"File size ({file_size:,} bytes) exceeds maximum ({max_size:,} bytes)"
        
        # Auto-detect limits based on file type
        extension = path.suffix.lower()
        
        if extension in cls.ALLOWED_IMAGE_EXTENSIONS:
            if file_size > cls.MAX_IMAGE_SIZE:
                return False, f"Image file too large (max {cls.MAX_IMAGE_SIZE // 1024 // 1024}MB)"
        
        elif extension == '.json':
            if file_size > cls.MAX_JSON_SIZE:
                return False, f"JSON file too large (max {cls.MAX_JSON_SIZE // 1024 // 1024}MB)"
        
        elif extension in cls.ALLOWED_TEXT_EXTENSIONS:
            if file_size > cls.MAX_TEXT_SIZE:
                return False, f"Text file too large (max {cls.MAX_TEXT_SIZE // 1024 // 1024}MB)"
        
        return True, None
    
    @classmethod
    def validate_path_traversal(cls, file_path: Union[str, Path]) -> Tuple[bool, Optional[str]]:
        """Check for path traversal attempts.
        
        Args:
            file_path: Path to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        path_str = str(file_path)
        
        # Check for path traversal patterns
        dangerous_patterns = [
            '..',
            '~/',
            '/etc/',
            '/sys/',
            '/proc/',
            'C:\\Windows',
            'C:\\System',
        ]
        
        for pattern in dangerous_patterns:
            if pattern in path_str:
                logger.warning(f"Path traversal attempt detected: {path_str}")
                return False, "Path contains potentially unsafe patterns"
        
        # Check for absolute paths that might escape the working directory
        path = Path(file_path)
        if path.is_absolute():
            # Only allow absolute paths within specific directories
            safe_dirs = [
                Path.home() / 'ComfyUI' / 'output',
                _get_validator_base_dir() / 'output',
                Path('/tmp'),  # Linux temp
                Path('C:\\Temp'),  # Windows temp
            ]
            
            if not any(str(path).startswith(str(safe_dir)) for safe_dir in safe_dirs if safe_dir.exists()):
                return False, "Absolute path outside of allowed directories"
        
        return True, None
    
    @classmethod
    def sanitize_filename(cls, filename: str) -> str:
        """Sanitize filename for safe file operations.
        
        Args:
            filename: Original filename
            
        Returns:
            Sanitized filename
        """
        # Remove path components
        filename = Path(filename).name
        
        # Replace dangerous characters
        dangerous_chars = '<>:"|?*\\/\0'
        for char in dangerous_chars:
            filename = filename.replace(char, '_')
        
        # Remove control characters
        filename = ''.join(char for char in filename if ord(char) >= 32)
        
        # Limit length
        max_length = 255
        if len(filename) > max_length:
            name, ext = Path(filename).stem, Path(filename).suffix
            max_name_length = max_length - len(ext)
            filename = name[:max_name_length] + ext
        
        # Ensure it's not empty
        if not filename or filename == '.':
            filename = 'unnamed_file'
        
        return filename


class RateLimitValidator:
    """Validates rate limiting and request throttling."""
    
    def __init__(self):
        """Initialize rate limit validator."""
        self.request_history: Dict[str, List[float]] = {}
        self.limits = {
            'api_calls': (100, 60),      # 100 calls per 60 seconds
            'file_uploads': (10, 60),     # 10 uploads per 60 seconds
            'prompt_saves': (50, 60),     # 50 saves per 60 seconds
            'searches': (30, 60),         # 30 searches per 60 seconds
        }
    
    def check_rate_limit(
        self,
        identifier: str,
        action: str,
        current_time: Optional[float] = None
    ) -> Tuple[bool, Optional[str]]:
        """Check if an action exceeds rate limits.
        
        Args:
            identifier: User/IP identifier
            action: Type of action
            current_time: Current timestamp (for testing)
            
        Returns:
            Tuple of (is_allowed, error_message)
        """
        import time
        
        if current_time is None:
            current_time = time.time()
        
        if action not in self.limits:
            return True, None  # No limit defined
        
        max_requests, time_window = self.limits[action]
        key = f"{identifier}:{action}"
        
        # Initialize history if needed
        if key not in self.request_history:
            self.request_history[key] = []
        
        # Remove old entries
        cutoff_time = current_time - time_window
        self.request_history[key] = [
            t for t in self.request_history[key]
            if t > cutoff_time
        ]
        
        # Check limit
        if len(self.request_history[key]) >= max_requests:
            wait_time = self.request_history[key][0] + time_window - current_time
            return False, f"Rate limit exceeded. Try again in {wait_time:.1f} seconds"
        
        # Record this request
        self.request_history[key].append(current_time)
        
        return True, None
    
    def reset_limits(self, identifier: Optional[str] = None):
        """Reset rate limits for an identifier or all.
        
        Args:
            identifier: Specific identifier to reset, or None for all
        """
        if identifier:
            keys_to_remove = [
                key for key in self.request_history
                if key.startswith(f"{identifier}:")
            ]
            for key in keys_to_remove:
                del self.request_history[key]
        else:
            self.request_history.clear()


class URLValidator:
    """Validates URLs for safety and correctness."""
    
    ALLOWED_SCHEMES = {'http', 'https'}
    BLOCKED_DOMAINS = {
        'localhost',
        '127.0.0.1',
        '0.0.0.0',
        '::1',
    }
    
    @classmethod
    def validate_url(cls, url: str) -> Tuple[bool, Optional[str]]:
        """Validate URL for safety.
        
        Args:
            url: URL to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            parsed = urlparse(url)
            
            # Check scheme
            if parsed.scheme not in cls.ALLOWED_SCHEMES:
                return False, f"URL scheme '{parsed.scheme}' not allowed"
            
            # Check for local/private addresses
            if parsed.hostname in cls.BLOCKED_DOMAINS:
                return False, "Local/private URLs not allowed"
            
            # Check for IP addresses in private ranges
            if parsed.hostname:
                import ipaddress
                try:
                    ip = ipaddress.ip_address(parsed.hostname)
                    if ip.is_private or ip.is_loopback:
                        return False, "Private IP addresses not allowed"
                except ValueError:
                    pass  # Not an IP address, that's fine
            
            return True, None
            
        except Exception as e:
            return False, f"Invalid URL: {e}"


# Convenience functions
def validate_prompt(text: str, **kwargs) -> Tuple[bool, Optional[str]]:
    """Convenience function for prompt validation."""
    return PromptValidator.validate_prompt_text(text, **kwargs)

def validate_file(file_path: Union[str, Path], **kwargs) -> Tuple[bool, Optional[str]]:
    """Convenience function for file validation."""
    valid, error = FileValidator.validate_file_type(file_path, **kwargs)
    if not valid:
        return False, error
    
    valid, error = FileValidator.validate_file_size(file_path)
    if not valid:
        return False, error
    
    return FileValidator.validate_path_traversal(file_path)

def sanitize_html(text: str) -> str:
    """Convenience function for HTML sanitization."""
    return PromptValidator.sanitize_html(text)

def sanitize_filename(filename: str) -> str:
    """Convenience function for filename sanitization."""
    return FileValidator.sanitize_filename(filename)

def validate_prompt_text(text: str) -> bool:
    """Validate prompt text input.

    Ensures the prompt text is a valid string with reasonable length limits.
    Empty or whitespace-only strings are rejected.

    Args:
        text: The prompt text to validate

    Returns:
        True if the text passes validation

    Raises:
        ValueError: If text is invalid with descriptive message including:
                   - Not a string type
                   - Empty or whitespace-only
                   - Exceeds maximum length (10,000 characters)
    """
    if not isinstance(text, str):
        raise ValueError("Prompt text must be a string")

    if not text or not text.strip():
        raise ValueError("Prompt text cannot be empty")

    if len(text.strip()) > 10000:  # Reasonable limit for prompt length
        raise ValueError("Prompt text is too long (maximum 10,000 characters)")

    return True

def validate_rating(rating: Optional[int]) -> bool:
    """Validate rating input.

    Validates rating values on a 1-5 scale, with None allowed for unrated prompts.

    Args:
        rating: The rating to validate (1-5 scale or None for no rating)

    Returns:
        True if the rating is valid

    Raises:
        ValueError: If rating is invalid:
                   - Not an integer (when not None)
                   - Outside the 1-5 range
    """
    if rating is None:
        return True

    if not isinstance(rating, int):
        raise ValueError("Rating must be an integer")

    if rating < 1 or rating > 5:
        raise ValueError("Rating must be between 1 and 5")

    return True

def validate_tags(tags: Union[str, List[str], None]) -> bool:
    """Validate tags input.

    Accepts tags as comma-separated string, list of strings, or None.
    Validates each tag for length and character restrictions.

    Args:
        tags: Tags as comma-separated string, list of strings, or None

    Returns:
        True if all tags are valid

    Raises:
        ValueError: If tags are invalid:
                   - Wrong input type (not string, list, or None)
                   - Individual tag is empty or only whitespace
                   - Individual tag exceeds 50 characters
                   - Tag contains invalid characters (non-alphanumeric, spaces, hyphens, underscores)
                   - More than 20 tags provided
    """
    if tags is None:
        return True

    if isinstance(tags, str):
        # Parse comma-separated tags
        tag_list = [tag.strip() for tag in tags.split(',') if tag.strip()]
        tags = tag_list

    if not isinstance(tags, list):
        raise ValueError("Tags must be a string, list, or None")

    for tag in tags:
        if not isinstance(tag, str):
            raise ValueError("All tags must be strings")

        if not tag.strip():
            raise ValueError("Tags cannot be empty")

        if len(tag.strip()) > 50:
            raise ValueError("Individual tags cannot exceed 50 characters")

        # Check for invalid characters (optional - you can adjust this)
        if not re.match(r'^[a-zA-Z0-9\s\-_]+$', tag.strip()):
            raise ValueError(f"Tag '{tag}' contains invalid characters")

    if len(tags) > 20:  # Reasonable limit
        raise ValueError("Maximum 20 tags allowed")

    return True
