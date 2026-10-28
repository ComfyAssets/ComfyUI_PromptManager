# Application Redesign Development Plan

The maintainer approved the [interactive concept](concepts/workspace/README.md)
on 2026-10-17. It is a design reference with simulated operations, not production
code. This plan proposes implementation order and acceptance gates.

## Delivery status

- Foundation merged in [#198](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/198).
- [#200](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/200) delivers native Images browsing, physical-file details, infinite scroll, Grid/List/Masonry, Viewer.js and video playback. The workspace opens on Images; original application entry points remain available.
- Shared role-aware sending and the physical-file prompt inspector merged in [#203](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/203) and [#206](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/206).
- The native Prompt Library merged in [#210](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/210) for [#208](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/208): one positive parent, saved negative variants, full selected text and a shared filtered image gallery. It does not invent chronological runs from counters or scan dates.
- [Durable history storage](prompt-history-storage.md) records job attempts and outcomes from [#212](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/212), frozen prompt contexts from [#213](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/213), and observed run-owned output locators from [#214](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/214). Those locators do not verify file bytes or establish gallery-image associations. That association contract and the chronological execution timeline remain pending under [#209](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/209).
- [#215](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/215) brings the shell, Images and natural-height Prompt Library presentation into the approved geometry and themes while retaining their existing operations. [#216](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/216) adds the shared current-file inspector with exact generation facts, scoped provenance, raw/download evidence and loaded-collection viewer controls, including a slideshow that pauses at native video. This delivery does not establish whole-application parity, change the default entry point or constitute a release. Recorded chronological run/context browsing continues under [#209](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/209); image tagging/review, System consolidation and remaining organization capabilities continue under #196.
- Local Inspect (#249, PRs L1–L3) replaces the `#/inspect` handoff with the concept's native "Inspect a file" page: one local image or video previews inline and opens the shared viewer and full inspector on a record read entirely in the browser, with nothing uploaded, imported or stored. The legacy metadata page (`web/metadata.html`, served at `/prompt_manager/gallery.html`) stays reachable from Overview; its capabilities map to the shared inspector, including its one-click COPY ALL summary, which is the shared inspector's "Copy settings summary" (D117, LI-4). PR L3 ([#286](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/286)) shipped that button and local roles equal to the server's resolver (`web/js/workspace/local-resolver.mjs`, TESTING.md "Local roles (#249 PR L3, decision LI-1)"). Recorded deviations D60–D66 and D117 are in [TESTING.md](../TESTING.md).

## Scope and compatibility

Rebuild the standalone application around one shared shell. Preserve existing
node schemas, saved workflows, backend capabilities, pinned Viewer.js and the
ComfyAssets toolkit. Keep existing application entry points usable during the
transition. Do not change the running maintainer instance during automated tests.

Use the concept's feature inventory as a parity checklist. Each row needs an
implementation issue, observable acceptance criteria and automated test coverage.
Record any intentional behavior change explicitly; no silent feature removal.

## Proposed delivery sequence

Use a tracking issue and small issue-linked branches and PRs. Dependencies may
require splitting these milestones further.

| Milestone | Deliverable and acceptance focus |
| --- | --- |
| 1. Foundation | Production shell, routing, shared theme and disposable page-test harness. Verify startup, navigation, dark/light changes, cleanup and existing entry points. Establish discovered-test and coverage baselines before changes. |
| 2. Image workspace | Real API-backed filters, Grid/List/Masonry, readable thumbnails, selection and shared Viewer.js inspector. Continuous infinite scroll only, with accessible Load more fallback, stable ordering, retry and restored browsing context. Bound retained DOM and test large datasets. |
| 3. Prompt library and history | Real positive/negative associations, selected full negative, run history and shared image filtering. Design any missing execution-history persistence and migration before wiring the timeline; never invent historical runs from incomplete metadata. |
| 4. Send to ComfyUI | Shared paper-plane actions wherever prompt text appears; role-specific and pair sends, destination preview, acknowledgement and failure recovery. Cover explicit empty versus unknown negatives and connected inputs. Never queue or replace a workflow. |
| 5. Editing and review | Prompt editing, vocabulary management, per-image manual/auto-tag approval and Needs review. Preserve cancellation semantics and distinguish tag approval from prompt-association decisions. |
| 6. System and maintenance | Settings, sources/scans, integrations, jobs, diagnostics, backup/restore and maintenance impact previews. Cover existing capabilities before retiring their old controls. |
| 7. Additional organization | Favorites independent of ratings, collections, saved views, Activity, scheduled backups and portable import previews. Specify persistence, validation and recovery separately for each capability. |
| 8. Cutover | Complete the parity checklist, migration and upgrade validation, accessibility/performance checks, documentation and targeted maintainer live testing. Switch the default only after these pass; preserve compatible URLs. |

## TDD and test layers

For each behavior, first write and run a meaningful failing test. Implement the
smallest working change, verify it passes, then refactor with the suite green.
Include failing-before/passing-after evidence in the PR.

- **Unit:** filtering and selection state, cursor handling, prompt roles,
  formatting, validation, navigation and lifecycle cleanup.
- **Integration:** real HTTP handlers and disposable SQLite/filesystem fixtures;
  persistence, migration from supported old schemas, idempotent scans, restore,
  rollback and reference-safe deletion. Mock inference and external services.
- **Smoke:** shipped production pages start and navigate with real fixture APIs;
  test populated, empty and error states, assets and unexpected console failures.
- **Browser workflows:** production HTML/JS across Chromium, Firefox, WebKit and
  touch; editing/reload, infinite append, viewer cleanup, tagging approval,
  keyboard access, delayed responses, cancellation and retries.
- **Host integration:** pinned disposable CPU ComfyUI, Classic and Nodes 2.0,
  switching both ways and workflow reload for host-facing changes. Exercise
  independent and combined pack installations for shared bridge/toolkit changes.

Retain existing test discovery and coverage gates. Carry forward database
injection and open-handle checks into new fixtures. Add new frontend modules to
coverage measurement and its baseline; do not lower floors or replace existing
regression coverage with mockup-only tests.

## GitHub and release gates

Extend existing required workflows: the Python 3.10–3.13 matrix on Linux,
Windows and macOS; 85% Python coverage; formatting; frontend units and per-file
coverage floors; vendor checks; browser workflows; real ComfyUI compatibility.
Publish traces, screenshots, logs and coverage artifacts on failures. See
[TESTING.md](../TESTING.md) for commands and fixture isolation requirements.

Every PR must map acceptance criteria to tests, report relevant checks and include
screenshots for visual changes. All required checks must pass before merging.
Use maintainer live review at meaningful visual and host-integration milestones;
capture discovered regressions in automated tests. Notify the maintainer when
Python changes require a ComfyUI restart.

Keep version/publication changes separate from intermediate UI work: the existing
registry workflow publishes on main/master changes to `pyproject.toml` or manual
dispatch. Do not trigger a release merely to preview a redesign milestone.
