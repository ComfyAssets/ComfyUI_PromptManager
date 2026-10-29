function validId(value) { return Number.isSafeInteger(value) && value > 0; }
function parent(value) {
    return value && validId(value.id) && typeof value.text==='string';
}
function parameters(filters,limit) {
    const query=new URLSearchParams({limit:String(limit)});
    for(const key of ['text','category']) {
        if(filters[key]!==undefined && typeof filters[key]!=='string') throw new TypeError('Invalid library filter');
        if(filters[key]) query.set(key,filters[key]);
    }
    if(filters.tags!==undefined && (!Array.isArray(filters.tags) || filters.tags.some(tag=>typeof tag!=='string'))) throw new TypeError('Invalid library tags');
    for(const tag of filters.tags || []) if(tag.trim()) query.append('tag',tag.trim());
    const sort=filters.sort || 'newest';
    if(!['newest','oldest','name','rating'].includes(sort)) throw new TypeError('Invalid library sort');
    query.set('sort',sort);return query;
}
// `variant_total` is the filtered parents' active negative variants (the concept caption's "M negative variants").
function validatePage(page,cursor) {
    if(!page || !Array.isArray(page.items) || page.items.some(item=>!parent(item)) || !Number.isSafeInteger(page.total) || page.total<0 || !Number.isSafeInteger(page.variant_total) || page.variant_total<0 || typeof page.has_more!=='boolean' || !(page.next_cursor===null || (typeof page.next_cursor==='string' && page.next_cursor.length)) || page.has_more!==(page.next_cursor!==null) || (page.has_more && cursor===page.next_cursor)) throw new TypeError('Invalid library page');
}
export function createLibraryStream({api,onChange=()=>{},restore}) {
    let state=Object.freeze({items:Object.freeze([]),total:0,variantTotal:null,cursor:null,hasMore:true,loading:false,error:null,epoch:null});
    let query=parameters({},40),request,disposed=false,seen=new Set();
    if(restore) {
        query=parameters(restore.filters,40);const saved=restore.state;
        // A shelf left before its first page answered (or after it failed) has no page yet: it restores as a fresh stream.
        const firstPagePending=saved?.variantTotal===null && Array.isArray(saved.items) && !saved.items.length && saved.total===0 && saved.cursor===null && saved.hasMore===true;
        if(!firstPagePending) validatePage({items:saved.items,total:saved.total,variant_total:saved.variantTotal,next_cursor:saved.cursor,has_more:saved.cursor!==null},null);
        if(new Set(saved.items.map(item=>item.id)).size!==saved.items.length) throw new TypeError('Duplicate restored parent');
        state=Object.freeze({...saved,epoch:saved.epoch ?? null,items:Object.freeze(saved.items.map(item=>Object.freeze({...item}))),loading:false,error:null});seen=new Set(state.items.map(item=>item.id));
    }
    const emit=patch=>{state=Object.freeze({...state,...patch});onChange(state);};
    async function loadMore() {
        if(disposed || state.loading || !state.hasMore) return 0;
        const current=request=new AbortController();const cursor=state.cursor;
        emit({loading:true,error:null});const params=new URLSearchParams(query);if(cursor!==null) params.set('cursor',cursor);
        try {
            const page=await api.get(`/prompt_manager/library?${params}`,{signal:current.signal});
            if(disposed || current!==request) return 0;
            validatePage(page,cursor);
            // #437: the storage epoch the shelf's items were read under, which a bulk write sends as its fence; a later
            // page from a restored storage is refused (its items would belong to another database).
            const epoch=typeof page.library_epoch==='string'?page.library_epoch:null;
            if(state.epoch!==null && epoch!==state.epoch) throw new TypeError('Prompt storage changed while the library was loading');
            const incoming=[];const added=new Set();
            for(const item of page.items) if(!seen.has(item.id) && !added.has(item.id)) {added.add(item.id);incoming.push(Object.freeze({...item}));}
            for(const value of added) seen.add(value);
            request=null;emit({items:incoming.length?Object.freeze([...state.items,...incoming]):state.items,epoch:state.epoch ?? epoch,total:page.total,variantTotal:page.variant_total,cursor:page.next_cursor,hasMore:page.has_more,loading:false,error:null});return incoming.length;
        } catch(error) {if(!disposed && current===request) {request=null;emit({loading:false,error});}return 0;}
    }
    return {get state(){return state;},loadMore,async reset(filters={}) {
        if(disposed) return 0;const next=parameters(filters,40);request?.abort();request=null;query=next;seen=new Set();emit({items:Object.freeze([]),total:0,variantTotal:null,cursor:null,hasMore:true,loading:false,error:null,epoch:null});return loadMore();
    },dispose(){disposed=true;request?.abort();request=null;}};
}
/**
 * Saved roles for one selection. A pair outside the bounded variant page is
 * passed separately as `selectedPair` (an exact-ID read) and must belong to the
 * same positive; it never enters `detail.variants`.
 */
