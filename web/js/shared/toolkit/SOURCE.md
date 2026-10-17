# Initial source provenance

The initial browser controls are extracted from ComfyAssets' own
KikoTools modules at commit `1820fb19ea5d200e8cd2a09bcbbcc35926f13228`.
KikoTools remains unchanged by this extraction. Future toolkit changes belong
here and are validated in PromptManager before a later KikoTools adoption PR.

| Toolkit file | KikoTools source | Original source SHA-256 |
| --- | --- | --- |
| `ui/core.js` | `web/ui/core.js` | `cd10b700441a607cb1d56051cf0a430c0b36f901c0c351d8f4dae067090d8838` |
| `ui/controls.css` | `web/ui/controls.css` | `64d6b0f3059b0a21187796059fe14bfb5358a796a18f775543b7baef43874e80` |

Host orchestration and coverage helpers were extracted and generalized from
KikoTools' maintainer tooling, with isolated tracked-file installation and a
project preparation adapter proven against PromptManager. They have no runtime
imports from either node pack. All original code retains the ComfyAssets MIT
license. No Pixaroma implementation, styles or assets were copied.

`ui/core.js` is unchanged. `ui/controls.css` retains only the shared control,
range, touch-target and forced-colors rules (original lines 1–61, 97–99, 118,
170–172 and closing brace 174). Feature-view selectors remain in KikoTools.
The retained declarations are unchanged; the extracted stylesheet SHA-256 is
`3ae1ca39077ffc3621b86ae7ef77202841fa27061b0e4623f2bb0413b27ecd7b`.

## Suggestion view extraction

`ui/suggestions.js` derives from KikoTools `web/ui/autocomplete-suggestions.js`
at commit `1820fb19ea5d200e8cd2a09bcbbcc35926f13228` (SHA-256 `2aecc78f53b7be3d67b77337b4a340f948afcc4cb1196ccf3b70739ef1f0d002`).
The generic view preserves its scope-based cleanup, safe text rendering and
viewport positioning. Pack-specific classes and IDs are replaced by `ca-` names;
callers can supply a list label and omit type badges. Corresponding completion
styles are extracted from KikoTools' `web/ui/controls.css`. KikoTools is unchanged.
Keyboard handling, input ARIA state, matching and insertion remain in consumers.
The stylesheet hash above describes the initial extraction before this addition.
