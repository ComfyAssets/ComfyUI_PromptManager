import {writeFile} from 'node:fs/promises';
import {startCoverage,saveCoverage} from '../../tools/vendor/browser-coverage.mjs';
import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import {captureCanonicalState} from './history-canonical-comparison.mjs';

// The editor is mounted through a test-only page import until Task 9 wires its entry actions.
test.use({locale:'en-US',timezoneId:'UTC',reducedMotion:'reduce'});
const workspace='/prompt_manager/workspace';
const ACTIONS={new:['Cancel','Save'],metadata:['Create text revision','Archive…','Cancel','Save'],revision:['Cancel','Save'],negative:['Cancel','Save']};
const LABELS=['Title','Positive prompt','Negative prompt (optional)','Category','Tags, separated by commas','Rating','Notes'];
const RATINGS=[['5','5 / 5'],['4','4 / 5'],['3','3 / 5'],['2','2 / 5'],['1','1 / 5'],['0','Unrated']];
const editorDialog=page=>page.locator('dialog.library-editor');

test.beforeEach(async({page,request,browserName})=>{
    await startCoverage(page,browserName);
    const reset=await request.post('/__test__/library-editor/reset',{data:{}});await expect(reset).toBeOK();page.fixture=await reset.json();
    page.editorErrors=[];page.on('pageerror',error=>page.editorErrors.push(error.message));
    page.writes=[];page.on('request',item=>{if(item.method()!=='GET')page.writes.push({method:item.method(),url:new URL(item.url()).pathname,body:item.postDataJSON()});});
});
test.afterEach(async({page,browserName})=>{await saveCoverage(page,browserName);expect(page.editorErrors).toEqual([]);});

async function mountEditor(page,theme) {
    await page.goto(`${workspace}#/overview`);await expect(page.locator('#workspace-main h1')).toHaveText('Overview');
    if(theme)await page.getByRole('combobox',{name:'Appearance',exact:true}).selectOption(theme);
    await page.evaluate(async()=>{
        const trigger=document.createElement('button');trigger.type='button';trigger.id='editor-trigger';trigger.textContent='Open editor';document.body.append(trigger);
        window.editorEvents={committed:[],reconciled:[]};
        try {
            const {createLibraryEditor,showWorkspaceToast}=await import('/prompt_manager/js/workspace/library-editor.mjs');
            const {createScope}=await import('/prompt_manager/js/shared/toolkit/core.js');
            const scope=createScope();
            const editor=createLibraryEditor({api:window.ApiClient.createApiClient(),parentScope:scope,
                onCommitted:commit=>window.editorEvents.committed.push(commit),onReconciled:result=>window.editorEvents.reconciled.push(result)});
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
    const dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});
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
    await expect(field(dialog,'Rating')).toHaveValue('4');
    await expect(field(dialog,'Notes')).toHaveValue(f.metadata.notes);
    expect(await field(dialog,'Rating').locator('option').evaluateAll(nodes=>nodes.map(node=>[node.value,node.textContent]))).toEqual(RATINGS);
    expect(await field(dialog,'Category').evaluate(node=>[...node.list.options].map(option=>option.value))).toEqual(['Portraits','Sketches']);
    expect(await field(dialog,'Tags, separated by commas').evaluate(node=>[...node.list.options].map(option=>option.value))).toEqual(['draft,','studio,','window light,']);
    await expect(dialog.locator('[placeholder]')).toHaveCount(0);
    await expect(dialog.locator('.demo-note')).toHaveCount(0);
    expect(await actionNames(dialog)).toEqual(ACTIONS.metadata);
    await expect(button(dialog,'Archive…')).toHaveClass(/\bdanger\b/);await expect(button(dialog,'Save')).toHaveClass(/\bca-primary\b/);
    await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
    await expect(page.locator('#editor-trigger')).toBeFocused();expect(page.writes).toHaveLength(0);
});

