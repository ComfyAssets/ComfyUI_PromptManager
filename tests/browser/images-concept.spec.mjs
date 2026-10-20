import {writeFile} from 'node:fs/promises';
import {test,expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
import {captureCanonicalState} from './history-canonical-comparison.mjs';
import {SIZES,THEMES,MOTIONS,seedConcept,openConcept,openProduction,inventoryOf,diffInventory,mutate,stripExact} from './concept-inventory.mjs';

// Task C7 of #249: the production Images page against the immutable concept, on the concept-shaped real fixture (24
// images of the concept's six prompts, its seeds, sizes and favorites). Heading, toolbar, filter row, caption, grid,
// the first six cards, the continuation and the panel box are compared at 1600x1100 and 390x844 in both themes, and the
// page's behaviors under normal and reduced motion; grid, cards and panel also at 1200x900 and 1800x1100 (2 and 4
// columns, the 275px and 310px panel). The topbar and sidebar (D5, D28, D29) are the shell spec's.
// Paired captures are artifacts only; there are no PNG baselines.
test.use({locale:'en-US',timezoneId:'UTC'});

// D37: the concept loads 12 images per batch (app.js imageBatchSize) and production one listing page (40), so both are
// compared once the 24 fixture images are all loaded: the concept after its own Load more press.
const FIXTURE_IMAGES=24;
const CONCEPT_BATCH=12;
const PRODUCTION_BATCH=40;
// Load more in production needs more than one listing page: the fixture continues the concept's rounds (two pages).
const LOAD_MORE_EXTRA=2*PRODUCTION_BATCH-FIXTURE_IMAGES;
const CARDS='.gallery-body > .image-grid > .media-card';
const WIDE_SIZES=Object.freeze([[1200,900],[1800,1100]]);

/** D35: production uses the singular for exactly one; the concept's plurals are hard-coded (app.js 447, 489, 517-521). */
export const countCopy=text=>text.replace(/(^|\D)1 positive prompts\b/g,'$11 positive prompt')
    .replace(/(^|\D)1 more images\b/g,'$11 more image').replace(/(^|\D)1 images\b/g,'$11 image');

/** D17: "<Today|Yesterday|weekday> · <Month D>" of the newest file (the fixture's own mtime), in the test's UTC zone. */
function captionDay(seconds,now=new Date()) {
    const day=value=>Date.UTC(value.getUTCFullYear(),value.getUTCMonth(),value.getUTCDate());
    const date=new Date(seconds*1000),days=Math.round((day(now)-day(date))/86400000);
    const name=days===0?'Today':days===1?'Yesterday':date.toLocaleDateString('en-US',{weekday:'long',timeZone:'UTC'});
    const options={month:'long',day:'numeric',timeZone:'UTC',...(date.getUTCFullYear()===now.getUTCFullYear()?{}:{year:'numeric'})};
    return `${name} · ${date.toLocaleDateString('en-US',options)}`;
}

/**
 * The row's flex lines laid out again from the concept's own item boxes, in order: an item breaks to a new line when its
 * hypothetical size (`flex-basis: 100%` → the row; a growing item → its min-width; otherwise its box) no longer fits
 * after the line's items and gaps; a growing item (the search field, `flex: 1`) takes its line's free space; items pack
 * from the start and centre in their line (`align-items: center`). The test proves this model on the concept rows first.
 */
export function reflow(row,items) {
    const gap=parseFloat(row.columnGap)||0,rowGap=parseFloat(row.rowGap)||0;
    // The unrounded boxes (`exact`); rounding happens once, on the result.
    const rowWidth=row.exact?.width??row.width;
    const exact=item=>item.exact??{left:item.left,width:item.width};
    const grows=item=>parseFloat(item.flexGrow)>0;
    const basis=item=>item.flexBasis==='100%'?rowWidth:grows(item)?parseFloat(item.minWidth)||0:exact(item).width;
    const lines=[];
    const order=row.items.filter(entry=>entry.shown);
    for(const item of items.filter(entry=>entry.shown)) {
        const line=lines.at(-1),used=line?line.reduce((sum,entry)=>sum+basis(entry)+gap,0):0;
        // The slack is one layout unit (1/60px in Firefox), which float sums of the browser's boxes may exceed.
        if(line&&used+basis(item)<=rowWidth+1/60)line.push(item);else lines.push([item]);
    }
    const placed=new Map();let top=0;
    const sameLine=(a,b)=>a.top<b.top+b.height&&b.top<a.top+a.height;
    const freeOf=line=>rowWidth-line.reduce((sum,item)=>sum+basis(item),0)-gap*(line.length-1);
    for(const line of lines) {
        const free=freeOf(line);
        const growing=line.filter(grows);
        const height=Math.max(...line.map(item=>item.height));
        let x=0,previous=null;
        for(const item of line) {
            // A growing item keeps its concept width plus the space its line gained.
            const width=growing.includes(item)?exact(item).width+(free-freeOf(order.filter(entry=>sameLine(entry,item))))/growing.length:exact(item).width;
            // An item that followed the same neighbour on the same line in the concept keeps the concept's own spacing,
            // moved by however much that neighbour grew; otherwise it follows the previous box and the gap.
            const adjacent=previous&&order.indexOf(item)===order.indexOf(previous)+1&&sameLine(item,previous);
            const before=previous&&placed.get(previous);
            const left=line.indexOf(item)===0?0:adjacent?before.left+exact(item).left-exact(previous).left+before.width-exact(previous).width:x;
            placed.set(item,{left,width,top:Math.round(top+(height-item.height)/2)});
            x=left+width+gap;previous=item;
        }
        top+=height+rowGap;
    }
    const moved=item=>{
        const box=placed.get(item);
        if(!box)return item;
        const grown=box.width-exact(item).width;
        return {...item,left:Math.round(box.left),top:box.top,width:Math.round(box.width),
            input:item.input&&{...item.input,width:Math.round((item.input.exact?.width??item.input.width)+grown)}};
    };
    return {...row,height:Math.round(top-rowGap),items:items.map(moved)};
}

/** A flex row without the items `isRemoved` picks; the remaining items flow again from the concept's boxes. */
function withoutItems(row,isRemoved) {
    return reflow(row,row.items.filter(item=>!isRemoved(item)));
}

/**
 * D8 (follow-up PR): the overlay row without its checkbox label. The concept stretches the star to the label's line box
 * (`align-items: normal`); alone, the row and the star are the star's own height: its min-height, or its line plus
 * padding and border when taller. Its place stays the concept's top-right (`rightInset`).
 */
function withoutCheckbox(overlays) {
    // A list row hides its overlays (concept `display: none`).
    if(!overlays.shown)return overlays;
    const items=overlays.items.filter(item=>item.tag!=='label');
    const own=item=>Math.max(parseFloat(item.minHeight)||0,parseFloat(item.lineHeight)+['paddingTop','paddingBottom','borderTopWidth','borderBottomWidth']
        .reduce((sum,key)=>sum+parseFloat(item[key]),0));
    const height=Math.max(...items.filter(item=>item.shown).map(own));
    // D8 (interim): alone, the star is pushed to the right by `margin-left: auto`, whose used value is the overlay's
    // free width; the checkbox PR removes that rule and this line.
    const pushed=item=>items.length===1?{marginLeft:`${overlays.width-item.width}px`}:{};
    return {...overlays,height,items:items.map(item=>item.shown?{...item,height,...pushed(item)}:item)};
}

/**
 * A list row's info once D9 narrows its actions to `actionsWidth`, from the concept's boxes:
 * - D3 (the measurable list row, `.list-grid .media-info` in the overlay): the info fills the row beside the art and may
 *   shrink below a long nowrap title (`min-width: 0`), so it is the card's inner width less the art, and the actions
 *   end at its right padding; it keeps its own height (text or actions plus padding), centred in the row the concept
 *   stretches it across, so the text, centred in either, moves only by half the lines it lost;
 * - D9: the wider text column fits a subtitle the concept wrapped when its one-line width (`maxContent`) now fits.
 */
function listInfo(card,actionsWidth) {
    const {info}=card,{copy,p}=info;
    const width=card.width-2*parseFloat(card.borderTopWidth)-card.art.width;
    const column=width-parseFloat(info.paddingLeft)-parseFloat(info.paddingRight)-parseFloat(info.columnGap)-actionsWidth;
    const line=parseFloat(p.lineHeight);
    const pHeight=p.height>line&&p.maxContent<=column?line:p.height;
    const copyHeight=copy.height-(p.height-pHeight);
    const natural=Math.max(copyHeight,info.actions.height)+parseFloat(info.paddingTop)+parseFloat(info.paddingBottom);
    // The row's inner box is the stretched concept info: its top is the card border, its height the art's.
    return {info:{width,top:Math.round(info.top+(info.height-natural)/2),height:Math.round(natural)},
        copy:{height:copyHeight,top:Math.round(info.top+(info.height-copyHeight)/2)},p:{height:pHeight},
        actionsLeft:info.left+width-parseFloat(info.paddingRight)-actionsWidth};
}

/** Recorded Images deviations only (spec §8); everything else in the concept inventory must equal production. */
const nocase=(a,b)=>{const x=a.toLowerCase(),y=b.toLowerCase();return x<y?-1:x>y?1:a<b?-1:a>b?1:0;};

export function mapImagesConcept(reference,{manifest,now=new Date()}) {
    const paths=new Map(manifest.images.map(image=>[image.filename,image.path]));
    const toolbar=withoutItems(reference.toolbar,item=>item.text==='Save view');
    // D16 (follow-up PR): no Save view until the saved-views store ships. D14: the sort appends production's two sorts.
    toolbar.items=toolbar.items.map(item=>item.id==='sort'?{...item,options:[...item.options,'Oldest first','Name Z–A']}:item);
    // D36: the category list is the Library's real list, sorted alphabetically (database/library.py `ORDER BY category
    // COLLATE NOCASE, category`); the concept lists its samples in prompt order (app.js 348). Only the order is mapped.
    toolbar.items=toolbar.items.map(item=>item.id==='category-filter'
        ?{...item,options:[item.options[0],...item.options.slice(1).sort(nocase)]}:item);
    // D29: no "Illustrative previews" sample tag.
    const filterRow=withoutItems(reference.filterRow,item=>String(item.className).includes('sample-tag'));
    filterRow.items=filterRow.items.map(item=>{
        // D32: the layout buttons may wrap at extreme zoom (they never do while they fit).
        if(item.className==='segmented')return {...item,flexWrap:'wrap'};
        // D14: the scope chip may shrink and wrap a long source or folder name inside itself.
        if(item.tag==='span'&&item.className==='chip')return {...item,minWidth:'0px'};
        return item;
    });
    // A shorter toolbar or filter row moves what follows it up by as much.
    const toolbarRise=reference.toolbar.height-toolbar.height;
    filterRow.top-=toolbarRise;
    const rise=toolbarRise+reference.filterRow.height-filterRow.height;
    const list=reference.grid.className.split(' ').includes('list-grid');
    const cards=reference.cards.map(card=>{
        const filename=card.open.label.replace(/^Open /,'');
        const actions=card.info.actions;
        const tag=actions.buttons.find(button=>button.label?.startsWith('Tag or auto-tag '));
        // D9: no Tag button until the image-annotations slice; the actions group narrows by its box and gap.
        const freed=tag?tag.width+parseFloat(actions.columnGap):0;
        // D3: a list row's info keeps its own height (text or actions plus its padding) so bounded rendering can measure
        // it, centred in the row the concept stretches it across; the text and the actions keep their place.
        const actionsWidth=actions.width-freed;
        const listRow=list?listInfo(card,actionsWidth):{};
        return {...card,
            // D29: the concept's alt adds its demo-only " · sample illustration N" to the card title.
            img:{...card.img,alt:card.img.alt.replace(/ · sample illustration \d+$/,'')},
            // D8 (follow-up PR): no selection checkbox yet; the star keeps the concept's top-right place.
            overlays:withoutCheckbox(card.overlays),
            info:{...card.info,...listRow.info,copy:{...card.info.copy,...listRow.copy},p:{...card.info.p,...listRow.p},
                // D10: the title keeps the file's relative path (the fixture manifest's) in `title`.
                h3:{...card.info.h3,title:paths.get(filename)??null},
                actions:{...actions,width:actionsWidth,left:listRow.actionsLeft??actions.left+freed,
                    buttons:actions.buttons.filter(button=>button!==tag)}}};
    });
    // D17: the right-hand date is the newest result's day under Recently added, here the fixture's newest mtime.
    // D35: the count line uses the singular for one.
    const caption={...reference.caption,spans:reference.caption.spans.map((span,index)=>index===1
        ?{...span,text:captionDay(manifest.newest_modified,now)}:{...span,text:countCopy(span.text)})};
    const continuation=mapContinuation(reference.continuation);
    return {...reference,toolbar,filterRow,cards,caption,continuation,panel:{...reference.panel,top:reference.panel.top-rise}};
}

/** D32: the continuation's text and Load more may wrap at extreme zoom; D35: its count uses the singular for one. */
function mapContinuation(continuation,numbers=null) {
    return {...continuation,flexWrap:'wrap',items:continuation.items.map(item=>item.tag!=='p'?item:{...item,
        // D37: a row with more to load counts production's own page ("N of M"), not the concept's 12-image batch.
        text:countCopy(numbers?item.text.replace(/^\d+ of \d+/,`${numbers.loaded} of ${numbers.total}`):item.text)})};
}

/** D37: the Load more row before its press, the concept's at 12 of 24 against production's at its own page. */
function compareLoadingRow(reference,actual,count) {
    const [,loaded,total]=count.match(/^(\d+) of (\d+)/);
    // The row's place follows the grid above it, whose height is the batch's (D37).
    const unplaced=({top,...row})=>stripExact(row);
    return diffInventory(unplaced(mapContinuation(reference,{loaded,total})),unplaced(actual));
}

/** D15: the Import dialog's two real operations, with the ruled intro and the Rescan wording. */
function mapImportConcept(reference) {
    return {...reference,
        intro:{...reference.intro,text:'Image inspection and output scanning have different effects. Choose the workflow you want.'},
        rows:reference.rows.filter(row=>row.title!=='Import portable recipes').map(row=>row.title==='Scan an output folder'
            ?{...row,description:'Find files added or removed in the output folders.',buttons:['Rescan']}:row)};
}

const SECTIONS=['heading','toolbar','filterRow','caption','grid','cards','continuation','panel','noPageScroll'];
const pick=inventory=>stripExact(inventory);

// The concept's own sample categories, in its first-appearance order (app.js 348): what D36 reorders.
const CONCEPT_CATEGORIES=['All categories','Landscapes','Architecture','Environments','Illustration'];

function compare(reference,actual,manifest,sections=SECTIONS) {
    const only=inventory=>Object.fromEntries(sections.map(key=>[key,inventory[key]]));
    return diffInventory(pick(only(mapImagesConcept(reference,{manifest}))),pick(only(actual)));
}

/** The comparison as a gate: throws, naming every differing path, unless production equals the mapped concept. */
function assertImagesMatch(reference,actual,manifest) {
    const diff=compare(reference,actual,manifest);
    if(diff.length)throw new Error(`the Images page differs from the concept at ${diff.map(item=>item.path).join(', ')}`);
}

/**
 * D3 (masonry sub-pixel snapping): bounded rendering places masonry cards from the layout arithmetic (media width over
 * the file's ratio), while the concept's flow snaps each box to the browser's layout unit (1/64px in Chromium and
 * WebKit, 1/60px in Firefox) before measuring the next. So masonry tops and heights are compared unrounded instead,
 * within one layout unit per card above in the column (the card's own included); the rounded fields are skipped there.
 */
const MASONRY_ROUNDED=/^cards\.\d+\.(top|height)$/;
function masonryDrift(reference,actual) {
    return reference.cards.flatMap((card,index)=>{
        const above=reference.cards.slice(0,index).filter(other=>Math.abs(other.exact.left-card.exact.left)<1).length;
        const bound=(above+1)/60+1e-6,other=actual.cards[index];
        return ['top','height'].filter(key=>!(Math.abs(card.exact[key]-other.exact[key])<=bound))
            .map(key=>({path:`cards.${index}.exact.${key}`,reference:card.exact[key],actual:other.exact[key],bound}));
    });
}
function compareLayout(layout,reference,mapped,actual) {
    const diff=diffInventory(stripExact({grid:mapped.grid,cards:mapped.cards}),stripExact(actual));
    return layout==='Masonry'?[...diff.filter(item=>!MASONRY_ROUNDED.test(item.path)),...masonryDrift(reference,actual)]:diff;
}

// Two frames, then every running transition (a card image zooming back after the pointer left it) finished.
const settle=page=>page.evaluate(async()=>{
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    await Promise.race([Promise.all(document.getAnimations().map(animation=>animation.finished.catch(()=>{}))),
        new Promise(resolve=>setTimeout(resolve,2000))]);
});
const countText=page=>page.locator('#result-count').textContent();
const toastText=page=>page.locator('.toast').textContent();

/** The concept with every fixture image loaded through its own Load more button; returns what the press did. */
async function openConceptLoaded(page,theme,motion) {
    await openConcept(page,'images',theme,motion);await page.mouse.move(0,0);await settle(page);
    const before=await page.locator(CARDS).count();
    const loadingRow=(await inventoryOf(page,'#main')).continuation;
    // A dispatched press: scrolling the button into view would let the concept's own observer load the batch first.
    // Where the first batch leaves the row in view (1800px, four columns), that observer has already loaded it.
    if(before<FIXTURE_IMAGES)await page.getByRole('button',{name:'Load more images',exact:true}).dispatchEvent('click');
    await expect(page.locator(CARDS)).toHaveCount(FIXTURE_IMAGES);
    const load={before,after:await page.locator(CARDS).count(),toast:await toastText(page),count:await countText(page),loadingRow};
    await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo(0,0);});await settle(page);
    return load;
}

