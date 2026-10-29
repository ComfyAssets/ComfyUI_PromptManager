import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import {test,expect} from './fixtures.mjs';
import {armUnseenRestores,unseenRestore} from './unseen-restore.mjs';

// #437 D148: select prompts on the Library shelf and set their category in one step (the legacy admin's Set Category).
const route='/prompt_manager/workspace#/library';
const cards=page=>page.locator('article[data-prompt-id]');
const card=(page,id)=>page.locator(`article[data-prompt-id="${id}"]`);
const box=(page,id)=>card(page,id).locator('.image-overlays input[type="checkbox"]');
const bar=page=>page.locator('#library-bulkbar > .bulkbar');
const dialog=page=>page.getByRole('dialog',{name:'Categorize selected prompts',exact:true});
const toast=page=>page.locator('.toast');
const heading=page=>page.locator('#workspace-main h1');
const tagsDialog=page=>page.getByRole('dialog',{name:'Tag selected prompts',exact:true});
const chips=(page,id)=>card(page,id).locator('.tags > .chip');
async function addTags(page,value) {
    await bar(page).getByRole('button',{name:'Add tags',exact:true}).click();
    await expect(tagsDialog(page)).toBeVisible();
    await tagsDialog(page).locator('#action-input').fill(value);
    await tagsDialog(page).getByRole('button',{name:'Apply',exact:true}).click();
}

async function shelf(request) {
    const body=await(await request.get('/prompt_manager/library?limit=100')).json();
    return body.items;
}
/** Every POST to the bulk category route, with its body. */
function recordWrites(page) {
    const writes=[];
    page.on('request',item=>{if(item.method()==='POST'&&new URL(item.url()).pathname==='/prompt_manager/bulk/category')writes.push(item.postDataJSON());});
    return writes;
}
/** The first mounted card's index once two frames in a row agree (the shelf's window follows scrolling a frame later). */
async function stableFirstIndex(page) {
    return page.evaluate(()=>new Promise(resolve=>{
        const first=()=>document.querySelector('article[data-prompt-id]')?.dataset.index;
        let previous=first();
        const check=()=>requestAnimationFrame(()=>requestAnimationFrame(()=>{const now=first();if(now===previous)resolve(Number(now));else{previous=now;check();}}));
        check();
    }));
}
async function setCategory(page,value) {
    await bar(page).getByRole('button',{name:'Set category',exact:true}).click();
    await expect(dialog(page)).toBeVisible();
    await dialog(page).locator('#action-input').fill(value);
    await dialog(page).getByRole('button',{name:'Apply',exact:true}).click();
}

test.beforeEach(async({page,request,browserName})=>{
    await startCoverage(page,browserName);await expect(await request.post('/__test__/reset',{data:{native_library:true}})).toBeOK();
    page.libraryErrors=[];page.on('pageerror',error=>page.libraryErrors.push(error.message));
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.libraryErrors).toEqual([]);});

test('Set category writes the selected prompts on the server, reloads the shelf and clears the selection',async({page,request})=>{
    const items=await shelf(request);const [first,second,third]=items;
    const writes=recordWrites(page);
    await page.goto(route);await expect(cards(page)).toHaveCount(items.length);
    await expect(page.locator('#library-bulkbar')).toBeEmpty();
    // The checkbox is named after the card's title, as the Images overlay names its file.
    await expect(box(page,first.id)).toHaveAccessibleName(new RegExp(`^Select `));
    await box(page,first.id).check();await expect(bar(page).locator('strong')).toHaveText('1 selected');
    await box(page,second.id).check();await expect(bar(page).locator('strong')).toHaveText('2 selected');
    // Two of three loaded are checked, so Select all N loaded follows the count (D151, #444 ruling).
    await expect(bar(page).getByRole('button')).toHaveText([`Select all ${items.length} loaded`,'Add tags','Set category','Clear']);
    await setCategory(page,'  Studio set  ');
    await expect(toast(page)).toHaveText('Category “Studio set” set on 2 prompts.');
    await expect(dialog(page)).toHaveCount(0);
    await expect(page.locator('#library-bulkbar')).toBeEmpty();
    for(const item of [first,second]) await expect(card(page,item.id).locator('.card-title .chip')).toHaveText('Studio set');
    await expect(card(page,third.id).locator('.card-title .chip')).toHaveText(third.category || 'Uncategorized');
    await expect(box(page,first.id)).not.toBeChecked();
    const after=await shelf(request);
    expect(after.filter(item=>item.category==='Studio set').map(item=>item.id).sort()).toEqual([first.id,second.id].sort());
    const epoch=(await(await request.get('/prompt_manager/library/options')).json()).library_epoch;
    expect(writes).toEqual([{prompt_ids:[first.id,second.id],category:'Studio set',expected_epoch:epoch}]);
    // The toolbar's category list now offers the new value, and so does the next dialog.
    await expect(page.locator('#category-filter option',{hasText:'Studio set'})).toHaveCount(1);
    await box(page,third.id).check();
    await bar(page).getByRole('button',{name:'Set category',exact:true}).click();
    await expect(dialog(page).locator('datalist option[value="Studio set"]')).toHaveCount(1);
    await dialog(page).getByRole('button',{name:'Cancel',exact:true}).click();await expect(dialog(page)).toHaveCount(0);
    expect(writes).toHaveLength(1);
});

