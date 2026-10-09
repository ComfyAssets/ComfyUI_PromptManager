// Run with: node --test tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const { escapeHtml, escapeAttr } = require("../../web/js/html-escape.js");

test("escapes the five HTML-significant characters", () => {
    assert.equal(escapeHtml(`&<>"'`), "&amp;&lt;&gt;&quot;&#39;");
});

test("a double quote cannot break out of an attribute", () => {
    const payload = `" onmouseover="alert(1)`;
    const escaped = escapeHtml(payload);
    assert.ok(!escaped.includes(`"`), escaped);
    assert.equal(escaped, `&quot; onmouseover=&quot;alert(1)`);
    assert.equal(`<a data-tag="${escaped}">`, `<a data-tag="&quot; onmouseover=&quot;alert(1)">`);
});

test("a single quote cannot break out of a single-quoted attribute", () => {
    const escaped = escapeHtml(`' onclick='alert(1)`);
    assert.ok(!escaped.includes(`'`), escaped);
    assert.equal(escaped, `&#39; onclick=&#39;alert(1)`);
});

test("a script tag is neutralised in text context", () => {
    assert.equal(escapeHtml("<script>x</script>"), "&lt;script&gt;x&lt;/script&gt;");
});

test("unicode and ordinary text pass through unchanged", () => {
    assert.equal(escapeHtml("héllo wörld 日本語 🙂"), "héllo wörld 日本語 🙂");
    assert.equal(escapeHtml("plain text, no specials"), "plain text, no specials");
});

test("already escaped text is escaped again, never double-decoded", () => {
    assert.equal(escapeHtml("&amp;"), "&amp;amp;");
});

test("null and undefined become the empty string", () => {
    assert.equal(escapeHtml(null), "");
    assert.equal(escapeHtml(undefined), "");
});

test("numbers and booleans are coerced to strings", () => {
    assert.equal(escapeHtml(42), "42");
    assert.equal(escapeHtml(0), "0");
    assert.equal(escapeHtml(1.5), "1.5");
    assert.equal(escapeHtml(false), "false");
});

test("objects are coerced through String()", () => {
    assert.equal(escapeHtml({ toString: () => "<b>" }), "&lt;b&gt;");
});

test("escapeAttr is the same function as escapeHtml", () => {
    assert.equal(escapeAttr, escapeHtml);
    assert.equal(escapeAttr(`a"b`), "a&quot;b");
});
