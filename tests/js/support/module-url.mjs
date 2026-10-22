// A dynamic import() takes a URL, never a filesystem path: on Windows a path such
// as D:\repo\x.mjs parses as the "d:" scheme (ERR_UNSUPPORTED_ESM_URL_SCHEME).
// Every oracle in this directory imports product modules through moduleUrl.
import { pathToFileURL } from 'node:url';

/** The file: URL for a filesystem path; `windows` forces the Windows form on any host (for tests). */
export function moduleUrl(filePath, { windows = process.platform === 'win32' } = {}) {
    return pathToFileURL(filePath, { windows }).href;
}
