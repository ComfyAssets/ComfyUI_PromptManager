// Shared concept-vs-production inventory for the mock-true rebuild (#249, spec §7). One in-page measurer runs the
// same concept selectors in the immutable concept (/__test__/canonical/workspace) and in production, which now renders
// the concept's classes. The concept is never rewritten: each page's map<Page>Concept(reference) applies only the
// recorded spec-§8 deviations, each commented with its D-number, and diffInventory must then report nothing.
import {expect} from '../../tools/vendor/node_modules/@playwright/test/index.mjs';
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

/** Production at `#/<route>` with the D4 Theme select, once the sidebar's real counts (D27) have settled. */
export async function openProduction(page,route,theme,motion='no-preference') {
    await page.emulateMedia({reducedMotion:motion});
    await page.goto('about:blank');await page.goto(`${PRODUCTION_URL}#/${route}`);
    await selectTheme(page,theme);
    await expect(page.locator('#workspace-main #page-title')).toBeVisible();
    await expect(page.locator('#primary-nav .nav-count')).toHaveCount(3);
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
 * is detached. Returns an async undo that restores the stylesheet and the removed elements in place.
 */
export async function mutate(page,{css=null,remove=[]}={}) {
    const token=await page.evaluate(({css,remove})=>{
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
        (window.__conceptMutations??=new Map()).set(token,undo);
        return token;
    },{css,remove});
    return ()=>page.evaluate(token=>{
        const undo=window.__conceptMutations?.get(token);
        if(!undo)throw new Error('mutate: this mutation was already undone or the page reloaded');
        undo.reverse().forEach(step=>step());window.__conceptMutations.delete(token);
    },token);
}

/**
 * Inventory sections: {chrome, heading, toolbar, filterRow, bulkbar, caption, grid, cards, continuation, panel, library,
 * history, noPageScroll}. A section is null when its element is absent. The Images sections (toolbar … panel, #249 C7)
 * measure the concept's Images markup; `cards` holds the first six; `bulkbar` is the selection bar's place and, while
 * anything is checked, its `.bulkbar` row. `library` (#249 E3) holds the Prompt library's toolbar,
 * caption, grid, first six cards and whatever follows the grid; history stays null until PR F.
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
    return {chrome:chromeOf(),heading:headingOf(),
        toolbar:when(main?.querySelector(':scope > search.toolbar'),rowOf),
        filterRow:when(main?.querySelector(':scope > .filter-row'),rowOf),
        bulkbar:when(main?.querySelector(':scope > #bulkbar'),bulkbarOf),
        caption:when(body?.querySelector(':scope > .section-caption'),captionOf),
        grid:when(grid,gridOf),
        cards:grid?[...grid.querySelectorAll(':scope > .media-card')].slice(0,6).map(cardOf):null,
        continuation:when(continuation,continuationOf),
        panel:when(gallery,panelOf),
        library:libraryOf(),history:null,noPageScroll:document.documentElement.scrollWidth<=innerWidth+1};
}
