import { writeFile } from 'node:fs/promises';
import { expect } from '../../tools/vendor/node_modules/@playwright/test/index.mjs';

// Content, row counts, and image bytes come from independent synthetic datasets.
// Saved choices and actual executions are separate production sections. The concept's
// illustrative repeat dialog is compared with Recorded run for native behavior;
// frozen evidence and current-file/unknown copy intentionally describe actual data.
// Parent acceptance scopes exact comparisons to history geometry/roles/native state.
// No content is masked and the immutable concept is never rewritten for comparison.
export const canonicalDifferences = {
    data: ['Independent synthetic text, image bytes, IDs, counts and text wrapping'],
    semantics: [
    'Saved combinations are distinct from the additional chronological executions',
    'Recorded run exposes exact frozen evidence; Repeat run is illustrative',
    'Current-file candidates disclose unknown identity and context membership',
    ],
    remainingApplicationPolish: [
        'Gallery card select checkbox (#249 D8 follow-up) and tag button (#249 D9, image-annotations slice) remain outside #209',
        'Shared role-block spacing, padding and glyph styling remain outside #209; exact role order/actions and evidence disclosure are covered',
        'Production keeps mobile toolbar inside media panel and readable scrolling details; the concept toolbar collision is not reproduced',
    ],
};

export async function captureCanonicalState(page, testInfo, name, selector) {
    await page.evaluate(async () => {
        await document.fonts.ready;
        for (const image of document.images) image.loading = 'eager';
        await Promise.all([...document.images].map(image => image.decode().catch(() => {})));
    });
    const target = selector ? page.locator(selector).first() : page;
    const path = testInfo.outputPath(`${name}.png`);
    await target.screenshot({ path, animations: 'disabled' });
    await testInfo.attach(name, { path, contentType: 'image/png' });
}

export async function historyGeometry(page, production) {
    const selectors = production ? {
        surface: '.library-surface', timeline: '.library-branches', positive: '.library-positive-lane',
        branch: '.library-variant-lane', label: '.library-variant-lane span', dot: '.library-variant-dot[aria-pressed="true"]',
        positiveLabel: '.library-positive-lane span',
        role: '[aria-label="Saved prompt texts"] .prompt-section',
        prompt: '[aria-label="Saved prompt texts"] .prompt-text', gallery: '.library-shared-gallery',
        heading: '.library-variants h2', intro: '.library-variants > p.muted',
        footer: '.library-variant-footer', all: '.library-variants button:last-child',
        recordedPositiveLabel: '.history-positive-lane span', recordedBranch: '.history-context-branch',
        recordedHeading: '.recorded-history h2', recordedTimeline: '.history-timeline', recordedCanvas: '.history-canvas',
        recordedPositive: '.history-positive-lane', recordedLabel: '.history-context-label',
        recordedAnchor: '.history-context-anchor', recordedRepeat: '.history-run-marker:not(.history-context-anchor)',
        recordedMarker: '.history-run-marker', recordedColumn: '.history-run-column',
    } : {
        surface: '.surface', timeline: '.timeline', positive: '.positive-lane', branch: '.variant-lane',
        label: '.variant-lane span', dot: '.variant-dot[aria-pressed="true"]', role: '.surface .prompt-section',
        positiveLabel: '.positive-lane span',
        prompt: '.surface .prompt-text', gallery: '.history-gallery',
        heading: '.timeline-panel h2', intro: '.timeline-panel .panel-heading .small',
        footer: '.timeline-lower', all: '.timeline-lower button',
        repeat: '.timeline .run-dot',
    };
    return page.evaluate(selectors => ({ pointerCoarse: matchMedia('(pointer: coarse)').matches,
        ...Object.fromEntries(Object.entries(selectors).map(([key, selector]) => {
        const nodes = [...document.querySelectorAll(selector)];
        return [key, nodes.map(node => {
            const style = getComputedStyle(node), before = getComputedStyle(node, '::before');
            const box = node.getBoundingClientRect();
            return { x: box.x, y: box.y, width: box.width, height: box.height,
                padding: style.padding, top: style.top, left: style.left, background: style.backgroundColor, color: style.color,
                border: style.borderTopWidth, borderColor: style.borderTopColor, radius: style.borderRadius,
                fontSize: style.fontSize, fontFamily: style.fontFamily, lineHeight: style.lineHeight,
                fontWeight: style.fontWeight, text: node.textContent, contextId: node.dataset.contextId || node.dataset.contextBranch,
                runId: node.dataset.runId,
                marginLeft: style.marginLeft, outline: style.outlineWidth, outlineColor: style.outlineColor,
                outlineOffset: style.outlineOffset, before: { width: before.width, height: before.height,
                    top: before.top, left: before.left, borderLeft: before.borderLeftWidth,
                    borderBottom: before.borderBottomWidth, radius: before.borderBottomLeftRadius,
                    background: before.backgroundColor, color: before.color, borderColor: before.borderTopColor,
                    borderLeftColor: before.borderLeftColor, borderBottomColor: before.borderBottomColor,
                    fontSize: before.fontSize, fontFamily: before.fontFamily, fontWeight: before.fontWeight,
                    content: before.content } };
        })];
    })) }), selectors);
}

