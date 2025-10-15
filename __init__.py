import importlib
import sys
from pathlib import Path

module = sys.modules.setdefault(__name__, sys.modules[__name__])
sys.modules.setdefault("promptmanager", module)
sys.modules.setdefault("custom_nodes.promptmanager", module)

# Safe print wrapper for Windows desktop app where stdout redirect can fail
def safe_print(message):
    """Print that won't crash if stdout is redirected improperly."""
    try:
        print(message)
        sys.stdout.flush()
    except (OSError, IOError):
        # Stdout redirect failed (common in Windows desktop ComfyUI)
        # Silently continue - the node will still load
        pass

def conditional_print(message):
    """Print only if logging is enabled in settings."""
    # Check if logging is enabled
    try:
        from src.core.logging_control import is_logging_enabled
        if is_logging_enabled():
            safe_print(message)
    except:
        # If we can't check settings, print anyway (fail-safe)
        safe_print(message)

# Add package directories to Python path for clean imports
_PACKAGE_ROOT = Path(__file__).parent.resolve()

# Add the package root FIRST so utils can be found
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

# Then add src directory
src_dir = _PACKAGE_ROOT / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

# Preload logging module so legacy imports resolve cleanly
try:
    importlib.import_module("promptmanager.loggers")
except Exception:
    # Defer to module-level fallbacks if the logging module cannot initialize
    pass

# Initialize logging control EARLY - before any other imports that might log
# This MUST happen before any logging module loads
try:
    import os
    from pathlib import Path as _TempPath

    # Check settings database directly to determine if logging should be enabled
    # This avoids circular import issues and ensures env var is set ASAP
    try:
        # Try to check the database directly - check multiple possible locations
        _db_paths = []
        for candidate in _TempPath.cwd().parents:
            if (candidate / "user" / "default" / "PromptManager").exists():
                _db_paths.append(candidate / "user" / "default" / "PromptManager" / "prompts.db")
                _db_paths.append(candidate / "user" / "default" / "PromptManager" / "data" / "prompts.db")
                break

        for _db_path in _db_paths:
            if _db_path and _db_path.exists():
                import sqlite3
                import json

                _conn = sqlite3.connect(str(_db_path))
                _cursor = _conn.cursor()

                # Check app_settings table (current schema)
                try:
                    _cursor.execute("SELECT value FROM app_settings WHERE key = 'enable_logging'")
                    _row = _cursor.fetchone()

                    if _row is not None:
                        _enable_logging = json.loads(_row[0]) if isinstance(_row[0], str) else _row[0]
                        os.environ['PROMPTMANAGER_LOGGING_DISABLED'] = '0' if _enable_logging else '1'
                        _conn.close()
                        break
                except Exception:
                    pass

                # Fallback to old settings table
                try:
                    _cursor.execute("SELECT value FROM settings WHERE key = 'enable_logging'")
                    _row = _cursor.fetchone()

                    if _row is not None:
                        _enable_logging = json.loads(_row[0]) if isinstance(_row[0], str) else _row[0]
                        os.environ['PROMPTMANAGER_LOGGING_DISABLED'] = '0' if _enable_logging else '1'
                        _conn.close()
                        break
                except Exception:
                    pass

                _conn.close()
                # Default to enabled if no setting found
                os.environ['PROMPTMANAGER_LOGGING_DISABLED'] = '0'
                break
        else:
            # Database doesn't exist yet, default to enabled
            os.environ['PROMPTMANAGER_LOGGING_DISABLED'] = '0'
    except Exception:
        # If anything fails, default to enabled
        os.environ['PROMPTMANAGER_LOGGING_DISABLED'] = '0'

    # Now initialize the LoggingControl class
    from src.core.logging_control import LoggingControl
    LoggingControl()
except Exception:
    # If logging control fails, continue anyway
    pass

