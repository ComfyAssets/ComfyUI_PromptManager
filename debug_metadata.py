#!/usr/bin/env python3
"""
Debug script to extract and analyze PNG metadata from the test image
to understand what should be displayed in the gallery views.
"""

import json
from PIL import Image
from PIL.PngImagePlugin import PngInfo

def extract_png_metadata(image_path):
    """Extract PNG metadata chunks from an image file."""
    try:
        with Image.open(image_path) as img:
            metadata = {}
            
            # Extract text chunks
            if hasattr(img, 'text'):
                for key, value in img.text.items():
                    metadata[key] = value
                    print(f"Found text chunk: {key} = {value[:100]}{'...' if len(value) > 100 else ''}")
            
            # Try to extract PNG info
            if hasattr(img, 'info'):
                print(f"\nImage info keys: {list(img.info.keys())}")
                for key, value in img.info.items():
                    if isinstance(value, str):
                        print(f"Info {key}: {value[:100]}{'...' if len(value) > 100 else ''}")
            
            return metadata
            
    except Exception as e:
        print(f"Error reading PNG metadata: {e}")
        return {}

def parse_comfyui_data(metadata):
    """Parse ComfyUI workflow and prompt data from PNG metadata."""
    workflow_data = None
    prompt_data = None
    
    # Look for workflow data
    workflow_fields = ['workflow', 'Workflow', 'comfy', 'ComfyUI']
    for field in workflow_fields:
        if field in metadata:
            try:
                workflow_data = json.loads(metadata[field])
                print(f"\nFound workflow data in field '{field}'")
                if isinstance(workflow_data, dict) and 'nodes' in workflow_data:
                    print(f"Workflow has {len(workflow_data['nodes'])} nodes")
                break
            except json.JSONDecodeError as e:
                print(f"Failed to parse workflow field '{field}': {e}")
    
    # Look for prompt data
    prompt_fields = ['prompt', 'Prompt', 'parameters', 'Parameters']
    for field in prompt_fields:
        if field in metadata:
            try:
                # Clean up NaN values that might break JSON parsing
                cleaned_json = metadata[field]
                cleaned_json = cleaned_json.replace(': NaN', ': null')
                cleaned_json = cleaned_json.replace(':NaN', ':null')
                cleaned_json = cleaned_json.replace('NaN', 'null')
                
                prompt_data = json.loads(cleaned_json)
                print(f"\nFound prompt data in field '{field}'")
                if isinstance(prompt_data, dict):
                    print(f"Prompt data has {len(prompt_data)} nodes")
                break
            except json.JSONDecodeError as e:
                print(f"Failed to parse prompt field '{field}': {e}")
    
    return workflow_data, prompt_data

