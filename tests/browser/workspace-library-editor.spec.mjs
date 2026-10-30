import {writeFile} from 'node:fs/promises';
import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import {test,expect} from './fixtures.mjs';
import {armUnseenRestores,unseenRestore,followRestore,SEEN_RESTORE_PILL,SEEN_RESTORE_TOAST} from './unseen-restore.mjs';
import {captureCanonicalState} from './history-canonical-comparison.mjs';
import {inventoryOf,mapConcept,diffInventory} from './library-editor-canonical-comparison.mjs';

// Dialog-level contracts mount the editor through a page import; the user flows below use the real entry actions.
test.use({locale:'en-US',timezoneId:'UTC',reducedMotion:'reduce'});
const workspace='/prompt_manager/workspace';
const ACTIONS={new:['Cancel','Save'],metadata:['Create text revision','Archive variant…','Delete prompt…','Cancel','Save'],revision:['Cancel','Save'],negative:['Cancel','Save']};
// D153 (#447): no Rating field; the fixture's positive stays stored with rating 4 through every workspace save.
const LABELS=['Title','Positive prompt','Negative prompt (optional)','Category','Tags, separated by commas','Notes'];
const UNAPPLIED='Saved as a new negative variant of an existing prompt. The title, category, tags and notes you entered were not applied.';
const editorDialog=page=>page.locator('dialog.library-editor');

test.beforeEach(async({page,request,browserName})=>{
    await startCoverage(page,browserName);await armUnseenRestores(page);
    const reset=await request.post('/__test__/library-editor/reset',{data:{}});await expect(reset).toBeOK();page.fixture=await reset.json();
    page.editorErrors=[];page.on('pageerror',error=>page.editorErrors.push(error.message));
    page.writes=[];page.on('request',item=>{if(item.method()!=='GET')page.writes.push({method:item.method(),url:new URL(item.url()).pathname,body:item.postDataJSON()});});
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.editorErrors).toEqual([]);});

async function mountEditor(page,theme) {
    await page.goto(`${workspace}#/overview`);await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    if(theme)await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);
    await page.evaluate(async()=>{
        const trigger=document.createElement('button');trigger.type='button';trigger.id='editor-trigger';trigger.textContent='Open editor';document.body.append(trigger);
        window.editorEvents={committed:[],reconciled:[],deletePrompt:[]};
        try {
            const {createLibraryEditor,showWorkspaceToast}=await import('/prompt_manager/js/workspace/library-editor.mjs');
            const {createScope}=await import('/prompt_manager/js/shared/toolkit/core.js');
            const scope=createScope();
            const editor=createLibraryEditor({api:window.ApiClient.createApiClient(),parentScope:scope,
                onCommitted:commit=>window.editorEvents.committed.push(commit),onReconciled:result=>window.editorEvents.reconciled.push(result),
                // As the prompt's page wires it (#481); the review itself is covered in workspace-library-delete.spec.mjs.
                onDeletePrompt:request=>window.editorEvents.deletePrompt.push(request.positiveId)});
            window.editorHarness={scope,editor,showWorkspaceToast};
        } catch(error) {window.editorHarness={missing:String(error)};}
    });
}
async function openEditor(page,options) {
    await page.evaluate(options=>{const trigger=document.getElementById('editor-trigger');trigger.focus();window.editorHarness.editor?.open({...options,trigger});},options);
    const dialog=editorDialog(page);await expect(dialog).toBeVisible();return dialog;
}
const field=(dialog,label)=>dialog.getByLabel(label,{exact:true});
const button=(dialog,name)=>dialog.getByRole('button',{name,exact:true});
const note=dialog=>dialog.locator('.warning-note');
async function ready(dialog) {await expect(button(dialog,'Save')).toBeEnabled();}
async function actionNames(dialog) {return dialog.locator('.dialog-actions button').evaluateAll(nodes=>nodes.map(node=>node.textContent.trim()));}
const detail=async(request,id,archive='all')=>(await(await request.get(`/prompt_manager/library/${id}?archive=${archive}`)).json());
const pairRead=async(request,id)=>(await(await request.get(`/prompt_manager/pairs/${id}`)).json()).pair;

test('edit recipe metadata shows the concept fields with the selected saved negative read-only',async({page})=>{
    const f=page.fixture;await mountEditor(page);
    let dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});
    await expect(dialog.getByRole('heading',{name:'Edit recipe metadata',exact:true})).toBeVisible();
    expect(await dialog.evaluate(node=>node.matches(':modal'))).toBe(true);
    await expect(button(dialog,'Close dialog')).toBeFocused();await ready(dialog);
    expect(await dialog.locator('.field label').evaluateAll(nodes=>nodes.map(node=>node.textContent))).toEqual(LABELS);
    await expect(field(dialog,'Title')).toHaveValue(f.metadata.title);
    await expect(field(dialog,'Positive prompt')).toHaveValue(f.positive_text);
    await expect(field(dialog,'Negative prompt (optional)')).toHaveValue(f.selected_negative);
    for(const label of ['Positive prompt','Negative prompt (optional)'])await expect(field(dialog,label)).toHaveJSProperty('readOnly',true);
    expect(await dialog.locator('textarea[readonly]').evaluateAll(nodes=>nodes.map(node=>node.tagName))).toEqual(['TEXTAREA','TEXTAREA']);
    await expect(dialog.getByRole('button',{name:/copy|send/i})).toHaveCount(0);
    await expect(dialog.getByRole('region')).toHaveCount(0);
    await expect(field(dialog,'Category')).toHaveValue('Portraits');
    await expect(field(dialog,'Tags, separated by commas')).toHaveValue('studio, window light');
    await expect(field(dialog,'Notes')).toHaveValue(f.metadata.notes);
    await expect(dialog.getByLabel('Rating')).toHaveCount(0);await expect(dialog.locator('select, #recipe-rating')).toHaveCount(0);
    expect(await field(dialog,'Category').evaluate(node=>[...node.list.options].map(option=>option.value))).toEqual(['Portraits','Sketches']);
    expect(await field(dialog,'Tags, separated by commas').evaluate(node=>[...node.list.options].map(option=>option.value))).toEqual(['draft,','studio,','window light,']);
    await expect(dialog.locator('[placeholder]')).toHaveCount(0);
    await expect(dialog.locator('.demo-note')).toHaveCount(0);
    expect(await actionNames(dialog)).toEqual(ACTIONS.metadata);
    await expect(button(dialog,'Archive variant…')).toHaveClass(/\bdanger\b/);await expect(button(dialog,'Save')).toHaveClass(/\bca-primary\b/);
    await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
    await expect(page.locator('#editor-trigger')).toBeFocused();expect(page.writes).toHaveLength(0);
    // D161 (#481): Delete prompt… closes the editor first and hands the prompt to the page's delete review.
    dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await expect(button(dialog,'Delete prompt…')).toHaveClass(/\bdanger\b/);await button(dialog,'Delete prompt…').click();
    await expect(dialog).toHaveCount(0);
    expect(await page.evaluate(()=>window.editorEvents.deletePrompt)).toEqual([f.positive_id]);expect(page.writes).toHaveLength(0);
});

test('editor modes persist through reload with selected saved negative',async({page,request})=>{
    const f=page.fixture;await mountEditor(page);
    let dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await field(dialog,'Title').fill('Edited portrait');await field(dialog,'Category').fill('');
    await field(dialog,'Tags, separated by commas').fill('studio, moody');
    await field(dialog,'Notes').fill('Edited notes');await button(dialog,'Save').click();
    await expect(dialog).toHaveCount(0);
    expect(page.writes.map(write=>`${write.method} ${write.url}`)).toEqual([`PATCH /prompt_manager/library/${f.positive_id}`]);
    expect(page.writes[0].body.metadata).toEqual({title:'Edited portrait',category:'',tags:['studio','moody'],notes:'Edited notes'});
    await expect.poll(()=>page.evaluate(()=>window.editorEvents.reconciled.length)).toBe(1);
    const saved=(await detail(request,f.positive_id)).positive;
    // D153: the edit never sends a rating, so the stored 4 stays.
    expect(f.metadata.rating).toBe(4);expect(saved).toMatchObject({title:'Edited portrait',category:null,rating:4,notes:'Edited notes'});expect([...saved.tags].sort()).toEqual(['moody','studio']);

    await page.reload();await mountEditor(page);
    dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await expect(field(dialog,'Title')).toHaveValue('Edited portrait');await expect(field(dialog,'Category')).toHaveValue('');
    await expect(field(dialog,'Negative prompt (optional)')).toHaveValue(f.selected_negative);
    await button(dialog,'Create text revision').click();
    await expect(dialog.getByRole('heading',{name:'Create text revision',exact:true})).toBeVisible();
    await expect(editorDialog(page)).toHaveCount(1);expect(await dialog.evaluate(node=>node.open&&node.matches(':modal'))).toBe(true);
    await expect(button(dialog,'Close dialog')).toBeFocused();await ready(dialog);
    await expect(field(dialog,'Positive prompt')).toHaveJSProperty('readOnly',false);
    await expect(field(dialog,'Negative prompt (optional)')).toHaveValue(f.selected_negative);
    expect(await actionNames(dialog)).toEqual(ACTIONS.revision);
    await field(dialog,'Positive prompt').fill('Editor portrait, revised dusk light');await field(dialog,'Category').fill('');
    await button(dialog,'Save').click();await expect(dialog).toHaveCount(0);
    const create=page.writes.at(-1);expect(`${create.method} ${create.url}`).toBe('POST /prompt_manager/library');
    expect(create.body).toMatchObject({positive_text:'Editor portrait, revised dusk light',negative_text:f.selected_negative,
        parent_metadata:{title:'Edited portrait',category:'Uncategorized',notes:'Edited notes'}});
    expect(Object.hasOwn(create.body.parent_metadata,'rating'),'D153: a revision sends no rating').toBe(false);
    expect([...create.body.parent_metadata.tags].sort()).toEqual(['moody','studio']);
    await expect.poll(()=>page.evaluate(()=>window.editorEvents.reconciled.length)).toBe(1);
    const revision=await page.evaluate(()=>window.editorEvents.committed.at(-1).receipt);
    expect(revision).toMatchObject({positive_created:true,pair_created:true});expect(revision.positive.id).not.toBe(f.positive_id);
    // D153: the revision is a new, unrated positive; the revised prompt keeps its stored rating.
    expect((await detail(request,revision.positive.id)).positive).toMatchObject({title:'Edited portrait',category:'Uncategorized',rating:null});
    expect((await detail(request,f.positive_id)).positive.rating).toBe(4);

    dialog=await openEditor(page,{mode:'new'});await expect(dialog.getByRole('heading',{name:'New prompt',exact:true})).toBeVisible();await ready(dialog);
    expect(await actionNames(dialog)).toEqual(ACTIONS.new);
    for(const label of ['Title','Positive prompt'])await expect(field(dialog,label)).toHaveJSProperty('required',true);
    const before=page.writes.length;await button(dialog,'Save').click();
    await expect(field(dialog,'Title')).toBeFocused();expect(await field(dialog,'Title').evaluate(node=>node.validity.valueMissing)).toBe(true);
    expect(page.writes).toHaveLength(before);
    await field(dialog,'Title').fill('Fresh idea');await field(dialog,'Positive prompt').fill('A fresh lighthouse at dawn');
    await button(dialog,'Save').click();await expect(dialog).toHaveCount(0);
    expect(page.writes.at(-1).body).toEqual({expected_epoch:expect.any(String),positive_text:'A fresh lighthouse at dawn',negative_text:'',
        parent_metadata:{title:'Fresh idea',category:'Uncategorized',tags:[],notes:''}});

    dialog=await openEditor(page,{mode:'negative',positiveId:f.positive_id});
    await expect(dialog.getByRole('heading',{name:'Add a negative variant',exact:true})).toBeVisible();await ready(dialog);
    expect(await dialog.locator('.field label').evaluateAll(nodes=>nodes.map(node=>node.textContent))).toEqual(['Negative prompt']);
    await expect(dialog.locator('p.small')).toHaveText('Leave empty for an explicitly empty negative. The positive stays unchanged.');
    expect(await actionNames(dialog)).toEqual(ACTIONS.negative);
    await field(dialog,'Negative prompt').fill('grain, dust');await button(dialog,'Save').click();await expect(dialog).toHaveCount(0);
    expect(page.writes.at(-1)).toMatchObject({method:'POST',url:`/prompt_manager/library/${f.positive_id}/variants`,body:{negative_text:'grain, dust'}});
    const added=await page.evaluate(()=>window.editorEvents.committed.at(-1).receipt);
    expect(added).toMatchObject({positive_created:false,pair_created:true,pair:{negative_text:'grain, dust'}});
    await expect(page.locator('#editor-trigger')).toBeFocused();
    expect((await detail(request,f.positive_id)).positive.rating,'D153: adding a negative keeps the stored rating').toBe(4);
});

