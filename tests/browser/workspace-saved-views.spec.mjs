import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';

// Saved views (#249 D16, maintainer ruling 2026-10-21): the toolbar's Save view (concept app.js a359, a1330–1346),
// the sidebar SAVED VIEWS rows (a331–335) and `#/saved/<id>` (a1158–1162), on Images and the Prompt library.
const workspace = '/prompt_manager/workspace';
const API = '/prompt_manager/saved-views';
const main = page => page.locator('#workspace-main');
const toolbar = page => main(page).locator('search.toolbar');
const search = page => page.getByRole('searchbox', { name: 'Search workspace', exact: true });
const sortBy = page => page.getByRole('combobox', { name: 'Sort results', exact: true });
const filtersButton = page => toolbar(page).getByRole('button', { name: /^Filters(?: · \d+)?$/ });
const imageCards = page => page.locator('article[data-image-key]');
const promptCards = page => page.locator('article[data-prompt-id]');
const savedNav = page => page.getByRole('navigation', { name: 'Saved views', exact: true });
const saveDialog = page => page.getByRole('dialog', { name: 'Save this view', exact: true });
const refineDialog = page => page.getByRole('dialog', { name: 'Refine this view', exact: true });
const toast = page => page.locator('#workspace-toast');
const IMAGE_DEFAULTS = { q: '', category: '', sort: 'newest', source: 'all', folder: '', media: 'all', tags: [] };

async function refine(page, values) {
    await filtersButton(page).click();
    for (const [label, value] of Object.entries(values)) {
        const control = refineDialog(page).getByLabel(label, { exact: true });
        if (label === 'Tags') await control.fill(value);
        else await control.selectOption(value);
    }
    await refineDialog(page).getByRole('button', { name: 'Apply filters', exact: true }).click();
    await expect(refineDialog(page)).toHaveCount(0);
}

async function listed(request) {
    const response = await request.get(API);
    await expect(response).toBeOK();
    return (await response.json()).views;
}

async function create(request, data) {
    const response = await request.post(API, { data });
    expect(response.status()).toBe(201);
    return (await response.json()).view;
}

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await expect(await request.post('/__test__/reset')).toBeOK();
    page.savedErrors = [];
    page.on('pageerror', error => page.savedErrors.push(error.message));
    // No listing read may fail on a saved view's values (an unknown Source would be a 400).
    page.on('response', response => {
        if (response.status() >= 400 && new URL(response.url()).pathname === '/prompt_manager/images/output') {
            page.savedErrors.push(`${response.status()} ${response.url()}`);
        }
    });
});

test.afterEach(async ({ page, browserName }) => {
    await saveCoverage(page, browserName);
    expect(page.savedErrors).toEqual([]);
});

