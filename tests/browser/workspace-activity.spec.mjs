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
    // A completed job is the concept's completed item.
    expect(conceptDone.title).toBe('✓ Library metadata enriched');
    expect(productionDone).toEqual({ ...conceptDone, title: '✓ Scan for metadata' });
    // D54: a running job's chip names its state ("Running", where the concept's sample reads "Needs attention"), and
    // its actions are Cancel job only (no "Simulate completion").
    expect(conceptRunning.buttons).toEqual(['Simulate completion', 'Cancel job']);
    const [, ...cancelOnly] = conceptRunning.tree.children.at(-1).children;
    const conceptActions = { ...conceptRunning.tree.children.at(-1), children: cancelOnly };
    expect(productionRunning).toEqual({ ...conceptRunning,
        tree: { ...conceptRunning.tree, children: [...conceptRunning.tree.children.slice(0, -1), conceptActions] },
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

    await pill(page).click();
    await expect(page).toHaveURL(new RegExp(`#/activity/${job.id}$`));
    await expect(main(page).getByRole('heading', { level: 1 })).toHaveText('Activity');
    await expect(page.locator('#workspace-breadcrumb')).toHaveText('Activity');
    await expect(page).toHaveTitle('Activity · Prompt Manager');
    await expect(navRow(page)).toHaveAttribute('aria-current', 'page');
    await expect(items(page).first()).toHaveAttribute('aria-current', 'true');
    await expect(items(page).first().locator('h3')).toHaveText('○ Scan for metadata');

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
    await items(page).first().getByRole('button', { name: 'Cancel job', exact: true }).click();
    await expect(toast(page)).toHaveText('Scan cancelled · 7 added');
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
        const busy = await store.start('scan').catch(error => ({ error: error.message }));
        return { id: result.job.id, title: result.job.title, toast: document.getElementById('workspace-toast').textContent,
            busy: busy.busy?.id ?? null, message: busy.message ?? null };
    });
    expect(started.title).toBe('Scan for metadata');
    expect(started.toast).toBe('Scan started.');
    // A second start while the scan runs is refused and names it (unless the small fixture scan already ended).
    if (started.busy !== null) expect(started.busy).toBe(started.id);
    await expect(toast(page)).toHaveText(/^Scan (started\.|finished · \d+ added · \d+ errors?)$/);
    await expect.poll(async () => (await listed(request)).find(item => item.id === started.id)?.state, { timeout: 15000 }).toBe('finished');
});
