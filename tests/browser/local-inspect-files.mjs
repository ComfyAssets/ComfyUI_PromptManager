// Files for the Inspect page workflows, as Playwright `setInputFiles` payloads ({name, mimeType, buffer}) or as the
// bytes a page builds a File or DataTransfer from. Built from the committed fixtures and the byte-exact PNG builders
// the node tests use (tests/js/helpers/png-bytes.mjs), so the browser reads exactly what the unit tests pin.
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { raw, ihdr, idat, iend, tEXt } from '../js/helpers/png-bytes.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(here, '..', 'fixtures');
const read = (...parts) => fs.readFileSync(path.join(FIXTURES, ...parts));
const file = (name, mimeType, buffer) => ({ name, mimeType, buffer: Buffer.from(buffer) });

/** One committed parity PNG (tests/fixtures/inspection_parity), e.g. f02.png. */
export const parityPng = (name, as = name) => file(as, 'image/png', read('inspection_parity', name));
/** One committed local-inspect fixture (tests/fixtures/local-inspect). */
export const localFixture = (name, mimeType, as = name) => file(as, mimeType, read('local-inspect', name));
/** The committed CC0 VP8 clip with no generation metadata. */
export const tinyWebm = () => file('tiny.webm', 'video/webm', read('tiny.webm'));

/** One decodable 2×2 opaque pixel row set, so a synthetic PNG previews. */
function pixels(width = 2, height = 2) {
    const row = [0, ...Array.from({ length: width }, () => [40, 120, 200, 255]).flat()];
    return zlib.deflateSync(Buffer.from(Array.from({ length: height }, () => row).flat()));
}

/**
 * A previewable PNG whose positive spans lines and whose negative is a ConditioningZeroOut: the legacy fill shows the
 * positive exactly and the ZeroOut guard makes the negative explicitly empty.
 */
export const POSITIVE_LINES = 'a lighthouse at dusk\nsoft rain, long exposure';
export function sendablePng(name = 'send-me.png') {
    const prompt = JSON.stringify({
        3: { class_type: 'KSampler', inputs: { seed: 11, steps: 12, cfg: 5, sampler_name: 'euler', scheduler: 'normal', denoise: 1, model: ['4', 0], positive: ['6', 0], negative: ['8', 0], latent_image: ['5', 0] } },
        4: { class_type: 'CheckpointLoaderSimple', inputs: { ckpt_name: 'local.safetensors' } },
        5: { class_type: 'EmptyLatentImage', inputs: { width: 512, height: 512, batch_size: 1 } },
        6: { class_type: 'CLIPTextEncode', inputs: { text: POSITIVE_LINES, clip: ['4', 1] } },
        8: { class_type: 'ConditioningZeroOut', inputs: { conditioning: ['6', 0] } },
    });
    return file(name, 'image/png', raw(ihdr(2, 2), idat(pixels()), tEXt('prompt', prompt), iend()));
}

/** A PNG whose text chunk fails its CRC: the reader reports the metadata unreadable; the bytes stay downloadable. */
export const corruptPng = (name = 'corrupt.png') =>
    file(name, 'image/png', raw(ihdr(2, 2), idat(pixels()), tEXt('prompt', '{"3":{}}', { crc: 1 }), iend()));

/** Bytes no reader claims, under an image or video name. */
export const disguised = () => [
    file('notes.png', 'image/png', Buffer.from('plain text, not an image')),
    file('page.png', 'image/png', Buffer.from('<!doctype html><title>x</title><p>hi')),
    file('vector.png', 'image/png', Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>')),
    file('stream.mp4', 'video/mp4', Buffer.from('#EXTM3U\n#EXT-X-VERSION:3\n')),
];

/**
 * core-faststart.mp4 with its video sample entry renamed from `avc1` to `zzzz`: the container and its tags read as
 * before, but no browser has a decoder for the stream, so the preview fails deterministically on every engine.
 */
export function undecodableMp4(name = 'undecodable.mp4') {
    const bytes = Buffer.from(read('local-inspect', 'core-faststart.mp4'));
    const at = bytes.indexOf(Buffer.from('avc1', 'latin1'), bytes.indexOf(Buffer.from('stsd', 'latin1')));
    if (at < 0) throw new Error('core-faststart.mp4 has no avc1 sample entry');
    bytes.write('zzzz', at, 'latin1');
    return file(name, 'video/mp4', bytes);
}
