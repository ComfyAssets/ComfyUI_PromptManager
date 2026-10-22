import {writeFile} from 'node:fs/promises';
import {test,expect} from './fixtures.mjs';
import {captureCanonicalState} from './history-canonical-comparison.mjs';
import {SIZES,THEMES,MOTIONS,seedConcept,openConcept as openConceptPage,openProduction,inventoryOf,diffInventory,focusRingOf,hoverStateOf,mutate} from './concept-inventory.mjs';

// Task A6 of #249: the production shell (sidebar, topbar, heading, footer) against the immutable concept, with the
// concept-shaped real fixture so the sidebar counts are the concept's own (24 images, 6 prompts, 15 tags). Both themes,
// 1600x1100 and 390x844, normal and reduced motion. Paired captures are artifacts only; there are no PNG baselines.
test.use({locale:'en-US',timezoneId:'UTC'});

// Routes whose shell is compared. The concept and production share route names for these three pages.
const ROUTES=Object.freeze([['images','Images'],['library','Prompt library'],['tags','Tags']]);
// The heading's content (description copy, actions) belongs to each page's own PR (C1, E1, F1) and is compared there;
// Tags & categories is already rebuilt (#245), so its heading is compared in full.
const FULL_HEADING=new Set(['tags']);

const rowNamed=(nav,text)=>nav.rows.find(row=>row.text===text);

const removedItem=item=>String(item.className).includes('concept-badge');

/**
 * The concept with its Activity count hidden (D52): production counts running jobs only and hides the count at 0, as
 * the comparison fixture has none. The concept's own CSS then lays out its button without the count.
 */
async function openConcept(page,route,theme,motion) {
    await openConceptPage(page,route,theme,motion);
    await mutate(page,{css:'#activity-count { display: none }'});
}

/** Left offsets of flex items packed along a row of `width` by its `justify-content` and `gap`. */
function packedLefts(items,width,justify,gap) {
    const total=items.reduce((sum,item)=>sum+item.width,0)+gap*(items.length-1);
    let start=0,spacing=gap;
    if(justify==='flex-end'||justify==='end')start=width-total;
    else if(justify==='center')start=(width-total)/2;
    else if(justify==='space-between'&&items.length>1)spacing=gap+(width-total)/(items.length-1);
    let x=start;
    return items.map(item=>{const left=Math.round(x);x+=item.width+spacing;return left;});
}

/** The top-actions row once removed items are gone: a centered flex row, so its size and item offsets follow. */
function remainingTopActions(actions) {
    const kept=actions.items.filter(item=>!removedItem(item));
    const shown=kept.filter(item=>item.shown);
    const height=Math.max(...shown.map(item=>item.height));
    const gap=parseFloat(actions.columnGap);
    // A full-width row (<=700px) keeps its width; otherwise it shrinks to its remaining items.
    const width=actions.fullWidth?actions.width:shown.reduce((sum,item)=>sum+item.width,0)+gap*(shown.length-1);
    // At <=700px the concept spreads its shown items with `space-between`; the hidden pill (D53) takes no place.
    // Offsets come from the concept's own sizes.
    const lefts=new Map(shown.map((item,index)=>[item,packedLefts(shown,width,actions.justifyContent,gap)[index]]));
    return {...actions,height,width,
        // D52: the full-width row (<=700px) wraps its items when a zoomed phone is narrower than they are.
        flexWrap:actions.fullWidth?'wrap':actions.flexWrap,
        items:kept.map(item=>item.shown?{...item,top:Math.round((height-item.height)/2),left:lefts.get(item)}:item)};
}

/** The footer once its button is gone: paddings plus the remaining spans along the footer's flex direction. */
function remainingFooter(footer,spans) {
    const content=footer.flexDirection==='column'
        ?spans.reduce((sum,span)=>sum+span.height,0)+parseFloat(footer.rowGap)*(spans.length-1)
        :Math.max(...spans.map(span=>span.height));
    return `${parseFloat(footer.paddingTop)+parseFloat(footer.paddingBottom)+content}px`;
}

