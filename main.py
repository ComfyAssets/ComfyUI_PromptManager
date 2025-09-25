#!/usr/bin/env python3
"""Main entry point for PromptManager application.

This module provides the command-line interface for starting the
PromptManager server with various options.
"""

import argparse
import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.app import run_app
from src.config import config
from src.database import db
from utils.file_system import get_file_system
try:
    from promptmanager.loggers import setup_logging, get_logger  # type: ignore
except ImportError:  # pragma: no cover - fallback for direct execution
    from loggers import setup_logging, get_logger  # type: ignore

logger = get_logger("promptmanager.main")


def parse_arguments():
    """Parse command-line arguments.
    
    Returns:
        Parsed arguments
    """
    parser = argparse.ArgumentParser(
        description="ComfyUI PromptManager V2 - Professional prompt and image management"
    )
    
    # Server options
    parser.add_argument(
        "--host",
        default=None,
        help=f"API server host (default: {config.api.host})"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"API server port (default: {config.api.port})"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode"
    )
    
    # Database options
    parser.add_argument(
        "--db",
        "--database",
        default=None,
        help=f"Database path (default: {config.database.path})"
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize database and exit"
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="Run database migrations and exit"
    )
    parser.add_argument(
        "--backup",
        metavar="PATH",
        help="Create database backup and exit"
    )
    parser.add_argument(
        "--restore",
        metavar="PATH",
        help="Restore database from backup and exit"
    )
    
    # WebSocket options
    parser.add_argument(
        "--no-websocket",
        action="store_true",
        help="Disable WebSocket server"
    )
    parser.add_argument(
        "--ws-port",
        type=int,
        default=None,
        help=f"WebSocket server port (default: {config.websocket.port})"
    )
    
    # Configuration options
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="Configuration file path"
    )
    parser.add_argument(
        "--save-config",
        metavar="FILE",
        help="Save current configuration to file and exit"
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Show current configuration and exit"
    )
    
    # Logging options
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=None,
        help=f"Logging level (default: {config.logging.level})"
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help=f"Log file path (default: {config.logging.file})"
    )
    
    # ComfyUI options
    parser.add_argument(
        "--comfyui-address",
        default=None,
        help=f"ComfyUI server address (default: {config.comfyui.server_address})"
    )
    parser.add_argument(
        "--no-auto-track",
        action="store_true",
        help="Disable automatic tracking of ComfyUI executions"
    )
    
    return parser.parse_args()


def apply_arguments(args):
    """Apply command-line arguments to configuration.
    
    Args:
        args: Parsed arguments
    """
    # Load custom config file if specified
    if args.config:
        config.load(args.config)
    
    # Apply server options
    if args.host:
        config.api.host = args.host
    if args.port:
        config.api.port = args.port
    if args.debug:
        config.api.debug = True
    
    # Apply database options
    if args.db:
        fs = get_file_system()
        resolved = fs.set_custom_database_path(args.db)
        config.database.path = str(resolved)
        # Reinitialize database with selected path
        global db
        from src.database import Database
        db = Database(config.database.path)
    
    # Apply WebSocket options
    if args.no_websocket:
        config.websocket.enabled = False
    if args.ws_port:
        config.websocket.port = args.ws_port
    
    # Apply logging options
    if args.log_level:
        config.logging.level = args.log_level
    if args.log_file:
        fs = get_file_system()
        canonical_log = str((fs.get_logs_dir() / 'promptmanager.log').resolve())
        if Path(args.log_file).expanduser().resolve() != Path(canonical_log):
            logger.warning('Custom --log-file is not supported; using %s', canonical_log)
        config.logging.file = canonical_log
    
    # Apply ComfyUI options
    if args.comfyui_address:
        config.comfyui.server_address = args.comfyui_address
    if args.no_auto_track:
        config.comfyui.auto_track = False


def main():
    """Main entry point."""
    args = parse_arguments()
    
    # Apply arguments to configuration
    apply_arguments(args)
    
    # Setup logging
    setup_logging(
        level=config.logging.level,
        log_file=config.logging.file,
        console=config.logging.console
    )
    
    # Handle configuration commands
    if args.show_config:
        import json
        print(json.dumps(config.to_dict(), indent=2))
        return 0
    
    if args.save_config:
        config.save(args.save_config)
        logger.info(f"Configuration saved to {args.save_config}")
        return 0
    
    # Handle database commands
    if args.init_db:
        logger.info("Initializing database...")
        db.initialize()
        logger.info("Database initialized successfully")
        return 0
    
    if args.migrate:
        logger.info("Running database migrations...")
        db.migrate()
        logger.info("Database migrations completed")
        return 0
    
    if args.backup:
        logger.info(f"Creating database backup to {args.backup}...")
        backup_path = db.backup(args.backup)
        logger.info(f"Database backed up to {backup_path}")
        return 0
    
    if args.restore:
        logger.info(f"Restoring database from {args.restore}...")
        db.restore(args.restore)
        logger.info("Database restored successfully")
        return 0
    
    # Print startup banner
    print("""
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║             ComfyUI PromptManager V2                     ║
    ║           Professional Prompt & Image Management          ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝
    """)
    
    logger.info("Starting PromptManager V2...")
    logger.info(f"Configuration: {config.config_file or 'defaults'}")
    logger.info(f"Database: {config.database.path}")
    logger.info(f"API Server: http://{config.api.host}:{config.api.port}")
    
    if config.websocket.enabled:
        logger.info(f"WebSocket: ws://{config.websocket.host}:{config.websocket.port}")
    
    if config.comfyui.enabled:
        logger.info(f"ComfyUI: {config.comfyui.server_address}")
    
    logger.info(f"API Documentation: http://{config.api.host}:{config.api.port}/api/v1/docs")
    
    try:
        # Run the application
        run_app()
    except KeyboardInterrupt:
        logger.info("\nShutting down gracefully...")
        return 0
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
