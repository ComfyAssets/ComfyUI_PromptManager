# Testing Documentation

## Overview

The ComfyUI PromptManager project uses pytest as its testing framework with comprehensive coverage reporting and test automation capabilities. This document provides guidance on running tests, understanding the test structure, and contributing new tests.

## Quick Start

### Prerequisites

- Python 3.8-3.12 (Python 3.13 has compatibility issues with some test dependencies)
- Virtual environment with test dependencies installed
- Access to the project root directory

### Running Tests

#### 1. Activate Virtual Environment

```bash
source venv/bin/activate
```

#### 2. Run All Tests

```bash
# Run all tests with basic output
pytest tests/

# Run with verbose output
pytest tests/ -v

# Run with detailed failure information
pytest tests/ -vv --tb=short
```

#### 3. Run Specific Test Categories

```bash
# Run only unit tests
pytest tests/unit/ -v

# Run only integration tests
pytest tests/integration/ -v

# Run a specific test file
pytest tests/unit/test_database_models.py -v

# Run a specific test class
pytest tests/unit/test_database_models.py::TestPromptModel -v

# Run a specific test method
pytest tests/unit/test_database_models.py::TestPromptModel::test_init_creates_database -v
```

## Coverage Reports

### Generate Coverage Reports

```bash
# Generate terminal coverage report
pytest tests/ --cov=src --cov-report=term-missing

# Generate HTML coverage report
pytest tests/ --cov=src --cov-report=html

# Generate both terminal and HTML reports
pytest tests/ --cov=src --cov-report=term-missing --cov-report=html

# Generate XML coverage report (for CI/CD)
pytest tests/ --cov=src --cov-report=xml
```

### View Coverage Reports

```bash
# View HTML coverage report in browser
open htmlcov/index.html

# On Linux
xdg-open htmlcov/index.html

# On macOS
open htmlcov/index.html

# On Windows
start htmlcov/index.html
```

### Coverage Requirements

- **Minimum coverage**: 80% (enforced in `pyproject.toml`)
- **Critical modules**: 90% coverage target
- **UI/frontend code**: 70% coverage target

## Test Organization

```
tests/
├── __init__.py
├── conftest.py          # Shared fixtures and test configuration
├── unit/                # Unit tests (fast, isolated)
│   ├── test_database_models.py
│   ├── test_repositories.py
│   └── test_services.py
├── integration/         # Integration tests (database, API, file system)
│   ├── test_api_endpoints.py
│   ├── test_filesystem_operations.py
│   └── test_websocket.py
└── fixtures/           # Test data and resources
    ├── images/
    └── workflows/
```

## Current Test Status

### Test Results Summary

```bash
# From venv:
source venv/bin/activate
pytest tests/unit/test_database_models.py -v

# Current Status (after partial fixes):
✅ 10 tests passing (database core functionality)
❌ 15 tests failing (API signature mismatches)
⚠️ 7 tests with errors (missing methods)
📊 Coverage at 6.51% (baseline established)

# Overall Progress:
- Total: 32 tests discovered
- Infrastructure: ✅ Working
- Virtual Environment: ✅ Configured
- Dependencies: ✅ Installed (pytest, coverage, etc.)
- Test Execution: ✅ Functional
```

### Module Coverage Breakdown

