"""Image processing utilities for PromptManager.

Provides comprehensive image manipulation including thumbnail generation,
optimization, format conversion, EXIF handling, watermarking, and batch processing.
"""

import io
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, Callable
from dataclasses import dataclass
from enum import Enum

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter
from PIL.ExifTags import TAGS, GPSTAGS

# Optional HEIF/HEIC support
try:
    import pillow_heif  # For HEIF/HEIC support
    pillow_heif.register_heif_opener()
    HAS_HEIF_SUPPORT = True
except ImportError:
    HAS_HEIF_SUPPORT = False

from .logging import get_logger
from .file_ops import AtomicWriter, BatchFileOperation

logger = get_logger("promptmanager.image_processing")

if not HAS_HEIF_SUPPORT:
    logger.warning("HEIF/HEIC support not available. Install pillow-heif for HEIF support.")


class ImageFormat(Enum):
    """Supported image formats."""
    JPEG = "JPEG"
    PNG = "PNG"
    WEBP = "WEBP"
    GIF = "GIF"
    BMP = "BMP"
    TIFF = "TIFF"
    HEIF = "HEIF"
    HEIC = "HEIC"


class ResizeMode(Enum):
    """Image resize modes."""
    FIT = "fit"  # Fit within bounds, maintain aspect ratio
    FILL = "fill"  # Fill bounds, crop if needed
    STRETCH = "stretch"  # Stretch to exact size
    THUMBNAIL = "thumbnail"  # Optimize for thumbnails


class WatermarkPosition(Enum):
    """Watermark positioning options."""
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"
    CENTER = "center"
    TILE = "tile"  # Repeat across image


@dataclass
class ImageInfo:
    """Container for image information."""
    path: Path
    format: str
    mode: str
    size: Tuple[int, int]
    file_size: int
    has_transparency: bool
    has_exif: bool
    exif_data: Optional[Dict[str, Any]] = None
    gps_data: Optional[Dict[str, Any]] = None


class ImageProcessor:
    """Core image processing operations."""
    
    # Quality settings
    JPEG_QUALITY = 85
    WEBP_QUALITY = 85
    PNG_COMPRESS_LEVEL = 6
    
    # Size limits
    MAX_DIMENSION = 10000
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
    
    @classmethod
    def load_image(cls, path: Union[str, Path]) -> Image.Image:
        """Load image with error handling.
        
        Args:
            path: Path to image file
            
        Returns:
            PIL Image object
        """
        image_path = Path(path)
        
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {path}")
        
        if image_path.stat().st_size > cls.MAX_FILE_SIZE:
            raise ValueError(f"Image file too large: {image_path.stat().st_size} bytes")
        
        try:
            img = Image.open(image_path)
            
            # Convert RGBA to RGB if saving as JPEG
            if img.mode == 'RGBA':
                logger.debug(f"Image has transparency: {path}")
            
            return img
            
        except Exception as e:
            logger.error(f"Failed to load image {path}: {e}")
            raise
    
    @classmethod
    def get_image_info(cls, path: Union[str, Path]) -> ImageInfo:
        """Get comprehensive image information.
        
        Args:
            path: Path to image
            
        Returns:
            ImageInfo object
        """
        image_path = Path(path)
        img = cls.load_image(image_path)
        
        # Extract EXIF data
        exif_data = None
        gps_data = None
        has_exif = False
        
        try:
            exif = img.getexif()
            if exif:
                has_exif = True
                exif_data = {}
                
                for tag_id, value in exif.items():
                    tag = TAGS.get(tag_id, tag_id)
                    exif_data[tag] = value
                
                # Extract GPS data if present
                if "GPSInfo" in exif_data:
                    gps_info = exif_data["GPSInfo"]
                    gps_data = {}
                    
                    for key in gps_info.keys():
                        decode = GPSTAGS.get(key, key)
                        gps_data[decode] = gps_info[key]
                        
        except Exception as e:
            logger.debug(f"Could not extract EXIF data: {e}")
        
        return ImageInfo(
            path=image_path,
            format=img.format or "UNKNOWN",
            mode=img.mode,
            size=img.size,
            file_size=image_path.stat().st_size,
            has_transparency=img.mode in ('RGBA', 'LA', 'P'),
            has_exif=has_exif,
            exif_data=exif_data,
            gps_data=gps_data
        )
    
    @classmethod
    def resize_image(
        cls,
        img: Image.Image,
        size: Tuple[int, int],
        mode: ResizeMode = ResizeMode.FIT,
        resample: Image.Resampling = Image.Resampling.LANCZOS
    ) -> Image.Image:
        """Resize image with various modes.
        
        Args:
            img: PIL Image
            size: Target size (width, height)
            mode: Resize mode
            resample: Resampling filter
            
        Returns:
            Resized image
        """
        target_width, target_height = size
        
        if mode == ResizeMode.FIT:
            # Maintain aspect ratio, fit within bounds
            img.thumbnail(size, resample)
            return img
            
        elif mode == ResizeMode.FILL:
            # Fill bounds, crop if needed
            return ImageOps.fit(img, size, resample)
            
        elif mode == ResizeMode.STRETCH:
            # Stretch to exact size
            return img.resize(size, resample)
            
        elif mode == ResizeMode.THUMBNAIL:
            # Optimized for thumbnails
            # Use LANCZOS for downscaling, add slight sharpening
            img.thumbnail(size, Image.Resampling.LANCZOS)
            
            # Apply slight sharpening for thumbnails
            if img.size[0] < 500:  # Only for small images
                img = img.filter(ImageFilter.UnsharpMask(radius=0.5, percent=50))
            
            return img
        
        else:
            raise ValueError(f"Unknown resize mode: {mode}")


