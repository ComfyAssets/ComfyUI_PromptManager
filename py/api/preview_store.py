"""Where previews live on disk (#320): the one resolver.

Every mapping between an image source (a "root"), one of its media files and
that file's preview on disk goes through this module. URL text stays in
``thumbnail_settings`` (``thumbnail_rel``: ``thumbnails/<rel>_thumb<ext>``,
the shape the serve route, ``inspection-state.mjs`` and ``admin.js`` share);
this module answers where such a preview is on disk.

The store of a source is where its previews live. With the default location
(mode ``source``, "Inside each source") it is ``<source>/thumbnails``, exactly
the folder every caller joined by hand before, so nothing moves.

Writes and the switch: every preview writer takes ``write_guard`` when it
starts (it records the location's ``generation``); the final rename runs
under ``write_lock`` and only when the generation is unchanged and no hold
applies (``thumbnail_encode.replace_unless_held``). Changing the location
bumps the generation under the same lock, so no write that began before can
land in the old store afterwards. Nothing reads or commits config while
``write_lock`` is held. The hold check runs under it too, so hold checks take
no lock (the job registry's ``running()`` is a lock-free snapshot) and
``bump_generation`` is never called with a registry or config lock held.

All functions are blocking: call them off the event loop.
"""

import errno
import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from . import preview_location, thumbnail_settings

try:
    from ...utils.media_folders import PREVIEW_FOLDER
except ImportError:  # tests import the package top-level
    from utils.media_folders import PREVIEW_FOLDER

logger = logging.getLogger("prompt_manager.thumbnails")

SOURCE_MODE = preview_location.SOURCE_MODE
FOLDER_MODE = preview_location.FOLDER_MODE
# The probe checks the folder this often, and a check (or a store call)
# taking longer than the budget marks it unreachable.
PROBE_INTERVAL = 5.0
IO_BUDGET = 2.0
PROBE_THREAD = "pm-preview-probe"


@dataclass(frozen=True)
class Location:
    """One snapshot of the thumbnail location (take one per request or job)."""

    mode: str = SOURCE_MODE
    path: str | None = None
    available: bool = True
    problem: str | None = None
    generation: int = 0


write_lock = threading.Lock()
_generation = 0
# The runtime location: the parsed setting, availability and the marker keys
# last read. Swapped as a whole; readers take one snapshot.
_state_lock = threading.Lock()
_parsed = preview_location.ParsedLocation()
_available = True
_problem = None
_keys = {}
_key_cache = {}
_clock = time.monotonic


def current():
    """The thumbnail location now."""
    with _state_lock:
        parsed, available, problem, generation_now = (
            _parsed,
            _available,
            _problem,
            _generation,
        )
    if parsed.mode == SOURCE_MODE:
        return Location(generation=generation_now)
    return Location(FOLDER_MODE, parsed.path, available, problem, generation_now)


def setting():
    """The parsed setting in use (``preview_location.ParsedLocation``)."""
    return _parsed


def generation():
    return _generation


def bump_generation():
    """Invalidate every write that started before (under ``write_lock``)."""
    global _generation
    with write_lock:
        _generation += 1
        return _generation


def _canonical(path):
    """Resolved and case-folded, so paths compare on every OS."""
    return Path(os.path.normcase(os.path.realpath(str(path))))


def _within(path, folder):
    return _canonical(path).is_relative_to(_canonical(folder))


# ── the setting and availability ─────────────────────────────────────


def configure(value, *, context=None, check=True):
    """Use the ``thumbnails.location`` *value* (config.json) from now on.

    A folder location is validated against *context* (``startup`` rules: a
    problem other than an unreachable folder makes it ``invalid``), then
    checked once; the probe keeps checking it. Swapped under ``write_lock``
    with a generation bump, so no write that started before lands.
    """
    global _parsed, _available, _problem, _generation
    parsed = preview_location.parse_location(value)
    problem = parsed.problem
    if parsed.mode == FOLDER_MODE and problem is None and context is not None:
        code = preview_location.location_problem(
            parsed.path, purpose="startup", context=context
        )
        if code == "location-unavailable":
            problem = "unreachable"
        elif code is not None:
            problem = "invalid"
            parsed = preview_location.ParsedLocation(
                mode=FOLDER_MODE,
                path=parsed.path,
                id=parsed.id,
                install=parsed.install,
                cleanup=parsed.cleanup,
                problem="invalid",
            )
    with write_lock:
        with _state_lock:
            _parsed = parsed
            _available = parsed.mode == SOURCE_MODE
            _problem = problem if parsed.mode == FOLDER_MODE else None
            _keys.clear()
            _key_cache.clear()
        _generation += 1
    if parsed.mode == FOLDER_MODE:
        if check and parsed.problem is None:
            refresh()
        _probe.start()
    else:
        _probe.stop()
    return current()


