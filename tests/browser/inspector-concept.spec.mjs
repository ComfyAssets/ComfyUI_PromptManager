import {writeFile} from 'node:fs/promises';
import {test,expect} from './fixtures.mjs';
import {captureCanonicalState} from './history-canonical-comparison.mjs';
import {SIZES,THEMES,seedConcept,openConcept,openProduction,inspectorOf,diffInventory,mutate,countCopy,saveImageTags} from './concept-inventory.mjs';

// Task D4 of #249: the Images side panel (app.js imageDetails a400–414) and the viewer's full details (imageDetails(i,
// true)) against the immutable concept, on the concept-shaped real fixture, at 1600x1100 and 390x844 in both themes.
// Both documents select the first listed image, alpine_00001.png (D19: the first load selects it). The fixture links it
// to a recipe without a category or tags, so (D39, ruled 2026-10-21) its panel falls back to the positive's own category
// and tags, as the concept's does. The panel's box in the gallery layout is the Images spec's. Paired captures are
// artifacts only; there are no PNG baselines.
test.use({locale:'en-US',timezoneId:'UTC'});

const CARDS='.gallery-body > .image-grid > .media-card';
const TITLE='Alpine observatory';
const FIXTURE_IMAGES=24;
// The concept's sticky panel never scrolls; production's scrolls inside the window above 700px (D20/D21 side panel).
const PANEL_INSET=40;
// D34: coarse pointers keep 40px targets for the viewer details' disclosure summaries and "Download original" (the
// concept's coarse rule covers buttons only). The recorded rule, applied to the concept under the same emulation so the
// browser lays out what follows the taller summaries; a fine pointer never matches it.
const D34_COARSE_TARGETS=`@media (pointer: coarse) {
    #viewer-details summary { box-sizing: border-box; min-block-size: 40px; padding-block: 10px; }
    #viewer-details a { display: inline-flex; align-items: center; min-block-size: 40px; }
}`;
// What an open "File & provenance" offers beyond the concept's text-only disclosure: D38's Show in folder right after
// the Path value, then D20's Download original.
const FILE_CONTROLS=[{tag:'button',text:'Show in folder',name:'Show alpine_00001.png in folder'},{tag:'a',text:'Download original',name:'Download original'}];

/** D22: production's confirmed prompt text is focusable, so it follows its block's actions among the controls. */
function withFocusablePrompts(inventory) {
    const controls=[...inventory.controls];
    for(const section of inventory.sections) {
        const send=section.actions.buttons.at(-1);
        const index=controls.findIndex(control=>control.name===send.name);
        controls.splice(index+1,0,{tag:'p',text:section.text.text,name:section.text.text,pressed:null,primary:false,minHeight:'0px'});
    }
    return {...inventory,controls,sections:inventory.sections.map(section=>({...section,text:{...section.text,tabIndex:0}}))};
}

/** D21: the category chip names its scope in a visually hidden prefix; here the linked positive's (D39 fallback). */
function withCategoryScope(inventory) {
    const [badge,category,...rest]=inventory.badges.chips;
    return {...inventory,badges:{...inventory.badges,chips:[badge,{...category,hidden:'Positive prompt category: '},...rest]}};
}

/**
 * Recorded inspector deviations only (spec §8, TESTING.md D3, D20–D22, D34 and D38); everything else must equal
 * production. `reference.viewerPlain` is the concept's own full details
 * with its tag chips made plain (D20) and, under a coarse pointer, D34's targets, both at runtime: the browser lays out
 * what follows them, as the Images spec's removed toolbar items do.
 */
export function mapInspectorConcept(reference,{viewport}) {
    const {panel,viewerPlain,fileControls}=reference;
    return {
        panel:withCategoryScope(withFocusablePrompts({...panel,
            // D3: the production-only inspector rules (`.file-inspector`, workspace.css) hook on this class.
            className:`${panel.className} file-inspector`,
            // D20/D21 side panel: the sticky panel scrolls inside the window, so long real prompts keep the footer
            // reachable; at 700px and below, where the concept unsticks it, it grows instead.
            overflowX:'auto',overflowY:'auto',maxHeight:viewport.width>700?`${viewport.height-PANEL_INSET}px`:'none'})),
        // D3 as above; D20: the viewer's tag chips are plain (the concept's go to the Library).
        viewerDetails:withCategoryScope(withFocusablePrompts({...viewerPlain,className:'file-inspector'})),
        // D38 and D20: the open "File & provenance" offers Show in folder and Download original.
        fileControls:[...fileControls,...FILE_CONTROLS],
    };
}

