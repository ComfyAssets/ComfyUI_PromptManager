import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import {test,expect} from './fixtures.mjs';
import {armUnseenRestores,unseenRestore} from './unseen-restore.mjs';
import {saveImageTags} from './concept-inventory.mjs';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

test.use({locale:'en-US',timezoneId:'UTC',reducedMotion:'reduce'});
const recorded=page=>page.getByRole('region',{name:'Recorded executions',exact:true});
const dialog=page=>page.getByRole('dialog',{name:'Recorded run',exact:true});
const saved=page=>page.getByRole('region',{name:'Saved prompt texts',exact:true});
const gallery=page=>page.getByRole('region',{name:'Shared gallery',exact:true});
const open=async(page,id)=>{await page.goto(`/prompt_manager/workspace#/library/${id}`);await expect(recorded(page)).toBeVisible();};
// A history opens on its first negative variant, as in the concept; these contracts start from the lane that links the
// fixture image (#249 D26: All images for this positive is a dialog now, not a gallery filter).
const showLinkedImage=async page=>{const dot=page.getByRole('button',{name:'Select negative Saved exact negative',exact:true});await dot.click();await expect(dot).toHaveAttribute('aria-pressed','true');};
const marker=(page,id,anchor='10')=>recorded(page).locator(`button[data-run-id="${id}"][data-anchor-id="${anchor}"]`);
const exact=async locator=>locator.evaluate(node=>node.textContent);

test.beforeEach(async({page,request,browserName})=>{
    await startCoverage(page,browserName);await armUnseenRestores(page);page.historyErrors=[];page.on('pageerror',error=>page.historyErrors.push(error.message));
    const reset=await request.post('/__test__/reset',{data:{chronological_history:true}});await expect(reset).toBeOK();page.fixture=await reset.json();
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.historyErrors).toEqual([]);});

const command=async(request,data)=>{const response=await request.post('/__test__/history/commands',{data});await expect(response).toBeOK();return response.json();};
const restoreSnapshot=async(page,request,bytes)=>{const response=await unseenRestore(page,()=>request.post('/prompt_manager/restore',{multipart:{database_file:{name:'history.db',mimeType:'application/octet-stream',buffer:bytes}}}));await expect(response).toBeOK();expect((await response.json()).success).toBe(true);};
const historyRead=async(request,id)=>{const response=await request.get(`/prompt_manager/library/${id}/history`);await expect(response).toBeOK();return response.json();};
const frozenContexts=contexts=>contexts.map(({navigation,positive_prompt_id,negative_prompt_id,pair_id,...frozen})=>frozen);
const immutableReceipts=items=>items.map(({current_candidate,...receipt})=>receipt);

async function reviewCapture(page,testInfo,name,target) {
    await target.scrollIntoViewIfNeeded();await expect(target).toBeVisible();await page.evaluate(()=>document.fonts.ready);
    const path=testInfo.outputPath(`${name}.png`);await target.screenshot({path,animations:'disabled'});await testInfo.attach(name,{path,contentType:'image/png'});
    const geometry=await target.evaluate(node=>{
        const box=node.getBoundingClientRect();
        return {bounds:{x:box.x,y:box.y,width:box.width,height:box.height},scrollLeft:node.scrollLeft,scrollTop:node.scrollTop,
            buttons:[...node.querySelectorAll('button')].filter(button=>button.checkVisibility()).map(button=>{const box=button.getBoundingClientRect();return {label:button.getAttribute('aria-label')||button.textContent,disabled:button.disabled,pressed:button.getAttribute('aria-pressed'),width:box.width,height:box.height};})};
    });
    const geometryPath=testInfo.outputPath(`${name}-geometry.json`);await writeFile(geometryPath,JSON.stringify(geometry,null,2));
    await testInfo.attach(`${name}-geometry`,{path:geometryPath,contentType:'application/json'});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
}

test('owned history commands validate fixed boundaries and expire after ordinary reset',async({request})=>{
    for(const data of [{}, {action:'arm',boundary:'arbitrary'}, {action:'replace-file',path:'/tmp/other'}, {action:'release',extra:true}, {action:'restore'}]) expect((await request.post('/__test__/history/commands',{data})).status()).toBe(400);
    await command(request,{action:'arm',boundary:'window'});await command(request,{action:'release'});
    await expect(await request.post('/__test__/reset')).toBeOK();
    expect((await request.post('/__test__/history/commands',{data:{action:'replace-file'}})).status()).toBe(409);
    expect((await request.get('/__test__/history/snapshot')).status()).toBe(409);
});

for(const boundary of ['window','detail','candidate','inspection','media']) test(`real restore rejects in-flight ${boundary} publication and all old epoch URLs`,async({request,page})=>{
    const f=page.fixture,window=await historyRead(request,f.detail_id),epoch=window.history_epoch;
    const base=`/prompt_manager/history/runs/${f.runs.shared}`;
    const detail=await(await request.get(`${base}?epoch=${epoch}`)).json();
    const firstWindow=await(await request.get(`/prompt_manager/library/${f.detail_id}/history?limit=1`)).json();expect(firstWindow.next_cursor).toBeTruthy();
    const outputs=await(await request.get(`${base}/outputs?epoch=${epoch}&limit=1`)).json();
    const allOutputs=await(await request.get(`${base}/outputs?epoch=${epoch}`)).json();
    const media=outputs.items[0].current_candidate.media;
    const snapshot=await request.get('/__test__/history/snapshot');await expect(snapshot).toBeOK();
    const bytes=await snapshot.body();
    const target={window:`/prompt_manager/library/${f.detail_id}/history?limit=1`,detail:`${base}?epoch=${epoch}`,candidate:`${base}/outputs?epoch=${epoch}`,inspection:media.inspection_url,media:media.url}[boundary];
    await command(request,{action:'arm',boundary});const pending=request.get(target);
    try {
        await expect.poll(async()=> (await(await request.get('/__test__/history/checkpoint')).json()).entered).toBe(true);
        await restoreSnapshot(page,request,bytes);
    } finally {await command(request,{action:'release'});}
    const retired=await pending;expect(retired.status()).toBe(409);expect((await retired.json()).code).toBe('history-changed');
    for(const url of [`/prompt_manager/library/${f.detail_id}/history?limit=1&cursor=${encodeURIComponent(firstWindow.next_cursor)}`,`${base}?epoch=${epoch}`,`${base}/outputs?epoch=${epoch}`,`${base}/outputs?epoch=${epoch}&cursor=${encodeURIComponent(outputs.next_cursor)}`,media.url,media.thumbnail_url,media.inspection_url]) {
        const response=await request.get(url);expect(response.status()).toBe(409);expect((await response.json()).code).toBe('history-changed');
    }
    const fresh=await historyRead(request,f.detail_id);expect(fresh.history_epoch).not.toBe(epoch);
    const after=await(await request.get(`${base}?epoch=${fresh.history_epoch}`)).json();expect(frozenContexts(after.contexts)).toEqual(frozenContexts(detail.contexts));
    for(const context of after.contexts) {expect(context.positive_prompt_id).toBeNull();expect(context.navigation).toEqual({positive_available:false,negative_available:false,pair_available:false});}
    const freshOutputs=await(await request.get(`${base}/outputs?epoch=${fresh.history_epoch}`)).json();
    expect(immutableReceipts(freshOutputs.items)).toEqual(immutableReceipts(allOutputs.items));
    expect(freshOutputs.items).toHaveLength(5);await expect(await request.get(`/prompt_manager/library/${f.detail_id}`)).toBeOK();
});

test('same-path replacement is current content and a during-inspection change is rejected',async({page,request})=>{
    const f=page.fixture,window=await historyRead(request,f.detail_id),base=`/prompt_manager/history/runs/${f.runs.shared}`;
    const before=await(await request.get(`${base}/outputs?epoch=${window.history_epoch}`)).json(),media=before.items[0].current_candidate.media;
    const original=await request.get(media.url);expect(original.headers()['cache-control']).toBe('no-store');const bytes=await original.body();
    await command(request,{action:'arm',boundary:'inspection'});const pending=request.get(media.inspection_url);
    try {
        await expect.poll(async()=> (await(await request.get('/__test__/history/checkpoint')).json()).entered).toBe(true);
        await command(request,{action:'replace-file'});
    } finally {await command(request,{action:'release'});}
    const changed=await pending;expect(changed.status()).toBe(409);expect((await changed.json()).code).toBe('candidate-stale');
    const after=await(await request.get(`${base}/outputs?epoch=${window.history_epoch}`)).json();expect(immutableReceipts(after.items)).toEqual(immutableReceipts(before.items));
    const current=await request.get(after.items[0].current_candidate.media.url);await expect(current).toBeOK();expect(await current.body()).not.toEqual(bytes);
    for(const field of ['url','thumbnail_url','inspection_url'])expect((await request.get(after.items[0].current_candidate.media[field])).headers()['cache-control']).toBe('no-store');
    expect(after.items[0]).toMatchObject({file_identity:'unverified',newly_generated:'unknown',context_membership:'unknown'});
});

test('root reconfiguration rejects in-flight candidates and every old scope URL without changing receipts',async({page,request})=>{
    const f=page.fixture,window=await historyRead(request,f.detail_id),base=`/prompt_manager/history/runs/${f.runs.shared}`;
    await open(page,f.detail_id);await showLinkedImage(page);await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(1);
    const before=await(await request.get(`${base}/outputs?epoch=${window.history_epoch}`)).json(),media=before.items[0].current_candidate.media;
    await command(request,{action:'arm',boundary:'candidate'});const pending=request.get(`${base}/outputs?epoch=${window.history_epoch}`);
    try {
        await expect.poll(async()=> (await(await request.get('/__test__/history/checkpoint')).json()).entered).toBe(true);
        await command(request,{action:'reconfigure-root'});
    } finally {await command(request,{action:'release'});}
    expect((await pending).status()).toBe(409);
    for(const field of ['url','thumbnail_url','inspection_url']) {const response=await request.get(media[field]);expect(response.status()).toBe(409);expect((await response.json()).code).toBe('candidate-stale');}
    const after=await(await request.get(`${base}/outputs?epoch=${window.history_epoch}`)).json();expect(immutableReceipts(after.items)).toEqual(immutableReceipts(before.items));
    expect(after.items[0].current_candidate).toMatchObject({status:'unavailable',reason:'namespace_mismatch'});
    await marker(page,f.runs.shared).click();await expect(dialog(page)).toContainText('namespace_mismatch');
    await page.keyboard.press('Escape');await expect(saved(page)).toBeVisible();await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(1);
});

test('archive delete merge and prune preserve frozen evidence without repairing original saved hints',async({page,request})=>{
    const f=page.fixture,window=await historyRead(request,f.detail_id),base=`/prompt_manager/history/runs/${f.runs.shared}?epoch=${window.history_epoch}`;
    const before=await(await request.get(base)).json(),outputs=await(await request.get(`/prompt_manager/history/runs/${f.runs.shared}/outputs?epoch=${window.history_epoch}`)).json();
    const details=await(await request.get(`/prompt_manager/library/${f.detail_id}`)).json();
    await expect(await request.patch(`/prompt_manager/pairs/${f.saved_pair}`,{data:{archived:true}})).toBeOK();
    expect(frozenContexts((await(await request.get(base)).json()).contexts)).toEqual(frozenContexts(before.contexts));
    await expect(await request.delete(`/prompt_manager/images/${f.detail_image_id}`)).toBeOK();
    for(const pair of details.variants)await expect(await request.delete(`/prompt_manager/pairs/${pair.id}`)).toBeOK();
    const text=details.positive.text;
    expect(f.merge_duplicate_id).not.toBe(f.detail_id);
    const merge=await request.post('/prompt_manager/cleanup');await expect(merge).toBeOK();expect((await merge.json()).duplicates_removed).toBeGreaterThan(0);
    await expect(await request.delete(`/prompt_manager/delete/${f.detail_id}`)).toBeOK();
    await expect(await request.post('/prompt_manager/pairs',{data:{positive_text:text,negative_text:'replacement saved negative'}})).toBeOK();
    const prune=await request.post('/prompt_manager/maintenance',{data:{operations:['prune_orphaned_prompts']}});await expect(prune).toBeOK();expect((await prune.json()).all_successful).toBe(true);
    const after=await(await request.get(base)).json();expect(frozenContexts(after.contexts)).toEqual(frozenContexts(before.contexts));
    for(const context of after.contexts) {expect(context.navigation.positive_available).toBe(false);expect(context.positive_prompt_id).toBeNull();}
    const receipts=await(await request.get(`/prompt_manager/history/runs/${f.runs.shared}/outputs?epoch=${window.history_epoch}`)).json();expect(immutableReceipts(receipts.items)).toEqual(immutableReceipts(outputs.items));
});

