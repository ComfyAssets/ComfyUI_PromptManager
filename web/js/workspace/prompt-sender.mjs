import {createBridgeClient, validateRoles} from '../shared/toolkit/prompt-bridge.js';
import {createButton, createField, createScope, createSelect} from '../shared/toolkit/core.js';

// The same wait as System's renderer line, so a busy tab is not reported as missing.
const DISCOVERY_WAIT_MS = 300;
const roleNames = roles => Object.keys(roles).join(' and ');
const roleOf = target => Array.isArray(target.roles) ? target.roles : [];
const accepts = (target,roles) => typeof target.id==='string' && Object.keys(roles).every(role=>roleOf(target).includes(role));
const linkedTo = (target,roles) => Object.keys(roles).filter(role=>Array.isArray(target.linkedRoles) && target.linkedRoles.includes(role));

const nodeLabel = target => `#${target.id} ${target.label}`;
// A lost reply leaves the outcome unknown; anything else the host said is a refusal, and nothing was written.
const LOST_REPLY = /timed out|aborted|disposed/i;

/** Why nothing in the discovered windows can receive these roles, most specific first (D173). */
function unavailableMessage(found,roles) {
    if (!found.length) return 'No ComfyUI window answered in this browser at this address. Open ComfyUI here, then find windows again.';
    for (const host of found) for (const target of host.targets) {
        const linked = accepts(target,roles) ? linkedTo(target,roles) : [];
        if (linked.length) return `${nodeLabel(target)} in ${host.label} has its ${linked.join(' and ')} prompt linked to another node, so it cannot receive text. Disconnect the link, then find windows again.`;
    }
    const where = found.length===1 ? found[0].label : `None of the ${found.length} ComfyUI windows`;
    const verb = found.length===1 ? 'has no' : 'has a';
    if (found.some(host=>host.targets.length)) {
        const need = Object.hasOwn(roles,'negative') ? ' Add a Prompt Manager (+/-) node, then find windows again.' : ' Add one, then find windows again.';
        return `${where} ${verb} Prompt Manager node that accepts a ${roleNames(roles)} prompt.${need}`;
    }
    return `${where} ${verb} Prompt Manager node. Add one to the workflow, then find windows again.`;
}
/** Keeps same-named windows apart: tabs on one workflow. */
function distinctLabels(items,suffix) {
    const seen = new Map();
    const totals = new Map();
    for (const item of items) totals.set(item.label,(totals.get(item.label) || 0)+1);
    return items.map(item => {
        const count = (seen.get(item.label) || 0)+1;seen.set(item.label,count);
        return totals.get(item.label)>1 ? {...item,label:suffix(item,count)} : item;
    });
}

