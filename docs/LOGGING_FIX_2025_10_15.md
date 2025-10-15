# Logging Isolation Fix - 2025-10-15

## Problem

PromptManager was modifying Python's **root logger**, which affected **ALL** logging in ComfyUI:

### Before (Broken)
```
2025-10-15 09:01:47 - root - INFO -
Import times for custom nodes:
2025-10-15 09:01:47 - root - INFO -    0.0 seconds: D:\ComfyUI\...\websocket_image_save.py
2025-10-15 09:01:47 - root - INFO -    0.0 seconds: D:\ComfyUI\...\apoloniartiff_node.py
2025-10-15 09:01:47 - alembic.runtime.migration - INFO - Context impl SQLiteImpl.
2025-10-15 09:01:47 - root - WARNING - No target revision found.
```

Every single log message in ComfyUI (including from other custom nodes) was being formatted with our timestamp prefix!

### After (Fixed)
```
Import times for custom nodes:
   0.0 seconds: D:\ComfyUI\...\websocket_image_save.py
   0.0 seconds: D:\ComfyUI\...\apoloniartiff_node.py
Context impl SQLiteImpl.
No target revision found.
Starting server
```

ComfyUI's original clean logging format is restored.

## Root Causes

### 1. `utils/logging.py` - Multiple Root Logger Violations

```python
# WRONG - This was modifying the root logger!
root_logger = logging.getLogger()  # No name = root logger
root_logger.setLevel(...)
root_logger.handlers.clear()  # Clears ALL handlers including ComfyUI's!
root_logger.addHandler(console_handler)  # Adds OUR handler to root
```

### 2. `loggers.py` - Using basicConfig()

```python
# WRONG - This configures the root logger!
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
```

## The Fix

### Key Principle: **NEVER TOUCH THE ROOT LOGGER**

```python
# RIGHT - Configure ONLY our logger
pm_logger = logging.getLogger("promptmanager")  # Named logger
pm_logger.setLevel(logging.INFO)
pm_logger.handlers.clear()  # Only clears OUR handlers
pm_logger.propagate = True  # Let ComfyUI's root logger handle output
pm_logger.addHandler(our_handler)  # Only affects our logs
```

## Changes Made

### `utils/logging.py`
- ✅ Changed `logging.getLogger()` → `logging.getLogger("promptmanager")`
- ✅ Removed `root_logger.handlers.clear()` - only clear our own handlers
- ✅ Set `pm_logger.propagate = True` - let ComfyUI display our logs
- ✅ All handlers added to `pm_logger` not `root_logger`
- ✅ Added docstrings warning about root logger

### `loggers.py`
- ✅ Removed `logging.basicConfig()` calls
- ✅ Changed to configure named logger `"promptmanager"`
- ✅ Set `propagate = True`
- ✅ Added critical warnings in docstrings

## How Python Logging Works

```
Root Logger (ComfyUI controls this)
  ├── ComfyUI's handlers (StreamHandler with no formatting)
  │
  ├─→ Logger: "promptmanager" (PromptManager controls this)
  │    ├── Our handlers (file logging, optional console)
  │    └── propagate=True → sends to root logger
  │
  ├─→ Logger: "PIL" (Pillow)
  ├─→ Logger: "aiohttp" (aiohttp)
  └─→ Logger: "alembic" (database migrations)
```

With `propagate=True`, our log messages:
1. Go through our handlers first (file logging, formatted console)
2. Then propagate to root logger (ComfyUI's clean console output)

## Testing

Users can verify the fix by:

1. **Before**: Every log line has timestamp prefix
2. **After**: Only PromptManager's own logs have our format
3. ComfyUI's startup messages are clean and unformatted

## Lessons Learned

### ❌ NEVER DO THIS in a Python library/plugin:
```python
logging.basicConfig(...)           # Configures root logger
logging.getLogger()                # Gets root logger (no name)
root = logging.getLogger()         # Explicit root logger access
root.handlers.clear()              # Destroys all handlers
```

### ✅ ALWAYS DO THIS instead:
```python
logger = logging.getLogger("mylib")  # Named logger
logger.setLevel(...)                 # Only affects us
logger.addHandler(...)               # Only our handler
logger.propagate = True              # Let host app handle output
```

## References

- [Python Logging HOWTO](https://docs.python.org/3/howto/logging.html#advanced-logging-tutorial)
- [Logging Best Practices](https://docs.python-guide.org/writing/logging/)
- Issue reported by: hans

## Commit

```
commit ff214f4
fix(logging): isolate PromptManager logging from ComfyUI root logger
```
