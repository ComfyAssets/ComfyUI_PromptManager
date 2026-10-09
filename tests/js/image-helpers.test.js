// Run with: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const {
    formatImageCaption,
    workflowDocumentHtml,
    autotagSingleBody,
    fallbackImageSource,
} = require("../../web/js/image-helpers.js");

const WD14 = { modelType: "wd14-vit", generalThreshold: 0.35, characterThreshold: 0.85, prompt: "ignored" };
const CAPTION = { modelType: "blip", generalThreshold: 0.35, characterThreshold: 0.85, prompt: "describe it" };

test("autotagSingleBody sends image_id for a database row", () => {
    const body = autotagSingleBody({ id: 42, prompt_id: 7, image_path: "/out/a.png" }, WD14);
    assert.equal(body.image_id, 42);
    assert.equal("path" in body, false);
});

test("autotagSingleBody sends path for an output-folder entry whose id is a digest", () => {
    const body = autotagSingleBody({ id: "3f2a9c1b0d4e5f67", path: "/out/sub/a.png", filename: "a.png" }, WD14);
    assert.equal(body.path, "/out/sub/a.png");
    assert.equal("image_id" in body, false);
});

test("autotagSingleBody prefers path when a numeric-looking string id has no prompt link", () => {
    const body = autotagSingleBody({ id: "1234567890abcdef", path: "/out/b.png" }, WD14);
    assert.deepEqual(Object.keys(body).filter((k) => k === "path" || k === "image_id"), ["path"]);
});

test("autotagSingleBody adds wd14 thresholds and no prompt", () => {
    const body = autotagSingleBody({ id: 1 }, WD14);
    assert.equal(body.model_type, "wd14-vit");
    assert.equal(body.general_threshold, 0.35);
    assert.equal(body.character_threshold, 0.85);
    assert.equal("prompt" in body, false);
});

test("autotagSingleBody adds the prompt for caption models and no thresholds", () => {
    const body = autotagSingleBody({ id: 1 }, CAPTION);
    assert.equal(body.model_type, "blip");
    assert.equal(body.prompt, "describe it");
    assert.equal("general_threshold" in body, false);
});

test("autotagSingleBody does not mutate its inputs", () => {
    const image = Object.freeze({ id: 1 });
    const settings = Object.freeze({ ...WD14 });
    assert.doesNotThrow(() => autotagSingleBody(image, settings));
});

const XSS = '"><img src=x onerror=1>';
const formatFileSize = (bytes) => `${bytes} B`;

test("formatImageCaption joins dimensions and size with the separator", () => {
    const caption = formatImageCaption({ width: 640, height: 480, file_size: 2048 }, { formatFileSize });
    assert.equal(caption, "640×480 | 2048 B");
});

test("formatImageCaption honours a custom separator and omits a missing size", () => {
    assert.equal(formatImageCaption({ width: 1, height: 2 }, { formatFileSize, separator: " • " }), "1×2");
});

test("formatImageCaption falls back to Unknown size when a dimension is missing", () => {
    assert.equal(formatImageCaption({ width: 640 }, { formatFileSize }), "Unknown size");
    assert.equal(formatImageCaption({}, { formatFileSize }), "Unknown size");
    assert.equal(formatImageCaption(null, { formatFileSize }), "Unknown size");
});

test("formatImageCaption escapes string dimensions so they cannot break out of an attribute", () => {
    const caption = formatImageCaption({ width: XSS, height: 10 }, { formatFileSize });
    assert.ok(!caption.includes("<"), caption);
    assert.ok(!caption.includes('"'), caption);
    assert.ok(caption.includes("&quot;&gt;&lt;img"), caption);
});

test("formatImageCaption ignores a non-numeric file_size instead of formatting it", () => {
    const caption = formatImageCaption({ width: 1, height: 1, file_size: XSS }, { formatFileSize });
    assert.equal(caption, "1×1");
});

test("formatImageCaption escapes whatever formatFileSize returns", () => {
    const caption = formatImageCaption({ width: 1, height: 1, file_size: 5 }, { formatFileSize: () => "<b>" });
    assert.equal(caption, "1×1 | &lt;b&gt;");
});

test("workflowDocumentHtml escapes the JSON so it cannot close the pre block", () => {
    const html = workflowDocumentHtml({ note: "</pre><script>alert(1)</script>" });
    assert.ok(html.includes("&lt;/pre&gt;&lt;script&gt;"), html);
    assert.ok(!html.includes("</pre><script>"), html);
    assert.ok(html.startsWith("<!DOCTYPE html>"));
    assert.ok(html.includes("<title>ComfyUI Workflow Data</title>"));
});

test("workflowDocumentHtml pretty-prints the workflow with two-space indentation", () => {
    const html = workflowDocumentHtml({ a: 1 });
    assert.ok(html.includes('{\n  &quot;a&quot;: 1\n}'), html);
});

test("fallbackImageSource offers the original once when a thumbnail fails", () => {
    const dataset = { original: "/serve/a.png", thumbnail: "/serve/thumbnails/a_thumb.png?v=1" };
    assert.equal(fallbackImageSource("/serve/thumbnails/a_thumb.png?v=1", dataset), "/serve/a.png");
});

test("fallbackImageSource gives up when the original itself failed or was already tried", () => {
    assert.equal(fallbackImageSource("/serve/a.png", { original: "/serve/a.png" }), null);
    assert.equal(fallbackImageSource("/serve/t.png", { original: "/serve/a.png", fellBack: "1" }), null);
    assert.equal(fallbackImageSource("/serve/t.png", {}), null);
    assert.equal(fallbackImageSource("/serve/t.png", undefined), null);
});