export function createSendSession({client, roles:rawRoles, onChange}) {
    const roles = Object.freeze(validateRoles(rawRoles));
    let state = {phase:'idle',hosts:[],hostId:'',targetId:'',preview:null,outcomeUnknown:false,message:''};
    let operation, generation = 0, disposed = false;
    const emit = patch => { if (!disposed) { state = Object.freeze({...state,...patch}); onChange(state); } };
    function invalidate() { generation++; operation?.abort(); operation = null; }
    function begin(phase) { invalidate(); operation = new AbortController(); emit({phase,preview:null,outcomeUnknown:false,message:''}); return {signal:operation.signal,version:generation}; }
    const current = request => !disposed && generation === request.version;
    const selected = () => state.hosts.find(host=>host.hostId===state.hostId)?.targets.find(target=>target.id===state.targetId);
    async function fetchPreview(request) {
        const preview=await client.preview(state.hostId,state.targetId,roles,{signal:request.signal});
        if (!current(request)) return null;
        const fields=preview?.fields;
        if(typeof preview?.ticket!=='string' || !preview.ticket || preview.target?.id!==state.targetId || !Array.isArray(fields) || fields.length!==Object.keys(roles).length || new Set(fields.map(field=>field?.role)).size!==fields.length || Array.from(fields).some(field=>!field || !Object.hasOwn(roles,field.role) || typeof field.before!=='string' || field.after!==roles[field.role])) throw Error('ComfyUI returned an unexpected preview. Find the destination and preview again.');
        // Undefined on a tab still running older code, which has nothing to compare.
        const found=selected()?.revision;
        if(found!==undefined && preview.revision!==found) throw Error('The workflow changed since this window was found. Nothing was sent. Find windows again.');
        return preview;
    }
    function hostChoice(host) {
        if (host.targets.length>1) return {hostId:host.hostId,targetId:'',message:`Choose a destination node in ${host.label}.`};
        const [target] = host.targets;
        return {hostId:host.hostId,targetId:target.id,message:`Sending to ${target.label} in ${host.label}. Apply to send it, or preview the changes first.`};
    }
    return {
        get state() { return state; },
        async discover() {
            if (disposed) return;
            const request = begin('discovering'); emit({hosts:[],hostId:'',targetId:'',message:'Looking for ComfyUI windows…'});
            try {
                const found = (await client.discover({signal:request.signal,waitMs:DISCOVERY_WAIT_MS})).map(host=>({...host,targets:host.targets.filter(target=>target && typeof target==='object')}));
                if (!current(request)) return;
                const hosts = distinctLabels(found.toSorted((a,b)=>a.hostId<b.hostId?-1:1).map(host=>({...host,targets:host.targets.filter(target=>accepts(target,roles) && !linkedTo(target,roles).length).map(target=>({...target,label:nodeLabel(target)}))})).filter(host=>host.targets.length),(host,count)=>count>1 ? `${host.label} (window ${count})` : host.label);
                // Nodes left out are counted so an auto-selected destination never hides the one the user meant.
                const skipped = found.reduce((total,host)=>total+host.targets.length,0)-hosts.reduce((total,host)=>total+host.targets.length,0);
                const note = skipped ? ` ${skipped} other Prompt Manager ${skipped===1?'node':'nodes'} cannot receive this prompt.` : '';
                if (!hosts.length) emit({phase:'select',hosts,message:unavailableMessage(found,roles)});
                else if (hosts.length>1) emit({phase:'select',hosts,message:`Choose a ComfyUI window.${note}`});
                else { const choice=hostChoice(hosts[0]); emit({phase:'select',hosts,...choice,message:choice.message+note}); }
            } catch (error) { if (current(request)) emit({phase:'error',message:error.message}); }
        },
        selectHost(hostId) { if (disposed) return; invalidate(); const host=state.hosts.find(host=>host.hostId===hostId); emit({phase:'select',preview:null,outcomeUnknown:false,...(host ? hostChoice(host) : {hostId:'',targetId:'',message:'Choose a ComfyUI window.'})}); },
        selectTarget(targetId) { if (disposed) return; invalidate(); const host=state.hosts.find(host=>host.hostId===state.hostId); const chosen=host?.targets.some(target=>target.id===targetId); emit({phase:'select',outcomeUnknown:false,targetId:chosen?targetId:'',preview:null,message:chosen?'Apply to send it, or preview the changes first.':'Choose a destination node, then preview changes.'}); },
        async preview() {
            if (disposed || !selected() || ['previewing','applying'].includes(state.phase)) return;
            const request=begin('previewing');
            try {
                const preview=await fetchPreview(request);
                if (!preview) return;
                const windowLabel=state.hosts.find(host=>host.hostId===state.hostId)?.label;
                emit({phase:'ready',preview,message:`Sending to ${nodeLabel({id:preview.target.id,label:preview.target.label ?? ''})} in ${windowLabel}. Review the complete changes, then apply.`});
            } catch(error) { if(current(request)) emit({phase:'error',preview:null,message:error.message}); }
        },
        async apply() {
            // Success stays closed: the second click of a double click must not send again.
            if(disposed || !selected() || !['select','ready','error'].includes(state.phase) || state.outcomeUnknown) return;
            const held=state.phase==='ready' ? state.preview : null;
            const targetId=state.targetId; const hostId=state.hostId;
            const request=begin('applying');
            emit({message:'Waiting for ComfyUI confirmation. Closing this dialog does not undo a sent update.'});
            let posted=false;
            try {
                const preview=held ?? await fetchPreview(request);
                if(!preview || !current(request)) return;
                posted=true;
                const acknowledgement=await client.apply(hostId,preview.ticket,{signal:request.signal});
                if(!current(request)) return;
                if(acknowledgement?.targetId!==targetId || !acknowledgement.values || Object.keys(acknowledgement.values).length!==Object.keys(roles).length || Object.keys(roles).some(role=>!Object.hasOwn(acknowledgement.values,role) || acknowledgement.values[role]!==roles[role])) throw Error('ComfyUI returned an unexpected acknowledgement.');
                emit({phase:'success',message:'Sent to ComfyUI.'});
            } catch(error) {
                if(!current(request)) return;
                const unknown=posted && (LOST_REPLY.test(error.message) || /unexpected acknowledgement/.test(error.message));
                emit({phase:'error',preview:null,outcomeUnknown:unknown,message:unknown ? `No confirmation received. The update may have applied. Check ComfyUI, then preview again. ${error.message}` : error.message});
            }
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
        const description=element('p','Choose where to send this prompt. Apply writes it to that node. Preview changes shows the result first, if you want to check it.');
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
            find.disabled=busy;inspect.disabled=busy || !state.targetId;apply.disabled=busy || !state.targetId || state.phase==='success' || state.outcomeUnknown;
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
