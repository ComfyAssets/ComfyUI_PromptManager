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
// The saved views' own rows, after the Favorites row (D51).
const viewRows = page => savedNav(page).locator('a[data-route^="saved/"]');
const favoritesRow = page => savedNav(page).locator('a[data-route="favorites"]');
const favoritesCount = page => favoritesRow(page).locator('.nav-count');
const editDialog = page => page.getByRole('dialog', { name: 'Edit saved view', exact: true });
const deleteDialog = page => page.getByRole('dialog', { name: 'Delete saved view', exact: true });
const heading = page => main(page).getByRole('heading', { level: 1 });
const saveDialog = page => page.getByRole('dialog', { name: 'Save this view', exact: true });
const refineDialog = page => page.getByRole('dialog', { name: 'Refine this view', exact: true });
const toast = page => page.locator('#workspace-toast');
const IMAGE_DEFAULTS = { q: '', category: '', sort: 'newest', source: 'all', folder: '', media: 'all', tags: [], favorites: false };

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
    // D51: deleting asks inside the dialog; no browser dialog (window.confirm) may ever open.
    page.on('dialog', dialog => { page.savedErrors.push(`browser ${dialog.type()}`); dialog.dismiss().catch(() => {}); });
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
    await expect(viewRows(page)).toHaveCount(0);

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
    await expect(viewRows(page)).toHaveText(['Saved view']);
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
    await expect(viewRows(page)).toHaveText(['Saved view', 'Search: dog']);
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

test('a view stored under older rules still lists and opens with its usable values (#249 I1)', async ({ page, request }) => {
    // No favorites key, a retired key and a sort that is no longer offered, as an older build stored it.
    const raw = async data => {
        const response = await request.post('/__test__/saved-views/raw', { data });
        await expect(response).toBeOK();
        return (await response.json()).id;
    };
    const imagesId = await raw({ page: 'images', name: 'Old cats', state: JSON.stringify({ q: 'cat', sort: 'shuffle', layout: 'grid' }) });
    await raw({ page: 'library', name: '', state: JSON.stringify({ q: 'dog', sort: 'name-desc' }) });
    expect(await listed(request)).toEqual([
        { id: imagesId, page: 'images', name: 'Old cats', state: { ...IMAGE_DEFAULTS, q: 'cat' } },
        expect.objectContaining({ page: 'library', name: 'Saved view', state: { q: 'dog', category: '', sort: 'newest', tags: [] } }),
    ]);
    await page.goto(`${workspace}#/saved/${imagesId}`);
    await expect(heading(page)).toHaveText('Old cats');
    await expect(viewRows(page)).toHaveText(['Old cats', 'Saved view']);
    await expect(search(page)).toHaveValue('cat');
    await expect(sortBy(page)).toHaveValue('newest');
    await expect(imageCards(page)).toHaveCount(1);
    await savedNav(page).getByRole('link', { name: 'Saved view', exact: true }).click();
    await expect(heading(page)).toHaveText('Saved view');
    await expect(search(page)).toHaveValue('dog');
    await expect(promptCards(page)).toHaveCount(1);
});