test('actual restore with reused numeric owner rejects stale history and offers explicit saved recovery',async({page,request})=>{
    const f=page.fixture;await open(page,f.detail_id);const original=await exact(saved(page).getByRole('region',{name:'Positive prompt',exact:true}).locator('p.prompt-text'));
    const source=await request.get('/__test__/history/reused-snapshot');await expect(source).toBeOK();await restoreSnapshot(page,request,await source.body());
    await marker(page,f.runs.shared).click();await expect(dialog(page)).toContainText('Recorded evidence changed');await page.keyboard.press('Escape');
    await expect(page.locator('.library-parent-status')).toContainText('changed');expect(await exact(saved(page).getByRole('region',{name:'Positive prompt',exact:true}).locator('p.prompt-text'))).toBe(original);
    await expect(recorded(page).getByRole('button',{name:'Reload recorded history',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'Reload saved Library view',exact:true}).click();await expect(saved(page)).toContainText('Restored owner with reused numeric ID');
    expect((await request.get(`/prompt_manager/history/runs/${f.runs.shared}`)).status()).toBe(404);
});

for(const outcome of ['success','error'])test(`delayed actual old browser ${outcome} cannot repaint after restore and route return`,async({page,request})=>{
    const f=page.fixture;await open(page,f.detail_id);const snapshot=await request.get('/__test__/history/snapshot');const bytes=await snapshot.body();
    let entered,release;const started=new Promise(resolve=>{entered=resolve;}),held=new Promise(resolve=>{release=resolve;});
    await page.route(new RegExp(`/history/runs/${f.runs.shared}\\?`),async route=>{
        if(outcome==='error')await restoreSnapshot(page,request,bytes);
        const response=await route.fetch();expect(response.status()).toBe(outcome==='success'?200:409);entered();await held;
        await route.fulfill({response}).catch(()=>{});
    });
    try {
        await marker(page,f.runs.shared).click();await started;if(outcome==='success')await restoreSnapshot(page,request,bytes);
        await page.evaluate(()=>{location.hash='#/overview';});await expect(page.locator('#workspace-main h1')).toHaveText('Overview');release();
        await page.unroute(new RegExp(`/history/runs/${f.runs.shared}\\?`));await open(page,f.detail_id);
        await expect(recorded(page).locator('button[data-run-id]')).toHaveCount(0);
        await expect(recorded(page).getByRole('button',{name:'Reload recorded history',exact:true})).toBeVisible();
        await recorded(page).getByRole('button',{name:'Reload recorded history',exact:true}).click();
        await expect(recorded(page).locator('button[data-run-id]').first()).toBeEnabled();await expect(page.locator('dialog[open]')).toHaveCount(0);
        await marker(page,f.runs.empty).click();await expect(dialog(page)).toContainText(f.runs.empty);await page.keyboard.press('Escape');await expect(saved(page)).toBeVisible();
    } finally {release();}
});

test('strict reset seeds real A,A,B positions, two contexts and independently selected saved gallery',async({page,request})=>{
    for(const data of [{chronological_history:'true'},{chronological_history:true,native_library:true},{chronological_history:true,full_inspector:false},{chronological_history:true,extra_images:0},{chronological_history:true,history_runs:true},{chronological_history:true,unknown:1},{history_runs:1}])
        expect((await request.post('/__test__/reset',{data})).status()).toBe(400);
    const fixture=page.fixture;let galleryReads=0;page.on('request',request=>{if(new URL(request.url()).pathname===`/prompt_manager/library/${fixture.positive_id}/images`)galleryReads++;});
    await open(page,fixture.positive_id);
    const detail=await(await request.get(`/prompt_manager/library/${fixture.positive_id}`)).json();
    const selected=detail.variants.find(v=>v.negative_text==='A negative').title;
    await page.getByRole('button',{name:`Select negative ${selected}`,exact:true}).click();
    await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(1);
    const before=galleryReads,keys=await gallery(page).locator('article[data-image-key]').evaluateAll(nodes=>nodes.map(n=>n.dataset.imageKey));
    await expect(recorded(page)).toContainText('3 distinct recorded runs as of this read');
    await expect(recorded(page)).toContainText('same positive raw text');
    await expect(recorded(page).locator('[data-context-branch]')).toHaveCount(2);
    const buttons=recorded(page).getByRole('list',{name:'Recorded run positions',exact:true}).getByRole('button');
    await expect(buttons).toHaveCount(3);
    expect(await buttons.evaluateAll(nodes=>nodes.map(n=>n.dataset.runId))).toEqual(fixture.aab);
    const contexts=await buttons.evaluateAll(nodes=>nodes.map(n=>n.dataset.contextId));expect(contexts[0]).toBe(contexts[1]);expect(contexts[2]).not.toBe(contexts[0]);
    for(const [index,id] of fixture.aab.entries()) {
        await marker(page,id).focus();await page.keyboard.press(index===1?' ':'Enter');
        await expect(dialog(page)).toContainText(id);
        await dialog(page).getByRole('button',{name:'Effective',exact:true}).click();
        await dialog(page).getByRole('button',{name:'Close recorded run',exact:true}).click();
        await expect(marker(page,id)).toBeFocused();
    }
    await expect(saved(page).getByRole('region',{name:'Negative prompt',exact:true})).toContainText('A negative');
    await expect(page.getByRole('button',{name:`Select negative ${selected}`,exact:true})).toHaveAttribute('aria-pressed','true');
    expect(galleryReads).toBe(before);expect(await gallery(page).locator('article[data-image-key]').evaluateAll(nodes=>nodes.map(n=>n.dataset.imageKey))).toEqual(keys);
});

test('frozen exact roles distinguish unknown, explicit empty, whitespace, mode and equal-looking snapshots',async({page,request})=>{
    const f=page.fixture;await open(page,f.detail_id);
    const window=await(await request.get(`/prompt_manager/library/${f.detail_id}/history`)).json();
    const same=window.context_summaries.filter(c=>c.negative_raw.preview?.startsWith('same visible negative'));
    expect(same.length).toBe(2);expect(same[0].context_id).not.toBe(same[1].context_id);
    await expect(recorded(page).locator(`[data-context-branch="${same[0].context_id}"]`)).toHaveCount(1);
    const shared=window.items.find(run=>run.run_id===f.runs.shared);
    await marker(page,f.runs.shared,'z').click();await expect(dialog(page)).toBeVisible();
    const contexts=dialog(page).getByRole('group',{name:'Recorded anchors',exact:true});
    await expect(contexts.getByRole('button')).toHaveCount(3);await expect(contexts.locator('[aria-pressed="true"]')).toContainText('z');
    const detail=await(await request.get(`/prompt_manager/history/runs/${f.runs.shared}?epoch=${window.history_epoch}`)).json();
    const chosen=detail.contexts.find(c=>c.anchor_id==='z');
    const pos=dialog(page).getByRole('region',{name:'Positive prompt',exact:true}),neg=dialog(page).getByRole('region',{name:'Negative prompt',exact:true});
    expect(await exact(pos.locator('p.prompt-text'))).toBe(chosen.positive.raw);expect(await exact(neg.locator('p.prompt-text'))).toBe(chosen.negative.raw);
    expect(await pos.evaluate(node=>node.nextElementSibling?.getAttribute('aria-label'))).toBe('Negative prompt');
    await dialog(page).getByRole('button',{name:'Effective',exact:true}).click();expect(await exact(neg.locator('p.prompt-text'))).toBe(chosen.negative.effective);
    await dialog(page).getByRole('button',{name:'Close recorded run',exact:true}).click();
    await marker(page,f.runs.unknown).click();await dialog(page).getByRole('button',{name:'Raw',exact:true}).click();
    const unknown=dialog(page).getByRole('region',{name:'Negative prompt',exact:true});await expect(unknown.getByRole('button')).toHaveCount(0);await expect(dialog(page).getByRole('button',{name:'Send prompt pair',exact:true})).toHaveCount(0);
    await dialog(page).getByRole('button',{name:'Effective',exact:true}).click();expect(await exact(unknown.locator('p.prompt-text'))).toBe('  \n\t ');
    // D22: the frozen block has no source line; each field's source stays in "Frozen field provenance".
    await expect(unknown).not.toContainText('host_cache_output');await expect(dialog(page).locator('.recorded-provenance')).toContainText(/Effective · \w+ · host_cache_output/);
    await dialog(page).getByRole('button',{name:'Close recorded run',exact:true}).click();
    await marker(page,f.runs.empty).click();await expect(dialog(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('p.prompt-text')).toHaveText('No negative prompt (explicitly empty)');
    await expect(dialog(page).getByRole('button',{name:'Copy negative prompt',exact:true})).toBeVisible();await expect(dialog(page).getByRole('button',{name:'Send prompt pair',exact:true})).toBeVisible();
    await dialog(page).getByRole('button',{name:'Close recorded run',exact:true}).click();
    await marker(page,f.runs.whitespace).click();await dialog(page).getByRole('button',{name:'Raw',exact:true}).click();expect(await exact(dialog(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('p.prompt-text'))).toBe('  \n\t ');
    await expect(dialog(page)).toContainText('graph_literal');await expect(dialog(page)).toContainText('Role assignment: inferred');
    await dialog(page).getByText('Frozen field provenance',{exact:true}).click();await expect(dialog(page)).toContainText('Prepend');await expect(dialog(page)).toContainText('Append');await expect(dialog(page)).toContainText('graph_semantics');
    expect(shared.matching_anchors.map(a=>a.anchor_id)).toEqual(['10','2','z']);
});

test('every ordered receipt remains selected through distinct current locations, Viewer and nested Escape',async({page,request})=>{
    const f=page.fixture;let outputReads=0;page.on('request',request=>{if(new URL(request.url()).pathname.endsWith('/outputs'))outputReads++;});await open(page,f.detail_id);await marker(page,f.runs.shared).click();
    await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    const receipts=dialog(page).locator('[data-receipt]');
    expect(await receipts.evaluateAll(nodes=>nodes.map(node=>[node.dataset.outputNodeId,Number(node.dataset.imageOrdinal)]))).toEqual([['10',0],['10',1],['2',0],['2',1],['z',0]]);
    await expect(receipts.nth(3)).toContainText('missing');await expect(receipts.nth(4)).toContainText('Transient');
    await expect(dialog(page)).toContainText('Context membership unknown');
    const loadedReads=outputReads;const view=receipts.nth(1).getByRole('button',{name:'View current file',exact:true});await view.click();
    const viewer=page.locator('dialog#viewer-dialog');await expect(viewer).toBeVisible();
    await expect(viewer).toContainText('1 of 2 loaded');await expect(viewer.getByRole('region',{name:'Negative prompt',exact:true})).not.toContainText('same visible negative');
    await viewer.getByRole('button',{name:'Next media',exact:true}).click();await expect(viewer).toContainText('2 of 2 loaded');
    await page.keyboard.press('Escape');await expect(viewer).toHaveCount(0);await expect(dialog(page)).toBeVisible();
    await expect(receipts.nth(2)).toHaveAttribute('aria-current','true');await expect(receipts.nth(2).getByRole('button',{name:'View current file',exact:true})).toBeFocused();
    await receipts.nth(1).getByRole('button',{name:'View current file',exact:true}).click();await page.keyboard.press('Escape');
    await expect(receipts.nth(1)).toHaveAttribute('aria-current','true');
    expect(outputReads).toBe(loadedReads);
    await page.keyboard.press('Escape');await expect(dialog(page)).toHaveCount(0);await expect(marker(page,f.runs.shared)).toBeFocused();
    const window=await(await request.get(`/prompt_manager/library/${f.detail_id}/history`)).json();
    const outputs=await(await request.get(`/prompt_manager/history/runs/${f.runs.shared}/outputs?epoch=${window.history_epoch}`)).json();expect(outputs.items).toHaveLength(5);
});

test('inspection first discovers a real restore and immediately retires every surrounding recorded action',async({page,request})=>{
    const f=page.fixture;await open(page,f.detail_id);await marker(page,f.runs.shared).click();
    await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    const snapshot=await request.get('/__test__/history/snapshot');await expect(snapshot).toBeOK();
    await command(request,{action:'arm',boundary:'inspection'});
    const inspectionRead=page.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/output/current/inspection'));
    try {
        await dialog(page).locator('[data-receipt]').first().getByRole('button',{name:'View current file',exact:true}).click();
        await expect(page.locator('dialog#viewer-dialog')).toBeVisible();
        await expect.poll(async()=> (await(await request.get('/__test__/history/checkpoint')).json()).entered).toBe(true);
        await page.evaluate(()=>{
            window.recordedRecoveryStates=[];
            const run=document.querySelector('.recorded-run-dialog');let changed=false;
            const observer=new MutationObserver(()=>{
                changed ||= run.querySelector('.recorded-run-status').textContent.includes('Recorded evidence changed');
                if(changed)window.recordedRecoveryStates.push({receipts:run.querySelectorAll('[data-receipt]').length,active:[...run.querySelectorAll('[data-send-role],[data-copy-role],[data-view-receipt]')].filter(node=>!node.disabled&&node.checkVisibility()).length});
            });observer.observe(run,{subtree:true,childList:true,attributes:true,characterData:true});
        });
        await restoreSnapshot(page,request,await snapshot.body());
    } finally {await command(request,{action:'release'});}
    const response=await inspectionRead;expect(response.status()).toBe(409);expect((await response.json()).code).toBe('history-changed');
    await expect(dialog(page).locator('.recorded-run-status')).toContainText('Recorded evidence changed');
    const viewer=page.locator('dialog#viewer-dialog');
    if(await viewer.count())await page.keyboard.press('Escape');
    await expect(dialog(page)).toBeVisible();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(0);
    await expect(dialog(page).locator('[data-send-role]:enabled:visible,[data-copy-role]:enabled:visible,[data-view-receipt]:enabled:visible')).toHaveCount(0);
    await expect(recorded(page).locator('button[data-run-id]')).toHaveCount(0);
    await expect(page.locator('.library-parent-status')).toContainText('revalidated');
    const states=await page.evaluate(()=>window.recordedRecoveryStates);expect(states.length).toBeGreaterThan(0);expect(states.every(state=>state.receipts===0&&state.active===0)).toBe(true);
    await expect(dialog(page).getByRole('button',{name:'Reload recorded history',exact:true})).toBeVisible();
});

test('active stale inspection echo reaches history ownership without a second metadata implementation',async({page})=>{
    const f=page.fixture;await open(page,f.detail_id);await marker(page,f.runs.shared).click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    await page.route(/\/output\/current\/inspection\?/,async route=>{
        const response=await route.fetch(),body=await response.json();body.history_epoch='33333333-3333-3333-3333-333333333333';await route.fulfill({response,json:body});
    });
    await dialog(page).locator('[data-receipt]').first().getByRole('button',{name:'View current file',exact:true}).click();
    await expect(dialog(page).locator('.recorded-run-status')).toContainText('Recorded evidence changed');
    await expect(dialog(page).locator('[data-receipt]')).toHaveCount(0);await expect(recorded(page).locator('button[data-run-id]')).toHaveCount(0);
});

test('unavailable exact receipt selection and targeted retry observe a newly available file without writes',async({page,request})=>{
    const f=page.fixture;await open(page,f.detail_id);await page.getByRole('button',{name:'Select negative Saved empty',exact:true}).click();
    const keys=await gallery(page).locator('article[data-image-key]').evaluateAll(nodes=>nodes.map(node=>node.dataset.imageKey));
    let writes=0,galleryReads=0,outputReads=0;
    page.on('request',request=>{const url=new URL(request.url());if(url.pathname.startsWith('/prompt_manager/')&&request.method()!=='GET')writes++;if(url.pathname.endsWith('/images'))galleryReads++;if(url.pathname.endsWith('/outputs'))outputReads++;});
    await marker(page,f.runs.shared,'z').click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    await dialog(page).getByRole('button',{name:'Effective',exact:true}).click();
    const missing=dialog(page).locator('[data-output-node-id="2"][data-image-ordinal="1"]');await expect(missing).toContainText('missing');
    await missing.getByRole('button',{name:'Select reported output',exact:true}).click();await expect(missing).toHaveAttribute('aria-current','true');
    await expect(missing.getByRole('button',{name:'View current file',exact:true})).toBeHidden();
    await command(request,{action:'make-missing-available'});
    const beforeReads=outputReads,beforeGallery=galleryReads;
    await missing.getByRole('button',{name:'Retry current availability',exact:true}).click();
    await expect(missing).toContainText('Current candidate: available');await expect(missing).toHaveAttribute('aria-current','true');
    expect(outputReads).toBe(beforeReads+1);expect(galleryReads).toBe(beforeGallery);expect(writes).toBe(0);
    await expect(dialog(page).getByRole('button',{name:'Effective',exact:true})).toHaveAttribute('aria-pressed','true');
    await expect(dialog(page).getByRole('group',{name:'Recorded anchors',exact:true}).locator('[aria-pressed="true"]')).toContainText('z');
    await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    await missing.getByRole('button',{name:'View current file',exact:true}).click();await expect(page.locator('dialog#viewer-dialog')).toBeVisible();await page.keyboard.press('Escape');
    await expect(missing).toHaveAttribute('aria-current','true');await page.keyboard.press('Escape');
    await expect(page.getByRole('button',{name:'Select negative Saved empty',exact:true})).toHaveAttribute('aria-pressed','true');
    expect(await gallery(page).locator('article[data-image-key]').evaluateAll(nodes=>nodes.map(node=>node.dataset.imageKey))).toEqual(keys);expect(writes).toBe(0);
});

test('marker labels disclose exact observed dequeue and outcome including times outside display range',async({page,request})=>{
    const f=page.fixture;await open(page,f.detail_id);const window=await historyRead(request,f.detail_id);
    for(const run of window.items)for(const anchor of [...new Map(run.matching_anchors.map(anchor=>[anchor.context_id,run.matching_anchors.find(first=>first.context_id===anchor.context_id)])).values()]) {
        const button=recorded(page).locator(`button[data-run-id="${run.run_id}"][data-context-id="${anchor.context_id}"]`);
        const label=await button.getAttribute('aria-label');
        expect(label).toContain(`Observed dequeue ${run.observed_dequeue_at} ms`);expect(label).toContain(`Outcome ${run.outcome}`);expect(label).toContain(`anchor ${anchor.anchor_id}`);
        expect(await button.getAttribute('title')).toBe(label);
        if(run.observed_dequeue_at==='9223372036854775807')expect(label).toContain('outside display range');
    }
});

// A timeline scroll re-renders the markers on the next frame. On a slow runner that frame lands between the press and
// the release of a marker tap (Playwright's own scroll-into-view scrolls the timeline first); the re-render must leave
// the pressed marker's nodes alone, or Chromium touch drops the click. The scroll event is dispatched so the marker
// stays under the pointer, and the two frames are the re-render itself.
test('a timeline re-render between press and release keeps the marker tap',async({page})=>{
    const f=page.fixture;await open(page,f.detail_id);
    const target=marker(page,f.runs.shared,'z');await expect(target).toHaveAttribute('aria-disabled','false');await target.scrollIntoViewIfNeeded();
    const box=await target.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();
    await recorded(page).locator('.history-timeline').evaluate(timeline=>{timeline.dispatchEvent(new Event('scroll'));return new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});
    await page.mouse.up();
    await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
});

test('same-epoch refresh retains recorded context mode and exact receipt after fresh candidate reads',async({page})=>{
    const f=page.fixture;await open(page,f.detail_id);await marker(page,f.runs.shared,'z').click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    await dialog(page).getByRole('button',{name:'Effective',exact:true}).click();
    const selected=dialog(page).locator('[data-output-node-id="2"][data-image-ordinal="1"]');await selected.getByRole('button',{name:'Select reported output',exact:true}).click();
    await page.keyboard.press('Escape');
    let reads=0;page.on('request',request=>{if(new URL(request.url()).pathname.endsWith('/outputs'))reads++;});
    await recorded(page).getByRole('button',{name:'Refresh history',exact:true}).click();
    await expect(marker(page,f.runs.shared,'z')).toHaveAttribute('aria-pressed','true');await expect.poll(()=>reads).toBe(1);
    await marker(page,f.runs.shared,'z').click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    await expect(selected).toHaveAttribute('aria-current','true');await expect(dialog(page).getByRole('button',{name:'Effective',exact:true})).toHaveAttribute('aria-pressed','true');
    await expect(dialog(page).getByRole('group',{name:'Recorded anchors',exact:true}).locator('[aria-pressed="true"]')).toContainText('z');
});

test('route reentry restores older timeline range and both scroll axes after 61 new same-epoch runs',async({page,request})=>{
    const reset=await request.post('/__test__/reset',{data:{chronological_history:true,history_runs:85}});await expect(reset).toBeOK();const f=await reset.json();
    await page.addInitScript(()=>{window.IntersectionObserver=undefined;});await open(page,f.positive_id);
    await expect(recorded(page)).toContainText('40 loaded positions');await recorded(page).getByRole('button',{name:'Load earlier runs',exact:true}).click();await expect(recorded(page)).toContainText('80 loaded positions');
    const oldIds=await recorded(page).locator('[data-history-run]').evaluateAll(nodes=>nodes.map(node=>node.dataset.historyRun));const oldRun=oldIds[0];
    await marker(page,oldRun).click();await expect(dialog(page)).toContainText(oldRun);await dialog(page).getByRole('button',{name:'Effective',exact:true}).click();await page.keyboard.press('Escape');
    const timeline=recorded(page).locator('.history-timeline');await timeline.evaluate(node=>{node.scrollLeft=731;node.scrollTop=95;node.dispatchEvent(new Event('scroll'));});
    const scroll=await timeline.evaluate(node=>({left:node.scrollLeft,top:node.scrollTop}));expect(scroll.left).toBeGreaterThan(0);expect(scroll.top).toBeGreaterThan(0);
    await page.getByRole('button',{name:'← Prompt library',exact:true}).evaluate(node=>node.click());await expect(recorded(page)).toHaveCount(0);await command(request,{action:'append-history-runs'});
    let windowReads=0;page.on('request',request=>{if(/\/library\/\d+\/history$/.test(new URL(request.url()).pathname))windowReads++;});
    let release,entered;const held=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{entered=resolve;});
    await page.route(new RegExp(`/history/runs/${oldRun}\\?`),async route=>{const response=await route.fetch();entered();await held;await route.fulfill({response}).catch(()=>{});});
    try {
        await page.evaluate(id=>{location.hash=`#/library/${id}`;},f.positive_id);await started;
        await expect(recorded(page)).toContainText('80 loaded positions');await expect(marker(page,oldRun)).toHaveAttribute('aria-disabled','true');
    } finally {release();}
    await expect(marker(page,oldRun)).toHaveAttribute('aria-pressed','true');await expect(marker(page,oldRun)).toHaveAttribute('aria-disabled','false');
    expect(await recorded(page).locator('[data-history-run]').evaluateAll(nodes=>nodes.map(node=>node.dataset.historyRun))).toEqual(oldIds);expect(windowReads).toBe(2);
    const restored=await timeline.evaluate(node=>({left:node.scrollLeft,top:node.scrollTop}));expect(Math.abs(restored.left-scroll.left)).toBeLessThanOrEqual(1);expect(Math.abs(restored.top-scroll.top)).toBeLessThanOrEqual(1);
    await recorded(page).getByRole('button',{name:'Refresh history',exact:true}).click();await expect(recorded(page)).toContainText('149 distinct recorded runs');await expect(recorded(page)).toContainText('40 loaded positions');
    await expect(recorded(page).locator('.history-status')).toContainText(`Selected run ${oldRun} is outside this loaded timeline window`);await expect(marker(page,oldRun)).toHaveCount(0);
    await recorded(page).getByRole('button',{name:'View selected run',exact:true}).click();await expect(dialog(page)).toContainText(oldRun);await expect(dialog(page).getByRole('button',{name:'Effective',exact:true})).toHaveAttribute('aria-pressed','true');
});

for(const [width,height] of [[1600,1100],[390,844]])for(const theme of ['dark','light'])test(`final fix review states ${width} ${theme}`,async({page,request},testInfo)=>{
    test.setTimeout(60000);await page.setViewportSize({width,height});await page.addInitScript(()=>{window.IntersectionObserver=undefined;});
    const f=page.fixture;await open(page,f.detail_id);await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);
    await marker(page,f.runs.shared,'z').click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    const missing=dialog(page).locator('[data-output-node-id="2"][data-image-ordinal="1"]');await missing.getByRole('button',{name:'Select reported output',exact:true}).click();
    await expect(missing).toHaveAttribute('aria-current','true');await expect(missing.getByRole('button',{name:'Retry current availability',exact:true})).toBeVisible();
    await reviewCapture(page,testInfo,`missing-selected-${width}-${theme}`,missing);
    await reviewCapture(page,testInfo,`missing-dialog-${width}-${theme}`,dialog(page));
    await command(request,{action:'make-missing-available'});await missing.getByRole('button',{name:'Retry current availability',exact:true}).click();
    await expect(missing.getByRole('button',{name:'View current file',exact:true})).toBeVisible();await expect(missing).toHaveAttribute('aria-current','true');
    await reviewCapture(page,testInfo,`available-selected-${width}-${theme}`,missing);await page.keyboard.press('Escape');
    const reset=await request.post('/__test__/reset',{data:{chronological_history:true,history_runs:85}});await expect(reset).toBeOK();const range=await reset.json();await open(page,range.positive_id);
    await recorded(page).getByRole('button',{name:'Load earlier runs',exact:true}).click();await expect(recorded(page)).toContainText('80 loaded positions');
    const ids=await recorded(page).locator('[data-history-run]').evaluateAll(nodes=>nodes.map(node=>node.dataset.historyRun)),oldRun=ids[0];
    await marker(page,oldRun).click();await expect(dialog(page)).toContainText(oldRun);await dialog(page).getByRole('button',{name:'Effective',exact:true}).click();await page.keyboard.press('Escape');
    const timeline=recorded(page).locator('.history-timeline');await timeline.evaluate(node=>{node.scrollLeft=731;node.scrollTop=95;node.dispatchEvent(new Event('scroll'));});
    const before=await timeline.evaluate(node=>({left:node.scrollLeft,top:node.scrollTop}));await reviewCapture(page,testInfo,`range-before-${width}-${theme}`,timeline);
    await page.getByRole('button',{name:'← Prompt library',exact:true}).evaluate(node=>node.click());await expect(recorded(page)).toHaveCount(0);await command(request,{action:'append-history-runs'});
    await page.evaluate(id=>{location.hash=`#/library/${id}`;},range.positive_id);await expect(recorded(page)).toContainText('80 loaded positions');await expect(marker(page,oldRun)).toHaveAttribute('aria-disabled','false');await expect(marker(page,oldRun)).toHaveAttribute('aria-pressed','true');
    const after=await timeline.evaluate(node=>({left:node.scrollLeft,top:node.scrollTop}));expect(Math.abs(after.left-before.left)).toBeLessThanOrEqual(1);expect(Math.abs(after.top-before.top)).toBeLessThanOrEqual(1);
    expect(await recorded(page).locator('[data-history-run]').evaluateAll(nodes=>nodes.map(node=>node.dataset.historyRun))).toEqual(ids);
    await reviewCapture(page,testInfo,`range-after-${width}-${theme}`,timeline);
    await recorded(page).getByRole('button',{name:'Refresh history',exact:true}).click();await expect(recorded(page)).toContainText(`Selected run ${oldRun} is outside this loaded timeline window`);await expect(recorded(page)).toContainText('149 distinct recorded runs');
    await reviewCapture(page,testInfo,`outside-window-${width}-${theme}`,recorded(page));
    await recorded(page).getByRole('button',{name:'View selected run',exact:true}).click();await expect(dialog(page)).toContainText(oldRun);await expect(dialog(page).getByRole('button',{name:'Effective',exact:true})).toHaveAttribute('aria-pressed','true');
    await reviewCapture(page,testInfo,`outside-selected-dialog-${width}-${theme}`,dialog(page));
    const hashes={};
    for(const path of ['docs/concepts/workspace/index.html','docs/concepts/workspace/app.js','docs/concepts/workspace/style.css','database/history_reads.py','py/api/history.py','web/js/workspace/history-state.mjs','web/js/workspace/history-media.mjs','web/js/workspace/history.mjs','web/js/workspace/recorded-run.mjs','web/js/workspace/workspace.css'])hashes[path]=createHash('sha256').update(await readFile(new URL(`../../${path}`,import.meta.url))).digest('hex');
    expect(hashes['docs/concepts/workspace/index.html']).toBe('d84712c7284bb232919a1ed9b154575c8ac574f63b2404a4ac4bc06cbf323c03');expect(hashes['docs/concepts/workspace/app.js']).toBe('e33e7652da12a68cd3fe31e94d77d680aa439b6434ceee94f21cc4ce8883263e');expect(hashes['docs/concepts/workspace/style.css']).toBe('2c479ca1a2ddbbd14a550eae27dd731c326ccdb91fe9ddc87e2b81cd5298fce8');
    const manifest=testInfo.outputPath('final-fix-visual-source-and-scroll.json');await writeFile(manifest,JSON.stringify({width,height,theme,oldRun,before,after,hashes},null,2));
    await testInfo.attach('final-fix-visual-source-and-scroll.json',{path:manifest,contentType:'application/json'});
});

test('output capture distinguishes unknown, complete zero, partial and conflicts with exact out-of-range time',async({page})=>{
    const f=page.fixture;await open(page,f.detail_id);
    for(const [name,expected] of [['equal_looking','Reported outputs not captured'],['empty','0 reported outputs · complete'],['whitespace','1 retained reported output · incomplete'],['conflicting','First captured context · conflicting evidence']]) {
        await marker(page,f.runs[name]).click();await expect(dialog(page)).toContainText(expected);
        if(name==='conflicting') {await expect(dialog(page)).toContainText('Recorded time outside display range');await expect(dialog(page)).toContainText('9223372036854775807');}
        await page.keyboard.press('Escape');
    }
});

test('history, run and output failures retry their own reads and route exit ignores late callbacks',async({page})=>{
    const f=page.fixture;let windowFailure=true,runFailure=true,outputFailure=true;
    await page.route(/\/library\/\d+\/history\?/,async route=>windowFailure?route.fulfill({status:503,json:{success:false}}):route.continue());
    await page.goto(`/prompt_manager/workspace#/library/${f.detail_id}`);await expect(recorded(page).getByRole('button',{name:'Retry recorded history',exact:true})).toBeVisible();
    await showLinkedImage(page);await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(1);windowFailure=false;await recorded(page).getByRole('button',{name:'Retry recorded history',exact:true}).click();
    await page.route(/\/history\/runs\/[^/]+\?/,async route=>runFailure?route.fulfill({status:503,json:{success:false}}):route.continue());
    await page.route(/\/history\/runs\/[^/]+\/outputs\?/,async route=>outputFailure?route.fulfill({status:503,json:{success:false}}):route.continue());
    await marker(page,f.runs.shared).click();await expect(dialog(page).getByRole('button',{name:'Retry run details',exact:true})).toBeVisible();runFailure=false;await dialog(page).getByRole('button',{name:'Retry run details',exact:true}).click();
    await expect(dialog(page).getByRole('button',{name:'Retry reported outputs',exact:true})).toBeVisible();outputFailure=false;await dialog(page).getByRole('button',{name:'Retry reported outputs',exact:true}).click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    await page.keyboard.press('Escape');let release;const held=new Promise(resolve=>{release=resolve;});await page.route(/\/history\/runs\/[^/]+\?/,async route=>{await held;await route.continue().catch(()=>{});});
    await marker(page,f.runs.empty).click();await page.evaluate(()=>{location.hash='#/overview';});release();await expect(page.locator('#workspace-main h1')).toHaveText('Overview');await expect(page.locator('dialog[open]')).toHaveCount(0);await expect(page.locator('.viewer-container')).toHaveCount(0);
});

test('more than 1000 real runs retain sparse columns, focused identity and pixel anchors on prepend with moveBefore absent',async({page,request})=>{
    test.setTimeout(120000);const response=await request.post('/__test__/reset',{data:{chronological_history:true,history_runs:1001}});await expect(response).toBeOK();const f=await response.json();
    await page.addInitScript(()=>{window.IntersectionObserver=undefined;Element.prototype.moveBefore=undefined;});await open(page,f.positive_id);
    const more=recorded(page).getByRole('button',{name:'Load earlier runs',exact:true,includeHidden:true});
    await expect(recorded(page).locator('[data-history-run]')).toHaveCount(40);
    const first=recorded(page).getByRole('list',{name:'Recorded run positions',exact:true}).getByRole('button').first();
    await first.focus();await first.evaluate(node=>{window.retainedHistoryButton=node;window.retainedHistoryRun=node.dataset.runId;window.retainedHistoryX=node.getBoundingClientRect().left;window.retainedHistoryY=node.getBoundingClientRect().top;});
    await more.evaluate(node=>node.click());await expect(recorded(page)).toContainText('80 loaded positions');
    const retained=await page.evaluate(()=>({same:window.retainedHistoryButton===document.querySelector(`[data-run-id="${window.retainedHistoryRun}"]`),focus:document.activeElement===window.retainedHistoryButton,delta:Math.abs(window.retainedHistoryX-window.retainedHistoryButton.getBoundingClientRect().left),vertical:Math.abs(window.retainedHistoryY-window.retainedHistoryButton.getBoundingClientRect().top)}));
    expect(retained.same&&retained.focus).toBe(true);expect(retained.delta).toBeLessThanOrEqual(1);expect(retained.vertical).toBeLessThanOrEqual(1);
    while(await more.isVisible()) {await more.evaluate(node=>node.click());await expect(more).not.toHaveAttribute('aria-disabled','true');}
    await expect(recorded(page)).toContainText('1004 loaded positions');
    const timeline=recorded(page).locator('.history-timeline');await timeline.evaluate(node=>{node.scrollLeft=node.scrollWidth/2;node.dispatchEvent(new Event('scroll'));});
    const bound=await timeline.evaluate(node=>Math.ceil(node.clientWidth/64)+8);
    await expect.poll(()=>recorded(page).locator('[data-history-run]').count()).toBeLessThanOrEqual(bound);
    expect(await recorded(page).locator('[data-run-id]').count()).toBeLessThanOrEqual(bound);
    expect(await page.evaluate(()=>window.retainedHistoryButton.isConnected&&document.activeElement===window.retainedHistoryButton)).toBe(true);
    await page.getByRole('button',{name:'← Prompt library',exact:true}).evaluate(node=>node.click());await expect(recorded(page)).toHaveCount(0);
});

test('390px and 200 percent layout keeps scrolling inside timeline and coarse targets remain 44px',async({page})=>{
    await page.setViewportSize({width:390,height:844});await open(page,page.fixture.detail_id);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
    await page.locator('#workspace-main').evaluate(node=>{node.style.zoom='2';});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
    await marker(page,page.fixture.runs.shared).click();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);await page.keyboard.press('Escape');
    await page.locator('#workspace-main').evaluate(node=>node.style.removeProperty('zoom'));
    await page.emulateMedia({forcedColors:'active'});await expect(marker(page,page.fixture.runs.shared)).toBeVisible();
});