test('a blank or control-character category keeps the dialog open; Clear and Escape clear the selection',async({page,request})=>{
    const items=await shelf(request);const writes=recordWrites(page);
    await page.goto(route);await expect(cards(page)).toHaveCount(items.length);
    await box(page,items[0].id).check();
    await bar(page).getByRole('button',{name:'Set category',exact:true}).click();
    const field=dialog(page).locator('#action-input');
    await field.fill('   ');await dialog(page).getByRole('button',{name:'Apply',exact:true}).click();
    await expect(dialog(page)).toBeVisible();await expect(field).toBeFocused();
    await field.fill('bad\tvalue');await dialog(page).getByRole('button',{name:'Apply',exact:true}).click();
    await expect(dialog(page)).toBeVisible();
    expect(await field.evaluate(node=>node.validationMessage)).toBe('A category cannot contain control characters.');
    await dialog(page).getByRole('button',{name:'Cancel',exact:true}).click();await expect(dialog(page)).toHaveCount(0);
    expect(writes).toHaveLength(0);
    await bar(page).getByRole('button',{name:'Clear',exact:true}).click();
    await expect(page.locator('#library-bulkbar')).toBeEmpty();await expect(heading(page)).toBeFocused();
    await expect(box(page,items[0].id)).not.toBeChecked();
    await box(page,items[1].id).check();
    await bar(page).getByRole('button',{name:'Set category',exact:true}).focus();await page.keyboard.press('Escape');
    await expect(page.locator('#library-bulkbar')).toBeEmpty();await expect(heading(page)).toBeFocused();
    // A filter change replaces the shelf and clears the selection too.
    await box(page,items[1].id).check();await expect(bar(page)).toBeVisible();
    await page.locator('#search').fill('portrait');
    await expect(page.locator('#library-bulkbar')).toBeEmpty();
});