export async function nativeDialogState(locator) {
    return locator.evaluate(node => {
        const style = getComputedStyle(node), box = node.getBoundingClientRect();
        return { modal: node.matches(':modal'), focusInside: node.contains(document.activeElement),
            background: style.backgroundColor, color: style.color, borderColor: style.borderTopColor,
            border: style.borderTopWidth, width: box.width, height: box.height,
            viewportWidth: innerWidth, viewportHeight: innerHeight };
    });
}

export async function exerciseCurrentPairSend(page, roles) {
    const host = await page.context().newPage();
    await host.goto('/prompt_manager/workspace#/overview');
    try {
        await host.evaluate(async () => {
            const { createBridgeHost } = await import('/prompt_manager/js/shared/toolkit/prompt-bridge.js');
            const target = { id: 'canonical-pair', label: 'Canonical destination', type: 'PromptManagerPair', roles: ['positive', 'negative'] };
            window.canonicalApplied = [];
            window.canonicalHost = createBridgeHost({ origin: location.origin, namespace: '/', label: 'Canonical ComfyUI', adapter: {
                listTargets: () => [target],
                preview: (_id, roles) => ({ preview: { target, fields: Object.entries(roles).map(([role, after]) => ({ role, before: '', after })),
                    composition: { prepend: '', append: '', prependConnected: false, appendConnected: false, before: '', after: roles.positive } }, snapshot: roles }),
                apply: roles => { window.canonicalApplied.push(roles);return { targetId: target.id, values: roles }; },
            } });
        });
        const viewer = page.locator('dialog#viewer-dialog');
        const send = viewer.getByRole('button', { name: 'Send prompt pair', exact: true });await send.click();
        const sender = page.getByRole('dialog', { name: 'Send to ComfyUI', exact: true });await expect(sender).toBeVisible();
        await sender.getByRole('combobox', { name: 'ComfyUI window', exact: true }).selectOption({ label: 'Canonical ComfyUI' });
        await sender.getByRole('combobox', { name: 'Destination node', exact: true }).selectOption({ label: 'Canonical destination' });
        await sender.getByRole('button', { name: 'Preview changes', exact: true }).click();
        await expect(sender).toContainText(roles.positive);await expect(sender).toContainText(roles.negative);
        await sender.getByRole('button', { name: 'Apply to ComfyUI', exact: true }).click();await expect(sender).toContainText('Sent to ComfyUI.');
        expect(await host.evaluate(() => window.canonicalApplied)).toEqual([roles]);
        await page.keyboard.press('Escape');await expect(send).toBeFocused();
    } finally {await host.evaluate(() => window.canonicalHost?.dispose());await host.close();}
}

