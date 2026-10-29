import {createScope,createButton} from '../shared/toolkit/core.js';
import {createPromptSender} from './prompt-sender.mjs';
import {mountPromptBlocks} from './prompt-inspector.mjs';
import {mountImages} from './images.mjs';
import {createLibraryStream,createLibraryDetail,libraryPromptRoles} from './library-state.mjs';
import {promptRoles} from './prompt-blocks.mjs';
import {createLibraryEditor,showWorkspaceToast} from './library-editor.mjs';
import {createLibraryEditorController} from './library-editor-state.mjs';
import {layoutLibraryRows,visibleLibraryRows,usedLabel,ratingLabel} from './library-layout.mjs';
import {createWorkspaceIcon} from './icons.mjs';
import {mountRecordedHistory} from './history.mjs';
import {countOf} from './plural.mjs';
import {addedTerms} from './history-state.mjs';
import {pageHeading} from './heading.mjs';
import {mountToolbar} from './toolbar.mjs';
import {libraryFilters,libraryViewState} from './saved-views.mjs';
import {openRefineDialog,openContentDialog} from './workspace-dialogs.mjs';
import {mountLibrarySelection} from './library-selection.mjs';
import {fallBackToOriginal,replaceOnError,showsThumbnail,thumbnailSource} from './thumbnail-support.mjs';

let shelfVisit=null;
// Every confirmed receipt advances this; a shelf mounted before it never restores its cached stream.
let writeGeneration=0, focusHeadingOnMount=false, savedParentId=null;
// The pair one reconciled save names, taken once by the history it opens (the concept selects it after a save, app.js 986).
const savedSelections=new Map();
const historyVisits=new Map();
// Exact-ID pair reads for selections outside the bounded variant page; never merged into it.
const selectedPairs=new Map();
// Parent metadata a reuse did not apply, kept for an explicit Edit metadata action.
const metadataDrafts=new Map();
const archivedVisible=new Map();
// The concept's three sorts first, then the Library's appended Oldest first (D14).
const LIBRARY_SORTS=Object.freeze([['newest','Recently added'],['name','Name A–Z'],['rating','Highest rated'],['oldest','Oldest first']]);
// A card's focusable controls in order: the D148 selection checkbox, then its buttons (#437).
const CARD_CONTROLS='button, input[type="checkbox"]';
const CATEGORIES_FAILED='Categories could not be loaded. Open the Prompt library again to retry.';
const ARCHIVED_MESSAGE='Archived. Find it under Show archived in Negative variants.';
function el(tag,text,className) {
    const node=document.createElement(tag);if(text!==undefined) node.textContent=text;if(className) node.className=className;return node;
}
// Cut at 80 UTF-16 units, never inside a surrogate pair (database/library.py titled() matches).
export function libraryTitle(text) {
    const first=text.split('\n')[0].trim();
    if (first.length<=80) return first;
    const cut=first.slice(0,80);
    return `${/[\uD800-\uDBFF]$/.test(cut)?cut.slice(0,-1):cut}…`;
}
const title=libraryTitle;
/** The full stored title; the first-line fallback only when it is blank. */
function displayTitle(positive) {return typeof positive.title==='string' && positive.title.trim()?positive.title:title(positive.text);}
function focusHeading(main) {const heading=main.querySelector('h1');if(!heading)return;heading.tabIndex=-1;heading.focus();}
/** The shell heading's action row (concept `.heading-actions`); a view mounted without one gets its own row. */
function headingActions(main,fallback) {
    const actions=pageHeading(main)?.actions;if(actions) return actions;
    const row=el('div',undefined,'heading-actions');fallback.append(row);return row;
}
/** The shell heading's description (concept `p.muted`); a view mounted without one shows it at the top of `fallback`. */
function headingDescription(main,fallback,text) {
    const description=pageHeading(main)?.description;
    if(description) description.textContent=text;else fallback.prepend(el('p',text,'muted'));
}

/** A confirmed receipt makes cached shelf items, cursor and measured heights stale; filters and anchor survive. */
const writeListeners=new Set();
export function invalidateLibraryAfterWrite(commit) {
    if(!commit || typeof commit.kind!=='string' || !commit.receipt || typeof commit.receipt!=='object') throw new TypeError('Invalid editor commit');
    writeGeneration++;
    if(shelfVisit) shelfVisit={filters:shelfVisit.filters,anchor:shelfVisit.layout?.anchor ?? shelfVisit.anchor ?? null};
    for(const listener of [...writeListeners]) listener(commit);
}
/** Forget the shelf's last visit (filters, items and position): the next visit starts fresh (S9: a restored library). */
export function forgetLibraryVisit() {
    writeGeneration++;shelfVisit=null;
}
/** Observe confirmed Library and vocabulary writes (the sidebar re-reads its counts); returns the unsubscribe. */
export function onLibraryWrite(listener) {
    if(typeof listener!=='function') throw new TypeError('Invalid Library write listener');
    writeListeners.add(listener);return ()=>{writeListeners.delete(listener);};
}
/**
 * Open the Library shelf with exactly these filters (Tags & categories Browse): the
 * visit is seeded like a returning shelf without a cached stream, so it reads page one
 * with the existing Library filters, and the heading takes focus as after a save.
 */
export function browseLibrary(filters) {
    const {text='',category='',tags=[],sort='newest'}=filters || {};
    if(typeof text!=='string' || typeof category!=='string' || !Array.isArray(tags) || tags.some(tag=>typeof tag!=='string') || typeof sort!=='string') throw new TypeError('Invalid Library filters');
    shelfVisit={filters:{text,category,tags:[...tags],sort},anchor:null};
    focusHeadingOnMount=true;window.location.hash='#/library';
}
/** Remember the selection a reconciled save names, so the destination history opens on it once. */
export function rememberLibraryResult(result) {
    const positive=result?.detail?.positive, kind=result?.commit?.kind;
    if(!positive || !Number.isSafeInteger(positive.id) || !Array.isArray(result.detail.variants) || typeof kind!=='string') throw new TypeError('Invalid reconciled Library result');
    if(kind==='create') savedParentId=positive.id;
    if(!result.pair || kind==='archive-pair') return;
    savedSelections.set(positive.id,result.pair.id);
    if(result.detail.variants.some(variant=>variant.id===result.pair.id)) selectedPairs.delete(positive.id);
    else selectedPairs.set(positive.id,result.pair);
    if(result.pair.archived) archivedVisible.set(positive.id,true);
}
/**
 * Toast copy from the receipt: the concept's save toast for New, Revision and Edit and its
 * Add-negative line (app.js 989, 1028); a reuse discloses what was not saved, and only
 * when the controller found the entered parent metadata differs from the stored one.
 */
function outcomeMessage(result,outcome) {
    const {kind,receipt}=result.commit;
    if(kind==='metadata') return 'Saved.';
    if(kind==='archive-pair') return ARCHIVED_MESSAGE;
    if(kind!=='create' && kind!=='add-negative') return null;
    if(receipt.positive_created) return 'Saved.';
    if(receipt.pair_created) return kind==='add-negative'?'Negative saved without adding another positive or gallery.'
        :outcome?.metadataNotApplied?'Saved as a new negative variant of an existing prompt. The title, category, tags, rating and notes you entered were not applied.'
            :'Saved as a new negative variant of an existing prompt.';
    if(receipt.pair.archived) return 'This combination is an archived negative variant. It stays archived; restore it from Show archived in Negative variants.';
    return 'This prompt pair already exists. Showing the saved version.';
}
/** Take the saved selection for one positive; it never outlives the visit that consumes it. */
function takeSavedSelection(positiveId) {
    const pairId=savedSelections.get(positiveId);savedSelections.delete(positiveId);return pairId ?? null;
}
function rememberUnappliedMetadata(result,{outcome,draft}={}) {
    if(result.commit.kind!=='create' || !outcome?.metadataNotApplied || !draft) return;
    const {title:name,category,tags,rating,notes}=draft;metadataDrafts.set(result.detail.positive.id,{title:name,category,tags,rating,notes});
}
/** New prompt, from Library or Images: a saved prompt always opens its own history, as in the concept. */
function createNewPromptEditor(api,scope,main) {
    return createLibraryEditor({api,parentScope:scope,onCommitted:invalidateLibraryAfterWrite,onReconciled:(result,context)=>{
        rememberLibraryResult(result);rememberUnappliedMetadata(result,context);
        const message=outcomeMessage(result,context?.outcome);if(message) showWorkspaceToast(message);
        const hash=`#/library/${result.detail.positive.id}`;
        if(window.location.hash===hash) focusHeading(main);else {focusHeadingOnMount=true;window.location.hash=hash;}
    }});
}
/** The Images heading's New prompt: opens the editor without mounting or navigating to the Library. */
export function mountNewPromptAction(main,api,parentScope) {
    const actions=headingActions(main,main);
    const button=createButton('','ca-primary');button.append(createWorkspaceIcon('plus'),el('span','New prompt'));actions.append(button);
    const editor=createNewPromptEditor(api,parentScope,main);
    parentScope.listen(button,'click',()=>editor.open({mode:'new',trigger:button}));
    return button;
}

