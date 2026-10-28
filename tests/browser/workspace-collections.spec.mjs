import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';

// Collections (#331): the sidebar COLLECTIONS group (D136, concept index.html 28–31, app.js 336–339), the collection
// page `#/collection/<id>` with its Edit (D137, concept render a1186–1189) and saved views on a collection (D140).
const workspace = '/prompt_manager/workspace';
const API = '/prompt_manager/collections';
const main = page => page.locator('#workspace-main');
const heading = page => main(page).getByRole('heading', { level: 1 });
const breadcrumb = page => page.locator('#workspace-breadcrumb');
const description = page => main(page).locator('.heading p.muted');
const missingNote = page => main(page).locator('.heading p.small');
const search = page => page.getByRole('searchbox', { name: 'Search workspace', exact: true });
const imageCards = page => page.locator('article[data-image-key]');
const collectionsNav = page => page.getByRole('navigation', { name: 'Collections', exact: true });
const collectionsLabel = page => page.locator('aside.sidebar .sidebar-label', { hasText: 'COLLECTIONS' });
const rows = page => collectionsNav(page).locator('a[data-route^="collection/"]');
const row = (page, name) => rows(page).filter({ hasText: name });
const savedNav = page => page.getByRole('navigation', { name: 'Saved views', exact: true });
const editButton = page => main(page).locator('.heading-actions > button').first();
const editDialog = page => page.getByRole('dialog', { name: 'Edit collection', exact: true });
const deleteDialog = page => page.getByRole('dialog', { name: 'Delete collection', exact: true });
const nameField = page => editDialog(page).getByRole('textbox', { name: 'Collection name', exact: true });
const toast = page => page.locator('#workspace-toast');

async function seed(request) {
    const response = await request.post('/__test__/collections/seed');
    await expect(response).toBeOK();
    return response.json();
}

async function listed(request) {
    const response = await request.get(API);
    await expect(response).toBeOK();
    return (await response.json()).collections;
}

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await expect(await request.post('/__test__/reset')).toBeOK();
    page.collectionErrors = [];
    page.on('pageerror', error => page.collectionErrors.push(error.message));
    // D51: deleting asks inside the dialog; no browser dialog (window.confirm) may ever open.
    page.on('dialog', dialog => { page.collectionErrors.push(`browser ${dialog.type()}`); dialog.dismiss().catch(() => {}); });
});

test.afterEach(async ({ page, browserName }) => {
    await saveCoverage(page, browserName);
    expect(page.collectionErrors).toEqual([]);
});

test('a collection row opens its page', async ({ page, request }) => {
    const manifest = await seed(request);
    const { id } = manifest.collections.Landscapes;
    await page.setViewportSize({ width: 1600, height: 1100 });
    await page.goto(`${workspace}#/images`);
    // D136: name order, the listed-member count, no "PROPOSED" badge.
    await expect(collectionsLabel(page)).toBeVisible();
    await expect(collectionsLabel(page)).toHaveText('COLLECTIONS');
    await expect(rows(page)).toHaveCount(2);
    await expect(rows(page).nth(0)).toContainText('Experiments');
    await expect(rows(page).nth(1)).toContainText('Landscapes');
    await expect(row(page, 'Experiments').locator('.nav-count')).toHaveText('8');
    await expect(row(page, 'Landscapes').locator('.nav-count')).toHaveText('12');
    await expect(row(page, 'Landscapes')).toHaveAttribute('href', `#/collection/${id}`);
    await expect(row(page, 'Landscapes').locator('svg')).toHaveCount(1);
    await expect(page.locator('aside.sidebar .new-label')).toHaveCount(0);
    await expect(imageCards(page)).toHaveCount(21);

    await row(page, 'Landscapes').click();
    await expect(page).toHaveURL(new RegExp(`#/collection/${id}$`));
    await expect(heading(page)).toHaveText('Landscapes');
    await expect(breadcrumb(page)).toHaveText('Collection');
    await expect(page).toHaveTitle('Collection · Prompt Manager');
    await expect(row(page, 'Landscapes')).toHaveAttribute('aria-current', 'page');
    await expect(description(page)).toHaveText('Your generations, their prompts, and the next idea.');
    await expect(imageCards(page)).toHaveCount(12);
    for (const path of manifest.files.Landscapes) {
        await expect(page.locator(`article[data-image-key$=":${path}"]`)).toHaveCount(1);
    }
    // MC3: the member whose file is gone is not listed and is counted in the note.
    await expect(missingNote(page)).toHaveText('1 image in this collection is not in your current sources.');

    // Every Images filter narrows the members.
    await search(page).fill('landscape_03');
    await expect(imageCards(page)).toHaveCount(1);
    await search(page).fill('');
    await expect(imageCards(page)).toHaveCount(12);

    await row(page, 'Experiments').click();
    await expect(heading(page)).toHaveText('Experiments');
    await expect(imageCards(page)).toHaveCount(8);
    await expect(missingNote(page)).toHaveCount(0);
});

