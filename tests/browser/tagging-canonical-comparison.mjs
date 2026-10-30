// Concept-vs-production inventory of the Tag image dialog (plan Mock inventory M2: docs/concepts/workspace/app.js
// tagReview a1037–1061, renderTagModelOptions a1062–1076, generateTags a1077–1135; style.css s1598–1694) and of the
// card's Info and Tag group (M1: app.js mediaCard a398, style.css s1589–1597). The *Of functions run in the page.
// The concept is never rewritten: mapConceptTagging applies only the recorded deviations, each named with its D-number
// or ruling; everything else must be equal.
export {diffInventory as diffTaggingInventory} from './library-editor-canonical-comparison.mjs';

/** The open Tag image dialog (`dialog#modal`), its controls in order with their concept tokens. */
export function taggingInventory(dialog) {
    const css=(node,...keys)=>{const style=getComputedStyle(node);return Object.fromEntries(keys.map(key=>[key,style[key]]));};
    const px=value=>Math.round(value*2)/2;
    const box=node=>{const rect=node.getBoundingClientRect();return {width:px(rect.width),height:px(rect.height)};};
    const font=node=>css(node,'fontSize','fontWeight','lineHeight','color','marginTop','marginBottom');
    const edges=node=>css(node,'paddingTop','paddingRight','paddingBottom','paddingLeft','borderTopWidth','borderTopStyle','borderTopColor',
        'borderTopLeftRadius','backgroundColor');
    const buttonOf=node=>({text:node.textContent.trim(),label:node.getAttribute('aria-label'),primary:node.classList.contains('ca-primary'),
        icon:node.querySelector('svg')!==null,disabled:node.getAttribute('aria-disabled')==='true'||node.disabled,
        ...css(node,'minHeight','fontSize','color','backgroundColor','borderTopColor','borderTopLeftRadius')});
    const controlOf=node=>({tag:node.tagName.toLowerCase(),id:node.id,type:node.type??null,value:node.value,
        min:node.getAttribute('min'),max:node.getAttribute('max'),step:node.getAttribute('step'),
        describedBy:node.getAttribute('aria-describedby'),
        options:node.tagName==='SELECT'?[...node.options].map(option=>[option.value,option.textContent]):null,
        ...css(node,'fontSize','minHeight','backgroundColor','color','borderTopColor','borderTopLeftRadius','paddingLeft')});
    const fieldOf=field=>{const label=field.querySelector('label'),control=field.querySelector('input,textarea,select');
        return {label:label.textContent,labelled:label.htmlFor===control.id,...css(field,'display','rowGap','marginTop','marginBottom'),control:controlOf(control)};};
    const header=dialog.querySelector('.dialog-header'),title=header.querySelector('h2'),close=header.querySelector('button');
    const body=dialog.querySelector('#modal-body');
    const heading=body.querySelector('.tag-image-heading');
    const preview=heading.querySelector('.tag-image-preview'),copy=heading.querySelector(':scope > div');
    const sizeChip=copy.querySelector('.chip');
    const note=body.querySelector('.tag-scope-note');
    const manual=body.querySelector('#manual-image-tags'),manualField=manual.closest('.field'),help=body.querySelector('#manual-tags-help');
    const panel=body.querySelector('.tag-model-panel'),options=panel.querySelector('#image-tag-model-options');
    const generate=[...panel.querySelectorAll('button')].at(-1);
    const suggestions=body.querySelector('section.tag-suggestion-panel');
    const list=suggestions.querySelector('#tag-suggestions'),status=suggestions.querySelector('#tag-generation-status');
    const count=suggestions.querySelector('#suggestion-count');
    const actions=body.querySelector('.dialog-actions');
    // A suggestion is a concept checkbox row (`label.tag-suggestion`) or a production chip (`span.chip.tag-suggestion`).
    const rows=[...list.querySelectorAll('.tag-suggestion')].map(row=>{
        const name=row.querySelector('span:not(.tag-confidence)'),confidence=row.querySelector('.tag-confidence');
        const checkbox=row.querySelector('input[type=checkbox]'),remove=row.querySelector('button');
        return {name:name.textContent,confidence:confidence.textContent,
            control:checkbox?{kind:'checkbox',checked:checkbox.checked}:remove?{kind:'remove',label:remove.getAttribute('aria-label')}:null,
            ...css(row,'display','alignItems','borderTopColor','backgroundColor','borderTopWidth','borderTopStyle','borderTopLeftRadius','fontSize','paddingTop','paddingLeft'),
            confidenceToken:css(confidence,'color','fontSize')};
    });
    return {
        dialog:{...css(dialog,'paddingTop','paddingRight','paddingBottom','paddingLeft','borderTopLeftRadius','backgroundColor','color','overflowY'),
            width:px(dialog.getBoundingClientRect().width),modal:dialog.matches(':modal'),tagging:dialog.classList.contains('tagging-modal'),
            labelledBy:document.getElementById(dialog.getAttribute('aria-labelledby'))===title,
            noHorizontalOverflow:dialog.scrollWidth<=dialog.clientWidth+1},
        header:{title:{text:title.textContent,...font(title)},close:{label:close.getAttribute('aria-label'),text:close.textContent.trim()}},
        demo:body.querySelector('.demo-note')?.textContent??null,
        order:[...body.children].map(node=>node.className),
        heading:{...css(heading,'display','columnGap','alignItems','marginBottom'),
            preview:{tag:preview.tagName.toLowerCase(),alt:preview.getAttribute('alt'),...box(preview),...css(preview,'objectFit','borderTopLeftRadius','borderTopWidth','borderTopStyle','borderTopColor','flexShrink')},
            h3:{text:copy.querySelector('h3').textContent,...font(copy.querySelector('h3'))},
            filename:{text:copy.querySelector('p.small').textContent,...font(copy.querySelector('p.small'))},
            size:sizeChip?{text:sizeChip.textContent,...css(sizeChip,'marginTop','fontSize','borderTopColor','borderTopLeftRadius','paddingLeft')}:null},
        scopeNote:note?{text:note.textContent,...edges(note),...css(note,'borderLeftWidth','borderLeftStyle','borderLeftColor','fontSize','color')}:null,
        manual:{...fieldOf(manualField),help:{text:help.textContent,...font(help)},described:help.id===manual.getAttribute('aria-describedby')},
        modelPanel:{...edges(panel),...css(panel,'marginTop'),h3:{text:panel.querySelector('h3').textContent,...font(panel.querySelector('h3'))},
            fields:[...panel.querySelectorAll('.field')].map(fieldOf),
            twoCol:options.querySelector('.two-col')?{columns:getComputedStyle(options.querySelector('.two-col')).gridTemplateColumns.split(' ').length,
                ...css(options.querySelector('.two-col'),'display','columnGap')}:null,
            generate:buttonOf(generate)},
        suggestions:{labelled:suggestions.getAttribute('aria-labelledby')===suggestions.querySelector('h3').id,...css(suggestions,'marginTop'),
            title:{text:suggestions.querySelector('h3').textContent,...font(suggestions.querySelector('h3'))},
            count:{text:count.textContent,...font(count)},
            copy:list.querySelector(':scope > p.small')?.textContent??null,
            rows,
            status:{text:status.textContent,role:status.getAttribute('role'),...css(status,'marginTop','fontSize','color')}},
        actions:{...css(actions,'position','bottom','marginLeft','marginRight','marginBottom','paddingTop','paddingLeft','backgroundColor',
            'borderTopWidth','borderTopStyle','borderTopColor','zIndex','display','justifyContent','columnGap'),
            buttons:[...actions.querySelectorAll('button')].map(buttonOf)},
        noPageScroll:document.documentElement.scrollWidth<=innerWidth+1,
    };
}

