# Reusable positive and negative prompts

Tracking: [#193](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/193).
This work builds on the Split node in PR #192. Keep the feature unmerged until
maintainer live testing is complete.

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