export function libraryPromptRoles(detail,pairId=null,selectedPair=null) {
    let variant=pairId===null?null:detail.variants.find(item=>item.id===pairId);
    if(pairId!==null && !variant && selectedPair?.id===pairId) {
        if(selectedPair.positive_prompt_id!==detail.positive.id || selectedPair.positive_text!==detail.positive.text || typeof selectedPair.negative_text!=='string') throw new TypeError('Saved negative variant belongs to another prompt');
        variant=selectedPair;
    }
    if(pairId!==null && !variant) throw new TypeError('Saved negative variant is unavailable');
    return {positive:{status:'resolved',text:detail.positive.text,source:'library'},negative:variant?{status:'resolved',text:variant.negative_text,source:'library'}:{status:'unresolved',text:null,source:null,message:'Choose a saved negative variant to inspect or send it.'}};
}
const ARCHIVE_SCOPES=new Set(['active','archived','all']);
export function createLibraryDetail({api,onChange,archive='active'}) {
    if(!ARCHIVE_SCOPES.has(archive)) throw new TypeError('Invalid archive scope');
    let request,disposed=false,id;
    let state=Object.freeze({detail:null,loading:false,error:null,loadingVariants:false,variantsError:null});
    const emit=patch=>{state=Object.freeze({...state,...patch});onChange(state);};
    function validate(detail) {
        if(!parent(detail.positive) || detail.positive.id!==id || !Array.isArray(detail.variants) || detail.variants.some(item=>!validId(item.id) || typeof item.negative_text!=='string' || typeof item.explicit_empty!=='boolean' || item.explicit_empty!==(item.negative_text==='')) || new Set(detail.variants.map(item=>item.id)).size!==detail.variants.length) throw new TypeError('Invalid prompt history');
        const total=detail.variant_total ?? detail.variants.length,more=detail.variants_has_more ?? false,cursor=detail.variants_next_cursor ?? null;
        if(!Number.isSafeInteger(total) || total<detail.variants.length || typeof more!=='boolean' || !(cursor===null || (typeof cursor==='string' && cursor.length>0)) || more!==(cursor!==null)) throw new TypeError('Invalid negative variant page');
        return {...detail,variant_total:total,variants_has_more:more,variants_next_cursor:cursor};
    }
    async function fetch(append) {
        if(disposed || (append && (!state.detail?.variants_has_more || state.loadingVariants || state.loading))) return 0;
        request?.abort();const current=request=new AbortController();const previous=state.detail;const cursor=append?previous.variants_next_cursor:null;
        emit(append?{loadingVariants:true,variantsError:null}:{detail:null,loading:true,error:null,loadingVariants:false,variantsError:null});
        try {
            if(!validId(id)) throw new TypeError('Invalid positive prompt');
            const query=new URLSearchParams({variant_limit:'40'});if(archive!=='active') query.set('archive',archive);if(cursor) query.set('variant_cursor',cursor);
            const raw=await api.get(`/prompt_manager/library/${id}?${query}`,{signal:current.signal});
            if(disposed || request!==current) return 0;
            const detail=validate(raw);
            if(append) {
                if(detail.positive.text!==previous.positive.text || (detail.variants_has_more && detail.variants_next_cursor===cursor)) throw new TypeError('Prompt history changed. Reload it before continuing.');
                const variants=new Map(previous.variants.map(item=>[item.id,item]));for(const item of detail.variants) if(!variants.has(item.id)) variants.set(item.id,item);detail.variants=[...variants.values()];
            }
            request=null;emit({detail,loading:false,error:null,loadingVariants:false,variantsError:null});return detail.variants.length-(append?previous.variants.length:0);
        } catch(error) {if(!disposed && request===current) {request=null;emit(append?{loadingVariants:false,variantsError:error}:{loading:false,error});}return 0;}
    }
    /** Same-parent reconciliation: a validated authoritative detail replaces the current one and supersedes any read. */
    function accept(detail) {
        if(disposed) throw new TypeError('Prompt history is disposed');
        const next=validate(detail);
        request?.abort();request=null;
        emit({detail:next,loading:false,error:null,loadingVariants:false,variantsError:null});
    }
    return {get state(){return state;},load(value){id=value;return fetch(false);},loadMore(){return fetch(true);},accept,dispose(){disposed=true;request?.abort();}};
}

export function libraryWindow(count,columns,top,height,focused=-1) {
    if(count<=120) return Array.from({length:count},(_,index)=>index);
    const rows=Math.ceil(count/columns),first=Math.max(0,Math.floor(top/578)-2),last=Math.min(rows-1,Math.ceil((top+height)/578)+2);
    const keep=new Set();
    for(let row=first;row<=last;row++) keep.add(row);
    if(focused>=0 && focused<count) keep.add(Math.floor(focused/columns));
    return [...keep].sort((a,b)=>a-b).flatMap(row=>Array.from({length:columns},(_,column)=>row*columns+column).filter(index=>index<count));
}