# Debug: Print sys.path to understand import resolution
import os
if os.environ.get("PROMPTMANAGER_DEBUG"):
    safe_print(f"[PromptManager] Package root: {_PACKAGE_ROOT}")
    safe_print(f"[PromptManager] Utils exists: {(_PACKAGE_ROOT / 'utils').exists()}")
    safe_print(f"[PromptManager] Sys.path[0:3]: {sys.path[0:3]}")

# Import the node classes - v1 and v2 widgets
from .prompt_manager_tracked import PromptManager

# Import v2 slim widgets
from .prompt_manager_v2 import PromptManagerV2
from .prompt_manager_positive import PromptManagerPositive
from .prompt_manager_negative import PromptManagerNegative

# Import text-only versions (no CLIP encoding)
from .prompt_manager_text import PromptManagerText
from .prompt_manager_v2_text import PromptManagerV2Text
from .prompt_manager_positive_text import PromptManagerPositiveText
from .prompt_manager_negative_text import PromptManagerNegativeText

# Import tracker nodes for capturing generation data
try:
    from .custom_nodes.prompt_manager_tracker import PromptManagerTracker, PromptManagerImageTracker
    _tracker_nodes_available = True
except ImportError:
    _tracker_nodes_available = False
    # Skip print on Windows desktop app - stdout redirect causes errors
    pass

# ComfyUI node registration
NODE_CLASS_MAPPINGS = {
    "PromptManager": PromptManager,  # Original v1 widget
    "PromptManagerV2": PromptManagerV2,  # V2 combined widget
    "PromptManagerPositive": PromptManagerPositive,  # V2 positive only
    "PromptManagerNegative": PromptManagerNegative,  # V2 negative only
    # Text-only versions (no CLIP encoding)
    "PromptManagerText": PromptManagerText,  # V1 text-only
    "PromptManagerV2Text": PromptManagerV2Text,  # V2 text-only
    "PromptManagerPositiveText": PromptManagerPositiveText,  # Positive text-only
    "PromptManagerNegativeText": PromptManagerNegativeText,  # Negative text-only
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptManager": "🧠 Prompt Manager",
    "PromptManagerV2": "🧠 Prompt Manager V2",
    "PromptManagerPositive": "🧠 Prompt Manager (Positive)",
    "PromptManagerNegative": "🧠 Prompt Manager (Negative)",
    # Text-only versions
    "PromptManagerText": "🧠 Prompt Manager (text)",
    "PromptManagerV2Text": "🧠 Prompt Manager V2 (text)",
    "PromptManagerPositiveText": "🧠 Prompt Manager (Positive/text)",
    "PromptManagerNegativeText": "🧠 Prompt Manager (Negative/text)",
}

# Add tracker nodes if available
if _tracker_nodes_available:
    NODE_CLASS_MAPPINGS["PromptManagerTracker"] = PromptManagerTracker
    NODE_CLASS_MAPPINGS["PromptManagerImageTracker"] = PromptManagerImageTracker
    NODE_DISPLAY_NAME_MAPPINGS["PromptManagerTracker"] = "📝 PM Tracker"
    NODE_DISPLAY_NAME_MAPPINGS["PromptManagerImageTracker"] = "🖼️ PM Image Tracker"

# Web directory for ComfyUI to serve static files
WEB_DIRECTORY = "./web"

# Optional: Export version for tracking
__version__ = "2.0.0"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

def get_version():
    """Return the current version."""
    return __version__


def ensure_database_initialized():
    """Ensure database is initialized with proper schema when node loads."""
    try:
        # Import database to trigger initialization and schema migration
        from .src.database import PromptDatabase

        # Create a database instance to ensure schema is up to date
        # This will automatically run any schema migrations
        db = PromptDatabase()
        conditional_print(f"[PromptManager] ✅ Database initialized and schema updated at: {db.db_path}")

        # Run all SQL migrations
        _run_sql_migrations(db)

        return db

    except ImportError:
        # Try alternative import path
        try:
            import sys
            import os
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from src.database import PromptDatabase

            db = PromptDatabase()
            conditional_print(f"[PromptManager] ✅ Database initialized and schema updated at: {db.db_path}")

            # Run thumbnail migration if needed
            _run_thumbnail_migration(db)

            return db
        except Exception as e:
            conditional_print(f"[PromptManager] Database initialization failed: {e}")
    except Exception as e:
        conditional_print(f"[PromptManager] Database initialization failed: {e}")

    return None