test('leaving a saved view returns Images and the Library to their own last state (#249 M2)', async ({ page, request }) => {
    const dogs = await create(request, { page: 'images', name: 'Dogs', state: { ...IMAGE_DEFAULTS, q: 'dogs' } });
    const cats = await create(request, { page: 'library', name: 'Cats', state: { q: 'cat', category: '', sort: 'newest', tags: [] } });
    await page.goto(`${workspace}#/images`);
    await search(page).fill('cat');
    await expect(imageCards(page)).toHaveCount(1);
    await viewRows(page).filter({ hasText: 'Dogs' }).click();
    await expect(heading(page)).toHaveText('Dogs');
    await expect(search(page)).toHaveValue('dogs');
    await page.locator('#primary-nav a[data-route="images"]').click();
    await expect(heading(page)).toHaveText('Images');
    await expect(search(page)).toHaveValue('cat');
    await expect(imageCards(page)).toHaveCount(1);

    await page.locator('#primary-nav a[data-route="library"]').click();
    await expect(promptCards(page)).toHaveCount(2);
    await search(page).fill('dog');
    await expect(promptCards(page)).toHaveCount(1);
    await page.goto(`${workspace}#/saved/${cats.id}`);
    await expect(heading(page)).toHaveText('Cats');
    await expect(search(page)).toHaveValue('cat');
    await page.locator('#primary-nav a[data-route="library"]').click();
    await expect(heading(page)).toHaveText('Prompt library');
    await expect(search(page)).toHaveValue('dog');
    await expect(promptCards(page)).toHaveCount(1);
    expect(dogs.id).not.toBe(cats.id);
});

test('a toolbar change while sources load keeps the saved Source and Folder (#249 M3)', async ({ page, request }) => {
    const view = await create(request, { page: 'images', name: 'Input shared', state: { ...IMAGE_DEFAULTS, source: '1', folder: 'shared' } });
    let release;
    const held = new Promise(resolve => { release = resolve; });
    await page.route('**/prompt_manager/gallery/subfolders?**', async route => { await held; await route.fallback(); });
    await page.goto(`${workspace}#/saved/${view.id}`);
    await expect(heading(page)).toHaveText('Input shared');
    await sortBy(page).selectOption('name');
    await expect(imageCards(page)).toHaveCount(0);
    release();
    await expect(filtersButton(page)).toHaveText('Filters · 2');
    await expect(sortBy(page)).toHaveValue('name');
    await expect(imageCards(page)).toHaveCount(1);
    await expect(imageCards(page)).toHaveAttribute('data-image-key', '1:shared/same.png');
});

