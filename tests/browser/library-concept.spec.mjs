import {writeFile} from 'node:fs/promises';
import {test,expect} from './fixtures.mjs';
import {captureCanonicalState} from './history-canonical-comparison.mjs';
import {SIZES,THEMES,MOTIONS,seedConceptAt,openConcept,openProduction,inventoryOf,diffInventory,mutate,stripExact,countCopy,reflow} from './concept-inventory.mjs';

// Task E3 of #249: the production Prompt library shelf against the immutable concept (app.js renderLibrary a572–586,
// style.css s648–708), on the concept-shaped real fixture (the concept's six prompts with their negatives, categories,
// tags and ratings, four images each, run_count 4 and last_used_at now). Heading, toolbar, caption, grid and the six
// cards are compared at 1600x1100 and 390x844 in both themes and motion settings, the grid and cards also at 1800x1100
// (3 columns), and the shelf's behaviors in both documents. The topbar and sidebar are the shell spec's.
// Paired captures are artifacts only; there are no PNG baselines.
test.use({locale:'en-US',timezoneId:'UTC'});

const CARDS='.library-grid .prompt-card';
const CONCEPT_PROMPTS=6;

/** Recorded Library deviations only (spec §8); everything else in the concept inventory must equal production. */
const nocase=(a,b)=>{const x=a.toLowerCase(),y=b.toLowerCase();return x<y?-1:x>y?1:a<b?-1:a>b?1:0;};
export function mapLibraryConcept(reference) {
    const library=reference.library;
    // Save view is the concept's own (saved views, #249 D16 retired).
    const toolbar={...library.toolbar};
    toolbar.items=toolbar.items.map(item=>{
        // D14: the Library appends its Oldest first after the concept's sorts; D153 (#447) drops "Highest rated".
        if(item.id==='sort')return {...item,options:[...item.options.filter(option=>option!=='Highest rated'),'Oldest first']};
        // D36: the category list is the Library's real list, sorted alphabetically; the concept lists prompt order.
        if(item.id==='category-filter')return {...item,options:[item.options[0],...item.options.slice(1).sort(nocase)]};
        return item;
    });
    // A shorter toolbar moves what follows it up by as much.
    const rise=library.toolbar.height-toolbar.height;
    const cards=library.cards.map((card,index)=>({...card,
        // D148 (#437): each card leads with the concept's Images `.image-overlays` selection checkbox, placed absolutely
        // over the art, so nothing else moves; it is compared with the concept's own overlay below.
        children:['image-overlays',...card.children],
        // D23: "− N negative variants" uses the singular for one; that row is the concept's own, measured so (`oracle`).
        meta:card.meta.items[0]?.text===PLURAL_ONE?reference.oracle.meta[index]:card.meta,
        // D23: a long title wraps inside the title row, so the title button may shrink below its text (`min-width: 0`).
        title:{...card.title,items:card.title.items.map(item=>item.tag==='button'?{...item,minWidth:'0px'}:item)},
        // D43: tag chips come in the store's alphabetical order; the row flows again from the concept's own chips.
        tags:sortedTags(card.tags),
        // D153 (#447): no rating stars; the concept page itself is measured without them (`dropConceptRatings`). The
        // fixture's run_count (4) and last_used_at (its own "now", where the page's clock stands) give the concept's own
        // "4 runs" and "Used today". The two-line clamp is
        // applied below through `clampedCards`, measured on the concept with `CLAMP_CSS` (its samples wrap at 390px).
        footer:card.footer}));
    return {...reference,library:{...library,toolbar,
        // D35: the caption uses the singular for one.
        caption:{...library.caption,top:library.caption.top-rise,spans:library.caption.spans.map(span=>({...span,text:countCopy(span.text)}))},
        ...(({grid,cards:clamped})=>({grid:{...grid,top:grid.top-rise},cards:clamped}))(clampedCards({grid:library.grid,cards})),
        // D40: the production-only continuation follows the grid (here the end of the shelf); the concept has none.
        trailing:[...library.trailing,{tag:'div',className:'library-continuation',text:'You’re all caught up.'}]}};
}

