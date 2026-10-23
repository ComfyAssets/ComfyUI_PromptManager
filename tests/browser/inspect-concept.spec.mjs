import { writeFile } from 'node:fs/promises';
import { startCoverage, saveCoverage } from '../../tools/vendor/browser-coverage.mjs';
import { test, expect } from './fixtures.mjs';
import { captureCanonicalState } from './history-canonical-comparison.mjs';
import { SIZES, THEMES, seedConcept, openConcept, openProduction, diffInventory, mutate } from './concept-inventory.mjs';
import { parityPng } from './local-inspect-files.mjs';

// PR L2 Task 7 of the local-Inspect plan (#249): the native "Inspect a file" page against the immutable concept's
// `#metadata` (docs/concepts/workspace/app.js renderMetadata 740–748, selection 1715–1729, style.css 709–722,
// 1180–1194, 1543–1545, 1559–1564), at 1600x1100 and 390x844 in both themes, in three states: initial, selected (the
// same synthetic PNG set into both `#local-file` inputs) and viewer-open. The concept is never edited: the recorded
// differences D60–D66 (TESTING.md) are applied to its measurement, and, where they change copy, to its page before it
// is measured, so the browser lays the concept out with production's words. After that the diff must be empty.
// Paired captures are artifacts only; there are no PNG baselines.
test.use({ locale: 'en-US', timezoneId: 'UTC', reducedMotion: 'reduce' });

const FILE = 'f02.png';
/** D60 and D61, the maintainer's LI-3 copy, keyed by the concept's own copy. */
const COPY = {
    action: ['Open sample metadata', 'Open file metadata'],
    intro: ['Keep the existing local-file inspection workflow. Only add the file to your library when you choose to import it.',
        'Read embedded metadata in this browser. The file is not uploaded or added to your library.'],
    chooser: ['Choose an image for a local preview', 'Choose an image or video for a local preview'],
    note: ['This mock previews your file locally. Sample metadata is available with “Open sample metadata”.',
        'Your file stays in this browser. Its metadata opens with “Open file metadata”.'],
    toast: ['Local preview only. No file uploaded; sample metadata is available above.',
        'Local preview only. No file uploaded; metadata is available above.'],
};
/** D62: the concept's image list plus the videos the local readers support. */
const ACCEPT = ['image/png,image/jpeg,image/webp', 'image/png,image/jpeg,image/webp,video/*,.mp4,.mov,.m4v,.webm,.mkv,.avi,.wmv'];

test.beforeEach(async ({ page, browserName }) => {
    await startCoverage(page, browserName);
    page.conceptErrors = [];
    page.on('pageerror', error => page.conceptErrors.push(error.message));
});

test.afterEach(async ({ page, browserName }) => {
    await saveCoverage(page, browserName);
    expect(page.conceptErrors).toEqual([]);
});

