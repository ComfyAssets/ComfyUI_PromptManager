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

import contextlib
import json
import os
import tempfile
import unittest
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


@contextlib.contextmanager
def _counted(module, name):
    """Count the calls of ``module.name`` while calling through."""
    real = getattr(module, name)
    calls = []

    def spy(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    with patch.object(module, name, side_effect=spy):
        yield calls


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


def _version(graph=None):
    """The item version of a Qwen-edit graph under the current table."""
    graph = graph if graph is not None else qwen_edit_graph(*PROMPTS)
    return review_capture.inspection_review_version({"prompt": graph})


def _retarget_unet_loader(doc):
    """A table edit for a class the Qwen-edit graph uses (no prompt effect)."""
    doc["facts"]["model_outputs"]["UNETLoader"] = "unet"


def _unrelated_encoder(doc):
    """A table edit for a class the Qwen-edit graph does not use."""
    doc["encoders"].append(
        {
            "class": "UnrelatedTextEncode",
            "slot": 0,
            "strategy": "single",
            "inputs": ["text"],
        }
    )


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

    def _pending(self):
        return self.api.db.library_counts()["review_pending"]

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
            evidence["semantics"], review_capture.semantics_version({"prompt": graph})
        )
        self.assertEqual(
            item["resolver_version"],
            review_capture.inspection_review_version({"prompt": graph}),
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

        with _counted(self.api._captures, "capture") as copies:
            job = await self._scan("all")

        self.assertEqual(copies, [])
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
        self.assertEqual(provenance["resolver_version"], _version())
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
        """A table change for a class the graph uses moves the item's
        version; the pending item stays applicable while its candidate is
        still offered."""
        self._require_routes()
        self._roots()
        path = self._write()
        await self._scan()
        (old,) = self._items()
        version = _version()
        item, detail, observed = await self._open()

        self._table(_retarget_unet_loader)
        self.assertNotEqual(_version(), version)

        candidate = self._candidate(detail, "add a red hat")
        status, data = await self._decide(
            item["id"], self._body(detail, observed, candidate["hash"])
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(item["id"], old["id"])
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

        version = _version()
        self._table(drop_encoder)
        self.assertNotEqual(_version(), version)
        before = self._snapshot()
        status, data = await self._decide(
            item["id"], self._body(detail, observed, candidate["hash"])
        )

        self.assertEqual(status, 409, data)
        self.assertEqual(data["code"], "review-conflict")
        self.assertEqual(self._snapshot(), before)
        # The rescan finds nothing to choose under the new version: the
        # stale, undecided item leaves the queue instead of staying stuck.
        job = await self._scan("all")
        self.assertEqual(self._items(), [])
        self.assertEqual(job["scope"]["review_untraced"], 1)


class TestMonitorNeverQueues(InspectionTestCase):
    def _handler(self):
        handler = ImageGenerationHandler(self.api.db, PromptTracker(self.api.db))
        handler.review_recorder = self.api.record_review_file
        return handler

    async def test_monitor_pass_never_queues_but_the_next_scan_does(self):
        self._roots()
        path = self._write()

        self.assertEqual(self.api.record_review_file(str(path)), "skipped")
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

        self.assertEqual(self.api.record_review_file(str(path)), "skipped")
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
            self.assertIsNone(_version())
            job = await self._scan()

        self.assertEqual(self._items(), [])
        (row,) = job["outcomes"]["review"]
        self.assertIn("could not be traced", row["detail"])


class TestVersionChanges(InspectionTestCase):
    """A version change never leaves two pending items for one file."""

    async def test_table_change_for_a_graph_class_leaves_one_item(self):
        self._require_routes()
        self._roots()
        path = self._write()
        await self._scan()
        (old,) = self._items()

        self._table(_retarget_unet_loader)
        job = await self._scan("all")

        (item,) = self._items()
        self.assertNotEqual(item["id"], old["id"])
        self.assertEqual(item["resolver_version"], _version())
        self.assertNotEqual(item["resolver_version"], old["resolver_version"])
        self.assertEqual(job["scope"]["review_items"], 1)
        self.assertEqual(self._pending(), 1)
        _item, (status, data) = await self._apply("make it night")
        self.assertEqual(status, 200, data)
        self.assertEqual(self._linked_text(path), "make it night")

    async def test_unrelated_table_edit_keeps_the_item_and_does_no_work(self):
        self._roots()
        self._write()
        await self._scan()
        (old,) = self._items()
        version = _version()

        self._table(_unrelated_encoder)
        self.assertEqual(_version(), version)
        self.assertNotEqual(
            inspection_semantics.get_semantics().version,
            json.loads(old["evidence"])["semantics"],
        )
        with (
            _counted(self.api._captures, "capture") as copies,
            _counted(review_capture, "resolve_inspection") as calls,
        ):
            await self._scan("all")

        self.assertEqual(copies, [])
        self.assertEqual([i["id"] for i in self._items()], [old["id"]])
        self.assertEqual(calls, [])

    async def test_resolver_change_leaves_one_default_item(self):
        self._roots()
        write_png(self.output_dir / "two.png", prompt=unlinkable_graph("cat", "dog"))
        await self._scan()
        (old,) = self._items()
        self.assertEqual(old["resolver_version"], 1)

        with patch.object(review_capture, "RESOLVER_VERSION", 2):
            await self._scan("all")

        (item,) = self._items()
        self.assertNotEqual(item["id"], old["id"])
        self.assertEqual(item["resolver_version"], 2)
        self.assertEqual(self._pending(), 1)

    async def test_decided_old_item_is_kept_and_no_duplicate_is_made(self):
        self._require_routes()
        self._roots()
        self._write()
        await self._scan()
        item, detail, observed = await self._open()
        status, data = await self._decide(
            item["id"], self._body(detail, observed, "unresolved")
        )
        self.assertEqual(status, 200, data)
        before = (self._items(), self._decisions())

        self._table(_retarget_unet_loader)
        await self._scan("all")
        self.assertEqual((self._items(), self._decisions()), before)
        # The decided bytes are not copied again on the next rescan.
        with _counted(self.api._captures, "capture") as copies:
            await self._scan("all")

        self.assertEqual(copies, [])
        self.assertEqual((self._items(), self._decisions()), before)
        self.assertEqual(self._pending(), 0)

    async def test_advanced_pending_item_is_kept_and_no_duplicate_is_made(self):
        self._roots()
        self._write()
        await self._scan()
        (old,) = self._items()
        conn = self.api.db.model.get_connection()
        with conn:
            conn.execute(
                "UPDATE media_review_items SET revision=1 WHERE id=?", (old["id"],)
            )
        before = self._items()

        self._table(_retarget_unet_loader)
        await self._scan("all")

        self.assertEqual(self._items(), before)


class TestInspectionRuns(InspectionTestCase):
    """The heavier profile runs once per new file state, never again."""

    async def test_new_file_is_inspected_once_and_not_on_rescan(self):
        self._roots()
        self._write()
        with _counted(review_capture, "resolve_inspection") as first:
            await self._scan()
        self.assertEqual(len(self._items()), 1)
        self.assertEqual(len(first), 1)

        with _counted(review_capture, "resolve_inspection") as again:
            await self._scan("all")

        self.assertEqual(again, [])
        self.assertEqual(len(self._items()), 1)

    async def test_untraced_file_is_not_inspected_again_until_it_changes(self):
        self._roots()
        graph = qwen_edit_graph(*PROMPTS)
        for node in graph.values():
            if node["class_type"] == "TextEncodeQwenImageEdit":
                node["class_type"] = "OpaqueTextEncode"
        self._write(graph=graph)
        with _counted(review_capture, "resolve_inspection") as first:
            job = await self._scan()
        self.assertEqual(len(first), 1)
        (row,) = job["outcomes"]["review"]
        self.assertIn("could not be traced", row["detail"])

        with _counted(review_capture, "resolve_inspection") as again:
            await self._scan("all")
        self.assertEqual(again, [])

        self._write(graph=graph, color=(0, 255, 0))
        with _counted(review_capture, "resolve_inspection") as changed:
            await self._scan("all")
        self.assertEqual(len(changed), 1)
        self.assertEqual(self._items(), [])

    async def test_capture_with_other_records_is_inspected_again(self):
        """The scan's result is reused only for identical records."""
        self._roots()
        self._write()
        real = review_capture.captured_review

        def edited(store, capture, profile=review_capture.DEFAULT):
            metadata, resolution = real(store, capture, profile)
            graph = json.loads(json.dumps(metadata["prompt"]))
            graph["100"]["inputs"]["prompt"] = "a captured edit"
            return {**metadata, "prompt": graph}, resolution

        with (
            patch.object(review_capture, "captured_review", side_effect=edited),
            _counted(review_capture, "resolve_inspection") as calls,
        ):
            await self._scan()

        self.assertEqual(len(calls), 2)
        (item,) = self._items()
        self.assertIn("a captured edit", _texts(self._evidence(item)))


class TestClassProjection(unittest.TestCase):
    def test_projection_over_every_named_class_is_the_whole_table(self):
        doc = json.loads(inspection_semantics.SEMANTICS_PATH.read_text("utf-8"))
        names = set(inspection_semantics.load_semantics().all_classes)
        names |= set(doc["facts"]["configuration_outputs"])
        names |= set(doc["facts"]["model_outputs"])
        names |= set(doc["facts"]["model_passthroughs"])
        names |= set(doc["facts"].get("scalar_outputs", {}))
        names |= {entry["class"] for entry in doc["lineage"]}

        self.assertEqual(inspection_semantics.class_projection(doc, names), doc)

    def test_class_version_moves_only_for_the_classes_it_covers(self):
        doc = json.loads(inspection_semantics.SEMANTICS_PATH.read_text("utf-8"))
        classes = frozenset({"KSampler", "UNETLoader"})
        version = inspection_semantics.class_version(doc, classes)
        _unrelated_encoder(doc)
        self.assertEqual(inspection_semantics.class_version(doc, classes), version)
        _retarget_unet_loader(doc)
        self.assertNotEqual(inspection_semantics.class_version(doc, classes), version)


class TestRecordingEdges(InspectionTestCase):
    async def test_store_refuses_a_new_version_beside_a_decided_item(self):
        self._roots()
        self._write()
        await self._scan()
        (old,) = self._items()
        conn = self.api.db.model.get_connection()
        with conn:
            conn.execute(
                "UPDATE media_review_items SET state='unresolved', revision=1 "
                "WHERE id=?",
                (old["id"],),
            )
        evidence = {**self._evidence(old), "resolver_version": 7}

        inserted = self.api.db.record_review_item(
            locator=old["locator"],
            spelled_path=old["spelled_path"],
            sha256=old["sha256"],
            size=old["size"],
            mtime_ns=old["mtime_ns"],
            epoch=self.api.db.get_execution_epoch(),
            resolver_version=7,
            evidence=evidence,
            candidate_count=old["candidate_count"],
            limited=bool(old["limited"]),
        )

        self.assertFalse(inserted)
        self.assertEqual([i["id"] for i in self._items()], [old["id"]])

    async def test_already_linked_file_with_an_inspection_choice_is_unchanged(self):
        self._roots()
        path = self._write()
        prompt_id = self.api.db.save_prompt(
            text="latest", prompt_hash=generate_prompt_hash("latest")
        )
        handler = ImageGenerationHandler(self.api.db, PromptTracker(self.api.db))
        handler.link_image_to_prompt(
            str(path),
            {"id": prompt_id, "text": "latest", "fallback": True},
            {"prompt": qwen_edit_graph(*PROMPTS)},
        )

        job = await self._scan("all")

        self.assertEqual(self._items(), [])
        self.assertEqual(job["outcomes"]["review"], [])
        (row,) = job["outcomes"]["known"]
        self.assertIn("Already linked", row["detail"])
        self.assertEqual(self._linked_text(path), "latest")

    async def test_untraced_memory_forgets_the_oldest_state(self):
        from py.api import review_recording

        self._roots()
        graph = qwen_edit_graph(*PROMPTS)
        for node in graph.values():
            if node["class_type"] == "TextEncodeQwenImageEdit":
                node["class_type"] = "OpaqueTextEncode"
        self._write("a.png", graph=graph)
        self._write("b.png", graph=graph)
        with patch.object(review_recording, "MAX_UNTRACED_MEMORY", 1):
            await self._scan()
            with _counted(review_capture, "resolve_inspection") as calls:
                await self._scan("all")

        # One remembered state: each file evicts the other, so both run.
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.api._untraced_inspections), 1)

    def test_graph_classes(self):
        self.assertEqual(review_capture.graph_classes({"parameters": "x"}), set())
        self.assertEqual(
            review_capture.graph_classes({"prompt": {"1": {"class_type": "A"}}}),
            {"A"},
        )
        many = {
            str(n): {"class_type": f"C{n}"}
            for n in range(review_capture.MAX_FINGERPRINT_CLASSES + 1)
        }
        self.assertIsNone(review_capture.graph_classes({"prompt": many}))
        self.assertEqual(
            review_capture.semantics_version({"prompt": many}),
            inspection_semantics.get_semantics().version,
        )


