import {writeFile} from 'node:fs/promises';
import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import {captureCanonicalState} from './history-canonical-comparison.mjs';
import {SIZES,THEMES,MOTIONS,seedConcept,openConcept,openProduction,inventoryOf,diffInventory,mutate,stripExact} from './concept-inventory.mjs';

// Task F5 of #249: the production Prompt history (#/library/<id>) against the immutable concept's (#prompt/alpine,
// app.js renderHistory a587–622, the dialogs a1282–1304, style.css 1059–1179 and 1546–1567), on the concept-shaped real
// fixture: Alpine observatory with its three saved negatives, four images linked to their pairs, four recorded runs and
// real pair run counts (2, 1, 1). Heading, summary, the negative variants panel, the comparison and the shared gallery's
// heading are compared at 1600x1100 and 390x844 in both themes and motion settings, with the page's behaviors in both
// documents. Both documents are measured with "Clean architecture" selected: it is the middle lane in either order
// (D46). The topbar and sidebar are the shell spec's, the gallery's cards the Images spec's (D47). Paired captures are
// artifacts only; there are no PNG baselines.
test.use({locale:'en-US',timezoneId:'UTC'});

const TITLE='Alpine observatory';
const SELECTED='Clean architecture';
const LANES=3;
const SHOW_ARCHIVED='Show archived (0)';
const SECTIONS=['heading','history','noPageScroll'];

/**
 * Recorded history deviations only (spec §8, TESTING.md D24–D26, D35, D45–D47); everything else must equal production.
 * `reference.oracle` is the concept page itself, measured once its lanes list newest first with the singular for one
 * (D46, D35) and once the approved Show archived button (#235) follows + Add negative: the browser lays out what moves.
 */
export function mapHistoryConcept(reference) {
    const {history}=reference.oracle;
    return {...reference,history:{...history,
        // D24: "N tracked runs" is the recorded window's `recorded_run_count`; the fixture records one run per image, so
        // the concept's "4 tracked runs" holds by construction (asserted on both documents below).
        summary:history.summary,
        variants:{...history.variants,
            // D45: the bounded "N of M saved negative variants" count stays a visually hidden status.
            structure:{...history.variants.structure,spoken:[{role:'status',text:`${LANES} of ${LANES} saved negative variants`}]}},
        compare:history.compare&&{...history.compare,grid:{...history.compare.grid,items:history.compare.grid.items.map((item,index)=>({...item,
            // D46: the two compared blocks are named Baseline and Selected, so no action name repeats on the page.
            sections:item.sections.map(section=>({...section,actions:{...section.actions,buttons:section.actions.buttons.map(button=>({...button,
                label:button.label.replace(/^(Copy|Send) negative/,`$1 ${COMPARED[index]} negative`)}))}}))}))}},
        // D47: the gallery's body is the Images collection (layout row, cards, side panel), so only its place, frame and
        // heading are compared here; the cards are the Images comparison's.
        gallery:history.gallery&&withoutHeight(history.gallery),
    }};
}
const COMPARED=['baseline','selected'];
const withoutHeight=({height,...gallery})=>gallery;

function compare(reference,actual) {
    const only=inventory=>Object.fromEntries(SECTIONS.map(key=>[key,key==='history'&&inventory.history?.gallery
        ?{...inventory.history,gallery:withoutHeight(inventory.history.gallery)}:inventory[key]]));
    return diffInventory(stripExact(only(mapHistoryConcept(reference))),stripExact(only(actual)));
}

/** The comparison as a gate: throws, naming every differing path, unless production equals the mapped concept. */
function assertHistoryMatch(reference,actual) {
    const diff=compare(reference,actual);
    if(diff.length)throw new Error(`the Prompt history differs from the concept at ${diff.map(item=>item.path).join(', ')}`);
}

