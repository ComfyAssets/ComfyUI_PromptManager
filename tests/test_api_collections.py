"""Collections (#331): the routes under /prompt_manager/collections."""

import json
import ntpath
import sqlite3
from unittest.mock import patch

try:
    from test_api_images import ImageAPITestCase, make_png
    from open_handles import assert_closed
except ImportError:
    from tests.test_api_images import ImageAPITestCase, make_png
    from tests.open_handles import assert_closed

BASE = "/prompt_manager/collections"
ITEMS = BASE + "/items"
MEMBERSHIP = BASE + "/membership"
SOURCES_CHANGED = "The sources changed. Refresh the images and try again."


class CollectionRoutesTestCase(ImageAPITestCase):
    async def tearDownAsync(self):
        self.api.db.close_all()
        assert_closed(self, self._temp_db.name, self._output_tmp.name)
        await super().tearDownAsync()

    def make(self, *names, root=None):
        for name in names:
            make_png((root or self.output_dir) / name)

    def scope(self, index=0):
        roots = list(self.api._get_all_output_dirs())
        return self.api._gallery_root_scope(roots[index], index)

    def item(self, path, index=0, scope=None):
        return {
            "root_index": index,
            "root_scope": scope or self.scope(index),
            "path": path,
        }

    def two_sources(self):
        second = self.comfy_dir / "second"
        second.mkdir()
        self.api._get_all_output_dirs = lambda: [self.output_dir, second]
        return second

    async def call(self, method, url, body=None, status=200, **params):
        response = await self.client.request(method, url, json=body, params=params)
        text = await response.text()
        self.assertEqual(response.status, status, text)
        data = json.loads(text)
        self.assertNotIn(self.comfy_dir.as_posix(), text)
        self.assertNotIn(str(self.comfy_dir).replace("\\", "\\\\"), text)
        return data

    async def add(self, status=200, **body):
        return await self.call("POST", ITEMS, body, status)

    async def listed(self):
        return (await self.call("GET", BASE))["collections"]


