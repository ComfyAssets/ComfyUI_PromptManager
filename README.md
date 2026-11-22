![PromptManager by ComfyAssets](assets/brand/banner.webp)

# ComfyUI Prompt Manager

**Every prompt you run, every image it made, and every negative you tried, in one searchable library inside ComfyUI.**

![The Prompt Manager workspace: a masonry gallery of generated images with the selected image's prompts, tags and settings beside it](assets/readme/hero.webp)

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)
![Nightly tests](https://github.com/ComfyAssets/ComfyUI_PromptManager/actions/workflows/nightly.yml/badge.svg)

Prompt Manager saves your prompts as you generate and links each image to the prompt that made it. One page, the workspace, lets you browse, tag, compare and reuse them. Your library is a local SQLite database on your own machine.

## What's new

- **A new UI.** The workspace replaces the separate dashboard, gallery and metadata pages.
- **Classic and Nodes 2.0.** The nodes work in both ComfyUI renderers. CI starts a real ComfyUI and checks both.
- **Automated tests.** Unit tests, browser tests in Chromium, Firefox, WebKit and touch emulation, and tests against a real ComfyUI.
- **Linux and Windows.** The Python suite runs on both, on Python 3.12, 3.13 and 3.14. macOS should work but is not tested.

## See it

All images on this page were generated and recorded through Prompt Manager in a clean demo library.

### Browse your whole output folder

The Images page shows your output as masonry, a grid or a list. Search, categories, filters and saved views narrow it. Images and videos appear together, and selecting one shows its prompts and settings on the same page.

![Images page in grid layout](assets/readme/images-grid.webp)

### Open any image and see how it was made

The viewer shows the positive and negative prompts, tags, seed, steps, CFG, sampler and model next to the image.

![The viewer with an image on the left and its prompts and generation settings on the right](assets/readme/viewer.webp)

### Keyboard shortcuts

The viewer has shortcuts for moving between files, the slideshow, zoom, rotate, flip, full screen, details, download and delete. Press `?` to see them all.

![The viewer's keyboard shortcuts overlay](assets/readme/viewer-shortcuts.webp)

### Select and act while you scroll

The selection bar stays pinned while you scroll, so you can keep picking images and act on them from any position on the page. A Back to top button appears once you scroll down.

![Images page scrolled down, with the selection bar pinned at the top and a Back to top button](assets/readme/pinned-bar.webp)

### One positive prompt, many experiments

The Prompt library groups everything by positive prompt. Repeat runs and negative variants stay with their positive prompt and don't add near-duplicate entries to your list.

![Prompt library cards showing each prompt's images, negative variants, runs and tags](assets/readme/library.webp)

### See negative variants on a timeline

Each prompt has its own page with a timeline of its negative variants. Click a red dot and the gallery filters to the images that negative variant made. Small dots mark repeat runs.

![Clicking through negative variants on the timeline filters the gallery below](assets/readme/timeline.anim.webp)

### Compare negatives term by term

Compare negatives shows two negative variants side by side and lists the terms one adds, so you can see what changed between runs.

![Two negative prompts side by side with the added terms highlighted](assets/readme/compare-negatives.webp)

### Auto-tag your images and approve the suggestions

Tag one image, a selection or your whole output with WD14 or JoyCaption. WD14 suggestions come with confidence scores, and you can review suggestions before Prompt Manager saves anything. Auto-tags belong to the image, so they never change your prompts.

![Tag image dialog with WD14 suggestions and their confidence scores](assets/readme/autotag.webp)

### Manage tags and categories

Tags & categories lists every tag and category with how often you use it. Rename, merge and delete tags, and preview a category change before you apply it.

![Tags and categories page](assets/readme/tags.webp)

### Group images your own way

Collections are sets of images you pick by hand. They are separate from favorites and saved views. Add images from the selection bar and the collection appears in the sidebar.

![A collection called Book cover ideas open in the workspace](assets/readme/collections.webp)

### Nothing gets guessed

When a file's metadata has more than one possible prompt, Prompt Manager doesn't pick for you. The file waits in Needs review, where you can choose the sampler path, link the file to any prompt in your library, or leave it unresolved.

![Link to a prompt dialog searching the library from Needs review](assets/readme/needs-review.webp)

### Inspect any file without importing it

Pick an image or video from your computer to read its prompts, settings and workflow. Your browser reads the file. Prompt Manager doesn't upload it or add it to your library.

![Inspecting a local file in the viewer](assets/readme/inspect.webp)

### Send a prompt back to your canvas

Pick an open ComfyUI window and a node, then review the exact before and after. Nothing changes until you apply it.

![Send to ComfyUI dialog showing the before and after of a prompt](assets/readme/send.webp)

### Deletes you can review

Before Prompt Manager removes anything, you choose what happens to the files:

- **Remove from the library.** The file stays on disk. This is the only choice on Windows.
- **Move to the system trash.** Linux and macOS only.
- **Delete permanently.** Linux and BSD servers only. This is for headless servers, where a trash fills the disk. You can't recover these files.

A safety backup of the database is on by default, and you can bring removed images back.

![Delete dialog offering to remove from the library only or move files to the trash](assets/readme/delete-review.webp)

### Maintenance in one place

System holds settings, image sources, auto-tag models, integrations, maintenance, backups and logs. Risky operations show a review first, and long ones run as jobs you can follow in Activity.

![System Maintenance section](assets/readme/system.webp)

![Activity page listing finished scans and tagging jobs](assets/readme/activity.webp)

### Works on your phone too

The workspace adapts to narrow screens, and the browser test suite includes touch.

![Images page and viewer at phone width](assets/readme/phone.webp)

## The nodes

The six nodes are under **ComfyAssets > Prompts** in the Add Node menu. They all share one library. Every screenshot below is a small real workflow that ran.

### Prompt Manager (Classic)

This node replaces CLIP Text Encode. It has the same CLIP input and the same CONDITIONING output, and it saves the prompt as it runs. It also outputs the text as a STRING.

![Prompt Manager Classic node in a workflow, Classic renderer](assets/readme/node-classic.webp)

The same workflow in Nodes 2.0:

![Prompt Manager Classic node in a workflow, Nodes 2.0 renderer](assets/readme/node-classic-nodes2.webp)

### Prompt Manager (Focused)

This node puts the editor first. Composition and metadata collapse, and **Browse library** lets you search, preview and use a saved prompt on the node.

![Prompt Manager Focused node in a workflow](assets/readme/node-focused.webp)

### Prompt Manager (Split)

This node has the same editor with the library always visible. A wide node shows them side by side. A narrow node stacks them.

![Prompt Manager Split node in a workflow](assets/readme/node-split.webp)

### Prompt Manager (+/-)

This node holds the positive and the negative prompt and saves them as a pair. The timeline's negative variants come from this node. It outputs CONDITIONING and STRING for both sides.

![Prompt Manager plus/minus node in a workflow](assets/readme/node-pair.webp)

The same workflow in Nodes 2.0:

![Prompt Manager plus/minus node in a workflow, Nodes 2.0 renderer](assets/readme/node-pair-nodes2.webp)

### Prompt Manager Text

This node saves the prompt and outputs a STRING, not CONDITIONING. Use it in workflows that send text to another node. Prepend and append inputs let you build the final text.

![Prompt Manager Text node feeding a CLIP Text Encode node](assets/readme/node-text.webp)

### Prompt Search List

This node searches your library by text, category, tags and minimum rating. It outputs the matches as a list, so ComfyUI runs each one. This workflow found three prompts tagged `mushroom` and made three images.

![Prompt Search List node producing three images](assets/readme/node-search.webp)

To link the images of a Search List batch to their prompts, wire the Search List output into a Prompt Manager Text node, not straight into CLIP Text Encode. Wired straight in, the images stay unlinked, because nothing saves the search text as a prompt.

## Install

Prompt Manager requires ComfyUI and Python 3.12 or newer. Its only dependencies of its own are `watchdog` and `Pillow`. The rest comes with ComfyUI.

Install from the Comfy Registry or ComfyUI-Manager by searching for Prompt Manager, or install by hand:

```bash
cd ComfyUI/custom_nodes/
git clone https://github.com/ComfyAssets/ComfyUI_PromptManager
pip install -r ComfyUI_PromptManager/requirements.txt
```

Restart ComfyUI. Click **PM** in the top bar, or **Web UI** on a node, or open `http://localhost:8188/prompt_manager/workspace`.

**Optional extras.** Prompt Manager installs none of these for you:

| To use | You need |
| --- | --- |
| WD14 auto-tagging (about 400 MB per model) | `onnxruntime`, or `onnxruntime-gpu` for CUDA |
| GGUF JoyCaption (about 4.5 GB) | `llama-cpp-python` |
| HuggingFace JoyCaption (about 16 GB) | `transformers`, `accelerate`, `bitsandbytes` |
| Prompts embedded in videos | `ffprobe` on PATH |
| [LoRA Manager](https://github.com/willmiao/ComfyUI-Lora-Manager) import | LoRA Manager installed. Turn the import on in System > Integrations. |

Download or unload tagging models in System > Auto-tagging.

## Where your data lives

| What | Where |
| --- | --- |
| Library (`prompts.db`), `config.json`, database backups, logs | `ComfyUI/user/__promptmanager/` on new installs |
| Thumbnails | `output/thumbnails` |
| Your images | Left where they are. Prompt Manager reads your output folder and any sources you add. |

Set `PROMPT_MANAGER_DATA_DIR` to an absolute folder to keep the library somewhere else. `config.json` can hold your CivitAI key, so treat it as private.

## Upgrading from an earlier version

- **Python 3.12 or newer is now required.**
- **Your library stays where it is until you choose.** The workspace offers two choices. **Move library** makes a verified copy in `user/__promptmanager/` and renames the old files without deleting them. **Keep it here** leaves the library in place.
- **Older images.** A one-time notice offers a metadata scan that links them to prompts and negatives. Scans don't count as new runs.
- **Going back isn't supported.** Prompt Manager now stores each workflow once, and an older version shows new records without their workflow or prompt graph. Back up first in System > Backup & export. See [the old README's changelog](README-legacy.md#changelog) for the manual way back.

## The old pages are gone

This version removes the dashboard, tags page, gallery and metadata viewer. The workspace does everything they did. Their URLs and bookmarks still work, and each one opens the matching workspace page.

| Old URL | Opens |
| --- | --- |
| `/prompt_manager/admin` | Prompt library |
| `/prompt_manager/admin#/tags` | Tags & categories |
| `/prompt_manager/admin#/tags/cats,dogs?mode=or` | Prompt library, filtered to prompts tagged `cats` or `dogs` |
| `/prompt_manager/gallery` | Images |
| `/prompt_manager/gallery.html` | Inspect a file |

A tag bookmark opens the Prompt library with that filter, and the filter stays in the address: `/prompt_manager/workspace#/library?tag=cats&tag=dogs&tag_mode=any`.

- Reload, Back and Forward keep the filter.
- The address follows the tags you set in **Filters**.
- You can bookmark such a link or write one yourself. Leave out `tag_mode` to match all the tags. `tag_mode=untagged` lists prompts without a tag.
- The sidebar's Prompt library link always shows the whole library.
- Search, category and sort aren't part of the address. **Save view** keeps those.

[README-legacy.md](README-legacy.md) still describes the old pages, for reference.

## Known limits

- **Trash is Linux and macOS only.** Windows has no Recycle Bin option, because the bin can delete large files permanently without warning. On Windows you can only remove an image from the library, and the file stays on disk. Where a move to the trash isn't possible on Linux or macOS, the dialog lists the reason for each file.
- **Permanent delete is Linux and BSD only.** You can't undo it. The safety backup brings back the library records, not the files.
- **Show in folder** opens the file manager on the machine running ComfyUI, which may not be the machine with your browser.
- **Returned images** get their prompt links back, but not their tags, categories, collections or favorites.
- **Inspect a file** doesn't add the file to your library.
- **Leave unresolved is permanent.** Needs review doesn't offer the file again unless its bytes change.
- **Pair prompts saved by earlier versions** stay Uncategorized. You can set the category with Edit metadata.

## How it's tested

| Layer | What runs |
| --- | --- |
| Python | Over 200 test files on Linux and Windows with Python 3.12 to 3.14, with an 85% coverage gate |
| Frontend | About 100 unit test files, with coverage floors per file |
| Browser | Over 60 Playwright spec files in Chromium, Firefox, WebKit and touch emulation |
| Real ComfyUI | A pinned CPU ComfyUI, started in Classic and Nodes 2.0, running real workflows |

Every pull request runs the Python suite, the frontend tests and the browser tests in Chromium, once. Firefox, WebKit, touch emulation, Python 3.13 and 3.14, Windows and the real ComfyUI run every night. Nothing tests macOS. It should work, but support is best-effort. To run the tests yourself, see [TESTING.md](TESTING.md).

## Changelog

[CHANGELOG.md](CHANGELOG.md) lists what changed in each release and the contributors whose pull requests are part of it. Full notes are on the [releases page](https://github.com/ComfyAssets/ComfyUI_PromptManager/releases).

## License

The license is MIT. See [LICENSE](LICENSE).
