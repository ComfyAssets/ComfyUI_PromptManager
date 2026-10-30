// A job's end toast follows the job, which has no time limit: under load it outlasts the 5 s expect default (#533, as
// #520). Do `action`, whose POST to `url` answers with the job it started, then wait for that job's event stream to end.
export const APPLY_URL = '/prompt_manager/maintenance/apply';
export const RETURN_URL = '/prompt_manager/images/removed/return';

export async function startAndAwaitJob(page, action, url = APPLY_URL) {
    const applied = page.waitForResponse(response => response.url().endsWith(url));
    await action();
    const { job } = await (await applied).json();
    const events = await page.request.get(`/prompt_manager/jobs/${job.id}/events`);
    if (!events.ok()) throw new Error(`Job ${job.id} events answered ${events.status()}`);
    return job;
}
