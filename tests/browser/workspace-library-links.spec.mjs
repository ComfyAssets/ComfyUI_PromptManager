// #668 (D207): `workspace#/library?tag=…&tag_mode=…` is the Prompt library with those Tags and that Tag match, against
// the real server. The filter stays in the address (Reload, Back and Forward keep it) and the address follows the
// shelf's tag filter. Old tag bookmarks are sent to this form (old-urls.spec.mjs).
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
/** The workspace at exactly this hash. */
const at=hash=>url=>url.pathname.endsWith('/prompt_manager/workspace')&&url.search===''&&url.hash===hash;
const heading=page=>page.locator('#workspace-main h1').first();
const tagsField=page=>refine(page).getByRole('textbox',{name:'Tags',exact:true});
const matchField=page=>refine(page).getByRole('combobox',{name:'Tag match',exact:true});
const apply=page=>refine(page).getByRole('button',{name:'Apply filters',exact:true});
const sidebarLibrary=page=>page.locator('aside.sidebar nav [data-route="library"]');

test.beforeEach(async({page,request,browserName})=>{
    await startCoverage(page,browserName);await expect(await request.post('/__test__/reset',{data:{native_library:true}})).toBeOK();
    page.libraryErrors=[];page.on('pageerror',error=>page.libraryErrors.push(error.message));
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.libraryErrors).toEqual([]);});

async function openRefine(page) {
    const folders=page.waitForResponse(item=>new URL(item.url()).pathname==='/prompt_manager/library/options'&&new URL(item.url()).searchParams.get('include')==='folders');
    await filtersButton(page).click();await expect(refine(page)).toBeVisible();await folders;
}
async function expectCats(page) {
    await expect(caption(page)).toHaveText(/^1 positive prompt · /);
    await expect(cards(page)).toHaveCount(1);await expect(card(page,'Cat portrait')).toHaveCount(1);
    await expect(filtersButton(page)).toHaveText('Filters · 1');
}

test('a Library link opens the shelf filtered by its tags, keeps them in the address and shows them in Refine',async({page})=>{
    await page.goto(`${workspace}#/library?tag=cats`);
    await expectCats(page);
    await expect(page).toHaveURL(at('#/library?tag=cats'));
    await expect(heading(page)).toHaveText('Prompt library');
    // As for any route, the title takes focus on a navigation inside the workspace, never on the first render.
    await expect(heading(page)).not.toBeFocused();
    await openRefine(page);
    await expect(tagsField(page)).toHaveValue('cats');await expect(matchField(page)).toHaveValue('all');
});

test('Reload keeps the filter of a Library link',async({page})=>{
    await page.goto(`${workspace}#/library?tag=cats&tag=dogs&tag_mode=any`);
    await expect(caption(page)).toHaveText(/^2 positive prompts · /);
    await page.reload();
    await expect(page).toHaveURL(at('#/library?tag=cats&tag=dogs&tag_mode=any'));
    await expect(caption(page)).toHaveText(/^2 positive prompts · /);
    await expect(card(page,'Cat portrait')).toHaveCount(1);await expect(card(page,'Dog landscape')).toHaveCount(1);
    await expect(filtersButton(page)).toHaveText('Filters · 2');
});

test('a Library link with two tags matches all of them unless it asks for any, and its address is made canonical',async({page})=>{
    await page.goto(`${workspace}#/library?tag=cats&tag=dogs`);
    await expect(caption(page)).toHaveText(/^0 positive prompts · /);await expect(filtersButton(page)).toHaveText('Filters · 1');
    await page.goto('about:blank');
    await page.goto(`${workspace}#/library?tag_mode=any&sort=name&tag=cats&tag=dogs&tag=cats`);
    await expect(caption(page)).toHaveText(/^2 positive prompts · /);
    await expect(filtersButton(page)).toHaveText('Filters · 2');
    await expect(page).toHaveURL(at('#/library?tag=cats&tag=dogs&tag_mode=any'));
});

