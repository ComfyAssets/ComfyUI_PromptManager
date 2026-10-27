import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';

test.beforeEach(async ({ request, page, browserName }) => {
    await startCoverage(page, browserName);
    await expect(await request.post('/__test__/reset')).toBeOK();
    page.workflowErrors = [];
    page.on('pageerror', error => page.workflowErrors.push(error.message));
    page.on('response', response => {
        if (response.status() >= 500 && response.url().includes('/prompt_manager/')) {
            page.workflowErrors.push(`${response.status()} ${response.url()}`);
        }
    });
    await page.addInitScript(() => { localStorage.setItem('pm_update_notice_dismissed', '3.2.0'); });
});

test.afterEach(async ({ page, browserName }) => {
    await saveCoverage(page, browserName);
    expect(page.workflowErrors).toEqual([]);
});

// #249 S6b: the legacy admin page says where the library lives (the fixture's database is configured explicitly).
test('the admin page names the library location', async ({ page }) => {
    await page.goto('/prompt_manager/admin');
    await expect(page.locator('#libraryLocation')).toHaveText('Library: browser.db (set in config.json)');
    await expect(page.locator('#totalPrompts')).not.toHaveText('-');
});

test('actual gallery form saves a prompt that the admin page can edit and reload', async ({ page, request }) => {
    await page.goto('/prompt_manager/gallery');
    await page.locator('#addPromptBtn').click();
    await page.locator('#addPromptText').fill('Browser saved landscape');
    await page.locator('#addPromptCategory').fill('Browser tests');
    await page.locator('#addPromptTagInput').fill('browser-tag');
    await page.locator('#addPromptTagInput').press('Enter');
    await page.locator('#saveNewPromptBtn').click();
    await expect(page.locator('#addPromptModal')).toBeHidden();
    await page.goto('/prompt_manager/admin');
    const text = page.locator('.prompt-text').filter({ hasText: 'Browser saved landscape' });
    await expect(text).toBeVisible();
    const id = await text.getAttribute('data-id');
    await page.locator(`[data-action="edit"][data-prompt-id="${id}"]`).click();
    const editor = page.locator(`.prompt-text[data-id="${id}"]`);
    await editor.fill('Browser edited portrait');
    await editor.press('Enter');
    await expect(page.locator('.prompt-text').filter({ hasText: 'Browser edited portrait' })).toBeVisible();
    await page.reload();
    await expect(page.locator('.prompt-text').filter({ hasText: 'Browser edited portrait' })).toBeVisible();
    const saved = await request.post('/prompt_manager/save', { data: { text: 'Browser saved landscape' } });
    expect((await saved.json()).prompt_id).not.toBe(Number(id));
});

// WebKit fails a fetch that a leaving page starts ("Fetch API cannot load … due to access control checks."), and
// Playwright reports that console error as a page error. A save's follow-up reads must not start once the page hides:
// here the statistics read after an in-place edit is held at the server while the page reloads.
test('an in-place edit whose follow-up reads outlive the page starts no read while it leaves', async ({ page, request }) => {
    await page.goto('/prompt_manager/admin');
    const text = page.locator('.prompt-text').filter({ hasText: 'Cat portrait' });
    const id = await text.getAttribute('data-id');
    await expect(await request.post('/__test__/hold', { data: { path: '/prompt_manager/stats' } })).toBeOK();
    await page.locator(`[data-action="edit"][data-prompt-id="${id}"]`).click();
    const editor = page.locator(`.prompt-text[data-id="${id}"]`);
    await editor.fill('Cat portrait at dusk');
    await editor.press('Enter');
    const held = async () => (await (await request.get('/__test__/hold')).json()).held['/prompt_manager/stats'];
    await expect.poll(held).toBe(1);
    await page.reload({ waitUntil: 'commit' });
    await expect(await request.post('/__test__/release')).toBeOK();
    await expect(page.locator('.prompt-text').filter({ hasText: 'Cat portrait at dusk' })).toBeVisible();
});