test('Save view saves the Images toolbar and Refine filters, and its sidebar row reopens them', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await page.goto(`${workspace}#/images`);
    await expect(imageCards(page)).toHaveCount(2);
    // The concept's sidebar group: SAVED VIEWS without its PROPOSED badge (D49), empty until a view is saved.
    await expect(page.locator('aside.sidebar .sidebar-label')).toHaveText(['WORKSPACE', 'SAVED VIEWS']);
    await expect(savedNav(page).locator('a')).toHaveCount(0);

    await search(page).fill('dogs');
    await expect(imageCards(page)).toHaveCount(1);
    await sortBy(page).selectOption('name');
    await refine(page, { Source: '1', Media: 'image' });
    await expect(filtersButton(page)).toHaveText('Filters · 2');
    const saveButton = toolbar(page).getByRole('button', { name: 'Save view', exact: true });
    await saveButton.click();
    // The concept's simpleInput: "View name", the search's default name, Cancel and the primary action.
    const dialog = saveDialog(page);
    await expect(dialog.getByRole('textbox', { name: 'View name', exact: true })).toHaveValue('Search: dogs');
    await expect(dialog.getByRole('textbox', { name: 'View name', exact: true })).toBeFocused();
    await expect(dialog.locator('.dialog-actions button')).toHaveText(['Cancel', 'Save']);
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(saveButton).toBeFocused();
    expect(await listed(request)).toEqual([]);

    await saveButton.click();
    await dialog.getByRole('textbox', { name: 'View name', exact: true }).fill('Dog shots');
    await dialog.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(toast(page)).toHaveText('Saved view added to the sidebar.');
    const [view] = await listed(request);
    expect(view).toMatchObject({ page: 'images', name: 'Dog shots',
        state: { ...IMAGE_DEFAULTS, q: 'dogs', sort: 'name', source: '1', media: 'image' } });
    // The concept's links() row: the search icon, then the name, and no count.
    const row = savedNav(page).getByRole('link', { name: 'Dog shots', exact: true });
    await expect(row).toHaveAttribute('href', `#/saved/${view.id}`);
    expect(await row.evaluate(link => [...link.childNodes].map(node => node.nodeType === Node.TEXT_NODE ? `text:${node.textContent}`
        : `${node.localName}.${node.getAttribute('class')}`))).toEqual(['svg.icon', 'text:Dog shots']);
    await expect(row).not.toHaveAttribute('aria-current', 'page');

    // Another page, then the saved row: its page opens with every saved value and the Filters count (D44).
    await page.getByRole('navigation', { name: 'Workspace', exact: true }).getByRole('link', { name: /^Prompt library/ }).click();
    await expect(promptCards(page).first()).toBeVisible();
    await row.click();
    await expect(page).toHaveURL(new RegExp(`#/saved/${view.id}$`));
    await expect(main(page).getByRole('heading', { level: 1 })).toHaveText('Dog shots');
    await expect(main(page).getByRole('heading', { level: 1 })).toBeFocused();
    await expect(page.locator('#workspace-breadcrumb')).toHaveText('Dog shots');
    await expect(page).toHaveTitle('Dog shots · Prompt Manager');
    await expect(row).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('aside.sidebar nav a[aria-current]')).toHaveCount(1);
    await expect(search(page)).toHaveValue('dogs');
    await expect(sortBy(page)).toHaveValue('name');
    await expect(filtersButton(page)).toHaveText('Filters · 2');
    await expect(imageCards(page)).toHaveCount(1);
    await expect(imageCards(page)).toHaveAttribute('data-image-key', '1:shared/same.png');
    await filtersButton(page).click();
    await expect(refineDialog(page).getByLabel('Source', { exact: true })).toHaveValue('1');
    await expect(refineDialog(page).getByLabel('Media', { exact: true })).toHaveValue('image');
    await page.keyboard.press('Escape');

    // A reload opens the same view from its link.
    await page.reload();
    await expect(main(page).getByRole('heading', { level: 1 })).toHaveText('Dog shots');
    await expect(imageCards(page)).toHaveCount(1);
    await expect(savedNav(page).getByRole('link', { name: 'Dog shots', exact: true })).toHaveAttribute('aria-current', 'page');
});

test('an empty name is Saved view, and a Library view reopens the shelf filters', async ({ page, request }) => {
    await page.goto(`${workspace}#/images`);
    await expect(imageCards(page)).toHaveCount(2);
    await toolbar(page).getByRole('button', { name: 'Save view', exact: true }).click();
    const name = saveDialog(page).getByRole('textbox', { name: 'View name', exact: true });
    await expect(name).toHaveValue('My workspace view');
    await name.fill('   ');
    await saveDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
    await expect(savedNav(page).getByRole('link')).toHaveText(['Saved view']);
    expect((await listed(request))[0]).toMatchObject({ page: 'images', name: 'Saved view', state: IMAGE_DEFAULTS });

    await page.goto(`${workspace}#/library`);
    await expect(promptCards(page)).toHaveCount(2);
    await search(page).fill('dog');
    await expect(promptCards(page)).toHaveCount(1);
    await refine(page, { Tags: 'dogs' });
    await expect(filtersButton(page)).toHaveText('Filters · 1');
    await toolbar(page).getByRole('button', { name: 'Save view', exact: true }).click();
    await expect(name).toHaveValue('Search: dog');
    await saveDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
    await expect(savedNav(page).getByRole('link')).toHaveText(['Saved view', 'Search: dog']);
    const library = (await listed(request))[1];
    expect(library).toMatchObject({ page: 'library', name: 'Search: dog', state: { q: 'dog', category: '', sort: 'newest', tags: ['dogs'] } });

    await page.goto(`${workspace}#/images`);
    await expect(imageCards(page)).toHaveCount(2);
    await savedNav(page).getByRole('link', { name: 'Search: dog', exact: true }).click();
    await expect(main(page).getByRole('heading', { level: 1 })).toHaveText('Search: dog');
    await expect(search(page)).toHaveValue('dog');
    await expect(filtersButton(page)).toHaveText('Filters · 1');
    await expect(promptCards(page)).toHaveCount(1);
    // Saved views open the page; one saved view to another re-renders with the other's state.
    await savedNav(page).getByRole('link', { name: 'Saved view', exact: true }).click();
    await expect(main(page).getByRole('heading', { level: 1 })).toHaveText('Saved view');
    await expect(search(page)).toHaveValue('');
    await expect(imageCards(page)).toHaveCount(2);
});

