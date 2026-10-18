# Testing and merge confidence

Routine changes should merge after the required checks pass and the diff is
reviewed. Add a regression test for each bug; prove it fails before the fix.

## What runs

| Layer | Real components | Replaced boundaries |
| --- | --- | --- |
| Python tests | Domain logic, SQLite, API handlers, filesystem checks | ComfyUI host and ML runtimes where needed |
| JavaScript tests | Frontend modules and controller methods | DOM/network fixtures for focused edge cases |
| Viewer checks | Production Viewer.js and frontend methods in Chromium desktop/touch | Controlled images and page fixtures |
| Real ComfyUI | Pinned CPU host, actual extension, node definitions, widgets, routes, SQLite and real execution lifecycle | Stock UI cases isolate host writes; the execution suite queues deterministic test-only nodes without models |
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
not certify every existing feature, arbitrary host versions,
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

## Unified workspace

The opt-in `/prompt_manager/workspace` page opens the native Images workspace.
Overview retains real `/prompt_manager/stats` counts. Both use the shipped toolkit. Existing admin, gallery and metadata URLs remain
available while replacement workspaces are developed. Browser smoke tests in
`tests/browser/workspace.spec.mjs` cover navigation/history, keyboard skip links,
mobile layout, theme persistence/fallback, loading/retry and stale responses.
The Images cases in `tests/browser/workspace-images.spec.mjs` additionally cover
server-side filters, cursor append/retry, Grid/List/Masonry, bounded DOM, keyboard
navigation, restored browsing context, Viewer.js cleanup and actual video playback.
`tests/test_api_workspace_images.py` checks signed cursors, source identities,
legacy compatibility and header-only dimensions. Frontend stream/layout units
cover cancellation, malformed responses and bounded visibility lookup. These run
automatically in the existing four-browser-project workflow and frontend coverage job. `tests/comfyui/workspace.spec.mjs` verifies the page against the
managed host with both renderer startup configurations.

Run the focused page checks with:

```sh
npm run --prefix tools/vendor test:workflows -- workspace.spec.mjs workspace-images.spec.mjs
node --test tests/js/workspace-*.test.js
```

Use the disposable fixture and managed-host prerequisites above. A maintainer
live check needs a ComfyUI restart after adding the Python route, then opening
`/prompt_manager/workspace`; the mockup preview server is a separate design tool.

## Prompt sending and cross-pack compatibility

`tests/js/prompt-bridge-contracts.test.js` runs the Toolkit's shared protocol
contracts against the actual checked-in browser asset. Adapter and lifecycle
units cover role isolation, connected inputs, stale previews, callback recovery,
serialization normalization and cached-page restoration. Browser tab workflows
exercise real BroadcastChannel delivery; `tests/comfyui/prompt-bridge.spec.mjs`
checks the real widgets, execution serialization and renderer transitions.

The existing required real-ComfyUI job also runs `test:compat`: PromptManager
alone, pinned KikoTools alone and both together, each starting in Classic and
Nodes 2.0. It copies tracked source into disposable hosts, preserves independent
configuration and checks edits/reload with both packs present. KikoTools remains
on its current embedded foundation; this does not adopt the Toolkit in that pack.

For local runs, use a clean KikoTools checkout at the revision in
`tools/vendor/cross-pack-runtime.json`, plus the managed-host prerequisites above:

```sh
PM_COMFY_ROOT=/path/to/pinned/comfyui PM_KIKO_ROOT=/path/to/pinned/kikotools \
  npm run --prefix tools/vendor test:compat
```

The compatibility runner reserves loopback ports 8193–8195; `PM_COMPAT_PORT`
selects another consecutive unprivileged range. Never run automated tests against
the maintainer's running ComfyUI. This JavaScript host extension needs a full
browser refresh after deployment; its first slice adds no gallery send controls.

### Workspace prompt inspection and sending

