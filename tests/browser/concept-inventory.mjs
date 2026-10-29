// Shared concept-vs-production inventory for the mock-true rebuild (#249, spec §7). One in-page measurer runs the
// same concept selectors in the immutable concept (/__test__/canonical/workspace) and in production, which now renders
// the concept's classes. The concept is never rewritten: each page's map<Page>Concept(reference) applies only the
// recorded spec-§8 deviations, each commented with its D-number, and diffInventory must then report nothing.
import {expect} from './fixtures.mjs';
export {diffInventory} from './library-editor-canonical-comparison.mjs';

export const CONCEPT_URL='/__test__/canonical/workspace';
export const PRODUCTION_URL='/prompt_manager/workspace';
export const SIZES=Object.freeze([[1600,1100],[390,844]]);
export const THEMES=Object.freeze(['dark','light']);
export const MOTIONS=Object.freeze(['no-preference','reduce']);

/** D35: production uses the singular for exactly one; the concept's plurals are hard-coded (app.js 447, 489, 517-521). */
export const countCopy=text=>text.replace(/(^|\D)1 positive prompts\b/g,'$11 positive prompt')
    .replace(/(^|\D)1 more images\b/g,'$11 more image').replace(/(^|\D)1 images\b/g,'$11 image');

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
export function withoutItems(row,isRemoved) {
    return reflow(row,row.items.filter(item=>!isRemoved(item)));
}

/** Seed the concept-shaped real fixture (tests/browser/workspace_concept_fixture.py) and return its manifest. */
export async function seedConcept(request,data={}) {
    const response=await request.post('/__test__/concept/reset',{data});
    await expect(response).toBeOK();
    return response.json();
}

/**
 * Seed the concept fixture and fix the browser's clock at the fixture's own instant (`manifest.now`), so the page and
 * the fixture read one "now": a prompt used now reads "Used today" and the caption names the fixture's day even when
 * the run crosses midnight between seeding and rendering. Timers keep running; only `Date` stands still.
 */
export async function seedConceptAt(page,request,data={}) {
    const manifest=await seedConcept(request,data);
    await page.clock.setFixedTime(new Date(manifest.now*1000));
    return manifest;
}

/**
 * Save `tags` as the current image tag set of the listed file at `path` (root 0) through the real observe and tag
 * routes (#249 PR C), releasing the capture afterwards; returns the receipt's saved names.
 */
export async function saveImageTags(request,path,tags) {
    const listing=await (await request.get('/prompt_manager/images/output?mode=workspace&limit=40')).json();
    const target={root_index:0,root_scope:listing.images.find(item=>item.root_index===0).root_scope,path};
    const observed=await (await request.post('/prompt_manager/image-annotations/observe',{data:{target}})).json();
    expect(observed.success).toBe(true);
    try {
        const receipt=await (await request.put('/prompt_manager/image-annotations/tags',{data:{target,capture_token:observed.capture.token,
            sha256:observed.capture.sha256,expected_epoch:observed.library_epoch,expected_tag_revision:observed.tag_revision,tags}})).json();
        expect(receipt.success).toBe(true);
        return receipt.tags;
    } finally {await request.delete(`/prompt_manager/image-annotations/capture/${observed.capture.token}`);}
}

async function selectTheme(page,theme) {
    await page.getByRole('combobox',{name:'Theme',exact:true}).selectOption(theme);
}

/** The concept at `#<route>` with its own Theme select. */
export async function openConcept(page,route,theme,motion='no-preference') {
    await page.emulateMedia({reducedMotion:motion});
    await page.goto('about:blank');await page.goto(`${CONCEPT_URL}#${route}`);
    await selectTheme(page,theme);
    await expect(page.locator('#main #page-title')).toBeVisible();
}

/**
 * D157 (#451): production opens Images in Masonry when the browser has no Default layout; the concept starts at Grid.
 * A comparison (and a test of the Grid's own geometry) starts production from the concept's state, a stored Default
 * layout of Grid, once per tab and only while this browser stored nothing (a test's own settings win).
 */
export async function startInGrid(page) {
    if (page.conceptLayoutSeeded) return;
    page.conceptLayoutSeeded = true;
    await page.addInitScript(() => {
        try {
            if (!location.pathname.startsWith('/prompt_manager/') || sessionStorage.getItem('pm-d157-seeded')) return;
            sessionStorage.setItem('pm-d157-seeded', '1');
            if (localStorage.getItem('gallerySettings') === null) localStorage.setItem('gallerySettings', JSON.stringify({ workspaceLayout: 'grid' }));
        } catch { /* A page without storage keeps the production default. */ }
    });
}

/** Production at `#/<route>` with the D4 Theme select, once the sidebar's real counts (D27) have settled. */
export async function openProduction(page,route,theme,motion='no-preference') {
    await page.emulateMedia({reducedMotion:motion});
    await startInGrid(page);
    await page.goto('about:blank');await page.goto(`${PRODUCTION_URL}#/${route}`);
    await selectTheme(page,theme);
    await expect(page.locator('#workspace-main #page-title')).toBeVisible();
    await expect(page.locator('#primary-nav .nav-count')).toHaveCount(3);
    // SAVED VIEWS' Favorites count (D51), the concept's starred images in the concept fixture.
    await expect(page.locator('#saved-nav a[data-route="favorites"] .nav-count')).toHaveCount(1);
    // SAVED VIEWS' Needs review count (ruling R5): the exact pending count.
    await expect(page.locator('#saved-nav a[data-route="review"] .nav-count')).toHaveCount(1);
}

/** Computed outline of the focused element, after keyboard focus so `:focus-visible` applies. */
export async function focusRingOf(locator) {
    return locator.evaluate(node=>{const style=getComputedStyle(node);return {focused:document.activeElement===node,
        visible:node.matches(':focus-visible'),outlineStyle:style.outlineStyle,outlineWidth:style.outlineWidth,
        outlineColor:style.outlineColor,outlineOffset:style.outlineOffset};});
}

/** Computed colors of an element while the pointer rests on it; the pointer is parked again afterwards. */
export async function hoverStateOf(page,locator) {
    await locator.hover();
    const state=await locator.evaluate(node=>{const style=getComputedStyle(node);return {hovered:node.matches(':hover'),
        color:style.color,backgroundColor:style.backgroundColor,borderTopColor:style.borderTopColor,
        textDecorationLine:style.textDecorationLine,boxShadow:style.boxShadow};});
    await page.mouse.move(0,0);
    return state;
}

/**
 * Mutate the open page so a comparison can be shown to notice a change (the A7 mutation check): `css` is appended as
 * the last stylesheet, so it wins over equally specific concept rules, and every element matching a `remove` selector
 * is detached; each `clone` ({selector, text}) inserts a copy of the first match after it, holding `text` and no
 * `data-action` (a control production adds beside a concept one, D85). Returns an async undo that restores the
 * stylesheet and the removed elements in place and drops the copies.
 */
