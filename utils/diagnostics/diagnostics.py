"""Diagnostic utilities for troubleshooting the PromptManager gallery system.

This module provides comprehensive diagnostic tools to help identify and resolve
issues with the PromptManager system, particularly the gallery and image monitoring
functionality. It performs systematic checks of all system components and provides
clear feedback on potential problems.

The diagnostic system checks:
- Database connectivity and schema integrity
- Image table structure and data
- File system permissions and access
- ComfyUI output directory detection
- Required Python dependencies
- System integration points

Typical usage:
    from utils.diagnostics.diagnostics.diagnostics import GalleryDiagnostics, run_diagnostics
    
    # Run full diagnostic suite
    results = run_diagnostics()
    
    # Or create custom diagnostic instance
    diagnostics = GalleryDiagnostics("custom_db.db")
    database_status = diagnostics.check_database()

The diagnostics provide:
- Clear pass/fail status for each component
- Detailed error messages and remediation suggestions
- Statistics and system information
- Test utilities for verifying functionality
"""

import os
import sqlite3
from pathlib import Path
from typing import Dict, Any, List

try:  # pragma: no cover - environment-specific import
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

# Import ComfyUIFileSystem for proper path resolution
try:
    try:
        from ..core.file_system import ComfyUIFileSystem  # type: ignore
    except ImportError:  # pragma: no cover - fallback for direct execution
        from utils.core.file_system import ComfyUIFileSystem  # type: ignore
except ImportError:
    ComfyUIFileSystem = None


def _get_diagnostics_base_dir() -> str:
    """Get the base directory for diagnostics using ComfyUI root detection."""
    if ComfyUIFileSystem is not None:
        try:
            fs_helper = ComfyUIFileSystem()
            comfyui_root = fs_helper.resolve_comfyui_root()
            return str(comfyui_root)
        except Exception:
            pass

    # Fallback to current directory if ComfyUIFileSystem fails
    return os.getcwd()


