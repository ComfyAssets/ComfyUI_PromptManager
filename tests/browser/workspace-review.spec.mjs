// Needs review (#249 PR F, plan Tasks F1 and F2, Mock inventory M4): the SAVED VIEWS row and its count, the Metadata
// review page over the real review routes, and Activity's Review metadata. Items are recorded by the real recorder
// (tests/browser/workspace_review_fixture.py), as the live monitor records a new output; the conflict item is a real
// monitor fallback link. An open page re-reads its queue from the review change stream (F2).
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

async function seed(request, items = 3, special = {}) {
    const response = await request.post('/__test__/review/reset', { data: { items, ...special } });
    await expect(response).toBeOK();
    return response.json();
}
async function pending(request) {
    return (await (await request.get('/prompt_manager/review/items?limit=1')).json()).total;
}
const links = async request => (await (await request.get('/__test__/review/links')).json()).links;
const streams = async request => (await (await request.get('/__test__/review/streams')).json());
const apply = page => panel(page).getByRole('button', { name: 'Apply association', exact: true });
const warning = page => panel(page).locator('.warning-note');
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
    // The first sampler path's pair is linked to that file, and only to it.
    expect(await links(request)).toEqual([{ filename: 'review_0000.png', prompt: 'Golden dunes at dusk',
        pair_id: expect.any(Number), positive: 'Golden dunes at dusk', negative: 'Soft negative, blur' }]);
});