/** D23's clamp, as workspace.css states it: the concept's own cards with it applied at runtime are the clamp oracle. */
const CLAMP_CSS='.prompt-card .prompt-text { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-clamp: 2; overflow: hidden }';
const CLAMP_LINES=2;
/**
 * D23: the positive prompt is clamped to two lines. Where a concept text wraps to more (its samples do at 390px), the
 * text box loses the extra lines, and its section, content and card shrink by as much, as does every box below it; the
 * grid's rows then close up by their tallest card's loss.
 */
export function clampedCards({grid,cards}) {
    const shrunk=cards.map(card=>{
        const text=card.section.text;
        if(text.lines<=CLAMP_LINES)return {card,loss:0};
        const loss=text.height-Math.round(CLAMP_LINES*parseFloat(text.lineHeight));
        return {loss,card:{...card,height:card.height-loss,
            content:{...card.content,height:card.content.height-loss},
            section:{...card.section,height:card.section.height-loss,text:{...text,height:text.height-loss,lines:CLAMP_LINES}},
            meta:{...card.meta,top:card.meta.top-loss},tags:{...card.tags,top:card.tags.top-loss},
            footer:{...card.footer,top:card.footer.top-loss}}};
    });
    // Rows are the cards sharing a top; a row is as tall as its tallest card, and each row moves up by the rows above.
    const tops=[...new Set(cards.map(card=>card.top))].sort((a,b)=>a-b);
    const rowLoss=top=>{
        const row=cards.flatMap((card,index)=>card.top===top?[index]:[]);
        return Math.max(...row.map(index=>cards[index].height))-Math.max(...row.map(index=>shrunk[index].card.height));
    };
    const above=top=>tops.filter(other=>other<top).reduce((sum,other)=>sum+rowLoss(other),0);
    return {grid:{...grid,height:grid.height-tops.reduce((sum,top)=>sum+rowLoss(top),0)},
        cards:shrunk.map(({card},index)=>({...card,top:card.top-above(cards[index].top)}))};
}

/** D43: a `.tags` row with its chips in alphabetical order, laid out again from the concept's chip boxes. */
export function sortedTags(tags) {
    return reflow(tags,[...tags.items].sort((a,b)=>nocase(a.text,b.text)));
}

const SECTIONS=['heading','library','noPageScroll'];
function compare(reference,actual,{sections=SECTIONS,library=null}={}) {
    const only=inventory=>Object.fromEntries(sections.map(key=>[key,key==='library'&&library
        ?Object.fromEntries(library.map(part=>[part,inventory.library[part]])):inventory[key]]));
    return diffInventory(stripExact(only(mapLibraryConcept(reference))),stripExact(only(actual)));
}

/** The comparison as a gate: throws, naming every differing path, unless production equals the mapped concept. */
function assertLibraryMatch(reference,actual) {
    const diff=compare(reference,actual);
    if(diff.length)throw new Error(`the Prompt library differs from the concept at ${diff.map(item=>item.path).join(', ')}`);
}

// Two frames, then every running transition finished.
const settle=page=>page.evaluate(async()=>{
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    await Promise.race([Promise.all(document.getAnimations().map(animation=>animation.finished.catch(()=>{}))),
        new Promise(resolve=>setTimeout(resolve,2000))]);
});
// Every card image decoded, so nothing moves while it is measured.
const decoded=page=>page.evaluate(async()=>{
    await document.fonts.ready;
    await Promise.all([...document.querySelectorAll('.library-grid img')].map(img=>{img.loading='eager';return img.decode().catch(()=>{});}));
});

const PLURAL_ONE='− 1 negative variants',SINGULAR_ONE='− 1 negative variant';
/**
 * The concept inventory, with `oracle.meta`: every card's `.card-meta` measured once the concept's hard-coded plural
 * reads "− 1 negative variant" (D23 "singular for one"), so the shorter chip and what follows it are the browser's own
 * layout. The concept page is restored afterwards.
 */