// A leave that never happens (a stopped navigation, or one answered with a download) must not leave the list dead.
test('a page whose leave was called off reads its list again on the next input', async ({ page }) => {
    await page.goto('/prompt_manager/admin');
    await expect(page.locator('.prompt-text').first()).toBeVisible();
    await page.evaluate(() => window.dispatchEvent(new Event('beforeunload')));
    await page.mouse.click(1, 1);
    const reread = page.waitForResponse(response => new URL(response.url()).pathname === '/prompt_manager/recent');
    await page.locator('#sortBy').selectOption('created_asc');
    expect(new URL((await reread).url()).searchParams.get('sort')).toBe('created_asc');
    await expect(page.locator('#resultsTitle')).toHaveText('Recent Prompts');
});

// The gallery's startup thumbnail check runs a second after load. When that second ends while the page is leaving,
// the check must start no read: WebKit fails it with "Fetch API cannot load … due to access control checks." and
// Playwright reports a page error (seen in CI on PR #289). The page clock makes the second end after the leave began.
for (const leave of ['beforeunload', 'pagehide']) {
    test(`the gallery startup check starts no read after ${leave}`, async ({ page }) => {
        await page.clock.install();
        const statusReads = [];
        page.on('request', req => { if (req.url().includes('/images/thumbnails/status')) statusReads.push(req.url()); });
        await page.goto('/prompt_manager/gallery');
        await expect(page.locator('#galleryGrid .image-item')).toHaveCount(2);
        await page.evaluate(type => window.dispatchEvent(new Event(type)), leave);
        await page.clock.runFor(1500);
        // A read made after the clock ran is listed, so the listener saw every status read.
        await page.evaluate(() => fetch('/prompt_manager/images/thumbnails/status?probe=1'));
        expect(statusReads.map(url => new URL(url).search)).toEqual(['?probe=1']);
    });
}

// #222: the gallery's Clear cache clears the server's thumbnails by its own request and keeps this browser's
// preferences and other tools' storage; nothing of Prompt Manager's is cached in a browser, and the toast says so.
test('the gallery cache clear keeps preferences and other tools caches', async ({ page }) => {
    await page.addInitScript(() => {
        if (sessionStorage.getItem('seeded')) return;
        sessionStorage.setItem('seeded', '1');
        localStorage.setItem('pm_workspace_theme', 'light');
        localStorage.setItem('other-tool-setting', 'keep');
        localStorage.setItem('pm_other_tool', 'keep');
    });
    await page.goto('/prompt_manager/gallery');
    await expect(page.locator('#galleryGrid .image-item')).toHaveCount(2);
    await page.evaluate(async () => { await (await caches.open('other-tool-gallery-thumbnails')).put('/x', new Response('x')); });
    const clears = [];
    page.on('request', req => { if (req.url().includes('/clear-thumbnails')) clears.push(req.method()); });
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#settingsBtn').click();
    await page.locator('#clearCacheBtn').click();
    await expect(page.getByText("Thumbnails cleared; original images untouched. Nothing of Prompt Manager's is cached in this browser."))
        .toBeVisible();
    expect(clears).toEqual(['POST']);
    expect(await page.evaluate(async () => ({
        theme: localStorage.getItem('pm_workspace_theme'), other: localStorage.getItem('other-tool-setting'),
        prefixed: localStorage.getItem('pm_other_tool'), notice: localStorage.getItem('pm_update_notice_dismissed'),
        caches: await caches.keys(),
    }))).toEqual({ theme: 'light', other: 'keep', prefixed: 'keep', notice: '3.2.0', caches: ['other-tool-gallery-thumbnails'] });
});

