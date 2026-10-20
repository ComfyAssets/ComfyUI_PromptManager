import { noun } from './plural.mjs';

/** Identity is the source and relative path; content hashes may be shared by files. */
export function imageKey(image) {
    return `${image.root_index}:${image.path}`;
}

/** The workspace listing's sorts: the concept's Recently added, Name A–Z and Highest rated, plus Oldest and Z–A (D14). */
export const IMAGE_SORTS = Object.freeze(['newest', 'oldest', 'name', 'name-desc', 'rating']);

const isCount = value => Number.isSafeInteger(value) && value >= 0;

function queryFor(filters, batchSize) {
    const query = new URLSearchParams({ mode: 'workspace', limit: String(batchSize) });
    for (const key of ['q', 'subfolder', 'category']) {
        const value = filters[key];
        if (value !== undefined && typeof value !== 'string') throw new TypeError('Invalid image filter');
        if (value) query.set(key, value);
    }
    const tags = filters.tags ?? [];
    // The listing takes a comma list that must all match, so a tag can neither be empty nor hold a comma.
    if (!Array.isArray(tags) || tags.some(tag => typeof tag !== 'string' || !tag.trim() || tag.includes(','))) {
        throw new TypeError('Invalid tags filter');
    }
    if (tags.length) query.set('tags', tags.map(tag => tag.trim()).join(','));
    const root = filters.root;
    if (root !== undefined && root !== 'all' && root !== '') {
        if (!/^(0|[1-9]\d*)$/.test(String(root)) || !Number.isSafeInteger(Number(root))) {
            throw new TypeError('Invalid root filter');
        }
        query.set('root', String(root));
    }
    const media = filters.media ?? 'all';
    if (!['all', 'image', 'video'].includes(media)) throw new TypeError('Invalid media filter');
    if (media !== 'all') query.set('media', media);
    const sort = filters.sort ?? 'newest';
    if (!IMAGE_SORTS.includes(sort)) throw new TypeError('Invalid sort filter');
    query.set('sort', sort);
    return query.toString();
}

function validatePage(page, cursor) {
    if (!page || !Array.isArray(page.images) || !Number.isSafeInteger(page.total) || page.total < 0 ||
        typeof page.has_more !== 'boolean' ||
        !(page.next_cursor === null || (typeof page.next_cursor === 'string' && page.next_cursor.length > 0)) ||
        page.has_more !== (page.next_cursor !== null) ||
        !(page.positive_count === undefined || page.positive_count === null || isCount(page.positive_count))) {
        throw new TypeError('Invalid image page');
    }
    if (page.has_more && page.next_cursor === cursor) throw new TypeError('Image cursor did not advance');
    for (const item of page.images) {
        if (!item || typeof item.path !== 'string' || !item.path ||
            !Number.isSafeInteger(item.root_index) || item.root_index < 0) {
            throw new TypeError('Invalid image identity');
        }
    }
}

/** `state` with the item named by `key` patched (both frozen); the same state when no item has that key. */
export function withItem(state, key, patch) {
    if (!patch || typeof patch !== 'object' || 'path' in patch || 'root_index' in patch) {
        throw new TypeError('An image patch cannot change its identity');
    }
    const index = state.items.findIndex(item => imageKey(item) === key);
    if (index < 0) return state;
    const items = [...state.items];
    items[index] = Object.freeze({ ...items[index], ...patch });
    return Object.freeze({ ...state, items: Object.freeze(items) });
}

