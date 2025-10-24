"""Gallery and image management API handlers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from aiohttp import web
from ...database.connection_helper import get_db_connection

if TYPE_CHECKING:
    from ..routes import PromptManagerAPI


class GalleryHandlers:
    """Handles all gallery and image-related endpoints."""

    def __init__(self, api: PromptManagerAPI):
        """Initialize with API instance for access to repos/services.

        Args:
            api: PromptManagerAPI instance providing access to repositories and services
        """
        self.api = api
        self.image_repo = api.image_repo
        self.generated_image_repo = getattr(api, 'generated_image_repo', None)
        self.prompt_repo = api.prompt_repo
        self.gallery = api.gallery
        self.metadata_extractor = api.metadata_extractor
        self.logger = api.logger

    async def list_gallery_images(self, request: web.Request) -> web.Response:
        """List gallery images with pagination, category filtering, and search.

        GET /api/v1/gallery/images?page=1&limit=50&category=portrait&sort_by=created_at&sort_order=desc&search=sunset
        """
        try:
            # Get parameters with defensive defaults
            page_str = request.query.get("page", "1")
            limit_str = request.query.get("limit", "50")
            category = request.query.get("category")
            search = request.query.get("search", "").strip()
            sort_by = request.query.get("sort_by", "created_at")
            sort_order = request.query.get("sort_order", "desc")

            # Parse with error handling
            try:
                page = int(page_str) if page_str else 1
            except (ValueError, TypeError):
                page = 1

            try:
                limit = int(limit_str) if limit_str else 50
            except (ValueError, TypeError):
                limit = 50

            # Ensure positive values
            page = max(1, page)
            limit = max(1, min(limit, 1000))  # Cap at 1000 for safety
            offset = (page - 1) * limit

            # Map frontend sort_by values to database columns
            sort_column_map = {
                'created_at': 'gi.generation_time',
                'updated_at': 'gi.generation_time',
                'name': 'gi.filename',
                'rating': 'p.rating',
                'file_size': 'gi.file_size'
            }

            # Get the actual column name, default to generation_time
            sort_column = sort_column_map.get(sort_by, 'gi.generation_time')

            # Validate sort order
            sort_order = sort_order.lower()
            if sort_order not in ('asc', 'desc'):
                sort_order = 'desc'

            # Build ORDER BY clause
            order_by_clause = f"{sort_column} {sort_order.upper()}"

            # Query with category filter using JOIN
            import sqlite3
            db_path = self.generated_image_repo.db_path if self.generated_image_repo else self.image_repo.db_path

            with get_db_connection(db_path) as conn:
                conn.row_factory = sqlite3.Row

                # Build query with optional category and search filters
                where_clauses = []
                params = []

                if category and category != 'all':
                    where_clauses.append("p.category = ?")
                    params.append(category)

                if search:
                    # Search in filename, positive_prompt, and tags
                    search_pattern = f"%{search}%"
                    where_clauses.append("(gi.filename LIKE ? OR p.positive_prompt LIKE ? OR p.tags LIKE ?)")
                    params.extend([search_pattern, search_pattern, search_pattern])

                where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

                # Get total count
                count_query = f"""
                    SELECT COUNT(*) as total
                    FROM generated_images gi
                    LEFT JOIN prompts p ON gi.prompt_id = p.id
                    {where_clause}
                """
                cursor = conn.execute(count_query, params)
                total = cursor.fetchone()['total']

                # Get paginated results
                data_query = f"""
                    SELECT
                        gi.*,
                        p.category,
                        p.positive_prompt,
                        p.tags,
                        p.rating
                    FROM generated_images gi
                    LEFT JOIN prompts p ON gi.prompt_id = p.id
                    {where_clause}
                    ORDER BY {order_by_clause}
                    LIMIT ? OFFSET ?
                """
                cursor = conn.execute(data_query, params + [limit, offset])
                images = [dict(row) for row in cursor.fetchall()]

            # Apply field mapping for frontend compatibility
            # Frontend expects thumbnail_url and image_url (V1 API format)
            for image in images:
                image_id = image.get('id')

                # Map thumbnail fields to thumbnail_url
                if image_id:
                    # Prefer database thumbnail paths to avoid hash collisions
                    # Check for thumbnail paths in priority order: medium > small > large
                    if image.get('thumbnail_medium_path'):
                        image['thumbnail_url'] = f"/api/v1/thumbnails/{image_id}/medium"
                    elif image.get('thumbnail_small_path'):
                        image['thumbnail_url'] = f"/api/v1/thumbnails/{image_id}/small"
                    elif image.get('thumbnail_large_path'):
                        image['thumbnail_url'] = f"/api/v1/thumbnails/{image_id}/large"
                    else:
                        # Fallback to hash-based lookup if no thumbnail paths in database
                        image['thumbnail_url'] = f"/api/v1/generated-images/{image_id}/file?thumbnail=1"

                    image['image_url'] = f"/api/v1/generated-images/{image_id}/file"
                    image['url'] = image['image_url']  # Alias for compatibility

                # Keep original fields for reference but prioritize mapped URLs
                # This ensures backward compatibility with both V1 and V2 consumers

            return web.json_response({
                "success": True,
                "data": images,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "total_pages": (total + limit - 1) // limit
                }
            })

        except ValueError as e:
            return web.json_response(
                {"error": f"Invalid parameter: {e}"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error listing gallery images: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def get_gallery_image(self, request: web.Request) -> web.Response:
        """Get gallery image details.

        GET /api/v1/gallery/images/{id}
        """
        try:
            image_id = request.match_info["id"]
            image = self.image_repo.read(image_id)

            if not image:
                return web.json_response(
                    {"error": "Image not found"},
                    status=404
                )

            # Add associated prompt if available
            if image.get("prompt_id"):
                image["prompt"] = self.api._format_prompt(
                    self.prompt_repo.read(image["prompt_id"])
                )

            return web.json_response({
                "success": True,
                "data": image
            })

        except Exception as e:
            self.logger.error(f"Error getting gallery image: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def get_gallery_image_file(self, request: web.Request) -> web.StreamResponse:
        """Stream an image or thumbnail file to the browser.

        GET /api/v1/gallery/images/{id}/file?thumbnail=1
        """
        try:
            image_id = int(request.match_info["id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "Invalid image id"}, status=400)

        record = self.image_repo.read(image_id)
        if not record:
            return web.json_response({"error": "Image not found"}, status=404)

        use_thumbnail = request.query.get("thumbnail") is not None
        candidate_path: Optional[str]

        if use_thumbnail:
            candidate_path = record.get("thumbnail_path") or record.get("thumbnail_small_path")
            if candidate_path:
                thumb_path = Path(candidate_path).expanduser()

                # Security validation for thumbnail path
                is_valid, error_msg = self.api._validate_image_path(thumb_path)
                if not is_valid:
                    return web.json_response({"error": error_msg}, status=403)

                if not thumb_path.exists():
                    candidate_path = None
                else:
                    return web.FileResponse(thumb_path)
        else:
            candidate_path = None

        # Fallback to the original asset when thumbnail is missing or requested directly
        if not candidate_path:
            candidate_path = record.get("file_path") or record.get("image_path")

        if not candidate_path:
            return web.json_response({"error": "Image path unavailable"}, status=404)

        path = Path(candidate_path).expanduser()

        # Security validation for original image path
        is_valid, error_msg = self.api._validate_image_path(path)
        if not is_valid:
            return web.json_response({"error": error_msg}, status=403)

        if not path.exists():
            return web.json_response({"error": "Image file not found"}, status=404)

        return web.FileResponse(path)

    async def get_generated_image_file(self, request: web.Request) -> web.StreamResponse:
        """Stream generated image file with security checks.

        GET /api/v1/generated-images/{image_id}/file?thumbnail=1
        """
        if not self.generated_image_repo:
            return web.json_response({"error": "Generated image repository unavailable"}, status=500)

        try:
            image_id = int(request.match_info["image_id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "Invalid image id"}, status=400)

        record = self.generated_image_repo.read(image_id)
        if not record:
            return web.json_response({"error": "Image not found"}, status=404)

        image_path = record.get("image_path") or record.get("file_path")
        if not image_path:
            return web.json_response({"error": "Image path unavailable"}, status=404)

        # Handle thumbnail request
        if request.query.get("thumbnail"):
            # Get thumbnail size (default to medium)
            size = request.query.get("size", "medium")
            if size not in ["small", "medium", "large", "xlarge"]:
                size = "medium"

            # Generate thumbnail path using the same logic as thumbnail service
            # Thumbnails are stored as: {thumbnail_dir}/{size}/{md5_hash}_{size}.ext
            import hashlib
            from ...config import config

            source_path = Path(image_path)
            thumbnail_dir = Path(config.storage.base_path) / config.storage.thumbnails_path

            # Generate MD5 hash of full source path (matching thumbnail service logic)
            path_hash = hashlib.md5(str(source_path).encode()).hexdigest()

            # Determine extension (thumbnails are typically .jpg)
            ext = source_path.suffix.lower()
            if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                ext = '.jpg'

            # Build thumbnail filename: {hash}_{size}.ext
            thumbnail_filename = f"{path_hash}_{size}{ext}"
            thumbnail_path = thumbnail_dir / size / thumbnail_filename

            # Check if thumbnail exists
            if thumbnail_path.exists():
                path = str(thumbnail_path)
            else:
                # Fallback to full image if thumbnail doesn't exist
                path = image_path
        else:
            path = image_path

        candidate = Path(path).expanduser()

        # Security validation: use centralized path validation
        is_valid, error_msg = self.api._validate_image_path(candidate)
        if not is_valid:
            return web.json_response({"error": error_msg}, status=403)

        # Path is validated, serve the file
        resolved = candidate.resolve(strict=True)

        # Create response with proper caching headers
        response = web.FileResponse(resolved)

        # For thumbnails, add cache control headers
        if request.query.get("thumbnail"):
            # Cache thumbnails for 1 hour, but allow revalidation
            response.headers['Cache-Control'] = 'public, max-age=3600, must-revalidate'
        else:
            # Cache full images for longer (1 day)
            response.headers['Cache-Control'] = 'public, max-age=86400'

        return response

    async def get_generated_image_metadata(self, request: web.Request) -> web.Response:
        """Return extracted metadata for a generated image.

        GET /api/v1/generated-images/{image_id}/metadata
        """
        if not self.generated_image_repo:
            return web.json_response({"error": "Generated image repository unavailable"}, status=500)

        try:
            image_id = int(request.match_info["image_id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "Invalid image id"}, status=400)

        record = self.generated_image_repo.read(image_id)
        if not record:
            return web.json_response({"error": "Image not found"}, status=404)

        metadata_raw = record.get("metadata")
        prompt_metadata_raw = record.get("prompt_metadata")
        workflow_raw = record.get("workflow_data") or record.get("workflow")

        def _parse_json(value: Any) -> Optional[Dict[str, Any]]:
            if value is None:
                return None
            if isinstance(value, dict):
                return value
            if isinstance(value, str) and value.strip():
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    self.logger.debug("Generated image %s metadata field was not valid JSON", image_id)
                    return None
            return None

        metadata = _parse_json(metadata_raw)
        prompt_metadata = _parse_json(prompt_metadata_raw)
        workflow = _parse_json(workflow_raw)

        # Prefer prompt_metadata when general metadata missing
        if not metadata and prompt_metadata:
            metadata = prompt_metadata

        # Fallback to extracting directly from file when needed
        if not metadata:
            file_path = record.get("file_path") or record.get("image_path")
            if file_path and Path(file_path).expanduser().exists():
                try:
                    metadata = self.metadata_extractor.extract_from_file(file_path)
                except Exception as exc:  # pragma: no cover - defensive logging
                    self.logger.debug("Failed to extract metadata for image %s: %s", image_id, exc)

        metadata = self.api._sanitize_for_json(metadata) if metadata else {}
        prompt_metadata = self.api._sanitize_for_json(prompt_metadata) if prompt_metadata else None
        workflow = self.api._sanitize_for_json(workflow) if workflow else None

        response_payload: Dict[str, Any] = {
            "id": image_id,
            "prompt_id": record.get("prompt_id"),
            "metadata": metadata,
            "prompt_metadata": prompt_metadata,
            "workflow": workflow,
            "file_path": record.get("file_path") or record.get("image_path"),
            "positive_prompt": record.get("prompt_text") or metadata.get("positive_prompt") if isinstance(metadata, dict) else None,
            "negative_prompt": record.get("negative_prompt") or metadata.get("negative_prompt") if isinstance(metadata, dict) else None,
            "model": record.get("checkpoint") or metadata.get("model") if isinstance(metadata, dict) else None,
            "sampler": record.get("sampler") or metadata.get("sampler") if isinstance(metadata, dict) else None,
            "steps": record.get("steps") or metadata.get("steps") if isinstance(metadata, dict) else None,
            "cfg_scale": record.get("cfg_scale") or metadata.get("cfg_scale") if isinstance(metadata, dict) else None,
            "seed": record.get("seed") or metadata.get("seed") if isinstance(metadata, dict) else None,
            "comfy_parsed": metadata.get("comfy_parsed") if isinstance(metadata, dict) else None,
        }

        # Ensure nested structures are JSON-safe
        response_payload = self.api._sanitize_for_json(response_payload)

        return web.json_response({"success": True, "data": response_payload})

    async def scan_for_images(self, request: web.Request) -> web.Response:
        """Scan directory for new images.

        POST /api/v1/gallery/scan
        Body: {directory: str}
        """
        try:
            data = await request.json()
            directory = data.get("directory", "")

            if not directory or not os.path.exists(directory):
                return web.json_response(
                    {"error": "Invalid directory"},
                    status=400
                )

            # Scan for images and add to database
            new_images = self.gallery.scan_directory(directory)

            for image_data in new_images:
                self.image_repo.create(image_data)

            return web.json_response({
                "success": True,
                "found": len(new_images),
                "message": f"Found {len(new_images)} new images"
            })

        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            self.logger.error(f"Error scanning for images: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def scan_comfyui_images(self, request: web.Request) -> web.Response:
        """Scan ComfyUI output directory for images with prompts.

        POST /api/scan
        Returns: JSON with scan results
        """
        try:
            # Import here to avoid circular imports
            from ...services.image_scanner import ImageScanner

            # Create scanner instance
            scanner = ImageScanner(self.api)

            # Track scan results
            files_scanned = 0
            prompts_found = 0
            prompts_added = 0
            images_linked = 0

            # Process scan results and send WebSocket progress updates
            async for data in scanner.scan_images_generator():
                if data['type'] == 'progress':
                    # Send WebSocket progress update
                    await self.api.realtime.send_progress(
                        operation='scan',
                        progress=data.get('progress', 0),
                        message=data.get('status', 'Scanning...'),  # Scanner uses 'status' field
                        stats={
                            'processed': data.get('processed', 0),
                            'found': data.get('found', 0),
                        }
                    )
                    files_scanned = data.get('processed', 0)
                    prompts_found = data.get('found', 0)
                elif data['type'] == 'complete':
                    files_scanned = data.get('processed', 0)
                    prompts_found = data.get('found', 0)
                    prompts_added = data.get('added', 0)
                    images_linked = data.get('linked', 0)
                    
                    # Send final progress update
                    await self.api.realtime.send_progress(
                        operation='scan',
                        progress=100,
                        message='Scan complete!',
                        stats={
                            'processed': files_scanned,
                            'found': prompts_found,
                            'added': prompts_added,
                            'linked': images_linked,
                        }
                    )
                    break
                elif data['type'] == 'error':
                    return web.json_response({
                        "success": False,
                        "message": data.get('message', 'Scan failed')
                    }, status=500)

            return web.json_response({
                "success": True,
                "files_scanned": files_scanned,
                "prompts_found": prompts_found,
                "prompts_added": prompts_added,
                "images_linked": images_linked,
                "message": f"Scan complete! Found {prompts_found} prompts, added {prompts_added} new ones"
            })

        except Exception as e:
            self.logger.error(f"Error in scan_comfyui_images: {e}")
            return web.json_response({
                "success": False,
                "message": str(e)
            }, status=500)

    async def scan_comfyui_images_stream(self, request: web.Request) -> web.StreamResponse:
        """Stream scan progress using Server-Sent Events.

        GET /api/scan/stream
        Returns: text/event-stream with progress updates
        """
        import json
        from ...services.image_scanner import ImageScanner

        response = web.StreamResponse()
        response.headers['Content-Type'] = 'text/event-stream'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'

        await response.prepare(request)

        try:
            # Create scanner instance
            scanner = ImageScanner(self.api)

            # Stream scan progress
            async for data in scanner.scan_images_generator():
                # Format as Server-Sent Event
                event_data = f"data: {json.dumps(data)}\n\n"
                await response.write(event_data.encode('utf-8'))

                # Break if complete or error
                if data['type'] in ['complete', 'error']:
                    break

        except Exception as e:
            self.logger.error(f"Error in scan stream: {e}")
            error_data = {
                'type': 'error',
                'message': str(e)
            }
            await response.write(f"data: {json.dumps(error_data)}\n\n".encode('utf-8'))

        finally:
            await response.write_eof()

        return response