"""
Unit tests for repository classes.

Tests the repository layer that provides data access abstraction
over the database models and operations.
"""

import pytest
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from src.repositories.prompt_repository import PromptRepository
from src.repositories.generated_image_repository import GeneratedImageRepository


class TestPromptRepository:
    """Test cases for the PromptRepository class."""
    
    def test_create_prompt(self, prompt_repository, sample_prompt_data):
        """Test creating a new prompt through repository."""
        prompt_id = prompt_repository.create(sample_prompt_data)
        
        assert prompt_id is not None
        assert isinstance(prompt_id, int)
        
        # Verify prompt was created
        prompt = prompt_repository.get_by_id(prompt_id)
        assert prompt is not None
        assert prompt['positive_prompt'] == sample_prompt_data['positive_prompt']
    
    def test_get_by_id(self, prompt_repository, sample_prompt_data):
        """Test retrieving prompt by ID."""
        prompt_id = prompt_repository.create(sample_prompt_data)
        
        prompt = prompt_repository.get_by_id(prompt_id)
        
        assert prompt is not None
        assert prompt['id'] == prompt_id
        assert prompt['positive_prompt'] == sample_prompt_data['positive_prompt']
        assert prompt['category'] == sample_prompt_data['category']
    
    def test_get_by_id_nonexistent(self, prompt_repository):
        """Test retrieving non-existent prompt returns None."""
        prompt = prompt_repository.get_by_id(99999)
        assert prompt is None
    
    def test_update_prompt(self, prompt_repository, sample_prompt_data):
        """Test updating an existing prompt."""
        prompt_id = prompt_repository.create(sample_prompt_data)
        
        update_data = {
            'category': 'updated_category',
            'rating': 5,
            'notes': 'Updated through repository'
        }
        
        success = prompt_repository.update(prompt_id, update_data)
        assert success is True
        
        # Verify update
        updated_prompt = prompt_repository.get_by_id(prompt_id)
        assert updated_prompt['category'] == 'updated_category'
        assert updated_prompt['rating'] == 5
        assert updated_prompt['notes'] == 'Updated through repository'
    
    def test_delete_prompt(self, prompt_repository, sample_prompt_data):
        """Test deleting a prompt."""
        prompt_id = prompt_repository.create(sample_prompt_data)
        
        # Verify exists
        assert prompt_repository.get_by_id(prompt_id) is not None
        
        # Delete
        success = prompt_repository.delete(prompt_id)
        assert success is True
        
        # Verify deleted
        assert prompt_repository.get_by_id(prompt_id) is None
    
    def test_find_by_text(self, prompt_repository, sample_prompt_data):
        """Test finding prompts by text search."""
        # Create test prompts
        prompts_data = [
            {**sample_prompt_data, 'positive_prompt': 'Beautiful mountain landscape'},
            {**sample_prompt_data, 'positive_prompt': 'Ocean waves at sunset'},
            {**sample_prompt_data, 'positive_prompt': 'Abstract geometric shapes'}
        ]
        
        for prompt_data in prompts_data:
            prompt_repository.create(prompt_data)
        
        # Search for mountain
        results = prompt_repository.find_by_text('mountain')
        assert len(results) >= 1
        assert any('mountain' in r['positive_prompt'].lower() for r in results)
        
        # Search for ocean
        results = prompt_repository.find_by_text('ocean')
        assert len(results) >= 1
        assert any('ocean' in r['positive_prompt'].lower() for r in results)
    
    def test_find_by_category(self, prompt_repository, sample_prompt_data):
        """Test finding prompts by category."""
        # Create prompts with different categories
        categories = ['landscapes', 'portraits', 'abstract']
        
        for category in categories:
            prompt_data = {**sample_prompt_data, 'category': category}
            prompt_repository.create(prompt_data)
        
        # Find by each category
        for category in categories:
            results = prompt_repository.find_by_category(category)
            assert len(results) >= 1
            assert all(r['category'] == category for r in results)
    
    def test_find_by_rating_range(self, prompt_repository, sample_prompt_data):
        """Test finding prompts by rating range."""
        # Create prompts with different ratings
        for rating in [1, 2, 3, 4, 5]:
            prompt_data = {**sample_prompt_data, 'rating': rating, 
                          'positive_prompt': f'Prompt with rating {rating}'}
            prompt_repository.create(prompt_data)
        
        # Find high-rated prompts
        results = prompt_repository.find_by_rating_range(min_rating=4)
        assert len(results) >= 2  # ratings 4 and 5
        assert all(r['rating'] >= 4 for r in results)
        
        # Find medium-rated prompts
        results = prompt_repository.find_by_rating_range(min_rating=2, max_rating=3)
        assert len(results) >= 2  # ratings 2 and 3
        assert all(2 <= r['rating'] <= 3 for r in results)
    
    def test_find_by_tags(self, prompt_repository, sample_prompt_data):
        """Test finding prompts by tags."""
        # Create prompts with different tag combinations
        prompts_with_tags = [
            {**sample_prompt_data, 'tags': ['nature', 'landscape', 'mountains']},
            {**sample_prompt_data, 'tags': ['portrait', 'character', 'anime']},
            {**sample_prompt_data, 'tags': ['abstract', 'geometric', 'colorful']}
        ]
        
        for prompt_data in prompts_with_tags:
            prompt_repository.create(prompt_data)
        
        # Search by single tag
        results = prompt_repository.find_by_tags(['nature'])
        assert len(results) >= 1
        
        # Verify results have the tag
        for result in results:
            if result['tags']:
                tags = json.loads(result['tags'])
                assert 'nature' in tags
    
    def test_get_all_categories(self, prompt_repository, sample_prompt_data):
        """Test getting all unique categories."""
        categories = ['landscapes', 'portraits', 'abstract', 'fantasy']
        
        for category in categories:
            prompt_data = {**sample_prompt_data, 'category': category}
            prompt_repository.create(prompt_data)
        
        all_categories = prompt_repository.get_all_categories()
        
        # Should contain all created categories
        for category in categories:
            assert category in all_categories
    
    def test_get_recent(self, prompt_repository, sample_prompt_data):
        """Test getting recent prompts."""
        # Create multiple prompts
        for i in range(5):
            prompt_data = {**sample_prompt_data, 
                          'positive_prompt': f'Recent prompt {i}'}
            prompt_repository.create(prompt_data)
        
        # Get recent prompts
        recent = prompt_repository.get_recent(limit=3)
        
        assert len(recent) <= 3
        # Should be ordered by creation time (newest first)
        if len(recent) > 1:
            for i in range(len(recent) - 1):
                current_time = datetime.fromisoformat(recent[i]['created_at'].replace('Z', '+00:00'))
                next_time = datetime.fromisoformat(recent[i + 1]['created_at'].replace('Z', '+00:00'))
                assert current_time >= next_time
    
    def test_count_total(self, prompt_repository, sample_prompt_data):
        """Test counting total prompts."""
        initial_count = prompt_repository.count_total()
        
        # Create some prompts
        for i in range(3):
            prompt_data = {**sample_prompt_data, 
                          'positive_prompt': f'Count test prompt {i}'}
            prompt_repository.create(prompt_data)
        
        final_count = prompt_repository.count_total()
        assert final_count == initial_count + 3
    
    def test_exists(self, prompt_repository, sample_prompt_data):
        """Test checking if prompt exists."""
        prompt_id = prompt_repository.create(sample_prompt_data)
        
        assert prompt_repository.exists(prompt_id) is True
        assert prompt_repository.exists(99999) is False
    
    def test_validate_data(self, prompt_repository):
        """Test data validation in repository."""
        # Test with missing required fields
        with pytest.raises(ValueError):
            prompt_repository.create({'category': 'test'})  # Missing positive_prompt
        
        # Test with invalid rating
        with pytest.raises(ValueError):
            prompt_repository.create({
                'positive_prompt': 'test',
                'rating': 10  # Invalid rating
            })
    
    def test_pagination(self, prompt_repository, sample_prompt_data):
        """Test paginated retrieval of prompts."""
        # Create multiple prompts
        for i in range(10):
            prompt_data = {**sample_prompt_data, 
                          'positive_prompt': f'Pagination test prompt {i}'}
            prompt_repository.create(prompt_data)
        
        # Test pagination
        page1 = prompt_repository.get_paginated(page=1, per_page=3)
        assert len(page1['items']) == 3
        assert page1['total'] >= 10
        assert page1['page'] == 1
        assert page1['per_page'] == 3
        
        page2 = prompt_repository.get_paginated(page=2, per_page=3)
        assert len(page2['items']) == 3
        assert page2['page'] == 2
        
        # Items should be different between pages
        page1_ids = {item['id'] for item in page1['items']}
        page2_ids = {item['id'] for item in page2['items']}
        assert page1_ids.isdisjoint(page2_ids)


