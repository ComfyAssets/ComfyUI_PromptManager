# Changelog

What changed in each release of ComfyUI Prompt Manager, newest first. Full notes for each version are on the [releases page](https://github.com/ComfyAssets/ComfyUI_PromptManager/releases).

Thank you to everyone whose pull requests are part of these releases. They are named beside their changes and listed under [Contributors](#contributors).

## v3.8.0 (Old pages removed, Tag cleanup, AVIF)

- **🧹 Tag cleanup**: System › Maintenance finds tags that are whole comma lists and tags used nowhere. You review them and remove only what you tick; a safety backup is always taken first. Later cleanups will be added to the same place ([#618](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/618))
- **🏷️ LoRA import tags**: a LoRA's trained words become a tag only when they are one short phrase. Lists and sentences are no longer saved as tags, and the import review says how many were left out ([#618](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/618))
- **🖼️ AVIF images**: collected, thumbnailed and linked to their prompts like PNG and WebP, where your Pillow can read AVIF. By [@LacklusterOpsec](https://github.com/LacklusterOpsec) ([#673](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/673), [#652](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/652))
- **🗑️ Old pages removed**: the old Dashboard, Gallery and Metadata viewer are gone, after the notice in 3.5.1. Their addresses open the matching workspace page, and old tag bookmarks open the Prompt library with that filter ([#668](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/668))
- **🔗 Tag filters in the address**: `#/library?tag=cats&tag=dogs&tag_mode=any` can be bookmarked and shared; Reload, Back and Forward keep the filter ([#668](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/668))
- **🐛 LoRA import label**: an import that had already saved when ComfyUI closed is shown as finished, not cancelled. By [@aryankalantri](https://github.com/aryankalantri) ([#672](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/672), [#619](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/619))
- **🪟 Windows**: two installs writing one shared thumbnail folder at the same moment no longer fail with "Permission denied" ([#674](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/674))
- **⚠️ Known limit**: Inspect shows a WebP or AVIF file but not its embedded prompt yet; the image still links to its prompt ([#656](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/656))

## v3.7.2 (Always a way to choose the prompt)

- **🎯 Choose prompt in Inspect**: an image with several possible prompts shows a Choose prompt section; Inspect creates the Needs review item itself, also for older images ([#659](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/659))
- **📥 Needs review without a scan**: the live monitor queues an ambiguous output as soon as its run ends ([#659](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/659))
- **🔢 Big workflows**: a file with more than 16 different prompts offers the 16 most used instead of none
- **🔗 Link to a prompt** lists the image's own prompts first; the Needs review queue loads as you scroll
- **🐛 rgthree Any Switch** no longer makes a decided prompt look ambiguous ([#657](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/657)); a role every candidate agrees on shows as resolved ([#658](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/658))
- **🧪 CI**: every change is tested once, on its pull request; other browsers, Python versions and Windows run nightly ([#666](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/666))

## v3.7.1 (WebP linking)

- **🖼️ WebP images link to their prompts**: ComfyUI's Save Animated WEBP stores the prompt in EXIF, which is now read. By [@LacklusterOpsec](https://github.com/LacklusterOpsec) ([#650](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/650))
- **🐛 Prepend and append text** start empty instead of `null`. By [@LacklusterOpsec](https://github.com/LacklusterOpsec) ([#651](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/651))

## v3.7.0 (Permanent delete, new logo)

- **🗑️ Permanent delete** for images on Linux and BSD servers, beside remove-from-library and trash. By [@k98kurz](https://github.com/k98kurz) ([#643](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/643))
- **🎨 New logo** in the README, the Comfy Registry and the browser tabs ([#632](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/632))
- **🐛 Folders with a backslash in their name** work on Linux and macOS; one odd file no longer stops the viewer opening ([#646](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/646))

## v3.6.0 (Library sorts)

- **↕️ Three more Prompt library sorts**: Recently used, Most used and Name Z–A
- **🐛 Windows network shares**: backup checks, restore, Move library and history reads open a library on a network path ([#620](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/620))
- **🐛 Unloading a tagging model** no longer freezes the server ([#620](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/620))

## v3.5.1 (Smaller download)

- **📦 Smaller package**: the Registry download drops from 17.4 MB to about 3.9 MB ([#615](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/615))
- **📣 Notice**: the old pages say they will be removed

## v3.5.0 (Thumbnail location, Send to all, Skipped tags)

- **📁 Thumbnail location**: keep thumbnails inside each source or in one folder of your choice, with a reviewed move ([#320](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/320))
- **📤 Send to every node**: Send to ComfyUI offers all eligible nodes of a window at once ([#542](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/542))
- **🚫 Skipped tags** and **saved tagging instructions** for auto-tagging ([#431](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/431))
- **🔧 Pair category and tags repair** in System › Maintenance ([#550](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/550))
- **⚡ Less memory** for scans and image work ([#523](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/523))
- **🐛 Prompt Search List** text is no longer saved as a prompt ([#548](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/548))

## v3.4.1 (Fix release)

- **🪟 Windows**: ComfyUI could hang on exit with watchdog 7.0.0
- **🔒 LoRA import errors** no longer show server details

## v3.4.0 (The workspace)

- **✨ Rebuilt around one page**, the workspace: Images, Prompt library, Tags & categories, Collections, Favorites, Needs review, Inspect a file, Activity and System
- **🖼️ Viewer** with keyboard shortcuts, zoom, rotate and slideshow; **Send to ComfyUI** from any prompt
- **🏷️ Per-image tags and auto-tagging** with WD14 or JoyCaption
- **🧩 Nodes**: Focused, Split and Prompt Manager (+/-) join Classic, Text and Search List; all run in Classic and Nodes 2.0
- **📁 Library outside the node folder**: new installs keep the library in `ComfyUI/user/__promptmanager/`; an existing library moves only when you choose
- **🗜️ Smaller database**: each workflow and prompt graph is stored once, compressed
- **⚠️ Requires Python 3.12 or newer**; downgrading is not supported

## Earlier versions

Releases from before the workspace, as they were written at the time.

## v3.3.1 (Proxy Routing Fix)

- **🌐 Proxy support**: the dashboard, gallery, tag page and metadata viewer now work when ComfyUI is reached through a proxy mount such as SwarmUI's `/ComfyBackendDirect/`. Scripts, images, API calls, progress streams and gallery folder filters stay under the mount, and the canvas node's buttons use ComfyUI's `fileURL()`. Direct ComfyUI URLs are unchanged. Thanks to @ghosttigerllc-bit ([#164](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/164))
- **📦 Dependencies**: the runtime minimums stay at `Pillow>=8.0.0` and `watchdog>=2.1.0`, so updating Prompt Manager never forces upgrades in the shared ComfyUI environment

## v3.3.0 (Hardening, Background Jobs & Worker Threads)

- **🔒 Security**: gallery roots restricted to ComfyUI directories and validated wherever they are read; media-only, fail-closed image serving; Windows path escape in static routes closed; quote-safe HTML escaping with inline handlers moved to data attributes; the CivitAI key is never returned and only sent to HTTPS CivitAI hosts; auto-tag paths contained; side-effect routes are POST-only; backups include the WAL and restores are size-capped, integrity-checked and applied through SQLite's online backup API
- **🧵 Worker Threads**: a Settings slider (default half the cores) used by the image rescan, thumbnail generation and duplicate detection. Measured: a 1 GB sample scanned in 2.5 s instead of 18 s at 8 threads
- **🔁 Background Jobs**: the image rescan and the duplicate scan run on the server, stream progress, survive a closed tab, minimize to a progress pill and re-attach after a reload
- **🎬 Video Prompts**: the scan reads the prompt ComfyUI's video savers embed in MP4/MOV/WebM containers (needs `ffprobe`)
- **🖼️ Thumbnails**: written atomically and served under versioned URLs so a regenerated thumbnail never shows a stale cached copy; the per-run cap applies only to thumbnails that still need generating and the gallery says how many remain
- **🏷️ Tag Autocomplete**: the dashboard tag box suggests tags, narrowed to those that co-occur with the tags already typed
- **♾️ Infinite Scrolling**: optional in the gallery and the dashboard list (saved default plus a session toggle)
- **⚙️ Gallery Settings** now apply at load; grid columns, media info and the Rescan button work as labelled
- **🐛 Correctness**: run counts increment when ComfyUI dequeues a prompt; negative prompts are never traced; per-thread SQLite connections and idempotent migrations (an old rebuild could delete every image row); `truncate_logs` no longer rolls the log over; the ComfyUI canvas loads only the canvas extension; Windows console banners are ASCII-safe; subfolders use `/` on every platform
- **🧪 Tests & CI**: 1,460 Python and 142 JavaScript tests (from 510 and 26), coverage 96% with an 85% gate, CI on Linux, Windows and macOS for Python 3.10–3.12, flake8 clean
- **⚠️ API changes**: image paths in responses are relative to the ComfyUI tree (use `url`); `autotag/download`, `autotag/start` and the thumbnail progress stream are POST-only; `lora/status` returns `has_civitai_api_key` instead of the key; `/search` is paginated

## v3.2.4 (Recently Used Sorting & Queue-Time Run Counts)

- **🕒 Recently Used Sort**: sort the dashboard by last use, most used or rating, server-side across pages
- **🔢 Run Counts**: `run_count` and `last_used_at` are recorded through the ComfyUI queue hook, so cached nodes still count
- **🔗 Positive-Prompt Image Linking**: generated images link to the positive prompt traced through the queued graph

## v3.2.1 (LoRA Manager Integration)

- **🔗 LoRA Manager Integration**: Import LoRA metadata, trigger words, and CivitAI example images from [ComfyUI-Lora-Manager](https://github.com/willmiao/ComfyUI-Lora-Manager) into your prompt database
- **💉 Auto-Inject Trigger Words**: Optionally append LoRA trigger words when `<lora:name:weight>` is detected in prompts during encoding
- **🔑 CivitAI API Key Support**: Authenticate with CivitAI to download NSFW preview images
- **📥 Import Progress Modal**: Real-time SSE streaming progress during LoRA import with per-model status

> **Note:** LoRA Manager support is a WIP — this was a highly requested feature. Please [open issues](https://github.com/ComfyAssets/ComfyUI_PromptManager/issues) for any bugs or feature requests.

## v3.2.0 (Folder Filter & QoL Improvements)

- **📁 Folder Filter**: New folder dropdown in the search panel to filter prompts by output subdirectory
- **📂 Multi-Directory Gallery Scan**: Configure multiple output directories and browse them all in one gallery
- **🖼️ Filmstrip Prompt Display**: Show prompt text and copy button in the filmstrip image viewer
- **🖱️ Click-to-Close Viewer**: Click outside the image viewer to close it
- **🐛 Bug Fixes**: Fixed database path config, UnboundLocalError in text inputs, node caching skip, diagnostics singleton lifecycle

## v3.1.0 (WD14 Tagger, Tailwind v4 & Major Refactors)

- **🏷️ WD14 Tagger Support**: Added WD14 SwinV2 and WD14 ViT as new auto-tag model options — fast ONNX-based classifiers (~400MB) that output Danbooru tags with confidence scores, no prompt needed
- **🎚️ Threshold Controls**: Adjustable general tag (default 0.35) and character tag (default 0.85) confidence thresholds for WD14 models via UI sliders
- **🎨 Tailwind v4 Migration**: Complete frontend redesign with ComfyUI theme token system (`--pm-*` design tokens) that adapts to ComfyUI's color palette
- **🏗️ API Refactor**: Split monolithic `api.py` into domain-specific route modules for better maintainability
- **🗃️ Junction Table Tags**: Migrated tag storage from JSON column to normalized junction tables with proper foreign keys
- **📄 Tags Page**: Dedicated tag management page with search, rename, merge, and delete operations
- **🔍 Load More Button**: Progressive prompt loading instead of traditional pagination
- **🔒 Security Hardening**: Resolved 15 GitHub code scanning alerts, prevented stack trace exposure in SSE errors
- **🧪 Test Coverage**: Added comprehensive unit tests for validators, hashing, metadata, config, and prompt tracker; API integration tests; database tests with CI pipeline
- **⚙️ CI/CD Improvements**: Added code quality scanning, security scanning, Dependabot, and pre-commit hooks with Black formatting
- **🐛 Bug Fixes**: Fixed custom image scan directory config (#108), gallery crash on thumbnail linking, broken imports after API refactor, widget value sync issues, and prepend/append metadata tag filtering in auto-tagger

## v3.0.26 (Top Bar Button)

- **🔘 Top Bar PM Button**: Added a "PM" button to the ComfyUI top bar for quick access to the admin dashboard
- **🚀 No Node Required**: Open PromptManager without having to add the node to your workflow first
- **🎨 Teal Button Design**: Distinctive teal-colored button positioned before the settings gear
- **🔄 Frontend Compatibility**: Works with both new ComfyUI frontend (v1.3.1+) and legacy versions

## v3.0.25 (Film Strip & QoL Improvements)

- **🎞️ Film Strip Preview**: Backported from V2 - thumbnail strip under each prompt showing linked images
- **🖼️ Quick Image Viewer**: Click any filmstrip thumbnail to open full-size image viewer
- **📸 Smart Thumbnails**: Automatically uses generated thumbnails for faster loading, falls back to full images
- **⬆️ Auto-Scroll on Pagination**: Page navigation now smoothly scrolls to top after loading

## v3.0.24 (Manual Prompt Creation)

- **➕ Add Prompt Button**: New "Add Prompt" button in the admin dashboard Bulk Actions bar
- **📝 Direct Prompt Creation**: Create prompts directly from the UI without generating images
- **🛡️ Protection from Pruning**: New prompts are automatically protected from orphan cleanup by default
- **🏷️ Full Metadata Support**: Set category, tags, rating, and notes when creating prompts
- **🔒 Orphan Protection Toggle**: Option to protect or unprotect prompts from maintenance cleanup

## v3.0.23 (AI AutoTag Release)

- **🏷️ AI AutoTag**: Automatically tag your entire image collection using JoyCaption vision models
- **🤖 Dual Model Support**: Choose between JoyCaption Beta One FP16 (high quality) or GGUF FP8 (lower VRAM)
- **📋 Two Tagging Modes**: Batch mode for bulk tagging, Review mode for one-by-one approval
- **⚙️ Smart Tag Handling**: Skip already-tagged images or re-tag all - respects your existing work
- **✏️ Customizable Prompts**: Adjust the system prompt to match your collection style and tag vocabulary
- **🔄 Real-time Progress**: SSE-based streaming progress updates during batch operations
- **📦 Tag Accordion UI**: Collapsible tag display shows first row with expandable section for large tag sets
- **🎯 Review Confirmation**: Prompts before overwriting existing tags in review mode

## v3.0.10 (Batch Processing Node)

- **📋 PromptSearchList Node**: New node that searches the prompt database and outputs results as a list
- **🔗 Batch Processing Support**: Uses `OUTPUT_IS_LIST` for native ComfyUI batch processing compatibility
- **🔍 Full Search Capabilities**: Search by text, category, tags, minimum rating, and result limit
- **⚡ Lightweight Operation**: Read-only search with no database writes
- **🎯 Workflow Integration**: Connect directly to batch processing nodes like String OutputList

## v3.0.9 (Output-Wide Gallery & Video Support)

- **📁 Output-Wide Gallery Mode**: Complete gallery redesign to show ALL content from ComfyUI output directory
- **🎬 Full Video Support**: Native video playback with HTML5 player for .mp4, .webm, .avi, .mov, .mkv, .m4v, .wmv formats
- **🖼️ Video Thumbnail Generation**: Automatic video thumbnails using OpenCV, ffmpeg, or placeholder fallback
- **⚙️ Video Settings Panel**: Configurable autoplay, mute, and loop settings with persistent preferences
- **🎮 Video Navigation**: Seamless navigation between videos with keyboard shortcuts and custom controls
- **📊 Video Metadata Display**: Show duration, resolution, aspect ratio, and detailed file information
- **🔄 Mixed Media Integration**: Videos and images seamlessly integrated in the same gallery interface
- **⚡ Enhanced Performance**: Optimized thumbnail generation for both images and videos
- **🎛️ Advanced Gallery Settings**: Comprehensive settings panel with video controls and performance options
- **🖱️ Improved Click Handling**: Fixed thumbnail failures to preserve click functionality
- **📱 Mobile Compatibility**: Enhanced responsive design for video playback on all devices

## v3.0.5 (Text-Only Node Release)

- **📝 PromptManagerText Node**: New text-only variant that outputs STRING instead of CONDITIONING
- **🔗 Text Concatenation**: Built-in prepend/append functionality for dynamic text assembly
- **⚡ Lightweight Processing**: No CLIP encoding overhead for pure text workflows
- **🔄 Dual Node Architecture**: Choose between CLIP encoding (PromptManager) or text-only (PromptManagerText)
- **🏷️ Shared Metadata System**: Both nodes use the same database and metadata features
- **🖼️ Unified Gallery**: Images from both node types appear in the same gallery system
- **📊 Enhanced Tracking**: Improved prompt tracking with prepend/append context preservation
- **📊 Enhanced Tracking**: Improved prompt tracking with prepend/append context preservation

## v3.0.3 (ViewerJS Integration & Enhanced Gallery)

- **🖼️ Professional Image Viewer**: Complete gallery redesign with [ViewerJS](https://github.com/fengyuanchen/viewerjs) integration
- **⚡ Advanced Image Navigation**: Zoom, rotation, slideshow, and professional viewing controls
- **🎨 Dark Theme Integration**: Seamless ViewerJS styling that matches the admin interface
- **🔬 Enhanced Metadata Parsing**: Improved ComfyUI workflow parameter extraction with targeted parsing
- **📊 Real-time Parameter Display**: Live CFG, steps, sampler, and seed extraction from workflow nodes
- **📋 Integrated Copy Functionality**: One-click copying of prompts and generation parameters
- **🛠️ Robust Value Detection**: Smart parsing that targets specific node types and validates parameters
- **⌨️ Keyboard Navigation**: Full keyboard support for gallery navigation and viewer controls
- **📱 Responsive Design**: Enhanced mobile and tablet compatibility

## v3.0.0 (Metadata Analysis Release)

- **🔬 PNG Metadata Analysis**: Advanced ComfyUI workflow extraction from PNG images
- **📋 Standalone Metadata Viewer**: Dedicated tool at `/web/metadata.html` for analyzing any ComfyUI image
- **🖼️ Integrated Metadata Panel**: Real-time workflow analysis in the image viewer
- **📸 Bulk Image Scanning**: Mass import and analysis of existing ComfyUI output directories
- **🛠️ System Management Suite**: Complete backup, restore, maintenance, and diagnostic tools
- **🚮 Orphaned Prompt Cleanup**: Remove prompts that have no linked images for database optimization
- **📋 Advanced Logging**: Comprehensive logging system with web-based log viewer
- **🎨 Enhanced Admin Dashboard**: Modern dark theme with improved UX and management tools
- **🔧 Robust Error Handling**: Advanced PNG parsing with NaN value cleaning and error recovery
- **📁 File Path Integration**: Direct file path display and system integration
- **⚡ Performance Optimizations**: Improved metadata extraction and database operations

## v2.0.0 (Gallery Release)

- **🖼️ Automatic Image Gallery**: Complete image tracking and gallery system
- **🌐 Advanced Web Interface**: Comprehensive admin dashboard with responsive design
- **📱 Image Viewer**: Full-screen modal with navigation and keyboard shortcuts
- **🔍 Enhanced Search**: Real-time search with advanced filtering options
- **⚡ Performance Improvements**: Optimized database operations
- **🛠️ Basic Diagnostics**: Initial system diagnostics and health monitoring
- **📊 Bulk Operations**: Multi-select prompt editing and management

## v1.0.0 (Initial Release)

- Core text encoding with database storage
- Search and filtering functionality
- Metadata support (categories, tags, ratings, notes)
- Hash-based deduplication
- SQLite backend with optimized schema
- Basic web interface
- Export functionality

## Contributors

Pull requests from the community that were merged into Prompt Manager:

| Contributor | Pull requests |
| --- | --- |
| [@LacklusterOpsec](https://github.com/LacklusterOpsec) | [#673](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/673) AVIF as a media type · [#650](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/650) WebP images link to their prompts · [#651](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/651) prepend/append defaults |
| [@aryankalantri](https://github.com/aryankalantri) | [#672](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/672) LoRA import finishes when it saved at exit |
| [@k98kurz](https://github.com/k98kurz) | [#643](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/643) permanent delete for images (Linux/BSD) |
| [@ghosttigerllc-bit](https://github.com/ghosttigerllc-bit) | [#165](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/165) A1111/Forge generation parameters · [#164](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/164) routing behind a ComfyUI proxy · [#140](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/140) recursive subfolder filtering and gallery tree |
| [@develop1st](https://github.com/develop1st) | [#141](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/141) collapsible sidebar setting and mobile overlap fix |
| [@logicalor](https://github.com/logicalor) | [#87](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/87) thumbnails in subfolders |
| [@x0x0b](https://github.com/x0x0b) | [#73](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/73) star icon alignment |
| [@robinjhuang](https://github.com/robinjhuang) | [#2](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/2) Registry publishing action · [#1](https://github.com/ComfyAssets/ComfyUI_PromptManager/pull/1) `pyproject.toml` for the Registry |
