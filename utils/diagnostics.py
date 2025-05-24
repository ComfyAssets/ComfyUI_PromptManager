"""
Diagnostic utilities for troubleshooting the PromptManager gallery system.
"""

import os
import sqlite3
from pathlib import Path
from typing import Dict, Any, List


class GalleryDiagnostics:
    """Diagnostics for the gallery system."""
    
    def __init__(self, db_path: str = "prompts.db"):
        self.db_path = db_path
    
    def run_full_diagnostic(self) -> Dict[str, Any]:
        """Run a complete diagnostic check."""
        print("\n" + "="*60)
        print("🔍 PROMPTMANAGER GALLERY DIAGNOSTICS")
        print("="*60)
        
        results = {
            'database': self.check_database(),
            'images_table': self.check_images_table(),
            'file_system': self.check_file_system(),
            'comfyui_output': self.check_comfyui_output(),
            'dependencies': self.check_dependencies()
        }
        
        print("\n" + "="*60)
        print("📋 DIAGNOSTIC SUMMARY")
        print("="*60)
        
        for category, result in results.items():
            status = "✅ PASS" if result['status'] == 'ok' else "❌ FAIL"
            print(f"{category.upper():<20} {status}")
            if result['status'] != 'ok':
                print(f"  Issue: {result['message']}")
        
        print("\n" + "="*60)
        return results
    
    def check_database(self) -> Dict[str, Any]:
        """Check database connection and structure."""
        print("\n🗄️  Checking Database...")
        
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
                print(f"   📝 Prompts in database: {prompt_count}")
                
                # Check if generated_images table exists
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name='generated_images'
                """)
                
                has_images_table = cursor.fetchone() is not None
                print(f"   🖼️  Images table exists: {has_images_table}")
                
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
        """Check the generated_images table specifically."""
        print("\n🖼️  Checking Images Table...")
        
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
                print(f"   📊 Images in database: {image_count}")
                
                # Get recent images
                cursor = conn.execute("""
                    SELECT gi.*, p.text 
                    FROM generated_images gi 
                    LEFT JOIN prompts p ON gi.prompt_id = p.id 
                    ORDER BY gi.generation_time DESC 
                    LIMIT 5
                """)
                recent_images = [dict(row) for row in cursor.fetchall()]
                
                print(f"   🕒 Recent images: {len(recent_images)}")
                for img in recent_images:
                    print(f"      - {img['filename']} -> Prompt {img['prompt_id']}")
                
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
        """Check file system and permissions."""
        print("\n📁 Checking File System...")
        
        try:
            # Check current directory
            current_dir = os.getcwd()
            print(f"   📂 Current directory: {current_dir}")
            
            # Check if we can write to current directory
            test_file = "test_write.tmp"
            try:
                with open(test_file, 'w') as f:
                    f.write("test")
                os.remove(test_file)
                can_write = True
            except:
                can_write = False
            
            print(f"   ✏️  Can write to directory: {can_write}")
            
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
        """Check ComfyUI output directories."""
        print("\n🎨 Checking ComfyUI Output...")
        
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
                print(f"   📁 Found output dir: {abs_path}")
                
                # Count images in this directory
                try:
                    image_files = []
                    for ext in ['.png', '.jpg', '.jpeg', '.webp']:
                        image_files.extend(Path(abs_path).rglob(f'*{ext}'))
                    print(f"      🖼️  Images found: {len(image_files)}")
                except Exception as e:
                    print(f"      ❌ Error scanning: {e}")
        
        # Try ComfyUI's folder_paths
        try:
            import folder_paths
            comfyui_output = folder_paths.get_output_directory()
            if comfyui_output and comfyui_output not in output_dirs:
                output_dirs.append(comfyui_output)
                print(f"   📁 ComfyUI output dir: {comfyui_output}")
        except ImportError:
            print("   ⚠️  ComfyUI folder_paths not available")
        
        return {
            'status': 'ok' if output_dirs else 'warning',
            'message': 'No output directories found' if not output_dirs else None,
            'output_dirs': output_dirs
        }
    
    def check_dependencies(self) -> Dict[str, Any]:
        """Check required dependencies."""
        print("\n📦 Checking Dependencies...")
        
        dependencies = {
            'watchdog': False,
            'PIL': False,
            'sqlite3': False
        }
        
        # Check watchdog
        try:
            import watchdog
            dependencies['watchdog'] = True
            print(f"   ✅ watchdog: {watchdog.__version__}")
        except ImportError:
            print("   ❌ watchdog: NOT INSTALLED")
        
        # Check PIL
        try:
            from PIL import Image
            dependencies['PIL'] = True
            print(f"   ✅ PIL (Pillow): Available")
        except ImportError:
            print("   ❌ PIL (Pillow): NOT AVAILABLE")
        
        # Check sqlite3
        try:
            import sqlite3
            dependencies['sqlite3'] = True
            print(f"   ✅ sqlite3: {sqlite3.sqlite_version}")
        except ImportError:
            print("   ❌ sqlite3: NOT AVAILABLE")
        
        all_deps_ok = all(dependencies.values())
        
        return {
            'status': 'ok' if all_deps_ok else 'error',
            'message': 'Missing dependencies' if not all_deps_ok else None,
            'dependencies': dependencies
        }
    
    def create_test_image_link(self, prompt_id: int, test_image_path: str = None) -> Dict[str, Any]:
        """Create a test image link to verify the system works."""
        print(f"\n🧪 Creating test image link for prompt {prompt_id}...")
        
        try:
            # Import database operations
            import sys
            import os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from database.operations import PromptDatabase
            
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
            
            print(f"   ✅ Test image linked with ID: {image_id}")
            
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


def run_diagnostics():
    """Run diagnostics from command line."""
    diagnostics = GalleryDiagnostics()
    return diagnostics.run_full_diagnostic()


if __name__ == "__main__":
    run_diagnostics()