// #379: the reviewed tags are that image's own tags; no prompt changes.
test('actual gallery review generates and persists tags for the selected root\'s image', async ({ page, request }) => {
    await page.goto('/prompt_manager/gallery');
    await expect(page.locator('#galleryGrid .image-item')).toHaveCount(2);
    await page.locator('#autoTagBtn').click();
    const generated = page.waitForResponse(response => response.url().endsWith('/prompt_manager/autotag/single'));
    await page.locator('#startReviewBtn').click();
    const generation = await generated;
    expect(generation.request().postDataJSON().root_index).toBe(0);
    await expect(page.locator('#reviewTagsContainer')).toContainText('sandstone');
    const result = page.waitForResponse(response => response.url().endsWith('/prompt_manager/autotag/apply'));
    await page.locator('#applyReviewBtn').click();
    expect((await (await result).json()).success).toBe(true);
    const data = await (await request.get('/prompt_manager/recent?limit=20')).json();
    expect(data.results.find(prompt => prompt.text === 'Cat portrait').tags).toEqual(['cats']);
    expect(data.results.find(prompt => prompt.text === 'Dog landscape').tags).toEqual(['dogs']);
    const tagged = await (await request.get('/prompt_manager/images/output?mode=workspace&tags=sandstone')).json();
    expect(tagged.images.map(image => `${image.root_index}:${image.path}`)).toEqual(['0:shared/same.png']);
});

test('actual Tags page reloads the selected filter and opens its image viewer', async ({ page }) => {
    await page.goto('/prompt_manager/admin#/tags');
    await page.locator('[data-tag-name="cats"]').click();
    await expect(page.locator('#tagPromptsGrid')).toContainText('Cat portrait');
    const dogs = page.waitForResponse(response => response.url().includes('/tags/dogs/prompts?'));
    await page.locator('[data-tag-name="dogs"]').click();
    const [shown] = (await (await dogs).json()).prompts[0].images;
    await expect(page.locator('#tagPromptsGrid')).toContainText('Dog landscape');
    await expect(page.locator('#tagPromptsGrid')).not.toContainText('Cat portrait');
    await page.locator('#tagPromptsGrid img').first().click();
    await expect(page.locator('.viewer-canvas img')).toBeVisible();
    // The viewer's title names the shown image through the page's title callback. ViewerJS renders it only once the
    // image is viewed, so closing at "visible" alone left the callback unrun on slower hosts (CI coverage missed it).
    await expect(page.locator('.viewer-title')).toHaveText(shown.filename);
    await page.locator('.viewer-container').getByRole('button', { name: 'Close', exact: true }).click();
    await expect(page.locator('.viewer-container')).toHaveCount(0);
});

test('actual gallery serves colliding filenames and shows the matching prompt', async ({ page }) => {
    await page.goto('/prompt_manager/gallery');
    await expect(page.locator('#galleryGrid .image-item')).toHaveCount(2);
    const images = page.locator('#galleryGrid img[data-original]');
    const first = await images.nth(0).getAttribute('data-original');
    const second = await images.nth(1).getAttribute('data-original');
    expect(first).not.toBe(second);
    await page.locator('#galleryGrid .image-item[data-image-index="0"]').click();
    await expect(page.locator('#metadata-content')).toContainText('Cat portrait');
    await page.keyboard.press('ArrowRight');
    await expect(page.locator('#metadata-content')).toContainText('Dog landscape');
    await page.keyboard.press('Escape');
    await expect(page.locator('#metadata-sidebar')).toHaveCount(0);
});

test('an edit collision is rejected by the backend and the page restores the original text', async ({ page }) => {
    await page.goto('/prompt_manager/admin');
    const original = page.locator('.prompt-text').filter({ hasText: 'Cat portrait' });
    const id = await original.getAttribute('data-id');
    await page.locator(`[data-action="edit"][data-prompt-id="${id}"]`).click();
    const editor = page.locator(`.prompt-text[data-id="${id}"]`);
    await editor.fill('Dog landscape');
    const rejected = page.waitForResponse(response => response.request().method() === 'PUT' && response.url().endsWith(`/prompts/${id}`));
    await editor.press('Enter');
    expect((await rejected).status()).toBe(409);
    await expect(editor).toHaveText('Cat portrait');
    await page.reload();
    await expect(page.locator(`.prompt-text[data-id="${id}"]`)).toHaveText('Cat portrait');
});

