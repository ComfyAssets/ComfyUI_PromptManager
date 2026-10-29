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
    await expect(grid.locator('dd')).toHaveText(['1 prompt not in a saved pair','1 prompt with negative variants: hidden, restorable',
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
    await review(page).getByRole('button',{name:'Archive 2 prompts',exact:true}).click();
    await expect(toast(page)).toHaveText('Archived 2 prompts.');
    await expect(cards(page)).toHaveCount(before.length-2);
    await expect(showArchived(page)).toHaveAttribute('aria-pressed','false');
    await showArchived(page).click();
    await expect(showArchived(page)).toHaveAttribute('aria-pressed','true');
    await expect(cards(page)).toHaveCount(2);
    await expect(page.locator('.section-caption > span').first()).toHaveText(/^2 archived prompts · \d+ negative variants? hidden with them$/);
    await expect(card(page,cat.id).locator('.card-title .chip',{hasText:'Archived'})).toHaveCount(1);
    await box(page,cat.id).check();
    await expect(bar(page).getByRole('button')).toHaveText(['Select all 2 loaded','Restore','Clear']);
    await bar(page).getByRole('button',{name:'Restore',exact:true}).click();
    await expect(toast(page)).toHaveText('Restored 1 prompt to the Prompt library.');
    await expect(cards(page)).toHaveCount(1);
    await showArchived(page).click();
    await expect(cards(page)).toHaveCount(before.length-1);
    await expect(card(page,cat.id)).toHaveCount(1);
    expect((await items(request,'archived')).map(item=>item.id)).toEqual([dual.id]);
    // An empty archived view says so.
    await showArchived(page).click();await box(page,dual.id).check();
    await bar(page).getByRole('button',{name:'Restore',exact:true}).click();
    await expect(page.locator('.library-status')).toHaveText('No archived prompts.');
});

test('an archived prompt\'s page says so and restores it from its notice',async({page,request})=>{
    const cat=byText(await items(request),'Cat portrait');
    const epoch=(await(await request.get('/prompt_manager/library/options')).json()).library_epoch;
    await expect(await request.post(DELETE,{data:{archive_ids:[cat.id],delete_ids:[],expected_epoch:epoch,backup:'off'}})).toBeOK();
    await page.goto(`/prompt_manager/workspace#/library/${cat.id}`);
    const notice=page.locator('.library-archived-notice');
    await expect(notice.locator('h2')).toHaveText('Archived');
    await expect(notice.locator('p').first()).toHaveText('This prompt and its negative variants are hidden from the Prompt library; nothing was removed.');
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
