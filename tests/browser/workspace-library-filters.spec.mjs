// #444 (D150): the Library's output-folder filter and multi-tag OR in "Refine this view", against the real server, and
// the Refine dialog against the concept's Filters dialog (app.js:1345-1352).
import {writeFile} from 'node:fs/promises';
import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import {test,expect} from './fixtures.mjs';
import {captureCanonicalState} from './history-canonical-comparison.mjs';
import {inventoryOf,diffInventory} from './library-editor-canonical-comparison.mjs';

const route='/prompt_manager/workspace#/library';
const concept='/__test__/canonical/workspace';
const cards=page=>page.locator('article[data-prompt-id]');
const card=(page,text)=>cards(page).filter({has:page.getByText(text,{exact:true})});
const toolbar=page=>page.locator('#workspace-main search.toolbar');
const filtersButton=page=>toolbar(page).getByRole('button',{name:/^Filters(?: · \d)?$/});
const refine=page=>page.getByRole('dialog',{name:'Refine this view',exact:true});
const folder=page=>refine(page).getByRole('combobox',{name:'Folder',exact:true});
const tags=page=>refine(page).getByRole('textbox',{name:'Tags',exact:true});
const match=page=>refine(page).getByRole('combobox',{name:'Tag match',exact:true});
const apply=page=>refine(page).getByRole('button',{name:'Apply filters',exact:true});
const caption=page=>page.locator('#workspace-main .section-caption > span').first();

test.beforeEach(async({page,request,browserName})=>{
    await startCoverage(page,browserName);await expect(await request.post('/__test__/reset',{data:{native_library:true}})).toBeOK();
    page.libraryErrors=[];page.on('pageerror',error=>page.libraryErrors.push(error.message));
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.libraryErrors).toEqual([]);});

async function open(page) {
    await page.goto(route);await expect(caption(page)).toHaveText(/^3 positive prompts · /);
}
/** Open Refine and wait for the folder list it reads on opening. */
async function openRefine(page) {
    const folders=page.waitForResponse(item=>new URL(item.url()).pathname==='/prompt_manager/library/options'&&new URL(item.url()).searchParams.get('include')==='folders');
    await filtersButton(page).click();await expect(refine(page)).toBeVisible();await folders;
}

test('Refine offers Folder, Tags and Tag match; a folder narrows the shelf to prompts with images there',async({page})=>{
    await open(page);await openRefine(page);
    await expect(refine(page).locator('.field label')).toHaveText(['Folder','Tags','Tag match']);
    // Both roots hold a "shared" folder; the root's own images name no folder.
    await expect(folder(page).locator('option')).toHaveText(['All output folders','shared']);
    await expect(match(page).locator('option')).toHaveText(['All of these tags','Any of these tags','Prompts without tags']);
    await folder(page).selectOption('shared');await apply(page).click();
    await expect(caption(page)).toHaveText(/^1 positive prompt · /);
    await expect(cards(page)).toHaveCount(1);await expect(card(page,'Cat portrait')).toHaveCount(1);
    await expect(filtersButton(page)).toHaveText('Filters · 1');
    await openRefine(page);await expect(folder(page)).toHaveValue('shared');
    await refine(page).getByRole('button',{name:'Clear filters',exact:true}).click();
    await expect(caption(page)).toHaveText(/^3 positive prompts · /);await expect(filtersButton(page)).toHaveText('Filters');
});

test('Tag match Any shows every prompt carrying one of the tags; changing only the match applies it',async({page})=>{
    await open(page);await openRefine(page);
    await tags(page).fill('cats, dogs');await apply(page).click();
    await expect(caption(page)).toHaveText(/^0 positive prompts · /);await expect(filtersButton(page)).toHaveText('Filters · 1');
    await openRefine(page);await match(page).selectOption('any');await apply(page).click();
    await expect(caption(page)).toHaveText(/^2 positive prompts · /);
    await expect(cards(page)).toHaveCount(2);await expect(card(page,'Cat portrait')).toHaveCount(1);await expect(card(page,'Dog landscape')).toHaveCount(1);
    await expect(filtersButton(page)).toHaveText('Filters · 2');
});

