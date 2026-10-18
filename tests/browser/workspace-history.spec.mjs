import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';

test.use({locale:'en-US',timezoneId:'UTC',reducedMotion:'reduce'});
const recorded=page=>page.getByRole('region',{name:'Recorded executions',exact:true});
const dialog=page=>page.getByRole('dialog',{name:'Recorded run',exact:true});
const saved=page=>page.getByRole('region',{name:'Saved prompt texts',exact:true});
const gallery=page=>page.getByRole('region',{name:'Shared gallery',exact:true});
const open=async(page,id)=>{await page.goto(`/prompt_manager/workspace#/library/${id}`);await expect(recorded(page)).toBeVisible();};
const marker=(page,id,anchor='10')=>recorded(page).locator(`button[data-run-id="${id}"][data-anchor-id="${anchor}"]`);
const exact=async locator=>locator.evaluate(node=>node.textContent);

test.beforeEach(async({page,request,browserName})=>{
    await startCoverage(page,browserName);page.historyErrors=[];page.on('pageerror',error=>page.historyErrors.push(error.message));
    const reset=await request.post('/__test__/reset',{data:{chronological_history:true}});await expect(reset).toBeOK();page.fixture=await reset.json();
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.historyErrors).toEqual([]);});

const command=async(request,data)=>{const response=await request.post('/__test__/history/commands',{data});await expect(response).toBeOK();return response.json();};
const restoreSnapshot=async(request,bytes)=>{const response=await request.post('/prompt_manager/restore',{multipart:{database_file:{name:'history.db',mimeType:'application/octet-stream',buffer:bytes}}});await expect(response).toBeOK();expect((await response.json()).success).toBe(true);};
const historyRead=async(request,id)=>{const response=await request.get(`/prompt_manager/library/${id}/history`);await expect(response).toBeOK();return response.json();};
const frozenContexts=contexts=>contexts.map(({navigation,positive_prompt_id,negative_prompt_id,pair_id,...frozen})=>frozen);
const immutableReceipts=items=>items.map(({current_candidate,...receipt})=>receipt);

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
        await restoreSnapshot(request,bytes);
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
    await open(page,f.detail_id);await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(1);
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
    const f=page.fixture;await open(page,f.detail_id);const original=await exact(saved(page).getByRole('region',{name:'Positive prompt',exact:true}).locator('pre'));
    const source=await request.get('/__test__/history/reused-snapshot');await expect(source).toBeOK();await restoreSnapshot(request,await source.body());
    await marker(page,f.runs.shared).click();await expect(dialog(page)).toContainText('Recorded evidence changed');await page.keyboard.press('Escape');
    await expect(page.locator('.library-parent-status')).toContainText('changed');expect(await exact(saved(page).getByRole('region',{name:'Positive prompt',exact:true}).locator('pre'))).toBe(original);
    await expect(recorded(page).getByRole('button',{name:'Reload recorded history',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'Reload saved Library view',exact:true}).click();await expect(saved(page)).toContainText('Restored owner with reused numeric ID');
    expect((await request.get(`/prompt_manager/history/runs/${f.runs.shared}`)).status()).toBe(404);
});

