import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import {test,expect} from './fixtures.mjs';
import {armUnseenRestores,unseenRestore} from './unseen-restore.mjs';

// #443 D152: Delete… on the Library bar deletes unpaired prompts, archives paired ones (restorable), keeps protected ones.
const route='/prompt_manager/workspace#/library';
const PREVIEW='/prompt_manager/library/delete/preview';
const DELETE='/prompt_manager/library/delete';
const cards=page=>page.locator('article[data-prompt-id]');
const card=(page,id)=>page.locator(`article[data-prompt-id="${id}"]`);
const box=(page,id)=>card(page,id).locator('.image-overlays input[type="checkbox"]');
const bar=page=>page.locator('#library-bulkbar > .bulkbar');
const toast=page=>page.locator('.toast');
const review=page=>page.getByRole('dialog');
const showArchived=page=>page.locator('#workspace-main .heading-actions').getByRole('button',{name:/^Show archived prompts/});
// #482: on the archived shelf the same button reads the concept's way back (app.js 594 "← Prompt library").
const backToLibrary=page=>page.locator('#workspace-main .heading-actions').getByRole('button',{name:'← Prompt library',exact:true});

async function items(request,shelf='active') {
    const query=shelf==='archived'?'&shelf=archived':'';
    return (await(await request.get(`/prompt_manager/library?limit=100${query}`)).json()).items;
}
const byText=(list,text)=>list.find(item=>item.text===text);
function recordPosts(page,path) {
    const posts=[];
    page.on('request',item=>{if(item.method()==='POST'&&new URL(item.url()).pathname===path)posts.push(item.postDataJSON());});
    return posts;
}
async function openReview(page) {
    await bar(page).getByRole('button',{name:'Delete…',exact:true}).click();
    await expect(review(page)).toBeVisible();
}

test.beforeEach(async({page,request,browserName})=>{
    await startCoverage(page,browserName);await expect(await request.post('/__test__/reset',{data:{native_library:true,extra_prompts:2}})).toBeOK();
    page.libraryErrors=[];page.on('pageerror',error=>page.libraryErrors.push(error.message));
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.libraryErrors).toEqual([]);});

test('Delete… reviews the selection, deletes the unpaired, archives the paired and keeps the protected',async({page,request})=>{
    const before=await items(request);
    const dog=byText(before,'Dog landscape'),cat=byText(before,'Cat portrait'),guarded=byText(before,'Library extra 0000');
    await expect(await request.post('/prompt_manager/bulk/tags',{data:{prompt_ids:[guarded.id],tags:['__protected__']}})).toBeOK();
    const previews=recordPosts(page,PREVIEW),writes=recordPosts(page,DELETE);
    await page.goto(route);await expect(cards(page)).toHaveCount(before.length);
    for(const item of [dog,cat,guarded]) await box(page,item.id).check();
    await expect(bar(page).getByRole('button')).toHaveText([`Select all ${before.length} loaded`,'Add tags','Set category','Delete…','Clear']);
    await expect(bar(page).getByRole('button',{name:'Delete…',exact:true})).toHaveClass(/danger/);
    await openReview(page);
    await expect(review(page)).toHaveAccessibleName('Delete or archive selected prompts');
    const grid=review(page).locator('dl.meta-grid > div');
    await expect(grid.locator('dt')).toHaveText(['Delete permanently','Archive','Protected (kept)','Image links removed','Backup']);
    await expect(grid.locator('dd')).toHaveText(['1 prompt not in a saved pair','1 prompt in a saved pair: hidden, restorable',
        '1: Library extra 0000','0 images will no longer show these prompts (files stay on disk)','Create and verify before deleting']);
    await expect(review(page).locator('p.small')).toHaveCount(3);
    await expect(review(page).getByRole('button',{name:'Delete 1 and archive 1 prompt',exact:true})).toHaveClass(/danger/);
    // Cancel writes nothing and releases the bar.
    await review(page).getByRole('button',{name:'Cancel',exact:true}).click();
    await expect(review(page)).toHaveCount(0);expect(writes).toEqual([]);
    await expect(bar(page).getByRole('button',{name:'Delete…',exact:true})).not.toHaveAttribute('aria-disabled','true');
    await openReview(page);
    await review(page).getByRole('button',{name:'Delete 1 and archive 1 prompt',exact:true}).click();
    await expect(toast(page)).toHaveText(/^Deleted 1 prompt\. Archived 1 prompt\. 1 protected prompt was kept\. Safety backup saved: \S+\.backup_\S+ \(System › Backup & export\)\.$/);
    await expect(page.locator('#library-bulkbar')).toBeEmpty();
    await expect(card(page,dog.id)).toHaveCount(0);await expect(card(page,cat.id)).toHaveCount(0);await expect(card(page,guarded.id)).toHaveCount(1);
    const epoch=(await(await request.get('/prompt_manager/library/options')).json()).library_epoch;
    // One preview per review opened (Cancel, then Delete…).
    const reviewed={prompt_ids:[dog.id,cat.id,guarded.id],expected_epoch:epoch};
    expect(previews).toEqual([reviewed,reviewed]);
    expect(writes).toEqual([{archive_ids:[cat.id],delete_ids:[dog.id],expected_epoch:epoch,backup:'on'}]);
    const after=await items(request);
    expect(byText(after,'Dog landscape')).toBeUndefined();expect(byText(after,'Cat portrait')).toBeUndefined();
    expect((await items(request,'archived')).map(item=>item.id)).toEqual([cat.id]);
    await expect(showArchived(page)).toHaveText('Show archived prompts (1)');
});

