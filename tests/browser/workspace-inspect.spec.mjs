// The native "Inspect a file" page (#249 local Inspect, PR L2 Task 6): one chosen or dropped local file previews
// inline, and the heading's one action opens the shared viewer with the full inspector on a record read entirely in
// this browser. The concept is docs/concepts/workspace/app.js renderMetadata (740–748) and its selection (1715–1729);
// the production differences are D60–D66 in TESTING.md.
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { parityPng, localFixture, tinyWebm, sendablePng, corruptPng, disguised, undecodableMp4, POSITIVE_LINES } from './local-inspect-files.mjs';

const workspace = '/prompt_manager/workspace';
const TOAST = 'Local preview only. No file uploaded; metadata is available above.';
const ACTION = 'Open file metadata';
const COPY = {
    intro: 'Read embedded metadata in this browser. The file is not uploaded or added to your library.',
    note: 'Your file stays in this browser. Its metadata opens with “Open file metadata”.',
    chooser: 'Choose an image or video for a local preview',
    multiple: 'Drop one file at a time.',
    directory: 'Folders cannot be inspected. Choose one image or video file.',
    unsupported: 'This file type cannot be previewed or inspected here. Choose an image or video file.',
    previewFailed: 'The preview could not be displayed. Metadata and the original remain available in the viewer.',
};

test.use({ locale: 'en-US', timezoneId: 'UTC', reducedMotion: 'reduce' });

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await expect(await request.post('/__test__/reset')).toBeOK();
    page.inspectErrors = [];
    page.on('pageerror', error => page.inspectErrors.push(error.message));
});

test.afterEach(async ({ page, browserName }) => {
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await saveCoverage(page, browserName);
    expect(page.inspectErrors).toEqual([]);
});

const main = page => page.locator('#workspace-main');
const action = page => main(page).locator('.heading-actions').getByRole('button', { name: ACTION, exact: true });
const chooser = page => page.locator('#local-file');
const preview = page => page.locator('#local-preview');
const status = page => preview(page).locator('p.small');
const viewer = page => page.locator('dialog#viewer-dialog');
const rail = page => viewer(page).locator('#viewer-details');

async function openInspect(page) {
    await page.goto(`${workspace}#/inspect`);
    await expect(main(page).getByRole('heading', { level: 1, name: 'Inspect a file', exact: true })).toBeVisible();
    await expect(chooser(page)).toBeAttached();
}

/** Selects through the real chooser, then waits for the page to classify the file. */
async function choose(page, file) {
    await chooser(page).setInputFiles(file);
}

async function openViewer(page) {
    await expect(action(page)).toHaveAttribute('aria-disabled', 'false');
    await action(page).click();
    await expect(viewer(page)).toBeVisible();
    await expect(rail(page).locator('.panel-heading')).toBeVisible();
}

/** Dispatches dragover + drop on `selector` with Files built in the page (and optional string items). */
function drop(page, selector, { files = [], strings = [] } = {}) {
    return page.evaluate(async ({ selector, files, strings }) => {
        const transfer = new DataTransfer();
        for (const file of files) transfer.items.add(new File([new Uint8Array(file.bytes)], file.name, { type: file.mimeType }));
        for (const [type, value] of strings) transfer.items.add(value, type);
        const target = document.querySelector(selector);
        const over = new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: transfer });
        const dropped = new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer });
        target.dispatchEvent(over);
        target.dispatchEvent(dropped);
        return { over: over.defaultPrevented, drop: dropped.defaultPrevented };
    }, { selector, files: files.map(file => ({ name: file.name, mimeType: file.mimeType, bytes: [...file.buffer] })), strings });
}

/** The rail's comparable content: prompts, the six facts, the raw records and the two downloads (D66 aside). */
const railContent = locator => locator.evaluate(node => {
    const raw = [...node.querySelectorAll('details')].find(item => item.querySelector('summary')?.textContent === 'Workflow & raw metadata');
    const facts = node.querySelector(':scope .meta-grid:not(details .meta-grid)');
    return {
        positive: node.querySelector('.prompt-section.positive p.prompt-text')?.textContent ?? null,
        negative: node.querySelector('.prompt-section.negative p.prompt-text')?.textContent ?? null,
        facts: facts ? [...facts.querySelectorAll(':scope > div')].map(item => [item.querySelector('dt').textContent, item.querySelector('dd').textContent]) : null,
        raw: raw ? [...raw.querySelectorAll('p.small, pre')].map(item => item.textContent) : null,
        downloads: raw ? [...raw.querySelectorAll('button')].map(item => [item.textContent, item.getAttribute('aria-disabled')]) : null,
    };
});

/** Counts storage writes and object URLs created and revoked for Files (download blobs are not the session's). */
async function instrument(page) {
    await page.addInitScript(() => {
        window.__inspect = { writes: [], created: [], revoked: [], files: new Set() };
        for (const name of ['setItem', 'removeItem', 'clear']) {
            const original = Storage.prototype[name];
            Storage.prototype[name] = function (...args) { window.__inspect.writes.push([name, args[0]]); return original.apply(this, args); };
        }
        const open = indexedDB.open.bind(indexedDB);
        indexedDB.open = (...args) => { window.__inspect.writes.push(['indexedDB.open', args[0]]); return open(...args); };
        const create = URL.createObjectURL, revoke = URL.revokeObjectURL;
        URL.createObjectURL = object => {
            const url = create(object);
            if (object instanceof File) { window.__inspect.created.push(url); window.__inspect.files.add(url); }
            return url;
        };
        URL.revokeObjectURL = url => { if (window.__inspect.files.has(url)) window.__inspect.revoked.push(url); return revoke(url); };
    });
}
const urls = page => page.evaluate(() => ({ created: [...window.__inspect.created], revoked: [...window.__inspect.revoked], writes: [...window.__inspect.writes] }));

