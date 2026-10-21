"""Several sampler branches that share one prompt pair resolve to that pair (#249).

The shape is a LoRA epoch-compare grid: one sampler per LoRA epoch, every
sampler fed by the same positive ``CLIPTextEncode`` and the same
``ConditioningZeroOut`` negative, every decoded image labelled, batched and
concatenated into one grid that a single ``SaveImage`` writes. Which branch
made which tile does not matter to the prompt: every branch used the same pair.
Branches that differ stay ambiguous.
"""

import unittest

from utils import prompt_pair_resolver as resolver
from utils.prompt_inspector import inspect_prompt_evidence
from utils.prompt_pair_resolver import resolve_prompt_pair

POSITIVE = "a woman holding a coffee cup, in a beanie, sitting by a window"
OTHER = "a man reading a newspaper on a park bench"
NEGATIVE = "blurry, low quality"
# Each profile with the negative it can read: inspection reads the zero-out.
PROFILES = {
    "default": (resolver.DEFAULT_PROFILE, NEGATIVE),
    "inspection": (resolver.INSPECTION_PROFILE, None),
}


def _grid(epochs: int = 4, positives: dict = None, negative: str = None) -> dict:
    """A compare grid; ``positives`` maps an epoch to its own positive text.

    The negative is a ``ConditioningZeroOut`` of the positive, or with
    ``negative`` an encoded text (the default profile cannot read a zero-out).
    """
    graph = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "base.safetensors", "weight_dtype": "default"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": "te.safetensors", "type": "stable_diffusion"},
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "vae.safetensors"}},
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": POSITIVE, "clip": ["2", 0]},
        },
        "5": (
            {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}}
            if negative is None
            else {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative, "clip": ["2", 0]},
            }
        ),
        "6": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 512, "height": 512, "batch_size": 1},
        },
    }
    batch = {}
    for epoch in range(epochs):
        base = 10 + epoch * 10
        lora, sampler, decode, label = (str(base + offset) for offset in range(4))
        positive = "4"
        if positives and epoch in positives:
            positive = str(base + 5)
            graph[positive] = {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": positives[epoch], "clip": ["2", 0]},
            }
        graph[lora] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "lora_name": f"subject_epoch_{epoch:02d}.safetensors",
                "strength_model": 1.0,
                "model": ["1", 0],
            },
        }
        graph[sampler] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": 42,
                "steps": 8,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": [lora, 0],
                "positive": [positive, 0],
                "negative": ["5", 0],
                "latent_image": ["6", 0],
            },
        }
        graph[decode] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": [sampler, 0], "vae": ["3", 0]},
        }
        graph[label] = {
            "class_type": "AddLabel",
            "inputs": {"text": f"epoch {epoch}", "image": [decode, 0]},
        }
        batch[f"images.image{epoch}"] = [label, 0]
    graph["900"] = {"class_type": "BatchImagesNode", "inputs": batch}
    graph["901"] = {
        "class_type": "ImageConcatFromBatch",
        "inputs": {"num_columns": 2, "images": ["900", 0]},
    }
    graph["902"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "compare/grid", "images": ["901", 0]},
    }
    return graph


def _texts(result: dict) -> tuple:
    return tuple(
        result[role]["text"] if isinstance(result[role], dict) else None
        for role in ("positive", "negative")
    )