class TestCollectionRoutes(CollectionRoutesTestCase):
    async def test_each_route_succeeds(self):
        self.make("a.png", "b.png", "c.png")
        self.assertEqual(
            await self.call("GET", BASE), {"success": True, "collections": []}
        )
        made = await self.add(
            name="  LoRa   testing ", items=[self.item("a.png"), self.item("b.png")]
        )
        grids = made["collection"]
        self.assertEqual(grids["name"], "LoRa testing")
        self.assertEqual(
            made,
            {
                "success": True,
                "collection": grids,
                "created": True,
                "added": 2,
                "already": 0,
                "not_found": 0,
            },
        )
        again = await self.add(
            collection_id=grids["id"], items=[self.item("b.png"), self.item("c.png")]
        )
        self.assertEqual(
            (again["created"], again["added"], again["already"]), (False, 1, 1)
        )
        self.assertEqual(
            await self.listed(),
            [{"id": grids["id"], "name": "LoRa testing", "count": 3, "missing": 0}],
        )
        membership = await self.call(
            "GET", MEMBERSHIP, root_index="0", root_scope=self.scope(), path="a.png"
        )
        self.assertEqual(membership, {"success": True, "collection_ids": [grids["id"]]})
        removed = await self.call(
            "DELETE",
            ITEMS,
            {"collection_id": grids["id"], "items": [self.item("a.png")]},
        )
        self.assertEqual(removed, {"success": True, "collection": grids, "removed": 1})
        renamed = await self.call(
            "PATCH", f"{BASE}/{grids['id']}", {"name": "LoRA grids"}
        )
        self.assertEqual(
            renamed,
            {"success": True, "collection": {"id": grids["id"], "name": "LoRA grids"}},
        )
        deleted = await self.call("DELETE", f"{BASE}/{grids['id']}")
        self.assertEqual(deleted, {"success": True, "deleted": True})
        self.assertEqual(await self.listed(), [])
        # Files are never touched.
        self.assertTrue((self.output_dir / "a.png").is_file())

    async def test_names_are_unique_ignoring_case(self):
        self.make("a.png", "b.png")
        first = (await self.add(name="LoRa testing", items=[self.item("a.png")]))[
            "collection"
        ]
        second = await self.add(name="lora TESTING", items=[self.item("b.png")])
        self.assertEqual(second["collection"], first)
        self.assertFalse(second["created"])
        self.assertEqual(len(await self.listed()), 1)

    async def test_bad_requests_are_400(self):
        self.make("a.png")
        made = await self.add(name="Set", items=[self.item("a.png")])
        cid = made["collection"]["id"]
        good = [self.item("a.png")]
        for body in (
            {"items": good},
            {"name": "x", "collection_id": cid, "items": good},
            {"name": "x"},
            {"name": "x", "items": good, "extra": 1},
            {"collection_id": "1", "items": good},
            {"collection_id": True, "items": good},
            {"collection_id": 0, "items": good},
            {"collection_id": 10**10, "items": good},
            {"name": "", "items": good},
            {"name": "x" * 101, "items": good},
            {"name": "bell\x07", "items": good},
            {"name": 5, "items": good},
            {"name": "x", "items": []},
            {"name": "x", "items": "a.png"},
            {"name": "x", "items": [self.item("a.png")] * 501},
            {"name": "x", "items": [{"root_index": 0, "path": "a.png"}]},
            {"name": "x", "items": [{**self.item("a.png"), "root_index": "0"}]},
            {"name": "x", "items": [{**self.item("a.png"), "root_scope": "xyz"}]},
        ):
            with self.subTest(body=body):
                data = await self.add(400, **body)
                self.assertFalse(data["success"])
        for body in ({"items": good}, {"collection_id": cid}):
            with self.subTest(remove=body):
                await self.call("DELETE", ITEMS, body, 400)
        for raw in ("abc", "0", "1234567890", "-1", "1.0"):
            with self.subTest(path_id=raw):
                await self.call("PATCH", f"{BASE}/{raw}", {"name": "x"}, 400)
                await self.call("DELETE", f"{BASE}/{raw}", None, 400)
        await self.call("PATCH", f"{BASE}/{cid}", {"name": ""}, 400)
        await self.call("PATCH", f"{BASE}/{cid}", {"label": "x"}, 400)
        for params in (
            {"root_index": "x", "root_scope": self.scope(), "path": "a.png"},
            {"root_index": "0", "path": "a.png"},
            {"root_index": "0", "root_scope": "nope", "path": "a.png"},
        ):
            with self.subTest(membership=params):
                await self.call("GET", MEMBERSHIP, status=400, **params)
        self.assertEqual((await self.listed())[0]["count"], 1)

    async def test_unsafe_paths_are_403(self):
        self.make("a.png")
        for path in (
            "../a.png",
            "sub/../../a.png",
            "C:/a.png",
            "a\\b.png",
            "/a.png",
            "notes.txt",
        ):
            with self.subTest(path=path):
                await self.add(403, name="Set", items=[self.item(path)])
                await self.call(
                    "GET",
                    MEMBERSHIP,
                    status=403,
                    root_index="0",
                    root_scope=self.scope(),
                    path=path,
                )
        self.assertEqual(await self.listed(), [])

    async def test_a_body_over_the_cap_is_413(self):
        response = await self.client.post(
            ITEMS, data=b'{"name":"x","items":[' + b" " * (1024 * 1024 + 1) + b"]}"
        )
        self.assertEqual(response.status, 413)

    async def test_missing_collection_and_files_are_404(self):
        self.make("a.png")
        data = await self.add(404, collection_id=999, items=[self.item("a.png")])
        self.assertEqual(data["code"], "collection-not-found")
        data = await self.call(
            "DELETE", ITEMS, {"collection_id": 999, "items": [self.item("a.png")]}, 404
        )
        self.assertEqual(data["code"], "collection-not-found")
        data = await self.call("PATCH", f"{BASE}/999", {"name": "x"}, 404)
        self.assertEqual(data["code"], "collection-not-found")
        data = await self.call("DELETE", f"{BASE}/999", None, 404)
        self.assertEqual(data["code"], "collection-not-found")
        data = await self.add(404, name="New", items=[self.item("never.png")])
        self.assertEqual(data["code"], "files-not-found")
        self.assertEqual(await self.listed(), [])

    async def test_unlisted_files_count_as_not_found(self):
        self.make("a.png")
        made = await self.add(
            name="Set", items=[self.item("a.png"), self.item("later.png")]
        )
        self.assertEqual((made["added"], made["not_found"]), (1, 1))
        again = await self.add(
            collection_id=made["collection"]["id"],
            items=[self.item("a.png"), self.item("later.png")],
        )
        self.assertEqual(
            (again["added"], again["already"], again["not_found"]), (0, 1, 1)
        )

    async def test_name_taken_is_409(self):
        self.make("a.png")
        first = (await self.add(name="First", items=[self.item("a.png")]))["collection"]
        second = (await self.add(name="Second", items=[self.item("a.png")]))[
            "collection"
        ]
        data = await self.call(
            "PATCH", f"{BASE}/{second['id']}", {"name": "FIRST"}, 409
        )
        self.assertEqual(
            (data["code"], data["collection_id"]), ("name-taken", first["id"])
        )
        renamed = await self.call("PATCH", f"{BASE}/{second['id']}", {"name": "SECOND"})
        self.assertEqual(renamed["collection"]["name"], "SECOND")

    async def test_stale_scope_is_409_after_reordering_sources(self):
        second = self.two_sources()
        self.make("a.png")
        self.make("b.png", root=second)
        stale = self.item("b.png", 1)
        made = await self.add(name="Set", items=[self.item("a.png")])
        cid = made["collection"]["id"]
        self.api._get_all_output_dirs = lambda: [second, self.output_dir]
        for method, body in (
            ("POST", {"collection_id": cid, "items": [self.item("b.png", 0), stale]}),
            ("DELETE", {"collection_id": cid, "items": [stale]}),
        ):
            with self.subTest(method=method):
                data = await self.call(method, ITEMS, body, 409)
                self.assertEqual(data["code"], "source-changed")
                self.assertEqual(data["error"], SOURCES_CHANGED)
        data = await self.call(
            "GET",
            MEMBERSHIP,
            status=409,
            root_index="1",
            root_scope=stale["root_scope"],
            path="b.png",
        )
        self.assertEqual(data["code"], "source-changed")
        # Nothing was written by the refused add; the member follows its folder.
        listed = await self.listed()
        self.assertEqual((listed[0]["count"], listed[0]["missing"]), (1, 0))
        membership = await self.call(
            "GET", MEMBERSHIP, root_index="1", root_scope=self.scope(1), path="a.png"
        )
        self.assertEqual(membership["collection_ids"], [cid])

    async def test_counts_exclude_a_missing_member(self):
        second = self.two_sources()
        self.make("a.png", "b.png")
        self.make("c.png", root=second)
        made = await self.add(
            name="Grids",
            items=[self.item("a.png"), self.item("b.png"), self.item("c.png", 1)],
        )
        other = await self.add(name="Other", items=[self.item("c.png", 1)])
        (self.output_dir / "b.png").unlink()
        self.api.invalidate_gallery_cache()
        listed = await self.listed()
        self.assertEqual(
            listed,
            [
                {
                    "id": made["collection"]["id"],
                    "name": "Grids",
                    "count": 2,
                    "missing": 1,
                },
                {
                    "id": other["collection"]["id"],
                    "name": "Other",
                    "count": 1,
                    "missing": 0,
                },
            ],
        )
        # The member is removable although it is no longer listed.
        removed = await self.call(
            "DELETE",
            ITEMS,
            {"collection_id": made["collection"]["id"], "items": [self.item("b.png")]},
        )
        self.assertEqual(removed["removed"], 1)

    async def test_counts_are_null_when_a_source_scan_fails(self):
        self.make("a.png")
        await self.add(name="Set", items=[self.item("a.png")])

        async def broken(_root):
            raise OSError("scan failed at /secret/place")

        with patch.object(self.api, "_get_gallery_files", side_effect=broken):
            data = await self.call("GET", BASE)
        self.assertEqual(
            data["collections"][0] | {"id": 0},
            {"id": 0, "name": "Set", "count": None, "missing": None},
        )
        self.assertNotIn("secret", json.dumps(data))

    async def test_windows_case_spellings_are_one_member(self):
        self.make("Sub/A.png")
        with patch("py.api.images._path_flavor", ntpath):
            made = await self.add(
                name="Set", items=[self.item("Sub/A.png"), self.item("SUB/a.PNG")]
            )
            self.assertEqual((made["added"], made["already"]), (1, 0))
            (self.output_dir / "Sub" / "A.png").rename(
                self.output_dir / "Sub" / "a.png"
            )
            (self.output_dir / "Sub").rename(self.output_dir / "sub")
            self.api.invalidate_gallery_cache()
            listed = await self.listed()
            self.assertEqual((listed[0]["count"], listed[0]["missing"]), (1, 0))
            membership = await self.call(
                "GET",
                MEMBERSHIP,
                root_index="0",
                root_scope=self.scope(),
                path="sub/a.png",
            )
            self.assertEqual(membership["collection_ids"], [made["collection"]["id"]])
            response = await self.client.get(
                "/prompt_manager/images/output",
                params={
                    "mode": "workspace",
                    "collection": str(made["collection"]["id"]),
                },
            )
            page = await response.json()
            self.assertEqual([i["path"] for i in page["images"]], ["sub/a.png"])

    async def test_posix_case_distinct_files_stay_two_members(self):
        self.make("A.png", "a.png")
        if len(list(self.output_dir.iterdir())) != 2:
            self.skipTest("case-insensitive filesystem")
        made = await self.add(
            name="Set", items=[self.item("A.png"), self.item("a.png")]
        )
        self.assertEqual(made["added"], 2)

    async def test_a_500_never_carries_the_exception_text(self):
        self.make("a.png")
        made = await self.add(name="Set", items=[self.item("a.png")])
        cid = made["collection"]["id"]

        def broken(*_args, **_kwargs):
            raise sqlite3.OperationalError("disk I/O error at /secret/prompts.db")

        cases = (
            (
                "list_collections",
                "GET",
                BASE,
                None,
                {},
                "Collections could not be read.",
            ),
            (
                "file_collections",
                "GET",
                MEMBERSHIP,
                None,
                {"root_index": "0", "root_scope": self.scope(), "path": "a.png"},
                "Collections could not be read.",
            ),
            (
                "add_to_collection",
                "POST",
                ITEMS,
                {"collection_id": cid, "items": [self.item("a.png")]},
                {},
                "The collection could not be changed.",
            ),
            (
                "remove_from_collection",
                "DELETE",
                ITEMS,
                {"collection_id": cid, "items": [self.item("a.png")]},
                {},
                "The collection could not be changed.",
            ),
            (
                "rename_collection",
                "PATCH",
                f"{BASE}/{cid}",
                {"name": "x"},
                {},
                "The collection could not be changed.",
            ),
            (
                "delete_collection",
                "DELETE",
                f"{BASE}/{cid}",
                None,
                {},
                "The collection could not be changed.",
            ),
        )
        for method_name, method, url, body, params, message in cases:
            with self.subTest(method=method_name):
                with patch.object(self.api.db, method_name, side_effect=broken):
                    data = await self.call(method, url, body, 500, **params)
                self.assertEqual(data, {"success": False, "error": message})


