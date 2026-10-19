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
    await expect(operations.locator('p')).toHaveText([...CATEGORIES.map(([,count])=>`${count} positive prompts`),'Rename or consolidate a category with a preview of affected recipes.']);
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
