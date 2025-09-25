"""Metadata extraction from PNG images and ComfyUI workflows.

Extracts generation parameters, workflow data, and prompt information
from PNG metadata chunks and ComfyUI output files.
"""

import json
import struct
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from PIL import Image

from src.core.base import BaseMetadataViewer


class MetadataExtractor(BaseMetadataViewer):
    """Extract and parse metadata from generated images.
    
    Handles PNG tEXt/iTXt chunks, ComfyUI workflow data,
    and various metadata formats from different generation tools.
    """

    def __init__(self):
        """Initialize metadata extractor."""
        super().__init__("png_extractor")
        
        # Known metadata keys from various tools
        self.known_keys = {
            # ComfyUI keys
            "workflow": "ComfyUI workflow data",
            "prompt": "Generation prompt",
            "negative_prompt": "Negative prompt",
            
            # Automatic1111/Stable Diffusion WebUI
            "parameters": "Generation parameters",
            
            # Common parameters
            "steps": "Sampling steps",
            "sampler": "Sampling method",
            "cfg_scale": "CFG scale",
            "seed": "Random seed",
            "size": "Image dimensions",
            "model_hash": "Model hash",
            "model": "Model name",
            "vae": "VAE model",
            "clip_skip": "CLIP skip layers",
            "denoising_strength": "Denoising strength",
            
            # SDXL specific
            "base_model": "Base model",
            "refiner_model": "Refiner model",
            "refiner_switch_at": "Refiner switch point",
            
            # ControlNet
            "controlnet": "ControlNet settings",
            "control_guidance": "Control guidance strength",
            
            # Upscaling
            "upscaler": "Upscaler model",
            "upscale_factor": "Upscale factor",
            
            # Other
            "creation_time": "Creation timestamp",
            "generation_time": "Generation duration",
            "software": "Generation software"
        }

    def parse_metadata(self, source: Any) -> Dict[str, Any]:
        """Parse metadata from source.
        
        Args:
            source: File path, bytes, or PIL Image
            
        Returns:
            Parsed metadata dictionary
        """
        if isinstance(source, (str, Path)):
            return self.extract_from_file(str(source))
        elif isinstance(source, bytes):
            return self.extract_from_bytes(source)
        elif isinstance(source, Image.Image):
            return self.extract_from_image(source)
        else:
            raise ValueError(f"Unsupported source type: {type(source)}")

    def format_for_display(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Format metadata for UI display.
        
        Args:
            metadata: Raw metadata
            
        Returns:
            Formatted metadata for frontend
        """
        formatted = {
            "sections": [],
            "raw": metadata
        }
        
        # Main generation parameters section
        main_params = self._extract_main_parameters(metadata)
        if main_params:
            formatted["sections"].append({
                "title": "Generation Parameters",
                "type": "parameters",
                "items": main_params
            })
        
        # Prompt section
        prompt_data = self._extract_prompts(metadata)
        if prompt_data:
            formatted["sections"].append({
                "title": "Prompts",
                "type": "prompts",
                "items": prompt_data
            })
        
        # Model information
        model_info = self._extract_model_info(metadata)
        if model_info:
            formatted["sections"].append({
                "title": "Model Information",
                "type": "model",
                "items": model_info
            })
        
        # Workflow data (if ComfyUI)
        if "workflow" in metadata:
            formatted["sections"].append({
                "title": "ComfyUI Workflow",
                "type": "workflow",
                "collapsible": True,
                "collapsed": True,
                "content": metadata["workflow"]
            })
        
        # Additional metadata
        additional = self._extract_additional(metadata)
        if additional:
            formatted["sections"].append({
                "title": "Additional Information",
                "type": "additional",
                "items": additional
            })
        
        return formatted

    def extract_from_file(self, file_path: str) -> Dict[str, Any]:
        """Extract metadata from image file.
        
        Args:
            file_path: Path to image file
            
        Returns:
            Extracted metadata
        """
        try:
            with Image.open(file_path) as img:
                return self.extract_from_image(img)
        except Exception as e:
            self.logger.error(f"Error extracting metadata from {file_path}: {e}")
            return {}

    def extract_from_bytes(self, data: bytes) -> Dict[str, Any]:
        """Extract metadata from image bytes.
        
        Args:
            data: Image file bytes
            
        Returns:
            Extracted metadata
        """
        try:
            with Image.open(BytesIO(data)) as img:
                return self.extract_from_image(img)
        except Exception as e:
            self.logger.error(f"Error extracting metadata from bytes: {e}")
            return {}

    def extract_from_image(self, img: Image.Image) -> Dict[str, Any]:
        """Extract metadata from PIL Image.
        
        Args:
            img: PIL Image object
            
        Returns:
            Extracted metadata
        """
        metadata = {}
        
        # Extract from PIL info
        if hasattr(img, "info"):
            metadata.update(img.info)
        
        # Extract PNG chunks if PNG format
        if img.format == "PNG":
            png_metadata = self._extract_png_chunks(img)
            metadata.update(png_metadata)
        
        # Parse specific formats
        metadata = self._parse_metadata_formats(metadata)
        
        # Clean and validate
        metadata = self.validate_metadata(metadata)
        
        return metadata

    def _extract_png_chunks(self, img: Image.Image) -> Dict[str, Any]:
        """Extract metadata from PNG chunks.
        
        Args:
            img: PIL Image in PNG format
            
        Returns:
            Metadata from PNG chunks
        """
        metadata = {}
        
        # Get PNG info if available
        if hasattr(img, "png") and hasattr(img.png, "chunks"):
            for chunk_type, chunk_data in img.png.chunks:
                if chunk_type in [b"tEXt", b"iTXt"]:
                    try:
                        # Decode text chunk
                        if chunk_type == b"tEXt":
                            key, value = chunk_data.split(b"\x00", 1)
                            metadata[key.decode("latin-1")] = value.decode("latin-1")
                        elif chunk_type == b"iTXt":
                            # iTXt is more complex, simplified handling
                            parts = chunk_data.split(b"\x00")
                            if len(parts) >= 2:
                                key = parts[0].decode("latin-1")
                                # Skip compression flags and language tag
                                value = b"\x00".join(parts[2:]).decode("utf-8", errors="ignore")
                                metadata[key] = value
                    except Exception as e:
                        self.logger.debug(f"Error parsing PNG chunk: {e}")
        
        return metadata

    def _parse_metadata_formats(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Parse known metadata formats.
        
        Args:
            metadata: Raw metadata
            
        Returns:
            Parsed metadata
        """
        parsed = metadata.copy()
        
        # Parse Automatic1111 parameters format
        if "parameters" in metadata:
            params = self._parse_a1111_parameters(metadata["parameters"])
            parsed.update(params)
        
        # Parse ComfyUI workflow
        if "workflow" in metadata:
            if isinstance(metadata["workflow"], str):
                try:
                    parsed["workflow"] = json.loads(metadata["workflow"])
                except json.JSONDecodeError:
                    pass
        
        # Parse prompt if it's JSON
        if "prompt" in metadata and isinstance(metadata["prompt"], str):
            if metadata["prompt"].startswith("{"):
                try:
                    parsed["prompt_data"] = json.loads(metadata["prompt"])
                except json.JSONDecodeError:
                    pass
        
        return parsed

    def _parse_a1111_parameters(self, params_text: str) -> Dict[str, Any]:
        """Parse Automatic1111 parameters format.
        
        Args:
            params_text: Parameters text from A1111
            
        Returns:
            Parsed parameters
        """
        params = {}
        
        if not params_text:
            return params
        
        lines = params_text.strip().split("\n")
        
        # First line is usually the positive prompt
        if lines:
            params["prompt"] = lines[0]
        
        # Look for negative prompt
        for i, line in enumerate(lines):
            if line.startswith("Negative prompt:"):
                params["negative_prompt"] = line[16:].strip()
                
                # Rest might be on next line
                if i + 1 < len(lines) and not ":" in lines[i + 1]:
                    params["negative_prompt"] += " " + lines[i + 1].strip()
        
        # Parse key-value pairs
        for line in lines:
            if ":" in line and not line.startswith("Negative prompt:"):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    key = parts[0].strip().lower().replace(" ", "_")
                    value = parts[1].strip()
                    
                    # Parse specific types
                    if key in ["steps", "seed", "cfg_scale", "clip_skip"]:
                        try:
                            value = float(value) if "." in value else int(value)
                        except ValueError:
                            pass
                    elif key == "size":
                        # Parse size as "widthxheight"
                        if "x" in value:
                            try:
                                w, h = value.split("x")
                                params["width"] = int(w)
                                params["height"] = int(h)
                            except ValueError:
                                pass
                    
                    params[key] = value
        
        return params

    def _extract_main_parameters(self, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract main generation parameters.
        
        Args:
            metadata: Full metadata
            
        Returns:
            List of parameter items for display
        """
        params = []
        
        # Key parameters to extract
        param_keys = [
            ("steps", "Steps"),
            ("sampler", "Sampler"),
            ("cfg_scale", "CFG Scale"),
            ("seed", "Seed"),
            ("width", "Width"),
            ("height", "Height"),
            ("denoising_strength", "Denoising"),
            ("clip_skip", "CLIP Skip")
        ]
        
        for key, label in param_keys:
            if key in metadata and metadata[key] is not None:
                params.append({
                    "label": label,
                    "value": str(metadata[key]),
                    "key": key
                })
        
        # Add size if width/height not separate
        if "size" in metadata and "width" not in metadata:
            params.append({
                "label": "Size",
                "value": metadata["size"],
                "key": "size"
            })
        
        return params

    def _extract_prompts(self, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract prompt information.
        
        Args:
            metadata: Full metadata
            
        Returns:
            List of prompt items for display
        """
        prompts = []
        
        # Positive prompt
        if "prompt" in metadata:
            prompts.append({
                "type": "positive",
                "label": "Prompt",
                "text": metadata["prompt"],
                "truncated": len(metadata["prompt"]) > 500
            })
        
        # Negative prompt
        if "negative_prompt" in metadata:
            prompts.append({
                "type": "negative",
                "label": "Negative Prompt",
                "text": metadata["negative_prompt"],
                "truncated": len(metadata["negative_prompt"]) > 500
            })
        
        return prompts

    def _extract_model_info(self, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract model information.
        
        Args:
            metadata: Full metadata
            
        Returns:
            List of model info items
        """
        model_info = []
        
        # Model name
        if "model" in metadata:
            model_info.append({
                "label": "Model",
                "value": metadata["model"],
                "key": "model"
            })
        elif "base_model" in metadata:
            model_info.append({
                "label": "Base Model",
                "value": metadata["base_model"],
                "key": "base_model"
            })
        
        # Model hash
        if "model_hash" in metadata:
            model_info.append({
                "label": "Model Hash",
                "value": metadata["model_hash"][:10] + "...",
                "full_value": metadata["model_hash"],
                "key": "model_hash"
            })
        
        # VAE
        if "vae" in metadata:
            model_info.append({
                "label": "VAE",
                "value": metadata["vae"],
                "key": "vae"
            })
        
        # Refiner (SDXL)
        if "refiner_model" in metadata:
            model_info.append({
                "label": "Refiner",
                "value": metadata["refiner_model"],
                "key": "refiner_model"
            })
        
        # Upscaler
        if "upscaler" in metadata:
            model_info.append({
                "label": "Upscaler",
                "value": metadata["upscaler"],
                "key": "upscaler"
            })
        
        return model_info

    def _extract_additional(self, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract additional metadata.
        
        Args:
            metadata: Full metadata
            
        Returns:
            List of additional items
        """
        additional = []
        
        # Skip already processed keys
        skip_keys = {
            "prompt", "negative_prompt", "steps", "sampler", "cfg_scale",
            "seed", "width", "height", "size", "model", "model_hash",
            "vae", "base_model", "refiner_model", "upscaler", "workflow",
            "parameters", "denoising_strength", "clip_skip"
        }
        
        # Add remaining keys
        for key, value in metadata.items():
            if key not in skip_keys and value is not None:
                # Format key for display
                label = key.replace("_", " ").title()
                
                # Skip very long values
                if isinstance(value, str) and len(value) > 1000:
                    continue
                
                additional.append({
                    "label": label,
                    "value": str(value),
                    "key": key
                })
        
        return additional