def _move_qwen_encoder_slot(doc):
    """An edit of the Qwen-edit encoder's own table entry."""
    for entry in doc["encoders"]:
        if entry["class"] == "TextEncodeQwenImageEdit":
            entry["slot"] = 1


def _unused_sampler(doc):
    """A new dict-keyed sampler class the Qwen-edit graph does not use."""
    doc["samplers"]["UnusedSampler"] = dict(doc["samplers"]["KSampler"])


class TestFingerprintCoverage(InspectionTestCase):
    def test_editing_the_encoders_entry_moves_the_version(self):
        version = _version()
        self._table(_move_qwen_encoder_slot)
        self.assertIsNotNone(_version())
        self.assertNotEqual(_version(), version)

    def test_an_unused_dict_keyed_class_leaves_the_version(self):
        version = _version()
        table = inspection_semantics.get_semantics().version
        self._table(_unused_sampler)
        self.assertNotEqual(inspection_semantics.get_semantics().version, table)
        self.assertEqual(_version(), version)


class TestInspectionResolverVersion(InspectionTestCase):
    async def test_inspection_resolver_change_supersedes_the_item(self):
        """An inspection-only resolver change moves the item version, so the
        rescan re-inspects and the new item is applicable (never a stuck
        409)."""
        self._require_routes()
        self._roots()
        path = self._write()
        await self._scan()
        (old,) = self._items()
        version = _version()
        real = review_capture.resolve_inspection

        def newer(metadata):
            resolution = real(metadata)
            return {
                **resolution,
                "candidates": [
                    {**c, "reason": "a newer inspection resolver"}
                    for c in resolution["candidates"]
                ],
            }

        with (
            patch.object(review_capture, "INSPECTION_RESOLVER_VERSION", 2),
            patch.object(review_capture, "resolve_inspection", side_effect=newer),
        ):
            self.assertNotEqual(_version(), version)
            await self._scan("all")
            (item,) = self._items()
            self.assertNotEqual(item["id"], old["id"])
            _item, (status, data) = await self._apply("add a red hat")

        self.assertEqual(status, 200, data)
        self.assertEqual(self._linked_text(path), "add a red hat")

    def test_profile_budgets_are_folded_into_the_version(self):
        from dataclasses import replace

        version = _version()
        wider = replace(
            review_capture.INSPECTION_PROFILE,
            total_visits=review_capture.INSPECTION_PROFILE.total_visits + 1,
        )
        with patch.object(review_capture, "INSPECTION_PROFILE", wider):
            self.assertNotEqual(_version(), version)


