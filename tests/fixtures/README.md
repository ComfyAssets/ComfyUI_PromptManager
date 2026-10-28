# Browser media fixtures

`tiny.webm` is an original, silent, one-second VP8 color test pattern generated
for this repository. It contains no user media and is dedicated to the public
domain under CC0-1.0.

Reproduce with FFmpeg:

```sh
ffmpeg -f lavfi -i testsrc=size=32x32:rate=4:duration=1 \
  -c:v libvpx -pix_fmt yuv420p -an tiny.webm
```

Browser tests use the committed file; FFmpeg is not required to run them.

# Local Inspect header fixtures

`local-inspect/tiny.jpg` (48x32) and `local-inspect/tiny.webp` (40x24) are original
synthetic FFmpeg color test patterns, under 4 KB, dedicated to the public domain
under CC0-1.0. `tests/js/png-local-reader.test.js` reads their dimensions with the
browser header reader and `tests/test_local_inspect_parity.py` checks the same two
files against Pillow.

Reproduce with FFmpeg:

```sh
ffmpeg -f lavfi -i testsrc=size=48x32:rate=1 -frames:v 1 -q:v 10 tiny.jpg
ffmpeg -f lavfi -i testsrc=size=40x24:rate=1 -frames:v 1 -c:v libwebp -quality 40 tiny.webp
```

# Local Inspect video fixtures

`local-inspect/core-faststart.mp4`, `core-moov-end.mp4`, `core.mov`, `core.m4v`,
`core.mkv`, `core-tags.webm`, `legacy-comment.mp4` and `legacy-comment.webm` are
0.2-second 16x16 black clips (each under 4 KB) carrying an original synthetic
ComfyUI graph in their container tags, dedicated to the public domain under
CC0-1.0. The `core-*` files carry one tag per key as core SaveVideo/SaveWEBM write
them (MP4/MOV/M4V through `-movflags use_metadata_tags`; Matroska upper-cases the
names); `core-faststart.mp4` also carries `parameters` and
`comfyassets_prompt_pairs`. The `legacy-comment.*` files carry the old
VideoHelperSuite `comment` JSON envelope (an ilst `©cmt` atom in MP4, a `COMMENT`
tag in WebM). `video-tags-v1.json` records `ffprobe -show_entries format_tags -of
json` for each file (ffmpeg 9.0.2); `tests/test_utils_video_metadata.py`
`VideoFixtureFfprobeTests` re-checks it when ffprobe is installed, and
`tests/js/workspace-local-video.test.js` plus `tests/test_local_inspect_parity.py`
hold the browser video reader to the same maps.

Reproduce with FFmpeg, from `tests/fixtures/local-inspect`:

```sh
PROMPT='{"3":{"class_type":"KSampler","inputs":{"seed":7,"steps":20,"cfg":8.0,"sampler_name":"euler","scheduler":"normal","denoise":1.0,"model":["4",0],"positive":["6",0],"negative":["7",0],"latent_image":["5",0]}},"4":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"synthetic-v1.safetensors"}},"5":{"class_type":"EmptyLatentImage","inputs":{"width":512,"height":512,"batch_size":1}},"6":{"class_type":"CLIPTextEncode","inputs":{"text":"a red fox in snow, detailed fur","clip":["4",1]}},"7":{"class_type":"CLIPTextEncode","inputs":{"text":"blurry, lowres","clip":["4",1]}},"8":{"class_type":"VAEDecode","inputs":{"samples":["3",0],"vae":["4",2]}},"9":{"class_type":"SaveVideo","inputs":{"filename_prefix":"video/synthetic","video":["8",0]}}}'
WORKFLOW='{"last_node_id":9,"nodes":[{"id":3,"type":"KSampler","widgets_values":[7,"fixed",20,8.0,"euler","normal",1.0]},{"id":6,"type":"CLIPTextEncode","widgets_values":["a red fox in snow, detailed fur"]},{"id":7,"type":"CLIPTextEncode","widgets_values":["blurry, lowres"]}],"links":[],"version":0.4}'
PARAMETERS=$'a red fox in snow, detailed fur\nNegative prompt: blurry, lowres\nSteps: 20, Sampler: Euler, CFG scale: 8.0, Seed: 7, Size: 512x512'
PAIRS='{"version":1,"nodes":{"6":{"records":[{"positive_text":"a red fox in snow, detailed fur","negative_text":"blurry, lowres","negative_effective_text":"blurry, lowres","prepend_text":"","append_text":""}]}}}'
COMMENT=$(PROMPT="$PROMPT" WORKFLOW="$WORKFLOW" python3 -c 'import json,os; print(json.dumps({"prompt": os.environ["PROMPT"], "workflow": json.loads(os.environ["WORKFLOW"])}))')
SRC=(-v quiet -y -f lavfi -i "color=c=black:s=16x16:d=0.2")
ffmpeg "${SRC[@]}" -movflags use_metadata_tags+faststart -metadata prompt="$PROMPT" -metadata workflow="$WORKFLOW" -metadata parameters="$PARAMETERS" -metadata comfyassets_prompt_pairs="$PAIRS" core-faststart.mp4
ffmpeg "${SRC[@]}" -movflags use_metadata_tags -metadata prompt="$PROMPT" -metadata workflow="$WORKFLOW" core-moov-end.mp4
ffmpeg "${SRC[@]}" -movflags use_metadata_tags -metadata prompt="$PROMPT" -metadata workflow="$WORKFLOW" core.mov
ffmpeg "${SRC[@]}" -movflags use_metadata_tags -metadata prompt="$PROMPT" -metadata workflow="$WORKFLOW" core.m4v
ffmpeg "${SRC[@]}" -metadata prompt="$PROMPT" -metadata workflow="$WORKFLOW" core.mkv
ffmpeg "${SRC[@]}" -c:v libvpx -metadata prompt="$PROMPT" -metadata workflow="$WORKFLOW" core-tags.webm
ffmpeg "${SRC[@]}" -metadata comment="$COMMENT" legacy-comment.mp4
ffmpeg "${SRC[@]}" -c:v libvpx -metadata comment="$COMMENT" legacy-comment.webm
for f in core-faststart.mp4 core-moov-end.mp4 core.mov core.m4v core.mkv core-tags.webm legacy-comment.mp4 legacy-comment.webm; do
  ffprobe -v quiet -show_entries format_tags -of json "$f"   # -> video-tags-v1.json "fixtures"[f] = .format.tags
done
```