test('a delayed real Tags response cannot replace a newer selection', async ({ page }) => {
    let release;
    const delayed = new Promise(resolve => { release = resolve; });
    let received;
    const waiting = new Promise(resolve => { received = resolve; });
    await page.route('**/prompt_manager/tags/cats/prompts?*', async route => {
        const response = await route.fetch();
        received();
        await delayed;
        await route.fulfill({ response });
    });
    await page.goto('/prompt_manager/admin#/tags');
    await page.locator('[data-tag-name="cats"]').click();
    await waiting;
    await page.locator('[data-tag-name="dogs"]').click();
    await expect(page.locator('#tagPromptsGrid')).toContainText('Dog landscape');
    const completed = page.waitForResponse(response => response.url().includes('/tags/cats/prompts?'));
    release();
    await completed;
    await expect(page.locator('#tagPromptsGrid')).not.toContainText('Cat portrait');
});

test('gallery infinite scroll appends a real backend page while retaining existing cards', async ({ page, request }) => {
    await expect(await request.post('/__test__/reset', { data: { extra_images: 108 } })).toBeOK();
    await page.goto('/prompt_manager/gallery');
    await expect(page.locator('#galleryGrid .image-item')).toHaveCount(100);
    await page.locator('#settingsBtn').click();
    await page.locator('#infiniteScrollToggle').check();
    await page.locator('#saveSettingsBtn').click();
    await expect(page.locator('#settingsModal')).toBeHidden();
    const first = page.locator('#galleryGrid .image-item').first();
    await first.evaluate(element => { element.dataset.preserved = 'yes'; });
    await page.locator('#galleryScroll').evaluate(element => { element.scrollTop = element.scrollHeight; });
    await expect(page.locator('#galleryGrid .image-item')).toHaveCount(110);
    await expect(first).toHaveAttribute('data-preserved', 'yes');
});

async function promptSnapshot(request) {
    const response = await request.get('/prompt_manager/recent?limit=100');
    await expect(response).toBeOK();
    return (await response.json()).results.map(({ id, text, tags, images }) => ({
        id, text, tags: [...tags].sort(),
        images: images.map(image => image.image_path).sort(),
    })).sort((a, b) => a.id - b.id);
}

async function downloadBackup(page) {
    const downloading = page.waitForEvent('download');
    await page.locator('#backupBtn').click();
    const download = await downloading;
    expect(download.suggestedFilename()).toMatch(/^prompts_backup_.*\.db$/);
    expect(await download.failure()).toBeNull();
    const chunks = [];
    for await (const chunk of await download.createReadStream()) chunks.push(chunk);
    const buffer = Buffer.concat(chunks);
    expect(buffer.subarray(0, 16).toString()).toBe('SQLite format 3\0');
    return { name: download.suggestedFilename(), mimeType: 'application/octet-stream', buffer };
}

async function chooseRestore(page, file) {
    await page.locator('#restoreBtn').click();
    await expect(page.locator('#restoreModal')).toBeVisible();
    await page.locator('#restoreFileInput').setInputFiles(file);
}

// Confirms the chosen restore and waits for the server's answer, not for a timer: a restore is disk-bound and can take
// longer than an expect timeout on a slow runner (#359). The modal closes only after that answer.
async function confirmRestoreAndWait(page) {
    const restored = page.waitForResponse(response => response.url().endsWith('/prompt_manager/restore'));
    await page.locator('#confirmRestore').click();
    const response = await restored;
    const body = await response.text();
    expect(response.status(), body).toBe(200);
    expect(JSON.parse(body)).toMatchObject({ success: true, prompt_count: 2, backup_created: expect.any(String) });
    await expect(page.locator('#restoreModal')).toBeHidden();
}

function confirmNextDialog(page, accept, message) {
    page.once('dialog', async dialog => {
        expect(dialog.type()).toBe('confirm');
        expect(dialog.message()).toContain(message);
        await (accept ? dialog.accept() : dialog.dismiss());
    });
}

async function deletePromptButton(page, text) {
    const prompt = page.locator('.prompt-text').filter({ hasText: text });
    await expect(prompt).toBeVisible();
    const id = await prompt.getAttribute('data-id');
    return page.locator(`[data-action="delete"][data-prompt-id="${id}"]`);
}