// Two frames, then every running transition finished.
const settle=page=>page.evaluate(async()=>{
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    await Promise.race([Promise.all(document.getAnimations().map(animation=>animation.finished.catch(()=>{}))),
        new Promise(resolve=>setTimeout(resolve,2000))]);
});
const decoded=page=>page.evaluate(async()=>{
    await document.fonts.ready;
    await Promise.all([...document.querySelectorAll('.history-gallery img')].map(img=>{img.loading='eager';return img.decode().catch(()=>{});}));
});

const dot=(page,name)=>page.getByRole('button',{name:`Select negative ${name}`,exact:true});
const compareButton=page=>page.getByRole('button',{name:'Compare negatives',exact:true});
const galleryCards=page=>page.locator('.history-gallery .media-card');

/** Press a variant dot and wait until the summary and the gallery follow it. */
async function selectVariant(page,name) {
    await dot(page,name).click();
    await expect(dot(page,name)).toHaveAttribute('aria-pressed','true');
    await expect(page.locator('.history-gallery .panel-heading .small')).toHaveText(`${name} · same positive prompt`);
    await expect(galleryCards(page).first()).toBeVisible();
    await page.mouse.move(0,0);await decoded(page);await settle(page);
}
async function setCompare(page,pressed) {
    const button=compareButton(page);
    if(await button.getAttribute('aria-pressed')!==String(pressed))await button.click();
    await expect(compareButton(page)).toHaveAttribute('aria-pressed',String(pressed));
    await page.mouse.move(0,0);await settle(page);
}

async function openConceptHistory(page,theme,motion) {
    await openConcept(page,'prompt/alpine',theme,motion);
    await expect(page.locator('.timeline .variant-lane')).toHaveCount(LANES);
    await selectVariant(page,SELECTED);
}
async function openProductionHistory(page,theme,motion,positiveId) {
    await openProduction(page,`library/${positiveId}`,theme,motion);
    await expect(page.locator('.timeline .variant-lane')).toHaveCount(LANES);
    await expect(page.locator('article.surface .card-meta > span').first()).toBeVisible();
    await selectVariant(page,SELECTED);
}

// D45: the recorded production rules that lay the panel out (library.css): the panel heading and the legend row wrap,
// All images wraps its label, and a lane's text wraps before a shown repeat-run dot. Applied to the concept so the browser
// lays out what follows; at 1600px nothing wraps.
const D45_LAYOUT=`.timeline-panel > .panel-heading, .timeline-lower { flex-wrap: wrap; }
.variant-lane:has(> .run-dot:not([hidden])) > span { max-inline-size: 139px; overflow-wrap: anywhere; }
.ca-tools .timeline-lower > button { max-inline-size: 100%; white-space: normal; }`;

/**
 * The concept laid out as production lists it: lanes newest first with their offsets (D46), "1 run" in the singular
 * (D35), the approved Show archived button after + Add negative (#235) and D45's layout rules. Returns the undo.
 */
async function conceptAsRecorded(page) {
    const restoreLayout=await mutate(page,{css:D45_LAYOUT});
    await page.evaluate(label=>{
        const timeline=document.querySelector('.timeline'),lanes=[...timeline.querySelectorAll(':scope > .variant-lane')];
        const texts=lanes.map(lane=>[lane.querySelector(':scope > span'),lane.querySelector(':scope > span').textContent]);
        lanes.reverse().forEach((lane,index)=>{lane.style.setProperty('--lane-offset',`${38+index*47}px`);timeline.append(lane);});
        for(const [span,text] of texts)span.textContent=text.replace(/(^|\D)1 runs$/,'$11 run');
        const add=[...document.querySelectorAll('.timeline-panel .row-actions > button')].find(node=>node.textContent==='+ Add negative');
        const archived=add.cloneNode(false);archived.removeAttribute('data-action');archived.removeAttribute('data-prompt');
        archived.setAttribute('aria-pressed','false');archived.textContent=label;add.after(archived);
        window.__historyOracle={timeline,order:[...lanes].reverse(),texts,archived};
    },SHOW_ARCHIVED);
    await settle(page);
    return ()=>page.evaluate(()=>{
        const {timeline,order,texts,archived}=window.__historyOracle;
        order.forEach((lane,index)=>{lane.style.setProperty('--lane-offset',`${38+index*47}px`);timeline.append(lane);});
        for(const [span,text] of texts)span.textContent=text;
        archived.remove();delete window.__historyOracle;
    }).then(restoreLayout);
}

