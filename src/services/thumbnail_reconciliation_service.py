"""Thumbnail Reconciliation Service for comprehensive validation and repair.

This service handles:
- Comprehensive scanning of thumbnails across database and disk
- Detection of broken links, orphaned files, and missing thumbnails
- Automated repair operations (fixing links, linking orphans, generating missing)
- Progress tracking for all operations
"""

import asyncio
import hashlib
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .enhanced_thumbnail_service import EnhancedThumbnailService, ThumbnailTask
from utils.cache import CacheManager
from utils.logging import get_logger

try:
    import sys
    parent_dir = Path(__file__).parent.parent.parent
    if str(parent_dir) not in sys.path:
        sys.path.insert(0, str(parent_dir))

    from ..database import PromptDatabase as Database
except ImportError:
    import importlib.util

    def _load_database_module():
        """Safely import the database module."""
        module_name = "promptmanager_database_operations"
        if module_name in sys.modules:
            return sys.modules[module_name]

        module_path = Path(__file__).resolve().parent.parent / "database" / "operations.py"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        sys.modules[module_name] = module
        return module

    database_module = _load_database_module()
    Database = database_module.PromptDatabase

logger = get_logger("promptmanager.thumbnail_reconciliation")


@dataclass
class ScanProgress:
    """Progress tracking for scan operations."""
    phase: str  # 'database_validation', 'disk_scan', 'orphan_matching'
    current: int
    total: int
    percentage: float
    message: str = ""


@dataclass
class ScanResults:
    """Results from comprehensive thumbnail scan."""
    total_images: int
    categories: Dict[str, int]  # valid, broken_links, linkable_orphans, missing
    breakdown: Dict[str, List[Dict]]
    true_orphans: Dict[str, Any]  # count, size_bytes, sample_files
    estimated_time_seconds: int


