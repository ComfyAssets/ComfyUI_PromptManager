# Shared ComfyAssets UI adoption

Status: proposed; no runtime behavior changes in this document.

Tracking: [PromptManager #186](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/186)
and [KikoTools #81](https://github.com/ComfyAssets/ComfyUI-KikoTools/issues/81).

The [KikoTools foundation proposal and mockup PR](https://github.com/ComfyAssets/ComfyUI-KikoTools/pull/82)
defines the common architecture and acceptance criteria. KikoTools' Local Image
Loader is the first pilot. PromptManager adoption follows validation of that
pilot and extraction of a versioned, original ComfyAssets UI core.

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

After the pilot, inventory the gallery dialogs and choose one bounded dialog as
the second consumer. Record its current behavior with tests before changing it.
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
evidence for appearance changes. Migrate additional dialogs/editors only after
the first consumer validates the package boundaries; do not rewrite all pages
or change backend schemas as part of this initiative.