/** The concept inventory, with `oracle`: the same page laid out as recorded (`conceptAsRecorded`). */
async function conceptInventory(page) {
    const reference=await inventoryOf(page,'#main');
    const restore=await conceptAsRecorded(page);
    const oracle=await inventoryOf(page,'#main');
    await restore();await settle(page);
    return {...reference,oracle};
}

const negativeText=page=>page.locator('article.surface .prompt-section.negative .prompt-text').textContent();
const galleryOf=async page=>({context:await page.locator('.history-gallery .panel-heading .small').textContent(),
    cards:await galleryCards(page).locator('.image-open').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('aria-label')))});

/** A dialog the page opens: its accessible name and how many media cards it shows once loaded; then closed. */
async function dialogOf(page,trigger,{name,cards}) {
    await trigger.click();
    const dialog=page.getByRole('dialog',{name,exact:true});
    await expect(dialog).toBeVisible();
    if(cards)await expect(dialog.locator('.media-card')).toHaveCount(cards);
    const result=cards?{name,cards:await dialog.locator('.media-card').count()}:{name};
    await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
    return result;
}

/** The behaviors Task F5 lists, with identical selectors in both documents. */
async function behaviorsOf(page,{sent,runDialog}) {
    await selectVariant(page,'Quality baseline');
    const before={negative:await negativeText(page),gallery:await galleryOf(page)};
    await selectVariant(page,SELECTED);
    const after={negative:await negativeText(page),gallery:await galleryOf(page)};
    const pressed=[await compareButton(page).getAttribute('aria-pressed')];
    await setCompare(page,true);pressed.push(await compareButton(page).getAttribute('aria-pressed'));
    const compared=await page.locator('section.surface:not(.timeline-panel) > h2').textContent();
    await setCompare(page,false);pressed.push(await compareButton(page).getAttribute('aria-pressed'));
    const all=await dialogOf(page,page.getByRole('button',{name:'All images for this positive',exact:true}),{name:'All images for this positive',cards:4});
    const run=await dialogOf(page,page.getByRole('button',{name:'Show repeat run for Quality baseline',exact:true}),{name:runDialog});
    await page.locator('.heading-actions').getByRole('button',{name:`Send prompt pair to ComfyUI: ${TITLE}`,exact:true}).click();
    const send=await sent(page);
    await page.getByRole('button',{name:'← Prompt library',exact:true}).click();
    await expect(page.locator('h1#page-title')).toHaveText('Prompt library');
    return {variant:{before,after},pressed,compared,all,run,send,back:await page.locator('h1#page-title').textContent()};
}