test('chooser opens the shared full inspector without a second read', async ({ page, request }) => {
    // The Images viewer's rail for the same bytes, served from the output folder.
    await expect(await request.post('/__test__/add-local-inspect-file', { data: { name: 'f02.png' } })).toBeOK();
    await page.goto(`${workspace}#/images`);
    await page.locator('article[data-image-key="0:local-inspect/f02.png"] .image-open').click();
    await expect(rail(page).locator('.prompt-section.positive p.prompt-text')).toBeVisible();
    const served = await railContent(rail(page));
    await page.keyboard.press('Escape');
    await expect(viewer(page)).toHaveCount(0);

    await page.locator('aside.sidebar nav').getByRole('link', { name: 'Inspect a file', exact: true }).click();
    await expect(main(page).getByRole('heading', { level: 1, name: 'Inspect a file', exact: true })).toBeVisible();
    await expect(main(page).locator('.heading p.muted')).toHaveText('A standalone file uses the same image viewer and prompt actions.');
    // The concept DOM: one surface, the drop area with its icon, label, input and note, then the preview slot.
    const surface = main(page).locator('section.surface');
    await expect(surface).toHaveCount(1);
    await expect(surface.locator('h2')).toHaveText('Inspect without importing');
    await expect(surface.locator(':scope > p')).toHaveText(COPY.intro);
    const area = surface.locator('div.file-drop.spaced-20');
    await expect(area.locator(':scope > svg')).toHaveCount(1);
    await expect(area.locator('label[for="local-file"]')).toHaveText(COPY.chooser);
    await expect(chooser(page)).toHaveAttribute('type', 'file');
    await expect(chooser(page)).toHaveAttribute('accept', 'image/png,image/jpeg,image/webp,video/*,.mp4,.mov,.m4v,.webm,.mkv,.avi,.wmv');
    await expect(chooser(page)).not.toHaveAttribute('multiple', /.*/);
    await expect(area.locator('span.small#local-file-note')).toHaveText(COPY.note);
    await expect(surface.locator(':scope > #local-preview')).toBeEmpty();
    // D60: the one heading action is described by the note until a file is chosen.
    await expect(main(page).locator('.heading-actions button')).toHaveCount(1);
    await expect(action(page)).toHaveClass(/\bca-primary\b/);
    await expect(action(page)).toHaveAttribute('aria-disabled', 'true');
    await expect(action(page)).toHaveAccessibleDescription(COPY.note);
    await action(page).dispatchEvent('click');
    await expect(viewer(page)).toHaveCount(0);

    await choose(page, parityPng('f02.png'));
    const image = preview(page).locator('img.local-file-preview');
    await expect(image).toHaveAttribute('alt', 'Local file preview');
    await expect(image).toHaveJSProperty('complete', true);
    expect(await image.evaluate(node => node.naturalWidth)).toBe(64);
    expect(await image.getAttribute('src')).toMatch(/^blob:/);
    const box = await image.evaluate(node => ({ height: node.getBoundingClientRect().height, fit: getComputedStyle(node).objectFit, margin: getComputedStyle(node).marginTop }));
    expect(box.height).toBeLessThanOrEqual(400);
    expect(box).toMatchObject({ fit: 'contain', margin: '20px' });
    await expect(page.locator('#workspace-toast')).toHaveText(TOAST);
    await expect(action(page)).toHaveAttribute('aria-disabled', 'false');
    await expect(action(page)).not.toHaveAttribute('aria-describedby', /.*/);

    await openViewer(page);
    await expect(viewer(page).locator('#viewer-title')).toHaveText('f02.png');
    const position = viewer(page).locator('.media-position');
    await expect(position).toHaveText('1 of 1 loaded: f02.png');
    for (const name of ['Previous media', 'Next media', 'Play slideshow']) {
        await expect(viewer(page).getByRole('button', { name, exact: true })).toHaveAttribute('aria-disabled', 'true');
    }
    await expect(viewer(page).locator('.viewer-canvas img')).toBeVisible();
    for (const name of ['Fit image', 'Rotate image 90 degrees', 'Flip image horizontally']) {
        await expect(viewer(page).getByRole('button', { name, exact: true })).toHaveAttribute('aria-disabled', 'false');
    }
    const canvas = viewer(page).locator('.viewer-canvas img');
    await viewer(page).getByRole('button', { name: 'Rotate image 90 degrees', exact: true }).click();
    await expect(canvas).toHaveAttribute('style', /rotate\(90deg\)/);
    await viewer(page).getByRole('button', { name: 'Flip image horizontally', exact: true }).click();
    await expect(canvas).toHaveAttribute('style', /scaleX\(-1\)/);
    await viewer(page).getByRole('button', { name: 'Fit image', exact: true }).click();
    await expect(canvas).not.toHaveAttribute('style', /rotate\(90deg\)/);

    // The same rail as Images for the same bytes, except the D66 provenance.
    const local = await railContent(rail(page));
    expect(local).toEqual(served);
    expect(local.positive).toBe('a red fox in snow, detailed fur');
    expect(local.negative).toBe('blurry, lowres');
    expect(local.facts.map(([label]) => label)).toEqual(['Seed', 'Steps', 'CFG', 'Sampler', 'Scheduler', 'Model']);
    expect(local.downloads).toEqual([['Download metadata JSON', null], ['Download workflow JSON', null]]);
    const provenance = rail(page).locator('details').filter({ has: page.locator('summary', { hasText: 'File & provenance' }) });
    await expect(provenance).toContainText('Selected local file · read in this browser');
    await expect(provenance).toContainText('Local file; no saved library association was checked.');
    await expect(provenance.locator('dt', { hasText: /^Path$/ })).toHaveCount(0);
    await expect(rail(page).getByRole('button', { name: 'Show in folder', exact: true })).toHaveCount(0);
    await expect(rail(page).getByRole('button', { name: /Prompt history/ })).toHaveCount(0);
    await expect(rail(page).locator('.chip-row button.chip')).toHaveCount(0);
    const original = viewer(page).getByRole('link', { name: 'Download original', exact: true });
    await expect(original).toHaveAttribute('href', /^blob:/);
    await expect(original).toHaveAttribute('download', 'f02.png');

    await page.keyboard.press('Escape');
    await expect(viewer(page)).toHaveCount(0);
    await expect(action(page)).toBeFocused();
    // Opening the viewer again shows the same record without reading the File a second time.
    await page.evaluate(() => {
        window.__slices = 0;
        const slice = Blob.prototype.slice;
        Blob.prototype.slice = function (...args) { if (this.name === 'f02.png') window.__slices++; return slice.apply(this, args); };
    });
    await openViewer(page);
    expect(await railContent(rail(page))).toEqual(served);
    expect(await page.evaluate(() => window.__slices)).toBe(0);
});