test('archive confirms the selected pair with fresh counts and writes only on confirm',async({page,request})=>{
    const f=page.fixture;await mountEditor(page);
    let dialog=await openEditor(page,{mode:'metadata',positiveId:f.standalone_id,pairId:null});await ready(dialog);
    const archive=button(dialog,'Archive variant…');await expect(archive).toBeDisabled();
    await expect(archive).toHaveAccessibleDescription('Archive needs a selected saved negative variant.');
    await expect(note(dialog)).toHaveText('Archive needs a selected saved negative variant.');
    await button(dialog,'Cancel').click();await expect(dialog).toHaveCount(0);await expect(page.locator('#editor-trigger')).toBeFocused();

    dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await button(dialog,'Archive variant…').click();
    await expect(dialog.getByRole('heading',{name:'Archive negative variant',exact:true})).toBeVisible();
    await expect(button(dialog,'Close dialog')).toBeFocused();
    const block=dialog.getByRole('region',{name:'Negative prompt',exact:true});
    await expect(block).toHaveClass(/\bprompt-negative\b/);await expect(block.locator('pre.prompt-text')).toHaveText(f.selected_negative);
    await expect(dialog).toContainText('1 image and 2 recorded runs stay linked.');
    await expect(dialog).toContainText('You can restore it from Show archived in Negative variants.');
    expect(await actionNames(dialog)).toEqual(['Cancel','Archive']);await expect(button(dialog,'Archive')).toHaveClass(/\bdanger\b/);
    await button(dialog,'Cancel').click();await expect(dialog).toHaveCount(0);
    await expect(page.locator('#editor-trigger')).toBeFocused();expect(page.writes).toHaveLength(0);
    expect((await pairRead(request,f.pair_id)).archived).toBe(false);

    dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await button(dialog,'Archive variant…').click();await expect(button(dialog,'Archive')).toBeEnabled();
    await button(dialog,'Archive').click();await expect(dialog).toHaveCount(0);
    expect(page.writes).toEqual([{method:'PATCH',url:`/prompt_manager/pairs/${f.pair_id}`,body:{archived:true}}]);
    expect((await pairRead(request,f.pair_id)).archived).toBe(true);
    await expect.poll(()=>page.evaluate(()=>window.editorEvents.reconciled.at(-1)?.commit.kind)).toBe('archive-pair');
});

test('a failed archive keeps focus on the Archive confirmation inside the dialog',async({page})=>{
    const f=page.fixture;await mountEditor(page);
    let failed=false;
    await page.route(`**/prompt_manager/pairs/${f.pair_id}`,route=>{
        if(route.request().method()!=='PATCH'||failed)return route.continue();
        failed=true;return route.fulfill({status:400,contentType:'application/json',body:'{"success":false,"error":"Cannot archive.","code":null}'});
    });
    const dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await button(dialog,'Archive variant…').click();await expect(button(dialog,'Archive')).toBeEnabled();
    await button(dialog,'Archive').click();
    await expect(note(dialog)).toContainText('Cannot archive.');expect(page.writes).toHaveLength(1);
    await expect(button(dialog,'Archive')).toBeFocused();
    expect(await dialog.evaluate(node=>node.contains(document.activeElement))).toBe(true);
});

test('an unconfirmed archive moves focus to Retry instead of dropping it out of the dialog',async({page,request})=>{
    const f=page.fixture;await mountEditor(page);let dropped=false;
    await page.route(`**/prompt_manager/pairs/${f.pair_id}`,async route=>{
        if(route.request().method()==='PATCH'&&!dropped){dropped=true;await route.fetch();await route.abort('failed');return;}
        await route.continue();
    });
    const dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await button(dialog,'Archive variant…').click();await expect(button(dialog,'Archive')).toBeEnabled();
    await button(dialog,'Archive').click();
    await expect(note(dialog)).toHaveText('Archive could not be confirmed. Retry sends the same archive once.');
    await expect(button(dialog,'Archive')).toBeDisabled();await expect(button(dialog,'Retry')).toBeFocused();
    expect(page.writes).toHaveLength(1);
    await page.keyboard.press('Enter');await expect(dialog).toHaveCount(0);
    expect(page.writes).toHaveLength(2);expect((await pairRead(request,f.pair_id)).archived).toBe(true);
});

test('an archive conflict offers an explicit Reload that re-reads and never replays the archive',async({page,request})=>{
    const f=page.fixture;await mountEditor(page);
    let conflicted=false;
    await page.route(`**/prompt_manager/pairs/${f.pair_id}`,route=>{
        if(route.request().method()!=='PATCH'||conflicted)return route.continue();
        conflicted=true;return route.fulfill({status:409,contentType:'application/json',body:'{"success":false,"error":"changed","code":"metadata-conflict"}'});
    });
    const dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await button(dialog,'Archive variant…').click();await expect(button(dialog,'Archive')).toBeEnabled();
    await button(dialog,'Archive').click();
    await expect(note(dialog)).toHaveText('This negative variant changed since it was loaded. Reload it and review it before archiving.');
    await expect(button(dialog,'Archive')).toBeDisabled();expect(page.writes).toHaveLength(1);
    expect(await actionNames(dialog)).toEqual(['Reload','Cancel','Archive']);
    await button(dialog,'Reload').click();
    await expect(note(dialog)).toHaveText('Loaded the latest saved values. Review them, then archive.');
    await expect(dialog).toContainText('1 image and 2 recorded runs stay linked.');
    await expect(dialog.getByRole('region',{name:'Negative prompt',exact:true})).toContainText(f.selected_negative);
    expect(page.writes).toHaveLength(1);expect((await pairRead(request,f.pair_id)).archived).toBe(false);
    await button(dialog,'Archive').click();await expect(dialog).toHaveCount(0);
    expect(page.writes.map(write=>`${write.method} ${write.url}`)).toEqual([`PATCH /prompt_manager/pairs/${f.pair_id}`,`PATCH /prompt_manager/pairs/${f.pair_id}`]);
    expect((await pairRead(request,f.pair_id)).archived).toBe(true);
});

test('cancel and busy submit have honest write counts',async({page})=>{
    const f=page.fixture;await mountEditor(page);
    let dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await button(dialog,'Save').click();await expect(dialog).toHaveCount(0);expect(page.writes).toHaveLength(0);
    dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await field(dialog,'Notes').fill('changed then cancelled');await button(dialog,'Close dialog').click();
    await expect(dialog).toHaveCount(0);await expect(page.locator('#editor-trigger')).toBeFocused();expect(page.writes).toHaveLength(0);

    let release;const held=new Promise(resolve=>{release=resolve;});
    await page.route(`**/prompt_manager/library/${f.positive_id}`,async route=>{if(route.request().method()==='PATCH')await held;await route.continue();});
    dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await field(dialog,'Notes').fill('busy save');await button(dialog,'Save').click();
    await expect(note(dialog)).toHaveText('Saving… The dialog stays open until the server answers.');
    for(const name of ['Save','Cancel','Close dialog','Create text revision','Archive variant…','Delete prompt…'])await expect(button(dialog,name)).toBeDisabled();
    await field(dialog,'Title').press('Enter');await page.keyboard.press('Escape');
    await expect(dialog).toBeVisible();expect(page.writes).toHaveLength(1);
    release();await expect(dialog).toHaveCount(0);expect(page.writes).toHaveLength(1);
    await expect(page.locator('#editor-trigger')).toBeFocused();
});

