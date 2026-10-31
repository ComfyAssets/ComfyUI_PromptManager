"""Metadata scanning enriches known pairs and conservatively leaves conflicts."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from tests.test_api_admin import AdminAPITestCase
from utils.hashing import generate_prompt_hash


def graph(positive="cat", negative="blur"):
    return {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": positive}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": negative}},
        "3": {
            "class_type": "KSampler",
            "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
        },
    }


def sampled_graph(positive="cat", negative="blur"):
    """A KSampler with a model link, as real workflows have."""
    g = graph(positive, negative)
    g["4"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m"}}
    g["3"]["inputs"]["model"] = ["4", 0]
    return g


class TestPairScan(AdminAPITestCase):
    def test_pair_scan_repeated_is_unchanged_and_empty_negative_is_supported(self):
        path = self.output_dir / "pair.png"
        meta = {"prompt": json.dumps(graph(negative=""))}
        self.assertEqual(self.api._ingest_scanned_file(path, meta), "added")
        self.assertEqual(self.api._ingest_scanned_file(path, meta), "unchanged")
        pairs = self.api.db.list_prompt_pairs()["pairs"]
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["negative_text"], "")
        self.assertEqual(pairs[0]["run_count"], 0)

    def _scanned(self, name, prompt_graph):
        meta = {"prompt": json.dumps(prompt_graph)}
        return self.api._ingest_scanned_file(self.output_dir / name, meta)

    def _texts(self):
        return [p["text"] for p in self.api.db.get_recent_prompts()["prompts"]]

    def test_samplers_sharing_one_prompt_link_that_prompt(self):
        # #249: every branch resolves to one exact pair, so the pair is ingested.
        g = sampled_graph()
        g["5"] = {**g["3"]}
        self.assertEqual(self._scanned("multi.png", g), "added")
        self.assertEqual(sorted(self._texts()), ["blur", "cat"])
        pairs = self.api.db.list_prompt_pairs()["pairs"]
        self.assertEqual(
            [(p["positive_text"], p["negative_text"]) for p in pairs], [("cat", "blur")]
        )

    def test_samplers_with_different_prompts_link_the_first_sampler_prompt(self):
        # The prompt the inspector and the legacy panels show for this file.
        g = sampled_graph()
        g["6"] = {"class_type": "CLIPTextEncode", "inputs": {"text": "dog"}}
        g["12"] = {**g["3"], "inputs": {**g["3"]["inputs"], "positive": ["6", 0]}}
        self.assertEqual(self._scanned("different.png", g), "added")
        self.assertEqual(self._texts(), ["cat"])

    def test_an_unsupported_pass_through_still_links_the_traced_prompt(self):
        g = sampled_graph()
        g["7"] = {
            "class_type": "MyConditioningTweak",
            "inputs": {"conditioning": ["1", 0]},
        }
        g["3"]["inputs"]["positive"] = ["7", 0]
        g["5"] = {**g["3"]}
        self.assertEqual(self._scanned("tweak.png", g), "added")
        self.assertEqual(self._texts(), ["cat"])

    def test_a_graph_without_a_traceable_prompt_saves_nothing(self):
        g = sampled_graph()
        g["5"] = {**g["3"]}
        for sampler in ("3", "5"):
            g[sampler]["inputs"]["positive"] = ["9", 0]
        g["9"] = {
            "class_type": "BasicGuider",
            "inputs": {"width": 2048, "height": 2048},
        }
        # Ambiguous with nothing to choose: unresolved, never a review item.
        self.assertEqual(self._scanned("untraceable.png", g), "unresolved")
        self.assertEqual(self.api.db.list_prompt_pairs()["total"], 0)
        self.assertEqual(self._texts(), [])

    def test_a_prompt_search_list_query_is_never_stored_as_a_prompt(self):
        # #548: the search text was stored and every batch image linked to it.
        g = sampled_graph()
        g["10"] = {
            "class_type": "PromptSearchList",
            "inputs": {"text": "fox", "category": "", "tags": "", "limit": 50},
        }
        g["1"]["inputs"]["text"] = ["10", 0]
        self.assertEqual(self._scanned("search.png", g), "unresolved")
        self.assertEqual(self._texts(), [])
        self.assertEqual(self.api.db.list_prompt_pairs()["total"], 0)

    def test_numeric_widgets_are_never_stored_as_a_prompt(self):
        # The pre-redesign scan stored "2048" from a resolution widget here.
        g = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": ["8", 0]}},
            "8": {"class_type": "DisplayText", "inputs": {"text_size": 2048}},
            "3": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {"guider": ["4", 0], "sigmas": ["5", 0]},
            },
            "4": {"class_type": "BasicGuider", "inputs": {"conditioning": ["1", 0]}},
        }
        self.assertIn(
            self._scanned("numbers.png", g), ("ambiguous", "unresolved", "found")
        )
        self.assertEqual(self._texts(), [])

    def test_batch_exposes_pair_counts_and_actual_unchanged_status(self):
        meta = {"parameters": "cat\nNegative prompt: blur\nSteps: 20, Sampler: Euler"}
        batch = [(Path(self.output_dir / "a.png"), meta)]
        first = self.api._ingest_scan_batch_sync(batch)
        self.assertEqual(first["pairs_added"], 1)
        self.assertEqual(first["pair_links_added"], 1)
        second = self.api._ingest_scan_batch_sync(batch)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(second["linked"], 0)
        self.assertEqual(second["pairs_added"], 0)

    def test_legacy_single_prompt_second_pass_not_reported_as_new_link(self):
        # A sampler-less graph: one encoder wired into a guider (#252).
        single = {
            "1": graph()["1"],
            "2": {"class_type": "BasicGuider", "inputs": {"conditioning": ["1", 0]}},
        }
        meta = {"prompt": json.dumps(single)}
        path = self.output_dir / "old.png"
        self.assertEqual(self.api._ingest_scanned_file(path, meta), "added")
        self.assertEqual(self.api._ingest_scanned_file(path, meta), "unchanged")

    async def test_cancel_finishes_at_safe_boundary_and_restart_is_idempotent(self):
        from py.api.jobs import Job

        job = Job("scan", "Scan")
        self.api._scan_job = job
        response = await self.client.post("/prompt_manager/scan/cancel")
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.json())["cancellation_requested"])
        counts = self.api._ingest_scan_batch_sync(
            [(self.output_dir / "cancel.png", {"prompt": json.dumps(graph())})]
        )
        self.assertEqual(counts["processed"], 0)
        self.assertEqual(self.api.db.list_prompt_pairs()["total"], 0)
        job.finish()
        self.api._scan_job = Job("scan", "Scan")
        first = self.api._ingest_scan_batch_sync(
            [(self.output_dir / "cancel.png", {"prompt": json.dumps(graph())})]
        )
        self.assertEqual(first["pairs_added"], 1)
        self.assertEqual(
            self.api._ingest_scan_batch_sync(
                [(self.output_dir / "cancel.png", {"prompt": json.dumps(graph())})]
            )["unchanged"],
            1,
        )

    def test_legacy_found_outcome_is_not_counted_twice(self):
        result = self.api._ingest_scan_batch_sync(
            [(self.output_dir / "blank.png", {"parameters": "Negative prompt: blur"})]
        )
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["found"], 1)

    def test_ambiguous_a1111_does_not_fall_back_to_legacy_guess(self):
        meta = {
            "parameters": (
                "cat\nNegative prompt: blur\nNegative prompt: noise"
                "\nSteps: 20, Sampler: Euler"
            )
        }
        # No candidate to choose: reported unresolved, still not guessed.
        self.assertEqual(
            self.api._ingest_scanned_file(self.output_dir / "ambiguous.png", meta),
            "unresolved",
        )
        self.assertEqual(self.api.db.get_recent_prompts()["prompts"], [])

    def _nan_scanned(self, name, prompt_graph):
        """Scan ``prompt_graph`` as ComfyUI writes it: ``is_changed`` is NaN."""
        stamped = {
            node_id: {**node, "is_changed": [float("nan")]}
            for node_id, node in prompt_graph.items()
        }
        text = json.dumps(stamped)
        self.assertIn("NaN", text)
        return self._scanned_text(name, text)

    def _scanned_text(self, name, prompt_text):
        meta = {"prompt": prompt_text}
        return self.api._ingest_scanned_file(self.output_dir / name, meta)

    @staticmethod
    def _hires_fix_graph(positive):
        """A PromptManager prompt on a base sampler and a hires-fix sampler.

        The base latent comes from a custom batch node and the hires pass from
        a custom image filter: no sampler branch traces, so the scan links the
        prompt the legacy pages show.
        """
        return {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "m.safetensors"},
            },
            "2": {
                "class_type": "MyTriggerWords",
                "inputs": {"trigger_words": ["1", 2]},
            },
            "3": {
                "class_type": "PromptManager",
                "inputs": {
                    "text": positive,
                    "prepend_text": ["2", 0],
                    "append_text": "",
                    "clip": ["1", 1],
                },
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "blur", "clip": ["1", 1]},
            },
            "5": {"class_type": "MyLatentBatch", "inputs": {"batch_size": 6}},
            "6": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 7,
                    "model": ["1", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0],
                },
            },
            "7": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["6", 0], "vae": ["1", 2]},
            },
            "8": {"class_type": "MyImageFilter", "inputs": {"images": ["7", 0]}},
            "9": {
                "class_type": "VAEEncode",
                "inputs": {"pixels": ["8", 0], "vae": ["1", 2]},
            },
            "14": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 7,
                    "denoise": 0.25,
                    "model": ["1", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["9", 0],
                },
            },
            "15": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["14", 0], "vae": ["1", 2]},
            },
            "16": {"class_type": "SaveImage", "inputs": {"images": ["15", 0]}},
        }

    def test_hires_fix_prompt_links_with_or_without_nan_is_changed(self):
        # Legacy parity: the legacy pages read NaN as null before parsing, so they
        # showed this prompt; the scan left NaN-stamped files in review.
        for name, scan in (("plain.png", None), ("stamped.png", "nan")):
            with self.subTest(name=name):
                g = self._hires_fix_graph("cat on a sofa")
                if scan:
                    status = self._nan_scanned(name, g)
                else:
                    status = self._scanned(name, g)
                self.assertIn(status, ("added", "linked"))
                self.assertEqual(self._texts(), ["cat on a sofa"])

    def test_untraced_single_sampler_links_the_legacy_prompt_with_nan(self):
        g = self._hires_fix_graph("cat on a sofa")
        for node_id in ("7", "8", "9", "14", "15"):
            del g[node_id]
        g["16"]["inputs"]["images"] = ["6", 0]
        self.assertEqual(self._nan_scanned("single.png", g), "added")
        self.assertEqual(self._texts(), ["cat on a sofa"])

    def test_nan_inside_prompt_text_is_kept(self):
        # Only the NaN number reads as null; legacy's regex also rewrote text.
        g = self._hires_fix_graph("NaN cat, value: NaN")
        self.assertEqual(self._nan_scanned("text.png", g), "added")
        self.assertEqual(self._texts(), ["NaN cat, value: NaN"])

    def test_infinity_is_still_unreadable_like_the_legacy_pages(self):
        text = json.dumps(self._hires_fix_graph("cat")).replace(
            '"seed": 7', '"seed": Infinity'
        )
        self.assertIn(self._scanned_text("inf.png", text), ("ambiguous", "unresolved"))
        self.assertEqual(self._texts(), [])

    def test_raw_png_workflow_is_stored_as_object_and_repeat_is_unchanged(self):
        path = self.output_dir / "workflow.png"
        workflow = {"nodes": [{"id": 1, "type": "PromptManagerPair"}]}
        metadata = {"prompt": json.dumps(graph()), "workflow": json.dumps(workflow)}
        self.assertEqual(self.api._ingest_scanned_file(path, metadata), "added")
        self.assertEqual(
            self.api.db.get_image_prompt_info(str(path))["workflow_data"], workflow
        )
        self.assertEqual(self.api._ingest_scanned_file(path, metadata), "unchanged")


def flux_graph(text="a red fox", encoder="CLIPTextEncode"):
    """A sampler-less Flux graph: the prompt reaches a guider's conditioning (#252)."""
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux.safetensors"}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "t5"}},
        "6": {"class_type": encoder, "inputs": {"text": text, "clip": ["2", 0]}},
        "22": {
            "class_type": "BasicGuider",
            "inputs": {"model": ["1", 0], "conditioning": ["6", 0]},
        },
        "13": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {"guider": ["22", 0], "sigmas": ["17", 0]},
        },
        "17": {"class_type": "BasicScheduler", "inputs": {"steps": 20}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["13", 0]}},
    }


