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

## Performance budgets

A spec performance budget gets two tests:

- A structural test that always runs, on every OS/Python job and under the
  coverage tracer. It asserts counts, never time: SELECTs per request, the
  `EXPLAIN QUERY PLAN` of the hot query, cache builds on cold and warm requests,
  and calls of the sort key per cursor page. Example:
  `test_filter_work_is_bounded` in `tests/test_api_workspace_images.py`.
- A wall-clock test that runs only with `PROMPTMANAGER_PERF_BUDGET=1` and
  without a tracer (`sys.gettrace()` or a `sys.monitoring` tool). It skips
  otherwise. It warms up once, then measures three rounds and asserts the best
  round's p95; the failure message lists every timing. Example:
  `test_filter_budget` (Images filters, spec D12: p95 < 150 ms at 50,000 files
  and 20,000 links).

The `perf budget` job in `.github/workflows/test.yml` is the reference
measurement: `ubuntu-latest`, one Python, no coverage, only the wall-clock tests.
It runs on every push to `main` and on `workflow_dispatch`, never on pull
requests, because shared-runner timing must not block a merge. Dispatch it on a
branch to measure before merging.
Shared Windows and macOS runners do not run them; the structural tests cover
portability. Run it locally with
`PROMPTMANAGER_PERF_BUDGET=1 python -m unittest tests.test_api_workspace_images.TestWorkspaceImages.test_filter_budget`
and quote the printed p95 in the PR body.

