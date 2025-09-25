"""Duplicate detection utilities using SHA256 hashing.

This module provides utilities for detecting duplicate prompts and images
using SHA256 hashing for efficient comparison.
"""

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .logging import get_logger

logger = get_logger("promptmanager.utils.duplicate_detector")


class DuplicateDetector:
    """Detect duplicates using SHA256 hashing."""
    
    @classmethod
    def hash_prompt(cls, prompt: str, negative_prompt: str = "") -> str:
        """Generate SHA256 hash for a prompt.
        
        Args:
            prompt: Main prompt text
            negative_prompt: Negative prompt text
            
        Returns:
            SHA256 hash string
        """
        # Normalize text
        prompt = cls._normalize_text(prompt)
        negative_prompt = cls._normalize_text(negative_prompt)
        
        # Combine with separator
        content = f"{prompt}|{negative_prompt}"
        
        # Generate hash
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    @classmethod
    def hash_file(cls, file_path: str, chunk_size: int = 8192) -> Optional[str]:
        """Generate SHA256 hash for a file.
        
        Args:
            file_path: Path to file
            chunk_size: Size of chunks to read
            
        Returns:
            SHA256 hash string or None if error
        """
        path = Path(file_path)
        
        if not path.exists():
            logger.error(f"File not found: {file_path}")
            return None
        
        try:
            sha256_hash = hashlib.sha256()
            
            with open(file_path, "rb") as f:
                while chunk := f.read(chunk_size):
                    sha256_hash.update(chunk)
            
            return sha256_hash.hexdigest()
            
        except Exception as e:
            logger.error(f"Error hashing file {file_path}: {e}")
            return None
    
    @classmethod
    def hash_image_content(cls, image_data: bytes) -> str:
        """Generate SHA256 hash for image data.
        
        Args:
            image_data: Image bytes
            
        Returns:
            SHA256 hash string
        """
        return hashlib.sha256(image_data).hexdigest()
    
    @classmethod
    def find_duplicate_prompts(cls, prompts: List[Dict[str, Any]]) -> List[List[int]]:
        """Find duplicate prompts in a list.
        
        Args:
            prompts: List of prompt dictionaries with 'id', 'prompt', 'negative_prompt'
            
        Returns:
            List of lists, each containing IDs of duplicate prompts
        """
        # Build hash map
        hash_map: Dict[str, List[int]] = {}
        
        for prompt_data in prompts:
            prompt_id = prompt_data.get("id")
            if not prompt_id:
                continue
            
            prompt_text = prompt_data.get("prompt", "")
            negative_text = prompt_data.get("negative_prompt", "")
            
            hash_value = cls.hash_prompt(prompt_text, negative_text)
            
            if hash_value not in hash_map:
                hash_map[hash_value] = []
            hash_map[hash_value].append(prompt_id)
        
        # Extract duplicate groups
        duplicates = []
        for hash_value, ids in hash_map.items():
            if len(ids) > 1:
                duplicates.append(ids)
        
        return duplicates
    
    @classmethod
    def find_duplicate_files(cls, file_paths: List[str]) -> List[List[str]]:
        """Find duplicate files by content hash.
        
        Args:
            file_paths: List of file paths
            
        Returns:
            List of lists, each containing paths of duplicate files
        """
        # Build hash map
        hash_map: Dict[str, List[str]] = {}
        
        for file_path in file_paths:
            hash_value = cls.hash_file(file_path)
            if not hash_value:
                continue
            
            if hash_value not in hash_map:
                hash_map[hash_value] = []
            hash_map[hash_value].append(file_path)
        
        # Extract duplicate groups
        duplicates = []
        for hash_value, paths in hash_map.items():
            if len(paths) > 1:
                duplicates.append(paths)
        
        return duplicates
    
    @classmethod
    def compare_prompts(cls, prompt1: Dict[str, Any], prompt2: Dict[str, Any]) -> bool:
        """Compare two prompts for equality.
        
        Args:
            prompt1: First prompt dictionary
            prompt2: Second prompt dictionary
            
        Returns:
            True if prompts are identical
        """
        hash1 = cls.hash_prompt(
            prompt1.get("prompt", ""),
            prompt1.get("negative_prompt", "")
        )
        hash2 = cls.hash_prompt(
            prompt2.get("prompt", ""),
            prompt2.get("negative_prompt", "")
        )
        
        return hash1 == hash2
    
    @classmethod
    def compare_files(cls, file1: str, file2: str) -> bool:
        """Compare two files for identical content.
        
        Args:
            file1: Path to first file
            file2: Path to second file
            
        Returns:
            True if files have identical content
        """
        hash1 = cls.hash_file(file1)
        hash2 = cls.hash_file(file2)
        
        if not hash1 or not hash2:
            return False
        
        return hash1 == hash2
    
    @classmethod
    def _normalize_text(cls, text: str) -> str:
        """Normalize text for consistent hashing.
        
        Args:
            text: Text to normalize
            
        Returns:
            Normalized text
        """
        if not text:
            return ""
        
        # Strip whitespace
        text = text.strip()
        
        # Normalize line endings
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # Remove multiple spaces
        import re
        text = re.sub(r'\s+', ' ', text)
        
        # Remove trailing whitespace from lines
        lines = text.split('\n')
        lines = [line.rstrip() for line in lines]
        text = '\n'.join(lines)
        
        return text
    
    @classmethod
    def get_similarity_score(cls, text1: str, text2: str) -> float:
        """Calculate similarity score between two texts.
        
        Uses Levenshtein distance for fuzzy matching.
        
        Args:
            text1: First text
            text2: Second text
            
        Returns:
            Similarity score (0.0 to 1.0)
        """
        if not text1 or not text2:
            return 0.0 if (text1 or text2) else 1.0
        
        # Normalize texts
        text1 = cls._normalize_text(text1)
        text2 = cls._normalize_text(text2)
        
        # Use simple ratio for now
        # Could be replaced with more sophisticated algorithm
        return cls._simple_similarity(text1, text2)
    
    @classmethod
    def _simple_similarity(cls, text1: str, text2: str) -> float:
        """Simple similarity calculation.
        
        Args:
            text1: First text
            text2: Second text
            
        Returns:
            Similarity score (0.0 to 1.0)
        """
        if text1 == text2:
            return 1.0
        
        # Character-based similarity
        set1 = set(text1)
        set2 = set(text2)
        
        if not set1 or not set2:
            return 0.0
        
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        
        return intersection / union if union > 0 else 0.0
    
    @classmethod
    def find_similar_prompts(cls, prompts: List[Dict[str, Any]], 
                            threshold: float = 0.8) -> List[Tuple[int, int, float]]:
        """Find similar prompts using fuzzy matching.
        
        Args:
            prompts: List of prompt dictionaries
            threshold: Similarity threshold (0.0 to 1.0)
            
        Returns:
            List of tuples (id1, id2, similarity_score)
        """
        similar_pairs = []
        
        for i, prompt1 in enumerate(prompts):
            for prompt2 in prompts[i + 1:]:
                # Compare main prompts
                score = cls.get_similarity_score(
                    prompt1.get("prompt", ""),
                    prompt2.get("prompt", "")
                )
                
                # Also consider negative prompts
                neg_score = cls.get_similarity_score(
                    prompt1.get("negative_prompt", ""),
                    prompt2.get("negative_prompt", "")
                )
                
                # Average the scores
                total_score = (score + neg_score) / 2
                
                if total_score >= threshold:
                    similar_pairs.append((
                        prompt1.get("id"),
                        prompt2.get("id"),
                        total_score
                    ))
        
        # Sort by similarity score (highest first)
        similar_pairs.sort(key=lambda x: x[2], reverse=True)
        
        return similar_pairs
    
    @classmethod
    def deduplicate_list(cls, items: List[Dict[str, Any]], 
                        key_fields: List[str]) -> List[Dict[str, Any]]:
        """Remove duplicates from a list based on key fields.
        
        Args:
            items: List of dictionaries
            key_fields: Fields to use for duplicate detection
            
        Returns:
            List with duplicates removed (keeps first occurrence)
        """
        seen_hashes = set()
        unique_items = []
        
        for item in items:
            # Build hash from key fields
            key_values = []
            for field in key_fields:
                value = item.get(field, "")
                if isinstance(value, str):
                    value = cls._normalize_text(value)
                key_values.append(str(value))
            
            hash_value = hashlib.sha256(
                "|".join(key_values).encode('utf-8')
            ).hexdigest()
            
            if hash_value not in seen_hashes:
                seen_hashes.add(hash_value)
                unique_items.append(item)
        
        return unique_items
    
    @classmethod
    def merge_duplicates(cls, duplicates: List[Dict[str, Any]], 
                        merge_strategy: str = "newest") -> Dict[str, Any]:
        """Merge duplicate items into one.
        
        Args:
            duplicates: List of duplicate items
            merge_strategy: Strategy for merging ('newest', 'oldest', 'highest_rated')
            
        Returns:
            Merged item
        """
        if not duplicates:
            return {}
        
        if len(duplicates) == 1:
            return duplicates[0]
        
        # Sort based on strategy
        if merge_strategy == "newest":
            duplicates.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        elif merge_strategy == "oldest":
            duplicates.sort(key=lambda x: x.get("created_at", ""))
        elif merge_strategy == "highest_rated":
            duplicates.sort(key=lambda x: x.get("rating", 0), reverse=True)
        
        # Start with base item
        merged = duplicates[0].copy()
        
        # Merge execution counts
        total_count = sum(d.get("execution_count", 0) for d in duplicates)
        merged["execution_count"] = total_count
        
        # Keep highest rating
        max_rating = max(d.get("rating", 0) for d in duplicates)
        merged["rating"] = max_rating
        
        # Merge tags
        all_tags = set()
        for d in duplicates:
            tags = d.get("tags", [])
            if isinstance(tags, list):
                all_tags.update(tags)
        merged["tags"] = list(all_tags)
        
        # Add merge info
        merged["merged_from"] = [d.get("id") for d in duplicates if d.get("id")]
        merged["merge_count"] = len(duplicates)
        
        return merged