class TestScanContainment(InspectionTestCase):
    async def test_an_inspection_failure_is_counted_and_the_scan_continues(self):
        self._roots()
        self._write("a.png")
        self._write("b.png", graph=qwen_edit_graph("snow", "rain"))
        real = review_capture.resolve_inspection

        def fails_for_a(metadata):
            if "make it night" in json.dumps(metadata):
                raise RuntimeError("inspection blew up")
            return real(metadata)

        with (
            patch.object(review_capture, "resolve_inspection", side_effect=fails_for_a),
            self.assertLogs("prompt_manager.api", "WARNING") as logs,
        ):
            job = await self._scan()

        self.assertEqual(job["state"], "finished")
        (item,) = self._items()
        self.assertEqual(os.path.basename(item["locator"]), "b.png")
        self.assertEqual(job["scope"]["review_errors"], 1)
        rows = {row["path"]: row for row in job["outcomes"]["review"]}
        self.assertIn("could not be traced", rows["a.png"]["detail"])
        self.assertIn("more than one prompt could match", rows["b.png"]["detail"])
        self.assertTrue(any("a.png" in line for line in logs.output))


class TestUntracedMemoryKeys(InspectionTestCase):
    async def test_untraced_memory_is_keyed_by_a_16_byte_digest(self):
        self._roots()
        graph = qwen_edit_graph(*PROMPTS)
        for node in graph.values():
            if node["class_type"] == "TextEncodeQwenImageEdit":
                node["class_type"] = "OpaqueTextEncode"
        self._write(graph=graph)

        await self._scan()

        (key,) = self.api._untraced_inspections
        self.assertIsInstance(key, bytes)
        self.assertEqual(len(key), 16)


