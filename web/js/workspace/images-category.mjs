import { countOf } from './plural.mjs';

/**
 * The Images selection bar's "Set category" (#332, D140; the concept's bulk-category → simpleInput). Apply sets each
 * selected image's own category (`PUT /prompt_manager/images/category`); "Use prompt category" removes it after a
 * confirmation (`DELETE`), so the image shows its prompt's category again. It never writes a prompt: prompt
 * categories are set in the Prompt library editor, the Prompt library's selection bar (#437, library-selection.mjs)
 * and on the legacy admin page's "Set Category".
 */
export const CATEGORY_URL = '/prompt_manager/images/category';
export const IMAGE_CATEGORIES_URL = '/prompt_manager/images/categories';
export const CATEGORY_DIALOG = Object.freeze({title:'Categorize selected images', label:'Category', action:'Apply'});
export const USE_PROMPT_CATEGORY = 'Use prompt category';
export const REMOVE_DIALOG = Object.freeze({title:'Use prompt category', action:'Remove'});
export const CATEGORY_FAILED = 'Category could not be set. Try again.';
export const NONE_FOUND = 'None of the selected images could be found. Refresh the images and try again.';
export const NONE_HAD_OWN = 'None of the selected images had its own category.';
export const SOURCES_CHANGED = 'The sources changed. Refresh the images and try again.';
// The server's limit (`validate_category`): the field stops there instead of failing on Apply.
export const CATEGORY_MAX_LENGTH = 100;
// One request names at most 500 images (DC2), as "Add tags" does.
export const MAX_CATEGORY_IMAGES = 500;

/**
 * The success toast of a set: unchanged items count as set; items that could not be found are named after. `noun` is
 * what was set (the Prompt library's selection bar passes "prompt", #437).
 */
export function setOutcome(result, category, noun = 'image') {
    const updated = Number(result?.updated) || 0, unchanged = Number(result?.unchanged) || 0;
    const missing = Number(result?.not_found) || 0;
    const done = `Category “${category}” set on ${countOf(updated + unchanged, noun)}.`;
    return missing ? `${done} ${missing.toLocaleString()} could not be found.` : done;
}

/** The toast of a removal. */
export function clearOutcome(result) {
    const cleared = Number(result?.cleared) || 0;
    return cleared ? `Removed the image category from ${countOf(cleared, 'image')}.` : NONE_HAD_OWN;
}

/** The confirmation sentence before "Use prompt category" removes the own category of `count` images. */
export function removeQuestion(count) {
    const which = count === 1 ? 'the 1 selected image' : `the ${count.toLocaleString()} selected images`;
    return `Remove the image category from ${which}? Each one then shows its prompt's category, or none if it has no ` +
        'linked prompt. This cannot be undone.';
}

/** The toast for a failed write: the coded answers the routes give, else the fixed failure. */
export function failureMessage(error) {
    if (error?.code === 'files-not-found') return NONE_FOUND;
    if (error?.code === 'source-changed') return SOURCES_CHANGED;
    if (error?.code === 'too-many-images' && typeof error.message === 'string' && error.message) return error.message;
    return CATEGORY_FAILED;
}

/** The toolbar's category list: the prompt and recipe categories plus the listed images' own ones, each once. */
export function mergeCategories(promptCategories, imageCategories) {
    const merged = [];
    const seen = new Set();
    for (const value of [...(promptCategories ?? []), ...(imageCategories ?? [])]) {
        if (typeof value !== 'string' || !value.trim() || seen.has(value)) continue;
        seen.add(value);
        merged.push(value);
    }
    return merged;
}
