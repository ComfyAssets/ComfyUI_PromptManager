/** Identity is the source and relative path; content hashes may be shared by files. */
export function imageKey(image) {
    return `${image.root_index}:${image.path}`;
}

function queryFor(filters, batchSize) {
    const query = new URLSearchParams({ mode: 'workspace', limit: String(batchSize) });
    for (const key of ['q', 'subfolder']) {
        const value = filters[key];
        if (value !== undefined && typeof value !== 'string') throw new TypeError('Invalid image filter');
        if (value) query.set(key, value);
    }
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
    if (!['newest', 'oldest', 'name', 'name-desc'].includes(sort)) throw new TypeError('Invalid sort filter');
    query.set('sort', sort);
    return query.toString();
}

function validatePage(page, cursor) {
    if (!page || !Array.isArray(page.images) || !Number.isSafeInteger(page.total) || page.total < 0 ||
        typeof page.has_more !== 'boolean' ||
        !(page.next_cursor === null || (typeof page.next_cursor === 'string' && page.next_cursor.length > 0)) ||
        page.has_more !== (page.next_cursor !== null)) {
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

/** Keep metadata for backward scrolling; the view bounds rendered media independently. */
export function createImageStream({ api, onChange = () => {}, batchSize = 40, restore }) {
    if (typeof api?.get !== 'function') throw new TypeError('An API client is required');
    if (!Number.isSafeInteger(batchSize) || batchSize < 1 || batchSize > 200) {
        throw new TypeError('Invalid image batch size');
    }
    let state = Object.freeze({ items: Object.freeze([]), total: 0, cursor: null,
        hasMore: true, loading: false, error: null, loaded: 0 });
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
            has_more: pristine ? false : saved.hasMore });
        seen = new Set(saved.items.map(imageKey));
        if (seen.size !== saved.items.length) throw new TypeError('Duplicate restored image identity');
        const items = Object.isFrozen(saved.items) && saved.items.every(Object.isFrozen) ? saved.items :
            Object.freeze(saved.items.map(item => Object.freeze({ ...item })));
        state = Object.freeze({ items, total: saved.total, cursor: saved.cursor, hasMore: saved.hasMore,
            loading: false, error: null, loaded: items.length });
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
            const page = await api.get(`/prompt_manager/images/output?${parameters}`, { signal: request.signal });
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
            controller = null;
            publish({ items, total: page.total, cursor: page.next_cursor, hasMore: page.has_more,
                loading: false, error: null, loaded: items.length });
            return incoming.length;
        } catch (error) {
            if (!disposed && request === controller) {
                controller = null;
                publish({ loading: false, error });
            }
            return 0;
        }
    }

    async function reset(filters = {}) {
        if (disposed) return 0;
        const nextQuery = queryFor(filters, batchSize);
        controller?.abort();
        controller = null;
        query = nextQuery;
        seen = new Set();
        publish({ items: Object.freeze([]), total: 0, cursor: null, hasMore: true,
            loading: false, error: null, loaded: 0 });
        return loadMore();
    }

    return {
        get state() { return state; },
        reset,
        loadMore,
        dispose() {
            if (disposed) return;
            disposed = true;
            controller?.abort();
            controller = null;
        },
    };
}