test('Show archived prompts lists archived prompts, and Restore on its bar returns them',async({page,request})=>{
    const before=await items(request);const cat=byText(before,'Cat portrait'),dual=byText(before,'Dual role portrait');
    await page.goto(route);await expect(cards(page)).toHaveCount(before.length);
    await box(page,cat.id).check();await box(page,dual.id).check();
    await openReview(page);
    await expect(review(page)).toHaveAccessibleName('Archive selected prompts');
    // The archive reason is the saved pair, for one or many prompts (live note on #470): never negative variants.
    await expect(review(page).locator('dl.meta-grid > div').filter({hasText:'Archive'}).locator('dd')).toHaveText('2 prompts in a saved pair: hidden, restorable');
    await review(page).getByRole('button',{name:'Archive 2 prompts',exact:true}).click();
    await expect(toast(page)).toHaveText('Archived 2 prompts.');
    await expect(cards(page)).toHaveCount(before.length-2);
    // #482: a plain button that names where it goes (no pressed state that its changing label would contradict).
    await expect(showArchived(page)).not.toHaveAttribute('aria-pressed');
    await showArchived(page).click();
    await expect(backToLibrary(page)).toBeVisible();await expect(backToLibrary(page)).not.toHaveAttribute('aria-pressed');
    await expect(showArchived(page)).toHaveCount(0);
    await expect(cards(page)).toHaveCount(2);
    await expect(page.locator('.section-caption > span').first()).toHaveText(/^2 archived prompts · \d+ negative variants? hidden with them$/);
    await expect(card(page,cat.id).locator('.card-title .chip',{hasText:'Archived'})).toHaveCount(1);
    await box(page,cat.id).check();
    // #492: the archived shelf offers Delete… after Restore.
    await expect(bar(page).getByRole('button')).toHaveText(['Select all 2 loaded','Restore','Delete…','Clear']);
    await bar(page).getByRole('button',{name:'Restore',exact:true}).click();
    await expect(toast(page)).toHaveText('Restored 1 prompt to the Prompt library.');
    await expect(cards(page)).toHaveCount(1);
    await backToLibrary(page).click();
    await expect(showArchived(page)).toHaveText('Show archived prompts (1)');
    await expect(cards(page)).toHaveCount(before.length-1);
    await expect(card(page,cat.id)).toHaveCount(1);
    expect((await items(request,'archived')).map(item=>item.id)).toEqual([dual.id]);
    // An empty archived view says so.
    await showArchived(page).click();await box(page,dual.id).check();
    await bar(page).getByRole('button',{name:'Restore',exact:true}).click();
    await expect(page.locator('.library-status')).toHaveText('No archived prompts.');
});