test('Import → Inspect a local image lands here', async ({ page }) => {
    await page.goto(`${workspace}#/images`);
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Import & inspect', exact: true });
    await dialog.getByRole('button', { name: 'Open inspector', exact: true }).click();
    await expect(page).toHaveURL(/#\/inspect$/);
    await expect(main(page).getByRole('heading', { level: 1, name: 'Inspect a file', exact: true })).toBeVisible();
    await expect(chooser(page)).toBeAttached();
    await expect(main(page).locator('.handoff')).toHaveCount(0);
    await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('drop accepts exactly one File and never navigates', async ({ page }) => {
    await openInspect(page);
    // A drop that misses the area is cancelled, so the browser never opens the file in place of the page.
    expect(await drop(page, 'body', { files: [parityPng('f02.png')] })).toEqual({ over: true, drop: true });
    await expect(preview(page)).toBeEmpty();
    expect(await drop(page, '.file-drop', { files: [parityPng('f02.png')] })).toEqual({ over: true, drop: true });
    await expect(preview(page).locator('img.local-file-preview')).toBeVisible();
    await expect(page.locator('#workspace-toast')).toHaveText(TOAST);
    // The chooser names the dropped file, never the one it replaced and never "No file chosen".
    expect(await chooser(page).evaluate(node => [...node.files].map(file => file.name))).toEqual(['f02.png']);
    const first = await preview(page).locator('img').getAttribute('src');
    // Two files, a link and markup keep the selection and say why.
    await drop(page, '.file-drop', { files: [parityPng('f02.png'), parityPng('f03.png')] });
    await expect(status(page)).toHaveText(COPY.multiple);
    await expect(status(page)).toHaveAttribute('role', 'alert');
    await expect(preview(page).locator('img')).toHaveAttribute('src', first);
    for (const strings of [[['text/uri-list', 'https://example.invalid/a.png']], [['text/html', '<img src="https://example.invalid/a.png">']]]) {
        await drop(page, '.file-drop', { strings });
        await expect(status(page)).toHaveText(COPY.unsupported);
        await expect(preview(page).locator('img')).toHaveAttribute('src', first);
    }
    await expect(action(page)).toHaveAttribute('aria-disabled', 'false');
    await openViewer(page);
    await expect(viewer(page).locator('#viewer-title')).toHaveText('f02.png');
    await expect(page).toHaveURL(/#\/inspect$/);
});

test('cancel and same-file reselect', async ({ page }) => {
    await instrument(page);
    await openInspect(page);
    await choose(page, parityPng('f02.png'));
    await expect(preview(page).locator('img.local-file-preview')).toBeVisible();
    const first = await preview(page).locator('img').getAttribute('src');
    // The chooser still names the chosen file, as in the concept; a cancel (no files) keeps the page as it is.
    expect(await chooser(page).evaluate(node => node.files[0]?.name)).toBe('f02.png');
    await chooser(page).setInputFiles([]);
    await expect(preview(page).locator('img')).toHaveAttribute('src', first);
    await expect(status(page)).toHaveCount(0);
    await expect(action(page)).toHaveAttribute('aria-disabled', 'false');
    // Opening the picker and cancelling it keeps naming the live selection (modern engines fire `cancel`, not `change`).
    await chooser(page).dispatchEvent('click');
    await chooser(page).dispatchEvent('cancel');
    expect(await chooser(page).evaluate(node => [...node.files].map(file => file.name))).toEqual(['f02.png']);
    await expect(preview(page).locator('img')).toHaveAttribute('src', first);
    await expect(action(page)).toHaveAttribute('aria-disabled', 'false');
    // The same file again is a new selection with its own URL; the old one is released. Opening the picker clears
    // the chooser, so the browser reports the same file as a change.
    // (Read in the same task: a picker the click opens is cancelled by the harness, which names the file again.)
    expect(await chooser(page).evaluate(node => { node.dispatchEvent(new MouseEvent('click')); return node.value; })).toBe('');
    await choose(page, parityPng('f02.png'));
    await expect(preview(page).locator('img')).not.toHaveAttribute('src', first);
    await expect(preview(page).locator('img.local-file-preview')).toBeVisible();
    const state = await urls(page);
    expect(state.created).toHaveLength(2);
    expect(state.revoked).toEqual([first]);
    await openViewer(page);
    await expect(viewer(page).locator('.media-position')).toHaveText('1 of 1 loaded: f02.png');
});

test('preview does not imply metadata support', async ({ page }) => {
    await instrument(page);
    await openInspect(page);
    // JPEG and WebP preview and report no supported metadata, exactly as Images reports them.
    for (const [file, format] of [[localFixture('tiny.jpg', 'image/jpeg'), 'JPEG'], [localFixture('tiny.webp', 'image/webp'), 'WEBP']]) {
        await choose(page, file);
        await expect(preview(page).locator('img.local-file-preview')).toBeVisible();
        await openViewer(page);
        await expect(rail(page)).toContainText('No supported embedded metadata was recorded in this file.');
        await expect(rail(page).locator('details').first()).toContainText(format);
        await page.keyboard.press('Escape');
        await expect(viewer(page)).toHaveCount(0);
    }
    // A PNG whose text chunk fails its CRC: unreadable metadata, the original still downloads.
    await choose(page, corruptPng());
    await expect(action(page)).toHaveAttribute('aria-disabled', 'false');
    await openViewer(page);
    await expect(rail(page)).toContainText('Embedded metadata could not be read.');
    const [download] = await Promise.all([page.waitForEvent('download'), viewer(page).getByRole('link', { name: 'Download original', exact: true }).click()]);
    expect(download.suggestedFilename()).toBe('corrupt.png');
    await page.keyboard.press('Escape');
    await expect(viewer(page)).toHaveCount(0);
    // Text, markup, SVG and a playlist under media names are unsupported and never get a URL.
    const before = (await urls(page)).created.length;
    for (const file of disguised()) {
        await choose(page, file);
        await expect(status(page)).toHaveText(COPY.unsupported);
        await expect(status(page)).toHaveAttribute('role', 'alert');
        await expect(preview(page).locator('img, video')).toHaveCount(0);
        await expect(action(page)).toHaveAttribute('aria-disabled', 'true');
    }
    expect((await urls(page)).created).toHaveLength(before);
});

test('local video plays inline and in the viewer with real tags', async ({ page, browserName }) => {
    await openInspect(page);
    await choose(page, localFixture('core-tags.webm', 'video/webm'));
    const video = preview(page).locator('video.local-file-preview');
    await expect(video).toBeVisible();
    expect(await video.evaluate(node => ({ controls: node.controls, playsInline: node.playsInline, preload: node.preload, muted: node.muted, autoplay: node.autoplay, paused: node.paused })))
        .toEqual({ controls: true, playsInline: true, preload: 'metadata', muted: true, autoplay: false, paused: true });
    // WebKit may not decode VP8: whichever state it reports, metadata is still read.
    await expect.poll(() => video.evaluate(node => node.readyState >= 1 || !!node.error)).toBe(true);
    await openViewer(page);
    await expect(rail(page).locator('.prompt-section.positive p.prompt-text')).toHaveText('a red fox in snow, detailed fur');
    await expect(rail(page).locator('.meta-grid').first().locator('dd').first()).toHaveText('7');
    await expect(viewer(page).locator('#viewer-stage video')).toHaveCount(1);
    await page.keyboard.press('Escape');
    await choose(page, tinyWebm());
    await expect(preview(page).locator('video.local-file-preview')).toBeVisible();
    await openViewer(page);
    await expect(rail(page)).toContainText('No supported embedded metadata was recorded in this file.');
    if (browserName !== 'webkit') await expect.poll(() => viewer(page).locator('#viewer-stage video').evaluate(node => node.readyState >= 1)).toBe(true);
});

test('the toast and a rejected drop never contradict the status line', async ({ page }) => {
    // A file whose bytes are gone after choosing: the status says its metadata cannot be read, so no toast says it is
    // available.
    await page.addInitScript(() => {
        const slice = Blob.prototype.slice;
        Blob.prototype.slice = function (...args) {
            if (this.name === 'gone.png') return { arrayBuffer: () => Promise.reject(new DOMException('The file is gone', 'NotReadableError')) };
            return slice.apply(this, args);
        };
    });
    await openInspect(page);
    await choose(page, parityPng('f02.png', 'gone.png'));
    await expect(status(page)).toHaveText('Embedded metadata could not be read.');
    await expect(action(page)).toHaveAttribute('aria-disabled', 'false');
    await expect(page.locator('#workspace-toast')).toHaveText('');
    // A rejected drop is its own alert beside the selection's status, which it never replaces, and it ends with the
    // concept's toast interval.
    await choose(page, undecodableMp4());
    const failed = status(page).filter({ hasText: COPY.previewFailed });
    await expect(failed).toHaveCount(1);
    await drop(page, '.file-drop', { files: [parityPng('f02.png'), parityPng('f03.png')] });
    const rejected = status(page).filter({ hasText: COPY.multiple });
    await expect(rejected).toHaveAttribute('role', 'alert');
    await expect(failed).toHaveCount(1);
    await expect(rejected).toHaveCount(0, { timeout: 15000 });
    await expect(status(page)).toHaveText([COPY.previewFailed]);
    // The next selection attempt ends a rejection at once.
    await drop(page, '.file-drop', { files: [parityPng('f02.png'), parityPng('f03.png')] });
    await expect(rejected).toHaveCount(1);
    await chooser(page).dispatchEvent('click');
    await chooser(page).dispatchEvent('cancel');
    await expect(status(page)).toHaveText([COPY.previewFailed]);
});

test('undecodable video keeps container metadata', async ({ page }) => {
    await openInspect(page);
    // A real MP4 whose sample entry names a codec no engine decodes; its container tags are intact.
    await choose(page, undecodableMp4());
    await expect(status(page)).toHaveText(COPY.previewFailed);
    await expect(status(page)).toHaveAttribute('role', 'alert');
    await expect(preview(page).locator('video')).toHaveCount(0);
    await openViewer(page);
    await expect(rail(page).locator('.prompt-section.positive p.prompt-text')).toHaveText('a red fox in snow, detailed fur');
    await expect(rail(page).locator('.meta-grid').first().locator('dd').first()).toHaveText('7');
});

/** Records, for each revoked URL, how many elements (inline or in the viewer) still held it, and counts resets. */
const trackReleases = page => page.evaluate(() => {
    window.__released = [];
    const revoke = URL.revokeObjectURL;
    URL.revokeObjectURL = url => {
        const live = [...document.querySelectorAll('img, video')].filter(node => node.getAttribute('src') === url);
        window.__released.push({ url, live: live.length });
        return revoke(url);
    };
    const load = HTMLMediaElement.prototype.load;
    window.__loads = 0;
    HTMLMediaElement.prototype.load = function () { if (!this.hasAttribute('src')) window.__loads++; return load.call(this); };
});

test('URLs and media end with their owning session', async ({ page }) => {
    await instrument(page);
    await openInspect(page);
    // Every release pauses the media and drops its src before the URL is revoked.
    await trackReleases(page);
    await choose(page, localFixture('core-tags.webm', 'video/webm'));
    await expect(preview(page).locator('video')).toBeVisible();
    // A settled video (metadata read, or failed) is reset with load() when it is released.
    await expect.poll(() => preview(page).locator('video').evaluate(node => node.readyState >= 1 || !!node.error)).toBe(true);
    // Replacement.
    await choose(page, parityPng('f02.png'));
    await expect(preview(page).locator('img.local-file-preview')).toBeVisible();
    let state = await urls(page);
    expect(state.revoked).toEqual([state.created[0]]);
    expect(await page.evaluate(() => window.__loads)).toBeGreaterThanOrEqual(1);
    // Route change.
    await page.locator('aside.sidebar nav').getByRole('link', { name: 'Overview', exact: true }).click();
    await expect(main(page).getByRole('heading', { level: 1, name: 'Overview', exact: true })).toBeVisible();
    state = await urls(page);
    expect(state.revoked).toEqual(state.created);
    // Five mount/unmount cycles with a selection and an opened viewer leave no live URL and no dialog.
    for (let cycle = 0; cycle < 5; cycle++) {
        await page.locator('aside.sidebar nav').getByRole('link', { name: 'Inspect a file', exact: true }).click();
        await choose(page, parityPng('f02.png', `cycle-${cycle}.png`));
        await openViewer(page);
        // The modal viewer makes the sidebar inert, so the route changes underneath it.
        await page.evaluate(() => { location.hash = '#/overview'; });
        await expect(main(page).getByRole('heading', { level: 1, name: 'Overview', exact: true })).toBeVisible();
    }
    state = await urls(page);
    expect(state.created).toHaveLength(7);
    expect([...state.revoked].sort()).toEqual([...state.created].sort());
    expect(new Set(state.revoked).size).toBe(state.revoked.length);
    expect((await page.evaluate(() => window.__released)).every(entry => entry.live === 0)).toBe(true);
    await expect(page.locator('dialog')).toHaveCount(0);
    // Page leave releases the selection once.
    await page.locator('aside.sidebar nav').getByRole('link', { name: 'Inspect a file', exact: true }).click();
    await choose(page, parityPng('f02.png', 'leave.png'));
    await expect(preview(page).locator('img.local-file-preview')).toBeVisible();
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: false })));
    state = await urls(page);
    expect(state.created).toHaveLength(8);
    expect(state.revoked.filter(url => url === state.created[7])).toHaveLength(1);
    // Page leave with the viewer open, on a page loaded at Inspect (so its listeners precede the shell's): the viewer
    // closes and releases its media before the URL is revoked, and a page restored from the back-forward cache shows
    // an empty chooser beside the empty selection.
    await page.reload();
    await expect(chooser(page)).toBeAttached();
    await trackReleases(page);
    await choose(page, parityPng('f02.png', 'leave-open.png'));
    await openViewer(page);
    await expect(viewer(page).getByRole('button', { name: 'Zoom in', exact: true })).toHaveAttribute('aria-disabled', 'false');
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })));
    await expect(page.locator('dialog')).toHaveCount(0);
    state = await urls(page);
    expect(state.created).toHaveLength(1);
    expect(state.revoked).toEqual(state.created);
    expect(await page.evaluate(() => window.__released)).toEqual([{ url: state.created[0], live: 0 }]);
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
    await expect(chooser(page)).toBeAttached();
    expect(await chooser(page).evaluate(node => ({ value: node.value, files: node.files.length }))).toEqual({ value: '', files: 0 });
    await expect(preview(page).locator('img, video')).toHaveCount(0);
    await expect(action(page)).toHaveAttribute('aria-disabled', 'true');
});

