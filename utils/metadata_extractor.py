"""ComfyUI metadata extraction utilities.

This module provides comprehensive metadata extraction capabilities for ComfyUI-generated
images. It can extract workflow information, prompt data, and generation parameters from
PNG images that contain embedded ComfyUI metadata in their text chunks.

The extractor supports:
- Complete workflow data extraction from PNG text chunks
- Text encoder node identification and analysis
- Generation parameter extraction (steps, cfg_scale, sampler, etc.)
- Basic file information fallback when metadata is unavailable
- Flexible node type detection for various ComfyUI extensions

Typical usage:
    from utils.metadata_extractor import ComfyUIMetadataExtractor

    extractor = ComfyUIMetadataExtractor()
    metadata = extractor.extract_metadata('/path/to/image.png')

    if metadata:
        workflow = metadata.get('workflow', {})
        text_nodes = metadata.get('text_encoder_nodes', [])
        params = extractor.get_generation_parameters(metadata)

The extracted metadata includes:
- file_info: Basic file stats (size, dimensions, format, timestamps)
- workflow: Complete ComfyUI workflow data structure
- text_encoder_nodes: List of identified text encoding nodes
- prompt: ComfyUI prompt execution data
- Generation parameters: steps, cfg_scale, sampler, seed, etc.
"""

import os
import json
from typing import Optional, Dict, Any
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from .logging_config import get_logger