// #482 (D154): a prompt in a saved pair can be deleted permanently, with its own saved pairs; Archive stays the default.
const choice=page=>review(page).getByRole('radiogroup',{name:'Prompts in a saved pair'});
const row=(page,term)=>review(page).locator('dl.meta-grid > div').filter({has:page.locator('dt',{hasText:new RegExp(`^${term}$`)})}).locator('dd');
test('Delete permanently removes a paired prompt with its own saved pair; its negative stays (#482)',async({page,request})=>{
    const before=await items(request);const dual=byText(before,'Dual role portrait'),cat=byText(before,'Cat portrait');
    const writes=recordPosts(page,DELETE);
    await page.goto(route);await expect(cards(page)).toHaveCount(before.length);
    await box(page,dual.id).check();await openReview(page);
    // Archive is checked: the review is #443's.
    await expect(choice(page).getByRole('radio',{name:'Archive (hidden, restorable)'})).toBeChecked();
    // The D90 radio row (the auto-tag "How to apply"): each radio keeps its own size beside its label.
    expect(await choice(page).evaluate(group=>{const label=group.querySelector('label'),radio=label.querySelector('input');
        return {group:group.className,display:getComputedStyle(label).display,gap:getComputedStyle(label).columnGap,
            margin:getComputedStyle(radio).marginLeft,full:radio.getBoundingClientRect().width>=label.getBoundingClientRect().width/2};}))
        .toEqual({group:'field',display:'flex',gap:'8px',margin:'0px',full:false});
    await expect(review(page).getByRole('button',{name:'Archive 1 prompt',exact:true})).toBeVisible();
    await choice(page).getByRole('radio',{name:'Delete permanently, with their saved pairs'}).check();
    await expect(review(page)).toHaveAccessibleName('Delete selected prompts');
    await expect(row(page,'Delete permanently')).toHaveText('1 prompt in a saved pair');
    await expect(row(page,'Saved pairs removed')).toHaveText('1 saved pair (1 with a negative prompt)');
    await expect(row(page,'Negatives kept')).toHaveText('1 negative prompt is kept');
    await expect(row(page,'Backup')).toHaveText('Create and verify before deleting');
    await expect(review(page).locator('p.small').first()).toHaveText(/^Deleting a prompt in a saved pair also removes its saved pairs/);
    // Back to Archive: the review is #443's again.
    await choice(page).getByRole('radio',{name:'Archive (hidden, restorable)'}).check();
    await expect(row(page,'Saved pairs removed')).toHaveCount(0);
    await expect(review(page).getByRole('button',{name:'Archive 1 prompt',exact:true})).toBeVisible();
    await choice(page).getByRole('radio',{name:'Delete permanently, with their saved pairs'}).check();
    await review(page).getByRole('button',{name:'Delete 1 prompt',exact:true}).click();
    await expect(toast(page)).toHaveText(/^Deleted 1 prompt\. Removed 1 saved pair\. Safety backup saved: /);
    await expect(card(page,dual.id)).toHaveCount(0);
    expect(writes).toHaveLength(1);
    expect(writes[0].delete_paired_ids).toEqual([dual.id]);
    expect(Object.keys(writes[0].paired_digests)).toEqual([String(dual.id)]);
    const after=await items(request);
    expect(byText(after,'Dual role portrait')).toBeUndefined();
    // Its negative (Cat portrait) is kept, with its own variants.
    expect(byText(after,'Cat portrait')?.id).toBe(cat.id);
    expect((await items(request,'archived')).map(item=>item.id)).toEqual([]);
});

test('a prompt used as another prompt\'s negative is archived even when Delete permanently is chosen (#482)',async({page,request})=>{
    const before=await items(request);const dual=byText(before,'Dual role portrait'),cat=byText(before,'Cat portrait');
    await page.goto(route);await expect(cards(page)).toHaveCount(before.length);
    await box(page,cat.id).check();await box(page,dual.id).check();await openReview(page);
    await choice(page).getByRole('radio',{name:'Delete permanently, with their saved pairs'}).check();
    await expect(row(page,'Archive')).toHaveText('1 prompt used as another prompt\'s negative: hidden, restorable');
    await expect(review(page).locator('p.small',{hasText:'archived instead of deleted'})).toHaveCount(1);
    await review(page).getByRole('button',{name:'Delete 1 and archive 1 prompt',exact:true}).click();
    await expect(toast(page)).toHaveText(/^Deleted 1 prompt\. Removed 1 saved pair\. Archived 1 prompt\./);
    expect((await items(request,'archived')).map(item=>item.id)).toEqual([cat.id]);
});

