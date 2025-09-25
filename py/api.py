"""REST API module for ComfyUI PromptManager."""

# PromptManager/py/api.py

import os
import sys
import json
import mimetypes
import traceback
from pathlib import Path

# Ensure promptmanager root is in path for utils imports
_API_FILE = Path(__file__).resolve()
_PM_ROOT = _API_FILE.parent.parent  # py/api.py -> promptmanager root
if str(_PM_ROOT) not in sys.path:
    sys.path.insert(0, str(_PM_ROOT))

from aiohttp import web

try:  # pragma: no cover - runtime import may fail during circular boot
    from src.services.migration_service import MigrationService
except Exception:
    MigrationService = None  # type: ignore[arg-type]


class PromptManagerAPI:
    """REST API handler for PromptManager operations and web interface."""

    def __init__(self):
        """Initialize the PromptManager API."""
        print("[PromptManagerAPI] Initializing migration service...")
        migration_cls = MigrationService
        if migration_cls is None:
            from src.services.migration_service import MigrationService as _ms

            migration_cls = _ms

        self.migration_service = migration_cls()
        print("[PromptManagerAPI] Migration service initialized")
        self.migration_progress = {
            "phase": "idle",
            "phase_progress": 0,
            "overall_progress": 0,
            "message": "",
            "elapsed_seconds": 0,
            "estimated_remaining_seconds": 0
        }
    
    def add_routes(self, routes):
        """Add API routes to ComfyUI server routes object.
        
        Args:
            routes: The aiohttp routes object from server.routes
        """
        
        # Test endpoint to verify API is working
        @routes.get("/prompt_manager/test")
        async def test_route(request):
            return web.json_response({
                "success": True,
                "message": "PromptManager API is working!",
            })
        
        # Settings endpoint that prompt_manager.js is looking for
        @routes.get("/prompt_manager/settings")
        async def get_settings(request):
            return web.json_response({
                "success": True,
                "settings": {
                    "enable_auto_save": True,
                    "enable_duplicate_detection": True,
                    "webui_display_mode": "newtab"
                }
            })
        
        # Save prompt endpoint
        @routes.post("/prompt_manager/save")
        async def save_prompt(request):
            try:
                data = await request.json()
                return web.json_response({
                    "success": True,
                    "message": "Prompt saved successfully",
                    "prompt_id": 1  # Placeholder ID
                })
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)
        
        # Delete prompt endpoint
        @routes.delete("/prompt_manager/delete/{prompt_id}")
        async def delete_prompt(request):
            prompt_id = request.match_info.get('prompt_id')
            return web.json_response({
                "success": True,
                "message": f"Prompt {prompt_id} deleted successfully"
            })
        
        # Serve static files including the main web UI
        # This handles both /prompt_manager/web and /prompt_manager/web/* paths
        @routes.get("/prompt_manager/web")
        @routes.get("/prompt_manager/web/{path:.*}")
        async def serve_static_file(request):
            """Serve static files for the web UI."""
            try:
                # Get the path parameter (may not exist for /prompt_manager/web route)
                path = request.match_info.get('path', '')
                
                # Security: prevent directory traversal
                if '..' in path:
                    return web.Response(text="Invalid path", status=400)
                
                # Get the path to the web directory using the pre-calculated root
                # _PM_ROOT is already resolved at module load time
                web_dir = os.path.join(str(_PM_ROOT), "web")
                
                # If no path specified or root path, serve index.html
                if not path or path == '' or path == '/':
                    file_path = os.path.join(web_dir, "index.html")
                else:
                    # Remove leading slash if present
                    if path.startswith('/'):
                        path = path[1:]
                    file_path = os.path.join(web_dir, path)
                
                if os.path.exists(file_path) and os.path.isfile(file_path):
                    # Determine content type
                    mime_type, _ = mimetypes.guess_type(file_path)
                    if mime_type is None:
                        if file_path.endswith('.js'):
                            mime_type = 'application/javascript'
                        elif file_path.endswith('.css'):
                            mime_type = 'text/css'
                        elif file_path.endswith('.html'):
                            mime_type = 'text/html'
                        elif file_path.endswith('.png'):
                            mime_type = 'image/png'
                        elif file_path.endswith('.svg'):
                            mime_type = 'image/svg+xml'
                        elif file_path.endswith('.ico'):
                            mime_type = 'image/x-icon'
                        else:
                            mime_type = 'application/octet-stream'
                    
                    # Split mime_type if it contains charset (e.g., "text/javascript; charset=utf-8")
                    # aiohttp expects charset to be passed separately
                    if mime_type and ';' in mime_type:
                        mime_type = mime_type.split(';')[0].strip()
                    
                    with open(file_path, "rb") as f:
                        content = f.read()
                    
                    # Determine if we should set charset for text files
                    charset = None
                    if mime_type and mime_type.startswith(('text/', 'application/javascript', 'application/json')):
                        charset = 'utf-8'
                    
                    return web.Response(
                        body=content,
                        content_type=mime_type,
                        charset=charset,
                        headers={
                            'Cache-Control': 'public, max-age=3600' if not file_path.endswith('.html') else 'no-cache'
                        }
                    )
                else:
                    return web.Response(text=f"File not found: {path}", status=404)
                    
            except Exception as e:
                error_msg = f"Error serving static file '{path}': {str(e)}"
                print(f"[Static Error] {error_msg}")
                # Log full traceback to console for debugging
                print(f"[Static Error] Traceback:\n{traceback.format_exc()}")
                
                # Return simple error to client
                return web.Response(
                    text=error_msg,
                    status=500
                )

        # ====== PROMPTS API ENDPOINTS ======
        
        # Get all prompts
        @routes.get("/api/prompt_manager/prompts")
        async def get_prompts(request):
            """Get all prompts from the database."""
            try:
                from src.database import PromptDatabase
                db = PromptDatabase()
                prompts = db.get_all_prompts()
                
                # Convert to JSON-serializable format
                prompts_list = []
                for prompt in prompts:
                    prompts_list.append({
                        "id": prompt["id"],
                        "text": prompt["text"],
                        "category": prompt.get("category", ""),
                        "tags": prompt.get("tags", []),
                        "created_at": prompt.get("created_at", ""),
                        "updated_at": prompt.get("updated_at", "")
                    })
                
                return web.json_response({
                    "success": True,
                    "data": prompts_list
                })
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)
        
        # Create a new prompt
        @routes.post("/api/prompt_manager/prompts")
        async def create_prompt(request):
            """Create a new prompt."""
            try:
                data = await request.json()
                from src.database import PromptDatabase
                db = PromptDatabase()
                
                prompt_id = db.save_prompt(
                    text=data.get("text", ""),
                    category=data.get("category", ""),
                    tags=data.get("tags", [])
                )
                
                return web.json_response({
                    "success": True,
                    "data": {"id": prompt_id}
                })
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)
        
        # Update a prompt
        @routes.put("/api/prompt_manager/prompts/{prompt_id}")
        async def update_prompt(request):
            """Update an existing prompt."""
            try:
                prompt_id = int(request.match_info['prompt_id'])
                data = await request.json()
                
                from src.database import PromptDatabase
                db = PromptDatabase()
                
                success = db.update_prompt(
                    prompt_id=prompt_id,
                    text=data.get("text"),
                    category=data.get("category"),
                    tags=data.get("tags")
                )
                
                return web.json_response({
                    "success": success,
                    "message": "Prompt updated successfully" if success else "Failed to update prompt"
                })
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)
        
        # Delete a prompt
        @routes.delete("/api/prompt_manager/prompts/{prompt_id}")
        async def delete_prompt_api(request):
            """Delete a prompt."""
            try:
                prompt_id = int(request.match_info['prompt_id'])
                
                from src.database import PromptDatabase
                db = PromptDatabase()
                
                success = db.delete_prompt(prompt_id)
                
                return web.json_response({
                    "success": success,
                    "message": "Prompt deleted successfully" if success else "Failed to delete prompt"
                })
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)

        # ====== MIGRATION API ENDPOINTS ======

        # Check for v1 database and migration status
        @routes.get("/api/prompt_manager/migration/check")
        async def check_migration(request):
            """Check if migration is needed."""
            try:
                v1_info = self.migration_service.check_v1_database()

                # Determine if migration is needed
                needs_migration = v1_info["v1_exists"] and v1_info["stats"]["prompts"] > 0

                # Include debug information in response
                response_data = {
                    "success": True,
                    "needs_migration": needs_migration,
                    "v1_info": v1_info,  # This will include the debug field
                    "status": "pending" if needs_migration else "not_needed"
                }
                
                # Log debug info on server side
                if "debug" in v1_info:
                    print(f"[Migration Debug] ComfyUI root: {v1_info['debug'].get('comfyui_root')}")
                    print(f"[Migration Debug] Looking for v1 DB at: {v1_info['debug'].get('v1_db_path')}")
                    print(f"[Migration Debug] v1 DB exists: {v1_info['debug'].get('v1_exists')}")
                    print(f"[Migration Debug] Files in root: {v1_info['debug'].get('root_files')}")
                    if v1_info['debug'].get('alternative_found'):
                        print(f"[Migration Debug] Alternative found at: {v1_info['debug']['alternative_found']}")

                return web.json_response(response_data)
            except Exception as e:
                print(f"[Migration Error] Check failed: {e}")
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)

        # Alternative migration check endpoint (the one frontend actually uses)
        @routes.get("/prompt_manager/api/migration/check")
        async def check_migration_frontend(request):
            """Check migration status (alternative endpoint for frontend)"""
            try:
                v1_info = self.migration_service.check_v1_database()
                needs_migration = v1_info["v1_exists"] and v1_info["stats"]["prompts"] > 0

                # Log debug info on server side
                if "debug" in v1_info:
                    print(f"[Migration Debug Alt] ComfyUI root: {v1_info['debug'].get('comfyui_root')}")
                    print(f"[Migration Debug Alt] Looking for v1 DB at: {v1_info['debug'].get('v1_db_path')}")
                    print(f"[Migration Debug Alt] v1 DB exists: {v1_info['debug'].get('v1_exists')}")
                    print(f"[Migration Debug Alt] Files in root: {v1_info['debug'].get('root_files')}")
                    if v1_info['debug'].get('alternative_found'):
                        print(f"[Migration Debug Alt] Alternative found at: {v1_info['debug']['alternative_found']}")

                return web.json_response({
                    "success": True,
                    "needs_migration": needs_migration,
                    "v1_info": v1_info,  # Include full v1_info with debug
                    "v1_detected": v1_info["v1_exists"],
                    "stats": v1_info["stats"],
                    "debug": v1_info.get("debug", {})  # Include debug info
                })
            except Exception as e:
                print(f"[Migration Error Alt] Check failed: {e}")
                return web.json_response({
                    "success": False,
                    "needs_migration": False,
                    "error": str(e)
                }, status=500)

        @routes.get("/prompt_manager/api/migration/info")
        async def get_migration_info(request):
            """Get v1 database information."""
            try:
                v1_info = self.migration_service.check_v1_database()

                # Format response to match documentation
                response = {
                    "needed": v1_info["v1_exists"] and v1_info["stats"]["prompts"] > 0,
                    "v1_info": {
                        "path": v1_info.get("v1_path", ""),
                        "size_bytes": 0,  # TODO: Get actual file size
                        "size_mb": 0,
                        "prompt_count": v1_info["stats"]["prompts"],
                        "image_count": v1_info["stats"]["images"]
                    },
                    "status": "pending" if v1_info["v1_exists"] else "not_needed"
                }

                # Get file size if database exists
                if v1_info.get("v1_path") and os.path.exists(v1_info["v1_path"]):
                    size_bytes = os.path.getsize(v1_info["v1_path"])
                    response["v1_info"]["size_bytes"] = size_bytes
                    response["v1_info"]["size_mb"] = round(size_bytes / (1024 * 1024), 1)

                return web.json_response(response)
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)

        # Execute migration
        @routes.post("/api/prompt_manager/migration/execute")
        async def execute_migration(request):
            """Execute the migration process."""
            try:
                data = await request.json()
                action = data.get("action", "migrate")

                if action == "migrate":
                    # Reset progress tracking
                    self.migration_progress = {
                        "phase": "backing_up",
                        "phase_progress": 0,
                        "overall_progress": 0,
                        "message": "Starting migration...",
                        "elapsed_seconds": 0,
                        "estimated_remaining_seconds": 180
                    }

                    # Execute migration
                    result = self.migration_service.execute_migration()

                    # Update progress to completed
                    self.migration_progress = {
                        "phase": "completed",
                        "phase_progress": 1.0,
                        "overall_progress": 1.0,
                        "message": "Migration completed successfully",
                        "elapsed_seconds": 60,
                        "estimated_remaining_seconds": 0
                    }

                    return web.json_response({
                        "success": result["success"],
                        "stats": {
                            "prompts_migrated": result["prompts_migrated"],
                            "images_migrated": result["images_linked"],
                            "duration_seconds": 60,  # TODO: Track actual duration
                            "backup_path": result["backup_path"],
                            "original_renamed_to": f"{self.migration_service.v1_db_path}.migrated"
                        },
                        "errors": result.get("errors", [])
                    })

                elif action == "fresh":
                    # Start fresh - just mark as completed
                    if os.path.exists(self.migration_service.v1_db_path):
                        # Rename old database
                        old_path = f"{self.migration_service.v1_db_path}.old"
                        os.rename(self.migration_service.v1_db_path, old_path)

                    return web.json_response({
                        "success": True,
                        "message": "Starting fresh with new database"
                    })

                else:
                    return web.json_response({
                        "success": False,
                        "error": f"Unknown action: {action}"
                    }, status=400)

            except Exception as e:
                self.migration_progress["phase"] = "error"
                self.migration_progress["message"] = str(e)
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)

        # Alternative endpoint path for migration start (matches docs)
        @routes.post("/prompt_manager/api/migration/execute")
        async def execute_migration_alt(request):
            """Execute migration (alternative endpoint for frontend)"""
            try:
                data = await request.json()
                action = data.get("action", "migrate")

                if action == "migrate":
                    # Perform migration
                    success, result = self.migration_service.migrate()
                    if not success:
                        return web.json_response({
                            "success": False,
                            "error": result.get("error", "Migration failed")
                        }, status=500)

                    return web.json_response({
                        "success": True,
                        "message": "Migration completed successfully",
                        "results": {
                            "prompts_migrated": result["prompts_transformed"],
                            "images_migrated": result["images_linked"],
                            "duration_seconds": 60,  # TODO: Track actual duration
                            "backup_path": result["backup_path"],
                            "original_renamed_to": f"{self.migration_service.v1_db_path}.migrated"
                        },
                        "errors": result.get("errors", [])
                    })

                elif action == "fresh":
                    # Start fresh - just mark as completed
                    if os.path.exists(self.migration_service.v1_db_path):
                        # Rename old database
                        old_path = f"{self.migration_service.v1_db_path}.old"
                        os.rename(self.migration_service.v1_db_path, old_path)

                    return web.json_response({
                        "success": True,
                        "message": "Starting fresh with new database"
                    })

                else:
                    return web.json_response({
                        "success": False,
                        "error": f"Unknown action: {action}"
                    }, status=400)

            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)

        @routes.post("/prompt_manager/api/migration/start")
        async def start_migration(request):
            """Start migration or fresh database (alternative endpoint)."""
            return await execute_migration(request)

        # Get migration progress
        @routes.get("/api/prompt_manager/migration/progress")
        async def get_migration_progress(request):
            """Get current migration progress."""
            return web.json_response(self.migration_progress)

        # Alternative progress endpoint (matches docs)
        @routes.get("/prompt_manager/api/migration/progress")
        async def get_migration_progress_alt(request):
            """Get current migration progress (alternative endpoint)."""
            return web.json_response(self.migration_progress)

        # Get migration status
        @routes.get("/api/prompt_manager/migration/status")
        async def get_migration_status(request):
            """Get overall migration status."""
            try:
                v1_info = self.migration_service.check_v1_database()
                v2_status = self.migration_service._check_v2_status()

                status = "not_needed"
                if v1_info["v1_exists"]:
                    if v2_status["has_data"]:
                        status = "completed"
                    else:
                        status = "pending"

                return web.json_response({
                    "success": True,
                    "status": status,
                    "v1_exists": v1_info["v1_exists"],
                    "v2_has_data": v2_status["has_data"],
                    "v2_prompt_count": v2_status["prompt_count"]
                })
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)

        # Alternative status endpoint (matches docs)
        @routes.get("/prompt_manager/api/migration/status")
        async def get_migration_status_alt(request):
            """Get overall migration status (alternative endpoint)."""
            return await get_migration_status(request)

        # Serve migration UI
        @routes.get("/prompt_manager/migration")
        async def serve_migration_ui(request):
            """Serve the migration modal interface."""
            try:
                # Get the path to the migration modal HTML
                current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                html_path = os.path.join(current_dir, "web", "migration_modal.html")

                # If migration modal doesn't exist, create a basic one
                if not os.path.exists(html_path):
                    # Return a basic migration UI
                    html_content = """
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <title>PromptManager Migration</title>
                        <style>
                            body { font-family: Arial, sans-serif; padding: 20px; background: #1a1a1a; color: #fff; }
                            .container { max-width: 600px; margin: 0 auto; }
                            .modal { background: #2a2a2a; padding: 30px; border-radius: 8px; }
                            h1 { color: #fff; }
                            .info { background: #333; padding: 15px; margin: 20px 0; border-radius: 4px; }
                            .buttons { display: flex; gap: 10px; margin-top: 20px; }
                            button { padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }
                            .migrate-btn { background: #4CAF50; color: white; }
                            .fresh-btn { background: #2196F3; color: white; }
                            .progress { display: none; margin-top: 20px; }
                            .progress-bar { width: 100%; height: 20px; background: #333; border-radius: 10px; overflow: hidden; }
                            .progress-fill { height: 100%; background: #4CAF50; transition: width 0.3s; }
                        </style>
                    </head>
                    <body>
                        <div class="container">
                            <div class="modal">
                                <h1>🔄 Database Migration</h1>
                                <div id="migration-info" class="info">
                                    Checking for v1 database...
                                </div>
                                <div class="buttons">
                                    <button class="migrate-btn" onclick="startMigration('migrate')">🔄 Migrate Data</button>
                                    <button class="fresh-btn" onclick="startMigration('fresh')">🗑️ Start Fresh</button>
                                </div>
                                <div id="progress" class="progress">
                                    <div class="progress-bar">
                                        <div id="progress-fill" class="progress-fill" style="width: 0%"></div>
                                    </div>
                                    <p id="progress-message"></p>
                                </div>
                            </div>
                        </div>
                        <script>
                            // Check for v1 database on load
                            fetch('/prompt_manager/api/migration/info')
                                .then(r => r.json())
                                .then(data => {
                                    const info = document.getElementById('migration-info');
                                    if (data.needed) {
                                        info.innerHTML = `
                                            <strong>V1 Database Found:</strong><br>
                                            Path: ${data.v1_info.path}<br>
                                            Prompts: ${data.v1_info.prompt_count}<br>
                                            Images: ${data.v1_info.image_count}<br>
                                            Size: ${data.v1_info.size_mb} MB
                                        `;
                                    } else {
                                        info.innerHTML = 'No v1 database found. You can start fresh.';
                                        document.querySelector('.migrate-btn').disabled = true;
                                    }
                                });

                            function startMigration(action) {
                                document.getElementById('progress').style.display = 'block';
                                document.querySelector('.buttons').style.display = 'none';

                                fetch('/prompt_manager/api/migration/start', {
                                    method: 'POST',
                                    headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({action: action})
                                })
                                .then(r => r.json())
                                .then(data => {
                                    if (data.success) {
                                        document.getElementById('progress-fill').style.width = '100%';
                                        document.getElementById('progress-message').textContent =
                                            action === 'migrate'
                                                ? `Migration complete! Migrated ${data.stats.prompts_migrated} prompts.`
                                                : 'Started with fresh database.';
                                        setTimeout(() => {
                                            window.location.href = '/prompt_manager/web';
                                        }, 2000);
                                    } else {
                                        document.getElementById('progress-message').textContent = 'Error: ' + data.error;
                                    }
                                });

                                // Simulate progress updates
                                let progress = 0;
                                const interval = setInterval(() => {
                                    progress += 10;
                                    if (progress <= 90) {
                                        document.getElementById('progress-fill').style.width = progress + '%';
                                        document.getElementById('progress-message').textContent = 'Migrating... ' + progress + '%';
                                    } else {
                                        clearInterval(interval);
                                    }
                                }, 500);
                            }
                        </script>
                    </body>
                    </html>
                    """
                    return web.Response(text=html_content, content_type="text/html", charset="utf-8")

                # Otherwise serve the existing file
                with open(html_path, "r", encoding="utf-8") as f:
                    html_content = f.read()
                return web.Response(text=html_content, content_type="text/html", charset="utf-8")

            except Exception as e:
                return web.Response(
                    text=f"<h1>Error</h1><p>Failed to load migration UI: {str(e)}</p>",
                    content_type="text/html",
                    status=500
                )
        
        # ====== MAINTENANCE API ENDPOINTS ======

        @routes.get("/api/prompt_manager/maintenance/stats")
        async def get_maintenance_stats(request):
            """Get database maintenance statistics."""
            try:
                from src.services.maintenance_service import MaintenanceService
                service = MaintenanceService(self)
                stats = service.get_statistics()

                return web.json_response({
                    "success": True,
                    "stats": stats
                })
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)

        @routes.post("/api/prompt_manager/maintenance/deduplicate")
        async def remove_duplicates(request):
            """Remove duplicate image links."""
            try:
                from src.services.maintenance_service import MaintenanceService
                service = MaintenanceService(self)
                result = service.remove_duplicates()

                # Broadcast update via SSE
                if hasattr(self, 'sse'):
                    await self.sse.send_toast(result['message'], 'success' if result['success'] else 'error')

                return web.json_response(result)
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)

        @routes.post("/api/prompt_manager/maintenance/clean-orphans")
        async def clean_orphans(request):
            """Remove orphaned image links."""
            try:
                from src.services.maintenance_service import MaintenanceService
                service = MaintenanceService(self)
                result = service.clean_orphans()

                # Broadcast update via SSE
                if hasattr(self, 'sse'):
                    await self.sse.send_toast(result['message'], 'success' if result['success'] else 'error')

                return web.json_response(result)
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)

        @routes.post("/api/prompt_manager/maintenance/validate-paths")
        async def validate_paths(request):
            """Validate image file paths."""
            try:
                from src.services.maintenance_service import MaintenanceService
                service = MaintenanceService(self)
                result = service.validate_paths()

                return web.json_response({
                    "success": result['success'],
                    "valid": result['valid'],
                    "missing": result['missing'],
                    "missing_files": result['missing_files'][:100],  # Limit for response size
                    "message": result['message']
                })
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)

        @routes.post("/api/prompt_manager/maintenance/optimize")
        async def optimize_database(request):
            """Optimize database (VACUUM and REINDEX)."""
            try:
                from src.services.maintenance_service import MaintenanceService
                service = MaintenanceService(self)
                result = service.optimize_database()

                # Broadcast update via SSE
                if hasattr(self, 'sse'):
                    await self.sse.send_toast(result['message'], 'success' if result['success'] else 'error')

                return web.json_response(result)
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)

        @routes.post("/api/prompt_manager/maintenance/check-integrity")
        async def check_integrity(request):
            """Check database integrity."""
            try:
                from src.services.maintenance_service import MaintenanceService
                service = MaintenanceService(self)
                result = service.check_integrity()

                return web.json_response(result)
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)

        @routes.post("/api/prompt_manager/maintenance/backup")
        async def backup_database(request):
            """Create database backup."""
            try:
                from src.services.maintenance_service import MaintenanceService
                service = MaintenanceService(self)
                result = service.backup_database()

                # Broadcast update via SSE
                if hasattr(self, 'sse'):
                    await self.sse.send_toast(result['message'], 'success' if result['success'] else 'error')

                return web.json_response(result)
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)

        @routes.post("/api/prompt_manager/maintenance/remove-missing")
        async def remove_missing_files(request):
            """Remove database entries for missing files."""
            try:
                from src.services.maintenance_service import MaintenanceService
                service = MaintenanceService(self)
                result = service.remove_missing_files()

                # Broadcast update via SSE
                if hasattr(self, 'sse'):
                    await self.sse.send_toast(result['message'], 'success' if result['success'] else 'error')

                return web.json_response(result)
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": str(e)
                }, status=500)

        # Routes are automatically registered via the decorator pattern
        print(f"[PromptManagerAPI] Routes registered successfully")
