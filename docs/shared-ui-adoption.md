# Shared ComfyAssets UI adoption

Status: approved direction; toolkit/testing foundation in progress.
Product UI brainstorming comes before node changes.

Tracking: [PromptManager #186](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/186)
and [KikoTools #81](https://github.com/ComfyAssets/ComfyUI-KikoTools/issues/81).

KikoTools is the validated model. The
[ComfyAssets Toolkit](https://github.com/ComfyAssets/ComfyAssets-Toolkit) is the
shared source; PromptManager proves the package boundaries before a later
KikoTools adoption PR. Other custom nodes can become additional consumers.

## Approved sequence

1. Extract reusable controls and test infrastructure from the working KikoTools
   reference into the toolkit. Keep routes, node schemas and controllers local.
2. Bring PromptManager's automated tests up to parity using the pinned toolkit.
3. Brainstorm PromptManager node layouts, controls and behavior with the maintainer
   before changing its production UI. Select one bounded first consumer together.
4. Validate shared distribution, themes, cleanup and compatibility through that
   consumer, then migrate incrementally. Test both packs together as appropriate.
5. Backport toolkit adoption to KikoTools after PromptManager proves it, preserving
   the existing KikoTools appearance, behavior and workflow compatibility.

KikoTools is not being migrated again during this phase. The original
[foundation mockup](https://github.com/ComfyAssets/ComfyUI-KikoTools/pull/82)
records the approved visual direction and inspiration.

## PromptManager requirements

- Preserve existing node names, inputs/outputs, API routes, saved workflows,
  gallery behavior, and existing test/coverage gates.
- Use shared control factories, editor slots, settings/help/window behavior and
  the ComfyUI adapter where applicable. Keep PromptManager controllers and HTTP
  behavior in this repository.
- Both projects must look identical: one shared set of controls, typography,
  spacing, colors and interaction states. Follow ComfyUI light/dark mode with
  our own styling, a blue primary accent and strong red errors/destructive actions.
  Use compact buttons, pill switches, segmented selections and responsive rows.
  Review the pilot mockup before production styling. Existing `--pm-*` tokens
  are migration context, not an approved design to retain.
- Theme standalone pages explicitly: `window.open` pages do not inherit the
  host's CSS variables. Test the agreed palette policy, live changes, fallbacks
  and persistence, including errors when host settings are unavailable.
- Ship a pinned, reproducible browser distribution. Installation must work
  without npm or KikoTools, including when both packs ship different core versions.
- Attribute [Pixaroma](https://github.com/pixaroma/ComfyUI-Pixaroma) as architectural
  inspiration. Do not copy/vendor his code, styles, or assets.

## First migration

With the maintainer, inventory the PromptManager nodes and gallery dialogs and
choose one bounded first UI consumer after brainstorming. Record its current behavior with tests before changing it.
Verify keyboard opening/closing, focus restoration, accessible labels, confirm
and cancel behavior, network failures, and light/dark appearances. Keep the
existing gallery and destructive-action browser workflows passing.

Build shared-source extraction and distribution checks before importing the core.
Test both packs alone and together, scoped styling, isolated settings/storage,
and independent cleanup. ComfyUI-facing changes require the real-host Classic,
Nodes 2.0 and live-switching matrix from the foundation proposal. Standalone
dialogs additionally run through PromptManager's existing browser harness.

## Delivery

Use small issue-linked PRs and TDD: verify each new behavioral test fails before
implementing it, then run relevant suites and required CI checks. Include visual
evidence for appearance changes. Migrate additional nodes/dialogs/editors only after
the first consumer validates the package boundaries; do not rewrite all pages
or change backend schemas as part of this initiative.