class ThumbnailGenerator:
    """Generate optimized thumbnails."""
    
    # Standard thumbnail sizes
    SIZES = {
        'small': (150, 150),
        'medium': (300, 300),
        'large': (600, 600),
        'xlarge': (1200, 1200)
    }
    
    def __init__(self, cache_dir: Union[str, Path] = None):
        """Initialize thumbnail generator.
        
        Args:
            cache_dir: Directory for thumbnail cache
        """
        self.cache_dir = Path(cache_dir) if cache_dir else Path.cwd() / '.thumbnails'
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def generate(
        self,
        image_path: Union[str, Path],
        size: Union[str, Tuple[int, int]] = 'medium',
        force: bool = False
    ) -> Path:
        """Generate thumbnail for image.
        
        Args:
            image_path: Source image path
            size: Size name or tuple (width, height)
            force: Force regeneration even if cached
            
        Returns:
            Path to thumbnail
        """
        source_path = Path(image_path)
        
        # Get target size
        if isinstance(size, str):
            target_size = self.SIZES.get(size, self.SIZES['medium'])
        else:
            target_size = size
        
        # Generate cache path
        cache_name = f"{source_path.stem}_{target_size[0]}x{target_size[1]}.jpg"
        cache_path = self.cache_dir / cache_name
        
        # Check cache
        if not force and cache_path.exists():
            # Check if source is newer than cache
            if cache_path.stat().st_mtime >= source_path.stat().st_mtime:
                logger.debug(f"Using cached thumbnail: {cache_path}")
                return cache_path
        
        # Generate thumbnail
        try:
            img = ImageProcessor.load_image(source_path)
            
            # Convert to RGB for JPEG output
            if img.mode in ('RGBA', 'LA', 'P'):
                # Create white background
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = background
            elif img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            
            # Resize
            thumb = ImageProcessor.resize_image(
                img,
                target_size,
                ResizeMode.THUMBNAIL
            )
            
            # Save with optimization
            with AtomicWriter(cache_path, 'wb') as f:
                thumb.save(
                    f,
                    'JPEG',
                    quality=80,
                    optimize=True,
                    progressive=True
                )
            
            logger.debug(f"Generated thumbnail: {cache_path}")
            return cache_path
            
        except Exception as e:
            logger.error(f"Failed to generate thumbnail: {e}")
            raise
    
    def generate_batch(
        self,
        image_paths: List[Union[str, Path]],
        size: Union[str, Tuple[int, int]] = 'medium',
        max_workers: int = 4
    ) -> Dict[Path, Optional[Path]]:
        """Generate thumbnails for multiple images.
        
        Args:
            image_paths: List of image paths
            size: Target size
            max_workers: Number of parallel workers
            
        Returns:
            Dictionary mapping source to thumbnail paths
        """
        results = {}
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.generate, path, size): Path(path)
                for path in image_paths
            }
            
            for future in as_completed(futures):
                source_path = futures[future]
                try:
                    thumb_path = future.result()
                    results[source_path] = thumb_path
                except Exception as e:
                    logger.error(f"Failed to generate thumbnail for {source_path}: {e}")
                    results[source_path] = None
        
        return results
    
    def clear_cache(self, older_than_days: Optional[int] = None):
        """Clear thumbnail cache.
        
        Args:
            older_than_days: Only remove thumbnails older than this
        """
        from .file_ops import FileCleanup
        
        if older_than_days:
            FileCleanup.remove_old_files(self.cache_dir, older_than_days, '*.jpg')
        else:
            for thumb in self.cache_dir.glob('*.jpg'):
                thumb.unlink()
            logger.info("Cleared thumbnail cache")