// D28 ruling (final review A, Minor 1): Overview gets a distinct outline home glyph. The concept has no home icon, and
// its `activity` pulse belongs to the Activity row, so the two entries never share one glyph.
const OVERVIEW_GLYPH='M3 11 12 4l9 7M5 10v10h14V10M10 20v-6h4v6';

/**
 * The concept's SAVED VIEWS rows (app.js a331–335) as production lists them: Favorites with its count (D51), then the
 * saved views. D50: no "Needs review" row until the tag-review slice (maintainer ruling 2026-10-21).
 */
function savedNav(nav) {
    const rows=nav.rows.filter(row=>row.text!=='Needs review');
    return {...nav,rows:rows.map((row,index)=>index===rows.length-1?{...row,gapAfter:null}:row)};
}

/** Recorded shell deviations only (spec §8). Everything else in the concept inventory must equal production. */
function mapShellConcept(reference) {
    const {chrome}=reference;
    const navs=chrome.navs
        // D28: COLLECTIONS is a proposal with no persisted store, so its nav is not rendered.
        .filter(nav=>nav.id!=='collections-nav')
        .map(nav=>{
            if(nav.id==='saved-nav')return savedNav(nav);
            if(nav.id!=='system-nav')return nav;
            const system=rowNamed(nav,'System');
            return {...nav,rows:[
                // D52: the Activity row counts running jobs only, so with none it has no count.
                {...rowNamed(nav,'Activity'),count:null},
                // D28: no Design review (prototype page).
                system,
                // D28: the production Overview hub follows System, as a concept nav row without a count. Its icon is
                // the ruling's own outline home glyph (OVERVIEW_GLYPH), never the concept's `activity` pulse.
                {...system,text:'Overview',current:null,gapAfter:null,icon:{...system.icon,d:OVERVIEW_GLYPH}},
            ]};
        });
    return {...reference,chrome:{...chrome,
        // D29: no prototype suffix in the document title.
        title:chrome.title.replace(/ · Prompt Manager concept$/,' · Prompt Manager'),
        // D28: no COLLECTIONS label. D49: SAVED VIEWS without its "PROPOSED" badge.
        labels:chrome.labels.filter(label=>label.text!=='COLLECTIONS PROPOSED')
            .map(label=>label.text==='SAVED VIEWS PROPOSED'?{...label,text:'SAVED VIEWS'}:label),
        navs,
        connection:{...chrome.connection,
            // D28: truthful copy instead of "Demo connection · Classic / Nodes 2.0".
            small:'Your library, on your server',
            // D28: no fake connection state, so the dot is the neutral muted token (the concept's label color).
            dot:{...chrome.connection.dot,backgroundColor:chrome.labels[0].color}},
        // D29: no "Interactive concept" badge. D53: the progress pill before the Activity button, hidden with no running
        // job. D52: the button's count is hidden at 0. The row's size and the remaining items' centered offsets follow
        // from the concept's own item sizes.
        topActions:remainingTopActions({...chrome.topActions,items:chrome.topActions.items.flatMap(item=>
            item.tag==='button'&&item.text==='Activity'?[{tag:'a',className:'activity-pill',shown:false},{...item,count:'0'}]:[item])}),
        footer:(()=>{
            // D29: production footer copy (one line, as the concept's own copy is), and no "What changes & why"
            // (a prototype review page); the footer then holds only its span.
            const spans=chrome.footer.spans.map(span=>({...span,text:'ComfyAssets · Local tools. Your library.'}));
            return {...chrome.footer,spans,buttons:[],height:remainingFooter(chrome.footer,spans)};
        })(),
    }};
}

/** The heading without its page-owned description copy and actions; the h1 text is the shell's and stays compared. */
function headingFrame(heading) {
    if(!heading)return heading;
    const {height,description,actions,...frame}=heading;
    const {text:copy,...descriptionTokens}=description??{};
    const {buttons,stacked,...actionTokens}=actions??{};
    return {...frame,description:description?descriptionTokens:null,actions:actions?actionTokens:null};
}

