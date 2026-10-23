"""Needs review choices from the inspection profile (follow-up to #284/#288).

A Qwen image-edit batch runs several KSamplers, each with its own
``TextEncodeQwenImageEdit(prompt=...)``, and every SaveImage shares one
prefix: the file cannot say which prompt made it. The scan's default
(ingestion) profile does not know the encoder or the string nodes, so every
candidate is unresolved and the file used to leave the queue as "could not
be traced". The inspection profile resolves every text, so these are real
choices. Ruling (option a): when the default profile is ambiguous with fewer
than two candidates that carry text, the scan asks the inspection profile;
with at least two choosable candidates it records the item from that
profile, and Apply re-resolves with the same profile. Nothing links
automatically from the inspection profile, and the live monitor never queues.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

try:
    from tests.test_api_review import ReviewRoutesTestCase
    from tests.test_review import unlinkable_graph, write_png
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_review import ReviewRoutesTestCase
    from test_review import unlinkable_graph, write_png

from utils import inspection_semantics, review_capture
from utils.hashing import generate_prompt_hash
from utils.image_monitor import ImageGenerationHandler
from utils.prompt_tracker import PromptTracker

NAME = "kittybear_00001_.png"
PROMPTS = ("make it night", "add a red hat", "turn the cat into a bear")


def qwen_edit_graph(*prompts, joined=()):
    """One Qwen image-edit sampler per prompt, all saved under one prefix.

    Each sampler has its own ``TextEncodeQwenImageEdit``, used for both
    positive and negative as the real batches do. ``joined`` prompts are
    ``(first, second)`` pairs joined by ``JoinStringMulti`` over two
    ``StringConstantMultiline`` nodes. Every SaveImage takes its prefix from
    one linked string node.
    """
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "q.safetensors"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "c.safetensors"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "v.safetensors"}},
        "4": {"class_type": "LoadImage", "inputs": {"image": "source.png"}},
        "5": {"class_type": "PrimitiveString", "inputs": {"value": "kittybear"}},
        "6": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 512, "height": 512, "batch_size": 1},
        },
    }
    sides = [("literal", text) for text in prompts]
    sides += [("joined", parts) for parts in joined]
    for index, (kind, text) in enumerate(sides):
        base = 100 + 10 * index
        prompt = text
        if kind == "joined":
            for offset, part in enumerate(text, start=1):
                graph[str(base + offset)] = {
                    "class_type": "StringConstantMultiline",
                    "inputs": {"string": part, "strip_newlines": True},
                }
            graph[str(base + 3)] = {
                "class_type": "JoinStringMulti",
                "inputs": {
                    "inputcount": 2,
                    "delimiter": ", ",
                    "return_list": False,
                    "string_1": [str(base + 1), 0],
                    "string_2": [str(base + 2), 0],
                },
            }
            prompt = [str(base + 3), 0]
        encoder, sampler = str(base), str(base + 5)
        graph[encoder] = {
            "class_type": "TextEncodeQwenImageEdit",
            "inputs": {
                "clip": ["2", 0],
                "prompt": prompt,
                "vae": ["3", 0],
                "image": ["4", 0],
            },
        }
        graph[sampler] = {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": [encoder, 0],
                "negative": [encoder, 0],
                "latent_image": ["6", 0],
                "seed": index,
            },
        }
        graph[str(base + 6)] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": [sampler, 0], "vae": ["3", 0]},
        }
        graph[str(base + 7)] = {
            "class_type": "SaveImage",
            "inputs": {"images": [str(base + 6), 0], "filename_prefix": ["5", 0]},
        }
    return graph


def _prefixed(graph, prefix):
    """``graph`` with every node id (and link to one) prefixed."""
    renamed = {}
    for node_id, node in graph.items():
        inputs = {
            key: (
                [prefix + value[0], value[1]]
                if isinstance(value, list) and len(value) == 2
                else value
            )
            for key, value in node["inputs"].items()
        }
        renamed[prefix + node_id] = {**node, "inputs": inputs}
    return renamed


def _texts(evidence):
    return sorted(
        c["positive"]["text"]
        for c in evidence["candidates"]
        if isinstance(c.get("positive"), dict)
    )


class InspectionTestCase(ReviewRoutesTestCase):
    def _write(self, name=NAME, graph=None, color=(255, 0, 0)):
        path = self.output_dir / name
        write_png(
            path,
            prompt=graph if graph is not None else qwen_edit_graph(*PROMPTS),
            color=color,
        )
        return path

    def _evidence(self, item):
        return json.loads(item["evidence"])

    def _table(self, edit):
        """Point the inspection profile at an edited copy of the table."""
        doc = json.loads(inspection_semantics.SEMANTICS_PATH.read_text("utf-8"))
        edit(doc)
        folder = tempfile.mkdtemp(dir=self.tmpdir)
        table = Path(folder) / "inspection-semantics.json"
        table.write_text(json.dumps(doc), encoding="utf-8")
        patcher = patch.object(inspection_semantics, "SEMANTICS_PATH", table)
        patcher.start()
        inspection_semantics.get_semantics.cache_clear()
        self.addCleanup(inspection_semantics.get_semantics.cache_clear)
        self.addCleanup(patcher.stop)

    async def _apply(self, positive):
        item, detail, observed = await self._open()
        candidate = self._candidate(detail, positive)
        return item, await self._decide(
            item["id"], self._body(detail, observed, candidate["hash"])
        )

    def _linked_text(self, path):
        row = (
            self.api.db.model.get_connection()
            .execute(
                "SELECT p.text FROM generated_images gi "
                "JOIN prompts p ON p.id=gi.prompt_id WHERE gi.image_path=?",
                (str(path),),
            )
            .fetchone()
        )
        return row[0] if row else None


class TestQwenEditBatchIsQueued(InspectionTestCase):
    async def test_qwen_edit_batch_is_queued_with_its_texts(self):
        self._roots()
        graph = qwen_edit_graph(*PROMPTS, joined=[("a red scarf", "snow")])
        self._write(graph=graph)

        job = await self._scan()

        (item,) = self._items()
        evidence = self._evidence(item)
        self.assertEqual(_texts(evidence), sorted(PROMPTS + ("a red scarf, snow",)))
        self.assertEqual(item["candidate_count"], 4)
        self.assertEqual(evidence["profile"], "inspection")
        self.assertEqual(
            evidence["semantics"], inspection_semantics.get_semantics().version
        )
        self.assertEqual(
            item["resolver_version"], review_capture.inspection_review_version()
        )
        self.assertNotEqual(item["resolver_version"], review_capture.RESOLVER_VERSION)
        self.assertEqual(job["scope"]["review_items"], 1)
        (row,) = job["outcomes"]["review"]
        self.assertEqual(row["path"], NAME)
        self.assertIn("more than one prompt could match", row["detail"])
        # Review choices only: nothing linked the file.
        self.assertIsNone(self._linked_text(self.output_dir / NAME))
        self.assertEqual(self._capture_files(), [])

    async def test_rescan_keeps_one_item_and_skips_the_copy(self):
        self._roots()
        self._write()
        await self._scan()
        (item,) = self._items()

        with patch.object(
            self.api._captures, "capture", side_effect=AssertionError("copied")
        ):
            job = await self._scan("all")

        self.assertEqual([i["id"] for i in self._items()], [item["id"]])
        self.assertEqual(job["scope"]["review_items"], 1)
        self.assertEqual(job["scope"]["review_untraced"], 0)

    async def test_more_than_sixteen_qwen_samplers_make_a_limited_item(self):
        self._roots()
        self._write(graph=qwen_edit_graph(*(f"edit {n}" for n in range(17))))

        await self._scan()

        (item,) = self._items()
        evidence = self._evidence(item)
        self.assertTrue(item["limited"])
        self.assertEqual(item["candidate_count"], 0)
        self.assertEqual(evidence["candidate_total"], 17)
        self.assertEqual(evidence["profile"], "inspection")

    async def test_evidence_over_the_cap_makes_a_limited_item(self):
        self._roots()
        self._write()
        real = review_capture.canonical_evidence

        def capped(value):
            if value.get("candidates"):
                raise ValueError("evidence exceeds the bound")
            return real(value)

        with patch.object(review_capture, "canonical_evidence", side_effect=capped):
            await self._scan()

        (item,) = self._items()
        self.assertTrue(item["limited"])
        self.assertEqual(self._evidence(item)["candidate_total"], 3)
        self.assertEqual(self._evidence(item)["profile"], "inspection")


class TestDefaultProfileStillDecides(InspectionTestCase):
    async def test_ambiguous_file_with_texts_keeps_the_default_profile(self):
        """A default-ambiguous file with a choice never runs the inspection
        profile, even when that profile would offer more candidates."""
        self._roots()
        graph = {
            **_prefixed(qwen_edit_graph("make it night"), "q"),
            **unlinkable_graph("cat", "dog"),
        }
        self._write(graph=graph)

        with patch.object(
            review_capture,
            "resolve_inspection",
            side_effect=AssertionError("inspection profile ran"),
        ):
            await self._scan()

        (item,) = self._items()
        evidence = self._evidence(item)
        self.assertNotIn("profile", evidence)
        self.assertNotIn("semantics", evidence)
        self.assertEqual(item["resolver_version"], review_capture.RESOLVER_VERSION)
        self.assertEqual(_texts(evidence), ["cat", "dog"])

    async def test_apply_of_a_default_item_never_runs_the_inspection_profile(self):
        self._roots()
        write_png(self.output_dir / "two.png", prompt=unlinkable_graph("cat", "dog"))
        await self._scan()
        with patch.object(
            review_capture,
            "resolve_inspection",
            side_effect=AssertionError("inspection profile ran"),
        ):
            _item, (status, data) = await self._apply("cat")
        self.assertEqual(status, 200, data)
        self.assertEqual(self._linked_text(self.output_dir / "two.png"), "cat")


class TestApplyInspectionCandidate(InspectionTestCase):
    async def test_apply_links_the_chosen_qwen_edit_text(self):
        self._require_routes()
        self._roots()
        path = self._write()
        await self._scan()

        item, (status, data) = await self._apply("add a red hat")

        self.assertEqual(status, 200, data)
        self.assertEqual(data["item"]["state"], "selected")
        self.assertEqual(data["total"], 0)
        self.assertEqual(self._linked_text(path), "add a red hat")
        links = self.api.db.get_image_prompt_pairs(data["pair"]["image_id"])
        (link,) = links
        provenance = link["provenance"]
        self.assertEqual(provenance["kind"], "user-selection")
        self.assertEqual(
            provenance["resolver_version"], review_capture.inspection_review_version()
        )
        self.assertEqual(provenance["positive"]["text"], "add a red hat")
        (decision,) = self._decisions()
        self.assertEqual(decision["item_id"], item["id"])
        self._no_paths()

    async def test_apply_rechecks_with_the_recorded_profile(self):
        """The default profile would not offer the candidate: Apply must use
        the profile the item recorded, and refuses when that profile no
        longer offers it."""
        self._require_routes()
        self._roots()
        self._write()
        await self._scan()
        calls = []
        real = review_capture.resolve_inspection

        def spy(metadata):
            calls.append(metadata)
            return real(metadata)

        with patch.object(review_capture, "resolve_inspection", side_effect=spy):
            _item, (status, data) = await self._apply("make it night")

        self.assertEqual(status, 200, data)
        self.assertEqual(len(calls), 1)

    async def test_table_change_keeps_the_item_applicable(self):
        """A semantics table change moves the version (a rescan records the
        new evidence beside the old item, as I2 does for the resolver), and
        the pending item stays applicable while its candidate is offered."""
        self._require_routes()
        self._roots()
        path = self._write()
        await self._scan()
        (old,) = self._items()
        version = review_capture.inspection_review_version()

        def add_encoder(doc):
            doc["encoders"].append(
                {
                    "class": "UnrelatedTextEncode",
                    "slot": 0,
                    "strategy": "single",
                    "inputs": ["text"],
                }
            )

        self._table(add_encoder)
        self.assertNotEqual(review_capture.inspection_review_version(), version)

        await self._scan("all")
        items = self._items()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], old["id"])
        self.assertEqual(
            self._evidence(items[1])["semantics"],
            inspection_semantics.get_semantics().version,
        )

        item, detail, observed = await self._open()
        self.assertEqual(item["id"], old["id"])
        candidate = self._candidate(detail, "add a red hat")
        status, data = await self._decide(
            item["id"], self._body(detail, observed, candidate["hash"])
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(self._linked_text(path), "add a red hat")

    async def test_table_change_that_drops_the_encoder_is_a_review_conflict(self):
        self._require_routes()
        self._roots()
        self._write()
        await self._scan()
        item, detail, observed = await self._open()
        candidate = self._candidate(detail, "add a red hat")

        def drop_encoder(doc):
            doc["encoders"] = [
                entry
                for entry in doc["encoders"]
                if entry["class"] != "TextEncodeQwenImageEdit"
            ]

        self._table(drop_encoder)
        before = self._snapshot()
        status, data = await self._decide(
            item["id"], self._body(detail, observed, candidate["hash"])
        )

        self.assertEqual(status, 409, data)
        self.assertEqual(data["code"], "review-conflict")
        self.assertEqual(self._snapshot(), before)


class TestMonitorNeverQueues(InspectionTestCase):
    def _handler(self):
        handler = ImageGenerationHandler(self.api.db, PromptTracker(self.api.db))
        handler.review_recorder = self.api.record_review_file
        return handler

    async def test_monitor_pass_never_queues_but_the_next_scan_does(self):
        self._roots()
        path = self._write()

        self.assertEqual(self.api.record_review_file(str(path)), "untraced")
        self.assertEqual(self._items(), [])
        self.assertEqual(self._capture_files(), [])

        await self._scan()
        (item,) = self._items()
        self.assertEqual(self._evidence(item)["profile"], "inspection")

    async def test_monitor_keeps_the_inspection_item_of_an_unlinked_output(self):
        """The monitor's untraced settle judges each item by its own
        evidence: an inspection item offers a choice and stays."""
        self._roots()
        path = self._write()
        await self._scan()
        (item,) = self._items()
        # Other bytes, so the stat shortcut does not answer for the file.
        self._write(color=(0, 0, 255))

        self.assertEqual(self.api.record_review_file(str(path)), "untraced")
        self.assertEqual([i["id"] for i in self._items()], [item["id"]])

    async def test_monitor_link_removes_the_inspection_item(self):
        self._roots()
        path = self._write()
        await self._scan()
        self.assertEqual(len(self._items()), 1)
        prompt_id = self.api.db.save_prompt(
            text="latest", prompt_hash=generate_prompt_hash("latest")
        )

        self._handler().link_image_to_prompt(
            str(path),
            {"id": prompt_id, "text": "latest", "fallback": True},
            {"prompt": qwen_edit_graph(*PROMPTS)},
        )

        self.assertEqual(self._items(), [])
        self.assertEqual(os.path.basename(str(path)), NAME)


class TestTableVersion(InspectionTestCase):
    def test_table_version_follows_content_not_layout(self):
        doc = {"schema": 1, "encoders": [{"class": "A", "slot": 0}]}
        same = json.loads(json.dumps(doc, indent=4))
        other = {"schema": 1, "encoders": [{"class": "B", "slot": 0}]}

        version = inspection_semantics.table_version(doc)

        self.assertRegex(version, "^[0-9a-f]{64}$")
        self.assertEqual(inspection_semantics.table_version(same), version)
        self.assertNotEqual(inspection_semantics.table_version(other), version)
        self.assertEqual(
            inspection_semantics.load_semantics().version,
            inspection_semantics.table_version(
                json.loads(inspection_semantics.SEMANTICS_PATH.read_text("utf-8"))
            ),
        )

    async def test_an_unusable_table_queues_nothing(self):
        self._roots()
        self._write()
        unusable = inspection_semantics.SemanticsError("bad table")
        with (
            patch.object(inspection_semantics, "get_semantics", side_effect=unusable),
            patch.object(review_capture, "get_semantics", side_effect=unusable),
        ):
            self.assertIsNone(review_capture.inspection_review_version())
            job = await self._scan()

        self.assertEqual(self._items(), [])
        (row,) = job["outcomes"]["review"]
        self.assertIn("could not be traced", row["detail"])