test('recovery states render inline and never replay a write',async({page,request})=>{
    const f=page.fixture;await mountEditor(page);
    let dialog=await openEditor(page,{mode:'metadata',positiveId:f.negative_only_id,pairId:null});
    await expect(note(dialog)).toHaveText('The latest saved values could not be loaded. Nothing was saved.');
    await expect(button(dialog,'Save')).toBeDisabled();await expect(button(dialog,'Retry')).toBeVisible();
    await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);

    dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    const current=await detail(request,f.positive_id,'active');
    await expect(await request.patch(`/prompt_manager/library/${f.positive_id}`,{data:{expected_epoch:current.library_epoch,if_match:current.positive.edit_token,metadata:{notes:'Changed elsewhere'}}})).toBeOK();
    await field(dialog,'Title').fill('Conflicting title');await button(dialog,'Save').click();
    await expect(note(dialog)).toHaveText('This prompt changed since it was loaded. Reload it and review your entries before saving.');
    await expect(button(dialog,'Save')).toBeDisabled();await button(dialog,'Reload').click();
    await expect(note(dialog)).toHaveText('Loaded the latest saved values. Review your entries, then save.');
    await expect(field(dialog,'Title')).toHaveValue('Conflicting title');await expect(field(dialog,'Notes')).toHaveValue('Changed elsewhere');
    await button(dialog,'Save').click();await expect(dialog).toHaveCount(0);
    expect(page.writes.filter(write=>write.method==='PATCH')).toHaveLength(2);
    expect((await detail(request,f.positive_id)).positive).toMatchObject({title:'Conflicting title',notes:'Changed elsewhere'});

    page.writes.length=0;
    dialog=await openEditor(page,{mode:'new'});await ready(dialog);
    const snapshot=await request.get('/__test__/library-editor/snapshot');await expect(snapshot).toBeOK();
    const buffer=await snapshot.body();
    const restore=await unseenRestore(page,()=>request.post('/prompt_manager/restore',{multipart:{database_file:{name:'editor.db',mimeType:'application/octet-stream',buffer}}}));
    await expect(restore).toBeOK();page.writes.length=0;
    await field(dialog,'Title').fill('After restore');await field(dialog,'Positive prompt').fill('Restored storage prompt');await button(dialog,'Save').click();
    await expect(note(dialog)).toHaveText('Prompt storage was restored since this dialog opened. Nothing was saved.');
    await expect(button(dialog,'Save')).toBeDisabled();expect(page.writes).toHaveLength(1);
    await button(dialog,'Reload Library state').click();
    await expect(note(dialog)).toHaveText('Loaded the latest saved values. Review your entries, then save.');
    await expect(field(dialog,'Title')).toHaveValue('After restore');expect(page.writes).toHaveLength(1);
    await button(dialog,'Cancel').click();await expect(dialog).toHaveCount(0);

    page.writes.length=0;let dropped=false;
    await page.route('**/prompt_manager/library',async route=>{
        if(route.request().method()==='POST'&&!dropped){dropped=true;await route.fetch();await route.abort('failed');return;}
        await route.continue();
    });
    dialog=await openEditor(page,{mode:'new'});await ready(dialog);
    await field(dialog,'Title').fill('Lost response');await field(dialog,'Positive prompt').fill('Lost response prompt');await button(dialog,'Save').click();
    await expect(note(dialog)).toHaveText('Save could not be confirmed. Your entries are kept; Retry sends the same save once.');
    await expect(button(dialog,'Save')).toBeDisabled();await expect(button(dialog,'Retry')).toBeFocused();
    await page.unroute('**/prompt_manager/library');
    await page.route('**/prompt_manager/library/*?variant_limit=40',route=>route.fulfill({status:500,contentType:'application/json',body:'{"success":false,"error":"boom"}'}),{times:1});
    await button(dialog,'Retry').click();
    await expect(note(dialog)).toHaveText('Saved; Library refresh failed.');
    expect(await page.evaluate(()=>window.editorEvents.committed.at(-1).receipt)).toMatchObject({positive_created:false,pair_created:false});
    expect(page.writes.map(write=>write.body.positive_text)).toEqual(['Lost response prompt','Lost response prompt']);
    await button(dialog,'Retry refresh').click();await expect(dialog).toHaveCount(0);expect(page.writes).toHaveLength(2);
    const rows=(await(await request.get('/prompt_manager/library?q=Lost%20response')).json()).items.filter(item=>item.text==='Lost response prompt');
    expect(rows).toHaveLength(1);
});

test('edge states keep drafts, explain disabled archive and clean up on dispose',async({page,request})=>{
    const f=page.fixture;
    await page.route('**/prompt_manager/tags',route=>route.fulfill({status:500,contentType:'application/json',body:'{"success":false,"error":"down"}'}));
    await mountEditor(page);
    let dialog=await openEditor(page,{mode:'new',draft:{title:'Drafted title',notes:'Drafted notes'}});await ready(dialog);
    await expect(field(dialog,'Title')).toHaveValue('Drafted title');await expect(field(dialog,'Notes')).toHaveValue('Drafted notes');
    expect(await field(dialog,'Tags, separated by commas').evaluate(node=>node.list.options.length)).toBe(0);
    await field(dialog,'Title').fill('   ');await field(dialog,'Positive prompt').fill('Spaces only title');await button(dialog,'Save').click();
    await expect(note(dialog)).toHaveText('Title is required');expect(page.writes).toHaveLength(0);
    await page.route('**/prompt_manager/library',route=>route.request().method()==='POST'
        ?route.fulfill({status:400,contentType:'application/json',body:'{"success":false,"error":"Title is too long.","code":null}'}):route.continue(),{times:1});
    await field(dialog,'Title').fill('Rejected title');await button(dialog,'Save').click();
    await expect(note(dialog)).toHaveText('Not saved: Title is too long. Your entries are kept.');
    await expect(button(dialog,'Save')).toBeEnabled();await expect(field(dialog,'Title')).toHaveValue('Rejected title');
    await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);

    dialog=await openEditor(page,{mode:'metadata',positiveId:f.negative_only_id,pairId:null});
    await expect(button(dialog,'Retry')).toBeVisible();await button(dialog,'Retry').click();
    await expect(note(dialog)).toHaveText('The latest saved values could not be loaded. Nothing was saved.');await button(dialog,'Cancel').click();

    dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.archived_pair_id});await ready(dialog);
    await expect(field(dialog,'Negative prompt (optional)')).toHaveValue(f.archived_negative);
    await expect(button(dialog,'Archive variant…')).toBeDisabled();await expect(note(dialog)).toHaveText('This negative variant is already archived.');
    await expect(button(dialog,'Archive variant…')).toHaveAccessibleDescription('This negative variant is already archived.');
    await button(dialog,'Cancel').click();

    let failed=true;
    await page.route(`**/prompt_manager/pairs/${f.empty_pair_id}`,route=>{if(!failed&&route.request().method()==='GET'){failed=true;return route.fulfill({status:500,contentType:'application/json',body:'{"success":false,"error":"down"}'});}return route.continue();});
    dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.empty_pair_id});await ready(dialog);
    await expect(field(dialog,'Negative prompt (optional)')).toHaveValue('');
    failed=false;await button(dialog,'Archive variant…').click();
    await expect(note(dialog)).toHaveText('Linked images and recorded runs could not be read. Nothing was archived.');
    await expect(button(dialog,'Archive')).toBeDisabled();
    await expect(dialog.getByRole('region',{name:'Negative prompt',exact:true})).toContainText('Empty negative prompt');
    await button(dialog,'Retry').click();await expect(dialog).toContainText('0 images and 0 recorded runs stay linked.');
    await expect(button(dialog,'Archive')).toBeEnabled();expect(page.writes).toHaveLength(1);// only the rejected create

    await page.evaluate(()=>window.editorHarness.scope.dispose());
    await expect(dialog).toHaveCount(0);await expect(page.locator('#editor-trigger')).toBeFocused();
    await page.evaluate(()=>window.editorHarness.editor.open({mode:'new'}));await expect(editorDialog(page)).toHaveCount(0);
    expect(page.writes).toHaveLength(1);expect((await pairRead(request,f.empty_pair_id)).archived).toBe(false);
});

test('the archive confirmation does not inherit a failed or invalid Edit save message',async({page})=>{
    const f=page.fixture;await mountEditor(page);
    await page.route(`**/prompt_manager/library/${f.positive_id}`,route=>route.request().method()==='PATCH'
        ?route.fulfill({status:400,contentType:'application/json',body:'{"success":false,"error":"Notes are too long.","code":null}'}):route.continue());
    let dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await field(dialog,'Notes').fill('Rejected notes');await button(dialog,'Save').click();
    await expect(note(dialog)).toHaveText('Not saved: Notes are too long. Your entries are kept.');
    await button(dialog,'Archive variant…').click();await expect(dialog).toContainText('1 image and 2 recorded runs stay linked.');
    await expect(note(dialog)).toBeHidden();await expect(button(dialog,'Archive')).toBeEnabled();
    await button(dialog,'Cancel').click();await expect(dialog).toHaveCount(0);
    dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await field(dialog,'Title').fill('   ');await button(dialog,'Save').click();await expect(note(dialog)).toHaveText('Title is required');
    await button(dialog,'Archive variant…').click();await expect(dialog).toContainText('1 image and 2 recorded runs stay linked.');
    await expect(note(dialog)).toBeHidden();await expect(button(dialog,'Archive')).toBeEnabled();
    expect(page.writes).toHaveLength(1);
});

test('an unconfirmed Edit save says Retry reloads for review and resends nothing',async({page})=>{
    const f=page.fixture;await mountEditor(page);let dropped=false;
    await page.route(`**/prompt_manager/library/${f.positive_id}`,async route=>{
        if(route.request().method()==='PATCH'&&!dropped){dropped=true;await route.fetch();await route.abort('failed');return;}
        await route.continue();
    });
    const dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await field(dialog,'Notes').fill('Unconfirmed notes');await button(dialog,'Save').click();
    await expect(note(dialog)).toHaveText('Save could not be confirmed. Your entries are kept; Retry reloads the latest saved values for review and sends nothing.');
    // Save is disabled now; focus moves to the one recovery action instead of falling out of the modal.
    await expect(button(dialog,'Save')).toBeDisabled();await expect(button(dialog,'Retry')).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(note(dialog)).toHaveText('Loaded the latest saved values. Review your entries, then save.');
    expect(await dialog.evaluate(node=>node.contains(document.activeElement))).toBe(true);
    await expect(field(dialog,'Notes')).toHaveValue('Unconfirmed notes');expect(page.writes).toHaveLength(1);
});

function toastStyle(node) {
    const style=getComputedStyle(node);
    return Object.fromEntries(['position','bottom','left','transform','backgroundColor','borderTopWidth','borderTopColor','paddingTop','paddingLeft',
        'borderTopLeftRadius','boxShadow','zIndex','maxWidth','fontSize','display'].map(key=>[key,style[key]]));
}
for(const width of [1600,390])test(`the shared workspace toast is one polite status matching the concept ${width}`,async({page})=>{
    await page.setViewportSize({width,height:844});
    await page.goto('/__test__/canonical/workspace#library');await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption('dark');
    await page.locator('#toast').evaluate(node=>{node.textContent='Negative saved without adding another positive or gallery.';});const reference=await page.locator('#toast').evaluate(toastStyle);
    await mountEditor(page,'dark');
    const toast=page.locator('#workspace-toast');await expect(toast).toHaveCount(1);
    await expect(toast).toHaveAttribute('role','status');await expect(toast).toHaveAttribute('aria-live','polite');await expect(toast).toBeHidden();
    await page.evaluate(()=>window.editorHarness.showWorkspaceToast('Negative saved without adding another positive or gallery.'));
    await expect(page.getByRole('status').filter({hasText:'Negative saved without adding another positive or gallery.'})).toBeVisible();
    expect(await toast.evaluate(toastStyle)).toEqual(reference);
    expect(reference).toMatchObject({position:'fixed',bottom:width<=700?'12px':'22px',borderTopWidth:'1px',paddingTop:'12px',paddingLeft:'18px',borderTopLeftRadius:'6px',fontSize:'12px'});
});

