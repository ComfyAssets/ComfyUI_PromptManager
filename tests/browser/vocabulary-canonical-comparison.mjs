// Concept-vs-production inventory for the native Tags & categories screen (concept renderTags, app.js:719-729).
// pageInventoryOf runs in the page; dialogs reuse the editor's inventoryOf. The concept is never rewritten:
// mapVocabularyConcept / mapVocabularyDialog apply only the recorded deviations, each named with its ruling.
import {diffInventory} from './library-editor-canonical-comparison.mjs';
export {inventoryOf, diffInventory} from './library-editor-canonical-comparison.mjs';

export function pageInventoryOf(main) {
    const css=(node,...keys)=>{const style=getComputedStyle(node);return Object.fromEntries(keys.map(key=>[key,style[key]]));};
    const px=value=>Math.round(value);
    const visible=node=>node.getClientRects().length>0;
    const font=node=>css(node,'fontFamily','fontSize','fontWeight','lineHeight','letterSpacing','color','marginTop','marginBottom');
    const edges=node=>css(node,'paddingTop','paddingRight','paddingBottom','paddingLeft','borderTopWidth','borderTopStyle','borderTopColor',
        'borderBottomWidth','borderBottomStyle','borderBottomColor','borderTopLeftRadius','backgroundColor','color');
    const buttonOf=node=>({text:node.textContent.trim(),type:node.type,primary:node.classList.contains('ca-primary'),danger:node.classList.contains('danger'),
        ...css(node,'minHeight','fontSize','fontWeight','color','backgroundColor','borderTopColor','borderTopLeftRadius','paddingLeft','paddingRight'),
        height:px(node.getBoundingClientRect().height)});
    // Visible text of a cell, without visually hidden helper text (recorded separately).
    const shownText=node=>[...node.childNodes].filter(child=>!(child.classList?.contains('ca-visually-hidden'))).map(child=>child.textContent).join('').trim();

    // Rendered line count of a cell's text, so a wrapped short name is reported even when the row height is unchanged.
    const lines=node=>{const range=document.createRange();range.selectNodeContents(node);return new Set([...range.getClientRects()].map(rect=>px(rect.top))).size;};
    const heading=main.querySelector(':scope > .heading');
    const copy=heading.firstElementChild;
    const h1=copy.querySelector('h1'),subtitle=copy.querySelector('p'),actions=heading.querySelector('.heading-actions');
    const grid=main.querySelector(':scope > .two-col'),gridBox=grid.getBoundingClientRect();
    const surfaces=[...grid.querySelectorAll(':scope > section.surface')];
    const [tagSurface,categorySurface]=surfaces;
    const wrap=tagSurface.querySelector('.table-wrap'),table=wrap.querySelector('table'),caption=table.querySelector('caption');
    const rows=[...table.querySelectorAll('tbody tr')];
    const firstCell=rows[0]?.querySelector('td');
    const operations=[...categorySurface.querySelectorAll('.operation')].filter(visible);
    const operationOf=row=>{const h3=row.querySelector('h3'),p=row.querySelector('p'),rowActions=row.querySelector('.row-actions');return {
            ...css(row,'display','flexDirection','justifyContent','alignItems','columnGap','paddingTop','paddingBottom','borderTopWidth','borderTopStyle','borderTopColor'),
            height:px(row.getBoundingClientRect().height),h3:{text:h3.textContent,...font(h3)},p:{text:p.textContent,...font(p)},
            actions:{...css(rowActions,'flexShrink','flexWrap','columnGap'),buttons:[...rowActions.querySelectorAll('button')].map(buttonOf)}};};
    // D149 (#444): production adds the Untagged prompts row and the tag search and sort above the table; the concept has
    // neither. The toolbar is compared on its own (toolbarInventoryOf) with the concept's page toolbar as donor.
    const untaggedRow=tagSurface.querySelector(':scope > .operation');
    const d149=untaggedRow?{untagged:operationOf(untaggedRow),
        // It starts where the concept's table starts: right after the paragraph.
        offset:px(untaggedRow.getBoundingClientRect().top-tagSurface.getBoundingClientRect().top),
        // The vertical space the two additions take: the table starts this much lower than after the paragraph alone.
        // (Both offsets rounded first, so offset + shift is exactly the table's rounded offset.)
        shift:px(wrap.getBoundingClientRect().top-tagSurface.getBoundingClientRect().top)-px(untaggedRow.getBoundingClientRect().top-tagSurface.getBoundingClientRect().top)}:null;
    return {
        heading:{...css(heading,'display','flexDirection','justifyContent','alignItems','columnGap','rowGap','marginBottom'),
            h1:{text:h1.textContent,tabIndex:h1.tabIndex,...font(h1)},
            subtitle:{text:subtitle.textContent,...font(subtitle)},
            actions:{...css(actions,'display','columnGap','flexWrap'),fullWidth:px(actions.getBoundingClientRect().width)===px(heading.getBoundingClientRect().width),
                buttons:[...actions.querySelectorAll('button')].map(buttonOf)},
            height:px(heading.getBoundingClientRect().height)},
        twoCol:{columns:getComputedStyle(grid).gridTemplateColumns.split(' ').length,...css(grid,'display','columnGap','rowGap'),
            stacked:surfaces.length>1&&surfaces[1].getBoundingClientRect().top>=surfaces[0].getBoundingClientRect().bottom,
            width:px(gridBox.width)},
        surfaces:surfaces.map(surface=>({...edges(surface),...css(surface,'marginTop'),width:px(surface.getBoundingClientRect().width),
            h2:{text:surface.querySelector('h2').textContent,...font(surface.querySelector('h2')),offset:px(surface.querySelector('h2').getBoundingClientRect().top-surface.getBoundingClientRect().top)},
            paragraphs:[...surface.querySelectorAll(':scope > p')].filter(visible).map(node=>({text:node.textContent,...font(node),
                offset:px(node.getBoundingClientRect().top-surface.getBoundingClientRect().top)}))})),
        table:{wrap:{...css(wrap,'overflowX','marginLeft','marginRight'),offset:px(wrap.getBoundingClientRect().top-tagSurface.getBoundingClientRect().top)},
            ...css(table,'borderCollapse','fontSize','color'),fullWidth:px(table.getBoundingClientRect().width)>=px(wrap.clientWidth),
            caption:{text:caption.textContent,...font(caption),...css(caption,'textAlign','captionSide')},
            headers:[...table.querySelectorAll('thead th')].map(cell=>({text:cell.textContent,scope:cell.getAttribute('scope'),...font(cell),...edges(cell),...css(cell,'textAlign','verticalAlign')})),
            firstCell:firstCell?{...font(firstCell),...edges(firstCell),...css(firstCell,'textAlign','verticalAlign')}:null,
            firstRowHeight:rows[0]?px(rows[0].getBoundingClientRect().height):null,
            rowActions:rows[0]?{...css(rows[0].querySelector('.row-actions'),'display','justifyContent','columnGap','flexWrap','alignItems'),
                buttons:[...rows[0].querySelectorAll('.row-actions button')].map(buttonOf)}:null,
            rows:rows.map(row=>{const cells=[...row.querySelectorAll('td')];return {cells:cells.length,height:px(row.getBoundingClientRect().height),tagLines:lines(cells[0]),tag:cells[0].textContent,prompts:cells[1].textContent,
                images:shownText(cells[2]),imagesHidden:cells[2].querySelector('.ca-visually-hidden')?.textContent??null,
                actions:[...cells[3].querySelectorAll('.row-actions button')].map(node=>node.textContent.trim())};})},
        operations:operations.map(operationOf),
        d149,
        noPageScroll:document.documentElement.scrollWidth<=innerWidth+1,
    };
}

