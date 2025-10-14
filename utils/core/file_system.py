"""
File system utilities for ComfyUI PromptManager.

Provides cross-platform file management using ComfyUI's official user directory structure.
Ensures all data is stored in the proper location: ComfyUI/user/default/PromptManager/

This fixes the v1 issue where files were stored in ComfyUI root directory.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

# Import logging with proper fallback
try:
    from .logging_config import get_logger  # type: ignore
except ImportError:
    import logging
    _fallback_loggers_initialized = set()

    def get_logger(name: str):
        logger = logging.getLogger(name)
        # Only initialize handler once per logger name to avoid duplicates
        if name not in _fallback_loggers_initialized and not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
                )
            )
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            _fallback_loggers_initialized.add(name)
        return logger


# -------------------------
# Small path utility helpers
# -------------------------


def _is_relative_to(path: Path, other: Path) -> bool:
    """Compat: pathlib.is_relative_to for older Python versions."""
    try:
        path.relative_to(other)
        return True
    except Exception:
        return False


def _writable_dir(p: Path) -> bool:
    """Probe writability by attempting to create and remove a temp file."""
    try:
        p.mkdir(parents=True, exist_ok=True)
        test = p / ".pm_write_test"
        with test.open("w", encoding="utf-8") as f:
            f.write("ok")
        test.unlink(missing_ok=True)  # type: ignore[arg-type]
        return True
    except Exception:
        return False


def _parents_including_self(p: Path) -> List[Path]:
    return [p] + list(p.parents)


def _path_no_symlink(p: Path) -> Path:
    """Return fully-resolved real path (collapses symlinks)."""
    try:
        return p.resolve(strict=False)
    except Exception:
        # On weird FS, fallback to realpath
        return Path(os.path.realpath(str(p)))


class ComfyUIFileSystem:
    """
    Manages file system operations for PromptManager using ComfyUI's official directory structure.

    Ensures all files are stored in: ComfyUI/user/default/PromptManager/
    Supports Windows, macOS, and Linux file systems.
    """

    NODE_NAME = "PromptManager"

    # Detection markers for ComfyUI root
    _ROOT_MARKER_SETS: List[Tuple[str, ...]] = [
        ("web", "comfy"),  # Standard ComfyUI structure
        ("web", "custom_nodes"),  # Alternative structure
        ("server.py", "main.py"),  # ComfyUI server files
    ]

    def __init__(self):
        """Initialize the file system manager."""
        self.logger = get_logger("prompt_manager.file_system")

        # Cached members (lazy-initialized)
        self._comfyui_root: Optional[Path] = None
        self._user_dir: Optional[Path] = None

        # User overrides
        self._custom_path: Optional[str] = None  # For PromptManager data root
        self._custom_db_path: Optional[str] = None  # Optional override for DB location

        # Settings cache
        self._settings_cache: Optional[Dict[str, Any]] = None

    # -------------------------
    # Root resolution & symlinks
    # -------------------------

    def resolve_comfyui_root(
        self,
        start: Optional[Path] = None,
        *,
        strategy: Literal[
            "prefer_symlink", "preserve_symlink", "resolve_all"
        ] = "prefer_symlink",
    ) -> Path:
        """
        Resolve the ComfyUI repository root directory.

        Priority:
          1) $COMFYUI_PATH (env var)
          2) Walk upwards from the *non-resolved* path to find 'custom_nodes' (preserves symlinks)
          3) If not found, optionally resolve symlinks and re-scan (depending on strategy)
          4) Walk upwards by markers
          5) Fallback: current working directory

        Args:
            start: Optional starting path for root discovery
            strategy:
                - "prefer_symlink": Try non-resolved scan first; if not found, switch to resolved scan
                - "preserve_symlink": Only scan non-resolved path; do not collapse symlinks
                - "resolve_all": Collapse symlinks early and scan only the real path

        Returns:
            Path to the ComfyUI root directory (preserving or collapsing symlinks based on strategy)
        """
        # Cached value shortcut
        if self._comfyui_root and self._comfyui_root.exists():
            return self._comfyui_root

        # Env override
        env_path = os.getenv("COMFYUI_PATH")
        if env_path:
            p = Path(env_path).expanduser()
            p = p if strategy != "resolve_all" else _path_no_symlink(p)
            if p.exists():
                self.logger.info(f"ComfyUI root from env: {p}")
                self._comfyui_root = p
                return p

        # Determine starting point
        if start is None:
            try:
                # Keep symlink identity unless strategy says resolve_all
                start = Path(__file__).absolute()
                if strategy == "resolve_all":
                    start = _path_no_symlink(start)
            except Exception:
                start = Path.cwd()

        # Helper: check for root by parent and marker match
        def _has_markers(base: Path) -> bool:
            try:
                for marker_set in self._ROOT_MARKER_SETS:
                    if all((base / m).exists() for m in marker_set):
                        return True
            except Exception:
                return False
            return False

        # 2) Non-resolved upward scan for custom_nodes (preferred for symlink-preserving)
        if strategy in ("prefer_symlink", "preserve_symlink"):
            for parent in _parents_including_self(start):
                if parent.name == "custom_nodes":
                    candidate = parent.parent
                    if _has_markers(candidate):
                        self.logger.info(
                            f"Found ComfyUI root via custom_nodes: {candidate}"
                        )
                        self._comfyui_root = candidate
                        return candidate
                # Direct child of custom_nodes
                if parent.parent and parent.parent.name == "custom_nodes":
                    candidate = parent.parent.parent
                    if _has_markers(candidate):
                        self.logger.info(
                            f"Found ComfyUI root: {candidate}"
                        )
                        self._comfyui_root = candidate
                        return candidate

        # 3) If configured, resolve and try again
        resolved_start = _path_no_symlink(start)
        if strategy in ("prefer_symlink", "resolve_all"):
            for parent in _parents_including_self(resolved_start):
                if parent.name == "custom_nodes":
                    candidate = parent.parent
                    if _has_markers(candidate):
                        self.logger.info(
                            f"Found ComfyUI root via resolved custom_nodes: {candidate}"
                        )
                        self._comfyui_root = candidate
                        return candidate
                if parent.parent and parent.parent.name == "custom_nodes":
                    candidate = parent.parent.parent
                    if _has_markers(candidate):
                        self.logger.info(
                            f"Found ComfyUI root: {candidate}"
                        )
                        self._comfyui_root = candidate
                        return candidate

        # 4) Marker scan upward (non-resolved first if preserving, else resolved)
        scan_bases = (
            _parents_including_self(start)
            if strategy == "preserve_symlink"
            else _parents_including_self(resolved_start)
        )
        for parent in scan_bases:
            if _has_markers(parent):
                self.logger.info(f"Found ComfyUI root via markers: {parent}")
                self._comfyui_root = parent
                return parent

        # 5) Windows desktop app fallback - check if cwd has user/ and custom_nodes/
        cwd = Path.cwd()
        if (cwd / "user").exists() and (cwd / "custom_nodes").exists():
            self.logger.info(f"Found ComfyUI root via user/custom_nodes detection (Windows desktop app): {cwd}")
            self._comfyui_root = cwd
            return cwd

        # 6) Last resort - check parent of custom_nodes even without markers
        for parent in scan_bases:
            if parent.name == "custom_nodes":
                candidate = parent.parent
                # Accept if it has user/ directory (Windows desktop app pattern)
                if (candidate / "user").exists():
                    self.logger.info(f"Found ComfyUI root via custom_nodes parent with user/ dir: {candidate}")
                    self._comfyui_root = candidate
                    return candidate

        # 7) No fallback - raise error if ComfyUI root not found
        error_msg = (
            "Could not find ComfyUI root directory!\n"
            "Please ensure PromptManager is installed in:\n"
            "  ComfyUI/custom_nodes/ComfyUI_PromptManager/\n"
            "Or set COMFYUI_PATH environment variable to your ComfyUI directory.\n"
            "Current working directory: {}\n"
            "Searched paths: {}".format(
                Path.cwd(),
                ", ".join(str(p) for p in scan_bases)
            )
        )
        self.logger.error(error_msg)
        raise RuntimeError(error_msg)

    # -------------------------
    # User data directory
    # -------------------------

    def get_user_dir(self, create: bool = True) -> Path:
        """
        Get the official user directory for PromptManager.

        Returns: ComfyUI/user/default/PromptManager/

        Args:
            create: Create the directory if it doesn't exist

        Returns:
            Path to the PromptManager user directory
        """
        # Custom path takes precedence
        if self._custom_path:
            custom = Path(self._custom_path).expanduser()
            if create:
                custom.mkdir(parents=True, exist_ok=True)
            return custom

        # Env override for user dir if provided (optional)
        env_user = os.getenv("COMFYUI_USER_DIR")
        if env_user:
            p = Path(env_user).expanduser() / "default" / self.NODE_NAME
            if create:
                p.mkdir(parents=True, exist_ok=True)
            self._user_dir = p
            return p

        if not self._user_dir or not self._user_dir.exists():
            root = self.resolve_comfyui_root()
            self._user_dir = root / "user" / "default" / self.NODE_NAME

        if create:
            self._user_dir.mkdir(parents=True, exist_ok=True)

        return self._user_dir

    def set_custom_path(self, path: Optional[str]) -> bool:
        """
        Set a custom path for data storage.

        Args:
            path: Custom path or None to use default

        Returns:
            True if path is valid and set, False otherwise
        """
        if not path:
            self._custom_path = None
            self.logger.info("Reset to default user directory")
            return True

        try:
            custom = Path(path).expanduser()
            custom.mkdir(parents=True, exist_ok=True)

            if not _writable_dir(custom):
                self.logger.error(f"No write permission for: {custom}")
                return False

            self._custom_path = str(custom)
            self.logger.info(f"Set custom path: {custom}")
            return True

        except Exception as e:
            self.logger.error(f"Invalid custom path: {e}")
            return False

    # -------------------------
    # Settings & DB path
    # -------------------------

    def _normalize_database_path(self, path_value: str) -> Path:
        """Normalize a database path string to an absolute Path within allowed locations."""
        candidate = Path(path_value).expanduser()
        if not candidate.is_absolute():
            candidate = self.get_user_dir() / candidate
        return _path_no_symlink(candidate)

    def _path_within_allowed_locations(self, path: Path) -> bool:
        user_dir = _path_no_symlink(self.get_user_dir())
        if _is_relative_to(_path_no_symlink(path), user_dir):
            return True

        if self._custom_path:
            custom_dir = _path_no_symlink(Path(self._custom_path).expanduser())
            if _is_relative_to(_path_no_symlink(path), custom_dir):
                return True

        return False

    def _update_settings_database_path(
        self, data: Dict[str, Any], new_path: Path, *, is_custom: bool
    ) -> None:
        """Persist an updated database path back to settings.json."""
        try:
            data["databasePath"] = str(new_path)
            data["databasePathCustom"] = bool(is_custom)
            self.save_json(data, "settings.json")
            # Keep cache in sync
            self._settings_cache = data
        except Exception as exc:
            self.logger.error(f"Unable to persist normalized database path: {exc}")

    def _load_settings_cache(self) -> Dict[str, Any]:
        if self._settings_cache is not None:
            return self._settings_cache
        settings = self.load_json("settings.json", {}) or {}
        self._settings_cache = settings
        return settings

    def _load_custom_db_path_from_settings(self) -> None:
        """Load a custom database path from the persisted settings file."""
        if self._custom_db_path is not None:
            return

        try:
            data = self._load_settings_cache()
            custom_path = data.get("databasePath")
            is_custom = bool(data.get("databasePathCustom", False))
            if not custom_path:
                return

            resolved = self._normalize_database_path(custom_path)
            default_path = _path_no_symlink(self.get_user_dir() / "prompts.db")
            comfy_root = _path_no_symlink(self.resolve_comfyui_root())
            legacy_root_path = _path_no_symlink(comfy_root / "prompts.db")

            if resolved == default_path:
                # Normalize if the stored path differs or wrongly marked custom
                if custom_path != str(default_path) or is_custom:
                    self._update_settings_database_path(
                        data, default_path, is_custom=False
                    )
                self._custom_db_path = None
                return

            # Treat legacy ComfyUI root DB as migration candidate
            same_as_legacy = resolved == legacy_root_path
            if same_as_legacy or (
                not is_custom and not self._path_within_allowed_locations(resolved)
            ):
                self.logger.warning(
                    "Detected database outside the PromptManager data directory. "
                    "Migrating to user/default/PromptManager."
                )
                try:
                    if resolved.exists():
                        if not default_path.exists():
                            default_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(resolved), str(default_path))
                        else:
                            backup = resolved.with_suffix(".legacy_backup")
                            shutil.move(str(resolved), str(backup))
                            self.logger.info(f"Legacy database backed up to {backup}")
                    self._update_settings_database_path(
                        data, default_path, is_custom=False
                    )
                except Exception as exc:
                    self.logger.error(f"Failed to migrate legacy database: {exc}")
                self._custom_db_path = None
                return

            # Accept custom path only if user intentionally stored outside default
            self._custom_db_path = str(resolved)

        except Exception as exc:
            self.logger.debug(
                f"Unable to load custom database path from settings: {exc}"
            )

    def set_custom_database_path(self, path: Optional[str]) -> Optional[Path]:
        """Configure an explicit database file path.

        Args:
            path: Desired database file path. If ``None`` the default user directory is used.

        Returns:
            The resolved path or ``None`` when reset to default.
        """
        if not path:
            self._custom_db_path = None
            return None

        resolved = self._normalize_database_path(path)
        if resolved.is_dir():
            resolved = resolved / "prompts.db"

        default_path = _path_no_symlink(self.get_user_dir() / "prompts.db")
        if resolved == default_path:
            self._custom_db_path = None
            settings = self._load_settings_cache()
            self._update_settings_database_path(settings, default_path, is_custom=False)
            return default_path

        self._custom_db_path = str(resolved)
        settings = self._load_settings_cache()
        self._update_settings_database_path(settings, resolved, is_custom=True)
        return resolved

    def move_database_file(self, target_path: str) -> Dict[str, Any]:
        """Move the prompts database to a new location atomically.

        The operation copies the database to the requested destination while
        keeping a backup of the original file. If anything fails the original
        database is restored from the backup to guarantee consistency.

        Args:
            target_path: The requested destination for the database file.

        Returns:
            Metadata about the move including previous and new paths.

        Raises:
            FileNotFoundError: When the current database does not exist.
            FileExistsError: If the destination already contains a database file.
            Exception: Any other error encountered during the move.
        """
        current_path = Path(self.get_database_path())
        if not current_path.exists():
            raise FileNotFoundError(f"Database not found at {current_path}")

        destination = Path(target_path).expanduser()
        if not destination.is_absolute():
            destination = _path_no_symlink(self.get_user_dir() / destination)
        else:
            destination = _path_no_symlink(destination)

        if destination.is_dir():
            destination = destination / current_path.name
        destination = destination.with_suffix(destination.suffix)  # normalize

        if destination == _path_no_symlink(current_path):
            return {
                "previous_path": str(current_path),
                "new_path": str(destination),
                "changed": False,
            }

        if destination.exists():
            raise FileExistsError(f"Destination already has a database: {destination}")

        destination.parent.mkdir(parents=True, exist_ok=True)

        backup_original = current_path.with_suffix(
            current_path.suffix + ".moving_backup"
        )
        temp_destination = destination.with_suffix(destination.suffix + ".tmp_move")

        # Ensure we start from a clean state
        try:
            backup_original.unlink(missing_ok=True)  # type: ignore[arg-type]
            temp_destination.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            pass

        self.logger.info(f"Moving database from {current_path} to {destination}")

        current_path.rename(backup_original)

        try:
            shutil.copy2(backup_original, temp_destination)

            # Basic integrity check: copy must match size of original
            if backup_original.stat().st_size != temp_destination.stat().st_size:
                raise IOError("Copied database size mismatch")

            temp_destination.replace(destination)

            # Persist custom path for future calls
            self.set_custom_database_path(str(destination))

            # Cleanup the original backup once everything succeeds
            try:
                backup_original.unlink()
            except Exception:
                pass

            self.logger.info(f"Database moved successfully to {destination}")

            return {
                "previous_path": str(current_path),
                "new_path": str(destination),
                "changed": True,
            }

        except Exception as exc:
            self.logger.error(f"Database move failed: {exc}")

            # Clean up any partially copied file
            try:
                temp_destination.unlink(missing_ok=True)  # type: ignore[arg-type]
            except Exception:
                pass

            # Restore original database location
            try:
                if backup_original.exists():
                    backup_original.rename(current_path)
            except Exception as restore_exc:
                self.logger.error(
                    f"Failed to restore original database after move failure: {restore_exc}"
                )
                raise restore_exc from exc

            raise

    def get_database_path(self, filename: str = "prompts.db") -> Path:
        """
        Get the path for the database file.

        Args:
            filename: Database filename

        Returns:
            Path to the database file
        """
        if self._custom_db_path is None:
            self._load_custom_db_path_from_settings()

        if self._custom_db_path:
            return Path(self._custom_db_path)

        return self.get_user_dir() / filename

    def has_custom_database_path(self) -> bool:
        """Return True if the database path was moved outside the default directory."""
        return self._custom_db_path is not None

    def get_settings_path(self, filename: str = "settings.json") -> Path:
        """
        Get the path for the settings file.

        Args:
            filename: Settings filename

        Returns:
            Path to the settings file
        """
        return self.get_user_dir() / filename

    # -------------------------
    # Standard subdirectories
    # -------------------------

    def get_backup_dir(self, create: bool = True) -> Path:
        backup_dir = self.get_user_dir() / "backups"
        if create:
            backup_dir.mkdir(parents=True, exist_ok=True)
        return backup_dir

    def get_export_dir(self, create: bool = True) -> Path:
        export_dir = self.get_user_dir() / "exports"
        if create:
            export_dir.mkdir(parents=True, exist_ok=True)
        return export_dir

    def get_logs_dir(self, create: bool = True) -> Path:
        logs_dir = self.get_user_dir() / "logs"
        if create:
            logs_dir.mkdir(parents=True, exist_ok=True)
        return logs_dir

    def get_cache_dir(self, create: bool = True) -> Path:
        cache_dir = self.get_user_dir() / "cache"
        if create:
            cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    # -------------------------
    # v1 migration & checks
    # -------------------------

    def migrate_v1_files(self) -> Dict[str, Any]:
        """
        Migrate v1 files from ComfyUI root to proper user directory.

        Looks for v1 files in ComfyUI root:
        - prompts.db
        - promptmanager_settings.json
        - example_prompts.db

        Returns:
            Migration results with file counts and paths
        """
        results: Dict[str, Any] = {"migrated": [], "errors": [], "skipped": []}

        root = self.resolve_comfyui_root()
        user_dir = self.get_user_dir()

        v1_files = [
            "prompts.db",
            "prompts.db.866_backup",
            "example_prompts.db",
            "promptmanager_settings.json",
            "prompt_manager_settings.json",
        ]

        for filename in v1_files:
            old_path = root / filename
            if not old_path.exists():
                continue

            # Normalize DB backups → prompts.db
            is_prompts_backup = filename.startswith("prompts.db") and (
                filename.endswith("_backup") or filename.endswith(".866_backup")
            )
            target_filename = "prompts.db" if is_prompts_backup else filename
            new_path = user_dir / target_filename

            # If already exists in v2 location, try to compare prompt counts (best-effort)
            if new_path.exists():
                if filename.endswith(".db") or filename.startswith("prompts.db"):
                    try:
                        import sqlite3

                        with (
                            sqlite3.connect(old_path) as v1_conn,
                            sqlite3.connect(new_path) as v2_conn,
                        ):
                            v1_count = v1_conn.execute(
                                "SELECT COUNT(*) FROM prompts"
                            ).fetchone()[0]
                            v2_count = v2_conn.execute(
                                "SELECT COUNT(*) FROM prompts"
                            ).fetchone()[0]

                        if v2_count < v1_count * 0.1:
                            self.logger.info(
                                f"Target database has {v2_count} prompts vs source {v1_count}, replacing"
                            )
                            partial_backup = new_path.with_suffix(".db.partial_backup")
                            shutil.move(str(new_path), str(partial_backup))
                            self.logger.info(
                                f"Backed up partial v2 database to {partial_backup}"
                            )
                        else:
                            results["skipped"].append(
                                {
                                    "file": filename,
                                    "reason": f"Already migrated ({v2_count} prompts in v2)",
                                }
                            )
                            continue
                    except Exception as e:
                        self.logger.warning(f"Could not compare database sizes: {e}")
                        results["skipped"].append(
                            {
                                "file": filename,
                                "reason": "Already exists in user directory",
                            }
                        )
                        continue
                else:
                    results["skipped"].append(
                        {"file": filename, "reason": "Already exists in user directory"}
                    )
                    continue

            try:
                backup_name = (
                    f"{filename}.v1_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                )
                backup_path = self.get_backup_dir() / backup_name

                if filename.endswith(".db") or filename.startswith("prompts.db"):
                    import sqlite3

                    try:
                        from src.repositories.prompt_repository import (
                            PromptRepository,  # type: ignore
                        )
                    except Exception:
                        project_root = Path(__file__).parent.parent
                        if str(project_root) not in sys.path:
                            sys.path.insert(0, str(project_root))
                        from src.repositories.prompt_repository import (
                            PromptRepository,  # type: ignore
                        )

                    # Backup v1 DB before touching it
                    shutil.copy2(old_path, backup_path)
                    self.logger.info(f"Created backup: {backup_path}")

                    # If target exists (rare path), back it up
                    if new_path.exists():
                        existing_backup = new_path.with_suffix(
                            ".db.pre_migration_backup"
                        )
                        self.logger.info(
                            f"Backing up existing v2 database to {existing_backup}"
                        )
                        shutil.move(str(new_path), str(existing_backup))

                    repo = PromptRepository(db_path=str(new_path))
                    self.logger.info(
                        f"Transforming database schema from v1 to v2 at {new_path}"
                    )

                    migrated = 0
                    with sqlite3.connect(old_path) as v1_conn:
                        cur = v1_conn.cursor()
                        cur.execute("PRAGMA table_info(prompts)")
                        columns = [col[1] for col in cur.fetchall()]

                        if "text" in columns:
                            cur.execute(
                                """
                                SELECT text, category, tags, rating, notes, hash,
                                       created_at, updated_at
                                FROM prompts
                            """
                            )
                        elif "prompt" in columns:
                            cur.execute(
                                """
                                SELECT prompt, negative_prompt, category, tags, rating, notes, hash,
                                       created_at, updated_at
                                FROM prompts
                            """
                            )
                        else:
                            raise ValueError(
                                "Unknown database schema (no 'text' or 'prompt' column)"
                            )

                        import json as _json

                        for row in cur.fetchall():
                            try:
                                if "text" in columns:
                                    prompt_text = row[0]
                                    negative_prompt = ""
                                    category = row[1]
                                    tags_str = row[2]
                                    rating = row[3]
                                    notes = row[4]
                                    prompt_hash = row[5]
                                else:
                                    prompt_text = row[0]
                                    negative_prompt = row[1] or ""
                                    category = row[2]
                                    tags_str = row[3]
                                    rating = row[4]
                                    notes = row[5]
                                    prompt_hash = row[6]

                                # Normalize tags
                                tags_json = "[]"
                                if tags_str:
                                    try:
                                        if isinstance(tags_str, str):
                                            _ = _json.loads(tags_str)
                                            tags_json = tags_str
                                        else:
                                            tags_json = _json.dumps(tags_str)
                                    except Exception:
                                        tags_json = "[]"

                                data = {
                                    "prompt": (prompt_text or "").strip(),
                                    "negative_prompt": (negative_prompt or "").strip(),
                                    "category": category,
                                    "tags": tags_json,
                                    "rating": (
                                        int(rating)
                                        if rating and 1 <= int(rating) <= 5
                                        else 0
                                    ),
                                    "notes": notes,
                                    "metadata": {},
                                    "workflow": {},
                                    "execution_count": 0,
                                }
                                if prompt_hash:
                                    data["hash"] = prompt_hash

                                repo.create(data)
                                migrated += 1
                            except Exception as e:
                                self.logger.error(f"Error migrating prompt: {e}")
                                continue

                    self.logger.info(
                        f"Successfully transformed {migrated} prompts to v2 schema"
                    )

                    migration_info = {
                        "file": filename,
                        "old_path": str(old_path),
                        "new_path": str(new_path),
                        "backup_path": str(backup_path),
                        "prompts_migrated": migrated,
                    }

                else:
                    # Non-DB files: copy + backup
                    shutil.copy2(old_path, new_path)
                    self.logger.info(f"Copied {filename} to user directory")
                    shutil.copy2(old_path, backup_path)
                    self.logger.info(f"Created backup: {backup_path}")

                    migration_info = {
                        "file": filename,
                        "old_path": str(old_path),
                        "new_path": str(new_path),
                        "backup_path": str(backup_path),
                    }

                # Mark old v1 file as migrated (non-destructive)
                old_backup = old_path.with_suffix(old_path.suffix + ".v1_migrated")
                old_path.rename(old_backup)

                results["migrated"].append(migration_info)

            except Exception as e:
                self.logger.error(f"Failed to migrate {filename}: {e}")
                results["errors"].append({"file": filename, "error": str(e)})

        return results

    def check_v1_files(self) -> Dict[str, Any]:
        """
        Check for v1 files in ComfyUI root without migrating.

        Returns:
            Information about found v1 files
        """
        root = self.resolve_comfyui_root()

        v1_files = [
            "prompts.db",
            "prompts.db.866_backup",
            "example_prompts.db",
            "promptmanager_settings.json",
            "prompt_manager_settings.json",
        ]

        found: List[Dict[str, Any]] = []
        for filename in v1_files:
            path = root / filename
            if path.exists():
                stat = path.stat()
                found.append(
                    {
                        "filename": filename,
                        "path": str(path),
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    }
                )

        stats: Dict[str, Any] = {}
        for file_info in found:
            if file_info["filename"].endswith(".db") or file_info[
                "filename"
            ].startswith("prompts.db"):
                try:
                    import sqlite3

                    with sqlite3.connect(file_info["path"]) as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT COUNT(*) FROM prompts")
                        stats["prompts"] = cur.fetchone()[0]
                        # These may not exist in all schemas; guard with try
                        try:
                            cur.execute("SELECT COUNT(*) FROM generated_images")
                            stats["images"] = cur.fetchone()[0]
                        except Exception:
                            pass
                        try:
                            cur.execute(
                                "SELECT COUNT(DISTINCT category) FROM prompts WHERE category IS NOT NULL"
                            )
                            stats["categories"] = cur.fetchone()[0]
                        except Exception:
                            pass
                except Exception as e:
                    self.logger.error(f"Error reading v1 database stats: {e}")

        return {
            "v1_exists": len(found) > 0,
            "v1_files": found,
            "v1_path": str(root),
            "stats": stats,
        }

    # -------------------------
    # JSON utilities
    # -------------------------

    def save_json(self, obj: Any, filename: str, pretty: bool = True) -> Path:
        """
        Save JSON data to user directory (atomic write).

        Args:
            obj: Object to save as JSON
            filename: Target filename
            pretty: Use pretty printing

        Returns:
            Path where file was saved
        """
        path = self.get_user_dir() / filename
        tmp = path.with_suffix(path.suffix + ".tmp")

        try:
            with tmp.open("w", encoding="utf-8") as f:
                if pretty:
                    json.dump(obj, f, ensure_ascii=False, indent=2)
                else:
                    json.dump(obj, f, ensure_ascii=False)
            tmp.replace(path)
            self.logger.debug(f"Saved JSON to: {path}")
            return path
        except Exception:
            # Best-effort cleanup
            try:
                tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
            except Exception:
                pass
            raise

    def load_json(self, filename: str, default: Any = None) -> Any:
        """
        Load JSON data from user directory.

        Args:
            filename: Source filename
            default: Default value if file doesn't exist

        Returns:
            Loaded JSON data or default value
        """
        path = self.get_user_dir(create=False) / filename
        if not path.exists():
            return default

        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Error loading JSON from {filename}: {e}")
            return default

    # -------------------------
    # Directory info / bootstrap
    # -------------------------

    def get_directory_info(self) -> Dict[str, Any]:
        """
        Get information about the current data directory.

        Returns:
            Dictionary with directory information
        """
        user_dir = self.get_user_dir()

        # Calculate total size
        total_size = 0
        file_count = 0
        for item in user_dir.rglob("*"):
            if item.is_file():
                try:
                    total_size += item.stat().st_size
                    file_count += 1
                except Exception:
                    continue

        return {
            "path": str(user_dir),
            "is_custom": self._custom_path is not None,
            "custom_path": self._custom_path,
            "exists": user_dir.exists(),
            "writable": _writable_dir(user_dir) if user_dir.exists() else False,
            "total_size": total_size,
            "file_count": file_count,
            "free_space": shutil.disk_usage(user_dir).free if user_dir.exists() else 0,
            "database_path": str(self.get_database_path()),
        }

    def ensure_directory_structure(self) -> Dict[str, Path]:
        """
        Ensure all required directories exist.

        Returns:
            Dictionary of directory paths
        """
        dirs = {
            "user": self.get_user_dir(),
            "backups": self.get_backup_dir(),
            "exports": self.get_export_dir(),
            "logs": self.get_logs_dir(),
            "cache": self.get_cache_dir(),
        }

        # Create README in user directory
        readme_path = dirs["user"] / "README.txt"
        if not readme_path.exists():
            readme_content = f"""PromptManager v2 Data Directory
