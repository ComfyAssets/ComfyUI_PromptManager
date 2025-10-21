#!/usr/bin/env python3
"""
Standalone thumbnail generator that works with explicit paths.

This version doesn't require ComfyUI structure detection when paths are provided explicitly.
"""

import argparse
import asyncio
import os
import signal
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# Ensure script can find modules
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Global cancellation flag
CANCEL_EVENT = threading.Event()

def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully."""
    print("\n\n⚠️  Cancellation requested, cleaning up...")
    CANCEL_EVENT.set()


@dataclass
class ImageRecord:
    image_id: str
    path: Path
    media_type: Optional[str]


def detect_path_column(conn: sqlite3.Connection) -> str:
    """Detect the column name for image paths."""
    cursor = conn.execute("PRAGMA table_info(generated_images)")
    cols = [row[1] for row in cursor.fetchall()]

    for candidate in ("image_path", "path", "file_path"):
        if candidate in cols:
            return candidate

    for column in cols:
        if "path" in column.lower():
            return column

    raise RuntimeError("generated_images table is missing an image path column.")


def load_image_records(conn: sqlite3.Connection, batch_size: Optional[int] = None) -> List[ImageRecord]:
    """Load image records from database."""
    path_column = detect_path_column(conn)
    query = (
        f"SELECT id, {path_column}, COALESCE(media_type, '') FROM generated_images "
        f"WHERE {path_column} IS NOT NULL AND {path_column} != ''"
    )

    if batch_size:
        batch_size = max(1, int(batch_size))
        query += f" LIMIT {batch_size}"

    cursor = conn.execute(query, ())

    records: List[ImageRecord] = []
    for image_id, raw_path, media_type in cursor.fetchall():
        path = Path(str(raw_path)).expanduser()
        if path.exists():
            records.append(ImageRecord(str(image_id), path, media_type or None))

    return records


def generate_thumbnail_path(
    source_path: Path,
    thumbnail_dir: Path,
    size_name: str
) -> Path:
    """Generate path for a thumbnail file."""
    # Create size-specific directory
    size_dir = thumbnail_dir / size_name
    size_dir.mkdir(parents=True, exist_ok=True)

    # Use source file's name with size suffix
    stem = source_path.stem
    ext = source_path.suffix or '.jpg'

    # For videos, always use .jpg
    if source_path.suffix.lower() in ('.mp4', '.avi', '.mov', '.webm'):
        ext = '.jpg'

    return size_dir / f"{stem}{ext}"


def create_thumbnail_with_pillow(
    source_path: Path,
    target_path: Path,
    size: tuple[int, int]
) -> bool:
    """Create a thumbnail using Pillow."""
    try:
        from PIL import Image

        with Image.open(source_path) as img:
            # Convert RGBA to RGB if needed
            if img.mode in ('RGBA', 'LA', 'P'):
                # Create white background
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = background

            # Calculate thumbnail size preserving aspect ratio
            img.thumbnail(size, Image.Resampling.LANCZOS)

            # Save thumbnail
            img.save(target_path, quality=85, optimize=True)
            return True

    except Exception as e:
        print(f"  ❌ Failed to create thumbnail: {e}", file=sys.stderr)
        return False


async def process_batch(
    images: List[ImageRecord],
    thumbnail_dir: Path,
    sizes: Dict[str, tuple[int, int]],
    force: bool,
    parallel: int
) -> Dict[str, Any]:
    """Process a batch of images."""
    from concurrent.futures import ThreadPoolExecutor

    completed = 0
    failed = 0
    skipped = 0
    start_time = time.time()

    # Create task list
    tasks = []
    for record in images:
        if CANCEL_EVENT.is_set():
            break

        for size_name, dimensions in sizes.items():
            thumb_path = generate_thumbnail_path(record.path, thumbnail_dir, size_name)

            if not force and thumb_path.exists():
                skipped += 1
                continue

            tasks.append((record.path, thumb_path, dimensions))

    if not tasks:
        return {
            'completed': 0,
            'failed': 0,
            'skipped': skipped,
            'duration': 0
        }

    print(f"\n📸 Processing {len(tasks)} thumbnails from {len(images)} images...")

    # Process with thread pool
    with ThreadPoolExecutor(max_workers=parallel) as executor:
        futures = []
        for source_path, target_path, size in tasks:
            if CANCEL_EVENT.is_set():
                break

            future = executor.submit(create_thumbnail_with_pillow, source_path, target_path, size)
            futures.append(future)

        # Wait for completion with progress
        for i, future in enumerate(futures, 1):
            if CANCEL_EVENT.is_set():
                # Cancel remaining futures
                for f in futures[i:]:
                    f.cancel()
                break

            try:
                result = future.result(timeout=30)
                if result:
                    completed += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

            # Show progress
            if i % 10 == 0 or i == len(futures):
                pct = (i / len(futures)) * 100
                print(f"\r  [{pct:6.2f}%] {i}/{len(futures)} | ✓{completed} ✗{failed}", end='', flush=True)

    print()  # Newline after progress

    duration = time.time() - start_time
    return {
        'completed': completed,
        'failed': failed,
        'skipped': skipped,
        'duration': duration
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate PromptManager thumbnails (standalone version)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This is a standalone version that works with explicit paths.

Examples:
  %(prog)s --db-path /path/to/prompts.db --output-dir /path/to/thumbnails
  %(prog)s --db-path ~/ComfyUI/user/default/PromptManager/prompts.db --sizes small medium
  %(prog)s --db-path prompts.db --parallel 8 --force
        """
    )

    parser.add_argument(
        '--db-path',
        type=str,
        required=True,
        help='Path to the PromptManager database (prompts.db)'
    )

    parser.add_argument(
        '--output-dir',
        type=Path,
        help='Directory for thumbnails (default: database_dir/thumbnails)'
    )

    parser.add_argument(
        '--sizes',
        nargs='+',
        choices=['small', 'medium', 'large', 'xlarge'],
        default=['small', 'medium', 'large'],
        help='Thumbnail sizes to generate (default: small medium large)'
    )

    parser.add_argument(
        '--parallel',
        type=int,
        default=4,
        help='Number of parallel workers (default: 4, use 0 for CPU count)'
    )

    parser.add_argument(
        '--batch-size',
        type=int,
        help='Process images in batches of this size'
    )

    parser.add_argument(
        '--force',
        action='store_true',
        help='Regenerate thumbnails even if they exist'
    )

    parser.add_argument(
        '-y', '--yes',
        action='store_true',
        help='Skip confirmation prompt'
    )

    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> None:
    """Main async function."""
    # Expand database path
    db_path = Path(args.db_path).expanduser().resolve()
    if not db_path.exists():
        print(f"❌ Error: Database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    # Determine output directory
    if args.output_dir:
        thumbnail_dir = args.output_dir.expanduser().resolve()
    else:
        # Default to database_dir/thumbnails
        thumbnail_dir = db_path.parent / 'thumbnails'

    # Create output directory
    thumbnail_dir.mkdir(parents=True, exist_ok=True)
    print(f"✓ Using thumbnail directory: {thumbnail_dir}")

    # Connect to database
    conn = sqlite3.connect(db_path, check_same_thread=False)

    # Enable optimizations
    conn.execute("PRAGMA cache_size = -64000")  # 64MB cache
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA mmap_size = 268435456")  # 256MB memory-mapped I/O

    # Load images
    images = load_image_records(conn, args.batch_size)
    conn.close()

    if not images:
        print("No images found in database.")
        return

    print(f"✓ Found {len(images):,} images in database")

    # Define sizes
    SIZE_MAP = {
        'small': (256, 256),
        'medium': (512, 512),
        'large': (1024, 1024),
        'xlarge': (2048, 2048)
    }

    sizes = {name: SIZE_MAP[name] for name in args.sizes}

    # Determine parallel workers
    parallel = args.parallel
    if parallel == 0:
        parallel = os.cpu_count() or 4

    # Show plan
    max_thumbnails = len(images) * len(sizes)
    print(f"\n📸 Thumbnail Generation Plan:")
    print(f"  • Images to process: {len(images):,}")
    print(f"  • Sizes: {', '.join(args.sizes)}")
    print(f"  • Max thumbnails: {max_thumbnails:,}")
    print(f"  • Parallel workers: {parallel}")
    print(f"  • Force regenerate: {args.force}")

    if not args.yes:
        response = input("\nProceed? [Y/n] ")
        if response.lower() in ('n', 'no'):
            print("Cancelled.")
            return

    # Set up signal handler
    signal.signal(signal.SIGINT, signal_handler)

    # Process
    print("\n🚀 Starting generation (press Ctrl+C to cancel)...")

    try:
        summary = await process_batch(images, thumbnail_dir, sizes, args.force, parallel)

        # Show summary
        if not CANCEL_EVENT.is_set():
            print(f"\n✅ Thumbnail generation complete!")
        else:
            print(f"\n⚠️  Generation cancelled")

        print(f"\n📊 Summary:")
        print(f"  • Completed: {summary['completed']:,}")
        print(f"  • Failed: {summary['failed']:,}")
        print(f"  • Skipped: {summary['skipped']:,}")
        print(f"  • Duration: {summary['duration']:.1f} seconds")

        if summary['completed'] > 0:
            rate = summary['completed'] / summary['duration']
            print(f"  • Rate: {rate:.1f} thumbnails/second")

    except KeyboardInterrupt:
        print("\n\n⚠️  Cancelled by user")


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Main entry point."""
    args = parse_args(argv)

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print('\n⚠️  Cancelled by user.')
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()