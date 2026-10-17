import {createScope,createButton,createField,createSelect} from '../shared/toolkit/core.js';
import {createPromptSender} from './prompt-sender.mjs';
import {mountPromptBlocks} from './prompt-inspector.mjs';
import {mountImages} from './images.mjs';
import {createLibraryStream,createLibraryDetail,libraryPromptRoles,libraryWindow} from './library-state.mjs';

let shelfVisit=null;
const variantSelections=new Map();
function el(tag,text,className) {
    const node=document.createElement(tag);if(text!==undefined) node.textContent=text;if(className) node.className=className;return node;
}
function link(text,href,className) {const node=el('a',text,className);node.href=href;return node;}
function title(text) {const first=text.split('\n')[0].trim();return first.length>80?`${first.slice(0,80)}…`:first;}
function branchIcon() {
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','1.8');
    const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d','M6 8v8m0-4h6a6 6 0 0 0 6-6M9 5a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm0 14a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM21 3a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z');svg.append(path);return svg;
}

export function mountLibrary(main,api,parentScope,{positiveId}={}) {
    const scope=createScope();parentScope.cleanup(()=>scope.dispose());
    main.classList.add('library-main');scope.cleanup(()=>main.classList.remove('library-main'));
    const sender=createPromptSender({scope});
    if(positiveId!==undefined) {mountHistory();return;}
    mountShelf();

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
        continuation.append(message,retry,more);section.append(introduction,toolbar,optionsStatus,legacy,caption,grid,continuation);main.append(section);
        const cards=new Map();let state,frame=0,loadFrame=0,observer,optionsRequest,focusOverride=-1,restoreFrame=0;
        const columns=()=>window.innerWidth>=1700?3:window.innerWidth<=700?1:2;
        function removeCard(card) {card.blocks.dispose();card.node.remove();}
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
            const footer=el('footer',undefined,'library-card-footer');footer.append(el('span',item.rating?`${item.rating} / 5`:'Unrated','muted'));
            const history=link('',`#/library/${item.id}`,'library-history-link');history.append(branchIcon(),el('span','Explore history'));footer.append(history);
            article.append(art,content,footer);article.dataset.index=index;return {node:article,blocks};
        }
        function renderWindow() {
            if(!state || scope.signal.aborted) return;
            const active=document.activeElement?.closest('article[data-prompt-id]');
            const focused=focusOverride>=0?focusOverride:active?Number(active.dataset.index):-1;
            const size=columns();const top=Math.max(0,-grid.getBoundingClientRect().top);
            const indexes=libraryWindow(state.items.length,size,top,window.innerHeight,focused);const keep=new Set(indexes.map(index=>state.items[index].id));
            for(const [id,card] of cards) if(!keep.has(id)) {removeCard(card);cards.delete(id);}
            const children=[];let previousRow=-1;
            for(const index of indexes) {
                const row=Math.floor(index/size);
                if(index%size===0 && row>previousRow+1) {
                    const gap=el('div',undefined,'library-row-spacer');gap.setAttribute('aria-hidden','true');gap.style.blockSize=`${(row-previousRow-1)*578-18}px`;children.push(gap);
                }
                let card=cards.get(state.items[index].id);
                if(!card) {card=cardFor(state.items[index],index);cards.set(state.items[index].id,card);}
                card.node.setAttribute('aria-posinset',index+1);card.node.setAttribute('aria-setsize',state.total);children.push(card.node);previousRow=row;
            }
            const remaining=Math.ceil(state.items.length/size)-previousRow-1;
            if(remaining>0) {const gap=el('div',undefined,'library-row-spacer');gap.setAttribute('aria-hidden','true');gap.style.blockSize=`${remaining*578-18}px`;children.push(gap);}
            // Remove obsolete spacers first: moving a focused card past an old spacer loses focus.
            for(const child of [...grid.children]) if(!children.includes(child)) child.remove();
            let previous=null;
            for(const child of children) {if(previous?previous.nextElementSibling!==child:grid.firstElementChild!==child) grid.insertBefore(child,previous?previous.nextElementSibling:grid.firstChild);previous=child;}
        }
        const schedule=()=>{if(!frame) frame=requestAnimationFrame(()=>{frame=0;renderWindow();});};
        function focusCard(index,last=false) {
            if(index<0 || index>=state.items.length) return;
            focusOverride=index;renderWindow();const card=cards.get(state.items[index].id).node;
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
            state=next;
            caption.textContent=`${next.items.length} of ${next.total} positive prompts · Images are grouped by positive prompt`;
            message.textContent=next.error?'Prompt library could not be loaded. Retry or refresh.':next.loading?'Loading prompt library…':!next.items.length?'No matching prompts. Try changing the filters.':!next.hasMore?'You’re all caught up.':'';
            message.setAttribute('role',next.error?'alert':'status');retry.hidden=!next.error;more.hidden=!next.hasMore || !!next.error;more.setAttribute('aria-disabled',String(next.loading));
            renderWindow();scheduleLoad();
        }
        const stream=createLibraryStream({api,onChange:render,restore:visit?{filters,state:visit.state}:undefined});
        function maybeLoad() {
            if(!observer || !state?.hasMore || state.loading || state.error || scope.signal.aborted) return;
            const bounds=continuation.getBoundingClientRect();if(bounds.top<=window.innerHeight+250 && bounds.bottom>=0) stream.loadMore();
        }
        function scheduleLoad() {if(!loadFrame) loadFrame=requestAnimationFrame(()=>{loadFrame=0;maybeLoad();});}
        if(typeof IntersectionObserver==='function') {observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)) maybeLoad();},{rootMargin:'250px'});observer.observe(continuation);}
        async function loadCategories() {
            optionsRequest?.abort();const request=optionsRequest=new AbortController();
            try {
                const data=await api.get('/prompt_manager/library/options',{signal:request.signal});if(scope.signal.aborted || optionsRequest!==request) return;
                const values=[...new Set([...data.categories,filters.category].filter(Boolean))];category.replaceChildren(new Option('All categories',''),...values.map(value=>new Option(value,value)));category.value=filters.category;optionsStatus.hidden=true;
            } catch {if(!scope.signal.aborted && optionsRequest===request) {optionsStatus.textContent='Categories could not be loaded. Refresh library to retry.';optionsStatus.hidden=false;}}
        }
        const reset=()=>stream.reset(filters);
        scope.listen(search,'input',scope.debounce(()=>{filters.text=search.value.trim();reset();},200));
        scope.listen(tags,'input',scope.debounce(()=>{filters.tags=[...new Set(tags.value.split(',').map(tag=>tag.trim()).filter(Boolean))];reset();},200));
        scope.listen(category,'change',()=>{filters.category=category.value;reset();});scope.listen(sort,'change',()=>{filters.sort=sort.value;reset();});
        scope.listen(refresh,'click',()=>{loadCategories();reset();});scope.listen(more,'click',()=>stream.loadMore());scope.listen(retry,'click',()=>stream.loadMore());
        scope.listen(window,'scroll',schedule);scope.listen(window,'resize',schedule);
        scope.cleanup(()=>{shelfVisit=state?{filters:{...filters,tags:[...filters.tags]},state,scrollY:window.scrollY}:null;stream.dispose();optionsRequest?.abort();observer?.disconnect();cancelAnimationFrame(frame);cancelAnimationFrame(loadFrame);cancelAnimationFrame(restoreFrame);for(const card of cards.values()) removeCard(card);cards.clear();});
        loadCategories();if(visit) {render(stream.state);restoreFrame=requestAnimationFrame(()=>{if(!scope.signal.aborted) {window.scrollTo(0,visit.scrollY);renderWindow();scheduleLoad();}});if(visit.state.loading || visit.state.error) stream.loadMore();} else reset();
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
            const summary=el('article',undefined,'library-surface');summary.append(el('span',detail.positive.category || 'Uncategorized','library-chip'));
            const promptContainer=el('div');promptContainer.setAttribute('role','region');promptContainer.setAttribute('aria-label','Saved prompt texts');summary.append(promptContainer);
            const all=createButton('All images for this positive');
            const variants=el('section',undefined,'library-surface library-variants');variants.setAttribute('aria-label','Saved negative variants');
            variants.append(el('h2','Negative variants'),el('p','Choose a saved negative to inspect its full text and filter the shared gallery.','muted'));
            const branches=el('div',undefined,'library-branches');branches.append(el('p','+ Positive prompt · unchanged','library-positive-lane'));
            const choices=[];
            const variantCount=el('p','','muted');variantCount.setAttribute('role','status');
            const variantError=el('p','','muted');variantError.setAttribute('role','alert');variantError.hidden=true;
            const more=createButton('Load more negative variants');const retry=createButton('Retry negative variants');retry.hidden=true;
            contentScope.listen(more,'click',()=>loader.loadMore());contentScope.listen(retry,'click',()=>loader.loadMore());
            updateBranches=state=>{
                const known=new Set(choices.map(choice=>choice.variant.id));
                for(const [index,variant] of detail.variants.entries()) if(!known.has(variant.id)) {
                    const lane=el('div',undefined,'library-variant-lane');const button=createButton('−','library-variant-dot');button.setAttribute('aria-label',`Select negative variant ${index+1}`);
                    button.setAttribute('aria-pressed',String(variantSelections.get(positiveId)===variant.id));
                    lane.append(button,el('span',`${variant.title || (variant.explicit_empty?'Explicitly empty negative':`Negative variant ${index+1}`)} · ${variant.image_count} linked images`));branches.append(lane);choices.push({button,variant});
                    contentScope.listen(button,'click',()=>select(variant.id));
                }
                variantCount.textContent=`${detail.variants.length} of ${detail.variant_total} saved negative variants`;
                more.hidden=!detail.variants_has_more || !!state.variantsError;more.setAttribute('aria-disabled',String(state.loadingVariants));retry.hidden=!state.variantsError;
                variantError.hidden=!state.variantsError;variantError.textContent=state.variantsError?'Negative variants could not be loaded. Retry to continue.':'';
            };
            updateBranches(state);
            if(!detail.variants.length) branches.append(el('p','No saved negative variants. Existing image associations remain available.','muted'));
            const note=el('p','Saved combinations, not an execution timeline.','muted');variants.append(branches,variantCount,variantError,more,retry,note,all);
            const context=el('p','','library-variant-context');context.setAttribute('role','status');
            const gallery=el('section',undefined,'library-shared-gallery');gallery.setAttribute('aria-label','Shared gallery');gallery.append(el('h2','Shared gallery'),context);
            const images=el('div',undefined,'library-linked-images');gallery.append(images);let activePairId;
            body.append(subtitle,summary,variants,gallery);
            blocks=mountPromptBlocks(promptContainer,{prompts:libraryPromptRoles(detail),sender,parentScope:contentScope});
            function select(pairId) {
                if(galleryScope && activePairId===pairId) return;activePairId=pairId;
                variantSelections.set(positiveId,pairId);blocks.update(libraryPromptRoles(detail,pairId));
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