/** Runs in the page: the Inspect surface below the heading, and the heading's actions. Self-contained. */
function measureInspect(scope) {
    const main = document.querySelector(scope);
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const name = node => [node.localName, ...[...node.classList].sort()].join('.');
    const font = node => css(node, 'fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'color');
    const origin = main.querySelector(':scope > section.surface');
    const box = node => {
        if (!node || !origin) return null;
        const a = node.getBoundingClientRect(), b = origin.getBoundingClientRect();
        return { left: Math.round(a.left - b.left), top: Math.round(a.top - b.top), width: Math.round(a.width), height: Math.round(a.height) };
    };
    const text = node => node ? node.textContent.replace(/\s+/g, ' ').trim() : null;
    const heading = main.querySelector(':scope > .heading');
    const surface = origin;
    const drop = surface?.querySelector(':scope > .file-drop');
    const input = drop?.querySelector('input#local-file');
    const preview = surface?.querySelector(':scope > #local-preview');
    const media = preview?.querySelector('.local-file-preview');
    return {
        heading: {
            title: text(heading?.querySelector('h1')),
            description: text(heading?.querySelector('p')),
            actions: [...(heading?.querySelectorAll('.heading-actions > *') ?? [])].map(node => ({ name: name(node), text: text(node),
                ...css(node, 'backgroundColor', 'color', 'fontSize', 'paddingTop', 'paddingLeft', 'borderTopLeftRadius'), box: box(node) })),
        },
        surface: surface && {
            children: [...surface.children].map(name), box: box(surface),
            ...css(surface, 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'borderTopWidth', 'borderTopStyle',
                'borderTopColor', 'borderTopLeftRadius', 'backgroundColor'),
            h2: { text: text(surface.querySelector(':scope > h2')), ...font(surface.querySelector(':scope > h2')), box: box(surface.querySelector(':scope > h2')) },
            p: { text: text(surface.querySelector(':scope > p')), ...font(surface.querySelector(':scope > p')),
                ...css(surface.querySelector(':scope > p'), 'marginTop'), box: box(surface.querySelector(':scope > p')) },
        },
        drop: drop && {
            children: [...drop.children].map(name), box: box(drop),
            ...css(drop, 'display', 'placeItems', 'textAlign', 'minHeight', 'rowGap', 'columnGap', 'marginTop', 'paddingTop',
                'paddingRight', 'paddingBottom', 'paddingLeft', 'borderTopWidth', 'borderTopStyle', 'borderTopColor',
                'borderBottomStyle', 'borderLeftStyle', 'borderRightStyle', 'borderTopLeftRadius', 'backgroundColor'),
            icon: { box: box(drop.querySelector(':scope > svg.icon')), viewBox: drop.querySelector(':scope > svg.icon')?.getAttribute('viewBox'),
                d: drop.querySelector(':scope > svg.icon path')?.getAttribute('d') ?? null,
                ...css(drop.querySelector(':scope > svg.icon'), 'color', 'strokeWidth') },
            label: { text: text(drop.querySelector(':scope > label')), for: drop.querySelector(':scope > label')?.htmlFor ?? null,
                ...font(drop.querySelector(':scope > label')), box: box(drop.querySelector(':scope > label')) },
            input: input && { type: input.type, accept: input.accept, multiple: input.multiple, ...css(input, 'maxWidth', 'fontSize', 'color'),
                box: box(input) },
            note: { text: text(drop.querySelector(':scope > span.small')), ...font(drop.querySelector(':scope > span.small')),
                box: box(drop.querySelector(':scope > span.small')) },
        },
        preview: preview && {
            children: [...preview.children].map(name), box: box(preview),
            media: media && { tag: media.localName, alt: media.getAttribute('alt'), blob: media.src.startsWith('blob:'),
                ...css(media, 'width', 'maxHeight', 'objectFit', 'marginTop', 'display'), box: box(media) },
        },
        // The chooser's displayed name: a file input shows the last path segment of its value.
        chosen: input ? { name: input.value.split(/[\\/]/).pop(), files: [...input.files].map(file => file.name) } : null,
    };
}

/** Runs in the page: the open viewer's dialog box, its toolbar controls in order and its rail's sections in order. */
function measureViewer() {
    const dialog = document.querySelector('dialog#viewer-dialog[open]');
    if (!dialog) return null;
    const css = (node, ...keys) => node ? Object.fromEntries(keys.map(key => [key, getComputedStyle(node)[key]])) : null;
    const rect = node => { const b = node.getBoundingClientRect(); return { left: Math.round(b.left), top: Math.round(b.top), width: Math.round(b.width), height: Math.round(b.height) }; };
    const text = node => node.textContent.replace(/\s+/g, ' ').trim();
    const rail = dialog.querySelector('#viewer-details');
    // The rail's sections in document order, whatever wraps them.
    const SECTIONS = [
        ['.panel-heading', () => 'heading'],
        ['.panel-heading + p.small', () => 'filename'],
        ['.tags', node => node.querySelector('button.chip') ? 'tag chips' : 'badges'],
        ['section.prompt-section.positive', () => 'positive'],
        ['section.prompt-section.negative', () => 'negative'],
        ['dl.meta-grid', node => node.closest('details') ? null : 'facts'],
        ['details > summary', node => text(node)],
        ['button.send', node => node.closest('.prompt-section') ? `send ${node.closest('.prompt-section').matches('.positive') ? 'positive' : 'negative'}` : 'send pair'],
        ['button', node => /Prompt history/.test(node.textContent) ? 'Prompt history' : text(node) === 'Copy settings summary' ? 'settings summary' : null],
    ];
    const sections = [...rail.querySelectorAll('*')].map(node => {
        for (const [selector, label] of SECTIONS) if (node.matches(selector)) return label(node);
        return null;
    }).filter(Boolean);
    return {
        dialog: rect(dialog),
        // The layout below the header; the header's height follows the title (D66) and the rail's height its content.
        layout: (() => {
            const header = dialog.querySelector('.dialog-header').getBoundingClientRect().bottom;
            const left = rect(dialog.querySelector('.viewer-left')), side = rect(rail);
            return { left: { ...left, top: Math.round(left.top - header) }, rail: { left: side.left, width: side.width },
                ...css(dialog.querySelector('.viewer-layout'), 'display', 'gridTemplateColumns') };
        })(),
        title: text(dialog.querySelector('#viewer-title')),
        subtitle: text(dialog.querySelector('.dialog-header p')),
        toolbar: [...dialog.querySelectorAll('#viewer-toolbar > button, #viewer-toolbar > a')].map(text),
        railTitle: text(rail.querySelector('.panel-heading h2')),
        sections,
    };
}

/**
 * D60–D62 applied to the open concept page before it is measured: production's action label, copy and accept list,
 * so the browser lays out what follows them. The concept file itself is never touched.
 */
async function withProductionCopy(page) {
    await page.evaluate(({ COPY, ACCEPT }) => {
        const main = document.querySelector('#main');
        const swap = (node, [from, to]) => { if (node.textContent !== from) throw new Error(`concept copy changed: ${node.textContent}`); node.textContent = to; };
        swap(main.querySelector('.heading-actions > button'), COPY.action);
        swap(main.querySelector('section.surface > p'), COPY.intro);
        swap(main.querySelector('.file-drop > label'), COPY.chooser);
        swap(main.querySelector('.file-drop > span.small'), COPY.note);
        const input = main.querySelector('#local-file');
        if (input.accept !== ACCEPT[0]) throw new Error(`concept accept changed: ${input.accept}`);
        input.accept = ACCEPT[1];
    }, { COPY, ACCEPT });
}

/**
 * D66: the viewer of a local file is titled by its filename and has no tag chips and no Prompt history. D117 (LI-4):
 * "Copy settings summary" follows the six facts.
 */
function mapViewerConcept(reference) {
    const sections = reference.sections.filter(section => section !== 'tag chips' && section !== 'Prompt history');
    const facts = sections.indexOf('facts');
    return {
        ...reference,
        title: FILE,
        railTitle: FILE,
        sections: [...sections.slice(0, facts + 1), 'settings summary', ...sections.slice(facts + 1)],
    };
}

// Two frames, then every running transition finished.
const settle = page => page.evaluate(async () => {
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    await Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {})));
});