async function conceptInventory(page) {
    const reference=await inventoryOf(page,'#main');
    await page.evaluate(({plural,singular})=>{
        window.__conceptPlurals=[...document.querySelectorAll('.prompt-card .card-meta > .chip.red')].filter(chip=>chip.textContent===plural);
        for(const chip of window.__conceptPlurals)chip.textContent=singular;
    },{plural:PLURAL_ONE,singular:SINGULAR_ONE});
    const meta=(await inventoryOf(page,'#main')).library.cards.map(card=>card.meta);
    await page.evaluate(plural=>{for(const chip of window.__conceptPlurals)chip.textContent=plural;delete window.__conceptPlurals;},PLURAL_ONE);
    return {...reference,oracle:{meta}};
}

/** The concept page with each card's tag chips in alphabetical order (the D43 oracle); returns the undo. */
async function sortConceptTags(page) {
    await page.evaluate(()=>{
        const nocase=(a,b)=>{const x=a.textContent.toLowerCase(),y=b.textContent.toLowerCase();return x<y?-1:x>y?1:0;};
        window.__conceptTagOrder=[...document.querySelectorAll('.prompt-card .tags')].map(row=>{
            const chips=[...row.children];row.append(...[...chips].sort(nocase));return [row,chips];});
    });
    return ()=>page.evaluate(()=>{for(const [row,chips] of window.__conceptTagOrder)row.append(...chips);delete window.__conceptTagOrder;});
}

/**
 * D153 (#447): production draws no 1–5 rating. The concept's card footers read "★★★★★ · Used today"; the stars and
 * their separator are taken out of the concept page itself, so the footer facts are measured as production draws them.
 * Returns the concept's original footer texts.
 */
async function dropConceptRatings(page) {
    return page.evaluate(()=>[...document.querySelectorAll('.prompt-card .card-footer > span')].map(span=>{
        const original=span.textContent;span.textContent=original.replace(/^★+ · /,'');return original;}));
}
async function openConceptLibrary(page,theme,motion) {
    await openConcept(page,'library',theme,motion);
    await expect(page.locator(CARDS)).toHaveCount(CONCEPT_PROMPTS);
    const footers=await dropConceptRatings(page);
    expect(footers,'the concept rates every card (D153 removes it)').toEqual(Array(CONCEPT_PROMPTS).fill().map((_,index)=>expect.stringMatching(/^★+ · \S/)));
    await page.mouse.move(0,0);await decoded(page);await settle(page);
}
async function openProductionLibrary(page,theme,motion) {
    await openProduction(page,'library',theme,motion);
    await expect(page.locator(CARDS)).toHaveCount(CONCEPT_PROMPTS);
    await expect(page.locator('#workspace-main .section-caption')).toBeVisible();
    // The category list is read once per visit; it is part of the toolbar inventory.
    await expect(page.locator('#category-filter option')).toHaveCount(5);
    await page.mouse.move(0,0);await decoded(page);await settle(page);
}

const titlesOf=page=>page.locator(`${CARDS} .card-title > button`).allTextContents();
const captionOf=page=>page.locator('.section-caption > span').first().textContent();

/** A shelf filter: the card titles and the caption it leaves, then the filter undone. */
async function filteredOf(page,apply,undo) {
    const before=await captionOf(page);
    await apply();
    await expect.poll(()=>captionOf(page)).not.toBe(before);
    await expect.poll(()=>page.locator(CARDS).count()).toBeLessThan(CONCEPT_PROMPTS);
    // Production empties the shelf when the filter applies, writes the answer's caption at once and places its cards
    // in the next animation frame (one paint for a viewer), so the empty shelf already counts as "fewer": read the
    // titles only after that frame, as the Images and Inspector comparisons do.
    await settle(page);
    const result={caption:await captionOf(page),titles:await titlesOf(page)};
    await undo();
    await expect.poll(()=>captionOf(page)).toBe(before);
    return result;
}

