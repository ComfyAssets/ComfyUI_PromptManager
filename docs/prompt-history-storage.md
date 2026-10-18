# Durable Prompt History

Status: the durable job ledger, immutable prompt contexts and run-owned output
references are implemented for
[#209](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/209). Gallery
image associations and the chronological timeline remain pending; #209 stays open.

## Recorded jobs

New jobs observed at the ComfyUI queue's dequeue boundary receive an immutable
run identity and a host-session/queue-item identity. The public ComfyUI job UUID
is a correlation field: submitting it twice records two attempts. Rejected graphs
and jobs removed from the queue before dequeue create no execution rows. Cached
jobs are recorded even when no PromptManager node runs, and headless jobs do not
require a browser or WebSocket client.

The additive `execution_runs` table stores dequeue time, observed callback time,
optional executor event times and an explicit outcome: `observed`, `succeeded`,
`failed`, `interrupted` or `unknown`. Matching stored terminal events determine
outcomes. Missing or conflicting terminal evidence remains unknown; a process
crash can leave an observed row without an invented completion time. Bounded
safe diagnostics retain node ID/type and exception type, never graph text,
arbitrary extra data, credentials, exception messages, traceback, inputs or
arbitrary output payloads. A bounded background writer keeps storage failures from
changing host execution and reports capture gaps rather than inventing rows.

Existing databases begin with zero execution rows. Migration and backup restore
preserve prompts, pairs, image links and aggregate counters; storage epochs keep
stale queued writes from being replayed into restored data. There is no
historical backfill from counters, scan dates, image timestamps or saved recipes.

[Testing](../TESTING.md#durable-execution-ledger) describes the real CPU host
matrix, historical failure proof, privacy checks and cleanup. Python deployment
requires restarting ComfyUI.

## What the existing UI proves

`prompts.run_count` and `prompt_pairs.run_count` remain aggregate usage counters,
not completed-run counts. `last_used_at` is the latest aggregate update; legacy
migration may have estimated counts from linked images. `generated_images`
identifies linked files; image counts and insertion times are not run counts or
execution times. Image/pair association creation time can reflect an old-image
scan. A saved pair's creation time is its recipe date.

The native Prompt Library shows saved negative variants, linked images and
aggregate uses. It does not expose the ledger as chronological dots or repeat-run
branches. `PromptTracker` identities and PNG recipe evidence remain separate
from durable host job identity.

## Immutable contexts and output references

Prompt contexts retain bounded raw/effective positive, negative and composition
snapshots with nullable recipe navigation hints and explicit evidence provenance.
Unknown text remains SQL NULL; a known empty negative remains empty text. Cached
evidence belongs to its exact cache provider and attempt, rather than inheriting
unrelated recent prompts. Deletion and restore can remove navigation hints without
rewriting historical snapshots or rebinding them to current library rows.

Schema v3 adds `execution_output_capture` and `execution_run_outputs` without
backfilling older runs or modifying gallery image links. At final queue completion,
the writer attaches bounded output evidence from that attempt's final host history
to its immutable run ID. The internal `get_execution_outputs(run_id)` reader
returns state, reason, format version, source and ordered references. A complete
capture with `none_reported` means the final host history reported no images;
`not_captured`, partial, unavailable and conflicting evidence remain explicit.
Failed and interrupted jobs can still have complete evidence for images reported
before termination. Running jobs do not gain a final capture merely because an
image node finished.

Each reference records output node ID, image ordinal, folder type, subfolder,
filename, path style and an opaque configured-root key. A cached repeat can retain
the same locators under a new run. Locators do not verify file bytes, continued
existence, a newly generated file or membership in any prompt context: the reader
reports file identity as `unverified`, generation and context membership as
`unknown`, and temp outputs as transient. Capture never reads, resolves or serves
the files and adds no public route. PNG metadata is not required. Same-path file
replacement and existing gallery provenance retain their separate semantics.

## Remaining history work

Define a deduplicated run/gallery-image relationship that preserves existing image
provenance and same-path replacement protection. Add a chronological timeline only
after its navigation and evidence semantics can honestly represent prompt-level
execution chronology, batched outputs and repeat-run branches.
