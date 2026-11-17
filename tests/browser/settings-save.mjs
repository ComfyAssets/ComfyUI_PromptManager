/**
 * Saving System settings in a spec (#631).
 *
 * A host save commits config.json durably (fsync, then an atomic replace). On a busy CI disk that write alone can
 * take seconds, longer than the five an expect gives the status line. So a spec waits for the save's answer before
 * it reads the status (TESTING.md: wait for a response, never for time). A save kept in this browser sends nothing
 * and needs no wait.
 */

export const SETTINGS_URL = '/prompt_manager/settings';
export const LORA_ENABLE_URL = '/prompt_manager/lora/enable';

/** The host's answer to the next save sent to `url`. Start it before the click that saves. */
export const hostSaveAnswer = (page, url = SETTINGS_URL) =>
    page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(url));

/** Click `button` and wait for the host's answer to the save it sends to `url`; resolves to that response. */
export async function clickHostSave(page, button, url = SETTINGS_URL) {
    const answer = hostSaveAnswer(page, url);
    await button.click();
    return answer;
}
