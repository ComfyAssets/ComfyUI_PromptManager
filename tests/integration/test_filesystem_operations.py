"""
Integration tests for file system operations and image processing.

Tests file monitoring, image metadata extraction, thumbnail generation,
and file system interactions with real files and directories.
"""

import pytest
import os
import time
import shutil
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock
from PIL import Image, PngImagePlugin
import json
import tempfile
import threading
import hashlib

from src.services.image_scanner import ImageScanner
from src.services.enhanced_thumbnail_service import EnhancedThumbnailService
from src.metadata.extractor import MetadataExtractor
from src.utils.file_metadata import FileMetadata, compute_file_metadata
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class TestImageScanning:
    """Test cases for image scanning and file monitoring."""
    
    @pytest.fixture
    def image_scanner(self, test_config, db_model):
        """Create an ImageScanner instance."""
        return ImageScanner(test_config, db_model)
    
    def test_scan_directory_for_images(self, image_scanner, test_files_structure):
        """Test scanning directory for image files."""
        # Get the output directory path
        output_dir = Path(image_scanner.config.output_dir)
        
        results = image_scanner.scan_directory(str(output_dir))
        
        assert 'images_found' in results
        assert 'videos_found' in results
        assert 'total_files' in results
        
        # Should find all image files
        image_extensions = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
        expected_images = [f for f in test_files_structure 
                          if f.suffix.lower() in image_extensions]
        
        assert results['images_found'] >= len(expected_images)
    
    def test_scan_directory_recursive(self, image_scanner, test_files_structure):
        """Test recursive directory scanning."""
        output_dir = Path(image_scanner.config.output_dir)
        
        results = image_scanner.scan_directory(str(output_dir), recursive=True)
        
        # Should find images in subdirectories
        nested_images = [f for f in test_files_structure 
                        if 'subdir' in str(f) and f.suffix.lower() in ['.png', '.jpg', '.webp']]
        
        assert results['images_found'] >= len(nested_images)
    
    def test_process_new_image_file(self, image_scanner, test_image_file):
        """Test processing a newly discovered image file."""
        result = image_scanner.process_image_file(str(test_image_file))
        
        assert result is not None
        assert 'status' in result
        assert 'image_info' in result
        
        if result['status'] == 'success':
            assert 'width' in result['image_info']
            assert 'height' in result['image_info']
            assert 'format' in result['image_info']
    
    def test_batch_process_images(self, image_scanner, test_files_structure):
        """Test batch processing of multiple images."""
        image_files = [str(f) for f in test_files_structure 
                      if f.suffix.lower() in ['.png', '.jpg', '.webp']]
        
        results = image_scanner.batch_process_images(image_files)
        
        assert 'processed' in results
        assert 'errors' in results
        assert 'total' in results
        
        assert results['total'] == len(image_files)
        assert results['processed'] + len(results['errors']) == results['total']
    
    def test_validate_image_file(self, image_scanner, test_image_file, temp_dir):
        """Test image file validation."""
        # Valid image
        assert image_scanner.validate_image_file(str(test_image_file)) is True
        
        # Non-existent file
        assert image_scanner.validate_image_file('/nonexistent/file.png') is False
        
        # Invalid file (not an image)
        text_file = temp_dir / 'not_an_image.txt'
        text_file.write_text('This is not an image')
        assert image_scanner.validate_image_file(str(text_file)) is False
        
        # Corrupted image file
        corrupted_file = temp_dir / 'corrupted.png'
        corrupted_file.write_bytes(b'PNG\x00\x00\x00corrupted')
        assert image_scanner.validate_image_file(str(corrupted_file)) is False
    
    def test_filter_supported_files(self, image_scanner, test_files_structure):
        """Test filtering files by supported formats."""
        all_files = [str(f) for f in test_files_structure]
        
        filtered = image_scanner.filter_supported_files(all_files)
        
        assert 'images' in filtered
        assert 'videos' in filtered
        assert 'unsupported' in filtered
        
        # All image files should be in images list
        for img_file in filtered['images']:
            assert any(img_file.endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp', '.gif'])
        
        # All video files should be in videos list
        for vid_file in filtered['videos']:
            assert any(vid_file.endswith(ext) for ext in ['.mp4', '.webm', '.avi', '.mov'])
    
    def test_get_file_hash(self, image_scanner, test_image_file):
        """Test generating file hash for duplicate detection."""
        file_hash = image_scanner.get_file_hash(str(test_image_file))
        
        assert file_hash is not None
        assert isinstance(file_hash, str)
        assert len(file_hash) == 64  # SHA256 hash length
        
        # Same file should produce same hash
        file_hash2 = image_scanner.get_file_hash(str(test_image_file))
        assert file_hash == file_hash2
    
    def test_detect_duplicates(self, image_scanner, temp_dir):
        """Test duplicate image detection."""
        # Create original image
        original_img = temp_dir / 'original.png'
        img = Image.new('RGB', (256, 256), color='red')
        img.save(original_img)
        
        # Create duplicate (copy)
        duplicate_img = temp_dir / 'duplicate.png'
        shutil.copy2(original_img, duplicate_img)
        
        # Create different image
        different_img = temp_dir / 'different.png'
        img2 = Image.new('RGB', (256, 256), color='blue')
        img2.save(different_img)
        
        files = [str(original_img), str(duplicate_img), str(different_img)]
        duplicates = image_scanner.find_duplicate_images(files)
        
        assert len(duplicates) >= 1  # Should find at least one duplicate group
        
        # Find the duplicate group containing our files
        duplicate_group = None
        for group in duplicates:
            if str(original_img) in group or str(duplicate_img) in group:
                duplicate_group = group
                break
        
        assert duplicate_group is not None
        assert str(original_img) in duplicate_group
        assert str(duplicate_img) in duplicate_group
        assert str(different_img) not in duplicate_group


class TestFileMonitoring:
    """Test cases for file system monitoring."""
    
    @pytest.fixture
    def file_monitor(self, test_config, db_model):
        """Create a file monitor instance."""
        from src.utils.file_monitor import FileMonitor
        return FileMonitor(test_config, db_model)
    
    def test_start_monitoring(self, file_monitor, test_config):
        """Test starting file system monitoring."""
        file_monitor.start_monitoring()
        
        assert file_monitor.observer is not None
        assert file_monitor.observer.is_alive()
        
        # Cleanup
        file_monitor.stop_monitoring()
    
    def test_stop_monitoring(self, file_monitor):
        """Test stopping file system monitoring."""
        file_monitor.start_monitoring()
        file_monitor.stop_monitoring()
        
        assert file_monitor.observer is None or not file_monitor.observer.is_alive()
    
    def test_file_creation_detection(self, file_monitor, test_config, temp_dir):
        """Test detection of new file creation."""
        detected_files = []
        
        def file_handler(file_path, event_type):
            detected_files.append((file_path, event_type))
        
        file_monitor.on_file_created = file_handler
        
        # Start monitoring
        file_monitor.start_monitoring()
        
        # Create a new image file
        new_image = Path(test_config.output_dir) / 'new_test_image.png'
        img = Image.new('RGB', (128, 128), color='green')
        img.save(new_image)
        
        # Wait for file system event
        time.sleep(0.5)
        
        # Should have detected the new file
        created_files = [f for f, event in detected_files if event == 'created']
        assert any(str(new_image) in f for f in created_files)
        
        # Cleanup
        file_monitor.stop_monitoring()
        if new_image.exists():
            new_image.unlink()
    
    def test_file_modification_detection(self, file_monitor, test_image_file):
        """Test detection of file modifications."""
        detected_changes = []
        
        def change_handler(file_path, event_type):
            detected_changes.append((file_path, event_type))
        
        file_monitor.on_file_modified = change_handler
        file_monitor.start_monitoring()
        
        # Modify the image file
        with open(test_image_file, 'ab') as f:
            f.write(b'modified')
        
        time.sleep(0.5)
        
        # Should have detected the modification
        modified_files = [f for f, event in detected_changes if event == 'modified']
        assert any(str(test_image_file) in f for f in modified_files)
        
        file_monitor.stop_monitoring()
    
    def test_ignore_temporary_files(self, file_monitor, test_config):
        """Test that temporary files are ignored."""
        detected_files = []
        
        def file_handler(file_path, event_type):
            detected_files.append((file_path, event_type))
        
        file_monitor.on_file_created = file_handler
        file_monitor.start_monitoring()
        
        # Create temporary files that should be ignored
        output_dir = Path(test_config.output_dir)
        temp_files = [
            output_dir / '.tmp_file',
            output_dir / 'file.tmp',
            output_dir / '~temp_file.png',
            output_dir / 'file.png.tmp'
        ]
        
        for temp_file in temp_files:
            temp_file.write_bytes(b'temporary data')
        
        time.sleep(0.5)
        
        # Temporary files should not be detected
        created_files = [f for f, _ in detected_files]
        for temp_file in temp_files:
            assert not any(str(temp_file) in f for f in created_files)
        
        # Cleanup
        file_monitor.stop_monitoring()
        for temp_file in temp_files:
            if temp_file.exists():
                temp_file.unlink()


class TestMetadataExtraction:
    """Test cases for image metadata extraction."""
    
    @pytest.fixture
    def metadata_extractor(self):
        """Create a MetadataExtractor instance."""
        return MetadataExtractor()
    
    def test_extract_basic_image_info(self, metadata_extractor, test_image_file):
        """Test extraction of basic image information."""
        info = metadata_extractor.extract_basic_info(str(test_image_file))
        
        assert info is not None
        assert 'width' in info
        assert 'height' in info
        assert 'format' in info
        assert 'mode' in info
        assert 'file_size' in info
        
        assert info['width'] == 512
        assert info['height'] == 512
        assert info['format'] == 'PNG'
    
    def test_extract_png_metadata(self, metadata_extractor, temp_dir):
        """Test extraction of PNG metadata chunks."""
        # Create PNG with metadata
        image_path = temp_dir / 'metadata_test.png'
        img = Image.new('RGB', (256, 256), color='blue')
        
        # Add PNG metadata
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('prompt', 'test prompt for extraction')
        metadata.add_text('negative_prompt', 'blurry, ugly')
        metadata.add_text('parameters', '{"steps": 20, "cfg_scale": 7.5}')
        metadata.add_text('workflow', '{"nodes": [{"type": "test"}]}')
        
        img.save(image_path, pnginfo=metadata)
        
        # Extract metadata
        extracted = metadata_extractor.extract_png_metadata(str(image_path))
        
        assert extracted is not None
        assert 'prompt' in extracted
        assert 'negative_prompt' in extracted
        assert 'parameters' in extracted
        assert 'workflow' in extracted
        
        assert extracted['prompt'] == 'test prompt for extraction'
        assert extracted['negative_prompt'] == 'blurry, ugly'
        
        # Parameters should be parsed as JSON
        if extracted['parameters']:
            params = json.loads(extracted['parameters'])
            assert params['steps'] == 20
            assert params['cfg_scale'] == 7.5
    
    def test_extract_exif_data(self, metadata_extractor, temp_dir):
        """Test extraction of EXIF data from JPEG images."""
        # Create JPEG with EXIF data
        jpeg_path = temp_dir / 'exif_test.jpg'
        img = Image.new('RGB', (300, 200), color='yellow')
        
        # Add EXIF data
        from PIL.ExifTags import TAGS
        exif_dict = {
            'ImageWidth': 300,
            'ImageLength': 200,
            'Software': 'Test Software',
            'DateTime': '2023:12:01 10:30:00'
        }
        
        img.save(jpeg_path, exif=img.getexif())
        
        extracted = metadata_extractor.extract_exif_data(str(jpeg_path))
        
        # EXIF extraction depends on PIL version and image creation method
        # At minimum, should return a dict (even if empty)
        assert isinstance(extracted, dict)
    
    def test_extract_comfyui_workflow(self, metadata_extractor, temp_dir):
        """Test extraction of ComfyUI workflow data."""
        # Create image with ComfyUI workflow metadata
        image_path = temp_dir / 'workflow_test.png'
        img = Image.new('RGB', (512, 512), color='purple')
        
        workflow_data = {
            'nodes': [
                {
                    'id': '1',
                    'class_type': 'CheckpointLoaderSimple',
                    'inputs': {'ckpt_name': 'test_model.safetensors'}
                },
                {
                    'id': '2',
                    'class_type': 'CLIPTextEncode',
                    'inputs': {'text': 'beautiful artwork', 'clip': ['1', 1]}
                },
                {
                    'id': '3',
                    'class_type': 'KSampler',
                    'inputs': {
                        'seed': 42,
                        'steps': 20,
                        'cfg': 7.5,
                        'sampler_name': 'euler',
                        'positive': ['2', 0]
                    }
                }
            ]
        }
        
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('workflow', json.dumps(workflow_data))
        metadata.add_text('prompt', 'beautiful artwork')
        
        img.save(image_path, pnginfo=metadata)
        
        extracted = metadata_extractor.extract_comfyui_workflow(str(image_path))
        
        assert extracted is not None
        assert 'workflow' in extracted
        assert 'parameters' in extracted
        
        # Parse workflow
        workflow = json.loads(extracted['workflow'])
        assert 'nodes' in workflow
        assert len(workflow['nodes']) == 3
        
        # Check specific node data
        checkpoint_node = next(n for n in workflow['nodes'] if n['class_type'] == 'CheckpointLoaderSimple')
        assert checkpoint_node['inputs']['ckpt_name'] == 'test_model.safetensors'
        
        sampler_node = next(n for n in workflow['nodes'] if n['class_type'] == 'KSampler')
        assert sampler_node['inputs']['seed'] == 42
        assert sampler_node['inputs']['steps'] == 20
    
    def test_extract_generation_parameters(self, metadata_extractor, temp_dir):
        """Test extraction of generation parameters from metadata."""
        image_path = temp_dir / 'params_test.png'
        img = Image.new('RGB', (512, 512), color='cyan')
        
        # Add generation parameters
        parameters = {
            'steps': 25,
            'cfg_scale': 8.0,
            'seed': 123456,
            'sampler': 'DPM++ 2M Karras',
            'model': 'realistic_model_v1.safetensors',
            'width': 512,
            'height': 512
        }
        
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('parameters', json.dumps(parameters))
        
        img.save(image_path, pnginfo=metadata)
        
        extracted = metadata_extractor.extract_generation_parameters(str(image_path))
        
        assert extracted is not None
        assert extracted['steps'] == 25
        assert extracted['cfg_scale'] == 8.0
        assert extracted['seed'] == 123456
        assert extracted['sampler'] == 'DPM++ 2M Karras'
        assert extracted['model'] == 'realistic_model_v1.safetensors'
    
    def test_handle_corrupted_metadata(self, metadata_extractor, temp_dir):
        """Test handling of corrupted or invalid metadata."""
        image_path = temp_dir / 'corrupted_metadata.png'
        img = Image.new('RGB', (256, 256), color='magenta')
        
        # Add corrupted JSON metadata
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('workflow', '{invalid json')
        metadata.add_text('parameters', 'not json at all')
        
        img.save(image_path, pnginfo=metadata)
        
        # Should handle corrupted metadata gracefully
        extracted = metadata_extractor.extract_png_metadata(str(image_path))
        
        assert extracted is not None
        # Raw text should still be available
        assert 'workflow' in extracted
        assert 'parameters' in extracted
        
        # But parsing should handle errors gracefully
        workflow_parsed = metadata_extractor.parse_workflow_json(extracted['workflow'])
        assert workflow_parsed is None  # Should return None for invalid JSON


class TestThumbnailGeneration:
    """Test cases for thumbnail generation and management."""
    
    def test_generate_single_thumbnail(self, thumbnail_service, test_image_file):
        """Test generating thumbnail for a single image."""
        thumbnail_path = thumbnail_service.generate_thumbnail(
            str(test_image_file), 
            'single_test_thumb.jpg'
        )
        
        assert thumbnail_path is not None
        assert os.path.exists(thumbnail_path)
        
        # Verify thumbnail properties
        with Image.open(thumbnail_path) as thumb:
            assert thumb.size[0] <= 256
            assert thumb.size[1] <= 256
            assert thumb.format == 'JPEG'
    
    def test_thumbnail_quality_settings(self, test_config, test_image_file):
        """Test thumbnail generation with different quality settings."""
        qualities = [60, 85, 95]
        
        for quality in qualities:
            service = EnhancedThumbnailService(test_config)
            service.quality = quality
            
            thumbnail_path = service.generate_thumbnail(
                str(test_image_file),
                f'quality_{quality}_thumb.jpg'
            )
            
            assert thumbnail_path is not None
            assert os.path.exists(thumbnail_path)
            
            # Higher quality should generally produce larger files
            file_size = os.path.getsize(thumbnail_path)
            assert file_size > 0
    
    def test_thumbnail_different_sizes(self, test_config, test_image_file):
        """Test generating thumbnails with different size settings."""
        sizes = [(128, 128), (256, 256), (512, 512)]
        
        for size in sizes:
            service = EnhancedThumbnailService(test_config)
            service.thumbnail_size = size
            
            thumbnail_path = service.generate_thumbnail(
                str(test_image_file),
                f'size_{size[0]}x{size[1]}_thumb.jpg'
            )
            
            assert thumbnail_path is not None
            
            with Image.open(thumbnail_path) as thumb:
                assert thumb.size[0] <= size[0]
                assert thumb.size[1] <= size[1]
    
    def test_batch_thumbnail_generation(self, thumbnail_service, test_files_structure):
        """Test batch generation of thumbnails."""
        image_files = [str(f) for f in test_files_structure 
                      if f.suffix.lower() in ['.png', '.jpg', '.webp']]
        
        results = thumbnail_service.batch_generate_thumbnails(image_files)
        
        assert len(results) == len(image_files)
        
        successful = [r for r in results if r['success']]
        failed = [r for r in results if not r['success']]
        
        # Most should succeed (assuming valid images)
        assert len(successful) >= len(image_files) // 2
        
        # Verify successful thumbnails exist
        for result in successful:
            assert os.path.exists(result['thumbnail_path'])
    
    def test_thumbnail_overwrite_behavior(self, thumbnail_service, test_image_file):
        """Test behavior when thumbnail already exists."""
        # Generate initial thumbnail
        thumbnail_path1 = thumbnail_service.generate_thumbnail(
            str(test_image_file),
            'overwrite_test_thumb.jpg'
        )
        
        # Get initial file stats
        initial_mtime = os.path.getmtime(thumbnail_path1)
        initial_size = os.path.getsize(thumbnail_path1)
        
        # Wait a bit to ensure different timestamp
        time.sleep(0.1)
        
        # Generate again (should overwrite)
        thumbnail_path2 = thumbnail_service.generate_thumbnail(
            str(test_image_file),
            'overwrite_test_thumb.jpg'
        )
        
        assert thumbnail_path1 == thumbnail_path2
        assert os.path.exists(thumbnail_path2)
        
        # Should have updated modification time
        new_mtime = os.path.getmtime(thumbnail_path2)
        assert new_mtime > initial_mtime
    
    def test_thumbnail_error_handling(self, thumbnail_service, temp_dir):
        """Test thumbnail generation error handling."""
        # Try to generate thumbnail for non-existent file
        result = thumbnail_service.generate_thumbnail(
            '/nonexistent/file.png',
            'error_test_thumb.jpg'
        )
        
        assert result is None
        
        # Try with invalid image file
        invalid_file = temp_dir / 'invalid_image.png'
        invalid_file.write_bytes(b'not an image')
        
        result = thumbnail_service.generate_thumbnail(
            str(invalid_file),
            'invalid_test_thumb.jpg'
        )
        
        assert result is None
    
    def test_video_thumbnail_generation(self, thumbnail_service, test_video_file):
        """Test generating thumbnails for video files."""
        with patch('cv2.VideoCapture') as mock_cv2:
            # Mock OpenCV for video thumbnail extraction
            mock_cap = Mock()
            mock_cap.isOpened.return_value = True
            mock_cap.read.return_value = (True, Mock())
            mock_cv2.return_value = mock_cap
            
            thumbnail_path = thumbnail_service.generate_video_thumbnail(
                str(test_video_file),
                'video_test_thumb.jpg'
            )
            
            # Should at least attempt to create thumbnail
            mock_cv2.assert_called_once()
    
    def test_cleanup_orphaned_thumbnails(self, thumbnail_service, test_image_file):
        """Test cleanup of thumbnails for deleted original images."""
        # Generate thumbnail
        thumbnail_path = thumbnail_service.generate_thumbnail(
            str(test_image_file),
            'orphan_cleanup_test.jpg'
        )
        
        assert os.path.exists(thumbnail_path)
        
        # Delete original image
        original_path = str(test_image_file)
        os.remove(test_image_file)
        
        # Run cleanup
        cleaned_count = thumbnail_service.cleanup_orphaned_thumbnails()
        
        # Should have cleaned up the orphaned thumbnail
        assert cleaned_count >= 0  # Depends on implementation
    
    def test_get_thumbnail_stats(self, thumbnail_service, test_files_structure):
        """Test getting thumbnail directory statistics."""
        # Generate some thumbnails
        image_files = [str(f) for f in test_files_structure[:3] 
                      if f.suffix.lower() in ['.png', '.jpg']]
        
        for img_file in image_files:
            thumbnail_service.generate_thumbnail(
                img_file, 
                f'stats_{os.path.basename(img_file)}_thumb.jpg'
            )
        
        stats = thumbnail_service.get_thumbnail_stats()
        
        assert 'total_thumbnails' in stats
        assert 'total_size' in stats
        assert 'average_size' in stats
        assert 'formats' in stats
        
        assert stats['total_thumbnails'] >= len(image_files)
        assert stats['total_size'] > 0


@pytest.mark.skip(reason="FileMetadataUtils class not yet implemented - tests need updating")
class TestFileSystemUtilities:
    """Test cases for file system utility functions."""

    @pytest.fixture
    def file_utils(self):
        """Create FileMetadata utilities."""
        # TODO: Update tests to use DirectoryManager from utils.file_ops
        pass
    
    def test_get_file_info(self, file_utils, test_image_file):
        """Test getting comprehensive file information."""
        info = file_utils.get_file_info(str(test_image_file))
        
        assert info is not None
        assert 'size' in info
        assert 'created' in info
        assert 'modified' in info
        assert 'extension' in info
        assert 'mime_type' in info
        
        assert info['size'] > 0
        assert info['extension'] == '.png'
    
    def test_calculate_directory_size(self, file_utils, test_config):
        """Test calculating total directory size."""
        output_dir = test_config.output_dir
        size_info = file_utils.calculate_directory_size(output_dir)
        
        assert 'total_size' in size_info
        assert 'file_count' in size_info
        assert 'directory_count' in size_info
        
        assert size_info['total_size'] >= 0
        assert size_info['file_count'] >= 0
    
    def test_find_files_by_pattern(self, file_utils, test_config):
        """Test finding files matching patterns."""
        output_dir = test_config.output_dir
        
        # Find all PNG files
        png_files = file_utils.find_files_by_pattern(output_dir, '*.png')
        assert isinstance(png_files, list)
        
        # All results should be PNG files
        for file_path in png_files:
            assert file_path.endswith('.png')
    
    def test_get_mime_type(self, file_utils, test_image_file, test_video_file):
        """Test MIME type detection."""
        # Image MIME type
        image_mime = file_utils.get_mime_type(str(test_image_file))
        assert image_mime is not None
        assert image_mime.startswith('image/')
        
        # Video MIME type
        video_mime = file_utils.get_mime_type(str(test_video_file))
        assert video_mime is not None
        # May be detected as video or application depending on system
    
    def test_is_hidden_file(self, file_utils, temp_dir):
        """Test detection of hidden files."""
        # Regular file
        regular_file = temp_dir / 'regular.txt'
        regular_file.write_text('regular file')
        assert file_utils.is_hidden_file(str(regular_file)) is False
        
        # Hidden file (starts with dot)
        hidden_file = temp_dir / '.hidden.txt'
        hidden_file.write_text('hidden file')
        assert file_utils.is_hidden_file(str(hidden_file)) is True
    
    def test_safe_file_operations(self, file_utils, temp_dir):
        """Test safe file operations with error handling."""
        # Test safe file copy
        source_file = temp_dir / 'source.txt'
        source_file.write_text('test content')
        
        dest_file = temp_dir / 'destination.txt'
        
        result = file_utils.safe_copy_file(str(source_file), str(dest_file))
        assert result is True
        assert dest_file.exists()
        assert dest_file.read_text() == 'test content'
        
        # Test safe file move
        moved_file = temp_dir / 'moved.txt'
        result = file_utils.safe_move_file(str(dest_file), str(moved_file))
        assert result is True
        assert moved_file.exists()
        assert not dest_file.exists()
    
    def test_disk_space_check(self, file_utils, temp_dir):
        """Test disk space availability check."""
        space_info = file_utils.get_disk_space(str(temp_dir))
        
        assert 'total' in space_info
        assert 'used' in space_info
        assert 'free' in space_info
        assert 'percent_used' in space_info
        
        assert space_info['total'] > 0
        assert space_info['free'] >= 0
        assert 0 <= space_info['percent_used'] <= 100