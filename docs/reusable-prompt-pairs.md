# Reusable positive and negative prompts

Tracking: [#193](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/193).
This work builds on the merged Split node in PR #192. Keep the feature unmerged
until maintainer live testing is complete.

## Storage contract

Keep existing prompt IDs, text hashes, metadata, image links and node schemas.
A component can serve either role. Ordered combinations refer to shared prompt
rows; an explicitly empty negative uses a null reference. Missing or ambiguous
metadata does not mean an empty negative. Pair metadata belongs to the
combination; saving another combination does not overwrite component metadata.
Existing trim/lowercase hash normalization remains unchanged.

Saved components are immutable while referenced. To change text, create or reuse
a component and save a new combination. Archive combinations that have generation
history. Pruning and legacy editing/deletion must respect these dependencies.
Historical workflow snapshots remain intact, including their original text.

## Migration and recovery

Create pair tables after existing legacy rebuild migrations, in an atomic,
repeatable migration. Never scan media during schema initialization. Test fresh,
legacy and current databases, repeated startup, injected failures and retries.
Preserve old backup compatibility. Migrate and validate a disposable backup copy
before replacing live database pages. Reject broken pair schemas, foreign keys,
triggers and views. Use SQLite backup APIs so committed WAL data is included.

## Metadata and scanning

Resolve positive and negative from the same sampler/output context. Track both
node ID and output slot; bound graph traversal and text construction. Unknown
conditioning operations and conflicting sampler candidates remain unresolved.
Never guess that the first encoder or most recent negative belongs to an image.

Import high-confidence pairs atomically with their image association and source
provenance. Repeated scans must not duplicate components, pairs or image links,
regenerate thumbnails, or increment generation run counts. Preserve existing
conflicting/manual associations for review. Report new, enriched, unchanged,
ambiguous, unresolved and failed results separately.

## Live node

Use the shared toolkit and Split foundation. Positive and negative editors have
equal size. Only positive receives prepend, append and automatic LoRA triggers.
Keep upstream connections authoritative. Library selection previews both fields
before an explicit load. Saving text changes creates another reusable combination.
Verify Classic and Nodes 2.0 startup, switching both directions, reload, caching,
linked inputs, popup behavior and lifecycle cleanup before maintainer testing.

## Delivery gate

Keep all existing coverage floors and test suites. Record regression failures
before implementing each behavior. Tests use disposable state and the managed
CPU host; never mutate the maintainer's running ComfyUI or personal database.
Python changes require a ComfyUI restart. Live approval is required before merge.

## Live testing

1. Download a database backup from Admin before the first restart. The additive
   schema migration runs on startup; existing workflows and prompt IDs stay valid.
2. Restart ComfyUI, reload the browser, and add **Prompt Manager (+/-)** from
   **ComfyAssets / Prompts**. Connect CLIP and the two conditioning outputs to the
   sampler's positive and negative inputs.
3. Enter both texts, save a pair, then keep positive unchanged and change negative.
   Recent should show both combinations with distinct negative excerpts. Selecting
   one previews both; **Use pair** loads both together.
4. Test category/tag autocomplete and metadata opt-in, resizing, light/dark,
   switching Classic / Nodes 2.0 both ways, and workflow reload. Connected fields
   stay authoritative; run the node to save connected metadata values.
5. Queue the same recipe twice and change only negative once. Usage should count
   dequeued jobs even when ComfyUI caches the node. Prepend, append and LoRA triggers
   affect positive only. Gallery text is labelled as recorded during execution or
   recovered from workflow metadata.
6. Run an output metadata scan to enrich existing images. Repeat it: pairs and
   links must remain deduplicated, with unchanged results rather than new links.
   Conflicting or ambiguous files need review and are preserved.

Fresh executions embed bounded text evidence in PNG metadata. A cached node may
not emit fresh evidence, so its graph-derived text is explicitly marked inferred.
Multiple distinct batch records without an image anchor remain unresolved.
Rescanning after interruption is safe through idempotent per-file writes; this
version does not persist a filesystem scan cursor across server restarts.
The API can request cancellation with `POST /prompt_manager/scan/cancel`; the
existing dashboard Hide button continues to hide progress without cancelling.

Pair APIs are under `/prompt_manager/pairs`: POST saves a combination, GET lists
and filters, GET/PATCH/DELETE `/{id}` reads, edits metadata or removes an unused
pair. Changing text creates another combination. Null metadata updates preserve
existing values; empty strings/lists clear supported fields. Archive a pair with
`PATCH {"archived":true}`. GET `/export` provides versioned JSON containing both
texts and pair metadata; this is an export format, not a JSON import endpoint.

## Gallery redesign concept retained

The maintainer approved the interactive
[negative variant timeline concept](concepts/prompt-variant-timeline.html) on
2026-10-17. Preserve it for the later gallery redesign; it is not production UI
and does not expand the current node work.

Keep one positive parent and one shared gallery, with red branches for reusable
negative variants and smaller dots for repeat runs. Show the selected negative
in full directly beneath the unchanged positive; selecting another variant or
run updates that text and filters the shared gallery. Comparison shows both
negatives. Distinguish explicitly empty negatives from an all-variants view and
saved variants from executed runs. Production history needs persisted execution
records; the prototype's timeline uses illustrative sample data.

The Admin upgrade/scan notice remains a separate follow-up: explain automatic
schema migration and offer an optional metadata enrichment scan or Later.