class TestGeneratedImageRepository:
    """Test cases for the GeneratedImageRepository class."""
    
    def test_create_image_record(self, image_repository, prompt_repository, sample_prompt_data):
        """Test creating an image record."""
        # First create a prompt
        prompt_id = prompt_repository.create(sample_prompt_data)
        
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
        
        image_id = image_repository.create(image_data)
        assert image_id is not None
        
        # Verify image record was created
        image = image_repository.get_by_id(image_id)
        assert image is not None
        assert image['prompt_id'] == prompt_id
        assert image['filename'] == 'image.png'
        assert image['width'] == 512
        assert image['height'] == 512
    
    def test_get_by_prompt_id(self, image_repository, prompt_repository, sample_prompt_data):
        """Test retrieving images by prompt ID."""
        prompt_id = prompt_repository.create(sample_prompt_data)
        
        # Create multiple images for the same prompt
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
            image_repository.create(image_data)
        
        images = image_repository.get_by_prompt_id(prompt_id)
        assert len(images) == 3
        assert all(img['prompt_id'] == prompt_id for img in images)
    
    def test_get_by_path(self, image_repository, prompt_repository, sample_prompt_data):
        """Test retrieving image by file path."""
        prompt_id = prompt_repository.create(sample_prompt_data)
        
        image_path = '/unique/test/path/image.png'
        image_data = {
            'prompt_id': prompt_id,
            'image_path': image_path,
            'filename': 'image.png',
            'file_size': 1024000,
            'width': 512,
            'height': 512,
            'format': 'PNG'
        }
        
        image_id = image_repository.create(image_data)
        
        # Find by path
        image = image_repository.get_by_path(image_path)
        assert image is not None
        assert image['id'] == image_id
        assert image['image_path'] == image_path
    
    def test_update_image_metadata(self, image_repository, prompt_repository, sample_prompt_data):
        """Test updating image metadata."""
        prompt_id = prompt_repository.create(sample_prompt_data)
        
        image_data = {
            'prompt_id': prompt_id,
            'image_path': '/test/path/image.png',
            'filename': 'image.png',
            'file_size': 1024000,
            'width': 512,
            'height': 512,
            'format': 'PNG'
        }
        
        image_id = image_repository.create(image_data)
        
        # Update metadata
        update_data = {
            'workflow_data': '{"updated": "workflow"}',
            'parameters': '{"updated": "parameters"}',
            'file_size': 2048000
        }
        
        success = image_repository.update(image_id, update_data)
        assert success is True
        
        # Verify update
        image = image_repository.get_by_id(image_id)
        assert image['workflow_data'] == '{"updated": "workflow"}'
        assert image['parameters'] == '{"updated": "parameters"}'
        assert image['file_size'] == 2048000
    
    def test_delete_image(self, image_repository, prompt_repository, sample_prompt_data):
        """Test deleting an image record."""
        prompt_id = prompt_repository.create(sample_prompt_data)
        
        image_data = {
            'prompt_id': prompt_id,
            'image_path': '/test/path/image.png',
            'filename': 'image.png',
            'file_size': 1024000,
            'width': 512,
            'height': 512,
            'format': 'PNG'
        }
        
        image_id = image_repository.create(image_data)
        
        # Verify exists
        assert image_repository.get_by_id(image_id) is not None
        
        # Delete
        success = image_repository.delete(image_id)
        assert success is True
        
        # Verify deleted
        assert image_repository.get_by_id(image_id) is None
    
    def test_find_by_format(self, image_repository, prompt_repository, sample_prompt_data):
        """Test finding images by format."""
        prompt_id = prompt_repository.create(sample_prompt_data)
        
        # Create images with different formats
        formats = ['PNG', 'JPG', 'WEBP']
        
        for i, fmt in enumerate(formats):
            image_data = {
                'prompt_id': prompt_id,
                'image_path': f'/test/path/image_{i}.{fmt.lower()}',
                'filename': f'image_{i}.{fmt.lower()}',
                'format': fmt,
                'width': 512,
                'height': 512
            }
            image_repository.create(image_data)
        
        # Find PNG images
        png_images = image_repository.find_by_format('PNG')
        assert len(png_images) >= 1
        assert all(img['format'] == 'PNG' for img in png_images)
    
    def test_find_by_size_range(self, image_repository, prompt_repository, sample_prompt_data):
        """Test finding images by resolution range."""
        prompt_id = prompt_repository.create(sample_prompt_data)
        
        # Create images with different sizes
        sizes = [(256, 256), (512, 512), (1024, 1024)]
        
        for i, (width, height) in enumerate(sizes):
            image_data = {
                'prompt_id': prompt_id,
                'image_path': f'/test/path/image_{i}.png',
                'filename': f'image_{i}.png',
                'width': width,
                'height': height,
                'format': 'PNG'
            }
            image_repository.create(image_data)
        
        # Find medium to large images
        large_images = image_repository.find_by_size_range(min_width=512)
        assert len(large_images) >= 2  # 512x512 and 1024x1024
        assert all(img['width'] >= 512 for img in large_images)
    
    def test_get_recent_images(self, image_repository, prompt_repository, sample_prompt_data):
        """Test getting recent images."""
        prompt_id = prompt_repository.create(sample_prompt_data)
        
        # Create multiple images
        for i in range(5):
            image_data = {
                'prompt_id': prompt_id,
                'image_path': f'/test/path/recent_{i}.png',
                'filename': f'recent_{i}.png',
                'width': 512,
                'height': 512,
                'format': 'PNG'
            }
            image_repository.create(image_data)
        
        recent = image_repository.get_recent(limit=3)
        assert len(recent) <= 3
        
        # Should be ordered by generation time (newest first)
        if len(recent) > 1:
            for i in range(len(recent) - 1):
                current_time = datetime.fromisoformat(recent[i]['generation_time'].replace('Z', '+00:00'))
                next_time = datetime.fromisoformat(recent[i + 1]['generation_time'].replace('Z', '+00:00'))
                assert current_time >= next_time
    
    def test_count_by_prompt(self, image_repository, prompt_repository, sample_prompt_data):
        """Test counting images for a specific prompt."""
        prompt_id = prompt_repository.create(sample_prompt_data)
        
        initial_count = image_repository.count_by_prompt(prompt_id)
        
        # Create images
        for i in range(3):
            image_data = {
                'prompt_id': prompt_id,
                'image_path': f'/test/path/count_{i}.png',
                'filename': f'count_{i}.png',
                'width': 512,
                'height': 512,
                'format': 'PNG'
            }
            image_repository.create(image_data)
        
        final_count = image_repository.count_by_prompt(prompt_id)
        assert final_count == initial_count + 3
    
    def test_foreign_key_constraint(self, image_repository):
        """Test that foreign key constraints are enforced."""
        # Try to create image with non-existent prompt_id
        image_data = {
            'prompt_id': 99999,  # Non-existent prompt
            'image_path': '/test/path/orphan.png',
            'filename': 'orphan.png',
            'width': 512,
            'height': 512,
            'format': 'PNG'
        }
        
        with pytest.raises(Exception):  # Should raise foreign key constraint error
            image_repository.create(image_data)
    
    def test_cascade_delete(self, image_repository, prompt_repository, sample_prompt_data):
        """Test that images are deleted when parent prompt is deleted."""
        prompt_id = prompt_repository.create(sample_prompt_data)
        
        # Create images for the prompt
        image_ids = []
        for i in range(3):
            image_data = {
                'prompt_id': prompt_id,
                'image_path': f'/test/path/cascade_{i}.png',
                'filename': f'cascade_{i}.png',
                'width': 512,
                'height': 512,
                'format': 'PNG'
            }
            image_id = image_repository.create(image_data)
            image_ids.append(image_id)
        
        # Verify images exist
        for image_id in image_ids:
            assert image_repository.get_by_id(image_id) is not None
        
        # Delete parent prompt
        prompt_repository.delete(prompt_id)
        
        # Images should be deleted too (cascade)
        for image_id in image_ids:
            assert image_repository.get_by_id(image_id) is None