async function conceptStates(page, theme, testInfo, tag) {
    await openConcept(page, 'metadata', theme);
    await page.mouse.move(0, 0);
    await withProductionCopy(page);
    await settle(page);
    const initial = await page.evaluate(measureInspect, '#main');
    await captureCanonicalState(page, testInfo, `concept-initial-${tag}`);
    await page.locator('#main #local-file').setInputFiles(parityPng(FILE));
    await expect(page.locator('#main #local-preview img.local-file-preview')).toBeVisible();
    await page.locator('#main #local-preview img').evaluate(image => image.decode().catch(() => {}));
    await expect(page.locator('#toast')).toHaveText(COPY.toast[0]);
    await settle(page);
    const selected = await page.evaluate(measureInspect, '#main');
    await captureCanonicalState(page, testInfo, `concept-selected-${tag}`);
    await page.locator('#main .heading-actions > button').click();
    await expect(page.locator('dialog#viewer-dialog[open] #viewer-details .panel-heading')).toBeVisible();
    await page.mouse.move(0, 0);
    await settle(page);
    const viewer = await page.evaluate(measureViewer);
    await captureCanonicalState(page, testInfo, `concept-viewer-${tag}`);
    return { initial, selected, viewer };
}

async function productionStates(page, theme, testInfo, tag) {
    await openProduction(page, 'inspect', theme);
    await page.mouse.move(0, 0);
    const action = page.locator('#workspace-main .heading-actions').getByRole('button', { name: COPY.action[1], exact: true });
    // D60: unavailable, and described by the drop note, until a classified file is chosen.
    await expect(action).toHaveAttribute('aria-disabled', 'true');
    await expect(action).toHaveAttribute('aria-describedby', 'local-file-note');
    await settle(page);
    const initial = await page.evaluate(measureInspect, '#workspace-main');
    await captureCanonicalState(page, testInfo, `production-initial-${tag}`);
    await page.locator('#workspace-main #local-file').setInputFiles(parityPng(FILE));
    await expect(page.locator('#workspace-main #local-preview img.local-file-preview')).toBeVisible();
    await page.locator('#workspace-main #local-preview img').evaluate(image => image.decode().catch(() => {}));
    await expect(page.locator('#workspace-toast')).toHaveText(COPY.toast[1]);
    await expect(action).toHaveAttribute('aria-disabled', 'false');
    // D63: a readable PNG shows no status line.
    await expect(page.locator('#workspace-main #local-preview p.small')).toHaveCount(0);
    await settle(page);
    const selected = await page.evaluate(measureInspect, '#workspace-main');
    await captureCanonicalState(page, testInfo, `production-selected-${tag}`);
    await action.click();
    await expect(page.locator('dialog#viewer-dialog[open] #viewer-details .panel-heading')).toBeVisible();
    await expect(page.locator('dialog#viewer-dialog[open] #viewer-details .meta-grid').first()).toBeVisible();
    await page.mouse.move(0, 0);
    await settle(page);
    const viewer = await page.evaluate(measureViewer);
    await captureCanonicalState(page, testInfo, `production-viewer-${tag}`);
    return { initial, selected, viewer };
}

