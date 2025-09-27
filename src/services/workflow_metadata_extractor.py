"""
ComfyUI Workflow Metadata Extractor
Extracts generation parameters from ComfyUI workflow execution data
"""

import json
import sqlite3
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

# Import connection helper if available
try:
    from src.database.connection_helper import get_db_connection
    USE_CONNECTION_HELPER = True
except ImportError:
    USE_CONNECTION_HELPER = False


class WorkflowMetadataExtractor:
    """
    Extracts metadata from ComfyUI workflow execution and saves to database.
    Can be used as a service or integrated with ComfyUI's execution pipeline.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def extract_from_workflow(self, workflow_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract generation parameters from ComfyUI workflow data.

        Args:
            workflow_data: ComfyUI workflow JSON data

        Returns:
            Dictionary containing extracted generation parameters
        """
        params = {
            "model": None,
            "positive": None,
            "negative": None,
            "seed": None,
            "steps": None,
            "cfg": None,
            "sampler": None,
            "scheduler": None,
            "width": None,
            "height": None,
            "vae": None,
            "lora": None,
            "lora_strength": None,
        }

        # Navigate through workflow nodes to extract parameters
        if "nodes" in workflow_data:
            for node in workflow_data["nodes"]:
                node_type = node.get("type", "")
                widgets = node.get("widgets_values", [])

                # Extract from CheckpointLoaderSimple
                if "CheckpointLoader" in node_type:
                    if widgets and len(widgets) > 0:
                        params["model"] = widgets[0]

                # Extract from KSampler nodes
                elif "KSampler" in node_type:
                    if len(widgets) >= 7:
                        params["seed"] = widgets[0]
                        params["steps"] = widgets[2]
                        params["cfg"] = widgets[3]
                        params["sampler"] = widgets[4]
                        params["scheduler"] = widgets[5]

                # Extract from CLIPTextEncode nodes
                elif "CLIPTextEncode" in node_type:
                    if widgets and len(widgets) > 0:
                        # Determine if positive or negative based on connections
                        prompt_text = widgets[0]
                        if self._is_positive_prompt(node, workflow_data):
                            params["positive"] = prompt_text
                        else:
                            params["negative"] = prompt_text

                # Extract from EmptyLatentImage
                elif "EmptyLatentImage" in node_type:
                    if len(widgets) >= 3:
                        params["width"] = widgets[0]
                        params["height"] = widgets[1]

                # Extract from VAE nodes
                elif "VAELoader" in node_type:
                    if widgets and len(widgets) > 0:
                        params["vae"] = widgets[0]

                # Extract from LoRA nodes
                elif "LoraLoader" in node_type:
                    if len(widgets) >= 3:
                        params["lora"] = widgets[0]
                        params["lora_strength"] = widgets[1]

        return params

    def _is_positive_prompt(self, node: Dict, workflow_data: Dict) -> bool:
        """
        Determine if a CLIP text node is positive or negative prompt.
        Uses simple heuristics based on node title and connections.
        """
        # Check node title first
        title = node.get("title", "").lower()
        if "positive" in title or "pos" in title:
            return True
        if "negative" in title or "neg" in title:
            return False

        # Check widget values for common negative keywords
        if node.get("widgets_values"):
            text = str(node["widgets_values"][0]).lower()
            negative_indicators = [
                "bad quality", "worst quality", "low quality",
                "blurry", "ugly", "deformed", "mutated",
                "nsfw", "nude", "naked"
            ]
            if any(indicator in text for indicator in negative_indicators):
                return False

        # Default to positive
        return True

    def extract_from_png_metadata(self, png_path: str) -> Dict[str, Any]:
        """
        Extract metadata from PNG file saved by ComfyUI.

        Args:
            png_path: Path to PNG file with embedded metadata

        Returns:
            Dictionary containing extracted parameters
        """
        try:
            from PIL import Image
            from PIL.PngImagePlugin import PngInfo

            image = Image.open(png_path)
            metadata = image.info

            # ComfyUI stores workflow in 'workflow' key
            if "workflow" in metadata:
                workflow_data = json.loads(metadata["workflow"])
                return self.extract_from_workflow(workflow_data)

            # Also check for 'prompt' key (execution data)
            if "prompt" in metadata:
                prompt_data = json.loads(metadata["prompt"])
                return self._extract_from_prompt_data(prompt_data)

        except Exception as e:
            print(f"[MetadataExtractor] Error extracting from PNG: {e}")

        return {}

    def _extract_from_prompt_data(self, prompt_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract parameters from ComfyUI prompt execution data.
        This is different from workflow data - it's the actual execution parameters.
        """
        params = {
            "model": None,
            "positive": None,
            "negative": None,
            "seed": None,
            "steps": None,
            "cfg": None,
            "sampler": None,
            "scheduler": None,
            "width": None,
            "height": None,
        }

        # Navigate through the execution data structure
        for node_id, node_data in prompt_data.items():
            if not isinstance(node_data, dict):
                continue

            class_type = node_data.get("class_type", "")
            inputs = node_data.get("inputs", {})

            # Extract from different node types
            if "CheckpointLoader" in class_type:
                params["model"] = inputs.get("ckpt_name")

            elif "KSampler" in class_type:
                params["seed"] = inputs.get("seed")
                params["steps"] = inputs.get("steps")
                params["cfg"] = inputs.get("cfg")
                params["sampler"] = inputs.get("sampler_name")
                params["scheduler"] = inputs.get("scheduler")

            elif "CLIPTextEncode" in class_type:
                text = inputs.get("text")
                if text:
                    # Simple heuristic for positive vs negative
                    if self._is_likely_negative(text):
                        params["negative"] = text
                    elif not params["positive"]:
                        params["positive"] = text

            elif "EmptyLatentImage" in class_type:
                params["width"] = inputs.get("width")
                params["height"] = inputs.get("height")

        return params

    def _is_likely_negative(self, text: str) -> bool:
        """Check if text is likely a negative prompt."""
        negative_keywords = [
            "bad", "worst", "low quality", "blurry",
            "ugly", "deformed", "mutated", "nsfw"
        ]
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in negative_keywords)

    def save_to_database(self, params: Dict[str, Any],
                        image_path: Optional[str] = None) -> bool:
        """
        Save extracted parameters to PromptManager database.

        Args:
            params: Dictionary of generation parameters
            image_path: Optional path to generated image

        Returns:
            True if saved successfully
        """
        if not params.get("positive"):
            return False  # Need at least positive prompt

        try:
            if USE_CONNECTION_HELPER:
                # Use connection helper with proper retry and WAL handling
                from src.database.connection_helper import execute_query

                # Check if prompt exists
                prompt_hash = hashlib.sha256(
                    f"{params.get('positive', '')}|{params.get('negative', '')}".encode()
                ).hexdigest()

                result = execute_query(
                    self.db_path,
                    "SELECT id FROM prompts WHERE hash = ?",
                    (prompt_hash,),
                    fetch_one=True
                )

                if result:
                    # Update existing - implementation continues below
                    pass
                else:
                    # Insert new - implementation continues below
                    pass

                # Continue with original logic but using helper
                conn = sqlite3.connect(self.db_path, timeout=30.0)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=10000")
                cursor = conn.cursor()
            else:
                # Fallback to original implementation with better settings
                conn = sqlite3.connect(self.db_path, timeout=30.0)

                # Enable WAL mode for better concurrency
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=10000")  # Increased to 10 seconds

                cursor = conn.cursor()

            # Generate hash for prompt
            positive = params.get("positive", "")
            negative = params.get("negative", "")
            prompt_hash = hashlib.sha256(
                f"{positive}|{negative}".encode()
            ).hexdigest()

            # Build generation_params JSON
            generation_params = {
                "model": params.get("model"),
                "seed": params.get("seed"),
                "steps": params.get("steps"),
                "cfg_scale": params.get("cfg"),
                "sampler": params.get("sampler"),
                "scheduler": params.get("scheduler"),
                "width": params.get("width"),
                "height": params.get("height"),
                "vae": params.get("vae"),
                "lora": params.get("lora"),
                "lora_strength": params.get("lora_strength"),
            }

            # Build sampler_settings JSON
            sampler_settings = {
                "sampler": params.get("sampler"),
                "scheduler": params.get("scheduler"),
                "steps": params.get("steps"),
                "cfg": params.get("cfg"),
                "seed": params.get("seed"),
            }

            # Generate model hash
            model_hash = ""
            if params.get("model"):
                model_hash = hashlib.md5(
                    params["model"].encode()
                ).hexdigest()[:16]

            # Check if prompt exists
            cursor.execute(
                "SELECT id FROM prompts WHERE hash = ?",
                (prompt_hash,)
            )
            existing = cursor.fetchone()

            if existing:
                # Update existing prompt
                cursor.execute("""
                    UPDATE prompts SET
                        model_hash = COALESCE(model_hash, ?),
                        sampler_settings = COALESCE(sampler_settings, ?),
                        generation_params = COALESCE(generation_params, ?),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE hash = ?
                """, (
                    model_hash,
                    json.dumps(sampler_settings),
                    json.dumps(generation_params),
                    prompt_hash
                ))
                prompt_id = existing[0]
            else:
                # Insert new prompt
                cursor.execute("""
                    INSERT INTO prompts (
                        positive_prompt, negative_prompt, hash,
                        model_hash, sampler_settings, generation_params,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (
                    positive, negative, prompt_hash,
                    model_hash,
                    json.dumps(sampler_settings),
                    json.dumps(generation_params)
                ))
                prompt_id = cursor.lastrowid

            # If image path provided, update generated_images
            if image_path and prompt_id:
                # Check if image already exists
                filename = Path(image_path).name
                cursor.execute(
                    "SELECT id FROM generated_images WHERE filename = ?",
                    (filename,)
                )
                existing_image = cursor.fetchone()

                if existing_image:
                    # Update existing image
                    cursor.execute("""
                        UPDATE generated_images SET
                            width = COALESCE(width, ?),
                            height = COALESCE(height, ?),
                            parameters = COALESCE(parameters, ?)
                        WHERE filename = ?
                    """, (
                        params.get("width"),
                        params.get("height"),
                        json.dumps(generation_params),
                        filename
                    ))
                else:
                    # Insert new image
                    cursor.execute("""
                        INSERT INTO generated_images (
                            prompt_id, filename, width, height, parameters
                        ) VALUES (?, ?, ?, ?, ?)
                    """, (
                        prompt_id,
                        filename,
                        params.get("width"),
                        params.get("height"),
                        json.dumps(generation_params)
                    ))

            conn.commit()
            conn.close()

            print(f"[MetadataExtractor] Saved metadata for prompt {prompt_hash[:8]}...")
            return True

        except Exception as e:
            print(f"[MetadataExtractor] Database save error: {e}")
            return False

    def batch_extract_from_directory(self, directory: str | Path,
                                    extensions: List[str] = [".png"]) -> int:
        """
        Extract metadata from all images in a directory.

        Args:
            directory: Directory containing images
            extensions: File extensions to process

        Returns:
            Number of images processed successfully
        """
        directory = Path(directory)
        if not directory.exists():
            return 0

        processed = 0
        for ext in extensions:
            for image_path in directory.rglob(f"*{ext}"):
                try:
                    params = self.extract_from_png_metadata(str(image_path))
                    if params and params.get("positive"):
                        if self.save_to_database(params, str(image_path)):
                            processed += 1
                except Exception as e:
                    print(f"[MetadataExtractor] Error processing {image_path}: {e}")

        return processed


# Singleton instance
_extractor_instance = None


def get_metadata_extractor(db_path: str) -> WorkflowMetadataExtractor:
    """Get singleton metadata extractor instance."""
    global _extractor_instance
    if _extractor_instance is None:
        _extractor_instance = WorkflowMetadataExtractor(db_path)
    return _extractor_instance