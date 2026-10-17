import {createBridgeClient, validateRoles} from '../shared/toolkit/prompt-bridge.js';
import {createButton, createField, createScope, createSelect} from '../shared/toolkit/core.js';

export function createSendSession({client, roles:rawRoles, onChange}) {
    const roles = Object.freeze(validateRoles(rawRoles));
    let state = {phase:'idle',hosts:[],hostId:'',targetId:'',preview:null,message:''};
    let operation, generation = 0, disposed = false;
    const emit = patch => { if (!disposed) { state = Object.freeze({...state,...patch}); onChange(state); } };
    function invalidate() { generation++; operation?.abort(); operation = null; }
    function begin(phase) { invalidate(); operation = new AbortController(); emit({phase,preview:null,message:''}); return {signal:operation.signal,version:generation}; }
    const current = request => !disposed && generation === request.version;
    const selected = () => state.hosts.find(host=>host.hostId===state.hostId)?.targets.find(target=>target.id===state.targetId);
    return {
        get state() { return state; },
        async discover() {
            if (disposed) return;
            const request = begin('discovering'); emit({hosts:[],hostId:'',targetId:'',message:'Looking for ComfyUI windows…'});
            try {
                const found = await client.discover({signal:request.signal});
                if (!current(request)) return;
                const hosts = found.map(host=>({...host,targets:host.targets.filter(target=>typeof target.id==='string' && Array.isArray(target.roles) && Object.keys(roles).every(role=>target.roles.includes(role)))})).filter(host=>host.targets.length);
                emit({phase:'select',hosts,message:hosts.length ? 'Choose a ComfyUI window and destination node.' : 'No compatible ComfyUI windows found. Open a workflow with a compatible Prompt Manager node, then find windows again.'});
            } catch (error) { if (current(request)) emit({phase:'error',message:error.message}); }
        },
        selectHost(hostId) { if (disposed) return; invalidate(); emit({phase:'select',hostId:state.hosts.some(host=>host.hostId===hostId)?hostId:'',targetId:'',preview:null,message:'Choose a destination node, then preview changes.'}); },
        selectTarget(targetId) { if (disposed) return; invalidate(); const host=state.hosts.find(host=>host.hostId===state.hostId); emit({phase:'select',targetId:host?.targets.some(target=>target.id===targetId)?targetId:'',preview:null,message:'Preview changes before applying.'}); },
        async preview() {
            if (disposed || !selected() || ['previewing','applying'].includes(state.phase)) return;
            const request=begin('previewing');
            try {
                const preview=await client.preview(state.hostId,state.targetId,roles,{signal:request.signal});
                if (!current(request)) return;
                const fields=preview?.fields;
                if(typeof preview?.ticket!=='string' || !preview.ticket || preview.target?.id!==state.targetId || !Array.isArray(fields) || fields.length!==Object.keys(roles).length || new Set(fields.map(field=>field?.role)).size!==fields.length || Array.from(fields).some(field=>!field || !Object.hasOwn(roles,field.role) || typeof field.before!=='string' || field.after!==roles[field.role])) throw Error('ComfyUI returned an unexpected preview. Find the destination and preview again.');
                emit({phase:'ready',preview,message:'Review the complete changes, then apply.'});
            } catch(error) { if(current(request)) emit({phase:'error',preview:null,message:error.message}); }
        },
        async apply() {
            if(disposed || state.phase!=='ready' || !state.preview) return;
            const {ticket}=state.preview; const targetId=state.targetId; const hostId=state.hostId;
            const request=begin('applying');
            emit({message:'Waiting for ComfyUI confirmation. Closing this dialog does not undo a sent update.'});
            try {
                const acknowledgement=await client.apply(hostId,ticket,{signal:request.signal});
                if(!current(request)) return;
                if(acknowledgement?.targetId!==targetId || !acknowledgement.values || Object.keys(acknowledgement.values).length!==Object.keys(roles).length || Object.keys(roles).some(role=>!Object.hasOwn(acknowledgement.values,role) || acknowledgement.values[role]!==roles[role])) throw Error('ComfyUI returned an unexpected acknowledgement.');
                emit({phase:'success',message:'Sent to ComfyUI.'});
            } catch(error) { if(current(request)) emit({phase:'error',preview:null,message:`No confirmation received. The update may have applied. Check ComfyUI, then preview again. ${error.message}`}); }
        },
        dispose() { if(disposed) return; disposed=true; invalidate(); client.dispose(); },
    };
}

function element(tag,text,className) {
    const node=document.createElement(tag);
    if(text!==undefined) node.textContent=text;
    if(className) node.className=className;
    return node;
}

export function createSendButton(label='Send to ComfyUI') {
    const button=createButton('', 'prompt-send-button');
    button.setAttribute('aria-label',label);
    const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');
    icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('aria-hidden','true');icon.setAttribute('fill','none');icon.setAttribute('stroke','currentColor');icon.setAttribute('stroke-width','1.8');
    const path=document.createElementNS('http://www.w3.org/2000/svg','path');
    path.setAttribute('d','M21 3 9.5 14.5M21 3l-7 18-4.5-6.5L3 10z');icon.append(path);
    button.append(icon,element('span',label));
    return button;
}

