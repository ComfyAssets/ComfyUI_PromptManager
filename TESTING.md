# Testing and merge confidence

Routine changes should merge after the required checks pass and the diff is
reviewed. Add a regression test for each bug; prove it fails before the fix.

## What runs

| Layer | Real components | Replaced boundaries |
| --- | --- | --- |
| Python tests | Domain logic, SQLite, API handlers, filesystem checks | ComfyUI host and ML runtimes where needed |
| JavaScript tests | Frontend modules and controller methods | DOM/network fixtures for focused edge cases |
| Viewer checks | Production Viewer.js and frontend methods in Chromium desktop/touch | Controlled images and page fixtures |
| Real ComfyUI | Pinned CPU host, actual extension, node definitions, widgets, routes and SQLite | Inference is not queued; browser startup settings and host preference writes are isolated |
| Page workflows | Production HTML, startup, click handlers, HTTP routes, SQLite, PNG files, thumbnails | ComfyUI host services and the tagging model |

Page workflows cover save/edit/reload, duplicate-edit rejection, gallery image
identity, Tags selection and viewer cleanup, delayed responses, auto-tag review
and persistence, and infinite scrolling without replacing existing cards.
Data safety workflows download and upload actual SQLite backups, verify restored
prompts/tags/image links, reject corrupt uploads without changing live data, and
exercise cancellation and network-failure recovery for restores and deletion.
Duplicate cleanup verifies only the selected file disappears; prompt deletion
verifies the other prompt and original image files survive.
They run in Chromium, Firefox, WebKit, and Chromium touch emulation. No GPU,
model downloads, running ComfyUI instance, or personal configuration is required.
The server binds to localhost and creates disposable directories and a database;
its reset endpoint exists only in the test harness.

## Run locally

Use a Python environment containing `requirements-dev.txt` and Node.js 24:

```sh
python -m unittest discover -s tests -q
npm ci --prefix tools/vendor --ignore-scripts
npx --prefix tools/vendor playwright install --with-deps chromium firefox webkit
npm run --prefix tools/vendor test
npm run --prefix tools/vendor test:coverage
npm run --prefix tools/vendor test:workflows -- --project firefox --project webkit
```

`PM_TEST_PYTHON` selects the Python executable; `PM_TEST_PORT` selects the local
port (default 8765). Set these environment variables when Python is outside PATH
or the port is occupied. Test servers are never reused. On unsupported Linux
distributions, browser system dependencies may need manual installation; CI uses
Ubuntu. For a quick browser run, use `test:workflows -- --project chromium`.

## Managed real ComfyUI

The shared engine comes from an exact commit of
[ComfyAssets Toolkit](https://github.com/ComfyAssets/ComfyAssets-Toolkit), recorded
in `tools/vendor/package.json` and its integrity-checked lockfile. The local
adapter sets PromptManager's database/configuration and monitoring policy;
source discovery, coverage mapping/gates and process lifecycle are shared code.
KikoTools remains the reference and is unchanged by this integration.

Use a **dedicated disposable** ComfyUI checkout at the revision in
`tools/vendor/comfyui-runtime.json`. With a separate Python environment, install
that checkout's requirements, this repo's `requirements-dev.txt`, and the exact
CPU torch/torchvision pins from the JSON (using PyTorch's CPU wheel index).
Install Chromium using the Playwright command above, then run:

```sh
PM_COMFY_ROOT=/path/to/disposable/ComfyUI \
PM_TEST_PYTHON=/path/to/cpu-env/bin/python \
npm run --prefix tools/vendor test:comfyui
```

CI performs this setup automatically. `PM_COMFY_URL` can select another
`http://127.0.0.1:PORT/` address (default port 8189). This is an owned managed
server: a running host is never reused. Do not point it at your live checkout.
The launcher verifies the upstream SHA and clean tracked files, snapshots only
tracked PromptManager files, and creates temporary user/input/output/database/
configuration/log directories. Personal untracked files are excluded. It runs
CPU/offline with only PromptManager enabled and removes owned state on shutdown.
The upstream checkout must also be disposable: ComfyUI may initialize local
runtime files there. Browser host-settings writes are intercepted; actual
PromptManager routes use the disposable host and database.

`real-comfyui-reports` retains startup logs, version attachments, screenshots and
traces for 14 days. Update upstream/frontend/CPU pins together after reviewing
and running both startup projects; never replace the SHA with a moving branch.
Update the toolkit via an exact reviewed commit archive and commit both npm
manifest and lockfile. No toolkit or npm installation is needed by end users.

## Coverage and failures

Python CI enforces 85% combined statement/branch coverage. Frontend coverage merges
Node V8 unit coverage with Chromium page-workflow coverage through c8. Every
`web/js/**/*.js` and `web/js/**/*.mjs` file appears, including untouched files at zero coverage. The
controller tests provide V8 filenames so their executed code is counted too.

`tools/vendor/frontend-coverage-baseline.json` holds a floor for every file and
metric. CI rejects regressions, missing measurements, and new files without a
reviewed baseline. These floors reflect current coverage, with two percentage
points of allowance for runtime/timing differences; they do not claim 80% coverage
of the large controllers. New files need at least 80% line coverage when added via
the baseline updater. Raise floors with `npm run --prefix tools/vendor
coverage:update`; it reruns the suites and cannot lower an existing floor.
Review baseline changes like code changes.

The `viewerjs` check runs on every PR, including backend-only changes. GitHub
artifacts named `frontend-test-reports` contain HTML coverage, a JSON summary,
and failure screenshots/traces. Open traces with Playwright's `show-trace` tool.

The real-host suite covers both Classic and Nodes 2.0 startup, switching both
ways, actual database prompt selection, execution-text serialization, workflow
reload, widget/style cleanup, node schemas and standalone-page opening. It does
not queue inference or certify every existing feature, arbitrary host versions,
GPU/model changes, cross-pack installation or visual design changes. Those still
need targeted validation.

## Required merge checks

`.github/branch-protection.json` records the required checks: all twelve Python/OS
combinations, Python coverage, formatting, frontend modules, `viewerjs`, and `real ComfyUI compatibility`.
The branch must be current with main, and administrators follow the same checks.
It does not require an additional human approval.

A repository administrator applies the configuration after the new workflow is
passing, then verifies it:

```sh
gh api --method PUT repos/ComfyAssets/ComfyUI_PromptManager/branches/main/protection \
  --input .github/branch-protection.json
gh api repos/ComfyAssets/ComfyUI_PromptManager/branches/main/protection/required_status_checks
```

Update the manifest whenever required job names change. Keep the frontend check
free of path filters so a required check is never silently skipped.
