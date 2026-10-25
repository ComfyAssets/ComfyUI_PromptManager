"""The staged LoRA reference import (#221, #249 System S10).

The import reads LoraManager's ``*.metadata.json`` files and turns each LoRA
into one prompt of the ``lora-manager`` category, tagged with its name and
trigger words and linked to its preview images. It runs in three stages, and
only the last writes to the database:

1. **discover**: list the metadata files (``discover``) and fingerprint them
   (path, size and modification time; ``fingerprint``). ``preview`` adds the
   counts a review shows. Nothing is written and nothing is requested.
2. **stage**: read each file and build its record; CivitAI example images are
   downloaded into the image cache first (best effort, counted). A cancelled
   or failed stage leaves the database untouched; downloaded images stay in
   the cache.
3. **replace**: ``PromptDatabase.replace_category_prompts`` swaps the old
   collection for the staged records in one transaction (or changes nothing).

Both the System job (``POST /lora/import/apply``) and the legacy ``POST
/lora/scan`` run these stages (``py/api/lora_integration.py``).
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import lora_utils

try:
    from ..utils.hashing import generate_prompt_hash
except ImportError:  # pragma: no cover - flat test imports
    from utils.hashing import generate_prompt_hash

LORA_CATEGORY = "lora-manager"
JOB_TITLE = "LoRA reference import"
# The job's outcome groups, in its counter-tile order.
GROUPS = ("imported", "linked", "skipped", "downloaded", "download_errors", "errors")
METADATA_SUFFIX = "*.metadata.json"


class ImportCancelled(Exception):
    """The import was cancelled while staging; nothing was written."""


def discover(lora_manager_path: str) -> List[Path]:
    """Every LoRA metadata file, sorted and without repeats.

    Raises whatever listing a folder raised: a discovery that cannot see
    every folder must not replace the collection with part of it.
    """
    seen = set()
    files = []
    for folder in lora_utils.find_lora_directories(lora_manager_path):
        for path in Path(folder).rglob(METADATA_SUFFIX):
            key = str(path)
            if key not in seen:
                seen.add(key)
                files.append(path)
    files.sort(key=str)
    return files


def fingerprint(files: List[Path]) -> str:
    """What a review saw of ``files``: each path, size and modification time."""
    digest = hashlib.sha256()
    for path in files:
        try:
            stat = path.stat()
            entry = f"{path}\0{stat.st_size}\0{stat.st_mtime_ns}\n"
        except OSError:
            entry = f"{path}\0missing\n"
        digest.update(entry.encode("utf-8", "surrogatepass"))
    return "sha256:" + digest.hexdigest()


def preview(lora_manager_path: str, cache_dir: Path) -> Dict:
    """The review's counts (blocking; reads only, requests nothing).

    ``{lora_files, with_metadata, planned_downloads, fingerprint, names}``:
    the metadata files found, those that parse, the example images an import
    would download (not yet in the cache) and the first LoRA names.
    """
    files = discover(lora_manager_path)
    with_metadata = 0
    planned = 0
    names = []
    for path in files:
        metadata = lora_utils.read_lora_metadata(path)
        if not metadata:
            continue
        with_metadata += 1
        planned += lora_utils.planned_civitai_downloads(metadata, path, cache_dir)
        if len(names) < 50:
            names.append(lora_utils.get_model_name_from_metadata(metadata))
    return {
        "lora_files": len(files),
        "with_metadata": with_metadata,
        "planned_downloads": planned,
        "fingerprint": fingerprint(files),
        "names": names,
    }


def build_record(metadata: Dict, path: Path, images: List[str]) -> Dict:
    """The prompt a LoRA becomes: its example prompt (else its name), tags, images."""
    model_name = lora_utils.get_model_name_from_metadata(metadata)
    text = lora_utils.get_example_prompt_from_metadata(metadata) or model_name
    tags = ["lora-manager", f"lora:{model_name}"]
    tags.extend(lora_utils.get_trigger_words_from_metadata(metadata))
    return {
        "name": model_name,
        "text": text,
        "hash": generate_prompt_hash(text),
        "tags": list(dict.fromkeys(tags)),
        "images": images,
    }


@dataclass
class StagedImport:
    """The records an import will write, and what staging counted."""

    total: int
    records: List[Dict] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(
        default_factory=lambda: {"downloaded": 0, "download_errors": 0}
    )
    fingerprint: str = ""


def stage(
    files: List[Path],
    cache_dir: Path,
    api_key: str = "",
    *,
    on_record: Optional[Callable[[int, Dict, "StagedImport"], None]] = None,
    cancelled: Callable[[], bool] = lambda: False,
    logger=None,
) -> StagedImport:
    """Read each file and build its record, downloading its example images.

    Blocking. A file that cannot be read, or whose record fails to build, is
    skipped by its name. ``cancelled()`` is asked before each file (raises
    ImportCancelled); ``on_record(index, record_or_None, staged)`` follows
    each one.
    """
    staged = StagedImport(total=len(files), fingerprint=fingerprint(files))
    for index, path in enumerate(files):
        if cancelled():
            raise ImportCancelled()
        record = None
        metadata = lora_utils.read_lora_metadata(path)
        if not metadata:
            staged.skipped.append(path.name)
        else:
            try:
                local = lora_utils.get_preview_images_from_metadata(metadata, path)
                remote = lora_utils.download_civitai_images(
                    metadata, path, cache_dir, api_key, stats=staged.stats
                )
                images = list(dict.fromkeys([*local, *remote]))
                record = build_record(metadata, path, images)
                staged.records.append(record)
            except Exception as error:  # one LoRA never stops the import
                name = lora_utils.get_model_name_from_metadata(metadata)
                if logger is not None:
                    logger.warning(f"Failed to prepare LoRA {name}: {error}")
                staged.skipped.append(name)
        if on_record is not None:
            on_record(index, record, staged)
    return staged
