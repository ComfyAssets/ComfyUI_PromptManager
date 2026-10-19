/** Count wording shared by Library and history views: the singular noun only for exactly one. */
export function noun(count, singular, plural = `${singular}s`) {
    return count === 1 ? singular : plural;
}

/** `1 linked image`, `2 linked images`. */
export function countOf(count, singular, plural) {
    return `${count} ${noun(count, singular, plural)}`;
}
