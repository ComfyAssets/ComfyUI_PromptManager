import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';

const route='/prompt_manager/workspace#/tags';
// Prompts per concept tag (app.js:71-165): first appearance order, Library Browse counts.
const CONCEPT_COUNTS=[['cinematic',2],['blue_hour',1],['architecture',1],['minimal',2],['warm_light',1],['editorial',1],['forest',1],['mist',1],
    ['peaceful',2],['neon',1],['night',1],['space',1],['concept_art',1],['coast',1],['soft_light',1]];
const CATEGORIES=[['Landscapes',3],['Architecture',1],['Environments',1],['Illustration',1]];
const main=page=>page.locator('#workspace-main');
const surfaces=page=>main(page).locator('.two-col > section.surface');
const table=page=>main(page).getByRole('table',{name:'Reusable tags',exact:true});
const rows=page=>table(page).locator('tbody tr');
const tagRow=(page,name)=>rows(page).filter({has:page.locator('td:first-child').getByText(name,{exact:true})});
const categoryRow=(page,name)=>surfaces(page).nth(1).locator('.operation').filter({has:page.getByRole('heading',{level:3,name,exact:true})});
const loadMore=page=>main(page).getByRole('button',{name:'Load more tags',exact:true});

async function seed(request,data={}) {
    const response=await request.post('/__test__/vocabulary/reset',{data});await expect(response).toBeOK();return response.json();
}
async function expectLibrary(page,{tags='',category='',count}) {
    await expect(page).toHaveURL(/#\/library$/);
    await expect(main(page).getByRole('heading',{level:1})).toHaveText('Prompt library');
    await expect(main(page).getByRole('heading',{level:1})).toBeFocused();
    await expect(page.getByRole('searchbox',{name:'Search prompts',exact:true})).toHaveValue('');
    await expect(page.getByRole('textbox',{name:'Tags',exact:true})).toHaveValue(tags);
    const select=page.getByRole('combobox',{name:'Category',exact:true});
    await expect(select).toHaveValue(category);
    if(!category) await expect(select.locator('option:checked')).toHaveText('All categories');
    await expect(page.locator('.library-caption')).toHaveText(`${count} of ${count} positive prompts · Images are grouped by positive prompt`);
    await expect(page.locator('article[data-prompt-id]')).toHaveCount(count);
}

let manifest;
test.beforeEach(async({page,request,browserName})=>{
    await startCoverage(page,browserName);manifest=await seed(request);
    page.vocabularyErrors=[];page.on('pageerror',error=>page.vocabularyErrors.push(error.message));
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.vocabularyErrors).toEqual([]);});

