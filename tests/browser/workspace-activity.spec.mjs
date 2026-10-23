import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { seedConcept, openConcept, openProduction } from './concept-inventory.mjs';

// The Activity store's periodic list read (activity-store.mjs POLL_MS) and the detail view's read throttle
// (activity-job-view.mjs DETAIL_INTERVAL_MS).
const POLL_MS = 15_000;
const DETAIL_INTERVAL_MS = 1_000;
// A request is reported to the test a little after the browser sends it; that delay is not a shorter wait.
const REPORT_SLACK_MS = 100;

// Activity (#249 Activity-1): the concept's Activity page (app.js renderActivity 732–738), its top-bar button
// (index.html 51–52) and sidebar row (app.js ~341), with the recorded D52 (counts of running jobs), D53 (the progress
// pill) and D54 (real job items). Fake jobs come from tests/browser/workspace_activity_fixture.py.
const workspace = '/prompt_manager/workspace';
const main = page => page.locator('#workspace-main');
const pill = page => page.locator('.top-actions #activity-pill');
const activityButton = page => page.locator('.top-actions button#activity-open');
const topCount = page => page.locator('#activity-count');
const navRow = page => page.locator('#system-nav a[data-route="activity"]');
const toast = page => page.locator('#workspace-toast');
const items = page => main(page).locator('section.surface > .activity-item');
const LOST = 'This job ended when ComfyUI restarted.';
// The job view (Task 3, D56): the page heading, then the Progress and Results surfaces and the Scan log.
const heading = page => main(page).locator(':scope > .heading');
const progressSection = page => main(page).getByRole('region', { name: 'Progress', exact: true });
const resultsSection = page => main(page).getByRole('region', { name: 'Results', exact: true });
const tiles = page => progressSection(page).locator('dl.stat-row > div.stat');
const outcomeButtons = page => resultsSection(page).locator('.outcome-list > button');
const fileRows = page => resultsSection(page).locator('.outcome-files > .file-row');
const resultsNote = page => resultsSection(page).locator('.outcome-files > p.small');
const filter = page => resultsSection(page).getByRole('searchbox', { name: 'Filter results', exact: true });
const scanLog = page => main(page).locator('details.term');
const TILE_LABELS = ['Added to Library', 'Already known', 'Needs review', 'No usable metadata', 'Errors'];
const VIEWER_HINT = 'each row opens the image in the viewer';

async function tileValues(page) {
    return tiles(page).evaluateAll(nodes => nodes.map(node => [node.className, node.querySelector('dt').textContent,
        node.querySelector('dd').textContent]));
}

async function openJob(page, id) {
    await page.goto(`${workspace}#/activity/${id}`);
    await expect(progressSection(page)).toBeVisible();
}

async function startJob(request, data = {}) {
    const response = await request.post('/__test__/jobs/start', { data: { total: 19622, ...data } });
    await expect(response).toBeOK();
    return (await response.json()).job;
}

async function step(request, id, data) {
    await expect(await request.post(`/__test__/jobs/${id}/step`, { data })).toBeOK();
}

async function finish(request, id, state = 'finished') {
    const response = await request.post(`/__test__/jobs/${id}/finish`, { data: { state } });
    await expect(response).toBeOK();
    return (await response.json()).job;
}

async function listed(request) {
    const response = await request.get('/prompt_manager/jobs');
    await expect(response).toBeOK();
    return (await response.json()).jobs;
}

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await expect(await request.post('/__test__/jobs/restart')).toBeOK();
    await expect(await request.post('/__test__/reset')).toBeOK();
    page.activityErrors = [];
    page.on('pageerror', error => page.activityErrors.push(error.message));
});

test.afterEach(async ({ page, request, browserName }) => {
    // The store's own reads can meet a route handler at teardown; that must not fail the test it outlived.
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await saveCoverage(page, browserName);
    expect(page.activityErrors).toEqual([]);
    await request.post('/__test__/jobs/restart');
});

/** The concept's item structure and the computed styles its CSS gives it, measured the same way in both documents. */
function measureItems(selector) {
    return [...document.querySelectorAll(selector)].map(item => {
        const css = (node, ...keys) => node && Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]]));
        const tree = node => ({ tag: node.localName, className: [...node.classList].sort().join(' '), children: [...node.children].map(tree) });
        const progress = item.querySelector('progress');
        return {
            tree: tree(item),
            title: item.querySelector('h3').textContent,
            chip: item.querySelector('.chip').textContent,
            buttons: [...item.querySelectorAll('.row-actions button')].map(button => button.textContent),
            styles: {
                item: css(item, 'paddingTop', 'paddingBottom', 'borderBottomWidth', 'borderBottomStyle', 'borderBottomColor'),
                heading: css(item.querySelector('.panel-heading'), 'display', 'justifyContent', 'alignItems', 'columnGap'),
                h3: css(item.querySelector('h3'), 'fontSize', 'fontWeight', 'color', 'marginTop', 'marginBottom'),
                chip: css(item.querySelector('.chip'), 'fontSize', 'color', 'backgroundColor', 'paddingTop', 'paddingLeft', 'borderTopLeftRadius'),
                p: css(item.querySelector('p'), 'fontSize', 'color', 'marginTop', 'lineHeight'),
                progress: progress && { ...css(progress, 'height', 'marginTop', 'marginBottom', 'accentColor'),
                    fills: Math.abs(progress.getBoundingClientRect().width - item.getBoundingClientRect().width) < 1,
                    max: progress.max, label: Boolean(progress.getAttribute('aria-label')) },
                actions: css(item.querySelector('.row-actions'), 'display', 'columnGap', 'marginTop'),
                button: css(item.querySelector('.row-actions button'), 'fontSize', 'paddingTop', 'paddingLeft', 'minHeight', 'borderTopLeftRadius'),
            },
        };
    });
}

/** D56: an item tree whose `h3` holds the link to the job's view. */
function withTitleLink(tree) {
    const [heading, ...rest] = tree.children;
    const link = { tag: 'a', className: '', children: [] };
    return { ...tree, children: [{ ...heading, children: heading.children.map(node => node.tag === 'h3' ? { ...node, children: [link] } : node) }, ...rest] };
}

function measurePage(mainSelector) {
    const root = document.querySelector(mainSelector);
    const section = root.querySelector(':scope > section.surface');
    return {
        h1: root.querySelector('.heading h1').textContent,
        description: root.querySelector('.heading p.muted').textContent,
        actions: [...root.querySelector('.heading .heading-actions').children].map(node => node.textContent),
        h2: section.querySelector(':scope > h2').textContent,
        surface: Object.fromEntries(['paddingTop', 'borderTopWidth', 'backgroundColor', 'borderTopLeftRadius']
            .map(key => [key, getComputedStyle(section)[key]])),
    };
}