class ThumbnailReconciliationService:
    """Service for comprehensive thumbnail validation and reconciliation."""

    def __init__(self, db: Database, cache: CacheManager, thumbnail_service: EnhancedThumbnailService):
        """Initialize reconciliation service.

        Args:
            db: Database instance
            cache: Cache manager instance
            thumbnail_service: Enhanced thumbnail service for generation
        """
        self.db = db
        self.cache = cache
        self.thumbnail_service = thumbnail_service
        self.thumbnail_dir = thumbnail_service.thumbnail_dir

        logger.info("ThumbnailReconciliationService initialized")

    async def comprehensive_scan(
        self,
        sizes: List[str],
        sample_limit: int = 6,
        progress_callback: Optional[Callable] = None
    ) -> ScanResults:
        """Perform comprehensive 3-phase scan of thumbnails.

        Phase 1: Database Validation - Check all DB records
        Phase 2: Disk File Scan - Scan all thumbnail files on disk
        Phase 3: Orphan Matching - Match orphaned files to parent images

        Args:
            sizes: List of thumbnail sizes to check
            sample_limit: Max sample files to return for true orphans
            progress_callback: Optional callback for progress updates

        Returns:
            ScanResults with categorized thumbnails
        """
        logger.info(f"Starting comprehensive scan for sizes: {sizes}")

        categories = {
            'valid': [],
            'broken_links': [],
            'linkable_orphans': [],
            'missing': [],
            'true_orphans': []
        }

        # Phase 1: Database Validation
        logger.info("Phase 1: Database validation")
        total_images = await self._count_images()

        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, image_path, filename,
                   thumbnail_small_path, thumbnail_medium_path,
                   thumbnail_large_path, thumbnail_xlarge_path
            FROM generated_images
            WHERE image_path IS NOT NULL AND image_path != ''
        """)

        all_images = cursor.fetchall()
        conn.close()

        for idx, row in enumerate(all_images):
            image_id, image_path, filename = row[0], row[1], row[2]
            thumb_paths = {
                'small': row[3],
                'medium': row[4],
                'large': row[5],
                'xlarge': row[6]
            }

            source_path = Path(image_path)
            if not source_path.exists():
                continue

            for size in sizes:
                db_path = thumb_paths.get(size)

                if db_path and db_path != 'FAILED':
                    # DB has a path - check if file exists
                    if Path(db_path).exists():
                        categories['valid'].append({
                            'image_id': image_id,
                            'size': size,
                            'path': db_path
                        })
                    else:
                        # Broken link - DB path doesn't exist on disk
                        categories['broken_links'].append({
                            'image_id': image_id,
                            'size': size,
                            'db_path': db_path,
                            'source_path': str(source_path)
                        })
                else:
                    # No DB path - missing thumbnail
                    categories['missing'].append({
                        'image_id': image_id,
                        'size': size,
                        'source_path': str(source_path)
                    })

            # Progress update every 100 images
            if idx % 100 == 0 and progress_callback:
                filename = source_path.name if source_path else f"image_{image_id}"
                progress = ScanProgress(
                    phase='database_validation',
                    current=idx + 1,
                    total=total_images,
                    percentage=(idx + 1) / total_images * 100,
                    message=f"Scanning: {filename}"
                )
                progress_callback(progress)

        logger.info(f"Phase 1 complete: {len(categories['valid'])} valid, "
                   f"{len(categories['broken_links'])} broken, "
                   f"{len(categories['missing'])} missing")

        # Phase 2: Disk File Scan
        logger.info("Phase 2: Disk file scan")
        disk_files = {}  # hash → {size: path}

        total_disk_files = 0
        processed_disk_files = 0

        # Count total files first
        for size in sizes:
            size_dir = self.thumbnail_dir / size
            if size_dir.exists():
                total_disk_files += len(list(size_dir.glob('*')))

        for size in sizes:
            size_dir = self.thumbnail_dir / size
            if not size_dir.exists():
                continue

            for file_path in size_dir.glob('*'):
                file_hash = self._extract_hash_from_filename(file_path.name)
                if file_hash:
                    if file_hash not in disk_files:
                        disk_files[file_hash] = {}
                    disk_files[file_hash][size] = file_path

                processed_disk_files += 1

                # Progress update every 500 files
                if processed_disk_files % 500 == 0 and progress_callback:
                    progress = ScanProgress(
                        phase='disk_scan',
                        current=processed_disk_files,
                        total=total_disk_files,
                        percentage=(processed_disk_files / total_disk_files * 100) if total_disk_files > 0 else 0,
                        message=f"Scanning: {file_path.name}"
                    )
                    progress_callback(progress)

        logger.info(f"Phase 2 complete: Found {len(disk_files)} unique thumbnail hashes on disk")

        # Phase 3: Match Orphans
        logger.info("Phase 3: Orphan matching")
        db_hashes = await self._get_all_image_path_hashes()

        orphan_count = 0
        for file_hash, size_paths in disk_files.items():
            # Check if this hash exists in DB
            if file_hash not in db_hashes:
                # True orphan - no matching image in DB
                for size, path in size_paths.items():
                    categories['true_orphans'].append({
                        'hash': file_hash,
                        'size': size,
                        'path': str(path),
                        'file_size': path.stat().st_size
                    })
            else:
                # File hash exists in DB - check if it's linked
                image_id = db_hashes[file_hash]

                # Get DB record for this image
                conn = sqlite3.connect(self.db.db_path)
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT thumbnail_small_path, thumbnail_medium_path,
                           thumbnail_large_path, thumbnail_xlarge_path
                    FROM generated_images
                    WHERE id = ?
                """, (image_id,))
                row = cursor.fetchone()
                conn.close()

                if row:
                    thumb_paths = {
                        'small': row[0],
                        'medium': row[1],
                        'large': row[2],
                        'xlarge': row[3]
                    }

                    for size, path in size_paths.items():
                        db_record = thumb_paths.get(size)

                        if not db_record or db_record == 'FAILED':
                            # Linkable orphan - file exists but not in DB
                            categories['linkable_orphans'].append({
                                'image_id': image_id,
                                'size': size,
                                'file_path': str(path)
                            })

            orphan_count += 1

            # Progress update every 100 orphans
            if orphan_count % 100 == 0 and progress_callback:
                # Get a sample filename from the current orphan
                sample_file = next(iter(size_paths.values())).name if size_paths else f"hash_{file_hash}"
                progress = ScanProgress(
                    phase='orphan_matching',
                    current=orphan_count,
                    total=len(disk_files),
                    percentage=(orphan_count / len(disk_files) * 100) if len(disk_files) > 0 else 0,
                    message=f"Matching: {sample_file}"
                )
                progress_callback(progress)

        logger.info(f"Phase 3 complete: {len(categories['linkable_orphans'])} linkable orphans, "
                   f"{len(categories['true_orphans'])} true orphans")

        # Calculate statistics
        true_orphans_size = sum(item['file_size'] for item in categories['true_orphans'])
        true_orphans_sample = categories['true_orphans'][:sample_limit]

        # Estimate time (rough calculation)
        total_ops = (
            len(categories['broken_links']) +
            len(categories['linkable_orphans']) +
            len(categories['missing'])
        )
        # Assume 2 images/sec for generation, instant for DB updates
        estimated_time = len(categories['missing']) // 2

        results = ScanResults(
            total_images=total_images,
            categories={
                'valid': len(categories['valid']),
                'broken_links': len(categories['broken_links']),
                'linkable_orphans': len(categories['linkable_orphans']),
                'missing': len(categories['missing'])
            },
            breakdown={
                'valid': categories['valid'],
                'broken_links': categories['broken_links'],
                'linkable_orphans': categories['linkable_orphans'],
                'missing': categories['missing']
            },
            true_orphans={
                'count': len(categories['true_orphans']),
                'size_bytes': true_orphans_size,
                'sample_files': true_orphans_sample
            },
            estimated_time_seconds=estimated_time
        )

        logger.info(f"Scan complete: {results.total_images} total images, "
                   f"{results.categories['valid']} valid, "
                   f"{results.categories['broken_links']} broken, "
                   f"{results.categories['linkable_orphans']} linkable orphans, "
                   f"{results.categories['missing']} missing")

        return results

    async def rebuild_unified(
        self,
        operations: Dict[str, bool],
        sizes: List[str],
        scan_results: Dict[str, List],
        progress_callback: Optional[Callable] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> Dict[str, Any]:
        """Execute unified rebuild operations with progress tracking.

        Args:
            operations: Dict of {fix_broken_links, link_orphans, generate_missing, delete_true_orphans}
            sizes: Thumbnail sizes to process
            scan_results: Results from comprehensive scan
            progress_callback: Optional callback for progress updates
            cancel_event: Optional event to cancel operation

        Returns:
            Summary dict with stats
        """
        logger.info(f"Starting unified rebuild with operations: {operations}")

        stats = {
            'fixed_links': 0,
            'linked_orphans': 0,
            'generated': 0,
            'deleted_orphans': 0,
            'failed': 0,
            'errors': []
        }

        total_ops = sum([
            len(scan_results.get('broken_links', [])) if operations.get('fix_broken_links') else 0,
            len(scan_results.get('linkable_orphans', [])) if operations.get('link_orphans') else 0,
            len(scan_results.get('missing', [])) if operations.get('generate_missing') else 0,
            len(scan_results.get('true_orphans', [])) if operations.get('delete_true_orphans') else 0,
        ])

        completed = 0
        start_time = datetime.now()

        # Operation 1: Fix Broken Links (fast - DB updates only)
        if operations.get('fix_broken_links'):
            logger.info(f"Fixing {len(scan_results.get('broken_links', []))} broken links")

            for item in scan_results.get('broken_links', []):
                if cancel_event and cancel_event.is_set():
                    logger.info("Rebuild cancelled during fix_broken_links")
                    break

                # Find actual file on disk
                source_path = Path(item['source_path'])
                correct_path = self.thumbnail_service.get_thumbnail_path(
                    source_path,
                    item['size']
                )

                if correct_path.exists():
                    await self._update_db_thumbnail_path(
                        item['image_id'],
                        item['size'],
                        str(correct_path)
                    )
                    stats['fixed_links'] += 1
                else:
                    stats['failed'] += 1
                    stats['errors'].append({
                        'operation': 'fix_broken_link',
                        'image_id': item['image_id'],
                        'size': item['size'],
                        'error': 'Thumbnail file not found on disk'
                    })

                completed += 1

                if completed % 10 == 0 and progress_callback:
                    progress_callback({
                        'operation': 'fixing_broken_links',
                        'completed': completed,
                        'total': total_ops,
                        'percentage': (completed / total_ops * 100) if total_ops > 0 else 0,
                        'current_file': f"{item['size']}: {Path(item['source_path']).name}",
                        'stats': stats.copy()
                    })

        # Operation 2: Link Orphans (fast - DB updates only)
        if operations.get('link_orphans'):
            logger.info(f"Linking {len(scan_results.get('linkable_orphans', []))} orphaned files")

            for item in scan_results.get('linkable_orphans', []):
                if cancel_event and cancel_event.is_set():
                    logger.info("Rebuild cancelled during link_orphans")
                    break

                await self._update_db_thumbnail_path(
                    item['image_id'],
                    item['size'],
                    item['file_path']
                )
                stats['linked_orphans'] += 1

                completed += 1

                if completed % 10 == 0 and progress_callback:
                    progress_callback({
                        'operation': 'linking_orphans',
                        'completed': completed,
                        'total': total_ops,
                        'percentage': (completed / total_ops * 100) if total_ops > 0 else 0,
                        'current_file': f"{item['size']}: {Path(item['file_path']).name}",
                        'stats': stats.copy()
                    })

        # Operation 3: Generate Missing (slow - actual file generation)
        if operations.get('generate_missing'):
            logger.info(f"Generating {len(scan_results.get('missing', []))} missing thumbnails")

            tasks = self._create_thumbnail_tasks(scan_results.get('missing', []), sizes)

            def gen_progress_callback(prog):
                if progress_callback:
                    progress_callback({
                        'operation': 'generating_missing',
                        'completed': completed + prog.get('completed', 0),
                        'total': total_ops,
                        'percentage': ((completed + prog.get('completed', 0)) / total_ops * 100) if total_ops > 0 else 0,
                        'current_file': prog.get('current_file'),
                        'stats': {
                            **stats,
                            'generated': prog.get('completed', 0)
                        }
                    })

            result = await self.thumbnail_service.generate_batch(
                tasks,
                progress_callback=gen_progress_callback,
                cancel_event=cancel_event
            )

            stats['generated'] = result.get('completed', 0)
            stats['failed'] += result.get('failed', 0)
            completed += result.get('processed', 0)

        # Operation 4: Delete True Orphans (if requested)
        if operations.get('delete_true_orphans'):
            logger.info(f"Deleting {len(scan_results.get('true_orphans', []))} true orphan files")

            for item in scan_results.get('true_orphans', []):
                if cancel_event and cancel_event.is_set():
                    logger.info("Rebuild cancelled during delete_true_orphans")
                    break

                try:
                    Path(item['path']).unlink()
                    stats['deleted_orphans'] += 1
                except Exception as e:
                    stats['failed'] += 1
                    stats['errors'].append({
                        'operation': 'delete_orphan',
                        'path': item['path'],
                        'error': str(e)
                    })

                completed += 1

        duration = (datetime.now() - start_time).total_seconds()

        summary = {
            'completed': completed,
            'total': total_ops,
            'duration_seconds': duration,
            'stats': stats
        }

        logger.info(f"Rebuild complete: {summary}")

        return summary

    def _extract_hash_from_filename(self, filename: str) -> Optional[str]:
        """Extract MD5 hash from thumbnail filename.

        Format: {hash}_{size}.{ext}
        Example: a3f2c1b5d6e7f8g9h0i1j2k3l4m5n6o7_medium.jpg

        Args:
            filename: Thumbnail filename

        Returns:
            MD5 hash or None if invalid format
        """
        match = re.match(r'^([a-f0-9]{32})_\w+\.\w+$', filename)
        return match.group(1) if match else None

    async def _get_all_image_path_hashes(self) -> Dict[str, int]:
        """Get mapping of image path hashes to image IDs.

        Returns:
            Dict of {hash: image_id}
        """
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT id, image_path FROM generated_images WHERE image_path IS NOT NULL")

        hash_map = {}
        for row in cursor.fetchall():
            image_id, image_path = row
            if image_path:
                path_hash = hashlib.md5(str(image_path).encode()).hexdigest()
                hash_map[path_hash] = image_id

        conn.close()
        return hash_map

    async def _count_images(self) -> int:
        """Count total images in database.

        Returns:
            Total image count
        """
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM generated_images WHERE image_path IS NOT NULL")
        count = cursor.fetchone()[0]
        conn.close()
        return count

    async def _update_db_thumbnail_path(self, image_id: int, size: str, path: str):
        """Update thumbnail path in database.

        Args:
            image_id: Image ID
            size: Thumbnail size (small, medium, large, xlarge)
            path: New thumbnail path
        """
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()

        column_name = f'thumbnail_{size}_path'
        cursor.execute(f"""
            UPDATE generated_images
            SET {column_name} = ?
            WHERE id = ?
        """, (path, image_id))

        conn.commit()
        conn.close()

    def _create_thumbnail_tasks(self, missing_items: List[Dict], sizes: List[str]) -> List[ThumbnailTask]:
        """Create thumbnail tasks from missing items.

        Args:
            missing_items: List of missing thumbnail items
            sizes: Thumbnail sizes to generate

        Returns:
            List of ThumbnailTask objects
        """
        tasks = []

        for item in missing_items:
            source_path = Path(item['source_path'])
            if not source_path.exists():
                continue

            size = item['size']
            if size not in sizes:
                continue

            is_video = self.thumbnail_service.ffmpeg.is_video(source_path)
            size_dims = self.thumbnail_service.base_generator.SIZES.get(size, (300, 300))

            task = ThumbnailTask(
                image_id=hashlib.md5(str(source_path).encode()).hexdigest(),
                source_path=source_path,
                thumbnail_path=self.thumbnail_service.get_thumbnail_path(source_path, size),
                size=size_dims,
                format=source_path.suffix.lower()[1:] if not is_video else 'jpg',
                is_video=is_video
            )
            tasks.append(task)

        return tasks