test('tags route renders the concept composition without a legacy handoff',async({page})=>{
    await page.goto(route);
    await expect(main(page).getByRole('heading',{level:1})).toHaveText('Tags & categories');
    await expect(main(page).locator('.workspace-heading-copy > p')).toHaveText('Organize once. Reuse everywhere.');
    const actions=main(page).locator('.workspace-heading .heading-actions button');
    await expect(actions).toHaveText(['+ Create tag']);
    await expect(actions.first()).not.toHaveClass(/ca-primary/);
    await expect(main(page).getByRole('button',{name:'Auto-tag images'})).toHaveCount(0);
    await expect(surfaces(page)).toHaveCount(2);
    await expect(surfaces(page).locator('h2')).toHaveText(['Tag vocabulary','Categories']);
    await expect(surfaces(page).locator(':scope > p:visible')).toHaveText(['Category/tag autocomplete uses this same vocabulary across nodes and dialogs.',
        'Categories provide a broad home. Tags describe content; proposed collections group projects without changing either.']);
    await expect(table(page).locator('caption')).toHaveText('Reusable tags');
    await expect(table(page).locator('thead th')).toHaveText(['Tag','Prompts','Images','Actions']);
    expect(await table(page).locator('thead th').evaluateAll(cells=>cells.map(cell=>cell.getAttribute('scope')))).toEqual(['col','col','col','col']);
    const expected=[...CONCEPT_COUNTS,[manifest.pair_only_tag,1],[manifest.archived_only_tag,0],[manifest.negative_only_tag,0],[manifest.unused_tag,0]];
    await expect(rows(page)).toHaveCount(expected.length);
    expect(manifest.tags).toEqual(expected.map(([name])=>name));
    await expect(rows(page).locator('td:nth-child(1)')).toHaveText(expected.map(([name])=>name));
    await expect(rows(page).locator('td:nth-child(2)')).toHaveText(expected.map(([,count])=>String(count)));
    const images=await rows(page).locator('td:nth-child(3)').evaluateAll(cells=>cells.map(cell=>[cell.querySelector('[aria-hidden="true"]')?.textContent,cell.querySelector('.ca-visually-hidden')?.textContent]));
    expect(images).toEqual(expected.map(()=>['—','Not available yet']));
    await expect(rows(page).first().locator('td:nth-child(3)')).toHaveAccessibleName('Not available yet');
    const labels=await rows(page).evaluateAll(nodes=>nodes.map(row=>[...row.querySelectorAll('td:nth-child(4) .row-actions button')].map(button=>button.textContent)));
    expect(labels).toEqual(expected.map(()=>['Browse','Rename / merge']));
    const operations=surfaces(page).nth(1).locator('.operation');
    await expect(operations.locator('h3')).toHaveText([...CATEGORIES.map(([name])=>name),'Manage category']);
    await expect(operations.locator('p')).toHaveText([...CATEGORIES.map(([,count])=>`${count} positive prompt${count===1?'':'s'}`),'Rename or consolidate a category with a preview of affected recipes.']);
    await expect(operations.locator('.row-actions button')).toHaveText(['Browse','Browse','Browse','Browse','Review']);
    await expect(main(page).locator('.handoff')).toHaveCount(0);
    await expect(main(page).locator('a[href*="admin"]')).toHaveCount(0);
    await expect(main(page).getByRole('button',{name:/^Retry/})).toHaveCount(0);
    await expect(loadMore(page)).toBeHidden();
});

test('prompt counts equal Library Browse results',async({page})=>{
    for(const name of ['cinematic',manifest.pair_only_tag,manifest.archived_only_tag]) {
        await page.goto(route);
        const row=tagRow(page,name);await expect(row).toHaveCount(1);
        const count=Number(await row.locator('td:nth-child(2)').textContent());
        await row.getByRole('button',{name:'Browse',exact:true}).click();
        await expectLibrary(page,{tags:name,count});
    }
});

test('category Browse sets only the category constraint',async({page})=>{
    await page.goto('/prompt_manager/workspace#/library');
    const search=page.getByRole('searchbox',{name:'Search prompts',exact:true});
    await search.fill('observatory');await expect(page.locator('article[data-prompt-id]')).toHaveCount(1);
    await page.getByRole('navigation',{name:'Workspace',exact:true}).getByRole('link',{name:'Tags & categories',exact:true}).click();
    const row=categoryRow(page,'Landscapes');await expect(row.locator('p')).toHaveText('3 positive prompts');
    await row.getByRole('button',{name:'Browse',exact:true}).click();
    await expectLibrary(page,{category:'Landscapes',count:3});
});

test('long vocabularies append and Load more is keyboard reachable',async({page,request})=>{
    manifest=await seed(request,{extra_tags:211});expect(manifest.tags).toHaveLength(230);
    await page.goto(route);
    await expect(rows(page)).toHaveCount(100);
    await expect(loadMore(page)).toBeVisible();
    await rows(page).last().getByRole('button',{name:'Rename / merge',exact:true}).evaluate(button=>button.focus({preventScroll:true}));
    await page.keyboard.press('Tab');
    await expect(loadMore(page)).toBeFocused();
    // Focus scrolled the end of the list into view, which appends the next page.
    await expect(rows(page)).toHaveCount(200);
    await expect(loadMore(page)).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(rows(page)).toHaveCount(230);
    await expect(loadMore(page)).toHaveCount(0);
    await expect(rows(page).nth(200).getByRole('button',{name:'Browse',exact:true})).toBeFocused();
    await expect(rows(page).locator('td:nth-child(1)')).toHaveText(manifest.tags);
    // Reload at the top so restored scroll does not append before the check.
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.reload();
    await expect(rows(page)).toHaveCount(100);
    const scroll=top=>page.evaluate(y=>new Promise(resolve=>{window.scrollTo(0,y ?? document.documentElement.scrollHeight);requestAnimationFrame(()=>requestAnimationFrame(resolve));}),top);
    for(const count of [200,230]) {
        // Each new arrival of the end of the list appends one page.
        await scroll(0);await scroll(null);
        await expect(rows(page)).toHaveCount(count);
    }
    await expect(loadMore(page)).toHaveCount(0);
    await expect(rows(page).locator('td:nth-child(1)')).toHaveText(manifest.tags);
});

