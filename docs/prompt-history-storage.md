# Durable Prompt History

Status: the durable job ledger is implemented as the first slice of
[#209](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/209). Prompt
contexts, output associations and the chronological timeline remain pending;
#209 stays open.

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
output payloads. A bounded background writer keeps storage failures from
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

## Remaining history work

Design bounded execution contexts with nullable recipe references, immutable
raw/effective positive, negative and composition snapshots and explicit
inferred/recorded/unknown provenance. Cached nodes must not inherit unrelated
recent prompt evidence. Define deletion and restore behavior before connecting
saved recipes to historical snapshots.

Add exact output references and a deduplicated run/image relationship without
claiming cached outputs were newly created. Preserve existing image provenance
and same-path replacement protection. Only after those semantics are tested can
the timeline honestly show prompt-level execution chronology, batched outputs
and repeat-run branches.