function compare(reference,actual,viewport) {
    return diffInventory(mapInspectorConcept(reference,{viewport}),actual);
}

/** The comparison as a gate: throws, naming every differing path, unless production equals the mapped concept. */
function assertInspectorMatch(reference,actual,viewport) {
    const diff=compare(reference,actual,viewport);
    if(diff.length)throw new Error(`the inspector differs from the concept at ${diff.map(item=>item.path).join(', ')}`);
}

// Two frames, then every running transition finished.
const settle=page=>page.evaluate(async()=>{
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    await Promise.race([Promise.all(document.getAnimations().map(animation=>animation.finished.catch(()=>{}))),
        new Promise(resolve=>setTimeout(resolve,2000))]);
});
const panelOf=page=>page.locator('#image-inspector');

/**
 * The concept with every fixture image loaded through its own Load more (it loads 12 per batch, D37), as production
 * has them: where the panel follows the grid (390px), reaching it would otherwise load the batch mid-comparison.
 */
async function openConceptPanel(page,theme) {
    await openConcept(page,'images',theme);await page.mouse.move(0,0);
    if(await page.locator(CARDS).count()<FIXTURE_IMAGES)await page.getByRole('button',{name:'Load more images',exact:true}).dispatchEvent('click');
    await expect(page.locator(CARDS)).toHaveCount(FIXTURE_IMAGES);
    await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo(0,0);});
    await expect(panelOf(page).locator('.panel-heading h2')).toHaveText(TITLE);
    await settle(page);
}
async function openProductionPanel(page,theme) {
    await openProduction(page,'images',theme);await page.mouse.move(0,0);
    await expect(page.locator(CARDS).first()).toBeVisible();
    await expect(panelOf(page).locator('.panel-heading h2')).toHaveText(TITLE);
    await expect(panelOf(page).locator('.detail-footer')).toBeVisible();
    await expect(panelOf(page).locator('.detail-inner > .tags')).toHaveCount(2);
    await settle(page);
}

/** The concept's tag chips in the open viewer as plain `span.chip`s (D20) and D34's coarse targets; returns an undo. */
async function plainViewerChips(page) {
    const restoreTargets=await mutate(page,{css:D34_COARSE_TARGETS});
    await page.evaluate(()=>{
        window.__plainChips=[...document.querySelectorAll('#viewer-details .tags > button.chip')].map(button=>{
            const span=document.createElement('span');span.className='chip';span.textContent=button.textContent;
            button.replaceWith(span);return [span,button];
        });
    });
    await settle(page);
    return async()=>{
        await page.evaluate(()=>{for(const [span,button] of window.__plainChips)span.replaceWith(button);delete window.__plainChips;});
        await restoreTargets();
    };
}

/** The controls an open "File & provenance" offers between its summary and the next disclosure's (m5). */
async function fileControlsOf(page) {
    const disclosure=page.locator('#viewer-details details').first();
    await disclosure.evaluate(node=>{node.open=true;});await settle(page);
    const {controls}=(await inspectorOf(page)).viewerDetails;
    await disclosure.evaluate(node=>{node.open=false;});await settle(page);
    const names=controls.map(control=>control.name);
    return controls.slice(names.indexOf('File & provenance')+1,names.indexOf('Workflow & raw metadata')).map(({tag,text,name})=>({tag,text,name}));
}

/**
 * The first card's viewer: its full details and open "File & provenance" measured (in the concept also with plain
 * chips), then closed again. `mutation` (production) is applied once the viewer is open and undone before it closes.
 */