async function openProductionLoaded(page,theme,motion,total=FIXTURE_IMAGES) {
    await openProduction(page,'images',theme,motion);await page.mouse.move(0,0);
    await expect(page.locator('#result-count')).toHaveText(new RegExp(`^${Math.min(total,PRODUCTION_BATCH)} of ${total} images`));
    await expect(page.locator(CARDS)).toHaveCount(Math.min(total,PRODUCTION_BATCH));
    await settle(page);
}

/** The Import dialog's intro and operation rows (D15). */
async function importDialogOf(page) {
    await page.locator('.heading-actions').getByRole('button',{name:'Import',exact:true}).click();
    const dialog=page.locator('dialog#modal[open]');
    await expect(dialog).toBeVisible();
    const inventory=await dialog.evaluate(node=>{
        const css=(element,...keys)=>{const style=getComputedStyle(element);return Object.fromEntries(keys.map(key=>[key,style[key]]));};
        const font=element=>css(element,'fontSize','fontWeight','lineHeight','color');
        const intro=node.querySelector('#modal-body > p.small');
        return {title:node.querySelector('#modal-title').textContent,
            intro:intro&&{text:intro.textContent,...font(intro)},
            ...css(node.querySelector('.operation-list'),'display','rowGap'),
            rows:[...node.querySelectorAll('.operation-list > .operation')].map(row=>({title:row.querySelector('h3').textContent,
                description:row.querySelector('p').textContent,buttons:[...row.querySelectorAll('button')].map(button=>button.textContent.trim()),
                titleFont:font(row.querySelector('h3')),descriptionFont:font(row.querySelector('p')),...css(row,'display','alignItems')}))};
    });
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    return inventory;
}

