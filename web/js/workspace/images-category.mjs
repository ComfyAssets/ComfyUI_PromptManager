import { countOf, noun } from './plural.mjs';

/**
 * The Images selection bar's "Set category" (#249 D8). Images have no category of their own: the listing's category
 * filter matches the linked positive prompt's, so the action sets the category on those prompts.
 */
export const CATEGORY_DIALOG = Object.freeze({title:'Categorize selected images', label:'Category', action:'Apply'});
export const CATEGORY_NONE_LINKED = "The selected images aren't in your Library yet, so there is no prompt category to set.";
export const CATEGORY_FAILED = 'Category could not be set. Try again.';
// The server's limit (`validate_category`): the field stops there instead of failing on Apply.
export const CATEGORY_MAX_LENGTH = 100;

function linkedPositive(image) {
    const link = image?.linked;
    if (link?.status !== 'linked') return null;
    return Number.isSafeInteger(link.positive_id) && link.positive_id > 0 ? link.positive_id : null;
}

/** The distinct linked positives of `images`, first-seen order; `skipped` counts the images without one. */
export function categoryTargets(images) {
    const ids = new Set();
    let skipped = 0;
    for (const image of images) {
        const id = linkedPositive(image);
        if (id === null) skipped += 1;
        else ids.add(id);
    }
    return {promptIds:[...ids], skipped};
}

/** The success toast: the prompts changed, then any selected images that had no linked prompt. */
export function categoryOutcome(prompts, skipped) {
    const done = `Category set on ${countOf(prompts, 'prompt')}.`;
    if (!skipped) return done;
    return `${done} ${countOf(skipped, 'selected image')} had no linked prompt and ${noun(skipped, 'was', 'were')} skipped.`;
}

/** The prompts the server reports changed; without a count, the number of prompts posted. */
export function updatedCount(result, posted) {
    const count = result?.updated_count;
    return Number.isSafeInteger(count) && count >= 0 ? count : posted;
}