`tests/test_api_prompt_inspector.py` covers rooted physical-file identity,
execution/graph/parameter evidence, saved associations, ambiguity, empty negatives,
read-only behavior, bounded PNG/video metadata and corrupt inputs. Run it with
`python -m unittest discover -s tests -p test_api_prompt_inspector.py` in a disposable
checkout. `tests/js/prompt-inspector.test.js` and `prompt-sender.test.js` cover
request races, explicit destination selection, preview tickets and uncertain
acknowledgements. Browser workflows exercise the same controls in Images and
Viewer, including nested modal focus, mobile layouts and per-role sending.
`tests/comfyui/prompt-sender.spec.mjs` sends through the actual workspace UI into
real Classic and Nodes 2.0 widgets and verifies workflow serialization without
loading a workflow or queueing generation.

## Durable execution ledger

`npm run --prefix tools/vendor test:executions` runs 42 real execution cases
(21 each in Classic and Nodes 2.0, retaining all 26 prior execution cases),
alongside the existing 78 stock host cases and six pack compatibility cases. Run
these suites serially because they share report cleanup and owned host resources. Use the same pinned disposable `PM_COMFY_ROOT`,
`PM_TEST_PYTHON` and Playwright browser installation as `test:comfyui`:

```sh
PM_COMFY_ROOT=/path/to/disposable/ComfyUI \
PM_TEST_PYTHON=/path/to/cpu-env/bin/python \
PLAYWRIGHT_BROWSERS_PATH=/path/to/playwright-browsers \
npm run --prefix tools/vendor test:executions
```

This suite reserves `http://127.0.0.1:8196/` and never attaches to an existing
server. Each renderer worker launches a CPU/offline host with independent
configuration, database, user and output directories. The shared launcher installs
tracked snapshots of PromptManager and a tiny temporary Git-tracked fixture pack.
Only that owned test pack provides cached text, deliberate failure, an
interruptible queue gate, deterministic CLIP/model/latent and CPU tensor inputs,
and control routes.
It replaces external sampler inference and trigger injection only inside the
disposable host; real registered PromptManager classes, queue, executor and cache
remain active. Its cache control resets the real executor on its worker to exercise
Classic, LRU, RAM-pressure and disabled caching; production has no test nodes or
endpoints. Cleanup always releases the gate, stops the host and removes both
owned directories. Logs and Playwright failures appear under
`tools/vendor/test-results/executions/` and the existing CI artifact upload.

The request-only case creates no browser or WebSocket client. Other cases verify
real `execution_cached` node lists after a failed job, both renderer switches and
workflow reload, repeated public UUIDs, interruption, graph rejection, and queued
removal before dequeue. Actual host history is checked alongside finite polling
of SQLite after the background writer. The read helper uses SQLite read-only
mode, exact UUID job/session filters and a 256-row limit; absent ledger schema is
explicit, while missing/corrupt databases and other read errors fail the suite.
Sensitive diagnostic and extra-data sentinels must never appear in lifecycle
rows. Exact prompt fields are deliberately persisted only in context snapshots.
The same read-only helper calls the internal `get_execution_contexts` and
`get_execution_outputs` readers for requested run IDs and inspects their snapshot
identities and current usage counts. Its `outputs` map is keyed by immutable run
ID, alongside the existing `rows`, `captures`, `usage` and `snapshotIds` results.

`execution-outputs.spec.mjs` runs actual stock SaveImage and PreviewImage nodes
from tiny CPU tensors. It compares each attempt's persisted descriptors with that
attempt's final host history, including A,A,B batches, multiple output nodes,
cached locators, reused public UUIDs, PNG metadata disabled, graphs without PM
nodes and text-only outputs. The pinned SaveImage returns an IMAGE socket; linking
it to the owned failure/gate node orders the partial-run tests explicitly. A held
downstream gate proves completed image work does not finalize the run early.
Other cases verify real output capture across startup in each renderer, switching
both ways and workflow reload. PM usage counts retain their existing per-job
behavior. References identify reported locators only: file identity is unverified,
new generation and context membership are unknown, and temp references are
transient. The stock suite excludes both execution files; discovery retains its
78 cases and the six pack cases unchanged.

