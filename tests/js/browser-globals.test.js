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
    "api-client.js": ["ApiClient"],
    "png-metadata.js": ["PngMetadata"],
    "comfy-metadata.js": ["ComfyMetadata"],
    "gallery-settings.js": ["GallerySettings"],
    "legacy-links.js": ["LegacyLinks"],
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