for(const outcome of ['success','error'])test(`delayed actual old browser ${outcome} cannot repaint after restore and route return`,async({page,request})=>{
    const f=page.fixture;await open(page,f.detail_id);const snapshot=await request.get('/__test__/history/snapshot');const bytes=await snapshot.body();
    let entered,release;const started=new Promise(resolve=>{entered=resolve;}),held=new Promise(resolve=>{release=resolve;});
    await page.route(new RegExp(`/history/runs/${f.runs.shared}\\?`),async route=>{
        if(outcome==='error')await restoreSnapshot(request,bytes);
        const response=await route.fetch();expect(response.status()).toBe(outcome==='success'?200:409);entered();await held;
        await route.fulfill({response}).catch(()=>{});
    });
    try {
        await marker(page,f.runs.shared).click();await started;if(outcome==='success')await restoreSnapshot(request,bytes);
        await page.evaluate(()=>{location.hash='#/overview';});await expect(page.locator('#workspace-main h1')).toHaveText('Overview');release();
        await page.unroute(new RegExp(`/history/runs/${f.runs.shared}\\?`));await open(page,f.detail_id);
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
    const selected=detail.variants.findIndex(v=>v.negative_text==='A negative')+1;
    await page.getByRole('button',{name:`Select negative variant ${selected}`,exact:true}).click();
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
    await expect(page.getByRole('button',{name:`Select negative variant ${selected}`,exact:true})).toHaveAttribute('aria-pressed','true');
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
    expect(await exact(pos.locator('pre'))).toBe(chosen.positive.raw);expect(await exact(neg.locator('pre'))).toBe(chosen.negative.raw);
    expect(await pos.evaluate(node=>node.nextElementSibling?.getAttribute('aria-label'))).toBe('Negative prompt');
    await dialog(page).getByRole('button',{name:'Effective',exact:true}).click();expect(await exact(neg.locator('pre'))).toBe(chosen.negative.effective);
    await dialog(page).getByRole('button',{name:'Close recorded run',exact:true}).click();
    await marker(page,f.runs.unknown).click();await dialog(page).getByRole('button',{name:'Raw',exact:true}).click();
    const unknown=dialog(page).getByRole('region',{name:'Negative prompt',exact:true});await expect(unknown.getByRole('button')).toHaveCount(0);await expect(dialog(page).getByRole('button',{name:'Send prompt pair',exact:true})).toHaveCount(0);
    await dialog(page).getByRole('button',{name:'Effective',exact:true}).click();expect(await exact(unknown.locator('pre'))).toBe('  \n\t ');await expect(unknown).toContainText('host_cache_output');
    await dialog(page).getByRole('button',{name:'Close recorded run',exact:true}).click();
    await marker(page,f.runs.empty).click();await expect(dialog(page).getByRole('region',{name:'Negative prompt',exact:true})).toContainText('Empty negative prompt');
    await expect(dialog(page).getByRole('button',{name:'Copy negative prompt',exact:true})).toBeVisible();await expect(dialog(page).getByRole('button',{name:'Send prompt pair',exact:true})).toBeVisible();
    await dialog(page).getByRole('button',{name:'Close recorded run',exact:true}).click();
    await marker(page,f.runs.whitespace).click();await dialog(page).getByRole('button',{name:'Raw',exact:true}).click();expect(await exact(dialog(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('pre'))).toBe('  \n\t ');
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
    const viewer=page.getByRole('dialog',{name:'Image preview',exact:true});await expect(viewer).toBeVisible();
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
    await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(1);windowFailure=false;await recorded(page).getByRole('button',{name:'Retry recorded history',exact:true}).click();
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
    await page.getByRole('link',{name:'Back to library',exact:true}).evaluate(node=>node.click());await expect(recorded(page)).toHaveCount(0);
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
        const positive=await exact(dialog(page).getByRole('region',{name:'Positive prompt',exact:true}).locator('pre'));
        const send=dialog(page).getByRole('button',{name:'Send prompt pair',exact:true});await send.click();
        const sender=page.getByRole('dialog',{name:'Send to ComfyUI',exact:true});await sender.getByRole('combobox',{name:'ComfyUI window',exact:true}).selectOption({label:'History ComfyUI'});
        await sender.getByRole('combobox',{name:'Destination node',exact:true}).selectOption({label:'History destination'});await sender.getByRole('button',{name:'Preview changes',exact:true}).click();
        await expect(sender.getByRole('button',{name:'Apply to ComfyUI',exact:true})).toBeEnabled();await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();await expect(sender).toContainText('Sent to ComfyUI.');
        expect(await host.evaluate(()=>window.historyApplied)).toEqual([{positive,negative:'  \n\t '}]);
        await page.keyboard.press('Escape');await expect(sender).toHaveCount(0);await expect(dialog(page)).toBeVisible();await expect(send).toBeFocused();
        await page.keyboard.press('Escape');await expect(dialog(page)).toHaveCount(0);
    } finally {await host.evaluate(()=>window.historyHost.dispose());await host.close();}
});

test('pagehide tears down dialogs and persisted reentry revalidates before enabling recorded actions',async({page})=>{
    await open(page,page.fixture.detail_id);await marker(page,page.fixture.runs.shared).click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    await dialog(page).locator('[data-receipt]').first().getByRole('button',{name:'View current file',exact:true}).click();await expect(page.getByRole('dialog',{name:'Image preview',exact:true})).toBeVisible();
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
        outputReads++;if(failures-->0)return route.fulfill({status:409,json:{success:false,history_version:1,code:'capture-changed',error:'Reported output capture changed',recovery:{action:'refresh-outputs'}}});
        await route.continue();
    });
    await open(page,f.detail_id);await marker(page,f.runs.shared).click();
    const reload=dialog(page).getByRole('button',{name:'Reload reported outputs',exact:true});await expect(reload).toBeVisible();expect(outputReads).toBe(2);
    const runCount=runReads,galleryCount=galleryReads;await reload.click();await expect(dialog(page).locator('[data-receipt]')).toHaveCount(5);
    expect(runReads).toBe(runCount);expect(galleryReads).toBe(galleryCount);expect(outputReads).toBe(3);await expect(dialog(page)).toContainText(f.runs.shared);
});