for (const theme of ['dark', 'light']) test(`activity page matches the concept list markup ${theme}`, async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await seedConcept(request);
    // The concept: its finished sample, then a running job started from System (startJob, app.js 940–952).
    await openConcept(page, 'activity', theme);
    const conceptPage = await page.evaluate(measurePage, '#main');
    const [conceptDone] = await page.evaluate(measureItems, '#main .activity-item');
    await page.goto('/__test__/canonical/workspace#system');
    await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption(theme);
    await page.getByRole('button', { name: 'Review thumbnails', exact: true }).click();
    await expect(page.locator('#main .activity-item progress')).toHaveCount(1);
    const [conceptRunning] = await page.evaluate(measureItems, '#main .activity-item');

    const done = await startJob(request);
    await step(request, done.id, { processed: 19622, counters: { added: 1420, errors: 18 } });
    await finish(request, done.id);
    const running = await startJob(request);
    await step(request, running.id, { processed: 8240 });
    await openProduction(page, 'activity', theme);
    await expect(items(page)).toHaveCount(2);
    const productionPage = await page.evaluate(measurePage, '#workspace-main');
    const [productionRunning, productionDone] = await page.evaluate(measureItems, '#workspace-main .activity-item');

    // D52: no "Unified activity · proposed" chip (prototype copy); the rest of the heading and the section are the concept's.
    expect(conceptPage.actions).toEqual(['Unified activity · proposed']);
    expect(productionPage).toEqual({ ...conceptPage, actions: [] });
    // A completed job is the concept's completed item; D56: its title is a link to the job's view, in the title's colour.
    expect(conceptDone.title).toBe('✓ Library metadata enriched');
    expect(productionDone).toEqual({ ...conceptDone, tree: withTitleLink(conceptDone.tree), title: '✓ Scan for metadata' });
    const links = await items(page).locator('h3 > a').evaluateAll(nodes => nodes.map(node => [node.getAttribute('href'),
        getComputedStyle(node).color === getComputedStyle(node.parentElement).color, getComputedStyle(node).textDecorationLine]));
    expect(links).toEqual([[`#/activity/${running.id}`, true, 'none'], [`#/activity/${done.id}`, true, 'none']]);
    // D54: a running job's chip names its state ("Running", where the concept's sample reads "Needs attention"), and
    // its actions are Cancel job only (no "Simulate completion").
    expect(conceptRunning.buttons).toEqual(['Simulate completion', 'Cancel job']);
    const [, ...cancelOnly] = conceptRunning.tree.children.at(-1).children;
    const conceptActions = { ...conceptRunning.tree.children.at(-1), children: cancelOnly };
    expect(productionRunning).toEqual({ ...conceptRunning,
        tree: withTitleLink({ ...conceptRunning.tree, children: [...conceptRunning.tree.children.slice(0, -1), conceptActions] }),
        title: '○ Scan for metadata', chip: 'Running', buttons: ['Cancel job'] });
    await expect(items(page).nth(1).locator('p')).toHaveText(/^Finished \d\d:\d\d · 1,420 added · 18 errors$/);
    await expect(items(page).first().locator('p')).toHaveText(/^Started \d\d:\d\d · 2 output folders · closing this view does not cancel it$/);
    await expect(items(page).first().locator('progress')).toHaveAttribute('aria-label', 'Scan for metadata progress');
    expect(await items(page).first().locator('progress').evaluate(node => node.value)).toBe(41);
});

test('pill shows while a job runs and opens its view', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await page.goto(`${workspace}#/images`);
    // D52: no running job, so neither count shows; the button is the concept's.
    await expect(activityButton(page)).toHaveAccessibleName('Activity');
    await expect(topCount(page)).toBeHidden();
    await expect(navRow(page)).toHaveText('Activity');
    await expect(navRow(page).locator('.nav-count')).toHaveCount(0);
    await expect(pill(page)).toBeHidden();

    const job = await startJob(request);
    // The list is read again on its own (15 s), or at once on a reload or when Activity opens.
    await page.reload();
    await expect(pill(page)).toBeVisible();
    await expect(pill(page)).toHaveText('Scanning · 0% · 0 / 19,622');
    await step(request, job.id, { processed: 8240 });
    const text = 'Scanning · 41% · 8,240 / 19,622';
    await expect(pill(page)).toHaveText(text);
    await expect(pill(page)).toHaveAttribute('aria-label', text);
    await expect(pill(page)).toHaveAttribute('href', `#/activity/${job.id}`);
    expect(await pill(page).locator('.activity-pill-bar i').evaluate(node => node.style.width)).toBe('41%');
    // The pill sits before the button, in the mockup's place, and both counts read 1.
    expect(await page.locator('.top-actions > *').evaluateAll(nodes => nodes.map(node => node.id || node.className)))
        .toEqual(['activity-pill', 'activity-open', 'theme-label', 'canvas-link']);
    await expect(topCount(page)).toBeVisible();
    await expect(topCount(page)).toHaveText('1');
    await expect(navRow(page).locator('.nav-count')).toHaveText('1');

    // The pill opens the job's own view (Task 3, D56), named by the job.
    await pill(page).click();
    await expect(page).toHaveURL(new RegExp(`#/activity/${job.id}$`));
    await expect(main(page).getByRole('heading', { level: 1 })).toHaveText('Scan for metadata');
    await expect(page.locator('#workspace-breadcrumb')).toHaveText('Activity');
    await expect(page).toHaveTitle('Scan for metadata · Prompt Manager');
    await expect(navRow(page)).toHaveAttribute('aria-current', 'page');
    await expect(progressSection(page).locator('progress')).toHaveJSProperty('value', 41);

    // The Activity button opens the list itself from any page.
    await page.locator('nav [data-route="library"]').click();
    await activityButton(page).click();
    await expect(page).toHaveURL(/#\/activity$/);
    await expect(items(page)).toHaveCount(1);

    await finish(request, job.id);
    await expect(pill(page)).toBeHidden();
    await expect(topCount(page)).toBeHidden();
    await expect(navRow(page).locator('.nav-count')).toHaveCount(0);
    await expect(items(page).first().locator('.chip.green')).toHaveText('Completed');
    await expect(items(page).first().locator('progress, .row-actions')).toHaveCount(0);
    // A finished job's title still opens its view (D56).
    await items(page).first().locator('h3 > a').click();
    await expect(page).toHaveURL(new RegExp(`#/activity/${job.id}$`));
    await expect(heading(page).locator('p.muted')).toHaveText(/^Finished \d\d:\d\d · 0 added · 0 errors$/);
});

test('a phone keeps the pill and its controls inside the topbar', async ({ page, request }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const job = await startJob(request);
    await step(request, job.id, { processed: 8240 });
    await page.goto(`${workspace}#/images`);
    await expect(pill(page)).toHaveText('Scanning · 41% · 8,240 / 19,622');
    await expect(pill(page)).toHaveAttribute('aria-label', 'Scanning · 41% · 8,240 / 19,622');
    await expect(pill(page).locator('.activity-pill-files, .activity-pill-bar')).toHaveCount(2);
    await expect(pill(page).locator('.activity-pill-files')).toBeHidden();
    const layout = await page.locator('.top-actions').evaluate(row => ({
        scroll: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
        inside: [...row.children].filter(node => node.getBoundingClientRect().width)
            .every(node => node.getBoundingClientRect().right <= row.getBoundingClientRect().right + 0.5
                && node.getBoundingClientRect().left >= row.getBoundingClientRect().left - 0.5),
    }));
    expect(layout).toEqual({ scroll: true, inside: true });
});