/** Click `control` on the first card, record the history page's title, and come back to a settled shelf. */
async function historyFrom(page,control,back) {
    const title=(await page.locator(CARDS).first().locator('.card-title > button').textContent()).trim();
    await page.locator(CARDS).first().locator(control).click();
    const h1=page.locator('h1#page-title');
    await expect(h1).toHaveText(title);
    const result={title,h1:(await h1.textContent()).trim()};
    await back();
    await expect(page.locator(CARDS)).toHaveCount(CONCEPT_PROMPTS);
    return result;
}

/** The behaviors §7 and Task E3 list, with identical selectors in both documents. `sent` reads what a send carried. */
async function behaviorsOf(page,{back,sent}) {
    const first=page.locator(CARDS).first();
    const title=await historyFrom(page,'.card-title > button',back);
    const explore=await historyFrom(page,'.card-footer .row-actions > button:not(.send)',back);
    // The footer send opens the sender with the positive prompt only.
    const positive=(await first.locator('.prompt-section.positive .prompt-text').textContent()).trim();
    await page.locator(CARDS).first().locator('.card-footer .row-actions > button.send').click();
    const send={positive,roles:await sent(page)};
    // Copy writes the full positive and shows the toast.
    await page.locator(CARDS).first().locator('.prompt-section.positive .prompt-actions > button').first().click();
    await expect(page.locator('.toast')).not.toHaveText('');
    const copy={toast:(await page.locator('.toast').textContent()).trim(),copied:await page.evaluate(()=>window.copied.at(-1))};
    const search=await filteredOf(page,()=>page.locator('#search').fill('neon'),()=>page.locator('#search').fill(''));
    const category=await filteredOf(page,()=>page.locator('#category-filter').selectOption('Landscapes'),()=>page.locator('#category-filter').selectOption(''));
    return {title,explore,send,copy,search,category};
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
    // The page's clock stands at the fixture's own "now", so its last use reads "Used today" on either side of midnight.
    testInfo.manifest=await seedConceptAt(page,request);
    // Both documents copy through the clipboard; the stub records what they wrote.
    await page.addInitScript(()=>{window.copied=[];Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copied.push(text);}}});});
});