/** The first image card's Info and Tag group (M1), with its card's filename. */
export function cardActionsInventory(card) {
    const css=(node,...keys)=>{const style=getComputedStyle(node);return Object.fromEntries(keys.map(key=>[key,style[key]]));};
    const info=card.querySelector('.media-info'),actions=info.querySelector(':scope > .image-card-actions');
    return {
        info:css(info,'display','justifyContent','alignItems','columnGap'),
        actions:{...css(actions,'display','alignItems','columnGap','flexShrink','minWidth'),lastInInfo:info.lastElementChild===actions},
        buttons:[...actions.querySelectorAll(':scope > button')].map(node=>{const rect=node.getBoundingClientRect();return {
            className:node.className,label:node.getAttribute('aria-label'),icon:node.querySelector('svg')?.innerHTML.replace(/\s+/g,' ').trim()??null,
            width:Math.round(rect.width),height:Math.round(rect.height),
            ...css(node,'minWidth','minHeight','paddingLeft','paddingTop','borderTopLeftRadius','borderTopColor','backgroundColor','color')};}),
    };
}

// D84 (plan D61): the legacy engines' values, labels and sizes, GGUF JoyCaption in the concept's Ollama slot.
const MODEL_LABELS=Object.freeze({'wd14-swinv2':['wd14-swinv2','WD14 SwinV2 · ~400 MB'],'wd14-vit':['wd14-vit','WD14 ViT · ~400 MB'],
    ollama:['gguf','GGUF JoyCaption (Recommended) · ~4.5 GB'],huggingface:['hf','HuggingFace JoyCaption · ~16 GB']});
