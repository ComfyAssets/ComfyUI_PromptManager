"""
Route tests for the prompt API (py/api/prompts.py).
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from host_stubs import install_folder_paths_stub
except ImportError:  # imported as tests.test_api_prompts
    from tests.host_stubs import install_folder_paths_stub

install_folder_paths_stub()
sys.modules.setdefault("server", MagicMock())

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import AioHTTPTestCase  # noqa: E402

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.operations import PromptDatabase  # noqa: E402
from py.api import PromptManagerAPI  # noqa: E402
from tests.private_temp import private_temp_db  # noqa: E402
import py.api.prompts as prompts_module  # noqa: E402
from py.api.prompts import (  # noqa: E402
    BULK_CATEGORY_MAX_PROMPTS,
    MAX_PAGE_LIMIT,
    MAX_PAGE_OFFSET,
    safe_error_message,
)
from utils.hashing import generate_prompt_hash  # noqa: E402


class PromptAPITestCase(AioHTTPTestCase):
    """App with PromptManager routes and a temporary SQLite database."""

    async def get_application(self):
        self._temp_db = private_temp_db(self)

        app = web.Application()
        routes = web.RouteTableDef()

        self.api = PromptManagerAPI(db=PromptDatabase(self._temp_db.name))
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
        await super().tearDownAsync()  # closes the aiohttp test client
        # Windows refuses to unlink a database that still has open handles.
        self.api.db.close_all()
        assert_closed(self, self._temp_db.name)
        for path in (
            self._temp_db.name,
            self._temp_db.name + "-wal",
            self._temp_db.name + "-shm",
        ):
            if os.path.exists(path):
                os.unlink(path)

    def _save_prompt(self, text="Test prompt", **kwargs):
        return self.api.db.save_prompt(
            text=text, prompt_hash=generate_prompt_hash(text), **kwargs
        )


class TestSafeErrorMessage(unittest.TestCase):
    """Error strings returned to clients never contain absolute server paths."""

    def test_oserror_uses_strerror_and_basename(self):
        secret = os.path.join(os.sep, "srv", "comfy", "output", "hidden.png")
        exc = FileNotFoundError(2, "No such file or directory", secret)

        message = safe_error_message(exc)

        self.assertNotIn(os.path.dirname(secret), message)
        self.assertIn("No such file or directory", message)
        self.assertIn("hidden.png", message)

    def test_oserror_without_filename(self):
        exc = PermissionError(13, "Permission denied")

        self.assertEqual(safe_error_message(exc), "Permission denied")

    def test_plain_exception_uses_str(self):
        self.assertEqual(safe_error_message(ValueError("bad value")), "bad value")

    def test_empty_message_falls_back_to_type_name(self):
        self.assertEqual(safe_error_message(RuntimeError()), "RuntimeError")


class TestSearchBounds(PromptAPITestCase):
    """GET /prompt_manager/search clamps limit/offset and rejects junk."""

    async def test_limit_is_clamped_to_max_page_limit(self):
        resp = await self.client.request("GET", "/prompt_manager/search?limit=999999")

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertLessEqual(len(data["results"]), MAX_PAGE_LIMIT)
        self.assertEqual(data["pagination"]["limit"], MAX_PAGE_LIMIT)

    async def test_zero_limit_is_raised_to_one(self):
        self._save_prompt("only one")
        resp = await self.client.request("GET", "/prompt_manager/search?limit=0")

        data = await resp.json()
        self.assertEqual(data["pagination"]["limit"], 1)
        self.assertEqual(len(data["results"]), 1)

    async def test_non_integer_limit_is_400(self):
        resp = await self.client.request("GET", "/prompt_manager/search?limit=abc")

        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["success"])
        self.assertIn("error", data)

    async def test_non_integer_offset_is_400(self):
        resp = await self.client.request("GET", "/prompt_manager/search?offset=1.5")

        self.assertEqual(resp.status, 400)

    async def test_negative_offset_is_clamped_to_zero(self):
        # Clamped, not rejected: paging backwards past page one yields page one.
        resp = await self.client.request("GET", "/prompt_manager/search?offset=-5")

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["pagination"]["offset"], 0)

    async def test_offset_beyond_int64_is_clamped_not_500(self):
        resp = await self.client.request(
            "GET", "/prompt_manager/search?offset=99999999999999999999999"
        )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["pagination"]["offset"], MAX_PAGE_OFFSET)

    async def test_offset_skips_results(self):
        for i in range(3):
            self._save_prompt(f"searchable {i}")

        resp = await self.client.request(
            "GET", "/prompt_manager/search?text=searchable&limit=2&offset=2"
        )

        data = await resp.json()
        self.assertEqual(len(data["results"]), 1)


class TestRecentBounds(PromptAPITestCase):
    """GET /prompt_manager/recent clamps limit/offset and rejects junk."""

    async def test_limit_is_clamped_to_max_page_limit(self):
        resp = await self.client.request("GET", "/prompt_manager/recent?limit=999999")

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertLessEqual(len(data["results"]), MAX_PAGE_LIMIT)
        self.assertEqual(data["pagination"]["limit"], MAX_PAGE_LIMIT)

    async def test_non_integer_limit_is_400(self):
        resp = await self.client.request("GET", "/prompt_manager/recent?limit=abc")

        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["success"])

    async def test_non_integer_page_is_400(self):
        resp = await self.client.request("GET", "/prompt_manager/recent?page=two")

        self.assertEqual(resp.status, 400)

    async def test_negative_offset_is_clamped_to_zero(self):
        # Clamped, not rejected: paging backwards past page one yields page one.
        resp = await self.client.request("GET", "/prompt_manager/recent?offset=-5")

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["pagination"]["offset"], 0)

    async def test_page_param_derives_offset(self):
        for i in range(5):
            self._save_prompt(f"paged {i}")

        resp = await self.client.request("GET", "/prompt_manager/recent?limit=2&page=3")

        data = await resp.json()
        self.assertEqual(data["pagination"]["offset"], 4)
        self.assertEqual(len(data["results"]), 1)

    async def test_huge_page_number_is_clamped_not_500(self):
        resp = await self.client.request(
            "GET", f"/prompt_manager/recent?limit={MAX_PAGE_LIMIT}&page={10**30}"
        )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["pagination"]["offset"], MAX_PAGE_OFFSET)
        self.assertEqual(data["results"], [])


def _raise(*_args, **_kwargs):
    raise RuntimeError("boom")


class RouteCoverageCase(PromptAPITestCase):
    """Helpers for driving success and failure branches of each route."""

    def _break(self, db_method):
        """Make a DB method raise so the route's 500 branch runs."""
        setattr(self.api.db, db_method, _raise)

    def _stub(self, db_method, value):
        setattr(self.api.db, db_method, lambda *a, **k: value)

    async def _json(self, method, path, **kwargs):
        resp = await self.client.request(method, path, **kwargs)
        return resp.status, await resp.json()