test('replacing a video that just started loading never blocks the page', async ({ context, browserName }) => {
    test.setTimeout(120000);
    // WebKit (GStreamer) blocked the page when a video whose pipeline was still starting was reset with load(): the
    // next chooser selection, or the steps after it, never completed. Each fresh page replaces its first video as
    // soon as it shows.
    for (let attempt = 0; attempt < 15; attempt++) {
        const page = await context.newPage();
        try {
            if (attempt === 0) await startCoverage(page, browserName);
            await openInspect(page);
            await choose(page, localFixture('core-tags.webm', 'video/webm'));
            await expect(preview(page).locator('video.local-file-preview')).toBeVisible();
            await chooser(page).setInputFiles(parityPng('f02.png'), { timeout: 8000 });
            await expect(preview(page).locator('img.local-file-preview')).toBeVisible();
            await expect(preview(page).locator('video')).toHaveCount(0);
            if (attempt === 0) await saveCoverage(page, browserName);
        } finally {
            await page.close();
        }
    }
});

test('a video replaced while still starting is reset once it settles', async ({ context, browserName }) => {
    // A WebKit (GStreamer) video released exactly at its `loadstart` sometimes blocks the page, with or without any
    // reset and also with the code before the deferred reset (2 of 24 runs); tracked in TESTING.md (D62). WebKit's
    // no-hang guard is `replacing a video that just started loading never blocks the page`.
    test.skip(browserName === 'webkit', 'WebKit release at loadstart is a tracked follow-up (D62)');
    test.setTimeout(120000);
    // The video is replaced from its own `loadstart` (readyState HAVE_NOTHING). It leaves the page with no src before
    // its URL is revoked, and is reset (load() with no src, which frees its decoder) only once it reports metadata or
    // an error, so the page never blocks. Fresh pages share one browser process.
    for (let attempt = 0; attempt < 5; attempt++) {
        const page = await context.newPage();
        try {
            if (attempt === 0) await startCoverage(page, browserName);
            await page.addInitScript(() => {
                const load = HTMLMediaElement.prototype.load;
                HTMLMediaElement.prototype.load = function () { if (!this.hasAttribute('src')) this.__resets = (this.__resets ?? 0) + 1; return load.call(this); };
                const revoke = URL.revokeObjectURL;
                URL.revokeObjectURL = url => {
                    const starting = window.__starting;
                    if (url === starting?.url) starting.revoked = { src: starting.node.hasAttribute('src'), connected: starting.node.isConnected, resets: starting.node.__resets ?? 0 };
                    return revoke(url);
                };
            });
            await openInspect(page);
            await page.evaluate(() => {
                const input = document.querySelector('#local-file'), slot = document.querySelector('#local-preview');
                new MutationObserver((records, observer) => {
                    const video = slot.querySelector('video');
                    if (!video) return;
                    observer.disconnect();
                    video.addEventListener('loadstart', () => {
                        window.__starting = { node: video, url: video.getAttribute('src'), readyState: video.readyState };
                        input.files = window.__pngFiles; input.dispatchEvent(new Event('change', { bubbles: true }));
                    }, { once: true });
                }).observe(slot, { childList: true });
            });
            const png = parityPng('f02.png');
            await page.evaluate(bytes => {
                const transfer = new DataTransfer();
                transfer.items.add(new File([new Uint8Array(bytes)], 'f02.png', { type: 'image/png' }));
                window.__pngFiles = transfer.files;
            }, [...png.buffer]);
            await choose(page, localFixture('core-tags.webm', 'video/webm'));
            await expect(preview(page).locator('img.local-file-preview')).toBeVisible({ timeout: 8000 });
            await expect(preview(page).locator('video')).toHaveCount(0);
            // Released while starting, and never reset synchronously: the URL went once the node had no src.
            expect(await page.evaluate(() => ({ readyState: window.__starting.readyState, revoked: window.__starting.revoked })))
                .toEqual({ readyState: 0, revoked: { src: false, connected: false, resets: 0 } });
            await expect.poll(() => page.evaluate(() => window.__starting.node.__resets ?? 0), { timeout: 15000 }).toBe(1);
            // The page still answers.
            await chooser(page).setInputFiles(parityPng('f02.png', 'after.png'), { timeout: 8000 });
            await expect(preview(page).locator('img.local-file-preview')).toBeVisible({ timeout: 8000 });
            if (attempt === 0) await saveCoverage(page, browserName);
        } finally {
            await page.close();
        }
    }
});