export async function mutate(page,{css=null,remove=[],clone=[]}={}) {
    const token=await page.evaluate(({css,remove,clone})=>{
        const token=`mutation-${Math.random().toString(36).slice(2)}`;
        const undo=[];
        if(css) {
            const style=document.createElement('style');style.dataset.mutation=token;style.textContent=css;
            document.head.append(style);undo.push(()=>style.remove());
        }
        for(const selector of remove) {
            const nodes=[...document.querySelectorAll(selector)];
            if(!nodes.length)throw new Error(`mutate: nothing matches ${selector}`);
            for(const node of nodes) {
                const parent=node.parentNode,next=node.nextSibling;
                node.remove();undo.push(()=>parent.insertBefore(node,next));
            }
        }
        for(const {selector,text} of clone) {
            const node=document.querySelector(selector);
            if(!node)throw new Error(`mutate: nothing matches ${selector}`);
            const copy=node.cloneNode(true);copy.textContent=text;copy.removeAttribute('data-action');
            node.after(copy);undo.push(()=>copy.remove());
        }
        (window.__conceptMutations??=new Map()).set(token,undo);
        return token;
    },{css,remove,clone});
    return ()=>page.evaluate(token=>{
        const undo=window.__conceptMutations?.get(token);
        if(!undo)throw new Error('mutate: this mutation was already undone or the page reloaded');
        undo.reverse().forEach(step=>step());window.__conceptMutations.delete(token);
    },token);
}

/**
 * #332 D146: the category chip names its origin after the category, in a muted `span.chip-source` that never splits
 * (workspace.css `.chip .chip-source`). The concept's chip shows the bare name (app.js a402), so the recorded rule and
 * the span are added to the concept page at runtime and the browser lays the chip out: the first `.tags` row's
 * non-badge chip under `root`. `category` also replaces the chip's text (an own category the concept never shows).
 * Returns the undo.
 */
export const D146_CHIP_SOURCE='.chip .chip-source { color: var(--ca-muted); white-space: nowrap; }';
export async function withChipSource(page,root,origin,category=null) {
    const restoreRule=await mutate(page,{css:D146_CHIP_SOURCE});
    const token=await page.evaluate(({root,origin,category})=>{
        const chip=document.querySelector(root)?.querySelector('.tags')?.querySelector(':scope > .chip:not(.blue)');
        if(!chip)throw new Error(`withChipSource: no category chip under ${root}`);
        const text=chip.textContent,source=document.createElement('span');
        source.className='chip-source';source.textContent=` · ${origin}`;
        if(category!==null)chip.textContent=category;
        chip.append(source);
        const token=`chip-source-${Math.random().toString(36).slice(2)}`;
        (window.__chipSources??=new Map()).set(token,()=>{chip.textContent=text;});
        return token;
    },{root,origin,category});
    return async()=>{
        await page.evaluate(token=>{window.__chipSources.get(token)();window.__chipSources.delete(token);},token);
        await restoreRule();
    };
}

/**
 * #449 D160: the inspector's tag sections in the concept's prompt-section frame (workspace.css `.tag-section`). The concept
 * has one bare `.tags` row after its prompt blocks (app.js a402), so the recorded rule is added and that row wrapped at
 * runtime, the browser laying out what follows: `sections` lists `[className, label, name]` per section, the first
 * holding the concept's row and each further one a copy of it. Returns the undo.
 */
export const D160_TAG_SECTION=`.tag-section { border-top: 1px solid var(--line); padding-top: 15px; margin-top: 15px; }
.tag-section .role-label { color: var(--ca-muted); }
.tag-section > .tags { margin-top: 0; }`;
export const PROMPT_TAGS=[['linked-tags','Prompt tags','Prompt tags']];
export async function withTagSections(page,root,sections=PROMPT_TAGS) {
    const restoreRule=await mutate(page,{css:D160_TAG_SECTION});
    const token=await page.evaluate(({root,sections})=>{
        const container=document.querySelector(root);
        const inner=container?.querySelector(':scope > .detail-inner')??container?.querySelector(':scope > div');
        const row=inner&&[...inner.querySelectorAll(':scope > .tags')].find(node=>node.previousElementSibling?.matches('section.prompt-section'));
        if(!row)throw new Error(`withTagSections: no tag row after the prompt blocks under ${root}`);
        const anchor=row.nextSibling,parent=row.parentNode,added=[];
        sections.forEach(([className,label,name],index)=>{
            const section=document.createElement('section');section.className=`tag-section ${className}`;section.setAttribute('aria-label',name);
            const heading=document.createElement('div');heading.className='prompt-heading';
            const text=document.createElement('span');text.className='role-label';text.textContent=`${label} · ${row.children.length}`;
            heading.append(text);section.append(heading,index?row.cloneNode(true):row);
            parent.insertBefore(section,anchor);added.push(section);
        });
        const token=`tag-sections-${Math.random().toString(36).slice(2)}`;
        (window.__tagSections??=new Map()).set(token,()=>{parent.insertBefore(row,added[0]);added.forEach(section=>section.remove());});
        return token;
    },{root,sections});
    return async()=>{
        await page.evaluate(token=>{window.__tagSections.get(token)();window.__tagSections.delete(token);},token);
        await restoreRule();
    };
}

/**
 * Inventory sections: {chrome, heading, toolbar, filterRow, bulkbar, caption, grid, cards, continuation, panel, library,
 * history, noPageScroll}. A section is null when its element is absent. The Images sections (toolbar … panel, #249 C7)
 * measure the concept's Images markup; `cards` holds the first six; `bulkbar` is the selection bar's place and, while
 * anything is checked, its `.bulkbar` row. `library` (#249 E3) holds the Prompt library's toolbar,
 * caption, grid, first six cards and whatever follows the grid. `history` (#249 F5) holds the Prompt history's summary,
 * negative variants panel, comparison (while Compare negatives is pressed) and the shared gallery's heading.
 */
export function inventoryOf(page,scope='main') {
    return page.evaluate(measureInventory,scope);
}

/** The inventory without its `exact` (unrounded) helper values, which only a re-layout reads; compare after this. */
export function stripExact(value) {
    if(Array.isArray(value))return value.map(stripExact);
    if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).filter(([key])=>key!=='exact').map(([key,item])=>[key,stripExact(item)]));
    return value;
}