/** The hovered first card image after its transition, then the pointer parked again. */
async function hoverOf(page) {
    const open=page.locator(CARDS).first().locator('.image-open');
    await open.hover();
    // The concept's transition is 0.2s; reduced motion makes it instant.
    await page.waitForTimeout(450);
    const state=await open.locator('img').evaluate(img=>({hovered:img.closest('.image-open').matches(':hover'),
        transform:getComputedStyle(img).transform,transitionDuration:getComputedStyle(img).transitionDuration,
        transitionProperty:getComputedStyle(img).transitionProperty,hoverNone:matchMedia('(hover: none)').matches}));
    await page.mouse.move(0,0);
    return state;
}

const activeOf=page=>page.evaluate(()=>{const node=document.activeElement;
    return {tag:node?.tagName.toLowerCase()??null,label:node?.getAttribute('aria-label')??null,
        card:node?.closest('.media-card')?[...node.closest('.image-grid').children].indexOf(node.closest('.media-card')):null};});
const selectedOf=page=>page.locator(CARDS).evaluateAll(nodes=>nodes.flatMap((node,index)=>node.classList.contains('selected')?[index]:[]));

const LAYOUTS=['List','Masonry','Grid','Large','Compact'];
/** The card boxes in each layout: Grid → List → Masonry → Grid, then the Large and Compact thumbnail sizes. */
async function layoutsOf(page,scope) {
    const result={};
    for(const layout of LAYOUTS) {
        if(['Large','Compact'].includes(layout))await page.locator('#image-size').selectOption(layout.toLowerCase());
        else {
            await page.locator('.filter-row .segmented').getByRole('button',{name:layout,exact:true}).click();
            await expect(page.locator('.filter-row .segmented').getByRole('button',{name:layout,exact:true})).toHaveAttribute('aria-pressed','true');
        }
        await page.mouse.move(0,0);await settle(page);await settle(page);
        const inventory=await inventoryOf(page,scope);
        result[layout]={grid:inventory.grid,cards:inventory.cards};
    }
    await page.locator('#image-size').selectOption('comfortable');await settle(page);
    return result;
}