async function viewerOf(page,concept,mutation=null) {
    await page.locator(CARDS).first().locator('.image-open').click();
    await expect(page.locator('#viewer-dialog[open] #viewer-details .panel-heading h2')).toHaveText(TITLE);
    await settle(page);
    const restoreMutation=mutation?await mutate(page,mutation):null;
    if(mutation)await settle(page);
    const {viewerDetails}=await inspectorOf(page);
    const fileControls=await fileControlsOf(page);
    let viewerPlain=null;
    if(concept) {const restore=await plainViewerChips(page);viewerPlain=(await inspectorOf(page)).viewerDetails;await restore();}
    await restoreMutation?.();
    await page.keyboard.press('Escape');
    await expect(page.locator('#viewer-dialog[open]')).toHaveCount(0);
    // The concept re-renders the Images page on the viewer's `close` event (app.js updateImages): let it land first.
    await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,0)));await settle(page);
    return {viewerDetails,viewerPlain,fileControls};
}
async function inventory(page,concept=false) {
    const {panel}=await inspectorOf(page);
    const {viewerDetails,viewerPlain,fileControls}=await viewerOf(page,concept);
    return concept?{panel,viewerDetails,viewerPlain,fileControls}:{panel,viewerDetails,fileControls};
}

/** A ComfyUI host in a second tab that acknowledges what it applies (the shipped prompt bridge). */
async function fakeHost(context) {
    const host=await context.newPage();await host.goto('/prompt_manager/workspace#/overview');
    await host.evaluate(async()=>{
        const {createBridgeHost}=await import('/prompt_manager/js/shared/toolkit/prompt-bridge.js');
        window.hostValues={positive:'Existing positive',negative:'Existing negative'};window.applied=[];
        const target={id:'pair-1',label:'My prompt pair',type:'PromptManagerPair',roles:['positive','negative']};
        window.fixtureHost=createBridgeHost({origin:location.origin,namespace:'/',label:'Test ComfyUI',adapter:{
            listTargets:()=>[target],
            preview:(_id,roles)=>({preview:{target,fields:Object.entries(roles).map(([role,after])=>({role,before:window.hostValues[role],after}))},snapshot:roles}),
            apply:roles=>{Object.assign(window.hostValues,roles);window.applied.push(roles);return {targetId:target.id,values:roles};},
        }});
    });
    return host;
}

/** Copy the positive: the concept's outcome is the toast, or its "Copy prompt" dialog where the clipboard is refused. */
async function copyOf(page) {
    // An earlier toast (the concept's Load more) is not the copy's outcome.
    await panelOf(page).locator('.prompt-actions').first().scrollIntoViewIfNeeded();await settle(page);
    await page.evaluate(()=>document.querySelectorAll('.toast').forEach(toast=>{toast.textContent='';}));
    await panelOf(page).getByRole('button',{name:`Copy positive prompt: ${TITLE}`,exact:true}).click();
    const dialog=page.getByRole('dialog',{name:'Copy prompt',exact:true});
    await expect.poll(async()=>(await page.locator('.toast').textContent())||(await dialog.count()?'dialog':'')).not.toBe('');
    const result={toast:await page.locator('.toast').textContent(),dialog:await dialog.count()>0};
    if(result.dialog){await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);}
    return result;
}

/** The concept's send: its dialog previews both roles (app.js openSend). */
async function conceptSendOf(page) {
    await panelOf(page).getByRole('button',{name:`Send prompt pair to ComfyUI: ${TITLE}`,exact:true}).click();
    const modal=page.locator('dialog#modal[open]');
    await expect(modal.locator('#modal-title')).toHaveText('Send to ComfyUI');
    const previews=await modal.locator('.send-preview').evaluateAll(nodes=>nodes.map(node=>[node.querySelector('h3').textContent,node.querySelector('p').textContent]));
    await page.keyboard.press('Escape');await expect(modal).toHaveCount(0);
    return previews;
}

