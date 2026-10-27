"""Recorded run history names the saver that wrote a file (#386, #240).

When a file's graph alone cannot name its prompt (sampler branches that differ
only in their prompt and share one saver prefix), the inspector asks recorded
history which save node wrote the file and walks from that node, as the #381
file-name anchor does. A result the file alone resolves is never changed; a
recorded saver that names nothing certain leaves the file "several possible
prompts". The resolved sides are labelled as coming from the recorded run.
"""

import json
import unittest

from utils import prompt_pair_resolver as resolver
from utils.prompt_inspector import inspect_prompt_evidence

try:
    from tests import saver_anchor_graphs as shapes
except ImportError:  # discovered with ``-s tests``
    import saver_anchor_graphs as shapes


def _resolve(graph, **options):
    return resolver.resolve_prompt_pair(
        {"prompt": graph}, profile=resolver.INSPECTION_PROFILE, **options
    )


def _positive(result):
    side = result["positive"]
    return side["text"] if isinstance(side, dict) else None


def _evidence(graph, **options):
    return inspect_prompt_evidence({"prompt": json.dumps(graph)}, **options)


# ``branch_bench``'s savers are node ``N3`` of branch N (10s based).
def _saver(index):
    return str(index * 10 + 3)


def guider_dataset():
    """The dataset run's shape: SamplerCustomAdvanced branches on BasicGuider
    (no negative), every branch saved with one prefix."""
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
        "2": {"class_type": "EmptyLatentImage", "inputs": {"width": 64}},
        "4": {"class_type": "RandomNoise", "inputs": {"noise_seed": 1}},
        "5": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "6": {
            "class_type": "BasicScheduler",
            "inputs": {"model": ["1", 0], "steps": 8, "scheduler": "simple"},
        },
    }
    for index, text in enumerate(shapes.BRANCH_TEXTS, start=1):
        base = index * 10
        graph[str(base)] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": text, "clip": ["1", 1]},
        }
        graph[str(base + 4)] = {
            "class_type": "BasicGuider",
            "inputs": {"model": ["1", 0], "conditioning": [str(base), 0]},
        }
        graph[str(base + 1)] = {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["4", 0],
                "guider": [str(base + 4), 0],
                "sampler": ["5", 0],
                "sigmas": ["6", 0],
                "latent_image": ["2", 0],
            },
        }
        graph[str(base + 2)] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": [str(base + 1), 0], "vae": ["1", 2]},
        }
        graph[str(base + 3)] = {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": "dataset/IMG_GEN",
                "images": [str(base + 2), 0],
            },
        }
    return graph


class TestRecordedSaverInResolver(unittest.TestCase):
    def test_the_recorded_saver_picks_its_branch_of_a_shared_prefix(self):
        for index, text in enumerate(shapes.BRANCH_TEXTS, start=1):
            with self.subTest(branch=index):
                result = _resolve(
                    shapes.shared_prefix(),
                    file_name="IMG_GEN_00371_.png",
                    recorded_saver_id=_saver(index),
                )
                self.assertEqual(result["state"], "resolved")
                self.assertEqual(_positive(result), text)
                self.assertEqual(result["sampler_id"], str(index * 10 + 1))

    def test_without_a_recorded_saver_the_shared_prefix_stays_several(self):
        result = _resolve(shapes.shared_prefix(), file_name="IMG_GEN_00371_.png")
        self.assertEqual(result["state"], "ambiguous")

    def test_a_recorded_saver_the_graph_lacks_anchors_nothing(self):
        for node in ("missing", "3", "2"):
            with self.subTest(node=node):
                result = _resolve(shapes.shared_prefix(), recorded_saver_id=node)
                self.assertEqual(result["state"], "ambiguous")

    def test_a_recorded_saver_of_an_unknown_class_anchors(self):
        graph = shapes.branch_bench(
            prefixes=["dataset/IMG_GEN"] * len(shapes.BRANCH_TEXTS),
            saver="VendorSaveImage",
        )
        result = _resolve(graph, recorded_saver_id=_saver(2))
        self.assertEqual(_positive(result), shapes.BRANCH_TEXTS[1])

    def test_the_default_profile_ignores_a_recorded_saver(self):
        result = resolver.resolve_prompt_pair(
            {"prompt": shapes.shared_prefix()}, recorded_saver_id=_saver(1)
        )
        self.assertEqual(result["state"], "ambiguous")