test('frozen Effective pair uses the shared acknowledged sender and Escape closes only its top dialog',async({page,context})=>{
    const host=await context.newPage();await host.goto('/prompt_manager/workspace#/overview');
    await host.evaluate(async()=>{
        const {createBridgeHost}=await import('/prompt_manager/js/shared/toolkit/prompt-bridge.js');
        const target={id:'history-pair',label:'History destination',type:'PromptManagerPair',roles:['positive','negative']};window.historyApplied=[];
        window.historyHost=createBridgeHost({origin:location.origin,namespace:'/',label:'History ComfyUI',adapter:{listTargets:()=>[target],
            preview:(_id,roles)=>({preview:{target,fields:Object.entries(roles).map(([role,after])=>({role,before:`Previous ${role}`,after})),composition:{prepend:'',append:'',prependConnected:false,appendConnected:false,before:'Previous positive',after:roles.positive??'Previous positive'}},snapshot:roles}),
            apply:roles=>{window.historyApplied.push(roles);return{targetId:target.id,values:roles};}}});
    });
    try {
        await open(page,page.fixture.detail_id);await marker(page,page.fixture.runs.unknown).click();await dialog(page).getByRole('button',{name:'Effective',exact:true}).click();
        const positive=await exact(dialog(page).getByRole('region',{name:'Positive prompt',exact:true}).locator('p.prompt-text'));
        const send=dialog(page).getByRole('button',{name:'Send prompt pair',exact:true});await send.click();
        const sender=page.getByRole('dialog',{name:'Send to ComfyUI',exact:true});await sender.getByRole('combobox',{name:'ComfyUI window',exact:true}).selectOption({label:'History ComfyUI'});
        await sender.getByRole('combobox',{name:'Destination node',exact:true}).selectOption({label:'History destination'});await sender.getByRole('button',{name:'Preview changes',exact:true}).click();
        await expect(sender.getByRole('button',{name:'Apply to ComfyUI',exact:true})).toBeEnabled();await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();await expect(sender).toContainText('Sent to ComfyUI.');
        expect(await host.evaluate(()=>window.historyApplied)).toEqual([{positive,negative:'  \n\t '}]);
        await page.keyboard.press('Escape');await expect(sender).toHaveCount(0);await expect(dialog(page)).toBeVisible();await expect(send).toBeFocused();
        await page.keyboard.press('Escape');await expect(dialog(page)).toHaveCount(0);
    } finally {await host.evaluate(()=>window.historyHost.dispose());await host.close();}
});

