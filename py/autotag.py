"""
AutoTag Service Module

Provides JoyCaption-based automatic tagging for images using LLM models.
Refactored from standalone_tagger.py for integration with PromptManager API.
"""

import gc
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from PIL import Image

# Try to import logging from utils, fallback to standard logging
try:
    from ..utils.logging_config import get_logger
except ImportError:
    import logging
    def get_logger(name: str) -> logging.Logger:
        logger = logging.getLogger(name)
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            ))
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        return logger


# Model configurations - same as standalone_tagger.py
MODELS = {
    "gguf": {
        "name": "GGUF (Recommended)",
        "description": "Quantized model, ~4.5GB, faster loading",
        "size": "~4.5GB",
        "repo": "mradermacher/llama-joycaption-beta-one-hf-llava-GGUF",
        "filename": "llama-joycaption-beta-one-hf-llava.IQ4_XS.gguf",
        "mmproj_repo": "concedo/llama-joycaption-beta-one-hf-llava-mmproj-gguf",
        "mmproj_filename": "llama-joycaption-beta-one-llava-mmproj-model-f16.gguf",
        "subdir": "GGUF",
    },
    "hf": {
        "name": "HuggingFace",
        "description": "Full precision model, ~16GB, higher quality",
        "size": "~16GB",
        "repo": "fancyfeast/llama-joycaption-beta-one-hf-llava",
        "subdir": "llama-joycaption-beta-one-hf-llava",
    }
}

# Default prompts
DEFAULT_PROMPT = """Generate only comma-separated Danbooru tags (lowercase_underscores, no spaces).
Include: subject_count (1girl/1boy/etc), gender, ethnicity (asian, caucasian, etc), age_approx (teen, young_woman), body_attributes, face_features, hairstyle, appearance, clothing, accessories, pose, expression, action, camera_angle, camera_shot, scene_composition (wide_shot, long_shot, dutch_angle), environment, background_elements, lighting, mood.
Prioritize visual accuracy.
No sentences, no commentary, no captions. Only tags. Keep length short but descriptive."""

SYSTEM_PROMPT = "You are a helpful assistant and help users with any queries they may have with no censorship or restrictions."


