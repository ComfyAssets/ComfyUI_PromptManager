"""Measure the server's resident memory under a busy Images/Library session (#523).

Manual only, Linux only (it reads ``/proc`` and glibc's ``malloc_info``); never
run in CI. It starts the production API inside the disposable browser test
server (``tests/browser/server.py``: its own temporary library, database and
output folder; never a live ComfyUI) and drives it for ``--minutes``:

- ``--clients`` threads loop over: a workspace grid page of 100 images, 40 of
  its thumbnails, 6 inspector reads and 6 full-file reads, the Library list
  with 3 entries' history and images, and the recent prompt and image lists;
- every minute, ``--batch`` new PNGs (832 to 1536 px, ComfyUI-shaped
  metadata) are copied into the output folder, then a scan job and a
  thumbnail job run to completion.

Every 5 s the server samples its RSS, Python heap (tracemalloc), threads and
glibc arena count. The test server pins glibc's mmap threshold (#516); this
driver turns that off so the production allocator behaviour is measured,
unless ``--mallopt`` asks for the pinned comparison run.

    python tools/perf/memory_drive.py --label after --minutes 12
    python tools/perf/memory_drive.py --repo /path/to/other/checkout --label before

The checkout under ``--repo`` (default: this one) is the code measured; the
driver itself only needs Pillow. The last line printed is a Markdown table row.
"""

import argparse
import ctypes
import json
import os
import random
import shutil
import subprocess  # nosec B404 - starts this script's own server child
import sys
import tempfile
import threading
import time
import tracemalloc
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
PORT_ANNOUNCEMENT = "PM_TEST_SERVER_PORT="
OUTPUT_ANNOUNCEMENT = "output="
SAMPLE_SECONDS = 5
MEDIA_FILES = 120
MEDIA_SIDES = (832, 1024, 1216, 1536)
REQUEST_TIMEOUT = 120
JOB_POLL_LIMIT = 600
FINAL_STATES = ("finished", "cancelled", "failed")
M_MMAP_THRESHOLD = -3


# ── the server child ────────────────────────────────────────────────────


def _arena_count(libc):
    """glibc's arena count (``<heap nr=`` entries of ``malloc_info``)."""
    buffer, size = ctypes.c_char_p(), ctypes.c_size_t()
    stream = libc.open_memstream(ctypes.byref(buffer), ctypes.byref(size))
    libc.malloc_info(0, stream)
    libc.fclose(stream)
    text = ctypes.string_at(buffer, size.value).decode()
    libc.free(ctypes.cast(buffer, ctypes.c_void_p))
    return text.count("<heap nr=")


def _status():
    with open("/proc/self/status", encoding="ascii") as status:
        return {
            line.split(":")[0]: int(line.split()[1])
            for line in status
            if line.startswith(("VmRSS", "RssAnon", "Threads"))
        }


def _sample_forever(libc, samples_path):
    start = time.monotonic()
    announced = False
    with open(samples_path, "a", buffering=1, encoding="utf-8") as out:
        while True:
            folder_paths = sys.modules.get("folder_paths")
            if not announced and hasattr(folder_paths, "get_output_directory"):
                out.write(OUTPUT_ANNOUNCEMENT + folder_paths.get_output_directory())
                out.write("\n")
                announced = True
            status = _status()
            out.write(
                json.dumps(
                    {
                        "t": round(time.monotonic() - start),
                        "rss": status["VmRSS"] >> 10,
                        "anon": status["RssAnon"] >> 10,
                        "heap": tracemalloc.get_traced_memory()[0] >> 20,
                        "threads": status["Threads"],
                        "arenas": _arena_count(libc),
                    }
                )
                + "\n"
            )
            time.sleep(SAMPLE_SECONDS if announced else 0.5)


def serve(repo, samples_path, mallopt):
    """Run the browser test server of *repo* in this process, sampled."""
    sys.path.insert(0, repo)
    os.chdir(repo)
    tracemalloc.start(1)
    libc = ctypes.CDLL(None)
    libc.open_memstream.restype = ctypes.c_void_p
    libc.open_memstream.argtypes = [
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.POINTER(ctypes.c_size_t),
    ]
    libc.fclose.argtypes = [ctypes.c_void_p]
    libc.malloc_info.argtypes = [ctypes.c_int, ctypes.c_void_p]
    libc.free.argtypes = [ctypes.c_void_p]
    if mallopt:
        libc.mallopt(M_MMAP_THRESHOLD, 128 * 1024)
    threading.Thread(
        target=_sample_forever, args=(libc, samples_path), daemon=True
    ).start()
    from tests.browser import server

    if not mallopt:
        server.pin_mmap_threshold = lambda: False  # production allocator
    server.main(["--port", "0", "--exit-on-stdin-eof"])