for(const [width,height] of SIZES)for(const theme of THEMES)test(`library geometry, tokens, controls and behaviors match the concept ${width}x${height} ${theme}`,async({page,context},testInfo)=>{
    test.setTimeout(240_000);
    await page.setViewportSize({width,height});
    const narrow=width<=700;
    const host=await openHost(context);
    try {
        const result={};
        for(const motion of MOTIONS) {
            const capture=motion==='no-preference';
            await openConceptLibrary(page,theme,motion);
            const reference=await conceptInventory(page);
            if(capture)await captureCanonicalState(page,testInfo,`concept-library-${width}-${theme}`);
            // The browser as the re-layout's oracle: the concept page itself with the D23 clamp applied to its cards.
            const restoreClamp=await mutate(page,{css:CLAMP_CSS});
            const clamped=(await inventoryOf(page,'#main')).library;
            await restoreClamp();
            // ...and with every card's tag chips put in alphabetical order (D43).
            const restoreTags=await sortConceptTags(page);
            const sorted=(await inventoryOf(page,'#main')).library.cards.map(card=>card.tags);
            await restoreTags();
            const referenceBehaviors=capture?await behaviorsOf(page,{back:()=>page.goBack(),sent:conceptSent}):null;

            await openProductionLibrary(page,theme,motion);
            const actual=await inventoryOf(page,'#workspace-main');
            if(capture)await captureCanonicalState(page,testInfo,`production-library-${width}-${theme}`);
            const actualBehaviors=capture?await behaviorsOf(page,{back:()=>page.getByRole('button',{name:'← Prompt library',exact:true}).click(),sent:productionSent(host)}):null;
            result[motion]={reference,sorted,clamped,actual,diff:compare(reference,actual),referenceBehaviors,actualBehaviors};
        }
        const path=testInfo.outputPath('library-geometry.json');await writeFile(path,JSON.stringify(result,null,2));
        await testInfo.attach('library-geometry',{path,contentType:'application/json'});

        for(const [motion,{reference,sorted,clamped,actual,diff,referenceBehaviors:concept,actualBehaviors:production}] of Object.entries(result)) {
            const {grid,cards}=reference.library,card=cards[0];
            // The concept itself is what this comparison assumes (style.css s648–708, 1424–1428, 1475–1483).
            expect.soft(grid,`${motion} concept grid`).toMatchObject({columns:narrow?1:2,columnGap:'18px',rowGap:'18px',cardCount:CONCEPT_PROMPTS});
            expect.soft(card,`${motion} concept card`).toMatchObject({borderTopLeftRadius:'8px',borderTopWidth:'1px',overflow:'hidden'});
            const art=narrow?150:170;
            expect.soft(card.art,`${motion} concept art`).toMatchObject({height:art,display:'grid',columnGap:'3px'});
            const [major,minor]=card.art.gridTemplateColumns.split(' ').map(parseFloat);
            expect.soft(Math.abs(major-2*minor),`${motion} concept art 2fr/1fr`).toBeLessThan(1);
            expect.soft(card.art.items.map(item=>item.height),`${motion} concept art images`).toEqual([art,art]);
            const pad=narrow?'15px':'18px';
            expect.soft(card.content,`${motion} concept content padding`).toMatchObject({paddingTop:pad,paddingLeft:pad,paddingRight:pad,paddingBottom:pad});
            expect.soft(card.title.items[0],`${motion} concept title`).toMatchObject({tag:'button',fontSize:'16px',fontWeight:'600'});
            expect.soft(card.meta,`${motion} concept meta`).toMatchObject({fontSize:'10px',columnGap:'9px'});
            expect.soft(card.meta.items.map(item=>item.text),`${motion} concept meta text`).toEqual(['− 3 negative variants','4 runs','4 images']);
            expect.soft(card.footer,`${motion} concept footer`).toMatchObject({paddingTop:'11px',paddingRight:'18px',paddingBottom:'11px',paddingLeft:'18px',borderTopWidth:'1px',borderTopStyle:'solid'});
            expect.soft(card.footer.facts.text,`${motion} concept footer facts (D153: without the stars)`).toBe('Used today');
            expect.soft(diffInventory(stripExact(cards.map(item=>sortedTags(item.tags))),stripExact(sorted)),`${motion} tags in alphabetical order`).toEqual([]);
            expect.soft(diffInventory(stripExact(clampedCards(reference.library)),stripExact({grid:clamped.grid,cards:clamped.cards})),`${motion} clamped cards`).toEqual([]);
            expect.soft(diff,`${motion} differences after the recorded deviations`).toEqual([]);
            expect.soft(actual.noPageScroll,`${motion} no page-level horizontal scroll`).toBe(true);
            if(!concept)continue;
            // Title and Explore history open the prompt's history; the footer send carries the positive only; copy writes
            // the full positive with the concept's toast; search and category narrow the shelf alike.
            expect.soft(concept.title,`${motion} concept title opens history`).toEqual({title:card.title.items[0].text,h1:card.title.items[0].text});
            expect.soft(concept.send.roles,`${motion} concept send`).toEqual({positive:concept.send.positive});
            expect.soft(concept.copy,`${motion} concept copy`).toEqual({toast:'Prompt copied.',copied:concept.send.positive});
            expect.soft(concept.search.titles,`${motion} concept search`).toEqual(['After the rain']);
            // D35: the shelf caption uses the singular for one.
            const counted=filter=>({...filter,caption:countCopy(filter.caption)});
            expect.soft(production,`${motion} behaviors`).toEqual({...concept,search:counted(concept.search),category:counted(concept.category)});
        }
    } finally {await host.evaluate(()=>window.conceptHost.dispose());await host.close();}
});

