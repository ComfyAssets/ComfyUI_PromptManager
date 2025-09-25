"""Hashing utilities for PromptManager.

Provides various hashing algorithms for prompts, images, and cache keys
including SHA256, perceptual hashing, and collision detection.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from PIL import Image
    import imagehash
    HAS_IMAGEHASH = True
except ImportError:
    HAS_IMAGEHASH = False

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.hashing")


class HashGenerator:
    """Generate various types of hashes for different data types."""
    
    @staticmethod
    def sha256(data: Union[str, bytes]) -> str:
        """Generate SHA256 hash of data.
        
        Args:
            data: String or bytes to hash
            
        Returns:
            Hexadecimal hash string
        """
        if isinstance(data, str):
            data = data.encode('utf-8')
        
        return hashlib.sha256(data).hexdigest()
    
    @staticmethod
    def sha256_file(file_path: Union[str, Path]) -> str:
        """Generate SHA256 hash of a file.
        
        Args:
            file_path: Path to file
            
        Returns:
            Hexadecimal hash string
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        sha256_hash = hashlib.sha256()
        with open(path, "rb") as f:
            # Read in chunks for large files
            for chunk in iter(lambda: f.read(65536), b""):
                sha256_hash.update(chunk)
        
        return sha256_hash.hexdigest()
    
    @staticmethod
    def md5(data: Union[str, bytes]) -> str:
        """Generate MD5 hash (for non-security purposes only).
        
        Args:
            data: String or bytes to hash
            
        Returns:
            Hexadecimal hash string
        """
        if isinstance(data, str):
            data = data.encode('utf-8')
        
        return hashlib.md5(data).hexdigest()
    
    @staticmethod
    def blake2b(data: Union[str, bytes], digest_size: int = 32) -> str:
        """Generate BLAKE2b hash (faster than SHA256).
        
        Args:
            data: String or bytes to hash
            digest_size: Size of digest in bytes (1-64)
            
        Returns:
            Hexadecimal hash string
        """
        if isinstance(data, str):
            data = data.encode('utf-8')
        
        return hashlib.blake2b(data, digest_size=digest_size).hexdigest()


class PromptHasher:
    """Specialized hashing for prompt text with normalization."""
    
    @staticmethod
    def normalize_prompt(text: str) -> str:
        """Normalize prompt text for consistent hashing.
        
        Args:
            text: Original prompt text
            
        Returns:
            Normalized text
        """
        # Convert to lowercase
        text = text.lower()
        
        # Normalize whitespace
        text = ' '.join(text.split())
        
        # Remove duplicate commas and spaces around them
        import re
        text = re.sub(r'\s*,\s*', ', ', text)
        text = re.sub(r',+', ',', text)
        
        # Strip leading/trailing whitespace
        text = text.strip()
        
        return text
    
    @classmethod
    def hash_prompt(
        cls,
        text: str,
        normalize: bool = True,
        include_negative: bool = False,
        negative_text: str = ""
    ) -> str:
        """Generate hash for prompt text.
        
        Args:
            text: Prompt text
            normalize: Whether to normalize before hashing
            include_negative: Include negative prompt in hash
            negative_text: Negative prompt text
            
        Returns:
            SHA256 hash of prompt
        """
        if normalize:
            text = cls.normalize_prompt(text)
            if negative_text:
                negative_text = cls.normalize_prompt(negative_text)
        
        if include_negative and negative_text:
            combined = f"{text}||NEGATIVE||{negative_text}"
        else:
            combined = text
        
        return HashGenerator.sha256(combined)
    
    @classmethod
    def hash_prompt_with_metadata(
        cls,
        text: str,
        metadata: Dict[str, Any],
        normalize: bool = True
    ) -> str:
        """Generate hash including prompt and metadata.
        
        Args:
            text: Prompt text
            metadata: Prompt metadata
            normalize: Whether to normalize text
            
        Returns:
            SHA256 hash
        """
        if normalize:
            text = cls.normalize_prompt(text)
        
        # Create consistent string representation
        meta_str = json.dumps(metadata, sort_keys=True, separators=(',', ':'))
        combined = f"{text}||META||{meta_str}"
        
        return HashGenerator.sha256(combined)