class TestSamplerLessScan(AdminAPITestCase):
    """Graphs where no node takes a positive or negative input (#252)."""

    def _scanned(self, name, prompt_graph, workflow=None):
        meta = {"prompt": json.dumps(prompt_graph)}
        if workflow is not None:
            meta["workflow"] = json.dumps(workflow)
        return self.api._ingest_scanned_file(self.output_dir / name, meta)

    def _texts(self):
        return [p["text"] for p in self.api.db.get_recent_prompts()["prompts"]]

    def test_the_one_wired_prompt_is_linked(self):
        self.assertEqual(self._scanned("flux.png", flux_graph()), "added")
        self.assertEqual(self._texts(), ["a red fox"])

    def test_a_text_node_feeding_the_encoder_is_one_prompt(self):
        g = flux_graph(text=["30", 0])
        g["30"] = {"class_type": "TextInput", "inputs": {"text": "a red fox"}}
        self.assertEqual(self._scanned("textinput.png", g), "added")
        self.assertEqual(self._texts(), ["a red fox"])

    def test_a_display_node_that_feeds_nothing_is_never_the_prompt(self):
        # A real scan stored a "=== GIT CLONE REPORT ===" display text this way.
        g = flux_graph()
        g["40"] = {
            "class_type": "easy showAnything",
            "inputs": {"text": "=== GIT CLONE REPORT ===\nTotal repositories: 1"},
        }
        self.assertEqual(self._scanned("report.png", g), "added")
        self.assertEqual(self._texts(), ["a red fox"])

    def test_only_unconsumed_texts_store_nothing(self):
        g = {
            "40": {"class_type": "easy showAnything", "inputs": {"text": "report"}},
            "41": {"class_type": "Textbox", "inputs": {"text": "install note"}},
        }
        self.assertEqual(self._scanned("notes.png", g), "found")
        self.assertEqual(self._texts(), [])

    def test_two_wired_texts_are_ambiguous_and_stay_unlinked(self):
        # A LoRA loader's text input is wired too: the scan stored "<lora:...>".
        g = flux_graph()
        g["50"] = {
            "class_type": "Lora Loader (LoraManager)",
            "inputs": {
                "model": ["1", 0],
                "clip": ["2", 0],
                "text": "<lora:style:1.00>",
            },
        }
        g["22"]["inputs"]["model"] = ["50", 0]
        g["6"]["inputs"]["clip"] = ["50", 1]
        self.assertEqual(self._scanned("lora.png", g), "unresolved")
        self.assertEqual(self._texts(), [])

    def test_an_untraceable_wired_text_makes_the_graph_ambiguous(self):
        # The encoder's text comes from a node no rule reads: never guess the other.
        g = flux_graph(text=["60", 0])
        g["60"] = {"class_type": "SomeLLMNode", "inputs": {"seed": 1}}
        g["50"] = {
            "class_type": "Lora Loader (LoraManager)",
            "inputs": {"model": ["1", 0], "text": "<lora:style:1.00>"},
        }
        g["22"]["inputs"]["model"] = ["50", 0]
        self.assertEqual(self._scanned("llm.png", g), "unresolved")
        self.assertEqual(self._texts(), [])

    def test_this_repositorys_prompt_node_is_authoritative(self):
        g = flux_graph(text="a red fox", encoder="PromptManager")
        g["50"] = {
            "class_type": "Lora Loader (LoraManager)",
            "inputs": {"model": ["1", 0], "text": "<lora:style:1.00>"},
        }
        g["22"]["inputs"]["model"] = ["50", 0]
        self.assertEqual(self._scanned("own.png", g), "added")
        self.assertEqual(self._texts(), ["a red fox"])

    def test_a_numeric_text_is_never_stored_as_a_prompt(self):
        self.assertEqual(self._scanned("number.png", flux_graph(text="2048")), "found")
        self.assertEqual(self._texts(), [])

    def test_workflow_widget_values_are_never_read(self):
        # The API text is linked from a node no rule reads; the UI workflow
        # still holds a stale widget value, which the old scan stored.
        g = flux_graph(text=["70", 0])
        g["70"] = {"class_type": "PromptBuilder", "inputs": {"prompt": "x", "n": 2}}
        workflow = {
            "nodes": [
                {"id": 6, "type": "CLIPTextEncode", "widgets_values": ["stale text"]}
            ]
        }
        self.assertEqual(self._scanned("stale.png", g, workflow), "unresolved")
        self.assertEqual(self._texts(), [])

    def test_a_lora_loaders_trigger_word_output_is_not_its_text(self):
        # The encoder reads the loader's trigger-word output (slot 2), not its
        # text input: the prompt is unknown, never the "<lora:...>" syntax.
        g = flux_graph(text=["50", 2])
        g["50"] = {
            "class_type": "Lora Loader (LoraManager)",
            "inputs": {"model": ["1", 0], "text": "<lora:style:1.00>"},
        }
        g["22"]["inputs"]["model"] = ["50", 0]
        self.assertEqual(self._scanned("trigger.png", g), "unresolved")
        self.assertEqual(self._texts(), [])

    def test_a_seeded_text_node_holds_a_template_not_the_prompt(self):
        g = flux_graph(text=["80", 0])
        g["80"] = {
            "class_type": "RandomPrompt",
            "inputs": {"text": "a {red|blue} fox", "seed": 5},
        }
        self.assertEqual(self._scanned("random.png", g), "unresolved")
        self.assertEqual(self._texts(), [])

    def test_a1111_parameters_beside_an_undecided_graph_are_read(self):
        # Metadata savers write both; the old scan read the A1111 positive.
        g = flux_graph()
        g["50"] = {
            "class_type": "Lora Loader (LoraManager)",
            "inputs": {"model": ["1", 0], "text": "<lora:style:1.00>"},
        }
        g["22"]["inputs"]["model"] = ["50", 0]
        meta = {
            "prompt": json.dumps(g),
            "parameters": "a red fox\nNegative prompt: blur\nSteps: 20",
        }
        path = self.output_dir / "saver.png"
        self.assertEqual(self.api._ingest_scanned_file(path, meta), "added")
        self.assertEqual(self._texts(), ["a red fox"])