const continuationText=page=>page.locator('.infinite-continuation > p').first().textContent();
/** A search: the count line, the continuation and the cards it leaves, then the search cleared again. */
async function searchOf(page,term) {
    const before=await countText(page);
    await page.locator('#search').fill(term);
    await expect.poll(()=>countText(page)).not.toBe(before);
    await expect.poll(()=>page.locator(CARDS).count()).toBeLessThan(FIXTURE_IMAGES);
    await settle(page);
    const result={count:await countText(page),continuation:await continuationText(page),cards:await page.locator(`${CARDS} .image-open`).evaluateAll(nodes=>nodes.map(node=>node.getAttribute('aria-label')))};
    await page.locator('#search').fill('');
    await expect(page.locator('#result-count')).toHaveText(before);
    return result;
}

/** The behaviors §7 lists, run with identical selectors in both documents. */
async function behaviorsOf(page,scope) {
    const hover=await hoverOf(page);
    const second=page.locator(CARDS).nth(1).locator('.image-open');
    const opened=await second.getAttribute('aria-label');
    await second.click();
    const viewer=page.locator('#viewer-dialog[open]');
    await expect(viewer).toBeVisible();
    const viewerTitle=await page.locator('#viewer-title').textContent();
    await page.keyboard.press('Escape');
    await expect(viewer).toHaveCount(0);
    await settle(page);
    const focusAfterViewer=await activeOf(page);
    await page.locator(CARDS).nth(2).locator('[aria-label^="Inspect"]').click();
    await expect.poll(()=>selectedOf(page)).toEqual([2]);
    const selected=await selectedOf(page);
    const layouts=await layoutsOf(page,scope);
    const search=await searchOf(page,'alpine');
    // Exactly one image (a unique filename stem): both counted nouns in the singular (D35).
    const single=await searchOf(page,'alpine_00003');
    return {hover,opened,viewerTitle,focusAfterViewer,selected,layouts,search,single};
}