test('closing the viewer on a video that just started never blocks the page', async ({ context, browserName }) => {
    test.setTimeout(180000);
    // The viewer's video teardown resets its element; on WebKit a reset of a still-starting pipeline blocked the page.
    for (let attempt = 0; attempt < 15; attempt++) {
        const page = await context.newPage();
        try {
            if (attempt === 0) await startCoverage(page, browserName);
            await openInspect(page);
            await choose(page, localFixture('core-tags.webm', 'video/webm'));
            await openViewer(page);
            await page.keyboard.press('Escape');
            await expect(viewer(page)).toHaveCount(0, { timeout: 8000 });
            await chooser(page).setInputFiles(parityPng('f02.png'), { timeout: 8000 });
            await expect(preview(page).locator('img.local-file-preview')).toBeVisible({ timeout: 8000 });
            if (attempt === 0) await saveCoverage(page, browserName);
        } finally {
            await page.close();
        }
    }
});

test('replacing a file mid-read never shows or sends the old record', async ({ page, context }) => {
    const host = await bridgeHost(context);
    try {
        await openInspect(page);
        // Hold every read of held.png after its classification prefix.
        await page.evaluate(() => {
            let release;
            window.__gate = new Promise(resolve => { release = resolve; });
            window.__releaseGate = release;
            window.__held = 0;
            const slice = Blob.prototype.slice;
            Blob.prototype.slice = function (...args) {
                const part = slice.apply(this, args);
                if (this.name !== 'held.png' || (args[0] ?? 0) === 0 && (args[1] ?? this.size) <= 32) return part;
                const read = part.arrayBuffer.bind(part);
                part.arrayBuffer = async () => { window.__held++; await window.__gate; return read(); };
                return part;
            };
        });
        await choose(page, parityPng('f03.png', 'held.png'));
        await expect.poll(() => page.evaluate(() => window.__held)).toBeGreaterThan(0);
        await choose(page, sendablePng('fresh.png'));
        await expect(preview(page).locator('img.local-file-preview')).toBeVisible();
        await page.evaluate(() => window.__releaseGate());
        await openViewer(page);
        await expect(viewer(page).locator('#viewer-title')).toHaveText('fresh.png');
        await expect(rail(page).locator('.prompt-section.positive p.prompt-text')).toHaveText(POSITIVE_LINES);
        await sendPair(page);
        expect(await host.evaluate(() => window.inspectApplied)).toEqual([{ positive: POSITIVE_LINES, negative: '' }]);
    } finally {
        await host.evaluate(() => window.inspectHost.dispose());
        await host.close();
    }
});