test('reload during a scan reattaches to the running job', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const job = await startJob(request);
    await step(request, job.id, { processed: 1000 });
    await page.goto(`${workspace}#/activity`);
    await expect(items(page)).toHaveCount(1);
    await expect(pill(page)).toHaveText('Scanning · 5% · 1,000 / 19,622');
    await page.reload();
    await expect(pill(page)).toHaveText('Scanning · 5% · 1,000 / 19,622');
    // The reloaded page follows the same job live and started nothing.
    await step(request, job.id, { processed: 9811 });
    await expect(pill(page)).toHaveText('Scanning · 50% · 9,811 / 19,622');
    expect(await items(page).first().locator('progress').evaluate(node => node.value)).toBe(50);
    await expect(items(page)).toHaveCount(1);
    expect((await listed(request)).map(item => [item.id, item.state])).toEqual([[job.id, 'running']]);

    // A second tab attaches to the same job and sees the same progress.
    const other = await page.context().newPage();
    await other.goto(`${workspace}#/activity`);
    await expect(pill(other)).toHaveText('Scanning · 50% · 9,811 / 19,622');
    await expect(other.locator('#workspace-main .activity-item')).toHaveCount(1);
    await other.close();
    expect((await listed(request)).length).toBe(1);

    // Cancel job asks the server to stop at a file boundary; the job ends cancelled with its partial counts.
    await step(request, job.id, { counters: { added: 7 } });
    await items(page).first().getByRole('button', { name: 'Cancel job', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect(toast(page)).toHaveText('Scan cancelled · 7 added');
    // The removed Cancel job hands its focus to the item's title link (review M4).
    await expect(items(page).first().locator('h3 > a')).toBeFocused();
    await expect(items(page).first().locator('.chip.blue')).toHaveText('Cancelled');
    await expect(items(page).first().locator('p')).toHaveText(/^Cancelled \d\d:\d\d at a file boundary · 7 added · completed work is kept$/);
    await expect(pill(page)).toBeHidden();
});

test('an empty Activity says no job has run, and a list that cannot be read says so', async ({ page }) => {
    // The empty and failed states were only reached in passing by other tests, so their coverage depended on timing.
    const empty = main(page).locator('section.surface > p.empty-state');
    await page.goto(`${workspace}#/activity`);
    await expect(empty).toHaveText('No jobs have run since ComfyUI started. Scans and other long operations appear here.');
    await expect(empty).not.toHaveAttribute('role');
    await expect(items(page)).toHaveCount(0);
    await page.route(url => url.pathname === '/prompt_manager/jobs', route => route.abort());
    await page.reload();
    await expect(empty).toHaveText('Activity could not be read. It is tried again shortly.');
    await expect(empty).toHaveAttribute('role', 'alert');
});

test('lost server clears the pill and marks jobs ended', async ({ page, request }) => {
    // A poll interval after the restart, then failed re-reads backing off for 1 + 2 + 4 s.
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 1600, height: 1100 });
    const job = await startJob(request);
    await step(request, job.id, { processed: 4000 });
    await page.goto(`${workspace}#/activity`);
    await expect(pill(page)).toBeVisible();
    await expect(topCount(page)).toHaveText('1');
    const reads = [];
    page.on('request', item => { if (new URL(item.url()).pathname === '/prompt_manager/jobs') reads.push(Date.now()); });
    // ComfyUI restarts: the stream drops without a final event and the new registry is empty.
    await expect(await request.post('/__test__/jobs/restart')).toBeOK();
    await expect(pill(page)).toBeHidden();
    await expect(topCount(page)).toBeHidden();
    await expect(items(page).first().locator('.chip.blue')).toHaveText('Ended');
    await expect(items(page).first().locator('p')).toHaveText(LOST);
    await expect(items(page).first().locator('progress, .row-actions')).toHaveCount(0);
    await expect(navRow(page).locator('.nav-count')).toHaveCount(0);
    // One re-read found the restart; there is no retry storm and no finish toast for a job that only vanished. The
    // read after it is the periodic one, a whole poll interval later: any extra read lands in that gap and fails it.
    const found = reads.length;
    expect(found, 'reads that found the restart').toBeGreaterThanOrEqual(1);
    await expect.poll(() => reads.length, { timeout: POLL_MS + 10_000 }).toBeGreaterThan(found);
    expect(reads[found] - reads[found - 1], 'the next read waits for the poll').toBeGreaterThanOrEqual(POLL_MS - REPORT_SLACK_MS);
    await expect(toast(page)).toHaveText('');

    // A server that cannot be read at all also ends the running job, then is read again with a growing backoff.
    const second = await startJob(request);
    await page.reload();
    await expect(pill(page)).toBeVisible();
    const failed = [];
    await page.route('**/prompt_manager/jobs**', route => { failed.push(Date.now()); return route.abort(); });
    await expect(await request.post('/__test__/jobs/restart')).toBeOK();
    await expect(pill(page)).toBeHidden();
    await expect(items(page).filter({ hasText: LOST })).toHaveCount(1);
    // 1 s, then 2 s, then 4 s: each failed re-read waits twice as long as the one before. The gaps between the
    // reads themselves are the evidence, so a slow runner noticing the ended job late cannot add a read to a window.
    await expect.poll(() => failed.length, { timeout: 20_000 }).toBeGreaterThanOrEqual(3);
    const gaps = failed.slice(1, 3).map((at, index) => at - failed[index]);
    expect(gaps[0]).toBeGreaterThanOrEqual(2000 - REPORT_SLACK_MS);
    expect(gaps[1]).toBeGreaterThanOrEqual(4000 - REPORT_SLACK_MS);
    await page.unroute('**/prompt_manager/jobs**');
    expect(second.id).not.toBe(job.id);
});

test('start and finish toasts', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const job = await startJob(request);
    await page.goto(`${workspace}#/images`);
    await expect(pill(page)).toBeVisible();
    await step(request, job.id, { processed: 19622, counters: { added: 1420, errors: 18 } });
    await finish(request, job.id);
    await expect(toast(page)).toHaveText('Scan finished · 1,420 added · 18 errors');
    await expect(pill(page)).toBeHidden();

    const single = await startJob(request);
    await page.reload();
    await expect(pill(page)).toBeVisible();
    await step(request, single.id, { processed: 19622, counters: { added: 1, errors: 1 } });
    await finish(request, single.id, 'failed');
    await expect(toast(page)).toHaveText('Scan failed · 1 added · 1 error');

    // The store's own start: the real metadata scan over this server's outputs, announced, followed and finished.
    await page.goto(`${workspace}#/activity`);
    const started = await page.evaluate(async () => {
        const { createActivityStore } = await import('/prompt_manager/js/workspace/activity-store.mjs');
        const controller = new AbortController();
        const store = createActivityStore({ api: window.ApiClient.createApiClient(), pollMs: 0,
            scope: { signal: controller.signal, cleanup: () => {} } });
        const result = await store.start('scan');
        // Read before the busy attempt: its list read may already see the small scan finished and toast that.
        const toast = document.getElementById('workspace-toast').textContent;
        const busy = await store.start('scan').catch(error => ({ error: error.message }));
        return { id: result.job.id, title: result.job.title, toast, busy: busy.busy?.id ?? null, message: busy.message ?? null };
    });
    expect(started.title).toBe('Scan for metadata');
    expect(started.toast).toBe('Scan started.');
    // A second start while the scan runs is refused and names it (unless the small fixture scan already ended).
    if (started.busy !== null) expect(started.busy).toBe(started.id);
    await expect(toast(page)).toHaveText(/^Scan (started\.|finished · \d+ added · \d+ errors?)$/);
    await expect.poll(async () => (await listed(request)).find(item => item.id === started.id)?.state, { timeout: 15000 }).toBe('finished');
});

// ── Task 3: Scan for metadata, its review dialog and the job view ─────────────────────────────

async function startFromImport(page) {
    await page.goto(`${workspace}#/images`);
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    const importDialog = page.getByRole('dialog', { name: 'Import & inspect', exact: true });
    const row = importDialog.locator('.operation').filter({ hasText: 'Scan for metadata' });
    await row.getByRole('button', { name: 'Review scan', exact: true }).click();
    const review = page.getByRole('dialog', { name: 'Review metadata scan', exact: true });
    await expect(review).toBeVisible();
    await review.getByRole('button', { name: 'Start scan', exact: true }).click();
    await expect(review).toHaveCount(0);
}

test('start scan creates a job, toasts, and opens its view', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const posted = [];
    page.on('request', item => { if (item.method() === 'POST' && new URL(item.url()).pathname === '/prompt_manager/jobs/scan') posted.push(item); });
    await startFromImport(page);
    await expect(page).toHaveURL(/#\/activity\/[0-9a-f]{32}$/);
    const id = page.url().split('/').at(-1);
    expect(posted).toHaveLength(1);
    // The fixture scan is small and may already have finished; the start toast came first either way.
    await expect(toast(page)).toHaveText(/^Scan (started\.|finished · \d+ added · \d+ errors?)$/);
    await expect(heading(page).locator('h1')).toHaveText('Scan for metadata');
    await expect(heading(page).locator('h1')).toBeFocused();
    expect((await listed(request)).map(item => item.id)).toEqual([id]);
    await expect.poll(async () => (await listed(request))[0].state, { timeout: 15000 }).toBe('finished');
    await expect(heading(page).locator('p.muted')).toHaveText(/^Finished \d\d:\d\d · \d+ added · \d+ errors?$/);
    // The finished scan's tiles add up to the files it processed, and its log is there to open.
    const job = (await listed(request))[0];
    const values = await tileValues(page);
    expect(values.map(([, label]) => label)).toEqual(TILE_LABELS);
    expect(values.reduce((sum, [, , value]) => sum + Number(value.replaceAll(',', '')), 0)).toBe(job.processed);
    // Opening the log reads its last lines (review M12).
    await scanLog(page).locator('summary').click();
    await expect(scanLog(page).locator('pre')).toContainText('Scan finished');
});

// ── Task 4 (Activity-2): scan scope, new and unresolved files by default ─────────────────────

