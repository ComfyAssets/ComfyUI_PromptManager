// #659 (design Part 5): Inspect's Choose prompt. A queued ambiguous file (tests/browser/workspace_review_fixture.py,
// recorded through the scan's recorder) shows its candidates in the Images details panel; choosing one links the file
// and takes it out of Needs review, leaving it unresolved decides it, and an item with nothing to choose sends the user
// to Needs review.
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';

const images = '/prompt_manager/workspace#/images';
const details = page => page.getByRole('complementary', { name: 'Selected image details', exact: true });
const choice = page => details(page).getByRole('region', { name: 'Choose prompt', exact: true });
const context = page => choice(page).getByRole('combobox', { name: 'Sampler context', exact: true });
const toast = page => page.locator('#workspace-toast');
const OPTIONS = ['Sampler 11 · Golden dunes at dusk / Soft negative, blur', 'Sampler 13 · Harbor lights at night / Soft negative, blur'];

async function seed(request, data) {
    const response = await request.post('/__test__/review/reset', { data });
    await expect(response).toBeOK();
    return response.json();
}
const pending = async request => (await (await request.get('/prompt_manager/review/items?limit=1')).json()).total;
const links = async request => (await (await request.get('/__test__/review/links')).json()).links;

/** The Images page with `filename`'s details open (a page may already have selected it). */
async function inspect(page, filename) {
    await page.goto(images);
    const card = page.locator('article[data-image-key]').filter({ hasText: filename });
    await card.getByRole('button', { name: `Inspect ${filename}`, exact: true }).click();
    await expect(details(page).locator('.detail-inner > p.small').first()).toHaveText(filename);
}

test.beforeEach(async ({ page, browserName }) => {
    await startCoverage(page, browserName);
    page.choiceErrors = [];
    page.on('pageerror', error => page.choiceErrors.push(error.message));
});
test.afterEach(async ({ page, browserName, request }) => {
    await saveCoverage(page, browserName);
    expect(page.choiceErrors).toEqual([]);
    await request.post('/__test__/jobs/restart');
});

test('choosing a candidate in Inspect links the file and takes it out of Needs review', async ({ page, request }) => {
    await seed(request, { items: 1 });
    expect(await pending(request)).toBe(1);
    await inspect(page, 'review_0000.png');
    // The section follows the file's own prompt blocks.
    await expect(choice(page)).toBeVisible();
    await expect(details(page).locator('.detail-inner > .inspect-choice')).toHaveCount(1);
    await expect(context(page).locator('option')).toHaveText(OPTIONS);
    await expect(choice(page).locator('.prompt-section.positive .prompt-text')).toHaveText('Golden dunes at dusk');
    await context(page).selectOption({ label: OPTIONS[1] });
    await expect(choice(page).locator('.prompt-section.positive .prompt-text')).toHaveText('Harbor lights at night');
    await expect(choice(page).locator('.prompt-section.negative .prompt-text')).toHaveText('Soft negative, blur');
    await expect(choice(page).getByRole('button', { name: 'Copy chosen positive prompt', exact: true })).toBeVisible();
    await expect(choice(page).getByRole('button', { name: 'Send chosen positive prompt to ComfyUI', exact: true })).toBeVisible();

    const decided = page.waitForResponse(response => /\/review\/items\/[^/]+\/decision$/.test(response.url()));
    const reread = page.waitForResponse(response => response.url().includes('/prompt_manager/images/inspect/') && response.ok());
    await choice(page).getByRole('button', { name: 'Use this prompt', exact: true }).click();
    expect((await decided).status()).toBe(200);
    await reread;
    await expect(toast(page)).toHaveText('Association accepted.');
    // The inspection is read again: the file is linked, so the section is gone and Prompt history opens its prompt.
    await expect(choice(page)).toHaveCount(0);
    await expect(details(page).getByRole('button', { name: 'Prompt history', exact: true })).toBeVisible();
    expect(await pending(request)).toBe(0);
    expect((await links(request)).map(row => [row.filename, row.prompt])).toEqual([['review_0000.png', 'Harbor lights at night']]);

    await page.goto('/prompt_manager/workspace#/review');
    await expect(page.locator('#workspace-main .notice h2')).toHaveText('No images need a decision');
});

test('Leave unresolved in Inspect decides the item and links nothing', async ({ page, request }) => {
    await seed(request, { items: 2 });
    await inspect(page, 'review_0001.png');
    await expect(context(page).locator('option')).toHaveText(OPTIONS);
    const decided = page.waitForResponse(response => /\/review\/items\/[^/]+\/decision$/.test(response.url()));
    await choice(page).getByRole('button', { name: 'Leave unresolved', exact: true }).click();
    expect((await decided).status()).toBe(200);
    await expect(toast(page)).toHaveText('Left unresolved.');
    // The item is decided: the section is gone and the file stays unlinked.
    await expect(choice(page)).toHaveCount(0);
    await expect(details(page).getByRole('button', { name: 'Prompt history', exact: true })).toHaveCount(0);
    expect(await pending(request)).toBe(1);
    expect(await links(request)).toEqual([]);
});

test('an item with nothing to choose offers Open in Needs review', async ({ page, request }) => {
    await seed(request, { items: 0, limited: true });
    await inspect(page, 'limited.png');
    await expect(choice(page)).toContainText('None of the possible prompts can be applied here. Decide this image in Needs review.');
    await expect(context(page)).toBeHidden();
    await expect(choice(page).getByRole('button', { name: 'Use this prompt', exact: true })).toBeHidden();
    await choice(page).getByRole('button', { name: 'Open in Needs review', exact: true }).click();
    await expect(page).toHaveURL(/#\/review$/);
    await expect(page.locator('#workspace-main .notice h2')).toHaveText('1 image needs a decision');
});

test('the viewer details show the same Choose prompt', async ({ page, request }) => {
    await seed(request, { items: 1 });
    await page.goto(images);
    const card = page.locator('article[data-image-key]').filter({ hasText: 'review_0000.png' });
    await card.getByRole('button', { name: 'Open review_0000.png', exact: true }).click();
    const viewer = page.locator('#viewer-dialog');
    await expect(viewer).toBeVisible();
    const section = viewer.getByRole('region', { name: 'Choose prompt', exact: true });
    await expect(section.getByRole('combobox', { name: 'Sampler context', exact: true }).locator('option')).toHaveText(OPTIONS);
    // Open in Needs review is offered only when nothing can be chosen.
    await expect(section.getByRole('button', { name: 'Open in Needs review', exact: true })).toBeHidden();
});

test('#657 D3: Choose prompt says when the file\'s prompts were shortened to the most used', async ({ page, request }) => {
    await seed(request, { items: 1 });
    await page.route(url => /\/prompt_manager\/review\/items\/[0-9a-f-]{36}$/.test(url.pathname), async route => {
        const response = await route.fetch();
        await route.fulfill({ response, json: { ...(await response.json()), collapsed: { shown: 2, total: 81, paths: 165 } } });
    });
    await inspect(page, 'review_0000.png');
    await expect(context(page).locator('option')).toHaveText(OPTIONS);
    await expect(choice(page).locator('.review-collapsed')).toHaveText('Showing the 2 most used of 81 prompts in this file.');
});
