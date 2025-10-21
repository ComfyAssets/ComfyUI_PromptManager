"""File operations utilities for PromptManager.

Provides safe file operations including atomic writes, directory management,
file locking, cleanup utilities, and backup operations.
"""

import os
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Callable
import json
import gzip
import hashlib

try:
    from ..core.logging_config import get_logger  # type: ignore
except ImportError:  # pragma: no cover - fallback for direct execution
    import logging

    def get_logger(name: str):
        logger = logging.getLogger(name)
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
                )
            )
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

# Import ComfyUIFileSystem for proper path resolution
try:
    from ..core.file_system import ComfyUIFileSystem  # type: ignore
except ImportError:  # pragma: no cover - fallback for direct execution
    try:
        from utils.core.file_system import ComfyUIFileSystem  # type: ignore
    except ImportError:
        ComfyUIFileSystem = None

logger = get_logger("promptmanager.file_ops")


def _get_file_ops_base_dir() -> Path:
    """Get the base directory for file operations using ComfyUI root detection."""
    if ComfyUIFileSystem is not None:
        try:
            fs_helper = ComfyUIFileSystem()
            comfyui_root = fs_helper.resolve_comfyui_root()
            return Path(comfyui_root)
        except Exception:
            pass

    # Fallback to current directory if ComfyUIFileSystem fails
    return Path.cwd()


class FileOperationError(Exception):
    """Base exception for file operations."""
    pass


class FileLockError(FileOperationError):
    """Exception for file locking issues."""
    pass


