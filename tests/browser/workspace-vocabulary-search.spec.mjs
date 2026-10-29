// #444 (D149): Tags & categories tag search, tag sort and the Untagged prompts row, against the real server.
import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import {test,expect} from './fixtures.mjs';
import {armUnseenRestores} from './unseen-restore.mjs';
import {captureCanonicalState} from './history-canonical-comparison.mjs';

const route='/prompt_manager/workspace#/tags';
const main=page=>page.locator('#workspace-main');
const tagSurface=page=>main(page).locator('.two-col > section.surface').first();
const table=page=>main(page).getByRole('table',{name:'Reusable tags',exact:true});
const rows=page=>table(page).locator('tbody tr');
const names=page=>rows(page).locator('td:first-child');
const tagRow=(page,name)=>rows(page).filter({has:page.locator('td:first-child').getByText(name,{exact:true})});
const search=page=>page.getByRole('searchbox',{name:'Search tags',exact:true});
const sort=page=>page.getByRole('combobox',{name:'Sort tags',exact:true});
const untagged=page=>tagSurface(page).locator('.operation').filter({has:page.getByRole('heading',{level:3,name:'Untagged prompts',exact:true})});
const status=page=>tagSurface(page).locator('search.vocabulary-tools [role="status"]');
const shelf=page=>page.locator('article[data-prompt-id]');
const filtersButton=(page,name)=>page.locator('#workspace-main search.toolbar').getByRole('button',{name,exact:true});
const refine=page=>page.getByRole('dialog',{name:'Refine this view',exact:true});

async function seed(request,data={}) {
    const response=await request.post('/__test__/vocabulary/reset',{data});await expect(response).toBeOK();return response.json();
}
let manifest;
test.beforeEach(async({page,request,browserName})=>{
    await startCoverage(page,browserName);await armUnseenRestores(page);
    // Two minimal recipes without tags (the concept recipes all carry tags).
    manifest=await seed(request,{extra_categories:2});
    page.vocabularyErrors=[];page.on('pageerror',error=>page.vocabularyErrors.push(error.message));
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.vocabularyErrors).toEqual([]);});

async function open(page) {
    await page.goto(route);await expect(rows(page)).toHaveCount(manifest.tags.length);
}

test('the tag vocabulary surface orders its paragraph, Untagged prompts, the search and sort, then the table',async({page})=>{
    await open(page);
    const order=await tagSurface(page).evaluate(node=>[...node.children].map(child=>child.matches('.operation')?'operation':
        child.matches('search.toolbar')?'toolbar':child.matches('.table-wrap')?'table':child.tagName.toLowerCase()).slice(0,5));
    expect(order).toEqual(['h2','p','operation','toolbar','table']);
    await expect(untagged(page).locator('p')).toHaveText('2 positive prompts');
    await expect(untagged(page).getByRole('button')).toHaveText(['Browse']);
    await expect(search(page)).toHaveAttribute('placeholder','Search tags…');
    await expect(search(page)).toHaveAttribute('maxlength','200');
    await expect(sort(page).locator('option')).toHaveText(['Oldest first','Name A–Z','Name Z–A','Most prompts','Fewest prompts']);
    await expect(sort(page)).toHaveValue('created');
    await expect(tagSurface(page).locator('label.search-field svg, label.search-field .ca-icon').first()).toBeVisible();
});

test('search narrows the tags on the server, says when nothing matches and keeps the categories',async({page})=>{
    await open(page);
    const categories=await main(page).locator('.two-col > section.surface').nth(1).locator('.operation h3').allTextContents();
    const reads=[];page.on('request',request=>{if(request.url().includes('/vocabulary/tags?'))reads.push(new URL(request.url()).searchParams);});
    await search(page).fill('LIGHT');
    await expect(names(page)).toHaveText(['warm_light','soft_light']);
    await expect(status(page)).toHaveText('2 tags');
    expect(reads.at(-1).get('search')).toBe('LIGHT');
    // Literal: an underscore is not a wildcard.
    await search(page).fill('a_c');await expect(rows(page)).toHaveCount(0);
    await expect(tagSurface(page).locator('p.empty')).toHaveText('No tags match this search.');
    await expect(status(page)).toHaveText('No tags match this search.');
    await expect(main(page).locator('.two-col > section.surface').nth(1).locator('.operation h3')).toHaveText(categories);
    await search(page).fill('');await expect(rows(page)).toHaveCount(manifest.tags.length);
    await expect(status(page)).toHaveText(`${manifest.tags.length} tags`);
});

test('sort orders every tag by name or by prompts, ties in creation order',async({page})=>{
    await open(page);
    const created=await names(page).allTextContents();
    await sort(page).selectOption('name');
    const byName=[...created].sort((a,b)=>a.toLowerCase()<b.toLowerCase()?-1:a.toLowerCase()>b.toLowerCase()?1:0);
    await expect(names(page)).toHaveText(byName);
    await sort(page).selectOption('name-desc');await expect(names(page)).toHaveText([...byName].reverse());
    await sort(page).selectOption('prompts');
    await expect(rows(page).first().locator('td:first-child')).toHaveText('cinematic');
    const counts=(await rows(page).locator('td:nth-child(2)').allTextContents()).map(Number);
    expect(counts).toEqual([...counts].sort((a,b)=>b-a));
    await sort(page).selectOption('prompts-asc');
    const ascending=(await rows(page).locator('td:nth-child(2)').allTextContents()).map(Number);
    expect(ascending).toEqual([...ascending].sort((a,b)=>a-b));
    await sort(page).selectOption('created');await expect(names(page)).toHaveText(created);
});

