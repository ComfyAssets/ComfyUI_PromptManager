"""
ComfyUI metadata extraction utilities.
Extracts workflow and prompt information from generated images.
"""

import os
import json
from typing import Optional, Dict, Any
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from .logging_config import get_logger


class ComfyUIMetadataExtractor:
    """Extracts ComfyUI metadata from generated images."""
    
    def __init__(self):
        """Initialize the metadata extractor."""
        self.logger = get_logger('prompt_manager.metadata_extractor')
    
    def extract_metadata(self, image_path: str) -> Optional[Dict[str, Any]]:
        """
        Extract ComfyUI workflow and prompt metadata from an image.
        
        Args:
            image_path: Path to the image file
            
        Returns:
            Dictionary containing extracted metadata or None if extraction fails
        """
        try:
            with Image.open(image_path) as image:
                metadata = {}
                
                # Add basic file information
                metadata['file_info'] = self.get_file_info(image_path, image)
                
                # Extract ComfyUI-specific metadata from PNG text chunks
                if hasattr(image, 'text') and image.text:
                    # Look for ComfyUI workflow data
                    if 'workflow' in image.text:
                        try:
                            workflow_data = json.loads(image.text['workflow'])
                            metadata['workflow'] = workflow_data
                            
                            # Extract text encoder nodes from workflow
                            text_encoder_nodes = self.find_text_encoder_nodes(workflow_data)
                            if text_encoder_nodes:
                                metadata['text_encoder_nodes'] = text_encoder_nodes
                                
                        except json.JSONDecodeError as e:
                            self.logger.warning(f"Failed to parse workflow JSON: {e}")
                    
                    # Look for prompt data
                    if 'prompt' in image.text:
                        try:
                            prompt_data = json.loads(image.text['prompt'])
                            metadata['prompt'] = prompt_data
                        except json.JSONDecodeError as e:
                            self.logger.warning(f"Failed to parse prompt JSON: {e}")
                    
                    # Extract other common metadata fields
                    metadata_fields = [
                        'parameters', 'model', 'sampler', 'steps', 'cfg_scale',
                        'seed', 'scheduler', 'positive', 'negative'
                    ]
                    
                    for field in metadata_fields:
                        if field in image.text:
                            try:
                                # Try to parse as JSON first
                                metadata[field] = json.loads(image.text[field])
                            except (json.JSONDecodeError, TypeError):
                                # Store as string if not valid JSON
                                metadata[field] = image.text[field]
                
                return metadata if any(key != 'file_info' for key in metadata.keys()) else None
                
        except Exception as e:
            self.logger.error(f"Error extracting metadata from {image_path}: {e}")
            return None
    
    def get_file_info(self, image_path: str, image: Image.Image) -> Dict[str, Any]:
        """
        Get basic file information.
        
        Args:
            image_path: Path to the image file
            image: PIL Image object
            
        Returns:
            Dictionary containing file information
        """
        try:
            stat = os.stat(image_path)
            return {
                'size': stat.st_size,
                'dimensions': list(image.size),
                'format': image.format,
                'mode': image.mode,
                'created_time': stat.st_ctime,
                'modified_time': stat.st_mtime
            }
        except Exception as e:
            self.logger.error(f"Error getting file info: {e}")
            return {}
    
    def find_text_encoder_nodes(self, workflow_data: Dict) -> list:
        """
        Find text encoder nodes in the workflow data.
        
        Args:
            workflow_data: ComfyUI workflow data
            
        Returns:
            List of text encoder node data
        """
        text_encoder_nodes = []
        
        if not isinstance(workflow_data, dict):
            return text_encoder_nodes
        
        # Check different possible workflow structures
        nodes_data = None
        
        # Try different keys where nodes might be stored
        if 'nodes' in workflow_data:
            nodes_data = workflow_data['nodes']
        elif 'workflow' in workflow_data and 'nodes' in workflow_data['workflow']:
            nodes_data = workflow_data['workflow']['nodes']
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
                    node_data['node_id'] = node_id
                    text_encoder_nodes.append(node_data)
        
        return text_encoder_nodes
    
    def is_text_encoder_node(self, node_data: Any) -> bool:
        """
        Check if a node is a text encoder node.
        
        Args:
            node_data: Node data to check
            
        Returns:
            True if the node is a text encoder
        """
        if not isinstance(node_data, dict):
            return False
        
        # Check for common text encoder node types
        text_encoder_types = [
            'CLIPTextEncode',
            'CLIPTextEncodeSDXL', 
            'CLIPTextEncodeSDXLRefiner',
            'PromptManager',  # Our custom node
            'BNK_CLIPTextEncoder',
            'Text Encoder',
            'CLIP Text Encode'
        ]
        
        # Check node type/class_type
        node_type = node_data.get('type') or node_data.get('class_type') or ''
        
        for encoder_type in text_encoder_types:
            if encoder_type.lower() in node_type.lower():
                return True
        
        # Check node title/name for text encoding keywords
        node_title = (node_data.get('title') or node_data.get('name') or '').lower()
        text_keywords = ['text', 'prompt', 'encode', 'clip']
        
        if any(keyword in node_title for keyword in text_keywords):
            return True
        
        return False
    
    def extract_prompt_text_from_workflow(self, workflow_data: Dict) -> Optional[str]:
        """
        Extract the actual prompt text from workflow data.
        
        Args:
            workflow_data: ComfyUI workflow data
            
        Returns:
            Extracted prompt text or None
        """
        text_encoder_nodes = self.find_text_encoder_nodes(workflow_data)
        
        for node in text_encoder_nodes:
            # Try different ways to get the prompt text
            inputs = node.get('inputs', {})
            
            # Common input field names for prompt text
            text_fields = ['text', 'prompt', 'positive', 'conditioning']
            
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
        
        Args:
            metadata: Full metadata dictionary
            
        Returns:
            Dictionary of generation parameters
        """
        parameters = {}
        
        # Common generation parameters to extract
        param_fields = [
            'steps', 'cfg_scale', 'sampler', 'scheduler', 'seed',
            'model', 'width', 'height', 'batch_size'
        ]
        
        for field in param_fields:
            if field in metadata:
                parameters[field] = metadata[field]
        
        # Extract from workflow if available
        if 'workflow' in metadata:
            workflow_params = self.extract_params_from_workflow(metadata['workflow'])
            parameters.update(workflow_params)
        
        return parameters
    
    def extract_params_from_workflow(self, workflow_data: Dict) -> Dict[str, Any]:
        """
        Extract generation parameters from workflow data.
        
        Args:
            workflow_data: ComfyUI workflow data
            
        Returns:
            Dictionary of extracted parameters
        """
        parameters = {}
        
        # This would need to be customized based on your specific workflow structure
        # For now, return empty dict - can be expanded based on specific needs
        
        return parameters