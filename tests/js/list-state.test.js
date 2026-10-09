// Run with: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const { nextPageState, shouldSaveEdit, hasMorePages, shouldLoadMore } = require("../../web/js/list-state.js");

test("hasMorePages follows pagination.has_more only while pages keep arriving", () => {
    assert.equal(hasMorePages({ pagination: { has_more: true } }, 5000), true);
    assert.equal(hasMorePages({ pagination: { has_more: false } }, 5000), false);
    assert.equal(hasMorePages({ pagination: { has_more: true } }, 0), false, "an empty page ends the loop");
    assert.equal(hasMorePages({}, 10), false, "no pagination block means a single page");
    assert.equal(hasMorePages(null, 10), false);
});

const current = { page: 2, limit: 50, total: 120, totalPages: 3 };

test("nextPageState moves to the requested page once the fetch succeeded", () => {
    assert.deepEqual(nextPageState(current, { page: 3, total: 120 }), { page: 3, limit: 50, total: 120, totalPages: 3 });
});

test("nextPageState clamps a page beyond the end when the total shrank", () => {
    assert.deepEqual(nextPageState(current, { page: 3, total: 60 }), { page: 2, limit: 50, total: 60, totalPages: 2 });
});

test("nextPageState never goes below page 1 and has at least one page", () => {
    assert.deepEqual(nextPageState(current, { page: 0, total: 0 }), { page: 1, limit: 50, total: 0, totalPages: 1 });
    assert.equal(nextPageState(current, { page: -5, total: 10 }).page, 1);
});

test("nextPageState keeps the current page and total when the response omits them", () => {
    assert.deepEqual(nextPageState(current, {}), current);
    assert.deepEqual(nextPageState(current, { total: "many", page: "x" }), current);
});

test("nextPageState accepts a new limit and recomputes totalPages", () => {
    assert.deepEqual(nextPageState(current, { page: 1, limit: 25, total: 120 }), { page: 1, limit: 25, total: 120, totalPages: 5 });
    assert.equal(nextPageState(current, { limit: 0 }).limit, 50, "a zero limit is ignored");
});

test("nextPageState does not mutate its input", () => {
    const before = { ...current };
    nextPageState(current, { page: 1, total: 5 });
    assert.deepEqual(current, before);
});

test("shouldSaveEdit is true only for a non-empty text that changed", () => {
    assert.equal(shouldSaveEdit("old", "new"), true);
    assert.equal(shouldSaveEdit("same", "same"), false);
    assert.equal(shouldSaveEdit("same", "  same \n"), false, "whitespace-only changes are not saved");
    assert.equal(shouldSaveEdit("old", "   "), false, "an emptied prompt is not saved");
    assert.equal(shouldSaveEdit("old", null), false);
});

test("shouldLoadMore asks for the next page only near the bottom with pages left", () => {
    const base = {
        infiniteScroll: true, loading: false, page: 1, limit: 100, total: 1000,
        scrollTop: 0, clientHeight: 800, scrollHeight: 4000, threshold: 600,
    };
    assert.equal(shouldLoadMore(base), false, "far from the bottom");
    assert.equal(shouldLoadMore({ ...base, scrollTop: 2700 }), true, "within the threshold");
    assert.equal(shouldLoadMore({ ...base, scrollTop: 2700, loading: true }), false, "a load is in flight");
    assert.equal(shouldLoadMore({ ...base, scrollTop: 2700, infiniteScroll: false }), false, "setting off");
    assert.equal(shouldLoadMore({ ...base, scrollTop: 2700, page: 10 }), false, "last page reached");
    assert.equal(shouldLoadMore({ ...base, scrollHeight: 700 }), true, "content shorter than the viewport");
    assert.equal(shouldLoadMore({ ...base, total: 0, scrollHeight: 700 }), false, "nothing to load");
});
