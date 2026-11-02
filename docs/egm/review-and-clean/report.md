# Review and clean: review-only audit

Date: 2026-11-01. Baseline: `main` at `7e11d9cae266b74e0820799eb0d74ab493e66026` (version **3.4.1**).

**REVIEW ONLY — DO NOT MERGE. Keep this PR in draft.** The maintainer approved sharing this audit for additional review; this branch contains no cleanup implementation.

All measurements and source-line references describe commit `7e11d9cae266b74e0820799eb0d74ab493e66026`, not the latest main. Main subsequently gained #592 (thumbnail location) at `043403d09e0ac199123d7da88c5a6c3e33c8bc46`; that addition is outside this audit snapshot. The documentation branch starts from that newer main so its only diff is this report.

## Recommendation

Proceed with a small cleanup of proven dead helpers, test-only scaffolding and unused assets, preceded by the risk-focused tests below. Keep the legacy pages for now. Two legacy jobs still need a retirement decision: prompt sorting by use, and managing the underlying records used only as negative components. Do not build replacements as part of this audit.

The most consequential code findings are inconsistent Windows UNC handling in SQLite readers, a write-only prompt registry that grows with executions, and model unloading on the server event loop. These are independent of deleting the old pages. The first and third need narrow regression tests and fixes; removal of the registry needs an explicit compatibility decision.

The audit changed no production source, tests, workflows, protected assets or configuration. This review branch publishes only the report at the maintainer's request. No cleanup, service restart or release is included. Raw logs, scripts, databases, archives and machine-specific files remain outside Git. This is a review-only exception to the normal policy that EGM reports stay local.

## Expected payoff

These are measured projections, not completed reductions. No projected reduction has been implemented.

| Work | Before → proposed after | Reduction / limits |
| --- | --- | --- |
| Legacy UI retirement | 121,845 → **111,707 runtime lines** | **10,138 lines / 8.3%**, across 16 files. Requires parity approval; replacement bookmark shims may reduce the net saving. |
| Small dead-helper cleanup | About **150 runtime lines removed**, plus obsolete tests | Modest: the measured helper/class spans total 149 lines before adjacent fields, comments and test changes. |
| Development-file package exclusions | **17,348,582 → 6,766,947 bytes ZIP** | **61.0% smaller** in a local preview excluding `tests/`, `tools/`, `docs/` and `.github/`; 1,028 → 362 archived files. Runtime completeness still needs installation testing. |
| Unused image artifacts | **1,737,407 bytes removed** from the current tree | Three files. This does not shrink existing Git history. |

Line counts include comments and whitespace. Package savings concern the Registry packaging path, not Git clones or history. Both ZIPs were local audit previews; neither is included in this branch or published to the Registry.

The strongest return is packaging exclusions, targeted correctness fixes and eventual legacy retirement: retiring the old UI removes two large controllers and their supporting code. The first helper-removal batch has a small numerical payoff. Defer broad file splitting and cosmetic reorganization until there is a demonstrated maintenance benefit. With Python coverage at 95.10% and frontend line coverage at 90.55%, prioritize safety invariants over percentage increases.

## 1. Scope, method and limits

Read the audit brief, local `CLAUDE.md` and `AGENTS.md`, [README.md](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/README.md), [pyproject.toml](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/pyproject.toml), the redesign plan, the testing contract's execution/safety/portability/parity sections, the available directory contracts, and relevant EGM designs and drift records. The EGM records for #444, #447, #550, #523 and #556 distinguish approved redesign changes from actual gaps. `openspec/AGENTS.md` does not exist at this head; no substitute spec was invented.

The review scans the tracked tree and takes deeper passes through registration, HTTP routes, prompt/image identity, backup/restore, library movement, deletion, model lifecycle, and the legacy/workspace UI entry points. It is not a claim that every line of the roughly 122,000 runtime source lines received a separate manual review.

Raw audit evidence was retained locally and is not part of this branch. The observations, results and limitations needed for review are summarized here. Inventory uses `git ls-files`; generated environments, the copied local briefs and audit artifacts do not enter the counts. Vulture 60%/80% output and coverage were treated as leads. Candidate decisions also checked browser script tags/imports, node schemas, route registration, dynamic `getattr`, SQL/config names, shutdown hooks, packaging and tool/test callers. Names inside comments, a same-named unrelated test helper, or a same-named local JS function do not constitute a caller.

Disposable probes exercised the findings without editing production code: 1,000 unique registry registrations and forced expiry; SQLite URI construction for a non-local UNC authority; unload call/thread identity; invalid save-request shapes; and native-Library eligibility of a negative-only record. Results appear with the corresponding findings. These were audit probes, not committed regression tests or proof of a fix.

### Baseline measurements

| Measurement | Before / current audit head |
| --- | ---: |
| Tracked files | 1,028 |
| Runtime `.py`, `.js`, `.mjs`, `.css`, `.html` files outside protected directories | 295 |
| Lines in those runtime files | 121,845 |
| Test/tooling source files in the same language set | 532 |
| Test/tooling source lines | 214,122 |
| Runtime files over 800 lines | 34 |
| Runtime Python functions over 50 lines | 212 |
| Python test files | 238 |
| Frontend unit-test files | 103 |
| Tooling unit-test files | 17 |
| Page-workflow spec files | 67 |
| Real-host spec files | 14 |
| Frontend files with coverage baselines | 123 |

The runtime count includes the 1,703-line generated `workspace/concept.css` mirror; it is not a hand-editing candidate. Protected sources and the approved concept are counted separately in `inventory.json`. Line totals are physical lines, including whitespace/comments, with no claim that minified/vendor code and handwritten code have the same cost. No removal has happened, so before and after are currently identical.

Largest runtime files, in lines:

| File | Lines | Review implication |
| --- | ---: | --- |
| [py/api/images.py](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/images.py) | 4,315 | Several real seams: listing/cursors, file access, thumbnails, inspection. |
| [web/js/admin.js](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/web/js/admin.js) | 3,102 | Legacy; retire after parity, avoid an interim cosmetic rewrite. |
| [web/js/gallery.js](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/web/js/gallery.js) | 3,053 | Legacy; same. |
| [database/operations.py](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/database/operations.py) | 2,854 | Prompt/tag/link operations share a large facade. |
| [utils/prompt_pair_resolver.py](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/utils/prompt_pair_resolver.py) | 2,749 | Complex resolver with differential contracts; split only with a demonstrated seam and parity evidence. |
| [py/api/admin.py](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/admin.py) | 1,893 | Both live scan/maintenance machinery and legacy routes remain. |
| [utils/image_monitor.py](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/utils/image_monitor.py) | 1,539 | Metadata-first attribution, queue fallback and shutdown behavior are coupled. |
| [py/api/autotag_routes.py](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/autotag_routes.py) | 1,534 | Native and compatibility routes plus model/stream/job policies. |

Counts and function spans were computed from the tracked files at the audit commit. Only the summarized results are published here.

### Baseline checks

| Check | Result | Evidence |
| --- | --- | --- |
| Python 3.12.9, `python -m coverage run -m tests.run_suite -q` | **PASS: 6,104 tests, 75 skips; 335.479 s** | `python-baseline.log` |
| `coverage combine`; `coverage report --fail-under=85` | **PASS: 95.10% combined**; 38,237 statements, 12,364 branches | `python-coverage.txt`, `python-coverage.json` |
| Frontend units, Node 24.22.2 | **PASS: 1,444 tests** | Frontend baseline log |
| Viewer fixture suite in pinned image | **PASS: 18 passed, 2 skipped** | Frontend baseline log |
| Full Chromium + touch workflows and combined frontend gate | **PASS: 2,473 passed, 1 skipped (23.7 min); 123-file coverage gate passed** | `frontend-baseline.log` |
| Tooling tests with the project Python selected | **PASS: 124 tests** | `tooling-baseline.log` |
| Viewer.js, Toolkit and concept stylesheet verification | **PASS**: Viewer.js 1.15.2, Toolkit 0.2.0, stylesheet 30,447 bytes | `vendor-check.log` |
| `black --check .` | **PASS: 452 files unchanged** | `black.log` |
| `flake8 .` | **PASS** | `flake8.log` |
| Frontend source/baseline membership | **PASS: 123 files** | `frontend-baseline-structure.log` |
| Existing System parity-table validation | **PASS: 54 rows, no structural/test-reference errors** | `system-parity.json` |
| Tracked-file distribution preview | **1,028 files; 32,088,917 bytes uncompressed / 17,348,582-byte ZIP**; includes tests/tooling/docs | `distribution-preview.json`, `tracked-bundle-preview.zip` |
| Standard sdist/wheel build, from a clean tracked snapshot | **FAIL: setuptools rejects multiple top-level packages**; separate from Registry packaging | `tracked-package-build.log` |
| Native Windows, other Python versions, Firefox/WebKit, real ComfyUI and cross-pack matrix | **Not run in this audit**; retain CI/managed-host gates | See verification plan |

