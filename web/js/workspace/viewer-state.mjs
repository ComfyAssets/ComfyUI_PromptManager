import {imageKey} from './images-state.mjs';
import {inspectPath} from './inspection-state.mjs';

/** A viewer visits only the descriptors loaded by its opening query. */
export function createViewerSession({items, initialKey, onChange = () => {}, setTimer = setTimeout, clearTimer = clearTimeout}) {
    if (!Array.isArray(items) || !items.length) throw new TypeError('A loaded media collection is required');
    const keys = new Set();
    const loaded = Object.freeze(items.map(item => {
        inspectPath(item);
        const key = imageKey(item);
        if (keys.has(key)) throw new TypeError('Duplicate viewer identity');
        keys.add(key);
        return Object.freeze({...item});
    }));
    const index = loaded.findIndex(item => imageKey(item) === initialKey);
    if (index < 0) throw new TypeError('Selected media is not loaded');
    let state = Object.freeze({key:initialKey,index,total:loaded.length,phase:'stopped',requested:false,mediaReady:false});
    function publish(update) {
        state = Object.freeze({...state,...update});
        onChange(state, loaded[state.index]);
    }
    publish({});
    return {
        get state() { return state; },
        navigate(direction) {
            if (state.phase === 'disposed') return;
            if (direction !== -1 && direction !== 1) throw new TypeError('Viewer direction must be -1 or 1');
            if (loaded.length === 1) return;
            const next = (state.index + direction + loaded.length) % loaded.length;
            publish({index:next,key:imageKey(loaded[next]),phase:'stopped',mediaReady:false});
        },
        ready(key) {
            if (state.phase !== 'disposed' && key === state.key) publish({mediaReady:true,phase:'stopped'});
        },
        failed(key) {
            if (state.phase !== 'disposed' && key === state.key) publish({mediaReady:false,phase:'error'});
        },
        dispose() {
            if (state.phase !== 'disposed') publish({phase:'disposed',requested:false,mediaReady:false});
        },
    };
}
