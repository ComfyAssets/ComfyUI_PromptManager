# Prompt Manager application redesign concept

Status: visual concept approved by the maintainer, 2026-10-17. Production
implementation is progressing in tested slices; see the [development plan](../../application-redesign.md).
The production workspace now has native Images browsing; this concept remains the
approved target for the complete application.

## Review the mockup

Run `python tools/preview-prompt-timeline.py --bind 0.0.0.0 --port 8767` and
open `/workspace/`. The original approved timeline remains available at `/`.
The server exposes an explicit allowlist of concept files and pinned assets.
All examples are synthetic; there are no application API calls, database access,
real model downloads, or ComfyUI handoffs. Local file inspection previews an
image in the browser without uploading it. Exports contain sample data only.

The sidebar opens Images, Prompt library, Tags & categories, Inspect a file,
Activity, System and Design review. System includes Overview, Appearance,
Sources & scans, Integrations, Auto-tagging, Maintenance, Backup & export, and
Diagnostics & logs. The in-app Design review contains the feature map below.

## Proposed direction

One application shell replaces separate Admin, Gallery and Metadata shells.
Use the approved shared ComfyAssets theme: ComfyUI dark/light, blue primary
actions, green positive roles, red negative roles and errors. Preserve existing
capabilities and services while consolidating controls and clarifying their scope.

Browsing is image-led with readable thumbnails and one metadata inspector.
Prompt library groups by positive; negative variants and runs share its gallery.
The full selected negative appears directly beneath the positive in history.
Favorites express preference independently of the existing quality rating.
Collections organize projects without duplicating recipe text; saved views keep
search/filter context. New capabilities are proposals, not implemented features.

Maintenance moves out of the browsing toolbar. Checks, impact preview, backups,
and apply steps have separate responsibilities. Distinguish prompt text reuse
from image-file duplicates, and metadata enrichment from thumbnail generation.
No repeated global thumbnail-generation prompt on opening the gallery.

## Send to ComfyUI

Use the same original paper-plane outline, blue accent and accessible role label
on every positive/negative prompt block, including image details and the viewer.
A pair action sends both. Preview destination and exact fields before applying;
positive-only and negative-only actions leave the other role untouched. An
explicitly empty negative means clear the target field; unknown metadata must
never be interpreted as empty. Sending text neither queues execution nor
replaces a workflow.

Production needs a shared host bridge with versioned payloads, origin validation,
destination discovery, capability checks, disconnected-host handling, linked-input
checks and acknowledgements. Preview actual target values and report success only
after acknowledgement. Default to asking for a destination; never guess between
multiple nodes or positive and negative encoder inputs. Verify Classic and Nodes
2.0 with both packs installed independently and together.

## Existing feature inventory

| Current feature | Proposed home / behavior | Disposition |
| --- | --- | --- |
| Admin prompt cards / per-prompt gallery | Prompt library + one prompt history workspace | Retained, rebuilt |
| Standalone image gallery / Admin image modal | One Images workspace + shared Viewer.js viewer | Consolidated |
| Video playback, autoplay / mute / loop, viewer slideshow and transforms | Shared media viewer; playback controls in Appearance; same image navigation | Retained |
| Metadata page / viewer sidebar / full-text popups | One reusable metadata inspector; inline complete prompt text | Consolidated |
| Prompt add, edit, rating, notes, copy, delete | Recipe editor, shared details, contextual bulk actions; reference-safe archive | Retained, clarified |
| Categories, tag rename / merge, autocomplete | Tags & categories; same vocabulary in every editor | Retained |
| Grid/list, thumbnail size, columns, pages/infinite scroll | Infinite scroll only; one Appearance configuration and contextual layout / density overrides | Simplified |
| Scan, rescan, monitoring directories, worker threads | Sources & scans; explicit metadata enrichment vs thumbnail work | Consolidated |
| Ollama, HuggingFace, WD14 variants, thresholds, review | One Auto-tagging configuration and review flow | Retained |
| Thumbnail quality, size limit, metadata cache, paths | Appearance + cache controls in Maintenance | Retained |
| Prompt dedup, hash checks, consistency, unused prompts | Maintenance plan with dependency checks and impact preview | Retained, safer flow |
| Image dedup, orphan image links, browser cache | Separate reviewed operations in Maintenance | Retained |
| Database backup / restore, JSON export | Backup & export; recovery and portable recipes clearly distinguished | Retained |
| Diagnostics, image-link checks, logs, rotation, log levels | System → Diagnostics & logs | Retained |
| LoRA detection/import, trigger injection, CivitAI key | System → Integrations; positive-only triggers | Retained |
| Statistics / counts | System overview + usage breakdown; context counts in the workspace | Retained |
| Send prompts to ComfyUI from every prompt surface | Role-aware paper-plane action + destination / overwrite preview | New proposal |
| Reusable negatives / approved branch timeline | Prompt history with selected negative directly beneath positive | Approved concept |
| Favorites, saved searches, project collections | Sidebar views; collections do not duplicate recipe text | New proposal |
| Ambiguous metadata review, source provenance, run comparisons | Needs review queue; recorded vs inferred evidence; pair/run selection | New / expanded |
| Unified jobs, cancellation, resumable progress visibility | Activity center; cancellation distinguished from hiding progress | New proposal |
| Scheduled backups, pre-change safety snapshots | Explicit recovery policy after storage configuration | New proposal |