test('a saved value the page no longer accepts stays default while the rest applies', async ({ page, request }) => {
    const view = await create(request, { page: 'images', name: 'Gone source',
        state: { ...IMAGE_DEFAULTS, q: 'cat', sort: 'name-desc', source: '7', folder: 'missing/folder' } });
    await page.goto(`${workspace}#/saved/${view.id}`);
    await expect(main(page).getByRole('heading', { level: 1 })).toHaveText('Gone source');
    await expect(imageCards(page)).toHaveCount(1);
    await expect(imageCards(page)).toHaveAttribute('data-image-key', '0:shared/same.png');
    await expect(search(page)).toHaveValue('cat');
    await expect(sortBy(page)).toHaveValue('name-desc');
    // The missing Source and Folder are dropped, so no refinement is active.
    await expect(filtersButton(page)).toHaveText('Filters');
    await expect(main(page).locator('.filter-row > span.chip')).toHaveText('All output folders');

    // A Source that does exist, with a folder it lists, applies.
    const kept = await create(request, { page: 'images', name: 'Input shared',
        state: { ...IMAGE_DEFAULTS, source: '1', folder: 'shared' } });
    await page.goto(`${workspace}#/saved/${kept.id}`);
    await expect(main(page).getByRole('heading', { level: 1 })).toHaveText('Input shared');
    await expect(filtersButton(page)).toHaveText('Filters · 2');
    await expect(imageCards(page)).toHaveCount(1);
    await expect(imageCards(page)).toHaveAttribute('data-image-key', '1:shared/same.png');
});

test('an unknown saved view opens Images, and a failed listing offers Retry', async ({ page, request }) => {
    await page.goto(`${workspace}#/saved/999`);
    await expect(page).toHaveURL(/#\/images$/);
    await expect(main(page).getByRole('heading', { level: 1 })).toHaveText('Images');

    const view = await create(request, { page: 'library', name: 'Cats', state: { q: 'cat', category: '', sort: 'newest', tags: [] } });
    let failing = true;
    await page.route(`**${API}`, route => failing && route.request().method() === 'GET'
        ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"success":false,"error":"down"}' })
        : route.fallback());
    await page.goto(`${workspace}#/saved/${view.id}`);
    await expect(main(page).getByRole('alert')).toHaveText('Saved views could not be loaded. Your saved data has not changed.');
    await expect(savedNav(page).locator('a')).toHaveCount(0);
    failing = false;
    await main(page).getByRole('button', { name: 'Retry', exact: true }).click();
    await expect(main(page).getByRole('heading', { level: 1 })).toHaveText('Cats');
    await expect(search(page)).toHaveValue('cat');
    await expect(promptCards(page)).toHaveCount(1);
    await expect(savedNav(page).getByRole('link')).toHaveText(['Cats']);
});

test('a failed save says so and adds nothing', async ({ page, request }) => {
    await page.goto(`${workspace}#/images`);
    await expect(imageCards(page)).toHaveCount(2);
    await page.route(`**${API}`, route => route.request().method() === 'POST'
        ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"success":false,"error":"Saved view operation failed"}' })
        : route.fallback());
    await toolbar(page).getByRole('button', { name: 'Save view', exact: true }).click();
    await saveDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
    await expect(toast(page)).toHaveText('The view could not be saved. Try again.');
    await expect(savedNav(page).locator('a')).toHaveCount(0);
    expect(await listed(request)).toEqual([]);
});
