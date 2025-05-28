#!/usr/bin/env python3
"""
Restart gallery system script.
Use this after fixing code to reload the image monitoring system.
"""

import sys
import os

# Add current directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

try:
    # Import and initialize the gallery system
    from database.operations import PromptDatabase
    from utils.prompt_tracker import PromptTracker
    from utils.image_monitor import ImageMonitor
    
    print("[RESTART] Restarting PromptManager Gallery System...")
    
    # Initialize components
    db = PromptDatabase()
    tracker = PromptTracker(db)
    monitor = ImageMonitor(db, tracker)
    
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