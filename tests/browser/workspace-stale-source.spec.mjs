// #430: an open workspace page whose sources changed under it. A ComfyUI restart no longer changes any scope (the
// key is persisted, see tests/test_api_workspace_restart.py); what is left is a real source change. Renumbered
// sources (`/__test__/sources/reverse`) are found again: the Tag image dialog stays open on its file and works, and
// the panel keeps the file. A scope that cannot be mapped (`/__test__/scope-key/rotate`, a lost key) blocks Generate
// and Save visibly, says why beside them and offers Refresh. Both commands are undone by every reset.
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';

const images = '/prompt_manager/workspace#/images';
const FIRST = 'tagging/dunes_00001.png', SECOND = 'tagging/dunes_00002.png';
const SOURCE_CHANGED = 'The source changed. Refresh the image collection before tagging this file.';
const card = (page, path, root = 0) => page.locator(`article[data-image-key="${root}:${path}"]`);
const cards = page => page.locator('.gallery-body article[data-image-key]');
const tagButton = (page, path, root = 0) => card(page, path, root).getByRole('button', { name: `Tag or auto-tag ${path.split('/').pop()}`, exact: true });
const infoButton = (page, path, root = 0) => card(page, path, root).getByRole('button', { name: `Inspect ${path.split('/').pop()}`, exact: true });
const dialog = page => page.getByRole('dialog', { name: 'Tag image', exact: true });
const manual = page => dialog(page).getByRole('textbox', { name: 'Image tags · edit manually', exact: true });
const generate = page => dialog(page).getByRole('button', { name: 'Generate suggestions', exact: true });
const primary = page => dialog(page).locator('.dialog-actions .ca-primary');
const notice = page => dialog(page).locator('.dialog-actions > .tag-action-notice');
const status = page => dialog(page).locator('#tag-generation-status');
const chips = page => dialog(page).locator('#tag-suggestions .chip.tag-suggestion');
const panel = page => page.getByRole('complementary', { name: 'Selected image details', exact: true });
const toast = page => page.locator('#workspace-toast');
const answered = (page, suffix, method = 'POST') => page.waitForResponse(item => new URL(item.url()).pathname.endsWith(suffix) && item.request().method() === method);

test.beforeEach(async ({ page, request, browserName }) => {
    await startCoverage(page, browserName);
    await expect(await request.post('/__test__/tagging/reset', { data: {} })).toBeOK();
    page.staleErrors = [];
    page.on('pageerror', error => page.staleErrors.push(error.message));
});
test.afterEach(async ({ page, request, browserName }) => {
    await saveCoverage(page, browserName);
    expect(page.staleErrors).toEqual([]);
    // The next spec file starts on the standard sources and key.
    await expect(await request.post('/__test__/tagging/reset', { data: {} })).toBeOK();
});

async function openSettled(page, url) {
    // The page reads its selected file's details as soon as it lists. A source change before that read is answered
    // makes the page find the file again on its own, so the test's old-numbering locators are gone before they are
    // used (#484): every test changes sources only after the read was answered.
    const inspected = page.waitForResponse(item => new URL(item.url()).pathname.startsWith('/prompt_manager/images/inspect/') &&
        item.request().method() === 'GET');
    await page.goto(url);
    expect((await inspected).status()).toBe(200);
}
async function openTag(page, path = FIRST, root = 0) {
    const observed = answered(page, '/image-annotations/observe');
    await tagButton(page, path, root).click();
    await expect(dialog(page)).toBeVisible();
    return observed;
}
async function savedTags(request, path, root) {
    const listing = await (await request.get('/prompt_manager/images/output?mode=workspace&limit=40')).json();
    const item = listing.images.find(image => image.path === path && image.root_index === root);
    const target = { root_index: root, root_scope: item.root_scope, path };
    const observed = await (await request.post('/prompt_manager/image-annotations/observe', { data: { target } })).json();
    await request.delete(`/prompt_manager/image-annotations/capture/${observed.capture.token}`);
    return observed.tags;
}