class TestListRoutesErrorPaths(RouteCoverageCase):

    async def test_search_db_failure_is_500(self):
        self._break("search_prompts")
        status, data = await self._json("GET", "/prompt_manager/search")
        self.assertEqual(status, 500)
        self.assertFalse(data["success"])
        self.assertEqual(data["results"], [])

    async def test_search_ignores_non_integer_min_rating(self):
        self._save_prompt("rated", rating=3)
        status, data = await self._json("GET", "/prompt_manager/search?min_rating=x")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["results"]), 1)

    async def test_recent_db_failure_is_500(self):
        self._break("get_recent_prompts")
        status, data = await self._json("GET", "/prompt_manager/recent")
        self.assertEqual(status, 500)
        self.assertEqual(data["pagination"]["total"], 0)

    async def test_categories_and_tags_db_failures_are_500(self):
        self._break("get_all_categories")
        self._break("get_all_tags")
        c_status, c_data = await self._json("GET", "/prompt_manager/categories")
        t_status, t_data = await self._json("GET", "/prompt_manager/tags")
        self.assertEqual((c_status, t_status), (500, 500))
        self.assertEqual(c_data["categories"], [])
        self.assertEqual(t_data["tags"], [])

    async def test_tags_lists_unused_row_and_suggest_shape_is_stable(self):
        pid = self._save_prompt("P1", tags=["used", "unused"])
        self.api.db.set_prompt_tags(pid, ["used"])
        status, data = await self._json("GET", "/prompt_manager/tags")
        self.assertEqual(status, 200)
        self.assertEqual(data["tags"], ["unused", "used"])
        s_status, s_data = await self._json("GET", "/prompt_manager/tags/suggest?q=")
        self.assertEqual(s_status, 200)
        self.assertEqual(
            s_data["suggestions"],
            [{"name": "used", "count": 1}, {"name": "unused", "count": 0}],
        )

    async def test_subfolders_success_and_failure(self):
        ok_status, ok_data = await self._json(
            "GET", "/prompt_manager/subfolders?include_ancestors=true"
        )
        self._break("get_prompt_subfolders")
        bad_status, _ = await self._json("GET", "/prompt_manager/subfolders")
        self.assertEqual(ok_status, 200)
        self.assertEqual(ok_data["subfolders"], [])
        self.assertEqual(bad_status, 500)

    async def test_export_db_failure_is_500(self):
        self._break("search_prompts")
        status, data = await self._json("GET", "/prompt_manager/export")
        self.assertEqual(status, 500)
        self.assertFalse(data["success"])