/** The concept's fixed CSS values (style.css 709–722, 1180–1194, 1543–1545, 1559–1564), restated as the plan pins them. */
function expectPinnedValues(state) {
    expect(state.surface).toMatchObject({ paddingTop: '22px', paddingLeft: '22px', borderTopLeftRadius: '7px', borderTopStyle: 'solid' });
    expect(state.surface.h2).toMatchObject({ fontSize: '17px', fontWeight: '600' });
    expect(state.surface.p).toMatchObject({ fontSize: '12px' });
    expect(state.drop).toMatchObject({ display: 'grid', textAlign: 'center', minHeight: '170px', rowGap: '12px', columnGap: '12px',
        marginTop: '20px', paddingTop: '22px', paddingLeft: '22px', borderTopStyle: 'dashed', borderBottomStyle: 'dashed',
        borderLeftStyle: 'dashed', borderRightStyle: 'dashed', borderTopWidth: '1px', borderTopLeftRadius: '7px' });
    expect(state.drop.input).toMatchObject({ maxWidth: '100%', fontSize: '12px' });
}

for (const [width, height] of SIZES) for (const theme of THEMES) {
    test(`inspect surface matches the concept ${width}x${height} ${theme}`, async ({ page, request }, testInfo) => {
        await page.setViewportSize({ width, height });
        await seedConcept(request);
        const tag = `${theme}-${width}`;
        const concept = await conceptStates(page, theme, testInfo, tag);
        const production = await productionStates(page, theme, testInfo, tag);
        const mappedViewer = mapViewerConcept(concept.viewer);
        const diff = {
            initial: diffInventory(concept.initial, production.initial),
            selected: diffInventory(concept.selected, production.selected),
            viewer: diffInventory(mappedViewer, production.viewer),
        };
        const path = testInfo.outputPath('inspect-geometry.json');
        await writeFile(path, JSON.stringify({ concept, production, diff }, null, 2));
        await testInfo.attach('inspect-geometry', { path, contentType: 'application/json' });

        // The concept is what this comparison assumes: its structure, its own copy (swapped above) and its chooser.
        expect(concept.initial.surface.children).toEqual(['h2', 'p', 'div.file-drop.spaced-20', 'div']);
        expect(concept.initial.drop.children).toEqual(['svg.icon', 'label', 'input', 'span.small']);
        expect(concept.selected.preview.children).toEqual(['img.local-file-preview']);
        expect(concept.selected.chosen).toEqual({ name: FILE, files: [FILE] });
        expect(concept.viewer.toolbar).toEqual(['← Previous', '−', '+', 'Fit', 'Rotate', 'Flip', 'Play', 'Download', 'Next →']);

        expect(diff).toEqual({ initial: [], selected: [], viewer: [] });
        for (const state of [production.initial, production.selected]) expectPinnedValues(state);
        expect(production.selected.preview.media).toMatchObject({ tag: 'img', alt: 'Local file preview', blob: true,
            maxHeight: '400px', objectFit: 'contain', marginTop: '20px' });
        // The chooser names the chosen file after a real setInputFiles, as the concept's does.
        expect(production.initial.chosen).toEqual({ name: '', files: [] });
        expect(production.selected.chosen).toEqual({ name: FILE, files: [FILE] });
        // D66 in the rail: no tag chips and no Prompt history; the title is the filename. D117 follows the facts.
        expect(production.viewer.sections).toEqual(['heading', 'filename', 'badges', 'positive', 'send positive', 'negative',
            'send negative', 'facts', 'settings summary', 'File & provenance', 'Workflow & raw metadata', 'send pair']);
    });
}