test('editor modes persist through reload with selected saved negative',async({page,request})=>{
    const f=page.fixture;await mountEditor(page);
    let dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await field(dialog,'Title').fill('Edited portrait');await field(dialog,'Category').fill('');
    await field(dialog,'Tags, separated by commas').fill('studio, moody');await field(dialog,'Rating').selectOption('0');
    await field(dialog,'Notes').fill('Edited notes');await button(dialog,'Save').click();
    await expect(dialog).toHaveCount(0);
    expect(page.writes.map(write=>`${write.method} ${write.url}`)).toEqual([`PATCH /prompt_manager/library/${f.positive_id}`]);
    expect(page.writes[0].body.metadata).toEqual({title:'Edited portrait',category:'',tags:['studio','moody'],rating:null,notes:'Edited notes'});
    await expect.poll(()=>page.evaluate(()=>window.editorEvents.reconciled.length)).toBe(1);
    const saved=(await detail(request,f.positive_id)).positive;
    expect(saved).toMatchObject({title:'Edited portrait',category:null,rating:null,notes:'Edited notes'});expect([...saved.tags].sort()).toEqual(['moody','studio']);

    await page.reload();await mountEditor(page);
    dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await expect(field(dialog,'Title')).toHaveValue('Edited portrait');await expect(field(dialog,'Category')).toHaveValue('');
    await expect(field(dialog,'Rating')).toHaveValue('0');await expect(field(dialog,'Negative prompt (optional)')).toHaveValue(f.selected_negative);
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
        parent_metadata:{title:'Edited portrait',category:'Uncategorized',rating:null,notes:'Edited notes'}});
    expect([...create.body.parent_metadata.tags].sort()).toEqual(['moody','studio']);
    await expect.poll(()=>page.evaluate(()=>window.editorEvents.reconciled.length)).toBe(1);
    const revision=await page.evaluate(()=>window.editorEvents.committed.at(-1).receipt);
    expect(revision).toMatchObject({positive_created:true,pair_created:true});expect(revision.positive.id).not.toBe(f.positive_id);
    expect((await detail(request,revision.positive.id)).positive).toMatchObject({title:'Edited portrait',category:'Uncategorized'});

    dialog=await openEditor(page,{mode:'new'});await expect(dialog.getByRole('heading',{name:'New prompt',exact:true})).toBeVisible();await ready(dialog);
    expect(await actionNames(dialog)).toEqual(ACTIONS.new);
    for(const label of ['Title','Positive prompt'])await expect(field(dialog,label)).toHaveJSProperty('required',true);
    const before=page.writes.length;await button(dialog,'Save').click();
    await expect(field(dialog,'Title')).toBeFocused();expect(await field(dialog,'Title').evaluate(node=>node.validity.valueMissing)).toBe(true);
    expect(page.writes).toHaveLength(before);
    await field(dialog,'Title').fill('Fresh idea');await field(dialog,'Positive prompt').fill('A fresh lighthouse at dawn');
    await field(dialog,'Rating').selectOption('5');await button(dialog,'Save').click();await expect(dialog).toHaveCount(0);
    expect(page.writes.at(-1).body).toEqual({expected_epoch:expect.any(String),positive_text:'A fresh lighthouse at dawn',negative_text:'',
        parent_metadata:{title:'Fresh idea',category:'Uncategorized',tags:[],rating:5,notes:''}});

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
});

test('archive confirms the selected pair with fresh counts and writes only on confirm',async({page,request})=>{
    const f=page.fixture;await mountEditor(page);
    let dialog=await openEditor(page,{mode:'metadata',positiveId:f.standalone_id,pairId:null});await ready(dialog);
    const archive=button(dialog,'Archive…');await expect(archive).toBeDisabled();
    await expect(archive).toHaveAccessibleDescription('Archive needs a selected saved negative variant.');
    await expect(note(dialog)).toHaveText('Archive needs a selected saved negative variant.');
    await button(dialog,'Cancel').click();await expect(dialog).toHaveCount(0);await expect(page.locator('#editor-trigger')).toBeFocused();

    dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await button(dialog,'Archive…').click();
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
    await button(dialog,'Archive…').click();await expect(button(dialog,'Archive')).toBeEnabled();
    await button(dialog,'Archive').click();await expect(dialog).toHaveCount(0);
    expect(page.writes).toEqual([{method:'PATCH',url:`/prompt_manager/pairs/${f.pair_id}`,body:{archived:true}}]);
    expect((await pairRead(request,f.pair_id)).archived).toBe(true);
    await expect.poll(()=>page.evaluate(()=>window.editorEvents.reconciled.at(-1)?.commit.kind)).toBe('archive-pair');
});