/** The concept's demo sender: the roles its preview lists, with their text. */
async function conceptSent(page) {
    const modal=page.locator('dialog#modal[open]');
    await expect(modal.locator('#modal-title')).toHaveText('Send to ComfyUI');
    const roles=await modal.locator('.send-preview').evaluateAll(nodes=>Object.fromEntries(nodes.map(node=>
        [node.querySelector('h3').textContent.startsWith('+')?'positive':'negative',node.querySelector('p').textContent])));
    await page.keyboard.press('Escape');await expect(modal).toHaveCount(0);
    return roles;
}
/** Production's acknowledged sender against a bridge host page: the roles its preview asked the host to change. */
function productionSent(host) {
    return async page=>{
        const sender=page.getByRole('dialog',{name:'Send to ComfyUI',exact:true});
        await sender.getByRole('combobox',{name:'ComfyUI window',exact:true}).selectOption({label:'Concept ComfyUI'});
        await sender.getByRole('combobox',{name:'Destination node',exact:true}).selectOption({label:'Concept destination'});
        await sender.getByRole('button',{name:'Preview changes',exact:true}).click();
        await expect.poll(()=>host.evaluate(()=>window.previewed.length)).toBe(1);
        const roles=await host.evaluate(()=>window.previewed.pop());
        await page.keyboard.press('Escape');await expect(sender).toHaveCount(0);
        return roles;
    };
}
async function openHost(context) {
    const host=await context.newPage();await host.goto('/prompt_manager/workspace#/overview');
    await host.evaluate(async()=>{
        const {createBridgeHost}=await import('/prompt_manager/js/shared/toolkit/prompt-bridge.js');
        const target={id:'pair-1',label:'Concept destination',type:'PromptManagerPair',roles:['positive','negative']};
        window.previewed=[];
        window.conceptHost=createBridgeHost({origin:location.origin,namespace:'/',label:'Concept ComfyUI',adapter:{
            listTargets:()=>[target],
            preview:(_id,roles)=>{window.previewed.push(roles);return {preview:{target,fields:Object.entries(roles).map(([role,after])=>({role,before:`Previous ${role}`,after})),
                composition:{prepend:'',append:'',prependConnected:false,appendConnected:false,before:'Previous positive',after:roles.positive??'Previous positive'}},snapshot:roles};},
            apply:roles=>({targetId:target.id,values:roles}),
        }});
    });
    return host;
}

test.beforeEach(async({page,request},testInfo)=>{
    testInfo.manifest=await seedConcept(request);
    await page.addInitScript(()=>{window.copied=[];Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copied.push(text);}}});});
});