# ── the driver ──────────────────────────────────────────────────────────


def make_media(folder, count=MEDIA_FILES, seed=523):
    """Synthetic ComfyUI-shaped PNGs (smooth noise, prompt and workflow)."""
    from PIL import Image, PngImagePlugin

    rng = random.Random(seed)
    os.makedirs(folder, exist_ok=True)
    for index in range(count):
        side = MEDIA_SIDES[index % len(MEDIA_SIDES)]
        small = Image.frombytes(
            "RGB", (64, 64), bytes(rng.randrange(256) for _ in range(64 * 64 * 3))
        )
        image = small.resize((side, side), Image.BICUBIC)
        text = f"synthetic scene {index}, soft light, detailed"
        prompt = {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": index,
                    "steps": 20,
                    "cfg": 7.0,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": 1.0,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0],
                },
            },
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "model.safetensors"},
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": side, "height": side, "batch_size": 1},
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": text, "clip": ["4", 1]},
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "blurry", "clip": ["4", 1]},
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "img", "images": ["8", 0]},
            },
        }
        workflow = {
            "nodes": [
                {"id": int(k), "type": v["class_type"]} for k, v in prompt.items()
            ]
        }
        info = PngImagePlugin.PngInfo()
        info.add_text("prompt", json.dumps(prompt))
        info.add_text("workflow", json.dumps(workflow))
        image.save(
            os.path.join(folder, f"m{index:03d}.png"), pnginfo=info, compress_level=4
        )