test('read failure shows inline error with Retry and no zero counts',async({page})=>{
    let failures=0;
    await page.route('**/prompt_manager/vocabulary/tags?*',route=>{
        if(failures++) return route.continue();
        return route.fulfill({status:500,json:{success:false,error:'Database is unavailable'}});
    });
    await page.goto(route);
    const alert=surfaces(page).first().getByRole('alert');
    await expect(alert).toHaveText('Tags could not be loaded. Nothing was changed.');
    await expect(table(page)).toHaveCount(0);
    await expect(surfaces(page).first().locator('td')).toHaveCount(0);
    await expect(surfaces(page).first().locator('.empty')).toBeHidden();
    await expect(loadMore(page)).toHaveCount(0);
    await expect(categoryRow(page,'Landscapes').locator('p')).toHaveText('3 positive prompts');
    const retry=surfaces(page).first().getByRole('button',{name:'Retry',exact:true});
    await retry.click();
    await expect(rows(page)).toHaveCount(manifest.tags.length);
    await expect(alert).toHaveCount(0);await expect(retry).toHaveCount(0);
});

test('breadcrumb and document title are Tags',async({page})=>{
    await page.goto(route);
    await expect(page.locator('#workspace-breadcrumb')).toHaveText('Tags');
    await expect(page).toHaveTitle('Tags · Prompt Manager');
    await expect(page.getByRole('navigation',{name:'Workspace',exact:true}).getByRole('link',{name:'Tags & categories',exact:true})).toHaveAttribute('aria-current','page');
    await page.reload();
    await expect(page.locator('#workspace-breadcrumb')).toHaveText('Tags');
    await page.goto('/prompt_manager/workspace#/library');
    await expect(page.locator('#workspace-breadcrumb')).toHaveText('Prompt library');
    await expect(page).toHaveTitle('Prompt library · Prompt Manager');
});

test('route disposal aborts requests and removes observers',async({page})=>{
    await page.addInitScript(()=>{
        window.__vocabularySignals=[];window.__vocabularyObservers=[];
        const fetch=window.fetch;
        window.fetch=(input,init={})=>{if(String(input).includes('/vocabulary/') && init.signal) window.__vocabularySignals.push(init.signal);return fetch(input,init);};
        const Observer=window.IntersectionObserver;
        window.IntersectionObserver=class extends Observer {
            constructor(...args) {super(...args);this.record={targets:[],disconnected:false};window.__vocabularyObservers.push(this.record);}
            observe(target) {this.record.targets.push(target.className);return super.observe(target);}
            disconnect() {this.record.disconnected=true;return super.disconnect();}
        };
    });
    const held=[];
    await page.route('**/prompt_manager/vocabulary/**',route=>{held.push(route);});
    await page.goto(route);
    await expect.poll(()=>page.evaluate(()=>window.__vocabularySignals.length)).toBe(2);
    expect(await page.evaluate(()=>window.__vocabularyObservers.filter(item=>item.targets.some(name=>name.includes('vocabulary-continuation'))).length)).toBe(2);
    await expect(main(page).getByText('Loading…',{exact:true})).toHaveCount(2);
    await page.getByRole('navigation',{name:'Workspace',exact:true}).getByRole('link',{name:'Prompt library',exact:true}).click();
    await expect(main(page).getByRole('heading',{level:1})).toHaveText('Prompt library');
    expect(await page.evaluate(()=>window.__vocabularySignals.map(signal=>signal.aborted))).toEqual([true,true]);
    expect(await page.evaluate(()=>window.__vocabularyObservers.filter(item=>item.targets.some(name=>name.includes('vocabulary-continuation'))).map(item=>item.disconnected))).toEqual([true,true]);
    await expect(main(page).locator('.two-col')).toHaveCount(0);
    expect(await page.evaluate(()=>document.getElementById('workspace-main').classList.contains('vocabulary-main'))).toBe(false);
    await page.unroute('**/prompt_manager/vocabulary/**');
    for(const item of held) await item.abort().catch(()=>{});
});