class ImageOptimizer:
    """Optimize images for size and quality."""
    
    @staticmethod
    def optimize(
        image_path: Union[str, Path],
        output_path: Optional[Union[str, Path]] = None,
        max_size: Optional[Tuple[int, int]] = None,
        quality: int = 85,
        format: Optional[ImageFormat] = None
    ) -> Path:
        """Optimize image for web/storage.
        
        Args:
            image_path: Source image
            output_path: Output path (default: overwrite source)
            max_size: Maximum dimensions
            quality: JPEG/WebP quality (1-100)
            format: Target format
            
        Returns:
            Path to optimized image
        """
        source = Path(image_path)
        output = Path(output_path) if output_path else source
        
        img = ImageProcessor.load_image(source)
        
        # Resize if needed
        if max_size and (img.width > max_size[0] or img.height > max_size[1]):
            img = ImageProcessor.resize_image(img, max_size, ResizeMode.FIT)
        
        # Determine output format
        if format:
            save_format = format.value
        else:
            # Auto-detect best format
            if img.mode == 'RGBA' or 'transparency' in img.info:
                save_format = 'PNG'
            else:
                save_format = 'JPEG'
        
        # Prepare save kwargs
        save_kwargs = {}
        
        if save_format == 'JPEG':
            # Convert to RGB if needed
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            save_kwargs = {
                'quality': quality,
                'optimize': True,
                'progressive': True
            }
            
        elif save_format == 'PNG':
            save_kwargs = {
                'compress_level': ImageProcessor.PNG_COMPRESS_LEVEL,
                'optimize': True
            }
            
        elif save_format == 'WEBP':
            save_kwargs = {
                'quality': quality,
                'method': 6,  # Slowest/best compression
                'lossless': False
            }
        
        # Save optimized image
        with AtomicWriter(output, 'wb') as f:
            img.save(f, save_format, **save_kwargs)
        
        # Log size reduction
        original_size = source.stat().st_size
        new_size = output.stat().st_size
        reduction = (1 - new_size / original_size) * 100
        
        logger.info(
            f"Optimized {source.name}: {original_size:,} -> {new_size:,} bytes "
            f"({reduction:.1f}% reduction)"
        )
        
        return output
    
    @staticmethod
    def optimize_batch(
        image_paths: List[Union[str, Path]],
        max_workers: int = 4,
        **kwargs
    ) -> Dict[Path, bool]:
        """Optimize multiple images in parallel.
        
        Args:
            image_paths: List of image paths
            max_workers: Number of parallel workers
            **kwargs: Arguments for optimize()
            
        Returns:
            Dictionary mapping paths to success status
        """
        results = {}
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(ImageOptimizer.optimize, path, **kwargs): Path(path)
                for path in image_paths
            }
            
            for future in as_completed(futures):
                source_path = futures[future]
                try:
                    future.result()
                    results[source_path] = True
                except Exception as e:
                    logger.error(f"Failed to optimize {source_path}: {e}")
                    results[source_path] = False
        
        return results