test('Shift+click selects a range across windowed cards, Tab reaches the checkbox at the window edge, and a return keeps the fence',async({page,request,isMobile})=>{
    await expect(await request.post('/__test__/reset',{data:{native_library:true,extra_prompts:200}})).toBeOK();
    if(!isMobile)await page.setViewportSize({width:1600,height:1100});
    const writes=recordWrites(page);
    await page.goto(route);await expect(cards(page).first()).toBeVisible();
    const more=page.getByRole('button',{name:'Load more prompts',exact:true});
    for(let index=0;index<3;index++) {
        await expect(more).toHaveAttribute('aria-disabled','false');
        const response=page.waitForResponse(item=>{const url=new URL(item.url());return url.pathname==='/prompt_manager/library'&&url.searchParams.has('cursor');});
        await more.evaluate(node=>node.click());await response;
    }
    await expect(more).toHaveAttribute('aria-disabled','false');
    const firstId=Number(await cards(page).first().getAttribute('data-prompt-id'));
    await box(page,firstId).check();
    // Windowed: fewer cards are mounted than are loaded.
    expect(await cards(page).count()).toBeLessThan(160);
    // Tab from the last mounted card's last control lands on the next (unmounted) card's checkbox.
    const last=cards(page).last();const lastIndex=Number(await last.getAttribute('data-index'));
    await last.locator('button').last().focus();await page.keyboard.press('Tab');
    const next=page.locator(`article[data-index="${lastIndex+1}"]`);
    await expect(next.locator('.image-overlays input')).toBeFocused();
    // Shift+click that card's checkbox: every card from the first to it is selected, mounted or not.
    await next.locator('.image-overlays > label').click({modifiers:['Shift']});
    await expect(bar(page).locator('strong')).toHaveText(`${lastIndex+2} selected`);
    // Shift+Tab from a checkbox whose previous card is unmounted lands on that card's last control.
    // Halfway down the loaded cards (short of the continuation, so no page loads), once the window stops moving.
    await page.evaluate(()=>{const grid=document.querySelector('.library-grid');scrollTo(0,grid.getBoundingClientRect().top+scrollY+grid.offsetHeight/2);});
    await expect.poll(()=>page.locator('article[data-index="0"]').count()).toBe(0);
    const topIndex=await stableFirstIndex(page);
    await page.locator(`article[data-index="${topIndex}"] .image-overlays input`).focus();await page.keyboard.press('Shift+Tab');
    await expect(page.locator(`article[data-index="${topIndex-1}"] .card-footer .row-actions > button`).last()).toBeFocused();
    // Leaving for a prompt's history and coming back restores the shelf without a read; its epoch still fences.
    await page.locator(`article[data-index="${topIndex}"]`).getByRole('button',{name:'Explore history',exact:true}).click();
    await page.getByRole('button',{name:'← Prompt library',exact:true}).click();
    await expect(cards(page).first()).toBeVisible();await expect(page.locator('#library-bulkbar')).toBeEmpty();
    const visible=Number(await cards(page).first().getAttribute('data-prompt-id'));
    await box(page,visible).check();await setCategory(page,'Returned');
    await expect(toast(page)).toHaveText('Category “Returned” set on 1 prompt.');
    const epoch=(await(await request.get('/prompt_manager/library/options')).json()).library_epoch;
    expect(writes).toEqual([{prompt_ids:[visible],category:'Returned',expected_epoch:epoch}]);
});

test('a restore the page did not see refuses the write, changes nothing and reloads the shelf',async({page,request})=>{
    await armUnseenRestores(page);
    const items=await shelf(request);
    await page.goto(route);await expect(cards(page)).toHaveCount(items.length);
    await box(page,items[0].id).check();
    const backup=await request.get('/prompt_manager/backup');await expect(backup).toBeOK();const buffer=await backup.body();
    const restore=await unseenRestore(page,()=>request.post('/prompt_manager/restore',{multipart:{database_file:{name:'library.db',mimeType:'application/octet-stream',buffer}}}));
    await expect(restore).toBeOK();
    const reload=page.waitForResponse(item=>{const url=new URL(item.url());return url.pathname==='/prompt_manager/library'&&!url.searchParams.has('cursor');});
    await setCategory(page,'After restore');
    await expect(toast(page)).toHaveText('Prompt storage was restored since this page loaded. No category was changed.');
    await reload;await expect(page.locator('#library-bulkbar')).toBeEmpty();
    expect((await shelf(request)).filter(item=>item.category==='After restore')).toEqual([]);
    // The reloaded shelf reads the new epoch: the next write goes through.
    await box(page,items[0].id).check();await setCategory(page,'After reload');
    await expect(toast(page)).toHaveText('Category “After reload” set on 1 prompt.');
});