test('390px layout stacks without horizontal page scroll; table scrolls locally',async({page,request})=>{
    manifest=await seed(request,{extra_tags:3});
    await page.setViewportSize({width:390,height:844});
    await page.goto(route);
    await expect(rows(page)).toHaveCount(manifest.tags.length);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
    const layout=await main(page).evaluate(node=>{
        const grid=node.querySelector('.two-col'),[left,right]=grid.querySelectorAll(':scope > .surface'),wrap=node.querySelector('.table-wrap');
        const heading=node.querySelector('.workspace-heading'),operation=node.querySelector('.operation');
        return {tracks:getComputedStyle(grid).gridTemplateColumns.split(' ').length,stacked:right.getBoundingClientRect().top>=left.getBoundingClientRect().bottom,
            overflow:getComputedStyle(wrap).overflowX,heading:getComputedStyle(heading).flexDirection,operation:getComputedStyle(operation).flexDirection,
            actions:node.querySelector('.workspace-heading .heading-actions').getBoundingClientRect().width===heading.getBoundingClientRect().width};
    });
    expect(layout).toEqual({tracks:1,stacked:true,overflow:'auto',heading:'column',operation:'column',actions:true});
});

// Dialogs: the editor's modal shell (concept simpleInput, app.js:1138-1143) with truthful outcomes.
const dialog=page=>page.locator('dialog.vocabulary-dialog');
const button=(scope,name)=>scope.getByRole('button',{name,exact:true});
const field=(scope,label)=>scope.getByLabel(label,{exact:true});
const note=scope=>scope.locator('.warning-note');
const toast=page=>page.locator('#workspace-toast');
const actionNames=scope=>scope.locator('.dialog-actions button').evaluateAll(nodes=>nodes.map(node=>node.textContent.trim()));
const snapshot=async request=>{const response=await request.get('/__test__/vocabulary/snapshot');await expect(response).toBeOK();return response.json();};
const backup=async request=>{const response=await request.get('/__test__/vocabulary/backup');await expect(response).toBeOK();return response.body();};
const restore=async(request,buffer)=>expect(await request.post('/prompt_manager/restore',{multipart:{database_file:{name:'vocabulary.db',mimeType:'application/octet-stream',buffer}}})).toBeOK();
const tagNames=rows=>rows.tags.map(([,name])=>name);
function trackWrites(page) {
    const writes=[];page.on('request',item=>{if(item.method()!=='GET')writes.push({method:item.method(),url:new URL(item.url()).pathname,body:item.postDataJSON()});});return writes;
}
async function openPage(page) {await page.goto(route);await expect(rows(page)).toHaveCount(manifest.tags.length);}
async function openDialog(page,trigger,title) {
    await trigger.click();const box=dialog(page);await expect(box).toBeVisible();
    expect(await box.evaluate(node=>node.matches(':modal'))).toBe(true);
    await expect(box.getByRole('heading',{level:2})).toHaveText(title);
    await expect(button(box,'Close dialog')).toBeFocused();
    return box;
}
const openRename=(page,name)=>openDialog(page,tagRow(page,name).getByRole('button',{name:'Rename / merge',exact:true}),'Rename or merge tag');
const openManage=page=>openDialog(page,categoryRow(page,'Manage category').getByRole('button',{name:'Review',exact:true}),'Manage categories');