/** Keep metadata for backward scrolling; the view bounds rendered media independently. */
export function createImageStream({ api, onChange = () => {}, batchSize = 40, restore, fetchPage }) {
    if (fetchPage !== undefined && typeof fetchPage !== 'function') throw new TypeError('Invalid image page adapter');
    if (!fetchPage && typeof api?.get !== 'function') throw new TypeError('An API client is required');
    const readPage = fetchPage || (({parameters, signal}) => api.get(`/prompt_manager/images/output?${parameters}`, {signal}));
    if (!Number.isSafeInteger(batchSize) || batchSize < 1 || batchSize > 200) {
        throw new TypeError('Invalid image batch size');
    }
    // positiveCount: distinct linked positives over the whole matching set, or null while unknown (PR B contract).
    let state = Object.freeze({ items: Object.freeze([]), total: 0, cursor: null,
        hasMore: true, loading: false, error: null, loaded: 0, positiveCount: null });
    let query = queryFor({}, batchSize);
    let controller = null;
    let disposed = false;
    let seen = new Set();
    if (restore !== undefined) {
        if (!restore || !restore.filters || typeof restore.filters !== 'object' || Array.isArray(restore.filters) || !restore.state) {
            throw new TypeError('Invalid restored image state');
        }
        query = queryFor(restore.filters, batchSize);
        const saved = restore.state;
        const pristine = Array.isArray(saved.items) && saved.items.length === 0 && saved.total === 0 &&
            saved.cursor === null && saved.hasMore === true;
        validatePage({ images: saved.items, total: saved.total, next_cursor: saved.cursor,
            has_more: pristine ? false : saved.hasMore, positive_count: saved.positiveCount });
        seen = new Set(saved.items.map(imageKey));
        if (seen.size !== saved.items.length) throw new TypeError('Duplicate restored image identity');
        const items = Object.isFrozen(saved.items) && saved.items.every(Object.isFrozen) ? saved.items :
            Object.freeze(saved.items.map(item => Object.freeze({ ...item })));
        state = Object.freeze({ items, total: saved.total, cursor: saved.cursor, hasMore: saved.hasMore,
            loading: false, error: null, loaded: items.length, positiveCount: saved.positiveCount ?? null });
    }

    function publish(update) {
        state = Object.freeze({ ...state, ...update });
        onChange(state);
    }

    async function loadMore() {
        if (disposed || state.loading || !state.hasMore) return 0;
        const request = new AbortController();
        controller = request;
        const cursor = state.cursor;
        publish({ loading: true, error: null });
        const parameters = new URLSearchParams(query);
        if (cursor !== null) parameters.set('cursor', cursor);
        try {
            const page = await readPage({parameters, signal:request.signal});
            if (disposed || request !== controller) return 0;
            validatePage(page, cursor);
            const incoming = [];
            const batchKeys = new Set();
            for (const item of page.images) {
                const key = imageKey(item);
                if (seen.has(key) || batchKeys.has(key)) continue;
                batchKeys.add(key);
                incoming.push(Object.freeze({ ...item }));
            }
            for (const key of batchKeys) seen.add(key);
            const items = incoming.length ? Object.freeze([...state.items, ...incoming]) : state.items;
            // A plain cursor page leaves the count out and a failed count read is null: a later page keeps the
            // first page's count, and a first page without one leaves it unknown.
            const positiveCount = isCount(page.positive_count) ? page.positive_count : cursor === null ? null : state.positiveCount;
            controller = null;
            publish({ items, total: page.total, cursor: page.next_cursor, hasMore: page.has_more,
                loading: false, error: null, loaded: items.length, positiveCount });
            return incoming.length;
        } catch (error) {
            if (!disposed && request === controller) {
                controller = null;
                publish({ loading: false, error });
            }
            return 0;
        }
    }

    async function reset(filters = {}, {load = true} = {}) {
        if (disposed) return 0;
        const nextQuery = queryFor(filters, batchSize);
        controller?.abort();
        controller = null;
        query = nextQuery;
        seen = new Set();
        publish({ items: Object.freeze([]), total: 0, cursor: null, hasMore: true,
            loading: false, error: null, loaded: 0, positiveCount: null });
        return load ? loadMore() : 0;
    }

    return {
        get state() { return state; },
        reset,
        loadMore,
        /** Patch one loaded item (a favorite toggle); false when it is not loaded or the stream is gone. */
        updateItem(key, patch) {
            const next = withItem(state, key, patch);
            if (disposed || next === state) return false;
            state = next;
            // The hint names the one patched item, so a page can repaint it without laying out the collection again.
            onChange(state, {patch:key});
            return true;
        },
        dispose() {
            if (disposed) return;
            disposed = true;
            controller?.abort();
            controller = null;
        },
    };
}

/** The concept's count line (app.js a446): "N of M images · K positive prompts", without the count while unknown. */
export function countLine({ loaded, total, positiveCount }) {
    const images = `${loaded.toLocaleString('en-US')} of ${total.toLocaleString('en-US')} ${noun(total, 'image')}`;
    return isCount(positiveCount) ? `${images} · ${positiveCount.toLocaleString('en-US')} ${noun(positiveCount, 'positive prompt')}` : images;
}

const DAY_MS = 86400000;
const startOfDay = date => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();

/**
 * D17: the caption's right-hand date, "<Today|Yesterday|weekday> · <Month D>" of the newest result under Newest first,
 * with ", YYYY" outside the current year; '' under Oldest first and the name or rating sorts.
 */
export function captionDate(items, sort, now = new Date()) {
    if (sort !== 'newest') return '';
    const newest = items.reduce((latest, item) => Number.isFinite(item.modified_time) ? Math.max(latest, item.modified_time) : latest, -Infinity);
    if (!Number.isFinite(newest)) return '';
    const date = new Date(newest * 1000);
    const days = Math.round((startOfDay(now) - startOfDay(date)) / DAY_MS);
    const day = days === 0 ? 'Today' : days === 1 ? 'Yesterday' : date.toLocaleDateString('en-US', { weekday: 'long' });
    const options = { month: 'long', day: 'numeric', ...(date.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }) };
    return `${day} · ${date.toLocaleDateString('en-US', options)}`;
}

/** D14: the filter row's scope chip, "All output folders" or "<source> · <folder>". */
export function scopeLabel({ root = 'all', subfolder = '' }, roots = []) {
    if ((root === 'all' || root === '') && !subfolder) return 'All output folders';
    const source = root === 'all' || root === '' ? 'All sources' :
        roots.find(item => String(item.index) === String(root))?.label ?? `Source ${root}`;
    return `${source} · ${subfolder || 'All output folders'}`;
}

/** D10: a card shows its confirmed linked positive's display title; any other link status shows the filename. */
export function cardTitle(image) {
    const title = image.linked?.status === 'linked' ? image.linked.title : null;
    return typeof title === 'string' && title ? title : image.filename;
}

const hasSize = image => [image.width, image.height].every(value => Number.isFinite(value) && value > 0);

/** D11: "W × H · EXT · Seed N", omitting unknown parts. The seed is the listing's decimal string, shown as sent. */
export function cardSubtitle(image) {
    const dot = image.filename.lastIndexOf('.');
    const extension = dot > 0 ? image.filename.slice(dot + 1) : '';
    return [hasSize(image) ? `${image.width} × ${image.height}` : '', extension.toUpperCase(),
        typeof image.seed === 'string' && image.seed ? `Seed ${image.seed}` : ''].filter(Boolean).join(' · ');
}

/** The concept card's `--media-ratio` (masonry art); unknown dimensions use the layout's 4:3 fallback. */
export function mediaRatio(image) {
    return hasSize(image) ? `${image.width}/${image.height}` : '4/3';
}