class FormatConverter:
    """Convert between image formats."""
    
    @staticmethod
    def convert(
        image_path: Union[str, Path],
        output_format: ImageFormat,
        output_path: Optional[Union[str, Path]] = None,
        keep_metadata: bool = False
    ) -> Path:
        """Convert image to different format.
        
        Args:
            image_path: Source image
            output_format: Target format
            output_path: Output path (default: same name, new extension)
            keep_metadata: Preserve EXIF data
            
        Returns:
            Path to converted image
        """
        source = Path(image_path)
        
        # Generate output path if not provided
        if output_path:
            output = Path(output_path)
        else:
            ext = '.' + output_format.value.lower()
            if ext == '.jpeg':
                ext = '.jpg'
            output = source.with_suffix(ext)
        
        img = ImageProcessor.load_image(source)
        
        # Get EXIF data if preserving
        exif = None
        if keep_metadata:
            try:
                exif = img.getexif()
            except:
                pass
        
        # Handle format-specific conversions
        if output_format == ImageFormat.JPEG:
            # Convert to RGB
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
        
        elif output_format == ImageFormat.PNG:
            # PNG supports all modes
            pass
        
        elif output_format == ImageFormat.WEBP:
            # WebP supports RGB and RGBA
            if img.mode not in ('RGB', 'RGBA'):
                img = img.convert('RGBA' if img.mode == 'P' else 'RGB')
        
        elif output_format in (ImageFormat.BMP, ImageFormat.TIFF):
            # Convert to RGB for compatibility
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
        
        # Save with appropriate options
        save_kwargs = {}
        
        if exif and output_format in (ImageFormat.JPEG, ImageFormat.PNG, ImageFormat.TIFF):
            save_kwargs['exif'] = exif
        
        if output_format == ImageFormat.JPEG:
            save_kwargs.update({
                'quality': ImageProcessor.JPEG_QUALITY,
                'optimize': True
            })
        elif output_format == ImageFormat.PNG:
            save_kwargs['compress_level'] = ImageProcessor.PNG_COMPRESS_LEVEL
        elif output_format == ImageFormat.WEBP:
            save_kwargs['quality'] = ImageProcessor.WEBP_QUALITY
        
        with AtomicWriter(output, 'wb') as f:
            img.save(f, output_format.value, **save_kwargs)
        
        logger.info(f"Converted {source.name} to {output_format.value}")
        return output
    
    @staticmethod
    def convert_batch(
        image_paths: List[Union[str, Path]],
        output_format: ImageFormat,
        output_dir: Optional[Union[str, Path]] = None,
        max_workers: int = 4
    ) -> Dict[Path, Optional[Path]]:
        """Convert multiple images to format.
        
        Args:
            image_paths: List of source images
            output_format: Target format
            output_dir: Output directory
            max_workers: Parallel workers
            
        Returns:
            Dictionary mapping source to output paths
        """
        results = {}
        
        # Prepare output directory
        if output_dir:
            out_dir = Path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            
            for path in image_paths:
                source = Path(path)
                
                if output_dir:
                    ext = '.' + output_format.value.lower()
                    if ext == '.jpeg':
                        ext = '.jpg'
                    output_path = out_dir / source.with_suffix(ext).name
                else:
                    output_path = None
                
                future = executor.submit(
                    FormatConverter.convert,
                    source,
                    output_format,
                    output_path
                )
                futures[future] = source
            
            for future in as_completed(futures):
                source_path = futures[future]
                try:
                    output_path = future.result()
                    results[source_path] = output_path
                except Exception as e:
                    logger.error(f"Failed to convert {source_path}: {e}")
                    results[source_path] = None
        
        return results