test('create tag adds an unused tag that every editor offers',async({page,request})=>{
    const writes=trackWrites(page);await openPage(page);const before=await snapshot(request);
    const box=await openDialog(page,main(page).getByRole('button',{name:'+ Create tag',exact:true}),'Create tag');
    await expect(box).toHaveAttribute('aria-labelledby',await box.getByRole('heading',{level:2}).getAttribute('id'));
    await expect(box.locator('.dialog-header > button')).toHaveText('×');
    await expect(box.locator('.field label')).toHaveText(['Tag name']);
    const input=field(box,'Tag name');await expect(input).toHaveValue('');await expect(input).toHaveAttribute('required','');
    expect(await actionNames(box)).toEqual(['Cancel','Apply']);
    await expect(button(box,'Apply')).toHaveClass(/ca-primary/);await expect(button(box,'Cancel')).not.toHaveClass(/ca-primary/);
    await button(box,'Apply').click();
    await expect(box).toBeVisible();expect(await input.evaluate(node=>node.validity.valueMissing)).toBe(true);expect(writes).toEqual([]);
    await input.fill('sunset');await button(box,'Apply').click();
    await expect(box).toHaveCount(0);
    await expect(toast(page)).toHaveText('Tag created.');
    const row=tagRow(page,'sunset');await expect(row.locator('td:nth-child(2)')).toHaveText('0');
    await expect(row.getByRole('button',{name:'Rename / merge',exact:true})).toBeFocused();
    expect(writes).toEqual([{method:'POST',url:'/prompt_manager/vocabulary/tags',body:{expected_epoch:manifest.epoch,name:'sunset'}}]);
    const after=await snapshot(request);
    expect(tagNames(after)).toEqual([...manifest.tags,'sunset']);
    expect({...after,tags:before.tags}).toEqual(before);
    await page.goto('/prompt_manager/workspace#/library');
    await page.getByRole('button',{name:'+ New prompt',exact:true}).click();
    await expect(page.locator('dialog.library-editor datalist#tag-options option[value="sunset,"]')).toHaveCount(1);
    await page.keyboard.press('Escape');
    await page.goto(route);await expect(tagRow(page,'sunset')).toHaveCount(1);
});

test('rename and merge persist and collapse duplicate links',async({page,request})=>{
    const writes=trackWrites(page);await openPage(page);
    let box=await openRename(page,'blue_hour');
    await expect(box.locator('.field label')).toHaveText(['New name; existing names merge']);
    const input=field(box,'New name; existing names merge');
    await expect(input).toHaveValue('blue_hour');await expect(input).toHaveAttribute('required','');
    expect(await actionNames(box)).toEqual(['Delete tag…','Cancel','Apply']);
    await expect(button(box,'Delete tag…')).toHaveClass(/danger/);await expect(button(box,'Apply')).toHaveClass(/ca-primary/);
    await input.fill('golden_hour');await button(box,'Apply').click();
    await expect(box).toHaveCount(0);await expect(toast(page)).toHaveText('Tag renamed.');
    await expect(tagRow(page,'blue_hour')).toHaveCount(0);
    await expect(tagRow(page,'golden_hour').locator('td:nth-child(2)')).toHaveText('1');
    await expect(tagRow(page,'golden_hour').getByRole('button',{name:'Rename / merge',exact:true})).toBeFocused();
    expect(writes.at(-1)).toMatchObject({method:'PATCH',body:{expected_epoch:manifest.epoch,name:'blue_hour',new_name:'golden_hour'}});
    expect(writes.at(-1).url).toMatch(/^\/prompt_manager\/vocabulary\/tags\/\d+$/);
    // Alpine observatory carries both: merging must leave one link, not two.
    box=await openRename(page,'architecture');
    await field(box,'New name; existing names merge').fill('cinematic');await button(box,'Apply').click();
    await expect(box).toHaveCount(0);await expect(toast(page)).toHaveText('Tag merged without duplicate links.');
    await expect(tagRow(page,'architecture')).toHaveCount(0);
    const target=tagRow(page,'cinematic');await expect(target.locator('td:nth-child(2)')).toHaveText('2');
    await expect(target.getByRole('button',{name:'Rename / merge',exact:true})).toBeFocused();
    const after=await snapshot(request);
    expect(tagNames(after)).not.toContain('architecture');
    const cinematic=after.tags.find(([,name])=>name==='cinematic')[0];
    expect(after.prompt_tags.filter(([,tag])=>tag===cinematic)).toHaveLength(2);
    await page.reload();await expect(tagRow(page,'golden_hour')).toHaveCount(1);await expect(tagRow(page,'architecture')).toHaveCount(0);
    await tagRow(page,'cinematic').getByRole('button',{name:'Browse',exact:true}).click();
    await expectLibrary(page,{tags:'cinematic',count:2});
});