/** A `search.toolbar`'s tokens (D149): runs in the page on the concept's page toolbar and on the tag search and sort. */
export function toolbarInventoryOf(root) {
    const css=(node,...keys)=>{const style=getComputedStyle(node);return Object.fromEntries(keys.map(key=>[key,style[key]]));};
    const px=value=>Math.round(value);
    const field=root.querySelector('.search-field'),input=field.querySelector('input'),select=root.querySelector('select');
    return {...css(root,'display','columnGap','rowGap','flexWrap','alignItems','marginBottom'),
        field:{...css(field,'display','alignItems','columnGap','flexGrow','minWidth','paddingLeft','paddingRight','borderTopWidth','borderTopStyle',
            'borderTopColor','borderTopLeftRadius','backgroundColor'),icon:Boolean(field.querySelector(':scope > svg')),height:px(field.getBoundingClientRect().height)},
        input:{type:input.type,...css(input,'fontSize','minHeight','backgroundColor','borderTopWidth','color')},
        select:{...css(select,'fontSize','minHeight','backgroundColor','color','borderTopColor','borderTopLeftRadius','paddingLeft'),
            height:px(select.getBoundingClientRect().height)}};
}


const countOf=(count,noun)=>`${count} ${noun}${count===1?'':'s'}`;

/**
 * D143 (#332): a category row counts the images whose own category it is: "N positive prompts · M images" when
 * prompts hold it too, "M images" for a value only images hold. Image-only rows follow every prompt-backed row, before
 * the closing Manage category row, each drawn as the concept's last category row with its own text (vocabulary-visual
 * checks this against the concept page with those rows added at runtime). `images` is `{value: count}`; `only` lists
 * the image-only values in their order.
 */