class ExifHandler:
    """Handle EXIF metadata in images."""
    
    @staticmethod
    def read_exif(image_path: Union[str, Path]) -> Dict[str, Any]:
        """Read EXIF data from image.
        
        Args:
            image_path: Path to image
            
        Returns:
            Dictionary of EXIF tags and values
        """
        img = ImageProcessor.load_image(image_path)
        exif_data = {}
        
        try:
            exif = img.getexif()
            
            for tag_id, value in exif.items():
                tag = TAGS.get(tag_id, tag_id)
                
                # Decode bytes
                if isinstance(value, bytes):
                    try:
                        value = value.decode('utf-8')
                    except:
                        value = str(value)
                
                exif_data[tag] = value
            
        except Exception as e:
            logger.debug(f"Could not read EXIF: {e}")
        
        return exif_data
    
    @staticmethod
    def strip_exif(
        image_path: Union[str, Path],
        output_path: Optional[Union[str, Path]] = None
    ) -> Path:
        """Remove EXIF data from image.
        
        Args:
            image_path: Source image
            output_path: Output path (default: overwrite)
            
        Returns:
            Path to stripped image
        """
        source = Path(image_path)
        output = Path(output_path) if output_path else source
        
        img = ImageProcessor.load_image(source)
        
        # Remove EXIF by creating new image
        data = list(img.getdata())
        img_without_exif = Image.new(img.mode, img.size)
        img_without_exif.putdata(data)
        
        # Save without EXIF
        with AtomicWriter(output, 'wb') as f:
            if img.format:
                img_without_exif.save(f, img.format)
            else:
                img_without_exif.save(f, 'PNG')
        
        logger.info(f"Stripped EXIF from {source.name}")
        return output
    
    @staticmethod
    def copy_exif(
        source_image: Union[str, Path],
        target_image: Union[str, Path],
        output_path: Optional[Union[str, Path]] = None
    ) -> Path:
        """Copy EXIF from one image to another.
        
        Args:
            source_image: Image with EXIF to copy
            target_image: Image to receive EXIF
            output_path: Output path
            
        Returns:
            Path to image with copied EXIF
        """
        source = Path(source_image)
        target = Path(target_image)
        output = Path(output_path) if output_path else target
        
        # Get EXIF from source
        source_img = ImageProcessor.load_image(source)
        exif = source_img.getexif()
        
        if not exif:
            logger.warning(f"No EXIF data in source: {source}")
            return target
        
        # Apply to target
        target_img = ImageProcessor.load_image(target)
        
        with AtomicWriter(output, 'wb') as f:
            target_img.save(f, target_img.format or 'JPEG', exif=exif)
        
        logger.info(f"Copied EXIF to {output.name}")
        return output


