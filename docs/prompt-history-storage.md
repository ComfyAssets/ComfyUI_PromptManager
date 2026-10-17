# Durable Prompt History

Status: future proposal tracked in [#209](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/209); the native library does not migrate execution storage.

## What the current database proves

- `prompts.run_count` and `prompt_pairs.run_count` are aggregate usage counters.
  `utils/usage_tracking.py` counts known recipes when ComfyUI dequeues a job,
  including cached node executions. Dequeue does not establish successful completion.
- `last_used_at` records the latest aggregate update. The legacy prompt migration
  estimated usage from linked images, with a minimum of one; no flag distinguishes
  those estimates from newer recorded uses (`database/models.py`).
- `generated_images` identifies linked files; distinct image IDs are image counts,
  never run counts. Its default `generation_time` is database insertion time.
- `image_prompt_pairs` stores immutable association provenance. More than one
  provenance row can describe the same image and recipe. Its `created_at` is the
  association time, potentially an old-image scan.
- `prompt_pairs.created_at` is the saved recipe date. Execution PNG evidence in
  `utils/pair_execution.py` records distinct text/composition values without a
  durable job ID or timestamp. `PromptTracker` execution IDs remain process-local.

The current UI can show saved negative variants, linked images and aggregate uses
with an estimate caveat. It cannot honestly show chronological execution dots,
repeat-run branches or completed-run counts.

## Smallest useful future migration

Add a durable `run_events` table using a stable host-session and ComfyUI job ID
plus recipe identity. Create started events at dequeue, rather than node encoding,
so cached executions remain visible. Retain nullable recipe references and immutable
raw/effective positive, negative and composition snapshots. Record completion,
failure and interruption only when observed; otherwise retain unknown status.

Add a deduplicated `run_images(run_id, image_id)` relationship. Multiple output
images can belong to one run, and one physical file must not manufacture multiple
runs through repeated metadata evidence. Preserve aggregate counters and existing
image provenance. Do not manufacture historical events from counters, file dates
or scans.

## Required migration and integration tests

Verify old databases upgrade transactionally and idempotently, with rollback on
failure; unchanged counters and associations; cached repeat executions; failed or
cancelled jobs; batched outputs; duplicate callbacks; process restart; unknown
connected inputs; and metadata rescans that create no execution events. Cover real
Classic and Nodes 2.0 hosts before exposing run chronology.