/**
 * D148 (#437): the selection checkbox overlay of `card` and the `.bulkbar` row inside `main`, with one item checked,
 * measured alike in the concept's Images page and production's Library: the overlay's inset in its card, the checkbox,
 * and the bar's box, tokens and the actions both bars have (Add tags, Set category, Clear; #442).
 */
const measureSelection=(page,{card,main})=>page.evaluate(({card,main})=>{
    const css=(node,...keys)=>{const style=getComputedStyle(node);return Object.fromEntries(keys.map(key=>[key,style[key]]));};
    const rect=node=>node.getBoundingClientRect(),px=value=>Math.round(value*100)/100;
    const font=node=>css(node,'fontFamily','fontSize','fontWeight','lineHeight','color');
    const edges=node=>css(node,'paddingTop','paddingRight','paddingBottom','paddingLeft','marginBottom','borderTopWidth','borderTopStyle',
        'borderTopColor','borderTopLeftRadius','backgroundColor');
    const host=document.querySelector(card),overlay=host.querySelector('.image-overlays'),label=overlay.querySelector(':scope > label');
    const input=label.querySelector('input'),root=document.querySelector(main),bar=root.querySelector('.bulkbar');
    const shared=['Add tags','Set category','Clear'];
    return {
        overlay:{left:px(rect(overlay).left-rect(host).left),top:px(rect(overlay).top-rect(host).top),right:px(rect(host).right-rect(overlay).right),
            ...css(overlay,'position','display','justifyContent','pointerEvents')},
        label:css(label,'pointerEvents'),checkbox:{width:px(rect(input).width),height:px(rect(input).height),checked:input.checked,...css(input,'accentColor')},
        bar:{left:px(rect(bar).left-rect(root).left),...css(bar,'display','alignItems','columnGap','rowGap','flexWrap'),...edges(bar),...font(bar),
            count:{text:bar.querySelector(':scope > strong').textContent,...font(bar.querySelector(':scope > strong'))},
            buttons:[...bar.querySelectorAll(':scope > button')].filter(button=>shared.includes(button.textContent.trim()))
                .map(button=>({text:button.textContent.trim(),height:px(rect(button).height),...font(button),...edges(button)}))},
    };
},{card,main});
async function conceptSelection(page,theme) {
    await openConcept(page,'images',theme);
    await page.locator('#image-grid .media-card').first().locator('input[data-select]').check();
    await expect(page.locator('#bulkbar .bulkbar')).toBeVisible();await page.mouse.move(0,0);await settle(page);
    return measureSelection(page,{card:'#image-grid .media-card',main:'#main'});
}
async function productionSelection(page,theme) {
    await openProductionLibrary(page,theme,'no-preference');
    await page.locator(CARDS).first().locator('.image-overlays input').check();
    await expect(page.locator('#library-bulkbar .bulkbar')).toBeVisible();await page.mouse.move(0,0);await settle(page);
    return measureSelection(page,{card:CARDS,main:'#workspace-main'});
}
for(const [width,height] of SIZES)for(const theme of THEMES)test(`the Library selection checkbox and bar match the concept's Images ones ${width}x${height} ${theme}`,async({page},testInfo)=>{
    await page.setViewportSize({width,height});
    const reference=await conceptSelection(page,theme);
    await captureCanonicalState(page,testInfo,`concept-images-selection-${width}-${theme}`);
    const actual=await productionSelection(page,theme);
    await captureCanonicalState(page,testInfo,`production-library-selection-${width}-${theme}`);
    await testInfo.attach('library-selection',{body:JSON.stringify({reference,actual},null,2),contentType:'application/json'});
    expect(reference.bar.buttons.map(button=>button.text),'the concept bar has every Library action').toEqual(['Add tags','Set category','Clear']);
    expect(actual).toEqual(reference);
    // The comparison notices a moved checkbox (spec §7 mutation check).
    await mutate(page,{css:'.prompt-card .image-overlays { inset: 20px 9px auto }'});
    expect((await measureSelection(page,{card:CARDS,main:'#workspace-main'})).overlay.top).not.toBe(reference.overlay.top);
});

