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
distributions, browser system dependencies may need manual installation. For a
quick browser run, use `test:workflows -- --project chromium`.

CI runs the browser jobs inside the pinned image
`mcr.microsoft.com/playwright:v<@playwright/test version>-noble`
(`tests/vendor/browser-image.test.mjs` keeps the tag in step with the lockfile).
Concept comparisons measure geometry, and geometry follows the fonts a browser
falls back to: the concept names Inter, which neither the image nor a stock
runner ships. On a plain `ubuntu-latest` runner the fallback is DejaVu Sans,
whose wider metrics make the concept's own Tags table overflow a 390px viewport,
so the 390px comparisons are only meaningful in the pinned image. To reproduce CI,
run the suites in that image.

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

The `viewerjs` check runs on every PR, including backend-only changes. It is an
aggregate job that passes only when the `frontend coverage (chromium and touch)`
job and both `page workflows (firefox)` and `page workflows (webkit)` jobs
succeed; the browsers run on separate runners in parallel. GitHub artifacts
named `frontend-test-reports` contain HTML coverage, a JSON summary, and the
Chromium/touch failure screenshots/traces; `browser-traces-firefox` and
`browser-traces-webkit` hold the other browsers' traces. Open traces with
Playwright's `show-trace` tool.

The real-host suite covers both Classic and Nodes 2.0 startup, switching both
ways, actual database prompt selection, execution-text serialization, workflow
reload, widget/style cleanup, node schemas and standalone-page opening. It does
not certify every existing feature, arbitrary host versions,
GPU/model changes, cross-pack installation or visual design changes. Those still
need targeted validation.

## Windows portability preflight

Before changing paths, SQLite, streams, restore or cleanup:

- Follow the boundary's existing native canonical/identity contract;
  `Path.resolve()` alone does not fold Windows case. Keep lexical aliases
  distinct from resolved targets. Cover mixed case, drive and UNC spellings,
  plus foreign-namespace denial with an allowed native control.
  See Python's [normcase documentation](https://docs.python.org/3.13/library/os.path.html#os.path.normcase).
