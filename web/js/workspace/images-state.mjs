import { noun } from './plural.mjs';

/** Identity is the source and relative path; content hashes may be shared by files. */
/** The layout and size a saved view, an embedded gallery and a page without readable preferences use. */
const FIXED_VIEW = Object.freeze({ layout: 'grid', density: 'comfortable' });

/**
 * The defaults a new Images visit starts from: System › Appearance's (`preferences`, preferences.mjs values; Masonry
 * when the layout is missing, #451), or the fixed Grid/Comfortable when they are unavailable (null).
 */
export function visitDefaults(preferences) {
    if (!preferences) return { ...FIXED_VIEW };
    return { layout: preferences.workspaceLayout || 'masonry', density: preferences.workspaceDensity || 'comfortable' };
}

/**
 * The layout and thumbnail size an Images visit starts with (#249 System S3). A saved view starts as before, at Grid
 * and Comfortable; any other new visit starts from `visitDefaults(preferences)`. The last visit (`prior`, the current
 * view) keeps a value its toolbar changed; a value it kept from the defaults it started from (`prior.defaults`) follows
 * defaults saved since, in this tab or another (#451). A prior without `defaults` keeps its own values.
 */
export function initialImagesView({ prior = null, saved = null, preferences = null } = {}) {
    if (!prior) return saved ? { ...FIXED_VIEW } : visitDefaults(preferences);
    const own = { layout: prior.layout || FIXED_VIEW.layout, density: prior.density || FIXED_VIEW.density };
    if (!prior.defaults || !preferences) return own;
    const current = visitDefaults(preferences);
    const follow = key => own[key] === prior.defaults[key] ? current[key] : own[key];
    return { layout: follow('layout'), density: follow('density') };
}

export function imageKey(image) {
    return `${image.root_index}:${image.path}`;
}

/** The workspace listing's sorts: the concept's Recently added and Name A–Z, plus Oldest and Z–A (D14; no rating, D153). */
export const IMAGE_SORTS = Object.freeze(['newest', 'oldest', 'name', 'name-desc']);

const isCount = value => Number.isSafeInteger(value) && value >= 0;
// The largest page a refresh asks for (the listing clamps at 500).
const REFRESH_PAGE_LIMIT = 200;

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
    // #498 (D162): Show removed images lists only the files a removal mark hides; never with a category, tags,
    // favorites or a collection (the server refuses them: such a file has no records).
    const removed = filters.removed ?? false;
    if (typeof removed !== 'boolean') throw new TypeError('Invalid removed filter');
    if (removed && (filters.category || (filters.tags ?? []).length || filters.favorites || (filters.collection ?? '') !== '')) {
        throw new TypeError('Invalid removed filter');
    }
    if (removed) query.set('removed', '1');
    const sort = filters.sort ?? 'newest';
    if (!IMAGE_SORTS.includes(sort)) throw new TypeError('Invalid sort filter');
    query.set('sort', sort);
    // The Favorites page (#249 D51): the listing keeps only favorites.
    const favorites = filters.favorites ?? false;
    if (typeof favorites !== 'boolean') throw new TypeError('Invalid favorites filter');
    if (favorites) query.set('favorites', '1');
    // A collection page (#331 D137): the listing keeps only that collection's members; never together with favorites.
    const collection = filters.collection ?? '';
    if (collection !== '') {
        if (favorites || !/^[1-9]\d{0,8}$/.test(typeof collection === 'number' ? String(collection) : collection)) {
            throw new TypeError('Invalid collection filter');
        }
        query.set('collection', String(collection));
    }
    return query.toString();
}

/**
 * The listing's filter as the bulk routes take it (#392 "Select all N matching"): the same parameters the listing
 * query carries, as an object of strings, without `mode`, `limit` and `cursor`.
 */
