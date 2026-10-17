# Prompt sending bridge

Issue #202 establishes the transport and host adapter before the redesigned
inspector displays prompt text. Gallery paper-plane controls and normalized
metadata follow in a separate slice; existing node layouts are unchanged.

## Shared transport

PromptManager consumes the exact Toolkit 0.2.0 artifact recorded in
`web/js/shared/toolkit/provenance.json`. The shared module has no ComfyUI imports.
Its client discovers explicit host sessions and compatible destinations, requests
a preview, and applies that preview using a one-use, client-bound, expiring ticket.
Success requires an acknowledgement with verified destination values.

BroadcastChannel connects independently opened tabs on the same origin and storage
partition. Deployment prefixes have separate channel namespaces. Exact origin,
protocol and peer checks reject unrelated messages. Same-origin scripts are
trusted; this transport is not authentication against other scripts on that origin.
Unsupported messaging is reported without breaking existing ComfyUI nodes.

An apply timeout or abort after posting means the outcome is unknown. It cannot
prove the host cancelled the write. The sender must inspect a fresh destination
preview before deciding to send again; automatic retry must not overwrite edits.

## PromptManager adapter

Classic, Text, Focused and Split accept positive text; +/- accepts either role or
both. Plain CLIP encoders are not assigned roles by title or position. An omitted
role remains untouched; an explicitly empty string clears only the selected
field. Unknown/ambiguous metadata must not become an empty string.

Previews show original and requested text plus positive prepend/append composition.
Connected composition is marked unknown rather than guessing upstream values.
Connected destination fields reject writes. Applying rechecks graph, node, widgets,
values and connections. Ordinary serialization cache synchronization is accepted
only when it preserves live values. Both requested roles are checked before writing.

Widget values, visible inputs, serialization and composed previews are synchronized.
Callback failures restore local fields; arbitrary external callback side effects
cannot be undone. Sending never queues execution or loads/replaces a workflow.

## Lifecycle and validation

Page hiding closes the host and invalidates tickets; restored pages mount one fresh
host. Client disposal clears pending timers/listeners and closes its channel.
Shared protocol contracts run against the actual bundled file, alongside adapter
units, cross-browser tab workflows and real Classic/Nodes 2.0 scenarios. Pinned
PromptManager-only, KikoTools-only and combined disposable hosts verify coexistence
and workflow reload without modifying either live installation.