test('a saved view keeps its folder and tag match',async({page})=>{
    await open(page);await openRefine(page);
    await folder(page).selectOption('shared');await tags(page).fill('cats, dogs');await match(page).selectOption('any');await apply(page).click();
    await expect(caption(page)).toHaveText(/^1 positive prompt · /);
    await toolbar(page).getByRole('button',{name:'Save view',exact:true}).click();
    const save=page.getByRole('dialog',{name:'Save this view',exact:true});
    await save.getByRole('textbox',{name:'View name',exact:true}).fill('Shared cats or dogs');
    const created=page.waitForResponse(item=>new URL(item.url()).pathname==='/prompt_manager/saved-views'&&item.request().method()==='POST');
    await save.getByRole('button',{name:'Save',exact:true}).click();
    const body=(await created).request().postDataJSON();
    expect(body.state).toMatchObject({folder:'shared',tag_mode:'any',tags:['cats','dogs']});
    await page.goto('about:blank');await open(page);
    await page.getByRole('navigation',{name:'Saved views',exact:true}).getByRole('link',{name:'Shared cats or dogs',exact:true}).click();
    await expect(caption(page)).toHaveText(/^1 positive prompt · /);await expect(filtersButton(page)).toHaveText('Filters · 3');
});

test('a chosen folder missing from the folder list stays listed and chosen in Refine',async({page})=>{
    await open(page);await openRefine(page);await folder(page).selectOption('shared');await apply(page).click();
    await expect(caption(page)).toHaveText(/^1 positive prompt · /);
    // The folder has no image any more as far as the list knows (or the read failed): Apply must not drop it.
    await page.route('**/prompt_manager/library/options?include=folders',route=>route.fulfill({json:{success:true,library_epoch:'x',categories:[],folders:[]}}));
    await openRefine(page);
    await expect(folder(page).locator('option')).toHaveText(['All output folders','shared']);await expect(folder(page)).toHaveValue('shared');
    await apply(page).click();await expect(caption(page)).toHaveText(/^1 positive prompt · /);await expect(filtersButton(page)).toHaveText('Filters · 1');
});

test('an older server that ignores the folder shows an error with Retry, never the whole shelf',async({page})=>{
    await open(page);
    await page.route('**/prompt_manager/library?*',async route=>{
        const response=await route.fetch();const body=await response.json();delete body.folder;delete body.tag_mode;
        await route.fulfill({response,json:body});
    });
    await openRefine(page);await folder(page).selectOption('shared');await apply(page).click();
    await expect(page.locator('#workspace-main').getByText('Prompt library could not be loaded. Retry.')).toBeVisible();
    await expect(cards(page)).toHaveCount(0);
});

// D150: the Refine dialog is the concept's Filters dialog: its Folder select and Tags input, plus Tag match drawn as the
// concept's own select; the concept's "Prompt evidence" (no data yet) and its prototype note are not shown.
function mapFiltersDialog(reference,{folders,matchOptions}) {
    const [conceptFolder,,conceptTags]=reference.fields;
    return {...reference,
        fields:[{...conceptFolder,options:folders},{...conceptTags},{...conceptFolder,label:'Tag match',options:matchOptions}],
        small:[],
        // Production's Apply reads "Apply filters" (Images and Library share it, #249).
        actions:{...reference.actions,buttons:reference.actions.buttons.map(item=>item.text==='Apply tag filter'?{...item,text:'Apply filters'}:item)}};
}
for(const [width,height] of [[1600,1100],[390,844]])for(const theme of ['dark','light'])test(`Refine dialog matches the concept Filters dialog ${width} ${theme} (D150)`,async({page},testInfo)=>{
    await page.setViewportSize({width,height});
    await page.goto('about:blank');await page.goto(`${concept}#library`);await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);
    await page.locator('#main').getByRole('button',{name:'Filters',exact:true}).click();await expect(page.locator('#modal')).toBeVisible();
    const reference=await page.locator('#modal').evaluate(inventoryOf);
    await captureCanonicalState(page,testInfo,`concept-filters-${width}-${theme}`,'#modal');
    await open(page);await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);await openRefine(page);
    await expect(folder(page).locator('option')).toHaveCount(2);
    const actual=await refine(page).evaluate(inventoryOf);
    await captureCanonicalState(page,testInfo,`production-refine-${width}-${theme}`,'dialog[open]');
    const expected=mapFiltersDialog(reference,{folders:[['','All output folders'],['shared','shared']],matchOptions:[['all','All of these tags'],['any','Any of these tags'],['untagged','Prompts without tags']]});
    const path=testInfo.outputPath('refine-inventory.json');await writeFile(path,JSON.stringify({reference,actual},null,2));
    await testInfo.attach('refine-inventory',{path,contentType:'application/json'});
    expect(reference.fields.map(item=>item.label)).toEqual(['Folder','Prompt evidence','Tags']);
    expect(diffInventory(expected,actual)).toEqual([]);
    await refine(page).getByRole('button',{name:'Close dialog',exact:true}).click();
    await openRefine(page);await folder(page).selectOption('shared');await tags(page).fill('cats, dogs');await match(page).selectOption('any');await apply(page).click();
    await expect(caption(page)).toHaveText(/^1 positive prompt · /);await page.mouse.move(0,0);
    await captureCanonicalState(page,testInfo,`production-library-folder-any-${width}-${theme}`,'#workspace-main');
});