For a meaningful historical failure, create a separate disposable detached
checkout at `fdba6fd0126a8a40cbd6f3e50ed4c5de8991efd4` and pass its path to
`launchExecutionHost({ repository })`. That base also predates the #211 config
import fix: after confirming an actual headless job succeeded, use
`readExecutionRuns` on its explicitly constructed owned path
`host.stateDirectory/custom_nodes/promptmanager/prompts.db`. It reports
`schemaAvailable: false` and empty rows, failing the expected succeeded-row
assertion. This historical inspection stays outside the ordinary suite, which
requires the configured database path. The launcher also accepts
`PM_EXECUTION_REPOSITORY`; omit it for the implementation worktree. Stage new
files first because snapshots use `git ls-files`. Never reset the implementation
checkout or point this harness at a live ComfyUI checkout.

The package-qualified config import also corrects #211: the configured database
path is honored when PromptManager runs as a ComfyUI custom node. Follow the
[database path compatibility and safe backup sequence](README.md#database-path-compatibility-211)
before restarting an existing deployment.

The ledger records only new dequeued jobs and observed outcomes. Existing
counters, prompts, pairs and image links are preserved, with no reconstructed old
runs. Schema v2 migrates automatically when the configured database opens, adding
immutable prompt-context tables without backfilling old runs. The internal reader
returns `not_captured` for preexisting runs; a new capture can instead be complete,
partial, ambiguous or unavailable. Keep the existing database backup/restore
procedure: restored history changes storage epoch, and old library navigation
hints must never be rebound to unrelated current rows.

Context cases verify A,A,B as three jobs/two combinations/one positive snapshot
when every positive field matches; all five registered PM variants; exact raw
whitespace and Unicode; known empty negatives; Text negative roles and crossed
pairs; fully cached repeats after trigger configuration changes; successful source
text before downstream failure/interruption; unknown cached dynamic raw; unsupported
transforms and duplicate list invocation ambiguity. Fresh values use
`runtime_return`, cached effective STRING outputs use `host_cache_output`, and
submitted literals use graph inference. SQL NULL means unknown; empty text is
known. Text is never trimmed or normalized by capture. Cache availability and
inferred sampler roles prove neither sampler execution nor consumption, and list
indices do not identify generated images. External trigger configuration changes
must not rewrite the effective text of an existing cached result. The cache matrix
also checks that positive/pair usage increments once per dequeued job while a
negative-only component retains its existing zero standalone-use count.

Fault injection for restore fencing, inherited async capability revocation, queued
finalization, epoch and generation revocation, provider-local cache entries, eviction/reset, malformed or
oversized payloads and missing host interfaces lives in the focused Python
`test_execution*.py` suites. These use disposable storage and mocked host interfaces;
they are distinct from the pinned real executor/cache checks above. Run-owned
output descriptors do not create gallery image associations or a chronological
timeline. Deploying the Python changes
requires a ComfyUI restart. After restart, queue three new jobs A,A,B with identical
positive fields and two negative recipes; existing counters should advance as usual,
and internal context inspection should find three attempts and two combinations.
For a live output check after that manual restart:

- Queue SaveImage and PreviewImage with a batch of two, then submit an unchanged
  cached repeat and a changed tensor/input under new jobs.
- Inspect each run's internal output capture against its final ComfyUI history;
  cached references may repeat, while the attempts remain distinct.
- Switch Classic to Nodes 2.0 and back, reload the workflow and queue again.
- Verify prompt/pair counters and gallery links retain their existing behavior;
  output references alone do not prove file bytes or prompt membership.

Automated checks use only managed disposable hosts. They never restart or upgrade
the personal ComfyUI server.