const CONCEPT_LABELS=Object.freeze({'WD14 SwinV2 · fast classifier':'WD14 SwinV2 · ~400 MB','WD14 ViT · visual tags':'WD14 ViT · ~400 MB',
    'Ollama · local language model':'GGUF JoyCaption (Recommended) · ~4.5 GB','HuggingFace · language model':'HuggingFace JoyCaption · ~16 GB'});

/**
 * The concept inventory with the recorded deviations applied:
 * - no prototype note (Global Constraints: `Prototype · suggestions are simulated…` is not shipped);
 * - D83 (ruling T4): while suggestions exist, "Remove anything you do not want to add." leads them, and every
 *   suggestion is a chip with a `Remove <name>` × instead of a checked checkbox row: the concept's own `.chip` corner
 *   (`chip`: a concept `.chip` inventory, the heading's size chip) around the row's own text (12px) in the checked
 *   row's colours (accent border, `--soft-blue`);
 * - D84 (plan D61): the legacy engine labels and values, and the server's default instructions (`instructions`);
 * - D91 (plan D68): a Suggestions count field after the model's own fields, on the concept's number field tokens
 *   (`numberField`: the concept's General confidence field) with the configured count (`limit`);
 * - D29: the prototype's "Sample suggestions from …" status reads "Suggestions from …".
 * The opened state is compared unmapped: both show the concept's on-screen "Generate suggestions with the selected
 * model before approving." (its renderTagModelOptions, a1062–1076, replaces the markup's sentence at once).
 */
export function mapConceptTagging(reference,{instructions,limit=10,chip,numberField}) {
    const [select,...own]=reference.modelPanel.fields;
    const suggestionsField={...numberField,label:'Suggestions',control:{...numberField.control,id:'image-suggestion-limit',
        value:String(limit),min:'1',max:'100',step:'1'}};
    const mappedOwn=own.map(field=>field.control.id==='image-tag-instructions'?{...field,control:{...field.control,value:instructions}}:field);
    const rows=reference.suggestions.rows.map(row=>({...row,
        control:row.control?.kind==='checkbox'&&row.control.checked?{kind:'remove',label:`Remove ${row.name}`}:row.control,
        borderTopLeftRadius:chip.borderTopLeftRadius}));
    const copy=rows.length&&reference.suggestions.copy===null?'Remove anything you do not want to add.':reference.suggestions.copy;
    const status=reference.suggestions.status.text.replace(/^Sample suggestions from (.+)\. Approve tags to save\.$/,
        (_,label)=>`Suggestions from ${CONCEPT_LABELS[label]??label}. Approve tags to save.`);
    return {...reference,
        demo:null,
        order:reference.order.filter(name=>name!=='demo-note'),
        modelPanel:{...reference.modelPanel,
            fields:[{...select,control:{...select.control,value:MODEL_LABELS[select.control.value]?.[0]??select.control.value,
                options:select.control.options.map(([value])=>MODEL_LABELS[value])}},...mappedOwn,suggestionsField]},
        suggestions:{...reference.suggestions,copy,rows,status:{...reference.suggestions.status,text:status}},
    };
}