test('all files checkbox changes the scope line and the request', async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const bodies = [];
    page.on('request', item => {
        if (item.method() === 'POST' && new URL(item.url()).pathname === '/prompt_manager/jobs/scan') bodies.push(item.postDataJSON());
    });
    const review = page.getByRole('dialog', { name: 'Review metadata scan', exact: true });
    const scopeValue = review.locator('.meta-grid > div').filter({ has: page.locator('dt', { hasText: 'Scope' }) }).locator('dd');
    // The concept's switch: a checkbox with role=switch.
    const allFiles = review.getByRole('switch', { name: 'All files', exact: true });
    const openReview = async () => {
        await page.goto(`${workspace}#/images`);
        await page.getByRole('button', { name: 'Import', exact: true }).click();
        await page.getByRole('dialog', { name: 'Import & inspect', exact: true }).locator('.operation')
            .filter({ hasText: 'Scan for metadata' }).getByRole('button', { name: 'Review scan', exact: true }).click();
        await expect(review).toBeVisible();
    };

    // The default: the concept's "New and unresolved files", the box unchecked; the request asks for "new".
    await openReview();
    await expect(scopeValue).toHaveText('New and unresolved files');
    await expect(allFiles).not.toBeChecked();
    await expect(review.locator('.setting-row p')).toHaveText('Also read the files already linked to their prompt again.');
    await expect(allFiles).toHaveAccessibleDescription('Also read the files already linked to their prompt again.');
    // Checking and unchecking switches the Scope value both ways.
    await allFiles.check();
    await expect(scopeValue).toHaveText('All files');
    await allFiles.uncheck();
    await expect(scopeValue).toHaveText('New and unresolved files');
    await review.getByRole('button', { name: 'Start scan', exact: true }).click();
    await expect(page).toHaveURL(/#\/activity\/[0-9a-f]{32}$/);
    await expect.poll(() => bodies.length).toBe(1);
    expect(bodies[0]).toEqual({ scope: 'new' });
    await expect(heading(page).locator('h1')).toHaveText('Scan for metadata');
    // Wait for the first scan to end so the second is not refused as busy.
    await scanLog(page).locator('summary').click();
    await expect(scanLog(page).locator('pre')).toContainText('Scan finished', { timeout: 15000 });
    await expect(scanLog(page).locator('pre')).toContainText('new and unresolved files');

    // Checked: "All files", and the request asks for "all".
    await openReview();
    await allFiles.check();
    await expect(scopeValue).toHaveText('All files');
    await review.getByRole('button', { name: 'Start scan', exact: true }).click();
    await expect.poll(() => bodies.length).toBe(2);
    expect(bodies[1]).toEqual({ scope: 'all' });
    await scanLog(page).locator('summary').click();
    await expect(scanLog(page).locator('pre')).toContainText('Scan started · ', { timeout: 15000 });
    await expect(scanLog(page).locator('pre')).toContainText('all files');
});

test('job view counters follow the mapping and update live', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const job = await startJob(request);
    await step(request, job.id, { processed: 8240, counters: { added: 1420, known: 6511, review: 37, no_metadata: 254, errors: 18 } });
    await openJob(page, job.id);
    // The header: the title, the detail line, then Download report (enabled in Task 5) and Cancel scan (danger).
    await expect(heading(page).locator('h1')).toHaveText('Scan for metadata');
    await expect(heading(page).locator('p.muted')).toHaveText(/^Started \d\d:\d\d · 2 output folders · closing this view does not cancel it$/);
    await expect(heading(page).locator('.heading-actions > button')).toHaveText(['Download report', 'Cancel scan']);
    await expect(heading(page).getByRole('button', { name: 'Download report', exact: true })).toBeEnabled();
    await expect(heading(page).getByRole('button', { name: 'Cancel scan', exact: true })).toHaveClass(/danger/);
    // Progress: the bar, the server's line and the five tiles in order and colour.
    await expect(progressSection(page).locator('.panel-heading > span.small')).toHaveText('Processing file 8,240 of 19,622');
    await expect(progressSection(page).locator('progress')).toHaveAttribute('aria-label', 'Scan progress');
    await expect(progressSection(page).locator('progress')).toHaveJSProperty('value', 41);
    expect(await tileValues(page)).toEqual([
        ['stat added', 'Added to Library', '1,420'], ['stat', 'Already known', '6,511'], ['stat warn', 'Needs review', '37'],
        ['stat', 'No usable metadata', '254'], ['stat bad', 'Errors', '18'],
    ]);
    const colours = await tiles(page).evaluateAll(nodes => nodes.map(node => getComputedStyle(node.querySelector('dd')).color));
    const expected = await page.evaluate(() => ['--green', '--ca-text', '--warning', '--ca-text', '--red'].map(name => {
        const probe = document.createElement('span');
        probe.style.color = `var(${name})`;
        document.querySelector('#workspace-main').append(probe);
        const colour = getComputedStyle(probe).color;
        probe.remove();
        return colour;
    }));
    expect([colours[0], colours[2], colours[4]]).toEqual([expected[0], expected[2], expected[4]]);
    expect(colours[1]).toBe(colours[3]);
    // The outcome list follows the mockup's order with the same counts.
    await expect(outcomeButtons(page)).toHaveText(['Added to Library1,420', 'Needs review37', 'Errors18', 'No usable metadata254', 'Already known6,511']);

    // Live: the next step updates the bar, the line and the tiles without a reload.
    await step(request, job.id, { processed: 9811, counters: { added: 1500, errors: 19 }, records: [
        { group: 'added', path: 'live/new.png', detail: 'Added to the Library as “Live”' },
    ] });
    await expect(progressSection(page).locator('.panel-heading > span.small')).toHaveText('Processing file 9,811 of 19,622');
    await expect(progressSection(page).locator('progress')).toHaveJSProperty('value', 50);
    await expect(tiles(page).first().locator('dd')).toHaveText('1,500');
    await expect(tiles(page).last().locator('dd')).toHaveText('19');
    await expect(fileRows(page)).toHaveCount(1);
    // A focused row keeps its focus while later reads append rows.
    await fileRows(page).first().focus();
    await step(request, job.id, { records: [{ group: 'added', path: 'live/next.png', detail: 'Added' }] });
    await expect(fileRows(page)).toHaveCount(2);
    await expect(fileRows(page).first()).toBeFocused();
    // The log is collapsed by default and holds the raw timestamped lines.
    await expect(scanLog(page)).not.toHaveAttribute('open', '');
    await expect(scanLog(page).locator('summary')).toContainText('Scan log');
    await scanLog(page).locator('summary').click();
    await expect(scanLog(page).locator('pre')).toHaveAttribute('tabindex', '0');
    await expect(scanLog(page).locator('pre')).toHaveText(/^\d\d:\d\d:\d\d \+ live\/new\.png · Added to the Library as “Live”\n\d\d:\d\d:\d\d \+ live\/next\.png · Added$/);

    // Finished: the header says so, Cancel scan is gone and the counters stay.
    await finish(request, job.id);
    await expect(heading(page).locator('p.muted')).toHaveText(/^Finished \d\d:\d\d · 1,501 added · 19 errors$/);
    await expect(heading(page).locator('.heading-actions > button')).toHaveText(['Download report']);
    await expect(tiles(page).first().locator('dd')).toHaveText('1,501');
});