test('downloaded backup restores deleted prompts, tags, and image links through the actual upload form', async ({ page, request }) => {
    await page.goto('/prompt_manager/admin');
    await expect(page.locator('.prompt-text')).toHaveCount(2);
    const before = await promptSnapshot(request);
    expect(before.map(prompt => prompt.tags)).toEqual([['cats'], ['dogs']]);
    expect(before.every(prompt => prompt.images.length === 1)).toBe(true);
    const backup = await downloadBackup(page);
    const button = await deletePromptButton(page, 'Cat portrait');
    confirmNextDialog(page, true, 'delete this prompt');
    await button.click();
    await expect(page.locator('.prompt-text')).toHaveCount(1);
    await expect(await request.post('/prompt_manager/save', { data: { text: 'Added after backup', tags: ['temporary'] } })).toBeOK();
    expect(await promptSnapshot(request)).not.toEqual(before);
    await chooseRestore(page, backup);
    confirmNextDialog(page, true, backup.name);
    await confirmRestoreAndWait(page);
    await expect(page.locator('.prompt-text')).toHaveCount(2);
    await page.reload();
    await expect(page.locator('.prompt-text').filter({ hasText: 'Cat portrait' })).toBeVisible();
    await expect(page.locator('.prompt-text').filter({ hasText: 'Added after backup' })).toHaveCount(0);
    expect(await promptSnapshot(request)).toEqual(before);
    await page.goto('/prompt_manager/admin#/tags');
    await page.locator('[data-tag-name="cats"]').click();
    await expect(page.locator('#tagPromptsGrid')).toContainText('Cat portrait');
    await page.locator('#tagPromptsGrid img').first().click();
    await expect(page.locator('.viewer-canvas img')).toBeVisible();
    await expect.poll(() => page.locator('.viewer-canvas img').evaluate(image => image.complete && image.naturalWidth > 0)).toBe(true);
});

test('restore validation and cancellation preserve live data and allow a valid retry', async ({ page, request }) => {
    await page.goto('/prompt_manager/admin');
    const before = await promptSnapshot(request);
    const backup = await downloadBackup(page);
    let uploads = 0;
    page.on('request', request => { if (request.url().endsWith('/prompt_manager/restore')) uploads++; });
    await page.locator('#restoreBtn').click();
    await page.locator('#confirmRestore').click();
    await expect(page.getByText('Please select a database file to restore', { exact: true })).toBeVisible();
    await page.locator('#restoreFileInput').setInputFiles({ ...backup, name: 'backup.txt' });
    await page.locator('#confirmRestore').click();
    await expect(page.getByText('Please select a valid .db file', { exact: true })).toBeVisible();
    expect(uploads).toBe(0);
    await page.locator('#restoreFileInput').setInputFiles(backup);
    confirmNextDialog(page, false, 'replace your current database');
    await page.locator('#confirmRestore').click();
    await expect(page.locator('#restoreModal')).toBeVisible();
    expect(uploads).toBe(0);
    await page.locator('#restoreFileInput').setInputFiles({ name: 'corrupt.db', mimeType: 'application/octet-stream', buffer: Buffer.from('not a SQLite database') });
    confirmNextDialog(page, true, 'corrupt.db');
    const rejected = page.waitForResponse(response => response.url().endsWith('/prompt_manager/restore'));
    await page.locator('#confirmRestore').click();
    expect((await rejected).status()).toBe(400);
    await expect(page.getByText(/Restore failed:/)).toBeVisible();
    await expect(page.locator('#restoreModal')).toBeVisible();
    await expect(page.locator('#confirmRestore')).toBeEnabled();
    expect(await promptSnapshot(request)).toEqual(before);
    await page.locator('#restoreFileInput').setInputFiles(backup);
    confirmNextDialog(page, true, backup.name);
    await confirmRestoreAndWait(page);
    expect(await promptSnapshot(request)).toEqual(before);
});

