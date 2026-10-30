// Needs review (#249 PR F, plan Tasks F1 and F2, Mock inventory M4): the SAVED VIEWS row and its count, the Metadata
// review page over the real review routes, and Activity's Review metadata. Items are recorded by the real recorder
// (tests/browser/workspace_review_fixture.py) through the scan's recorder (only scans add items); the conflict item's file was linked
// to another prompt after it was queued. An open page re-reads its queue from the review change stream (F2).
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
const OPTIONS = ['Sampler 11 · Golden dunes at dusk / Soft negative, blur', 'Sampler 13 · Harbor lights at night / Soft negative, blur'];

const leaveUnresolved = page => panel(page).getByRole('button', { name: 'Leave unresolved', exact: true });
const linkButton = page => panel(page).getByRole('button', { name: 'Link to a prompt…', exact: true });
const linkDialog = page => page.getByRole('dialog', { name: 'Link to a prompt', exact: true });

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
/**
 * Wait until the server has dropped every stream a page closed. It notices a closed stream only when a write to it
 * fails, and the close can reach it after any one write (WebKit under load): each poll sends one more change notice,
 * which changes nothing in the queue, until the count is 0.
 */
async function closedStreamsDropped(request) {
    await expect.poll(async () => {
        await expect(await request.post('/__test__/review/notify')).toBeOK();
        return (await streams(request)).streams;
    }).toBe(0);
}
const apply = page => panel(page).getByRole('button', { name: 'Apply association', exact: true });
const warning = page => panel(page).locator('.warning-note');
/** The page with its first item's sampler paths loaded. */
async function open(page) {
    await page.goto(review);
    await expect(leaveUnresolved(page)).not.toHaveAttribute('aria-disabled', 'true');
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
    // #536: the page stays and the next item opens.
    await expect(page).toHaveURL(/#\/review$/);
    await expect(panel(page).locator('p.small')).toHaveText('review_0001.png');
    await expect(reviewRow(page)).toHaveText('Needs review1');
    expect(await pending(request)).toBe(1);
    // The first sampler path's pair is linked to that file, and only to it.
    expect(await links(request)).toEqual([{ filename: 'review_0000.png', prompt: 'Golden dunes at dusk',
        pair_id: expect.any(Number), positive: 'Golden dunes at dusk', negative: 'Soft negative, blur' }]);
});

test('conflict leaves links unchanged and explains', async ({ page, request }) => {
    // Queued first, then linked to another prompt (before a scan or monitor pass could settle it).
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
    await leaveUnresolved(page).click();
    await expect(toast(page)).toHaveText('Left unresolved.');
    expect(await links(request)).toEqual(before);
    expect(await pending(request)).toBe(0);
});

test('a limited item can be linked or left unresolved; an unavailable file only left unresolved', async ({ page, request }) => {
    await seed(request, 0, { limited: true, unavailable: true });
    await open(page);
    await expect(cards(page)).toHaveCount(2);
    await expect(panel(page).locator('p.small')).toHaveText('limited.png');
    await expect(context(page)).toBeHidden();
    await expect(warning(page)).toHaveText('This file has too many sampler paths to list. Link it to a prompt or leave it unresolved.');
    await expect(linkButton(page)).toHaveClass(/ca-primary/);
    // The unavailable file: no current root lists it, so its media and Tag are inert.
    const unavailable = cards(page).nth(1);
    await expect(unavailable.locator('.image-open')).toHaveAttribute('aria-disabled', 'true');
    await expect(unavailable.locator('.image-card-actions button').last()).toHaveAttribute('aria-disabled', 'true');
    // F3: its empty media button keeps the concept's 1.33 media area, so the card lines up with its neighbours.
    await expect(cards(page).first().locator('.image-open img')).toBeVisible();
    const sizes = await cards(page).evaluateAll(nodes => nodes.map(node => {
        const art = node.querySelector('.image-open').getBoundingClientRect();
        return [Math.round(art.width), Math.round(art.height), Math.round(node.getBoundingClientRect().height)];
    }));
    expect(sizes[1]).toEqual(sizes[0]);
    await unavailable.getByRole('button', { name: 'Inspect unavailable.png', exact: true }).click();
    await expect(panel(page).locator('p.small')).toHaveText('unavailable.png');
    await expect(warning(page)).toHaveText('This file is not under a current image source, so it cannot be opened. You can leave it unresolved.');
    await expect(panel(page).getByRole('button', { name: 'Tag image', exact: true })).toHaveAttribute('aria-disabled', 'true');
    const observes = [];
    page.on('request', item => { if (item.url().endsWith('/image-annotations/observe')) observes.push(item.url()); });
    await expect(linkButton(page)).toHaveAttribute('aria-disabled', 'true');
    await leaveUnresolved(page).click();
    await expect(toast(page)).toHaveText('Left unresolved.');
    expect(observes).toEqual([]);
    expect(await pending(request)).toBe(1);
});