test('added rows open the viewer, error rows are plain', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const job = await startJob(request);
    // The fixture's two listed files share a name in the output and input roots; the row's root label tells them apart.
    await step(request, job.id, { processed: 3, records: [
        { group: 'added', path: 'shared/same.png', root: 'output', detail: 'Added to the Library as “Cat portrait”' },
        { group: 'known', path: 'shared/same.png', root: 'input', detail: 'Already linked to “Dog portrait”' },
        { group: 'errors', path: 'broken/truncated.png', root: 'output', detail: 'Error: the file could not be processed (see the server log)' },
    ] });
    await openJob(page, job.id);
    await expect(outcomeButtons(page).first()).toHaveAttribute('aria-pressed', 'true');
    await expect(fileRows(page)).toHaveCount(1);
    const added = fileRows(page).first();
    expect(await added.evaluate(node => node.localName)).toBe('button');
    await expect(added.locator('.path')).toHaveText('shared/same.png');
    await expect(added.locator('.why')).toHaveText('Added to the Library as “Cat portrait”');
    await expect(resultsNote(page)).toHaveText('Each row opens the image in the viewer.');
    await added.click();
    const viewer = page.locator('dialog#viewer-dialog[open]');
    await expect(viewer).toBeVisible();
    await expect(viewer.locator('#viewer-title')).toContainText('same.png');
    await expect(viewer.locator('#viewer-stage img').first()).toHaveAttribute('src', /[?&]root=0(&|$)/);
    await page.keyboard.press('Escape');
    await expect(viewer).toHaveCount(0);
    await expect(added).toBeFocused();

    // Already known opens the input root's file.
    await outcomeButtons(page).filter({ hasText: 'Already known' }).click();
    await expect(outcomeButtons(page).filter({ hasText: 'Already known' })).toHaveAttribute('aria-pressed', 'true');
    await fileRows(page).first().click();
    await expect(page.locator('dialog#viewer-dialog[open] #viewer-stage img').first()).toHaveAttribute('src', /[?&]root=1(&|$)/);
    await page.keyboard.press('Escape');

    // Errors: plain text rows, no viewer hint.
    await outcomeButtons(page).filter({ hasText: 'Errors' }).click();
    await expect(fileRows(page)).toHaveCount(1);
    expect(await fileRows(page).first().evaluate(node => node.localName)).toBe('div');
    await expect(fileRows(page).first().locator('.path')).toHaveText('broken/truncated.png');
    await expect(resultsNote(page)).toHaveCount(0);
    // An empty group says so.
    await outcomeButtons(page).filter({ hasText: 'Needs review' }).click();
    await expect(resultsSection(page).locator('.outcome-files > p.empty-state')).toHaveText('No files in this group.');

    // A listed row whose file has left the listing toasts instead of opening an empty viewer.
    await step(request, job.id, { records: [{ group: 'added', path: 'gone/missing.png', root: 'output', detail: 'Added' }] });
    await outcomeButtons(page).first().click();
    await expect(fileRows(page)).toHaveCount(2);
    await fileRows(page).nth(1).click();
    await expect(toast(page)).toHaveText('This file is no longer in Images. Rescan from Import and try again.');
    await expect(page.locator('dialog#viewer-dialog')).toHaveCount(0);
    // A row opens only its own root's file: a root whose label no longer matches (a replaced source) never opens the
    // other root's same path, and a row without a root index opens nothing (review M8, M9).
    const lookups = [];
    page.on('request', item => { const url = new URL(item.url()); if (url.pathname === '/prompt_manager/images/output' && url.searchParams.get('path')) lookups.push(url.search); });
    await step(request, job.id, { records: [
        { group: 'added', path: 'shared/same.png', root: 'output', root_index: 1, detail: 'Added' },
        { group: 'added', path: 'shared/same.png', root: 'output', root_index: null, detail: 'Added' },
    ] });
    await expect(fileRows(page)).toHaveCount(4);
    for (const index of [2, 3]) {
        await fileRows(page).nth(index).click();
        await expect(toast(page)).toHaveText('This file is no longer in Images. Rescan from Import and try again.');
        await expect(page.locator('dialog#viewer-dialog')).toHaveCount(0);
    }
    expect(lookups).toEqual([]);
    // The lookup is the exact identity, not a name search.
    await fileRows(page).first().click();
    await expect(page.locator('dialog#viewer-dialog[open]')).toBeVisible();
    expect(lookups).toEqual(['?mode=workspace&root=0&path=shared%2Fsame.png&limit=5']);
    await page.keyboard.press('Escape');
});

test('filter narrows the selected outcome', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const job = await startJob(request);
    await step(request, job.id, { processed: 4, records: [
        { group: 'added', path: 'CCC/portrait_0001.png', detail: 'Added' },
        { group: 'added', path: 'CCC/portrait_0002.png', detail: 'Added' },
        { group: 'added', path: 'forest/pines_0008.png', detail: 'Added' },
        { group: 'errors', path: 'CCC/broken.png', detail: 'Error' },
    ] });
    await openJob(page, job.id);
    await expect(fileRows(page)).toHaveCount(3);
    await expect(filter(page)).toHaveAttribute('placeholder', 'Filter by file path…');
    await filter(page).fill('ccc/');
    await expect(fileRows(page).locator('.path')).toHaveText(['CCC/portrait_0001.png', 'CCC/portrait_0002.png']);
    await expect(resultsNote(page)).toHaveText(`Showing 2 of 3 · ${VIEWER_HINT}`);
    // The filter stays while another outcome is chosen.
    await outcomeButtons(page).filter({ hasText: 'Errors' }).click();
    await expect(fileRows(page).locator('.path')).toHaveText(['CCC/broken.png']);
    await filter(page).fill('pines');
    await expect(fileRows(page)).toHaveCount(0);
    await expect(resultsSection(page).locator('.outcome-files > p.empty-state')).toHaveText('No listed file matches this filter.');
    await outcomeButtons(page).first().click();
    await expect(fileRows(page).locator('.path')).toHaveText(['forest/pines_0008.png']);
    await filter(page).fill('');
    await expect(fileRows(page)).toHaveCount(3);
});

test('job view renders 200-row cap note', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    // The page's timers are Playwright's, running at real speed, so the M12 check below can run a read throttle out.
    await page.clock.install();
    const job = await startJob(request);
    await step(request, job.id, { processed: 260, bulk: { group: 'added', count: 250 }, records: [
        ...Array.from({ length: 10 }, (_, index) => ({ group: 'no_metadata', path: `misc/plain_${index}.png`, detail: 'No prompt metadata in the file' })),
    ] });
    // Every detail read of this job: in flight, and whether one has returned the full listing.
    const detailUrl = `/prompt_manager/jobs/${job.id}`;
    const isDetail = item => new URL(item.url()).pathname === detailUrl;
    const detailReads = { inFlight: 0, full: false };
    page.on('request', item => { if (isDetail(item)) detailReads.inFlight += 1; });
    page.on('requestfailed', item => { if (isDetail(item)) detailReads.inFlight -= 1; });
    page.on('response', async response => {
        if (!isDetail(response)) return;
        const body = await response.json().catch(() => null);
        const outcomes = body?.job?.outcomes ?? {};
        if (outcomes.added?.length === 200 && outcomes.no_metadata?.length === 10) detailReads.full = true;
        detailReads.inFlight -= 1;
    });
    await openJob(page, job.id);
    await expect(tiles(page).first().locator('dd')).toHaveText('250');
    await expect(fileRows(page)).toHaveCount(200);
    await expect(resultsNote(page)).toHaveText(`Showing 200 of 250 · ${VIEWER_HINT}`);
    await outcomeButtons(page).filter({ hasText: 'No usable metadata' }).click();
    await expect(fileRows(page)).toHaveCount(10);
    await expect(resultsNote(page)).toHaveCount(0);
    // Every listed row is in the page and it stays responsive: a filter answers at once.
    await outcomeButtons(page).first().click();
    await filter(page).fill('file_00199');
    await expect(fileRows(page)).toHaveCount(1);
    await expect(resultsNote(page)).toHaveText(`Showing 1 of 250 · ${VIEWER_HINT}`);

    // Review M12: with the list capped and the log closed, more files only update the counters; the detail is read
    // again only when the log opens with new lines.
    // The reads from opening the view have settled: one returned the full listing and none is in flight. The view
    // re-reads only while what the summary lists differs from the last read, so no further read is due.
    await expect.poll(() => detailReads.full && detailReads.inFlight === 0).toBe(true);
    const reads = [];
    page.on('request', item => { if (new URL(item.url()).pathname === `/prompt_manager/jobs/${job.id}`) reads.push(Date.now()); });
    await step(request, job.id, { processed: 300, bulk: { group: 'added', count: 40 } });
    await expect(tiles(page).first().locator('dd')).toHaveText('290');
    // Every read the counter update could have scheduled is due within one throttle interval: run it out, so any such
    // read is sent before the log opens. Requests reach the test in the order they were sent, so once the log's read
    // has answered, a read sent before it would already be listed.
    await page.clock.runFor(DETAIL_INTERVAL_MS);
    expect(reads).toEqual([]);
    await scanLog(page).locator('summary').click();
    await expect.poll(() => reads.length).toBe(1);
    await expect(scanLog(page).locator('pre')).toContainText('bulk/file_00039.png');
    expect(reads, 'only the log\'s own read').toHaveLength(1);
});