test('manage categories previews real impact before applying',async({page,request})=>{
    const writes=trackWrites(page);await openPage(page);
    let box=await openManage(page);
    await expect(box.locator('.field label')).toHaveText(['Category','Proposed new category name']);
    const source=field(box,'Category'),target=field(box,'Proposed new category name');
    await expect(source.locator('option')).toHaveText(CATEGORIES.map(([name])=>name));
    await expect(source).toHaveValue('Landscapes');
    await expect(target).toHaveValue('');await expect(target).toHaveAttribute('required','');await expect(target).toHaveAttribute('list','categories');
    expect(await box.locator('datalist#categories option').evaluateAll(nodes=>nodes.map(node=>node.value))).toEqual(CATEGORIES.map(([name])=>name));
    expect(await actionNames(box)).toEqual(['Cancel','Preview']);
    await expect(button(box,'Preview')).toHaveClass(/ca-primary/);
    const impact=box.locator('p.small');await expect(impact).toBeHidden();
    await target.fill('Scenery');await button(box,'Preview').click();
    await expect(impact).toHaveText('“Landscapes” is used by 3 positive prompts. Apply renames it to “Scenery”.');
    expect(await actionNames(box)).toEqual(['Cancel','Apply']);
    expect(writes.map(write=>write.url)).toEqual(['/prompt_manager/vocabulary/categories/preview']);
    await target.fill('Sceneries');
    await expect(impact).toBeHidden();expect(await actionNames(box)).toEqual(['Cancel','Preview']);
    await target.fill('Scenery');await button(box,'Preview').click();await expect(impact).toBeVisible();
    await button(box,'Apply').click();
    await expect(box).toHaveCount(0);await expect(toast(page)).toHaveText('Category renamed.');
    await expect(categoryRow(page,'Landscapes')).toHaveCount(0);
    await expect(categoryRow(page,'Scenery').locator('p')).toHaveText('3 positive prompts');
    await expect(categoryRow(page,'Scenery').getByRole('button',{name:'Browse',exact:true})).toBeFocused();
    expect(writes.at(-1)).toMatchObject({url:'/prompt_manager/vocabulary/categories/apply',body:{expected_epoch:manifest.epoch,source:'Landscapes',target:'Scenery',impact_token:expect.stringMatching(/^[0-9a-f]{64}$/)}});

    box=await openManage(page);
    await field(box,'Category').selectOption('Illustration');await field(box,'Proposed new category name').fill('Architecture');
    await button(box,'Preview').click();
    await expect(box.locator('p.small')).toHaveText('“Illustration” is used by 1 positive prompt. Apply consolidates it into the existing “Architecture”.');
    await field(box,'Category').selectOption('Environments');await expect(box.locator('p.small')).toBeHidden();
    await field(box,'Category').selectOption('Illustration');await button(box,'Preview').click();
    await button(box,'Apply').click();
    await expect(box).toHaveCount(0);await expect(toast(page)).toHaveText('Categories consolidated.');
    await expect(categoryRow(page,'Illustration')).toHaveCount(0);
    await expect(categoryRow(page,'Architecture').locator('p')).toHaveText('2 positive prompts');
    await page.reload();await expect(categoryRow(page,'Scenery')).toHaveCount(1);await expect(categoryRow(page,'Illustration')).toHaveCount(0);
    const after=await snapshot(request);
    expect(after.prompts.filter(row=>row[2]==='Scenery')).toHaveLength(3);
});

