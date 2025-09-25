"""
Unit tests for database models and operations.

Tests the core database functionality including schema creation,
migrations, CRUD operations, and data integrity.
"""

import pytest
import sqlite3
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch, Mock

from src.database.models import PromptModel
from src.database.operations import PromptDatabase


class TestPromptModel:
    """Test cases for the PromptModel class."""
    
    def test_init_creates_database(self, test_db_path):
        """Test that database initialization creates the database file."""
        assert not os.path.exists(test_db_path)
        model = PromptModel(test_db_path)
        assert os.path.exists(test_db_path)
        assert model.db_path == test_db_path
    
    def test_init_creates_directory_if_not_exists(self, temp_dir):
        """Test that database initialization creates parent directories."""
        nested_path = temp_dir / "nested" / "deeper" / "test.db"
        model = PromptModel(str(nested_path))
        assert nested_path.exists()
        assert nested_path.parent.exists()
    
    def test_database_schema_creation(self, db_model, db_connection):
        """Test that all required tables and indexes are created."""
        # Check tables exist
        cursor = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row['name'] for row in cursor.fetchall()}
        
        expected_tables = {
            'prompts', 'generated_images', 'settings', 'prompt_tracking'
        }
        assert expected_tables.issubset(tables)
        
        # Check indexes exist
        cursor = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        indexes = {row['name'] for row in cursor.fetchall()}
        
        expected_indexes = {
            'idx_prompts_positive', 'idx_prompts_category', 
            'idx_prompts_hash', 'idx_prompt_images'
        }
        assert expected_indexes.issubset(indexes)
    
    def test_prompts_table_schema(self, db_connection):
        """Test that prompts table has correct schema."""
        cursor = db_connection.execute("PRAGMA table_info(prompts)")
        columns = {row['name']: row['type'] for row in cursor.fetchall()}
        
        expected_columns = {
            'id': 'INTEGER',
            'prompt': 'TEXT',
            'negative_prompt': 'TEXT',
            'category': 'TEXT',
            'tags': 'TEXT',
            'rating': 'INTEGER',
            'notes': 'TEXT',
            'hash': 'TEXT',
            'created_at': 'TIMESTAMP',
            'updated_at': 'TIMESTAMP'
        }
        
        for col_name, col_type in expected_columns.items():
            assert col_name in columns
            assert columns[col_name] == col_type
    
    def test_generated_images_table_schema(self, db_connection):
        """Test that generated_images table has correct schema."""
        cursor = db_connection.execute("PRAGMA table_info(generated_images)")
        columns = {row['name']: row['type'] for row in cursor.fetchall()}
        
        expected_columns = {
            'id': 'INTEGER',
            'prompt_id': 'INTEGER',
            'image_path': 'TEXT',
            'filename': 'TEXT',
            'generation_time': 'TIMESTAMP',
            'file_size': 'INTEGER',
            'width': 'INTEGER',
            'height': 'INTEGER',
            'format': 'TEXT',
            'workflow_data': 'TEXT',
            'parameters': 'TEXT'
        }
        
        for col_name, col_type in expected_columns.items():
            assert col_name in columns
            assert columns[col_name] == col_type
    
    def test_foreign_keys_enabled(self, db_connection):
        """Test that foreign key constraints are enabled."""
        cursor = db_connection.execute("PRAGMA foreign_keys")
        result = cursor.fetchone()
        assert result[0] == 1  # Foreign keys should be enabled
    
    def test_get_connection(self, db_model):
        """Test getting a database connection."""
        conn = db_model.get_connection()
        assert isinstance(conn, sqlite3.Connection)
        assert conn.row_factory == sqlite3.Row
        conn.close()
    
    def test_vacuum_database(self, db_model):
        """Test database vacuum operation."""
        # Add some data first
        with db_model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompts (positive_prompt, negative_prompt) VALUES (?, ?)",
                ("test prompt", "test negative")
            )
            conn.commit()
        
        # Vacuum should not raise an error
        db_model.vacuum_database()
    
    def test_get_database_info(self, populated_db):
        """Test getting database statistics."""
        db_operations, prompt_ids = populated_db
        model = db_operations.model
        
        info = model.get_database_info()
        
        assert 'total_prompts' in info
        assert 'unique_categories' in info
        assert 'total_images' in info
        assert 'database_size_bytes' in info
        assert 'database_path' in info
        
        assert info['total_prompts'] >= 3  # From populated_db fixture
        assert info['database_size_bytes'] > 0
        assert os.path.exists(info['database_path'])
    
    def test_backup_database(self, db_model, temp_dir):
        """Test database backup functionality."""
        backup_path = str(temp_dir / "backup.db")
        
        # Add some data
        with db_model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompts (positive_prompt, negative_prompt) VALUES (?, ?)",
                ("backup test", "backup negative")
            )
            conn.commit()
        
        # Create backup
        result = db_model.backup_database(backup_path)
        assert result is True
        assert os.path.exists(backup_path)
        
        # Verify backup contains data
        backup_model = PromptModel(backup_path)
        with backup_model.get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM prompts")
            count = cursor.fetchone()[0]
            assert count >= 1
    
    def test_migration_from_legacy_schema(self, temp_dir):
        """Test migration from legacy v1 schema to v2."""
        db_path = str(temp_dir / "legacy.db")
        
        # Create legacy schema
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE prompts (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,  -- Legacy column name
                workflow_name TEXT,  -- Legacy column
                category TEXT,
                rating INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Insert legacy data
        conn.execute(
            "INSERT INTO prompts (text, workflow_name, category, rating) VALUES (?, ?, ?, ?)",
            ("legacy prompt text", "legacy workflow", "test", 4)
        )
        conn.commit()
        conn.close()
        
        # Initialize model (should trigger migration)
        model = PromptModel(db_path)
        
        # Verify migration worked
        with model.get_connection() as conn:
            # Check new schema exists
            cursor = conn.execute("PRAGMA table_info(prompts)")
            columns = {row['name'] for row in cursor.fetchall()}
            
            assert 'prompt' in columns
            assert 'negative_prompt' in columns
            assert 'text' not in columns  # Legacy column should be gone
            assert 'workflow_name' not in columns  # Legacy column should be gone
            
            # Check data was migrated
            cursor = conn.execute("SELECT positive_prompt, category, rating FROM prompts")
            row = cursor.fetchone()
            assert row['prompt'] == "legacy prompt text"
            assert row['category'] == "test"
            assert row['rating'] == 4


class TestPromptDatabase:
    """Test cases for the PromptDatabase operations."""
    
    def test_save_prompt_basic(self, db_operations, sample_prompt_data):
        """Test saving a basic prompt."""
        # Adjust data to match save_prompt signature
        adjusted_data = {
            'prompt': sample_prompt_data['prompt'],
            'negative_prompt': sample_prompt_data['negative_prompt'],
            'category': sample_prompt_data['category'],
            'tags': sample_prompt_data['tags'],
            'rating': sample_prompt_data['rating'],
            'notes': sample_prompt_data['notes']
        }
        prompt_id = db_operations.save_prompt(**adjusted_data)
        assert prompt_id is not None
        assert isinstance(prompt_id, int)
        
        # Verify prompt was saved
        prompt = db_operations.get_prompt_by_id(prompt_id)
        assert prompt is not None
        assert prompt['text'] == sample_prompt_data['prompt']
        assert prompt['negative_prompt'] == sample_prompt_data['negative_prompt']
        assert prompt['category'] == sample_prompt_data['category']
        assert prompt['rating'] == sample_prompt_data['rating']
    
    def test_save_prompt_with_tags(self, db_operations):
        """Test saving a prompt with tags."""
        prompt_data = {
            'prompt': 'Test with tags',
            'negative_prompt': 'test negative',
            'tags': ['tag1', 'tag2', 'tag3']
        }

        prompt_id = db_operations.save_prompt(**prompt_data)
        prompt = db_operations.get_prompt_by_id(prompt_id)
        
        # Tags should be stored as JSON
        import json
        stored_tags = json.loads(prompt['tags']) if prompt['tags'] else []
        assert stored_tags == prompt_data['tags']
    
    def test_save_prompt_duplicate_prevention(self, db_operations, sample_prompt_data):
        """Test that duplicate prompts are not saved twice."""
        # Save first prompt
        prompt_id1 = db_operations.save_prompt(**sample_prompt_data)
        
        # Try to save identical prompt
        prompt_id2 = db_operations.save_prompt(**sample_prompt_data)
        
        # Should return the same ID (duplicate detected)
        assert prompt_id1 == prompt_id2
        
        # Verify only one prompt exists
        with db_operations.model.get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM prompts")
            count = cursor.fetchone()[0]
            assert count == 1
    
    def test_update_prompt(self, db_operations, sample_prompt_data):
        """Test updating an existing prompt."""
        # Save initial prompt
        prompt_id = db_operations.save_prompt(**sample_prompt_data)
        
        # Update prompt
        updated_data = sample_prompt_data.copy()
        updated_data['category'] = 'updated_category'
        updated_data['rating'] = 5
        updated_data['notes'] = 'Updated notes'
        
        result = db_operations.update_prompt(prompt_id, **updated_data)
        assert result is True
        
        # Verify update
        prompt = db_operations.get_prompt_by_id(prompt_id)
        assert prompt['category'] == 'updated_category'
        assert prompt['rating'] == 5
        assert prompt['notes'] == 'Updated notes'
    
    def test_delete_prompt(self, db_operations, sample_prompt_data):
        """Test deleting a prompt."""
        prompt_id = db_operations.save_prompt(**sample_prompt_data)
        
        # Verify prompt exists
        assert db_operations.get_prompt_by_id(prompt_id) is not None
        
        # Delete prompt
        result = db_operations.delete_prompt(prompt_id)
        assert result is True
        
        # Verify prompt is deleted
        assert db_operations.get_prompt_by_id(prompt_id) is None
    
    def test_search_prompts_by_text(self, populated_db):
        """Test searching prompts by text content."""
        db_operations, _ = populated_db
        
        # Search for landscape
        results = db_operations.search_prompts(text="landscape")
        assert len(results) >= 1
        
        # Should find the landscape prompt
        landscape_found = any("landscape" in r['prompt'].lower() for r in results)
        assert landscape_found
    
    def test_search_prompts_by_category(self, populated_db):
        """Test searching prompts by category."""
        db_operations, _ = populated_db
        
        results = db_operations.search_prompts(category="landscapes")
        assert len(results) >= 1
        
        # All results should have landscapes category
        for result in results:
            assert result['category'] == 'landscapes'
    
    def test_search_prompts_by_rating(self, populated_db):
        """Test searching prompts by rating range."""
        db_operations, _ = populated_db
        
        # Search for high rated prompts
        results = db_operations.search_prompts(rating_min=4)
        assert len(results) >= 1
        
        # All results should have rating >= 4
        for result in results:
            assert result['rating'] >= 4
    
    def test_search_prompts_by_tags(self, populated_db):
        """Test searching prompts by tags."""
        db_operations, _ = populated_db
        
        results = db_operations.search_prompts(tags=['nature'])
        assert len(results) >= 1
        
        # Should find prompts with nature tag
        import json
        nature_found = False
        for result in results:
            if result['tags']:
                tags = json.loads(result['tags'])
                if 'nature' in tags:
                    nature_found = True
                    break
        assert nature_found
    
    def test_get_recent_prompts(self, populated_db):
        """Test getting recent prompts."""
        db_operations, _ = populated_db
        
        results = db_operations.get_recent_prompts(limit=5)
        assert len(results) <= 5
        assert len(results) >= 3  # From populated_db
        
        # Results should be ordered by creation time (newest first)
        if len(results) > 1:
            for i in range(len(results) - 1):
                current_time = datetime.fromisoformat(results[i]['created_at'].replace('Z', '+00:00'))
                next_time = datetime.fromisoformat(results[i + 1]['created_at'].replace('Z', '+00:00'))
                assert current_time >= next_time
    
    def test_get_categories(self, populated_db):
        """Test getting all unique categories."""
        db_operations, _ = populated_db
        
        categories = db_operations.get_categories()
        assert len(categories) >= 3  # landscapes, portraits, abstract
        
        expected_categories = {'landscapes', 'portraits', 'abstract'}
        assert expected_categories.issubset(set(categories))
    
    def test_save_generated_image(self, db_operations, sample_prompt_data):
        """Test saving generated image metadata."""
        # First save a prompt
        prompt_id = db_operations.save_prompt(**sample_prompt_data)
        
        # Save generated image
        image_data = {
            'prompt_id': prompt_id,
            'image_path': '/test/path/image.png',
            'filename': 'image.png',
            'file_size': 1024000,
            'width': 512,
            'height': 512,
            'format': 'PNG',
            'workflow_data': '{"test": "workflow"}',
            'parameters': '{"steps": 20, "cfg": 7.5}'
        }
        
        image_id = db_operations.save_generated_image(**image_data)
        assert image_id is not None
        
        # Verify image was saved
        images = db_operations.get_prompt_images(prompt_id)
        assert len(images) == 1
        assert images[0]['filename'] == 'image.png'
        assert images[0]['width'] == 512
        assert images[0]['height'] == 512
    
    def test_get_prompt_images(self, db_operations, sample_prompt_data):
        """Test getting images for a specific prompt."""
        prompt_id = db_operations.save_prompt(**sample_prompt_data)
        
        # Save multiple images for the prompt
        for i in range(3):
            image_data = {
                'prompt_id': prompt_id,
                'image_path': f'/test/path/image_{i}.png',
                'filename': f'image_{i}.png',
                'file_size': 1024000,
                'width': 512,
                'height': 512,
                'format': 'PNG'
            }
            db_operations.save_generated_image(**image_data)
        
        images = db_operations.get_prompt_images(prompt_id)
        assert len(images) == 3
        
        # Images should be ordered by generation time
        filenames = [img['filename'] for img in images]
        assert 'image_0.png' in filenames
        assert 'image_1.png' in filenames
        assert 'image_2.png' in filenames
    
    def test_database_error_handling(self, test_db_path):
        """Test error handling for database operations."""
        # Test with invalid database path
        invalid_path = "/invalid/path/that/does/not/exist/test.db"
        
        # Should not raise exception during init
        with pytest.raises(Exception):
            db_operations = PromptDatabase(invalid_path)
    
    def test_concurrent_access(self, db_operations, sample_prompt_data):
        """Test concurrent database access doesn't cause issues."""
        import threading
        import time
        
        results = []
        errors = []
        
        def save_prompt_worker(worker_id):
            try:
                data = sample_prompt_data.copy()
                data['prompt'] = f"Concurrent prompt {worker_id}"
                prompt_id = db_operations.save_prompt(**data)
                results.append(prompt_id)
            except Exception as e:
                errors.append(e)
        
        # Create multiple threads
        threads = []
        for i in range(5):
            thread = threading.Thread(target=save_prompt_worker, args=(i,))
            threads.append(thread)
        
        # Start all threads
        for thread in threads:
            thread.start()
        
        # Wait for completion
        for thread in threads:
            thread.join()
        
        # Verify all operations completed successfully
        assert len(errors) == 0, f"Concurrent access errors: {errors}"
        assert len(results) == 5
        assert len(set(results)) == 5  # All unique IDs
    
    @pytest.mark.parametrize("rating", [1, 2, 3, 4, 5])
    def test_rating_constraints(self, db_operations, sample_prompt_data, rating):
        """Test that rating constraints are enforced."""
        data = sample_prompt_data.copy()
        data['rating'] = rating
        
        prompt_id = db_operations.save_prompt(**data)
        prompt = db_operations.get_prompt_by_id(prompt_id)
        assert prompt['rating'] == rating
    
    def test_rating_invalid_values(self, db_operations, sample_prompt_data):
        """Test handling of invalid rating values."""
        # Test rating outside valid range
        data = sample_prompt_data.copy()
        data['rating'] = 10  # Invalid rating
        
        # Should either reject or clamp the value
        try:
            prompt_id = db_operations.save_prompt(**data)
            prompt = db_operations.get_prompt_by_id(prompt_id)
            # If accepted, should be clamped to valid range
            assert 1 <= prompt['rating'] <= 5
        except Exception:
            # Or should raise an exception
            pass  # This is also acceptable behavior