test('an answer that may follow a committed write says so and reloads; a refused write keeps the selection',async({page,request})=>{
    const items=await shelf(request);
    await page.goto(route);await expect(cards(page)).toHaveCount(items.length);
    await box(page,items[0].id).check();
    await page.route('**/prompt_manager/bulk/category',route=>route.fulfill({status:500,contentType:'application/json',body:'{"success":false,"error":"boom"}'}),{times:1});
    await setCategory(page,'Refused');
    await expect(toast(page)).toHaveText('Category could not be set. Try again.');
    await expect(bar(page).locator('strong')).toHaveText('1 selected');
    let sent=false;
    await page.route('**/prompt_manager/bulk/category',async route=>{sent=true;await route.fetch();await route.abort('failed');},{times:1});
    await setCategory(page,'Unconfirmed');
    await expect(toast(page)).toHaveText('The category change could not be confirmed. The Library was reloaded.');
    expect(sent).toBe(true);
    await expect(page.locator('#library-bulkbar')).toBeEmpty();
    await expect(card(page,items[0].id).locator('.card-title .chip')).toHaveText('Unconfirmed');
});

// #442: Add tags writes prompt tags (never image tags) on the selected prompts.
function recordTagWrites(page) {
    const writes=[];
    page.on('request',item=>{if(item.method()==='POST'&&new URL(item.url()).pathname==='/prompt_manager/bulk/tags')writes.push(item.postDataJSON());});
    return writes;
}
async function promptTags(request,id) {
    return (await shelf(request)).find(item=>item.id===id).tags;
}

test('Add tags adds prompt tags to the selected prompts, suggests the vocabulary and reloads the shelf',async({page,request})=>{
    const items=await shelf(request);const [first,second,third]=items;const writes=recordTagWrites(page);
    const vocabulary=(await(await request.get('/prompt_manager/tags')).json()).tags;
    await page.goto(route);await expect(cards(page)).toHaveCount(items.length);
    await box(page,first.id).check();await box(page,second.id).check();
    await bar(page).getByRole('button',{name:'Add tags',exact:true}).click();
    await expect(tagsDialog(page).locator('datalist option')).toHaveCount(vocabulary.length);
    await tagsDialog(page).getByRole('button',{name:'Cancel',exact:true}).click();await expect(tagsDialog(page)).toHaveCount(0);
    await addTags(page,' studio-lit , Night ,studio-lit');
    await expect(toast(page)).toHaveText('Added “studio-lit, Night” to 2 prompts.');
    await expect(page.locator('#library-bulkbar')).toBeEmpty();
    for(const item of [first,second]) {
        await expect(chips(page,item.id).filter({hasText:'studio-lit'})).toHaveCount(1);
        expect(await promptTags(request,item.id)).toEqual(expect.arrayContaining(['studio-lit','Night']));
    }
    expect(await promptTags(request,third.id)).not.toContain('studio-lit');
    const epoch=(await(await request.get('/prompt_manager/library/options')).json()).library_epoch;
    expect(writes).toEqual([{prompt_ids:[first.id,second.id],tags:['studio-lit','Night'],expected_epoch:epoch}]);
    // A second add of the same names changes nothing and says so.
    await box(page,first.id).check();await addTags(page,'Studio-Lit');
    await expect(toast(page)).toHaveText('1 prompt already had “Studio-Lit”.');
});

test('the Add tags field states what it refuses, and a restore the page did not see adds nothing',async({page,request})=>{
    await armUnseenRestores(page);
    const items=await shelf(request);const writes=recordTagWrites(page);
    await page.goto(route);await expect(cards(page)).toHaveCount(items.length);
    await box(page,items[0].id).check();
    await bar(page).getByRole('button',{name:'Add tags',exact:true}).click();
    const field=tagsDialog(page).locator('#action-input');
    for(const [value,message] of [[' , ','Enter at least one tag.'],['x'.repeat(51),'A tag can have at most 50 characters.'],
        [Array.from({length:21},(_,i)=>`t${i}`).join(','),'Enter at most 20 tags.']]) {
        await field.fill(value);await tagsDialog(page).getByRole('button',{name:'Apply',exact:true}).click();
        await expect(tagsDialog(page)).toBeVisible();
        expect(await field.evaluate(node=>node.validationMessage)).toBe(message);
    }
    await tagsDialog(page).getByRole('button',{name:'Cancel',exact:true}).click();
    expect(writes).toHaveLength(0);
    const backup=await request.get('/prompt_manager/backup');await expect(backup).toBeOK();const buffer=await backup.body();
    const restore=await unseenRestore(page,()=>request.post('/prompt_manager/restore',{multipart:{database_file:{name:'library.db',mimeType:'application/octet-stream',buffer}}}));
    await expect(restore).toBeOK();
    await addTags(page,'after-restore');
    await expect(toast(page)).toHaveText('Prompt storage was restored since this page loaded. No tags were added.');
    await expect(page.locator('#library-bulkbar')).toBeEmpty();
    expect((await(await request.get('/prompt_manager/tags')).json()).tags).not.toContain('after-restore');
});