class ImageHasher:
    """Perceptual hashing for images to find similar images."""
    
    def __init__(self):
        """Initialize image hasher."""
        if not HAS_IMAGEHASH:
            logger.warning("imagehash library not available. Install with: pip install imagehash")
        
        self.hash_functions = {
            'average': self.average_hash,
            'perceptual': self.perceptual_hash,
            'difference': self.difference_hash,
            'wavelet': self.wavelet_hash,
            'color': self.color_hash,
        }
    
    def average_hash(self, image_path: Union[str, Path], hash_size: int = 8) -> Optional[str]:
        """Generate average hash of image.
        
        Args:
            image_path: Path to image
            hash_size: Size of hash (default 8 = 64 bit)
            
        Returns:
            Hash string or None if unavailable
        """
        if not HAS_IMAGEHASH:
            return None
        
        try:
            image = Image.open(image_path)
            hash_obj = imagehash.average_hash(image, hash_size)
            return str(hash_obj)
        except Exception as e:
            logger.error(f"Failed to generate average hash: {e}")
            return None
    
    def perceptual_hash(self, image_path: Union[str, Path], hash_size: int = 8) -> Optional[str]:
        """Generate perceptual hash (pHash) of image.
        
        Args:
            image_path: Path to image
            hash_size: Size of hash
            
        Returns:
            Hash string or None if unavailable
        """
        if not HAS_IMAGEHASH:
            return None
        
        try:
            image = Image.open(image_path)
            hash_obj = imagehash.phash(image, hash_size)
            return str(hash_obj)
        except Exception as e:
            logger.error(f"Failed to generate perceptual hash: {e}")
            return None
    
    def difference_hash(self, image_path: Union[str, Path], hash_size: int = 8) -> Optional[str]:
        """Generate difference hash of image.
        
        Args:
            image_path: Path to image
            hash_size: Size of hash
            
        Returns:
            Hash string or None if unavailable
        """
        if not HAS_IMAGEHASH:
            return None
        
        try:
            image = Image.open(image_path)
            hash_obj = imagehash.dhash(image, hash_size)
            return str(hash_obj)
        except Exception as e:
            logger.error(f"Failed to generate difference hash: {e}")
            return None
    
    def wavelet_hash(self, image_path: Union[str, Path], hash_size: int = 8) -> Optional[str]:
        """Generate wavelet hash of image.
        
        Args:
            image_path: Path to image
            hash_size: Size of hash
            
        Returns:
            Hash string or None if unavailable
        """
        if not HAS_IMAGEHASH:
            return None
        
        try:
            image = Image.open(image_path)
            hash_obj = imagehash.whash(image, hash_size)
            return str(hash_obj)
        except Exception as e:
            logger.error(f"Failed to generate wavelet hash: {e}")
            return None
    
    def color_hash(self, image_path: Union[str, Path]) -> Optional[str]:
        """Generate color hash of image.
        
        Args:
            image_path: Path to image
            
        Returns:
            Hash string or None if unavailable
        """
        if not HAS_IMAGEHASH:
            return None
        
        try:
            image = Image.open(image_path)
            hash_obj = imagehash.colorhash(image)
            return str(hash_obj)
        except Exception as e:
            logger.error(f"Failed to generate color hash: {e}")
            return None
    
    def multi_hash(self, image_path: Union[str, Path]) -> Dict[str, Optional[str]]:
        """Generate multiple hash types for an image.
        
        Args:
            image_path: Path to image
            
        Returns:
            Dictionary of hash type to hash value
        """
        results = {}
        for hash_type, hash_func in self.hash_functions.items():
            results[hash_type] = hash_func(image_path)
        
        return results
    
    @staticmethod
    def compare_hashes(hash1: str, hash2: str) -> float:
        """Compare two perceptual hashes.
        
        Args:
            hash1: First hash
            hash2: Second hash
            
        Returns:
            Similarity score (0-1, 1 being identical)
        """
        if not HAS_IMAGEHASH:
            return 0.0
        
        try:
            h1 = imagehash.hex_to_hash(hash1)
            h2 = imagehash.hex_to_hash(hash2)
            
            # Calculate Hamming distance
            distance = h1 - h2
            
            # Convert to similarity (assuming 64-bit hash)
            max_distance = 64
            similarity = 1.0 - (distance / max_distance)
            
            return max(0.0, min(1.0, similarity))
            
        except Exception as e:
            logger.error(f"Failed to compare hashes: {e}")
            return 0.0


