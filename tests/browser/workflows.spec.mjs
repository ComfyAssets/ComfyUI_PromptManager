import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';

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

test('actual gallery review generates and persists tags for the selected root', async ({ page, request }) => {
    await page.goto('/prompt_manager/gallery');
    await expect(page.locator('#galleryGrid .image-item')).toHaveCount(2);
    await page.locator('#autoTagBtn').click();
    const generated = page.waitForResponse(response => response.url().endsWith('/prompt_manager/autotag/single'));
    await page.locator('#startReviewBtn').click();
    const generation = await generated;
    expect(generation.request().postDataJSON().root_index).toBe(0);
    await expect(page.locator('#reviewTagsContainer')).toContainText('generated');
    const result = page.waitForResponse(response => response.url().endsWith('/prompt_manager/autotag/apply'));
    await page.locator('#applyReviewBtn').click();
    expect((await (await result).json()).success).toBe(true);
    const data = await (await request.get('/prompt_manager/recent?limit=20')).json();
    expect(data.results.find(prompt => prompt.text === 'Cat portrait').tags).toContain('generated');
    expect(data.results.find(prompt => prompt.text === 'Dog landscape').tags).not.toContain('generated');
});

test('actual Tags page reloads the selected filter and opens its image viewer', async ({ page }) => {
    await page.goto('/prompt_manager/admin#/tags');
    await page.locator('[data-tag-name="cats"]').click();
    await expect(page.locator('#tagPromptsGrid')).toContainText('Cat portrait');
    await page.locator('[data-tag-name="dogs"]').click();
    await expect(page.locator('#tagPromptsGrid')).toContainText('Dog landscape');
    await expect(page.locator('#tagPromptsGrid')).not.toContainText('Cat portrait');
    await page.locator('#tagPromptsGrid img').first().click();
    await expect(page.locator('.viewer-canvas img')).toBeVisible();
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
