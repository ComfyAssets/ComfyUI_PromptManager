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
    function chromeOf() {
        if(!sidebar||!workspace)return null;
        const shell=document.querySelector('.app-shell');
        const brand=sidebar.querySelector('.brand'),mark=brand.querySelector('.brand-mark'),small=brand.querySelector('small');
        const bottom=sidebar.querySelector('.sidebar-bottom'),connection=sidebar.querySelector('.connection');
        const topbar=workspace.querySelector('.topbar'),crumbs=topbar.querySelector('.breadcrumbs'),actions=topbar.querySelector('.top-actions');
        const footer=workspace.querySelector('.app-footer');
        const sidebarStyle=getComputedStyle(sidebar);
        const columns=getComputedStyle(shell).gridTemplateColumns.split(' ');
        const item=node=>{
            const base={tag:node.tagName.toLowerCase(),className:node.className,shown:shown(node)};
            if(!base.shown)return base;
            const box=relative(node,actions);
            const select=node.querySelector('select');
            return {...base,text:textOf(node,'select, .count'),...font(node),...edges(node),...layout(node),width:box.width,height:box.height,top:box.top,
                count:node.querySelector('.count')?.textContent??null,
                select:select?{options:[...select.options].map(option=>[option.value,option.textContent]),label:select.getAttribute('aria-label'),
                    labelled:select.labels?.[0]===node,...font(select),...edges(select),minHeight:getComputedStyle(select).minHeight,
                    width:px(rect(select).width),height:px(rect(select).height)}:null,
                hiddenSuffix:[...node.querySelectorAll('[aria-hidden="true"]')].map(child=>child.textContent).join('')||null,
                whiteSpace:getComputedStyle(node).whiteSpace};
        };
        return {
            title:document.title.replace(/ · Prompt Manager.*$/,''),
            skip:{text:document.querySelector('a.skip')?.textContent.trim()??null,...css(document.querySelector('a.skip'),'position','top','left')},
            body:{...font(document.body),backgroundColor:getComputedStyle(document.body).backgroundColor,
                tokens:Object.fromEntries(['--ca-text','--ca-muted','--ca-focus','--ca-accent','--ca-field','--ca-surface','--ca-line',
                    '--line','--page','--sidebar','--soft-blue','--green'].map(name=>[name,getComputedStyle(document.body).getPropertyValue(name).trim()]))},
            shell:{display:getComputedStyle(shell).display,columns:columns.length,sidebarColumn:columns.length>1?columns[0]:null,
                minHeight:getComputedStyle(shell).minHeight},
            sidebar:{...layout(sidebar),...edges(sidebar),...css(sidebar,'top','overflowY','borderInlineEndWidth'),width:px(rect(sidebar).width),
                fillsViewport:Math.abs(rect(sidebar).height-innerHeight)<=1},
            brand:{text:textOf(brand),href:brand.getAttribute('href')?.replace(/^#\/?/,''),...relative(brand,sidebar),...font(brand),...layout(brand),...edges(brand),
                mark:{text:textOf(mark),...font(mark),width:px(rect(mark).width),height:px(rect(mark).height)},
                small:{text:small.textContent,...font(small),...css(small,'display','marginTop')}},
            labels:[...sidebar.querySelectorAll('.sidebar-label')].map(node=>({text:textOf(node),shown:shown(node),...font(node),...layout(node),...edges(node)})),
            navs:[...sidebar.querySelectorAll('nav')].map(nav=>{const links=[...nav.querySelectorAll(':scope > a')];return {
                id:nav.id,label:nav.getAttribute('aria-label'),shown:shown(nav),...layout(nav),...edges(nav),
                width:px(rect(nav).width),rows:links.map(navRow)};}),
            bottom:(()=>{
                // In the column sidebar the bottom group is pinned to the end by `margin-top: auto`, whose used value is
                // only the free space above it, so the pin (its inset from the sidebar's padding edge) is recorded instead.
                const pinned=sidebarStyle.display==='flex';
                const {marginTop,...rest}=edges(bottom);
                return {...rest,...css(bottom,'display'),marginTop:pinned?'auto':marginTop,
                    bottomInset:pinned?px(rect(sidebar).bottom-rect(bottom).bottom-parseFloat(sidebarStyle.paddingBottom)):null};
            })(),
            connection:{shown:shown(connection),text:textOf(connection,'small'),small:connection.querySelector('small')?.textContent??null,
                ...layout(connection),...edges(connection),...font(connection),
                smallFont:font(connection.querySelector('small')),
                dot:(dot=>({...css(dot,'width','height','borderTopLeftRadius','backgroundColor','marginTop')}))(connection.querySelector('i'))},
            topbar:{...layout(topbar),...edges(topbar),...css(topbar,'height','minHeight'),...relative(topbar,workspace),
                fullWidth:px(rect(topbar).width)===px(rect(workspace).width)},
            breadcrumbs:{shown:shown(crumbs),text:textOf(crumbs),...layout(crumbs),...font(crumbs),
                parts:[...crumbs.querySelectorAll('span')].map(span=>({text:span.textContent,...font(span)})),
                ...(shown(crumbs)?{left:relative(crumbs,topbar).left,middle:px(rect(crumbs).top+rect(crumbs).height/2-rect(topbar).top)}:{})},
            topActions:{...layout(actions),...edges(actions),width:px(rect(actions).width),height:px(rect(actions).height),
                fullWidth:Math.abs(rect(actions).width-(rect(topbar).width-parseFloat(getComputedStyle(topbar).paddingLeft)-parseFloat(getComputedStyle(topbar).paddingRight)))<=1,
                rightInset:px(rect(topbar).right-rect(actions).right),
                items:[...actions.children].map(item)},
            main:{tag:main.tagName.toLowerCase(),tabIndex:main.tabIndex,...css(main,'paddingTop','paddingRight','paddingBottom','paddingLeft','maxWidth','marginLeft','marginRight')},
            footer:footer?{...layout(footer),...edges(footer),...font(footer),...css(footer,'height'),width:px(rect(footer).width),
                fullWidth:px(rect(footer).width)===px(rect(workspace).width),
                spans:[...footer.querySelectorAll(':scope > span')].map(span=>({text:span.textContent.replace(/\s+/g,' ').trim(),...font(span),height:px(rect(span).height)})),
                buttons:[...footer.querySelectorAll('button')].map(button=>({text:button.textContent.trim(),...font(button),...edges(button),
                    minHeight:getComputedStyle(button).minHeight,height:px(rect(button).height)}))}:null,
        };
    }
    function headingOf() {
        const heading=main?.querySelector(':scope > .heading');
        if(!heading)return null;
        const copy=heading.firstElementChild,h1=copy?.querySelector('h1'),description=copy?.querySelector('p'),actions=heading.querySelector('.heading-actions');
        return {...layout(heading),...edges(heading),height:px(rect(heading).height),
            copy:{tag:copy.tagName.toLowerCase(),...layout(copy)},
            h1:{id:h1.id,tabIndex:h1.tabIndex,text:h1.textContent,...font(h1),...edges(h1)},
            description:description?{className:description.className,text:description.textContent,...font(description),...edges(description)}:null,
            actions:actions?{...layout(actions),...edges(actions),
                fullWidth:px(rect(actions).width)===px(rect(heading).width),stacked:rect(actions).top>=rect(copy).bottom-0.5,
                buttons:[...actions.querySelectorAll('button')].map(node=>({text:node.textContent.trim(),primary:node.classList.contains('ca-primary'),
                    ...font(node),...edges(node),minHeight:getComputedStyle(node).minHeight,height:px(rect(node).height)}))}:null};
    }
    return {chrome:chromeOf(),heading:headingOf(),toolbar:null,filterRow:null,caption:null,grid:null,cards:null,panel:null,
        library:null,history:null,noPageScroll:document.documentElement.scrollWidth<=innerWidth+1};
}