/**
 * Production's own chip geometry beyond the concept's `.chip` (D83): the chip's padding hugs its × button. Only the
 * leaves D83 replaces are dropped from both sides; names, confidences, colours, radius and font stay compared.
 */
export function withoutChipGeometry(inventory) {
    return {...inventory,suggestions:{...inventory.suggestions,rows:inventory.suggestions.rows.map(({paddingTop,paddingLeft,alignItems,...row})=>row)}};
}

/**
 * Needs review (plan Task F3, Mock inventory M4: app.js renderReview a749–760, style.css s407–415, s796–814 and their
 * breakpoints): the page's main element as a tree, runs in the page. Every shown box below `main` to `depth` levels is
 * recorded with its tag and classes, its box relative to its parent, its fonts, edges and layout, a leaf's text, a
 * button's label and icon, a select's options; a `display: contents` node is replaced by its children (D20). A flex
 * row of actions also records the gaps between its children and its right inset, so a row whose button copy differs
 * (D29) still compares by order, spacing and alignment.
 */
export function reviewInventory(selector) {
    const main=document.querySelector(selector);
    // Half pixels: the concept's 13.5px line heights add up exactly, so a box that loses one line moves by 13.5px.
    const px=value=>Math.round(value*2)/2;
    const css=(node,...keys)=>{const style=getComputedStyle(node);return Object.fromEntries(keys.map(key=>[key,style[key]]));};
    const rect=node=>node.getBoundingClientRect();
    const shown=node=>node.getClientRects().length>0&&getComputedStyle(node).visibility!=='hidden';
    const relative=(node,origin)=>{const a=rect(node),b=rect(origin);return {left:px(a.left-b.left),top:px(a.top-b.top),width:px(a.width),height:px(a.height)};};
    const font=node=>css(node,'fontFamily','fontSize','fontWeight','lineHeight','letterSpacing','color','textTransform','textAlign');
    const edges=node=>css(node,'paddingTop','paddingRight','paddingBottom','paddingLeft','marginTop','marginRight','marginBottom','marginLeft',
        'borderTopWidth','borderTopStyle','borderTopColor','borderRightWidth','borderRightStyle','borderRightColor','borderBottomWidth',
        'borderBottomStyle','borderBottomColor','borderLeftWidth','borderLeftStyle','borderLeftColor','borderTopLeftRadius','backgroundColor',
        'boxShadow','outlineStyle');
    const layout=node=>css(node,'display','position','flexDirection','flexWrap','justifyContent','alignItems','columnGap','rowGap',
        'gridTemplateColumns','overflowX','overflowY','objectFit','minHeight','minWidth');
    const tagOf=node=>`${node.localName}${[...node.classList].sort().map(name=>`.${name}`).join('')}`;
    const textOf=node=>node.textContent.replace(/\s+/g,' ').trim();
    const boxed=node=>[...node.children].flatMap(child=>getComputedStyle(child).display==='contents'?boxed(child):[child]);
    const LEAVES=new Set(['button','select','input','img','svg','textarea','progress']);
    function nodeOf(node,origin,depth) {
        const base={tag:tagOf(node),...relative(node,origin),...font(node),...edges(node),...layout(node)};
        const name=node.localName;
        if(name==='svg')return {tag:base.tag,width:base.width,height:base.height,d:node.querySelector('path')?.getAttribute('d')??null,
            ...css(node,'strokeWidth','color','verticalAlign')};
        if(name==='button')return {...base,text:textOf(node),label:node.getAttribute('aria-label'),pressed:node.getAttribute('aria-pressed'),
            disabled:node.getAttribute('aria-disabled')==='true'||node.disabled,primary:node.classList.contains('ca-primary'),
            children:boxed(node).filter(shown).map(child=>nodeOf(child,node,0))};
        if(name==='select')return {...base,id:node.id,options:[...node.options].map(option=>[option.textContent,option.disabled])};
        if(name==='input')return {...base,id:node.id,type:node.type,label:node.getAttribute('aria-label')};
        if(name==='img')return {...base,alt:node.getAttribute('alt')};
        const children=boxed(node).filter(shown);
        const item={...base,id:node.id||null,role:node.getAttribute('role'),label:node.getAttribute('aria-label'),
            text:children.length?null:textOf(node),htmlFor:node.htmlFor||null};
        if(/(^|\s)(dialog-actions|row-actions|image-card-actions)(\s|$)/.test(node.className)&&children.length) {
            const boxes=children.map(rect);
            item.gaps=boxes.slice(1).map((box,index)=>px(box.left-boxes[index].right));
            item.rightInset=px(rect(node).right-boxes.at(-1).right);
        }
        if(depth>0&&!LEAVES.has(name))item.children=children.map(child=>nodeOf(child,node,depth-1));
        return item;
    }
    return {main:nodeOf(main,main,8),noPageScroll:document.documentElement.scrollWidth<=innerWidth+1};
}

