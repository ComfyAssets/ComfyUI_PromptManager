# Prompt inspection and sending

The opt-in workspace at `/prompt_manager/workspace#/images` shows full positive
and negative prompts in both Image details and the full-image preview. Existing
gallery and metadata tools remain available during the application rebuild.

Each prompt shows its evidence source: recorded execution, workflow inference,
or embedded generation parameters. Path-only saved-library associations are
marked unverified and cannot be sent: a filename alone cannot prove that an image
has not been replaced. Missing, ambiguous, and unreadable prompts remain visible
as distinct states. An explicitly
empty negative is a resolved value and can clear a destination's negative field.

Use the blue paper-plane action beside a prompt, or **Send prompt pair**. Keep a
ComfyUI tab open under the same origin and deployment prefix. Choose a window and
compatible Prompt Manager node, select **Preview changes**, review the complete
before/after text and preserved prepend/append composition, then **Apply to
ComfyUI**. The dialog reports success only after a matching acknowledgement.
It does not load workflows or queue generation. If confirmation is lost, inspect
ComfyUI and preview again before another attempt. Closing the dialog cannot undo
an update already sent.

## Read-only API

`GET /prompt_manager/images/inspect/{relative_path}?root={root_index}` requires
an explicit configured root, including when two roots contain the same filename.
The response contains `image` identity and `prompts.positive` / `prompts.negative`.
Each role has `status` (`resolved`, `absent`, `unresolved`, or `ambiguous`), `text`
(string only when resolved, otherwise null), and `source`. Inspection does not
index files, alter saved prompts, run scans, or decode image pixels. PNG text and
video probe output are bounded; unsupported or malformed evidence remains
unresolved rather than becoming sendable placeholder text. Videos are read from
one container carrier: the per-key tags written by core SaveVideo/SaveWEBM and
current VideoHelperSuite (`prompt`, `workflow`, `parameters`,
`comfyassets_prompt_pairs`), else the old VideoHelperSuite `comment` envelope,
else `description`. The admin scan reads the same carrier, so Images and
ingestion agree on a video's prompts (see TESTING.md for the order and budgets;
the live monitor processes images only).

Python route changes require restarting ComfyUI. Browser assets require a refresh.