def refresh():
    """Check the folder now (bounded by IO_BUDGET); returns availability."""
    parsed = _parsed
    if parsed.mode != FOLDER_MODE or parsed.problem is not None:
        return parsed.mode == SOURCE_MODE
    try:
        available, problem, marker = _probe.check(parsed.path, parsed.id)
    except (OSError, TimeoutError, RuntimeError):
        available, problem, marker = False, "unreachable", None
    _set_state(parsed, available, problem, marker)
    return available


def _set_keys(parsed, marker):
    """Take the keys of a marker just written (availability unchanged)."""
    with _state_lock:
        if _parsed is not parsed:
            return
        keys = dict((marker or {}).get("keys") or {})
        if keys != _keys:
            _keys.clear()
            _keys.update(keys)
            _key_cache.clear()


def set_cleanup(entries):
    """The setting's ``cleanup`` after a removal was committed."""
    global _parsed
    with _state_lock:
        _parsed = replace(_parsed, cleanup=tuple(entries))


def mark_invalid():
    """The setting cannot be used (a startup check that failed): folder
    mode stays in force, unavailable, so nothing is written anywhere."""
    global _parsed, _available, _problem
    with _state_lock:
        if _parsed.mode != FOLDER_MODE:
            _parsed = preview_location.ParsedLocation(
                mode=FOLDER_MODE, problem="invalid"
            )
        else:
            _parsed = replace(_parsed, problem="invalid")
        _available, _problem = False, "invalid"


def _set_state(parsed, available, problem, marker):
    global _available, _problem
    with _state_lock:
        if _parsed is not parsed:
            return  # the setting changed meanwhile
        _available, _problem = available, problem
        keys = dict((marker or {}).get("keys") or {})
        if keys != _keys:
            _keys.clear()
            _keys.update(keys)
            _key_cache.clear()


def note_failure():
    """A store call failed or stalled: unreachable until the probe finds the
    folder again (requests do no store I/O meanwhile)."""
    global _available, _problem
    if _parsed.mode != FOLDER_MODE:
        return
    with _state_lock:
        _available, _problem = False, "unreachable"
    _probe.wake()


@contextmanager
def store_io():
    """Around a store call in folder mode: an I/O error (other than a file
    that is not there) or a call over IO_BUDGET marks the folder
    unreachable."""
    if _parsed.mode != FOLDER_MODE:
        yield
        return
    started = _clock()
    try:
        yield
    except OSError as error:
        if not isinstance(error, (FileNotFoundError, NotADirectoryError)):
            note_failure()
        raise
    finally:
        if _clock() - started > IO_BUDGET:
            note_failure()