/** A second tab acting as a ComfyUI host with one Prompt Manager pair destination. */
async function bridgeHost(context) {
    const host = await context.newPage();
    await host.goto(`${workspace}#/overview`);
    await host.evaluate(async () => {
        const { createBridgeHost } = await import('/prompt_manager/js/shared/toolkit/prompt-bridge.js');
        const target = { id: 'pair-1', label: 'Inspect destination', type: 'PromptManagerPair', roles: ['positive', 'negative'] };
        window.inspectApplied = [];
        window.inspectPreviews = [];
        window.inspectHost = createBridgeHost({ origin: location.origin, namespace: '/', label: 'Inspect ComfyUI', adapter: {
            listTargets: () => [target],
            preview: (_id, roles) => {
                window.inspectPreviews.push(roles);
                return { preview: { target, fields: Object.entries(roles).map(([role, after]) => ({ role, before: `Previous ${role}`, after })),
                    composition: { prepend: '', append: '', prependConnected: false, appendConnected: false, before: 'Previous positive', after: roles.positive ?? 'Previous positive' } }, snapshot: roles };
            },
            apply: roles => { window.inspectApplied.push(roles); return { targetId: target.id, values: roles }; },
        } });
    });
    return host;
}

async function sendPair(page) {
    await rail(page).getByRole('button', { name: /^Send prompt pair to ComfyUI: / }).click();
    const sender = page.getByRole('dialog', { name: 'Send to ComfyUI', exact: true });
    await sender.getByRole('combobox', { name: 'ComfyUI window', exact: true }).selectOption({ label: 'Inspect ComfyUI' });
    await sender.getByRole('combobox', { name: 'Destination node', exact: true }).selectOption({ label: 'Inspect destination' });
    await sender.getByRole('button', { name: 'Preview changes', exact: true }).click();
    await sender.getByRole('button', { name: 'Apply to ComfyUI', exact: true }).click();
    await expect(sender).toContainText('Sent to ComfyUI.');
    await sender.getByRole('button', { name: 'Close', exact: true }).click();
}