/** Production's send: the acknowledged sender, previewing and applying both roles to a chosen node. */
async function productionSendOf(page,context) {
    const host=await fakeHost(context);
    try {
        await panelOf(page).getByRole('button',{name:`Send prompt pair to ComfyUI: ${TITLE}`,exact:true}).click();
        const sender=page.getByRole('dialog',{name:'Send to ComfyUI',exact:true});await expect(sender).toBeVisible();
        const windows=sender.getByRole('combobox',{name:'ComfyUI window',exact:true});
        await expect(windows.locator('option')).toContainText(['Test ComfyUI']);await windows.selectOption({label:'Test ComfyUI'});
        await sender.getByRole('combobox',{name:'Destination node',exact:true}).selectOption({label:'My prompt pair'});
        await sender.getByRole('button',{name:'Preview changes',exact:true}).click();
        await sender.getByRole('button',{name:'Apply to ComfyUI',exact:true}).click();
        await expect(sender).toContainText('Sent to ComfyUI.');
        const applied=await host.evaluate(()=>window.applied);
        await sender.getByRole('button',{name:'Close',exact:true}).click();await expect(sender).toHaveCount(0);
        return applied;
    } finally {await host.evaluate(()=>window.fixtureHost.dispose());await host.close();}
}

/** A tag chip: the collection it leaves (the count line), then the chip pressed again for the same result. */
async function tagOf(page) {
    const before=await page.locator('#result-count').textContent();
    await panelOf(page).locator('.detail-inner > .tags').last().getByRole('button',{name:'cinematic',exact:true}).click();
    await expect.poll(()=>page.locator('#result-count').textContent()).not.toBe(before);
    await settle(page);
    return {count:await page.locator('#result-count').textContent(),
        cards:await page.locator(`${CARDS} .image-open`).evaluateAll(nodes=>nodes.map(node=>node.getAttribute('aria-label')))};
}

const historyOf=async page=>{
    await panelOf(page).getByRole('button',{name:'Prompt history',exact:true}).click();
    await expect.poll(()=>page.evaluate(()=>location.hash)).not.toMatch(/images$/);
    return page.evaluate(()=>location.hash);
};

test.beforeEach(async({request},testInfo)=>{testInfo.manifest=await seedConcept(request);});

