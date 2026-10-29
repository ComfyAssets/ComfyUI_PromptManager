// A restore the open workspace page has not noticed yet (#413).
//
// Specs that restore through the API while a page is open check what the page does with a restore it did not see:
// its next write is refused as "Prompt storage was restored since this dialog opened". But the page's Activity store
// reads the job list every 15 s (activity-store.mjs POLL_MS), and a read the server answers while the restore job runs
// makes the page follow it: when it finishes, the page is shown again from the restored data (S9, index.mjs
// onRestoreFinished), which closes any open dialog. On a slow runner a restore can take longer than the time left to
// the next read (CI run 40646112668: the restore took 16 s), so whether the page saw it depended on timing.
//
// `armUnseenRestores(page)`, before the page loads, passes every job-list read of the page through one route, so it
// knows which reads the server has not answered yet. `unseenRestore(page, restoring)` then closes the gate, waits
// for those reads' answers (sent before the restore began, they cannot list it), runs `restoring()`, and only after
// it has answered lets the held reads go on: they read the restore as already ended, as a tab that read its job list
// just before and just after the restore does. A restore that is never seen running never reloads the page.
const JOBS = '**/prompt_manager/jobs';
const gates = new WeakMap();

/** Route `page`'s job-list reads through the restore gate. Call before the page's first navigation. */
export async function armUnseenRestores(page) {
    const gate = { closed: null, inFlight: 0, idle: [] };
    gates.set(page, gate);
    await page.route(JOBS, async route => {
        if (gate.closed) {
            await gate.closed;
            return route.fallback();
        }
        gate.inFlight += 1;
        try {
            const response = await route.fetch();
            await route.fulfill({ response }).catch(() => {});
        } catch {
            await route.abort().catch(() => {});
        } finally {
            gate.inFlight -= 1;
            if (gate.inFlight === 0) for (const resolve of gate.idle.splice(0)) resolve();
        }
    });
}

/** Run `restoring()` (an API restore) unseen by `page`'s Activity store; resolves with what it resolves with. */
export async function unseenRestore(page, restoring) {
    const gate = gates.get(page);
    if (!gate) throw new Error('armUnseenRestores(page) must run before the page loads');
    let open;
    gate.closed = new Promise(resolve => { open = resolve; });
    try {
        // Reads already on their way were sent before the restore: their answers cannot list it.
        if (gate.inFlight > 0) await new Promise(resolve => gate.idle.push(resolve));
        return await restoring();
    } finally {
        gate.closed = null;
        open();
    }
}

// A restore the open page does see run and finish (#415), driven by the test instead of the 15 s poll's timing. The
// job is shaped as the server's (py/api/backup_restore.py: kind `restore`, one `errors` group, not cancellable).
const RESTORE_JOB_ID = '415415415415415415415415415415ab';
const restoreJob = state => ({ id: RESTORE_JOB_ID, kind: 'restore', title: 'Database restore', state, started_at: 1,
    finished_at: state === 'running' ? null : 2, progress: 0, status_line: state === 'running' ? '' : 'Restored the test snapshot.',
    processed: 0, total: 0, counters: { errors: 0 }, scope: {}, listed: { errors: 0 }, log_length: 0, cancellable: false });
/** The Activity pill's link while the page follows that restore. */
export const SEEN_RESTORE_PILL = `#/activity/${RESTORE_JOB_ID}`;
/** The toast the page shows when it sees that restore finish (activity-kinds.mjs `restore.finishToast`). */
export const SEEN_RESTORE_TOAST = 'Database restore finished · Restored the test snapshot.';

/**
 * Call before the page's first navigation, after `armUnseenRestores(page)` (a later route wins). The page's first
 * job-list read lists a restore job running, so its Activity store follows it; its job stream is refused, so the store
 * reads the list again, and every later read is held until `finish()`, which answers them with the job finished and
 * resolves once one of those answers reached the page. The real restore the test runs meanwhile goes through the API.
 */
export async function followRestore(page) {
    let release;
    const released = new Promise(resolve => { release = resolve; });
    let first = true;
    await page.route(`${JOBS}/*/events`, route => route.abort().catch(() => {}));
    await page.route(JOBS, async route => {
        if (first) {
            first = false;
            return route.fulfill({ json: { success: true, jobs: [restoreJob('running')] } }).catch(() => {});
        }
        await released;
        // A read the page has aborted since (a newer read supersedes it) can no longer be answered.
        return route.fulfill({ json: { success: true, jobs: [restoreJob('finished')] } }).catch(() => {});
    });
    return {
        async finish() {
            const answered = page.waitForResponse(response => new URL(response.url()).pathname === '/prompt_manager/jobs');
            release();
            await answered;
        },
    };
}