/** Runs in the page; self-contained. `scope` selects the page's main element. */
export function measureInventory(scope) {
    const px=value=>Math.round(value);
    const css=(node,...keys)=>{const style=getComputedStyle(node);return Object.fromEntries(keys.map(key=>[key,style[key]]));};
    const shown=node=>Boolean(node)&&node.getClientRects().length>0&&getComputedStyle(node).visibility!=='hidden';
    const rect=node=>node.getBoundingClientRect();
    const font=node=>css(node,'fontFamily','fontSize','fontWeight','lineHeight','letterSpacing','color','textTransform');
    const edges=node=>css(node,'paddingTop','paddingRight','paddingBottom','paddingLeft','marginTop','marginRight','marginBottom','marginLeft',
        'borderTopWidth','borderTopStyle','borderTopColor','borderRightWidth','borderRightStyle','borderRightColor',
        'borderBottomWidth','borderBottomStyle','borderBottomColor','borderTopLeftRadius','backgroundColor');
    const layout=node=>css(node,'display','flexDirection','flexWrap','justifyContent','alignItems','columnGap','rowGap','position');
    // Text of a node without its descendants matching `skip` (counts, nested controls), whitespace collapsed.
    const textOf=(node,skip)=>{const copy=node.cloneNode(true);if(skip)copy.querySelectorAll(skip).forEach(item=>item.remove());
        return copy.textContent.replace(/\s+/g,' ').trim();};
    const relative=(node,origin)=>{const a=rect(node),b=rect(origin);return {left:px(a.left-b.left),top:px(a.top-b.top),width:px(a.width),height:px(a.height)};};
    const main=document.querySelector(scope);
    const sidebar=document.querySelector('aside.sidebar');
    const workspace=document.querySelector('.workspace');

    function iconOf(node) {
        const svg=node.querySelector(':scope > svg.icon');
        if(!svg)return null;
        const box=rect(svg);
        return {width:px(box.width),height:px(box.height),viewBox:svg.getAttribute('viewBox'),hidden:svg.getAttribute('aria-hidden'),
            d:svg.querySelector('path')?.getAttribute('d')??null,...css(svg,'strokeWidth','color','verticalAlign','flexShrink')};
    }
    function navRow(link,index,links) {
        const box=rect(link),count=link.querySelector('.nav-count'),icon=link.querySelector(':scope > svg.icon');
        const style=getComputedStyle(link);
        const content=(icon?rect(icon).width+parseFloat(style.columnGap||0):0)+(()=>{
            const range=document.createRange();const text=[...link.childNodes].find(item=>item.nodeType===Node.TEXT_NODE&&item.textContent.trim());
            if(!text)return 0;range.selectNodeContents(text);return range.getBoundingClientRect().width;})();
        const next=links[index+1];let gapAfter=null;
        if(next){const other=rect(next);gapAfter=px(other.top)===px(box.top)?{axis:'x',size:px(other.left-box.right)}:{axis:'y',size:px(other.top-box.bottom)};}
        return {text:textOf(link,'.nav-count'),current:link.getAttribute('aria-current'),
            ...layout(link),...edges(link),...font(link),minHeight:style.minHeight,height:px(box.height),
            fullWidth:px(box.width)===px(rect(link.parentElement).width),
            hugs:Math.abs(box.width-content-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight))<=1.5,
            gapAfter,icon:iconOf(link),
            count:count?{text:count.textContent,shown:shown(count),...font(count),...css(count,'marginInlineStart')}:null};
    }
    // Every subsection is null when its element is missing, so a removed node is reported by its path
    // (for example `chrome.breadcrumbs`) instead of failing the measurement.
    const when=(node,measure)=>node?measure(node):null;
    // The accessible text of a node: its text without aria-hidden descendants.
    const accessibleText=node=>textOf(node,'[aria-hidden="true"]');
    function itemOf(node,actions) {
        const base={tag:node.tagName.toLowerCase(),className:node.className,shown:shown(node)};
        if(!base.shown)return base;
        const box=relative(node,actions);
        return {...base,text:textOf(node,'select, .count'),...font(node),...edges(node),...layout(node),
            left:box.left,top:box.top,width:box.width,height:box.height,
            count:node.querySelector('.count')?.textContent??null,
            select:when(node.querySelector('select'),select=>({options:[...select.options].map(option=>[option.value,option.textContent]),
                label:select.getAttribute('aria-label'),labelled:select.labels?.[0]===node,...font(select),...edges(select),
                minHeight:getComputedStyle(select).minHeight,width:px(rect(select).width),height:px(rect(select).height)})),
            hiddenSuffix:[...node.querySelectorAll('[aria-hidden="true"]')].map(child=>child.textContent).join('')||null,
            whiteSpace:getComputedStyle(node).whiteSpace};
    }
    function sidebarOf() {
        return when(sidebar,()=>({...layout(sidebar),...edges(sidebar),
            ...css(sidebar,'top','overflowY','borderInlineEndWidth'),width:px(rect(sidebar).width),
            fillsViewport:Math.abs(rect(sidebar).height-innerHeight)<=1}));
    }
    function bottomOf(bottom) {
        // In the column sidebar the bottom group is pinned to the end by `margin-top: auto`, whose used value is only
        // the free space above it, so the pin (its inset from the sidebar's padding edge) is recorded instead.
        const sidebarStyle=getComputedStyle(sidebar);
        const pinned=sidebarStyle.display==='flex';
        const {marginTop,...rest}=edges(bottom);
        return {...rest,...css(bottom,'display'),marginTop:pinned?'auto':marginTop,
            bottomInset:pinned?px(rect(sidebar).bottom-rect(bottom).bottom-parseFloat(sidebarStyle.paddingBottom)):null};
    }
    function topActionsOf(actions,topbar) {
        const style=getComputedStyle(topbar);
        return {...layout(actions),...edges(actions),width:px(rect(actions).width),height:px(rect(actions).height),
            fullWidth:Math.abs(rect(actions).width-(rect(topbar).width-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight)))<=1,
            rightInset:px(rect(topbar).right-rect(actions).right),
            items:[...actions.children].map(node=>itemOf(node,actions))};
    }
    function chromeOf() {
        if(!sidebar&&!workspace)return null;
        const shell=document.querySelector('.app-shell');
        const brand=sidebar?.querySelector('.brand');
        const topbar=workspace?.querySelector('.topbar');
        const footer=workspace?.querySelector('.app-footer');
        return {
            title:document.title,
            skip:when(document.querySelector('a.skip'),skip=>({text:skip.textContent.trim(),...css(skip,'position','top','left')})),
            body:{...font(document.body),backgroundColor:getComputedStyle(document.body).backgroundColor,
                tokens:Object.fromEntries(['--ca-text','--ca-muted','--ca-focus','--ca-accent','--ca-field','--ca-surface','--ca-line',
                    '--line','--page','--sidebar','--soft-blue','--green'].map(name=>[name,getComputedStyle(document.body).getPropertyValue(name).trim()]))},
            shell:when(shell,()=>{const columns=getComputedStyle(shell).gridTemplateColumns.split(' ');
                return {display:getComputedStyle(shell).display,columns:columns.length,sidebarColumn:columns.length>1?columns[0]:null,
                    minHeight:getComputedStyle(shell).minHeight};}),
            sidebar:sidebarOf(),
            brand:when(brand,()=>({text:textOf(brand),name:accessibleText(brand),href:brand.getAttribute('href')?.replace(/^#\/?/,''),
                ...relative(brand,sidebar),...font(brand),...layout(brand),...edges(brand),
                mark:when(brand.querySelector('.brand-mark'),mark=>({text:textOf(mark),hidden:mark.getAttribute('aria-hidden'),...font(mark),
                    width:px(rect(mark).width),height:px(rect(mark).height)})),
                small:when(brand.querySelector('small'),small=>({text:small.textContent,...font(small),...css(small,'display','marginTop')}))})),
            labels:sidebar?[...sidebar.querySelectorAll('.sidebar-label')].map(node=>({text:textOf(node),shown:shown(node),...font(node),...layout(node),...edges(node)})):null,
            navs:sidebar?[...sidebar.querySelectorAll('nav')].map(nav=>{const links=[...nav.querySelectorAll(':scope > a')];return {
                id:nav.id,label:nav.getAttribute('aria-label'),shown:shown(nav),...layout(nav),...edges(nav),
                width:px(rect(nav).width),rows:links.map(navRow)};}):null,
            bottom:when(sidebar?.querySelector('.sidebar-bottom'),bottomOf),
            connection:when(sidebar?.querySelector('.connection'),connection=>({shown:shown(connection),text:textOf(connection,'small'),
                small:connection.querySelector('small')?.textContent??null,...layout(connection),...edges(connection),...font(connection),
                smallFont:when(connection.querySelector('small'),font),
                dot:when(connection.querySelector('i'),dot=>css(dot,'width','height','borderTopLeftRadius','backgroundColor','marginTop'))})),
            topbar:when(topbar,()=>({...layout(topbar),...edges(topbar),...css(topbar,'height','minHeight'),...relative(topbar,workspace),
                fullWidth:px(rect(topbar).width)===px(rect(workspace).width)})),
            breadcrumbs:when(topbar?.querySelector('.breadcrumbs'),crumbs=>({shown:shown(crumbs),text:textOf(crumbs),...layout(crumbs),...font(crumbs),
                parts:[...crumbs.querySelectorAll('span')].map(span=>({text:span.textContent,...font(span)})),
                ...(shown(crumbs)?{left:relative(crumbs,topbar).left,middle:px(rect(crumbs).top+rect(crumbs).height/2-rect(topbar).top)}:{})})),
            topActions:when(topbar?.querySelector('.top-actions'),actions=>topActionsOf(actions,topbar)),
            main:when(main,()=>({tag:main.tagName.toLowerCase(),tabIndex:main.tabIndex,
                ...css(main,'paddingTop','paddingRight','paddingBottom','paddingLeft','maxWidth','marginLeft','marginRight')})),
            footer:when(footer,()=>({...layout(footer),...edges(footer),...font(footer),...css(footer,'height'),width:px(rect(footer).width),
                fullWidth:px(rect(footer).width)===px(rect(workspace).width),
                spans:[...footer.querySelectorAll(':scope > span')].map(span=>({text:span.textContent.replace(/\s+/g,' ').trim(),...font(span),height:px(rect(span).height)})),
                buttons:[...footer.querySelectorAll('button')].map(button=>({text:button.textContent.trim(),...font(button),...edges(button),
                    minHeight:getComputedStyle(button).minHeight,height:px(rect(button).height)}))})),
        };
    }
    function headingOf() {
        const heading=main?.querySelector(':scope > .heading');
        if(!heading)return null;
        const copy=heading.firstElementChild,h1=copy?.querySelector('h1'),description=copy?.querySelector('p'),actions=heading.querySelector('.heading-actions');
        return {...layout(heading),...edges(heading),height:px(rect(heading).height),
            copy:when(copy,()=>({tag:copy.tagName.toLowerCase(),...layout(copy)})),
            h1:when(h1,()=>({id:h1.id,tabIndex:h1.tabIndex,text:h1.textContent,...font(h1),...edges(h1)})),
            description:when(description,()=>({className:description.className,text:description.textContent,...font(description),...edges(description)})),
            actions:when(actions,()=>({...layout(actions),...edges(actions),
                fullWidth:px(rect(actions).width)===px(rect(heading).width),stacked:copy?rect(actions).top>=rect(copy).bottom-0.5:null,
                buttons:[...actions.querySelectorAll('button')].map(node=>({text:node.textContent.trim(),primary:node.classList.contains('ca-primary'),
                    ...font(node),...edges(node),minHeight:getComputedStyle(node).minHeight,height:px(rect(node).height)}))}))};
    }
    // ---- Images (#249 C7). Every box is relative to its section, every section to `main` (vertical rhythm). ----
    const classes=node=>[...node.classList].sort().join(' ');
    const sized=node=>({width:px(rect(node).width),height:px(rect(node).height)});
    /** One control or label in a toolbar-like row: what it is, says and offers, and its box in `origin`. */
    function controlOf(node,origin) {
        const base={tag:node.tagName.toLowerCase(),className:classes(node),id:node.id||null,shown:shown(node)};
        if(!base.shown)return base;
        const style=getComputedStyle(node),input=node.querySelector('input');
        // A group (the layout `.segmented`) is recorded button by button: its joined text says nothing about either.
        const buttons=[...node.querySelectorAll(':scope > button')];
        return {...base,text:buttons.length?null:textOf(node,'select, option'),label:node.getAttribute('aria-label'),pressed:node.getAttribute('aria-pressed'),
            role:buttons.length?node.getAttribute('role'):null,buttons:buttons.length?buttons.map(button=>controlOf(button,node)):null,
            primary:node.classList.contains('ca-primary'),htmlFor:node.htmlFor||null,...relative(node,origin),
            ...font(node),...edges(node),...layout(node),minHeight:style.minHeight,minWidth:style.minWidth,flexGrow:style.flexGrow,flexBasis:style.flexBasis,
            icon:iconOf(node),
            options:node.tagName==='SELECT'?[...node.options].map(option=>option.textContent):null,
            input:when(input,()=>({type:input.type,id:input.id,placeholder:input.placeholder,label:input.getAttribute('aria-label'),
                ...font(input),...edges(input),minHeight:getComputedStyle(input).minHeight,...relative(input,node),exact:{width:rect(input).width}})),
            // Unrounded place, for a comparison that lays a row out again; `stripExact` removes it before any diff.
            exact:{left:rect(node).left-rect(origin).left,width:rect(node).width}};
    }
    function rowOf(row) {
        return {...layout(row),...edges(row),...relative(row,main),exact:{width:rect(row).width},items:[...row.children].map(node=>controlOf(node,row))};
    }
    const gallery=main?.querySelector(':scope > .gallery-layout');
    const body=gallery?.querySelector(':scope > .gallery-body');
    const grid=body?.querySelector(':scope > .image-grid');
    function captionOf(caption) {
        return {...layout(caption),...edges(caption),...font(caption),...relative(caption,body),
            // A span's width follows its text, so only its line box is recorded (the D17 date's text differs).
            spans:[...caption.children].filter(shown).map(span=>({tag:span.tagName.toLowerCase(),id:span.id||null,text:textOf(span),...font(span),
                top:relative(span,caption).top,height:relative(span,caption).height}))};
    }
    function gridOf() {
        const style=getComputedStyle(grid);
        return {id:grid.id||null,className:classes(grid),columns:style.gridTemplateColumns.split(/\s+/).length,
            ...css(grid,'columnGap','rowGap'),...relative(grid,body),cardCount:grid.querySelectorAll(':scope > .media-card').length};
    }
    function cardOf(card) {
        const art=card.querySelector(':scope > .media-art'),open=art?.querySelector('.image-open'),img=open?.querySelector('img');
        const overlays=art?.querySelector('.image-overlays'),info=card.querySelector(':scope > .media-info');
        const copy=info?.querySelector(':scope > div:not(.image-card-actions)'),actions=info?.querySelector('.image-card-actions');
        const h3=copy?.querySelector('h3'),p=copy?.querySelector('p');
        return {className:classes(card),ratio:card.style.getPropertyValue('--media-ratio').replace(/\s/g,''),...relative(card,grid),
            exact:{left:rect(card).left-rect(grid).left,top:rect(card).top-rect(grid).top,height:rect(card).height},
            ...edges(card),...css(card,'boxShadow','overflow','display'),
            art:when(art,()=>({...relative(art,card),...css(art,'position')})),
            open:when(open,()=>({label:open.getAttribute('aria-label'),...relative(open,card),...edges(open),...css(open,'display','overflow')})),
            img:when(img,()=>({alt:img.alt,width:img.getAttribute('width'),height:img.getAttribute('height'),...sized(img),
                ...css(img,'objectFit','aspectRatio','transform','transitionProperty','transitionDuration')})),
            // An overlay item's horizontal margin may be `auto`, whose used value is the free space beside it: it is kept
            // to the pixel, and the item's place is also recorded as its inset from the overlay's right edge.
            overlays:when(overlays,()=>!shown(overlays)?{display:getComputedStyle(overlays).display,shown:false}:({shown:true,...layout(overlays),
                ...css(overlays,'display','pointerEvents'),...relative(overlays,card),
                items:[...overlays.children].map(node=>{const item=controlOf(node,card);
                    return item.shown?{...item,marginLeft:`${px(parseFloat(item.marginLeft))}px`,marginRight:`${px(parseFloat(item.marginRight))}px`,
                        rightInset:px(rect(overlays).right-rect(node).right)}:item;})})),
            info:when(info,()=>({...layout(info),...edges(info),...relative(info,card),
                // The text and the actions are placed in the card (what is seen), so a centred list row compares by place.
                // The text's heights are the unrounded flow (the same CSS lays them out alike in both documents), so lines
                // of 16.5px and 13.5px add up exactly.
                copy:when(copy,()=>({left:relative(copy,card).left,top:relative(copy,card).top,height:rect(copy).height,...css(copy,'minWidth')})),
                h3:when(h3,()=>({text:h3.textContent,title:h3.getAttribute('title'),...font(h3),...edges(h3),
                    ...css(h3,'overflowX','textOverflow','whiteSpace'),height:rect(h3).height})),
                // `maxContent` is the subtitle's one-line width (an unwrapped hidden copy beside it), so a wider text column can
                // be shown to fit it on one line.
                p:when(p,()=>({text:p.textContent,...font(p),...edges(p),height:rect(p).height,maxContent:(()=>{
                    const probe=p.cloneNode(true);probe.style.cssText='position:absolute;visibility:hidden;white-space:nowrap;width:max-content';
                    p.after(probe);const width=rect(probe).width;probe.remove();return px(width);})()})),
                actions:when(actions,()=>({...layout(actions),...relative(actions,card),...css(actions,'flexShrink','minWidth'),
                    buttons:[...actions.children].map(node=>controlOf(node,actions))}))}))};
    }
    function continuationOf(continuation) {
        return {id:continuation.id||null,...layout(continuation),...edges(continuation),...relative(continuation,body),
            items:[...continuation.children].filter(shown).map(node=>controlOf(node,continuation))};
    }
    function panelOf() {
        const panel=gallery.children[1];
        // The panel's content (and so the layout's height) is the inspector's, PR D; its box is compared here.
        const {height,...place}=relative(gallery,main);
        return {...css(gallery,'display','gridTemplateColumns','columnGap','rowGap','alignItems'),...place,
            box:when(panel,()=>{const box=relative(panel,gallery);return {left:box.left,top:box.top,width:box.width,...css(panel,'position','top')};})};
    }
    const continuation=body?.querySelector(':scope > .infinite-continuation');
    function bulkbarOf(bar) {
        return {id:bar.id||null,...relative(bar,main),children:bar.children.length,bar:when(bar.querySelector(':scope > .bulkbar'),rowOf)};
    }
    // ---- Prompt library (#249 E3). The concept's main holds the toolbar, caption and grid; production's shelf wraps
    // them in `section.library-shelf` and windows the cards into `.library-row`s (spec §3.4: "row windowing kept"),
    // so every Library box is found by descendant and placed relative to `main` or its own card.
    // A `display: contents` node has no box: its children stand in its place (the D23 prompt-block mount point).
    const boxedChildren=node=>[...node.children].flatMap(child=>getComputedStyle(child).display==='contents'?boxedChildren(child):[child]);
    function libraryCardOf(card,origin) {
        const art=card.querySelector(':scope > .card-art'),content=card.querySelector(':scope > .card-content'),footer=card.querySelector(':scope > .card-footer');
        const title=content?.querySelector(':scope > .card-title'),meta=content?.querySelector(':scope > .card-meta'),tags=content?.querySelector(':scope > .tags');
        const section=content?.querySelector('.prompt-section'),heading=section?.querySelector(':scope > .prompt-heading');
        const text=section?.querySelector(':scope > .prompt-text'),facts=footer?.querySelector(':scope > span'),actions=footer?.querySelector(':scope > .row-actions');
        const lines=node=>Math.round(rect(node).height/parseFloat(getComputedStyle(node).lineHeight));
        const spanOf=(node,parent)=>({tag:node.tagName.toLowerCase(),className:classes(node),text:textOf(node),...font(node),...edges(node),...relative(node,parent)});
        return {tag:card.tagName.toLowerCase(),className:classes(card),...relative(card,origin),...edges(card),...css(card,'overflow','display','boxShadow'),
            children:[...card.children].map(classes),
            art:when(art,()=>({...relative(art,card),...css(art,'display','gridTemplateColumns','columnGap','rowGap','backgroundColor'),
                items:[...art.children].map(node=>({tag:node.tagName.toLowerCase(),className:classes(node),alt:node.getAttribute('alt'),...relative(node,art),
                    ...css(node,'objectFit')}))})),
            content:when(content,()=>({...relative(content,card),...edges(content),children:boxedChildren(content).map(node=>`${node.tagName.toLowerCase()}.${classes(node)}`)})),
            title:when(title,()=>({...layout(title),...edges(title),...relative(title,content),
                items:[...title.children].map(node=>node.tagName==='BUTTON'?controlOf(node,title):spanOf(node,title))})),
            section:when(section,()=>({className:classes(section),...relative(section,content),...edges(section),
                label:when(heading?.querySelector('.role-label'),label=>({text:label.textContent,...font(label)})),
                actions:when(heading?.querySelector('.prompt-actions'),group=>({...layout(group),...relative(group,section),
                    buttons:[...group.children].map(node=>controlOf(node,group))})),
                text:when(text,()=>({text:text.textContent,...font(text),...edges(text),...relative(text,section),lines:lines(text)}))})),
            meta:when(meta,()=>({...layout(meta),...edges(meta),...font(meta),...relative(meta,content),items:[...meta.children].map(node=>spanOf(node,meta))})),
            // A chip's unrounded place too, so a re-ordered row can be laid out again (`reflow`; `stripExact` removes it).
            tags:when(tags,()=>({...layout(tags),...edges(tags),...relative(tags,content),exact:{width:rect(tags).width},
                items:[...tags.children].map(node=>({...spanOf(node,tags),shown:shown(node),exact:{left:rect(node).left-rect(tags).left,width:rect(node).width}}))})),
            footer:when(footer,()=>({...layout(footer),...edges(footer),...font(footer),...relative(footer,card),
                // What is seen (hidden-only text left out) and what is spoken (aria-hidden text left out).
                facts:when(facts,()=>({text:textOf(facts,'.ca-visually-hidden'),spoken:accessibleText(facts),...font(facts),...relative(facts,footer)})),
                actions:when(actions,()=>({...layout(actions),...relative(actions,footer),buttons:[...actions.children].map(node=>controlOf(node,actions))}))}))};
    }
    function libraryOf() {
        const grid=main?.querySelector('.library-grid');
        if(!grid)return null;
        const toolbar=main.querySelector('search.toolbar'),caption=main.querySelector('.section-caption');
        const cards=[...grid.querySelectorAll('.prompt-card')];
        // Whatever the page shows after the grid, in document order (the concept: nothing).
        const trailing=[];
        for(let node=grid;node&&node!==main;node=node.parentElement)
            for(let next=node.nextElementSibling;next;next=next.nextElementSibling)if(shown(next))trailing.push({tag:next.tagName.toLowerCase(),className:classes(next),text:next.innerText.replace(/\s+/g,' ').trim()});
        const style=getComputedStyle(grid);
        return {toolbar:when(toolbar,rowOf),
            caption:when(caption,()=>({...layout(caption),...edges(caption),...font(caption),...relative(caption,main),
                spans:[...caption.children].filter(shown).map(span=>({tag:span.tagName.toLowerCase(),text:textOf(span),...font(span),
                    top:relative(span,caption).top,height:relative(span,caption).height}))})),
            grid:{columns:style.gridTemplateColumns.split(/\s+/).length,...css(grid,'columnGap','rowGap'),...relative(grid,main),cardCount:cards.length},
            cards:cards.slice(0,6).map(card=>libraryCardOf(card,grid)),trailing};
    }
    // ---- Prompt history (#249 F5): renderHistory a587–622, style.css 1059–1179 and 1546–1567. The concept's main holds
    // the summary, the variants panel, the comparison and the gallery; production's sit in `section.library-history`, so
    // each is found by descendant and placed relative to `main` (vertical rhythm) or to its own section.
    const spoken=node=>node.classList.contains('ca-visually-hidden');
    const outline=node=>css(node,'outlineStyle','outlineWidth','outlineColor','outlineOffset');
    const pseudo=(node,which)=>{const style=getComputedStyle(node,which);return Object.fromEntries(['content','position','top','left','width','height',
        'borderLeftWidth','borderBottomWidth','borderLeftColor','borderBottomColor','borderBottomLeftRadius','backgroundColor','color'].map(key=>[key,style[key]]));};
    // What a section shows, in order: each shown child as `tag.classes`, visually hidden statuses apart (`spoken`).
    const structureOf=node=>({children:boxedChildren(node).filter(child=>shown(child)&&!spoken(child)).map(child=>`${child.tagName.toLowerCase()}.${classes(child)}`),
        spoken:[...node.querySelectorAll('.ca-visually-hidden')].filter(shown).map(child=>({role:child.getAttribute('role'),text:child.textContent}))});
    function promptSectionOf(section,origin) {
        const heading=section.querySelector(':scope > .prompt-heading'),label=heading?.querySelector('.role-label');
        const actions=heading?.querySelector('.prompt-actions'),text=section.querySelector(':scope > .prompt-text');
        return {className:classes(section),...relative(section,origin),...edges(section),
            label:when(label,()=>({text:label.textContent,...font(label)})),
            actions:when(actions,()=>({...layout(actions),...relative(actions,section),buttons:[...actions.children].map(node=>controlOf(node,actions))})),
            text:when(text,()=>({text:text.textContent,...font(text),...edges(text),...relative(text,section),
                lines:Math.round(rect(text).height/parseFloat(getComputedStyle(text).lineHeight))}))};
    }
    function panelHeadingOf(heading,origin) {
        const h2=heading.querySelector('h2'),note=heading.querySelector('.small'),actions=heading.querySelector(':scope > .row-actions');
        return {...layout(heading),...edges(heading),...relative(heading,origin),
            h2:when(h2,()=>({text:h2.textContent,...font(h2),...edges(h2),...relative(h2,heading)})),
            note:when(note,()=>({tag:note.tagName.toLowerCase(),text:note.textContent,...font(note),...edges(note),...relative(note,heading)})),
            actions:when(actions,()=>({...layout(actions),...relative(actions,heading),buttons:[...actions.children].filter(shown).map(node=>controlOf(node,actions))}))};
    }
    function laneOf(lane,timeline) {
        const dot=lane.querySelector(':scope > .variant-dot'),label=lane.querySelector(':scope > span'),run=lane.querySelector(':scope > .run-dot');
        return {className:classes(lane),offset:lane.style.getPropertyValue('--lane-offset'),...relative(lane,timeline),...edges(lane),...css(lane,'minWidth','position'),
            before:pseudo(lane,'::before'),
            dot:when(dot,()=>({...controlOf(dot,lane),...outline(dot),...css(dot,'position','top','left','width','height','color')})),
            label:when(label,()=>({text:label.textContent,...font(label),...relative(label,lane),...css(label,'position','top','left')})),
            run:shown(run)?{...controlOf(run,lane),...css(run,'position','top','left','width','height')}:null};
    }
    function timelineOf(timeline,origin) {
        const positive=timeline.querySelector(':scope > .positive-lane'),span=positive?.querySelector('span');
        return {...edges(timeline),...css(timeline,'overflowX','position'),...relative(timeline,origin),
            positive:when(positive,()=>({...edges(positive),...css(positive,'height','minWidth'),...relative(positive,timeline),before:pseudo(positive,'::before'),
                span:when(span,()=>({text:span.textContent,...font(span),...relative(span,positive)}))})),
            lanes:[...timeline.querySelectorAll(':scope > .variant-lane')].map(lane=>laneOf(lane,timeline)),
            structure:structureOf(timeline)};
    }
    function variantsOf(panel) {
        const heading=panel.querySelector(':scope > .panel-heading'),timeline=panel.querySelector(':scope > .timeline'),lower=panel.querySelector(':scope > .timeline-lower');
        return {className:classes(panel),...edges(panel),...relative(panel,main),structure:structureOf(panel),
            heading:when(heading,()=>panelHeadingOf(heading,panel)),timeline:when(timeline,()=>timelineOf(timeline,panel)),
            lower:when(lower,()=>({...layout(lower),...edges(lower),...relative(lower,panel),
                items:[...lower.children].filter(shown).map(node=>({...controlOf(node,lower),...font(node)}))}))};
    }
    function compareOf(section) {
        const h2=section.querySelector(':scope > h2'),intro=section.querySelector(':scope > p:not(.small)'),grid=section.querySelector(':scope > .compare-grid');
        return {...edges(section),...relative(section,main),structure:structureOf(section),
            h2:when(h2,()=>({text:h2.textContent,...font(h2),...edges(h2)})),
            intro:when(intro,()=>({text:intro.textContent,...font(intro),...edges(intro),...relative(intro,section)})),
            grid:when(grid,()=>({columns:getComputedStyle(grid).gridTemplateColumns.split(/\s+/).length,...css(grid,'columnGap','rowGap'),...relative(grid,section),
                items:[...grid.children].map(item=>({className:classes(item),...edges(item),...relative(item,grid),
                    sections:[...item.querySelectorAll('.prompt-section')].map(node=>promptSectionOf(node,item))}))})),
            notes:[...section.querySelectorAll(':scope > p.small')].map(note=>({text:note.textContent,...font(note),...edges(note),...relative(note,section)})),
            added:[...section.querySelectorAll(':scope > .diff-added')].map(chip=>({text:chip.textContent,...font(chip),...edges(chip),...css(chip,'display'),...relative(chip,section)}))};
    }
    function historyOf() {
        const panel=main?.querySelector('section.timeline-panel');
        if(!panel)return null;
        const summary=main.querySelector('article.surface'),gallery=main.querySelector('section.history-gallery');
        const compare=[...main.querySelectorAll('section.surface:not(.timeline-panel)')].find(shown);
        const chip=summary?.querySelector(':scope > .chip'),meta=summary?.querySelector(':scope > .card-meta');
        const galleryHeading=gallery?.querySelector(':scope > .panel-heading');
        return {
            summary:when(summary,()=>({...edges(summary),...relative(summary,main),structure:structureOf(summary),
                chip:when(chip,()=>({text:chip.textContent,...font(chip),...edges(chip),...relative(chip,summary)})),
                sections:[...summary.querySelectorAll('.prompt-section')].map(node=>promptSectionOf(node,summary)),
                meta:when(meta,()=>({...layout(meta),...edges(meta),...font(meta),...relative(meta,summary),
                    items:[...meta.children].filter(shown).map(node=>({text:node.textContent,...font(node),...relative(node,meta)}))}))})),
            variants:variantsOf(panel),
            compare:when(compare,compareOf),
            gallery:when(gallery,()=>({...edges(gallery),...relative(gallery,main),
                heading:when(galleryHeading,()=>panelHeadingOf(galleryHeading,gallery))})),
        };
    }
    return {chrome:chromeOf(),heading:headingOf(),
        toolbar:when(main?.querySelector(':scope > search.toolbar'),rowOf),
        filterRow:when(main?.querySelector(':scope > .filter-row'),rowOf),
        bulkbar:when(main?.querySelector(':scope > #bulkbar'),bulkbarOf),
        caption:when(body?.querySelector(':scope > .section-caption'),captionOf),
        grid:when(grid,gridOf),
        cards:grid?[...grid.querySelectorAll(':scope > .media-card')].slice(0,6).map(cardOf):null,
        continuation:when(continuation,continuationOf),
        panel:when(gallery,panelOf),
        library:libraryOf(),history:historyOf(),noPageScroll:document.documentElement.scrollWidth<=innerWidth+1};
}

