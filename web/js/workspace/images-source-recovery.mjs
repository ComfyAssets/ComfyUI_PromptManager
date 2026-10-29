/**
 * Finding a file again after its source changed (#430).
 *
 * A listed file is addressed as `{root_index, root_scope, path}`. When the sources are renumbered every per-file
 * action on it answers 409 `source-changed`. `rescopeImage` asks the server (POST /prompt_manager/images/rescope)
 * for the file's current listing descriptor: the server maps the target only when its scope was issued for one of
 * the current roots, so a removed or replaced source (or a changed key) answers null and the page offers Refresh.
 *
 * `createSourceRecovery` is the page's single flight: one rescope per held `(key, scope)` pair, its result shared
 * by every caller (the inspector, the viewer and the Tag image dialog may all ask for the same file), and a pair
 * already tried answers its first result, so a refusal can never loop. `apply(image, fresh, started)` is the page's
 * part (refresh the collection, move the selection); `started` is the page generation when the rescope began, so a
 * page whose filters changed meanwhile can leave itself alone. `reset()` (a manual Refresh) forgets every pair.
 */
import { imageKey } from './images-state.mjs';

export const RESCOPE_URL = '/prompt_manager/images/rescope';
// A page holds a handful of stale files at once; older pairs are forgotten first.
const MEMO_LIMIT = 64;
const hex64 = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);

function validTarget(value) {
    return value !== null && typeof value === 'object' && Number.isSafeInteger(value.root_index) && value.root_index >= 0 &&
        hex64(value.root_scope) && typeof value.path === 'string' && value.path.length > 0;
}

/** The fresh descriptor of `image` (same path, current root and scope), else null. */
export async function rescopeImage(api, image, {signal} = {}) {
    if (!validTarget(image)) return null;
    let value;
    try {
        value = await api.post(RESCOPE_URL, {target: {root_index: image.root_index, root_scope: image.root_scope, path: image.path}},
            {signal});
    } catch (error) {
        // 409 (the source is gone) or 404 (the file is gone): final, the caller offers Refresh. Anything else (no
        // answer, a 5xx) is rethrown so the pair may be asked again.
        if (error?.status === 404 || error?.status === 409) return null;
        throw error;
    }
    const fresh = value?.success === true ? value.image : null;
    if (!validTarget(fresh) || fresh.path !== image.path) return null;
    return Object.freeze({...fresh});
}

export function createSourceRecovery({api, signal, apply, generation = () => 0}) {
    // An embedded gallery's own page adapter may have no `post`: its rescope then simply finds nothing.
    if (typeof apply !== 'function') throw new TypeError('createSourceRecovery needs apply');
    const memo = new Map();
    function recover(image) {
        if (!validTarget(image)) return Promise.resolve(null);
        const pair = `${imageKey(image)}|${image.root_scope}`;
        if (memo.has(pair)) return memo.get(pair);
        const started = generation();
        const pending = rescopeImage(api, image, {signal})
            .then(fresh => (fresh && !signal?.aborted ? apply(image, fresh, started) : null))
            .then(result => result ?? null, () => {
                if (memo.get(pair) === pending) memo.delete(pair);
                return null;
            });
        memo.set(pair, pending);
        if (memo.size > MEMO_LIMIT) memo.delete(memo.keys().next().value);
        return pending;
    }
    return {recover, reset: () => memo.clear()};
}

/**
 * The Images page's `apply` (#430). Keys are `root_index:path`, so after a renumbering nothing from the old list is
 * kept by key (a checked or selected key could name a same-named file in another source): the viewer closes, the
 * checks clear, a Source filter on the old index follows the file, and only the file itself stays selected while the
 * collection lists again. An unchanged numbering re-reads the loaded range in place. `page` holds the page's hooks.
 */
export function recoveredSource(page) {
    return async (image, fresh, started) => {
        // The held scope still names its root: nothing is stale (a media error had another cause).
        if (fresh.root_index === image.root_index && fresh.root_scope === image.root_scope) return null;
        if (!page.current(started)) return fresh;
        const selected = page.selected(image);
        if (fresh.root_index === image.root_index) {
            if (selected) page.inspect(fresh);
            await page.stream.refresh().catch(() => {});
            return fresh;
        }
        page.closeViewer();
        page.clearChecks();
        const {filters} = page;
        if (filters.root === String(image.root_index)) filters.root = String(fresh.root_index);
        else if (filters.root !== 'all') { filters.root = 'all'; filters.subfolder = ''; }
        if (selected) page.inspect(fresh);
        else page.dropSelection();
        page.updateScope();
        page.stream.reset(filters, {load:page.listsNow()});
        page.rediscover();
        return fresh;
    };
}