for(const [width,height] of SIZES)for(const theme of THEMES)test(`history geometry, tokens, controls and behaviors match the concept ${width}x${height} ${theme}`,async({page,context},testInfo)=>{
    test.setTimeout(240_000);
    await page.setViewportSize({width,height});
    const positiveId=testInfo.manifest.prompts[0].positive_id;
    const host=await openHost(context);
    try {
        const result={};
        for(const motion of MOTIONS) {
            const capture=motion==='no-preference';
            await openConceptHistory(page,theme,motion);
            const reference=await conceptInventory(page);
            await setCompare(page,true);
            const referenceCompared=await conceptInventory(page);
            if(capture)await captureCanonicalState(page,testInfo,`concept-history-${width}-${theme}`);
            const referenceBehaviors=capture?await behaviorsOf(page,{sent:conceptSent,runDialog:'Repeat run'}):null;

            await openProductionHistory(page,theme,motion,positiveId);
            const actual=await inventoryOf(page,'#workspace-main');
            await setCompare(page,true);
            const actualCompared=await inventoryOf(page,'#workspace-main');
            if(capture)await captureCanonicalState(page,testInfo,`production-history-${width}-${theme}`);
            const actualBehaviors=capture?await behaviorsOf(page,{sent:productionSent(host),runDialog:'Recorded run'}):null;
            result[motion]={reference,actual,diff:compare(reference,actual),referenceCompared,actualCompared,
                compareDiff:compare(referenceCompared,actualCompared),referenceBehaviors,actualBehaviors};
        }
        const path=testInfo.outputPath('history-geometry.json');await writeFile(path,JSON.stringify(result,null,2));
        await testInfo.attach('history-geometry',{path,contentType:'application/json'});
        for(const [motion,{diff,compareDiff,actual,referenceCompared}] of Object.entries(result)) {
            // The concept itself is what this comparison assumes (style.css 709–722, 1059–1179, 1426, 1546).
            const {summary,variants,compare:compared,gallery}=referenceCompared.history;
            expect.soft(summary,`${motion} concept surface`).toMatchObject({paddingTop:'22px',paddingLeft:'22px',borderTopLeftRadius:'7px',borderTopWidth:'1px'});
            // D24: the fixture's four recorded runs give the concept's own "4 tracked runs"; production reads them.
            expect.soft(summary.meta.items.map(item=>item.text),`${motion} concept counts`).toEqual(['4 tracked runs','4 images','3 negative variants']);
            expect.soft(actual.history.summary.meta.items.map(item=>item.text),`${motion} tracked runs`).toEqual(['4 tracked runs','4 images','3 negative variants']);
            expect.soft(variants.timeline,`${motion} concept timeline`).toMatchObject({paddingTop:'18px',paddingLeft:'20px',borderTopLeftRadius:'6px'});
            variants.timeline.lanes.forEach((lane,index)=>{
                expect.soft(lane,`${motion} concept lane ${index}`).toMatchObject({offset:`${38+index*47}px`,marginLeft:`${38+index*47}px`,height:57});
                expect.soft(lane.dot,`${motion} concept dot ${index}`).toMatchObject({width:'29px',height:'29px',text:'−'});
            });
            expect.soft(variants.timeline.lanes.map(lane=>lane.run&&{width:lane.run.width,left:lane.run.left}),`${motion} concept run dots`).toEqual([{width:'12px',left:'190px'},null,null]);
            expect.soft(compared.grid,`${motion} concept compare grid`).toMatchObject({columns:width>700?2:1,columnGap:'15px'});
            expect.soft(compared.added.map(chip=>chip.text),`${motion} concept added terms`).toEqual(['+ distorted architecture','+ watermark','+ text']);
            expect.soft(compared.added[0],`${motion} concept diff chip`).toMatchObject({fontSize:'11px',paddingTop:'3px',paddingLeft:'7px',borderTopLeftRadius:'4px',display:'inline-block'});
            expect.soft(gallery,`${motion} concept gallery`).toMatchObject({marginTop:'24px',heading:{h2:{text:'Shared gallery',fontSize:'14px'},note:{text:`${SELECTED} · same positive prompt`}}});
            expect.soft(diff,`${motion} differences after the recorded deviations`).toEqual([]);
            expect.soft(compareDiff,`${motion} differences with Compare negatives pressed`).toEqual([]);
            expect.soft(actual.noPageScroll,`${motion} no page-level horizontal scroll`).toBe(true);
        }
        const {referenceBehaviors:concept,actualBehaviors:production}=result['no-preference'];
        expect.soft(production,'behaviors').toEqual({...concept,run:{...concept.run,name:'Recorded run'}});
    } finally {await host.evaluate(()=>window.conceptHost.dispose());await host.close();}
});

// The comparison is only worth something if it notices a change (spec §7 mutation check): one CSS mutation (the variant
// dot's width) and one DOM mutation (Compare negatives removed), each applied after an exact match.
test('history comparison detects mutations',async({page},testInfo)=>{
    await page.setViewportSize({width:1600,height:1100});
    await openConceptHistory(page,'dark','no-preference');
    const reference=await conceptInventory(page);
    await openProductionHistory(page,'dark','no-preference',testInfo.manifest.prompts[0].positive_id);
    const measure=()=>inventoryOf(page,'#workspace-main');
    assertHistoryMatch(reference,await measure());

    const restoreCss=await mutate(page,{css:'.ca-tools .variant-dot { width: 20px }'});
    const cssMutated=await measure();
    expect(()=>assertHistoryMatch(reference,cssMutated)).toThrow(/history\.variants\.timeline\.lanes\.0\.dot\.width/);
    await restoreCss();await settle(page);
    assertHistoryMatch(reference,await measure());

    await mutate(page,{remove:['.timeline-panel .row-actions > button[aria-pressed]:first-child']});
    const domMutated=await measure();
    expect(()=>assertHistoryMatch(reference,domMutated)).toThrow(/history\.variants\.heading\.actions\.buttons\.0\.text/);
});
