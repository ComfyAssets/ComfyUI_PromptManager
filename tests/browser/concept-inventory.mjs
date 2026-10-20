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
 * Inventory sections: {chrome, heading, toolbar, filterRow, caption, grid, cards, panel, library, history,
 * noPageScroll}. A section is null when its element is absent. Sections whose page PR has not landed yet
 * (toolbar … history, PRs C–F) are null until that PR adds its measurer here.
 */
export function inventoryOf(page,scope='main') {
    return page.evaluate(measureInventory,scope);
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
    return {chrome:chromeOf(),heading:headingOf(),toolbar:null,filterRow:null,caption:null,grid:null,cards:null,panel:null,
        library:null,history:null,noPageScroll:document.documentElement.scrollWidth<=innerWidth+1};
}
