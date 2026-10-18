import {createScope,createButton,createField,createSelect} from '../shared/toolkit/core.js';
import {createPromptSender} from './prompt-sender.mjs';
import {mountPromptBlocks} from './prompt-inspector.mjs';
import {mountImages} from './images.mjs';
import {createLibraryStream,createLibraryDetail,libraryPromptRoles} from './library-state.mjs';
import {layoutLibraryRows,visibleLibraryRows} from './library-layout.mjs';
import {createWorkspaceIcon} from './icons.mjs';
import {mountRecordedHistory} from './history.mjs';

let shelfVisit=null;
const variantSelections=new Map();
const historyVisits=new Map();
function el(tag,text,className) {
    const node=document.createElement(tag);if(text!==undefined) node.textContent=text;if(className) node.className=className;return node;
}
function link(text,href,className) {const node=el('a',text,className);node.href=href;return node;}
function title(text) {const first=text.split('\n')[0].trim();return first.length>80?`${first.slice(0,80)}…`:first;}

export function mountLibrary(main,api,parentScope,{positiveId}={}) {
    const scope=createScope();parentScope.cleanup(()=>scope.dispose());
    main.classList.add('library-main');
    const sender=createPromptSender({scope});
    if(positiveId!==undefined) mountHistory();
    else mountShelf();
    // Save the visit before removing the class that defines the shelf's scroll range.
    scope.cleanup(()=>main.classList.remove('library-main'));

    function mountShelf() {
        const visit=shelfVisit;const filters={text:'',category:'',tags:[],sort:'newest',...visit?.filters};
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
        (main.querySelector('.workspace-heading-copy') || section).append(introduction);
        continuation.append(message,retry,more);section.append(toolbar,optionsStatus,caption,grid,continuation,legacy);main.append(section);
        const cards=new Map(), rowNodes=new Map(), spacers=new Map(), heights=new Map(), pendingMeasurements=new Map();
        let state,frame=0,loadFrame=0,observer,optionsRequest,focusOverride=-1;
        const columns=()=>window.innerWidth>=1700?3:window.innerWidth<=700?1:2;
        const readInlineSize=()=>Math.round(grid.getBoundingClientRect().width*64)/64;
        let inlineSize=readInlineSize(), columnCount=columns(), layout={rows:[],height:0}, dirty=true, items, indexesById=new Map();
        let anchor=visit?.layout?.anchor || null, pendingAnchor=anchor, observedScrollY=window.scrollY;
        let restoreScrollY=Number.isFinite(visit?.scrollY) && visit.scrollY>=0?visit.scrollY:null;
        if(visit?.layout?.inlineSize===inlineSize && visit.layout.columns===columnCount) for(const [id,height] of visit.layout.heights) heights.set(id,height);
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
            const name=link(title(item.text),`#/library/${item.id}`,'library-title-link');const h2=el('h2');h2.append(name);
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
                }
            }
            state=next;
            caption.textContent=`${next.items.length} of ${next.total} positive prompts · Images are grouped by positive prompt`;
            message.textContent=next.error?'Prompt library could not be loaded. Retry or refresh.':next.loading?'Loading prompt library…':!next.items.length?'No matching prompts. Try changing the filters.':!next.hasMore?'You’re all caught up.':'';
            message.setAttribute('role',next.error?'alert':'status');retry.hidden=!next.error;more.hidden=!next.hasMore || !!next.error;more.setAttribute('aria-disabled',String(next.loading));
            schedule();scheduleLoad();
        }
        const stream=createLibraryStream({api,onChange:render,restore:visit?{filters,state:visit.state}:undefined});
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
        scope.cleanup(()=>{shelfVisit=state?{filters:{...filters,tags:[...filters.tags]},state,scrollY:window.scrollY,layout:{inlineSize,columns:columnCount,heights:[...heights],anchor:pendingAnchor || anchor || captureAnchor()}}:null;stream.dispose();optionsRequest?.abort();observer?.disconnect();measurementObserver?.disconnect();cancelAnimationFrame(frame);cancelAnimationFrame(loadFrame);for(const card of cards.values())removeCard(card);cards.clear();});
        loadCategories();if(visit) {render(stream.state);if(visit.state.loading || visit.state.error)stream.loadMore();} else reset();
    }

    function mountHistory() {
        const history=el('section',undefined,'library-history');history.setAttribute('aria-label','Prompt history');main.append(history);
        const back=link('Back to library','#/library','library-back');
        const body=el('div');history.append(back,body);
        let blocks,galleryScope,detail,contentScope,updateBranches;
        scope.cleanup(()=>{blocks?.dispose();galleryScope?.dispose();contentScope?.dispose();});
        const loader=createLibraryDetail({api,onChange:render});scope.cleanup(()=>loader.dispose());
        function render(state) {
            if(detail && state.detail && !state.loading && !state.error) {detail=state.detail;updateBranches(state);return;}
            contentScope?.dispose();contentScope=createScope();detail=null;updateBranches=null;
            blocks?.dispose();blocks=null;galleryScope?.dispose();galleryScope=null;body.replaceChildren();
            if(state.loading || state.error) {
                const message=el('p',state.loading?'Loading prompt history…':'Prompt history could not be loaded.','library-status');message.setAttribute('role',state.error?'alert':'status');body.append(message);
                if(state.error) {const retry=createButton('Retry prompt history');contentScope.listen(retry,'click',()=>loader.load(positiveId));body.append(retry);}return;
            }
            detail=state.detail;main.querySelector('h1').textContent=title(detail.positive.text);
            const subtitle=el('p','One positive prompt. Reusable negative variants. One shared gallery.','intro');
            const summary=el('article',undefined,'library-surface');const categoryChip=el('span',detail.positive.category || 'Uncategorized','library-chip');summary.append(categoryChip);
            const promptContainer=el('div');promptContainer.setAttribute('role','region');promptContainer.setAttribute('aria-label','Saved prompt texts');summary.append(promptContainer);
            const parentStatus=el('p','','library-parent-status muted');parentStatus.hidden=true;parentStatus.setAttribute('role','status');
            const retryParent=createButton('Retry saved Library details'),reloadSaved=createButton('Reload saved Library view');retryParent.hidden=reloadSaved.hidden=true;summary.append(parentStatus,retryParent,reloadSaved);
            const ownerText=detail.positive.text;let savedReadState='current',parentRequest,parentGeneration=0;
            contentScope.cleanup(()=>{parentGeneration++;parentRequest?.abort();});
            const all=createButton('All images for this positive');
            const variants=el('section',undefined,'library-surface library-variants');variants.setAttribute('aria-label','Saved negative variants');
            variants.append(el('h2','Negative variants'),el('p','Choose a saved negative to inspect its full text and filter the shared gallery.','muted'));
            const branches=el('div',undefined,'library-branches');const positiveLane=el('p',undefined,'library-positive-lane');positiveLane.append(el('span','Positive prompt · unchanged'));branches.append(positiveLane);
            const choices=[];
            const variantCount=el('p','','muted');variantCount.setAttribute('role','status');
            const variantError=el('p','','muted');variantError.setAttribute('role','alert');variantError.hidden=true;
            const more=createButton('Load more negative variants');const retry=createButton('Retry negative variants');retry.hidden=true;
            contentScope.listen(more,'click',()=>{if(savedReadState==='current')loader.loadMore();});contentScope.listen(retry,'click',()=>{if(savedReadState==='current')loader.loadMore();});
            updateBranches=state=>{
                const known=new Set(choices.map(choice=>choice.variant.id));
                for(const [index,variant] of detail.variants.entries()) if(!known.has(variant.id)) {
                    const lane=el('div',undefined,'library-variant-lane');lane.style.setProperty('--lane-offset',`${38+index*47}px`);const button=createButton('−','library-variant-dot');button.setAttribute('aria-label',`Select negative variant ${index+1}`);
                    button.setAttribute('aria-pressed',String(variantSelections.get(positiveId)===variant.id));
                    const label=el('span');lane.append(button,label);branches.append(lane);choices.push({button,variant,label});
                    contentScope.listen(button,'click',()=>select(variant.id));
                }
                for(const [index,choice] of choices.entries()) {
                    choice.variant=detail.variants.find(variant=>variant.id===choice.variant.id) || choice.variant;
                    const variant=choice.variant;choice.label.textContent=`${variant.title || (variant.explicit_empty?'Explicitly empty negative':`Negative variant ${index+1}`)} · ${variant.image_count} linked images`;
                    choice.button.disabled=savedReadState!=='current';
                }
                variantCount.textContent=`${detail.variants.length} of ${detail.variant_total} saved negative variants`;
                more.hidden=!detail.variants_has_more || !!state.variantsError;more.setAttribute('aria-disabled',String(state.loadingVariants || savedReadState!=='current'));retry.hidden=!state.variantsError;
                more.disabled=retry.disabled=savedReadState!=='current';all.disabled=savedReadState!=='current';
                variantError.hidden=!state.variantsError;variantError.textContent=state.variantsError?'Negative variants could not be loaded. Retry to continue.':'';
            };
            updateBranches(state);
            if(!detail.variants.length) branches.append(el('p','No saved negative variants. Existing image associations remain available.','muted'));
            const note=el('p','Saved combinations, not an execution timeline.','muted');
            const variantFooter=el('div',undefined,'library-variant-footer'),variantSummary=el('div',undefined,'library-variant-summary');variantSummary.append(variantCount,note);variantFooter.append(variantSummary,all);
            variants.append(branches,variantError,more,retry,variantFooter);
            const context=el('p','','library-variant-context');context.setAttribute('role','status');
            const gallery=el('section',undefined,'library-shared-gallery');gallery.setAttribute('aria-label','Shared gallery');gallery.append(el('h2','Shared gallery'),context);
            const images=el('div',undefined,'library-linked-images');gallery.append(images);let activePairId;
            const recorded=el('div',undefined,'library-recorded-container');
            body.append(subtitle,summary,variants,recorded,gallery);
            function setParentState(parent) {
                if(contentScope.signal.aborted || (!parent.loading && !parent.error && !parent.detail)) return;
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
                updateBranches(loader.state);blocks?.setState({prompts:libraryPromptRoles(detail,variantSelections.get(positiveId)??null),actionsEnabled:savedReadState==='current'});
            }
            async function retrySavedParent() {
                parentRequest?.abort();const request=parentRequest=new AbortController(),generation=++parentGeneration;setParentState({loading:true});
                try {
                    const fresh=await api.get(`/prompt_manager/library/${positiveId}?variant_limit=40`,{signal:request.signal});
                    if(contentScope.signal.aborted || generation!==parentGeneration) return;
                    if(!fresh?.positive || !Number.isSafeInteger(fresh.positive.id) || typeof fresh.positive.text!=='string' || !Array.isArray(fresh.variants) || fresh.variants.some(variant=>!Number.isSafeInteger(variant.id) || typeof variant.negative_text!=='string' || typeof variant.explicit_empty!=='boolean' || variant.explicit_empty!==(variant.negative_text===''))) throw new TypeError('Invalid saved Library details');
                    setParentState({detail:fresh});
                } catch(error) {if(!contentScope.signal.aborted && generation===parentGeneration)setParentState({error});}
            }
            contentScope.listen(retryParent,'click',retrySavedParent);contentScope.listen(reloadSaved,'click',()=>{variantSelections.delete(positiveId);loader.load(positiveId);});
            if(!historyVisits.has(positiveId)) historyVisits.set(positiveId,{});
            mountRecordedHistory(recorded,{api,positiveId,parentScope:contentScope,sender,visit:historyVisits.get(positiveId),onParentRevalidate:parent=>{
                if(contentScope.signal.aborted) return;parentGeneration++;parentRequest?.abort();setParentState(parent);
            }});
            blocks=mountPromptBlocks(promptContainer,{prompts:libraryPromptRoles(detail),sender,parentScope:contentScope});
            function select(pairId) {
                if(savedReadState!=='current' || (galleryScope && activePairId===pairId)) return;activePairId=pairId;
                variantSelections.set(positiveId,pairId);blocks.setState({prompts:libraryPromptRoles(detail,pairId),actionsEnabled:true});
                for(const choice of choices) choice.button.setAttribute('aria-pressed',String(choice.variant.id===pairId));all.setAttribute('aria-pressed',String(pairId===null));
                context.textContent=pairId===null?'All images for this positive, including unassigned images. Choose a saved negative variant to inspect or send it.':`Negative variant ${detail.variants.findIndex(variant=>variant.id===pairId)+1} · same positive prompt`;
                galleryScope?.dispose();galleryScope=createScope();images.replaceChildren();
                mountImages(images,api,galleryScope,{embedded:true,fetchPage:({parameters,signal})=>{const query=new URLSearchParams(parameters);if(pairId!==null) query.set('pair_id',String(pairId));return api.get(`/prompt_manager/library/${positiveId}/images?${query}`,{signal});}});
            }
            contentScope.listen(all,'click',()=>select(null));const remembered=variantSelections.get(positiveId);select(detail.variants.some(variant=>variant.id===remembered)?remembered:null);
        }
        loader.load(positiveId);
    }
}