class TestTagStatsAndFilters(RouteCoverageCase):

    async def test_tags_stats_rejects_bad_limit(self):
        status, _ = await self._json("GET", "/prompt_manager/tags/stats?limit=x")
        self.assertEqual(status, 400)

    async def test_tags_stats_db_failure_is_500(self):
        self._break("get_tags_with_counts")
        status, _ = await self._json("GET", "/prompt_manager/tags/stats")
        self.assertEqual(status, 500)

    async def test_tag_prompts_success(self):
        self._save_prompt("P1", tags=["sky"])
        status, data = await self._json("GET", "/prompt_manager/tags/sky/prompts")
        self.assertEqual(status, 200)
        self.assertEqual(data["tag"], "sky")
        self.assertEqual(len(data["prompts"]), 1)
        self.assertIn("pagination", data)

    async def test_tag_prompts_bad_limit_and_db_failure(self):
        bad_status, _ = await self._json(
            "GET", "/prompt_manager/tags/sky/prompts?offset=z"
        )
        self._break("get_prompts_by_tags")
        err_status, _ = await self._json("GET", "/prompt_manager/tags/sky/prompts")
        self.assertEqual((bad_status, err_status), (400, 500))

    async def test_tags_filter_untagged_mode(self):
        self._save_prompt("no tags")
        self._save_prompt("tagged", tags=["x"])
        status, data = await self._json(
            "GET", "/prompt_manager/tags/filter?untagged=true"
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["mode"], "untagged")
        self.assertEqual(len(data["prompts"]), 1)

    async def test_tags_filter_untagged_bad_limit(self):
        status, _ = await self._json(
            "GET", "/prompt_manager/tags/filter?untagged=true&limit=x"
        )
        self.assertEqual(status, 400)

    async def test_tags_filter_requires_tags(self):
        status, _ = await self._json("GET", "/prompt_manager/tags/filter")
        self.assertEqual(status, 400)

    async def test_tags_filter_and_or_modes(self):
        self._save_prompt("both", tags=["a", "b"])
        self._save_prompt("only a", tags=["a"])
        _, and_data = await self._json(
            "GET", "/prompt_manager/tags/filter?tags=a,b&mode=and"
        )
        _, or_data = await self._json(
            "GET", "/prompt_manager/tags/filter?tags=a,b&mode=weird"
        )
        self.assertEqual(len(and_data["prompts"]), 1)
        self.assertEqual(or_data["mode"], "and")
        self.assertEqual(len(or_data["prompts"]), 1)

    async def test_tags_filter_bad_limit_and_db_failure(self):
        bad_status, _ = await self._json(
            "GET", "/prompt_manager/tags/filter?tags=a&limit=x"
        )
        self._break("get_prompts_by_tags")
        err_status, _ = await self._json("GET", "/prompt_manager/tags/filter?tags=a")
        self.assertEqual((bad_status, err_status), (400, 500))


