// Needs review (#249 PR F, plan Task F1, Mock inventory M4): the SAVED VIEWS row and its count, the Metadata review
// page over the real review routes, and Activity's Review metadata. Items are recorded by the real recorder
// (tests/browser/workspace_review_fixture.py), as the live monitor records a new output.
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';

const review = '/prompt_manager/workspace#/review';
const main = page => page.locator('#workspace-main');
const savedNav = page => page.getByRole('navigation', { name: 'Saved views', exact: true });
const reviewRow = page => savedNav(page).locator('a[data-route="review"]');
const panel = page => main(page).getByRole('region', { name: 'Resolve the association', exact: true });
const context = page => panel(page).getByRole('combobox', { name: 'Sampler context', exact: true });
const cards = page => main(page).locator('.review-queue article.media-card');
const toast = page => page.locator('#workspace-toast');
const NOTICE = 'Multiple sampler paths were found. The existing image links were preserved. Choose the correct context or leave them unresolved.';
const EMPTY = 'Ambiguous images are added when a metadata scan or a new generation finds more than one sampler path.';
const OPTIONS = ['Sampler 11 · Golden dunes at dusk / Soft negative, blur', 'Sampler 13 · Harbor lights at night / Soft negative, blur',
    'Leave unresolved'];

async function seed(request, items = 3) {
    const response = await request.post('/__test__/review/reset', { data: { items } });
    await expect(response).toBeOK();
    return response.json();
}
async function pending(request) {
    return (await (await request.get('/prompt_manager/review/items?limit=1')).json()).total;
}
/** The page with its first item's sampler paths loaded. */
async function open(page) {
    await page.goto(review);
    await expect(context(page)).toBeEnabled();
}

test.beforeEach(async ({ page, browserName }) => {
    await startCoverage(page, browserName);
    page.reviewErrors = [];
    page.on('pageerror', error => page.reviewErrors.push(error.message));
});
test.afterEach(async ({ page, browserName, request }) => {
    await saveCoverage(page, browserName);
    expect(page.reviewErrors).toEqual([]);
    await request.post('/__test__/jobs/restart');
});