class ComfyUIMetadataExtractor:
    """Extracts ComfyUI metadata from generated images.

    This class handles extraction of ComfyUI workflow and generation metadata
    from PNG images. It parses the text chunks embedded by ComfyUI and extracts
    structured information about workflows, prompts, and generation parameters.

    The extractor is designed to be robust and handle various ComfyUI workflow
    formats, including custom nodes and different metadata structures.
    """

    def __init__(self):
        """Initialize the metadata extractor.

        Sets up logging and prepares the extractor for metadata parsing operations.
        """
        self.logger = get_logger("prompt_manager.metadata_extractor")

    def extract_metadata(self, image_path: str) -> Optional[Dict[str, Any]]:
        """
        Extract ComfyUI workflow and prompt metadata from an image.

        This method opens a PNG image and extracts all available ComfyUI metadata
        from the embedded text chunks. It handles JSON parsing, workflow analysis,
        and parameter extraction.

        Args:
            image_path: Path to the PNG image file to analyze

        Returns:
            Dictionary containing extracted metadata with keys:
            - file_info: Basic file information (always present)
            - workflow: Parsed workflow data (if available)
            - text_encoder_nodes: List of text encoding nodes (if found)
            - prompt: ComfyUI prompt data (if available)
            - Additional fields: parameters, model, sampler, steps, etc. (if present)
            Returns None if no ComfyUI metadata is found or extraction fails

        Raises:
            Exception: Re-raises any exception that occurs during extraction,
                      with appropriate error logging
        """
        try:
            with Image.open(image_path) as image:
                metadata = {}

                # Add basic file information
                metadata["file_info"] = self.get_file_info(image_path, image)

                # Extract ComfyUI-specific metadata from PNG text chunks
                if hasattr(image, "text") and image.text:
                    # Look for ComfyUI workflow data
                    if "workflow" in image.text:
                        try:
                            workflow_data = json.loads(image.text["workflow"])
                            metadata["workflow"] = workflow_data

                            # Extract text encoder nodes from workflow
                            text_encoder_nodes = self.find_text_encoder_nodes(
                                workflow_data
                            )
                            if text_encoder_nodes:
                                metadata["text_encoder_nodes"] = text_encoder_nodes

                        except json.JSONDecodeError as e:
                            self.logger.warning(f"Failed to parse workflow JSON: {e}")

                    # Look for prompt data
                    if "prompt" in image.text:
                        try:
                            prompt_data = json.loads(image.text["prompt"])
                            metadata["prompt"] = prompt_data
                        except json.JSONDecodeError as e:
                            self.logger.warning(f"Failed to parse prompt JSON: {e}")

                    # Extract other common metadata fields
                    metadata_fields = [
                        "parameters",
                        "model",
                        "sampler",
                        "steps",
                        "cfg_scale",
                        "seed",
                        "scheduler",
                        "positive",
                        "negative",
                    ]

                    for field in metadata_fields:
                        if field in image.text:
                            try:
                                # Try to parse as JSON first
                                metadata[field] = json.loads(image.text[field])
                            except (json.JSONDecodeError, TypeError):
                                # Store as string if not valid JSON
                                metadata[field] = image.text[field]

                return (
                    metadata
                    if any(key != "file_info" for key in metadata.keys())
                    else None
                )

        except Exception as e:
            self.logger.error(f"Error extracting metadata from {image_path}: {e}")
            return None

    def get_file_info(self, image_path: str, image: Image.Image) -> Dict[str, Any]:
        """
        Get basic file information.

        Extracts fundamental file properties including size, dimensions, format,
        and filesystem timestamps.

        Args:
            image_path: Path to the image file
            image: Opened PIL Image object

        Returns:
            Dictionary containing:
            - size: File size in bytes
            - dimensions: Image dimensions as [width, height]
            - format: Image format (PNG, JPEG, etc.)
            - mode: Color mode (RGB, RGBA, etc.)
            - created_time: File creation timestamp
            - modified_time: File modification timestamp
        """
        try:
            stat = os.stat(image_path)
            return {
                "size": stat.st_size,
                "dimensions": list(image.size),
                "format": image.format,
                "mode": image.mode,
                "created_time": stat.st_ctime,
                "modified_time": stat.st_mtime,
            }
        except Exception as e:
            self.logger.error(f"Error getting file info: {e}")
            return {}

    def find_text_encoder_nodes(self, workflow_data: Dict) -> list:
        """
        Find text encoder nodes in the workflow data.

        Searches through the workflow structure to identify nodes that perform
        text encoding operations. This includes standard ComfyUI nodes like
        CLIPTextEncode as well as custom nodes and extensions.

        Args:
            workflow_data: ComfyUI workflow data structure (can be in various formats)

        Returns:
            List of dictionaries representing text encoder nodes, each containing
            the node's configuration, inputs, and metadata. For dictionary-based
            workflows, adds 'node_id' field to each node.
        """
        text_encoder_nodes = []

        if not isinstance(workflow_data, dict):
            return text_encoder_nodes

        # Check different possible workflow structures
        nodes_data = None

        # Try different keys where nodes might be stored
        if "nodes" in workflow_data:
            nodes_data = workflow_data["nodes"]
        elif "workflow" in workflow_data and "nodes" in workflow_data["workflow"]:
            nodes_data = workflow_data["workflow"]["nodes"]
        elif isinstance(workflow_data, dict):
            # Sometimes the workflow data is just a flat dict of node IDs
            nodes_data = workflow_data

        if not nodes_data:
            return text_encoder_nodes

        # Handle different node data structures
        if isinstance(nodes_data, list):
            # Nodes as a list
            for node in nodes_data:
                if self.is_text_encoder_node(node):
                    text_encoder_nodes.append(node)
        elif isinstance(nodes_data, dict):
            # Nodes as a dictionary (node_id -> node_data)
            for node_id, node_data in nodes_data.items():
                if self.is_text_encoder_node(node_data):
                    node_data["node_id"] = node_id
                    text_encoder_nodes.append(node_data)

        return text_encoder_nodes

    def is_text_encoder_node(self, node_data: Any) -> bool:
        """
        Check if a node is a text encoder node.

        Determines whether a given node performs text encoding by examining
        its type, class, and title for known text encoding patterns. Supports
        various ComfyUI node types and custom extensions.

        Args:
            node_data: Node configuration dictionary to analyze

        Returns:
            True if the node is identified as a text encoder, False otherwise
        """
        if not isinstance(node_data, dict):
            return False

        # Check for common text encoder node types
        text_encoder_types = [
            "CLIPTextEncode",
            "CLIPTextEncodeSDXL",
            "CLIPTextEncodeSDXLRefiner",
            "PromptManager",  # Our custom node
            "BNK_CLIPTextEncoder",
            "Text Encoder",
            "CLIP Text Encode",
        ]

        # Check node type/class_type
        node_type = node_data.get("type") or node_data.get("class_type") or ""

        for encoder_type in text_encoder_types:
            if encoder_type.lower() in node_type.lower():
                return True

        # Check node title/name for text encoding keywords
        node_title = (node_data.get("title") or node_data.get("name") or "").lower()
        text_keywords = ["text", "prompt", "encode", "clip"]

        if any(keyword in node_title for keyword in text_keywords):
            return True

        return False

    def extract_prompt_text_from_workflow(self, workflow_data: Dict) -> Optional[str]:
        """
        Extract the actual prompt text from workflow data.

        Searches through text encoder nodes to find and extract the actual
        prompt text that was used for generation. Handles various input field
        names and data structures.

        Args:
            workflow_data: ComfyUI workflow data structure

        Returns:
            The extracted prompt text string, or None if no prompt text is found
        """
        text_encoder_nodes = self.find_text_encoder_nodes(workflow_data)

        for node in text_encoder_nodes:
            # Try different ways to get the prompt text
            inputs = node.get("inputs", {})

            # Common input field names for prompt text
            text_fields = ["text", "prompt", "positive", "conditioning"]

            for field in text_fields:
                if field in inputs and inputs[field]:
                    if isinstance(inputs[field], str):
                        return inputs[field]
                    elif isinstance(inputs[field], list) and inputs[field]:
                        return str(inputs[field][0])

        return None

    def get_generation_parameters(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract generation parameters from metadata.

        Parses the metadata to extract key generation parameters used for
        image creation, including sampling settings, model information,
        and other configuration values.

        Args:
            metadata: Full metadata dictionary from extract_metadata()

        Returns:
            Dictionary containing generation parameters such as:
            - steps: Number of sampling steps
            - cfg_scale: Classifier-free guidance scale
            - sampler: Sampling method used
            - scheduler: Noise scheduler
            - seed: Random seed value
            - model: Model name/path
            - width/height: Image dimensions
            - batch_size: Number of images generated
        """
        parameters = {}

        # Common generation parameters to extract
        param_fields = [
            "steps",
            "cfg_scale",
            "sampler",
            "scheduler",
            "seed",
            "model",
            "width",
            "height",
            "batch_size",
        ]

        for field in param_fields:
            if field in metadata:
                parameters[field] = metadata[field]

        # Extract from workflow if available
        if "workflow" in metadata:
            workflow_params = self.extract_params_from_workflow(metadata["workflow"])
            parameters.update(workflow_params)

        return parameters

    def extract_params_from_workflow(self, workflow_data: Dict) -> Dict[str, Any]:
        """
        Extract generation parameters from workflow data.

        Analyzes the workflow structure to identify and extract generation
        parameters from various node types. This method can be extended
        to support specific workflow patterns and custom nodes.

        Args:
            workflow_data: ComfyUI workflow data structure

        Returns:
            Dictionary of extracted parameters. Currently returns empty dict
            but can be extended based on specific workflow analysis needs.
        """
        parameters = {}

        # This would need to be customized based on your specific workflow structure
        # For now, return empty dict - can be expanded based on specific needs

        return parameters
