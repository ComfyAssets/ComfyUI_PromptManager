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

        # Run thumbnail migration if needed
        _run_thumbnail_migration(db)

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


def _run_thumbnail_migration(db):
    """Run the thumbnail migration to add required columns."""
    try:
        import sqlite3

        # Get the actual database path
        if hasattr(db, 'db_path'):
            db_path = db.db_path
        else:
            # Fallback to getting path from file system
            from utils.file_system import get_file_system
            fs = get_file_system()
            db_path = str(fs.get_database_path('prompts.db'))

        print(f"[PromptManager] Checking thumbnail migration at: {db_path}")

        # Check if thumbnail columns already exist
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if thumbnail columns exist in generated_images table
        cursor.execute("PRAGMA table_info(generated_images)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'thumbnail_small_path' not in columns:
            print("[PromptManager] Running thumbnail migration...")

            # Read and execute migration SQL
            migration_path = Path(__file__).parent / 'src' / 'database' / 'migrations' / '001_add_thumbnail_support.sql'
            if migration_path.exists():
                with open(migration_path, 'r') as f:
                    migration_sql = f.read()

                # Split by semicolons and execute each statement
                statements = [s.strip() for s in migration_sql.split(';') if s.strip() and not s.strip().startswith('--')]
                for stmt in statements:
                    if 'DOWN' in stmt:
                        break  # Don't run rollback statements
                    try:
                        cursor.execute(stmt)
                    except sqlite3.OperationalError as e:
                        # Ignore "already exists" errors
                        if 'already exists' not in str(e).lower():
                            print(f"[PromptManager] Migration statement warning: {e}")

                conn.commit()
                print("[PromptManager] ✅ Thumbnail migration completed")
            else:
                print("[PromptManager] ⚠️ Migration file not found")
        else:
            print("[PromptManager] Thumbnail columns already exist, skipping migration")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"[PromptManager] Thumbnail migration error: {e}")


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