/**
 * The inspector (#249 D4): `panel` is the Images side panel's content (`aside#image-inspector`, app.js imageDetails
 * a400–414) and `viewerDetails` the viewer's full details (`aside#viewer-details`, imageDetails(i, true)), each null
 * when absent. Both are measured by one in-page function with the concept's selectors in either document.
 */
export async function inspectorOf(page) {
    return {panel:await page.evaluate(measureInspector,'#image-inspector'),
        viewerDetails:await page.evaluate(measureInspector,'#viewer-details')};
}

/** Runs in the page; self-contained. Every box is relative to the measured root. */
export function measureInspector(selector) {
    const root=document.querySelector(selector);
    if(!root)return null;
    const px=value=>Math.round(value);
    const css=(node,...keys)=>{const style=getComputedStyle(node);return Object.fromEntries(keys.map(key=>[key,style[key]]));};
    const shown=node=>Boolean(node)&&node.getClientRects().length>0&&getComputedStyle(node).visibility!=='hidden';
    const rect=node=>node.getBoundingClientRect();
    const font=node=>css(node,'fontFamily','fontSize','fontWeight','lineHeight','letterSpacing','color','textTransform');
    const edges=node=>css(node,'paddingTop','paddingRight','paddingBottom','paddingLeft','marginTop','marginRight','marginBottom','marginLeft',
        'borderTopWidth','borderTopStyle','borderTopColor','borderRightWidth','borderBottomWidth','borderTopLeftRadius','backgroundColor');
    const layout=node=>css(node,'display','flexDirection','flexWrap','justifyContent','alignItems','columnGap','rowGap');
    const box=node=>{const a=rect(node),b=rect(root);return {left:px(a.left-b.left),top:px(a.top-b.top),width:px(a.width),height:px(a.height)};};
    const classes=node=>[...node.classList].sort().join(' ');
    const when=(node,measure)=>node?measure(node):null;
    // Visible text: without visually hidden prefixes and aria-hidden glyphs, whitespace collapsed.
    const textOf=(node,skip='.ca-visually-hidden, [aria-hidden="true"]')=>{const copy=node.cloneNode(true);copy.querySelectorAll(skip).forEach(item=>item.remove());
        return copy.textContent.replace(/\s+/g,' ').trim();};
    const hiddenOf=node=>[...node.querySelectorAll('.ca-visually-hidden')].map(item=>item.textContent).join('')||null;
    const nameOf=node=>node.getAttribute('aria-label')??textOf(node,'[aria-hidden="true"]');
    function iconOf(node) {
        const svg=node.querySelector(':scope > svg.icon');
        if(!svg)return null;
        return {width:px(rect(svg).width),height:px(rect(svg).height),viewBox:svg.getAttribute('viewBox'),hidden:svg.getAttribute('aria-hidden'),
            d:svg.querySelector('path')?.getAttribute('d')??null,...css(svg,'strokeWidth','color')};
    }
    function controlOf(node) {
        const style=getComputedStyle(node);
        return {tag:node.tagName.toLowerCase(),className:classes(node),text:textOf(node),name:nameOf(node),pressed:node.getAttribute('aria-pressed'),
            primary:node.classList.contains('ca-primary'),...box(node),...font(node),...edges(node),...layout(node),
            minHeight:style.minHeight,minWidth:style.minWidth,icon:iconOf(node)};
    }
    // #332 D146: a category chip's visible origin (`span.chip-source`), null on every other chip.
    function chipOf(node) {
        const source=node.querySelector(':scope > .chip-source');
        return {tag:node.tagName.toLowerCase(),className:classes(node),text:textOf(node),hidden:hiddenOf(node),...box(node),...font(node),...edges(node),
            minHeight:getComputedStyle(node).minHeight,
            source:when(source,()=>({text:source.textContent,...font(source),...css(source,'whiteSpace'),...box(source)}))};
    }
    function rowOf(row) {
        return {...layout(row),...edges(row),...box(row),chips:[...row.children].map(chipOf)};
    }
    function sectionOf(section) {
        const heading=section.querySelector(':scope > .prompt-heading'),label=heading?.querySelector('.role-label');
        const actions=heading?.querySelector('.prompt-actions'),text=section.querySelector(':scope > .prompt-text');
        return {className:classes(section),...edges(section),...box(section),
            heading:when(heading,()=>({...layout(heading),...edges(heading),...box(heading)})),
            label:when(label,()=>({tag:label.tagName.toLowerCase(),text:label.textContent,...font(label),...box(label)})),
            actions:when(actions,()=>({...layout(actions),...box(actions),buttons:[...actions.children].map(controlOf)})),
            text:when(text,()=>({tag:text.tagName.toLowerCase(),className:classes(text),text:text.textContent,tabIndex:text.tabIndex,...font(text),...edges(text),
                ...css(text,'whiteSpace','overflowWrap'),...box(text),lines:px(rect(text).height/parseFloat(getComputedStyle(text).lineHeight))}))};
    }
    function tagSectionOf(section) {
        const heading=section.querySelector(':scope > .prompt-heading'),label=heading?.querySelector('.role-label'),row=section.querySelector(':scope > .tags');
        return {className:classes(section),name:section.getAttribute('aria-label'),...edges(section),...box(section),
            heading:when(heading,()=>({...layout(heading),...edges(heading),...box(heading)})),
            label:when(label,()=>({tag:label.tagName.toLowerCase(),text:label.textContent,...font(label),...box(label)})),
            row:when(row,rowOf)};
    }
    function factsOf(list) {
        const style=getComputedStyle(list);
        return {...css(list,'display','columnGap','rowGap','fontSize'),...edges(list),columns:style.gridTemplateColumns.split(/\s+/).length,...box(list),
            items:[...list.children].map(item=>{const dt=item.querySelector('dt'),dd=item.querySelector('dd');
                return {...box(item),dt:when(dt,()=>({text:dt.textContent,...font(dt),...edges(dt)})),dd:when(dd,()=>({text:dd.textContent,...font(dd),...edges(dd)}))};})};
    }
    const describe=item=>`${item.localName}${item.classList.length?'.'+[...item.classList].join('.'):''}`;
    const panel=root.classList.contains('detail-panel');
    // The panel's `.detail-inner`, the viewer's class-less wrapper (D20). A prompt-block mount point that is
    // `display: contents` is transparent: its sections count as the wrapper's own children.
    const inner=root.querySelector(panel?':scope > .detail-inner':':scope > div');
    const parts=inner?[...inner.children].flatMap(item=>getComputedStyle(item).display==='contents'?[...item.children]:[item]):[];
    const sections=parts.filter(item=>item.classList.contains('prompt-section'));
    const firstSection=parts.indexOf(sections[0]);
    const rows=parts.filter(item=>item.classList.contains('tags'));
    const heading=parts.find(item=>item.classList.contains('panel-heading')),h2=heading?.querySelector('h2'),format=heading?.querySelector('.chip');
    const filename=parts.find(item=>item.matches('p.small'));
    const cover=root.querySelector(':scope > .detail-cover'),footer=root.querySelector(':scope > .detail-footer');
    // What a closed disclosure holds is not offered (only its summary is), whatever boxes the engine keeps for it.
    const offered=node=>{const closed=node.closest('details:not([open])');return !closed||node.parentElement===closed&&node.localName==='summary';};
    const controls=[...root.querySelectorAll('button, a[href], [tabindex]:not([tabindex="-1"]), summary')].filter(node=>shown(node)&&offered(node));
    return {
        className:classes(root),label:root.getAttribute('aria-label'),
        ...css(root,'position','top','overflowX','overflowY','maxHeight'),...edges(root),width:px(rect(root).width),height:px(rect(root).height),
        structure:{outer:[...root.children].map(describe),inner:parts.map(describe)},
        cover:when(cover,()=>({tag:cover.tagName.toLowerCase(),alt:cover.getAttribute('alt'),...box(cover),...css(cover,'objectFit','display')})),
        inner:when(inner,()=>({...edges(inner),...box(inner)})),
        heading:when(heading,()=>({...layout(heading),...edges(heading),...box(heading),
            h2:when(h2,()=>({text:h2.textContent,...font(h2),...edges(h2),...box(h2)})),format:when(format,chipOf)})),
        filename:when(filename,()=>({text:filename.textContent,...font(filename),...edges(filename),...box(filename)})),
        badges:when(rows.find(row=>parts.indexOf(row)<firstSection),rowOf),
        sections:sections.map(sectionOf),
        tags:when(rows.find(row=>parts.indexOf(row)>firstSection),rowOf),
        // #449 D160: the labelled tag sections after the prompt blocks, each with its frame, heading, label and chip row.
        tagSections:parts.filter(item=>item.matches('section.tag-section')).map(tagSectionOf),
        facts:when(parts.find(item=>item.matches('dl.meta-grid')),factsOf),
        // Disclosures (viewer only) by their summary and frame; their content is the D20 production facts.
        details:parts.filter(item=>item.localName==='details').map(item=>{const summary=item.querySelector(':scope > summary');
            return {open:item.open,...edges(item),...box(item),summary:when(summary,()=>({text:summary.textContent,...font(summary),...box(summary)}))};}),
        footer:when(footer,()=>({...layout(footer),...edges(footer),...box(footer),buttons:[...footer.children].map(controlOf)})),
        // The viewer's pair send and history follow the wrapper directly (imageDetails(i, true) has no footer).
        actions:panel?null:[...root.children].filter(item=>item.localName==='button').map(controlOf),
        controls:controls.map(node=>({tag:node.tagName.toLowerCase(),text:textOf(node),name:nameOf(node),pressed:node.getAttribute('aria-pressed'),
            primary:node.classList.contains('ca-primary'),minHeight:getComputedStyle(node).minHeight})),
    };
}