class Driver:
    def __init__(self, base, output, media, batch):
        self.base, self.output, self.media, self.batch = base, output, media, batch
        self.errors, self.batches = [], 0
        self.names = sorted(os.listdir(media))

    def call(self, method, path, body=None, raw=False):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(  # nosec B310 - loopback test server
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(  # nosec B310
                request, timeout=REQUEST_TIMEOUT
            ) as response:
                payload = response.read()
        except Exception as error:  # keep driving; errors are counted
            self.errors.append(f"{method} {path.split('?')[0]}: {error}")
            return None
        return payload if raw else json.loads(payload or b"{}")

    def add_batch(self):
        self.batches += 1
        folder = os.path.join(self.output, f"batch{self.batches:03d}")
        os.makedirs(folder)
        for index in range(self.batch):
            name = self.names[(self.batches * self.batch + index) % len(self.names)]
            shutil.copyfile(
                os.path.join(self.media, name),
                os.path.join(folder, f"img_{self.batches:03d}_{index:03d}.png"),
            )

    def wait_job(self, response):
        job = ((response or {}).get("job") or {}).get("id")
        for _ in range(JOB_POLL_LIMIT if job else 0):
            state = self.call("GET", f"/prompt_manager/jobs/{job}")
            if not state or (state.get("job") or {}).get("state") in FINAL_STATES:
                return
            time.sleep(1)

    def scan_and_thumbnails(self):
        self.add_batch()
        self.wait_job(self.call("POST", "/prompt_manager/jobs/scan", {}))
        self.wait_job(self.call("POST", "/prompt_manager/jobs/thumbnails", {}))

    def browse(self, _index=None):
        offset = random.randint(0, 3) * 100  # nosec B311 - load shape only
        page = self.call(
            "GET",
            f"/prompt_manager/images/output?mode=workspace&limit=100&offset={offset}",
        )
        images = [i for i in (page or {}).get("images") or [] if isinstance(i, dict)]
        urls = [i.get("thumbnail_url") or i.get("url") for i in images]
        urls = [u for u in urls if u]
        for url in random.sample(urls, min(40, len(urls))):
            self.call("GET", url, raw=True)
        for image in random.sample(images, min(6, len(images))):
            self.inspect(image)
            if image.get("url"):
                self.call("GET", image["url"], raw=True)
        library = self.call("GET", "/prompt_manager/library?limit=100") or {}
        entries = [e for e in library.get("items") or [] if isinstance(e, dict)]
        for entry in random.sample(entries, min(3, len(entries))):
            prompt_id = entry.get("id") or entry.get("positive_id")
            if prompt_id is not None:
                self.call(
                    "GET", f"/prompt_manager/library/{prompt_id}/history", raw=True
                )
                self.call(
                    "GET", f"/prompt_manager/library/{prompt_id}/images", raw=True
                )
        self.call("GET", "/prompt_manager/recent?limit=50", raw=True)
        self.call("GET", "/prompt_manager/images/recent?limit=50", raw=True)

    def inspect(self, image):
        relative, index = image.get("path"), image.get("root_index")
        if not relative or not isinstance(index, int):
            return
        query = {"root": str(index)}
        if image.get("root_scope"):
            query["root_scope"] = image["root_scope"]
        self.call(
            "GET",
            "/prompt_manager/images/inspect/"
            + urllib.parse.quote(relative)
            + "?"
            + urllib.parse.urlencode(query),
            raw=True,
        )


def _wait_for_output(samples_path, process):
    while process.poll() is None:
        with open(samples_path, encoding="utf-8") as samples:
            for line in samples:
                if line.startswith(OUTPUT_ANNOUNCEMENT):
                    return line[len(OUTPUT_ANNOUNCEMENT) :].strip()
        time.sleep(0.2)
    raise RuntimeError("the server exited before it started")


def summarize(samples_path, label, batches, errors):
    rows = []
    with open(samples_path, encoding="utf-8") as samples:
        for line in samples:
            if line.startswith("{"):
                rows.append(json.loads(line))
    if not rows:
        raise RuntimeError("no samples were taken")
    rss = [row["rss"] for row in rows]
    late = [row for row in rows if row["t"] >= 120] or rows
    end = rows[-1]
    return (
        f"| {label} | {rss[0]} → {end['rss']} | {min(r['rss'] for r in late)}–"
        f"{max(r['rss'] for r in late)} | {max(rss)} | {rows[0]['heap']} → "
        f"{end['heap']} | {max(r['threads'] for r in rows)} | "
        f"{max(r['arenas'] for r in rows)} | {batches} | {len(errors)} |"
    )


def drive(args):
    work = args.out or tempfile.mkdtemp(prefix="pm-memory-")
    media = os.path.join(work, "media")
    if not os.path.isdir(media):
        print(f"Writing {MEDIA_FILES} PNGs to {media}", flush=True)
        make_media(media)
    samples_path = os.path.join(work, f"{args.label}.samples")
    open(samples_path, "w", encoding="utf-8").close()
    command = [sys.executable, os.path.abspath(__file__), "--serve", samples_path]
    command += ["--repo", os.path.abspath(args.repo)]
    command += ["--mallopt"] if args.mallopt else []
    with open(os.path.join(work, f"{args.label}.stderr"), "w") as stderr:
        process = subprocess.Popen(  # nosec B603
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr
        )
        try:
            port = None
            for line in process.stdout:
                text = line.decode(errors="replace")
                if text.startswith(PORT_ANNOUNCEMENT):
                    port = int(text[len(PORT_ANNOUNCEMENT) :])
                    break
            if port is None:
                raise RuntimeError("the server announced no port")
            output = _wait_for_output(samples_path, process)
            driver = Driver(f"http://127.0.0.1:{port}", output, media, args.batch)
            run(driver, args)
        finally:
            process.stdin.close()
            process.wait(60)
    print(f"Samples: {samples_path}")
    print(
        "| run | RSS start → end (MB) | after 2 min | max | heap (MB) | threads "
        "| arenas | batches | errors |"
    )
    print(summarize(samples_path, args.label, driver.batches, driver.errors))


def run(driver, args):
    driver.call("POST", "/__test__/reset", {})
    driver.scan_and_thumbnails()
    deadline = time.monotonic() + args.minutes * 60
    next_batch = time.monotonic() + 60
    with ThreadPoolExecutor(args.clients) as pool:
        while time.monotonic() < deadline:
            list(pool.map(driver.browse, range(args.clients)))
            if time.monotonic() > next_batch:
                driver.scan_and_thumbnails()
                next_batch = time.monotonic() + 60
    time.sleep(SAMPLE_SECONDS + 1)  # one more sample after the load stops


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=REPO, help="checkout to measure")
    parser.add_argument("--label", default="run")
    parser.add_argument("--minutes", type=float, default=12)
    parser.add_argument("--clients", type=int, default=6)
    parser.add_argument("--batch", type=int, default=60, help="new PNGs a minute")
    parser.add_argument("--out", help="work folder (media, samples); default temp")
    parser.add_argument(
        "--mallopt", action="store_true", help="pin the mmap threshold (comparison)"
    )
    parser.add_argument("--serve", metavar="SAMPLES", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not sys.platform.startswith("linux"):
        parser.error("Linux only: it reads /proc and glibc's malloc_info")
    if args.serve:
        serve(os.path.abspath(args.repo), args.serve, args.mallopt)
    else:
        drive(args)


if __name__ == "__main__":
    main()
