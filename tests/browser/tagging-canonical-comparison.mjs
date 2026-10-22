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
 * - D83 (ruling T4): "Remove anything you do not want to add." and every suggestion a chip with a `Remove <name>` ×
 *   instead of a checked checkbox row: the concept's own `.chip` corner (`chip`: a concept `.chip` inventory, the
 *   heading's size chip) around the row's own text (12px) in the checked row's colours (accent border, `--soft-blue`);
 * - D84 (plan D61): the legacy engine labels and values, and the server's default instructions (`instructions`);
 * - D91 (plan D68): a Suggestions count field after the model's own fields, on the concept's number field tokens
 *   (`numberField`: the concept's General confidence field) with the configured count (`limit`);
 * - D29: the prototype's "Sample suggestions from …" status reads "Suggestions from …";
 * - D83, `opened`: on open the concept's renderTagModelOptions (a1062–1076) replaces its own initial sentence with the
 *   model-changed one at once; production shows the initial sentence of ruling T4 until a model change.
 */
export function mapConceptTagging(reference,{instructions,limit=10,chip,numberField,opened=false}) {
    const [select,...own]=reference.modelPanel.fields;
    const suggestionsField={...numberField,label:'Suggestions',control:{...numberField.control,id:'image-suggestion-limit',
        value:String(limit),min:'1',max:'100',step:'1'}};
    const mappedOwn=own.map(field=>field.control.id==='image-tag-instructions'?{...field,control:{...field.control,value:instructions}}:field);
    const rows=reference.suggestions.rows.map(row=>({...row,
        control:row.control?.kind==='checkbox'&&row.control.checked?{kind:'remove',label:`Remove ${row.name}`}:row.control,
        borderTopLeftRadius:chip.borderTopLeftRadius}));
    const initial='Choose a model, then generate tags. Remove anything you do not want to add.';
    const copy=opened&&reference.suggestions.copy==='Generate suggestions with the selected model before approving.'?initial:
        reference.suggestions.copy?.replace('Uncheck anything you do not want to add.','Remove anything you do not want to add.')??null;
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