test('the choice resets to Archive on every Delete…, and a selection with no paired prompt has none (#482)',async({page,request})=>{
    const before=await items(request);const dual=byText(before,'Dual role portrait'),dog=byText(before,'Dog landscape');
    await page.goto(route);await box(page,dual.id).check();await openReview(page);
    await choice(page).getByRole('radio',{name:'Delete permanently, with their saved pairs'}).check();
    await review(page).getByRole('button',{name:'Cancel',exact:true}).click();
    await openReview(page);
    await expect(choice(page).getByRole('radio',{name:'Archive (hidden, restorable)'})).toBeChecked();
    await review(page).getByRole('button',{name:'Cancel',exact:true}).click();
    await bar(page).getByRole('button',{name:'Clear',exact:true}).click();
    await box(page,dog.id).check();await openReview(page);
    await expect(choice(page)).toHaveCount(0);
});

// #492: Delete… on the archived shelf deletes archived prompts permanently, with their own saved pairs; no choice.
async function archiveByApi(request,ids) {
    const epoch=(await(await request.get('/prompt_manager/library/options')).json()).library_epoch;
    const preview=await(await request.post(PREVIEW,{data:{prompt_ids:ids,expected_epoch:epoch}})).json();
    expect(preview.archive).toEqual(ids);
    await expect(await request.post(DELETE,{data:{archive_ids:ids,delete_ids:[],expected_epoch:epoch,backup:'on'}})).toBeOK();
    return epoch;
}
test('Delete… on the archived shelf deletes an archived prompt and its saved pair permanently (#492)',async({page,request})=>{
    const dual=byText(await items(request),'Dual role portrait');
    const epoch=await archiveByApi(request,[dual.id]);
    const previews=recordPosts(page,PREVIEW),writes=recordPosts(page,DELETE);
    await page.goto(route);await showArchived(page).click();await expect(card(page,dual.id)).toHaveCount(1);
    await box(page,dual.id).check();
    await openReview(page);
    await expect(review(page)).toHaveAccessibleName('Delete archived prompts');
    await expect(choice(page)).toHaveCount(0);
    await expect(row(page,'Delete permanently')).toHaveText('1 prompt in a saved pair');
    await expect(row(page,'Saved pairs removed')).toHaveText(/^1 saved pair/);
    await expect(row(page,'Backup')).toHaveText('Create and verify before deleting');
    expect(previews).toEqual([{prompt_ids:[dual.id],expected_epoch:epoch,shelf:'archived'}]);
    // Cancel writes nothing and returns focus to Delete….
    await review(page).getByRole('button',{name:'Cancel',exact:true}).click();
    await expect(review(page)).toHaveCount(0);expect(writes).toEqual([]);
    await expect(bar(page).getByRole('button',{name:'Delete…',exact:true})).toBeFocused();
    await openReview(page);
    await review(page).getByRole('button',{name:'Delete 1 prompt',exact:true}).click();
    await expect(toast(page)).toHaveText(/^Deleted 1 prompt\. Removed 1 saved pair\. Safety backup saved: /);
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({archive_ids:[dual.id],delete_ids:[],delete_paired_ids:[dual.id],shelf:'archived'});
    await expect(page.locator('.library-status')).toHaveText('No archived prompts.');
    await backToLibrary(page).click();
    await expect(showArchived(page)).toHaveText('Show archived prompts (0)');
    expect(byText(await items(request),'Dual role portrait')).toBeUndefined();
    expect(await items(request,'archived')).toEqual([]);
});

test('an archived prompt restored since the shelf was read is kept, in a close-only review (#492)',async({page,request})=>{
    const dual=byText(await items(request),'Dual role portrait');
    await archiveByApi(request,[dual.id]);
    const epoch=(await(await request.get('/prompt_manager/library/options')).json()).library_epoch;
    await page.goto(route);await showArchived(page).click();await expect(card(page,dual.id)).toHaveCount(1);
    await box(page,dual.id).check();
    // Restored elsewhere after the shelf was read: the review counts it and deletes nothing.
    await expect(await request.post('/prompt_manager/library/restore',{data:{prompt_ids:[dual.id],expected_epoch:epoch}})).toBeOK();
    await openReview(page);
    await expect(review(page).locator('p.small').last()).toHaveText('None of the selected prompts can be deleted.');
    await expect(row(page,'No longer archived \\(kept\\)')).toHaveText('1');
    await expect(review(page).getByRole('button',{name:/^Delete \d/})).toHaveCount(0);
    expect(byText(await items(request),'Dual role portrait')?.id).toBe(dual.id);
});

