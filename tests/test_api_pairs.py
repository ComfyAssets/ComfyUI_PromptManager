"""Pair routes preserve shared text and expose portable combinations."""

from tests.test_api_prompts import PromptAPITestCase


class TestPairAPI(PromptAPITestCase):
    async def _create(self, **overrides):
        payload = {"positive_text": "cat", "negative_text": "blur"}
        payload.update(overrides)
        return await self.client.post("/prompt_manager/pairs", json=payload)

    async def test_create_reuse_read_and_search(self):
        response = await self._create(tags=["studio"], title="Portrait")
        self.assertEqual(response.status, 200)
        pair = (await response.json())["pair"]
        response = await self._create(negative_text="noise")
        second = (await response.json())["pair"]
        self.assertEqual(pair["positive_prompt_id"], second["positive_prompt_id"])
        self.assertNotEqual(pair["id"], second["id"])
        response = await self.client.get("/prompt_manager/pairs?text=blur")
        data = await response.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["pairs"][0]["tags"], ["studio"])
        response = await self.client.get(f'/prompt_manager/pairs/{pair["id"]}')
        self.assertEqual((await response.json())["pair"]["title"], "Portrait")

    async def test_negative_must_be_explicit_but_can_be_empty(self):
        response = await self.client.post(
            "/prompt_manager/pairs", json={"positive_text": "cat"}
        )
        self.assertEqual(response.status, 400)
        response = await self._create(negative_text="")
        self.assertEqual(response.status, 200)
        self.assertIsNone((await response.json())["pair"]["negative_prompt_id"])

    async def test_invalid_payloads_are_400_without_writes(self):
        for body in (
            [],
            None,
            {},
            {"positive_text": 12, "negative_text": ""},
            {"positive_text": "cat", "negative_text": None},
            {"positive_text": "cat", "negative_text": "blur", "rating": True},
            {"positive_text": "cat", "negative_text": "blur", "tags": [12]},
        ):
            response = await self.client.post("/prompt_manager/pairs", json=body)
            self.assertEqual(response.status, 400, body)
        response = await self.client.get("/prompt_manager/pairs")
        self.assertEqual((await response.json())["total"], 0)

    async def test_repeated_save_does_not_overwrite_metadata(self):
        response = await self._create(title="Original", tags=["kept"])
        pair = (await response.json())["pair"]
        response = await self._create(title="Other", tags=["ignored"])
        repeated = (await response.json())["pair"]
        self.assertEqual(repeated["id"], pair["id"])
        self.assertEqual(repeated["title"], "Original")
        self.assertEqual(repeated["tags"], ["kept"])

    async def test_metadata_update_and_archive(self):
        pair = (await (await self._create()).json())["pair"]
        url = f'/prompt_manager/pairs/{pair["id"]}'
        response = await self.client.patch(url, json={"title": "New", "tags": ["a"]})
        self.assertEqual(response.status, 200)
        response = await self.client.patch(url, json={"positive_text": "dog"})
        self.assertEqual(response.status, 400)
        response = await self.client.patch(url, json={"archived": True})
        self.assertEqual(response.status, 200)
        self.assertEqual(
            (await (await self.client.get("/prompt_manager/pairs")).json())["total"], 0
        )
        response = await self.client.get("/prompt_manager/pairs?include_archived=true")
        self.assertEqual((await response.json())["total"], 1)
        response = await self.client.delete(url)
        self.assertEqual(response.status, 200)
        self.assertIsNotNone(self.api.db.get_prompt_by_id(pair["positive_prompt_id"]))

    async def test_export_is_versioned_and_contains_text_not_only_local_ids(self):
        await self._create(tags=["a"])
        response = await self.client.get("/prompt_manager/pairs/export")
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["pairs"][0]["positive_text"], "cat")
        self.assertEqual(data["pairs"][0]["negative_text"], "blur")
        self.assertNotIn("positive_prompt_id", data["pairs"][0])

    async def test_missing_invalid_ids_and_pagination(self):
        for verb in ("get", "delete"):
            for suffix, status in (("abc", 400), ("9999", 404)):
                response = await getattr(self.client, verb)(
                    f"/prompt_manager/pairs/{suffix}"
                )
                self.assertEqual(response.status, status)
        for query in ("limit=abc", "offset=abc", "include_archived=maybe"):
            response = await self.client.get("/prompt_manager/pairs?" + query)
            self.assertEqual(response.status, 400)
        response = await self.client.get("/prompt_manager/pairs?limit=99999&offset=-1")
        data = await response.json()
        self.assertEqual(data["limit"], 500)
        self.assertEqual(data["offset"], 0)

    async def test_old_edit_and_delete_routes_report_pair_dependency(self):
        pair = (await (await self._create()).json())["pair"]
        prompt_id = pair["positive_prompt_id"]
        response = await self.client.put(
            f"/prompt_manager/prompts/{prompt_id}", json={"text": "dog"}
        )
        self.assertEqual(response.status, 409)
        response = await self.client.delete(f"/prompt_manager/delete/{prompt_id}")
        self.assertEqual(response.status, 409)
        response = await self.client.post(
            "/prompt_manager/bulk/delete", json={"prompt_ids": [prompt_id]}
        )
        self.assertEqual(response.status, 409)

    async def test_recent_pairs_sort_by_use_and_reject_unknown_sort(self):
        first = (await (await self._create()).json())["pair"]
        await self._create(negative_text="noise")
        self.api.db.record_prompt_pair_use(first["id"])
        response = await self.client.get("/prompt_manager/pairs?sort=last_used_desc")
        self.assertEqual((await response.json())["pairs"][0]["id"], first["id"])
        response = await self.client.get("/prompt_manager/pairs?sort=invalid")
        self.assertEqual(response.status, 400)

    async def test_null_metadata_patch_is_noop_for_existing_pair(self):
        pair = (await (await self._create(rating=4)).json())["pair"]
        response = await self.client.patch(
            f'/prompt_manager/pairs/{pair["id"]}', json={"rating": None}
        )
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["pair"]["rating"], 4)
        response = await self.client.patch(
            "/prompt_manager/pairs/99999", json={"rating": None}
        )
        self.assertEqual(response.status, 404)

    async def test_database_failures_are_generic_and_bad_json_is_rejected(self):
        from unittest.mock import patch

        with patch.object(
            self.api.db,
            "save_prompt_pair",
            side_effect=RuntimeError("/private/db/path"),
        ):
            response = await self._create()
        self.assertEqual(response.status, 500)
        self.assertNotIn("/private", await response.text())
        response = await self.client.post(
            "/prompt_manager/pairs",
            data="{",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 400)
