"""Concept-shaped real data for every concept comparison (D30 of #249).

The approved concept's six prompts (docs/concepts/workspace/app.js:71-178) and its
24 sample images (app.js:180-239) become real rows and real PNG files in this
server's disposable database and output folder, so production renders the
concept's shapes from its own stores: the same titles, texts, categories, tags,
ratings and negative names, the same image dimensions, filenames and seeds, and
the same sidebar counts (24 images, 6 prompts, 15 tags). Nothing is mocked.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo

from utils.execution_contexts import (
    ContextBundle,
    PromptSnapshot,
    RecipeContext,
    TextEvidence,
)
from utils.execution_history_events import make_finish, make_start
from utils.execution_outputs import OutputBundle, OutputReference, snapshot_output_roots
from utils.pair_execution import EXECUTION_KEY

if TYPE_CHECKING:
    from py.api import PromptManagerAPI


@dataclass(frozen=True)
class ConceptPrompt:
    id: str
    title: str
    category: str
    tags: tuple[str, ...]
    positive: str
    negatives: tuple[tuple[str, str], ...]
    color: tuple[str, str, str]
    rating: int


# app.js:71-178, in concept order. Negatives are (name, text) pairs.
CONCEPT_PROMPTS = (
    ConceptPrompt(
        "alpine",
        "Alpine observatory",
        "Landscapes",
        ("cinematic", "blue_hour", "architecture"),
        "A remote alpine observatory at blue hour, warm light through glass, dramatic "
        "mountain peaks, cinematic composition, atmospheric mist, fine detail",
        (
            ("Quality baseline", "blurry, low quality, low resolution"),
            (
                "Clean architecture",
                "blurry, low quality, low resolution, distorted architecture, "
                "watermark, text",
            ),
            ("No negative", ""),
        ),
        ("#14233c", "#9aaac3", "#d8d9dd"),
        5,
    ),
    ConceptPrompt(
        "dunes",
        "Quiet geometry",
        "Architecture",
        ("minimal", "warm_light", "editorial"),
        "Monolithic sandstone pavilion in a vast desert, sweeping dunes, warm "
        "terracotta, late afternoon sun, architectural photography, quiet geometry",
        (
            ("Natural materials", "plastic, harsh shadows, noise, watermark"),
            (
                "Clean lines",
                "plastic, harsh shadows, noise, watermark, distorted geometry",
            ),
        ),
        ("#6c4948", "#ddad91", "#efe0c5"),
        4,
    ),
    ConceptPrompt(
        "forest",
        "Somewhere in the pines",
        "Landscapes",
        ("forest", "mist", "peaceful"),
        "A quiet cedar cabin above a still forest lake, towering evergreens, soft "
        "morning mist, muted jade and moss, a small amber window, cinematic still",
        (("Soft atmosphere", "oversaturated, blurry, low quality, text"),),
        ("#163b3d", "#6f9691", "#c0cfc1"),
        5,
    ),
    ConceptPrompt(
        "city",
        "After the rain",
        "Environments",
        ("neon", "night", "cinematic"),
        "Rain-soaked city street at night, blue neon and rose reflections, deep "
        "perspective, cinematic science fiction, solitary architecture, detailed "
        "atmosphere",
        (
            ("Clean atmosphere", "blurry, low resolution, watermark, oversaturated"),
            (
                "Less signage",
                "blurry, low resolution, watermark, oversaturated, text, signs",
            ),
        ),
        ("#1a2143", "#6869a1", "#cf8fbc"),
        4,
    ),
    ConceptPrompt(
        "lunar",
        "The long way home",
        "Illustration",
        ("space", "minimal", "concept_art"),
        "An isolated lunar research outpost, a luminous planet above the horizon, "
        "sweeping pale terrain, quiet science fiction, finely detailed concept art",
        (("Quality baseline", "blurry, low quality, watermark"),),
        ("#19283b", "#7398ac", "#e2d3c4"),
        5,
    ),
    ConceptPrompt(
        "coast",
        "Between sea and sky",
        "Landscapes",
        ("coast", "soft_light", "peaceful"),
        "A solitary lighthouse on a rugged coastal cliff, sea mist, glints of morning "
        "light on turquoise water, atmospheric perspective, fine art landscape",
        (("Natural light", "oversaturated, flat lighting, watermark, text"),),
        ("#255565", "#8cbcc2", "#e8d9bf"),
        4,
    ),
)
# app.js:220-227: the image at flat index n has the n % 6 dimensions; with six
# prompts per round that is the prompt's own index.
CONCEPT_DIMENSIONS = (
    (1536, 1152),
    (1024, 1536),
    (1280, 1280),
    (1600, 900),
    (1152, 1536),
    (1536, 1024),
)
CONCEPT_IMAGE_ROUNDS = 4
CONCEPT_RUN_COUNT = 4
# The concept's Model fact (app.js:408).
CONCEPT_MODEL = "illustrative-model.safetensors"
MAX_EXTRA_IMAGES = 120
SESSION = "5c1f9a7e-3f0b-4a55-9d0e-2b8c4f6a7d10"
_TABLES = (
    "execution_run_outputs",
    "execution_output_capture",
    "execution_run_contexts",
    "execution_context_capture",
    "execution_runs",
    "execution_contexts",
    "execution_prompt_snapshots",
    "execution_context_texts",
    "image_favorites",
    "image_prompt_pairs",
    "prompt_pair_tags",
    "prompt_pairs",
    "generated_images",
    "prompt_tags",
    "tags",
    "prompt_library_state",
    "prompts",
)


def concept_starred(prompt_index: int, round_index: int) -> bool:
    """The concept's favorites (app.js ``starred``): each other prompt's first image."""
    return round_index == 0 and prompt_index % 2 == 0


def concept_seed(prompt_index: int, round_index: int) -> int:
    """The concept's sample seed (app.js:185)."""
    return 42001 + prompt_index * 100 + round_index