for(const [width,height] of SIZES)for(const theme of THEMES)test(`inspector panel geometry, tokens and controls match the concept ${width}x${height} ${theme}`,async({page,context,browserName},testInfo)=>{
    test.setTimeout(180_000);
    // The concept's copy and production's both use the async clipboard; Chromium asks for the permission.
    if(browserName==='chromium')await context.grantPermissions(['clipboard-read','clipboard-write']);
    await page.setViewportSize({width,height});
    const manifest=testInfo.manifest;
    const viewport={width,height};
    await openConceptPanel(page,theme);
    await captureCanonicalState(page,testInfo,`concept-inspector-${width}-${theme}`);
    const reference=await inventory(page,true);
    const concept={copy:await copyOf(page),send:await conceptSendOf(page),tag:await tagOf(page)};
    await openConceptPanel(page,theme);concept.history=await historyOf(page);

    await openProductionPanel(page,theme);
    await captureCanonicalState(page,testInfo,`production-inspector-${width}-${theme}`);
    const actual=await inventory(page);
    const production={copy:await copyOf(page),send:await productionSendOf(page,context)};
    const listing=page.waitForRequest(item=>item.url().includes('/images/output?')&&new URL(item.url()).searchParams.get('tags')==='cinematic');
    production.tag=await tagOf(page);await listing;
    // D44: the applied tag is one active refinement, so the toolbar's Filters button counts it.
    await page.getByRole('button',{name:'Filters · 1',exact:true}).click();
    production.tagFilter=await page.getByRole('dialog').getByRole('textbox',{name:'Tags',exact:true}).inputValue();
    await page.keyboard.press('Escape');
    await openProductionPanel(page,theme);production.history=await historyOf(page);

    const diff=compare(reference,actual,viewport);
    const path=testInfo.outputPath('inspector-geometry.json');
    await writeFile(path,JSON.stringify({reference,actual,diff,concept,production},null,2));
    await testInfo.attach('inspector-geometry',{path,contentType:'application/json'});

    // The concept itself is what this comparison assumes (spec §3.3, s523–615).
    const {panel}=reference,coarse=await page.evaluate(()=>matchMedia('(pointer: coarse)').matches);
    expect.soft(panel.width,'concept panel width').toBe(width>700?310:width-28);
    expect.soft(panel.position,'concept panel position').toBe(width>700?'sticky':'static');
    expect.soft(panel.cover.height,'concept cover').toBe(width>700?150:200);
    expect.soft(panel.inner.paddingTop,'concept inner padding').toBe('17px');
    expect.soft(panel.heading.h2.fontSize,'concept h2').toBe('14px');
    for(const section of panel.sections) {
        expect.soft(section.label,`concept ${section.className} label`).toMatchObject({fontSize:'10px',letterSpacing:'0.7px',textTransform:'uppercase',fontWeight:'650'});
        expect.soft(section.text,`concept ${section.className} text`).toMatchObject({fontSize:'12px',lineHeight:'21px'});
        for(const button of section.actions.buttons)expect.soft(button,`concept ${section.className} action`).toMatchObject(coarse?{minHeight:'36px',minWidth:'36px'}:{minHeight:'27px',minWidth:'28px'});
    }
    expect.soft(panel.sections.map(section=>section.label.color)[0],'concept role colors').not.toBe(panel.sections[1].label.color);
    expect.soft(panel.facts,'concept facts').toMatchObject({columns:2,columnGap:'12px',rowGap:'12px',fontSize:width>700?'11px':'12px'});
    expect.soft(panel.footer,'concept footer').toMatchObject({paddingTop:'13px',paddingRight:'17px',paddingBottom:'13px',paddingLeft:'17px'});
    expect.soft(panel.badges.chips.map(chip=>chip.text),'concept badges').toEqual(['Execution recorded','Landscapes']);
    expect.soft(reference.viewerDetails.structure.inner.slice(-2),'concept full details').toEqual(['details','details']);

    expect.soft(diff,'differences after the recorded deviations').toEqual([]);
    // Copy: the concept's toast "Prompt copied." (or its dialog), in both documents.
    expect.soft(production.copy,'copy').toEqual(concept.copy);
    if(browserName==='chromium')expect.soft(production.copy,'copy toast').toEqual({toast:'Prompt copied.',dialog:false});
    // Send: the concept previews both roles; production applies exactly those texts and ComfyUI acknowledges them.
    const [positive,negative]=concept.send;
    expect.soft(concept.send.map(([heading])=>heading),'concept send').toEqual(['+ Positive · replace','− Negative · replace']);
    expect.soft(production.send,'acknowledged send').toEqual([{positive:positive[1],negative:negative[1]}]);
    expect.soft(negative[1],'fixture negative').toBe('blurry, low quality, low resolution');
    // Tag chip: D21, the Images Tags filter (the concept searches the tag's text instead), leaving the same images.
    expect.soft(production.tagFilter,'tag chip filter').toBe('cinematic');
    // The concept's search also matches the forest prompt's text ("cinematic still"), which is not tagged cinematic
    // (app.js 73, 115, 130): production leaves the tagged prompts' images, in the concept's order.
    const tagged=concept.tag.cards.filter(card=>!card.startsWith('Open forest_'));
    expect.soft(concept.tag.count,'concept tag search').toBe('12 of 12 images · 3 positive prompts');
    expect.soft(production.tag,'tag chip collection').toEqual({count:countCopy(`${tagged.length} of ${tagged.length} images · 2 positive prompts`),cards:tagged});
    // Prompt history: the concept's `#prompt/<id>` is production's Library history of the linked positive.
    expect.soft(concept.history,'concept history').toBe('#prompt/alpine');
    expect.soft(production.history,'history').toBe(`#/library/${manifest.prompts[0].positive_id}`);
});

// D39 (ruled 2026-10-21): a recipe-linked image shows its recipe's own category and tags when set, otherwise its
// positive's, field by field. The concept fixture's recipes have neither, so this recipe-linked image shows its
// positive's, as the concept does, and its tag chips are Tags filter buttons again (D21: the positive's own tags).
test('a recipe-linked image without recipe metadata shows its positive prompt metadata (D39)',async({page})=>{
    await page.setViewportSize({width:1600,height:1100});
    await openProductionPanel(page,'dark');
    await page.locator(CARDS).nth(1).locator('[aria-label^="Inspect"]').click();
    await expect(panelOf(page).locator('.panel-heading h2')).toHaveText('Quiet geometry');
    const rows=panelOf(page).locator('.detail-inner > .tags');
    await expect(rows).toHaveCount(2);
    await expect(rows.first().locator('.chip')).toHaveText(['Execution recorded','Positive prompt category: Architecture']);
    await expect(rows.first().locator('.chip .ca-visually-hidden')).toHaveText('Positive prompt category: ');
    await expect(rows.nth(1).locator('button.chip')).toHaveText(['minimal','warm_light','editorial']);
    await expect(rows.nth(1).locator('span.chip')).toHaveCount(0);
    await expect(panelOf(page).locator('.detail-footer button')).toHaveText(['Send to ComfyUI','Prompt history']);
});

