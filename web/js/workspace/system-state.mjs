/**
 * The System draft store (#249 System S2 Task 5): every System section edits drafts behind the one Save row (the
 * concept's `.save-row`, app.js renderSystem 705), in three domains:
 * - `browser`: preferences kept in this browser (Appearance);
 * - `host`: the server configuration, saved with the `revision` its read returned (`expected_revision`, #220);
 * - `runtime`: values applied to the running server only (the log level, Q10b).
 *
 * `save()` writes browser, then host, then runtime, and only the domains with changes. A domain that fails keeps its
 * drafts dirty and its keys `failed()`, so the section highlights them; the others are saved. A host save refused
 * because the configuration changed elsewhere (`settings-changed`) is a `conflict` and keeps the draft. A host save before
 * `load('host')` returned a revision fails without a request: a blind write would merge over a configuration changed
 * elsewhere (S1 reads a null `expected_revision` as "merge into what is on disk"). `reloadHost()` reads the host values
 * again (System calls it on mount); a host conflict reloads them itself, so the next save carries the current revision.
 * Drafts live for
 * the page session (Q10a): a section left and opened again shows them, and a reload of its values keeps them.
 *
 * Adapters: `readBrowser()` and `readRuntime()` return the values; `readHost()` returns `{values, revision}`.
 * `writeBrowser(changes)` returns `{persisted}` (false: kept for this tab only) and throws an error with `code:
 * 'settings-changed'` when another page changed a drafted value (a conflict: the browser values are read again),
 * `writeHost(changes, {expected_revision})` the settings response (`{success, persisted, revision}`, and the
 * `restart_required_fields` the status names), `writeRuntime(changes)` anything; a write that throws failed. A host
 * write that saved part of its changes throws with `partial: {saved: [keys], revision}`: those keys are saved, the
 * revision is adopted and only the rest fail (`partial`, S10). A host
 * write refused as `invalid-source` (its error's `source` and server message) makes that message the status and
 * `invalidSource()` that source, so Sources marks its row (S4). One refused as `invalid-setting` (S11: the server
 * named the setting it refused) makes that message the status too, naming no source.
 */
export { SYSTEM_SECTIONS, isSystemSection } from './state.mjs';

/** D92: the Save row's status copy; never the concept's prototype text. */
export const SETTINGS_STATUS = Object.freeze({
    clean: 'No unsaved changes.',
    dirty: 'Unsaved changes.',
    browser: 'Saved in this browser.',
    host: 'Saved to the server configuration.',
    runtime: 'Applied until the server restarts.',
    partial: 'Some changes were not saved. Review the highlighted settings.',
    // S10 review I3: a host save written in two parts where only one was saved.
    partialSaved: 'Some changes were saved to the server configuration; the highlighted ones were not.',
    tabOnly: 'Saved for this tab only; browser storage is unavailable.',
    conflict: 'Settings changed elsewhere. Reload this section to see them.',
    // D128: a saved setting the running server applies only after a restart.
    restart: 'Restart ComfyUI to apply:',
});

/** D128: the settings a host save can report in `restart_required_fields`, by their row label. */
export const RESTART_LABELS = Object.freeze({ monitoring_enabled: 'Watch for new outputs' });

export const DRAFT_DOMAINS = Object.freeze(['browser', 'host', 'runtime']);
const CONFLICT_CODE = 'settings-changed';
const INVALID_SOURCE_CODE = 'invalid-source';
/** S11: a `/settings` save refused for one value (a 400 whose message names the setting, e.g. a confidence of 1.5). */
export const INVALID_SETTING_CODE = 'invalid-setting';
const MESSAGE_CODES = new Set([INVALID_SOURCE_CODE, INVALID_SETTING_CODE]);
const ADAPTERS = ['readBrowser', 'readHost', 'readRuntime', 'writeBrowser', 'writeHost', 'writeRuntime'];

/**
 * The status copy of one `save()` outcome (`{browser, host, runtime}`, each clean/saved/tab-only/applied/failed/conflict).
 * `restart` lists the host fields the server applies at its next start, `invalid` the source a refused host save named
 * (`{source, message}`): its message is the status (D128).
 */
