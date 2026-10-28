import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { mapBrowserCoverage } from '@comfyassets/toolkit/testing/coverage';
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
    // The source is left out (#432): c8 reads it from the mapped file on disk, and repeating every script's text in every
    // raw file made the coverage merge run out of memory.
    const result = mapBrowserCoverage(entries, { urlPrefix: '/prompt_manager/js/', sourceRoot: join(repositoryRoot, 'web/js') })
        .map(({ source: _source, ...entry }) => entry);
    mkdirSync(process.env.PM_JS_COVERAGE, { recursive: true });
    writeFileSync(join(process.env.PM_JS_COVERAGE, `browser-${randomUUID()}.json`), JSON.stringify({ result }));
}