class AtomicWriter:
    """Atomic file writing with automatic rollback on failure."""
    
    def __init__(self, target_path: Union[str, Path], mode: str = 'w', encoding: str = 'utf-8'):
        """Initialize atomic writer.
        
        Args:
            target_path: Final destination path
            mode: File mode ('w', 'wb', 'a')
            encoding: Text encoding (ignored for binary mode)
        """
        self.target_path = Path(target_path)
        self.mode = mode
        self.encoding = encoding if 'b' not in mode else None
        self.temp_path: Optional[Path] = None
        self.temp_file = None
        
    def __enter__(self):
        """Create temporary file for writing."""
        # Create temp file in same directory for atomic rename
        self.temp_path = self.target_path.with_suffix(
            f'.tmp{os.getpid()}_{threading.get_ident()}'
        )
        
        if 'b' in self.mode:
            self.temp_file = open(self.temp_path, self.mode)
        else:
            self.temp_file = open(self.temp_path, self.mode, encoding=self.encoding)
        
        return self.temp_file
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close temp file and rename or cleanup."""
        if self.temp_file:
            self.temp_file.close()
        
        if exc_type is None and self.temp_path:
            # Success - rename temp to target
            try:
                # Preserve permissions if target exists
                if self.target_path.exists():
                    shutil.copystat(self.target_path, self.temp_path)
                
                # Atomic rename
                self.temp_path.replace(self.target_path)
                logger.debug(f"Atomically wrote {self.target_path}")
                
            except Exception as e:
                logger.error(f"Failed to rename temp file: {e}")
                if self.temp_path.exists():
                    self.temp_path.unlink()
                raise FileOperationError(f"Atomic write failed: {e}")
        else:
            # Failure - cleanup temp file
            if self.temp_path and self.temp_path.exists():
                self.temp_path.unlink()
                logger.debug(f"Cleaned up temp file after error: {self.temp_path}")


class FileLock:
    """Cross-platform file locking mechanism."""
    
    _locks: Dict[str, threading.Lock] = {}
    _lock_registry = threading.Lock()
    
    def __init__(self, file_path: Union[str, Path], timeout: float = 10.0):
        """Initialize file lock.
        
        Args:
            file_path: Path to lock
            timeout: Lock acquisition timeout in seconds
        """
        self.file_path = Path(file_path).absolute()
        self.lock_file = self.file_path.with_suffix('.lock')
        self.timeout = timeout
        self.acquired = False
        
    def acquire(self) -> bool:
        """Acquire the lock.
        
        Returns:
            True if lock acquired, False if timeout
        """
        start_time = time.time()
        
        # Get or create thread lock for this file
        with self._lock_registry:
            if str(self.file_path) not in self._locks:
                self._locks[str(self.file_path)] = threading.Lock()
            thread_lock = self._locks[str(self.file_path)]
        
        # Try to acquire thread lock with timeout
        acquired = thread_lock.acquire(timeout=self.timeout)
        if not acquired:
            return False
        
        try:
            # Try to create lock file
            while time.time() - start_time < self.timeout:
                try:
                    # Create lock file with PID
                    self.lock_file.parent.mkdir(parents=True, exist_ok=True)
                    
                    # Check if lock file exists and is stale
                    if self.lock_file.exists():
                        # Check if lock is stale (older than 1 hour)
                        if time.time() - self.lock_file.stat().st_mtime > 3600:
                            logger.warning(f"Removing stale lock: {self.lock_file}")
                            self.lock_file.unlink()
                        else:
                            time.sleep(0.1)
                            continue
                    
                    # Create lock file
                    with open(self.lock_file, 'x') as f:
                        f.write(f"{os.getpid()}\n{threading.get_ident()}")
                    
                    self.acquired = True
                    return True
                    
                except FileExistsError:
                    time.sleep(0.1)
                    
        except Exception as e:
            thread_lock.release()
            raise FileLockError(f"Failed to acquire lock: {e}")
        
        # Timeout reached
        thread_lock.release()
        return False
    
    def release(self):
        """Release the lock."""
        if self.acquired:
            try:
                if self.lock_file.exists():
                    self.lock_file.unlink()
                
                # Release thread lock
                with self._lock_registry:
                    if str(self.file_path) in self._locks:
                        self._locks[str(self.file_path)].release()
                
                self.acquired = False
                logger.debug(f"Released lock: {self.lock_file}")
                
            except Exception as e:
                logger.error(f"Failed to release lock: {e}")
    
    def __enter__(self):
        """Context manager entry."""
        if not self.acquire():
            raise FileLockError(f"Failed to acquire lock within {self.timeout} seconds")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.release()


class DirectoryManager:
    """Utilities for directory management."""
    
    @staticmethod
    def create_directory(path: Union[str, Path], parents: bool = True) -> Path:
        """Create directory with proper error handling.
        
        Args:
            path: Directory path to create
            parents: Create parent directories if needed
            
        Returns:
            Path object of created directory
        """
        dir_path = Path(path)
        
        try:
            dir_path.mkdir(parents=parents, exist_ok=True)
            logger.debug(f"Created directory: {dir_path}")
            return dir_path
            
        except Exception as e:
            raise FileOperationError(f"Failed to create directory {path}: {e}")
    
    @staticmethod
    def safe_remove_directory(
        path: Union[str, Path],
        require_empty: bool = True,
        confirm_non_empty: Optional[Callable[[], bool]] = None
    ) -> bool:
        """Safely remove directory with checks.
        
        Args:
            path: Directory to remove
            require_empty: Only remove if empty
            confirm_non_empty: Callback to confirm non-empty removal
            
        Returns:
            True if removed, False otherwise
        """
        dir_path = Path(path)
        
        if not dir_path.exists():
            return False
        
        if not dir_path.is_dir():
            raise FileOperationError(f"Path is not a directory: {path}")
        
        try:
            # Check if empty
            contents = list(dir_path.iterdir())
            
            if contents and require_empty:
                logger.warning(f"Directory not empty: {path}")
                return False
            
            if contents and confirm_non_empty:
                if not confirm_non_empty():
                    logger.info("Directory removal cancelled by user")
                    return False
            
            # Remove directory
            shutil.rmtree(dir_path)
            logger.info(f"Removed directory: {path}")
            return True
            
        except Exception as e:
            raise FileOperationError(f"Failed to remove directory {path}: {e}")
    
    @staticmethod
    def get_directory_size(path: Union[str, Path]) -> int:
        """Calculate total size of directory in bytes.
        
        Args:
            path: Directory path
            
        Returns:
            Total size in bytes
        """
        total = 0
        dir_path = Path(path)
        
        if not dir_path.is_dir():
            raise FileOperationError(f"Not a directory: {path}")
        
        for entry in dir_path.rglob('*'):
            if entry.is_file():
                total += entry.stat().st_size
        
        return total
    
    @staticmethod
    def clean_empty_directories(root: Union[str, Path]) -> List[Path]:
        """Remove all empty directories under root.
        
        Args:
            root: Root directory to clean
            
        Returns:
            List of removed directories
        """
        removed = []
        root_path = Path(root)
        
        # Walk bottom-up to remove nested empty dirs
        for dir_path in sorted(root_path.rglob('*'), reverse=True):
            if dir_path.is_dir():
                try:
                    # Try to remove - will fail if not empty
                    dir_path.rmdir()
                    removed.append(dir_path)
                    logger.debug(f"Removed empty directory: {dir_path}")
                except OSError:
                    # Directory not empty
                    pass
        
        return removed


class FileCleanup:
    """Utilities for file cleanup operations."""
    
    @staticmethod
    def remove_old_files(
        directory: Union[str, Path],
        days: int,
        pattern: str = '*',
        dry_run: bool = False
    ) -> List[Path]:
        """Remove files older than specified days.
        
        Args:
            directory: Directory to clean
            days: Remove files older than this many days
            pattern: Glob pattern for files to consider
            dry_run: If True, only return files that would be deleted
            
        Returns:
            List of removed (or would-be removed) files
        """
        removed = []
        dir_path = Path(directory)
        cutoff = datetime.now() - timedelta(days=days)
        
        for file_path in dir_path.glob(pattern):
            if file_path.is_file():
                mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                
                if mtime < cutoff:
                    removed.append(file_path)
                    
                    if not dry_run:
                        try:
                            file_path.unlink()
                            logger.debug(f"Removed old file: {file_path}")
                        except Exception as e:
                            logger.error(f"Failed to remove {file_path}: {e}")
        
        return removed
    
    @staticmethod
    def clean_temp_files(
        directory: Union[str, Path],
        patterns: List[str] = None
    ) -> List[Path]:
        """Remove temporary files matching patterns.
        
        Args:
            directory: Directory to clean
            patterns: List of glob patterns (default: common temp patterns)
            
        Returns:
            List of removed files
        """
        if patterns is None:
            patterns = ['*.tmp', '*.temp', '*.bak', '*.~*', '.*.swp']
        
        removed = []
        dir_path = Path(directory)
        
        for pattern in patterns:
            for file_path in dir_path.rglob(pattern):
                if file_path.is_file():
                    try:
                        file_path.unlink()
                        removed.append(file_path)
                        logger.debug(f"Removed temp file: {file_path}")
                    except Exception as e:
                        logger.error(f"Failed to remove {file_path}: {e}")
        
        return removed
    
    @staticmethod
    def cleanup_by_size(
        directory: Union[str, Path],
        max_size_mb: float,
        keep_newest: bool = True
    ) -> List[Path]:
        """Remove files to keep directory under size limit.
        
        Args:
            directory: Directory to clean
            max_size_mb: Maximum total size in MB
            keep_newest: If True, remove oldest files first
            
        Returns:
            List of removed files
        """
        removed = []
        dir_path = Path(directory)
        max_bytes = max_size_mb * 1024 * 1024
        
        # Get all files with sizes
        files = []
        total_size = 0
        
        for file_path in dir_path.rglob('*'):
            if file_path.is_file():
                size = file_path.stat().st_size
                mtime = file_path.stat().st_mtime
                files.append((file_path, size, mtime))
                total_size += size
        
        if total_size <= max_bytes:
            return removed
        
        # Sort by modification time
        files.sort(key=lambda x: x[2], reverse=keep_newest)
        
        # Remove files until under limit
        for file_path, size, _ in files:
            if total_size <= max_bytes:
                break
            
            try:
                file_path.unlink()
                removed.append(file_path)
                total_size -= size
                logger.debug(f"Removed file for size limit: {file_path}")
            except Exception as e:
                logger.error(f"Failed to remove {file_path}: {e}")
        
        return removed


class BackupManager:
    """Utilities for backup operations."""
    
    def __init__(self, backup_dir: Union[str, Path] = None):
        """Initialize backup manager.
        
        Args:
            backup_dir: Default backup directory
        """
        self.backup_dir = Path(backup_dir) if backup_dir else _get_file_ops_base_dir() / 'backups'
        self.backup_dir.mkdir(parents=True, exist_ok=True)
    
    def create_backup(
        self,
        source: Union[str, Path],
        compress: bool = False,
        timestamp: bool = True
    ) -> Path:
        """Create backup of file or directory.
        
        Args:
            source: File or directory to backup
            compress: Whether to compress the backup
            timestamp: Add timestamp to backup name
            
        Returns:
            Path to backup
        """
        source_path = Path(source)
        
        if not source_path.exists():
            raise FileOperationError(f"Source does not exist: {source}")
        
        # Generate backup name
        if timestamp:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_name = f"{source_path.name}.{ts}.bak"
        else:
            backup_name = f"{source_path.name}.bak"
        
        if compress:
            backup_name += '.gz'
        
        backup_path = self.backup_dir / backup_name
        
        try:
            if source_path.is_file():
                if compress:
                    # Compress file
                    with open(source_path, 'rb') as f_in:
                        with gzip.open(backup_path, 'wb') as f_out:
                            shutil.copyfileobj(f_in, f_out)
                else:
                    # Copy file
                    shutil.copy2(source_path, backup_path)
            else:
                # Backup directory
                if compress:
                    # Create tar.gz archive
                    import tarfile
                    backup_path = backup_path.with_suffix('.tar.gz')
                    with tarfile.open(backup_path, 'w:gz') as tar:
                        tar.add(source_path, arcname=source_path.name)
                else:
                    # Copy directory
                    shutil.copytree(source_path, backup_path)
            
            logger.info(f"Created backup: {backup_path}")
            return backup_path
            
        except Exception as e:
            raise FileOperationError(f"Backup failed: {e}")
    
    def rotate_backups(self, pattern: str, keep: int = 5) -> List[Path]:
        """Keep only the newest N backups matching pattern.
        
        Args:
            pattern: Glob pattern for backup files
            keep: Number of backups to keep
            
        Returns:
            List of removed backup files
        """
        backups = sorted(
            self.backup_dir.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        
        removed = []
        for backup in backups[keep:]:
            try:
                backup.unlink()
                removed.append(backup)
                logger.debug(f"Removed old backup: {backup}")
            except Exception as e:
                logger.error(f"Failed to remove backup {backup}: {e}")
        
        return removed
    
    def verify_backup(self, backup_path: Union[str, Path], source_path: Union[str, Path]) -> bool:
        """Verify backup integrity.
        
        Args:
            backup_path: Path to backup
            source_path: Original source path
            
        Returns:
            True if backup is valid
        """
        backup = Path(backup_path)
        source = Path(source_path)
        
        if not backup.exists():
            logger.error(f"Backup does not exist: {backup}")
            return False
        
        try:
            # Check compressed backups
            if backup.suffix == '.gz':
                if source.is_file():
                    # Compare file hashes
                    with open(source, 'rb') as f:
                        source_hash = hashlib.sha256(f.read()).hexdigest()
                    
                    with gzip.open(backup, 'rb') as f:
                        backup_hash = hashlib.sha256(f.read()).hexdigest()
                    
                    return source_hash == backup_hash
                else:
                    # Can't easily verify compressed directories
                    return backup.stat().st_size > 0
            
            # Check uncompressed backups
            if source.is_file() and backup.is_file():
                # Compare file hashes
                with open(source, 'rb') as f:
                    source_hash = hashlib.sha256(f.read()).hexdigest()
                
                with open(backup, 'rb') as f:
                    backup_hash = hashlib.sha256(f.read()).hexdigest()
                
                return source_hash == backup_hash
            
            # For directories, just check existence and non-empty
            return backup.exists() and (
                backup.stat().st_size > 0 if backup.is_file() 
                else any(backup.iterdir())
            )
            
        except Exception as e:
            logger.error(f"Backup verification failed: {e}")
            return False
    
    def restore_backup(self, backup_path: Union[str, Path], target: Union[str, Path]) -> bool:
        """Restore from backup.
        
        Args:
            backup_path: Path to backup
            target: Where to restore
            
        Returns:
            True if successful
        """
        backup = Path(backup_path)
        target_path = Path(target)
        
        if not backup.exists():
            raise FileOperationError(f"Backup does not exist: {backup}")
        
        try:
             # Handle compressed backups
            if backup.suffix == '.gz':
                if '.tar.gz' in backup.name:
                    # Extract tar.gz archive with path validation
                    import tarfile
                    with tarfile.open(backup, 'r:gz') as tar:
                        for member in tar.getmembers():
                            if member.name.startswith('/') or '..' in member.name:
                                raise ValueError(f"Potentially dangerous tar member: {member.name}")
                        tar.extractall(target_path.parent)
                else:
                    # Decompress single file
                    with gzip.open(backup, 'rb') as f_in:
                        with open(target_path, 'wb') as f_out:
                            shutil.copyfileobj(f_in, f_out)
            else:
                # Restore uncompressed backup
                if backup.is_file():
                    shutil.copy2(backup, target_path)
                else:
                    if target_path.exists():
                        shutil.rmtree(target_path)
                    shutil.copytree(backup, target_path)
            
            logger.info(f"Restored backup from {backup} to {target_path}")
            return True
            
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            return False


class BatchFileOperation:
    """Atomic batch file operations with rollback."""
    
    def __init__(self):
        """Initialize batch operation."""
        self.operations: List[Dict[str, Any]] = []
        self.completed: List[Dict[str, Any]] = []
        self.backups: List[Path] = []
        
    def add_write(self, path: Union[str, Path], content: Union[str, bytes], mode: str = 'w'):
        """Add write operation to batch.
        
        Args:
            path: File path
            content: Content to write
            mode: Write mode
        """
        self.operations.append({
            'type': 'write',
            'path': Path(path),
            'content': content,
            'mode': mode
        })
    
    def add_delete(self, path: Union[str, Path]):
        """Add delete operation to batch.
        
        Args:
            path: File or directory to delete
        """
        self.operations.append({
            'type': 'delete',
            'path': Path(path)
        })
    
    def add_move(self, source: Union[str, Path], dest: Union[str, Path]):
        """Add move operation to batch.
        
        Args:
            source: Source path
            dest: Destination path
        """
        self.operations.append({
            'type': 'move',
            'source': Path(source),
            'dest': Path(dest)
        })
    
    def add_copy(self, source: Union[str, Path], dest: Union[str, Path]):
        """Add copy operation to batch.
        
        Args:
            source: Source path
            dest: Destination path
        """
        self.operations.append({
            'type': 'copy',
            'source': Path(source),
            'dest': Path(dest)
        })
    
    def execute(self, backup_manager: Optional[BackupManager] = None) -> bool:
        """Execute all operations atomically.
        
        Args:
            backup_manager: Optional backup manager for rollback
            
        Returns:
            True if all operations succeeded
        """
        try:
            # Create backups if manager provided
            if backup_manager:
                for op in self.operations:
                    if op['type'] in ('write', 'delete', 'move'):
                        path = op.get('path') or op.get('source')
                        if path and path.exists():
                            backup = backup_manager.create_backup(path, timestamp=True)
                            self.backups.append(backup)
            
            # Execute operations
            for op in self.operations:
                self._execute_operation(op)
                self.completed.append(op)
            
            logger.info(f"Batch operation completed: {len(self.completed)} operations")
            return True
            
        except Exception as e:
            logger.error(f"Batch operation failed: {e}")
            self.rollback(backup_manager)
            return False
    
    def _execute_operation(self, op: Dict[str, Any]):
        """Execute single operation.
        
        Args:
            op: Operation dictionary
        """
        op_type = op['type']
        
        if op_type == 'write':
            with AtomicWriter(op['path'], op['mode']) as f:
                f.write(op['content'])
        
        elif op_type == 'delete':
            path = op['path']
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        
        elif op_type == 'move':
            shutil.move(str(op['source']), str(op['dest']))
        
        elif op_type == 'copy':
            source = op['source']
            if source.is_file():
                shutil.copy2(source, op['dest'])
            else:
                shutil.copytree(source, op['dest'])
    
    def rollback(self, backup_manager: Optional[BackupManager] = None):
        """Rollback completed operations.
        
        Args:
            backup_manager: Backup manager for restoration
        """
        logger.info("Rolling back batch operations...")
        
        # Restore from backups if available
        if backup_manager and self.backups:
            for backup in reversed(self.backups):
                try:
                    # Extract original path from backup name
                    original_name = backup.name.split('.')[0]
                    target = backup.parent.parent / original_name
                    backup_manager.restore_backup(backup, target)
                except Exception as e:
                    logger.error(f"Failed to restore backup {backup}: {e}")
        
        # Otherwise try to reverse operations
        else:
            for op in reversed(self.completed):
                try:
                    self._reverse_operation(op)
                except Exception as e:
                    logger.error(f"Failed to reverse operation: {e}")
    
    def _reverse_operation(self, op: Dict[str, Any]):
        """Reverse a single operation.
        
        Args:
            op: Operation to reverse
        """
        op_type = op['type']
        
        if op_type == 'write':
            # Delete written file
            op['path'].unlink(missing_ok=True)
        
        elif op_type == 'delete':
            # Can't restore deleted files without backup
            logger.warning(f"Cannot restore deleted file without backup: {op['path']}")
        
        elif op_type == 'move':
            # Move back
            shutil.move(str(op['dest']), str(op['source']))
        
        elif op_type == 'copy':
            # Delete copy
            dest = op['dest']
            if dest.is_file():
                dest.unlink()
            elif dest.is_dir():
                shutil.rmtree(dest)


# Convenience functions
@contextmanager
def atomic_write(path: Union[str, Path], mode: str = 'w', encoding: str = 'utf-8'):
    """Context manager for atomic file writing.
    
    Args:
        path: File path
        mode: Write mode
        encoding: Text encoding
        
    Yields:
        File handle for writing
    """
    with AtomicWriter(path, mode, encoding) as f:
        yield f


@contextmanager
def file_lock(path: Union[str, Path], timeout: float = 10.0):
    """Context manager for file locking.
    
    Args:
        path: File path to lock
        timeout: Lock timeout in seconds
        
    Yields:
        FileLock instance
    """
    with FileLock(path, timeout) as lock:
        yield lock


def safe_write_json(path: Union[str, Path], data: Any, **kwargs):
    """Safely write JSON file atomically.
    
    Args:
        path: File path
        data: Data to serialize
        **kwargs: Additional arguments for json.dump
    """
    with atomic_write(path, 'w') as f:
        json.dump(data, f, **kwargs)


def cleanup_old_files(directory: Union[str, Path], days: int = 30, pattern: str = '*'):
    """Remove files older than specified days.
    
    Args:
        directory: Directory to clean
        days: Remove files older than this
        pattern: File pattern to match
        
    Returns:
        List of removed files
    """
    return FileCleanup.remove_old_files(directory, days, pattern)


def create_backup(source: Union[str, Path], backup_dir: Union[str, Path] = None) -> Path:
    """Create timestamped backup of file or directory.
    
    Args:
        source: Source to backup
        backup_dir: Backup directory (default: ./backups)
        
    Returns:
        Path to backup
    """
    manager = BackupManager(backup_dir)
    return manager.create_backup(source, timestamp=True)