function compare(route,reference,actual) {
    const expected=mapShellConcept(reference);
    const heading=FULL_HEADING.has(route);
    return [
        ...diffInventory({chrome:expected.chrome,noPageScroll:expected.noPageScroll},{chrome:actual.chrome,noPageScroll:actual.noPageScroll}),
        ...diffInventory(heading?expected.heading:headingFrame(expected.heading),heading?actual.heading:headingFrame(actual.heading))
            .map(item=>({...item,path:`heading.${item.path}`})),
    ];
}

/** The comparison as a gate: throws, naming every differing path, unless production equals the mapped concept. */
function assertShellMatches(route,reference,actual) {
    const diff=compare(route,reference,actual);
    if(diff.length)throw new Error(`the shell differs from the concept at ${diff.map(item=>item.path).join(', ')}`);
}

/** Keyboard focus rings on the first WORKSPACE nav link and the first heading button. */
async function ringsOf(page,mainSelector) {
    // Any key press makes the next programmatic focus keyboard-modal (`:focus-visible`); WebKit does not Tab to links.
    await page.locator('#page-title').focus();await page.keyboard.press('Tab');
    const button=page.locator(`${mainSelector} .heading-actions button`).first();
    await expect(button).toBeFocused();
    const headingButton=await focusRingOf(button);
    const link=page.locator('#primary-nav a').first();await link.focus();
    const navLink=await focusRingOf(link);
    await link.blur();
    return {navLink,headingButton};
}

/** Hover colors of the first non-current WORKSPACE row, the Theme select and the ComfyUI link. */
async function hoversOf(page) {
    return {
        navRow:await hoverStateOf(page,page.locator('#primary-nav a:not([aria-current])').first()),
        theme:await hoverStateOf(page,page.getByRole('combobox',{name:'Theme',exact:true})),
        canvasLink:await hoverStateOf(page,page.locator('.topbar .canvas-link')),
    };
}

/** A viewport capture with the footer (and, at 1600, the sidebar's bottom group) in view. */
async function captureFooter(page,testInfo,name) {
    await page.locator('.app-footer').scrollIntoViewIfNeeded();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await captureCanonicalState(page,testInfo,name);
}

test.beforeEach(async({request})=>{await seedConcept(request);});