test('history epoch recovery reports saved-parent read failures and retries without resetting the gallery',async({page})=>{
    const f=page.fixture;await open(page,f.detail_id);await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(1);
    let parentFailure=true,galleryReads=0;page.on('request',request=>{if(new URL(request.url()).pathname===`/prompt_manager/library/${f.detail_id}/images`)galleryReads++;});
    await page.route(new RegExp(`/prompt_manager/library/${f.detail_id}(?:\\?|$)`),async route=>parentFailure?route.fulfill({status:503,json:{success:false,error:'Unavailable'}}):route.continue());
    await page.route(/\/history\/runs\/[^/]+\/outputs\?/,async route=>route.fulfill({status:409,json:{success:false,history_version:1,code:'history-changed',error:'History changed',recovery:{action:'reload-history'}}}));
    await marker(page,f.runs.shared).click();await expect(dialog(page)).toContainText('Recorded evidence changed');await dialog(page).getByRole('button',{name:'Close recorded run',exact:true}).click();
    const retry=page.getByRole('button',{name:'Retry saved Library details',exact:true});await expect(retry).toBeVisible();await expect(page.locator('.library-parent-status')).toContainText('could not be revalidated');
    parentFailure=false;await retry.click();await expect(page.locator('.library-parent-status')).toContainText('revalidated');await expect(retry).toBeHidden();
    await expect(recorded(page).getByRole('button',{name:'Reload recorded history',exact:true})).toBeVisible();expect(galleryReads).toBe(0);await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(1);
});

test('changed saved-owner identity is disclosed without silently relabeling or rebinding the existing gallery',async({page,request})=>{
    const f=page.fixture;await open(page,f.detail_id);await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(1);
    const positive=saved(page).getByRole('region',{name:'Positive prompt',exact:true});const original=await exact(positive.locator('pre'));
    const cards=gallery(page).locator('article[data-image-key]');await cards.first().evaluate(node=>{window.savedGalleryCard=node;});let galleryReads=0;
    page.on('request',request=>{if(new URL(request.url()).pathname===`/prompt_manager/library/${f.detail_id}/images`)galleryReads++;});
    const details=await(await request.get(`/prompt_manager/library/${f.detail_id}`)).json();
    await expect(await request.delete(`/prompt_manager/images/${f.detail_image_id}`)).toBeOK();
    for(const variant of details.variants) await expect(await request.delete(`/prompt_manager/pairs/${variant.id}`)).toBeOK();
    await expect(await request.put(`/prompt_manager/prompts/${f.detail_id}`,{data:{text:'Replaced saved owner'}})).toBeOK();
    await page.route(/\/history\/runs\/[^/]+\/outputs\?/,async route=>route.fulfill({status:409,json:{success:false,history_version:1,code:'history-changed',error:'History changed',recovery:{action:'reload-history'}}}));
    await marker(page,f.runs.shared).click();await expect(dialog(page)).toContainText('Recorded evidence changed');await dialog(page).getByRole('button',{name:'Close recorded run',exact:true}).click();
    await expect(page.locator('.library-parent-status')).toContainText('changed');expect(await exact(positive.locator('pre'))).toBe(original);
    const reload=page.getByRole('button',{name:'Reload saved Library view',exact:true});await expect(reload).toBeVisible();
    expect(await cards.first().evaluate(node=>node===window.savedGalleryCard)).toBe(true);expect(galleryReads).toBe(0);
    await reload.click();await expect(saved(page).getByRole('region',{name:'Positive prompt',exact:true})).toContainText('Replaced saved owner');
});

