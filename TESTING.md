# Testing and merge confidence

Routine changes should merge after the required checks pass and the diff is
reviewed. Add a regression test for each bug; prove it fails before the fix.

## What runs

| Layer | Real components | Replaced boundaries |
| --- | --- | --- |
| Python tests | Domain logic, SQLite, API handlers, filesystem checks | ComfyUI host and ML runtimes where needed |
| JavaScript tests | Frontend modules and controller methods | DOM/network fixtures for focused edge cases |
| Viewer checks | Production Viewer.js and frontend methods in Chromium desktop/touch | Controlled images and page fixtures |
| Real ComfyUI | Pinned CPU host, actual extension, node definitions, widgets, routes, SQLite and real execution lifecycle | Stock UI cases isolate host writes; the execution suite queues deterministic test-only nodes without models |
| Page workflows | Production HTML, startup, click handlers, HTTP routes, SQLite, PNG files, thumbnails | ComfyUI host services and the tagging model |

Page workflows cover save/edit/reload, duplicate-edit rejection, gallery image
identity, Tags selection and viewer cleanup, delayed responses, auto-tag review
and persistence, and infinite scrolling without replacing existing cards.
Data safety workflows download and upload actual SQLite backups, verify restored
prompts/tags/image links, reject corrupt uploads without changing live data, and
exercise cancellation and network-failure recovery for restores and deletion.
Duplicate cleanup verifies only the selected file disappears; prompt deletion
verifies the other prompt and original image files survive.
They run in Chromium, Firefox, WebKit, and Chromium touch emulation. No GPU,
model downloads, running ComfyUI instance, or personal configuration is required.
The server binds to localhost and creates disposable directories and a database;
its reset endpoint exists only in the test harness.

## Run locally

Use a Python environment containing `requirements-dev.txt` and Node.js 24:

```sh
python -m tests.run_suite -q        # what CI runs; see "Time limits and hangs"
npm ci --prefix tools/vendor --ignore-scripts
npx --prefix tools/vendor playwright install --with-deps chromium firefox webkit
npm run --prefix tools/vendor test
npm run --prefix tools/vendor test:coverage
npm run --prefix tools/vendor test:workflows -- --project firefox --project webkit
```

`python -m unittest discover -s tests` still works and finds the same tests;
it just has no hang watchdog.

`PM_TEST_PYTHON` selects the Python executable; set it when Python is outside
PATH. Every Playwright worker starts its own `tests/browser/server.py --port 0`,
which binds a port the OS picks and prints it as `PM_TEST_SERVER_PORT=<n>`, with
its own temp root and database (`tests/browser/fixtures.mjs`), so
parallel workers and concurrent runs never share state. `PM_TEST_WORKERS` sets the
worker count (default 1 under `CI`, otherwise a quarter of the CPUs, at most 8).
Test servers are never reused across runs. The launcher holds each server's
stdin open and the server (`--exit-on-stdin-eof`) shuts down cleanly, removing its
temp root, when that pipe closes, so a worker killed without teardown leaves no
server behind. A test that fails gets the stderr its server wrote during the test
attached to the report as `server-stderr` (an aiohttp traceback behind a 500).
A `/__test__/.../reset` still running after 2 s writes a report to the job log,
passing test or not: every thread's stack taken while it waits, the default
executor's threads and queue, the open connections and WAL size, then the total
and how long each executor call queued and ran. Its lines start with
`pm-slow-reset| ` (`tests/browser/slow_reset_watch.py`). The report never reads a
connection another thread is using: one that is busy inside SQLite would block
that read with the GIL held and freeze the whole server.

The coverage run can be split the way CI splits it, and each shard keeps the raw
V8 files it writes under `coverage/js/raw`:

```sh
PM_TEST_WORKERS=8 npm run --prefix tools/vendor test:coverage            # whole run, one report
npm run --prefix tools/vendor test:coverage:shard -- 1/3                 # node units, viewer suite, workflows 1/3
npm run --prefix tools/vendor test:coverage:shard -- 2/3                 # workflows 2/3 (likewise 3/3)
npm run --prefix tools/vendor coverage:merge                             # report and baseline check of every raw file
npm run --prefix tools/vendor test:workflows -- --project webkit --shard 2/2
```

`coverage:merge` refuses an empty raw directory; it does not count shards, so run
every shard first (CI's merge job checks all three arrived). Measured on a 32-CPU
machine in the pinned image (2026-10-21): `test:coverage` takes about 5 minutes
with 8 workers and 24 minutes with 1; all four projects, 8 workers, each test 3
times (6132 tests), 24 minutes. A CI-sized run (`docker run --cpus=4`,
`PM_TEST_WORKERS=3`, all four projects once, 2044 tests) takes 25.5 minutes.

Specs must pass beside each other, not only alone. Wait for the page's own
signal, never for time: a rendering frame (`requestAnimationFrame`) for layout
work queued by a render or a scroll, a response or a visible state for data. A
timer that "usually" covers a frame is a race on a loaded runner. Library
virtualization specs settle with `libraryObservation.settled()` (two consecutive
frames with no Library frame queued). To look for such races, repeat a spec under
load (`--workers 8 --repeat-each 10`, and in the image with `--cpus=1` or `2`).
CPU limits do not reproduce disk-bound timeouts (backup, restore). For those,
throttle writes in the image: with rootful Docker on a host with cgroup v2, add
`--device-write-iops /dev/<disk>:20` to the `docker run`, where `<disk>` is the
whole disk (not a partition) holding Docker's data root, because the test server
keeps its database in the container's `/tmp`. On a plain partition,
`lsblk -no pkname "$(df --output=source "$(docker info -f '{{.DockerRootDir}}')" | tail -1)"`
prints it; on LVM or LUKS, `lsblk -s` walks down to the disk. A spec that waits
on a timer then fails at its 5 s expect. A spec that waits on the response can
still fail at the 30 s test timeout when several workers share the throttled
disk; that means the disk is too slow for the test budget, not that the spec is
wrong.

On unsupported Linux
distributions, browser system dependencies may need manual installation. For a
quick browser run, use `test:workflows -- --project chromium`.

CI runs the browser jobs inside the pinned image
`mcr.microsoft.com/playwright:v<@playwright/test version>-noble`
(`tests/vendor/browser-image.test.mjs` keeps the tag in step with the lockfile).
Concept comparisons measure geometry, and geometry follows the fonts a browser
falls back to: the concept names Inter, which neither the image nor a stock
runner ships. On a plain `ubuntu-latest` runner the fallback is DejaVu Sans,
whose wider metrics make the concept's own Tags table overflow a 390px viewport,
so the 390px comparisons are only meaningful in the pinned image. To reproduce CI,
run the suites in that image.

## Managed real ComfyUI

The shared engine comes from an exact commit of
[ComfyAssets Toolkit](https://github.com/ComfyAssets/ComfyAssets-Toolkit), recorded
in `tools/vendor/package.json` and its integrity-checked lockfile. The local
adapter sets PromptManager's database/configuration and monitoring policy;
source discovery, coverage mapping/gates and process lifecycle are shared code.
KikoTools remains the reference and is unchanged by this integration.

Use a **dedicated disposable** ComfyUI checkout at the revision in
`tools/vendor/comfyui-runtime.json`. With a separate Python environment, install
that checkout's requirements, this repo's `requirements-dev.txt`, and the exact
CPU torch/torchvision pins from the JSON (using PyTorch's CPU wheel index).
Install Chromium using the Playwright command above, then run:

```sh
PM_COMFY_ROOT=/path/to/disposable/ComfyUI \
PM_TEST_PYTHON=/path/to/cpu-env/bin/python \
npm run --prefix tools/vendor test:comfyui
```

CI performs this setup automatically. `PM_COMFY_URL` can select another
`http://127.0.0.1:PORT/` address (default port 8189). This is an owned managed
server: a running host is never reused. Do not point it at your live checkout.
One port serves one run. The launcher refuses a port that is already in use and
reports ready only when `/system_stats` names its own base directory, so two runs
started together on 8189 fail at startup ("already in use" or "answered by a
ComfyUI host this run did not start") instead of one run testing the other's
host and losing it mid-test (`ECONNREFUSED`, `ERR_CONNECTION_REFUSED`, Network
Error). Every test checks the answering host again. Agents sharing a machine
give each run its own `PM_COMFY_URL` port.
The launcher verifies the upstream SHA and clean tracked files, snapshots only
tracked PromptManager files, and creates temporary user/input/output/database/
configuration/log directories. Personal untracked files are excluded. It runs
CPU/offline with only PromptManager enabled and removes owned state on shutdown.
The upstream checkout must also be disposable: ComfyUI may initialize local
runtime files there. Browser host-settings writes are intercepted; actual
PromptManager routes use the disposable host and database.

`real-comfyui-reports` retains startup logs, version attachments, screenshots and
traces for 14 days. Update upstream/frontend/CPU pins together after reviewing
and running both startup projects; never replace the SHA with a moving branch.
Update the toolkit via an exact reviewed commit archive and commit both npm
manifest and lockfile. No toolkit or npm installation is needed by end users.

## Coverage and failures

Python CI enforces 85% combined statement/branch coverage. Frontend coverage merges
Node V8 unit coverage with Chromium page-workflow coverage through c8. Every
`web/js/**/*.js` and `web/js/**/*.mjs` file appears, including untouched files at zero coverage. The
controller tests provide V8 filenames so their executed code is counted too.

`tools/vendor/frontend-coverage-baseline.json` holds a floor for every file and
metric. CI rejects regressions, missing measurements, and new files without a
reviewed baseline. These floors reflect current coverage, with two percentage
points of allowance for runtime/timing differences; they do not claim 80% coverage
of the large controllers. New files need at least 80% line coverage when added via
the baseline updater. Raise floors with `npm run --prefix tools/vendor
coverage:update`; it reruns the suites and cannot lower an existing floor.
Review baseline changes like code changes.

`npm run --prefix tools/vendor coverage:baseline-check` runs in well under a
second without a browser and fails, naming the files, when a measured
`web/js` file has no baseline entry or the baseline lists a file that no longer
exists. It uses the gate's own `discoverSources`, and `npm test` runs the same
check, so run it before pushing a new or deleted frontend file instead of
waiting for the `frontend coverage` jobs.

The `viewerjs` check runs on every PR, including backend-only changes. It is an
aggregate job. It requires the three `frontend coverage (shard N/3)` jobs and
`frontend coverage (merge)` to succeed on every event. The four
`page workflows (firefox|webkit, shard N/2)` jobs run only in the merge queue,
on `main` and on `workflow_dispatch` (see "Pull requests and the merge queue").
`viewerjs` requires them to be skipped on a pull request and to succeed
everywhere else. Any other failed, cancelled or skipped job fails it. Every browser job runs with `PM_TEST_WORKERS: 3` (the public
runners have 4 vCPUs). Each coverage shard runs `test:coverage:shard -- N/3`
(shard 1 also runs the node units and the viewer suite, shard 3 the vendor pins
and tooling tests) and uploads its raw V8 files as `coverage-raw-N` (kept 3
days). The merge job downloads them, fails unless all three arrived with raw
files and without clashing names, then runs `coverage:merge` (report plus
baseline check). `tests/vendor/ci-workflow.test.mjs` checks this shape. GitHub
artifacts named `frontend-test-reports` contain the merged HTML coverage and
JSON summary; `frontend-traces-shard-N` hold the Chromium/touch failure
screenshots/traces and `browser-traces-<browser>-shard-N` the other browsers'
traces. Open traces with Playwright's `show-trace` tool.

Every `npm ci` first restores npm's download cache (`~/.npm`), keyed on the vendor
lockfile and the job's pinned image tag (`tests/vendor/ci-workflow.test.mjs` checks
the key names the same tag as the job's `container.image`). Without it, `npm ci` stalled for about 300 s
(npm's fetch timeout) in most of the eight concurrent jobs; with a hit it takes
0 to 2 s. A lockfile change misses the cache once, so expect one slow run after it.
Measured on PR #274 (run 39725176320 at cbc8a61, cache hit): the whole workflow
took 8m38s, against about 33 minutes with one worker and no shards. Jobs: coverage
shards 7m34s/6m04s/5m22s (tests 6m38s/4m53s/4m04s), merge 54s, firefox shards
7m57s/5m21s (tests 7m08s/4m34s), webkit shards 8m16s/5m03s (tests 7m26s/3m58s).
Browser jobs keep a 20-minute limit: the slowest job is about 8.5 minutes, a cache
miss adds up to 5 to `npm ci`, and runner speed varies (the first sharded run was
cancelled at a 12-minute limit).
Playwright shards by test count, whole files at a time, so shard 1 carries the
slow concept-comparison specs (`*-concept.spec.mjs`, alphabetically first) and
runs longer; the remedy, if it starts to matter, is more shards, not reordering.

The real-host suite covers both Classic and Nodes 2.0 startup, switching both
ways, actual database prompt selection, execution-text serialization, workflow
reload, widget/style cleanup, node schemas and standalone-page opening. It does
not certify every existing feature, arbitrary host versions,
GPU/model changes, cross-pack installation or visual design changes. Those still
need targeted validation.

## Time limits and hangs

CI and the pre-push hook run the Python suite as `python -m tests.run_suite`
(`-v`, `-q`, `-s START` and `-p PATTERN` as in `unittest discover`), and the
coverage job as `python -m coverage run -m tests.run_suite`. The runner
(`tests/run_suite.py`) discovers the same tests with the same `sys.path` and
warning filters as `python -m unittest discover -s tests`, exits 0, 1, or 5 when
no tests ran (Python 3.12+ behaviour, on every version), and adds a watchdog: it writes `start: <test id>` to stderr before every test, at every
verbosity, and arms `faulthandler.dump_traceback_later`. If a test, its teardown,
its event-loop shutdown or interpreter exit stops making progress, the stack of
every thread goes to stderr (`Timeout (0:02:00)!` then `Thread 0x...` blocks) and
the process exits non-zero.

- `PM_TEST_HANG_LIMIT` (default 120 s) bounds each test from its `start:` line to
  the next one, including the class and module fixtures (`tearDownClass`,
  `setUpModule`, ...) that run in between. `PM_TEST_SHUTDOWN_LIMIT` (default
  120 s) bounds the end of the run: the summary, non-daemon threads, atexit
  handlers and coverage's save. Both must be
  positive integers; anything else stops the runner with exit 2. 120 s stays below
  asyncio's 300 s wait for default-executor threads on Python 3.12 and 3.13 (3.10
  and 3.11 wait without limit), so a hang in an `IsolatedAsyncioTestCase` loop
  shutdown is dumped inside the test that hung.
- Reading a dump: the last `start:` line names the test. An `ok` after it means
  the test body passed and the hang came after it: in its teardown or event-loop
  shutdown (where `IsolatedAsyncioTestCase` joins executor threads), or in a
  class or module fixture before the next test; the stack shows which. A dump
  after the summary is a hang at interpreter exit. The dump shows thread stacks, not asyncio task
  stacks.
- When the watchdog fires under coverage, that run's coverage data is lost; the
  job fails anyway and the dump is what matters.

Every job in `test.yml`, `code-quality.yml` and `viewerjs-check.yml` has
`timeout-minutes` (`tests/vendor/ci-workflow.test.mjs` checks it). The rule is
ceil(1.5 x the slowest of three recent green runs on `main`) + 3 minutes, never
under 5; the three run ids, their times and the resulting limits are written
in a comment above `jobs:`. The 3 minutes leave room for one watchdog firing, so
the dump prints before GitHub cancels the job. A job that hits its limit, or a
watchdog dump, is a failure like any other: read the dump, find the blocking
call, fix it. Never raise a limit or rerun to get green; a limit changes only
with new measurements written next to it.

### After a push

Follow the run for the commit you pushed, never an older one:

```sh
gh run list --commit <pushed sha> --workflow test.yml     # the run id for that commit
gh run watch <run id> --exit-status                       # returns when the run ends
gh run view <run id> --json jobs                          # shows a job that already failed
gh run view --job <job id> --log-failed                   # that job's failed log, at once
```

Python 3.13/3.14, real ComfyUI and Firefox/WebKit failures first show up in the
merge queue, on the merge-group commit rather than the
pushed one. Find that run with `gh run list --event merge_group --workflow test.yml`
(or `viewerjs-check.yml`) and match its head branch
(`gh-readonly-queue/main/pr-<N>-...`). Windows and macOS failures show up only
after the merge, in `gh run list --workflow portability.yml`.

Start fixing as soon as a failed log shows the cause, but push the fix only after
the run for that commit has finished: pull-request runs use `cancel-in-progress`,
so a push cancels the running jobs and can hide a second failure.

## Background threads at exit

PromptManager's own background threads stop through one package teardown,
`_stop_background_work()` in `__init__.py` (#301, #302). It runs once and
works against one 10 s deadline. Each step gets the smaller of its own bound
and the time left. The steps, in order:

1. Refuse new previews (`refuse_auto_thumbnails()`), then detach the
   monitor's callbacks (Needs review recorder, preview queue, Images listing
   listener).
2. `ImageMonitor.stop_monitoring` works against one 5 s deadline. Closing the
   image handler joins the image worker; watchdog's `observer.stop()` (not
   bounded here) and the observer join get what is left, which can be 0 s.
3. `close_auto_thumbnails(final=True)` stops the preview worker (30 s) and
   kills ffmpeg.
4. `PromptTracker.shutdown` stops the prompt cleanup thread (2 s).

A failing step, or a second Ctrl+C, is logged with its traceback and does not
skip the later steps. A clean run logs one INFO line to PromptManager's log:
`Background work stopped in 0.4 s: monitor=ok thumbnails=ok tracker=ok`. If
any part is still running, one WARNING goes to the console:
`Background work not stopped in time (10.0 s): monitor`. A Ctrl+C that lands
between two steps (each step catches its own) is re-raised after the result is
stored, with the parts not reached False, and the `atexit` callback is
removed: the later steps are skipped, no summary line is logged, and a later
call returns that result and runs nothing.

ComfyUI never runs aiohttp's `on_cleanup` hooks, because it never calls
`runner.cleanup()`. So `atexit` is the live host's path. It runs on Ctrl+C
(`main.py` catches `KeyboardInterrupt` and returns) and on a normal exit.
The teardown is registered after the execution history hook, so at exit
(newest first) it runs before the history writer's 2 s dispose.
It also runs from `on_cleanup` on hosts that do run it (our test servers) and
from the tests' package unload (`cleanup_package`), and it removes its own
`atexit` callback. Nothing runs when the process is killed: SIGTERM, a
ComfyUI-Manager restart through `os.execv`, a closed Windows console, or
ComfyUI Desktop stopping its server. SQLite's WAL journal covers those.

Jobs running at exit (#376): before any `atexit` callback, Python joins every
executor thread (`concurrent.futures` registers that join with
`threading._register_atexit`). The package registers its own callback there
(`utils/exit_signal.install`, after `concurrent.futures`, so it runs first):
it sets the exit signal and kills the running encoder processes
(`shutdown_encoders`, the ffmpeg that never sees Ctrl+C on Windows). The call in
flight then stops at its next safe point: `Job.cancelled` is True, so every
check a job already makes between files stops it, `map_parallel` starts no
further item (`ExitRequested`), the scan walk stops between folders, LoRA
import staging stops before its next sidecar (also for the legacy scan) and a
tagging model download stops before its next chunk where huggingface_hub takes
`tqdm_class` (1.x removes its temp file; 0.x `hf_hub_download` does not take it
and finishes the file). Xet transfers ignore that checkpoint, so the hook also
calls hub's own `abort_xet_session` when huggingface_hub is loaded. The
trigger-word cache load (small JSON sidecars) has no checkpoint. No check sits
inside a library write. That hook is
CPython-internal; without it (or when it is refused) `install` returns False and
exit waits for the call in flight, as before. `tests/test_exit_cancel.py` runs a
child process with three such calls running and requires its exit within 15 s.

Stop contract: `PromptTracker.shutdown(timeout)`,
`ImageGenerationHandler.shutdown(timeout)` (restartable),
`ImageGenerationHandler.close(timeout)` (final: `schedule()` refuses and a
sealed run's re-check is dropped) and `ImageMonitor.stop_monitoring(timeout)`
each signal their thread and join it within the bound. They return True only
when it ended, and log a warning naming the part when it did not. Called on
their own thread, they only signal.

Test rules:

- Every test that constructs a real `PromptTracker`, `ImageGenerationHandler`
  or `ImageMonitor` ends with its final stop and an assertion
  (`tests/background_stops.py`: `close_handler`, `stop_monitor`,
  `stop_tracker`). The stop comes before the folder and databases are removed.
  `tearDown` runs before `addCleanup` callbacks, so a class whose `tearDown`
  removes files stops the thread there.
- A test waits for a state through an `Event` set by the code under test, or
  by a wrapper around it, with a 30 s ceiling. It never sleeps or polls for
  that state.
- A test that asserts "still running" first waits for an `entered` event
  inside the held call, then stops with `timeout=0.05`.
- A test that asserts a history writer's `dispose()`/`close()` is True right
  after queuing work drains first (`drain_writer`), or holds the write itself
  (#370).
- Patch the module attribute of the code under test (for example
  `utils.prompt_tracker.time`, or the package's `_clock`), or patch with
  `wraps=`. Never replace a `time` or `atexit` function for the whole
  process.
- `tests/test_package_exit.py` runs a child process that imports the
  package, leaves the image worker alive and returns. Its `atexit` reporter
  checks the teardown's result. This is the interpreter path ComfyUI takes
  after Ctrl+C, on all three OS.

## Performance budgets

A spec performance budget gets two tests:

- A structural test that always runs, on every OS/Python job and under the
  coverage tracer. It asserts counts, never time: SELECTs per request, the
  `EXPLAIN QUERY PLAN` of the hot query, cache builds on cold and warm requests,
  and calls of the sort key per cursor page. Example:
  `test_filter_work_is_bounded` in `tests/test_api_workspace_images.py`.
- A wall-clock test that runs only with `PROMPTMANAGER_PERF_BUDGET=1` and
  without a tracer (`sys.gettrace()` or a `sys.monitoring` tool). It skips
  otherwise. It warms up once, then measures three rounds and asserts the best
  round's p95; the failure message lists every timing. Example:
  `test_filter_budget` (Images filters, spec D12: p95 < 150 ms at 50,000 files
  and 20,000 links).

A hot SQLite read that returns many rows aggregates them into one step
(`json_group_array`) instead of stepping once per row, so it does not wait for
the GIL once per row while ComfyUI generates (#354, `get_image_link_matches`).

The `perf budget` job in `.github/workflows/test.yml` is the reference
measurement: `ubuntu-latest`, one Python, no coverage, only the wall-clock tests.
It runs on every push to `main` and on `workflow_dispatch`, never on pull
requests, because shared-runner timing must not block a merge. Dispatch it on a
branch to measure before merging.
Shared Windows and macOS runners do not run them; the structural tests cover
portability. Run it locally with
`PROMPTMANAGER_PERF_BUDGET=1 python -m unittest tests.test_api_workspace_images.TestWorkspaceImages.test_filter_budget`
and quote the printed p95 in the PR body.

A red `perf budget` run on `main` means the code got slower: fix the code (or
change the budget in the spec with a reason) in a follow-up PR. Never rerun it to
green. It is not a required check, and it cannot be one while it skips pull
requests (`.github/branch-protection.json`).

## Images listing cache (#348)

`/prompt_manager/images/output`, `/prompt_manager/gallery/subfolders` and
`POST /prompt_manager/gallery/rescan` read one shared listing per output root
(`RootListing` in `py/api/gallery_listing.py`, `_listing` in
`py/api/images.py`). The tests are `tests/test_gallery_listing.py` (pure
helpers) and `TestSharedListing` in `tests/test_api_images.py` (routes); they
hold a walk open with a gated scan instead of sleeping.

- **Single flight.** At most one walk per root runs at a time; every reader
  that needs that root while it runs waits for it (shielded, so a client that
  disconnects never cancels it). The walk, the comparison with the previous
  listing and both folder sets run in the executor (`walk_root`). A walk that
  finds the same `(path, mtime)` list keeps the previous list object, so the
  favorites lookup and the sort orders are not rebuilt.
- **30 s background refresh.** A listing older than `REFRESH_AFTER` (30 s) is
  served at once and one background walk replaces it. Files added or removed
  by hand therefore show up one open later than before; Rescan shows them at
  once.
- **Blocking invalidation after deletes.** Duplicate cleanup and the legacy
  duplicate delete still call `invalidate_gallery_cache()`: the listing is
  dropped and the next read waits for a walk, so a deleted file is never
  listed. A walk running during the drop is discarded (the record's
  `generation` outlives every drop); a cold reader gives up with a 500 after 3
  discarded walks in a row. Thumbnail writes (jobs, conversion, the legacy
  generate and clear routes) no longer drop the listing: thumbnail URLs are
  computed per request.
- **Startup warm-up.** The root `__init__.py` registers
  `api.warm_gallery_listings` on the host's `on_startup`, which starts one
  background walk per root, and `api.cleanup_gallery_listings` on
  `on_cleanup`, which cancels running walks. A warm-up failure never stops
  startup; the first read retries.
- **Failures and Rescan.** `walk_root` opens the root with `os.scandir` first,
  so a deleted or unreadable root fails the walk instead of listing nothing.
  A failed walk keeps the previous listing, logs one warning per streak of
  failures and is retried at most once per `REFRESH_AFTER`; a reader with no
  listing gets a 500. Rescan waits for a walk that started after the click
  (by walk number, not by clock), Rescans clicked during one walk share the
  next, and a failed walk is a 500 even when a previous listing exists.
- **New outputs (added without a walk, PR 2).** The image monitor reports
  every settled output it accepts (by default `.png .jpg .jpeg .webp .gif`;
  videos only when their extension is in `monitoring.extensions`), linked or
  not, with its `realpath`, through `set_output_listener`. On its worker
  thread the API's `notify_output_settled` only does
  `loop.call_soon_threadsafe(output_settled, path)`, and does nothing before
  the loop is known or after `cleanup_gallery_listings`. `output_settled`
  matches the path against each walk's `resolved_root` (`normcase(realpath)`,
  computed in the executor; `root_contains` ends the match at a separator
  and ignores a Windows `\\?\` or `\\?\UNC\` prefix on either side).
- **The add.** A root with a stored listing and no walk running gets the
  file added without a walk. `relative_part` slices the path below the
  root in its own case (Windows compares folded, slices the original);
  `_run_adds` places the pending files of one root in one executor batch
  (`_patch_listing`): `_settled_entry` applies the walk's filters
  (not a symlink or folder by `lstat`, `_name_suffix` in `MEDIA_EXTENSIONS`,
  no `thumbnails` folder, at most `MAX_SCAN_DEPTH` folders, every folder on
  the way listable by `os.access`, which on Windows ignores ACLs: a folder
  whose ACL denies listing is added there and corrected by the next walk). Where names fold case the stored
  spelling must match first, as the filters compare it: Windows through
  `nt._getfinalpathname` (its `realpath` keeps the given case when it cannot
  open the file), macOS through `F_GETPATH` on a non-blocking open (a FIFO
  never holds the executor); a case-insensitive mount on Linux is not
  checked and is corrected by the next walk. `insert_entry` builds a new list with
  the `(Path, mtime)` entry where the walk's newest-first sort puts it,
  replacing the same path's entry. `add_folder` copies both folder sets. The
  batch is stored only when no walk started and no drop happened while it
  ran (a walk started after the report lists the file itself); `walked_at`
  is untouched, so the 30 s refresh still walks and corrects any drift, and
  `walk_root` keeps the patched list object when that walk finds the same.
  Tests compare every added listing with a real walk (`_walked`).
- **When it walks instead.** The root is marked dirty (`mark_root_dirty`,
  `_mark_dirty`) when the add cannot be exact: a walk is running (it may have
  passed the folder: the trailing walk), the path cannot be sliced, the
  `lstat` or the spelling read fails, the stored spelling differs, another
  file has the same mtime (a walk orders those by its
  visit order), the listing is at `MAX_GALLERY_FILES`, or the batch raised.
  A file the walk's filters exclude is neither added nor walked. A root
  without a listing, or a dropped one, is walked by its next read. A dirty
  root gets one background walk, no sooner than `rewalk_interval` =
  `min(15, max(5, 5 × last walk's duration))` seconds after the previous walk
  started. `_schedule_walk` never arms a timer while a walk runs (the walk's
  end re-checks `dirty`: the trailing walk), arms at most one per root and
  never sooner than 0.5 s, and keeps the earlier deadline when the stale
  refresh and a dirty root both want one; every walk start (read, Rescan,
  warm-up or timer) cancels it and forgets the pending adds. A failed walk
  clears `dirty`; a cancelled walk starts no trailing walk. When the output
  roots change (`reset_gallery_roots_cache`) every record is `retired`: its
  timer is cancelled and no add, mark, timer or trailing walk touches it
  until a reader or the warm-up asks for that root again (the monitor keeps
  watching a removed root until restart). Tests patch
  `REWALK_FLOOR`/`REWALK_CAP` and run the pending timer's callback by hand
  (`_fire`) instead of waiting for it, and hold an add batch in the executor
  (`_hold_adds`) to race it with a walk, a drop or the cleanup.
- **Workspace caches after an add.** A patched listing shares every
  unchanged entry object with the one before it, so `listing_delta` finds
  the change by identity (a walk builds new tuples and is rebuilt in full,
  as is any change of more than `MAX_DELTA` entries). The per-file values
  build only the added files (`_workspace_listed_step`), the newest order
  places them by bisection (`_newest_step`, only when the per-file cache
  still holds exactly the values this call built: a page read and the warm
  build run in executor threads at once, and an order labelled with older
  values than it holds would add a file twice), the favorites lookup is copied
  and extended (`_favorite_listing_step`, rebuilt when a key collides), and
  the other orders, wanted paths and locators rebuild lazily. On a synthetic
  20,000-file listing a one-file add costs about 3 ms in the executor and
  15 ms for the warm build, against about 155 ms + 80 ms for a walk and a
  full build (`test_a_warm_build_after_an_add_builds_only_the_added_values`
  counts the values built and the sort keys read).
- **Measuring it.** `tools/vendor/perf/images_load_probe.mjs` is a maintainer
  tool, never run in CI. After `npm ci --prefix tools/vendor` and
  `npx --prefix tools/vendor playwright install chromium`, run
  `node tools/vendor/perf/images_load_probe.mjs http://127.0.0.1:8188` against
  a running ComfyUI. It prints the time to the first decoded thumbnail, the
  time to network idle and the 10 slowest requests, from a fresh browser
  context each run.

## Windows portability preflight

Before changing paths, SQLite, streams, restore or cleanup:

- Follow the boundary's existing native canonical/identity contract;
  `Path.resolve()` alone does not fold Windows case. Keep lexical aliases
  distinct from resolved targets. Cover mixed case, drive and UNC spellings,
  plus foreign-namespace denial with an allowed native control.
  See Python's [normcase documentation](https://docs.python.org/3.13/library/os.path.html#os.path.normcase).
- Close every owned database/model connection, worker and stream handle before
  unlink or `TemporaryDirectory.cleanup()`. Await worker/stream retirement.
  A SQLite [`with conn` manages transactions, not connection closure](https://docs.python.org/3.13/library/sqlite3.html#how-to-use-the-connection-context-manager),
  and [`addCleanup` runs after teardown](https://docs.python.org/3.13/library/unittest.html#unittest.TestCase.addCleanup);
  use explicit `finally` cleanup when teardown removes files first.
- Windows can reject unlink of an open file; Unix can remove its directory entry
  while the handle remains usable. Preserve the shared behavior assertions and
  exercise the actual native branch, then prove handle release and successful
  cleanup. Do not hide failures with blanket skips or retries.
  See Python's [remove/unlink semantics](https://docs.python.org/3.13/library/os.html#os.remove).
- Concurrent SQLite tests must not depend on how fast the host commits.
  Managed connections use WAL with `synchronous=NORMAL` (see
  `configure_connection`) so commits do not hold the write lock across a
  flush. Make real overlap deterministic (for example hold the first writer
  open with `FirstWriterHold`) and give peers that must all succeed the
  ordinary busy timeout; cross-thread handoffs wait on events with generous
  ceilings, never a short wall clock (#242).
- Run native Windows CI early for these changes. Pull requests and the merge
  queue run only Linux, so start Windows and macOS on the branch with
  `gh workflow run portability.yml --ref <branch>`.
  Include a Windows impact and evidence note in the PR. All six Windows/macOS
  combinations in that run, every Linux leg and every applicable
  exact-head check must pass before merge. Portable `ntpath`/`PureWindowsPath`
  controls do not establish native filesystem, locking or cleanup success.

## Pull requests and the merge queue

Each PR runs the full matrix once, in the merge queue (#420). The event decides
what runs:

| Job | `pull_request` | `merge_group`, push to `main`, `workflow_dispatch` |
| --- | --- | --- |
| `py3.12 on ubuntu-latest` | runs | runs |
| `py3.13 on ubuntu-latest`, `py3.14 on ubuntu-latest` | not created | run |
| `real ComfyUI compatibility` | skipped | runs |
| `coverage gate (85%)`, `black and flake8`, `frontend modules`, `bandit (high severity)` | run | run |
| `frontend coverage (shard N/3)`, `frontend coverage (merge)` (vendor pins and tooling tests on shard 3) | run | run |
| `page workflows (firefox\|webkit, shard N/2)` | skipped | run |
| `all tests`, `viewerjs` (gates) | run | run |
| `perf budget` | skipped | push and dispatch only (not in the queue) |
| `py3.12-3.14 on windows-latest/macos-latest` (`portability.yml`) | not run | push to `main`, nightly, `workflow_dispatch` only; never required |

GitHub has one required set for pull requests and the queue. A job skipped by
its `if:` reports Success. A dropped matrix leg never reports, so it would stay
pending. That is why `all tests` (in `test.yml`) and `viewerjs` carry the
guarantee:
- On a pull request they require the subset to succeed and the queue-only jobs
  to be skipped.
- On every other event they require every job to succeed.

So a merge group cannot turn green unless all three Linux Python legs, real
ComfyUI and the four browser shards passed.

Windows and macOS (Python 3.12-3.14) are out of the blocking path for now
(maintainer, 2026-10-28). They run in `portability.yml` on every push to `main`,
nightly and on demand, and no context there is required. A red portability run
is still a failure: fix it at its cause, not by rerunning, and have every one
green before the workspace cutover (#334). `tests/vendor/ci-workflow.test.mjs` pins the
event split and runs both gate scripts for each event.

## Required merge checks

`.github/branch-protection.json` records the required checks:
`py3.12 on ubuntu-latest`, `coverage gate (85%)`, `black and flake8`,
`frontend modules`, `all tests`, `viewerjs`, `real ComfyUI compatibility` and
`bandit (high severity)`. Each one reports on a pull request.
The same set lives in two places, and both must change together: the `main`
ruleset (25960526, which also holds the merge queue) and the classic branch
protection on `main`, which the manifest mirrors. GitHub requires the union of
the two, so a context left in either one blocks every PR that does not report it.
It does not require an additional human approval.

A repository administrator applies the configuration after the new workflow is
passing, then verifies it:

```sh
gh api repos/ComfyAssets/ComfyUI_PromptManager/rulesets/25960526 \
  | jq '.rules[] | select(.type == "required_status_checks") | [.parameters.required_status_checks[].context]'
```

A PUT replaces the whole `rules` array, so edit the current ruleset in place.
Keep the `deletion`, `non_fast_forward` and `merge_queue` rules, and give every
context `integration_id` 15368 (GitHub Actions), so no other integration can
post a required check. Save the current ruleset first; it is the rollback:

```sh
gh api repos/ComfyAssets/ComfyUI_PromptManager/rulesets/25960526 > ruleset-before.json
jq '{name, target, enforcement, conditions, bypass_actors, rules: [.rules[]
     | if .type == "required_status_checks"
       then .parameters.required_status_checks = [$ARGS.positional[] | {context: ., integration_id: 15368}]
       else . end]}' ruleset-before.json --args "py3.12 on ubuntu-latest" "..." > ruleset-new.json
gh api --method PUT repos/ComfyAssets/ComfyUI_PromptManager/rulesets/25960526 --input ruleset-new.json
```

Afterwards, read it back. Check that the `merge_queue` rule is still there and
that every context keeps its `integration_id`. Then apply the manifest to the
classic protection and read both back:

```sh
gh api --method PUT repos/ComfyAssets/ComfyUI_PromptManager/branches/main/protection \
  --input .github/branch-protection.json
gh api repos/ComfyAssets/ComfyUI_PromptManager/branches/main/protection/required_status_checks
gh api repos/ComfyAssets/ComfyUI_PromptManager/rules/branches/main
```
 Update the manifest whenever
required job names change. Keep the frontend check
free of path filters so a required check is never silently skipped.

## Unified workspace

The opt-in `/prompt_manager/workspace` page opens the native Images workspace.
Overview retains real `/prompt_manager/stats` counts. Both use the shipped toolkit. Existing admin, gallery and metadata URLs remain
available while replacement workspaces are developed. Browser smoke tests in
`tests/browser/workspace.spec.mjs` cover navigation/history, keyboard skip links,
mobile layout, theme persistence/fallback, loading/retry and stale responses.
The Images cases in `tests/browser/workspace-images.spec.mjs` additionally cover
server-side filters, cursor append/retry, Grid/List/Masonry, bounded DOM, keyboard
navigation, restored browsing context, Viewer.js cleanup and actual video playback.
`tests/test_api_workspace_images.py` checks signed cursors, source identities,
legacy compatibility and header-only dimensions. Frontend stream/layout units
cover cancellation, malformed responses and bounded visibility lookup. These run
automatically in the existing four-browser-project workflow and frontend coverage job. `tests/comfyui/workspace.spec.mjs` verifies the page against the
managed host with both renderer startup configurations.

The shared inline and collection inspector reads the current rooted file's supported
embedded records, exact generation facts and recorded or inferred prompt evidence.
It renders in two concept surfaces (#249 D20–D22, D38). The Images side panel shows
only the concept content: cover, title, filename, the evidence badge with a category
chip, the + and − prompt blocks with copy and send, tag chips (the file's own image
tags first, D88, then the linked tags; image tags and the linked positive's own tags
filter Images, D21), the six facts, and a footer with "Send to ComfyUI" and
"Prompt history" only when prompt roles resolved or a link is saved; the sticky panel
scrolls inside the window. The viewer's details (`#viewer-details`) repeat that
content without the cover, then "File & provenance" (the honest file facts, the
`Prompt evidence` line, Download original and, for a listed file, D38's local-only
"Show in folder" after the Path) and "Workflow & raw metadata" (raw records and their
two downloads), then the pair send and Prompt history buttons. D22 words an empty
positive explicitly and makes confirmed prompt text focusable.
`tests/browser/inspector-concept.spec.mjs` compares both surfaces with the concept
(see the D20–D39 notes under Images).
`tests/browser/full-inspector.spec.mjs` covers original/metadata/workflow downloads,
response limits, source changes, nested sending, matched dark/light and mobile
geometry, and the loaded-collection viewer. `viewer matches the concept shell and
toolbar order` measures the concept's `#viewer-dialog` shell, header copy, toolbar
order and frame geometry in the concept and in production at 1600 and 390 pixels;
its #249 D34 mapping keeps production's Download link, the appended position and
reason lines, coarse 40px targets and the toolbar inside `.viewer-left` (Viewer.js's
own `.viewer-toolbar` rule lifts the concept's across the dialog). The viewer never fetches another page
or preloads all originals. Its explicit slideshow waits 3.5 seconds after image
readiness, pauses at native video without advancing on `ended`, and resumes on a
manually selected image only while prior intent remains. Pause/Stop, inspector
reading/copy/download/send, hidden document, media failure, close, query/route
disposal and pagehide cancel that intent; returning visibility or closing the sender
does not resume it. Stop leaves native video playback alone. Video uses only the
existing normalized `gallerySettings` autoplay/mute/loop values, read without writes;
denied/malformed storage falls back to autoplay off, mute on and loop on. Rejected
autoplay keeps native controls usable.

The inspector does not establish historical file identity, infer run membership,
edit image tags, export all EXIF, or load/execute a workflow. Saved vocabulary is
labelled by its recipe or positive-prompt scope. Limited raw evidence is omitted
atomically, with JSON exports unavailable and the rooted original still downloadable.
The inspection API uses one bounded read with per-record budgets: role records
(`prompt`, `parameters`, the pair execution record) share 2 MiB and exceeding it
fails the read; the `workflow` record has its own 4 MiB and past it only the workflow
becomes record format `limited` (no raw text, graph `limited`, roles and facts
unaffected); other text chunks are skipped unread; byte-identical duplicates are
accepted within a bounded read budget, while differing duplicates (and any duplicate
role record over that budget) still fail. Other read bounds are unchanged: 8 MiB
non-PNG header, 10,000 PNG chunks and a three-second ffprobe (per probe, at most two
probes). Video containers are read through one shared carrier selector
(`utils/video_metadata.select_video_carrier`, used by the inspector and by the admin
scan): carriers are, in order, the per-key tags that current savers write (`prompt`,
`workflow`, `parameters`, `comfyassets_prompt_pairs`, each `original-text`; Matroska
upper-cased names fold on our side), then the old VideoHelperSuite `comment` JSON
envelope, then `description` (object values are `reserialized-json`; envelope keys are
matched exactly, as the legacy readers did, never folded). The first carrier holding a
role key wins, otherwise the first holding a workflow, and carriers never merge. Two
per-key tags folding to one key are a duplicate record: byte-identical ones are one
record, differing ones fail the read. ffprobe is asked for exactly those six tags.
Its JSON output escapes tag text (at most doubling it), so the stdout bounds are sized
so that only the decoded per-record budgets above can fail or limit a record, and a
video reads exactly as the identical PNG: the first probe allows twice the role and
workflow budgets plus 64 KiB of envelope (12 MiB + 64 KiB); past that the inspector
probes once more without the `workflow` tag within twice the role budget plus 64 KiB
(4 MiB + 64 KiB) and the per-key workflow becomes `limited`, while an envelope over
that bound still fails the read because it cannot be split. The admin scan reads the
same carrier with the default resolver profile, so a per-key video links like the same
graph in a PNG; the live monitor processes images only (unchanged, see the follow-up
below) (`tests/test_utils_video_metadata.py`, `tests/test_api_admin.py`
`TestScanReadsVideos`, `tests/test_api_prompt_inspector.py` `test_video_*`,
`tests/test_inspection_response.py` `TestVideoProbeBoundsMatchPngBudgets`; the
real-container round trips skip without ffmpeg). Resolution under
`INSPECTION_PROFILE` bounds work, not graph size: 2,000 visits per candidate chain,
20,000 in total, 64 link/JSON-container levels, 100,000 resolved characters, and a
two 20,000-node ceilings: one over the top-level entries of the API prompt, and one
over the workflow graph's UI nodes plus subgraph-definition nodes (past it the
workflow graph and its facts become limited). The serialized
response stays under 8 MiB by omitting, in order, the workflow graph, then the prompt
graph, then all raw records atomically. Retry does not raise these limits. Ingestion
(scan, live monitor, admin import) and queue-time run counting keep the default
profile, including its 500-node gate. Only `INSPECTION_PROFILE` reads the node
semantics table `web/js/inspection-semantics.json` (at most 1 MiB and eight nesting
levels, loaded on first use). With it, `SamplerCustomAdvanced` follows its `guider`
link (through Reroutes) to `CFGGuider` (positive and negative) or `BasicGuider`
(positive only; the negative is `absent`, "Guider has no negative input"). Any
other guider, such as `DualCFGGuider`, is `unsupported-node`. Conditioning is
traced through the table's pass-throughs (FluxGuidance, ReferenceLatent, the
ConditioningSet* nodes, ControlNet apply nodes, InpaintModelConditioning and
WanImageToVideo by output slot). `ConditioningZeroOut` is an explicit empty text
with source `zeroed-conditioning`. `easy ifElse` with a literal boolean follows that
branch only; with a linked boolean both branches are traced, equal texts resolve and
different texts make the sampler `ambiguous` with one candidate per choice (at most
32, else `work-limit`). ConditioningCombine/Concat/Average resolve only when every
part has the same text, otherwise `unresolved` with `composite` naming the node
(but see the added-conditioning floor below for a later stage).
Results that depend on a forced choice are never memoized. A `FaceDetailer`
(`detailer-stage`) is a sampler stage: the samplers reached through its `image`
ancestry are compared with it. Equal texts resolve with the detailer as the sampler;
different texts are `ambiguous` with `multistage-differs` and one candidate per
stage; an unreadable stage makes the chain `unresolved`. Unanchored, a sampler
whose output only feeds detailers (or previews) is not a candidate of its own.
A sampler's latent ancestry is walked through every link input of non-sampler
nodes within the same visit and depth budgets: with no earlier sampler it is a
single stage whatever the origin (VAEEncode, EmptyLatentBatch, EmptyFlux2LatentImage,
EmptyHunyuanLatentVideo, SetLatentNoiseMask, InpaintModelConditioning or
WanImageToVideo slot 2). Pass-through lineage (#304 follow-up, maintainer option B,
2026-10-23): the table's `lineage` section lists the outputs whose image or latent
continues one input's (`VAEDecode`/`VAEDecodeTiled` `samples`, `VAEEncode`/
`VAEEncodeTiled` `pixels`, `ImageUpscaleWithModel`, `ImageScale`, `ImageScaleBy`,
`ImageScaleToTotalPixels` `image`, `LatentUpscale`/`LatentUpscaleBy` `samples`,
cg-image-filter `Image Filter` slot 0 `images` and slot 1 `latents`; a selection of
the same batch), plus Reroutes. Lineage describes no prompt, so its classes are not
claimed by a prompt category. When the latent walk finds exactly one earlier sampler
and the latent descends from that sampler's output slot 0 only through lineage
nodes (a hires-fix or refiner chain, img2img from an in-graph decode, a direct
latent link), the two stages are compared like a detailer and its upstream stage:
equal role texts resolve with the later sampler named, different texts are
`ambiguous` `multistage-differs` with one candidate per stage, and an unreadable
earlier stage makes the chain `unresolved` with that stage's reason. Unanchored, the
earlier sampler is judged inside the chain when its output reaches no other file
output. A size or resolution node reading the same image beside the lineage (a
`ResolutionCalculator` feeding `ImageScale` width and height) is allowed, since the
walk then still finds only that sampler. Any other earlier sampler, or one reached
only through an unlisted image node, stays `unresolved` "Multistage sampler ancestry
is ambiguous". Added-conditioning floor: a later stage whose own positive fails only
as `composite` keeps its earlier stages' shared pair ("A later stage adds
conditioning to the base prompt", naming the earlier stage's sampler) when that
positive reaches, through Reroutes and conditioning pass-throughs, a composite the
table marks `additive` (ConditioningCombine, ConditioningConcat; never
ConditioningAverage, which can weight a part away), one of its parts is exactly the
earlier stages' positive text, and its negative text equals theirs. This is the
three-stage Flux run whose UltimateSDUpscale combines the PromptManager prompt with
a style-trigger encoder: legacy linked the PromptManager text and every stage is
conditioned on it. The positive is always labelled (maintainer ruling): its source is
`added-conditioning` (D126; with a D125 omission too,
`runtime-parts-omitted-and-added-conditioning`) and its provenance names the
composite (`added_by`), whatever the added text is; no length heuristic. A combine without the base text, a different negative, or
different earlier stages stay as they were (`composite` or `multistage-differs`).
An image-edit batch (several samplers with distinct prompts, one shared saver
prefix) stays `ambiguous`, and text generated at run time stays untraceable
(`tests/test_resolver_pass_through.py` on synthetic shapes from
`tests/passthrough_graphs.py`; the browser port is compared on the named
`select-*` chain cases and `passthrough_fuzz_cases`). Ingestion keeps the default
profile's historical latent rule (D1), so the admin scan still reaches these files
through its legacy-parser fallback. Run-time PromptManager parts (maintainer option
A, 2026-10-23): LoraManager's `TriggerWord Toggle` is a `runtime` class (its output is
the LoRA's trigger words as filtered at run time). A PromptManager (or
PromptManagerPair) `prepend_text` or `append_text` linked directly to a `runtime`
node is left out: the text is the node's own, combined with any other literal or
traced part exactly as before, the side's source is `runtime-parts-omitted`, and a
resolved pair's reason is "Text added at run time, such as trigger words, is not
included". The prompt block shows that line under the text (D125; the evidence
badge stays "Workflow inferred"). The label follows the text through encoders,
concatenations and chained PromptManagers: a side is labelled when any node on its
traced path left a part out, and its provenance lists those PromptManager nodes
(`omitted_parts`). Comparisons include what a text leaves out, so a prompt that left
out a part never equals a complete one (a branch with the toggle and one without stay
ambiguous; such stages are `multistage-differs`). Only classes in the table's
`runtime` list are left out, never another `runtime-value` source (a JoinStringMulti
with a linked list flag stays `runtime-value`). The toggle's output is never computed. A run-time node further upstream (inside a
concatenation), a run-time main `text`, or an unknown feeder keep their outcome
(`runtime-value` or `unsupported-node`). This is what legacy linked for the June
hires-fix runs (`TestRunTimePromptManagerParts`, the `select-runtime-*` parity
cases); an unlisted node with sampler-shaped inputs (positive, negative, guider,
latent_image, a pipe, video embeds, custom-sampler parts, or a `model` link together
with conditioning, neg_conditioning, samples or latent) is `unsupported-node`
naming it, here and in a detailer's image ancestry; a latent that is not a link or
reaches its own sampler is `malformed`; running out of budget is `work-limit` or
`depth-limit`, never single stage. Each sampler's walk runs once per resolution;
selector branch runs share it and other candidates are charged its visit cost.
A UI workflow node whose `type` is a list or an object (read only for the overwritten
PromptManager check) is skipped like a non-object node, since it can be neither a
subgraph instance nor a PromptManager node, so a prompt that resolves still resolves
and the other nodes still feed the check; a list or object `class_type` is never a
preview output. Before #286 both raised and the inspect route answered 500
(`test_unhashable_*` in `tests/test_api_prompt_inspector.py`, the `workflow-type-*`
golden cases on both sides).
Text encoders: `single` encoders (the Qwen image encoders, `TextEncodeQwenImage21`
slot 0 `prompt` and slot 1 `negative_prompt`, `KikoTextEncode`) read one string
input unchanged. `parts-equal` encoders (CLIPTextEncodeSDXL, Flux, SD3) resolve when
every part is identical or exactly one part is non-empty (exact `""`, whitespace is
text); otherwise `composite`. String sources reproduce the pinned KJNodes code from
literal settings only: `StringConstant` is its `string`; `StringConstantMultiline`
with a literal true `strip_newlines` is `replace("\n", "").strip()`;
`JoinStringMulti` keeps `string_1` (even empty), appends `string_2`..`string_N`
(`N` = `inputcount`) after the literal delimiter unless missing or exactly `""`, and
ignores later inputs. A linked setting is `unsupported-node`; a list output
(`return_list` true or linked) and `PromptSearchList` are `runtime-value` ("Text is
decided at run time"), never one list item. Every
intermediate text is length-checked and charged to the scanned-character budget. If
the table cannot be loaded, graph inspection is `unresolved` ("Inspection node
semantics are unavailable") and ingestion is unaffected, because it never imports the table.
Unknown nodes pass through (#381, maintainer-approved 2026-10-27; no per-node
inventory): an output whose class the table, its `facts`, `lineage` and the code's
adapters never name is read through, not refused. A `text` input is its text (the
legacy encoder rule, so RegexReplace or LoRAfromText show their input text), except
that a string output of a node with a seed input (`seed`, `noise_seed`,
`sampling_mode.seed`) and no linked `conditioning`/`positive`/`negative` input is
generated at run time (a language model such as TextGenerate, a wildcard):
`runtime-value` naming it. Else, at output slot 0 or 1 of a node with a `positive` or
`negative` input, only `positive` (slot 1, `negative`) is followed, nothing when it
is not linked; else a linked `conditioning` alone; else every linked input except
the table's `pass_through.skip_inputs` (core type inputs: model, clip, vae, image,
latent, ...), each traced with kind `any`. A followed input with no text adds
nothing; limits and selector choices propagate; another failure (`runtime-value`,
`composite`, `malformed`) blocks, unless a PromptManager node was reached: this
repository's own nodes are authoritative, and then only their texts count. One
distinct text resolves; several make the sampler `ambiguous` "Several prompts reach
the sampler" with one candidate per distinct outcome, in input order (at most 32,
else `work-limit`). A PromptManagerPair's execution record is what ran: while its
inputs are checked against it, a walk through an unknown node that fails, diverges
or passes text through is unverifiable and never overrides the record. No text
at all is `unsupported-node` naming the outermost unknown node. An unlisted output
of a listed class (a sampler, a loader, a lineage node) stays `unsupported-node`, so
the walk never reads another stage's prompt. `INSPECTION_RESOLVER_VERSION` is 2 for
this rule; ingestion keeps the default profile (`RESOLVER_VERSION` 1). The legacy
parity gate (`tests/test_legacy_parity_gate.py`) records, per corpus (the
generation-params vectors, the inspection parity fixtures, the local resolver golden
and selection cases, the unknown-node shapes in `tests/unknown_node_graphs.py`),
every graph whose inspection positive equals the legacy parser's; a graph may join
that list (`python3 -m tests.test_legacy_parity_gate --write`), never leave it. A graph
may read differently from legacy only when `LEGACY_WRONG` there states why (legacy's
first-sampler pick on a branch the file name identifies).
Saver anchoring (#381): an inspected file's name (`path.name` on the server, the chosen
file's name locally) anchors an unanchored inspection on the one node whose
`filename_prefix` (literal, or traced as a string) has a last path part that makes the
file's stem `<prefix>_<digits>` with an optional trailing `_`. That node's linked inputs
are walked like a detailer's image ancestry, through any image node, and the listed
samplers found are the candidates. No match, several matches, a walk that fails or no
sampler found means no anchor: nothing is guessed and the file stays "several possible
prompts". Ingestion ignores file names. Shared-conditioning stages: in a sampler's
latent walk, an earlier sampler reading the very same conditioning links (positive and
negative, or a guider; compared by node ID text and slot) is the same prompt, not an
unattributed stage, when it is a plain first stage: not a detailer, no prompt override
set, and no listed sampler in its own latent ancestry (else it stays unattributed) (`tests/test_resolver_saver_anchor.py`, shapes in
`tests/saver_anchor_graphs.py` from the maintainer's `gpu_unlock_bench`, dataset and
ANIMA runs; browser parity through the `anchor-*` cases, which carry `__file_name`).
Recorded run history (#386): both inspect routes (Images and a history occurrence)
ask `database.run_attribution.recorded_output` inside the file's stat window. It
answers only for a file that exactly one recorded run reported and that was written
during that run (the #342 rules): the one output node that run reported it from (none
when several did) and, per sampler anchor, the effective texts PromptManager recorded
at execution (a role only inferred from the graph is none). Only when the file alone
leaves the prompt open (`ambiguous` or `unresolved`) the inspector resolves again
anchored on that node (`recorded_saver_id`, walked like a file-name match; a file
name naming a different saver contradicts history and anchors nothing), and keeps
that result only when history's saver chose its samplers (`recorded_anchor`) and it
names the positive (resolved, or a guider with no negative); its `graph-inferred`
sides read `recorded-run` ("Recorded run" badge, ranked below every "Workflow
inferred" source). Still open at the one sampler that saver leads to, with that
sampler's positive text recorded (a language model's output feeding PromptManager),
the recorded texts show as `execution`. Outputs and texts are read in one
transaction. A result the file resolves alone never changes; a lookup failure is
logged and judges the file alone; local Inspect and ingestion are unchanged
(`tests/test_recorded_saver_anchor.py`, `tests/test_run_attribution.py`,
`tests/test_api_history_outputs.py`).

`tools/inspection_corpus.py --root DIR --out JSONL [--workers N] [--baseline JSONL]
[--summary TXT] [--code TREE]` measures a media folder read-only against the #240
invariants H1 to H8. It runs the inspection route pipeline and the workspace JS facts
of `--code` (default: its own tree), opens media only for reading, follows no
symlinks, imports no database, and refuses `--out`/`--summary` inside the root or a
code tree. Its rows hold statuses, sizes, timings and SHA-256 digests of resolved
role texts, never file names or prompt text unless `--with-paths` is given; digests
of short prompts can still be guessed, so the JSONL stays local and only the
aggregate summary goes into a PR body. Run it on the base commit first and pass that
JSONL as `--baseline` for H1 and H8 (2x the baseline p99, never below 50 ms). It
exits 1 when an invariant fails, including the `harness` line: a tool exception or
facts that were not computed (no Node.js) fail the run. Unreadable or unsupported
files are inspector outcomes, not harness errors. Never point it at a live install's folders from an
automated job. Installing the Python inspection/source-scope changes requires a
maintainer ComfyUI restart. Automated tests use disposable hosts and never restart
the running personal ComfyUI.

Run the focused page checks with:

```sh
npm run --prefix tools/vendor test:workflows -- workspace.spec.mjs workspace-images.spec.mjs
node --test tests/js/workspace-*.test.js
```

Use the disposable fixture and managed-host prerequisites above. A maintainer
live check needs a ComfyUI restart after adding the Python route, then opening
`/prompt_manager/workspace`; the mockup preview server is a separate design tool.

### Concept shell and comparison method (#249)

**CSS base and drift check.** `web/js/workspace/concept.css` is a byte-identical copy
of the approved concept's `docs/concepts/workspace/style.css`. Never edit the copy;
it is only rewritten from the concept with `npm run --prefix tools/vendor sync:concept`.
`npm run --prefix tools/vendor check` runs `sync-concept-css.mjs --check`, which fails
on a missing copy or names the first differing byte. `workspace.html` loads, in order,
the toolkit controls, Viewer.js, `concept.css` and the production overlay `workspace.css`
last; every page stylesheet that waited for its page PR is retired.
`tests/vendor/concept-css.test.mjs` asserts that order, the drift check, and that no
other page or canvas script loads workspace CSS.

**Overlay decision-marker lint.** `lintOverlay` in `tools/vendor/sync-concept-css.mjs`
fails any `workspace.css` rule whose selector is also a concept selector unless the
rule carries a `/* D<n> */` comment, before its selector or inside its block. The
D-number must be one of this spec's (#249): a comment that cites any other issue, such
as `D5 (#245)`, is that issue's ruling and is not a marker, so write `#245 decision 5`
for those. The lint test fails if `workspace.css` is missing. The match is by exact
selector, so a narrower selector over a concept rule (for example
`.ca-tools dialog.prompt-sender`) passes the lint; by convention such rules carry a
marker too.

PR D deleted `images.css` and `inspector.css`, and PR F-b deleted `library.css`, the
last page stylesheet (test `the retired shell, vocabulary, editor, Images, inspector and
library stylesheets stay deleted`). `the overlay keeps no retired Images page or
inspector rules` keeps the replaced card, filter, toolbar, preview, facts and inspector
rules out, and `the overlay keeps no rule waiting for a page rebuild` keeps out the
interim guards and the history page's private role colors. Every rule that stayed has
a live consumer and a marker: the Library rows windowed (D3) and the shelf's states
(D40), the history panel's wrap, archived-lane and coarse-target rules (D45), and under
**D48** the production surfaces the concept never shows, drawn in the concept's tokens
(`--green`, `--red`, `--ca-*`): recorded executions and the Recorded run dialog (#209,
D26), `pre.prompt-text` for stored text in the frozen field provenance and the Archive
negative confirmation, whose `section.prompt-block` (#235) keeps its rules too, and the
acknowledged prompt sender (spec §2.2). Rules that repeated the concept
(the `.library-grid` tracks and breakpoints, the history `.row-actions` flex row and
button size, `summary` cursors, `h2` margins, the sender and Recorded run dialogs'
colors and border) or had no effect (`min-inline-size` on block children, a
`.recorded-run-dialog pre` limit, a 700px padding that a later rule overrode) were
deleted; the workspace browser specs on Chromium and the concept specs on touch prove
the pages unchanged.

**Comparison method.** Production must equal the concept except for the recorded
deviations in the #249 design (spec §8, D-numbers). It is never compared against
blessed production screenshots.
- The fixture is `tests/browser/workspace_concept_fixture.py`, reset through
  `POST /__test__/concept/reset`. It holds the concept's six prompts and pairs, and
  24 PNGs with real links, runs and the concept's inspector facts. The sidebar counts
  are therefore the concept's own: 24, 6 and 15.
- The shared inventory is `tests/browser/concept-inventory.mjs`. One self-contained
  in-page measurer runs the same concept selectors in the immutable concept
  (`/__test__/canonical/workspace`) and in production. An absent element becomes
  `null`, so the diff names its path (for example `chrome.breadcrumbs`) instead of
  failing the measurement.
- Each page's `map<Page>Concept(reference)` applies only the recorded deviations, each
  commented with its D-number. `diffInventory` must then report nothing.
- Sizes, offsets and heights that follow from a removed element are derived from the
  concept's own measurements, never copied from production.
- `mutate(page, {css, remove})` appends a stylesheet and detaches elements, and returns
  an undo. Each comparison has a mutation check proving it fails. For the shell, a
  `.sidebar nav a { min-height: 30px }` rule must fail at `chrome.navs.0.rows.0.minHeight`
  and `.height`, and removing `.breadcrumbs` must fail at `chrome.breadcrumbs`.
- Paired concept/production captures are attached as test artifacts. There are no PNG
  baselines.

`tests/browser/shell-concept.spec.mjs` compares the shell: the sidebar, topbar, page
heading and footer, at 1600x1100 and 390x844, in dark and light, under normal and
reduced motion, on Images, Prompt library and Tags. The heading is compared in full on
Tags; on Images and Library only its frame and h1 are compared, because their copy and
actions belong to their own PRs. The spec also compares the hover state of the first
non-current nav row, the Theme select and the ComfyUI link, and the keyboard rings of
the first nav link and heading button.

Recorded shell deviations:
- **D4**: a "Theme" select with Dark, Light and System. System follows the ComfyUI
  palette, then the OS.
- **D5**: retired by Activity (#249 Activity-1). The concept's Activity button and
  Activity row are shown; their differences are D52 to D54.
- **D28**: no COLLECTIONS, and no Design review. The bottom nav is
  the concept's Activity and System, plus the production Overview hub (its own
  outline home glyph, not the concept's `activity` pulse, and no count).
  The connection reads "Your library, on your server" with a neutral muted dot.
- **D49** (saved views, #249 D16): the SAVED VIEWS group label has no "PROPOSED"
  badge, because saved views are persisted (`/prompt_manager/saved-views`). Its rows are
  the concept's `links()` rows: the search icon, the view name and no count, linking
  `#/saved/<id>`. Opening one shows its page with the saved toolbar value and Refine
  filters, and the view's name as the h1, breadcrumb and title. A value its page no
  longer accepts (a removed Source or Folder) stays default; an unknown id opens Images.
  A toolbar change made while the sources are still loading keeps the saved Source and
  Folder until discovery settles. A saved view, like Favorites, never replaces the plain
  Images or Library return state. Writes (POST, PATCH) validate strictly; a state over
  4 KB is refused in the browser with "The view is too large to save. Shorten its
  search, folder or tags and try again." Startup and restore check only the table's
  structure and that each row's state is a JSON object of at most 4 KB, never the value
  rules, so a row saved under older rules (a missing or retired key, a sort no longer
  offered) never stops the database from opening: reads fill missing keys with their
  defaults, drop unknown keys, default invalid values and make the name displayable
  (`tests/test_saved_views.py`). A live row whose state is not a JSON object never
  blocks startup either: its id is logged as a warning, the row is left in place, the
  listing and rename skip it, and its `#/saved/<id>` link behaves like a missing view.
  Restore stays strict and rejects such a backup ("Saved views contains invalid values"). Only request validation is a 400; any other failure is
  a logged 500 with a generic message. The Favorites count re-read after starring is
  coalesced to one read 300 ms after the last toggle.
  `a saved view row matches the concept's` saves one view in both documents and
  compares the rows; `tests/browser/workspace-saved-views.spec.mjs` covers saving,
  reopening, the defaults that replace rejected values, and failed reads and saves.
- **D50** (retired by #249 PR F, ruling R5): SAVED VIEWS now lists the concept's
  "Needs review" row after Favorites, with its count; the comparison maps nothing out
  for it (only the concept's sample count "3" reads as the fixture's "0").
- **D51** (maintainer ruling, 2026-10-21, beyond the concept): SAVED VIEWS starts with
  the concept's Favorites row (star icon, name, count). Its count is the Favorites
  listing's own `limit=1` total (`/images/output?mode=workspace&favorites=1`), follows
  the nav-count rules (hidden until loaded, absent when its read fails, never a
  placeholder) and is re-read after every confirmed star or unstar, on any page.
  `#/favorites` is Images filtered to favorites, named "Favorites" (concept
  `renderImages`); `favorites=1` keeps only the selected roots' favorites, read per root
  from `image_favorites` and matched against the scan with the toggle's case folding,
  and a failed read is an error, never an unfiltered page. Unlike the concept, an
  unstarred card stays on Favorites (showing ☆) until the next load, so the star can be
  undone in place. Favorites neither restores nor replaces the Images return state.
  Save view there saves `favorites: true`, so the view reopens the favorites. An open
  saved view's heading leads with **Edit**: "Edit saved view" with "View name" and
  Delete… (danger), Cancel and Save. Save re-reads the list, retitles the page and
  toasts "Saved view renamed."; an empty name keeps the dialog open. Delete… swaps the
  same dialog to "Delete saved view" (Cancel / Delete; never `window.confirm`; its Cancel
  closes the dialog without deleting rather than returning to Edit); Delete
  removes the view, replaces the dead route with the view's plain page and toasts
  "Saved view deleted.". Failures toast "The view could not be renamed. Try again." or
  "The view could not be deleted. Try again."; a view another tab already deleted counts
  as deleted. `tests/browser/workspace-saved-views.spec.mjs` covers the row, count,
  route, star updates, Save view on Favorites, Edit, and the failure paths;
  `tests/test_image_favorites.py` covers `favorites=1`.
- **D52** (Activity, #249 Activity-1): the top-bar `button#activity-open` and the
  sidebar's Activity row (the concept's `activity` pulse, first in `system-nav`) count
  this session's *running* jobs. The concept counts
  every sample job. `span.count#activity-count` is `hidden` at 0 (its text stays "0"),
  and the row has no `.nav-count` then. The Activity heading has no "Unified activity ·
  proposed" chip (prototype copy). At `max-width: 700px` the full-width `.top-actions`
  row may wrap (`flex-wrap: wrap`), so the button, Theme and ComfyUI stay inside a
  zoomed phone. The shell comparison hides the concept's count at runtime
  (`#activity-count { display: none }`), so the concept's own CSS lays out its button
  without it, and maps the row's count to none, the button's count to "0" and the
  phone row's `flexWrap` to `wrap`.
- **D53**: while a job runs, the approved Activity mockup's progress pill sits before
  the Activity button on every page: `a#activity-pill.activity-pill`, "Scanning · 41% ·
  8,240 / 19,622" (the verb by job kind, the server's floored percentage, and the file
  counts once the total is known), a 70px mini bar, `href="#/activity/<id>"` and the same
  text as its `aria-label`. With no running job it is `hidden`, which the shell
  comparison maps as a hidden item before the button. At `max-width: 700px` it keeps the
  verb and percentage only; on coarse pointers it is 40px tall like the concept's
  buttons. Its rules are in `workspace.css` under D53.
- **D54**: each `.activity-item` is a real job of this session (`GET
  /prompt_manager/jobs`), running first, then finished newest first. The concept's
  structure and CSS stay: `.panel-heading` > `h3` "✓ <title>" for a completed job and
  "○ <title>" otherwise, plus `span.chip`, then `p`, and for a running job `<progress
  max=100>` ("<title> progress") and `.row-actions`. The chip is green "Completed" or
  blue "Running", "Cancelled", "Failed" or "Ended". The line reads "Started HH:MM · K
  output folders · closing this view does not cancel it" while running, "Finished HH:MM
  · N added · M errors", "Cancelled HH:MM at a file boundary · N added · completed work
  is kept", "Stopped on an error at HH:MM · N added · M errors", or "This job ended when
  ComfyUI restarted." The running actions are "Cancel job" only (no "Simulate
  completion"); a failed cancel toasts "The job could not be cancelled. Try again.". With
  no job the section says "No jobs have run since ComfyUI started. Scans and other long
  operations appear here." `#/activity/<id>` is that job's own view (D56). When a job
  ends while its Cancel job has focus, focus moves to the item's title link.

  The Activity store reads the list once, then follows each running job over
  `/prompt_manager/jobs/<id>/events`. A dropped stream re-reads the list after 1 s,
  doubling to at most 30 s; a received event resets the wait, and so does a good list
  read after failed ones or after any read that was not for a dropped stream. A running
  job the server no longer lists, or one whose server could not be read twice in a row,
  is shown as ended, so no pill stays; one failed read (a proxy's 502) changes nothing.
  A finished job the server no longer lists (it keeps 20) is dropped. Numbers are
  grouped "en-US", as the server's lines are. While the tab is visible the list is read again every 15 s (and whenever
  Activity opens), so a job started in another tab or on the legacy admin page appears
  without a reload. Toasts: "Scan started." after this tab's own start; "Scan finished
  · N added · M errors", "Scan cancelled · N added" or "Scan failed · N added · M
  errors" when a job this tab saw running ends (plural helper for "error").
  `tests/js/workspace-activity-store.test.js` covers the store, its backoff and the
  copy; `tests/browser/workspace-activity.spec.mjs` covers the list against the
  concept's own items (`renderActivity` and `startJob`), the pill and counts, a phone,
  reload and a second tab, Cancel job, a restart and an unreachable server, and the
  toasts, with fake jobs from `tests/browser/workspace_activity_fixture.py`
  (`/__test__/jobs/*`, including a simulated restart that drops the job streams).
- **D55** (Scan for metadata, Activity-1 Task 3): Images → Import keeps Rescan as the
  quick refresh (D15) and then shows the concept's own "Scan an output folder → Review
  scan" row as "Scan for metadata" ("Discover and enrich existing files using the
  configured sources.", button "Review scan"). It opens the concept's `scan-review`,
  "Review metadata scan": no sample-plan `.demo-note` (prototype copy); Sources and
  Scope in a `dl.meta-grid` (the concept's maintenance plan uses `dl`; `scan-review`
  uses a `div`) reading "N output folders" (the discovered sources) and the concept's
  "New and unresolved files" (the D58 checkbox switches it to "All files"); the
  concept's sentence; then
  a backup `.notice` (the concept's System notice box): `h3` "Back up the database first
  (recommended)", "Downloads a copy of the prompts database before the scan changes any
  records." and "Back up now", which downloads the existing `/prompt_manager/backup`
  copy (as the legacy admin page does), reads "Backing up…" while aria-disabled, keeps
  the dialog open and toasts "Database backup downloaded." or "The database could not be
  backed up. Try again."; then Cancel / Start scan (primary; the concept's "Simulate
  scan"). Start scan closes the dialog, starts the session scan job (`POST
  /prompt_manager/jobs/scan`), toasts "Scan started." and opens `#/activity/<id>`; a
  failed start toasts "The scan could not start. Try again.". The scan's progress line
  now groups thousands ("Processing file 8,240 of 19,622"), as the mockup does.
  `tests/browser/activity-concept.spec.mjs` compares the dialog with the concept's
  `scan-review` and its box with the concept's System `.notice` (dark and light,
  Chromium and touch, with a CSS and a DOM mutation check); `images-concept.spec.mjs`
  maps the Import row; `workspace-images.spec.mjs` covers the dialog, the backup and
  Cancel.
- **D56**: one job's view at `#/activity/<id>`, the approved mockup
  (`activity-scan.html`, "100% spot on"), which the concept does not have. The page
  heading names the job (h1, document title "<title> · Prompt Manager"; the breadcrumb
  stays "Activity"), its line is the D54 item line, and its actions are Download report
  (disabled until Activity-2) and, while the job runs, "Cancel scan" (`.danger`; "Cancel
  job" for another kind; a failed request toasts the D54 message). Then `section.surface`
  "Progress": `.panel-heading` (h2, `span.small` with the server's "Processing file N of
  M · about X min left", or after the end "N of M files processed", "Cancelled after N
  of M files", the error line, or the restart line), `<progress max=100>` ("Scan
  progress") and `dl.stat-row` tiles in order: Added to Library (`.added`, green),
  Already known, Needs review (`.warn`, warning), No usable metadata, Errors (`.bad`,
  red). Then `section.surface` "Results": `label.search-field` "Filter by file path…"
  (`aria-label` "Filter results", case-folded match on the root label and path), and
  `.outcomes` with `.outcome-list` (`button[aria-pressed]` label + count, in the
  mockup's order Added, Needs review, Errors, No usable metadata, Already known; the
  view opens on the first with files) beside `.outcome-files`: one `.file-row` (`.path`
  root-relative, `.why` the server's detail) per listed file. The detail wording is the
  builder's (approved with Task 1): "Added to the Library as “title”", "Linked to
  “title”", "Already linked to “title”", "Needs review: …", "Error: …", where the title
  is the prompt's Library title (its stored title, else its text's first line), not the
  mockup's sample "→ “title” · new positive + negative". Each log line is "HH:MM:SS
  <prefix> <root label>/<path> · <detail>" rather than the mockup's "[HH:MM:SS] + path →
  prompt #4812 (new)". The header's actions sit in the page heading's
  `.heading-actions`, where the mockup has a `.panel-heading > .row-actions`. An
  empty outcome shows "No files in this group."
  or "No listed file matches this filter.", and the note "Showing N of M" when fewer
  than the counter are shown (the server lists 200 per outcome), with "each row opens
  the image in the viewer" for Added and Already known. Then `details.term`, collapsed:
  "● Scan log — raw detail, click to show" and `pre[tabindex=0]` with the raw lines.
  The summary follows the store and carries `listed` (rows per outcome) and
  `log_length`; the rows and log are read from `GET /prompt_manager/jobs/<id>` once on
  opening, then at most once a second only while what it lists changes (or, with the
  log open, its length), so a job whose lists are capped and whose log is closed is not
  re-read; opening the log reads its new lines. When the job ends while Cancel scan has
  focus, focus moves to the job's heading. An unknown id, or a job the store's list no
  longer has, shows "This job is not in this session’s Activity. Jobs are kept until
  ComfyUI restarts." and "All activity"; a job lost with a restart keeps what was read.
  At `max-width: 700px` the tiles take two columns and the outcome list stacks above
  the files. Each Activity item's title is now a link to its view, in the title's
  colour. The rules are in `workspace.css` under D56. `activity-concept.spec.mjs`
  restates the mockup's structure and CSS values and checks them with mutations;
  `workspace-activity.spec.mjs` covers the counters, live updates, viewer rows, the
  filter, the 200-row cap, Cancel scan, the busy start and an unknown or lost job.
- **D57**: rows under Added and Already known are `button.file-row`s that open the
  existing media viewer on that one file, by the identity Images opens it by
  (`root_index:path`). Each scan row records its root's listing index (`root_index`,
  the place in `_get_all_output_dirs`) beside its label. The row's source is that index
  in `/prompt_manager/gallery/subfolders?mode=workspace` and must still carry the row's
  label, so a reordered or replaced source never opens another root's file; the
  workspace listing's new `path` filter (one exact relative path, case-folded, at most
  1,024 characters, no traversal) returns that file. A row without an index, a changed
  source or a file the listing no longer has toasts "This file is no longer in Images.
  Rescan from Import and try again."; other rows are plain text, with no new file
  route. A start refused because another record-changing job runs toasts "Another job
  is running: <title>." with the title linking that job's view (the shared toast now
  accepts link parts). A toast with a link stays 20 s instead of 6.5 s, and any toast
  stays while the pointer or focus is on it.
- **D58** (scan scope, Activity-2 Task 4): the concept's `scan-review` states a fixed
  scope, "New and unresolved files". Production keeps that value as the default and adds
  the concept's own System toggle after the `dl.meta-grid`: a `.setting-row` with the
  label "All files", the hint "Also read the files already linked to their prompt
  again." (the switch's `aria-describedby`) and the Toolkit switch (`label.ca-switch`, a
  checkbox with `role="switch"`, as the concept's `hydrateSwitches` renders it),
  unchecked. Checking it switches the Scope value to "All files"; Start scan sends
  `POST /prompt_manager/jobs/scan {scope: "new" | "all"}` (no body, an empty body or no
  `scope` is "new"; another value is a 400).
  - In "new" the scan skips, before reading any metadata, the files the Library already
    links with a resolved link and that are unchanged on disk: exactly one
    `generated_images` row for the file, exactly one distinct pair link on it
    (`image_prompt_pairs`, written only by resolved ingestion), and a stored
    `file_size` equal to the file's size in this walk. Both paths go through
    `normalize_image_path` (absolute, normalized, `os.path.normcase`); a row without
    `file_path` matches by its `image_path`.
  - Read again: files linked without a pair (the monitor's queue and run-key links,
    fallback links made before #342, the legacy positive-only import), linked to more than one prompt or pair, not linked, a
    file whose size changed (one replaced at the same path, e.g. a reused ComfyUI
    counter name, is read and its conflict lands under Needs review), and a row with no
    stored size (unknown is not known). The scan now stores each file's size when it
    ingests it, so a file read once is skipped by the next "new" pass; the first pass
    after the upgrade fills the missing sizes, which reports those files as enriched
    (Added) instead of unchanged.
  - The known set is one streamed query that keeps only the scanned paths. If it cannot
    be read, the scan logs "Known files could not be read · reading every file" and
    reads every file.
  - A skipped file counts as processed and unchanged in the legacy counts (not as
    found) and under Already known, with the detail "Skipped · already linked" and the
    log line "= <root>/<path> · Skipped · already linked"; the log's first line names
    the scope ("Scan started · N output folders · new and unresolved files" or "· all
    files"). A legacy-page scan that attaches to a running "new" job therefore shows
    the skipped files in its `unchanged` count.
  - "All files" reads every file, as the scan did in Activity-1, and the legacy
    `/prompt_manager/scan` route always does when it starts the scan (its SSE payload
    keys are unchanged).
  - `activity-concept.spec.mjs` compares the switch with the concept's System → Sources
    toggle ("Watch for new outputs"), without its place-dependent top margin;
    `workspace-activity.spec.mjs` covers the Scope value, the description and the
    request both ways; `tests/test_scan_scope.py` covers the skip, the size rule, the
    replaced file, the re-read of unresolved files, "all", the legacy route, the
    fallback, the empty body, the 400 and Windows case folding.
- **D59** (job report and log, Activity-2 Task 5): the job view's "Download report" is
  enabled. It fetches `GET /prompt_manager/jobs/<id>/report` through the api-client URL
  (so a proxy mount works) and saves a JSON attachment named
  `prompt-manager-<kind>-<YYYYMMDD-HHMM>.json` (server local time), then toasts "Job
  report downloaded." (or "The report could not be downloaded. Try again."). The report
  holds the summary, every counter, each outcome group's exact `count`, its listed
  `rows` (at most 200) and a `truncated` flag, the scope and the log; rows and log lines
  carry root-relative paths only, never an absolute path (an unknown id is a 404).
  A job lost with a restart has no report on the server, so its Download report is
  disabled; a view left while the report downloads still saves it but shows no toast.
  The scan log opens at its newest line every time it is opened and follows new lines
  only while the reader is at the bottom; a reader who scrolled up keeps the position
  while it stays open (checked on Chromium, Firefox and WebKit).
  `test_report_is_attachment_without_absolute_paths` and the browser tests `download
  report saves the job JSON` and `log keeps the reader's scroll position` cover it.
- **D29**: no prototype copy. There is no "Interactive concept" badge and no
  "What changes & why" button. The footer reads "ComfyAssets · Local tools. Your
  library.", and the title has no " concept" suffix. Save view's dialog action reads
  "Save", not "Apply in prototype", and its toast drops the demo's word "Sample".
- **D31**: at `max-width: 950px` the overlay caps the one-column shell at
  `minmax(0, 1fr)`, so extreme zoom cannot widen it to the sidebar's min-content.
  On the Images cards (#249 C2) a card's info row stacks its text above Info and Tag
  once the row is narrower than the narrowest bounded card (120px; a list row's info
  is what its art leaves), and the two buttons wrap when even that row cannot hold
  them (`stacksCardInfo`, `tests/js/workspace-image-layout.test.js`).
- **#245 D2 (retired, #249 C2)**: the Tags heading shows the concept's "Auto-tag images";
  it opens the Tag image dialog (D115 when nothing is selected).
- **Brand name**: not a deviation. The brand link's accessible name matches the
  concept's, mark included, because production's `.brand-mark` is not `aria-hidden`
  either. The inventory compares `brand.name` and `brand.mark.hidden`.

```sh
npm run --prefix tools/vendor check
npm test --prefix tools/vendor
npm run --prefix tools/vendor test:workflows -- shell-concept.spec.mjs \
  --project chromium --project touch --project firefox --project webkit
```

`tests/browser/images-concept.spec.mjs` compares the Images page (#249 C7): heading,
toolbar, filter row, caption, grid, the first six cards, the continuation and the panel
box, in Grid, List, Masonry and the Large and Compact sizes, at 1600x1100 and 390x844
in both themes and motion settings; grid, cards and panel also at 1200x900 (2 columns,
275px panel) and 1800x1100 (4 columns, 310px panel). The concept is compared once its
own Load more has loaded all 24 fixture images (D37: it loads 12 per batch, production
40); the Load more row is compared before the press in both documents. Behaviors: hover
zoom, the 2nd card opens the viewer titled "<title> · <filename>", Escape returns focus
to it (production only: the concept re-renders its grid and drops focus), Info on the
3rd card moves `.selected`, searches for "alpine" and for one image, and Load more with
its toast. `mapImagesConcept` applies D3, D8 (the selection bar's real actions only),
D10, D14, D15, D17, D29, D32, D35 (singular counts) and D36 (categories
sorted alphabetically). Removed toolbar and filter-row items flow again from the
concept's own boxes (`reflow`), checked against the concept page with those items
removed at runtime. Masonry tops and heights are compared unrounded, within one layout
unit per card above in the column (D3). `one footer height for the collection (D3)`
seeds two seedless files (`seedless_images` on `/__test__/concept/reset`, with
`short_seedless` so their "96 × 72 · PNG" subtitle stays on one line beside Info and
Tag) and asserts that every row reserves the tallest footer and that their own footer
is shorter than the row it reserves.

### Images page controls and recorded deviations (#249 PR C)

The Images page renders the concept's DOM with real data. Its controls, top to bottom:

- **Heading**: "Images" / "Your generations, their prompts, and the next idea.",
  `Import` (opens "Import & inspect") and the shell's `New prompt`.
- **Toolbar** (`toolbar.mjs`, shared with the Library in PR E): `#search` "Search
  prompts, tags, filenames…" (debounced 250 ms; filename, linked positive text and
  Library title, and tag names), `#category-filter` "All categories" plus the
  Library's categories, `#sort` (Recently added, Name A–Z, Highest rated, then Oldest
  first and Name Z–A), and `Filters` (opens "Refine this view").
- **Filter row**: Grid / List / Masonry (`.segmented`, group "Image layout"),
  "Thumbnail size" Comfortable / Large / Compact, the scope chip ("All output folders"
  or "<source> · <folder>"), and "Group by positive prompt →" (goes to the Library).
- **Caption**: "N of M images · K positive prompts" (`#result-count`) and, under
  Recently added only, the newest result's day.
- **Grid** (`#image-grid`): concept media cards. The image opens the viewer
  (click, Enter, Space); Info selects the card into the side panel; the star toggles
  the favorite; arrows, Home and End move between cards. Bounded rendering keeps the
  DOM small, and returning to Images restores filters, layout, size, selection and
  scroll.
- **Continuation** (`#gallery-continuation`): scrolling near it loads the next page;
  `Load more images` and `Retry` do the same by hand, with a toast per batch.
- **Viewer** (`dialog#viewer-dialog`): "<title> · <filename>", the concept toolbar
  (a "Media controls" group), the shared file inspector beside the stage, and Escape
  returns focus to the opening image.

How the earlier workspace Images filters moved (old → new). The legacy `/gallery`,
`/admin` and `/metadata` pages are unchanged.

| Before PR C | Now |
| --- | --- |
| "Search filenames" (filename or folder) | toolbar search, which also matches linked prompt text, title and tags (D12) |
| Source | "Refine this view" → Source (D14); shown in the scope chip |
| Folder | "Refine this view" → Folder (D14); shown in the scope chip |
| Media (All media / Images / Videos) | "Refine this view" → Media (D14) |
| Sort: Newest first / Oldest first / Filename A–Z / Filename Z–A | toolbar sort: Recently added / Oldest first / Name A–Z / Name Z–A, plus Highest rated (D13, D14) |
| (none) | toolbar category and "Refine this view" → Tags, through the linked positive (D13, D14) |
| Refresh | Import → "Scan an output folder" → Rescan (D15) |
| Grid / List / Masonry, Comfortable / Large / Compact | the same choices in the concept filter row |
| Card image → side panel; preview icon → viewer | image → viewer; Info → side panel (D19) |
| "All images" heading and count | the concept caption and count line (D17, D35) |

Recorded deviations on this page (spec §8):
- **D3**: bounded rendering stays. Every grid row reserves the tallest measured card
  footer, masonry tops may differ by sub-pixel snapping (at most one layout unit per
  card above in the column), and a List row's info takes its natural height, centred
  beside the 160×95 art.
- **D29**: no prototype copy (shell). The Activity button and pill are the shell's
  (D52, D53).
- **D7**: the star is a real persisted favorite with "Added to favorites." /
  "Removed from favorites." and a rollback toast on failure.
- **D8**: every Images card has the concept's select checkbox before the star; the
  `#bulkbar` shows "N selected" and only the real actions in the concept's order (Add
  tags, then D85's Auto-tag selected, Set category, Export metadata, Clear). Add to
  collection waits for collections; "Remove links…" is dropped. (Amended by #249 PR D:
  Add tags ships, see "Bulk tagging from the selection bar"; #392 adds range, keyboard,
  touch and "all N matching" selection, see "Selecting many images", D135.) The selection survives Load more and
  bounded re-mounting and is cleared whenever the collection is replaced (search,
  category, sort, Filters, source change, rescan, reload). A return to Images restores
  the saved collection unreplaced and keeps its selection; a full page reload starts
  empty. The Library-embedded gallery has no checkbox and no bar. Export metadata reads
  each checked image's inspection (at most 4 at a time) and downloads one
  `prompt-manager-images-metadata.json` (`format`, `version: 1`, `exported_at`, and the
  inspector's own "Download metadata JSON" envelopes in listing order); the button is
  `aria-disabled` and ignores a second press while it runs, the selection is kept, and
  the toast names the unread images (none read: no download); leaving Images aborts the
  reads with no download and no toast. Set category opens the
  concept's `simpleInput` dialog ("Categorize selected images", `input#action-input`
  labelled Category, Cancel / Apply) and posts the selection's distinct linked positive
  ids to `/prompt_manager/bulk/category` (images have no category; the filter matches
  the linked positive's). With no linked image checked it posts nothing and opens no
  dialog; an empty value keeps the dialog open; the field stops at the server's 100
  characters; success reloads the categories and the listing together (clearing the
  selection, focus to the heading, and, as any replaced collection does, the inspector
  moves to the first listed image); failure keeps the selection. The write carries no
  page signal: leaving Images while it runs lets it finish without a toast, and the return
  loads the collection afresh instead of the saved visit. Three deviations from the
  concept's `simpleInput`: the field starts empty (the concept pre-fills its demo
  "Landscapes"), the action reads "Apply" (not "Apply in prototype"), and the
  nothing-linked check runs on the Set category press, so no dialog opens. The route
  rejects anything but 1-1000 positive integer ids within SQLite's INTEGER range
  (deduplicated) and a category that fails `validate_category`. `images selection bar matches
  the concept` compares the bar and the checked card with one card checked; the omitted
  actions flow again from the concept's own boxes, checked against the concept page with
  them removed at runtime.
- **D9 (retired, #249 C2)**: every card shows the concept's Tag (`tagImage`, "Tag or
  auto-tag <filename>") after Info; it opens the Tag image dialog without selecting
  the card (see "Tag image dialog" below).
- **D10**: the card title is the linked positive's display title, else the filename;
  the `h3` keeps the relative path in `title`.
- **D11**: the subtitle is "W × H · EXT · Seed N" with unknown parts omitted.
- **D14**: Source, Folder, Media and Tags live in "Refine this view" (no "Prompt
  evidence" field, no scope note); the primary action reads "Apply filters"; extra
  sorts come after the concept's three; long folder names wrap inside the scope chip.
  Folder lists only the chosen Source's folders (each workspace root lists its own),
  and an unchanged Apply or Clear reloads nothing.
- **D15**: Import offers "Inspect a local image" (`#/inspect`) and "Scan an output
  folder" (Rescan, `/gallery/rescan`); no "Import portable recipes". The concept has
  no rescan copy: toasts say "Rescanning output folders…", then "Rescan finished. N
  files in M output folders." (the scan's `total` and `roots`) or "Rescan could not
  finish. Try again."; while it runs, a reopened Import shows an aria-disabled
  "Rescanning…".
- **D16 (retired)**: Save view is the concept's own toolbar button, between sort and
  Filters, on Images and the Prompt library (see D49). It saves the visible toolbar value
  with the Refine filters; the comparisons no longer map it.
- **D17**: the caption date is the newest result's day under Recently added (", YYYY"
  outside the current year), hidden under every other sort.
- **D18**: the concept hover zoom, with no sticky zoom on hover-less (touch) devices.
- **D32**: the layout control may wrap, so the filter and continuation rows never
  overflow at 390px and 200% zoom.
- **D33**: after Load more or Retry, focus moves to the first new image without
  scrolling, and only while focus is still on the pressed control or `body`.
- **D34**: the viewer keeps its toolbar under the stage in `.viewer-left`, a flex
  column above 700px, `max-height: min(94dvh, 94%)`, the "Download original" link,
  the appended position and reason lines, full-stage video and failure overlay,
  40×40 coarse targets, production's control names and "Media controls" group, and
  `overscroll-behavior: contain`.
- **D35**: counts pluralize ("1 positive prompt", "1 image", "1 more image loaded").
- **D36**: the category list is the Library's, sorted alphabetically (case-insensitive)
  by the page, whatever order the server sends.
- **D37**: production loads 40 images per page (the concept 12), so the continuation
  and toast use 40; after Escape production returns focus to the opening image.
- **D44** (#249 PR E): Filters reads "Filters · N" while N of Source, Folder, Media and
  Tags are set in "Refine this view" (see the Prompt library deviations).
- **D20**: the side panel shows only the concept content; "File & provenance",
  "Workflow & raw metadata" and the downloads are only in the viewer's details
  (`#viewer-details`: the panel content without the cover in a class-less wrapper, the
  two disclosures, then the pair send and Prompt history buttons). "File & provenance"
  keeps the honest facts (current-file note, Source, Path, Size, Dimensions, Format,
  Media type, Modified, the reported-location occurrence, the saved-association
  sentence, Download original) and the concept's `Prompt evidence: <badge>` line in the
  D21 badge's words in lower case, left out when there is no badge. Raw records keep
  their names, representations and the two downloads with their unavailable reasons;
  the viewer's pair send and history buttons may wrap at 200% zoom. Tag chips in the
  viewer are plain (the concept's go to the Library). The details' disclosure
  summaries and "Download original" keep 40px coarse targets (D34). Prompt history
  closes the viewer and, when its target is the route already shown, re-renders it
  (the concept's `go()`).
- **D20/D21 side panel**: the sticky panel scrolls inside the window
  (`max-block-size` with `overflow: auto`, not the concept's `overflow: hidden`), so
  long real prompts keep the footer reachable; an empty badge or tag `.tags` row is
  left out (the concept always renders both); the footer, its pair send and Prompt
  history appear only with resolved prompt roles or a saved link; a video file keeps
  `video.detail-cover`; and only the linked positive's own tags are `button.chip`
  filters, while recipe, recorded-run and embedded tags stay plain `span.chip`.
  Amended by D88 (#249 PR C): the file's own current image tags lead the same row
  and are filters too; a linked tag already shown (case-insensitive) is left out.
- **D22**: an empty positive reads "No positive prompt (explicitly empty)" (the concept
  applies its negative wording to both roles), and a confirmed `p.prompt-text` is
  focusable (`tabindex=0`, marked focus ring) so long prompts stay keyboard-readable.
- **D38 Show in folder** (maintainer decision 2026-10-20, beyond the mock): in the
  viewer's File & provenance the Path value stays text, followed in its `dd` by a
  "Show in folder" button (`Show <filename> in folder`), only for a listed file
  (`root_index`, `root_scope`, `path`), so never in the side panel or a recorded run.
  It POSTs that identity as JSON to `/prompt_manager/images/reveal` and toasts:

  | Result | Toast |
  |---|---|
  | Success | "Opened the folder on this computer." |
  | 403 `not-local` | "Show in folder works only in a browser on the computer running ComfyUI." |
  | 501 `unavailable` | "No file manager is available on this computer." |
  | 429 `busy` | "The folder is already opening." |
  | Anything else (415, 404, 409, 500, offline) | "The folder could not be opened. Try again." |

  The route is local-only. It answers 403 `not-local` unless all of these hold: no
  forwarding-style header (`Forwarded`, `Via`, `X-Real-IP`, `CF-Connecting-IP`,
  `True-Client-IP`, `X-Client-IP`, `X-Cluster-Client-IP`, `Fastly-Client-IP`, and any
  `X-Forwarded-*`, `X-Original-*` or `*-Client-IP`); a loopback `request.remote`; a
  loopback `Host` (`localhost`, `*.localhost` or a loopback address, against DNS
  rebinding); an `Origin` equal to the request's own scheme, host and port (absent,
  `null`, another port or another scheme is refused; browsers send `Origin` on every
  fetch POST); and, when sent, `Sec-Fetch-Site: same-origin`. A body that is not
  `application/json` is 415 `unsupported-media-type`, so any cross-origin request
  needs a CORS preflight. The body is strict (an int root, a 64-hex scope, a bounded
  relative path; absolute, drive and `..` paths are 400), the scope goes through
  `_gallery_scope_error` (409 `source-changed`), and the path resolves only inside
  that root to a regular media file (403/404; a resolution `OSError` such as
  `ENAMETOOLONG` is the same JSON 404, and the client's path is never logged at error
  level). One reveal runs at a time per process with a 1 s cooldown after it
  (`RevealGate`, 429 `busy`), on its own single worker (`reveal_executor`), never
  ComfyUI's default executor. Just before launching, `utils/file_reveal.py` re-checks
  that the real path is still a regular file inside the root (a swapped parent is
  404). Windows starts no process: `ILCreateFromPathW` and
  `SHOpenFolderAndSelectItems` select the file through the shell API (Explorer's own
  command line splits on commas, so a path like `output\a,\Windows\...` must never
  reach it), falling back to `os.startfile(folder, "explore")`. macOS runs
  `/usr/bin/open -R`; Linux asks FileManager1 `ShowItems` with `dbus-send
  --print-reply --reply-timeout=2000` (a real reply or a fallback) and else runs
  `xdg-open` on the folder. Those openers are argv lists resolved only on a fixed
  system PATH, with no shell, DEVNULL streams, `close_fds`, their own session, a
  minimal environment (no venv PATH, PYTHONPATH or API keys) and a daemon-thread
  reaper. `tests/test_image_reveal.py` mocks `Popen`, `sys.platform`, `shutil.which`,
  the shell API and `os.startfile`; the browser server's opener is a stub
  (`POST`/`GET /__test__/reveal`) with no cooldown. The inspector comparison reads
  closed disclosures as hidden, so the button needs no mapping there.

  Residual risk, accepted: a reverse proxy or tunnel that adds no forwarding header
  (a bare nginx `proxy_pass` to 127.0.0.1, `ssh -R`, `socat`) cannot be told apart
  from a local client, and a non-browser client behind it can forge a matching
  `Host` and `Origin`. Such a client can open a folder of a listed file on the host's
  screen, at most once a second, and learn whether that file exists (which the
  listing routes already tell it). Operators who expose ComfyUI that way accept this.
- **D39** (ruled 2026-10-21: fallback): a recipe-linked image shows its recipe's own
  category when set, otherwise the linked positive prompt's, and its recipe's own tags
  when it has any, otherwise the positive's; each field falls back on its own and the
  two tag sets are never merged. Set category (#259) writes the positive's category and
  the Images category and Tags filters match the positive's, so the panel now agrees
  with them whenever the recipe adds nothing. The inspection response names each
  field's source (`library.category_scope`, `library.tags_scope`: `pair`, `positive` or
  null); the category chip's visually hidden prefix (D21) says "Recipe category: " or
  "Positive prompt category: " from it, and tag chips are Tags filter buttons only when
  they are the positive's (#260 D2). History's media inspector uses the same rule. The
  concept shows the prompt's category and tags for every image and the concept
  fixture's recipes have neither, so the inspector comparison needs no mapping for it.
- **D42**: the concept never shows a toast and a page alert together. Production keeps
  the concept's fixed toast (centred, 22px from the bottom, 12px at 700px and below)
  and, when an Import → Rescan toast leaves the Images discovery alert
  (`.options-status`), scrolls that alert into view only as far as needed, with
  `scroll-margin-block-end: 96px`, so it is never under the toast.
  `Rescan toast does not cover the discovery alert it leaves` ends the window inside
  the toast's band at 1600 and 390 pixels and measures both boxes.

`tests/browser/inspector-concept.spec.mjs` compares the side panel and the viewer's
full details (#249 D4) for alpine_00001.png, at 1600x1100 and 390x844 in both themes:
structure, cover, heading, filename, badges, prompt blocks (role labels, actions,
text), tag chips, facts, footer, the viewer's disclosures and pair actions, and every
offered control's name and minimum height. Each role's copy and send are named after
the prompt, as the concept's are ("Copy positive prompt: <title>"). The concept loads
all 24 fixture images first (D37). Behaviors: copy shows "Prompt copied." (or the
same "Copy prompt" dialog in both); the pair send previews both roles in the concept
and is applied and acknowledged by a test ComfyUI tab in production; the `cinematic`
chip sets the Images Tags filter (D21: the concept searches the word, which also
matches the untagged forest prompt's text); Prompt history goes to
`#/library/<positive id>` (the concept's `#prompt/<id>`). `mapInspectorConcept`
applies D3 (the `.file-inspector` class of the production-only rules), D20 (the
viewer's plain tag chips, laid out by the concept page itself with its chips made
plain at runtime), the D20/D21 side-panel scroll (`overflow: auto`,
`max-height: calc(100dvh - 40px)` above 700px), D21 (the category's hidden scope
prefix) and D22 (focusable prompt text). `inspector comparison detects mutations`
changes `.prompt-heading .role-label` letter spacing and removes `.detail-footer`.

Manual check (quick), after a browser refresh on Images: hover an image (it zooms);
click it (the viewer reads "<title> · <filename>"; Escape closes it); press a card's
Info (the side panel changes); search a word from a prompt (matching images remain);
open Filters (Source, Folder and Media are there); Import → Rescan picks up a newly
saved file.

```sh
npm run --prefix tools/vendor test:workflows -- images-concept.spec.mjs \
  workspace-images.spec.mjs full-inspector.spec.mjs inspector-concept.spec.mjs \
  --project chromium --project touch --project firefox --project webkit
```

### Tag image dialog and recorded deviations (#249 PR C)

Every Images card and every Library-embedded gallery card shows Info then the
concept's Tag (`app.js` mediaCard). Tag opens the concept's **Tag image** dialog
(`web/js/workspace/image-tagging.mjs` over the C1 controller
`image-tagging-state.mjs`) in the shared modal shell with the concept's
`tagging-modal` class, so its `.dialog-actions` stay sticky while the content
scrolls. Focus starts on × and returns to the Tag button. The preview is the
capture's own `preview_url` (through `ApiClient.resolveUrl`): an `img`, or a
`video preload="metadata" muted`. The capture is released on ×, Escape, Cancel,
Save, a route change (the shell closes its dialog) and `pagehide`, which closes the
dialog (a page restored from the back/forward cache shows no dead dialog); the
release `DELETE` and the Save `PUT` are sent with `keepalive` (`ApiClient`'s
additive option), so they finish while the page unloads. A save still in flight
when the dialog closes keeps its capture until the `PUT` settles, then releases it
and still reports the outcome: the saved toast and every page's refresh, or the
failure as a toast. Generate is explicit and uses the server's real defaults
(`GET /prompt_manager/autotag/models`), and the fields show exactly those values
(they are re-rendered when the observation lands and on a model change, never while
typing); an invalid field reports native validity and sends nothing. A tag conflict
shows the current set at once: the status line appends "Saved now: <tags>." and the
manual field holds the current set ahead of the draft (repeats dropped), so the next
Save sends what is shown against the current revision. Save writes the manual
names, then the remaining chips, deduplicated case-insensitively (first spelling
wins), and toasts the mock's copy verbatim, "<n> tags saved for <filename>." even
for one tag, plus " Prompt association still awaits review." from Needs review.
Tagging one file never changes another file (an equal-byte copy included), a
prompt, a pair or a link. States the mock lacks (generating, busy, conflict,
changed storage or file, expired capture, unconfirmed save, unavailable model,
video) are text in `#tag-generation-status` only; **Retry** (D116) leads the
actions while the controller allows it, where the other workspace dialogs place
their Retry. The Tags
page re-reads its lists after every confirmed save, so the Images counts follow.

The image-tagging plan's deviation numbers were renumbered by +23 because the
Inspect plan keeps D60–D66 (plan D60 → D83, D61 → D84, D65 → D88, D68 → D91).

- **D83** (plan D60, ruling T4): the suggestions are removable chips instead of
  checkbox rows: `span.chip.tag-suggestion` in a `.tags` row, in the concept's
  checked-row tokens (accent border, `--soft-blue`), each with the name, the
  confidence (`98%` for WD14, "Suggested" for language models) and a × "Remove
  <tag>". A chip has the concept `.chip`'s 4px corner (the later `.tag-suggestion`
  row rule would give 5px) around the row's own 12px text. At most the requested
  count, all preselected. While chips exist, ruling T4's "Remove anything you do not
  want to add." (`p.small`) leads them inside `#tag-suggestions`; removing the last
  chip shows "All suggestions removed. Generate again or enter tags manually." The
  opening sentence is the concept's on-screen copy, "Generate suggestions with the
  selected model before approving.": the concept's markup carries another ("Choose a
  model… Uncheck anything…"), but its `renderTagModelOptions` (a1062–1076) replaces it
  as soon as the dialog opens, so the opened state is compared unmapped.
- **D84** (plan D61, rulings T1/T2): the Tagging model options are legacy's four
  engines in the mock's order with legacy labels and sizes (`WD14 SwinV2 · ~400
  MB`, `WD14 ViT · ~400 MB`, `GGUF JoyCaption (Recommended) · ~4.5 GB` in the
  mock's Ollama slot, `HuggingFace JoyCaption · ~16 GB`); the instructions start
  from the server's `default_prompt`.
- **D91** (plan D68, ruling T3-bulk): a **Suggestions** number field (1–100) for
  every model, prefilled from `autotag.defaults.suggestion_limit` (10); a model
  change keeps it.
- **D88** (plan D65, ruling T7/C2): the inspector's chip row (side panel and viewer
  details, after the prompt blocks) shows the file's current image tags first (the
  B3 envelope's `image_tags`, `state: "current"`, S1), then D21's linked tags not
  already shown (case-insensitive, the image tag's spelling wins). In the Images
  panel every image tag is a Tags filter `button.chip` (the filter matches image
  tags too) and linked tags keep D21's rule; in the viewer, and wherever no Tags
  filter exists (the Library-embedded gallery, Activity and recorded runs), every
  chip is a plain `span.chip`. `none` and `unavailable` add no chips and no other
  chrome; a malformed facet fails the inspection like any malformed field. The
  concept has one row of the image's own tags (`i.tags`, app.js a402), so with the
  concept image's tags saved as its image tags production still equals the concept.
  After a Tag save the selected file's inspection is re-read, so the panel redraws
  the row without reselecting and the viewer, which shares that inspection, shows it.
- **D115** (beyond the plan's list): the concept always has a selected image, so
  its "Auto-tag images" always opens the dialog. Production opens it for the image
  selected in Images when it was last left; with none it is `aria-disabled` and
  pressing it toasts "Select an image in Images first." (no new chrome).
- **D116** (ruling T5): **Retry** leads `.dialog-actions` (before Cancel and the
  primary) while a failed observation, a failed Generate, an unconfirmed save or an
  answer that needs a fresh observation (changed storage or file, expired capture)
  can be retried; the concept has no failure states, so it has no Retry. After a
  failed Generate it takes Generate's own path (an invalid field reports its
  validity); an unconfirmed save resends the identical body; after a changed storage,
  file or expired capture it observes again and keeps the typed draft (when the saved
  set changed meanwhile, the current set leads the draft and the status line says
  "The tags of this image changed since the dialog opened. Saved now: <tags>.").
  Browser tests: "D116: Retry leads the actions after an unconfirmed save…", "D116:
  Retry after an expired capture observes again and keeps the typed draft (M5)" and
  the paired `production-unknown-outcome` capture.
- **D29** (no prototype copy) in the dialog: no "Prototype · suggestions are
  simulated…" note, the status reads "Suggestions from <model>. Approve tags to save."
  (not "Sample suggestions from …"), and the toast has no " in this tab".

After a confirmed save the selected file's inspection is re-read quietly: the panel
and the viewer keep the current details until the new answer lands, with no "Loading
file details…" in between (`createInspectionController().refresh()`). While a Tags
filter or a search is active the saved file may start or stop matching, so Images
re-reads its loaded range in place (`createImageStream().refresh()`: the same query
from the first page until the loaded count, published once). The cards, the scroll
position and the selection stay while the selected file still matches; when it no
longer does, the panel follows the collection (its first file), as after a reload,
and the selection bar keeps only the checked files still listed, so its count is what
Set category and Export metadata apply to. A refresh never runs beside a page load
already in flight: asked during one, it is pending and runs once that page settles
(success or failure; a reset drops it), and Load more waits for a running refresh
(it neither loads nor moves focus). The re-read asks for pages as large as the rest of
the loaded range (at most 200 files, at least one batch), one after another (each
cursor comes from the page before); a failed re-read toasts "Images could not be
refreshed after the tag save. Refresh image collection to retry." and keeps the
cards. A save made while Images is left (Tags › Auto-tag images) marks a saved Images
visit with a Tags filter or a search stale: returning lists it again with its
filters instead of restoring the old cards. Browser tests: "the post-save re-read
keeps the panel content until the new read lands", "a save under a Tags filter
re-reads the listing: …", "a save under a search keeps the scroll position and the
loaded range", "a failed listing re-read after a save says so and keeps the cards
(M2)" and "a tag save from Tags reloads a filtered Images visit instead of restoring
it (M6)"; the pending refresh and the page sizes are unit-tested.

Concept comparison (`tests/browser/tagging-concept.spec.mjs` with
`tagging-canonical-comparison.mjs`, plan Task C4): production runs on the concept
fixture with the concept image's own tags (`cinematic, blue_hour, architecture`)
saved as its image tags, and the fake model answers the concept's own sample
(`/__test__/tagging/suggestions`: mountain 0.98, observatory 0.95, snow 0.88,
blue_hour 0.81), so the dialog's inventory (order, text, field attributes, tokens,
preview box, chips, status, sticky actions) must equal the concept's at 1600×1100 and
390×844, dark and light: opened, generated with WD14 and generated with a language
model, plus the empty result (the concept above its own threshold), the review-scoped
dialog with its scope note and the card's Info and Tag group (M1). `mapConceptTagging`
applies only D29, D83, D84 and D91; a mutation check (the model panel's border
removed, the primary relabelled) shows the comparison notices a change. Every
production-versus-concept difference of PR C and its record:

| Difference | Record |
| --- | --- |
| Removable chips with × instead of checked checkbox rows; "Remove anything…" while chips exist; the all-removed copy | D83 (ruling T4) |
| Legacy engine labels and values, GGUF in the Ollama slot, server default instructions | D84 (rulings T1/T2) |
| Suggestions count field | D91 (ruling T3-bulk) |
| No prototype note, "Suggestions from …", no " in this tab" | D29 |
| Image tags first in the inspector and viewer chip row | D88 (ruling T7/C2) |
| Auto-tag images `aria-disabled` without a selected image | D115 |
| Retry leads the actions when the controller allows it | D116 (ruling T5) |
| States the concept lacks (busy, conflict, changed storage or file, unconfirmed save, unavailable model, video) | Global Constraints: `#tag-generation-status` text only |

### Bulk tagging from the selection bar (#249 PR D)

The Images selection bar shows the concept's **Add tags** after the count, then
**Auto-tag selected** (D85), before Set category. Both take the checked files when
pressed, in listing order, as their reveal identity (`{root_index, root_scope, path}`);
more than 500 toasts "Select at most 500 images." and opens nothing. The selection
stays after every start, refusal and review. The job's start body is bounded so 500
targets of the longest legal path (1024 code points, even escaped as `\uXXXX`) fit:
`500 × (1024 × 6 + 256) + 64 KiB`; a larger body answers 413
(`tests/test_image_tag_jobs.py` "test_start_body_bounds").

- **Add tags** opens the concept's `simpleInput` (app.js a1378–1383) as Set category
  does: "Tag selected images", "Tags, separated by commas" (empty: the concept's
  "cinematic, concept" is sample text), Cancel / Apply. The names are parsed as the Tag
  image dialog's manual field parses them (trimmed, case-insensitive repeats dropped,
  first spelling wins); a field that parses to no names keeps the dialog open with the
  typed text and reports "Enter at least one tag." on the field (its native validity,
  cleared on the next edit), and nothing is sent. Apply starts the
  `image-tags` job (`POST /prompt_manager/jobs/image-tags`, `{mode: "add", targets,
  tags}`) and toasts "Tagging started."; Activity shows its progress.
- A refused start toasts why and changes nothing: another record-changing job (a scan)
  gives "Another job is running: <title>." with a link to it (D57's toast), a held model
  gives the server's `model-busy` sentence, any other coded refusal its own sentence,
  and anything else "Tagging could not start. Try again." / "Auto-tagging could not
  start. Try again.".
- When a job this page started ends having tagged files, those files are read again
  once: the selected file's inspection (its chips, D88) and, under a Tags filter or a
  search, the loaded range, as after a Tag save.

- **D85** (plan D62, ruling L1, beyond the concept): **Auto-tag selected** opens
  "Auto-tag selected images" in the Tag image dialog's shell (`tagging-modal`) with
  its model panel, headed "Auto-tag <N> image(s)" (the panel's "Auto-tag this image"
  for N files), without the preview, the manual field, the suggestions or Generate.
  Its fields show the server's defaults (`GET /prompt_manager/autotag/models`, the
  D91 Suggestions count included) once that read lands; Start waits for it. A model
  the status lists as not downloaded keeps Start `aria-disabled` and
  `#tag-generation-status` says "<model> is not downloaded. Download it in System ›
  Auto-tagging." (the server's own sentence), so no job starts only to fail.
  Start validates the fields as Generate does (native validity; nothing is sent).
  The concept comparison (`images-concept.spec.mjs`) uses the concept page itself as
  the oracle, with Add to collection and Remove links… removed and a copy of its Add
  tags button reading "Auto-tag selected" inserted after it.
- **D90** (plan D67, ruling T3-bulk): the dialog's **How to apply** radio group
  (`.field[role=radiogroup]`, radios beside their labels): **Apply automatically**
  (checked; primary "Start auto-tagging") starts the `image-tags` job
  (`{mode: "auto", targets, model_type, options}`) and toasts "Auto-tagging started.";
  **Review each image** (primary "Start review") walks the selection in the browser
  (`images-tagging-review.mjs`). Each image opens the Tag image dialog titled "Tag
  image · Image <i> of <M>" on the chosen model and options with Generate already
  running; its actions are Stop reviewing, Skip and Approve and next (Approve and
  finish on the last). Approve saves the manual field plus the remaining chips as Save
  does, without its per-image toast, and opens the next image; Skip writes nothing and
  advances; Stop reviewing, Escape and × end the session. While Approve's save is in
  flight, Skip and Stop reviewing are `aria-disabled` and Escape and × do nothing, so the
  closing count includes that save. When no suggestions are on screen (a model change
  cleared them, or Generate was refused) the primary saves the manual field only and
  reads "Save and next" ("Save and finish" on the last image). Each image is one gated
  `suggest` call, so the session holds no model between images; a `model-busy` answer
  stays on that image with its sentence in `#tag-generation-status` and D116's Retry
  until the user retries or skips. Ending toasts "<n> of <M> images tagged." and
  returns focus to Auto-tag selected.
- **D91** extends to the bulk dialog: its Suggestions field is the job's and the
  review's `suggestion_limit`.
- **D118** (copy the concept has no line for; the concept's `bulk-apply` only toasts a
  demo sentence): an Add tags field that parses to no names reports "Enter at least one
  tag." on the field; a start refused for no coded reason toasts "Tagging could not start. Try
  again." or "Auto-tagging could not start. Try again." (as D55's "The scan could not
  start. Try again."); and the bulk dialog's model panel is headed "Auto-tag <N>
  image(s)" where the Tag image dialog's reads "Auto-tag this image". Everything else
  the selection bar's tagging shows is the concept's or the plan's (D85, D90, D91).
- **D89** (plan D66, Activity for the `image-tags` job): the job's Activity item and its
  view are the scan's (D54, D56, D57, D59) with the job kind's own counters, from one
  per-kind table (`activity-kinds.mjs` since S5, D111; a kind without one gets no
  tiles, "Job log" and "Cancel job"). The titles are the server's: "Add
  tags to images" and "Auto-tag images".
  - The pill's verb is "Tagging" ("Tagging · 41% · 8,240 / 19,622").
  - The item line and the view's heading line read "Started HH:MM · N images · closing
    this view does not cancel it" (the job's image count, grouped "en-US"), "Finished
    HH:MM · N tagged · M errors", "Cancelled HH:MM at a file boundary · N tagged ·
    completed work is kept" or "Stopped on an error at HH:MM · N tagged · M errors".
  - The view's actions are Download report and, while running, "Cancel tagging"
    (`.danger`); the progress bar is "<title> progress".
  - Four `dl.stat-row` tiles in the mockup's tones: Tagged (`.added`, green),
    Unchanged, Skipped (`.warn`, warning), Errors (`.bad`, red). They keep the
    mockup's five-column row (two columns on a phone), so a tile is the scan's width
    and the fifth cell stays empty.
  - The outcome list reads Tagged, Errors, Skipped, Unchanged (what got tagged and what
    went wrong first, as the scan's), and the view opens on the first with files.
  - Rows under Tagged and Unchanged are D57's `button.file-row`s that open the file in
    the viewer by its listing identity; Skipped and Errors rows are plain text.
  - The log is "● Tagging log — raw detail, click to show".
  - The row and log wording is the job's (D1), since neither the concept nor the
    mockup has a line for it: "Added N tag(s)", "No new tags", and the skip reasons
    "Video: manual tags only", "Too large to verify", "File changed", "File not found",
    "Not a supported still image" and "The file could not be read now. Run the job again
    for it." (the capture store stayed at capacity, for example with Tag dialogs open in
    other tabs, after a short backoff of retries). Log prefixes are `+` tagged, `=`
    unchanged, `-` skipped and `!` error.
  - The progress line is the scan's: "Processing file N of M · about X min left", the
    estimate (D56's `_time_left`) shown once two files are done, each file one batch.
    An ended job never keeps it: its status line becomes "<Add tags | Auto-tag>
    <finished | cancelled> · N files processed" (or the storage-restored sentence),
    pinned by `test_an_ended_job_shows_no_time_left` (#379 live check).
  - Download report saves `prompt-manager-image-tags-<YYYYMMDD-HHMM>.json`.
  - The start toasts are "Tagging started." / "Auto-tagging started." (D85); the end
    toasts follow the design's pattern with the job's counts, as the scan's: "<title>
    finished · N tagged · M errors", "<title> cancelled · N tagged" or "<title> failed ·
    N tagged · M errors".
  - Tests: `tests/js/workspace-activity-store.test.js` ("image-tags jobs get their own
    tiles outcomes and pill verb", "unknown kinds get the generic view without the scan tiles");
    `tests/browser/workspace-activity.spec.mjs` ("an image tagging job shows Tagged
    Unchanged Skipped Errors and opens tagged rows", "cancel tagging stops at a file
    boundary", "the report download names the kind", on fake `image-tags` jobs from
    `workspace_activity_fixture.py`); `tests/browser/activity-concept.spec.mjs` ("image
    tagging job view follows the approved mockup", dark and light, with a mutation
    check). The scan's view is unchanged.

Browser tests (`tests/browser/workspace-tagging-bulk.spec.mjs`, on the tagging
fixture): "selection bar shows Add tags then Auto-tag selected before Set category",
"Add tags runs a job and the cards show the tags after it finishes", "Auto-tag
selected with Apply automatically runs a job with the chosen model, thresholds and
suggestion count", "a model that is not downloaded keeps Start disabled and says
why", "Suggestions field prefills from the setting and caps chips at the entered
count", "Review each image walks the selection one dialog at a time and only approved
images gain tags", "Review holds Stop reviewing, Escape and × while a save is in
flight, and names a manual-only save" and "a running scan makes the start report
busy". Units:
`tests/js/workspace-images-tagging-bulk.test.js`,
`tests/js/workspace-images-tagging-review.test.js`.

| Difference | Record |
| --- | --- |
| Add tags ships (Add to collection and Remove links… still omitted) | D8 (amended) |
| Auto-tag selected and its dialog | D85 (ruling L1) |
| How to apply and the Review each image session | D90 (ruling T3-bulk) |
| Suggestions count in the bulk dialog | D91 |
| Copy without a concept line: no tags, start fallbacks, the panel heading | D118 |
| Activity item and job view for image tagging jobs | D89 (plan D66) |

### Selecting many images (#392)

**D135** (beyond the concept, which draws only the per-card checkbox): Images selects
like a file manager, in Grid, List (where the concept hides the checkbox) and Masonry,
over every loaded batch. The selection is a set of image keys in display order, so
ranges include cards that bounded rendering has unmounted.

- Clicking a checkbox toggles it, and that image becomes the anchor. Ctrl/Cmd+click
  anywhere on a card (image, star, Info, Tag) also toggles it and makes it the anchor;
  nothing opens, inspects or changes the favorite.
- Shift+click on a card selects the range from the anchor to it. Shift+click on a
  checkbox gives its range the box's new state, so it can clear a range. The anchor
  stays where it is. Without an anchor, the clicked image becomes the anchor.
- In the grid, Ctrl/Cmd+A selects every loaded image; in the search box it keeps its
  text meaning. Shift+Arrow, Shift+Home and Shift+End move focus as the plain keys do,
  then select from the anchor (the starting card when there is none) to the new card.
  Escape in the grid clears the selection. Escape in the bar clears it too, and focus
  moves to the heading, as after Clear.
- Touch: a 500 ms long-press (at most 10 px of movement) selects the card and turns on
  touch selection mode. The context menu during the press and the click after it are
  swallowed, and the card has no image callout. Taps then toggle cards until the
  selection is empty. A keyboard Enter or Space keeps its plain meaning.
- A plain click keeps its meaning: the image opens the viewer, and Info inspects.
- When every loaded image is checked and the listing has more, the bar offers
  **Select all N matching** right after the count. Pressing it gives **All N matching
  selected**: later batches arrive checked, and unchecking any image goes back to the
  loaded images minus that one. Clear, Escape, and a new search, filter, sort or rescan
  end it. A refresh after a tag save keeps it. Return visits keep the loaded images,
  and the offer shows again.
- In that mode, Add tags and Auto-tag selected start the `image-tags` job with
  `filter` (the listing's own query as an object: `q`, `subfolder`, `category`,
  `tags`, `root`, `media`, `sort`, `favorites`, and with a Source the loaded root's
  `root_scope`) instead of `targets`. The server resolves the filter at start, in
  listing order, using the listing's own admission code (`_admitted_entries`). That
  means "all matching" when the action starts: an output written since then that
  matches is included. A `root_scope` that no longer names its root (the sources were
  renumbered) answers 409 `source-changed`. The limit is 10,000 images (`MAX_LISTING_SELECTION`).
  Over the limit, the client toasts "Narrow the view to at most 10,000 images." and the
  server answers 400 `too-many-images`. No match is 400 `no-images`. Auto-tag in this
  mode does not offer Review each image, because the review walks images in the
  browser. Set category posts `{images_filter, category}` to `/prompt_manager/bulk/category`.
  The server sets the category on the distinct linked positives and answers
  `prompt_count` and `skipped`. A listing whose `positive_count` is 0 toasts the
  nothing-linked sentence and opens no dialog, and over 10,000 matches it toasts the
  limit sentence. Export metadata reads every image in
  the browser, so it is `aria-disabled` in this mode, and its `title` says why.

Tests: `tests/browser/workspace-images.spec.mjs` ("Shift+click selects a range across
an appended batch in Grid, List and Masonry", "Ctrl/Cmd+click toggles a card without
opening it, and a plain click still opens the viewer", "Ctrl/Cmd+A, Select all N
matching and Add tags tag every matching image on the server", "Shift+arrow keys extend
the selection from the focused card, and Escape in the bar clears it", "a touch
long-press starts selection mode, taps then toggle, and an empty selection ends it");
units `tests/js/workspace-images-multiselect.test.js`,
`tests/js/workspace-images-tagging-bulk.test.js` ("all matching sends the listing
filter instead of targets"), `tests/js/workspace-images-state.test.js`
(`listingFilter`); Python `tests/test_image_tag_jobs.py` (`TestFilterStart`) and
`tests/test_api_workspace_images.py` (`TestListingFilterSelection`).

Native image tagging (#249 PR B routes, PR C UI): `POST
/prompt_manager/image-annotations/observe` (capture one listed file, its saved set and
`tag_revision`), `GET`/`DELETE /prompt_manager/image-annotations/capture/<token>`
(the preview and its release), `POST /prompt_manager/image-annotations/suggest` (the
gated top-N suggestions, at most the requested count) and `PUT
/prompt_manager/image-annotations/tags` (re-hash, then save one byte record's set).
Cheap reads follow spec amendment S1 (the verified `(size, mtime_ns)` fingerprint;
residual risk above, under "Image annotations schema"). No automated test downloads
or loads a model: the browser server injects `TestModel`, whose `download_model`
raises, and every tagging spec asserts no `/autotag/download/` request. Capture
limits are those of the capture store above (256 MiB per file, 8 completed captures,
512 MiB reserved, 900 s idle, 1800 s hard). The browser fixtures release every capture
on reset. PR C is HTML, CSS and JS only (no restart); the PR B routes it calls need a
maintainer ComfyUI restart once.

Test files: `tests/browser/workspace-tagging.spec.mjs` (the fixture is
`tests/browser/workspace_tagging_fixture.py`: two files sharing one prompt and
pair, an equal-byte copy, a video, an unlinked file, the fake model with `hf`, and
no model files; every test asserts no download request),
`tests/js/workspace-image-tagging.test.js`,
`tests/js/workspace-image-tagging-state.test.js`,
`tests/js/workspace-images-lifecycle.test.js`, and for D88
`tests/js/workspace-file-inspector.test.js`,
`tests/js/workspace-inspection-state.test.js` and `tests/browser/inspector-concept.spec.mjs`
("image tags saved on the concept image keep the inspector equal to the concept"), and
for the comparison `tests/browser/tagging-concept.spec.mjs` and
`tests/js/workspace-images-state.test.js` (the in-place refresh), and on all four
projects `tests/browser/workspace-tagging-lifecycle.spec.mjs` (leaving the page with
the dialog open releases the capture with no console error; closing during a save
releases it after the save and still reports it).

```sh
node --test tests/js/workspace-image-tagging*.test.js tests/js/workspace-images-lifecycle.test.js
npm run --prefix tools/vendor test:workflows -- workspace-tagging.spec.mjs tagging-concept.spec.mjs \
  workspace-vocabulary.spec.mjs images-concept.spec.mjs --project chromium --project touch
npm run --prefix tools/vendor test:workflows -- workspace-tagging-lifecycle.spec.mjs tagging-concept.spec.mjs \
  --project firefox --project webkit
```

### Prompt library cards and recorded deviations (#249 PR E)

The shelf uses the shared concept toolbar and caption (E1) and the concept's
`article.prompt-card` (app.js `renderLibrary` a586, style.css s653–708): `.card-art`
(at most two previews), `.card-content` (`.card-title` with the title `button` and the
category `span.chip`, the positive prompt block, `.card-meta`, `.tags`) and
`.card-footer` (the rating and last-use line, then `.row-actions` with the positive
`button.send.icon-btn` and "Explore history"). Rows stay windowed (`.library-row`).
`tests/browser/workspace-library.spec.mjs` covers the markup, the clamp, the buttons and
the measured row heights on Chromium and touch.

Recorded deviations on this page:
- **D23**: real card facts. "N runs" is the parent's `run_count`. The footer reads
  "Used today", "Used yesterday", "Used N days ago" (under a week) or "Used <Mon D>"
  ("Used <Mon D>, <YYYY>" for a use in another calendar year, so last year's date never
  reads as this year's), counted in the viewer's local calendar days from `last_used_at` (a time ahead of the
  browser reads as today), and "Not used yet" without one. Stars are `★` × rating with
  a visually hidden "Rated N out of 5"; an unrated parent shows only the last-use words.
  The meta chip reads "− N negative variants" (singular for one). The title and Explore
  history are buttons that open `#/library/<id>`, as the concept's are; the trade-off
  against the old links is that they offer no open in a new tab, middle-click or URL on
  hover. The positive is clamped to two lines
  (`.prompt-card .prompt-text`); copy and send use the full text. A long title wraps
  inside the title row, and the category chip keeps whole words ("Uncategorized" for
  none). A parent with one preview, or none, fills the art row; no preview reads "No
  available image previews" (`.small`). Each card's actions add its title to their names
  ("Copy positive prompt: <title>", as the concept's do).
- **D40**: production-only shelf copy the concept never shows. A failed page reads
  "Prompt library could not be loaded. Retry." beside "Retry prompt library" (no Refresh,
  D15). A failed category list reads "Categories could not be loaded. Open the Prompt
  library again to retry." (Retry re-reads it only when the shelf failed too). The
  continuation keeps "Loading prompt library…", "No matching prompts. Try changing the
  filters." (the concept: "No matching prompts."), "You’re all caught up.", "The saved
  prompt is hidden by the current filters." and "Load more prompts".
- **D41**: the search keeps the concept's shared placeholder "Search prompts, tags,
  filenames…" for parity with Images, although Library search matches positive text and
  titles and saved negative text and variant titles, not filenames or tags (Tags live in
  "Refine this view", D14).
- **D43**: a card's tag chips come in the store's alphabetical order (`ORDER BY t.name`,
  as everywhere tags are read); the concept lists each sample's tags in authored order.
- **D44**: the shared toolbar's Filters button reads "Filters · N" while N "Refine this
  view" filters are active, and plain "Filters" otherwise; the text is its accessible
  name, so the count is spoken. The concept has no indicator, and without one a Tags
  filter set by Tags & categories Browse was invisible on the shelf. The Library counts
  Tags; Images counts Source, Folder, Media and Tags (each once). The toolbar category is
  not a Refine filter and is not counted. The caption's count is a `role="status"`
  region, written only when it changes and kept shown while the shelf reloads, so
  Load more and scrolling never re-announce it.

`tests/browser/library-concept.spec.mjs` compares the shelf (#249 E3) on the
concept-shaped fixture, which creates the concept's prompts last to first so Recently
added lists them in the concept's order: heading, toolbar, caption, grid and the six
cards (art, title row, prompt block, meta, tags, footer) at 1600x1100 and 390x844 in
both themes and motion settings, and the grid and cards at 1800x1100 (3 columns).
Behaviors, in both documents: the title and Explore history open the prompt's history
(its `h1` is the title), the footer send carries the positive only (a bridge host page
records the roles production's preview asks for), copy writes the full positive with
"Prompt copied.", and the "neon" search and the Landscapes category leave the same cards
and caption. `mapLibraryConcept` applies D14 (Oldest first appended), D23 (the
title button's `min-width: 0`; "− 1 negative variant", measured on the concept with the
singular at runtime; the spoken "Rated N out of 5"; the two-line clamp, which the
concept's samples exceed at 390px, checked against the concept with the clamp rule
applied), D35 (singular captions), D36 (sorted categories), D40 (the continuation after
the grid) and D43 (sorted chips, checked against the concept with its chips sorted).
D44 needs no mapping: the comparison runs with no Refine filter active, so the button
reads "Filters" as the concept's does. Nothing else follows the grid: the legacy
"Current prompt library" link is removed from the shelf; Overview's legacy card ("Open
existing prompt library") and the sidebar still link `/prompt_manager/admin`
(`tests/browser/workspace.spec.mjs`). `library comparison detects
mutations` injects `.card-footer { padding: 2px }` and removes `.card-meta .chip.red`.

Manual check (quick), after a browser refresh on Prompt library: each card shows the
title with its category chip on one line, two lines of the positive with copy and send,
"− N negative variants · N runs · N images" and the tags; the footer reads "★★★ · Used
today" (or the real date) with send and "Explore history"; Filters → Tags still narrows
the shelf, and the button then reads "Filters · 1" (D44), also after a Browse from Tags &
categories.

```sh
npm run --prefix tools/vendor test:workflows -- library-concept.spec.mjs \
  workspace-library.spec.mjs --project chromium --project touch --project firefox --project webkit
```

### Prompt history page and recorded deviations (#249 PR F-a)

The history page (`#/library/<id>`) takes the concept's heading, summary, negative
variants panel and comparison (app.js `renderHistory` a591–616, the dialogs a1282–1304,
style.css 1059–1179). The summary is `article.surface` with the category chip, the
positive and selected negative prompt blocks and `.card-meta` "N tracked runs · N images
· N negative variants" (tracked runs from the recorded window's `recorded_run_count`).
The panel is `section.surface.timeline-panel` (region "Saved negative variants") with
Compare negatives, + Add negative and Show archived, a `.timeline` of `.variant-lane`
rows (`--lane-offset` 38 + 47i, the "−" `.variant-dot`, "<name> · N runs" and a
`.run-dot` for a pair counted more than once) and `.timeline-lower` with the legend and
All images for this positive. Recorded executions, frozen fields, send and archived
lanes keep their contracts. `tests/browser/workspace-history.spec.mjs` and
`workspace-library-editor.spec.mjs` cover them on Chromium and touch.

Spec decisions this page applies: **D24** (Send to ComfyUI is disabled only while the
selected pair's roles load), **D25** (Compare negatives compares a baseline with the
selected red dot, and lists the selected side's added comma terms) and **D26** (All
images for this positive is a dialog with its own bounded gallery; the run dot opens
Recorded run for the pair's newest recorded execution, else a Repeat run dialog with the
pair's newest linked image, reading the archived gallery for an archived pair).

Recorded deviations on this page:
- **D45**: a variant dot is named "Select negative <title>" when no other loaded lane
  shares that title (compared trimmed), else "Select negative variant N"; a title that
  itself reads "variant N" always uses the lane's own number, so no two names repeat.
  The bounded "N of M saved negative variants" count stays a visually hidden status,
  and production's "Saved combinations, not an execution timeline." is removed.
  Archived lanes (#235) are concept lanes marked `.library-variant-archived` at the .6
  marker emphasis (their text stays at full contrast) with their Restore button. Under
  `pointer: coarse` the variant dot keeps a 47px target and the 12px run dot a 44px one
  (`::after` overlays). While a run dot shows, the lane text wraps within 139px before
  it. The panel heading, the legend row and All images wrap at 390px and 200% zoom.
- **D46**: lanes list newest first (`ORDER BY pp.id DESC`), so the comparison's
  baseline is the oldest (lowest-id) active variant, not the first lane; while the
  variant page has more, the oldest is not loaded and the section reads "Load more
  negative variants to reach the baseline." instead of guessing. The two blocks are
  named "Baseline" and "Selected" (regions and copy/send actions), so no name repeats.
  Compare negatives is disabled while the positive has no active variant. The page
  opens on the newest lane (existing behavior), while the concept opens on
  `negatives[0]`, its baseline: the first comparison reads "Baseline compared with
  <newest>" where the concept compares the baseline with itself. The history concept
  comparison (PR F-b, F5) maps this.
- **D47** (#249 PR F-b): the shared gallery is the concept's `section.history-gallery`
  (region "Shared gallery") with `.panel-heading` (h2 "Shared gallery", `span.small`
  "<name> · same positive prompt") after the variants panel (and Compare), and
  recorded executions (#209) follow it, as the concept has no recorded section. The
  name is the lane's own name ("<title>", else "Negative variant N"), with " · Archived"
  for an archived pair, and follows a rename. Its cards are the Images media cards: the
  star wherever the read returns `favorite`, no checkbox or selection bar (Images only,
  #259), the image opens the Viewer. A saved variant with no images shows the concept's
  `.empty` "Saved variant · no runs yet." beside the grid (the concept puts it inside
  `.image-grid`, which only holds positioned cards here). The concept's "Shared gallery"
  heading is the gallery's only heading and names its region: the embedded collection's
  "Linked images" heading and "N loaded · N linked images" count are removed, since they
  repeated it (maintainer ruling, 2026-10-21). Two production pieces stay, which the
  concept's gallery does not show: the layout row (Grid/List/Masonry and Thumbnail size),
  because the legacy gallery let people change layout and density and nothing else on the
  page offers it, and the selected-image side panel, the one shared inspector the Images
  page uses (spec §3.3), so an image's details stay reachable without opening the viewer.
  The same ruling covers the page's two media dialogs, All images for this positive and
  Repeat run: as in the concept (app.js a1282-1304), each is named by its own dialog
  title, with no "Linked images" heading or "N loaded · N linked images" count, and a
  stranded focus returns to the dialog's close button (maintainer ruling, 2026-10-21).
  The Images page keeps its caption. With no saved variant the page shows every image
  of the positive under "All images for this positive, including unassigned images. …"
  and no empty copy (a state the concept never reaches).
- **D48** (#249 PR F-b): production surfaces the concept never shows keep their own
  overlay rules, drawn in the concept's tokens: recorded executions and the Recorded run
  dialog (#209, D26), the Archive negative confirmation (#235) and the acknowledged
  prompt sender (spec §2.2). With `library.css` retired, these rules live in
  `workspace.css` (see "Concept shell and comparison method").

The run dot reads `GET /prompt_manager/library/{positive_id}/history/pairs/{pair_id}/newest`
(see "Chronological Library history"), and each lane's "N runs" is the pair's
`run_count` (see "Workspace data fields").

`tests/browser/history-concept.spec.mjs` (#249 PR F-b, F5) compares this page with the
immutable concept's `#prompt/alpine` on the concept fixture, at 1600×1100 and 390×844 in
both themes and motion settings, on Chromium and touch: the heading, the summary, the
variants panel, the comparison (Compare negatives pressed) and the shared gallery's
place and heading, all measured by `inventoryOf`'s `history` section with the concept's
selectors in both documents, with "Clean architecture" selected (the middle lane in
either order). The fixture links and records its images oldest first, so ids grow with
time as real runs do, and counts one pair use per image, which gives the concept's
"Quality baseline · 2 runs" and its run dot. `mapHistoryConcept` applies D24 (the four
recorded runs give the concept's own "4 tracked runs", asserted on both), D45 (the
visually hidden "3 of 3 saved negative variants" status, and its wrap rules applied to
the concept at runtime), D35 and D46 (the concept's lanes reordered newest first with
"1 run", laid out by the browser), D46's Baseline/Selected action names, #235's Show
archived button (added to the concept at runtime) and D47 (the gallery's body is the
Images collection, so its height is not compared). D25: each compared block mounts in a
`display: contents` child of the concept's `.compare-item`, so the item carries no
production class or margin. Behaviors are compared in both documents: a red dot changes
the negative block and the gallery, Compare toggles `aria-pressed`, All images opens a
dialog with the positive's four images, the run dot opens a dialog (D26: Recorded run
where the concept shows Repeat run), neither dialog has a collection caption (D47), the
heading send previews the selected pair, and
← Prompt library navigates. `history comparison detects mutations` injects
`.ca-tools .variant-dot { width: 20px }` and removes Compare negatives. The older
`history-canonical-comparison.mjs` stays for `workspace-visual.spec.mjs`'s recorded
executions (#209) checks; its production selectors are now the concept's.

```sh
npm run --prefix tools/vendor test:workflows -- history-concept.spec.mjs \
  workspace-history.spec.mjs --project chromium --project touch --project firefox --project webkit
```

### Inspect a file page and recorded deviations (#249 local Inspect, PR L2)

`#/inspect` ("Inspect a file") is a native workspace route; it no longer hands off to
the legacy metadata page, which stays reachable from Overview's "Open existing metadata
inspector" card (`/prompt_manager/gallery.html`). The page is the concept's `#metadata`
(app.js `renderMetadata` a740–748, selection a1715–1729; style.css 709–722, 1180–1194,
1543–1545, 1559–1564) in the concept's own classes (`.surface`, `.file-drop`,
`.spaced-20`, `.local-file-preview`, all from the synced `concept.css`), with no
`workspace.css` override. One local image (PNG/JPEG/WebP) or video (MP4/MOV/M4V/WebM/MKV;
AVI/WMV preview only), chosen or dropped, previews inline. The heading's one action opens
the shared viewer with the full inspector on a record read entirely in this browser
(`web/js/workspace/inspect.mjs`, `local-session.mjs`, `local-inspection.mjs` and the L1
readers `png-metadata.js`, `local-video.mjs`, `local-projection.mjs`). Nothing is
uploaded, imported or stored. PRs L1 and L2 change only HTML and JS, so they need no
ComfyUI restart; PR L3 does (below).

Local roles (#249 PR L3, decision LI-1): the prompts and resolution of a local record are
the server inspector's for the same records. `web/js/workspace/local-resolver.mjs` (with
`local-resolver-support.mjs`, `local-resolver-stages.mjs`, `local-parameters.mjs` and
`py-json.mjs`) ports `inspect_prompt_evidence` with `INSPECTION_PROFILE`: execution
evidence, graph tracing, every bound, several sampler candidates with the
identical-branch rule (#270), preview-only stages, detailer stages (multistage) and A1111
`parameters`. It reads the server's own node table, `web/js/inspection-semantics.json`,
which the page fetches once through the API client as it mounts, before any selection
(`local-semantics.mjs`; `/prompt_manager/js/inspection-semantics.json` is the one JSON
file the static route serves, by exact path). A table that cannot be fetched answers
"The inspector's node table is unavailable." as the server does. The legacy fill in
`projectInspectionPayload` then applies to a local record exactly where it applies to the
Images answer for the same bytes. Python is the oracle:
`tests/test_local_inspect_parity.py` `LocalResolverParityTests` compares the local
evidence byte for byte with `inspect_prompt_evidence` for every parity PNG and inspection
vector (prompts, resolution and facts), the golden and selection record sets
(`tests/local_resolver_cases.py`, `tests/local_resolver_selection_cases.py`), every work
bound and 1,200 seeded fuzz cases; `tests/fixtures/local-inspect/resolver-vectors.json`
holds the committed answers the Node unit test reads. The browser spec `chooser shows the
Images roles and resolution for every parity PNG` compares the rail with Images for all
25 committed parity PNGs. The static route change and the resolver fixes (Python) need a
ComfyUI restart.

Main thread (M4): the resolver runs on the page's thread, not a worker. On a desktop
(Node 26) `resolveLocalEvidence` on maximal 2 MiB graphs (a 19,990-sampler chain, 6,600
independent samplers, a consumer-dense scan, a 100,000-node PromptManager workflow) took
130–455 ms; the server took 80–905 ms on the same inputs. `localFileAdapter.read` runs in
bounded steps (read, table, parse and resolve, project, fit) with a turn of the event
loop between them, every wait ends with the selection's signal (a stalled table request
included), and the inspector carries `aria-busy="true"` with "Loading file details…"
until the answer lands. `tests/js/workspace-local-inspection.test.js` pins a 2,500 ms
budget per step on maximal independent-sampler and stage-chain graphs, checks that the
stepped read answers byte for byte as one synchronous build, and that an abort at any
step ends the read.

Bounds (the server's own budgets, ported by PR L1; see "Follow-up: local Inspect parity"
under "Chronological Library history" for the known divergences):
- PNG: at most 10,000 chunks; keyword 1–79 bytes; every chunk range checked against the
  File's size before slicing. Role records (`prompt`, `parameters`,
  `comfyassets_prompt_pairs`) share 2 MiB encoded and 2 MiB decoded (over that, the read
  fails); a workflow over 4 MiB is `limited`; duplicates are capped at 8 MiB (workflow)
  and 4 MiB (roles, decoded `parameters`). The sum of slices read from any PNG, however
  large or sparse, is at most 19,794,389 bytes.
- Video: the same record rule over the container's tags (per-key carrier first, then the
  `comment` envelope, then `description`); at most 10,000 boxes or elements; no slice
  inside `mdat` or a Cluster beyond its header.
- JPEG/WebP: at most 64 KiB, for dimensions only.
- Response: 8 MiB of actual UTF-8 JSON; omission order workflow graph, prompt graph, all
  raw. The whole File is never read with `arrayBuffer()`, `text()` or `stream()`.

Privacy, asserted by `local inspection makes no content request or storage write` and
`local roles send exactly` in `tests/browser/workspace-inspect.spec.mjs`, and by the
managed-host spec below: the only static file Inspect reads is its node table, once per
page and before any selection; no request carries the file's bytes, name or text (fetch, XHR,
beacon, WebSocket, EventSource); Inspect writes nothing to `localStorage`,
`sessionStorage` or IndexedDB; only an explicit Send crosses the host bridge, and its
payload carries neither the `blob:` URL nor the filename; no console message names the
file or quotes its prompts (asserted on the managed host). The selection's `blob:` URL belongs to one local session and is revoked once,
on replacement, route change, unmount or `pagehide`, never while an element in the page holds it: the inline preview
and, when it is open, the viewer drop their media first. The page, not the session, owns `pagehide`: it closes the
viewer, releases the inline media, then ends the selection and clears the chooser, so a page restored from the
back-forward cache shows an empty chooser beside the empty selection (`URLs and media end with their owning
session`, which also leaves with the viewer open).

Recorded deviations on this page (maintainer rulings LI-3, LI-6; plan "Mock inventory and
mapped differences"):
- **D60** (heading action, LI-3): "Open file metadata" (`ca-primary`) replaces the
  concept's "Open sample metadata". It is `aria-disabled="true"` and
  `aria-describedby="local-file-note"` (the drop note) until a classified selection is
  ready, then opens the shared viewer on that file.
- **D61** (copy, LI-3): the surface `p` reads "Read embedded metadata in this browser.
  The file is not uploaded or added to your library."; the drop note "Your file stays in
  this browser. Its metadata opens with “Open file metadata”."; the chooser label "Choose
  an image or video for a local preview"; the toast "Local preview only. No file
  uploaded; metadata is available above.". The h2 "Inspect without importing" is the
  concept's, verbatim.
- **D62** (accept and video): `accept` is the concept's
  `image/png,image/jpeg,image/webp` plus `video/*,.mp4,.mov,.m4v,.webm,.mkv,.avi,.wmv`. A
  video previews in the same `#local-preview` slot as `video.local-file-preview` with
  native `controls`, `playsInline`, `preload="metadata"` and `muted`, never autoplays
  inline. On replacement or dispose it is paused, has its `src` removed, is reset with
  `load()` (which frees its decoder) and is removed from the page, before its URL is
  revoked. Each preview owns its listeners, so the page never pins a released node.
  A video replaced while still starting (a source, but neither metadata nor an error yet)
  stays where it is, inert and `aria-hidden`, with the new preview inserted after it, and
  the session's revoke of its URL waits; once it reports metadata or an error (or after
  15 s) it is released as above and only then is its URL revoked. For that moment (the
  time to metadata, or at most 15 s for a video that never settles) the old video and the
  new preview both show, the new one below it: a known, brief overlap. Stacking them in one
  slot instead hung again, because it changes the video's own box: the old video
  absolutely positioned in its own box hung 5 of 120 pages (8 of 120 with `opacity: 0`),
  and a one-cell grid 4 of 120. Only `opacity: 0` on the video, left in the flow, did not
  hang (0 of 120), and alone it would leave an empty hole above the new preview. A route
  change within the workspace waits the same way: the shell renders the next route once a
  video still starting on Inspect settles (`leaveGuard`; the latest route wins). Route
  changes at `loadstart` + 0–7 ms hung 4–5 of 120 pages before and 0 of 120 after.
  A real page leave cannot wait. With no page code releasing anything, navigating
  away hung 8 of 120 pages and reload 22 of 120 (3 and 8 of 120 with the page's own
  release), so that WebKit teardown stays a known limitation; dispose still releases at
  once. Cause, found with native stacks of the hung WPE web process in the
  pinned image: WebKit (GStreamer) deadlocks when a video leaves the page or the viewport
  before its pipeline has started. The main thread changes the pipeline state and waits
  for the source's streaming thread, which is itself waiting on the main thread in a
  synchronous bus handler. Removing the video, hiding it, or only pushing it below the
  fold with a taller preview inserted before it all hung (7 to 21 of 120 fresh pages each,
  replaced at `loadstart` + 0–7 ms), and none of the earlier steps (`pause()`, removing
  `src`, `load()`, revoking the URL) was the cause on its own; pushing it out after
  `loadedmetadata` hung 0 of 180. Specs: `replacing a video that just started loading
  never blocks the page`, and `a video replaced while still starting is reset once it
  settles`, which now runs on WebKit too (15 fresh pages, replaced at `loadstart` + 0–4
  ms), and `leaving for another route while a video starts waits for it to settle`. The
  viewer's own video teardown was checked the same way (`closing the viewer on a video
  that just started never blocks the page`, 15 fresh pages on WebKit) and needed no
  change.
- **D63** (status line): one `p.small.spaced-16` in `#local-preview`, for states the
  concept never shows: "Drop one file at a time.", "Folders cannot be inspected. Choose
  one image or video file.", "This file type cannot be previewed or inspected here.
  Choose an image or video file.", "The preview could not be displayed. Metadata and the
  original remain available in the viewer." and "Embedded metadata could not be read."
  (the shared inspector's own copy) are `role="alert"`; "Metadata in AVI and WMV files
  is not read here. The preview and the original remain available." is `role="status"`.
  A rejected chooser or drop ("Drop one file at a time.", folders, an unsupported drop)
  keeps the selection and its status line: its alert is a second line after it, removed
  by the next selection attempt or after the concept's toast interval (6.5 s). The toast
  "Local preview only…; metadata is available above." shows only for a selection whose
  status is empty or the AVI/WMV note, never beside an unreadable or failed preview.
- **D64** (drop, LI-6): `dragover`/`drop` on `.file-drop` accept exactly one File, read
  only `dataTransfer.files`/`items` (never its strings) and change nothing visually.
  While the page is mounted, `window` `dragover`/`drop` call `preventDefault`, so a missed
  drop cannot navigate away. A drop is ignored while a dialog is open.
- **D65** (no revoke on load): unlike the concept, the preview's `blob:` URL is not
  revoked on `load`: the viewer and Download original use it until the session ends.
- **D66** (local rail, in `file-inspector.mjs` for `identity.kind === 'local-file'`): the
  viewer and rail title is the filename; "File & provenance" opens with "Selected local
  file · read in this browser", lists Source, Size, Dimensions, Format, Media type and
  Modified with no Path row and no Show in folder, and ends with "Local file; no saved
  library association was checked." and Download original. There is no Prompt history
  action and no category or tag chips.
- **D117** (Copy settings summary, LI-4): the legacy metadata
  page's COPY ALL, consolidated into the shared full inspector (the viewer's
  `#viewer-details`, so Images, the recorded history's current file and Inspect all have
  it). The concept has no such action. One plain button "Copy settings summary" follows
  the six facts (before "File & provenance"); the Images side panel keeps the concept's
  footer and has none. Its text is the legacy `copyAllData` template, line for line:
  `Checkpoint`, `Positive Prompt`, `Negative Prompt`, `Steps`, `CFG Scale`, `Sampler`,
  `Seed`, with the legacy placeholders ("Unknown", "No prompt found", "No negative prompt
  found"). The values are the ones the inspector shows: a confirmed prompt and a known
  fact verbatim, anything else (an explicitly empty prompt included, as the legacy parse
  treats '') the placeholder. Against the legacy page, for the same file, only these
  differ: numbers keep the server's spelling ("8.0" where the legacy page wrote 8); a fact
  the legacy parse could not read can be known; and a negative the #249 ZeroOut guard
  zeroed reads "No negative prompt found" where the legacy parse read the positive's text
  through the zero-out. `workspace-file-inspector.test.js` runs the template taken from
  `web/metadata.html` itself on the legacy parse of every parity PNG: 22 of 25 are
  byte-equal, f01b and f23 differ by the ZeroOut guard and f17 by facts the legacy parse
  missed. Copying works as the concept's prompt copy: the toast "Settings summary
  copied.", or, without a clipboard or when it is refused, the select-to-copy dialog
  titled "Copy settings summary". `.ca-tools #viewer-details > div > button` wraps
  (`white-space: normal`) in the narrow rail at 200% zoom, as D20's buttons do. The concept
  comparisons add the button to the concept's viewer at runtime (a copy of its own plain
  button) and to the Inspect rail's section order.

The chooser keeps naming the chosen file, as the concept's does: it is cleared as the
picker opens (so choosing the same file again is a new selection), not after `change`,
and named again with the current File (through a `DataTransfer`, which fires no
`change`) when the picker is cancelled. An accepted drop names the dropped file, so the
chooser never names a replaced file and never reads "No file chosen" beside a selection.

Legacy floor (`web/metadata.html`), dispositions:
- PNG by chooser and by drop: kept (`#local-file`, D64).
- Positive and negative with Copy: kept, in the shared inspector's prompt blocks.
- Full text (the old "Show more"): the prompt blocks show the whole text.
- Raw workflow view and Download workflow JSON: kept, in "Workflow & raw metadata".
- Copy all summary and filename copy (LI-4): the filename is selectable text in the rail
  and Download original saves the file under its name. COPY ALL is the shared full
  inspector's "Copy settings summary" (D117), with the legacy text.
- Fully browser-local reading: kept and asserted (privacy above).
- Case-variant keys `Workflow|comfy|ComfyUI|Prompt|Parameters` (LI-5): a separate
  backend issue; Inspect follows the server's readers, so the two never disagree.

`tests/browser/inspect-concept.spec.mjs` compares the page with the immutable concept's
`#metadata` on the concept fixture, at 1600×1100 and 390×844 in both themes, on Chromium
and touch, in three states: initial, selected (`f02.png` from the parity corpus set into
both `#local-file` inputs with a real `setInputFiles`) and viewer-open (the heading
action). `withProductionCopy` applies D60–D62 to the open concept page before it is
measured (it throws if the concept's own copy changed), so the browser lays the concept
out with production's words; `mapViewerConcept` applies D66 (title, no tag chips, no
Prompt history). The diff of the heading, surface, drop area (icon, label, input, note),
preview and viewer (dialog box, layout below its header, toolbar control order, rail
section order) must then be empty. Pinned values are asserted on production: surface
padding 22px and radius 7px; drop area dashed 1px border, radius 7px, min-height 170px,
gap 12px, padding 22px, margin-top 20px; h2 17px/600; p 12px; input 12px and
max-width 100%; preview max-height 400px, `object-fit: contain`, margin-top 20px. The
chooser's displayed name is asserted to be `f02.png` in both documents. `mutation is
noticed` makes the drop border solid (noticed on all four sides and nowhere else),
renames the label and removes the note. Paired captures
(`{concept,production}-{initial,selected,viewer}-{dark,light}-{1600,390}.png`) and
`inspect-geometry.json` are attached as test artifacts; there are no PNG baselines.

`tests/comfyui/inspect-sender.spec.mjs` (`local file roles send exactly across renderer
transitions`) runs on the managed CPU host: the Inspect page reads a PNG whose positive
spans lines and whose negative is a ConditioningZeroOut (explicitly empty) and
`core-tags.webm`, and sends each pair to a Prompt Manager (+/-) node in Classic and
Nodes 2.0, switching both ways with a graph reload each time. Each send previews the
node's current and new roles, writes exactly those roles, and neither queues nor loads a
workflow. A connected `negative_text` input rejects the whole preview with no change. The
workspace page makes no non-GET request and logs neither filename nor prompt text.
Follow-up (pair node, separate investigation): the node's `text` widget ignored a value
set after a `PrimitiveString` linked `negative_text` (it kept its prior value), so this spec
and `library-sender.spec.mjs` set the positive before connecting the negative input.

```sh
node --test tests/js/workspace-local-session.test.js tests/js/workspace-state.test.js
npm run --prefix tools/vendor test:workflows -- inspect-concept.spec.mjs workspace-inspect.spec.mjs \
  --project chromium --project touch --project firefox --project webkit
npm run --prefix tools/vendor test:comfyui -- inspect-sender.spec.mjs
```

### Needs review page and recorded deviations (#249 PR F)

`#/review` is the concept's Metadata review page (app.js `renderReview` a749–760) over
the review routes (`web/js/workspace/review.mjs`, `review-state.mjs`): breadcrumb and
document title "Review" (the concept's), heading "Metadata review" with its line and
**Open activity** (`#/activity`), the `.notice` "N images need a decision" with the
concept's sentence, then `.gallery-layout`: the queue's concept media cards
(`image-card.mjs`, shared with Images; the image opens the shared viewer, Info selects
the item, Tag opens the shared Tag image dialog with the review scope note) and
`section.surface` "Resolve the association" (filename, Tag image, Sampler context,
the chosen path's prompt blocks, Leave for later and primary Apply association). The
first item is selected on load, as the concept's first image is. Sampler context lists
"Sampler <id> · <positive> / <negative>" per candidate (the server's label), disables
unusable ones, preselects the first workable one and ends with "Leave unresolved"
(rulings R2/R3); "Leave unresolved" shows the ambiguous status in both blocks. Apply
takes a fresh capture (`observe`) and posts the decision; Leave unresolved on a file
under no current root posts without a capture. Both actions return to Images (R7); a
recorded decision re-reads the row's count, as does every metadata scan that ends. A
refused decision keeps the selection and shows the server's sentence (the
association conflict's explanation included) as `p.warning-note` in the panel; a
restored storage or a changed queue reads the queue again; a request with no answer
keeps its capture and body, and the next Apply of the same choice resends it once.
The SAVED VIEWS row "Needs review" (review icon) follows Favorites with the exact
pending count (`GET /prompt_manager/review/items?limit=1`, its `total`).

- **D86** (plan D63, ruling R6): an empty queue shows only the notice, "No images need a
  decision" and "Ambiguous images are added when a metadata scan or a new generation
  finds more than one sampler path."
- **D87** (plan D64): an ended metadata scan whose `scope.review_items` is above 0
  carries the concept's **Review metadata** (app.js a738 `go-review`): in its Activity
  item after the item's line, and in its job view's heading after Download report. The
  item's chip still names its state (D54), where the concept's says "Needs attention".
- **D119**: the queue cards have no selection checkbox and
  no star (the page has no selection bar and the queue carries no favorite state); a
  file under no current root has an empty, `aria-disabled` image button and Tag; more
  than 40 items add the Images page's `.infinite-continuation` ("40 of 45 images",
  Load more images) after the layout; loading and a failed read are the page's status
  line (with Retry); a limited or unavailable file and the decision states are text in
  the panel's `p.warning-note`; the toasts "Association accepted." and "Left
  unresolved." replace the concept's prototype toast. The concept's "Sample review
  queue" chip and "sample" wording are prototype copy (D29). When a re-read (below)
  finds the open item gone, the next one opens with "<file> was decided elsewhere, so
  the next image is open."; a failed re-read keeps the queue and says "The review queue
  could not be read again. Reload the page to see the latest images." A queue card's
  title is its file name and its line the extension only (an undecided item names no
  prompt, and D11 omits the size and seed a queue item does not carry); its image's alt
  is the file name; the panel's Copy and Send buttons carry no prompt title in their
  names; the Tag image dialog opened from the queue heads with the file name. An
  unavailable file's empty media button keeps the concept's 1.33 media area, so its
  card lines up with the others. A long file name in the panel's `p.small` wraps inside
  the panel (`overflow-wrap: anywhere`), where the concept's short sample name never
  overflows. A queue card shows the Images listing's thumbnail (`thumbnail_url`, the
  same URL the Images page uses; a preview not made yet is made on first view); a re-read
  rebuilds a card whose media, target or availability changed (a root came or went), so
  Open and Tag follow it. When an unanswered decision's item leaves the queue in a
  re-read, the decision was recorded: the page finishes as a recorded decision does (its
  toast, back to Images), not with "decided elsewhere".
- **D120** (the concept has no live-update state): while the
  change stream is down, the heading carries the quiet status line "Live updates are
  paused: changes made elsewhere appear once the connection returns." (`p.small
  .review-live`, `role="status"`); the stream's next `ready` hides it.

**Live queue (F2).** An open Needs review page holds one server-sent event stream,
`GET /prompt_manager/review/events` (`py/api/review_events.py`): `event: ready` when it
opens, then `event: change` after each committed queue change (an item the scan or the
monitor recorded, a spelling refresh, a decision from any tab; `database/review_changes.py`
notifies after the commit, from whichever thread wrote it) and after a restore. `change`
carries no data. `ready` carries the queue's version `{library_epoch, review_revision}`,
read after the stream subscribed, and every list read answers the same pair
(`review_revision` is the review index revision, which every queue change moves; a
restore moves the epoch). **One read per visit (F3):** the page re-reads on `ready` only
when the queue it shows is older than that version (an unknown version, `{}` when it
cannot be read, re-reads); a `ready` heard while the first read runs is compared once that
read answers. The test server counts a stream as open only once it has sent `ready`, so a
browser test that changes the queue after that count is a `change`, never part of the
version. The page re-reads on every `change`, keeping the open item, its choice and an
unanswered decision; re-reads asked for while one runs collapse into one more. A change
heard while a decision is in flight is owed: a refused or unanswered decision reads it
once the panel is idle again (a recorded one leaves the page). Load more asked for during
a re-read waits for it and continues after the queue it read; a re-read started during
Load more takes that click over and reads one page deeper, and a failed re-read leaves
Load more clickable. Apply fences on the epoch of the detail it decides: an observe under
another storage releases its capture and reads the queue and the item again, and any
re-read under another epoch reads the open item again. **Every re-read keeps the loaded depth (F3):** it
reads page after page (40 each, the route's limit) until it holds as many items as Load
more had shown, so the cards stay and the scroll position does not move; a restore met
after the first page reads once more from the first page. The row's count follows the
total the page shows (no count read per change). Nothing polls: a `: keepalive` comment
every 25 s only lets the server drop a closed tab's subscription. The server sends the
first change at once and collapses the notices of the next 0.4 s into one trailing
`change` (`COALESCE_SECONDS`), so a busy scan costs an open page one re-read per 0.4 s at
most. Shutdown (`on_shutdown`) ends every stream and answers a later one 503. A
reconnect the browser gives up on (a 503, or a proxy's error while ComfyUI restarts) is
opened again by the page after 3 s, doubling to 30 s, with D120's line shown while
the stream is down; its `ready` re-syncs the queue. The stream lives only while the tab is
visible: a hidden tab closes it and a visible one opens it again, and that `ready` reads
only when the queue changed meanwhile. A duplicate recording and a refused decision change
nothing and send nothing. Residual: each visible Needs review page holds one HTTP/1.1
connection (browsers allow six per origin, shared with ComfyUI's editor), so several
side-by-side visible windows on the page, during a metadata scan's job stream, can still
queue the origin's other requests; a re-read of a deep queue costs one request per 40
items shown.

Pinned by `tests/test_api_review_events.py`, `tests/js/workspace-review-state.test.js`
("refresh …", "a refresh keeps the loaded depth …", "ready naming …") and
`tests/browser/workspace-review.spec.mjs` ("an open page shows …", "a visit reads its
queue once …", "a live re-read keeps the loaded depth and the scroll position", "a stream
the browser gives up on …", "a hidden tab closes its stream …", "a re-read refreshes a
card …"); the controller's owed, Load more and epoch cases are "I1: …", "I2: …", "M3:
…", "M5: …" and "M7: …" in `workspace-review-state.test.js`, and the coalescing is "a
burst of changes …" in `test_api_review_events.py`.
Leaving the page mid-decision releases that decision's capture (the server may never see
the request), and leaving during the observe releases the capture it then answers
("leaving during the observe …").
**Concept comparison (F3).** `tests/browser/review-concept.spec.mjs` measures the whole
page (`reviewInventory` in `tests/browser/tagging-canonical-comparison.mjs`: every shown
box below `main` with its place, fonts, edges and layout, a leaf's text, a button's name,
a select's options) in the concept at `#review` and in production on the fixture's
concept-shaped queue (`POST /__test__/review/reset {items: 3, concept: true}`: the
concept's first three image names and sizes, and the concept's first prompt on the open
item), and `mapConceptReview` applies only: D29 (no "Sample review queue" chip, no
"sample" in the panel heading and Apply), D119 (above), D3 (the grid's
`position: relative`), production's hooks and accessible names (`review-queue`,
`review-panel`, the list roles, the region names) and sample data (the candidate options
except Leave unresolved; widths and heights that follow from the changed copy, moved by
exactly that much). It then matches at 1600×1100 and 390×844 in both themes on all four
projects; the gallery layout follows the concept at 1600, 1200, 900 and 390px (310px,
275px with a 15px gap, 280px, one column); the SAVED VIEWS rows match with Needs review
after Favorites; the queue's Tag image dialog matches the concept's needs-review dialog
(`needs-review-tagging.png`) under the C4 mapping; and the comparison is shown to notice
a dropped notice border and reordered actions. The paired captures (populated queue in
both themes and widths, conflict, limited item, unavailable file, empty queue) are test
artifacts; no screenshot baseline is added. The comparison found one gap, the collapsed
media area of an unavailable file's card, fixed above (`workspace-review.spec.mjs` "a
limited item and an unavailable file …" pins it).

- **D3** and **D20** apply to the queue grid (its cards stay in the grid's flow; it is
  not virtualized) and to the panel's prompt blocks (their mount point is transparent).

Tests: `tests/browser/review-concept.spec.mjs` (concept comparison),
`tests/js/workspace-review-state.test.js` (controller),
`tests/js/workspace-state.test.js` (route and count),
`tests/browser/workspace-review.spec.mjs` (queue, count, apply, leave, Tag, empty
queue, Load more, Activity's Review metadata; items recorded by the real recorder in
`tests/browser/workspace_review_fixture.py`), and the SAVED VIEWS rows in
`shell-concept.spec.mjs` and `workspace-saved-views.spec.mjs`.

### Native image tagging and Needs review (#249 PRs A–F)

One place for the whole image-tagging slice; the details live in the sections named.

- **Routes.** Tagging: `POST /prompt_manager/image-annotations/observe`, `GET`/`DELETE
  /prompt_manager/image-annotations/capture/<token>`, `POST
  /prompt_manager/image-annotations/suggest`, `PUT /prompt_manager/image-annotations/tags`
  ("Tag image dialog and recorded deviations"). Needs review: `GET
  /prompt_manager/review/items?limit=1..40&cursor=` (answers `library_epoch`,
  `review_revision`, `total`), `GET /prompt_manager/review/items/<id>`, `POST
  /prompt_manager/review/items/<id>/decision` and the change stream `GET
  /prompt_manager/review/events` ("Needs review decisions and queue reads", "Needs review
  page and recorded deviations"). Every legacy route keeps its payload; the only legacy
  change is `409 model-busy` from the legacy Auto-tag routes while another model
  operation holds the shared gate.
- **S1 (fingerprint-current reads, ruled 2026-10-22).** The Images Tags filter, search and
  the inspector/viewer chips use a byte record only while the file's `(st_size,
  st_mtime_ns)` equals its verified fingerprint; writes hash the bytes. Residual risk
  under "Image annotations schema".
- **Capture limits.** 256 MiB per file, 8 completed captures, 512 MiB reserved, 2
  capture/verify workers, 900 s idle, 1800 s hard; the browser fixtures release every
  capture on reset, and leaving the Needs review page mid-decision releases its capture.
- **The record-changing job.** Bulk Add tags and Auto-tag selected run as the
  `image-tags` Activity job (PR D); like the metadata scan it is a record-changing job, so
  a start while another runs is refused (`busy`). Needs review items come from that scan
  and from the live monitor, and a decision changes links only when compatible
  (`association-conflict` otherwise, with zero writes).
- **No downloads.** No automated test downloads or loads a model: the browser server
  injects `TestModel` (its `download_model` raises), native routes and jobs never
  download, and every tagging spec asserts no `/autotag/download/` request.
- **Restart.** PRs A, B, D and E and PR F (the review change stream, the queue version)
  change Python: the maintainer restarts ComfyUI once after merging. HTML, CSS and JS
  changes need only a browser refresh.

### Native System (#249 System)

`#/system/<section>` is a native workspace route rendered by `web/js/workspace/system.mjs`
on the concept's `renderSystem` markup (app.js a698–706; `concept.css` 709–895). `#/system`
is Overview under the sidebar's own hash; `#/system/overview|appearance|sources|
integrations|tagging|maintenance|backups|diagnostics` select a section, and an unknown
child (the concept's `#/system/general`) is replaced by `#/system/overview`. The
production hub `#/overview` is unchanged (D28); its System card opens
`#/system/overview` (Q12). Switching sections keeps the shell, the drafts and focus on
the pressed section button and disposes the previous section's scope, so its reads,
listeners and dialogs never outlive it.

System is the home for settings and maintenance (#330 System S13). The legacy pages keep
their prompt list, tags, Scan Images, auto-tag batch and review and gallery browsing; their
Settings, Diagnostics, View Logs, Maintenance, Backup DB, Restore DB, the gallery's
thumbnail, cache, rescan and duplicate tools and the auto-tag model downloads open the
System section that owns them in a new tab (`legacy-system-links.spec.mjs`). Each page
keeps only its own settings in Page options. What left the legacy pages is listed in
"Legacy System UI removed by S13".

Drafts (`system-state.mjs`) live for the page session (Q10). Save writes the dirty
browser fields, then the host fields (`POST /prompt_manager/settings` with
`expected_revision`, #220), then the runtime fields. A failed domain keeps its drafts and
highlights their controls; `settings-changed` is a conflict and keeps the draft. System
reads the host values on mount and again after a conflict; until a read returned a
revision, a host save fails without a request, so it never sends a blind
`expected_revision: null` (which S1 merges into the file on disk). Save stays disabled
until a section has an unsaved change.

Appearance (`system-appearance.mjs`, S3) is the concept's "Browsing & display" surface
on real values. Its browser rows are this browser's display defaults in the legacy
gallery's own `gallerySettings` object (`web/js/workspace/preferences.mjs`, Q3): Default
layout and Thumbnail size (`workspaceLayout` grid/list/masonry, `workspaceDensity`
comfortable/large/compact, new keys), Load metadata on selection (`autoLoadMetadata`),
Cache metadata in this browser (`cacheMetadata`), Image loading quality (`imageQuality`:
Optimized→`medium`, Original→`high`, Fast→`low`; the thumbnail dialog's preselected quality
once S5 lands), the three video rows (`videoAutoplay`, `videoMute`, `videoLoop`) and Show
file paths (`showFilePaths`). A write re-reads the stored object and writes back only the
changed keys, every other key (legacy paging, thumbnail state, unknown keys) as stored;
the legacy gallery's Save also keeps the keys it does not edit, so the two pages stay in
sync. The workspace never reads the legacy paging keys (`defaultLimit`, `gridColumns`,
`infiniteScroll`; README continuous-browsing ruling). A browser that refuses storage
(denied, or a full quota that leaves the stored bytes as they were) keeps the change for
this tab only and the status says "Saved for this tab only; browser storage is
unavailable.", never "Saved in this browser.". Another tab's change (a `storage` event)
repaints the clean controls; a draft it overtook stays, highlighted, with the conflict
status. Open from ComfyUI (New tab→`newtab`, Popup window→`popup`) and Node search
auto-hide (seconds) (a whole number 0–300; anything else is highlighted and never sent)
are the server's `webui_display_mode` and `result_timeout`, saved through
`POST /prompt_manager/settings` with the revision. The consumers read the defaults on
their next visit, never the current view: a new Images visit starts from the layout and
size (the last visit keeps its own, a saved view opens at Grid and Comfortable as
before); with metadata off a selection reads nothing until "Load metadata"; with the
cache off selecting the shown file again re-reads it (on, the one current record is
reused); paths off leaves out only the Path row of File & provenance; the viewer reads
the video defaults.

Sources & scans (`system-sources.mjs`, S4) is the concept's "sources" body on the
server's sources. `GET /prompt_manager/settings` reports them (public paths only):
`sources.configured` (each `gallery_root_paths` entry with `valid`, `code` and the
server's `reason`), `sources.effective` (what is scanned and served, with its
`root_index`, `watch` (`watching` only when the image monitor's observer is alive,
scheduled that folder and monitoring is on, `stopping` when it still watches it but
monitoring was switched off, `manual` when monitoring is off or no monitor runs, else
`restart_pending`) and `in_comfyui` (its public path is relative to the ComfyUI base)),
`sources.auto_detected`, `monitor` and `settings.monitoring_enabled`.
`POST /prompt_manager/settings/validate-source {path}` checks one folder and writes
nothing. Every caller (validate-source, the settings save, config.json at load) refuses
a source through the same checks (`sources.source_problem`): its form first, then
containment decided as text (`normcase(normpath)` against the allowed folders' own
spellings), before anything resolves, stats or opens the path, so a UNC share, the
`\\?\` and `\\.\` namespaces, a device or any folder outside the allowed ones never
reaches the filesystem or the network (no SMB request, no existence or timing oracle).
Only then is the path resolved and contained again, which catches a symlink out. The
filesystem root and the home directory answer `outside-allowed` with the same reason as
any outside path. ComfyUI folders outside its base directory (`--output-directory`)
are path anchors too, so their sources keep a relative public path
(`renders/portraits`) that round-trips and never collides by folder name. A save whose
list holds a source the server cannot use is a 400 `invalid-source` naming its public
path (`source`, as text: a refused path is never resolved to name it), its `index` and
`reason`, and saves nothing. A save that changes the sources (compared as folders, not
spellings) holds the registry as `records` ("Source change"), so it is refused with
409 `busy` while a scan runs. `POST /prompt_manager/jobs/scan {scope, roots, paths}`
scans only the given effective `root_index`es (strict ints, at most 64), each with the
public path the page read for it; an index past the list or one that now names another
folder is 409 `sources-changed` and starts nothing (the page toasts the server's
reason and reads the sources again); the legacy `/scan` is unchanged. The section
lists one row per source ("<path> · watching | watching until restart | manual scan |
restart pending" with "Scan changes" for a watched source, else "Scan folder"), which
opens the shared "Review metadata scan" for that one source ("1 output folder") and
starts the session job over it; the job opens in Activity, and the toast only says it
started. Enrich
existing metadata and the notice's Review scan review every source. The notice shows
until this browser dismissed the 3.2.0 update notice (`pm_update_notice_dismissed`,
the legacy admin page's key); Later dismisses it without scanning, and Review scan
dismisses it as the legacy Scan does. "+ Add source" asks validate-source, then adds
the folder to the host draft of `gallery_root_paths`; Remove takes one out; "Watch for
new outputs" (`monitoring_enabled`) and Worker threads (`worker_threads`) are host
drafts too, and Save writes them with the revision. A save the server refuses because a
scan runs toasts "Another job is running: <title>." with a link to that job (D57) and
keeps the drafts; a save refused for one source says the server's message as the status
and marks that row. After a save the source list is read again; a monitoring change
waits for a restart (the running observer keeps watching until then, and the status
says "Restart ComfyUI to apply: Watch for new outputs."). Thumbnail generation's
Review options opens the concept's "Thumbnail maintenance" dialog (D99, below).
A list that cannot be read shows "Sources could not be loaded. Your saved data has not
changed." and Retry, never an empty list.

Thumbnail generation is an Activity job (S5, `py/api/thumbnail_jobs.py`). `GET
/prompt_manager/images/thumbnails/plan?mode=missing|rebuild|convert`
counts the work (`targets`, `fresh`, `outdated`, `remaining` past the 10,000-file batch cap,
the source labels, and the server's `format`, `quality` and `size` it writes with) and
writes nothing; an unknown mode is a 400 `invalid-request` that starts nothing. `size`
and `quality` an older client sends are ignored (#317). `POST /prompt_manager/jobs/thumbnails {mode}` starts the
`thumbnails` job ("Thumbnail generation"): admission `guarded` (never beside a scan or
another record-changing job, one at a time, a legacy clear refused while it runs),
cancellable at a file boundary (a file not yet started is left alone; written previews
stay), outcome groups Generated / Skipped (already fresh, listed without a log line
each) / Errors. Format, quality and the longest edge are the server's thumbnail
settings (#317, below), read once when the job starts and shown in its scope. Video
previews take the same settings: the OpenCV frame, ffmpeg's frame (no OpenCV; a PNG on
stdout fitted inside the edge keeping its aspect ratio and never upscaled, `-vf
scale='min(iw,E)':'min(ih,E)':force_original_aspect_ratio=decrease`) and the
placeholder are encoded like an image's preview.
MAX_THUMBNAIL_FILES (10,000) bounds one batch, not the job: after each batch the
missing, rebuild and convert jobs plan again, leaving out every file a batch already
worked on, and go on until nothing is left, the job is cancelled, or a batch made no
progress (every file failed: the rest waits for the next run). Progress counts against
the overall total and the final line reports every batch (`TestBatchesContinue`,
`test_convert_goes_on_past_the_per_batch_cap`). The plan dialog counts all of it.
`rebuild` writes fresh previews again, each through the same temp-file
rename, so nothing is cleared first and a failed file keeps its old preview (a video
whose frame cannot be decoded keeps its preview instead of getting the placeholder);
originals are only read (`tests/test_thumbnail_jobs.py`). Cancel is also checked per
file during the planning walk: a job cancelled there stops the walk at the next file,
publishes no `start` frame and ends "Cancelled before any preview was written". Every
exit sets a final status line: "N previews written · M errors" (plus "· R more on the
next run" when a batch made no progress), "No previews to generate · N already up to date" (or "No
images or videos to preview"), "Cancelled · N previews written", or the failure; a job
with nothing to do never reads "Processing file 0 of 0" (`test_every_exit_sets_a_truthful_final_status_line`,
and "a finished thumbnail job with nothing to do says so" in `workspace-activity.spec.mjs`).
Windows: replacing a preview another process holds open (the gallery serving it, an
image viewer) fails with WinError 5 or 32; that file counts as an error reading
"Preview in use by another program; the old one was kept" (never the raw "[WinError
5]"), and its old preview stays. Any other PermissionError reads "Permission denied
writing the preview" (`test_a_locked_preview_is_a_readable_coded_error`).
Thumbnail work (the job and the gallery's generate and clear holds) is `guarded`, but it
writes only preview files and no records, so the image monitor's Needs review
recording runs beside it instead of being deferred for a whole rebuild
(`PREVIEW_ONLY_KINDS` in `py/api/review_recording.py`, `blocker(..., compatible=)`;
`test_a_running_thumbnail_job_does_not_defer_monitor_recording`). Other `guarded` and
all `records` jobs still defer it.

Deliberate change: the legacy gallery stream `POST /images/generate-thumbnails/progress`
now starts this job (missing previews with the server's thumbnail settings; its
`quality` preset no longer sizes anything, #317 R3) or attaches to the running one (never
to a running Convert outdated, which it refuses with its error frame), and streams the job's unchanged `status`/`start`/
`progress`/`file_error`/`complete` frames after a first `job {id, started}` frame that
names the job and whether this stream started it. The stream reads the registry after
its headers are sent, so a job that starts in that gap is attached to, not refused. A
client that goes away no longer stops the work (`test_thumbnail_job_survives_client_disconnect`:
one worker, 9 files in three batches; the stream sees its client go after the first
batch and all 9 previews are written, where a stream that stops on disconnect leaves 1).
The gallery's Cancel (its settings panel and the startup dialog) cancels by that id,
and only a job its own stream started: a Cancel before the `job` frame keeps the stream
open until it arrives, a double Cancel posts once, and a job another tab or System
started keeps running while the gallery stops following it (`tests/js/gallery-thumbnails.test.js`).
A job cancelled from Activity ends the stream with the error frame "Thumbnail
generation was cancelled". The blocking
`POST /images/generate-thumbnails` and the clear stay legacy holds.

The workspace starts it from "Thumbnail maintenance" (`thumbnail-review.mjs`, the
concept's `thumbnail-review`), opened by Sources' Thumbnail generation → Review options,
Overview's Review thumbnails and Convert outdated, and Appearance's Convert outdated.
Scope is Missing thumbnails only (`missing`, the default), Rebuild all (explicit opt-in)
(`rebuild`) or Convert outdated (removes older formats) (`convert`, #317). D131: the
concept's Quality and Preview size limit fields are replaced by a line naming the
server's settings ("Previews are written as WebP · quality 80 · 512 px. Change this in
System › Appearance."). Start closes the dialog and starts the job
through the Activity store, which toasts "<job title> started."; the job opens
in Activity. A start refused because another job holds the slot toasts the D57 busy
link toast and stays. The legacy gallery's thumbnail tools keep working on the same
server settings.

Automatic previews (#249, `py/api/thumbnail_auto.py`). The approved mock has no
repeated global thumbnail prompt (`docs/concepts/workspace/README.md`), so the
workspace never asks; previews are a cache the server keeps:

- Every listing gives an image or video a thumbnail URL (`media_thumbnail_url`: Images,
  saved views and favorites, Library cards and galleries, Needs review, duplicate
  groups). An existing preview's URL keeps its `v=<mtime>`; a missing one is listed
  without `v=`.
- `GET /images/serve/thumbnails/<rel>_thumb<ext>` for a missing preview is refused as
  text first (traversal, a backslash, a drive or stream colon, empty or dot segments, so
  `D:/x`, `\\server\share` and a leading `/` never reach a joined path), then admitted
  on the event loop (`_PreviewGate`): 3 previews are written at once on the API's own
  `pm-preview` pool (never the default executor), one flight runs per file (the history
  route shares the flight key and the admission), and at most 64 requests wait; one
  more gets its fallback at once without listing a folder. Inside the admission the
  preview maps back to its media file under the same root (exact names, then one folder
  listing for a mixed-case extension; a regular file inside the root, never through a
  link) and is made with the server's thumbnail settings (#317). When it cannot be made an
  image's original is served and a video's placeholder frame (a video is never sent to
  an `<img>`), both `Cache-Control: no-store`; cards and the inspector cover also swap a
  video back to its own element when its preview image fails. Only a file that cannot
  be decoded is remembered for the session (path, size, mtime; retried once it changes)
  and counted as `failed` in `/images/thumbnails/status`, which Overview's row adds
  ("N missing · M outdated · K failed"); I/O errors and a preview another program holds
  open (Windows) are not. A preview folder (or any link segment of it) resolving out of
  the root is never written. Media inside a nested `thumbnails` folder is listed with no
  thumbnail and keeps its original. The listing's `thumbnail_ready` says whether the
  preview file exists (the legacy gallery's dot and "Fast").
- The video decoder falls through OpenCV, ffmpeg (a tracked process, killed on
  shutdown) and the placeholder frame: an OpenCV that cannot open or read a clip (AV1)
  no longer ends the run.
- The image monitor hands every settled output to `queue_capture_thumbnail`, which
  queues it for one background worker: each wake takes at most Worker threads files and
  writes them on that many threads; a queued file is not queued twice; the queue holds
  10,000 and a file left out is made on view.
- A finished scan that added files counts the missing previews of the sources it
  walked: 200 or more start the thumbnail job (missing only, visible and cancellable in
  Activity, never silent bulk work); fewer go through the worker.
- Holds: automatic previews write only `thumbnails/` files and no records, so they run
  beside a scan, image tagging, the thumbnail job and the Needs review recording (S5
  M7). They wait (a view gets the original, a queued file is left for its view) while a
  restore, a duplicate removal, a sources change, the gallery's thumbnail clear or an
  `exclusive` job runs (`_auto_thumbnail_blocker`), checked when a write starts (after
  its slot) and again just before its rename.
- Shutdown is bounded and logged: the worker stops at a file boundary, ffmpeg is
  killed, an idle worker leaves (the next output starts a fresh one), the view pool
  shuts down, and after the app's cleanup a late monitor event
  starts nothing. A package reload stops the workers the previous import left.
- Cards and strips load previews only: Images in every layout and size, Library cards,
  Needs review cards and the inspector's cover (a video's cover is its preview frame).
  The media viewer's main media and downloads load the original
  (`workspace-thumbnails.spec.mjs` records every media request; `tests/test_thumbnail_auto.py`).

Thumbnail format and settings (#317, `py/api/thumbnail_settings.py`,
`py/api/thumbnail_encode.py`, `py/api/thumbnail_convert.py`):

- Server settings `thumbnail_format` (`webp` default, `jxl`, `jpeg`),
  `thumbnail_quality` (1–100, default 80) and `thumbnail_edge` (128–2048 px, default
  512) live in config.json's `thumbnails` section and are saved through `POST
  /prompt_manager/settings` with its revision; a bad value is a 400 and changes
  nothing. The legacy `gallery.web_interface.thumbnail_size` (never read; older saves
  wrote its default 256) is ignored. `GET /prompt_manager/settings` also reports
  `thumbnail_encoders` (`jxl: {available, encoder, reason}`). One module reads the
  settings; every writer uses it: automatic previews (capture and first view), the
  thumbnail job, the history route, and the legacy gallery's stream and blocking
  generate (`tests/test_thumbnail_format.py`).
- Names: `thumbnails/<stem>_thumb.webp|.jxl|.jpg` for images and videos alike. Every
  lookup (listings, duplicates, enrichment, the autotag scan, the history route) takes
  the first existing preview the file owns, the configured format first: an older
  release's `_thumb.png` (or `.jpeg`, `.gif`, the source's own spelling or the
  upper-case one) keeps showing, and counts as outdated, until it is converted. History's
  cover uses the same lookup as the Images listing. Two files with one stem in a folder
  share one preview name, made from the first source in `.png`, `.jpg`, `.jpeg`,
  `.webp`, `.gif`, then videos. A request for an older name answers the current preview. Previews
  are served with an explicit `image/webp` / `image/jxl` type (the platform's mimetypes
  table may not know them) and the same cache headers; `.jxl` is served from
  `thumbnails/` only.
- Every preview is written by `write_thumbnail`: the first frame (animated sources give
  a still), RGB, fitted inside the edge, encoded into a hidden temp sibling that is
  closed, verified to decode (a JPEG XL file Pillow cannot read is checked by its
  signature) and renamed over the preview; a failure keeps the old file. ffmpeg writes
  with `-update 1`, so a `%` in a name is never a sequence pattern, and reads a video as
  `file:<path>`. Temp files a crash left (`.<stem>.part-<pid>-<thread><ext>`) are
  removed by the clear and, when older than two minutes, by a sweep at startup (which
  also removes one carrying its own PID that predates the process: containers reuse
  PIDs).
- JPEG XL needs no new dependency: Pillow (natively or with `pillow_jxl`, imported when
  installed), else ffmpeg with `libjxl`, else `cjxl`, detected once. Quality maps to
  libjxl's distance (`0.1 + (100 − q) × 0.09` from 30 up: q80 → 1.9, q85 → 1.45; never
  lossless). Without an encoder, saving `jxl` is a 400 `jxl-unavailable` with the
  reason. An encoder that fails because its binary or module is gone makes that write
  detect again once: another encoder found is used, none at all writes the file as WebP
  (logged once) and refreshes the availability Appearance reads; any other failure
  raises without a probe. "No encoder" is probed again on the settings read, on a
  `jxl` save and after 60 s, so one slow probe never disables JPEG XL until a restart; if JPEG XL has no encoder, previews are written as WebP
  and count as the format written. Encoder processes (and ffmpeg's frames) are tracked,
  bounded by a 30 s timeout and killed at the app's cleanup, after which none starts.
- A preview in another format than the one written now is *outdated*.
  `/images/thumbnails/status` reports `pending` (missing), `outdated` and `ready`; the
  missing job never writes a second preview beside an outdated one. Convert outdated
  (`POST /jobs/thumbnails {mode: "convert"}`, "Convert outdated thumbnails") runs under
  the thumbnail job's kind, guarded admission, worker threads and cancel: for each
  outdated file it writes the new preview from the source (a video with no decodable
  frame fails instead of getting the placeholder; a current preview it keeps must
  decode), then removes that file's old-format previews (regular files in its
  `thumbnails/` folder that the source owns, the source still present; one already gone
  counts as removed, and a path that is the new preview under another spelling is never
  removed). Files sharing a preview name are converted once, as a group, from
  the first source above, and each file's bytes count once. A failure keeps the old
  preview; orphans (source gone) and links are never touched. While it runs, a change of
  the thumbnail format, quality or size limit is a 409 `busy` naming it, and should the
  format still change underneath, the job stops at the next file without removing
  anything ("Stopped: the thumbnail format changed …"). Its final line is "N converted · F
  failed · <before> → <after>"
  (`tests/test_thumbnail_convert.py`, "Convert outdated replaces older-format previews"
  in `workspace-system-sources.spec.mjs`). Nothing converts by itself: Appearance's
  format change only makes previews outdated.
- A thumbnail is only a grid or cover placeholder. Inspection refuses any path under a
  root's `thumbnails/` (403) and always parses the original; the media viewer, its
  download, History's viewer and sending use the original
  (`TestThumbnailsAreOnlyPlaceholders`, `the viewer and its download always use the
  original` in `workspace-media-viewer.test.js`). The inspector cover accepts the
  listing's preview in any format the file owns (`inspection-state.mjs`).
- The workspace decodes a 1×1 embedded JPEG XL once (`thumbnail-support.mjs`); where it
  fails, cards and the inspector cover load the original for a `.jxl` preview, and any
  preview `<img>` that fails to load falls back to the original (the legacy gallery
  already does). A video whose preview it cannot show keeps its own element (Images) or
  its "Video preview" label (Library), never a broken image. History's cover URL names
  no format, so the server serves a `.jxl` preview only when the URL carries `jxl=1`
  (added by the workspace where its probe decoded JPEG XL: Firefox may not list it in
  Accept; any value but `0` or `1` is a 400) or the request's `Accept` lists
  `image/jxl`; otherwise the file's preview in another format, else the original
  image or a video's placeholder. Appearance says when this browser cannot show JPEG XL thumbnails.

Overview (`system-overview.mjs`) reads `GET /prompt_manager/system/overview` once per
visit through `latestOnly()` on the section scope; a late answer after the section left
paints nothing. Until it answers, the section shows "Loading the overview…"; a failure
shows "The overview could not be loaded. Your saved data has not changed." and Retry,
never zeros. The thumbnail row is a separate read of `/images/thumbnails/status` (a
walk of every source), so the other values never wait for it: "Checking thumbnails…"
until it answers. The Backup status row is likewise its own read of
`/prompt_manager/backup/status` (S9). The health check (`POST /prompt_manager/system/health`) is a `read`
registry job of kind `health`: one at a time, beside a scan, not cancellable and with
no file outcomes. The page tracks the started job in the Activity store (its event
stream; the store marks it lost if the server stops listing it), never by polling. A press while one runs (another tab) waits
for that one. The last report lives for this server session (`GET
/prompt_manager/system/health`) and describes the database it was taken from: a
restore that replaces the database forgets it, and a check that started before the
swap is not kept. The check's own connection opens a `mode=ro` URI that keeps a Windows
UNC path (`file:////server/share/prompts.db`; pathlib's `file://server/share/...` is a
remote authority SQLite refuses). A check that raises fails only itself, and the
missing-files check reads its rows inside the snapshot but stats the files after it
ended, so a slow source never pins the WAL. Statistics
read `GET /prompt_manager/system/statistics` (`by_category`, the legacy `/stats` totals
and `rated_images`).

Read-only standard for a check or preview (S2's health check; S6 and S8 previews use
the same): the database and `-wal` bytes are unchanged, the `-shm` size is unchanged,
the connection's `total_changes` is 0 at close and the data reads back unchanged. The
`-shm` bytes are not compared: every WAL reader rewrites its read marks there, so no
reader can keep it byte-identical (`test_health_check_never_writes`).

Recorded deviations (plan D60–D75, D79–D82 renumbered D92–D107, D109–D110 and D111–D114; D121–D134 assigned). D94,
the per-section hand-off panel, was retired by S12 (#329): every section is native.

- **D92** (shell): no "Sample configuration" chip. The Save status is truthful: "No
  unsaved changes.", "Unsaved changes.", "Saved in this browser." / "Saved to the server
  configuration." / "Applied until the server restarts.", "Some changes were not saved.
  Review the highlighted settings.", "Saved for this tab only; browser storage is
  unavailable." and "Settings changed elsewhere. Reload this section to see them."
  "Save preferences" is `aria-disabled` (opacity .55, `not-allowed`) while clean or
  saving; a setting the last save could not save gets a red outline. CSS under D92.
- **D93** (Overview): real values. The metrics are the Library's eligible positives
  and active saved negatives and the indexed `generated_images` rows (Q2). "Database
  integrity" is "Not checked" until a check ran this session, then "N issues" (checks
  that found issues, even beside a check that could not run), "Passed" or "Incomplete".
  The review row is "N ambiguous image links" (the Needs review queue's pending count,
  `needs_review.pending`) with the concept's line; its Review metadata action opens
  `#/review` (the concept's `go-review`). A count
  that is not an integer reads "Ambiguous image links" / "Not available yet." (Q6).
  "Backup status" says what `GET /prompt_manager/backup/status` reports, in Backup &
  export's own status line (D103, S9): "Checking backups…" until its own read answers,
  then the session's last download and the newest safety backup, "No backup recorded."
  or "The backup status could not be read.".
  The thumbnail row is "Checking thumbnails…" until its own read answers, then "No
  missing thumbnails", "N missing thumbnails" (the pending count of
  `/images/thumbnails/status`, followed by " · M outdated" when previews are in an
  older format and " · K failed" when automatic previews failed this session) or
  "Thumbnail status unavailable"; Review thumbnails opens the thumbnail dialog (D99),
  and while previews are outdated a second button, Convert outdated, opens it on that
  scope (D131). "One display configuration" says "Grid
  and density settings are shared across views." (no paging). The sources row is
  "N output sources" with "Monitoring and enrichment share one source list.", prefixed
  by "M of N configured sources available." or "None configured is available; ComfyUI's
  output folder is used." when that is the case.
- **D95** (health report): the concept's `health-check` dialog titled "Health report",
  with no sample note. It lists every check in the server's order: Integrity check,
  Foreign keys & pair references, Canonical hashes, Pair uniqueness (added), Missing
  files, Ambiguous metadata, Consistency (added). Missing files has one row per image
  source when there are several ("Missing files · output"). Each chip is the real
  result: "Passed", a count with what was found ("2 broken references", "1 link needs
  review", the concept's "N links need review" and "N images need review"; "100+
  problems found" when SQLite's integrity check stopped at its limit), "Not
  available" (an unavailable source), "Not available yet" (Ambiguous metadata, Q6) or
  "Could not check". A `p.small` after the list says when it was checked and whether
  every check passed, some could not run, or issues need review. Close / Review missing
  links, which reviews a fresh Missing-file links plan (D100, S7); it never cleans up.
  Nothing is changed by a check.
- **D96** (Statistics): "Usage statistics" without the sample note: the concept's
  "Runs by category" table (Category / Runs / Avg. rating) with the real runs and
  average rating of eligible parents per category ("Uncategorized", "Not rated"),
  then a second table "Library totals" (Total / Count: Prompts, Tags, Images from the
  legacy statistics, and Rated images: indexed images whose linked prompt is rated).
  The Overview row says "Runs and ratings by category, with library totals." because
  that is what the dialog shows (the concept's own dialog shows only "Runs by
  category"); per-prompt run counts, prompt tags and dated runs are recorded, but no
  table of them exists yet. A failed read toasts "Statistics could not
  be loaded. Your saved data has not changed."
- **D121** (production hub): the Overview hub's System card reads the concept's System
  heading and "Open System"; the concept has no hub card.
- **D122** (shell): the section nav's `aria-label` is "System sections"; the concept's
  literal label is its prototype action name "system-tab sections".
- **D123** (Maintenance): the concept's Maintenance notice
  ("Check first. Review changes. Then apply." + Run health checks) was delivered ahead of
  the rest of Maintenance (S2), as the native place that runs the health check; since S7
  the section's two surfaces follow it and its hand-off panel is gone. A press shows "Checking…" (`aria-disabled`) and opens the report; a
  failed check toasts "The health check could not finish. Nothing was changed."
- **D97** (Appearance): added rows for the legacy gallery settings the concept leaves
  out, after its own rows: "Show image details on cards" (`showImageInfo`; off hides a
  card's title and file line, Info and Tag stay at the end of the row), "Collapse viewer
  details by default" (`sidebarCollapsedByDefault`; the viewer opens with
  `#viewer-details` hidden and a "Show details"/"Hide details" toggle with
  `aria-expanded`, the stage taking the whole width), and the operation "Reset display
  defaults" ("Restore this browser’s display defaults, then save. Other settings are
  kept.", button "Reset"), which drafts the gallery's defaults for the browser rows only.
  The node search timeout shows the server's real value, not the sample 30. CSS under D97.
- **D98** (Sources): each configured source's row has a plain Remove after its scan
  button; nothing changes until Save. A source added this session reads "<path> · not
  saved yet" with Remove only until Save; a configured source the server cannot use
  reads "<path> · <server reason>" with Remove only. A line under the list says when the
  list is not the whole story: "No source is configured. After saving, ComfyUI’s output
  folder is used." (the last one removed) and "No configured source is available;
  ComfyUI’s output folder is used." (every configured source unusable). Auto-detected
  rows are listed without Remove and say they are the default on the row itself:
  "ComfyUI/output · watching · default" ("ComfyUI/" only when `in_comfyui`; their scan
  still sends the server's public path), with no line under the list when nothing is
  configured. "restart pending" is the third
  watch state. The operation "Refresh files" ("Read every source again to find files
  added or removed outside ComfyUI.", button "Refresh") closes the surface: it calls the
  legacy gallery's `/gallery/rescan` and toasts "Files refreshed. N files in M output
  folders." or "Files could not be refreshed. Try again.". The concept's "sample path"
  note is prototype copy and is dropped.
- **D99** (Sources/Overview, S5): the concept's `thumbnail-review` dialog ("Thumbnail
  maintenance") with production copy. A `p.small[role=status]` line before the actions
  says what the server's read-only plan (`GET /images/thumbnails/plan`) counts for the
  chosen scope: "N previews to generate · M already up to date." or, for Rebuild all,
  "Every preview is regenerated: N files.", N counting the files past the per-batch
  cap too (#317); "Counting previews…" while it is read and "The previews could not be
  counted." when the read fails (Start still starts). Each scope is read once per
  dialog, since size and quality change no count. The primary is "Start" (the
  concept's "Simulate thumbnail job"). Overview's Review thumbnails opens the same
  dialog (the concept's row starts a sample "Thumbnail check" job directly). Maintenance's
  Thumbnail cache → Review options opens it too (S7). `thumbnail dialog matches the concept
  from sources|overview` (system-concept.spec, both themes and sizes) maps the line and
  "Start"; `thumbnail dialog comparison detects mutations` proves they are the only
  difference and catches a renamed option, swapped options, swapped actions and a
  changed style.
- **D111** (Activity, S5): each job kind's pill verb, toasts, line, tiles, outcomes,
  openable rows and labels come from one descriptor table (`activity-kinds.mjs`;
  `activity-store.mjs` re-exports it). The scan's (D56) and image tagging's (D89) are
  unchanged. A thumbnail job reads "Generating thumbnails · N% · a / b" in the pill,
  counts Generated (green), Skipped and Errors (red), lists Generated, Errors, Skipped
  (Generated and Skipped rows open the viewer), says "Thumbnails finished · N generated ·
  M errors" ("cancelled · N generated", "failed · …") and keeps "Job log" and "Cancel
  job". Every other kind (the health check, a kind this page does not know) gets the
  generic descriptor: title-based toasts and no tiles or Results, so it never shows the
  scan's zero tiles (S1 review M6, S2 review M3). A running job without a known total
  has an indeterminate `<progress>` (no `value`) and a pill without a percentage, whose
  mini bar (`aria-hidden`; the pill's label carries the state) is marked `indeterminate`
  and sweeps instead of showing an empty fill (a dimmed full bar under reduced motion). Only a
  running job the server can cancel shows Cancel. Tests:
  `tests/js/workspace-activity-kinds.test.js`, and in `workspace-activity.spec.mjs` "a
  thumbnail job shows Generated Skipped Errors, its pill verb and its toasts" and "a held
  legacy job and the health check show no tiles, no Results and no made-up percentage".
- **D111, S7 kinds**: a maintenance apply (`maintenance`, titled after its operation,
  not cancellable except "Store each workflow once") reads its title in the pill (no
  total, so no percentage), counts Changed (green), Protected and Errors, keeps
  "Maintenance log", and toasts "<title> finished · N changed" or, when it failed,
  "<title> failed · <the server's status line>" (a plan-changed apply says "The library
  changed since the review; nothing was changed. Review the plan again."). The duplicate
  search (`duplicates`) reads "Finding duplicates · N% · a / b", counts Groups found
  (rows open their first copy), says "Duplicate search started." and "Duplicate search
  finished · N groups found", with "Search log" and "Cancel search". The duplicate
  cleanup (`duplicate_cleanup`, not cancellable) reads "Removing duplicate files", counts
  Removed (green), Kept and Errors (Kept rows open the viewer), and toasts "Duplicate
  cleanup finished · N removed · M errors" or, when it failed, "Duplicate cleanup failed ·
  <the server's status line>". A refused start names the job the server named, not the
  first running one. Every job keeps Download report. Tests: the
  three S7 cases in `tests/js/workspace-activity-kinds.test.js`.
- **D112** (Activity, S5): a legacy-page operation held on the registry (not
  cancellable, no outcome groups) reads as its server title ("Database restore (admin
  page)", "Thumbnail clear (gallery page)"), with no tiles, no Results, no Cancel, an
  indeterminate bar while it runs and "<title> finished." when it ends, whatever its
  kind (the gallery's clear is a `thumbnails` hold).
- **D100** (Maintenance, S7): the concept's `maintenancePlan` dialog ("Review maintenance
  plan") on the server's read-only plan (`POST /maintenance/preview`), opened by each
  Review plan and by the health report's Review missing links. No sample note. The `h3`
  is the operation's row title (the concept prints its `data-plan` name, "duplicate
  prompts"); the `dl.meta-grid` keeps Scope / Protected / Backup /
  Media on disk with the plan's values: its scope label, its protected reasons joined
  with " · " ("Nothing is protected" when none), "Create and verify before changes
  (<db>.backup_<date and time>)" or "No safety backup (the policy is off)", and its
  media line. (A `workflow_storage` plan would read "Store each workflow once" and
  "Always created and verified first (…)", but Maintenance has no row for it: the
  concept has none, so the migration is reviewed and applied through the API and only
  its jobs appear, in Activity.) A
  `details` "Listed records (N)" ("(N of M)" past the server's 50) holds the records the
  plan names in a `pre`. The `p.small` says "Nothing changes until you apply. This
  review holds for N minutes." (from `expires_at`), plus "Not enough free disk space:
  about X MB is needed and Y MB is free." when the plan's disk check is short. Then the
  D55 backup box ("Back up the database first (recommended)", "Back up now") and Cancel /
  "Apply reviewed operation" (the concept's "Simulate reviewed operation"). A plan with
  nothing to do (scope 0, "No duplicate prompt text") shows its rows, "There is nothing to
  apply; nothing was changed." and only Close, never an Apply that would start an empty
  job (`a plan with nothing to do shows only Close, never Apply`). Apply sends
  only the token; the dialog closes, "<title> started." is toasted and the job opens in
  Activity. A refused apply stays and says why: "Another job is running: <title>."
  (D57), "This plan was already applied: <job>." (`plan-used`, linking the job), the
  server's line for an expired or replaced review (then the plan is reviewed again at
  once), for a short disk, for nothing to do and for the server's own pre-start failure
  (`apply-failed`). Only our server's refusal is definitive: a known code, or a 4xx whose
  body says `success:false`. A dropped connection, a proxy's 5xx (no code) or a 2xx whose
  body cannot be read may follow a started job, so they say "Outcome not confirmed. Check
  Activity before applying it again." and read the Activity list again
  (`lost apply response…` runs the apply and then drops its answer; `a proxy error or an
  unreadable answer is not a refusal` answers a 504 after the server started the job). A
  review button holds ("Reviewing…", `aria-disabled`) while its preview is read, so a
  double press sends one preview (`a review button pressed twice sends one preview`). A
  failed apply records its code as the job's `scope.failure`; when the plan was overtaken
  (`plan-changed`, `storage-changed`, `plan-expired`), its Activity view offers "Review
  again", which reviews the same operation afresh (a duplicate cleanup whose sources
  changed goes back to Maintenance to search again).
  Cancel and Escape write nothing (`Cancel writes nothing`: a read-only dump of the rows
  and the media hashes, `GET /__test__/system/digest`). `plan dialogs match the concept`
  (system-maintenance-concept.spec, both themes and sizes) compares each of the four
  operations' dialogs with the concept's, mapped by these values only.
  S12 (#329): the `rotated_logs` plan (Diagnostics & logs › Review log cleanup, `h3`
  "Rotated log copies") changes no library record: its Backup reads "Not needed: no
  library record changes" (`backup.policy: "none"`), it has no D55 backup box, and its
  `details` reads "Listed files (N)" (`a plan without a database change reads Not needed
  and shows no backup box`, `rotated_logs details read Listed files`).
- **D101** (Maintenance, S7): Duplicate image files → Find duplicates starts the
  duplicate search (Activity, D111); its job view adds "Review groups" after Download
  report once it finished with groups. The concept's `duplicates` dialog ("Review
  duplicate image files") shows the real groups ten at a time: a `p.small[role=status]`
  "Groups 1–10 of N", then per group a `p.small` "N identical files · S each" and one
  `.compare-grid` with a `.compare-item` per copy (its preview as the concept's
  `img.sample-fluid`, and a radio "Keep <source>/<path>", the oldest copy checked, its
  label wrapping anywhere (CSS under D101) so long dataset paths never make the review
  scroll sideways (`long copy paths wrap; the duplicates review never scrolls sideways`,
  desktop and phone); a copy
  gone since the search is disabled and says so), "Previous groups" / "Next groups" when
  there are more than ten, the `p.small` "Removing a copy deletes its file from disk;
  its database links move to the kept copy. History keeps the removed paths in its run
  records. Nothing is deleted until you review and apply the cleanup.", and Cancel /
  "Review duplicate cleanup". Choices survive paging; the review sends every group shown
  so far as `{group_id, root_index, path}` (never a bare path: two sources may hold a
  file at the same path) and opens the D100 plan for "Duplicate image files". The
  primary stays the concept's plain button; the plan it opens has the primary.
  `duplicates dialog matches the concept` and `duplicates comparison detects mutations`
  (system-maintenance-concept.spec) pin the mapping.
- **D102** (Maintenance, S7, Q5, #222): Metadata cache clears only the browser entries
  Prompt Manager owns as a cache (`web/js/cache-ownership.js`). The inventory of every
  storage writer has none (see "Browser cache ownership" below), so the row's line adds
  "Nothing is cached in this browser." and its Review plan is `aria-disabled` and opens
  nothing; a browser that refused to list its storage says "This browser did not allow
  checking its storage." instead. With owned entries (a test names one through the module's inventory), Review
  plan opens "Review maintenance plan" › "Metadata cache" (Scope "N cached entries in
  this browser", Protected "Preferences and other tools’ data in this browser", Backup
  "Not needed: the database is not changed", Media "No files are removed") and "Clear
  browser cache" clears exactly those and toasts what it cleared. The row's dimmed button
  is D93's style. `maintenance matches the concept` (both themes and sizes) maps only this
  row; `maintenance comparison detects mutations` proves it is the only difference and
  catches a removed row, a renamed operation, a lost danger mark and a swapped token.
- **D103** (Backup & export, S9): "Back up" (the concept's "Simulate backup") downloads
  the verified copy of `GET /prompt_manager/backup` through the api client (the same
  `downloadDatabaseBackup` as every backup box; a copy whose `X-PromptManager-Verify` is
  not `ok` toasts "Database backup downloaded, but it did not pass verification:
  <reason>"), then reads the status again. The `p.small#backup-status` under the
  actions shows only what `GET /prompt_manager/backup/status` reports: "Downloaded
  backup · <date, time> · verified." (or "not verified") for this session's last
  download and "Safety backup · <date, time> · N on disk." for the newest
  `<db>.backup_*`, else "No backup recorded."; "Checking backups…" while read and "The
  backup status could not be read." when it failed (never "no backup"). Portable
  export's "Export JSON" (the concept's "Export sample JSON") downloads the Library
  document (`/library/export`); a second `.row-actions.spaced` adds "Export all prompts
  JSON" (`/export`, the legacy admin page's Export of every prompt) and "Export saved pairs JSON"
  (`/pairs/export`), plain buttons as the concept's; a failed export toasts "The export
  could not be downloaded. Nothing was saved to this device." "Import / inspect file"
  opens the shared "Import & inspect" (D15, D55), as Images does.
- **D104** (Backup & export, S9, Q4): Recovery policy keeps the concept's "Proposed" chip.
  "Create a safety backup before maintenance" is the server's `safety_backup`
  (`maintenance.safety_backup`) policy. Its line says what it does ("Keep a verified copy
  of the database on disk before maintenance, cleanups and LoRA imports change records. A
  restore always keeps one."); the concept's "Preview a verified backup before applying
  operations that change records." describes nothing the server does. It is drafted behind the Save row and saved with
  `expected_revision` (a configuration changed elsewhere is the conflict status); it is
  disabled with "Server settings could not be read." when the read failed. "Scheduled
  database backups" offers Manual only; Daily and Weekly stay listed but disabled
  (there is no scheduler), the row's line is "Daily and Weekly are not available yet:
  back up with Back up whenever you choose." (the concept's proposal line is
  prototype copy) and nothing is drafted for it. The warning note reads "Restores are
  staged, migrated and validated before replacing live data."
- **D105** (Backup & export, S9): "Review restore…" picks a file (no type filter: the
  safety backups `<db>.backup_<time>` have no `.db` extension; the server validates) and stages it
  (`POST /prompt_manager/restore/preview`, multipart through `fetchUrl`; the button reads
  "Reviewing…" meanwhile). "Restore preview" is the shared review: no sample note; the
  concept's `dl.meta-grid` Snapshot (file name · size · date), Integrity ("Verified" plus
  the stage's document repairs), Migration ("Current schema" / "Upgraded to the current
  schema"), Safety backup ("Created before replacing (<label>)": the restore always
  keeps one; the Snapshot size is whole KB below 1 MB), then "In the backup" and "In the library now" (prompts, image links, saved
  pairs, runs); a `p.small` "Restore replaces the whole library with this backup and
  runs alone in Activity. Nothing changes until you restore. This review holds for N
  minutes."; the D55 backup box ("Downloads a copy of the prompts database before the
  restore replaces it."); Cancel / "Restore" (the concept's "Simulate restore", still
  `danger`). Leaving it any way but Restore (Cancel, ×, Escape, another dialog, the page
  leaving) discards the stage (`DELETE …/preview/<token>`). Restore starts the "Database
  restore" Activity job (exclusive, not cancellable, indeterminate; its descriptor ends
  with the server's line) and opens its view; a review the server asks to redo (expired,
  library changed) stages the same file again. A Restore refused as `busy` (another job
  runs; the server keeps the token) opens the same review again with "Discard" / "Restore":
  Restore retries, Discard (or leaving) releases it. A job whose leases did not drain
  hands its review back (`scope.review_kept`, "…The review was reopened in the page that
  started the restore: restore it again there, or discard it."): the page that started it
  opens the review again over the page shown now, with Discard / Restore. Every other path
  that abandons a token after Restore (a used, expired or replaced review, a lost answer)
  sends its DELETE, which changes nothing for a token the server dropped or a job took. A
  third review while two are held elsewhere toasts "Two restores are already being
  reviewed in another window. Discard one there, or try again when it expires (15 minutes
  after it was reviewed)." An upload whose answer is lost because the section was left
  mid-response is not discarded by the page: the server's 15-minute expiry removes it. When a restore the page saw running
  finishes, the cached Images and Library visits are dropped, the sidebar counts and
  saved views are read again and the open route is shown again (the Activity job view
  stays). A refused file toasts "This file can't be restored: <validator reason>.". A
  review value may wrap anywhere (`#modal-body > dl.meta-grid dd`, CSS under D105), so a
  long file name keeps the concept's two equal columns at phone width.
- **D106** (Integrations, S10, Q8): the send bridge always asks for the ComfyUI window
  and node, and the nodes always follow ComfyUI's own renderer, so "Default send
  destination" offers only "Ask each time" and "Host compatibility" only "Follow host
  renderer"; the concept's other options stay listed but disabled, each row says why in a
  `p.small` under its description, and nothing is stored for them. "Return to canvas
  after send" is off and disabled with "Not available: sending applies the text in
  ComfyUI and keeps this page open." Host compatibility adds a `p.small[role=status]`
  with the renderer the open ComfyUI windows report over the bridge (`renderer`:
  `classic` or `nodes2` on each Prompt Manager destination,
  `web/js/prompt-bridge-adapter.js`; `#renderer-status`): "Detected: Nodes 2.0 (1 ComfyUI window).", "…
  Classic and Nodes 2.0 (2 ComfyUI windows).", "ComfyUI is open; add a Prompt Manager
  node to see which renderer it uses." or "No ComfyUI window is open in this browser, so
  its renderer is not known."; it looks again when the page becomes visible, and only the
  newest check paints. The two selects never shrink for the reason lines and, at phone
  width, may grow to 160px (the concept caps them at 120px and cuts "Follow host
  renderer"), so each shows its chosen option whole (`selectsFit` in the comparison). "LoraManager
  integration" replaces the concept's sample line with the detection ("Detected:
  custom_nodes/ComfyUI-Lora-Manager." / "Not detected in ComfyUI’s custom_nodes
  folders." / "Found at …, but outside ComfyUI’s custom_nodes folders, so it can’t be
  enabled." for a linked install the server would refuse, from `/lora/status`'s
  `detected_usable`); the legacy read-only "LoraManager path" row follows it; "Automatically
  include trigger words" adds "Trigger words are (not) loaded." (the trigger cache, a
  separate fact). The notes read as the row description (`.setting-row p`). Pinned by
  `integrations matches the concept` (both themes and sizes) and its mutation check
  (system-integrations-concept.spec), the D106 tests of
  workspace-system-integrations.spec, and `each destination reports its window
  renderer, and System › Integrations shows it (S10)` on the real host
  (tests/comfyui/prompt-bridge.spec).
- **D107** (Integrations, S10): "LoRA reference data" reads "Review import" (the
  concept's button only simulated a job) and opens "Review LoRA import" in the concept's
  record-changing review (its `maintenancePlan` shell): `h3` "LoRA reference data", the
  `dl.meta-grid` Scope ("Replace N imported prompts with M LoRAs"), Found ("N metadata
  files · M readable"), Protected (the saved pairs that block it), Network ("Downloads
  up to N example images from CivitAI[ with your saved key]" / "No downloads"), Backup
  and Media on disk, a `details` "LoRAs found (N)", the hold line plus "Example images
  downloaded before a failure stay in the image cache." in the `p.small`, the D55 backup
  box and Cancel / "Start import" (`danger` when it would remove every import). A review
  that saved pairs block, or with nothing to import, shows its rows and only Close.
  Start runs the "LoRA reference import" Activity job (D111 descriptor: Imported, Linked
  to existing, Skipped, Images downloaded, Download errors, Errors; Cancel only while it
  downloads; a failure ends with the server's reason). Pinned by `LoRA import review
  matches the concept` and its mutation check (system-integrations-concept.spec) and
  the import tests of workspace-system-integrations.spec.
- **D114** (Integrations, S10): the CivitAI key field is never filled in (the page never
  receives the key). Typing drafts a new key, a blank field keeps the saved one (no
  draft), and "Clear saved key" (shown only while a key is saved) drafts its removal.
  A `p.small#civitai-key-status[role=status]` (it also describes the field) says "A key
  is saved." / "No key saved." / "The new key replaces the saved one when you save." /
  "The saved key is removed when you save."; pressing "Clear saved key" keeps the
  keyboard on the key field, and the line is rewritten only when its text changes. A typed
  key is dropped when the section is left (the field comes back empty); leaving without one
  keeps the last Save's outcome, so Sources still marks a refused source. The field is `autocomplete=new-password`, so browsers never offer it
  as a login; the concept's
  prototype warning is dropped. The LoRA rows save through the one Save row:
  `POST /lora/enable` with `expected_revision` (a config.json changed elsewhere is
  `settings-changed`), then `/settings` for any other host change with the revision the
  first save returned (`writeHostInParts`). When only one part is written, the store keeps
  what saved, adopts its revision and highlights only the rest: the status is the
  server's reason (an unusable source) or "Some changes were saved to the server
  configuration; the highlighted ones were not.", and the next Save needs no conflict
  (`a save where only the LoRA part is written says so; the next save needs no conflict`). Pinned by `key never prefilled; blank keeps; Clear saved key
  clears; saved through Save preferences (D114)` and `a LoRA save over settings changed
  elsewhere is a conflict, never a blind write` (workspace-system-integrations.spec).
- **D113** (Sources): the Worker threads range ends at the server's `cpu_count` (the
  concept's fixed `max=16`) and shows the server's real `worker_threads` (the concept's
  sample 4).
- **D127** (Sources): the concept's `add-source` `simpleInput` with production
  copy: the field is "Folder path" (the concept's "(sample only)" dropped), it starts
  empty (not the demo "/images/new-collection"), and the action is "Add" (not "Apply in
  prototype"); Enter in the field adds too. `add source dialog matches the concept`
  (system-concept.spec) compares the open dialog with these three mapped, and `add
  source comparison detects mutations` proves the mapping is the only difference. A
  folder the server refuses keeps the dialog open with the field
  `aria-invalid` and the server's reason in a `p.small[role=alert]` above the actions;
  a folder the server names as one already listed says "This folder is already a
  source.", and a check that cannot finish says "The folder could not be checked. Try
  again."; neither adds anything.
  The concept dialog does no check, so `invalid source shows the server reason`
  (workspace-system-sources.spec) pins it.
- **D128** (Sources): states the concept's sample rows never show. A source the
  running monitor still watches after "Watch for new outputs" was saved off reads
  "<path> · watching until restart" (still "Scan changes"), and the save's status adds
  "Restart ComfyUI to apply: Watch for new outputs." from `restart_required_fields`. A
  configured source the effective list holds under another spelling reads "<path> ·
  same folder as another source" with only Remove. A save refused for one source
  (`invalid-source`) marks that row (`aria-invalid`, outlined like a D92 setting) and
  the status is the server's "<path> can't be used: <reason>. Remove it or reconnect it,
  then save." ("Remove it, then save." when reconnecting cannot help). Pinned by
  `Watch for new outputs saves monitoring_enabled; a restart applies it` and `a save
  refused for one unusable source names it and marks its row`
  (workspace-system-sources.spec) and the system-state and system-sources node tests.
- **D129** (library location, S6b): the concept shows neither where the library lives
  nor a way to move it. System › Overview's "Library health" gains a "Library location"
  `.operation` row after Backup status, in that row's own markup: "<path> · <why>",
  plus "You chose to keep it here." and any `PROMPT_MANAGER_DATA_DIR` problem, with
  "Move library" for a node-folder library and "Details" (a `dl.meta-grid` dialog)
  otherwise. While a node-folder library is offered (`location.offer`), the shell shows
  the concept's `.notice` (the Sources notice's markup: `div > h2 + p`, then
  `.row-actions` with "Keep it here" and the `ca-primary` "Move library") in
  `#library-banner` above `main`, in main's own width and padding, with no bottom margin
  (main's padding spaces it); `workspace.css` carries the `/* D129 */` rules. Move
  library opens the shared review (From, To, Size, Moves with it, Old files). Pinned by
  `system shell and overview match the concept` (the mapping inserts the row) and
  `library notice matches the concept notice` with its mutation check
  (system-concept.spec), and the D129 tests of workspace-system.spec.
- **D130** (Appearance, #317): the concept keeps "Thumbnail quality, size limit" in
  Appearance (README) but draws no rows for them. Appearance gains a second
  `section.surface`, "Thumbnails", after "Browsing & display", built from the concept's
  `.setting-row` markup: "Thumbnail format" (WebP / JPEG XL / JPEG), "Thumbnail
  quality" (1–100), "Thumbnail size limit" (128–2048 px), all host settings saved with
  the revision, then the operation "Convert outdated thumbnails". JPEG XL is not in the
  concept: it is offered only when the server found an encoder, else the option is
  disabled and the row shows the server's reason ("No JPEG XL encoder found. Install
  pillow-jxl-plugin …"); a row note also says when this browser cannot show JPEG XL
  thumbnails. After a save that changed the format, the operation's line reads "Format
  saved. Existing previews keep their old format until you convert them." Pinned by the
  appearance comparison (the mapping inserts the surface), `Appearance › Thumbnails
  saves the server thumbnail settings` (workspace-system.spec) and
  `workspace-system-thumbnails.test.js`.
- **D131** (Thumbnail maintenance and Overview, #317): the dialog's Quality and Preview
  size limit fields are replaced by a `p.small` after the plan line naming the server's
  settings ("Previews are written as WebP · quality 80 · 512 px. Change this in System ›
  Appearance."), since previews follow those settings everywhere; Scope gains "Convert
  outdated (removes older formats)", whose plan line reads "N previews in an older format
  to convert." Overview's thumbnail row reads "N missing · M outdated · K failed" and,
  while previews are outdated, adds a second button, Convert outdated, that opens the
  dialog on that scope. Pinned by the thumbnail dialog comparison (mapped) and its
  mutation check (system-concept.spec) and `the thumbnail row counts outdated previews`
  (workspace-system.spec).
- **D132** (Auto-tagging, S11, #328, OQ1/OQ5/OQ6/OQ10): "Tagging model" (`#tag-model`)
  lists the four real engines in the concept's order, each `<label> · <size_label>` from
  `GET /prompt_manager/autotag/models` ("WD14 SwinV2 · ~400 MB", "WD14 ViT · ~400 MB",
  "GGUF JoyCaption · ~4.5 GB", "HuggingFace JoyCaption · ~16 GB"; GGUF takes the
  concept's "Ollama" slot, as D61 does). Its description is the chosen engine's live
  status instead of the concept's design note: "Loading model status…" (the select is
  disabled until the read answers), "Model status could not be read.", "Not downloaded.
  Download size <size>.", "Downloaded. Not loaded.", "Downloaded and loaded." or "Partly
  downloaded. Download again to finish." (GGUF with one of its two files). The concept's
  two simulated job buttons are "Download" ("Download again" for an engine on disk),
  which starts the `model_download` Activity job and opens it, and "Unload", a direct
  `POST /autotag/unload` with a toast ("<label> unloaded.") and a fresh status read,
  enabled only while a model is loaded; a busy model's refusal reads "The tagging model
  is busy: <operation>. Try again when it finishes.". "Preview tagging review" opens the
  Tag image dialog for the image selected in Images with the section's current model and
  values; it is `aria-disabled` without a selection (the D115 sentence "Select an image
  in Images first.") or with an invalid value (the toast names the setting); a saved
  engine the section does not know reads "The saved tagging model is not one of these
  engines. Choose one and save preferences.". A part-downloaded GGUF offers "Download
  again"; an Unload that found nothing loaded only reads the status again. At phone width
  the concept's 120px select cap cuts every engine label, so `#tag-model` alone keeps its
  natural width there and its row wraps the select under the text (`workspace.css`
  `/* D132 */`). The concept's cap cuts Existing tags too ("Replace after review"); by the
  maintainer's decision (#398) that parity is not kept: `#tag-existing` never shrinks for
  its row text and at phone width keeps its natural width beside the text (its row does
  not wrap; the text column narrows). 1600 is as the concept draws it. The comparison
  expects `selectsFit` true for every select and measures each row's `flexWrap` and
  whether its control sits under the text. Pinned by `auto-tagging matches the concept`
  (both themes and sizes), `auto-tagging at 390 wraps only the engine row`, `auto-tagging
  selects fit every option at 390/320 wide` (every option of both selects, both themes, no
  page scroll) and the mutation check (a wrapped row, a squeezed engine and Existing tags
  select) (system-tagging-concept.spec), and the model, Download, Unload and Preview
  tests of workspace-system-tagging.spec.
- **D133** (Auto-tagging, S11): a "Suggestions per image" row (`#tag-suggestions`,
  number 1–100, host key `autotag_suggestion_limit`) follows the two confidence rows:
  the persisted count the Tag image dialog's Suggestions field (D91) starts from. Pinned
  by the comparison's mapping (it inserts the row) and `defaults save through Save
  preferences and the Tag image dialog uses them`.
- **D134** (Auto-tagging, S11, OQ2/OQ3/OQ7/OQ8; amended by #379): an `.operation` "Tag
  output images" with "Review" follows `.row-actions`: the per-image batch (#379), whose
  description reads "Tag each output image from its own pixels with the model above.
  Prompts are not changed. Review the scope first.". Review is
  `aria-disabled` (the press explains) while the chosen engine is not downloaded or a
  value it uses is invalid; otherwise it posts `/autotag/batch/preview` with the drafted
  engine and policy (read-only) and opens "Review tag output images" in the concept's
  record-changing review (its `maintenancePlan` shell): `h3` "Tag output images", the
  `dl.meta-grid` Model, Existing tags, Images, Already auto-tagged, Images to tag
  ("about N"), Time, Settings ("General <g> · Character <c>" or "Template of N
  characters", then " · up to L tags per image" with the preview's saved
  `suggestion_limit`) and Backup, the estimate sentence ("Nothing is written until you
  start. The number to tag is an estimate: images changed since they were tagged are
  tagged again, and missing files are skipped. Activity reports what was tagged.") in the
  `p.small`, the D55 backup box and Cancel / "Start tagging", which starts the
  cancellable `autotag` job and opens it. Time (DEC11) is `m = max(1,
  ceil(estimate_seconds / 60))` read "about m min" under 90 and "about H h M min" (or
  "about H h") from 90, followed by " on a processor; a graphics card can be faster."
  or, when the preview says WD14 runs on CUDA, " on the graphics card."; the language
  models read "Language-model engines take longer per image. Activity shows the time
  left once tagging starts.". The option label "Skip already tagged" is the concept's;
  for this batch it skips images already auto-tagged (hand tags do not count). Replace
  after review, a model not on disk, a short disk, no image and nothing to tag each show
  the rows with only Cancel and the reason ("Replacing tags needs a review of each
  image. Use Preview tagging review, or choose Skip or Merge for a batch run.", "The
  output folders contain no images to tag.", "Every output image is already
  auto-tagged.", "Not enough free disk space: the safety backup needs about X MB and Y
  MB is free. Nothing is written until there is room.") in the close-only review
  Integrations shares (`openCloseOnlyReview`). Pinned by
  `tag output images review matches the concept`, its mutation check and `the
  Cancel-only review matches the concept review shell` (system-tagging-concept.spec) and
  the batch tests of workspace-system-tagging.spec; the mutation check renames each grid
  label in turn.
- **D109** (Logs, S12, #329): the concept's Logs surface on the live server. The toolbar
  keeps "Level", Refresh and the download (renamed "Download log": the active log,
  `/logs/download/prompt_manager.log`, through the api client), with all six minimum
  levels (All levels, DEBUG, INFO, WARNING, ERROR, CRITICAL; the concept has three) and an
  added `label` "Lines" + `input#log-limit[type=number]` 1–500 (default 100): a value that
  is not a whole number 1–500 after trimming gets `aria-invalid` and sends nothing, a
  Level change included. The `pre` shows the in-memory buffer newest first as `HH:MM:SS
  LEVEL message` ("No log entries at this level." / "The log could not be read."); a
  newer read wins. Added after it, `p.small#log-stats[role=status]` "Log statistics":
  "N entries in memory · E errors · W warnings · F files · S on disk" (errors are ERROR +
  CRITICAL, singular for 1, links not counted). "Runtime log level" adds CRITICAL after
  the concept's INFO, WARNING, DEBUG, ERROR; it and "Console logging" are runtime drafts
  saved by Save preferences (`POST /logs/config`, only the changed keys) and read the
  server's values; their descriptions say "Applied until the server restarts." (the
  concept's say a gallery toggle is replaced and output stays quiet). Inside "Log files &
  retention", after the concept's line ("prompt_manager.log · <size> · rotated copies:
  N", N the `rotated` rows) and Review log cleanup (D100's `rotated_logs` plan): a
  `p.small` "Rotates at M MB · keeps K rotated copies · file logging on|off · memory
  buffer B entries" (read-only), an `.operation-list` of `/logs/files` (`h3` name, `p`
  "<size> · <YYYY-MM-DD HH:MM> · Active log | Rotated copy | Other file | Link, not
  downloaded", "Download" named "Download <name>" except for a link), and the danger
  `.operation` "Clear all logs" / "Empty the active log and delete its rotated copies." /
  "Clear all logs…", which confirms ("Empties prompt_manager.log, deletes N rotated
  copies (S) and clears the lines shown here. The other files in the log folder are
  kept. This cannot be undone."), runs `POST /logs/truncate`, toasts "Logs cleared · N
  rotated copies deleted." (plus " · K could not be deleted") and reads the viewer,
  statistics, files and configuration again. Pinned by workspace-system-diagnostics.spec's
  Task 5 and Task 6 tests.
- **D110** (Diagnostics, S12, #329): the concept's Diagnostics surface with live values.
  The `div.meta-grid` `dd`s read one `GET /system/diagnostics`: Database "Readable ·
  <size> · N images" or "Not readable: <message>"; Sources "N sources · M watching", "N
  sources · K not available", or with none configured "ComfyUI output folder · watching
  | manual scan"; Viewer.js "<pinned version> · pinned vendor". Host renderers is D106's
  renderer line from the send bridge. Each reads "Loading…" first, "Busy: <job> is
  running." when an exclusive job refuses the read (D57's naming) and "Could not be
  read." on any other failure. Run checks opens the shared Health report (D95). The
  buttons drop the concept's "sample": "Download diagnostic report" saves a fresh report
  plus `host_renderers` as `prompt-manager-diagnostics-YYYYMMDD-HHMM.json` (local time)
  and toasts "Diagnostic report downloaded." ("Busy: <job> is running. Try again when it
  ends." / "The diagnostic report could not be downloaded. Nothing was saved to this
  device."); "Inspect" opens `#/inspect`, which reads a file and writes nothing. An added
  `.operation` "Test image link" / "Write a placeholder image record to check that
  linking works." / "Create test link…" keeps the legacy synthetic link (S12-11): it
  reads the most recent prompt on mount (`aria-disabled` with "Add a prompt first." or
  "The most recent prompt could not be read." added to the description), confirms in
  "Create test link" naming the prompt and how Missing-file links removes the record,
  and toasts "Test link created (image record N)." or "The test link could not be
  created. Nothing was changed.". Pinned by workspace-system-diagnostics.spec's Task 4
  tests.
- **D124** (inspector): with "Load metadata on selection" off, the Images panel and
  the viewer's details show "Metadata loads when you ask for it." and a "Load metadata"
  button instead of reading the file. The concept has no such state, so no concept
  comparison renders it: `metadata off shows an explicit load and loads once on request`
  (workspace-system.spec) pins it instead.
- **D125** (prompt blocks, maintainer option A): a resolved prompt whose source is
  `runtime-parts-omitted` (a PromptManager `prepend_text` or `append_text` fed directly
  by a listed run-time node, such as LoraManager's TriggerWord Toggle, was left out)
  shows the muted line "Text added at run time, such as trigger words, is not
  included." under its text. The evidence badge stays "Workflow inferred"; the line is
  never sent or copied. The concept has no such state (`a prompt that left out run-time
  trigger words says so under its text (D125)` in `tests/js/prompt-blocks.test.js`).
- **D126** (prompt blocks, maintainer ruling on the added-conditioning floor): a
  resolved positive whose source is `added-conditioning` (a later stage combined more
  conditioning with the base prompt, as UltimateSDUpscale's ConditioningCombine of the
  PromptManager and a style trigger) shows "Conditioning added at a later stage is not
  included." the same way. A prompt with both shows both lines, D125 first (source
  `runtime-parts-omitted-and-added-conditioning`). Always labelled, with no length or
  "short trigger" heuristic (`a prompt a later stage added conditioning to says so, and
  both lines show together (D126)`).

Appearance scope and states. The Default layout and Thumbnail size defaults start the
Images page only (a new visit, not the current view or a saved view); the embedded
galleries (a prompt history's Shared gallery and its All images / Repeat run dialogs)
have no toolbar, so they keep Grid/Comfortable, while the metadata, cache and card
defaults apply there too. A host row whose `/settings` read failed stays disabled with
"Server settings could not be read."; a rejected timeout says "Some changes were not
saved. Review the highlighted settings." A display default another page saved under one
of this tab's drafts flags that draft with the conflict status, whether it arrived while
System was mounted (a `storage` event) or while it was not (checked again on mount, and
before a write); the draft is never written over the other page's value without that
status first. A change kept for this tab only is stored by the next write storage accepts.

Open question (saved-views follow-up, M8): a saved view stores no layout or size and
always opens at Grid/Comfortable, even when the Default layout is Masonry. Should saved
views store a layout, or follow the default?

`tests/browser/system-concept.spec.mjs` compares the shell and Overview with the concept
(dark and light, 1600×1100 and 390×844), mapping only the deviations above; unmapped,
the difference is exactly the D92, D93, D96 and D122 paths. Each surface's `top` is
measured, which pins the concept's `.surface + .surface` 18px offset that concept.css
also applies inside `.two-col` (copied as is). `statistics and health dialogs match
the concept` maps D95 from the concept's own five rows (labels verbatim): it inserts
Pair uniqueness after Canonical hashes and Consistency last, splits Missing files per
source when there are several, replaces only the chips with the fixture's expected
results, and asserts the full unmapped path list; it maps D96 and compares the
Maintenance notice unchanged. `system comparison detects mutations` removes a row,
renames a metric label, swaps the `--line` token, recolours a chip, renames a check,
removes the totals table and renames a caption. `appearance matches the concept` maps
only D97 and the real timeout (unmapped, exactly those paths differ), and `appearance
comparison detects mutations` removes a row, renames a label and an option and swaps
the `--line` token. `sources matches the concept with|without the notice` (both
themes and sizes; the fixture's monitor watches the first source, as the concept's
first sample is watched, and the concept's Later removes its notice) maps the real
sources for the two samples, D98's Remove and Refresh files, and D113; unmapped,
exactly those paths differ. `sources comparison detects mutations` removes the notice
and a source row, renames a label and the Add source button and swaps the `--line`
token. Paired captures (`{concept,production}-system-{dark,light}-{1600,390}.png`,
`{concept,production}-{statistics,health}-{dark,light}.png`,
`{concept,production}-appearance-{dark,light}-{1600,390}.png`,
`{concept,production}-sources-{with,without}-notice-{dark,light}-{1600,390}.png`,
`concept-thumbnail-review-{dark,light}-{1600,390}.png`,
`production-thumbnail-review-{sources,overview}-{dark,light}-{1600,390}.png`) are test artifacts; there
are no PNG baselines. `tests/browser/workspace-system.spec.mjs` covers the real counts,
the health check and metric 4, a failed check, the thumbnail row against the status
endpoint (painted after the rest when its read is held, unavailable when it fails), its Review thumbnails
opening the thumbnail dialog and starting the job, the review row (Q6), the section links, the Statistics dialog against the
route, a load failure with Retry and late answers after leaving, and Appearance: new
visits versus the current view and a saved view, the embedded gallery keeping Grid,
metadata off and on request, the cache (counted up to a request barrier), hidden paths,
the video defaults, the host fields with their revision, a stale one and a failed read,
reload and a second tab, a second tab's save while System was closed, storage refused,
the D97 rows and reset. `tests/browser/workspace-system-sources.spec.mjs` covers the
notice's Later and Review scan, a scoped scan from one source into Activity, Add source
with its server check and Save, a refused folder, Remove down to auto-detect, an absent
configured source, the three watch states, the monitoring and worker rows, Refresh
files, the thumbnail dialog (its plan line per scope with the preselected quality, an
invalid size that requests nothing, Start into Activity, Rebuild all, a failed plan and
a refused start), a save refused by a running scan and a failed read. Its hooks are
`tests/browser/workspace_system_fixture.py` (`/__test__/system/*`: reset, injected
failures, an absent source, a deleted linked file, unthumbnailed files, a fake image
monitor) on the
concept-shaped fixture, with delays through the shared request holds; reset also restores
the server's host settings and its `config.json`, which must be the one inside the server's
temp root (`tests/test_workspace_system_fixture.py`).

```sh
python3 -m unittest tests.test_database_health tests.test_system_api tests.test_library \
  tests.test_workspace_system_fixture -v
node --test tests/js/workspace-system-state.test.js tests/js/workspace-system-controls.test.js \
  tests/js/workspace-system-overview.test.js tests/js/workspace-state.test.js \
  tests/js/workspace-system-appearance.test.js tests/js/workspace-preferences.test.js \
  tests/js/workspace-inspection-state.test.js tests/js/workspace-images-state.test.js \
  tests/js/workspace-system-sources.test.js tests/js/workspace-thumbnail-review.test.js
python3 -m unittest tests.test_settings_sources tests.test_scan_scope -v
python3 -m unittest tests.test_maintenance_plans tests.test_api_maintenance -v
npm run --prefix tools/vendor test:workflows -- system-concept.spec.mjs workspace-system.spec.mjs \
  workspace-system-sources.spec.mjs \
  --project chromium --project touch --project firefox --project webkit
```

### Maintenance contract (#249 System S6)

`POST /prompt_manager/maintenance/preview {operation, options:{}}` reviews one
operation and answers a `Plan` (`py/api/maintenance.py`, `database/maintenance.py`):
`duplicate_prompts` (legacy `cleanup_duplicates`: equal text ignoring case and outer
spaces, the oldest prompt of each group kept; a group with a member in a saved pair is
kept whole), `optimize` (VACUUM, with the database size, its free pages and the free
space it needs), `unused_components` (legacy `prune_orphaned_prompts`; the #239
protections are the predicates `ORPHANED_PROMPTS_SQL` is built from, so the plan's
protected reasons are exactly the prune's rules) and `missing_links` (legacy
`cleanup_orphaned_images`, judged as the health check's `missing_files` through
`health.link_states`: only a link under a source that can be read now counts; one under
an unavailable source or outside every source is protected). The operation enum is
strict, `options` must be `{}` and the body is capped at 4 KiB (512 KiB for
`duplicate_files`: 1 MiB, below). A preview meets the
read-only standard above on its own `mode=ro` snapshot (`test_preview_writes_nothing`,
per operation, plus the media bytes) and stats files after the snapshot ended. The
plan lists at most 50 detail rows and no server path. Its fingerprint (the ids, text
hashes, metadata, tags, Library state and pair refs of every prompt involved, or each
link's row and source), its targets and the sources it judged stay in the server's
`PlanStore` under a single-use `secrets.token_urlsafe(32)` token: 15 minutes, at most 8
plans and 16 MiB of payload. A plan over that budget is refused with `too-large`, never
truncated; a new plan drops the oldest ones, which then read as expired; a restart
forgets them all. A plan is bound to the database it read: a restore during the preview
answers `storage-changed`. Errors are coded (`invalid-request`, `too-large`,
`storage-changed`, or a null code with "Failed to review maintenance") and carry no
path or exception text.

`POST /prompt_manager/maintenance/apply {token}` starts the reviewed plan as an
Activity job (202 `{job}`): kind `maintenance`, titled after the concept's row
("Duplicate prompt text", "Optimize database", "Unused prompt components", "Missing-file
links"), admission `records` (it never runs beside a scan, restore, LoRA import or other
record-changing job, legacy-page holds included: `test_apply_refused_while_restore_runs`),
not cancellable, with the groups `changed`, `protected` and `errors`. The token is consumed
on the loop turn the job starts, so a second apply answers 409 `plan-used` with that job;
a refused start (`busy`) keeps it. Other errors: `invalid-request`, `plan-expired` (410:
unknown, expired, or forgotten after a restart) and `storage-changed` (409: a restore
replaced the database since the review; the plan is dropped). The job:

1. takes a safety backup when the plan was reviewed with the policy on or the policy is
   on now (`maintenance.safety_backup` in `config.json`, default `true`; GET/POST
   `/prompt_manager/settings` field `safety_backup` with the revision). The copy is made
   with SQLite's backup API into a temp file beside the database, switched to a
   rollback journal (so a later read-only open leaves no `-wal`/`-shm` beside it),
   verified with `verify_database_file`, fsynced, made `0o600` on POSIX and only then
   renamed with `os.replace` onto `<db>.backup_<YYYYmmdd_HHMMSS_ffffff>`, reserved with
   `O_EXCL` (`_1`, `_2`… when a coarse clock repeats a stamp:
   `test_backup_names_unique_within_one_second`). A backup file on disk is therefore
   always a verified one. Any failure removes what it wrote and ends the job
   `backup-failed` before anything is changed (`test_backup_failure_aborts_before_write`);
2. applies the plan: inside `BEGIN IMMEDIATE` the fingerprint is computed again on the
   write connection and a difference ends the job `plan-changed` with nothing written
   (`test_apply_rejects_changed_target`; a missing link whose file is back or whose source
   went away is never removed). The targets are deleted in batches of 400 ids in that one
   transaction. Missing-file links remove database rows only; every media file's bytes
   are unchanged (`test_missing_links_never_deletes_files`). VACUUM runs outside a
   transaction and binds to its database only;
3. counts the changed and protected records (outcome rows hold root-relative paths or
   prompt ids only), logs the backup by its file name, and prunes safety backups to the
   newest 10. With no backup the log and the status line say "No safety backup".

The legacy restore (`POST /prompt_manager/restore`) takes a backup of any size the disks
have room for, so a real library's safety backup restores: the upload, its staged copy
and the swap (the upload twice in the temporary folder, once more plus the current
database beside the database, added up on one disk, each with the 16 MiB margin) must
fit, under a 64 GiB ceiling (`restore_max_bytes`). A request whose Content-Length does
not fit is refused before anything is written, one without is refused as soon as its
running size does not, both with 507 `insufficient-space` and "Not enough free disk
space to restore a 1.6 GB backup; there is room for about …. Nothing was changed."
(no path); past the ceiling it is the legacy 400 "File too large" with code `too-large`.
The upload streams with `read_chunk`, which aiohttp's `client_max_size` (ComfyUI's
`--max-upload-size`, 100 MB by default) does not cap, and its temp file is closed and
removed on every path (`TestLargeRestores`, a 150 MB backup under a 100 MB app limit).

Safety backups and restore backups are one pool: every `<db>.backup_*` file beside the
database counts toward the same `MAX_SAFETY_BACKUPS` (10), whichever wrote it, and the
pool is pruned after every backup a job took, also when the apply then failed
(`test_safety_backups_share_the_restore_pool`,
`test_the_pool_is_pruned_when_the_apply_fails_after_the_backup`). The legacy
`GET /backup` download writes no file beside the database, so it adds nothing to the
pool. Each plan reports `backup.expected_bytes` (the backup's size, `page_count *
page_size`, or null with the policy off) and `disk: {free_bytes, required_bytes,
sufficient}` for the database folder: the backup needs its size there, and Optimize
needs the database's size twice (the WAL beside it and SQLite's temporary copy, added
up when the temporary folder is on the same disk), each plus a 16 MiB margin. The
temporary folder is the one SQLite really uses (`sqlite_temp_dir`): on Unix
`SQLITE_TMPDIR`, `TMPDIR`, then the first writable of `/var/tmp`, `/usr/tmp` and
`/tmp` (not Python's `tempfile` order, which picks a RAM-backed `/tmp` on many Linux
systems); on Windows `TMP`, `TEMP`, then `USERPROFILE`. Whether a backup is taken is
decided once, when Apply measured the disk, and handed to the job. Apply looks at the
token again with no await before it starts the job, so a token used or expired during
the measurement answers its code, never a 500. A backup is put in place with
`os.link` (atomic, refuses an existing name; reserve-and-replace where a filesystem
has no hard links), and startup removes the `.pm-safety-*.tmp` copies an interrupted
backup left. Optimize runs alone: its job has the `exclusive` admission class (refused
beside any job, and every job is refused while it runs), and its plan says the database
is locked while VACUUM runs and that run counts or captures waiting longer than the
30-second busy timeout are not recorded. The duplicate prompt merge is strict inside
the apply: a group whose copies link one image twice, or whose removed copy has its own
Library title, is protected in the plan, and any merge error rolls the transaction back
(`MergeConflict`); the legacy cleanup keeps its lenient behaviour. Apply
measures again before it starts a job and refuses a short disk up front with 507
`insufficient-space` and a truthful message ("Not enough free disk space: the safety
backup needs about … in the database folder and … is free. Nothing was changed."); no
job starts and the token stays usable
(`test_apply_refuses_up_front_when_the_backup_does_not_fit`,
`test_apply_refuses_up_front_when_vacuum_does_not_fit`).

**Duplicate image files** (`py/api/duplicate_files.py`, `py/api/duplicate_review.py`;
`tests/test_api_duplicate_files.py`). `POST /prompt_manager/jobs/duplicates` starts the
find: kind `duplicates`, "Find duplicate image files", admission `read`, cancellable,
group `groups_found`, over every effective source (the legacy scan's
`_hash_media_groups`: no symlinks, no `thumbnails`, bounded, each file once). The
find lists a physical file once, by `(st_dev, st_ino)`: a source listed twice under two
spellings, a symlinked source inside another one and a hardlink are one file, never a
copy of themselves (`test_a_folder_listed_twice_never_loses_its_only_copy`,
`test_a_symlinked_source_inside_another_is_one_file`,
`test_a_hardlink_is_never_a_copy_to_remove`). One file reached through two mounts is
protected, never guessed at: a removal on another `st_dev` than its kept copy, with the
same size and mtimes within 2 s, is kept as "may be the same file reached through another
mount or share" when its `st_ino` is equal or its path within its source is the same, or
one ends with the other on a whole folder name (sources listed at different depths:
`test_a_deeper_source_on_another_mount_is_protected`)
(NFS and SMB, a loopback mount, a FUSE pool: `TestAnotherMount`). Some true copies on
another disk are therefore never offered. A volume that reports no file id protects
every pair ("Can't confirm this is a separate file on this drive"). The job checks both
before each file's records move, holds the kept copy open, and right before the unlink
checks again that the held kept copy is still the file at its path (`fstat` of the held
handle against `os.stat` of the path) and that the guard still holds; otherwise the file
is kept, its records move back to it, and it counts as not removed
(`test_a_kept_copy_deleted_during_the_move_keeps_the_file`). On Windows the hold also
makes deleting the kept copy itself through another path fail. The server holds the groups (content
hash, root-relative paths, source index, size, mtime and inode) for the last three
finds; `GET /prompt_manager/maintenance/duplicates/{job_id}?offset&limit` pages
them ten at a time (`not-found` 404, `job-running` or `no-groups` 409). The preview
`{operation:"duplicate_files", options:{job_id, keep:[{group_id, root_index, path}]}}`
(body cap 1 MiB, at most 2,000 keep entries; `test_the_keep_limit_fits_the_body_cap`) removes, in each group named, every copy
not kept; a keeper that is not in its group is `invalid-request`, and a source no longer
listed at its index is `sources-changed`. Every keeper and removal is checked with
`file_problem`: containment as text first, with no filesystem access (inside its
source, not under `thumbnails`, a media extension), then `lstat` (a link or any
non-regular file is refused), the resolved path still inside the resolved source, not
the kept copy itself (`os.path.samestat`), and the size, mtime and inode the find saw.
The preview reads no file; the removal job hashes each file and its kept copy again
right before the unlink (`test_preview_reads_no_file_and_spots_a_touched_one`). A removal that fails, or whose kept copies all fail, is
protected with its reason. The plan names each removal, its kept copy and how many
database links move, says the safety backup covers the database and not the files,
and writes nothing. Apply runs `duplicate_cleanup` (records, not cancellable, groups
`removed`, `kept`, `errors`): sources unchanged, the promised safety backup, then per
file the kept copy and the file are checked again (hashes included, and never the same
file), the file's records move to the kept copy in one short `BEGIN IMMEDIATE`
transaction, and only then is the file removed (every hash handle closed first:
`test_handles_closed_before_unlink`), with its thumbnail (a video's `.jpg` too). A crash
or a failed unlink therefore leaves the records on the identical kept copy, and a failed
move leaves the file and its records alone: no row ever names a deleted file
(`test_a_failure_between_two_files_leaves_no_row_on_a_deleted_file`). The status line
counts the files removed and not removed. A changed file is kept (`test_changed_content_skips_file`); the
first failed unlink stops the job, a locked or read-only file with "The file is open in
another program or read-only; it was not deleted, and its links now name the kept copy."
and never the OS text, and the
files after it are listed as not attempted (`test_partial_failure_reports_each_path`).
Records move for the reviewed files only (`database/duplicate_copies.py`): each image
record, looked up under every spelling of the file by containment
(`py/api/duplicate_spellings.py`: each configured, monitored, ComfyUI output or listed
folder whose `os.stat` identity is a folder above the file, plus the path below it, so a
parent spelling of a nested source and a case variant match:
`test_links_under_a_symlinked_source_spelling_move`,
`test_a_parent_spelling_of_a_nested_source`), moves to the kept copy under the same
spelling, or, when its prompt already links the kept copy, hands its saved-pair
links to that record and is deleted; a favorite under any listed root that holds the
file moves with it (`test_nested_sources_subfolder_first_with_a_parent_favorite`)
(`test_db_links_reconciled_only_for_removed_files`); the copy's image tags join the kept
copy's as their union; and its pending Needs review items (still `pending` at revision
0 with no decision row) are settled (`test_tags_move_as_a_union_and_pending_items_settle`).
The plan states each of these. The find refuses while a removal
runs and the removal while a find runs.

The legacy gallery routes keep their shapes on the same kinds: `/scan_duplicates`
holds `duplicates` (read, "Find duplicate image files (gallery page)"),
`/scan_duplicates/stream` starts the find over the gallery's output folder or attaches
to a running one that covers it (409 otherwise), `/scan_duplicates/status` reports the
latest find, and `/delete_duplicate_images` holds `duplicate_cleanup` (records) and is
refused while a find runs (`test_legacy_delete_duplicates_unchanged`). The link
statements are index searches (`test_link_queries_use_the_path_indexes`; the fixture
check is in the PR). Windows: the hash reads close before `os.remove`; a sharing
violation is a `PermissionError` answered with the coded message (native on the
Windows runners, `test_locked_file_gives_a_coded_message`).

A failure the job cannot name ends it "Maintenance failed; nothing was changed." with a
null code and no exception text. Windows: every connection the backup and the previews
open is closed before the temp copy is renamed or removed, and the suites' teardown
asserts no handle under the temp root is still open.

The legacy `POST /prompt_manager/maintenance` keeps its shape. Its `vacuum` result is
`success:false` with the error when VACUUM fails (`vacuum_database` now raises after
logging), and `cleanup_orphaned_images` removes only links under a source that can be
read now (the health check's sources), never every link of an unmounted drive.
`cleanup_missing_images()` without sources keeps the old rule for its other callers.
Query plans on a fixture of 5,100 prompts, 8,000 images, 300 pairs and 200 recorded
runs: every per-prompt protection and fingerprint read is an index search, the
duplicate groups and the link rows are one scan each, and a delete batch (400 ids)
searches by rowid with its cascades by index.

**Workflow storage** (`database/workflow_documents.py`, `database/workflow_storage.py`,
`py/api/workflow_storage.py`; `tests/test_workflow_documents.py`,
`tests/test_workflow_migration.py`, `tests/test_api_workflow_storage.py`). Each distinct
workflow and prompt graph text is one `workflow_documents` row: `sha256` of its exact
UTF-8 bytes (unique), `byte_length` and `data` = `zlib.compress` of those bytes. Image rows
reference it through `workflow_document_id` / `prompt_document_id` (foreign keys, one
index each). The text is stored and hashed exactly as written, never parsed and
re-serialized. Two forms are readable forever: old (text inline, no id) and new (inline
NULL, id set); inline text wins when both are present. Every reader goes through
`read_stored_text` / `resolve_image_row` (image lists, image by id, prompt info for the
inspector and metadata page, diagnostics, pair ingestion's comparison), which also drops
the id keys so payloads keep their shape; a document that fails its length or hash check
reads as unreadable (None), never as other text. Round trips are proven byte for byte
over edge texts (spacing, key order, duplicate keys, number spellings, `NaN`/`Infinity`
tokens, NUL, BOM, astral and combining characters, 500-deep nesting, a 1.2 MB+
workflow, `""` and NULL) and seeded random text and workflows
(`TestRoundTrip`). New writes (`link_image_to_prompt`, pair ingestion for scan, monitor
and Needs review) store the new form. No trigger is installed (restore validators refuse
them): a document goes in the transaction that drops its last reference (image and
prompt deletes, missing-link cleanup, duplicate merges, duplicate-file link merges,
ingestion updates: `TestGarbage`, `TestMoreGarbagePaths`), and startup, the migration's
end and Reclaim space sweep anything a path missed. `verify_database_file` checks the
table definition, the id columns, their foreign keys and indexes, the stored values and
dangling ids (`TestSchemaErrors`); a pre-dedupe file is accepted and migrated on open
and on restore. The `workflow_storage` maintenance plan counts the old-form records
(one pass over record headers) and its apply starts "Store each workflow once"
(`records`, cancellable): first, always and whatever `maintenance.safety_backup` says,
a verified pre-migration backup named `<db>.pre-workflow-storage_<YYYYmmdd_HHMMSS_ffffff>`,
outside the pruned `<db>.backup_*` pool and kept until the user removes it (the preview
reports `backup.policy: "required"`; `test_the_backup_is_taken_even_with_the_policy_off`,
`test_the_backup_is_outside_the_pool_never_pruned_and_restores`); then batches of at most
200 rows, 16 MB of text or 0.5 s, each one `BEGIN IMMEDIATE` transaction found by a rowid
range search and read one row at a time, followed by 0.15 s with the write lock free so
a writer on another connection gets in (`test_a_writer_on_another_connection_gets_in_between_batches`).
Each text is read back through the accessor before its row changes. Each row has its own
savepoint: a row that cannot be rewritten (text SQLite cannot decode, a text that would
not come back) stays inline, is listed in `errors` and named in the job's last line, and
the rest go on, Reclaim space included (`TestRowErrors`); any other failure (a full disk,
an I/O error, a malformed database, a bug) rolls the batch back whole and fails the job
(`TestSystemErrorsFailTheBatch`, `TestSystemErrorsInTheJob`). With nothing to rewrite the
plan says "Nothing to rewrite" and Apply answers 409 `nothing-to-do` with no backup; only
the first apply keeps a `pre-workflow-storage` backup, and a later one (after a Cancel, a
rerun) takes a pooled safety backup (`TestOneKeptBackup`). The job loop's pause is tested
with a second-connection writer (`TestLiveLoopLetsWritersIn`). A reused document is decompressed and checked first and repaired from the text
when damaged, and `verify_database_file` (restore and backup verification) reads every
document and refuses a damaged one (`TestDamagedDocuments`). The legacy table rebuilds
refuse a file that already has the document id columns (`TestRebuildGuards`).
`tools/workflow_text_digests.py <db> <out>` writes the sha256 of every record's stored
texts, so a `diff` of its output before and after the rewrite proves byte identity. It
loads only `database/workflow_documents.py` (no package start-up, no log handler) and
opens the database `mode=ro`, with `immutable=1` when no `-wal`/`-shm` exists, so it
creates none (`TestDigestToolSideEffects`). A damaged document's verify reason names it
and says what to do, and a backup that fails on it says so in the job line. A crash or Cancel leaves
committed batches new form and the rest old form, and the next apply continues
(`test_a_crash_mid_migration_leaves_every_row_readable_and_resumes`,
`test_cancel_stops_after_the_current_batch_and_a_rerun_continues`). Values that are not
text stay inline (protected). "Reclaim space" follows when every row was looked at
(`exclusive`, not cancellable): a disk check for VACUUM's copies of the live pages,
then sweep, VACUUM and `wal_checkpoint(TRUNCATE)`; when another job holds the registry it
does not start and the finished migration names Optimize database. No image or video
file is opened (`test_the_jobs_never_open_a_media_file` tracks every Python open under
the output folder and compares bytes and mtimes). Memory stays bounded by one row plus
the batch's distinct documents (`test_memory_stays_bounded_by_one_row`, tracemalloc).
Restoring a deduped backup on a version before this one is not supported: that
version's restore accepts the file and then reads rewritten images without their
workflow (README changelog).

### LoRA reference import contract (#221, #249 System S10)

The LoRA import is staged (`py/lora_import.py`): discover the LoraManager
`*.metadata.json` files and fingerprint every scanned one, kept or skipped (path, size,
modification time and whether it was kept, #362; a skipped sidecar added, removed or edited
after the review refuses Start with `plan-changed`, `test_start_refused_when_a_skipped_sidecar_changed`); stage each
record (CivitAI example images go into the image cache first, best effort, counted in
`downloaded` / `download_errors`; each image is written to a temp file beside its cache
entry and moved in place, so a half-written image is never seen as cached); the files are
listed and fingerprinted again (a file changed, removed or added while staging aborts with
nothing written); then `PromptDatabase.replace_category_prompts`
(`database/category_import.py`) replaces the `lora-manager` category in one
`BEGIN IMMEDIATE` transaction. Any failure leaves the previous prompts, tags and image
links exactly as they were; images already downloaded stay in the cache and the job says
so. A record whose preparation fails is skipped (legacy); a prompt a saved pair uses
blocks the whole replacement (`PairDependencyError`, nothing deleted); a replacement with
no record over a non-empty collection needs `allow_empty` (a reviewed import), so the
legacy scan refuses it.

`POST /prompt_manager/lora/import/preview` (body `{}`) writes nothing and requests
nothing (`test_preview_writes_nothing_and_makes_no_requests`: DB and `-wal` bytes, `-shm`
size, rows, no cache folder, the downloader never called) and answers the maintenance
`Plan` shape plus `lora_files`, `with_metadata`, `current_imports`, `blocked_by_pairs`,
`planned_downloads` and `network`; a blocked plan has no token. `POST
/prompt_manager/lora/import/apply {token}` starts the `lora_import` job (202): title "LoRA
reference import", admission `records`, cancellable only while it stages, the safety
backup the policy promises before the replace. Refusals: `plan-changed` (409, the files
changed since the review, or the integration was turned off or moved to another install),
`plan-expired`, `plan-used`, `storage-changed`, `busy`,
`insufficient-space`, `apply-failed`. The job fails with `plan-changed` (the files or the
imports changed meanwhile), `blocked-by-pairs`, `nothing-to-import`, `backup-failed` or
`import-failed`, each with nothing written; the job keeps what staging skipped, by name,
whatever happened after. The tests change real files behind a held download
(`test_files_changed_while_staging_fail_the_job`, removed and added files too). The
replace binds the category, not one parameter per prompt, so SQLite's variable limit
never caps a collection. Cached CivitAI images are written to a `.pm-*.part` file in the
same folder and renamed into place, with the umask's mode as a plain save gives; each import
first removes `.pm-*.part` files older than an hour from the image cache and its LoRA
folders (`TestStaleDownloadSweep`). A download starts only at an HTTPS `civitai.com`,
`civitai.red` or `civitai.green` host from the fixed `CIVITAI_HOSTS` list (the key goes
with that first request only); a redirect is followed only over HTTPS to one of those
domains or a subdomain (parsed host name, default port, no credentials), at most three
hops, without the key (`TestCivitaiRedirects`, `TestCivitaiRedAndGreenDomains`). Each
failed download logs one warning naming the URL without its query and a short reason
(HTTP status, refused redirect, hop limit, size cap, decode or save error, timeout or
network error), never the key (`TestDownloadFailureWarnings`). The legacy `POST /lora/scan` runs the same stages under its SSE
event shape; a page that closes its connection stops staging before its next LoRA, and the
scan keeps its records hold until staging has stopped, then ends `cancelled` in Activity
(`test_a_disconnected_legacy_scan_stops_and_holds_until_it_stopped`) (`test_legacy_scan_events_unchanged_on_success`), holds
the registry as "LoRA import (admin page)" and takes no safety backup. Both imports are
`records` jobs, so a restore (`exclusive`, #249 S8) is refused while one runs and they are
refused while a restore runs; the review is a request lease ("LoRA import review"), refused
(409 `busy`) during a restore, and a restore waits for it (`LoraImportRestoreTests`). `POST /lora/enable`
accepts the optional `expected_revision`. LoraManager detection also searches every
`custom_nodes` folder ComfyUI registers through `folder_paths` (#144), main folder first.

**Where LoraManager keeps its files (#355).** `py/lora_manager_layout.py` repeats
LoraManager's rules without importing it (`tests/test_lora_manager_layout.py`). The
settings file is `<LORA_MANAGER_SETTINGS_DIR>/settings.json`, else the plugin's own
`settings.json` when portable mode is on (`LORA_MANAGER_PORTABLE` `1`/`0` decides, other
values are ignored; otherwise the JSON value `use_portable_settings: true`), else
`platformdirs.user_config_dir("ComfyUI-LoRA-Manager")`. It is read as UTF-8, at most 5 MB,
and only seven keys are kept (`SETTINGS_KEYS`); a missing file means LoraManager's
defaults. Discovery: in `alongside` mode every LoRA root is searched and a sidecar is kept
when `<base>.safetensors` exists beside it; in `centralized` mode only the sidecar root is
searched and a sidecar is kept when its `file_path` exists inside a LoRA root (unparseable
ones are skipped by name). `lora_files` counts the metadata files seen, `with_metadata`
the kept, readable LoRAs and `skipped` the rest. Media per LoRA, without repeats: the
preview (`preview_url`, else LoraManager's `PREVIEW_EXTENSIONS` order), then every file in
`<example-images root>[/<library>]/<sha256>/` in natural order; a leading video yields to the
first still. A file counts only with a servable extension and inside the guarded media
roots. Only an absolute `preview_url` is used; a relative one is skipped with a DEBUG line
naming the sidecar. A sidecar that is not valid UTF-8 JSON is skipped like any unreadable
one (#362). CivitAI downloads run only for a LoRA without local media; a record's
images are linked in reverse order, so its first image (the cover) is the newest link and
the card image. The folder guard keeps a folder from the settings, and each of ComfyUI's
default LoRA folders (`models/loras`, `extra_model_paths.yaml`, `folder_paths`), only when
it exists and is neither a filesystem root (`/`, `C:\`, a UNC share root) nor the home
folder or a folder that contains it (realpath + `normcase`); a refused default folder that
exists is listed in the layout's `refused_defaults` and logged as one warning each time the
refused set changes (`RefusedDefaultsTests`). An unusable layout (no install, unreadable /
non-JSON / non-object / oversized settings, no `platformdirs`, a missing or refused
centralized sidecar root, "the sidecar folder is a filesystem root, the home folder or a
folder that contains it") gives a review without a token ("LoraManager settings: not
usable: <reason>. Nothing was changed."), a legacy scan error with that reason, the default
LoRA roots for serving (no example-images root), and trigger words from the default roots
in alongside mode. A folder discovery cannot list answers the review with 500, code
`folder-unreadable` ("A LoRA folder could not be listed (...). Nothing was changed.").
`TriggerWordCache.load` never raises: an `OSError` is a warning, anything else is logged
with its traceback (#362), both keep the previous map, and a running node retries a failed
load only after 60 s (`TRIGGER_RETRY_SECONDS`). `tests/run_suite.py` and `tests/browser/server.py` point
`LORA_MANAGER_SETTINGS_DIR` at a disposable folder, so no test reads the developer's real
LoraManager settings; tests that call `load_layout` inject their own folder too.

**Linked-only gate and the layout cache (#362).** While the integration is on, the LoRA
allow-list (`ImagesMixin._lora_image_dirs`) is the layout's `media_roots` plus the CivitAI
download cache, and `_media_roots(outputs)` appends it after the output folders for Library
previews, the Library prompt detail, the per-prompt images page, `serve_output_image` for a
root past the outputs, `inspect_image` and `reveal_image`. Under such a root only a file the
database records as a linked image is served, inspected or revealed (`_linked_only_error`,
by its given and its real path); anything else answers 403. Library responses mark that
media `linked_only`: no preview file, no favorite, no Tag button. The allow-list's layout
comes from `py/lora_layout_cache.py` (`tests/test_lora_layout_cache.py`, which injects the
clocks, `stat` and `ntpath` and never sleeps): one entry keyed by the stripped configured
path, reused while the plugin settings file's and the settings file's `(st_mtime_ns,
st_size)` (or "missing") are unchanged, the install and every media root still resolve to
themselves (a folder swapped for a symlink reloads and the guard runs again), and the entry
is under `MAX_AGE_SECONDS` (30 s, monotonic clock) old. A load is stored only when no
watched file was modified within `RACY_SECONDS` (3 s) of the load (git's racy rule: a write
during the load, LoraManager's in-place write, FAT/HFS+ coarse times), every watched file
could be checked (any `stat` error but not-found, and an offline Windows share, is "cannot
validate"), the settings were readable, and no `clear()` ran meanwhile (generation
counter). Hits take no lock; concurrent misses each load. A finished import (System job or
legacy scan, both through `replace_lora_imports`) clears it. The integration-off check runs
before it, and the configured-path rule (a configured path that is not an install under
`custom_nodes` adds no folders) on its result; a hit also needs the same home folder, which
the guard compares against. `serve_image` builds the allow-list in the
executor. The cache writes one INFO line, "LoraManager layout changed: <source> settings,
<mode> storage, <n> media folders", when a stored layout differs from the last one logged.
The review, Start, the legacy scan and the trigger cache read the layout fresh. Image route
tests clear the per-process cache in `ImageRouteCoverageCase.setUp`; `write_settings` in
`TestLoraManagerMediaServing` back-dates its file 10 s so the first load is stored.

`tests/browser/workspace_integrations_fixture.py` (`/__test__/integrations/*`) makes a fake
LoraManager install and LoRA folder under the server's temp root, holds or fails the
import, saves a blocking pair and answers CivitAI downloads with a stub opener, so no
test reaches the network.

```sh
python3 -m unittest tests.test_lora_layout_cache tests.test_lora_manager_layout \
  tests.test_lora_import tests.test_api_lora tests.test_lora_database tests.test_lora_utils \
  tests.test_api_images -v
node --test tests/js/workspace-system-integrations.test.js tests/js/prompt-bridge-adapter.test.js
npm run --prefix tools/vendor test:workflows -- workspace-system-integrations.spec.mjs \
  system-integrations-concept.spec.mjs \
  --project chromium --project touch --project firefox --project webkit
```

### Auto-tagging contract (#328, #249 System S11)

System › Auto-tagging (`web/js/workspace/system-tagging.mjs`, D132–D134) edits one
server-side tagging configuration, `autotag.defaults` in config.json: `model_type`
(`wd14-swinv2`, `wd14-vit`, `gguf`, `hf`), `existing` (`skip`, `merge`,
`replace_after_review`), `general_threshold` and `character_threshold` (0–1 in
steps of 0.05, #369),
`template` (non-blank, at most 4,000 characters and 16 KiB) and `suggestion_limit`
(1–100). They are host drafts (`autotag_model_type`, `autotag_existing`,
`autotag_general_threshold`, `autotag_character_threshold`, `autotag_template`,
`autotag_suggestion_limit`) saved by the one Save row through `POST
/prompt_manager/settings` with `expected_revision`; a bad value is refused with a 400
naming the setting by its row label ("General-tag confidence must be between 0 and
1."), nothing is written, the Save row shows that sentence (`invalid-setting`, set by
`settingRefusal` in system.mjs only for a save that carried `autotag_*` keys; every
other section's plain 400 keeps the generic status) and the control is highlighted.
The template's limit counts code points, as Python's `len` does. A hand-edited bad value
falls back to its default on load, one key at a time. Every page shows the thresholds in
steps of 0.05 (the System rows, the Tag image dialogs, the legacy sliders), so a saved
threshold off that grid is refused ("General-tag confidence must be a multiple of
0.05.", `validate_saved_threshold`; `draftProblem` holds back Preview tagging review and
the batch Review with the same sentence) and falls back on load; an accepted one is
stored exactly on the grid (#369). A per-request threshold keeps its own check: `/image-annotations/suggest` and
`/jobs/autotag` take 0–1; the legacy `/autotag/start` passes its options through
`validate_options` and starts the `autotag` job titled "Tag output images (legacy page)";
the legacy `/autotag/single` only requires a number. `GET /autotag/models` reports the
saved values (`wd14_general_threshold`, `wd14_character_threshold`, `suggestion_limit`,
`default_prompt`, `model_type`, `existing`), so the Tag image and Auto-tag selected
dialogs start from them; a preset (the bulk review, Preview tagging review) wins.

`GET /prompt_manager/autotag/models` reads the disk in the executor, never loads or
downloads a model and carries no absolute path: per engine `name`, `label`,
`size_label`, `description`, `size`, `downloaded`, `loaded` (GGUF also `model_exists`,
`mmproj_exists`), plus `model_loaded` and `loaded_model_type`. Opening the section
makes exactly one such read per mount (`entering the section makes no model call
besides status`).

One model gate (`py/model_gate.py`) covers download, unload, both batches, the legacy
single-image tag, Generate suggestions and Auto-tag selected. A refusal is `409
model-busy` with `operation`, the holder's label: the download job's title, "Tag output
images", "Tag output images (legacy page)", "Tagging one image (legacy page)",
"Unloading the tagging model", "Auto-tag selected images", "Generate suggestions", else
"Another tagging operation". `ApiError.operation` keeps it (only on `model-busy`).

- **Download**: `POST /prompt_manager/jobs/model-download {model_type}` starts the
  `model_download` job (202), title "Tagging model download · <label>", admission
  `read`, not cancellable, indeterminate (Activity's generic held view: no Cancel, a
  `<progress>` without a value), holding the gate until it ends. A second download is
  `409 busy`; the legacy SSE route attaches to the same job and keeps its frames.
- **Unload**: `POST /prompt_manager/autotag/unload` answers `{success, message}` or
  `409 model-busy`.
- **Tag output images** (per image since #379; prompt tags and prompt text are never
  written): its targets are every still image the output folders list (the Images
  listing of each root, linked to a prompt or not; videos are not targets; a file under
  two overlapping roots is one target). `POST /prompt_manager/autotag/batch/preview
  {model_type, existing}` writes nothing and opens or stats no image file (it may wait
  for the shared listing walk); it answers `images`, `already_tagged` (images with any
  auto-tagged byte record), `to_tag` (`images − already_tagged` under Skip, `images`
  under Merge; both estimates), `estimate_seconds` (WD14: `to_tag` × 1.6 s on the
  processor or × `WD14_GPU_SECONDS_PER_IMAGE` when ONNX Runtime offers CUDA, see
  `py/tagging_defaults.py`; `null` for GGUF and HF), `estimate_device` (`cpu`, `gpu`,
  or `null`), `suggestion_limit` (the saved count the run uses), `model {type, label,
  size_label, downloaded}`, `existing`, `review_required`, `backup {policy}` and
  `disk`. `POST
  /prompt_manager/jobs/autotag {model_type, existing, options}` (WD14: both thresholds;
  GGUF/HF: `instructions`, the template) starts the cancellable `autotag` job (202),
  title "Tag output images", admission `records`, a safety backup by the policy first.
  The job runs the `image-tags` loop (`_tag_each_file`) on each image: a capture, at
  most `suggestion_limit` model tags, a re-hash, then a union into that image's tags
  that also marks its bytes auto-tagged (`media_image_autotags`, one row per byte
  record; an empty result and an unsupported still are marked too; File changed, File
  not found, Too large to verify, read, inference and tag-limit errors are not). Phases,
  each checked for Cancel: backup, model load, listing ("Listing output images…"), a
  pre-pass that stats each file without opening it (gone: "File not found", over 256
  MiB: "Too large to verify", under Skip a marked current record: "Already
  auto-tagged"; counted with `record_counted`, rows up to the cap, no log line each),
  then the loop ("Processing file {k + n} of {T}", the time left from the model's own
  rate, D56 unchanged). It ends "Tagged N images", "Cancelled. Tags already added were
  kept.", or failed with "Prompt storage was restored; start the job again.", "The
  output folders could not be listed. Try again." or the model's refusal.
  Refusals: `400 review-required` (Replace after review never runs as a batch), `409
  model-unavailable` ("<label> is not downloaded. Download it in System ›
  Auto-tagging."), `507 insufficient-space`, `409 busy`, `409 model-busy`. The section
  toasts `model-busy` with its operation, the other three with the server's sentence,
  `busy` with the running job (D57), anything else "<what> could not start. Try again.".
  Outcomes: Tagged / Unchanged / Skipped / Errors (the `image-tags` descriptor, D111).
  `tests/test_autotag_batch.py` pins it, including two images of one prompt getting
  different tags with no `prompts` or `prompt_tags` row changed; the marks' schema group
  is pinned by `tests/test_autotag_schema.py` (forward-only: one empty `WITHOUT ROWID`
  table, optional in old backups, refused without the annotation group).

- **The legacy pages (#379, PR 2)** write image tags, never prompt tags.
  `POST /autotag/start` (the admin and gallery batch) reads `model_type`, `prompt`,
  `skip_tagged` (Skip or Merge), `keep_in_memory` and the thresholds (else the saved
  ones), checks them with `validate_options` (WD14: both thresholds; GGUF/HF:
  `instructions` = the non-blank `prompt`, else the saved template; the saved
  "Suggestions per image") and starts the `autotag` job "Tag output images (legacy
  page)" (gate owner `legacy-batch`) through the native start, so the safety backup and
  the disk check apply. A second legacy batch answers JSON 409 `{success: false, error:
  "Auto-tagging is already running"}` (also when another legacy start wins the race past
  that check); `SseStream` hands a non-2xx JSON `error` to `onerror` as `serverMessage`,
  which both pages show instead of "Connection lost during auto tagging". Any other refusal (a job in the way: "<title>
  already running", `model-unavailable`, `insufficient-space`, `model-busy`, a bad
  option) is one `{type: "error", status: "error", message}` frame. The stream relays
  the job: the phases as `{type: "progress", progress: 0 | 5 | 8, status: "Taking a
  safety backup…" | "Loading model…" | "Listing output images…"}`, each image as
  `{type: "progress", progress: 10 + progress × 85 / 100, status, processed, tagged,
  skipped}` (at most every 0.5 s; `processed` = Tagged + Unchanged + Errors), and the end
  as `complete` (`processed`, `tagged`, `skipped`, `errors`, `skipped_outside_roots: 0`,
  `status` "Complete" or "No still images found", `model_status`, `model_loaded`),
  `{type: "cancelled", message: "Cancelled. Tags already added were kept."}` or an
  `error` frame with the job's status line. A job quiet for 5 s repeats its last frame,
  and a closed stream cancels the job (the legacy pages have no other stop).
  `POST /autotag/single` takes `existing` (`"skip"` or `"merge"`, default merge). Under
  Skip, an image whose current bytes are auto-tagged answers `{success, already_tagged:
  true, image_tags, prompt_id, filename}` after one stat, without the model gate.
  Otherwise the image is captured and the model runs on the capture under the gate with
  the saved cap; the answer adds `tags`, `sha256` (the captured bytes) and `image_tags`,
  and `service.custom_prompt` is never set. Refusals: `409 model-unavailable`, `413
  too-large`, `404 file-not-found`, `409 file-changed`, `503 busy`, `415 not-a-still`
  (the image is marked), `409 model-busy`. `POST /autotag/apply` takes exactly
  `{image_id, sha256, tags}` or `{path, root_index?, sha256, tags}` (64 KiB), re-hashes
  the file and unions the tags into that image (`add_image_tags`, marked): `{success,
  added_tags, total_tags}`; an empty list only marks the image. A body with `prompt_id`
  is `400` "This page is out of date. Reload it."; a changed file `409 file-changed`
  ("The image changed since its tags were generated."); a full set `409 tag-limit`; a
  restore `409 storage-changed`. The admin review asks each image with Skip first: an
  auto-tagged one shows "Image Already Auto-tagged" / "A tagging model already tagged
  this image. Generate new tags?" with its image tags (Skip, Skip All Tagged, Re-tag asks
  again with Merge); the gallery review always merges. Apply & Next sends the image and
  its `sha256` (linked or not). The batch copy: "Skip images already auto-tagged",
  "Auto tagging complete! Tagged N images." ("1 image"), and under both progress bars
  "Closing this window stops tagging. Tags already added are kept. Prompt Manager's
  Activity page shows the time left.". Pinned by `tests/test_autotag_legacy.py`,
  `tests/js/image-helpers.test.js` and `legacy-autotag.spec`.
- **Tag image dialog marks (DEC4).** A save after Generate suggestions ran on that
  capture marks the image auto-tagged (also a replayed save, and a saved retry after
  `tag-conflict`); a save without Generate does not. The API keeps the suggested capture
  tokens; each new one drops the tokens whose capture is gone (`expires_in`, which never
  resets a capture's idle clock), a release or a saved receipt drops its own, and
  closing the captures clears them (`TestDialogMarks`).

No browser test downloads or runs a model: the injected `TestModel` answers, its
`download_model` raises unless `/__test__/tagging/download {mode: hold | succeed |
fail}` installs a fake that waits on a fixture event, and every tagging reset releases
it. Every workspace-system-tagging test asserts no `/autotag/download/` request.
Changing the routes or `py/config.py` needs a ComfyUI restart; the section itself is
HTML, CSS and JS only.

### Logs and diagnostics contract (#249 System S12)

System › Diagnostics & logs (`web/js/workspace/system-diagnostics.mjs`, D109–D110)
reads and deletes only through the routes below; the legacy admin Logs and Diagnostics
modals use the same routes and keep working.

- **Rule R** (`utils/logging_config.py`): a file may be deleted as a rotated copy only
  when it is a direct entry of the log folder named `prompt_manager.log.<n>` (`<n>` a
  decimal integer ≥ 1 with no leading zero, compared after `normcase`) and a regular
  file that is not a link or junction (`lstat`). Every other entry is `active`, `link`
  or `other` and is never deleted. `/logs/files` rows carry that `kind`; links are listed
  with their own `lstat` size and are not counted or sized by `/logs/stats`. A download
  is served only for a regular, non-link file of the folder (a link, a folder or a name
  not listed is 404), read off the event loop.
- **Reviewed cleanup**: `POST /maintenance/preview {operation: "rotated_logs", options:
  {}}` lists every rotated copy (basenames, size and time) and writes nothing; `POST
  /maintenance/apply {token}` starts the `maintenance` job "Rotated log copies"
  (admission `read`, not cancellable). Under the manager's files lock and every file
  handler's lock it re-scans; when any listed file is missing, no longer rotated or
  differs in size, `mtime_ns` or a non-zero inode, it deletes nothing and fails
  `plan-changed` ("The log files changed since the review; nothing was deleted. Review
  again.", Activity offers Review again). Otherwise it deletes exactly the listed files;
  each is a Changed row ("Deleted · <size>"), a file that cannot be removed an Errors
  row. Two Prompt Manager processes sharing one log folder are not locked against each
  other: each file's identity is checked just before its unlink.
- **Clear all logs** (`POST /logs/truncate`, also the admin page's Clear) empties the
  active log through its handler, deletes the rotated copies (Rule R) and clears the
  buffer; other files and links are kept. Response keys are unchanged. It deletes or
  empties no more than its confirmation showed (#389):
  - `/logs/files` rows carry `identity`, the string `"<size>:<mtime_ns>:<ino>"` (a
    string: `mtime_ns` and many inodes are beyond a browser's exact integers). The
    workspace sends `{"expected": [{name, identity}]}` with the active and rotated rows
    its dialog was built from. Under the files lock and every handler lock each listed
    rotated copy must still match (`_matches_review`) and the listed active log must be
    the same inode, or nothing changes: 409 `logs-changed`. Only listed copies are
    deleted; one that appeared after the confirmation is kept. A malformed list is 400.
  - Without a body (the legacy admin page, whose confirm lists nothing; also the
    workspace when the file list could not be read and the dialog said "deletes its
    rotated copies") every rotated copy present at the locked rescan is deleted.
  - Either way, before anything changes (best effort, not a cross-process lock: a
    write before our own last write, or any while file logging is off here, is not
    seen): a second writer of the active log (our
    handler's flushed stream is behind the file's size, or its file is now a rotated
    copy: another ComfyUI sharing the folder) is 409 `logs-foreign-writer`; an active
    log with more than one name (`st_nlink > 1` by `lstat` or the handler's `fstat`,
    NTFS included) is 409 `logs-hard-link`, never truncated (replacing it instead would
    rename a file our handler holds open, which Windows refuses). Each copy is
    `lstat`-checked again just before its unlink; one that changed stops the clear there
    and keeps it, the copies after it and the active log, which is emptied last.
  - The browser fixture grows the active log through the handler's own stream, since a
    second handle appending to it is exactly what Clear refuses.
- **Runtime settings** (`GET/POST /logs/config`): the level (DEBUG…CRITICAL) and console
  logging apply until the server restarts; the retention values are shown read-only in
  the workspace and the API keeps accepting them.
- **Report** (`GET /system/diagnostics`): built on the server from an explicit list of
  fields (version, Python, platform, Viewer.js version, the four legacy checks, database
  info, source counts, log configuration and statistics, the session's health summary
  and the non-path configuration with `has_civitai_api_key`). Every path is relative to
  a ComfyUI anchor or reads "outside ComfyUI"; a backstop scrub replaces any absolute
  path or the saved key and counts it in `redacted`, which the tests require to be 0. It
  takes a registry lease (409 `busy` while an exclusive job such as a restore runs).
- **Compressed downloads**: the gzip middleware drops a handler's declared
  `Content-Length` before it compresses a body, so the response declares the compressed
  length. A stale length made a browser wait for bytes that never came: the log download
  of any file over 1 KB hung (`test_compressed_body_declares_its_own_length`).

The browser server writes its logs into `<server root>/logs`
(`tests/browser/workspace_diagnostics_fixture.py`), whose hooks seed records with fixed
times, rotated copies, a link (POSIX) and a user's `.bak`, and roll the copies over; no
test reads or deletes a real log folder.

`tests/browser/system-diagnostics-concept.spec.mjs` compares the section and its log
cleanup review with the concept (dark and light, 1600x1100 and 390x844, no page scroll;
both selects show their whole option at 320 wide). The fixture's `{mode: "concept"}` seed
reproduces the concept's Logs sample: only its four buffer lines, a 1.4 MB active log with
three rotated copies and console logging off, so those values are compared as the concept
prints them. `mapDiagnosticsConcept` applies only D109 and D110, and `mapCleanupConcept`
D100 (the shared `mapPlanConcept`) with D109's rule for this plan (no backup box, "Not
needed: no library record changes", "Listed files"). The mutation check asserts that the
unmapped difference is exactly the deviated paths. The comparison found that the Database
line read `database_info.size_bytes`, a key the report does not have, so it always said
"0 bytes"; it reads `database_size_bytes`, the key `get_database_info()` returns.

### Legacy System capability coverage

Every System capability of the legacy admin and gallery pages has an owner (#249 System
S12, #329). Status is `covered` (a native control replaces it), `kept in legacy` (it stays
on the legacy page, which keeps working) or `approved removal` (a ruling retired it). Native
control is `` `<section>` · <role> · "<accessible name>" ``, `<section>` a System hash id
and `<role>` one of button, combobox, switch, slider, spinbutton, textbox, group, status or
term, followed by ` · in "<row title>"` when several rows of the section share the name.
Every `covered` row names one, except the rows in `CONTROL_EXEMPTIONS`
(`tools/vendor/legacy-coverage.mjs`), each with its reason: today only "Gallery per-image
auto-tag / review", which the Images selection bar owns, not a System section. A row that
is not `covered` names none (`—`). Test is the exact title of a test that runs, then its
file. Anchors are line numbers on `main` at this PR.

S13 (#330) removed the legacy copy of the `covered` rows the legacy pages linked to System;
"Legacy System UI removed by S13" below lists every removed element against its row.

`tests/vendor/legacy-coverage.test.mjs` fails on a row with any of these problems:
- no owner, or a status outside the three;
- a malformed Native control cell;
- a covered row without a control (unless exempt), or a control on a row that is not
  covered;
- a test title its file does not declare: `test(` or `it(` (`.only` and `.slow` count),
  or a `def test_` at function or method indentation;
- a title whose test is only declared with `.skip`, `.fixme` or `.fail`.

A title that appears only in a comment or a Python docstring does not count. CI running
those tests green is what proves them. `every covered legacy System capability has its
native control` (workspace-system.spec) opens each covered row's section and finds the
control by role and accessible name, inside its row when the cell names one. The shared
"Review plan" of the per-operation review row and the Sources "Refresh" of the monitoring
list stay unscoped: any row's control proves them.

| Capability | Legacy anchor | Owner | Status | Native control | Test |
| --- | --- | --- | --- | --- | --- |
| Image scan directories add/remove | `admin.html:377–390`; `admin.js:200,1273–1323` | S4 Sources | covered | `sources` · button · "+ Add source" | Add source validates on the server, then marks the host draft dirty; Save persists (workspace-system-sources.spec.mjs) |
| Currently monitoring list + Refresh | `admin.html:392–400`; `admin.js:1032` | S4 Sources | covered | `sources` · button · "Refresh" | watching only with a live observer; restart pending otherwise (workspace-system-sources.spec.mjs) |
| Monitoring enabled | `py/config.py:212,248` | S4 Sources | covered | `sources` · switch · "Watch for new outputs" | Watch for new outputs saves monitoring_enabled; a restart applies it (workspace-system-sources.spec.mjs) |
| Search results auto-hide `result_timeout` | `admin.html:407–412` | S3 Appearance | covered | `appearance` · spinbutton · "Node search auto-hide (seconds)" | host fields round-trip through /settings with revision (workspace-system.spec.mjs) |
| Web UI display mode | `admin.html:414–422` | S3 Appearance | covered | `appearance` · combobox · "Open from ComfyUI" | host fields round-trip through /settings with revision (workspace-system.spec.mjs) |
| Infinite scrolling default | `admin.html:424–430`; `admin.js:689–727` | Legacy prompt list (README continuous browsing); read-only in the report | kept in legacy | — | shouldLoadMore asks for the next page only near the bottom with pages left (list-state.test.js) |
| Worker threads | `admin.html:437–445` | S4 Sources | covered | `sources` · slider · "Worker threads" | worker range max is cpu_count (workspace-system-sources.spec.mjs) |
| LoraManager badge, enable, path, trigger words, CivitAI key, clear key | `admin.html:448–490`; `admin.js:1052–1156` | S10 Integrations | covered | `integrations` · switch · "LoraManager integration" | LoRA rows show detected / enabled / cache-loaded separately (workspace-system-integrations.spec.mjs) |
| LoRA data import with progress | `admin.html:492–498,517–541`; `admin.js:1157` | S10 Integrations | covered | `integrations` · button · "Review import" | Review import shows replacement, blocked items and network effects with the backup box; Start opens the job (D107) (workspace-system-integrations.spec.mjs) |
| Statistics | `admin.html:159`; `admin.js:1627` | S2 Overview | covered | `overview` · button · "Explore" | statistics dialog lists runs by category and library totals (workspace-system.spec.mjs) |
| Export components JSON `/export` | `admin.html:163`; `admin.js:1607`; `py/api/prompts.py:238` | S9 Backup & export | covered | `backups` · button · "Export all prompts JSON" | Export JSON downloads /library/export; secondary buttons download all prompts (/export) and /pairs/export (D103) (workspace-system-backups.spec.mjs) |
| Saved pairs export | `py/api/pairs.py:84` | S9 Backup & export | covered | `backups` · button · "Export saved pairs JSON" | Export JSON downloads /library/export; secondary buttons download all prompts (/export) and /pairs/export (D103) (workspace-system-backups.spec.mjs) |
| Library export | `py/api/library.py:223` | S9 Backup & export | covered | `backups` · button · "Export JSON" | Export JSON downloads /library/export; secondary buttons download all prompts (/export) and /pairs/export (D103) (workspace-system-backups.spec.mjs) |
| Diagnostics run | `admin.html:171,659`; `admin.js:1984` | S12 Diagnostics | covered | `diagnostics` · button · "Run checks" | Run checks opens the shared Health report (workspace-system-diagnostics.spec.mjs) |
| Synthetic image-link test | `admin.html:662`; `admin.js:2101` | S12 Diagnostics | covered | `diagnostics` · button · "Create test link…" | Create test link confirms first and names the prompt (workspace-system-diagnostics.spec.mjs) |
| Maintenance cleanup_duplicates, vacuum, cleanup_orphaned_images, prune_orphaned_prompts | `admin.html:692–748`; `admin.js:1834` | S6/S7 Maintenance | covered | `maintenance` · button · "Review plan" · in "Duplicate prompt text" | Apply reviewed operation closes, toasts, opens the job in Activity (workspace-system-maintenance.spec.mjs) |
| Maintenance check_hash_duplicates, check_consistency | `admin.html:692–748`; `admin.js:1834` | S2 Health report (Maintenance notice, D123) | covered | `maintenance` · button · "Run health checks" | Run health checks opens Health report and updates metric 4 (workspace-system.spec.mjs) |
| Maintenance statistics (database info) | `admin.html:692–748`; `admin.js:1834` | S12 Diagnostics | covered | `diagnostics` · term · "Database" | meta-grid shows real database and source status, the pinned Viewer.js version and the host renderers (workspace-system-diagnostics.spec.mjs) |
| Maintenance select all / clear all / run | `admin.html:754–762`; `admin.js:1822–1833` | S7 per-operation review | covered | `maintenance` · button · "Review plan" | plan dialog shows the server's scope, protected, backup policy and media lines with the backup box (D100); Cancel writes nothing (workspace-system-maintenance.spec.mjs) |
| Prompt cleanup `/cleanup` | `py/api/admin.py:620` | S6/S7 Maintenance | covered | `maintenance` · button · "Review plan" · in "Duplicate prompt text" | test_duplicate_prompts_plan_counts_the_legacy_groups (test_maintenance_plans.py) |
| Database backup | `admin.html:179,824`; `admin.js:2139,2454` | S9 Backup & export | covered | `backups` · button · "Back up" | Back up downloads through the api client and revokes the object URL (D103) (workspace-system-backups.spec.mjs) |
| Restore from file | `admin.html:183,616–642`; `admin.js:2177–2248` | S8/S9 Backup & export | covered | `backups` · button · "Review restore…" | Review restore stages the chosen file and shows the real summary with the backup box (D105) (workspace-system-backups.spec.mjs) |
| Metadata scan with progress, cancel, pill | `admin.html:187,805–865`; `admin.js:2336–2500` | S4 Sources | covered | `sources` · button · "Review scan" | Review scan opens the shared Review metadata scan with all sources (workspace-system-sources.spec.mjs) |
| Rescan-required notice + dismiss | `admin.html:1367–1410`; `admin.js:991–1011` | S4 Sources | covered | `sources` · button · "Later" | notice Later persists the 3.2.0 dismissal (pm_update_notice_dismissed) without scanning (workspace-system-sources.spec.mjs) |
| View logs with level and limit | `admin.html:195,885–907,974`; `admin.js:2579–2641` | S12 Logs | covered | `diagnostics` · combobox · "Level" | level filter is minimum-level over all six choices (workspace-system-diagnostics.spec.mjs) |
| Log statistics | `admin.html:921–941`; `admin.js:2512` | S12 Logs | covered | `diagnostics` · status · "Log statistics" | log statistics show entries, errors, warnings, files and size (workspace-system-diagnostics.spec.mjs) |
| Log files list and download | `admin.html:910,982–985`; `admin.js:2545,2642,2679` | S12 Logs | covered | `diagnostics` · button · "Download log" | each listed file downloads by its returned name and a link has no Download (workspace-system-diagnostics.spec.mjs) |
| Clear/truncate logs | `admin.html:915`; `admin.js:2704` | S12 Logs | covered | `diagnostics` · button · "Clear all logs…" | Clear all logs states both counts, confirms first and reads everything again (workspace-system-diagnostics.spec.mjs) |
| Rotated-log cleanup (new, #329) | — | S12 Logs | covered | `diagnostics` · button · "Review log cleanup" | Review log cleanup lists only rotated copies and keeps the rest (workspace-system-diagnostics.spec.mjs) |
| Set log level (incl. CRITICAL), console logging | `admin.html:948–966`; `admin.js:2530,2729` | S12 Logs | covered | `diagnostics` · combobox · "Runtime log level" | runtime level (including CRITICAL) and console logging save and say they apply until the server restarts (workspace-system-diagnostics.spec.mjs) |
| Log file_logging / max_file_size / backup_count / buffer_size | `py/api/logging_routes.py:56,285–299` | S12 Logs (read-only) | covered | `diagnostics` · group · "Log files & retention" | each listed file downloads by its returned name and a link has no Download (workspace-system-diagnostics.spec.mjs) |
| Auto-tag model list with status | `admin.html:1095–1148`; `admin.js:3641` | S11 Auto-tagging | covered | `tagging` · combobox · "Tagging model" | model options are the four real engines with size and the description shows live status (workspace-system-tagging.spec.mjs) |
| Auto-tag model download | `admin.html:1413–1438`; `admin.js:3680–3747`; `gallery.js:3412` | S11 Auto-tagging | covered | `tagging` · button · "Download" | Download starts the job and Activity shows it without Cancel (workspace-system-tagging.spec.mjs) |
| Unload model | `gallery.js:3480` | S11 Auto-tagging | covered | `tagging` · button · "Unload" | Unload is enabled only while a model is loaded, and a held gate's refusal names the operation (workspace-system-tagging.spec.mjs) |
| Tag template, WD14 thresholds, already-tagged mode | `admin.html:1150–1198`; `admin.js:3748` | S11 Auto-tagging | covered | `tagging` · combobox · "Existing tags" | defaults save through Save preferences and the Tag image dialog uses them (workspace-system-tagging.spec.mjs) |
| Batch auto-tag output images | `admin.html:191,1210,1279–1318`; `admin.js:3748–3840` | S11 Auto-tagging | covered | `tagging` · button · "Review" | Tag output images reviews the images, tags each one and never a prompt (#379) (workspace-system-tagging.spec.mjs) |
| Batch review loop | `admin.html:1206,1218–1364`; `admin.js:3841–4172` | S11 + image-tagging plan | covered | `tagging` · button · "Preview tagging review" | Preview tagging review opens the Tag image dialog for the selected image (workspace-system-tagging.spec.mjs) |
| Gallery per-image auto-tag / review | `gallery.html:601,605`; `gallery.js:3506,3674–3753` | Image-tagging plan (selection bar) | covered | — | Auto-tag selected with Apply automatically runs a job with the chosen model, thresholds and suggestion count (workspace-tagging-bulk.spec.mjs) |
| Gallery thumbnail generation | `gallery.js:1852,1967` | S5/S7 | covered | `sources` · button · "Review options" · in "Thumbnail generation" | Start closes the dialog, toasts, and opens the job in Activity (workspace-system-sources.spec.mjs) |
| Clear server thumbnails | `gallery.js:2175–2192` | S7 Maintenance | covered | `maintenance` · button · "Review options" · in "Thumbnail cache" | Thumbnail cache opens the S5 dialog (workspace-system-maintenance.spec.mjs) |
| Browser cache clear | `gallery.js:2194–2207` | S7 Maintenance | covered | `maintenance` · button · "Review plan" · in "Metadata cache" | Metadata cache reviews and clears only owned browser entries when there are some (D102) (workspace-system-maintenance.spec.mjs) |
| Refresh file listing | `gallery.js:2209` | S4 Sources | covered | `sources` · button · "Refresh" · in "Refresh files" | Refresh files calls /gallery/rescan and toasts its counts (workspace-system-sources.spec.mjs) |
| Duplicate image scan, review, delete | `gallery.js:2234–2577` | S6/S7 Maintenance | covered | `maintenance` · button · "Find duplicates" | Find duplicates starts a job; its view links Review groups; Keep radios build a duplicate_files plan (D101) (workspace-system-maintenance.spec.mjs) |
| Default view mode, image quality | `gallery-settings.js:26–27` | S3 Appearance | covered | `appearance` · combobox · "Default layout" | defaults apply to a new visit but not to the current view or a saved view (workspace-system.spec.mjs) |
| Auto-load metadata, cache metadata, show file paths | `gallery-settings.js:31–33` | S3 Appearance | covered | `appearance` · switch · "Load metadata on selection" | metadata off shows an explicit load and loads once on request (workspace-system.spec.mjs) |
| Video autoplay, mute, loop | `gallery-settings.js:40–42` | S3 Appearance | covered | `appearance` · switch · "Autoplay videos" | video defaults reach the viewer (workspace-system.spec.mjs) |
| Image info on cards; sidebar collapsed | `gallery-settings.js:30,34` | S3 Appearance | covered | `appearance` · switch · "Show image details on cards" | added rows reach their consumers (workspace-system.spec.mjs) |
| Reset gallery settings | `gallery.js:1813` | S3 Appearance | covered | `appearance` · button · "Reset" | reset restores defaults without touching other keys (workspace-system.spec.mjs) |
| Lazy loading | `gallery-settings.js:25` | Legacy gallery (native always lazy) | kept in legacy | — | a legacy gallery Save keeps the workspace keys and every other stored key (gallery-settings.test.js) |
| Results per page / grid columns / paged mode | `gallery-settings.js:28–29,35` | README continuous browsing | approved removal | — | numeric strings from <select> values are accepted and columns are clamped (gallery-settings.test.js) |
| Startup thumbnail prompt | `gallery-settings.js:38–39`; `gallery.js:2578` | README; Overview thumbnail row | approved removal | — | thumbnail row reflects the status endpoint (workspace-system.spec.mjs) |
| Client debug output `debugMode` | `gallery-settings.js:36` | Legacy gallery (S12-10) | kept in legacy | — | a legacy gallery Save keeps the workspace keys and every other stored key (gallery-settings.test.js) |
| API timeout `apiTimeout` | `gallery-settings.js:37` | Legacy gallery (S12-10; read by no request) | kept in legacy | — | defaults are a complete, sane settings object (gallery-settings.test.js) |
| config.json-only keys | `py/config.py:212–234,722–739`; `py/api/diagnostics_report.py:185` | S12 report (read-only) | covered | `diagnostics` · button · "Download diagnostic report" | test_report_lists_config_only_keys (test_api_diagnostics_report.py) |

### Legacy System UI removed by S13

System S13 (#330) points the legacy pages' System buttons at native System (a link that
opens `workspace#/system/<id>` in a new tab) and removes the legacy copy of a capability
only when its row above is `covered`. One row per removed capability: Capability is the
exact Capability cell of its coverage row, Page is `admin` or `gallery`, Removed ids and
Removed methods list what went (`—` for none), and Native test copies that row's Test
cell. An id that stays on a System link is not listed, only its handler. The gallery's
Default View Mode left Page options too: System › Appearance's Default layout is the one
default-layout control. Nothing writes the legacy `defaultViewMode` any more; a stored
value still picks the view the legacy gallery opens in (grid otherwise), and Page options
Save and Reset keep it.

`tests/vendor/legacy-coverage.test.mjs` fails when a row cites a capability that is not
`covered` or copies another test, when an id or method is listed twice for one page, or
when a listed id is still on its page (`id="…"` in the markup or the script, a
`getElementById`/`querySelector('#…')` lookup, an entry of a modal-id list) or a listed
method is still defined or called (`.name(`). A deletion listed nowhere is caught by the
kept-flow tests (`kept admin flows still work` in `legacy-system-links.spec.mjs` and the
legacy page workflows), not by this check.

| Capability | Page | Removed ids | Removed methods | Native test |
| --- | --- | --- | --- | --- |
| Diagnostics run | admin | `#diagnosticsModal`, `#runDiagnosticsBtn`, `#diagnosticsContent` | `showDiagnosticsModal()`, `closeDiagnostics()`, `runDiagnostics()`, `renderDiagnostics()` | Run checks opens the shared Health report (workspace-system-diagnostics.spec.mjs) |
| Synthetic image-link test | admin | `#testImageLinkBtn` | `testImageLink()` | Create test link confirms first and names the prompt (workspace-system-diagnostics.spec.mjs) |
| View logs with level and limit | admin | `#logsModal`, `#logLevel`, `#logLimit`, `#refreshLogsBtn`, `#logsContainer` | `showLogsModal()`, `closeLogs()`, `refreshLogs()`, `displayLogs()` | level filter is minimum-level over all six choices (workspace-system-diagnostics.spec.mjs) |
| Log statistics | admin | `#logStatsTotal`, `#logStatsErrors`, `#logStatsWarnings`, `#logStatsLevel`, `#logStatsSize` | `loadLogStats()`, `formatBytes()` | log statistics show entries, errors, warnings, files and size (workspace-system-diagnostics.spec.mjs) |
| Log files list and download | admin | `#downloadLogsBtn`, `#logFilesList` | `loadLogFiles()`, `downloadLogs()`, `downloadLogFile()` | each listed file downloads by its returned name and a link has no Download (workspace-system-diagnostics.spec.mjs) |
| Clear/truncate logs | admin | `#clearLogsBtn` | `clearLogs()` | Clear all logs states both counts, confirms first and reads everything again (workspace-system-diagnostics.spec.mjs) |
| Set log level (incl. CRITICAL), console logging | admin | `#updateLogConfigBtn`, `#setLogLevel`, `#consoleLogging` | `loadLogConfig()`, `updateLogConfig()` | runtime level (including CRITICAL) and console logging save and say they apply until the server restarts (workspace-system-diagnostics.spec.mjs) |
| Image scan directories add/remove | admin | `#scanPathsList`, `#addScanPathBtn` | `renderScanPaths()`, `addScanPath()`, `removeScanPath()`, `_collectScanPaths()` | Add source validates on the server, then marks the host draft dirty; Save persists (workspace-system-sources.spec.mjs) |
| Currently monitoring list + Refresh | admin | `#refreshMonitoringStatus`, `#monitoringStatus` | `updateMonitoringStatus()` | watching only with a live observer; restart pending otherwise (workspace-system-sources.spec.mjs) |
| Search results auto-hide `result_timeout` | admin | `#resultTimeout` | — | host fields round-trip through /settings with revision (workspace-system.spec.mjs) |
| Web UI display mode | admin | `#webuiDisplayMode` | — | host fields round-trip through /settings with revision (workspace-system.spec.mjs) |
| Worker threads | admin | `#workerThreads`, `#workerThreadsValue` | `renderWorkerThreads()` | worker range max is cpu_count (workspace-system-sources.spec.mjs) |
| LoraManager badge, enable, path, trigger words, CivitAI key, clear key | admin | `#loraDetectionBadge`, `#loraEnabled`, `#loraSettings`, `#loraManagerPath`, `#loraTriggerWords`, `#civitaiApiKey`, `#clearCivitaiKey` | `detectLoraManager()`, `_bindLoraEvents()`, `saveLoraSettings()` | LoRA rows show detected / enabled / cache-loaded separately (workspace-system-integrations.spec.mjs) |
| LoRA data import with progress | admin | `#loraImportModal`, `#loraImportBtn`, `#loraImportStatus`, `#loraImportPercent`, `#loraImportBar`, `#loraImportProcessed`, `#loraImportImported` | `runLoraImport()` | Review import shows replacement, blocked items and network effects with the backup box; Start opens the job (D107) (workspace-system-integrations.spec.mjs) |
| Maintenance cleanup_duplicates, vacuum, cleanup_orphaned_images, prune_orphaned_prompts | admin | `#maintenanceModal`, `#cleanup_duplicates`, `#vacuum`, `#cleanup_orphaned_images`, `#prune_orphaned_prompts`, `#runMaintenanceBtn`, `#maintenanceResults` | `showMaintenanceModal()`, `closeMaintenance()`, `runMaintenance()`, `renderMaintenanceResults()` | Apply reviewed operation closes, toasts, opens the job in Activity (workspace-system-maintenance.spec.mjs) |
| Maintenance check_hash_duplicates, check_consistency | admin | `#check_hash_duplicates`, `#check_consistency` | — | Run health checks opens Health report and updates metric 4 (workspace-system.spec.mjs) |
| Maintenance statistics (database info) | admin | `#statistics` | — | meta-grid shows real database and source status, the pinned Viewer.js version and the host renderers (workspace-system-diagnostics.spec.mjs) |
| Maintenance select all / clear all / run | admin | `#selectAllMaintenanceBtn`, `#clearAllMaintenanceBtn` | `selectAllMaintenance()`, `clearAllMaintenance()` | plan dialog shows the server's scope, protected, backup policy and media lines with the backup box (D100); Cancel writes nothing (workspace-system-maintenance.spec.mjs) |
| Database backup | admin | — | `backupDatabase()` | Back up downloads through the api client and revokes the object URL (D103) (workspace-system-backups.spec.mjs) |
| Restore from file | admin | `#restoreModal`, `#restoreFileInput`, `#confirmRestore`, `#cancelRestore` | `showRestoreModal()`, `confirmRestore()` | Review restore stages the chosen file and shows the real summary with the backup box (D105) (workspace-system-backups.spec.mjs) |
| Gallery thumbnail generation | gallery | `#generateThumbnailsBtn`, `#thumbnailStatus`, `#thumbnailProgress`, `#progressText`, `#progressPercent`, `#cancelThumbnailsBtn`, `#progressBar`, `#progressDetails`, `#progressETA` | `generateThumbnails()`, `generateThumbnailsWithProgress()` | Start closes the dialog, toasts, and opens the job in Activity (workspace-system-sources.spec.mjs) |
| Default view mode, image quality | gallery | `#defaultViewModeSelect`, `#imageQualitySelect` | — | defaults apply to a new visit but not to the current view or a saved view (workspace-system.spec.mjs) |
| Image info on cards; sidebar collapsed | gallery | `#showImageInfoToggle`, `#sidebarCollapsedByDefaultToggle` | — | added rows reach their consumers (workspace-system.spec.mjs) |
| Video autoplay, mute, loop | gallery | `#videoAutoplayToggle`, `#videoMuteToggle`, `#videoLoopToggle` | — | video defaults reach the viewer (workspace-system.spec.mjs) |
| Auto-load metadata, cache metadata, show file paths | gallery | `#autoLoadMetadataToggle`, `#cacheMetadataToggle`, `#showFilePathsToggle` | — | metadata off shows an explicit load and loads once on request (workspace-system.spec.mjs) |
| Browser cache clear | gallery | `#clearCacheBtn` | `clearCache()` | Metadata cache reviews and clears only owned browser entries when there are some (D102) (workspace-system-maintenance.spec.mjs) |
| Clear server thumbnails | gallery | — | — | Thumbnail cache opens the S5 dialog (workspace-system-maintenance.spec.mjs) |
| Refresh file listing | gallery | `#rescanFolderBtn` | `rescanFolder()` | Refresh files calls /gallery/rescan and toasts its counts (workspace-system-sources.spec.mjs) |
| Duplicate image scan, review, delete | gallery | `#scanDuplicatesBtn`, `#dupScanPill`, `#dupScanPillText`, `#duplicatesModal`, `#closeDuplicatesBtn`, `#duplicatesScanStatus`, `#dupProgressText`, `#dupProgressBar`, `#duplicatesContent`, `#duplicatesList`, `#duplicatesFooter`, `#duplicatesCount`, `#selectAllBtn`, `#removeDuplicatesBtn` | `scanDuplicates()`, `attachDuplicateStream()`, `resumeDuplicateScan()`, `applyDuplicateEvent()`, `renderDuplicateProgress()`, `setDuplicateButton()`, `showDuplicatePill()`, `hideDuplicatePill()`, `openDuplicatesModal()`, `showDuplicatesModal()`, `hideDuplicatesModal()`, `displayDuplicates()`, `selectAllDuplicates()`, `removeDuplicates()` | Find duplicates starts a job; its view links Review groups; Keep radios build a duplicate_files plan (D101) (workspace-system-maintenance.spec.mjs) |
| Auto-tag model download | admin | `#autoTagDownloadModal`, `#downloadModelName`, `#downloadStatus`, `#downloadProgressPercent`, `#downloadProgressBar`, `#cancelDownloadBtn` | `downloadModel()`, `cancelDownload()` | Download starts the job and Activity shows it without Cancel (workspace-system-tagging.spec.mjs) |
| Auto-tag model download | gallery | `#autoTagDownloadModal`, `#downloadModelName`, `#downloadStatus`, `#downloadProgressPercent`, `#downloadProgressBar`, `#cancelDownloadBtn` | `downloadModel()`, `cancelDownload()` | Download starts the job and Activity shows it without Cancel (workspace-system-tagging.spec.mjs) |
| Unload model | gallery | — | `unloadModel()` | Unload is enabled only while a model is loaded, and a held gate's refusal names the operation (workspace-system-tagging.spec.mjs) |

### Browser cache ownership (#222)

The legacy gallery's Clear cache used to delete every localStorage key starting `pm_` or
`promptManager_` (the workspace theme and the dismissed update notice among them) and
every CacheStorage cache whose name held "gallery" or "thumbnail" (another tool's, on
ComfyUI's shared origin). `web/js/cache-ownership.js` names what a clear may remove
from an inventory of the real writers (2026-10-24): every key a Prompt Manager page
writes is a preference (`gallerySettings`, `pm_workspace_theme`,
`pm_update_notice_dismissed`) and no page opens CacheStorage or IndexedDB (the
`web/sw.js` service worker that did never shipped). So nothing is removed, and the
toast says "Nothing of Prompt Manager's is cached in this browser." `the inventory covers
every browser storage writer in web/` (`tests/js/cache-ownership.test.js`) fails when a
page writes a key that is neither a preference nor inventoried, writes one it cannot name
(a computed `setItem` key, a bracket or property assignment on a Storage), or opens
CacheStorage or IndexedDB; `the storage scan reports every way of writing browser
storage` pins those patterns. It skips `web/lib` (third-party builds) and the pinned
Toolkit build (`web/js/shared/toolkit`, never hand-edited, verified by `npm run check`;
its storage belongs to the Toolkit repository and a new pin is reviewed on its own). Since
System S13 (#330) the legacy gallery no longer offers Clear cache: System › Maintenance's
Metadata cache and Thumbnail cache reviews own both parts (removal table above), and
`cache-ownership.js` serves the native review.

The duplicates dialog's View Full named its source (`?root=<index>`, with the path
URL-encoded) when the gallery's output folder is a listed source
(`test_view_full_names_the_gallery_folder_source`); S13 removed the legacy dialog, and the
route keeps that behaviour for System › Maintenance's duplicate review.

### Library location contract (#249 System S6b)

`utils/library_location.py` is the one resolver of every data path: the database
(`database/operations.py` `_resolve_db_path`, diagnostics), config.json
(`PromptManagerConfig.get_config_path`, so the `ConfigStore`), the backups (beside the
database), the logs (`utils/logging_config.py`) and the LoRA image cache
(`py/lora_utils.py`). Order: a valid `PROMPT_MANAGER_DATA_DIR` (absolute, never inside
ComfyUI's output, input or temp folder, a public user folder, an extra gallery root or
an image source listed in any config.json the resolver may read; `config_location` and
`resolve_library` read those sources the same way, so config.json and the database
never split; an invalid one is ignored and reported as a problem code), used when
it holds a library or no node-folder library exists; an explicit
`database.default_path` other than `prompts.db` (relative to the node folder, as
before) and `PROMPT_MANAGER_CONFIG_PATH`, honoured as before; ComfyUI's
`user/__promptmanager` (`folder_paths.get_system_user_directory("promptmanager")`, or
`get_user_directory()/__promptmanager`) once its marker `library-location.json` says
`moved`; a node-folder `prompts.db` that was not moved; else `user/__promptmanager`
(new installs; outside ComfyUI the node folder). An empty environment folder never
replaces a node-folder library (`data-dir-empty`). Nothing is moved or copied at
startup (`tests/test_library_location.py`, `tests/test_library_location_wiring.py`).

`POST /prompt_manager/system/library/move` (`py/api/library_location.py`,
`database/library_move.py`) starts "Move library": kind `library_move`, admission
`exclusive`, not cancellable. Refusals answer before any job: 400 `not-movable`,
`same-folder`, `target-invalid`; 409 `target-has-library`, `target-conflict`, `busy`;
507 `insufficient-space` (the free space of the target's volume, for the database, its
backups and config.json plus 16 MiB). The job copies with SQLite's backup API from one
read transaction (counts, document digest and `data_version` of that snapshot) into a
`.pm-move-*` temp file in the target folder (`journal_mode=DELETE`, fsync, 0600),
verifies it (`verify_database_file`, every workflow document, the same rows in every
table, the same documents), copies every `prompts.db.backup_*` and
`prompts.db.pre-workflow-storage_*` (times kept, size and SHA-256 checked), then, under
the old file's `BEGIN IMMEDIATE` and the config lock, rechecks `data_version` (a write
meanwhile copies again, up to three times, then `library-busy`), copies config.json
(0600, checked), writes the marker `pending`, gives every file its final name with
`os.replace` inside the target folder (a different drive is a copy, never a rename),
writes the marker `moved` and switches the process (`switch_process`: the resolver's
override, `PromptModel.repoint_instances` closes every connection of every model of the
old file and points it at the new one, the review and history observers are re-keyed,
the API's plans and health report are dropped). Only then are the old `prompts.db` and
`config.json` renamed `*.moved-to-<folder>` after a `wal_checkpoint(TRUNCATE)`; a file
still open elsewhere is left in place and named (`old-in-use`, `old-locked`); the old
backups stay. A failure before the marker says `moved` removes everything it wrote and
leaves the old library in use, untouched (`TestFailures`); a crash leaves the old
library in use (no or a `pending` marker; the next move replaces its own leftovers) or
the new one (`moved`) (`TestCrashWindows`). `GET /prompt_manager/system/library` and the
Overview's `location` give `{path, source, movable, keep, offer, target, problem,
size_bytes}` with paths relative to ComfyUI; a path in the node folder reads through
the node folder's own unresolved place in custom_nodes (`custom_nodes/promptmanager/
prompts.db` even when that folder is a symlink to a checkout outside ComfyUI; never
absolute, never `..`, else the shared public path, which sources and jobs keep using
unchanged); `POST .../keep {keep}` persists
`library.keep_in_node_folder` through the `ConfigStore`. Errors are coded and name no
path (`tests/test_api_library_location.py`). The real-host `library-location.spec.mjs`
(execution suite, its own fresh host on an OS-chosen port, no configuration) proves a new
install creates `user/__promptmanager/prompts.db` and its logs, adds nothing but byte
code to the node folder, and that `/userdata` serves none of it.

The switch runs while the copy is write-locked too (a guard transaction on it), so a
switch that fails at any point (the resolver, part of the models, the observers) is
undone whole: every model points at the old file again, the copy and its marker are
removed, the old file's connections the partial switch detached are closed (the models
reopen it on next use), and the move fails "… Nothing was moved."
(`TestFailedSwitchIsUndone`). A failed switch can stall up to the 30 s busy timeout when
a writer is blocked on the copy's guard lock; this is not a deadlock, and the writer
then retries on the old file. What
follows a complete switch (dropping plans, the health report, the history read fence,
which is closed first) is best effort: logged, never a failed move. A thread that
opens a connection while the model is being repointed keeps it only if the model's
generation did not change meanwhile (`PromptModel.get_connection`).

Writes racing the switch: a background write that was waiting on the old file's lock
when the switch closed its connection fails, and run counting (`count_runs`,
`count_pair_runs`) and the image monitor's link (`ImageGenerationHandler.
link_image_to_prompt`: the resolved-pair ingest and the plain prompt link)
run it once more through `PromptDatabase.survive_library_switch` when the model's
generation changed during the attempt; the retry opens the new library. The monitor
links through `PromptDatabase.link_image_to_prompt_or_raise`, which raises database
errors (`link_image_to_prompt` keeps its log-and-return-0 contract for other callers);
each attempt is one transaction, so a failed attempt rolls back and a committed one
does not retry, and the image is linked once. `TestBackgroundWritesSurviveTheSwitch`
races a real UPDATE (run counting) and a real plain image link against the lock and
asserts each lands in the new file only. Any other failure is not retried. Known limit: that
blocked writer, and the mover with it, waits out the writer's busy timeout (30 s)
before its connection closes: SQLite holds the connection's mutex during the busy
wait, so neither a close nor `Connection.interrupt()` ends it sooner (measured), and
another thread cannot shorten the timeout of a connection in use. Other writes are
unaffected: threads that ask for a connection after the switch get the new file.
Writes from the HTTP routes (a prompt saved in the dashboard at that moment) are not
retried and report their error.

A restart after a crash between the switch and the rename finishes the rename: the
marker names the old `prompts.db` and `config.json` (`from`, `config_from`), and the API
renames them `*.moved-to-<folder>` at startup when they are still there under their
own names, never deleting them and leaving a file still in use alone
(`TestRestartFinishesTheRename`). A second ComfyUI process using the same node folder
(two installs sharing one custom_nodes folder) is not switched: it keeps writing to the
old file, renamed or not, and those writes are not in the moved library; a rename while
it has the file open is refused as `old-in-use`. The Overview flags a library that stays
in the node folder through an explicit `database.default_path` or a node-folder
config.json (`inside-node-folder`: a reinstall deletes it, CivitAI key included); it is
not offered a move.

Windows: every connection to the old file is closed before its rename (the source
snapshot connection, every model's connections, the checkpoint connection); temp files
are closed before `os.replace`; renames stay inside one folder; folder identity compares
`(st_dev, st_ino)` and `normcase(realpath)`, so case variants and links of a served
folder are refused. The browser test server closes its own log handlers before its temp
root (which now holds the logs) is removed.

### Backup and restore contract (#249 System S8)

A restore is staged, then published (`database/restore_stage.py`). The upload is
streamed to `.pm-restore-upload-*.tmp` and staged in a `.pm-restore-*` folder, both in
the library's own folder (never the system temp folder); free space there must hold
the upload three times (upload, stage, WAL growth) plus the current database
(`restore_room`). The stage checks the upload without reading every workflow document,
copies it with the backup API (rollback-journal and WAL files alike), migrates an older
schema, repairs damaged workflow documents (read one row at a time: a body that inflates
completely while only its recorded hash or only its length disagrees is re-keyed or merged
into the document holding that text; any other damaged body is re-stored from an inline
copy with the same SHA-256, else dropped with its images' references cleared; repaired,
cleared and distinct images are counted), rotates the
history epoch, makes the copy standalone, verifies it with every document read and
records its SHA-256. Publishing rechecks that digest (`StageChanged`), keeps the
current database as `<db>.backup_<YYYYmmdd_HHMMSS_ffffff>[_n]` (`database/safety_backup.py`:
standalone, fsynced, 0600, not verified because the database being replaced may be the
damaged one), copies the stage over the live pages through the backup API (#226: no
file copy) and checkpoints the WAL with TRUNCATE, waiting up to 30 s for readers and
logging one that stays (`tests/test_restore_stage.py`).

`POST /prompt_manager/restore/preview` answers `{token, expires_at, summary, schema,
integrity, documents, live, size, safety_backup}` and writes nothing live; at most two
stages are held (429 `too-large`) for 15 minutes, and a timer removes an expired stage
without another request; `DELETE .../preview/{token}` removes one. A preview that is
refused, fails or is cancelled (even while the worker still stages) removes its stage. The
store of reviewed previews changes only on the event loop; stage folders are removed in
the executor. `POST /prompt_manager/restore/apply {token}` rechecks the live fingerprint (409
`plan-changed`, stage dropped) and starts "Database restore": kind `restore`, admission
`exclusive` (it runs alone, like Move library and Optimize), not cancellable,
indeterminate. Its runner waits for request leases (when they outlast the wait the job
fails `busy` and the review is kept under its token, flagged `scope.review_kept` for the
page that started it), rechecks the fingerprint,
publishes, swaps `self.db`, prunes the pool and logs the safety backup by name, any
document repair and the restored counts; a failed page copy keeps the safety backup and
says so. Once the copy replaced the library the restore succeeded: pruning, reopening
`self.db`, dropping the old library's previews and closing the previous object only log
their failures, in the job and the legacy route alike. The fingerprint covers row counts
and the newest row and edit of prompts, images, pairs, runs and saved views, not every
edit (a tag or favorite change on an older row); those survive in the safety backup. Tokens are single use (409 `plan-used` with the job), expire (410
`plan-expired`) and are bound to the database identity (409 `storage-changed`). The
legacy `/restore` runs the same stage and publish under the same `exclusive` kind with
its old response; a repair adds `workflow_documents` and a sentence to `message`
(`tests/test_restore_plans.py`, `tests/test_api_backup_restore.py`).

Short request-scoped work takes a registry lease (`JobRegistry.lease`): a Needs review
decision, a maintenance preview, the restore preview's read of the live library (not its
upload or staging) and the apply's check. A lease is not
listed, never refuses another lease or a job, and is refused (409 `busy`) while an
`exclusive` job runs; the restore waits for running leases before it switches the file
(`tests/test_job_leases.py`). `GET /prompt_manager/backup` holds `backup` ("Database
backup", `guarded`) only while it writes a standalone copy beside the database, so a
stalled download never blocks other work. A copy that is not SQLite or fails `PRAGMA
integrity_check` is a 500 with no bytes. A copy that fails only a semantic check
(`verify_database_file`, workflow documents not read) is still sent, so a user can always
get a backup: the reason is logged, sent as `X-PromptManager-Verify` (`ok` otherwise) and
recorded as `verified:false`. The file is streamed outside the hold, then closed and
removed. `GET
/prompt_manager/backup/status` lists up to ten `<db>.backup_*` files newest first (name,
bytes, modified time; no paths) and this session's last verified download, or null.

System › Backup & export (`system-backups.mjs`, S9) is the concept's "backups" body on
these routes (D103–D105 above); Overview's Backup status row reads the same status. The
admin page's Backup and Restore are unchanged. `tests/browser/system-backups-concept.spec.mjs`
compares the section, the Restore preview and the Import & inspect dialog with the
concept (both themes, 1600×1100 and 390×844), mapping only D103–D105 (D15 and D55 for the
shared Import dialog); unmapped, exactly those paths differ, and `backup & export
comparison detects mutations` renames a label, removes a row, drops a danger mark and
swaps tokens. `tests/browser/workspace-system-backups.spec.mjs` covers Back up and its
revoked object URL, the status line against the server (none, a safety backup, a failed
read), Overview's row, the restore review against the preview (and that it writes
nothing), Cancel and Escape discarding the stage, a restore through Activity that
reloads the counts and the open Library, a busy refusal reopened and retried, two
refusals then a third review, ×, a section and a route leave discarding, a safety-backup
file name, an invalid file, the three exports and a
failed one, Import / inspect (Rescan while running, Scan for metadata, Inspect), the
safety-backup policy with its revision and a conflict, the schedule, a failed settings
read and the legacy routes. The fixture (`workspace_system_fixture.py`) writes a safety
backup (`/__test__/system/safety-backup`), lists the backups and held stages
(`GET /__test__/system/backups`), and its reset drops held reviews, removes the safety
backups and forgets the last download.

```sh
node --test tests/js/workspace-system-backups.test.js tests/js/workspace-system-overview.test.js
npm run --prefix tools/vendor test:workflows -- workspace-system-backups.spec.mjs \
  system-backups-concept.spec.mjs \
  --project chromium --project touch --project firefox --project webkit
```

## Workspace data fields for the concept pages (#249)

These fields are additive. The legacy `/prompt_manager/images/output` listing (no
`mode=workspace`) and the legacy pages do not change, and existing workspace pages
ignore the new keys.

Workspace image items (`/prompt_manager/images/output?mode=workspace`, and Library
image galleries) gain three card facts. Library list previews carry none of them
and skip the parse.

- `linked: {status, positive_id, title, title_source, category, rating}`. One
  `get_image_link_summaries` SELECT per 200 paths, matched exactly as the
  inspection route matches (exact or normalized `file_path`, no basename
  fallback). `status` is `linked`, `ambiguous` (several owners), `none`, or
  `unavailable` when the database read failed (logged, no error text). Treat
  anything but `linked` as the filename fallback. In pair scope (the image has
  exactly one distinct pair), `positive_id` is the pair's positive, and the title
  comes from that positive's Library title, then its text's first line, never
  from the pair's own title, which names the negative variant (#249 D10/D20). In
  positive scope it comes from the Library title, then the first line. A first line longer than 80 code points is cut to 80 plus `…`, like
  `library.mjs` `title()`. `title_source` is `stored`, `text` or null. `category`
  and `rating` come from the linked positive (D13), not from the pair. The title
  gate is shared with the inspector (`_verified_title`): a `stored` title always
  shows. A `text` title shows only when the file's own positive, parsed from its
  records by the shared parser, gives the same display title. Otherwise the title
  is null on the card and in the inspector header. Videos and unreadable files
  have no parsed positive, so their `text` titles are withheld.
- `seed`: a decimal string, or null. Always a string, because `JSON.parse` rounds
  numbers past 2**53 and ComfyUI's randomized seeds are often that large. It is
  `extract_generation_params` from `utils/generation_params.py` (the inspector's
  legacy parser, #251), run on the `prompt`/`workflow` records read with the
  dimensions. A PNG is read by the inspector's own reader
  (`utils.prompt_inspector.read_inspection_file`): text after IDAT counts, the
  shared byte budgets apply, and a file with conflicting duplicate `prompt` or
  `workflow` chunks gets no card seed, as the inspector refuses it (the card
  keeps its dimensions). Other formats use Pillow's header `info`, as the
  inspector's header read does. The records go through the inspection
  projection first (`utils/inspection_response.project_graph`), which carries
  integers past 2**53 as their exact digits, so the card and the inspector read
  the same digits. The parse runs after the file handle closes. A record over
  2 MiB (UTF-8), a parser error, a recursion error, a placeholder, or a
  non-integral, boolean or non-finite seed gives null. When the prompt graph's
  sampler stages read different seed inputs and the inspector's resolution names
  one sampler for a resolved pair (a detailer or pass-through chain, whose facts
  are the named stage's, D19), the card compares that stage's seed (read by the
  same parser, the other samplers hidden from it) with its own and shows no seed
  only when they differ. A seed that agrees, or one the named stage does not make
  known, is kept, as on main (a saved sampler beside a preview-only one keeps its
  seed; an ambiguous chain, whose facts merge and fill from the parser, keeps it).
  Only such graphs are resolved for the card, with `INSPECTION_PROFILE`
  (`test_a_chain_the_inspector_names_one_stage_of_shows_no_differing_seed`,
  `test_a_seed_that_agrees_with_the_named_stage_is_kept`,
  `test_a_named_ambiguous_chain_keeps_the_parser_seed`; a literal named seed needs no
  second parse, and stages sharing one linked seed skip the check. Measured on a
  200-card page of about 54 KB graphs, half multi-stage with per-stage seeds: 618 ms
  cold against main's 387 ms, about 1.2 ms more per such card, paid once per file
  through the header-facts cache below;
  before this rule a KSampler then FaceDetailer chain with two seeds broke the
  contract below). A card never shows a seed that differs from the inspector's
  (`test_card_seed_equals_inspector_seed_when_both_present`, which `JSON.parse`s
  the listing in Node and compares `String(seed)` with the inspector's seed,
  including one past 2**53). The card shows no seed for A1111 `parameters` files
  or for a BasicGuider custom sampler, where the inspector does.
- Header facts are cached. Dimensions, `seed` and the parsed positive's display
  title are read once per `(path, mtime)` of the current gallery scan and kept
  beside the scan's per-file values. A monitor invalidation or TTL rescan keeps
  the facts of unchanged files and drops the rest, and a roots change drops them
  all. A header that could not be read is not stored. Links and favorites are
  never cached (`test_warm_page_reuses_card_facts_per_scan`: a warm page reads
  and parses no header). Measured cold cost is about 3.4 ms per 50 KB record set
  and 25 ms per 400 KB workflow.
- `favorite: bool`. One favorites SELECT per root per page. A read failure logs a
  warning and shows false.

Filters (`mode=workspace`; anything invalid is 400 "Invalid gallery filters"):

- `q` (at most 500 characters). An entry passes when the case-folded relative path
  contains it, or when its linked positive's text, Library title or a tag name
  contains it (SQLite `LIKE` with escaping, ASCII case folding only). A pair's own
  title names its negative variant, so it is not searched (#249 D12): a variant
  name alone matches nothing. Search does not apply the card title gate, and does
  not search pair tags.
- `category` (exact and case-sensitive, at most 200 characters, stripped; blank
  means none) and `tags` (a comma list, blanks dropped, order-free, AND, at most 30
  tags of at most 200 characters) admit linked images only, through the linked
  positive.
- Ambiguous files never match a linked filter, and they rank as unlinked.
- `sort` is `newest | oldest | name | name-desc | rating`. `rating` orders by
  rating 5 to 1, then linked but unrated, then unlinked and ambiguous; ties break
  newest first. A signed cursor covers every filter and the sort, so reusing it
  with a change is 400.
- One link read (`get_image_link_matches`) serves the filters, the rating sort
  and the count. SQLite returns its rows as one JSON value in one step (#354):
  Python's `sqlite3` releases and retakes the GIL around every step, and while
  ComfyUI generates each retake can wait on the busy thread, so a read that
  stepped once per linked row took seconds. The result keeps only the listed
  paths; peak memory grows with the database's matching linked rows (about
  530 bytes each, freed on return). When the JSON step fails (a SQLite without
  JSON functions, a BLOB value, a JSON text over SQLite's length limit) the read
  streams the rows as before and logs one warning per process; a missing JSON
  function stops the attempt for the rest of the process. An interrupted read
  (Move library) raises as before. `tests/test_database_link_matches.py` proves both reads
  equal on every filter and on odd values.

`positive_count` is the number of distinct linked positives over the full matching
set, not the page:

- Present on every first page, and on every page of a filtered (`q`, `category`,
  `tags`) or `sort=rating` listing.
- Absent on a plain listing's cursor pages, which read no links. The frontend
  keeps the first page's value.
- Null when a plain first page's link read failed. The listing still returns 200,
  and consumers then hide "· K positive prompts". A `q` search alone also
  survives the failure: it matches filenames only, as before linked search, and
  every page's count is null (logged with the traceback). A failure under
  `category`, `tags` or the rating sort stays a safe 500.

Library parents (list, detail and command receipts) gain `run_count` (an int; the
D23 migration estimate for rows from before 3.2.4) and `last_used_at`, a UTC ISO
string. A naive value reads as UTC, and an unreadable or out-of-range value is
null. The edit token ignores both. `variant_total` has two meanings. On the list
response it counts the active variants over all filtered parents (the same
snapshot as `total`, the same on every cursor page). On the detail response it
counts one positive's variants in the chosen archive scope. The Library caption
reads it from the list state. Each detail variant (active and archived) also carries
its pair's `run_count` (`prompt_pairs.run_count`, 0 when null), which the history lanes
show as "N runs" (#249 F2).

Favorites store (D7 = A): `PUT` and `DELETE /prompt_manager/images/favorite` with
`{root_index, path}`. Both are idempotent and answer
`{success, favorite, root_index, path}`. The boundary returns 400 for an unknown
root or a malformed body, and 403 for traversal, an absolute or drive path, NUL, a
non-media name, or a key over the stored bound. PUT needs a listed file (404
otherwise). DELETE also clears a path that is no longer listed. Send the DELETE
body as `application/json` (`api.del(url, {body})` in the shared API client).
The Images card star (#249 C5) toggles optimistically, allows one request per file
at a time, and on any failure restores the previous state and announces it in the
shared toast. The browser fixtures clear `image_favorites` on reset, and the
concept fixture stars the concept's favorites (each other prompt's first image).

- The table is `image_favorites(root_key, relative_path, created_at)`. `root_key`
  is the root's real path (`os.path.realpath`), case-normalized with `/`
  separators. It is stored server-side only and never returned. Folder labels are
  not unique across roots, so they are not used as keys.
- `relative_path` is `normcase(path)` with `/`. On Windows a case-only rename
  keeps the star. On POSIX the key is the exact spelling, so `A.png` and `a.png`
  stay separate (tests swap in `ntpath` through `py.api.images._path_flavor`).
  Moving the install or changing a root's real path orphans its rows, which are
  harmless and never shown.
- Schema validation (`image_favorites_schema_error`) runs at startup and on
  restore. Old backups without the table restore, and backup plus restore keeps
  WAL-resident favorites and closes every handle (`tests/test_database_backup.py`).

Image categories (#332, store and routes): an image file may carry its own
category; without one it shows the category its link supplies.

- The table is `image_categories(root_key, relative_path, category, updated_at)`
  with primary key `(root_key, relative_path)` and the index
  `idx_image_categories_category`. `migrate_image_categories` creates both empty
  inside a savepoint, with no backfill. It reads only `sqlite_master` and its own
  table's PRAGMAs, and writes only its two objects. `tests/test_image_categories.py`
  checks this with an SQLite authorizer on a 23,000-link library, and checks that
  `prompts` and `prompt_pairs` `(id, category, updated_at)` are byte-for-byte equal
  afterwards.
- The **file key** is `image_category_key(sources, absolute path)` in
  `py/api/images.py`: the deepest output source that contains the path (compared
  lexically, never resolving the path, so a symlinked subfolder keeps its listed
  spelling) and the path below it, keyed with the favorites helpers. A source
  matches by its real path or its configured spelling (a detected output folder
  can be a symlink). Without nested sources it equals a favorite's key. Every write and read uses it. A path
  that no source contains has no key, so it has no own category.
- `image_categories_schema_error` checks the structure at startup (a value saved
  under older rules never stops the library opening; a table without its index,
  which this code never leaves, is repaired by the install). Restore also checks the
  values: text columns, keys within the favorites bound, `category` 1 to 100
  characters with no character below U+0020 and no leading or trailing space, and
  `updated_at` of at most 64 bytes. Backups made before #332 restore without the
  table; a migrated restore stage must have it (`require_library_state`).
- `PUT /prompt_manager/images/category` `{items, category}` answers
  `{success, category, updated, unchanged, not_found}`.
  `DELETE /prompt_manager/images/category` `{items}` answers `{success, cleared}`.
  `GET /prompt_manager/images/categories` answers `{success, categories}`: the
  sorted distinct own values whose file is in some source's listing.
- `items` are 1 to 500 Images targets `{root_index, root_scope, path}`, checked by
  `py/api/media_target.py` (shared with Collections, #331). Refusals:
  - 400: a malformed body, a field other than `items` and `category`, a
    `root_index` outside the output sources (a LoRA folder), or a category that is
    blank after stripping, over 100 characters, or holds a control character;
  - 403: traversal, a drive, a backslash, a non-media name ("Only media files can
    have a category") or a key over the stored bound;
  - 409 `source-changed`: a stale `root_scope`;
  - 413: a body over 1 MiB.
  Any invalid item fails the whole request before a write. Targets that fold to one
  key are written and counted once. Instead of `items` a request may send `filter`,
  the Images listing's own query ("Select all N matching", #392, keys as in
  `parse_listing_filter`), resolved now: over 500 matches is a 400
  `too-many-images` ("Select at most 500 images."), a renumbered source a 409
  `source-changed`.
- PUT writes only files the source's listing holds and counts the rest in
  `not_found`; when none is listed it answers 404 `files-not-found` and writes
  nothing. A file that already has the value counts as `unchanged` and keeps its
  `updated_at`. DELETE accepts any well-formed target, so a missing file's
  category can still be removed. An unexpected failure is a logged 500 ("The
  category could not be changed." / "Image categories could not be read.") without
  the exception text. The routes never read or write `prompts`, `prompt_pairs` or
  `generated_images`; `POST /prompt_manager/bulk/category` is unchanged.
- Duplicate cleanup (System › Maintenance): the kept copy's own category wins. A
  kept copy without one receives the removed copy's (`COPY_SQL`, part of the
  cleanup's query-plan check). The removed copy's row is never deleted, so a
  give-back (the kept copy changed) and a failed delete lose nothing. The job log
  says "Kept N image categories on the kept copies".
  `tests/test_duplicate_image_categories.py` covers nested sources in both orders.
- A moved, renamed or deleted file's row stays and is hidden; a file that later
  appears at the same path shows it. The real-host check is the last test in
  `tests/comfyui/image-attribution.spec.mjs`: PUT, list, DELETE on a generated
  output.

Image annotations schema (#249, `database/annotation_schema.py`): the group
`media_annotation_metadata` (one row: schema version, `image_index_revision`),
`media_annotation_files` (one private locator per physical file,
`normcase(join(realpath(root), relative))`, server-side only),
`media_annotation_bytes` (one record per observed whole-file SHA-256 with the
`size`/`mtime_ns` verified when those bytes were last seen and the set's
`tag_revision`) and `media_image_tags` (the ordered tag set of one byte record,
joined into the global `tags` registry). It installs like favorites: optional in
old backups, required once current (`annotation_schema_error`, the single
`require_library_state` flag), validated read-only before and after a SAVEPOINT
install and on restore, never backfilled and never stat-ing media
(`tests/test_annotation_schema.py`). Writers (`database/annotations.py`) always
supply the UUID ids, run in `_pair_transaction` fenced by the storage epoch, and
replace or union one byte record's set (`tag-conflict` / `tag-limit` with the
current set, identical retries `replayed`); vocabulary rename, merge and delete
carry `media_image_tags` through the one `_move_tag_links` loop, and tag rows,
impact and command receipts report `image_count` / `images` /
`images_changed` as files (`tests/test_annotations.py`,
`tests/test_pair_tag_maintenance.py`, `tests/test_vocabulary.py`). Cheap reads
(spec amendment S1) use a byte record only while the file's current
`(size, mtime_ns)` equals its verified fingerprint; an in-place rewrite that
preserves both keeps the old set matching until the file is next observed. The
Images Tags filter (a file matches when every tag is on its linked positive or
its current image tag set) and `q` search find their candidates through the tag
index (#379), never by streaming every image tag row: `image_tag_ids` reads the
tag names that tag at least one image (an `EXISTS` on `idx_media_image_tags_tag`)
and keeps a name when it equals a Tags filter tag exactly or contains `q`
case-folded; `image_tag_locators` reads the files carrying those ids (`tag_id IN`,
500 ids a chunk). Only the listed ones among them are stat'ed, and only their
current records count; a linked positive's prompt tags still combine with the
image tags under a Tags filter. `test_indexed_candidates_equal_the_full_stream`
compares the lookup with the old full stream (kept in the test as the reference)
and `test_tag_search_reads_image_tags_only_by_tag_id` traces every statement; the

inspector's `image_tags` facet (`current` / `none` / `unavailable`) uses the
inspection's own stable stat (`tests/test_api_workspace_images.py`
`TestWorkspaceImageTags`, `tests/test_api_prompt_inspector.py`). The
browser fixtures clear `media_image_tags`, `media_annotation_bytes` and
`media_annotation_files` on reset and never the metadata row.

Review queue schema (#249, `database/review_schema.py`,
`tests/test_review_schema.py`): the group `media_review_metadata` (one row:
schema version, `review_index_revision`), `media_review_items` (one ambiguous
resolver result per `(byte_id, resolver_version, evidence_hash)`, citing an
annotation byte record `ON DELETE RESTRICT`, with the file's recorded
`spelled_path` (the locator is case-folded on Windows, a listing target is
not), 0..16 candidates, `limited`,
`state` pending/selected/unresolved and a revision) and `media_review_decisions`
(one row per item decision revision; `selected` exactly when a
`candidate_hash` is present; `pair_id`/`image_id` navigation is set to NULL,
never cascaded, when the pair or image is deleted, through
`idx_media_review_decisions_pair`/`_image`). Evidence is canonical JSON
(`canonical_evidence`: one object, sorted keys, no whitespace, ASCII only, no
NaN/infinity, at most 8 MiB) and `evidence_hash` is its SHA-256. The group is
valid only beside a valid annotation group (a review group without annotation
tables is refused, catalog only, before the annotation install), installs like
the annotation group (optional in old backups, required once current under
`require_library_state`, SAVEPOINT install validated before and after), and
validation re-canonicalizes every evidence document in one streamed pass. A
startup runs that pass once (`test_startup_validates_stored_evidence_once`; the
install trusts the annotation group it has just validated, and the SQL row
predicates check type and size only, because the streamed pass proves the
rest): 2.5 s to open a database of 40,000 items with 4 KiB of evidence each,
was 7.7 s with two passes. Restore and `verify_database_file` keep the full
validation, annotation group included. The browser fixtures clear
`media_review_decisions` and `media_review_items` before the annotation tables,
never the metadata row.

Needs review items (#249, `utils/review_capture.py`, `database/review.py`,
`tests/test_review.py`, `tests/test_review_outcome.py`,
`tests/test_review_choosable.py`): a file is queued only when its final outcome
is ambiguous and unlinked and there is something to choose. Only scans produce
items; the monitor links new outputs and clears pending items it resolves
(maintainer rulings, 2026-10-23). The output scan (Activity's
Scan for metadata and the legacy `/scan`) hands `record_ambiguous_file` a file
the ingestion resolver finds `ambiguous` only when the browser-parser fallback
then links nothing (status `ambiguous`) and no saved row links the file
already; an ambiguous file a saved row links is reported `unchanged` ("Already
linked to its prompt"), not Needs review. A file the fallback links, an exact
pair the ingester stores, and `conflict` and `unresolved` files are not
queued. Neither is an ambiguous file with nothing to choose: fewer than two of
its candidates are `resolved` with non-blank positive text (`reviewable`). With
none the file could not be traced; with exactly one, the untraced branches may
still have made the file, so that text is not certain either. Both report
`unresolved` ("could not be traced"), are never linked, and their pending,
undecided items whose own evidence offers no choice (a limited item counts as
a choice when it holds at least two candidates) leave the queue on the next
scan (`settle_untraced_review_items`). The maintainer's
second live check found 39 items whose 11 candidates were all "unresolved".
Inspection-profile choices (`tests/test_review_inspection.py`, ruling a): when
the default profile is ambiguous without a choice and nothing links the file,
the scan (never the monitor) resolves it again with `INSPECTION_PROFILE`, which
knows `TextEncodeQwenImageEdit`, `StringConstantMultiline`, `JoinStringMulti`
and the rest of `web/js/inspection-semantics.json`. With at least two choosable
candidates there, the file is `ambiguous` and is queued from that profile's
evidence, which adds `profile: "inspection"` and `semantics`: the SHA-256 of
`class_projection`, the table restricted to the graph's node classes plus its
class-independent `schema` and `stage_inputs` (the resolver looks the table up
only by a graph node's class). The item's `resolver_version` is
`inspection_review_version(metadata)`, 2^40 plus 40 bits of SHA-256 over the
profile with its budgets, `RESOLVER_VERSION`, `INSPECTION_RESOLVER_VERSION`
(bump it when the inspection profile would resolve the same metadata otherwise)
and that fingerprint. A table edit about the
graph's classes behaves like a resolver change (I2); an edit about other
classes changes nothing. One file never holds two pending items for the same
bytes: a new version's item supersedes the byte record's other items when all
are pending and undecided (revision 0, no decision row, the self-heal's safe
delete); beside a decided or advanced item nothing new is stored, and the
rescan's stat shortcut then skips those bytes under any version. A pending item
stays applicable while the bytes still offer its chosen candidate.
Apply re-resolves the capture with the profile the item recorded and re-checks
the chosen hash; the link goes through the same compatible-only ingestion. The
inspection profile never links on its own, and ingestion keeps the default
profile (D1). The heavier profile runs only for files already ambiguous without
a choice, and once per file state: the capture reuses the scan's result when
it holds the same records; a rescan skips files already queued or decided at
their current size and `mtime_ns` under their version, and files this process
already found untraced at the same state (an in-memory map of at most 65,536
files, oldest forgotten first, keyed by 16-byte digests: 8.6 MiB measured at
the cap). A file whose inspection raises is logged, counted in
`review_errors` and reported `unresolved`; the scan goes on. An older version's
pending, undecided item of bytes now untraced leaves the queue
(`settle_stale_review_items`), so an item never stays stuck at 409.
Saver anchor (#390, `tests/test_review_saver_anchor.py`): the scan and Apply
resolve with the file's name as the workspace inspector does, so the one saver
whose `filename_prefix` names the file anchors its branch. A file is never both
unlinked and unqueued (maintainer ruling): when only that anchor resolves it
(the named inspection run resolves, the unnamed one does not) and neither the
default profile nor the parser floor links it, the scan queues it with the
anchor's pair as its one candidate (evidence `anchor: "saver"`; the page
preselects it), never linking it. Such an item counts as a choice for the
untraced settle. Files already linked or resolved by the default path are
unchanged. No match, several matches or an unfinished walk anchor nothing, and
the file is queued as before. `REVIEW_RULE_VERSION` is folded into inspection
item versions, so older inspection items are recorded again once. Apply accepts
the anchored pair and refuses a choice the anchor contradicts. Measured on the
maintainer's 40,890 outputs: the queue is unchanged (249 files); the 242
`gpu_unlock_bench` files the inspector resolves are linked by the parser
floor. The 16-candidate limited
rule and the 8 MiB bound apply unchanged. The queue heals itself: after each batch, the scan removes the pending
items of every listed file (read or skipped as known) that a saved row now
links, and the monitor does the same for each ambiguous output it links
(`settle_linked_review_items`; the review index revision moves). Only an
undecided item goes (`pending`, revision 0, no decision row): a decision is
never deleted or changed, so no schema state was added. The monitor never
records an item (only scans add items, maintainer ruling 2026-10-23): an
unlinked file passed to `record_review_file` is `skipped` with no capture,
and only a linked one's pending items are settled
(`test_monitor_never_records_an_unlinked_ambiguous_output`). The
maintainer's live check of #284/#288 found the queue at 10,232 against a review
group of 4,677: every fallback-linked ambiguous file had been queued. It captures
the file (scope `needs-review`), reads its metadata again from the capture,
resolves it with the default profile and stores only an ambiguous result,
bound to the capture's byte record (created or refreshed like an observe) and
fenced by the capture's storage epoch. Evidence is the resolver reason plus one
`{sampler_id, state, reason, positive, negative, hash}` per selectable
candidate (nested ambiguous stages flattened, duplicates dropped; each side
`{text, storage_text, provenance}`; `hash` = SHA-256 of the canonical candidate
without it). More than 16 candidates, or evidence over 8 MiB, is a `limited`
item without candidates. Recording is idempotent per `(byte_id,
resolver_version, evidence_hash)` and never touches an existing item or its
decisions; changed bytes add a new pending item and keep the old one. The
capture is released before the write; no model is loaded and no pixel is
decoded. Scan statuses, counters, outcome rows and the legacy SSE events are
unchanged apart from the already-linked and nothing-to-choose cases above; the job's `scope` gains
`review_items` (this scan's files that are in the pending queue after it, new
or already there: the review group's ambiguous rows, less files a capture could
not read or a decision already settled), `review_added` (new items),
`review_settled` (items removed because their file is linked now),
`review_untraced` (items removed because they offer nothing to choose) and
`review_errors`. The log ends with `? N images added to Needs review`,
`? N images already in Needs review`, `= N images left Needs review · now
linked`, `= N images left Needs review · nothing to choose` and `! N images could not be added to Needs review` (failures, e.g.
capture capacity), each only when N > 0. Recording captures in the background: it never takes the store's
last worker or capture slot, which stay free for an interactive Tag dialog or
Apply, and waits up to 2 s for room before counting the file as not added
(`test_background_recording_leaves_a_worker_for_the_tag_dialog`). The monitor's
pass runs after its own link, best effort: a failure is logged and the link
stays. Recording follows the registry's `records`
admission (#289): the scan's recording runs under its own `records` job, and
the monitor's pass (one file, not a job, which only settles) is refused
while a `records` or `guarded` job or hold runs, except thumbnail work, which
writes no records (S5 M7). It then writes nothing, logs that the file waits
for the next scan, and answers `deferred`; the next "new" scan reads the ambiguous file
again (`test_monitor_recording_is_refused_beside_record_changing_jobs`). A new item moves `review_index_revision`. A
rescan does not copy or hash an ambiguous file whose current size and
`mtime_ns` equal the verified fingerprint of bytes an item already cites (a
stat compare); the file still stays in the default "new" scope, because it is
not a known image. The skip and a duplicate insert record the current spelling
of the locator on its items (`refresh_review_spelling`, the review index
revision moves, item revisions do not), so a case-only rename on Windows or
macOS keeps its item applicable. Residual risks of the skip, like S1's: an
in-place rewrite that keeps both size and `mtime_ns` is never re-queued (Apply
still re-hashes, so it cannot link those bytes), and a file that is deleted or
moved leaves its pending item counted in `total` (its target then formats as
`unavailable` or fails to open, and Leave unresolved clears it).

Needs review decisions and queue reads (#249, `py/api/review.py`,
`database/review.py`, `database/review_decisions.py`,
`tests/test_api_review.py`): `GET /prompt_manager/review/items?limit=1..40&cursor=`
answers `{success, library_epoch, total, items, next_cursor}` with pending items
oldest first (each item adds the card's `url`, the Images listing's
`/prompt_manager/images/serve/<path>?root=<i>&root_scope=<scope>` formatted without a
stat, null when the file is under no current root, and `is_video` from its extension); `total` is the exact pending count. The cursor is an HMAC keyset
on `(created_at, id)` bound to the storage epoch only (ruling M3, so "load more"
keeps working while the monitor adds an item per generation): a malformed one
is 400, a well-formed one this process cannot verify (tampered, or opened
before a ComfyUI restart: the secret is per process) is 409 `review-conflict`
with the current `total`, and one from before a restore is 409
`storage-changed`. A decision or a new item leaves an open cursor valid: a
decided item drops out of later pages and new items (stamped `utc_now_iso()`,
microseconds) append after it. Residual risk: an item recorded after a wall
clock step backwards sorts before an open cursor and shows only on reload.
Targets are formatted from `spelled_path` against the current roots (the
roots' real paths are the only file access); a file under no current root is
`unavailable` with a null target. Every read and decision lists the roots and
resolves real paths on a worker thread, never on the event loop. `GET .../items/{id}` adds `candidates`
(`hash`, `sampler_id`, `state` resolved/unresolved, `reason`, `label` "<positive
title> / <negative title>" from the saved prompt's Library title else the
first line, and the role texts), `dependency_fingerprint` (SHA-256 of the
file's legacy rows under every spelling of the same physical file, positive
hashes and pair links with their negative hashes, as the ingester reads them) and `sha256`; a detail over 8 MiB, or a
limited item, has no candidates. `POST .../items/{id}/decision
{expected_epoch, expected_revision, capture_token, sha256, dependency_fingerprint,
choice}` with a candidate hash (Apply) requires a capture of the item's file
whose bytes are the item's, re-hashes the current file (`file-changed`
otherwise, also for a fresh capture of new bytes), re-reads the capture and
requires that its bytes are still ambiguous and still offer the chosen
candidate (`review-conflict` otherwise; a resolver change that rewords the
reason or reorders candidates leaves the item applicable, and
`RESOLVER_VERSION` in `utils/prompt_pair_resolver.py` is the one version
ingestion provenance and review share), then in one `_pair_transaction`
checks the epoch, the item revision and state (`review-conflict` with the
current item for a repeat), the re-hashed digest, the dependency fingerprint
and the public ingester's compatible-only rules through
`_ingest_resolved_pair_in` (any refusal is 409 `association-conflict` with
zero writes, explained inline; Apply never reparents a link). Physical
identity: the roots are real paths while the monitor links under the
directory it watches, so a symlinked or junctioned root, `subst` or a mapped
drive spells the same file otherwise. Every legacy row whose spelling ends with
the file's name (the link summaries' unindexed suffix lookup) is resolved by
`normcase(realpath)` against the locator, else `samefile`; two rows of the
file refuse the choice, one is enriched under the spelling it was linked
with, and a same-name row the request did not classify is a changed
association (`TestPhysicalIdentity`, symlinked root and a portable fake
junction), and writes
the link with `kind:"user-selection"` provenance (decision id, reviewed digest,
candidate hash, resolver version, role provenance), the decision row, the
item's new state and revision and `review_index_revision`. `"unresolved"`
records the decision only and accepts `capture_token: null`. A successful
decision releases its capture. No decision changes run or usage counts or
calls an execution writer; a Tag save never changes a review item. Errors carry
fixed sentences: `review-failed` / `decision-failed` 500s never echo exception
text or paths.

Capture store (#249, `utils/media_capture.py`, `tests/test_media_capture.py`):
one explicit action captures one rooted media file into an owner-only
`prompt-manager-captures-*` temp directory, streaming it in 1 MiB chunks while
hashing. Identity is compared like with like: the opened descriptor against
the path only on the API-stable facts (`size`, `mtime_ns`); the full identity
(`size`, `mtime_ns`, `ctime_ns`, `dev`/`ino` where meaningful) between the
`fstat` before and after the copy and between the path stats before and after
(native Windows CI with Python 3.12/3.13 showed `stat` and `fstat` disagreeing
on `st_ctime_ns`: change time versus creation time; 3.10/3.11 agree). Any
disagreement is `CaptureChanged` and the copy is removed. Error messages carry
`strerror` only, never a path. Aliases (a symlinked
file or directory, even inside the root), escapes, non-media names and
directories are `CaptureDenied`; the locator is
`normcase(join(realpath(root), relative))`, so a symlinked root shares the
listing's identity. Fixed limits: 256 MiB per file (stat and stream), 8
completed captures, 512 MiB reserved, 2 capture/verify workers, 900 s idle,
1800 s hard; interactive admission is nonblocking (`CaptureCapacity`), a
`background=True` capture (Needs review recording) never takes the last worker
or capture slot and waits up to `BACKGROUND_WAIT_SECONDS` (2 s) for room, and
lifetimes are tested with an injected clock. The host's on_cleanup close is
final: a recording still in flight gets a capture error instead of creating a
new temp directory after it. An acquired capture outlives expiry and release
until its last use ends. Header-only dimensions (4096 px per side,
16,777,216 px) and video mark Generate unsupported; `verify` re-hashes the
current file without a copy. Every handle closes before unlink (a patched
`os.unlink` asserts it, plus `assert_closed`), and files are unlinked outside
the store lock. Known limit: on Windows before Python 3.13 `fstat` reports the
creation time, so a same-size rewrite that restores `mtime_ns` during the copy
window completes there with a digest of the copied bytes; POSIX and Windows on
3.13+ detect it through the descriptor's change time (native CI, 3.13 only).
Vocabulary rename, merge and delete bump the `tag_revision` of every image set
they change (and `image_index_revision`), so a Tag dialog opened before the
change gets `tag-conflict` on Save (`tests/test_pair_tag_maintenance.py`).
A case or Unicode spelling of a listed name (`A.PNG` for `a.png`, an NFD name)
is an alias too: each path part must be spelled as its directory lists it, so a
case- or normalization-insensitive filesystem (macOS, Windows) cannot store a
second locator. The capture preview answers one byte range with 206 (416 for
an unsatisfiable or multi-range header) inside the capture's pin, and a
cancelled preview, observe or Generate still closes its handle and ends its pin
or capture (`tests/test_api_annotations.py` `TestCancellation`,
`tests/test_model_gate.py`).

Follow-ups (tracked, not accepted): the legacy Auto-tag batch's cleanup
`unload_model()` in `py/api/autotag_routes.py` is not gated against the
batch's own in-flight inference when the batch coroutine is cancelled (other
owners are kept out; the thread may log an error); and `py/autotag.py`,
`py/api/images.py` and `database/operations.py` are over the 800-line ceiling
(all three were before #249 PR B), with a split of `py/autotag.py` into
backends and service planned first.

Budgets: `test_filter_work_is_bounded` always runs. It asserts that a plain first
page makes 1 link SELECT and a plain cursor page none, that every filter shape
makes exactly 1, the covering-index plan, and that the per-file values are built
once per scan and only for new files after a monitor invalidation. The opt-in
`test_filter_budget` (`PROMPTMANAGER_PERF_BUDGET=1`, the `perf budget` job) times
the D12 p95 < 150 ms budget; see "Performance budgets".

```sh
python -m unittest tests.test_api_workspace_images tests.test_card_seed \
  tests.test_image_favorites tests.test_library tests.test_api_library \
  tests.test_database_backup -q
```

Installing these fields needs a maintainer ComfyUI restart (Python, routes and
schema). Automated tests use disposable databases and output folders.

## Prompt sending and cross-pack compatibility

`tests/js/prompt-bridge-contracts.test.js` runs the Toolkit's shared protocol
contracts against the actual checked-in browser asset. Adapter and lifecycle
units cover role isolation, connected inputs, stale previews, callback recovery,
serialization normalization and cached-page restoration. Browser tab workflows
exercise real BroadcastChannel delivery; `tests/comfyui/prompt-bridge.spec.mjs`
checks the real widgets, execution serialization and renderer transitions.

The existing required real-ComfyUI job also runs `test:compat`: PromptManager
alone, pinned KikoTools alone and both together, each starting in Classic and
Nodes 2.0. It copies tracked source into disposable hosts, preserves independent
configuration and checks edits/reload with both packs present. KikoTools remains
on its current embedded foundation; this does not adopt the Toolkit in that pack.

For local runs, use a clean KikoTools checkout at the revision in
`tools/vendor/cross-pack-runtime.json`, plus the managed-host prerequisites above:

```sh
PM_COMFY_ROOT=/path/to/pinned/comfyui PM_KIKO_ROOT=/path/to/pinned/kikotools \
  npm run --prefix tools/vendor test:compat
```

The compatibility runner reserves loopback ports 8193–8195; `PM_COMPAT_PORT`
selects another consecutive unprivileged range. Never run automated tests against
the maintainer's running ComfyUI. This JavaScript host extension needs a full
browser refresh after deployment; its first slice adds no gallery send controls.

### Workspace prompt inspection and sending

`tests/test_api_prompt_inspector.py` covers rooted physical-file identity,
execution/graph/parameter evidence, saved associations, ambiguity, empty negatives,
read-only behavior, bounded PNG/video metadata and corrupt inputs. Run it with
`python -m unittest discover -s tests -p test_api_prompt_inspector.py` in a disposable
checkout. `tests/js/prompt-inspector.test.js` and `prompt-sender.test.js` cover
request races, explicit destination selection, preview tickets and uncertain
acknowledgements. Browser workflows exercise the same controls in Images and
Viewer, including nested modal focus, mobile layouts and per-role sending.
`tests/comfyui/prompt-sender.spec.mjs` sends through the actual workspace UI into
real Classic and Nodes 2.0 widgets and verifies workflow serialization without
loading a workflow or queueing generation.

## Image attribution: certain or unlinked (#342)

The image monitor and the output scan link an output only to a prompt that
provably produced it; anything uncertain stays unlinked and the monitor logs it
at info ("No certain prompt for <file>; left unlinked"). There is no recency
fallback (`_get_fallback_prompt` is gone) and no unproven tracker entry.

- **Empty editor.** Prepend and append text are never stored as the prompt
  (maintainer's product decision; #343 tracks letting a link drive `text`). A
  PromptManager, Text, Focused, Split or Pair node whose `text` is empty saves,
  counts and tracks nothing for the positive side, logs at info (no "Could not
  save or track" warning), and marks its run key empty, so no earlier entry of
  that key can be used and a batch item keeps its queue place
  (`PromptTracker.mark_empty`). The queue hook counts nothing for it either. A
  save or tracking failure of a positive node marks its run key the same way, so
  an older entry of that key never stands in for the failed run.
- **Run keys** (`utils/run_keys.py`): sha256 of a prompt node plus its whole
  upstream closure (`class_type` and `inputs`) in the API graph. Nodes register
  tracker entries under it; the monitor computes it from the image's embedded
  graph. The snapshot taken when the file appears names the key's latest run; a
  queued entry is taken only by key and from that run (batch items in order, a
  placeholder leaves its image unlinked, same-key entries of earlier runs ahead
  of it are stale and dropped, entries of later runs that executed during the
  monitor's delay wait for their own images). Without a queued entry the
  snapshot's entry itself is used only when its run id is known
  (`comfy_execution.utils.get_executing_context`) and that run registered one
  entry for the key, which covers a cached re-run (sampler seed changed) without
  applying a batch's last item to every image. When the key's latest entry is a
  placeholder the image stays unlinked without consulting history (the same
  rule as a popped placeholder). More than one positive PromptManager node in
  the image graph, or any positive node without a key: never linked from the
  tracker. Links are followed exactly as ComfyUI's `is_link` defines them (a
  `str` node id and an `int` or `float` slot in a two-item list). Equal keys do
  not always mean equal text: the key can't see `IS_CHANGED`, so an upstream
  node that forces re-runs gives two runs one key; that is why the run named by
  the snapshot is required as well. A file event delivered after the next run
  already re-registered the key is the one remaining exposure (see the ledger).
- **Recorded history** (`database/run_attribution.py`): a file is attributed when
  exactly one recorded run reported its output location (root key, subfolder,
  name), its mtime lies within that run (dequeue to terminal or callback, +-2 s),
  and every positive context names the same stored prompt (the live
  `positive_prompt_id` hint). The monitor tries it last; the scan tries it when the
  #170 browser-parser floor finds nothing, and also for files with no prompt
  metadata at all (`--disable-metadata`, savers that embed none); it never
  creates a prompt. Lookups are batched (`stored_run_prompts`): one
  parameterized `filename IN (...)` query per chunk of at most 500 names
  (`LOOKUP_CHUNK`), matched in memory, so a scan batch asks history once per
  chunk, never once per file. The history schema is unchanged (no extra index),
  so older builds still open the library.
- **After the run ends.** An image with no run keys (no embedded API graph) that
  nothing identifies waits for its run: when the run's outputs are recorded
  (`write_execution_outputs_for_finish` notifies `subscribe_outputs_sealed`
  listeners after commit), each waiting image the run reported gets exactly one
  history-only re-check on the monitor's worker. At most 1000 images wait, each
  for at most an hour; shutdown forgets them. The image is registered before the
  first history check, so a run sealed in between is not missed; a re-check that
  can't be scheduled yet (the image's own pass still pending, or the pending set
  full) is kept due and scheduled when a pending image finishes. A keyless image
  outside ComfyUI's output folder never waits (history can't attribute it).
- **Spellings.** History keeps ComfyUI's own spelling of its output folder (the
  root key). A file is matched through that spelling first, then with both sides
  resolved (`realpath`), so an output folder reached through a symlink or a
  Windows junction still attributes the scan's resolved file paths; waiting
  files are keyed by their resolved path for the same reason. The scan makes
  its batch's history lookup only when the first file in the batch needs it.
- **Blank positive.** A resolved pair whose stored positive is empty (the #342
  image with its Pair execution record) is `unresolved` for the scan and for
  `ingest_resolved_pair` (it used to raise in `_save_pair`).
- **Parity floor.** Metadata linking, the scan's resolver and parser floor are
  unchanged for every non-blank result; only guesses are gone.

Tests: `tests/test_prompt_attribution.py` (nodes, tracker, monitor, run keys),
`tests/test_run_attribution.py` (history attribution, monitor and scan),
`tests/test_review_image_links_tool.py`, and the real-host
`tests/comfyui/image-attribution.spec.mjs` (both renderers): a typed Pair image
links, a Pair image whose positive is only linked prepend text from an unknown
node stays unlinked with nothing stored, an unknown node feeding `text` links
through its run key, and its cached re-run with a new sampler seed links again
(the host's `execution_cached` event names the PromptManager node, under the
fixture's Classic cache). A second case saves with metadata disabled and holds
the run open behind the fixture gate after SaveImage: the output is waiting and
unlinked while the run is open, and links to the stored prompt once the run's
outputs are recorded. The managed host config disables monitoring, so these
cases start and stop the plugin's monitor through the owned fixture route
`/pm_execution_fixture/monitor` (its GET lists the waiting files).
On origin/main the prepend image was linked to an unrelated prompt.

Repair: `tools/review_image_links.py <db> --output-dir <ComfyUI output> --ids
<ids>` (or `--prompt <id>`) judges links by these rules (`keep`, `relink`,
`unlink`, `conflict`, `missing`) on a backup copy and changes nothing.
`--apply` writes `relink`/`unlink` only for ids named with `--ids`, after saving
`<db>.pre-relink_<stamp>`, and only while each image is still linked to the
prompt it was judged on (otherwise it is counted as skipped). `relink` points the
link record at the other prompt and drops its pair links. `unlink` removes only
the image's link record (its `generated_images` row, the metadata snapshot stored
in it and its pair links): that is how the app represents an unlinked image, the
same as a file the scan could not attribute, so the file stays on disk and in the
gallery as unlinked, and its favorite and tags (kept by file) are untouched. Every
run prints this. Queue-linked batch images can't be proven after the fact and read
`unlink`, so review before applying. Stop ComfyUI first.
## Linked prompt text (#343)

A link drives `text` on every PromptManager node, and `negative_text` on the Pair,
as it drives any core widget. The Focused, Split and Pair editors hide their widgets,
and the frontend only draws a widget's socket while the widget is visible, so
`web/js/text-links.js` detaches those two inputs from their hidden widgets. They become
ordinary sockets; the schema, widget order and saved `widgets_values` do not change. A
link dropped on a prompt box (or its link note) fills that box's input: Nodes 2.0 resolves
it through the box's `.lg-node-widget` and `[data-slot-key]`, Classic through the node's
`getInputOnPos`. An exact STRING link dropped elsewhere on the editor fills the main prompt
first, then the negative, before the frontend's own choice (it used to pick `prepend_text`,
which caused #342). A link being moved off a text input counts as free, as the frontend
counts it, so dropping it back on the editor keeps it on `text`. Wildcard links (a
frontend Primitive before it is typed) are not steered.

The frontend's legacy Primitive is virtual: it writes its value into the target widget,
which the editor text no longer is. For the editors' detached inputs the `graphToPrompt`
wrapper puts the Primitive's value (through virtual reroutes, also inside subgraphs, where
the queued id is `<subgraph node>:<inner node>`) into the queued `text`/`negative_text`,
resolved as the host resolves a value written into the widget: the Primitive's text
replacement when its "Run widget replace on values" option is on, then the hidden widget's
own serializer (dynamic prompts: `{red|blue}` becomes one choice). Each queued output is
filled once (the queue hook only fills an output that did not come through the wrapper),
so a wildcard is drawn once per run. Inputs still bound to their widget (the Classic
nodes) are left as the host resolved them; as on main, the Classic text widget's own
serializer queues its text as written, wildcards included (specs wait on the node's
`_pmTextSerializerReady` before queuing). A Primitive drives the text as before, also in
workflows saved before #343. On every Run the host logs "Invalid widget or widget name"
for a Primitive wired to a detached editor input: its own widget write finds no widget
binding there. That is expected and harmless. A linked
Classic text box gives its canvas row back while linked (the core keeps an empty gap) and
gets its size back on disconnect. The metadata parsers map a known node's positional
widget values through its widget table when its widget-bound inputs do not line up with
it (the editors' text inputs are sockets now).

Not covered, by ruling: linking a subgraph input to an inner editor's `text` gives the
subgraph a plain STRING socket, not a promoted typeable widget as a core widget input
would. The socket was unreachable before #343, so nothing regressed.
While linked, each editor says the value comes from a link, and the typed text is kept
in the workflow for when the link is disconnected. That includes the Classic nodes,
which used to save `null` under a link.

- `tests/js/text-links.test.js`: socket exposure, drop preference (exact STRING, moved
  links), box hit-test, Primitive values through reroutes, replacement and the widget
  serializer (bound inputs left to the host), stored values, the Classic row.
- `tests/test_generation_params.py` (`workflow-only-detached-*` cases, with JS parity
  vectors): editor text inputs without widget bindings keep their positional values, and so
  does a core KSampler whose only widget-bound input (`cfg`) is out of schema order.
- `tests/browser/{focused,pair,classic}-node.spec.mjs`: the linked note, typed text
  kept through save and configure (also with a workflow's widget binding restored), the
  Classic "use prompt" guard while linked.
- `tests/comfyui/text-link.spec.mjs` (both startup projects): a real mouse drag of a
  STRING output onto each node's prompt box (and onto the Pair's negative socket); the
  link, the note and the typed text survive renderer switches both ways, a reload,
  disconnect and node removal. `fixtures/workflow-before-343.json` and
  `workflow-before-343-primitive.json` were serialized by the pre-#343 code on the pinned
  host and must load unchanged; the second one's legacy Primitives keep driving the text
  through a real Run. Also: a Primitive dropped on the Main prompt box and a string on the
  Negative prompt box, a moved link released on the editor body, paste, clone and undo, a
  legacy Primitive inside a subgraph, and wildcard Primitives on every node type queued
  through a real Run as one choice, the same one another `graphToPrompt` wrapper sees.
- `tests/comfyui/execution-text-link.spec.mjs` (execution host): workflows built by the
  real frontend through the sockets, with text from `PrimitiveString`, the legacy
  Primitive or through `PreviewAny`, for PromptManager, PromptManagerText, Focused, Split
  and the Pair (positive, negative, both, neither linked, and a Primitive negative). A
  wildcard Primitive on PromptManager (queued as written) and Focused (one choice) saves
  and counts what was queued. The linked text is saved, counted once, recorded as
  the run's raw positive/negative, and after a scan the run's image links to it. Text
  only known at run time (`PreviewAny`) cannot be read from the image; the case only
  requires that it is never linked to another prompt (its attribution from the recorded
  run belongs to #342).

```sh
node --test tests/js/text-links.test.js
npm run --prefix tools/vendor test:workflows -- classic-node.spec.mjs focused-node.spec.mjs pair-node.spec.mjs \
  --project chromium --project touch --project firefox --project webkit
PM_COMFY_ROOT=... PM_TEST_PYTHON=... npm run --prefix tools/vendor test:comfyui -- text-link
PM_COMFY_ROOT=... PM_TEST_PYTHON=... npm run --prefix tools/vendor test:executions -- execution-text-link
```

## Durable execution ledger

`npm run --prefix tools/vendor test:executions` runs 52 real execution cases
(26 each in Classic and Nodes 2.0, retaining all 48 prior execution cases and adding
the two #342 image-attribution cases),
alongside the existing 80 stock host cases (the original 78 plus the native
editor host case) and six pack compatibility cases. Run
these suites serially because they share report cleanup and owned host resources. Use the same pinned disposable `PM_COMFY_ROOT`,
`PM_TEST_PYTHON` and Playwright browser installation as `test:comfyui`:

```sh
PM_COMFY_ROOT=/path/to/disposable/ComfyUI \
PM_TEST_PYTHON=/path/to/cpu-env/bin/python \
PLAYWRIGHT_BROWSERS_PATH=/path/to/playwright-browsers \
npm run --prefix tools/vendor test:executions
```

This suite reserves `http://127.0.0.1:8196/` and never attaches to an existing
server. Each renderer worker launches a CPU/offline host with independent
configuration, database, user and output directories. The shared launcher installs
tracked snapshots of PromptManager and a tiny temporary Git-tracked fixture pack.
Only that owned test pack provides cached text, deliberate failure, an
interruptible queue gate, deterministic CLIP/model/latent and CPU tensor inputs,
and control routes.
It replaces external sampler inference and trigger injection only inside the
disposable host; real registered PromptManager classes, queue, executor and cache
remain active. Its cache control resets the real executor on its worker to exercise
Classic, LRU, RAM-pressure and disabled caching; production has no test nodes or
endpoints. Cleanup always releases the gate, stops the host and removes both
owned directories. Logs and Playwright failures appear under
`tools/vendor/test-results/executions/` and the existing CI artifact upload.

The request-only case creates no browser or WebSocket client. Other cases verify
real `execution_cached` node lists after a failed job, both renderer switches and
workflow reload, repeated public UUIDs, interruption, graph rejection, and queued
removal before dequeue. Actual host history is checked alongside finite polling
of SQLite after the background writer. The read helper uses SQLite read-only
mode, exact UUID job/session filters and a 256-row limit; absent ledger schema is
explicit, while missing/corrupt databases and other read errors fail the suite.
Sensitive diagnostic and extra-data sentinels must never appear in lifecycle
rows. Exact prompt fields are deliberately persisted only in context snapshots.
The same read-only helper calls the internal `get_execution_contexts` and
`get_execution_outputs` readers for requested run IDs and inspects their snapshot
identities and current usage counts. Its `outputs` map is keyed by immutable run
ID, alongside the existing `rows`, `captures`, `usage` and `snapshotIds` results.

`execution-outputs.spec.mjs` runs actual stock SaveImage and PreviewImage nodes
from tiny CPU tensors. It compares each attempt's persisted descriptors with that
attempt's final host history, including A,A,B batches, multiple output nodes,
cached locators, reused public UUIDs, PNG metadata disabled, graphs without PM
nodes and text-only outputs. The pinned SaveImage returns an IMAGE socket; linking
it to the owned failure/gate node orders the partial-run tests explicitly. A held
downstream gate proves completed image work does not finalize the run early.
Other cases verify real output capture across startup in each renderer, switching
both ways and workflow reload. PM usage counts retain their existing per-job
behavior. References identify reported locators only: file identity is unverified,
new generation and context membership are unknown, and temp references are
transient. The stock suite excludes both execution files; discovery retains its
80 cases (the original 78 plus the native editor host case) and the six pack
cases unchanged.

For a meaningful historical failure, create a separate disposable detached
checkout at `fdba6fd0126a8a40cbd6f3e50ed4c5de8991efd4` and pass its path to
`launchExecutionHost({ repository })`. That base also predates the #211 config
import fix: after confirming an actual headless job succeeded, use
`readExecutionRuns` on its explicitly constructed owned path
`host.stateDirectory/custom_nodes/promptmanager/prompts.db`. It reports
`schemaAvailable: false` and empty rows, failing the expected succeeded-row
assertion. This historical inspection stays outside the ordinary suite, which
requires the configured database path. The launcher also accepts
`PM_EXECUTION_REPOSITORY`; omit it for the implementation worktree. Stage new
files first because snapshots use `git ls-files`. Never reset the implementation
checkout or point this harness at a live ComfyUI checkout.

The package-qualified config import also corrects #211: the configured database
path is honored when PromptManager runs as a ComfyUI custom node. Follow the
[database path compatibility and safe backup sequence](README.md#database-path-compatibility-211)
before restarting an existing deployment.

The ledger records only new dequeued jobs and observed outcomes. Existing
counters, prompts, pairs and image links are preserved, with no reconstructed old
runs. Schema v2 migrates automatically when the configured database opens, adding
immutable prompt-context tables without backfilling old runs. The internal reader
returns `not_captured` for preexisting runs; a new capture can instead be complete,
partial, ambiguous or unavailable. Keep the existing database backup/restore
procedure: restored history changes storage epoch, and old library navigation
hints must never be rebound to unrelated current rows.

Context cases verify A,A,B as three jobs/two combinations/one positive snapshot
when every positive field matches; all five registered PM variants; exact raw
whitespace and Unicode; known empty negatives; Text negative roles and crossed
pairs; fully cached repeats after trigger configuration changes; successful source
text before downstream failure/interruption; unknown cached dynamic raw; unsupported
transforms and duplicate list invocation ambiguity. Fresh values use
`runtime_return`, cached effective STRING outputs use `host_cache_output`, and
submitted literals use graph inference. SQL NULL means unknown; empty text is
known. Text is never trimmed or normalized by capture. Cache availability and
inferred sampler roles prove neither sampler execution nor consumption, and list
indices do not identify generated images. External trigger configuration changes
must not rewrite the effective text of an existing cached result. The cache matrix
also checks that positive/pair usage increments once per dequeued job while a
negative-only component retains its existing zero standalone-use count.

Fault injection for restore fencing, inherited async capability revocation, queued
finalization, epoch and generation revocation, provider-local cache entries, eviction/reset, malformed or
oversized payloads and missing host interfaces lives in the focused Python
`test_execution*.py` suites. These use disposable storage and mocked host interfaces;
they are distinct from the pinned real executor/cache checks above. Run-owned
output descriptors do not create gallery image associations or a chronological
timeline. Deploying the Python changes
requires a ComfyUI restart. After restart, queue three new jobs A,A,B with identical
positive fields and two negative recipes; existing counters should advance as usual,
and internal context inspection should find three attempts and two combinations.
For a live output check after that manual restart:

- Queue SaveImage and PreviewImage with a batch of two, then submit an unchanged
  cached repeat and a changed tensor/input under new jobs.
- Inspect each run's internal output capture against its final ComfyUI history;
  cached references may repeat, while the attempts remain distinct.
- Switch Classic to Nodes 2.0 and back, reload the workflow and queue again.
- Verify prompt/pair counters and gallery links retain their existing behavior;
  output references alone do not prove file bytes or prompt membership.

Automated checks use only managed disposable hosts. They never restart or upgrade
the personal ComfyUI server.

## Chronological Library history (#209)

`#/library/{positive_id}` retains saved negative selection, the full selected
negative directly beneath its positive, acknowledged role/pair sending, and one
pair-filtered shared gallery. Recorded executions are a separate projection.
Selecting an execution, anchor, Raw/Effective mode, or reported occurrence does
not select a saved variant or refetch its gallery. A,A,B means three attempts,
two frozen contexts, and one exact raw-positive group. Different composition or
evidence can produce different frozen contexts despite equal-looking previews.

The versioned public reads are:

| Route | Meaning |
| --- | --- |
| `GET /prompt_manager/library/{positive_id}/history` | Exact frozen raw-positive group; compact chronological windows |
| `GET /prompt_manager/library/{positive_id}/history/pairs/{pair_id}/newest` | The pair's newest recorded run for the history run dot (#249 D26): `{run: {run_id, anchor_id, context_id}}` or `{run: null}` |
| `GET /prompt_manager/history/runs/{run_id}` | Frozen fields, per-field provenance, capture and outcome for a known UUID |
| `GET /prompt_manager/history/runs/{run_id}/outputs` | Ordered host-reported occurrences, including repeated locations |
| `GET /prompt_manager/history/runs/{run_id}/output/current/{media,thumbnail,inspection}` | Authorized current file at that reported location, using the returned epoch/scope URL |

The newest-pair read takes no query. It answers 404 when the positive is not an
eligible saved prompt or the pair is not one of its pairs. It walks the positive's exact
raw-text scope newest first and follows only a row whose `reference_epoch` is the
current epoch and whose saved-pair hint still resolves to that pair (the
`get_history_run` hint rule). It inspects at most 16 candidates, so 16 newer stale hints
report `{run: null}` (the dot then opens Repeat run). Like the other reads it runs inside
the fenced history snapshot: a restore or epoch change is 409 `history-changed`, a
storage failure 503 `storage-unavailable`.

Run and output reads accept the returned `epoch`; current-file routes require
the returned epoch, current scope and occurrence coordinates. Run integers are
decimal strings, preserving SQLite's full signed 64-bit values. Windows and
receipt pages default to 40 with a 1–100 limit. Cursors are opaque, authenticated,
scope-bound and capped at 4096 bytes. Run JSON has an 8 MiB UTF-8 limit and receipt
pages a 1 MiB limit; exceeding a limit fails explicitly instead of truncating
exact evidence. The unchanged capture limits remain 16 anchors/256 occurrences.

A cursor freezes its upper ordering boundary, not an application-wide snapshot:
each page has one SQLite read transaction and a count as of that read. Later
pages can observe completed capture, deletion or other current changes. Newer
runs require Refresh history. Unknown positive raw text cannot enter an exact
Library group but remains readable through its known run UUID. Unknown (`null`),
known empty (`""`) and whitespace are distinct. Complete-zero reported outputs
means the host reported none; missing capture means unknown, and partial or
conflicting capture remains incomplete. Receipts prove reported locations, not
historical bytes, newly generated files or context-output ownership. Cached
attempts can repeat exactly the same receipts.

Each nonempty history page also returns an authenticated `resume_cursor`, including
an exhausted page with no continuation. Re-entry uses `resume` instead of `cursor`
to read from that prior upper boundary, then restores at most the prior number of
40-run pages. It retains horizontal/vertical scroll and the visible anchor as
in-memory visit intent. The API validates epoch and exact saved-text scope again;
DTOs and current candidates are never saved as visit intent. Explicit Refresh
loads the newest window/count and revalidates the selected run's exact raw-positive
membership, context and receipt. A valid selection outside that window has an
explicit label and **View selected run** control.

Every receipt can be selected, including missing/error candidates. **Retry current
availability** rereads its existing bounded output page; a reduced response limit
may require several reads within that one already loaded page. Earlier rows,
context/mode, exact selection and saved gallery remain. Recovery actions use the
shared client's strict `smaller-limit`, `reload-outputs`, `retry-read` and
`refresh-history` vocabulary. Active inspection epoch/identity failures immediately
reach the owning history recovery, clearing stale recorded actions before reload.

Restore acceptance depends on merged #223: incompatible online storage
replacement must safely refuse without harming another owner's WAL/connection.
History fencing does not repair unsafe replacement. For accepted replacement,
the read transaction ends before a fresh-connection epoch check; the final
epoch/generation validation is the publication linearization point. Browser
generations also ignore late success/error callbacks after route/scope changes.
Old cursors, epoch and current-scope URLs fail with bounded recovery instructions.
Restored numeric IDs never repair an original navigation hint. Saved Library
details are revalidated separately, and a changed owner requires explicit reload.

Current media/thumbnail responses use an already-opened, validated canonical
file handle, bounded streaming and `Cache-Control: no-store`, including HEAD and
range behavior. Inspection is a current metadata observation with pre/post file
facts, canonical reauthorization and a fresh scope/epoch check; it is not pinned
historical bytes or an OS filesystem snapshot. A deterministic change during the
metadata read is rejected. A later request can legitimately observe replacement
bytes at the same path without changing the immutable receipt.

The focused acceptance command retains all four projects:

```sh
npm run --prefix tools/vendor test:workflows -- \
  workspace-history.spec.mjs workspace-visual.spec.mjs \
  --project chromium --project touch --project firefox --project webkit
npm run --prefix tools/vendor test:executions
npm run --prefix tools/vendor test:comfyui
npm run --prefix tools/vendor test:compat
```

Use the pinned disposable prerequisites above; managed suites run serially.
The real execution helpers compare SQLite captures to these public routes for
stock SaveImage/PreviewImage batches, multiple nodes, metadata disabled, cached
headless repeats, partial failure and interruption. Additional cases verify the
actual PM A,A,B group, unknown-positive exclusion, and exact frozen Raw/Effective
pair sends through the acknowledged host bridge after Classic/Nodes 2.0 startup,
both switches and workflow reload, without queuing or loading a workflow on send.
All original 42 execution, 78 stock-host and six compatibility cases remain.

The browser fixture uses actual APIs/SQLite/files, fixed owned backup snapshots,
one-shot bounded publication barriers and fixed filesystem/configuration commands.
Reset and cleanup release barriers, restore seams and revoke commands. It never
attaches to a personal host. The canonical HTML and its six required assets are
served from a fixed test-only allowlist; the original concept index/app/style
bytes are hash-pinned and unmodified. At 1600×1100 and 390×844 in both themes,
with fixed locale/UTC/reduced motion and ready fonts, tests capture unmasked
concept/production PNG pairs and geometry, and pin actual branch padding,
surfaces, 2px role lines, 57px rhythm, staggering, colors, selected focus and native
dialog behavior. Controls and actions are checked independently of screenshots.
Keyboard focus/return, coarse targets, 200% layout, route return and persisted
page lifecycle recovery retain their separate behavioral tests.

Initial production acceptance requires maintainer inspection of paired PNGs and
geometry; never update snapshots automatically or mask content to bless a gap.
Data-dependent text/images/counts and the clearly labeled saved/frozen/current
distinctions are documented in the comparison helper. The gallery cards carry the
concept's Tag button beside Info (#249 C2 retired D9); every prompt block now renders the concept's
`section.prompt-section` (D22), so the shared role-block spacing, padding and glyph
gap is closed, and only the production-only surfaces above keep their own rules
(D48). The
responsive production Viewer keeps its toolbar in the media panel and prompt details readable rather than reproducing the concept's
mobile toolbar collision. These are explicit remaining visual differences, not
a whole-application or full-gallery/Viewer pixel-parity claim.

Follow-up: monitor video support. The live image monitor (`utils/image_monitor.py`,
`GalleryConfig.SUPPORTED_EXTENSIONS`) processes images only; videos written by
ComfyUI savers reach the Library through the admin scan, which reads the shared video
carrier. Accepting `VIDEO_EXTENSIONS` in the monitor and routing them through
`read_video_metadata` is a separate, issue-linked change.

Follow-up: local Inspect parity (#249 PR L1). The browser readers
(`web/js/png-metadata.js`, `web/js/workspace/local-video.mjs`,
`web/js/workspace/local-projection.mjs`) answer as the server does for the same bytes
and a matching extension (`tests/test_local_inspect_parity.py`; the real-ffprobe cases
skip without ffmpeg). The remaining known divergences, checked against ffprobe 9.0.2,
`read_inspection_file` and `_metadata`, are tracked here, not accepted as parity:

- Media kind comes from the bytes locally and from the extension on the server: a PNG
  named `.mp4` is read as a PNG locally (format and size shown) while the server probes
  it with ffprobe (no format, no size); MP4 bytes named `.png` read locally as video
  while Pillow fails them on the server (`unreadable`). Parity is stated for the same
  bytes with a matching extension.
- JPEG dimensions: the local header read stops at 64 KiB, so a JPEG whose SOF follows
  more than 64 KiB of APP segments (camera, Photoshop and EXIF/XMP workflow savers)
  shows no dimensions, while Pillow reads them within the server's 8 MiB header bound.
  The fix is a segment-header walk up to 8 MiB, which needs a plan amendment.
- Classic QuickTime `moov/udta` text atoms (`©cmt`/`desc` with a 16-bit size and
  language, outside `meta`) are not read locally; ffprobe reports them as
  `comment`/`description`, so the server can select that envelope while the local
  read is `absent`. No ComfyUI saver writes them.
- Tag-size bounds count raw bytes locally and ffprobe's escaped JSON on the server
  (first probe 12 MiB + 64 KiB, role-only re-probe 4 MiB + 64 KiB): only tag sets near
  those bounds and dense with quotes or control characters can make one side re-probe
  or fail while the other reads.
- More than 10,000 container elements stop the local read (`limited`, metadata
  `unreadable`); ffprobe reads such files.
- An EBML DocType other than `matroska`/`webm` is an invalid container locally; ffmpeg
  only warns and reads it.
- Deep JSON (local roles, PR L3, and video envelopes): both browser CPython-JSON
  parsers (`py-json.mjs`, `local-video.mjs`) share one cap, `JSON_MAX_DEPTH` = 1,000
  nested arrays or objects (each counts, an empty one too); past it a record is invalid
  JSON locally. The server's `json.loads` raises RecursionError at a limit that depends
  on the Python, measured on Linux x86-64: 993 levels from a bare script on 3.10 and
  3.11 (the 1,000 recursion limit minus the caller's frames, so about 990 inside the
  server), 9,997 on 3.12 and 9,998 on 3.13 (the C recursion counter; lower on Windows
  builds), and about 52,150 on 3.14 (stack-based, varies with the thread's stack).
  The record kinds answer: a deep `prompt` graph (or `comfyassets_prompt_pairs`
  envelope) is malformed; a deep `workflow` is ignored, so the overwritten PromptManager
  check (R1) does not run and a prompt can resolve; a deep video `comment` or
  `description` envelope makes the file unreadable on both sides (the server's
  RecursionError reaches `read_inspection_file`). That gives two directions:
  - 3.10/3.11, between the server's limit and 1,000: the server refuses and the local
    page reads, so a deep prompt resolves locally while the server says malformed, and
    a deep workflow can make R1 refuse locally where the server resolves.
  - 3.12 and newer, from 1,001 up to the server's limit: the local page refuses and
    the server reads, so a deep prompt is malformed locally while the server resolves,
    and a deep workflow is ignored locally while the server can refuse it under R1.
  Past both limits they agree. `test_local_json_depth_cap_at_its_boundary` checks the
  local answer at 999, 1,000 and 1,001 for both kinds and that any server difference
  is only one of the two directions above.
- Local roles (PR L3): `\d` in the A1111 settings footer is any Unicode decimal
  digit on both sides, but the browser's Unicode version can be newer than the server
  Python's, so a footer whose step count uses a digit added after Unicode 15 can differ.
  `str.lower()` and `toLowerCase()` differ only for characters no node class name uses.
- A duplicate key in an object enclosing a nest deeper than the running
  Python's json limit is `malformed` locally and `limited` on that Python.
- Decimal exponent limits assume CPython's C `_decimal`; on a `_pydecimal` interpreter
  the server would read the over-limit exponents the local projection refuses.
- Follow-up (two CPython-JSON emulations, review M6): `local-video.mjs`
  (`pythonJsonLoads`/`pythonFloatRepr`, numbers kept as lexemes) and `py-json.mjs`
  (`pyJsonLoads`/`pyFloatRepr`, Python value kinds) both emulate `json.loads`; only the
  depth cap is shared so far. A fix to one parser can miss the other, so the video
  reader should move onto `py-json.mjs` in a later PR.

Preserve raw logs, failed traces, paired PNGs, geometry, runtime versions and
source hashes before later managed commands clear their output directories.
Frontend baseline updates are additive through `coverage:update` only after
every previous metric floor and each new file's 80% line requirement pass; rerun
the coverage gate using the committed baseline. Python retains its 85% combined
statement/branch gate. Deploying the history Python changes requires a maintainer
ComfyUI restart; automated acceptance never restarts the personal server.

## Native recipe editor (#234)

The Library page edits recipes in one native modal (`dialog.library-editor`)
instead of sending users to the legacy dashboard. It is frontend only: it consumes
the Library/pair routes, epoch tokens, null-clears and conflict codes defined by
the backend change (#233) and does not add or change a route, so this section does
not repeat them. The editor reads fresh, `no-store` state before it opens.

The four dialog modes match the approved concept's shell: **New prompt**,
**Edit recipe metadata**, **Create text revision** and **Add a negative variant**.
Pair **Archive…** and **Restore** are one reviewed action with **Show archived**
in the saved-variants list; archiving a pair never touches its positive prompt, its
siblings, history or images. **Export** downloads the full v1 recipe export. A
failed Save keeps focus inside the dialog and keeps every entry.

Recovery is exercised against real failure responses, not mocks of the controller:

- a **conflict** response keeps the draft and explains the newer saved state; a
  **storage-changed** response writes nothing, and only New prompt re-reads in
  place: every other dialog offers only **Reload Library**, because an old numeric
  owner is never resolved after a restore;
- an **unknown outcome** (no response) keeps the draft and offers an explicit
  **Retry** that resends the identical request once, which the server
  deduplicates; an Edit metadata retry instead reloads the latest saved values for
  review and resends nothing;
- a **confirmed receipt** whose follow-up refresh fails offers a read-only
  refresh retry and never re-sends the write.

The concept comparison (`library-editor-canonical-comparison.mjs`) inventories the
concept dialog and the production dialog in the same browser: geometry, CSS tokens,
colors and a control inventory (labels, field types, options, actions and their
danger/primary classes). Only the documented differences (the prototype's Save
label and demo note) are mapped. The mutation check proves the comparison fails when
a token, label or control is changed, so it cannot pass vacuously.

PNG baselines are intentionally absent: `toHaveScreenshot` is not used for the
editor. Paired concept/production captures and the artifact-only states
(`library-editor-visual.spec.mjs`) are attached as test artifacts for maintainer
acceptance; a baseline is added only after the maintainer accepts them.

Focused commands:

```sh
npm run --prefix tools/vendor test:workflows -- \
  workspace-library-editor.spec.mjs library-editor-visual.spec.mjs \
  --project chromium --project touch --project firefox --project webkit
npm run --prefix tools/vendor test:comfyui   # library-sender.spec.mjs host regression
```

`native editor preview sends exact saved roles across renderer transitions` opens
the real workspace, creates a prompt with an explicitly empty negative, and sends the
selected pair through the acknowledged host bridge in Classic and Nodes 2.0, across
both switches and a workflow reload. It asserts the actual widgets and serialized
workflow, no queue or workflow load, unchanged history and pair metadata, and that
a connected destination rejects the preview without changing either role. It needs
the pinned disposable ComfyUI, so it runs in CI's "real ComfyUI compatibility" job.

Deploying this change needs no Python change and no ComfyUI restart: the diff for
the editor UI touches only HTML, CSS and JavaScript (plus test fixtures). A browser
refresh is enough. New editor modules have additive coverage floors in
`tools/vendor/frontend-coverage-baseline.json`; none were lowered.

## Native vocabulary backend (#236)

Routes under `/prompt_manager/vocabulary/`: `GET tags` and `GET categories`
(bounded pages, signed epoch-scoped cursors), `POST tags` (create or reuse an exact
name), `PATCH tags/{id}` (rename), `POST tags/{id}/delete`, `GET tags/{id}/impact`,
`POST categories/preview` and `POST categories/apply`. Every command carries
`expected_epoch`; tag commands also carry the exact `{id, name}` identity, and
category apply carries the preview's `impact_token`.

Error contract: command validation failures are 400 with `code: null`; reads
return `{success: false, error}` with no `code`, and impact on an unknown id is 404. A stale epoch is 409
`storage-changed`; a changed or missing identity, or a changed category projection,
is 409 `vocabulary-conflict` with a generic message. Both 409 bodies include the
current `library_epoch`. Nothing is written on any refusal.

Count semantics: a count is the number of eligible Library parents carrying the value
directly or through one active saved pair, exactly what `list_library(tags=[name])` or
`list_library(category=value)` returns. Unused registry tags report 0 and are kept by
`get_all_tags`, `suggest_tags`, backup and restore, duplicate cleanup, orphan pruning
and owner deletion (`TestRetainedTags` in `tests/test_database_backup.py`). Restore
rotates the epoch, so a pre-restore `expected_epoch` is refused with `storage-changed`.
Owner rows and their timestamps never change on a tag command; a category apply only
touches rows carrying the exact source.

Two legacy routes change additively: `GET /tags` now lists unused registry tags, and
`/tags/suggest` without context appends unused names at count 0. Unused tags become
deletable only through the native Delete tag (vocabulary UI PR), not on the legacy
tags page.

Both token functions (`library_edit_token`, `category_impact_token`) share
`canonical_token` in `database/library_tokens.py`; `TestSharedTokenHashing` pins their
outputs.

Deploying this backend needs a ComfyUI restart (Python and route changes).

## Native vocabulary UI (#236)

`#/tags` ("Tags & categories", page title "Tags") is a native workspace route; it no
longer hands off to the legacy tag manager. That page stays reachable from Overview's
"Open existing tag manager" card for tag search and sort, the untagged entry and
multi-tag AND/OR browsing; bulk prompt actions (delete, tag, category) stay on the
legacy admin page. The screen consumes only the `/prompt_manager/vocabulary/*` routes
above and adds no route, so PR B needs no ComfyUI restart: a browser refresh is enough.

Screen: a `+ Create tag` heading action, a Tag vocabulary table (columns Tag, Prompts,
Images counting the files that carry the tag (`image_count`, #249 C2), and Actions with Browse and
Rename / merge), and a Categories panel whose category rows end with the Manage
category row (Review). Lists load 100 rows at a time, append when scrolled into view
and keep `Load more tags` / `Load more categories` while more remain. Prompts counts
are the Library Browse totals.

Dialogs (`vocabulary-dialog.mjs`): **Create tag**, **Rename or merge tag** (renaming to
an existing name merges; it carries a **Delete tag…** confirmation that names the
links removed) and **Manage categories** (Category select, **Preview**, then **Apply**).
Names with commas are rejected on these native routes only.

Guarded states, each exercised against the real server in
`tests/browser/workspace-vocabulary.spec.mjs`: stale epoch (`storage-changed`) and
`vocabulary-conflict` write nothing and re-read; an unknown outcome keeps the dialog
open, re-reads and never resends a delete; an unconfirmed Delete offers Retry that only
re-reads the impact; a failed list read shows Retry with "Nothing was changed."; a
failed Load more moves keyboard focus to its Retry; a write that lands after its dialog
was force-closed or left with Back still re-reads the lists and drops the cached Library
shelf; Manage categories reads up to 1,000 categories and says so when capped.
**Browse** on a tag or category row opens Library with exactly that Tags or Category
filter (the concept puts the name in the search text instead); the Prompts count equals
the number of cards Browse shows.

Test files: `tests/js/workspace-vocabulary-state.test.js` (strict parsing, stale and
abort handling, late writes after dispose, sentences), `tests/js/workspace-state.test.js`
(route/title), `tests/browser/workspace-vocabulary.spec.mjs` (screen, dialogs, Browse,
mobile, the concept geometry/token/control comparison and its mutation check),
`tests/browser/vocabulary-canonical-comparison.mjs` (inventory mapping),
`tests/browser/vocabulary-visual.spec.mjs` (paired capture artifacts) and the
`focused node tag suggestions include a newly created tag` case in
`tests/browser/focused-node.spec.mjs`. The fixture is
`tests/browser/workspace_vocabulary_fixture.py`. The screen is not host-facing, so no
`tests/comfyui` case was added.

```sh
node --test tests/js/workspace-vocabulary-state.test.js tests/js/workspace-state.test.js
npm run --prefix tools/vendor test:workflows -- \
  workspace-vocabulary.spec.mjs vocabulary-visual.spec.mjs focused-node.spec.mjs \
  --project chromium --project touch --project firefox --project webkit
```

Recorded deviations from the concept (maintainer acceptance): bounded lists with
Load more; title "Tags"; Manage categories adds a Category select and Preview before
Apply; "Apply" instead of "Apply in prototype"; Delete tag… inside Rename or merge;
Browse sets the exact filter; singular "1 positive prompt"; the production list appends
fixture-only tags after the concept's sample tags. Paired concept/production captures
for both themes and widths are attached as test artifacts; no PNG baseline is added
until acceptance. Not compared: hover states and the dialog focus ring (the page-level
keyboard ring and link ring offsets are asserted). No coverage floor was lowered;
`vocabulary-state.mjs`, `vocabulary.mjs` and `vocabulary-dialog.mjs` have additive
floors in `frontend-coverage-baseline.json`.