test('backup and restore recover from interrupted network requests without losing data', async ({ page, request }) => {
    await page.goto('/prompt_manager/admin');
    const before = await promptSnapshot(request);
    await page.route('**/prompt_manager/backup', route => route.abort('failed'), { times: 1 });
    await page.locator('#backupBtn').click();
    await expect(page.getByText(/Backup failed:/)).toBeVisible();
    const backup = await downloadBackup(page);
    await chooseRestore(page, backup);
    await page.route('**/prompt_manager/restore', route => route.abort('failed'), { times: 1 });
    confirmNextDialog(page, true, backup.name);
    await page.locator('#confirmRestore').click();
    await expect(page.getByText(/Restore failed:/)).toBeVisible();
    await expect(page.locator('#confirmRestore')).toBeEnabled();
    await expect(page.locator('#restoreModal')).toBeVisible();
    expect(await promptSnapshot(request)).toEqual(before);
    confirmNextDialog(page, true, backup.name);
    await confirmRestoreAndWait(page);
    expect(await promptSnapshot(request)).toEqual(before);
});

test('prompt deletion requires confirmation and can retry after a network failure', async ({ page, request }) => {
    await page.goto('/prompt_manager/admin');
    const before = await promptSnapshot(request);
    const button = await deletePromptButton(page, 'Cat portrait');
    let deletes = 0;
    page.on('request', request => { if (request.method() === 'DELETE') deletes++; });
    confirmNextDialog(page, false, 'delete this prompt');
    await button.click();
    expect(deletes).toBe(0);
    expect(await promptSnapshot(request)).toEqual(before);
    await page.route('**/prompt_manager/delete/*', route => route.abort('failed'), { times: 1 });
    confirmNextDialog(page, true, 'delete this prompt');
    await button.click();
    await expect(page.getByText('Failed to delete prompt', { exact: true })).toBeVisible();
    expect(await promptSnapshot(request)).toEqual(before);
    confirmNextDialog(page, true, 'delete this prompt');
    await button.click();
    await expect(page.locator('.prompt-text')).toHaveCount(1);
    await page.reload();
    await expect(page.locator('.prompt-text')).toHaveCount(1);
    expect(await promptSnapshot(request)).toEqual(before.filter(prompt => prompt.text !== 'Cat portrait'));
    await page.goto('/prompt_manager/gallery');
    await expect(page.locator('#galleryGrid .image-item')).toHaveCount(2);
    for (const url of await page.locator('#galleryGrid img[data-original]').evaluateAll(images => images.map(image => image.dataset.original))) {
        await expect(await request.get(url)).toBeOK();
    }
});

// The duplicate scan reads the output root only, but the fixture's input root holds a different
// shared/same.png. A bare /images/serve/<path> URL tries every gallery root in turn, so once the
// output copy is deleted it answers 200 with the input copy: check each file in its own root.
const serveIn = (root, path) => `/prompt_manager/images/serve/${path.split(/[\\/]/).map(encodeURIComponent).join('/')}?root=${root}`;
const rootOf = url => new URL(url, 'http://gallery.test').searchParams.get('root');

// Which copy the scan keeps depends on file times: the fixture stamps shared/same.png with the
// wall clock, and the kernel can stamp its bulk copies a fraction of a millisecond earlier.
// listOrder rewrites the finished scan's groups so a test can pick the order it needs.
async function openDuplicateScan(page, request, listOrder = null) {
    await expect(await request.post('/__test__/reset', { data: { extra_images: 2 } })).toBeOK();
    if (listOrder) {
        await page.route('**/prompt_manager/scan_duplicates/stream', async route => {
            const response = await route.fetch();
            const body = (await response.text()).replace(/^data: (.*)$/gm, (line, json) => {
                const event = JSON.parse(json);
                if (event.type !== 'complete') return line;
                for (const group of event.duplicates) group.images = listOrder(group.images);
                return `data: ${JSON.stringify(event)}`;
            });
            await route.fulfill({ response, body });
        });
    }
    await page.goto('/prompt_manager/gallery');
    await expect(page.locator('#galleryGrid .image-item')).toHaveCount(4);
    const originals = await page.locator('#galleryGrid img[data-original]').evaluateAll(images => images.map(image => image.dataset.original));
    const output = rootOf(originals.find(url => url.includes('/bulk/')));
    const input = rootOf(originals.find(url => url.includes('/shared/same.png') && rootOf(url) !== output));
    expect(output).not.toBeNull();
    expect(input).not.toBeNull();
    await page.locator('#settingsBtn').click();
    await page.locator('#scanDuplicatesBtn').click();
    await expect(page.locator('#duplicatesCount')).toHaveText('1');
    const candidates = page.locator('.duplicate-checkbox');
    await expect(candidates).toHaveCount(2);
    return { originals, output, input, candidates };
}

