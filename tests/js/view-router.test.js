// Run with: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const { resolveView, VIEWS } = require("../../web/js/view-router.js");

test("the tags deep link and its sub-routes resolve to the tags view", () => {
    assert.equal(resolveView("#/tags"), "tags");
    assert.equal(resolveView("#/tags/"), "tags");
    assert.equal(resolveView("#/tags/landscape,portrait?mode=or"), "tags");
});

test("an empty, bare or unknown hash resolves to the prompts dashboard", () => {
    assert.equal(resolveView(""), "prompts");
    assert.equal(resolveView("#"), "prompts");
    assert.equal(resolveView("#/"), "prompts");
    assert.equal(resolveView("#/other"), "prompts");
    assert.equal(resolveView("#/tagsX"), "prompts", "prefix must end at a path boundary");
    assert.equal(resolveView("#tags"), "prompts");
});

test("non-string input resolves to the prompts dashboard", () => {
    assert.equal(resolveView(undefined), "prompts");
    assert.equal(resolveView(null), "prompts");
    assert.equal(resolveView(42), "prompts");
});

test("every resolved value is a listed view", () => {
    assert.ok(Object.isFrozen(VIEWS));
    for (const hash of ["#/tags", "", "#/x"]) assert.ok(VIEWS.includes(resolveView(hash)));
});
