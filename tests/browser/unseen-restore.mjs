// A restore the open workspace page has not noticed yet (#413).
//
// Specs that restore through the API while a page is open check what the page does with a restore it did not see:
// its next write is refused as "Prompt storage was restored since this dialog opened". But the page's Activity store
// reads the job list every 15 s (activity-store.mjs POLL_MS), and a read that lands while the restore job runs makes
// the page follow it: when it finishes, the page is shown again from the restored data (S9, index.mjs
// onRestoreFinished), which closes any open dialog. On a slow runner a restore can take longer than the time left to
// the next read (CI run 40646112668: the restore took 16 s), so whether the page saw it depended on timing.
//
// `unseenRestore` makes it the case these specs mean, every time: while `restoring()` runs, the page's job list reads
// wait, and they go on once it has answered, so they read the restore as already ended, as a tab that read its job
// list just before and just after the restore does. A restore that is not seen running never reloads the page.
const JOBS = '**/prompt_manager/jobs';

/** Run `restoring()` (an API restore) unseen by `page`'s Activity store; resolves with what it resolves with. */
export async function unseenRestore(page, restoring) {
    let answered;
    const done = new Promise(resolve => { answered = resolve; });
    const hold = async route => { await done; await route.fallback(); };
    await page.route(JOBS, hold);
    try {
        return await restoring();
    } finally {
        answered();
        await page.unroute(JOBS, hold);
    }
}