class GalleryDiagnostics:
    """Diagnostics for the gallery system.
    
    This class provides comprehensive diagnostic capabilities for the PromptManager
    gallery system. It systematically checks all components and dependencies to
    identify potential issues and provide actionable feedback.
    
    The diagnostics cover:
    - Database structure and connectivity
    - Image tracking table integrity
    - File system access and permissions
    - ComfyUI integration points
    - Python dependency availability
    
    Each diagnostic method returns a standardized result dictionary with:
    - status: 'ok', 'warning', or 'error'
    - message: Descriptive message (for warnings/errors)
    - Additional data specific to the diagnostic
    """
    
    def __init__(self, db_path: str = "prompts.db"):
        """Initialize the diagnostics system.
        
        Args:
            db_path: Path to the SQLite database file to diagnose
        """
        self.db_path = db_path
        self.logger = get_logger('prompt_manager.diagnostics')
    
    def run_full_diagnostic(self) -> Dict[str, Any]:
        """Run a complete diagnostic check.
        
        Executes all diagnostic checks in sequence and provides a comprehensive
        report of system status. Logs detailed information during the process.
        
        Returns:
            Dictionary mapping diagnostic categories to their results:
            - database: Database connectivity and structure check
            - images_table: Image tracking table specific check
            - file_system: File system access and permissions check
            - comfyui_output: ComfyUI output directory detection
            - dependencies: Python dependency availability check
        """
        self.logger.info("\n" + "="*60)
        self.logger.info("[DIAG] PROMPTMANAGER GALLERY DIAGNOSTICS")
        self.logger.info("="*60)
        
        results = {
            'database': self.check_database(),
            'images_table': self.check_images_table(),
            'file_system': self.check_file_system(),
            'comfyui_output': self.check_comfyui_output(),
            'dependencies': self.check_dependencies()
        }
        
        self.logger.info("\n" + "="*60)
        self.logger.info("[SUMMARY] DIAGNOSTIC SUMMARY")
        self.logger.info("="*60)
        
        for category, result in results.items():
            status = "[PASS] PASS" if result['status'] == 'ok' else "[FAIL] FAIL"
            self.logger.info(f"{category.upper():<20} {status}")
            if result['status'] != 'ok':
                self.logger.warning(f"  Issue: {result['message']}")
        
        self.logger.info("\n" + "="*60)
        return results
    
    def check_database(self) -> Dict[str, Any]:
        """Check database connection and structure.
        
        Verifies that the database file exists, is accessible, and contains
        the expected prompt table structure.
        
        Returns:
            Dictionary containing:
            - status: 'ok' or 'error'
            - message: Error description (if status is 'error')
            - prompt_count: Number of prompts in database (if successful)
            - has_images_table: Whether generated_images table exists (if successful)
        """
        self.logger.info("\n[DB]  Checking Database...")
        
        try:
            if not os.path.exists(self.db_path):
                return {
                    'status': 'error',
                    'message': f'Database file not found: {self.db_path}'
                }
            
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                
                # Check prompts table
                cursor = conn.execute("SELECT COUNT(*) as count FROM prompts")
                prompt_count = cursor.fetchone()['count']
                self.logger.info(f"   [NOTE] Prompts in database: {prompt_count}")
                
                # Check if generated_images table exists
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name='generated_images'
                """)
                
                has_images_table = cursor.fetchone() is not None
                self.logger.info(f"   [IMG]  Images table exists: {has_images_table}")
                
                return {
                    'status': 'ok',
                    'prompt_count': prompt_count,
                    'has_images_table': has_images_table
                }
                
        except Exception as e:
            return {
                'status': 'error',
                'message': f'Database error: {str(e)}'
            }
    
    def check_images_table(self) -> Dict[str, Any]:
        """Check the generated_images table specifically.
        
        Examines the generated_images table structure and content to verify
        the gallery system can function properly.
        
        Returns:
            Dictionary containing:
            - status: 'ok' or 'error'
            - message: Error description (if table missing or inaccessible)
            - image_count: Number of images in table (if successful)
            - recent_images: List of recent image records (if successful)
        """
        self.logger.info("\n[IMG]  Checking Images Table...")
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                
                # Check if table exists
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name='generated_images'
                """)
                
                if not cursor.fetchone():
                    return {
                        'status': 'error',
                        'message': 'generated_images table does not exist - run the updated code to create it'
                    }
                
                # Check image records
                cursor = conn.execute("SELECT COUNT(*) as count FROM generated_images")
                image_count = cursor.fetchone()['count']
                self.logger.info(f"   [STATS] Images in database: {image_count}")
                
                # Get recent images
                cursor = conn.execute("""
                    SELECT gi.*, p.text 
                    FROM generated_images gi 
                    LEFT JOIN prompts p ON gi.prompt_id = p.id 
                    ORDER BY gi.generation_time DESC 
                    LIMIT 5
                """)
                recent_images = [dict(row) for row in cursor.fetchall()]
                
                self.logger.info(f"   [TIME] Recent images: {len(recent_images)}")
                for img in recent_images:
                    self.logger.info(f"      - {img['filename']} -> Prompt {img['prompt_id']}")
                
                return {
                    'status': 'ok',
                    'image_count': image_count,
                    'recent_images': recent_images
                }
                
        except Exception as e:
            return {
                'status': 'error', 
                'message': f'Images table error: {str(e)}'
            }
    
    def check_file_system(self) -> Dict[str, Any]:
        """Check file system and permissions.
        
        Verifies that the application has appropriate file system access
        for reading images and writing database files.
        
        Returns:
            Dictionary containing:
            - status: 'ok' or 'error'
            - message: Error description (if issues detected)
            - current_dir: Current working directory path
            - can_write: Whether write access is available
        """
        self.logger.info("\n[DIR] Checking File System...")
        
        try:
            # Check current directory
            current_dir = _get_diagnostics_base_dir()
            self.logger.info(f"   [FOLDER] Current directory: {current_dir}")
            
            # Check if we can write to current directory
            test_file = "test_write.tmp"
            try:
                with open(test_file, 'w') as f:
                    f.write("test")
                os.remove(test_file)
                can_write = True
            except:
                can_write = False
            
            self.logger.info(f"   [EDIT]  Can write to directory: {can_write}")
            
            return {
                'status': 'ok',
                'current_dir': current_dir,
                'can_write': can_write
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'message': f'File system error: {str(e)}'
            }
    
    def check_comfyui_output(self) -> Dict[str, Any]:
        """Check ComfyUI output directories.
        
        Attempts to locate ComfyUI output directories where generated images
        would be stored. Checks both common locations and ComfyUI's configured paths.
        
        Returns:
            Dictionary containing:
            - status: 'ok' if directories found, 'warning' if none found
            - message: Warning message (if no directories found)
            - output_dirs: List of detected output directory paths
        """
        self.logger.info("\n[STYLE] Checking ComfyUI Output...")
        
        output_dirs = []
        
        # Try to detect ComfyUI output directories
        potential_dirs = [
            "output",
            "../output",
            "../../output", 
            "ComfyUI/output",
            "../ComfyUI/output",
            "../../ComfyUI/output"
        ]
        
        for dir_path in potential_dirs:
            abs_path = os.path.abspath(dir_path)
            if os.path.exists(abs_path):
                output_dirs.append(abs_path)
                self.logger.info(f"   [DIR] Found output dir: {abs_path}")
                
                # Count images in this directory
                try:
                    image_files = []
                    for ext in ['.png', '.jpg', '.jpeg', '.webp']:
                        image_files.extend(Path(abs_path).rglob(f'*{ext}'))
                    self.logger.info(f"      [IMG]  Images found: {len(image_files)}")
                except Exception as e:
                    self.logger.error(f"      [FAIL] Error scanning: {e}")
        
        # Try ComfyUI's folder_paths
        try:
            import folder_paths
            comfyui_output = folder_paths.get_output_directory()
            if comfyui_output and comfyui_output not in output_dirs:
                output_dirs.append(comfyui_output)
                self.logger.info(f"   [DIR] ComfyUI output dir: {comfyui_output}")
        except ImportError:
            self.logger.warning("   [WARN]  ComfyUI folder_paths not available")
        
        return {
            'status': 'ok' if output_dirs else 'warning',
            'message': 'No output directories found' if not output_dirs else None,
            'output_dirs': output_dirs
        }
    
    def check_dependencies(self) -> Dict[str, Any]:
        """Check required dependencies.
        
        Verifies that all required Python packages are available for the
        PromptManager system to function properly.
        
        Returns:
            Dictionary containing:
            - status: 'ok' if all dependencies available, 'error' if any missing
            - message: Error description (if dependencies missing)
            - dependencies: Dictionary mapping package names to availability status
        """
        self.logger.info("\n[PKG] Checking Dependencies...")
        
        dependencies = {
            'watchdog': False,
            'PIL': False,
            'sqlite3': False
        }
        
        # Check watchdog
        try:
            import watchdog
            dependencies['watchdog'] = True
            self.logger.info(f"   [PASS] watchdog: {watchdog.__version__}")
        except ImportError:
            self.logger.error("   [FAIL] watchdog: NOT INSTALLED")
        
        # Check PIL
        try:
            from PIL import Image
            dependencies['PIL'] = True
            self.logger.info(f"   [PASS] PIL (Pillow): Available")
        except ImportError:
            self.logger.error("   [FAIL] PIL (Pillow): NOT AVAILABLE")
        
        # Check sqlite3
        try:
            import sqlite3
            dependencies['sqlite3'] = True
            self.logger.info(f"   [PASS] sqlite3: {sqlite3.sqlite_version}")
        except ImportError:
            self.logger.error("   [FAIL] sqlite3: NOT AVAILABLE")
        
        all_deps_ok = all(dependencies.values())
        
        return {
            'status': 'ok' if all_deps_ok else 'error',
            'message': 'Missing dependencies' if not all_deps_ok else None,
            'dependencies': dependencies
        }
    
    def create_test_image_link(self, prompt_id: int, test_image_path: str = None) -> Dict[str, Any]:
        """Create a test image link to verify the system works.
        
        Creates a test entry in the generated_images table to verify that
        the image linking functionality is working correctly.
        
        Args:
            prompt_id: ID of an existing prompt to link the test image to
            test_image_path: Optional path for the test image (uses fake path if None)
            
        Returns:
            Dictionary containing:
            - status: 'ok' if test link created successfully, 'error' otherwise
            - message: Success or error message
            - image_id: ID of created test image record (if successful)
        """
        self.logger.info(f"\n[TEST] Creating test image link for prompt {prompt_id}...")
        
        try:
            # Import database operations
            import sys
            import os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from src.database import PromptDatabase
            
            db = PromptDatabase()
            
            # Create a fake image path if none provided
            if not test_image_path:
                test_image_path = "/fake/test/image.png"
            
            # Create test metadata
            test_metadata = {
                'file_info': {
                    'size': 1024000,
                    'dimensions': [512, 512],
                    'format': 'PNG'
                },
                'workflow': {'test': True},
                'prompt': {'test_prompt': 'This is a test'}
            }
            
            # Link the test image
            image_id = db.link_image_to_prompt(
                prompt_id=str(prompt_id),
                image_path=test_image_path,
                metadata=test_metadata
            )
            
            self.logger.info(f"   [PASS] Test image linked with ID: {image_id}")
            
            return {
                'status': 'ok',
                'image_id': image_id,
                'message': f'Test image linked successfully with ID {image_id}'
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'message': f'Failed to create test link: {str(e)}'
            }


def run_diagnostics() -> Dict[str, Any]:
    """Run diagnostics from command line.
    
    Convenience function to create a GalleryDiagnostics instance and run
    the full diagnostic suite with default settings.
    
    Returns:
        Complete diagnostic results dictionary
    """
    diagnostics = GalleryDiagnostics()
    return diagnostics.run_full_diagnostic()


if __name__ == "__main__":
    run_diagnostics()
