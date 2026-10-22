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