test('without collections the group shows no label and no rows', async ({ page }) => {
    await page.goto(`${workspace}#/images`);
    await expect(imageCards(page).first()).toBeVisible();
    await expect(collectionsNav(page)).toHaveCount(1);
    await expect(rows(page)).toHaveCount(0);
    await expect(collectionsLabel(page)).toBeHidden();
});

test('a collection page neither restores nor replaces the Images return state', async ({ page, request }) => {
    const manifest = await seed(request);
    await page.goto(`${workspace}#/images`);
    await search(page).fill('experiment');
    await expect(imageCards(page)).toHaveCount(8);
    await row(page, 'Landscapes').click();
    await expect(search(page)).toHaveValue('');
    await expect(imageCards(page)).toHaveCount(12);
    await search(page).fill('landscape_01');
    await expect(imageCards(page)).toHaveCount(1);
    await page.goto(`${workspace}#/images`);
    await expect(search(page)).toHaveValue('experiment');
    expect(manifest.collections.Landscapes.count).toBe(12);
});

test('Edit renames the open collection; a taken name stays in the dialog', async ({ page, request }) => {
    const manifest = await seed(request);
    const { id } = manifest.collections.Landscapes;
    await page.goto(`${workspace}#/collection/${id}`);
    await expect(heading(page)).toHaveText('Landscapes');
    await expect(editButton(page)).toHaveText('Edit');
    await editButton(page).click();
    await expect(nameField(page)).toHaveValue('Landscapes');
    await expect(editDialog(page).locator('.dialog-actions button')).toHaveText(['Delete…', 'Cancel', 'Save']);

    // CUJ 3: names are unique ignoring case.
    await nameField(page).fill('experiments');
    await editDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
    await expect(editDialog(page).getByRole('alert')).toHaveText('A collection named “experiments” already exists.');
    await expect(heading(page)).toHaveText('Landscapes');

    await nameField(page).fill('  Landscape   grids ');
    await editDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
    await expect(editDialog(page)).toHaveCount(0);
    await expect(toast(page)).toHaveText('Collection renamed.');
    await expect(heading(page)).toHaveText('Landscape grids');
    await expect(breadcrumb(page)).toHaveText('Collection');
    await expect(row(page, 'Landscape grids')).toHaveAttribute('aria-current', 'page');
    await expect(rows(page).nth(1)).toContainText('Landscape grids');
    expect((await listed(request)).map(item => item.name)).toEqual(['Experiments', 'Landscape grids']);
});

test('a failed rename keeps the dialog; a failed delete changes nothing', async ({ page, request }) => {
    const manifest = await seed(request);
    const { id } = manifest.collections.Experiments;
    await page.goto(`${workspace}#/collection/${id}`);
    await expect(heading(page)).toHaveText('Experiments');
    let failing = true;
    await page.route(`**${API}/${id}`, route => failing
        ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"success":false,"error":"The collection could not be changed."}' })
        : route.fallback());
    await editButton(page).click();
    await nameField(page).fill('Tests');
    await editDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
    await expect(toast(page)).toHaveText('The collection could not be renamed. Try again.');
    await expect(editDialog(page)).toBeVisible();
    await expect(heading(page)).toHaveText('Experiments');
    await editDialog(page).getByRole('button', { name: 'Delete…', exact: true }).click();
    await expect(deleteDialog(page).locator('p')).toHaveText(
        'Delete “Experiments”? Its 8 images stay where they are; only the collection is removed.');
    await deleteDialog(page).getByRole('button', { name: 'Delete', exact: true }).click();
    await expect(toast(page)).toHaveText('The collection could not be deleted. Try again.');
    await expect(page).toHaveURL(new RegExp(`#/collection/${id}$`));
    expect(await listed(request)).toHaveLength(2);
    failing = false;
});