def concept_filename(prompt: ConceptPrompt, round_index: int) -> str:
    """The concept's sample filename (app.js:187)."""
    return f"{prompt.id}_{round_index + 1:05}.png"


def _graph(positive: str, negative: str, seed: int) -> dict:
    """A ComfyUI API prompt with the concept's facts (app.js:406-408).

    The recipe is a Prompt Manager pair node, so with its embedded execution record
    the inspector reports both roles as execution evidence, which the panel badges
    "Execution recorded" (D21). One sampler keeps the seed unambiguous.
    """
    return {
        "1": {
            "class_type": "PromptManagerPair",
            "inputs": {"text": positive, "negative_text": negative, "clip": ["2", 1]},
        },
        "2": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": CONCEPT_MODEL},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["2", 0],
                "positive": ["1", 0],
                "negative": ["1", 1],
                "seed": seed,
                "steps": 28,
                "cfg": 6.5,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
            },
        },
        "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}},
    }


def _execution_record(positive: str, negative: str) -> dict:
    """The pair node's embedded execution evidence (utils/pair_execution.py)."""
    record = {
        "text": positive,
        "negative_text": negative,
        "positive_text": positive,
        "negative_effective_text": negative,
        "prepend_text": "",
        "append_text": "",
    }
    return {"version": 1, "nodes": {"1": {"records": [record]}}}


def _snapshot(text: str) -> PromptSnapshot:
    """A recorded prompt snapshot whose raw and effective texts are ``text``."""

    def known(value: str) -> TextEvidence:
        return TextEvidence(value, "recorded", "runtime_return", None)

    return PromptSnapshot(known(text), known(text), known(""), known(""))


def write_concept_png(path: Path, prompt_index: int, round_index: int) -> dict:
    """Write the concept's sample image ``round_index`` of prompt ``prompt_index``.

    Returns its facts: variant, negative, seed and dimensions.
    """
    prompt = CONCEPT_PROMPTS[prompt_index]
    index = round_index * len(CONCEPT_PROMPTS) + prompt_index
    variant = round_index % len(prompt.negatives)
    negative = prompt.negatives[variant][1]
    seed = concept_seed(prompt_index, round_index)
    width, height = size = CONCEPT_DIMENSIONS[index % len(CONCEPT_DIMENSIONS)]
    sky, ground, light = prompt.color
    info = PngInfo()
    info.add_text("prompt", json.dumps(_graph(prompt.positive, negative, seed)))
    info.add_text(
        EXECUTION_KEY, json.dumps(_execution_record(prompt.positive, negative))
    )
    with Image.new("RGB", size, sky) as image:
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, height * 2 // 3, width, height), fill=ground)
        radius = min(width, height) // 9
        left, top = width * 2 // 3, height // 6
        draw.ellipse((left, top, left + radius, top + radius), fill=light)
        image.save(path, pnginfo=info)
    return {
        "variant": variant,
        "negative": negative,
        "seed": seed,
        "width": width,
        "height": height,
    }


def _clear(api: PromptManagerAPI, root: Path) -> None:
    with api.db.model.get_connection() as conn:
        for table in _TABLES:
            conn.execute(f"DELETE FROM {table}")
    for name in ("output", "input"):
        shutil.rmtree(root / name)
        (root / name).mkdir()


def _record_run(
    api: PromptManagerAPI,
    *,
    epoch: str,
    item: int,
    context: RecipeContext,
    output: OutputReference,
    observed_at: int,
) -> str:
    """One finished execution with its recipe context and its one output."""
    job = str(uuid5(UUID(SESSION), f"concept-job-{item}"))
    start = make_start(SESSION, ((item, job, {}, {}), item), epoch, observed_at)
    api.db.write_execution_start(start)
    finish = make_finish(
        SESSION,
        item,
        epoch,
        SimpleNamespace(messages=[("execution_success", {"prompt_id": job})]),
        observed_at + 10,
    )
    api.db.write_execution_finish(finish)
    api.db.write_execution_contexts(
        start.run_id, epoch, ContextBundle((context,), "complete", "captured")
    )
    api.db.write_execution_outputs_for_finish(
        finish, OutputBundle((output,), "complete", "reported")
    )
    return start.run_id


