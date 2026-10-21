import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import { seedConcept, openConcept, openProduction } from './concept-inventory.mjs';

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

test('lost server clears the pill and marks jobs ended', async ({ page, request }) => {
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
    // One re-read found the restart; there is no retry storm and no finish toast for a job that only vanished.
    await page.waitForTimeout(2500);
    expect(reads.length).toBeLessThanOrEqual(2);
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
    await page.waitForTimeout(3500);
    // 1 s, then 2 s: at most two re-reads in 3.5 s.
    expect(failed.length).toBeLessThanOrEqual(2);
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

test('job view counters follow the mapping and update live', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const job = await startJob(request);
    await step(request, job.id, { processed: 8240, counters: { added: 1420, known: 6511, review: 37, no_metadata: 254, errors: 18 } });
    await openJob(page, job.id);
    // The header: the title, the detail line, then Download report (Task 5) and Cancel scan (danger).
    await expect(heading(page).locator('h1')).toHaveText('Scan for metadata');
    await expect(heading(page).locator('p.muted')).toHaveText(/^Started \d\d:\d\d · 2 output folders · closing this view does not cancel it$/);
    await expect(heading(page).locator('.heading-actions > button')).toHaveText(['Download report', 'Cancel scan']);
    await expect(heading(page).getByRole('button', { name: 'Download report', exact: true })).toBeDisabled();
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
    const job = await startJob(request);
    await step(request, job.id, { processed: 260, bulk: { group: 'added', count: 250 }, records: [
        ...Array.from({ length: 10 }, (_, index) => ({ group: 'no_metadata', path: `misc/plain_${index}.png`, detail: 'No prompt metadata in the file' })),
    ] });
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
    const reads = [];
    page.on('request', item => { if (new URL(item.url()).pathname === `/prompt_manager/jobs/${job.id}`) reads.push(Date.now()); });
    await step(request, job.id, { processed: 300, bulk: { group: 'added', count: 40 } });
    await expect(tiles(page).first().locator('dd')).toHaveText('290');
    await page.waitForTimeout(1500);
    expect(reads).toEqual([]);
    await scanLog(page).locator('summary').click();
    await expect.poll(() => reads.length).toBe(1);
    await expect(scanLog(page).locator('pre')).toContainText('bulk/file_00039.png');
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

test('busy start names the running job', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const running = await startJob(request, { title: 'Database maintenance', kind: 'maintenance' });
    await startFromImport(page);
    await expect(toast(page)).toHaveText('Another job is running: Database maintenance.');
    await expect(toast(page).getByRole('link', { name: 'Database maintenance', exact: true })).toHaveAttribute('href', `#/activity/${running.id}`);
    await expect(page).toHaveURL(/#\/images$/);
    expect((await listed(request)).map(item => item.id)).toEqual([running.id]);
    // Review M14: a toast with a link outlasts the plain 6.5 s, so the link stays reachable.
    await page.waitForTimeout(7000);
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
    // What was read before the restart stays on the page.
    await expect(fileRows(page)).toHaveCount(1);
    await expect(pill(page)).toBeHidden();
});