test.beforeEach(async({request},testInfo)=>{testInfo.manifest=await seedConcept(request);});

for(const [width,height] of SIZES)for(const theme of THEMES)test(`images page geometry, tokens, controls and behaviors match the concept ${width}x${height} ${theme}`,async({page,request},testInfo)=>{
    test.setTimeout(240_000);
    await page.setViewportSize({width,height});
    const manifest=testInfo.manifest;const result={};
    for(const motion of MOTIONS) {
        const capture=motion==='no-preference';
        const conceptLoad=await openConceptLoaded(page,theme,motion);
        const reference=await inventoryOf(page,'#main');
        if(capture)await captureCanonicalState(page,testInfo,`concept-images-${width}-${theme}`);
        // The browser as the re-layout's oracle: the concept page itself without Save view, then without the sample tag.
        const removed={};
        for(const [key,selector] of [['toolbar','.toolbar > [data-action="save-view"]'],['filterRow','.filter-row > .sample-tag']]) {
            const restore=await mutate(page,{remove:[selector]});
            removed[key]=(await inventoryOf(page,'#main'))[key];
            await restore();
        }
        const referenceImport=await importDialogOf(page);
        const referenceBehaviors=await behaviorsOf(page,'#main');

        await openProductionLoaded(page,theme,motion);
        const actual=await inventoryOf(page,'#workspace-main');
        if(capture)await captureCanonicalState(page,testInfo,`production-images-${width}-${theme}`);
        const actualImport=await importDialogOf(page);
        const actualBehaviors=await behaviorsOf(page,'#workspace-main');
        result[motion]={conceptLoad,reference,removed,actual,diff:compare(reference,actual,manifest),referenceImport,actualImport,
            importDiff:diffInventory(mapImportConcept(referenceImport),actualImport),referenceBehaviors,actualBehaviors};
    }
    // Load more in production: a fixture past one listing page appends one batch and shows the concept's toast.
    testInfo.manifest=await seedConcept(request,{extra_images:LOAD_MORE_EXTRA});
    for(const motion of MOTIONS) {
        const total=FIXTURE_IMAGES+LOAD_MORE_EXTRA;
        await openProductionLoaded(page,theme,motion,total);
        const before=await page.locator(CARDS).count();
        const loadingRow=(await inventoryOf(page,'#workspace-main')).continuation,loadingCount=await countText(page);
        await page.getByRole('button',{name:'Load more images',exact:true}).click();
        await expect(page.locator('#result-count')).toHaveText(new RegExp(`^${total} of ${total} images`));
        result[motion].productionLoad={before,after:await page.locator(CARDS).count(),toast:await toastText(page),count:await countText(page)};
        result[motion].loadingDiff=compareLoadingRow(result[motion].conceptLoad.loadingRow,loadingRow,loadingCount);
    }
    const path=testInfo.outputPath('images-geometry.json');await writeFile(path,JSON.stringify(result,null,2));
    await testInfo.attach('images-geometry',{path,contentType:'application/json'});

    for(const [motion,entry] of Object.entries(result)) {
        const {conceptLoad:{loadingRow,...conceptLoad},productionLoad,loadingDiff,reference,removed,actual,diff,referenceImport,importDiff,
            referenceBehaviors:concept,actualBehaviors:production}=entry;
        // The concept itself is what this comparison assumes: its first batch, its sample card and its copy.
        expect.soft(conceptLoad,`${motion} concept Load more`).toEqual({before:CONCEPT_BATCH,after:FIXTURE_IMAGES,
            toast:`${CONCEPT_BATCH} more images loaded. ${FIXTURE_IMAGES} of ${FIXTURE_IMAGES}.`,count:`${FIXTURE_IMAGES} of ${FIXTURE_IMAGES} images · 6 positive prompts`});
        // D37: production appends one listing page, with the concept's toast and count copy (D35 for one).
        expect.soft(productionLoad,`${motion} production Load more`).toEqual({before:PRODUCTION_BATCH,after:2*PRODUCTION_BATCH,
            toast:countCopy(`${PRODUCTION_BATCH} more images loaded. ${2*PRODUCTION_BATCH} of ${2*PRODUCTION_BATCH}.`),
            count:countCopy(`${2*PRODUCTION_BATCH} of ${2*PRODUCTION_BATCH} images · 6 positive prompts`)});
        // D37: the row with more to load, before its press, in both documents.
        expect.soft(loadingRow.items.map(item=>item.text),`${motion} concept loading row`).toEqual([`${CONCEPT_BATCH} of ${FIXTURE_IMAGES} images · more load as you scroll`,'Load more images']);
        expect.soft(loadingDiff,`${motion} loading row differences after D32, D35, D37`).toEqual([]);
        // D11: the concept's subtitle is the fixture's own "W × H · PNG · Seed N".
        const first=manifest.images[0];
        expect.soft(reference.cards[0].info.p.text,`${motion} concept subtitle`).toBe(`${first.width} × ${first.height} · PNG · Seed ${first.seed}`);
        expect.soft(reference.cards[0].info.h3.fontSize,`${motion} concept h3 size`).toBe('11px');
        expect.soft(referenceImport.rows.map(row=>row.title),`${motion} concept Import rows`).toEqual(['Inspect a local image','Scan an output folder','Import portable recipes']);
        // The row model reproduces the concept's own rows, and the concept page itself once Save view (D16) or the
        // sample tag (D29) is removed from it at runtime: the browser is the oracle for the remainders the mapping uses.
        expect.soft(diffInventory(stripExact(reflow(reference.toolbar,reference.toolbar.items)),stripExact(reference.toolbar)),`${motion} toolbar model`).toEqual([]);
        expect.soft(diffInventory(stripExact(reflow(reference.filterRow,reference.filterRow.items)),stripExact(reference.filterRow)),`${motion} filter row model`).toEqual([]);
        expect.soft(diffInventory(stripExact(withoutItems(reference.toolbar,item=>item.text==='Save view')),stripExact(removed.toolbar)),
            `${motion} toolbar without Save view`).toEqual([]);
        expect.soft(diffInventory(stripExact(withoutItems(reference.filterRow,item=>String(item.className).includes('sample-tag'))),stripExact(removed.filterRow)),
            `${motion} filter row without the sample tag`).toEqual([]);
        expect.soft(reference.toolbar.items.find(item=>item.id==='category-filter').options,`${motion} concept categories`).toEqual(CONCEPT_CATEGORIES);
        expect.soft(diff,`${motion} differences after the recorded deviations`).toEqual([]);
        expect.soft(importDiff,`${motion} Import dialog differences after D15`).toEqual([]);
        expect.soft(actual.noPageScroll,`${motion} no page-level horizontal scroll`).toBe(true);
        // Hover (C4): the concept's 1.03 zoom over 0.2s, instant under reduced motion; D18: none where there is no hover.
        expect.soft(concept.hover,`${motion} concept hover`).toMatchObject({hovered:true,transform:'matrix(1.03, 0, 0, 1.03, 0, 0)',
            transitionDuration:motion==='reduce'?'0s':'0.2s'});
        expect.soft(production.hover,`${motion} hover`).toEqual(production.hover.hoverNone?{...concept.hover,transform:'none'}:concept.hover);
        // The 2nd card's image opens the viewer titled "<title> · <filename>".
        expect.soft(concept.viewerTitle,`${motion} concept viewer title`).toBe(`${reference.cards[1].info.h3.text} · ${concept.opened.replace(/^Open /,'')}`);
        expect.soft(production.viewerTitle,`${motion} viewer title`).toBe(concept.viewerTitle);
        // Escape returns focus to the image that opened the viewer.
        expect.soft(production.focusAfterViewer,`${motion} focus after the viewer`).toEqual({tag:'button',label:concept.opened,card:1});
        // The concept re-renders its grid when the viewer closes (app.js 1737-1743), so its focus falls to the body;
        // §7 and D19 keep production's restore, which is what is asserted above.
        // Info on the 3rd card moves `.media-card.selected` there.
        expect.soft(production.selected,`${motion} selection after Info`).toEqual(concept.selected);
        // List (160×95 art) and Masonry (--media-ratio media) as the concept lays them out.
        expect.soft(concept.layouts.List.cards[0].img,`${motion} concept list art`).toMatchObject({width:160,height:95});
        for(const layout of LAYOUTS) {
            const mapped=mapImagesConcept({...reference,...concept.layouts[layout]},{manifest});
            expect.soft(compareLayout(layout,concept.layouts[layout],mapped,production.layouts[layout]),`${motion} ${layout} layout`).toEqual([]);
        }
        for(const card of concept.layouts.Masonry.cards) {
            const [w,h]=card.ratio.split('/').map(Number);
            expect.soft(Math.abs(card.img.width/card.img.height-w/h),`${motion} concept masonry ratio ${card.open.label}`).toBeLessThan(0.02);
        }
        // Searches leave the same images, count line and continuation in both documents (D35 for one).
        const mappedSearch=search=>({...search,count:countCopy(search.count),continuation:countCopy(search.continuation)});
        expect.soft(production.search,`${motion} search alpine`).toEqual(mappedSearch(concept.search));
        expect.soft(concept.single,`${motion} concept single result`).toMatchObject({count:'1 of 1 images · 1 positive prompts',cards:['Open alpine_00003.png']});
        expect.soft(production.single,`${motion} search for one image`).toEqual(mappedSearch(concept.single));
        expect.soft(production.single.count,`${motion} singular count line`).toBe('1 of 1 image · 1 positive prompt');
    }
});