export async function compareCanonicalHistory(reference, actual, testInfo) {
    const path = testInfo.outputPath('canonical-history-geometry.json');
    await writeFile(path, JSON.stringify({ reference, actual, comparisonScope: canonicalDifferences }, null, 2));
    await testInfo.attach('canonical-history-geometry', { path, contentType: 'application/json' });
    for (const geometry of [reference, actual]) {
        expect.soft(geometry.role).toHaveLength(2);
        expect.soft(geometry.prompt).toHaveLength(2);
        expect.soft(geometry.heading[0].fontSize).toBe('14px');
        expect.soft(geometry.intro[0].fontSize).toBe('12px');
        expect.soft(geometry.footer).toHaveLength(1);
        expect.soft(geometry.positiveLabel).toHaveLength(1);
        expect.soft(geometry.positiveLabel[0]).toMatchObject({ top: '-25px', left: '38px', fontSize: '10px' });
        expect.soft(geometry.surface[0].padding).toBe('22px');
        expect.soft(geometry.surface[0].radius).toBe('7px');
        expect.soft(geometry.timeline[0].padding).toBe('18px 20px');
        expect.soft(geometry.positive[0].border).toBe('2px');
        for (const [index, branch] of geometry.branch.entries()) {
            expect.soft(branch.height).toBe(57);
            expect.soft(branch.border).toBe('2px');
            expect.soft(branch.marginLeft).toBe(`${38 + index * 47}px`);
            expect.soft(branch.before).toMatchObject({ width: '30px', height: '25px', top: '-25px', left: '-30px', borderLeft: '2px', borderBottom: '2px', radius: '22px' });
        }
    }
    expect.soft(actual.recordedPositiveLabel).toHaveLength(1);
    expect.soft(actual.recordedPositiveLabel[0]).toMatchObject({ top: '-25px', left: '38px', fontSize: '10px' });
    expect.soft(actual.recordedHeading[0].fontSize).toBe(reference.heading[0].fontSize);
    expect.soft(actual.recordedCanvas[0].width).toBeGreaterThanOrEqual(actual.recordedTimeline[0].width - 42);
    for (const geometry of [reference, actual]) {
        expect.soft(geometry.runDialog).toMatchObject({ modal: true, focusInside: true, border: '1px' });
        expect.soft(geometry.runDialog.width).toBeLessThanOrEqual(geometry.runDialog.viewportWidth);
        expect.soft(geometry.runDialog.height).toBeLessThanOrEqual(geometry.runDialog.viewportHeight);
    }
    for (const property of ['background', 'color', 'borderColor']) expect.soft(actual.runDialog[property]).toBe(reference.runDialog[property]);
    for (const [key, properties] of [
        ['surface', ['padding', 'background', 'border', 'borderColor', 'radius']],
        ['timeline', ['padding', 'background', 'border', 'borderColor', 'radius']],
        ['positive', ['border', 'borderColor']],
        ['positiveLabel', ['color', 'fontSize', 'top', 'left']],
        ['branch', ['height', 'border', 'borderColor', 'fontSize']],
        ['label', ['color', 'fontSize']],
        ['dot', ['background', 'borderColor', 'radius', 'outline', 'outlineColor', 'outlineOffset', 'color', 'fontSize']],
    ]) {
        for (const property of properties) expect.soft(actual[key][0]?.[property], `${key}.${property}`).toBe(reference[key][0][property]);
    }
    for (const property of ['color', 'background']) expect.soft(actual.positive[0].before[property]).toBe(reference.positive[0].before[property]);
    for (const [index, block] of actual.role.entries()) for (const property of ['border', 'borderColor', 'fontSize']) expect.soft(block[property]).toBe(reference.role[index][property]);
    for (const [index, prompt] of actual.prompt.entries()) for (const property of ['fontSize', 'lineHeight', 'fontFamily']) expect.soft(prompt[property]).toBe(reference.prompt[index][property]);
    assertRecordedHistory(reference, actual, { branchCount: 1, anchorCount: 1, repeatCount: 0 });
}

