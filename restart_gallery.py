#!/usr/bin/env python3
"""
ComfyUI_PromptManager Gallery System Restart Script.

This script reinitializes and restarts the automatic image gallery monitoring system
for ComfyUI_PromptManager. Use this script after code modifications or when the 
gallery system needs to be reloaded.

The script performs the following operations:
1. Initializes the database connection
2. Creates a PromptTracker instance for linking prompts to images
3. Sets up the ImageMonitor for real-time file system monitoring
4. Starts monitoring ComfyUI output directories
5. Reports system status and readiness

Usage:
    python restart_gallery.py

This is particularly useful during development when you need to restart the
gallery monitoring system without restarting the entire ComfyUI application.
"""

import sys
import os

# Add current directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

try:
    # Import and initialize the gallery system
    from database.operations import PromptDatabase
    from utils.prompt_tracker import get_prompt_tracker
    from utils.image_monitor import get_image_monitor

    print("[RESTART] Restarting PromptManager Gallery System...")

    # Initialize components using singleton getters
    db = PromptDatabase()
    tracker = get_prompt_tracker(db)
    monitor = get_image_monitor(db, tracker)

    print("[SUCCESS] Components initialized successfully")
    
    # Test image monitoring directories
    status = monitor.get_status()
    print(f"[INFO] Monitoring status: {status}")
    
    # Start monitoring
    monitor.start_monitoring()
    print("[SUCCESS] Image monitoring restarted")
    
    print("\n[READY] Gallery system restart completed!")
    print("Generate some images now to test the automatic linking.")
    
except ImportError as e:
    print(f"[ERROR] Import error: {e}")
    print("Make sure you're running this from the PromptManager directory")
    
except Exception as e:
    print(f"[ERROR] Error: {e}")
    import traceback
    traceback.print_exc()