test('a saved view left while its list or rename is pending leaves the next page alone', async ({ page, request }) => {
    const view = await create(request, { page: 'library', name: 'Cats', state: { q: 'cat', category: '', sort: 'newest', tags: [] } });
    let releaseList = () => {};
    const holdList = async () => {
        const held = new Promise(resolve => { releaseList = resolve; });
        await page.unroute(`**${API}`);
        await page.route(`**${API}`, async route => { if (route.request().method() === 'GET') await held; await route.fallback(); });
    };
    // Leaving while the shell's first read is pending: the view never mounts over the page left to.
    await holdList();
    await page.goto(`${workspace}#/saved/${view.id}`);
    await page.evaluate(() => { window.location.hash = '#/tags'; });
    await expect(heading(page)).toHaveText('Tags & categories');
    releaseList();
    await expect(viewRows(page)).toHaveText(['Cats']);
    await expect(heading(page)).toHaveText('Tags & categories');
    // An unknown id re-reads the list once; leaving during that read keeps the page left to.
    await holdList();
    await page.evaluate(() => { window.location.hash = '#/saved/999'; });
    await page.evaluate(() => { window.location.hash = '#/library'; });
    await expect(heading(page)).toHaveText('Prompt library');
    releaseList();
    await expect(viewRows(page)).toHaveText(['Cats']);
    await expect(page).toHaveURL(/#\/library$/);
    await expect(heading(page)).toHaveText('Prompt library');
    await page.unroute(`**${API}`);

    // A rename that lands after the view was left does not retitle the next page.
    let releaseRename;
    const renameHeld = new Promise(resolve => { releaseRename = resolve; });
    await page.route(`**${API}/${view.id}`, async route => { await renameHeld; await route.fallback(); });
    await page.goto(`${workspace}#/saved/${view.id}`);
    await expect(heading(page)).toHaveText('Cats');
    await main(page).locator('.heading-actions > button').first().click();
    await editDialog(page).getByRole('textbox', { name: 'View name', exact: true }).fill('Kittens');
    await editDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
    await page.locator('#primary-nav a[data-route="images"]').click();
    await expect(heading(page)).toHaveText('Images');
    releaseRename();
    await expect(viewRows(page)).toHaveText(['Kittens']);
    await expect(heading(page)).toHaveText('Images');
    await expect(page).toHaveTitle('Images · Prompt Manager');
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
    await expect(viewRows(page)).toHaveCount(0);
    failing = false;
    await main(page).getByRole('button', { name: 'Retry', exact: true }).click();
    await expect(main(page).getByRole('heading', { level: 1 })).toHaveText('Cats');
    await expect(search(page)).toHaveValue('cat');
    await expect(promptCards(page)).toHaveCount(1);
    await expect(viewRows(page)).toHaveText(['Cats']);
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
    await expect(viewRows(page)).toHaveCount(0);
    expect(await listed(request)).toEqual([]);
});

async function star(page, key) {
    await page.locator(`article[data-image-key="${key}"] .star-button`).click();
}

test('Favorites is the first SAVED VIEWS row, with a real count that follows every star', async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    let release;
    const held = new Promise(resolve => { release = resolve; });
    // Hidden until its read settles: never a placeholder "0".
    await page.route(url => url.pathname.endsWith('/prompt_manager/images/output') && url.searchParams.get('favorites') === '1'
        && url.searchParams.get('limit') === '1', async route => { await held; await route.fallback(); }, { times: 1 });
    await page.goto(`${workspace}#/images`);
    await expect(imageCards(page)).toHaveCount(2);
    await expect(favoritesRow(page)).toHaveAttribute('href', '#/favorites');
    await expect(favoritesCount(page)).toHaveCount(0);
    release();
    // The concept's row (app.js a332): the star icon, "Favorites", then the count.
    await expect(favoritesCount(page)).toHaveText('0');
    expect(await favoritesRow(page).evaluate(link => [...link.childNodes].map(node => node.nodeType === Node.TEXT_NODE
        ? `text:${node.textContent}` : `${node.localName}.${node.getAttribute('class')}`)))
        .toEqual(['svg.icon', 'text:Favorites', 'span.nav-count']);
    expect(await savedNav(page).locator('a').evaluateAll(links => links.map(link => link.dataset.route))).toEqual(['favorites']);

    await star(page, '1:shared/same.png');
    await expect(toast(page)).toHaveText('Added to favorites.');
    await expect(favoritesCount(page)).toHaveText('1');

    // #/favorites: Images filtered to favorites, named Favorites (concept renderImages a417).
    const listings = [];
    page.on('request', item => {
        const url = new URL(item.url());
        if (url.pathname.endsWith('/prompt_manager/images/output') && url.searchParams.get('limit') !== '1') listings.push(url.searchParams);
    });
    await favoritesRow(page).click();
    await expect(page).toHaveURL(/#\/favorites$/);
    await expect(heading(page)).toHaveText('Favorites');
    await expect(heading(page)).toBeFocused();
    await expect(page.locator('#workspace-breadcrumb')).toHaveText('Favorites');
    await expect(page).toHaveTitle('Favorites · Prompt Manager');
    await expect(favoritesRow(page)).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('aside.sidebar nav a[aria-current]')).toHaveCount(1);
    await expect(imageCards(page)).toHaveCount(1);
    await expect(imageCards(page)).toHaveAttribute('data-image-key', '1:shared/same.png');
    await expect(main(page).locator('#result-count')).toHaveText(/^1 of 1 image/);
    expect(listings.at(-1).get('favorites')).toBe('1');

    // Unstarring on Favorites keeps the card (with ☆) so it can be undone in place; the count follows.
    await star(page, '1:shared/same.png');
    await expect(toast(page)).toHaveText('Removed from favorites.');
    await expect(imageCards(page).locator('.star-button')).toHaveAttribute('aria-pressed', 'false');
    await expect(favoritesCount(page)).toHaveText('0');
    await star(page, '1:shared/same.png');
    await expect(favoritesCount(page)).toHaveText('1');

    // Favorites never replaces the Images return state: Images still lists everything.
    await page.getByRole('navigation', { name: 'Workspace', exact: true }).getByRole('link', { name: /^Images/ }).click();
    await expect(heading(page)).toHaveText('Images');
    await expect(imageCards(page)).toHaveCount(2);
    await page.goto(`${workspace}#/favorites`);
    await expect(heading(page)).toHaveText('Favorites');
    await expect(imageCards(page)).toHaveCount(1);
});