// #481 (D161): a prompt's own page deletes it from Edit metadata (the concept's recipe editor, app.js 976), through the
// same review: "Delete prompt…" beside "Archive variant…" (the variant's archive, renamed so the two cannot be confused).
const editMetadata=page=>page.locator('#workspace-main .heading-actions').getByRole('button',{name:'Edit metadata',exact:true});
const editor=page=>page.locator('dialog.library-editor[open]');
async function deleteFromPage(page,id){
    await page.goto(`/prompt_manager/workspace#/library/${id}`);
    await editMetadata(page).click();
    await expect(editor(page).locator('.dialog-actions button')).toHaveText(['Create text revision','Archive variant…','Delete prompt…','Cancel','Save']);
    await expect(editor(page).getByRole('button',{name:'Delete prompt…',exact:true})).toHaveClass(/\bdanger\b/);
    await editor(page).getByRole('button',{name:'Delete prompt…',exact:true}).click();
    await expect(editor(page)).toHaveCount(0);await expect(review(page)).toBeVisible();
}
test('Delete prompt… on a prompt\'s page archives it by default, and deletes it permanently when chosen (#481)',async({page,request})=>{
    const dual=byText(await items(request),'Dual role portrait');
    const previews=recordPosts(page,PREVIEW);
    await deleteFromPage(page,dual.id);
    expect(previews.map(body=>body.prompt_ids)).toEqual([[dual.id]]);
    await expect(choice(page).getByRole('radio',{name:'Archive (hidden, restorable)'})).toBeChecked();
    await review(page).getByRole('button',{name:'Archive 1 prompt',exact:true}).click();
    await expect(toast(page)).toHaveText('Archived 1 prompt.');
    // The page stays and shows its Archived notice.
    await expect(page.locator('.library-archived-notice h2')).toHaveText('Archived');
    await expect(page.locator('#workspace-main h1')).toBeFocused();
    await page.locator('.library-archived-notice').getByRole('button',{name:'Restore this prompt to the Prompt library'}).click();
    await expect(toast(page)).toHaveText('Restored 1 prompt to the Prompt library.');
    // Delete permanently: back to the Prompt library, the prompt gone.
    await editMetadata(page).click();await editor(page).getByRole('button',{name:'Delete prompt…',exact:true}).click();
    await choice(page).getByRole('radio',{name:'Delete permanently, with their saved pairs'}).check();
    await review(page).getByRole('button',{name:'Delete 1 prompt',exact:true}).click();
    await expect(toast(page)).toHaveText(/^Deleted 1 prompt\. Removed 1 saved pair\. Safety backup saved: /);
    await expect(page).toHaveURL(/#\/library$/);
    await expect(card(page,dual.id)).toHaveCount(0);
    expect(byText(await items(request),'Dual role portrait')).toBeUndefined();
});

test('Cancel on the page\'s review changes nothing and returns focus to Edit metadata (#481)',async({page,request})=>{
    const dual=byText(await items(request),'Dual role portrait');
    const writes=recordPosts(page,DELETE);
    await deleteFromPage(page,dual.id);
    await review(page).getByRole('button',{name:'Cancel',exact:true}).click();
    await expect(review(page)).toHaveCount(0);await expect(editMetadata(page)).toBeFocused();
    expect(writes).toEqual([]);
    expect(byText(await items(request),'Dual role portrait')?.id).toBe(dual.id);
});

test('the page\'s delete holds one review at a time, Escape returns focus, and an archived page\'s review is close-only (#481)',async({page,request})=>{
    const dual=byText(await items(request),'Dual role portrait');
    const previews=recordPosts(page,PREVIEW);
    let release;const held=new Promise(resolve=>{release=resolve;});
    await page.route(`**${PREVIEW}`,async item=>{await held;await item.fallback();});
    await page.goto(`/prompt_manager/workspace#/library/${dual.id}`);
    await editMetadata(page).click();await editor(page).getByRole('button',{name:'Delete prompt…',exact:true}).click();
    // While the preview is held, Edit metadata is held too: no second review and no save can race the delete.
    await expect(editMetadata(page)).toHaveAttribute('aria-disabled','true');
    await editMetadata(page).click({force:true});await expect(editor(page)).toHaveCount(0);
    release();
    await expect(review(page)).toBeVisible();
    expect(previews).toHaveLength(1);
    expect(previews[0].expected_epoch).toMatch(/\S/);
    await page.keyboard.press('Escape');
    await expect(review(page)).toHaveCount(0);await expect(editMetadata(page)).toBeFocused();
    await expect(editMetadata(page)).not.toHaveAttribute('aria-disabled');
    await page.unroute(`**${PREVIEW}`);
    // Archived: its review has nothing to change and only closes.
    await editMetadata(page).click();await editor(page).getByRole('button',{name:'Delete prompt…',exact:true}).click();
    await review(page).getByRole('button',{name:'Archive 1 prompt',exact:true}).click();
    await expect(page.locator('.library-archived-notice')).toBeVisible();
    await editMetadata(page).click();await editor(page).getByRole('button',{name:'Delete prompt…',exact:true}).click();
    await expect(review(page).locator('p.small').last()).toHaveText('None of the selected prompts can be deleted or archived.');
    await expect(review(page).getByRole('button',{name:/^(Delete|Archive)/})).toHaveCount(0);
});

test('a failed review on the prompt\'s page says so, changes nothing and releases Edit metadata (#481)',async({page,request})=>{
    const dual=byText(await items(request),'Dual role portrait');
    const writes=recordPosts(page,DELETE);
    await page.route(`**${PREVIEW}`,item=>item.fulfill({status:500,contentType:'application/json',body:JSON.stringify({success:false,code:'review-failed',error:'x'})}),{times:1});
    await page.goto(`/prompt_manager/workspace#/library/${dual.id}`);
    await editMetadata(page).click();await editor(page).getByRole('button',{name:'Delete prompt…',exact:true}).click();
    await expect(toast(page)).toHaveText('The selected prompts could not be reviewed. Try again.');
    await expect(review(page)).toHaveCount(0);await expect(editMetadata(page)).not.toHaveAttribute('aria-disabled');
    expect(writes).toEqual([]);
    await editMetadata(page).click();await expect(editor(page)).toBeVisible();
});

test('a delete answered after the page was left invalidates the shelf without a toast or a jump (#481)',async({page,request})=>{
    const dual=byText(await items(request),'Dual role portrait');
    let release;const held=new Promise(resolve=>{release=resolve;});
    let answered;const done=new Promise(resolve=>{answered=resolve;});
    await page.route(`**${DELETE}`,async item=>{await held;const response=await item.fetch();await item.fulfill({response});answered();});
    await deleteFromPage(page,dual.id);
    await review(page).getByRole('button',{name:'Archive 1 prompt',exact:true}).click();
    await page.evaluate(()=>{window.location.hash='#/overview';});
    await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    // Every toast text shown from here on is recorded, so one shown and hidden again still counts.
    await page.evaluate(()=>{window.seenToasts=[];new MutationObserver(()=>{for(const node of document.querySelectorAll('.toast'))
        if(node.textContent.trim())window.seenToasts.push(node.textContent.trim());}).observe(document.body,{subtree:true,childList:true,characterData:true});});
    release();await done;
    // The answer lands on Overview: no navigation; the shelf reads again when opened; and no toast was ever shown.
    await page.evaluate(()=>{window.location.hash='#/library';});
    await expect(card(page,dual.id)).toHaveCount(0);
    expect((await items(request,'archived')).map(item=>item.id)).toEqual([dual.id]);
    expect(await page.evaluate(()=>window.seenToasts)).toEqual([]);
});

test('a restore since the prompt\'s page loaded is refused and the page reads the Library again (#481)',async({page,request})=>{
    const dual=byText(await items(request),'Dual role portrait');
    await armUnseenRestores(page);
    await page.goto(`/prompt_manager/workspace#/library/${dual.id}`);
    await editMetadata(page).click();await editor(page).getByRole('button',{name:'Delete prompt…',exact:true}).click();
    const backup=await request.get('/prompt_manager/backup');await expect(backup).toBeOK();const buffer=await backup.body();
    const restore=await unseenRestore(page,()=>request.post('/prompt_manager/restore',{multipart:{database_file:{name:'library.db',mimeType:'application/octet-stream',buffer}}}));
    await expect(restore).toBeOK();
    await review(page).getByRole('button',{name:'Archive 1 prompt',exact:true}).click();
    await expect(toast(page)).toHaveText('The library was restored or moved since this page loaded. Nothing was deleted or archived.');
    await expect(page.locator('#workspace-main h1')).toBeFocused();
    expect((await items(request,'archived')).map(item=>item.id)).toEqual([]);
});

test('an archived prompt\'s page says so and restores it from its notice',async({page,request})=>{
    const cat=byText(await items(request),'Cat portrait');
    const epoch=(await(await request.get('/prompt_manager/library/options')).json()).library_epoch;
    await expect(await request.post(DELETE,{data:{archive_ids:[cat.id],delete_ids:[],expected_epoch:epoch,backup:'off'}})).toBeOK();
    await page.goto(`/prompt_manager/workspace#/library/${cat.id}`);
    const notice=page.locator('.library-archived-notice');
    await expect(notice.locator('h2')).toHaveText('Archived');
    // The notice says why (the saved pair) and counts the prompt's negative variants (live note on #470).
    expect(cat.variant_count).toBeGreaterThan(1);
    await expect(notice.locator('p').first()).toHaveText(`This prompt is archived because it is in a saved pair. It and its ${cat.variant_count} negative variants are hidden from the Prompt library; nothing was removed.`);
    await notice.getByRole('button',{name:'Restore this prompt to the Prompt library'}).click();
    await expect(toast(page)).toHaveText('Restored 1 prompt to the Prompt library.');
    await expect(notice).toHaveCount(0);
    expect(byText(await items(request),'Cat portrait')?.id).toBe(cat.id);
});

// #443: a starved renderer (headless WebKit under CPU load, a background tab) can hold animation frames for
// seconds; a loaded page of prompts is shown without waiting for one, on first load and after a delete.
async function holdAnimationFrames(page) {
    await page.addInitScript(()=>{
        const request=window.requestAnimationFrame.bind(window),cancel=window.cancelAnimationFrame.bind(window);
        const held=new Map(),real=new Map();let holding=true,next=0;
        window.requestAnimationFrame=callback=>{const id=--next;if(holding)held.set(id,callback);else real.set(id,request(callback));return id;};
        window.cancelAnimationFrame=id=>{held.delete(id);if(real.has(id))cancel(real.get(id));real.delete(id);};
        window.__releaseAnimationFrames=()=>{holding=false;for(const [id,callback] of held)real.set(id,request(callback));held.clear();};
    });
}
test('a held animation frame does not hide the loaded prompts, on first load or after a delete',async({page,request})=>{
    const before=await items(request);const dog=byText(before,'Dog landscape');
    await holdAnimationFrames(page);
    await page.goto(route);await expect(cards(page)).toHaveCount(before.length);
    await box(page,dog.id).check();await openReview(page);
    await review(page).getByRole('button',{name:'Delete 1 prompt',exact:true}).click();
    await expect(toast(page)).toHaveText(/^Deleted 1 prompt\./);
    await expect(cards(page)).toHaveCount(before.length-1);await expect(card(page,dog.id)).toHaveCount(0);
    await page.evaluate(()=>window.__releaseAnimationFrames());
    await expect(cards(page)).toHaveCount(before.length-1);
});

test('a double press sends one preview, and the bar holds while it is reviewed',async({page,request})=>{
    const dog=byText(await items(request),'Dog landscape');
    const previews=recordPosts(page,PREVIEW);
    let release;const held=new Promise(resolve=>{release=resolve;});
    await page.route(`**${PREVIEW}`,async item=>{await held;await item.fallback();});
    await page.goto(route);await box(page,dog.id).check();
    const remove=bar(page).getByRole('button',{name:/^(Delete…|Reviewing…)$/});
    await remove.click();
    await expect(remove).toHaveText('Reviewing…');await expect(remove).toHaveAttribute('aria-disabled','true');
    await expect(bar(page).getByRole('button',{name:'Clear',exact:true})).toHaveAttribute('aria-disabled','true');
    await remove.click({force:true});
    release();
    await expect(review(page)).toHaveAccessibleName('Delete selected prompts');
    expect(previews).toHaveLength(1);
    await review(page).getByRole('button',{name:'Cancel',exact:true}).click();
    await expect(bar(page).getByRole('button',{name:'Delete…',exact:true})).not.toHaveAttribute('aria-disabled','true');
});

test('a restore since the page loaded, a failed backup, a busy job and an unconfirmed answer each say what happened',async({page,request})=>{
    const before=await items(request);const dog=byText(before,'Dog landscape');
    await armUnseenRestores(page);
    await page.goto(route);await expect(cards(page)).toHaveCount(before.length);
    // Failed backup: nothing changed, the selection stays.
    await page.route(`**${DELETE}`,item=>item.fulfill({status:500,contentType:'application/json',
        body:JSON.stringify({success:false,code:'backup-failed',error:'The safety backup could not be taken and verified; nothing was changed.'})}));
    await box(page,dog.id).check();await openReview(page);
    await review(page).getByRole('button',{name:'Delete 1 prompt',exact:true}).click();
    await expect(toast(page)).toHaveText('The safety backup failed. The safety backup could not be taken and verified; nothing was changed.');
    await expect(box(page,dog.id)).toBeChecked();
    // Busy: names the job, nothing changed.
    await page.unroute(`**${DELETE}`);
    await page.route(`**${DELETE}`,item=>item.fulfill({status:409,contentType:'application/json',
        body:JSON.stringify({success:false,code:'busy',error:'busy',running:{id:'abc',title:'Scan'}})}));
    await openReview(page);await review(page).getByRole('button',{name:'Delete 1 prompt',exact:true}).click();
    await expect(toast(page)).toHaveText('Scan is running. Nothing was changed; try again when it finishes.');
    // Unconfirmed: the shelf reloads.
    await page.unroute(`**${DELETE}`);
    await page.route(`**${DELETE}`,item=>item.abort());
    await openReview(page);await review(page).getByRole('button',{name:'Delete 1 prompt',exact:true}).click();
    await expect(toast(page)).toHaveText('The server did not confirm the change. The Library was reloaded: check the shelf and Show archived prompts.');
    await expect(page.locator('#library-bulkbar')).toBeEmpty();
    await page.unroute(`**${DELETE}`);
    // A restore the page has not seen: the review opened before it is refused on the server.
    await box(page,dog.id).check();await openReview(page);
    const backup=await request.get('/prompt_manager/backup');await expect(backup).toBeOK();const buffer=await backup.body();
    const restore=await unseenRestore(page,()=>request.post('/prompt_manager/restore',{multipart:{database_file:{name:'library.db',mimeType:'application/octet-stream',buffer}}}));
    await expect(restore).toBeOK();
    await review(page).getByRole('button',{name:'Delete 1 prompt',exact:true}).click();
    await expect(toast(page)).toHaveText('The library was restored or moved since this page loaded. Nothing was deleted or archived.');
    expect(byText(await items(request),'Dog landscape')).toBeDefined();
});

test('a failed review keeps the selection, and a selection nothing can change opens the close-only review',async({page,request})=>{
    const before=await items(request);const guarded=byText(before,'Library extra 0000');
    await expect(await request.post('/prompt_manager/bulk/tags',{data:{prompt_ids:[guarded.id],tags:['__protected__']}})).toBeOK();
    await page.goto(route);await expect(cards(page)).toHaveCount(before.length);
    await box(page,guarded.id).check();
    await page.route(`**${PREVIEW}`,item=>item.fulfill({status:500,contentType:'application/json',body:JSON.stringify({success:false,code:'review-failed',error:'x'})}),{times:1});
    await bar(page).getByRole('button',{name:'Delete…',exact:true}).click();
    await expect(toast(page)).toHaveText('The selected prompts could not be reviewed. Try again.');
    await expect(box(page,guarded.id)).toBeChecked();
    await expect(bar(page).getByRole('button',{name:'Delete…',exact:true})).not.toHaveAttribute('aria-disabled','true');
    await openReview(page);
    await expect(review(page)).toHaveAccessibleName('Delete selected prompts');
    await expect(review(page).locator('p.small').last()).toHaveText('None of the selected prompts can be deleted or archived.');
    await expect(review(page).locator('.dialog-actions > button')).toHaveText(['Close']);
    await review(page).getByRole('button',{name:'Close',exact:true}).click();
    await expect(bar(page).getByRole('button',{name:'Delete…',exact:true})).not.toHaveAttribute('aria-disabled','true');
    expect(byText(await items(request),'Library extra 0000')).toBeDefined();
});
