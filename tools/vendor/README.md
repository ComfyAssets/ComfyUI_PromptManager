# Viewer.js maintenance

ComfyUI serves the committed files in `web/lib/viewerjs/`. Node and npm are only
needed by maintainers and CI. Keep upstream files pristine; application behavior
belongs in `web/js/`, and theme overrides belong in `comfyui-theme.css`.

## Install and verify

Use Node 24 or newer. From the repository root:

```sh
npm ci --prefix tools/vendor --ignore-scripts
npm run --prefix tools/vendor check
npm run --prefix tools/vendor test
node --test tests/js/*.test.js
npx --prefix tools/vendor playwright install chromium
npm run --prefix tools/vendor test:browser
```

On Linux, use `playwright install --with-deps chromium` when browser system
libraries are missing. The browser tests use synthetic images and mock the
ComfyUI API boundary; they run production viewer methods and the committed
Viewer.js JS/CSS, without a running ComfyUI instance.

`check` is read-only and fails if any JS, CSS, license, or provenance file differs
from the installed package. The manifest, lockfile, installed package, and asset
version banners must agree. The lockfile records npm's archive integrity;
`provenance.json` records the upstream source, package integrity, and SHA-256 of
each copied file. A normal `npm ci --ignore-scripts` verifies archive integrity.

Full-page workflows, frontend coverage gates, and required merge checks are
documented in [TESTING.md](../../TESTING.md). The `viewerjs` CI check runs both
the fixture tests above and the real-page workflows on every PR.

## Update

```sh
npm run --prefix tools/vendor update
# Or select a specific stable version:
npm run --prefix tools/vendor update -- --version 1.15.2
```

The command installs an exact version with lifecycle scripts disabled and copies
`dist/viewer.min.js`, `dist/viewer.min.css`, and `LICENSE`. It writes deterministic
provenance with no timestamps. Repeat the checks above and commit the manifest,
lockfile, and all vendor files together. To regenerate files from the current
pin without changing versions, run `npm run --prefix tools/vendor sync`.

## Automated update PRs

`Update vendored Viewer.js` checks npm's stable release daily at 07:30 UTC and
supports **Actions → Update vendored Viewer.js → Run workflow**, with an optional
version. It tests the generated update before opening `deps/viewerjs-X.Y.Z`.
Existing open or closed PRs for that release are left alone. A failed test does
not publish a branch or PR. Changes require review; the workflow never merges
or approves PRs.

The organization and repository must enable **Actions → General → Workflow
permissions → Allow GitHub Actions to create and approve pull requests**.
Keep default workflow permissions read-only. Only the update job requests
`contents: write`, `pull-requests: write`, and `actions: write`.

The workflow uses `GITHUB_TOKEN`; no personal token is needed. Because bot-token
pushes do not normally trigger other workflows, it explicitly dispatches the
read-only `Viewer.js vendor and compatibility` workflow on the update branch.
Both workflow files must be merged into the default branch before scheduling or
manual dispatch becomes available.

## Compatibility checklist

Check gallery grid/list views, navigation across 100-image windows, metadata,
slideshow/fullscreen, keyboard navigation, Escape and focus cleanup. Check the
admin gallery/filmstrip and tag viewer. On touch devices, test long swipes,
short drags, and pinch/rotate gestures. Confirm the ComfyUI theme remains usable.

The gallery keeps its bounded viewer and custom slideshow. Upstream navbar
windowing is not a replacement for bounding the supplied image list: native
slideshow playback can still preload every supplied original. Review new
preloading defaults, gesture behavior, CSS changes, and any adapter use of
Viewer.js internal methods before adopting another release.

## Shared ComfyAssets test tooling

The exact toolkit commit archive in `package.json` / `package-lock.json` supplies
host orchestration and coverage helpers. `comfyui-host.mjs` is PromptManager's
configuration adapter; keep reusable lifecycle changes in the toolkit source.
The existing npm scripts remain the public maintainer entry points. See
[TESTING.md](../../TESTING.md) for the managed real-host matrix and update policy.

## Shared ComfyAssets browser controls

Prompt Manager (Classic) consumes the same pinned toolkit's browser controls.
The node identifier remains `PromptManager`; existing workflows, sockets, and
immediate result selection are preserved. PromptManager Text is unchanged.

Committed assets in `web/js/shared/toolkit/` work without npm or KikoTools installed.
Maintain common controls in [ComfyAssets Toolkit](https://github.com/ComfyAssets/ComfyAssets-Toolkit),
then update the exact archive pin and lockfile here. Regenerate using
`npm run --prefix tools/vendor sync:toolkit`; `npm run --prefix tools/vendor check`
verifies both Viewer.js and the toolkit without rewriting files. The toolkit
distribution includes its license, source history, archive integrity and per-file
hashes. Do not edit its copied files directly. Classic's controller and layout
remain in `web/js/classic-node.js` and `web/js/classic-node.css`.

Run the frontend coverage/browser suites and managed Classic/Nodes 2.0 matrix
after updates. Shared modules participate in the frontend coverage gate.
Pixaroma's reusable framework inspired this architecture; no Pixaroma code,
styles or assets are included.