class TestMonitorNeverRecords(InspectionTestCase):
    async def test_monitor_never_records_an_unlinked_ambiguous_output(self):
        """Only scans add items (maintainer ruling); the monitor only clears
        the items of outputs it links."""
        self._roots()
        path = self.output_dir / "two.png"
        write_png(path, prompt=unlinkable_graph("cat", "dog"))

        with _counted(self.api._captures, "capture") as copies:
            self.assertEqual(self.api.record_review_file(str(path)), "skipped")

        self.assertEqual(copies, [])
        self.assertEqual(self._items(), [])
        await self._scan()
        self.assertEqual(len(self._items()), 1)


class TestStaleSettleContainment(InspectionTestCase):
    async def test_a_failed_stale_settle_is_logged_and_counted(self):
        import sqlite3

        self._roots()
        graph = qwen_edit_graph(*PROMPTS)
        for node in graph.values():
            if node["class_type"] == "TextEncodeQwenImageEdit":
                node["class_type"] = "OpaqueTextEncode"
        self._write(graph=graph)
        with (
            patch.object(
                self.api.db,
                "settle_stale_review_items",
                side_effect=sqlite3.OperationalError("database is locked"),
            ),
            self.assertLogs("prompt_manager.api", "WARNING") as logs,
        ):
            job = await self._scan()

        self.assertEqual(job["state"], "finished")
        self.assertEqual(job["scope"]["review_errors"], 1)
        self.assertTrue(any("Stale Needs review" in line for line in logs.output))
