#!/bin/bash
# Comprehensive test runner script for ComfyUI PromptManager
# This script provides convenient shortcuts for common testing scenarios

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Configuration
PYTHON_CMD="python"
PYTEST_CMD="$PYTHON_CMD -m pytest"
COVERAGE_CMD="$PYTHON_CMD -m coverage"
TEST_DIR="$PROJECT_ROOT/tests"
SRC_DIR="$PROJECT_ROOT/src"

# Function to print colored output
print_colored() {
    local color=$1
    local message=$2
    echo -e "${color}${message}${NC}"
}

# Function to print section headers
print_header() {
    local message=$1
    echo
    print_colored "$CYAN" "================================"
    print_colored "$CYAN" "$message"
    print_colored "$CYAN" "================================"
    echo
}

# Function to check if dependencies are installed
check_dependencies() {
    print_header "🔍 Checking Dependencies"
    
    if ! command -v python &> /dev/null; then
        print_colored "$RED" "❌ Python not found. Please install Python 3.8+."
        exit 1
    fi
    
    if ! python -c "import pytest" &> /dev/null; then
        print_colored "$YELLOW" "⚠️  pytest not found. Installing test dependencies..."
        pip install -e .[test] || {
            print_colored "$RED" "❌ Failed to install test dependencies"
            exit 1
        }
    fi
    
    print_colored "$GREEN" "✅ Dependencies are installed"
}

# Function to set up test environment
setup_test_env() {
    print_header "🛠️  Setting up Test Environment"
    
    export PYTHONPATH="$PROJECT_ROOT:$SRC_DIR:$PYTHONPATH"
    export TESTING=1
    export LOG_LEVEL=DEBUG
    
    # Create necessary directories
    mkdir -p "$PROJECT_ROOT/test_reports"
    mkdir -p "$PROJECT_ROOT/htmlcov"
    
    print_colored "$GREEN" "✅ Test environment configured"
}

# Function to run unit tests
run_unit_tests() {
    print_header "🧪 Running Unit Tests"
    
    $PYTEST_CMD "$TEST_DIR/unit" \
        -v \
        --tb=short \
        -m "unit" \
        --durations=10 \
        "${@}"
}

# Function to run integration tests
run_integration_tests() {
    print_header "🔗 Running Integration Tests"
    
    $PYTEST_CMD "$TEST_DIR/integration" \
        -v \
        --tb=short \
        -m "integration" \
        --durations=10 \
        "${@}"
}

# Function to run all tests
run_all_tests() {
    print_header "🚀 Running All Tests"
    
    $PYTEST_CMD "$TEST_DIR" \
        -v \
        --tb=short \
        --durations=10 \
        "${@}"
}

# Function to run tests with coverage
run_coverage_tests() {
    print_header "📊 Running Tests with Coverage"
    
    $PYTEST_CMD "$TEST_DIR" \
        --cov=src \
        --cov-report=html:htmlcov \
        --cov-report=term-missing \
        --cov-report=xml \
        --cov-fail-under=80 \
        -v \
        "${@}"
    
    if [ -f "$PROJECT_ROOT/htmlcov/index.html" ]; then
        print_colored "$GREEN" "📊 Coverage report generated: file://$PROJECT_ROOT/htmlcov/index.html"
    fi
}

# Function to run quick smoke tests
run_quick_tests() {
    print_header "⚡ Running Quick Tests"
    
    $PYTEST_CMD "$TEST_DIR/unit" \
        -v \
        --tb=short \
        -m "unit and not slow" \
        -x \
        --durations=5 \
        "${@}"
}

# Function to run specific test categories
run_database_tests() {
    print_header "🗃️  Running Database Tests"
    
    $PYTEST_CMD "$TEST_DIR" \
        -v \
        --tb=short \
        -m "database" \
        "${@}"
}

run_api_tests() {
    print_header "🌐 Running API Tests"
    
    $PYTEST_CMD "$TEST_DIR/integration/test_api_endpoints.py" \
        -v \
        --tb=short \
        -m "api" \
        "${@}"
}