class TestIdenticalSamplerBranches(unittest.TestCase):
    def test_grid_of_identical_branches_resolves_to_the_shared_pair(self):
        for name, (profile, negative) in PROFILES.items():
            with self.subTest(profile=name):
                result = resolve_prompt_pair(
                    {"prompt": _grid(negative=negative)}, profile=profile
                )
                self.assertEqual(result["state"], "resolved")
                self.assertEqual(_texts(result), (POSITIVE, negative or ""))
                # No one branch is named: every branch made this pair.
                self.assertIsNone(result["sampler_id"])
                self.assertEqual(result["reason"], resolver.IDENTICAL_BRANCHES_REASON)
                # Every branch stays listed as the evidence for the shared pair.
                self.assertEqual(
                    [item["sampler_id"] for item in result["candidates"]],
                    ["11", "21", "31", "41"],
                )
                self.assertEqual(
                    {_texts(item) for item in result["candidates"]},
                    {(POSITIVE, negative or "")},
                )
                self.assertEqual(result["reason_code"], None)
                if negative is None:
                    self.assertEqual(
                        result["negative"]["provenance"]["text_source"],
                        "zeroed-conditioning",
                    )

    def test_default_profile_keeps_an_unreadable_zero_out_grid_ambiguous(self):
        # Ingestion cannot read ConditioningZeroOut: every branch is unresolved,
        # so the grid stays ambiguous and the scan keeps its legacy fallback.
        result = resolve_prompt_pair({"prompt": _grid()})
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(result["reason"], "Multiple sampler branches")
        self.assertEqual(
            {item["state"] for item in result["candidates"]}, {"unresolved"}
        )

    def test_single_branch_grid_still_names_its_sampler(self):
        for name, (profile, negative) in PROFILES.items():
            with self.subTest(profile=name):
                result = resolve_prompt_pair(
                    {"prompt": _grid(epochs=1, negative=negative)}, profile=profile
                )
                self.assertEqual(result["state"], "resolved")
                self.assertEqual(result["sampler_id"], "11")
                self.assertEqual(result["reason"], "")

    def test_branches_with_different_prompts_stay_ambiguous(self):
        for name, (profile, negative) in PROFILES.items():
            with self.subTest(profile=name):
                source = _grid(positives={2: OTHER}, negative=negative)
                result = resolve_prompt_pair({"prompt": source}, profile=profile)
                self.assertEqual(result["state"], "ambiguous")
                self.assertEqual(result["reason"], "Multiple sampler branches")
                self.assertIsNone(result["positive"])
                self.assertEqual(
                    sorted(_texts(item)[0] for item in result["candidates"]),
                    sorted([POSITIVE] * 3 + [OTHER]),
                )

    def test_a_branch_that_cannot_be_read_keeps_the_grid_ambiguous(self):
        for name, (profile, negative) in PROFILES.items():
            with self.subTest(profile=name):
                source = _grid(negative=negative)
                source["31"]["inputs"]["negative"] = ["800", 0]
                source["800"] = {"class_type": "MysteryConditioning", "inputs": {}}
                result = resolve_prompt_pair({"prompt": source}, profile=profile)
                self.assertEqual(result["state"], "ambiguous")
                self.assertEqual(result["reason"], "Multiple sampler branches")

    def test_identical_unreadable_branches_are_not_collapsed(self):
        source = _grid(epochs=2)
        source["5"] = {"class_type": "MysteryConditioning", "inputs": {}}
        for name, (profile, _negative) in PROFILES.items():
            with self.subTest(profile=name):
                result = resolve_prompt_pair({"prompt": source}, profile=profile)
                self.assertEqual(result["state"], "ambiguous")

    def test_same_text_with_a_different_recipe_stays_ambiguous(self):
        adapters = {
            ("RecipeText", 0): resolver.OutputAdapter(
                "text", "conditioning", compose=True
            )
        }
        source = _grid(epochs=2, negative=NEGATIVE)
        source["21"]["inputs"]["positive"] = ["700", 0]
        source["700"] = {
            "class_type": "RecipeText",
            "inputs": {"prepend_text": "a woman", "text": "holding a coffee cup"},
        }
        alone = resolve_prompt_pair(
            {"prompt": source}, output_node_id="21", adapters=adapters
        )
        # Branch "10" reads the same effective text through a plain encoder.
        source["4"]["inputs"]["text"] = alone["positive"]["text"]
        result = resolve_prompt_pair({"prompt": source}, adapters=adapters)
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(
            {_texts(item) for item in result["candidates"]},
            {(alone["positive"]["text"], NEGATIVE)},
        )
        self.assertEqual(
            {item["positive"]["storage_text"] for item in result["candidates"]},
            {alone["positive"]["text"], "holding a coffee cup"},
        )

    def test_inspector_shows_the_positive_and_an_explicitly_empty_negative(self):
        evidence = inspect_prompt_evidence({"prompt": _grid()})
        prompts = evidence["prompts"]
        self.assertEqual(prompts["positive"]["status"], "resolved")
        self.assertEqual(prompts["positive"]["text"], POSITIVE)
        self.assertEqual(prompts["negative"]["status"], "resolved")
        self.assertEqual(prompts["negative"]["text"], "")
        self.assertEqual(prompts["negative"]["source"], "zeroed-conditioning")
        self.assertEqual(evidence["resolution"]["state"], "resolved")
        self.assertIsNone(evidence["resolution"]["sampler_id"])

    def test_inspector_keeps_differing_grids_ambiguous(self):
        evidence = inspect_prompt_evidence({"prompt": _grid(positives={0: OTHER})})
        self.assertEqual(evidence["resolution"]["state"], "ambiguous")
        self.assertEqual(evidence["prompts"]["positive"]["status"], "ambiguous")
        self.assertNotIn(POSITIVE, str(evidence["prompts"]))


if __name__ == "__main__":
    unittest.main()