test('delete tag confirms real impact and removes only the vocabulary name',async({page,request})=>{
    const writes=trackWrites(page);await openPage(page);const before=await snapshot(request);
    let box=await openRename(page,'cinematic');
    await button(box,'Delete tag…').click();
    await expect(box.getByRole('heading',{level:2})).toHaveText('Delete tag');
    await expect(button(box,'Close dialog')).toBeFocused();
    await expect(box.locator('p.small')).toHaveText('Deleting “cinematic” removes the name and its links from 2 positive prompts. The prompts, negative variants, images and history themselves are kept.');
    expect(await actionNames(box)).toEqual(['Cancel','Delete']);
    await expect(button(box,'Delete')).toHaveClass(/danger/);await expect(box.locator('.field')).toHaveCount(0);
    await button(box,'Cancel').click();await expect(box).toHaveCount(0);
    await expect(tagRow(page,'cinematic').getByRole('button',{name:'Rename / merge',exact:true})).toBeFocused();
    expect(writes).toEqual([]);expect(await snapshot(request)).toEqual(before);

    box=await openRename(page,'cinematic');await button(box,'Delete tag…').click();
    await expect(box.locator('p.small')).toBeVisible();await button(box,'Delete').click();
    await expect(box).toHaveCount(0);await expect(toast(page)).toHaveText('Tag deleted.');
    await expect(tagRow(page,'cinematic')).toHaveCount(0);
    await expect(main(page).getByRole('heading',{level:1})).toBeFocused();
    expect(writes).toEqual([{method:'POST',url:expect.stringMatching(/^\/prompt_manager\/vocabulary\/tags\/\d+\/delete$/),body:{expected_epoch:manifest.epoch,name:'cinematic'}}]);
    const after=await snapshot(request);
    const cinematic=before.tags.find(([,name])=>name==='cinematic')[0];
    expect(after.tags).toEqual(before.tags.filter(([id])=>id!==cinematic));
    expect(after.prompt_tags).toEqual(before.prompt_tags.filter(([,tag])=>tag!==cinematic));
    for(const table of ['prompt_pair_tags','prompt_pairs','generated_images','image_prompt_pairs']) expect(after[table]).toEqual(before[table]);
    expect(after.prompts.map(([id,text,category])=>[id,text,category])).toEqual(before.prompts.map(([id,text,category])=>[id,text,category]));
    await page.goto('/prompt_manager/workspace#/library');
    await expect(page.locator('article[data-prompt-id]')).toHaveCount(6);
    await expect(page.locator('.library-chip').filter({hasText:/^cinematic$/})).toHaveCount(0);
    await expect(page.locator('.library-chip').filter({hasText:/^blue_hour$/})).toHaveCount(1);
});

test('cancel and escape write nothing and restore focus',async({page,request})=>{
    const writes=trackWrites(page);await openPage(page);const before=await snapshot(request);
    const create=main(page).getByRole('button',{name:'+ Create tag',exact:true});
    let box=await openDialog(page,create,'Create tag');
    await field(box,'Tag name').fill('never_saved');await page.keyboard.press('Escape');
    await expect(box).toHaveCount(0);await expect(create).toBeFocused();
    box=await openRename(page,'mist');await field(box,'New name; existing names merge').fill('never_renamed');
    await button(box,'Close dialog').click();await expect(box).toHaveCount(0);
    await expect(tagRow(page,'mist').getByRole('button',{name:'Rename / merge',exact:true})).toBeFocused();
    box=await openRename(page,'mist');await button(box,'Delete tag…').click();await expect(box.locator('p.small')).toBeVisible();
    await page.keyboard.press('Escape');await expect(box).toHaveCount(0);
    await expect(tagRow(page,'mist').getByRole('button',{name:'Rename / merge',exact:true})).toBeFocused();
    box=await openManage(page);await field(box,'Proposed new category name').fill('Scenery');await button(box,'Preview').click();
    await expect(box.locator('p.small')).toBeVisible();await button(box,'Cancel').click();
    await expect(box).toHaveCount(0);await expect(categoryRow(page,'Manage category').getByRole('button',{name:'Review',exact:true})).toBeFocused();
    expect(writes.map(write=>write.url)).toEqual(['/prompt_manager/vocabulary/categories/preview']);
    expect(await snapshot(request)).toEqual(before);
    await expect(toast(page)).toHaveText('');
});

