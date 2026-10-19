import {rootedFileAdapter} from './inspection-state.mjs';

/** A viewer visits only the descriptors loaded by its opening query. */
export function createViewerSession({items, initialKey, onChange = () => {}, setTimer = setTimeout, clearTimer = clearTimeout, adapter = rootedFileAdapter}) {
    if (!Array.isArray(items) || !items.length) throw new TypeError('A loaded media collection is required');
    const keys = new Set();
    const loaded = Object.freeze(items.map(item => {
        adapter.validate(item);
        const key = adapter.key(item);
        if (keys.has(key)) throw new TypeError('Duplicate viewer identity');
        keys.add(key);
        return Object.freeze({...item, ...(adapter !== rootedFileAdapter && item.occurrence ? {occurrence:Object.freeze({...item.occurrence}), reference:Object.freeze({...item.reference})} : {})});
    }));
    const index = loaded.findIndex(item => adapter.key(item) === initialKey);
    if (index < 0) throw new TypeError('Selected media is not loaded');
    let state = Object.freeze({key:initialKey,index,total:loaded.length,phase:'stopped',requested:false,mediaReady:false});
    let timer = null, deadline = 0;
    function cancel() {
        deadline++;
        if (timer !== null) clearTimer(timer);
        timer = null;
    }
    function publish(update) {
        state = Object.freeze({...state,...update});
        onChange(state, loaded[state.index]);
    }
    function phase(ready = state.mediaReady) {
        return !state.requested ? 'stopped' : loaded[state.index].is_video ? 'paused-video' : ready ? 'playing-image' : 'waiting-image';
    }
    function schedule() {
        if (state.phase !== 'playing-image' || timer !== null) return;
        const key = state.key, token = ++deadline;
        timer = setTimer(() => {
            if (token !== deadline || state.phase !== 'playing-image' || !state.requested || state.key !== key) return;
            timer = null;navigate(1);
        },3500);
    }
    function navigate(direction) {
        if (state.phase === 'disposed') return;
        if (direction !== -1 && direction !== 1) throw new TypeError('Viewer direction must be -1 or 1');
        if (loaded.length === 1) return;
        cancel();
        const next = (state.index + direction + loaded.length) % loaded.length;
        publish({index:next,key:adapter.key(loaded[next]),phase:state.requested ? loaded[next].is_video ? 'paused-video' : 'waiting-image' : 'stopped',mediaReady:false});
    }
    publish({});
    return {
        get state() { return state; },
        navigate,
        play() {
            if (state.phase === 'disposed' || state.phase === 'error' || state.requested || loaded.length < 2 || loaded[state.index].is_video) return;
            publish({requested:true,phase:state.mediaReady ? 'playing-image' : 'waiting-image'});
            schedule();
        },
        stop() {
            if (state.phase === 'disposed') return;
            cancel();publish({requested:false,phase:state.phase === 'error' ? 'error' : 'stopped'});
        },
        ready(key) {
            if (state.phase === 'disposed' || key !== state.key || state.mediaReady) return;
            publish({mediaReady:true,phase:phase(true)});schedule();
        },
        failed(key) {
            if (state.phase === 'disposed' || key !== state.key) return;
            cancel();publish({mediaReady:false,phase:'error',requested:false});
        },
        dispose() {
            if (state.phase === 'disposed') return;
            cancel();publish({phase:'disposed',requested:false,mediaReady:false});
        },
    };
}