test('a failed Favorites count shows no badge', async ({ page }) => {
    await page.route(url => url.pathname.endsWith('/prompt_manager/images/output') && url.searchParams.get('favorites') === '1'
        && url.searchParams.get('limit') === '1', route => route.fulfill({ status: 503, json: { success: false, error: 'Unavailable' } }));
    await page.goto(`${workspace}#/images`);
    await expect(page.locator('#primary-nav a[data-route="images"] .nav-count')).toHaveText('2');
    await expect(favoritesRow(page)).toBeVisible();
    await expect(favoritesCount(page)).toHaveCount(0);
    // The refused count read is this test's own; nothing else failed.
    page.savedErrors = page.savedErrors.filter(error => !/^503 .*favorites=1/.test(error));
});

test('Save view on Favorites reopens the favorites', async ({ page, request }) => {
    await page.goto(`${workspace}#/images`);
    await expect(imageCards(page)).toHaveCount(2);
    await star(page, '0:shared/same.png');
    await expect(favoritesCount(page)).toHaveText('1');
    await page.goto(`${workspace}#/favorites`);
    await expect(imageCards(page)).toHaveCount(1);
    await toolbar(page).getByRole('button', { name: 'Save view', exact: true }).click();
    await saveDialog(page).getByRole('textbox', { name: 'View name', exact: true }).fill('Best');
    await saveDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
    await expect(toast(page)).toHaveText('Saved view added to the sidebar.');
    const [view] = await listed(request);
    expect(view).toMatchObject({ page: 'images', name: 'Best', state: { ...IMAGE_DEFAULTS, favorites: true } });
    await expect(viewRows(page)).toHaveText(['Best']);
    await page.goto(`${workspace}#/images`);
    await expect(imageCards(page)).toHaveCount(2);
    await viewRows(page).click();
    await expect(heading(page)).toHaveText('Best');
    await expect(imageCards(page)).toHaveCount(1);
    await expect(imageCards(page)).toHaveAttribute('data-image-key', '0:shared/same.png');
});