test('cancel scan ends cancelled with partial counts', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const job = await startJob(request);
    await step(request, job.id, { processed: 9, counters: { added: 7, errors: 2 } });
    await openJob(page, job.id);
    // From the keyboard: when the job ends and Cancel scan goes away, focus moves to the job's heading (review M4).
    await heading(page).getByRole('button', { name: 'Cancel scan', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect(toast(page)).toHaveText('Scan cancelled · 7 added');
    await expect(heading(page).locator('h1')).toBeFocused();
    await expect(heading(page).locator('p.muted')).toHaveText(/^Cancelled \d\d:\d\d at a file boundary · 7 added · completed work is kept$/);
    await expect(heading(page).getByRole('button', { name: 'Cancel scan', exact: true })).toHaveCount(0);
    await expect(progressSection(page).locator('.panel-heading > span.small')).toHaveText('Cancelled after 9 of 19,622 files');
    expect((await tileValues(page)).map(([, , value]) => value)).toEqual(['7', '0', '0', '0', '2']);
    expect((await listed(request))[0].state).toBe('cancelled');

    // A failed cancel request keeps the button and says so.
    const second = await startJob(request);
    await openJob(page, second.id);
    await page.route('**/cancel', route => route.fulfill({ status: 500, json: { success: false, error: 'nope' } }));
    await heading(page).getByRole('button', { name: 'Cancel scan', exact: true }).click();
    await expect(toast(page)).toHaveText('The job could not be cancelled. Try again.');
    await expect(heading(page).getByRole('button', { name: 'Cancel scan', exact: true })).not.toHaveAttribute('aria-disabled', 'true');
});

// Truth rule: a held legacy-page job (a restore from the admin page) cannot be cancelled, so neither the Activity
// list nor the job's view offers Cancel; the cancellable scan beside it keeps its own (review I3).
test('a held legacy job shows no Cancel in the list or its view', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const response = await request.post('/__test__/jobs/hold', { data: { kind: 'restore', title: 'Database restore (admin page)' } });
    await expect(response).toBeOK();
    const held = (await response.json()).job;
    expect(held.cancellable).toBe(false);
    await page.goto(`${workspace}#/activity`);
    const item = items(page).filter({ has: page.getByRole('link', { name: /Database restore \(admin page\)$/ }) });
    await expect(item.locator('.chip')).toHaveText('Running');
    await expect(item.locator('progress')).toHaveCount(1);
    await expect(main(page).getByRole('button', { name: 'Cancel job', exact: true })).toHaveCount(0);

    await item.getByRole('link').click();
    await expect(heading(page).locator('h1')).toHaveText('Database restore (admin page)');
    await expect(heading(page).locator('.heading-actions > button')).toHaveText(['Download report']);
    await expect(heading(page).getByRole('button', { name: /^Cancel/ })).toHaveCount(0);

    // When the hold ends the view shows the end, still without a Cancel.
    await finish(request, held.id);
    await expect(heading(page).locator('p.muted')).toHaveText(/^Finished/);
    await expect(heading(page).locator('.heading-actions > button')).toHaveText(['Download report']);
});

// Since #289 an item dropped its Cancel job only when it was already in the page, so the first render of jobs the
// store already knew (Activity opened from another page) kept a Cancel job on a job that had ended, and moved a
// running job's progress after its actions. The read Activity starts on opening is held, so that first render is
// what the page shows (the read's own render would otherwise repair it a moment later).
test('jobs that ended before Activity opened show no Cancel job, a running one keeps its progress first', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const finished = await startJob(request, { title: 'Finished scan' });
    await finish(request, finished.id);
    const cancelled = await startJob(request, { title: 'Cancelled scan' });
    await finish(request, cancelled.id, 'cancelled');
    await startJob(request, { title: 'Running scan' });
    // The page's first list read has all three jobs once the running one shows in the pill.
    await page.goto(`${workspace}#/images`);
    await expect(pill(page)).toBeVisible();
    let hold;
    const held = new Promise(resolve => { hold = resolve; });
    // Only the first read is held; any later one goes through.
    await page.route(url => url.pathname === '/prompt_manager/jobs', route => {
        if (!hold) return route.continue();
        hold(route);
        hold = null;
    });
    await activityButton(page).click();
    const route = await held;
    await expect(items(page)).toHaveCount(3);
    const item = title => items(page).filter({ has: page.getByRole('link', { name: new RegExp(`${title}$`) }) });
    for (const ended of ['Finished scan', 'Cancelled scan']) {
        await expect(item(ended).locator('progress, .row-actions')).toHaveCount(0);
        await expect(item(ended).getByRole('button', { name: 'Cancel job', exact: true })).toHaveCount(0);
    }
    await expect(item('Running scan').getByRole('button', { name: 'Cancel job', exact: true })).toHaveCount(1);
    expect(await item('Running scan').evaluate(node => [...node.children].map(child => child.localName + (child.className ? `.${child.className}` : ''))))
        .toEqual(['div.panel-heading', 'p', 'progress', 'div.row-actions']);
    await route.continue();
});

test('busy start names the running job', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    // The page's timers are Playwright's, so the toast's lifetime below is run out rather than waited for.
    await page.clock.install();
    const running = await startJob(request, { title: 'Database maintenance', kind: 'maintenance' });
    await startFromImport(page);
    await expect(toast(page)).toHaveText('Another job is running: Database maintenance.');
    await expect(toast(page).getByRole('link', { name: 'Database maintenance', exact: true })).toHaveAttribute('href', `#/activity/${running.id}`);
    await expect(page).toHaveURL(/#\/images$/);
    expect((await listed(request)).map(item => item.id)).toEqual([running.id]);
    // Review M14: a toast with a link outlasts the plain 6.5 s, so the link stays reachable. Running the page's timers
    // 7 s on fires a 6.5 s clear for certain, however slowly this runner reached this line.
    await page.clock.runFor(7000);
    await expect(toast(page).getByRole('link', { name: 'Database maintenance', exact: true })).toBeVisible();
    await toast(page).getByRole('link', { name: 'Database maintenance', exact: true }).click();
    await expect(heading(page).locator('h1')).toHaveText('Database maintenance');
    // A job of another kind cancels as "Cancel job".
    await expect(heading(page).locator('.heading-actions > button')).toHaveText(['Download report', 'Cancel job']);
});

test('an unknown or restarted job view says so', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await page.goto(`${workspace}#/activity/${'0'.repeat(32)}`);
    await expect(main(page).locator('section.surface p.empty-state')).toHaveText('This job is not in this session’s Activity. Jobs are kept until ComfyUI restarts.');
    await expect(main(page).getByRole('link', { name: 'All activity', exact: true })).toHaveAttribute('href', '#/activity');

    const job = await startJob(request);
    await step(request, job.id, { processed: 5, records: [{ group: 'added', path: 'kept/row.png', detail: 'Added' }] });
    await openJob(page, job.id);
    await expect(fileRows(page)).toHaveCount(1);
    await expect(await request.post('/__test__/jobs/restart')).toBeOK();
    await expect(heading(page).locator('p.muted')).toHaveText(LOST);
    await expect(heading(page).getByRole('button', { name: 'Cancel scan', exact: true })).toHaveCount(0);
    // A lost job has no report on the server (review M9): Download report is disabled rather than failing.
    await expect(heading(page).getByRole('button', { name: 'Download report', exact: true })).toBeDisabled();
    // What was read before the restart stays on the page.
    await expect(fileRows(page)).toHaveCount(1);
    await expect(pill(page)).toBeHidden();
});

// Review M3: a finished job the server no longer lists is gone from this tab too, and its open view says so.
test('a finished job the server pruned leaves its open view honest', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const job = await startJob(request);
    await step(request, job.id, { processed: 1, records: [{ group: 'added', path: 'kept/row.png', detail: 'Added' }] });
    await finish(request, job.id);
    await openJob(page, job.id);
    await expect(fileRows(page)).toHaveCount(1);
    await filter(page).focus();
    await expect(await request.post('/__test__/jobs/restart')).toBeOK();
    // The next periodic list read (15 s) no longer has the finished job.
    test.slow();
    await expect(main(page).locator('section.surface p.empty-state')).toHaveText('This job is not in this session’s Activity. Jobs are kept until ComfyUI restarts.', { timeout: 20000 });
    await expect(progressSection(page)).toHaveCount(0);
    await expect(heading(page).locator('h1')).toHaveText('Activity');
    await expect(heading(page).locator('h1')).toBeFocused();
    await expect(page).toHaveTitle('Activity · Prompt Manager');
});

