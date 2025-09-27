#!/usr/bin/env python3
"""
Validate that generation data is being captured properly.
Shows what data is present and what's still missing.
"""

import sys
import sqlite3
import json
from pathlib import Path
from typing import Dict, Any


def check_database(db_path: str) -> Dict[str, Any]:
    """
    Check database for populated generation data.

    Args:
        db_path: Path to prompts.db

    Returns:
        Dictionary with data statistics
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    stats = {
        "total_prompts": 0,
        "prompts_with_generation_params": 0,
        "prompts_with_model_hash": 0,
        "prompts_with_sampler_settings": 0,
        "total_images": 0,
        "images_with_dimensions": 0,
        "images_with_parameters": 0,
        "tracking_entries": 0,
        "recent_captures": []
    }

    # Check prompts table
    cursor.execute("SELECT COUNT(*) FROM prompts")
    stats["total_prompts"] = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM prompts
        WHERE generation_params IS NOT NULL
        AND generation_params != ''
        AND generation_params != 'null'
    """)
    stats["prompts_with_generation_params"] = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM prompts
        WHERE model_hash IS NOT NULL
        AND model_hash != ''
    """)
    stats["prompts_with_model_hash"] = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM prompts
        WHERE sampler_settings IS NOT NULL
        AND sampler_settings != ''
        AND sampler_settings != 'null'
    """)
    stats["prompts_with_sampler_settings"] = cursor.fetchone()[0]

    # Check generated_images table
    cursor.execute("SELECT COUNT(*) FROM generated_images")
    stats["total_images"] = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM generated_images
        WHERE width IS NOT NULL AND height IS NOT NULL
    """)
    stats["images_with_dimensions"] = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM generated_images
        WHERE parameters IS NOT NULL
        AND parameters != ''
        AND parameters != 'null'
    """)
    stats["images_with_parameters"] = cursor.fetchone()[0]

    # Check prompt_tracking table
    cursor.execute("SELECT COUNT(*) FROM prompt_tracking")
    stats["tracking_entries"] = cursor.fetchone()[0]

    # Get recent captures
    cursor.execute("""
        SELECT
            positive_prompt,
            model_hash,
            generation_params,
            created_at
        FROM prompts
        WHERE generation_params IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 5
    """)

    for row in cursor.fetchall():
        try:
            params = json.loads(row[2]) if row[2] else {}
            stats["recent_captures"].append({
                "prompt": row[0][:50] + "..." if len(row[0]) > 50 else row[0],
                "model_hash": row[1] or "None",
                "model": params.get("model", "Unknown"),
                "created": row[3]
            })
        except:
            pass

    cursor.close()
    conn.close()

    return stats


def print_report(stats: Dict[str, Any]):
    """Print a formatted report of the data capture status."""

    print("\n" + "=" * 60)
    print("📊 PROMPTMANAGER DATA CAPTURE VALIDATION REPORT")
    print("=" * 60)

    # Calculate percentages
    params_pct = (
        (stats["prompts_with_generation_params"] / stats["total_prompts"] * 100)
        if stats["total_prompts"] > 0 else 0
    )
    model_pct = (
        (stats["prompts_with_model_hash"] / stats["total_prompts"] * 100)
        if stats["total_prompts"] > 0 else 0
    )
    sampler_pct = (
        (stats["prompts_with_sampler_settings"] / stats["total_prompts"] * 100)
        if stats["total_prompts"] > 0 else 0
    )
    dims_pct = (
        (stats["images_with_dimensions"] / stats["total_images"] * 100)
        if stats["total_images"] > 0 else 0
    )

    print("\n📝 PROMPTS TABLE:")
    print(f"  Total prompts: {stats['total_prompts']}")
    print(f"  With generation_params: {stats['prompts_with_generation_params']} ({params_pct:.1f}%)")
    print(f"  With model_hash: {stats['prompts_with_model_hash']} ({model_pct:.1f}%)")
    print(f"  With sampler_settings: {stats['prompts_with_sampler_settings']} ({sampler_pct:.1f}%)")

    print("\n🖼️ IMAGES TABLE:")
    print(f"  Total images: {stats['total_images']}")
    print(f"  With dimensions: {stats['images_with_dimensions']} ({dims_pct:.1f}%)")
    print(f"  With parameters: {stats['images_with_parameters']}")

    print("\n📊 TRACKING TABLE:")
    print(f"  Total tracking entries: {stats['tracking_entries']}")

    # Status assessment
    print("\n✅ DATA CAPTURE STATUS:")

    if params_pct > 50:
        print("  ✅ Good coverage - tracker nodes are working!")
    elif params_pct > 10:
        print("  ⚠️ Partial coverage - some data being captured")
    elif stats["prompts_with_generation_params"] > 0:
        print("  🔄 Just started - data capture beginning")
    else:
        print("  ❌ No data captured yet - please use tracker nodes")

    # Recent captures
    if stats["recent_captures"]:
        print("\n🕐 RECENT CAPTURES:")
        for capture in stats["recent_captures"]:
            print(f"  • {capture['created']}")
            print(f"    Model: {capture['model']}")
            print(f"    Prompt: {capture['prompt']}")

    # Recommendations
    print("\n💡 RECOMMENDATIONS:")

    if params_pct < 100:
        print("  1. Add PromptManagerTracker node to your workflows")
        print("  2. The tracker will capture data for new generations")

    if stats["tracking_entries"] == 0:
        print("  3. Tracker nodes will populate prompt_tracking table")

    if dims_pct < 100 and stats["total_images"] > 0:
        print("  4. Use PromptManagerImageTracker to capture image dimensions")

    print("\n" + "=" * 60)
    print("For setup instructions: see docs/TRACKER_NODES_GUIDE.md")
    print("=" * 60 + "\n")


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

    print(f"📂 Checking database: {db_path}")

    # Check the database
    stats = check_database(str(db_path))

    # Print report
    print_report(stats)


if __name__ == "__main__":
    main()