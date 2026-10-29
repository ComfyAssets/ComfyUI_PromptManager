// Concept-vs-production control inventory for the native editor dialog. inventoryOf runs in the page.
export function inventoryOf(dialog) {
    const css=(node,...keys)=>{const style=getComputedStyle(node);return Object.fromEntries(keys.map(key=>[key,style[key]]));};
    const header=dialog.querySelector('.dialog-header'),title=header.querySelector('h2'),close=header.querySelector('button');
    const actions=dialog.querySelector('.dialog-actions'),twoCol=dialog.querySelector('.two-col');
    return {
        dialog:{...css(dialog,'paddingTop','paddingRight','paddingBottom','paddingLeft','borderTopLeftRadius','borderTopWidth','borderTopStyle','maxHeight','overflowY','backgroundColor','color','borderTopColor','fontFamily','fontSize','lineHeight'),
            width:dialog.getBoundingClientRect().width,modal:dialog.matches(':modal'),backdrop:getComputedStyle(dialog,'::backdrop').backgroundColor,
            labelledBy:document.getElementById(dialog.getAttribute('aria-labelledby'))===title},
        header:css(header,'display','justifyContent','alignItems','columnGap','marginBottom'),
        title:{text:title.textContent,...css(title,'fontSize','fontWeight','marginTop','marginBottom','lineHeight')},
        close:{label:close.getAttribute('aria-label'),text:close.textContent.trim(),focused:document.activeElement===close,...css(close,'fontSize','minWidth','minHeight')},
        fields:[...dialog.querySelectorAll('.field')].map(field=>{
            const label=field.querySelector('label'),control=field.querySelector('input,textarea,select');
            return {label:label.textContent,labelled:label.htmlFor===control.id,tag:control.tagName.toLowerCase(),readOnly:Boolean(control.readOnly),required:control.required,
                placeholder:control.getAttribute('placeholder'),listed:Boolean(control.list),commaOptions:control.list?[...control.list.options].every(option=>option.value.endsWith(',')):null,
                options:control.tagName==='SELECT'?[...control.options].map(option=>[option.value,option.textContent]):null,
                field:css(field,'display','rowGap','fontSize','marginTop','marginBottom'),control:{...css(control,'fontSize','minHeight','resize','backgroundColor','color','borderTopColor','borderTopLeftRadius','paddingLeft'),height:control.getBoundingClientRect().height}};
        }),
        twoCol:twoCol?{columns:getComputedStyle(twoCol).gridTemplateColumns.split(' ').length,...css(twoCol,'display','columnGap')}:null,
        small:[...dialog.querySelectorAll('p.small')].map(node=>({text:node.textContent,...css(node,'fontSize','color')})),
        actions:{...css(actions,'display','justifyContent','columnGap','flexWrap','marginTop'),
            buttons:[...actions.querySelectorAll('button')].map(node=>({text:node.textContent.trim(),type:node.type,danger:node.classList.contains('danger'),primary:node.classList.contains('ca-primary'),
                ...css(node,'minHeight','fontSize','color','borderTopColor','backgroundColor'),height:node.getBoundingClientRect().height}))},
        demo:Boolean(dialog.querySelector('.demo-note')),
    };
}
// Documented differences only: prototype Save label, prototype demo note, data-dependent values, and D153 (#447): the
// concept's "Rating" select is not drawn.
export function mapConcept(reference) {
    return {...reference,demo:false,fields:reference.fields.filter(item=>item.label!=='Rating'),actions:{...reference.actions,buttons:reference.actions.buttons.map(item=>item.text==='Save in prototype'?{...item,text:'Save'}:item)}};
}

// Flatten an inventory to path->value so a comparison reports exactly which leaves differ.
function flatten(value,path,out) {
    if(Array.isArray(value)) {out.set(`${path}.length`,value.length);value.forEach((item,index)=>flatten(item,`${path}.${index}`,out));}
    else if(value&&typeof value==='object')for(const [key,item] of Object.entries(value))flatten(item,path?`${path}.${key}`:key,out);
    else out.set(path,value);
    return out;
}
export function diffInventory(reference,actual) {
    const left=flatten(reference,'',new Map()),right=flatten(actual,'',new Map());
    return [...new Set([...left.keys(),...right.keys()])].filter(path=>left.get(path)!==right.get(path))
        .map(path=>({path,reference:left.get(path),actual:right.get(path)}));
}
