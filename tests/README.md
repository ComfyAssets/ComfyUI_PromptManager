# ComfyUI PromptManager Testing Framework

This directory contains a comprehensive testing suite for the ComfyUI PromptManager project, designed to ensure code quality, reliability, and maintainability through systematic testing approaches.

## 📋 Table of Contents

- [Overview](#overview)
- [Test Structure](#test-structure)
- [Running Tests](#running-tests)
- [Test Categories](#test-categories)
- [Coverage Requirements](#coverage-requirements)
- [Writing Tests](#writing-tests)
- [Continuous Integration](#continuous-integration)
- [Troubleshooting](#troubleshooting)

## 🎯 Overview

The testing framework provides:

- **Comprehensive Coverage**: Unit, integration, and end-to-end tests
- **Real-time Testing**: WebSocket and real-time communication tests
- **Database Testing**: Full database operations with isolation
- **File System Testing**: Image processing and file monitoring
- **API Testing**: REST endpoints and request/response validation
- **Performance Testing**: Benchmarks and performance regression detection
- **Automated CI/CD**: Continuous integration ready

### Key Features

✅ **80%+ Code Coverage** requirement with detailed reporting  
✅ **Parallel Test Execution** for faster feedback  
✅ **Test Isolation** with fresh databases and temporary directories  
✅ **Comprehensive Fixtures** for database, files, and mocks  
✅ **Multi-environment Testing** via tox  
✅ **Real File Testing** with actual image and video files  

## 📁 Test Structure

```
tests/
├── conftest.py                    # Pytest configuration and fixtures
├── unit/                          # Unit tests (fast, isolated)
│   ├── test_database_models.py    # Database models and schema tests
│   ├── test_repositories.py       # Repository layer tests
│   └── test_services.py           # Service layer business logic tests
├── integration/                   # Integration tests (slower, real dependencies)
│   ├── test_api_endpoints.py      # REST API endpoint tests
│   ├── test_websocket.py          # WebSocket communication tests
│   └── test_filesystem_operations.py # File system and image processing tests
├── fixtures/                      # Test data and fixtures
│   ├── sample_images/             # Sample image files for testing
│   ├── sample_workflows/          # ComfyUI workflow test data
│   └── test_data.json             # Structured test data
└── performance/                   # Performance and benchmark tests
    ├── test_database_performance.py
    ├── test_image_processing_performance.py
    └── benchmarks/
```

### Test Categories

| Category | Marker | Purpose | Speed |
|----------|--------|---------|-------|
| **Unit** | `@pytest.mark.unit` | Fast, isolated component tests | ~100ms |
| **Integration** | `@pytest.mark.integration` | Real dependencies, databases | ~1-5s |
| **Database** | `@pytest.mark.database` | Database operations and transactions | ~1-3s |
| **API** | `@pytest.mark.api` | HTTP endpoints and validation | ~500ms-2s |
| **WebSocket** | `@pytest.mark.websocket` | Real-time communication | ~1-3s |
| **FileSystem** | `@pytest.mark.filesystem` | File operations and monitoring | ~2-5s |
| **Slow** | `@pytest.mark.slow` | Long-running tests (>5s) | >5s |
| **Performance** | `@pytest.mark.performance` | Benchmarks and profiling | Variable |

## 🚀 Running Tests

### Quick Start

```bash
# Install test dependencies
pip install -e .[test]

# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test categories
pytest -m unit              # Unit tests only
pytest -m integration       # Integration tests only
pytest -m "not slow"        # Skip slow tests
```

### Using Test Runner Scripts

#### Shell Script (Recommended)
```bash
# Make executable
chmod +x scripts/run_tests.sh

# Run different test suites
./scripts/run_tests.sh unit              # Unit tests
./scripts/run_tests.sh integration       # Integration tests
./scripts/run_tests.sh coverage          # Tests with coverage
./scripts/run_tests.sh quick             # Fast smoke tests
./scripts/run_tests.sh all               # All tests
./scripts/run_tests.sh ci                # Full CI suite

# Run specific categories
./scripts/run_tests.sh database          # Database tests
./scripts/run_tests.sh api               # API tests
./scripts/run_tests.sh websocket         # WebSocket tests
./scripts/run_tests.sh filesystem        # File system tests

# Utilities
./scripts/run_tests.sh clean             # Clean artifacts
./scripts/run_tests.sh lint              # Code quality
./scripts/run_tests.sh format            # Code formatting
```

#### Python Script
```bash
# More advanced control
python scripts/test_runner.py unit --verbose
python scripts/test_runner.py coverage --html --parallel
python scripts/test_runner.py all --pattern "test_database"
python scripts/test_runner.py api --failfast --timeout 120
```

### Advanced Options

```bash
# Parallel execution (faster)
pytest -n auto

# Stop on first failure
pytest -x

# Verbose output with timings
pytest -v --durations=10

# Run specific test patterns
pytest -k "test_database"
pytest -k "test_api and not slow"

# Generate different coverage reports
pytest --cov=src \
    --cov-report=html:htmlcov \
    --cov-report=xml:coverage.xml \
    --cov-report=term-missing

# Run with timeout (prevent hanging tests)
pytest --timeout=300

# Run tests with specific log levels
LOG_LEVEL=DEBUG pytest -v
```

## 📊 Test Categories Explained

### Unit Tests (`tests/unit/`)

**Purpose**: Fast, isolated tests of individual components  
**Speed**: ~100ms per test  
**Dependencies**: None (mocked)  

- ✅ Database models and schema validation
- ✅ Repository CRUD operations
- ✅ Service business logic
- ✅ Utility functions and helpers
- ✅ Data validation and transformation

**Example**:
```python
def test_prompt_creation(prompt_repository, sample_prompt_data):
    """Test creating a new prompt through repository."""
    prompt_id = prompt_repository.create(sample_prompt_data)
    assert prompt_id is not None
    
    prompt = prompt_repository.get_by_id(prompt_id)
    assert prompt['positive_prompt'] == sample_prompt_data['positive_prompt']
```

### Integration Tests (`tests/integration/`)

**Purpose**: Test interactions between components with real dependencies  
**Speed**: ~1-5s per test  
**Dependencies**: Real database, file system, network  

#### API Endpoint Tests
- ✅ HTTP request/response validation
- ✅ Authentication and authorization
- ✅ Error handling and status codes
- ✅ Request/response data serialization
- ✅ Pagination and filtering

#### WebSocket Tests
- ✅ Connection lifecycle management
- ✅ Real-time message broadcasting
- ✅ Progress update notifications
- ✅ Error handling and reconnection
- ✅ Multi-client communication

#### File System Tests
- ✅ Image scanning and processing
- ✅ Thumbnail generation
- ✅ Metadata extraction from PNG/JPEG
- ✅ File monitoring and change detection
- ✅ Directory traversal and filtering

## 📈 Coverage Requirements

### Minimum Coverage Targets

| Component | Target | Critical |
|-----------|--------|----------|
| **Database Models** | 90% | ✅ Schema, migrations |
| **Repositories** | 85% | ✅ CRUD operations |
| **Services** | 80% | ✅ Business logic |
| **API Endpoints** | 85% | ✅ Request handling |
| **Utilities** | 75% | ⚠️  Helper functions |
| **Overall Project** | 80% | ✅ Required for CI |

### Coverage Reports

```bash
# Generate HTML coverage report
pytest --cov=src --cov-report=html
# Open: htmlcov/index.html

# Terminal coverage report
pytest --cov=src --cov-report=term-missing

# XML coverage for CI systems
pytest --cov=src --cov-report=xml
```

### Coverage Analysis

The coverage reports show:
- **Line Coverage**: Which lines of code were executed
- **Branch Coverage**: Which code branches were taken
- **Missing Lines**: Specific lines not covered by tests
- **Excluded Lines**: Lines marked with `# pragma: no cover`

## ✍️ Writing Tests

### Test Naming Conventions

```python
# ✅ Good test names (descriptive, specific)
def test_create_prompt_with_valid_data_returns_id()
def test_get_prompt_by_nonexistent_id_returns_none()
def test_search_prompts_by_category_filters_correctly()
def test_api_create_prompt_with_invalid_data_returns_400()

# ❌ Poor test names (vague, generic)
def test_prompt_creation()
def test_get_prompt()
def test_search()
```

### Test Structure (AAA Pattern)

```python
def test_prompt_repository_update_existing_prompt(prompt_repository, sample_prompt_data):
    """Test updating an existing prompt via repository."""
    
    # Arrange - Set up test data and conditions
    prompt_id = prompt_repository.create(sample_prompt_data)
    update_data = {
        'category': 'updated_category',
        'rating': 5,
        'notes': 'Updated notes'
    }
    
    # Act - Execute the operation being tested
    success = prompt_repository.update(prompt_id, update_data)
    
    # Assert - Verify the results
    assert success is True
    
    updated_prompt = prompt_repository.get_by_id(prompt_id)
    assert updated_prompt['category'] == 'updated_category'
    assert updated_prompt['rating'] == 5
    assert updated_prompt['notes'] == 'Updated notes'
```

### Using Fixtures

#### Database Fixtures
```python
def test_database_operations(db_operations, sample_prompt_data):
    """Test using database operations fixture."""
    # Fixture provides fresh database instance
    prompt_id = db_operations.save_prompt(**sample_prompt_data)
    assert prompt_id is not None
```

#### File System Fixtures
```python
def test_image_processing(test_image_file, thumbnail_service):
    """Test using image file fixture."""
    # Fixture provides real PNG file with metadata
    thumbnail_path = thumbnail_service.generate_thumbnail(str(test_image_file))
    assert thumbnail_path is not None
```

#### Mock Fixtures
```python
def test_websocket_communication(mock_websocket, websocket_manager):
    """Test using WebSocket mock."""
    # Fixture provides configured AsyncMock
    await websocket_manager.connect_client(mock_websocket)
    mock_websocket.send.assert_called_once()
```

### Parametrized Tests

```python
@pytest.mark.parametrize("rating,expected_valid", [
    (1, True),
    (3, True), 
    (5, True),
    (0, False),  # Invalid
    (6, False),  # Invalid
    (10, False)  # Invalid
])
def test_prompt_rating_validation(prompt_repository, rating, expected_valid):
    """Test prompt rating validation with various values."""
    prompt_data = create_test_prompt()
    prompt_data['rating'] = rating
    
    if expected_valid:
        prompt_id = prompt_repository.create(prompt_data)
        assert prompt_id is not None
    else:
        with pytest.raises(ValueError):
            prompt_repository.create(prompt_data)
```

### Async Test Functions

```python
@pytest.mark.asyncio
async def test_websocket_message_broadcasting(websocket_manager):
    """Test WebSocket message broadcasting to multiple clients."""
    # Connect multiple clients
    clients = []
    for i in range(3):
        mock_ws = AsyncMock()
        client_id = await websocket_manager.connect_client(mock_ws)
        clients.append((client_id, mock_ws))
    
    # Broadcast message
    test_message = {'type': 'test', 'data': 'broadcast test'}
    await websocket_manager.broadcast_message(test_message)
    
    # Verify all clients received message
    for client_id, mock_ws in clients:
        mock_ws.send.assert_called_once()
```

### Error Testing

```python
def test_api_handles_invalid_json_gracefully(client):
    """Test API error handling for malformed requests."""
    response = client.post(
        "/api/prompts",
        data="invalid json data",
        headers={"content-type": "application/json"}
    )
    
    assert response.status_code == 400
    assert "error" in response.json()
    assert "invalid json" in response.json()["error"].lower()
```

## 🔄 Continuous Integration

### GitHub Actions Integration

The testing framework is designed to work with CI/CD pipelines:

```yaml
# .github/workflows/tests.yml
name: Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: [3.8, 3.9, "3.10", "3.11", "3.12"]
    
    steps:
    - uses: actions/checkout@v3
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: ${{ matrix.python-version }}
    
    - name: Install dependencies
      run: |
        pip install -e .[test]
    
    - name: Run tests
      run: |
        ./scripts/run_tests.sh ci
    
    - name: Upload coverage
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml
```

### Local CI Testing

```bash
# Run the same tests as CI
./scripts/run_tests.sh ci

# Or use tox for multi-environment testing
pip install tox
tox  # Tests all Python versions
```

### Pre-commit Hooks

```bash
# Install pre-commit hooks
pip install pre-commit
pre-commit install

# Run hooks manually
pre-commit run --all-files
```

## 🛠️ Troubleshooting

### Common Issues

#### 1. **Import Errors**
```
ModuleNotFoundError: No module named 'src'
```
**Solution**: Ensure `PYTHONPATH` includes project root:
```bash
export PYTHONPATH="/path/to/project:$PYTHONPATH"
# Or use the test runner scripts which handle this automatically
```

#### 2. **Database Locked Errors**
```
sqlite3.OperationalError: database is locked
```
**Solution**: Tests should use isolated databases via fixtures. Check that:
- Each test uses the `db_model` or `db_operations` fixture
- No tests share database files
- Connections are properly closed in fixtures

#### 3. **File Permission Errors**
```
PermissionError: [Errno 13] Permission denied
```
**Solution**: Check file permissions and use `temp_dir` fixture:
```python
def test_file_operation(temp_dir):
    test_file = temp_dir / "test.txt"
    test_file.write_text("test content")  # Uses temp directory
```

#### 4. **Timeout Errors**
```
Failed: Timeout >300.0s
```
**Solution**: Either optimize slow tests or increase timeout:
```bash
pytest --timeout=600  # 10 minutes
# Or mark slow tests
@pytest.mark.slow
def test_long_operation():
    pass
```

#### 5. **Coverage Too Low**
```
FAILED: coverage is 75%, required 80%
```
**Solution**: Add tests for uncovered code or adjust requirements:
```bash
# Find uncovered lines
pytest --cov=src --cov-report=term-missing

# Generate detailed HTML report
pytest --cov=src --cov-report=html
# Open htmlcov/index.html to see missing coverage
```

### Debugging Tests

#### Using pytest debugger
```bash
# Drop into debugger on failures
pytest --pdb

# Drop into debugger on first failure
pytest -x --pdb

# Use specific test with debugger
pytest tests/unit/test_database_models.py::test_specific_function --pdb
```

#### Verbose output
```bash
# Maximum verbosity
pytest -vvv

# Show test timings
pytest --durations=0

# Show stdout/stderr
pytest -s
```

#### Logging during tests
```python
import logging

def test_with_logging(caplog):
    with caplog.at_level(logging.INFO):
        # Your test code here
        pass
    
    assert "Expected log message" in caplog.text
```

### Performance Issues

#### Slow test identification
```bash
# Show slowest tests
pytest --durations=10

# Profile test execution
pytest --profile

# Run only fast tests
pytest -m "not slow"
```

#### Parallel execution
```bash
# Install pytest-xdist
pip install pytest-xdist

# Run tests in parallel
pytest -n auto  # Use all CPU cores
pytest -n 4     # Use 4 processes
```

### Environment Issues

#### Missing dependencies
```bash
# Install all test dependencies
pip install -e .[test]

# Or install specific missing packages
pip install pytest pytest-cov pytest-asyncio
```

#### Python version compatibility
```bash
# Test multiple Python versions with tox
pip install tox
tox

# Or test specific version
tox -e py39
```

## 📚 Additional Resources

- **pytest Documentation**: https://docs.pytest.org/
- **pytest-cov Documentation**: https://pytest-cov.readthedocs.io/
- **pytest-asyncio Documentation**: https://pytest-asyncio.readthedocs.io/
- **Python Testing Best Practices**: https://docs.python-guide.org/writing/tests/
- **FastAPI Testing**: https://fastapi.tiangolo.com/tutorial/testing/

## 🤝 Contributing

When adding new tests:

1. **Follow naming conventions** - descriptive, specific test names
2. **Use appropriate fixtures** - leverage existing fixtures for consistency  
3. **Add proper markers** - mark tests with appropriate categories
4. **Maintain isolation** - tests should not depend on each other
5. **Update coverage** - ensure new code is properly tested
6. **Document complex tests** - add docstrings for non-obvious test logic

### Test Review Checklist

- ✅ Test name clearly describes what is being tested
- ✅ Test follows AAA pattern (Arrange, Act, Assert)
- ✅ Uses appropriate fixtures and mocks
- ✅ Has proper error handling test cases
- ✅ Includes edge cases and boundary conditions
- ✅ Maintains good coverage of new code
- ✅ Runs reliably without flaky behavior
- ✅ Uses appropriate test markers

---

Happy testing! 🧪✨