- Close every owned database/model connection, worker and stream handle before
  unlink or `TemporaryDirectory.cleanup()`. Await worker/stream retirement.
  A SQLite [`with conn` manages transactions, not connection closure](https://docs.python.org/3.13/library/sqlite3.html#how-to-use-the-connection-context-manager),
  and [`addCleanup` runs after teardown](https://docs.python.org/3.13/library/unittest.html#unittest.TestCase.addCleanup);
  use explicit `finally` cleanup when teardown removes files first.
- Windows can reject unlink of an open file; Unix can remove its directory entry
  while the handle remains usable. Preserve the shared behavior assertions and
  exercise the actual native branch, then prove handle release and successful
  cleanup. Do not hide failures with blanket skips or retries.
  See Python's [remove/unlink semantics](https://docs.python.org/3.13/library/os.html#os.remove).
- Concurrent SQLite tests must not depend on how fast the host commits.
  Managed connections use WAL with `synchronous=NORMAL` (see
  `configure_connection`) so commits do not hold the write lock across a
  flush. Make real overlap deterministic (for example hold the first writer
  open with `FirstWriterHold`) and give peers that must all succeed the
  ordinary busy timeout; cross-thread handoffs wait on events with generous
  ceilings, never a short wall clock (#242).
- Run native Windows CI early for these changes and include a Windows impact and
  evidence note in the PR. All twelve OS/Python combinations and every applicable
  exact-head check must pass before merge. Portable `ntpath`/`PureWindowsPath`
  controls do not establish native filesystem, locking or cleanup success.

## Required merge checks

`.github/branch-protection.json` records the required checks: all twelve Python/OS
combinations, Python coverage, formatting, frontend modules, `viewerjs`, `real ComfyUI compatibility`,
and `bandit (high severity)`.
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

The shared inline and collection inspector reads the current rooted file's supported
embedded records, exact generation facts and recorded or inferred prompt evidence.
`tests/browser/full-inspector.spec.mjs` covers original/metadata/workflow downloads,
response limits, source changes, nested sending, matched dark/light and mobile
geometry, and the loaded-collection viewer. The viewer never fetches another page
or preloads all originals. Its explicit slideshow waits 3.5 seconds after image
readiness, pauses at native video without advancing on `ended`, and resumes on a
manually selected image only while prior intent remains. Pause/Stop, inspector
reading/copy/download/send, hidden document, media failure, close, query/route
disposal and pagehide cancel that intent; returning visibility or closing the sender
does not resume it. Stop leaves native video playback alone. Video uses only the
existing normalized `gallerySettings` autoplay/mute/loop values, read without writes;
denied/malformed storage falls back to autoplay off, mute on and loop on. Rejected
autoplay keeps native controls usable.

The inspector does not establish historical file identity, infer run membership,
edit image tags, export all EXIF, or load/execute a workflow. Saved vocabulary is
labelled by its recipe or positive-prompt scope. Limited raw evidence is omitted
atomically, with JSON exports unavailable and the rooted original still downloadable.
The inspection API uses one bounded read with per-record budgets: role records
(`prompt`, `parameters`, the pair execution record) share 2 MiB and exceeding it
fails the read; the `workflow` record has its own 4 MiB and past it only the workflow
becomes record format `limited` (no raw text, graph `limited`, roles and facts
unaffected); other text chunks are skipped unread; byte-identical duplicates are
accepted within a bounded read budget, while differing duplicates (and any duplicate
role record over that budget) still fail. Other read bounds are unchanged: 8 MiB
non-PNG header, 10,000 PNG chunks and a three-second ffprobe. Resolution under
`INSPECTION_PROFILE` bounds work, not graph size: 2,000 visits per candidate chain,
20,000 in total, 64 link/JSON-container levels, 100,000 resolved characters, and a
two 20,000-node ceilings: one over the top-level entries of the API prompt, and one
over the workflow graph's UI nodes plus subgraph-definition nodes (past it the
workflow graph and its facts become limited). The serialized
response stays under 8 MiB by omitting, in order, the workflow graph, then the prompt
graph, then all raw records atomically. Retry does not raise these limits. Ingestion
(scan, live monitor, admin import) and queue-time run counting keep the default
profile, including its 500-node gate. Only `INSPECTION_PROFILE` reads the node
semantics table `web/js/inspection-semantics.json` (at most 1 MiB and eight nesting
levels, loaded on first use). With it, `SamplerCustomAdvanced` follows its `guider`
link (through Reroutes) to `CFGGuider` (positive and negative) or `BasicGuider`
(positive only; the negative is `absent`, "Guider has no negative input"). Any
other guider, such as `DualCFGGuider`, is `unsupported-node`. Conditioning is
traced through the table's pass-throughs (FluxGuidance, ReferenceLatent, the
ConditioningSet* nodes, ControlNet apply nodes, InpaintModelConditioning and
WanImageToVideo by output slot). `ConditioningZeroOut` is an explicit empty text
with source `zeroed-conditioning`. `easy ifElse` with a literal boolean follows that
branch only; with a linked boolean both branches are traced, equal texts resolve and
different texts make the sampler `ambiguous` with one candidate per choice (at most
32, else `work-limit`). ConditioningCombine/Concat/Average resolve only when every
part has the same text, otherwise `unresolved` with `composite` naming the node.
Results that depend on a forced choice are never memoized. A `FaceDetailer`
(`detailer-stage`) is a sampler stage: the samplers reached through its `image`
ancestry are compared with it. Equal texts resolve with the detailer as the sampler;
different texts are `ambiguous` with `multistage-differs` and one candidate per
stage; an unreadable stage makes the chain `unresolved`. Unanchored, a sampler
whose output only feeds detailers (or previews) is not a candidate of its own.
Text encoders: `single` encoders (the Qwen image encoders, `TextEncodeQwenImage21`
slot 0 `prompt` and slot 1 `negative_prompt`, `KikoTextEncode`) read one string
input unchanged. `parts-equal` encoders (CLIPTextEncodeSDXL, Flux, SD3) resolve when
every part is identical or exactly one part is non-empty (exact `""`, whitespace is
text); otherwise `composite`. String sources reproduce the pinned KJNodes code from
literal settings only: `StringConstant` is its `string`; `StringConstantMultiline`
with a literal true `strip_newlines` is `replace("\n", "").strip()`;
`JoinStringMulti` keeps `string_1` (even empty), appends `string_2`..`string_N`
(`N` = `inputcount`) after the literal delimiter unless missing or exactly `""`, and
ignores later inputs. A linked setting is `unsupported-node`; a list output
(`return_list` true or linked) and `PromptSearchList` are `runtime-value` ("Text is
decided at run time"), never one list item. Unverified text nodes (LoRAfromText,
RegexReplace, XYZPrompt, ImpactWildcardProcessor) stay `unsupported-node`. Every
intermediate text is length-checked and charged to the scanned-character budget. If
the table cannot be loaded, graph inspection is `unresolved` ("Inspection node
semantics are unavailable") and ingestion is unaffected, because it never imports the table.

`tools/inspection_corpus.py --root DIR --out JSONL [--workers N] [--baseline JSONL]
[--summary TXT] [--code TREE]` measures a media folder read-only against the #240
invariants H1 to H8. It runs the inspection route pipeline and the workspace JS facts
of `--code` (default: its own tree), opens media only for reading, follows no
symlinks, imports no database, and refuses `--out`/`--summary` inside the root or a
code tree. Its rows hold statuses, sizes, timings and SHA-256 digests of resolved
role texts, never file names or prompt text unless `--with-paths` is given; digests
of short prompts can still be guessed, so the JSONL stays local and only the
aggregate summary goes into a PR body. Run it on the base commit first and pass that
JSONL as `--baseline` for H1 and H8 (2x the baseline p99, never below 50 ms). It
exits 1 when an invariant fails, including the `harness` line: a tool exception or
facts that were not computed (no Node.js) fail the run. Unreadable or unsupported
files are inspector outcomes, not harness errors. Never point it at a live install's folders from an
automated job. Installing the Python inspection/source-scope changes requires a
maintainer ComfyUI restart. Automated tests use disposable hosts and never restart
the running personal ComfyUI.

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

`npm run --prefix tools/vendor test:executions` runs 46 real execution cases
(23 each in Classic and Nodes 2.0, retaining all 42 prior execution cases),
alongside the existing 80 stock host cases (the original 78 plus the native
editor host case) and six pack compatibility cases. Run
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
80 cases (the original 78 plus the native editor host case) and the six pack
cases unchanged.

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

## Chronological Library history (#209)

`#/library/{positive_id}` retains saved negative selection, the full selected
negative directly beneath its positive, acknowledged role/pair sending, and one
pair-filtered shared gallery. Recorded executions are a separate projection.
Selecting an execution, anchor, Raw/Effective mode, or reported occurrence does
not select a saved variant or refetch its gallery. A,A,B means three attempts,
two frozen contexts, and one exact raw-positive group. Different composition or
evidence can produce different frozen contexts despite equal-looking previews.

The versioned public reads are:

| Route | Meaning |
| --- | --- |
| `GET /prompt_manager/library/{positive_id}/history` | Exact frozen raw-positive group; compact chronological windows |
| `GET /prompt_manager/history/runs/{run_id}` | Frozen fields, per-field provenance, capture and outcome for a known UUID |
| `GET /prompt_manager/history/runs/{run_id}/outputs` | Ordered host-reported occurrences, including repeated locations |
| `GET /prompt_manager/history/runs/{run_id}/output/current/{media,thumbnail,inspection}` | Authorized current file at that reported location, using the returned epoch/scope URL |

Run and output reads accept the returned `epoch`; current-file routes require
the returned epoch, current scope and occurrence coordinates. Run integers are
decimal strings, preserving SQLite's full signed 64-bit values. Windows and
receipt pages default to 40 with a 1–100 limit. Cursors are opaque, authenticated,
scope-bound and capped at 4096 bytes. Run JSON has an 8 MiB UTF-8 limit and receipt
pages a 1 MiB limit; exceeding a limit fails explicitly instead of truncating
exact evidence. The unchanged capture limits remain 16 anchors/256 occurrences.

A cursor freezes its upper ordering boundary, not an application-wide snapshot:
each page has one SQLite read transaction and a count as of that read. Later
pages can observe completed capture, deletion or other current changes. Newer
runs require Refresh history. Unknown positive raw text cannot enter an exact
Library group but remains readable through its known run UUID. Unknown (`null`),
known empty (`""`) and whitespace are distinct. Complete-zero reported outputs
means the host reported none; missing capture means unknown, and partial or
conflicting capture remains incomplete. Receipts prove reported locations, not
historical bytes, newly generated files or context-output ownership. Cached
attempts can repeat exactly the same receipts.

Each nonempty history page also returns an authenticated `resume_cursor`, including
an exhausted page with no continuation. Re-entry uses `resume` instead of `cursor`
to read from that prior upper boundary, then restores at most the prior number of
40-run pages. It retains horizontal/vertical scroll and the visible anchor as
in-memory visit intent. The API validates epoch and exact saved-text scope again;
DTOs and current candidates are never saved as visit intent. Explicit Refresh
loads the newest window/count and revalidates the selected run's exact raw-positive
membership, context and receipt. A valid selection outside that window has an
explicit label and **View selected run** control.

Every receipt can be selected, including missing/error candidates. **Retry current
availability** rereads its existing bounded output page; a reduced response limit
may require several reads within that one already loaded page. Earlier rows,
context/mode, exact selection and saved gallery remain. Recovery actions use the
shared client's strict `smaller-limit`, `reload-outputs`, `retry-read` and
`refresh-history` vocabulary. Active inspection epoch/identity failures immediately
reach the owning history recovery, clearing stale recorded actions before reload.

Restore acceptance depends on merged #223: incompatible online storage
replacement must safely refuse without harming another owner's WAL/connection.
History fencing does not repair unsafe replacement. For accepted replacement,
the read transaction ends before a fresh-connection epoch check; the final
epoch/generation validation is the publication linearization point. Browser
generations also ignore late success/error callbacks after route/scope changes.
Old cursors, epoch and current-scope URLs fail with bounded recovery instructions.
Restored numeric IDs never repair an original navigation hint. Saved Library
details are revalidated separately, and a changed owner requires explicit reload.

Current media/thumbnail responses use an already-opened, validated canonical
file handle, bounded streaming and `Cache-Control: no-store`, including HEAD and
range behavior. Inspection is a current metadata observation with pre/post file
facts, canonical reauthorization and a fresh scope/epoch check; it is not pinned
historical bytes or an OS filesystem snapshot. A deterministic change during the
metadata read is rejected. A later request can legitimately observe replacement
bytes at the same path without changing the immutable receipt.

The focused acceptance command retains all four projects:

```sh
PM_TEST_PORT=8766 npm run --prefix tools/vendor test:workflows -- \
  workspace-history.spec.mjs workspace-visual.spec.mjs \
  --project chromium --project touch --project firefox --project webkit
npm run --prefix tools/vendor test:executions
npm run --prefix tools/vendor test:comfyui
npm run --prefix tools/vendor test:compat
```

Use the pinned disposable prerequisites above; managed suites run serially.
The real execution helpers compare SQLite captures to these public routes for
stock SaveImage/PreviewImage batches, multiple nodes, metadata disabled, cached
headless repeats, partial failure and interruption. Additional cases verify the
actual PM A,A,B group, unknown-positive exclusion, and exact frozen Raw/Effective
pair sends through the acknowledged host bridge after Classic/Nodes 2.0 startup,
both switches and workflow reload, without queuing or loading a workflow on send.
All original 42 execution, 78 stock-host and six compatibility cases remain.

The browser fixture uses actual APIs/SQLite/files, fixed owned backup snapshots,
one-shot bounded publication barriers and fixed filesystem/configuration commands.
Reset and cleanup release barriers, restore seams and revoke commands. It never
attaches to a personal host. The canonical HTML and its six required assets are
served from a fixed test-only allowlist; the original concept index/app/style
bytes are hash-pinned and unmodified. At 1600×1100 and 390×844 in both themes,
with fixed locale/UTC/reduced motion and ready fonts, tests capture unmasked
concept/production PNG pairs and geometry, and pin actual branch padding,
surfaces, 2px role lines, 57px rhythm, staggering, colors, selected focus and native
dialog behavior. Controls and actions are checked independently of screenshots.
Keyboard focus/return, coarse targets, 200% layout, route return and persisted
page lifecycle recovery retain their separate behavioral tests.

Initial production acceptance requires maintainer inspection of paired PNGs and
geometry; never update snapshots automatically or mask content to bless a gap.
Data-dependent text/images/counts and the clearly labeled saved/frozen/current
distinctions are documented in the comparison helper. Broader Images card
activation/Info/tag/favorite/select parity and shared Viewer stage/toolbar polish
and shared role-block spacing/padding/glyph polish remain outside #209. The
responsive production Viewer keeps its toolbar in the media panel and prompt details readable rather than reproducing the concept's
mobile toolbar collision. These are explicit remaining visual differences, not
a whole-application or full-gallery/Viewer pixel-parity claim.

Preserve raw logs, failed traces, paired PNGs, geometry, runtime versions and
source hashes before later managed commands clear their output directories.
Frontend baseline updates are additive through `coverage:update` only after
every previous metric floor and each new file's 80% line requirement pass; rerun
the coverage gate using the committed baseline. Python retains its 85% combined
statement/branch gate. Deploying the history Python changes requires a maintainer
ComfyUI restart; automated acceptance never restarts the personal server.

## Native recipe editor (#234)

The Library page edits recipes in one native modal (`dialog.library-editor`)
instead of sending users to the legacy dashboard. It is frontend only: it consumes
the Library/pair routes, epoch tokens, null-clears and conflict codes defined by
the backend change (#233) and does not add or change a route, so this section does
not repeat them. The editor reads fresh, `no-store` state before it opens.

The four dialog modes match the approved concept's shell: **New prompt**,
**Edit recipe metadata**, **Create text revision** and **Add a negative variant**.
Pair **Archive…** and **Restore** are one reviewed action with **Show archived**
in the saved-variants list; archiving a pair never touches its positive prompt, its
siblings, history or images. **Export** downloads the full v1 recipe export. A
failed Save keeps focus inside the dialog and keeps every entry.

Recovery is exercised against real failure responses, not mocks of the controller:

- a **conflict** response keeps the draft and explains the newer saved state; a
  **storage-changed** response writes nothing, and only New prompt re-reads in
  place: every other dialog offers only **Reload Library**, because an old numeric
  owner is never resolved after a restore;
- an **unknown outcome** (no response) keeps the draft and offers an explicit
  **Retry** that resends the identical request once, which the server
  deduplicates; an Edit metadata retry instead reloads the latest saved values for
  review and resends nothing;
- a **confirmed receipt** whose follow-up refresh fails offers a read-only
  refresh retry and never re-sends the write.

The concept comparison (`library-editor-canonical-comparison.mjs`) inventories the
concept dialog and the production dialog in the same browser: geometry, CSS tokens,
colors and a control inventory (labels, field types, options, actions and their
danger/primary classes). Only the documented differences (the prototype's Save
label and demo note) are mapped. The mutation check proves the comparison fails when
a token, label or control is changed, so it cannot pass vacuously.

PNG baselines are intentionally absent: `toHaveScreenshot` is not used for the
editor. Paired concept/production captures and the artifact-only states
(`library-editor-visual.spec.mjs`) are attached as test artifacts for maintainer
acceptance; a baseline is added only after the maintainer accepts them.

Focused commands:

```sh
PM_TEST_PORT=8766 npm run --prefix tools/vendor test:workflows -- \
  workspace-library-editor.spec.mjs library-editor-visual.spec.mjs \
  --project chromium --project touch --project firefox --project webkit
npm run --prefix tools/vendor test:comfyui   # library-sender.spec.mjs host regression
```

`native editor preview sends exact saved roles across renderer transitions` opens
the real workspace, creates a prompt with an explicitly empty negative, and sends the
selected pair through the acknowledged host bridge in Classic and Nodes 2.0, across
both switches and a workflow reload. It asserts the actual widgets and serialized
workflow, no queue or workflow load, unchanged history and pair metadata, and that
a connected destination rejects the preview without changing either role. It needs
the pinned disposable ComfyUI, so it runs in CI's "real ComfyUI compatibility" job.

Deploying this change needs no Python change and no ComfyUI restart: the diff for
the editor UI touches only HTML, CSS and JavaScript (plus test fixtures). A browser
refresh is enough. New editor modules have additive coverage floors in
`tools/vendor/frontend-coverage-baseline.json`; none were lowered.

## Native vocabulary backend (#236)

Routes under `/prompt_manager/vocabulary/`: `GET tags` and `GET categories`
(bounded pages, signed epoch-scoped cursors), `POST tags` (create or reuse an exact
name), `PATCH tags/{id}` (rename), `POST tags/{id}/delete`, `GET tags/{id}/impact`,
`POST categories/preview` and `POST categories/apply`. Every command carries
`expected_epoch`; tag commands also carry the exact `{id, name}` identity, and
category apply carries the preview's `impact_token`.

Error contract: command validation failures are 400 with `code: null`; reads
return `{success: false, error}` with no `code`, and impact on an unknown id is 404. A stale epoch is 409
`storage-changed`; a changed or missing identity, or a changed category projection,
is 409 `vocabulary-conflict` with a generic message. Both 409 bodies include the
current `library_epoch`. Nothing is written on any refusal.

Count semantics: a count is the number of eligible Library parents carrying the value
directly or through one active saved pair, exactly what `list_library(tags=[name])` or
`list_library(category=value)` returns. Unused registry tags report 0 and are kept by
`get_all_tags`, `suggest_tags`, backup and restore, duplicate cleanup, orphan pruning
and owner deletion (`TestRetainedTags` in `tests/test_database_backup.py`). Restore
rotates the epoch, so a pre-restore `expected_epoch` is refused with `storage-changed`.
Owner rows and their timestamps never change on a tag command; a category apply only
touches rows carrying the exact source.

Two legacy routes change additively: `GET /tags` now lists unused registry tags, and
`/tags/suggest` without context appends unused names at count 0. Unused tags become
deletable only through the native Delete tag (vocabulary UI PR), not on the legacy
tags page.

Both token functions (`library_edit_token`, `category_impact_token`) share
`canonical_token` in `database/library_tokens.py`; `TestSharedTokenHashing` pins their
outputs.

Deploying this backend needs a ComfyUI restart (Python and route changes).

## Native vocabulary UI (#236)

`#/tags` ("Tags & categories", page title "Tags") is a native workspace route; it no
longer hands off to the legacy tag manager. That page stays reachable from Overview's
"Open existing tag manager" card for tag search and sort, the untagged entry and
multi-tag AND/OR browsing; bulk prompt actions (delete, tag, category) stay on the
legacy admin page. The screen consumes only the `/prompt_manager/vocabulary/*` routes
above and adds no route, so PR B needs no ComfyUI restart: a browser refresh is enough.

Screen: a `+ Create tag` heading action, a Tag vocabulary table (columns Tag, Prompts,
Images shown as "—" with hidden "Not available yet", and Actions with Browse and
Rename / merge), and a Categories panel whose category rows end with the Manage
category row (Review). Lists load 100 rows at a time, append when scrolled into view
and keep `Load more tags` / `Load more categories` while more remain. Prompts counts
are the Library Browse totals.

Dialogs (`vocabulary-dialog.mjs`): **Create tag**, **Rename or merge tag** (renaming to
an existing name merges; it carries a **Delete tag…** confirmation that names the
links removed) and **Manage categories** (Category select, **Preview**, then **Apply**).
Names with commas are rejected on these native routes only.

Guarded states, each exercised against the real server in
`tests/browser/workspace-vocabulary.spec.mjs`: stale epoch (`storage-changed`) and
`vocabulary-conflict` write nothing and re-read; an unknown outcome keeps the dialog
open, re-reads and never resends a delete; an unconfirmed Delete offers Retry that only
re-reads the impact; a failed list read shows Retry with "Nothing was changed."; a
failed Load more moves keyboard focus to its Retry; a write that lands after its dialog
was force-closed or left with Back still re-reads the lists and drops the cached Library
shelf; Manage categories reads up to 1,000 categories and says so when capped.
**Browse** on a tag or category row opens Library with exactly that Tags or Category
filter (the concept puts the name in the search text instead); the Prompts count equals
the number of cards Browse shows.

Test files: `tests/js/workspace-vocabulary-state.test.js` (strict parsing, stale and
abort handling, late writes after dispose, sentences), `tests/js/workspace-state.test.js`
(route/title), `tests/browser/workspace-vocabulary.spec.mjs` (screen, dialogs, Browse,
mobile, the concept geometry/token/control comparison and its mutation check),
`tests/browser/vocabulary-canonical-comparison.mjs` (inventory mapping),
`tests/browser/vocabulary-visual.spec.mjs` (paired capture artifacts) and the
`focused node tag suggestions include a newly created tag` case in
`tests/browser/focused-node.spec.mjs`. The fixture is
`tests/browser/workspace_vocabulary_fixture.py`. The screen is not host-facing, so no
`tests/comfyui` case was added.

```sh
node --test tests/js/workspace-vocabulary-state.test.js tests/js/workspace-state.test.js
PM_TEST_PORT=8766 npm run --prefix tools/vendor test:workflows -- \
  workspace-vocabulary.spec.mjs vocabulary-visual.spec.mjs focused-node.spec.mjs \
  --project chromium --project touch --project firefox --project webkit
```

Recorded deviations from the concept (maintainer acceptance): no Auto-tag images
button and an Images "—" cell until the image-annotation slice; bounded lists with
Load more; title "Tags"; Manage categories adds a Category select and Preview before
Apply; "Apply" instead of "Apply in prototype"; Delete tag… inside Rename or merge;
Browse sets the exact filter; singular "1 positive prompt"; the production list appends
fixture-only tags after the concept's sample tags. Paired concept/production captures
for both themes and widths are attached as test artifacts; no PNG baseline is added
until acceptance. Not compared: hover states and the dialog focus ring (the page-level
keyboard ring and link ring offsets are asserted). No coverage floor was lowered;
`vocabulary-state.mjs`, `vocabulary.mjs` and `vocabulary-dialog.mjs` have additive
floors in `frontend-coverage-baseline.json`.