for(const [width,height] of SIZES)for(const theme of THEMES)test(`shell geometry, tokens and controls match the concept ${width}x${height} ${theme}`,async({page},testInfo)=>{
    await page.setViewportSize({width,height});const result={};
    for(const motion of MOTIONS)for(const [route,breadcrumb] of ROUTES) {
        await openConcept(page,route,theme,motion);await page.mouse.move(0,0);
        const reference=await inventoryOf(page,'#main');
        if(motion==='no-preference')await captureCanonicalState(page,testInfo,`concept-shell-${route}-${width}-${theme}`);
        const referenceRings=FULL_HEADING.has(route)?await ringsOf(page,'#main'):null;
        const referenceHovers=await hoversOf(page);
        if(motion==='no-preference')await captureFooter(page,testInfo,`concept-footer-${route}-${width}-${theme}`);
        await openProduction(page,route,theme,motion);await page.mouse.move(0,0);
        const actual=await inventoryOf(page,'#workspace-main');
        if(motion==='no-preference')await captureCanonicalState(page,testInfo,`production-shell-${route}-${width}-${theme}`);
        const actualRings=FULL_HEADING.has(route)?await ringsOf(page,'#workspace-main'):null;
        const actualHovers=await hoversOf(page);
        if(motion==='no-preference')await captureFooter(page,testInfo,`production-footer-${route}-${width}-${theme}`);
        result[`${motion} ${route}`]={route,breadcrumb,reference,actual,diff:compare(route,reference,actual),referenceRings,actualRings,
            referenceHovers,actualHovers};
    }
    const path=testInfo.outputPath('shell-geometry.json');await writeFile(path,JSON.stringify(result,null,2));
    await testInfo.attach('shell-geometry',{path,contentType:'application/json'});
    for(const [key,{route,breadcrumb,reference,actual,diff,referenceRings,actualRings,referenceHovers,actualHovers}] of Object.entries(result)) {
        // The concept itself is what this comparison assumes: its sample counts and its shell controls.
        expect.soft(reference.chrome.navs[0].rows.map(row=>[row.text,row.count?.text??null]),`${key} concept WORKSPACE rows`)
            .toEqual([['Images','24'],['Prompt library','6'],['Tags & categories','15'],['Inspect a file',null]]);
        expect.soft(reference.chrome.topActions.items.map(item=>item.className||item.tag),`${key} concept top actions`)
            .toEqual(['concept-badge','button','theme-label','canvas-link']);
        expect.soft(reference.chrome.breadcrumbs.parts.at(-1).text,`${key} concept breadcrumb`).toBe(breadcrumb);
        expect.soft(reference.chrome.title,`${key} concept title`).toBe(`${breadcrumb} · Prompt Manager concept`);
        expect.soft(actual.chrome.title,`${key} production title`).toBe(`${breadcrumb} · Prompt Manager`);
        expect.soft(reference.heading.h1.text,`${key} concept page title`).toBe(route==='tags'?'Tags & categories':breadcrumb);
        // The concept's own nav-row hover (style.css:183-186) is what production is held to.
        expect.soft(referenceHovers.navRow,`${key} concept nav-row hover`).toMatchObject({hovered:true,color:reference.chrome.body.color});
        expect.soft(actualHovers,`${key} hover states`).toEqual(referenceHovers);
        expect.soft(diff,`${key} differences after the recorded deviations`).toEqual([]);
        expect.soft(actual.noPageScroll,`${key} no page-level horizontal scroll`).toBe(true);
        if(referenceRings) {
            // The toolkit's button ring (2px offset) wins over the concept's 3px for buttons, in the concept too (#245).
            expect.soft(referenceRings.headingButton,`${key} concept heading-button ring`).toMatchObject({focused:true,visible:true,outlineStyle:'solid',outlineWidth:'2px'});
            expect.soft(referenceRings.navLink,`${key} concept nav-link ring`).toMatchObject({focused:true,outlineStyle:'solid',outlineWidth:'2px',outlineOffset:'3px'});
            expect.soft(actualRings,`${key} focus rings`).toEqual(referenceRings);
        }
    }
});

// Saved views (#249 D16): a view saved from the Images toolbar adds the concept's own saved row (search icon, name, no
// count) to SAVED VIEWS. The concept's demo action reads "Apply in prototype"; production's is "Save" (D29).
for(const [width,height] of SIZES)test(`a saved view row matches the concept's ${width}x${height}`,async({page})=>{
    await page.setViewportSize({width,height});
    const save=async(action,mainSelector)=>{
        await page.locator(`${mainSelector} .toolbar`).getByRole('button',{name:'Save view',exact:true}).click();
        const dialog=page.getByRole('dialog',{name:'Save this view',exact:true});
        await expect(dialog.getByRole('textbox',{name:'View name',exact:true})).toHaveValue('My workspace view');
        await dialog.getByRole('textbox',{name:'View name',exact:true}).fill('Golden hour');
        await dialog.getByRole('button',{name:action,exact:true}).click();
        await expect(page.locator('#saved-nav').getByRole('link',{name:'Golden hour',exact:true})).toHaveCount(1);
        await page.mouse.move(0,0);
        return inventoryOf(page,mainSelector);
    };
    await openConcept(page,'images','dark');
    const reference=await save('Apply in prototype','#main');
    expect(reference.chrome.navs.find(nav=>nav.id==='saved-nav').rows.map(row=>row.text)).toEqual(['Favorites','Needs review','Golden hour']);
    await openProduction(page,'images','dark');
    const actual=await save('Save','#workspace-main');
    expect(diffInventory(mapShellConcept(reference).chrome.navs,actual.chrome.navs)).toEqual([]);
    expect(actual.chrome.navs.find(nav=>nav.id==='saved-nav').rows.map(row=>[row.text,row.count]))
        .toEqual([['Favorites',reference.chrome.navs.find(nav=>nav.id==='saved-nav').rows[0].count],['Golden hour',null]]);
});