test('conflict and restore states keep the dialog and write nothing',async({page,request})=>{
    const saved=await backup(request);const writes=trackWrites(page);await openPage(page);
    let box=await openRename(page,'mist');
    await expect(await request.put('/prompt_manager/tags/mist',{data:{new_name:'fog'}})).toBeOK();
    await field(box,'New name; existing names merge').fill('haze');await button(box,'Apply').click();
    await expect(note(box)).toHaveText('This tag changed since this dialog opened. Nothing was changed. The list was re-read; review it before trying again.');
    await expect(box).toBeVisible();await expect(button(box,'Apply')).toBeDisabled();
    await expect(tagRow(page,'fog')).toHaveCount(1);await expect(tagRow(page,'mist')).toHaveCount(0);
    expect(tagNames(await snapshot(request))).not.toContain('haze');
    await button(box,'Cancel').click();await expect(box).toHaveCount(0);

    box=await openManage(page);await field(box,'Proposed new category name').fill('Scenery');await button(box,'Preview').click();
    await expect(box.locator('p.small')).toHaveText('“Landscapes” is used by 3 positive prompts. Apply renames it to “Scenery”.');
    const geometry=manifest.recipes[1].positive_id;
    const current=await(await request.get(`/prompt_manager/library/${geometry}`)).json();
    await expect(await request.patch(`/prompt_manager/library/${geometry}`,{data:{expected_epoch:current.library_epoch,if_match:current.positive.edit_token,metadata:{category:'Landscapes'}}})).toBeOK();
    await button(box,'Apply').click();
    await expect(note(box)).toHaveText('This category changed since this dialog opened. Nothing was changed. Preview again before applying.');
    expect(await actionNames(box)).toEqual(['Cancel','Preview']);await expect(box.locator('p.small')).toBeHidden();
    expect((await snapshot(request)).prompts.some(row=>row[2]==='Scenery')).toBe(false);
    await button(box,'Preview').click();
    await expect(box.locator('p.small')).toHaveText('“Landscapes” is used by 4 positive prompts. Apply renames it to “Scenery”.');
    await expect(note(box)).toBeHidden();
    await button(box,'Cancel').click();

    const before=await snapshot(request);
    box=await openDialog(page,main(page).getByRole('button',{name:'+ Create tag',exact:true}),'Create tag');
    await restore(request,saved);
    await field(box,'Tag name').fill('sunrise');await button(box,'Apply').click();
    await expect(note(box)).toHaveText('Prompt storage was restored since this dialog opened. Nothing was changed.');
    await expect(button(box,'Apply')).toBeDisabled();
    expect(await actionNames(box)).toEqual(['Reload','Cancel','Apply']);
    const restored=await snapshot(request);expect(tagNames(restored)).not.toContain('sunrise');expect(restored.tags).not.toEqual(before.tags);
    await button(box,'Reload').click();await expect(box).toHaveCount(0);
    await expect(main(page).getByRole('heading',{level:1})).toBeFocused();
    await expect(tagRow(page,'mist')).toHaveCount(1);await expect(tagRow(page,'fog')).toHaveCount(0);
    expect(writes.filter(write=>write.url==='/prompt_manager/vocabulary/tags')).toHaveLength(1);
});

test('lost response is outcome-unknown',async({page,request})=>{
    const writes=trackWrites(page);await openPage(page);
    await page.route('**/prompt_manager/vocabulary/tags/*',async route=>{
        if(route.request().method()!=='PATCH')return route.continue();
        await route.fetch();await route.abort('failed');
    });
    const box=await openRename(page,'forest');
    await field(box,'New name; existing names merge').fill('woodland');await button(box,'Apply').click();
    await expect(note(box)).toHaveText('The result could not be confirmed. The lists were re-read; check them before applying again.');
    await expect(box).toBeVisible();
    await expect(tagRow(page,'woodland')).toHaveCount(1);await expect(tagRow(page,'forest')).toHaveCount(0);
    expect(tagNames(await snapshot(request))).toContain('woodland');
    // Never resent on its own.
    await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,300)));
    expect(writes.filter(write=>write.method==='PATCH')).toHaveLength(1);
    await expect(button(box,'Apply')).toBeEnabled();
    await button(box,'Cancel').click();await expect(box).toHaveCount(0);
    await expect(main(page).getByRole('heading',{level:1})).toBeFocused();
});

test('a failed Load more read moves keyboard focus to Retry',async({page,request})=>{
    manifest=await seed(request,{extra_tags:211});
    await page.route('**/prompt_manager/vocabulary/tags?*',route=>{
        if(new URL(route.request().url()).searchParams.has('cursor')) return route.fulfill({status:500,json:{success:false,error:'Database is unavailable'}});
        return route.continue();
    });
    await page.goto(route);await expect(rows(page)).toHaveCount(100);
    await loadMore(page).evaluate(button=>button.focus({preventScroll:true}));
    await page.keyboard.press('Enter');
    const retry=surfaces(page).first().getByRole('button',{name:'Retry',exact:true});
    await expect(retry).toBeVisible();await expect(retry).toBeFocused();
    await expect(rows(page)).toHaveCount(100);
});