/**
 * `tree` with `edit(node)` applied to every node `match` picks (a tag prefix or a predicate), depth first, new objects
 * only.
 */
function editNodes(tree,match,edit) {
    const children=tree.children?.map(child=>editNodes(child,match,edit));
    const node=children?{...tree,children}:tree;
    const picked=typeof match==='function'?match(node):node.tag?.startsWith(match);
    return picked?edit(node):node;
}
const findNode=(tree,prefix)=>tree.tag?.startsWith(prefix)?tree:tree.children?.map(child=>findNode(child,prefix)).find(Boolean)??null;

/**
 * The concept's Metadata review inventory (reviewInventory) with the recorded deviations applied, the remaining sample
 * data taken from production (`actual`) only where the concept's own copy cannot say it:
 * - D29: no "Sample review queue" chip in the notice; "Resolve the association" and "Apply association" without the
 *   prototype's "sample";
 * - D119: queue cards carry no selection checkbox and no star (the overlay row stays, empty); a card's title is its
 *   file name (an undecided item names no prompt) and its line is the extension only (D11 omits the unknown size and
 *   seed, which a queue item does not carry); the image's alt is the file name; the prompt blocks' Copy and Send names
 *   carry no prompt title;
 * - D3: the grid keeps the Images grid's `position: relative` (its cards stay in its flow);
 * - production's hooks and names: the `review-queue` / `review-panel` classes, the queue's list roles and name, the
 *   panel heading's id (the region's name) and the prompt sections' region names;
 * - sample data: the Sampler context's candidate options (the file's own evidence; the last option, Leave unresolved,
 *   stays compared) and the widths that follow from copy the deviations above change (a card's text column, the
 *   panel's action buttons; their gaps and right inset stay compared).
 * Where that copy changes the height of a box, the boxes after it move by exactly that much: the stacked notice (390)
 * loses the chip and its row gap; a card whose concept line wraps (390: "1536 × 1152 · PNG · Seed 42001") takes
 * production's one-line height, its row of the grid shrinks by the most any of its cards lost, and in one column the
 * panel below the grid moves up by the grid's loss. The actions keep production's centred place in the card's info.
 */