test('the inline current file names its prompts apart from the frozen run fields',async({page})=>{
    // The current file resolves its own prompts, as a real host's saved PNG does, so the dialog holds two prompt texts per role.
    await page.route(url=>url.pathname.endsWith('/inspection'),async route=>{const response=await route.fetch();const body=await response.json();
        body.prompts={positive:{status:'resolved',text:'current file positive',source:'graph-inferred'},negative:{status:'resolved',text:'current file negative',source:'graph-inferred'}};
        await route.fulfill({response,json:body});});
    await open(page,page.fixture.detail_id);await marker(page,page.fixture.runs.shared).click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    await dialog(page).getByRole('button',{name:'Raw',exact:true}).click();
    const frozen=dialog(page).getByRole('region',{name:'Frozen run prompt texts',exact:true});
    const before=await exact(frozen.getByRole('region',{name:'Positive prompt',exact:true}).locator('p.prompt-text'));
    await dialog(page).locator('[data-receipt]').first().getByRole('button',{name:'View current file',exact:true}).click();
    await expect(page.locator('dialog#viewer-dialog')).toBeVisible();await page.keyboard.press('Escape');await expect(dialog(page)).toBeVisible();
    const current=dialog(page).getByRole('region',{name:'Current reported file',exact:true});await expect(current).toBeVisible();
    await expect(current.getByRole('region',{name:'Current file positive prompt',exact:true}).locator('p.prompt-text')).toHaveText('current file positive');
    await expect(current.getByRole('region',{name:'Current file negative prompt',exact:true}).locator('p.prompt-text')).toHaveText('current file negative');
    await expect(current.getByRole('button',{name:/^Copy current file positive prompt: /})).toBeVisible();
    for(const role of ['Positive','Negative'])await expect(dialog(page).getByRole('region',{name:`${role} prompt`,exact:true})).toHaveCount(1);
    await expect(current.getByRole('region',{name:/^(Positive|Negative) prompt$/})).toHaveCount(0);
    expect(await exact(dialog(page).getByRole('region',{name:'Positive prompt',exact:true}).locator('p.prompt-text'))).toBe(before);
});

// #449 (D154), #457: History's inline current file draws the file's own image tags in the shared labelled section, named
// with its context so they stay apart from the frozen run's fields. The tags are saved on the real file through the Tag
// routes and come from History's own current-file read (its `image_tags` facet), as Images reads them.
test('the inline current file labels its image tags after its negative prompt',async({page,request})=>{
    const reads=[];
    await page.route(url=>url.pathname.endsWith('/inspection'),async route=>{const response=await route.fetch();const body=await response.json();
        reads.push(body.image_tags);
        body.prompts={positive:{status:'resolved',text:'current file positive',source:'graph-inferred'},negative:{status:'resolved',text:'current file negative',source:'graph-inferred'}};
        await route.fulfill({response,json:body});});
    const current=dialog(page).getByRole('region',{name:'Current reported file',exact:true});
    const tags=current.getByRole('region',{name:'Current file image tags',exact:true});
    const showCurrent=async()=>{
        await open(page,page.fixture.detail_id);await marker(page,page.fixture.runs.shared).click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
        await dialog(page).getByRole('button',{name:'Raw',exact:true}).click();
        await dialog(page).locator('[data-receipt]').first().getByRole('button',{name:'View current file',exact:true}).click();
        await expect(page.locator('dialog#viewer-dialog')).toBeVisible();await page.keyboard.press('Escape');await expect(dialog(page)).toBeVisible();
        await expect(current.getByRole('region',{name:'Current file negative prompt',exact:true})).toBeVisible();
    };
    // An untagged file draws no image tags section.
    await showCurrent();await expect(tags).toHaveCount(0);expect(reads.at(-1)).toEqual({state:'none',tags:[]});
    await dialog(page).getByRole('button',{name:'Close recorded run',exact:true}).click();
    expect(await saveImageTags(request,'history/current.png',['history_tag','dusk'])).toEqual(['history_tag','dusk']);
    await showCurrent();expect(reads.at(-1)).toEqual({state:'current',tags:['history_tag','dusk']});
    await expect(tags.locator('.role-label')).toHaveText('Image tags · 2');await expect(tags.locator('.tags > .chip')).toHaveText(['history_tag','dusk']);
    const negative=current.getByRole('region',{name:'Current file negative prompt',exact:true});
    expect(await negative.evaluate((node,section)=>({after:Boolean(node.compareDocumentPosition(section)&Node.DOCUMENT_POSITION_FOLLOWING),inside:node.contains(section)}),await tags.elementHandle())).toEqual({after:true,inside:false});
    // The viewer opened from History shares the inspection: the same section, plain chips (no Tags filter there, D88).
    await dialog(page).locator('[data-receipt]').first().getByRole('button',{name:'View current file',exact:true}).click();
    const viewerTags=page.locator('dialog#viewer-dialog #viewer-details section.tag-section.image-tags');
    await expect(viewerTags.locator('.role-label')).toHaveText('Image tags · 2');
    await expect(viewerTags.locator('span.chip')).toHaveText(['history_tag','dusk']);await expect(viewerTags.locator('button')).toHaveCount(0);
    await page.keyboard.press('Escape');await expect(dialog(page)).toBeVisible();
});