test('Edit renames an open view, and deletes it only after confirming inside the dialog', async ({ page, request }) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    const view = await create(request, { page: 'images', name: 'Dogs', state: { ...IMAGE_DEFAULTS, q: 'dogs' } });
    await page.goto(`${workspace}#/images`);
    await expect(imageCards(page)).toHaveCount(2);
    // Plain pages have no Edit.
    await expect(main(page).locator('.heading-actions').getByRole('button', { name: 'Edit', exact: true })).toHaveCount(0);
    await viewRows(page).click();
    await expect(heading(page)).toHaveText('Dogs');
    // D51: Edit leads the heading's actions of an open view.
    const edit = main(page).locator('.heading-actions > button').first();
    await expect(edit).toHaveText('Edit');
    await edit.click();
    const dialog = editDialog(page);
    const name = dialog.getByRole('textbox', { name: 'View name', exact: true });
    await expect(name).toHaveValue('Dogs');
    await expect(name).toBeFocused();
    await expect(dialog.locator('.dialog-actions button')).toHaveText(['Delete…', 'Cancel', 'Save']);
    await expect(dialog.getByRole('button', { name: 'Delete…', exact: true })).toHaveClass(/\bdanger\b/);
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(edit).toBeFocused();

    // An empty name keeps the dialog open on its field.
    await edit.click();
    await name.fill('  ');
    await dialog.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(name).toBeFocused();
    await name.fill('Good dogs');
    await dialog.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(toast(page)).toHaveText('Saved view renamed.');
    await expect(viewRows(page)).toHaveText(['Good dogs']);
    await expect(heading(page)).toHaveText('Good dogs');
    await expect(page.locator('#workspace-breadcrumb')).toHaveText('Good dogs');
    await expect(page).toHaveTitle('Good dogs · Prompt Manager');
    await expect(edit).toBeFocused();
    expect(await listed(request)).toEqual([{ ...view, name: 'Good dogs' }]);
    // The rename kept the view's page state.
    await expect(search(page)).toHaveValue('dogs');
    await expect(imageCards(page)).toHaveCount(1);

    // Delete… asks first; its Cancel deletes nothing.
    await edit.click();
    await dialog.getByRole('button', { name: 'Delete…', exact: true }).click();
    const confirm = deleteDialog(page);
    await expect(confirm).toContainText('Delete “Good dogs”? Only the saved view is removed; its images and prompts stay.');
    await expect(confirm.locator('.dialog-actions button')).toHaveText(['Cancel', 'Delete']);
    await expect(confirm.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused();
    await confirm.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(confirm).toHaveCount(0);
    expect(await listed(request)).toHaveLength(1);

    await edit.click();
    await dialog.getByRole('button', { name: 'Delete…', exact: true }).click();
    await confirm.getByRole('button', { name: 'Delete', exact: true }).click();
    await expect(toast(page)).toHaveText('Saved view deleted.');
    await expect(page).toHaveURL(/#\/images$/);
    await expect(heading(page)).toHaveText('Images');
    await expect(viewRows(page)).toHaveCount(0);
    expect(await listed(request)).toEqual([]);
    // The dead link left no history entry.
    await page.goBack();
    await expect(page).not.toHaveURL(new RegExp(`#/saved/${view.id}$`));
});

test('a deleted Library view returns to the Library, and failed edits say so', async ({ page, request }) => {
    const view = await create(request, { page: 'library', name: 'Cats', state: { q: 'cat', category: '', sort: 'newest', tags: [] } });
    await page.goto(`${workspace}#/saved/${view.id}`);
    await expect(heading(page)).toHaveText('Cats');
    const edit = main(page).locator('.heading-actions > button').first();
    await expect(edit).toHaveText('Edit');
    let failing = true;
    await page.route(`**${API}/${view.id}`, route => failing
        ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"success":false,"error":"Saved view operation failed"}' })
        : route.fallback());
    await edit.click();
    await editDialog(page).getByRole('textbox', { name: 'View name', exact: true }).fill('Kittens');
    await editDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
    await expect(toast(page)).toHaveText('The view could not be renamed. Try again.');
    await expect(heading(page)).toHaveText('Cats');
    await expect(viewRows(page)).toHaveText(['Cats']);
    await edit.click();
    await editDialog(page).getByRole('button', { name: 'Delete…', exact: true }).click();
    await deleteDialog(page).getByRole('button', { name: 'Delete', exact: true }).click();
    await expect(toast(page)).toHaveText('The view could not be deleted. Try again.');
    await expect(page).toHaveURL(new RegExp(`#/saved/${view.id}$`));
    expect(await listed(request)).toHaveLength(1);

    failing = false;
    await edit.click();
    await editDialog(page).getByRole('button', { name: 'Delete…', exact: true }).click();
    await deleteDialog(page).getByRole('button', { name: 'Delete', exact: true }).click();
    await expect(toast(page)).toHaveText('Saved view deleted.');
    await expect(page).toHaveURL(/#\/library$/);
    await expect(heading(page)).toHaveText('Prompt library');
    await expect(viewRows(page)).toHaveCount(0);
});