class TestScanDecision(AdminAPITestCase):
    """The scan applies the shared per-file decision (#247): one prompt per
    file, no junk text, marks on every prompt it creates."""

    def _path(self, name):
        path = self.output_dir / name
        path.write_bytes(b"png")
        return path

    def _links(self, path):
        rows = self.api.db.model.get_connection().execute(
            "SELECT prompt_id FROM generated_images WHERE image_path=?", (str(path),)
        )
        return [row[0] for row in rows]

    def _prompt(self, text):
        return self.api.db.get_prompt_by_hash(generate_prompt_hash(text))

    def test_a_file_linked_to_another_prompt_is_a_conflict(self):
        path = self._path("fox.png")
        other = self.api.db.save_prompt(
            "a dog", prompt_hash=generate_prompt_hash("a dog")
        )
        self.api.db.link_image_to_prompt(other, str(path))
        meta = {"prompt": json.dumps(flux_graph())}
        self.assertEqual(self.api._ingest_scanned_file(path, meta), "conflict")
        self.assertEqual(self._links(path), [other])
        self.assertIsNone(self._prompt("a red fox"))

    def test_junk_text_is_never_stored(self):
        cases = {
            "lora.png": {"prompt": json.dumps(flux_graph(text="<lora:style:1.00>"))},
            "model.png": {"parameters": "models/flux-dev.safetensors"},
        }
        for name, meta in cases.items():
            with self.subTest(name=name):
                path = self._path(name)
                self.assertEqual(self.api._ingest_scanned_file(path, meta), "found")
                self.assertEqual(self._links(path), [])
        self.assertEqual(self.api.db.get_recent_prompts()["prompts"], [])

    def test_created_prompts_carry_the_scan_marks(self):
        path = self._path("pair.png")
        meta = {"prompt": json.dumps(sampled_graph("cat", "blur"))}
        self.assertEqual(self.api._ingest_scanned_file(path, meta), "added")
        for text in ("cat", "blur"):
            prompt = self._prompt(text)
            self.assertEqual(prompt["category"], "scanned")
            self.assertEqual(prompt["tags"], ["auto-scanned"])
            self.assertEqual(prompt["notes"], "Auto-scanned from pair.png")

    def test_outcome_of_a_positive_reports_its_title(self):
        """Regression guard (passes on the base): the detailed positive row."""
        path = self._path("fox.png")
        meta = {"prompt": json.dumps(flux_graph())}
        first = self.api._ingest_scanned_file(path, meta, detailed=True)
        self.assertEqual(first, {"status": "added", "title": "a red fox"})
        again = self.api._ingest_scanned_file(path, meta, detailed=True)
        self.assertEqual(again["status"], "unchanged")
        other = self._path("fox2.png")
        linked = self.api._ingest_scanned_file(other, meta, detailed=True)
        self.assertEqual(linked["status"], "linked")

    def test_outcome_of_a_pair_reports_its_counts(self):
        """Regression guard (passes on the base): the detailed pair row."""
        path = self._path("pair.png")
        meta = {"prompt": json.dumps(sampled_graph("cat", "blur"))}
        result = self.api._ingest_scanned_file(path, meta, detailed=True)
        self.assertEqual(result["status"], "added")
        self.assertEqual((result["pairs_added"], result["links_added"]), (1, 1))
        self.assertEqual(result["title"], "cat")

    def test_outcome_without_a_store_reports_the_unlinked_status(self):
        """Regression guard (passes on the base): never-stored rows."""
        path = self._path("notes.png")
        meta = {"prompt": json.dumps({"40": {"class_type": "Textbox", "inputs": {}}})}
        self.assertEqual(self.api._ingest_scanned_file(path, meta), "found")
        self.assertIsNone(self.api._ingest_scanned_file(path, {}))

    def test_review_lists_get_the_default_profiles_ambiguous_files(self):
        """Regression guard (passes on the base): Needs review inputs."""
        g = sampled_graph()
        g["6"] = {"class_type": "CLIPTextEncode", "inputs": {"text": "dog"}}
        g["12"] = {**g["3"], "inputs": {**g["3"]["inputs"], "positive": ["6", 0]}}
        ambiguous, untraced, records = [], [], {}
        path = self._path("two.png")
        status = self.api._ingest_scanned_file(
            path,
            {"prompt": json.dumps(g)},
            ambiguous=ambiguous,
            untraced=untraced,
            records=records,
        )
        # Linked by the legacy floor, still listed (queued only if unlinked).
        self.assertEqual(status, "added")
        self.assertEqual((ambiguous, untraced), ([path], []))
        self.assertEqual(records["canonical"]["prompt"], g)

    def test_an_ambiguous_file_with_a_junk_answer_is_still_queued(self):
        from tests.test_file_prompt import _numeric_first_branches

        path = self._path("numbers.png")
        meta = {"prompt": json.dumps(_numeric_first_branches())}
        with patch.object(
            type(self.api), "_record_scanned_review", autospec=True
        ) as record:
            counts = self.api._ingest_scan_batch_sync([(path, meta)])
        self.assertEqual(counts["ambiguous"], 1, counts)
        record.assert_called_once()
        self.assertEqual(self._links(path), [])

    def test_a_conflict_row_says_the_link_was_kept(self):
        from py.api.admin import _scan_outcome

        _group, row, _line = _scan_outcome(self._path("c.png"), [], "conflict")
        self.assertEqual(row["detail"], "Kept: already linked to a different prompt")

    def test_a_library_move_reruns_the_scan_writes(self):
        from py.api import admin

        real = admin.ingest_resolved_pair
        model = self.api.db.model

        def moved_once(*args, **kwargs):
            if not calls:
                calls.append(1)
                model._generation += 1
                raise sqlite3.OperationalError("closed")
            return real(*args, **kwargs)

        calls = []
        path = self._path("pair.png")
        with patch.object(admin, "ingest_resolved_pair", side_effect=moved_once):
            meta = {"prompt": json.dumps(sampled_graph("cat", "blur"))}
            self.assertEqual(self.api._ingest_scanned_file(path, meta), "added")
        self.assertEqual(calls, [1])

    def test_an_off_node_behind_a_pass_through_stores_nothing(self):
        """#90 regression guard for the scan (main stored nothing here)."""
        from tests.test_save_to_library import run_graph

        g = run_graph(save=False)
        g["1"]["class_type"] = "PromptManagerText"
        g["7"] = {"class_type": "easy showAnything", "inputs": {"anything": ["1", 0]}}
        g["8"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ["7", 0], "clip": ["4", 1]},
        }
        g["3"]["inputs"]["positive"] = ["8", 0]
        path = self._path("off.png")
        meta = {"prompt": json.dumps(g)}
        self.assertEqual(self.api._ingest_scanned_file(path, meta), "not_saved")
        self.assertEqual(self.api.db.get_recent_prompts()["prompts"], [])
        self.assertEqual(self._links(path), [])