// #332 CUJ 5 (MC8, §5.3): History's current-file inspector reads the file's own category with the Images key rule, so
// the inline current file and its viewer show "Portraits · this image"; current.png has two saved links, so it inherits
// nothing and shows no chip before.
test('the current file shows its own category with its origin in the recorded run and its viewer',async({page,request})=>{
    const chip=scope=>scope.locator('span.chip:has(> span.chip-source)');
    const current=dialog(page).getByRole('region',{name:'Current reported file',exact:true});
    const viewCurrent=async()=>{await dialog(page).locator('[data-receipt]').first().getByRole('button',{name:'View current file',exact:true}).click();
        const details=page.locator('dialog#viewer-dialog #viewer-details');await expect(details.locator('.panel-heading')).toBeVisible();return details;};
    await open(page,page.fixture.detail_id);await marker(page,page.fixture.runs.shared).click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    let details=await viewCurrent();await expect(details.locator('.panel-heading h2')).toBeVisible();await expect(chip(details)).toHaveCount(0);
    await page.keyboard.press('Escape');await expect(current).toBeVisible();await expect(chip(current)).toHaveCount(0);
    await dialog(page).getByRole('button',{name:'Close recorded run',exact:true}).click();
    const {images}=await(await request.get('/prompt_manager/images/output?mode=workspace&subfolder=history&limit=40')).json();
    const file=images.find(image=>image.root_index===0&&image.path==='history/current.png');
    const put=await request.put('/prompt_manager/images/category',{data:{items:[{root_index:0,root_scope:file.root_scope,path:file.path}],category:'Portraits'}});
    await expect(put).toBeOK();expect((await put.json()).updated).toBe(1);
    await open(page,page.fixture.detail_id);await marker(page,page.fixture.runs.shared).click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    details=await viewCurrent();
    await expect(chip(details)).toHaveText('Image category: Portraits · this image');
    await page.keyboard.press('Escape');await expect(current).toBeVisible();
    await expect(chip(current)).toHaveText('Image category: Portraits · this image');
    expect(await chip(current).locator('.chip-source').evaluate(node=>node.textContent)).toBe(' · this image');
});

// LI-4 (D117): the recorded history's current-file viewer is the shared full inspector, so it copies the legacy summary.
test('the current file viewer copies its settings summary',async({page})=>{
    await page.addInitScript(()=>{window.__copied=[];Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.__copied.push(text);}}});});
    await page.route(url=>url.pathname.endsWith('/inspection'),async route=>{const response=await route.fetch();const body=await response.json();
        body.prompts={positive:{status:'resolved',text:'current file positive',source:'graph-inferred'},negative:{status:'resolved',text:'current file negative',source:'graph-inferred'}};
        await route.fulfill({response,json:body});});
    await open(page,page.fixture.detail_id);await marker(page,page.fixture.runs.shared).click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    await dialog(page).locator('[data-receipt]').first().getByRole('button',{name:'View current file',exact:true}).click();
    const details=page.locator('dialog#viewer-dialog #viewer-details');await expect(details.locator('.panel-heading')).toBeVisible();
    const facts=await details.locator(':scope > div > dl.meta-grid dd').allTextContents();
    await details.getByRole('button',{name:'Copy settings summary',exact:true}).click();
    await expect(page.locator('#workspace-toast')).toHaveText('Settings summary copied.');
    const [seed,steps,cfg,sampler,,model]=facts;
    expect(await page.evaluate(()=>window.__copied)).toEqual([[`Checkpoint: ${model}`,'Positive Prompt: current file positive','Negative Prompt: current file negative',
        `Steps: ${steps}`,`CFG Scale: ${cfg}`,`Sampler: ${sampler}`,`Seed: ${seed}`].join('\n').replace(/: (Not recorded|Unresolved|Ambiguous|Unavailable within inspection limits)(?=\n|$)/g,': Unknown')]);
});

test('the recorded run dialog with a resolved current file has no duplicate button or region names',async({page})=>{
    await page.route(url=>url.pathname.endsWith('/inspection'),async route=>{const response=await route.fetch();const body=await response.json();
        body.prompts={positive:{status:'resolved',text:'current file positive',source:'graph-inferred'},negative:{status:'resolved',text:'current file negative',source:'graph-inferred'}};
        await route.fulfill({response,json:body});});
    await open(page,page.fixture.detail_id);await marker(page,page.fixture.runs.shared).click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    await dialog(page).getByRole('button',{name:'Raw',exact:true}).click();
    await dialog(page).locator('[data-receipt]').first().getByRole('button',{name:'View current file',exact:true}).click();
    await expect(page.locator('dialog#viewer-dialog')).toBeVisible();await page.keyboard.press('Escape');
    await expect(dialog(page).getByRole('region',{name:'Current reported file',exact:true})).toBeVisible();
    await expect(dialog(page).getByRole('region',{name:'Current file positive prompt',exact:true})).toBeVisible();
    // Receipt rows repeat per-row actions by design; every other control and region in the dialog must be uniquely named.
    const snapshot=await dialog(page).ariaSnapshot();const counts=new Map();
    for(const line of snapshot.split('\n')){const match=/^\s*- (button|region|link) "((?:[^"\\]|\\.)*)"/.exec(line);if(!match)continue;
        const key=`${match[1]} ${match[2]}`;counts.set(key,(counts.get(key)||0)+1);}
    expect(counts.size).toBeGreaterThan(10);
    const receiptNames=await dialog(page).locator('[data-receipt]').first().evaluate(row=>[...row.querySelectorAll('button,a,[role="region"]')].map(node=>node.getAttribute('aria-label')||node.textContent.trim()));
    const repeated=[...counts].filter(([key,n])=>n>1&&!receiptNames.some(name=>key.endsWith(` ${name}`)));
    expect(repeated).toEqual([]);
});

test('pagehide tears down dialogs and persisted reentry revalidates before enabling recorded actions',async({page})=>{
    await open(page,page.fixture.detail_id);await marker(page,page.fixture.runs.shared).click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    await dialog(page).locator('[data-receipt]').first().getByRole('button',{name:'View current file',exact:true}).click();await expect(page.locator('dialog#viewer-dialog')).toBeVisible();
    await page.evaluate(()=>dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true})));await expect(page.locator('dialog[open],.viewer-container')).toHaveCount(0);
    let release;const held=new Promise(resolve=>{release=resolve;});let checks=0;
    await page.route(/\/history\/runs\/[^/]+\?/,async route=>{checks++;await held;await route.continue().catch(()=>{});});
    await page.evaluate(()=>dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));await expect(recorded(page)).toBeVisible();
    await expect(recorded(page).locator('button[data-run-id]').first()).toHaveAttribute('aria-disabled','true');
    await marker(page,page.fixture.runs.shared).evaluate(node=>node.click());await expect(dialog(page)).toHaveCount(0);release();
    await expect(recorded(page).locator('button[data-run-id]').first()).toHaveAttribute('aria-disabled','false');expect(checks).toBeGreaterThan(0);await expect(dialog(page)).toHaveCount(0);
    await marker(page,page.fixture.runs.shared).click();await expect(dialog(page)).toContainText(page.fixture.runs.shared);await page.keyboard.press('Escape');
});

test('native connected moves and keyboard navigation preserve marker identity through earlier branch reorder',async({page,request})=>{
    const result=await request.post('/__test__/reset',{data:{chronological_history:true,history_runs:140}});await expect(result).toBeOK();const f=await result.json();
    await page.addInitScript(()=>{window.IntersectionObserver=undefined;window.historyMoves=0;const move=Element.prototype.moveBefore;window.hasHistoryMove=typeof move==='function';if(move)Element.prototype.moveBefore=function(...args){window.historyMoves++;return move.apply(this,args);};});
    await open(page,f.positive_id);const buttons=recorded(page).locator('button[data-run-id]');await expect(buttons).toHaveCount(40);await buttons.first().focus();
    await buttons.first().evaluate(node=>{window.keyedHistoryButton=node;window.keyedHistoryX=node.getBoundingClientRect().left;window.keyedHistoryY=node.getBoundingClientRect().top;});
    await recorded(page).getByRole('button',{name:'Load earlier runs',exact:true}).evaluate(node=>node.click());await expect(recorded(page)).toContainText('80 loaded positions');
    const retained=await page.evaluate(()=>({same:window.keyedHistoryButton.isConnected,focused:document.activeElement===window.keyedHistoryButton,delta:Math.abs(window.keyedHistoryX-window.keyedHistoryButton.getBoundingClientRect().left),vertical:Math.abs(window.keyedHistoryY-window.keyedHistoryButton.getBoundingClientRect().top)}));
    expect(retained.same&&retained.focused).toBe(true);expect(retained.delta).toBeLessThanOrEqual(1);expect(retained.vertical).toBeLessThanOrEqual(1);
    if(await page.evaluate(()=>window.hasHistoryMove)) expect(await page.evaluate(()=>window.historyMoves)).toBeGreaterThan(0);
    await page.keyboard.press('End');await expect(recorded(page).locator('[data-history-run]').last()).toHaveAttribute('aria-posinset','80');
    expect(await page.evaluate(()=>document.activeElement.closest('[data-history-run]')?.getAttribute('aria-posinset'))).toBe('80');
});

test('coarse-pointer markers expose 44px targets while retaining the small repeat visual',async({browser,request})=>{
    const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true});const page=await context.newPage();
    try {
        const reset=await request.post('/__test__/reset',{data:{chronological_history:true}});const f=await reset.json();await open(page,f.positive_id);const buttons=recorded(page).locator('button[data-run-id]');await expect(buttons).toHaveCount(3);
        expect(await buttons.evaluateAll(nodes=>nodes.every(node=>node.getBoundingClientRect().width>=44&&node.getBoundingClientRect().height>=44))).toBe(true);
        const repeat=marker(page,f.aab[1]);expect(await repeat.evaluate(node=>({width:getComputedStyle(node,'::before').width,height:getComputedStyle(node,'::before').height}))).toEqual({width:'12px',height:'12px'});
        await repeat.tap();await expect(dialog(page)).toContainText(f.aab[1]);await dialog(page).getByRole('button',{name:'Close recorded run',exact:true}).tap();await expect(dialog(page)).toHaveCount(0);
    } finally {await context.close();}
});

test('coarse recorded toolbar and disclosure expose 44px targets and real touch actions',async({browser,browserName,request},testInfo)=>{
    const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:browserName!=='firefox'});const page=await context.newPage();
    try {
        const reset=await request.post('/__test__/reset',{data:{chronological_history:true}});const f=await reset.json();await open(page,f.positive_id);
        expect(await page.evaluate(()=>matchMedia('(pointer: coarse)').matches)).toBe(true);
        const controls=recorded(page).locator('.history-toolbar button:visible,.history-scope summary');
        const sizes=await controls.evaluateAll(nodes=>nodes.map(node=>({label:node.textContent,width:node.getBoundingClientRect().width,height:node.getBoundingClientRect().height})));
        await testInfo.attach('coarse-recorded-controls.json',{body:JSON.stringify(sizes,null,2),contentType:'application/json'});
        for(const control of sizes) {expect.soft(control.width,control.label).toBeGreaterThanOrEqual(44);expect.soft(control.height,control.label).toBeGreaterThanOrEqual(44);}
        await recorded(page).getByText('Recorded history scope',{exact:true}).tap();await expect(recorded(page).locator('.history-scope')).toHaveAttribute('open','');
        await recorded(page).getByRole('button',{name:'Refresh history',exact:true}).tap();await expect(recorded(page).locator('button[data-run-id]')).toHaveCount(3);
    } finally {await context.close();}
});

test('capture recovery reloads only reported outputs while retaining frozen run and saved gallery',async({page})=>{
    const f=page.fixture;let failures=2,outputReads=0,runReads=0,galleryReads=0;
    page.on('request',request=>{const path=new URL(request.url()).pathname;if(path===`/prompt_manager/history/runs/${f.runs.shared}`)runReads++;if(path===`/prompt_manager/library/${f.detail_id}/images`)galleryReads++;});
    await page.route(/\/history\/runs\/[^/]+\/outputs\?/,async route=>{
        outputReads++;if(failures-->0)return route.fulfill({status:409,json:{success:false,history_version:1,code:'capture-changed',error:'Reported output capture changed',recovery:{action:'reload-outputs'}}});
        await route.continue();
    });
    await open(page,f.detail_id);await marker(page,f.runs.shared).click();
    const reload=dialog(page).getByRole('button',{name:'Reload reported outputs',exact:true});await expect(reload).toBeVisible();expect(outputReads).toBe(2);
    const runCount=runReads,galleryCount=galleryReads;await reload.click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    expect(runReads).toBe(runCount);expect(galleryReads).toBe(galleryCount);expect(outputReads).toBe(3);await expect(dialog(page)).toContainText(f.runs.shared);
});

test('history epoch recovery reports saved-parent read failures and retries without resetting the gallery',async({page})=>{
    const f=page.fixture;await open(page,f.detail_id);await showLinkedImage(page);await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(1);
    let parentFailure=true,galleryReads=0;page.on('request',request=>{if(new URL(request.url()).pathname===`/prompt_manager/library/${f.detail_id}/images`)galleryReads++;});
    await page.route(new RegExp(`/prompt_manager/library/${f.detail_id}(?:\\?|$)`),async route=>parentFailure?route.fulfill({status:503,json:{success:false,error:'Unavailable'}}):route.continue());
    await page.route(/\/history\/runs\/[^/]+\/outputs\?/,async route=>route.fulfill({status:409,json:{success:false,history_version:1,code:'history-changed',error:'History changed',recovery:{action:'refresh-history'}}}));
    await marker(page,f.runs.shared).click();await expect(dialog(page)).toContainText('Recorded evidence changed');await dialog(page).getByRole('button',{name:'Close recorded run',exact:true}).click();
    const retry=page.getByRole('button',{name:'Retry saved Library details',exact:true});await expect(retry).toBeVisible();await expect(page.locator('.library-parent-status')).toContainText('could not be revalidated');
    parentFailure=false;await retry.click();await expect(page.locator('.library-parent-status')).toContainText('revalidated');await expect(retry).toBeHidden();
    await expect(recorded(page).getByRole('button',{name:'Reload recorded history',exact:true})).toBeVisible();expect(galleryReads).toBe(0);await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(1);
});