// The comparison is only worth something if it notices a change (spec §7 mutation check): one CSS mutation (the
// `.media-info` padding) and one DOM mutation (the Inspect buttons removed), each applied after an exact match.
test('images comparison detects mutations',async({page},testInfo)=>{
    await page.setViewportSize({width:1600,height:1100});
    const manifest=testInfo.manifest;
    await openConceptLoaded(page,'dark','no-preference');
    const reference=await inventoryOf(page,'#main');
    await openProductionLoaded(page,'dark','no-preference');
    const measure=()=>inventoryOf(page,'#workspace-main');
    assertImagesMatch(reference,await measure(),manifest);

    const restoreCss=await mutate(page,{css:'.media-info { padding: 4px }'});
    const cssMutated=await measure();
    expect(()=>assertImagesMatch(reference,cssMutated,manifest)).toThrow(/cards\.0\.info\.paddingTop/);
    await restoreCss();await settle(page);
    assertImagesMatch(reference,await measure(),manifest);

    await mutate(page,{remove:['[aria-label^="Inspect"]']});
    const domMutated=await measure();
    expect(()=>assertImagesMatch(reference,domMutated,manifest)).toThrow(/cards\.0\.info\.actions\.buttons\.length/);
});

// Grid, cards and panel at the other concept breakpoints: 2 columns and the 275px panel at 1200, 4 columns and the
// 310px panel at 1800.
for(const [width,height] of WIDE_SIZES)for(const theme of THEMES)test(`images grid, cards and panel match the concept ${width}x${height} ${theme}`,async({page},testInfo)=>{
    await page.setViewportSize({width,height});
    const manifest=testInfo.manifest;
    await openConceptLoaded(page,theme,'no-preference');
    const reference=await inventoryOf(page,'#main');
    await captureCanonicalState(page,testInfo,`concept-images-${width}-${theme}`);
    await openProductionLoaded(page,theme,'no-preference');
    const actual=await inventoryOf(page,'#workspace-main');
    await captureCanonicalState(page,testInfo,`production-images-${width}-${theme}`);
    expect(reference.grid.columns,'concept columns').toBe(width<=1250?2:4);
    expect(reference.panel.box.width,'concept panel').toBe(width<=1250?275:310);
    expect(compare(reference,actual,manifest,['grid','cards','panel'])).toEqual([]);
});

