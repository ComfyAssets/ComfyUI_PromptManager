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