test('Delete removes the collection only after confirming, and leaves every file', async ({ page, request }) => {
    const manifest = await seed(request);
    const { id } = manifest.collections.Landscapes;
    await page.goto(`${workspace}#/collection/${id}`);
    await editButton(page).click();
    await editDialog(page).getByRole('button', { name: 'Delete…', exact: true }).click();
    // The stored count: 12 listed members and the missing one.
    await expect(deleteDialog(page).locator('p')).toHaveText(
        'Delete “Landscapes”? Its 13 images stay where they are; only the collection is removed.');
    await deleteDialog(page).getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(deleteDialog(page)).toHaveCount(0);
    expect(await listed(request)).toHaveLength(2);

    await editButton(page).click();
    await editDialog(page).getByRole('button', { name: 'Delete…', exact: true }).click();
    await deleteDialog(page).getByRole('button', { name: 'Delete', exact: true }).click();
    await expect(toast(page)).toHaveText('Collection deleted.');
    await expect(page).toHaveURL(/#\/images$/);
    await expect(heading(page)).toHaveText('Images');
    await expect(rows(page)).toHaveCount(1);
    await expect(imageCards(page)).toHaveCount(21);
    expect((await listed(request)).map(item => item.name)).toEqual(['Experiments']);
});

test('an unknown collection says it no longer exists, with the way back to Images', async ({ page, request }) => {
    await seed(request);
    await page.goto(`${workspace}#/collection/999999`);
    await expect(heading(page)).toHaveText('Collection');
    await expect(breadcrumb(page)).toHaveText('Collection');
    await expect(description(page)).toHaveText('This collection no longer exists.');
    await expect(imageCards(page)).toHaveCount(0);
    await main(page).getByRole('link', { name: 'Open Images', exact: true }).click();
    await expect(page).toHaveURL(/#\/images$/);
    await expect(imageCards(page)).toHaveCount(21);
});

test('a failed list says so and Retry reads it again', async ({ page, request }) => {
    const manifest = await seed(request);
    const { id } = manifest.collections.Experiments;
    let failing = true;
    await page.route(`**${API}`, route => failing
        ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"success":false,"error":"Collections could not be read."}' })
        : route.fallback());
    await page.goto(`${workspace}#/collection/${id}`);
    await expect(description(page)).toHaveText('Collections could not be loaded. Your saved data has not changed.');
    await expect(description(page)).toHaveAttribute('role', 'alert');
    await expect(rows(page)).toHaveCount(0);
    failing = false;
    await main(page).getByRole('button', { name: 'Retry', exact: true }).click();
    await expect(heading(page)).toHaveText('Experiments');
    await expect(imageCards(page)).toHaveCount(8);
    await expect(rows(page)).toHaveCount(2);
});

test('a saved view keeps its collection, and says so once the collection is deleted (MC6)', async ({ page, request }) => {
    const manifest = await seed(request);
    const view = manifest.saved_view;
    await page.goto(`${workspace}#/images`);
    await savedNav(page).locator(`a[data-route="saved/${view.id}"]`).click();
    await expect(heading(page)).toHaveText(view.name);
    await expect(breadcrumb(page)).toHaveText(view.name);
    await expect(imageCards(page)).toHaveCount(8);
    // A saved view is not the collection page: no missing note.
    await expect(missingNote(page)).toHaveCount(0);

    // Save view on a collection page stores the collection.
    await row(page, 'Landscapes').click();
    await expect(imageCards(page)).toHaveCount(12);
    await search(page).fill('landscape_1');
    await expect(imageCards(page)).toHaveCount(3);
    await main(page).getByRole('button', { name: 'Save view', exact: true }).click();
    const save = page.getByRole('dialog', { name: 'Save this view', exact: true });
    await save.getByRole('textbox', { name: 'View name', exact: true }).fill('Grid seed');
    await save.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(toast(page)).toHaveText('Saved view added to the sidebar.');
    const views = (await (await request.get('/prompt_manager/saved-views')).json()).views;
    const saved = views.find(item => item.name === 'Grid seed');
    expect(saved.state).toMatchObject({ q: 'landscape_1', favorites: false, collection: String(manifest.collections.Landscapes.id) });

    await expect(await request.delete(`${API}/${manifest.collections.Experiments.id}`)).toBeOK();
    await page.goto(`${workspace}#/saved/${view.id}`);
    await expect(heading(page)).toHaveText(view.name);
    await expect(main(page).locator('.empty')).toHaveText('The collection this view uses was deleted.');
    await expect(main(page).locator('.empty')).toBeVisible();
    await expect(imageCards(page)).toHaveCount(0);
    await expect(main(page).getByRole('button', { name: 'Retry', exact: true })).toBeHidden();
});

test('a collection deleted in another tab shows the deleted-collection page', async ({ page, request }) => {
    const manifest = await seed(request);
    const { id } = manifest.collections.Experiments;
    await page.goto(`${workspace}#/images`);
    await expect(rows(page)).toHaveCount(2);
    await expect(await request.delete(`${API}/${id}`)).toBeOK();
    await row(page, 'Experiments').click();
    await expect(description(page)).toHaveText('This collection no longer exists.');
    await expect(rows(page)).toHaveCount(1);
});

// D136 / CUJ 15: the rows show and open at desktop and phone width in both themes. The concept's own phone rule hides
// every nav count there, so the rows keep their counts only at desktop width.
for (const [width, height] of [[1600, 1100], [390, 844]]) for (const theme of ['dark', 'light']) {
    test(`collection rows show and open at ${width}x${height} ${theme}`, async ({ page, request }) => {
        const manifest = await seed(request);
        await page.setViewportSize({ width, height });
        await page.goto(`${workspace}#/images`);
        await page.getByRole('combobox', { name: 'Theme', exact: true }).selectOption(theme);
        for (const name of ['Experiments', 'Landscapes']) await expect(row(page, name)).toBeVisible();
        const count = row(page, 'Landscapes').locator('.nav-count');
        if (width > 950) await expect(count).toBeVisible();
        else await expect(count).toBeHidden();
        // Phone width: the label stays hidden as every sidebar label is; desktop shows it.
        if (width > 950) await expect(collectionsLabel(page)).toBeVisible();
        else await expect(collectionsLabel(page)).toBeHidden();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
        await row(page, 'Landscapes').click();
        await expect(page).toHaveURL(new RegExp(`#/collection/${manifest.collections.Landscapes.id}$`));
        await expect(heading(page)).toHaveText('Landscapes');
        await expect(imageCards(page)).toHaveCount(12);
    });
}

test('a finished restore reads the collections again', async ({ page, request }) => {
    await seed(request);
    await expect(await request.post('/__test__/system/reset')).toBeOK();
    await expect(await request.post('/__test__/jobs/restart')).toBeOK();
    try {
        const backup = await request.get('/prompt_manager/backup');
        await expect(backup).toBeOK();
        const bytes = await backup.body();
        const [experiments] = await listed(request);
        await expect(await request.delete(`${API}/${experiments.id}`)).toBeOK();
        await page.goto(`${workspace}#/system/backups`);
        await expect(rows(page)).toHaveCount(1);
        const chooser = page.waitForEvent('filechooser');
        await page.getByRole('button', { name: 'Review restore…', exact: true }).click();
        await (await chooser).setFiles({ name: 'prompts_backup_20261027_093000.db', mimeType: 'application/octet-stream', buffer: bytes });
        await page.locator('dialog#modal[open]').getByRole('button', { name: 'Restore', exact: true }).click();
        await expect(toast(page)).toHaveText(/^Database restore finished · /);
        // The list was read again without a reload.
        await expect(rows(page)).toHaveCount(2);
        await expect(row(page, 'Experiments').locator('.nav-count')).toHaveText('8');
    } finally {
        await request.post('/__test__/jobs/restart');
        await request.post('/__test__/system/reset');
    }
});

test('renaming a collection deleted in another tab says so and shows the deleted-collection page', async ({ page, request }) => {
    const manifest = await seed(request);
    const { id } = manifest.collections.Experiments;
    await page.goto(`${workspace}#/collection/${id}`);
    await expect(heading(page)).toHaveText('Experiments');
    await editButton(page).click();
    await expect(await request.delete(`${API}/${id}`)).toBeOK();
    await nameField(page).fill('Tests');
    await editDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
    await expect(toast(page)).toHaveText('That collection no longer exists.');
    await expect(editDialog(page)).toHaveCount(0);
    await expect(description(page)).toHaveText('This collection no longer exists.');
    await expect(rows(page)).toHaveCount(1);
});

/** A Playwright hold on the collections list read: `held` fills as reads arrive; `release()` lets them all through. */
async function holdListReads(page, answer = route => route.fallback()) {
    const held = [];
    let open = false;
    await page.route(`**${API}`, route => {
        if (route.request().method() !== 'GET') return route.fallback();
        if (open) return answer(route);
        held.push(route);
        return undefined;
    });
    return {
        held,
        async release() {
            open = true;
            for (const route of held.splice(0)) await answer(route).catch(() => {});
        },
    };
}

test('a list read superseded by another read still opens a collection made since (review M1)', async ({ page, request }) => {
    const manifest = await seed(request);
    const listing = await (await request.get('/prompt_manager/images/output?mode=workspace&limit=1')).json();
    const [file] = listing.images;
    await page.goto(`${workspace}#/collection/${manifest.collections.Landscapes.id}`);
    await expect(heading(page)).toHaveText('Landscapes');
    // Another tab makes "Fresh"; this tab does not list it yet.
    const added = await request.post(`${API}/items`, { data: { name: 'Fresh',
        items: [{ root_index: file.root_index, root_scope: file.root_scope, path: file.path }] } });
    await expect(added).toBeOK();
    const fresh = (await added.json()).collection.id;
    await expect(rows(page)).toHaveCount(2);
    const hold = await holdListReads(page);
    // A rename schedules the 300 ms count re-read; opening "Fresh" first reads the list once more. The timer's read
    // supersedes that one while both are held.
    await editButton(page).click();
    await nameField(page).fill('Landscape grids');
    await editDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
    await expect(toast(page)).toHaveText('Collection renamed.');
    await page.evaluate(hash => { window.location.hash = hash; }, `#/collection/${fresh}`);
    await expect.poll(() => hold.held.length).toBeGreaterThanOrEqual(2);
    await expect(main(page)).not.toContainText('Collections could not be loaded.');
    await hold.release();
    await expect(heading(page)).toHaveText('Fresh');
    await expect(imageCards(page)).toHaveCount(1);
});

test('a listing that finds the collection gone while the list still has it offers Retry (review M2)', async ({ page, request }) => {
    const manifest = await seed(request);
    const { id } = manifest.collections.Experiments;
    let gone = true;
    await page.route('**/prompt_manager/images/output?*', route => gone && route.request().url().includes(`collection=${id}`)
        ? route.fulfill({ status: 404, contentType: 'application/json',
            body: '{"success":false,"error":"Collection not found","code":"collection-not-found"}' })
        : route.fallback());
    await page.goto(`${workspace}#/images`);
    await expect(rows(page)).toHaveCount(2);
    // The shell's re-read is held, then fails: the page must not stay blank.
    const hold = await holdListReads(page, route => route.fulfill({ status: 500, contentType: 'application/json',
        body: '{"success":false,"error":"Collections could not be read."}' }));
    await row(page, 'Experiments').click();
    await expect.poll(() => hold.held.length).toBe(1);
    await hold.release();
    const retry = main(page).getByRole('button', { name: 'Retry', exact: true });
    await expect(retry).toBeVisible();
    await expect(main(page).locator('#gallery-continuation p')).toHaveText('Images could not be loaded. Retry or rescan the collection.');
    gone = false;
    await page.unroute(`**${API}`);
    await retry.click();
    await expect(imageCards(page)).toHaveCount(8);

    // The list read again still lists it: the same Retry, never a blank page.
    gone = true;
    await page.evaluate(() => { window.location.hash = '#/images'; });
    await expect(heading(page)).toHaveText('Images');
    await row(page, 'Experiments').click();
    await expect(retry).toBeVisible();
    gone = false;
    await retry.click();
    await expect(imageCards(page)).toHaveCount(8);
});

test('the missing-members note follows the list read again (review L2)', async ({ page, request }) => {
    const manifest = await seed(request);
    const { id } = manifest.collections.Landscapes;
    let missing = null;
    await page.route(`**${API}`, async route => {
        if (route.request().method() !== 'GET' || missing === null) return route.fallback();
        const response = await route.fetch();
        const data = await response.json();
        data.collections = data.collections.map(item => item.id === id ? { ...item, missing } : item);
        return route.fulfill({ response, json: data });
    });
    await page.goto(`${workspace}#/collection/${id}`);
    await expect(missingNote(page)).toHaveText('1 image in this collection is not in your current sources.');
    const rename = async name => {
        await editButton(page).click();
        await nameField(page).fill(name);
        await editDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
        await expect(toast(page)).toHaveText('Collection renamed.');
    };
    missing = 3;
    await rename('Landscapes A');
    await expect(missingNote(page)).toHaveText('3 images in this collection are not in your current sources.');
    missing = 0;
    await rename('Landscapes B');
    await expect(missingNote(page)).toHaveCount(0);
    missing = 1;
    await rename('Landscapes C');
    await expect(missingNote(page)).toHaveText('1 image in this collection is not in your current sources.');
});

test('a rename that settles after its dialog closed leaves the next dialog open (review L1)', async ({ page, request }) => {
    const manifest = await seed(request);
    const { id } = manifest.collections.Landscapes;
    await page.goto(`${workspace}#/collection/${id}`);
    await expect(imageCards(page)).toHaveCount(12);
    let release;
    const released = new Promise(resolve => { release = resolve; });
    await page.route(`**${API}/${id}`, async route => {
        if (route.request().method() !== 'PATCH') return route.fallback();
        await released;
        return route.fallback();
    });
    await editButton(page).click();
    await nameField(page).fill('Later');
    await editDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
    await page.keyboard.press('Escape');
    await expect(editDialog(page)).toHaveCount(0);
    await main(page).getByRole('button', { name: 'Save view', exact: true }).click();
    const save = page.getByRole('dialog', { name: 'Save this view', exact: true });
    await expect(save).toBeVisible();
    release();
    await expect(heading(page)).toHaveText('Later');
    await expect(save).toBeVisible();
});

test('a page that says a collection or view could not be opened takes focus after a route change (review L3)', async ({ page, request }) => {
    await seed(request);
    await page.goto(`${workspace}#/images`);
    await expect(heading(page)).toHaveText('Images');
    await page.evaluate(() => { window.location.hash = '#/collection/999999'; });
    await expect(description(page)).toHaveText('This collection no longer exists.');
    await expect(heading(page)).toBeFocused();

    await page.route(`**${API}`, route => route.request().method() === 'GET'
        ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"success":false,"error":"x"}' })
        : route.fallback());
    await page.evaluate(() => { window.location.hash = '#/images'; });
    await expect(heading(page)).toHaveText('Images');
    await page.evaluate(() => { window.location.hash = '#/collection/999998'; });
    await expect(main(page).getByRole('button', { name: 'Retry', exact: true })).toBeVisible();
    await expect(heading(page)).toBeFocused();

    await page.route('**/prompt_manager/saved-views', route => route.request().method() === 'GET'
        ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"success":false,"error":"x"}' })
        : route.fallback());
    await page.evaluate(() => { window.location.hash = '#/saved/5'; });
    await expect(description(page)).toHaveText('Saved views could not be loaded. Your saved data has not changed.');
    await expect(heading(page)).toBeFocused();
});
