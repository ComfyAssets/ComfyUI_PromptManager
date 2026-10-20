import { metadataEnvelope } from './file-inspector.mjs';
import { countOf } from './plural.mjs';

/** The Images selection bar's "Export metadata" (#249 D8): one JSON bundle of the inspector's envelopes. */
export const BUNDLE_FILENAME = 'prompt-manager-images-metadata.json';
const BUNDLE_FORMAT = 'prompt-manager-images-metadata';
const EXPORT_CONCURRENCY = 4;

export function metadataBundle(envelopes, exportedAt) {
    return {format:BUNDLE_FORMAT, version:1, exported_at:exportedAt, images:[...envelopes]};
}

/**
 * Read every image's inspection record, at most four at a time, and keep the envelopes in the images' order.
 * A read that fails, or a record without full metadata, counts as one image that could not be read.
 */
export async function collectImagesMetadata(images, read) {
    const envelopes = new Array(images.length).fill(null);
    let next = 0;
    async function worker() {
        while (next < images.length) {
            const index = next++;
            try {
                envelopes[index] = metadataEnvelope(await read(images[index]));
            } catch {
                // Counted below with the records that carry no full metadata.
            }
        }
    }
    await Promise.all(Array.from({length:Math.min(EXPORT_CONCURRENCY, images.length)}, worker));
    const found = envelopes.filter(envelope => envelope !== null);
    return {envelopes:found, failed:images.length - found.length};
}

/** The export's toast; with nothing exported there is no download. */
export function exportOutcome(exported, total) {
    if (!exported) return 'Metadata could not be read. Try again.';
    if (exported === total) return `Exported metadata for ${countOf(exported, 'image')}.`;
    return `Exported metadata for ${exported} of ${countOf(total, 'image')}. ${total - exported} could not be read.`;
}