class TestRecordedSaverInInspector(unittest.TestCase):
    def test_the_inspector_shows_the_recorded_branch_labelled_as_recorded(self):
        evidence = _evidence(
            shapes.shared_prefix(),
            file_name="IMG_GEN_00371_.png",
            recorded_saver_id=_saver(3),
        )
        self.assertEqual(evidence["resolution"]["state"], "resolved")
        positive = evidence["prompts"]["positive"]
        self.assertEqual(positive["text"], shapes.BRANCH_TEXTS[2])
        self.assertEqual(positive["source"], "recorded-run")
        self.assertEqual(evidence["prompts"]["negative"]["source"], "recorded-run")

    def test_a_guider_branch_without_a_negative_shows_its_positive(self):
        self.assertEqual(
            _evidence(guider_dataset(), file_name="IMG_GEN_00371_.png")["resolution"][
                "state"
            ],
            "ambiguous",
        )
        evidence = _evidence(
            guider_dataset(), file_name="IMG_GEN_00371_.png", recorded_saver_id="23"
        )
        self.assertEqual(
            evidence["prompts"]["positive"],
            {
                "status": "resolved",
                "text": shapes.BRANCH_TEXTS[1],
                "source": "recorded-run",
            },
        )
        self.assertEqual(evidence["prompts"]["negative"]["status"], "absent")

    def test_a_file_name_naming_another_saver_contradicts_history(self):
        # The bench file name names branch 2's saver; history says branch 4's.
        graph = shapes.branch_bench()
        for index in (1, 3, 4):
            graph[_saver(index)]["inputs"]["filename_prefix"] = "bench/run/shared"
        result = _resolve(
            graph, file_name="s45_branch_2_00001_.png", recorded_saver_id=_saver(4)
        )
        self.assertIs(result["recorded_anchor"], False)
        evidence = _evidence(
            shapes.shared_prefix(),
            file_name="IMG_GEN_00001_.png",
            recorded_saver_id=_saver(4),
        )
        self.assertEqual(
            evidence["prompts"]["positive"]["text"], shapes.BRANCH_TEXTS[3]
        )

    def test_a_file_that_resolves_alone_keeps_its_own_result(self):
        # The file name names branch 2; a recorded saver never overrides it.
        evidence = _evidence(
            shapes.branch_bench(),
            file_name="s45_branch_2_00001_.png",
            recorded_saver_id=_saver(4),
        )
        positive = evidence["prompts"]["positive"]
        self.assertEqual(positive["text"], shapes.BRANCH_TEXTS[1])
        self.assertEqual(positive["source"], "graph-inferred")

    def test_a_recorded_saver_that_names_nothing_keeps_several_prompts(self):
        evidence = _evidence(shapes.shared_prefix(), recorded_saver_id="missing")
        self.assertEqual(evidence["resolution"]["state"], "ambiguous")
        self.assertEqual(evidence["prompts"]["positive"]["status"], "ambiguous")

    def test_no_recorded_saver_is_unchanged(self):
        self.assertEqual(
            _evidence(shapes.shared_prefix(), recorded_saver_id=None),
            _evidence(shapes.shared_prefix()),
        )


LLM_TEXT = "a red vintage car parked by the sea, golden hour"


def text_generate_run():
    """The maintainer's moss run: a language model writes PromptManager's text."""
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}},
        "2": {"class_type": "EmptyLatentImage", "inputs": {"width": 64}},
        "17": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "a car"}},
        "14": {
            "class_type": "PrimitiveStringMultiline",
            "inputs": {"value": "You are a prompt engineer."},
        },
        "11": {
            "class_type": "TextGenerate",
            "inputs": {
                "prompt": ["17", 0],
                "system_prompt": ["14", 0],
                "clip": ["1", 1],
                "max_length": 4096,
                "sampling_mode.seed": 0,
            },
        },
        "36": {
            "class_type": "PromptManagerPair",
            "inputs": {
                "text": ["11", 0],
                "negative_text": "",
                "prepend_text": "",
                "append_text": "",
                "clip": ["1", 1],
            },
        },
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 1,
                "steps": 20,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["36", 0],
                "negative": ["36", 1],
                "latent_image": ["2", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["7", 0], "vae": ["1", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "moss", "images": ["8", 0]},
        },
    }


