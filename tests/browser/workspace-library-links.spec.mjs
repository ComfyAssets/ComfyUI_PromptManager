// #668 (D207): `workspace#/library?tag=…&tag_mode=…` opens the Prompt library with those Tags and that Tag match,
// against the real server. Old tag bookmarks are sent to this form (old-urls.spec.mjs).
import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import {test,expect} from './fixtures.mjs';

const workspace='/prompt_manager/workspace';
const cards=page=>page.locator('article[data-prompt-id]');
const card=(page,text)=>cards(page).filter({has:page.getByText(text,{exact:true})});
const toolbar=page=>page.locator('#workspace-main search.toolbar');
const filtersButton=page=>toolbar(page).getByRole('button',{name:/^Filters(?: · \d)?$/});
const refine=page=>page.getByRole('dialog',{name:'Refine this view',exact:true});
const caption=page=>page.locator('#workspace-main .section-caption > span').first();
const toast=page=>page.locator('#workspace-toast');
const libraryUrl=/\/prompt_manager\/workspace#\/library$/;

test.beforeEach(async({page,request,browserName})=>{
    await startCoverage(page,browserName);await expect(await request.post('/__test__/reset',{data:{native_library:true}})).toBeOK();
    page.libraryErrors=[];page.on('pageerror',error=>page.libraryErrors.push(error.message));
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.libraryErrors).toEqual([]);});

async function openRefine(page) {
    const folders=page.waitForResponse(item=>new URL(item.url()).pathname==='/prompt_manager/library/options'&&new URL(item.url()).searchParams.get('include')==='folders');
    await filtersButton(page).click();await expect(refine(page)).toBeVisible();await folders;
}

test('a Library link opens the shelf filtered by its tags, shows them in Refine and leaves a plain address',async({page})=>{
    await page.goto(`${workspace}#/library?tag=cats`);
    await expect(caption(page)).toHaveText(/^1 positive prompt · /);
    await expect(cards(page)).toHaveCount(1);await expect(card(page,'Cat portrait')).toHaveCount(1);
    await expect(filtersButton(page)).toHaveText('Filters · 1');
    await expect(page).toHaveURL(libraryUrl);
    await expect(page.locator('#workspace-main h1')).toHaveText('Prompt library');
    // As for any route, the title takes focus on a navigation inside the workspace, never on the first render.
    await expect(page.locator('#workspace-main h1')).not.toBeFocused();
    await openRefine(page);
    await expect(refine(page).getByRole('textbox',{name:'Tags',exact:true})).toHaveValue('cats');
    await expect(refine(page).getByRole('combobox',{name:'Tag match',exact:true})).toHaveValue('all');
});

test('a Library link with two tags matches all of them unless it asks for any',async({page})=>{
    await page.goto(`${workspace}#/library?tag=cats&tag=dogs`);
    await expect(caption(page)).toHaveText(/^0 positive prompts · /);await expect(filtersButton(page)).toHaveText('Filters · 1');
    await page.goto('about:blank');
    await page.goto(`${workspace}#/library?tag=cats&tag=dogs&tag_mode=any`);
    await expect(caption(page)).toHaveText(/^2 positive prompts · /);
    await expect(card(page,'Cat portrait')).toHaveCount(1);await expect(card(page,'Dog landscape')).toHaveCount(1);
    await expect(filtersButton(page)).toHaveText('Filters · 2');await expect(page).toHaveURL(libraryUrl);
});

test('a Library link replaces the filter of a shelf that is already open, each time it is followed',async({page})=>{
    await page.goto(`${workspace}#/tags`);
    await expect(page.locator('#workspace-main h1')).toHaveText('Tags & categories');
    await page.evaluate(()=>{window.location.hash='#/library?tag=cats';});
    await expect(caption(page)).toHaveText(/^1 positive prompt · /);await expect(card(page,'Cat portrait')).toHaveCount(1);
    await expect(page).toHaveURL(libraryUrl);await expect(page.locator('#workspace-main h1')).toBeFocused();
    // The open shelf saves its own filter when it is replaced; the link's filter still wins.
    await page.evaluate(()=>{window.location.hash='#/library?tag=dogs';});
    await expect(card(page,'Dog landscape')).toHaveCount(1);await expect(cards(page)).toHaveCount(1);
    await expect(filtersButton(page)).toHaveText('Filters · 1');await expect(page).toHaveURL(libraryUrl);
    // The same link again is applied again after the reader cleared the filter.
    await openRefine(page);await refine(page).getByRole('button',{name:'Clear filters',exact:true}).click();
    await expect(caption(page)).toHaveText(/^3 positive prompts · /);
    await page.evaluate(()=>{window.location.hash='#/library?tag=dogs';});
    await expect(caption(page)).toHaveText(/^1 positive prompt · /);await expect(page).toHaveURL(libraryUrl);
});

test('a Library link for untagged prompts asks the server for them',async({page})=>{
    const listed=page.waitForResponse(item=>{const url=new URL(item.url());return url.pathname==='/prompt_manager/library'&&url.searchParams.get('tag_mode')==='untagged';});
    await page.goto(`${workspace}#/library?tag_mode=untagged`);
    const body=await (await listed).json();
    expect(body.tag_mode).toBe('untagged');
    await expect(cards(page)).toHaveCount(body.items.length);
    await expect(filtersButton(page)).toHaveText('Filters · 1');await expect(page).toHaveURL(libraryUrl);
});

test('a Library link that cannot be read opens the whole shelf and says so',async({page})=>{
    await page.goto(`${workspace}#/library?tag=cats&tag_mode=either`);
    await expect(caption(page)).toHaveText(/^3 positive prompts · /);
    await expect(filtersButton(page)).toHaveText('Filters');
    await expect(toast(page)).toHaveText("This link's tag filter couldn't be read. Showing the whole prompt library.");
    await expect(page).toHaveURL(libraryUrl);
});

test('a Library link with an unknown tag shows the empty shelf, not the whole library',async({page})=>{
    await page.goto(`${workspace}#/library?tag=no-such-tag`);
    await expect(caption(page)).toHaveText(/^0 positive prompts · /);
    await expect(page.locator('#workspace-main').getByText('No matching prompts. Try changing the filters.',{exact:true})).toBeVisible();
    await expect(filtersButton(page)).toHaveText('Filters · 1');
});
