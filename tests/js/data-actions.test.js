// Run with: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const { readAction, dispatch } = require("../../web/js/data-actions.js");

test("readAction parses promptId and index as integers", () => {
    const parsed = readAction({ action: "open-film", promptId: "42", index: "3" });
    assert.equal(parsed.action, "open-film");
    assert.equal(parsed.promptId, 42);
    assert.equal(parsed.index, 3);
});

test("readAction turns missing or non-numeric ids into null", () => {
    assert.equal(readAction({ action: "edit" }).promptId, null);
    assert.equal(readAction({ action: "edit", promptId: "abc" }).promptId, null);
    assert.equal(readAction({ action: "edit", index: "" }).index, null);
});

test("readAction passes string fields through untouched", () => {
    const parsed = readAction({ action: "remove-tag", tag: `a "quoted" tag`, url: "/x?y=1&z=2", copyText: "p" });
    assert.equal(parsed.tag, `a "quoted" tag`);
    assert.equal(parsed.url, "/x?y=1&z=2");
    assert.equal(parsed.copyText, "p");
});

test("readAction tolerates a missing dataset", () => {
    assert.equal(readAction(undefined).action, "");
    assert.equal(readAction(null).promptId, null);
});

test("dispatch calls the matching handler with the parsed action and extras", () => {
    const calls = [];
    const handled = dispatch(
        { action: "edit", promptId: "7" },
        { edit: (a, el) => calls.push([a.promptId, el]) },
        "element",
    );
    assert.equal(handled, true);
    assert.deepEqual(calls, [[7, "element"]]);
});

test("dispatch returns false and calls nothing for an unknown or empty action", () => {
    let called = 0;
    const handlers = { edit: () => called++ };
    assert.equal(dispatch({ action: "other" }, handlers), false);
    assert.equal(dispatch({}, handlers), false);
    assert.equal(dispatch({ action: "constructor" }, handlers), false, "no prototype lookups");
    assert.equal(called, 0);
});