def extract_generation_params(workflow_data, prompt_data):
    """Extract generation parameters from ComfyUI data."""
    params = {
        'checkpoint': 'Unknown',
        'positive_prompt': 'No prompt found',
        'negative_prompt': 'No negative prompt found',
        'steps': 'Unknown',
        'cfg_scale': 'Unknown',
        'sampler': 'Unknown',
        'seed': 'Unknown'
    }
    
    # Parse prompt data first (more reliable)
    if prompt_data:
        print("\nAnalyzing prompt data...")
        for node_id, node in prompt_data.items():
            node_type = node.get('class_type', '')
            inputs = node.get('inputs', {})
            
            print(f"  Node {node_id}: {node_type}")
            if inputs:
                print(f"    Inputs: {list(inputs.keys())}")
            
            if node_type == 'CheckpointLoaderSimple' and 'ckpt_name' in inputs:
                params['checkpoint'] = inputs['ckpt_name']
                print(f"    Found checkpoint: {params['checkpoint']}")
            
            elif node_type == 'PromptManager' and 'text' in inputs:
                params['positive_prompt'] = inputs['text']
                print(f"    Found PromptManager text: {params['positive_prompt'][:100]}...")
            
            elif node_type == 'CLIPTextEncode' and 'text' in inputs:
                text = inputs['text'].lower()
                if any(neg_word in text for neg_word in ['bad anatomy', 'unfinished', 'censored', 'negative', 'embedding:']):
                    params['negative_prompt'] = inputs['text']
                    print(f"    Found negative prompt: {params['negative_prompt'][:100]}...")
                elif params['positive_prompt'] == 'No prompt found':
                    params['positive_prompt'] = inputs['text']
                    print(f"    Found positive prompt: {params['positive_prompt'][:100]}...")
            
            elif 'sampler' in node_type.lower() or node_type in ['KSampler', 'SamplerCustomAdvanced', 'DetailDaemonSamplerNode']:
                if 'seed' in inputs:
                    params['seed'] = inputs['seed']
                if 'steps' in inputs:
                    params['steps'] = inputs['steps'] 
                if 'cfg' in inputs:
                    params['cfg_scale'] = inputs['cfg']
                if 'sampler_name' in inputs:
                    params['sampler'] = inputs['sampler_name']
                if 'scheduler' in inputs:
                    params['scheduler'] = inputs['scheduler']
                print(f"    Found {node_type}: seed={inputs.get('seed', 'N/A')}, steps={inputs.get('steps', 'N/A')}, cfg={inputs.get('cfg', 'N/A')}, sampler={inputs.get('sampler_name', 'N/A')}")
                
            # Also check for BasicScheduler which might have steps
            elif node_type == 'BasicScheduler' and 'steps' in inputs:
                if params['steps'] == 'Unknown':
                    params['steps'] = inputs['steps']
                    print(f"    Found BasicScheduler with steps: {params['steps']}")
                    
            # Check for CFG values
            elif node_type == 'CFGGuider' and 'cfg' in inputs:
                if params['cfg_scale'] == 'Unknown':
                    params['cfg_scale'] = inputs['cfg']
                    print(f"    Found CFGGuider with cfg: {params['cfg_scale']}")
            
            # Check for seed in various nodes
            elif 'seed' in inputs:
                if params['seed'] == 'Unknown':
                    params['seed'] = inputs['seed']
                    print(f"    Found {node_type} with seed: {params['seed']}")
            elif 'noise_seed' in inputs:
                if params['seed'] == 'Unknown':
                    params['seed'] = inputs['noise_seed']
                    print(f"    Found {node_type} with noise_seed: {params['seed']}")
            
            # Check for noise/seed generators
            elif node_type in ['RandomNoise', 'EmptyLatentImage'] and 'seed' in inputs:
                if params['seed'] == 'Unknown':
                    params['seed'] = inputs['seed']
                    print(f"    Found {node_type} with seed: {params['seed']}")
    
    # Fallback to workflow data if needed
    if workflow_data and workflow_data.get('nodes'):
        print("\nAnalyzing workflow data...")
        for node in workflow_data['nodes']:
            node_type = node.get('type', '')
            widgets = node.get('widgets_values', [])
            
            if node_type == 'CheckpointLoaderSimple' and widgets:
                if params['checkpoint'] == 'Unknown':
                    params['checkpoint'] = widgets[0]
                    print(f"  Found checkpoint in workflow: {params['checkpoint']}")
            
            elif node_type == 'PromptManager' and widgets:
                if params['positive_prompt'] == 'No prompt found':
                    params['positive_prompt'] = widgets[0]
                    print(f"  Found PromptManager in workflow: {params['positive_prompt'][:100]}...")
            
            elif node_type == 'KSampler' and len(widgets) >= 4:
                if params['seed'] == 'Unknown':
                    params['seed'] = widgets[0]
                    params['steps'] = widgets[1] 
                    params['cfg_scale'] = widgets[2]
                    params['sampler'] = widgets[3]
                    print(f"  Found KSampler in workflow: seed={params['seed']}, steps={params['steps']}")
    
    return params

def main():
    print("=== PNG Metadata Debug Tool ===\n")
    
    image_path = "test_metadata_file.png"
    print(f"Analyzing: {image_path}")
    
    # Extract raw metadata
    metadata = extract_png_metadata(image_path)
    print(f"\nFound {len(metadata)} metadata fields:")
    for key in metadata.keys():
        print(f"  - {key}")
    
    # Parse ComfyUI data
    workflow_data, prompt_data = parse_comfyui_data(metadata)
    
    # Extract generation parameters
    params = extract_generation_params(workflow_data, prompt_data)
    
    print("\n=== EXTRACTED PARAMETERS ===")
    for key, value in params.items():
        print(f"{key}: {value}")
    
    print("\n=== RAW METADATA FIELDS ===")
    for key, value in metadata.items():
        if len(value) < 200:
            print(f"{key}: {value}")
        else:
            print(f"{key}: {value[:200]}... (truncated)")

if __name__ == "__main__":
    main()