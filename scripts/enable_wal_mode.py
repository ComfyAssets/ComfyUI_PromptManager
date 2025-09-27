#!/usr/bin/env python3
"""
Enable WAL mode on the PromptManager database for better concurrency.
This solves "database is locked" errors when multiple processes access the database.
"""

import sqlite3
from pathlib import Path
import sys


def enable_wal_mode(db_path: str) -> bool:
    """
    Enable WAL mode on the database.

    Args:
        db_path: Path to the database

    Returns:
        True if successful
    """
    try:
        conn = sqlite3.connect(db_path)

        # Enable WAL mode
        result = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        print(f"✅ Journal mode: {result[0]}")

        # Set busy timeout to 5 seconds
        conn.execute("PRAGMA busy_timeout=5000")
        print("✅ Busy timeout: 5000ms")

        # Optimize for concurrent access
        conn.execute("PRAGMA synchronous=NORMAL")
        print("✅ Synchronous: NORMAL")

        # Enable memory-mapped I/O
        conn.execute("PRAGMA mmap_size=268435456")  # 256MB
        print("✅ Memory map: 256MB")

        # Set auto-checkpoint
        conn.execute("PRAGMA wal_autocheckpoint=1000")
        print("✅ WAL autocheckpoint: 1000 pages")

        # Verify settings
        print("\n📊 Database configuration:")
        settings = [
            "journal_mode",
            "busy_timeout",
            "synchronous",
            "mmap_size",
            "wal_autocheckpoint"
        ]

        for setting in settings:
            result = conn.execute(f"PRAGMA {setting}").fetchone()
            print(f"  {setting}: {result[0] if result else 'N/A'}")

        conn.close()
        return True

    except Exception as e:
        print(f"❌ Error enabling WAL mode: {e}")
        return False


def main():
    """Main entry point."""

    # Find database
    db_paths = [
        Path.home() / "ai-apps/ComfyUI-3.12/user/default/PromptManager/prompts.db",
        Path("user/default/PromptManager/prompts.db"),
        Path("../user/default/PromptManager/prompts.db"),
        Path("../../user/default/PromptManager/prompts.db"),
    ]

    db_path = None
    for path in db_paths:
        if path.exists():
            db_path = path
            break

    if not db_path:
        print("❌ Could not find prompts.db database")
        print("Searched in:", db_paths)
        sys.exit(1)

    print(f"📂 Database: {db_path}")
    print("=" * 60)
    print("🔧 Enabling WAL mode for better concurrency...")
    print("=" * 60)

    if enable_wal_mode(str(db_path)):
        print("\n✅ WAL mode enabled successfully!")
        print("This will:")
        print("  • Allow concurrent reads and writes")
        print("  • Prevent 'database is locked' errors")
        print("  • Improve performance with multiple processes")
        print("\nNote: You'll see .db-wal and .db-shm files next to the database.")
        print("This is normal - they're part of WAL mode operation.")
    else:
        print("\n❌ Failed to enable WAL mode")
        sys.exit(1)


if __name__ == "__main__":
    main()