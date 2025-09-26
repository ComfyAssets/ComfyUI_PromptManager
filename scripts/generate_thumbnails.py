#!/usr/bin/env python3
"""High-performance thumbnail generator for PromptManager databases.

This script can be invoked from the command line to regenerate every thumbnail
stored in the PromptManager database using the same logic as the runtime
service, but without waiting for the web UI.  It is intended for power users
who want to rebuild their thumbnail library as fast as their machine allows.

Example usage::

    python -m scripts.generate_thumbnails --parallel 0 --force

The ``--parallel`` flag controls the worker count (``0`` = unlimited /
ThreadPool default).  ``--force`` forces regeneration even when a thumbnail
already exists on disk.  By default, all standard PromptManager sizes are
produced so the output is a 1:1 match with the UI.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

# Local imports live inside the repository; make sure our path is present.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import config
from src.services.enhanced_thumbnail_service import (  # noqa: E402
    EnhancedThumbnailService,
    ThumbnailTask,
    ThumbnailStatus,
)
from utils.cache import MemoryCache  # noqa: E402
from utils.file_system import get_file_system  # noqa: E402


# ---------------------------------------------------------------------------
# Helper infrastructure
# ---------------------------------------------------------------------------


class SimpleCache:
    """In-memory cache stub that satisfies the thumbnail service contract."""

    def __init__(self) -> None:
        self._cache = MemoryCache()
        self._caches = {'thumbnails': self._cache}

    def get(self, key: str, default: Any = None) -> Any:
        return self._cache.get(key, default)

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        self._cache.set(key, value, ttl=ttl)

    def get_cache(self, name: str) -> Optional[Any]:
        """Get a named cache."""
        return self._caches.get(name)

    def register_cache(self, name: str, cache: Any) -> None:
        """Register a named cache."""
        self._caches[name] = cache

    def clear_pattern(self, pattern: str) -> int:
        if hasattr(self._cache, "clear_pattern"):
            return self._cache.clear_pattern(pattern)
        self._cache.clear()
        return 0

    def clear_all(self) -> None:
        self._cache.clear()


@dataclass
class ImageRecord:
    """Lightweight record describing a single image entry."""

    image_id: str
    path: Path
    media_type: Optional[str]


class ThumbnailDatabaseAdapter:
    """Minimal async adapter implementing the methods the service expects."""

    def __init__(self, db_path: Path, conn: sqlite3.Connection) -> None:
        self.db_path = db_path
        self._conn = conn
        self._columns = self._load_columns()
        self._size_columns = {
            'small': 'thumbnail_small_path',
            'medium': 'thumbnail_medium_path',
            'large': 'thumbnail_large_path',
            'xlarge': 'thumbnail_xlarge_path',
        }

    def _load_columns(self) -> Dict[str, bool]:
        cursor = self._conn.execute("PRAGMA table_info(generated_images)")
        columns = {row[1]: True for row in cursor.fetchall()}
        if not columns:
            raise RuntimeError(
                "generated_images table not found. Ensure you have migrated to PromptManager v2."
            )
        return columns

    async def batch_update_thumbnails(self, updates: Sequence[Dict[str, Any]]) -> None:
        if not updates:
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._apply_updates, list(updates))

    # Synchronous portion executed in a worker thread
    def _apply_updates(self, updates: Sequence[Dict[str, Any]]) -> None:
        timestamp = datetime.utcnow().isoformat(timespec='seconds')
        with self._conn:  # context manager commits automatically
            for update in updates:
                image_id = update.get('image_id')
                thumb_path = Path(update.get('thumbnail_path', ''))
                if not image_id or not thumb_path:
                    continue

                size_name = thumb_path.parent.name
                column = self._size_columns.get(size_name)
                if column and column in self._columns:
                    self._conn.execute(
                        f"UPDATE generated_images SET {column} = ?, thumbnails_generated_at = ? WHERE id = ?",
                        (str(thumb_path), timestamp, image_id),
                    )

    async def get_image(self, image_id: str) -> Optional[Dict[str, Any]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_image_sync, image_id)

    def _get_image_sync(self, image_id: str) -> Optional[Dict[str, Any]]:
        cursor = self._conn.execute(
            "SELECT image_path, file_path, path FROM generated_images WHERE id = ?",
            (image_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        image_path, file_path, legacy_path = row
        resolved = image_path or file_path or legacy_path
        if not resolved:
            return None
        return {'path': resolved}


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def detect_path_column(conn: sqlite3.Connection) -> str:
    cursor = conn.execute("PRAGMA table_info(generated_images)")
    cols = [row[1] for row in cursor.fetchall()]
    for candidate in ("image_path", "path", "file_path"):
        if candidate in cols:
            return candidate
    for column in cols:
        if "path" in column.lower():
            return column
    raise RuntimeError(
        "generated_images table is missing an image path column."
        " If you are still on v1, provide --db-path pointing to your v2 prompts.db."
    )


def load_image_records(conn: sqlite3.Connection) -> List[ImageRecord]:
    path_column = detect_path_column(conn)
    cursor = conn.execute(
        f"SELECT id, {path_column}, COALESCE(media_type, '') FROM generated_images "
        f"WHERE {path_column} IS NOT NULL AND {path_column} != ''"
    )

    records: List[ImageRecord] = []
    for image_id, raw_path, media_type in cursor.fetchall():
        path = Path(str(raw_path)).expanduser()
        records.append(ImageRecord(str(image_id), path, media_type or None))
    return records


def build_thumbnail_tasks(
    service: EnhancedThumbnailService,
    images: Iterable[ImageRecord],
    sizes: Sequence[str],
    force: bool,
) -> List[ThumbnailTask]:
    size_map = service.base_generator.SIZES
    tasks: List[ThumbnailTask] = []

    for record in images:
        source_path = record.path
        if not source_path.exists():
            continue

        is_video = service.ffmpeg.is_video(source_path)
        if is_video and not service.ffmpeg.ffmpeg_path:
            # Cannot render video thumbnails without ffmpeg; skip until available.
            continue

        suffix = source_path.suffix.lower().lstrip('.') or 'jpg'
        if is_video:
            suffix = 'jpg'

        for size_name in sizes:
            dims = size_map.get(size_name)
            if not dims:
                continue

            thumb_path = service.get_thumbnail_path(source_path, size_name)
            if not force and thumb_path.exists():
                continue

            tasks.append(
                ThumbnailTask(
                    image_id=record.image_id,
                    source_path=source_path,
                    thumbnail_path=thumb_path,
                    size=dims,
                    format=suffix,
                    is_video=is_video,
                )
            )

    return tasks


def format_summary(summary: Dict[str, Any]) -> str:
    completed = summary.get('completed', 0)
    failed = summary.get('failed', 0)
    skipped = summary.get('skipped', 0)
    duration = summary.get('duration_seconds', summary.get('duration', 0))
    return (
        f"Completed: {completed:,}  Failed: {failed:,}  Skipped: {skipped:,}  "
        f"Time: {duration:.1f}s"
    )


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


async def generate_thumbnails(args: argparse.Namespace) -> None:
    if args.comfy_root:
        os.environ['COMFYUI_PATH'] = str(args.comfy_root.expanduser().resolve())

    fs = get_file_system()
    db_path = Path(args.db_path).expanduser() if args.db_path else Path(fs.get_database_path('prompts.db'))
    if not db_path.exists():
        raise FileNotFoundError(
            f"PromptManager database not found at {db_path}. Run from a machine with ComfyUI Prompts data."
        )

    conn = sqlite3.connect(db_path, check_same_thread=False)
    adapter = ThumbnailDatabaseAdapter(db_path, conn)
    cache = SimpleCache()

    if args.parallel is not None:
        config.set('thumbnail.max_parallel', args.parallel)

    service = EnhancedThumbnailService(adapter, cache)
    # Override concurrency if the user provided an explicit value
    if args.parallel is not None:
        service.set_parallel_workers(args.parallel)

    sizes = args.sizes or list(service.base_generator.SIZES.keys())

    images = load_image_records(conn)
    tasks = build_thumbnail_tasks(service, images, sizes, force=args.force)

    if not tasks:
        print("No thumbnails need to be generated. Nothing to do.")
        return

    print(f"Discovered {len(tasks):,} thumbnail operations across {len(images):,} images.")

    def progress_callback(progress: Dict[str, Any]) -> None:
        current = progress.get('current_file', '') or ''
        pct = progress.get('percentage', 0)
        processed = progress.get('processed', 0)
        total = progress.get('total', 0)
        line = f"[{pct:6.2f}%] {processed}/{total} {current}"
        print(f"\r{line:<80}", end='', flush=True)

    summary = await service.generate_batch(tasks, progress_callback=progress_callback)
    print()  # newline after progress bar
    print(format_summary(summary))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate PromptManager thumbnails from the CLI")
    parser.add_argument(
        '--sizes',
        nargs='+',
        choices=['small', 'medium', 'large', 'xlarge'],
        help='Thumbnail sizes to generate (default: all)',
    )
    parser.add_argument(
        '--comfy-root',
        type=Path,
        help='Path to the ComfyUI installation root (if auto-detection fails)',
    )
    parser.add_argument(
        '--db-path',
        type=str,
        help='Explicit path to prompts.db (overrides auto-detected location)',
    )
    parser.add_argument(
        '--parallel',
        type=int,
        default=None,
        help='Worker count. Use 0 for unlimited (ThreadPool default).',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Regenerate thumbnails even if the target file already exists.',
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    try:
        asyncio.run(generate_thumbnails(args))
    except KeyboardInterrupt:
        print('\nCancelled by user.')


if __name__ == '__main__':
    main()