run_websocket_tests() {
    print_header "🔌 Running WebSocket Tests"
    
    $PYTEST_CMD "$TEST_DIR/integration/test_websocket.py" \
        -v \
        --tb=short \
        -m "websocket" \
        "${@}"
}

run_filesystem_tests() {
    print_header "📁 Running File System Tests"
    
    $PYTEST_CMD "$TEST_DIR/integration/test_filesystem_operations.py" \
        -v \
        --tb=short \
        -m "filesystem" \
        "${@}"
}

# Function to run linting
run_linting() {
    print_header "🧹 Running Code Quality Checks"
    
    if command -v black &> /dev/null; then
        print_colored "$BLUE" "🖤 Running Black formatter check..."
        black --check --diff src tests || print_colored "$YELLOW" "⚠️  Black formatting issues found"
    fi
    
    if command -v flake8 &> /dev/null; then
        print_colored "$BLUE" "🔍 Running Flake8 linting..."
        flake8 src tests || print_colored "$YELLOW" "⚠️  Flake8 issues found"
    fi
    
    if command -v mypy &> /dev/null; then
        print_colored "$BLUE" "🔬 Running MyPy type checking..."
        mypy src || print_colored "$YELLOW" "⚠️  MyPy type issues found"
    fi
}

# Function to format code
format_code() {
    print_header "🎨 Formatting Code"
    
    if command -v black &> /dev/null; then
        print_colored "$BLUE" "🖤 Running Black formatter..."
        black src tests
    else
        print_colored "$YELLOW" "⚠️  Black not found, skipping formatting"
    fi
    
    if command -v isort &> /dev/null; then
        print_colored "$BLUE" "📚 Running isort..."
        isort src tests
    else
        print_colored "$YELLOW" "⚠️  isort not found, skipping import sorting"
    fi
}

# Function to clean test artifacts
clean_artifacts() {
    print_header "🧹 Cleaning Test Artifacts"
    
    # Remove coverage files
    rm -rf "$PROJECT_ROOT/htmlcov"
    rm -f "$PROJECT_ROOT/.coverage"
    rm -f "$PROJECT_ROOT/coverage.xml"
    rm -f "$PROJECT_ROOT/coverage.json"
    
    # Remove pytest cache
    rm -rf "$PROJECT_ROOT/.pytest_cache"
    
    # Remove test reports
    rm -rf "$PROJECT_ROOT/test_reports"
    
    # Remove Python cache files
    find "$PROJECT_ROOT" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "$PROJECT_ROOT" -type f -name "*.pyc" -delete 2>/dev/null || true
    find "$PROJECT_ROOT" -type f -name "*.pyo" -delete 2>/dev/null || true
    find "$PROJECT_ROOT" -type f -name "*.pyd" -delete 2>/dev/null || true
    
    # Remove tox cache
    rm -rf "$PROJECT_ROOT/.tox"
    
    print_colored "$GREEN" "✅ Cleaned test artifacts"
}

# Function to run continuous integration tests
run_ci_tests() {
    print_header "🤖 Running CI Tests"
    
    print_colored "$BLUE" "Step 1: Linting and formatting checks..."
    run_linting
    
    print_colored "$BLUE" "Step 2: Unit tests..."
    run_unit_tests --tb=short
    
    print_colored "$BLUE" "Step 3: Integration tests..."
    run_integration_tests --tb=short
    
    print_colored "$BLUE" "Step 4: Coverage report..."
    run_coverage_tests --tb=short
    
    print_colored "$GREEN" "✅ CI tests completed successfully"
}

# Function to run performance tests
run_performance_tests() {
    print_header "🏃 Running Performance Tests"
    
    if [ -d "$TEST_DIR/performance" ]; then
        $PYTEST_CMD "$TEST_DIR/performance" \
            -v \
            --tb=short \
            -m "performance" \
            --benchmark-only \
            "${@}"
    else
        print_colored "$YELLOW" "⚠️  Performance tests directory not found, skipping..."
    fi
}

