"""The staged LoRA reference import (#221, #249 System S10, #355).

The import reads LoraManager's ``*.metadata.json`` sidecars and turns each
LoRA into one prompt of the ``lora-manager`` category, tagged with its name and
trigger words and linked to its images. It runs in three stages, and only the
last writes to the database:

1. **discover**: read LoraManager's settings the way LoraManager does
   (``py/lora_manager_layout.py``: the settings file from the environment
   folder, the portable plugin file or the user config folder; the LoRA
   roots, the storage mode, the sidecar and example-images roots) and find
   the sidecars (``discover``). In ``alongside`` mode a sidecar beside its
   model is kept when ``<base>.safetensors`` exists; in ``centralized`` mode
   every sidecar under the sidecar root is kept when its ``file_path`` names
   a model inside a LoRA root. ``fingerprint`` covers every scanned sidecar,
   kept or skipped (path, size, modification time, kept or not, #362) and the
   layout; ``preview`` adds the counts a review shows. Nothing is written
   and nothing is requested.
2. **stage**: read each sidecar and build its record. Its LoraManager preview
   and example images are linked where they are (``local_media``); only a
   LoRA without any local image downloads its CivitAI example images into the
   image cache first (best effort, counted). A cancelled or failed stage
   leaves the database untouched; downloaded images stay in the cache.
3. **replace**: the sidecars are found and fingerprinted once more (a file
   changed, removed or added while staging aborts the import), then
   ``PromptDatabase.replace_category_prompts`` swaps the old collection for
   the staged records in one transaction (or changes nothing).

Both the System job (``POST /lora/import/apply``) and the legacy ``POST
/lora/scan`` run these stages (``py/api/lora_integration.py``).
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import AbstractSet, Callable, Dict, List, Optional

from . import lora_utils
from .lora_manager_layout import (
    Discovery,
    discover_sidecars,
    layout_summary,
    local_media,
)

try:
    from ..utils import exit_signal
    from ..utils.hashing import generate_prompt_hash
except ImportError:  # pragma: no cover - flat test imports
    from utils import exit_signal
    from utils.hashing import generate_prompt_hash

LORA_CATEGORY = "lora-manager"
JOB_TITLE = "LoRA reference import"
# The job's outcome groups, in its counter-tile order.
GROUPS = ("imported", "linked", "skipped", "downloaded", "download_errors", "errors")


class ImportCancelled(Exception):
    """The import was cancelled while staging; nothing was written."""


def discover(lora_manager_path: Optional[str]) -> Discovery:
    """The sidecars of every LoRA whose model exists inside a LoRA root.

    ``lora_manager_path`` is resolved as the integration resolves it (an
    install under ``custom_nodes``; empty means auto-detection). Raises
    whatever listing a folder raised: a discovery that cannot see every
    folder must not replace the collection with part of it.
    """
    return discover_sidecars(lora_utils.lora_manager_layout(lora_manager_path))


def fingerprint(discovery: Discovery) -> str:
    """What a review saw: the layout, then every scanned sidecar, kept or
    skipped, with its path, size, modification time and whether it was kept
    (#362). A LoraManager save that changes unrelated settings leaves it as
    it was."""
    layout = discovery.layout
    digest = hashlib.sha256()
    for value in (
        layout.settings_path,
        layout.source,
        layout.error,
        layout.mode,
        "\0".join(layout.lora_roots),
        layout.sidecar_root,
        layout.example_images_root,
        layout.library,
    ):
        digest.update(f"{value}\n".encode("utf-8", "surrogatepass"))
    for name in discovery.unreadable:
        digest.update(f"unreadable\0{name}\n".encode("utf-8", "surrogatepass"))
    kept = {str(sidecar.metadata_path) for sidecar in discovery.sidecars}
    for path in discovery.scanned_files:
        flag = 1 if str(path) in kept else 0
        try:
            stat = path.stat()
            entry = f"{path}\0{stat.st_size}\0{stat.st_mtime_ns}\0{flag}\n"
        except OSError:
            entry = f"{path}\0missing\0{flag}\n"
        digest.update(entry.encode("utf-8", "surrogatepass"))
    return "sha256:" + digest.hexdigest()


def preview(
    lora_manager_path: Optional[str],
    cache_dir: Path,
    media_extensions: AbstractSet[str],
) -> Dict:
    """The review's counts (blocking; reads only, requests nothing).

    ``{lora_files, with_metadata, skipped, with_local_media,
    planned_downloads, fingerprint, names, layout}``: the metadata files
    seen, the kept LoRAs whose sidecar parses, the rest, the LoRAs with a
    local image, the CivitAI images an import would download for the LoRAs
    without one (not yet in the cache), the first LoRA names and what the
    layout says (``layout_summary``).
    """
    discovery = discover(lora_manager_path)
    layout = discovery.layout
    with_metadata = 0
    with_local_media = 0
    planned = 0
    names = []
    for sidecar in discovery.sidecars:
        metadata = lora_utils.read_lora_metadata(sidecar.metadata_path)
        if not metadata:
            continue
        with_metadata += 1
        if local_media(sidecar, metadata, layout, media_extensions):
            with_local_media += 1
        else:
            planned += lora_utils.planned_civitai_downloads(
                metadata, sidecar.metadata_path, cache_dir
            )
        if len(names) < 50:
            names.append(lora_utils.get_model_name_from_metadata(metadata))
    return {
        "lora_files": discovery.scanned,
        "with_metadata": with_metadata,
        "skipped": discovery.scanned - with_metadata,
        "with_local_media": with_local_media,
        "planned_downloads": planned,
        "fingerprint": fingerprint(discovery),
        "names": names,
        "layout": layout_summary(layout, Path.home()),
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


def stage(
    discovery: Discovery,
    cache_dir: Path,
    api_key: str = "",
    *,
    media_extensions: AbstractSet[str],
    on_record: Optional[Callable[[int, Dict, "StagedImport"], None]] = None,
    cancelled: Callable[[], bool] = lambda: False,
    logger=None,
) -> StagedImport:
    """Read each sidecar and build its record with its images.

    Blocking. Local images (``local_media``) come first; a LoRA without any
    downloads its CivitAI example images. A sidecar that cannot be read, or
    whose record fails to build, is skipped by its name, as are the
    centralized sidecars discovery could not parse. ``cancelled()`` is asked
    before each sidecar, and so is the exit signal (#376: the legacy scan's
    ``cancelled`` does not read it); either raises ImportCancelled. ``on_record(index,
    record_or_None, staged)`` follows each one.
    """
    sidecars = discovery.sidecars
    staged = StagedImport(total=len(sidecars))
    staged.skipped.extend(discovery.unreadable)
    for index, sidecar in enumerate(sidecars):
        if cancelled() or exit_signal.requested():
            raise ImportCancelled()
        record = None
        path = sidecar.metadata_path
        metadata = lora_utils.read_lora_metadata(path)
        if not metadata:
            staged.skipped.append(path.name)
        else:
            try:
                local = local_media(
                    sidecar, metadata, discovery.layout, media_extensions
                )
                remote = []
                if not local:
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