export function withImageCounts(operations,{images={},only=[]}={}) {
    const rows=operations.map(row=>images[row.h3.text]&&!only.includes(row.h3.text)
        ?{...row,p:{...row.p,text:`${row.p.text} · ${countOf(images[row.h3.text],'image')}`}}:row);
    const manage=rows.at(-1),last=rows.at(-2);
    const added=only.map(value=>({...last,h3:{...last.h3,text:value},p:{...last.p,text:countOf(images[value],'image')}}));
    return [...rows.slice(0,-1),...added,manage];
}

// Recorded deviations only (plan "Mock inventory" + decisions D1-D15 + ledger rulings). Everything else must be equal.
// `imageCategories` (#332 D143) is the fixture's own image categories, none by default.
export function mapVocabularyConcept(reference,imageCategories={},{untaggedText=null,shift=0}={}) {
    const operations=reference.operations.map(row=>({...row,p:{...row.p,
        // Ledger ruling: "1 positive prompts" -> singular, consistent with the shared plural helper (#238/#243).
        text:row.p.text.replace(/^1 positive prompts$/,'1 positive prompt')}}));
    // D149 (#444): the Untagged prompts row is drawn as the concept's first category row with its own text and a Browse
    // action, and the table moves down by the space it and the tag search and sort take.
    const first=reference.operations[0];
    const d149=untaggedText===null?null:{untagged:{...first,h3:{...first.h3,text:'Untagged prompts'},p:{...first.p,text:untaggedText}},
        offset:reference.table.wrap.offset,shift};
    return {...reference,d149,
        table:{...reference.table,wrap:{...reference.table.wrap,offset:reference.table.wrap.offset+shift},rows:reference.table.rows.map(row=>({...row,
            // Data, not a deviation: the concept tags each sample image with its prompt's tags; the vocabulary fixture
            // tags no image, so every Images count is 0 (#249 C2 retired #245 D3).
            images:'0'}))},
        operations:withImageCounts(operations,imageCategories),
    };
}

/** The D149 mapping inputs: the fixture's Untagged prompts count text (none by default) and production's table shift. */
export function d149Of(actual,untaggedText='0 positive prompts') {
    return actual.d149?{untaggedText,shift:actual.d149.shift}:{};
}

/** The page comparison: concept rows are the concept's sample tags; production appends fixture-only tags (D4). */
export function compareVocabularyPage(reference,actual,imageCategories={}) {
    const expected=mapVocabularyConcept(reference,imageCategories,d149Of(actual));
    const shared=expected.table.rows.length;
    return {diff:diffInventory(expected,{...actual,table:{...actual.table,rows:actual.table.rows.slice(0,shared)}}),
        extra:actual.table.rows.slice(shared).map(row=>row.tag)};
}

// Concept dialogs are simpleInput (app.js:1138-1143). donors are concept inventories for controls the concept
// shows elsewhere: a `.field select` and a `p.small` (Filters dialog, app.js:1351) and a danger dialog action
// (Archive… in the editor, app.js:976), so added controls are held to concept tokens, never to production values.
// `preview` is the Manage categories sentence once Preview has been pressed (D9; D144, #332, adds its images).
export function mapVocabularyDialog(mode,reference,{filters,editor,categories,preview=null}) {
    const fields=reference.fields.map(item=>({...item,
        // Inventory rows 27-29: the name is `required` (blank Apply is blocked natively).
        required:true}));
    const buttons=reference.actions.buttons.map(item=>item.text==='Apply in prototype'
        // Inventory row 26: "Apply in prototype" is Apply; it submits the form so `required` is enforced natively.
        ?{...item,text:'Apply',type:'submit'}:item);
    if(mode==='rename') {
        // D14 (and ledger ruling "Delete inside Rename/merge"): danger `Delete tag…` first in the actions, concept danger tokens.
        const danger=editor.actions.buttons.find(item=>item.danger);
        return {...reference,fields,actions:{...reference.actions,buttons:[{...danger,text:'Delete tag…',type:'button'},...buttons]}};
    }
    if(mode==='manage') {
        const select=filters.fields.find(item=>item.tag==='select');
        return {...reference,
            fields:[
                // D9: a `Category` source select first, listing the stored categories (Manage loads every page, ruling: up to 1,000).
                {...select,label:'Category',required:true,options:categories.map(value=>[value,value])},
                // D9 / Task 8: the proposed name offers a category datalist (no trailing commas: one value).
                ...fields.map(item=>({...item,listed:true,commaOptions:false}))],
            // D9: the preview sentence is a `p.small`, empty and hidden until Preview.
            small:[{...filters.small[0],text:preview??''}],
            // D9: the primary is `Preview` until a preview is shown, then `Apply`.
            actions:{...reference.actions,buttons:preview===null?buttons.map(item=>item.text==='Apply'?{...item,text:'Preview'}:item):buttons}};
    }
    return {...reference,fields,actions:{...reference.actions,buttons}};
}