// D35 for the toast and the continuation: production's Load more appending exactly one image, against the concept's
// own toast and row copy (app.js 489, 517-521) with production's numbers (D37).
test('one appended image reads in the singular',async({page,request})=>{
    // A second fixture seed (dozens of PNGs and their thumbnails) runs inside the test.
    test.setTimeout(120_000);
    await page.setViewportSize({width:1600,height:1100});
    const total=PRODUCTION_BATCH+1;
    await seedConcept(request,{extra_images:total-FIXTURE_IMAGES});
    await openProductionLoaded(page,'dark','no-preference',total);
    await expect(page.locator('.infinite-continuation > p').first()).toHaveText(countCopy(`${PRODUCTION_BATCH} of ${total} images · more load as you scroll`));
    // A dispatched press: scrolling the button into view lets the page's own observer load the one image first.
    await page.getByRole('button',{name:'Load more images',exact:true}).dispatchEvent('click');
    await expect(page.locator('#result-count')).toHaveText(new RegExp(`^${total} of ${total} images`));
    await expect(page.locator('.toast')).toHaveText(countCopy(`1 more images loaded. ${total} of ${total}.`));
    expect(countCopy('1 more images loaded. 41 of 41.')).toBe('1 more image loaded. 41 of 41.');
    await expect(page.locator('.infinite-continuation > p').first()).toHaveText(`You’re all caught up · ${total} images`);
});