Combined frontend coverage is **90.55% lines/statements, 93.52% functions and 92.61% branches** (37,918 measured lines, 18,664 branches). The source/baseline membership check also passes. The table above records the final results; raw reports are not committed.

The Python measurement is on macOS. The browser run uses `mcr.microsoft.com/playwright:v1.64.1-noble`, ARM64, limited to four CPUs and four workers, under the browser-suite lock. Its image digest is `sha256:47b510dd9230bf0b9bfc9b20102241e577ea7a99334ab4c3ca19f19770e9b8ea`. It runs Chromium/touch only, not the full browser matrix. The Python environment is confined to this worktree's ignored `venv/`.

Initial setup attempts were invalid and are retained rather than hidden. A global disposable data-directory override defeated fixture isolation: the initial Python run had 19 failures/55 errors, and browser workers shared a database (`the live database changed while the seed ran`). That browser run was stopped. Removing the override restored the suites' own disposable fixtures. The first tooling attempt selected a Python without watchdog; setting `PM_TEST_PYTHON` fixed that prerequisite. An initial `.venv` also fell outside the repository's `venv/` lint/coverage exclusions; it was replaced with `venv/`. These are setup corrections, not source fixes or reruns of a failing CI head. The invalid attempts are retained in the local audit evidence separately from the corrected baseline. No watchdog limit was raised, no test weakened, and no live state was involved.

## 2. Quality findings

### F01 — HIGH: several retained SQLite readers reject Windows UNC libraries