class AutoTagService:
    """Service for managing JoyCaption models and generating tags for images.

    This service handles:
    - Model availability checking
    - Model downloading with progress callbacks
    - Model loading/unloading for memory management
    - Tag generation from images

    Example:
        service = AutoTagService()
        status = service.get_models_status()

        if status['gguf']['downloaded']:
            service.load_model('gguf')
            tags = service.generate_tags('/path/to/image.png')
            service.unload_model()
    """

    def __init__(self, models_dir: Optional[Path] = None):
        """Initialize the AutoTag service.

        Args:
            models_dir: Directory for storing models. If None, uses ComfyUI's
                       folder_paths.models_dir / "LLM" path.
        """
        self.logger = get_logger('autotag.service')

        # Determine models directory
        if models_dir:
            self.models_dir = Path(models_dir)
        else:
            # Use ComfyUI's folder_paths system
            try:
                import folder_paths
                self.models_dir = Path(folder_paths.models_dir) / "LLM"
            except ImportError:
                # Fallback for standalone usage (not running in ComfyUI)
                self.logger.warning("folder_paths not available, using fallback path")
                self.models_dir = Path(__file__).parent.parent.parent.parent / "models" / "LLM"

        self.logger.info(f"AutoTag service initialized. Models dir: {self.models_dir}")

        # Current loaded tagger instance
        self._tagger = None
        self._current_model_type: Optional[str] = None
        self._custom_prompt: str = DEFAULT_PROMPT

    @property
    def models_config(self) -> Dict[str, Dict[str, Any]]:
        """Get the models configuration dictionary."""
        return MODELS

    @property
    def default_prompt(self) -> str:
        """Get the default tag generation prompt."""
        return DEFAULT_PROMPT

    @property
    def custom_prompt(self) -> str:
        """Get the current custom prompt."""
        return self._custom_prompt

    @custom_prompt.setter
    def custom_prompt(self, value: str):
        """Set a custom prompt for tag generation."""
        self._custom_prompt = value

    def get_models_status(self) -> Dict[str, Dict[str, Any]]:
        """Get availability status for all model types.

        Returns:
            Dictionary with status for each model type:
            {
                'gguf': {
                    'name': 'GGUF (Recommended)',
                    'description': '...',
                    'size': '~4.5GB',
                    'downloaded': True,
                    'model_exists': True,
                    'mmproj_exists': True,  # GGUF only
                    'model_path': '/path/to/model'
                },
                'hf': {...}
            }
        """
        status = {}

        for model_type, config in MODELS.items():
            model_status = {
                'name': config['name'],
                'description': config['description'],
                'size': config['size'],
                'downloaded': False,
                'model_path': None
            }

            if model_type == 'gguf':
                model_exists, mmproj_exists = self._check_gguf_models()
                model_status['model_exists'] = model_exists
                model_status['mmproj_exists'] = mmproj_exists
                model_status['downloaded'] = model_exists and mmproj_exists
                if model_status['downloaded']:
                    model_status['model_path'] = str(
                        self.models_dir / config['subdir'] / config['filename']
                    )
            else:  # hf
                model_status['downloaded'] = self._check_hf_model()
                if model_status['downloaded']:
                    # Get the actual path (local or cache)
                    model_status['model_path'] = str(
                        self._get_hf_model_path()
                    )

            status[model_type] = model_status

        return status

    def _check_gguf_models(self) -> Tuple[bool, bool]:
        """Check if GGUF model and mmproj files exist.

        Returns:
            Tuple of (model_exists, mmproj_exists)
        """
        config = MODELS['gguf']
        gguf_dir = self.models_dir / config['subdir']
        model_path = gguf_dir / config['filename']
        mmproj_path = gguf_dir / config['mmproj_filename']
        return model_path.exists(), mmproj_path.exists()

    def _check_hf_model(self) -> bool:
        """Check if HuggingFace model exists.

        Checks both the local models directory and the HuggingFace cache.

        Returns:
            True if model directory contains config.json
        """
        config = MODELS['hf']

        # Check local directory first
        model_dir = self.models_dir / config['subdir']
        self.logger.debug(f"Checking local HF model path: {model_dir}")
        if (model_dir / "config.json").exists():
            self.logger.debug("Found HF model in local directory")
            return True

        # Check HuggingFace cache as fallback
        hf_cache_path = self._get_hf_cache_path(config['repo'])
        if hf_cache_path:
            self.logger.debug(f"Found HF model in cache: {hf_cache_path}")
            return True

        self.logger.debug("HF model not found in local dir or cache")
        return False

    def _get_hf_cache_path(self, repo_id: str) -> Optional[Path]:
        """Get the path to a model in the HuggingFace cache.

        Args:
            repo_id: The HuggingFace repo ID (e.g., 'fancyfeast/llama-joycaption-beta-one-hf-llava')

        Returns:
            Path to the cached model directory, or None if not found
        """
        try:
            from huggingface_hub import scan_cache_dir, HFCacheInfo
        except ImportError:
            self.logger.debug("huggingface_hub not available for cache check")
            return None

        try:
            cache_info = scan_cache_dir()
            for repo in cache_info.repos:
                if repo.repo_id == repo_id and repo.repo_type == "model":
                    # Get the latest revision's snapshot path
                    for revision in repo.revisions:
                        snapshot_path = revision.snapshot_path
                        if (Path(snapshot_path) / "config.json").exists():
                            return Path(snapshot_path)
        except Exception as e:
            self.logger.debug(f"Error scanning HF cache: {e}")

        return None

    def _get_hf_model_path(self) -> Optional[Path]:
        """Get the actual path to the HuggingFace model.

        Checks local directory first, then HuggingFace cache.

        Returns:
            Path to the model directory, or None if not found
        """
        config = MODELS['hf']

        # Check local directory first
        model_dir = self.models_dir / config['subdir']
        if (model_dir / "config.json").exists():
            return model_dir

        # Check HuggingFace cache as fallback
        cache_path = self._get_hf_cache_path(config['repo'])
        if cache_path:
            return cache_path

        return None

    def download_model(
        self,
        model_type: str,
        progress_callback: Optional[Callable[[str, float], None]] = None
    ) -> bool:
        """Download a model with optional progress updates.

        Args:
            model_type: Either 'gguf' or 'hf'
            progress_callback: Optional callback(status_message, progress_percent)

        Returns:
            True if download successful, False otherwise

        Raises:
            ValueError: If model_type is not valid
        """
        if model_type not in MODELS:
            raise ValueError(f"Invalid model type: {model_type}. Must be 'gguf' or 'hf'")

        try:
            from huggingface_hub import hf_hub_download, snapshot_download
        except ImportError:
            self.logger.error("huggingface_hub not installed")
            if progress_callback:
                progress_callback("Error: huggingface_hub not installed", 0)
            return False

        try:
            if model_type == 'gguf':
                return self._download_gguf_models(progress_callback)
            else:
                return self._download_hf_model(progress_callback)
        except Exception as e:
            self.logger.error(f"Download failed: {e}")
            if progress_callback:
                progress_callback(f"Error: {str(e)}", 0)
            return False

    def _download_gguf_models(
        self,
        progress_callback: Optional[Callable[[str, float], None]] = None
    ) -> bool:
        """Download GGUF model and mmproj files."""
        from huggingface_hub import hf_hub_download

        config = MODELS['gguf']
        gguf_dir = self.models_dir / config['subdir']
        gguf_dir.mkdir(parents=True, exist_ok=True)

        model_path = gguf_dir / config['filename']
        mmproj_path = gguf_dir / config['mmproj_filename']

        # Download main model
        if not model_path.exists():
            if progress_callback:
                progress_callback(f"Downloading {config['filename']}...", 10)

            self.logger.info(f"Downloading GGUF model: {config['filename']}")
            hf_hub_download(
                repo_id=config['repo'],
                filename=config['filename'],
                local_dir=str(gguf_dir),
                local_dir_use_symlinks=False
            )
            self.logger.info("GGUF model downloaded")

        if progress_callback:
            progress_callback("Main model ready", 50)

        # Download mmproj
        if not mmproj_path.exists():
            if progress_callback:
                progress_callback(f"Downloading {config['mmproj_filename']}...", 60)

            self.logger.info(f"Downloading mmproj: {config['mmproj_filename']}")
            hf_hub_download(
                repo_id=config['mmproj_repo'],
                filename=config['mmproj_filename'],
                local_dir=str(gguf_dir),
                local_dir_use_symlinks=False
            )
            self.logger.info("mmproj downloaded")

        if progress_callback:
            progress_callback("Download complete", 100)

        return True

    def _download_hf_model(
        self,
        progress_callback: Optional[Callable[[str, float], None]] = None
    ) -> bool:
        """Download HuggingFace model."""
        from huggingface_hub import snapshot_download

        config = MODELS['hf']
        model_dir = self.models_dir / config['subdir']

        if not self._check_hf_model():
            if progress_callback:
                progress_callback(f"Downloading {config['repo']}...", 10)

            self.logger.info(f"Downloading HF model: {config['repo']}")
            snapshot_download(
                repo_id=config['repo'],
                local_dir=str(model_dir),
                local_dir_use_symlinks=False
            )
            self.logger.info("HF model downloaded")

        if progress_callback:
            progress_callback("Download complete", 100)

        return True

    def load_model(self, model_type: str, use_gpu: bool = True) -> bool:
        """Load a model into memory for tag generation.

        Args:
            model_type: Either 'gguf' or 'hf'
            use_gpu: Whether to use GPU acceleration (default True)

        Returns:
            True if model loaded successfully

        Raises:
            ValueError: If model_type is invalid
            RuntimeError: If model not downloaded or loading fails
        """
        if model_type not in MODELS:
            raise ValueError(f"Invalid model type: {model_type}")

        # Unload existing model first
        if self._tagger is not None:
            self.unload_model()

        status = self.get_models_status()
        if not status[model_type]['downloaded']:
            raise RuntimeError(f"Model {model_type} not downloaded")

        try:
            if model_type == 'gguf':
                self._tagger = self._load_gguf_tagger(use_gpu)
            else:
                self._tagger = self._load_hf_tagger()

            self._current_model_type = model_type
            self.logger.info(f"Model {model_type} loaded successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to load model {model_type}: {e}")
            self._tagger = None
            self._current_model_type = None
            raise RuntimeError(f"Failed to load model: {e}")

    def _load_gguf_tagger(self, use_gpu: bool = True):
        """Load GGUF-based tagger."""
        from llama_cpp import Llama
        from llama_cpp.llama_chat_format import Llava15ChatHandler

        config = MODELS['gguf']
        gguf_dir = self.models_dir / config['subdir']
        model_path = gguf_dir / config['filename']
        mmproj_path = gguf_dir / config['mmproj_filename']

        self.logger.info("Loading GGUF model...")
        n_gpu_layers = -1 if use_gpu else 0

        tagger = Llama(
            model_path=str(model_path),
            n_ctx=4096,
            n_batch=2048,
            n_threads=4,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
            chat_handler=Llava15ChatHandler(clip_model_path=str(mmproj_path)),
            offload_kqv=True,
        )

        self.logger.info("GGUF model loaded")
        return ('gguf', tagger)

    def _load_hf_tagger(self, quantization: str = "8bit"):
        """Load HuggingFace-based tagger."""
        import torch
        from transformers import AutoProcessor, LlavaForConditionalGeneration, BitsAndBytesConfig

        # Get the actual model path (local or cache)
        model_path = self._get_hf_model_path()
        if model_path is None:
            raise RuntimeError("HuggingFace model not found in local directory or cache")

        self.logger.info(f"Loading HuggingFace model from {model_path}...")
        device = "cuda" if torch.cuda.is_available() else "cpu"

        processor = AutoProcessor.from_pretrained(str(model_path))
        model_kwargs = {"device_map": "cuda" if device == "cuda" else "cpu"}

        if quantization == "8bit":
            qnt_config = BitsAndBytesConfig(
                load_in_8bit=True,
                bnb_8bit_compute_dtype=torch.float16,
                bnb_8bit_use_double_quant=True,
                llm_int8_skip_modules=["vision_tower", "multi_modal_projector"],
            )
            model = LlavaForConditionalGeneration.from_pretrained(
                str(model_path),
                torch_dtype=torch.float16,
                quantization_config=qnt_config,
                **model_kwargs
            )
        else:
            model = LlavaForConditionalGeneration.from_pretrained(
                str(model_path),
                torch_dtype=torch.bfloat16,
                **model_kwargs
            )

        model.eval()
        self.logger.info("HuggingFace model loaded")
        # Track the compute dtype for pixel_values conversion
        compute_dtype = torch.float16 if quantization == "8bit" else torch.bfloat16
        return ('hf', (model, processor, device, compute_dtype))

    def unload_model(self):
        """Unload the current model and free memory."""
        if self._tagger is not None:
            self.logger.info(f"Unloading model: {self._current_model_type}")
            del self._tagger
            self._tagger = None
            self._current_model_type = None
            gc.collect()

            # Try to clear CUDA cache if available
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass

            self.logger.info("Model unloaded, memory freed")

    def is_model_loaded(self) -> bool:
        """Check if a model is currently loaded."""
        return self._tagger is not None

    def get_loaded_model_type(self) -> Optional[str]:
        """Get the type of currently loaded model."""
        return self._current_model_type

    def generate_tags(
        self,
        image_path: str,
        prompt: Optional[str] = None
    ) -> List[str]:
        """Generate tags for an image.

        Args:
            image_path: Path to the image file
            prompt: Custom prompt for tag generation. Uses default if None.

        Returns:
            List of generated tags

        Raises:
            RuntimeError: If no model is loaded
            FileNotFoundError: If image doesn't exist
        """
        if self._tagger is None:
            raise RuntimeError("No model loaded. Call load_model() first.")

        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        use_prompt = prompt or self._custom_prompt

        # Load image
        image = Image.open(image_path)
        if image.mode != 'RGB':
            image = image.convert('RGB')

        # Generate based on model type
        model_type, tagger_obj = self._tagger

        if model_type == 'gguf':
            raw_tags = self._generate_gguf(tagger_obj, image, use_prompt)
        else:
            model, processor, device, compute_dtype = tagger_obj
            raw_tags = self._generate_hf(model, processor, device, compute_dtype, image, use_prompt)

        # Parse tags from response
        tags = self._parse_tags(raw_tags)
        return tags

    def _generate_gguf(self, model, image: Image.Image, prompt: str) -> str:
        """Generate tags using GGUF model."""
        import base64
        import io

        # Resize image
        image = image.resize((336, 336), Image.Resampling.BILINEAR)

        # Encode to base64
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        buffer.seek(0)
        img_base64 = base64.b64encode(buffer.read()).decode('utf-8')
        data_uri = f"data:image/png;base64,{img_base64}"

        # Create message
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}}
                ]
            }
        ]

        # Generate
        response = model.create_chat_completion(
            messages=messages,
            max_tokens=512,
            temperature=0.6,
            top_p=0.9,
            stop=["</s>", "User:", "Assistant:"],
            stream=False,
        )

        return response["choices"][0]["message"]["content"].strip()

    def _generate_hf(
        self,
        model,
        processor,
        device: str,
        compute_dtype,
        image: Image.Image,
        prompt: str
    ) -> str:
        """Generate tags using HuggingFace model."""
        import torch

        # Resize image
        image = image.resize((336, 336), Image.Resampling.LANCZOS)

        convo = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        convo_string = processor.apply_chat_template(
            convo, tokenize=False, add_generation_prompt=True
        )

        inputs = processor(
            text=[convo_string], images=[image], return_tensors="pt"
        ).to(device)

        # Convert pixel_values to the model's compute dtype
        # (float16 for 8-bit quantized, bfloat16 for non-quantized)
        if 'pixel_values' in inputs and inputs['pixel_values'] is not None:
            inputs['pixel_values'] = inputs['pixel_values'].to(compute_dtype)

        with torch.inference_mode(), torch.cuda.amp.autocast(enabled=True):
            generate_ids = model.generate(
                **inputs,
                max_new_tokens=512,
                do_sample=True,
                temperature=0.6,
                top_p=0.9,
                use_cache=True,
            )[0]

        generate_ids = generate_ids[inputs['input_ids'].shape[1]:]
        return processor.tokenizer.decode(generate_ids, skip_special_tokens=True).strip()

    def _parse_tags(self, raw_output: str) -> List[str]:
        """Parse raw model output into a list of clean tags.

        Args:
            raw_output: Raw text output from the model

        Returns:
            List of cleaned, deduplicated tags
        """
        # Patterns to exclude
        exclude_prefixes = (
            'copyright:',
            'meta:',
            'photo_',
            'photo:',
        )

        # Split by common delimiters
        tags = []

        # Handle comma-separated tags
        for part in raw_output.split(','):
            tag = part.strip().lower()
            # Remove any quotes or extra characters
            tag = tag.strip('"\'')
            # Replace spaces with underscores (Danbooru style)
            tag = tag.replace(' ', '_')
            # Remove empty tags
            if tag and len(tag) > 1:
                # Filter out unwanted tag patterns
                if not tag.startswith(exclude_prefixes):
                    tags.append(tag)

        # Deduplicate while preserving order
        seen = set()
        unique_tags = []
        for tag in tags:
            if tag not in seen:
                seen.add(tag)
                unique_tags.append(tag)

        return unique_tags


# Singleton instance for API use
_service_instance: Optional[AutoTagService] = None


def get_autotag_service() -> AutoTagService:
    """Get or create the singleton AutoTagService instance."""
    global _service_instance
    if _service_instance is None:
        _service_instance = AutoTagService()
    return _service_instance
