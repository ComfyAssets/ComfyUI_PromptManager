// Run with: node --test tests/js/
// Each page module is a UMD-style IIFE: CommonJS export under Node, a window
// global in the browser. This loads every module the way a browser would and
// checks the global name the pages rely on.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const MODULES = {
    "html-escape.js": ["HtmlEscape", "escapeHtml", "escapeAttr"],
    "data-actions.js": ["DataActions"],
    "api-client.js": ["ApiClient"],
    "list-state.js": ["ListState"],
    "sse-stream.js": ["SseStream"],
    "png-metadata.js": ["PngMetadata"],
    "view-router.js": ["ViewRouter"],
    "prompt-list-sort.js": ["PromptListSort"],
    "comfy-metadata.js": ["ComfyMetadata"],
};

function loadInFakeBrowser(file) {
    const source = fs.readFileSync(path.join(__dirname, "../../web/js", file), "utf8");
    const window = {};
    window.window = window;
    const context = vm.createContext({
        window,
        globalThis: window,
        console,
        TextDecoder,
        TextEncoder,
        AbortController,
        URLSearchParams,
        Symbol,
        Object,
        Array,
        Number,
        String,
        Math,
        Set,
        Map,
        JSON,
        Error,
        Promise,
        DataView,
        Uint8Array,
        ArrayBuffer,
    });
    vm.runInContext(source, context, { filename: file });
    return window;
}

for (const [file, globals] of Object.entries(MODULES)) {
    test(`${file} exposes ${globals.join(", ")} on window when module is undefined`, () => {
        const window = loadInFakeBrowser(file);
        for (const name of globals) {
            assert.ok(window[name], `${name} missing`);
            assert.ok(["object", "function"].includes(typeof window[name]), `${name} is a ${typeof window[name]}`);
        }
    });
}

test("the browser escapeHtml global is the same function as HtmlEscape.escapeHtml", () => {
    const window = loadInFakeBrowser("html-escape.js");
    assert.equal(window.escapeHtml, window.HtmlEscape.escapeHtml);
    assert.equal(window.escapeAttr, window.escapeHtml);
    assert.equal(window.escapeHtml(`<a href="x">`), "&lt;a href=&quot;x&quot;&gt;");
});

test("the browser ViewRouter global resolves the tags deep link", () => {
    const window = loadInFakeBrowser("view-router.js");
    assert.equal(window.ViewRouter.resolveView("#/tags/a"), "tags");
});