test('an archive conflict offers an explicit Reload that re-reads and never replays the archive',async({page,request})=>{
    const f=page.fixture;await mountEditor(page);
    let conflicted=false;
    await page.route(`**/prompt_manager/pairs/${f.pair_id}`,route=>{
        if(route.request().method()!=='PATCH'||conflicted)return route.continue();
        conflicted=true;return route.fulfill({status:409,contentType:'application/json',body:'{"success":false,"error":"changed","code":"metadata-conflict"}'});
    });
    const dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.pair_id});await ready(dialog);
    await button(dialog,'Archive…').click();await expect(button(dialog,'Archive')).toBeEnabled();
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
    for(const name of ['Save','Cancel','Close dialog','Create text revision','Archive…'])await expect(button(dialog,name)).toBeDisabled();
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
    const restore=await request.post('/prompt_manager/restore',{multipart:{database_file:{name:'editor.db',mimeType:'application/octet-stream',buffer:await snapshot.body()}}});
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
    await expect(button(dialog,'Save')).toBeDisabled();
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
    await expect(button(dialog,'Archive…')).toBeDisabled();await expect(note(dialog)).toHaveText('This negative variant is already archived.');
    await expect(button(dialog,'Archive…')).toHaveAccessibleDescription('This negative variant is already archived.');
    await button(dialog,'Cancel').click();

    let failed=true;
    await page.route(`**/prompt_manager/pairs/${f.empty_pair_id}`,route=>{if(!failed&&route.request().method()==='GET'){failed=true;return route.fulfill({status:500,contentType:'application/json',body:'{"success":false,"error":"down"}'});}return route.continue();});
    dialog=await openEditor(page,{mode:'metadata',positiveId:f.positive_id,pairId:f.empty_pair_id});await ready(dialog);
    await expect(field(dialog,'Negative prompt (optional)')).toHaveValue('');
    failed=false;await button(dialog,'Archive…').click();
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

function inventoryOf(dialog) {
    const css=(node,...keys)=>{const style=getComputedStyle(node);return Object.fromEntries(keys.map(key=>[key,style[key]]));};
    const header=dialog.querySelector('.dialog-header'),title=header.querySelector('h2'),close=header.querySelector('button');
    const actions=dialog.querySelector('.dialog-actions'),twoCol=dialog.querySelector('.two-col');
    return {
        dialog:{...css(dialog,'paddingTop','paddingRight','paddingBottom','paddingLeft','borderTopLeftRadius','borderTopWidth','borderTopStyle','maxHeight','overflowY','backgroundColor','color','borderTopColor','fontFamily','fontSize','lineHeight'),
            width:dialog.getBoundingClientRect().width,modal:dialog.matches(':modal'),backdrop:getComputedStyle(dialog,'::backdrop').backgroundColor,
            labelledBy:document.getElementById(dialog.getAttribute('aria-labelledby'))===title},
        header:css(header,'display','justifyContent','alignItems','columnGap','marginBottom'),
        title:{text:title.textContent,...css(title,'fontSize','fontWeight','marginTop','marginBottom','lineHeight')},
        close:{label:close.getAttribute('aria-label'),text:close.textContent.trim(),focused:document.activeElement===close,...css(close,'fontSize','minWidth','minHeight')},
        fields:[...dialog.querySelectorAll('.field')].map(field=>{
            const label=field.querySelector('label'),control=field.querySelector('input,textarea,select');
            return {label:label.textContent,labelled:label.htmlFor===control.id,tag:control.tagName.toLowerCase(),readOnly:Boolean(control.readOnly),required:control.required,
                placeholder:control.getAttribute('placeholder'),listed:Boolean(control.list),commaOptions:control.list?[...control.list.options].every(option=>option.value.endsWith(',')):null,
                options:control.tagName==='SELECT'?[...control.options].map(option=>[option.value,option.textContent]):null,
                field:css(field,'display','rowGap','fontSize','marginTop','marginBottom'),control:css(control,'fontSize','minHeight','resize')};
        }),
        twoCol:twoCol?{columns:getComputedStyle(twoCol).gridTemplateColumns.split(' ').length,...css(twoCol,'display','columnGap')}:null,
        small:[...dialog.querySelectorAll('p.small')].map(node=>({text:node.textContent,...css(node,'fontSize','color')})),
        actions:{...css(actions,'display','justifyContent','columnGap','flexWrap','marginTop'),
            buttons:[...actions.querySelectorAll('button')].map(node=>({text:node.textContent.trim(),type:node.type,danger:node.classList.contains('danger'),primary:node.classList.contains('ca-primary'),
                ...css(node,'minHeight','fontSize','color','borderTopColor','backgroundColor')}))},
        demo:Boolean(dialog.querySelector('.demo-note')),
    };
}
// Documented differences only: prototype Save label, prototype demo note, and data-dependent values.
function mapConcept(reference) {
    return {...reference,demo:false,actions:{...reference.actions,buttons:reference.actions.buttons.map(item=>item.text==='Save in prototype'?{...item,text:'Save'}:item)}};
}

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