class _Probe:
    """The background check of the thumbnail folder (one thread, one check
    at a time; a stuck check is left to finish on its own thread)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._checking = None

    def check(self, path, expected_id):
        """``folder_state`` within IO_BUDGET; raises TimeoutError.

        The answer comes from a check that started after this call: one
        already running may have looked before what the caller just did, so
        it is waited for (never run twice at once; a stuck one times out here)
        and followed by a new one."""
        key = (path, expected_id)
        with self._lock:
            earlier = self._running(key)
            pending = None if earlier else self._start_check(path, expected_id)
        if earlier:
            self._wait(earlier)
            with self._lock:
                # One another caller started meanwhile is new enough to join.
                pending = self._running(key) or self._start_check(path, expected_id)
        self._wait(pending)
        with self._lock:
            if self._checking is pending:
                self._checking = None
        if "error" in pending[2]:
            raise pending[2]["error"]
        return pending[2]["value"]

    def _running(self, key):
        """The check of *key* still running (lock held), else None."""
        pending = self._checking
        if pending is None or pending[0] != key or pending[1].is_set():
            return None
        return pending

    @staticmethod
    def _wait(pending):
        if not pending[1].wait(IO_BUDGET):
            raise TimeoutError("the thumbnail folder did not answer in time")

    def _start_check(self, path, expected_id):
        """Start one check on its own thread (lock held)."""
        result = {}
        done = threading.Event()

        def run():
            try:
                result["value"] = preview_location.folder_state(path, expected_id)
            except BaseException as error:  # check() re-raises it in the caller
                result["error"] = error
            finally:
                done.set()

        pending = ((path, expected_id), done, result)
        self._checking = pending
        threading.Thread(target=run, name=PROBE_THREAD + "-check", daemon=True).start()
        return pending

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            # Each thread has its own stop: a stopped one never resumes.
            stop = self._stop = threading.Event()
            thread = threading.Thread(
                target=self._run, args=(stop,), name=PROBE_THREAD, daemon=True
            )
            try:
                thread.start()
            except RuntimeError:
                return  # refused at interpreter exit: no probe
            self._thread = thread

    def _run(self, stop):
        while not stop.is_set():
            self._wake.wait(PROBE_INTERVAL)
            self._wake.clear()
            if stop.is_set():
                return
            try:
                refresh()
            except Exception:  # one failed check never stops the probe
                logger.debug("Thumbnail folder check failed", exc_info=True)

    def wake(self):
        self._wake.set()

    def stop(self, timeout=2.0):
        """Stop the probe; True when its thread ended."""
        with self._lock:
            thread, self._thread = self._thread, None
        self._stop.set()
        self._wake.set()
        if thread is None or thread is threading.current_thread():
            return True
        thread.join(timeout)
        return not thread.is_alive()


_probe = _Probe()


def stop_probe(timeout=2.0):
    """Stop the folder probe (the package teardown, tests)."""
    return _probe.stop(timeout)


# ── stores ───────────────────────────────────────────────────────────


def source_folder(root):
    """``<source>/thumbnails``: the store in source mode, a leftover in
    folder mode."""
    return Path(root) / PREVIEW_FOLDER


def store_container(root, location=None):
    """The folder a store of *root* must stay inside: the source itself, or
    the thumbnail folder in folder mode."""
    location = location or current()
    if location.mode == FOLDER_MODE:
        return Path(location.path) if location.path else None
    return Path(root)


def _identity(root):
    return os.path.normcase(os.path.realpath(str(root)))


def _lookup_key(root):
    spelling = str(root)
    with _state_lock:
        if spelling in _key_cache:
            return _key_cache[spelling]
        keys = dict(_keys)
    key = preview_location.find_key(
        keys,
        spelling,
        _identity(root),
        preview_location.file_identity(root),
    )
    if key is not None:
        with _state_lock:
            _key_cache[spelling] = key
    return key


def store_dir(root, location=None):
    """The store of *root*: ``<source>/thumbnails``, or in folder mode
    ``<folder>/<key>`` (None while the folder is unavailable or before the
    source has a key: a reader then finds no preview)."""
    location = location or current()
    if location.mode == SOURCE_MODE:
        return Path(root) / PREVIEW_FOLDER
    if not location.available or not location.path:
        return None
    key = _lookup_key(root)
    return None if key is None else _key_folder(location.path, key)


def _key_folder(folder, key):
    """``<folder>/<key>``, only for a key that is one folder name directly
    below the thumbnail folder (a marker is data, never a path)."""
    if not preview_location.valid_key(key):
        return None
    store = Path(folder) / key
    if store.parent != Path(folder) or store.name != key:
        return None
    return store


def ensure_store(root, location=None, *, create=False):
    """``store_dir`` for a writer: in folder mode a source without a key gets
    one (recorded in the marker); with *create* its key folder is made (never
    the thumbnail folder itself). None while the folder is unavailable, and
    for a key folder that is a link, junction or reparse point: nothing is
    planned, written or removed through it."""
    location = location or current()
    store = store_dir(root, location)
    if location.mode == SOURCE_MODE or (store is None and not location.available):
        return store
    if store is None:
        store = _assign_key(root, location)
        if store is None:
            return None
    try:
        with store_io():
            linked = preview_location.is_link_like(store)
    except OSError:
        return None
    if linked:
        logger.warning(f"Skipping the thumbnail folder {store}: it is a link")
        return None
    if create:
        try:
            with store_io():
                os.mkdir(store)
        except FileExistsError:
            pass  # already there: another writer made the key folder
        except OSError:
            return None
    return store


def _assign_key(root, location):
    parsed = _parsed
    if location.path != parsed.path:
        return None
    spelling = str(root)
    identity = _identity(root)
    dev_ino = preview_location.file_identity(root)
    assigned = {}

    def add(marker):
        if marker.get("id") != parsed.id:
            raise ValueError("another folder's marker")  # marker-mismatch
        keys = marker["keys"]
        key = preview_location.find_key(keys, spelling, identity, dev_ino)
        if key is None:
            taken = {k: v.get("identity") for k, v in keys.items()}
            key = preview_location.source_key(spelling, identity, taken)
            keys[key] = {
                "path": spelling,
                "identity": identity,
                "dev_ino": dev_ino,
                "install": parsed.install,
            }
        assigned["key"] = key

    try:
        with write_lock, store_io():
            marker = preview_location.write_marker(location.path, add)
    except (OSError, ValueError):
        return None
    _set_keys(parsed, marker)
    return _key_folder(location.path, assigned["key"])


def may_write(path, location=None):
    """The one ownership check every preview write and delete passes.

    Source mode: True (the callers' own root rules apply, as before). Folder
    mode: *path* lies below a source key folder of the thumbnail folder,
    lexically and resolved, and no link, junction or reparse point sits
    anywhere from the key folder down to it (the file itself included).
    """
    location = location or current()
    if location.mode == SOURCE_MODE:
        return True
    if not location.path:
        return False
    folder = Path(os.path.abspath(location.path))
    path = Path(os.path.abspath(path))
    try:
        relative = path.relative_to(folder)
    except ValueError:
        return False
    if len(relative.parts) < 2 or ".." in relative.parts:
        return False
    store = _key_folder(folder, relative.parts[0])
    if store is None or not _within(path, folder):
        return False
    try:
        return _no_link_below(store, path)
    except OSError:
        note_failure()
        return False


def make_parent(dst, location=None):
    """Make the folders a preview *dst* is written into. In folder mode only
    folders below the thumbnail folder are made, never the folder itself,
    and only while it holds this install's marker (an unplugged drive or an
    unmounted mount point raises FileNotFoundError); a *dst* that does not
    pass ``may_write`` raises PermissionError."""
    location = location or current()
    dst = Path(dst)
    if location.mode == SOURCE_MODE or not location.path:
        if location.mode != SOURCE_MODE:
            raise FileNotFoundError(errno.ENOENT, "thumbnail folder unavailable")
        dst.parent.mkdir(parents=True, exist_ok=True)
        return
    folder = Path(os.path.abspath(location.path))
    with store_io():
        if not location.available or not folder.is_dir():
            raise FileNotFoundError(errno.ENOENT, "thumbnail folder unavailable")
        marker = preview_location.read_marker(str(folder))
        if marker is None or marker.get("id") != _parsed.id:
            note_failure()
            raise FileNotFoundError(errno.ENOENT, "thumbnail folder unavailable")
        if not may_write(dst, location):
            raise PermissionError(errno.EACCES, "outside the thumbnail folder")
        current_folder = folder
        for part in Path(os.path.abspath(dst.parent)).relative_to(folder).parts:
            current_folder = current_folder / part
            try:
                os.mkdir(current_folder)
            except FileExistsError:
                pass  # already there: another writer made this folder
            if preview_location.is_link_like(current_folder):
                raise PermissionError(errno.EACCES, "a link in the thumbnail folder")


def store_is_link(store, location=None):
    """Whether a store folder is a link (a junction or reparse point too in
    folder mode): it is skipped by sweeps and refused by clears."""
    location = location or current()
    if location.mode == SOURCE_MODE:
        return Path(store).is_symlink()
    return preview_location.is_link_like(store)


def given_store(root, store):
    """A caller's explicit store argument: the default spelling
    (``<root>/thumbnails``) means the resolver's store; another folder (only
    tests pass one) is used as given."""
    store = Path(store)
    if store == Path(root) / PREVIEW_FOLDER:
        return store_dir(root)
    return store


def preview_path(root, media_rel, ext=None, location=None, *, create=False):
    """Where the preview of *media_rel* (relative to *root*) is, or is to be
    written (*create*: a writer, which gives a new source its key), in *ext*
    (the extension written now when None)."""
    if create:
        store = ensure_store(root, location)
    else:
        store = store_dir(root, location)
    if store is None:
        return None
    if ext is None:
        from .thumbnail_encode import writer_settings

        ext = writer_settings().extension
    return store / (thumbnail_settings.preview_stem(media_rel) + ext)


def request_path(root, parts, location=None):
    """The disk path of the URL parts ``("thumbnails", ...)``, or None when
    *parts* is not under an exact ``thumbnails`` first part or the store has
    no place for it. The text is checked before (``is_preview_text``)."""
    parts = tuple(parts)
    if len(parts) < 2 or parts[0] != PREVIEW_FOLDER:
        return None
    store = store_dir(root, location)
    if store is None:
        return None
    return store.joinpath(*parts[1:])


def _no_link_below(store, path):
    """No folder from *store* down to *path* (and *store* itself) is a link,
    junction or reparse point (lexical walk, one lstat each)."""
    store = Path(os.path.abspath(store))
    path = Path(os.path.abspath(path))
    try:
        relative = path.relative_to(store)
    except ValueError:
        return False
    current = store
    for part in ("",) + relative.parts:
        current = current / part if part else current
        if preview_location.is_link_like(current):
            return False
    return True


def owns(root, path, location=None):
    """Whether *path* lies inside the store of *root*, resolved: the store
    stays inside its container and no link takes *path* out of the store.
    In folder mode no link, junction or reparse point may sit anywhere from
    the store down to *path*."""
    location = location or current()
    store = store_dir(root, location)
    if store is None:
        return False
    container = store_container(root, location)
    if not (_within(store, container) and _within(path, store)):
        return False
    if location.mode == SOURCE_MODE:
        return True
    return may_write(path, location)


# ── preview text ─────────────────────────────────────────────────────


def is_preview_text(filepath):
    """URL text naming a preview: ``thumbnails/<...>/<name>_thumb<ext>``.

    Text only, before any join: no backslash, drive or stream colon, and no
    empty, ``.`` or ``..`` segment; ``thumbnails`` exactly as the first part
    and nowhere else among the folders; the extension, in any case, one a
    preview may carry (``X_thumb.JPG`` of an older release included).
    """
    if not filepath or "\\" in filepath or ":" in filepath:
        return False
    parts = filepath.split("/")
    if len(parts) < 2 or parts[0] != PREVIEW_FOLDER:
        return False
    if any(part in ("", ".", "..") for part in parts):
        return False
    if PREVIEW_FOLDER in parts[1:-1]:
        return False
    stem, extension = os.path.splitext(parts[-1])
    return (
        stem.endswith(thumbnail_settings.THUMB_SUFFIX)
        and len(stem) > len(thumbnail_settings.THUMB_SUFFIX)
        and extension.lower() in thumbnail_settings.THUMBNAIL_EXTENSIONS
    )


# ── writes ───────────────────────────────────────────────────────────


class _WriteGuard:
    """``guard()`` for one preview write: true while the location has not
    changed since the write started and *hold* (if any) allows it."""

    __slots__ = ("generation", "_hold")

    def __init__(self, hold=None):
        self.generation = _generation
        self._hold = hold

    def __call__(self):
        if self.generation != _generation:
            return False
        return self._hold is None or bool(self._hold())


def write_guard(hold=None):
    """Take when a preview write starts; hand to its final rename."""
    if isinstance(hold, _WriteGuard):
        return hold
    return _WriteGuard(hold)


@contextmanager
def committed(guard=None):
    """Around a preview's final rename: holds ``write_lock`` and yields
    whether the write may land (``guard()``, re-checked under the lock)."""
    with write_lock:
        yield guard is None or bool(guard())
