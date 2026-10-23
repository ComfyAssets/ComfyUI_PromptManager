import test from 'node:test';
import assert from 'node:assert/strict';

// Guarded: before the module exists every test fails on an assertion, not on the import.
const loaded = await import('../../web/js/workspace/images-tagging-review.mjs').catch(() => null);
const module = () => { assert.ok(loaded, 'images-tagging-review.mjs is not implemented'); return loaded; };
const SCOPE = 'e'.repeat(64);
const images = ['a.png', 'b.png', 'c.png'].map(path => ({ root_index: 0, root_scope: SCOPE, path, filename: path }));
const OPTIONS = { general_threshold: 0.4, character_threshold: 0.8, suggestion_limit: 3 };

/** A session over fake dialogs: `dialogs` holds every open call's arguments and whether it was closed. */
function session(extra = {}) {
    const dialogs = [];
    const toasts = [];
    let closes = 0;
    const saved = [];
    const handle = module().startReviewSession({
        api: {}, scope: {}, images, model_type: 'wd14-vit', options: OPTIONS, trigger: 'auto-tag',
        onSaved: event => saved.push(event.filename),
        open: args => {
            const dialog = { args, closed: 0 };
            dialogs.push(dialog);
            return { close: () => { dialog.closed += 1; args.onStop(); } };
        },
        close: () => { closes += 1; },
        notify: text => toasts.push(text),
        ...extra,
    });
    return { handle, dialogs, toasts, saved, closes: () => closes };
}
const approve = (dialog, tags = ['x']) => dialog.args.onSaved({ filename: dialog.args.image.filename, tags, target: {} });

test('the first image opens at once with the session model, options and its position', () => {
    const { dialogs } = session();
    assert.equal(dialogs.length, 1);
    const { args } = dialogs[0];
    assert.equal(args.image, images[0]);
    assert.deepEqual(args.position, { index: 1, total: 3 });
    assert.deepEqual(args.preset, { model: 'wd14-vit', options: OPTIONS });
    assert.equal(args.autoGenerate, true);
    assert.equal(args.actions, 'review');
    assert.equal(args.source, 'bulk');
    assert.equal(args.trigger, 'auto-tag');
    assert.throws(() => module().startReviewSession({ images: [] }), TypeError);
});

test('advances only on approve or skip and writes nothing on skip or stop', () => {
    const { dialogs, toasts, saved } = session();
    // Nothing from the dialog (a Generate in progress, a model-busy answer): the session waits on that image.
    assert.equal(dialogs.length, 1);
    dialogs[0].args.onSkip();
    assert.equal(dialogs.length, 2);
    assert.deepEqual(dialogs[1].args.position, { index: 2, total: 3 });
    // A second report from a dialog already left changes nothing.
    dialogs[0].args.onSkip();
    dialogs[0].args.onStop();
    assert.equal(dialogs.length, 2);
    approve(dialogs[1]);
    assert.equal(dialogs.length, 3);
    dialogs[2].args.onStop();
    assert.deepEqual(saved, ['b.png']);
    assert.deepEqual(toasts, ['1 of 3 images tagged.']);
    assert.equal(dialogs.length, 3);
});

test('approve saves exactly the dialog\'s manual-plus-chips set', () => {
    // The session never writes itself: each approve is the dialog's own Save (its event carries the saved set).
    const forwarded = [];
    const { dialogs } = session({ onSaved: event => forwarded.push(event.tags) });
    approve(dialogs[0], ['manual', 'chip']);
    assert.deepEqual(forwarded, [['manual', 'chip']]);
    assert.deepEqual(dialogs[1].args.preset, { model: 'wd14-vit', options: OPTIONS });
});

test('last image offers Approve and finish', () => {
    const { dialogs, toasts, closes } = session();
    approve(dialogs[0]);
    dialogs[1].args.onSkip();
    assert.deepEqual(dialogs[2].args.position, { index: 3, total: 3 });
    approve(dialogs[2]);
    // The last approve ends the session: the dialog closes and the count is toasted once.
    assert.equal(closes(), 1);
    assert.deepEqual(toasts, ['2 of 3 images tagged.']);
    dialogs[2].args.onStop();
    assert.deepEqual(toasts, ['2 of 3 images tagged.']);
});

test('model-busy on one image waits for retry or skip', () => {
    const { dialogs, toasts } = session();
    // The dialog shows the refusal with Retry; a Retry that succeeds then approves, all on the same image.
    assert.equal(dialogs.length, 1);
    approve(dialogs[0]);
    assert.equal(dialogs.length, 2);
    assert.deepEqual(toasts, []);
});

test('stop releases listeners and in-flight requests', () => {
    const { handle, dialogs, toasts, saved } = session();
    handle.stop();
    assert.equal(dialogs[0].closed, 1);
    assert.deepEqual(toasts, ['0 of 3 images tagged.']);
    // A save that settles after the stop still counts as written, but nothing opens or toasts again.
    approve(dialogs[0]);
    dialogs[0].args.onSkip();
    handle.stop();
    assert.equal(dialogs.length, 1);
    assert.deepEqual(saved, ['a.png']);
    assert.deepEqual(toasts, ['0 of 3 images tagged.']);
    assert.equal(module().reviewOutcome(1, 1), '1 of 1 images tagged.');
});