export function createPromptSender({scope,baseUrl=window.ApiClient.resolveUrl('/prompt_manager/'),clientFactory=createBridgeClient}) {
    let active;
    const close = () => active?.dispose();
    scope.cleanup(close);
    return {open(roles,trigger) {
        if(scope.signal.aborted) return;
        close();
        const local=createScope();
        const dialog=element('dialog',undefined,'prompt-sender');
        dialog.setAttribute('aria-label','Send to ComfyUI');
        const header=element('header',undefined,'prompt-sender-header');
        const title=element('h2','Send to ComfyUI');const dismiss=createButton('Close');
        header.append(title,dismiss);
        const description=element('p','Choose where to send this prompt. Nothing changes until you approve the preview.');
        const status=element('p','', 'prompt-sender-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
        const host=createSelect([['','Choose a ComfyUI window']], '');
        const target=createSelect([['','Choose a destination node']], '');
        const controls=element('div',undefined,'prompt-sender-options');
        controls.append(createField('ComfyUI window',host,'prompt-sender-host'),createField('Destination node',target,'prompt-sender-target'));
        const preview=element('section',undefined,'prompt-sender-preview');preview.setAttribute('aria-label','Prompt changes');
        const find=createButton('Find ComfyUI windows');const inspect=createButton('Preview changes');const apply=createButton('Apply to ComfyUI','ca-primary');
        inspect.disabled=true;apply.disabled=true;
        const actions=element('footer',undefined,'prompt-sender-actions');actions.append(find,inspect,apply);
        dialog.append(header,description,controls,status,preview,actions);document.body.append(dialog);
        let session,closed=false;
        function dispose() {
            if(closed) return;closed=true;local.dispose();session?.dispose();
            if(dialog.open) dialog.close();dialog.remove();
            if(active?.dialog===dialog) active=null;
            if(trigger?.isConnected) trigger.focus();
        }
        active={dialog,dispose};
        local.listen(dismiss,'click',dispose);local.listen(dialog,'close',dispose);
        local.listen(host,'change',()=>session?.selectHost(host.value));
        local.listen(target,'change',()=>session?.selectTarget(target.value));
        local.listen(find,'click',()=>session?.discover());
        local.listen(inspect,'click',()=>session?.preview());
        local.listen(apply,'click',()=>session?.apply());
        function options(select,placeholder,entries,value) {
            select.replaceChildren(...[["",placeholder],...entries].map(([value,label])=>{const option=element('option',label);option.value=value;return option;}));select.value=value;
        }
        function textBlock(parent,label,value) {
            parent.append(element('h4',label));const text=element('pre');text.tabIndex=0;text.append(element('code',value === '' ? '(empty)' : value));parent.append(text);
        }
        function render(state) {
            if(closed) return;
            const busy=['discovering','previewing','applying'].includes(state.phase);
            status.textContent=state.message;status.dataset.phase=state.phase;
            status.setAttribute('role',state.phase==='error'?'alert':'status');status.setAttribute('aria-live',state.phase==='error'?'assertive':'polite');
            dialog.setAttribute('aria-busy',String(busy));
            options(host,'Choose a ComfyUI window',state.hosts.map(item=>[item.hostId,item.label]),state.hostId);
            const selected=state.hosts.find(item=>item.hostId===state.hostId);
            options(target,'Choose a destination node',(selected?.targets || []).map(item=>[item.id,item.label]),state.targetId);
            host.disabled=busy;target.disabled=busy || !selected;
            find.disabled=busy;inspect.disabled=busy || !state.targetId;apply.disabled=state.phase!=='ready';
            preview.replaceChildren();
            if(state.preview) {
                preview.append(element('h3','Review changes'));
                for(const field of state.preview.fields || []) {
                    const section=element('section',undefined,`prompt-sender-field prompt-role-${field.role}`);
                    section.append(element('h3',field.role==='positive'?'Positive prompt':'Negative prompt'));
                    textBlock(section,'Before',field.before);textBlock(section,'After',field.after);preview.append(section);
                }
                const composition=state.preview.composition;
                if(composition) {
                    const section=element('section',undefined,'prompt-sender-composition');section.append(element('h3','Positive composition'));
                    textBlock(section,'Prepend (preserved)',composition.prepend || '');textBlock(section,'Append (preserved)',composition.append || '');
                    textBlock(section,'Combined before',composition.before ?? 'Resolved at execution from connected inputs.');
                    textBlock(section,'Combined after',composition.after ?? 'Resolved at execution from connected inputs.');
                    if(composition.prependConnected || composition.appendConnected) section.append(element('p','Connected prepend and append inputs are preserved and resolved when the workflow runs.'));
                    preview.append(section);
                }
            }
        }
        dialog.showModal();
        try {
            const url=new URL(baseUrl,window.location.href);
            if(url.origin!==window.location.origin || !url.pathname.endsWith('/prompt_manager/')) throw Error('Invalid PromptManager deployment origin or path');
            const client=clientFactory({namespace:url.pathname.slice(0,-'prompt_manager/'.length),origin:window.location.origin});
            try { session=createSendSession({client,roles,onChange:render}); } catch(error) {client.dispose();throw error;}
            session.discover();
        } catch(error) {status.textContent=`Prompt sending is unavailable. ${error.message}`;status.dataset.phase='error';status.setAttribute('role','alert');status.setAttribute('aria-live','assertive');find.disabled=true;host.disabled=true;target.disabled=true;}
    }};
}
