// The node table local Inspect resolves with (#249 local Inspect, PR L3 Task 10): web/js/inspection-semantics.json,
// the very table the server's inspector reads, fetched once per page through the API client (the proxy-mount rule).
// The Inspect page asks for it as it mounts, before any file is chosen; it is the only static file Inspect fetches,
// and the request carries nothing about a selection. A table that cannot be fetched or used is null: the roles then
// answer as the server does when its table is unusable, and a later selection asks again.
import { compileSemantics } from './local-resolver.mjs';

export const SEMANTICS_PATH = '/prompt_manager/js/inspection-semantics.json';
let pending = null;

async function fetchSemantics() {
    const client = globalThis.window?.ApiClient;
    if (typeof client?.resolveUrl !== 'function' || typeof globalThis.fetch !== 'function') return null;
    const response = await globalThis.fetch(client.resolveUrl(SEMANTICS_PATH), { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`Inspection node table request failed (${response.status})`);
    return compileSemantics(await response.json());
}

/** The compiled table (one request per page), or null when it is unavailable. */
export function loadInspectionSemantics() {
    if (pending !== null) return pending;
    const current = pending = fetchSemantics().catch(() => null).then(table => {
        if (table === null && pending === current) pending = null;
        return table;
    });
    return current;
}