// Task C3 (D88, #249 PR C): the concept's chip row is the image's own tags (`i.tags`, app.js a402). With those names
// saved as the file's current image tags, production draws the row from them (Tags filter buttons in the panel, plain
// in the viewer, the linked positive's equal tags not repeated) and still equals the concept, in both sizes.
for(const [width,height] of SIZES)test(`image tags saved on the concept image keep the inspector equal to the concept ${width}x${height} (D88)`,async({page,request},testInfo)=>{
    test.setTimeout(120_000);
    const viewport={width,height};await page.setViewportSize(viewport);
    await openConceptPanel(page,'dark');
    const names=await panelOf(page).locator('.detail-inner > .tags').last().locator('button.chip').allTextContents();
    expect(names).toEqual(['cinematic','blue_hour','architecture']);
    await captureCanonicalState(page,testInfo,`concept-image-tags-${width}`,'#image-inspector');
    const reference=await inventory(page,true);
    const {path}=testInfo.manifest.images.find(item=>item.filename==='alpine_00001.png');
    expect(await saveImageTags(request,path,names)).toEqual(names);
    const read=page.waitForResponse(item=>new URL(item.url()).pathname===`/prompt_manager/images/inspect/${path}`);
    await openProductionPanel(page,'dark');
    expect((await (await read).json()).image_tags).toEqual({state:'current',tags:names});
    await expect(panelOf(page).locator('.detail-inner > .tags').last().locator('.chip')).toHaveText(names);
    await captureCanonicalState(page,testInfo,`production-image-tags-${width}`,'#image-inspector');
    assertInspectorMatch(reference,await inventory(page),viewport);
});

// The comparison is only worth something if it notices a change (spec §7 mutation check): on the panel one CSS
// mutation (the role label's letter spacing) and one DOM mutation (the footer removed); in the viewer (final review m5)
// its pair actions removed and D38's Show in folder removed; each applied after an exact match.
test('inspector comparison detects mutations',async({page})=>{
    test.setTimeout(120_000);
    const viewport={width:1600,height:1100};
    await page.setViewportSize(viewport);
    await openConceptPanel(page,'dark');
    const reference=await inventory(page,true);
    await openProductionPanel(page,'dark');
    const {viewerDetails,fileControls}=await inventory(page);
    const measure=async()=>({panel:(await inspectorOf(page)).panel,viewerDetails,fileControls});
    assertInspectorMatch(reference,await measure(),viewport);

    const restoreCss=await mutate(page,{css:'.prompt-heading .role-label { letter-spacing: 0 }'});
    const cssMutated=await measure();
    expect(()=>assertInspectorMatch(reference,cssMutated,viewport)).toThrow(/panel\.sections\.0\.label\.letterSpacing/);
    await restoreCss();await settle(page);
    assertInspectorMatch(reference,await measure(),viewport);

    const panel=(await inspectorOf(page)).panel;
    const viewerMutated=async mutation=>{const {viewerDetails,fileControls}=await viewerOf(page,false,mutation);return {panel,viewerDetails,fileControls};};
    const withoutActions=await viewerMutated({remove:['#viewer-details > button']});
    expect(()=>assertInspectorMatch(reference,withoutActions,viewport)).toThrow(/viewerDetails\.actions/);
    const withoutReveal=await viewerMutated({remove:['#viewer-details button[aria-label$=" in folder"]']});
    expect(()=>assertInspectorMatch(reference,withoutReveal,viewport)).toThrow(/fileControls/);
    assertInspectorMatch(reference,await viewerMutated(),viewport);

    await mutate(page,{remove:['#image-inspector > .detail-footer']});
    const domMutated=await measure();
    expect(()=>assertInspectorMatch(reference,domMutated,viewport)).toThrow(/panel\.footer/);
});
