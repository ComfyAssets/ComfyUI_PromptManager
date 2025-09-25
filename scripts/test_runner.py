#!/usr/bin/env python3
"""
Comprehensive test runner script for ComfyUI PromptManager.

This script provides various testing commands with proper environment setup,
coverage reporting, and result analysis.

Usage:
    python scripts/test_runner.py [command] [options]

Commands:
    unit        - Run unit tests only
    integration - Run integration tests only
    all         - Run all tests
    coverage    - Run tests with coverage reporting
    quick       - Run quick smoke tests
    database    - Run database-specific tests
    api         - Run API endpoint tests
    websocket   - Run WebSocket communication tests
    filesystem  - Run file system operation tests
    performance - Run performance benchmarks
    clean       - Clean up test artifacts

Examples:
    python scripts/test_runner.py unit
    python scripts/test_runner.py coverage --html
    python scripts/test_runner.py all --parallel
    python scripts/test_runner.py api --verbose
"""

import sys
import os
import argparse
import subprocess
import shutil
import time
from pathlib import Path
from typing import List, Optional, Dict, Any
import json

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "src"))


class TestRunner:
    """Main test runner class."""
    
    def __init__(self):
        self.project_root = Path(__file__).parent.parent
        self.test_dir = self.project_root / "tests"
        self.coverage_dir = self.project_root / "htmlcov"
        self.reports_dir = self.project_root / "test_reports"
        
        # Ensure reports directory exists
        self.reports_dir.mkdir(exist_ok=True)
        
        # Test commands mapping
        self.test_commands = {
            'unit': self._run_unit_tests,
            'integration': self._run_integration_tests,
            'all': self._run_all_tests,
            'coverage': self._run_coverage_tests,
            'quick': self._run_quick_tests,
            'database': self._run_database_tests,
            'api': self._run_api_tests,
            'websocket': self._run_websocket_tests,
            'filesystem': self._run_filesystem_tests,
            'performance': self._run_performance_tests,
            'clean': self._clean_artifacts
        }
    
    def run(self, command: str, **kwargs) -> int:
        """Run the specified test command."""
        if command not in self.test_commands:
            print(f"Error: Unknown command '{command}'")
            print(f"Available commands: {', '.join(self.test_commands.keys())}")
            return 1
        
        print(f"\n🧪 Running {command} tests...")
        print(f"📁 Project root: {self.project_root}")
        print(f"📂 Test directory: {self.test_dir}")
        print("-" * 60)
        
        try:
            return self.test_commands[command](**kwargs)
        except KeyboardInterrupt:
            print("\n❌ Tests interrupted by user")
            return 130
        except Exception as e:
            print(f"\n❌ Error running tests: {e}")
            return 1
    
    def _build_pytest_command(self, test_paths: List[str], **kwargs) -> List[str]:
        """Build pytest command with appropriate options."""
        cmd = ["python", "-m", "pytest"]
        
        # Add test paths
        cmd.extend(test_paths)
        
        # Add common options
        if kwargs.get('verbose', False):
            cmd.append('-v')
        else:
            cmd.append('--tb=short')
        
        if kwargs.get('parallel', False):
            cmd.extend(['-n', 'auto'])
        
        if kwargs.get('failfast', False):
            cmd.append('-x')
        
        if kwargs.get('quiet', False):
            cmd.append('-q')
        
        # Add coverage options if requested
        if kwargs.get('coverage', False):
            cmd.extend([
                '--cov=src',
                '--cov-report=term-missing'
            ])
            
            if kwargs.get('html', False):
                cmd.append('--cov-report=html')
            
            if kwargs.get('xml', False):
                cmd.append('--cov-report=xml')
        
        # Add markers
        markers = kwargs.get('markers', [])
        for marker in markers:
            cmd.extend(['-m', marker])
        
        # Add specific test patterns
        test_pattern = kwargs.get('pattern')
        if test_pattern:
            cmd.extend(['-k', test_pattern])
        
        # Add timeout
        if kwargs.get('timeout'):
            cmd.extend(['--timeout', str(kwargs['timeout'])])
        
        return cmd
    
    def _run_command(self, cmd: List[str]) -> int:
        """Run command and return exit code."""
        print(f"🔧 Running: {' '.join(cmd)}")
        start_time = time.time()
        
        try:
            result = subprocess.run(cmd, cwd=self.project_root)
            end_time = time.time()
            
            duration = end_time - start_time
            if result.returncode == 0:
                print(f"✅ Tests completed successfully in {duration:.2f}s")
            else:
                print(f"❌ Tests failed after {duration:.2f}s")
            
            return result.returncode
        except FileNotFoundError:
            print("❌ pytest not found. Please install test dependencies:")
            print("   pip install -e .[test]")
            return 1
    
    def _run_unit_tests(self, **kwargs) -> int:
        """Run unit tests only."""
        cmd = self._build_pytest_command(
            [str(self.test_dir / "unit")],
            markers=['unit'],
            **kwargs
        )
        return self._run_command(cmd)
    
    def _run_integration_tests(self, **kwargs) -> int:
        """Run integration tests only.""" 
        cmd = self._build_pytest_command(
            [str(self.test_dir / "integration")],
            markers=['integration'],
            **kwargs
        )
        return self._run_command(cmd)
    
    def _run_all_tests(self, **kwargs) -> int:
        """Run all tests."""
        cmd = self._build_pytest_command([str(self.test_dir)], **kwargs)
        return self._run_command(cmd)
    
    def _run_coverage_tests(self, **kwargs) -> int:
        """Run tests with comprehensive coverage reporting."""
        kwargs.update({
            'coverage': True,
            'html': True,
            'xml': True
        })
        
        cmd = self._build_pytest_command([str(self.test_dir)], **kwargs)
        result = self._run_command(cmd)
        
        if result == 0 and kwargs.get('html', True):
            coverage_report = self.coverage_dir / "index.html"
            if coverage_report.exists():
                print(f"📊 Coverage report: {coverage_report}")
        
        return result
    
    def _run_quick_tests(self, **kwargs) -> int:
        """Run quick smoke tests."""
        kwargs.update({
            'markers': ['unit', 'not slow'],
            'failfast': True
        })
        
        cmd = self._build_pytest_command(
            [str(self.test_dir / "unit")],
            **kwargs
        )
        return self._run_command(cmd)
    
    def _run_database_tests(self, **kwargs) -> int:
        """Run database-specific tests."""
        cmd = self._build_pytest_command(
            [str(self.test_dir)],
            markers=['database'],
            **kwargs
        )
        return self._run_command(cmd)
    
    def _run_api_tests(self, **kwargs) -> int:
        """Run API endpoint tests."""
        cmd = self._build_pytest_command(
            [str(self.test_dir / "integration" / "test_api_endpoints.py")],
            markers=['api'],
            **kwargs
        )
        return self._run_command(cmd)
    
    def _run_websocket_tests(self, **kwargs) -> int:
        """Run WebSocket communication tests."""
        cmd = self._build_pytest_command(
            [str(self.test_dir / "integration" / "test_websocket.py")],
            markers=['websocket'],
            **kwargs
        )
        return self._run_command(cmd)
    
    def _run_filesystem_tests(self, **kwargs) -> int:
        """Run file system operation tests."""
        cmd = self._build_pytest_command(
            [str(self.test_dir / "integration" / "test_filesystem_operations.py")],
            markers=['filesystem'],
            **kwargs
        )
        return self._run_command(cmd)
    
    def _run_performance_tests(self, **kwargs) -> int:
        """Run performance benchmark tests."""
        perf_dir = self.test_dir / "performance"
        if not perf_dir.exists():
            print("⚠️  Performance tests not found, skipping...")
            return 0
        
        cmd = self._build_pytest_command(
            [str(perf_dir)],
            markers=['performance'],
            **kwargs
        )
        return self._run_command(cmd)
    
    def _clean_artifacts(self, **kwargs) -> int:
        """Clean up test artifacts and generated files."""
        artifacts_to_clean = [
            self.coverage_dir,
            self.project_root / ".coverage",
            self.project_root / "coverage.xml",
            self.project_root / "coverage.json", 
            self.project_root / ".pytest_cache",
            self.project_root / "test_reports",
            self.project_root / ".tox",
        ]
        
        # Clean Python cache files
        cache_patterns = [
            "**/__pycache__",
            "**/*.pyc",
            "**/*.pyo",
            "**/*.pyd"
        ]
        
        cleaned_count = 0
        
        for artifact in artifacts_to_clean:
            if artifact.exists():
                if artifact.is_dir():
                    shutil.rmtree(artifact)
                    print(f"🗑️  Removed directory: {artifact}")
                else:
                    artifact.unlink()
                    print(f"🗑️  Removed file: {artifact}")
                cleaned_count += 1
        
        # Clean cache files
        for pattern in cache_patterns:
            for cache_file in self.project_root.glob(pattern):
                if cache_file.is_dir():
                    shutil.rmtree(cache_file)
                else:
                    cache_file.unlink()
                cleaned_count += 1
        
        print(f"✅ Cleaned {cleaned_count} artifacts")
        return 0
    
    def generate_test_report(self, test_results: Dict[str, Any]) -> None:
        """Generate comprehensive test report."""
        report_file = self.reports_dir / f"test_report_{int(time.time())}.json"
        
        with open(report_file, 'w') as f:
            json.dump(test_results, f, indent=2)
        
        print(f"📋 Test report saved: {report_file}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Comprehensive test runner for ComfyUI PromptManager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        'command',
        choices=['unit', 'integration', 'all', 'coverage', 'quick', 
                'database', 'api', 'websocket', 'filesystem', 'performance', 'clean'],
        help='Test command to run'
    )
    
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Verbose output'
    )
    
    parser.add_argument(
        '-q', '--quiet',
        action='store_true', 
        help='Quiet output'
    )
    
    parser.add_argument(
        '-x', '--failfast',
        action='store_true',
        help='Stop on first failure'
    )
    
    parser.add_argument(
        '-p', '--parallel',
        action='store_true',
        help='Run tests in parallel'
    )
    
    parser.add_argument(
        '--html',
        action='store_true',
        help='Generate HTML coverage report'
    )
    
    parser.add_argument(
        '--xml',
        action='store_true',
        help='Generate XML coverage report'
    )
    
    parser.add_argument(
        '-k', '--pattern',
        type=str,
        help='Run tests matching pattern'
    )
    
    parser.add_argument(
        '--timeout',
        type=int,
        default=300,
        help='Test timeout in seconds'
    )
    
    parser.add_argument(
        '--report',
        action='store_true',
        help='Generate test report'
    )
    
    args = parser.parse_args()
    
    # Convert args to kwargs
    kwargs = {
        'verbose': args.verbose,
        'quiet': args.quiet,
        'failfast': args.failfast,
        'parallel': args.parallel,
        'html': args.html,
        'xml': args.xml,
        'pattern': args.pattern,
        'timeout': args.timeout,
        'coverage': args.command == 'coverage' or args.html or args.xml
    }
    
    # Run tests
    runner = TestRunner()
    exit_code = runner.run(args.command, **kwargs)
    
    # Generate report if requested
    if args.report and exit_code == 0:
        runner.generate_test_report({
            'command': args.command,
            'timestamp': time.time(),
            'exit_code': exit_code,
            'options': kwargs
        })
    
    sys.exit(exit_code)


if __name__ == '__main__':
    main()