test('mutation is noticed', async ({ page, request }, testInfo) => {
    await page.setViewportSize({ width: 1600, height: 1100 });
    await seedConcept(request);
    await openConcept(page, 'metadata', 'dark');
    await withProductionCopy(page);
    const concept = await page.evaluate(measureInspect, '#main');
    await openProduction(page, 'inspect', 'dark');
    await page.mouse.move(0, 0);
    expect(diffInventory(concept, await page.evaluate(measureInspect, '#workspace-main'))).toEqual([]);

    // A solid border on the drop area is noticed on every side and nowhere else.
    const undoCss = await mutate(page, { css: '#workspace-main .file-drop { border-style: solid; }' });
    expect(diffInventory(concept, await page.evaluate(measureInspect, '#workspace-main')).map(item => item.path))
        .toEqual(['drop.borderTopStyle', 'drop.borderBottomStyle', 'drop.borderLeftStyle', 'drop.borderRightStyle']);
    await undoCss();
    // A renamed label is noticed (its text; its box only if the new words lay out differently).
    const label = page.locator('#workspace-main .file-drop > label');
    await label.evaluate(node => { node.dataset.original = node.textContent; node.textContent = 'Choose a file'; });
    const renamed = diffInventory(concept, await page.evaluate(measureInspect, '#workspace-main')).map(item => item.path);
    expect(renamed).toContain('drop.label.text');
    expect(renamed.every(path => path.startsWith('drop.label.'))).toBe(true);
    await label.evaluate(node => { node.textContent = node.dataset.original; delete node.dataset.original; });
    // A removed note is noticed by its path.
    const undoDom = await mutate(page, { remove: ['#workspace-main .file-drop > span.small'] });
    expect(diffInventory(concept, await page.evaluate(measureInspect, '#workspace-main')).map(item => item.path))
        .toEqual(expect.arrayContaining(['drop.children.3', 'drop.note.text']));
    await undoDom();
    expect(diffInventory(concept, await page.evaluate(measureInspect, '#workspace-main'))).toEqual([]);
    await captureCanonicalState(page, testInfo, 'production-after-mutations');
});