def _run_sql_migrations(db):
    """Run all SQL migrations in order."""
    try:
        import sqlite3
        import re
        from datetime import datetime

        # Get the actual database path
        if hasattr(db, 'db_path'):
            db_path = db.db_path
        else:
            # Fallback to getting path from file system
            from utils.core.file_system import get_file_system
            fs = get_file_system()
            db_path = str(fs.get_database_path('prompts.db'))

        # Get migrations directory
        migrations_dir = Path(__file__).parent / 'src' / 'database' / 'migrations'
        if not migrations_dir.exists():
            conditional_print(f"[ComfyUI-PromptManager] Migrations directory not found: {migrations_dir}")
            return

        # Get all migration files sorted by version number
        migration_files = sorted(migrations_dir.glob('*.sql'))

        if not migration_files:
            conditional_print("\033[93m[ComfyUI-PromptManager] No migration files found\033[0m")
            return

        # Print header with colored output (only if logging enabled)
        conditional_print("")
        conditional_print(f"\033[94m[ComfyUI-PromptManager] Database initialized and schema updated at:\033[0m {db_path}")
        conditional_print(f"\033[94m[ComfyUI-PromptManager] Version:\033[0m {__version__} - Applying database patches:")

        # Connect to database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # First check if migrations table exists and has status column
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='migrations'")
        table_exists = cursor.fetchone() is not None

        if table_exists:
            # Check if status column exists
            cursor.execute("PRAGMA table_info(migrations)")
            columns = [col[1] for col in cursor.fetchall()]

            if 'status' not in columns:
                # Upgrade the migrations table
                cursor.execute("ALTER TABLE migrations ADD COLUMN status TEXT DEFAULT 'success'")
                cursor.execute("ALTER TABLE migrations ADD COLUMN error_message TEXT")
                conn.commit()
        else:
            # Create migrations table with all columns
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    filename TEXT,
                    status TEXT DEFAULT 'success',
                    error_message TEXT
                )
            """)
            conn.commit()

        # Get list of already attempted migrations
        cursor.execute("SELECT version FROM migrations")
        applied_migrations = set(row[0] for row in cursor.fetchall())

        # Process each migration file
        migrations_applied = 0
        total_migrations = len(migration_files)

        for migration_file in migration_files:
            # Extract version number from filename (e.g., 006_add_word_cloud_cache.sql -> 6)
            version_match = re.match(r'^(\d+)', migration_file.name)
            if not version_match:
                continue

            version = int(version_match.group(1))

            # Check if already applied
            if version in applied_migrations:
                conditional_print(f"🫶 \033[94mPatching:\033[0m {migration_file.name}")
                conditional_print(f"🫶 Patch already applied - skipping")
            else:
                conditional_print(f"🫶 \033[94mPatching:\033[0m {migration_file.name}")

                try:
                    # Read migration file
                    with open(migration_file, 'r') as f:
                        migration_sql = f.read()

                    # Execute the entire migration as a script
                    cursor.executescript(migration_sql)

                    # Record that this migration has been applied
                    cursor.execute(
                        "INSERT INTO migrations (version, applied_at, filename, status) VALUES (?, ?, ?, ?)",
                        (version, datetime.utcnow().isoformat(), migration_file.name, 'success')
                    )
                    conn.commit()

                    conditional_print(f"🫶 Patch applied successfully")
                    migrations_applied += 1

                except sqlite3.Error as e:
                    error_message = str(e).lower()

                    # Check if it's a "duplicate column" error, which is safe to ignore
                    if 'duplicate column' in error_message or 'already exists' in error_message:
                        # Column already exists, treat as success
                        try:
                            cursor.execute(
                                "INSERT INTO migrations (version, applied_at, filename, status) VALUES (?, ?, ?, ?)",
                                (version, datetime.utcnow().isoformat(), migration_file.name, 'success')
                            )
                            conn.commit()
                            conditional_print(f"🫶 Patch already applied (columns exist) - skipping")
                            migrations_applied += 1
                        except:
                            pass
                    else:
                        # Record failed migration for other errors
                        try:
                            cursor.execute(
                                "INSERT INTO migrations (version, applied_at, filename, status, error_message) VALUES (?, ?, ?, ?, ?)",
                                (version, datetime.utcnow().isoformat(), migration_file.name, 'failed', str(e))
                            )
                            conn.commit()
                        except:
                            # If insert fails, just continue
                            pass

                        conditional_print(f"⚠️  Patch failed: {e}")
                        conn.rollback()

        cursor.close()
        conn.close()

        # Print summary (only if logging enabled)
        conditional_print(f"\033[94mTotal: {total_migrations} patches loaded\033[0m")
        if migrations_applied > 0:
            conditional_print(f"\033[92m[ComfyUI-PromptManager] ✅ {migrations_applied} new patches applied\033[0m")

    except Exception as e:
        conditional_print(f"\033[93m[ComfyUI-PromptManager] ⚠️ Database patching error:\033[0m {e}")


def initialize_api(server):
    """Initialize API routes when ComfyUI starts.

    This is called by ComfyUI with the server instance.
    """
    try:
        conditional_print("\n" + "=" * 60)
        conditional_print("🚀 Initializing PromptManager v2.0.0")
        conditional_print("=" * 60)

        module_name = "src.api.routes"
        api_module = sys.modules.get(module_name)
        if api_module is None:
            api_module = importlib.import_module(module_name)

        if not hasattr(api_module, "PromptManagerAPI"):
            sys.modules.pop(module_name, None)
            api_module = importlib.import_module(module_name)

        PromptManagerAPI = getattr(api_module, "PromptManagerAPI")

        api = PromptManagerAPI()
        routes = server.routes

        api.add_routes(routes)

        try:
            from . import web_routes

            web_routes.setup_routes(routes)
        except Exception as exc:  # pragma: no cover - defensive
            conditional_print(f"[PromptManager] Failed to setup web routes: {exc}")

        async def health_proxy(request):
            return await api.health_check(request)

        routes.get('/prompt_manager/health')(health_proxy)
        routes.get('/api/prompt_manager/health')(health_proxy)

        # Get actual server URL from ComfyUI (respects --listen and --port)
        from utils.comfyui_utils import get_comfyui_server_url
        server_url = get_comfyui_server_url()

        conditional_print("✅ PromptManager API initialized successfully")
        conditional_print(f"📍 Access web UI at: {server_url}/prompt_manager/")
        conditional_print("=" * 60 + "\n")

        return api

    except Exception as e:
        conditional_print(f"❌ Failed to initialize PromptManager API: {e}")
        import traceback

        traceback.print_exc()
        return None


# Auto-initialize when imported by ComfyUI
def setup():
    """Setup function called when node is loaded."""
    conditional_print(f"[PromptManager] Node loaded - v{__version__}")

    # Ensure required directories exist
    base_dir = Path(__file__).parent
    required_dirs = [base_dir / "web", base_dir / "data", base_dir / "logs"]

    for dir_path in required_dirs:
        dir_path.mkdir(exist_ok=True)

    # Ensure database is initialized with proper schema
    ensure_database_initialized()


# Track if we've already initialized to prevent double loading
_SETUP_DONE = False
_API_INITIALIZED = False

# Call setup on import
if not _SETUP_DONE:
    setup()
    _SETUP_DONE = True

# Initialize API if server is available (ComfyUI will call this)
try:
    from server import PromptServer
    from aiohttp import web

    conditional_print(f"[PromptManager] 🔍 Checking PromptServer availability...")
    conditional_print(f"[PromptManager]    - PromptServer.instance exists: {PromptServer.instance is not None}")
    conditional_print(f"[PromptManager]    - _API_INITIALIZED: {_API_INITIALIZED}")

    if PromptServer.instance and not _API_INITIALIZED:
        try:
            conditional_print(f"[PromptManager] 🚀 Starting API route registration...")

            # Import API module
            from src.api.routes import PromptManagerAPI
            from utils.core.file_system import get_file_system

            # Get database path
            fs = get_file_system()
            db_path = str(fs.get_database_path("prompts.db"))
            conditional_print(f"[PromptManager]    - Database path: {db_path}")

            # Create API instance
            prompt_api = PromptManagerAPI(db_path)
            conditional_print(f"[PromptManager]    - PromptManagerAPI instance created")

            # Create route table and register API routes
            routes = web.RouteTableDef()
            prompt_api.add_routes(routes)

            # Register web UI routes
            try:
                from . import web_routes
                web_routes.setup_routes(routes)
                conditional_print(f"[PromptManager]    - Web UI routes registered")
            except Exception as exc:
                conditional_print(f"[PromptManager]    - Failed to setup web routes: {exc}")
            conditional_print(f"[PromptManager]    - Routes populated: {len(routes)} routes")

            # Register with ComfyUI server (routes go on the app, not routes object)
            PromptServer.instance.app.add_routes(routes)
            conditional_print(f"[PromptManager]    - Routes added to PromptServer.app")

            # Debug: Check if our route is actually registered
            for route in PromptServer.instance.app.router.routes():
                if '/api/prompt_manager/settings' in str(route.resource):
                    conditional_print(f"[PromptManager]    - Found settings route: {route.method} {route.resource}")

            # Debug: Try to access the route directly
            conditional_print(f"[PromptManager]    - Total app routes: {len(list(PromptServer.instance.app.router.routes()))}")

            # Get actual server URL from ComfyUI (respects --listen and --port)
            from utils.comfyui_utils import get_comfyui_server_url
            server_url = get_comfyui_server_url()

            _API_INITIALIZED = True
            conditional_print(f"[PromptManager] ✅ API routes registered successfully!")
            conditional_print(f"[PromptManager]    - Test endpoint: {server_url}/api/prompt_manager/settings")

        except Exception as e:
            conditional_print(f"[PromptManager] ❌ Failed to initialize API")
            conditional_print(f"[PromptManager]    - Error: {e}")
            import traceback
            conditional_print(f"[PromptManager]    - Traceback:")
            traceback.print_exc()
    else:
        if not PromptServer.instance:
            conditional_print("[PromptManager] ⏳ PromptServer.instance not available yet")
        if _API_INITIALIZED:
            conditional_print("[PromptManager] ⏭️  API already initialized, skipping")

except ImportError as e:
    # Running outside ComfyUI context
    conditional_print(f"[PromptManager] ⚠️  Server module not available (running outside ComfyUI)")
    conditional_print(f"[PromptManager]    - Import error: {e}")
    pass
except Exception as e:
    conditional_print(f"[PromptManager] ⚠️  API initialization deferred due to error")
    conditional_print(f"[PromptManager]    - Error: {e}")

# Print startup message with loaded tools (only once)
if _SETUP_DONE:
    safe_print("")
    safe_print(f"\033[94m[ComfyUI-PromptManager] Version:\033[0m {get_version()}")
    for node_key, display_name in NODE_DISPLAY_NAME_MAPPINGS.items():
        safe_print(f"🫶 \033[94mLoaded:\033[0m {display_name}")
    safe_print(f"\033[94mTotal: {len(NODE_CLASS_MAPPINGS)} tools loaded\033[0m")
    safe_print("")
