// Run with: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const { formatImageCaption, workflowDocumentHtml } = require("../../web/js/image-helpers.js");

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
