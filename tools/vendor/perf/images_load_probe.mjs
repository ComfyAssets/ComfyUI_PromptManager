// Maintainer tool, run by hand (never in CI): how long the workspace Images
// page takes to show its first thumbnail (#348, TESTING.md "Images listing
// cache (#348)").
//
// Once:  npm ci --prefix tools/vendor
//        npx --prefix tools/vendor playwright install chromium
// Then:  node tools/vendor/perf/images_load_probe.mjs http://127.0.0.1:8188
//
// Prints the time to the first loaded thumbnail, the time to network idle and
// the 10 slowest requests. Each run opens a fresh browser context (no cache).
import { chromium } from '@playwright/test';

const SLOWEST = 10;
const TIMEOUT_MS = 120_000;

function usage() {
    console.error('usage: node tools/vendor/perf/images_load_probe.mjs <base-url>');
    console.error('example: node tools/vendor/perf/images_load_probe.mjs http://127.0.0.1:8188');
    process.exit(2);
}

function parseBase(argument) {
    if (!argument) usage();
    let url;
    try {
        url = new URL(argument);
    } catch {
        usage();
    }
    if (url.protocol !== 'http:' && url.protocol !== 'https:') usage();
    return url.origin + url.pathname.replace(/\/+$/, '');
}

const ms = value => `${Math.round(value)} ms`;

async function probe(base) {
    const browser = await chromium.launch();
    try {
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
        const page = await context.newPage();
        const requests = [];
        page.on('requestfinished', request => {
            const timing = request.timing();
            requests.push({
                url: request.url().replace(base, ''),
                status: null,
                ms: timing.responseEnd >= 0 ? timing.responseEnd : NaN,
                response: request.response(),
            });
        });
        const started = Date.now();
        await page.goto(`${base}/prompt_manager/workspace#/images`, { timeout: TIMEOUT_MS });
        // The first Images thumbnail the browser has decoded.
        await page.waitForFunction(() => [...document.images].some(image =>
            image.complete && image.naturalWidth > 0
            && image.src.includes('/prompt_manager/images/serve/')), null,
        { timeout: TIMEOUT_MS, polling: 'raf' });
        const firstThumbnail = Date.now() - started;
        await page.waitForLoadState('networkidle', { timeout: TIMEOUT_MS });
        const networkIdle = Date.now() - started;
        for (const entry of requests) {
            entry.status = (await entry.response)?.status() ?? 'n/a';
        }
        const slowest = requests.filter(entry => Number.isFinite(entry.ms))
            .sort((a, b) => b.ms - a.ms).slice(0, SLOWEST);
        console.log(`first thumbnail: ${ms(firstThumbnail)}`);
        console.log(`network idle:    ${ms(networkIdle)}`);
        console.log(`requests:        ${requests.length}`);
        console.log(`slowest ${slowest.length}:`);
        for (const entry of slowest) {
            console.log(`  ${ms(entry.ms).padStart(9)}  ${String(entry.status).padEnd(3)}  ${entry.url}`);
        }
    } finally {
        await browser.close();
    }
}

probe(parseBase(process.argv[2])).catch(error => {
    console.error(`probe failed: ${error.message}`);
    process.exit(1);
});
