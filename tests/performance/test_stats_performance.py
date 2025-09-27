"""
Performance tests for statistics service.
Tests to ensure stats load quickly and efficiently.
"""

import time
import pytest
from unittest.mock import Mock, patch
import psutil
import os


class TestStatsPerformance:
    """Performance benchmarks for statistics operations."""

    def test_stats_overview_under_1_second(self, stats_service, sample_prompts):
        """Stats overview must complete under 1 second."""
        # Arrange
        stats_service.db.get_all_prompts = Mock(return_value=sample_prompts[:1000])

        # Act
        start = time.time()
        result = stats_service.get_overview()
        duration = time.time() - start

        # Assert
        assert duration < 1.0, f"Stats overview took {duration:.2f}s, should be < 1s"
        assert result is not None
        assert 'total_prompts' in result
        assert 'category_breakdown' in result

    def test_handles_10k_prompts_efficiently(self, stats_service, large_dataset):
        """Service handles 10,000+ prompts efficiently."""
        # Arrange
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB
        stats_service.db.get_all_prompts = Mock(return_value=large_dataset)

        # Act
        start = time.time()
        result = stats_service.get_overview()
        calculation_time = time.time() - start
        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory

        # Assert
        assert calculation_time < 2.0, f"10k prompts took {calculation_time:.2f}s, should be < 2s"
        assert memory_increase < 100, f"Memory increased by {memory_increase:.1f}MB, should be < 100MB"
        assert result['total_prompts'] == 10000

    def test_cached_responses_instant(self, stats_service, sample_prompts):
        """Cached responses return instantly (<10ms)."""
        # Arrange
        stats_service.db.get_all_prompts = Mock(return_value=sample_prompts)
        # First call to populate cache
        stats_service.get_overview()

        # Act - second call should hit cache
        start = time.time()
        result = stats_service.get_overview()
        duration = time.time() - start

        # Assert
        assert duration < 0.01, f"Cached response took {duration*1000:.2f}ms, should be < 10ms"
        assert stats_service.db.get_all_prompts.call_count == 1  # Only called once

    def test_incremental_update_faster_than_full(self, stats_service, sample_prompts):
        """Incremental updates are 10x faster than full recalculation."""
        # Arrange
        stats_service.db.get_all_prompts = Mock(return_value=sample_prompts)

        # Act - full calculation
        start = time.time()
        stats_service.calculate_full()
        full_time = time.time() - start

        # Act - incremental update
        start = time.time()
        stats_service.update_incremental(sample_prompts[0])
        incremental_time = time.time() - start

        # Assert
        assert incremental_time < full_time / 10, \
            f"Incremental ({incremental_time:.4f}s) should be 10x faster than full ({full_time:.4f}s)"

    def test_no_blocking_on_startup(self, stats_service):
        """Stats service doesn't block on startup."""
        # Act
        start = time.time()
        stats_service.initialize()
        startup_time = time.time() - start

        # Assert
        assert startup_time < 0.1, f"Startup took {startup_time:.2f}s, should be < 0.1s"
        assert stats_service.is_ready()

    def test_concurrent_requests_handled_efficiently(self, stats_service, sample_prompts):
        """Multiple concurrent requests don't degrade performance."""
        import concurrent.futures

        # Arrange
        stats_service.db.get_all_prompts = Mock(return_value=sample_prompts)

        # Act - simulate 10 concurrent requests
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            start = time.time()
            futures = [executor.submit(stats_service.get_overview) for _ in range(10)]
            results = [f.result() for f in futures]
            total_time = time.time() - start

        # Assert
        assert total_time < 1.5, f"10 concurrent requests took {total_time:.2f}s"
        assert all(r is not None for r in results)
        # Should use caching, so DB called minimally
        assert stats_service.db.get_all_prompts.call_count <= 2


@pytest.fixture
def stats_service():
    """Mock stats service for testing."""
    from src.services.stats_service import UnifiedStatsService
    service = UnifiedStatsService()
    service.db = Mock()
    return service


@pytest.fixture
def sample_prompts():
    """Generate sample prompts for testing."""
    return [
        {
            'id': i,
            'title': f'Prompt {i}',
            'category': f'Category {i % 10}',
            'tags': [f'tag{i % 5}', f'tag{i % 3}'],
            'created_at': '2024-01-01',
            'updated_at': '2024-01-01',
            'usage_count': i % 100
        }
        for i in range(1000)
    ]


@pytest.fixture
def large_dataset():
    """Generate large dataset for stress testing."""
    return [
        {
            'id': i,
            'title': f'Prompt {i}',
            'category': f'Category {i % 50}',
            'tags': [f'tag{j}' for j in range(i % 10)],
            'created_at': '2024-01-01',
            'updated_at': '2024-01-01',
            'usage_count': i % 1000,
            'metadata': {f'key{j}': f'value{j}' for j in range(10)}
        }
        for i in range(10000)
    ]