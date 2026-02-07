"""
Basic tests for PromptManager functionality.
"""

import os
import tempfile
import unittest
from unittest.mock import Mock, patch

# Add the parent directory to the path for imports
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash
from utils.validators import validate_prompt_text, validate_rating, validate_tags


class TestBasicFunctionality(unittest.TestCase):
    """Test basic functionality of PromptManager components."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Use a temporary database for testing
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.db = PromptDatabase(self.temp_db.name)
    
    def tearDown(self):
        """Clean up test fixtures."""
        # Remove temporary database
        if os.path.exists(self.temp_db.name):
            os.unlink(self.temp_db.name)
    
    def test_prompt_hash_generation(self):
        """Test prompt hash generation."""
        text1 = "A beautiful landscape with mountains"
        text2 = "A BEAUTIFUL LANDSCAPE WITH MOUNTAINS"  # Different case
        text3 = "  A beautiful landscape with mountains  "  # Extra whitespace
        text4 = "A different prompt"
        
        hash1 = generate_prompt_hash(text1)
        hash2 = generate_prompt_hash(text2)
        hash3 = generate_prompt_hash(text3)
        hash4 = generate_prompt_hash(text4)
        
        # Same content should produce same hash (case and whitespace insensitive)
        self.assertEqual(hash1, hash2)
        self.assertEqual(hash1, hash3)
        
        # Different content should produce different hash
        self.assertNotEqual(hash1, hash4)
        
        # Hash should be 64 characters (SHA256 hex)
        self.assertEqual(len(hash1), 64)
    
    def test_prompt_validation(self):
        """Test prompt text validation."""
        # Valid prompts
        self.assertTrue(validate_prompt_text("Valid prompt text"))
        self.assertTrue(validate_prompt_text("Another valid prompt"))
        
        # Invalid prompts
        with self.assertRaises(ValueError):
            validate_prompt_text("")  # Empty
        
        with self.assertRaises(ValueError):
            validate_prompt_text("   ")  # Whitespace only
        
        with self.assertRaises(ValueError):
            validate_prompt_text("x" * 10001)  # Too long
        
        with self.assertRaises(ValueError):
            validate_prompt_text(123)  # Not a string
    
    def test_rating_validation(self):
        """Test rating validation."""
        # Valid ratings
        self.assertTrue(validate_rating(None))
        self.assertTrue(validate_rating(1))
        self.assertTrue(validate_rating(3))
        self.assertTrue(validate_rating(5))
        
        # Invalid ratings
        with self.assertRaises(ValueError):
            validate_rating(0)  # Too low
        
        with self.assertRaises(ValueError):
            validate_rating(6)  # Too high
        
        with self.assertRaises(ValueError):
            validate_rating("3")  # Not an integer
    
    def test_tags_validation(self):
        """Test tags validation."""
        # Valid tags
        self.assertTrue(validate_tags(None))
        self.assertTrue(validate_tags([]))
        self.assertTrue(validate_tags(["tag1", "tag2"]))
        self.assertTrue(validate_tags("tag1, tag2, tag3"))
        
        # Invalid tags
        with self.assertRaises(ValueError):
            validate_tags([""])  # Empty tag
        
        with self.assertRaises(ValueError):
            validate_tags(["x" * 51])  # Tag too long
        
        with self.assertRaises(ValueError):
            validate_tags(["tag"] * 21)  # Too many tags
    
    def test_database_save_and_retrieve(self):
        """Test basic database save and retrieve operations."""
        # Save a prompt
        prompt_id = self.db.save_prompt(
            text="Test prompt for database",
            category="test",
            tags=["test", "database"],
            rating=4,
            notes="Test notes",
            prompt_hash=generate_prompt_hash("Test prompt for database")
        )
        
        self.assertIsInstance(prompt_id, int)
        self.assertGreater(prompt_id, 0)
        
        # Retrieve the prompt
        retrieved = self.db.get_prompt_by_id(prompt_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved['text'], "Test prompt for database")
        self.assertEqual(retrieved['category'], "test")
        self.assertEqual(retrieved['tags'], ["test", "database"])
        self.assertEqual(retrieved['rating'], 4)
        self.assertEqual(retrieved['notes'], "Test notes")
    
    def test_duplicate_detection(self):
        """Test duplicate prompt detection."""
        text = "Duplicate test prompt"
        hash_val = generate_prompt_hash(text)
        
        # Save original prompt
        prompt_id1 = self.db.save_prompt(
            text=text,
            category="test",
            prompt_hash=hash_val
        )
        
        # Try to save the same prompt again
        existing = self.db.get_prompt_by_hash(hash_val)
        self.assertIsNotNone(existing)
        self.assertEqual(existing['id'], prompt_id1)
    
    def test_search_functionality(self):
        """Test prompt search functionality."""
        # Save multiple prompts
        prompts = [
            {
                "text": "Beautiful landscape with mountains",
                "category": "landscape",
                "tags": ["nature", "mountains"],
                "rating": 5
            },
            {
                "text": "Portrait of a woman",
                "category": "portrait",
                "tags": ["people", "woman"],
                "rating": 4
            },
            {
                "text": "Abstract art piece",
                "category": "abstract",
                "tags": ["art", "abstract"],
                "rating": 3
            }
        ]
        
        for prompt_data in prompts:
            self.db.save_prompt(
                text=prompt_data["text"],
                category=prompt_data["category"],
                tags=prompt_data["tags"],
                rating=prompt_data["rating"],
                prompt_hash=generate_prompt_hash(prompt_data["text"])
            )
        
        # Test text search
        results = self.db.search_prompts(text="landscape")
        self.assertEqual(len(results), 1)
        self.assertIn("landscape", results[0]['text'])
        
        # Test category search
        results = self.db.search_prompts(category="portrait")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['category'], "portrait")
        
        # Test rating search
        results = self.db.search_prompts(rating_min=4)
        self.assertEqual(len(results), 2)  # Rating 4 and 5
        
        # Test tag search
        results = self.db.search_prompts(tags=["nature"])
        self.assertEqual(len(results), 1)
        self.assertIn("nature", results[0]['tags'])


class TestNodeIntegration(unittest.TestCase):
    """Test the actual node integration (mocked)."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Use a temporary database for testing
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
    
    def tearDown(self):
        """Clean up test fixtures."""
        # Remove temporary database
        if os.path.exists(self.temp_db.name):
            os.unlink(self.temp_db.name)
    
    @patch('prompt_manager_base.PromptDatabase')
    def test_node_encode_function(self, mock_db_class):
        """Test the node's encode function with mocked dependencies."""
        # Mock the database
        mock_db = Mock()
        mock_db_class.return_value = mock_db
        mock_db.save_prompt.return_value = 1
        mock_db.get_prompt_by_hash.return_value = None

        # Mock CLIP model
        mock_clip = Mock()
        mock_clip.tokenize.return_value = "mock_tokens"
        mock_clip.encode_from_tokens_scheduled.return_value = "mock_conditioning"

        # Import and test the node
        from prompt_manager import PromptManager

        node = PromptManager()

        # Test encoding
        result = node.encode_prompt(
            clip=mock_clip,
            text="Test prompt",
            search_text=""
        )

        # Verify CLIP was called correctly
        mock_clip.tokenize.assert_called_once_with("Test prompt")
        mock_clip.encode_from_tokens_scheduled.assert_called_once_with("mock_tokens")

        # Verify result tuple (conditioning, prompt_text)
        self.assertEqual(result[1], "Test prompt")
    
    def test_node_input_types(self):
        """Test the node's input type definitions."""
        from prompt_manager import PromptManager
        
        input_types = PromptManager.INPUT_TYPES()
        
        # Check required inputs
        self.assertIn("text", input_types["required"])
        self.assertIn("clip", input_types["required"])
        
        # Check optional inputs
        self.assertIn("search_text", input_types["optional"])


if __name__ == '__main__':
    unittest.main()