test('changed saved-owner identity is disclosed without silently relabeling or rebinding the existing gallery',async({page,request})=>{
    const f=page.fixture;await open(page,f.detail_id);await showLinkedImage(page);await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(1);
    const positive=saved(page).getByRole('region',{name:'Positive prompt',exact:true});const original=await exact(positive.locator('p.prompt-text'));
    const cards=gallery(page).locator('article[data-image-key]');await cards.first().evaluate(node=>{window.savedGalleryCard=node;});let galleryReads=0;
    page.on('request',request=>{if(new URL(request.url()).pathname===`/prompt_manager/library/${f.detail_id}/images`)galleryReads++;});
    const details=await(await request.get(`/prompt_manager/library/${f.detail_id}`)).json();
    await expect(await request.delete(`/prompt_manager/images/${f.detail_image_id}`)).toBeOK();
    for(const variant of details.variants) await expect(await request.delete(`/prompt_manager/pairs/${variant.id}`)).toBeOK();
    await expect(await request.put(`/prompt_manager/prompts/${f.detail_id}`,{data:{text:'Replaced saved owner'}})).toBeOK();
    await page.route(/\/history\/runs\/[^/]+\/outputs\?/,async route=>route.fulfill({status:409,json:{success:false,history_version:1,code:'history-changed',error:'History changed',recovery:{action:'refresh-history'}}}));
    await marker(page,f.runs.shared).click();await expect(dialog(page)).toContainText('Recorded evidence changed');await dialog(page).getByRole('button',{name:'Close recorded run',exact:true}).click();
    await expect(page.locator('.library-parent-status')).toContainText('changed');expect(await exact(positive.locator('p.prompt-text'))).toBe(original);
    const reload=page.getByRole('button',{name:'Reload saved Library view',exact:true});await expect(reload).toBeVisible();
    expect(await cards.first().evaluate(node=>node===window.savedGalleryCard)).toBe(true);expect(galleryReads).toBe(0);
    await reload.click();await expect(saved(page).getByRole('region',{name:'Positive prompt',exact:true})).toContainText('Replaced saved owner');
});

test('a bounded saved-parent prefix leaves previously loaded variants unverified without claiming deletion',async({page,request})=>{
    const f=page.fixture;const detail=await(await request.get(`/prompt_manager/library/${f.detail_id}`)).json();
    for(let index=0;index<41;index++)await expect(await request.post('/prompt_manager/pairs',{data:{positive_text:detail.positive.text,negative_text:`Bounded negative ${index}`}})).toBeOK();
    await open(page,f.detail_id);await expect(page.getByRole('button',{name:'Load more negative variants',exact:true})).toBeVisible();await page.getByRole('button',{name:'Load more negative variants',exact:true}).click();
    const choices=page.getByRole('button',{name:/^Select negative /});await expect(choices).toHaveCount(43);await choices.last().click();await expect(choices.last()).toHaveAttribute('aria-pressed','true');
    const negative=await saved(page).getByRole('region',{name:'Negative prompt',exact:true}).textContent();
    await page.route(/\/history\/runs\/[^/]+\/outputs\?/,async route=>route.fulfill({status:409,json:{success:false,history_version:1,code:'history-changed',error:'History changed',recovery:{action:'refresh-history'}}}));
    await marker(page,f.runs.shared).click();await expect(dialog(page)).toContainText('Recorded evidence changed');await page.keyboard.press('Escape');
    await expect(page.locator('.library-parent-status')).toContainText('not observed in this bounded revalidation page');await expect(page.locator('.library-parent-status')).not.toContainText(/deleted|removed/i);
    await expect(choices.last()).toHaveAttribute('aria-pressed','true');expect(await saved(page).getByRole('region',{name:'Negative prompt',exact:true}).textContent()).toBe(negative);
    await expect(page.getByRole('button',{name:'Reload saved Library view',exact:true})).toBeVisible();await expect(choices.last()).toBeDisabled();
});

test('an authoritative save after a stale revalidation makes the saved view current again',async({page,request})=>{
    const f=page.fixture;const detail=await(await request.get(`/prompt_manager/library/${f.detail_id}`)).json();
    for(let index=0;index<41;index++)await expect(await request.post('/prompt_manager/pairs',{data:{positive_text:detail.positive.text,negative_text:`Bounded negative ${index}`}})).toBeOK();
    await open(page,f.detail_id);await page.getByRole('button',{name:'Load more negative variants',exact:true}).click();
    const choices=page.getByRole('button',{name:/^Select negative /});await expect(choices).toHaveCount(43);await choices.last().click();
    const negative=await saved(page).getByRole('region',{name:'Negative prompt',exact:true}).textContent();
    await page.route(/\/history\/runs\/[^/]+\/outputs\?/,async route=>route.fulfill({status:409,json:{success:false,history_version:1,code:'history-changed',error:'History changed',recovery:{action:'refresh-history'}}}));
    await marker(page,f.runs.shared).click();await expect(dialog(page)).toContainText('Recorded evidence changed');await page.keyboard.press('Escape');
    await expect(page.getByRole('button',{name:'Reload saved Library view',exact:true})).toBeVisible();await expect(choices.first()).toBeDisabled();
    await page.getByRole('button',{name:'Edit metadata',exact:true}).click();const editor=page.locator('dialog.library-editor');
    await expect(editor.getByRole('button',{name:'Save',exact:true})).toBeEnabled();await editor.getByLabel('Notes',{exact:true}).fill('Saved after stale revalidation');
    await editor.getByRole('button',{name:'Save',exact:true}).click();await expect(editor).toHaveCount(0);
    await expect(page.locator('.library-parent-status')).toBeHidden();await expect(page.getByRole('button',{name:'Reload saved Library view',exact:true})).toBeHidden();
    await expect(choices.first()).toBeEnabled();await expect(page.locator('.variant-dot[aria-pressed="true"]')).toHaveCount(1);
    expect(await saved(page).getByRole('region',{name:'Negative prompt',exact:true}).textContent()).toBe(negative);
    const names=await choices.evaluateAll(nodes=>nodes.map(node=>node.getAttribute('aria-label')));expect(new Set(names).size).toBe(names.length);
});

async function retainSavedSelection(page,request,fixture) {
    await open(page,fixture.detail_id);
    const details=await(await request.get(`/prompt_manager/library/${fixture.detail_id}`)).json();
    const name=details.variants.find(variant=>variant.id===fixture.saved_pair).title;
    await page.getByRole('button',{name:`Select negative ${name}`,exact:true}).click();
    await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(1);
    const negative=await exact(saved(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('p.prompt-text'));
    await gallery(page).locator('article[data-image-key]').first().evaluate(node=>{window.fix1SavedGalleryCard=node;});
    let reads=0;page.on('request',request=>{if(new URL(request.url()).pathname===`/prompt_manager/library/${fixture.detail_id}/images`)reads++;});
    return async()=>{
        await expect(page.getByRole('button',{name:`Select negative ${name}`,exact:true})).toHaveAttribute('aria-pressed','true');
        expect(await exact(saved(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('p.prompt-text'))).toBe(negative);
        expect(await gallery(page).locator('article[data-image-key]').first().evaluate(node=>node===window.fix1SavedGalleryCard)).toBe(true);expect(reads).toBe(0);
    };
}

for(const change of ['mode','anchor']) test(`initial receipt read restarts after a held ${change} cancellation`,async({page,request})=>{
    const f=page.fixture;const retained=await retainSavedSelection(page,request,f);let reads=0,release,entered;
    const held=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{entered=resolve;});
    let aborted=0;page.on('requestfailed',request=>{if(new URL(request.url()).pathname===`/prompt_manager/history/runs/${f.runs.shared}/outputs`)aborted++;});
    await page.route(new RegExp(`/history/runs/${f.runs.shared}/outputs\\?`),async route=>{reads++;if(reads===1){entered();await held;}await route.continue().catch(()=>{});});
    try {
        await marker(page,f.runs.shared).click();await started;
        if(change==='mode')await dialog(page).getByRole('button',{name:'Effective',exact:true}).click();
        else await dialog(page).getByRole('group',{name:'Recorded anchors',exact:true}).getByRole('button',{name:/^Anchor z /}).click();
        release();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);expect(reads).toBe(2);
        await expect.poll(()=>aborted).toBeGreaterThan(0);
        if(change==='mode')await expect(dialog(page).getByRole('button',{name:'Effective',exact:true})).toHaveAttribute('aria-pressed','true');
        else await expect(dialog(page).getByRole('group',{name:'Recorded anchors',exact:true}).getByRole('button',{name:/^Anchor z /})).toHaveAttribute('aria-pressed','true');
        await page.keyboard.press('Escape');await retained();
    } finally {release();}
});

test('same-run reopen loads page one after cancellation and a held fresh detail read',async({page,request})=>{
    const f=page.fixture,retained=await retainSavedSelection(page,request,f);let reads=0,releaseOutputs,enteredOutputs,releaseDetail,enteredDetail;
    const outputsHeld=new Promise(resolve=>{releaseOutputs=resolve;}),outputsStarted=new Promise(resolve=>{enteredOutputs=resolve;});
    const detailHeld=new Promise(resolve=>{releaseDetail=resolve;}),detailStarted=new Promise(resolve=>{enteredDetail=resolve;});
    await page.route(new RegExp(`/history/runs/${f.runs.shared}/outputs\\?`),async route=>{reads++;enteredOutputs();await outputsHeld;await route.continue().catch(()=>{});});
    try {
        await marker(page,f.runs.shared).click();await outputsStarted;await dialog(page).getByRole('button',{name:'Effective',exact:true}).click();await dialog(page).getByRole('button',{name:'Close recorded run',exact:true}).click();
        await page.route(new RegExp(`/history/runs/${f.runs.shared}\\?`),async route=>{enteredDetail();await detailHeld;await route.continue().catch(()=>{});});
        await marker(page,f.runs.shared).click();await detailStarted;await expect(dialog(page).locator('[data-receipt]')).toHaveCount(0);await expect(dialog(page)).toContainText('Loading recorded run');
        releaseDetail();releaseOutputs();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);expect(reads).toBe(3);
        await expect(dialog(page).getByRole('button',{name:'Effective',exact:true})).toHaveAttribute('aria-pressed','true');await page.keyboard.press('Escape');await retained();
    } finally {releaseDetail();releaseOutputs();}
});

test('late output capture replaces the earlier uncaptured detail summary with real complete-zero evidence',async({page,request})=>{
    const f=page.fixture,retained=await retainSavedSelection(page,request,f);let release,entered;
    const held=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{entered=resolve;});
    for(const data of [{}, {run_id:f.runs.equal_looking,extra:true}, {run_id:[]}, {run_id:'not-a-run'}])expect((await request.post('/__test__/history/capture-zero-outputs',{data})).status()).toBe(400);
    await page.route(new RegExp(`/history/runs/${f.runs.equal_looking}/outputs\\?`),async route=>{entered();await held;await route.continue().catch(()=>{});});
    const detailRead=page.waitForResponse(response=>new URL(response.url()).pathname===`/prompt_manager/history/runs/${f.runs.equal_looking}`);
    try {
        await marker(page,f.runs.equal_looking).click();await started;const detail=await(await detailRead).json();expect(detail.output_summary.state).toBe('not_captured');
        const write=await request.post('/__test__/history/capture-zero-outputs',{data:{run_id:f.runs.equal_looking}});await expect(write).toBeOK();expect((await write.json()).success).toBe(true);
        const pageRead=page.waitForResponse(response=>new URL(response.url()).pathname===`/prompt_manager/history/runs/${f.runs.equal_looking}/outputs`);release();const outputs=await(await pageRead).json();
        expect(outputs.capture).toMatchObject({state:'complete',reason:'none_reported'});expect(outputs.retained_occurrence_count).toBe(0);expect(outputs.list_status).toBe('complete');
        await expect(dialog(page).locator('.recorded-output-status')).toHaveText('0 reported outputs · complete · All retained receipts loaded.');
        await expect(dialog(page).getByText('Output capture',{exact:true}).locator('..').locator('dd')).toHaveText('complete · none_reported');
        await expect(dialog(page).getByText('Context capture',{exact:true}).locator('..').locator('dd')).toHaveText('complete · captured');
        await page.keyboard.press('Escape');await retained();
    } finally {release();}
});

