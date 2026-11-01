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
const outcomeUnknown = (posted,error) => posted && (LOST_REPLY.test(error.message) || /unexpected acknowledgement/.test(error.message));
const UNKNOWN_ADVICE = 'Check ComfyUI, then preview again.';
/** The Destination node value that sends to every eligible node of the window (#542, D191). Node ids never take it. */
export const ALL_NODES = '*';
// "All" sends in node id order: 2 before 10, and a subgraph id "3:1" after "3".
const byNodeId = (a,b) => a.id.localeCompare(b.id,'en',{numeric:true});
const allMessage = (count,where) => `Sending to all ${count} eligible nodes in ${where}.`;

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
    // `previews` holds the reviewed nodes of "All", in send order; `preview` the one node otherwise.
    let state = {phase:'idle',hosts:[],hostId:'',targetId:'',preview:null,previews:[],outcomeUnknown:false,message:''};
    let operation, generation = 0, disposed = false;
    const emit = patch => { if (!disposed) { state = Object.freeze({...state,...patch}); onChange(state); } };
    function invalidate() { generation++; operation?.abort(); operation = null; }
    function begin(phase) { invalidate(); operation = new AbortController(); emit({phase,preview:null,previews:[],outcomeUnknown:false,message:''}); return {signal:operation.signal,version:generation}; }
    const current = request => !disposed && generation === request.version;
    const chosenHost = () => state.hosts.find(host=>host.hostId===state.hostId);
    /** The nodes the current choice sends to: one, every eligible node of the window for "All", or none. */
    function chosen() {
        const host=chosenHost();
        if (!host) return [];
        if (state.targetId===ALL_NODES) return host.targets.length>1 ? host.targets.toSorted(byNodeId) : [];
        const target=host.targets.find(item=>item.id===state.targetId);
        return target ? [target] : [];
    }
    // checkRevision is off only for a later node of "All", whose revision our own earlier write may have moved.
    async function fetchPreview(request,hostId,target,checkRevision=true) {
        const preview=await client.preview(hostId,target.id,roles,{signal:request.signal});
        if (!current(request)) return null;
        const fields=preview?.fields;
        if(typeof preview?.ticket!=='string' || !preview.ticket || preview.target?.id!==target.id || !Array.isArray(fields) || fields.length!==Object.keys(roles).length || new Set(fields.map(field=>field?.role)).size!==fields.length || Array.from(fields).some(field=>!field || !Object.hasOwn(roles,field.role) || typeof field.before!=='string' || field.after!==roles[field.role])) throw Error('ComfyUI returned an unexpected preview. Find the destination and preview again.');
        // Undefined on a tab still running older code, which has nothing to compare.
        const found=target.revision;
        if(checkRevision && found!==undefined && preview.revision!==found) throw Error('The workflow changed since this window was found. Nothing was sent. Find windows again.');
        return preview;
    }
    function checkAcknowledgement(acknowledgement,targetId) {
        if(acknowledgement?.targetId!==targetId || !acknowledgement.values || Object.keys(acknowledgement.values).length!==Object.keys(roles).length || Object.keys(roles).some(role=>!Object.hasOwn(acknowledgement.values,role) || acknowledgement.values[role]!==roles[role])) throw Error('ComfyUI returned an unexpected acknowledgement.');
    }
    const reviewedText = change => JSON.stringify([change.fields.map(field=>[field.role,field.before]).sort(),change.composition ?? null]);
    const sameChanges = (reviewed,fresh) => reviewedText(reviewed)===reviewedText(fresh);
    /** Q542-2 C: every node of "All" previews before anything is written; the first refusal names its node. */
    async function previewEach(request,hostId,targets) {
        const reviewed=[];
        for (const target of targets) {
            try {
                const preview=await fetchPreview(request,hostId,target);
                if (!preview) return null;
                reviewed.push({target,label:target.label,preview});
            } catch(error) {
                if (!current(request)) return null;
                throw Error(`${target.label}: ${error.message}${/Nothing was sent/.test(error.message) ? '' : ' Nothing was sent.'}`);
            }
        }
        return reviewed;
    }
    /**
     * Sends the reviewed nodes one after another, best effort past refusals. Only the first node of a pass taken in this
     * click keeps its ticket: a held review may have expired at the host (30 s), and our own writes may make later
     * tickets stale, so every other node takes a fresh ticket that must show exactly the reviewed text.
     */
    async function applyEach(request,hostId,reviewed,where,held) {
        const sent=[],refused=[];let written=false;
        for (const [index,{target,preview}] of reviewed.entries()) {
            let posted=false;
            try {
                let ticket=preview.ticket;
                if (index>0 || held) {
                    // Until something was posted the revision is still the discovered one; after that our own write may move it.
                    const fresh=await fetchPreview(request,hostId,target,!written);
                    if (!fresh) return;
                    if (!sameChanges(preview,fresh)) throw Error('Its prompt changed since the preview. Preview again.');
                    ticket=fresh.ticket;
                }
                posted=written=true;
                const acknowledgement=await client.apply(hostId,ticket,{signal:request.signal});
                if (!current(request)) return;
                checkAcknowledgement(acknowledgement,target.id);
                sent.push(target);
            } catch(error) {
                if (!current(request)) return;
                const summary=[`Sent to ${sent.length} of ${reviewed.length} nodes in ${where}.`,...refused];
                if (outcomeUnknown(posted,error)) {
                    const unsent=reviewed.slice(index+1).map(item=>item.label);
                    emit({phase:'error',outcomeUnknown:true,message:[...summary,`No confirmation received from ${target.label}; the update may have applied.`,...(unsent.length ? [`Not sent: ${unsent.join(', ')}.`] : []),UNKNOWN_ADVICE,error.message].join(' ')});
                    return;
                }
                refused.push(`${target.label}: ${error.message}`);
            }
        }
        const message=[`Sent to ${sent.length} of ${reviewed.length} nodes in ${where}.`,...refused].join(' ');
        emit(refused.length ? {phase:'error',message} : {phase:'success',message});
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
        selectHost(hostId) { if (disposed) return; invalidate(); const host=state.hosts.find(host=>host.hostId===hostId); emit({phase:'select',preview:null,previews:[],outcomeUnknown:false,...(host ? hostChoice(host) : {hostId:'',targetId:'',message:'Choose a ComfyUI window.'})}); },
        selectTarget(targetId) {
            if (disposed) return; invalidate(); const host=chosenHost();
            // D173: "All" is a choice only where there are two or more nodes, and it is never made for you.
            const all=targetId===ALL_NODES && host?.targets.length>1;
            const chosenOne=all || Boolean(host?.targets.some(target=>target.id===targetId));
            const message=all ? `${allMessage(host.targets.length,host.label)} Apply to send it, or preview the changes first.` : chosenOne ? 'Apply to send it, or preview the changes first.' : 'Choose a destination node, then preview changes.';
            emit({phase:'select',outcomeUnknown:false,targetId:chosenOne?targetId:'',preview:null,previews:[],message});
        },
        async preview() {
            const targets=chosen();
            if (disposed || !targets.length || ['previewing','applying'].includes(state.phase)) return;
            const all=state.targetId===ALL_NODES; const hostId=state.hostId; const windowLabel=chosenHost()?.label;
            const request=begin('previewing');
            try {
                if (all) {
                    const previews=await previewEach(request,hostId,targets);
                    if (previews) emit({phase:'ready',previews,message:`${allMessage(targets.length,windowLabel)} Review the complete changes, then apply.`});
                    return;
                }
                const preview=await fetchPreview(request,hostId,targets[0]);
                if (!preview) return;
                emit({phase:'ready',preview,message:`Sending to ${nodeLabel({id:preview.target.id,label:preview.target.label ?? ''})} in ${windowLabel}. Review the complete changes, then apply.`});
            } catch(error) { if(current(request)) emit({phase:'error',preview:null,previews:[],message:error.message}); }
        },
        async apply() {
            // Success stays closed: the second click of a double click must not send again.
            const targets=chosen();
            if(disposed || !targets.length || !['select','ready','error'].includes(state.phase) || state.outcomeUnknown) return;
            const all=state.targetId===ALL_NODES;
            const held=state.phase==='ready' ? (all ? state.previews : state.preview) : null;
            const hostId=state.hostId; const windowLabel=chosenHost()?.label;
            const request=begin('applying');
            emit({message:'Waiting for ComfyUI confirmation. Closing this dialog does not undo a sent update.'});
            if (all) {
                let reviewed;
                try { reviewed=held?.length ? held : await previewEach(request,hostId,targets); }
                catch(error) { if(current(request)) emit({phase:'error',message:error.message}); return; }
                if (reviewed) await applyEach(request,hostId,reviewed,windowLabel,Boolean(held?.length));
                return;
            }
            const [target]=targets;
            let posted=false;
            try {
                const preview=held ?? await fetchPreview(request,hostId,target);
                if(!preview || !current(request)) return;
                posted=true;
                const acknowledgement=await client.apply(hostId,preview.ticket,{signal:request.signal});
                if(!current(request)) return;
                checkAcknowledgement(acknowledgement,target.id);
                emit({phase:'success',message:'Sent to ComfyUI.'});
            } catch(error) {
                if(!current(request)) return;
                const unknown=outcomeUnknown(posted,error);
                emit({phase:'error',preview:null,outcomeUnknown:unknown,message:unknown ? `No confirmation received. The update may have applied. ${UNKNOWN_ADVICE} ${error.message}` : error.message});
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
        let session,closed=false,headingIds=0;
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
        function textBlock(parent,label,value,level=4) {
            parent.append(element(`h${Math.min(level,6)}`,label));const text=element('pre');text.tabIndex=0;text.append(element('code',value === '' ? '(empty)' : value));parent.append(text);
        }
        function render(state) {
            if(closed) return;
            const busy=['discovering','previewing','applying'].includes(state.phase);
            status.textContent=state.message;status.dataset.phase=state.phase;
            status.setAttribute('role',state.phase==='error'?'alert':'status');status.setAttribute('aria-live',state.phase==='error'?'assertive':'polite');
            dialog.setAttribute('aria-busy',String(busy));
            options(host,'Choose a ComfyUI window',state.hosts.map(item=>[item.hostId,item.label]),state.hostId);
            const selected=state.hosts.find(item=>item.hostId===state.hostId);
            const nodes=(selected?.targets || []).map(item=>[item.id,item.label]);
            // Q542-4 A: the last option sends to every eligible node of this window.
            if(nodes.length>1) nodes.push([ALL_NODES,`All ${nodes.length} eligible nodes`]);
            options(target,'Choose a destination node',nodes,state.targetId);
            host.disabled=busy;target.disabled=busy || !selected;
            find.disabled=busy;inspect.disabled=busy || !state.targetId;apply.disabled=busy || !state.targetId || state.phase==='success' || state.outcomeUnknown;
            preview.replaceChildren();
            if(state.preview) {
                preview.append(element('h3','Review changes'));
                changes(preview,state.preview,3);
            }
            if(state.previews?.length) {
                preview.append(element('h3','Review changes'));
                // One section per node of "All", in the order they will be sent, named by its heading.
                for(const item of state.previews) {
                    const node=element('section',undefined,'prompt-sender-node');const heading=element('h4',item.label);
                    heading.id=`prompt-sender-node-${++headingIds}`;node.setAttribute('aria-labelledby',heading.id);
                    node.append(heading);changes(node,item.preview,5);preview.append(node);
                }
            }
        }
        function changes(parent,change,level) {
            for(const field of change.fields || []) {
                const section=element('section',undefined,`prompt-sender-field prompt-role-${field.role}`);
                section.append(element(`h${level}`,field.role==='positive'?'Positive prompt':'Negative prompt'));
                textBlock(section,'Before',field.before,level+1);textBlock(section,'After',field.after,level+1);parent.append(section);
            }
            const composition=change.composition;
            if(composition) {
                const section=element('section',undefined,'prompt-sender-composition');section.append(element(`h${level}`,'Positive composition'));
                textBlock(section,'Prepend (preserved)',composition.prepend || '',level+1);textBlock(section,'Append (preserved)',composition.append || '',level+1);
                textBlock(section,'Combined before',composition.before ?? 'Resolved at execution from connected inputs.',level+1);
                textBlock(section,'Combined after',composition.after ?? 'Resolved at execution from connected inputs.',level+1);
                if(composition.prependConnected || composition.appendConnected) section.append(element('p','Connected prepend and append inputs are preserved and resolved when the workflow runs.'));
                parent.append(section);
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