class TestTagSuggestRoute(RouteCoverageCase):
    """GET /prompt_manager/tags/suggest?q=&with=&limit="""

    URL = "/prompt_manager/tags/suggest"

    def _seed(self):
        for text, tags in (
            ("P1", ["asian", "portrait", "smile"]),
            ("P2", ["asian", "portrait"]),
            ("P3", ["portrait", "dog"]),
        ):
            self.api.db.save_prompt(text, "test", tags)

    async def test_suggestions_narrow_by_context_tags(self):
        self._seed()

        status, data = await self._json("GET", f"{self.URL}?q=&with=asian")

        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertEqual(
            [(t["name"], t["count"]) for t in data["suggestions"]],
            [("portrait", 2), ("smile", 1)],
        )

    async def test_prefix_and_messy_context_parsing(self):
        self._seed()

        status, data = await self._json(
            "GET", f"{self.URL}?q=S&with=%20asian%2C%2Cportrait%2Casian%20"
        )

        self.assertEqual(status, 200)
        self.assertEqual([t["name"] for t in data["suggestions"]], ["smile"])

    async def test_limit_is_clamped_and_route_is_not_a_tag_name(self):
        self._seed()

        status, data = await self._json("GET", f"{self.URL}?limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["suggestions"]), 1)

        status, data = await self._json("GET", f"{self.URL}?limit=9999")
        self.assertEqual(status, 200)
        self.assertLessEqual(len(data["suggestions"]), 50)

        status, data = await self._json("GET", f"{self.URL}?limit=abc")
        self.assertEqual(status, 200)

    async def test_db_failure_is_500(self):
        self._break("suggest_tags")
        status, data = await self._json("GET", self.URL)
        self.assertEqual(status, 500)
        self.assertFalse(data["success"])


class TestTagRoutePagingBounds(RouteCoverageCase):
    """Tag listings clamp limit/offset like every other list endpoint."""

    ROUTES = (
        "/prompt_manager/tags/stats",
        "/prompt_manager/tags/sky/prompts",
        "/prompt_manager/tags/filter?tags=sky",
        "/prompt_manager/tags/filter?untagged=true",
    )

    @staticmethod
    def _with(route, query):
        return f"{route}{'&' if '?' in route else '?'}{query}"

    async def test_limit_is_clamped_to_max_page_limit(self):
        for route in self.ROUTES:
            status, data = await self._json("GET", self._with(route, "limit=999999"))
            self.assertEqual(status, 200, route)
            self.assertEqual(data["pagination"]["limit"], MAX_PAGE_LIMIT, route)

    async def test_zero_limit_becomes_one(self):
        for route in self.ROUTES:
            _, data = await self._json("GET", self._with(route, "limit=0"))
            self.assertEqual(data["pagination"]["limit"], 1, route)

    async def test_negative_offset_is_clamped_to_zero(self):
        for route in self.ROUTES:
            _, data = await self._json("GET", self._with(route, "offset=-7"))
            self.assertEqual(data["pagination"]["offset"], 0, route)

    async def test_huge_offset_is_clamped_not_500(self):
        for route in self.ROUTES:
            status, data = await self._json(
                "GET", self._with(route, f"offset={10**30}")
            )
            self.assertEqual(status, 200, route)
            self.assertEqual(data["pagination"]["offset"], MAX_PAGE_OFFSET, route)

    async def test_non_integer_values_are_400(self):
        for route in self.ROUTES:
            status, data = await self._json("GET", self._with(route, "limit=1.5"))
            self.assertEqual(status, 400, route)
            self.assertFalse(data["success"], route)


class TestPublicPathsInPromptResponses(RouteCoverageCase):
    """Preview images on prompt listings never expose the server's directories."""

    SECRET_DIR = os.path.join(os.sep, "srv", "secret")

    async def setUpAsync(self):
        await super().setUpAsync()
        self.pid = self._save_prompt("has preview", tags=["sky"])
        image_path = os.path.join(self.SECRET_DIR, "gen.png")
        self.assertGreater(self.api.db.link_image_to_prompt(self.pid, image_path), 0)

    async def _body(self, method, path, **kwargs):
        resp = await self.client.request(method, path, **kwargs)
        text = await resp.text()
        self.assertNotIn(self.SECRET_DIR, text, path)
        return resp.status, json.loads(text)

    async def test_listing_routes_publish_preview_image_paths(self):
        routes = {
            "/prompt_manager/recent": "results",
            "/prompt_manager/search?q=preview": "results",
            "/prompt_manager/tags/sky/prompts": "prompts",
            "/prompt_manager/tags/filter?tags=sky": "prompts",
        }
        for route, key in routes.items():
            status, data = await self._body("GET", route)

            self.assertEqual(status, 200, route)
            image = data[key][0]["images"][0]
            # No ComfyUI anchor is configured here, so only the name survives.
            self.assertEqual(image["image_path"], "gen.png", route)
            self.assertNotIn("file_path", image, route)

    async def test_oserror_bodies_keep_only_the_basename(self):
        def boom(*_args, **_kwargs):
            raise FileNotFoundError(
                2, "No such file", os.path.join(self.SECRET_DIR, "prompts.db")
            )

        for db_method, route in (
            ("get_tags_with_counts", "/prompt_manager/tags/stats"),
            ("search_prompts", "/prompt_manager/search"),
            ("get_recent_prompts", "/prompt_manager/recent"),
            ("get_prompts_by_tags", "/prompt_manager/tags/sky/prompts"),
        ):
            setattr(self.api.db, db_method, boom)
            status, data = await self._body("GET", route)

            self.assertEqual(status, 500, route)
            self.assertIn("prompts.db", data["error"], route)


class TestTagNamesAreDecodedOnce(RouteCoverageCase):
    """aiohttp already decodes match_info; a tag literally named '%41' stays '%41'."""

    async def setUpAsync(self):
        await super().setUpAsync()
        self.pid = self._save_prompt("percent tag", tags=["%41"])

    async def test_tag_prompts_lookup(self):
        status, data = await self._json("GET", "/prompt_manager/tags/%2541/prompts")

        self.assertEqual(status, 200)
        self.assertEqual(data["tag"], "%41")
        self.assertEqual(len(data["prompts"]), 1)

    async def test_rename_tag(self):
        status, data = await self._json(
            "PUT", "/prompt_manager/tags/%2541", json={"new_name": "renamed"}
        )

        self.assertEqual(status, 200)
        self.assertEqual(data["old_name"], "%41")
        self.assertEqual(data["affected_count"], 1)
        self.assertEqual(self.api.db.get_prompt_by_id(self.pid)["tags"], ["renamed"])

    async def test_delete_tag(self):
        status, data = await self._json("DELETE", "/prompt_manager/tags/%2541")

        self.assertEqual(status, 200)
        self.assertEqual(data["tag_name"], "%41")
        self.assertEqual(data["affected_count"], 1)
        self.assertEqual(self.api.db.get_prompt_by_id(self.pid)["tags"], [])


class TestTagInputValidation(RouteCoverageCase):
    """Tag mutations run validate_tags: bad input is a 400, never a traceback."""

    def _requests(self, tag):
        pid = self._save_prompt("validate me", tags=["old"])
        return (
            ("POST", f"/prompt_manager/prompts/{pid}/tags", {"tag": tag}),
            ("POST", "/prompt_manager/prompts/tags", {"prompt_id": pid, "tags": [tag]}),
            ("POST", "/prompt_manager/bulk/tags", {"prompt_ids": [pid], "tags": [tag]}),
            (
                "POST",
                "/prompt_manager/tags/merge",
                {"source_tags": ["old"], "target_tag": tag},
            ),
            (
                "POST",
                "/prompt_manager/tags/merge",
                {"source_tags": [tag], "target_tag": "t"},
            ),
            ("PUT", "/prompt_manager/tags/old", {"new_name": tag}),
        )

    async def _assert_all_400(self, tag):
        for method, route, body in self._requests(tag):
            status, data = await self._json(method, route, json=body)

            self.assertEqual(status, 400, (route, body))
            self.assertFalse(data["success"], route)
            self.assertIn("error", data, route)

    async def test_non_string_tags_are_400(self):
        await self._assert_all_400(123)

    async def test_overlong_tags_are_400(self):
        await self._assert_all_400("x" * 51)

    async def test_control_characters_are_400(self):
        await self._assert_all_400("bad\x00tag")


class TestExportPagesThroughAllPrompts(RouteCoverageCase):
    """GET /prompt_manager/export never truncates: it pages until exhausted."""

    async def test_export_is_complete_beyond_one_page(self):
        ids = {self._save_prompt(f"export {i}") for i in range(5)}
        real_search = self.api.db.search_prompts
        calls = []

        def spy(*args, **kwargs):
            calls.append((kwargs.get("limit"), kwargs.get("offset")))
            return real_search(*args, **kwargs)

        self.api.db.search_prompts = spy

        with patch.object(prompts_module, "EXPORT_PAGE_SIZE", 2):
            resp = await self.client.request("GET", "/prompt_manager/export")

        self.assertEqual(resp.status, 200)
        data = json.loads(await resp.text())
        self.assertEqual(data["total_prompts"], 5)
        self.assertEqual({p["id"] for p in data["prompts"]}, ids)
        self.assertEqual(calls, [(2, 0), (2, 2), (2, 4)])


class TestTagMutationRoutes(RouteCoverageCase):

    async def test_rename_rejects_bad_json_and_empty_name(self):
        bad_json, _ = await self._json(
            "PUT",
            "/prompt_manager/tags/old",
            data=b"{nope",
            headers={"Content-Type": "application/json"},
        )
        empty, _ = await self._json(
            "PUT", "/prompt_manager/tags/old", json={"new_name": "  "}
        )
        self.assertEqual((bad_json, empty), (400, 400))

    async def test_rename_reports_skipped_rows_and_db_failure(self):
        self._stub("rename_tag_all_prompts", {"affected_count": 1, "skipped_count": 2})
        status, data = await self._json(
            "PUT", "/prompt_manager/tags/old", json={"new_name": "new"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["skipped_count"], 2)
        self.assertIn("warning", data)
        self._break("rename_tag_all_prompts")
        err_status, _ = await self._json(
            "PUT", "/prompt_manager/tags/old", json={"new_name": "new"}
        )
        self.assertEqual(err_status, 500)

    async def test_delete_tag_reports_skipped_rows_and_db_failure(self):
        self._stub("delete_tag_all_prompts", {"affected_count": 3, "skipped_count": 1})
        status, data = await self._json("DELETE", "/prompt_manager/tags/gone")
        self.assertEqual(status, 200)
        self.assertEqual(data["skipped_count"], 1)
        self._break("delete_tag_all_prompts")
        err_status, _ = await self._json("DELETE", "/prompt_manager/tags/gone")
        self.assertEqual(err_status, 500)

    async def test_merge_validation_errors(self):
        bad_json, _ = await self._json(
            "POST",
            "/prompt_manager/tags/merge",
            data=b"{nope",
            headers={"Content-Type": "application/json"},
        )
        no_source, _ = await self._json(
            "POST", "/prompt_manager/tags/merge", json={"target_tag": "t"}
        )
        no_target, _ = await self._json(
            "POST", "/prompt_manager/tags/merge", json={"source_tags": ["s"]}
        )
        self.assertEqual((bad_json, no_source, no_target), (400, 400, 400))

    async def test_merge_reports_skipped_rows_and_db_failure(self):
        self._stub(
            "merge_tags",
            {"affected_count": 2, "tags_merged": ["s"], "skipped_count": 1},
        )
        status, data = await self._json(
            "POST",
            "/prompt_manager/tags/merge",
            json={"source_tags": ["s"], "target_tag": "t"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["skipped_count"], 1)
        self._break("merge_tags")
        err_status, _ = await self._json(
            "POST",
            "/prompt_manager/tags/merge",
            json={"source_tags": ["s"], "target_tag": "t"},
        )
        self.assertEqual(err_status, 500)


class TestSaveRoute(RouteCoverageCase):

    async def test_invalid_rating_is_400(self):
        status, data = await self._json(
            "POST", "/prompt_manager/save", json={"text": "ok", "rating": 99}
        )
        self.assertEqual(status, 400)
        self.assertIn("Rating", data["error"])

    async def test_duplicate_updates_metadata(self):
        first = self._save_prompt("same text")
        status, data = await self._json(
            "POST",
            "/prompt_manager/save",
            json={"text": "same text", "category": "cat", "tags": ["t"], "rating": 4},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["is_duplicate"])
        self.assertEqual(data["prompt_id"], first)
        stored = self.api.db.get_prompt_by_id(first)
        self.assertEqual(stored["category"], "cat")
        self.assertEqual(stored["rating"], 4)

    async def test_duplicate_without_metadata_leaves_row_alone(self):
        first = self._save_prompt("plain dup")
        status, data = await self._json(
            "POST", "/prompt_manager/save", json={"text": "plain dup"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["prompt_id"], first)

    async def test_db_failure_is_500(self):
        self._break("get_prompt_by_hash")
        status, data = await self._json(
            "POST", "/prompt_manager/save", json={"text": "boom"}
        )
        self.assertEqual(status, 500)
        self.assertFalse(data["success"])


class TestDeleteUpdateRatingRoutes(RouteCoverageCase):

    async def test_delete_invalid_id_and_db_failure(self):
        bad, _ = await self._json("DELETE", "/prompt_manager/delete/abc")
        self._break("delete_prompt")
        err, _ = await self._json("DELETE", "/prompt_manager/delete/1")
        self.assertEqual((bad, err), (400, 500))

    async def test_update_text_validation_and_not_found(self):
        pid = self._save_prompt("orig")
        empty, _ = await self._json(
            "PUT", f"/prompt_manager/prompts/{pid}", json={"text": "  "}
        )
        too_long, _ = await self._json(
            "PUT", f"/prompt_manager/prompts/{pid}", json={"text": "x" * 10_001}
        )
        missing, _ = await self._json(
            "PUT", "/prompt_manager/prompts/999999", json={"text": "new"}
        )
        bad_id, _ = await self._json(
            "PUT", "/prompt_manager/prompts/abc", json={"text": "new"}
        )
        self.assertEqual((empty, too_long, missing, bad_id), (400, 400, 404, 400))

    async def test_update_text_db_failure(self):
        pid = self._save_prompt("orig")
        self._break("update_prompt_text")
        status, _ = await self._json(
            "PUT", f"/prompt_manager/prompts/{pid}", json={"text": "new"}
        )
        self.assertEqual(status, 500)

    async def test_rating_validation_not_found_and_failure(self):
        pid = self._save_prompt("rate me")
        invalid, _ = await self._json(
            "PUT", f"/prompt_manager/prompts/{pid}/rating", json={"rating": 0}
        )
        missing, _ = await self._json(
            "PUT", "/prompt_manager/prompts/999999/rating", json={"rating": 3}
        )
        bad_id, _ = await self._json(
            "PUT", "/prompt_manager/prompts/abc/rating", json={"rating": 3}
        )
        self._break("update_prompt_rating")
        err, _ = await self._json(
            "PUT", f"/prompt_manager/prompts/{pid}/rating", json={"rating": 3}
        )
        self.assertEqual((invalid, missing, bad_id, err), (400, 404, 400, 500))


class TestPromptTagRoutes(RouteCoverageCase):

    async def test_add_tag_validation_paths(self):
        pid = self._save_prompt("tagme", tags=["have"])
        empty, _ = await self._json(
            "POST", f"/prompt_manager/prompts/{pid}/tags", json={"tag": ""}
        )
        missing, _ = await self._json(
            "POST", "/prompt_manager/prompts/999999/tags", json={"tag": "x"}
        )
        bad_id, _ = await self._json(
            "POST", "/prompt_manager/prompts/abc/tags", json={"tag": "x"}
        )
        dup, dup_data = await self._json(
            "POST", f"/prompt_manager/prompts/{pid}/tags", json={"tag": "have"}
        )
        self.assertEqual((empty, missing, bad_id, dup), (400, 404, 400, 200))
        self.assertTrue(dup_data["success"])
        self.assertEqual(self.api.db.get_prompt_by_id(pid)["tags"], ["have"])

    async def test_add_tag_db_failure(self):
        pid = self._save_prompt("tagme")
        self._break("set_prompt_tags")
        status, _ = await self._json(
            "POST", f"/prompt_manager/prompts/{pid}/tags", json={"tag": "x"}
        )
        self.assertEqual(status, 500)

    async def test_add_tags_bulk_to_single_prompt(self):
        pid = self._save_prompt("multi", tags=["a"])
        status, data = await self._json(
            "POST",
            "/prompt_manager/prompts/tags",
            json={"prompt_id": pid, "tags": ["a", " b ", "", "c"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["tags_added"], 2)
        again, again_data = await self._json(
            "POST",
            "/prompt_manager/prompts/tags",
            json={"prompt_id": pid, "tags": ["a"]},
        )
        self.assertEqual(again, 200)
        self.assertEqual(again_data["tags_added"], 0)
        self.assertIn("No new tags", again_data["message"])

    async def test_add_tags_validation_and_failures(self):
        no_id, _ = await self._json(
            "POST", "/prompt_manager/prompts/tags", json={"tags": ["a"]}
        )
        no_tags, _ = await self._json(
            "POST", "/prompt_manager/prompts/tags", json={"prompt_id": 1, "tags": "a"}
        )
        missing, _ = await self._json(
            "POST",
            "/prompt_manager/prompts/tags",
            json={"prompt_id": 999999, "tags": ["a"]},
        )
        self._break("get_prompt_by_id")
        err, _ = await self._json(
            "POST", "/prompt_manager/prompts/tags", json={"prompt_id": 1, "tags": ["a"]}
        )
        self.assertEqual((no_id, no_tags, missing, err), (400, 400, 404, 500))

    async def test_remove_tag_paths(self):
        pid = self._save_prompt("untag", tags=["a", "b"])
        removed, _ = await self._json(
            "DELETE", f"/prompt_manager/prompts/{pid}/tags", json={"tag": "a"}
        )
        absent, _ = await self._json(
            "DELETE", f"/prompt_manager/prompts/{pid}/tags", json={"tag": "zzz"}
        )
        missing, _ = await self._json(
            "DELETE", "/prompt_manager/prompts/999999/tags", json={"tag": "a"}
        )
        bad_id, _ = await self._json(
            "DELETE", "/prompt_manager/prompts/abc/tags", json={"tag": "a"}
        )
        self.assertEqual((removed, absent, missing, bad_id), (200, 200, 404, 400))
        self.assertEqual(self.api.db.get_prompt_by_id(pid)["tags"], ["b"])

    async def test_remove_tag_db_failure(self):
        pid = self._save_prompt("untag", tags=["a"])
        self._break("set_prompt_tags")
        status, _ = await self._json(
            "DELETE", f"/prompt_manager/prompts/{pid}/tags", json={"tag": "a"}
        )
        self.assertEqual(status, 500)


class TestBulkRoutes(RouteCoverageCase):

    async def test_bulk_delete_paths(self):
        ids = [self._save_prompt(f"bulk {i}") for i in range(3)]
        empty, _ = await self._json(
            "POST", "/prompt_manager/bulk/delete", json={"prompt_ids": []}
        )
        ok, ok_data = await self._json(
            "POST", "/prompt_manager/bulk/delete", json={"prompt_ids": ids[:2]}
        )
        self._break("bulk_delete_prompts")
        err, _ = await self._json(
            "POST", "/prompt_manager/bulk/delete", json={"prompt_ids": ids}
        )
        self.assertEqual((empty, ok, err), (400, 200, 500))
        self.assertEqual(ok_data["deleted_count"], 2)

    async def test_bulk_add_tags_paths(self):
        ids = [self._save_prompt(f"bulk tag {i}") for i in range(2)]
        empty, _ = await self._json(
            "POST", "/prompt_manager/bulk/tags", json={"prompt_ids": ids, "tags": []}
        )
        ok, ok_data = await self._json(
            "POST", "/prompt_manager/bulk/tags", json={"prompt_ids": ids, "tags": ["z"]}
        )
        self._break("bulk_add_tags")
        err, _ = await self._json(
            "POST", "/prompt_manager/bulk/tags", json={"prompt_ids": ids, "tags": ["z"]}
        )
        self.assertEqual((empty, ok, err), (400, 200, 500))
        self.assertEqual(ok_data["updated_count"], 2)

    async def test_bulk_set_category_paths(self):
        ids = [self._save_prompt(f"bulk cat {i}") for i in range(2)]
        empty, _ = await self._json(
            "POST", "/prompt_manager/bulk/category", json={"category": "c"}
        )
        ok, ok_data = await self._json(
            "POST",
            "/prompt_manager/bulk/category",
            json={"prompt_ids": ids, "category": "c"},
        )
        self._break("bulk_set_category")
        err, _ = await self._json(
            "POST",
            "/prompt_manager/bulk/category",
            json={"prompt_ids": ids, "category": "c"},
        )
        self.assertEqual((empty, ok, err), (400, 200, 500))
        self.assertEqual(ok_data["updated_count"], 2)
        self.assertEqual(self.api.db.get_prompt_by_id(ids[0])["category"], "c")

    async def _set_category(self, body):
        return await self._json("POST", "/prompt_manager/bulk/category", json=body)

    async def test_bulk_set_category_rejects_malformed_prompt_ids(self):
        pid = self._save_prompt("bulk cat strict")
        cases = (
            None,
            "1",
            {"id": pid},
            [],
            [pid, "2"],
            [pid, 0],
            [pid, -3],
            [pid, 1.5],
            [True],
            [pid, None],
            # Beyond SQLite's INTEGER range: binding it would raise OverflowError.
            [pid, 2**63],
            [2**64],
            list(range(1, BULK_CATEGORY_MAX_PROMPTS + 2)),
        )
        for prompt_ids in cases:
            with self.subTest(prompt_ids=str(prompt_ids)[:40]):
                status, data = await self._set_category(
                    {"prompt_ids": prompt_ids, "category": "c"}
                )
                self.assertEqual(status, 400, data)
                self.assertFalse(data["success"])
        self.assertIsNone(self.api.db.get_prompt_by_id(pid)["category"])

    async def test_bulk_set_category_validates_the_category(self):
        pid = self._save_prompt("bulk cat validate")
        for category in ("x" * 101, "bad\x00name", 7, ["c"]):
            with self.subTest(category=str(category)[:20]):
                status, data = await self._set_category(
                    {"prompt_ids": [pid], "category": category}
                )
                self.assertEqual(status, 400, data)
                self.assertFalse(data["success"])
        for body in ([pid], "text"):
            with self.subTest(body=body):
                status, data = await self._set_category(body)
                self.assertEqual(status, 400, data)
        resp = await self.client.post(
            "/prompt_manager/bulk/category",
            data="{not json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status, 400)
        self.assertIsNone(self.api.db.get_prompt_by_id(pid)["category"])

    async def test_bulk_set_category_dedupes_and_accepts_the_cap(self):
        ids = [self._save_prompt(f"bulk cat dedupe {i}") for i in range(2)]
        seen = []
        original = self.api.db.bulk_set_category

        def spy(prompt_ids, category):
            seen.append((list(prompt_ids), category))
            return original(prompt_ids, category)

        self.api.db.bulk_set_category = spy
        status, data = await self._set_category(
            {"prompt_ids": [ids[1], ids[0], ids[1], ids[0]], "category": " Land "}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["updated_count"], 2)
        self.assertEqual(seen, [([ids[1], ids[0]], "Land")])
        # The cap itself is accepted; unknown ids simply do not count.
        capped = ids + list(range(10**6, 10**6 + BULK_CATEGORY_MAX_PROMPTS - 2))
        status, data = await self._set_category(
            {"prompt_ids": capped, "category": "Sea"}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["updated_count"], 2)
        self.assertEqual(self.api.db.get_prompt_by_id(ids[0])["category"], "Sea")

    async def test_bulk_set_category_keeps_the_legacy_admin_request(self):
        # web/js/admin.js posts parseInt ids and a trimmed, non-empty category.
        ids = [self._save_prompt(f"bulk cat legacy {i}") for i in range(3)]
        status, data = await self._set_category(
            {"prompt_ids": ids, "category": "Portraits"}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(
            set(data), {"success", "message", "updated_count"}, "response shape"
        )
        self.assertTrue(data["success"])
        self.assertEqual(data["updated_count"], 3)
        self.assertEqual(data["message"], "Set category for 3 prompts")
        for pid in ids:
            self.assertEqual(self.api.db.get_prompt_by_id(pid)["category"], "Portraits")


if __name__ == "__main__":
    unittest.main()


class TestPromptEditHash(PromptAPITestCase):
    async def test_edit_moves_the_hash_and_original_text_can_be_saved_again(self):
        original_id = self._save_prompt("Original landscape")
        response = await self.client.put(
            f"/prompt_manager/prompts/{original_id}", json={"text": "Edited portrait"}
        )
        self.assertEqual(response.status, 200)
        row = self.api.db.get_prompt_by_id(original_id)
        self.assertEqual(row["hash"], generate_prompt_hash("Edited portrait"))
        self.assertIsNone(
            self.api.db.get_prompt_by_hash(generate_prompt_hash("Original landscape"))
        )
        response = await self.client.post(
            "/prompt_manager/save", json={"text": "Original landscape"}
        )
        self.assertEqual(response.status, 200)
        self.assertNotEqual((await response.json())["prompt_id"], original_id)
        response = await self.client.post(
            "/prompt_manager/save", json={"text": "Edited portrait"}
        )
        self.assertEqual((await response.json())["prompt_id"], original_id)

    async def test_duplicate_edit_returns_conflict_without_changing_either_prompt(self):
        first = self._save_prompt("Original landscape", tags=["landscape"])
        second = self._save_prompt("Existing portrait", tags=["portrait"])
        before = [self.api.db.get_prompt_by_id(pid) for pid in (first, second)]
        response = await self.client.put(
            f"/prompt_manager/prompts/{first}", json={"text": "  EXISTING PORTRAIT  "}
        )
        self.assertEqual(response.status, 409)
        self.assertFalse((await response.json())["success"])
        self.assertEqual(
            [self.api.db.get_prompt_by_id(pid) for pid in (first, second)], before
        )

    async def test_edit_with_same_normalized_hash_keeps_its_identity(self):
        pid = self._save_prompt("Original landscape")
        response = await self.client.put(
            f"/prompt_manager/prompts/{pid}", json={"text": "ORIGINAL LANDSCAPE"}
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            self.api.db.get_prompt_by_id(pid)["text"], "ORIGINAL LANDSCAPE"
        )
