"""
Image Scanner Service
Scans ComfyUI output directory for images with prompt metadata
"""

import os
import json
import asyncio
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional, List, AsyncGenerator
from PIL import Image
import logging

from .metadata import ComfyMetadataParser

# Import hashing function once at module level to avoid sys.path modifications during scanning
try:
    from utils.validation.hashing import generate_prompt_hash
except ImportError:
    # Fallback: add project root to path and try again
    import sys
    import os
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from utils.validation.hashing import generate_prompt_hash


class ImageScanner:
    """Service for scanning images and extracting ComfyUI metadata"""

    def __init__(self, api_instance):
        """Initialize the scanner with an API instance"""
        self.api = api_instance
        self.logger = api_instance.logger if hasattr(api_instance, 'logger') else logging.getLogger(__name__)
        # Get the prompt repository from the API instance
        self.prompt_repo = api_instance.prompt_repo if hasattr(api_instance, 'prompt_repo') else None
        self.generated_image_repo = api_instance.generated_image_repo if hasattr(api_instance, 'generated_image_repo') else None
        self._comfy_parser = ComfyMetadataParser()

    def _find_comfyui_output_dir(self) -> Optional[str]:
        """Find the ComfyUI output directory"""
        # Try to get from ComfyUI folder_paths
        try:
            import folder_paths
            output_dir = folder_paths.get_output_directory()
            if output_dir and os.path.exists(output_dir):
                return output_dir
        except ImportError:
            pass

        # Do NOT search relative to CWD - require proper ComfyUI root
        raise RuntimeError(
            "Cannot find ComfyUI output directory. "
            "Please ensure PromptManager is installed in ComfyUI/custom_nodes/ "
            "or set COMFYUI_PATH environment variable."
        )

        self.logger.error("ComfyUI output directory not found")
        return None

    def extract_comfyui_metadata(self, image_path: str) -> Optional[Dict[str, Any]]:
        """Extract ComfyUI metadata from a PNG file"""
        try:
            with Image.open(image_path) as img:
                metadata = {}

                # Check for PNG metadata
                if hasattr(img, 'info'):
                    # Look for ComfyUI specific keys
                    for key in ['prompt', 'workflow', 'extra_pnginfo']:
                        if key in img.info:
                            metadata[key] = img.info[key]

                return metadata if metadata else {}

        except Exception as e:
            self.logger.error(f"Failed to extract metadata from {image_path}: {e}")
            return None

    def extract_basic_metadata(self, file_path: str) -> Dict[str, Any]:
        """Extract basic metadata from non-PNG media files"""
        metadata = {}
        file_ext = Path(file_path).suffix.lower()

        try:
            # Get file stats
            stat = os.stat(file_path)
            metadata['file_size'] = stat.st_size
            metadata['modified_time'] = stat.st_mtime

            # Handle image formats (WebP, JPEG, etc)
            if file_ext in ['.webp', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff']:
                try:
                    with Image.open(file_path) as img:
                        metadata['width'] = img.width
                        metadata['height'] = img.height
                        metadata['format'] = img.format
                        metadata['mode'] = img.mode
                        # For animated GIFs
                        if hasattr(img, 'n_frames'):
                            metadata['frames'] = img.n_frames
                except Exception as e:
                    self.logger.debug(f"Could not extract image metadata from {file_path}: {e}")

            # Handle video formats
            elif file_ext in ['.mp4', '.avi', '.mov', '.webm', '.mkv']:
                metadata['media_type'] = 'video'
                # Basic file info only - would need ffprobe for detailed metadata
                metadata['format'] = file_ext[1:].upper()

            # Handle audio formats
            elif file_ext in ['.wav', '.mp3', '.ogg', '.flac', '.aac', '.m4a']:
                metadata['media_type'] = 'audio'
                metadata['format'] = file_ext[1:].upper()

            return metadata

        except Exception as e:
            self.logger.error(f"Failed to extract basic metadata from {file_path}: {e}")
            return {}

    def parse_comfyui_prompt(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Parse ComfyUI prompt data from metadata"""
        if not metadata:
            return {}

        parsed = self._comfy_parser.parse_metadata(metadata)

        result: Dict[str, Any] = {
            "positive_prompt": parsed.positive_prompt,
            "negative_prompt": parsed.negative_prompt,
            "model": parsed.model,
            "cfg_scale": parsed.cfg_scale,
            "steps": parsed.steps,
            "sampler": parsed.sampler,
            "scheduler": parsed.scheduler,
            "seed": parsed.seed,
            "clip_skip": parsed.clip_skip,
            "loras": [lora.__dict__ for lora in parsed.loras],
            "raw_chunks": parsed.raw_chunks,
        }

        if parsed.prompt:
            result["prompt_data"] = parsed.prompt
        if parsed.workflow:
            result["workflow"] = parsed.workflow
        if parsed.denoise is not None:
            result["denoise"] = parsed.denoise

        # Remove empty entries while preserving False/0 values
        return {key: value for key, value in result.items() if value not in (None, [], {})}

    def extract_readable_prompt(self, parsed_data: Dict[str, Any]) -> Optional[str]:
        """Extract readable prompt text from parsed data"""
        prompt_parts = []

        if 'positive_prompt' in parsed_data:
            prompt_parts.append(f"Positive: {parsed_data['positive_prompt']}")

        if 'negative_prompt' in parsed_data:
            prompt_parts.append(f"Negative: {parsed_data['negative_prompt']}")

        if prompt_parts:
            return "\n".join(prompt_parts)

        # Fallback: try to extract any text from prompt_data
        if 'prompt_data' in parsed_data:
            texts = []
            for node_id, node_data in parsed_data['prompt_data'].items():
                if isinstance(node_data, dict) and 'inputs' in node_data:
                    if 'text' in node_data['inputs']:
                        texts.append(node_data['inputs']['text'])

            if texts:
                return "\n".join(texts)

        return None

    async def scan_images_generator(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Generator that yields progress updates during image scanning"""
        try:
            # Broadcast realtime progress if available
            if hasattr(self.api, 'realtime'):
                await self.api.realtime.send_progress('scan', 0, 'Starting scan...')

            # Find ComfyUI output directory
            output_dir = self._find_comfyui_output_dir()
            if not output_dir:
                error_msg = 'ComfyUI output directory not found'
                if hasattr(self.api, 'realtime'):
                    await self.api.realtime.send_toast(error_msg, 'error')
                yield {'type': 'error', 'message': error_msg}
                return

            yield {'type': 'progress', 'progress': 0, 'status': 'Scanning for PNG files...', 'processed': 0, 'found': 0}

            # Broadcast scan start
            if hasattr(self.api, 'realtime'):
                await self.api.realtime.send_progress('scan', 0, 'Scanning for PNG files...')

            # Define supported media formats
            image_extensions = ['*.png', '*.jpg', '*.jpeg', '*.webp', '*.gif', '*.bmp', '*.tiff']
            video_extensions = ['*.mp4', '*.avi', '*.mov', '*.webm', '*.mkv']
            audio_extensions = ['*.wav', '*.mp3', '*.ogg', '*.flac', '*.aac', '*.m4a']

            # Find all media files
            media_files = []
            for ext in image_extensions + video_extensions + audio_extensions:
                media_files.extend(Path(output_dir).rglob(ext))

            total_files = len(media_files)

            if total_files == 0:
                if hasattr(self.api, 'realtime'):
                    await self.api.realtime.send_progress('scan', 100, 'No media files found')
                    await self.api.realtime.send_toast('No media files found in output directory', 'info')
                yield {'type': 'complete', 'processed': 0, 'found': 0, 'added': 0, 'linked': 0}
                return

            yield {
                'type': 'progress',
                'progress': 5,
                'status': f'Found {total_files} media files to process...',
                'processed': 0,
                'found': 0
            }

            processed_count = 0
            found_count = 0
            added_count = 0
            linked_count = 0

            for i, media_file in enumerate(media_files):
                # Always increment processed count, even if processing fails
                processed_count += 1
                
                try:
                    # Extract metadata (PNG files may have ComfyUI metadata)
                    if media_file.suffix.lower() == '.png':
                        metadata = self.extract_comfyui_metadata(str(media_file))
                    else:
                        # For other formats, extract basic metadata
                        metadata = self.extract_basic_metadata(str(media_file))

                    # For PNG files, try to extract prompt data
                    if media_file.suffix.lower() == '.png' and metadata:
                        # Parse ComfyUI prompt data
                        parsed_data = self.parse_comfyui_prompt(metadata)

                        # Check if we found any meaningful prompt data
                        if parsed_data.get('positive_prompt') or parsed_data.get('negative_prompt'):
                            found_count += 1

                            # Extract readable prompt text
                            prompt_text = self.extract_readable_prompt(parsed_data)

                            if prompt_text and prompt_text.strip():
                                # Generate hash for duplicate detection
                                prompt_hash = generate_prompt_hash(prompt_text.strip())

                                # Check if prompt already exists
                                if self.prompt_repo:
                                    existing = self.prompt_repo.find_by_hash(prompt_hash)
                                    if existing:
                                        # Link image to existing prompt (if not already linked)
                                        if self.generated_image_repo:
                                            try:
                                                # Check if this image is already linked to this prompt
                                                existing_link = self.generated_image_repo.find_by_prompt_and_path(
                                                    existing['id'], str(media_file)
                                                )

                                                if not existing_link:
                                                    # Add the image to the generated_images table
                                                    image_data = {
                                                        'prompt_id': existing['id'],
                                                        'image_path': str(media_file),  # Use image_path, not file_path
                                                        'filename': media_file.name,
                                                        'prompt_metadata': parsed_data,  # Use prompt_metadata column name
                                                        'media_type': 'image'  # PNG files are images
                                                    }
                                                    self.generated_image_repo.create(image_data)
                                                    linked_count += 1
                                                    self.logger.debug(f"Linked image {media_file.name} to existing prompt {existing['id']}")
                                                else:
                                                    self.logger.debug(f"Image {media_file.name} already linked to prompt {existing['id']}")
                                            except Exception as e:
                                                self.logger.error(f"Failed to link image {media_file} to existing prompt: {e}")
                                    else:
                                        # Save new prompt
                                        prompt_data = {
                                            'positive_prompt': parsed_data.get('positive_prompt', prompt_text.strip()),
                                            'negative_prompt': parsed_data.get('negative_prompt', ''),
                                            'category': 'scanned',
                                            'tags': json.dumps(['auto-scanned']),
                                            'notes': f'Auto-scanned from {media_file.name}',
                                            'hash': prompt_hash
                                        }
                                        prompt_id = self.prompt_repo.create(prompt_data)

                                        if prompt_id:
                                            added_count += 1
                                            self.logger.info(f"Saved new prompt with ID {prompt_id} from {media_file.name}")

                                            # Link image to new prompt (check shouldn't be needed but be safe)
                                            if self.generated_image_repo:
                                                try:
                                                    # Check if somehow this image was already linked
                                                    existing_link = self.generated_image_repo.find_by_prompt_and_path(
                                                        prompt_id, str(media_file)
                                                    )

                                                    if not existing_link:
                                                        image_data = {
                                                            'prompt_id': prompt_id,
                                                            'image_path': str(media_file),  # Use image_path, not file_path
                                                            'filename': media_file.name,
                                                            'prompt_metadata': parsed_data,  # Use prompt_metadata column name
                                                            'media_type': 'image'  # PNG files are images
                                                        }
                                                        self.generated_image_repo.create(image_data)
                                                        linked_count += 1
                                                except Exception as e:
                                                    self.logger.error(f"Failed to link image {media_file} to new prompt: {e}")

                    # For non-PNG media files, try to link them to prompts
                    elif metadata:
                        # Try to find a related PNG file with the same base name
                        base_name = media_file.stem  # filename without extension
                        prompt_id = None

                        # Strategy 1: Look for a PNG with the same base name
                        if self.generated_image_repo:
                            # Search for PNG files with similar names
                            possible_png_path = media_file.parent / f"{base_name}.png"
                            if possible_png_path.exists():
                                # Check if this PNG has a prompt
                                existing_png = self.generated_image_repo.find_by_path(str(possible_png_path))
                                if existing_png and existing_png.get('prompt_id'):
                                    prompt_id = existing_png['prompt_id']
                                    self.logger.debug(f"Found related PNG for {media_file.name}, linking to prompt {prompt_id}")

                        # Strategy 2: Look for files with similar timestamps (within 5 seconds)
                        if not prompt_id and self.generated_image_repo and 'modified_time' in metadata:
                            target_time = metadata['modified_time']
                            # Search for images created around the same time
                            similar_files = []
                            for other_file in media_file.parent.glob("*.png"):
                                try:
                                    other_stat = other_file.stat()
                                    time_diff = abs(other_stat.st_mtime - target_time)
                                    if time_diff <= 5:  # Within 5 seconds
                                        existing = self.generated_image_repo.find_by_path(str(other_file))
                                        if existing and existing.get('prompt_id'):
                                            prompt_id = existing['prompt_id']
                                            self.logger.debug(f"Found PNG with similar timestamp for {media_file.name}, linking to prompt {prompt_id}")
                                            break
                                except:
                                    continue

                        # Store media file info with or without prompt link
                        if self.generated_image_repo:
                            try:
                                # Check if this file is already in the database
                                existing_link = self.generated_image_repo.find_by_path(str(media_file))

                                if not existing_link:
                                    if prompt_id is None:
                                        self.logger.debug(
                                            "Skipping media file %s - unable to determine associated prompt",
                                            media_file.name,
                                        )
                                        continue

                                    # Determine media type based on file extension
                                    file_ext = media_file.suffix.lower()
                                    if file_ext in ['.mp4', '.avi', '.mov', '.webm', '.mkv']:
                                        media_type = 'video'
                                    elif file_ext in ['.wav', '.mp3', '.ogg', '.flac', '.aac', '.m4a']:
                                        media_type = 'audio'
                                    else:
                                        media_type = 'image'  # WebP, JPEG, GIF, etc.

                                    # Add the media file to the generated_images table
                                    image_data = {
                                        'prompt_id': prompt_id,
                                        'image_path': str(media_file),
                                        'filename': media_file.name,
                                        'prompt_metadata': metadata,  # Store basic metadata
                                        'media_type': media_type
                                    }
                                    self.generated_image_repo.create(image_data)
                                    linked_count += 1
                                    if prompt_id:
                                        self.logger.info(f"Linked {media_file.name} to prompt {prompt_id}")
                                    else:
                                        self.logger.debug(f"Added media file {media_file.name} to database (no prompt link)")
                            except Exception as e:
                                self.logger.error(f"Failed to add media file {media_file} to database: {e}")

                except Exception as e:
                    self.logger.error(f"Failed to process {media_file}: {e}", exc_info=True)

                # Calculate progress
                progress = min(95, int((i + 1) / total_files * 95))

                # Send progress updates more frequently for large batches
                # Every 5 files for <1000 files, every 25 for 1000-5000, every 100 for >5000
                update_frequency = 5 if total_files < 1000 else (25 if total_files < 5000 else 100)
                
                # Send progress update at intervals or at the end
                if (i + 1) % update_frequency == 0 or (i + 1) == total_files:
                    # More informative status message
                    status = f'Processing images... ({processed_count}/{total_files}) • {found_count} prompts • {linked_count} linked'

                    # Broadcast realtime progress updates
                    if hasattr(self.api, 'realtime'):
                        await self.api.realtime.send_progress('scan', progress, status)

                    yield {
                        'type': 'progress',
                        'progress': progress,
                        'status': status,
                        'processed': processed_count,
                        'found': found_count,
                        'added': added_count,
                        'linked': linked_count
                    }

                # Small delay to prevent blocking
                if i % 50 == 0:
                    await asyncio.sleep(0.01)

            # Final status update before completion
            final_status = f'Finalizing... Processed {processed_count} files, found {found_count} prompts, linked {linked_count} images'
            if hasattr(self.api, 'realtime'):
                await self.api.realtime.send_progress('scan', 98, final_status)
            
            yield {
                'type': 'progress',
                'progress': 98,
                'status': final_status,
                'processed': processed_count,
                'found': found_count,
                'added': added_count,
                'linked': linked_count
            }

            # Send completion
            if hasattr(self.api, 'realtime'):
                await self.api.realtime.send_progress('scan', 100, 'Scan complete!')
                msg = f'Scan complete! Found {found_count} prompts, added {added_count} new prompts'
                await self.api.realtime.send_toast(msg, 'success')

                # Notify gallery of updates if images were added
                if added_count > 0 or linked_count > 0:
                    await self.api.realtime.notify_image_added({'count': linked_count, 'source': 'scan'})

            yield {
                'type': 'complete',
                'progress': 100,
                'processed': processed_count,
                'found': found_count,
                'added': added_count,
                'linked': linked_count
            }

        except Exception as e:
            self.logger.error(f"Scan error: {e}")
            if hasattr(self.api, 'realtime'):
                await self.api.realtime.send_toast(f'Scan failed: {str(e)}', 'error')
            yield {'type': 'error', 'message': f'Scan failed: {str(e)}'}


async def create_scan_response(api_instance):
    """Create an async generator for the scan response"""
    scanner = ImageScanner(api_instance)
    async for data in scanner.scan_images_generator():
        # Format as Server-Sent Event
        yield f"data: {json.dumps(data)}\n\n"