export function mapConceptReview(reference,actual) {
    const named=(node,id)=>({...node,id});
    const actualCards=findNode(actual.main,'div.image-grid')?.children??[];
    const actualActions=findNode(actual.main,'div.dialog-actions');
    let cardIndex=0;
    let main={...reference.main,id:actual.main.id};
    // D188: the Resolve panel is sticky.
    const actualPanel=findNode(actual.main,'div.gallery-layout')?.children[1];
    if(actualPanel)main=editNodes(main,'div.gallery-layout',layout=>({...layout,children:layout.children.map((child,index)=>index===1?{...child,position:actualPanel.position}:child)}));
    let noticeLoss=0;
    main=editNodes(main,'div.notice',notice=>{
        const chip=notice.children.find(child=>child.tag==='span.chip');
        if(chip&&notice.flexDirection==='column')noticeLoss=chip.height+parseFloat(notice.rowGap);
        return {...notice,height:notice.height-noticeLoss,children:notice.children.filter(child=>child!==chip)};
    });
    const noticeIndex=main.children.findIndex(child=>child.tag==='div.notice');
    main={...main,height:main.height-noticeLoss,children:main.children.map((child,index)=>index>noticeIndex?{...child,top:child.top-noticeLoss}:child)};
    const losses=[];
    main=editNodes(main,'div.image-grid',grid=>({...grid,tag:'div.image-grid.review-queue',position:'relative',role:'list',label:'Review queue'}));
    main=editNodes(main,'article.media-card',card=>{
        const twin=actualCards[cardIndex++];
        const filename=findNode(card,'button.image-open').label.replace(/^Open /,'');
        const art=editNodes(card.children[0],'div.image-overlays',overlays=>({...overlays,height:0,text:'',children:[]}));
        const withAlt=editNodes(art,'img',img=>({...img,alt:filename}));
        const info=card.children[1];
        const [copy,...rest]=info.children;
        const twinCopy=twin?.children[1]?.children[0];
        const [h3,p]=copy.children;
        const twinLine=twinCopy?.children[1];
        const loss=twinLine?p.height-twinLine.height:0;
        losses.push({top:card.top,loss});
        const mappedCopy={...copy,width:twinCopy?.width,height:copy.height-loss,children:[{...h3,text:filename,width:twinCopy?.children[0]?.width},
            {...p,text:'PNG',width:twinLine?.width,height:p.height-loss}]};
        const [actions,...after]=rest;
        const twinActions=twin?.children[1]?.children[1];
        const mappedActions=actions&&loss?{...actions,top:twinActions?.top}:actions;
        return {...card,role:'listitem',height:card.height-loss,
            children:[withAlt,{...info,height:info.height-loss,children:[mappedCopy,...(actions?[mappedActions]:[]),...after]},...card.children.slice(2)]};
    });
    // Each grid row loses what its most shortened card lost; later rows move up by the rows before them.
    const rowLoss=new Map();
    for(const {top,loss} of losses)rowLoss.set(top,Math.max(rowLoss.get(top)??0,loss));
    const lossBefore=top=>[...rowLoss].filter(([rowTop])=>rowTop<top).reduce((sum,[,loss])=>sum+loss,0);
    const gridLoss=[...rowLoss.values()].reduce((sum,loss)=>sum+loss,0);
    main=editNodes(main,'div.image-grid',grid=>({...grid,height:grid.height-gridLoss,
        children:grid.children.map(card=>({...card,top:card.top-lossBefore(card.top)}))}));
    main=editNodes(main,'div.gallery-layout',layout=>{
        if(!gridLoss||layout.gridTemplateColumns.trim().split(/\s+/).length>1)return layout;
        return {...layout,height:layout.height-gridLoss,children:layout.children.map((child,index)=>index?{...child,top:child.top-gridLoss}:child)};
    });
    if(gridLoss&&findNode(main,'div.gallery-layout').gridTemplateColumns.trim().split(/\s+/).length===1)main={...main,height:main.height-gridLoss};
    main=editNodes(main,'section.surface',surface=>{
        const [h2,...rest]=surface.children;
        return {...surface,tag:'section.review-panel.surface',children:[named({...h2,text:h2.text.replace('Resolve the sample association','Resolve the association')},'review-panel-title'),...rest]};
    });
    main=editNodes(main,'select',select=>{
        const twin=findNode(actual.main,'select');
        return select.id==='review-context'?{...select,options:[...twin.options.slice(0,-1),select.options.at(-1)]}:select;
    });
    main=editNodes(main,node=>/^section\..*\bprompt-section\b/.test(node.tag),section=>({...section,label:section.tag.includes('positive')?'Positive prompt':'Negative prompt'}));
    main=editNodes(main,node=>node.tag?.startsWith('button')&&/^(Copy|Send) (positive|negative) prompt/.test(node.label??''),
        button=>({...button,label:button.label.replace(/: .+$/,'')}));
    main=editNodes(main,'div.dialog-actions',actions=>({...actions,children:actions.children.map((button,index)=>({...button,
        text:button.text.replace('Apply sample association','Apply association'),
        left:actualActions?.children[index]?.left,width:actualActions?.children[index]?.width}))}));
    return {...reference,main};
}