export function listingFilter(filters) {
    const query = new URLSearchParams(queryFor(filters, 1));
    query.delete('mode'); query.delete('limit');
    return Object.fromEntries(query);
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

/** A page's `removed_total` (#498), or null from an older server. */
const removedOf = page => (isCount(page?.removed_total) ? page.removed_total : null);

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
    // removedTotal (#498): the removed images in the selected source(s), from the first page; null while unknown.
    let state = Object.freeze({ items: Object.freeze([]), total: 0, cursor: null,
        hasMore: true, loading: false, error: null, loaded: 0, positiveCount: null, removedTotal: null });
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
            loading: false, error: null, loaded: items.length, positiveCount: saved.positiveCount ?? null,
            removedTotal: isCount(saved.removedTotal) ? saved.removedTotal : null });
    }

    function publish(update) {
        state = Object.freeze({ ...state, ...update });
        onChange(state);
    }

    // A refresh in flight (its abort controller): Load more waits for it, a reset or dispose cancels it. A refresh
    // asked while a page loads is pending and runs once that page settles (I6).
    let refreshing = null;
    let refreshPending = false;

    async function loadMore() {
        if (disposed || state.loading || !state.hasMore || refreshing) return 0;
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
            const removedTotal = cursor === null ? removedOf(page) : state.removedTotal;
            publish({ items, total: page.total, cursor: page.next_cursor, hasMore: page.has_more,
                loading: false, error: null, loaded: items.length, positiveCount, removedTotal });
            return incoming.length;
        } catch (error) {
            if (!disposed && request === controller) {
                controller = null;
                publish({ loading: false, error });
            }
            return 0;
        } finally {
            runPendingRefresh(request);
        }
    }
    function runPendingRefresh(request) {
        // Only the page this load owned: a reset or dispose already dropped the pending refresh.
        if (disposed || !refreshPending || state.loading || request.signal.aborted) return;
        refreshPending = false;
        // A failure is the page's to report on the refresh it asked for; here it has no caller left.
        refresh().catch(() => {});
    }

    /**
     * Re-read the loaded range of the same query (a write may have changed which files match) and publish the new
     * collection once, with the hint `{replaced: true}`; the current items stay shown until then. Pages are as large
     * as the rest of the range (at most REFRESH_PAGE_LIMIT, at least one batch), read one after another (each page's
     * cursor comes from the one before). A read failure rejects and leaves the collection as it was; a reset or a
     * dispose abandons it. Resolves to the number of items now loaded (0 when abandoned, or deferred behind a page
     * load in flight, which runs it once settled).
     */
    async function refresh() {
        if (disposed) return 0;
        if (state.loading) { refreshPending = true; return 0; }
        refreshing?.abort();
        const request = refreshing = new AbortController();
        const target = Math.max(state.loaded, 1);
        const items = [], keys = new Set();
        let cursor = null, total = 0, hasMore = true, positiveCount = null, removedTotal = null;
        try {
            while (items.length < target && hasMore) {
                const parameters = new URLSearchParams(query);
                parameters.set('limit', String(Math.min(REFRESH_PAGE_LIMIT, Math.max(target - items.length, batchSize))));
                if (cursor !== null) parameters.set('cursor', cursor);
                const page = await readPage({parameters, signal:request.signal});
                if (disposed || request !== refreshing) return 0;
                validatePage(page, cursor);
                for (const item of page.images) {
                    const key = imageKey(item);
                    if (keys.has(key)) continue;
                    keys.add(key);
                    items.push(Object.freeze({ ...item }));
                }
                if (cursor === null) {
                    positiveCount = isCount(page.positive_count) ? page.positive_count : null;
                    removedTotal = removedOf(page);
                }
                ({total, next_cursor: cursor, has_more: hasMore} = page);
            }
        } catch (error) {
            // A newer refresh, a reset or a dispose replaced this one (and aborted it): nothing to report.
            if (request !== refreshing) return 0;
            refreshing = null;
            throw error;
        }
        refreshing = null;
        seen = keys;
        state = Object.freeze({ ...state, items: Object.freeze(items), total, cursor, hasMore, loading: false, error: null,
            loaded: items.length, positiveCount, removedTotal });
        onChange(state, {replaced: true});
        return items.length;
    }

    async function reset(filters = {}, {load = true} = {}) {
        if (disposed) return 0;
        const nextQuery = queryFor(filters, batchSize);
        controller?.abort();
        controller = null;
        refreshing?.abort();
        refreshing = null;
        refreshPending = false;
        query = nextQuery;
        seen = new Set();
        publish({ items: Object.freeze([]), total: 0, cursor: null, hasMore: true,
            loading: false, error: null, loaded: 0, positiveCount: null, removedTotal: state.removedTotal });
        return load ? loadMore() : 0;
    }

    return {
        get state() { return state; },
        reset,
        loadMore,
        refresh,
        /** Whether a refresh is re-reading the loaded range (Load more waits for it). */
        get refreshing() { return refreshing !== null; },
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
            refreshPending = false;
            controller?.abort();
            controller = null;
            refreshing?.abort();
            refreshing = null;
        },
    };
}

/**
 * The concept's count line (app.js a446): "N of M images · K positive prompts", without the count while unknown, and
 * the page's first "Loading images…" while a first page loads.
 */
export function countLine({ loaded, total, positiveCount, loading = false, error = null }) {
    // A first page in flight has no count yet: "0 of 0 images" would claim the filter matched nothing (#391).
    if (loading && !loaded && !error) return 'Loading images…';
    const images = `${loaded.toLocaleString('en-US')} of ${total.toLocaleString('en-US')} ${noun(total, 'image')}`;
    return isCount(positiveCount) ? `${images} · ${positiveCount.toLocaleString('en-US')} ${noun(positiveCount, 'positive prompt')}` : images;
}

const DAY_MS = 86400000;
const startOfDay = date => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();

/**
 * D17: the caption's right-hand date, "<Today|Yesterday|weekday> · <Month D>" of the newest result under Newest first,
 * with ", YYYY" outside the current year; '' under Oldest first and the name sorts.
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

/** D44: how many "Refine this view" filters (Source, Folder, Media, Tags) narrow the view, for the Filters label. */
export function activeRefinements({ root = 'all', subfolder = '', media = 'all', tags = [] }) {
    return [root !== 'all' && root !== '', Boolean(subfolder), media !== 'all', tags.length > 0].filter(Boolean).length;
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

/** The card checkboxes' selection (#249 D8): a new frozen set with `key` added or removed; unchanged, the same set. */
export function toggleSelection(selected, key, on) {
    if (selected.has(key) === on) return selected;
    const next = new Set(selected);
    if (on) next.add(key); else next.delete(key);
    return Object.freeze(next);
}

/** The concept selection bar's count (app.js updateBulk), grouped like the page's other counts. */
export function selectionLabel(count) {
    return `${count.toLocaleString('en-US')} selected`;
}
