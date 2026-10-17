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
