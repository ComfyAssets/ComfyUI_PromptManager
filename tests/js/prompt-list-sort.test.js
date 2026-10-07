// Run with: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const sort = require("../../web/js/prompt-list-sort.js");

test("default sort is recently used and is a listed option", () => {
    assert.equal(sort.DEFAULT_SORT, "last_used_desc");
    assert.ok(sort.SORT_OPTIONS.some((o) => o.value === sort.DEFAULT_SORT));
});

test("options include Recently used and Most used, each with a label", () => {
    const values = sort.SORT_OPTIONS.map((o) => o.value);
    assert.ok(values.includes("last_used_desc"));
    assert.ok(values.includes("run_count_desc"));
    for (const option of sort.SORT_OPTIONS) assert.ok(option.label.trim(), option.value);
    assert.equal(new Set(values).size, values.length, "values are unique");
});

test("options cannot be mutated by callers", () => {
    assert.throws(() => sort.SORT_OPTIONS.push({ value: "x", label: "x" }));
    // Non-strict code fails silently on frozen objects, so check the value survived
    const first = sort.SORT_OPTIONS[0].value;
    sort.SORT_OPTIONS[0].value = "hacked";
    assert.equal(sort.SORT_OPTIONS[0].value, first);
    assert.ok(sort.SORT_OPTIONS.every(Object.isFrozen));
});

test("normalizeSort keeps known values and falls back for anything else", () => {
    assert.equal(sort.normalizeSort("run_count_desc"), "run_count_desc");
    for (const bad of [undefined, null, "", "created_at; DROP TABLE prompts", 42, "LAST_USED_DESC"]) {
        assert.equal(sort.normalizeSort(bad), sort.DEFAULT_SORT, String(bad));
    }
});

test("buildRecentUrl computes offset from page and always sends a valid sort", () => {
    assert.equal(
        sort.buildRecentUrl({ page: 3, limit: 25, sort: "rating_desc" }),
        "/prompt_manager/recent?limit=25&offset=50&page=3&sort=rating_desc",
    );
    assert.equal(
        sort.buildRecentUrl({ sort: "bogus" }),
        "/prompt_manager/recent?limit=50&offset=0&page=1&sort=last_used_desc",
    );
});

test("withSort returns a copy with the sort set and leaves the input untouched", () => {
    const params = new URLSearchParams({ text: "castle", sort: "text_asc" });
    const result = sort.withSort(params, "run_count_desc");
    assert.equal(result.get("sort"), "run_count_desc");
    assert.equal(result.get("text"), "castle");
    assert.equal(params.get("sort"), "text_asc");
    assert.equal(sort.withSort(new URLSearchParams(), "nope").get("sort"), sort.DEFAULT_SORT);
});

test("formatRunCount labels zero, one and many runs", () => {
    assert.equal(sort.formatRunCount(0), "Never run");
    assert.equal(sort.formatRunCount(1), "1 run");
    assert.equal(sort.formatRunCount(642), "642 runs");
    assert.equal(sort.formatRunCount(1234), "1,234 runs");
});

test("formatRunCount hides the badge for missing or invalid counts", () => {
    for (const bad of [undefined, null, -1, 1.5, "3", NaN]) {
        assert.equal(sort.formatRunCount(bad), "", String(bad));
    }
});