test('the inventory reports a missing shell element by its path instead of failing',async({page})=>{
    await page.setViewportSize({width:1600,height:1100});
    await openConcept(page,'tags','dark');await page.mouse.move(0,0);
    const reference=mapShellConcept(await inventoryOf(page,'#main'));
    await openProduction(page,'tags','dark');await page.mouse.move(0,0);
    expect(diffInventory(reference.chrome,(await inventoryOf(page,'#workspace-main')).chrome)).toEqual([]);
    await page.locator('.breadcrumbs').evaluate(node=>node.remove());
    await page.locator('.connection small').evaluate(node=>node.remove());
    const actual=await inventoryOf(page,'#workspace-main');
    expect(actual.chrome.breadcrumbs).toBeNull();
    expect(actual.chrome.connection.smallFont).toBeNull();
    const paths=diffInventory(reference.chrome,actual.chrome).map(item=>item.path);
    expect(paths).toContain('breadcrumbs');
    expect(paths).toContain('connection.smallFont');
    expect(paths).toContain('connection.small');
});

test('the concept fixture renders the concept inspector facts',async({page})=>{
    await page.setViewportSize({width:1600,height:1100});
    await openProduction(page,'images','dark');
    // The first image is the concept's first sample, selected into the panel on first load (D19).
    const facts=page.locator('#workspace-main #image-inspector .meta-grid');
    await expect(facts).toBeVisible();
    const pairs=await facts.evaluate(list=>Object.fromEntries([...list.querySelectorAll('dt')].map(term=>[term.textContent.trim(),term.nextElementSibling.textContent.trim()])));
    // app.js:404-408: the concept's facts for alpine_00001.png.
    expect(pairs).toEqual({Seed:'42001',Steps:'28',CFG:'6.5',Sampler:'dpmpp_2m',Scheduler:'karras',Model:'illustrative-model.safetensors'});
    // D21: execution evidence is the concept's "Execution recorded" badge.
    await expect(page.locator('#image-inspector span.chip.blue')).toHaveText('Execution recorded');
});

// Task A7: the comparison is only worth something if it notices a change. One CSS mutation (a nav-row height the
// concept never uses) and one DOM mutation (the breadcrumbs removed) are applied to production after an exact match,
// and each must make the comparison fail with the path of what changed.
for(const [width,height] of SIZES)test(`shell comparison detects mutations ${width}x${height}`,async({page})=>{
    await page.setViewportSize({width,height});
    await openConcept(page,'tags','dark');await page.mouse.move(0,0);
    const reference=await inventoryOf(page,'#main');
    await openProduction(page,'tags','dark');await page.mouse.move(0,0);
    const measure=()=>inventoryOf(page,'#workspace-main');
    const clean=await measure();
    assertShellMatches('tags',reference,clean);

    const restoreCss=await mutate(page,{css:'.sidebar nav a { min-height: 30px }'});
    const cssMutated=await measure();
    expect(()=>assertShellMatches('tags',reference,cssMutated)).toThrow(/navs\.0\.rows\.0\.minHeight/);
    expect(()=>assertShellMatches('tags',reference,cssMutated)).toThrow(/navs\.0\.rows\.0\.height/);
    await restoreCss();
    assertShellMatches('tags',reference,await measure());

    await mutate(page,{remove:['.breadcrumbs']});
    const domMutated=await measure();
    expect(domMutated.chrome.breadcrumbs).toBeNull();
    // The missing section is reported by its own path (A6: absent sections become null), not by a measuring error.
    expect(()=>assertShellMatches('tags',reference,domMutated)).toThrow(/(at |, )chrome\.breadcrumbs(,|$)/);
});