class TestCollectionListingFilter(CollectionRoutesTestCase):
    """#392's "all N matching" filter carries ``collection`` like the listing."""

    async def test_a_filter_with_a_collection_resolves_only_members(self):
        second = self.two_sources()
        self.make("a.png", "b.png")
        self.make("c.png", root=second)
        made = await self.add(
            name="Set", items=[self.item("a.png"), self.item("c.png", 1)]
        )
        cid = str(made["collection"]["id"])
        selection = await self.api.resolve_listing_filter({"collection": cid})
        self.assertEqual(
            sorted((index, path) for index, _scope, path in selection.targets),
            [(0, "a.png"), (1, "c.png")],
        )
        selection = await self.api.resolve_listing_filter(
            {"collection": cid, "root": "1", "root_scope": self.scope(1)}
        )
        self.assertEqual([t[2] for t in selection.targets], ["c.png"])
        for bad in ("0", "x", "1234567890"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    from py.api.images import parse_listing_filter

                    parse_listing_filter({"collection": bad})

    async def test_bulk_category_acts_on_collection_members_only(self):
        self.make("a.png", "b.png")
        inside = self._save_prompt("Inside", category="Old")
        outside = self._save_prompt("Outside", category="Old")
        self.api.db.link_image_to_prompt(inside, str(self.output_dir / "a.png"))
        self.api.db.link_image_to_prompt(outside, str(self.output_dir / "b.png"))
        made = await self.add(name="Set", items=[self.item("a.png")])
        cid = str(made["collection"]["id"])
        data = await self.call(
            "POST",
            "/prompt_manager/bulk/category",
            {"images_filter": {"collection": cid}, "category": "New"},
        )
        self.assertEqual((data["updated_count"], data["prompt_count"]), (1, 1))
        self.assertEqual(self.api.db.get_prompt_by_id(inside)["category"], "New")
        self.assertEqual(self.api.db.get_prompt_by_id(outside)["category"], "Old")

    async def test_a_deleted_collection_in_a_bulk_filter_is_a_404(self):
        self.make("a.png")
        data = await self.call(
            "POST",
            "/prompt_manager/bulk/category",
            {"images_filter": {"collection": "77"}, "category": "New"},
            status=404,
        )
        self.assertEqual(data["code"], "collection-not-found")
        with self.assertRaises(Exception) as caught:
            await self.api.resolve_listing_filter({"collection": "77"})
        self.assertEqual(type(caught.exception).__name__, "CollectionMissing")


class TestReviewFindings(CollectionRoutesTestCase):
    """PR #400 review: loop work, counts snapshot, id and query rules."""

    async def test_no_realpath_runs_on_the_event_loop(self):
        import os
        import threading

        names = [f"img{n}.png" for n in range(20)]
        self.make(*names)
        await self.call("GET", BASE)  # warm the listing cache
        loop_thread = threading.current_thread()
        on_loop = []
        real = os.path.realpath

        def watched(*args, **kwargs):
            if threading.current_thread() is loop_thread:
                on_loop.append(args[0])
            return real(*args, **kwargs)

        items = [self.item(name) for name in names]
        cid = None
        with patch("os.path.realpath", side_effect=watched):
            made = await self.add(name="Set", items=items)
            cid = made["collection"]["id"]
            await self.call("DELETE", ITEMS, {"collection_id": cid, "items": items[:5]})
            await self.call(
                "GET",
                MEMBERSHIP,
                root_index="0",
                root_scope=items[0]["root_scope"],
                path="img9.png",
            )
            await self.call("GET", BASE)
        self.assertEqual(on_loop, [])

    async def test_missing_is_never_negative(self):
        self.make("a.png", "b.png")
        made = await self.add(
            name="Set", items=[self.item("a.png"), self.item("b.png")]
        )
        cid = made["collection"]["id"]
        real = self.api.db.collection_count_snapshot

        def raced(root_keys):
            members, stored = real(root_keys)
            return members, {cid: 1}  # a remove landed between two reads

        with patch.object(self.api.db, "collection_count_snapshot", side_effect=raced):
            listed = await self.listed()
        self.assertEqual((listed[0]["count"], listed[0]["missing"]), (2, 0))

    async def test_path_ids_follow_the_listing_rule(self):
        self.make("a.png")
        made = await self.add(name="Set", items=[self.item("a.png")])
        cid = made["collection"]["id"]
        for raw in ("0", "007", f"0{cid}"):
            with self.subTest(raw=raw):
                await self.call("PATCH", f"{BASE}/{raw}", {"name": "x"}, 400)
                await self.call("DELETE", f"{BASE}/{raw}", None, 400)
        self.assertEqual(len(await self.listed()), 1)

    async def test_membership_refuses_repeated_query_keys(self):
        self.make("a.png", "b.png")
        response = await self.client.get(
            MEMBERSHIP
            + f"?root_index=0&root_scope={self.scope()}&path=a.png&path=b.png"
        )
        self.assertEqual(response.status, 400)
        response = await self.client.get(
            MEMBERSHIP
            + f"?root_index=0&root_index=0&root_scope={self.scope()}&path=a.png"
        )
        self.assertEqual(response.status, 400)

    async def test_deleting_a_collection_leaves_everything_else(self):
        from py.api.images import _favorite_root_key

        try:
            from tests.test_annotation_schema import insert_group_rows, table_rows
        except ImportError:
            from test_annotation_schema import insert_group_rows, table_rows

        self.make("a.png")
        db = self.api.db
        db.set_image_favorite(_favorite_root_key(self.output_dir), "a.png")
        prompt = self._save_prompt("Linked")
        db.link_image_to_prompt(prompt, str(self.output_dir / "a.png"))
        with db.model.get_connection() as conn:
            insert_group_rows(conn, locator=str(self.output_dir / "a.png"))
        made = await self.add(name="Set", items=[self.item("a.png")])
        tables = (
            "image_favorites",
            "generated_images",
            "prompts",
            "tags",
            "media_annotation_files",
            "media_annotation_bytes",
            "media_image_tags",
        )
        before = table_rows(db.model.get_connection(), tables)
        file_bytes = (self.output_dir / "a.png").read_bytes()
        await self.call("DELETE", f"{BASE}/{made['collection']['id']}")
        self.assertEqual(table_rows(db.model.get_connection(), tables), before)
        self.assertEqual((self.output_dir / "a.png").read_bytes(), file_bytes)
        self.assertTrue(all(before[t] for t in tables))