Locations: [database/models.py:1202](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/database/models.py#L1202) and `:1310`; [database/restore_stage.py:117](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/database/restore_stage.py#L117); [database/library_move.py:321](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/database/library_move.py#L321); [utils/history_read_fence.py:57](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/utils/history_read_fence.py#L57); [py/api/backup_restore.py:189](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/backup_restore.py#L189); [py/api/admin.py:402](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/admin.py#L402).

These readers independently build a SQLite URI with `Path(...).as_uri()`. On Windows a network path becomes `file://server/share/...`; the normal SQLite build rejects that non-local URI authority. The project already implements the compatible empty-authority form in [database/health.py:98](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/database/health.py#L98) (`readonly_uri`), with portable and native-control tests at [tests/test_database_health.py:337](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/tests/test_database_health.py#L337) and `:363`.

Cost: a library may be opened by ordinary filesystem-path connections yet fail backup verification/download, staging, Move library verification, or fenced history reads. Safety-backed operations can be refused before writing. This is a recovery/availability defect, not evidence of observed data loss.

Evidence: the probe produces `file://server/share/prompts.db?mode=ro` and SQLite's `invalid uri authority: server`; the shared helper produces `file:////server/share/prompts.db?mode=ro`. **No native Windows share was exercised here.**

Proposed fix: use one low-level SQLite read-only URI helper at every retained call site, retaining each caller's timeout, `trusted_schema`, snapshot and closure policies. Start with failing URI-boundary tests, then native Windows backup/restore/history controls on a disposable share. Do not infer native success from a `PureWindowsPath` assertion. Avoid a new dependency cycle by putting the helper below models/health, or preserving an appropriate facade.

### F02 — HIGH: the prompt metadata registry retains each execution indefinitely

Locations: [utils/comfyui_integration.py:89](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/utils/comfyui_integration.py#L89), `:128`, `:178`; writers at `prompt_manager.py:263`, `prompt_manager_text.py:234`, `prompt_manager_pair.py:177`; construction in `prompt_manager_base.py:89`.

Each executed node invents a time-based ID and adds full text/metadata to both a thread-local dictionary and the process-global dictionary. Production code writes this registry but never calls its reader or cleanup method. Even explicit `cleanup_old_prompts` removes only global entries. It does not bound or clear the execution thread's dictionary.

Evidence: 1,000 unique registrations leave 1,000 entries in both stores; after forcing every global entry to expire, global count is zero while the thread-local count remains 1,000. The current 91.95% combined coverage for this file exercises lookup behavior but has no bounded-growth invariant. ComfyUI saves metadata from node inputs; the old SaveImage patch is deliberately absent (#75).

Proposed fix: determine whether the registry has a supported external consumer. If not, remove the obsolete registry and its node-side writes, preserving the tests that SaveImage is untouched and outputs/history still carry the proper text. If an external contract exists, add an explicit bounded lifecycle to both stores with deterministic long-run tests. Do not simply delete the tested reader and leave production writes accumulating. This is an **UNSURE removal candidate**, despite its write-only internal use.

### F03 — MEDIUM: Unload blocks ComfyUI's event loop

Locations: [py/api/autotag_routes.py:1403](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/autotag_routes.py#L1403)–`:1426`; [py/autotag.py:911](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/autotag.py#L911); consumer [web/js/workspace/system-tagging.mjs:53](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/web/js/workspace/system-tagging.mjs#L53).

`unload_autotag_model` takes the model gate but calls `service.unload_model()` synchronously. That operation destroys the model, runs `gc.collect()` and may empty CUDA caches. It can stall unrelated UI/API/queue work. The existing image-tag job already calls the same operation through `await model_gate.run(...)` ([py/api/image_tag_jobs.py:614](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/image_tag_jobs.py#L614)).

Evidence: the audit probe records identical event-loop and unload thread IDs. [tests/test_api_autotag.py:734](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/tests/test_api_autotag.py#L734) tests success/failure/counts, not event-loop responsiveness or cancellation while the worker is still unloading.

Proposed fix: run unload through the existing model-gate executor mechanism; hold the gate until the worker is finished even if the request is cancelled. Add an event-gated fake unload that proves an unrelated loop callback can complete while unload waits, and that another model user remains refused until release. No model download is needed to test this.

### F04 — MEDIUM: malformed save requests escape validation as server errors

Location: [py/api/prompts.py:727](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/prompts.py#L727)–`:745`.

The handler calls `.get()` and `.strip()` before validating the JSON object and field types. `[]`, `{"text":123}`, and `{"text":"fixture","category":123}` all return a 500 envelope with an AttributeError logged, instead of a bounded 400 validation refusal. The save route is still registered and must remain compatible with existing consumers.

Evidence: all three outcomes are reproduced in `probes.log`. Existing invalid-rating coverage does not cover these shapes. The 96.39% combined coverage for this file therefore does not establish its input contract.

Proposed fix: validate shape/types with the existing bounded JSON-body helper before normalization; add table-driven route tests proving 400 responses, unchanged database rows, and unchanged behavior for valid legacy payloads. This intentionally changes invalid-input responses, so it is a bug fix requiring approval, not a behavior-neutral refactor.

### F05 — MEDIUM: image API responsibilities are too interwoven for safe retirement

Location: [py/api/images.py:1455](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/images.py#L1455)–`:4315` (also the helpers/cache structures above its mixin).

One 4,315-line file combines route wiring, legacy and workspace listing, signed cursors, source scopes, file containment, thumbnails/video subprocesses, favorites, inspection and prompt lookup. Other mixins import helpers from it. Removing legacy paths without identifying those dependencies risks deleting shared workspace/node behavior.

Proposed fix: after caller-led removal, extract only useful seams—thumbnail generation, media access/containment, and workspace listing/formatting—keeping the current mixin/helper facade while callers transition. Add behavior tests for retained boundaries before moving them. Do not split to satisfy a line target or rewrite the resolver alongside it.

### F06 — MEDIUM: a second configuration writer survives only for tests

Locations: [py/config.py:81](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/config.py#L81) (`write_private_json`), `:1059` (`save_to_file`); contrast [py/config_store.py:81](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/config_store.py#L81) and `:157`.

This unused production path truncates the existing file before JSON serialization, bypasses the commit lock/revision contract, loses unknown sections, and logs failures instead of returning them. Only tests call it. Keeping it makes it easy for future code to bypass the safe `ConfigStore` writer.

Proposed fix: remove the **TEST-ONLY** save method and its private writer together, replacing fixture setup through existing disposable fixture helpers where needed. Remove their own tests only with their implementation; retain `ConfigStore` atomicity, unknown-key preservation and permission tests. Do not remove the live `load_from_file` reader or change old configuration formats.

### F07 — LOW: instructions and status documentation disagree with the implementation

Locations/evidence:

- `CLAUDE.md:30` says 3.3.x; [pyproject.toml:4](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/pyproject.toml#L4) says 3.4.1.
- `CLAUDE.md:1` and `AGENTS.md:2` refer to absent `openspec/AGENTS.md`.
- `AGENTS.md:84` still says macOS runs in portability CI; `portability.yml:40` runs Windows only. [README.md:18](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/README.md#L18) also says the Python suite runs on all three OSes, contradicting its own final testing section.
- `AGENTS.md:126` says three coverage shards/two other-browser shards; current `viewerjs-check.yml:66` and `:206` use **four** and **three**. `CLAUDE.md:158` also still says two other-browser shards. [TESTING.md](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/TESTING.md) retains some old three-/four-shard and two-/three-shard prose together.
- [docs/application-redesign.md:73](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/docs/application-redesign.md#L73) names Python 3.10–3.13 and macOS CI, and `:78` asks for PR screenshots. The actual floor is Python 3.12 and the maintainer prohibits PR image uploads.
- The redesign plan describes history under #209 as pending although the chronological Library exists; default entry points already changed under #525.
- `CLAUDE.md`'s general HTML-refresh rule needs the [TESTING.md:7561](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/TESTING.md#L7561) exception: `workspace.html` is server-cached and needs a ComfyUI restart when edited.
- Several EGM line anchors and run-counting descriptions predate the cached-runtime receipt work; they are context, not authority for deleting code.

Proposed fix: reconcile README, TESTING, redesign status and the two local briefs from current code/workflows; preserve the test parsers' required sections. Keep the managed OpenSpec block intact while recording the missing-file situation locally or recovering its authoritative source after a maintainer ruling. Do not regenerate the protected concept or add screenshots. Keep local briefs untracked.

### F08 — LOW: two automation jobs are outside the timeout contract

Locations: [.github/workflows/publish.yml:15](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/.github/workflows/publish.yml#L15), [.github/workflows/viewerjs-update.yml:21](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/.github/workflows/viewerjs-update.yml#L21); guard coverage at [tests/vendor/ci-workflow.test.mjs:279](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/tests/vendor/ci-workflow.test.mjs#L279).

These jobs declare no `timeout-minutes`. The current guard enumerates the four test/quality/portability workflows, so tooling green does not enforce the repository's broader “every CI job” rule. A stalled publish/update can hold a runner much longer than intended.

Proposed fix: first obtain the required three green-main duration measurements, then add limits and comments and extend the guard to all workflows. Do not invent durations or change a limit to hide a failure. Do not dispatch the publishing workflow for this audit.

### F09 — MEDIUM: the packaging path has no exclusions for development content

Location: [pyproject.toml:9](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/pyproject.toml#L9)–`:11`, [.github/workflows/publish.yml:4](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/.github/workflows/publish.yml#L4)–`:9`.

There is no `.comfyignore` or export-ignore manifest. The inspected [publish action](https://raw.githubusercontent.com/Comfy-Org/publish-node-action/v1/action.yml) runs `comfy node publish`; the current [upstream packager](https://raw.githubusercontent.com/Comfy-Org/comfy-cli/main/comfy_cli/file_utils.py) includes tracked files when no exclusion specification is present. On that basis, a future clean-checkout publish would include the development tree. This is an inference from the current upstream source, not inspection of an already published Registry artifact.

The local tracked-file preview confirms 1,028 files / 32,088,917 uncompressed bytes: `tests/` alone contributes 549 files / 11,739,034 bytes, `docs/` 73 / 8,727,000, and `tools/` 36 / 239,782. These costs are separate from working runtime dependencies. See `distribution-preview.json`; the ZIP is only a local audit artifact and was not uploaded.

Proposed fix: add reviewed `.comfyignore` exclusions and a non-publishing archive-content check, preserving all runtime Python/web assets, semantics JSON, required licenses and install metadata. Verify the exact supported CLI version and archive before release. No publishing command was run. The action's unpinned CLI installation also makes its future packaging behavior version-dependent.

Separately, `uv build` fails from a clean tracked snapshot because setuptools discovers multiple top-level packages. That does not establish a Registry failure; wheel support is a separate scope decision. Do not casually edit [pyproject.toml](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/pyproject.toml): **any change to that file on main matches the publish trigger**, even with the same version. No packaging metadata was edited here.

### Checks that did not establish a defect

- The reviewed dynamic SQL identifiers/orders were local constants or whitelists; no exploitable SQL injection was demonstrated. `library._ORDERS`, pair/tag sort maps and fixed history ordering are safe exceptions to the blanket `order_by_clause` prose. Do not mechanically force unrelated sort domains through one function.
- The several Python/browser resolvers are necessary ports for local, non-uploaded inspection; differential tests enforce agreement. They are not duplicate metadata inspectors to delete based on filenames.
- Prompt hashes ultimately use `utils.hashing.generate_prompt_hash`; no second live prompt-hash algorithm was found in the reviewed paths.
- The restore fingerprint deliberately does not cover every older-row tag/favorite edit; the safety backup retains them. That limitation is already recorded in TESTING and is not a newly discovered data-loss bug.
- npm installation emitted four moderate-severity advisory notices. A later **offline** audit of the correct `tools/vendor` package returned no advisory entries; that does not disprove the install notice or provide a trustworthy fresh advisory assessment. No dependency versions or vendor pins were changed. Keep this discrepancy as an unassessed tooling-dependency item, not a claim of four exploitable runtime vulnerabilities.

## 3. Dead-code candidate decisions

`DEAD` below means no reachable caller found after the entry-point checks, and removal is proposed for the implementation pass. **Nothing in this table has been removed yet.** Counts/spans are recorded in `candidate-spans.json` and the tracked-file/name/line search results in `reference-audit.json`; the vulture reports include rejected false positives as well as leads.

| Candidate | Label | Evidence and disposition |
| --- | --- | --- |
| [database/annotations.py:294](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/database/annotations.py#L294), `_current_set` | DEAD | Only the definition occurs as a symbol; the similarly named test describes a result, not a call. Current annotation paths construct their own result. Remove the six-line helper. |
| [py/api/autotag_routes.py:946](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/autotag_routes.py#L946), `_gated_runner` | DEAD | No member caller, callback registration or dynamic method selection. [tests/test_jobs.py](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/tests/test_jobs.py) defines an unrelated local helper with this name. Remove nine lines; retain `model_gate.run`. |
| [py/api/thumbnail_jobs.py:92](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/thumbnail_jobs.py#L92), `from_query` parameter | DEAD | The value is never read; one internal call supplies it. Remove the keyword/argument only, preserving body/query behavior. Do not remove `thumbnail_options`. |
| `image_meta_data.png` | DEAD | No tracked text, test fixture, page, tooling or packaging reference; not under `WEB_DIRECTORY` or a static media root. Remove unused 86,639-byte artifact. |
| `test_metadata_file.png` | DEAD | Same checks; not used by the PNG/metadata tests. Remove unused 1,616,051-byte artifact. |
| `images/pm-lora-results.png` | DEAD | Unlike the ten other `images/` assets, it is not linked by [README-legacy.md](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/README-legacy.md) or any tracked caller. Remove 34,717 bytes. |
| [py/api/__init__.py:478](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/__init__.py#L478), `_acquire_long_job`; `_LongJobGuard` and `_long_jobs` field | TEST-ONLY | Only tests/doc examples use this old lock. Production admissions use `JobRegistry`. Remove this test-only cluster and its tests/assertions. **Keep `_long_job_busy_response`**, which several live admin paths call. |
| [py/api/__init__.py:1080](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/__init__.py#L1080), `_parse_comfyui_prompt` | TEST-ONLY | Only [tests/test_api_core.py](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/tests/test_api_core.py) calls the wrapper. Remove wrapper; move any distinct parser assertions to retained `utils/file_prompt.parse_comfyui_prompt` tests rather than losing legacy-data-reader coverage. Keep the actual parser. |
| [py/config.py:1059](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/config.py#L1059), `save_to_file`, and its sole helper `write_private_json` | TEST-ONLY | Calls are confined to `test_config.py` / `test_thumbnail_settings.py`; live writers use ConfigStore. See F06. |
| [utils/hashing.py:129](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/utils/hashing.py#L129), `is_duplicate_prompt` | TEST-ONLY | Only `test_hashing.py` and its own module examples refer to it. Remove wrapper/tests/docs; retain the shared hash, text/key functions and SQL registrations. |
| [utils/validators.py:300](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/utils/validators.py#L300), `parse_tags_string` | TEST-ONLY | Only `test_validators.py` calls it. Not in `utils.__all__`; not an API/config parser. Remove with its own tests. |
| `prompt_manager_base.py:436`, `search_prompts_api`; `:440`, `get_recent_prompts_api`; `:458`, `get_gallery_status` | TEST-ONLY | Only node-contract tests call them; none is a ComfyUI `FUNCTION`, route or JS callback. Remove if no supported direct-Python consumer is claimed; keep actual `_search_prompts`, DB reads and monitor/tracker status methods. |
| [database/review_changes.py:50](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/database/review_changes.py#L50), `observer_count` | TEST-ONLY | Keep: it lets lifecycle tests prove observer unsubscribe/leak behavior without inspecting internals. Explicit testing seam, not a user feature. |
| [py/api/duplicate_files.py:110](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/duplicate_files.py#L110), `query_plan_sql` | TEST-ONLY | Keep: exposes the real query shape for structural database/performance tests. Removing it only to shrink code would weaken useful assertions. |
| [py/api/thumbnail_encode.py:229](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/thumbnail_encode.py#L229), `reset_encoders`; [utils/video_metadata.py:52](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/utils/video_metadata.py#L52), `reset_missing_notice` | TEST-ONLY | Keep: deterministic test/fixture reset seams prevent state leakage across suites. |
| [utils/execution_contexts.py:87](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/utils/execution_contexts.py#L87), `context_digest` | TEST-ONLY | Candidate for removal with its focused tests; do not remove live snapshot/content digests, persisted encodings or schema validators. Low priority until its historical API intent is checked. |
| [py/lora_utils.py:408](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/lora_utils.py#L408), `get_civitai_image_urls` | TEST-ONLY | Candidate for removal with its extraction tests; the live allow-listed download planner is separate and stays. Confirm direct-Python bridge consumers before deleting this public helper. |
| [utils/prompt_graph.py:298](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/utils/prompt_graph.py#L298), `positive_prompt_nodes` | TEST-ONLY | Keep pending an explicit public-helper decision. It is a conservative graph projection used by attribution tests, while runtime counting uses `run_prompt_nodes` / component projections. Do not delete the latter or weaken attribution assertions. |
| [utils/prompt_inspector.py:466](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/utils/prompt_inspector.py#L466), `read_inspection_metadata`; `:769`, `inspect_prompt_roles` | TEST-ONLY | **Keep**: explicitly documented compatibility projections over the current reader/resolver. The brief protects legacy-data readers; these tiny facades cost little and must not be confused with old HTML inspectors. |
| DB convenience methods including `get_prompts_by_category`, `get_top_rated_prompts`, `delete_prompts_by_category`, `get_image_paths_matching`, `list_library_images`, older context/output reads | TEST-ONLY | Keep in this pass: compatibility/data-reader or destructive-operation surfaces need a stronger contract review. The facade documentation names several; low call counts do not authorize dropping old data access. |
| `PromptModel.restore_from_file`, migrations/schema helpers, inline/document workflow readers | TEST-ONLY where applicable | Keep regardless of call count: required recovery, upgrade and old-data compatibility. `reset_schema_cache` is also a necessary test isolation seam. |
| [utils/prompt_tracker.py:440](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/utils/prompt_tracker.py#L440), `extend_prompt_timeout`'s `additional_seconds` | UNSURE | Public compatibility parameter is explicitly documented as unused; current behavior resets a timestamp. Keep the signature and do not invent new timeout semantics during cleanup. |
| Entire [utils/comfyui_integration.py](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/utils/comfyui_integration.py) registry and its node writes | UNSURE | Reachable writes, test-only reads, no automatic cleanup; F02. Resolve the external-consumer question before removal. |
| Existing HTTP routes with no workspace caller, including old search, pair, scan, restore and image endpoints | UNSURE | Public HTTP consumers and node/bridge clients may exist. Keep until each route's consumer contract is checked; do not call them dead from frontend absence. |

### Legacy-only UI inventory

All 16 files below are currently reachable from registered HTML entry points and therefore **LEGACY-ONLY**, not DEAD. Together they account for **10,138 physical lines**. This is a candidate retirement envelope, not a promised net deletion: bookmark shims and retained tests may remain.

- Pages: [web/admin.html](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/web/admin.html) (997), [web/gallery.html](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/web/gallery.html) (624), [web/metadata.html](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/web/metadata.html) (431).
- Controllers: [web/js/admin.js](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/web/js/admin.js) (3,102), `gallery.js` (3,053), `tags-page.js` (1,146).
- Modules loaded only by those pages/controllers: `data-actions.js` (51), `html-escape.js` (42), `image-helpers.js` (162), `list-state.js` (64), `prompt-list-sort.js` (64), `scan-progress.js` (121), `sse-stream.js` (154), `tag-autocomplete.js` (61), `view-router.js` (27), `theme-bridge.js` (39).

[web/comfy/prompt_manager.js](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/web/comfy/prompt_manager.js) has a same-named local `escapeHtml` method but does not load the `html-escape.js` module. Conversely, **keep** `api-client.js`, `gallery-settings.js`, `png-metadata.js`, `comfy-metadata.js`, `cache-ownership.js`, node editors, prompt-library/bridge adapters, and `workspace-entry.js`: the workspace and/or canvas still use them. Keep [web/index.html](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/web/index.html)'s compatibility redirect. Never hand-edit `web/lib/`, `web/js/shared/`, `reference/`, the concept or its generated stylesheet mirror.

Rejected static false positives include `RETURN_TYPES`, `FUNCTION`, `OUTPUT_IS_LIST`, `IS_CHANGED`, `NODE_CLASS_MAPPINGS`, decorator-registered handlers, dynamically selected health checks, SQL/schema constants imported from tests or validators, and context-manager/redirect-handler signature arguments. Unused signature arguments do not authorize breaking those interfaces.

## 4. Legacy parity and retirement gate

The approved concept and later rulings allow consolidation and some explicit removals. This table evaluates jobs, not whether every old button has a clone. “Covered” identifies an actual native destination and a declared test; it does not certify behavior on a running user host. “Blocked” means no equivalent job was demonstrated and no specific removal ruling was found. The complete System table is reproduced below.

### Browsing, editing, metadata and page lifetime

| Legacy capability / current evidence | Workspace destination | Proof / retirement status |
| --- | --- | --- |
| Dashboard recent/search list; text/category/tag filtering | Prompt library toolbar and Refine | `workspace-library.spec.mjs`: “search, category and tag filters preserve their constraints and return standalone positives”. Covered. |
| Folder filtering of prompts | Prompt library → Refine → Folder | `workspace-library-filters.spec.mjs`: “Refine offers Folder, Tags and Tag match; a folder narrows the shelf to prompts with images there”. Covered (#444). |
| Multiple tags AND/OR; untagged prompts | Library Tag match; Tags → Untagged prompts | `workspace-library-filters.spec.mjs`: “Tag match Any shows every prompt carrying one of the tags; changing only the match applies it”; vocabulary/state tests cover untagged. Covered. |
| Newest/oldest and A–Z prompt sorting | Library sort | `workspace-library-state.test.js`, `workspace-library.spec.mjs` toolbar/sort cases. Covered for these orders. |
| **Recently Used, Most Used, Z–A prompt sorting** (`prompt-list-sort.js:16`–`:22`) | **None**; native `library.mjs:39` and `library-state.mjs:13` allow only newest/name/oldest | **BLOCKER B1.** No equivalent was found. Preserve legacy. Decide whether each job is required or intentionally retired; do not implement it in this pass. Ratings sorts are a separate approved removal. |
| Add prompt; category/tags/notes editing; duplicate rejection | New prompt / Edit metadata | `workspace-library-editor.spec.mjs`: “editor modes persist through reload with selected saved negative”; editor recovery cases. Covered for eligible parents and saved-pair metadata. |
| Change existing prompt text in place | Create text revision | Same editor test proves a new positive/pair is created and old text remains. Consolidated with **different deliberate semantics**; include that difference in retirement approval rather than claiming identical mutation. Keep underlying legacy text-update API until consumer audit. |
| **Manage the actual prompt-level category/tags/notes/text of a record used only as a negative component** | **No native component-management surface demonstrated**; pair metadata belongs to the pair | **BLOCKER B2 / contract decision.** `library.py:12` and `:449` exclude pure-negative records; `workspace-library.spec.mjs:47` asserts the grouping intentionally. Probe: old component is readable, absent from native parents, native detail is null. Grouping itself is approved; retiring management of that record's own metadata needs an explicit decision. No replacement screen in this pass. |
| Prompt rating/minimum rating/Highest or Lowest Rated | No native rating controls | **Approved removal**, D153 / #447, [TESTING.md:3085](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/TESTING.md#L3085); keep stored ratings and the node/API contracts that still use them. Do not replace ratings with favorites in the data model. |
| Select prompts; bulk category and tags | Prompt library selection bar | `workspace-library-selection.spec.mjs`: “Set category writes the selected prompts on the server, reloads the shelf and clears the selection”; “Add tags adds prompt tags to the selected prompts, suggests the vocabulary and reloads the shelf”. Covered. |
| Confirm/retry prompt deletion | Library Delete… / archived shelf / Delete prompt… | `workspace-library-delete.spec.mjs`: “Delete… reviews the selection, deletes the unpaired, archives the paired and keeps the protected”; paired permanent-delete cases. Covered, with approved reference-safe archive/delete rules. |
| View a prompt's images / filmstrip | Its Library history gallery and shared viewer | `workspace-library.spec.mjs`: “history displays the selected full negative beneath its positive and filters one shared physical gallery”. Covered. |
| Tag list search; alphabetical and count sorts | Tags & categories toolbar | `workspace-vocabulary-state.test.js`, `workspace-vocabulary.spec.mjs`, #444 tag-sort cases. Covered; TAG_SORTS includes both directions and most/fewest prompts. |
| Rename/delete/merge tags and browse associated prompts | Tags & categories | `workspace-vocabulary.spec.mjs`: “rename and merge persist and collapse duplicate links”, “delete tag confirms real impact and removes only the vocabulary name”, “prompt counts equal Library Browse results”. Covered. |
| Gallery colliding filenames, source/folder/media filters, filename search | Images | `workspace-images.spec.mjs`: “native Images keeps colliding filenames distinct and inspects the selected source”; source/folder/media filter and filename-search tests. Covered. |
| Grid/list, thumbnail size, continuous browsing | Images Grid/List/Masonry, thumbnail size, append + Load more | `workspace-images.spec.mjs`: “layout changes retain selection and use one collection in Grid, List and Masonry”; “continuous loading appends without replacing cards or losing selected details”. Covered. Manual columns/page-size/paged mode have an explicit simplified/approved-removal record, not a replacement control. |
| Video playback; autoplay/mute/loop; mixed-media navigation | Shared viewer + Appearance | `workspace-system.spec.mjs`: “video defaults reach the viewer”; Images/video viewer cases; `workspace-inspect.spec.mjs`: “local video plays inline and in the viewer with real tags”. Covered. |
| Slideshow, zoom/rotate/flip/fullscreen, keyboard/touch navigation | Shared media viewer | `tests/vendor` viewer fixture suite and [tests/browser/workspace-viewer-keys.spec.mjs](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/tests/browser/workspace-viewer-keys.spec.mjs) / `workspace-viewer-mode.spec.mjs`; viewer key and teardown tests. Keep the actual viewer tests when legacy controller tests go away. |
| Metadata sidebar, full positive/negative text, settings, raw workflow, downloads | Shared current-file inspector/viewer | `full-inspector.spec.mjs`, `prompt-inspector.spec.mjs`, `workspace-history.spec.mjs`; the same file/prompt inspection contract is used throughout. Covered. |
| Local PNG chooser/drag-and-drop, workflow download, copy individual prompts | Inspect a file | `workspace-inspect.spec.mjs`: “chooser opens the shared full inspector without a second read”, “drop accepts exactly one File and never navigates”, “local inspection makes no content request or storage write”; inspector download tests. Covered; local video extends it. |
| Metadata COPY ALL summary | Shared inspector → Copy settings summary | `workspace-inspect.spec.mjs`: “Copy settings summary copies the same legacy summary in Images and Inspect”; no-clipboard fallback case. Covered (#249 L3 / D117). |
| Per-image manual/automatic tags; batch generate/review/save | Images selection/tag dialog, Needs review, System tagging | `workspace-tagging-bulk.spec.mjs`, image-tagging/review specs, and native System table below. Covered with the approved image-owned tag model. Do not delete annotation/review persistence with old UI code. |
| Add a prompt with category/tags/notes from the gallery (`gallery.js:2383`, `:2591`) | Images → New prompt / shared editor | `workspace-library-editor.spec.mjs`: “new from Images cancel preserves browse” also saves on its second open and checks navigation to the saved history; “editor modes persist through reload with selected saved negative” covers metadata persistence. Covered. The legacy form is a blank editor, not an automatic metadata import. Keep `/save` for existing consumers. |
| Protect a prompt from cleanup; deliberately unprotect it | New native parents are retained by Library state; explicit `__protected__` tags remain editable through metadata/tag controls | Maintenance and `workspace-library-delete.spec.mjs` protected-record cases; [TESTING.md:2870](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/TESTING.md#L2870) / `:2924` records how to unprotect and the intentionally stronger deletion rule. Consolidated, with the default/explicit-delete semantics included in retirement approval rather than cloning the checkbox. |
| Scan/duplicate job progress, reconnect, cancellation | Activity and shared review/job UI | `workspace-activity.spec.mjs`, System sources/maintenance tests. Covered; jobs remain session-only and lost jobs are shown honestly. |
| Old static URLs and `admin#/tags/<tags>?mode=or` bookmarks | Plain-page mappings proposed below; deep filter URLs unresolved | **Approval gate.** Current URLs intentionally remain usable under #525. Hash fragments never reach the HTTP handler, so a blanket server redirect cannot preserve tag bookmarks. |
| Bootstrap failure fallback and no-JS links | Current inline `workspace-boot-failure` links point to the legacy pages | **Retirement dependency**, not dead markup. `workspace.spec.mjs` tests module-fetch and startup-throw failures. Replace the fallback with a working reload/canvas/help path before removing its destinations. |

### System parity: complete current inventory

Source: [TESTING.md:5619](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/TESTING.md#L5619)–`:5708`. Its validator currently reports **54 rows: 48 covered, 4 kept in legacy, 2 approved removals**, with no missing declared-test references. S13 already removed many old System controls after coverage was approved. Those historical anchors do not imply the controls are still in the current HTML.

| Capability | Legacy reference (historical) | Native destination/control | Current disposition | Declared proof |
| --- | --- | --- | --- | --- |
| Image scan directories add/remove | `admin.html:377–390`; `admin.js:200,1273–1323` | S4 Sources; `sources` · button · "+ Add source" | covered | Add source validates on the server, then marks the host draft dirty; Save persists (workspace-system-sources.spec.mjs) |
| Currently monitoring list + Refresh | `admin.html:392–400`; `admin.js:1032` | S4 Sources; `sources` · button · "Refresh" | covered | watching only with a live observer; restart pending otherwise (workspace-system-sources.spec.mjs) |
| Monitoring enabled | `py/config.py:212,248` | S4 Sources; `sources` · switch · "Watch for new outputs" | covered | Watch for new outputs saves monitoring_enabled; a restart applies it (workspace-system-sources.spec.mjs) |
| Search results auto-hide `result_timeout` | `admin.html:407–412` | S3 Appearance; `appearance` · spinbutton · "Node search auto-hide (seconds)" | covered | host fields round-trip through /settings with revision (workspace-system.spec.mjs) |
| Web UI display mode | `admin.html:414–422` | S3 Appearance; `appearance` · combobox · "Open from ComfyUI" | covered | host fields round-trip through /settings with revision (workspace-system.spec.mjs) |
| Infinite scrolling default | `admin.html:424–430`; `admin.js:689–727` | Legacy prompt list (README continuous browsing); read-only in the report; — | kept in legacy | shouldLoadMore asks for the next page only near the bottom with pages left (list-state.test.js) |
| Worker threads | `admin.html:437–445` | S4 Sources; `sources` · slider · "Worker threads" | covered | worker range max is cpu_count (workspace-system-sources.spec.mjs) |
| LoraManager badge, enable, path, trigger words, CivitAI key, clear key | `admin.html:448–490`; `admin.js:1052–1156` | S10 Integrations; `integrations` · switch · "LoraManager integration" | covered | LoRA rows show detected / enabled / cache-loaded separately (workspace-system-integrations.spec.mjs) |
| LoRA data import with progress | `admin.html:492–498,517–541`; `admin.js:1157` | S10 Integrations; `integrations` · button · "Review import" | covered | Review import shows replacement, blocked items and network effects with the backup box; Start opens the job (D107) (workspace-system-integrations.spec.mjs) |
| Statistics | `admin.html:159`; `admin.js:1627` | S2 Overview; `overview` · button · "Explore" | covered | statistics dialog lists runs by category and database record counts (workspace-system.spec.mjs) |
| Export components JSON `/export` | `admin.html:163`; `admin.js:1607`; [py/api/prompts.py:238](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/prompts.py#L238) | S9 Backup & export; `backups` · button · "Export all prompts JSON" | covered | Export JSON downloads /library/export; secondary buttons download all prompts (/export) and /pairs/export (D103) (workspace-system-backups.spec.mjs) |
| Saved pairs export | [py/api/pairs.py:84](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/pairs.py#L84) | S9 Backup & export; `backups` · button · "Export saved pairs JSON" | covered | Export JSON downloads /library/export; secondary buttons download all prompts (/export) and /pairs/export (D103) (workspace-system-backups.spec.mjs) |
| Library export | [py/api/library.py:223](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/library.py#L223) | S9 Backup & export; `backups` · button · "Export JSON" | covered | Export JSON downloads /library/export; secondary buttons download all prompts (/export) and /pairs/export (D103) (workspace-system-backups.spec.mjs) |
| Diagnostics run | `admin.html:171,659`; `admin.js:1984` | S12 Diagnostics; `diagnostics` · button · "Run checks" | covered | Run checks opens the shared Health report (workspace-system-diagnostics.spec.mjs) |
| Synthetic image-link test | `admin.html:662`; `admin.js:2101` | S12 Diagnostics; `diagnostics` · button · "Create test link…" | covered | Create test link confirms first and names the prompt (workspace-system-diagnostics.spec.mjs) |
| Maintenance cleanup_duplicates, vacuum, cleanup_orphaned_images, prune_orphaned_prompts | `admin.html:692–748`; `admin.js:1834` | S6/S7 Maintenance; `maintenance` · button · "Review plan" · in "Duplicate prompt text" | covered | Apply reviewed operation closes, toasts, opens the job in Activity (workspace-system-maintenance.spec.mjs) |
| Maintenance check_hash_duplicates, check_consistency | `admin.html:692–748`; `admin.js:1834` | S2 Health report (Maintenance notice, D123); `maintenance` · button · "Run health checks" | covered | Run health checks opens Health report and updates metric 4 (workspace-system.spec.mjs) |
| Maintenance statistics (database info) | `admin.html:692–748`; `admin.js:1834` | S12 Diagnostics; `diagnostics` · term · "Database" | covered | meta-grid shows real database and source status, the pinned Viewer.js version and the host renderers (workspace-system-diagnostics.spec.mjs) |
| Maintenance select all / clear all / run | `admin.html:754–762`; `admin.js:1822–1833` | S7 per-operation review; `maintenance` · button · "Review plan" | covered | plan dialog shows the server's scope, protected, backup policy and media lines with the backup box (D100); Cancel writes nothing (workspace-system-maintenance.spec.mjs) |
| Prompt cleanup `/cleanup` | [py/api/admin.py:620](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/admin.py#L620) | S6/S7 Maintenance; `maintenance` · button · "Review plan" · in "Duplicate prompt text" | covered | test_duplicate_prompts_plan_counts_the_legacy_groups (test_maintenance_plans.py) |
| Database backup | `admin.html:179,824`; `admin.js:2139,2454` | S9 Backup & export; `backups` · button · "Back up" | covered | Back up downloads through the api client and revokes the object URL (D103) (workspace-system-backups.spec.mjs) |
| Restore from file | `admin.html:183,616–642`; `admin.js:2177–2248` | S8/S9 Backup & export; `backups` · button · "Review restore…" | covered | Review restore stages the chosen file and shows the real summary with the backup box (D105) (workspace-system-backups.spec.mjs) |
| Metadata scan with progress, cancel, pill | `admin.html:187,805–865`; `admin.js:2336–2500` | S4 Sources; `sources` · button · "Review scan" | covered | Review scan opens the shared Review metadata scan with all sources (workspace-system-sources.spec.mjs) |
| Rescan-required notice + dismiss | `admin.html:1367–1410`; `admin.js:991–1011` | S4 Sources; `sources` · button · "Later" | covered | notice Later persists the 3.2.0 dismissal (pm_update_notice_dismissed) without scanning (workspace-system-sources.spec.mjs) |
| View logs with level and limit | `admin.html:195,885–907,974`; `admin.js:2579–2641` | S12 Logs; `diagnostics` · combobox · "Level" | covered | level filter is minimum-level over all six choices (workspace-system-diagnostics.spec.mjs) |
| Log statistics | `admin.html:921–941`; `admin.js:2512` | S12 Logs; `diagnostics` · status · "Log statistics" | covered | log statistics show entries, errors, warnings, files and size (workspace-system-diagnostics.spec.mjs) |
| Log files list and download | `admin.html:910,982–985`; `admin.js:2545,2642,2679` | S12 Logs; `diagnostics` · button · "Download log" | covered | each listed file downloads by its returned name and a link has no Download (workspace-system-diagnostics.spec.mjs) |
| Clear/truncate logs | `admin.html:915`; `admin.js:2704` | S12 Logs; `diagnostics` · button · "Clear all logs…" | covered | Clear all logs states both counts, confirms first and reads everything again (workspace-system-diagnostics.spec.mjs) |
| Rotated-log cleanup (new, #329) | — | S12 Logs; `diagnostics` · button · "Review log cleanup" | covered | Review log cleanup lists only rotated copies and keeps the rest (workspace-system-diagnostics.spec.mjs) |
| Set log level (incl. CRITICAL), console logging | `admin.html:948–966`; `admin.js:2530,2729` | S12 Logs; `diagnostics` · combobox · "Runtime log level" | covered | runtime level (including CRITICAL) and console logging save and say they apply until the server restarts (workspace-system-diagnostics.spec.mjs) |
| Log file_logging / max_file_size / backup_count / buffer_size | `py/api/logging_routes.py:56,285–299` | S12 Logs (read-only); `diagnostics` · group · "Log files & retention" | covered | each listed file downloads by its returned name and a link has no Download (workspace-system-diagnostics.spec.mjs) |
| Auto-tag model list with status | `admin.html:1095–1148`; `admin.js:3641` | S11 Auto-tagging; `tagging` · combobox · "Tagging model" | covered | model options are the four real engines with size and the description shows live status (workspace-system-tagging.spec.mjs) |
| Auto-tag model download | `admin.html:1413–1438`; `admin.js:3680–3747`; `gallery.js:3412` | S11 Auto-tagging; `tagging` · button · "Download" | covered | Download starts the job and Activity shows it without Cancel (workspace-system-tagging.spec.mjs) |
| Unload model | `gallery.js:3480` | S11 Auto-tagging; `tagging` · button · "Unload" | covered | Unload is enabled only while a model is loaded, and a held gate's refusal names the operation (workspace-system-tagging.spec.mjs) |
| Tag template, WD14 thresholds, already-tagged mode | `admin.html:1150–1198`; `admin.js:3748` | S11 Auto-tagging; `tagging` · combobox · "Existing tags" | covered | defaults save through Save preferences and the Tag image dialog uses them (workspace-system-tagging.spec.mjs) |
| Batch auto-tag output images | `admin.html:191,1210,1279–1318`; `admin.js:3748–3840` | S11 Auto-tagging; `tagging` · button · "Review" | covered | Tag output images reviews the images, tags each one and never a prompt (#379) (workspace-system-tagging.spec.mjs) |
| Batch review loop | `admin.html:1206,1218–1364`; `admin.js:3841–4172` | S11 + image-tagging plan; `tagging` · button · "Preview tagging review" | covered | Preview tagging review opens the Tag image dialog for the selected image (workspace-system-tagging.spec.mjs) |
| Gallery per-image auto-tag / review | `gallery.html:601,605`; `gallery.js:3506,3674–3753` | Image-tagging plan (selection bar); — | covered | Auto-tag selected with Apply automatically runs a job with the chosen model, thresholds and suggestion count (workspace-tagging-bulk.spec.mjs) |
| Gallery thumbnail generation | `gallery.js:1852,1967` | S5/S7; `sources` · button · "Review options" · in "Thumbnail generation" | covered | Start closes the dialog, toasts, and opens the job in Activity (workspace-system-sources.spec.mjs) |
| Clear server thumbnails | `gallery.js:2175–2192` | S7 Maintenance; `maintenance` · button · "Review options" · in "Thumbnail cache" | covered | Thumbnail cache opens the S5 dialog (workspace-system-maintenance.spec.mjs) |
| Browser cache clear | `gallery.js:2194–2207` | S7 Maintenance; `maintenance` · button · "Review plan" · in "Metadata cache" | covered | Metadata cache reviews and clears only owned browser entries when there are some (D102) (workspace-system-maintenance.spec.mjs) |
| Refresh file listing | `gallery.js:2209` | S4 Sources; `sources` · button · "Refresh" · in "Refresh files" | covered | Refresh files calls /gallery/rescan and toasts its counts (workspace-system-sources.spec.mjs) |
| Duplicate image scan, review, delete | `gallery.js:2234–2577` | S6/S7 Maintenance; `maintenance` · button · "Find duplicates" | covered | Find duplicates starts a job; its view links Review groups; Keep radios build a duplicate_files plan (D101) (workspace-system-maintenance.spec.mjs) |
| Default view mode, image quality | `gallery-settings.js:26–27` | S3 Appearance; `appearance` · combobox · "Default layout" | covered | defaults apply to a new visit but not to the current view or a saved view (workspace-system.spec.mjs) |
| Auto-load metadata, cache metadata, show file paths | `gallery-settings.js:31–33` | S3 Appearance; `appearance` · switch · "Load metadata on selection" | covered | metadata off shows an explicit load and loads once on request (workspace-system.spec.mjs) |
| Video autoplay, mute, loop | `gallery-settings.js:40–42` | S3 Appearance; `appearance` · switch · "Autoplay videos" | covered | video defaults reach the viewer (workspace-system.spec.mjs) |
| Image info on cards; sidebar collapsed | `gallery-settings.js:30,34` | S3 Appearance; `appearance` · switch · "Show image details on cards" | covered | added rows reach their consumers (workspace-system.spec.mjs) |
| Reset gallery settings | `gallery.js:1813` | S3 Appearance; `appearance` · button · "Reset" | covered | reset restores defaults without touching other keys (workspace-system.spec.mjs) |
| Lazy loading | `gallery-settings.js:25` | Legacy gallery (native always lazy); — | kept in legacy | a legacy gallery Save keeps the workspace keys and every other stored key (gallery-settings.test.js) |
| Results per page / grid columns / paged mode | `gallery-settings.js:28–29,35` | README continuous browsing; — | approved removal | numeric strings from <select> values are accepted and columns are clamped (gallery-settings.test.js) |
| Startup thumbnail prompt | `gallery-settings.js:38–39`; `gallery.js:2578` | README; Overview thumbnail row; — | approved removal | thumbnail row reflects the status endpoint (workspace-system.spec.mjs) |
| Client debug output `debugMode` | `gallery-settings.js:36` | Legacy gallery (S12-10); — | kept in legacy | a legacy gallery Save keeps the workspace keys and every other stored key (gallery-settings.test.js) |
| API timeout `apiTimeout` | `gallery-settings.js:37` | Legacy gallery (S12-10; read by no request); — | kept in legacy | defaults are a complete, sane settings object (gallery-settings.test.js) |
| config.json-only keys | `py/config.py:212–234,722–739`; [py/api/diagnostics_report.py:185](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/py/api/diagnostics_report.py#L185) | S12 report (read-only); `diagnostics` · button · "Download diagnostic report" | covered | test_report_lists_config_only_keys (test_api_diagnostics_report.py) |

The four kept-in-legacy settings are presentation/debug preferences, not four missing maintenance tools: legacy infinite-scroll default, lazy-loading toggle, debug output and an API-timeout value read by no request. The native app always uses continuous browsing/lazy loading. Include disposition of these old controls/keys in the retirement approval; keep existing stored preferences readable and preserve unrelated keys. Do not invent new native settings to reproduce inert controls.

### Bookmark proposal (not approved)

1. Keep the old HTTP entry points long term, even after their HTML/controllers retire.
2. Plain `/admin` → workspace Library; plain `/gallery` → Images; `/gallery.html` → Inspect. Preserve any reverse-proxy mount prefix.
3. Serve a tiny `/admin` compatibility shim for fragments, because the server cannot see `#/tags`. Bare `#/tags` can map to workspace Tags. Selected-tag AND/OR bookmarks require a separately approved mapping or explicit retirement of their filter semantics; until that decision, keep their legacy path functional.
4. Do not drop unknown query/hash state silently or redirect a tag bookmark to unfiltered Images. Add tests for direct visits, proxy prefixes, Back navigation and fragment handling.
5. Replace/remove every old link, including the static boot-failure/noscript fallback and README links, before deleting page files. `workspace.html` edits require a host restart because its response is cached.

### Backend retirement boundary

Retirement is not permission to delete every route old controllers called. The workspace still uses `/export`, backup/restore, prompt/pair/tag operations, settings, scans, thumbnails, logs, shared file serving and the model routes. The node library and bridge also call the HTTP surface independently. Keep schemas, migrations, restore/old-data readers, public node names/input order and root node modules. Review each apparently unused route as a separate contract decision after the frontend is gone.

## 5. Coverage work, ranked by risk

The aggregate gate is already strong. The useful additions are the following behavioral invariants, not a general percentage chase.

| Priority | Retained boundary / evidence | Test work before refactoring |
| --- | --- | --- |
| 1 | UNC-backed backup, restore, Move library, history fence (F01) | Assert each reader uses the valid URI; then native Windows share round trips, old-schema restore, handle closure and rollback/no-write-on-failure. `history_read_fence.py` is 98.39% covered yet misses this whole platform contract. |
| 1 | Backup/restore cancellation/adoption and library-move copy failures | `backup_restore.py` is 88.19% combined (78.79% branches); relevant gaps include `:506`, `:558`–`:563`, `:576`–`:577`, `:956`–`:970`. Exercise a cancelled worker that fails, post-publish cleanup failures that must not report an unperformed restore, and stage/handle retirement. In `library_move.py:517`–`:519` and `:538`–`:539`, fail a real copy operation and prove source/old library remain usable and only owned temps are cleaned. |
| 1 | Delete/trash and removed-image recovery | Keep original bytes and reference-protection assertions; prove file/stream release before cleanup on Windows. The local macOS run measures [utils/trash.py](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/utils/trash.py) at 29.15% because Linux/BSD branches are skipped; do not treat that as missing Linux tests or lower a floor. Preserve `test_trash_freedesktop.py`, mount-root and route-audit coverage. This audit did not mount a filesystem or exercise native Windows locking. |
| 2 | Prompt/image linking, cached run counts and metadata | Retain conservative ambiguous/unlinked results, FIFO cleanup, saved negative roles, document/inline-reader parity and Python/browser differential tests. Add a bounded-growth test before changing F02; do not replace the registry-reader tests without proving saved image metadata, run counting and history remain correct. |
| 3 | API shape/type/oversize boundaries | F04's three cases must fail before the fix, then return 400 and preserve storage. Retain envelope/error-redaction tests; audit the other `.json()`-then-normalize handlers using the same case matrix. |
| 3 | Model unload responsiveness and cancellation | Event-gated worker tests for F03; success/failure-only fake service tests are insufficient. Keep model-gate refusal/release tests. |
| 4 | Shared code whose legacy tests would disappear | Before deleting legacy specs, move distinct shared PNG/Comfy metadata parsing, API-client/proxy-prefix behavior, gallerySettings unknown-key preservation, Viewer lifecycle, backup/restore and data-safety assertions into native/shared tests. Remove tests only for code actually removed. |

Existing frontend floors are only 47% lines for admin, 54% for gallery and 57% for tags-page; these are legacy-specific baselines, not permission to give extracted new modules low coverage. New files need at least 80% lines and all retained per-file floors remain. Removing a measured file must remove its obsolete baseline entry and pass `coverage:baseline-check`; retained shared modules still enter combined coverage.

## 6. Repository organization and distribution

- Remove the three unreferenced image artifacts listed as DEAD: **1,737,407 bytes** in the current tree. Deleting them does not shrink old Git history, and no history rewrite is proposed.
- Keep [README-legacy.md](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/README-legacy.md): README links it for the still-shipped old UI and the upgrade/rollback history, and TESTING links its database-path compatibility section. Do not remove old-data/recovery instructions just because the old controls retire. Later split the useful historical upgrade notes from page-only instructions, updating every link.
- Ten of the eleven `images/` files are live documentation assets. Keep them while the legacy README points to them; do not move them for cosmetic root cleanup. `assets/readme/` is the current README's live image set.
- Keep root node files exactly where ComfyUI imports them. Do not introduce a `src/` migration or rename a node/input during cleanup.
- `docs/` is broadly ignored, including the audit and local plans. Existing tracked concept/design/docs remain tracked; new required tests/spec references in that tree must be explicitly accounted for. Local `EGM.md`, CLAUDE and AGENTS stay excluded. No needed currently tracked runtime file was found hidden by an ignore rule.
- TESTING is 7,562 lines / 585,548 bytes. It combines execution commands, a safety contract, machine-parsed parity tables and a large decision/deviation ledger. A later organization commit can split reference material by feature while leaving a short testing entry point, stable links and parser inputs. Update `legacy-coverage.mjs` and its meaningful tests in the same change if tables move. Do not blindly move headings the guard parses.
- Defer large facade splits until actual removals show the durable seams. [database/operations.py](https://github.com/ComfyAssets/ComfyUI_PromptManager/blob/7e11d9cae266b74e0820799eb0d74ab493e66026/database/operations.py) can eventually keep the public facade while delegating prompt search, vocabulary and image links. Avoid copying database logic into the API or creating divergent Toolkit controls.
- The local tracked-bundle preview includes tests, tooling, docs and screenshots (F09); upstream packaging-source inspection explains why the absence of `.comfyignore` matters. Add a non-publishing artifact-content check and exclusions for the next approved release. No standard Python artifact was produced because the clean-snapshot build failed. Do not make an opportunistic version/build-metadata change or upload the local audit ZIPs.

## 7. Proposed PR order — approval required

These are proposed future implementation batches, not work included in this review-only PR. Use issue-linked implementation branches once the maintainer supplies an existing issue or approves creating/updating one. No issue will be posted as an incidental audit action. This review branch uses #334 as the related cutover context and does not close it or any other issue. Each PR needs exact-head checks, bot-review handling, Windows impact/evidence and a `Manual check:` line. Keep required checks/coverage thresholds unchanged.

| Order | Proposed scope and logical commits | Validation / manual tier |
| --- | --- | --- |
| 1 — tests and small dead-code cleanup | About 4–6 commits: (1) add distinct retained data-safety coverage that does not just mirror implementation; (2) remove `_current_set`, `_gated_runner` and the unused keyword; (3) remove the old lock/scaffolding and safe test-only wrappers with their own tests; (4) remove the unused config writer while preserving ConfigStore behavior; (5) remove the three unreferenced assets; (6) reconcile root docs/local briefs. Defer public/compatibility helpers marked keep/UNSURE. | Targeted Python/Node tests, full Python suite and both coverage gates, baseline check, vendor/tooling checks, black/flake8 and pre-push. Legacy pages stay. **Manual check: none** for the behavior-neutral portion. |
| 2 — narrow recovery/lifecycle fixes, before large refactors | About 4–6 commits, tests before each fix: UNC-safe SQLite reads and native controls; off-loop model unload/cancellation; invalid save-body refusals. These are explicit bug fixes, not a claim of byte-for-byte unchanged failure behavior. The registry only joins if its external-contract question is resolved and host tests prove removal safe. | Native Windows run is mandatory for UNC/handles. Managed Classic/Nodes 2.0 execution/reload tests for node-side changes. **Manual check: quick** for model unload/host behavior; upgrade to **required** if the real dependency behavior cannot be proved with the managed test host, and then keep that work out of a batch until tested. Python changes need a maintainer restart later. |
| 3 — maintainability/organization only if still useful | About 3–5 commits: tests protecting the chosen seam; extract retained image/thumbnail/media helpers behind stable facades; remove genuinely redundant private helpers; split testing reference material with stable links/guards; define an artifact-content check without publishing. Avoid moving root node modules or touching pyproject/vendor/concept pins. Do not refactor legacy controllers destined for deletion. | Retained behavior/persistence/failure/cleanup tests and coverage on every new source file (80%+ for JS). **Manual check: none** unless host-facing behavior is involved; then isolate/validate it. |
| Separate retirement stage — blocked | Only after parity-table approval and B1/B2/bookmark/fallback decisions. Small commits: links/compatibility entry points; old pages; controllers and proven legacy-only helper modules; application-owned obsolete CSS if any; route/query review last. No protected CSS/vendor edits. Keep database recovery/compatibility code. | Native workspace/shared tests replace distinct legacy coverage first; remove only obsolete tests, rerun baseline check and full frontend gate, and use the CI browser/real-host matrix. **Manual check: required**: targeted maintainer upgrade, bookmarked URL and real-host check. This stage is its own PR(s) and cannot join an untested cleanup batch. |

If multiple approved PRs are ready together, follow the brief's combined-branch/one-commit-per-PR queue policy. Do not consume additional queue runs merely to parallelize cosmetic cleanup. Do not push a fix while the preceding head's CI still runs. No merging, publication or running-install modification is part of the current authorization.

## 8. Open decisions and unresolved evidence

1. **The maintainer approved sharing the audit for further review. Review the proposed implementation batches before starting cleanup.** Public compatibility helpers marked keep/UNSURE and all legacy pages remain unless separately approved.
2. **B1:** preserve Recently Used / Most Used / reverse-alphabetical prompt browsing via separate work, or explicitly retire those jobs. Recommendation: keep the legacy page until a ruling; do not quietly remove them.
3. **B2:** decide what users should do with prompt-level metadata on pure-negative component records. Recommendation: retain existing management access until that data contract is settled. Positive-parent grouping and pair metadata are already intentional; they do not themselves answer this question.
4. **Registry:** identify any supported external consumer. Recommendation: remove the write-only registry only if there is none; otherwise bound both stores. Its public-looking method names and regression assertions merit an explicit decision.
5. Approve bookmark mappings and the bootstrap-failure fallback before retirement. Deep tag-filter fragments cannot be preserved by a plain server redirect.
6. Standard wheel support and the exact previously published Registry archive remain unverified. The local preview establishes development-file inclusion for the current tracked-file packaging path; `.comfyignore` and an artifact guard are proposed for approval, with no release or pyproject change. npm's initial notices and later offline result do not establish a current vulnerability verdict.
7. The audit has no native Windows run, full Firefox/WebKit run, managed real-host matrix, live user test or fresh exact-head CI observation. Those are implementation/retirement verification gates, not results claimed here. No Windows claim is based solely on the macOS pass or the URI probe.

The next action is additional review of this report. **Keep this PR in draft; do not mark ready, enqueue or merge it. Implementation belongs in separately approved branches.**