test('a rename while searching re-reads the same search and sort',async({page})=>{
    await open(page);
    await sort(page).selectOption('name');await search(page).fill('light');
    await expect(names(page)).toHaveText(['soft_light','warm_light']);
    await tagRow(page,'warm_light').getByRole('button',{name:'Rename / merge',exact:true}).click();
    const box=page.locator('dialog.vocabulary-dialog');
    await box.getByLabel('New name; existing names merge',{exact:true}).fill('amber_light');
    await box.getByRole('button',{name:'Apply',exact:true}).click();await expect(box).toHaveCount(0);
    await expect(names(page)).toHaveText(['amber_light','soft_light']);
    await expect(tagRow(page,'amber_light').getByRole('button',{name:'Rename / merge',exact:true})).toBeFocused();
    await expect(search(page)).toHaveValue('light');await expect(sort(page)).toHaveValue('name');
});

test('Untagged prompts Browse opens the Library on exactly the prompts without tags',async({page})=>{
    await open(page);
    await untagged(page).getByRole('button',{name:'Browse',exact:true}).click();
    await expect(page).toHaveURL(/#\/library$/);
    await expect(main(page).getByRole('heading',{level:1})).toBeFocused();
    await expect(shelf(page)).toHaveCount(2);
    await expect(page.locator('#workspace-main .section-caption > span').first()).toHaveText(/^2 positive prompts · /);
    for(const card of await shelf(page).all())await expect(card.locator('.tags .chip')).toHaveCount(0);
    await filtersButton(page,'Filters · 1').click();
    await expect(refine(page).getByRole('combobox',{name:'Tag match',exact:true})).toHaveValue('untagged');
    await expect(refine(page).getByRole('textbox',{name:'Tags',exact:true})).toBeDisabled();
    // Changing only the Tag match applies it: the whole shelf returns.
    await refine(page).getByRole('combobox',{name:'Tag match',exact:true}).selectOption('all');
    await expect(refine(page).getByRole('textbox',{name:'Tags',exact:true})).toBeEnabled();
    await refine(page).getByRole('button',{name:'Apply filters',exact:true}).click();
    await expect(filtersButton(page,'Filters')).toBeVisible();
    await expect.poll(()=>shelf(page).count()).toBeGreaterThan(2);
});

test('Refine › Prompts without tags on the shelf drops typed tags and Clear restores All',async({page})=>{
    await page.goto('/prompt_manager/workspace#/library');await expect(shelf(page).first()).toBeVisible();
    await filtersButton(page,'Filters').click();
    await refine(page).getByRole('textbox',{name:'Tags',exact:true}).fill('cinematic');
    await refine(page).getByRole('combobox',{name:'Tag match',exact:true}).selectOption('untagged');
    await refine(page).getByRole('button',{name:'Apply filters',exact:true}).click();
    await expect(shelf(page)).toHaveCount(2);
    await filtersButton(page,'Filters · 1').click();
    await expect(refine(page).getByRole('textbox',{name:'Tags',exact:true})).toHaveValue('');
    await refine(page).getByRole('button',{name:'Clear filters',exact:true}).click();
    await expect(filtersButton(page,'Filters')).toBeVisible();await expect.poll(()=>shelf(page).count()).toBeGreaterThan(2);
});

test('an older server that ignores the search shows an error with Retry, never an unfiltered list',async({page})=>{
    await open(page);
    // An un-restarted server answers without the echo.
    await page.route('**/prompt_manager/vocabulary/tags?*',async route=>{
        const response=await route.fetch();const body=await response.json();delete body.search;delete body.sort;
        await route.fulfill({response,json:body});
    });
    await search(page).fill('light');
    await expect(tagSurface(page).locator('.vocabulary-error')).toBeVisible();
    await expect(rows(page)).toHaveCount(0);
    await expect(tagSurface(page).getByRole('button',{name:'Retry',exact:true})).toBeVisible();
});

test('the search and sort fit 390px without a page scroll',async({page})=>{
    await page.setViewportSize({width:390,height:844});await open(page);
    const layout=await tagSurface(page).evaluate(node=>{
        const tools=node.querySelector('search.vocabulary-tools'),field=tools.querySelector('.search-field'),select=tools.querySelector('select');
        const box=node.getBoundingClientRect(),fieldBox=field.getBoundingClientRect(),selectBox=select.getBoundingClientRect();
        return {inside:fieldBox.left>=box.left&&selectBox.right<=box.right+0.5,fullField:Math.round(fieldBox.width)===Math.round(tools.getBoundingClientRect().width)};
    });
    expect(layout).toEqual({inside:true,fullField:true});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
});

// Artifact-only captures for the maintainer's quick check (no pixel assertions): a search with a sort, the Library on
// Untagged prompts and its Refine dialog.
for(const [width,height] of [[1600,1100],[390,844]])for(const theme of ['dark','light'])test(`D149 captures ${width} ${theme}`,async({page},testInfo)=>{
    await page.setViewportSize({width,height});await open(page);
    await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);
    await sort(page).selectOption('prompts');await search(page).fill('i');await expect(status(page)).toHaveText(/^\d+ tags$/);
    await page.mouse.move(0,0);
    await captureCanonicalState(page,testInfo,`production-tags-search-${width}-${theme}`,'#workspace-main');
    await untagged(page).getByRole('button',{name:'Browse',exact:true}).click();await expect(shelf(page)).toHaveCount(2);
    await captureCanonicalState(page,testInfo,`production-library-untagged-${width}-${theme}`,'#workspace-main');
    await filtersButton(page,'Filters · 1').click();await expect(refine(page)).toBeVisible();
    await captureCanonicalState(page,testInfo,`production-refine-untagged-${width}-${theme}`,'dialog[open]');
});
