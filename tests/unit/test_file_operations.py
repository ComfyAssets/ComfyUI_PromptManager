"""Unit tests for Windows-safe file operations."""

import platform
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.utils.file_operations import (
    FileOperationError,
    checkpoint_wal_file,
    close_all_sqlite_connections,
    copy_with_verify,
    safe_delete_with_retry,
    safe_rename_database,
    safe_rename_with_retry,
)


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite database for testing."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO test (value) VALUES ('test_data')")
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def temp_wal_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite database with WAL mode."""
    db_path = tmp_path / "test_wal.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO test (value) VALUES ('wal_test_data')")
    conn.commit()
    conn.close()
    return db_path


class TestCloseAllSqliteConnections:
    """Tests for close_all_sqlite_connections function."""

    def test_basic_call(self, temp_db: Path):
        """Test that function runs without errors."""
        close_all_sqlite_connections(temp_db)
        # Should complete without exception

    def test_with_wal_files(self, temp_wal_db: Path):
        """Test with WAL files present."""
        close_all_sqlite_connections(temp_wal_db)
        # Should complete without exception


class TestCheckpointWalFile:
    """Tests for checkpoint_wal_file function."""

    def test_checkpoint_non_wal_database(self, temp_db: Path):
        """Test checkpoint on non-WAL database."""
        result = checkpoint_wal_file(temp_db)
        assert result is True

    def test_checkpoint_wal_database(self, temp_wal_db: Path):
        """Test checkpoint on WAL-enabled database."""
        result = checkpoint_wal_file(temp_wal_db)
        assert result is True

    def test_checkpoint_nonexistent_database(self, tmp_path: Path):
        """Test checkpoint on non-existent database."""
        fake_db = tmp_path / "nonexistent.db"
        result = checkpoint_wal_file(fake_db)
        assert result is False


class TestSafeRenameWithRetry:
    """Tests for safe_rename_with_retry function."""

    def test_successful_rename(self, temp_db: Path, tmp_path: Path):
        """Test successful rename on first attempt."""
        dest = tmp_path / "renamed.db"
        result = safe_rename_with_retry(temp_db, dest)
        assert result is True
        assert dest.exists()
        assert not temp_db.exists()

    def test_rename_nonexistent_source(self, tmp_path: Path):
        """Test rename with non-existent source."""
        src = tmp_path / "nonexistent.db"
        dest = tmp_path / "dest.db"
        with pytest.raises(FileOperationError, match="does not exist"):
            safe_rename_with_retry(src, dest)

    def test_rename_removes_existing_dest(self, temp_db: Path, tmp_path: Path):
        """Test that existing destination is removed."""
        dest = tmp_path / "existing.db"
        dest.touch()
        result = safe_rename_with_retry(temp_db, dest)
        assert result is True
        assert dest.exists()

    @patch("src.utils.file_operations.Path.rename")
    def test_rename_with_retries(self, mock_rename: MagicMock, temp_db: Path, tmp_path: Path):
        """Test rename with retry logic."""
        dest = tmp_path / "dest.db"

        # Fail twice, then succeed
        mock_rename.side_effect = [
            PermissionError("File locked"),
            PermissionError("File locked"),
            None,
        ]

        result = safe_rename_with_retry(temp_db, dest, retry_delay=0.1)
        assert result is True
        assert mock_rename.call_count == 3

    @patch("src.utils.file_operations.Path.rename")
    def test_rename_exceeds_max_retries(self, mock_rename: MagicMock, temp_db: Path, tmp_path: Path):
        """Test rename failure after max retries."""
        dest = tmp_path / "dest.db"
        mock_rename.side_effect = PermissionError("File locked")

        with pytest.raises(FileOperationError, match="Failed to rename"):
            safe_rename_with_retry(temp_db, dest, max_retries=3, retry_delay=0.1)

        assert mock_rename.call_count == 3


class TestCopyWithVerify:
    """Tests for copy_with_verify function."""

    def test_successful_copy(self, temp_db: Path, tmp_path: Path):
        """Test successful file copy with verification."""
        dest = tmp_path / "subdir" / "copy.db"
        result = copy_with_verify(temp_db, dest, verify=True)
        assert result is True
        assert dest.exists()
        assert temp_db.exists()  # Original still exists
        assert temp_db.stat().st_size == dest.stat().st_size

    def test_copy_without_verification(self, temp_db: Path, tmp_path: Path):
        """Test copy without size verification."""
        dest = tmp_path / "copy.db"
        result = copy_with_verify(temp_db, dest, verify=False)
        assert result is True
        assert dest.exists()

    def test_copy_nonexistent_source(self, tmp_path: Path):
        """Test copy with non-existent source."""
        src = tmp_path / "nonexistent.db"
        dest = tmp_path / "dest.db"
        with pytest.raises(FileOperationError, match="does not exist"):
            copy_with_verify(src, dest)

    @patch("src.utils.file_operations.shutil.copy2")
    def test_copy_creates_parent_dirs(self, mock_copy: MagicMock, temp_db: Path, tmp_path: Path):
        """Test that parent directories are created."""
        dest = tmp_path / "deep" / "nested" / "path" / "copy.db"
        mock_copy.return_value = None

        copy_with_verify(temp_db, dest, verify=False)
        assert dest.parent.exists()


class TestSafeDeleteWithRetry:
    """Tests for safe_delete_with_retry function."""

    def test_successful_delete(self, temp_db: Path):
        """Test successful file deletion."""
        assert temp_db.exists()
        result = safe_delete_with_retry(temp_db)
        assert result is True
        assert not temp_db.exists()

    def test_delete_nonexistent_file(self, tmp_path: Path):
        """Test delete of non-existent file returns False."""
        fake_file = tmp_path / "nonexistent.db"
        result = safe_delete_with_retry(fake_file)
        assert result is False

    @patch("src.utils.file_operations.Path.unlink")
    def test_delete_with_retries(self, mock_unlink: MagicMock, temp_db: Path):
        """Test delete with retry logic."""
        # Fail once, then succeed
        mock_unlink.side_effect = [PermissionError("File locked"), None]

        result = safe_delete_with_retry(temp_db, max_retries=3, retry_delay=0.1)
        assert result is True
        assert mock_unlink.call_count == 2

    @patch("src.utils.file_operations.Path.unlink")
    def test_delete_exceeds_max_retries(self, mock_unlink: MagicMock, temp_db: Path):
        """Test delete failure after max retries."""
        mock_unlink.side_effect = PermissionError("File locked")

        with pytest.raises(FileOperationError, match="Failed to delete"):
            safe_delete_with_retry(temp_db, max_retries=3, retry_delay=0.1)

        assert mock_unlink.call_count == 3


class TestSafeRenameDatabase:
    """Tests for safe_rename_database function."""

    def test_successful_rename(self, temp_db: Path, tmp_path: Path):
        """Test successful database rename."""
        dest = tmp_path / "renamed.db"
        result = safe_rename_database(temp_db, dest)
        assert result is True
        assert dest.exists()
        assert not temp_db.exists()

    def test_rename_with_wal_files(self, temp_wal_db: Path, tmp_path: Path):
        """Test rename with WAL and SHM files."""
        # Create WAL and SHM files
        wal_file = temp_wal_db.with_suffix(temp_wal_db.suffix + "-wal")
        shm_file = temp_wal_db.with_suffix(temp_wal_db.suffix + "-shm")

        # Ensure they exist (they should from WAL mode)
        if not wal_file.exists():
            wal_file.touch()
        if not shm_file.exists():
            shm_file.touch()

        dest = tmp_path / "renamed.db"
        result = safe_rename_database(temp_wal_db, dest)
        assert result is True
        assert dest.exists()

    def test_rename_nonexistent_database(self, tmp_path: Path):
        """Test rename of non-existent database."""
        src = tmp_path / "nonexistent.db"
        dest = tmp_path / "dest.db"
        with pytest.raises(FileOperationError, match="does not exist"):
            safe_rename_database(src, dest)

    @patch("src.utils.file_operations.safe_rename_with_retry")
    @patch("src.utils.file_operations.copy_with_verify")
    @patch("src.utils.file_operations.safe_delete_with_retry")
    def test_copy_fallback_on_rename_failure(
        self,
        mock_delete: MagicMock,
        mock_copy: MagicMock,
        mock_rename: MagicMock,
        temp_db: Path,
        tmp_path: Path,
    ):
        """Test that copy fallback is used when rename fails."""
        dest = tmp_path / "dest.db"

        # Rename fails, copy succeeds
        mock_rename.side_effect = FileOperationError("Rename failed")
        mock_copy.return_value = True
        mock_delete.return_value = True

        # Mock SQLite integrity check
        with patch("src.utils.file_operations.sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn
            mock_conn.execute.return_value = None

            result = safe_rename_database(temp_db, dest, use_copy_fallback=True)

        assert result is True
        assert mock_rename.called
        assert mock_copy.called
        assert mock_delete.called

    @patch("src.utils.file_operations.safe_rename_with_retry")
    def test_no_fallback_when_disabled(
        self, mock_rename: MagicMock, temp_db: Path, tmp_path: Path
    ):
        """Test that fallback is not used when disabled."""
        dest = tmp_path / "dest.db"
        mock_rename.side_effect = FileOperationError("Rename failed")

        with pytest.raises(FileOperationError, match="Rename failed"):
            safe_rename_database(temp_db, dest, use_copy_fallback=False)

    @patch("src.utils.file_operations.safe_rename_with_retry")
    @patch("src.utils.file_operations.copy_with_verify")
    @patch("src.utils.file_operations.sqlite3.connect")
    def test_copy_fallback_integrity_check_fails(
        self,
        mock_connect: MagicMock,
        mock_copy: MagicMock,
        mock_rename: MagicMock,
        temp_db: Path,
        tmp_path: Path,
    ):
        """Test that copy fallback fails if integrity check fails."""
        dest = tmp_path / "dest.db"

        mock_rename.side_effect = FileOperationError("Rename failed")
        mock_copy.return_value = True
        mock_connect.return_value.execute.side_effect = sqlite3.Error("Corrupted")

        with pytest.raises(FileOperationError, match="integrity check"):
            safe_rename_database(temp_db, dest, use_copy_fallback=True)


@pytest.mark.skipif(
    platform.system() != "Windows",
    reason="Windows-specific behavior tests"
)
class TestWindowsSpecificBehavior:
    """Tests for Windows-specific file operation behavior."""

    def test_handles_windows_paths(self, tmp_path: Path):
        """Test that Windows paths are handled correctly."""
        # This would use actual Windows path syntax in a real Windows environment
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.commit()
        conn.close()

        dest = tmp_path / "renamed.db"
        result = safe_rename_database(db_path, dest)
        assert result is True