# Function to run security tests
run_security_tests() {
    print_header "🔒 Running Security Tests"
    
    if command -v bandit &> /dev/null; then
        print_colored "$BLUE" "🛡️  Running Bandit security scan..."
        bandit -r src -f json -o bandit-report.json || print_colored "$YELLOW" "⚠️  Security issues found, check bandit-report.json"
    else
        print_colored "$YELLOW" "⚠️  Bandit not found, skipping security tests"
    fi
    
    if command -v safety &> /dev/null; then
        print_colored "$BLUE" "🔍 Running Safety dependency scan..."
        safety check || print_colored "$YELLOW" "⚠️  Vulnerable dependencies found"
    else
        print_colored "$YELLOW" "⚠️  Safety not found, skipping dependency scan"
    fi
}

# Function to show help
show_help() {
    cat << EOF
🧪 ComfyUI PromptManager Test Runner

Usage: $0 [COMMAND] [OPTIONS]

COMMANDS:
  unit              Run unit tests only
  integration       Run integration tests only
  all               Run all tests
  coverage          Run tests with coverage reporting
  quick             Run quick smoke tests
  database          Run database-specific tests
  api               Run API endpoint tests
  websocket         Run WebSocket tests
  filesystem        Run file system tests
  performance       Run performance benchmarks
  lint              Run code quality checks
  format            Format code with Black and isort
  security          Run security scans
  ci                Run complete CI test suite
  clean             Clean test artifacts
  help              Show this help message

OPTIONS:
  -v, --verbose     Verbose output
  -x, --failfast    Stop on first failure
  -k PATTERN        Run tests matching pattern
  --parallel        Run tests in parallel
  --no-cov          Skip coverage reporting (for faster runs)

EXAMPLES:
  $0 unit                    # Run unit tests
  $0 coverage               # Run tests with coverage
  $0 all -x                 # Run all tests, stop on first failure
  $0 unit -k "test_database" # Run unit tests matching pattern
  $0 ci                     # Run complete CI suite
  $0 clean                  # Clean up test artifacts

ENVIRONMENT VARIABLES:
  TESTING=1                 # Automatically set during tests
  LOG_LEVEL=DEBUG           # Set log level for tests
  PYTHONPATH                # Automatically configured

For more detailed control, use the Python test runner:
  python scripts/test_runner.py --help

EOF
}

# Main execution logic
main() {
    local command=${1:-help}
    shift || true
    
    case $command in
        unit)
            check_dependencies
            setup_test_env
            run_unit_tests "$@"
            ;;
        integration)
            check_dependencies
            setup_test_env
            run_integration_tests "$@"
            ;;
        all)
            check_dependencies
            setup_test_env
            run_all_tests "$@"
            ;;
        coverage)
            check_dependencies
            setup_test_env
            run_coverage_tests "$@"
            ;;
        quick)
            check_dependencies
            setup_test_env
            run_quick_tests "$@"
            ;;
        database)
            check_dependencies
            setup_test_env
            run_database_tests "$@"
            ;;
        api)
            check_dependencies
            setup_test_env
            run_api_tests "$@"
            ;;
        websocket)
            check_dependencies
            setup_test_env
            run_websocket_tests "$@"
            ;;
        filesystem)
            check_dependencies
            setup_test_env
            run_filesystem_tests "$@"
            ;;
        performance)
            check_dependencies
            setup_test_env
            run_performance_tests "$@"
            ;;
        lint)
            run_linting
            ;;
        format)
            format_code
            ;;
        security)
            run_security_tests
            ;;
        ci)
            check_dependencies
            setup_test_env
            run_ci_tests
            ;;
        clean)
            clean_artifacts
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            print_colored "$RED" "❌ Unknown command: $command"
            echo
            show_help
            exit 1
            ;;
    esac
}

# Run main function with all arguments
main "$@"