// Activity-2 Task 5 (D59): Download report and the log's scroll position.
test('download report saves the job JSON', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const job = await startJob(request);
    await step(request, job.id, { processed: 2, records: [
        { group: 'added', path: 'sub/a.png', root: 'output', detail: 'Added' },
        { group: 'errors', path: 'broken/b.png', root: 'output', detail: 'Error' },
    ] });
    await finish(request, job.id);
    await openJob(page, job.id);
    const button = heading(page).getByRole('button', { name: 'Download report', exact: true });
    await expect(button).toBeEnabled();
    const [download] = await Promise.all([page.waitForEvent('download'), button.click()]);
    expect(download.suggestedFilename()).toMatch(/^prompt-manager-scan-\d{8}-\d{4}\.json$/);
    const path = await download.path();
    const report = JSON.parse(await (await import('node:fs/promises')).readFile(path, 'utf8'));
    expect(report.job.id).toBe(job.id);
    expect(report.outcomes.added.rows.map(row => row.path)).toEqual(['sub/a.png']);
    expect(report.outcomes.errors.truncated).toBe(false);
    expect(report.log.length).toBeGreaterThan(0);
    await expect(toast(page)).toHaveText('Job report downloaded.');
});

test('log keeps the reader\'s scroll position', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const job = await startJob(request);
    const more = (from, count) => Array.from({ length: count }, (_, index) => ({ group: 'known', path: `log/f_${from + index}.png`,
        root: 'output', detail: 'Known' }));
    await step(request, job.id, { processed: 60, records: more(0, 60) });
    await openJob(page, job.id);
    await scanLog(page).locator('summary').click();
    const pre = scanLog(page).locator('pre');
    await expect(pre).toContainText('log/f_59.png');
    const metrics = () => pre.evaluate(node => ({ top: node.scrollTop, max: node.scrollHeight - node.clientHeight }));
    // Opening starts at the newest line.
    await expect.poll(async () => { const m = await metrics(); return m.max > 0 && m.top >= m.max - 2; }).toBe(true);
    // Pinned to the bottom: new lines are followed.
    await step(request, job.id, { processed: 90, records: more(60, 30) });
    await expect(pre).toContainText('log/f_89.png');
    await expect.poll(async () => { const m = await metrics(); return m.top >= m.max - 2; }).toBe(true);
    // Scrolled up: the position stays while more lines arrive.
    await pre.evaluate(node => { node.scrollTop = 40; });
    await step(request, job.id, { processed: 120, records: more(90, 30) });
    await expect(pre).toContainText('log/f_119.png');
    expect((await metrics()).top).toBe(40);
    // Closed and opened again (review M6): every open starts at the newest line, also after lines arrived meanwhile.
    await scanLog(page).locator('summary').click();
    await expect(scanLog(page)).not.toHaveAttribute('open', '');
    await step(request, job.id, { processed: 150, records: more(120, 30) });
    await scanLog(page).locator('summary').click();
    await expect(pre).toContainText('log/f_149.png');
    await expect.poll(async () => { const m = await metrics(); return m.max > 0 && m.top >= m.max - 2; }).toBe(true);
});

test('a failed report download says so and leaves the button usable', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const job = await startJob(request);
    await finish(request, job.id);
    await openJob(page, job.id);
    await page.route(`**/prompt_manager/jobs/${job.id}/report`, route => route.fulfill({ status: 500, body: 'no' }));
    const button = heading(page).getByRole('button', { name: 'Download report', exact: true });
    await button.click();
    await expect(toast(page)).toHaveText('The report could not be downloaded. Try again.');
    await expect(button).not.toHaveAttribute('aria-disabled', 'true');
});

// ── #249 PR D Task D3 (D89, plan D66): image tagging jobs in Activity ─────────────────────────

const TAG_JOB = { kind: 'image-tags', title: 'Auto-tag images', mode: 'auto', images: 4, total: 4 };

test('an image tagging job shows Tagged Unchanged Skipped Errors and opens tagged rows', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const job = await startJob(request, TAG_JOB);
    await step(request, job.id, { processed: 3, records: [
        { group: 'tagged', path: 'shared/same.png', root: 'output', detail: 'Added 2 tags' },
        { group: 'unchanged', path: 'shared/same.png', root: 'input', detail: 'No new tags' },
        { group: 'skipped', path: 'video/clip_0004.webm', root: 'output', detail: 'Video: manual tags only' },
    ] });
    await page.goto(`${workspace}#/images`);
    // The pill names the kind's verb.
    await expect(pill(page)).toHaveText('Tagging · 75% · 3 / 4');
    await openJob(page, job.id);
    await expect(heading(page).locator('h1')).toHaveText('Auto-tag images');
    await expect(page).toHaveTitle('Auto-tag images · Prompt Manager');
    // The header's scope is the number of images, and the job cancels as "Cancel tagging" (danger).
    await expect(heading(page).locator('p.muted')).toHaveText(/^Started \d\d:\d\d · 4 images · closing this view does not cancel it$/);
    await expect(heading(page).locator('.heading-actions > button')).toHaveText(['Download report', 'Cancel tagging']);
    await expect(heading(page).getByRole('button', { name: 'Cancel tagging', exact: true })).toHaveClass(/danger/);
    await expect(progressSection(page).locator('.panel-heading > span.small')).toHaveText('Processing file 3 of 4');
    await expect(progressSection(page).locator('progress')).toHaveAttribute('aria-label', 'Auto-tag images progress');
    // Four tiles in the scan's tones: Tagged green, Unchanged plain, Skipped warning, Errors red.
    expect(await tileValues(page)).toEqual([
        ['stat added', 'Tagged', '1'], ['stat', 'Unchanged', '1'], ['stat warn', 'Skipped', '1'], ['stat bad', 'Errors', '0'],
    ]);
    // What got tagged and what went wrong first, as the scan's list does.
    await expect(outcomeButtons(page)).toHaveText(['Tagged1', 'Errors0', 'Skipped1', 'Unchanged1']);
    await expect(outcomeButtons(page).first()).toHaveAttribute('aria-pressed', 'true');

    // Tagged rows open the viewer on that file (D57's identity).
    const tagged = fileRows(page).first();
    expect(await tagged.evaluate(node => node.localName)).toBe('button');
    await expect(tagged.locator('.path')).toHaveText('shared/same.png');
    await expect(tagged.locator('.why')).toHaveText('Added 2 tags');
    await expect(resultsNote(page)).toHaveText('Each row opens the image in the viewer.');
    await tagged.click();
    const viewer = page.locator('dialog#viewer-dialog[open]');
    await expect(viewer.locator('#viewer-stage img').first()).toHaveAttribute('src', /[?&]root=0(&|$)/);
    await page.keyboard.press('Escape');
    await expect(viewer).toHaveCount(0);
    await expect(tagged).toBeFocused();
    // Unchanged rows open too, here the input root's file.
    await outcomeButtons(page).filter({ hasText: 'Unchanged' }).click();
    await fileRows(page).first().click();
    await expect(page.locator('dialog#viewer-dialog[open] #viewer-stage img').first()).toHaveAttribute('src', /[?&]root=1(&|$)/);
    await page.keyboard.press('Escape');
    // Skipped rows are plain text with their reason.
    await outcomeButtons(page).filter({ hasText: 'Skipped' }).click();
    expect(await fileRows(page).first().evaluate(node => node.localName)).toBe('div');
    await expect(fileRows(page).first().locator('.why')).toHaveText('Video: manual tags only');
    await expect(resultsNote(page)).toHaveCount(0);
    // The log is the job's own: "Tagging log", with the runner's prefixes.
    await expect(scanLog(page).locator('summary')).toHaveText('● Tagging log — raw detail, click to show');
    await scanLog(page).locator('summary').click();
    await expect(scanLog(page).locator('pre')).toContainText('+ shared/same.png · Added 2 tags');

    // An error, then the end: the header and the Activity item read "N tagged · M errors".
    await step(request, job.id, { processed: 4, records: [
        { group: 'errors', path: 'broken/truncated.png', root: 'output', detail: 'Auto-tagging failed. Check server logs for details.' },
    ] });
    await expect(tiles(page).last().locator('dd')).toHaveText('1');
    await finish(request, job.id);
    await expect(toast(page)).toHaveText('Auto-tag images finished · 1 tagged · 1 error');
    await expect(heading(page).locator('p.muted')).toHaveText(/^Finished \d\d:\d\d · 1 tagged · 1 error$/);
    await expect(heading(page).locator('.heading-actions > button')).toHaveText(['Download report']);
    await expect(progressSection(page).locator('.panel-heading > span.small')).toHaveText('4 of 4 files processed');
    await page.goto(`${workspace}#/activity`);
    await expect(items(page).first().locator('h3')).toHaveText('✓ Auto-tag images');
    await expect(items(page).first().locator('p')).toHaveText(/^Finished \d\d:\d\d · 1 tagged · 1 error$/);
});