// The grid and cards at the concept's three-column breakpoint (style.css 1274–1276).
for(const theme of THEMES)test(`library grid and cards match the concept 1800x1100 ${theme}`,async({page},testInfo)=>{
    await page.setViewportSize({width:1800,height:1100});
    await openConceptLibrary(page,theme,'no-preference');
    const reference=await conceptInventory(page);
    await captureCanonicalState(page,testInfo,`concept-library-1800-${theme}`);
    await openProductionLibrary(page,theme,'no-preference');
    const actual=await inventoryOf(page,'#workspace-main');
    await captureCanonicalState(page,testInfo,`production-library-1800-${theme}`);
    expect(reference.library.grid.columns,'concept columns').toBe(3);
    expect(compare(reference,actual,{sections:['library'],library:['grid','cards','trailing']})).toEqual([]);
});

// PR #284's CI run: the fixture recorded the last use at 23:59:5x UTC and the shelf rendered at 00:00:05, so production
// read "Used yesterday" against the concept's "Used today". Here the browser's wall clock starts at 23:59:58 UTC, the
// fixture records its use at that instant, and midnight passes before either document opens.
const BEFORE_MIDNIGHT=Date.UTC(2026,9,22,23,59,58);
const PAST_MIDNIGHT_MS=5_000;
test('a run that crosses midnight still reads the concept fixture\'s day',async({page,request})=>{
    await page.setViewportSize({width:1800,height:1100});
    await page.clock.install({time:BEFORE_MIDNIGHT});
    const manifest=await seedConceptAt(page,request,{now:BEFORE_MIDNIGHT/1000});
    expect(manifest.now*1000,'the fixture\'s now').toBe(BEFORE_MIDNIGHT);
    const used=(await (await request.get('/prompt_manager/library')).json()).items.map(item=>Date.parse(item.last_used_at));
    expect(used,'every last use at the fixture\'s now').toEqual(Array(CONCEPT_PROMPTS).fill(BEFORE_MIDNIGHT));
    await page.clock.fastForward(PAST_MIDNIGHT_MS);
    await openConceptLibrary(page,'dark','no-preference');
    const reference=await conceptInventory(page);
    await openProductionLibrary(page,'dark','no-preference');
    const actual=await inventoryOf(page,'#workspace-main');
    expect(actual.library.cards.map(card=>card.footer.facts.text)).toEqual(Array(CONCEPT_PROMPTS).fill('Used today'));
    expect(compare(reference,actual,{sections:['library'],library:['grid','cards','trailing']})).toEqual([]);
});

// The comparison is only worth something if it notices a change (spec §7 mutation check): one CSS mutation (the footer
// padding) and one DOM mutation (the negative-variants chip removed), each applied after an exact match.
test('library comparison detects mutations',async({page})=>{
    await page.setViewportSize({width:1600,height:1100});
    await openConceptLibrary(page,'dark','no-preference');
    const reference=await conceptInventory(page);
    await openProductionLibrary(page,'dark','no-preference');
    const measure=()=>inventoryOf(page,'#workspace-main');
    assertLibraryMatch(reference,await measure());

    const restoreCss=await mutate(page,{css:'.card-footer { padding: 2px }'});
    const cssMutated=await measure();
    expect(()=>assertLibraryMatch(reference,cssMutated)).toThrow(/library\.cards\.0\.footer\.paddingTop/);
    await restoreCss();await settle(page);
    assertLibraryMatch(reference,await measure());

    await mutate(page,{remove:['.card-meta .chip.red']});
    const domMutated=await measure();
    expect(()=>assertLibraryMatch(reference,domMutated)).toThrow(/library\.cards\.0\.meta\.items\.length/);
});