/** `savedState` opens a saved view (#249 D16) instead of the last visit; `onSaveView(trigger, state)` is Save view. */
export function mountLibrary(main,api,parentScope,{positiveId,savedState=null,onSaveView=()=>{}}={}) {
    const scope=createScope();parentScope.cleanup(()=>scope.dispose());
    main.classList.add('library-main');
    const sender=createPromptSender({scope});
    if(positiveId!==undefined) mountHistory();
    else mountShelf();
    if(focusHeadingOnMount) {focusHeadingOnMount=false;focusHeading(main);}
    // Save the visit before removing the class that defines the shelf's scroll range.
    scope.cleanup(()=>main.classList.remove('library-main'));

    function mountShelf() {
        const saved=savedState?libraryFilters(savedState):null;
        const visit=saved?null:shelfVisit, mountedGeneration=writeGeneration;
        // An invalidated visit keeps only filters and anchor: the stream restarts and heights are measured again.
        const restorable=visit?.state?visit:null;
        const filters={text:'',category:'',tags:[],sort:'newest',...visit?.filters,...saved};
        const section=el('section',undefined,'library-shelf');section.setAttribute('aria-label','Prompt library');
        // The shared concept toolbar (app.js toolbar(), a347–360): Tags live in "Refine this view" (D14), no Refresh (D15).
        // Its own scope, disposed after the visit is saved: removing it first would shorten the page and clamp scrollY.
        const toolbarScope=createScope();
        // D44: Filters reads "Filters · 1" while a Tags filter (from Refine or a Browse) narrows the shelf.
        const refinements=()=>filters.tags.length?1:0;
        const toolbar=mountToolbar(section,{scope:toolbarScope,categories:filters.category?[filters.category]:[],sorts:LIBRARY_SORTS,
            value:{q:filters.text,category:filters.category,sort:filters.sort},activeFilters:refinements(),onChange:applyToolbar,onFilters:openFilters,
            onSaveView:trigger=>{const value=toolbar.value();onSaveView(trigger,libraryViewState({...filters,text:value.q.trim(),category:value.category,sort:value.sort}));}});
        const optionsStatus=el('p','','library-options-status');optionsStatus.hidden=true;optionsStatus.setAttribute('role','alert');
        // The concept caption (app.js renderLibrary a586): the filtered set's parents and their negative variants.
        const caption=el('div',undefined,'section-caption');const captionCount=el('span');caption.append(captionCount,el('span','Images are grouped by positive prompt'));caption.hidden=true;
        // The count is announced when it changes; a reload keeps the last count shown, so nothing else re-announces it.
        captionCount.setAttribute('role','status');
        const grid=el('div',undefined,'library-grid');grid.setAttribute('role','list');grid.setAttribute('aria-label','Positive prompts');
        // #437 D148: select prompts and set their category (the legacy admin's Set Category); a write reloads the shelf.
        let categoryNames=[];
        const selection=mountLibrarySelection({scope,api,grid,getItems:()=>state?.items ?? [],getEpoch:()=>state?.epoch ?? null,
            getCategories:()=>categoryNames,focusFallback:{focus:()=>focusHeading(main)},
            onCommitted:(kind,receipt)=>invalidateLibraryAfterWrite({kind:`bulk-${kind}`,receipt}),onReload:()=>{loadCategories();reset();}});
        const message=el('p','','library-status');message.setAttribute('role','status');
        const continuation=el('div',undefined,'library-continuation');
        const more=createButton('Load more prompts');const retry=createButton('Retry prompt library');retry.hidden=true;
        const actions=headingActions(main,section);
        const exportButton=createButton('Export');exportButton.setAttribute('aria-label','Export full Library JSON');
        const newPrompt=createButton('+ New prompt','ca-primary');actions.append(exportButton,newPrompt);
        const exportStatus=el('p','','library-options-status');exportStatus.hidden=true;exportStatus.setAttribute('role','alert');
        const editor=createNewPromptEditor(api,scope,main);
        scope.listen(newPrompt,'click',()=>editor.open({mode:'new',trigger:newPrompt}));
        const exportUrls=new Set();let exporting=false;
        scope.cleanup(()=>{for(const url of exportUrls)URL.revokeObjectURL(url);exportUrls.clear();});
        // The exact full-Library document; a failure is reported before anything is downloaded.
        async function exportLibrary() {
            if(exporting) return;exporting=true;exportStatus.hidden=true;
            try {
                const response=await fetch(window.ApiClient.resolveUrl('/prompt_manager/library/export'),{cache:'no-store',credentials:'same-origin',signal:scope.signal});
                if(!response.ok) throw new Error(`Export failed with status ${response.status}`);
                const text=await response.text();const parsed=JSON.parse(text);
                if(parsed?.kind!=='prompt-manager-library' || parsed.schema_version!==1 || !Array.isArray(parsed.positives)) throw new TypeError('Invalid Library export');
                if(scope.signal.aborted) return;
                const url=URL.createObjectURL(new Blob([text],{type:'application/json'}));exportUrls.add(url);
                const anchor=el('a');anchor.href=url;anchor.download='prompt-manager-library-v1.json';anchor.hidden=true;section.append(anchor);anchor.click();anchor.remove();
                setTimeout(()=>{if(exportUrls.delete(url))URL.revokeObjectURL(url);},0);
            } catch {
                if(!scope.signal.aborted) {exportStatus.textContent='The Library export could not be downloaded. Nothing was saved to this device.';exportStatus.hidden=false;}
            } finally {exporting=false;}
        }
        scope.listen(exportButton,'click',exportLibrary);
        headingDescription(main,section,'One positive. Reusable negatives. Every experiment stays connected.');
        // Nothing follows the grid in the concept; the legacy Library stays one click away on Overview (spec §5).
        continuation.append(message,retry,more);section.append(optionsStatus,exportStatus,selection.element,caption,grid,continuation);main.append(section);
        const cards=new Map(), rowNodes=new Map(), spacers=new Map(), heights=new Map(), pendingMeasurements=new Map();
        let state,frame=0,loadFrame=0,observer,optionsRequest,focusOverride=-1;
        const columns=()=>window.innerWidth>=1700?3:window.innerWidth<=700?1:2;
        const readInlineSize=()=>Math.round(grid.getBoundingClientRect().width*64)/64;
        let inlineSize=readInlineSize(), columnCount=columns(), layout={rows:[],height:0}, dirty=true, items, indexesById=new Map();
        let anchor=restorable?.layout?.anchor || null, pendingAnchor=anchor, observedScrollY=window.scrollY, retainedAnchor=restorable?null:visit?.anchor || null;
        let restoreScrollY=Number.isFinite(restorable?.scrollY) && restorable.scrollY>=0?restorable.scrollY:null;
        if(restorable?.layout?.inlineSize===inlineSize && restorable.layout.columns===columnCount) for(const [id,height] of restorable.layout.heights) heights.set(id,height);
        const hiddenSave=restorable?null:savedParentId;savedParentId=null;
        function removeCard(card) {measurementObserver?.unobserve(card.node);pendingMeasurements.delete(Number(card.node.dataset.promptId));card.blocks.dispose();card.node.remove();}
        /** The concept's prompt card (app.js renderLibrary a586) with real counts, last use and rating (#249 D23). */
        function cardFor(item,index) {
            const article=el('article',undefined,'prompt-card');article.dataset.promptId=item.id;article.setAttribute('role','listitem');
            const art=el('div',undefined,'card-art');art.setAttribute('aria-hidden','true');
            const previews=item.previews || [];
            for(const image of previews.slice(0,2)) {
                // #317: a video with no preview, or one this browser cannot show (JPEG XL), keeps its label.
                if(!showsThumbnail(image)) art.append(el('span','Video preview','small'));
                else {
                    // #317: an image's JPEG XL preview this browser cannot show, or one that fails, loads the original;
                    // a video's preview that fails becomes its label, never a broken image.
                    const img=el('img');img.src=window.ApiClient.resolveUrl(thumbnailSource(image));if(image.is_video) replaceOnError(img,()=>el('span','Video preview','small'));else fallBackToOriginal(img,window.ApiClient.resolveUrl(image.url));img.alt='';img.loading='lazy';img.decoding='async';img.width=image.width || 800;img.height=image.height || 600;art.append(img);
                }
            }
            if(!previews.length) art.append(el('span','No available image previews','small'));
            const name=displayTitle(item);
            const content=el('div',undefined,'card-content');
            const heading=el('div',undefined,'card-title');const open=createButton(name);open.dataset.history='';
            heading.append(open,el('span',item.category || 'Uncategorized','chip'));
            const prompts=el('div');
            const blocks=mountPromptBlocks(prompts,{prompts:{positive:{status:'resolved',text:item.text,source:'library'}},sender,parentScope:scope,roles:['positive'],title:name});
            const meta=el('div',undefined,'card-meta');
            meta.append(el('span',`\u2212 ${countOf(item.variant_count,'negative variant')}`,'chip red'),el('span',countOf(item.run_count ?? 0,'run')),el('span',countOf(item.image_count,'image')));
            const vocabulary=el('div',undefined,'tags');for(const tag of item.tags || []) vocabulary.append(el('span',tag,'chip'));
            content.append(heading,prompts,meta,vocabulary);
            const footer=el('div',undefined,'card-footer');
            const {stars,hidden}=ratingLabel(item.rating), used=usedLabel(item.last_used_at);
            const facts=el('span');
            if(stars) {const shown=el('span',stars);shown.setAttribute('aria-hidden','true');facts.append(shown,el('span',hidden,'ca-visually-hidden'),` \u00b7 ${used}`);}
            else facts.textContent=used;
            const actions=el('div',undefined,'row-actions');
            const send=createButton('','send icon-btn');send.setAttribute('aria-label',`Send positive prompt to ComfyUI: ${name}`);send.append(createWorkspaceIcon('send'));send.dataset.sendPositive='';
            const explore=createButton('');explore.append(createWorkspaceIcon('branch'),' Explore history');explore.dataset.history='';
            actions.append(send,explore);footer.append(facts,actions);
            article.append(selection.overlayFor(item,name),art,content,footer);article.dataset.index=index;return {node:article,blocks};
        }
        function captureAnchor() {
            // Mounted rows can already differ from estimates before their observer batch.
            for(const row of grid.children) {
                if(!row.classList.contains('library-row'))continue;
                const bounds=row.getBoundingClientRect();
                if(bounds.bottom>0 && bounds.top<window.innerHeight)return {id:Number(row.firstElementChild.dataset.promptId),offset:bounds.top};
            }
            return null;
        }
        function reconcileScroll() {
            const delta=window.scrollY-observedScrollY;observedScrollY=window.scrollY;
            if(!delta)return;
            // Keep pending height compensation while honoring movement since it was queued.
            if(pendingAnchor)pendingAnchor={...pendingAnchor,offset:pendingAnchor.offset-delta};
            else anchor=captureAnchor();
        }
        function spacer(key,height) {
            let node=spacers.get(key);
            if(!node) {node=el('div',undefined,'library-row-spacer');node.setAttribute('aria-hidden','true');spacers.set(key,node);}
            const size=`${height}px`;if(node.style.blockSize!==size)node.style.blockSize=size;
            return node;
        }
        function renderWindow(topOverride) {
            if(!state || scope.signal.aborted) return;
            const active=document.activeElement, activeCard=active?.closest('article[data-prompt-id]');
            const focused=focusOverride>=0?focusOverride:activeCard?Number(activeCard.dataset.index):-1;
            const top=topOverride??Math.max(0,-grid.getBoundingClientRect().top);
            const visible=state.items.length<=120?layout.rows.map((_,index)=>index):visibleLibraryRows(layout.rows,top,window.innerHeight,focused);
            const keep=new Set(), children=[];let previous=-1;
            for(const rowIndex of visible) {
                const row=layout.rows[rowIndex];
                if(rowIndex>previous+1) {
                    const end=previous<0?0:layout.rows[previous].top+layout.rows[previous].height+18;
                    children.push(spacer(`${previous}:${rowIndex}`,row.top-end-18));
                }
                let rowNode=rowNodes.get(rowIndex);
                if(!rowNode) {rowNode=el('div',undefined,'library-row');rowNode.setAttribute('role','presentation');rowNodes.set(rowIndex,rowNode);}
                if(!rowNode.isConnected)grid.append(rowNode);
                for(let index=row.first;index<=row.last;index++) {
                    const item=state.items[index];keep.add(item.id);let card=cards.get(item.id);
                    if(!card) {card=cardFor(item,index);cards.set(item.id,card);rowNode.append(card.node);measurementObserver?.observe(card.node);}
                    else if(card.node.parentElement!==rowNode)rowNode.append(card.node);
                    card.node.dataset.index=index;card.node.setAttribute('aria-posinset',index+1);card.node.setAttribute('aria-setsize',state.total);
                }
                children.push(rowNode);previous=rowIndex;
            }
            for(const [id,card] of cards) if(!keep.has(id)) {removeCard(card);cards.delete(id);}
            if(previous>=0 && previous<layout.rows.length-1) {const end=layout.rows[previous].top+layout.rows[previous].height;children.push(spacer(`${previous}:end`,layout.height-end-18));}
            for(const child of [...grid.children]) if(!children.includes(child)) child.remove();
            let sibling=null;
            for(const child of children) {if(sibling?sibling.nextElementSibling!==child:grid.firstElementChild!==child)grid.insertBefore(child,sibling?sibling.nextElementSibling:grid.firstChild);sibling=child;}
            for(const [key,node] of rowNodes)if(!node.isConnected)rowNodes.delete(key);
            for(const [key,node] of spacers)if(!node.isConnected)spacers.delete(key);
            if(activeCard && active.isConnected && document.activeElement!==active)active.focus({preventScroll:true});
        }
        function flush() {
            frame=0;if(scope.signal.aborted)return;
            reconcileScroll();
            const width=readInlineSize(), size=columns();
            if(width!==inlineSize || size!==columnCount) {pendingAnchor ||= anchor;inlineSize=width;columnCount=size;heights.clear();dirty=true;}
            for(const [id,measurement] of pendingMeasurements) {
                if(measurement.width===inlineSize && indexesById.has(id) && Math.abs((heights.get(id)??0)-measurement.height)>.5) {heights.set(id,measurement.height);dirty=true;}
            }
            pendingMeasurements.clear();
            const restore=dirty?(pendingAnchor || anchor):null;
            if(dirty) {layout=layoutLibraryRows(state.items.map(item=>heights.get(item.id)??null),columnCount,{estimate:columnCount===1?422:448});dirty=false;}
            const index=restore?indexesById.get(restore.id):undefined;
            const target=index===undefined?undefined:Math.max(0,layout.rows[Math.floor(index/columnCount)].top-restore.offset);
            renderWindow(target);
            const card=index===undefined?null:cards.get(restore.id);
            if(card) {
                const delta=card.node.getBoundingClientRect().top-restore.offset;if(Math.abs(delta)>.5)window.scrollBy(0,delta);
                anchor=restore;
            } else {
                if(restoreScrollY!==null)window.scrollTo(0,restoreScrollY);
                anchor=captureAnchor();
            }
            // Read the actual clamped result now; its later scroll event is not new motion.
            observedScrollY=window.scrollY;
            restoreScrollY=null;pendingAnchor=null;scheduleLoad();
        }
        const schedule=()=>{if(!frame)frame=requestAnimationFrame(flush);};
        const measurementObserver=typeof ResizeObserver==='function'?new ResizeObserver(entries=>{
            const width=readInlineSize();let changed=width!==inlineSize || columns()!==columnCount;
            for(const entry of entries) {
                const id=Number(entry.target.dataset.promptId);if(!cards.has(id) || cards.get(id).node!==entry.target)continue;
                const height=entry.borderBoxSize?.[0]?.blockSize ?? entry.target.getBoundingClientRect().height;
                if(Number.isFinite(height) && height>0 && (width!==inlineSize || Math.abs((pendingMeasurements.get(id)?.height??heights.get(id)??0)-height)>.5)) {pendingMeasurements.set(id,{height,width});changed=true;}
            }
            if(changed) {pendingAnchor ||= anchor;schedule();}
        }):null;
        measurementObserver?.observe(grid);
        /** Focus a card's first or `last` control (Tab across the window edge), or its title (`title`: Home/End/arrows). */
        function focusCard(index,last=false,title=false) {
            if(index<0 || index>=state.items.length) return;
            if(dirty) {cancelAnimationFrame(frame);flush();}focusOverride=index;renderWindow();const card=cards.get(state.items[index].id).node;
            const controls=card.querySelectorAll(CARD_CONTROLS);(title?card.querySelector('.card-title > button'):last?controls[controls.length-1]:controls[0]).focus();card.scrollIntoView({block:'nearest'});focusOverride=-1;renderWindow();
        }
        // The title and Explore history open the prompt's history; the footer send sends the full positive (D23).
        scope.listen(grid,'click',event=>{
            const card=event.target.closest('article[data-prompt-id]');if(!card) return;
            const item=state?.items[indexesById.get(Number(card.dataset.promptId))];if(!item) return;
            if(event.target.closest('[data-history]')) window.location.hash=`#/library/${item.id}`;
            const send=event.target.closest('[data-send-positive]');if(send) sender.open({positive:item.text},send);
        });
        scope.listen(grid,'keydown',event=>{
            const card=event.target.closest('article[data-prompt-id]');if(!card) return;
            const index=Number(card.dataset.index);
            // D151 (#444 ruling): Ctrl/Cmd+A selects every loaded prompt and Escape clears, as on Images (focus stays).
            if((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase()==='a') {event.preventDefault();selection.selectAll();return;}
            if(event.key==='Escape' && selection.hasSelection()) {event.preventDefault();selection.clear();return;}
            if(event.target.matches('[data-history]') && ['Home','End','ArrowLeft','ArrowRight'].includes(event.key)) {event.preventDefault();focusCard(event.key==='Home'?0:event.key==='End'?state.items.length-1:index+(event.key==='ArrowLeft'?-1:1),false,true);}
            else if(event.key==='Tab') {
                const controls=card.querySelectorAll(CARD_CONTROLS);const next=index+(event.shiftKey?-1:1);
                const edge=event.shiftKey?controls[0]:controls[controls.length-1];
                if(event.target===edge && state.items[next] && !cards.has(state.items[next].id)) {event.preventDefault();focusCard(next,event.shiftKey);}
            }
        });
        function render(next) {
            const batch=items!==next.items;
            if(batch) {
                pendingAnchor ||= captureAnchor();items=next.items;indexesById=new Map(items.map((item,index)=>[item.id,index]));dirty=true;
                if(!items.length) {
                    anchor=null;pendingAnchor=null;heights.clear();layout={rows:[],height:0};
                    for(const card of cards.values())removeCard(card);
                    cards.clear();rowNodes.clear();spacers.clear();grid.replaceChildren();
                } else if(retainedAnchor) {pendingAnchor=retainedAnchor;retainedAnchor=null;}
            }
            state=next;
            // A loaded batch: the selection bar's count and Select all N loaded follow it (D151).
            if(batch) selection.listingChanged();
            if(next.variantTotal!==null) {
                const count=`${countOf(next.total,'positive prompt')} · ${countOf(next.variantTotal,'negative variant')}`;
                if(captionCount.textContent!==count) captionCount.textContent=count;caption.hidden=false;
            }
            const hidden=hiddenSave!==null && !next.loading && !next.error && !next.hasMore && !next.items.some(item=>item.id===hiddenSave)?' The saved prompt is hidden by the current filters.':'';
            message.textContent=(next.error?'Prompt library could not be loaded. Retry.':next.loading?'Loading prompt library…':!next.items.length?'No matching prompts. Try changing the filters.':!next.hasMore?'You’re all caught up.':'')+hidden;
            message.setAttribute('role',next.error?'alert':'status');retry.hidden=!next.error;more.hidden=!next.hasMore || !!next.error;more.setAttribute('aria-disabled',String(next.loading));
            schedule();scheduleLoad();
        }
        const stream=createLibraryStream({api,onChange:render,restore:restorable?{filters,state:restorable.state}:undefined});
        function maybeLoad() {
            if(!observer || !state?.hasMore || state.loading || state.error || scope.signal.aborted) return;
            const bounds=continuation.getBoundingClientRect();if(bounds.top<=window.innerHeight+250 && bounds.bottom>=0) stream.loadMore();
        }
        function scheduleLoad() {if(observer && !loadFrame) loadFrame=requestAnimationFrame(()=>{loadFrame=0;maybeLoad();});}
        if(typeof IntersectionObserver==='function') {observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)) maybeLoad();},{rootMargin:'250px'});observer.observe(continuation);}
        async function loadCategories() {
            optionsRequest?.abort();const request=optionsRequest=new AbortController();
            try {
                const data=await api.get('/prompt_manager/library/options',{signal:request.signal});if(scope.signal.aborted || optionsRequest!==request) return;
                categoryNames=[...data.categories];toolbar.setCategories([...data.categories,filters.category]);toolbar.setValue({category:filters.category});optionsStatus.hidden=true;
            } catch {if(!scope.signal.aborted && optionsRequest===request) {optionsStatus.textContent=CATEGORIES_FAILED;optionsStatus.hidden=false;}}
        }
        const reset=()=>{restoreScrollY=null;selection.clear();toolbar.setActiveFilters(refinements());return stream.reset(filters);};
        /** A toolbar report; a debounced search that lands after a select already applied the same values reloads nothing. */
        function applyToolbar(value) {
            const next={text:value.q.trim(),category:value.category,sort:value.sort};
            if(Object.entries(next).every(([key,item])=>filters[key]===item)) return;
            Object.assign(filters,next);reset();
        }
        /** "Refine this view" with the Library's one extra filter, Tags (D14); Clear also clears the toolbar category. */
        function openFilters(trigger) {
            openRefineDialog({scope,trigger,fields:[{key:'tags'}],values:{tags:filters.tags.join(', ')},onApply:applyRefinement,
                onClear:cleared=>{toolbar.setValue({category:cleared.category});applyRefinement(cleared);}});
        }
        function applyRefinement({tags,category=filters.category}) {
            const next=[...new Set(tags.split(',').map(tag=>tag.trim()).filter(Boolean))];
            if(category===filters.category && next.length===filters.tags.length && next.every((tag,index)=>tag===filters.tags[index])) return;
            filters.category=category;filters.tags=next;reset();
        }
        scope.listen(more,'click',()=>stream.loadMore());
        // Retry also re-reads a category list that failed (D15: Refresh is gone).
        scope.listen(retry,'click',()=>{if(!optionsStatus.hidden)loadCategories();stream.loadMore();});
        scope.listen(window,'scroll',()=>{reconcileScroll();schedule();});
        // Mobile viewport resizing can adjust scroll before delivering its resize event.
        scope.listen(window,'resize',()=>{pendingAnchor ||= anchor;observedScrollY=window.scrollY;schedule();});
        scope.cleanup(()=>{
            const savedFilters={...filters,tags:[...filters.tags]},savedAnchor=pendingAnchor || anchor || captureAnchor();
            // A write while mounted makes this stream stale: keep only what still means something.
            // A saved view is not the plain shelf, so it never replaces the Library return state (#249 M2, as Favorites).
            if(!saved) shelfVisit=mountedGeneration!==writeGeneration?{filters:savedFilters,anchor:savedAnchor}:state?{filters:savedFilters,state,scrollY:window.scrollY,layout:{inlineSize,columns:columnCount,heights:[...heights],anchor:savedAnchor}}:null;
            toolbarScope.dispose();stream.dispose();optionsRequest?.abort();observer?.disconnect();measurementObserver?.disconnect();cancelAnimationFrame(frame);cancelAnimationFrame(loadFrame);for(const card of cards.values())removeCard(card);cards.clear();});
        loadCategories();if(restorable) {render(stream.state);if(restorable.state.loading || restorable.state.error)stream.loadMore();} else reset();
    }

    function mountHistory() {
        const history=el('section',undefined,'library-history');history.setAttribute('aria-label','Prompt history');main.append(history);
        const body=el('div');history.append(body);
        // Concept heading (app.js a591–597): ← Prompt library, Edit metadata, then the selected pair's Send to ComfyUI.
        headingDescription(main,history,'Prompt history · one positive, one shared gallery');
        const actions=headingActions(main,history);
        const back=createButton('← Prompt library'),edit=createButton('Edit metadata'),send=createButton('','send');
        send.append(createWorkspaceIcon('send'),el('span','Send to ComfyUI'));send.disabled=true;actions.append(back,edit,send);
        let blocks,galleryScope,detail,contentScope,updateBranches,updateParent,current=null;
        scope.cleanup(()=>{blocks?.dispose();galleryScope?.dispose();contentScope?.dispose();});
        // Every visit opens on the first negative, as the concept's navigate() does (app.js 1149: state.variant = 0);
        // only the pair a save just named is selected instead. A verified pair outside the page belongs to that save.
        let pendingSelection=takeSavedSelection(positiveId);
        if(selectedPairs.get(positiveId)?.id!==pendingSelection) selectedPairs.delete(positiveId);
        const loader=createLibraryDetail({api,onChange:render});scope.cleanup(()=>loader.dispose());
        const editor=createLibraryEditor({api,parentScope:scope,onCommitted:invalidateLibraryAfterWrite,onReconciled:reconciled,onRefreshRequired:reloadView});
        scope.listen(back,'click',()=>{window.location.hash='#/library';});
        scope.listen(edit,'click',()=>{
            const draft=metadataDrafts.get(positiveId);metadataDrafts.delete(positiveId);
            editor.open({mode:'metadata',positiveId,pairId:current?.activePairId() ?? null,trigger:edit,draft:draft || null});
        });
        scope.listen(send,'click',()=>{const roles=current?.sendRoles();if(roles)sender.open(roles,send);});
        function reconciled(result,context) {
            rememberLibraryResult(result);rememberUnappliedMetadata(result,context);
            const message=outcomeMessage(result,context?.outcome),id=result.detail.positive.id;
            if(message) showWorkspaceToast(message);
            if(id!==positiveId) {focusHeadingOnMount=true;window.location.hash=`#/library/${id}`;return;}
            pendingSelection=takeSavedSelection(positiveId);
            if(current) current.apply(result);else loader.load(positiveId);
            focusHeading(main);
        }
        /** Explicit exit after restored storage: dispose the recorded view, then remount with a fresh visit. */
        function reloadView() {
            writeGeneration++;shelfVisit=shelfVisit && {filters:shelfVisit.filters,anchor:null};
            pendingSelection=null;savedSelections.delete(positiveId);selectedPairs.delete(positiveId);metadataDrafts.delete(positiveId);
            current=null;contentScope?.dispose();contentScope=null;historyVisits.delete(positiveId);
            loader.load(positiveId);focusHeading(main);
        }
        function render(state) {
            if(detail && state.detail && !state.loading && !state.error) {detail=state.detail;updateParent();updateBranches(state);return;}
            contentScope?.dispose();contentScope=createScope();detail=null;updateBranches=null;updateParent=null;current=null;send.disabled=true;
            blocks?.dispose();blocks=null;galleryScope?.dispose();galleryScope=null;body.replaceChildren();
            if(state.loading || state.error) {
                const message=el('p',state.loading?'Loading prompt history…':'Prompt history could not be loaded.','library-status');message.setAttribute('role',state.error?'alert':'status');body.append(message);
                if(state.error) {const retry=createButton('Retry prompt history');contentScope.listen(retry,'click',()=>loader.load(positiveId));body.append(retry);}return;
            }
            detail=state.detail;const content=contentScope;
            // The concept summary (app.js a598): category chip, positive and selected negative blocks, then the counts (D24).
            const summary=el('article',undefined,'surface');const categoryChip=el('span','','chip');summary.append(categoryChip);
            const promptContainer=el('div');promptContainer.setAttribute('role','region');promptContainer.setAttribute('aria-label','Saved prompt texts');summary.append(promptContainer);
            const meta=el('div',undefined,'card-meta'),runsMeta=el('span'),imagesMeta=el('span'),variantsMeta=el('span');runsMeta.hidden=true;
            meta.append(runsMeta,imagesMeta,variantsMeta);summary.append(meta);
            const parentStatus=el('p','','library-parent-status muted');parentStatus.hidden=true;parentStatus.setAttribute('role','status');
            const retryParent=createButton('Retry saved Library details'),reloadSaved=createButton('Reload saved Library view');retryParent.hidden=reloadSaved.hidden=true;summary.append(parentStatus,retryParent,reloadSaved);
            const ownerText=detail.positive.text;let savedReadState='current',parentRequest,parentGeneration=0;
            content.cleanup(()=>{parentGeneration++;parentRequest?.abort();});
            const all=createButton('All images for this positive');
            // The concept's negative variants panel (app.js a598, style.css 1059-1154): heading, the lanes, then the legend.
            const variants=el('section',undefined,'surface timeline-panel');variants.setAttribute('aria-label','Saved negative variants');
            const panelHeading=el('div',undefined,'panel-heading'),panelCopy=el('div'),rowActions=el('div',undefined,'row-actions');
            panelCopy.append(el('h2','Negative variants'),el('p','Choose a red dot to change the negative and filter this gallery.','small'));
            // Compare negatives (aria-pressed) opens the comparison section below; Show archived is the approved #235 addition.
            const compare=createButton('Compare negatives'),addNegative=createButton('+ Add negative'),showArchived=createButton('');
            compare.setAttribute('aria-pressed','false');showArchived.setAttribute('aria-pressed','false');
            rowActions.append(compare,addNegative,showArchived);panelHeading.append(panelCopy,rowActions);variants.append(panelHeading);
            content.listen(compare,'click',()=>{compare.setAttribute('aria-pressed',String(compare.getAttribute('aria-pressed')!=='true'));updateCompare();});
            // The concept's comparison (app.js a599-616, style.css 1155-1179): after the timeline panel, the baseline (the oldest
            // active variant, D25) beside the selected negative, then its added terms. Another dot updates it in place.
            const compareSection=el('section',undefined,'surface');let compareBlocks=[],compareKey=null;
            const disposeCompareBlocks=()=>{for(const item of compareBlocks)item.dispose();compareBlocks=[];};
            content.cleanup(disposeCompareBlocks);
            function compareBlock(text,label) {
                // The concept's `.compare-item` holds the block itself; the blocks mount in a transparent child (D25).
                const item=el('div',undefined,'compare-item'),mount=el('div');item.append(mount);
                // D46: the two blocks are named Baseline and Selected, so no region or action name repeats on the page.
                compareBlocks.push(mountPromptBlocks(mount,{prompts:{negative:{status:'resolved',text,source:'library'}},roles:['negative'],sender,parentScope:content,includePairAction:false,contextLabel:label,title:displayTitle(detail.positive)}));
                compareBlocks.at(-1).setState({prompts:{negative:{status:'resolved',text,source:'library'}},actionsEnabled:savedReadState==='current'});
                return item;
            }
            function updateCompare() {
                if(detail.variant_total===0) compare.setAttribute('aria-pressed','false');
                compare.disabled=detail.variant_total===0;
                if(compare.getAttribute('aria-pressed')!=='true') {compareSection.remove();disposeCompareBlocks();compareKey=null;return;}
                if(variants.nextSibling!==compareSection) variants.after(compareSection);
                // D46: the oldest active variant may sit on an unloaded page; the baseline is never guessed from the newest lanes.
                const baseline=detail.variants_has_more?null:detail.variants.reduce((oldest,variant)=>!oldest || variant.id<oldest.id?variant:oldest,null);
                const lane=[...lanes.values()].find(item=>item.entry.variant.id===activePairId) || [...lanes.values()].find(item=>item.entry.variant.id===baseline?.id);
                const selected=(activePairId!==null && activePairId!==undefined && recordFor(activePairId)) || baseline;
                const name=lane?laneName(lane.entry):'';
                const key=JSON.stringify([baseline?.id,baseline?.negative_text,selected?.id,selected?.negative_text,name,displayTitle(detail.positive),savedReadState]);
                if(key===compareKey) return;
                compareKey=key;disposeCompareBlocks();compareSection.replaceChildren(el('h2','Compare negative variants'));
                if(!baseline || !selected) {compareSection.append(el('p','Load more negative variants to reach the baseline.','small'));return;}
                const grid=el('div',undefined,'compare-grid');grid.append(compareBlock(baseline.negative_text,'Baseline'),compareBlock(selected.negative_text,'Selected'));
                compareSection.append(el('p',`Baseline compared with ${name}. Choose another red dot to change the comparison.`),grid,el('p','Added terms','small'));
                const terms=addedTerms(baseline.negative_text,selected.negative_text);
                if(terms.length) for(const term of terms) compareSection.append(el('span',`+ ${term}`,'diff-added'));
                else compareSection.append(el('p','No added terms.','small'));
            }
            const branches=el('div',undefined,'timeline');const positiveLane=el('div',undefined,'positive-lane');positiveLane.append(el('span','Positive prompt · unchanged'));branches.append(positiveLane);
            const lanes=new Map();const emptyNote=el('p','No saved negative variants. Existing image associations remain available.','muted');
            // D45: the bounded page count stays a status for assistive technology; the concept shows no count line.
            const variantCount=el('p','','ca-visually-hidden');variantCount.setAttribute('role','status');
            const variantError=el('p','','muted');variantError.setAttribute('role','alert');variantError.hidden=true;
            const restoreStatus=el('p','','muted library-restore-status');restoreStatus.setAttribute('role','alert');restoreStatus.hidden=true;
            const restoreAction=createButton('');restoreAction.hidden=true;let restoreHandler=null;
            content.listen(restoreAction,'click',()=>restoreHandler?.());
            const more=createButton('Load more negative variants');const retry=createButton('Retry negative variants');retry.hidden=true;
            const archivedLoader=createLibraryDetail({api,onChange:()=>{if(!content.signal.aborted)updateBranches(loader.state);},archive:'archived'});content.cleanup(()=>archivedLoader.dispose());
            const restoringPairs=new Set();
            let archivedShown=archivedVisible.get(positiveId)===true,archivedStale=false;
            const archivedList=()=>archivedShown?archivedLoader.state.detail?.variants || []:[];
            const separate=()=>{const pair=selectedPairs.get(positiveId);return pair && !detail.variants.some(variant=>variant.id===pair.id) && !archivedList().some(variant=>variant.id===pair.id)?pair:null;};
            const moreTarget=()=>detail.variants_has_more?loader:archivedShown && archivedLoader.state.detail?.variants_has_more?archivedLoader:null;
            content.listen(more,'click',()=>{if(savedReadState==='current')moreTarget()?.loadMore();});
            content.listen(retry,'click',()=>{
                if(savedReadState!=='current') return;
                if(archivedLoader.state.error) archivedLoader.load(positiveId);else if(archivedLoader.state.variantsError) archivedLoader.loadMore();else loader.loadMore();
            });
            content.listen(addNegative,'click',()=>editor.open({mode:'negative',positiveId,trigger:addNegative}));
            content.listen(showArchived,'click',()=>{
                archivedShown=!archivedShown;archivedVisible.set(positiveId,archivedShown);
                // A write while hidden makes the loaded archived list stale; reread it when it is shown again.
                if(archivedShown && (archivedStale || (!archivedLoader.state.detail && !archivedLoader.state.loading))) {archivedStale=false;archivedLoader.load(positiveId);}
                if(!archivedShown && archivedList().every(variant=>variant.id!==activePairId) && detail.variants.every(variant=>variant.id!==activePairId) && separate()?.id!==activePairId) select(null);
                updateBranches(loader.state);
            });
            function laneName(entry) {
                const {variant}=entry;
                return variant.title || (variant.negative_text===''?'Explicitly empty negative':entry.kind==='separate'?'Selected saved negative variant':`Negative variant ${entry.number}`);
            }
            function laneEntries() {
                const entries=detail.variants.map((variant,index)=>({key:`active:${variant.id}`,kind:'active',variant,number:index+1}));
                const loadedArchived=archivedList(),outside=separate();
                // Archived numbers follow the active total; a pair outside the loaded archived page follows them, so names stay unique.
                if(outside) entries.push({key:`${outside.archived?'archived':'separate'}:${outside.id}`,kind:outside.archived?'archived':'separate',variant:outside,number:detail.variant_total+loadedArchived.length+1});
                for(const [index,variant] of loadedArchived.entries()) entries.push({key:`archived:${variant.id}`,kind:'archived',variant,number:detail.variant_total+index+1});
                return entries;
            }
            /** D45: a dot names its pair by a title no other loaded lane shares, else by its number, so every name is unique. */
            // Titles compare trimmed (accessible names are whitespace-normalized), and a title that reads like a fallback
            // "variant N" is never used, so it cannot collide with an untitled lane's name.
            const trimmedTitle=variant=>typeof variant.title==='string'?variant.title.trim():'';
            function dotName(entry,entries) {
                const title=trimmedTitle(entry.variant);
                return title && !/^variant\s+\d+$/i.test(title) && entries.filter(other=>trimmedTitle(other.variant)===title).length===1?title:`variant ${entry.number}`;
            }
            const runCount=variant=>Number.isSafeInteger(variant.run_count) && variant.run_count>0?variant.run_count:0;
            function laneFor(entry) {
                let lane=lanes.get(entry.key);
                if(!lane) {
                    // Concept lane (app.js a598): the "−" dot, "<name> · N runs", then the repeat-run dot (D26).
                    const node=el('div',undefined,`variant-lane${entry.kind==='archived'?' library-variant-archived':''}`);const button=createButton('−','variant-dot');const label=el('span');
                    const runDot=createButton('','run-dot');runDot.hidden=true;
                    node.append(button,label,runDot);lane={node,button,label,runDot,text:document.createTextNode(''),entry};label.append(lane.text);
                    if(entry.kind==='archived') {lane.restore=createButton('Restore','library-variant-restore');label.append(lane.restore);content.listen(lane.restore,'click',()=>restorePair(lane.entry.variant.id,lane.restore));}
                    content.listen(button,'click',()=>select(lane.entry.variant.id));content.listen(runDot,'click',()=>openRunDot(lane));lanes.set(entry.key,lane);
                }
                lane.entry=entry;return lane;
            }
            updateBranches=state=>{
                const entries=laneEntries(),keep=new Set(entries.map(entry=>entry.key)),active=document.activeElement;
                for(const [key,lane] of lanes) if(!keep.has(key)) {lane.node.remove();lanes.delete(key);}
                let previous=positiveLane;
                for(const [index,entry] of entries.entries()) {
                    const lane=laneFor(entry),name=laneName(entry);
                    lane.node.style.setProperty('--lane-offset',`${38+index*47}px`);
                    const named=entry.kind==='separate'?'the saved negative variant outside the loaded list':dotName(entry,entries);
                    lane.button.setAttribute('aria-label',entry.kind==='separate'?'Select the saved negative variant outside the loaded list':`Select negative ${named}`);
                    lane.button.setAttribute('aria-pressed',String(activePairId===entry.variant.id));lane.button.disabled=savedReadState!=='current';
                    lane.text.textContent=`${name} · ${countOf(runCount(entry.variant),'run')}${entry.kind==='archived'?' · Archived':''}`;
                    // D26: the small dot appears only for a pair counted more than once.
                    lane.runDot.hidden=runCount(entry.variant)<=1;lane.runDot.setAttribute('aria-label',`Show repeat run for ${named}`);lane.runDot.disabled=savedReadState!=='current';
                    if(lane.restore) {lane.restore.setAttribute('aria-label',`Restore ${name}`);lane.restore.disabled=savedReadState!=='current' || restoringPairs.has(entry.variant.id);}
                    if(previous.nextSibling!==lane.node) previous.after(lane.node);
                    previous=lane.node;
                }
                if(active?.isConnected && document.activeElement!==active) active.focus({preventScroll:true});
                emptyNote.hidden=entries.length>0;
                variantCount.textContent=`${detail.variants.length} of ${countOf(detail.variant_total,'saved negative variant')}`;
                const archivedState=archivedLoader.state,target=moreTarget();
                const loadError=state.variantsError || (archivedShown && (archivedState.error || archivedState.variantsError));
                more.hidden=!target || !!loadError;more.setAttribute('aria-disabled',String(state.loadingVariants || archivedState.loading || archivedState.loadingVariants || savedReadState!=='current'));retry.hidden=!loadError;
                more.disabled=retry.disabled=savedReadState!=='current';all.disabled=savedReadState!=='current';addNegative.disabled=savedReadState!=='current';
                variantError.hidden=!loadError;variantError.textContent=!loadError?'':state.variantsError?'Negative variants could not be loaded. Retry to continue.':'Archived negative variants could not be loaded. Retry to continue.';
                const archivedCount=Number.isSafeInteger(detail.positive.archived_variant_count)?detail.positive.archived_variant_count:0;
                showArchived.textContent=`Show archived (${archivedCount})`;showArchived.setAttribute('aria-pressed',String(archivedShown));
                updateCompare();updateGalleryContext();
            };
            const updateMeta=()=>{imagesMeta.textContent=countOf(detail.positive.image_count ?? 0,'image');variantsMeta.textContent=countOf(detail.positive.variant_count ?? 0,'negative variant');};
            updateParent=()=>{
                const name=displayTitle(detail.positive);
                main.querySelector('h1').textContent=name;categoryChip.textContent=detail.positive.category || 'Uncategorized';updateMeta();
                send.setAttribute('aria-label',`Send prompt pair to ComfyUI: ${name}`);blocks?.setTitle(name);
            };
            let activePairId;
            updateParent();updateBranches(state);
            branches.append(emptyNote);
            const lower=el('div',undefined,'timeline-lower');lower.append(el('span','Green + positive · red − negative · small dot repeat run','small'),all);
            variants.append(branches,variantCount,variantError,restoreStatus,restoreAction,more,retry,lower);
            // The concept's shared gallery (app.js a616-621): its heading names the selected variant; the embedded
            // collection renders the PR C media cards. Recorded executions (#209) stay below it.
            const context=el('span','','small');context.setAttribute('role','status');
            const galleryTitle=el('h2','Shared gallery');galleryTitle.tabIndex=-1;
            const galleryHeading=el('div',undefined,'panel-heading');galleryHeading.append(galleryTitle,context);
            const gallery=el('section',undefined,'history-gallery');gallery.setAttribute('aria-label','Shared gallery');gallery.append(galleryHeading);
            const images=el('div',undefined,'library-linked-images');gallery.append(images);
            const recorded=el('div',undefined,'library-recorded-container');
            body.append(summary,variants,gallery,recorded);
            const recordFor=pairId=>detail.variants.find(variant=>variant.id===pairId) || archivedList().find(variant=>variant.id===pairId) || (separate()?.id===pairId?separate():null);
            const archivedSelection=pairId=>archivedList().some(variant=>variant.id===pairId) || (separate()?.id===pairId && separate().archived===true);
            function rolesFor(pairId) {
                if(pairId===null || detail.variants.some(variant=>variant.id===pairId)) return libraryPromptRoles(detail,pairId);
                const outside=separate();if(outside?.id===pairId) return libraryPromptRoles(detail,pairId,outside);
                return libraryPromptRoles({...detail,variants:[...detail.variants,...archivedList()]},pairId);
            }
            function updateSend() {
                const roles=activePairId===null || activePairId===undefined?{}:promptRoles(rolesFor(activePairId));
                send.disabled=savedReadState!=='current' || !Object.hasOwn(roles,'positive') || !Object.hasOwn(roles,'negative');
            }
            function setParentState(parent) {
                if(content.signal.aborted || (!parent.loading && !parent.error && !parent.detail)) return;
                parentStatus.hidden=false;parentStatus.setAttribute('role',parent.error?'alert':'status');retryParent.hidden=true;reloadSaved.hidden=true;
                if(parent.loading) {savedReadState='validating';parentStatus.textContent='Revalidating saved Library details…';}
                else if(parent.error) {savedReadState='error';parentStatus.textContent='Saved Library details could not be revalidated. The saved selection and gallery have been retained.';retryParent.hidden=false;}
                else {
                    const fresh=parent.detail;
                    if(fresh.positive.id!==positiveId || fresh.positive.text!==ownerText) {
                        savedReadState='stale';parentStatus.textContent='Saved Library owner changed. This view retains the previous prompt and gallery. Reload the saved Library view to inspect current data.';reloadSaved.hidden=false;
                    } else {
                        const observed=new Map(fresh.variants.map(variant=>[variant.id,variant]));const loaded=new Set(detail.variants.map(variant=>variant.id));const additional=fresh.variants.some(variant=>!loaded.has(variant.id));let changed=false,unobserved=false;
                        const variants=detail.variants.map(variant=>{
                            const current=observed.get(variant.id);
                            if(!current) {unobserved=true;return variant;}
                            if(current.negative_text!==variant.negative_text || current.explicit_empty!==variant.explicit_empty) {changed=true;return variant;}
                            return {...variant,...current};
                        });
                        detail={...detail,positive:fresh.positive,variants};categoryChip.textContent=fresh.positive.category || 'Uncategorized';updateMeta();
                        savedReadState=changed || unobserved || additional?'stale':'current';
                        parentStatus.textContent=changed?'A saved negative changed identity. This view retains its previous selection and gallery. Reload the saved Library view to inspect current data.':unobserved?(fresh.variants_has_more?'Some loaded saved negatives were not observed in this bounded revalidation page. Reload the saved Library view to validate them.':'Some loaded saved negatives were not present in the revalidation result. This view retains its previous selection and gallery.'):additional?'Saved Library choices changed. This view retains its previous selection and gallery. Reload the saved Library view to inspect current choices.':'Saved Library details revalidated.';
                        reloadSaved.hidden=savedReadState==='current';
                    }
                }
                updateBranches(loader.state);blocks?.setState({prompts:rolesFor(activePairId ?? null),actionsEnabled:savedReadState==='current'});updateSend();
            }
            async function retrySavedParent() {
                parentRequest?.abort();const request=parentRequest=new AbortController(),generation=++parentGeneration;setParentState({loading:true});
                try {
                    const fresh=await api.get(`/prompt_manager/library/${positiveId}?variant_limit=40`,{signal:request.signal});
                    if(content.signal.aborted || generation!==parentGeneration) return;
                    if(!fresh?.positive || !Number.isSafeInteger(fresh.positive.id) || typeof fresh.positive.text!=='string' || !Array.isArray(fresh.variants) || fresh.variants.some(variant=>!Number.isSafeInteger(variant.id) || typeof variant.negative_text!=='string' || typeof variant.explicit_empty!=='boolean' || variant.explicit_empty!==(variant.negative_text===''))) throw new TypeError('Invalid saved Library details');
                    setParentState({detail:fresh});
                } catch(error) {if(!content.signal.aborted && generation===parentGeneration)setParentState({error});}
            }
            content.listen(retryParent,'click',retrySavedParent);content.listen(reloadSaved,'click',()=>{pendingSelection=null;loader.load(positiveId);});
            if(!historyVisits.has(positiveId)) historyVisits.set(positiveId,{});
            const recordedView=mountRecordedHistory(recorded,{api,positiveId,parentScope:content,sender,visit:historyVisits.get(positiveId),onParentRevalidate:parent=>{
                if(content.signal.aborted) return;parentGeneration++;parentRequest?.abort();setParentState(parent);
            },onRecordedCount:count=>{runsMeta.hidden=count===null;runsMeta.textContent=count===null?'':countOf(count,'tracked run');}});
            // The heading carries the pair send, as in the concept; the saved texts keep their per-role actions.
            blocks=mountPromptBlocks(promptContainer,{prompts:libraryPromptRoles(detail),sender,parentScope:content,includePairAction:false,title:displayTitle(detail.positive)});
            /** The gallery heading's "<name> · same positive prompt" with the lane's own name, renamed with it (D47). */
            // With no saved variant the page shows every image of the positive, a state the concept never reaches.
            function updateGalleryContext() {
                if(activePairId===undefined) return;
                const lane=[...lanes.values()].find(item=>item.entry.variant.id===activePairId);
                const archived=activePairId!==null && archivedSelection(activePairId);
                context.textContent=activePairId===null?'All images for this positive, including unassigned images. Choose a saved negative variant to inspect or send it.':`${lane?laneName(lane.entry):'Selected saved negative variant'}${archived?' · Archived':''} · same positive prompt`;
            }
            function select(pairId,{force=false}={}) {
                if(savedReadState!=='current' || (!force && galleryScope && activePairId===pairId)) return;
                if(pairId!==null && !recordFor(pairId)) pairId=null;
                activePairId=pairId;blocks.setState({prompts:rolesFor(pairId),actionsEnabled:true});
                for(const lane of lanes.values()) lane.button.setAttribute('aria-pressed',String(lane.entry.variant.id===pairId));
                const archived=pairId!==null && archivedSelection(pairId);
                updateGalleryContext();
                galleryScope?.dispose();galleryScope=createScope();images.replaceChildren();
                // An archived pair's images are read in the archived scope; the gallery stays authoritative. The
                // embedded collection has no caption: the gallery heading names it (D47).
                mountImages(images,api,galleryScope,{embedded:true,focusTarget:galleryTitle,emptyText:pairId===null?undefined:'Saved variant · no runs yet.',fetchPage:({parameters,signal})=>{const query=new URLSearchParams(parameters);if(pairId!==null) query.set('pair_id',String(pairId));if(archived) query.set('archive','archived');return api.get(`/prompt_manager/library/${positiveId}/images?${query}`,{signal});}});
                updateSend();updateCompare();
            }
            /** A metadata receipt names no pair, so the page re-reads its own selection through the exact-ID route. */
            async function rereadSelectedPair() {
                const pairId=activePairId;if(pairId===null || pairId===undefined) return;
                try {
                    const value=await api.get(`/prompt_manager/pairs/${pairId}`,{signal:content.signal,cache:'no-store'});
                    const pair=value?.pair;
                    if(content.signal.aborted || activePairId!==pairId || pair?.id!==pairId || pair.positive_prompt_id!==positiveId || pair.positive_text!==detail.positive.text || typeof pair.negative_text!=='string') return;
                    if(!detail.variants.some(variant=>variant.id===pairId) && !archivedList().some(variant=>variant.id===pairId)) {selectedPairs.set(positiveId,pair);updateBranches(loader.state);}
                    blocks.setState({prompts:rolesFor(pairId),actionsEnabled:savedReadState==='current'});updateSend();
                } catch {
                    // The previous verified selection stays; a later explicit action reads it again.
                }
            }
            function showRestoreProblem(text,label,handler) {
                restoreStatus.textContent=text;restoreStatus.hidden=!text;restoreHandler=handler;restoreAction.textContent=label || '';restoreAction.hidden=!label;
            }
            /** Restore through the editor controller's fenced pair path; nothing restores silently. */
            async function restorePair(pairId,button) {
                // A rebuilt lane must not offer a second Restore while this one is in flight.
                if(restoringPairs.has(pairId)) return;
                restoringPairs.add(pairId);showRestoreProblem('');button.disabled=true;
                const controller=createLibraryEditorController({api,mode:'metadata',positiveId,pairId,onCommitted:invalidateLibraryAfterWrite,onReconciled:result=>{
                    rememberLibraryResult(result);pendingSelection=takeSavedSelection(positiveId);apply(result);
                    const dot=[...lanes.values()].find(lane=>lane.entry.kind!=='archived' && lane.entry.variant.id===pairId)?.button;dot?.focus();
                }});
                content.cleanup(()=>controller.dispose());
                const settle=()=>{
                    const {phase,error}=controller.state;
                    if(phase==='complete' || phase==='disposed' || content.signal.aborted) return;
                    if(button.isConnected) button.disabled=savedReadState!=='current';
                    if(phase==='storage-changed' || (phase==='saved-refresh-error' && error?.code==='storage-changed')) showRestoreProblem(phase==='storage-changed'?'Prompt storage was restored since this view loaded. Nothing was restored.':'Restored; Library refresh failed because prompt storage was restored. Reload the Library to see current data.','Reload Library',reloadView);
                    else if(phase==='saved-refresh-error') showRestoreProblem('Restored; Library refresh failed.','Retry refresh',async()=>{await controller.retryReconciliation();settle();});
                    else if(phase==='unknown-outcome') showRestoreProblem('Restore could not be confirmed. Retry sends the same restore once.','Retry',async()=>{await controller.retryUnconfirmed();settle();});
                    else showRestoreProblem(`Not restored: ${error?.message || 'The saved negative variant could not be read.'}`);
                };
                try {
                    await controller.initialize();
                    if(controller.state.phase==='ready') await controller.setArchive(false);
                } finally {
                    restoringPairs.delete(pairId);if(!content.signal.aborted) updateBranches(loader.state);
                }
                settle();
            }
            /** Same-parent reconciliation from one authoritative result; the separately verified pair stays outside the page. */
            function apply(result) {
                const kind=result.commit.kind,pair=result.pair,previous=detail.variants.map(variant=>variant.id);
                // An authoritative reconciled read supersedes any stale or pending revalidation.
                parentGeneration++;parentRequest?.abort();savedReadState='current';
                parentStatus.hidden=true;parentStatus.textContent='';retryParent.hidden=reloadSaved.hidden=true;
                loader.accept(result.detail);
                // A reused archived pair is shown where it lives, under Show archived; archiving itself never turns the toggle on.
                if(pair?.archived && kind!=='archive-pair') {archivedShown=true;archivedVisible.set(positiveId,true);}
                if(archivedShown) archivedLoader.load(positiveId);else archivedStale=true;
                if(kind==='metadata') {rereadSelectedPair();return;}
                if(kind==='archive-pair') {
                    if(selectedPairs.get(positiveId)?.id===pair.id) selectedPairs.delete(positiveId);
                    const index=previous.indexOf(pair.id),next=detail.variants[Math.min(Math.max(index,0),detail.variants.length-1)];
                    select(next?.id ?? null,{force:true});return;
                }
                if(pair) select(pair.id,{force:true});
            }
            current={activePairId:()=>activePairId ?? null,sendRoles:()=>{const roles=activePairId===null || activePairId===undefined?{}:promptRoles(rolesFor(activePairId));return Object.hasOwn(roles,'positive') && Object.hasOwn(roles,'negative')?roles:null;},apply};
            /** A bounded media dialog (D26): its gallery lives until the dialog closes or the view goes. */
            // One slot: each open replaces the previous dialog's scope, so repeated opens never pile up cleanups.
            let imagesDialogScope=null;content.cleanup(()=>{imagesDialogScope?.dispose();imagesDialogScope=null;});
            function openImagesDialog({title,intro,className,fetchPage,trigger}) {
                imagesDialogScope?.dispose();
                const dialogScope=createScope();imagesDialogScope=dialogScope;
                const grid=el('div',undefined,className);
                openContentDialog({scope:content,title,nodes:intro?[el('p',intro,'small'),grid]:[grid],trigger,onClose:()=>{dialogScope.dispose();if(imagesDialogScope===dialogScope) imagesDialogScope=null;}});
                // D47: the dialog's own title names its images, as in the concept (a1282-1304), so the collection has no
                // "Linked images" heading or count; a stranded focus returns to the dialog's close button.
                mountImages(grid,api,dialogScope,{embedded:true,focusTarget:grid.closest('dialog')?.querySelector('.dialog-header > button') ?? null,fetchPage});
            }
            const imagesPage=(id,query,signal)=>api.get(`/prompt_manager/library/${id}/images?${query}`,{signal});
            /** Concept "All images for this positive" (a1282-1290): every image of the positive, Load more bounded. */
            function openAllImagesDialog(id,trigger) {
                openImagesDialog({title:'All images for this positive',trigger,fetchPage:({parameters,signal})=>imagesPage(id,new URLSearchParams(parameters),signal)});
            }
            /** Concept "Repeat run" (a1291-1304): the pair's newest linked image only. */
            function openRepeatRun(pair,trigger) {
                openImagesDialog({title:'Repeat run',intro:'Same positive and negative, another execution.',className:'spaced-15',trigger,fetchPage:async({parameters,signal})=>{
                    const query=new URLSearchParams(parameters);query.set('pair_id',String(pair.id));query.set('limit','1');query.delete('cursor');
                    if(pair.archived===true) query.set('archive','archived');
                    const page=await imagesPage(positiveId,query,signal),images=Array.isArray(page?.images)?page.images.slice(0,1):page?.images;
                    return {...page,images,total:Array.isArray(images)?images.length:page?.total,has_more:false,next_cursor:null};
                }});
            }
            /** D26: the run dot opens Recorded run for the pair's newest recorded execution, else Repeat run. */
            const runLookups=new Set();
            async function openRunDot(lane) {
                const pair=lane.entry.variant,trigger=lane.runDot;
                if(savedReadState!=='current' || runLookups.has(pair.id)) return;
                runLookups.add(pair.id);let run=null,failed=false;
                try {
                    const value=await api.get(`/prompt_manager/library/${positiveId}/history/pairs/${pair.id}/newest`,{signal:content.signal,cache:'no-store'});
                    run=value?.run && typeof value.run.run_id==='string'?value.run:null;
                } catch {
                    failed=true;
                } finally {runLookups.delete(pair.id);}
                // The view may have gone stale while the lookup was in flight; a stale view opens nothing.
                if(content.signal.aborted || savedReadState!=='current') return;
                if(run && recordedView.openRun({runId:run.run_id,anchorId:run.anchor_id ?? null,contextId:run.context_id ?? null,trigger})) return;
                if(failed) showWorkspaceToast('Recorded runs could not be read. Showing the newest linked image.');
                openRepeatRun(pair,trigger);
            }
            content.listen(all,'click',()=>openAllImagesDialog(positiveId,all));
            const remembered=pendingSelection,outside=selectedPairs.get(positiveId);pendingSelection=null;
            if(outside?.archived && outside.id===remembered && !archivedShown) {archivedShown=true;archivedVisible.set(positiveId,true);}
            if(archivedShown) archivedLoader.load(positiveId);
            // Open on the first negative variant (concept navigate: state.variant = 0) unless a save just named a pair.
            let initial=detail.variants[0]?.id ?? null;
            if(detail.variants.some(variant=>variant.id===remembered)) initial=remembered;
            else if(outside && outside.id===remembered) {try {libraryPromptRoles(detail,remembered,outside);initial=remembered;} catch {selectedPairs.delete(positiveId);}}
            updateBranches(state);select(initial);
        }
        loader.load(positiveId);
    }
}
