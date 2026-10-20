import {writeFile} from 'node:fs/promises';
import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import {captureCanonicalState} from './history-canonical-comparison.mjs';
import {SIZES,THEMES,MOTIONS,seedConcept,openConcept,openProduction,inventoryOf,diffInventory,focusRingOf} from './concept-inventory.mjs';

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

const removedItem=item=>String(item.className).includes('concept-badge')||(item.tag==='button'&&item.text.startsWith('Activity'));

/** The top-actions row once removed items are gone: a centered flex row, so its size and item offsets follow. */
function remainingTopActions(actions) {
    const kept=actions.items.filter(item=>!removedItem(item));
    const shown=kept.filter(item=>item.shown);
    const height=Math.max(...shown.map(item=>item.height));
    const gap=parseFloat(actions.columnGap);
    const width=shown.reduce((sum,item)=>sum+item.width,0)+gap*(shown.length-1);
    return {...actions,height,
        // A full-width row (<=700px) keeps its width; otherwise it shrinks to its remaining items.
        width:actions.fullWidth?actions.width:width,
        items:kept.map(item=>item.shown?{...item,top:Math.round((height-item.height)/2)}:item)};
}

/** The footer once its button is gone: paddings plus the remaining spans along the footer's flex direction. */
function remainingFooter(footer,spans) {
    const content=footer.flexDirection==='column'
        ?spans.reduce((sum,span)=>sum+span.height,0)+parseFloat(footer.rowGap)*(spans.length-1)
        :Math.max(...spans.map(span=>span.height));
    return `${parseFloat(footer.paddingTop)+parseFloat(footer.paddingBottom)+content}px`;
}

/** Recorded shell deviations only (spec §8). Everything else in the concept inventory must equal production. */
function mapShellConcept(reference) {
    const {chrome}=reference;
    const navs=chrome.navs
        // D28: SAVED VIEWS and COLLECTIONS are proposals with no persisted store, so their navs are not rendered.
        .filter(nav=>nav.id!=='saved-nav'&&nav.id!=='collections-nav')
        .map(nav=>{
            if(nav.id!=='system-nav')return nav;
            const activity=rowNamed(nav,'Activity'),system=rowNamed(nav,'System');
            return {...nav,rows:[
                // D5: no Activity row until an activity center exists. D28: no Design review (prototype page).
                system,
                // D28: the production Overview hub follows System, as a concept nav row without a count. Its icon is the
                // concept's own `activity` glyph, which production names `overview`.
                {...system,text:'Overview',current:null,gapAfter:null,icon:{...system.icon,d:activity.icon.d}},
            ]};
        });
    return {...reference,chrome:{...chrome,
        // D28: only the WORKSPACE group label remains.
        labels:chrome.labels.filter(label=>label.text==='WORKSPACE'),
        navs,
        connection:{...chrome.connection,
            // D28: truthful copy instead of "Demo connection · Classic / Nodes 2.0".
            small:'Your library, on your server',
            // D28: no fake connection state, so the dot is the neutral muted token (the concept's label color).
            dot:{...chrome.connection.dot,backgroundColor:chrome.labels[0].color}},
        // D29: no "Interactive concept" badge. D5: no Activity button. The row's size and the remaining items'
        // centered offsets follow from the concept's own item sizes.
        topActions:remainingTopActions(chrome.topActions),
        footer:(()=>{
            // D29: production footer copy (one line, as the concept's own copy is), and no "What changes & why"
            // (a prototype review page); the footer then holds only its span.
            const spans=chrome.footer.spans.map(span=>({...span,text:'ComfyAssets · Local tools. Your library.'}));
            return {...chrome.footer,spans,buttons:[],height:remainingFooter(chrome.footer,spans)};
        })(),
    },
    heading:reference.heading&&{...reference.heading,actions:reference.heading.actions&&{...reference.heading.actions,
        // #245 vocabulary ruling D2 (mapVocabularyConcept): Auto-tag images is omitted until the image-annotation slice.
        buttons:reference.heading.actions.buttons.filter(item=>item.text!=='Auto-tag images')}}};
}

/** Layout and typography of the heading without its page-owned copy and actions. */
function headingFrame(heading) {
    if(!heading)return heading;
    const {height,h1,description,actions,...frame}=heading;
    const {text,...h1Tokens}=h1;
    const {text:copy,...descriptionTokens}=description??{};
    const {buttons,stacked,...actionTokens}=actions??{};
    return {...frame,h1:h1Tokens,description:description?descriptionTokens:null,actions:actions?actionTokens:null};
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

test.beforeEach(async({request})=>{await seedConcept(request);});

for(const [width,height] of SIZES)for(const theme of THEMES)test(`shell geometry, tokens and controls match the concept ${width}x${height} ${theme}`,async({page},testInfo)=>{
    await page.setViewportSize({width,height});const result={};
    for(const motion of MOTIONS)for(const [route,breadcrumb] of ROUTES) {
        await openConcept(page,route,theme,motion);await page.mouse.move(0,0);
        const reference=await inventoryOf(page,'#main');
        if(motion==='no-preference')await captureCanonicalState(page,testInfo,`concept-shell-${route}-${width}-${theme}`);
        const referenceRings=FULL_HEADING.has(route)?await ringsOf(page,'#main'):null;
        await openProduction(page,route,theme,motion);await page.mouse.move(0,0);
        const actual=await inventoryOf(page,'#workspace-main');
        if(motion==='no-preference')await captureCanonicalState(page,testInfo,`production-shell-${route}-${width}-${theme}`);
        const actualRings=FULL_HEADING.has(route)?await ringsOf(page,'#workspace-main'):null;
        result[`${motion} ${route}`]={breadcrumb,reference,actual,diff:compare(route,reference,actual),referenceRings,actualRings};
    }
    const path=testInfo.outputPath('shell-geometry.json');await writeFile(path,JSON.stringify(result,null,2));
    await testInfo.attach('shell-geometry',{path,contentType:'application/json'});
    for(const [key,{breadcrumb,reference,actual,diff,referenceRings,actualRings}] of Object.entries(result)) {
        // The concept itself is what this comparison assumes: its sample counts and its shell controls.
        expect.soft(reference.chrome.navs[0].rows.map(row=>[row.text,row.count?.text??null]),`${key} concept WORKSPACE rows`)
            .toEqual([['Images','24'],['Prompt library','6'],['Tags & categories','15'],['Inspect a file',null]]);
        expect.soft(reference.chrome.topActions.items.map(item=>item.className||item.tag),`${key} concept top actions`)
            .toEqual(['concept-badge','button','theme-label','canvas-link']);
        expect.soft(reference.chrome.breadcrumbs.parts.at(-1).text,`${key} concept breadcrumb`).toBe(breadcrumb);
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
