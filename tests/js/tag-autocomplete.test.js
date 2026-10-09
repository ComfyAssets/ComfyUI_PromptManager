// Run with: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const {
    splitTagInput,
    applySuggestion,
    suggestUrl,
    moveSelection,
} = require("../../web/js/tag-autocomplete.js");

test("splitTagInput separates committed tags from the tag being typed", () => {
    assert.deepEqual(splitTagInput("asian, portrait, sm"), { committed: ["asian", "portrait"], current: "sm" });
    assert.deepEqual(splitTagInput("asian,"), { committed: ["asian"], current: "" });
    assert.deepEqual(splitTagInput("asian, "), { committed: ["asian"], current: "" });
    assert.deepEqual(splitTagInput("asi"), { committed: [], current: "asi" });
    assert.deepEqual(splitTagInput(""), { committed: [], current: "" });
    assert.deepEqual(splitTagInput(" asian ,, Asian , dog, "), { committed: ["asian", "dog"], current: "" });
});

test("applySuggestion replaces the partial tag and leaves the cursor ready for the next one", () => {
    assert.equal(applySuggestion("asian, sm", "smile"), "asian, smile, ");
    assert.equal(applySuggestion("", "asian"), "asian, ");
    assert.equal(applySuggestion("asian, ", "portrait"), "asian, portrait, ");
    assert.equal(applySuggestion("asian, asi", "asian"), "asian, ");
});

test("suggestUrl encodes the prefix and the committed tags", () => {
    assert.equal(suggestUrl("asian, sm"), "/prompt_manager/tags/suggest?q=sm&with=asian&limit=15");
    assert.equal(suggestUrl("a&b, c d, x", 5), "/prompt_manager/tags/suggest?q=x&with=a%26b%2Cc+d&limit=5");
    assert.equal(suggestUrl(""), "/prompt_manager/tags/suggest?q=&with=&limit=15");
});

test("moveSelection wraps around the list and starts from nothing selected", () => {
    assert.equal(moveSelection(-1, 1, 3), 0);
    assert.equal(moveSelection(-1, -1, 3), 2);
    assert.equal(moveSelection(2, 1, 3), 0);
    assert.equal(moveSelection(0, -1, 3), 2);
    assert.equal(moveSelection(1, 1, 0), -1);
});