def two_generated_branches():
    """``text_generate_run`` plus a second generated branch saved by an unknown
    saver and also previewed."""
    graph = text_generate_run()
    graph["31"] = {**graph["11"], "inputs": {**graph["11"]["inputs"]}}
    graph["37"] = {
        "class_type": "PromptManagerPair",
        "inputs": {**graph["36"]["inputs"], "text": ["31", 0]},
    }
    graph["20"] = {
        "class_type": "KSampler",
        "inputs": {
            **graph["7"]["inputs"],
            "positive": ["37", 0],
            "negative": ["37", 1],
        },
    }
    graph["21"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["20", 0], "vae": ["1", 2]},
    }
    graph["22"] = {
        "class_type": "Image Save",
        "inputs": {"filename_prefix": "other", "images": ["21", 0]},
    }
    graph["23"] = {"class_type": "PreviewImage", "inputs": {"images": ["21", 0]}}
    return graph


class TestRecordedTexts(unittest.TestCase):
    """The texts PromptManager recorded at execution, per sampler of the run."""

    def test_a_run_time_text_shows_what_the_run_recorded(self):
        alone = _evidence(text_generate_run(), file_name="moss_00026_.png")
        self.assertEqual(alone["resolution"]["reason_code"], "runtime-value")
        evidence = _evidence(
            text_generate_run(),
            file_name="moss_00026_.png",
            recorded_saver_id="9",
            recorded_texts={"7": {"positive": LLM_TEXT, "negative": ""}},
        )
        self.assertEqual(
            evidence["prompts"],
            {
                "positive": {
                    "status": "resolved",
                    "text": LLM_TEXT,
                    "source": "execution",
                },
                "negative": {"status": "resolved", "text": "", "source": "execution"},
            },
        )
        self.assertEqual(evidence["resolution"]["state"], "resolved")
        self.assertEqual(evidence["resolution"]["sampler_id"], "7")

    def test_an_unrecorded_negative_stays_open(self):
        evidence = _evidence(
            text_generate_run(),
            recorded_saver_id="9",
            recorded_texts={"7": {"positive": LLM_TEXT, "negative": None}},
        )
        self.assertEqual(evidence["prompts"]["positive"]["text"], LLM_TEXT)
        self.assertEqual(evidence["prompts"]["negative"]["status"], "unresolved")

    def test_texts_that_do_not_name_the_open_sampler_change_nothing(self):
        alone = _evidence(text_generate_run())
        for texts in (
            {"8": {"positive": LLM_TEXT, "negative": ""}},
            {"7": {"positive": None, "negative": ""}},
            {"7": {"positive": "  ", "negative": ""}},
            {"7": "not roles"},
            {},
        ):
            with self.subTest(texts=texts):
                self.assertEqual(
                    _evidence(
                        text_generate_run(), recorded_saver_id="9", recorded_texts=texts
                    ),
                    alone,
                )

    def test_texts_need_the_saver_history_recorded_to_lead_to_the_sampler(self):
        texts = {"7": {"positive": LLM_TEXT, "negative": ""}}
        alone = _evidence(text_generate_run())
        for saver in (None, "missing", "2"):
            with self.subTest(saver=saver):
                self.assertEqual(
                    _evidence(
                        text_generate_run(),
                        recorded_saver_id=saver,
                        recorded_texts=texts,
                    ),
                    alone,
                )

    def test_texts_follow_the_recorded_saver_not_another_sampler(self):
        # Review: the texts are those of the sampler history's saver leads to.
        graph = two_generated_branches()
        texts = {
            "7": {"positive": "STAGE X TEXT", "negative": ""},
            "20": {"positive": "STAGE Y TEXT", "negative": ""},
        }
        for saver, text, sampler in (
            ("22", "STAGE Y TEXT", "20"),
            ("9", "STAGE X TEXT", "7"),
        ):
            with self.subTest(saver=saver):
                evidence = _evidence(
                    graph,
                    file_name="renamed_00001_.png",
                    recorded_saver_id=saver,
                    recorded_texts=texts,
                )
                self.assertEqual(evidence["prompts"]["positive"]["text"], text)
                self.assertEqual(evidence["resolution"]["sampler_id"], sampler)


if __name__ == "__main__":
    unittest.main()