test('renumbered sources under an open Tag dialog: it finds the file again, keeps the draft and Generate works', async ({ page, request }) => {
    await openSettled(page, images);
    await expect(card(page, FIRST)).toHaveClass(/\bselected\b/);
    expect((await openTag(page)).status()).toBe(200);
    await manual(page).fill('kept_draft');
    await expect(await request.post('/__test__/sources/reverse')).toBeOK();

    // Generate is refused (the held scope names another index now); the page asks where the file is and retargets.
    const refused = answered(page, '/image-annotations/suggest');
    const rescoped = answered(page, '/images/rescope');
    const observedAgain = answered(page, '/image-annotations/observe');
    await generate(page).click();
    expect((await refused).status()).toBe(409);
    const mapped = await (await rescoped).json();
    expect([mapped.image.root_index, mapped.image.path]).toEqual([1, FIRST]);
    const observed = await observedAgain;
    expect(observed.status()).toBe(200);
    expect(observed.request().postDataJSON().target).toEqual({ root_index: 1, root_scope: mapped.image.root_scope, path: FIRST });
    await expect(dialog(page)).toBeVisible();
    await expect(notice(page)).toHaveCount(0);
    await expect(manual(page)).toHaveValue('kept_draft');
    await expect(dialog(page).locator('img.tag-image-preview')).toHaveAttribute('src', /\/image-annotations\/capture\//);

    // The page listed again under the new numbering and kept the file selected.
    await expect(card(page, FIRST, 1)).toHaveClass(/\bselected\b/);
    await expect(card(page, FIRST, 0)).toHaveCount(0);

    const suggested = answered(page, '/image-annotations/suggest');
    await generate(page).click();
    expect((await suggested).status()).toBe(200);
    await expect(chips(page).first()).toBeVisible();
    const saved = answered(page, '/image-annotations/tags', 'PUT');
    await primary(page).click();
    expect((await saved).request().postDataJSON().target.root_index).toBe(1);
    await expect(toast(page)).toContainText('tags saved for dunes_00001.png.');
    expect(await savedTags(request, FIRST, 1)).toContain('kept_draft');
});

test('the inspector finds a renumbered file again; checked files from the old numbering are cleared', async ({ page, request }) => {
    await openSettled(page, images);
    await expect(card(page, FIRST)).toHaveClass(/\bselected\b/);
    await card(page, FIRST).getByRole('checkbox', { name: /^Select / }).check();
    await expect(page.locator('.bulkbar')).toBeVisible();
    await expect(await request.post('/__test__/sources/reverse')).toBeOK();

    const refused = page.waitForResponse(item => new URL(item.url()).pathname === `/prompt_manager/images/inspect/${SECOND}` &&
        new URL(item.url()).searchParams.get('root') === '0');
    const reread = page.waitForResponse(item => new URL(item.url()).pathname === `/prompt_manager/images/inspect/${SECOND}` &&
        new URL(item.url()).searchParams.get('root') === '1');
    await infoButton(page, SECOND).click();
    expect((await refused).status()).toBe(409);
    expect((await reread).status()).toBe(200);
    await expect(card(page, SECOND, 1)).toHaveClass(/\bselected\b/);
    await expect(panel(page)).not.toContainText('The source changed');
    await expect(panel(page).getByRole('button', { name: 'Refresh image collection', exact: true })).toHaveCount(0);
    // A checked key of the old list could name a same-named file in another source: nothing stays checked.
    await expect(page.locator('.bulkbar')).toBeHidden();
    await expect(cards(page).getByRole('checkbox', { checked: true })).toHaveCount(0);
});

test('a scope that cannot be mapped: Generate and Save are visibly disabled, the reason and Refresh sit beside them', async ({ page }) => {
    await openSettled(page, images);
    await expect(card(page, FIRST)).toBeVisible();
    await expect(await page.request.post('/__test__/scope-key/rotate')).toBeOK();
    const rescoped = answered(page, '/images/rescope');
    expect((await openTag(page)).status()).toBe(409);
    expect((await rescoped).status()).toBe(409);

    await expect(notice(page)).toContainText(SOURCE_CHANGED);
    await expect(status(page)).toHaveText('');
    const refresh = notice(page).getByRole('button', { name: 'Refresh image collection', exact: true });
    await expect(refresh).toBeVisible();
    for (const button of [generate(page), primary(page)]) {
        await expect(button).toHaveAttribute('aria-disabled', 'true');
        await expect(button).toHaveCSS('opacity', '0.55');
    }
    // The notice is its own first row of the sticky actions, above the buttons.
    const rows = await dialog(page).locator('.dialog-actions').evaluate(actions => {
        const noticeBox = actions.firstElementChild.getBoundingClientRect();
        return { first: actions.firstElementChild.className, below: [...actions.querySelectorAll(':scope > button')].every(button => button.getBoundingClientRect().top >= noticeBox.bottom - 1) };
    });
    expect(rows).toEqual({ first: 'tag-action-notice', below: true });
    const preview = dialog(page).locator('.tag-image-preview');
    await expect(preview).toHaveText('Preview unavailable');
    await expect(preview).toHaveAttribute('role', 'img');
    const suggest = [];
    page.on('request', item => { if (item.url().endsWith('/image-annotations/suggest')) suggest.push(item.url()); });
    // Pressed anyway (Playwright refuses an aria-disabled target, so the press is dispatched), nothing is sent.
    await generate(page).dispatchEvent('click');
    await primary(page).dispatchEvent('click');
    expect(suggest).toEqual([]);
    await page.screenshot({ path: test.info().outputPath('stale-source-tag-dialog.png') });

    // Refresh closes the dialog and lists again with the current scopes; Tag then works.
    const relisted = page.waitForResponse(item => item.url().includes('/images/output?'));
    await refresh.click();
    await expect(dialog(page)).toHaveCount(0);
    await relisted;
    await expect(card(page, FIRST)).toBeVisible();
    expect((await openTag(page)).status()).toBe(200);
    await expect(notice(page)).toHaveCount(0);
    await expect(generate(page)).not.toHaveAttribute('aria-disabled', 'true');
});

test('an unmappable inspector read keeps its visible Refresh, and Refresh recovers the panel', async ({ page }) => {
    await openSettled(page, images);
    await expect(card(page, FIRST)).toHaveClass(/\bselected\b/);
    await expect(await page.request.post('/__test__/scope-key/rotate')).toBeOK();
    const rescoped = answered(page, '/images/rescope');
    await infoButton(page, SECOND).click();
    expect((await rescoped).status()).toBe(409);
    await expect(panel(page)).toContainText('The source changed. Refresh the image collection before inspecting this file.');
    const relisted = page.waitForResponse(item => item.url().includes('/images/output?'));
    await panel(page).getByRole('button', { name: 'Refresh image collection', exact: true }).click();
    await relisted;
    await expect(cards(page).first()).toHaveClass(/\bselected\b/);
    await expect(panel(page)).not.toContainText('The source changed');
});

test('a Load more cursor the server cannot verify offers Refresh instead of a dead Retry', async ({ page, request }) => {
    await expect(await request.post('/__test__/tagging/reset', { data: { extra_images: 60 } })).toBeOK();
    await openSettled(page, images);
    await expect(cards(page).first()).toBeVisible();
    await expect(page.locator('#gallery-continuation')).toContainText('more load as you scroll');
    await expect(await request.post('/__test__/scope-key/rotate')).toBeOK();
    const stale = page.waitForResponse(item => item.url().includes('/images/output?') && new URL(item.url()).searchParams.has('cursor'));
    await page.locator('#gallery-continuation').scrollIntoViewIfNeeded();
    const answer = await stale;
    expect(answer.status()).toBe(400);
    expect((await answer.json()).code).toBe('cursor-stale');
    const continuation = page.locator('#gallery-continuation');
    await expect(continuation).toContainText('The image collection changed. Refresh image collection to continue.');
    const relisted = page.waitForResponse(item => item.url().includes('/images/output?') && !new URL(item.url()).searchParams.has('cursor'));
    await continuation.getByRole('button', { name: 'Refresh image collection', exact: true }).click();
    expect((await relisted).status()).toBe(200);
    await expect(continuation).not.toContainText('The image collection changed');
});

test('a viewer whose media source was renumbered closes onto the file, kept selected', async ({ page, request }) => {
    await openSettled(page, images);
    await expect(card(page, FIRST)).toHaveClass(/\bselected\b/);
    await expect(await request.post('/__test__/sources/reverse')).toBeOK();
    const rescoped = answered(page, '/images/rescope');
    await card(page, FIRST).locator('.image-open').click();
    const mapped = await (await rescoped).json();
    expect([mapped.image.root_index, mapped.image.path]).toEqual([1, FIRST]);
    await expect(page.locator('#viewer-dialog')).toHaveCount(0);
    await expect(card(page, FIRST, 1)).toHaveClass(/\bselected\b/);
    // Opened again, it shows the file under its current source.
    await card(page, FIRST, 1).locator('.image-open').click();
    await expect(page.locator('#viewer-dialog[open]')).toBeVisible();
    await expect(page.locator('#viewer-dialog').getByText('Media could not be loaded. Retry this file.', { exact: true })).toBeHidden();
    await expect.poll(() => page.locator('#viewer-dialog img').first().evaluate(image => image.complete && image.naturalWidth > 0)).toBe(true);
});

test('a collection page finds a renumbered member again and stays on its members', async ({ page, request }) => {
    // Members are stored by root path, not index (#331), so a renumbering keeps them; the page's scopes go stale.
    const listing = await (await request.get('/prompt_manager/images/output?mode=workspace&limit=40')).json();
    const first = listing.images.find(image => image.path === FIRST && image.root_index === 0);
    const name = `Stale ${test.info().workerIndex}-${Date.now()}`;
    const added = await request.post('/prompt_manager/collections/items', { data: { name, items: [
        { root_index: 0, root_scope: first.root_scope, path: FIRST }] } });
    await expect(added).toBeOK();
    const collections = (await (await request.get('/prompt_manager/collections')).json()).collections;
    const { id } = collections.find(item => item.name === name);
    await openSettled(page, `/prompt_manager/workspace#/collection/${id}`);
    await expect(cards(page)).toHaveCount(1);
    await expect(card(page, FIRST)).toHaveClass(/\bselected\b/);
    await expect(await request.post('/__test__/sources/reverse')).toBeOK();

    const rescoped = answered(page, '/images/rescope');
    const relisted = page.waitForResponse(item => item.url().includes('/images/output?') && new URL(item.url()).searchParams.get('collection') === String(id));
    await card(page, FIRST).locator('.image-open').click();
    expect((await (await rescoped).json()).image.root_index).toBe(1);
    expect((await relisted).status()).toBe(200);
    await expect(page).toHaveURL(new RegExp(`#/collection/${id}$`));
    await expect(cards(page)).toHaveCount(1);
    await expect(card(page, FIRST, 1)).toHaveClass(/\bselected\b/);
    // Tag image works on the member under its new source.
    expect((await openTag(page, FIRST, 1)).status()).toBe(200);
    await expect(notice(page)).toHaveCount(0);
});