test('a bounded saved-parent prefix leaves previously loaded variants unverified without claiming deletion',async({page,request})=>{
    const f=page.fixture;const detail=await(await request.get(`/prompt_manager/library/${f.detail_id}`)).json();
    for(let index=0;index<41;index++)await expect(await request.post('/prompt_manager/pairs',{data:{positive_text:detail.positive.text,negative_text:`Bounded negative ${index}`}})).toBeOK();
    await open(page,f.detail_id);await expect(page.getByRole('button',{name:'Load more negative variants',exact:true})).toBeVisible();await page.getByRole('button',{name:'Load more negative variants',exact:true}).click();
    const choices=page.getByRole('button',{name:/^Select negative variant /});await expect(choices).toHaveCount(43);await choices.last().click();await expect(choices.last()).toHaveAttribute('aria-pressed','true');
    const negative=await saved(page).getByRole('region',{name:'Negative prompt',exact:true}).textContent();
    await page.route(/\/history\/runs\/[^/]+\/outputs\?/,async route=>route.fulfill({status:409,json:{success:false,history_version:1,code:'history-changed',error:'History changed',recovery:{action:'reload-history'}}}));
    await marker(page,f.runs.shared).click();await expect(dialog(page)).toContainText('Recorded evidence changed');await page.keyboard.press('Escape');
    await expect(page.locator('.library-parent-status')).toContainText('not observed in this bounded revalidation page');await expect(page.locator('.library-parent-status')).not.toContainText(/deleted|removed/i);
    await expect(choices.last()).toHaveAttribute('aria-pressed','true');expect(await saved(page).getByRole('region',{name:'Negative prompt',exact:true}).textContent()).toBe(negative);
    await expect(page.getByRole('button',{name:'Reload saved Library view',exact:true})).toBeVisible();await expect(choices.last()).toBeDisabled();
});

async function retainSavedSelection(page,request,fixture) {
    await open(page,fixture.detail_id);
    const details=await(await request.get(`/prompt_manager/library/${fixture.detail_id}`)).json();
    const index=details.variants.findIndex(variant=>variant.id===fixture.saved_pair);
    await page.getByRole('button',{name:`Select negative variant ${index+1}`,exact:true}).click();
    await expect(gallery(page).locator('article[data-image-key]')).toHaveCount(1);
    const negative=await exact(saved(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('pre'));
    await gallery(page).locator('article[data-image-key]').first().evaluate(node=>{window.fix1SavedGalleryCard=node;});
    let reads=0;page.on('request',request=>{if(new URL(request.url()).pathname===`/prompt_manager/library/${fixture.detail_id}/images`)reads++;});
    return async()=>{
        await expect(page.getByRole('button',{name:`Select negative variant ${index+1}`,exact:true})).toHaveAttribute('aria-pressed','true');
        expect(await exact(saved(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('pre'))).toBe(negative);
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
            return route.fulfill({status:409,json:{success:false,history_version:1,code:'capture-changed',error:'Reported output capture changed',recovery:{action:'refresh-outputs'}}});
        }
        await route.continue();
    });
    const detailRead=page.waitForResponse(response=>new URL(response.url()).pathname===`/prompt_manager/history/runs/${f.runs.whitespace}`);
    const outputsRead=page.waitForResponse(response=>new URL(response.url()).pathname===`/prompt_manager/history/runs/${f.runs.whitespace}/outputs`&&response.status()===200);
    await marker(page,f.runs.whitespace).click();const detail=await(await detailRead).json();expect(detail.output_summary.state).toBe('partial');
    const outputs=await(await outputsRead).json();expect(outputs.capture.state).toBe('conflicting');expect(outputs.retained_occurrence_count).toBe(1);expect(outputs.list_status).toBe('incomplete');
    await expect(dialog(page).locator('[data-receipt]')).toHaveCount(1);await expect(dialog(page).locator('.recorded-output-status')).toContainText('1 retained reported output · incomplete · conflicting');
    await expect(dialog(page).getByText('Output capture',{exact:true}).locator('..').locator('dd')).toHaveText('conflicting · conflicting');expect(runReads).toBe(1);expect(outputsReads).toBe(2);
    expect(await exact(dialog(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('pre'))).toBe('  \n\t ');await page.keyboard.press('Escape');await retained();
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
