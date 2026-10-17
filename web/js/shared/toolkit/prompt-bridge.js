const VERSION = 1;
const MAX_TEXT = 1048576;
const id = () => {
    if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID();
    // getRandomValues also works on HTTP LAN origins where randomUUID is unavailable.
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
};
const plain = value => value !== null && typeof value === 'object' && [Object.prototype, null].includes(Object.getPrototypeOf(value));
const identifier = value => typeof value === 'string' && value.length > 0 && value.length <= 200;

export function validateRoles(roles) {
    if (!plain(roles)) throw new TypeError('Prompt roles must be an object');
    const keys = Reflect.ownKeys(roles);
    if (!keys.length || keys.some(key => !['positive', 'negative'].includes(key))) throw new TypeError('Unknown or missing prompt role');
    const result = {}; let size = 0;
    for (const key of keys) {
        const descriptor = Object.getOwnPropertyDescriptor(roles, key);
        if (!('value' in descriptor) || typeof descriptor.value !== 'string') throw new TypeError('Prompt roles must be strings');
        size += new TextEncoder().encode(descriptor.value).length;
        if (size > MAX_TEXT) throw new TypeError('Prompt roles are too large');
        result[key] = descriptor.value;
    }
    return result;
}
function normalizedOrigin(value) {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || url.origin === 'null') throw new TypeError('Bridge requires an HTTP(S) origin');
    return url.origin;
}
function sameOrigin(value, expected) {
    try {
        if (globalThis.Origin?.from) {
            const actual = globalThis.Origin.from(value);
            return !actual.opaque && actual.isSameOrigin(globalThis.Origin.from(expected));
        }
        return normalizedOrigin(value) === expected;
    } catch { return false; }
}
function transport(options, receive, fail) {
    if (!identifier(options.namespace)) throw new TypeError('Bridge namespace is required');
    const origin = normalizedOrigin(options.origin);
    const factory = options.channelFactory ?? (name => {
        if (typeof globalThis.BroadcastChannel !== 'function') throw new Error('BroadcastChannel is unavailable');
        return new globalThis.BroadcastChannel(name);
    });
    const channel = factory(`comfyassets:prompt-bridge:v${VERSION}:${options.namespace}`);
    let closed = false;
    const message = event => {
        const data = event.data;
        if (!closed && sameOrigin(event.origin, origin) && plain(data) && data.version === VERSION && data.origin === origin && data.namespace === options.namespace && identifier(data.requestId) && identifier(data.clientId) && ['discover', 'preview', 'apply'].includes(data.op)) receive(data);
    };
    const error = () => fail(new Error('Bridge transport failed'));
    channel.addEventListener('message', message); channel.addEventListener('messageerror', error);
    return {
        send(data) { if (closed) throw new Error('Bridge disposed'); channel.postMessage({ ...data, version: VERSION, origin, namespace: options.namespace }); },
        dispose() { if (closed) return; closed = true; channel.removeEventListener('message', message); channel.removeEventListener('messageerror', error); channel.close(); },
    };
}
function boundedSet(map, key, value, max) {
    map.set(key, value);
    while (map.size > max) map.delete(map.keys().next().value);
}
function duration(value, fallback) {
    return Number.isFinite(value) && value > 0 ? Math.min(value, 2147483647) : fallback;
}