# Local Inspect projection cases

`local-inspect/projection-cases.json` holds synthetic record sets (prompt, workflow,
parameters and pair-record texts, optional `record_formats`, a video `carrier_text`
and `limited` records) that exercise every branch of the server's inspection
projection: Python's Decimal spellings and exact-construction limits, the 4300-digit
integer limit, duplicate keys, NaN/Infinity, the depth-64 and node ceilings, prompt
node key filtering and the carrier rule. `records: null` stands for an unreadable
file. `expect` pins both graph statuses; `needs` names an interpreter feature a case
depends on. `tests/js/workspace-local-projection.test.js` runs them through
`web/js/workspace/local-projection.mjs`, and `tests/test_local_inspect_parity.py`
asserts that its answer equals `utils.inspection_response._metadata` byte for byte.
All texts are original synthetic content, dedicated to the public domain under
CC0-1.0.

# Inspection parity fixtures

`inspection_parity/` holds small synthetic PNGs (`f01b.png` ... `f24.png`, each
under 32 KiB) and `vectors.json`, used by the #240 inspection parity tests. Every
graph, prompt text, model name and filename prefix in them is original synthetic
content written for this repository. Nothing comes from user output. They are
dedicated to the public domain under CC0-1.0.

`tests/inspection_parity.py` builds every fixture from code. It writes the
`prompt` and `workflow` records the way core ComfyUI `SaveImage` does (Pillow
`PngInfo.add_text`, `compress_level=4`, a 64x64 constant colour image), and
splices extra raw `tEXt` chunks with real CRC32 for the duplicate-chunk shapes
`f15` and `f16`. The larger shapes `f01`, `f04` and `f05` are built at test
time instead of committed. Reproduce the committed files with:

```sh
python3 -m tests.inspection_parity --write tests/fixtures/inspection_parity
```

`tests/test_inspection_parity_fixtures.py` compares the decoded text chunks with
the builders (not bytes, because zlib output differs between platforms).

`vectors.json` holds an `inspection` and a `default` vector for every fixture,
committed or built at test time (test-time fixtures are built into a temporary
directory). `inspection` is the workspace inspector path, with the facts the
workspace shows. `default` pins only the resolver's default profile, run on the
role records decoded from the PNG text chunks. Ingestion's own readers are covered
by their own suites. The expectations start as the observed baseline. A change
that alters an outcome edits the vector in the same commit.
`tests/js/legacy-metadata-characterization.test.js` pins what
the legacy viewers show on the same files.

# Legacy contract snapshot

`legacy_contract_snapshot.json` records what the legacy admin and gallery pages
talk to while System S13 (#330) moves their tools to native System: every
registered route as `"<METHOD> <path>"` (`routes`) and the key paths of
`GET /prompt_manager/settings` to depth 2 (`settings_keys`; list items give the
union of their keys as `name[].key`). `tests/test_legacy_contract_snapshot.py`
compares a temp-database API against it. Record it again only for an intended
change, and say why in the PR body:

```sh
PM_RECORD_LEGACY_SNAPSHOT=1 python3 -m unittest tests.test_legacy_contract_snapshot
```

Cutover (#334) removes the test and this fixture.