test('leave unresolved removes the item from pending', async ({ page, request }) => {
    await seed(request, 2);
    await open(page);
    await leaveUnresolved(page).click();
    await expect(toast(page)).toHaveText('Left unresolved.');
    await expect(page).toHaveURL(/#\/review$/);
    await expect(panel(page).locator('p.small')).toHaveText('review_0001.png');
    expect(await pending(request)).toBe(1);
});

test('leave for later writes nothing and opens the next item', async ({ page, request }) => {
    await seed(request, 2);
    await open(page);
    const writes = [];
    page.on('request', item => { if (item.method() !== 'GET') writes.push(item.url()); });
    await panel(page).getByRole('button', { name: 'Leave for later', exact: true }).click();
    await expect(panel(page).locator('p.small')).toHaveText('review_0001.png');
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

test('#515: tags typed before the image observation answers are saved', async ({ page, request }) => {
    await seed(request, 1);
    await open(page);
    // Hold the dialog's observation so the typing lands while it is pending (a slow host or WebKit in CI).
    let release;
    const held = new Promise(resolve => { release = resolve; });
    const matcher = '**/prompt_manager/image-annotations/observe';
    let reachedObserve;
    const reached = new Promise(resolve => { reachedObserve = resolve; });
    await page.route(matcher, async route => { reachedObserve(); await held; await route.continue(); });
    await panel(page).getByRole('button', { name: 'Tag image', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Tag image', exact: true });
    await reached;
    const field = dialog.getByRole('textbox', { name: 'Image tags · edit manually', exact: true });
    await field.fill('dunes');
    const save = dialog.getByRole('button', { name: 'Save manual tags', exact: true });
    await expect(save).toHaveAttribute('aria-disabled', 'true');
    const answered = page.waitForResponse(item => item.url().endsWith('/image-annotations/observe'));
    release();
    await answered;
    await expect(save).not.toHaveAttribute('aria-disabled', 'true');
    await expect(field).toHaveValue('dunes');
    await save.click();
    await expect(toast(page)).toHaveText('1 tags saved for review_0000.png. Prompt association still awaits review.');
    await page.unroute(matcher);
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
    expect(order.slice(-3)).toEqual(['Leave for later', 'Leave unresolved', 'Apply association']);
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
    // Enter on Leave for later stays on the page; the only item stays open.
    await panel(page).getByRole('button', { name: 'Leave for later', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/#\/review$/);
});

test('an open page shows items a scan adds and keeps the open choice', async ({ page, request }) => {
    await seed(request, 1);
    await open(page);
    await expect.poll(async () => (await streams(request)).streams).toBe(1);
    await context(page).selectOption({ label: OPTIONS[1] });
    await expect(reviewRow(page)).toHaveText('Needs review1');
    await expect(await request.post('/__test__/review/add', { data: { kind: 'scan' } })).toBeOK();
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
    await leaveUnresolved(other).click();
    await expect(toast(other)).toHaveText('Left unresolved.');
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
    await expect(await request.post('/__test__/review/add', { data: { kind: 'scan' } })).toBeOK();
    await expect(cards(page)).toHaveCount(2);
    await expect(reviewRow(page)).toHaveText('Needs review2');
    // The page's first read and the change's re-read; the shell's count read at start, and the row follows the page.
    expect(reads.filter(search => search === '?limit=40')).toHaveLength(2);
    expect(reads.filter(search => search === '?limit=1')).toHaveLength(1);
});

test('a ready whose version cannot be read re-reads the queue once', async ({ page, request }) => {
    // The stream's ready names no readable version (an older server, a proxy that rewrote it): the page reads again.
    await seed(request, 1);
    const reads = reviewReads(page);
    // M1: `retry` keeps the browser from reconnecting the ended stream during the test, so exactly one re-read follows.
    await page.route(url => url.pathname === '/prompt_manager/review/events', route => route.fulfill({
        status: 200, headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store' },
        body: 'retry: 3600000\nevent: ready\ndata: not json\n\n' }));
    let reread = 0;
    const second = page.waitForResponse(item => item.url().endsWith('/prompt_manager/review/items?limit=40') && ++reread === 2);
    await open(page);
    await second;
    await expect(cards(page)).toHaveCount(1);
    expect(reads.filter(search => search === '?limit=40')).toHaveLength(2);
});

const live = page => main(page).locator('.heading .review-live');
const NOT_LIVE = 'Live updates are paused: changes made elsewhere appear once the connection returns.';

test('a stream the browser gives up on is opened again, and the page says it is not live meanwhile', async ({ page, request }) => {
    // I3: a 503 (shutdown, or a proxy while the backend restarts) fails an EventSource for good.
    await seed(request, 1);
    let attempts = 0;
    await page.route(url => url.pathname === '/prompt_manager/review/events', route => {
        attempts += 1;
        if (attempts === 1) return route.fulfill({ status: 503, contentType: 'application/json', body: '{"success":false}' });
        return route.fallback();
    });
    await open(page);
    await expect(live(page)).toHaveText(NOT_LIVE);
    await expect(live(page)).toBeVisible();
    // The page opens the stream again (after 3 s) and is live once it sends ready.
    await expect.poll(async () => (await streams(request)).streams, { timeout: 15000 }).toBe(1);
    await expect(live(page)).toBeHidden();
    await expect(await request.post('/__test__/review/add', { data: { kind: 'scan' } })).toBeOK();
    await expect(cards(page)).toHaveCount(2);
    expect(attempts).toBe(2);
});

/** Make the page hidden or visible as a tab switch does. */
async function setVisible(page, visible) {
    await page.evaluate(shown => {
        Object.defineProperty(document, 'hidden', { configurable: true, get: () => !shown });
        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => (shown ? 'visible' : 'hidden') });
        document.dispatchEvent(new Event('visibilitychange'));
    }, visible);
}

test('a hidden tab closes its stream and a visible one reads only what changed meanwhile', async ({ page, request }) => {
    // I4: background tabs must not hold one of the origin's six connections.
    await seed(request, 1);
    const reads = reviewReads(page);
    await open(page);
    await expect.poll(async () => (await streams(request)).streams).toBe(1);
    await setVisible(page, false);
    await expect(await request.post('/__test__/review/add', { data: { kind: 'scan' } })).toBeOK();
    await closedStreamsDropped(request);
    await expect(cards(page)).toHaveCount(1);
    expect(reads.filter(search => search === '?limit=40')).toHaveLength(1);
    // Visible again: the reopened stream's ready names a newer version, so the page reads once.
    const reread = page.waitForResponse(item => item.url().endsWith('/prompt_manager/review/items?limit=40'));
    await setVisible(page, true);
    await expect.poll(async () => (await streams(request)).streams).toBe(1);
    await reread;
    await expect(cards(page)).toHaveCount(2);
    await expect(live(page)).toBeHidden();
    expect(reads.filter(search => search === '?limit=40')).toHaveLength(2);
});

test('a stream whose close reaches the server after a change is dropped at a later write', async ({ page, request }) => {
    // The CI ordering made certain: the hidden page's close arrives only after the scan addition's change was
    // written to its stream, so that write succeeds and a later one finds the stream closed.
    await seed(request, 1);
    await open(page);
    await expect.poll(async () => (await streams(request)).streams).toBe(1);
    await page.evaluate(() => {
        const close = EventSource.prototype.close;
        window.lateChanges = 0;
        window.heldCloses = [];
        EventSource.prototype.close = function () {
            this.addEventListener('change', () => { window.lateChanges += 1; });
            window.heldCloses.push(() => close.call(this));
        };
    });
    await setVisible(page, false);
    await expect(await request.post('/__test__/review/add', { data: { kind: 'scan' } })).toBeOK();
    await expect.poll(() => page.evaluate(() => window.lateChanges)).toBe(1);
    await page.evaluate(() => window.heldCloses.splice(0).forEach(close => close()));
    expect((await streams(request)).streams).toBe(1);
    await closedStreamsDropped(request);
    await expect(cards(page)).toHaveCount(1);
});

test('a re-read refreshes a card whose media or target changed', async ({ page, request }) => {
    // M6 and M10: a root came or went between reads; the card follows, and a known thumbnail is its media.
    await seed(request, 2);
    let rewrite = false;
    await page.route(url => url.pathname === '/prompt_manager/review/items' && url.search === '?limit=40', async route => {
        if (!rewrite) return route.fallback();
        const response = await route.fetch();
        const body = await response.json();
        body.items[0] = { ...body.items[0], thumbnail_url: `${body.items[0].url}&thumb=1` };
        body.items[1] = { ...body.items[1], url: null, thumbnail_url: null, target: null, unavailable: true };
        return route.fulfill({ response, json: body });
    });
    await open(page);
    await expect.poll(async () => (await streams(request)).streams).toBe(1);
    const second = cards(page).nth(1);
    await expect(second.getByRole('button', { name: 'Open review_0001.png', exact: true })).not.toHaveAttribute('aria-disabled', 'true');
    rewrite = true;
    await expect(await request.post('/__test__/review/add', { data: { kind: 'scan' } })).toBeOK();
    await expect(cards(page)).toHaveCount(3);
    await expect(cards(page).first().locator('img')).toHaveAttribute('src', /&thumb=1$/);
    await expect(second.getByRole('button', { name: 'Open review_0001.png', exact: true })).toHaveAttribute('aria-disabled', 'true');
    await expect(second.locator('img')).toHaveCount(0);
    await expect(second.locator('.image-card-actions button').last()).toHaveAttribute('aria-disabled', 'true');
    await expect(cards(page).first()).toHaveClass(/selected/);
});

test('leaving during the observe releases the capture it answers', async ({ page, request }) => {
    // M4: the server made the capture; the page left before the answer came.
    await seed(request, 1);
    await open(page);
    let held;
    const sent = new Promise(resolve => { held = resolve; });
    await page.route(url => url.pathname === '/prompt_manager/image-annotations/observe', async route => {
        const response = await route.fetch();
        held(() => route.fulfill({ response }));
    });
    await apply(page).click();
    const answer = await sent;
    expect((await streams(request)).captures).toBe(1);
    await page.evaluate(() => { window.location.hash = '#/tags'; });
    await expect(main(page).getByRole('heading', { level: 1 })).toHaveText('Tags & categories');
    await answer();
    await expect.poll(async () => (await streams(request)).captures).toBe(0);
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
    await expect(await request.post('/__test__/review/add', { data: { kind: 'scan' } })).toBeOK();
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
    await closedStreamsDropped(request);
    await expect.poll(() => inFlight.size).toBe(0);
});

test('#536: nothing selectable says why and links through the picker, then the next item opens', async ({ page, request }) => {
    await seed(request, 1, { unreadable: true });
    await open(page);
    await cards(page).nth(1).getByRole('button', { name: 'Inspect unreadable.png', exact: true }).click();
    await expect(panel(page)).toContainText("None of this file's 2 sampler paths could be traced to a complete prompt");
    await expect(panel(page)).toContainText('Unsupported latent ancestry (2 paths)');
    await expect(context(page)).toBeHidden();
    await expect(panel(page).getByRole('button', { name: 'Apply association', exact: true })).toBeHidden();
    await expect(panel(page)).not.toContainText('Multiple possible prompts');
    await expect(linkButton(page)).toHaveClass(/ca-primary/);

    await linkButton(page).click();
    const dialog = linkDialog(page);
    await expect(dialog).toContainText("This records your choice; the file's metadata does not confirm it.");
    const submit = dialog.getByRole('button', { name: 'Link image', exact: true });
    await expect(submit).toHaveAttribute('aria-disabled', 'true');
    await dialog.getByRole('searchbox').fill('harbor');
    const harbor = dialog.getByRole('button', { name: 'Quiet harbor', exact: true });
    await expect(harbor).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Golden dunes at dusk', exact: true })).toHaveCount(0);
    await harbor.click();
    // Several recipes: the reviewer must choose.
    const negative = dialog.getByRole('combobox', { name: 'Negative prompt', exact: true });
    await expect(negative.locator('option')).toHaveText(['Choose a negative…', 'washed out', 'grainy', 'No negative']);
    await expect(submit).toHaveAttribute('aria-disabled', 'true');
    await expect(submit).toHaveCSS('opacity', '0.55');
    await expect(harbor).toHaveAttribute('aria-pressed', 'true');
    await negative.selectOption({ label: 'grainy' });
    const linked = page.waitForResponse(item => /\/link$/.test(item.url()));
    await submit.click();
    expect((await linked).status()).toBe(200);
    await expect(toast(page)).toHaveText('Linked to Quiet harbor.');
    await expect(dialog).toHaveCount(0);
    await expect(page).toHaveURL(/#\/review$/);
    await expect(panel(page).locator('p.small')).toHaveText('review_0000.png');
    expect(await pending(request)).toBe(1);
    const link = (await links(request)).find(row => row.filename === 'unreadable.png');
    expect([link.positive, link.negative]).toEqual(['Quiet harbor', 'grainy']);
});

test('#536: a refused link stays in the dialog with the server\'s sentence', async ({ page, request }) => {
    await seed(request, 0, { unreadable: true });
    await open(page);
    await linkButton(page).click();
    const dialog = linkDialog(page);
    await dialog.getByRole('button', { name: 'Golden dunes at dusk', exact: true }).click();
    // One recipe: preselected and visible.
    const negative = dialog.getByRole('combobox', { name: 'Negative prompt', exact: true });
    await expect(negative).toHaveValue(/^[0-9]+$/);
    await page.route(/\/link$/, route => route.fulfill({ status: 409, contentType: 'application/json',
        body: JSON.stringify({ success: false, code: 'association-conflict', error: 'This image is already linked.' }) }));
    await dialog.getByRole('button', { name: 'Link image', exact: true }).click();
    await expect(dialog.locator('.warning-note')).toHaveText('This image is already linked.');
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    expect(await pending(request)).toBe(1);
    expect(await links(request)).toEqual([]);
});