async function expectOnlySelectedGone(page, request, { originals, output, input }, selected, retained) {
    expect((await request.get(serveIn(output, selected))).status()).toBe(404);
    await expect(await request.get(serveIn(output, retained))).toBeOK();
    await expect(await request.get(serveIn(input, 'shared/same.png'))).toBeOK();
    await page.reload();
    await expect(page.locator('#galleryGrid .image-item')).toHaveCount(3);
    const remaining = await page.locator('#galleryGrid img[data-original]').evaluateAll(images => images.map(image => image.dataset.original));
    expect(remaining).toHaveLength(originals.length - 1);
    for (const url of remaining) {
        expect(originals).toContain(url);
        await expect(await request.get(url)).toBeOK();
    }
}

test('duplicate cleanup cancels safely and deletes only the selected file after a retry', async ({ page, request }) => {
    const scan = await openDuplicateScan(page, request);
    const { output, candidates } = scan;
    const selected = await candidates.first().getAttribute('data-image-path');
    const retained = await candidates.last().getAttribute('data-image-path');
    await candidates.first().check();
    let deletes = 0;
    page.on('request', request => { if (request.url().endsWith('/delete_duplicate_images')) deletes++; });
    confirmNextDialog(page, false, 'permanently delete 1 duplicate image files');
    await page.locator('#removeDuplicatesBtn').click();
    expect(deletes).toBe(0);
    await expect(await request.get(serveIn(output, selected))).toBeOK();
    await page.route('**/prompt_manager/delete_duplicate_images', route => route.abort('failed'), { times: 1 });
    confirmNextDialog(page, true, 'permanently delete 1 duplicate image files');
    await page.locator('#removeDuplicatesBtn').click();
    await expect(page.getByText('Failed to delete duplicate images', { exact: true })).toBeVisible();
    await expect(page.locator('#removeDuplicatesBtn')).toBeEnabled();
    await expect(await request.get(serveIn(output, selected))).toBeOK();
    confirmNextDialog(page, true, 'permanently delete 1 duplicate image files');
    const deleted = page.waitForResponse(response => response.url().endsWith('/delete_duplicate_images'));
    await page.locator('#removeDuplicatesBtn').click();
    expect((await (await deleted).json())).toMatchObject({ success: true, deleted_count: 1, failed_count: 0 });
    await expect(page.locator('#duplicatesModal')).toBeHidden();
    await expectOnlySelectedGone(page, request, scan, selected, retained);
});

test('deleting a duplicate whose path another gallery root also holds removes only the scanned copy', async ({ page, request }) => {
    // The order CI saw: a bulk copy stamped first, so shared/same.png is the first Delete candidate.
    const ciOrder = ['bulk/0000.png', 'shared/same.png', 'bulk/0001.png'];
    const rank = image => ciOrder.indexOf(image.path.replace(/\\/g, '/'));
    const scan = await openDuplicateScan(page, request, images => [...images].sort((a, b) => rank(a) - rank(b)));
    const selected = await scan.candidates.first().getAttribute('data-image-path');
    const retained = await scan.candidates.last().getAttribute('data-image-path');
    expect(selected.replace(/\\/g, '/')).toBe('shared/same.png');
    await scan.candidates.first().check();
    confirmNextDialog(page, true, 'permanently delete 1 duplicate image files');
    const deleted = page.waitForResponse(response => response.url().endsWith('/delete_duplicate_images'));
    await page.locator('#removeDuplicatesBtn').click();
    expect((await (await deleted).json())).toMatchObject({ success: true, deleted_count: 1, failed_count: 0 });
    await expect(page.locator('#duplicatesModal')).toBeHidden();
    await expectOnlySelectedGone(page, request, scan, selected, retained);
});
