"""Shared validators for DRY validation logic.

This module provides a single source of truth for all validation operations,
eliminating code duplication across different components.
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Tuple

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.validators")


class ValidationError(Exception):
    """Custom validation error with detailed information."""
    
    def __init__(self, field: str, message: str, value: Any = None):
        """Initialize validation error.
        
        Args:
            field: The field that failed validation
            message: The error message
            value: The invalid value (optional)
        """
        self.field = field
        self.message = message
        self.value = value
        super().__init__(f"{field}: {message}")


class Validators:
    """Shared validators for all components."""
    
    # Validation constants
    MAX_PROMPT_LENGTH = 10000
    MAX_METADATA_SIZE = 65535
    MAX_NAME_LENGTH = 255
    MAX_DESCRIPTION_LENGTH = 5000
    MAX_TAG_LENGTH = 50
    MAX_TAGS = 100
    
    # Regex patterns
    FILENAME_PATTERN = re.compile(r'^[\w\-. ]+$')
    TAG_PATTERN = re.compile(r'^[\w\-]+$')
    
    @staticmethod
    def validate_prompt(prompt: str, field_name: str = "prompt") -> str:
        """Validate a prompt string.
        
        Args:
            prompt: The prompt to validate
            field_name: The name of the field for error messages
            
        Returns:
            The validated prompt
            
        Raises:
            ValidationError: If validation fails
        """
        if not prompt:
            raise ValidationError(field_name, "Cannot be empty")
        
        if not isinstance(prompt, str):
            raise ValidationError(field_name, "Must be a string")
        
        if len(prompt) > Validators.MAX_PROMPT_LENGTH:
            raise ValidationError(
                field_name,
                f"Exceeds maximum length of {Validators.MAX_PROMPT_LENGTH}"
            )
        
        return prompt.strip()
    
    @staticmethod
    def validate_metadata(metadata: Any) -> Dict:
        """Validate and normalize metadata.
        
        Args:
            metadata: The metadata to validate
            
        Returns:
            The validated metadata dictionary
            
        Raises:
            ValidationError: If validation fails
        """
        if metadata is None:
            return {}
        
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                raise ValidationError("metadata", "Invalid JSON format")
        
        if not isinstance(metadata, dict):
            raise ValidationError("metadata", "Must be a dictionary")
        
        # Check size
        json_size = len(json.dumps(metadata))
        if json_size > Validators.MAX_METADATA_SIZE:
            raise ValidationError(
                "metadata",
                f"Exceeds maximum size of {Validators.MAX_METADATA_SIZE} bytes"
            )
        
        return metadata
    
    @staticmethod
    def validate_name(name: str, field_name: str = "name") -> str:
        """Validate a name field.
        
        Args:
            name: The name to validate
            field_name: The name of the field for error messages
            
        Returns:
            The validated name
            
        Raises:
            ValidationError: If validation fails
        """
        if not name:
            raise ValidationError(field_name, "Cannot be empty")
        
        if not isinstance(name, str):
            raise ValidationError(field_name, "Must be a string")
        
        name = name.strip()
        
        if len(name) > Validators.MAX_NAME_LENGTH:
            raise ValidationError(
                field_name,
                f"Exceeds maximum length of {Validators.MAX_NAME_LENGTH}"
            )
        
        return name
    
    @staticmethod
    def validate_filename(filename: str) -> str:
        """Validate a filename.
        
        Args:
            filename: The filename to validate
            
        Returns:
            The validated filename
            
        Raises:
            ValidationError: If validation fails
        """
        if not filename:
            raise ValidationError("filename", "Cannot be empty")
        
        if not isinstance(filename, str):
            raise ValidationError("filename", "Must be a string")
        
        filename = filename.strip()
        
        if not Validators.FILENAME_PATTERN.match(filename):
            raise ValidationError(
                "filename",
                "Contains invalid characters. Only alphanumeric, dash, underscore, space and dot allowed"
            )
        
        return filename
    
    @staticmethod
    def validate_tags(tags: Union[str, List[str]]) -> List[str]:
        """Validate and normalize tags.
        
        Args:
            tags: The tags to validate (comma-separated string or list)
            
        Returns:
            The validated list of tags
            
        Raises:
            ValidationError: If validation fails
        """
        if not tags:
            return []
        
        # Convert string to list
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(',')]
        
        if not isinstance(tags, list):
            raise ValidationError("tags", "Must be a list or comma-separated string")
        
        # Validate each tag
        validated = []
        for tag in tags:
            if not isinstance(tag, str):
                continue
            
            tag = tag.strip().lower()
            if not tag:
                continue
            
            if len(tag) > Validators.MAX_TAG_LENGTH:
                raise ValidationError(
                    "tags",
                    f"Tag '{tag}' exceeds maximum length of {Validators.MAX_TAG_LENGTH}"
                )
            
            if not Validators.TAG_PATTERN.match(tag):
                raise ValidationError(
                    "tags",
                    f"Tag '{tag}' contains invalid characters"
                )
            
            if tag not in validated:
                validated.append(tag)
        
        if len(validated) > Validators.MAX_TAGS:
            raise ValidationError(
                "tags",
                f"Too many tags. Maximum allowed is {Validators.MAX_TAGS}"
            )
        
        return validated
    
    @staticmethod
    def validate_id(id_value: Any, field_name: str = "id") -> int:
        """Validate an ID field.
        
        Args:
            id_value: The ID to validate
            field_name: The name of the field for error messages
            
        Returns:
            The validated ID as integer
            
        Raises:
            ValidationError: If validation fails
        """
        try:
            id_int = int(id_value)
            if id_int <= 0:
                raise ValidationError(field_name, "Must be a positive integer")
            return id_int
        except (TypeError, ValueError):
            raise ValidationError(field_name, "Must be a valid integer")
    
    @staticmethod
    def validate_pagination(limit: Any = None, offset: Any = None) -> Tuple[int, int]:
        """Validate pagination parameters.
        
        Args:
            limit: The limit parameter
            offset: The offset parameter
            
        Returns:
            Tuple of (limit, offset) with defaults
            
        Raises:
            ValidationError: If validation fails
        """
        # Validate limit
        if limit is None:
            limit = 100
        else:
            try:
                limit = int(limit)
                if limit <= 0:
                    raise ValidationError("limit", "Must be positive")
                if limit > 1000:
                    raise ValidationError("limit", "Cannot exceed 1000")
            except (TypeError, ValueError):
                raise ValidationError("limit", "Must be a valid integer")
        
        # Validate offset
        if offset is None:
            offset = 0
        else:
            try:
                offset = int(offset)
                if offset < 0:
                    raise ValidationError("offset", "Cannot be negative")
            except (TypeError, ValueError):
                raise ValidationError("offset", "Must be a valid integer")
        
        return limit, offset
    
    @staticmethod
    def calculate_hash(content: str) -> str:
        """Calculate SHA256 hash of content.
        
        Args:
            content: The content to hash
            
        Returns:
            The hexadecimal hash string
        """
        return hashlib.sha256(content.encode()).hexdigest()