// #444 ruling (D151): select every loaded prompt, as the legacy page's select-all checkbox did for its page.
test('Ctrl/Cmd+A and Select all N loaded select every loaded prompt, windowed ones included',async({page,request,isMobile})=>{
    await expect(await request.post('/__test__/reset',{data:{native_library:true,extra_prompts:200}})).toBeOK();
    if(!isMobile)await page.setViewportSize({width:1600,height:1100});
    const writes=recordTagWrites(page);
    await page.goto(route);await expect(cards(page).first()).toBeVisible();
    const more=page.getByRole('button',{name:'Load more prompts',exact:true});
    for(let index=0;index<3;index++) {
        await expect(more).toHaveAttribute('aria-disabled','false');
        const response=page.waitForResponse(item=>{const url=new URL(item.url());return url.pathname==='/prompt_manager/library'&&url.searchParams.has('cursor');});
        await more.evaluate(node=>node.click());await response;
    }
    await expect(more).toHaveAttribute('aria-disabled','false');
    const loaded=160;expect(await cards(page).count()).toBeLessThan(loaded);
    // In the search box Ctrl/Cmd+A keeps its text meaning: it selects the box's text, no prompt.
    await page.locator('#search').focus();await page.keyboard.press('ControlOrMeta+a');
    await expect(page.locator('#library-bulkbar')).toBeEmpty();
    // One checked card offers the rest; the offer selects every loaded prompt and focus moves to the first action.
    const firstId=Number(await cards(page).first().getAttribute('data-prompt-id'));
    await box(page,firstId).check();
    const offer=bar(page).getByRole('button',{name:`Select all ${loaded} loaded`,exact:true});
    await expect(bar(page).locator(':scope > *').nth(1)).toHaveText(`Select all ${loaded} loaded`);
    await offer.click();
    await expect(bar(page).locator('strong')).toHaveText(`${loaded} selected`);
    await expect(offer).toHaveCount(0);
    await expect(bar(page).getByRole('button',{name:'Add tags',exact:true})).toBeFocused();
    // Another batch arrives unchecked: the offer comes back with the new N.
    {
        const response=page.waitForResponse(item=>{const url=new URL(item.url());return url.pathname==='/prompt_manager/library'&&url.searchParams.has('cursor');});
        await more.evaluate(node=>node.click());await response;
    }
    await expect(bar(page).getByRole('button',{name:`Select all ${loaded+40} loaded`,exact:true})).toBeVisible();
    await expect(bar(page).locator('strong')).toHaveText(`${loaded} selected`);
    // Escape in the grid clears; Ctrl/Cmd+A in the grid selects them all again.
    const title=cards(page).first().locator('.card-title > button');
    await title.focus();await page.keyboard.press('Escape');
    await expect(page.locator('#library-bulkbar')).toBeEmpty();await expect(title).toBeFocused();
    await page.keyboard.press('ControlOrMeta+a');
    const all=loaded+40;
    await expect(bar(page).locator('strong')).toHaveText(`${all} selected`);
    // While the write runs, every bar action and the offer are aria-disabled.
    await box(page,firstId).uncheck();
    let release;const held=new Promise(resolve=>{release=resolve;});
    await page.route('**/prompt_manager/bulk/tags',async route=>{await held;await route.continue();},{times:1});
    await addTags(page,'everyone');
    for(const name of ['Add tags','Set category',`Select all ${all} loaded`]) await expect(bar(page).getByRole('button',{name,exact:true})).toHaveAttribute('aria-disabled','true');
    release();
    await expect(toast(page)).toHaveText(`Added “everyone” to ${all-1} prompts.`);
    expect(writes).toHaveLength(1);expect(new Set(writes[0].prompt_ids).size).toBe(all-1);
});
