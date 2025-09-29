import importlib
import sys
from pathlib import Path

module = sys.modules.setdefault(__name__, sys.modules[__name__])
sys.modules.setdefault("promptmanager", module)
sys.modules.setdefault("custom_nodes.promptmanager", module)

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

# Debug: Print sys.path to understand import resolution
import os
if os.environ.get("PROMPTMANAGER_DEBUG"):
    print(f"[PromptManager] Package root: {_PACKAGE_ROOT}")
    print(f"[PromptManager] Utils exists: {(_PACKAGE_ROOT / 'utils').exists()}")
    print(f"[PromptManager] Sys.path[0:3]: {sys.path[0:3]}")

# Import the node classes - v1 and v2 widgets
from .prompt_manager_tracked import PromptManager

# Import v2 slim widgets
from .prompt_manager_v2 import PromptManagerV2
from .prompt_manager_positive import PromptManagerPositive
from .prompt_manager_negative import PromptManagerNegative

# Import tracker nodes for capturing generation data
try:
    from .custom_nodes.prompt_manager_tracker import PromptManagerTracker, PromptManagerImageTracker
    _tracker_nodes_available = True
except ImportError:
    _tracker_nodes_available = False
    print("[PromptManager] Tracker nodes not available - generation data capture disabled")

# ComfyUI node registration
NODE_CLASS_MAPPINGS = {
    "PromptManager": PromptManager,  # Original v1 widget
    "PromptManagerV2": PromptManagerV2,  # V2 combined widget
    "PromptManagerPositive": PromptManagerPositive,  # V2 positive only
    "PromptManagerNegative": PromptManagerNegative,  # V2 negative only
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptManager": "🧠 Prompt Manager",
    "PromptManagerV2": "🧠 Prompt Manager V2",
    "PromptManagerPositive": "🧠 Prompt Manager (Positive)",
    "PromptManagerNegative": "🧠 Prompt Manager (Negative)",
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
        print(f"[PromptManager] ✅ Database initialized and schema updated at: {db.db_path}")

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
            print(f"[PromptManager] ✅ Database initialized and schema updated at: {db.db_path}")

            # Run thumbnail migration if needed
            _run_thumbnail_migration(db)

            return db
        except Exception as e:
            print(f"[PromptManager] Database initialization failed: {e}")
    except Exception as e:
        print(f"[PromptManager] Database initialization failed: {e}")

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
            from utils.file_system import get_file_system
            fs = get_file_system()
            db_path = str(fs.get_database_path('prompts.db'))

        # Get migrations directory
        migrations_dir = Path(__file__).parent / 'src' / 'database' / 'migrations'
        if not migrations_dir.exists():
            print(f"[ComfyUI-PromptManager] Migrations directory not found: {migrations_dir}")
            return

        # Get all migration files sorted by version number
        migration_files = sorted(migrations_dir.glob('*.sql'))

        if not migration_files:
            print("\033[93m[ComfyUI-PromptManager] No migration files found\033[0m")
            return

        # Print header with colored output
        print()
        print(f"\033[94m[ComfyUI-PromptManager] Database initialized and schema updated at:\033[0m {db_path}")
        print(f"\033[94m[ComfyUI-PromptManager] Version:\033[0m {__version__} - Applying database patches:")

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
                print(f"🫶 \033[94mPatching:\033[0m {migration_file.name}")
                print(f"🫶 Patch already applied - skipping")
            else:
                print(f"🫶 \033[94mPatching:\033[0m {migration_file.name}")

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

                    print(f"🫶 Patch applied successfully")
                    migrations_applied += 1

                except sqlite3.Error as e:
                    # Record failed migration
                    try:
                        cursor.execute(
                            "INSERT INTO migrations (version, applied_at, filename, status, error_message) VALUES (?, ?, ?, ?, ?)",
                            (version, datetime.utcnow().isoformat(), migration_file.name, 'failed', str(e))
                        )
                        conn.commit()
                    except:
                        # If insert fails, just continue
                        pass

                    print(f"⚠️  Patch failed: {e}")
                    conn.rollback()

        cursor.close()
        conn.close()

        # Print summary
        print(f"\033[94mTotal: {total_migrations} patches loaded\033[0m")
        if migrations_applied > 0:
            print(f"\033[92m[ComfyUI-PromptManager] ✅ {migrations_applied} new patches applied\033[0m")

    except Exception as e:
        print(f"\033[93m[ComfyUI-PromptManager] ⚠️ Database patching error:\033[0m {e}")


def initialize_api(server):
    """Initialize API routes when ComfyUI starts.

    This is called by ComfyUI with the server instance.
    """
    try:
        print("\n" + "=" * 60)
        print("🚀 Initializing PromptManager v2.0.0")
        print("=" * 60)

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
            print(f"[PromptManager] Failed to setup web routes: {exc}")

        async def health_proxy(request):
            return await api.health_check(request)

        routes.get('/prompt_manager/health')(health_proxy)
        routes.get('/api/prompt_manager/health')(health_proxy)

        print("✅ PromptManager API initialized successfully")
        print(f"📍 Access web UI at: http://localhost:8188/prompt_manager/")
        print("=" * 60 + "\n")

        return api

    except Exception as e:
        print(f"❌ Failed to initialize PromptManager API: {e}")
        import traceback

        traceback.print_exc()
        return None


# Auto-initialize when imported by ComfyUI
def setup():
    """Setup function called when node is loaded."""
    print(f"[PromptManager] Node loaded - v{__version__}")

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

    if PromptServer.instance and not _API_INITIALIZED:
        initialize_api(PromptServer.instance)
        _API_INITIALIZED = True
except ImportError:
    # Running outside ComfyUI context
    pass
except Exception as e:
    print(f"[PromptManager] API initialization deferred: {e}")

# Print startup message with loaded tools (only once)
if _SETUP_DONE:
    print()
    print(f"\033[94m[ComfyUI-PromptManager] Version:\033[0m {get_version()}")
    for node_key, display_name in NODE_DISPLAY_NAME_MAPPINGS.items():
        print(f"🫶 \033[94mLoaded:\033[0m {display_name}")
    print(f"\033[94mTotal: {len(NODE_CLASS_MAPPINGS)} tools loaded\033[0m")
    print()