test('capture-only recovery uses the changed real output capture without re-reading run or saved gallery',async({page,request})=>{
    const f=page.fixture,retained=await retainSavedSelection(page,request,f);let outputsReads=0,runReads=0;
    page.on('request',request=>{if(new URL(request.url()).pathname===`/prompt_manager/history/runs/${f.runs.whitespace}`)runReads++;});
    await page.route(new RegExp(`/history/runs/${f.runs.whitespace}/outputs\\?`),async route=>{
        outputsReads++;
        if(outputsReads===1) {
            const write=await request.post('/__test__/history/capture-zero-outputs',{data:{run_id:f.runs.whitespace}});await expect(write).toBeOK();expect((await write.json()).success).toBe(true);
            return route.fulfill({status:409,json:{success:false,history_version:1,code:'capture-changed',error:'Reported output capture changed',recovery:{action:'reload-outputs'}}});
        }
        await route.continue();
    });
    const detailRead=page.waitForResponse(response=>new URL(response.url()).pathname===`/prompt_manager/history/runs/${f.runs.whitespace}`);
    const outputsRead=page.waitForResponse(response=>new URL(response.url()).pathname===`/prompt_manager/history/runs/${f.runs.whitespace}/outputs`&&response.status()===200);
    await marker(page,f.runs.whitespace).click();const detail=await(await detailRead).json();expect(detail.output_summary.state).toBe('partial');
    const outputs=await(await outputsRead).json();expect(outputs.capture.state).toBe('conflicting');expect(outputs.retained_occurrence_count).toBe(1);expect(outputs.list_status).toBe('incomplete');
    await expect(dialog(page).locator('[data-receipt]')).toHaveCount(1);await expect(dialog(page).locator('.recorded-output-status')).toContainText('1 retained reported output · incomplete · conflicting');
    await expect(dialog(page).getByText('Output capture',{exact:true}).locator('..').locator('dd')).toHaveText('conflicting · conflicting');expect(runReads).toBe(1);expect(outputsReads).toBe(2);
    expect(await exact(dialog(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('p.prompt-text'))).toBe('  \n\t ');await page.keyboard.press('Escape');await retained();
});

test('completed page one stays bounded across mode changes until explicit output paging',async({page,request})=>{
    const f=page.fixture,retained=await retainSavedSelection(page,request,f);let reads=0,releaseNext,enteredNext;
    const held=new Promise(resolve=>{releaseNext=resolve;}),started=new Promise(resolve=>{enteredNext=resolve;});
    await page.route(new RegExp(`/history/runs/${f.runs.paged}/outputs\\?`),async route=>{
        reads++;if(new URL(route.request().url()).searchParams.has('cursor')){enteredNext();await held;}await route.continue().catch(()=>{});
    });
    try {
        await marker(page,f.runs.paged).click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(40);expect(reads).toBe(1);
        await dialog(page).getByRole('button',{name:'Effective',exact:true}).click();await dialog(page).getByRole('button',{name:'Raw',exact:true}).click();
        const more=dialog(page).getByRole('button',{name:'Load more reported outputs',exact:true});await expect(more).toBeEnabled();expect(reads).toBe(1);await expect(dialog(page).locator('[data-receipt]')).toHaveCount(40);
        await more.click();await started;expect(reads).toBe(2);releaseNext();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(41);await expect(more).toBeHidden();
        await page.keyboard.press('Escape');await retained();
    } finally {releaseNext();}
});

// #249 PR F1: the concept heading (app.js a591–597) and summary surface (a598) with real counts (D24).
const historyHeading=page=>page.locator('#workspace-main > .heading');
const summary=page=>page.locator('#workspace-main .library-history article.surface');
test('history heading actions stay on one row at 1600 and are enabled after load',async({page,request})=>{
    await page.setViewportSize({width:1600,height:1100});const f=page.fixture;
    let releaseDetail;const held=new Promise(resolve=>{releaseDetail=resolve;});
    await page.route(new RegExp(`/prompt_manager/library/${f.positive_id}\\?`),async route=>{await held;await route.continue().catch(()=>{});});
    try {
        await page.goto(`/prompt_manager/workspace#/library/${f.positive_id}`);
        const heading=historyHeading(page),send=heading.locator('.heading-actions > button.send');
        await expect(heading.locator('p.muted')).toHaveText('Prompt history · one positive, one shared gallery');
        // D24: disabled only while the selected pair's roles are loading.
        await expect(send).toBeDisabled();releaseDetail();
        await expect(recorded(page)).toBeVisible();await expect(send).toBeEnabled();
    } finally {releaseDetail();}
    const heading=historyHeading(page),title=await heading.locator('h1').textContent();
    const actions=heading.locator('.heading-actions > button');
    await expect(actions).toHaveCount(3);
    expect(await actions.evaluateAll(nodes=>nodes.map(node=>node.textContent.trim()))).toEqual(['← Prompt library','Edit metadata','Send to ComfyUI']);
    await expect(actions.nth(2)).toHaveClass('send');
    await expect(actions.nth(2)).toHaveAccessibleName(`Send prompt pair to ComfyUI: ${title}`);
    for(const action of [actions.nth(0),actions.nth(1)]) await expect(action).toBeEnabled();
    const boxes=await actions.evaluateAll(nodes=>nodes.map(node=>{const box=node.getBoundingClientRect();return {y:box.y,height:box.height};}));
    expect(new Set(boxes.map(box=>box.y)).size).toBe(1);
    const row=await heading.locator('.heading-actions').evaluate(node=>node.getBoundingClientRect().height);
    expect(row).toBe(boxes[0].height);
});
test('summary meta uses recorded runs',async({page,request})=>{
    const f=page.fixture;await open(page,f.positive_id);
    const detail=await(await request.get(`/prompt_manager/library/${f.positive_id}`)).json(),runs=(await historyRead(request,f.positive_id)).recorded_run_count;
    const surface=summary(page),title=await historyHeading(page).locator('h1').textContent();
    await expect(surface).toHaveCount(1);
    await expect(surface.locator(':scope > span.chip')).toHaveText(detail.positive.category || 'Uncategorized');
    const plural=(count,word)=>`${count} ${count===1?word:`${word}s`}`;
    await expect(surface.locator(':scope > .card-meta > span')).toHaveText([plural(runs,'tracked run'),plural(detail.positive.image_count,'image'),plural(detail.positive.variant_count,'negative variant')]);
    // The concept order: category chip, positive block, selected negative block, then the meta line.
    expect(await surface.evaluate(node=>[...node.querySelectorAll('.chip, .prompt-section, .card-meta')].filter(item=>item.checkVisibility()).map(item=>item.className))).toEqual(['chip','prompt-section positive','prompt-section negative','card-meta']);
    for(const name of ['Copy positive prompt','Send positive prompt to ComfyUI','Copy negative prompt','Send negative prompt to ComfyUI'])
        await expect(saved(page).getByRole('button',{name:`${name}: ${title}`,exact:true})).toBeVisible();
    await expect(surface.locator('.prompt-section').first()).toHaveCSS('border-top-width','1px');
});

// #249 F2: the concept's negative variants panel (app.js a598, style.css 1059-1154) and its two dialogs (D26).
const variantsPanel=page=>page.getByRole('region',{name:'Saved negative variants',exact:true});
const modalDialog=(page,name)=>page.getByRole('dialog',{name,exact:true});
test('variants panel matches the concept',async({page})=>{
    const f=page.fixture;await open(page,f.canonical_id);const panel=variantsPanel(page);
    await expect(panel).toHaveClass('surface timeline-panel');
    const heading=panel.locator(':scope > .panel-heading');
    await expect(heading.locator(':scope > div > h2')).toHaveText('Negative variants');
    await expect(heading.locator(':scope > div > p.small')).toHaveText('Choose a red dot to change the negative and filter this gallery.');
    const actions=heading.locator(':scope > .row-actions > button');
    expect(await actions.evaluateAll(nodes=>nodes.map(node=>node.textContent))).toEqual(['Compare negatives','+ Add negative','Show archived (0)']);
    const compare=actions.nth(0);await expect(compare).toHaveAttribute('aria-pressed','false');
    await compare.click();await expect(compare).toHaveAttribute('aria-pressed','true');await compare.click();await expect(compare).toHaveAttribute('aria-pressed','false');
    const timeline=panel.locator(':scope > .timeline');
    await expect(timeline.locator(':scope > .positive-lane > span')).toHaveText('Positive prompt · unchanged');
    const lanes=timeline.locator(':scope > .variant-lane');await expect(lanes).toHaveCount(3);
    // Newest saved pair first, as the bounded variant page reads; every lane is offset 38 + 47i.
    const names=['No negative','Clean geometry','Quality baseline'],runs=['0 runs','2 runs','2 runs'];
    for(const [index,name] of names.entries()) {
        const lane=lanes.nth(index);
        await expect(lane).toHaveAttribute('style',`--lane-offset: ${38+index*47}px;`);
        const dot=lane.locator(':scope > button.variant-dot');
        await expect(dot).toHaveText('−');await expect(dot).toHaveAccessibleName(`Select negative ${name}`);
        await expect(dot).toHaveAttribute('aria-pressed',String(index===0));
        await expect(lane.locator(':scope > span')).toHaveText(`${name} · ${runs[index]}`);
        expect(await lane.evaluate(node=>[...node.children].filter(child=>!child.hidden).map(child=>child.tagName.toLowerCase()+'.'+child.className))).toEqual(index===0?['button.variant-dot','span.']:['button.variant-dot','span.','button.run-dot']);
    }
    await lanes.nth(2).locator('.variant-dot').click();await expect(lanes.nth(2).locator('.variant-dot')).toHaveAttribute('aria-pressed','true');
    await expect(lanes.nth(0).locator('.variant-dot')).toHaveAttribute('aria-pressed','false');
    await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(2);
    const lower=panel.locator(':scope > .timeline-lower');
    await expect(lower.locator(':scope > span.small')).toHaveText('Green + positive · red − negative · small dot repeat run');
    await expect(lower.locator(':scope > button')).toHaveText('All images for this positive');
    await expect(lower.locator(':scope > button')).not.toHaveAttribute('aria-pressed',/.*/);
    await expect(panel).not.toContainText('Saved combinations, not an execution timeline.');
    // The concept tokens reach the lanes (style.css 1096-1137).
    await expect(lanes.first()).toHaveCSS('height','57px');await expect(lanes.nth(1)).toHaveCSS('margin-left','85px');
    await expect(lanes.first().locator('.variant-dot')).toHaveCSS('border-radius','50%');
    await expect(lanes.nth(1).locator('.run-dot')).toHaveCSS('left','190px');
});
test('run dot appears only for pairs with more than one run and opens its run',async({page})=>{
    const f=page.fixture;await open(page,f.canonical_id);const panel=variantsPanel(page);
    await expect(panel.getByRole('button',{name:/^Show repeat run for /})).toHaveCount(2);
    await expect(panel.getByRole('button',{name:'Show repeat run for No negative',exact:true})).toHaveCount(0);
    // D45: the lane text wraps before the run dot, and a coarse pointer reaches the 12px dot through a 44px target.
    for(const lane of await panel.locator('.variant-lane:has(> .run-dot:not([hidden]))').all()) {
        const [text,dot]=await Promise.all([lane.locator(':scope > span').boundingBox(),lane.locator(':scope > .run-dot').boundingBox()]);
        expect(text.width).toBeLessThanOrEqual(139.5);expect(text.x+text.width).toBeLessThan(dot.x);
    }
    if(await page.evaluate(()=>matchMedia('(pointer: coarse)').matches)) {
        const dot=panel.getByRole('button',{name:'Show repeat run for Quality baseline',exact:true});await dot.scrollIntoViewIfNeeded();
        const reach=await dot.evaluate(node=>{
            const box=node.getBoundingClientRect(),x=box.x+box.width/2,y=box.y+box.height/2;
            return [[x-20,y],[x+20,y],[x,y-20],[x,y+20]].map(([px,py])=>{const hit=document.elementFromPoint(px,py);return hit?.closest('button')===node?'dot':`${hit?.tagName}.${hit?.className}`;});
        });
        expect(reach).toEqual(['dot','dot','dot','dot']);
    }
    // The baseline has a recorded execution: the dot opens Recorded run for it.
    const baseline=panel.getByRole('button',{name:'Show repeat run for Quality baseline',exact:true});await baseline.click();
    await expect(dialog(page)).toBeVisible();await expect(dialog(page)).toContainText(f.canonical_run);
    await dialog(page).getByRole('button',{name:'Close recorded run',exact:true}).click();await expect(dialog(page)).toHaveCount(0);
    // Clean geometry was counted but never recorded: Repeat run shows its newest linked image only.
    const clean=panel.getByRole('button',{name:'Show repeat run for Clean geometry',exact:true});
    const reads=[];page.on('request',request=>{const url=new URL(request.url());if(url.pathname===`/prompt_manager/library/${f.canonical_id}/images`)reads.push(url.searchParams);});
    await clean.click();const repeat=modalDialog(page,'Repeat run');await expect(repeat).toBeVisible();
    await expect(repeat.locator('#modal-body > p.small')).toHaveText('Same positive and negative, another execution.');
    await expect(repeat.locator('#modal-body > .spaced-15 article[data-image-key]')).toHaveCount(1);
    // D47: as in the concept (a1291-1304), the dialog title names the image; no collection heading or count repeats it.
    await expect(repeat.locator('.section-caption')).toHaveCount(0);
    await expect(repeat.getByRole('heading',{name:'Linked images',exact:true})).toHaveCount(0);
    await expect(repeat.getByText(/ loaded · /)).toHaveCount(0);
    expect(await repeat.evaluate(node=>[...node.querySelectorAll('h1, h2')].filter(item=>!item.closest('aside')).map(item=>item.textContent))).toEqual(['Repeat run']);
    expect(reads.at(-1).get('pair_id')).toBe(String(f.clean_pair));expect(reads.at(-1).get('limit')).toBe('1');
    await page.keyboard.press('Escape');await expect(repeat).toHaveCount(0);await expect(clean).toBeFocused();
});
test('all images dialog lists every image for the positive',async({page,request})=>{
    const f=page.fixture;await open(page,f.detail_id);
    const detail=await(await request.get(`/prompt_manager/library/${f.detail_id}`)).json();
    const all=variantsPanel(page).getByRole('button',{name:'All images for this positive',exact:true});
    // Opening on the first lane, the shared gallery shows that lane only.
    await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(0);
    await all.click();const listing=modalDialog(page,'All images for this positive');await expect(listing).toBeVisible();
    await expect(listing.locator('article[data-image-key]')).toHaveCount(detail.positive.image_count);
    await page.keyboard.press('Escape');await expect(listing).toHaveCount(0);await expect(all).toBeFocused();
    await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(0);
    // Leaving the route closes an open dialog and its gallery.
    await all.click();await expect(listing.locator('article[data-image-key]')).toHaveCount(detail.positive.image_count);
    await page.evaluate(()=>{location.hash='#/overview';});await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    await expect(page.locator('dialog[open]')).toHaveCount(0);await expect(page.locator('#modal')).toHaveCount(0);
});
test('compare toggles, follows the selected dot and stacks at 390',async({page})=>{
    const f=page.fixture;await open(page,f.canonical_id);const panel=variantsPanel(page);
    const compare=panel.getByRole('button',{name:'Compare negatives',exact:true});
    const section=page.locator('.library-history .timeline-panel + section.surface');
    await expect(section).toHaveCount(0);
    await compare.click();await expect(compare).toHaveAttribute('aria-pressed','true');
    // The concept section (app.js a599-616) follows the timeline panel; the baseline is the oldest active lane (D25).
    await expect(section).toHaveCount(1);
    expect(await section.evaluate(node=>[...node.children].map(child=>child.tagName.toLowerCase()+(child.className?'.'+child.className:'')))).toEqual(['h2','p','div.compare-grid','p.small','p.small']);
    await expect(section.locator(':scope > h2')).toHaveText('Compare negative variants');
    await expect(section.locator(':scope > p').first()).toHaveText('Baseline compared with No negative. Choose another red dot to change the comparison.');
    const items=section.locator(':scope > .compare-grid > .compare-item');await expect(items).toHaveCount(2);
    await expect(items.nth(0).locator('.prompt-section.negative .prompt-text')).toHaveText('blurred edges, watermark');
    await expect(items.nth(1).locator('.prompt-section.negative .prompt-text')).toHaveText('No negative prompt (explicitly empty)');
    await expect(items.nth(0).locator('.prompt-section.positive')).toHaveCount(0);
    // D46: the two blocks are named apart, so no region or action name repeats on the page.
    await expect(section.getByRole('region',{name:'Baseline negative prompt',exact:true})).toBeVisible();
    await expect(section.getByRole('region',{name:'Selected negative prompt',exact:true})).toBeVisible();
    await expect(section.locator(':scope > p.small').first()).toHaveText('Added terms');
    await expect(section.locator(':scope > p.small').nth(1)).toHaveText('No added terms.');
    await expect(section.locator('.diff-added')).toHaveCount(0);
    // Another dot updates the same section in place.
    const node=await section.elementHandle();
    await panel.getByRole('button',{name:'Select negative Clean geometry',exact:true}).click();
    await expect(section.locator(':scope > p').first()).toHaveText('Baseline compared with Clean geometry. Choose another red dot to change the comparison.');
    await expect(items.nth(1).locator('.prompt-text')).toHaveText('flat light');
    await expect(section.locator(':scope > span.diff-added')).toHaveText(['+ flat light']);
    await expect(section.locator(':scope > p.small')).toHaveCount(1);
    expect(await node.evaluate(element=>element.isConnected)).toBe(true);
    await expect(section.locator('.diff-added')).toHaveCSS('display','inline-block');
    await expect(section.locator('.compare-item').first()).toHaveCSS('padding','15px');
    await page.setViewportSize({width:1600,height:1100});
    let boxes=await items.evaluateAll(nodes=>nodes.map(node=>node.getBoundingClientRect().toJSON()));
    expect(boxes[1].x).toBeGreaterThan(boxes[0].x+boxes[0].width-1);expect(Math.abs(boxes[1].y-boxes[0].y)).toBeLessThan(1);
    await page.setViewportSize({width:390,height:844});
    boxes=await items.evaluateAll(nodes=>nodes.map(node=>node.getBoundingClientRect().toJSON()));
    expect(Math.abs(boxes[1].x-boxes[0].x)).toBeLessThan(1);expect(boxes[1].y).toBeGreaterThanOrEqual(boxes[0].y+boxes[0].height);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
    await panel.getByRole('button',{name:'Select negative Quality baseline',exact:true}).click();
    await expect(section.locator(':scope > p').first()).toHaveText('Baseline compared with Quality baseline. Choose another red dot to change the comparison.');
    await expect(section.locator(':scope > p.small').nth(1)).toHaveText('No added terms.');
    await compare.click();await expect(compare).toHaveAttribute('aria-pressed','false');await expect(section).toHaveCount(0);
});
test('compare waits for the page holding the oldest active negative',async({page})=>{
    const f=page.fixture;
    // A bounded first page that does not reach the oldest saved pair: the baseline is not guessed from the newest lanes (D46).
    await page.route(new RegExp(`/prompt_manager/library/${f.canonical_id}\\?`),async route=>{
        const url=new URL(route.request().url());if(url.searchParams.has('variant_cursor') || url.searchParams.get('archive')) return route.continue();
        const response=await route.fetch(),body=await response.json();
        await route.fulfill({response,json:{...body,variants:body.variants.slice(0,2),variants_has_more:true,variants_next_cursor:'held'}});
    });
    await open(page,f.canonical_id);const panel=variantsPanel(page);
    await panel.getByRole('button',{name:'Compare negatives',exact:true}).click();
    const section=page.locator('.library-history .timeline-panel + section.surface');
    await expect(section.locator(':scope > h2')).toHaveText('Compare negative variants');
    await expect(section.locator(':scope > p.small')).toHaveText('Load more negative variants to reach the baseline.');
    await expect(section.locator('.compare-grid')).toHaveCount(0);
    await page.unroute(new RegExp(`/prompt_manager/library/${f.canonical_id}\\?`));
});
test('run dot falls back to Repeat run when the lookup fails or Recorded run cannot open',async({page})=>{
    const f=page.fixture;
    await page.route(/\/history\/pairs\/\d+\/newest/,route=>route.fulfill({status:503,json:{success:false}}));
    await open(page,f.canonical_id);const panel=variantsPanel(page);
    const baseline=panel.getByRole('button',{name:'Show repeat run for Quality baseline',exact:true});
    await baseline.click();const repeat=modalDialog(page,'Repeat run');await expect(repeat).toBeVisible();
    await expect(page.locator('#workspace-toast')).toHaveText('Recorded runs could not be read. Showing the newest linked image.');
    await expect(dialog(page)).toHaveCount(0);
    await page.keyboard.press('Escape');await expect(repeat).toHaveCount(0);await expect(baseline).toBeFocused();
    await page.unroute(/\/history\/pairs\/\d+\/newest/);
    // The lookup names a run, but the recorded history never loaded, so Recorded run cannot open: Repeat run, no toast.
    await page.route(/\/library\/\d+\/history\?/,route=>route.fulfill({status:503,json:{success:false}}));
    await page.reload();await expect(recorded(page).getByRole('button',{name:'Retry recorded history',exact:true})).toBeVisible();
    await page.locator('#workspace-toast').evaluate(node=>{node.textContent='';});
    const newest=page.waitForResponse(response=>/\/history\/pairs\/\d+\/newest/.test(response.url()));
    await variantsPanel(page).getByRole('button',{name:'Show repeat run for Quality baseline',exact:true}).click();
    expect((await(await newest).json()).run.run_id).toBe(f.canonical_run);
    await expect(modalDialog(page,'Repeat run')).toBeVisible();await expect(dialog(page)).toHaveCount(0);
    await expect(page.locator('#workspace-toast')).toHaveText('');
});
test('an archived pair\'s Repeat run reads the archived gallery',async({page,request})=>{
    const f=page.fixture;
    await expect(await request.patch(`/prompt_manager/pairs/${f.clean_pair}`,{data:{archived:true}})).toBeOK();
    await open(page,f.canonical_id);const panel=variantsPanel(page);
    await panel.getByRole('button',{name:'Show archived (1)',exact:true}).click();
    const reads=[];page.on('request',request=>{const url=new URL(request.url());if(url.pathname===`/prompt_manager/library/${f.canonical_id}/images`)reads.push(url.searchParams);});
    await panel.getByRole('button',{name:'Show repeat run for Clean geometry',exact:true}).click();
    const repeat=modalDialog(page,'Repeat run');await expect(repeat.locator('.spaced-15 article[data-image-key]')).toHaveCount(1);
    expect(reads.at(-1).get('pair_id')).toBe(String(f.clean_pair));expect(reads.at(-1).get('archive')).toBe('archived');
    // Each open replaces the previous dialog and its gallery.
    await page.keyboard.press('Escape');await panel.getByRole('button',{name:'All images for this positive',exact:true}).click();
    await expect(modalDialog(page,'All images for this positive')).toBeVisible();await expect(page.locator('dialog[open]')).toHaveCount(1);
});
test('Compare negatives is disabled without an active variant',async({page,request})=>{
    const f=page.fixture,detail=await(await request.get(`/prompt_manager/library/${f.canonical_id}`)).json();
    for(const variant of detail.variants) await expect(await request.patch(`/prompt_manager/pairs/${variant.id}`,{data:{archived:true}})).toBeOK();
    await open(page,f.canonical_id);const compare=variantsPanel(page).getByRole('button',{name:'Compare negatives',exact:true});
    await expect(compare).toBeDisabled();await expect(compare).toHaveAttribute('aria-pressed','false');
    await expect(page.locator('.library-history .timeline-panel + section.surface')).toHaveCount(0);
});
test('dot names never repeat for trimmed titles or titles that read like a fallback',async({page,request})=>{
    const f=page.fixture,detail=await(await request.get(`/prompt_manager/library/${f.canonical_id}`)).json();
    const byTitle=Object.fromEntries(detail.variants.map(variant=>[variant.title,variant.id]));
    await expect(await request.patch(`/prompt_manager/pairs/${byTitle['No negative']}`,{data:{title:'variant 2'}})).toBeOK();
    await expect(await request.patch(`/prompt_manager/pairs/${byTitle['Quality baseline']}`,{data:{title:' Clean geometry '}})).toBeOK();
    await open(page,f.canonical_id);
    const names=await variantsPanel(page).locator('.variant-dot').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('aria-label')));
    expect(new Set(names).size).toBe(names.length);
    expect(names).toEqual(['Select negative variant 1','Select negative variant 2','Select negative variant 3']);
});
// #249 F4: the concept's shared gallery (app.js a616-621, style.css 1546-1548) of PR C media cards (C3 activation).
test('shared gallery uses concept cards, image opens the viewer, empty copy',async({page,request})=>{
    const f=page.fixture;await open(page,f.canonical_id);
    const section=gallery(page),heading=section.locator(':scope > .panel-heading');
    expect(await section.evaluate(node=>`${node.tagName.toLowerCase()}.${node.className}`)).toBe('section.history-gallery');
    await expect(heading.locator(':scope > h2')).toHaveText('Shared gallery');
    // The page opens on the newest lane, a saved variant with no runs: the concept's empty copy.
    await expect(heading.locator(':scope > span.small')).toHaveText('No negative · same positive prompt');
    await expect(section.locator('.empty')).toHaveText('Saved variant · no runs yet.');await expect(section.locator('.empty')).toBeVisible();
    await expect(section.locator('article[data-image-key]')).toHaveCount(0);
    // D47 (maintainer ruling 2026-10-21): the concept's "Shared gallery" heading is the gallery's only heading, so the
    // collection's "Linked images" heading and its "N loaded · N linked images" count are gone; the layout row and the
    // selected-image side panel stay.
    await expect(section.locator('.section-caption')).toHaveCount(0);
    await expect(section.getByRole('heading',{name:'Linked images',exact:true})).toHaveCount(0);
    await expect(section.getByText(/ loaded · /)).toHaveCount(0);
    // Outside the side panel (whose own heading names the selected image), the gallery has one section heading.
    expect(await section.evaluate(node=>[...node.querySelectorAll('h1, h2')].filter(item=>!item.closest('aside')).map(item=>item.textContent))).toEqual(['Shared gallery']);
    await expect(section.getByRole('group',{name:'Image layout',exact:true})).toBeVisible();
    await expect(section.getByRole('combobox',{name:'Thumbnail size',exact:true})).toBeVisible();
    await expect(section.getByRole('complementary',{name:'Selected image details',exact:true})).toBeVisible();
    // As in the concept, the gallery follows the variants panel; recorded executions (#209) stay below it.
    const order=await page.locator('.library-history').evaluate(node=>[...node.querySelectorAll('section[aria-label]')].map(item=>item.getAttribute('aria-label')).filter(name=>['Saved negative variants','Shared gallery','Recorded executions'].includes(name)));
    expect(order).toEqual(['Saved negative variants','Shared gallery','Recorded executions']);
    const dot=variantsPanel(page).getByRole('button',{name:'Select negative Quality baseline',exact:true});await dot.click();await expect(dot).toHaveAttribute('aria-pressed','true');
    await expect(heading.locator(':scope > span.small')).toHaveText('Quality baseline · same positive prompt');
    const cards=section.locator('article.media-card[data-image-key]');await expect(cards).toHaveCount(2);
    await expect(section.locator('.empty')).toBeHidden();
    const details=await(await request.get(`/prompt_manager/library/${f.canonical_id}`)).json();
    const pair=details.variants.find(variant=>variant.title==='Quality baseline').id;
    const listing=await(await request.get(`/prompt_manager/library/${f.canonical_id}/images?pair_id=${pair}`)).json();
    const first=cards.first(),filename=(await first.locator('.image-open').getAttribute('aria-label')).replace(/^Open /,'');
    const image=listing.images.find(item=>item.filename===filename);expect(typeof image.favorite).toBe('boolean');
    // The concept card: art (open button + overlays), then the info row; the star where the read returns favorite and
    // no checkbox (the selection bar stays Images-only, #259).
    expect(await first.evaluate(node=>[...node.children].map(child=>child.className))).toEqual(['media-art','media-info']);
    expect(await first.locator(':scope > .media-art').evaluate(node=>[...node.children].map(child=>`${child.tagName.toLowerCase()}.${child.className}`))).toEqual(['button.image-open','div.image-overlays']);
    await expect(first.locator('.image-overlays input[type="checkbox"]')).toHaveCount(0);
    const star=first.locator('.image-overlays > button.star-button');
    await expect(star).toHaveAccessibleName(`Favorite ${filename}`);await expect(star).toHaveAttribute('aria-pressed',String(image.favorite));
    await expect(star).toHaveText(image.favorite?'★':'☆');
    await expect(first.locator('.media-info h3')).toHaveCount(1);
    await expect(first.locator('.image-card-actions > button.icon-btn:first-child')).toHaveAccessibleName(`Inspect ${filename}`);
    // C3 activation: the image opens the viewer and Escape returns focus to it.
    const openButton=first.locator('.image-open');await openButton.click();
    const viewer=page.locator('dialog#viewer-dialog');await expect(viewer).toBeVisible();
    await page.keyboard.press('Escape');await expect(viewer).toBeHidden();await expect(openButton).toBeFocused();
    await expect(section.locator(':scope > .panel-heading')).toHaveCSS('justify-content','space-between');
    await expect(section).toHaveCSS('margin-top','24px');
    // D47 (maintainer ruling 2026-10-21) covers the history page's dialogs too: the All images dialog is named by its
    // own title, with no "Linked images" heading or "N loaded · N linked images" count, as the concept's (a1282-1290).
    await variantsPanel(page).getByRole('button',{name:'All images for this positive',exact:true}).click();
    const all=page.getByRole('dialog',{name:'All images for this positive',exact:true});
    await expect(all.locator('article[data-image-key]').first()).toBeVisible();
    await expect(all.locator('.section-caption')).toHaveCount(0);
    await expect(all.getByRole('heading',{name:'Linked images',exact:true})).toHaveCount(0);
    await expect(all.getByText(/ loaded · /)).toHaveCount(0);
    expect(await all.evaluate(node=>[...node.querySelectorAll('h1, h2')].filter(item=>!item.closest('aside')).map(item=>item.textContent))).toEqual(['All images for this positive']);
    await page.keyboard.press('Escape');await expect(all).toHaveCount(0);
    // At 390px the heading stays inside the gallery and the page never scrolls sideways.
    await page.setViewportSize({width:390,height:844});
    await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
    const [box,text]=await Promise.all([section.boundingBox(),heading.locator(':scope > span.small').boundingBox()]);
    expect(text.x+text.width).toBeLessThanOrEqual(box.x+box.width+0.5);
});
