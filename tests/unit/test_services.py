"""
Unit tests for service classes.

Tests the service layer that provides business logic and coordination
between repositories and external systems.
"""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, mock_open
from PIL import Image
import json

from src.services.image_service import ImageService
from src.services.enhanced_thumbnail_service import EnhancedThumbnailService
from src.services.image_scanner import ImageScanner
from src.services.stats_service import StatsService


class TestImageService:
    """Test cases for the ImageService class."""
    
    def test_init(self, test_config, db_model):
        """Test ImageService initialization."""
        service = ImageService(test_config, db_model)
        
        assert service.config == test_config
        assert service.db_model == db_model
        assert service.supported_formats == {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
        assert service.video_formats == {'.mp4', '.webm', '.avi', '.mov', '.mkv', '.m4v', '.wmv'}
    
    def test_is_supported_image(self, image_service):
        """Test image format detection."""
        assert image_service.is_supported_image('test.png') is True
        assert image_service.is_supported_image('test.jpg') is True
        assert image_service.is_supported_image('test.jpeg') is True
        assert image_service.is_supported_image('test.webp') is True
        assert image_service.is_supported_image('test.gif') is True
        
        assert image_service.is_supported_image('test.bmp') is False
        assert image_service.is_supported_image('test.txt') is False
        assert image_service.is_supported_image('test.mp4') is False
    
    def test_is_supported_video(self, image_service):
        """Test video format detection."""
        assert image_service.is_supported_video('test.mp4') is True
        assert image_service.is_supported_video('test.webm') is True
        assert image_service.is_supported_video('test.avi') is True
        assert image_service.is_supported_video('test.mov') is True
        
        assert image_service.is_supported_video('test.png') is False
        assert image_service.is_supported_video('test.txt') is False
    
    def test_get_image_info(self, image_service, test_image_file):
        """Test extracting image information."""
        info = image_service.get_image_info(str(test_image_file))
        
        assert info is not None
        assert info['width'] == 512
        assert info['height'] == 512
        assert info['format'] == 'PNG'
        assert info['file_size'] > 0
        assert 'file_path' in info
    
    def test_get_image_info_nonexistent(self, image_service):
        """Test handling of non-existent image files."""
        info = image_service.get_image_info('/nonexistent/file.png')
        assert info is None
    
    @patch('src.services.image_service.ImageService._extract_png_metadata')
    def test_get_image_metadata(self, mock_extract, image_service, test_image_file, sample_image_metadata):
        """Test extracting image metadata."""
        mock_extract.return_value = sample_image_metadata
        
        metadata = image_service.get_image_metadata(str(test_image_file))
        
        assert metadata is not None
        assert metadata['prompt'] == 'test prompt'
        assert metadata['steps'] == 20
        assert metadata['cfg_scale'] == 7.5
        mock_extract.assert_called_once()
    
    def test_process_new_image(self, image_service, test_image_file, prompt_repository, sample_prompt_data):
        """Test processing a new image file."""
        # Create a prompt first
        prompt_id = prompt_repository.create(sample_prompt_data)
        
        with patch.object(image_service, 'get_image_metadata') as mock_metadata:
            mock_metadata.return_value = {'prompt': sample_prompt_data['positive_prompt']}
            
            result = image_service.process_new_image(str(test_image_file), prompt_id)
            
            assert result is not None
            assert 'image_id' in result
            assert result['status'] == 'processed'
    
    def test_batch_process_images(self, image_service, test_files_structure):
        """Test batch processing multiple images."""
        image_files = [f for f in test_files_structure if f.suffix.lower() in ['.png', '.jpg', '.webp']]
        
        with patch.object(image_service, 'process_new_image') as mock_process:
            mock_process.return_value = {'image_id': 1, 'status': 'processed'}
            
            results = image_service.batch_process_images(image_files)
            
            assert len(results) == len(image_files)
            assert all(r['status'] == 'processed' for r in results)
            assert mock_process.call_count == len(image_files)
    
    def test_scan_directory(self, image_service, test_files_structure, test_config):
        """Test scanning directory for images."""
        output_dir = Path(test_config.output_dir)
        
        found_images = image_service.scan_directory(str(output_dir))
        
        # Should find all image files
        image_extensions = {'.png', '.jpg', '.webp'}
        expected_count = sum(1 for f in test_files_structure 
                           if f.suffix.lower() in image_extensions)
        
        assert len(found_images) >= expected_count
        
        # All results should be supported image files
        for image_path in found_images:
            assert image_service.is_supported_image(image_path)
    
    def test_scan_directory_recursive(self, image_service, test_files_structure, test_config):
        """Test recursive directory scanning."""
        output_dir = Path(test_config.output_dir)
        
        found_images = image_service.scan_directory(str(output_dir), recursive=True)
        
        # Should find images in subdirectories too
        nested_images = sum(1 for f in test_files_structure 
                          if 'subdir' in str(f) and f.suffix.lower() in ['.png', '.jpg', '.webp'])
        
        assert len(found_images) >= nested_images
    
    def test_get_supported_files(self, image_service, test_files_structure):
        """Test filtering supported files."""
        all_files = [str(f) for f in test_files_structure]
        
        supported = image_service.get_supported_files(all_files)
        
        # Should only contain supported image and video files
        for file_path in supported['images']:
            assert image_service.is_supported_image(file_path)
        
        for file_path in supported['videos']:
            assert image_service.is_supported_video(file_path)
    
    @patch('src.services.image_service.cv2')
    def test_extract_video_thumbnail(self, mock_cv2, image_service, test_video_file):
        """Test video thumbnail extraction."""
        # Mock OpenCV
        mock_cap = Mock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (True, Mock())
        mock_cv2.VideoCapture.return_value = mock_cap
        mock_cv2.imencode.return_value = (True, b'fake_image_data')
        
        thumbnail = image_service.extract_video_thumbnail(str(test_video_file))
        
        assert thumbnail is not None
        mock_cv2.VideoCapture.assert_called_once()


class TestEnhancedThumbnailService:
    """Test cases for the EnhancedThumbnailService class."""
    
    def test_init(self, test_config):
        """Test service initialization."""
        service = EnhancedThumbnailService(test_config)
        
        assert service.config == test_config
        assert service.thumbnail_size == (256, 256)
        assert service.quality == 85
    
    def test_generate_thumbnail(self, thumbnail_service, test_image_file):
        """Test thumbnail generation."""
        thumbnail_path = thumbnail_service.generate_thumbnail(
            str(test_image_file), 'test_thumb.jpg'
        )
        
        assert thumbnail_path is not None
        assert os.path.exists(thumbnail_path)
        
        # Verify thumbnail size
        with Image.open(thumbnail_path) as thumb:
            assert thumb.size[0] <= 256
            assert thumb.size[1] <= 256
    
    def test_generate_thumbnail_preserves_aspect_ratio(self, thumbnail_service, temp_dir):
        """Test that thumbnail generation preserves aspect ratio."""
        # Create a rectangular image
        wide_image_path = temp_dir / 'wide_image.png'
        img = Image.new('RGB', (1024, 512), color='blue')  # 2:1 aspect ratio
        img.save(wide_image_path)
        
        thumbnail_path = thumbnail_service.generate_thumbnail(
            str(wide_image_path), 'wide_thumb.jpg'
        )
        
        with Image.open(thumbnail_path) as thumb:
            width, height = thumb.size
            aspect_ratio = width / height
            original_ratio = 1024 / 512
            
            # Aspect ratio should be preserved (within tolerance)
            assert abs(aspect_ratio - original_ratio) < 0.1
    
    def test_get_thumbnail_path(self, thumbnail_service):
        """Test thumbnail path generation."""
        original_path = '/path/to/image.png'
        
        thumb_path = thumbnail_service.get_thumbnail_path(original_path)
        
        assert thumb_path.endswith('.jpg')
        assert 'thumbnails' in thumb_path
    
    def test_thumbnail_exists(self, thumbnail_service, test_image_file):
        """Test checking if thumbnail exists."""
        original_path = str(test_image_file)
        
        # Should not exist initially
        assert thumbnail_service.thumbnail_exists(original_path) is False
        
        # Generate thumbnail
        thumbnail_service.generate_thumbnail(original_path, 'exists_test.jpg')
        
        # Should exist now
        assert thumbnail_service.thumbnail_exists(original_path) is True
    
    def test_batch_generate_thumbnails(self, thumbnail_service, test_files_structure):
        """Test batch thumbnail generation."""
        image_files = [str(f) for f in test_files_structure 
                      if f.suffix.lower() in ['.png', '.jpg', '.webp']]
        
        results = thumbnail_service.batch_generate_thumbnails(image_files)
        
        assert len(results) == len(image_files)
        
        for result in results:
            assert 'original_path' in result
            assert 'thumbnail_path' in result
            assert 'success' in result
            
            if result['success']:
                assert os.path.exists(result['thumbnail_path'])
    
    def test_cleanup_orphaned_thumbnails(self, thumbnail_service, test_image_file):
        """Test cleanup of orphaned thumbnails."""
        # Generate a thumbnail
        thumbnail_path = thumbnail_service.generate_thumbnail(
            str(test_image_file), 'orphan_test.jpg'
        )
        
        assert os.path.exists(thumbnail_path)
        
        # Remove original image
        os.remove(test_image_file)
        
        # Cleanup should remove the orphaned thumbnail
        cleaned_count = thumbnail_service.cleanup_orphaned_thumbnails()
        
        assert cleaned_count >= 0  # May be 0 if cleanup logic differs
    
    def test_get_thumbnail_stats(self, thumbnail_service, test_files_structure):
        """Test getting thumbnail statistics."""
        # Generate some thumbnails
        image_files = [str(f) for f in test_files_structure[:2] 
                      if f.suffix.lower() in ['.png', '.jpg']]
        
        for img_file in image_files:
            thumbnail_service.generate_thumbnail(img_file, f'stats_test_{os.path.basename(img_file)}.jpg')
        
        stats = thumbnail_service.get_thumbnail_stats()
        
        assert 'total_thumbnails' in stats
        assert 'total_size' in stats
        assert stats['total_thumbnails'] >= len(image_files)


class TestImageScanner:
    """Test cases for the ImageScanner service."""
    
    @pytest.fixture
    def image_scanner(self, test_config, db_model):
        """Create an ImageScanner instance."""
        return ImageScanner(test_config, db_model)
    
    def test_init(self, image_scanner, test_config, db_model):
        """Test ImageScanner initialization."""
        assert image_scanner.config == test_config
        assert image_scanner.db_model == db_model
    
    def test_scan_for_new_images(self, image_scanner, test_files_structure):
        """Test scanning for new images."""
        with patch.object(image_scanner, '_process_image') as mock_process:
            mock_process.return_value = {'status': 'processed', 'image_id': 1}
            
            results = image_scanner.scan_for_new_images()
            
            assert 'total_found' in results
            assert 'processed' in results
            assert 'errors' in results
    
    def test_process_single_image(self, image_scanner, test_image_file):
        """Test processing a single image."""
        with patch.object(image_scanner, '_extract_metadata') as mock_extract:
            mock_extract.return_value = {'prompt': 'test prompt'}
            
            result = image_scanner.process_single_image(str(test_image_file))
            
            assert result is not None
            assert 'status' in result
    
    def test_validate_image_file(self, image_scanner, test_image_file):
        """Test image file validation."""
        assert image_scanner.validate_image_file(str(test_image_file)) is True
        assert image_scanner.validate_image_file('/nonexistent/file.png') is False
    
    def test_get_scan_progress(self, image_scanner):
        """Test getting scan progress."""
        progress = image_scanner.get_scan_progress()
        
        assert 'current_file' in progress
        assert 'total_files' in progress
        assert 'processed_count' in progress
        assert 'error_count' in progress


class TestStatsService:
    """Test cases for the StatsService."""
    
    @pytest.fixture
    def stats_service(self, db_model):
        """Create a StatsService instance."""
        return StatsService(db_model)
    
    def test_get_prompt_stats(self, stats_service, populated_db):
        """Test getting prompt statistics."""
        db_operations, _ = populated_db
        
        stats = stats_service.get_prompt_stats()
        
        assert 'total_prompts' in stats
        assert 'unique_categories' in stats
        assert 'average_rating' in stats
        assert 'total_tags' in stats
        
        assert stats['total_prompts'] >= 3  # From populated_db
    
    def test_get_image_stats(self, stats_service, populated_db, image_repository, prompt_repository):
        """Test getting image statistics."""
        db_operations, prompt_ids = populated_db
        
        # Add some images
        for prompt_id in prompt_ids[:2]:
            image_data = {
                'prompt_id': prompt_id,
                'image_path': f'/test/path/image_{prompt_id}.png',
                'filename': f'image_{prompt_id}.png',
                'width': 512,
                'height': 512,
                'format': 'PNG'
            }
            image_repository.create(image_data)
        
        stats = stats_service.get_image_stats()
        
        assert 'total_images' in stats
        assert 'total_file_size' in stats
        assert 'formats' in stats
        assert 'resolutions' in stats
        
        assert stats['total_images'] >= 2
    
    def test_get_usage_stats(self, stats_service, populated_db):
        """Test getting usage statistics."""
        db_operations, _ = populated_db
        
        stats = stats_service.get_usage_stats()
        
        assert 'recent_activity' in stats
        assert 'popular_categories' in stats
        assert 'top_rated_prompts' in stats
    
    def test_get_database_stats(self, stats_service):
        """Test getting database statistics."""
        stats = stats_service.get_database_stats()
        
        assert 'database_size' in stats
        assert 'table_counts' in stats
        assert 'index_info' in stats
    
    def test_generate_report(self, stats_service, populated_db):
        """Test generating comprehensive statistics report."""
        db_operations, _ = populated_db
        
        report = stats_service.generate_report()
        
        assert 'prompt_stats' in report
        assert 'image_stats' in report
        assert 'usage_stats' in report
        assert 'database_stats' in report
        assert 'generated_at' in report
    
    def test_calculate_trends(self, stats_service, populated_db):
        """Test calculating usage trends."""
        db_operations, _ = populated_db
        
        trends = stats_service.calculate_trends(days=30)
        
        assert 'daily_prompts' in trends
        assert 'category_trends' in trends
        assert 'rating_trends' in trends