const conceptModes={
    new:['library',async page=>page.getByRole('button',{name:'+ New prompt',exact:true}).click()],
    metadata:['prompt/alpine',async page=>page.getByRole('button',{name:'Edit metadata',exact:true}).click()],
    revision:['prompt/alpine',async page=>{await page.getByRole('button',{name:'Edit metadata',exact:true}).click();await page.locator('#modal').getByRole('button',{name:'Create text revision',exact:true}).click();}],
    negative:['prompt/alpine',async page=>page.getByRole('button',{name:'+ Add negative',exact:true}).click()],
};

for(const [width,height] of [[1600,1100],[390,844]])for(const theme of ['dark','light'])test(`dialog geometry, tokens and controls match the concept ${width} ${theme}`,async({page},testInfo)=>{
    await page.setViewportSize({width,height});const f=page.fixture;const result={};
    for(const mode of Object.keys(conceptModes)) {
        const [hash,openMode]=conceptModes[mode];
        await page.goto('about:blank');await page.goto(`/__test__/canonical/workspace#${hash}`);
        await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);await openMode(page);const modal=page.locator('#modal');await expect(modal).toBeVisible();
        if(mode==='revision')await page.locator('#modal-close').focus();
        const reference=await modal.evaluate(inventoryOf);
        await captureCanonicalState(page,testInfo,`concept-editor-${mode}-${width}-${theme}`,'#modal');
        result[mode]={reference};
    }
    await mountEditor(page,theme);
    const options={new:{mode:'new'},metadata:{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id},revision:{mode:'revision',positiveId:f.positive_id,pairId:f.pair_id},negative:{mode:'negative',positiveId:f.positive_id}};
    for(const mode of Object.keys(conceptModes)) {
        const dialog=await openEditor(page,options[mode]);await ready(dialog);
        result[mode].actual=await dialog.evaluate(inventoryOf);
        await captureCanonicalState(page,testInfo,`production-editor-${mode}-${width}-${theme}`,'dialog.library-editor');
        await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
    }
    const path=testInfo.outputPath('editor-geometry.json');await writeFile(path,JSON.stringify(result,null,2));
    await testInfo.attach('editor-geometry',{path,contentType:'application/json'});
    for(const [mode,{reference,actual}] of Object.entries(result)) {
        const expected=mapConcept(reference);
        expect.soft(reference.demo,`${mode} concept demo note`).toBe(true);
        expect.soft(expected.actions.buttons.map(item=>item.text),`${mode} actions`).toEqual(ACTIONS[mode]);
        expect.soft(actual.dialog,`${mode} dialog`).toEqual(expected.dialog);
        for(const key of ['header','title','close','fields','twoCol','small','actions','demo'])expect.soft(actual[key],`${mode} ${key}`).toEqual(expected[key]);
        // The UA modal max-inline-size (100% - 6px - 2em) also applies to the concept at 390px.
        expect.soft(actual.dialog).toMatchObject({paddingTop:'24px',borderTopLeftRadius:'9px',borderTopWidth:'1px',width:Math.min(650,width-24,width-34),modal:true});
        expect.soft(actual.title.fontSize).toBe('18px');
        expect.soft(actual.actions).toMatchObject({justifyContent:'flex-end',columnGap:'8px',flexWrap:'wrap',marginTop:'23px'});
        for(const item of actual.fields)expect.soft(item.field).toMatchObject({rowGap:'7px',fontSize:'12px'});
        for(const item of actual.fields.filter(item=>item.tag==='textarea'))expect.soft(item.control.minHeight).toBe('100px');
        if(actual.twoCol)expect.soft(actual.twoCol.columns,`${mode} two columns`).toBe(width<=700?1:2);
        if(testInfo.project.name==='chromium')expect.soft(actual.dialog.backdrop).toBe('rgba(8, 11, 18, 0.72)');
    }
    expect(await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
});

// Real entry actions: Library, Images and prompt history headings, and the Negative variants panel.
const historyRegion=page=>page.getByRole('region',{name:'Prompt history',exact:true});
const headingActions=page=>page.locator('#workspace-main > .heading > .heading-actions');
const variantsPanel=page=>page.getByRole('region',{name:'Saved negative variants',exact:true});
const savedTexts=page=>historyRegion(page).getByRole('region',{name:'Saved prompt texts',exact:true});
const toast=page=>page.locator('#workspace-toast');
const visibleNames=locator=>locator.evaluateAll(nodes=>nodes.map(node=>node.textContent.trim()));
const pressedVariant=page=>variantsPanel(page).locator('.variant-dot[aria-pressed="true"]');
async function openHistory(page,id) {
    await page.goto(`${workspace}#/library/${id}`);await expect(variantsPanel(page)).toBeVisible();
}
async function selectPair(page,name) {
    const dot=variantsPanel(page).getByRole('button',{name,exact:true});await dot.click();await expect(dot).toHaveAttribute('aria-pressed','true');
}
/** In-app navigation away and back, as the concept's hash router does (app.js navigate: state.variant = 0). */
async function leaveAndReturn(page,id) {
    await page.getByRole('button',{name:'← Prompt library',exact:true}).click();await expect(page.locator('#workspace-main h1')).toHaveText('Prompt library');
    await page.evaluate(id=>{location.hash=`#/library/${id}`;},id);await expect(variantsPanel(page)).toBeVisible();await expect(pressedVariant(page)).toHaveCount(1);
}
const laneLabel=(page,text)=>variantsPanel(page).locator('.variant-lane').filter({hasText:text});

// #415: a restore the page sees finish while New prompt is open keeps the dialog and its entries; the Library is shown
// again from the restored data only once the dialog has closed.
test('a restore seen finishing keeps New prompt open and reloads the Library after it closes',async({page,request})=>{
    const f=page.fixture;const snapshot=await request.get('/__test__/library-editor/snapshot');await expect(snapshot).toBeOK();
    const buffer=await snapshot.body();
    const current=await detail(request,f.positive_id,'active');
    await expect(await request.patch(`/prompt_manager/library/${f.positive_id}`,{data:{expected_epoch:current.library_epoch,if_match:current.positive.edit_token,metadata:{title:'Retitled elsewhere'}}})).toBeOK();
    const restoreRun=await followRestore(page);
    await page.goto(`${workspace}#/library`);const card=page.locator(`article[data-prompt-id="${f.positive_id}"]`);
    await expect(card).toContainText('Retitled elsewhere');
    await expect(page.locator('#activity-pill')).toHaveAttribute('href',SEEN_RESTORE_PILL);
    await headingActions(page).getByRole('button',{name:'+ New prompt',exact:true}).click();
    const dialog=editorDialog(page);await expect(dialog.getByRole('heading',{name:'New prompt',exact:true})).toBeVisible();await ready(dialog);
    await field(dialog,'Title').fill('Typed before restore');await field(dialog,'Positive prompt').fill('A prompt typed while storage was restored');
    await expect(await request.post('/prompt_manager/restore',{multipart:{database_file:{name:'editor.db',mimeType:'application/octet-stream',buffer}}})).toBeOK();
    page.writes.length=0;
    await restoreRun.finish();
    await expect(toast(page)).toHaveText(SEEN_RESTORE_TOAST);
    await expect(dialog).toBeVisible();
    await expect(field(dialog,'Title')).toHaveValue('Typed before restore');
    await expect(field(dialog,'Positive prompt')).toHaveValue('A prompt typed while storage was restored');
    await expect(card).toContainText('Retitled elsewhere');
    await button(dialog,'Save').click();
    await expect(note(dialog)).toHaveText('Prompt storage was restored since this dialog opened. Nothing was saved.');
    await expect(button(dialog,'Save')).toBeDisabled();expect(page.writes).toHaveLength(1);
    await button(dialog,'Cancel').click();await expect(dialog).toHaveCount(0);
    await expect(card).not.toContainText('Retitled elsewhere');await expect(card).toContainText('Editor portrait');
    await expect(page.locator('#workspace-main h1')).toBeFocused();
    expect(page.writes).toHaveLength(1);
});

test('library heading offers Export then + New prompt, and New opens the saved prompt history',async({page,request})=>{
    await page.goto(`${workspace}#/library`);await expect(page.locator('article[data-prompt-id]').first()).toBeVisible();
    const actions=headingActions(page).locator('button');
    // D152 (#443): Show archived prompts (N) comes first, a plain button (#482: no pressed state; on the archived shelf it
    // reads "← Prompt library").
    expect(await visibleNames(actions)).toEqual(['Show archived prompts (0)','Export','+ New prompt']);
    await expect(actions.nth(0)).not.toHaveAttribute('aria-pressed');await expect(actions.nth(0)).not.toHaveClass(/ca-primary/);
    // One image is counted in the singular.
    const f=page.fixture;await expect(page.locator(`article[data-prompt-id="${f.positive_id}"]`).getByText('1 image',{exact:true})).toHaveCount(1);
    await expect(actions.nth(1)).toHaveAccessibleName('Export full Library JSON');await expect(actions.nth(1)).not.toHaveClass(/ca-primary/);
    await expect(actions.nth(2)).toHaveClass(/\bca-primary\b/);
    for(const index of [0,1,2])expect((await actions.nth(index).boundingBox()).height).toBeGreaterThanOrEqual(32);
    await actions.nth(2).click();const dialog=editorDialog(page);await expect(dialog.getByRole('heading',{name:'New prompt',exact:true})).toBeVisible();await ready(dialog);
    await field(dialog,'Title').fill('Harbor at dawn');await field(dialog,'Positive prompt').fill('A quiet harbor at dawn');await field(dialog,'Negative prompt (optional)').fill('fog, noise');
    await button(dialog,'Save').click();await expect(dialog).toHaveCount(0);
    const created=(await(await request.get('/prompt_manager/library?text=quiet%20harbor')).json()).items[0];
    await expect(page).toHaveURL(new RegExp(`#/library/${created.id}$`));
    await expect(page.locator('#workspace-main h1')).toHaveText('Harbor at dawn');await expect(page.locator('#workspace-main h1')).toBeFocused();
    await expect(toast(page)).toHaveText('Saved.');
    await expect(pressedVariant(page)).toHaveCount(1);await expect(savedTexts(page).getByRole('region',{name:'Negative prompt',exact:true})).toContainText('fog, noise');
    // Exactly one saved negative is counted in the singular, in history and on the shelf card.
    await expect(variantsPanel(page).getByText('1 of 1 saved negative variant',{exact:true})).toHaveCount(1);
    await page.getByRole('button',{name:'← Prompt library',exact:true}).click();
    await expect(page.locator(`article[data-prompt-id="${created.id}"]`).getByText('− 1 negative variant',{exact:true})).toHaveCount(1);
    expect(page.writes.map(write=>`${write.method} ${write.url}`)).toEqual(['POST /prompt_manager/library']);
});