A red `perf budget` run on `main` means the code got slower: fix the code (or
change the budget in the spec with a reason) in a follow-up PR. Never rerun it to
green. It is not a required check, and it cannot be one while it skips pull
requests (`.github/branch-protection.json`).

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
It renders in two concept surfaces (#249 D20–D22, D38). The Images side panel shows
only the concept content: cover, title, filename, the evidence badge with a category
chip, the + and − prompt blocks with copy and send, tag chips (the linked positive's
own tags filter Images, D21), the six facts, and a footer with "Send to ComfyUI" and
"Prompt history" only when prompt roles resolved or a link is saved; the sticky panel
scrolls inside the window. The viewer's details (`#viewer-details`) repeat that
content without the cover, then "File & provenance" (the honest file facts, the
`Prompt evidence` line, Download original and, for a listed file, D38's local-only
"Show in folder" after the Path) and "Workflow & raw metadata" (raw records and their
two downloads), then the pair send and Prompt history buttons. D22 words an empty
positive explicitly and makes confirmed prompt text focusable.
`tests/browser/inspector-concept.spec.mjs` compares both surfaces with the concept
(see the D20–D39 notes under Images).
`tests/browser/full-inspector.spec.mjs` covers original/metadata/workflow downloads,
response limits, source changes, nested sending, matched dark/light and mobile
geometry, and the loaded-collection viewer. `viewer matches the concept shell and
toolbar order` measures the concept's `#viewer-dialog` shell, header copy, toolbar
order and frame geometry in the concept and in production at 1600 and 390 pixels;
its #249 D34 mapping keeps production's Download link, the appended position and
reason lines, coarse 40px targets and the toolbar inside `.viewer-left` (Viewer.js's
own `.viewer-toolbar` rule lifts the concept's across the dialog). The viewer never fetches another page
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
A sampler's latent ancestry is walked through every link input of non-sampler
nodes within the same visit and depth budgets: with no earlier sampler it is a
single stage whatever the origin (VAEEncode, EmptyLatentBatch, EmptyFlux2LatentImage,
EmptyHunyuanLatentVideo, SetLatentNoiseMask, InpaintModelConditioning or
WanImageToVideo slot 2). An earlier sampler (img2img from a decode, hires fix) stays
`unresolved` "Multistage sampler ancestry is ambiguous" until stage texts are
compared; an unlisted node with sampler-shaped inputs (positive, negative, guider,
latent_image, a pipe, video embeds, custom-sampler parts, or a `model` link together
with conditioning, neg_conditioning, samples or latent) is `unsupported-node`
naming it, here and in a detailer's image ancestry; a latent that is not a link or
reaches its own sampler is `malformed`; running out of budget is `work-limit` or
`depth-limit`, never single stage. Each sampler's walk runs once per resolution;
selector branch runs share it and other candidates are charged its visit cost.
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

### Concept shell and comparison method (#249)

**CSS base and drift check.** `web/js/workspace/concept.css` is a byte-identical copy
of the approved concept's `docs/concepts/workspace/style.css`. Never edit the copy;
it is only rewritten from the concept with `npm run --prefix tools/vendor sync:concept`.
`npm run --prefix tools/vendor check` runs `sync-concept-css.mjs --check`, which fails
on a missing copy or names the first differing byte. `workspace.html` loads, in order,
the toolkit controls, Viewer.js, `concept.css`, the page stylesheets that their own
PRs retire, and the production overlay `workspace.css` last.
`tests/vendor/concept-css.test.mjs` asserts that order, the drift check, and that no
other page or canvas script loads workspace CSS.

**Overlay decision-marker lint.** `lintOverlay` in `tools/vendor/sync-concept-css.mjs`
fails any `workspace.css` rule whose selector is also a concept selector unless the
rule carries a `/* D<n> */` comment, before its selector or inside its block. The
D-number must be one of this spec's (#249): a comment that cites any other issue, such
as `D5 (#245)`, is that issue's ruling and is not a marker, so write `#245 decision 5`
for those. The lint test fails if `workspace.css` is missing. The match is by exact
selector, so a narrower selector over a concept rule (for example
`.ca-tools dialog.prompt-sender`) passes the lint. Such rules, and the page-stylesheet
leftovers such as `library.css` `.row-actions`, name the PR that retires them, by
convention.

The lint also runs over every page stylesheet that loads before the overlay and is
still kept (`library.css`; test `kept page stylesheets mark their concept-selector
overrides`). PR D deleted `images.css` and `inspector.css` (test `the retired shell,
vocabulary, editor, Images and inspector stylesheets stay deleted`); their few
production-only rules with a live consumer (inspector wrapping and the inert download,
the narrow Images container, the Library and recorded-run prompt blocks, the Archive
negative's `section.prompt-block`, the prompt sender) moved to the overlay under
`#249 D3`, and `the overlay keeps no retired Images page or inspector rules` keeps the
replaced card, filter, toolbar, preview, facts and inspector rules out.

**Comparison method.** Production must equal the concept except for the recorded
deviations in the #249 design (spec §8, D-numbers). It is never compared against
blessed production screenshots.
- The fixture is `tests/browser/workspace_concept_fixture.py`, reset through
  `POST /__test__/concept/reset`. It holds the concept's six prompts and pairs, and
  24 PNGs with real links, runs and the concept's inspector facts. The sidebar counts
  are therefore the concept's own: 24, 6 and 15.
- The shared inventory is `tests/browser/concept-inventory.mjs`. One self-contained
  in-page measurer runs the same concept selectors in the immutable concept
  (`/__test__/canonical/workspace`) and in production. An absent element becomes
  `null`, so the diff names its path (for example `chrome.breadcrumbs`) instead of
  failing the measurement.
- Each page's `map<Page>Concept(reference)` applies only the recorded deviations, each
  commented with its D-number. `diffInventory` must then report nothing.
- Sizes, offsets and heights that follow from a removed element are derived from the
  concept's own measurements, never copied from production.
- `mutate(page, {css, remove})` appends a stylesheet and detaches elements, and returns
  an undo. Each comparison has a mutation check proving it fails. For the shell, a
  `.sidebar nav a { min-height: 30px }` rule must fail at `chrome.navs.0.rows.0.minHeight`
  and `.height`, and removing `.breadcrumbs` must fail at `chrome.breadcrumbs`.
- Paired concept/production captures are attached as test artifacts. There are no PNG
  baselines.

`tests/browser/shell-concept.spec.mjs` compares the shell: the sidebar, topbar, page
heading and footer, at 1600x1100 and 390x844, in dark and light, under normal and
reduced motion, on Images, Prompt library and Tags. The heading is compared in full on
Tags; on Images and Library only its frame and h1 are compared, because their copy and
actions belong to their own PRs. The spec also compares the hover state of the first
non-current nav row, the Theme select and the ComfyUI link, and the keyboard rings of
the first nav link and heading button.

Recorded shell deviations:
- **D4**: a "Theme" select with Dark, Light and System. System follows the ComfyUI
  palette, then the OS.
- **D5**: no Activity button or Activity row until an activity center exists.
- **D28**: no SAVED VIEWS or COLLECTIONS, and no Design review. The bottom nav is
  System plus the production Overview hub (its own outline home glyph, not the concept's
  `activity` pulse, and no count).
  The connection reads "Your library, on your server" with a neutral muted dot.
- **D29**: no prototype copy. There is no "Interactive concept" badge and no
  "What changes & why" button. The footer reads "ComfyAssets · Local tools. Your
  library.", and the title has no " concept" suffix.
- **D31**: at `max-width: 950px` the overlay caps the one-column shell at
  `minmax(0, 1fr)`, so extreme zoom cannot widen it to the sidebar's min-content.
- **#245 D2**: no "Auto-tag images" on the Tags heading until the image-annotation
  slice.
- **Brand name**: not a deviation. The brand link's accessible name matches the
  concept's, mark included, because production's `.brand-mark` is not `aria-hidden`
  either. The inventory compares `brand.name` and `brand.mark.hidden`.

```sh
npm run --prefix tools/vendor check
npm test --prefix tools/vendor
npm run --prefix tools/vendor test:workflows -- shell-concept.spec.mjs \
  --project chromium --project touch --project firefox --project webkit
```

`tests/browser/images-concept.spec.mjs` compares the Images page (#249 C7): heading,
toolbar, filter row, caption, grid, the first six cards, the continuation and the panel
box, in Grid, List, Masonry and the Large and Compact sizes, at 1600x1100 and 390x844
in both themes and motion settings; grid, cards and panel also at 1200x900 (2 columns,
275px panel) and 1800x1100 (4 columns, 310px panel). The concept is compared once its
own Load more has loaded all 24 fixture images (D37: it loads 12 per batch, production
40); the Load more row is compared before the press in both documents. Behaviors: hover
zoom, the 2nd card opens the viewer titled "<title> · <filename>", Escape returns focus
to it (production only: the concept re-renders its grid and drops focus), Info on the
3rd card moves `.selected`, searches for "alpine" and for one image, and Load more with
its toast. `mapImagesConcept` applies D3, D8 (the selection bar's real actions only),
D16 (follow-up PR: no Save view), D9, D10, D14, D15, D17, D29, D32, D35 (singular counts) and D36 (categories
sorted alphabetically). Removed toolbar and filter-row items flow again from the
concept's own boxes (`reflow`), checked against the concept page with those items
removed at runtime. Masonry tops and heights are compared unrounded, within one layout
unit per card above in the column (D3). `one footer height for the collection (D3)`
seeds two seedless files (`seedless_images` on `/__test__/concept/reset`) and asserts
that every row reserves the tallest footer.

### Images page controls and recorded deviations (#249 PR C)

The Images page renders the concept's DOM with real data. Its controls, top to bottom:

- **Heading**: "Images" / "Your generations, their prompts, and the next idea.",
  `Import` (opens "Import & inspect") and the shell's `New prompt`.
- **Toolbar** (`toolbar.mjs`, shared with the Library in PR E): `#search` "Search
  prompts, tags, filenames…" (debounced 250 ms; filename, linked positive text and
  Library title, and tag names), `#category-filter` "All categories" plus the
  Library's categories, `#sort` (Recently added, Name A–Z, Highest rated, then Oldest
  first and Name Z–A), and `Filters` (opens "Refine this view").
- **Filter row**: Grid / List / Masonry (`.segmented`, group "Image layout"),
  "Thumbnail size" Comfortable / Large / Compact, the scope chip ("All output folders"
  or "<source> · <folder>"), and "Group by positive prompt →" (goes to the Library).
- **Caption**: "N of M images · K positive prompts" (`#result-count`) and, under
  Recently added only, the newest result's day.
- **Grid** (`#image-grid`): concept media cards. The image opens the viewer
  (click, Enter, Space); Info selects the card into the side panel; the star toggles
  the favorite; arrows, Home and End move between cards. Bounded rendering keeps the
  DOM small, and returning to Images restores filters, layout, size, selection and
  scroll.
- **Continuation** (`#gallery-continuation`): scrolling near it loads the next page;
  `Load more images` and `Retry` do the same by hand, with a toast per batch.
- **Viewer** (`dialog#viewer-dialog`): "<title> · <filename>", the concept toolbar
  (a "Media controls" group), the shared file inspector beside the stage, and Escape
  returns focus to the opening image.

How the earlier workspace Images filters moved (old → new). The legacy `/gallery`,
`/admin` and `/metadata` pages are unchanged.

| Before PR C | Now |
| --- | --- |
| "Search filenames" (filename or folder) | toolbar search, which also matches linked prompt text, title and tags (D12) |
| Source | "Refine this view" → Source (D14); shown in the scope chip |
| Folder | "Refine this view" → Folder (D14); shown in the scope chip |
| Media (All media / Images / Videos) | "Refine this view" → Media (D14) |
| Sort: Newest first / Oldest first / Filename A–Z / Filename Z–A | toolbar sort: Recently added / Oldest first / Name A–Z / Name Z–A, plus Highest rated (D13, D14) |
| (none) | toolbar category and "Refine this view" → Tags, through the linked positive (D13, D14) |
| Refresh | Import → "Scan an output folder" → Rescan (D15) |
| Grid / List / Masonry, Comfortable / Large / Compact | the same choices in the concept filter row |
| Card image → side panel; preview icon → viewer | image → viewer; Info → side panel (D19) |
| "All images" heading and count | the concept caption and count line (D17, D35) |

Recorded deviations on this page (spec §8):
- **D3**: bounded rendering stays. Every grid row reserves the tallest measured card
  footer, masonry tops may differ by sub-pixel snapping (at most one layout unit per
  card above in the column), and a List row's info takes its natural height, centred
  beside the 160×95 art.
- **D5**, **D29**: no Activity button and no prototype copy (shell).
- **D7**: the star is a real persisted favorite with "Added to favorites." /
  "Removed from favorites." and a rollback toast on failure.
- **D8**: every Images card has the concept's select checkbox before the star; the
  `#bulkbar` shows "N selected" and only the real actions in the concept's order (Set
  category, Export metadata, Clear). Add tags and Add to collection wait for image tags
  and collections; "Remove links…" is dropped. The selection survives Load more and
  bounded re-mounting and is cleared whenever the collection is replaced (search,
  category, sort, Filters, source change, rescan, reload). A return to Images restores
  the saved collection unreplaced and keeps its selection; a full page reload starts
  empty. The Library-embedded gallery has no checkbox and no bar. Export metadata reads
  each checked image's inspection (at most 4 at a time) and downloads one
  `prompt-manager-images-metadata.json` (`format`, `version: 1`, `exported_at`, and the
  inspector's own "Download metadata JSON" envelopes in listing order); the button is
  `aria-disabled` and ignores a second press while it runs, the selection is kept, and
  the toast names the unread images (none read: no download); leaving Images aborts the
  reads with no download and no toast. Set category opens the
  concept's `simpleInput` dialog ("Categorize selected images", `input#action-input`
  labelled Category, Cancel / Apply) and posts the selection's distinct linked positive
  ids to `/prompt_manager/bulk/category` (images have no category; the filter matches
  the linked positive's). With no linked image checked it posts nothing and opens no
  dialog; an empty value keeps the dialog open; the field stops at the server's 100
  characters; success reloads the categories and the listing together (clearing the
  selection, focus to the heading, and, as any replaced collection does, the inspector
  moves to the first listed image); failure keeps the selection. The write carries no
  page signal: leaving Images while it runs lets it finish without a toast, and the return
  loads the collection afresh instead of the saved visit. Three deviations from the
  concept's `simpleInput`: the field starts empty (the concept pre-fills its demo
  "Landscapes"), the action reads "Apply" (not "Apply in prototype"), and the
  nothing-linked check runs on the Set category press, so no dialog opens. The route
  rejects anything but 1-1000 positive integer ids within SQLite's INTEGER range
  (deduplicated) and a category that fails `validate_category`. `images selection bar matches
  the concept` compares the bar and the checked card with one card checked; the omitted
  actions flow again from the concept's own boxes, checked against the concept page with
  them removed at runtime.
- **D9**: no Tag button until the image-annotations slice.
- **D10**: the card title is the linked positive's display title, else the filename;
  the `h3` keeps the relative path in `title`.
- **D11**: the subtitle is "W × H · EXT · Seed N" with unknown parts omitted.
- **D14**: Source, Folder, Media and Tags live in "Refine this view" (no "Prompt
  evidence" field, no scope note); the primary action reads "Apply filters"; extra
  sorts come after the concept's three; long folder names wrap inside the scope chip.
  Folder lists only the chosen Source's folders (each workspace root lists its own),
  and an unchanged Apply or Clear reloads nothing.
- **D15**: Import offers "Inspect a local image" (`#/inspect`) and "Scan an output
  folder" (Rescan, `/gallery/rescan`); no "Import portable recipes". The concept has
  no rescan copy: toasts say "Rescanning output folders…", then "Rescan finished. N
  files in M output folders." (the scan's `total` and `roots`) or "Rescan could not
  finish. Try again."; while it runs, a reopened Import shows an aria-disabled
  "Rescanning…".
- **D16 (follow-up)**: no Save view in PR C; a follow-up PR adds real saved views.
- **D17**: the caption date is the newest result's day under Recently added (", YYYY"
  outside the current year), hidden under every other sort.
- **D18**: the concept hover zoom, with no sticky zoom on hover-less (touch) devices.
- **D32**: the layout control may wrap, so the filter and continuation rows never
  overflow at 390px and 200% zoom.
- **D33**: after Load more or Retry, focus moves to the first new image without
  scrolling, and only while focus is still on the pressed control or `body`.
- **D34**: the viewer keeps its toolbar under the stage in `.viewer-left`, a flex
  column above 700px, `max-height: min(94dvh, 94%)`, the "Download original" link,
  the appended position and reason lines, full-stage video and failure overlay,
  40×40 coarse targets, production's control names and "Media controls" group, and
  `overscroll-behavior: contain`.
- **D35**: counts pluralize ("1 positive prompt", "1 image", "1 more image loaded").
- **D36**: the category list is the Library's, sorted alphabetically (case-insensitive)
  by the page, whatever order the server sends.
- **D37**: production loads 40 images per page (the concept 12), so the continuation
  and toast use 40; after Escape production returns focus to the opening image.
- **D44** (#249 PR E): Filters reads "Filters · N" while N of Source, Folder, Media and
  Tags are set in "Refine this view" (see the Prompt library deviations).
- **D20**: the side panel shows only the concept content; "File & provenance",
  "Workflow & raw metadata" and the downloads are only in the viewer's details
  (`#viewer-details`: the panel content without the cover in a class-less wrapper, the
  two disclosures, then the pair send and Prompt history buttons). "File & provenance"
  keeps the honest facts (current-file note, Source, Path, Size, Dimensions, Format,
  Media type, Modified, the reported-location occurrence, the saved-association
  sentence, Download original) and the concept's `Prompt evidence: <badge>` line in the
  D21 badge's words in lower case, left out when there is no badge. Raw records keep
  their names, representations and the two downloads with their unavailable reasons;
  the viewer's pair send and history buttons may wrap at 200% zoom. Tag chips in the
  viewer are plain (the concept's go to the Library). The details' disclosure
  summaries and "Download original" keep 40px coarse targets (D34). Prompt history
  closes the viewer and, when its target is the route already shown, re-renders it
  (the concept's `go()`).
- **D20/D21 side panel**: the sticky panel scrolls inside the window
  (`max-block-size` with `overflow: auto`, not the concept's `overflow: hidden`), so
  long real prompts keep the footer reachable; an empty badge or tag `.tags` row is
  left out (the concept always renders both); the footer, its pair send and Prompt
  history appear only with resolved prompt roles or a saved link; a video file keeps
  `video.detail-cover`; and only the linked positive's own tags are `button.chip`
  filters, while recipe, recorded-run and embedded tags stay plain `span.chip`.
- **D22**: an empty positive reads "No positive prompt (explicitly empty)" (the concept
  applies its negative wording to both roles), and a confirmed `p.prompt-text` is
  focusable (`tabindex=0`, marked focus ring) so long prompts stay keyboard-readable.
- **D38 Show in folder** (maintainer decision 2026-10-20, beyond the mock): in the
  viewer's File & provenance the Path value stays text, followed in its `dd` by a
  "Show in folder" button (`Show <filename> in folder`), only for a listed file
  (`root_index`, `root_scope`, `path`), so never in the side panel or a recorded run.
  It POSTs that identity as JSON to `/prompt_manager/images/reveal` and toasts:

  | Result | Toast |
  |---|---|
  | Success | "Opened the folder on this computer." |
  | 403 `not-local` | "Show in folder works only in a browser on the computer running ComfyUI." |
  | 501 `unavailable` | "No file manager is available on this computer." |
  | 429 `busy` | "The folder is already opening." |
  | Anything else (415, 404, 409, 500, offline) | "The folder could not be opened. Try again." |

  The route is local-only. It answers 403 `not-local` unless all of these hold: no
  forwarding-style header (`Forwarded`, `Via`, `X-Real-IP`, `CF-Connecting-IP`,
  `True-Client-IP`, `X-Client-IP`, `X-Cluster-Client-IP`, `Fastly-Client-IP`, and any
  `X-Forwarded-*`, `X-Original-*` or `*-Client-IP`); a loopback `request.remote`; a
  loopback `Host` (`localhost`, `*.localhost` or a loopback address, against DNS
  rebinding); an `Origin` equal to the request's own scheme, host and port (absent,
  `null`, another port or another scheme is refused; browsers send `Origin` on every
  fetch POST); and, when sent, `Sec-Fetch-Site: same-origin`. A body that is not
  `application/json` is 415 `unsupported-media-type`, so any cross-origin request
  needs a CORS preflight. The body is strict (an int root, a 64-hex scope, a bounded
  relative path; absolute, drive and `..` paths are 400), the scope goes through
  `_gallery_scope_error` (409 `source-changed`), and the path resolves only inside
  that root to a regular media file (403/404; a resolution `OSError` such as
  `ENAMETOOLONG` is the same JSON 404, and the client's path is never logged at error
  level). One reveal runs at a time per process with a 1 s cooldown after it
  (`RevealGate`, 429 `busy`), on its own single worker (`reveal_executor`), never
  ComfyUI's default executor. Just before launching, `utils/file_reveal.py` re-checks
  that the real path is still a regular file inside the root (a swapped parent is
  404). Windows starts no process: `ILCreateFromPathW` and
  `SHOpenFolderAndSelectItems` select the file through the shell API (Explorer's own
  command line splits on commas, so a path like `output\a,\Windows\...` must never
  reach it), falling back to `os.startfile(folder, "explore")`. macOS runs
  `/usr/bin/open -R`; Linux asks FileManager1 `ShowItems` with `dbus-send
  --print-reply --reply-timeout=2000` (a real reply or a fallback) and else runs
  `xdg-open` on the folder. Those openers are argv lists resolved only on a fixed
  system PATH, with no shell, DEVNULL streams, `close_fds`, their own session, a
  minimal environment (no venv PATH, PYTHONPATH or API keys) and a daemon-thread
  reaper. `tests/test_image_reveal.py` mocks `Popen`, `sys.platform`, `shutil.which`,
  the shell API and `os.startfile`; the browser server's opener is a stub
  (`POST`/`GET /__test__/reveal`) with no cooldown. The inspector comparison reads
  closed disclosures as hidden, so the button needs no mapping there.

  Residual risk, accepted: a reverse proxy or tunnel that adds no forwarding header
  (a bare nginx `proxy_pass` to 127.0.0.1, `ssh -R`, `socat`) cannot be told apart
  from a local client, and a non-browser client behind it can forge a matching
  `Host` and `Origin`. Such a client can open a folder of a listed file on the host's
  screen, at most once a second, and learn whether that file exists (which the
  listing routes already tell it). Operators who expose ComfyUI that way accept this.
- **D39**: a recipe-linked image shows its recipe's own category and tags (#224), not
  its positive's. The concept shows the prompt's category and tags for every image;
  the concept fixture's recipes have neither, so their images show no category chip
  and no tag row. `positive_links` on `/__test__/concept/reset` links the newest
  images to their positive alone, so the inspector comparison sees the positive's
  category ("Positive prompt category: " hidden prefix, D21) and tag filters.
- **D42**: the concept never shows a toast and a page alert together. Production keeps
  the concept's fixed toast (centred, 22px from the bottom, 12px at 700px and below)
  and, when an Import → Rescan toast leaves the Images discovery alert
  (`.options-status`), scrolls that alert into view only as far as needed, with
  `scroll-margin-block-end: 96px`, so it is never under the toast.
  `Rescan toast does not cover the discovery alert it leaves` ends the window inside
  the toast's band at 1600 and 390 pixels and measures both boxes.

`tests/browser/inspector-concept.spec.mjs` compares the side panel and the viewer's
full details (#249 D4) for alpine_00001.png, at 1600x1100 and 390x844 in both themes:
structure, cover, heading, filename, badges, prompt blocks (role labels, actions,
text), tag chips, facts, footer, the viewer's disclosures and pair actions, and every
offered control's name and minimum height. Each role's copy and send are named after
the prompt, as the concept's are ("Copy positive prompt: <title>"). The concept loads
all 24 fixture images first (D37). Behaviors: copy shows "Prompt copied." (or the
same "Copy prompt" dialog in both); the pair send previews both roles in the concept
and is applied and acknowledged by a test ComfyUI tab in production; the `cinematic`
chip sets the Images Tags filter (D21: the concept searches the word, which also
matches the untagged forest prompt's text); Prompt history goes to
`#/library/<positive id>` (the concept's `#prompt/<id>`). `mapInspectorConcept`
applies D3 (the `.file-inspector` class of the production-only rules), D20 (the
viewer's plain tag chips, laid out by the concept page itself with its chips made
plain at runtime), the D20/D21 side-panel scroll (`overflow: auto`,
`max-height: calc(100dvh - 40px)` above 700px), D21 (the category's hidden scope
prefix) and D22 (focusable prompt text). `inspector comparison detects mutations`
changes `.prompt-heading .role-label` letter spacing and removes `.detail-footer`.

Manual check (quick), after a browser refresh on Images: hover an image (it zooms);
click it (the viewer reads "<title> · <filename>"; Escape closes it); press a card's
Info (the side panel changes); search a word from a prompt (matching images remain);
open Filters (Source, Folder and Media are there); Import → Rescan picks up a newly
saved file.

```sh
npm run --prefix tools/vendor test:workflows -- images-concept.spec.mjs \
  workspace-images.spec.mjs full-inspector.spec.mjs inspector-concept.spec.mjs \
  --project chromium --project touch --project firefox --project webkit
```

### Prompt library cards and recorded deviations (#249 PR E)

The shelf uses the shared concept toolbar and caption (E1) and the concept's
`article.prompt-card` (app.js `renderLibrary` a586, style.css s653–708): `.card-art`
(at most two previews), `.card-content` (`.card-title` with the title `button` and the
category `span.chip`, the positive prompt block, `.card-meta`, `.tags`) and
`.card-footer` (the rating and last-use line, then `.row-actions` with the positive
`button.send.icon-btn` and "Explore history"). Rows stay windowed (`.library-row`).
`tests/browser/workspace-library.spec.mjs` covers the markup, the clamp, the buttons and
the measured row heights on Chromium and touch.

Recorded deviations on this page:
- **D23**: real card facts. "N runs" is the parent's `run_count`. The footer reads
  "Used today", "Used yesterday", "Used N days ago" (under a week) or "Used <Mon D>"
  ("Used <Mon D>, <YYYY>" for a use in another calendar year, so last year's date never
  reads as this year's), counted in the viewer's local calendar days from `last_used_at` (a time ahead of the
  browser reads as today), and "Not used yet" without one. Stars are `★` × rating with
  a visually hidden "Rated N out of 5"; an unrated parent shows only the last-use words.
  The meta chip reads "− N negative variants" (singular for one). The title and Explore
  history are buttons that open `#/library/<id>`, as the concept's are; the trade-off
  against the old links is that they offer no open in a new tab, middle-click or URL on
  hover. The positive is clamped to two lines
  (`.prompt-card .prompt-text`); copy and send use the full text. A long title wraps
  inside the title row, and the category chip keeps whole words ("Uncategorized" for
  none). A parent with one preview, or none, fills the art row; no preview reads "No
  available image previews" (`.small`). Each card's actions add its title to their names
  ("Copy positive prompt: <title>", as the concept's do).
- **D40**: production-only shelf copy the concept never shows. A failed page reads
  "Prompt library could not be loaded. Retry." beside "Retry prompt library" (no Refresh,
  D15). A failed category list reads "Categories could not be loaded. Open the Prompt
  library again to retry." (Retry re-reads it only when the shelf failed too). The
  continuation keeps "Loading prompt library…", "No matching prompts. Try changing the
  filters." (the concept: "No matching prompts."), "You’re all caught up.", "The saved
  prompt is hidden by the current filters." and "Load more prompts".
- **D41**: the search keeps the concept's shared placeholder "Search prompts, tags,
  filenames…" for parity with Images, although Library search matches positive text and
  titles and saved negative text and variant titles, not filenames or tags (Tags live in
  "Refine this view", D14).
- **D43**: a card's tag chips come in the store's alphabetical order (`ORDER BY t.name`,
  as everywhere tags are read); the concept lists each sample's tags in authored order.
- **D44**: the shared toolbar's Filters button reads "Filters · N" while N "Refine this
  view" filters are active, and plain "Filters" otherwise; the text is its accessible
  name, so the count is spoken. The concept has no indicator, and without one a Tags
  filter set by Tags & categories Browse was invisible on the shelf. The Library counts
  Tags; Images counts Source, Folder, Media and Tags (each once). The toolbar category is
  not a Refine filter and is not counted. The caption's count is a `role="status"`
  region, written only when it changes and kept shown while the shelf reloads, so
  Load more and scrolling never re-announce it.

`tests/browser/library-concept.spec.mjs` compares the shelf (#249 E3) on the
concept-shaped fixture, which creates the concept's prompts last to first so Recently
added lists them in the concept's order: heading, toolbar, caption, grid and the six
cards (art, title row, prompt block, meta, tags, footer) at 1600x1100 and 390x844 in
both themes and motion settings, and the grid and cards at 1800x1100 (3 columns).
Behaviors, in both documents: the title and Explore history open the prompt's history
(its `h1` is the title), the footer send carries the positive only (a bridge host page
records the roles production's preview asks for), copy writes the full positive with
"Prompt copied.", and the "neon" search and the Landscapes category leave the same cards
and caption. `mapLibraryConcept` applies D14 (Oldest first appended), D16 (no Save
view; the toolbar flows again, checked against the concept without it), D23 (the
title button's `min-width: 0`; "− 1 negative variant", measured on the concept with the
singular at runtime; the spoken "Rated N out of 5"; the two-line clamp, which the
concept's samples exceed at 390px, checked against the concept with the clamp rule
applied), D35 (singular captions), D36 (sorted categories), D40 (the continuation after
the grid) and D43 (sorted chips, checked against the concept with its chips sorted).
D44 needs no mapping: the comparison runs with no Refine filter active, so the button
reads "Filters" as the concept's does. Nothing else follows the grid: the legacy
"Current prompt library" link is removed from the shelf; Overview's legacy card ("Open
existing prompt library") and the sidebar still link `/prompt_manager/admin`
(`tests/browser/workspace.spec.mjs`). `library comparison detects
mutations` injects `.card-footer { padding: 2px }` and removes `.card-meta .chip.red`.

Manual check (quick), after a browser refresh on Prompt library: each card shows the
title with its category chip on one line, two lines of the positive with copy and send,
"− N negative variants · N runs · N images" and the tags; the footer reads "★★★ · Used
today" (or the real date) with send and "Explore history"; Filters → Tags still narrows
the shelf, and the button then reads "Filters · 1" (D44), also after a Browse from Tags &
categories.

```sh
npm run --prefix tools/vendor test:workflows -- library-concept.spec.mjs \
  workspace-library.spec.mjs --project chromium --project touch --project firefox --project webkit
```

### Prompt history page and recorded deviations (#249 PR F-a)

The history page (`#/library/<id>`) takes the concept's heading, summary, negative
variants panel and comparison (app.js `renderHistory` a591–616, the dialogs a1282–1304,
style.css 1059–1179). The summary is `article.surface` with the category chip, the
positive and selected negative prompt blocks and `.card-meta` "N tracked runs · N images
· N negative variants" (tracked runs from the recorded window's `recorded_run_count`).
The panel is `section.surface.timeline-panel` (region "Saved negative variants") with
Compare negatives, + Add negative and Show archived, a `.timeline` of `.variant-lane`
rows (`--lane-offset` 38 + 47i, the "−" `.variant-dot`, "<name> · N runs" and a
`.run-dot` for a pair counted more than once) and `.timeline-lower` with the legend and
All images for this positive. Recorded executions, frozen fields, send and archived
lanes keep their contracts. `tests/browser/workspace-history.spec.mjs` and
`workspace-library-editor.spec.mjs` cover them on Chromium and touch.

Spec decisions this page applies: **D24** (Send to ComfyUI is disabled only while the
selected pair's roles load), **D25** (Compare negatives compares a baseline with the
selected red dot, and lists the selected side's added comma terms) and **D26** (All
images for this positive is a dialog with its own bounded gallery; the run dot opens
Recorded run for the pair's newest recorded execution, else a Repeat run dialog with the
pair's newest linked image, reading the archived gallery for an archived pair).

Recorded deviations on this page:
- **D45**: a variant dot is named "Select negative <title>" when no other loaded lane
  shares that title (compared trimmed), else "Select negative variant N"; a title that
  itself reads "variant N" always uses the lane's own number, so no two names repeat.
  The bounded "N of M saved negative variants" count stays a visually hidden status,
  and production's "Saved combinations, not an execution timeline." is removed.
  Archived lanes (#235) are concept lanes marked `.library-variant-archived` at the .6
  marker emphasis (their text stays at full contrast) with their Restore button. Under
  `pointer: coarse` the variant dot keeps a 47px target and the 12px run dot a 44px one
  (`::after` overlays). While a run dot shows, the lane text wraps within 139px before
  it. The panel heading, the legend row and All images wrap at 390px and 200% zoom.
- **D46**: lanes list newest first (`ORDER BY pp.id DESC`), so the comparison's
  baseline is the oldest (lowest-id) active variant, not the first lane; while the
  variant page has more, the oldest is not loaded and the section reads "Load more
  negative variants to reach the baseline." instead of guessing. The two blocks are
  named "Baseline" and "Selected" (regions and copy/send actions), so no name repeats.
  Compare negatives is disabled while the positive has no active variant. The page
  opens on the newest lane (existing behavior), while the concept opens on
  `negatives[0]`, its baseline: the first comparison reads "Baseline compared with
  <newest>" where the concept compares the baseline with itself. The history concept
  comparison (PR F-b, F5) maps this.
- **D47** (#249 PR F-b): the shared gallery is the concept's `section.history-gallery`
  (region "Shared gallery") with `.panel-heading` (h2 "Shared gallery", `span.small`
  "<name> · same positive prompt") after the variants panel (and Compare), and
  recorded executions (#209) follow it, as the concept has no recorded section. The
  name is the lane's own name ("<title>", else "Negative variant N"), with " · Archived"
  for an archived pair, and follows a rename. Its cards are the Images media cards: the
  star wherever the read returns `favorite`, no checkbox or selection bar (Images only,
  #259), the image opens the Viewer. A saved variant with no images shows the concept's
  `.empty` "Saved variant · no runs yet." beside the grid (the concept puts it inside
  `.image-grid`, which only holds positioned cards here). The concept's "Shared gallery"
  heading is the gallery's only heading and names its region: the embedded collection's
  "Linked images" heading and "N loaded · N linked images" count are removed, since they
  repeated it (maintainer ruling, 2026-10-21). Two production pieces stay, which the
  concept's gallery does not show: the layout row (Grid/List/Masonry and Thumbnail size),
  because the legacy gallery let people change layout and density and nothing else on the
  page offers it, and the selected-image side panel, the one shared inspector the Images
  page uses (spec §3.3), so an image's details stay reachable without opening the viewer.
  The same ruling covers the page's two media dialogs, All images for this positive and
  Repeat run: as in the concept (app.js a1282-1304), each is named by its own dialog
  title, with no "Linked images" heading or "N loaded · N linked images" count, and a
  stranded focus returns to the dialog's close button (maintainer ruling, 2026-10-21).
  The Images page keeps its caption. With no saved variant the page shows every image
  of the positive under "All images for this positive, including unassigned images. …"
  and no empty copy (a state the concept never reaches).

The run dot reads `GET /prompt_manager/library/{positive_id}/history/pairs/{pair_id}/newest`
(see "Chronological Library history"), and each lane's "N runs" is the pair's
`run_count` (see "Workspace data fields").

`tests/browser/history-concept.spec.mjs` (#249 PR F-b, F5) compares this page with the
immutable concept's `#prompt/alpine` on the concept fixture, at 1600×1100 and 390×844 in
both themes and motion settings, on Chromium and touch: the heading, the summary, the
variants panel, the comparison (Compare negatives pressed) and the shared gallery's
place and heading, all measured by `inventoryOf`'s `history` section with the concept's
selectors in both documents, with "Clean architecture" selected (the middle lane in
either order). The fixture links and records its images oldest first, so ids grow with
time as real runs do, and counts one pair use per image, which gives the concept's
"Quality baseline · 2 runs" and its run dot. `mapHistoryConcept` applies D24 (the four
recorded runs give the concept's own "4 tracked runs", asserted on both), D45 (the
visually hidden "3 of 3 saved negative variants" status, and its wrap rules applied to
the concept at runtime), D35 and D46 (the concept's lanes reordered newest first with
"1 run", laid out by the browser), D46's Baseline/Selected action names, #235's Show
archived button (added to the concept at runtime) and D47 (the gallery's body is the
Images collection, so its height is not compared). D25: each compared block mounts in a
`display: contents` child of the concept's `.compare-item`, so the item carries no
production class or margin. Behaviors are compared in both documents: a red dot changes
the negative block and the gallery, Compare toggles `aria-pressed`, All images opens a
dialog with the positive's four images, the run dot opens a dialog (D26: Recorded run
where the concept shows Repeat run), neither dialog has a collection caption (D47), the
heading send previews the selected pair, and
← Prompt library navigates. `history comparison detects mutations` injects
`.ca-tools .variant-dot { width: 20px }` and removes Compare negatives. The older
`history-canonical-comparison.mjs` stays for `workspace-visual.spec.mjs`'s recorded
executions (#209) checks; its production selectors are now the concept's.

```sh
npm run --prefix tools/vendor test:workflows -- history-concept.spec.mjs \
  workspace-history.spec.mjs --project chromium --project touch --project firefox --project webkit
```

## Workspace data fields for the concept pages (#249)

These fields are additive. The legacy `/prompt_manager/images/output` listing (no
`mode=workspace`) and the legacy pages do not change, and existing workspace pages
ignore the new keys.

Workspace image items (`/prompt_manager/images/output?mode=workspace`, and Library
image galleries) gain three card facts. Library list previews carry none of them
and skip the parse.

- `linked: {status, positive_id, title, title_source, category, rating}`. One
  `get_image_link_summaries` SELECT per 200 paths, matched exactly as the
  inspection route matches (exact or normalized `file_path`, no basename
  fallback). `status` is `linked`, `ambiguous` (several owners), `none`, or
  `unavailable` when the database read failed (logged, no error text). Treat
  anything but `linked` as the filename fallback. In pair scope (the image has
  exactly one distinct pair), `positive_id` is the pair's positive, and the title
  comes from that positive's Library title, then its text's first line, never
  from the pair's own title, which names the negative variant (#249 D10/D20). In
  positive scope it comes from the Library title, then the first line. A first line longer than 80 code points is cut to 80 plus `…`, like
  `library.mjs` `title()`. `title_source` is `stored`, `text` or null. `category`
  and `rating` come from the linked positive (D13), not from the pair. The title
  gate is shared with the inspector (`_verified_title`): a `stored` title always
  shows. A `text` title shows only when the file's own positive, parsed from its
  records by the shared parser, gives the same display title. Otherwise the title
  is null on the card and in the inspector header. Videos and unreadable files
  have no parsed positive, so their `text` titles are withheld.
- `seed`: a decimal string, or null. Always a string, because `JSON.parse` rounds
  numbers past 2**53 and ComfyUI's randomized seeds are often that large. It is
  `extract_generation_params` from `utils/generation_params.py` (the inspector's
  legacy parser, #251), run on the `prompt`/`workflow` records read with the
  dimensions. A PNG is read by the inspector's own reader
  (`utils.prompt_inspector.read_inspection_file`): text after IDAT counts, the
  shared byte budgets apply, and a file with conflicting duplicate `prompt` or
  `workflow` chunks gets no card seed, as the inspector refuses it (the card
  keeps its dimensions). Other formats use Pillow's header `info`, as the
  inspector's header read does. The records go through the inspection
  projection first (`utils/inspection_response.project_graph`), which carries
  integers past 2**53 as their exact digits, so the card and the inspector read
  the same digits. The parse runs after the file handle closes. A record over
  2 MiB (UTF-8), a parser error, a recursion error, a placeholder, or a
  non-integral, boolean or non-finite seed gives null. A card never shows a seed
  that differs from the inspector's
  (`test_card_seed_equals_inspector_seed_when_both_present`, which `JSON.parse`s
  the listing in Node and compares `String(seed)` with the inspector's seed,
  including one past 2**53). The card shows no seed for A1111 `parameters` files
  or for a BasicGuider custom sampler, where the inspector does.
- Header facts are cached. Dimensions, `seed` and the parsed positive's display
  title are read once per `(path, mtime)` of the current gallery scan and kept
  beside the scan's per-file values. A monitor invalidation or TTL rescan keeps
  the facts of unchanged files and drops the rest, and a roots change drops them
  all. A header that could not be read is not stored. Links and favorites are
  never cached (`test_warm_page_reuses_card_facts_per_scan`: a warm page reads
  and parses no header). Measured cold cost is about 3.4 ms per 50 KB record set
  and 25 ms per 400 KB workflow.
- `favorite: bool`. One favorites SELECT per root per page. A read failure logs a
  warning and shows false.

Filters (`mode=workspace`; anything invalid is 400 "Invalid gallery filters"):

- `q` (at most 500 characters). An entry passes when the case-folded relative path
  contains it, or when its linked positive's text, Library title or a tag name
  contains it (SQLite `LIKE` with escaping, ASCII case folding only). A pair's own
  title names its negative variant, so it is not searched (#249 D12): a variant
  name alone matches nothing. Search does not apply the card title gate, and does
  not search pair tags.
- `category` (exact and case-sensitive, at most 200 characters, stripped; blank
  means none) and `tags` (a comma list, blanks dropped, order-free, AND, at most 30
  tags of at most 200 characters) admit linked images only, through the linked
  positive.
- Ambiguous files never match a linked filter, and they rank as unlinked.
- `sort` is `newest | oldest | name | name-desc | rating`. `rating` orders by
  rating 5 to 1, then linked but unrated, then unlinked and ambiguous; ties break
  newest first. A signed cursor covers every filter and the sort, so reusing it
  with a change is 400.
- One link read (`get_image_link_matches`, streamed and bounded to the listed
  paths) serves the filters, the rating sort and the count.

`positive_count` is the number of distinct linked positives over the full matching
set, not the page:

- Present on every first page, and on every page of a filtered (`q`, `category`,
  `tags`) or `sort=rating` listing.
- Absent on a plain listing's cursor pages, which read no links. The frontend
  keeps the first page's value.
- Null when a plain first page's link read failed. The listing still returns 200,
  and consumers then hide "· K positive prompts". A `q` search alone also
  survives the failure: it matches filenames only, as before linked search, and
  every page's count is null (logged with the traceback). A failure under
  `category`, `tags` or the rating sort stays a safe 500.

Library parents (list, detail and command receipts) gain `run_count` (an int; the
D23 migration estimate for rows from before 3.2.4) and `last_used_at`, a UTC ISO
string. A naive value reads as UTC, and an unreadable or out-of-range value is
null. The edit token ignores both. `variant_total` has two meanings. On the list
response it counts the active variants over all filtered parents (the same
snapshot as `total`, the same on every cursor page). On the detail response it
counts one positive's variants in the chosen archive scope. The Library caption
reads it from the list state. Each detail variant (active and archived) also carries
its pair's `run_count` (`prompt_pairs.run_count`, 0 when null), which the history lanes
show as "N runs" (#249 F2).

Favorites store (D7 = A): `PUT` and `DELETE /prompt_manager/images/favorite` with
`{root_index, path}`. Both are idempotent and answer
`{success, favorite, root_index, path}`. The boundary returns 400 for an unknown
root or a malformed body, and 403 for traversal, an absolute or drive path, NUL, a
non-media name, or a key over the stored bound. PUT needs a listed file (404
otherwise). DELETE also clears a path that is no longer listed. Send the DELETE
body as `application/json` (`api.del(url, {body})` in the shared API client).
The Images card star (#249 C5) toggles optimistically, allows one request per file
at a time, and on any failure restores the previous state and announces it in the
shared toast. The browser fixtures clear `image_favorites` on reset, and the
concept fixture stars the concept's favorites (each other prompt's first image).

- The table is `image_favorites(root_key, relative_path, created_at)`. `root_key`
  is the root's real path (`os.path.realpath`), case-normalized with `/`
  separators. It is stored server-side only and never returned. Folder labels are
  not unique across roots, so they are not used as keys.
- `relative_path` is `normcase(path)` with `/`. On Windows a case-only rename
  keeps the star. On POSIX the key is the exact spelling, so `A.png` and `a.png`
  stay separate (tests swap in `ntpath` through `py.api.images._path_flavor`).
  Moving the install or changing a root's real path orphans its rows, which are
  harmless and never shown.
- Schema validation (`image_favorites_schema_error`) runs at startup and on
  restore. Old backups without the table restore, and backup plus restore keeps
  WAL-resident favorites and closes every handle (`tests/test_database_backup.py`).

Budgets: `test_filter_work_is_bounded` always runs. It asserts that a plain first
page makes 1 link SELECT and a plain cursor page none, that every filter shape
makes exactly 1, the covering-index plan, and that the per-file values are built
once per scan and only for new files after a monitor invalidation. The opt-in
`test_filter_budget` (`PROMPTMANAGER_PERF_BUDGET=1`, the `perf budget` job) times
the D12 p95 < 150 ms budget; see "Performance budgets".

```sh
python -m unittest tests.test_api_workspace_images tests.test_card_seed \
  tests.test_image_favorites tests.test_library tests.test_api_library \
  tests.test_database_backup -q
```

Installing these fields needs a maintainer ComfyUI restart (Python, routes and
schema). Automated tests use disposable databases and output folders.

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
| `GET /prompt_manager/library/{positive_id}/history/pairs/{pair_id}/newest` | The pair's newest recorded run for the history run dot (#249 D26): `{run: {run_id, anchor_id, context_id}}` or `{run: null}` |
| `GET /prompt_manager/history/runs/{run_id}` | Frozen fields, per-field provenance, capture and outcome for a known UUID |
| `GET /prompt_manager/history/runs/{run_id}/outputs` | Ordered host-reported occurrences, including repeated locations |
| `GET /prompt_manager/history/runs/{run_id}/output/current/{media,thumbnail,inspection}` | Authorized current file at that reported location, using the returned epoch/scope URL |

The newest-pair read takes no query. It answers 404 when the positive is not an
eligible saved prompt or the pair is not one of its pairs. It walks the positive's exact
raw-text scope newest first and follows only a row whose `reference_epoch` is the
current epoch and whose saved-pair hint still resolves to that pair (the
`get_history_run` hint rule). It inspects at most 16 candidates, so 16 newer stale hints
report `{run: null}` (the dot then opens Repeat run). Like the other reads it runs inside
the fenced history snapshot: a restore or epoch change is 409 `history-changed`, a
storage failure 503 `storage-unavailable`.

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
distinctions are documented in the comparison helper. The Images card tag
button (#249 D9) and shared role-block
spacing/padding/glyph polish remain outside #209. The
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