test('local roles send exactly', async ({ page, context }) => {
    const host = await bridgeHost(context);
    try {
        await openInspect(page);
        await choose(page, sendablePng());
        await openViewer(page);
        await expect(rail(page).locator('.prompt-section.positive p.prompt-text')).toHaveText(POSITIVE_LINES);
        await expect(rail(page).locator('.prompt-section.negative p.prompt-text')).toHaveText('No negative prompt (explicitly empty)');
        await sendPair(page);
        const applied = await host.evaluate(() => window.inspectApplied);
        expect(applied).toEqual([{ positive: POSITIVE_LINES, negative: '' }]);
        const payload = JSON.stringify([applied, await host.evaluate(() => window.inspectPreviews)]);
        expect(payload).not.toContain('blob:');
        expect(payload).not.toContain('send-me');
        // The viewer is still open, focus came back to the send action.
        await expect(viewer(page)).toBeVisible();
    } finally {
        await host.evaluate(() => window.inspectHost.dispose());
        await host.close();
    }
});

test('local inspection makes no content request or storage write', async ({ page }) => {
    await instrument(page);
    await openInspect(page);
    // The shell's own reads (counts, Activity) settle before the baseline.
    await expect(page.locator('aside.sidebar nav [data-route="images"] .nav-count')).toBeAttached();
    const baseline = (await urls(page)).writes.length;
    const requests = [];
    page.on('request', request => requests.push({ url: request.url(), method: request.method(), body: request.postData() }));
    page.on('websocket', socket => requests.push({ url: socket.url(), method: 'WS', body: null }));
    for (const file of [sendablePng('private-name.png'), localFixture('core-tags.webm', 'video/webm', 'private-clip.webm')]) {
        await choose(page, file);
        await openViewer(page);
        await rail(page).locator('summary', { hasText: 'Workflow & raw metadata' }).click();
        await page.keyboard.press('Escape');
        await expect(viewer(page)).toHaveCount(0);
    }
    const network = requests.filter(entry => !entry.url.startsWith('blob:') && !entry.url.startsWith('data:'));
    expect(network.filter(entry => /private|lighthouse|fox/.test(`${entry.url} ${entry.body ?? ''}`))).toEqual([]);
    expect(network.filter(entry => entry.method !== 'GET')).toEqual([]);
    expect(network.filter(entry => !/\/prompt_manager\/(activity|images\/output|library|vocabulary)/.test(entry.url))).toEqual([]);
    expect((await urls(page)).writes.slice(baseline)).toEqual([]);
});

