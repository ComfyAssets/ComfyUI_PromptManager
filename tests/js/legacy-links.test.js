// Run with: node --test tests/js/
// #668 (D207): where an old `/prompt_manager/admin#…` bookmark lands in the workspace (web/js/legacy-links.js, loaded
// by web/admin-redirect.html). Every target must be one the workspace router understands.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { adminTarget } = require("../../web/js/legacy-links.js");
const state = import("../../web/js/workspace/state.mjs");

test("the dashboard and unknown hashes open the Prompt library", () => {
    for (const hash of ["", "#", "#/", "#/prompts", "#tags", "#/tagsx", "#/tagged/a", undefined, null, 7]) {
        assert.equal(adminTarget(hash), "workspace#/library", String(hash));
    }
});

test("the bare Tags route and an empty tag list open Tags & categories", () => {
    for (const hash of ["#/tags", "#/tags/", "#/tags/,", "#/tags/%20", "#/tags/ , ,", "#/tags/?mode=or"]) {
        assert.equal(adminTarget(hash), "workspace#/tags", hash);
    }
});

test("selected tags open the Library filtered by them, matching all unless the old mode was or", () => {
    assert.equal(adminTarget("#/tags/landscape"), "workspace#/library?tag=landscape");
    assert.equal(adminTarget("#/tags/landscape,portrait"), "workspace#/library?tag=landscape&tag=portrait");
    assert.equal(adminTarget("#/tags/landscape,portrait?mode=or"), "workspace#/library?tag=landscape&tag=portrait&tag_mode=any");
    for (const mode of ["and", "OR", "", "any"]) {
        assert.equal(adminTarget(`#/tags/a,b?mode=${mode}`), "workspace#/library?tag=a&tag=b", mode);
    }
    // The old page wrote ?mode=or only for two or more tags and ignored it for one.
    assert.equal(adminTarget("#/tags/a?mode=or"), "workspace#/library?tag=a");
    assert.equal(adminTarget("#/tags/a,a?mode=or"), "workspace#/library?tag=a");
});

test("tag names are decoded as the old page encoded them and encoded again for the workspace", () => {
    // encodeURIComponent per tag, joined with commas (tags-page.js updateUrlHash).
    assert.equal(adminTarget("#/tags/red%20fox,a%2Cb,%E2%98%85%26%3D%3F%23"),
        `workspace#/library?${new URLSearchParams([["tag", "red fox"], ["tag", "a,b"], ["tag", "★&=?#"]])}`);
    // Trimmed before and after decoding; repeats dropped.
    assert.equal(adminTarget("#/tags/ a ,%20a,b"), "workspace#/library?tag=a&tag=b");
    // A segment that cannot be decoded is used as written (the old page threw on it).
    assert.equal(adminTarget("#/tags/100%,b"), "workspace#/library?tag=100%25&tag=b");
    // Only the text between the first and the second `?` was the old query.
    assert.equal(adminTarget("#/tags/a,b?x=1?mode=or"), "workspace#/library?tag=a&tag=b");
    assert.equal(adminTarget("#/tags/a,b?x=1&mode=or"), "workspace#/library?tag=a&tag=b&tag_mode=any");
});

test("the old untagged entry opens the Library's untagged prompts, alone or mixed", () => {
    assert.equal(adminTarget("#/tags/__untagged__"), "workspace#/library?tag_mode=untagged");
    // The old page asked for untagged prompts whenever the list held the entry, ignoring the other tags.
    assert.equal(adminTarget("#/tags/a,__untagged__?mode=or"), "workspace#/library?tag_mode=untagged");
});

test("every target is relative and resolves in the workspace router to the filter it names", async () => {
    const { resolveRoute } = await state;
    const cases = [
        ["", "library", undefined],
        ["#/tags", "tags", undefined],
        ["#/tags/red%20fox,a%2Cb?mode=or", "library", { tags: ["red fox", "a,b"], tagMode: "any" }],
        ["#/tags/a", "library", { tags: ["a"], tagMode: "all" }],
        ["#/tags/__untagged__", "library", { tags: [], tagMode: "untagged" }],
        ["#/tags/100%", "library", { tags: ["100%"], tagMode: "all" }],
    ];
    for (const [hash, id, browse] of cases) {
        const target = adminTarget(hash);
        // Relative, so a reverse-proxy mount prefix in front of /prompt_manager/admin survives.
        assert.match(target, /^workspace#\//, hash);
        const route = resolveRoute(target.slice("workspace".length));
        assert.deepEqual([route.id, route.browse, route.browseInvalid], [id, browse, undefined], hash);
    }
});

test("the module is a window global in the browser", () => {
    const source = fs.readFileSync(path.join(__dirname, "../../web/js/legacy-links.js"), "utf8");
    const window = {};
    vm.runInContext(source, vm.createContext({ window, URLSearchParams }));
    assert.equal(window.LegacyLinks.adminTarget("#/tags"), "workspace#/tags");
});

test("the redirect page loads the module, replaces the address and always offers the Library link", () => {
    const html = fs.readFileSync(path.join(__dirname, "../../web/admin-redirect.html"), "utf8");
    assert.match(html, /<script src="js\/legacy-links\.js"><\/script>/);
    assert.match(html, /location\.replace\(/);
    // Visible without scripts and when the script fails: not inside <noscript>.
    assert.match(html.replace(/<noscript>[\s\S]*?<\/noscript>/g, ""), /<a href="workspace#\/library">/);
    // The inline script on a page where the module failed to load still leaves for the Library.
    const inline = html.match(/<script>([\s\S]*?)<\/script>/)[1];
    for (const [globals, expected] of [
        [{}, "workspace#/library"],
        [{ LegacyLinks: { adminTarget() { throw new Error("broken"); } } }, "workspace#/library"],
        [{ LegacyLinks: { adminTarget: hash => `workspace#/seen${hash}` } }, "workspace#/seen#/tags/a"],
    ]) {
        const replaced = [];
        const window = { ...globals, location: { hash: "#/tags/a", replace: target => replaced.push(target) } };
        vm.runInContext(inline, vm.createContext({ window }));
        assert.deepEqual(replaced, [expected]);
    }
});