test('new from Images cancel preserves browse',async({page})=>{
    await page.goto(`${workspace}#/images`);await expect(page.locator('#workspace-main h1')).toHaveText('Images');
    // #249 C2: the concept's Images heading puts Import before New prompt.
    const actions=headingActions(page).locator('button');expect(await visibleNames(actions)).toEqual(['Import','New prompt']);
    const trigger=actions.last();await expect(trigger).toHaveClass(/\bca-primary\b/);await expect(trigger.locator('svg')).toHaveCount(1);
    await trigger.click();let dialog=editorDialog(page);await expect(dialog.getByRole('heading',{name:'New prompt',exact:true})).toBeVisible();await ready(dialog);
    await field(dialog,'Title').fill('Discarded');await button(dialog,'Cancel').click();await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();await expect(page).toHaveURL(/#\/images$/);await expect(page.locator('.library-shelf, .library-history')).toHaveCount(0);
    expect(page.writes).toHaveLength(0);
    await trigger.click();dialog=editorDialog(page);await ready(dialog);
    await field(dialog,'Title').fill('From images');await field(dialog,'Positive prompt').fill('A prompt saved from Images');await button(dialog,'Save').click();
    await expect(page).toHaveURL(/#\/library\/\d+$/);await expect(page.locator('#workspace-main h1')).toHaveText('From images');await expect(page.locator('#workspace-main h1')).toBeFocused();
});

test('history heading follows the concept and sends the selected saved pair',async({page})=>{
    const f=page.fixture;await openHistory(page,f.positive_id);
    const actions=headingActions(page).locator('button:not([role="menuitem"])');
    // D163 (#512): the More menu button follows Send to ComfyUI.
    expect(await visibleNames(actions)).toEqual(['← Prompt library','Edit metadata','Send to ComfyUI','⋯']);
    await expect(page.getByRole('link',{name:'Back to library',exact:true})).toHaveCount(0);
    const send=actions.nth(2);await expect(send).toHaveClass(/\bsend\b/);await expect(send.locator('svg')).toHaveCount(1);
    await expect(send).toHaveAccessibleName(`Send prompt pair to ComfyUI: ${f.metadata.title}`);
    expect(await visibleNames(variantsPanel(page).locator('.row-actions button'))).toEqual(['Compare negatives','+ Add negative','Show archived (1)']);
    await expect(variantsPanel(page).getByRole('button',{name:'Show archived (1)',exact:true})).toHaveAttribute('aria-pressed','false');
    // The concept opens a prompt history on its first negative variant (app.js navigate: state.variant = 0), so Send starts enabled.
    await expect(pressedVariant(page)).toHaveAccessibleName('Select negative variant 1');await expect(send).toBeEnabled();
    await selectPair(page,'Select negative variant 2');await expect(send).toBeEnabled();
    await expect(savedTexts(page).getByRole('button',{name:'Send prompt pair',exact:true})).toHaveCount(0);
    await send.click();const sender=page.getByRole('dialog',{name:'Send to ComfyUI',exact:true});await expect(sender).toBeVisible();
    await sender.getByRole('button',{name:'Close',exact:true}).click();await expect(sender).toHaveCount(0);await expect(send).toBeFocused();
    await actions.nth(0).click();await expect(page).toHaveURL(/#\/library$/);await expect(page.locator('#workspace-main h1')).toHaveText('Prompt library');
    expect(page.writes).toHaveLength(0);
});

test('returning to a history opens on the first negative, forgetting a clicked variant; All images is a dialog',async({page})=>{
    const f=page.fixture;await openHistory(page,f.positive_id);
    await expect(pressedVariant(page)).toHaveAccessibleName('Select negative variant 1');
    await selectPair(page,'Select negative variant 2');
    await leaveAndReturn(page,f.positive_id);await expect(pressedVariant(page)).toHaveAccessibleName('Select negative variant 1');
    // #249 D26: All images opens the concept dialog and leaves the selected negative as it was.
    const all=variantsPanel(page).getByRole('button',{name:'All images for this positive',exact:true});
    await all.click();const listing=page.getByRole('dialog',{name:'All images for this positive',exact:true});await expect(listing).toBeVisible();
    await page.keyboard.press('Escape');await expect(listing).toHaveCount(0);await expect(pressedVariant(page)).toHaveAccessibleName('Select negative variant 1');
    await leaveAndReturn(page,f.positive_id);
    await expect(pressedVariant(page)).toHaveAccessibleName('Select negative variant 1');
    await expect(headingActions(page).locator('button.send')).toBeEnabled();expect(page.writes).toHaveLength(0);
});

test('add negative re-renders history with the saved or reused pair selected',async({page})=>{
    const f=page.fixture;await openHistory(page,f.positive_id);
    const add=variantsPanel(page).getByRole('button',{name:'+ Add negative',exact:true});
    await add.click();let dialog=editorDialog(page);await ready(dialog);await field(dialog,'Negative prompt').fill('grain, dust');await button(dialog,'Save').click();
    await expect(dialog).toHaveCount(0);await expect(page).toHaveURL(new RegExp(`#/library/${f.positive_id}$`));
    await expect(toast(page)).toHaveText('Negative saved without adding another positive or gallery.');await expect(page.locator('#workspace-main h1')).toBeFocused();
    await expect(savedTexts(page).getByRole('region',{name:'Negative prompt',exact:true})).toContainText('grain, dust');
    await expect(laneLabel(page,'Negative variant 1 ·')).toHaveCount(1);await expect(pressedVariant(page)).toHaveAccessibleName('Select negative variant 1');
    await add.click();dialog=editorDialog(page);await ready(dialog);await field(dialog,'Negative prompt').fill(`  ${f.selected_negative.toUpperCase()} `);await button(dialog,'Save').click();
    await expect(dialog).toHaveCount(0);await expect(toast(page)).toHaveText('This prompt pair already exists. Showing the saved version.');
    await expect(savedTexts(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('p.prompt-text')).toHaveText(f.selected_negative);
    await expect(laneLabel(page,/· 2 runs$/)).toHaveCount(1);
    await expect(pressedVariant(page)).toHaveAccessibleName('Select negative variant 3');
    // The saved selection applies to that save only; returning opens on the first negative, as in the concept.
    await leaveAndReturn(page,f.positive_id);await expect(pressedVariant(page)).toHaveAccessibleName('Select negative variant 1');
    expect(page.writes.map(write=>write.url)).toEqual([`/prompt_manager/library/${f.positive_id}/variants`,`/prompt_manager/library/${f.positive_id}/variants`]);
});

test('reused pair outside first forty remains selected without loading every page',async({page,request})=>{
    test.setTimeout(90000);
    const f=page.fixture;let current=await detail(request,f.positive_id,'active');
    for(let index=0;index<41;index++) {
        const response=await request.post(`/prompt_manager/library/${f.positive_id}/variants`,{data:{expected_epoch:current.library_epoch,if_match:current.positive.edit_token,negative_text:`bulk negative ${index}`}});
        await expect(response).toBeOK();current={...current,positive:(await response.json()).positive};
    }
    const variantCursorRequests=[];page.on('request',item=>{if(item.url().includes('variant_cursor'))variantCursorRequests.push(item.url());});
    await openHistory(page,f.positive_id);await expect(variantsPanel(page)).toContainText('40 of 44 saved negative variants');page.writes.length=0;
    await variantsPanel(page).getByRole('button',{name:'+ Add negative',exact:true}).click();
    const dialog=editorDialog(page);await ready(dialog);await field(dialog,'Negative prompt').fill(f.first_negative);await button(dialog,'Save').click();await expect(dialog).toHaveCount(0);
    await expect(savedTexts(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('p.prompt-text')).toHaveText(f.first_negative);
    await expect(pressedVariant(page)).toHaveCount(1);await expect(variantsPanel(page)).toContainText('40 of 44 saved negative variants');
    await expect(variantsPanel(page).locator('.variant-lane')).toHaveCount(41);
    expect(variantCursorRequests).toHaveLength(0);expect(page.writes).toHaveLength(1);
});

test('edit metadata stays on the page and re-reads the selected pair',async({page,request})=>{
    const f=page.fixture;await openHistory(page,f.positive_id);await selectPair(page,'Select negative variant 2');
    const pairReads=[];page.on('request',item=>{if(item.method()==='GET'&&new URL(item.url()).pathname===`/prompt_manager/pairs/${f.pair_id}`)pairReads.push(item.url());});
    await page.getByRole('button',{name:'Edit metadata',exact:true}).click();const dialog=editorDialog(page);await ready(dialog);
    await expect(field(dialog,'Negative prompt (optional)')).toHaveValue(f.selected_negative);
    const before=pairReads.length;
    await field(dialog,'Title').fill('Edited in place');await field(dialog,'Category').fill('Studio work');await button(dialog,'Save').click();await expect(dialog).toHaveCount(0);
    await expect(page.locator('#workspace-main h1')).toHaveText('Edited in place');await expect(page.locator('#workspace-main h1')).toBeFocused();
    await expect(toast(page)).toHaveText('Saved.');
    await expect(page).toHaveURL(new RegExp(`#/library/${f.positive_id}$`));await expect(historyRegion(page).locator('article.surface > span.chip').first()).toHaveText('Studio work');
    await expect(pressedVariant(page)).toHaveAccessibleName('Select negative variant 2');
    await expect.poll(()=>pairReads.length).toBeGreaterThan(before);
    await expect(headingActions(page).locator('button.send')).toHaveAccessibleName('Send prompt pair to ComfyUI: Edited in place');
    expect((await detail(request,f.positive_id)).positive).toMatchObject({title:'Edited in place',category:'Studio work'});
});

test('saved refresh failure retries only reads',async({page})=>{
    const f=page.fixture;await openHistory(page,f.positive_id);
    await page.getByRole('button',{name:'Edit metadata',exact:true}).click();const dialog=editorDialog(page);await ready(dialog);
    let failed=false;
    await page.route(`**/prompt_manager/library/${f.positive_id}?variant_limit=40`,route=>{if(page.writes.length&&!failed){failed=true;return route.fulfill({status:500,contentType:'application/json',body:'{"success":false,"error":"boom"}'});}return route.continue();});
    await field(dialog,'Notes').fill('Refresh after failure');await button(dialog,'Save').click();
    await expect(page.getByText('Saved; Library refresh failed.',{exact:true})).toBeVisible();
    const writeRequests=page.writes;expect(writeRequests).toHaveLength(1);
    await button(dialog,'Retry refresh').click();await expect(dialog).toHaveCount(0);expect(writeRequests).toHaveLength(1);
    await expect(page.locator('#workspace-main h1')).toBeFocused();
});

test('restored storage after a confirmed save or before an archive offers an explicit Library reload',async({page,request})=>{
    const f=page.fixture;const snapshot=await(await request.get('/__test__/library-editor/snapshot')).body();
    const restore=()=>unseenRestore(page,()=>request.post('/prompt_manager/restore',{multipart:{database_file:{name:'editor.db',mimeType:'application/octet-stream',buffer:snapshot}}}));
    await openHistory(page,f.positive_id);
    await page.route(`**/prompt_manager/library/${f.positive_id}`,async route=>{
        if(route.request().method()!=='PATCH')return route.continue();
        const response=await route.fetch();await expect(await restore()).toBeOK();return route.fulfill({response});
    });
    await page.getByRole('button',{name:'Edit metadata',exact:true}).click();let dialog=editorDialog(page);await ready(dialog);
    await field(dialog,'Notes').fill('Lost to a restore');
    // The save's answer waits for a whole restore in the route above; wait for that answer, not the 5 s expect
    // default (#501 merge queue: under load the restore outlasted it on WebKit).
    const saved=page.waitForResponse(item=>item.request().method()==='PATCH'&&new URL(item.url()).pathname===`/prompt_manager/library/${f.positive_id}`);
    await button(dialog,'Save').click();await saved;
    await expect(note(dialog)).toHaveText('Saved; Library refresh failed because prompt storage was restored. Reload the Library to see current data.');
    expect(await actionNames(dialog)).toEqual(['Reload Library','Create text revision','Archive variant…','Delete prompt…','Cancel','Save']);
    for(const name of ['Create text revision','Archive variant…','Delete prompt…'])await expect(button(dialog,name)).toBeDisabled();
    await button(dialog,'Reload Library').click();await expect(dialog).toHaveCount(0);
    await expect(variantsPanel(page)).toBeVisible();await expect(page.locator('#workspace-main h1')).toHaveText(f.metadata.title);
    await page.unroute(`**/prompt_manager/library/${f.positive_id}`);expect(page.writes).toHaveLength(1);
    await selectPair(page,'Select negative variant 2');
    await page.getByRole('button',{name:'Edit metadata',exact:true}).click();dialog=editorDialog(page);await ready(dialog);
    await button(dialog,'Archive variant…').click();await expect(button(dialog,'Archive')).toBeEnabled();
    await expect(await restore()).toBeOK();await button(dialog,'Archive').click();
    await expect(note(dialog)).toHaveText('Prompt storage was restored since this dialog opened. Nothing was archived.');
    expect(await actionNames(dialog)).toEqual(['Reload Library','Cancel','Archive']);expect(page.writes).toHaveLength(1);
    await button(dialog,'Reload Library').click();await expect(dialog).toHaveCount(0);await expect(variantsPanel(page)).toBeVisible();
    expect((await pairRead(request,f.pair_id)).archived).toBe(false);
});

const DISCARDS='Leaving this dialog discards your entries.';
test('restored storage before an Edit or Add negative save offers only the Library reload exit',async({page,request})=>{
    const f=page.fixture;const snapshot=await(await request.get('/__test__/library-editor/snapshot')).body();
    const restore=()=>unseenRestore(page,()=>request.post('/prompt_manager/restore',{multipart:{database_file:{name:'editor.db',mimeType:'application/octet-stream',buffer:snapshot}}}));
    await openHistory(page,f.positive_id);
    await page.getByRole('button',{name:'Edit metadata',exact:true}).click();let dialog=editorDialog(page);await ready(dialog);
    await expect(await restore()).toBeOK();
    await field(dialog,'Notes').fill('Typed before the restore');await button(dialog,'Save').click();
    await expect(note(dialog)).toHaveText(`Prompt storage was restored since this dialog opened. Nothing was saved. ${DISCARDS}`);
    expect(await actionNames(dialog)).toEqual(['Reload Library','Create text revision','Archive variant…','Delete prompt…','Cancel','Save']);
    await expect(button(dialog,'Reload Library state')).toHaveCount(0);await expect(button(dialog,'Save')).toBeDisabled();
    // Revise and archive would re-read the old numeric IDs, which may now name another prompt.
    for(const name of ['Create text revision','Archive variant…','Delete prompt…'])await expect(button(dialog,name)).toBeDisabled();
    await expect(button(dialog,'Reload Library')).toBeFocused();
    expect(page.writes).toHaveLength(1);// the refused save
    await button(dialog,'Reload Library').click();await expect(dialog).toHaveCount(0);await expect(variantsPanel(page)).toBeVisible();
    expect(page.writes).toHaveLength(1);expect((await detail(request,f.positive_id)).positive.notes).toBe(f.metadata.notes);

    await variantsPanel(page).getByRole('button',{name:'+ Add negative',exact:true}).click();dialog=editorDialog(page);await ready(dialog);
    await expect(await restore()).toBeOK();
    await field(dialog,'Negative prompt').fill('typed before the restore');await button(dialog,'Save').click();
    await expect(note(dialog)).toHaveText(`Prompt storage was restored since this dialog opened. Nothing was saved. ${DISCARDS}`);
    expect(await actionNames(dialog)).toEqual(['Reload Library','Cancel','Save']);
    await expect(button(dialog,'Reload Library state')).toHaveCount(0);expect(page.writes).toHaveLength(2);
    await button(dialog,'Reload Library').click();await expect(dialog).toHaveCount(0);await expect(variantsPanel(page)).toBeVisible();
    expect(page.writes).toHaveLength(2);
    expect((await detail(request,f.positive_id)).variants.some(variant=>variant.negative_text==='typed before the restore')).toBe(false);
});

// #249 F2: archived lanes (#235) are concept lanes marked archived, with the D45 reduced emphasis.
test('show archived and restore still work',async({page,request})=>{
    const f=page.fixture;await openHistory(page,f.positive_id);
    const timeline=variantsPanel(page).locator(':scope > .timeline');
    expect(await visibleNames(variantsPanel(page).locator('.panel-heading .row-actions button'))).toEqual(['Compare negatives','+ Add negative','Show archived (1)']);
    await expect(timeline.locator('.library-variant-archived')).toHaveCount(0);
    const toggle=variantsPanel(page).getByRole('button',{name:'Show archived (1)',exact:true});await toggle.click();await expect(toggle).toHaveAttribute('aria-pressed','true');
    const lane=timeline.locator(':scope > .variant-lane.library-variant-archived');await expect(lane).toHaveCount(1);
    await expect(lane.locator(':scope > span')).toContainText(/ · \d+ runs? · Archived/);
    const dot=lane.locator(':scope > button.variant-dot');await expect(dot).toHaveText('−');
    expect(Number(await dot.evaluate(node=>getComputedStyle(node).opacity))).toBeLessThan(1);
    expect(Number(await lane.evaluate(node=>getComputedStyle(node,'::before').opacity))).toBeLessThan(1);
    await expect(lane.locator(':scope > span')).toHaveCSS('opacity','1');
    await dot.click();await expect(dot).toHaveAttribute('aria-pressed','true');
    const writes=page.writes.length;
    await lane.getByRole('button',{name:/^Restore /}).click();
    await expect(timeline.locator('.library-variant-archived')).toHaveCount(0);
    await expect(variantsPanel(page).getByRole('button',{name:'Show archived (0)',exact:true})).toBeVisible();
    expect(page.writes.slice(writes)).toEqual([{method:'PATCH',url:`/prompt_manager/pairs/${f.archived_pair_id}`,body:{archived:false}}]);
    expect((await pairRead(request,f.archived_pair_id)).archived).toBe(false);
    await expect(timeline.locator(':scope > .variant-lane > .variant-dot[aria-pressed="true"]')).toHaveCount(1);
});

test('Restore stays disabled until the restore settles and sends one PATCH',async({page,request})=>{
    const f=page.fixture;await openHistory(page,f.positive_id);
    let release;const held=new Promise(resolve=>{release=resolve;});
    await page.route(`**/prompt_manager/pairs/${f.archived_pair_id}`,async route=>{if(route.request().method()==='PATCH')await held;await route.continue();});
    const toggle=()=>variantsPanel(page).getByRole('button',{name:/^Show archived/});
    const restoreButton=()=>variantsPanel(page).locator('.library-variant-archived').getByRole('button',{name:/^Restore/});
    await toggle().click();await expect(restoreButton()).toBeEnabled();
    await restoreButton().click();await expect.poll(()=>page.writes.length).toBe(1);await expect(restoreButton()).toBeDisabled();
    // Hiding and showing archived variants rebuilds the lane while the restore is in flight.
    await toggle().click();await toggle().click();
    await expect(restoreButton()).toBeDisabled();await restoreButton().evaluate(node=>node.click());
    release();
    await expect(variantsPanel(page).locator('.library-variant-archived')).toHaveCount(0);
    expect(page.writes).toEqual([{method:'PATCH',url:`/prompt_manager/pairs/${f.archived_pair_id}`,body:{archived:false}}]);
    expect((await pairRead(request,f.archived_pair_id)).archived).toBe(false);
});

test('archive hides a pair, Show archived finds it and Restore keeps it selected with links and counts unchanged',async({page,request})=>{
    const f=page.fixture;
    const evidence=async()=>{
        const pair=await pairRead(request,f.pair_id);const gallery=await(await request.get(`/prompt_manager/library/${f.positive_id}/images?limit=50`)).json();
        const history=await(await request.get(`/prompt_manager/library/${f.positive_id}/history`)).json();const parent=(await detail(request,f.positive_id,'all')).positive;
        return {counts:{images:pair.image_count,runs:pair.run_count},gallery:gallery.images.map(item=>item.id),history:history.recorded_run_count,parentImages:parent.image_count};
    };
    const before=await evidence();expect(before.counts).toEqual({images:1,runs:2});
    await openHistory(page,f.positive_id);await selectPair(page,'Select negative variant 2');
    await page.getByRole('button',{name:'Edit metadata',exact:true}).click();const dialog=editorDialog(page);await ready(dialog);
    await button(dialog,'Archive variant…').click();await expect(button(dialog,'Archive')).toBeEnabled();await button(dialog,'Archive').click();await expect(dialog).toHaveCount(0);
    await expect(toast(page)).toHaveText('Archived. Find it under Show archived in Negative variants.');await expect(page.locator('#workspace-main h1')).toBeFocused();
    await expect(variantsPanel(page).locator('.variant-lane')).toHaveCount(2);await expect(pressedVariant(page)).toHaveCount(1);
    await expect(savedTexts(page).getByRole('region',{name:'Negative prompt',exact:true})).not.toContainText(f.selected_negative);
    expect(await evidence()).toEqual(before);
    const toggle=variantsPanel(page).getByRole('button',{name:'Show archived (2)',exact:true});await expect(toggle).toHaveAttribute('aria-pressed','false');
    await toggle.click();await expect(toggle).toHaveAttribute('aria-pressed','true');
    const archived=variantsPanel(page).locator('.library-variant-archived');await expect(archived).toHaveCount(2);
    const lane=archived.filter({hasText:'· 2 runs · Archived'});await expect(lane).toHaveCount(1);
    const opacity=await lane.locator('.variant-dot').evaluate(node=>getComputedStyle(node).opacity);expect(Number(opacity)).toBeLessThan(1);
    const galleryReads=[];page.on('request',item=>{if(item.url().includes('/images?'))galleryReads.push(new URL(item.url()).searchParams);});
    await lane.locator('.variant-dot').click();await expect(lane.locator('.variant-dot')).toHaveAttribute('aria-pressed','true');
    await expect(savedTexts(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('p.prompt-text')).toHaveText(f.selected_negative);
    await expect(page.getByRole('region',{name:'Shared gallery',exact:true}).locator('article[data-image-key]')).toHaveCount(1);
    // #249 D8 ruling (a): the Library-embedded gallery has no select checkbox and no selection bar.
    await expect(page.getByRole('region',{name:'Shared gallery',exact:true}).locator('.image-overlays input[type="checkbox"]')).toHaveCount(0);
    await expect(page.locator('#bulkbar, .bulkbar')).toHaveCount(0);
    // D47: the archived pair's gallery is named by its "Shared gallery" heading, with no separate loaded count.
    await expect(page.getByRole('region',{name:'Shared gallery',exact:true}).locator('.panel-heading .small')).toHaveText(/ · Archived · same positive prompt$/);
    expect(galleryReads.at(-1).get('archive')).toBe('archived');expect(galleryReads.at(-1).get('pair_id')).toBe(String(f.pair_id));
    await expect(headingActions(page).getByRole('button',{name:/^Send prompt pair to ComfyUI/})).toBeEnabled();
    const writes=page.writes.length;
    await lane.getByRole('button',{name:/^Restore/}).click();
    await expect(variantsPanel(page).getByRole('button',{name:'Show archived (1)',exact:true})).toBeVisible();
    await expect(variantsPanel(page).locator('.library-variant-archived')).toHaveCount(1);
    await expect(pressedVariant(page)).toHaveCount(1);await expect(savedTexts(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('p.prompt-text')).toHaveText(f.selected_negative);
    expect(page.writes.slice(writes)).toEqual([{method:'PATCH',url:`/prompt_manager/pairs/${f.pair_id}`,body:{archived:false}}]);
    expect((await pairRead(request,f.pair_id)).archived).toBe(false);expect(await evidence()).toEqual(before);
    await expect(page.getByRole('region',{name:'Shared gallery',exact:true}).locator('article[data-image-key]')).toHaveCount(1);
    expect(galleryReads.at(-1).get('archive')).toBeNull();
});

test('filter hidden save resets shelf cursor and measured heights retaining anchor',async({page})=>{
    await page.goto(`${workspace}#/library`);await page.getByRole('searchbox',{name:'Search workspace',exact:true}).fill('Standalone');
    await expect(page.locator('article[data-prompt-id]')).toHaveCount(1);
    const shelfReads=[];page.on('request',item=>{const url=new URL(item.url());if(url.pathname==='/prompt_manager/library'&&item.method()==='GET')shelfReads.push(url.searchParams);});
    await page.getByRole('button',{name:'+ New prompt',exact:true}).click();const dialog=editorDialog(page);await ready(dialog);
    await field(dialog,'Title').fill('Hidden by filter');await field(dialog,'Positive prompt').fill('Lighthouse in heavy rain');await button(dialog,'Save').click();
    await expect(page).toHaveURL(/#\/library\/\d+$/);await expect(page.locator('#workspace-main h1')).toHaveText('Hidden by filter');
    await page.getByRole('button',{name:'← Prompt library',exact:true}).click();
    await expect(page.getByRole('searchbox',{name:'Search workspace',exact:true})).toHaveValue('Standalone');
    await expect(page.locator('article[data-prompt-id]')).toHaveCount(1);
    await expect(page.getByText('The saved prompt is hidden by the current filters.',{exact:false})).toBeVisible();
    expect(shelfReads.length).toBeGreaterThan(0);expect(shelfReads.every(params=>!params.has('cursor'))).toBe(true);
    expect(shelfReads.at(-1).get('text')).toBe('Standalone');
});

test('export downloads the full Library JSON without a toast and reports a failure before any download',async({page,request})=>{
    await page.addInitScript(()=>{window.revokedUrls=[];const revoke=URL.revokeObjectURL.bind(URL);URL.revokeObjectURL=url=>{window.revokedUrls.push(url);revoke(url);};});
    await page.goto(`${workspace}#/library`);await expect(page.locator('article[data-prompt-id]').first()).toBeVisible();
    const exportButton=page.getByRole('button',{name:'Export full Library JSON',exact:true});
    const [download]=await Promise.all([page.waitForEvent('download'),exportButton.click()]);
    expect(download.suggestedFilename()).toBe('prompt-manager-library-v1.json');
    const saved=JSON.parse(await (await import('node:fs/promises')).readFile(await download.path(),'utf8'));
    expect(saved).toEqual(await(await request.get('/prompt_manager/library/export')).json());
    expect(saved).toMatchObject({schema_version:1,kind:'prompt-manager-library'});
    await expect.poll(()=>page.evaluate(()=>window.revokedUrls.length)).toBe(1);await expect(toast(page)).toBeHidden();
    await page.route('**/prompt_manager/library/export',route=>route.fulfill({status:500,contentType:'application/json',body:'{"success":false,"error":"down"}'}));
    let downloads=0;page.on('download',()=>downloads++);await exportButton.click();
    await expect(page.getByRole('alert').filter({hasText:'The Library export could not be downloaded.'})).toBeVisible();
    expect(downloads).toBe(0);await expect(toast(page)).toBeHidden();expect(page.writes).toHaveLength(0);
});

const uniqueDotNames=async page=>{const names=await variantsPanel(page).locator('.variant-dot').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('aria-label')));expect(new Set(names).size).toBe(names.length);};

test('archived reuse is reported, shown under Show archived and selected read-only from Add negative and New',async({page})=>{
    const f=page.fixture;await openHistory(page,f.positive_id);
    const galleryReads=[];page.on('request',item=>{if(item.url().includes('/images?'))galleryReads.push(new URL(item.url()).searchParams);});
    await variantsPanel(page).getByRole('button',{name:'+ Add negative',exact:true}).click();let dialog=editorDialog(page);await ready(dialog);
    await field(dialog,'Negative prompt').fill(f.archived_negative);await button(dialog,'Save').click();await expect(dialog).toHaveCount(0);
    const message='This combination is an archived negative variant. It stays archived; restore it from Show archived in Negative variants.';
    await expect(toast(page)).toHaveText(message);
    await expect(variantsPanel(page).getByRole('button',{name:'Show archived (1)',exact:true})).toHaveAttribute('aria-pressed','true');
    const lane=variantsPanel(page).locator('.library-variant-archived');await expect(lane).toHaveCount(1);
    await expect(lane.locator('.variant-dot')).toHaveAttribute('aria-pressed','true');
    await expect(savedTexts(page).getByRole('region',{name:'Negative prompt',exact:true}).locator('p.prompt-text')).toHaveText(f.archived_negative);
    await expect.poll(()=>galleryReads.at(-1)?.get('archive')).toBe('archived');expect(galleryReads.at(-1).get('pair_id')).toBe(String(f.archived_pair_id));
    await uniqueDotNames(page);
    await page.goto(`${workspace}#/library`);await page.getByRole('button',{name:'+ New prompt',exact:true}).click();dialog=editorDialog(page);await ready(dialog);
    await field(dialog,'Title').fill(f.metadata.title);await field(dialog,'Positive prompt').fill(f.positive_text);await field(dialog,'Negative prompt (optional)').fill(f.archived_negative);
    await field(dialog,'Category').fill(f.metadata.category);await field(dialog,'Tags, separated by commas').fill(f.metadata.tags.join(', '));await field(dialog,'Notes').fill(f.metadata.notes);
    await button(dialog,'Save').click();await expect(page).toHaveURL(new RegExp(`#/library/${f.positive_id}$`));
    await expect(toast(page)).toHaveText(message);
    await expect(variantsPanel(page).locator('.library-variant-archived .variant-dot')).toHaveAttribute('aria-pressed','true');
    expect(page.writes.every(write=>write.method==='POST')).toBe(true);
});

test('reuse that leaves parent metadata unapplied discloses it and keeps the draft for Edit metadata',async({page,request})=>{
    const f=page.fixture;await page.goto(`${workspace}#/library`);
    await page.getByRole('button',{name:'+ New prompt',exact:true}).click();let dialog=editorDialog(page);await ready(dialog);
    await field(dialog,'Title').fill('A different title');await field(dialog,'Positive prompt').fill(f.positive_text);await field(dialog,'Negative prompt (optional)').fill('brand new negative');
    await field(dialog,'Notes').fill('Draft notes');await button(dialog,'Save').click();
    await expect(page).toHaveURL(new RegExp(`#/library/${f.positive_id}$`));
    await expect(toast(page)).toHaveText(UNAPPLIED);
    await expect(page.locator('#workspace-main h1')).toHaveText(f.metadata.title);
    await page.getByRole('button',{name:'Edit metadata',exact:true}).click();dialog=editorDialog(page);await ready(dialog);
    await expect(field(dialog,'Title')).toHaveValue('A different title');await expect(field(dialog,'Notes')).toHaveValue('Draft notes');
    await button(dialog,'Cancel').click();
    await page.getByRole('button',{name:'Edit metadata',exact:true}).click();dialog=editorDialog(page);await ready(dialog);
    await expect(field(dialog,'Title')).toHaveValue(f.metadata.title);await button(dialog,'Cancel').click();
    expect(page.writes).toHaveLength(1);
    // D153: neither the reuse nor the kept draft touches the stored rating.
    expect((await detail(request,f.positive_id)).positive.rating).toBe(4);
});

test('reuse with the stored parent metadata entered does not claim it was not applied',async({page})=>{
    const f=page.fixture;await page.goto(`${workspace}#/library`);
    await page.getByRole('button',{name:'+ New prompt',exact:true}).click();let dialog=editorDialog(page);await ready(dialog);
    await field(dialog,'Title').fill(f.metadata.title);await field(dialog,'Positive prompt').fill(f.positive_text);await field(dialog,'Negative prompt (optional)').fill('another brand new negative');
    await field(dialog,'Category').fill(f.metadata.category);await field(dialog,'Tags, separated by commas').fill(f.metadata.tags.join(', '));
    await field(dialog,'Notes').fill(f.metadata.notes);
    await button(dialog,'Save').click();await expect(page).toHaveURL(new RegExp(`#/library/${f.positive_id}$`));
    await expect(toast(page)).toHaveText('Saved as a new negative variant of an existing prompt.');
    await page.getByRole('button',{name:'Edit metadata',exact:true}).click();dialog=editorDialog(page);await ready(dialog);
    await expect(field(dialog,'Title')).toHaveValue(f.metadata.title);await button(dialog,'Cancel').click();
    expect(page.writes).toHaveLength(1);
});

test('reuse of an uncategorized prompt with the category left blank is not unapplied metadata, a different category is',async({page,request})=>{
    const f=page.fixture;const current=await detail(request,f.positive_id,'active');
    await expect(await request.patch(`/prompt_manager/library/${f.positive_id}`,{data:{expected_epoch:current.library_epoch,if_match:current.positive.edit_token,metadata:{category:''}}})).toBeOK();
    expect((await detail(request,f.positive_id)).positive.category).toBeNull();
    const reuse=async(category,negative)=>{
        await page.goto(`${workspace}#/library`);await page.getByRole('button',{name:'+ New prompt',exact:true}).click();const dialog=editorDialog(page);await ready(dialog);
        await field(dialog,'Title').fill(f.metadata.title);await field(dialog,'Positive prompt').fill(f.positive_text);await field(dialog,'Negative prompt (optional)').fill(negative);
        await field(dialog,'Category').fill(category);await field(dialog,'Tags, separated by commas').fill(f.metadata.tags.join(', '));
        await field(dialog,'Notes').fill(f.metadata.notes);
        await button(dialog,'Save').click();await expect(page).toHaveURL(new RegExp(`#/library/${f.positive_id}$`));
    };
    await reuse('','blank category negative');
    await expect(toast(page)).toHaveText('Saved as a new negative variant of an existing prompt.');
    await reuse('Landscapes','different category negative');
    await expect(toast(page)).toHaveText(UNAPPLIED);
    expect(page.writes.map(write=>`${write.method} ${write.url}`)).toEqual(['POST /prompt_manager/library','POST /prompt_manager/library']);
});

test('turning Show archived on again after a hidden archive shows the newly archived pair',async({page})=>{
    const f=page.fixture;await openHistory(page,f.positive_id);
    const toggle=()=>variantsPanel(page).getByRole('button',{name:/^Show archived/});
    await toggle().click();await expect(variantsPanel(page).locator('.library-variant-archived')).toHaveCount(1);
    await toggle().click();await expect(toggle()).toHaveAttribute('aria-pressed','false');await expect(variantsPanel(page).locator('.library-variant-archived')).toHaveCount(0);
    await selectPair(page,'Select negative variant 2');
    await page.getByRole('button',{name:'Edit metadata',exact:true}).click();const dialog=editorDialog(page);await ready(dialog);
    await button(dialog,'Archive variant…').click();await expect(button(dialog,'Archive')).toBeEnabled();await button(dialog,'Archive').click();await expect(dialog).toHaveCount(0);
    await expect(toggle()).toHaveText('Show archived (2)');await toggle().click();
    await expect(variantsPanel(page).locator('.library-variant-archived')).toHaveCount(2);
    await expect(laneLabel(page,'· 2 runs · Archived')).toHaveCount(1);await uniqueDotNames(page);
});

test('the comparison reports a wrong role color, a wrong field color and a removed Save control',async({page})=>{
    const f=page.fixture;await mountEditor(page,'dark');
    const dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    // Work on a copy of the production dialog so the live editor is untouched; ids are renamed to keep label/list wiring valid.
    await dialog.evaluate(node=>{
        const copy=node.cloneNode(true);copy.removeAttribute('open');copy.id='mutant';copy.classList.add('mutant');
        const ids=new Set([...copy.querySelectorAll('[id]')].map(item=>item.id));
        for(const item of copy.querySelectorAll('[id]'))item.id=`${item.id}-copy`;
        for(const item of copy.querySelectorAll('[for],[list],[aria-labelledby],[aria-describedby]'))for(const attr of ['for','list','aria-labelledby','aria-describedby']) {
            const value=item.getAttribute(attr);if(value&&ids.has(value))item.setAttribute(attr,`${value}-copy`);
        }
        copy.setAttribute('open','');node.parentElement.append(copy);
    });
    const mutant=page.locator('dialog.mutant');await expect(mutant).toBeVisible();
    const baseline=await mutant.evaluate(inventoryOf);
    expect(diffInventory(baseline,await mutant.evaluate(inventoryOf))).toEqual([]);
    await page.addStyleTag({content:'dialog.mutant .danger{color:rgb(1,2,3)!important}dialog.mutant .field input{color:rgb(4,5,6)!important;background-color:rgb(7,8,9)!important}'});
    await mutant.getByRole('button',{name:'Save',exact:true}).evaluate(node=>node.remove());
    const paths=diffInventory(baseline,await mutant.evaluate(inventoryOf)).map(item=>item.path);
    expect(paths).toContain('actions.buttons.length');
    expect(paths).toContain('actions.buttons.1.color');
    expect(paths).toContain('fields.0.control.color');
    expect(paths).toContain('fields.0.control.backgroundColor');
    await page.keyboard.press('Escape');
});

test('long and error states retain footer focus and reflow',async({page})=>{
    await page.setViewportSize({width:390,height:844});
    await mountEditor(page,'light');
    const dialog=await openEditor(page,{mode:'new'});await ready(dialog);
    const unbroken='W'.repeat(240);
    await field(dialog,'Title').fill(`A very long title that keeps going ${unbroken} and going`);
    await field(dialog,'Positive prompt').fill('A quiet harbor at dawn');
    await field(dialog,'Notes').fill(Array.from({length:60},(_,index)=>`Long note line ${index} ${unbroken.slice(0,40)}`).join('\n'));
    await page.route('**/prompt_manager/library',route=>route.request().method()==='POST'
        ?route.fulfill({status:400,contentType:'application/json',body:'{"success":false,"error":"Title is too long.","code":null}'}):route.continue(),{times:1});
    await button(dialog,'Save').click();
    await expect(note(dialog)).toHaveText('Not saved: Title is too long. Your entries are kept.');
    // Save had focus when the write started and keeps it after the failure; focus never falls out of the modal.
    await expect(button(dialog,'Save')).toBeFocused();
    expect(await dialog.evaluate(node=>node.contains(document.activeElement))).toBe(true);
    const state=await dialog.evaluate(node=>({scrolls:node.scrollHeight>node.clientHeight,height:node.getBoundingClientRect().height,innerHeight,overflowY:getComputedStyle(node).overflowY,
        pageOverflow:document.documentElement.scrollWidth-document.documentElement.clientWidth,dialogOverflow:node.scrollWidth-node.clientWidth,
        left:node.getBoundingClientRect().left,right:node.getBoundingClientRect().right,viewport:innerWidth}));
    expect(state.scrolls).toBe(true);expect(state.height).toBeLessThanOrEqual(state.innerHeight*.9+1);expect(['auto','scroll']).toContain(state.overflowY);
    expect(state.pageOverflow).toBeLessThanOrEqual(1);expect(state.dialogOverflow).toBeLessThanOrEqual(1);expect(state.left).toBeGreaterThanOrEqual(0);expect(state.right).toBeLessThanOrEqual(state.viewport);
    for(const name of ['Cancel','Save']) {
        const item=button(dialog,name);await item.scrollIntoViewIfNeeded();await expect(item).toBeVisible();await expect(item).toBeEnabled();
        await item.focus();await expect(item).toBeFocused();
        const box=await item.boundingBox();expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(390);
    }
    // Keyboard: Tab from Cancel reaches Save inside the dialog; the draft survives the error.
    await button(dialog,'Cancel').focus();await page.keyboard.press('Tab');await expect(button(dialog,'Save')).toBeFocused();
    await expect(field(dialog,'Positive prompt')).toHaveValue('A quiet harbor at dawn');
    expect(page.writes.filter(write=>write.method==='POST')).toHaveLength(1);
    await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});

// Task A2: the editor dialogs keep matching the concept on the concept base stylesheet, with library-editor.css retired.
const CONCEPT_BASE=['js/shared/toolkit/controls.css','lib/viewerjs/viewer.min.css','js/workspace/concept.css',
    'js/workspace/workspace.css'];
for(const [width,height,theme] of [[1600,1100,'dark'],[390,844,'light']])test(`concept base keeps the tags screen and editor dialogs equal to the concept ${width} ${theme}`,async({page})=>{
    await page.setViewportSize({width,height});const f=page.fixture;const references={};
    for(const mode of Object.keys(conceptModes)) {
        const [hash,openMode]=conceptModes[mode];
        await page.goto('about:blank');await page.goto(`/__test__/canonical/workspace#${hash}`);
        await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);await openMode(page);await expect(page.locator('#modal')).toBeVisible();
        if(mode==='revision')await page.locator('#modal-close').focus();
        references[mode]=mapConcept(await page.locator('#modal').evaluate(inventoryOf));
    }
    await mountEditor(page,theme);
    expect(await page.evaluate(()=>[...document.styleSheets].filter(sheet=>sheet.href).map(sheet=>new URL(sheet.href).pathname.replace(/^\/prompt_manager\//,'')))).toEqual(CONCEPT_BASE);
    const options={new:{mode:'new'},metadata:{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id},revision:{mode:'revision',positiveId:f.positive_id,pairId:f.pair_id},negative:{mode:'negative',positiveId:f.positive_id}};
    for(const mode of Object.keys(conceptModes)) {
        const dialog=await openEditor(page,options[mode]);await ready(dialog);
        expect(diffInventory(references[mode],await dialog.evaluate(inventoryOf)),`${mode} dialog`).toEqual([]);
        await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
    }
});
