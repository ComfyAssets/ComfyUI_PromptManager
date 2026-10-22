// The Node oracles that Python drives (tests/js/support/*.mjs) import product
// modules dynamically. A dynamic import takes a URL or a bare/relative specifier,
// never a filesystem path: on Windows `D:\...\x.mjs` parses as the `d:` scheme
// (ERR_UNSUPPORTED_ESM_URL_SCHEME). Every such import goes through moduleUrl, and
// this test proves the Windows form on any host so Linux catches a regression.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const support = path.join(here, "support");
const m = await import("./support/module-url.mjs").catch((error) => {
    if (error.code !== "ERR_MODULE_NOT_FOUND") throw error;
    return {};
});

test("moduleUrl turns a filesystem path into a file: URL on every platform", async () => {
    assert.equal(typeof m.moduleUrl, "function", "moduleUrl is exported");
    assert.equal(m.moduleUrl("D:\\x\\y z.mjs", { windows: true }), "file:///D:/x/y%20z.mjs");
    assert.equal(m.moduleUrl("C:\\repo\\web\\js\\workspace\\local-video.mjs", { windows: true }), "file:///C:/repo/web/js/workspace/local-video.mjs");
    assert.equal(m.moduleUrl("/repo/web/js/a b.mjs", { windows: false }), "file:///repo/web/js/a%20b.mjs");
    const own = m.moduleUrl(path.join(here, "support", "module-url.mjs"));
    assert.ok(own.startsWith("file:///"), own);
    assert.equal((await import(own)).moduleUrl, m.moduleUrl, "the URL imports the same module instance");
});

test("every dynamic import in the Node oracles is a file URL, never a path", () => {
    const scripts = fs.readdirSync(support).filter((name) => name.endsWith(".mjs"));
    assert.ok(scripts.includes("local-inspect-oracle.mjs"));
    for (const name of scripts) {
        const source = fs.readFileSync(path.join(support, name), "utf8").split("\n").filter((line) => !line.trim().startsWith("//")).join("\n");
        for (const match of source.matchAll(/\bimport\(\s*([^)]*?)[,)]/g)) {
            const argument = match[1].trim();
            assert.ok(/^(moduleUrl\(|pathToFileURL\(|["'`]\.{1,2}\/)/.test(argument), `${name}: import(${argument}) must be a file URL or a relative specifier`);
        }
    }
});