test('conflict leaves links unchanged and explains', async ({ page, request }) => {
    // A real monitor fallback: the new output is linked to the most recent prompt, then queued.
    await seed(request, 0, { conflict: true });
    const before = await links(request);
    expect(before).toEqual([{ filename: 'conflict.png', prompt: 'Misty forest path', pair_id: null, positive: null,
        negative: null }]);
    await open(page);
    await expect(panel(page).locator('p.small')).toHaveText('conflict.png');
    await expect(context(page).locator('option')).toHaveText(OPTIONS);
    const decided = page.waitForResponse(item => /\/decision$/.test(item.url()));
    await apply(page).click();
    const answer = await decided;
    expect(answer.status()).toBe(409);
    expect((await answer.json()).code).toBe('association-conflict');
    await expect(warning(page)).toHaveText('This image is already linked to another prompt or negative, so this sampler path cannot be attached safely. The existing links were preserved.');
    await expect(page).toHaveURL(/#\/review$/);
    await expect(context(page)).toBeEnabled();
    await expect(context(page)).toHaveValue(/^[0-9a-f]{64}$/);
    expect(await links(request)).toEqual(before);
    expect(await pending(request)).toBe(1);
    await expect(reviewRow(page)).toHaveText('Needs review1');
    // The file can still be left unresolved, which links nothing either.
    await context(page).selectOption({ label: 'Leave unresolved' });
    await apply(page).click();
    await expect(toast(page)).toHaveText('Left unresolved.');
    expect(await links(request)).toEqual(before);
    expect(await pending(request)).toBe(0);
});

test('a limited item and an unavailable file can only be left unresolved', async ({ page, request }) => {
    await seed(request, 0, { limited: true, unavailable: true });
    await open(page);
    await expect(cards(page)).toHaveCount(2);
    await expect(panel(page).locator('p.small')).toHaveText('limited.png');
    await expect(context(page).locator('option')).toHaveText(['Leave unresolved']);
    await expect(warning(page)).toHaveText('This file has too many sampler paths to list. You can leave it unresolved.');
    // The unavailable file: no current root lists it, so its media and Tag are inert.
    const unavailable = cards(page).nth(1);
    await expect(unavailable.locator('.image-open')).toHaveAttribute('aria-disabled', 'true');
    await expect(unavailable.locator('.image-card-actions button').last()).toHaveAttribute('aria-disabled', 'true');
    await unavailable.getByRole('button', { name: 'Inspect unavailable.png', exact: true }).click();
    await expect(panel(page).locator('p.small')).toHaveText('unavailable.png');
    await expect(warning(page)).toHaveText('This file is not under a current image source, so it cannot be opened. You can leave it unresolved.');
    await expect(panel(page).getByRole('button', { name: 'Tag image', exact: true })).toHaveAttribute('aria-disabled', 'true');
    const observes = [];
    page.on('request', item => { if (item.url().endsWith('/image-annotations/observe')) observes.push(item.url()); });
    await apply(page).click();
    await expect(toast(page)).toHaveText('Left unresolved.');
    expect(observes).toEqual([]);
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

test('tag image from review saves tags and the item remains pending', async ({ page, request }) => {
    await seed(request, 1);
    await open(page);
    const before = await links(request);
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
    // The item is untouched: still listed, still open, nothing linked.
    await expect(cards(page)).toHaveCount(1);
    await expect(context(page)).toBeEnabled();
    expect(await links(request)).toEqual(before);
    const [item] = (await (await request.get('/prompt_manager/review/items')).json()).items;
    expect([item.state, item.revision]).toEqual(['pending', 0]);
    // The card's Tag opens the same dialog.
    await cards(page).first().getByRole('button', { name: 'Tag or auto-tag review_0000.png', exact: true }).click();
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('p.tag-scope-note')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
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

test('keyboard focus labels and touch targets', async ({ page, request }) => {
    await seed(request, 2);
    await open(page);
    const first = cards(page).first();
    for (const [name, role] of [['Open review_0000.png', 'button'], ['Inspect review_0000.png', 'button'],
        ['Tag or auto-tag review_0000.png', 'button']]) await expect(first.getByRole(role, { name, exact: true })).toBeVisible();
    await expect(main(page).getByRole('list', { name: 'Review queue', exact: true })).toBeVisible();
    await expect(panel(page)).toBeVisible();
    // Keyboard: Info on the second card opens it, and the panel's controls take focus in their order.
    const info = cards(page).nth(1).getByRole('button', { name: 'Inspect review_0001.png', exact: true });
    await info.focus();
    await page.keyboard.press('Enter');
    await expect(panel(page).locator('p.small')).toHaveText('review_0001.png');
    await expect(context(page)).toBeEnabled();
    await context(page).focus();
    await expect(context(page)).toBeFocused();
    const order = await panel(page).evaluate(node => [...node.querySelectorAll('button, select')]
        .filter(control => control.offsetParent !== null).map(control => control.getAttribute('aria-label') || control.textContent.trim()));
    expect(order[0]).toBe('Tag image');
    expect(order.slice(-2)).toEqual(['Leave for later', 'Apply association']);
    // Targets: the panel's controls are 40px on a coarse pointer (the concept's coarse buttons) and 24px otherwise (the
    // WCAG 2.2 minimum); the card's buttons keep the concept's compact size, never under 24px (its ≤700px rule).
    const coarse = await page.evaluate(() => matchMedia('(pointer: coarse)').matches);
    const sized = [
        [panel(page).getByRole('button', { name: 'Tag image', exact: true }), coarse ? 40 : 24],
        [context(page), coarse ? 40 : 24],
        [panel(page).getByRole('button', { name: 'Leave for later', exact: true }), coarse ? 40 : 24],
        [apply(page), coarse ? 40 : 24],
        [first.getByRole('button', { name: 'Inspect review_0000.png', exact: true }), 24],
        [first.getByRole('button', { name: 'Tag or auto-tag review_0000.png', exact: true }), 24],
    ];
    for (const [control, minimum] of sized) {
        const box = await control.boundingBox();
        const label = await control.evaluate(node => node.getAttribute('aria-label') || node.textContent.trim().slice(0, 40));
        expect(box.height, label).toBeGreaterThanOrEqual(minimum);
        expect(box.width, label).toBeGreaterThanOrEqual(24);
    }
    // Enter on Leave for later returns to Images.
    await panel(page).getByRole('button', { name: 'Leave for later', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/#\/images$/);
});

test('an open page shows items the monitor adds and keeps the open choice', async ({ page, request }) => {
    await seed(request, 1);
    await open(page);
    await expect.poll(async () => (await streams(request)).streams).toBe(1);
    await context(page).selectOption({ label: OPTIONS[1] });
    await expect(reviewRow(page)).toHaveText('Needs review1');
    await expect(await request.post('/__test__/review/add', { data: { kind: 'monitor' } })).toBeOK();
    await expect(cards(page)).toHaveCount(2);
    await expect(main(page).locator('.notice h2')).toHaveText('2 images need a decision');
    await expect(reviewRow(page)).toHaveText('Needs review2');
    await expect(cards(page).first()).toHaveClass(/selected/);
    await expect(panel(page).locator('p.small')).toHaveText('review_0000.png');
    await expect(context(page).locator('option:checked')).toHaveText(OPTIONS[1]);
    await expect(warning(page)).toBeHidden();
});

test('an open page shows items a metadata scan adds', async ({ page, request }) => {
    await seed(request, 1);
    await expect(await request.post('/__test__/review/add', { data: { kind: 'file' } })).toBeOK();
    await open(page);
    await expect.poll(async () => (await streams(request)).streams).toBe(1);
    await expect(cards(page)).toHaveCount(1);
    const started = await request.post('/prompt_manager/jobs/scan', { data: { scope: 'new' } });
    expect(started.status()).toBe(202);
    await expect(cards(page)).toHaveCount(2);
    await expect(cards(page).nth(1).getByRole('button', { name: 'Inspect added_0000.png', exact: true })).toBeVisible();
    await expect(reviewRow(page)).toHaveText('Needs review2');
});

test('a decision in another tab leaves the open page and opens the next image', async ({ page, request, context: browserContext }) => {
    await seed(request, 2);
    await open(page);
    await expect.poll(async () => (await streams(request)).streams).toBe(1);
    const other = await browserContext.newPage();
    await open(other);
    await context(other).selectOption({ label: 'Leave unresolved' });
    await apply(other).click();
    await expect(other).toHaveURL(/#\/images$/);
    await other.close();
    await expect(cards(page)).toHaveCount(1);
    await expect(panel(page).locator('p.small')).toHaveText('review_0001.png');
    await expect(warning(page)).toHaveText('review_0000.png was decided elsewhere, so the next image is open.');
    await expect(main(page).locator('.notice h2')).toHaveText('1 image needs a decision');
    await expect(reviewRow(page)).toHaveText('Needs review1');
    await expect(context(page)).toBeEnabled();
});

/** The queue reads (`?limit=40`, cursors) and count reads (`?limit=1`) the page sends from now on. */
function reviewReads(page) {
    const reads = [];
    page.on('request', item => {
        const url = new URL(item.url());
        if (url.pathname === '/prompt_manager/review/items') reads.push(url.searchParams.get('cursor') ? 'more' : url.search);
    });
    return reads;
}

test('a visit reads its queue once, and a change reads it once more', async ({ page, request }) => {
    // F3 ruling: the stream's ready must not read a queue the first read already holds.
    await seed(request, 1);
    const reads = reviewReads(page);
    await open(page);
    await expect.poll(async () => (await streams(request)).streams).toBe(1);
    await expect(await request.post('/__test__/review/add', { data: { kind: 'monitor' } })).toBeOK();
    await expect(cards(page)).toHaveCount(2);
    await expect(reviewRow(page)).toHaveText('Needs review2');
    // The page's first read and the change's re-read; the shell's count read at start, and the row follows the page.
    expect(reads.filter(search => search === '?limit=40')).toHaveLength(2);
    expect(reads.filter(search => search === '?limit=1')).toHaveLength(1);
});

test('a live re-read keeps the loaded depth and the scroll position', async ({ page, request }) => {
    // F3 ruling: a queue change re-reads as many items as Load more had shown, and the page does not jump.
    await seed(request, 45);
    await open(page);
    await expect.poll(async () => (await streams(request)).streams).toBe(1);
    await main(page).getByRole('button', { name: 'Load more images', exact: true }).click();
    await expect(cards(page)).toHaveCount(45);
    await cards(page).last().scrollIntoViewIfNeeded();
    const scrollTop = () => cards(page).first().evaluate(node => {
        for (let el = node.parentElement; el; el = el.parentElement) {
            const style = getComputedStyle(el);
            if (/(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight) return el.scrollTop;
        }
        return document.scrollingElement.scrollTop;
    });
    const before = await scrollTop();
    expect(before).toBeGreaterThan(0);
    const reads = reviewReads(page);
    await expect(await request.post('/__test__/review/add', { data: { kind: 'monitor' } })).toBeOK();
    await expect(main(page).locator('.notice h2')).toHaveText('46 images need a decision');
    await expect(cards(page)).toHaveCount(46);
    expect(await scrollTop()).toBe(before);
    expect(reads).toEqual(['?limit=40', 'more']);
    await expect(main(page).locator('.infinite-continuation')).toHaveCount(0);
});

test('teardown releases captures listeners and requests', async ({ page, request }) => {
    await seed(request, 1);
    await open(page);
    await expect.poll(async () => (await streams(request)).streams).toBe(1);
    const inFlight = new Set();
    page.on('request', item => { if (item.url().includes('/prompt_manager/review/')) inFlight.add(item); });
    page.on('requestfinished', item => inFlight.delete(item));
    page.on('requestfailed', item => inFlight.delete(item));
    // A decision whose answer never comes: leaving the page must still release its capture.
    let held;
    const sent = new Promise(resolve => { held = resolve; });
    await page.route(url => /\/decision$/.test(url.pathname), route => held(route));
    await apply(page).click();
    const route = await sent;
    expect((await streams(request)).captures).toBe(1);
    await page.evaluate(() => { window.location.hash = '#/tags'; });
    await expect(main(page).getByRole('heading', { level: 1 })).toHaveText('Tags & categories');
    await expect.poll(async () => (await streams(request)).captures).toBe(0);
    await route.abort().catch(() => {});
    // The server drops a closed stream at its next write: a monitor addition is one.
    await expect(await request.post('/__test__/review/add', { data: { kind: 'monitor' } })).toBeOK();
    await expect.poll(async () => (await streams(request)).streams).toBe(0);
    await expect.poll(() => inFlight.size).toBe(0);
});
