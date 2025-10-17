"""Metadata extraction and image metadata API handlers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from ..routes import PromptManagerAPI


class MetadataHandlers:
    """Handles all metadata-related endpoints."""

    def __init__(self, api: PromptManagerAPI):
        """Initialize with API instance for access to repos/services.

        Args:
            api: PromptManagerAPI instance providing access to repositories and services
        """
        self.api = api
        self.image_repo = api.image_repo
        self.metadata_extractor = api.metadata_extractor
        self.logger = api.logger

    async def get_image_metadata(self, request: web.Request) -> web.Response:
        """Get metadata for image file.

        GET /api/v1/metadata/{filename}
        """
        try:
            filename = request.match_info["filename"]

            # Find image record
            image = self.image_repo.find_by_filename(filename)

            if not image:
                return web.json_response(
                    {"error": "Image not found"},
                    status=404
                )

            # Extract metadata from file
            metadata = self.metadata_extractor.extract_from_file(image["image_path"])

            return web.json_response(
                {
                    "success": True,
                    "data": metadata
                },
                dumps=lambda obj: json.dumps(obj, allow_nan=False)
            )

        except FileNotFoundError:
            return web.json_response(
                {"error": "Image file not found"},
                status=404
            )
        except Exception as e:
            self.logger.error(f"Error getting metadata: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def extract_metadata(self, request: web.Request) -> web.Response:
        """Extract metadata from uploaded file.

        POST /api/v1/metadata/extract
        Body: multipart/form-data with 'file' field
        """
        try:
            self.logger.info("[metadata.extract] Incoming request from %s", request.remote)

            reader = await request.multipart()
            field = await reader.next()

            # Walk the multipart payload until we find the uploaded file
            while field is not None and field.name != "file":
                self.logger.debug("[metadata.extract] Skipping field '%s'", field.name)
                field = await reader.next()

            if field is None:
                self.logger.warning("[metadata.extract] No 'file' part found in multipart payload")
                return web.json_response(
                    {"error": "File field required"},
                    status=400
                )

            # Read file data
            data = await field.read()

            # Extract metadata
            metadata = self.metadata_extractor.extract_from_bytes(data)
            metadata = self.api._sanitize_for_json(metadata)

            self.logger.info("[metadata.extract] Metadata extracted successfully (%s bytes)", len(data))

            return web.json_response({
                "success": True,
                "data": metadata
            })

        except Exception as e:
            self.logger.exception("[metadata.extract] Error extracting metadata")
            return web.json_response(
                {"error": str(e)},
                status=500
            )