export function statusText(outcome) {
    const results = DRAFT_DOMAINS.map(domain => outcome?.[domain]);
    if (results.includes('conflict')) return SETTINGS_STATUS.conflict;
    if (results.includes('failed')) return outcome?.invalid?.message || SETTINGS_STATUS.partial;
    if (results.includes('partial')) return outcome?.invalid?.message || SETTINGS_STATUS.partialSaved;
    const saved = [];
    if (outcome?.browser === 'saved') saved.push(SETTINGS_STATUS.browser);
    if (outcome?.browser === 'tab-only') saved.push(SETTINGS_STATUS.tabOnly);
    if (outcome?.host === 'saved') {
        saved.push(SETTINGS_STATUS.host);
        const restart = (outcome.restart ?? []).map(field => RESTART_LABELS[field]).filter(Boolean);
        if (restart.length) saved.push(`${SETTINGS_STATUS.restart} ${restart.join(', ')}.`);
    }
    if (outcome?.runtime === 'applied') saved.push(SETTINGS_STATUS.runtime);
    return saved.join(' ') || SETTINGS_STATUS.clean;
}

function requireDomain(domain) {
    if (!DRAFT_DOMAINS.includes(domain)) throw new TypeError(`Unknown settings domain: ${domain}`);
}

/** Create the store; see the module comment for the adapters. */
export function createSystemDrafts(adapters) {
    for (const name of ADAPTERS) {
        if (typeof adapters?.[name] !== 'function') throw new TypeError(`System drafts need ${name}`);
    }
    const baseline = { browser: {}, host: {}, runtime: {} };
    const drafts = { browser: new Map(), host: new Map(), runtime: new Map() };
    const failedKeys = { browser: new Set(), host: new Set(), runtime: new Set() };
    const listeners = new Set();
    let revision = null;
    let hostRead = 'pending';
    let outcome = null;
    let saving = null;
    // What the last host save said beyond its result: the restart fields, or the source it refused.
    let hostDetail = {};

    const notify = () => { for (const listener of [...listeners]) listener(); };
    const changes = domain => Object.fromEntries(drafts[domain]);

    /** Read a domain's saved values; drafts are kept, and a draft equal to the new value is clean. */
    async function load(domain) {
        requireDomain(domain);
        let values;
        if (domain === 'host') {
            const read = await adapters.readHost();
            values = read?.values ?? {};
            revision = read?.revision ?? null;
            hostRead = 'loaded';
        } else {
            values = await (domain === 'browser' ? adapters.readBrowser() : adapters.readRuntime());
        }
        baseline[domain] = { ...(values ?? {}) };
        for (const [key, value] of drafts[domain]) if (Object.is(baseline[domain][key], value)) drafts[domain].delete(key);
        notify();
        return { ...baseline[domain] };
    }

    function get(domain, key) {
        requireDomain(domain);
        return drafts[domain].has(key) ? drafts[domain].get(key) : baseline[domain][key];
    }

    function set(domain, key, value) {
        requireDomain(domain);
        if (Object.is(baseline[domain][key], value)) drafts[domain].delete(key);
        else drafts[domain].set(key, value);
        failedKeys[domain].delete(key);
        outcome = null;
        notify();
    }

    function dirty(domain) {
        if (domain !== undefined) {
            requireDomain(domain);
            return drafts[domain].size > 0;
        }
        return DRAFT_DOMAINS.some(name => drafts[name].size > 0);
    }

    function discard(domain) {
        for (const name of domain === undefined ? DRAFT_DOMAINS : [domain]) {
            requireDomain(name);
            drafts[name].clear();
            failedKeys[name].clear();
        }
        outcome = null;
        notify();
    }

    /** The values of one domain as written: they become its saved values, and drafts still equal to them are clean. */
    function commit(domain, written) {
        for (const [key, value] of written) {
            baseline[domain][key] = value;
            if (drafts[domain].has(key) && Object.is(drafts[domain].get(key), value)) drafts[domain].delete(key);
        }
    }

    function fail(domain, written) {
        for (const [key] of written) failedKeys[domain].add(key);
    }

    async function saveDomain(domain) {
        const written = new Map(drafts[domain]);
        if (written.size === 0) return 'clean';
        const values = Object.fromEntries(written);
        try {
            if (domain === 'browser') {
                const result = await adapters.writeBrowser(values);
                commit(domain, written);
                return result?.persisted === false ? 'tab-only' : 'saved';
            }
            if (domain === 'host') {
                if (typeof revision !== 'string') { fail(domain, written); return 'failed'; }
                const result = await adapters.writeHost(values, { expected_revision: revision });
                if (result?.code === CONFLICT_CODE) { fail(domain, written); return 'conflict'; }
                if (!result?.success || result.persisted !== true) { fail(domain, written); return 'failed'; }
                if (typeof result.revision === 'string') revision = result.revision;
                const restart = result.restart_required_fields;
                if (Array.isArray(restart)) hostDetail.restart = restart.filter(field => typeof field === 'string');
                commit(domain, written);
                return 'saved';
            }
            await adapters.writeRuntime(values);
            commit(domain, written);
            return 'applied';
        } catch (error) {
            if (domain === 'host' && MESSAGE_CODES.has(error?.code) && typeof error.message === 'string') {
                const source = error.code === INVALID_SOURCE_CODE && typeof error.source === 'string' ? error.source : null;
                hostDetail.invalid = { source, message: error.message };
            }
            const partial = domain === 'host' ? error?.partial : null;
            if (partial && Array.isArray(partial.saved)) {
                // S10 review I3: part of the host save was written (config.json moved on): keep what saved, adopt the
                // revision it produced, and fail only the rest, so the next save is not a false conflict.
                if (typeof partial.revision === 'string') revision = partial.revision;
                const saved = new Map([...written].filter(([key]) => partial.saved.includes(key)));
                commit(domain, saved);
                fail(domain, new Map([...written].filter(([key]) => !saved.has(key))));
                if (error?.code === CONFLICT_CODE) return 'conflict';
                return saved.size ? 'partial' : 'failed';
            }
            fail(domain, written);
            return error?.code === CONFLICT_CODE ? 'conflict' : 'failed';
        }
    }

    /**
     * Read the host values again; a failed read leaves host saves refused until one succeeds, and `hostState()` says
     * so (System S3: the host rows show that the server settings could not be read).
     */
    const reloadHost = () => load('host').then(() => true, () => { hostRead = 'failed'; notify(); return false; });

    /** Save the dirty domains in order; a Save pressed while one runs joins it. */
    function save() {
        saving ??= (async () => {
            for (const domain of DRAFT_DOMAINS) failedKeys[domain].clear();
            hostDetail = {};
            const result = {};
            for (const domain of DRAFT_DOMAINS) result[domain] = await saveDomain(domain);
            Object.assign(result, hostDetail);
            // Settings changed elsewhere: read them (and their revision), keeping the drafts (Q10).
            if (result.host === 'conflict') await reloadHost();
            if (result.browser === 'conflict') await load('browser').catch(() => {});
            return result;
        })().then(result => {
            saving = null;
            outcome = result;
            notify();
            return result;
        });
        return saving;
    }

    /**
     * A value the control could not take (System S3: a timeout outside 0–300): no draft, the control is highlighted and
     * the status says a change was not saved.
     */
    function reject(domain, key) {
        requireDomain(domain);
        drafts[domain].delete(key);
        failedKeys[domain].add(key);
        outcome = { [domain]: 'failed' };
        notify();
    }

    /**
     * Another page changed saved values this tab has drafts for (a browser `storage` event, System S3): the drafts stay
     * and are highlighted, and the status says the settings changed elsewhere.
     */
    function conflict(domain, keys) {
        requireDomain(domain);
        if (!keys.length) return;
        for (const key of keys) failedKeys[domain].add(key);
        outcome = { [domain]: 'conflict' };
        notify();
    }

    return {
        load,
        reloadHost,
        /** Whether the host values were read: `pending`, `loaded`, or `failed` (the last read failed). */
        hostState: () => hostRead,
        reject,
        conflict,
        /** The saved values of a domain (its last read or save), not the drafts. */
        saved: domain => { requireDomain(domain); return { ...baseline[domain] }; },
        get,
        set,
        dirty,
        discard,
        save,
        changes,
        saving: () => saving !== null,
        failed: domain => { requireDomain(domain); return new Set(failedKeys[domain]); },
        /** The source the last save was refused for (its public path), until the next edit; else null. */
        invalidSource: () => outcome?.invalid?.source ?? null,
        /** The Save row's text: the last save's outcome until the next edit, else unsaved or clean. */
        statusText: () => (outcome ? statusText(outcome) : dirty() ? SETTINGS_STATUS.dirty : SETTINGS_STATUS.clean),
        subscribe(listener) {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
    };
}