def seed_workspace_concept(
    api: PromptManagerAPI, root: Path, *, extra_images: int = 0
) -> dict:
    """Replace this server's disposable rows and files with the concept's data.

    ``extra_images`` continues the concept's rounds (same prompts, dimensions and
    seed rule) after its 24 images, for bounded-rendering and Load more checks.
    """
    if isinstance(extra_images, bool) or not isinstance(extra_images, int):
        raise TypeError("extra_images must be an integer")
    if not 0 <= extra_images <= MAX_EXTRA_IMAGES:
        raise ValueError("extra_images is out of range")
    _clear(api, root)
    db = api.db
    epoch = db.get_execution_epoch()
    prompts = []
    for prompt in CONCEPT_PROMPTS:
        first_name, first_text = prompt.negatives[0]
        receipt = db.create_library_recipe(
            prompt.positive,
            first_text,
            expected_epoch=epoch,
            parent_metadata={
                "title": prompt.title,
                "category": prompt.category,
                "tags": list(prompt.tags),
                "rating": prompt.rating,
            },
        )
        positive_id = receipt["positive"]["id"]
        pair_ids = [receipt["pair"]["id"]]
        db.update_prompt_pair_metadata(pair_ids[0], title=first_name)
        for name, text in prompt.negatives[1:]:
            pair_ids.append(db.save_prompt_pair(prompt.positive, text, title=name))
        # Real queue-hook counting (D23): four runs, the last one now.
        db.record_prompt_use(positive_id, times=CONCEPT_RUN_COUNT)
        prompts.append(
            {"id": prompt.id, "positive_id": positive_id, "pair_ids": pair_ids}
        )

    folder = root / "output" / "concepts"
    folder.mkdir()
    roots = snapshot_output_roots("posix", str(root / "output"), str(root / "temp"))
    # The API package needs the host stubs the server installs before importing it.
    from py.api.images import _favorite_relative_key, _favorite_root_key

    favorite_root = _favorite_root_key(list(api._get_all_output_dirs())[0])
    total = len(CONCEPT_PROMPTS) * CONCEPT_IMAGE_ROUNDS + extra_images
    rounds = math.ceil(total / len(CONCEPT_PROMPTS))
    now = time.time()
    images, runs = [], []
    for round_index in range(rounds):
        for prompt_index, prompt in enumerate(CONCEPT_PROMPTS):
            index = round_index * len(CONCEPT_PROMPTS) + prompt_index
            if index >= total:
                break
            name = concept_filename(prompt, round_index)
            path = folder / name
            facts = write_concept_png(path, prompt_index, round_index)
            variant, negative, seed = facts["variant"], facts["negative"], facts["seed"]
            # The concept lists images newest first in this order.
            stamp = now - 60 - index * 60
            os.utime(path, (stamp, stamp))
            owner = prompts[prompt_index]
            image_id = db.link_image_to_prompt(owner["positive_id"], str(path))
            pair_id = owner["pair_ids"][variant]
            db.link_image_to_pair(pair_id, image_id, {"source": "concept fixture"})
            if concept_starred(prompt_index, round_index):
                db.set_image_favorite(
                    favorite_root, _favorite_relative_key(f"concepts/{name}")
                )
            context = RecipeContext(
                "3", _snapshot(prompt.positive), _snapshot(negative), "inferred"
            )
            reference = OutputReference(
                "4", 0, "output", "concepts", name, "posix", roots.output_key
            )
            runs.append(
                _record_run(
                    api,
                    epoch=epoch,
                    item=index,
                    context=context,
                    output=reference,
                    observed_at=int(stamp * 1000),
                )
            )
            images.append(
                {
                    "filename": name,
                    "image_id": image_id,
                    "pair_id": pair_id,
                    "prompt": prompt.id,
                    "variant": variant,
                    "seed": seed,
                    "width": facts["width"],
                    "height": facts["height"],
                }
            )
    with db.model.get_connection() as conn:
        counts = conn.execute(
            "SELECT COUNT(*), MIN(run_count), MAX(run_count) FROM prompts "
            "WHERE id IN (SELECT prompt_id FROM prompt_library_state)"
        ).fetchone()
        tags = [row[0] for row in conn.execute("SELECT name FROM tags ORDER BY id")]
    if tuple(counts) != (len(CONCEPT_PROMPTS), CONCEPT_RUN_COUNT, CONCEPT_RUN_COUNT):
        raise RuntimeError(f"Concept fixture run counts are not real: {tuple(counts)}")
    api._generate_gallery_thumbnails_sync(api._get_all_output_dirs(), (150, 150))
    api.invalidate_gallery_cache()
    return {
        "epoch": epoch,
        "prompts": prompts,
        "images": images,
        "runs": runs,
        "tags": tags,
    }