// D3 (one footer height for the collection): bounded rendering cannot measure unrendered cards, so every row reserves
// the tallest measured footer. Two seedless files (D11 "W × H · PNG", one line) form the first row at 390px, where
// every concept subtitle wraps: their row is as tall as the wrapped rows, and every concept row moves down by one.
test('one footer height for the collection (D3)',async({page,request})=>{
    // A second fixture seed (dozens of PNGs and their thumbnails) runs inside the test.
    test.setTimeout(120_000);
    await page.setViewportSize({width:390,height:844});
    await openConceptLoaded(page,'dark','no-preference');
    const reference=await inventoryOf(page,'#main');
    expect(reference.grid.columns).toBe(2);
    expect(reference.cards.every(card=>card.info.p.height>parseFloat(card.info.p.lineHeight))).toBe(true);
    const manifest=await seedConcept(request,{seedless_images:2});
    await openProductionLoaded(page,'dark','no-preference',FIXTURE_IMAGES+2);
    const actual=await inventoryOf(page,'#workspace-main');
    const box=card=>({left:card.left,top:card.top,width:card.width,height:card.height});
    const pitch=reference.cards[2].top-reference.cards[0].top;
    expect(actual.cards.map(box)).toEqual([box(reference.cards[0]),box(reference.cards[1]),
        ...reference.cards.slice(0,4).map(card=>({...box(card),top:card.top+pitch}))]);
    const seedless=actual.cards.slice(0,2);
    expect(seedless.map(card=>card.open.label)).toEqual(manifest.seedless.map(image=>`Open ${image.filename}`));
    expect(seedless.map(card=>card.info.p.text)).toEqual(['1536 × 1152 · PNG','1536 × 1152 · PNG']);
    // Their own footer is one line shorter than the row it reserves.
    for(const card of seedless)expect(card.info.height).toBeLessThan(reference.cards[0].info.height);
});