test('queue shows persisted items and the nav count', async ({ page, request }) => {
    await seed(request, 3);
    await open(page);
    // SAVED VIEWS: Favorites, then Needs review with the exact pending count (ruling R5).
    await expect(savedNav(page).locator('a').nth(1)).toHaveAttribute('data-route', 'review');
    await expect(reviewRow(page)).toHaveText('Needs review3');
    await expect(reviewRow(page)).toHaveAttribute('aria-current', 'page');
    await expect(page).toHaveTitle('Review · Prompt Manager');
    await expect(page.locator('#workspace-breadcrumb')).toHaveText('Review');
    await expect(main(page).getByRole('heading', { level: 1 })).toHaveText('Metadata review');
    await expect(main(page).locator('.heading p.muted')).toHaveText('Uncertain evidence stays visible until you decide.');
    await expect(main(page).locator('.notice h2')).toHaveText('3 images need a decision');
    await expect(main(page).locator('.notice p')).toHaveText(NOTICE);
    await expect(cards(page)).toHaveCount(3);
    await expect(cards(page).first()).toHaveClass(/selected/);
    await expect(panel(page).locator('p.small')).toHaveText('review_0000.png');
    await expect(context(page).locator('option')).toHaveText(OPTIONS);
    await expect(context(page)).toHaveValue(/^[0-9a-f]{64}$/);
    await expect(panel(page).locator('.prompt-section.positive .prompt-text')).toHaveText('Golden dunes at dusk');
    await expect(panel(page).locator('.prompt-section.negative .prompt-text')).toHaveText('Soft negative, blur');
    await context(page).selectOption({ label: OPTIONS[1] });
    await expect(panel(page).locator('.prompt-section.positive .prompt-text')).toHaveText('Harbor lights at night');
    await context(page).selectOption({ label: 'Leave unresolved' });
    await expect(panel(page).locator('.prompt-section.positive .prompt-text')).toHaveText('Multiple possible prompts. Source could not be determined.');
    // Info selects another item.
    await cards(page).nth(1).getByRole('button', { name: 'Inspect review_0001.png', exact: true }).click();
    await expect(panel(page).locator('p.small')).toHaveText('review_0001.png');
    await expect(cards(page).nth(1)).toHaveClass(/selected/);
    await expect(context(page)).toBeEnabled();
    // The card's image opens the shared viewer.
    await cards(page).nth(1).getByRole('button', { name: 'Open review_0001.png', exact: true }).click();
    await expect(page.locator('#viewer-dialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#viewer-dialog')).toHaveCount(0);
    // Open activity.
    await main(page).getByRole('button', { name: 'Open activity', exact: true }).click();
    await expect(page).toHaveURL(/#\/activity$/);
});

test('apply candidate links the pair and the item leaves the queue and the count drops', async ({ page, request }) => {
    await seed(request, 2);
    await open(page);
    await expect(reviewRow(page)).toHaveText('Needs review2');
    const decided = page.waitForResponse(item => /\/review\/items\/[^/]+\/decision$/.test(item.url()));
    await panel(page).getByRole('button', { name: 'Apply association', exact: true }).click();
    expect((await decided).status()).toBe(200);
    await expect(toast(page)).toHaveText('Association accepted.');
    await expect(page).toHaveURL(/#\/images$/);
    await expect(reviewRow(page)).toHaveText('Needs review1');
    expect(await pending(request)).toBe(1);
});

test('leave unresolved removes the item from pending', async ({ page, request }) => {
    await seed(request, 2);
    await open(page);
    await context(page).selectOption({ label: 'Leave unresolved' });
    await panel(page).getByRole('button', { name: 'Apply association', exact: true }).click();
    await expect(toast(page)).toHaveText('Left unresolved.');
    await expect(page).toHaveURL(/#\/images$/);
    expect(await pending(request)).toBe(1);
});

test('leave for later writes nothing and goes to Images', async ({ page, request }) => {
    await seed(request, 2);
    await open(page);
    const writes = [];
    page.on('request', item => { if (item.method() !== 'GET') writes.push(item.url()); });
    await panel(page).getByRole('button', { name: 'Leave for later', exact: true }).click();
    await expect(page).toHaveURL(/#\/images$/);
    expect(writes).toEqual([]);
    expect(await pending(request)).toBe(2);
});

test('tag image from review opens the shared dialog with the scope note', async ({ page, request }) => {
    await seed(request, 1);
    await open(page);
    await panel(page).getByRole('button', { name: 'Tag image', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Tag image', exact: true });
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('p.tag-scope-note')).toHaveText('Tags belong to this image. Resolve its prompt association separately in Needs review.');
    await dialog.getByRole('textbox', { name: 'Image tags · edit manually', exact: true }).fill('dunes');
    await dialog.getByRole('button', { name: 'Save manual tags', exact: true }).click();
    await expect(toast(page)).toHaveText('1 tags saved for review_0000.png. Prompt association still awaits review.');
    await expect(dialog).toHaveCount(0);
    expect(await pending(request)).toBe(1);
    await expect(page).toHaveURL(/#\/review$/);
});

test('an empty queue shows the empty notice only', async ({ page, request }) => {
    await seed(request, 0);
    await page.goto(review);
    await expect(main(page).locator('.notice h2')).toHaveText('No images need a decision');
    await expect(main(page).locator('.notice p')).toHaveText(EMPTY);
    await expect(main(page).locator('.gallery-layout')).toHaveCount(0);
    await expect(reviewRow(page)).toHaveText('Needs review0');
});

test('load more appends the next page', async ({ page, request }) => {
    await seed(request, 45);
    await open(page);
    await expect(cards(page)).toHaveCount(40);
    const continuation = main(page).locator('.infinite-continuation');
    await expect(continuation.locator('p')).toHaveText('40 of 45 images');
    await continuation.getByRole('button', { name: 'Load more images', exact: true }).click();
    await expect(cards(page)).toHaveCount(45);
    await expect(continuation).toHaveCount(0);
});

test('a scan job with review items offers Review metadata in Activity', async ({ page, request }) => {
    await seed(request, 1);
    const started = await (await request.post('/__test__/jobs/start', { data: { total: 10, review_items: 2 } })).json();
    await expect(await request.post(`/__test__/jobs/${started.job.id}/finish`, { data: {} })).toBeOK();
    const plain = await (await request.post('/__test__/jobs/start', { data: { total: 10 } })).json();
    await expect(await request.post(`/__test__/jobs/${plain.job.id}/finish`, { data: {} })).toBeOK();
    await page.goto('/prompt_manager/workspace#/activity');
    const item = main(page).locator(`.activity-item[data-job-id="${started.job.id}"]`);
    await expect(item.getByRole('button', { name: 'Review metadata', exact: true })).toBeVisible();
    await expect(main(page).locator(`.activity-item[data-job-id="${plain.job.id}"] button`)).toHaveCount(0);
    await page.goto(`/prompt_manager/workspace#/activity/${started.job.id}`);
    const actions = main(page).locator('.heading-actions button');
    await expect(actions).toHaveText(['Download report', 'Review metadata']);
    await actions.nth(1).click();
    await expect(page).toHaveURL(/#\/review$/);
    await expect(main(page).getByRole('heading', { level: 1 })).toHaveText('Metadata review');
});

test('a failed item read says so and Retry reads it again', async ({ page, request }) => {
    await seed(request, 1);
    await page.route(url => /\/prompt_manager\/review\/items\/[0-9a-f-]{36}$/.test(url.pathname),
        route => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ success: false, code: 'review-failed', error: 'x' }) }),
        { times: 1 });
    await page.goto(review);
    await expect(panel(page).locator('.warning-note')).toHaveText('This item could not be read. Select it again to retry.');
    await expect(panel(page).getByText('Could not read prompts from this file.', { exact: true })).toBeVisible();
    await expect(panel(page).getByRole('button', { name: 'Apply association', exact: true })).toHaveAttribute('aria-disabled', 'true');
    await panel(page).getByRole('button', { name: 'Retry prompt details', exact: true }).click();
    await expect(context(page)).toBeEnabled();
    await expect(context(page).locator('option')).toHaveText(OPTIONS);
    await expect(panel(page).locator('.warning-note')).toBeHidden();
});

test('a failed queue read offers Retry', async ({ page, request }) => {
    await seed(request, 1);
    await page.route(url => url.pathname.endsWith('/prompt_manager/review/items') && url.searchParams.get('limit') === '40',
        route => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ success: false, code: 'review-failed', error: 'x' }) }),
        { times: 1 });
    await page.goto(review);
    await expect(main(page).getByRole('alert')).toHaveText('The review queue could not be read.');
    await main(page).getByRole('button', { name: 'Retry', exact: true }).click();
    await expect(context(page)).toBeEnabled();
    await expect(main(page).locator('.notice h2')).toHaveText('1 image needs a decision');
});

test('an association conflict keeps the selection and explains', async ({ page, request }) => {
    await seed(request, 1);
    await open(page);
    await page.route(url => /\/decision$/.test(url.pathname), route => route.fulfill({ status: 409, contentType: 'application/json',
        body: JSON.stringify({ success: false, code: 'association-conflict', error: 'This image is already linked to another prompt or negative, so this sampler path cannot be attached safely. The existing links were preserved.' }) }),
        { times: 1 });
    await panel(page).getByRole('button', { name: 'Apply association', exact: true }).click();
    await expect(panel(page).locator('.warning-note')).toHaveText(/already linked to another prompt/);
    await expect(page).toHaveURL(/#\/review$/);
    await expect(context(page)).toBeEnabled();
    expect(await pending(request)).toBe(1);
});