================================

This directory contains all data for the PromptManager extension.

Directory Structure:
- prompts.db: Main database with all your prompts
- settings.json: Your configuration settings
- backups/: Automatic and manual backups
- exports/: Exported prompts and data
- logs/: Application logs
- cache/: Temporary cache files

This is the official location for ComfyUI extension data.
(In v1, files were incorrectly stored in the ComfyUI root directory)

Generated: {datetime.now().isoformat()}
"""
            readme_path.write_text(readme_content, encoding="utf-8")

        return dirs


# Global instance
_file_system: Optional[ComfyUIFileSystem] = None


def get_file_system() -> ComfyUIFileSystem:
    """Get the global file system manager instance."""
    global _file_system
    if _file_system is None:
        _file_system = ComfyUIFileSystem()
    return _file_system


# Convenience functions
def get_user_dir(create: bool = True) -> Path:
    """Get the PromptManager user directory."""
    return get_file_system().get_user_dir(create)


def get_database_path() -> Path:
    """Get the database file path."""
    return get_file_system().get_database_path()


def get_settings_path() -> Path:
    """Get the settings file path."""
    return get_file_system().get_settings_path()


def migrate_v1_files() -> Dict[str, Any]:
    """Migrate v1 files to proper location."""
    return get_file_system().migrate_v1_files()


def check_v1_files() -> Dict[str, Any]:
    """Check for v1 files without migrating."""
    return get_file_system().check_v1_files()
