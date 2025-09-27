"""
Integration tests for statistics service.
Tests interaction between stats components and database.
"""

import pytest
import json
from unittest.mock import Mock, patch, MagicMock
import time


class TestStatsIntegration:
    """Integration tests for stats service components."""

    def test_incremental_updates_match_full_calculation(self, stats_service, sample_prompts):
        """Incremental updates produce same results as full calculation."""
        # Arrange
        stats_service.db.get_all_prompts = Mock(return_value=sample_prompts)

        # Act - full calculation
        full_stats = stats_service.calculate_full()

        # Act - incremental calculation
        stats_service.reset()
        for prompt in sample_prompts:
            stats_service.update_incremental(prompt)
        incremental_stats = stats_service.get_current_stats()

        # Assert
        assert full_stats['total_prompts'] == incremental_stats['total_prompts']
        assert full_stats['category_breakdown'] == incremental_stats['category_breakdown']
        assert full_stats['tag_frequency'] == incremental_stats['tag_frequency']

    def test_background_scheduler_updates_stats(self, stats_service, scheduler_mock):
        """Background scheduler updates stats correctly."""
        # Arrange
        stats_service.enable_background_updates(interval=1)  # 1 second interval

        # Act - wait for scheduler to run
        time.sleep(1.5)

        # Assert
        assert scheduler_mock.called
        assert stats_service.last_update_time is not None
        assert stats_service.is_cache_valid()

    def test_cache_invalidation_on_prompt_changes(self, stats_service, db_mock):
        """Cache is invalidated when prompts are modified."""
        # Arrange
        initial_prompts = [{'id': 1, 'title': 'Test', 'category': 'A'}]
        db_mock.get_all_prompts = Mock(return_value=initial_prompts)

        # Act - get initial stats (cached)
        stats1 = stats_service.get_overview()

        # Act - modify prompt
        stats_service.on_prompt_updated({'id': 1, 'title': 'Updated', 'category': 'B'})

        # Act - get stats again (should recalculate)
        stats2 = stats_service.get_overview()

        # Assert
        assert stats_service.cache_invalidated
        assert db_mock.get_all_prompts.call_count == 2  # Called twice due to invalidation

    def test_api_endpoints_use_stats_service(self, test_client, stats_service):
        """API endpoints correctly use unified stats service."""
        # Arrange
        stats_service.get_overview = Mock(return_value={
            'total_prompts': 100,
            'category_breakdown': {'A': 50, 'B': 50}
        })

        # Act
        response = test_client.get('/api/stats/overview')
        data = json.loads(response.data)

        # Assert
        assert response.status_code == 200
        assert data['total_prompts'] == 100
        assert stats_service.get_overview.called

    def test_websocket_updates_on_stats_change(self, websocket_client, stats_service):
        """WebSocket sends updates when stats change."""
        # Arrange
        websocket_client.connect()
        messages = []
        websocket_client.on_message = lambda msg: messages.append(msg)

        # Act - trigger stats update
        stats_service.broadcast_update({
            'type': 'stats_update',
            'data': {'total_prompts': 150}
        })

        # Assert
        assert len(messages) == 1
        assert messages[0]['type'] == 'stats_update'
        assert messages[0]['data']['total_prompts'] == 150

    def test_database_indexes_improve_query_speed(self, db_connection):
        """Database indexes reduce query time significantly."""
        # Arrange - create test data
        cursor = db_connection.cursor()
        for i in range(10000):
            cursor.execute(
                "INSERT INTO prompts (title, category, created_at) VALUES (?, ?, ?)",
                (f"Prompt {i}", f"Cat{i % 100}", "2024-01-01")
            )
        db_connection.commit()

        # Act - query without index
        start = time.time()
        cursor.execute("SELECT COUNT(*) FROM prompts WHERE category = ?", ("Cat50",))
        no_index_time = time.time() - start

        # Act - add index
        cursor.execute("CREATE INDEX idx_prompts_category ON prompts(category)")

        # Act - query with index
        start = time.time()
        cursor.execute("SELECT COUNT(*) FROM prompts WHERE category = ?", ("Cat50",))
        with_index_time = time.time() - start

        # Assert
        assert with_index_time < no_index_time / 2, \
            f"Index should improve query speed by >50% (was {no_index_time:.4f}s, now {with_index_time:.4f}s)"

    def test_pagination_for_large_datasets(self, stats_service, large_dataset):
        """Large datasets are paginated correctly."""
        # Arrange
        stats_service.db.get_prompts_paginated = Mock(
            side_effect=lambda page, size: large_dataset[page*size:(page+1)*size]
        )

        # Act - get first page
        page1 = stats_service.get_prompts_page(page=0, size=100)

        # Act - get second page
        page2 = stats_service.get_prompts_page(page=1, size=100)

        # Assert
        assert len(page1) == 100
        assert len(page2) == 100
        assert page1[0]['id'] != page2[0]['id']

    def test_error_recovery_on_database_failure(self, stats_service):
        """Service recovers gracefully from database errors."""
        # Arrange
        stats_service.db.get_all_prompts = Mock(side_effect=Exception("Database error"))

        # Act
        result = stats_service.get_overview()

        # Assert
        assert result is not None
        assert result['error'] is None  # Should use cached or default values
        assert stats_service.is_in_fallback_mode()

    def test_memory_cleanup_after_large_operations(self, stats_service, large_dataset):
        """Memory is properly cleaned after processing large datasets."""
        import gc
        import psutil
        import os

        # Arrange
        process = psutil.Process(os.getpid())
        stats_service.db.get_all_prompts = Mock(return_value=large_dataset)

        # Act - process large dataset
        initial_memory = process.memory_info().rss / 1024 / 1024
        stats_service.process_bulk_update(large_dataset)
        after_processing = process.memory_info().rss / 1024 / 1024

        # Act - cleanup
        stats_service.cleanup()
        gc.collect()
        after_cleanup = process.memory_info().rss / 1024 / 1024

        # Assert
        memory_recovered = after_processing - after_cleanup
        assert memory_recovered > (after_processing - initial_memory) * 0.7, \
            "Should recover at least 70% of memory used during processing"


@pytest.fixture
def stats_service():
    """Create stats service for testing."""
    from src.services.stats_service import UnifiedStatsService
    return UnifiedStatsService()


@pytest.fixture
def test_client(app):
    """Create Flask test client."""
    return app.test_client()


@pytest.fixture
def websocket_client():
    """Mock WebSocket client for testing."""
    client = Mock()
    client.messages = []
    client.connect = Mock()
    client.send = Mock()
    return client


@pytest.fixture
def scheduler_mock():
    """Mock background scheduler."""
    with patch('src.services.stats_service.BackgroundScheduler') as mock:
        yield mock


@pytest.fixture
def db_mock():
    """Mock database connection."""
    return Mock()


@pytest.fixture
def db_connection():
    """Create test SQLite database."""
    import sqlite3
    conn = sqlite3.connect(':memory:')
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE prompts (
            id INTEGER PRIMARY KEY,
            title TEXT,
            category TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    return conn


@pytest.fixture
def large_dataset():
    """Generate large dataset for testing."""
    return [
        {
            'id': i,
            'title': f'Prompt {i}',
            'category': f'Category {i % 50}',
            'tags': [f'tag{j}' for j in range(5)],
            'created_at': '2024-01-01',
            'usage_count': i % 1000
        }
        for i in range(10000)
    ]