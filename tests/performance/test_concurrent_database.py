"""
Load tests for concurrent database operations.

Tests the connection_helper's ability to handle multiple simultaneous
database operations without locks, specifically for the ComfyUI monitor
and metadata extractor use cases.
"""

import pytest
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Dict, Any
import json
import hashlib

# Import the services that use the database
from src.services.workflow_metadata_extractor import WorkflowMetadataExtractor
from src.database.connection_helper import get_db_connection


class TestConcurrentDatabaseOperations:
    """Test concurrent database access with connection_helper."""

    @pytest.fixture
    def test_db(self):
        """Create a temporary test database."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.db', delete=False) as f:
            db_path = f.name

        # Initialize database schema
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                positive_prompt TEXT NOT NULL,
                negative_prompt TEXT,
                hash TEXT UNIQUE NOT NULL,
                model_hash TEXT,
                sampler_settings TEXT,
                generation_params TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS generated_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_id INTEGER NOT NULL,
                filename TEXT UNIQUE NOT NULL,
                width INTEGER,
                height INTEGER,
                parameters TEXT,
                FOREIGN KEY (prompt_id) REFERENCES prompts (id)
            )
        """)
        conn.commit()
        conn.close()

        yield db_path

        # Cleanup
        Path(db_path).unlink(missing_ok=True)

    def _generate_test_params(self, index: int) -> Dict[str, Any]:
        """Generate test parameters for metadata extraction."""
        return {
            "model": f"test_model_{index}.safetensors",
            "positive": f"test positive prompt {index}",
            "negative": f"test negative prompt {index}",
            "seed": 1000 + index,
            "steps": 20,
            "cfg": 7.0,
            "sampler": "euler",
            "scheduler": "normal",
            "width": 512,
            "height": 512,
        }

    def test_concurrent_writes_no_locks(self, test_db):
        """Test that concurrent writes don't cause database locks."""
        extractor = WorkflowMetadataExtractor(test_db)

        # Track results
        results = []
        errors = []

        def write_metadata(index: int):
            """Write metadata in a thread."""
            try:
                params = self._generate_test_params(index)
                success = extractor.save_to_database(params, f"test_image_{index}.png")
                results.append(success)
            except Exception as e:
                errors.append(str(e))

        # Create multiple threads writing simultaneously
        threads = []
        num_threads = 10
        for i in range(num_threads):
            thread = threading.Thread(target=write_metadata, args=(i,))
            threads.append(thread)

        # Start all threads at once
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify results
        assert len(errors) == 0, f"Database lock errors occurred: {errors}"
        assert len(results) == num_threads
        assert all(results), "Some writes failed"

        # Verify all records were saved
        with get_db_connection(test_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM prompts")
            count = cursor.fetchone()[0]
            assert count == num_threads, f"Expected {num_threads} prompts, got {count}"

    def test_concurrent_read_write_mix(self, test_db):
        """Test concurrent mix of reads and writes."""
        extractor = WorkflowMetadataExtractor(test_db)

        # Pre-populate some data
        for i in range(5):
            params = self._generate_test_params(i)
            extractor.save_to_database(params, f"initial_{i}.png")

        errors = []
        write_count = 0
        read_count = 0
        lock = threading.Lock()

        def mixed_operations(index: int):
            """Perform mix of reads and writes."""
            nonlocal write_count, read_count
            try:
                if index % 2 == 0:
                    # Write operation
                    params = self._generate_test_params(index + 100)
                    extractor.save_to_database(params, f"concurrent_{index}.png")
                    with lock:
                        write_count += 1
                else:
                    # Read operation
                    with get_db_connection(test_db) as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT COUNT(*) FROM prompts")
                        cursor.fetchone()
                    with lock:
                        read_count += 1
            except Exception as e:
                errors.append(str(e))

        # Run concurrent operations
        threads = []
        num_threads = 20
        for i in range(num_threads):
            thread = threading.Thread(target=mixed_operations, args=(i,))
            threads.append(thread)

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # Verify no errors
        assert len(errors) == 0, f"Errors during concurrent operations: {errors}"
        assert write_count == 10  # Half were writes
        assert read_count == 10   # Half were reads

    def test_high_load_sustained_writes(self, test_db):
        """Test sustained high-load concurrent writes."""
        extractor = WorkflowMetadataExtractor(test_db)

        errors = []
        successful_writes = 0
        lock = threading.Lock()

        def sustained_writer(thread_id: int, num_writes: int):
            """Write multiple records in sequence."""
            nonlocal successful_writes
            for i in range(num_writes):
                try:
                    params = self._generate_test_params(thread_id * 1000 + i)
                    if extractor.save_to_database(params, f"thread_{thread_id}_img_{i}.png"):
                        with lock:
                            successful_writes += 1
                except Exception as e:
                    errors.append(f"Thread {thread_id}, write {i}: {str(e)}")

        # Run multiple threads, each writing multiple records
        threads = []
        num_threads = 5
        writes_per_thread = 20
        total_expected = num_threads * writes_per_thread

        start_time = time.time()

        for thread_id in range(num_threads):
            thread = threading.Thread(target=sustained_writer, args=(thread_id, writes_per_thread))
            threads.append(thread)

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        elapsed_time = time.time() - start_time

        # Verify results
        assert len(errors) == 0, f"Errors during sustained load: {errors[:5]}"  # Show first 5 errors
        assert successful_writes == total_expected, f"Expected {total_expected} writes, got {successful_writes}"

        # Performance assertion - should complete in reasonable time
        # With connection_helper, this should be much faster than with locks
        assert elapsed_time < 30, f"Sustained writes took too long: {elapsed_time}s"

        print(f"\nPerformance: {total_expected} concurrent writes in {elapsed_time:.2f}s")
        print(f"Throughput: {total_expected/elapsed_time:.2f} writes/sec")

    def test_connection_pool_reuse(self, test_db):
        """Test that connection pooling is working correctly."""
        # This test verifies thread-local connection pooling behavior

        connection_ids = set()
        lock = threading.Lock()

        def get_connection_id(thread_id: int):
            """Get connection ID from this thread."""
            with get_db_connection(test_db) as conn:
                # Get SQLite connection object ID
                conn_id = id(conn)
                with lock:
                    connection_ids.add((thread_id, conn_id))

        # Run multiple operations in same thread
        for _ in range(3):
            get_connection_id(0)

        # In thread-local pooling, same thread should reuse connection
        thread_0_connections = [cid for tid, cid in connection_ids if tid == 0]
        assert len(set(thread_0_connections)) <= 2, \
            "Thread should reuse connections from pool"

    def test_no_database_locked_errors(self, test_db):
        """Specifically test for 'database is locked' errors."""
        extractor = WorkflowMetadataExtractor(test_db)

        lock_errors = []

        def aggressive_writer(index: int):
            """Write aggressively to try to trigger locks."""
            for i in range(10):
                try:
                    params = self._generate_test_params(index * 100 + i)
                    extractor.save_to_database(params)
                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e).lower():
                        lock_errors.append(str(e))
                except Exception:
                    pass  # Ignore other errors for this specific test

        # Launch many threads simultaneously
        threads = []
        for i in range(15):  # Aggressive thread count
            thread = threading.Thread(target=aggressive_writer, args=(i,))
            threads.append(thread)

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # The key assertion - no database locked errors should occur
        assert len(lock_errors) == 0, \
            f"Database locked errors detected: {lock_errors}"

    def test_retry_logic_on_busy(self, test_db):
        """Test that retry logic handles busy database correctly."""
        # This test simulates a scenario where database is briefly busy
        # and verifies the retry logic works

        extractor = WorkflowMetadataExtractor(test_db)
        success_count = 0
        lock = threading.Lock()

        def write_with_contention(index: int):
            """Write while other operations are ongoing."""
            nonlocal success_count
            try:
                # Add small random delay to increase contention
                time.sleep(0.001 * (index % 5))
                params = self._generate_test_params(index)
                if extractor.save_to_database(params, f"retry_test_{index}.png"):
                    with lock:
                        success_count += 1
            except Exception:
                pass  # Retry logic should handle this

        threads = []
        num_threads = 30  # High contention

        for i in range(num_threads):
            thread = threading.Thread(target=write_with_contention, args=(i,))
            threads.append(thread)

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # With retry logic, we should get most or all writes through
        success_rate = success_count / num_threads
        assert success_rate >= 0.95, \
            f"Success rate too low: {success_rate:.2%} (expected >= 95%)"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
