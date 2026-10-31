# ComfyUI Prompt Manager

**Every prompt you run, every image it made, and every negative you tried, in one searchable library inside ComfyUI.**

![The Prompt Manager workspace: a masonry gallery of generated images with the selected image's prompts, tags and settings beside it](assets/readme/hero.webp)

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)
![Tests](https://github.com/ComfyAssets/ComfyUI_PromptManager/workflows/Tests/badge.svg)

Prompt Manager saves your prompts as you generate, links each image back to the prompt that made it, and gives you one page to browse, tag, compare and reuse all of it. Your library is a local SQLite database on your own machine.

## What's new: rebuilt from the ground up

- **A new UI.** One app, the workspace, replaces the separate dashboard, gallery and metadata pages. The old pages still work if you want them.
- **Classic and Nodes 2.0.** The nodes work in both ComfyUI renderers. CI starts a real ComfyUI and checks both.
- **Tested end to end.** Unit tests, browser tests in Chromium, Firefox, WebKit and touch emulation, and tests against a real ComfyUI.
- **Mac, Linux and Windows.** The Python suite runs on all three, on Python 3.12, 3.13 and 3.14.

## See it

All images on this page were generated and recorded through Prompt Manager in a clean demo library.

### Your whole output folder, finally browsable

Masonry, grid or list, with search, categories, filters and saved views. Images and videos sit side by side, and selecting one shows its prompts and settings without leaving the page.

![Images page in grid layout](assets/readme/images-grid.webp)

### Open any image and see how it was made

The viewer shows the positive and negative prompts, tags, seed, steps, CFG, sampler and model next to the image.

![The viewer with an image on the left and its prompts and generation settings on the right](assets/readme/viewer.webp)

### Keep your hands on the keyboard

The viewer has shortcuts for moving between files, the slideshow, zoom, rotate, flip, full screen, details, download and delete. Press `?` to see them all.

![The viewer's keyboard shortcuts overlay](assets/readme/viewer-shortcuts.webp)

### Long libraries stay easy to work in

The selection bar stays pinned while you scroll, so you can keep picking images and act on them from anywhere. Back to top appears once you've scrolled down the page.

![Images page scrolled down, with the selection bar pinned at the top and a Back to top button](assets/readme/pinned-bar.webp)

### One positive prompt, many experiments

The Prompt library groups everything by positive prompt, so repeat runs and negative variants stay together instead of filling your list with near-duplicates.

![Prompt library cards showing each prompt's images, negative variants, runs and tags](assets/readme/library.webp)

### Watch your negatives branch on a timeline

Each prompt has its own page with a timeline of its negative variants. Click a red dot and the gallery filters to the images that negative made. Small dots mark repeat runs.

![Clicking through negative variants on the timeline filters the gallery below](assets/readme/timeline.anim.webp)

### Compare negatives term by term

Compare negatives shows two variants side by side and lists the terms one adds, so you can see what actually changed between runs.

![Two negative prompts side by side with the added terms highlighted](assets/readme/compare-negatives.webp)

### Auto-tag your images, and approve what sticks

Tag one image, a selection or your whole output with WD14 or JoyCaption. WD14 suggestions come with confidence scores, and you can review suggestions before anything is saved. Auto-tags belong to the image, so they never change your prompts.

![Tag image dialog with WD14 suggestions and their confidence scores](assets/readme/autotag.webp)

### Keep your vocabulary tidy

Tags & categories lists every tag and category with how often it's used. Rename, merge and delete tags, and preview a category change before you apply it.

![Tags and categories page](assets/readme/tags.webp)

### Group images your own way

Collections are hand-picked sets of images, on top of favorites and saved views. Add from the selection bar and they appear in the sidebar.

![A collection called Book cover ideas open in the workspace](assets/readme/collections.webp)

### Nothing gets guessed

When a file's metadata has more than one possible prompt, Prompt Manager doesn't pick for you. The file waits in Needs review, where you can choose the sampler path, link it to any prompt in your library, or leave it unresolved.

![Link to a prompt dialog searching the library from Needs review](assets/readme/needs-review.webp)

### Inspect any file, without importing it

Pick an image or video from your computer to read its prompts, settings and workflow. The file is read in your browser. It isn't uploaded or added to your library.

![Inspecting a local file in the viewer](assets/readme/inspect.webp)

### Send a prompt back to your canvas

Pick an open ComfyUI window and a node, then review the exact before and after. Nothing changes until you apply it.

![Send to ComfyUI dialog showing the before and after of a prompt](assets/readme/send.webp)

### Deletes you can review

Choose what happens to the files before anything is removed: remove the image from the library and leave the file on disk, or on Linux and macOS also move it to the system trash. On Windows, images can only be removed from the library. A safety backup of the database is on by default, and removed images can be brought back.

![Delete dialog offering to remove from the library only or move files to the trash](assets/readme/delete-review.webp)

### Maintenance in one place

System holds settings, image sources, auto-tag models, integrations, maintenance, backups and logs. Risky operations show a review first, and long ones run as jobs you can follow in Activity.

![System Maintenance section](assets/readme/system.webp)

![Activity page listing finished scans and tagging jobs](assets/readme/activity.webp)

### Works on your phone too

The workspace adapts to narrow screens, and touch is part of the browser test suite.

![Images page and viewer at phone width](assets/readme/phone.webp)

## The nodes

Six nodes, under **ComfyAssets > Prompts** in the Add Node menu. Pick the one that suits your workflow; they all share one library. Every screenshot below is a small real workflow that ran.

### Prompt Manager (Classic)

The drop-in replacement for CLIP Text Encode: same CLIP input, same CONDITIONING output, with the prompt saved as it runs. It also outputs the text as a STRING.

![Prompt Manager Classic node in a workflow, Classic renderer](assets/readme/node-classic.webp)

The same workflow in Nodes 2.0:

![Prompt Manager Classic node in a workflow, Nodes 2.0 renderer](assets/readme/node-classic-nodes2.webp)

### Prompt Manager (Focused)

An editor-first layout. Composition and metadata fold away, and **Browse library** lets you search, preview and use a saved prompt right on the node.

![Prompt Manager Focused node in a workflow](assets/readme/node-focused.webp)

### Prompt Manager (Split)

The same editor with the library always visible. Wide nodes put them side by side; narrow nodes stack them.

![Prompt Manager Split node in a workflow](assets/readme/node-split.webp)

### Prompt Manager (+/-)

Positive and negative in one node, saved as a pair. This is the node that feeds the negative-variant timeline. It outputs CONDITIONING and STRING for both sides.

![Prompt Manager plus/minus node in a workflow](assets/readme/node-pair.webp)

The same workflow in Nodes 2.0:

![Prompt Manager plus/minus node in a workflow, Nodes 2.0 renderer](assets/readme/node-pair-nodes2.webp)

### Prompt Manager Text

Saves the prompt and outputs a STRING instead of CONDITIONING, for workflows that send text to another node. Prepend and append inputs let you build the final text.

![Prompt Manager Text node feeding a CLIP Text Encode node](assets/readme/node-text.webp)

### Prompt Search List

Searches your library by text, category, tags and minimum rating, then outputs the matches as a list so ComfyUI runs each one. This workflow found three prompts tagged `mushroom` and made three images.

![Prompt Search List node producing three images](assets/readme/node-search.webp)

## Install

**Requirements:** ComfyUI and Python 3.12 or newer. `watchdog` and `Pillow` are the only dependencies of their own; the rest comes with ComfyUI.

Install from the Comfy Registry or ComfyUI-Manager (search for Prompt Manager), or by hand:

```bash
cd ComfyUI/custom_nodes/
git clone https://github.com/ComfyAssets/ComfyUI_PromptManager
pip install -r ComfyUI_PromptManager/requirements.txt
```

Restart ComfyUI. Click **PM** in the top bar, or **Web UI** on a node, or open `http://localhost:8188/prompt_manager/workspace`.

**Optional extras.** Nothing here is installed for you:

| To use | You need |
| --- | --- |
| WD14 auto-tagging (about 400 MB per model) | `onnxruntime`, or `onnxruntime-gpu` for CUDA |
| GGUF JoyCaption (about 4.5 GB) | `llama-cpp-python` |
| HuggingFace JoyCaption (about 16 GB) | `transformers`, `accelerate`, `bitsandbytes` |
| Prompts embedded in videos | `ffprobe` on PATH |
| [LoRA Manager](https://github.com/willmiao/ComfyUI-Lora-Manager) import | LoRA Manager installed; turn it on in System > Integrations |

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
- **Your library stays where it is** until you choose. The workspace offers **Move library**, which makes a verified copy in `user/__promptmanager/` and renames the old files instead of deleting them, or **Keep it here**.
- **Older images:** a one-time notice offers a metadata scan that links them to prompts and negatives. Scans don't count as new runs.
- **Going back isn't supported.** Workflows are now stored once, and an older version shows new records without their workflow or prompt graph. Back up first in System > Backup & export. See [the old README's changelog](README-legacy.md#changelog) for the manual way back.

## The old pages still work

They stay at their URLs, and System's Overview section links to them:

| Page | URL |
| --- | --- |
| Dashboard | `/prompt_manager/admin` |
| Tags | `/prompt_manager/admin#/tags` |
| Gallery | `/prompt_manager/gallery` |
| Metadata viewer | `/prompt_manager/gallery.html` |

The documentation for those pages is in [README-legacy.md](README-legacy.md).

## Known limits

- **Trash is Linux and macOS only.** Windows has no Recycle Bin option, because the bin can delete large files permanently without warning. On Windows you can only remove an image from the library, and the file stays on disk. Where a move to the trash isn't possible on Linux or macOS, the dialog lists why for each file.
- **Show in folder** opens the file manager on the machine running ComfyUI, not necessarily the one with your browser.
- **Returned images** get their prompt links back, but not their tags, categories, collections or favorites.
- **Inspect a file** doesn't add the file to your library.
- **Leave unresolved is permanent.** A file you leave unresolved in Needs review isn't offered again unless its bytes change.
- **Pair prompts saved by earlier versions** stay Uncategorized. You can set the category with Edit metadata.

## How it's tested

| Layer | What runs |
| --- | --- |
| Python | Over 200 test files on Linux and Windows with Python 3.12 to 3.14, with an 85% coverage gate |
| Frontend | About 100 unit test files, with coverage floors per file |
| Browser | Over 60 Playwright spec files in Chromium, Firefox, WebKit and touch emulation |
| Real ComfyUI | A pinned CPU ComfyUI, started in Classic and Nodes 2.0, running real workflows |

Linux runs on every pull request and in the merge queue. Windows runs on every push to main and nightly. macOS is not tested; it should work, but support is best-effort. To run the tests yourself, see [TESTING.md](TESTING.md).

## License

MIT. See [LICENSE](LICENSE).