test('cancel tagging stops at a file boundary', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const job = await startJob(request, { ...TAG_JOB, title: 'Add tags to images', mode: 'add', images: 1250, total: 1250 });
    await step(request, job.id, { processed: 2, counters: { tagged: 2 } });
    await page.goto(`${workspace}#/activity`);
    await expect(items(page).first().locator('p')).toHaveText(/^Started \d\d:\d\d · 1,250 images · closing this view does not cancel it$/);
    await openJob(page, job.id);
    await heading(page).getByRole('button', { name: 'Cancel tagging', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect(toast(page)).toHaveText('Add tags to images cancelled · 2 tagged');
    await expect(heading(page).locator('h1')).toBeFocused();
    await expect(heading(page).locator('p.muted')).toHaveText(/^Cancelled \d\d:\d\d at a file boundary · 2 tagged · completed work is kept$/);
    await expect(heading(page).getByRole('button', { name: 'Cancel tagging', exact: true })).toHaveCount(0);
    await expect(progressSection(page).locator('.panel-heading > span.small')).toHaveText('Cancelled after 2 of 1,250 files');
    expect((await tileValues(page)).map(([, , value]) => value)).toEqual(['2', '0', '0', '0']);
    expect((await listed(request))[0].state).toBe('cancelled');
});

test('the report download names the kind', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const job = await startJob(request, TAG_JOB);
    await step(request, job.id, { processed: 1, records: [
        { group: 'tagged', path: 'sub/a.png', root: 'output', detail: 'Added 1 tag' },
    ] });
    await finish(request, job.id);
    await openJob(page, job.id);
    const button = heading(page).getByRole('button', { name: 'Download report', exact: true });
    const [download] = await Promise.all([page.waitForEvent('download'), button.click()]);
    expect(download.suggestedFilename()).toMatch(/^prompt-manager-image-tags-\d{8}-\d{4}\.json$/);
    const report = JSON.parse(await (await import('node:fs/promises')).readFile(await download.path(), 'utf8'));
    expect(report.job.kind).toBe('image-tags');
    expect(report.job.scope).toEqual({ mode: 'auto', images: 4 });
    expect(Object.keys(report.outcomes)).toEqual(['tagged', 'unchanged', 'skipped', 'errors']);
    expect(report.outcomes.tagged.rows.map(row => [row.path, row.detail])).toEqual([['sub/a.png', 'Added 1 tag']]);
    await expect(toast(page)).toHaveText('Job report downloaded.');
});

// D111 (System S5): each kind's own descriptor. A thumbnail job counts Generated (green), Skipped and Errors (red),
// its pill says "Generating thumbnails" and its toasts "Thumbnails finished · N generated · M errors".
const THUMB_JOB = { kind: 'thumbnails', total: 10, output_folders: 2 };

test('a thumbnail job shows Generated Skipped Errors, its pill verb and its toasts', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const job = await startJob(request, THUMB_JOB);
    await step(request, job.id, { processed: 4, records: [
        { group: 'generated', path: 'sub/a.png', root: 'output', detail: 'Preview written' },
        { group: 'skipped', path: 'b.png', root: 'input', detail: 'Preview already up to date' },
        { group: 'errors', path: 'broken.png', root: 'output', detail: 'Not a valid image file' },
    ] });
    await page.goto(`${workspace}#/images`);
    await expect(pill(page)).toHaveText('Generating thumbnails · 40% · 4 / 10');
    await page.goto(`${workspace}#/activity`);
    await expect(items(page).first().locator('p')).toHaveText(/^Started \d\d:\d\d · 2 output folders · closing this view does not cancel it$/);
    await expect(items(page).first().locator('progress')).toHaveAttribute('value', '40');
    await openJob(page, job.id);
    await expect(heading(page).locator('h1')).toHaveText('Thumbnail generation');
    await expect(heading(page).locator('.heading-actions > button')).toHaveText(['Download report', 'Cancel job']);
    expect(await tileValues(page)).toEqual([['stat added', 'Generated', '1'], ['stat', 'Skipped', '1'], ['stat bad', 'Errors', '1']]);
    await expect(outcomeButtons(page)).toHaveText(['Generated1', 'Errors1', 'Skipped1']);
    await expect(fileRows(page).first().locator('.path')).toHaveText('sub/a.png');
    expect(await fileRows(page).first().evaluate(node => node.localName)).toBe('button');
    await outcomeButtons(page).filter({ hasText: 'Errors' }).click();
    expect(await fileRows(page).first().evaluate(node => node.localName)).toBe('div');
    await expect(scanLog(page).locator('summary')).toHaveText('● Job log — raw detail, click to show');

    await finish(request, job.id);
    await expect(toast(page)).toHaveText('Thumbnails finished · 1 generated · 1 error');
    await expect(heading(page).locator('p.muted')).toHaveText(/^Finished \d\d:\d\d · 1 generated · 1 error$/);
    await expect(heading(page).locator('.heading-actions > button')).toHaveText(['Download report']);
});

test('a held legacy job and the health check show no tiles, no Results and no made-up percentage', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const response = await request.post('/__test__/jobs/hold',
        { data: { kind: 'thumbnails', title: 'Thumbnail clear (gallery page)', admission: 'guarded' } });
    await expect(response).toBeOK();
    const held = (await response.json()).job;
    await page.goto(`${workspace}#/images`);
    // D112: the pill names the held operation and shows no percentage.
    await expect(pill(page)).toHaveText('Thumbnail clear (gallery page)');
    await page.goto(`${workspace}#/activity`);
    const item = items(page).filter({ has: page.getByRole('link', { name: /Thumbnail clear \(gallery page\)$/ }) });
    await expect(item.locator('p')).toHaveText(/^Started \d\d:\d\d · closing this view does not cancel it$/);
    await expect(item.locator('progress')).toHaveCount(1);
    await expect(item.locator('progress')).not.toHaveAttribute('value');
    await expect(item.getByRole('button', { name: 'Cancel job', exact: true })).toHaveCount(0);
    await item.getByRole('link').click();
    await expect(heading(page).locator('h1')).toHaveText('Thumbnail clear (gallery page)');
    await expect(progressSection(page).locator('progress')).not.toHaveAttribute('value');
    await expect(progressSection(page).locator('dl.stat-row')).toHaveCount(0);
    await expect(resultsSection(page)).toHaveCount(0);
    await expect(scanLog(page)).toHaveCount(1);
    await finish(request, held.id);
    await expect(toast(page)).toHaveText('Thumbnail clear (gallery page) finished.');
    await expect(heading(page).locator('p.muted')).toHaveText(/^Finished \d\d:\d\d$/);
    await expect(progressSection(page).locator('progress')).toHaveAttribute('value', '100');

    // The health check (a read job of its own kind): its status line, no tiles, no Cancel.
    const health = await startJob(request, { kind: 'health', total: 0 });
    await openJob(page, health.id);
    await expect(heading(page).locator('h1')).toHaveText('Health check');
    await expect(progressSection(page).locator('.panel-heading > span.small')).toHaveText('Checking the database and image links');
    await expect(progressSection(page).locator('progress')).not.toHaveAttribute('value');
    await expect(progressSection(page).locator('dl.stat-row')).toHaveCount(0);
    await expect(resultsSection(page)).toHaveCount(0);
    await expect(heading(page).locator('.heading-actions > button')).toHaveText(['Download report']);
    await expect(pill(page)).toHaveText('Health check');
    await finish(request, health.id);
    await expect(toast(page)).toHaveText('Health check finished.');
});
