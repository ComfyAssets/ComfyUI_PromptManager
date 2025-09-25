"""
Database Maintenance Service
Handles database cleanup, optimization, and health checks
"""

import os
import sqlite3
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime

class MaintenanceService:
    """Service for database maintenance operations"""

    def __init__(self, api_instance):
        """Initialize the maintenance service with an API instance"""
        self.api = api_instance
        self.logger = api_instance.logger if hasattr(api_instance, 'logger') else logging.getLogger(__name__)
        self.prompt_repo = api_instance.prompt_repo if hasattr(api_instance, 'prompt_repo') else None
        self.generated_image_repo = api_instance.generated_image_repo if hasattr(api_instance, 'generated_image_repo') else None
        self.db_path = self._get_db_path()

    def _get_db_path(self) -> str:
        """Get the database path from repositories"""
        if self.prompt_repo and hasattr(self.prompt_repo, 'db_path'):
            return self.prompt_repo.db_path
        # Fallback to correct ComfyUI user directory
        # Try to find ComfyUI root and use user/default/PromptManager path
        import sys
        for path in sys.path:
            if 'ComfyUI' in path:
                comfy_root = path.split('custom_nodes')[0] if 'custom_nodes' in path else path
                user_db_path = os.path.join(comfy_root, 'user', 'default', 'PromptManager', 'prompts.db')
                if os.path.exists(user_db_path):
                    return user_db_path
        # Last resort fallback
        return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'prompts.db')

    def get_statistics(self) -> Dict[str, Any]:
        """Get database statistics"""
        stats = {
            'prompts': 0,
            'images': 0,
            'duplicates': 0,
            'orphaned': 0,
            'missing_files': 0,
            'database_size': 0
        }

        try:
            # Get prompt count
            if self.prompt_repo:
                stats['prompts'] = self.prompt_repo.count()

            # Get image count
            if self.generated_image_repo:
                stats['images'] = self.generated_image_repo.count()

            # Count duplicates
            stats['duplicates'] = self._count_duplicates()

            # Count orphaned images
            if self.generated_image_repo:
                orphaned = self.generated_image_repo.find_orphaned()
                stats['orphaned'] = len(orphaned)

            # Count missing files
            if self.generated_image_repo:
                validation = self.generated_image_repo.validate_paths()
                stats['missing_files'] = len(validation.get('missing', []))

            # Get database file size
            if os.path.exists(self.db_path):
                stats['database_size'] = os.path.getsize(self.db_path)

        except Exception as e:
            self.logger.error(f"Error getting statistics: {e}")

        return stats

    def _count_duplicates(self) -> int:
        """Count duplicate image links"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                query = """
                    SELECT COUNT(*) FROM generated_images
                    WHERE id NOT IN (
                        SELECT MIN(id)
                        FROM generated_images
                        GROUP BY prompt_id, image_path
                    )
                """
                cursor = conn.execute(query)
                result = cursor.fetchone()
                return result[0] if result else 0
        except Exception as e:
            self.logger.error(f"Error counting duplicates: {e}")
            return 0

    def remove_duplicates(self) -> Dict[str, Any]:
        """Remove duplicate image links"""
        result = {
            'success': False,
            'removed': 0,
            'message': ''
        }

        try:
            if self.generated_image_repo:
                removed = self.generated_image_repo.remove_duplicates()
                result['removed'] = removed
                result['success'] = True
                result['message'] = f"Removed {removed} duplicate image links"
                self.logger.info(f"Removed {removed} duplicate image links")
            else:
                result['message'] = "Image repository not available"
        except Exception as e:
            result['message'] = f"Error removing duplicates: {str(e)}"
            self.logger.error(result['message'])

        return result

    def clean_orphans(self) -> Dict[str, Any]:
        """Remove orphaned image links"""
        result = {
            'success': False,
            'removed': 0,
            'message': ''
        }

        try:
            if self.generated_image_repo:
                orphaned = self.generated_image_repo.find_orphaned()

                # Delete orphaned records
                removed = 0
                for record in orphaned:
                    try:
                        self.generated_image_repo.delete(record['id'])
                        removed += 1
                    except Exception as e:
                        self.logger.error(f"Failed to delete orphaned record {record['id']}: {e}")

                result['removed'] = removed
                result['success'] = True
                result['message'] = f"Removed {removed} orphaned image links"
                self.logger.info(f"Removed {removed} orphaned image links")
            else:
                result['message'] = "Image repository not available"
        except Exception as e:
            result['message'] = f"Error cleaning orphans: {str(e)}"
            self.logger.error(result['message'])

        return result

    def validate_paths(self) -> Dict[str, Any]:
        """Validate image file paths"""
        result = {
            'success': False,
            'valid': 0,
            'missing': 0,
            'missing_files': [],
            'message': ''
        }

        try:
            if self.generated_image_repo:
                validation = self.generated_image_repo.validate_paths()
                result['valid'] = len(validation.get('valid', []))
                result['missing'] = len(validation.get('missing', []))
                result['missing_files'] = [
                    {
                        'id': img['id'],
                        'path': img.get('image_path') or img.get('file_path'),
                        'prompt_id': img.get('prompt_id')
                    }
                    for img in validation.get('missing', [])[:100]  # Limit to 100 for display
                ]
                result['success'] = True
                result['message'] = f"Found {result['valid']} valid and {result['missing']} missing files"
                self.logger.info(result['message'])
            else:
                result['message'] = "Image repository not available"
        except Exception as e:
            result['message'] = f"Error validating paths: {str(e)}"
            self.logger.error(result['message'])

        return result

    def optimize_database(self) -> Dict[str, Any]:
        """Optimize database (VACUUM and REINDEX)"""
        result = {
            'success': False,
            'size_before': 0,
            'size_after': 0,
            'message': ''
        }

        try:
            # Get size before
            if os.path.exists(self.db_path):
                result['size_before'] = os.path.getsize(self.db_path)

            # Perform optimization
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("VACUUM")
                conn.execute("REINDEX")

            # Get size after
            if os.path.exists(self.db_path):
                result['size_after'] = os.path.getsize(self.db_path)

            saved = result['size_before'] - result['size_after']
            result['success'] = True
            result['message'] = f"Database optimized. Saved {saved:,} bytes"
            self.logger.info(result['message'])

        except Exception as e:
            result['message'] = f"Error optimizing database: {str(e)}"
            self.logger.error(result['message'])

        return result

    def check_integrity(self) -> Dict[str, Any]:
        """Check database integrity"""
        result = {
            'success': False,
            'issues': [],
            'message': ''
        }

        try:
            with sqlite3.connect(self.db_path) as conn:
                # Check integrity
                cursor = conn.execute("PRAGMA integrity_check")
                check_results = cursor.fetchall()

                if check_results and check_results[0][0] == 'ok':
                    result['success'] = True
                    result['message'] = "Database integrity check passed"
                else:
                    result['issues'] = [row[0] for row in check_results]
                    result['message'] = f"Database integrity check found {len(result['issues'])} issues"

                # Check foreign key violations
                cursor = conn.execute("PRAGMA foreign_key_check")
                fk_violations = cursor.fetchall()
                if fk_violations:
                    result['issues'].append(f"Found {len(fk_violations)} foreign key violations")

                self.logger.info(result['message'])

        except Exception as e:
            result['message'] = f"Error checking integrity: {str(e)}"
            self.logger.error(result['message'])

        return result

    def backup_database(self) -> Dict[str, Any]:
        """Create a database backup"""
        result = {
            'success': False,
            'backup_path': '',
            'message': ''
        }

        try:
            # Generate backup filename with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = self.db_path + f'.backup_{timestamp}'

            # Create backup
            with sqlite3.connect(self.db_path) as source:
                with sqlite3.connect(backup_path) as backup:
                    source.backup(backup)

            result['success'] = True
            result['backup_path'] = backup_path
            result['message'] = f"Database backed up to {backup_path}"
            self.logger.info(result['message'])

        except Exception as e:
            result['message'] = f"Error creating backup: {str(e)}"
            self.logger.error(result['message'])

        return result

    def remove_missing_files(self) -> Dict[str, Any]:
        """Remove database entries for missing image files"""
        result = {
            'success': False,
            'removed': 0,
            'message': ''
        }

        try:
            if self.generated_image_repo:
                validation = self.generated_image_repo.validate_paths()
                missing = validation.get('missing', [])

                # Delete records for missing files
                removed = 0
                for record in missing:
                    try:
                        self.generated_image_repo.delete(record['id'])
                        removed += 1
                    except Exception as e:
                        self.logger.error(f"Failed to delete record for missing file {record['id']}: {e}")

                result['removed'] = removed
                result['success'] = True
                result['message'] = f"Removed {removed} records for missing files"
                self.logger.info(result['message'])
            else:
                result['message'] = "Image repository not available"
        except Exception as e:
            result['message'] = f"Error removing missing file records: {str(e)}"
            self.logger.error(result['message'])

        return result

    def refresh_file_metadata(self, batch_size: int = 500) -> Dict[str, Any]:
        """Recompute missing file metadata such as size and dimensions."""

        result = {
            'success': False,
            'processed': 0,
            'updated': 0,
            'missing_path': 0,
            'message': ''
        }

        if not self.generated_image_repo:
            result['message'] = "Image repository not available"
            return result

        try:
            stats = self.generated_image_repo.populate_missing_file_metadata(batch_size=batch_size)
            result.update(stats)
            result['success'] = True
            result['message'] = (
                f"Updated {stats['updated']} records (processed {stats['processed']}, "
                f"missing path {stats['missing_path']})"
            )
            self.logger.info(result['message'])
        except Exception as e:
            result['message'] = f"Error refreshing file metadata: {e}"
            self.logger.error(result['message'])

        return result

    def fix_broken_links(self) -> Dict[str, Any]:
        """Fix broken image links by finding relocated images"""
        result = {
            'success': False,
            'fixed': 0,
            'unfixable': 0,
            'message': ''
        }

        try:
            if self.generated_image_repo:
                # Get all images with their paths
                validation = self.generated_image_repo.validate_paths()
                missing = validation.get('missing', [])

                fixed_count = 0
                unfixable_count = 0

                # Common ComfyUI output directories to search
                search_dirs = []
                import sys
                for path in sys.path:
                    if 'ComfyUI' in path:
                        comfy_root = path.split('custom_nodes')[0] if 'custom_nodes' in path else path
                        search_dirs.extend([
                            os.path.join(comfy_root, 'output'),
                            os.path.join(comfy_root, 'user', 'default', 'ComfyUI', 'output'),
                            os.path.join(comfy_root, 'input'),
                            os.path.join(comfy_root, 'temp')
                        ])
                        break

                # Remove duplicates and non-existent dirs
                search_dirs = [d for d in list(set(search_dirs)) if os.path.exists(d)]

                for record in missing:
                    old_path = record.get('image_path') or record.get('file_path')
                    if not old_path:
                        unfixable_count += 1
                        continue

                    filename = os.path.basename(old_path)
                    found = False

                    # Try to find the file in common directories
                    for search_dir in search_dirs:
                        for root, dirs, files in os.walk(search_dir):
                            if filename in files:
                                new_path = os.path.join(root, filename)
                                # Verify it's actually an image file
                                if new_path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
                                    try:
                                        # Update the database with new path
                                        with sqlite3.connect(self.db_path) as conn:
                                            conn.execute(
                                                "UPDATE generated_images SET image_path = ? WHERE id = ?",
                                                (new_path, record['id'])
                                            )
                                            conn.commit()
                                        fixed_count += 1
                                        found = True
                                        self.logger.info(f"Fixed path for image {record['id']}: {old_path} -> {new_path}")
                                        break
                                    except Exception as e:
                                        self.logger.error(f"Failed to update path for image {record['id']}: {e}")
                            if found:
                                break
                        if found:
                            break

                    if not found:
                        unfixable_count += 1
                        self.logger.debug(f"Could not find relocated file for: {filename}")

                result['fixed'] = fixed_count
                result['unfixable'] = unfixable_count
                result['success'] = True
                result['message'] = f"Fixed {fixed_count} broken links, {unfixable_count} could not be fixed"
                self.logger.info(result['message'])
            else:
                result['message'] = "Image repository not available"
        except Exception as e:
            result['message'] = f"Error fixing broken links: {str(e)}"
            self.logger.error(result['message'])

        return result

    def create_backup(self) -> Dict[str, Any]:
        """Create a database backup"""
        result = {
            'success': False,
            'message': ''
        }

        try:
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = self.db_path + f'.backup_{timestamp}'

            import shutil
            shutil.copy2(self.db_path, backup_path)

            # Get file size
            import os
            size = os.path.getsize(backup_path)
            size_mb = size / (1024 * 1024)

            result['success'] = True
            result['message'] = f"Backup created: {os.path.basename(backup_path)} ({size_mb:.2f} MB)"
            result['backup_path'] = backup_path
            result['size'] = f"{size_mb:.2f} MB"
            self.logger.info(result['message'])
        except Exception as e:
            result['message'] = f"Error creating backup: {str(e)}"
            self.logger.error(result['message'])

        return result

    def reindex_database(self) -> Dict[str, Any]:
        """Reindex all database tables"""
        result = {
            'success': False,
            'message': ''
        }

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Get all indexes
                cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
                indexes = cursor.fetchall()

                reindexed = 0
                for index in indexes:
                    if index[0] and not index[0].startswith('sqlite_'):
                        try:
                            cursor.execute(f"REINDEX {index[0]}")
                            reindexed += 1
                        except Exception as e:
                            self.logger.warning(f"Failed to reindex {index[0]}: {e}")

                conn.commit()

                result['success'] = True
                result['count'] = reindexed
                result['message'] = f"Successfully reindexed {reindexed} indexes"
                self.logger.info(result['message'])
        except Exception as e:
            result['message'] = f"Error reindexing database: {str(e)}"
            self.logger.error(result['message'])

        return result

    def export_backup(self) -> Dict[str, Any]:
        """Export a database backup with metadata"""
        result = {
            'success': False,
            'message': ''
        }

        try:
            from datetime import datetime
            import json
            import os

            # Create backup
            backup_result = self.create_backup()
            if not backup_result.get('success'):
                return backup_result

            backup_path = backup_result.get('backup_path')

            # Create metadata file
            metadata = {
                'timestamp': datetime.now().isoformat(),
                'database_version': '2.0.0',
                'statistics': self.get_statistics(),
                'backup_file': os.path.basename(backup_path),
                'size': backup_result.get('size')
            }

            metadata_path = backup_path + '.json'
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)

            result['success'] = True
            result['message'] = f"Backup exported with metadata"
            result['backup_path'] = backup_path
            result['metadata_path'] = metadata_path
            result['size'] = backup_result.get('size')
            self.logger.info(result['message'])
        except Exception as e:
            result['message'] = f"Error exporting backup: {str(e)}"
            self.logger.error(result['message'])

        return result