export function createBridgeHost(options) {
    const hostId = options.hostId ?? id();
    if (!identifier(hostId) || typeof options.label !== 'string' || !options.adapter || ['listTargets', 'preview', 'apply'].some(key => typeof options.adapter[key] !== 'function')) throw new TypeError('Invalid host adapter');
    const tickets = new Map(); const replay = new Map();
    const ttl = duration(options.previewTtlMs, 30000);
    const maxTickets = Math.max(1, Math.floor(duration(options.maxTickets, 100)));
    const maxReplay = Math.max(1, Math.floor(duration(options.maxReplay, 200)));
    let disposed = false;
    const dispose = () => { disposed = true; tickets.clear(); replay.clear(); wire.dispose(); };
    const wire = transport(options, data => {
        if (data.kind !== 'request' || (data.op !== 'discover' && data.hostId !== hostId) || (data.op === 'discover' && data.hostId !== undefined)) return;
        const key = JSON.stringify([data.clientId, data.requestId]);
        let fingerprint;
        try { fingerprint = JSON.stringify([data.op, data.hostId, data.targetId, data.roles, data.ticket]); } catch { return; }
        if (fingerprint.length > MAX_TEXT * 6 + 1000) return;
        const previous = replay.get(key);
        if (previous) { if (previous.fingerprint === fingerprint) send(previous.response); return; }
        const response = { kind: 'response', op: data.op, requestId: data.requestId, clientId: data.clientId, hostId };
        try {
            const now = Date.now();
            for (const [ticket, entry] of tickets) if (entry.expires <= now) tickets.delete(ticket);
            if (data.op === 'discover') response.result = { hostId, label: options.label, targets: options.adapter.listTargets() };
            if (data.op === 'preview') {
                if (!identifier(data.targetId)) throw new TypeError('Invalid target');
                const value = options.adapter.preview(data.targetId, validateRoles(data.roles));
                if (!plain(value) || !plain(value.preview) || !Object.hasOwn(value, 'snapshot')) throw new TypeError('Invalid preview');
                const ticket = id();
                response.result = { ...value.preview, ticket };
                // Serialize public output before retaining a snapshot; uncloneable results fail closed.
                structuredClone(response.result);
                boundedSet(tickets, ticket, { clientId: data.clientId, snapshot: value.snapshot, expires: now + ttl }, maxTickets);
            }
            if (data.op === 'apply') {
                if (!identifier(data.ticket)) throw new TypeError('Invalid ticket');
                const entry = tickets.get(data.ticket);
                if (!entry || entry.clientId !== data.clientId) throw new Error('Preview expired or unavailable; preview again');
                tickets.delete(data.ticket);
                response.result = options.adapter.apply(entry.snapshot);
            }
        } catch (error) { response.error = error instanceof Error ? error.message : 'Bridge request failed'; delete response.result; }
        boundedSet(replay, key, { fingerprint, response }, maxReplay);
        send(response);
    }, dispose);
    function send(response) { try { wire.send(response); } catch { dispose(); } }
    return { hostId, dispose, get disposed() { return disposed; } };
}

export function createBridgeClient(options) {
    const clientId = options.clientId ?? id();
    if (!identifier(clientId)) throw new TypeError('Invalid client ID');
    const pending = new Map(); let disposed = false;
    const dispose = (reason = new Error('Bridge disposed')) => {
        if (disposed) return; disposed = true;
        for (const entry of [...pending.values()]) entry.finish(reason);
        wire.dispose();
    };
    const wire = transport(options, data => {
        if (data.kind !== 'response' || data.clientId !== clientId || !identifier(data.hostId)) return;
        const entry = pending.get(data.requestId);
        if (!entry || data.op !== entry.op || (entry.hostId && data.hostId !== entry.hostId)) return;
        if (entry.op === 'discover') {
            if (!data.error && plain(data.result) && data.result.hostId === data.hostId && typeof data.result.label === 'string' && Array.isArray(data.result.targets)) entry.hosts.set(data.hostId, data.result);
        } else if (typeof data.error === 'string') entry.finish(new Error(data.error));
        else if (Object.hasOwn(data, 'result') && (entry.op !== 'preview' || (plain(data.result) && identifier(data.result.ticket)))) entry.finish(null, data.result);
    }, dispose);
    function request(op, payload = {}, options = {}) {
        return new Promise((resolve, reject) => {
            if (disposed) return reject(new Error('Bridge disposed'));
            const signal = options.signal;
            if (signal?.aborted) return reject(new Error('Bridge request aborted'));
            const requestId = id(); const hosts = new Map();
            let timer;
            const abort = () => finish(new Error('Bridge request aborted'));
            const finish = (error, result) => {
                clearTimeout(timer); signal?.removeEventListener('abort', abort); pending.delete(requestId);
                if (error) reject(error); else resolve(result);
            };
            pending.set(requestId, { op, hostId: payload.hostId, hosts, finish });
            signal?.addEventListener('abort', abort, { once: true });
            const timeout = op === 'discover' ? duration(options.waitMs, 150) : duration(options.timeoutMs ?? options.requestTimeoutMs, duration(clientOptions.timeoutMs ?? clientOptions.requestTimeoutMs, 3000));
            timer = setTimeout(() => op === 'discover' ? finish(null, [...hosts.values()]) : finish(new Error('Bridge request timed out')), timeout);
            try { wire.send({ kind: 'request', op, requestId, clientId, ...payload }); } catch (error) { dispose(error); }
        });
    }
    const clientOptions = options;
    return {
        clientId,
        discover: (options = {}) => request('discover', {}, options),
        preview(hostId, targetId, roles, options = {}) {
            try {
                if (!identifier(hostId) || !identifier(targetId)) throw new TypeError('Invalid destination');
                return request('preview', { hostId, targetId, roles: validateRoles(roles) }, options);
            } catch (error) { return Promise.reject(error); }
        },
        apply(hostId, ticket, options = {}) {
            if (!identifier(hostId) || !identifier(ticket)) return Promise.reject(new TypeError('Invalid destination or ticket'));
            return request('apply', { hostId, ticket }, options);
        },
        dispose,
    };
}