class Watermarker:
    """Add watermarks to images."""
    
    def __init__(
        self,
        watermark: Union[str, Path, Image.Image],
        opacity: float = 0.5,
        scale: float = 0.2
    ):
        """Initialize watermarker.
        
        Args:
            watermark: Watermark image or path
            opacity: Watermark opacity (0-1)
            scale: Scale relative to target image
        """
        if isinstance(watermark, (str, Path)):
            self.watermark = Image.open(watermark)
        else:
            self.watermark = watermark
        
        # Ensure watermark has alpha channel
        if self.watermark.mode != 'RGBA':
            self.watermark = self.watermark.convert('RGBA')
        
        self.opacity = max(0, min(1, opacity))
        self.scale = max(0.01, min(1, scale))
    
    def apply(
        self,
        image_path: Union[str, Path],
        position: WatermarkPosition = WatermarkPosition.BOTTOM_RIGHT,
        output_path: Optional[Union[str, Path]] = None,
        padding: int = 10
    ) -> Path:
        """Apply watermark to image.
        
        Args:
            image_path: Target image
            position: Watermark position
            output_path: Output path
            padding: Padding from edges
            
        Returns:
            Path to watermarked image
        """
        source = Path(image_path)
        output = Path(output_path) if output_path else source
        
        img = ImageProcessor.load_image(source)
        
        # Convert to RGBA for compositing
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        # Scale watermark
        wm_width = int(img.width * self.scale)
        wm_height = int(self.watermark.height * (wm_width / self.watermark.width))
        watermark = self.watermark.resize((wm_width, wm_height), Image.Resampling.LANCZOS)
        
        # Adjust opacity
        if self.opacity < 1:
            alpha = watermark.split()[-1]
            alpha = alpha.point(lambda p: p * self.opacity)
            watermark.putalpha(alpha)
        
        # Calculate position
        if position == WatermarkPosition.TOP_LEFT:
            pos = (padding, padding)
        elif position == WatermarkPosition.TOP_RIGHT:
            pos = (img.width - wm_width - padding, padding)
        elif position == WatermarkPosition.BOTTOM_LEFT:
            pos = (padding, img.height - wm_height - padding)
        elif position == WatermarkPosition.BOTTOM_RIGHT:
            pos = (img.width - wm_width - padding, img.height - wm_height - padding)
        elif position == WatermarkPosition.CENTER:
            pos = ((img.width - wm_width) // 2, (img.height - wm_height) // 2)
        elif position == WatermarkPosition.TILE:
            # Tile watermark across image
            for x in range(0, img.width, wm_width + padding):
                for y in range(0, img.height, wm_height + padding):
                    img.paste(watermark, (x, y), watermark)
            pos = None
        else:
            pos = (img.width - wm_width - padding, img.height - wm_height - padding)
        
        # Apply watermark
        if pos:
            img.paste(watermark, pos, watermark)
        
        # Save
        save_format = 'PNG' if output.suffix.lower() == '.png' else 'JPEG'
        
        if save_format == 'JPEG':
            # Convert to RGB for JPEG
            background = Image.new('RGB', img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background
        
        with AtomicWriter(output, 'wb') as f:
            img.save(f, save_format, quality=95)
        
        logger.info(f"Applied watermark to {source.name}")
        return output
    
    def apply_batch(
        self,
        image_paths: List[Union[str, Path]],
        position: WatermarkPosition = WatermarkPosition.BOTTOM_RIGHT,
        output_dir: Optional[Union[str, Path]] = None,
        max_workers: int = 4
    ) -> Dict[Path, Optional[Path]]:
        """Apply watermark to multiple images.
        
        Args:
            image_paths: List of images
            position: Watermark position
            output_dir: Output directory
            max_workers: Parallel workers
            
        Returns:
            Dictionary mapping source to output paths
        """
        results = {}
        
        if output_dir:
            out_dir = Path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            
            for path in image_paths:
                source = Path(path)
                
                if output_dir:
                    output_path = out_dir / source.name
                else:
                    output_path = None
                
                future = executor.submit(
                    self.apply,
                    source,
                    position,
                    output_path
                )
                futures[future] = source
            
            for future in as_completed(futures):
                source_path = futures[future]
                try:
                    output_path = future.result()
                    results[source_path] = output_path
                except Exception as e:
                    logger.error(f"Failed to watermark {source_path}: {e}")
                    results[source_path] = None
        
        return results


class BatchProcessor:
    """Process multiple images with various operations."""
    
    def __init__(self, max_workers: int = 4):
        """Initialize batch processor.
        
        Args:
            max_workers: Number of parallel workers
        """
        self.max_workers = max_workers
        self.operations: List[Callable] = []
    
    def add_resize(self, size: Tuple[int, int], mode: ResizeMode = ResizeMode.FIT):
        """Add resize operation.
        
        Args:
            size: Target size
            mode: Resize mode
        """
        def op(img):
            return ImageProcessor.resize_image(img, size, mode)
        self.operations.append(op)
        return self
    
    def add_optimize(self, quality: int = 85):
        """Add optimization operation.
        
        Args:
            quality: JPEG/WebP quality
        """
        def op(img_path):
            return ImageOptimizer.optimize(img_path, quality=quality)
        self.operations.append(op)
        return self
    
    def add_watermark(self, watermark: Union[str, Path, Image.Image], position: WatermarkPosition):
        """Add watermark operation.
        
        Args:
            watermark: Watermark image
            position: Watermark position
        """
        wm = Watermarker(watermark)
        def op(img_path):
            return wm.apply(img_path, position)
        self.operations.append(op)
        return self
    
    def add_convert(self, format: ImageFormat):
        """Add format conversion.
        
        Args:
            format: Target format
        """
        def op(img_path):
            return FormatConverter.convert(img_path, format)
        self.operations.append(op)
        return self
    
    def process(
        self,
        image_paths: List[Union[str, Path]],
        output_dir: Optional[Union[str, Path]] = None,
        preserve_originals: bool = True
    ) -> Dict[Path, Dict[str, Any]]:
        """Process images with all operations.
        
        Args:
            image_paths: List of images
            output_dir: Output directory
            preserve_originals: Keep original files
            
        Returns:
            Dictionary with processing results
        """
        results = {}
        
        # Setup output directory
        if output_dir:
            out_dir = Path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
        
        # Process each image
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            
            for path in image_paths:
                source = Path(path)
                
                # Determine output path
                if preserve_originals:
                    if output_dir:
                        work_path = out_dir / source.name
                    else:
                        work_path = source.with_stem(f"{source.stem}_processed")
                    
                    # Copy to work path
                    import shutil
                    shutil.copy2(source, work_path)
                else:
                    work_path = source
                
                # Submit processing job
                future = executor.submit(self._process_single, work_path)
                futures[future] = source
            
            # Collect results
            for future in as_completed(futures):
                source_path = futures[future]
                try:
                    result = future.result()
                    results[source_path] = {
                        'success': True,
                        'output': result,
                        'operations': len(self.operations)
                    }
                except Exception as e:
                    logger.error(f"Failed to process {source_path}: {e}")
                    results[source_path] = {
                        'success': False,
                        'error': str(e),
                        'operations': 0
                    }
        
        return results
    
    def _process_single(self, image_path: Path) -> Path:
        """Process single image through all operations.
        
        Args:
            image_path: Image to process
            
        Returns:
            Path to processed image
        """
        current_path = image_path
        
        for i, operation in enumerate(self.operations):
            try:
                # Some operations work on paths, others on images
                result = operation(current_path)
                
                if isinstance(result, Path):
                    current_path = result
                elif isinstance(result, Image.Image):
                    # Save intermediate result
                    with AtomicWriter(current_path, 'wb') as f:
                        result.save(f, result.format or 'PNG')
                
                logger.debug(f"Applied operation {i+1}/{len(self.operations)} to {image_path.name}")
                
            except Exception as e:
                logger.error(f"Operation {i+1} failed on {image_path.name}: {e}")
                raise
        
        return current_path


# Convenience functions
def create_thumbnail(
    image_path: Union[str, Path],
    size: Union[str, Tuple[int, int]] = 'medium'
) -> Path:
    """Create thumbnail for image.
    
    Args:
        image_path: Source image
        size: Size name or dimensions
        
    Returns:
        Path to thumbnail
    """
    generator = ThumbnailGenerator()
    return generator.generate(image_path, size)


def optimize_image(image_path: Union[str, Path], quality: int = 85) -> Path:
    """Optimize image for web.
    
    Args:
        image_path: Source image
        quality: JPEG/WebP quality
        
    Returns:
        Path to optimized image
    """
    return ImageOptimizer.optimize(image_path, quality=quality)


def convert_format(
    image_path: Union[str, Path],
    format: Union[str, ImageFormat]
) -> Path:
    """Convert image to different format.
    
    Args:
        image_path: Source image
        format: Target format name or enum
        
    Returns:
        Path to converted image
    """
    if isinstance(format, str):
        format = ImageFormat[format.upper()]
    
    return FormatConverter.convert(image_path, format)


def strip_metadata(image_path: Union[str, Path]) -> Path:
    """Remove EXIF and other metadata from image.
    
    Args:
        image_path: Source image
        
    Returns:
        Path to stripped image
    """
    return ExifHandler.strip_exif(image_path)


def batch_process(
    image_paths: List[Union[str, Path]],
    operations: List[str],
    **kwargs
) -> Dict[Path, Dict[str, Any]]:
    """Process multiple images with specified operations.
    
    Args:
        image_paths: List of images
        operations: List of operation names
        **kwargs: Operation parameters
        
    Returns:
        Processing results
    """
    processor = BatchProcessor()
    
    for op in operations:
        if op == 'resize':
            processor.add_resize(kwargs.get('size', (800, 800)))
        elif op == 'optimize':
            processor.add_optimize(kwargs.get('quality', 85))
        elif op == 'watermark':
            if 'watermark_path' in kwargs:
                processor.add_watermark(
                    kwargs['watermark_path'],
                    kwargs.get('position', WatermarkPosition.BOTTOM_RIGHT)
                )
        elif op == 'convert':
            if 'format' in kwargs:
                processor.add_convert(ImageFormat[kwargs['format'].upper()])
    
    return processor.process(image_paths)