test('touch layout at 390 has no horizontal overflow at 200% zoom', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openInspect(page);
    await choose(page, parityPng('f02.png'));
    await expect(preview(page).locator('img.local-file-preview')).toBeVisible();
    for (const zoom of ['1', '2']) {
        await page.evaluate(value => { document.documentElement.style.zoom = value; }, zoom);
        await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
        const area = await page.locator('.file-drop').evaluate(node => node.getBoundingClientRect().right <= document.documentElement.clientWidth + 0.5);
        expect(area).toBe(true);
    }
});

// LI-4 (D-NEW-1): "Copy settings summary" after the six facts is the legacy metadata page's COPY ALL, in the shared full
// inspector, so a file served in Images and the same bytes chosen here copy the same summary of what the rail shows.
/** Records clipboard writes in the page, or (`clipboard: false`) leaves the page without a clipboard. */
async function stubClipboard(page, { clipboard = true } = {}) {
    await page.addInitScript(available => {
        window.__copied = [];
        Object.defineProperty(navigator, 'clipboard', { configurable: true,
            value: available ? { writeText: async text => { window.__copied.push(text); } } : undefined });
    }, clipboard);
}
/** The summary the rail's own prompts and facts make, in the legacy COPY ALL lines and placeholders. */
const expectedSummary = locator => locator.evaluate(node => {
    const facts = Object.fromEntries([...node.querySelectorAll(':scope .meta-grid:not(details .meta-grid) > div')]
        .map(item => [item.querySelector('dt').textContent, item.querySelector('dd').textContent]));
    // A confirmed prompt has its Copy action; an explicitly empty one takes the legacy placeholder too.
    const prompt = role => {
        const text = node.querySelector(`.prompt-section.${role} [data-copy-role]`) ? node.querySelector(`.prompt-section.${role} p.prompt-text`).textContent : null;
        return text === `No ${role} prompt (explicitly empty)` ? null : text;
    };
    return [`Checkpoint: ${facts.Model}`, `Positive Prompt: ${prompt('positive') ?? 'No prompt found'}`,
        `Negative Prompt: ${prompt('negative') ?? 'No negative prompt found'}`, `Steps: ${facts.Steps}`, `CFG Scale: ${facts.CFG}`,
        `Sampler: ${facts.Sampler}`, `Seed: ${facts.Seed}`].join('\n');
});
async function copySummary(page) {
    const button = rail(page).getByRole('button', { name: 'Copy settings summary', exact: true });
    // It follows the six facts, before "File & provenance".
    expect(await button.evaluate(node => [node.previousElementSibling?.localName, node.nextElementSibling?.localName])).toEqual(['dl', 'details']);
    await button.click();
}

test('Copy settings summary copies the same legacy summary in Images and Inspect', async ({ page, request }) => {
    await stubClipboard(page);
    await expect(await request.post('/__test__/add-local-inspect-file', { data: { name: 'f02.png' } })).toBeOK();
    await page.goto(`${workspace}#/images`);
    await page.locator('article[data-image-key="0:local-inspect/f02.png"] .image-open').click();
    await expect(rail(page).locator('.prompt-section.positive p.prompt-text')).toBeVisible();
    const served = await expectedSummary(rail(page));
    expect(served).toMatch(/^Checkpoint: .+\nPositive Prompt: [\s\S]+\nNegative Prompt: [\s\S]+\nSteps: .+\nCFG Scale: .+\nSampler: .+\nSeed: .+$/);
    expect(served).not.toContain('Unknown');
    await copySummary(page);
    await expect(page.locator('#workspace-toast')).toHaveText('Settings summary copied.');
    expect(await page.evaluate(() => window.__copied)).toEqual([served]);
    await page.keyboard.press('Escape');
    await expect(viewer(page)).toHaveCount(0);

    await openInspect(page);
    await choose(page, parityPng('f02.png'));
    await openViewer(page);
    expect(await expectedSummary(rail(page))).toBe(served);
    await copySummary(page);
    await expect.poll(() => page.evaluate(() => window.__copied)).toEqual([served, served]);
    // The panel variant (the Images side panel) keeps the concept's footer: no summary action there.
    await page.keyboard.press('Escape');
    await page.goto(`${workspace}#/images`);
    await expect(page.locator('#image-inspector .panel-heading')).toBeVisible();
    await expect(page.locator('#image-inspector').getByRole('button', { name: 'Copy settings summary', exact: true })).toHaveCount(0);
});

test('Copy settings summary without a clipboard opens the select-to-copy dialog', async ({ page }) => {
    await stubClipboard(page, { clipboard: false });
    await openInspect(page);
    await choose(page, sendablePng());
    await openViewer(page);
    const summary = await expectedSummary(rail(page));
    expect(summary).toContain(`Positive Prompt: ${POSITIVE_LINES}`);
    await copySummary(page);
    const fallback = page.getByRole('dialog', { name: 'Copy settings summary', exact: true });
    await expect(fallback).toBeVisible();
    await expect(fallback.getByRole('textbox', { name: 'Select and copy this text', exact: true })).toHaveValue(summary);
    await expect(fallback.getByRole('textbox', { name: 'Select and copy this text', exact: true })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(fallback).toHaveCount(0);
    // Escape closes only the copy dialog; focus returns to the action in the still-open viewer.
    await expect(viewer(page)).toBeVisible();
    await expect(rail(page).getByRole('button', { name: 'Copy settings summary', exact: true })).toBeFocused();
});