class CacheKeyGenerator:
    """Generate cache keys for various operations."""
    
    @staticmethod
    def generate_key(*args, **kwargs) -> str:
        """Generate cache key from arguments.
        
        Args:
            *args: Positional arguments
            **kwargs: Keyword arguments
            
        Returns:
            Cache key string
        """
        # Create consistent string representation
        key_parts = []
        
        # Add positional arguments
        for arg in args:
            if isinstance(arg, (str, int, float, bool)):
                key_parts.append(str(arg))
            else:
                # Use JSON for complex types
                key_parts.append(json.dumps(arg, sort_keys=True, default=str))
        
        # Add keyword arguments (sorted for consistency)
        for k, v in sorted(kwargs.items()):
            if isinstance(v, (str, int, float, bool)):
                key_parts.append(f"{k}={v}")
            else:
                key_parts.append(f"{k}={json.dumps(v, sort_keys=True, default=str)}")
        
        # Generate hash of combined key
        combined = '||'.join(key_parts)
        return HashGenerator.md5(combined)
    
    @staticmethod
    def generate_file_cache_key(file_path: Union[str, Path]) -> str:
        """Generate cache key for file operations.
        
        Args:
            file_path: Path to file
            
        Returns:
            Cache key incorporating file metadata
        """
        path = Path(file_path)
        
        if path.exists():
            stat = path.stat()
            key_data = {
                'path': str(path.absolute()),
                'size': stat.st_size,
                'mtime': stat.st_mtime,
            }
        else:
            key_data = {
                'path': str(path.absolute()),
                'exists': False,
            }
        
        return CacheKeyGenerator.generate_key(**key_data)
    
    @staticmethod
    def generate_api_cache_key(
        endpoint: str,
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None
    ) -> str:
        """Generate cache key for API requests.
        
        Args:
            endpoint: API endpoint
            params: Query parameters
            headers: Request headers (filtered)
            
        Returns:
            Cache key for the request
        """
        # Filter sensitive headers
        safe_headers = {}
        if headers:
            ignore_headers = {'authorization', 'cookie', 'x-api-key'}
            safe_headers = {
                k: v for k, v in headers.items()
                if k.lower() not in ignore_headers
            }
        
        return CacheKeyGenerator.generate_key(
            endpoint=endpoint,
            params=params or {},
            headers=safe_headers
        )


class CollisionDetector:
    """Detect and handle hash collisions."""
    
    def __init__(self):
        """Initialize collision detector."""
        self.hash_registry: Dict[str, List[str]] = {}
    
    def register_hash(self, hash_value: str, original_data: str) -> bool:
        """Register a hash and check for collisions.
        
        Args:
            hash_value: Hash to register
            original_data: Original data that produced the hash
            
        Returns:
            True if collision detected
        """
        if hash_value not in self.hash_registry:
            self.hash_registry[hash_value] = [original_data]
            return False
        
        # Check if it's the same data
        if original_data in self.hash_registry[hash_value]:
            return False
        
        # Collision detected
        self.hash_registry[hash_value].append(original_data)
        logger.warning(f"Hash collision detected for hash {hash_value[:16]}...")
        return True
    
    def get_collisions(self) -> Dict[str, List[str]]:
        """Get all detected collisions.
        
        Returns:
            Dictionary of hash to list of colliding data
        """
        return {
            hash_val: data_list
            for hash_val, data_list in self.hash_registry.items()
            if len(data_list) > 1
        }
    
    def clear(self):
        """Clear the collision registry."""
        self.hash_registry.clear()


# Convenience functions
def hash_prompt(text: str, **kwargs) -> str:
    """Convenience function for prompt hashing."""
    return PromptHasher.hash_prompt(text, **kwargs)

def hash_file(file_path: Union[str, Path]) -> str:
    """Convenience function for file hashing."""
    return HashGenerator.sha256_file(file_path)

def hash_image_perceptual(image_path: Union[str, Path]) -> Optional[str]:
    """Convenience function for perceptual image hashing."""
    hasher = ImageHasher()
    return hasher.perceptual_hash(image_path)

def generate_cache_key(*args, **kwargs) -> str:
    """Convenience function for cache key generation."""
    return CacheKeyGenerator.generate_key(*args, **kwargs)

def generate_prompt_hash(text: str) -> str:
    """Generate a SHA256 hash for prompt text to enable deduplication.

    Normalizes the input text (strips whitespace, converts to lowercase)
    before hashing to ensure consistent results for functionally identical
    prompts with minor formatting differences.

    Args:
        text: The prompt text to hash

    Returns:
        SHA256 hexadecimal digest of the normalized text

    Raises:
        TypeError: If the input is not a string

    Example:
        >>> generate_prompt_hash("Beautiful Landscape")
        'c3d5f8a9b2e1...'
        >>> generate_prompt_hash(" beautiful landscape ")
        'c3d5f8a9b2e1...'
        # Both produce the same hash due to normalization
    """
    if not isinstance(text, str):
        raise TypeError("Text must be a string")

    # Normalize the text by stripping whitespace and converting to lowercase
    # for consistent hashing regardless of minor formatting differences
    normalized_text = text.strip().lower()

    return hashlib.sha256(normalized_text.encode('utf-8')).hexdigest()