export function assertRecordedHistory(reference, actual, { branchCount, anchorCount, repeatCount }) {
    expect(branchCount).toBeGreaterThan(0);
    expect(reference.branch.length).toBeGreaterThanOrEqual(2);
    expect(reference.dot).toHaveLength(1);
    expect(reference.repeat.length).toBeGreaterThan(0);
    expect.soft(actual.recordedBranch, 'chronological branches').toHaveLength(branchCount);
    expect.soft(actual.recordedLabel, 'chronological labels').toHaveLength(branchCount);
    expect.soft(actual.recordedAnchor, 'chronological anchors').toHaveLength(anchorCount);
    expect.soft(actual.recordedRepeat, 'chronological repeats').toHaveLength(repeatCount);
    expect.soft(actual.recordedMarker).toHaveLength(anchorCount + repeatCount);
    expect.soft(actual.recordedColumn).toHaveLength(anchorCount + repeatCount);
    expect.soft(actual.recordedPositive).toHaveLength(1);
    for (const property of ['border', 'borderColor']) expect.soft(actual.recordedPositive[0][property], `recorded positive ${property}`).toBe(reference.positive[0][property]);
    for (const property of ['color', 'background', 'width', 'height', 'fontSize']) expect.soft(actual.recordedPositive[0].before[property], `recorded positive glyph ${property}`).toBe(reference.positive[0].before[property]);
    for (const [index, branch] of actual.recordedBranch.entries()) {
        for (const property of ['height', 'border', 'borderColor']) expect.soft(branch[property], `recorded branch ${index} ${property}`).toBe(reference.branch[0][property]);
        for (const property of ['width', 'height', 'top', 'left', 'borderLeft', 'borderBottom', 'radius', 'borderLeftColor', 'borderBottomColor']) expect.soft(branch.before[property], `recorded curve ${index} ${property}`).toBe(reference.branch[0].before[property]);
        if (index) expect.soft(branch.y - actual.recordedBranch[index - 1].y, 'recorded 57px branch rhythm').toBe(reference.branch[1].y - reference.branch[0].y);
    }
    for (const label of actual.recordedLabel) for (const property of ['color', 'fontSize']) expect.soft(label[property], `recorded label ${property}`).toBe(reference.label[0][property]);
    for (const [index, column] of actual.recordedColumn.entries()) {
        expect.soft(column.left).toBe(`${38 + index * 64}px`);
        if (index) expect.soft(column.x - actual.recordedColumn[index - 1].x, '64px chronological columns').toBe(64);
    }
    const checkMarker = (marker, canonical, anchor) => {
        const visual = actual.pointerCoarse ? marker.before : marker;
        const size = anchor ? 29 : 12;
        expect.soft(visual.width, 'recorded marker visual width').toBe(actual.pointerCoarse ? `${size}px` : size);
        expect.soft(visual.height, 'recorded marker visual height').toBe(actual.pointerCoarse ? `${size}px` : size);
        for (const property of ['background', 'borderColor', 'radius']) expect.soft(visual[property], `recorded marker ${property}`).toBe(canonical[property]);
        if (actual.pointerCoarse) {
            expect.soft(marker.width, 'coarse marker hit width').toBeGreaterThanOrEqual(44);
            expect.soft(marker.height, 'coarse marker hit height').toBeGreaterThanOrEqual(44);
            expect.soft(visual.borderLeft).toBe('2px');
        } else expect.soft(marker.border).toBe(canonical.border);
        if (anchor) {
            expect.soft(marker.text).toBe('−');
            if (actual.pointerCoarse) expect.soft(visual.content).toBe('"−"');
            for (const property of ['color', 'fontSize', 'fontFamily', 'fontWeight']) expect.soft(visual[property], `recorded minus ${property}`).toBe(canonical[property]);
            const branch = actual.recordedBranch.find(value => value.contextId === marker.contextId);
            expect(branch, 'each context anchor has its real branch').toBeTruthy();
            expect.soft(Math.abs(marker.x + marker.width / 2 - branch.x)).toBeLessThanOrEqual(1);
            expect.soft(Math.abs(marker.y + marker.height / 2 - branch.y)).toBeLessThanOrEqual(1);
        } else expect.soft(marker.text).toBe('');
    };
    for (const marker of actual.recordedAnchor) checkMarker(marker, reference.dot[0], true);
    for (const marker of actual.recordedRepeat) checkMarker(marker, reference.repeat[0], false);
}
