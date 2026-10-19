import {createScope,createButton,createField,createSelect} from '../shared/toolkit/core.js';
import {createPromptSender} from './prompt-sender.mjs';
import {mountPromptBlocks} from './prompt-inspector.mjs';
import {mountImages} from './images.mjs';
import {createLibraryStream,createLibraryDetail,libraryPromptRoles} from './library-state.mjs';
import {promptRoles} from './prompt-blocks.mjs';
import {createLibraryEditor,showWorkspaceToast} from './library-editor.mjs';
import {createLibraryEditorController} from './library-editor-state.mjs';
import {layoutLibraryRows,visibleLibraryRows} from './library-layout.mjs';
import {createWorkspaceIcon} from './icons.mjs';
import {mountRecordedHistory} from './history.mjs';

let shelfVisit=null;
// Every confirmed receipt advances this; a shelf mounted before it never restores its cached stream.
let writeGeneration=0, focusHeadingOnMount=false, savedParentId=null;
const variantSelections=new Map();
const historyVisits=new Map();
// Exact-ID pair reads for selections outside the bounded variant page; never merged into it.
const selectedPairs=new Map();
// Parent metadata a reuse did not apply, kept for an explicit Edit metadata action.
const metadataDrafts=new Map();
const archivedVisible=new Map();
const ARCHIVED_MESSAGE='Archived. Find it under Show archived in Negative variants.';
function el(tag,text,className) {
    const node=document.createElement(tag);if(text!==undefined) node.textContent=text;if(className) node.className=className;return node;
}
function link(text,href,className) {const node=el('a',text,className);node.href=href;return node;}
function title(text) {const first=text.split('\n')[0].trim();return first.length>80?`${first.slice(0,80)}…`:first;}
/** The full stored title; the first-line fallback only when it is blank. */
function displayTitle(positive) {return typeof positive.title==='string' && positive.title.trim()?positive.title:title(positive.text);}
function focusHeading(main) {const heading=main.querySelector('h1');if(!heading)return;heading.tabIndex=-1;heading.focus();}
function headingActions(main,fallback) {
    const actions=el('div',undefined,'heading-actions');(main.querySelector('.workspace-heading') || fallback).append(actions);return actions;
}

/** A confirmed receipt makes cached shelf items, cursor and measured heights stale; filters and anchor survive. */
export function invalidateLibraryAfterWrite(commit) {
    if(!commit || typeof commit.kind!=='string' || !commit.receipt || typeof commit.receipt!=='object') throw new TypeError('Invalid editor commit');
    writeGeneration++;
    if(shelfVisit) shelfVisit={filters:shelfVisit.filters,anchor:shelfVisit.layout?.anchor ?? shelfVisit.anchor ?? null};
}
/** Remember the selection a reconciled save names, so the destination history opens on it. */
export function rememberLibraryResult(result) {
    const positive=result?.detail?.positive, kind=result?.commit?.kind;
    if(!positive || !Number.isSafeInteger(positive.id) || !Array.isArray(result.detail.variants) || typeof kind!=='string') throw new TypeError('Invalid reconciled Library result');
    if(kind==='create') savedParentId=positive.id;
    if(!result.pair || kind==='archive-pair') return;
    variantSelections.set(positive.id,result.pair.id);
    if(result.detail.variants.some(variant=>variant.id===result.pair.id)) selectedPairs.delete(positive.id);
    else selectedPairs.set(positive.id,result.pair);
    if(result.pair.archived) archivedVisible.set(positive.id,true);
}
/**
 * Toast copy from the receipt: the concept's save toast for New, Revision and Edit and its
 * Add-negative line (app.js 989, 1028); a reuse discloses what was not saved.
 */
