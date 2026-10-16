import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { randomUUID } from 'node:crypto';
import { repositoryRoot } from './vendor-viewerjs.mjs';

export async function startCoverage(page, browserName) {
    if (process.env.PM_JS_COVERAGE && browserName === 'chromium') {
        await page.coverage.startJSCoverage({ resetOnNavigation: false });
    }
}
export async function saveCoverage(page, browserName) {
    if (!process.env.PM_JS_COVERAGE || browserName !== 'chromium') return;
    const entries = await page.coverage.stopJSCoverage();
    const result = [];
    for (const entry of entries) {
        const match = entry.url.match(/\/prompt_manager\/js\/([a-z0-9-]+\.js)(?:\?.*)?$/i);
        if (match) result.push({ ...entry, url: pathToFileURL(join(repositoryRoot, 'web/js', match[1])).href });
    }
    mkdirSync(process.env.PM_JS_COVERAGE, { recursive: true });
    writeFileSync(join(process.env.PM_JS_COVERAGE, `browser-${randomUUID()}.json`), JSON.stringify({ result }));
}