// #249 System S7 Task 20, shared since S12 (#329): the maintenance plan dialog's measurement and its D100 mapping,
// moved unchanged from system-maintenance-concept.spec.mjs so the rotated-log review uses the same ones.
/** Runs in the page: the open review dialog (plan or duplicates): its title, body and the concept's tokens. */
export function measureReview() {
    const dialog = document.querySelector('dialog#modal[open]');
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const text = node => node?.textContent.trim() ?? null;
    const body = dialog.querySelector('#modal-body');
    const grid = body.querySelector(':scope > dl.meta-grid');
    const compare = body.querySelector('.compare-grid');
    const item = compare?.querySelector('.compare-item');
    const notes = [...body.querySelectorAll(':scope > p.small')];
    const note = notes.at(-1);
    const actions = body.querySelector(':scope > .dialog-actions');
    return {
        title: text(dialog.querySelector('#modal-title')),
        tree: [...body.children].map(name),
        heading: body.querySelector(':scope > h3') && { text: text(body.querySelector(':scope > h3')),
            ...css(body.querySelector(':scope > h3'), 'fontSize', 'fontWeight', 'color', 'marginBottom') },
        grid: grid && { rows: [...grid.children].map(row => [text(row.querySelector('dt')), text(row.querySelector('dd'))]),
            ...css(grid, 'display', 'gridTemplateColumns', 'columnGap', 'rowGap'),
            dt: css(grid.querySelector('dt'), 'fontSize', 'color', 'textTransform'), dd: css(grid.querySelector('dd'), 'fontSize', 'color') },
        details: body.querySelector(':scope > details') && { summary: text(body.querySelector(':scope > details > summary')),
            tree: [...body.querySelector(':scope > details').children].map(name) },
        backup: body.querySelector(':scope > .notice') && { tree: [...body.querySelector(':scope > .notice').children].map(name),
            text: [...body.querySelectorAll(':scope > .notice > div:first-child > *')].map(node => [node.localName, text(node)]),
            buttons: [...body.querySelectorAll(':scope > .notice button')].map(text) },
        compare: compare && { tree: [...compare.children].map(name), ...css(compare, 'display', 'gridTemplateColumns', 'columnGap'),
            items: [...compare.children].map(entry => ({ tree: [...entry.children].map(name), label: text(entry.querySelector('label')),
                checked: entry.querySelector('input[type=radio]').checked, image: entry.querySelector('img')?.className ?? null })),
            item: css(item, 'paddingTop', 'paddingLeft', 'backgroundColor', 'borderTopWidth', 'borderTopColor', 'borderTopLeftRadius'),
            image: css(item.querySelector('img'), 'width', 'objectFit'),
            label: css(item.querySelector('label'), 'fontSize', 'color'),
            radio: { name: item.querySelector('input[type=radio]').name.startsWith('keep-') ? 'keep-*' : 'other',
                type: item.querySelector('input').type } },
        note: note && { text: text(note), ...css(note, 'fontSize', 'color', 'marginTop') },
        actions: { tree: [...actions.children].map(name), ...css(actions, 'display', 'justifyContent', 'columnGap', 'marginTop'),
            buttons: [...actions.children].map(item => ({ text: text(item), className: item.className,
                ...css(item, 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft', 'borderTopColor') })) },
    };
}

export const BACKUP_BOX = Object.freeze({ tree: ['div', 'div.row-actions'], text: [['h3', 'Back up the database first (recommended)'],
    ['p', 'Downloads a copy of the prompts database before this operation changes any records.']], buttons: ['Back up now'] });

/**
 * D100: the server's plan in the concept's dialog: the operation's row title as its `h3` (the concept prints its
 * `data-plan` name), the four rows with the plan's values, the listed records (`details`), the hold line in the
 * concept's `p.small`, the D55 backup box before the actions, and "Apply reviewed operation". No sample note.
 */
export function mapPlanConcept(reference, title, plan) {
    const backup = plan.backup.policy === 'off' ? 'No safety backup (the policy is off)'
        : `Create and verify before changes (${plan.backup.destination_label})`;
    const tree = ['h3', 'dl.meta-grid', ...(plan.details.length ? ['details'] : []), 'p.small', 'div.notice', 'div.dialog-actions'];
    const [cancel, apply] = reference.actions.buttons;
    return {
        ...reference,
        tree,
        heading: { ...reference.heading, text: title },
        grid: { ...reference.grid, rows: [['Scope', plan.scope.label],
            ['Protected', plan.protected.reasons.length ? plan.protected.reasons.join(' · ') : 'Nothing is protected'],
            ['Backup', backup], ['Media on disk', plan.media.label]] },
        details: plan.details.length ? { summary: `Listed records (${plan.details.length})`, tree: ['summary', 'pre'] } : null,
        backup: BACKUP_BOX,
        note: { ...reference.note, text: 'Nothing changes until you apply. This review holds for 15 minutes.' },
        actions: { ...reference.actions, buttons: [cancel, { ...apply, text: 'Apply reviewed operation' }] },
    };
}