function outcomeMessage(result) {
    const {kind,receipt}=result.commit;
    if(kind==='metadata') return 'Saved.';
    if(kind==='archive-pair') return ARCHIVED_MESSAGE;
    if(kind!=='create' && kind!=='add-negative') return null;
    if(receipt.positive_created) return 'Saved.';
    if(receipt.pair_created) return kind==='add-negative'?'Negative saved without adding another positive or gallery.'
        :'Saved as a new negative variant of an existing prompt. The title, category, tags, rating and notes you entered were not applied.';
    if(receipt.pair.archived) return 'This combination is an archived negative variant. It stays archived; restore it from Show archived in Negative variants.';
    return 'This prompt pair already exists. Showing the saved version.';
}
function rememberUnappliedMetadata(result,{outcome,draft}={}) {
    if(result.commit.kind!=='create' || !outcome?.metadataNotApplied || !draft) return;
    const {title:name,category,tags,rating,notes}=draft;metadataDrafts.set(result.detail.positive.id,{title:name,category,tags,rating,notes});
}
/** New prompt, from Library or Images: a saved prompt always opens its own history, as in the concept. */
function createNewPromptEditor(api,scope,main) {
    return createLibraryEditor({api,parentScope:scope,onCommitted:invalidateLibraryAfterWrite,onReconciled:(result,context)=>{
        rememberLibraryResult(result);rememberUnappliedMetadata(result,context);
        const message=outcomeMessage(result);if(message) showWorkspaceToast(message);
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

export function mountLibrary(main,api,parentScope,{positiveId}={}) {
    const scope=createScope();parentScope.cleanup(()=>scope.dispose());
    main.classList.add('library-main');
    const sender=createPromptSender({scope});
    if(positiveId!==undefined) mountHistory();
    else mountShelf();
    if(focusHeadingOnMount) {focusHeadingOnMount=false;focusHeading(main);}
    // Save the visit before removing the class that defines the shelf's scroll range.
    scope.cleanup(()=>main.classList.remove('library-main'));

    function mountShelf() {
        const visit=shelfVisit, mountedGeneration=writeGeneration;
        // An invalidated visit keeps only filters and anchor: the stream restarts and heights are measured again.
        const restorable=visit?.state?visit:null;
        const filters={text:'',category:'',tags:[],sort:'newest',...visit?.filters};
        const section=el('section',undefined,'library-shelf');section.setAttribute('aria-label','Prompt library');
        const introduction=el('p','One positive. Reusable negatives. Every experiment stays connected.','intro');
        const toolbar=el('search',undefined,'library-toolbar');
        const search=el('input');search.type='search';search.placeholder='Search prompts and saved negatives…';search.value=filters.text;search.maxLength=500;
        const category=createSelect([['','All categories']], '');category.value=filters.category;
        const tags=el('input');tags.type='text';tags.placeholder='Comma-separated tags';tags.value=filters.tags.join(', ');tags.maxLength=2000;
        const sort=createSelect([['newest','Recently added'],['oldest','Oldest first'],['name','Prompt A–Z'],['rating','Highest rated']], '');sort.value=filters.sort;
        const refresh=createButton('Refresh library');
        toolbar.append(createField('Search prompts',search,'library-search'),createField('Category',category,'library-category'),createField('Tags',tags,'library-tags'),createField('Sort',sort,'library-sort'),refresh);
        const optionsStatus=el('p','','library-options-status');optionsStatus.hidden=true;optionsStatus.setAttribute('role','alert');
        const caption=el('p','','library-caption');caption.setAttribute('role','status');
        const grid=el('div',undefined,'library-grid');grid.setAttribute('role','list');grid.setAttribute('aria-label','Positive prompts');
        const message=el('p','','library-status');message.setAttribute('role','status');
        const continuation=el('div',undefined,'library-continuation');
        const more=createButton('Load more prompts');const retry=createButton('Retry prompt library');retry.hidden=true;
        const legacy=link('Current prompt library',window.ApiClient.resolveUrl('/prompt_manager/admin'),'workspace-link');
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
        (main.querySelector('.workspace-heading-copy') || section).append(introduction);
        continuation.append(message,retry,more);section.append(toolbar,optionsStatus,exportStatus,caption,grid,continuation,legacy);main.append(section);
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
        function cardFor(item,index) {
            const article=el('article',undefined,'library-card');article.dataset.promptId=item.id;article.setAttribute('role','listitem');
            const art=el('div',undefined,'library-card-art');art.setAttribute('aria-hidden','true');
            const previews=item.previews || [];
            for(const image of previews.slice(0,2)) {
                if(image.is_video && !image.thumbnail_url) art.append(el('span','Video preview','library-art-empty'));
                else {
                    const img=el('img');img.src=window.ApiClient.resolveUrl(image.thumbnail_url || image.url);img.alt='';img.loading='lazy';img.decoding='async';img.width=image.width || 800;img.height=image.height || 600;art.append(img);
                }
            }
            if(!previews.length) art.append(el('span','No available image previews','library-art-empty'));
            const content=el('div',undefined,'library-card-content');
            const heading=el('header',undefined,'library-card-title');
            const name=link(displayTitle(item),`#/library/${item.id}`,'library-title-link');const h2=el('h2');h2.append(name);
            heading.append(h2,el('span',item.category || 'Uncategorized','library-chip'));
            const prompts=el('div',undefined,'library-card-prompts');
            const blocks=mountPromptBlocks(prompts,{prompts:{positive:{status:'resolved',text:item.text,source:'library'}},sender,parentScope:scope,roles:['positive']});
            const stats=el('div',undefined,'library-card-meta');stats.append(el('span',item.variant_count?`${item.variant_count} saved negative variants`:'No saved negatives','library-chip library-chip-negative'),el('span',`${item.image_count} linked images`));
            const vocabulary=el('div',undefined,'library-tags');for(const tag of item.tags || []) vocabulary.append(el('span',tag,'library-chip'));
            content.append(heading,prompts,stats,vocabulary);
            const footer=el('footer',undefined,'library-card-footer');
            const rating=el('span',item.rating?'★'.repeat(item.rating)+'☆'.repeat(5-item.rating):'Unrated','library-rating');rating.setAttribute('role','img');rating.setAttribute('aria-label',item.rating?`Rated ${item.rating} out of 5`:'Unrated');footer.append(rating);
            const history=link('',`#/library/${item.id}`,'library-history-link');history.append(createWorkspaceIcon('branch'),el('span','Explore history'));footer.append(history);
            article.append(art,content,footer);article.dataset.index=index;return {node:article,blocks};
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
            if(dirty) {layout=layoutLibraryRows(state.items.map(item=>heights.get(item.id)??null),columnCount,{estimate:columnCount===1?448:468});dirty=false;}
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
        function focusCard(index,last=false) {
            if(index<0 || index>=state.items.length) return;
            if(dirty) {cancelAnimationFrame(frame);flush();}focusOverride=index;renderWindow();const card=cards.get(state.items[index].id).node;
            const controls=card.querySelectorAll('a,button');(last?controls[controls.length-1]:controls[0]).focus();card.scrollIntoView({block:'nearest'});focusOverride=-1;renderWindow();
        }
        scope.listen(grid,'keydown',event=>{
            const card=event.target.closest('article[data-prompt-id]');if(!card) return;
            const index=Number(card.dataset.index);
            if(event.target.matches('a') && ['Home','End','ArrowLeft','ArrowRight'].includes(event.key)) {event.preventDefault();focusCard(event.key==='Home'?0:event.key==='End'?state.items.length-1:index+(event.key==='ArrowLeft'?-1:1));}
            else if(event.key==='Tab') {
                const controls=card.querySelectorAll('a,button');const next=index+(event.shiftKey?-1:1);
                const edge=event.shiftKey?controls[0]:controls[controls.length-1];
                if(event.target===edge && state.items[next] && !cards.has(state.items[next].id)) {event.preventDefault();focusCard(next,event.shiftKey);}
            }
        });
        function render(next) {
            if(items!==next.items) {
                pendingAnchor ||= captureAnchor();items=next.items;indexesById=new Map(items.map((item,index)=>[item.id,index]));dirty=true;
                if(!items.length) {
                    anchor=null;pendingAnchor=null;heights.clear();layout={rows:[],height:0};
                    for(const card of cards.values())removeCard(card);
                    cards.clear();rowNodes.clear();spacers.clear();grid.replaceChildren();
                } else if(retainedAnchor) {pendingAnchor=retainedAnchor;retainedAnchor=null;}
            }
            state=next;
            caption.textContent=`${next.items.length} of ${next.total} positive prompts · Images are grouped by positive prompt`;
            const hidden=hiddenSave!==null && !next.loading && !next.error && !next.hasMore && !next.items.some(item=>item.id===hiddenSave)?' The saved prompt is hidden by the current filters.':'';
            message.textContent=(next.error?'Prompt library could not be loaded. Retry or refresh.':next.loading?'Loading prompt library…':!next.items.length?'No matching prompts. Try changing the filters.':!next.hasMore?'You’re all caught up.':'')+hidden;
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
                const values=[...new Set([...data.categories,filters.category].filter(Boolean))];category.replaceChildren(new Option('All categories',''),...values.map(value=>new Option(value,value)));category.value=filters.category;optionsStatus.hidden=true;
            } catch {if(!scope.signal.aborted && optionsRequest===request) {optionsStatus.textContent='Categories could not be loaded. Refresh library to retry.';optionsStatus.hidden=false;}}
        }
        const reset=()=>{restoreScrollY=null;return stream.reset(filters);};
        scope.listen(search,'input',scope.debounce(()=>{filters.text=search.value.trim();reset();},200));
        scope.listen(tags,'input',scope.debounce(()=>{filters.tags=[...new Set(tags.value.split(',').map(tag=>tag.trim()).filter(Boolean))];reset();},200));
        scope.listen(category,'change',()=>{filters.category=category.value;reset();});scope.listen(sort,'change',()=>{filters.sort=sort.value;reset();});
        scope.listen(refresh,'click',()=>{loadCategories();reset();});scope.listen(more,'click',()=>stream.loadMore());scope.listen(retry,'click',()=>stream.loadMore());
        scope.listen(window,'scroll',()=>{reconcileScroll();schedule();});
        // Mobile viewport resizing can adjust scroll before delivering its resize event.
        scope.listen(window,'resize',()=>{pendingAnchor ||= anchor;observedScrollY=window.scrollY;schedule();});
        scope.cleanup(()=>{
            const savedFilters={...filters,tags:[...filters.tags]},savedAnchor=pendingAnchor || anchor || captureAnchor();
            // A write while mounted makes this stream stale: keep only what still means something.
            shelfVisit=mountedGeneration!==writeGeneration?{filters:savedFilters,anchor:savedAnchor}:state?{filters:savedFilters,state,scrollY:window.scrollY,layout:{inlineSize,columns:columnCount,heights:[...heights],anchor:savedAnchor}}:null;
            stream.dispose();optionsRequest?.abort();observer?.disconnect();measurementObserver?.disconnect();cancelAnimationFrame(frame);cancelAnimationFrame(loadFrame);for(const card of cards.values())removeCard(card);cards.clear();});
        loadCategories();if(restorable) {render(stream.state);if(restorable.state.loading || restorable.state.error)stream.loadMore();} else reset();
    }

    function mountHistory() {
        const history=el('section',undefined,'library-history');history.setAttribute('aria-label','Prompt history');main.append(history);
        const body=el('div');history.append(body);
        // Concept heading: ← Prompt library, Edit metadata, then the selected pair's Send to ComfyUI.
        const actions=headingActions(main,history);
        const back=createButton('← Prompt library'),edit=createButton('Edit metadata'),send=createButton('','send');
        send.append(createWorkspaceIcon('send'),el('span','Send to ComfyUI'));send.disabled=true;actions.append(back,edit,send);
        let blocks,galleryScope,detail,contentScope,updateBranches,updateParent,current=null;
        scope.cleanup(()=>{blocks?.dispose();galleryScope?.dispose();contentScope?.dispose();});
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
            const message=outcomeMessage(result),id=result.detail.positive.id;
            if(message) showWorkspaceToast(message);
            if(id!==positiveId) {focusHeadingOnMount=true;window.location.hash=`#/library/${id}`;return;}
            if(current) current.apply(result);else loader.load(positiveId);
            focusHeading(main);
        }
        /** Explicit exit after restored storage: dispose the recorded view, then remount with a fresh visit. */
        function reloadView() {
            writeGeneration++;shelfVisit=shelfVisit && {filters:shelfVisit.filters,anchor:null};
            variantSelections.delete(positiveId);selectedPairs.delete(positiveId);metadataDrafts.delete(positiveId);
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
            const subtitle=el('p','One positive prompt. Reusable negative variants. One shared gallery.','intro');
            const summary=el('article',undefined,'library-surface');const categoryChip=el('span','','library-chip');summary.append(categoryChip);
            const promptContainer=el('div');promptContainer.setAttribute('role','region');promptContainer.setAttribute('aria-label','Saved prompt texts');summary.append(promptContainer);
            const parentStatus=el('p','','library-parent-status muted');parentStatus.hidden=true;parentStatus.setAttribute('role','status');
            const retryParent=createButton('Retry saved Library details'),reloadSaved=createButton('Reload saved Library view');retryParent.hidden=reloadSaved.hidden=true;summary.append(parentStatus,retryParent,reloadSaved);
            const ownerText=detail.positive.text;let savedReadState='current',parentRequest,parentGeneration=0;
            content.cleanup(()=>{parentGeneration++;parentRequest?.abort();});
            const all=createButton('All images for this positive');
            const variants=el('section',undefined,'library-surface library-variants');variants.setAttribute('aria-label','Saved negative variants');
            const panelHeading=el('div',undefined,'library-panel-heading'),panelCopy=el('div'),rowActions=el('div',undefined,'row-actions');
            panelCopy.append(el('h2','Negative variants'),el('p','Choose a saved negative to inspect its full text and filter the shared gallery.','muted'));
            const addNegative=createButton('+ Add negative'),showArchived=createButton('');showArchived.setAttribute('aria-pressed','false');
            rowActions.append(addNegative,showArchived);panelHeading.append(panelCopy,rowActions);variants.append(panelHeading);
            const branches=el('div',undefined,'library-branches');const positiveLane=el('p',undefined,'library-positive-lane');positiveLane.append(el('span','Positive prompt · unchanged'));branches.append(positiveLane);
            const lanes=new Map();const emptyNote=el('p','No saved negative variants. Existing image associations remain available.','muted');
            const variantCount=el('p','','muted');variantCount.setAttribute('role','status');
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
            function laneFor(entry) {
                let lane=lanes.get(entry.key);
                if(!lane) {
                    const node=el('div',undefined,`library-variant-lane${entry.kind==='archived'?' library-variant-archived':''}`);const button=createButton('−','library-variant-dot');const label=el('span');
                    node.append(button,label);lane={node,button,label,text:document.createTextNode(''),entry};label.append(lane.text);
                    if(entry.kind==='archived') {lane.restore=createButton('Restore','library-variant-restore');label.append(lane.restore);content.listen(lane.restore,'click',()=>restorePair(lane.entry.variant.id,lane.restore));}
                    content.listen(button,'click',()=>select(lane.entry.variant.id));lanes.set(entry.key,lane);
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
                    lane.button.setAttribute('aria-label',entry.kind==='separate'?'Select the saved negative variant outside the loaded list':`Select negative variant ${entry.number}`);
                    lane.button.setAttribute('aria-pressed',String(activePairId===entry.variant.id));lane.button.disabled=savedReadState!=='current';
                    lane.text.textContent=`${name} · ${entry.variant.image_count} linked images${entry.kind==='archived'?' · Archived':''}`;
                    if(lane.restore) {lane.restore.setAttribute('aria-label',`Restore ${name}`);lane.restore.disabled=savedReadState!=='current' || restoringPairs.has(entry.variant.id);}
                    if(previous.nextSibling!==lane.node) previous.after(lane.node);
                    previous=lane.node;
                }
                if(active?.isConnected && document.activeElement!==active) active.focus({preventScroll:true});
                emptyNote.hidden=entries.length>0;
                variantCount.textContent=`${detail.variants.length} of ${detail.variant_total} saved negative variants`;
                const archivedState=archivedLoader.state,target=moreTarget();
                const loadError=state.variantsError || (archivedShown && (archivedState.error || archivedState.variantsError));
                more.hidden=!target || !!loadError;more.setAttribute('aria-disabled',String(state.loadingVariants || archivedState.loading || archivedState.loadingVariants || savedReadState!=='current'));retry.hidden=!loadError;
                more.disabled=retry.disabled=savedReadState!=='current';all.disabled=savedReadState!=='current';addNegative.disabled=savedReadState!=='current';
                variantError.hidden=!loadError;variantError.textContent=!loadError?'':state.variantsError?'Negative variants could not be loaded. Retry to continue.':'Archived negative variants could not be loaded. Retry to continue.';
                const archivedCount=Number.isSafeInteger(detail.positive.archived_variant_count)?detail.positive.archived_variant_count:0;
                showArchived.textContent=`Show archived (${archivedCount})`;showArchived.setAttribute('aria-pressed',String(archivedShown));
            };
            updateParent=()=>{
                main.querySelector('h1').textContent=displayTitle(detail.positive);categoryChip.textContent=detail.positive.category || 'Uncategorized';
                send.setAttribute('aria-label',`Send prompt pair to ComfyUI: ${displayTitle(detail.positive)}`);
            };
            let activePairId;
            updateParent();updateBranches(state);
            branches.append(emptyNote);
            const note=el('p','Saved combinations, not an execution timeline.','muted');
            const variantFooter=el('div',undefined,'library-variant-footer'),variantSummary=el('div',undefined,'library-variant-summary');variantSummary.append(variantCount,note);variantFooter.append(variantSummary,all);
            variants.append(branches,variantError,restoreStatus,restoreAction,more,retry,variantFooter);
            const context=el('p','','library-variant-context');context.setAttribute('role','status');
            const gallery=el('section',undefined,'library-shared-gallery');gallery.setAttribute('aria-label','Shared gallery');gallery.append(el('h2','Shared gallery'),context);
            const images=el('div',undefined,'library-linked-images');gallery.append(images);
            const recorded=el('div',undefined,'library-recorded-container');
            body.append(subtitle,summary,variants,recorded,gallery);
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
                        detail={...detail,positive:fresh.positive,variants};categoryChip.textContent=fresh.positive.category || 'Uncategorized';
                        savedReadState=changed || unobserved || additional?'stale':'current';
                        parentStatus.textContent=changed?'A saved negative changed identity. This view retains its previous selection and gallery. Reload the saved Library view to inspect current data.':unobserved?(fresh.variants_has_more?'Some loaded saved negatives were not observed in this bounded revalidation page. Reload the saved Library view to validate them.':'Some loaded saved negatives were not present in the revalidation result. This view retains its previous selection and gallery.'):additional?'Saved Library choices changed. This view retains its previous selection and gallery. Reload the saved Library view to inspect current choices.':'Saved Library details revalidated.';
                        reloadSaved.hidden=savedReadState==='current';
                    }
                }
                updateBranches(loader.state);blocks?.setState({prompts:rolesFor(variantSelections.get(positiveId)??null),actionsEnabled:savedReadState==='current'});updateSend();
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
            content.listen(retryParent,'click',retrySavedParent);content.listen(reloadSaved,'click',()=>{variantSelections.delete(positiveId);loader.load(positiveId);});
            if(!historyVisits.has(positiveId)) historyVisits.set(positiveId,{});
            mountRecordedHistory(recorded,{api,positiveId,parentScope:content,sender,visit:historyVisits.get(positiveId),onParentRevalidate:parent=>{
                if(content.signal.aborted) return;parentGeneration++;parentRequest?.abort();setParentState(parent);
            }});
            // The heading carries the pair send, as in the concept; the saved texts keep their per-role actions.
            blocks=mountPromptBlocks(promptContainer,{prompts:libraryPromptRoles(detail),sender,parentScope:content,includePairAction:false});
            function select(pairId,{force=false}={}) {
                if(savedReadState!=='current' || (!force && galleryScope && activePairId===pairId)) return;
                if(pairId!==null && !recordFor(pairId)) pairId=null;
                activePairId=pairId;variantSelections.set(positiveId,pairId);blocks.setState({prompts:rolesFor(pairId),actionsEnabled:true});
                for(const lane of lanes.values()) lane.button.setAttribute('aria-pressed',String(lane.entry.variant.id===pairId));all.setAttribute('aria-pressed',String(pairId===null));
                const index=detail.variants.findIndex(variant=>variant.id===pairId),archived=pairId!==null && archivedSelection(pairId);
                const lane=[...lanes.values()].find(item=>item.entry.variant.id===pairId);
                context.textContent=pairId===null?'All images for this positive, including unassigned images. Choose a saved negative variant to inspect or send it.':index>=0?`Negative variant ${index+1} · same positive prompt`:`${lane?laneName(lane.entry):'Selected saved negative variant'}${archived?' · Archived':''} · same positive prompt`;
                galleryScope?.dispose();galleryScope=createScope();images.replaceChildren();
                // An archived pair's images are read in the archived scope; the gallery stays authoritative.
                mountImages(images,api,galleryScope,{embedded:true,fetchPage:({parameters,signal})=>{const query=new URLSearchParams(parameters);if(pairId!==null) query.set('pair_id',String(pairId));if(archived) query.set('archive','archived');return api.get(`/prompt_manager/library/${positiveId}/images?${query}`,{signal});}});
                updateSend();
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
                    rememberLibraryResult(result);apply(result);
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
            content.listen(all,'click',()=>select(null));
            const remembered=variantSelections.get(positiveId),outside=selectedPairs.get(positiveId);
            if(outside?.archived && outside.id===remembered && !archivedShown) {archivedShown=true;archivedVisible.set(positiveId,true);}
            if(archivedShown) archivedLoader.load(positiveId);
            let initial=null;
            if(detail.variants.some(variant=>variant.id===remembered)) initial=remembered;
            else if(outside?.id===remembered) {try {libraryPromptRoles(detail,remembered,outside);initial=remembered;} catch {selectedPairs.delete(positiveId);}}
            updateBranches(state);select(initial);
        }
        loader.load(positiveId);
    }
}