test('opening a prompt and going Back returns to the filtered address and filter, and Forward to the prompt',async({page})=>{
    await page.goto(`${workspace}#/library?tag=cats`);
    await expectCats(page);
    await card(page,'Cat portrait').locator('[data-history]').first().click();
    await expect(page).toHaveURL(/\/prompt_manager\/workspace#\/library\/\d+$/);
    const promptUrl=page.url();
    await page.goBack();
    await expect(page).toHaveURL(at('#/library?tag=cats'));await expectCats(page);
    await page.goForward();
    await expect(page).toHaveURL(promptUrl);
    await expect(page.locator('#workspace-main article[data-prompt-id]')).toHaveCount(0);
    await page.goBack();
    await expect(page).toHaveURL(at('#/library?tag=cats'));await expectCats(page);
    // The prompt page's own way back keeps the filter too.
    await card(page,'Cat portrait').locator('[data-history]').first().click();
    await expect(page).toHaveURL(promptUrl);
    await page.locator('#workspace-main').getByRole('button',{name:'← Prompt library',exact:true}).click();
    await expect(page).toHaveURL(at('#/library?tag=cats'));await expectCats(page);
});

test('changing the tag filter in Refine updates the address without adding history entries, and clearing it returns to the plain address',async({page})=>{
    await page.goto(`${workspace}#/overview`);
    await expect(heading(page)).toHaveText('Overview');
    await sidebarLibrary(page).click();
    await expect(caption(page)).toHaveText(/^3 positive prompts · /);await expect(page).toHaveURL(at('#/library'));
    const entries=await page.evaluate(()=>window.history.length);
    await openRefine(page);await tagsField(page).fill('cats');await apply(page).click();
    await expectCats(page);await expect(page).toHaveURL(at('#/library?tag=cats'));
    await openRefine(page);await tagsField(page).fill('dogs, cats');await matchField(page).selectOption('any');await apply(page).click();
    await expect(caption(page)).toHaveText(/^2 positive prompts · /);
    await expect(page).toHaveURL(at('#/library?tag=dogs&tag=cats&tag_mode=any'));
    await openRefine(page);await matchField(page).selectOption('untagged');await apply(page).click();
    await expect(page).toHaveURL(at('#/library?tag_mode=untagged'));await expect(filtersButton(page)).toHaveText('Filters · 1');
    // Search, category and sort are not in the address.
    await page.locator('#workspace-main search.toolbar').getByRole('searchbox').fill('zzz');
    await expect(caption(page)).toHaveText(/^0 positive prompts · /);
    await expect(page).toHaveURL(at('#/library?tag_mode=untagged'));
    await page.locator('#workspace-main search.toolbar').getByRole('searchbox').fill('');
    await openRefine(page);await refine(page).getByRole('button',{name:'Clear filters',exact:true}).click();
    await expect(caption(page)).toHaveText(/^3 positive prompts · /);await expect(page).toHaveURL(at('#/library'));
    expect(await page.evaluate(()=>window.history.length)).toBe(entries);
    // One step back leaves the Library: no filter change made an entry.
    await page.goBack();
    await expect(page).toHaveURL(at('#/overview'));
});

test('the sidebar Library link shows the whole Library after a link filter and after a filter set in Refine',async({page})=>{
    await page.goto(`${workspace}#/library?tag=cats`);
    await expectCats(page);
    await sidebarLibrary(page).click();
    await expect(page).toHaveURL(at('#/library'));
    await expect(caption(page)).toHaveText(/^3 positive prompts · /);await expect(filtersButton(page)).toHaveText('Filters');
    await openRefine(page);await tagsField(page).fill('dogs');await apply(page).click();
    await expect(page).toHaveURL(at('#/library?tag=dogs'));await expect(caption(page)).toHaveText(/^1 positive prompt · /);
    await sidebarLibrary(page).click();
    await expect(page).toHaveURL(at('#/library'));
    await expect(caption(page)).toHaveText(/^3 positive prompts · /);await expect(filtersButton(page)).toHaveText('Filters');
    // Leaving for another page and coming back by the sidebar is the whole Library too.
    await page.evaluate(()=>{window.location.hash='#/library?tag=cats';});
    await expectCats(page);await expect(heading(page)).toBeFocused();
    await page.locator('aside.sidebar nav [data-route="tags"]').click();
    await expect(heading(page)).toHaveText('Tags & categories');
    await sidebarLibrary(page).click();
    await expect(page).toHaveURL(at('#/library'));await expect(caption(page)).toHaveText(/^3 positive prompts · /);
});

test('a Library link replaces the filter of a shelf that is already open',async({page})=>{
    await page.goto(`${workspace}#/library?tag=cats`);
    await expectCats(page);
    await page.evaluate(()=>{window.location.hash='#/library?tag=dogs';});
    await expect(card(page,'Dog landscape')).toHaveCount(1);await expect(cards(page)).toHaveCount(1);
    await expect(filtersButton(page)).toHaveText('Filters · 1');await expect(page).toHaveURL(at('#/library?tag=dogs'));
    // Back is the earlier link's filter.
    await page.goBack();
    await expect(page).toHaveURL(at('#/library?tag=cats'));await expectCats(page);
});

test('Browse on Tags & categories lands on the filtered address',async({page})=>{
    await page.goto(`${workspace}#/tags`);
    await expect(heading(page)).toHaveText('Tags & categories');
    const row=page.locator('#workspace-main tbody tr').filter({has:page.locator('td:first-child').getByText('cats',{exact:true})});
    await row.getByRole('button',{name:'Browse',exact:true}).click();
    await expect(page).toHaveURL(at('#/library?tag=cats'));await expectCats(page);
    await page.reload();
    await expect(page).toHaveURL(at('#/library?tag=cats'));await expectCats(page);
});

test('a saved view with a tag filter keeps its own address, and the Library link after it is the whole Library',async({page})=>{
    await page.goto(`${workspace}#/library?tag=cats&tag=dogs&tag_mode=any`);
    await expect(caption(page)).toHaveText(/^2 positive prompts · /);
    await page.locator('#workspace-main search.toolbar').getByRole('button',{name:'Save view',exact:true}).click();
    const save=page.getByRole('dialog',{name:'Save this view',exact:true});
    await save.getByRole('textbox',{name:'View name',exact:true}).fill('Cats or dogs');
    await save.getByRole('button',{name:'Save',exact:true}).click();
    const link=page.getByRole('navigation',{name:'Saved views',exact:true}).getByRole('link',{name:'Cats or dogs',exact:true});
    await expect(link).toBeVisible();
    await sidebarLibrary(page).click();
    await expect(page).toHaveURL(at('#/library'));await expect(caption(page)).toHaveText(/^3 positive prompts · /);
    await link.click();
    await expect(page).toHaveURL(/\/prompt_manager\/workspace#\/saved\/\d+$/);
    await expect(caption(page)).toHaveText(/^2 positive prompts · /);await expect(filtersButton(page)).toHaveText('Filters · 2');
    const savedUrl=page.url();
    // A filter change inside the saved view never rewrites its address.
    await openRefine(page);await tagsField(page).fill('cats');await apply(page).click();
    await expect(caption(page)).toHaveText(/^1 positive prompt · /);
    await expect(page).toHaveURL(savedUrl);
    await sidebarLibrary(page).click();
    await expect(page).toHaveURL(at('#/library'));await expect(caption(page)).toHaveText(/^3 positive prompts · /);
});

test('a Library link for untagged prompts asks the server for them',async({page})=>{
    const listed=page.waitForResponse(item=>{const url=new URL(item.url());return url.pathname==='/prompt_manager/library'&&url.searchParams.get('tag_mode')==='untagged';});
    await page.goto(`${workspace}#/library?tag_mode=untagged`);
    const body=await (await listed).json();
    expect(body.tag_mode).toBe('untagged');
    await expect(cards(page)).toHaveCount(body.items.length);
    await expect(filtersButton(page)).toHaveText('Filters · 1');await expect(page).toHaveURL(at('#/library?tag_mode=untagged'));
});

test('a Library link that cannot be read opens the whole shelf at the plain address and says so',async({page})=>{
    await page.goto(`${workspace}#/library?tag=cats&tag_mode=either`);
    await expect(caption(page)).toHaveText(/^3 positive prompts · /);
    await expect(filtersButton(page)).toHaveText('Filters');
    await expect(toast(page)).toHaveText("This link's tag filter couldn't be read. Showing the whole prompt library.");
    await expect(page).toHaveURL(at('#/library'));
});

test('a Library link with an unknown tag shows the empty shelf, not the whole library',async({page})=>{
    await page.goto(`${workspace}#/library?tag=no-such-tag`);
    await expect(caption(page)).toHaveText(/^0 positive prompts · /);
    await expect(page.locator('#workspace-main').getByText('No matching prompts. Try changing the filters.',{exact:true})).toBeVisible();
    await expect(filtersButton(page)).toHaveText('Filters · 1');await expect(page).toHaveURL(at('#/library?tag=no-such-tag'));
});