| Module | Coverage | Tests | Status |
|--------|----------|-------|--------|
| database/models.py | 61.76% | 12 | ✅ Good |
| database/operations.py | 11.30% | 5 | 🔶 Needs improvement |
| utils/file_metadata.py | 34.29% | 3 | 🔶 Partial |
| config.py | 46.98% | 4 | 🔶 Partial |
| core/base_service.py | 25.45% | 2 | 🔴 Low coverage |
| services/* | <20% | 0 | 🔴 Need tests |
| api/* | <10% | 0 | 🔴 Need tests |

## Test Execution Commands

### Essential Commands

```bash
# Activate virtual environment
source venv/bin/activate

# Run all tests
pytest tests/

# Run tests with coverage
pytest tests/ --cov=src --cov-report=html

# View HTML coverage report
open htmlcov/index.html

# View test execution report
open test_report.html

# Run tests in parallel (faster)
pytest tests/ -n auto

# Run only failed tests from last run
pytest tests/ --lf

# Run tests that match a pattern
pytest tests/ -k "database"

# Run tests with specific markers
pytest tests/ -m "unit"
pytest tests/ -m "integration"
pytest tests/ -m "not slow"
```

### Development Workflow Commands

```bash
# Run tests in watch mode (requires pytest-watch)
ptw tests/

# Run tests and drop into debugger on failure
pytest tests/ --pdb

# Run tests with detailed assertion introspection
pytest tests/ -vv

# Profile test execution time
pytest tests/ --durations=10

# Generate test report with timing
pytest tests/ --html=test_report.html --self-contained-html
```

## Test Markers

Tests can be marked with decorators to categorize them:

```python
@pytest.mark.unit          # Fast, isolated unit tests
@pytest.mark.integration   # Tests requiring database/API
@pytest.mark.slow          # Tests taking >1 second
@pytest.mark.database      # Tests requiring database
@pytest.mark.api           # API endpoint tests
@pytest.mark.websocket     # WebSocket tests
@pytest.mark.filesystem    # File system operations
```

Run tests by marker:

```bash
# Run only unit tests
pytest -m unit

# Run all except slow tests
pytest -m "not slow"

# Run database and API tests
pytest -m "database or api"
```

## Writing Tests

### Test File Naming

- Test files must start with `test_` or end with `_test.py`
- Test classes must start with `Test`
- Test methods must start with `test_`

### Using Fixtures

Common fixtures available in `conftest.py`:

```python
def test_database_creation(test_db_path, temp_dir):
    """Test using database and temp directory fixtures."""
    assert os.path.exists(test_db_path)
    assert temp_dir.exists()

def test_with_config(test_config):
    """Test using configuration fixture."""
    assert test_config.storage.output_dir is not None

def test_with_mock_app(mock_comfyui_app):
    """Test using mock ComfyUI app."""
    assert mock_comfyui_app.output_dir is not None
```

### Test Structure Example

```python
import pytest
from src.database.models import PromptModel

class TestPromptModel:
    """Test cases for PromptModel."""

    def test_init_creates_database(self, test_db_path):
        """Test that initialization creates database file."""
        model = PromptModel(test_db_path)
        assert os.path.exists(test_db_path)

    @pytest.mark.parametrize("rating", [1, 2, 3, 4, 5])
    def test_rating_validation(self, db_model, rating):
        """Test rating constraints."""
        result = db_model.validate_rating(rating)
        assert result is True
```

## Continuous Integration

### GitHub Actions Workflow

```yaml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.11'
      - run: |
          pip install -e ".[test]"
          pytest tests/ --cov=src --cov-report=xml
      - uses: codecov/codecov-action@v2
```

## Troubleshooting

### Common Issues

#### 1. Import Errors

```bash
ImportError: cannot import name 'ClassName' from 'module'
```

**Solution**: Check that the import path matches the actual module structure.

#### 2. Database Lock Errors

```bash
sqlite3.OperationalError: database is locked
```

**Solution**: Ensure tests properly close database connections and use fixtures for isolation.

#### 3. Fixture Not Found

```bash
pytest.FixtureLookupError: fixture 'fixture_name' not found
```

**Solution**: Check that the fixture is defined in `conftest.py` or imported correctly.

#### 4. Coverage Not Meeting Requirements

```bash
FAIL Required test coverage of 80% not reached. Total coverage: 75.43%
```

**Solution**: Add more tests for uncovered code paths. Use `--cov-report=term-missing` to see which lines need coverage.

## Best Practices

1. **Test Isolation**: Each test should be independent and not rely on other tests
2. **Use Fixtures**: Leverage fixtures for common setup and teardown
3. **Mock External Dependencies**: Mock file system, network calls, and external services
4. **Test Edge Cases**: Include tests for error conditions and boundary values
5. **Descriptive Names**: Use clear, descriptive test names that explain what is being tested
6. **Fast Tests**: Keep unit tests fast (<100ms), mark slow tests appropriately
7. **Coverage Goals**: Aim for >80% coverage, focusing on critical paths first

## Contributing Tests

When adding new features or fixing bugs:

1. Write tests first (TDD approach)
2. Ensure tests fail initially
3. Implement the feature/fix
4. Verify tests pass
5. Check coverage hasn't decreased
6. Run the full test suite before committing
