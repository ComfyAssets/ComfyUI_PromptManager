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
    // The page shows the edit at once and saves it after: reload only once the save answered (a reload that wins the
    // race abandons the PUT, as seen on WebKit in CI).
    const savedEdit = page.waitForResponse(item => item.request().method() === 'PUT'
        && item.url().endsWith(`/prompt_manager/prompts/${id}`));
    await editor.press('Enter');
    await expect(page.locator('.prompt-text').filter({ hasText: 'Browser edited portrait' })).toBeVisible();
    expect((await savedEdit).ok()).toBe(true);
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
    await page.locator('#pageOptionsBtn').click();
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