## Implementation boundaries and test requirements

Keep Viewer.js 1.15.2 and the shared toolkit as reproducible pinned artifacts;
this mock uses their actual checked-in files without modifying them. Preserve
legacy routes during the transition, current node layouts/schemas, existing
controllers where reusable, and all existing test/coverage gates.

The timeline needs persisted execution records to represent real runs. Saved
views, collections, metadata review resolution, activity history, scheduled
backups, portable JSON import, and the ComfyUI bridge need separate implementation
and persistence work. Preserve source evidence and distinguish recorded from
inferred metadata. Existing schema migration is automatic; old image enrichment
remains an optional explicit scan. The one-time upgrade notice belongs in the
new shell, with Scan and Later choices.

Implement in reviewed slices: shared application shell and inspector; reusable
prompt blocks/send bridge; image workspace; grouped library/history; settings and
maintenance; optional organization and workflow improvements. Write regressions
first for production behavior. Retain Python, frontend unit, Chromium/Firefox/
WebKit/touch and managed Classic/Nodes 2.0 tests. Add behavior tests for shared
filters, navigation state, viewer cleanup, interrupted jobs, dependency-safe
maintenance, backup/restore, paired metadata and correct send destinations.

Screenshots in `screenshots/` record the reviewed starting point. The approved
standalone timeline in `../prompt-variant-timeline.html` remains unchanged.

## Review polish

History actions use a shared branch icon. Images can switch among Grid, List and
Masonry; Masonry preserves each image's proportions and keeps DOM order while
packing cards with a responsive layout. It does not require experimental native
masonry support. Resize observers are disconnected when leaving the view.

Every image card includes a tagging action beside Info, including cards in Needs
review. Choose WD14 SwinV2, WD14 ViT, Ollama or HuggingFace; adjust thresholds or
instructions, generate sample suggestions, edit tags manually, and approve only
the desired suggestions. Changes are staged until approval, apply to that image
alone and do not resolve uncertain prompt associations. Changing models discards
previous suggestions. Cancel preserves existing tags. All inference is simulated.

## Continuous browsing decision

The redesign uses infinite scroll only. Remove page numbers, previous/next page
buttons, paged/infinite mode selection and results-per-page controls. Keep request
batching internal. The mock appends 12 images at a time in Grid, List and Masonry;
it preserves existing card nodes, focus and selection while appending. It also
retains the loaded range when switching layouts or revisiting a search. A
keyboard-accessible Load more action provides an alternate trigger for the same
continuous stream, not pagination. Exhausted and empty results are explicit.

Production will need bounded rendering, stable cursors, scroll restoration and
loading/error/retry behavior for large collections. Existing backend pagination
can continue providing internal request batches; no production tests or routes
are removed as part of this prototype.
